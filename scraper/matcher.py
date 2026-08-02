"""
Fuzzy name matching engine for mapping scraped names to FL26 database names.

Uses a multi-strategy approach:
1. Manual override lookup (highest priority)
2. Exact match after normalization
3. Context-aware roster confirmation (boosts confidence if player is in from_team)
4. token_set_ratio (handles extra middle names / mononyms)
5. token_sort_ratio (handles word order: "Ronaldo Cristiano" vs "Cristiano Ronaldo")
6. WRatio (weighted combination, general fallback)
7. Club prefix/suffix normalization (strips FC, CF, AC, SV, etc.)
"""
import json
import logging
import re
import unicodedata
from pathlib import Path
from typing import Optional

from rapidfuzz import fuzz, process

import config

logger = logging.getLogger(__name__)

# Common club prefix/suffix tokens that often differ between data sources
_CLUB_AFFIX_REGEX = re.compile(
    r"\b(fc|cf|sc|ac|cd|ud|fk|sk|as|us|ss|sv|vfl|vfb|spvgg|kaa|krc|rsc|vv|afc|bsc|ogc|club|calcio|sad|kv|tsv|fsv|1\.\s*fc|1\.fc)\b",
    re.IGNORECASE,
)

# Position categorization maps
_POS_GK = {"GK"}
_POS_DEF = {"CB", "LB", "RB", "DF", "LWB", "RWB"}
_POS_MID = {"DMF", "CMF", "AMF", "LMF", "RMF", "DM", "CM", "CAM", "AM", "LM", "RM", "MF"}
_POS_FWD = {"LWF", "RWF", "SS", "CF", "ST", "LW", "RW", "FW"}


def _get_pos_category(pos: str) -> str:
    """Return broad category: 'GK', 'DEF', 'MID', 'FWD', or 'UNKNOWN'."""
    p = (pos or "").strip().upper()
    if p in _POS_GK:
        return "GK"
    if p in _POS_DEF:
        return "DEF"
    if p in _POS_MID:
        return "MID"
    if p in _POS_FWD:
        return "FWD"
    return "UNKNOWN"


def _is_position_compatible(trans_pos: str, pes_pos: str) -> bool:
    """
    Check if a transfer's reported position is compatible with a database player's position.
    Strictly forbids GK ⇄ Outfield mismatches.
    """
    if not trans_pos or not pes_pos:
        return True
    cat1 = _get_pos_category(trans_pos)
    cat2 = _get_pos_category(pes_pos)
    if cat1 == "UNKNOWN" or cat2 == "UNKNOWN":
        return True
    # GK must strictly match GK
    if cat1 == "GK" or cat2 == "GK":
        return cat1 == cat2
    return True


def _normalize(name: str) -> str:
    """
    Normalize a name for comparison:
    - Lowercase
    - Strip diacritics (é → e, ü → u, ñ → n)
    - Collapse whitespace
    - Strip leading/trailing whitespace
    """
    nfkd = unicodedata.normalize("NFKD", name)
    stripped = "".join(c for c in nfkd if unicodedata.category(c) != "Mn")
    return " ".join(stripped.lower().split())


def _clean_club_name(name: str) -> str:
    """
    Normalize a club name and strip common noise affixes (FC, CF, AC, etc.).
    Example: 'FC Barcelona' → 'barcelona', 'Union Saint-Gilloise' → 'union saint-gilloise'
    """
    norm = _normalize(name)
    cleaned = _CLUB_AFFIX_REGEX.sub(" ", norm)
    return " ".join(cleaned.split())


def _load_json(path: Path) -> dict:
    """Load a JSON file, return empty dict if missing."""
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


