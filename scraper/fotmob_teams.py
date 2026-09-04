#!/usr/bin/env python3
"""Fail-closed FotMob team sitemap crawler."""

import json
import logging
import os
import re
import tempfile
import time
from pathlib import Path
from urllib.parse import unquote
import urllib.request

import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("fotmob_crawler")

SITEMAP_INDEX_URL = "https://www.fotmob.com/sitemap/en/teams.xml"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}


def clean_name(slug: str) -> str:
    return unquote(slug).replace("-", " ").title()


def _fetch_text(url: str) -> str:
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=15) as response:
        return response.read().decode("utf-8")


def _atomic_write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
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


def crawl_sitemaps() -> list[dict]:
    """Fetch every child sitemap or raise without replacing existing indexes."""
    logger.info("Fetching sitemap index: %s", SITEMAP_INDEX_URL)
    index_text = _fetch_text(SITEMAP_INDEX_URL)
    child_sitemaps = re.findall(
        r"<loc>(https://www\.fotmob\.com/sitemap/en/teams/\d+\.xml)</loc>",
        index_text,
    )
    if not child_sitemaps:
        raise RuntimeError("FotMob sitemap index contained no child sitemaps")

    teams: list[dict] = []
    seen_ids: set[int] = set()
    failures: list[str] = []
    for index, sitemap_url in enumerate(child_sitemaps, 1):
        logger.info("Fetching sitemap %s/%s", index, len(child_sitemaps))
        try:
            sitemap_text = _fetch_text(sitemap_url)
        except Exception as exc:
            failures.append(f"{sitemap_url}: {exc}")
            continue
        matches = list(re.finditer(
            r"<loc>https://www\.fotmob\.com/teams/(\d+)/overview/([^<]+)</loc>",
            sitemap_text,
        ))
        if not matches:
            failures.append(f"{sitemap_url}: no team URLs")
            continue
        for match in matches:
            team_id = int(match.group(1))
            slug = match.group(2)
            if team_id in seen_ids:
                continue
            seen_ids.add(team_id)
            teams.append({
                "fotmob_id": team_id,
                "name": clean_name(slug),
                "slug": slug,
                "url": f"https://www.fotmob.com/teams/{team_id}/overview/{slug}",
            })
        time.sleep(0.5)

    if failures:
        preview = "; ".join(failures[:5])
        raise RuntimeError(
            f"FotMob sitemap crawl incomplete ({len(failures)} failed): {preview}"
        )
    if not teams:
        raise RuntimeError("FotMob sitemap crawl produced no teams")
    json_path = config.DATA_DIR / "fotmob_teams.json"
    teams.sort(key=lambda item: item["fotmob_id"])
    _atomic_write_json(json_path, teams)
    logger.info("Saved %s complete FotMob team identities", len(teams))
    return teams


if __name__ == "__main__":
    crawl_sitemaps()
