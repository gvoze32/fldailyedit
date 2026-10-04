"""Per-save FotMob ↔ PES club identity derived from provider data and the save.

Bindings come from two kinds of evidence only:

* an unambiguous name pass between the selected save's clubs and the crawled
  FotMob team catalog (``data/fotmob_teams.json``), and
* squad overlap: a complete FotMob current squad whose players sit on one
  save club's roster.  Overlap bindings are cached per save scope and win
  over name bindings.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path

from rapidfuzz import fuzz, process

import config
from scraper.matcher import NameMatcher, _clean_club_name
from scraper.models import SquadSnapshot
from scraper.text import fold_text

logger = logging.getLogger(__name__)

UNRESOLVED = object()

MATCH_THRESHOLD = 90.0
FUZZY_MARGIN = 5.0
CONFIDENT_NAME_SCORE = 98.0
OVERLAP_MIN_SHARE = 0.75
OVERLAP_MIN_MEMBERS = 11
OVERLAP_RUNNER_UP_FACTOR = 3
_OVERLAP_PLAYER_CONFIDENCE = 90.0
_NON_CLUB_LABELS = {"", "free agent", "without club", "unattached", "career break", "retired"}
_CATEGORY_RE = re.compile(
    r"\b(women|woman|ladies|feminine|femenino|feminino|frauen|academy|youth|"
    r"reserves?|reserve|primavera|next\s+gen|u[ -]?\d{2}|ii|b)\b",
    re.IGNORECASE,
)
_FUZZY_SCORERS = (fuzz.token_set_ratio, fuzz.token_sort_ratio, fuzz.WRatio)


def load_fotmob_teams(path: Path | None = None) -> list[dict]:
    """Load the crawled FotMob team catalog."""
    catalog_path = path or config.DATA_DIR / "fotmob_teams.json"
    payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{catalog_path} must contain a JSON array")
    return payload


def _atomic_write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        temp_path = Path(temp_name)
        if temp_path.exists():
            temp_path.unlink()


def _category_compatible(pes_name: str, fotmob_name: str) -> bool:
    """Require PES and FotMob to name the same youth/women/reserve category."""
    pes_markers = {marker.casefold() for marker in _CATEGORY_RE.findall(pes_name)}
    fotmob_markers = {marker.casefold() for marker in _CATEGORY_RE.findall(fotmob_name)}
    return pes_markers == fotmob_markers


def _has_meaningful_fuzzy_overlap(pes_name: str, fotmob_name: str) -> bool:
    """Require a substantive token link for fuzzy-only club matches."""
    pes_tokens = set(_clean_club_name(pes_name).split())
    fotmob_tokens = set(_clean_club_name(fotmob_name).split())
    if any(len(token) >= 3 for token in pes_tokens & fotmob_tokens):
        return True
    for pes_token in pes_tokens:
        for fotmob_token in fotmob_tokens:
            shorter, longer = sorted((pes_token, fotmob_token), key=len)
            if len(shorter) >= 4 and longer.startswith(shorter):
                return True
    return False


def _normalize_catalog(fotmob_teams: Sequence[dict]) -> list[dict]:
    normalized: list[dict] = []
    seen: set[int] = set()
    for item in fotmob_teams:
        if not isinstance(item, dict) or "fotmob_id" not in item:
            raise ValueError("FotMob team entry is missing fotmob_id")
        team_id = int(item["fotmob_id"])
        name = str(item.get("name") or item.get("slug") or "").strip()
        if not name or team_id in seen:
            raise ValueError(f"Invalid or duplicate FotMob team entry: {team_id}")
        seen.add(team_id)
        clean = dict(item)
        clean["fotmob_id"] = team_id
        clean["name"] = name
        normalized.append(clean)
    return normalized


def _name_bindings(pes_clubs: Mapping[int, str], catalog: list[dict]) -> dict[int, dict]:
    """Return one-to-one PES → FotMob mappings that names alone prove."""
    folded_names = [fold_text(item["name"]) for item in catalog]
    by_fold: dict[str, list[int]] = defaultdict(list)
    by_clean: dict[str, list[int]] = defaultdict(list)
    for index, item in enumerate(catalog):
        by_fold[folded_names[index]].append(index)
        by_clean[_clean_club_name(item["name"])].append(index)

    proposals: dict[int, tuple[float, dict]] = {}
    for pes_team_id, pes_name in pes_clubs.items():
        norm_pes = fold_text(pes_name)
        tiers: dict[int, tuple[int, float]] = {}
        for index in by_fold.get(norm_pes, ()):
            tiers[index] = (3, 100.0)
        for index in by_clean.get(_clean_club_name(pes_name), ()):
            tiers.setdefault(index, (2, 98.0))
        fuzzy: dict[int, float] = {}
        for scorer in _FUZZY_SCORERS:
            for _, score, index in process.extract(
                norm_pes,
                folded_names,
                scorer=scorer,
                limit=None,
                score_cutoff=MATCH_THRESHOLD - FUZZY_MARGIN,
            ):
                fuzzy[index] = max(fuzzy.get(index, 0.0), float(score))
        for index, score in fuzzy.items():
            tiers.setdefault(index, (1, score))

        scored = sorted(
            (
                (tier, score, catalog[index])
                for index, (tier, score) in tiers.items()
                if _category_compatible(pes_name, catalog[index]["name"])
            ),
            key=lambda candidate: (candidate[0], candidate[1]),
            reverse=True,
        )
        if not scored:
            continue
        best_tier, best_score, best = scored[0]
        same_tier_runner = next(
            (candidate for candidate in scored[1:] if candidate[0] == best_tier),
            None,
        )
        if best_score < MATCH_THRESHOLD:
            continue
        if same_tier_runner is not None and best_score - same_tier_runner[1] < FUZZY_MARGIN:
            tied = [
                candidate
                for candidate in scored
                if candidate[0] == best_tier and best_score - candidate[1] < FUZZY_MARGIN
            ]
            # FotMob's long-lived senior identities use legacy IDs; later
            # women/youth/duplicate sitemap identities use much larger IDs.
            # Only use this signal when it leaves exactly one candidate.
            legacy = [candidate for candidate in tied if candidate[2]["fotmob_id"] < 200_000]
            if len(legacy) != 1:
                continue
            best_tier, best_score, best = legacy[0]
        if best_tier == 1 and not _has_meaningful_fuzzy_overlap(pes_name, best["name"]):
            continue
        proposals[pes_team_id] = (best_score, best)

    proposed_by_fotmob: dict[int, int] = Counter(
        item["fotmob_id"] for _, item in proposals.values()
    )
    return {
        pes_team_id: {
            **item,
            "pes_team_id": pes_team_id,
            "pes_team_name": pes_clubs[pes_team_id],
            "match_score": round(score, 1),
            "identity_source": "unambiguous_name",
        }
        for pes_team_id, (score, item) in proposals.items()
        if proposed_by_fotmob[item["fotmob_id"]] == 1
    }


def _load_cache(cache_path: Path, save_scope: str) -> dict[int, int]:
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        logger.warning("Ignoring unreadable club identity cache %s: %s", cache_path, exc)
        return {}
    scoped = payload.get(save_scope) if isinstance(payload, dict) else None
    if not isinstance(scoped, dict):
        return {}
    learned: dict[int, int] = {}
    for raw_fotmob_id, raw_pes_id in scoped.items():
        try:
            learned[int(raw_fotmob_id)] = int(raw_pes_id)
        except (TypeError, ValueError):
            logger.warning("Ignoring malformed club identity cache entry %r", raw_fotmob_id)
    return learned


class ClubIdentityIndex:
    """One-to-one FotMob ↔ PES club bindings for one selected save."""

    def __init__(
        self,
        pes_clubs: Mapping[int, str],
        catalog: list[dict],
        *,
        cache_path: Path,
        save_scope: str,
    ):
        self._pes_clubs = {int(team_id): str(name) for team_id, name in pes_clubs.items()}
        self._catalog = {item["fotmob_id"]: item for item in catalog}
        self._cache_path = Path(cache_path)
        self._save_scope = save_scope
        self._by_pes: dict[int, dict] = {}
        self._by_fotmob: dict[int, int] = {}
        self._learned: dict[int, int] = {}
        self._provider_names: dict[int, set[str]] = defaultdict(set)
        self._alias_ids: dict[str, set[int]] = {}
        self._clean_alias_ids: dict[str, set[int]] = {}
        self._matcher = NameMatcher()
        self._matcher.load_team_db([(name, team_id) for team_id, name in self._pes_clubs.items()])
        self._roster_matcher_key: tuple | None = None
        self._roster_matcher: NameMatcher | None = None
        self._owners_key: tuple | None = None
        self._owners: dict[int, set[int]] = {}
        pes_folds: dict[str, set[int]] = defaultdict(set)
        for team_id, name in self._pes_clubs.items():
            pes_folds[fold_text(name)].add(team_id)
        self._pes_folds = dict(pes_folds)

    # -- binding -----------------------------------------------------------

    def _bind(self, entry: dict) -> None:
        fotmob_id = int(entry["fotmob_id"])
        pes_team_id = int(entry["pes_team_id"])
        previous_pes = self._by_fotmob.pop(fotmob_id, None)
        if previous_pes is not None:
            self._by_pes.pop(previous_pes, None)
        previous = self._by_pes.pop(pes_team_id, None)
        if previous is not None:
            self._by_fotmob.pop(int(previous["fotmob_id"]), None)
            self._learned.pop(int(previous["fotmob_id"]), None)
        self._by_pes[pes_team_id] = entry
        self._by_fotmob[fotmob_id] = pes_team_id

    def _overlap_entry(self, fotmob_id: int, pes_team_id: int, name: str, url: str, score: float) -> dict:
        source = self._catalog.get(fotmob_id) or {
            "fotmob_id": fotmob_id,
            "name": name or self._pes_clubs[pes_team_id],
            "slug": "",
            "url": url or f"https://www.fotmob.com/teams/{fotmob_id}/overview",
        }
        return {
            **source,
            "pes_team_id": pes_team_id,
            "pes_team_name": self._pes_clubs[pes_team_id],
            "match_score": round(score, 1),
            "identity_source": "squad_overlap",
        }

    def _apply_learned(self, fotmob_id: int, pes_team_id: int, entry: dict) -> None:
        current = self._by_fotmob.get(fotmob_id)
        holder = self._by_pes.get(pes_team_id)
        if current not in (None, pes_team_id) or (
            holder is not None and int(holder["fotmob_id"]) != fotmob_id
        ):
            logger.warning(
                "Squad overlap rebinds FotMob %s to PES %s (was FotMob→PES %s, PES held FotMob %s)",
                fotmob_id,
                pes_team_id,
                current,
                holder["fotmob_id"] if holder else None,
            )
        self._bind(entry)
        self._learned[fotmob_id] = pes_team_id
        self._rebuild_aliases()

    def _rebuild_aliases(self) -> None:
        pes_folds = self._pes_folds
        alias_ids: dict[str, set[int]] = defaultdict(set)
        clean_alias_ids: dict[str, set[int]] = defaultdict(set)
        for pes_team_id, entry in self._by_pes.items():
            fotmob_id = int(entry["fotmob_id"])
            names = {str(entry.get("name") or "")}
            slug = str(entry.get("slug") or "")
            if slug:
                names.add(slug.replace("-", " "))
            names |= self._provider_names.get(fotmob_id, set())
            for alias in names:
                folded = fold_text(alias)
                if not folded or pes_folds.get(folded, {pes_team_id}) != {pes_team_id}:
                    continue
                alias_ids[folded].add(pes_team_id)
                cleaned = _clean_club_name(alias)
                if cleaned:
                    clean_alias_ids[cleaned].add(pes_team_id)
        self._alias_ids = dict(alias_ids)
        self._clean_alias_ids = dict(clean_alias_ids)

    # -- lookups -----------------------------------------------------------

    def pes_for_fotmob(self, fotmob_id: int) -> int | None:
        try:
            return self._by_fotmob.get(int(fotmob_id))
        except (TypeError, ValueError):
            return None

    def fotmob_for_pes(self, pes_team_id: int) -> int | None:
        try:
            entry = self._by_pes.get(int(pes_team_id))
        except (TypeError, ValueError):
            return None
        return int(entry["fotmob_id"]) if entry else None

    def resolve_name(self, name: str) -> int | None | object:
        """Return a PES club ID, None when confidently absent, else UNRESOLVED."""
        clean = (name or "").strip()
        folded = fold_text(clean)
        if folded in _NON_CLUB_LABELS:
            return None
        alias_hits = self._alias_ids.get(folded) or self._clean_alias_ids.get(
            _clean_club_name(clean)
        )
        if alias_hits:
            return next(iter(alias_hits)) if len(alias_hits) == 1 else UNRESOLVED
        team_id, _, confidence = self._matcher.match_team(clean)
        if team_id is not None:
            return team_id if confidence >= CONFIDENT_NAME_SCORE else UNRESOLVED
        if confidence >= config.MATCH_THRESHOLD_TEAM:
            return UNRESOLVED
        return None

    def entries(self) -> list[dict]:
        return sorted((dict(entry) for entry in self._by_pes.values()), key=lambda item: item["fotmob_id"])

    def aliases(self) -> dict[str, str]:
        """Provider-name aliases → PES club name, for NameMatcher.load_team_aliases."""
        aliases: dict[str, str] = {}
        for pes_team_id, entry in self._by_pes.items():
            target = self._pes_clubs[pes_team_id]
            for alias in {str(entry.get("name") or "")} | self._provider_names.get(
                int(entry["fotmob_id"]), set()
            ):
                folded = fold_text(alias)
                if folded and self._alias_ids.get(folded) == {pes_team_id}:
                    aliases[alias] = target
        return aliases

    # -- learning ----------------------------------------------------------

    def _owners_for_rosters(
        self,
        rosters: Mapping[int, Sequence[int]],
    ) -> dict[int, set[int]]:
        """PES player → save clubs rostering them, rebuilt when rosters change."""
        key = tuple(
            (int(team_id), tuple(player_ids))
            for team_id, player_ids in rosters.items()
            if int(team_id) in self._pes_clubs
        )
        if self._owners_key != key:
            # Kept as a defaultdict: frozenset(owners) then iterates exactly
            # as before, which fixes the roster matcher's candidate order.
            owners: defaultdict[int, set[int]] = defaultdict(set)
            for team_id, player_ids in key:
                for player_id in player_ids:
                    owners[int(player_id)].add(team_id)
            self._owners = owners
            self._owners_key = key
        return self._owners

    def _matcher_for_rosters(
        self,
        rostered: frozenset[int],
        player_names: Mapping[int, str],
    ) -> NameMatcher:
        key = (id(player_names), rostered)
        if self._roster_matcher is None or self._roster_matcher_key != key:
            matcher = NameMatcher()
            matcher.load_player_db(
                [(player_names[player_id], player_id) for player_id in rostered if player_names.get(player_id)]
            )
            self._roster_matcher = matcher
            self._roster_matcher_key = key
        return self._roster_matcher

    def learn_from_snapshot(
        self,
        snapshot: SquadSnapshot,
        rosters: Mapping[int, Sequence[int]],
        player_names: Mapping[int, str],
    ) -> int | None:
        """Bind a FotMob club to the one save club its current squad overlaps."""
        try:
            fotmob_id = int(snapshot.team_id_fotmob)
        except (TypeError, ValueError):
            return None
        if fotmob_id <= 0 or not snapshot.complete or len(snapshot.members) < OVERLAP_MIN_MEMBERS:
            return None
        if snapshot.club_name:
            self._provider_names[fotmob_id].add(snapshot.club_name)

        owners = self._owners_for_rosters(rosters)
        if not owners:
            return None
        matcher = self._matcher_for_rosters(frozenset(owners), player_names)

        counts: Counter[int] = Counter()
        matched = 0
        seen: set[int] = set()
        for member in snapshot.members:
            player_id, _, confidence = matcher.match_player(
                member.player_name,
                threshold=_OVERLAP_PLAYER_CONFIDENCE,
                nationality=member.nationality or None,
                age=member.age or None,
            )
            if player_id is None or confidence < _OVERLAP_PLAYER_CONFIDENCE or player_id in seen:
                continue
            seen.add(player_id)
            teams = owners.get(player_id, set())
            if len(teams) != 1:
                continue
            matched += 1
            counts[next(iter(teams))] += 1
        if not counts:
            return None

        ranked = counts.most_common()
        best_team, best_count = ranked[0]
        runner_count = ranked[1][1] if len(ranked) > 1 else 0
        if (
            best_count < OVERLAP_MIN_MEMBERS
            or best_count < OVERLAP_MIN_SHARE * matched
            or best_count < OVERLAP_RUNNER_UP_FACTOR * runner_count
            or not _category_compatible(self._pes_clubs[best_team], snapshot.club_name or "")
        ):
            logger.info(
                "FotMob club %s (%s) squad overlap inconclusive: best PES %s %s/%s, runner-up %s",
                snapshot.club_name,
                fotmob_id,
                best_team,
                best_count,
                matched,
                runner_count,
            )
            return None

        entry = self._overlap_entry(
            fotmob_id,
            best_team,
            snapshot.club_name,
            snapshot.source_url,
            100.0 * best_count / matched,
        )
        self._apply_learned(fotmob_id, best_team, entry)
        return best_team

    def save(self) -> None:
        """Persist learned overlap bindings for this save scope."""
        try:
            payload = json.loads(self._cache_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                payload = {}
        except FileNotFoundError:
            payload = {}
        except (OSError, ValueError) as exc:
            logger.warning("Replacing unreadable club identity cache %s: %s", self._cache_path, exc)
            payload = {}
        payload[self._save_scope] = {
            str(fotmob_id): pes_team_id for fotmob_id, pes_team_id in sorted(self._learned.items())
        }
        _atomic_write_json(self._cache_path, payload)


def build_club_identity_index(
    pes_clubs: Mapping[int, str],
    fotmob_teams: Sequence[dict],
    *,
    cache_path: Path,
    save_scope: str,
) -> ClubIdentityIndex:
    """Build the per-save index: name pass, then cached overlap bindings on top."""
    catalog = _normalize_catalog(fotmob_teams)
    index = ClubIdentityIndex(pes_clubs, catalog, cache_path=cache_path, save_scope=save_scope)
    for entry in _name_bindings(index._pes_clubs, catalog).values():
        index._bind(entry)
    for fotmob_id, pes_team_id in _load_cache(Path(cache_path), save_scope).items():
        if pes_team_id not in index._pes_clubs:
            logger.warning(
                "Dropping cached club binding FotMob %s → PES %s: club not in save",
                fotmob_id,
                pes_team_id,
            )
            continue
        index._bind(index._overlap_entry(fotmob_id, pes_team_id, "", "", 100.0))
        index._learned[fotmob_id] = pes_team_id
    index._rebuild_aliases()
    logger.info(
        "Club identity index: %s/%s save clubs bound (%s learned from squad overlap)",
        len(index._by_pes),
        len(index._pes_clubs),
        len(index._learned),
    )
    return index
