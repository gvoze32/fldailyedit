"""
Binary edit file reader/writer.

Reads the decrypted data.dat and provides functions to:
- Parse the header to get entry counts
- Calculate table offsets dynamically for the supported PES edit-file layout
- Read all players (ID + name)
- Read all teams (ID + name)
- Read team rosters (from Team-Player Table)
- Move players between teams (the core transfer operation)

All offsets are calculated from the header — nothing is hardcoded except entry sizes
and field positions within the supported PES edit-file layout.
"""
import logging
import struct
from dataclasses import replace
from pathlib import Path

import config
from editor.models import ManagerInfo, PlayerInfo, TeamData, TeamInfo
from editor.player_assignment import PlayerAssignmentDatabase
from editor.player_catalog import PlayerCatalogReport, build_player_catalog
from editor.playerbin import POSITION_NAMES, PlayerBinDatabase
from editor.release_policy import PlayerUsage, ReleasePolicy
from editor.save_metadata import SaveHeader
from editor.teambin import TeamBinDatabase, TeamBinRecord
from editor.roster import (
    COMPETITION_SECTION_SIZE,
    FIRST_TEAM_SLOT_COUNT,
    GAME_PLAN_ENTRY_SIZE,
    GP_ATTACK_PLAYERS,
    GP_LINEUP,
    GP_POSITION_ENTRY_SIZE,
    GP_POSITION_PHASE_OFFSETS,
    GP_POSITION_PRESETS,
    GP_SINGLE_PLAYER_ROLES,
    GP_TEAM_ID,
    MAX_GAME_PLANS,
    MAX_TEAM_PLAYER,
    MIN_CLUB_ROSTER_SIZE,
    RosterGamePlanMixin,
    TEAM_PLAYER_ENTRY_SIZE,
    TP_MAX_PLAYERS,
    TP_PLAYER_IDS,
    TP_SHIRT_NUMBERS,
    TP_TEAM_ID,
    _game_plan_position_code,
)
logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
# Entry sizes (bytes) — same across PES20/21/FL26
# ──────────────────────────────────────────────────────────────
HEADER_SIZE = 0x7C               # 124 bytes
PLAYER_ENTRY_SIZE = 0xF0         # 240 bytes (data only)
PLAYER_APPEARANCE_SIZE = 0x48    # 72 bytes
PLAYER_TOTAL_SIZE = PLAYER_ENTRY_SIZE + PLAYER_APPEARANCE_SIZE  # 312 bytes (interleaved)
TEAM_ENTRY_SIZE = 0x24C          # 588 bytes
MANAGER_ENTRY_SIZE = 0x58        # 88 bytes
COMPETITION_ENTRY_SIZE = 0x2F8   # 760 bytes
STADIUM_ENTRY_SIZE = 0xBC        # 188 bytes (wiki says 0xBB but block math proves 0xBC)
UNKNOWN_ENTRY_SIZE = 0x84        # 132 bytes

# ──────────────────────────────────────────────────────────────
# MAX allocated slots per block (vanilla PES21)
#
# IMPORTANT: Each block is allocated at a FIXED max size, regardless
# of how many entries are actually "used" (reported in the header).
# These values are derived from the wiki's documented start/end offsets:
#   max_slots = (next_table_start - this_table_start) / entry_size
#
# FL26 may have different max values if SmokePatch expanded the database.
# ──────────────────────────────────────────────────────────────
MAX_PLAYERS = 30_000       # (0x8ED2FC - 0x7C) / 312
MAX_TEAMS = 750            # (0x958DA4 - 0x8ED2FC) / 588
MAX_MANAGERS = 1_300       # (0x974C84 - 0x958DA4) / 88
MAX_COMPETITIONS = 65      # (0x980D7C - 0x974C84) / 760
MAX_STADIUMS = 65          # (0x983D38 - 0x980D7C) / 188
MAX_UNKNOWN = 2_500        # (0x9D4648 - 0x983D38) / 132

# ──────────────────────────────────────────────────────────────
# Header field offsets (uint16 LE at these positions)
# ──────────────────────────────────────────────────────────────
HDR_PLAYER_COUNT = 0x60
HDR_TEAM_COUNT = 0x64
HDR_MANAGER_COUNT = 0x66
HDR_STADIUM_COUNT = 0x68
HDR_COMPETITION_COUNT = 0x6A
HDR_UNKNOWN_COUNT = 0x6C
HDR_TEAM_PLAYER_COUNT = 0x70
HDR_GAME_PLAN_COUNT = 0x74

# ──────────────────────────────────────────────────────────────
# Field offsets within entries
# ──────────────────────────────────────────────────────────────
# Player entry
PE_PLAYER_ID = 0x00        # 4 bytes uint32 LE
PE_PLAYER_NAME = 0x36      # 61 bytes null-terminated string
PE_PRINT_NAME = 0x73       # 61 bytes null-terminated string
PE_AGE_BYTE = 0x20         # 6-bit age field starts at bit 7
PE_REGISTERED_POSITION_BYTE = 0x21  # 4-bit position field starts at bit 5

