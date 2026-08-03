#!/usr/bin/env python3
"""Build a fail-closed, one-to-one FotMob ↔ PES club identity index."""

import json
import logging
import os
import re
import tempfile
from collections import defaultdict
from pathlib import Path

from rapidfuzz import fuzz

import config
from editor import crypto
from editor.editfile import EditFile
from scraper.matcher import NameMatcher, _clean_club_name, _normalize

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("validate_teams")

MATCH_THRESHOLD = 90.0
FUZZY_MARGIN = 5.0
_CATEGORY_RE = re.compile(
    r"\b(women|woman|ladies|feminine|femenino|feminino|frauen|academy|youth|"
    r"reserves?|reserve|primavera|next\s+gen|u[ -]?\d{2}|ii|b)\b",
    re.IGNORECASE,
)


def _atomic_write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        temp_path = Path(temp_name)
        if temp_path.exists():
            temp_path.unlink()


def get_pes_clubs(edit_file_path: str | Path) -> dict[int, str]:
    """Decrypt a save, validate it, and return club IDs/names only."""
    path = Path(edit_file_path)
    if not path.exists():
        raise FileNotFoundError(f"Edit file not found: {path}")

    temp_dir = crypto.decrypt(path)
    try:
        data_dat = temp_dir / "data.dat"
        if not data_dat.exists():
            raise RuntimeError(f"Decrypted save has no data.dat: {temp_dir}")

        edit_file = EditFile()
        edit_file.load(data_dat)
        integrity = edit_file.validate_integrity()
        if not integrity["valid"]:
            preview = "; ".join(integrity["errors"][:5])
            raise RuntimeError(f"Input save failed integrity validation: {preview}")

        teams = edit_file.get_all_team_info()
        return {
            team_id: teams[team_id].name
            for team_id in sorted(edit_file.get_club_team_ids())
            if team_id in teams and teams[team_id].name
        }
    finally:
        crypto.cleanup_temp(temp_dir)


def _category_compatible(pes_name: str, fotmob_name: str) -> bool:
    """Reject youth/women/reserve variants unless PES names the same category."""
    pes_markers = {marker.casefold() for marker in _CATEGORY_RE.findall(pes_name)}
    fotmob_markers = {marker.casefold() for marker in _CATEGORY_RE.findall(fotmob_name)}
    return fotmob_markers.issubset(pes_markers)


def _score_club_name(pes_name: str, fotmob_name: str) -> float:
    norm_pes = _normalize(pes_name)
    norm_fotmob = _normalize(fotmob_name)
    if norm_pes == norm_fotmob:
        return 100.0
    if _clean_club_name(pes_name) == _clean_club_name(fotmob_name):
        return 98.0
    return float(max(
        fuzz.token_set_ratio(norm_pes, norm_fotmob),
        fuzz.token_sort_ratio(norm_pes, norm_fotmob),
        fuzz.WRatio(norm_pes, norm_fotmob),
    ))


def _rank_club_name(pes_name: str, fotmob_name: str) -> tuple[int, float]:
    """Rank identity evidence before fuzzy similarity can reward substrings."""
    if _normalize(pes_name) == _normalize(fotmob_name):
        return 3, 100.0
    if _clean_club_name(pes_name) == _clean_club_name(fotmob_name):
        return 2, 98.0
    return 1, _score_club_name(pes_name, fotmob_name)


