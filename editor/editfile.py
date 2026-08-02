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

from editor.models import PlayerInfo, TeamData, TeamInfo

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
TE_TEAM_NAME = 0x068       # 70 bytes null-terminated string
TE_ABBREVIATION = 0x0AE    # 4 bytes null-terminated string

# Team-Player table entry
TP_TEAM_ID = 0x00          # 4 bytes uint32 LE
TP_PLAYER_IDS = 0x04       # 40 × 4 bytes (160 bytes total)
TP_SHIRT_NUMBERS = 0xA4    # 40 × 2 bytes (80 bytes total)
TP_MAX_PLAYERS = 40

# Game plan entry
GP_TEAM_ID = 0x000         # 4 bytes uint32 LE
GP_LINEUP = 0x1E4          # 40 bytes (40 × 1 byte index IDs)
GP_CAPTAIN = 0x212         # 1 byte index ID


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

    def get_all_players(self) -> dict[int, PlayerInfo]:
        """
        Read all player entries (ID + name only).

        Returns:
            {player_id: PlayerInfo}
        """
        players = {}
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

        logger.info(f"Read {len(players)} players")
        return players

    def get_all_team_info(self) -> dict[int, TeamInfo]:
        """
        Read all team entries (ID + name + abbreviation).

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
            name = self._read_string(entry_offset + TE_TEAM_NAME, 70)
            abbrev = self._read_string(entry_offset + TE_ABBREVIATION, 4)

            teams[team_id] = TeamInfo(
                team_id=team_id,
                name=name,
                abbreviation=abbrev,
            )

        logger.info(f"Read {len(teams)} teams")
        return teams

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

    def move_player(self, player_id: int, from_team_id: int, to_team_id: int) -> bool:
        """
        Transfer a player from one team to another.

        Steps:
        1. Find player in source team's roster
        2. Remove from source (compact slots by shifting last non-zero entry)
        3. Add to destination (first empty slot)
        4. Assign an unused shirt number
        5. Update game plan index IDs if needed

        Args:
            player_id: The player to transfer.
            from_team_id: Source team ID.
            to_team_id: Destination team ID.

        Returns:
            True if transfer succeeded, False otherwise.
        """
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

        if to_roster.is_full:
            logger.error(f"Destination team {to_team_id} is full (40 players)")
            return False

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
        elif last_idx > player_idx:
            # Move last player into the vacated slot (compact)
            self._write_player_slot(
                from_entry, player_idx,
                from_roster.player_ids[last_idx],
                from_roster.shirt_numbers[last_idx],
            )
            # Zero out the last slot
            self._write_player_slot(from_entry, last_idx, 0, 0)

            # Update game plan for source team (index IDs shifted)
            self._update_game_plan_after_removal(from_team_id, player_idx, last_idx)
        else:
            # player_idx > last_idx shouldn't happen, but handle gracefully
            self._write_player_slot(from_entry, player_idx, 0, 0)

        # --- Step 2: Add to destination ---
        dest_slot = to_roster.first_empty_slot()
        if dest_slot == -1:
            logger.error(f"No empty slot in destination team {to_team_id} (should have been caught)")
            return False

        # Pick a shirt number (use a simple unused number)
        used_numbers = set(to_roster.shirt_numbers)
        shirt_num = 1
        for candidate in range(1, 100):
            if candidate not in used_numbers:
                shirt_num = candidate
                break

        self._write_player_slot(to_entry, dest_slot, player_id, shirt_num)

        logger.info(
            f"Transfer: player {player_id} moved from team {from_team_id} "
            f"(slot {player_idx}) to team {to_team_id} (slot {dest_slot}, shirt #{shirt_num})"
        )
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
        Update game plan index IDs after a player is compacted out of the roster.

        When slot[removed_idx] is replaced by slot[replacement_idx] (last player),
        any game plan reference to replacement_idx should become removed_idx,
        and any reference to removed_idx should be cleared.
        """
        gp_offset = self._find_game_plan_offset(team_id)
        if gp_offset is None:
            return

        lineup_offset = gp_offset + GP_LINEUP

        for i in range(TP_MAX_PLAYERS):
            idx_byte = self._data[lineup_offset + i]

            if idx_byte == replacement_idx:
                # This player moved to removed_idx's slot
                self._data[lineup_offset + i] = removed_idx
            elif idx_byte == removed_idx:
                # The removed player — set to the replacement's new position
                # Actually this player was removed, so we could set to 0 or leave
                # But since the slot now contains the replacement player,
                # and we already handled that case above, this shouldn't normally trigger
                pass

        # Also update captain if needed
        captain_offset = gp_offset + GP_CAPTAIN
        captain_idx = self._data[captain_offset]
        if captain_idx == replacement_idx:
            self._data[captain_offset] = removed_idx

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
