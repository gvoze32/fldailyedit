"""
Tests for the binary edit file editor.

Uses synthetic mock binary data that mimics the PES21 data.dat structure.
"""
import struct
import pytest

from editor.editfile import (
    EditFile,
    HEADER_SIZE, PLAYER_TOTAL_SIZE, TEAM_ENTRY_SIZE, MANAGER_ENTRY_SIZE,
    COMPETITION_ENTRY_SIZE, STADIUM_ENTRY_SIZE, UNKNOWN_ENTRY_SIZE,
    TEAM_PLAYER_ENTRY_SIZE, COMPETITION_SECTION_SIZE, GAME_PLAN_ENTRY_SIZE,
    MAX_PLAYERS, MAX_TEAMS, MAX_MANAGERS, MAX_COMPETITIONS,
    MAX_STADIUMS, MAX_UNKNOWN, MAX_TEAM_PLAYER, MAX_GAME_PLANS,
    HDR_PLAYER_COUNT, HDR_TEAM_COUNT, HDR_MANAGER_COUNT,
    HDR_STADIUM_COUNT, HDR_COMPETITION_COUNT, HDR_UNKNOWN_COUNT,
    HDR_TEAM_PLAYER_COUNT, HDR_GAME_PLAN_COUNT,
    TP_TEAM_ID, TP_PLAYER_IDS, TP_SHIRT_NUMBERS, TP_MAX_PLAYERS,
    CREATED_PLAYER_ID_MIN,
    GP_TEAM_ID, GP_LINEUP, GP_CAPTAIN,
    PE_PLAYER_ID, PE_PLAYER_NAME,
    TE_TEAM_ID, TE_TEAM_NAME,
)
from editor.models import TeamData, PlayerInfo, TeamInfo


def _build_mock_data(
    num_players=3,
    num_teams=2,
    num_managers=1,
    num_competitions=1,
    num_stadiums=1,
    num_unknown=1,
    num_team_player=2,
    num_game_plans=2,
    team_player_entries=None,
    player_entries=None,
    team_entries=None,
    league_team_ids=None,
):
    """
    Build a mock data.dat with blocks sized at MAX capacity (like real PES21).

    num_* values are written as "used counts" in the header, but blocks
    are always allocated at full MAX capacity to match real file structure.

    team_player_entries: list of (team_id, [player_ids...], [shirt_nums...])
    player_entries: list of (player_id, name)
    team_entries: list of (team_id, name)
    league_team_ids: team IDs encoded into one competition-membership division
    """
    header = bytearray(HEADER_SIZE)

    # Write "used" counts into header
    struct.pack_into("<H", header, HDR_PLAYER_COUNT, num_players)
    struct.pack_into("<H", header, HDR_TEAM_COUNT, num_teams)
    struct.pack_into("<H", header, HDR_MANAGER_COUNT, num_managers)
    struct.pack_into("<H", header, HDR_STADIUM_COUNT, num_stadiums)
    struct.pack_into("<H", header, HDR_COMPETITION_COUNT, num_competitions)
    struct.pack_into("<H", header, HDR_UNKNOWN_COUNT, num_unknown)
    struct.pack_into("<H", header, HDR_TEAM_PLAYER_COUNT, num_team_player)
    struct.pack_into("<H", header, HDR_GAME_PLAN_COUNT, num_game_plans)

    # Blocks are sized at MAX capacity (not "used" count)
    player_block = bytearray(MAX_PLAYERS * PLAYER_TOTAL_SIZE)
    if player_entries:
        for i, (pid, name) in enumerate(player_entries):
            offset = i * PLAYER_TOTAL_SIZE
            struct.pack_into("<I", player_block, offset + PE_PLAYER_ID, pid)
            name_bytes = name.encode("utf-8")[:60]
            player_block[offset + PE_PLAYER_NAME:offset + PE_PLAYER_NAME + len(name_bytes)] = name_bytes

    if team_entries is None and team_player_entries:
        team_entries = [
            (tid, f"Team {tid}")
            for tid, _pids, _shirts in team_player_entries[:num_teams]
        ]

    team_block = bytearray(MAX_TEAMS * TEAM_ENTRY_SIZE)
    if team_entries:
        for i, (tid, name) in enumerate(team_entries):
            offset = i * TEAM_ENTRY_SIZE
            struct.pack_into("<I", team_block, offset + TE_TEAM_ID, tid)
            name_bytes = name.encode("utf-8")[:69]
            team_block[offset + TE_TEAM_NAME:offset + TE_TEAM_NAME + len(name_bytes)] = name_bytes

    manager_block = bytearray(MAX_MANAGERS * MANAGER_ENTRY_SIZE)
    competition_block = bytearray(MAX_COMPETITIONS * COMPETITION_ENTRY_SIZE)
    stadium_block = bytearray(MAX_STADIUMS * STADIUM_ENTRY_SIZE)
    unknown_block = bytearray(MAX_UNKNOWN * UNKNOWN_ENTRY_SIZE)

    tp_block = bytearray(MAX_TEAM_PLAYER * TEAM_PLAYER_ENTRY_SIZE)
    if team_player_entries:
        for i, (tid, pids, shirts) in enumerate(team_player_entries):
            offset = i * TEAM_PLAYER_ENTRY_SIZE
            struct.pack_into("<I", tp_block, offset + TP_TEAM_ID, tid)
            for j, pid in enumerate(pids[:TP_MAX_PLAYERS]):
                struct.pack_into("<I", tp_block, offset + TP_PLAYER_IDS + j * 4, pid)
            for j, sn in enumerate(shirts[:TP_MAX_PLAYERS]):
                struct.pack_into("<H", tp_block, offset + TP_SHIRT_NUMBERS + j * 2, sn)

    # Competition entry section (flat 4656 bytes) + Game plan block
    comp_entry_section = bytearray(COMPETITION_SECTION_SIZE)
    if league_team_ids:
        for index, team_id in enumerate(league_team_ids):
            struct.pack_into("<I", comp_entry_section, index * 4, team_id)
    gp_block = bytearray(MAX_GAME_PLANS * GAME_PLAN_ENTRY_SIZE)
    if team_player_entries:
        for i, (tid, _pids, _shirts) in enumerate(team_player_entries[:num_game_plans]):
            offset = i * GAME_PLAN_ENTRY_SIZE
            struct.pack_into("<I", gp_block, offset + GP_TEAM_ID, tid)
            gp_block[offset + GP_LINEUP:offset + GP_LINEUP + TP_MAX_PLAYERS] = bytes(
                range(TP_MAX_PLAYERS)
            )

    # Assemble everything
    data = bytearray()
    data += header
    data += player_block
    data += team_block
    data += manager_block
    data += competition_block
    data += stadium_block
    data += unknown_block
    data += tp_block
    data += comp_entry_section
    data += gp_block

    return bytes(data)


