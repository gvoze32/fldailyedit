"""
Binary edit file reader/writer.

Reads the decrypted data.dat and provides functions to:
- Parse the header to get entry counts
- Calculate table offsets dynamically (handles FL26 differences from vanilla PES21)
- Read all players (ID + name)
- Read all teams (ID + name)
- Read team rosters (from Team-Player Table)
- Move players between teams (the core transfer operation)

All offsets are calculated from the header — nothing is hardcoded except entry sizes
and field positions within entries, which are the same across PES20/21/FL26.
"""
import csv
import logging
import struct
from pathlib import Path

import config
from editor.models import ManagerInfo, PlayerInfo, TeamData, TeamInfo

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
TEAM_PLAYER_ENTRY_SIZE = 0x11C   # 284 bytes
GAME_PLAN_ENTRY_SIZE = 0x274     # 628 bytes

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
MAX_TEAM_PLAYER = 750      # (0xA08650 - 0x9D4648) / 284

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

# Team-Player table entry
TP_TEAM_ID = 0x00          # 4 bytes uint32 LE
TP_PLAYER_IDS = 0x04       # 40 × 4 bytes (160 bytes total)
TP_SHIRT_NUMBERS = 0xA4    # 40 × 2 bytes (80 bytes total)
TP_MAX_PLAYERS = 40

# Game plan entry
GP_TEAM_ID = 0x000         # 4 bytes uint32 LE
GP_LINEUP = 0x1E4          # 40 bytes (40 × 1 byte index IDs)
GP_LONG_FK = 0x209         # 1 byte index ID
GP_SHORT_FK = 0x20A        # 1 byte index ID
GP_FK_2 = 0x20B            # 1 byte index ID
GP_LEFT_CK = 0x20C         # 1 byte index ID
GP_RIGHT_CK = 0x20D        # 1 byte index ID
GP_PK = 0x20E              # 1 byte index ID
GP_ATTACK_PLAYERS = 0x20F  # 3 bytes (3 × 1 byte index IDs)
GP_CAPTAIN = 0x212         # 1 byte index ID


def assign_smart_shirt_number(
    used_numbers: set[int],
    preferred_number: int | None = None,
    position: str = "",
    is_gk: bool = False,
) -> int:
    """
    Allocate a smart, conflict-free shirt number (1-99).

    1. If preferred_number is specified (1-99) and vacant -> allocate it.
    2. Otherwise, allocate the most position-appropriate vacant number:
       - Goalkeeper: [1, 12, 13, 22, 23, 25, 30, 31, 33, 40, 99, 1..99]
       - Defender: [2, 3, 4, 5, 6, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30..99]
       - Midfielder: [6, 7, 8, 10, 11, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29..99]
       - Forward: [9, 7, 10, 11, 14, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30..99]
    3. Fallback: lowest available integer 1-99.
    """
    if preferred_number and 1 <= preferred_number <= 99 and preferred_number not in used_numbers:
        return preferred_number

    pos_upper = (position or "").strip().upper()

    if is_gk or pos_upper == "GK":
        priority = [1, 12, 13, 22, 23, 25, 30, 31, 33, 40, 99]
    elif pos_upper in ("CB", "LB", "RB", "CB/LB", "CB/RB", "LWB", "RWB"):
        priority = [2, 3, 4, 5, 6, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30]
    elif pos_upper in ("CMF", "DMF", "AMF", "LM", "RM", "DM", "AM", "CM"):
        priority = [6, 7, 8, 10, 11, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30]
    elif pos_upper in ("CF", "SS", "LWF", "RWF", "ST", "LW", "RW"):
        priority = [9, 7, 10, 11, 14, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30]
    else:
        priority = [7, 8, 9, 10, 11, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30]

    for num in priority:
        if num not in used_numbers:
            return num

    for num in range(1, 100):
        if num not in used_numbers:
            return num

    return 99



