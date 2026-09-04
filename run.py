#!/usr/bin/env python3
"""
FL Daily Edit — Main Entry Point

Usage:
    python run.py run --dry-run                       # Preview all changes; write nothing
    python run.py run --edit-file /path/to/EDIT00000000
    python run.py inspect --edit-file /path/to/EDIT00000000
    python run.py audit --edit-file /path/to/EDIT00000000 --json
    python run.py compare --left-cpk /path/to/data_s2526.cpk --right-cpk /path/to/data_extra.cpk --json
    python run.py validate --edit-file /path/to/EDIT00000000
    python run.py log                                 # Show recent transfer log

Workflow:
    1. Collect and reconcile FotMob, Wikipedia, and Sortitoutsi transfers
    2. Decrypt and validate the edit file (pesXdecrypter)
    3. Load the selected save’s current player/roster state
    4. Match identities and plan safe roster actions
    5. Apply verified transfers, validate, re-encrypt, and log
"""
import argparse
import hashlib
import json
import logging
import shlex
import struct
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import config
from editor import backup as backup_mod
from editor import crypto
from editor.crypto import CryptoError
from editor.editfile import (
    COMPETITION_SECTION_SIZE,
    MIN_CLUB_ROSTER_SIZE,
    EditFile,
)
from editor import logger as transfer_logger
from editor.player_spec import (
    PLAYER_CREATE_DISABLED_REASON,
    PLAYER_CREATE_MUTATIONS_ENABLED,
    PlayerSpec,
    PlayerSpecError,
    SpecResult,
    apply_player_specs,
    approve_player_proposal,
    assess_create,
    assess_update,
    load_base_manifest,
    load_player_specs,
    validate_spec_set,
    verify_base_file,
)
from editor.player_catalog import PlayerCatalogError, load_id_name_text
from editor.metadata_audit import audit_metadata, format_metadata_audit
from editor.base_audit import audit_base_roster, format_base_roster_audit
from editor.base_refresh import BaseRefreshError, refresh_base
from editor.release_policy import (
    ReleasePolicyError,
    import_usage_csv,
    load_release_policy,
)
from editor.metadata_diff import (
    compare_metadata_variants,
    format_metadata_variant_diff,
)
from editor.player_assignment import PlayerAssignmentDatabase
from editor.playerbin import PlayerBinDatabase
from editor.teambin import TeamBinDatabase
from editor.locking import EditFileLock, EditLockError
from installer.paths import DestinationError, discover_game_cpk, reject_game_root_save
from tools.cpk_extract import read_file as read_cpk_file
from scraper.fotmob import (
    IncompleteScrapeError,
    fetch_fotmob_transfers,
    get_transfer_window_range,
    merge_transfers,
    parse_iso_date,
    parse_iso_datetime,
    fetch_transfers_for_club_names,
    fetch_major_clubs_transfers_safely,
)
from tools.generate_player_draft import (
    PlayerDraftError,
    validate_generated_proposal,
    write_player_draft,
)
from scraper.matcher import NameMatcher
from scraper.models import MatchedTransfer
from scraper.sortitoutsi import fetch_sortitoutsi_transfers
from scraper.sources import reconcile_transfer_sources
from scraper.wikipedia import fetch_wikipedia_transfers
from scraper.transfermarkt import fetch_transfermarkt_transfers
from local_update import (
    CancellationToken,
    LocalUpdateError,
    LocalUpdateProgress,
    LocalUpdateRequest,
    LocalUpdateResult,
    LocalUpdateService,
    LocalUpdateStage,
)

logger = logging.getLogger(__name__)
if not hasattr(config, "BASE_EDIT_PATH"):
    config.BASE_EDIT_PATH = config.EDIT_FILE_PATH
UNRESOLVED_TEAM_ID = -1
_NON_CLUB_LABELS = {"", "free agent", "without club", "unattached", "career break", "retired"}


@dataclass
class PlannedRosterAction:
    match: MatchedTransfer
    action: str
    current_team_id: int | None
    reason: str = ""
    overflow_player_id: int | None = None
    overflow_details: dict[str, object] | None = None


def _optional_positive_int(value) -> int | None:
    """Parse an identifier from external/history data and reject sentinel values."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _resolve_run_paths(args) -> tuple[Path, Path]:
    """Resolve an incremental input and output without discarding prior runs."""
    explicit_input = Path(args.edit_file) if getattr(args, "edit_file", None) else None
    output_arg = getattr(args, "output", None)
    in_place = bool(getattr(args, "in_place", False))
    from_base = bool(getattr(args, "from_base", False))

    if output_arg:
        output_path = Path(output_arg)
    elif in_place:
        output_path = explicit_input or config.EDIT_FILE_PATH
    else:
        output_path = config.OUTPUT_FILE_PATH

    if explicit_input is not None:
        edit_path = explicit_input
    elif from_base or in_place:
        edit_path = config.EDIT_FILE_PATH
    elif output_path.exists():
        # Continue from the last successful output. Re-reading the pristine base
        # on every scheduled run would silently undo transfers that aged out of
        # the current scrape window.
        edit_path = output_path
    else:
        edit_path = config.EDIT_FILE_PATH

    reject_game_root_save(edit_path)
    reject_game_root_save(output_path)
    return edit_path, output_path
def _ensure_safe_edit_paths(*paths: Path) -> None:
    """Fail closed before any command reads or writes a game-root save."""
    try:
        for path in paths:
            reject_game_root_save(path)
    except DestinationError as error:
        print(f"Unsafe save path: {error}")
        raise SystemExit(2) from error



def _sha256_file(path: Path) -> str:
    """Return a stable digest without loading a large EDIT file into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _competition_section_bounds(edit_file: EditFile) -> tuple[int, int]:
    """Return the league-membership section without overlapping game plans."""
    start = edit_file.competition_entry_start
    return start, start + COMPETITION_SECTION_SIZE


def _load_represented_fotmob_club_ids() -> set[int]:
    """Load the generated one-to-one FotMob ↔ PES club identity index."""
    validated_path = config.DATA_DIR / "fotmob_teams_validated.json"
    try:
        validated_payload = json.loads(validated_path.read_text(encoding="utf-8"))
        if not isinstance(validated_payload, list):
            raise ValueError(f"{validated_path} must contain a JSON array")
    except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        raise IncompleteScrapeError(
            f"Could not load FotMob/PES club identity data: {exc}"
        ) from exc

    represented_ids: set[int] = set()
    represented_pes_ids: set[int] = set()
    for item in validated_payload:
        if (
            not isinstance(item, dict)
            or "fotmob_id" not in item
            or "pes_team_id" not in item
        ):
            raise IncompleteScrapeError(
                f"Malformed club identity entry in {validated_path}"
            )
        try:
            fotmob_id = int(item["fotmob_id"])
            pes_team_id = int(item["pes_team_id"])
        except (TypeError, ValueError) as exc:
            raise IncompleteScrapeError(
                f"Non-numeric club identity in {validated_path}: {exc}"
            ) from exc
        if fotmob_id in represented_ids or pes_team_id in represented_pes_ids:
            raise IncompleteScrapeError(
                f"Club identity index is not one-to-one at FotMob {fotmob_id} / "
                f"PES {pes_team_id}"
            )
        represented_ids.add(fotmob_id)
        represented_pes_ids.add(pes_team_id)

    if not represented_ids:
        raise IncompleteScrapeError("Represented FotMob club ID allowlist is empty")
    return represented_ids


def _transfer_sort_key(transfer):
    """Apply dated transfers chronologically and shirt-number updates last."""
    parsed_date = parse_iso_datetime(transfer.date)
    return (
        transfer.transfer_type == "shirt_number_update",
        parsed_date is None,
        parsed_date or parse_iso_datetime("2000-01-01"),
    )


def _match_transfer_team(
    matcher: NameMatcher,
    short_name: str,
    full_name: str = "",
    fotmob_id: int | str | None = None,
    validated_fotmob_ids: set[int] | None = None,
) -> tuple[int | None, str, float]:
    """Match all available club names and reject conflicting identities."""
    raw_names = [full_name or "", short_name or ""]
    if any(name.strip().casefold() in _NON_CLUB_LABELS for name in raw_names if name.strip()):
        return None, "", 100.0

    id_is_validated: bool | None = None
    if fotmob_id is not None and validated_fotmob_ids is not None:
        try:
            id_is_validated = int(fotmob_id) in validated_fotmob_ids
        except (TypeError, ValueError):
            return UNRESOLVED_TEAM_ID, "", 0.0

    names: list[str] = []
    for value in (full_name, short_name):
        clean = (value or "").strip()
        if clean and clean.casefold() not in {name.casefold() for name in names}:
            names.append(clean)

    results = [matcher.match_team(name) for name in names]
    matched_ids = {team_id for team_id, _, _ in results if team_id is not None}
    if len(matched_ids) > 1:
        logger.warning(
            "Conflicting club identities for %s: %s",
            names,
            sorted(matched_ids),
        )
        return UNRESOLVED_TEAM_ID, "", max(
            (confidence for _, _, confidence in results), default=0.0
        )
    if matched_ids:
        team_id = next(iter(matched_ids))
        best_result = max(
            (result for result in results if result[0] == team_id),
            key=lambda result: result[2],
        )
        if id_is_validated is False:
            if best_result[2] >= 98.0:
                logger.warning(
                    "FotMob club %s (%s) strongly matches PES but its ID is not "
                    "validated; skipping mutations until the identity index is fixed",
                    full_name or short_name,
                    fotmob_id,
                )
                return UNRESOLVED_TEAM_ID, "", best_result[2]
            # A weakly similar, non-allowlisted ID is treated as a club that is
            # genuinely absent from PES, enabling a safe release/signing.
            return None, "", best_result[2]
        if fotmob_id is None and best_result[2] < 98.0:
            logger.warning(
                "Rejecting ID-less fuzzy club match for %s at %.1f%%",
                full_name or short_name,
                best_result[2],
            )
            return UNRESOLVED_TEAM_ID, "", best_result[2]
        return best_result
    best_unresolved_confidence = max(
        (confidence for _, _, confidence in results), default=0.0
    )
    if id_is_validated is True or best_unresolved_confidence >= 98.0:
        return UNRESOLVED_TEAM_ID, "", best_unresolved_confidence
    return None, "", best_unresolved_confidence


def _transfer_event_order_key(
    indexed_transfer: tuple[int, object],
) -> tuple[bool, datetime, int]:
    """Order transfer events by timestamp while preserving unknown-order input."""
    index, transfer = indexed_transfer
    event_datetime = parse_iso_datetime(str(getattr(transfer, "date", "") or ""))
    return (
        event_datetime is None,
        event_datetime or datetime.max.replace(tzinfo=timezone.utc),
        index,
    )

