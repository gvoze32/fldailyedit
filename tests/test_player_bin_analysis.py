from __future__ import annotations

import struct
from pathlib import Path

import config
import pytest

from editor.editfile import PE_REGISTERED_POSITION_BYTE, EditFile
from editor.roster import (
    GP_ATTACK_PLAYERS,
    GP_CAPTAIN,
    GP_LEFT_CK,
    GP_LINEUP,
    GP_PK,
    GP_POSITION_PRESETS,
    GP_POSITION_PHASE_OFFSETS,
    GP_RIGHT_CK,
)
from editor.models import PlayerInfo
from editor.player_assignment import (
    PlayerAssignmentDatabase,
    PlayerAssignmentRecord,
)
from editor.playerbin import (
    POSITION_NAMES,
    RECORD_SIZE,
    PlayerBinDatabase,
    PlayerBinRecord,
)
from editor.teambin import TeamBinDatabase, TeamBinRecord
from tools.cpk_extract import extract_file, extract_game_databases, list_files


CPK_PATH = Path("reference/data_s2526.cpk")
EXTRA_CPK_PATH = Path("reference/download/data_extra.cpk")
T99_CPK_PATH = Path("reference/T99 Patch V10/t99p21_v10_liveupd.cpk")




@pytest.mark.skipif(not CPK_PATH.exists(), reason="CPK fixture is not available")
def test_cpk_extracts_and_indexes_player_bin(tmp_path: Path):
    files = list_files(CPK_PATH)
    assert len(files) == 20
    assert any(name.endswith("/Player.bin") for name in files)
    output = tmp_path / "Player.bin"
    assert extract_file(CPK_PATH, "Player.bin", output) > 1_000_000

    database = PlayerBinDatabase.load(output)
    assert len(database) > 25_000
    assert database.get(162196) == PlayerBinRecord(
        player_id=162196,
        name="Marco Palestra",
        age=20,
        registered_position="RB",
        market_value_eur=0,
        print_name="PALESTRA",
        youth_team_id=0,
        owner_team_key=0,
        contract_until=20250630,
        loan_until=0,
        caps=0,
    )
@pytest.mark.skipif(
    not T99_CPK_PATH.exists(), reason="T99 live update CPK fixture is not available"
)
def test_t99_mode5_cpk_extracts_native_databases(tmp_path: Path):
    files = list_files(T99_CPK_PATH)
    assert "common/etc/pesdb/Player.bin" in files
    assert "common/etc/pesdb/Team.bin" in files
    assert "common/etc/pesdb/PlayerAssignment.bin" in files

    player_path = tmp_path / "Player.bin"
    team_path = tmp_path / "Team.bin"
    assignment_path = tmp_path / "PlayerAssignment.bin"
    assert extract_file(T99_CPK_PATH, "Player.bin", player_path) > 1_000_000
    assert extract_file(T99_CPK_PATH, "Team.bin", team_path) > 10_000
    assert (
        extract_file(
            T99_CPK_PATH,
            "PlayerAssignment.bin",
            assignment_path,
        )
        > 100_000
    )

    players = PlayerBinDatabase.load(player_path)
    teams = TeamBinDatabase.load(team_path)
    assignments = PlayerAssignmentDatabase.load(assignment_path)
    assert len(players) == 21_962
    assert len(teams) == 698
    assert len(assignments) == 21_172
    assert players.get(38439) is not None


def test_playerbin_invalid_position_is_not_misclassified_as_goalkeeper():
    raw = bytearray(RECORD_SIZE)
    struct.pack_into("<I", raw, 0x08, 999999)
    raw[0x33] = 5
    raw[0x36] = 15 << 2
    raw[0x44 : 0x44 + len(b"Unknown Position")] = b"Unknown Position"

    record = PlayerBinDatabase.from_bytes(raw).get(999999)

    assert record is not None
    assert record.registered_position == "UNKNOWN(15)"

@pytest.mark.skipif(
    not EXTRA_CPK_PATH.exists(), reason="data_extra.cpk fixture is not available"
)
def test_data_extra_indexes_team_and_player_assignments(tmp_path: Path):
    team_path = tmp_path / "Team.bin"
    assignment_path = tmp_path / "PlayerAssignment.bin"
    assert extract_file(EXTRA_CPK_PATH, "Team.bin", team_path) > 10_000
    assert (
        extract_file(
            EXTRA_CPK_PATH,
            "PlayerAssignment.bin",
            assignment_path,
        )
        > 100_000
    )

    teams = TeamBinDatabase.load(team_path)
    assignments = PlayerAssignmentDatabase.load(assignment_path)

    assert len(teams) == 749
    assert teams.get(320) == TeamBinRecord(320, "Cagliari Calcio", "CAG")
    assert len(assignments) == 21_364
    assert assignments.player_count == 19_415
    assert assignments.team_keys_for(162196) == (12, 320)
    assert assignments.team_keys_for(1073003) == ()


def test_edit_file_attaches_team_and_assignment_metadata():
    edit_file = EditFile()
    edit_file.attach_playerbin(
        PlayerBinDatabase(
            {162196: PlayerBinRecord(162196, "Palestra", 20, "RB", 0, "PALESTRA")}
        )
    )
    edit_file._player_cache = {162196: PlayerInfo(162196, "", "")}

    teams = TeamBinDatabase((TeamBinRecord(320, "Cagliari Calcio", "CAG"),))
    assignments = PlayerAssignmentDatabase(
        (PlayerAssignmentRecord(1, 162196, 320, 0),)
    )

    edit_file.attach_teambin(teams)
    edit_file.attach_player_assignment(assignments)

    assert edit_file.get_master_team(320) == TeamBinRecord(320, "Cagliari Calcio", "CAG")
    assert edit_file.get_player_assignment_teams(162196) == (320,)
    assert edit_file._player_metadata(162196).print_name == "PALESTRA"
    with pytest.raises(TypeError):
        edit_file.attach_teambin(PlayerAssignmentDatabase(()))


