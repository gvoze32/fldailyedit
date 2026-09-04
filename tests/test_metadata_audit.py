from __future__ import annotations

import json
from argparse import Namespace
from datetime import date

from editor.metadata_audit import audit_metadata
from editor.models import TeamData
from editor.player_assignment import (
    PlayerAssignmentDatabase,
    PlayerAssignmentRecord,
)
from editor.playerbin import PlayerBinDatabase, PlayerBinRecord
from editor.teambin import TeamBinDatabase, TeamBinRecord


class _RosterSource:
    def __init__(self, rosters: dict[int, TeamData]):
        self._rosters = rosters

    def get_all_rosters(self) -> dict[int, TeamData]:
        return self._rosters




def _databases() -> tuple[
    PlayerBinDatabase, TeamBinDatabase, PlayerAssignmentDatabase
]:
    players = PlayerBinDatabase(
        (
            PlayerBinRecord(
                1,
                "Player One",
                20,
                "CF",
                0,
                "ONE",
                0,
                10,
                20240101,
                20260101,
                5,
            ),
            PlayerBinRecord(2, "Player Two", 21, "CMF", 0, "TWO", contract_until=20261340),
            PlayerBinRecord(4, "Player Four", 22, "RB", 1000, "FOUR", contract_until=20270101),
        )
    )
    teams = TeamBinDatabase((TeamBinRecord(10, "Club Ten", "TEN"),))
    assignments = PlayerAssignmentDatabase(
        (
            PlayerAssignmentRecord(1, 1, 10, 0),
            PlayerAssignmentRecord(2, 1, 10, 0),
            PlayerAssignmentRecord(3, 2, 10, 0),
            PlayerAssignmentRecord(4, 2, 11, 0),
            PlayerAssignmentRecord(5, 99, 10, 0),
            PlayerAssignmentRecord(6, 0, 10, 0),
            PlayerAssignmentRecord(7, 3, 0, 0),
        )
    )
    return players, teams, assignments


def test_audit_metadata_reports_bounded_consistency_and_contract_diagnostics():
    players, teams, assignments = _databases()
    source = _RosterSource(
        {
            101: TeamData(101, [1, 2, 3, 0], [1, 2, 3, 0]),
        }
    )

    report = audit_metadata(
        source,
        players,
        teams,
        assignments,
        as_of=date(2025, 1, 1),
    )

    assert report.save_players_missing_from_playerbin == 1
    assert report.save_players_missing_from_playerbin_preview == (3,)
    assert report.assignment_players_missing_from_playerbin == 2
    assert report.assignment_players_missing_from_playerbin_preview == (3, 99)
    assert report.playerbin_players_missing_from_assignment == 1
    assert report.playerbin_players_missing_from_assignment_preview == (4,)
    assert report.assignment_team_keys_missing_from_teambin == 1
    assert report.assignment_team_keys_missing_from_teambin_preview == (11,)
    assert report.duplicate_assignment_pairs == 1
    assert report.duplicate_assignment_pairs_preview == ((1, 10),)
    assert report.multi_assignment_players == 1
    assert report.multi_assignment_players_preview == (2,)
    assert report.assignment_zero_player_rows == 1
    assert report.assignment_zero_team_rows == 1
    assert report.contract_expired_players == 1
    assert report.contract_expired_players_preview == (1,)
    assert report.invalid_contract_players == 1
    assert report.invalid_contract_players_preview == (2,)
    assert report.loan_players == 1
    assert report.owner_team_players == 1
    assert report.market_value_players == 1
    assert report.caps_players == 1
    json.dumps(report.to_dict(), sort_keys=True)


def test_audit_metadata_marks_missing_sources_without_failing_closed_report():
    source = _RosterSource({101: TeamData(101, [1], [1])})

    report = audit_metadata(source, None)

    assert report.save_roster_players == 1
    assert report.missing_sources == (
        "Player.bin",
        "Team.bin",
        "PlayerAssignment.bin",
    )
    assert report.playerbin_entries is None
    assert report.save_players_missing_from_playerbin is None
    assert report.assignment_players is None


def test_audit_cli_emits_machine_readable_sources_and_report(
    monkeypatch, tmp_path, capsys
):
    import run
    from tests.test_editor import _build_mock_data

    edit_path = tmp_path / "EDIT00000000"
    edit_path.write_bytes(b"encrypted")
    decrypted = tmp_path / "decrypted"
    decrypted.mkdir()
    (decrypted / "data.dat").write_bytes(
        _build_mock_data(
            num_players=3,
            num_teams=1,
            num_team_player=1,
            num_game_plans=1,
            team_player_entries=[(101, [1, 2, 3], [1, 2, 3])],
        )
    )
    players, teams, assignments = _databases()
    monkeypatch.setattr(run.crypto, "decrypt", lambda _path: decrypted)
    monkeypatch.setattr(run.crypto, "cleanup_temp", lambda _path: None)
    monkeypatch.setattr(
        run,
        "_load_playerbin_database",
        lambda *args, **kwargs: (players, "fixture::Player.bin"),
    )
    monkeypatch.setattr(
        run,
        "_load_teambin_database",
        lambda *args, **kwargs: (teams, "fixture::Team.bin"),
    )
    monkeypatch.setattr(
        run,
        "_load_player_assignment_database",
        lambda *args, **kwargs: (assignments, "fixture::PlayerAssignment.bin"),
    )
    monkeypatch.setattr(
        run.sys,
        "argv",
        [
            "run.py",
            "audit",
            "--edit-file",
            str(edit_path),
            "--as-of",
            "2025-01-01",
            "--json",
        ],
    )

    run.main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["as_of"] == "2025-01-01"
    assert payload["save_roster_players"] == 3
    assert payload["sources"] == {
        "Player.bin": "fixture::Player.bin",
        "Team.bin": "fixture::Team.bin",
        "PlayerAssignment.bin": "fixture::PlayerAssignment.bin",
    }
