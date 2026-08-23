"""Read-only checks that reviewed player specs match one edit-file roster."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from typing import Any, Iterable, Protocol

from editor.models import TeamData, TeamInfo
from editor.player_spec import PlayerSpec


class BaseRosterSource(Protocol):
    def get_all_rosters(self) -> dict[int, TeamData]: ...

    def get_all_team_info(self) -> dict[int, TeamInfo]: ...

    def get_all_players(self) -> dict[int, Any]: ...


@dataclass(frozen=True, slots=True)
class BaseRosterFinding:
    path: str
    name: str
    operation: str
    player_id: int
    status: str
    expected_team_id: int | None
    expected_team_name: str | None
    actual_team_ids: tuple[int, ...]
    loan_parent_team_id: int | None
    loan_parent_team_name: str | None
    loan_parent_status: str | None
    loan_status: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class BaseRosterAuditReport:
    findings: tuple[BaseRosterFinding, ...]

    @property
    def issue_count(self) -> int:
        return sum(
            finding.status != "present"
            or finding.loan_parent_status == "missing"
            or finding.loan_status == "expired"
            for finding in self.findings
        )

    @property
    def valid(self) -> bool:
        return self.issue_count == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "issue_count": self.issue_count,
            "findings": [finding.to_dict() for finding in self.findings],
        }


def audit_base_roster(
    edit_file: BaseRosterSource,
    specs: Iterable[PlayerSpec],
    *,
    as_of: date | None = None,
) -> BaseRosterAuditReport:
    """Check active Player Updates against actual base roster ownership."""
    report_date = date.today() if as_of is None else as_of

    rosters = edit_file.get_all_rosters()
    teams = edit_file.get_all_team_info()
    players = edit_file.get_all_players()
    actual_teams: dict[int, tuple[int, ...]] = {}
    for team_id, roster in rosters.items():
        for player_id in roster.player_ids:
            if player_id:
                actual_teams[player_id] = tuple(
                    (*actual_teams.get(player_id, ()), team_id)
                )

    findings: list[BaseRosterFinding] = []
    for spec in sorted(specs, key=lambda item: item.path.name):
        if spec.lifecycle_status != "active":
            continue
        player_id = spec.identity.pes_id
        registered = tuple(sorted(actual_teams.get(player_id, ())))
        create = spec.create
        expected_team_id = create.team_id if create is not None else None
        expected_team_name = create.team_name if create is not None else None
        if not registered:
            status = "missing"
        elif expected_team_id is not None and registered != (expected_team_id,):
            status = "wrong_team"
        elif player_id not in players:
            status = "missing_player_metadata"
        else:
            status = "present"

        loan_parent_team_id = None
        loan_parent_team_name = None
        loan_parent_status = None
        loan_status = None
        if spec.loan is not None:
            loan_parent_team_id = spec.loan.parent_team_id
            loan_parent_team_name = spec.loan.parent_team_name
            parent = teams.get(loan_parent_team_id)
            loan_parent_status = (
                "present"
                if parent is not None and parent.name == loan_parent_team_name
                else "missing"
            )
            if report_date < spec.loan.start_date:
                loan_status = "future"
            elif report_date > spec.loan.end_date:
                loan_status = "expired"
            else:
                loan_status = "active"

        findings.append(
            BaseRosterFinding(
                path=str(spec.path),
                name=spec.identity.name,
                operation=spec.operation,
                player_id=player_id,
                status=status,
                expected_team_id=expected_team_id,
                expected_team_name=expected_team_name,
                actual_team_ids=registered,
                loan_parent_team_id=loan_parent_team_id,
                loan_parent_team_name=loan_parent_team_name,
                loan_parent_status=loan_parent_status,
                loan_status=loan_status,
            )
        )
    return BaseRosterAuditReport(tuple(findings))


def format_base_roster_audit(report: BaseRosterAuditReport) -> str:
    lines = [
        "Base roster audit: " + ("PASS" if report.valid else "ATTENTION"),
        f"  Active specs checked: {len(report.findings)}",
        f"  Issues: {report.issue_count}",
    ]
    for finding in report.findings:
        actual = ",".join(str(team_id) for team_id in finding.actual_team_ids) or "none"
        expected = (
            f"{finding.expected_team_name} ({finding.expected_team_id})"
            if finding.expected_team_id is not None
            else "existing roster"
        )
        loan = ""
        if finding.loan_parent_team_id is not None:
            loan = (
                f"; loan parent={finding.loan_parent_team_name}"
                f" ({finding.loan_parent_status}, {finding.loan_status})"
            )
        lines.append(
            f"  {finding.name} [{finding.operation}] {finding.status}: "
            f"expected={expected}; actual={actual}{loan}"
        )
    return "\n".join(lines)


__all__ = (
    "BaseRosterAuditReport",
    "BaseRosterFinding",
    "audit_base_roster",
    "format_base_roster_audit",
)