@pytest.mark.skipif(
    not (CPK_PATH.exists() and EXTRA_CPK_PATH.exists()),
    reason="game database CPK fixtures are not available",
)
def test_extract_game_databases_reads_all_supported_cpk_members(tmp_path: Path):
    game_root = tmp_path / "Football Life 2026"
    download = game_root / "download"
    download.mkdir(parents=True)
    (download / "data_s2526.cpk").symlink_to(CPK_PATH.resolve())
    (download / "data_extra.cpk").symlink_to(EXTRA_CPK_PATH.resolve())

    extracted = extract_game_databases(game_root, tmp_path / "metadata")

    assert set(extracted) == {
        "Player.bin",
        "PlayerAssignment.bin",
        "Team.bin",
    }
    assert len(PlayerBinDatabase.load(extracted["Player.bin"])) > 25_000
    assert len(TeamBinDatabase.load(extracted["Team.bin"])) == 743
    assert len(PlayerAssignmentDatabase.load(extracted["PlayerAssignment.bin"])) == 21_245




def test_playerbin_position_protects_unedited_goalkeeper_from_overflow_release():
    from tests.test_editor import _build_mock_data

    data = _build_mock_data(
        num_players=40,
        num_teams=1,
        num_team_player=1,
        num_game_plans=1,
        team_player_entries=[(101, list(range(1000, 1040)), list(range(1, 41)))],
    )
    edit_file = EditFile()
    edit_file.load_bytes(data)
    edit_file._player_cache = {
        1000 + index: PlayerInfo(
            player_id=1000 + index,
            name=f"P{index}",
        )
        for index in range(40)
    }
    edit_file.attach_playerbin(
        PlayerBinDatabase(
            {
                1025: PlayerBinRecord(1025, "Backup GK", 24, "GK", 0),
            }
        )
    )

    slot, player_id = edit_file.find_overflow_release_candidate(101)
    assert (slot, player_id) == (39, 1039)

def test_playerbin_only_metadata_still_allows_role_based_overflow_release():
    from tests.test_editor import _build_mock_data

    data = _build_mock_data(
        num_players=40,
        num_teams=1,
        num_team_player=1,
        num_game_plans=1,
        team_player_entries=[(101, list(range(1000, 1040)), list(range(1, 41)))],
    )
    edit_file = EditFile()
    edit_file.load_bytes(data)
    edit_file.attach_playerbin(PlayerBinDatabase({}))

    assert edit_file.find_overflow_release_candidate(101) == (39, 1039)


def test_game_plan_compaction_keeps_replacement_at_removed_ordinal():
    from tests.test_editor import _build_mock_data

    data = _build_mock_data(
        num_players=40,
        num_teams=1,
        num_team_player=1,
        num_game_plans=1,
        team_player_entries=[(128, list(range(1000, 1040)), list(range(1, 41)))],
    )
    edit_file = EditFile()
    edit_file.load_bytes(data)
    lineup_offset = edit_file.game_plan_start + GP_LINEUP
    lineup = [1, 4, 6, 5, 14, 15, 16, 34, 23, 17, 24]
    lineup += [39]
    lineup += [slot for slot in range(40) if slot not in lineup]
    edit_file._data[lineup_offset : lineup_offset + 40] = bytes(lineup)

    assert edit_file.release_player(1034, 128)
    assert list(edit_file._data[lineup_offset : lineup_offset + 11]) == [
        1, 4, 6, 5, 14, 15, 16, 34, 23, 17, 24
    ]


def test_game_plan_compaction_updates_role_ordinals():
    from tests.test_editor import _build_mock_data

    data = _build_mock_data(
        num_players=40,
        num_teams=1,
        num_team_player=1,
        num_game_plans=1,
        team_player_entries=[(128, list(range(1000, 1040)), list(range(1, 41)))],
    )
    edit_file = EditFile()
    edit_file.load_bytes(data)
    game_plan_offset = edit_file.game_plan_start
    edit_file._data[game_plan_offset + GP_LEFT_CK] = 34
    edit_file._data[game_plan_offset + GP_RIGHT_CK] = 39
    edit_file._data[game_plan_offset + GP_PK] = 39
    edit_file._data[game_plan_offset + GP_CAPTAIN] = 34
    edit_file._data[
        game_plan_offset + GP_ATTACK_PLAYERS : game_plan_offset + GP_ATTACK_PLAYERS + 3
    ] = bytes((34, 39, 7))

    assert edit_file.release_player(1034, 128)

    assert edit_file._data[game_plan_offset + GP_LEFT_CK] == 0xFF
    assert edit_file._data[game_plan_offset + GP_RIGHT_CK] == 34
    assert edit_file._data[game_plan_offset + GP_PK] == 34
    assert edit_file._data[game_plan_offset + GP_CAPTAIN] == 0xFF
    assert list(
        edit_file._data[
            game_plan_offset + GP_ATTACK_PLAYERS : game_plan_offset + GP_ATTACK_PLAYERS + 3
        ]
    ) == [0xFF, 34, 7]


def test_integrity_reports_goalkeeper_assigned_to_outfield_position():
    from tests.test_editor import _build_mock_data

    data = _build_mock_data(
        num_players=1,
        num_teams=1,
        num_team_player=1,
        num_game_plans=1,
        team_player_entries=[
            (101, [162196] + list(range(2000, 2015)), list(range(1, 17)))
        ],
        league_team_ids=[101],
    )
    edit_file = EditFile()
    edit_file.load_bytes(data)
    edit_file.attach_playerbin(
        PlayerBinDatabase({162196: PlayerBinRecord(162196, "Marco", 20, "GK", 0)})
    )
    game_plan_offset = edit_file.game_plan_start
    edit_file._data[game_plan_offset + GP_LINEUP : game_plan_offset + GP_LINEUP + 40] = bytes(
        range(40)
    )
    for preset_offset in GP_POSITION_PRESETS:
        edit_file._data[game_plan_offset + preset_offset] = 1

    report = edit_file.validate_integrity()
    assert sum("assigns GK" in error for error in report["errors"]) == 3