# Team entry
TE_TEAM_ID = 0x000         # 4 bytes uint32 LE
TE_MANAGER_ID = 0x004      # 4 bytes uint32 LE
TE_TEAM_NAME = 0x068       # 70 bytes null-terminated string
TE_ABBREVIATION = 0x0AE    # 4 bytes null-terminated string

# Manager entry
ME_MANAGER_ID = 0x000      # 4 bytes uint32 LE
ME_NATIONALITY = 0x004     # 2 bytes uint16 LE
ME_PICTURE_ID = 0x006      # 2 bytes uint16 LE
ME_MANAGER_NAME = 0x009    # 79 bytes null-terminated string




class EditFile(RosterGamePlanMixin):
    """
    Reads and modifies a decrypted data.dat file using the supported PES edit-file layout.

    Usage:
        ef = EditFile("path/to/data.dat")
        ef.load()

        # Read data
        teams = ef.get_all_team_info()
        players = ef.get_all_players()
        roster = ef.get_team_roster(team_id=101)

        # Transfer a player
        ef.move_player(player_id=12345, from_team_id=101, to_team_id=202)

        # Save
        ef.save("path/to/data.dat")
    """

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else None
        self._data: bytearray = bytearray()

        # Counts from header
        self.player_count: int = 0
        self.team_count: int = 0
        self.manager_count: int = 0
        self.stadium_count: int = 0
        self.competition_count: int = 0
        self.unknown_count: int = 0
        self.team_player_count: int = 0
        self.game_plan_count: int = 0

        # Calculated table start offsets
        self.player_start: int = 0
        self.team_start: int = 0
        self.manager_start: int = 0
        self.competition_start: int = 0
        self.stadium_start: int = 0
        self.unknown_start: int = 0
        self.team_player_start: int = 0
        self.competition_entry_start: int = 0
        self.game_plan_start: int = 0
        self.save_header: SaveHeader | None = None
        self.game_root: Path | None = None
        self.player_catalog_report: PlayerCatalogReport | None = None
        self.playerbin_db: PlayerBinDatabase | None = None
        self.playerbin_source: str | None = None
        self.teambin_db: TeamBinDatabase | None = None
        self.teambin_source: str | None = None
        self.player_assignment_db: PlayerAssignmentDatabase | None = None
        self.player_assignment_source: str | None = None
        self.release_protected_player_ids: dict[int, frozenset[int]] = {}
        self.release_usage: dict[int, PlayerUsage] = {}

        # Track players transferred in the current session to protect them from overflow auto-release
        self.transferred_player_ids: set[int] = set()
    def attach_save_header(self, header: SaveHeader | None) -> None:
        """Attach decrypted container metadata for profile-aware catalog loading."""
        if header is not None and not isinstance(header, SaveHeader):
            raise TypeError("header must be a SaveHeader or None")
        self.save_header = header

    @property
    def is_pes21_save(self) -> bool:
        """Return whether this EditFile belongs to vanilla PES 2021."""
        header = getattr(self, "save_header", None)
        return bool(header is not None and header.is_pes21)


    def attach_playerbin(self, database: PlayerBinDatabase | None) -> None:
        """Attach the master Player.bin metadata used by roster fallbacks."""
        if database is not None and not isinstance(database, PlayerBinDatabase):
            raise TypeError("database must be a PlayerBinDatabase or None")
        self.playerbin_db = database
        self.player_catalog_report = None

    def attach_teambin(self, database: TeamBinDatabase | None) -> None:
        """Attach Team.bin names and stable team keys when available."""
        if database is not None and not isinstance(database, TeamBinDatabase):
            raise TypeError("database must be a TeamBinDatabase or None")
        self.teambin_db = database

    def attach_player_assignment(
        self, database: PlayerAssignmentDatabase | None
    ) -> None:
        """Attach PlayerAssignment.bin roster ownership when available."""
        if database is not None and not isinstance(
            database, PlayerAssignmentDatabase
        ):
            raise TypeError(
                "database must be a PlayerAssignmentDatabase or None"
            )
        self.player_assignment_db = database

    def get_master_team(self, team_key: int) -> TeamBinRecord | None:
        """Return Team.bin metadata for a stable team key."""
        database = getattr(self, "teambin_db", None)
        return database.get(team_key) if database is not None else None

    def get_player_assignment_teams(self, player_id: int) -> tuple[int, ...]:
        """Return Team.bin keys associated with a player assignment."""
        database = getattr(self, "player_assignment_db", None)
        return database.team_keys_for(player_id) if database is not None else ()


    def attach_release_policy(self, policy: ReleasePolicy | None) -> None:
        """Attach optional protected-player and offline-usage release policy."""
        if policy is not None and not isinstance(policy, ReleasePolicy):
            raise TypeError("policy must be a ReleasePolicy or None")
        self.release_protected_player_ids = (
            {} if policy is None else dict(policy.protected_players)
        )
        self.release_usage = {} if policy is None else dict(policy.usage)


    def _native_player_position(self, player_id: int) -> str | None:
        """Return native registered position from the selected Player.bin."""
        database = getattr(self, "playerbin_db", None)
        if database is None:
            return None
        record = database.get(player_id)
        if record is None:
            return None
        position = (record.registered_position or "").strip()
        return position or None

    def get_player_position(self, player_id: int) -> str | None:
        """Return native position first, then selected-save fallback metadata."""
        native_position = self._native_player_position(player_id)
        if native_position:
            return native_position
        player_info = getattr(self, "_player_cache", {}).get(player_id)
        return (
            player_info.position
            if player_info is not None and player_info.position
            else None
        )

    def _mutation_player_position(
        self, player_id: int, supplied_position: str = ""
    ) -> str:
        """Prefer native metadata, then transfer, then save fallback data."""
        return (
            self._native_player_position(player_id)
            or (supplied_position or "").strip()
            or self.get_player_position(player_id)
            or ""
        )

    def _player_metadata(self, player_id: int) -> PlayerInfo | None:
        """Return save metadata enriched with authoritative native fields."""
        player_info = getattr(self, "_player_cache", {}).get(player_id)
        database = getattr(self, "playerbin_db", None)
        record = database.get(player_id) if database is not None else None
        if player_info is not None:
            if record is None:
                return player_info
            return PlayerInfo(
                player_id=player_info.player_id,
                name=player_info.name or record.name,
                print_name=player_info.print_name or record.print_name or record.name,
                position=record.registered_position or player_info.position,
                nationality=player_info.nationality,
                age=record.age or player_info.age,
            )
        if record is None:
            return None
        return PlayerInfo(
            player_id=record.player_id,
            name=record.name,
            print_name=record.print_name or record.name,
            position=record.registered_position,
            age=record.age,
        )


    def load(self, path: str | Path | None = None):
        """Load and parse data.dat from disk."""
        if path:
            self.path = Path(path)
        if not self.path or not self.path.exists():
            raise FileNotFoundError(f"data.dat not found: {self.path}")

        with open(self.path, "rb") as f:
            self._data = bytearray(f.read())

        logger.info(f"Loaded {len(self._data):,} bytes from {self.path}")
        self._parse_header()
        self._calculate_offsets()

    def load_bytes(self, data: bytes | bytearray):
        """Load from raw bytes (for testing)."""
        self._data = bytearray(data)
        self._parse_header()
        self._calculate_offsets()

    def _parse_header(self):
        """Read entry counts from the header."""
        if len(self._data) < HEADER_SIZE:
            raise ValueError(f"Data too small ({len(self._data)} bytes), expected at least {HEADER_SIZE}")

        self.player_count = struct.unpack_from("<H", self._data, HDR_PLAYER_COUNT)[0]
        self.team_count = struct.unpack_from("<H", self._data, HDR_TEAM_COUNT)[0]
        self.manager_count = struct.unpack_from("<H", self._data, HDR_MANAGER_COUNT)[0]
        self.stadium_count = struct.unpack_from("<H", self._data, HDR_STADIUM_COUNT)[0]
        self.competition_count = struct.unpack_from("<H", self._data, HDR_COMPETITION_COUNT)[0]
        self.unknown_count = struct.unpack_from("<H", self._data, HDR_UNKNOWN_COUNT)[0]
        self.team_player_count = struct.unpack_from("<H", self._data, HDR_TEAM_PLAYER_COUNT)[0]
        self.game_plan_count = struct.unpack_from("<H", self._data, HDR_GAME_PLAN_COUNT)[0]

        logger.info(
            f"Header: players={self.player_count}, teams={self.team_count}, "
            f"managers={self.manager_count}, stadiums={self.stadium_count}, "
            f"competitions={self.competition_count}, unknown={self.unknown_count}, "
            f"team_player={self.team_player_count}, game_plans={self.game_plan_count}"
        )

    def _calculate_offsets(self):
        """
        Calculate table start positions from fixed MAX slot sizes.

        PES21 allocates fixed-size blocks for each table regardless of how many
        entries are actually used. The header counts tell us how many are populated,
        but the block sizes are determined by the MAX constants.

        Tables are laid out sequentially:
        Header → Players (data+appearance interleaved) → Teams → Managers →
        Competitions → Stadiums → Unknown → Team-Player → Competition Entry → Game Plans
        """
        self.player_start = HEADER_SIZE  # 0x7C
        self.team_start = self.player_start + MAX_PLAYERS * PLAYER_TOTAL_SIZE
        self.manager_start = self.team_start + MAX_TEAMS * TEAM_ENTRY_SIZE
        self.competition_start = self.manager_start + MAX_MANAGERS * MANAGER_ENTRY_SIZE
        self.stadium_start = self.competition_start + MAX_COMPETITIONS * COMPETITION_ENTRY_SIZE
        self.unknown_start = self.stadium_start + MAX_STADIUMS * STADIUM_ENTRY_SIZE
        self.team_player_start = self.unknown_start + MAX_UNKNOWN * UNKNOWN_ENTRY_SIZE
        self.competition_entry_start = (
            self.team_player_start + MAX_TEAM_PLAYER * TEAM_PLAYER_ENTRY_SIZE
        )
        self.game_plan_start = (
            self.competition_entry_start + COMPETITION_SECTION_SIZE
        )

        logger.info(
            f"Calculated offsets: "
            f"players=0x{self.player_start:X}, "
            f"teams=0x{self.team_start:X}, "
            f"managers=0x{self.manager_start:X}, "
            f"competitions=0x{self.competition_start:X}, "
            f"stadiums=0x{self.stadium_start:X}, "
            f"unknown=0x{self.unknown_start:X}, "
            f"team_player=0x{self.team_player_start:X}, "
            f"competition_entry=0x{self.competition_entry_start:X}, "
            f"game_plan=0x{self.game_plan_start:X}"
        )

    # ──────────────────────────────────────────────────────────
    # Read operations
    # ──────────────────────────────────────────────────────────

    def _read_string(self, offset: int, max_len: int) -> str:
        """Read a null-terminated string from the data."""
        end = offset + max_len
        if end > len(self._data):
            end = len(self._data)
        raw = self._data[offset:end]
        # Find null terminator
        null_pos = raw.find(0)
        if null_pos >= 0:
            raw = raw[:null_pos]
        try:
            return raw.decode("utf-8", errors="replace")
        except Exception:
            return raw.decode("latin-1", errors="replace")


    def get_all_players(self, csv_path: Path | None = None, include_base_db: bool = True) -> dict[int, PlayerInfo]:
        """
        Load an optional external FL player catalog and merge edited save players.

        When the external reference is unavailable, names embedded in the
        selected save remain usable and any unknown roster IDs stay unmatched.
        The legacy CSV is used only for rostered IDs absent from the current
        reference. It is never allowed to reintroduce stale free agents.

        Returns:
            {player_id: PlayerInfo}
        """
        edited_players: dict[int, PlayerInfo] = {}
        edited_count = 0
        for i in range(self.player_count):
            entry_offset = self.player_start + i * PLAYER_TOTAL_SIZE

            if entry_offset + PLAYER_ENTRY_SIZE > len(self._data):
                logger.warning(f"Player entry {i} at 0x{entry_offset:X} exceeds data size")
                break

            player_id = struct.unpack_from("<I", self._data, entry_offset + PE_PLAYER_ID)[0]
            if player_id == 0:
                continue

            name = self._read_string(entry_offset + PE_PLAYER_NAME, 61)
            print_name = self._read_string(entry_offset + PE_PRINT_NAME, 61)
            raw_entry = self._data[
                entry_offset : entry_offset + PLAYER_ENTRY_SIZE
            ]
            age = (
                int.from_bytes(
                    raw_entry[PE_AGE_BYTE : PE_AGE_BYTE + 2], "little"
                )
                >> 7
            ) & 0x3F
            position_id = (
                int.from_bytes(
                    raw_entry[
                        PE_REGISTERED_POSITION_BYTE : PE_REGISTERED_POSITION_BYTE + 2
                    ],
                    "little",
                )
                >> 5
            ) & 0x0F
            position = (
                POSITION_NAMES[position_id]
                if position_id < len(POSITION_NAMES)
                else ""
            )
            edited_players[player_id] = PlayerInfo(
                player_id=player_id,
                name=name,
                print_name=print_name,
                position=position,
                age=age,
            )
            edited_count += 1

        if not include_base_db:
            return edited_players

        roster_ids = {
            player_id
            for roster in self.get_all_rosters().values()
            for player_id in roster.player_ids
            if player_id
        }
        if self.is_pes21_save:
            # PES 2021/T99 IDs come from the matching native Player.bin. The
            # bundled FL26 text catalog uses a different ID universe.
            current_path = None
            legacy_path = None
        else:
            current_path = (
                config.CURRENT_PLAYERS_FILE
                if config.CURRENT_PLAYERS_FILE.is_file()
                else None
            )
            legacy_path = csv_path or (
                config.PLAYERS_CSV_FILE if current_path is not None else None
            )

        database = getattr(self, "playerbin_db", None)
        playerbin_roster_ids = {
            player_id
            for player_id in roster_ids
            if database is not None and database.get(player_id) is not None
        }
        catalog_roster_ids = roster_ids - playerbin_roster_ids
        players, report = build_player_catalog(
            current_path=current_path,
            legacy_csv_path=legacy_path,
            edited_players=edited_players,
            roster_ids=catalog_roster_ids,
        )

        if database is not None:
            for player_id in sorted(playerbin_roster_ids):
                record = database.get(player_id)
                if record is None:
                    continue
                existing = players.get(player_id)
                if existing is None:
                    players[player_id] = PlayerInfo(
                        player_id=record.player_id,
                        name=record.name,
                        print_name=record.print_name or record.name,
                        position=record.registered_position,
                        age=record.age,
                    )
                else:
                    players[player_id] = PlayerInfo(
                        player_id=existing.player_id,
                        name=existing.name or record.name,
                        print_name=existing.print_name or record.print_name or record.name,
                        position=record.registered_position or existing.position,
                        nationality=existing.nationality,
                        age=record.age or existing.age,
                    )
            report = replace(
                report,
                roster_entries=len(roster_ids),
                positions=sum(
                    bool(players.get(player_id) and players[player_id].position)
                    for player_id in roster_ids
                ),
                ages=sum(
                    bool(players.get(player_id) and players[player_id].age)
                    for player_id in roster_ids
                ),
            )

        self._player_cache = players
        self.player_catalog_report = report
        logger.info(
            "Loaded %s current players, %s roster-only legacy fallbacks, and %s edited players",
            report.current_entries,
            report.legacy_roster_fallbacks,
            report.edited_entries,
        )
        logger.info(f"Total active players in database: {len(players)}")
        return players

    def get_all_team_info(self) -> dict[int, TeamInfo]:
        """
        Read all team entries (ID + name + abbreviation + manager_id).

        Returns:
            {team_id: TeamInfo}
        """
        teams = {}
        for i in range(self.team_count):
            entry_offset = self.team_start + i * TEAM_ENTRY_SIZE

            if entry_offset + TEAM_ENTRY_SIZE > len(self._data):
                logger.warning(f"Team entry {i} at 0x{entry_offset:X} exceeds data size")
                break

            team_id = struct.unpack_from("<I", self._data, entry_offset + TE_TEAM_ID)[0]
            manager_id = struct.unpack_from("<I", self._data, entry_offset + TE_MANAGER_ID)[0]
            name = self._read_string(entry_offset + TE_TEAM_NAME, 70)
            abbrev = self._read_string(entry_offset + TE_ABBREVIATION, 4)

            teams[team_id] = TeamInfo(
                team_id=team_id,
                name=name,
                abbreviation=abbrev,
                manager_id=manager_id,
            )

        logger.info(f"Read {len(teams)} teams")
        return teams

    def get_all_managers(self) -> dict[int, ManagerInfo]:
        """
        Read all manager entries from the Manager Entry table.

        Returns:
            {manager_id: ManagerInfo}
        """
        managers = {}
        for i in range(self.manager_count):
            entry_offset = self.manager_start + i * MANAGER_ENTRY_SIZE
            if entry_offset + MANAGER_ENTRY_SIZE > len(self._data):
                break

            mid = struct.unpack_from("<I", self._data, entry_offset + ME_MANAGER_ID)[0]
            nat = struct.unpack_from("<H", self._data, entry_offset + ME_NATIONALITY)[0]
            name = self._read_string(entry_offset + ME_MANAGER_NAME, 79)

            if mid != 0 or name:
                managers[mid] = ManagerInfo(
                    manager_id=mid,
                    name=name,
                    nationality=nat,
                )

        logger.info(f"Read {len(managers)} managers")
        return managers

    def get_league_divisions(self) -> list[list[int]]:
        """
        Read league division groupings from the Competition Entry section.

        Returns:
            List of lists of team IDs for each league division.
        """
        entry_start = self.competition_entry_start
        num_slots = COMPETITION_SECTION_SIZE // 4
        all_teams = self.get_all_team_info()

        divisions: list[list[int]] = []
        current_div: list[int] = []

        for i in range(num_slots):
            if entry_start + (i + 1) * 4 > len(self._data):
                break
            tid = struct.unpack_from("<I", self._data, entry_start + i * 4)[0]
            if tid != 0 and tid != 0xFFFF0300 and tid in all_teams:
                current_div.append(tid)
            else:
                if current_div:
                    divisions.append(current_div)
                    current_div = []
        if current_div:
            divisions.append(current_div)

        return divisions

    def get_club_team_ids(self) -> set[int]:
        """
        Get the set of all valid club team IDs in the game.
        Excludes national teams.
        """
        all_teams = self.get_all_team_info()
        league_divs = self.get_league_divisions()
        club_ids = set()
        for div in league_divs:
            club_ids.update(div)

        # Fallback: all teams with ID > 100 if divisions not found
        if not club_ids:
            club_ids = {tid for tid in all_teams if tid > 100}

        return club_ids

    def get_team_roster(self, team_id: int) -> TeamData | None:
        """
        Read the roster (player IDs + shirt numbers) for a specific team.

        Args:
            team_id: The team ID to look up.

        Returns:
            TeamData or None if team not found.
        """
        for i in range(self.team_player_count):
            entry_offset = self.team_player_start + i * TEAM_PLAYER_ENTRY_SIZE

            if entry_offset + TEAM_PLAYER_ENTRY_SIZE > len(self._data):
                break

            tid = struct.unpack_from("<I", self._data, entry_offset + TP_TEAM_ID)[0]
            if tid != team_id:
                continue

            return self._read_team_player_entry(entry_offset)

        return None

    def get_all_rosters(self) -> dict[int, TeamData]:
        """
        Read all team rosters.

        Returns:
            {team_id: TeamData}
        """
        rosters = {}
        for i in range(self.team_player_count):
            entry_offset = self.team_player_start + i * TEAM_PLAYER_ENTRY_SIZE

            if entry_offset + TEAM_PLAYER_ENTRY_SIZE > len(self._data):
                break

            td = self._read_team_player_entry(entry_offset)
            rosters[td.team_id] = td

        logger.info(f"Read {len(rosters)} team rosters")
        return rosters

    def _read_team_player_entry(self, offset: int) -> TeamData:
        """Parse a single Team-Player table entry."""
        team_id = struct.unpack_from("<I", self._data, offset + TP_TEAM_ID)[0]

        player_ids = []
        for j in range(TP_MAX_PLAYERS):
            pid = struct.unpack_from("<I", self._data, offset + TP_PLAYER_IDS + j * 4)[0]
            player_ids.append(pid)

        shirt_numbers = []
        for j in range(TP_MAX_PLAYERS):
            sn = struct.unpack_from("<H", self._data, offset + TP_SHIRT_NUMBERS + j * 2)[0]
            shirt_numbers.append(sn)

        return TeamData(
            team_id=team_id,
            player_ids=player_ids,
            shirt_numbers=shirt_numbers,
        )


    # ──────────────────────────────────────────────────────────
    # Save
    # ──────────────────────────────────────────────────────────

    def save(self, path: str | Path | None = None):
        """Write modified data back to disk."""
        save_path = Path(path) if path else self.path
        if not save_path:
            raise ValueError("No save path specified")

        save_path.parent.mkdir(parents=True, exist_ok=True)
        with open(save_path, "wb") as f:
            f.write(self._data)

        logger.info(f"Saved {len(self._data):,} bytes to {save_path}")

    def _validate_game_plan_semantics(
        self,
        offset: int,
        team_id: int,
        roster: TeamData,
        lineup: list[int],
        errors: list[str],
    ) -> int:
        if self.is_pes21_save:
            # The FL26 position-code invariant is not portable to PES 2021
            # native metadata; roster/table integrity remains validated below.
            return 0
        if getattr(self, "playerbin_db", None) is None:
            return 0
        starter_slots = lineup[: min(FIRST_TEAM_SLOT_COUNT, roster.roster_size)]
        checks = 0
        for role, slot in enumerate(starter_slots):
            player_id = roster.player_ids[slot] if 0 <= slot < TP_MAX_PLAYERS else 0
            if not player_id:
                continue
            registered_position = (
                self.get_player_position(player_id) or ""
            ).strip().upper()
            registered_code = _game_plan_position_code(registered_position)
            if registered_code is None:
                continue
            for preset_offset in GP_POSITION_PRESETS:
                for phase_offset in GP_POSITION_PHASE_OFFSETS:
                    position_address = (
                        offset
                        + preset_offset
                        + phase_offset
                        + role * GP_POSITION_ENTRY_SIZE
                    )
                    if position_address >= len(self._data):
                        continue
                    position_code = self._data[position_address]
                    checks += 1
                    if (
                        registered_code == 0 and position_code != 0
                    ):
                        phase_label = (
                            ""
                            if phase_offset == 0
                            else f" phase 0x{phase_offset:X}"
                        )
                        errors.append(
                            f"Team {team_id} game-plan preset 0x{preset_offset:X}"
                            f"{phase_label} assigns GK player {player_id} "
                            f"position code {position_code}"
                        )
                    elif registered_code != 0 and position_code == 0:
                        phase_label = (
                            ""
                            if phase_offset == 0
                            else f" phase 0x{phase_offset:X}"
                        )
                        errors.append(
                            f"Team {team_id} game-plan preset 0x{preset_offset:X}"
                            f"{phase_label} assigns {registered_position} player "
                            f"{player_id} goalkeeper position code 0"
                        )
        return checks

    # ──────────────────────────────────────────────────────────
    # Diagnostics
    # ──────────────────────────────────────────────────────────

    def validate_integrity(self) -> dict[str, object]:
        """Validate supported PES edit-file table bounds and cross-table roster invariants."""
        errors: list[str] = []
        warnings: list[str] = []

        expected_size = self.game_plan_start + MAX_GAME_PLANS * GAME_PLAN_ENTRY_SIZE
        if len(self._data) != expected_size:
            errors.append(
                f"data.dat size is {len(self._data):,}; expected {expected_size:,} bytes "
                "for the supported PES edit-file layout"
            )

        count_limits = {
            "players": (self.player_count, MAX_PLAYERS),
            "teams": (self.team_count, MAX_TEAMS),
            "managers": (self.manager_count, MAX_MANAGERS),
            "competitions": (self.competition_count, MAX_COMPETITIONS),
            "stadiums": (self.stadium_count, MAX_STADIUMS),
            "unknown": (self.unknown_count, MAX_UNKNOWN),
            "team-player": (self.team_player_count, MAX_TEAM_PLAYER),
            "game plans": (self.game_plan_count, MAX_GAME_PLANS),
        }
        for name, (count, maximum) in count_limits.items():
            if not 0 <= count <= maximum:
                errors.append(f"Header {name} count {count} exceeds block capacity {maximum}")

        edited_player_ids: set[int] = set()
        for i in range(min(self.player_count, MAX_PLAYERS)):
            offset = self.player_start + i * PLAYER_TOTAL_SIZE
            if offset + PLAYER_ENTRY_SIZE > len(self._data):
                errors.append(f"Player entry {i} exceeds data.dat bounds")
                break
            player_id = struct.unpack_from("<I", self._data, offset + PE_PLAYER_ID)[0]
            if player_id == 0:
                continue
            if player_id in edited_player_ids:
                errors.append(f"Duplicate edited-player ID {player_id}")
            edited_player_ids.add(player_id)

        team_ids: set[int] = set()
        for i in range(min(self.team_count, MAX_TEAMS)):
            offset = self.team_start + i * TEAM_ENTRY_SIZE
            if offset + TEAM_ENTRY_SIZE > len(self._data):
                errors.append(f"Team entry {i} exceeds data.dat bounds")
                break
            team_id = struct.unpack_from("<I", self._data, offset + TE_TEAM_ID)[0]
            if team_id in team_ids:
                errors.append(f"Duplicate team entry for team {team_id}")
            team_ids.add(team_id)

        rosters: dict[int, TeamData] = {}
        seen_team_ids: set[int] = set()
        for i in range(min(self.team_player_count, MAX_TEAM_PLAYER)):
            offset = self.team_player_start + i * TEAM_PLAYER_ENTRY_SIZE
            if offset + TEAM_PLAYER_ENTRY_SIZE > len(self._data):
                errors.append(f"Team-player entry {i} exceeds data.dat bounds")
                break
            roster = self._read_team_player_entry(offset)
            tid = roster.team_id
            if tid not in team_ids:
                errors.append(f"Team-player entry references unknown team {tid}")
            if tid in seen_team_ids:
                errors.append(f"Duplicate team-player entry for team {tid}")
            seen_team_ids.add(tid)
            rosters[tid] = roster

            nonzero_slots = [slot for slot, pid in enumerate(roster.player_ids) if pid != 0]
            if nonzero_slots:
                last_slot = nonzero_slots[-1]
                holes = [slot for slot in range(last_slot) if roster.player_ids[slot] == 0]
                if holes:
                    errors.append(f"Team {tid} roster has empty holes at slots {holes}")

            active_ids = [pid for pid in roster.player_ids if pid != 0]
            if len(active_ids) != len(set(active_ids)):
                errors.append(f"Team {tid} contains the same player more than once")

            active_shirts = [
                shirt
                for pid, shirt in zip(roster.player_ids, roster.shirt_numbers)
                if pid != 0 and shirt != 0
            ]
            if any(shirt > 999 for shirt in active_shirts):
                errors.append(f"Team {tid} contains a shirt number above 999")
            if len(active_shirts) != len(set(active_shirts)):
                errors.append(f"Team {tid} contains duplicate non-zero shirt numbers")
            if any(
                pid == 0 and shirt != 0
                for pid, shirt in zip(roster.player_ids, roster.shirt_numbers)
            ):
                # PES21 can retain stale shirt numbers in unused slots. They
                # are ignored by the game and must not block a local update.
                warnings.append(
                    f"Team {tid} has a shirt number assigned to an empty roster slot"
                )

        league_divisions = self.get_league_divisions()
        club_ids = {team_id for division in league_divisions for team_id in division}
        if not club_ids:
            errors.append("Competition membership does not identify any clubs")
        undersized_clubs = {
            tid: rosters[tid].roster_size if tid in rosters else 0
            for tid in club_ids
            if tid not in rosters
            or rosters[tid].roster_size < MIN_CLUB_ROSTER_SIZE
        }
        for tid, roster_size in sorted(undersized_clubs.items()):
            errors.append(
                f"Club {tid} roster has {roster_size} players; "
                f"minimum is {MIN_CLUB_ROSTER_SIZE}"
            )
        club_registrations: dict[int, list[int]] = {}
        for tid, roster in rosters.items():
            if tid not in club_ids:
                continue
            for pid in roster.roster:
                club_registrations.setdefault(pid, []).append(tid)
        duplicate_club_players = {
            pid: tids for pid, tids in club_registrations.items() if len(tids) > 1
        }
        for pid, tids in sorted(duplicate_club_players.items()):
            errors.append(f"Player {pid} is registered to multiple clubs: {tids}")

        checked_game_plans = 0
        semantic_position_checks = 0
        seen_game_plan_team_ids: set[int] = set()
        for i in range(min(self.game_plan_count, MAX_GAME_PLANS)):
            offset = self.game_plan_start + i * GAME_PLAN_ENTRY_SIZE
            if offset + GAME_PLAN_ENTRY_SIZE > len(self._data):
                errors.append(f"Game-plan entry {i} exceeds data.dat bounds")
                break
            tid = struct.unpack_from("<I", self._data, offset + GP_TEAM_ID)[0]
            if tid in seen_game_plan_team_ids:
                errors.append(f"Duplicate game-plan entry for team {tid}")
            seen_game_plan_team_ids.add(tid)
            roster = rosters.get(tid)
            if roster is None:
                errors.append(f"Game-plan entry references unknown team {tid}")
                continue
            if roster.roster_size == 0:
                continue

            checked_game_plans += 1
            active_slots = {slot for slot, pid in enumerate(roster.player_ids) if pid != 0}
            lineup = list(self._data[offset + GP_LINEUP : offset + GP_LINEUP + TP_MAX_PLAYERS])
            active_prefix = lineup[: len(active_slots)]
            if len(active_prefix) != len(set(active_prefix)) or set(active_prefix) != active_slots:
                errors.append(
                    f"Team {tid} game-plan active prefix does not map its roster one-to-one"
                )

            starters = lineup[: min(11, len(active_slots))]
            if len(starters) != len(set(starters)) or any(
                slot not in active_slots for slot in starters
            ):
                errors.append(f"Team {tid} game plan has duplicate or empty starting slots")
            semantic_position_checks += self._validate_game_plan_semantics(
                offset,
                tid,
                roster,
                lineup,
                errors,
            )

            role_offsets = list(GP_SINGLE_PLAYER_ROLES)
            role_offsets.extend(GP_ATTACK_PLAYERS + index for index in range(3))
            for role_offset in role_offsets:
                value = self._data[offset + role_offset]
                if value != 0xFF and value >= TP_MAX_PLAYERS:
                    errors.append(
                        f"Team {tid} game-plan role at 0x{role_offset:X} has invalid slot {value}"
                    )
                elif value != 0xFF and value not in active_slots:
                    errors.append(
                        f"Team {tid} game-plan role at 0x{role_offset:X} points to "
                        f"empty roster slot {value}"
                    )

        metrics = {
            "data_size": len(self._data),
            "expected_data_size": expected_size,
            "rosters": len(rosters),
            "clubs": len(club_ids),
            "duplicate_club_players": len(duplicate_club_players),
            "undersized_clubs": len(undersized_clubs),
            "checked_game_plans": checked_game_plans,
            "semantic_position_checks": semantic_position_checks,
        }
        return {
            "valid": not errors,
            "errors": errors,
            "warnings": warnings,
            "metrics": metrics,
        }

    def validate_offsets(self) -> dict:
        """
        Validate calculated offsets against known vanilla PES21 values.
        Useful for checking if FL26 has different structure.

        Returns:
            Dict with validation results.
        """
        vanilla = {
            "player_start": 0x7C,
            "team_start": 0x8ED2FC,
            "manager_start": 0x958DA4,
            "competition_start": 0x974C84,
            "stadium_start": 0x980D7C,
            "unknown_start": 0x983D38,
            "team_player_start": 0x9D4648,
            "competition_entry_start": 0xA08650,
            "game_plan_start": 0xA09880,
        }

        results = {}
        for name, expected in vanilla.items():
            actual = getattr(self, name)
            match = actual == expected
            results[name] = {
                "expected": f"0x{expected:X}",
                "actual": f"0x{actual:X}",
                "match": match,
                "diff": actual - expected,
            }

        return results

    def print_summary(self):
        """Print a human-readable summary of the file."""
        print(f"Data size: {len(self._data):,} bytes")
        print(f"Players:   {self.player_count}")
        print(f"Teams:     {self.team_count}")
        print(f"Managers:  {self.manager_count}")
        print(f"Stadiums:  {self.stadium_count}")
        print(f"Competitions: {self.competition_count}")
        print(f"Team-Player entries: {self.team_player_count}")
        print(f"Game plans: {self.game_plan_count}")
        print()
        print(f"Offsets:")
        print(f"  Players:     0x{self.player_start:08X}")
        print(f"  Teams:       0x{self.team_start:08X}")
        print(f"  Managers:    0x{self.manager_start:08X}")
        print(f"  Competitions:0x{self.competition_start:08X}")
        print(f"  Stadiums:    0x{self.stadium_start:08X}")
        print(f"  Unknown:     0x{self.unknown_start:08X}")
        print(f"  Team-Player: 0x{self.team_player_start:08X}")
        print(f"  League data: 0x{self.competition_entry_start:08X}")
        print(f"  Game Plans:  0x{self.game_plan_start:08X}")

        # Validate against vanilla
        results = self.validate_offsets()
        mismatches = [k for k, v in results.items() if not v["match"]]
        if mismatches:
            print(f"\n⚠ Offset mismatches vs vanilla PES21 ({len(mismatches)}):")
            for name in mismatches:
                r = results[name]
                print(f"  {name}: expected {r['expected']}, got {r['actual']} (diff: {r['diff']:+d})")
        else:
            print(f"\n✓ All offsets match vanilla PES21")
