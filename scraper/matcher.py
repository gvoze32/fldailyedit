"""
Fuzzy name matching engine for mapping Transfermarkt names to FL26 database names.

Uses a multi-strategy approach:
1. Manual override lookup (highest priority)
2. Exact match after normalization
3. token_sort_ratio (handles word order: "Ronaldo Cristiano" vs "Cristiano Ronaldo")
4. partial_ratio (handles abbreviations: "K. Mbappé" vs "Kylian Mbappé")
5. WRatio (weighted combination, general fallback)
"""
import json
import logging
import unicodedata
from pathlib import Path
from typing import Optional

from rapidfuzz import fuzz, process

import config

logger = logging.getLogger(__name__)


def _normalize(name: str) -> str:
    """
    Normalize a name for comparison:
    - Lowercase
    - Strip diacritics (é → e, ü → u, ñ → n)
    - Collapse whitespace
    - Strip leading/trailing whitespace
    """
    # Decompose unicode chars, then strip combining marks (diacritics)
    nfkd = unicodedata.normalize("NFKD", name)
    stripped = "".join(c for c in nfkd if unicodedata.category(c) != "Mn")
    return " ".join(stripped.lower().split())


def _load_json(path: Path) -> dict:
    """Load a JSON file, return empty dict if missing."""
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


