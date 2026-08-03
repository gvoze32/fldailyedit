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
import logging
import struct
from pathlib import Path

import config
from editor.models import ManagerInfo, PlayerInfo, TeamData, TeamInfo
from editor.player_catalog import PlayerCatalogReport, build_player_catalog

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
COMPETITION_SECTION_SIZE = 0x1230  # 4656-byte flat league-membership section
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
MAX_GAME_PLANS = 750       # fixed block; FL26 currently populates 749 entries

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
GP_LINEUP = 0x1E4          # 40 bytes (40 × 1 byte index IDs, offsets 0x1E4 to 0x20B)
GP_LEFT_CK = 0x20C         # 1 byte index ID
GP_RIGHT_CK = 0x20D        # 1 byte index ID
GP_PK = 0x20E              # 1 byte index ID
GP_ATTACK_PLAYERS = 0x20F  # 3 bytes (3 × 1 byte index IDs: 0x20F, 0x210, 0x211)
GP_CAPTAIN = 0x212         # 1 byte index ID

GP_SINGLE_PLAYER_ROLES = (
    GP_LEFT_CK,
    GP_RIGHT_CK,
    GP_PK,
    GP_CAPTAIN,
)


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
        self.competition_entry_start: int = 0
        self.game_plan_start: int = 0
        self.player_catalog_report: PlayerCatalogReport | None = None

        # Track players transferred in the current session to protect them from overflow auto-release
        self.transferred_player_ids: set[int] = set()

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
        Load the current FL26 player catalog and merge edited save players.

        The legacy CSV is used only for rostered IDs absent from the current
        SmokePatch reference. It is never allowed to reintroduce stale free agents.

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

            edited_players[player_id] = PlayerInfo(
                player_id=player_id,
                name=name,
                print_name=print_name,
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
        players, report = build_player_catalog(
            current_path=config.CURRENT_PLAYERS_FILE,
            legacy_csv_path=csv_path or config.PLAYERS_CSV_FILE,
            edited_players=edited_players,
            roster_ids=roster_ids,
        )
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
    # Write operations
    # ──────────────────────────────────────────────────────────

    def find_player_team(self, player_id: int, club_only: bool = True) -> int | None:
        """Find the team ID that currently has this player registered."""
        teams = self.find_player_teams(player_id, club_only=club_only)
        return teams[0] if teams else None

    def find_player_teams(self, player_id: int, club_only: bool = True) -> list[int]:
        """Return every team containing a player, preserving table order."""
        club_ids = self.get_club_team_ids() if club_only else None
        teams: list[int] = []
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
                    teams.append(tid)
                    break
        return teams

    def find_overflow_release_candidate(
        self,
        team_id: int,
        exclude_player_id: int | None = None,
        roster_player_ids: list[int] | None = None,
        protected_player_ids: set[int] | None = None,
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
        if roster_player_ids is None:
            to_entry = self._find_team_player_entry_offset(team_id)
            if to_entry is None:
                return 39, 0
            player_ids = self._read_team_player_entry(to_entry).player_ids
        else:
            player_ids = roster_player_ids
        # Protect both the current incoming player and any player transferred in this run
        protected_ids = (
            {exclude_player_id}
            | getattr(self, "transferred_player_ids", set())
            | (protected_player_ids or set())
        )
        active_slots = [
            (slot, pid)
            for slot, pid in enumerate(player_ids)
            if pid != 0 and pid not in protected_ids
        ]
        # If all players in squad were transferred in this run, fallback to all except current
        if not active_slots:
            active_slots = [
                (slot, pid)
                for slot, pid in enumerate(player_ids)
                if pid != 0 and pid != exclude_player_id
            ]
        if not active_slots:
            return 39, 0

        if not hasattr(self, "_player_cache") or not self._player_cache:
            # Overflow release is unsafe without caller-supplied metadata.
            return 39, 0

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

    def get_player_shirt_number(self, team_id: int, player_id: int) -> int | None:
        """Return a player's current shirt number, or None when not registered."""
        roster = self.get_team_roster(team_id)
        if roster is None:
            return None
        idx = roster.player_index(player_id)
        if idx == -1 or idx >= len(roster.shirt_numbers):
            return None
        return roster.shirt_numbers[idx]

    def update_player_shirt_number(self, team_id: int, player_id: int, shirt_number: int) -> bool:
        """Update a player's shirt number directly without transferring."""
        if not 1 <= shirt_number <= 999:
            logger.error(f"Invalid shirt number {shirt_number}; expected 1..999")
            return False
        entry = self._find_team_player_entry_offset(team_id)
        if entry is None:
            return False
        
        roster = self._read_team_player_entry(entry)
        idx = roster.player_index(player_id)
        if idx == -1:
            return False
            
        # Avoid unnecessary writes
        if roster.shirt_numbers[idx] == shirt_number:
            return True

        if any(
            pid != 0 and slot != idx and number == shirt_number
            for slot, (pid, number) in enumerate(zip(roster.player_ids, roster.shirt_numbers))
        ):
            logger.warning(f"Shirt #{shirt_number} is already used on team {team_id}")
            return False
            
        self._write_player_slot(entry, idx, player_id, shirt_number)
        logger.debug(f"Updated player {player_id} on team {team_id} to shirt #{shirt_number}")
        return True

    def move_player(
        self,
        player_id: int,
        from_team_id: int,
        to_team_id: int,
        shirt_number: int | None = None,
        preferred_shirt_number: int | None = None,
        position: str = "",
        allow_overflow_release: bool = False,
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

        if from_team_id == to_team_id:
            logger.error(f"Source and destination are the same team ({from_team_id})")
            return False

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

        # A player may also be registered for a national team, but must never
        # occur in two club rosters. Refuse to amplify a corrupt input.
        current_clubs = self.find_player_teams(player_id, club_only=True)
        unexpected_clubs = [tid for tid in current_clubs if tid != from_team_id]
        if unexpected_clubs:
            logger.error(
                f"Player {player_id} is already registered to club(s) {unexpected_clubs}; "
                "transfer aborted"
            )
            return False

        # If no preferred shirt number was passed, use their existing shirt number from source
        if target_shirt is None and player_idx < len(from_roster.shirt_numbers):
            old_sn = from_roster.shirt_numbers[player_idx]
            if old_sn > 0:
                target_shirt = old_sn

        overflow_pid: int | None = None
        if to_roster.is_full:
            if not allow_overflow_release:
                logger.error(
                    f"Destination team {to_team_id} is full (40/40); "
                    "overflow release was not authorized"
                )
                return False
            _, overflow_pid = self.find_overflow_release_candidate(
                to_team_id, exclude_player_id=player_id
            )
            if overflow_pid == 0:
                logger.error(f"No safe overflow release candidate for team {to_team_id}")
                return False

            logger.warning(
                f"Destination team {to_team_id} is full (40/40). "
                f"Auto-releasing lowest ability player {overflow_pid} to Free Agent."
            )
            if not self.release_player(overflow_pid, to_team_id):
                logger.error(f"Could not release overflow player {overflow_pid} from team {to_team_id}")
                return False
            to_roster = self._read_team_player_entry(to_entry)

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

        # --- Step 2: Add to destination with Smart Shirt Number ---
        dest_slot = to_roster.first_empty_slot()
        if dest_slot == -1:
            logger.error(f"No empty slot in destination team {to_team_id}")
            return False

        pinfo = getattr(self, "_player_cache", {}).get(player_id)
        effective_pos = position or (pinfo.position if pinfo else "")
        is_gk = (pinfo.is_goalkeeper if pinfo else False) or (effective_pos.upper() == "GK")

        used_numbers = {sn for sn in to_roster.shirt_numbers if sn > 0}
        shirt_num = assign_smart_shirt_number(
            used_numbers=used_numbers,
            preferred_number=target_shirt,
            position=effective_pos,
            is_gk=is_gk,
        )

        self.transferred_player_ids.add(player_id)
        self._write_player_slot(to_entry, dest_slot, player_id, shirt_num)
        self._update_game_plan_after_addition(to_team_id, dest_slot)

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
        allow_overflow_release: bool = False,
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

        current_clubs = self.find_player_teams(player_id, club_only=True)
        if current_clubs:
            logger.error(
                f"Player {player_id} is already registered to club(s) {current_clubs}; "
                f"cannot add to team {to_team_id} as a free agent"
            )
            return False

        if to_roster.is_full:
            if not allow_overflow_release:
                logger.error(
                    f"Team {to_team_id} roster is full (40/40); "
                    "overflow release was not authorized"
                )
                return False
            slot_to_rel, pid_to_rel = self.find_overflow_release_candidate(to_team_id, exclude_player_id=player_id)
            if pid_to_rel == 0:
                logger.error(f"No safe overflow release candidate for team {to_team_id}")
                return False
            logger.warning(
                f"Team {to_team_id} roster is full (40/40). "
                f"Auto-releasing lowest ability player {pid_to_rel} (slot {slot_to_rel}) to Free Agent."
            )
            if not self.release_player(pid_to_rel, to_team_id):
                logger.error(f"Could not release overflow player {pid_to_rel} from team {to_team_id}")
                return False
            to_roster = self._read_team_player_entry(to_entry)

        dest_slot = to_roster.first_empty_slot()
        if dest_slot == -1:
            logger.error(f"Team {to_team_id} roster is full (40 players)")
            return False

        pinfo = getattr(self, "_player_cache", {}).get(player_id)
        effective_pos = position or (pinfo.position if pinfo else "")
        is_gk = (pinfo.is_goalkeeper if pinfo else False) or (effective_pos.upper() == "GK")

        used_numbers = {sn for sn in to_roster.shirt_numbers if sn > 0}
        shirt_num = assign_smart_shirt_number(
            used_numbers=used_numbers,
            preferred_number=target_shirt,
            position=effective_pos,
            is_gk=is_gk,
        )

        self.transferred_player_ids.add(player_id)
        self._write_player_slot(to_entry, dest_slot, player_id, shirt_num)
        self._update_game_plan_after_addition(to_team_id, dest_slot)
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
        if not 0 <= slot_idx < TP_MAX_PLAYERS:
            raise IndexError(f"Roster slot out of range: {slot_idx}")
        if not 0 <= player_id <= 0xFFFFFFFF:
            raise ValueError(f"Player ID out of uint32 range: {player_id}")
        if not 0 <= shirt_num <= 999:
            raise ValueError(f"Shirt number out of range: {shirt_num}")
        pid_offset = entry_offset + TP_PLAYER_IDS + slot_idx * 4
        sn_offset = entry_offset + TP_SHIRT_NUMBERS + slot_idx * 2

        struct.pack_into("<I", self._data, pid_offset, player_id)
        struct.pack_into("<H", self._data, sn_offset, shirt_num)

    def _update_game_plan_after_removal(self, team_id: int, removed_idx: int, replacement_idx: int):
        """
        Game Plan Doctor: Repair squad tactics integrity when a player is removed.
        Preserve the order of active players after roster compaction.

        Only a structurally valid game plan is changed. Custom/legacy layouts
        that do not map the active roster exactly are left byte-for-byte intact.
        """
        gp_offset = self._find_game_plan_offset(team_id)
        if gp_offset is None or gp_offset + GAME_PLAN_ENTRY_SIZE > len(self._data):
            return

        lineup_offset = gp_offset + GP_LINEUP
        lineup = list(self._data[lineup_offset : lineup_offset + TP_MAX_PLAYERS])

        old_size = (replacement_idx + 1) if replacement_idx >= 0 else (removed_idx + 1)
        if not 1 <= old_size <= TP_MAX_PLAYERS:
            return
        expected_old_slots = set(range(old_size))
        active_order = lineup[:old_size]
        if len(active_order) != len(set(active_order)) or set(active_order) != expected_old_slots:
            logger.warning(
                f"Team {team_id} has a non-standard game plan; preserving it during removal"
            )
            return

        last_idx = old_size - 1
        new_active_order = [
            removed_idx if slot == last_idx else slot
            for slot in active_order
            if slot != removed_idx
        ]
        if len(new_active_order) != old_size - 1 or set(new_active_order) != set(range(old_size - 1)):
            logger.warning(f"Could not safely compact game plan for team {team_id}; preserving it")
            return

        lineup[: old_size - 1] = new_active_order
        lineup[old_size - 1] = last_idx
        self._data[lineup_offset : lineup_offset + TP_MAX_PLAYERS] = bytes(lineup)

        # Update role bytes (Captain, CK, PK, Attackers)
        # In PES21, these bytes store the ROSTER INDEX (0..39) or 0xFF (Default/Auto)
        for roff in GP_SINGLE_PLAYER_ROLES:
            target_off = gp_offset + roff
            if target_off < len(self._data):
                val = self._data[target_off]
                if val == removed_idx:
                    self._data[target_off] = 0xFF
                elif val == last_idx:
                    self._data[target_off] = removed_idx

        att_off = gp_offset + GP_ATTACK_PLAYERS
        for b in range(3):
            if att_off + b < len(self._data):
                val = self._data[att_off + b]
                if val == removed_idx:
                    self._data[att_off + b] = 0xFF
                elif val == last_idx:
                    self._data[att_off + b] = removed_idx

    def _update_game_plan_after_addition(self, team_id: int, added_slot: int):
        """
        Ensure newly signed player at added_slot is placed on active bench
        while preserving the active-roster one-to-one mapping.
        """
        gp_offset = self._find_game_plan_offset(team_id)
        if gp_offset is None or gp_offset + GAME_PLAN_ENTRY_SIZE > len(self._data):
            return

        lineup_offset = gp_offset + GP_LINEUP
        lineup = list(self._data[lineup_offset : lineup_offset + TP_MAX_PLAYERS])

        # The active prefix before addition must be a one-to-one mapping of
        # the compact roster slots. Otherwise preserve the original tactics.
        active_prefix = lineup[:added_slot]
        if len(active_prefix) != len(set(active_prefix)) or set(active_prefix) != set(range(added_slot)):
            return

        if added_slot < TP_MAX_PLAYERS:
            try:
                pos = lineup.index(added_slot, added_slot)
            except ValueError:
                pos = -1
            if pos >= 0 and pos != added_slot:
                lineup[pos], lineup[added_slot] = lineup[added_slot], lineup[pos]
            else:
                lineup[added_slot] = added_slot
            self._data[lineup_offset : lineup_offset + TP_MAX_PLAYERS] = bytes(lineup)

    def _find_game_plan_offset(self, team_id: int) -> int | None:
        """Find the byte offset of a team's game plan entry."""
        for i in range(self.game_plan_count):
            offset = self.game_plan_start + i * GAME_PLAN_ENTRY_SIZE
            if offset + 4 > len(self._data):
                break
            tid = struct.unpack_from("<I", self._data, offset + GP_TEAM_ID)[0]
            if tid == team_id:
                return offset
        return None

    def repair_game_plans(self) -> dict[str, int]:
        """Repair active lineup mappings without replacing tactical data wholesale.

        Existing valid roster-slot references keep their relative order. Missing
        active slots are appended, duplicate/empty references are displaced to
        the inactive tail, and roles pointing outside the active roster are reset
        to the game's automatic value (0xFF).
        """
        rosters = self.get_all_rosters()
        repaired_lineups = 0
        reset_roles = 0
        checked = 0

        for i in range(min(self.game_plan_count, MAX_GAME_PLANS)):
            offset = self.game_plan_start + i * GAME_PLAN_ENTRY_SIZE
            if offset + GAME_PLAN_ENTRY_SIZE > len(self._data):
                break

            tid = struct.unpack_from("<I", self._data, offset + GP_TEAM_ID)[0]
            roster = rosters.get(tid)
            if roster is None or roster.roster_size == 0:
                continue

            checked += 1
            active_slots = [slot for slot, pid in enumerate(roster.player_ids) if pid != 0]
            active_set = set(active_slots)
            lineup_offset = offset + GP_LINEUP
            lineup = list(self._data[lineup_offset : lineup_offset + TP_MAX_PLAYERS])

            preserved: list[int] = []
            seen: set[int] = set()
            for slot in lineup:
                if slot in active_set and slot not in seen:
                    preserved.append(slot)
                    seen.add(slot)
            preserved.extend(slot for slot in active_slots if slot not in seen)

            active_count = len(active_slots)
            if lineup[:active_count] != preserved:
                lineup[:active_count] = preserved
                self._data[lineup_offset : lineup_offset + TP_MAX_PLAYERS] = bytes(
                    lineup
                )
                repaired_lineups += 1

            role_offsets = list(GP_SINGLE_PLAYER_ROLES)
            role_offsets.extend(GP_ATTACK_PLAYERS + index for index in range(3))
            for role_offset in role_offsets:
                role_address = offset + role_offset
                value = self._data[role_address]
                if value != 0xFF and value not in active_set:
                    self._data[role_address] = 0xFF
                    reset_roles += 1

        return {
            "checked_game_plans": checked,
            "repaired_lineups": repaired_lineups,
            "reset_roles": reset_roles,
        }

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

    def validate_integrity(self) -> dict[str, object]:
        """Validate FL26 table bounds and cross-table roster invariants."""
        errors: list[str] = []
        warnings: list[str] = []

        expected_size = self.game_plan_start + MAX_GAME_PLANS * GAME_PLAN_ENTRY_SIZE
        if len(self._data) != expected_size:
            errors.append(
                f"data.dat size is {len(self._data):,}; expected {expected_size:,} bytes "
                "for the FL26/PES21 layout"
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
            if any(pid == 0 and shirt != 0 for pid, shirt in zip(roster.player_ids, roster.shirt_numbers)):
                errors.append(f"Team {tid} has a shirt number assigned to an empty roster slot")

        club_ids = self.get_club_team_ids()
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
            if len(starters) != len(set(starters)) or any(slot not in active_slots for slot in starters):
                errors.append(f"Team {tid} game plan has duplicate or empty starting slots")

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
            "checked_game_plans": checked_game_plans,
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