def test_integrity_reads_position_preset_by_lineup_role():
    from tests.test_editor import _build_mock_data

    data = _build_mock_data(
        num_players=16,
        num_teams=1,
        num_team_player=1,
        num_game_plans=1,
        team_player_entries=[
            (101, [2000, 162196] + list(range(2001, 2015)), list(range(1, 17)))
        ],
        league_team_ids=[101],
    )
    edit_file = EditFile()
    edit_file.load_bytes(data)
    edit_file.attach_playerbin(
        PlayerBinDatabase({162196: PlayerBinRecord(162196, "Marco", 20, "GK", 0)})
    )
    game_plan_offset = edit_file.game_plan_start
    edit_file._data[
        game_plan_offset + GP_LINEUP : game_plan_offset + GP_LINEUP + 40
    ] = bytes([1, 0] + list(range(2, 40)))
    for preset_offset in GP_POSITION_PRESETS:
        edit_file._data[game_plan_offset + preset_offset + 4] = 1

    report = edit_file.validate_integrity()

    assert not any("assigns GK" in error for error in report["errors"])

def test_removal_repairs_goalkeeper_position_bytes_after_slot_compaction():
    from tests.test_editor import _build_mock_data

    data = _build_mock_data(
        num_players=40,
        num_teams=1,
        num_team_player=1,
        num_game_plans=1,
        team_player_entries=[
            (101, list(range(1000, 1040)), list(range(1, 41))),
        ],
        league_team_ids=[101],
    )
    edit_file = EditFile()
    edit_file.load_bytes(data)
    edit_file.attach_playerbin(
        PlayerBinDatabase(
            {
                1039: PlayerBinRecord(1039, "Backup GK", 24, "GK", 0),
            }
        )
    )
    edit_file._player_cache = {
        1001: PlayerInfo(1001, "Starter", position="CB"),
        1039: PlayerInfo(1039, "Backup GK", position="GK"),
    }
    game_plan_offset = edit_file.game_plan_start
    for preset_offset in GP_POSITION_PRESETS:
        for phase_offset in GP_POSITION_PHASE_OFFSETS:
            edit_file._data[
                game_plan_offset + preset_offset + phase_offset :
                game_plan_offset + preset_offset + phase_offset + 11
            ] = bytes([0, 1] + [1] * 9)

    before = edit_file.validate_integrity()
    assert before["valid"] is True

    assert edit_file.release_player(1001, 101)

    roster = edit_file.get_team_roster(101)
    assert roster is not None
    assert roster.player_ids[1] == 1039
    updated_lineup = list(
        edit_file._data[
            game_plan_offset + GP_LINEUP : game_plan_offset + GP_LINEUP + 40
        ]
    )
    assert updated_lineup[:2] == [1, 0]
    for preset_offset in GP_POSITION_PRESETS:
        for phase_offset in GP_POSITION_PHASE_OFFSETS:
            assert (
                edit_file._data[
                    game_plan_offset + preset_offset + phase_offset
                ]
                == 0
            )
    report = edit_file.validate_integrity()
    assert not any("assigns GK" in error for error in report["errors"])


def test_repair_game_plans_preserves_role_position_bytes_when_moving_goalkeeper():
    from tests.test_editor import _build_mock_data

    data = _build_mock_data(
        num_players=40,
        num_teams=1,
        num_team_player=1,
        num_game_plans=1,
        team_player_entries=[
            (101, list(range(1000, 1040)), list(range(1, 41))),
        ],
        league_team_ids=[101],
    )
    edit_file = EditFile()
    edit_file.load_bytes(data)
    edit_file.attach_playerbin(
        PlayerBinDatabase({1000: PlayerBinRecord(1000, "Starting GK", 24, "GK", 0)})
    )
    game_plan_offset = edit_file.game_plan_start
    lineup_offset = game_plan_offset + GP_LINEUP
    edit_file._data[lineup_offset : lineup_offset + 40] = bytes(
        [1, 0] + list(range(2, 40))
    )
    for preset_offset in GP_POSITION_PRESETS:
        for phase_offset in GP_POSITION_PHASE_OFFSETS:
            edit_file._data[
                game_plan_offset + preset_offset + phase_offset :
                game_plan_offset + preset_offset + phase_offset + 11
            ] = bytes([0, 12] + [1] * 9)

    metrics = edit_file.repair_game_plans()
    lineup = list(edit_file._data[lineup_offset : lineup_offset + 11])

    assert lineup[:2] == [0, 1]
    assert metrics["repaired_goalkeeper_roles"] == 1
    assert metrics["repaired_position_bytes"] == 0
    for preset_offset in GP_POSITION_PRESETS:
        for phase_offset in GP_POSITION_PHASE_OFFSETS:
            assert edit_file._data[
                game_plan_offset + preset_offset + phase_offset
            ] == 0
            assert edit_file._data[
                game_plan_offset + preset_offset + phase_offset + 1
            ] == 12
    assert edit_file.validate_integrity()["valid"] is True


