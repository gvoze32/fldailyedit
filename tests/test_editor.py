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
    TEAM_PLAYER_ENTRY_SIZE, GAME_PLAN_ENTRY_SIZE,
    MAX_PLAYERS, MAX_TEAMS, MAX_MANAGERS, MAX_COMPETITIONS,
    MAX_STADIUMS, MAX_UNKNOWN, MAX_TEAM_PLAYER,
    HDR_PLAYER_COUNT, HDR_TEAM_COUNT, HDR_MANAGER_COUNT,
    HDR_STADIUM_COUNT, HDR_COMPETITION_COUNT, HDR_UNKNOWN_COUNT,
    HDR_TEAM_PLAYER_COUNT, HDR_GAME_PLAN_COUNT,
    TP_TEAM_ID, TP_PLAYER_IDS, TP_SHIRT_NUMBERS, TP_MAX_PLAYERS,
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
):
    """
    Build a mock data.dat with blocks sized at MAX capacity (like real PES21).

    num_* values are written as "used counts" in the header, but blocks
    are always allocated at full MAX capacity to match real file structure.

    team_player_entries: list of (team_id, [player_ids...], [shirt_nums...])
    player_entries: list of (player_id, name)
    team_entries: list of (team_id, name)
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
    comp_entry_section = bytearray(0x1230)
    gp_block = bytearray(num_game_plans * GAME_PLAN_ENTRY_SIZE)

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

        players = ef.get_all_players()
        assert len(players) == 3
        assert players[1001].name == "Lionel Messi"
        assert players[1002].name == "Cristiano Ronaldo"
        assert players[1003].name == "Neymar"


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
        data = self._build_transfer_data()
        ef = EditFile()
        ef.load_bytes(data)

        result = ef.move_player(1002, from_team_id=101, to_team_id=102)
        assert result is True

        # Verify source: 1002 removed, 1003 compacted into slot 1
        src = ef.get_team_roster(101)
        assert 1002 not in src.roster
        assert src.roster_size == 2
        assert src.player_ids[0] == 1001
        assert src.player_ids[1] == 1003  # compacted from slot 2

        # Verify dest: 1002 added
        dst = ef.get_team_roster(102)
        assert 1002 in dst.roster
        assert dst.roster_size == 3

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

