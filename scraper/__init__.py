"""
Scraper package for FL Daily Edit.
"""
from scraper.fotmob import FotmobScraper, fetch_fotmob_transfers
from scraper.matcher import NameMatcher
from scraper.models import (
    CaptainUpdate,
    ManagerUpdate,
    MatchedTransfer,
    ScrapeResult,
    SquadMember,
    SquadSnapshot,
    Transfer,
)

__all__ = [
    "FotmobScraper",
    "fetch_fotmob_transfers",
    "NameMatcher",
    "CaptainUpdate",
    "ManagerUpdate",
    "MatchedTransfer",
    "ScrapeResult",
    "SquadMember",
    "SquadSnapshot",
    "Transfer",
]