class TestEditFileHeader:
    def test_parse_header_counts(self):
        data = _build_mock_data(num_players=100, num_teams=20, num_managers=15)
        ef = EditFile()
        ef.load_bytes(data)

        assert ef.player_count == 100
        assert ef.team_count == 20
        assert ef.manager_count == 15

    def test_calculate_offsets(self):
        data = _build_mock_data(num_players=10, num_teams=5)
        ef = EditFile()
        ef.load_bytes(data)

        assert ef.player_start == HEADER_SIZE  # 0x7C = 124
        # Blocks are always sized at MAX capacity, not "used" count
        expected_team_start = HEADER_SIZE + MAX_PLAYERS * PLAYER_TOTAL_SIZE
        assert ef.team_start == expected_team_start

    def test_vanilla_offsets(self):
        """Verify that vanilla PES21 counts produce the documented offsets."""
        data = _build_mock_data(
            num_players=4830,
            num_teams=210,
            num_managers=231,
            num_competitions=46,
            num_stadiums=55,
            num_unknown=79,
            num_team_player=210,
            num_game_plans=210,
        )
        ef = EditFile()
        ef.load_bytes(data)

        results = ef.validate_offsets()
        for name, r in results.items():
            assert r["match"], f"{name}: expected {r['expected']}, got {r['actual']}"

    def test_valid_mock_passes_integrity_validation(self):
        data = _build_mock_data(
            num_players=32,
            num_teams=2,
            num_team_player=2,
            num_game_plans=2,
            team_entries=[(101, "Alpha FC"), (102, "Beta FC")],
            team_player_entries=[
                (101, list(range(1001, 1017)), list(range(1, 17))),
                (102, list(range(2001, 2017)), list(range(17, 33))),
            ],
            league_team_ids=[101, 102],
        )
        ef = EditFile()
        ef.load_bytes(data)

        report = ef.validate_integrity()

        assert report["valid"] is True
        assert report["errors"] == []

    def test_integrity_rejects_club_with_fewer_than_sixteen_players(self):
        data = _build_mock_data(
            num_players=15,
            num_teams=1,
            num_team_player=1,
            num_game_plans=1,
            team_entries=[(101, "Undersized FC")],
            team_player_entries=[
                (101, list(range(1001, 1016)), list(range(1, 16))),
            ],
            league_team_ids=[101],
        )
        ef = EditFile()
        ef.load_bytes(data)

        report = ef.validate_integrity()

        assert report["valid"] is False
        assert any(
            "Club 101 roster has 15 players; minimum is 16" in error
            for error in report["errors"]
        )

    def test_integrity_rejects_missing_club_membership_classification(self):
        data = _build_mock_data(
            num_players=16,
            num_teams=1,
            num_team_player=1,
            num_game_plans=1,
            team_entries=[(100, "Low ID FC")],
            team_player_entries=[
                (100, list(range(1001, 1017)), list(range(1, 17))),
            ],
        )
        ef = EditFile()
        ef.load_bytes(data)

        report = ef.validate_integrity()

        assert report["valid"] is False
        assert "Competition membership does not identify any clubs" in report["errors"]

    def test_integrity_rejects_club_without_a_roster_entry(self):
        data = _build_mock_data(
            num_players=0,
            num_teams=1,
            num_team_player=0,
            num_game_plans=0,
            team_entries=[(101, "Rosterless FC")],
            league_team_ids=[101],
        )
        ef = EditFile()
        ef.load_bytes(data)

        report = ef.validate_integrity()

        assert report["valid"] is False
        assert "Club 101 roster has 0 players; minimum is 16" in report["errors"]

    def test_integrity_rejects_role_pointing_to_empty_roster_slot(self):
        data = bytearray(_build_mock_data(
            num_players=1,
            num_teams=1,
            num_team_player=1,
            num_game_plans=1,
            team_entries=[(101, "Alpha FC")],
            team_player_entries=[(101, [1001], [7])],
            league_team_ids=[101],
        ))
        ef = EditFile()
        ef.load_bytes(data)
        game_plan_base = ef.game_plan_start
        data[game_plan_base + GP_CAPTAIN] = 39  # captain references an empty slot
        ef.load_bytes(data)

        report = ef.validate_integrity()

        assert report["valid"] is False
        assert any("points to empty roster slot 39" in error for error in report["errors"])

    def test_repair_game_plan_preserves_valid_order_and_fills_missing_slots(self):
        data = bytearray(_build_mock_data(
            num_players=16,
            num_teams=1,
            num_team_player=1,
            num_game_plans=1,
            team_entries=[(101, "Alpha FC")],
            team_player_entries=[
                (101, list(range(1001, 1017)), list(range(1, 17))),
            ],
            league_team_ids=[101],
        ))
        ef = EditFile()
        ef.load_bytes(data)
        game_plan_base = ef.game_plan_start
        lineup = game_plan_base + GP_LINEUP
        ef._data[lineup:lineup + 4] = bytes([2, 2, 39, 0])
        ef._data[game_plan_base + GP_CAPTAIN] = 39

        metrics = ef.repair_game_plans()
        report = ef.validate_integrity()

        assert list(ef._data[lineup:lineup + 16]) == [
            2, 0, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 1, 3
        ]
        assert ef._data[game_plan_base + GP_CAPTAIN] == 0xFF
        assert metrics["repaired_lineups"] == 1
        assert metrics["reset_roles"] == 1
        assert report["valid"] is True

    def test_game_plan_removal_keeps_backup_goalkeeper_in_goalkeeper_role(self):
        data = _build_mock_data(
            num_players=40,
            num_teams=1,
            num_team_player=1,
            num_game_plans=1,
            team_player_entries=[
                (101, list(range(1000, 1040)), list(range(1, 41))),
            ],
        )
        edit_file = EditFile()
        edit_file.load_bytes(data)
        game_plan_base = edit_file.game_plan_start
        lineup = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 39, 0]
        lineup.extend(range(11, 39))
        edit_file._data[
            game_plan_base + GP_LINEUP : game_plan_base + GP_LINEUP + TP_MAX_PLAYERS
        ] = bytes(lineup)
        edit_file._player_cache = {
            1000: PlayerInfo(1000, "Backup goalkeeper", position="GK"),
            1001: PlayerInfo(1001, "Starting goalkeeper", position="GK"),
        }

        assert edit_file.release_player(1001, 101)

        roster = edit_file.get_team_roster(101)
        assert roster is not None
        assert roster.player_ids[0] == 1000
        updated_lineup = list(
            edit_file._data[
                game_plan_base + GP_LINEUP : game_plan_base + GP_LINEUP + TP_MAX_PLAYERS
            ]
        )
        assert updated_lineup[:11] == [0, 2, 3, 4, 5, 6, 7, 8, 9, 10, 1]
        assert updated_lineup.index(0) == 0
        assert sorted(updated_lineup) == list(range(TP_MAX_PLAYERS))

    def test_game_plan_removal_does_not_promote_goalkeeper_to_outfield_role(self):
        data = _build_mock_data(
            num_players=40,
            num_teams=1,
            num_team_player=1,
            num_game_plans=1,
            team_player_entries=[
                (101, list(range(1000, 1040)), list(range(1, 41))),
            ],
        )
        edit_file = EditFile()
        edit_file.load_bytes(data)
        game_plan_base = edit_file.game_plan_start
        lineup = [0, 2, 3, 4, 5, 6, 7, 8, 9, 39, 10, 1, 11]
        lineup.extend(range(12, 39))
        edit_file._data[
            game_plan_base + GP_LINEUP : game_plan_base + GP_LINEUP + TP_MAX_PLAYERS
        ] = bytes(lineup)
        edit_file._player_cache = {
            1000: PlayerInfo(1000, "Starting goalkeeper", position="GK"),
            1001: PlayerInfo(1001, "Backup goalkeeper", position="GK"),
            1011: PlayerInfo(1011, "Reserve forward", position="CF"),
            1010: PlayerInfo(1010, "Starting forward", position="CF"),
        }

        assert edit_file.release_player(1010, 101)

        roster = edit_file.get_team_roster(101)
        assert roster is not None
        updated_lineup = list(
            edit_file._data[
                game_plan_base + GP_LINEUP : game_plan_base + GP_LINEUP + TP_MAX_PLAYERS
            ]
        )
        assert updated_lineup[:11] == [0, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]
        assert roster.player_ids[10] == 1039
        assert roster.player_ids[11] == 1011
        assert updated_lineup.index(1) == 11
        assert sorted(updated_lineup) == list(range(TP_MAX_PLAYERS))

    def test_game_plan_removal_avoids_slot_zero_goalkeeper_without_metadata(self):
        data = _build_mock_data(
            num_players=40,
            num_teams=1,
            num_team_player=1,
            num_game_plans=1,
            team_player_entries=[
                (101, list(range(1000, 1040)), list(range(1, 41))),
            ],
        )
        edit_file = EditFile()
        edit_file.load_bytes(data)
        game_plan_base = edit_file.game_plan_start
        lineup = [1, 2, 3, 4, 5, 6, 7, 8, 9, 39, 10, 0, 11]
        lineup.extend(range(12, 39))
        edit_file._data[
            game_plan_base + GP_LINEUP : game_plan_base + GP_LINEUP + TP_MAX_PLAYERS
        ] = bytes(lineup)

        assert edit_file.release_player(1010, 101)

        updated_lineup = list(
            edit_file._data[
                game_plan_base + GP_LINEUP : game_plan_base + GP_LINEUP + TP_MAX_PLAYERS
            ]
        )
        assert updated_lineup[:11] == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]
        assert updated_lineup.index(0) == 11
        assert sorted(updated_lineup) == list(range(TP_MAX_PLAYERS))


    def test_game_plan_addition_uses_active_bench_role_for_sparse_roster_slot(self):
        data = _build_mock_data(
            num_players=39,
            num_teams=1,
            num_team_player=1,
            num_game_plans=1,
            team_player_entries=[
                (101, [0] + list(range(1001, 1040)), list(range(40))),
            ],
        )
        edit_file = EditFile()
        edit_file.load_bytes(data)
        game_plan_base = edit_file.game_plan_start
        lineup = list(range(1, 40)) + [0]
        edit_file._data[
            game_plan_base + GP_LINEUP : game_plan_base + GP_LINEUP + TP_MAX_PLAYERS
        ] = bytes(lineup)

        assert edit_file.add_player(9999, 101)

        updated_lineup = list(
            edit_file._data[
                game_plan_base + GP_LINEUP : game_plan_base + GP_LINEUP + TP_MAX_PLAYERS
            ]
        )
        assert updated_lineup[:11] == list(range(1, 12))
        assert updated_lineup[-1] == 0
        assert sorted(updated_lineup) == list(range(TP_MAX_PLAYERS))

    def test_game_plan_removal_updates_active_prefix_with_legacy_tail(self):
        data = _build_mock_data(
            num_players=17,
            num_teams=1,
            num_team_player=1,
            num_game_plans=1,
            team_player_entries=[
                (101, list(range(1000, 1017)), list(range(1, 18))),
            ],
            league_team_ids=[101],
        )
        edit_file = EditFile()
        edit_file.load_bytes(data)
        game_plan_base = edit_file.game_plan_start
        lineup = [0, 1, 16, *range(2, 16)]
        lineup.extend([0xFF] * (TP_MAX_PLAYERS - len(lineup)))
        edit_file._data[
            game_plan_base + GP_LINEUP : game_plan_base + GP_LINEUP + TP_MAX_PLAYERS
        ] = bytes(lineup)

        assert edit_file.release_player(1005, 101)

        updated_lineup = list(
            edit_file._data[
                game_plan_base + GP_LINEUP : game_plan_base + GP_LINEUP + TP_MAX_PLAYERS
            ]
        )
        assert updated_lineup[:16] == list(range(16))
        assert edit_file.validate_integrity()["valid"] is True

    def test_game_plan_addition_updates_active_prefix_with_legacy_tail(self):
        data = _build_mock_data(
            num_players=15,
            num_teams=1,
            num_team_player=1,
            num_game_plans=1,
            team_player_entries=[
                (101, list(range(1000, 1015)), list(range(1, 16))),
            ],
            league_team_ids=[101],
        )
        edit_file = EditFile()
        edit_file.load_bytes(data)
        game_plan_base = edit_file.game_plan_start
        lineup = list(range(15)) + [0xFF] * (TP_MAX_PLAYERS - 15)
        edit_file._data[
            game_plan_base + GP_LINEUP : game_plan_base + GP_LINEUP + TP_MAX_PLAYERS
        ] = bytes(lineup)

        assert edit_file.add_player(9999, 101)

        updated_lineup = list(
            edit_file._data[
                game_plan_base + GP_LINEUP : game_plan_base + GP_LINEUP + TP_MAX_PLAYERS
            ]
        )
        assert updated_lineup[:16] == list(range(16))
        assert edit_file.validate_integrity()["valid"] is True


