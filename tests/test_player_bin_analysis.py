from __future__ import annotations

import struct
from pathlib import Path

import config
import pytest

from editor.editfile import (
    GP_ATTACK_PLAYERS,
    GP_CAPTAIN,
    GP_LEFT_CK,
    GP_LINEUP,
    GP_PK,
    GP_POSITION_PRESETS,
    GP_POSITION_PHASE_OFFSETS,
    GP_RIGHT_CK,
    EditFile,
)
from editor.models import PlayerInfo
from editor.player_codec import (
    PLAYER_APPEARANCE_SIZE,
    _build_generic_appearance,
    load_appearance_template,
    load_appearance_template_bytes,
)
from editor.player_assignment import (
    PlayerAssignmentDatabase,
    PlayerAssignmentRecord,
)
from editor.playerbin import RECORD_SIZE, PlayerBinDatabase, PlayerBinRecord
from editor.teambin import TeamBinDatabase, TeamBinRecord
from tools.cpk_extract import extract_file, extract_game_databases, list_files


CPK_PATH = Path("reference/data_s2526.cpk")
EXTRA_CPK_PATH = Path("reference/download/data_extra.cpk")
APPEARANCE_PATH = Path("data/PlayerAppearance.bin")




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
        "PlayerAppearance.bin",
        "PlayerAssignment.bin",
        "Team.bin",
    }
    assert len(PlayerBinDatabase.load(extracted["Player.bin"])) > 25_000
    assert len(TeamBinDatabase.load(extracted["Team.bin"])) == 743
    assert len(PlayerAssignmentDatabase.load(extracted["PlayerAssignment.bin"])) == 21_245


@pytest.mark.skipif(not APPEARANCE_PATH.exists(), reason="appearance fixture is not available")
def test_appearance_template_overlays_only_identity_and_colors():
    template = load_appearance_template(APPEARANCE_PATH, donor_player_id=91)
    assert len(template) == PLAYER_APPEARANCE_SIZE
    assert load_appearance_template_bytes(
        APPEARANCE_PATH.read_bytes(), donor_player_id=91
    ) == template

    player = type("Player", (), {"player_id": 999, "skin_color": 2, "iris_color": 1})()
    result = _build_generic_appearance(player, template=template)

    assert len(result) == PLAYER_APPEARANCE_SIZE
    assert struct.unpack_from("<I", result, 0)[0] == 999
    assert struct.unpack_from("<I", result, 8)[0] == 999
    assert result[45] == 2
    assert result[64] & 0x0F == 1
    for offset in (4, 5, 6, 7, 12, 20, 30, 40, 44, 46, 50, 60, 70):
        assert result[offset] == template[offset]


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
            overall_rating=80 if index != 25 and index != 35 else (52 if index == 25 else 62),
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
@pytest.mark.skipif(not CPK_PATH.exists(), reason="CPK fixture is not available")
def test_runtime_loads_playerbin_from_discovered_game_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import run

    game_root = tmp_path / "Football Life 2026"
    archive = game_root / "download" / "data_s2526.cpk"
    archive.parent.mkdir(parents=True)
    archive.symlink_to(CPK_PATH.resolve())
    monkeypatch.setattr(config, "GAME_ROOT", game_root)
    monkeypatch.setattr(config, "PLAYER_BIN_FILE", tmp_path / "missing-Player.bin")

    database, source = run._load_playerbin_database()

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
    import run

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

    teams, team_source = run._load_teambin_database()
    assignments, assignment_source = run._load_player_assignment_database()

    assert teams is not None
    assert teams.get(320) == TeamBinRecord(320, "Cagliari Calcio", "CAG")
    assert team_source == f"{extra}::Team.bin"
    assert assignments is not None
    assert assignments.team_keys_for(162196) == (12, 320)
    assert assignment_source == f"{extra}::PlayerAssignment.bin"


@pytest.mark.skipif(not CPK_PATH.exists(), reason="CPK fixture is not available")
def test_runtime_loads_playerappearance_from_discovered_game_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import run

    game_root = tmp_path / "Football Life 2026"
    archive = game_root / "download" / "data_s2526.cpk"
    archive.parent.mkdir(parents=True)
    archive.symlink_to(CPK_PATH.resolve())
    monkeypatch.setattr(config, "PLAYER_APPEARANCE_FILE", tmp_path / "missing-appearance.bin")

    data, source = run._load_player_appearance_data(game_root=game_root)

    assert data is not None
    assert source == f"{archive}::PlayerAppearance.bin"
    assert load_appearance_template_bytes(data, donor_player_id=91) == load_appearance_template(
        APPEARANCE_PATH, donor_player_id=91
    )