def _match_transfers_statefully(
    transfers,
    matcher: NameMatcher,
    threshold: float,
    team_player_map: dict[int, list[int]],
    club_ids: set[int],
    historical_entries: list[dict] | None = None,
    validated_fotmob_ids: set[int] | None = None,
) -> list[MatchedTransfer]:
    """Match transfer events chronologically while advancing virtual rosters."""
    virtual_rosters = {
        team_id: list(player_ids)
        for team_id, player_ids in team_player_map.items()
    }
    loaned_by_parent: dict[int, set[int]] = {}
    prior_permanent_routes: dict[
        tuple[int, int], list[tuple[int, datetime]]
    ] = {}
    fotmob_identity_candidates: dict[int, set[int]] = {}
    fotmob_identity_names: dict[int, str] = {}

    history = sorted(
        historical_entries or [],
        key=lambda entry: parse_iso_datetime(
            str(entry.get("transfer_date") or entry.get("timestamp") or "")
        ) or parse_iso_datetime("2000-01-01"),
    )
    for entry in history:
        player_id = _optional_positive_int(entry.get("player_id"))
        fotmob_player_id = _optional_positive_int(entry.get("fotmob_player_id"))
        if player_id and fotmob_player_id:
            fotmob_identity_candidates.setdefault(fotmob_player_id, set()).add(
                player_id
            )
            if entry.get("player_name"):
                fotmob_identity_names[fotmob_player_id] = str(entry["player_name"])
        source = _optional_positive_int(entry.get("from_team_id"))
        if not player_id or not source:
            continue
        transfer_type = str(entry.get("transfer_type", "")).lower()
        destination = _optional_positive_int(entry.get("to_team_id"))
        event_datetime = parse_iso_datetime(
            str(entry.get("transfer_date") or entry.get("timestamp") or "")
        )
        if transfer_type == "loan":
            loaned_by_parent.setdefault(source, set()).add(player_id)
        else:
            loaned_by_parent.get(source, set()).discard(player_id)
            if (
                destination
                and event_datetime is not None
                and transfer_type not in {"end of loan", "shirt_number_update"}
            ):
                prior_permanent_routes.setdefault((player_id, source), []).append(
                    (destination, event_datetime)
                )

    fotmob_to_pes = {
        fotmob_player_id: next(iter(player_ids))
        for fotmob_player_id, player_ids in fotmob_identity_candidates.items()
        if len(player_ids) == 1
    }

    ordered_transfers = [
        transfer
        for _, transfer in sorted(
            enumerate(transfers),
            key=_transfer_event_order_key,
        )
    ]
    matched: list[MatchedTransfer] = []

    for transfer in ordered_transfers:
        transfer_datetime = parse_iso_datetime(
            str(getattr(transfer, "date", "") or "")
        )
        ftid, ftname, ftconf = _match_transfer_team(
            matcher,
            transfer.from_club,
            transfer.from_club_full_name,
            transfer.from_club_id_fotmob,
            validated_fotmob_ids,
        )
        ttid, ttname, ttconf = _match_transfer_team(
            matcher,
            transfer.to_club,
            transfer.to_club_full_name,
            transfer.to_club_id_fotmob,
            validated_fotmob_ids,
        )

        context_map = virtual_rosters
        parent_loaned = loaned_by_parent.get(ftid, set()) if ftid is not None else set()
        if ftid is not None and parent_loaned:
            context_map = dict(virtual_rosters)
            context_map[ftid] = list(
                dict.fromkeys(virtual_rosters.get(ftid, []) + list(parent_loaned))
            )

        pid, pname, pconf = matcher.match_player(
            transfer.player_name,
            threshold=threshold,
            from_team_id=ftid,
            to_team_id=ttid,
            team_player_map=context_map,
            position=transfer.position,
            nationality=transfer.nationality,
            age=transfer.age,
        )
        fotmob_player_id = _optional_positive_int(transfer.player_id_fotmob)
        known_pid = fotmob_to_pes.get(fotmob_player_id) if fotmob_player_id else None
        if known_pid is not None and pid is None:
            pid = known_pid
            pname = fotmob_identity_names.get(
                fotmob_player_id, transfer.player_name
            )
            pconf = 100.0
        elif known_pid is not None and pid != known_pid:
            logger.warning(
                "FotMob player %s conflicts with PES history (%s vs %s); skipping",
                transfer.player_id_fotmob,
                known_pid,
                pid,
            )
            pid, pname = None, ""

        current_clubs = (
            [
                team_id
                for team_id, roster in virtual_rosters.items()
                if team_id in club_ids and pid in roster
            ]
            if pid is not None
            else []
        )
        current_team_id = current_clubs[0] if len(current_clubs) == 1 else None
        if ftid is None and transfer.infer_from_current_roster:
            inferred_team_id = current_team_id
            inferred_team_name = (
                matcher.get_team_name(inferred_team_id)
                if inferred_team_id is not None
                else ""
            )
            can_infer_source = (
                transfer.verification_status == "enabled"
                and bool(transfer.proof_urls)
                and pconf == 100.0
                and ttid is not None
                and ttid >= 0
                and inferred_team_id is not None
                and bool(inferred_team_name)
            )
            if can_infer_source:
                ftid = inferred_team_id
                ftname = inferred_team_name
                ftconf = 100.0
                transfer.from_club = ftname
                transfer.from_club_full_name = ftname
                logger.info(
                    "Inferred moderated transfer source from unique current roster: "
                    "%s (%s) -> %s",
                    transfer.player_name,
                    ftname,
                    transfer.to_club,
                )
            else:
                # A destination-only community signal must never degrade into a
                # generic free-agent signing or release when source inference is
                # ambiguous.
                ftid = UNRESOLVED_TEAM_ID
                ftname = ""
                ftconf = 0.0

        is_loan_transfer = (
            transfer.is_loan or transfer.transfer_type == "loan"
        )
        if (
            pid is not None
            and is_loan_transfer
            and ftid is not None
            and ftid >= 0
            and ttid is not None
            and ttid >= 0
            and current_team_id is not None
            and current_team_id != ftid
            and current_team_id != ttid
            and transfer_datetime is not None
            and any(
                destination_team_id == current_team_id
                and route_datetime <= transfer_datetime
                for destination_team_id, route_datetime in prior_permanent_routes.get(
                    (pid, ftid), []
                )
            )
        ):
            stale_source_id = ftid
            stale_source_name = ftname or transfer.from_club
            ftid = current_team_id
            ftname = matcher.get_team_name(current_team_id) or ftname
            ftconf = 100.0
            logger.info(
                "Reconciled stale loan source for %s: %s (%s) -> %s (%s)",
                transfer.player_name,
                stale_source_name,
                stale_source_id,
                ftname or "current roster",
                current_team_id,
            )

        if pid is not None and fotmob_player_id is not None:
            existing_pid = fotmob_to_pes.get(fotmob_player_id)
            if existing_pid is None:
                fotmob_to_pes[fotmob_player_id] = pid
                fotmob_identity_names[fotmob_player_id] = pname or transfer.player_name
        match = MatchedTransfer(
            transfer=transfer,
            player_id=pid,
            from_team_id=ftid,
            to_team_id=ttid,
            player_confidence=pconf,
            from_team_confidence=ftconf,
            to_team_confidence=ttconf,
            matched_player_name=pname,
            matched_from_team=ftname,
            matched_to_team=ttname,
        )
        matched.append(match)

        if (
            pid is None
            or ftid == UNRESOLVED_TEAM_ID
            or transfer.transfer_type == "shirt_number_update"
        ):
            continue

        if (
            ftid is not None
            and ttid is not None
            and current_team_id == ftid
            and transfer_datetime is not None
            and not is_loan_transfer
            and transfer.transfer_type != "end of loan"
        ):
            prior_permanent_routes.setdefault((pid, ftid), []).append(
                (ttid, transfer_datetime)
            )

        can_move_from_parent = (
            ftid is not None
            and pid in loaned_by_parent.get(ftid, set())
            and current_team_id is not None
        )

        if ftid is not None and ttid is not None and (
            current_team_id == ftid or can_move_from_parent
        ):
            virtual_rosters[current_team_id].remove(pid)
            if pid not in virtual_rosters.setdefault(ttid, []):
                virtual_rosters[ttid].append(pid)
        elif ftid is None and ttid is not None and current_team_id is None:
            virtual_rosters.setdefault(ttid, []).append(pid)
        elif ftid is not None and ttid is None and current_team_id == ftid:
            virtual_rosters[ftid].remove(pid)

        if is_loan_transfer and ftid is not None:
            loaned_by_parent.setdefault(ftid, set()).add(pid)
        elif ftid is not None:
            loaned_by_parent.get(ftid, set()).discard(pid)

    return matched


def _decide_roster_action(
    current_team_id: int | None,
    from_team_id: int | None,
    to_team_id: int | None,
    transfer_type: str,
    superseded_loan_team_ids: frozenset[int] = frozenset(),
) -> str:
    """Choose a fail-closed roster mutation from the verified current state."""
    if transfer_type == "shirt_number_update":
        return "shirt_update" if to_team_id is not None and current_team_id == to_team_id else "skip"

    if from_team_id is not None and to_team_id is not None:
        if current_team_id == to_team_id:
            return "noop"
        if (
            current_team_id == from_team_id
            or current_team_id in superseded_loan_team_ids
        ):
            return "move"
        return "skip"

    if from_team_id is None and to_team_id is not None:
        if current_team_id == to_team_id:
            return "noop"
        if current_team_id is None:
            return "add"
        return "skip"

    if from_team_id is not None and to_team_id is None:
        if current_team_id == from_team_id:
            return "release"
        if current_team_id is None:
            return "noop"
        return "skip"

    return "skip"


def _build_superseded_loan_sources(
    matches: list[MatchedTransfer],
    historical_entries: list[dict] | None = None,
) -> dict[int, frozenset[int]]:
    """Authorize newer parent-club moves from an earlier loan destination.

    Transfer feeds commonly omit the synthetic loan-return event. For example,
    PSG -> Tottenham (loan) followed by PSG -> Juventus (permanent) leaves a
    current PES roster at Tottenham even though the newer event names PSG as
    its source. Only a strictly earlier, fully matched loan from that same
    parent club can authorize the stale loan club as the actual move source.
    """
    prior_loans: dict[int, list[tuple[int, int, datetime]]] = {}
    allowed_sources: dict[int, frozenset[int]] = {}

    for entry in historical_entries or []:
        if str(entry.get("transfer_type", "")).lower() != "loan":
            continue
        try:
            player_id = int(entry.get("player_id") or 0)
            parent_team_id = int(entry.get("from_team_id") or 0)
            loan_team_id = int(entry.get("to_team_id") or 0)
        except (TypeError, ValueError):
            continue
        transfer_date = parse_iso_datetime(
            str(entry.get("transfer_date") or entry.get("timestamp") or "")
        )
        if player_id and parent_team_id and loan_team_id and transfer_date:
            prior_loans.setdefault(player_id, []).append(
                (parent_team_id, loan_team_id, transfer_date)
            )

    for match in matches:
        player_id = match.player_id
        transfer_date = parse_iso_datetime(match.transfer.date)
        if (
            player_id is not None
            and match.from_team_id is not None
            and match.to_team_id is not None
            and transfer_date is not None
            and (match.transfer.is_loan or match.transfer.transfer_type == "loan")
        ):
            prior_loans.setdefault(player_id, []).append(
                (match.from_team_id, match.to_team_id, transfer_date)
            )

    for match in matches:
        player_id = match.player_id
        transfer_date = parse_iso_datetime(match.transfer.date)
        if (
            player_id is not None
            and match.from_team_id is not None
            and match.to_team_id is not None
            and transfer_date is not None
        ):
            allowed_sources[id(match)] = frozenset(
                loan_team_id
                for parent_team_id, loan_team_id, loan_date in prior_loans.get(
                    player_id, []
                )
                if parent_team_id == match.from_team_id
                and loan_team_id != match.to_team_id
                and loan_date < transfer_date
            )

    return allowed_sources


def _plan_roster_actions(
    matches: list[MatchedTransfer],
    all_rosters: dict,
    club_ids: set[int],
    edit_file: EditFile,
    superseded_loan_sources: dict[int, frozenset[int]],
    allow_overflow_release: bool = True,
) -> list[PlannedRosterAction]:
    """Build one chronological roster plan and simulate every accepted action."""
    rosters = {
        team_id: list(roster.player_ids)
        for team_id, roster in all_rosters.items()
        if team_id in club_ids
    }
    player_clubs: dict[int, set[int]] = {}
    for team_id, player_ids in rosters.items():
        for player_id in player_ids:
            if player_id:
                player_clubs.setdefault(player_id, set()).add(team_id)

    planned: list[PlannedRosterAction] = []
    transferred_in_plan: set[int] = set()

    def remove_from_roster(team_id: int, player_id: int) -> None:
        roster = rosters[team_id]
        player_index = roster.index(player_id)
        last_index = max(index for index, value in enumerate(roster) if value)
        roster[player_index] = roster[last_index]
        roster[last_index] = 0
        player_clubs.get(player_id, set()).discard(team_id)

    def add_to_roster(team_id: int, player_id: int) -> None:
        roster = rosters[team_id]
        roster[roster.index(0)] = player_id
        player_clubs.setdefault(player_id, set()).add(team_id)

    for match in matches:
        if not match.is_fully_matched:
            if match.player_id is None:
                reason = "player_not_matched"
            elif match.from_team_id == UNRESOLVED_TEAM_ID:
                reason = "source_team_not_matched"
            elif match.to_team_id == UNRESOLVED_TEAM_ID:
                reason = "destination_team_not_matched"
            else:
                reason = "transfer_not_fully_matched"
            planned.append(PlannedRosterAction(match, "skip", None, reason))
            continue

        player_id = match.player_id

        current_clubs = sorted(player_clubs.get(player_id, set()))
        if len(current_clubs) > 1:
            planned.append(
                PlannedRosterAction(
                    match,
                    "skip",
                    None,
                    f"duplicate_registration:{current_clubs}",
                )
            )
            continue

        current_team_id = current_clubs[0] if current_clubs else None
        action = _decide_roster_action(
            current_team_id,
            match.from_team_id,
            match.to_team_id,
            match.transfer.transfer_type,
            superseded_loan_sources.get(id(match), frozenset()),
        )
        item = PlannedRosterAction(match, action, current_team_id)
        if (
            action in {"move", "release"}
            and current_team_id in rosters
            and sum(player_id != 0 for player_id in rosters[current_team_id])
            <= MIN_CLUB_ROSTER_SIZE
        ):
            item.action = "skip"
            item.reason = "source_roster_minimum"

        if item.action in {"move", "add"}:
            destination = match.to_team_id
            if destination is None or destination not in rosters:
                item.action = "skip"
                item.reason = "destination_roster_missing"
            elif 0 not in rosters[destination]:
                if not allow_overflow_release:
                    item.action = "skip"
                    item.reason = "destination_roster_full"
                else:
                    _, overflow_player_id = edit_file.find_overflow_release_candidate(
                        destination,
                        exclude_player_id=player_id,
                        roster_player_ids=rosters[destination],
                        protected_player_ids=transferred_in_plan,
                    )
                    if not overflow_player_id:
                        item.action = "skip"
                        item.reason = "no_safe_overflow_candidate"
                    else:
                        item.overflow_player_id = overflow_player_id
                        describe = getattr(
                            edit_file,
                            "describe_overflow_release_candidate",
                            None,
                        )
                        if callable(describe):
                            item.overflow_details = describe(
                                destination,
                                overflow_player_id,
                                roster_player_ids=rosters[destination],
                                protected_player_ids=transferred_in_plan,
                            )
                        remove_from_roster(destination, overflow_player_id)

        if item.action == "move":
            if current_team_id is None or current_team_id not in rosters:
                item.action = "skip"
                item.reason = "source_roster_missing"
            else:
                remove_from_roster(current_team_id, player_id)
                add_to_roster(match.to_team_id, player_id)
                transferred_in_plan.add(player_id)
        elif item.action == "add":
            add_to_roster(match.to_team_id, player_id)
            transferred_in_plan.add(player_id)
        elif item.action == "release" and current_team_id is not None:
            remove_from_roster(current_team_id, player_id)

        planned.append(item)

    return planned


def _dedupe_shirt_number_matches(
    matches: list[MatchedTransfer],
) -> tuple[list[MatchedTransfer], int]:
    """Keep one fail-closed shirt-number observation per player and club."""
    regular: list[MatchedTransfer] = []
    groups: dict[tuple[int, int], list[MatchedTransfer]] = {}

    for match in matches:
        if (
            match.transfer.transfer_type != "shirt_number_update"
            or match.player_id is None
            or match.to_team_id is None
        ):
            regular.append(match)
            continue
        groups.setdefault((match.player_id, match.to_team_id), []).append(match)

    skipped = 0
    for group in groups.values():
        ranked = sorted(group, key=lambda item: item.min_confidence, reverse=True)
        winner = ranked[0]
        conflicting = [
            item
            for item in ranked[1:]
            if item.transfer.shirt_number != winner.transfer.shirt_number
        ]
        if conflicting and winner.min_confidence - conflicting[0].min_confidence < 3.0:
            skipped += len(group)
            continue
        regular.append(winner)
        skipped += len(group) - 1

    return regular, skipped


def _iso_date_arg(value: str) -> str:
    parsed = parse_iso_date(value)
    if parsed is None or value != parsed.isoformat():
        raise argparse.ArgumentTypeError("expected an ISO date in YYYY-MM-DD format")
    return value


def _percentage_arg(value: str) -> float:
    try:
        number = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected a number from 0 to 100") from exc
    if not 0 <= number <= 100:
        raise argparse.ArgumentTypeError("expected a number from 0 to 100")
    return number


