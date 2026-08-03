"""
Scraper package for FL Daily Edit.
"""
from scraper.fotmob import FotmobScraper, fetch_fotmob_transfers
from scraper.matcher import NameMatcher
from scraper.models import MatchedTransfer, Transfer

__all__ = [
    "FotmobScraper",
    "fetch_fotmob_transfers",
    "NameMatcher",
    "MatchedTransfer",
    "Transfer",
]