class NameMatcher:
    """
    Matches scraped names from FotMob against a database of known names (from FL26).
    Uses rapidfuzz with multiple scoring strategies, phonetic matching,
    club context disambiguation, and alias tables.

    Usage:
        matcher = NameMatcher()
        matcher.load_player_db({"Lionel Messi": 12345, "Kylian Mbappé": 67890})
        matcher.load_team_db({"FC Barcelona": 101, "Paris Saint-Germain": 202})

        # Match scraped names
        pid, name, conf = matcher.match_player("L. Messi", position="RW")
        tid, name, conf = matcher.match_team("PSG")
    """

    def __init__(self):
        # {normalized_name: (original_name, id)}
        self._player_db: dict[str, tuple[str, int]] = {}
        self._team_db: dict[str, tuple[str, int]] = {}

        # {player_id: [(normalized_name, original_name)]}
        self._player_id_to_names: dict[int, list[tuple[str, str]]] = {}

        # {player_id: position_str}
        self._player_positions: dict[int, str] = {}
        # {player_id: nationality_str}
        self._player_nationalities: dict[int, str] = {}
        # {player_id: age_int}
        self._player_ages: dict[int, int] = {}

        # For rapidfuzz: list of normalized names
        self._player_names: list[str] = []
        self._team_names: list[str] = []

        # Cleaned team names for fallback affix-insensitive matching: {cleaned_name: (orig_name, tid)}
        self._cleaned_team_db: dict[str, tuple[str, int]] = {}
        self._cleaned_team_names: list[str] = []

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

    def load_player_db(
        self,
        players: dict[str, int],
        positions: Optional[dict[int, str]] = None,
        nationalities: Optional[dict[int, str]] = None,
        ages: Optional[dict[int, int]] = None,
    ):
        """
        Load the FL26 player database.

        Args:
            players: {player_name: player_id}
            positions: Optional {player_id: position_string} (e.g. {123: 'GK', 456: 'CF'})
            nationalities: Optional {player_id: nationality_string}
            ages: Optional {player_id: age_int}
        """
        self._player_db.clear()
        self._player_id_to_names.clear()
        self._player_positions.clear()
        self._player_nationalities.clear()
        self._player_ages.clear()

        if positions:
            self._player_positions = {pid: pos for pid, pos in positions.items()}
        if nationalities:
            self._player_nationalities = {pid: nat for pid, nat in nationalities.items()}
        if ages:
            self._player_ages = {pid: age for pid, age in ages.items()}

        for name, pid in players.items():
            norm = _normalize(name)
            self._player_db[norm] = (name, pid)
            if pid not in self._player_id_to_names:
                self._player_id_to_names[pid] = []
            self._player_id_to_names[pid].append((norm, name))

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
        self._cleaned_team_db.clear()

        for name, tid in teams.items():
            if clubs_only and tid <= 100:
                continue
            norm = _normalize(name)
            self._team_db[norm] = (name, tid)

            cleaned = _clean_club_name(name)
            if cleaned and cleaned not in self._cleaned_team_db:
                self._cleaned_team_db[cleaned] = (name, tid)

        self._team_names = list(self._team_db.keys())
        self._cleaned_team_names = list(self._cleaned_team_db.keys())
        logger.info(f"Loaded {len(self._team_db)} teams into matcher (clubs_only={clubs_only})")

    def _score_player(
        self,
        query_norm: str,
        candidate_norm: str,
        position: Optional[str] = None,
        candidate_pid: Optional[int] = None,
        nationality: Optional[str] = None,
        age: Optional[int] = None,
    ) -> float:
        """
        Calculate a composite fuzzy score for player names with Tri-Factor verification
        (Name match + Positional compatibility + Nationality/Age alignment).
        """
        # Position Compatibility Gate
        if position and candidate_pid and self._player_positions:
            pes_pos = self._player_positions.get(candidate_pid, "")
            if not _is_position_compatible(position, pes_pos):
                # Severe penalty for position mismatch (e.g. GK matched to striker)
                return 0.0

        # 1. token_set_ratio handles extra middle names / substrings cleanly
        s_set = fuzz.token_set_ratio(query_norm, candidate_norm)
        # 2. token_sort_ratio handles word order
        s_sort = fuzz.token_sort_ratio(query_norm, candidate_norm)
        # 3. WRatio as general weighted metric
        s_w = fuzz.WRatio(query_norm, candidate_norm)

        base_score = max(s_set, s_sort, s_w)

        # Check for Initial + Surname match (e.g. "k mbappe" vs "kylian mbappe")
        q_tokens = query_norm.split()
        c_tokens = candidate_norm.split()
        if len(q_tokens) >= 2 and len(c_tokens) >= 2:
            if len(q_tokens[0]) == 1 and q_tokens[0] == c_tokens[0][0] and q_tokens[-1] == c_tokens[-1]:
                base_score = max(base_score, 90.0)

        # Length penalty for very short query strings matching long candidates
        if len(query_norm) < 5 and len(candidate_norm) >= len(query_norm) * 2.5:
            base_score *= 0.75

        # Position alignment bonus if broad category matches
        if position and candidate_pid and self._player_positions:
            pes_pos = self._player_positions.get(candidate_pid, "")
            if pes_pos and _get_pos_category(position) == _get_pos_category(pes_pos):
                base_score = min(100.0, base_score + 2.0)

        # Tri-Factor Nationality verification
        if nationality and candidate_pid and self._player_nationalities:
            db_nat = self._player_nationalities.get(candidate_pid, "")
            if db_nat:
                norm_scraped_nat = _normalize(nationality)
                norm_db_nat = _normalize(db_nat)
                if norm_scraped_nat in norm_db_nat or norm_db_nat in norm_scraped_nat:
                    base_score = min(100.0, base_score + 6.0)

        # Tri-Factor Age verification
        if age and age > 0 and candidate_pid and self._player_ages:
            db_age = self._player_ages.get(candidate_pid, 0)
            if db_age > 0:
                diff = abs(age - db_age)
                if diff <= 1:
                    base_score = min(100.0, base_score + 4.0)
                elif diff > 4:
                    base_score = max(0.0, base_score - 10.0)

        return min(100.0, float(base_score))

    def match_player(
        self,
        scraped_name: str,
        threshold: float = 0,
        from_team_id: Optional[int] = None,
        to_team_id: Optional[int] = None,
        team_player_map: Optional[dict[int, list[int]]] = None,
        position: Optional[str] = None,
        nationality: Optional[str] = None,
        age: Optional[int] = None,
    ) -> tuple[Optional[int], str, float]:
        """
        Match a scraped player name to the FL26 database with optional context verification.

        Args:
            scraped_name: Player name from FotMob.
            threshold: Minimum confidence. 0 = use config default.
            from_team_id: Optional origin club ID for context-aware disambiguation.
            to_team_id: Optional destination/parent club ID for loan returns/verification.
            team_player_map: Optional {team_id: [player_ids]} map to confirm player is on roster.
            position: Optional position string (e.g. 'GK', 'CB', 'CF') from transfer metadata.
            nationality: Optional player nationality.
            age: Optional player age.

        Returns:
            (player_id, matched_fl26_name, confidence)
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

        # Step 2: Exact match after normalization
        if norm_query in self._player_db:
            orig, pid = self._player_db[norm_query]
            if not position or not self._player_positions or _is_position_compatible(position, self._player_positions.get(pid, "")):
                return pid, orig, 100.0

        # Step 3: Context-Aware Candidate Search
        candidates = process.extract(
            norm_query,
            self._player_names,
            scorer=fuzz.token_set_ratio,
            limit=10,
        )

        best_id, best_name, best_conf = None, "", 0.0

        # Check relevant club rosters (from_team_id for departures/transfers, to_team_id for loan returns)
        relevant_rosters = set()
        if team_player_map:
            if from_team_id is not None and from_team_id in team_player_map:
                relevant_rosters.update(team_player_map[from_team_id])
            if to_team_id is not None and to_team_id in team_player_map:
                relevant_rosters.update(team_player_map[to_team_id])

        if relevant_rosters:
            for cand_norm, cand_raw_score, _ in candidates:
                if cand_norm in self._player_db:
                    orig, pid = self._player_db[cand_norm]
                    if pid in relevant_rosters:
                        composite_score = self._score_player(
                            norm_query,
                            cand_norm,
                            position=position,
                            candidate_pid=pid,
                            nationality=nationality,
                            age=age,
                        )
                        if composite_score >= 68.0:
                            logger.debug(
                                f"Context confirmed: '{scraped_name}' found in club roster "
                                f"as '{orig}' (pid={pid}, score={composite_score:.1f}%)"
                            )
                            return pid, orig, 100.0

        # Step 4: General Multi-Scorer Matching with position check
        for cand_norm, _, _ in candidates:
            cand_orig, cand_pid = self._player_db[cand_norm]
            score = self._score_player(
                norm_query,
                cand_norm,
                position=position,
                candidate_pid=cand_pid,
                nationality=nationality,
                age=age,
            )
            if score > best_conf:
                best_conf = score
                best_name = cand_norm

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
            scraped_name: Team name from FotMob.
            threshold: Minimum confidence. 0 = use config default.

        Returns:
            (team_id, matched_fl26_name, confidence)
        """
        if threshold == 0:
            threshold = config.MATCH_THRESHOLD_TEAM

        if not self._team_names:
            logger.warning("Team database is empty — load it first")
            return None, "", 0.0

        # Step 1: Check aliases
        alias_target = self._team_aliases.get(scraped_name)
        if not alias_target:
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

        # Step 3: Exact match on cleaned club name (stripping FC, CF, AC, etc.)
        cleaned_query = _clean_club_name(scraped_name)
        if cleaned_query and cleaned_query in self._cleaned_team_db:
            orig, tid = self._cleaned_team_db[cleaned_query]
            return tid, orig, 98.0

        # Step 4: Fuzzy match across standard team names
        best_id, best_name, best_conf = None, "", 0.0

        for scorer in (fuzz.token_set_ratio, fuzz.token_sort_ratio, fuzz.WRatio):
            result = process.extractOne(norm_query, self._team_names, scorer=scorer)
            if result and result[1] > best_conf:
                best_conf = result[1]
                best_name = result[0]

        # Step 5: Fallback fuzzy match on cleaned club names
        if best_conf < threshold and cleaned_query and self._cleaned_team_names:
            for scorer in (fuzz.token_set_ratio, fuzz.token_sort_ratio):
                result = process.extractOne(cleaned_query, self._cleaned_team_names, scorer=scorer)
                if result and result[1] > best_conf:
                    best_conf = result[1]
                    best_name = result[0]
                    if best_name in self._cleaned_team_db:
                        orig, tid = self._cleaned_team_db[best_name]
                        if best_conf >= threshold:
                            return tid, orig, best_conf

        if best_conf >= threshold and best_name in self._team_db:
            orig, tid = self._team_db[best_name]
            return tid, orig, best_conf

        logger.debug(
            f"No team match for '{scraped_name}' "
            f"(best: '{best_name}' at {best_conf:.0f}%, threshold: {threshold}%)"
        )
        return None, "", best_conf

