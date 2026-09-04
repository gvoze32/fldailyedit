"""Read-only consistency reports across save and game metadata databases."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any, Protocol

from editor.models import TeamData
from editor.player_assignment import PlayerAssignmentDatabase
from editor.playerbin import PlayerBinDatabase
from editor.teambin import TeamBinDatabase

_PREVIEW_LIMIT = 10


class RosterSource(Protocol):
    """Minimal EditFile surface required by :func:`audit_metadata`."""

    def get_all_rosters(self) -> dict[int, TeamData]:
        ...


def _preview(values: set[int] | list[int] | tuple[int, ...]) -> tuple[int, ...]:
    return tuple(sorted(values)[:_PREVIEW_LIMIT])


def _parse_contract(value: int) -> date | None | bool:
    """Return a date, None for no date, or False for malformed metadata."""
    if value <= 0:
        return None
    year, remainder = divmod(value, 10_000)
    month, day = divmod(remainder, 100)
    try:
        return date(year, month, day)
    except ValueError:
        return False


@dataclass(frozen=True, slots=True)
class MetadataAuditReport:
    """Bounded, JSON-serializable diagnostics for one selected metadata variant."""

    as_of: str
    save_roster_teams: int
    save_roster_players: int
    playerbin_entries: int | None
    teambin_entries: int | None
    assignment_entries: int | None
    assignment_players: int | None
    missing_sources: tuple[str, ...]
    save_players_missing_from_playerbin: int | None
    save_players_missing_from_playerbin_preview: tuple[int, ...]
    assignment_players_missing_from_playerbin: int | None
    assignment_players_missing_from_playerbin_preview: tuple[int, ...]
    playerbin_players_missing_from_assignment: int | None
    playerbin_players_missing_from_assignment_preview: tuple[int, ...]
    assignment_team_keys_missing_from_teambin: int | None
    assignment_team_keys_missing_from_teambin_preview: tuple[int, ...]
    duplicate_assignment_pairs: int | None
    duplicate_assignment_pairs_preview: tuple[tuple[int, int], ...]
    multi_assignment_players: int | None
    multi_assignment_players_preview: tuple[int, ...]
    assignment_zero_player_rows: int | None
    assignment_zero_team_rows: int | None
    contract_expired_players: int | None
    contract_expired_players_preview: tuple[int, ...]
    invalid_contract_players: int | None
    invalid_contract_players_preview: tuple[int, ...]
    loan_players: int | None
    loan_players_preview: tuple[int, ...]
    owner_team_players: int | None
    market_value_players: int | None
    caps_players: int | None

    def to_dict(self) -> dict[str, Any]:
        """Return stable JSON-compatible report data."""
        return asdict(self)


def audit_metadata(
    edit_file: RosterSource,
    playerbin_db: PlayerBinDatabase | None,
    teambin_db: TeamBinDatabase | None = None,
    assignment_db: PlayerAssignmentDatabase | None = None,
    *,
    as_of: date | None = None,
) -> MetadataAuditReport:
    """Audit save roster IDs and optional native game metadata without mutating data."""
    report_date = date.today() if as_of is None else as_of
    rosters = edit_file.get_all_rosters()
    save_player_ids = {
        player_id
        for roster in rosters.values()
        for player_id in roster.player_ids
        if player_id
    }


    missing_sources = tuple(
        source
        for source, database in (
            ("Player.bin", playerbin_db),
            ("Team.bin", teambin_db),
            ("PlayerAssignment.bin", assignment_db),
        )
        if database is None
    )

    playerbin_records = tuple(playerbin_db.values()) if playerbin_db is not None else ()
    playerbin_ids = {record.player_id for record in playerbin_records}
    save_missing = save_player_ids - playerbin_ids if playerbin_db is not None else set()

    assignment_records = assignment_db.records if assignment_db is not None else ()
    assignment_ids = {
        record.player_id for record in assignment_records if record.player_id
    }
    assignment_team_keys = {
        record.team_key for record in assignment_records if record.team_key
    }
    assignment_pair_counts = Counter(
        (record.player_id, record.team_key)
        for record in assignment_records
        if record.player_id and record.team_key
    )
    duplicate_pairs = {
        pair for pair, count in assignment_pair_counts.items() if count > 1
    }
    player_assignment_keys: dict[int, set[int]] = {}
    for record in assignment_records:
        if record.player_id and record.team_key:
            player_assignment_keys.setdefault(record.player_id, set()).add(
                record.team_key
            )
    multi_assignment_ids = {
        player_id
        for player_id, team_keys in player_assignment_keys.items()
        if len(team_keys) > 1
    }

    assignment_missing_players = (
        assignment_ids - playerbin_ids if playerbin_db is not None else set()
    )
    playerbin_missing_assignments = (
        playerbin_ids - assignment_ids
        if playerbin_db is not None and assignment_db is not None
        else set()
    )
    unknown_team_keys = (
        {
            team_key
            for team_key in assignment_team_keys
            if teambin_db is not None and team_key not in teambin_db
        }
        if teambin_db is not None and assignment_db is not None
        else set()
    )

    expired_contract_ids: set[int] = set()
    invalid_contract_ids: set[int] = set()
    loan_ids: set[int] = set()
    owner_team_ids: set[int] = set()
    market_value_ids: set[int] = set()
    caps_ids: set[int] = set()
    if playerbin_db is not None:
        for record in playerbin_records:
            contract = _parse_contract(record.contract_until)
            if contract is False:
                invalid_contract_ids.add(record.player_id)
            elif isinstance(contract, date) and contract <= report_date:
                expired_contract_ids.add(record.player_id)
            if record.is_on_loan:
                loan_ids.add(record.player_id)
            if record.owner_team_key:
                owner_team_ids.add(record.player_id)
            if record.market_value_eur:
                market_value_ids.add(record.player_id)
            if record.caps:
                caps_ids.add(record.player_id)

    return MetadataAuditReport(
        as_of=report_date.isoformat(),
        save_roster_teams=len(rosters),
        save_roster_players=len(save_player_ids),
        playerbin_entries=len(playerbin_db) if playerbin_db is not None else None,
        teambin_entries=len(teambin_db) if teambin_db is not None else None,
        assignment_entries=(
            len(assignment_db) if assignment_db is not None else None
        ),
        assignment_players=(
            assignment_db.player_count if assignment_db is not None else None
        ),
        missing_sources=missing_sources,
        save_players_missing_from_playerbin=(
            len(save_missing) if playerbin_db is not None else None
        ),
        save_players_missing_from_playerbin_preview=_preview(save_missing),
        assignment_players_missing_from_playerbin=(
            len(assignment_missing_players)
            if playerbin_db is not None and assignment_db is not None
            else None
        ),
        assignment_players_missing_from_playerbin_preview=_preview(
            assignment_missing_players
        ),
        playerbin_players_missing_from_assignment=(
            len(playerbin_missing_assignments)
            if playerbin_db is not None and assignment_db is not None
            else None
        ),
        playerbin_players_missing_from_assignment_preview=_preview(
            playerbin_missing_assignments
        ),
        assignment_team_keys_missing_from_teambin=(
            len(unknown_team_keys)
            if teambin_db is not None and assignment_db is not None
            else None
        ),
        assignment_team_keys_missing_from_teambin_preview=_preview(unknown_team_keys),
        duplicate_assignment_pairs=(
            len(duplicate_pairs) if assignment_db is not None else None
        ),
        duplicate_assignment_pairs_preview=tuple(sorted(duplicate_pairs)[:_PREVIEW_LIMIT]),
        multi_assignment_players=(
            len(multi_assignment_ids) if assignment_db is not None else None
        ),
        multi_assignment_players_preview=_preview(multi_assignment_ids),
        assignment_zero_player_rows=(
            sum(record.player_id == 0 for record in assignment_records)
            if assignment_db is not None
            else None
        ),
        assignment_zero_team_rows=(
            sum(record.team_key == 0 for record in assignment_records)
            if assignment_db is not None
            else None
        ),
        contract_expired_players=(
            len(expired_contract_ids) if playerbin_db is not None else None
        ),
        contract_expired_players_preview=_preview(expired_contract_ids),
        invalid_contract_players=(
            len(invalid_contract_ids) if playerbin_db is not None else None
        ),
        invalid_contract_players_preview=_preview(invalid_contract_ids),
        loan_players=len(loan_ids) if playerbin_db is not None else None,
        loan_players_preview=_preview(loan_ids),
        owner_team_players=(len(owner_team_ids) if playerbin_db is not None else None),
        market_value_players=(
            len(market_value_ids) if playerbin_db is not None else None
        ),
        caps_players=len(caps_ids) if playerbin_db is not None else None,
    )


def _count(value: int | None) -> str:
    return "unavailable" if value is None else f"{value:,}"


def _preview_text(values: tuple[int, ...] | tuple[tuple[int, int], ...]) -> str:
    if not values:
        return "-"
    if isinstance(values[0], tuple):
        return ", ".join(f"{player}/{team}" for player, team in values)
    return ", ".join(str(value) for value in values)


def format_metadata_audit(report: MetadataAuditReport) -> str:
    """Format a bounded human-readable metadata audit."""
    lines = [
        "--- Save Metadata Audit ---",
        f"As of: {report.as_of}",
        "",
        "Consistency:",
        f"  Save players missing from Player.bin: "
        f"{_count(report.save_players_missing_from_playerbin)} "
        f"({_preview_text(report.save_players_missing_from_playerbin_preview)})",
        f"  Assignment players missing from Player.bin: "
        f"{_count(report.assignment_players_missing_from_playerbin)} "
        f"({_preview_text(report.assignment_players_missing_from_playerbin_preview)})",
        f"  Player.bin players without assignment: "
        f"{_count(report.playerbin_players_missing_from_assignment)} "
        f"({_preview_text(report.playerbin_players_missing_from_assignment_preview)})",
        f"  Assignment team keys missing from Team.bin: "
        f"{_count(report.assignment_team_keys_missing_from_teambin)} "
        f"({_preview_text(report.assignment_team_keys_missing_from_teambin_preview)})",
        f"  Duplicate assignment pairs: {_count(report.duplicate_assignment_pairs)} "
        f"({_preview_text(report.duplicate_assignment_pairs_preview)})",
        f"  Players with multiple assignments: "
        f"{_count(report.multi_assignment_players)} "
        f"({_preview_text(report.multi_assignment_players_preview)})",
        f"  Assignment rows with zero player/team: "
        f"{_count(report.assignment_zero_player_rows)}/"
        f"{_count(report.assignment_zero_team_rows)}",
        "",
        "Player.bin fields:",
        f"  Expired contracts: {_count(report.contract_expired_players)} "
        f"({_preview_text(report.contract_expired_players_preview)})",
        f"  Invalid contract dates: {_count(report.invalid_contract_players)} "
        f"({_preview_text(report.invalid_contract_players_preview)})",
        f"  Loan records: {_count(report.loan_players)} "
        f"({_preview_text(report.loan_players_preview)})",
        f"  Owner-team metadata: {_count(report.owner_team_players)}",
        f"  Non-zero market values: {_count(report.market_value_players)}",
        f"  Players with caps: {_count(report.caps_players)}",
    ]
    if report.missing_sources:
        lines.extend(["", "Missing sources: " + ", ".join(report.missing_sources)])
    return "\n".join(lines)


__all__ = ("MetadataAuditReport", "audit_metadata", "format_metadata_audit")