def test_repair_game_plans_moves_extra_goalkeeper_to_bench_without_relabeling_roles():
    from tests.test_editor import _build_mock_data

    data = _build_mock_data(
        num_players=40,
        num_teams=1,
        num_team_player=1,
        num_game_plans=1,
        team_player_entries=[
            (101, list(range(1000, 1040)), list(range(1, 41))),
        ],
        league_team_ids=[101],
    )
    edit_file = EditFile()
    edit_file.load_bytes(data)
    edit_file.attach_playerbin(
        PlayerBinDatabase(
            {
                1000: PlayerBinRecord(1000, "Starting GK", 24, "GK", 0),
                1008: PlayerBinRecord(1008, "Extra GK", 24, "GK", 0),
                1011: PlayerBinRecord(1011, "Reserve CF", 24, "CF", 0),
            }
        )
    )
    game_plan_offset = edit_file.game_plan_start
    lineup_offset = game_plan_offset + GP_LINEUP
    edit_file._data[lineup_offset : lineup_offset + 40] = bytes(range(40))
    before_positions = []
    for preset_offset in GP_POSITION_PRESETS:
        for phase_offset in GP_POSITION_PHASE_OFFSETS:
            positions = bytes([0, 1, 1, 1, 1, 1, 1, 1, 10, 9, 12])
            position_offset = game_plan_offset + preset_offset + phase_offset
            edit_file._data[position_offset : position_offset + 11] = positions
            before_positions.append(positions)

    metrics = edit_file.repair_game_plans()
    lineup = list(edit_file._data[lineup_offset : lineup_offset + 40])

    assert lineup[:12] == [0, 1, 2, 3, 4, 5, 6, 7, 11, 9, 10, 8]
    assert lineup[11] == 8
    assert metrics["repaired_goalkeeper_roles"] == 1
    position_index = 0
    for preset_offset in GP_POSITION_PRESETS:
        for phase_offset in GP_POSITION_PHASE_OFFSETS:
            position_offset = game_plan_offset + preset_offset + phase_offset
            assert (
                bytes(edit_file._data[position_offset : position_offset + 11])
                == before_positions[position_index]
            )
            position_index += 1


def test_removal_promotes_player_matching_vacated_role_position():
    from tests.test_editor import _build_mock_data

    data = _build_mock_data(
        num_players=40,
        num_teams=1,
        num_team_player=1,
        num_game_plans=1,
        team_player_entries=[
            (101, list(range(1000, 1040)), list(range(1, 41))),
        ],
        league_team_ids=[101],
    )
    edit_file = EditFile()
    edit_file.load_bytes(data)
    edit_file.attach_playerbin(
        PlayerBinDatabase(
            {
                1001: PlayerBinRecord(1001, "Departed CF", 24, "CF", 0),
                1038: PlayerBinRecord(1038, "Reserve CF", 24, "CF", 0),
                1039: PlayerBinRecord(1039, "Marco Palestra", 20, "RB", 0),
            }
        )
    )
    game_plan_offset = edit_file.game_plan_start
    for preset_offset in GP_POSITION_PRESETS:
        for phase_offset in GP_POSITION_PHASE_OFFSETS:
            edit_file._data[
                game_plan_offset + preset_offset + phase_offset :
                game_plan_offset + preset_offset + phase_offset + 11
            ] = bytes([0, 12] + [1] * 9)

    assert edit_file.release_player(1001, 101)

    roster = edit_file.get_team_roster(101)
    assert roster is not None
    lineup = list(
        edit_file._data[
            game_plan_offset + GP_LINEUP :
            game_plan_offset + GP_LINEUP + 40
        ]
    )
    assert roster.player_ids[1] == 1039
    assert roster.player_ids[38] == 1038
    assert lineup[1] == 38
    assert lineup.index(1) == 38
    for preset_offset in GP_POSITION_PRESETS:
        for phase_offset in GP_POSITION_PHASE_OFFSETS:
            assert edit_file._data[
                game_plan_offset + preset_offset + phase_offset + 1
            ] == 12



def test_promotion_uses_native_positions_over_stale_save_labels():
    from tests.test_editor import _build_mock_data

    data = _build_mock_data(
        num_players=0,
        num_teams=1,
        num_team_player=1,
        num_game_plans=1,
        team_player_entries=[
            (101, list(range(1000, 1011)) + [143196, 148009], list(range(1, 14)))
        ],
        league_team_ids=[101],
    )
    edit_file = EditFile()
    edit_file.load_bytes(data)
    edit_file._player_cache = {
        143196: PlayerInfo(143196, "Moisés Caicedo", position="AMF"),
        148009: PlayerInfo(148009, "Cristhian Mosquera", position="AMF"),
    }
    edit_file.attach_playerbin(
        PlayerBinDatabase(
            {
                143196: PlayerBinRecord(143196, "Moisés Caicedo", 24, "DMF", 0),
                148009: PlayerBinRecord(148009, "Cristhian Mosquera", 21, "CB", 0),
            }
        )
    )

    assert edit_file._select_game_plan_promotion_slot(
        101,
        [11, 12],
        1000,
        1,
        target_position_code=POSITION_NAMES.index("CB"),
    ) == 12
    assert edit_file._select_game_plan_promotion_slot(
        101,
        [11, 12],
        1000,
        1,
        target_position_code=POSITION_NAMES.index("DMF"),
    ) == 11


