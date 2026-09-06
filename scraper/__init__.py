"""
Scraper package for FL Daily Edit.
"""
from scraper.fotmob import FotmobScraper, fetch_fotmob_transfers
from scraper.matcher import NameMatcher
from scraper.models import CaptainUpdate, MatchedTransfer, ScrapeResult, Transfer

__all__ = [
    "FotmobScraper",
    "fetch_fotmob_transfers",
    "NameMatcher",
    "CaptainUpdate",
    "MatchedTransfer",
    "ScrapeResult",
    "Transfer",
]