def build_validated_club_index(
    pes_clubs: dict[int, str],
    fotmob_teams: list[dict],
    major_clubs: dict[str, int],
) -> list[dict]:
    """Return unambiguous mappings, with curated major IDs taking precedence."""
    normalized_fotmob: list[dict] = []
    by_fotmob_id: dict[int, dict] = {}
    for item in fotmob_teams:
        if not isinstance(item, dict) or "fotmob_id" not in item:
            raise ValueError("FotMob team entry is missing fotmob_id")
        team_id = int(item["fotmob_id"])
        name = str(item.get("name") or item.get("slug") or "").strip()
        if not name or team_id in by_fotmob_id:
            raise ValueError(f"Invalid or duplicate FotMob team entry: {team_id}")
        clean = dict(item)
        clean["fotmob_id"] = team_id
        clean["name"] = name
        normalized_fotmob.append(clean)
        by_fotmob_id[team_id] = clean

    matcher = NameMatcher()
    matcher.load_team_db([(name, team_id) for team_id, name in pes_clubs.items()])
    mappings: dict[int, dict] = {}
    used_fotmob_ids: set[int] = set()

    for major_name, raw_fotmob_id in major_clubs.items():
        fotmob_id = int(raw_fotmob_id)
        source = by_fotmob_id.get(fotmob_id)
        if source is None:
            # Curated IDs are the authority and may repair an older partial
            # sitemap crawl. The next complete crawl will enrich slug/URL.
            source = {
                "fotmob_id": fotmob_id,
                "name": str(major_name),
                "slug": "",
                "url": f"https://www.fotmob.com/teams/{fotmob_id}/overview",
            }
        pes_team_id, pes_name, _ = matcher.match_team(str(major_name))
        if pes_team_id is None:
            raise ValueError(f"Curated club {major_name!r} does not resolve to one PES club")
        if pes_team_id in mappings and mappings[pes_team_id]["fotmob_id"] != fotmob_id:
            raise ValueError(f"Curated clubs conflict for PES team {pes_team_id}")
        mappings[pes_team_id] = {
            **source,
            "pes_team_id": pes_team_id,
            "pes_team_name": pes_name,
            "match_score": 100.0,
            "identity_source": "major_clubs",
        }
        used_fotmob_ids.add(fotmob_id)

    proposals: dict[int, tuple[float, dict]] = {}
    for pes_team_id, pes_name in pes_clubs.items():
        if pes_team_id in mappings:
            continue
        scored = sorted(
            (
                (*_rank_club_name(pes_name, item["name"]), item)
                for item in normalized_fotmob
                if item["fotmob_id"] not in used_fotmob_ids
                and _category_compatible(pes_name, item["name"])
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
        runner_score = same_tier_runner[1] if same_tier_runner else -1.0
        if best_score < MATCH_THRESHOLD:
            continue
        if same_tier_runner is not None and best_score - runner_score < FUZZY_MARGIN:
            tied = [
                candidate
                for candidate in scored
                if candidate[0] == best_tier
                and best_score - candidate[1] < FUZZY_MARGIN
            ]
            legacy_candidates = [
                candidate for candidate in tied if candidate[2]["fotmob_id"] < 200_000
            ]
            # FotMob's long-lived senior identities generally use legacy IDs;
            # later women/youth/duplicate sitemap identities use much larger
            # IDs. Only use this signal when it leaves exactly one candidate.
            if len(legacy_candidates) != 1:
                continue
            best_tier, best_score, best = legacy_candidates[0]
        proposals[pes_team_id] = (best_score, best)

    proposed_by_fotmob: dict[int, list[int]] = defaultdict(list)
    for pes_team_id, (_, item) in proposals.items():
        proposed_by_fotmob[item["fotmob_id"]].append(pes_team_id)

    for pes_team_id, (score, item) in proposals.items():
        if len(proposed_by_fotmob[item["fotmob_id"]]) != 1:
            continue
        mappings[pes_team_id] = {
            **item,
            "pes_team_id": pes_team_id,
            "pes_team_name": pes_clubs[pes_team_id],
            "match_score": round(score, 1),
            "identity_source": "unambiguous_name",
        }

    return sorted(mappings.values(), key=lambda item: item["fotmob_id"])


def validate() -> None:
    fotmob_path = config.DATA_DIR / "fotmob_teams.json"
    major_path = config.DATA_DIR / "major_clubs.json"
    output_path = config.DATA_DIR / "fotmob_teams_validated.json"
    pes_output_path = config.DATA_DIR / "pes_teams.json"

    fotmob_teams = json.loads(fotmob_path.read_text(encoding="utf-8"))
    major_clubs = json.loads(major_path.read_text(encoding="utf-8"))
    if not isinstance(fotmob_teams, list) or not isinstance(major_clubs, dict):
        raise ValueError("Club source files have an invalid top-level JSON type")

    pes_clubs = get_pes_clubs(config.EDIT_FILE_PATH)
    validated = build_validated_club_index(pes_clubs, fotmob_teams, major_clubs)
    if not validated:
        raise RuntimeError("Validation produced an empty FotMob/PES club index")

    _atomic_write_json(
        pes_output_path,
        [{"pes_team_id": team_id, "name": name} for team_id, name in pes_clubs.items()],
    )
    _atomic_write_json(output_path, validated)
    logger.info(
        "Validated %s/%s PES clubs; wrote %s",
        len(validated),
        len(pes_clubs),
        output_path,
    )


if __name__ == "__main__":
    validate()
