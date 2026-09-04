from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging

from editor.editfile import EditFile
from editor.roster import MIN_CLUB_ROSTER_SIZE
from scraper.fotmob import parse_iso_datetime
from scraper.matcher import NameMatcher
from scraper.models import MatchedTransfer

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
    overflow_details: dict[str, object] | None = None


def _optional_positive_int(value) -> int | None:
    """Parse an identifier from external/history data and reject sentinel values."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None

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
    validated_fotmob_teams: dict[int, int] | None = None,
) -> tuple[int | None, str, float]:
    """Resolve validated IDs before falling back to club-name matching."""
    raw_names = [full_name or "", short_name or ""]
    if any(name.strip().casefold() in _NON_CLUB_LABELS for name in raw_names if name.strip()):
        return None, "", 100.0

    id_is_validated: bool | None = None
    validated_team_id: int | None = None
    if fotmob_id is not None:
        try:
            normalized_fotmob_id = int(fotmob_id)
        except (TypeError, ValueError):
            return UNRESOLVED_TEAM_ID, "", 0.0
        if validated_fotmob_ids is not None:
            id_is_validated = normalized_fotmob_id in validated_fotmob_ids
        if validated_fotmob_teams is not None:
            validated_team_id = validated_fotmob_teams.get(normalized_fotmob_id)
    if validated_team_id is not None:
        return validated_team_id, full_name or short_name, 100.0


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
    validated_fotmob_teams: dict[int, int] | None = None,
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
            validated_fotmob_teams,
        )
        ttid, ttname, ttconf = _match_transfer_team(
            matcher,
            transfer.to_club,
            transfer.to_club_full_name,
            transfer.to_club_id_fotmob,
            validated_fotmob_ids,
            validated_fotmob_teams,
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