@pytest.mark.parametrize(
    ("player_id", "native_position", "supplied_position", "expected_role"),
    [
        (151751, "GK", "AMF", 0),
        (138156, "AMF", "DMF", 10),
        (143196, "DMF", "AMF", 10),
        (117087, "RWF", "AMF", 10),
        (162196, "RB", "CF", 10),
        (148009, "CB", "ST", 10),
    ],
    ids=("Penders", "Palmer", "Caicedo", "Pedro-Neto", "Palestra", "Mosquera"),
)
def test_added_player_uses_native_position_for_changed_game_plan_role(
    player_id: int,
    native_position: str,
    supplied_position: str,
    expected_role: int,
):
    from tests.test_editor import _build_mock_data

    data = _build_mock_data(
        num_players=0,
        num_teams=1,
        num_team_player=1,
        num_game_plans=1,
        team_player_entries=[
            (101, list(range(1000, 1010)), list(range(1, 11))),
        ],
        league_team_ids=[101],
    )
    edit_file = EditFile()
    edit_file.load_bytes(data)
    edit_file.attach_playerbin(
        PlayerBinDatabase(
            {
                player_id: PlayerBinRecord(
                    player_id,
                    f"Native {player_id}",
                    20,
                    native_position,
                    0,
                )
            }
        )
    )
    game_plan_offset = edit_file.game_plan_start
    lineup_offset = game_plan_offset + GP_LINEUP
    stale_position_code = POSITION_NAMES.index("CF")
    for preset_offset in GP_POSITION_PRESETS:
        for phase_offset in GP_POSITION_PHASE_OFFSETS:
            edit_file._data[
                game_plan_offset + preset_offset + phase_offset + 10
            ] = stale_position_code

    assert edit_file.add_player(
        player_id,
        101,
        position=supplied_position,
    )

    lineup = list(edit_file._data[lineup_offset : lineup_offset + 11])
    assert lineup.index(10) == expected_role
    expected_position_code = POSITION_NAMES.index(native_position)
    for preset_offset in GP_POSITION_PRESETS:
        for phase_offset in GP_POSITION_PHASE_OFFSETS:
            assert edit_file._data[
                game_plan_offset
                + preset_offset
                + phase_offset
                + expected_role
            ] == expected_position_code


def test_native_playerbin_position_overrides_stale_save_position():
    from tests.test_editor import _build_mock_data

    data = _build_mock_data(
        num_players=1,
        num_teams=1,
        num_team_player=1,
        num_game_plans=1,
        player_entries=[(138156, "Cole Palmer")],
        team_player_entries=[
            (101, [138156], [10]),
        ],
        league_team_ids=[101],
    )
    edit_file = EditFile()
    edit_file.load_bytes(data)
    edit_file._data[
        edit_file.player_start + PE_REGISTERED_POSITION_BYTE
    ] = POSITION_NAMES.index("GK") << 5
    edit_file.attach_playerbin(
        PlayerBinDatabase(
            {
                138156: PlayerBinRecord(
                    138156,
                    "Cole Palmer",
                    23,
                    "AMF",
                    0,
                )
            }
        )
    )

    players = edit_file.get_all_players()

    assert players[138156].position == "AMF"
    assert edit_file.get_player_position(138156) == "AMF"
    assert edit_file._mutation_player_position(138156, "DMF") == "AMF"


def test_save_position_reads_four_bit_field_across_bytes():
    from tests.test_editor import _build_mock_data

    data = _build_mock_data(
        num_players=1,
        num_teams=1,
        num_team_player=1,
        num_game_plans=1,
        player_entries=[(138156, "Cole Palmer")],
        team_player_entries=[
            (101, [138156], [10]),
        ],
        league_team_ids=[101],
    )
    edit_file = EditFile()
    edit_file.load_bytes(data)
    position_offset = edit_file.player_start + PE_REGISTERED_POSITION_BYTE
    edit_file._data[position_offset : position_offset + 2] = (
        POSITION_NAMES.index("AMF") << 5
    ).to_bytes(2, "little")

    players = edit_file.get_all_players(include_base_db=False)

    assert players[138156].position == "AMF"


def test_removal_relabels_copied_player_with_native_position():
    from tests.test_editor import _build_mock_data

    data = _build_mock_data(
        num_players=0,
        num_teams=1,
        num_team_player=1,
        num_game_plans=1,
        team_player_entries=[
            (101, list(range(1000, 1010)) + [162196], list(range(1, 12))),
        ],
        league_team_ids=[101],
    )
    edit_file = EditFile()
    edit_file.load_bytes(data)
    edit_file.attach_playerbin(
        PlayerBinDatabase(
            {
                1001: PlayerBinRecord(1001, "Departed", 20, "CB", 0),
                162196: PlayerBinRecord(162196, "Marco Palestra", 20, "RB", 0),
            }
        )
    )
    game_plan_offset = edit_file.game_plan_start
    before_coordinates = []
    for preset_offset in GP_POSITION_PRESETS:
        for phase_offset in GP_POSITION_PHASE_OFFSETS:
            position_offset = (
                game_plan_offset + preset_offset + phase_offset
            )
            edit_file._data[position_offset + 1] = POSITION_NAMES.index("CB")
            edit_file._data[position_offset + 0x0B + 2] = 0x12
            edit_file._data[position_offset + 0x0B + 3] = 0x34
            before_coordinates.append(
                bytes(edit_file._data[position_offset + 0x0B + 2 : position_offset + 0x0B + 4])
            )

    assert edit_file.release_player(1001, 101)

    roster = edit_file.get_team_roster(101)
    assert roster is not None
    assert roster.player_ids[1] == 162196
    lineup_offset = game_plan_offset + GP_LINEUP
    assert edit_file._data[lineup_offset + 1] == 1
    after_coordinates = [
        bytes(
            edit_file._data[
                game_plan_offset + preset_offset + phase_offset + 0x0B + 2 :
                game_plan_offset + preset_offset + phase_offset + 0x0B + 4
            ]
        )
        for preset_offset in GP_POSITION_PRESETS
        for phase_offset in GP_POSITION_PHASE_OFFSETS
    ]
    assert after_coordinates == before_coordinates
    for preset_offset in GP_POSITION_PRESETS:
        for phase_offset in GP_POSITION_PHASE_OFFSETS:
            assert edit_file._data[
                game_plan_offset + preset_offset + phase_offset + 1
            ] == POSITION_NAMES.index("RB")


