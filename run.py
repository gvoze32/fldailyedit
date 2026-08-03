#!/usr/bin/env python3
"""
FL26 Transfer Automation Tool — Main Entry Point

Usage:
    python run.py run --dry-run                       # Scrape + match only, no file changes
    python run.py run --edit-file /path/to/EDIT00000000
    python run.py inspect --edit-file /path/to/EDIT00000000
    python run.py validate --edit-file /path/to/EDIT00000000
    python run.py log                                 # Show recent transfer log

Workflow:
    1. Collect and reconcile FotMob, Wikipedia, and Sortitoutsi transfers
    2. Decrypt and validate the edit file (pesXdecrypter)
    3. Load the current FL26 catalog and roster state
    4. Match identities and plan safe roster actions
    5. Apply the batch, validate, re-encrypt, and log it
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
from datetime import datetime
from pathlib import Path

import config
from editor import backup as backup_mod
from editor import crypto
from editor.editfile import COMPETITION_SECTION_SIZE, EditFile
from editor import logger as transfer_logger
from editor.locking import EditFileLock, EditLockError
from editor.player_catalog import PlayerCatalogError, load_id_name_text
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
from scraper.matcher import NameMatcher
from scraper.models import MatchedTransfer
from scraper.sortitoutsi import fetch_sortitoutsi_transfers
from scraper.sources import reconcile_transfer_sources
from scraper.wikipedia import fetch_wikipedia_transfers
from scraper.transfermarkt import fetch_transfermarkt_transfers

logger = logging.getLogger(__name__)
UNRESOLVED_TEAM_ID = -1
_NON_CLUB_LABELS = {"", "free agent", "without club", "unattached", "career break", "retired"}


@dataclass
class PlannedRosterAction:
    match: MatchedTransfer
    action: str
    current_team_id: int | None
    reason: str = ""
    overflow_player_id: int | None = None


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

    return edit_path, output_path


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


def _match_transfers_statefully(
    transfers,
    matcher: NameMatcher,
    threshold: float,
    team_player_map: dict[int, list[int]],
    club_ids: set[int],
    historical_entries: list[dict] | None = None,
    validated_fotmob_ids: set[int] | None = None,
) -> list[MatchedTransfer]:
    """Match chronologically while advancing a virtual roster context."""
    virtual_rosters = {
        team_id: list(player_ids)
        for team_id, player_ids in team_player_map.items()
    }
    loaned_by_parent: dict[int, set[int]] = {}
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
        if str(entry.get("transfer_type", "")).lower() == "loan":
            loaned_by_parent.setdefault(source, set()).add(player_id)
        else:
            loaned_by_parent.get(source, set()).discard(player_id)

    fotmob_to_pes = {
        fotmob_player_id: next(iter(player_ids))
        for fotmob_player_id, player_ids in fotmob_identity_candidates.items()
        if len(player_ids) == 1
    }

    matched: list[MatchedTransfer] = []
    for transfer in transfers:
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
        if ftid is None and transfer.infer_from_current_roster:
            inferred_team_id = current_clubs[0] if len(current_clubs) == 1 else None
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

        current_team_id = current_clubs[0] if len(current_clubs) == 1 else None
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

        if ftid is not None and (transfer.is_loan or transfer.transfer_type == "loan"):
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
    allow_overflow_release: bool = False,
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
        player_id = match.player_id
        if player_id is None:
            planned.append(
                PlannedRosterAction(match, "skip", None, "player_not_matched")
            )
            continue

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

        if action in {"move", "add"}:
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


def cmd_validate(args):
    """Validate an encrypted FL26 edit file and return a shell-friendly status."""
    edit_path = Path(args.edit_file)
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
            print("PASS: save structure matches known-good Football Life 2026 files")
            return
        print(f"FAIL: {len(report['errors'])} integrity error(s)")
        raise SystemExit(2)
    finally:
        crypto.cleanup_temp(temp_dir)


def cmd_repair(args):
    """Repair a legacy base using consensus registrations from valid references."""
    edit_path = Path(args.edit_file)
    output_path = Path(args.output) if args.output else config.OUTPUT_FILE_PATH
    reference_paths = [Path(path) for path in args.reference]

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
        transfer_batches.append(
            fetch_major_clubs_transfers_safely(
                since_date=since_date, window=window
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

        print("\n🔎 Adding Transfermarkt route corroborators...")
        transfermarkt_corroborators = fetch_transfermarkt_transfers()
        corroborators.extend(transfermarkt_corroborators)
        print(
            f"  Transfermarkt found {len(transfermarkt_corroborators)} recent complete routes"
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


def _load_match_database(edit_file: EditFile):
    """Build roster-aware player and club indexes from one validated save."""
    print("\n📋 Reading FL26 database...")
    players = edit_file.get_all_players()
    edit_file._player_cache = players
    teams_info = edit_file.get_all_team_info()
    club_ids = edit_file.get_club_team_ids()
    current_team_names = load_id_name_text(
        config.CURRENT_TEAMS_FILE,
        label="team",
        minimum_entries=700,
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
    catalog_report = getattr(edit_file, "player_catalog_report", None)
    if allow_overflow_release and (
        catalog_report is None
        or not catalog_report.has_complete_overflow_metadata
    ):
        raise PlayerCatalogError(
            "--allow-overflow-release requires complete player position and OVR "
            "metadata; the current FL26 name catalog does not provide it"
        )

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
        fully_matched,
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


def _print_dry_run(edit_file: EditFile, roster_plan) -> None:
    """Render planned actions without mutating or writing the EDIT file."""
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
            print(
                "  WOULD AUTO-RELEASE: player "
                f"{planned_action.overflow_player_id} from team {match.to_team_id}"
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


def cmd_run(args):
    """Main pipeline — scrape, match, apply transfers."""
    dry_run = args.dry_run
    edit_path, output_path = _resolve_run_paths(args)
    threshold = args.threshold or config.MATCH_THRESHOLD_PLAYER

    if not dry_run and not edit_path.exists():
        print(f"Edit file not found: {edit_path}")
        print("Use --edit-file to specify the path, or set EDIT_FILE_PATH in config.py")
        sys.exit(1)

    allow_overflow_release = getattr(args, "allow_overflow_release", False)
    transfers = _scrape_run_transfers(args)

    if not transfers:
        print("No transfers found. Exiting.")
        return

    if dry_run and not edit_path.exists():
        print("\n⚠ Dry-run mode without edit file — showing scraped data only.")
        print(f"\nAll {len(transfers)} transfers:")
        for t in transfers:
            print(f"  {t}")
        return

    output_lock = EditFileLock(output_path)
    output_lock.acquire()
    try:
        input_digest = _sha256_file(edit_path)
        same_input_output = output_path.resolve() == edit_path.resolve()
        output_existed = output_path.exists()
        output_digest = (
            input_digest
            if same_input_output
            else _sha256_file(output_path) if output_existed else None
        )
    except Exception:
        output_lock.release()
        raise

    print(f"\n🔓 Decrypting {edit_path}...")
    try:
        temp_dir = crypto.decrypt(edit_path)
    except Exception as e:
        output_lock.release()
        print(f"Decryption failed: {e}")
        sys.exit(1)

    try:
        data_dat = temp_dir / "data.dat"
        if not data_dat.exists():
            dat_files = list(temp_dir.glob("*.dat"))
            data_dat = max(dat_files, key=lambda f: f.stat().st_size)

        ef = EditFile()
        ef.load(data_dat)

        integrity = ef.validate_integrity()
        if not integrity["valid"]:
            print("\n❌ Input save failed FL26 integrity validation; no changes will be written.")
            for error in integrity["errors"][:20]:
                print(f"  - {error}")
            remaining = len(integrity["errors"]) - 20
            if remaining > 0:
                print(f"  ... and {remaining} more errors")
            print("Use a known-good Football Life 2026 EDIT00000000 as --edit-file.")
            sys.exit(2)

        matcher, all_rosters, team_player_map, club_ids = _load_match_database(ef)
        roster_plan, fully_matched, save_scope = _match_and_plan_transfers(
            transfers,
            matcher,
            threshold,
            team_player_map,
            all_rosters,
            club_ids,
            ef,
            output_path,
            allow_overflow_release=allow_overflow_release,
        )

        if not fully_matched:
            print("\nNo fully matched transfers to apply. Exiting.")
            return

        run_records = []
        if dry_run:
            _print_dry_run(ef, roster_plan)
            return

        # Create backup before modifying
        print(f"\n💾 Creating backup...")
        backup_path = backup_mod.create_backup(edit_path)
        print(f"  Backup: {backup_path}")

        print(f"\n⚡ Applying verified transfers and shirt-number changes...")
        transfer_applied = 0
        shirt_numbers_applied = 0
        unchanged = 0
        safety_skipped = 0
        original_data = bytes(ef._data)
        pending_logs = []

        for planned_action in roster_plan:
            m = planned_action.match
            pid = m.player_id
            to_tid = m.to_team_id
            t = m.transfer
            
            if pid is None:
                continue
                
            action = planned_action.action
            current_tid = planned_action.current_team_id
            if action == "skip":
                safety_skipped += 1
                print(
                    f"  ⚠ Safety skip {m.matched_player_name or t.player_name}: "
                    f"{planned_action.reason or 'state mismatch'}"
                )
                continue
            if action == "noop":
                unchanged += 1
                continue

            ok = False
            pref_shirt = t.shirt_number
            previous_shirt = None
            if action == "shirt_update":
                if pref_shirt is None:
                    unchanged += 1
                    continue
                previous_shirt = ef.get_player_shirt_number(to_tid, pid)
                if previous_shirt == pref_shirt:
                    unchanged += 1
                    continue
                conflicting_player = _find_shirt_number_conflict(
                    ef, to_tid, pid, pref_shirt
                )
                if conflicting_player is not None:
                    safety_skipped += 1
                    print(
                        f"  ⚠ Safety skip {m.matched_player_name or t.player_name}: "
                        f"shirt #{pref_shirt} is already assigned to player "
                        f"{conflicting_player} on team {to_tid}"
                    )
                    continue
                ok = ef.update_player_shirt_number(to_tid, pid, pref_shirt)
            elif action == "move":
                ok = ef.move_player(
                    pid,
                    current_tid,
                    to_tid,
                    shirt_number=pref_shirt,
                    position=t.position,
                    allow_overflow_release=allow_overflow_release,
                )
            elif action == "add":
                ok = ef.add_player(
                    pid,
                    to_tid,
                    shirt_number=pref_shirt,
                    position=t.position,
                    allow_overflow_release=allow_overflow_release,
                )
            elif action == "release":
                ok = ef.release_player(pid, m.from_team_id)

            if ok:
                if action == "shirt_update":
                    shirt_numbers_applied += 1
                else:
                    transfer_applied += 1
                pending_logs.append((m, previous_shirt, action))
                run_records.append({
                    "player_name": m.matched_player_name or m.transfer.player_name,
                    "from_team": m.matched_from_team or m.transfer.from_club,
                    "to_team": m.matched_to_team or m.transfer.to_club,
                    "position": m.transfer.position,
                    "fee": m.transfer.fee,
                    "transfer_type": m.transfer.transfer_type,
                    "confidence": m.min_confidence,
                    "dry_run": False,
                    "previous_shirt_number": previous_shirt,
                    "shirt_number": pref_shirt if action == "shirt_update" else None,
                    "roster_action": action,
                    "sources": list(m.transfer.sources),
                    "source_urls": list(m.transfer.source_urls),
                    "proof_urls": list(m.transfer.proof_urls),
                })
            else:
                ef._data = bytearray(original_data)
                print(
                    f"  ✗ Failed: {m.matched_player_name or m.transfer.player_name} "
                    f"({m.action_type}); entire batch rolled back"
                )
                sys.exit(2)

        print(
            f"\n  Transfers applied: {transfer_applied}, "
            f"shirt numbers changed: {shirt_numbers_applied}, "
            f"already current: {unchanged}, safety-skipped: {safety_skipped}"
        )

        post_integrity = ef.validate_integrity()
        if not post_integrity["valid"]:
            ef._data = bytearray(original_data)
            print("\n❌ Modified save failed integrity validation; changes were rolled back.")
            for error in post_integrity["errors"][:20]:
                print(f"  - {error}")
            remaining = len(post_integrity["errors"]) - 20
            if remaining > 0:
                print(f"  ... and {remaining} more errors")
            sys.exit(2)

        if _sha256_file(edit_path) != input_digest:
            ef._data = bytearray(original_data)
            print(
                "\n❌ Input EDIT file changed while this run was processing; "
                "stale output was not written."
            )
            sys.exit(2)
        if not same_input_output:
            output_changed = output_path.exists() != output_existed
            if output_existed and output_path.exists():
                output_changed = _sha256_file(output_path) != output_digest
            if output_changed:
                ef._data = bytearray(original_data)
                print(
                    "\n❌ Output EDIT file changed while this run was processing; "
                    "concurrent output was preserved."
                )
                sys.exit(2)

        # Save modified data.dat
        ef.save(data_dat)

        # Re-encrypt
        output_path.parent.mkdir(parents=True, exist_ok=True)
        print(f"\n🔒 Re-encrypting → {output_path}...")
        crypto.encrypt(temp_dir, output_path)

        # Persist audit entries only after the binary passed validation and
        # verified encryption round-trip.
        for m, previous_shirt, action in pending_logs:
            transfer_logger.log_transfer(
                player_name=m.matched_player_name or m.transfer.player_name,
                player_id=m.player_id,
                from_team=m.matched_from_team or m.transfer.from_club,
                from_team_id=m.from_team_id or 0,
                to_team=m.matched_to_team or m.transfer.to_club,
                to_team_id=m.to_team_id or 0,
                confidence=m.min_confidence,
                transfer_type=m.transfer.transfer_type,
                dry_run=False,
                position=m.transfer.position,
                fee=m.transfer.fee,
                market_value=m.transfer.market_value,
                transfer_date=m.transfer.date,
                previous_shirt_number=previous_shirt,
                shirt_number=(
                    m.transfer.shirt_number
                    if m.transfer.transfer_type == "shirt_number_update"
                    else None
                ),
                roster_action=action,
                save_scope=save_scope,
                fotmob_player_id=m.transfer.player_id_fotmob,
                sortitoutsi_player_id=m.transfer.player_id_sortitoutsi,
                transfermarkt_player_id=m.transfer.player_id_transfermarkt,
                transfermarkt_from_club_id=m.transfer.from_club_id_transfermarkt,
                transfermarkt_to_club_id=m.transfer.to_club_id_transfermarkt,
                transfermarkt_transfer_id=m.transfer.transfer_id_transfermarkt,
                sources=m.transfer.sources,
                source_urls=m.transfer.source_urls,
                proof_urls=m.transfer.proof_urls,
            )

        # Save visual reports
        transfer_logger.save_reports(run_records)

        print(
            f"\n✅ Done! {transfer_applied} transfers applied; "
            f"{shirt_numbers_applied} shirt numbers changed."
        )
        if output_path.resolve() != edit_path.resolve():
            print(f"   Input (base/pristine):   {edit_path}")
            print(f"   Output (updated save):   {output_path}")
        else:
            print(f"   Updated file:            {output_path}")
        print(f"   Backup at:               {backup_path}")
        print(f"   Log at:                  {config.TRANSFER_LOG_FILE}")
        print(f"   Visual Summary Report:   {config.OUTPUT_DIR / 'transfer_summary.md'}")

    finally:
        crypto.cleanup_temp(temp_dir)
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
        description="FL26 Transfer Automation Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")

    sub = parser.add_subparsers(dest="command")

    # run (default)
    p_run = sub.add_parser("run", help="Scrape + match + apply transfers")
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
        action="store_true",
        help="Allow releasing a displayed overflow candidate when a roster is full",
    )
    p_run.set_defaults(func=cmd_run)

    # schedule
    p_sched = sub.add_parser("schedule", help="Run transfers continuously on a timer")
    p_sched.add_argument("--interval-hours", type=_positive_float_arg, default=6.0, help="Interval between runs in hours (default: 6.0)")
    p_sched.add_argument("--dry-run", action="store_true", help="Don't modify the edit file")
    schedule_source = p_sched.add_mutually_exclusive_group()
    schedule_source.add_argument("--edit-file", type=str, help="Path to input edit00000000")
    schedule_source.add_argument(
        "--from-base",
        action="store_true",
        help="Rebuild from base/EDIT00000000 on every scheduled run",
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
        action="store_true",
        help="Allow releasing a displayed overflow candidate when a roster is full",
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

    # validate
    p_validate = sub.add_parser("validate", help="Validate an encrypted FL26 edit file")
    p_validate.add_argument("--edit-file", type=str, required=True, help="Path to edit00000000")
    p_validate.set_defaults(func=cmd_validate)

    # repair
    p_repair = sub.add_parser(
        "repair",
        help="Repair a legacy base without importing reference league memberships",
    )
    p_repair.add_argument("--edit-file", type=str, required=True, help="Legacy base EDIT00000000")
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
    p_log.add_argument("--last", type=int, default=20, help="Number of recent entries (default: 20)")
    p_log.set_defaults(func=cmd_log)

    # Pre-parse argv: if first arg is a flag or omitted, default to 'run'
    subcommands = {
        "run", "schedule", "cron", "inspect", "validate", "repair", "log", "-h", "--help"
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
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
