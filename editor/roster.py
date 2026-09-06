from __future__ import annotations

import logging
import struct

from editor.models import PlayerInfo, TeamData
from editor.playerbin import POSITION_NAMES

logger = logging.getLogger(__name__)

TEAM_PLAYER_ENTRY_SIZE = 0x11C   # 284 bytes
COMPETITION_SECTION_SIZE = 0x1230  # 4656-byte flat league-membership section
GAME_PLAN_ENTRY_SIZE = 0x274     # 628 bytes
MAX_TEAM_PLAYER = 750      # (0xA08650 - 0x9D4648) / 284
MAX_GAME_PLANS = 750       # fixed block; FL26 currently populates 749 entries
# Team-Player table entry
TP_TEAM_ID = 0x00          # 4 bytes uint32 LE
TP_PLAYER_IDS = 0x04       # 40 × 4 bytes (160 bytes total)
TP_SHIRT_NUMBERS = 0xA4    # 40 × 2 bytes (80 bytes total)
TP_MAX_PLAYERS = 40
MIN_CLUB_ROSTER_SIZE = 16
FIRST_TEAM_SLOT_COUNT = 11
MATCHDAY_SQUAD_SLOT_COUNT = 18
MIN_GOALKEEPERS = 2
# Keep high/reserved IDs out of the overflow pool when a native reserve exists.
RESERVED_PLAYER_ID_MIN = 0x100000

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

# Each preset stores three 11-byte player-position arrays before GP_LINEUP:
# kickoff, in possession, and out of possession. The codes use the same
# order as Player.bin's registered positions.
GP_POSITION_PRESETS = (0x004, 0x0A4, 0x144)
GP_POSITION_PHASE_OFFSETS = (0x000, 0x021, 0x042)
GP_POSITION_ENTRY_SIZE = 1

_GOALKEEPER_POSITION_LABELS = frozenset({"GK", "GOALKEEPER", "KEEPER", "GOALIE"})

_POSITION_CODE_ALIASES = {
    "AM": "AMF",
    "CM": "CMF",
    "DM": "DMF",
    "GOALIE": "GK",
    "GOALKEEPER": "GK",
    "KEEPER": "GK",
    "LM": "LMF",
    "LW": "LWF",
    "RM": "RMF",
    "RW": "RWF",
    "ST": "CF",
}
_POSITION_CODE_BY_LABEL = {
    position: index for index, position in enumerate(POSITION_NAMES)
}
_POSITION_LINE_BY_CODE = (
    "GK",
    "DEF",
    "DEF",
    "DEF",
    "MID",
    "MID",
    "MID",
    "MID",
    "MID",
    "FWD",
    "FWD",
    "FWD",
    "FWD",
)


def _game_plan_position_line(code: int | None) -> str | None:
    if code is None or not 0 <= code < len(_POSITION_LINE_BY_CODE):
        return None
    return _POSITION_LINE_BY_CODE[code]


def _game_plan_position_code(position: str | None) -> int | None:
    """Map a registered or transfer position label to a game-plan code."""
    label = (position or "").strip().upper()
    label = _POSITION_CODE_ALIASES.get(label, label)
    return _POSITION_CODE_BY_LABEL.get(label)



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

    if is_gk or pos_upper in _GOALKEEPER_POSITION_LABELS:
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