def test_move_relabels_copied_player_after_source_compaction():
    from tests.test_editor import _build_mock_data

    data = _build_mock_data(
        num_players=0,
        num_teams=2,
        num_team_player=2,
        num_game_plans=2,
        team_player_entries=[
            (101, list(range(1000, 1011)) + [162196], list(range(1, 13))),
            (102, [2000] + [0] * 39, [20] + [0] * 39),
        ],
        league_team_ids=[101, 102],
    )
    edit_file = EditFile()
    edit_file.load_bytes(data)
    edit_file.attach_playerbin(
        PlayerBinDatabase(
            {
                1001: PlayerBinRecord(1001, "Departed", 20, "CB", 0),
                162196: PlayerBinRecord(162196, "Marco Palestra", 20, "RB", 0),
                2000: PlayerBinRecord(2000, "Destination Player", 20, "CF", 0),
            }
        )
    )

    game_plan_offset = edit_file._find_game_plan_offset(101)
    assert game_plan_offset is not None
    for preset_offset in GP_POSITION_PRESETS:
        for phase_offset in GP_POSITION_PHASE_OFFSETS:
            position_offset = (
                game_plan_offset + preset_offset + phase_offset
            )
            edit_file._data[position_offset + 1] = POSITION_NAMES.index("CB")
            edit_file._data[position_offset + 0x0B + 2] = 0x12
            edit_file._data[position_offset + 0x0B + 3] = 0x34

    assert edit_file.move_player(1001, 101, 102, position="ST")

    roster = edit_file.get_team_roster(101)
    assert roster is not None
    assert roster.player_ids[1] == 162196
    lineup_offset = game_plan_offset + GP_LINEUP
    assert edit_file._data[lineup_offset + 1] == 1
    for preset_offset in GP_POSITION_PRESETS:
        for phase_offset in GP_POSITION_PHASE_OFFSETS:
            position_offset = (
                game_plan_offset + preset_offset + phase_offset
            )
            assert edit_file._data[position_offset + 1] == POSITION_NAMES.index("RB")
            assert bytes(
                edit_file._data[
                    position_offset + 0x0B + 2 : position_offset + 0x0B + 4
                ]
            ) == b"\x12\x34"


def test_reserve_compaction_keeps_copied_starter_role():
    from tests.test_editor import _build_mock_data

    data = _build_mock_data(
        num_players=0,
        num_teams=1,
        num_team_player=1,
        num_game_plans=1,
        team_player_entries=[
            (101, list(range(1000, 1016)), list(range(1, 17))),
        ],
        league_team_ids=[101],
    )
    edit_file = EditFile()
    edit_file.load_bytes(data)
    game_plan_offset = edit_file.game_plan_start
    lineup_offset = game_plan_offset + GP_LINEUP
    lineup = [0, 1, 2, 3, 4, 5, 6, 7, 8, 15, 10, 11, 12, 13, 9, 14]
    lineup.extend(range(16, 40))
    edit_file._data[lineup_offset : lineup_offset + 40] = bytes(lineup)

    assert edit_file.release_player(1014, 101)

    updated_lineup = list(edit_file._data[lineup_offset : lineup_offset + 40])
    assert updated_lineup[:15] == [
        0, 1, 2, 3, 4, 5, 6, 7, 8, 14, 10, 11, 12, 13, 9
    ]
    assert updated_lineup[15:] == list(range(16, 40)) + [15]


def test_removal_relabels_promoted_player_with_native_position():
    from tests.test_editor import _build_mock_data

    data = _build_mock_data(
        num_players=0,
        num_teams=1,
        num_team_player=1,
        num_game_plans=1,
        team_player_entries=[
            (101, list(range(1000, 1013)), list(range(1, 14))),
        ],
        league_team_ids=[101],
    )
    edit_file = EditFile()
    edit_file.load_bytes(data)
    edit_file.attach_playerbin(
        PlayerBinDatabase(
            {
                1001: PlayerBinRecord(1001, "Departed CB", 24, "CB", 0),
                1011: PlayerBinRecord(1011, "Marco Palestra", 20, "RB", 0),
                1012: PlayerBinRecord(1012, "Replacement", 24, "LWF", 0),
            }
        )
    )
    game_plan_offset = edit_file.game_plan_start
    for preset_offset in GP_POSITION_PRESETS:
        for phase_offset in GP_POSITION_PHASE_OFFSETS:
            edit_file._data[
                game_plan_offset + preset_offset + phase_offset + 1
            ] = POSITION_NAMES.index("CB")

    assert edit_file.release_player(1001, 101)

    roster = edit_file.get_team_roster(101)
    assert roster is not None
    assert roster.player_ids[1] == 1012
    assert roster.player_ids[11] == 1011
    lineup = list(
        edit_file._data[
            game_plan_offset + GP_LINEUP :
            game_plan_offset + GP_LINEUP + 40
        ]
    )
    assert lineup[:2] == [0, 11]
    for preset_offset in GP_POSITION_PRESETS:
        for phase_offset in GP_POSITION_PHASE_OFFSETS:
            assert edit_file._data[
                game_plan_offset + preset_offset + phase_offset + 1
            ] == POSITION_NAMES.index("RB")


def test_goalkeeper_removal_uses_first_reserve_without_metadata():
    from tests.test_editor import _build_mock_data

    data = _build_mock_data(
        num_players=0,
        num_teams=1,
        num_team_player=1,
        num_game_plans=1,
        team_player_entries=[
            (101, list(range(1000, 1013)), list(range(1, 14))),
        ],
        league_team_ids=[101],
    )
    edit_file = EditFile()
    edit_file.load_bytes(data)
    game_plan_offset = edit_file.game_plan_start
    lineup_offset = game_plan_offset + GP_LINEUP

    assert edit_file.release_player(1000, 101)

    roster = edit_file.get_team_roster(101)
    assert roster is not None
    assert roster.player_ids[0] == 1012
    assert roster.player_ids[11] == 1011
    lineup = list(edit_file._data[lineup_offset : lineup_offset + 40])
    assert lineup[:2] == [11, 1]
    assert lineup.index(0) == 11


