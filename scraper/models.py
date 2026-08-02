"""
Data models for the scraper module.
"""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Transfer:
    """A single transfer scraped from Transfermarkt."""
    player_name: str
    from_club: str
    to_club: str
    date: str = ""
    transfer_type: str = "transfer"  # "transfer", "loan", "end of loan", "free transfer"
    fee: str = ""
    league: str = ""
    season: str = ""

    def __str__(self):
        return f"{self.player_name}: {self.from_club} → {self.to_club} ({self.transfer_type})"


@dataclass
class MatchedTransfer:
    """A transfer matched to FL26 database IDs."""
    transfer: Transfer

    # Matched IDs from FL26 database
    player_id: Optional[int] = None
    from_team_id: Optional[int] = None
    to_team_id: Optional[int] = None

    # Match quality
    player_confidence: float = 0.0
    from_team_confidence: float = 0.0
    to_team_confidence: float = 0.0

    # FL26 names that were matched to
    matched_player_name: str = ""
    matched_from_team: str = ""
    matched_to_team: str = ""

    @property
    def is_fully_matched(self) -> bool:
        """True if player, source team, and dest team are all matched."""
        return all([
            self.player_id is not None,
            self.from_team_id is not None,
            self.to_team_id is not None,
        ])

    @property
    def min_confidence(self) -> float:
        """Lowest confidence score among all matches."""
        return min(self.player_confidence, self.from_team_confidence, self.to_team_confidence)

    def __str__(self):
        status = "✓" if self.is_fully_matched else "✗"
        return (
            f"[{status}] {self.transfer.player_name} (id={self.player_id}, "
            f"conf={self.player_confidence:.0f}%): "
            f"{self.transfer.from_club} → {self.transfer.to_club}"
        )
