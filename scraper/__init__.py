"""
Scraper package for FL Daily Edit.
"""
from scraper.fotmob import FotmobScraper, fetch_fotmob_transfers
from scraper.matcher import NameMatcher
from scraper.models import MatchedTransfer, Transfer
from scraper.player_draft import (
    DraftSourceError,
    PlayerDraftSource,
    fetch_sortitoutsi_player_profile,
    parse_sortitoutsi_person_url,
    parse_sortitoutsi_player_profile,
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
    "DraftSourceError",
    "PlayerDraftSource",
    "fetch_sortitoutsi_player_profile",
    "parse_sortitoutsi_person_url",
    "parse_sortitoutsi_player_profile",
    "PesRetroStatsError",
    "PesRetroStatsProfile",
    "fetch_pes_retro_stats_profile",
    "parse_pes_retro_stats_profile",
    "parse_pes_retro_stats_url",
]