def test_unknown_role_zero_keeps_incumbent_over_reserve_goalkeepers():
    from tests.test_editor import _build_mock_data

    data = _build_mock_data(
        num_players=0,
        num_teams=1,
        num_team_player=1,
        num_game_plans=1,
        team_player_entries=[
            (101, list(range(1000, 1013)), list(range(1, 14))),
        ],
        league_team_ids=[101],
    )
    edit_file = EditFile()
    edit_file.load_bytes(data)
    edit_file.attach_playerbin(
        PlayerBinDatabase(
            {
                151751: PlayerBinRecord(151751, "Mike Penders", 20, "GK", 0),
                101076: PlayerBinRecord(
                    101076, "Emiliano Martínez", 33, "GK", 0
                ),
            }
        )
    )
    edit_file._player_cache = {
        1000: PlayerInfo(1000, "Existing incumbent", position="")
    }
    roster = edit_file.get_team_roster(101)
    assert roster is not None
    roster.player_ids[11] = 151751
    roster.player_ids[12] = 101076
    game_plan_offset = edit_file.game_plan_start
    lineup_offset = game_plan_offset + GP_LINEUP
    lineup = list(edit_file._data[lineup_offset : lineup_offset + 40])

    edit_file._repair_game_plan_goalkeeper_positions(
        game_plan_offset,
        roster,
        lineup,
    )

    assert lineup[:2] == [0, 1]


def test_gameplan_uses_primary_native_goalkeeper_and_keeps_reserve():
    from tests.test_editor import _build_mock_data

    data = _build_mock_data(
        num_players=0,
        num_teams=1,
        num_team_player=1,
        num_game_plans=1,
        team_player_entries=[
            (
                101,
                [101076, 151751] + list(range(2000, 2038)),
                list(range(1, 41)),
            )
        ],
        league_team_ids=[101],
    )
    edit_file = EditFile()
    edit_file.load_bytes(data)
    edit_file.attach_playerbin(
        PlayerBinDatabase(
            {
                101076: PlayerBinRecord(
                    101076, "Emiliano Martínez", 33, "GK", 0, caps=37
                ),
                151751: PlayerBinRecord(151751, "Mike Penders", 20, "GK", 0),
            }
        )
    )
    game_plan_offset = edit_file.game_plan_start
    lineup_offset = game_plan_offset + GP_LINEUP
    edit_file._data[lineup_offset : lineup_offset + 40] = bytes(
        [1] + list(range(2, 12)) + [0] + list(range(12, 40))
    )

    metrics = edit_file.repair_game_plans()

    lineup = list(edit_file._data[lineup_offset : lineup_offset + 40])
    assert lineup[0] == 0
    assert lineup[11] == 1
    assert metrics["repaired_goalkeeper_roles"] == 1


def test_gameplan_uses_club_roster_order_not_international_caps():
    from tests.test_editor import _build_mock_data

    data = _build_mock_data(
        num_players=0,
        num_teams=1,
        num_team_player=1,
        num_game_plans=1,
        team_player_entries=[
            (
                101,
                [101520, 47242] + list(range(2000, 2038)),
                list(range(1, 41)),
            )
        ],
        league_team_ids=[101],
    )
    edit_file = EditFile()
    edit_file.load_bytes(data)
    edit_file.attach_playerbin(
        PlayerBinDatabase(
            {
                101520: PlayerBinRecord(
                    101520, "David Raya", 30, "GK", 0, caps=8
                ),
                47242: PlayerBinRecord(
                    47242, "Kepa Arrizabalaga", 31, "GK", 0, caps=15
                ),
            }
        )
    )
    lineup_offset = edit_file.game_plan_start + GP_LINEUP
    edit_file._data[lineup_offset : lineup_offset + 40] = bytes(
        [0] + list(range(2, 12)) + [1] + list(range(12, 40))
    )

    metrics = edit_file.repair_game_plans()

    lineup = list(edit_file._data[lineup_offset : lineup_offset + 40])
    assert lineup[0] == 0
    assert lineup[11] == 1
    assert metrics["repaired_goalkeeper_roles"] == 0



def test_gameplan_moves_every_extra_goalkeeper_out_of_starters():
    from tests.test_editor import _build_mock_data

    data = _build_mock_data(
        num_players=0,
        num_teams=1,
        num_team_player=1,
        num_game_plans=1,
        team_player_entries=[
            (
                101,
                [101076, 151751] + list(range(2000, 2038)),
                list(range(1, 41)),
            )
        ],
        league_team_ids=[101],
    )
    edit_file = EditFile()
    edit_file.load_bytes(data)
    edit_file.attach_playerbin(
        PlayerBinDatabase(
            {
                101076: PlayerBinRecord(
                    101076, "Emiliano Martínez", 33, "GK", 0
                ),
                151751: PlayerBinRecord(
                    151751, "Mike Penders", 20, "GK", 0
                ),
                2011: PlayerBinRecord(
                    2011, "Bench Outfielder", 24, "CB", 0
                ),
            }
        )
    )
    lineup_offset = edit_file.game_plan_start + GP_LINEUP

    metrics = edit_file.repair_game_plans()

    lineup = list(edit_file._data[lineup_offset : lineup_offset + 40])
    assert lineup[0] == 0
    assert lineup[1] == 11
    assert lineup[11] == 1
    assert metrics["repaired_goalkeeper_roles"] == 1