class RosterGamePlanMixin:
    """Roster mutations and tactical repair over an EditFile byte buffer."""

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

    def get_team_captain_player(self, team_id: int) -> int | None:
        """Return the player currently stored as a team's captain."""
        game_plan_offset = self._find_game_plan_offset(team_id)
        if (
            game_plan_offset is None
            or game_plan_offset + GAME_PLAN_ENTRY_SIZE > len(self._data)
        ):
            return None

        roster = self.get_team_roster(team_id)
        if roster is None:
            return None
        slot = self._data[game_plan_offset + GP_CAPTAIN]
        if (
            slot == 0xFF
            or slot >= TP_MAX_PLAYERS
            or slot >= len(roster.player_ids)
            or roster.player_ids[slot] == 0
        ):
            return None
        return roster.player_ids[slot]

    def set_team_captain(self, team_id: int, player_id: int) -> bool:
        """Set a team's captain using the player's active roster slot."""
        game_plan_offset = self._find_game_plan_offset(team_id)
        if (
            game_plan_offset is None
            or game_plan_offset + GAME_PLAN_ENTRY_SIZE > len(self._data)
            or player_id <= 0
        ):
            return False

        roster = self.get_team_roster(team_id)
        if roster is None:
            return False
        matching_slots = [
            slot
            for slot, roster_player_id in enumerate(roster.player_ids)
            if roster_player_id == player_id
        ]
        if len(matching_slots) != 1:
            return False

        self._data[game_plan_offset + GP_CAPTAIN] = matching_slots[0]
        return True


    def _overflow_role_slots(
        self, team_id: int, player_ids: list[int]
    ) -> dict[int, int]:
        """Return game-plan role order keyed by persisted roster slot."""
        role_slots = {
            slot: slot for slot, player_id in enumerate(player_ids) if player_id != 0
        }
        game_plan_offset = self._find_game_plan_offset(team_id)
        if game_plan_offset is None:
            return role_slots
        lineup_start = game_plan_offset + GP_LINEUP
        lineup_end = lineup_start + TP_MAX_PLAYERS
        if lineup_end > len(self._data):
            return role_slots
        lineup = list(self._data[lineup_start:lineup_end])
        active_roster_slots = set(role_slots)
        active_prefix = lineup[: len(active_roster_slots)]
        if (
            len(active_prefix) != len(active_roster_slots)
            or len(set(active_prefix)) != len(active_prefix)
            or set(active_prefix) != active_roster_slots
        ):
            return role_slots
        return {
            roster_slot: role
            for role, roster_slot in enumerate(active_prefix)
        }

    def find_overflow_release_candidate(
        self,
        team_id: int,
        exclude_player_id: int | None = None,
        roster_player_ids: list[int] | None = None,
        protected_player_ids: set[int] | None = None,
    ) -> tuple[int, int]:
        """
        Find the safest player to release when a roster is full.

        Release ranking is roster-role based, not ability based:

        1. Keep the incoming player, players transferred in this run, and
           explicitly protected players out of the candidate pool.
        2. Protect the first-team game-plan roles and then the matchday bench.
        3. Prefer the deepest native reserve role, which is the least likely to
           play.
        4. Keep at least two goalkeepers when position metadata identifies them.
        5. Prefer a native reserve over a high/reserved-ID player when both
           are otherwise equivalent.

        The game-plan order is used when it is a valid permutation of the
        roster slots; otherwise the persisted roster order is the fallback.
        Player ability values are deliberately never read or compared here.

        Returns:
            (roster_slot_index, player_id)
        """
        if roster_player_ids is None:
            to_entry = self._find_team_player_entry_offset(team_id)
            if to_entry is None:
                return 39, 0
            player_ids = self._read_team_player_entry(to_entry).player_ids
        else:
            player_ids = roster_player_ids

        protected_ids = (
            {exclude_player_id}
            | getattr(self, "transferred_player_ids", set())
            | getattr(self, "release_protected_player_ids", {}).get(
                team_id, frozenset()
            )
            | (protected_player_ids or set())
        )
        active_slots = [
            (slot, pid)
            for slot, pid in enumerate(player_ids)
            if pid != 0 and pid not in protected_ids
        ]
        # If all players in the squad were transferred in this run, fall back
        # to every player except the incoming one.
        if not active_slots:
            active_slots = [
                (slot, pid)
                for slot, pid in enumerate(player_ids)
                if pid != 0 and pid != exclude_player_id
            ]
        if not active_slots:
            return 39, 0

        # A valid game-plan prefix is the best local proxy for first-team,
        # matchday-squad, and reserve usage.  Keep returning roster slots so
        # release_player() can mutate the correct persisted entry.
        role_slots = self._overflow_role_slots(team_id, player_ids)

        metadata = {
            pid: self._player_metadata(pid) for _, pid in active_slots
        }

        def is_goalkeeper(slot: int, player: PlayerInfo | None) -> bool:
            # Slot zero is the historical PES fallback for the starting GK.
            return slot == 0 or bool(player and player.is_goalkeeper)

        total_gks = sum(
            is_goalkeeper(slot, metadata[pid]) for slot, pid in active_slots
        )

        def candidate_sort_key(item: tuple[int, int]) -> tuple[int, ...]:
            roster_slot, pid = item
            role = role_slots.get(roster_slot, roster_slot)
            player = metadata[pid]
            goalkeeper = is_goalkeeper(roster_slot, player)
            is_reserved_player = pid >= RESERVED_PLAYER_ID_MIN
            usage = getattr(self, "release_usage", {}).get(pid)

            if role < FIRST_TEAM_SLOT_COUNT:
                tier = 3
            elif role < MATCHDAY_SQUAD_SLOT_COUNT:
                tier = 2
            elif goalkeeper and total_gks <= MIN_GOALKEEPERS:
                tier = 2
            elif is_reserved_player:
                # High/reserved IDs are preserved over native deep reserves,
                # but remain releasable if no native candidate exists.
                tier = 1
            else:
                tier = 0

            if usage is None:
                usage_key = (1, 0, 0, 0, 0)
            else:
                usage_key = (
                    0,
                    usage.minutes,
                    usage.starts,
                    usage.appearances,
                    usage.news_mentions,
                )

            # Known low usage wins before depth; depth remains the fallback.
            return (tier, *usage_key, -role, -roster_slot)

        best_slot, best_pid = min(active_slots, key=candidate_sort_key)
        return best_slot, best_pid

    def describe_overflow_release_candidate(
        self,
        team_id: int,
        player_id: int,
        roster_player_ids: list[int] | None = None,
        protected_player_ids: set[int] | None = None,
    ) -> dict[str, object] | None:
        """Return explainable ranking details for one selected candidate."""
        if roster_player_ids is None:
            entry = self._find_team_player_entry_offset(team_id)
            if entry is None:
                return None
            player_ids = self._read_team_player_entry(entry).player_ids
        else:
            player_ids = roster_player_ids
        slot, selected_id = self.find_overflow_release_candidate(
            team_id,
            roster_player_ids=player_ids,
            protected_player_ids=protected_player_ids,
        )
        if selected_id != player_id:
            return None
        role = self._overflow_role_slots(team_id, player_ids).get(slot, slot)
        player = self._player_metadata(player_id)
        usage = getattr(self, "release_usage", {}).get(player_id)
        if role < FIRST_TEAM_SLOT_COUNT:
            role_group = "first_team"
        elif role < MATCHDAY_SQUAD_SLOT_COUNT:
            role_group = "matchday_bench"
        else:
            role_group = "reserve"
        return {
            "team_id": team_id,
            "slot": slot,
            "role": role,
            "role_group": role_group,
            "player_id": player_id,
            "name": player.name if player is not None else "",
            "is_reserved": player_id >= RESERVED_PLAYER_ID_MIN,
            "is_goalkeeper": bool(player and player.is_goalkeeper) or slot == 0,
            "usage": None if usage is None else usage.to_dict(),
            "selection_basis": (
                "lowest_known_usage_then_deepest_role"
                if usage is not None
                else "deepest_role"
            ),
        }


    def get_player_shirt_number(self, team_id: int, player_id: int) -> int | None:
        """Return a player's current shirt number, or None when not registered."""
        roster = self.get_team_roster(team_id)
        if roster is None:
            return None
        idx = roster.player_index(player_id)
        if idx == -1 or idx >= len(roster.shirt_numbers):
            return None
        return roster.shirt_numbers[idx]

    def update_player_shirt_numbers(
        self,
        team_id: int,
        updates: list[tuple[int, int]],
    ) -> bool:
        """Apply several shirt-number changes without order-dependent conflicts."""
        if not updates:
            return True

        entry = self._find_team_player_entry_offset(team_id)
        if entry is None:
            return False

        roster = self._read_team_player_entry(entry)
        indexed_updates: dict[int, tuple[int, int]] = {}
        requested_numbers: dict[int, int] = {}
        for player_id, shirt_number in updates:
            try:
                valid_number = 1 <= shirt_number <= 999
            except TypeError:
                valid_number = False
            if not valid_number:
                logger.error(
                    f"Invalid shirt number {shirt_number}; expected 1..999"
                )
                return False

            existing = indexed_updates.get(player_id)
            if existing is not None:
                if existing[1] != shirt_number:
                    logger.warning(
                        "Conflicting shirt updates requested for player %s on team %s",
                        player_id,
                        team_id,
                    )
                    return False
                continue

            slot_idx = roster.player_index(player_id)
            if slot_idx == -1:
                return False
            other_player_id = requested_numbers.get(shirt_number)
            if other_player_id is not None and other_player_id != player_id:
                logger.warning(
                    "Shirt #%s requested by multiple players on team %s",
                    shirt_number,
                    team_id,
                )
                return False
            indexed_updates[player_id] = (slot_idx, shirt_number)
            requested_numbers[shirt_number] = player_id

        update_player_ids = set(indexed_updates)
        for player_id, (slot_idx, shirt_number) in indexed_updates.items():
            for other_slot, (other_player_id, other_shirt_number) in enumerate(
                zip(roster.player_ids, roster.shirt_numbers)
            ):
                if (
                    other_slot != slot_idx
                    and other_player_id != 0
                    and other_shirt_number == shirt_number
                    and other_player_id not in update_player_ids
                ):
                    logger.warning(
                        "Shirt #%s is already used on team %s",
                        shirt_number,
                        team_id,
                    )
                    return False

        for player_id, (slot_idx, shirt_number) in indexed_updates.items():
            self._write_player_slot(entry, slot_idx, player_id, shirt_number)
            logger.debug(
                "Updated player %s on team %s to shirt #%s",
                player_id,
                team_id,
                shirt_number,
            )
        return True

    def update_player_shirt_number(
        self,
        team_id: int,
        player_id: int,
        shirt_number: int,
    ) -> bool:
        """Update one player's shirt number without transferring."""
        return self.update_player_shirt_numbers(
            team_id,
            [(player_id, shirt_number)],
        )

    def move_player(
        self,
        player_id: int,
        from_team_id: int,
        to_team_id: int,
        shirt_number: int | None = None,
        preferred_shirt_number: int | None = None,
        position: str = "",
        allow_overflow_release: bool = True,
    ) -> bool:
        """
        Transfer a player from one team to another.

        Steps:
        1. Find player in source team's roster
        2. Remove from source (compact slots by shifting last non-zero entry)
        3. Add to destination (first empty slot or auto-release a deep reserve if full)
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

        effective_pos = self._mutation_player_position(player_id, position)

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
                f"Auto-releasing deepest reserve player {overflow_pid} to Free Agent."
            )
            if not self.release_player(overflow_pid, to_team_id):
                logger.error(f"Could not release overflow player {overflow_pid} from team {to_team_id}")
                return False
            to_roster = self._read_team_player_entry(to_entry)

        self._remove_roster_player(
            from_team_id,
            from_entry,
            from_roster,
            player_idx,
            player_id,
            effective_pos,
        )

        # Re-read to_roster in case from_entry == to_entry
        to_roster = self._read_team_player_entry(to_entry)

        # --- Step 2: Add to destination with Smart Shirt Number ---
        dest_slot = to_roster.first_empty_slot()
        if dest_slot == -1:
            logger.error(f"No empty slot in destination team {to_team_id}")
            return False

        is_gk = _game_plan_position_code(effective_pos) == 0

        used_numbers = {
            shirt_number
            for player_id, shirt_number in zip(
                to_roster.player_ids, to_roster.shirt_numbers
            )
            if player_id != 0 and shirt_number > 0
        }
        shirt_num = assign_smart_shirt_number(
            used_numbers=used_numbers,
            preferred_number=target_shirt,
            position=effective_pos,
            is_gk=is_gk,
        )

        self.transferred_player_ids.add(player_id)
        self._write_player_slot(to_entry, dest_slot, player_id, shirt_num)
        self._update_game_plan_after_addition(
            to_team_id,
            dest_slot,
            added_position=effective_pos,
        )

        logger.info(
            f"Transfer: player {player_id} moved from team {from_team_id} "
            f"(slot {player_idx}) to team {to_team_id} (slot {dest_slot}, shirt #{shirt_num})"
        )
        return True

    def release_player(
        self,
        player_id: int,
        from_team_id: int,
        position: str = "",
    ) -> bool:
        """
        Release a player to Free Agent (or when moving to an unrepresented club).

        Removes the player from the team's 40-slot roster and compacts the slots.
        In PES21, any registered player not assigned to a club automatically
        becomes a Free Agent.

        Args:
            player_id: Player ID to release.
            from_team_id: Team ID to remove player from.
            position: Optional registered position label for game-plan repair.

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
        effective_pos = self._mutation_player_position(player_id, position)

        self._remove_roster_player(
            from_team_id,
            from_entry,
            from_roster,
            player_idx,
            player_id,
            effective_pos,
        )

        logger.info(f"Released player {player_id} from team {from_team_id} (now Free Agent)")
        return True

    def add_player(
        self,
        player_id: int,
        to_team_id: int,
        shirt_number: int | None = None,
        preferred_shirt_number: int | None = None,
        position: str = "",
        allow_overflow_release: bool = True,
        protected_player_ids: set[int] | None = None,
    ) -> bool:
        """
        Sign a player from Free Agent into a team.

        Args:
            player_id: Player ID to add.
            to_team_id: Destination team ID.
            position: Player position label.
            protected_player_ids: Player IDs that must not be auto-released.

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
            slot_to_rel, pid_to_rel = self.find_overflow_release_candidate(
                to_team_id,
                exclude_player_id=player_id,
                protected_player_ids=protected_player_ids,
            )
            if pid_to_rel == 0:
                logger.error(f"No safe overflow release candidate for team {to_team_id}")
                return False
            logger.warning(
                f"Team {to_team_id} roster is full (40/40). "
                f"Auto-releasing deepest reserve player {pid_to_rel} (slot {slot_to_rel}) "
                "to Free Agent."
            )
            if not self.release_player(pid_to_rel, to_team_id):
                logger.error(f"Could not release overflow player {pid_to_rel} from team {to_team_id}")
                return False
            to_roster = self._read_team_player_entry(to_entry)

        dest_slot = to_roster.first_empty_slot()
        if dest_slot == -1:
            logger.error(f"Team {to_team_id} roster is full (40 players)")
            return False

        effective_pos = self._mutation_player_position(player_id, position)
        is_gk = _game_plan_position_code(effective_pos) == 0

        used_numbers = {
            shirt_number
            for player_id, shirt_number in zip(
                to_roster.player_ids, to_roster.shirt_numbers
            )
            if player_id != 0 and shirt_number > 0
        }
        shirt_num = assign_smart_shirt_number(
            used_numbers=used_numbers,
            preferred_number=target_shirt,
            position=effective_pos,
            is_gk=is_gk,
        )

        self.transferred_player_ids.add(player_id)
        self._write_player_slot(to_entry, dest_slot, player_id, shirt_num)
        self._update_game_plan_after_addition(
            to_team_id,
            dest_slot,
            added_position=effective_pos,
        )
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

    def _remove_roster_player(
        self,
        team_id: int,
        entry_offset: int,
        roster: TeamData,
        player_index: int,
        player_id: int,
        position: str,
    ) -> None:
        """Compact one roster slot and preserve its game-plan player mapping."""
        last_index = max(
            index for index, active_player_id in enumerate(roster.player_ids)
            if active_player_id
        )
        replacement_index = last_index if last_index > player_index else -1
        if replacement_index >= 0:
            self._write_player_slot(
                entry_offset,
                player_index,
                roster.player_ids[replacement_index],
                roster.shirt_numbers[replacement_index],
            )
            self._write_player_slot(entry_offset, replacement_index, 0, 0)
        else:
            self._write_player_slot(entry_offset, player_index, 0, 0)
        self._update_game_plan_after_removal(
            team_id,
            player_index,
            replacement_index,
            removed_player_id=player_id,
            removed_position=position,
        )

    def _select_game_plan_promotion_slot(
        self,
        team_id: int,
        candidate_slots: list[int],
        removed_player_id: int | None,
        removed_role: int,
        *,
        target_position_code: int | None = None,
    ) -> int | None:
        """Choose a bench player compatible with a vacated starter role."""
        roster = self.get_team_roster(team_id)
        if roster is None:
            return None

        removed_position = (
            (self.get_player_position(removed_player_id) or "").strip().upper()
            if removed_player_id is not None
            else ""
        )
        removed_position_code = _game_plan_position_code(removed_position)
        role_position_code = (
            target_position_code
            if target_position_code is not None
            else removed_position_code
        )
        target_is_goalkeeper = role_position_code == 0 or (
            role_position_code is None
            and not removed_position
            and removed_role == 0
        )
        target_line = _game_plan_position_line(role_position_code)

        known_same_position: list[int] = []
        same_position_line: list[int] = []
        for slot in candidate_slots:
            player_id = roster.player_ids[slot]
            if not player_id:
                continue
            position = (self.get_player_position(player_id) or "").strip().upper()
            position_code = _game_plan_position_code(position)
            if (
                role_position_code is not None
                and position_code == role_position_code
            ):
                known_same_position.append(slot)
            if (
                target_line is not None
                and _game_plan_position_line(position_code) == target_line
            ):
                same_position_line.append(slot)

        if known_same_position:
            return known_same_position[0]
        if target_is_goalkeeper:
            # Prefer the historical slot-zero fallback, then the first
            # matchday reserve.  Leaving the copied last roster slot in role
            # zero can put an outfield player in the goalkeeper location when
            # metadata is unavailable.
            return next(
                (
                    slot
                    for slot in candidate_slots
                    if slot == 0
                ),
                candidate_slots[0] if candidate_slots else None,
            )
        if same_position_line:
            return same_position_line[0]
        return None





    def _repair_game_plan_role_position(
        self,
        game_plan_offset: int,
        role: int,
        position_code: int | None,
    ) -> int:
        """Relabel a starter role when a roster mutation changes its player."""
        if (
            not 0 <= role < FIRST_TEAM_SLOT_COUNT
            or position_code is None
            or not 0 <= position_code < len(POSITION_NAMES)
        ):
            return 0

        repaired = 0
        for preset_offset in GP_POSITION_PRESETS:
            for phase_offset in GP_POSITION_PHASE_OFFSETS:
                position_address = (
                    game_plan_offset
                    + preset_offset
                    + phase_offset
                    + role * GP_POSITION_ENTRY_SIZE
                )
                if position_address >= len(self._data):
                    continue
                if self._data[position_address] != position_code:
                    self._data[position_address] = position_code
                    repaired += 1
        return repaired

    def _repair_game_plan_changed_player_positions(
        self,
        game_plan_offset: int,
        roster: TeamData,
        previous_player_roles: dict[int, int],
        lineup: list[int],
        *,
        position_overrides: dict[int, str] | None = None,
    ) -> int:
        """Align changed starter occupants with their registered positions."""
        overrides = position_overrides or {}
        starter_count = min(FIRST_TEAM_SLOT_COUNT, roster.roster_size)
        repaired = 0
        for role, slot in enumerate(lineup[:starter_count]):
            if not 0 <= slot < TP_MAX_PLAYERS:
                continue
            player_id = roster.player_ids[slot]
            if not player_id or previous_player_roles.get(player_id) == role:
                continue
            position = overrides.get(player_id) or self.get_player_position(player_id) or ""
            repaired += self._repair_game_plan_role_position(
                game_plan_offset,
                role,
                _game_plan_position_code(position),
            )
        return repaired


    def _repair_game_plan_goalkeeper_positions(
        self,
        game_plan_offset: int,
        roster: TeamData,
        lineup: list[int],
        *,
        position_overrides: dict[int, str] | None = None,
    ) -> tuple[int, int]:
        """Keep known goalkeepers in the goalkeeper lineup role."""
        active_count = min(TP_MAX_PLAYERS, roster.roster_size)
        starter_count = min(FIRST_TEAM_SLOT_COUNT, active_count)
        if len(lineup) < active_count:
            return 0, 0

        overrides = position_overrides or {}

        def player_position(player_id: int) -> str:
            return overrides.get(player_id) or self.get_player_position(player_id) or ""
        def is_goalkeeper_role(role: int) -> bool:
            slot = lineup[role]
            return (
                0 <= slot < TP_MAX_PLAYERS
                and _game_plan_position_code(
                    player_position(roster.player_ids[slot])
                )
                == 0
            )
        def role_has_goalkeeper_codes(role: int) -> bool:
            found_code = False
            for preset_offset in GP_POSITION_PRESETS:
                for phase_offset in GP_POSITION_PHASE_OFFSETS:
                    position_address = (
                        game_plan_offset
                        + preset_offset
                        + phase_offset
                        + role * GP_POSITION_ENTRY_SIZE
                    )
                    if position_address >= len(self._data):
                        return False
                    found_code = True
                    if self._data[position_address] != 0:
                        return False
            return found_code



        role0_is_unknown_incumbent = False
        if starter_count:
            role0_slot = lineup[0]
            if 0 <= role0_slot < TP_MAX_PLAYERS:
                role0_player_id = roster.player_ids[role0_slot]
                role0_position = _game_plan_position_code(
                    player_position(role0_player_id)
                )
                role0_is_unknown_incumbent = (
                    bool(role0_player_id)
                    and role0_position is None
                    and role_has_goalkeeper_codes(0)
                )

        starter_goalkeeper_roles = [
            role for role in range(starter_count) if is_goalkeeper_role(role)
        ]
        bench_goalkeeper_roles = [
            role
            for role in range(starter_count, active_count)
            if is_goalkeeper_role(role)
        ]
        known_goalkeeper_roles = starter_goalkeeper_roles + bench_goalkeeper_roles
        if role0_is_unknown_incumbent and bench_goalkeeper_roles:
            # A role-zero player with an unknown label and a valid GK marker
            # is an incumbent, not a reason to promote an arbitrary reserve.
            goalkeeper_roles = [0]
        else:
            goalkeeper_roles = known_goalkeeper_roles
        if not goalkeeper_roles:
            return 0, 0

        repaired_roles = 0
        lineup_changed = False
        # Team-Player slot order is the local squad hierarchy. Player.bin
        # ``caps`` counts international appearances, so it cannot identify a
        # club's first-choice goalkeeper (for example, Raya versus Kepa).
        primary_role = min(
            goalkeeper_roles,
            key=lambda role: (lineup[role], role),
        )
        if primary_role != 0:
            lineup[0], lineup[primary_role] = (
                lineup[primary_role],
                lineup[0],
            )
            repaired_roles += 1
            lineup_changed = True

        bench_roles = [
            role
            for role in range(starter_count, active_count)
            if _game_plan_position_code(
                player_position(roster.player_ids[lineup[role]])
            )
            != 0
        ]
        extra_starter_goalkeeper_roles = [
            role
            for role in range(1, starter_count)
            if is_goalkeeper_role(role)
        ]
        for goalkeeper_role in extra_starter_goalkeeper_roles:
            bench_role = next(iter(bench_roles), None)
            if bench_role is None:
                break
            lineup[goalkeeper_role], lineup[bench_role] = (
                lineup[bench_role],
                lineup[goalkeeper_role],
            )
            bench_roles.pop(0)
            repaired_roles += 1
            lineup_changed = True

        if lineup_changed:
            self._data[
                game_plan_offset + GP_LINEUP : game_plan_offset + GP_LINEUP + TP_MAX_PLAYERS
            ] = bytes(lineup)

        return repaired_roles, 0

    def _update_game_plan_after_removal(
        self,
        team_id: int,
        removed_idx: int,
        replacement_idx: int,
        *,
        removed_player_id: int | None = None,
        removed_position: str = "",
    ) -> None:
        """
        Keep game-plan roles attached to players after roster compaction.

        Team-Player removal copies the last active player into the removed
        roster slot.  Rebuild the active lineup from the old role order,
        remap that copied player, and promote a compatible bench player only
        when a starter was removed.  Invalid/custom active prefixes remain
        untouched.
        """
        gp_offset = self._find_game_plan_offset(team_id)
        if gp_offset is None or gp_offset + GAME_PLAN_ENTRY_SIZE > len(self._data):
            return

        roster = self.get_team_roster(team_id)
        if roster is None:
            return

        lineup_offset = gp_offset + GP_LINEUP
        lineup = list(self._data[lineup_offset : lineup_offset + TP_MAX_PLAYERS])
        if len(lineup) != TP_MAX_PLAYERS:
            return

        new_active_slots = {
            slot for slot, player_id in enumerate(roster.player_ids) if player_id
        }
        stale_slot = replacement_idx if replacement_idx >= 0 else removed_idx
        old_active_slots = new_active_slots | {stale_slot}
        old_active_count = len(old_active_slots)
        if not 1 <= old_active_count <= TP_MAX_PLAYERS:
            return

        active_order = lineup[:old_active_count]
        if (
            len(active_order) != old_active_count
            or len(set(active_order)) != old_active_count
            or set(active_order) != old_active_slots
            or removed_idx not in active_order
            or (
                replacement_idx >= 0
                and replacement_idx not in active_order
            )
        ):
            logger.warning(
                f"Team {team_id} has a non-standard active game-plan order; "
                "preserving it during removal"
            )
            return
        replacement_player_id = (
            roster.player_ids[removed_idx]
            if replacement_idx >= 0 and 0 <= removed_idx < TP_MAX_PLAYERS
            else None
        )

        def previous_player_id(slot: int) -> int:
            if slot == removed_idx:
                return removed_player_id or 0
            if replacement_idx >= 0 and slot == replacement_idx:
                return replacement_player_id or 0
            if 0 <= slot < TP_MAX_PLAYERS:
                return roster.player_ids[slot]
            return 0

        previous_player_roles = {
            player_id: role
            for role, slot in enumerate(
                active_order[: min(FIRST_TEAM_SLOT_COUNT, old_active_count)]
            )
            if (player_id := previous_player_id(slot))
        }


        removed_role = active_order.index(removed_idx)
        target_position_code: int | None = None
        if removed_role < FIRST_TEAM_SLOT_COUNT:
            position_address = (
                gp_offset
                + GP_POSITION_PRESETS[0]
                + GP_POSITION_PHASE_OFFSETS[0]
                + removed_role * GP_POSITION_ENTRY_SIZE
            )
            if position_address < len(self._data):
                raw_position_code = self._data[position_address]
                if removed_role == 0 or raw_position_code != 0:
                    target_position_code = raw_position_code
        promoted_slot: int | None = None
        if removed_role < FIRST_TEAM_SLOT_COUNT and old_active_count > FIRST_TEAM_SLOT_COUNT:
            promotion_candidates = [
                slot
                for slot in active_order[FIRST_TEAM_SLOT_COUNT:]
                if slot not in {removed_idx, replacement_idx}
            ]
            promoted_slot = self._select_game_plan_promotion_slot(
                team_id,
                promotion_candidates,
                removed_player_id,
                removed_role,
                target_position_code=target_position_code,
            )
            if promoted_slot is None:
                removed_position_known = bool(
                    removed_player_id is not None
                    and self.get_player_position(removed_player_id)
                )
                stale_role = active_order.index(stale_slot)
                goalkeeper_role = (
                    active_order.index(0) if 0 in active_order else None
                )
                goalkeeper_would_enter_starters = (
                    stale_role < FIRST_TEAM_SLOT_COUNT
                    and goalkeeper_role is not None
                    and goalkeeper_role > stale_role
                    and goalkeeper_role - 1 < FIRST_TEAM_SLOT_COUNT
                )
                if not removed_position_known and goalkeeper_would_enter_starters:
                    promoted_slot = next(
                        (slot for slot in promotion_candidates if slot != 0),
                        None,
                    )

        if promoted_slot is not None:
            new_active: list[int] = []
            for slot in active_order:
                if slot == removed_idx:
                    new_active.append(promoted_slot)
                elif slot == replacement_idx:
                    new_active.append(removed_idx)
                elif slot == promoted_slot:
                    continue
                else:
                    new_active.append(slot)
        elif removed_role < FIRST_TEAM_SLOT_COUNT:
            # A copied last-slot player replaces a removed starter at the
            # departed player's role when no compatible promotion exists.
            new_active = [
                slot for slot in active_order if slot != stale_slot
            ]
        elif replacement_idx >= 0:
            # For a reserve removal, keep the copied player's former role and
            # remove the departed reserve's role.  Dropping stale_slot alone
            # would shift every later role and move a starter into the wrong
            # tactical position.
            new_active = [
                removed_idx if slot == replacement_idx else slot
                for slot in active_order
                if slot != removed_idx
            ]
        else:
            new_active = [
                slot for slot in active_order if slot != removed_idx
            ]

        if (
            len(new_active) != len(new_active_slots)
            or set(new_active) != new_active_slots
        ):
            logger.warning(
                f"Could not safely compact game plan for team {team_id}; "
                "preserving it"
            )
            return

        # Keep the inactive tactical bytes in place.  Appending the stale
        # roster slot preserves the established full-permutation layout when
        # the tail is a permutation, while still allowing legacy 0xFF tails.
        new_lineup = new_active + lineup[old_active_count:] + [stale_slot]
        if len(new_lineup) != TP_MAX_PLAYERS:
            logger.warning(
                f"Could not safely rebuild game plan for team {team_id}; "
                "preserving it"
            )
            return
        self._data[lineup_offset : lineup_offset + TP_MAX_PLAYERS] = bytes(
            new_lineup
        )
        self._repair_game_plan_goalkeeper_positions(
            gp_offset,
            roster,
            new_lineup,
        )
        position_overrides = (
            {removed_player_id: removed_position}
            if removed_player_id is not None and removed_position
            else None
        )
        self._repair_game_plan_changed_player_positions(
            gp_offset,
            roster,
            previous_player_roles,
            new_lineup,
            position_overrides=position_overrides,
        )
        for role_offset in GP_SINGLE_PLAYER_ROLES:
            target_offset = gp_offset + role_offset
            value = self._data[target_offset]
            if value == removed_idx:
                self._data[target_offset] = 0xFF
            elif replacement_idx >= 0 and value == replacement_idx:
                self._data[target_offset] = removed_idx

        attack_offset = gp_offset + GP_ATTACK_PLAYERS
        for index in range(3):
            target_offset = attack_offset + index
            value = self._data[target_offset]
            if value == removed_idx:
                self._data[target_offset] = 0xFF
            elif replacement_idx >= 0 and value == replacement_idx:
                self._data[target_offset] = removed_idx

    def _update_game_plan_after_addition(
        self,
        team_id: int,
        added_slot: int,
        *,
        added_position: str = "",
    ) -> None:
        """
        Append a newly registered player to the active game-plan bench.

        Existing role order is preserved.  The roster slot can be sparse in a
        legacy save, so it must not be used as a formation-role index.
        """
        gp_offset = self._find_game_plan_offset(team_id)
        if gp_offset is None or gp_offset + GAME_PLAN_ENTRY_SIZE > len(self._data):
            return

        roster = self.get_team_roster(team_id)
        if roster is None or not 0 <= added_slot < TP_MAX_PLAYERS:
            return
        active_slots = [
            slot for slot, player_id in enumerate(roster.player_ids) if player_id
        ]
        if added_slot not in active_slots:
            return

        old_active_slots = set(active_slots) - {added_slot}
        old_active_count = len(old_active_slots)
        if old_active_count >= TP_MAX_PLAYERS:
            return

        lineup_offset = gp_offset + GP_LINEUP
        lineup = list(self._data[lineup_offset : lineup_offset + TP_MAX_PLAYERS])
        if len(lineup) != TP_MAX_PLAYERS:
            return
        active_prefix = lineup[:old_active_count]
        if (
            len(active_prefix) != old_active_count
            or len(set(active_prefix)) != old_active_count
            or set(active_prefix) != old_active_slots
        ):
            return
        previous_player_roles = {
            player_id: role
            for role, slot in enumerate(
                active_prefix[: min(FIRST_TEAM_SLOT_COUNT, old_active_count)]
            )
            if (player_id := roster.player_ids[slot])
        }


        try:
            added_role = lineup.index(added_slot, old_active_count)
        except ValueError:
            added_role = -1
        if added_role < 0:
            lineup[old_active_count] = added_slot
        elif added_role != old_active_count:
            lineup[added_role], lineup[old_active_count] = (
                lineup[old_active_count],
                lineup[added_role],
            )
        self._data[lineup_offset : lineup_offset + TP_MAX_PLAYERS] = bytes(
            lineup
        )
        added_player_id = roster.player_ids[added_slot]
        effective_added_position = self._mutation_player_position(
            added_player_id,
            added_position,
        )
        position_overrides = (
            {added_player_id: effective_added_position}
            if effective_added_position
            else None
        )
        self._repair_game_plan_goalkeeper_positions(
            gp_offset,
            roster,
            lineup,
            position_overrides=position_overrides,
        )
        self._repair_game_plan_changed_player_positions(
            gp_offset,
            roster,
            previous_player_roles,
            lineup,
            position_overrides=position_overrides,
        )

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
        to the game's automatic value (0xFF). Position bytes remain attached to
        their formation roles.
        """
        rosters = self.get_all_rosters()
        repaired_lineups = 0
        repaired_goalkeeper_roles = 0
        repaired_position_bytes = 0
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

            role_repairs, position_repairs = self._repair_game_plan_goalkeeper_positions(
                offset,
                roster,
                lineup,
            )
            repaired_goalkeeper_roles += role_repairs
            repaired_position_bytes += position_repairs
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
            "repaired_goalkeeper_roles": repaired_goalkeeper_roles,
            "repaired_position_bytes": repaired_position_bytes,
        }
