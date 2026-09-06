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
from collections.abc import Iterable, Mapping
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

_NON_CLUB_NAMES = {
    "",
    "free agent",
    "without club",
    "unattached",
    "career break",
    "retired",
}
_CONTEXT_PLAYER_MIN_CONFIDENCE = 90.0

# Position categorization maps
_POS_GK = {"GK", "GOALKEEPER", "KEEPER", "GOALIE"}
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
        # Multiple FL26 players can legitimately have the same normalized name.
        # Keep every candidate instead of silently overwriting all but one.
        self._player_candidates: dict[str, list[tuple[str, int]]] = {}
        self._team_db: dict[str, tuple[str, int]] = {}
        self._team_candidates: dict[str, list[tuple[str, int]]] = {}

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
        self._cleaned_team_candidates: dict[str, list[tuple[str, int]]] = {}
        self._cleaned_team_names: list[str] = []

        # Manual overrides
        self._player_overrides: dict[str, str] = {}  # scraped_name → fl26_name
        self._team_aliases: dict[str, str] = {}       # alias → canonical fl26_name
        self._normalized_player_overrides: dict[str, str] = {}
        self._normalized_team_aliases: dict[str, str] = {}

        self._load_overrides()

    def _load_overrides(self):
        """Load manual override files."""
        raw_player_overrides = _load_json(config.NAME_OVERRIDES_FILE)
        raw_team_aliases = _load_json(config.TEAM_ALIASES_FILE)
        self._player_overrides = {
            alias: target
            for alias, target in raw_player_overrides.items()
            if isinstance(alias, str)
            and isinstance(target, str)
            and not alias.startswith("_")
        }
        self._team_aliases = {
            alias: target
            for alias, target in raw_team_aliases.items()
            if isinstance(alias, str)
            and isinstance(target, str)
            and not alias.startswith("_")
        }
        self._normalized_player_overrides = {
            _normalize(alias): target
            for alias, target in self._player_overrides.items()
        }
        self._normalized_team_aliases = {
            _normalize(alias): target
            for alias, target in self._team_aliases.items()
        }
        if self._player_overrides:
            logger.info(f"Loaded {len(self._player_overrides)} player name overrides")
        if self._team_aliases:
            logger.info(f"Loaded {len(self._team_aliases)} team aliases")

    def load_player_db(
        self,
        players: Mapping[str, int] | Iterable[tuple[str, int]],
        positions: Optional[dict[int, str]] = None,
        nationalities: Optional[dict[int, str]] = None,
        ages: Optional[dict[int, int]] = None,
    ):
        """
        Load the FL26 player database.

        Args:
            players: {player_name: player_id} or an iterable of
                (player_name, player_id) pairs. The iterable form preserves
                duplicate names and is preferred for the full FL26 database.
            positions: Optional {player_id: position_string} (e.g. {123: 'GK', 456: 'CF'})
            nationalities: Optional {player_id: nationality_string}
            ages: Optional {player_id: age_int}
        """
        self._player_db.clear()
        self._player_candidates.clear()
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

        records = players.items() if isinstance(players, Mapping) else players
        record_count = 0
        for name, pid in records:
            norm = _normalize(name)
            if not norm:
                continue
            candidates = self._player_candidates.setdefault(norm, [])
            if not any(existing_pid == pid for _, existing_pid in candidates):
                candidates.append((name, pid))
                record_count += 1
            # Retain this compatibility map for callers that inspect it, but
            # matching always uses _player_candidates so names are not lost.
            self._player_db.setdefault(norm, (name, pid))
            if pid not in self._player_id_to_names:
                self._player_id_to_names[pid] = []
            self._player_id_to_names[pid].append((norm, name))

        self._player_names = list(self._player_candidates.keys())
        duplicate_names = sum(1 for values in self._player_candidates.values() if len(values) > 1)
        logger.info(
            f"Loaded {record_count} player records ({len(self._player_names)} unique names, "
            f"{duplicate_names} ambiguous names) into matcher"
        )

    def load_team_db(
        self,
        teams: Mapping[str, int] | Iterable[tuple[str, int]],
        clubs_only: bool = False,
    ):
        """
        Load the FL26 team database.

        Args:
            teams: {team_name: team_id} or an iterable of name/ID pairs.
            clubs_only: Legacy numeric filter for unfiltered databases. Prefer
                passing an already verified club mapping and leaving this False;
                FL26 contains real clubs with IDs at or below 100.
        """
        self._team_db.clear()
        self._team_candidates.clear()
        self._cleaned_team_db.clear()
        self._cleaned_team_candidates.clear()

        records = teams.items() if isinstance(teams, Mapping) else teams
        for name, tid in records:
            if clubs_only and tid <= 100:
                continue
            norm = _normalize(name)
            if not norm:
                continue
            candidates = self._team_candidates.setdefault(norm, [])
            if not any(existing_tid == tid for _, existing_tid in candidates):
                candidates.append((name, tid))
            self._team_db.setdefault(norm, (name, tid))

            cleaned = _clean_club_name(name)
            if cleaned:
                cleaned_candidates = self._cleaned_team_candidates.setdefault(cleaned, [])
                if not any(existing_tid == tid for _, existing_tid in cleaned_candidates):
                    cleaned_candidates.append((name, tid))
                self._cleaned_team_db.setdefault(cleaned, (name, tid))

        self._team_names = list(self._team_candidates.keys())
        self._cleaned_team_names = list(self._cleaned_team_candidates.keys())
        ambiguous = sum(1 for values in self._cleaned_team_candidates.values() if len(values) > 1)
        logger.info(
            f"Loaded {len(self._team_names)} team names into matcher "
            f"({ambiguous} ambiguous cleaned names, clubs_only={clubs_only})"
        )

    def get_team_name(self, team_id: int) -> str:
        """Return the canonical loaded team name for an ID, if available."""
        for candidates in self._team_candidates.values():
            for name, candidate_id in candidates:
                if candidate_id == team_id:
                    return name
        return ""

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
        Calculate a composite fuzzy score with optional position, nationality,
        and age evidence when those fields are actually available.
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

        # Optional nationality evidence
        if nationality and candidate_pid and self._player_nationalities:
            db_nat = self._player_nationalities.get(candidate_pid, "")
            if db_nat:
                norm_scraped_nat = _normalize(nationality)
                norm_db_nat = _normalize(db_nat)
                if norm_scraped_nat in norm_db_nat or norm_db_nat in norm_scraped_nat:
                    base_score = min(100.0, base_score + 6.0)

        # Optional age evidence
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

        # Source context is authoritative for a pending transfer. Destination
        # context is only a fallback for an event already reflected by the base.
        source_roster: set[int] = set()
        destination_roster: set[int] = set()
        if team_player_map:
            if from_team_id is not None and from_team_id in team_player_map:
                source_roster.update(team_player_map[from_team_id])
            if to_team_id is not None and to_team_id in team_player_map:
                destination_roster.update(team_player_map[to_team_id])
        context_groups = [
            (label, roster)
            for label, roster in (
                ("source", source_roster),
                ("destination", destination_roster),
            )
            if roster
        ]

        def resolve_exact(records: list[tuple[str, int]]) -> tuple[str, int] | None:
            compatible = [
                (orig, pid)
                for orig, pid in records
                if not position
                or not self._player_positions
                or _is_position_compatible(position, self._player_positions.get(pid, ""))
            ]
            for _, roster in context_groups:
                contextual = [(orig, pid) for orig, pid in compatible if pid in roster]
                if len(contextual) == 1:
                    return contextual[0]
                if contextual:
                    compatible = contextual
                    break

            if nationality and len(compatible) > 1:
                norm_nat = _normalize(nationality)
                nat_matches = [
                    (orig, pid)
                    for orig, pid in compatible
                    if (db_nat := _normalize(self._player_nationalities.get(pid, "")))
                    and (norm_nat in db_nat or db_nat in norm_nat)
                ]
                if nat_matches:
                    compatible = nat_matches

            if age and age > 0 and len(compatible) > 1:
                known_ages = [
                    (abs(age - self._player_ages[pid]), orig, pid)
                    for orig, pid in compatible
                    if self._player_ages.get(pid, 0) > 0
                ]
                if known_ages:
                    best_diff = min(item[0] for item in known_ages)
                    compatible = [(orig, pid) for diff, orig, pid in known_ages if diff == best_diff]

            return compatible[0] if len(compatible) == 1 else None

        # Step 1: Check manual overrides
        override_target = self._player_overrides.get(scraped_name)
        if not override_target:
            override_target = self._normalized_player_overrides.get(_normalize(scraped_name))
        if override_target:
            norm_target = _normalize(override_target)
            selected = resolve_exact(self._player_candidates.get(norm_target, []))
            if selected:
                orig, pid = selected
                logger.debug(f"Player override: '{scraped_name}' → '{orig}' (id={pid})")
                return pid, orig, 100.0

        norm_query = _normalize(scraped_name)

        # Step 2: Exact match after normalization
        exact_records = self._player_candidates.get(norm_query, [])
        if exact_records:
            selected = resolve_exact(exact_records)
            if selected:
                orig, pid = selected
                return pid, orig, 100.0
            # If compatible exact candidates remain but context cannot
            # disambiguate them, choosing one arbitrarily is unsafe.
            compatible_exact = [
                (orig, pid)
                for orig, pid in exact_records
                if not position
                or not self._player_positions
                or _is_position_compatible(position, self._player_positions.get(pid, ""))
            ]
            if len(compatible_exact) > 1:
                logger.warning(
                    f"Ambiguous exact player name '{scraped_name}' matches "
                    f"{len(compatible_exact)} FL26 players; skipping without roster context"
                )
                return None, "", 100.0

        # Step 3: Context-Aware Candidate Search
        candidates = process.extract(
            norm_query,
            self._player_names,
            scorer=fuzz.token_set_ratio,
            limit=10,
        )

        best_name, best_conf = "", 0.0

        for context_label, roster in context_groups:
            contextual_scores: list[tuple[float, str, int]] = []
            for cand_norm, _, _ in candidates:
                for orig, pid in self._player_candidates.get(cand_norm, []):
                    if pid in roster:
                        composite_score = self._score_player(
                            norm_query,
                            cand_norm,
                            position=position,
                            candidate_pid=pid,
                            nationality=nationality,
                            age=age,
                        )
                        contextual_scores.append((composite_score, orig, pid))
            contextual_scores.sort(reverse=True, key=lambda item: item[0])
            if contextual_scores and contextual_scores[0][0] >= threshold:
                top = contextual_scores[0]
                if top[0] < _CONTEXT_PLAYER_MIN_CONFIDENCE:
                    logger.warning(
                        f"Rejecting weak {context_label} roster match "
                        f"'{scraped_name}': '{top[1]}' ({top[0]:.1f})"
                    )
                    return None, "", top[0]
                runner_up = next(
                    (item for item in contextual_scores[1:] if item[2] != top[2]),
                    None,
                )
                if runner_up is None or top[0] - runner_up[0] >= 3.0:
                    logger.debug(
                        f"{context_label.title()} context confirmed: '{scraped_name}' found in club roster "
                        f"as '{top[1]}' (pid={top[2]}, score={top[0]:.1f}%)"
                    )
                    return top[2], top[1], top[0]
                logger.warning(
                    f"Ambiguous {context_label} roster match '{scraped_name}': "
                    f"'{top[1]}' ({top[0]:.1f}) vs '{runner_up[1]}' "
                    f"({runner_up[0]:.1f}); skipping"
                )
                return None, "", top[0]

        # Step 4: General Multi-Scorer Matching with position check
        scored: list[tuple[float, str, int]] = []
        for cand_norm, _, _ in candidates:
            for cand_orig, cand_pid in self._player_candidates.get(cand_norm, []):
                score = self._score_player(
                    norm_query,
                    cand_norm,
                    position=position,
                    candidate_pid=cand_pid,
                    nationality=nationality,
                    age=age,
                )
                scored.append((score, cand_orig, cand_pid))

        # Use supplied metadata as a disambiguation filter before comparing
        # fuzzy scores. The score itself may cap at 100 for several candidates.
        if position:
            wanted_category = _get_pos_category(position)
            exact_position = [
                item
                for item in scored
                if _get_pos_category(self._player_positions.get(item[2], "")) == wanted_category
            ]
            if wanted_category != "UNKNOWN" and exact_position:
                scored = exact_position
        if nationality and len(scored) > 1:
            norm_nat = _normalize(nationality)
            exact_nationality = [
                item
                for item in scored
                if (db_nat := _normalize(self._player_nationalities.get(item[2], "")))
                and (norm_nat in db_nat or db_nat in norm_nat)
            ]
            if exact_nationality:
                scored = exact_nationality
        if age and age > 0 and len(scored) > 1:
            known_age_items = [
                (abs(age - self._player_ages[item[2]]), item)
                for item in scored
                if self._player_ages.get(item[2], 0) > 0
            ]
            if known_age_items:
                best_age_diff = min(diff for diff, _ in known_age_items)
                scored = [item for diff, item in known_age_items if diff == best_age_diff]

        scored.sort(reverse=True, key=lambda item: item[0])
        if scored:
            best_conf, best_name, best_id = scored[0]
            runner_up = next((item for item in scored[1:] if item[2] != best_id), None)
            ambiguity_margin = 3.0
            if best_conf >= threshold and (
                runner_up is None or best_conf - runner_up[0] >= ambiguity_margin
            ):
                return best_id, best_name, best_conf
            if best_conf >= threshold and runner_up is not None:
                logger.warning(
                    f"Ambiguous player match '{scraped_name}': '{best_name}' ({best_conf:.1f}) "
                    f"vs '{runner_up[1]}' ({runner_up[0]:.1f}); skipping"
                )

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

        # These values deliberately mean "no roster".  Never allow fuzzy
        # matching to reinterpret one as an actual club with a similar name.
        if _normalize(scraped_name) in _NON_CLUB_NAMES:
            return None, "", 100.0

        if not self._team_names:
            logger.warning("Team database is empty — load it first")
            return None, "", 0.0

        # Step 1: Check aliases
        alias_target = self._team_aliases.get(scraped_name)
        if not alias_target:
            alias_target = self._normalized_team_aliases.get(_normalize(scraped_name))

        query_name = alias_target or scraped_name
        norm_query = _normalize(query_name)

        # Step 2: Exact match
        exact_candidates = self._team_candidates.get(norm_query, [])
        if len(exact_candidates) == 1:
            orig, tid = exact_candidates[0]
            if alias_target:
                logger.debug(f"Team alias: '{scraped_name}' → '{orig}' (id={tid})")
            return tid, orig, 100.0
        if len(exact_candidates) > 1:
            logger.warning(f"Ambiguous exact team name '{scraped_name}'; skipping")
            return None, "", 100.0

        # Step 3: Exact match on cleaned club name (stripping FC, CF, AC, etc.)
        cleaned_query = _clean_club_name(query_name)
        cleaned_candidates = self._cleaned_team_candidates.get(cleaned_query, [])
        if len(cleaned_candidates) == 1:
            orig, tid = cleaned_candidates[0]
            return tid, orig, 100.0 if alias_target else 98.0
        if len(cleaned_candidates) > 1:
            logger.warning(
                f"Ambiguous cleaned team name '{scraped_name}' matches "
                f"{len(cleaned_candidates)} FL26 clubs; skipping"
            )
            return None, "", 98.0

        # Step 4: Fuzzy match across standard team names
        standard_scores = sorted(
            (
                (
                    max(
                        fuzz.token_set_ratio(norm_query, candidate),
                        fuzz.token_sort_ratio(norm_query, candidate),
                        fuzz.WRatio(norm_query, candidate),
                    ),
                    candidate,
                )
                for candidate in self._team_names
            ),
            reverse=True,
        )
        best_conf, best_name = standard_scores[0] if standard_scores else (0.0, "")
        runner_up_conf = standard_scores[1][0] if len(standard_scores) > 1 else -1.0
        ambiguity_margin = 3.0
        if best_conf >= threshold and best_conf - runner_up_conf >= ambiguity_margin:
            candidates = self._team_candidates.get(best_name, [])
            if len(candidates) == 1:
                orig, tid = candidates[0]
                return tid, orig, 100.0 if alias_target else best_conf

        # Step 5: Fallback fuzzy match on cleaned club names
        if cleaned_query and self._cleaned_team_names:
            cleaned_scores = sorted(
                (
                    (
                        max(
                            fuzz.token_set_ratio(cleaned_query, candidate),
                            fuzz.token_sort_ratio(cleaned_query, candidate),
                        ),
                        candidate,
                    )
                    for candidate in self._cleaned_team_names
                ),
                reverse=True,
            )
            clean_conf, clean_name = cleaned_scores[0]
            clean_runner_up = cleaned_scores[1][0] if len(cleaned_scores) > 1 else -1.0
            if clean_conf > best_conf:
                best_conf, best_name = clean_conf, clean_name
            if clean_conf >= threshold and clean_conf - clean_runner_up >= ambiguity_margin:
                candidates = self._cleaned_team_candidates.get(clean_name, [])
                if len(candidates) == 1:
                    orig, tid = candidates[0]
                    return tid, orig, 100.0 if alias_target else clean_conf

        logger.debug(
            f"No team match for '{scraped_name}' "
            f"(best: '{best_name}' at {best_conf:.0f}%, threshold: {threshold}%)"
        )
        return None, "", best_conf