def test_gameplan_does_not_promote_native_outfield_player_to_goalkeeper():
    from tests.test_editor import _build_mock_data

    data = _build_mock_data(
        num_players=0,
        num_teams=1,
        num_team_player=1,
        num_game_plans=1,
        team_player_entries=[
            (
                101,
                [138156, 151751] + list(range(2000, 2038)),
                list(range(1, 41)),
            )
        ],
        league_team_ids=[101],
    )
    edit_file = EditFile()
    edit_file.load_bytes(data)
    edit_file.attach_playerbin(
        PlayerBinDatabase(
            {
                138156: PlayerBinRecord(138156, "Cole Palmer", 23, "AMF", 0),
                151751: PlayerBinRecord(151751, "Mike Penders", 20, "GK", 0),
            }
        )
    )
    game_plan_offset = edit_file.game_plan_start
    lineup_offset = game_plan_offset + GP_LINEUP
    edit_file._data[lineup_offset : lineup_offset + 40] = bytes(
        [0] + list(range(2, 12)) + [1] + list(range(12, 40))
    )

    edit_file.repair_game_plans()

    lineup = list(edit_file._data[lineup_offset : lineup_offset + 40])
    assert lineup[0] == 1
    assert lineup[11] == 0

def test_added_goalkeeper_stays_on_bench_when_primary_exists():
    from tests.test_editor import _build_mock_data

    data = _build_mock_data(
        num_players=0,
        num_teams=1,
        num_team_player=1,
        num_game_plans=1,
        team_player_entries=[
            (101, [101076] + list(range(2000, 2010)), list(range(1, 12)))
        ],
        league_team_ids=[101],
    )
    edit_file = EditFile()
    edit_file.load_bytes(data)
    edit_file.attach_playerbin(
        PlayerBinDatabase(
            {
                101076: PlayerBinRecord(
                    101076, "Emiliano Martínez", 33, "GK", 0, caps=37
                ),
                151751: PlayerBinRecord(151751, "Mike Penders", 20, "GK", 0),
            }
        )
    )
    game_plan_offset = edit_file.game_plan_start
    lineup_offset = game_plan_offset + GP_LINEUP

    assert edit_file.add_player(151751, 101, position="GK")

    lineup = list(edit_file._data[lineup_offset : lineup_offset + 40])
    assert lineup[0] == 0
    assert lineup[11] == 11

def test_goalkeeper_addition_uses_transfer_position_for_game_plan_role():
    from tests.test_editor import _build_mock_data

    data = _build_mock_data(
        num_players=0,
        num_teams=1,
        num_team_player=1,
        num_game_plans=1,
        team_player_entries=[
            (101, list(range(1000, 1010)), list(range(1, 11))),
        ],
        league_team_ids=[101],
    )
    edit_file = EditFile()
    edit_file.load_bytes(data)
    game_plan_offset = edit_file.game_plan_start
    lineup_offset = game_plan_offset + GP_LINEUP

    assert edit_file.add_player(2000, 101, position="GK")

    lineup = list(edit_file._data[lineup_offset : lineup_offset + 11])
    assert lineup[:2] == [10, 1]
    assert lineup[10] == 0
def test_runtime_falls_back_to_local_reference_playerbin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import native_metadata

    reference_path = tmp_path / "reference" / "Player.bin"
    reference_path.parent.mkdir(parents=True)
    raw = bytearray(RECORD_SIZE)
    struct.pack_into("<I", raw, 0x08, 162196)
    raw[0x36] = POSITION_NAMES.index("RB") << 2
    raw[0x44 : 0x44 + len(b"Marco Palestra")] = b"Marco Palestra"
    reference_path.write_bytes(raw)
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(config, "PLAYER_BIN_FILE", tmp_path / "missing-Player.bin")
    monkeypatch.setattr(config, "GAME_ROOT", None)

    database, source = native_metadata._load_playerbin_database()

    assert database is not None
    assert database.get(162196).registered_position == "RB"
    assert source == str(reference_path)


@pytest.mark.skipif(not CPK_PATH.exists(), reason="CPK fixture is not available")
def test_runtime_loads_playerbin_from_discovered_game_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import native_metadata

    game_root = tmp_path / "Football Life 2026"
    archive = game_root / "download" / "data_s2526.cpk"
    archive.parent.mkdir(parents=True)
    archive.symlink_to(CPK_PATH.resolve())
    monkeypatch.setattr(config, "GAME_ROOT", game_root)
    monkeypatch.setattr(config, "PLAYER_BIN_FILE", tmp_path / "missing-Player.bin")

    database, source = native_metadata._load_playerbin_database()

    assert database is not None
    assert database.get(162196).registered_position == "RB"
    assert source == f"{archive}::Player.bin"



@pytest.mark.skipif(
    not (CPK_PATH.exists() and EXTRA_CPK_PATH.exists()),
    reason="game database CPK fixtures are not available",
)
def test_runtime_loads_team_and_assignment_indexes_from_game_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import native_metadata

    game_root = tmp_path / "Football Life 2026"
    download = game_root / "download"
    download.mkdir(parents=True)
    extra = download / "data_extra.cpk"
    extra.symlink_to(EXTRA_CPK_PATH.resolve())
    monkeypatch.setattr(config, "GAME_ROOT", game_root)
    monkeypatch.setattr(config, "TEAM_BIN_FILE", tmp_path / "missing-Team.bin")
    monkeypatch.setattr(
        config,
        "PLAYER_ASSIGNMENT_FILE",
        tmp_path / "missing-PlayerAssignment.bin",
    )

    teams, team_source = native_metadata._load_teambin_database()
    assignments, assignment_source = native_metadata._load_player_assignment_database()

    assert teams is not None
    assert teams.get(320) == TeamBinRecord(320, "Cagliari Calcio", "CAG")
    assert team_source == f"{extra}::Team.bin"
    assert assignments is not None
    assert assignments.team_keys_for(162196) == (12, 320)
    assert assignment_source == f"{extra}::PlayerAssignment.bin"