class TestReadPlayers:
    def test_read_players(self):
        data = _build_mock_data(
            num_players=3,
            player_entries=[
                (1001, "Lionel Messi"),
                (1002, "Cristiano Ronaldo"),
                (1003, "Neymar"),
            ],
        )
        ef = EditFile()
        ef.load_bytes(data)

        players = ef.get_all_players(include_base_db=False)
        assert len(players) == 3
        assert players[1001].name == "Lionel Messi"
        assert players[1002].name == "Cristiano Ronaldo"
        assert players[1003].name == "Neymar"


    def test_read_players_without_external_catalog(self, monkeypatch, tmp_path):
        import config

        data = _build_mock_data(
            num_players=2,
            num_team_player=1,
            player_entries=[
                (1001, "Vanilla One"),
                (1002, "Vanilla Two"),
            ],
            team_player_entries=[
                (101, [1001, 1002], [1, 2]),
            ],
        )
        monkeypatch.setattr(config, "CURRENT_PLAYERS_FILE", tmp_path / "missing.txt")
        monkeypatch.setattr(config, "PLAYERS_CSV_FILE", tmp_path / "missing.csv")

        edit_file = EditFile()
        edit_file.load_bytes(data)

        players = edit_file.get_all_players()

        assert set(players) == {1001, 1002}
        assert edit_file.player_catalog_report.current_entries == 0

