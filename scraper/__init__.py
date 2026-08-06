"""
Scraper package for FL Daily Edit.
"""
from scraper.fotmob import FotmobScraper, fetch_fotmob_transfers
from scraper.matcher import NameMatcher
from scraper.models import MatchedTransfer, Transfer
from scraper.pes_retro_snapshot import (
    SOURCE_MODEL,
    PesRetroSnapshotError,
    profile_from_snapshot,
    profile_to_snapshot,
)
from scraper.pes_retro_stats import (
    PesRetroStatsError,
    PesRetroStatsProfile,
    fetch_pes_retro_stats_profile,
    parse_pes_retro_stats_profile,
    parse_pes_retro_stats_url,
)

__all__ = [
    "FotmobScraper",
    "fetch_fotmob_transfers",
    "NameMatcher",
    "MatchedTransfer",
    "Transfer",
    "PesRetroStatsError",
    "PesRetroStatsProfile",
    "SOURCE_MODEL",
    "PesRetroSnapshotError",
    "profile_from_snapshot",
    "profile_to_snapshot",
    "fetch_pes_retro_stats_profile",
    "parse_pes_retro_stats_profile",
    "parse_pes_retro_stats_url",
]