class NameMatcher:
    """
    Matches scraped names (from Transfermarkt) against a database of known names (from FL26).

    Usage:
        matcher = NameMatcher()
        # Load FL26 data
        matcher.load_player_db({"Lionel Messi": 12345, "Kylian Mbappé": 67890})
        matcher.load_team_db({"FC Barcelona": 101, "Paris Saint-Germain": 202})

        # Match scraped names
        pid, name, conf = matcher.match_player("L. Messi")
        tid, name, conf = matcher.match_team("PSG")
    """

    def __init__(self):
        # {normalized_name: (original_name, id)}
        self._player_db: dict[str, tuple[str, int]] = {}
        self._team_db: dict[str, tuple[str, int]] = {}

        # For rapidfuzz: list of normalized names
        self._player_names: list[str] = []
        self._team_names: list[str] = []

        # Manual overrides
        self._player_overrides: dict[str, str] = {}  # scraped_name → fl26_name
        self._team_aliases: dict[str, str] = {}       # alias → canonical fl26_name

        self._load_overrides()

    def _load_overrides(self):
        """Load manual override files."""
        self._player_overrides = _load_json(config.NAME_OVERRIDES_FILE)
        self._team_aliases = _load_json(config.TEAM_ALIASES_FILE)
        if self._player_overrides:
            logger.info(f"Loaded {len(self._player_overrides)} player name overrides")
        if self._team_aliases:
            logger.info(f"Loaded {len(self._team_aliases)} team aliases")

    def load_player_db(self, players: dict[str, int]):
        """
        Load the FL26 player database.

        Args:
            players: {player_name: player_id}
        """
        self._player_db.clear()
        for name, pid in players.items():
            norm = _normalize(name)
            self._player_db[norm] = (name, pid)
        self._player_names = list(self._player_db.keys())
        logger.info(f"Loaded {len(self._player_db)} players into matcher")

    def load_team_db(self, teams: dict[str, int], clubs_only: bool = True):
        """
        Load the FL26 team database.

        Args:
            teams: {team_name: team_id}
            clubs_only: If True, exclude national teams (team_id <= 100).
        """
        self._team_db.clear()
        for name, tid in teams.items():
            if clubs_only and tid <= 100:
                continue
            norm = _normalize(name)
            self._team_db[norm] = (name, tid)
        self._team_names = list(self._team_db.keys())
        logger.info(f"Loaded {len(self._team_db)} teams into matcher (clubs_only={clubs_only})")

    def match_player(
        self,
        scraped_name: str,
        threshold: float = 0,
    ) -> tuple[Optional[int], str, float]:
        """
        Match a scraped player name to the FL26 database.

        Args:
            scraped_name: Player name from Transfermarkt.
            threshold: Minimum confidence. 0 = use config default.

        Returns:
            (player_id, matched_fl26_name, confidence)
            Returns (None, "", 0) if no match found above threshold.
        """
        if threshold == 0:
            threshold = config.MATCH_THRESHOLD_PLAYER

        if not self._player_names:
            logger.warning("Player database is empty — load it first")
            return None, "", 0.0

        # Step 1: Check manual overrides
        override_target = self._player_overrides.get(scraped_name)
        if override_target:
            norm_target = _normalize(override_target)
            if norm_target in self._player_db:
                orig, pid = self._player_db[norm_target]
                logger.debug(f"Player override: '{scraped_name}' → '{orig}' (id={pid})")
                return pid, orig, 100.0

        norm_query = _normalize(scraped_name)

        # Step 2: Exact match
        if norm_query in self._player_db:
            orig, pid = self._player_db[norm_query]
            return pid, orig, 100.0

        # Step 3: Fuzzy match with multiple strategies
        best_id, best_name, best_conf = None, "", 0.0

        # 3a: token_sort_ratio — best for word order differences
        result = process.extractOne(
            norm_query, self._player_names, scorer=fuzz.token_sort_ratio
        )
        if result and result[1] > best_conf:
            best_conf = result[1]
            best_name = result[0]

        # 3b: partial_ratio — best for abbreviations ("K. Mbappé" vs "Kylian Mbappé")
        result = process.extractOne(
            norm_query, self._player_names, scorer=fuzz.partial_ratio
        )
        if result and result[1] > best_conf:
            best_conf = result[1]
            best_name = result[0]

        # 3c: WRatio — weighted combination (general fallback)
        result = process.extractOne(
            norm_query, self._player_names, scorer=fuzz.WRatio
        )
        if result and result[1] > best_conf:
            best_conf = result[1]
            best_name = result[0]

        if best_conf >= threshold and best_name in self._player_db:
            orig, pid = self._player_db[best_name]
            return pid, orig, best_conf

        logger.debug(
            f"No player match for '{scraped_name}' "
            f"(best: '{best_name}' at {best_conf:.0f}%, threshold: {threshold}%)"
        )
        return None, "", best_conf

    def match_team(
        self,
        scraped_name: str,
        threshold: float = 0,
    ) -> tuple[Optional[int], str, float]:
        """
        Match a scraped team name to the FL26 database.

        Args:
            scraped_name: Team name from Transfermarkt.
            threshold: Minimum confidence. 0 = use config default.

        Returns:
            (team_id, matched_fl26_name, confidence)
            Returns (None, "", 0) if no match found above threshold.
        """
        if threshold == 0:
            threshold = config.MATCH_THRESHOLD_TEAM

        if not self._team_names:
            logger.warning("Team database is empty — load it first")
            return None, "", 0.0

        # Step 1: Check aliases
        alias_target = self._team_aliases.get(scraped_name)
        if not alias_target:
            # Try case-insensitive alias lookup
            for alias, target in self._team_aliases.items():
                if alias.lower() == scraped_name.lower():
                    alias_target = target
                    break

        if alias_target:
            norm_target = _normalize(alias_target)
            if norm_target in self._team_db:
                orig, tid = self._team_db[norm_target]
                logger.debug(f"Team alias: '{scraped_name}' → '{orig}' (id={tid})")
                return tid, orig, 100.0

        norm_query = _normalize(scraped_name)

        # Step 2: Exact match
        if norm_query in self._team_db:
            orig, tid = self._team_db[norm_query]
            return tid, orig, 100.0

        # Step 3: Fuzzy match
        best_id, best_name, best_conf = None, "", 0.0

        for scorer in (fuzz.token_sort_ratio, fuzz.partial_ratio, fuzz.WRatio):
            result = process.extractOne(norm_query, self._team_names, scorer=scorer)
            if result and result[1] > best_conf:
                best_conf = result[1]
                best_name = result[0]

        if best_conf >= threshold and best_name in self._team_db:
            orig, tid = self._team_db[best_name]
            return tid, orig, best_conf

        logger.debug(
            f"No team match for '{scraped_name}' "
            f"(best: '{best_name}' at {best_conf:.0f}%, threshold: {threshold}%)"
        )
        return None, "", best_conf
