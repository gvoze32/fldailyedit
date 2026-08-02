"""
Data models for the editor module.
"""
from dataclasses import dataclass, field


@dataclass
class PlayerInfo:
    """Minimal player info extracted from the edit file or database."""
    player_id: int
    name: str
    print_name: str = ""  # shirt/club print name
    overall_rating: int = 0  # player overall ability (0 if unknown)
    position: str = ""  # e.g. 'GK', 'CB', 'LB', 'RB', 'DMF', 'CMF', 'AMF', 'LWF', 'RWF', 'SS', 'CF'

    @property
    def is_goalkeeper(self) -> bool:
        return self.position.strip().upper() == "GK"


@dataclass
class TeamData:
    """Team roster data from the Team-Player Table."""
    team_id: int
    player_ids: list[int] = field(default_factory=lambda: [0] * 40)
    shirt_numbers: list[int] = field(default_factory=lambda: [0] * 40)

    @property
    def roster(self) -> list[int]:
        """Non-zero player IDs (actual players on this team)."""
        return [pid for pid in self.player_ids if pid != 0]

    @property
    def roster_size(self) -> int:
        return len(self.roster)

    @property
    def is_full(self) -> bool:
        return self.roster_size >= 40

    def first_empty_slot(self) -> int:
        """Index of the first empty (0) slot, or -1 if full."""
        try:
            return self.player_ids.index(0)
        except ValueError:
            return -1

    def has_player(self, player_id: int) -> bool:
        return player_id in self.player_ids

    def player_index(self, player_id: int) -> int:
        """Index of player in the slot array, or -1 if not found."""
        try:
            return self.player_ids.index(player_id)
        except ValueError:
            return -1


@dataclass
class TeamInfo:
    """Team metadata from the Team Entry table (name, abbreviation)."""
    team_id: int
    name: str
    abbreviation: str = ""