class EditFile:
    """
    Reads and modifies a decrypted PES 2021 / FL26 data.dat file.

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
        self.game_plan_start: int = 0

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
        # Game plan comes after team-player table + competition entry section
        # Competition entry section is variable, so we calculate game plan start from the end
        # For now, calculate based on team-player end
        self.game_plan_start = self.team_player_start + MAX_TEAM_PLAYER * TEAM_PLAYER_ENTRY_SIZE

        logger.info(
            f"Calculated offsets: "
            f"players=0x{self.player_start:X}, "
            f"teams=0x{self.team_start:X}, "
            f"managers=0x{self.manager_start:X}, "
            f"competitions=0x{self.competition_start:X}, "
            f"stadiums=0x{self.stadium_start:X}, "
            f"unknown=0x{self.unknown_start:X}, "
            f"team_player=0x{self.team_player_start:X}, "
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
        Read all player entries (ID + name only).
        First loads the global FL26 player database from CSV (if include_base_db is True),
        then merges any custom/edited players found in the edit file.

        Returns:
            {player_id: PlayerInfo}
        """
        players: dict[int, PlayerInfo] = {}

        # Step 1: Load base player database from CSV if available and requested
        csv_file = csv_path or getattr(config, "PLAYERS_CSV_FILE", None)
        if include_base_db and csv_file and Path(csv_file).exists():
            try:
                with open(csv_file, "r", encoding="utf-8-sig") as f:
                    reader = csv.reader(f)
                    header = next(reader, None)
                    for row in reader:
                        if len(row) >= 2 and row[0].strip().isdigit():
                            pid = int(row[0].strip())
                            pname = row[1].strip()
                            ovr = 0
                            pos = ""
                            if len(row) >= 3:
                                val = row[2].strip()
                                if val.isdigit():
                                    ovr = int(val)
                                else:
                                    pos = val
                            if len(row) >= 4:
                                val = row[3].strip()
                                if val.isdigit():
                                    ovr = int(val)
                                else:
                                    pos = val
                            players[pid] = PlayerInfo(
                                player_id=pid, name=pname, print_name=pname, overall_rating=ovr, position=pos
                            )
                logger.info(f"Loaded {len(players)} players from database CSV ({csv_file})")
            except Exception as e:
                logger.warning(f"Failed to load player database CSV: {e}")

        # Step 2: Merge custom edited players from the edit file
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

            players[player_id] = PlayerInfo(
                player_id=player_id,
                name=name,
                print_name=print_name,
            )
            edited_count += 1

        if edited_count > 0:
            logger.info(f"Merged {edited_count} custom/edited players from save file")
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

    def get_team_manager(self, team_id: int) -> int | None:
        """Get the assigned manager ID for a team."""
        team_info = self.get_all_team_info().get(team_id)
        return team_info.manager_id if team_info else None

    def set_team_manager(self, team_id: int, manager_id: int) -> bool:
        """Set the assigned manager ID for a team."""
        for i in range(self.team_count):
            entry_offset = self.team_start + i * TEAM_ENTRY_SIZE
            if entry_offset + TEAM_ENTRY_SIZE > len(self._data):
                break
            tid = struct.unpack_from("<I", self._data, entry_offset + TE_TEAM_ID)[0]
            if tid == team_id:
                struct.pack_into("<I", self._data, entry_offset + TE_MANAGER_ID, manager_id)
                logger.info(f"Assigned manager ID {manager_id} to team {team_id}")
                return True
        return False

    def get_league_divisions(self) -> list[list[int]]:
        """
        Read the 29 league division groupings from offset 0xA08650.

        Returns:
            List of lists of team IDs for each league division.
        """
        entry_start = 0xA08650
        entry_size = 0x1230
        num_slots = entry_size // 4
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
    # Write operations
    # ──────────────────────────────────────────────────────────

    def find_player_team(self, player_id: int, club_only: bool = True) -> int | None:
        """Find the team ID that currently has this player registered."""
        club_ids = self.get_club_team_ids() if club_only else None
        for i in range(self.team_player_count):
            entry_offset = self.team_player_start + i * TEAM_PLAYER_ENTRY_SIZE
            if entry_offset + TEAM_PLAYER_ENTRY_SIZE > len(self._data):
                break
            tid = struct.unpack_from("<I", self._data, entry_offset + TP_TEAM_ID)[0]
            if club_ids and tid not in club_ids:
                continue
            for j in range(TP_MAX_PLAYERS):
                pid = struct.unpack_from("<I", self._data, entry_offset + TP_PLAYER_IDS + j * 4)[0]
                if pid == player_id:
                    return tid
        return None

    def find_overflow_release_candidate(
        self, team_id: int, exclude_player_id: int | None = None
    ) -> tuple[int, int]:
        """
        Find the best player to release to Free Agent when team roster is full (40/40).

        Selection criteria:
        1. Prioritizes players with the lowest overall ability (OVR).
        2. Protects starters (slots 0-10) and primary substitutes (slots 11-17) if possible.
        3. For players with equal or unknown ability, chooses the deepest reserve slot (closest to slot 39).

        Returns:
            (slot_index, player_id)
        """
        to_entry = self._find_team_player_entry_offset(team_id)
        if to_entry is None:
            return 39, 0

        to_roster = self._read_team_player_entry(to_entry)
        active_slots = [
            (slot, pid)
            for slot, pid in enumerate(to_roster.player_ids)
            if pid != 0 and pid != exclude_player_id
        ]
        if not active_slots:
            return 39, 0

        if not hasattr(self, "_player_cache") or not self._player_cache:
            self._player_cache = self.get_all_players()

        # Count total goalkeepers in active roster to protect minimum GK requirement
        total_gks = 0
        for slot, pid in active_slots:
            pinfo = self._player_cache.get(pid)
            if (pinfo and pinfo.is_goalkeeper) or slot == 0:
                total_gks += 1

        def candidate_sort_key(item: tuple[int, int]):
            slot, pid = item
            pinfo = self._player_cache.get(pid)
            ovr = pinfo.overall_rating if pinfo and pinfo.overall_rating > 0 else 999
            is_gk = (pinfo.is_goalkeeper if pinfo else False) or (slot == 0)

            # Role & Positional Tier:
            # Tier 3: Starters (slots 0-10) -> Strictly protected
            # Tier 2: Substitutes (slots 11-17) or Protected GK (min 2 GKs rule)
            # Tier 0: Deep Reserves (slots 18-39) -> Normal candidate pool
            if slot < 11:
                tier = 3
            elif slot < 18:
                tier = 2
            elif is_gk and total_gks <= 2:
                tier = 2  # Protect minimum 2 GKs rule
            else:
                tier = 0

            # Position-adjusted effective rating:
            # Goalkeepers have different stat scaling and specialist value.
            # Give reserve GKs +10 effective rating bonus so outfield surplus reserves
            # are preferred for release over specialist goalkeepers.
            effective_ovr = ovr + (10 if is_gk else 0)

            return (tier, effective_ovr, -slot)

        best_slot, best_pid = min(active_slots, key=candidate_sort_key)
        return best_slot, best_pid

    def move_player(
        self,
        player_id: int,
        from_team_id: int,
        to_team_id: int,
        shirt_number: int | None = None,
        preferred_shirt_number: int | None = None,
        position: str = "",
    ) -> bool:
        """
        Transfer a player from one team to another.

        Steps:
        1. Find player in source team's roster
        2. Remove from source (compact slots by shifting last non-zero entry)
        3. Add to destination (first empty slot or auto-release lowest ability player if full)
        4. Assign a smart, conflict-free shirt number based on position & preference
        5. Update game plan index IDs and repair captaincy / set-piece roles if needed

        Args:
            player_id: The player to transfer.
            from_team_id: Source team ID.
            to_team_id: Destination team ID.
            shirt_number: Optional preferred kit number.
            preferred_shirt_number: Optional alias for shirt_number.
            position: Player position label (e.g. 'GK', 'ST', 'CB').

        Returns:
            True if transfer succeeded, False otherwise.
        """
        target_shirt = shirt_number if shirt_number is not None else preferred_shirt_number

        # Find team-player entries
        from_entry = self._find_team_player_entry_offset(from_team_id)
        to_entry = self._find_team_player_entry_offset(to_team_id)

        if from_entry is None:
            logger.error(f"Source team {from_team_id} not found in Team-Player table")
            return False
        if to_entry is None:
            logger.error(f"Destination team {to_team_id} not found in Team-Player table")
            return False

        # Read current rosters
        from_roster = self._read_team_player_entry(from_entry)
        to_roster = self._read_team_player_entry(to_entry)

        # Validate
        player_idx = from_roster.player_index(player_id)
        if player_idx == -1:
            logger.error(f"Player {player_id} not found on team {from_team_id}")
            return False

        if to_roster.has_player(player_id):
            logger.warning(f"Player {player_id} already on destination team {to_team_id}")
            return False

        # If no preferred shirt number was passed, use their existing shirt number from source
        if target_shirt is None and player_idx < len(from_roster.shirt_numbers):
            old_sn = from_roster.shirt_numbers[player_idx]
            if old_sn > 0:
                target_shirt = old_sn

        # --- Step 1: Remove from source ---
        # Find last non-zero player in source roster
        last_idx = -1
        for k in range(TP_MAX_PLAYERS - 1, -1, -1):
            if from_roster.player_ids[k] != 0:
                last_idx = k
                break

        if last_idx == player_idx:
            # Player is the last one — just zero out
            self._write_player_slot(from_entry, player_idx, 0, 0)
            self._update_game_plan_after_removal(from_team_id, player_idx, -1)
        elif last_idx > player_idx:
            # Move last player into the vacated slot (compact)
            self._write_player_slot(
                from_entry, player_idx,
                from_roster.player_ids[last_idx],
                from_roster.shirt_numbers[last_idx],
            )
            # Zero out the last slot
            self._write_player_slot(from_entry, last_idx, 0, 0)

            # Update game plan for source team (index IDs shifted & repaired)
            self._update_game_plan_after_removal(from_team_id, player_idx, last_idx)
        else:
            # player_idx > last_idx shouldn't happen, but handle gracefully
            self._write_player_slot(from_entry, player_idx, 0, 0)
            self._update_game_plan_after_removal(from_team_id, player_idx, -1)

        # Re-read to_roster in case from_entry == to_entry
        to_roster = self._read_team_player_entry(to_entry)

        # --- Step 2: Handle Full Roster (40 slots limit) ---
        if to_roster.is_full:
            slot_to_rel, pid_to_rel = self.find_overflow_release_candidate(to_team_id, exclude_player_id=player_id)
            logger.warning(
                f"Destination team {to_team_id} is full (40/40). "
                f"Auto-releasing lowest ability player {pid_to_rel} (slot {slot_to_rel}) to Free Agent."
            )
            self.release_player(pid_to_rel, to_team_id)
            to_roster = self._read_team_player_entry(to_entry)

        # --- Step 3: Add to destination with Smart Shirt Number ---
        dest_slot = to_roster.first_empty_slot()
        if dest_slot == -1:
            logger.error(f"No empty slot in destination team {to_team_id}")
            return False

        if not hasattr(self, "_player_cache") or not self._player_cache:
            self._player_cache = self.get_all_players()
        pinfo = self._player_cache.get(player_id)
        effective_pos = position or (pinfo.position if pinfo else "")
        is_gk = (pinfo.is_goalkeeper if pinfo else False) or (effective_pos.upper() == "GK")

        used_numbers = {sn for sn in to_roster.shirt_numbers if sn > 0}
        shirt_num = assign_smart_shirt_number(
            used_numbers=used_numbers,
            preferred_number=target_shirt,
            position=effective_pos,
            is_gk=is_gk,
        )

        self._write_player_slot(to_entry, dest_slot, player_id, shirt_num)

        logger.info(
            f"Transfer: player {player_id} moved from team {from_team_id} "
            f"(slot {player_idx}) to team {to_team_id} (slot {dest_slot}, shirt #{shirt_num})"
        )
        return True

    def release_player(self, player_id: int, from_team_id: int) -> bool:
        """
        Release a player to Free Agent (or when moving to an unrepresented club).

        Removes the player from the team's 40-slot roster and compacts the slots.
        In PES21, any registered player not assigned to a club automatically
        becomes a Free Agent.

        Args:
            player_id: Player ID to release.
            from_team_id: Team ID to remove player from.

        Returns:
            True if released successfully, False otherwise.
        """
        from_entry = self._find_team_player_entry_offset(from_team_id)
        if from_entry is None:
            logger.error(f"Team {from_team_id} not found in Team-Player table")
            return False

        from_roster = self._read_team_player_entry(from_entry)
        player_idx = from_roster.player_index(player_id)
        if player_idx == -1:
            logger.error(f"Player {player_id} not found on team {from_team_id}")
            return False

        # Find last non-zero player in roster
        last_idx = -1
        for k in range(TP_MAX_PLAYERS - 1, -1, -1):
            if from_roster.player_ids[k] != 0:
                last_idx = k
                break

        if last_idx == player_idx:
            self._write_player_slot(from_entry, player_idx, 0, 0)
            self._update_game_plan_after_removal(from_team_id, player_idx, -1)
        elif last_idx > player_idx:
            self._write_player_slot(
                from_entry, player_idx,
                from_roster.player_ids[last_idx],
                from_roster.shirt_numbers[last_idx],
            )
            self._write_player_slot(from_entry, last_idx, 0, 0)
            self._update_game_plan_after_removal(from_team_id, player_idx, last_idx)
        else:
            self._write_player_slot(from_entry, player_idx, 0, 0)
            self._update_game_plan_after_removal(from_team_id, player_idx, -1)

        logger.info(f"Released player {player_id} from team {from_team_id} (now Free Agent)")
        return True

    def add_player(
        self,
        player_id: int,
        to_team_id: int,
        shirt_number: int | None = None,
        preferred_shirt_number: int | None = None,
        position: str = "",
    ) -> bool:
        """
        Sign a player from Free Agent into a team.

        Args:
            player_id: Player ID to add.
            to_team_id: Destination team ID.
            shirt_number: Optional preferred kit number.
            preferred_shirt_number: Optional alias for shirt_number.
            position: Player position label.

        Returns:
            True if added successfully, False otherwise.
        """
        target_shirt = shirt_number if shirt_number is not None else preferred_shirt_number

        to_entry = self._find_team_player_entry_offset(to_team_id)
        if to_entry is None:
            logger.error(f"Team {to_team_id} not found in Team-Player table")
            return False

        to_roster = self._read_team_player_entry(to_entry)
        if to_roster.has_player(player_id):
            logger.warning(f"Player {player_id} already on team {to_team_id}")
            return False

        if to_roster.is_full:
            slot_to_rel, pid_to_rel = self.find_overflow_release_candidate(to_team_id, exclude_player_id=player_id)
            logger.warning(
                f"Team {to_team_id} roster is full (40/40). "
                f"Auto-releasing lowest ability player {pid_to_rel} (slot {slot_to_rel}) to Free Agent."
            )
            self.release_player(pid_to_rel, to_team_id)
            to_roster = self._read_team_player_entry(to_entry)

        dest_slot = to_roster.first_empty_slot()
        if dest_slot == -1:
            logger.error(f"Team {to_team_id} roster is full (40 players)")
            return False

        if not hasattr(self, "_player_cache") or not self._player_cache:
            self._player_cache = self.get_all_players()
        pinfo = self._player_cache.get(player_id)
        effective_pos = position or (pinfo.position if pinfo else "")
        is_gk = (pinfo.is_goalkeeper if pinfo else False) or (effective_pos.upper() == "GK")

        used_numbers = {sn for sn in to_roster.shirt_numbers if sn > 0}
        shirt_num = assign_smart_shirt_number(
            used_numbers=used_numbers,
            preferred_number=target_shirt,
            position=effective_pos,
            is_gk=is_gk,
        )

        self._write_player_slot(to_entry, dest_slot, player_id, shirt_num)
        logger.info(f"Signed player {player_id} to team {to_team_id} (slot {dest_slot}, shirt #{shirt_num})")
        return True

    def _find_team_player_entry_offset(self, team_id: int) -> int | None:
        """Find the byte offset of a team's Team-Player table entry."""
        for i in range(self.team_player_count):
            offset = self.team_player_start + i * TEAM_PLAYER_ENTRY_SIZE
            if offset + 4 > len(self._data):
                break
            tid = struct.unpack_from("<I", self._data, offset + TP_TEAM_ID)[0]
            if tid == team_id:
                return offset
        return None

    def _write_player_slot(self, entry_offset: int, slot_idx: int, player_id: int, shirt_num: int):
        """Write a player ID and shirt number to a specific slot in a Team-Player entry."""
        pid_offset = entry_offset + TP_PLAYER_IDS + slot_idx * 4
        sn_offset = entry_offset + TP_SHIRT_NUMBERS + slot_idx * 2

        struct.pack_into("<I", self._data, pid_offset, player_id)
        struct.pack_into("<H", self._data, sn_offset, shirt_num)

    def _update_game_plan_after_removal(self, team_id: int, removed_idx: int, replacement_idx: int):
        """
        Game Plan Doctor: Repair and maintain squad tactics integrity when a player is removed.

        When slot[removed_idx] is replaced by slot[replacement_idx] (last player),
        1. Any game plan reference to replacement_idx becomes removed_idx.
        2. If Captain or set-piece takers referenced the removed player (or an invalid slot),
           reassigns captaincy and set pieces to the highest rated remaining players.
        3. Ensures Lineup index array contains valid slot indices.
        """
        gp_offset = self._find_game_plan_offset(team_id)
        if gp_offset is None or gp_offset + GAME_PLAN_ENTRY_SIZE > len(self._data):
            return

        team_roster = self.get_team_roster(team_id)
        if not team_roster:
            return

        active_slots = [i for i, pid in enumerate(team_roster.player_ids) if pid != 0]
        if not active_slots:
            return

        if not hasattr(self, "_player_cache") or not self._player_cache:
            self._player_cache = self.get_all_players()

        # Find best captain candidate (highest OVR starter or active player)
        def player_ovr(slot_idx: int) -> int:
            pid = team_roster.player_ids[slot_idx]
            p = self._player_cache.get(pid)
            return p.overall_rating if p and p.overall_rating > 0 else 50

        best_captain_slot = max(active_slots, key=lambda s: (1 if s < 11 else 0, player_ovr(s)))
        best_fk_slot = max(active_slots, key=lambda s: player_ovr(s))

        # Re-map 1-byte role index fields
        role_offsets = [
            GP_CAPTAIN,
            GP_LONG_FK,
            GP_SHORT_FK,
            GP_FK_2,
            GP_LEFT_CK,
            GP_RIGHT_CK,
            GP_PK,
        ]

        for roff in role_offsets:
            target_off = gp_offset + roff
            if target_off < len(self._data):
                val = self._data[target_off]
                if val == replacement_idx and replacement_idx >= 0:
                    self._data[target_off] = removed_idx
                elif val == removed_idx or val not in active_slots:
                    # Pointed to departed player or invalid index -> reassign!
                    fallback_slot = best_captain_slot if roff == GP_CAPTAIN else best_fk_slot
                    self._data[target_off] = fallback_slot

        # Repair Players to Join Attack (3 bytes)
        att_off = gp_offset + GP_ATTACK_PLAYERS
        for b in range(3):
            if att_off + b < len(self._data):
                val = self._data[att_off + b]
                if val == replacement_idx and replacement_idx >= 0:
                    self._data[att_off + b] = removed_idx
                elif val == removed_idx or (val != 0xFF and val not in active_slots):
                    self._data[att_off + b] = 0xFF  # None

        # Re-map Lineup (40 bytes)
        lineup_offset = gp_offset + GP_LINEUP
        for i in range(TP_MAX_PLAYERS):
            if lineup_offset + i < len(self._data):
                idx_byte = self._data[lineup_offset + i]
                if idx_byte == replacement_idx and replacement_idx >= 0:
                    self._data[lineup_offset + i] = removed_idx

    def _find_game_plan_offset(self, team_id: int) -> int | None:
        """Find the byte offset of a team's game plan entry."""
        # game_plan_start is after team-player table + competition entry section
        # It's already calculated correctly in _calculate_offsets
        # Note: there's a 4656-byte (0x1230) competition entry section between
        # team-player table and game plans, which game_plan_start doesn't account for.
        # The actual game plan base = team_player_end + 0x1230
        competition_entry_size = 0x1230  # 4656 bytes flat section
        gp_base = self.game_plan_start + competition_entry_size

        for i in range(self.game_plan_count):
            offset = gp_base + i * GAME_PLAN_ENTRY_SIZE
            if offset + 4 > len(self._data):
                break
            tid = struct.unpack_from("<I", self._data, offset + GP_TEAM_ID)[0]
            if tid == team_id:
                return offset
        return None

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

    # ──────────────────────────────────────────────────────────
    # Diagnostics
    # ──────────────────────────────────────────────────────────

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