class TestReadTeams:
    def test_read_team_info(self):
        data = _build_mock_data(
            num_teams=2,
            team_entries=[
                (101, "FC Barcelona"),
                (102, "Real Madrid"),
            ],
        )
        ef = EditFile()
        ef.load_bytes(data)

        teams = ef.get_all_team_info()
        assert len(teams) == 2
        assert teams[101].name == "FC Barcelona"
        assert teams[102].name == "Real Madrid"


class TestTeamRosters:
    @pytest.fixture
    def ef_with_rosters(self):
        data = _build_mock_data(
            num_team_player=2,
            team_player_entries=[
                (101, [1001, 1002, 1003, 0], [10, 7, 11, 0]),
                (102, [2001, 2002, 0, 0], [9, 5, 0, 0]),
            ],
        )
        ef = EditFile()
        ef.load_bytes(data)
        return ef

    def test_read_roster(self, ef_with_rosters):
        roster = ef_with_rosters.get_team_roster(101)
        assert roster is not None
        assert roster.team_id == 101
        assert roster.roster == [1001, 1002, 1003]
        assert roster.roster_size == 3

    def test_roster_not_found(self, ef_with_rosters):
        roster = ef_with_rosters.get_team_roster(999)
        assert roster is None

    def test_get_all_rosters(self, ef_with_rosters):
        rosters = ef_with_rosters.get_all_rosters()
        assert len(rosters) == 2
        assert 101 in rosters
        assert 102 in rosters

    def test_shirt_numbers(self, ef_with_rosters):
        roster = ef_with_rosters.get_team_roster(101)
        assert roster.shirt_numbers[0] == 10
        assert roster.shirt_numbers[1] == 7
        assert roster.shirt_numbers[2] == 11


