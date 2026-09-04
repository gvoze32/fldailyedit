from __future__ import annotations

import json
from pathlib import Path

import pytest


from editor.metadata_diff import (
    compare_metadata_variants,
    format_metadata_variant_diff,
)
from editor.player_assignment import PlayerAssignmentDatabase, PlayerAssignmentRecord
from editor.playerbin import PlayerBinDatabase, PlayerBinRecord
from editor.teambin import TeamBinDatabase, TeamBinRecord


def _player(player_id: int, name: str, position: str = "CF") -> PlayerBinRecord:
    return PlayerBinRecord(
        player_id=player_id,
        name=name,
        age=20,
        registered_position=position,
        market_value_eur=100,
        print_name=name,
    )


def test_compare_metadata_variants_reports_changed_and_added_native_records():
    report = compare_metadata_variants(
        "left.cpk",
        PlayerBinDatabase((_player(1, "One"), _player(2, "Two"))),
        TeamBinDatabase((TeamBinRecord(10, "Left Club", "LFT"),)),
        PlayerAssignmentDatabase(
            (
                PlayerAssignmentRecord(1, 1, 10, 0),
                PlayerAssignmentRecord(1, 1, 10, 0),
            )
        ),
        "right.cpk",
        PlayerBinDatabase((_player(1, "Renamed"), _player(3, "Three"))),
        TeamBinDatabase(
            (
                TeamBinRecord(10, "Left Club", "LFT"),
                TeamBinRecord(11, "New", "NEW"),
            )
        ),
        PlayerAssignmentDatabase(
            (
                PlayerAssignmentRecord(1, 1, 10, 0),
                PlayerAssignmentRecord(2, 3, 11, 0),
            )
        ),
    )

    player, team, assignment = report.databases
    assert (
        player.changed_entries,
        player.only_left_entries,
        player.only_right_entries,
    ) == (1, 1, 1)
    assert (
        team.identical_entries,
        team.only_left_entries,
        team.only_right_entries,
    ) == (1, 0, 1)
    assert assignment.left_entries == 2
    assert assignment.right_entries == 2
    assert assignment.identical_entries == 1
    assert assignment.only_left_entries == 1
    assert assignment.only_right_entries == 1
    assert assignment.changed_entries == 1
    assert "Player.bin" in format_metadata_variant_diff(report)
    assert report.to_dict()["databases"][0]["changed_preview"] == ("1",)


def test_native_transfer_metadata_payload_is_bounded_and_json_safe():
    from types import SimpleNamespace

    import run_pipeline

    edit_file = SimpleNamespace(
        playerbin_db=PlayerBinDatabase(
            (_player(162196, "Native Player", "RB"),)
        ),
        playerbin_source="fixture::Player.bin",
        player_assignment_db=PlayerAssignmentDatabase(
            (PlayerAssignmentRecord(1, 162196, 10, 0),)
        ),
        player_assignment_source="fixture::PlayerAssignment.bin",
        teambin_db=TeamBinDatabase((TeamBinRecord(10, "Native Club", "NAT"),)),
    )

    payload = run_pipeline._native_transfer_metadata(edit_file, 162196)

    assert payload["player_bin"]["found"] is True
    assert payload["player_bin"]["registered_position"] == "RB"
    assert payload["player_assignment"]["teams"] == [
        {"team_key": 10, "name": "Native Club", "abbreviation": "NAT"}
    ]


def test_compare_cli_emits_bounded_json(monkeypatch, tmp_path, capsys):
    import run

    left_path = tmp_path / "left.cpk"
    right_path = tmp_path / "right.cpk"
    left_path.write_bytes(b"fixture")
    right_path.write_bytes(b"fixture")
    databases = (
        PlayerBinDatabase((_player(1, "One"),)),
        TeamBinDatabase((TeamBinRecord(10, "Club", "CLB"),)),
        PlayerAssignmentDatabase((PlayerAssignmentRecord(1, 1, 10, 0),)),
    )
    monkeypatch.setattr(
        run,
        "_load_metadata_variant_from_cpk",
        lambda _path: databases,
    )
    monkeypatch.setattr(
        run.sys,
        "argv",
        [
            "run.py",
            "compare",
            "--left-cpk",
            str(left_path),
            "--right-cpk",
            str(right_path),
            "--json",
        ],
    )

    run.main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["left_source"] == str(left_path)
    assert payload["right_source"] == str(right_path)
    assert [item["database"] for item in payload["databases"]] == [
        "Player.bin",
        "Team.bin",
        "PlayerAssignment.bin",
    ]


@pytest.mark.skipif(
    not (
        Path("reference/data_s2526.cpk").exists()
        and Path("reference/download/data_extra.cpk").exists()
    ),
    reason="reference CPK variants are not available",
)
def test_reference_cpk_diff_matches_documented_semantic_contract():
    import run

    left_path = Path("reference/data_s2526.cpk")
    right_path = Path("reference/download/data_extra.cpk")
    left = run._load_metadata_variant_from_cpk(left_path)
    right = run._load_metadata_variant_from_cpk(right_path)
    report = compare_metadata_variants(
        str(left_path),
        *left,
        str(right_path),
        *right,
    )

    player, team, assignment = report.databases
    assert (
        player.left_entries,
        player.right_entries,
        player.identical_entries,
        player.changed_entries,
        player.only_left_entries,
        player.only_right_entries,
    ) == (27_840, 29_492, 10_054, 17_786, 0, 1_652)
    assert (
        team.left_entries,
        team.right_entries,
        team.identical_entries,
        team.changed_entries,
        team.only_left_entries,
        team.only_right_entries,
    ) == (743, 749, 726, 17, 0, 6)
    assert (
        assignment.left_entries,
        assignment.right_entries,
        assignment.identical_entries,
        assignment.changed_entries,
        assignment.only_left_entries,
        assignment.only_right_entries,
    ) == (21_245, 21_364, 16_324, 0, 4_921, 5_040)
