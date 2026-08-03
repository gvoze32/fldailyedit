"""
Data models for the scraper module.
"""
from dataclasses import dataclass
from typing import Optional


@dataclass
class Transfer:
    """A normalized transfer event from one or more external sources."""
    player_name: str
    from_club: str
    to_club: str
    date: str = ""
    transfer_type: str = "transfer"  # "transfer", "loan", "end of loan", "free transfer"
    fee: str = ""
    league: str = ""
    season: str = ""
    position: str = ""  # e.g. "GK", "CB", "LB", "RB", "CM", "CAM", "LW", "RW", "ST", "CF"
    is_loan: bool = False
    is_contract_extension: bool = False
    market_value: int = 0
    from_club_id_fotmob: Optional[int] = None
    to_club_id_fotmob: Optional[int] = None
    from_club_full_name: str = ""
    to_club_full_name: str = ""
    nationality: str = ""
    age: int = 0
    shirt_number: Optional[int] = None
    player_id_fotmob: Optional[int] = None
    sources: tuple[str, ...] = ("fotmob",)
    source_urls: tuple[str, ...] = ()
    proof_urls: tuple[str, ...] = ()
    player_id_sortitoutsi: Optional[int] = None
    verification_status: str = "verified"
    infer_from_current_roster: bool = False

    def __str__(self):
        pos_badge = f" [{self.position}]" if self.position else ""
        type_badge = f" ({self.transfer_type})"
        return f"{self.player_name}{pos_badge}: {self.from_club} → {self.to_club}{type_badge}"


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
    def _has_valid_from_team(self) -> bool:
        return self.from_team_id is not None and self.from_team_id >= 0

    @property
    def _has_valid_to_team(self) -> bool:
        return self.to_team_id is not None and self.to_team_id >= 0

    @property
    def is_fully_matched(self) -> bool:
        """True if player and at least one valid transfer action can be taken."""
        return self.is_club_transfer or self.is_release or self.is_sign

    @property
    def is_club_transfer(self) -> bool:
        """Both source and destination clubs are known in FL26 database."""
        return (
            self.player_id is not None
            and self._has_valid_from_team
            and self._has_valid_to_team
        )

    @property
    def is_release(self) -> bool:
        """Player exists in source team, but destination is Free Agent or unrepresented club."""
        return (
            self.player_id is not None
            and self._has_valid_from_team
            and self.to_team_id is None
        )

    @property
    def is_sign(self) -> bool:
        """Player exists in FL26, but coming from Free Agent or unrepresented club into an FL26 team."""
        return (
            self.player_id is not None
            and self.from_team_id is None
            and self._has_valid_to_team
        )

    @property
    def action_type(self) -> str:
        if self.transfer.transfer_type == "shirt_number_update":
            return "shirt_number_update"
        if self.is_club_transfer:
            return "transfer"
        elif self.is_release:
            return "release_to_free_agent"
        elif self.is_sign:
            return "sign_from_free_agent"
        return "unmatched"

    @property
    def min_confidence(self) -> float:
        """Lowest confidence score among matched components."""
        confs = [self.player_confidence]
        if self._has_valid_from_team:
            confs.append(self.from_team_confidence)
        if self._has_valid_to_team:
            confs.append(self.to_team_confidence)
        return min(confs) if confs else 0.0

    def __str__(self):
        status = "✓" if self.is_fully_matched else "✗"
        action = f" ({self.action_type})" if self.is_fully_matched else ""
        return (
            f"[{status}] {self.transfer.player_name} (id={self.player_id}, "
            f"conf={self.player_confidence:.0f}%): "
            f"{self.transfer.from_club} → {self.transfer.to_club}{action}"
        )