class TestMovePlayer:
    def _build_transfer_data(self):
        return _build_mock_data(
            num_team_player=2,
            team_player_entries=[
                # Team 101: 3 players
                (101, [1001, 1002, 1003, 0], [10, 7, 11, 0]),
                # Team 102: 2 players
                (102, [2001, 2002, 0, 0], [9, 5, 0, 0]),
            ],
        )

    def test_basic_transfer(self):
        """Move player 1002 from team 101 to team 102."""
        data = _build_mock_data(
            num_team_player=2,
            team_player_entries=[
                (101, list(range(1001, 1018)), list(range(1, 18))),
                (102, list(range(2001, 2017)), list(range(18, 34))),
            ],
            league_team_ids=[101, 102],
        )
        ef = EditFile()
        ef.load_bytes(data)

        result = ef.move_player(1002, from_team_id=101, to_team_id=102)
        assert result is True
        assert ef.validate_integrity()["valid"] is True

        # Verify source: 1002 removed, 1017 compacted into slot 1
        src = ef.get_team_roster(101)
        assert 1002 not in src.roster
        assert src.roster_size == 16
        assert src.player_ids[0] == 1001
        assert src.player_ids[1] == 1017

        # Verify dest: 1002 added
        dst = ef.get_team_roster(102)
        assert 1002 in dst.roster
        assert dst.roster_size == 17

    def test_transfer_last_player(self):
        """Move the last player in a team's roster (no compaction needed)."""
        data = self._build_transfer_data()
        ef = EditFile()
        ef.load_bytes(data)

        result = ef.move_player(1003, from_team_id=101, to_team_id=102)
        assert result is True

        src = ef.get_team_roster(101)
        assert src.roster_size == 2
        assert 1003 not in src.roster

        dst = ef.get_team_roster(102)
        assert dst.roster_size == 3
        assert 1003 in dst.roster

    def test_transfer_player_not_on_team(self):
        """Moving a player that's not on the source team should fail."""
        data = self._build_transfer_data()
        ef = EditFile()
        ef.load_bytes(data)

        result = ef.move_player(9999, from_team_id=101, to_team_id=102)
        assert result is False

    def test_transfer_already_on_dest(self):
        """Moving a player already on the destination team should fail."""
        data = self._build_transfer_data()
        ef = EditFile()
        ef.load_bytes(data)

        result = ef.move_player(2001, from_team_id=102, to_team_id=102)
        assert result is False

    def test_transfer_source_team_not_found(self):
        data = self._build_transfer_data()
        ef = EditFile()
        ef.load_bytes(data)

        result = ef.move_player(1001, from_team_id=999, to_team_id=102)
        assert result is False

    def test_transfer_dest_team_not_found(self):
        data = self._build_transfer_data()
        ef = EditFile()
        ef.load_bytes(data)

        result = ef.move_player(1001, from_team_id=101, to_team_id=999)
        assert result is False

    def test_transfer_assigns_shirt_number(self):
        """Transferred player should get an unused shirt number."""
        data = self._build_transfer_data()
        ef = EditFile()
        ef.load_bytes(data)

        ef.move_player(1001, from_team_id=101, to_team_id=102)

        dst = ef.get_team_roster(102)
        idx = dst.player_index(1001)
        assert idx >= 0
        shirt = dst.shirt_numbers[idx]
        assert shirt > 0

    def test_multiple_transfers(self):
        """Move two players sequentially."""
        data = self._build_transfer_data()
        ef = EditFile()
        ef.load_bytes(data)

        ef.move_player(1001, from_team_id=101, to_team_id=102)
        ef.move_player(2001, from_team_id=102, to_team_id=101)

        src = ef.get_team_roster(101)
        dst = ef.get_team_roster(102)

        assert 1001 not in src.roster
        assert 2001 in src.roster
        assert 1001 in dst.roster
        assert 2001 not in dst.roster