def _positive_int_arg(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected a positive integer") from exc
    if number < 1:
        raise argparse.ArgumentTypeError("expected a positive integer")
    return number


def _positive_float_arg(value: str) -> float:
    try:
        number = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected a positive number") from exc
    if number <= 0:
        raise argparse.ArgumentTypeError("expected a positive number")
    return number


def setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def cmd_inspect(args):
    """Inspect an edit file — show structure, counts, offsets."""
    edit_path = Path(args.edit_file)
    _ensure_safe_edit_paths(edit_path)

    print(f"Decrypting {edit_path}...")
    try:
        temp_dir = crypto.decrypt(edit_path)
    except Exception as e:
        print(f"Decryption failed: {e}")
        print("Make sure pesXdecrypter is installed. See MEMORY.md §4.")
        sys.exit(1)

    try:
        data_dat = temp_dir / "data.dat"
        if not data_dat.exists():
            dat_files = list(temp_dir.glob("*.dat"))
            if dat_files:
                data_dat = max(dat_files, key=lambda f: f.stat().st_size)
            else:
                print(f"No .dat files found in {temp_dir}")
                sys.exit(1)

        ef = EditFile()
        ef.load(data_dat)
        ef.print_summary()

        integrity = ef.validate_integrity()
        status = "PASS" if integrity["valid"] else "FAIL"
        print(f"\nIntegrity: {status}")
        print(f"  Metrics: {integrity['metrics']}")
        for error in integrity["errors"][:20]:
            print(f"  ERROR: {error}")
        if len(integrity["errors"]) > 20:
            print(f"  ... and {len(integrity['errors']) - 20} more errors")

        print("\n--- League Divisions in Save File ---")
        teams = ef.get_all_team_info()
        entry_start, entry_end = _competition_section_bounds(ef)
        entry_size = entry_end - entry_start
        num_slots = entry_size // 4

        clusters = []
        current_cluster = []
        for i in range(num_slots):
            tid = struct.unpack_from("<I", ef._data, entry_start + i * 4)[0]
            if tid != 0 and tid != 0xFFFF0300 and tid in teams:
                current_cluster.append(teams[tid].name)
            else:
                if current_cluster:
                    clusters.append(current_cluster)
                    current_cluster = []
        if current_cluster:
            clusters.append(current_cluster)

        for idx, cl in enumerate(clusters):
            preview = ", ".join(cl[:3])
            suffix = f" ... [{cl[-1]}]" if len(cl) > 3 else ""
            print(f"  Division {idx+1:2d} ({len(cl):2d} teams): {preview}{suffix}")

        club_ids = ef.get_club_team_ids()
        print(f"\nTotal Clubs: {len(club_ids)}")
        print(f"Total Other/National Teams: {len(set(teams) - club_ids)}")
        managers = ef.get_all_managers()
        print(f"Total Managers / Coaches: {len(managers)}")

    finally:
        crypto.cleanup_temp(temp_dir)




def cmd_metadata_audit(args):
    """Audit one save against the selected native game metadata variant."""
    edit_path = Path(args.edit_file)
    _ensure_safe_edit_paths(edit_path)
    json_output = bool(getattr(args, "json", False))
    if not json_output:
        print(f"Decrypting {edit_path}...")
    try:
        temp_dir = crypto.decrypt(edit_path)
    except Exception as exc:
        print(f"Decryption failed: {exc}")
        print("Make sure pesXdecrypter is installed. See MEMORY.md §4.")
        sys.exit(1)

    try:
        data_dat = temp_dir / "data.dat"
        if not data_dat.exists():
            dat_files = list(temp_dir.glob("*.dat"))
            if dat_files:
                data_dat = max(dat_files, key=lambda path: path.stat().st_size)
            else:
                print(f"No .dat files found in {temp_dir}")
                sys.exit(1)

        edit_file = EditFile()
        edit_file.load(data_dat)
        game_root = getattr(args, "game_root", None)
        playerbin_db, playerbin_source = _load_playerbin_database(
            getattr(args, "player_bin", None),
            game_root=game_root,
        )
        teambin_db, teambin_source = _load_teambin_database(
            getattr(args, "team_bin", None),
            game_root=game_root,
        )
        assignment_db, assignment_source = _load_player_assignment_database(
            getattr(args, "player_assignment", None),
            game_root=game_root,
        )
        as_of = date.fromisoformat(
            getattr(args, "as_of", None) or date.today().isoformat()
        )
        report = audit_metadata(
            edit_file,
            playerbin_db,
            teambin_db,
            assignment_db,
            as_of=as_of,
        )
        sources = {
            "Player.bin": playerbin_source,
            "Team.bin": teambin_source,
            "PlayerAssignment.bin": assignment_source,
        }
        if json_output:
            payload = report.to_dict()
            payload["sources"] = sources
            print(json.dumps(payload, sort_keys=True))
        else:
            print(format_metadata_audit(report))
            print("\nSources:")
            for label, source in sources.items():
                print(f"  {label}: {source or 'unavailable'}")
    finally:
        crypto.cleanup_temp(temp_dir)


def cmd_base_roster_audit(args) -> None:
    """Audit active Player Updates against one decrypted base roster."""
    edit_path = Path(args.edit_file)
    _ensure_safe_edit_paths(edit_path)
    json_output = bool(getattr(args, "json", False))
    strict = bool(getattr(args, "strict", False))
    temp_dir = None
    try:
        temp_dir = crypto.decrypt(edit_path)
        data_dat = temp_dir / "data.dat"
        if not data_dat.exists():
            dat_files = list(temp_dir.glob("*.dat"))
            if not dat_files:
                raise PlayerSpecError("decryption produced no data block")
            data_dat = max(dat_files, key=lambda path: path.stat().st_size)
        edit_file = EditFile()
        edit_file.load(data_dat)
        specs = load_player_specs(getattr(args, "spec_dir", None))
        as_of = date.fromisoformat(
            getattr(args, "as_of", None) or date.today().isoformat()
        )
        report = audit_base_roster(edit_file, specs, as_of=as_of)
        if json_output:
            print(json.dumps(report.to_dict(), sort_keys=True))
        else:
            print(format_base_roster_audit(report))
        if strict and not report.valid:
            raise SystemExit(2)
    except Exception as exc:
        print(f"Base roster audit failed: {exc}")
        raise SystemExit(2) from exc
    finally:
        if temp_dir is not None:
            crypto.cleanup_temp(temp_dir)


def cmd_base_refresh(args) -> None:
    """Verify and optionally promote one local or HTTPS EDIT base candidate."""
    try:
        report = refresh_base(
            args.source,
            revision=args.revision,
            spec_dir=getattr(args, "spec_dir", None),
            as_of=date.fromisoformat(
                getattr(args, "as_of", None) or date.today().isoformat()
            ),
            promote=bool(getattr(args, "promote", False)),
            strict_audit=bool(getattr(args, "strict_audit", False)),
        )
        if getattr(args, "json", False):
            print(json.dumps(report.to_dict(), sort_keys=True))
        else:
            print(
                f"Base candidate verified: {report.candidate}\n"
                f"  SHA-256: {report.sha256}\n"
                f"  Size: {report.size:,} bytes\n"
                f"  Audit: {'PASS' if report.audit.valid else 'ATTENTION'} "
                f"({report.audit.issue_count} issue(s))\n"
                f"  Promoted: {report.promoted}"
            )
    except BaseRefreshError as exc:
        print(f"Base refresh failed: {exc}")
        raise SystemExit(2) from exc


def cmd_usage_import(args) -> None:
    """Merge CSV player usage counters into the offline release policy."""
    try:
        policy = import_usage_csv(
            args.input,
            getattr(args, "output", None),
            source=getattr(args, "source", ""),
            season=getattr(args, "season", ""),
            as_of=getattr(args, "as_of", ""),
        )
        print(
            f"Imported {len(policy.usage)} usage snapshots into "
            f"{getattr(args, 'output', None) or config.RELEASE_POLICY_FILE}"
        )
    except ReleasePolicyError as exc:
        print(f"Usage import failed: {exc}")
        raise SystemExit(2) from exc
def _resolve_comparison_cpk(
    explicit_path: Path | str | None,
    game_root: Path | str | None,
    label: str,
) -> Path:
    if explicit_path is not None:
        path = Path(explicit_path)
        if not path.is_file():
            raise FileNotFoundError(f"{label} CPK not found: {path}")
        return path
    path = discover_game_cpk(None if game_root is None else Path(game_root))
    if path is None:
        raise FileNotFoundError(
            f"{label} CPK not found below {game_root or 'configured game roots'}"
        )
    return path


def _load_metadata_variant_from_cpk(path: Path):
    def load(database_type, member: str):
        return database_type.from_bytes(read_cpk_file(path, member))

    return (
        load(PlayerBinDatabase, _PLAYER_BIN_CPK_MEMBER),
        load(TeamBinDatabase, _TEAM_BIN_CPK_MEMBER),
        load(PlayerAssignmentDatabase, _PLAYER_ASSIGNMENT_CPK_MEMBER),
    )


def cmd_compare_metadata(args):
    """Compare supported native metadata between two CPK variants."""
    try:
        left_path = _resolve_comparison_cpk(
            getattr(args, "left_cpk", None),
            getattr(args, "left_game_root", None),
            "Left",
        )
        right_path = _resolve_comparison_cpk(
            getattr(args, "right_cpk", None),
            getattr(args, "right_game_root", None),
            "Right",
        )
        left_databases = _load_metadata_variant_from_cpk(left_path)
        right_databases = _load_metadata_variant_from_cpk(right_path)
        report = compare_metadata_variants(
            str(left_path),
            *left_databases,
            str(right_path),
            *right_databases,
        )
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise SystemExit(f"Metadata comparison failed: {exc}") from exc

    if getattr(args, "json", False):
        print(json.dumps(report.to_dict(), sort_keys=True))
    else:
        print(format_metadata_variant_diff(report))




def cmd_validate(args):
    """Validate an encrypted edit file with a supported PES edit-file layout."""
    edit_path = Path(args.edit_file)
    _ensure_safe_edit_paths(edit_path)
    print(f"Validating {edit_path}...")
    temp_dir = crypto.decrypt(edit_path)
    try:
        ef = EditFile(temp_dir / "data.dat")
        ef.load()
        report = ef.validate_integrity()
        print(f"Metrics: {report['metrics']}")
        for warning in report["warnings"]:
            print(f"WARNING: {warning}")
        for error in report["errors"]:
            print(f"ERROR: {error}")
        if report["valid"]:
            print("PASS: save structure matches the supported PES edit-file layout")
            return
        print(
            f"FAIL: {len(report['errors'])} supported PES edit-file layout "
            "error(s)"
        )
        raise SystemExit(2)
    finally:
        crypto.cleanup_temp(temp_dir)


def cmd_repair(args):
    """Repair a legacy base using consensus registrations from valid references."""
    edit_path = Path(args.edit_file)
    output_path = Path(args.output) if args.output else config.OUTPUT_FILE_PATH
    reference_paths = [Path(path) for path in args.reference]
    _ensure_safe_edit_paths(edit_path, output_path, *reference_paths)

    base_temp = crypto.decrypt(edit_path)
    reference_temps: list[Path] = []
    try:
        ef = EditFile(base_temp / "data.dat")
        ef.load()
        league_block_start, league_block_end = _competition_section_bounds(ef)
        original_league_block = bytes(ef._data[league_block_start:league_block_end])

        references: list[EditFile] = []
        for reference_path in reference_paths:
            reference_temp = crypto.decrypt(reference_path)
            reference_temps.append(reference_temp)
            reference = EditFile(reference_temp / "data.dat")
            reference.load()
            report = reference.validate_integrity()
            if not report["valid"]:
                raise ValueError(
                    f"Reference is not structurally valid: {reference_path} "
                    f"({len(report['errors'])} errors)"
                )
            references.append(reference)

        base_clubs = ef.get_club_team_ids()
        registrations: dict[int, list[int]] = {}
        for tid, roster in ef.get_all_rosters().items():
            if tid not in base_clubs:
                continue
            for player_id in roster.roster:
                registrations.setdefault(player_id, []).append(tid)
        duplicates = {
            player_id: teams
            for player_id, teams in registrations.items()
            if len(teams) > 1
        }

        repaired_duplicates = 0
        released_to_free_agent = 0
        players = ef.get_all_players()
        ef._player_cache = players
        for player_id, current_teams in sorted(duplicates.items()):
            votes: list[int | None] = []
            for reference in references:
                reference_teams = reference.find_player_teams(player_id, club_only=True)
                if len(reference_teams) > 1:
                    raise ValueError(
                        f"Reference unexpectedly registers player {player_id} to multiple clubs"
                    )
                votes.append(reference_teams[0] if reference_teams else None)

            preferred_team, vote_count = Counter(votes).most_common(1)[0]
            if vote_count <= len(references) // 2:
                raise ValueError(
                    f"References do not agree how to repair player {player_id}: {votes}"
                )

            for team_id in list(current_teams):
                if team_id != preferred_team and not ef.release_player(player_id, team_id):
                    raise RuntimeError(f"Could not remove duplicate player {player_id} from {team_id}")

            if preferred_team is None:
                released_to_free_agent += 1
            elif preferred_team not in ef.find_player_teams(player_id, club_only=True):
                player = players.get(player_id)
                if not ef.add_player(
                    player_id,
                    preferred_team,
                    position=player.position if player else "",
                ):
                    raise RuntimeError(
                        f"Could not place duplicate player {player_id} on consensus team "
                        f"{preferred_team}"
                    )
            repaired_duplicates += 1

        game_plan_repairs = ef.repair_game_plans()
        if bytes(ef._data[league_block_start:league_block_end]) != original_league_block:
            raise RuntimeError("Repair attempted to change league promotion/division membership")

        final_report = ef.validate_integrity()
        if not final_report["valid"]:
            for error in final_report["errors"][:20]:
                print(f"ERROR: {error}")
            raise RuntimeError(
                f"Repair did not produce a valid save ({len(final_report['errors'])} errors remain)"
            )

        ef.save(base_temp / "data.dat")
        replaced_output_backup = None
        if output_path.exists():
            replaced_output_backup = backup_mod.create_backup(output_path)
        crypto.encrypt(base_temp, output_path)
        print(f"PASS: repaired legacy base → {output_path}")
        if replaced_output_backup is not None:
            print(f"  Previous output backup: {replaced_output_backup}")
        print(f"  Duplicate player registrations repaired: {repaired_duplicates}")
        print(f"  Players released by reference consensus: {released_to_free_agent}")
        print(f"  Game-plan repair: {game_plan_repairs}")
        print("  League/division membership: preserved byte-for-byte from the legacy base")
    finally:
        for reference_temp in reference_temps:
            crypto.cleanup_temp(reference_temp)
        crypto.cleanup_temp(base_temp)



def _deep_transfer_since_date(
    window: str,
    since_date: str | None,
) -> str | None:
    """Avoid replaying stale club-history events against the current base."""
    if since_date is not None or (window or "auto").casefold() != "auto":
        return since_date
    return date.today().replace(month=1, day=1).isoformat()


def _scrape_run_transfers(args):
    """Fetch, merge, order, and preview transfers for one pipeline run."""
    popular_only = bool(getattr(args, "popular", False))
    window = getattr(args, "window", "auto") or "auto"
    since_date = getattr(args, "since", None)
    club_filter = getattr(args, "club", None)
    deep_mode = bool(getattr(args, "deep", False))
    fotmob_only = bool(getattr(args, "fotmob_only", False))

    start_date, end_date = get_transfer_window_range(window)
    cutoff_info = (
        f"since {since_date}"
        if since_date
        else f"window '{window}' ({start_date} to {end_date or 'latest'})"
    )
    transfer_batches = []
    if club_filter:
        clubs = [club.strip() for club in club_filter.split(",") if club.strip()]
        print(
            f"\n🎯 Scraping club-focused transfers for: {', '.join(clubs)} "
            f"({cutoff_info})..."
        )
        transfer_batches.append(
            fetch_transfers_for_club_names(
                clubs, since_date=since_date, window=window
            )
        )
    elif deep_mode:
        print(
            "\n🌪️ Deep Mode: Scraping transfers and squads for indexed clubs "
            f"({cutoff_info})..."
        )
        deep_since_date = _deep_transfer_since_date(window, since_date)
        transfer_batches.append(
            fetch_major_clubs_transfers_safely(
                since_date=deep_since_date, window=window
            )
        )
        print(
            "\n📡 Adding Live Global Feed to catch other minor leagues "
            f"({cutoff_info}, automatic pagination)..."
        )
        transfer_batches.append(
            fetch_fotmob_transfers(
                popular_only=popular_only,
                since_date=since_date,
                window=window,
            )
        )
    else:
        print(
            f"\n⚡ Fast Mode: Scraping live transfers from FotMob "
            f"({cutoff_info}, automatic pagination)..."
        )
        transfer_batches.append(
            fetch_fotmob_transfers(
                popular_only=popular_only,
                since_date=since_date,
                window=window,
            )
        )

    fast_signals = []
    corroborators = []
    if not club_filter and not fotmob_only:
        print(
            "\n🌐 Adding confirmed Wikipedia transfer lists "
            f"({cutoff_info})..."
        )
        wikipedia_transfers = fetch_wikipedia_transfers(
            since_date=since_date,
            window=window,
        )
        wikipedia_events = [
            transfer for transfer in wikipedia_transfers if transfer.date
        ]
        wikipedia_corroborators = [
            transfer
            for transfer in wikipedia_transfers
            if not transfer.date
            and transfer.verification_status == "corroborator"
        ]
        transfer_batches.append(wikipedia_events)
        corroborators.extend(wikipedia_corroborators)
        print(
            f"  Wikipedia found {len(wikipedia_events)} dated transfers and "
            f"{len(wikipedia_corroborators)} undated route corroborators"
        )

        print("\n🚦 Adding moderated Sortitoutsi fast signals...")
        fast_signals = fetch_sortitoutsi_transfers(since_date=since_date)
        print(f"  Sortitoutsi found {len(fast_signals)} enabled signals")

        print("\n🔎 Adding verified Transfermarkt detailed transfers...")
        transfermarkt_events = fetch_transfermarkt_transfers(
            since_date=since_date or start_date
        )
        transfer_batches.append(transfermarkt_events)
        print(
            f"  Transfermarkt found {len(transfermarkt_events)} dated transfers"
        )

    transfers = (
        reconcile_transfer_sources(
            transfer_batches,
            fast_signals,
            corroborators,
        )
        if fast_signals or corroborators or len(transfer_batches) > 1
        else merge_transfers(transfer_batches)
    )
    # Apply historical moves oldest-to-newest. Current squad shirt-number
    # updates intentionally run last.
    transfers.sort(key=_transfer_sort_key)
    source_counts = Counter(
        source
        for transfer in transfers
        for source in transfer.sources
    )
    source_summary = ", ".join(
        f"{source}={count}" for source, count in sorted(source_counts.items())
    )
    print(f"  Reconciled sources: {source_summary or 'none'}")
    print(f"\nTotal unique transfers to process: {len(transfers)}")
    for transfer in transfers[:5]:
        print(f"  {transfer}")
    if len(transfers) > 5:
        print(f"  ... and {len(transfers) - 5} more")
    return transfers
def _game_database_archives(
    game_root: Path | str | None = None,
) -> tuple[Path, ...]:
    """Return the one selected game database archive."""
    selected_root = (
        game_root if game_root is not None else getattr(config, "GAME_ROOT", None)
    )
    primary = discover_game_cpk(selected_root)
    return (primary,) if primary is not None else ()


_PLAYER_BIN_CPK_MEMBER = "common/etc/pesdb/Player.bin"
_TEAM_BIN_CPK_MEMBER = "common/etc/pesdb/Team.bin"
_PLAYER_ASSIGNMENT_CPK_MEMBER = "common/etc/pesdb/PlayerAssignment.bin"


def _load_binary_database(
    configured_path: Path | str | None,
    database_type,
    cpk_member: str,
    label: str,
    *,
    game_root: Path | str | None = None,
):
    """Load one binary database from an extracted file or game CPK."""
    if configured_path is not None:
        candidate = Path(configured_path)
        if candidate.is_file():
            try:
                return database_type.load(candidate), str(candidate)
            except (OSError, ValueError) as exc:
                logger.warning(
                    "Ignoring invalid %s metadata %s: %s", label, candidate, exc
                )

    for cpk_path in _game_database_archives(game_root):
        try:
            payload = read_cpk_file(cpk_path, cpk_member)
        except FileNotFoundError:
            continue
        except (OSError, ValueError) as exc:
            logger.warning(
                "Ignoring unreadable %s metadata in %s: %s", label, cpk_path, exc
            )
            continue
        try:
            return database_type.from_bytes(payload), f"{cpk_path}::{label}"
        except (OSError, ValueError) as exc:
            logger.warning(
                "Ignoring invalid %s metadata in %s: %s", label, cpk_path, exc
            )
    return None, None


def _load_playerbin_database(
    player_bin_path: Path | str | None = None,
    *,
    game_root: Path | str | None = None,
) -> tuple[PlayerBinDatabase | None, str | None]:
    """Load Player.bin from an explicit path or selected game database CPK."""
    configured_path = (
        getattr(config, "PLAYER_BIN_FILE", None)
        if player_bin_path is None
        else player_bin_path
    )
    return _load_binary_database(
        configured_path,
        PlayerBinDatabase,
        _PLAYER_BIN_CPK_MEMBER,
        "Player.bin",
        game_root=game_root,
    )


def _load_teambin_database(
    team_bin_path: Path | str | None = None,
    *,
    game_root: Path | str | None = None,
) -> tuple[TeamBinDatabase | None, str | None]:
    """Load Team.bin from an explicit path or selected game database CPK."""
    configured_path = (
        getattr(config, "TEAM_BIN_FILE", None)
        if team_bin_path is None
        else team_bin_path
    )
    return _load_binary_database(
        configured_path,
        TeamBinDatabase,
        _TEAM_BIN_CPK_MEMBER,
        "Team.bin",
        game_root=game_root,
    )


def _load_player_assignment_database(
    assignment_path: Path | str | None = None,
    *,
    game_root: Path | str | None = None,
) -> tuple[PlayerAssignmentDatabase | None, str | None]:
    """Load PlayerAssignment.bin from an explicit path or selected CPK."""
    configured_path = (
        getattr(config, "PLAYER_ASSIGNMENT_FILE", None)
        if assignment_path is None
        else assignment_path
    )
    return _load_binary_database(
        configured_path,
        PlayerAssignmentDatabase,
        _PLAYER_ASSIGNMENT_CPK_MEMBER,
        "PlayerAssignment.bin",
        game_root=game_root,
    )

_PLAYER_APPEARANCE_CPK_MEMBER = (
    "common/character0/model/character/appearance/PlayerAppearance.bin"
)


def _load_player_appearance_data(
    appearance_path: Path | None = None,
    game_root: Path | None = None,
) -> tuple[bytes | None, str | None]:
    """Load raw PlayerAppearance.bin from an explicit file or game CPK."""
    configured_path = (
        Path(appearance_path)
        if appearance_path is not None
        else getattr(config, "PLAYER_APPEARANCE_FILE", None)
    )
    if configured_path is not None:
        candidate = Path(configured_path)
        if candidate.is_file():
            try:
                return candidate.read_bytes(), str(candidate)
            except OSError as exc:
                logger.warning(
                    "Ignoring unreadable PlayerAppearance.bin %s: %s",
                    candidate,
                    exc,
                )

    cpk_path = discover_game_cpk(
        game_root
        if game_root is not None
        else getattr(config, "GAME_ROOT", None)
    )
    if cpk_path is None:
        return None, None
    try:
        payload = read_cpk_file(cpk_path, _PLAYER_APPEARANCE_CPK_MEMBER)
        return payload, f"{cpk_path}::PlayerAppearance.bin"
    except (OSError, ValueError) as exc:
        logger.warning(
            "Ignoring invalid game PlayerAppearance.bin metadata %s: %s",
            cpk_path,
            exc,
        )
        return None, None


def _load_match_database(
    edit_file: EditFile,
    release_policy_file: str | Path | None = None,
):
    """Build roster-aware player and club indexes from one validated save."""
    print("\n📋 Reading selected save database...")
    playerbin_database, playerbin_source = _load_playerbin_database()
    edit_file.playerbin_source = playerbin_source
    if playerbin_database is not None:
        edit_file.attach_playerbin(playerbin_database)
        print(f"  Loaded Player.bin metadata from {playerbin_source}")
    teambin_database, teambin_source = _load_teambin_database()
    edit_file.teambin_source = teambin_source
    if teambin_database is not None:
        edit_file.attach_teambin(teambin_database)
        print(f"  Loaded Team.bin metadata from {teambin_source}")
    assignment_database, assignment_source = _load_player_assignment_database()
    edit_file.player_assignment_source = assignment_source
    if assignment_database is not None:
        edit_file.attach_player_assignment(assignment_database)
        print(
            "  Loaded PlayerAssignment.bin metadata "
            f"from {assignment_source}"
        )
    players = edit_file.get_all_players()
    edit_file._player_cache = players
    try:
        release_policy = load_release_policy(release_policy_file)
    except ReleasePolicyError as exc:
        raise PlayerCatalogError(str(exc)) from exc
    attach_policy = getattr(edit_file, "attach_release_policy", None)
    if callable(attach_policy):
        attach_policy(release_policy)
    if release_policy.protected_players or release_policy.usage:
        print(
            "  Release policy: "
            f"{len(release_policy.protected_players)} protected clubs, "
            f"{len(release_policy.usage)} usage snapshots"
        )
    teams_info = edit_file.get_all_team_info()
    club_ids = edit_file.get_club_team_ids()

    catalog_report = getattr(edit_file, "player_catalog_report", None)
    current_catalog_entries = (
        getattr(catalog_report, "current_entries", None)
        if catalog_report is not None
        else None
    )
    ovr_count = getattr(catalog_report, "overall_ratings", None)
    roster_count = getattr(catalog_report, "roster_entries", None)
    if ovr_count is not None and roster_count is not None:
        print(
            "  OVR metadata: "
            f"{ovr_count}/{roster_count} roster players have verified save OVR"
        )
    # The team reference is tied to the current player reference. If that
    # SPFL catalog is unavailable, ignore any bundled team file as well: it
    # may describe a different base than a ULM/vanilla save.
    use_external_team_names = (
        current_catalog_entries is None or current_catalog_entries > 0
    )
    current_team_names = (
        load_id_name_text(
            config.CURRENT_TEAMS_FILE,
            label="team",
            minimum_entries=700,
        )
        if use_external_team_names
        else {}
    )

    team_name_to_id = {
        current_team_names.get(team_id, team.name): team_id
        for team_id, team in teams_info.items()
        if team_id in club_ids
    }
    matcher = NameMatcher()
    matcher.load_player_db(
        [(player.name, player_id) for player_id, player in players.items()],
        positions={
            player_id: player.position
            for player_id, player in players.items()
            if player.position
        },
        nationalities={
            player_id: player.nationality
            for player_id, player in players.items()
            if player.nationality
        },
        ages={
            player_id: player.age
            for player_id, player in players.items()
            if player.age
        },
    )
    # The save's league memberships already filter national teams. Numeric
    # club-ID heuristics are invalid for FL26 (some real clubs have low IDs).
    matcher.load_team_db(team_name_to_id, clubs_only=False)

    all_rosters = edit_file.get_all_rosters()
    team_player_map = {
        team_id: roster.roster for team_id, roster in all_rosters.items()
    }
    if current_catalog_entries == 0:
        print("  ⚠ External player catalog unavailable; using names from selected save")
    print(
        f"  {len(players)} players, {len(team_name_to_id)} playable clubs "
        "(national teams excluded)"
    )
    return matcher, all_rosters, team_player_map, club_ids


def _match_and_plan_transfers(
    transfers,
    matcher,
    threshold,
    team_player_map,
    all_rosters,
    club_ids,
    edit_file,
    output_path,
    *,
    allow_overflow_release,
):
    """Match scraped identities, classify them, and create safe roster actions."""

    print(
        "\n🔍 Matching transfers with roster-aware identity verification "
        f"(threshold={threshold}%)..."
    )
    save_scope = str(output_path.resolve())
    historical_entries = transfer_logger.read_log(
        save_scope=save_scope,
        include_legacy=(output_path.resolve() == config.OUTPUT_FILE_PATH.resolve()),
    )
    matched = _match_transfers_statefully(
        transfers,
        matcher,
        threshold,
        team_player_map,
        club_ids,
        historical_entries=historical_entries,
        validated_fotmob_ids=_load_represented_fotmob_club_ids(),
    )
    matched, duplicate_shirt_matches = _dedupe_shirt_number_matches(matched)
    superseded_loan_sources = _build_superseded_loan_sources(
        matched,
        historical_entries=historical_entries,
    )
    if duplicate_shirt_matches:
        print(
            f"  ⚠ Skipped {duplicate_shirt_matches} duplicate or ambiguous "
            "shirt-number matches"
        )

    non_shirt = [
        match
        for match in matched
        if match.transfer.transfer_type != "shirt_number_update"
    ]
    fully_matched = [match for match in matched if match.is_fully_matched]
    partial = [match for match in matched if not match.is_fully_matched]
    roster_plan = _plan_roster_actions(
        matched,
        all_rosters,
        club_ids,
        edit_file,
        superseded_loan_sources,
        allow_overflow_release=allow_overflow_release,
    )
    print(
        f"  ✓ Fully actionable: {len(fully_matched)} "
        f"(Club Transfers: {sum(match.is_club_transfer for match in non_shirt)}, "
        f"Departures: {sum(match.is_release for match in non_shirt)}, "
        f"Signings: {sum(match.is_sign for match in non_shirt)}, "
        "Shirt Number Checks: "
        f"{sum(match.transfer.transfer_type == 'shirt_number_update' and match.is_fully_matched for match in matched)})"
    )
    print(f"  ✗ Unmatched: {len(partial)}")
    if partial:
        print("\n  Unmatched transfers (preview):")
        for match in partial[:10]:
            print(f"    {match}")
    return roster_plan, fully_matched, save_scope






def _print_dry_run(
    edit_file: EditFile,
    roster_plan,
) -> None:
    """Render planned roster actions without mutating."""
    print("\n🔍 DRY-RUN — checking each match against the current roster:")
    would_apply = 0
    already_current = 0
    safety_skipped = 0
    for planned_action in roster_plan:
        match = planned_action.match
        action = planned_action.action
        if action == "skip":
            safety_skipped += 1
            print(
                f"  SAFETY SKIP ({planned_action.reason or 'state_mismatch'}, "
                f"current={planned_action.current_team_id}, source={match.from_team_id}, "
                f"destination={match.to_team_id}): {match}"
            )
            continue
        if action == "noop" or (
            action == "shirt_update" and match.transfer.shirt_number is None
        ):
            already_current += 1
            print(f"  ALREADY CURRENT: {match}")
            continue
        if action == "shirt_update":
            current_shirt = edit_file.get_player_shirt_number(
                match.to_team_id, match.player_id
            )
            if current_shirt == match.transfer.shirt_number:
                already_current += 1
                continue
            conflicting_player = _find_shirt_number_conflict(
                edit_file,
                match.to_team_id,
                match.player_id,
                match.transfer.shirt_number,
            )
            if conflicting_player is not None:
                safety_skipped += 1
                print(
                    f"  SAFETY SKIP (shirt_number_conflict:{conflicting_player}): "
                    f"{match}"
                )
                continue

        would_apply += 1
        if planned_action.overflow_player_id is not None:
            details = planned_action.overflow_details or {}
            name = details.get("name") or "unknown player"
            role_group = details.get("role_group", "unknown")
            role = details.get("role", "?")
            usage = details.get("usage")
            if isinstance(usage, dict):
                usage_text = (
                    f"minutes={usage.get('minutes', '?')}, "
                    f"starts={usage.get('starts', '?')}, "
                    f"apps={usage.get('appearances', '?')}, "
                    f"news={usage.get('news_mentions', '?')}"
                )
            else:
                usage_text = "usage=unavailable"
            print(
                f"  WOULD AUTO-RELEASE: {name} "
                f"(id={planned_action.overflow_player_id}, "
                f"role={role_group}:{role}, {usage_text}) "
                f"from team {match.to_team_id}"
            )
        print(f"  WOULD {action.upper()}: {match}")
    print(
        f"\nDry-run complete. Would apply: {would_apply}, "
        f"already current: {already_current}, safety-skipped: {safety_skipped}. "
        "No files were written."
    )


def _find_shirt_number_conflict(
    edit_file: EditFile,
    team_id: int | None,
    player_id: int | None,
    shirt_number: int | None,
) -> int | None:
    """Return the other player already using a requested shirt number."""
    if team_id is None or player_id is None or shirt_number is None:
        return None
    roster = edit_file.get_team_roster(team_id)
    if roster is None:
        return None
    for other_player_id, other_shirt_number in zip(
        roster.player_ids, roster.shirt_numbers
    ):
        if (
            other_player_id not in (0, player_id)
            and other_shirt_number == shirt_number
        ):
            return other_player_id
    return None

def _native_transfer_metadata(edit_file: EditFile, player_id: int) -> dict[str, object]:
    """Capture read-only native metadata for one transfer report row."""
    metadata: dict[str, object] = {}
    playerbin_db = getattr(edit_file, "playerbin_db", None)
    playerbin_source = getattr(edit_file, "playerbin_source", None)
    if playerbin_db is not None:
        record = playerbin_db.get(player_id)
        player_payload: dict[str, object] = {
            "source": playerbin_source,
            "found": record is not None,
        }
        if record is not None:
            player_payload.update(
                {
                    "player_id": record.player_id,
                    "name": record.name,
                    "print_name": record.print_name,
                    "age": record.age,
                    "registered_position": record.registered_position,
                    "market_value_eur": record.market_value_eur,
                    "contract_until": record.contract_until,
                    "loan_until": record.loan_until,
                    "is_on_loan": record.is_on_loan,
                    "owner_team_key": record.owner_team_key,
                    "youth_team_id": record.youth_team_id,
                    "caps": record.caps,
                }
            )
        metadata["player_bin"] = player_payload

    assignment_db = getattr(edit_file, "player_assignment_db", None)
    if assignment_db is not None:
        team_keys = assignment_db.team_keys_for(player_id)
        assignment_payload: dict[str, object] = {
            "source": getattr(edit_file, "player_assignment_source", None),
            "team_keys": list(team_keys),
        }
        teambin_db = getattr(edit_file, "teambin_db", None)
        if teambin_db is not None:
            assignment_payload["teams"] = [
                {
                    "team_key": team.team_key,
                    "name": team.name,
                    "abbreviation": team.abbreviation,
                }
                for team_key in team_keys
                if (team := teambin_db.get(team_key)) is not None
            ]
        metadata["player_assignment"] = assignment_payload
    return metadata



class _RunPrepared:
    def __init__(
        self,
        *,
        temp_dir: Path,
        data_dat: Path,
        edit_file: EditFile,
        edit_path: Path,
        output_path: Path,
        input_digest: str,
        same_input_output: bool,
        output_existed: bool,
        output_digest: str | None,
    ) -> None:
        self.temp_dir = temp_dir
        self.data_dat = data_dat
        self.edit_file = edit_file
        self.edit_path = edit_path
        self.output_path = output_path
        self.input_digest = input_digest
        self.same_input_output = same_input_output
        self.output_existed = output_existed
        self.output_digest = output_digest
        self.output_lock: EditFileLock | None = None
        self.roster_plan = ()
        self.save_scope = str(output_path.resolve())
        self.backup_path: Path | None = None
        self.original_data = bytes(
            getattr(edit_file, "_data", data_dat.read_bytes())
        )
        # Native Player.bin metadata can expose semantic issues already present
        # in the selected save.  Keep those diagnostics as a baseline so a
        # transfer is rejected only when it introduces a new integrity error.
        self.pre_mutation_integrity_errors: tuple[str, ...] = ()
        self.pending_logs = []
        self.run_records = []


class _RunMutation:
    def __init__(
        self,
        *,
        transfer_applied: int,
        shirt_numbers_changed: int,
        unchanged: int,
        safety_skipped: int,
    ) -> None:
        self.transfer_applied = transfer_applied
        self.shirt_numbers_changed = shirt_numbers_changed
        self.unchanged = unchanged
        self.safety_skipped = safety_skipped


class _RunLocalUpdateRuntime:
    """Adapter from the shared service lifecycle to the verified edit-file pipeline."""

    @staticmethod
    def _args(request: LocalUpdateRequest) -> argparse.Namespace:
        return argparse.Namespace(
            popular=request.popular,
            window=request.window,
            since=request.since,
            club=request.club,
            deep=request.deep,
            fotmob_only=request.fotmob_only,
            allow_overflow_release=request.allow_overflow_release,
        )

    @staticmethod
    def _release_lock(prepared: _RunPrepared) -> None:
        if prepared.output_lock is not None:
            prepared.output_lock.release()
            prepared.output_lock = None

    def scrape(
        self,
        request: LocalUpdateRequest,
        _token: CancellationToken,
    ):
        if not request.dry_run and not request.edit_path.exists():
            raise LocalUpdateError(
                "missing_input",
                f"Edit file not found: {request.edit_path}",
                stage=LocalUpdateStage.SCRAPING,
            )
        return _scrape_run_transfers(self._args(request))

    def validate_and_prepare(
        self,
        request: LocalUpdateRequest,
        _transfers,
        _token: CancellationToken,
    ) -> _RunPrepared:
        output_path = request.target_path
        prepared: _RunPrepared | None = None
        lock = EditFileLock(output_path)
        try:
            lock.acquire()
        except Exception as error:
            raise LocalUpdateError(
                "target_locked",
                str(error),
                stage=LocalUpdateStage.VALIDATING,
            ) from error

        try:
            input_digest = _sha256_file(request.edit_path)
            same_input_output = output_path.resolve() == request.edit_path.resolve()
            output_existed = output_path.exists()
            output_digest = (
                input_digest
                if same_input_output
                else _sha256_file(output_path) if output_existed else None
            )

            print(f"\n🔓 Decrypting {request.edit_path}...")
            try:
                temp_dir = crypto.decrypt(request.edit_path)
            except Exception as error:
                raise LocalUpdateError(
                    "decrypt_failed",
                    f"Decryption failed: {error}",
                    stage=LocalUpdateStage.VALIDATING,
                ) from error

            data_dat = temp_dir / "data.dat"
            if not data_dat.exists():
                dat_files = list(temp_dir.glob("*.dat"))
                if not dat_files:
                    raise LocalUpdateError(
                        "invalid_save",
                        f"Decryption produced no data block in {temp_dir}",
                        stage=LocalUpdateStage.VALIDATING,
                    )
                data_dat = max(dat_files, key=lambda path: path.stat().st_size)

            edit_file = EditFile()
            edit_file.load(data_dat)
            integrity = edit_file.validate_integrity()
            if not integrity["valid"]:
                details = [
                    "Input save failed supported edit-file integrity validation; no changes were written."
                ]
                details.extend(f"  - {error}" for error in integrity["errors"][:20])
                remaining = len(integrity["errors"]) - 20
                if remaining > 0:
                    details.append(f"  ... and {remaining} more errors")
                details.append(
                    "Use a standard EDIT00000000 save with a supported layout."
                )
                raise LocalUpdateError(
                    "invalid_save",
                    "\n".join(details),
                    stage=LocalUpdateStage.VALIDATING,
                )

            prepared = _RunPrepared(
                temp_dir=temp_dir,
                data_dat=data_dat,
                edit_file=edit_file,
                edit_path=request.edit_path,
                output_path=output_path,
                input_digest=input_digest,
                same_input_output=same_input_output,
                output_existed=output_existed,
                output_digest=output_digest,
            )
            prepared.output_lock = lock
            return prepared
        except LocalUpdateError:
            if prepared is not None:
                crypto.cleanup_temp(prepared.temp_dir)
            else:
                temp_dir = locals().get("temp_dir")
                if temp_dir is not None:
                    crypto.cleanup_temp(temp_dir)
            lock.release()
            raise
        except Exception as error:
            temp_dir = locals().get("temp_dir")
            if temp_dir is not None:
                crypto.cleanup_temp(temp_dir)
            lock.release()
            raise LocalUpdateError(
                "invalid_save",
                f"Could not load the selected save: {error}",
                stage=LocalUpdateStage.VALIDATING,
            ) from error

    def match_and_plan(
        self,
        request: LocalUpdateRequest,
        prepared: _RunPrepared,
        transfers,
        _token: CancellationToken,
    ):
        try:
            if request.release_policy_file is None:
                matcher, all_rosters, team_player_map, club_ids = (
                    _load_match_database(prepared.edit_file)
                )
            else:
                matcher, all_rosters, team_player_map, club_ids = (
                    _load_match_database(
                        prepared.edit_file,
                        request.release_policy_file,
                    )
                )
            baseline_integrity = prepared.edit_file.validate_integrity()
            prepared.pre_mutation_integrity_errors = tuple(
                str(error) for error in baseline_integrity.get("errors", [])
            )
            roster_plan, fully_matched, save_scope = _match_and_plan_transfers(
                transfers,
                matcher,
                request.threshold or config.MATCH_THRESHOLD_PLAYER,
                team_player_map,
                all_rosters,
                club_ids,
                prepared.edit_file,
                prepared.output_path,
                allow_overflow_release=request.allow_overflow_release,
            )
            prepared.roster_plan = roster_plan
            prepared.save_scope = save_scope
            return roster_plan, fully_matched
        except LocalUpdateError:
            raise
        except Exception as error:
            raise LocalUpdateError(
                "matching_failed",
                f"Transfer matching failed: {error}",
                stage=LocalUpdateStage.MATCHING,
            ) from error

    def apply(
        self,
        request: LocalUpdateRequest,
        prepared: _RunPrepared,
        _plan,
        token: CancellationToken,
    ):
        actionable_roster = any(
            item.action in {"move", "add", "release", "shirt_update"}
            for item in prepared.roster_plan
        )
        if not actionable_roster:
            unchanged = sum(
                item.action == "noop" for item in prepared.roster_plan
            )
            safety_skipped = sum(
                item.action == "skip" for item in prepared.roster_plan
            )
            print("\nNo effective transfer or shirt-number changes to apply. Exiting.")
            return LocalUpdateResult(
                target_path=prepared.output_path,
                backup_path=None,
                installed_sha256=None,
                transfer_applied=0,
                shirt_numbers_changed=0,
                unchanged=unchanged,
                safety_skipped=safety_skipped,
                no_changes=True,
            )

        token.raise_if_cancelled()
        print("\n💾 Creating backup...")
        try:
            prepared.backup_path = backup_mod.create_backup(prepared.edit_path)
        except Exception as error:
            raise LocalUpdateError(
                "backup_failed",
                f"Backup failed: {error}",
                stage=LocalUpdateStage.APPLYING,
            ) from error
        print(f"  Backup: {prepared.backup_path}")

        print("\n⚡ Applying verified transfers and shirt-number changes...")
        transfer_applied = 0
        shirt_numbers_applied = 0
        unchanged = 0
        safety_skipped = 0
        original_data = prepared.original_data

        for planned_action in prepared.roster_plan:
            token.raise_if_cancelled()
            match = planned_action.match
            to_team_id = match.to_team_id
            transfer = match.transfer
            action = planned_action.action
            if action == "skip":
                safety_skipped += 1
                print(
                    f"  ⚠ Safety skip {match.matched_player_name or transfer.player_name}: "
                    f"{planned_action.reason or 'state mismatch'}"
                )
                continue

            player_id = match.player_id
            if player_id is None:
                continue

            current_team_id = planned_action.current_team_id
            if action == "noop":
                unchanged += 1
                continue
            native_metadata = _native_transfer_metadata(
                prepared.edit_file,
                player_id,
            )

            ok = False
            preferred_shirt = transfer.shirt_number
            previous_shirt = None
            if action == "shirt_update":
                if preferred_shirt is None:
                    unchanged += 1
                    continue
                previous_shirt = prepared.edit_file.get_player_shirt_number(
                    to_team_id,
                    player_id,
                )
                if previous_shirt == preferred_shirt:
                    unchanged += 1
                    continue
                conflicting_player = _find_shirt_number_conflict(
                    prepared.edit_file,
                    to_team_id,
                    player_id,
                    preferred_shirt,
                )
                if conflicting_player is not None:
                    safety_skipped += 1
                    print(
                        f"  ⚠ Safety skip {match.matched_player_name or transfer.player_name}: "
                        f"shirt #{preferred_shirt} is already assigned to player "
                        f"{conflicting_player} on team {to_team_id}"
                    )
                    continue
                ok = prepared.edit_file.update_player_shirt_number(
                    to_team_id,
                    player_id,
                    preferred_shirt,
                )
            elif action == "move":
                ok = prepared.edit_file.move_player(
                    player_id,
                    current_team_id,
                    to_team_id,
                    shirt_number=preferred_shirt,
                    position=transfer.position,
                    allow_overflow_release=request.allow_overflow_release,
                )
            elif action == "add":
                ok = prepared.edit_file.add_player(
                    player_id,
                    to_team_id,
                    shirt_number=preferred_shirt,
                    position=transfer.position,
                    allow_overflow_release=request.allow_overflow_release,
                )
            elif action == "release":
                ok = prepared.edit_file.release_player(
                    player_id,
                    match.from_team_id,
                )

            if not ok:
                prepared.edit_file._data = bytearray(original_data)
                raise LocalUpdateError(
                    "apply_failed",
                    f"Failed: {match.matched_player_name or transfer.player_name} "
                    f"({match.action_type}); entire batch rolled back",
                    stage=LocalUpdateStage.APPLYING,
                )

            if action == "shirt_update":
                shirt_numbers_applied += 1
            else:
                transfer_applied += 1
            prepared.pending_logs.append((match, previous_shirt, action))
            prepared.run_records.append(
                {
                    "player_name": match.matched_player_name or transfer.player_name,
                    "from_team": match.matched_from_team or transfer.from_club,
                    "to_team": match.matched_to_team or transfer.to_club,
                    "position": transfer.position,
                    "fee": transfer.fee,
                    "transfer_type": transfer.transfer_type,
                    "confidence": match.min_confidence,
                    "dry_run": False,
                    "previous_shirt_number": previous_shirt,
                    "shirt_number": (
                        preferred_shirt if action == "shirt_update" else None
                    ),
                    "roster_action": action,
                    "sources": list(transfer.sources),
                    "source_urls": list(transfer.source_urls),
                    "proof_urls": list(transfer.proof_urls),
                    "native_metadata": native_metadata,
                }
            )


        print(
            f"\n  Transfers applied: {transfer_applied}, "
            f"shirt numbers changed: {shirt_numbers_applied}, "
            f"already current: {unchanged}, safety-skipped: {safety_skipped}"
        )
        return _RunMutation(
            transfer_applied=transfer_applied,
            shirt_numbers_changed=shirt_numbers_applied,
            unchanged=unchanged,
            safety_skipped=safety_skipped,
        )

    def verify(
        self,
        _request: LocalUpdateRequest,
        prepared: _RunPrepared,
        _mutation: _RunMutation,
        _token: CancellationToken,
    ) -> None:
        post_integrity = prepared.edit_file.validate_integrity()
        post_errors = tuple(
            str(error) for error in post_integrity.get("errors", [])
        )
        baseline_errors = set(prepared.pre_mutation_integrity_errors)
        new_errors = tuple(
            error for error in post_errors if error not in baseline_errors
        )
        if new_errors:
            prepared.edit_file._data = bytearray(prepared.original_data)
            details = [
                "Modified save failed integrity validation; changes were rolled back."
            ]
            details.extend(f"  - {error}" for error in new_errors[:20])
            remaining = len(new_errors) - 20
            if remaining > 0:
                details.append(f"  ... and {remaining} more errors")
            preserved = len(post_errors) - len(new_errors)
            if preserved > 0:
                details.append(
                    f"  Preserved {preserved} pre-existing integrity diagnostics."
                )
            raise LocalUpdateError(
                "post_validation_failed",
                "\n".join(details),
                stage=LocalUpdateStage.VERIFYING,
            )
        if post_errors:
            print(
                f"\n  Preserved {len(post_errors)} pre-existing "
                "integrity diagnostics."
            )

        if _sha256_file(prepared.edit_path) != prepared.input_digest:
            prepared.edit_file._data = bytearray(prepared.original_data)
            raise LocalUpdateError(
                "input_changed",
                "Input EDIT file changed while this run was processing; "
                "stale output was not written.",
                stage=LocalUpdateStage.VERIFYING,
            )

        if not prepared.same_input_output:
            output_changed = prepared.output_path.exists() != prepared.output_existed
            if prepared.output_existed and prepared.output_path.exists():
                output_changed = (
                    _sha256_file(prepared.output_path) != prepared.output_digest
                )
            if output_changed:
                prepared.edit_file._data = bytearray(prepared.original_data)
                raise LocalUpdateError(
                    "output_changed",
                    "Output EDIT file changed while this run was processing; "
                    "concurrent output was preserved.",
                    stage=LocalUpdateStage.VERIFYING,
                )

    def publish(
        self,
        _request: LocalUpdateRequest,
        prepared: _RunPrepared,
        mutation: _RunMutation,
        _token: CancellationToken,
    ) -> LocalUpdateResult:
        try:
            prepared.edit_file.save(prepared.data_dat)
            prepared.output_path.parent.mkdir(parents=True, exist_ok=True)
            print(f"\n🔒 Re-encrypting → {prepared.output_path}...")
            crypto.encrypt(prepared.temp_dir, prepared.output_path)
        except Exception as error:
            prepared.edit_file._data = bytearray(prepared.original_data)
            raise LocalUpdateError(
                "publish_failed",
                f"Could not publish verified save: {error}",
                stage=LocalUpdateStage.ENCRYPTING,
            ) from error

        diagnostic: str | None = None
        try:
            for (match, previous_shirt, action), run_record in zip(
                prepared.pending_logs,
                prepared.run_records,
                strict=True,
            ):
                transfer = match.transfer
                transfer_logger.log_transfer(
                    player_name=match.matched_player_name or transfer.player_name,
                    player_id=match.player_id,
                    from_team=match.matched_from_team or transfer.from_club,
                    from_team_id=match.from_team_id or 0,
                    to_team=match.matched_to_team or transfer.to_club,
                    to_team_id=match.to_team_id or 0,
                    confidence=match.min_confidence,
                    transfer_type=transfer.transfer_type,
                    dry_run=False,
                    position=transfer.position,
                    fee=transfer.fee,
                    market_value=transfer.market_value,
                    transfer_date=transfer.date,
                    previous_shirt_number=previous_shirt,
                    shirt_number=(
                        transfer.shirt_number
                        if transfer.transfer_type == "shirt_number_update"
                        else None
                    ),
                    roster_action=action,
                    save_scope=prepared.save_scope,
                    fotmob_player_id=transfer.player_id_fotmob,
                    sortitoutsi_player_id=transfer.player_id_sortitoutsi,
                    transfermarkt_player_id=transfer.player_id_transfermarkt,
                    transfermarkt_from_club_id=transfer.from_club_id_transfermarkt,
                    transfermarkt_to_club_id=transfer.to_club_id_transfermarkt,
                    transfermarkt_transfer_id=transfer.transfer_id_transfermarkt,
                    sources=transfer.sources,
                    source_urls=transfer.source_urls,
                    proof_urls=transfer.proof_urls,
                    native_metadata=run_record.get("native_metadata"),
                )
            transfer_logger.save_reports(prepared.run_records)
        except Exception as error:
            diagnostic = (
                "Save published, but transfer logging/report generation failed: "
                f"{error}"
            )
            print(f"\n⚠ {diagnostic}")
        installed_sha256 = (
            _sha256_file(prepared.output_path)
            if prepared.output_path.exists()
            else None
        )
        return LocalUpdateResult(
            target_path=prepared.output_path,
            backup_path=prepared.backup_path,
            installed_sha256=installed_sha256,
            transfer_applied=mutation.transfer_applied,
            shirt_numbers_changed=mutation.shirt_numbers_changed,
            unchanged=mutation.unchanged,
            safety_skipped=mutation.safety_skipped,
            diagnostic=diagnostic,
        )

    def preview(
        self,
        _request: LocalUpdateRequest,
        prepared: _RunPrepared,
        plan,
        _token: CancellationToken,
    ) -> LocalUpdateResult:
        _print_dry_run(prepared.edit_file, plan[0] if isinstance(plan, tuple) else plan)
        return LocalUpdateResult(
            target_path=prepared.output_path,
            backup_path=None,
            installed_sha256=None,
            transfer_applied=0,
            shirt_numbers_changed=0,
            unchanged=0,
            safety_skipped=0,
            no_changes=True,
        )

    @staticmethod
    def cleanup(prepared: _RunPrepared) -> None:
        crypto.cleanup_temp(prepared.temp_dir)
        if prepared.output_lock is not None:
            prepared.output_lock.release()
            prepared.output_lock = None


def build_local_update_service() -> LocalUpdateService:
    """Return the shared local update service used by CLI and installer GUI."""

    return LocalUpdateService(_RunLocalUpdateRuntime())


def cmd_run(args):
    """CLI adapter for the shared verified-transfer service."""
    dry_run = bool(getattr(args, "dry_run", False))
    try:
        edit_path, output_path = _resolve_run_paths(args)
    except DestinationError as error:
        print(f"Unsafe save path: {error}")
        raise SystemExit(2) from error
    game_root = getattr(args, "game_root", None)
    if game_root is not None:
        config.GAME_ROOT = Path(game_root)

    if not dry_run and not edit_path.exists():
        print(f"Edit file not found: {edit_path}")
        print("Use --edit-file to specify the path, or set EDIT_FILE_PATH in config.py")
        sys.exit(1)

    if dry_run and not edit_path.exists():
        transfers = _scrape_run_transfers(args)
        if not transfers:
            print("No verified transfers found. Nothing to apply.")
            return
        print("\n⚠ Dry-run mode without edit file — showing scraped data only.")
        print(f"\nAll {len(transfers)} transfers:")
        for transfer in transfers:
            print(f"  {transfer}")
        return

    request = LocalUpdateRequest(
        edit_path=edit_path,
        output_path=output_path,
        deep=bool(getattr(args, "deep", False)),
        window=getattr(args, "window", "auto") or "auto",
        since=getattr(args, "since", None),
        club=getattr(args, "club", None),
        threshold=getattr(args, "threshold", None) or config.MATCH_THRESHOLD_PLAYER,
        popular=bool(getattr(args, "popular", False)),
        fotmob_only=bool(getattr(args, "fotmob_only", False)),
        allow_overflow_release=bool(
            getattr(args, "allow_overflow_release", True)
        ),
        release_policy_file=getattr(args, "release_policy_file", None),
        dry_run=dry_run,
    )

    try:
        result = build_local_update_service().execute(request)
    except LocalUpdateError as error:
        print(f"\n❌ {error}")
        sys.exit(1 if error.code in {"missing_input", "decrypt_failed"} else 2)

    if dry_run:
        return
    if result.no_changes:
        if (
            result.transfer_applied == 0
            and result.shirt_numbers_changed == 0
            and result.unchanged == 0
            and result.safety_skipped == 0
        ):
            print("No verified transfers found. Nothing to apply.")
        return

    print(
        f"\n✅ Done! {result.transfer_applied} transfers applied; "
        f"{result.shirt_numbers_changed} shirt numbers changed."
    )
    if result.diagnostic:
        print(f"   Warning: {result.diagnostic}")
    if result.target_path.resolve() != edit_path.resolve():
        print(f"   Input (base/pristine):   {edit_path}")
        print(f"   Output (updated save):   {result.target_path}")
    else:
        print(f"   Updated file:            {result.target_path}")
    print(f"   Backup at:               {result.backup_path}")
    print(f"   Log at:                  {config.TRANSFER_LOG_FILE}")
    print(f"   Visual Summary Report:   {config.OUTPUT_DIR / 'transfer_summary.md'}")




def _decrypted_data_file(decrypted_path: Path) -> Path:
    """Resolve data.dat from either a decrypted directory or direct test fixture."""
    decrypted_path = Path(decrypted_path)
    if decrypted_path.is_file():
        return decrypted_path
    data_file = decrypted_path / "data.dat"
    if data_file.exists():
        return data_file
    dat_files = list(decrypted_path.glob("*.dat"))
    if not dat_files:
        raise FileNotFoundError(f"No decrypted .dat file found in {decrypted_path}")
    return max(dat_files, key=lambda path: path.stat().st_size)


def _assess_player_specs(
    edit_file: EditFile,
    specs: tuple[PlayerSpec, ...],
    base_revision: str,
    *,
    allow_overflow_release: bool = True,
    reject_create_mutations: bool = False,
) -> tuple[SpecResult, ...]:
    """Assess every spec against one revision without mutating the edit file."""
    all_players = edit_file.get_all_players()
    results = []
    for spec in sorted(specs, key=lambda item: item.path.name):
        if spec.lifecycle_status != "active":
            results.append(
                SpecResult(
                    spec.identity.pes_id,
                    spec.identity.name,
                    spec.lifecycle_status,
                    spec.lifecycle_reason or f"lifecycle_{spec.lifecycle_status}",
                )
            )
        elif base_revision not in spec.applies_to:
            results.append(
                SpecResult(
                    spec.identity.pes_id,
                    spec.identity.name,
                    "needs_review",
                    "base_revision_not_reviewed",
                )
            )
        elif spec.operation == "create" and reject_create_mutations:
            results.append(
                SpecResult(
                    spec.identity.pes_id,
                    spec.identity.name,
                    "rejected",
                    PLAYER_CREATE_DISABLED_REASON,
                )
            )
        elif spec.operation == "create":
            results.append(
                assess_create(
                    edit_file,
                    spec,
                    all_players,
                    allow_overflow_release=allow_overflow_release,
                )
            )
        else:
            results.append(assess_update(edit_file, spec, all_players))
    return tuple(results)


def _print_player_spec_results(
    specs: tuple[PlayerSpec, ...],
    results: tuple[SpecResult, ...],
) -> None:
    """Print deterministic semantic results and lifecycle/operation totals."""
    for result in results:
        diagnostic = (
            f"; diagnostic: {result.diagnostic}" if result.diagnostic else ""
        )
        print(
            f"  {result.name} (PES ID {result.pes_id}): "
            f"{result.status} ({result.reason}){diagnostic}"
        )
    counts = Counter(spec.lifecycle_status for spec in specs)
    operations = Counter(spec.operation for spec in specs)
    result_counts = Counter(result.status for result in results)
    print(
        "Player Updates: "
        f"active={counts['active']}, needs-review={result_counts['needs_review']}, "
        f"integrated={counts['integrated']}, superseded={counts['superseded']}, "
        f"create={operations['create']}, update={operations['update']}"
    )


def _invalid_player_spec_validation_results(
    specs: tuple[PlayerSpec, ...],
    results: tuple[SpecResult, ...],
    base_revision: str,
) -> tuple[SpecResult, ...]:
    invalid: list[SpecResult] = []
    for spec, result in zip(specs, results, strict=True):
        if spec.lifecycle_status in {"integrated", "superseded"}:
            valid = result.status == spec.lifecycle_status
        elif base_revision not in spec.applies_to:
            valid = result.status == "needs_review"
        else:
            valid = result.status in {"ready", "waiting"}
        if not valid:
            invalid.append(result)
    return tuple(invalid)


def _require_valid_edit(edit_file: EditFile, stage: str) -> None:
    report = edit_file.validate_integrity()
    if report["valid"]:
        return
    print(
        f"{stage} failed supported PES edit-file layout validation; "
        "no audits were written."
    )
    for error in report["errors"][:20]:
        print(f"  - {error}")
    raise SystemExit(2)


def cmd_players_validate(args) -> None:
    """Validate the pristine base and recompute trusted proposals offline."""
    base_path = Path(config.BASE_EDIT_PATH)
    if not base_path.exists():
        print(f"Pristine base not found: {base_path}")
        raise SystemExit(2)
    try:
        manifest = verify_base_file(base_path)
    except PlayerSpecError as exc:
        print(f"Pristine base verification failed: {exc}")
        raise SystemExit(2) from exc

    decrypted = None
    try:
        decrypted = crypto.decrypt(base_path)
        edit_file = EditFile()
        edit_file.load(_decrypted_data_file(decrypted))
        _require_valid_edit(edit_file, "Pristine base")

        try:
            specs = load_player_specs(allow_proposals=True)
            validate_spec_set(specs)
        except PlayerSpecError as exc:
            print(f"Player Update validation failed: {exc}")
            raise SystemExit(2) from exc

        proposal_specs = tuple(
            sorted(
                (
                    spec
                    for spec in specs
                    if getattr(spec, "proposal", None) is not None
                ),
                key=lambda item: item.path.name,
            )
        )
        completed_specs = tuple(
            spec for spec in specs if getattr(spec, "proposal", None) is None
        )
        proposal_failures = False
        for spec in proposal_specs:
            try:
                validate_generated_proposal(spec.path, edit_file)
            except (PlayerDraftError, PlayerSpecError) as exc:
                print(
                    "Player Update validation failed: "
                    f"{spec.path.name}: {exc}"
                )
                proposal_failures = True
        if proposal_failures:
            raise SystemExit(2)

        if proposal_specs:
            proposal_results = _assess_player_specs(
                edit_file, proposal_specs, manifest.revision
            )
            invalid_proposals = _invalid_player_spec_validation_results(
                proposal_specs, proposal_results, manifest.revision
            )
            if invalid_proposals:
                for result in invalid_proposals:
                    print(
                        "Player Update validation failed: "
                        f"{result.name} (PES ID {result.pes_id}): "
                        f"{result.status} ({result.reason})"
                    )
                raise SystemExit(2)
            for spec in proposal_specs:
                print(
                    f"  {spec.identity.name} (PES ID {spec.identity.pes_id}): "
                    "proposal_ready (requires human review)"
                )

        results = _assess_player_specs(
            edit_file, completed_specs, manifest.revision
        )
        if completed_specs:
            _print_player_spec_results(completed_specs, results)
            invalid_results = _invalid_player_spec_validation_results(
                completed_specs,
                results,
                manifest.revision,
            )
            if invalid_results:
                print(
                    "Player Update validation failed: "
                    f"{len(invalid_results)} current active update(s) are invalid."
                )
                raise SystemExit(2)
    finally:
        if decrypted is not None:
            crypto.cleanup_temp(decrypted)


def cmd_players_approve(args) -> None:
    """Approve one generated proposal against the pristine base offline."""
    base_path = Path(config.BASE_EDIT_PATH)
    if not base_path.exists():
        print(f"Pristine base not found: {base_path}")
        raise SystemExit(2)
    try:
        verify_base_file(base_path)
    except PlayerSpecError as exc:
        print(f"Pristine base verification failed: {exc}")
        raise SystemExit(2) from exc

    decrypted = None
    try:
        decrypted = crypto.decrypt(base_path)
        edit_file = EditFile()
        edit_file.load(_decrypted_data_file(decrypted))
        _require_valid_edit(edit_file, "Pristine base")
        approved_path = approve_player_proposal(Path(args.spec), edit_file)
        print(approved_path)
    except (
        OSError,
        PlayerDraftError,
        PlayerSpecError,
        ValueError,
        struct.error,
        CryptoError,
    ) as exc:
        print(f"Player approval failed: {exc}")
        raise SystemExit(2) from exc
    finally:
        if decrypted is not None:
            crypto.cleanup_temp(decrypted)


def _machine_json_string(value: str) -> str:
    """Encode untrusted text as one inert JSON string value."""
    return (
        json.dumps(value, ensure_ascii=True)
        .replace("$", "\\u0024")
        .replace("`", "\\u0060")
    )


def _machine_path_value(value: str) -> str:
    """Keep simple paths readable and JSON-encode every other path."""
    if value and all(
        character.isascii() and (character.isalnum() or character in "._/-")
        for character in value
    ):
        return value
    return _machine_json_string(value)


def cmd_players_generate_draft(args) -> None:
    """Generate one reviewable Pes Retro Stats proposal from a trusted issue event."""
    output_dir = Path(args.output_dir)
    path = write_player_draft(Path(args.event), output_dir)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        player_name = payload["identity"]["name"]
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise PlayerDraftError("generated draft has no valid player name") from exc
    if not isinstance(player_name, str) or not player_name:
        raise PlayerDraftError("generated draft has no valid player name")

    display_path = (output_dir / path.name).as_posix()
    print(f"SPEC_PATH={_machine_path_value(display_path)}")
    print(f"PLAYER_NAME={_machine_json_string(player_name)}")


def _player_spec_audit_record(
    spec: PlayerSpec,
    result: SpecResult,
    edit_file: EditFile,
    save_scope: str,
) -> dict:
    """Build a non-transfer audit record for one applied player spec."""
    field_changes = [
        {"field": field, "from": patch.current, "to": patch.target}
        for field, patch in spec.patches.items()
    ]
    create = spec.create
    shirt_number = None
    if create is not None:
        get_shirt_number = getattr(edit_file, "get_player_shirt_number", None)
        if get_shirt_number is not None:
            shirt_number = get_shirt_number(create.team_id, result.pes_id)
    return {
        "player_name": result.name,
        "player_id": result.pes_id,
        "from_team": "Missing from FL26 database" if create is not None else "",
        "from_team_id": 0,
        "to_team": create.team_name if create is not None else "",
        "to_team_id": create.team_id if create is not None else 0,
        "confidence": 100.0,
        "transfer_type": (
            "player_spec_create" if result.status == "created" else "player_spec_update"
        ),
        "dry_run": False,
        "position": create.registered_position if create is not None else "",
        "transfer_date": spec.evidence.effective_date.isoformat(),
        "shirt_number": shirt_number,
        "roster_action": "create" if result.status == "created" else "update",
        "save_scope": save_scope,
        "pes_retro_stats_player_id": spec.identity.pes_retro_stats_id,
        "sources": ("player_spec",),
        "source_urls": (spec.evidence.profile_url,),
        "proof_urls": spec.evidence.proof_urls,
        "field_changes": field_changes,
    }


def _verify_player_spec_output(output_path: Path) -> None:
    """Decrypt the encrypted result and reject it unless integrity survives."""
    verified_decrypted = crypto.decrypt(output_path)
    try:
        verified = EditFile()
        verified.load(_decrypted_data_file(verified_decrypted))
        _require_valid_edit(verified, "Encrypted output")
    finally:
        crypto.cleanup_temp(verified_decrypted)


def _raise_for_player_spec_mutation_failures(
    results: tuple[SpecResult, ...],
) -> None:
    failures = tuple(
        result
        for result in results
        if result.status == "rejected" and result.reason == "mutation_failed"
    )
    if not failures:
        return
    print(
        "Applying Player Updates failed: "
        f"{len(failures)} unexpected mutation error(s); "
        "independent successful changes were preserved."
    )
    raise SystemExit(2)

def _print_player_apply_preflight(
    edit_file: EditFile,
    specs: tuple[PlayerSpec, ...],
    results: tuple[SpecResult, ...],
    appearance_source: str | None,
) -> None:
    """Print create destinations and safety inputs without mutating a save."""
    print("\nPlayer Apply Preflight:")
    for spec, result in zip(specs, results, strict=True):
        if spec.create is None:
            continue
        create = spec.create
        roster = edit_file.get_team_roster(create.team_id)
        roster_text = (
            "missing"
            if roster is None
            else f"{roster.roster_size}/{len(roster.player_ids)}"
        )
        loan_text = "none"
        if spec.loan is not None:
            loan_text = (
                f"{spec.loan.parent_team_name} "
                f"({spec.loan.start_date.isoformat()}..{spec.loan.end_date.isoformat()})"
            )
        appearance_text = appearance_source or "unavailable"
        print(
            f"  {spec.identity.name}: {result.status} ({result.reason}); "
            f"destination={create.team_name} ({create.team_id}); "
            f"roster={roster_text}; loan_parent={loan_text}; "
            f"appearance={appearance_text}"
        )
    print("  No files were written.")


def cmd_players_apply(args) -> None:
    """Apply reviewed specs in an explicit locked save transaction."""
    manifest = load_base_manifest()
    if args.base_revision != manifest.revision:
        print(
            f"Base revision mismatch: expected {manifest.revision}, "
            f"received {args.base_revision}"
        )
        raise SystemExit(2)

    edit_path = Path(args.edit_file)
    output_path = edit_path if args.in_place else Path(args.output)
    _ensure_safe_edit_paths(edit_path, output_path)
    if not edit_path.exists():
        print(f"Edit file not found: {edit_path}")
        raise SystemExit(2)

    try:
        specs = load_player_specs()
    except PlayerSpecError as exc:
        if "requires human approval" in str(exc):
            print("human_review_required")
            raise SystemExit(2) from exc
        raise
    if any(getattr(spec, "proposal", None) is not None for spec in specs):
        print("human_review_required")
        raise SystemExit(2)
    validate_spec_set(specs)
    output_lock = EditFileLock(output_path)
    output_lock.acquire()
    decrypted = None
    try:
        input_digest = _sha256_file(edit_path)
        same_input_output = output_path.resolve() == edit_path.resolve()
        output_existed = output_path.exists()
        output_digest = (
            input_digest
            if same_input_output
            else _sha256_file(output_path) if output_existed else None
        )

        decrypted = crypto.decrypt(edit_path)
        edit_file = EditFile()
        data_file = _decrypted_data_file(decrypted)
        edit_file.load(data_file)
        _require_valid_edit(edit_file, "Input save")
        allow_create_requested = bool(getattr(args, "allow_create", False))
        allow_create = (
            allow_create_requested and PLAYER_CREATE_MUTATIONS_ENABLED
        )
        allow_overflow_release = bool(
            getattr(args, "allow_overflow_release", True)
        )
        if allow_create_requested and not PLAYER_CREATE_MUTATIONS_ENABLED:
            print(
                "  Reviewed create-player mutations are disabled; "
                "--allow-create is reserved for a future safe implementation."
            )
        appearance_source = None
        if allow_create:
            appearance_data, appearance_source = _load_player_appearance_data(
                getattr(args, "appearance_file", None),
                getattr(args, "game_root", None),
            )
            if appearance_data is not None:
                edit_file.attach_player_appearance(appearance_data)
                print(f"  Loaded PlayerAppearance.bin metadata from {appearance_source}")
            else:
                print(
                    "  PlayerAppearance.bin metadata unavailable; "
                    "create specs will be rejected safely"
                )
        if bool(getattr(args, "preflight", False)):
            results = _assess_player_specs(
                edit_file,
                specs,
                manifest.revision,
                allow_overflow_release=allow_overflow_release,
                reject_create_mutations=True,
            )
            _print_player_spec_results(specs, results)
            _print_player_apply_preflight(
                edit_file,
                specs,
                results,
                appearance_source,
            )
            return

        apply_kwargs = {
            "allow_overflow_release": allow_overflow_release,
        }
        if allow_create:
            apply_kwargs["allow_create"] = True
        results = apply_player_specs(
            edit_file,
            specs,
            manifest.revision,
            edit_file.get_all_players(),
            **apply_kwargs,
        )
        _print_player_spec_results(specs, results)
        changed_results = tuple(
            result for result in results if result.status in {"created", "updated"}
        )
        if not changed_results:
            print("No Player Update changes to apply; no backup or output was written.")
            _raise_for_player_spec_mutation_failures(results)
            return

        _require_valid_edit(edit_file, "Modified save")
        backup_path = backup_mod.create_backup(edit_path)
        if _sha256_file(edit_path) != input_digest:
            print("Input EDIT file changed while Player Updates were being processed.")
            raise SystemExit(2)
        if not same_input_output:
            output_changed = output_path.exists() != output_existed
            if output_existed and output_path.exists():
                output_changed = _sha256_file(output_path) != output_digest
            if output_changed:
                print("Output EDIT file changed; concurrent output was preserved.")
                raise SystemExit(2)

        edit_file.save(data_file)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        crypto.encrypt(decrypted, output_path)
        _verify_player_spec_output(output_path)

        specs_by_id = {spec.identity.pes_id: spec for spec in specs}
        save_scope = str(output_path.resolve())
        audit_records = [
            _player_spec_audit_record(
                specs_by_id[result.pes_id],
                result,
                edit_file,
                save_scope,
            )
            for result in changed_results
        ]
        for record in audit_records:
            transfer_logger.log_transfer(**record)
        report_records = transfer_logger.read_log(save_scope)
        transfer_logger.save_reports(report_records)
        update_label = "Player Update" if len(audit_records) == 1 else "Player Updates"
        print(
            f"Applied {len(audit_records)} {update_label} to {output_path}. "
            f"Backup: {backup_path}"
        )
        _raise_for_player_spec_mutation_failures(results)
    finally:
        if decrypted is not None:
            crypto.cleanup_temp(decrypted)
        output_lock.release()


def cmd_log(args):
    """Show recent transfer log."""
    transfer_logger.print_summary(last_n=args.last or 20)


def cmd_schedule(args):
    """Run transfers on a recurring interval."""
    interval_sec = int(args.interval_hours * 3600)
    print(f"⏰ Starting transfer automation scheduler (interval: every {args.interval_hours} hours)...")
    print("Press Ctrl+C to stop.")

    iteration = 1
    while True:
        print(f"\n--- [Scheduler Run #{iteration}] {time.strftime('%Y-%m-%d %H:%M:%S')} ---")
        try:
            cmd_run(args)
        except SystemExit as e:
            # cmd_run uses non-zero SystemExit for fail-closed operational
            # aborts. A scheduler must record that run and try again later.
            if e.code in (None, 0):
                raise
            logger.error("Scheduler run #%s aborted with exit code %s", iteration, e.code)
            print(f"✗ Run #{iteration} aborted safely (exit code {e.code})")
        except Exception as e:
            logger.error(f"Scheduler run #{iteration} failed: {e}", exc_info=True)
            print(f"✗ Run #{iteration} encountered an error: {e}")

        iteration += 1
        print(f"\n💤 Sleeping for {args.interval_hours} hours until next run...")
        try:
            time.sleep(interval_sec)
        except KeyboardInterrupt:
            print("\nScheduler stopped by user.")
            break


def cmd_cron(args):
    """Generate or install crontab entry for automated transfers."""
    py_path = sys.executable
    script_path = (Path(__file__).parent / "run.py").resolve()
    cwd_path = Path(__file__).parent.resolve()
    
    interval_hours = args.interval_hours or 6
    cron_expr = f"0 */{interval_hours} * * *"
    cron_line = (
        f"{cron_expr} cd {shlex.quote(str(cwd_path))} && "
        f"{shlex.quote(str(py_path))} {shlex.quote(str(script_path))} run >> "
        f"{shlex.quote(str(config.DATA_DIR / 'cron.log'))} 2>&1"
    )

    print("\n📅 Automated Cron Configuration")
    print("================================")
    print(f"Schedule: Every {interval_hours} hours (`{cron_expr}`)")
    print(f"\nCrontab entry:\n\n  {cron_line}\n")
    print("To install automatically, run:")
    print(f'  (crontab -l 2>/dev/null; echo "{cron_line}") | crontab -')


def main():
    parser = argparse.ArgumentParser(
        description="FL Daily Edit",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")

    sub = parser.add_subparsers(dest="command")

    # run (default)
    p_run = sub.add_parser(
        "run", help="Apply verified transfers and current squad numbers"
    )
    p_run.add_argument("--dry-run", action="store_true", help="Don't modify the edit file")
    run_source = p_run.add_mutually_exclusive_group()
    run_source.add_argument("--edit-file", type=str, help="Path to input EDIT00000000")
    run_source.add_argument(
        "--from-base",
        action="store_true",
        help="Rebuild from base/EDIT00000000 instead of continuing from an existing output",
    )
    run_target = p_run.add_mutually_exclusive_group()
    run_target.add_argument("-o", "--output", type=str, help="Path to output updated edit00000000 (default: output/EDIT00000000)")
    run_target.add_argument("--in-place", action="store_true", help="Overwrite input edit file in-place instead of writing to output/")
    p_run.add_argument(
        "--game-root",
        type=Path,
        help="Game installation root; auto-load Player.bin from download/",
    )
    p_run.add_argument("--club", type=str, help="Comma-separated club names to focus scrape (e.g. 'Chelsea,Arsenal')")
    p_run.add_argument("--deep", action="store_true", help="Deep fetch across all locally indexed FotMob clubs")
    p_run.add_argument("--window", type=str, choices=["auto", "summer", "winter", "all"], default="auto", help="Transfer window (default: auto)")
    p_run.add_argument("--since", type=_iso_date_arg, help="Scrape transfers since date (YYYY-MM-DD)")
    p_run.add_argument("--threshold", type=_percentage_arg, help="Fuzzy match confidence threshold (0-100)")
    p_run.add_argument("--popular", action="store_true", help="Only request FotMob popular transfers")
    p_run.add_argument(
        "--fotmob-only",
        action="store_true",
        help="Disable supplemental Wikipedia and Sortitoutsi sources",
    )
    p_run.add_argument(
        "--allow-overflow-release",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Allow role-based overflow release (default); use "
            "--no-allow-overflow-release to keep full rosters unchanged"
        ),
    )
    p_run.add_argument(
        "--release-policy",
        type=Path,
        help="JSON protected-player and offline-usage snapshot (default: data/release_policy.json)",
    )
    p_run.set_defaults(func=cmd_run)

    # explicit player-spec workflow
    p_players = sub.add_parser(
        "players",
        help="Validate or apply revision-scoped Player Updates",
        description="Validate or apply revision-scoped Player Updates",
        formatter_class=lambda prog: argparse.HelpFormatter(prog, width=100),
    )
    players_sub = p_players.add_subparsers(
        dest="players_command", required=True
    )
    p_players_validate = players_sub.add_parser(
        "validate", help="Validate Player Updates against the pristine base"
    )
    p_players_validate.set_defaults(func=cmd_players_validate)
    p_players_approve = players_sub.add_parser(
        "approve", help="Approve one generated Player Update proposal"
    )
    p_players_approve.add_argument(
        "--spec", required=True, help="Path to the generated player proposal JSON"
    )
    p_players_approve.set_defaults(func=cmd_players_approve)
    p_players_generate = players_sub.add_parser(
        "generate-draft",
        help="Generate a reviewable Pes Retro Stats proposal from an issue event",
        description="Generate a reviewable Pes Retro Stats proposal from an issue event.",
    )
    p_players_generate.add_argument(
        "--event", required=True, help="Path to a trusted GitHub issue-event JSON file"
    )
    p_players_generate.add_argument(
        "--output-dir", required=True, help="Directory for the generated player draft"
    )
    p_players_generate.set_defaults(func=cmd_players_generate_draft)
    p_players_apply = players_sub.add_parser(
        "apply", help="Apply reviewed Player Updates to an EDIT file"
    )
    p_players_apply.add_argument(
        "--base-revision",
        required=True,
        help="Exact base-manifest revision reviewed by the caller",
    )
    p_players_apply.add_argument(
        "--edit-file", required=True, help="Path to input EDIT00000000"
    )
    p_players_apply.add_argument(
        "--allow-create",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "Reserved compatibility option; reviewed create-player mutations "
            "are currently disabled until a safe implementation is available"
        ),
    )
    p_players_apply.add_argument(
        "--allow-overflow-release",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Keep role-based overflow release enabled by default for roster "
            "mutations; use --no-allow-overflow-release to keep full rosters "
            "unchanged"
        ),
    )
    p_players_apply.add_argument(
        "--appearance-file",
        type=Path,
        help="Reserved raw or WESYS PlayerAppearance.bin compatibility input",
    )
    p_players_apply.add_argument(
        "--game-root",
        type=Path,
        help="Game installation root used to discover PlayerAppearance.bin in download/",
    )
    p_players_apply.add_argument(
        "--preflight",
        action="store_true",
        help="Print create destinations and safety inputs without writing",
    )
    player_target = p_players_apply.add_mutually_exclusive_group(required=True)
    player_target.add_argument(
        "-o", "--output", help="Path to output updated EDIT00000000"
    )
    player_target.add_argument(
        "--in-place", action="store_true", help="Overwrite the input EDIT file"
    )
    p_players_apply.set_defaults(func=cmd_players_apply)

    # schedule
    p_sched = sub.add_parser(
        "schedule",
        help="Run transfers and squad-number sync continuously on a timer",
    )
    p_sched.add_argument("--interval-hours", type=_positive_float_arg, default=6.0, help="Interval between runs in hours (default: 6.0)")
    p_sched.add_argument("--dry-run", action="store_true", help="Don't modify the edit file")
    schedule_source = p_sched.add_mutually_exclusive_group()
    schedule_source.add_argument("--edit-file", type=str, help="Path to input edit00000000")
    schedule_source.add_argument(
        "--from-base",
        action="store_true",
        help="Rebuild from base/EDIT00000000 on every scheduled run",
    )
    p_sched.add_argument(
        "--game-root",
        type=Path,
        help="Game installation root; auto-load Player.bin from download/",
    )
    schedule_target = p_sched.add_mutually_exclusive_group()
    schedule_target.add_argument("-o", "--output", type=str, help="Path to output updated edit00000000 (default: output/EDIT00000000)")
    schedule_target.add_argument("--in-place", action="store_true", help="Overwrite input edit file in-place")
    p_sched.add_argument("--club", type=str, help="Comma-separated club names to focus scrape (e.g. 'Chelsea,Arsenal')")
    p_sched.add_argument("--deep", action="store_true", help="Deep fetch across all locally indexed FotMob clubs")
    p_sched.add_argument("--window", type=str, choices=["auto", "summer", "winter", "all"], default="auto", help="Transfer window (default: auto)")
    p_sched.add_argument("--since", type=_iso_date_arg, help="Scrape transfers since date (YYYY-MM-DD)")
    p_sched.add_argument("--threshold", type=_percentage_arg, help="Fuzzy match confidence threshold (0-100)")
    p_sched.add_argument("--popular", action="store_true", help="Only request FotMob popular transfers")
    p_sched.add_argument(
        "--fotmob-only",
        action="store_true",
        help="Disable supplemental Wikipedia and Sortitoutsi sources",
    )
    p_sched.add_argument(
        "--allow-overflow-release",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Allow role-based overflow release (default); use "
            "--no-allow-overflow-release to keep full rosters unchanged"
        ),
    )
    p_sched.add_argument(
        "--release-policy",
        type=Path,
        help="JSON protected-player and offline-usage snapshot (default: data/release_policy.json)",
    )
    p_sched.set_defaults(func=cmd_schedule)

    # cron
    p_cron = sub.add_parser("cron", help="Generate crontab line for automated scheduling")
    p_cron.add_argument("--interval-hours", type=_positive_int_arg, default=6, help="Interval in hours (default: 6)")
    p_cron.set_defaults(func=cmd_cron)

    # inspect
    p_inspect = sub.add_parser("inspect", help="Inspect an edit file structure")
    p_inspect.add_argument("--edit-file", type=str, required=True, help="Path to edit00000000")
    p_inspect.set_defaults(func=cmd_inspect)


    # metadata audit
    p_audit = sub.add_parser(
        "audit",
        help="Audit a save against native Player/Team metadata",
    )
    p_audit.add_argument(
        "--edit-file",
        type=str,
        required=True,
        help="Path to input EDIT00000000",
    )
    p_audit.add_argument(
        "--game-root",
        type=Path,
        help="Game installation root containing download/*.cpk",
    )
    p_audit.add_argument(
        "--player-bin",
        type=Path,
        help="Explicit Player.bin path",
    )
    p_audit.add_argument(
        "--team-bin",
        type=Path,
        help="Explicit Team.bin path",
    )
    p_audit.add_argument(
        "--player-assignment",
        type=Path,
        help="Explicit PlayerAssignment.bin path",
    )
    p_audit.add_argument(
        "--as-of",
        type=_iso_date_arg,
        default=date.today().isoformat(),
        help="Contract report date (YYYY-MM-DD)",
    )
    p_audit.add_argument(
        "--json",
        action="store_true",
        help="Emit only the bounded audit report as JSON",
    )
    p_audit.set_defaults(func=cmd_metadata_audit)

    # reviewed-spec/base roster audit
    p_base_audit = sub.add_parser(
        "base-audit",
        help="Check active Player Updates against one base roster",
    )
    p_base_audit.add_argument(
        "--edit-file",
        type=str,
        required=True,
        help="Path to input EDIT00000000",
    )
    p_base_audit.add_argument(
        "--spec-dir",
        type=Path,
        help="Player spec directory (default: players)",
    )
    p_base_audit.add_argument(
        "--json",
        action="store_true",
        help="Emit the bounded audit report as JSON",
    )
    p_base_audit.add_argument(
        "--strict",
        action="store_true",
        help="Exit 2 when any active spec is missing or inconsistent",
    )
    p_base_audit.add_argument(
        "--as-of",
        type=_iso_date_arg,
        default=date.today().isoformat(),
        help="Loan review date (YYYY-MM-DD)",
    )
    p_base_audit.set_defaults(func=cmd_base_roster_audit)

    # safe base refresh
    p_base_refresh = sub.add_parser(
        "base-refresh",
        help="Verify and optionally promote a local or HTTPS EDIT base",
    )
    p_base_refresh.add_argument(
        "--source",
        required=True,
        help="Local EDIT00000000 path or HTTPS download URL",
    )
    p_base_refresh.add_argument(
        "--revision",
        required=True,
        help="Base revision to write when --promote is used",
    )
    p_base_refresh.add_argument(
        "--spec-dir",
        type=Path,
        help="Player spec directory (default: players)",
    )
    p_base_refresh.add_argument(
        "--as-of",
        type=_iso_date_arg,
        default=date.today().isoformat(),
        help="Loan review date (YYYY-MM-DD)",
    )
    p_base_refresh.add_argument(
        "--promote",
        action="store_true",
        help="Atomically replace base/EDIT00000000 and its manifest",
    )
    p_base_refresh.add_argument(
        "--strict-audit",
        action="store_true",
        help="Reject promotion when active specs are missing or inconsistent",
    )
    p_base_refresh.add_argument(
        "--json",
        action="store_true",
        help="Emit the verification report as JSON",
    )
    p_base_refresh.set_defaults(func=cmd_base_refresh)

    # offline usage snapshot importer
    p_usage_import = sub.add_parser(
        "usage-import",
        help="Merge CSV usage counters into the release policy",
    )
    p_usage_import.add_argument(
        "--input",
        type=Path,
        required=True,
        help="CSV with player_id, minutes, starts, appearances, news_mentions",
    )
    p_usage_import.add_argument(
        "--output",
        type=Path,
        help="Output policy path (default: data/release_policy.json)",
    )
    p_usage_import.add_argument("--source", default="", help="Usage source label")
    p_usage_import.add_argument("--season", default="", help="Season label")
    p_usage_import.add_argument("--as-of", default="", help="Snapshot date label")
    p_usage_import.set_defaults(func=cmd_usage_import)
    # native metadata variant comparison
    p_compare = sub.add_parser(
        "compare",
        help="Compare native metadata between two CPK variants",
    )
    left_source = p_compare.add_mutually_exclusive_group(required=True)
    left_source.add_argument(
        "--left-cpk",
        type=Path,
        help="Left data_s2526.cpk or data_extra.cpk path",
    )
    left_source.add_argument(
        "--left-game-root",
        type=Path,
        help="Left game installation root containing download/*.cpk",
    )
    right_source = p_compare.add_mutually_exclusive_group(required=True)
    right_source.add_argument(
        "--right-cpk",
        type=Path,
        help="Right data_s2526.cpk or data_extra.cpk path",
    )
    right_source.add_argument(
        "--right-game-root",
        type=Path,
        help="Right game installation root containing download/*.cpk",
    )
    p_compare.add_argument(
        "--json",
        action="store_true",
        help="Emit the bounded comparison as JSON",
    )
    p_compare.set_defaults(func=cmd_compare_metadata)


    # validate
    p_validate = sub.add_parser(
        "validate", help="Validate an encrypted edit file with a supported PES edit-file layout"
    )
    p_validate.add_argument("--edit-file", type=str, required=True, help="Path to edit00000000")
    p_validate.set_defaults(func=cmd_validate)

    # repair
    p_repair = sub.add_parser(
        "repair",
        help="Repair a legacy base without importing reference league memberships",
    )
    p_repair.add_argument(
        "--edit-file",
        type=str,
        required=True,
        help="Legacy base EDIT00000000",
    )
    p_repair.add_argument(
        "--reference",
        type=str,
        action="append",
        required=True,
        help="Known-good reference EDIT00000000; repeat for consensus",
    )
    p_repair.add_argument("-o", "--output", type=str, help="Repaired output path")
    p_repair.set_defaults(func=cmd_repair)

    # log
    p_log = sub.add_parser("log", help="Show recent transfer log")
    p_log.add_argument(
        "--last", type=int, default=20, help="Number of recent entries (default: 20)"
    )
    p_log.set_defaults(func=cmd_log)

    subcommands = {
        "run", "players", "schedule", "cron", "inspect", "audit", "base-audit",
        "base-refresh", "usage-import", "compare", "validate", "repair", "log",
        "-h", "--help",
    }
    if len(sys.argv) > 1 and sys.argv[1] not in subcommands:
        sys.argv.insert(1, "run")
    elif len(sys.argv) == 1:
        sys.argv.append("run")

    args = parser.parse_args()
    setup_logging(args.verbose)

    if hasattr(args, "func"):
        try:
            args.func(args)
        except IncompleteScrapeError as exc:
            print(f"\n❌ Scrape incomplete; no roster changes were written: {exc}")
            raise SystemExit(2) from exc
        except PlayerCatalogError as exc:
            print(f"\n❌ Player catalog invalid; no roster changes were written: {exc}")
            raise SystemExit(2) from exc
        except EditLockError as exc:
            print(f"\n❌ Concurrent run rejected: {exc}")
            raise SystemExit(2) from exc
        except PlayerDraftError as exc:
            print(f"Player draft generation failed: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