class TestTeamDataModel:
    def test_roster_property(self):
        td = TeamData(team_id=1, player_ids=[100, 200, 0, 0])
        assert td.roster == [100, 200]

    def test_is_full(self):
        td = TeamData(team_id=1, player_ids=list(range(1, 41)))
        assert td.is_full
        assert td.roster_size == 40

    def test_not_full(self):
        td = TeamData(team_id=1, player_ids=[1, 2, 0, 0])
        assert not td.is_full

    def test_first_empty_slot(self):
        td = TeamData(team_id=1, player_ids=[100, 200, 0, 0])
        assert td.first_empty_slot() == 2

    def test_first_empty_slot_full(self):
        td = TeamData(team_id=1, player_ids=list(range(1, 41)))
        assert td.first_empty_slot() == -1

    def test_has_player(self):
        td = TeamData(team_id=1, player_ids=[100, 200, 0, 0])
        assert td.has_player(100)
        assert not td.has_player(999)

    def test_player_index(self):
        td = TeamData(team_id=1, player_ids=[100, 200, 300, 0])
        assert td.player_index(200) == 1
        assert td.player_index(999) == -1


class TestReleaseAndAddPlayer:
    def _build_test_data(self):
        return _build_mock_data(
            num_players=5,
            num_teams=2,
            num_team_player=2,
            team_player_entries=[
                (101, [1001, 1002, 1003], [7, 10, 11]),
                (102, [2001, 2002], [1, 9]),
            ],
        )

    def test_release_player_compaction(self):
        """Releasing a middle player compacts the team roster."""
        data = self._build_test_data()
        ef = EditFile()
        ef.load_bytes(data)

        ok = ef.release_player(1002, from_team_id=101)
        assert ok is True

        roster = ef.get_team_roster(101)
        assert 1002 not in roster.roster
        assert roster.roster_size == 2
        assert roster.player_ids[0] == 1001
        assert roster.player_ids[1] == 1003  # compacted from slot 2

    def test_get_player_shirt_number(self):
        ef = EditFile()
        ef.load_bytes(self._build_test_data())

        assert ef.get_player_shirt_number(101, 1002) == 10
        assert ef.get_player_shirt_number(101, 9999) is None
        assert ef.get_player_shirt_number(999, 1002) is None

    def test_release_player_not_found(self):
        data = self._build_test_data()
        ef = EditFile()
        ef.load_bytes(data)

        ok = ef.release_player(9999, from_team_id=101)
        assert ok is False

    def test_add_player_from_free_agent(self):
        data = self._build_test_data()
        ef = EditFile()
        ef.load_bytes(data)

        ok = ef.add_player(3001, to_team_id=102)
        assert ok is True

        roster = ef.get_team_roster(102)
        assert 3001 in roster.roster
        assert roster.roster_size == 3

    def test_add_player_already_exists(self):
        data = self._build_test_data()
        ef = EditFile()
        ef.load_bytes(data)

        ok = ef.add_player(2001, to_team_id=102)
        assert ok is False

    def test_add_player_rejects_existing_player_on_another_club(self):
        """A signing may not duplicate a player already registered elsewhere."""
        data = self._build_test_data()
        ef = EditFile()
        ef.load_bytes(data)

        ok = ef.add_player(1001, to_team_id=102)
        assert ok is False
        assert ef.get_team_roster(101).has_player(1001)
        assert not ef.get_team_roster(102).has_player(1001)

    def test_overflow_releases_deepest_reserve_without_ability_ranking(self):
        """A full roster releases its deepest reserve, regardless of OVR."""
        data = self._build_test_data()
        ef = EditFile()
        ef.load_bytes(data)

        # Fill team 101 to 40 players
        to_entry = ef._find_team_player_entry_offset(101)
        for slot in range(40):
            ef._write_player_slot(to_entry, slot, 1000 + slot, slot + 1)

        roster = ef.get_team_roster(101)
        assert roster.is_full is True
        assert roster.roster_size == 40

        # Ability values are intentionally irrelevant to overflow ranking.
        from editor.models import PlayerInfo
        ef._player_cache = {
            1000 + i: PlayerInfo(
                player_id=1000 + i,
                name=f"P{i}",
                overall_rating=99 if i == 30 else 40,
            )
            for i in range(40)
        }

        # Add a new 41st player
        ok = ef.add_player(9999, to_team_id=101)
        assert ok is True

        new_roster = ef.get_team_roster(101)
        assert new_roster.roster_size == 40
        assert 9999 in new_roster.roster
        # The deepest reserve (slot 39) is released, not the lowest OVR.
        assert 1039 not in new_roster.roster
    
    def test_overflow_uses_game_plan_depth_over_roster_slot(self):
        """The game-plan reserve order wins over raw roster-slot order."""
        data = self._build_test_data()
        ef = EditFile()
        ef.load_bytes(data)

        to_entry = ef._find_team_player_entry_offset(101)
        for slot in range(40):
            ef._write_player_slot(to_entry, slot, 1000 + slot, slot + 1)

        lineup_start = ef._find_game_plan_offset(101) + GP_LINEUP
        lineup = list(ef._data[lineup_start : lineup_start + TP_MAX_PLAYERS])
        lineup[18], lineup[39] = lineup[39], lineup[18]
        ef._data[lineup_start : lineup_start + TP_MAX_PLAYERS] = bytes(lineup)

        slot, player_id = ef.find_overflow_release_candidate(101)

        assert (slot, player_id) == (18, 1018)

    def test_overflow_prefers_native_reserve_over_created_player(self):
        """A created deep reserve is retained when a native reserve exists."""
        data = self._build_test_data()
        ef = EditFile()
        ef.load_bytes(data)

        to_entry = ef._find_team_player_entry_offset(101)
        for slot in range(39):
            ef._write_player_slot(to_entry, slot, 1000 + slot, slot + 1)
        ef._write_player_slot(to_entry, 39, CREATED_PLAYER_ID_MIN, 40)

        slot, player_id = ef.find_overflow_release_candidate(101)

        assert (slot, player_id) == (38, 1038)

    def test_overflow_respects_per_club_protected_players(self):
        """A configured protected player is excluded from the candidate pool."""
        from editor.release_policy import ReleasePolicy

        data = self._build_test_data()
        ef = EditFile()
        ef.load_bytes(data)
        to_entry = ef._find_team_player_entry_offset(101)
        for slot in range(40):
            ef._write_player_slot(to_entry, slot, 1000 + slot, slot + 1)
        ef.attach_release_policy(ReleasePolicy({101: frozenset({1039})}, {}))

        slot, player_id = ef.find_overflow_release_candidate(101)

        assert (slot, player_id) == (38, 1038)

    def test_overflow_prefers_low_usage_over_deeper_unknown_reserve(self):
        """Known offline usage beats raw reserve depth within one tier."""
        from editor.release_policy import PlayerUsage, ReleasePolicy

        data = self._build_test_data()
        ef = EditFile()
        ef.load_bytes(data)
        to_entry = ef._find_team_player_entry_offset(101)
        for slot in range(40):
            ef._write_player_slot(to_entry, slot, 1000 + slot, slot + 1)
        ef.attach_release_policy(
            ReleasePolicy(
                {},
                {
                    1038: PlayerUsage(0, 0, 0),
                    1039: PlayerUsage(900, 20, 30),
                },
            )
        )

        slot, player_id = ef.find_overflow_release_candidate(101)

        assert (slot, player_id) == (38, 1038)


    def test_overflow_protects_goalkeepers(self):
        """Backup GK stays protected while two goalkeepers are registered."""
        data = self._build_test_data()
        ef = EditFile()
        ef.load_bytes(data)

        to_entry = ef._find_team_player_entry_offset(101)
        for slot in range(40):
            ef._write_player_slot(to_entry, slot, 1000 + slot, slot + 1)

        from editor.models import PlayerInfo
        ef._player_cache = {
            1000 + i: PlayerInfo(
                player_id=1000 + i,
                name=f"P{i}",
                position="GK" if i in (0, 25) else "CF",
            )
            for i in range(40)
        }

        ok = ef.add_player(9999, to_team_id=101, allow_overflow_release=True)
        assert ok is True

        new_roster = ef.get_team_roster(101)
        assert 1025 in new_roster.roster
        assert 1039 not in new_roster.roster

    def test_overflow_releases_deepest_reserve_with_excess_goalkeepers(self):
        """An excess reserve GK is eligible once two GKs remain protected."""
        data = self._build_test_data()
        ef = EditFile()
        ef.load_bytes(data)

        to_entry = ef._find_team_player_entry_offset(101)
        for slot in range(40):
            ef._write_player_slot(to_entry, slot, 1000 + slot, slot + 1)

        from editor.models import PlayerInfo
        ef._player_cache = {
            1000 + i: PlayerInfo(
                player_id=1000 + i,
                name=f"P{i}",
                position="GK" if i in (0, 11, 39) else "CMF",
            )
            for i in range(40)
        }

        ok = ef.add_player(9999, to_team_id=101, allow_overflow_release=True)
        assert ok is True

        new_roster = ef.get_team_roster(101)
        assert 1039 not in new_roster.roster
        assert 1000 in new_roster.roster
        assert 1011 in new_roster.roster
    def test_full_roster_rejects_explicitly_disabled_overflow_release(self):
        data = self._build_test_data()
        ef = EditFile()
        ef.load_bytes(data)
        to_entry = ef._find_team_player_entry_offset(101)
        for slot in range(40):
            ef._write_player_slot(to_entry, slot, 1000 + slot, slot + 1)

        assert ef.add_player(9999, to_team_id=101, allow_overflow_release=False) is False
        assert ef.get_team_roster(101).roster == list(range(1000, 1040))
