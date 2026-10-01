"""Evidence-gated, project-owned game-plan settings from FotMob league data."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import logging
import math
import os
from pathlib import Path
import tempfile
from urllib.parse import urlparse

import aiohttp

import config
from scraper.fotmob import DEFAULT_HEADERS
from scraper.models import SquadSnapshot, TacticalUpdate

logger = logging.getLogger(__name__)

FOTMOB_LEAGUE_URL = "https://www.fotmob.com/api/data/leagues"
_REQUEST_CONCURRENCY = 4
_REQUEST_ATTEMPTS = 3
_RETRYABLE_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})
_CACHE_VERSION = 1
_CACHE_TTL = timedelta(hours=20)
_CACHE_MAX_AGE = timedelta(days=60)
_MIN_MATCHES = 3
_MIN_LEAGUE_TEAMS = 8

# Current-season team aggregates supplied by FotMob's league stats endpoint.
_METRICS = {
    "possession": "possession_percentage_team",
    "passes": "accurate_pass_team",
    "long_balls": "accurate_long_balls_team",
    "crosses": "accurate_cross_team",
    "final_third_wins": "poss_won_att_3rd_team",
}


def _parse_number(value) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _parse_positive_int(value) -> int | None:
    if isinstance(value, bool) or (
        isinstance(value, float) and not value.is_integer()
    ):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _parse_metric_table(payload: dict) -> dict[int, dict[str, float | int]]:
    tables = payload.get("TopLists")
    if not isinstance(tables, list):
        return {}

    rows: dict[int, dict[str, float | int]] = {}
    for table in tables:
        if not isinstance(table, dict):
            continue
        stat_rows = table.get("StatList")
        if not isinstance(stat_rows, list):
            continue
        for row in stat_rows:
            if not isinstance(row, dict):
                continue
            team_id = _parse_positive_int(row.get("TeamId"))
            value = _parse_number(row.get("StatValue"))
            matches = _parse_positive_int(
                row.get("MatchesPlayed") or row.get("StatValueCount")
            )
            if team_id is None or value is None or matches is None:
                continue
            rows[team_id] = {"value": value, "matches": matches}
    return rows


def _load_cache(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"version": _CACHE_VERSION, "leagues": {}}
    except (OSError, TypeError, ValueError) as error:
        logger.warning("Ignoring unreadable FotMob tactics cache: %s", error)
        return {"version": _CACHE_VERSION, "leagues": {}}

    if (
        not isinstance(payload, dict)
        or payload.get("version") != _CACHE_VERSION
        or not isinstance(payload.get("leagues"), dict)
    ):
        logger.warning("Ignoring incompatible FotMob tactics cache")
        return {"version": _CACHE_VERSION, "leagues": {}}
    return payload


def _save_cache(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = handle.name
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    except (OSError, TypeError, ValueError) as error:
        logger.warning("Could not save FotMob tactics cache: %s", error)
        if temporary is not None:
            try:
                Path(temporary).unlink()
            except OSError:
                pass


def _cached_entry_is_fresh(entry: object, now: datetime) -> bool:
    if not isinstance(entry, dict):
        return False
    try:
        fetched_at = datetime.fromisoformat(
            str(entry.get("fetched_at", "")).replace("Z", "+00:00")
        )
    except ValueError:
        return False
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=timezone.utc)
    age = now - fetched_at.astimezone(timezone.utc)
    return (
        timedelta(0) <= age < _CACHE_TTL
        and isinstance(entry.get("teams"), dict)
        and entry.get("partial") is not True
    )


def _valid_stat_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or parsed.netloc.casefold() != "data.fotmob.com"
        or not parsed.path.startswith("/stats/")
        or not parsed.path.endswith(".json")
    ):
        return None
    return value


async def _get_json(
    session: aiohttp.ClientSession,
    url: str,
    semaphore: asyncio.Semaphore,
) -> dict:
    last_error: Exception | None = None
    for attempt in range(_REQUEST_ATTEMPTS):
        try:
            async with semaphore:
                async with session.get(url) as response:
                    if response.status == 200:
                        payload = await response.json(content_type=None)
                        if isinstance(payload, dict):
                            return payload
                        raise ValueError(f"FotMob returned non-object JSON from {url}")
                    if (
                        response.status not in _RETRYABLE_STATUSES
                        or attempt + 1 >= _REQUEST_ATTEMPTS
                    ):
                        raise RuntimeError(
                            f"FotMob returned HTTP {response.status} for {url}"
                        )
                    retry_after = response.headers.get("Retry-After", "")
                    try:
                        delay = min(max(float(retry_after), 0.0), 8.0)
                    except (TypeError, ValueError):
                        delay = min(0.5 * (2**attempt), 8.0)
            await asyncio.sleep(delay)
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as error:
            last_error = error
            if attempt + 1 >= _REQUEST_ATTEMPTS:
                break
            await asyncio.sleep(min(0.5 * (2**attempt), 8.0))
    if last_error is not None:
        raise RuntimeError(f"FotMob request failed for {url}: {last_error}") from last_error
    raise RuntimeError(f"FotMob request failed for {url}")


def _league_stat_urls(payload: dict) -> tuple[str, dict[str, str]]:
    details = payload.get("details")
    season = ""
    if isinstance(details, dict):
        season = str(
            details.get("selectedSeason")
            or details.get("latestSeason")
            or ""
        )

    stats = payload.get("stats")
    team_stats = stats.get("teams") if isinstance(stats, dict) else None
    if not isinstance(team_stats, list):
        return season, {}

    urls: dict[str, str] = {}
    for item in team_stats:
        if not isinstance(item, dict):
            continue
        participant = item.get("participant")
        stat = participant.get("stat") if isinstance(participant, dict) else None
        name = stat.get("name") if isinstance(stat, dict) else item.get("name")
        key = next(
            (
                metric
                for metric, stat_name in _METRICS.items()
                if name == stat_name
            ),
            None,
        )
        if key is None:
            continue
        url = _valid_stat_url(item.get("fetchAllUrl"))
        if url is not None:
            urls[key] = url

    return season, urls


async def _fetch_league_entry(
    session: aiohttp.ClientSession,
    league_id: int,
    semaphore: asyncio.Semaphore,
    now: datetime,
) -> dict:
    league_url = f"{FOTMOB_LEAGUE_URL}?id={league_id}&ccode3=GBR"
    league_payload = await _get_json(session, league_url, semaphore)
    season, stat_urls = _league_stat_urls(league_payload)
    if not stat_urls:
        raise RuntimeError(f"FotMob league {league_id} has no team stat links")

    metric_payloads = await asyncio.gather(
        *(_get_json(session, url, semaphore) for url in stat_urls.values()),
        return_exceptions=True,
    )
    team_metrics: dict[int, dict[str, dict[str, float | int]]] = {}
    failed_metrics: list[str] = []
    for metric, payload in zip(stat_urls, metric_payloads, strict=True):
        if isinstance(payload, Exception):
            failed_metrics.append(metric)
            logger.warning(
                "FotMob team stat %s failed for league %s: %s",
                metric,
                league_id,
                payload,
            )
            continue
        for team_id, value in _parse_metric_table(payload).items():
            team_metrics.setdefault(team_id, {})[metric] = value

    if not team_metrics:
        raise RuntimeError(f"FotMob league {league_id} returned no team statistics")

    return {
        "season": season,
        "fetched_at": now.isoformat(),
        "teams": {
            str(team_id): metrics
            for team_id, metrics in team_metrics.items()
        },
        "source_urls": [league_url, *stat_urls.values()],
        "partial": bool(failed_metrics),
    }


def _entry_metric(
    team: dict,
    metric: str,
    *,
    minimum_matches: int = _MIN_MATCHES,
) -> tuple[float, int] | None:
    item = team.get(metric)
    if not isinstance(item, dict):
        return None
    value = _parse_number(item.get("value"))
    matches = _parse_positive_int(item.get("matches"))
    if (
        value is None
        or value < 0.0
        or matches is None
        or matches < minimum_matches
    ):
        return None
    return value, matches


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    position = fraction * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _quartile_decisions(
    values_by_team: dict[int, float],
    *,
    low_value: int,
    high_value: int,
) -> dict[int, int]:
    if len(values_by_team) < _MIN_LEAGUE_TEAMS:
        return {}
    values = list(values_by_team.values())
    low = _percentile(values, 0.25)
    high = _percentile(values, 0.75)
    if low >= high:
        return {}
    return {
        team_id: high_value if value >= high else low_value
        for team_id, value in values_by_team.items()
        if value >= high or value <= low
    }


def _league_slider_decisions(values_by_team: dict[int, float]) -> dict[int, int]:
    """Scale a league-relative profile to the game's 1-10 slider range."""
    if len(values_by_team) < _MIN_LEAGUE_TEAMS:
        return {}
    low = min(values_by_team.values())
    high = max(values_by_team.values())
    if low >= high:
        return {}
    spread = high - low
    return {
        team_id: 1 + int((value - low) / spread * 9 + 0.5)
        for team_id, value in values_by_team.items()
    }


def _league_decisions(entry: dict) -> dict[int, tuple[dict[str, int], int]]:
    teams = entry.get("teams")
    if not isinstance(teams, dict):
        return {}
    normalized: dict[int, dict] = {}
    for raw_team_id, profile in teams.items():
        team_id = _parse_positive_int(raw_team_id)
        if team_id is not None and isinstance(profile, dict):
            normalized[team_id] = profile

    possessions: dict[int, float] = {}
    long_ball_shares: dict[int, float] = {}
    cross_shares: dict[int, float] = {}
    final_third_wins: dict[int, float] = {}
    sample_counts: dict[str, dict[int, int]] = {
        setting: {}
        for setting in (
            "attacking_style",
            "build_up",
            "attacking_area",
            "defensive_style",
            "containment_area",
            "pressuring",
            "defensive_line",
            "compactness",
        )
    }
    for team_id, profile in normalized.items():
        possession = _entry_metric(profile, "possession")
        if possession is not None and 0.0 <= possession[0] <= 100.0:
            possessions[team_id] = possession[0]
            sample_counts["attacking_style"][team_id] = possession[1]

        passes = _entry_metric(profile, "passes")
        long_balls = _entry_metric(profile, "long_balls")
        if (
            passes is not None
            and long_balls is not None
            and passes[1] == long_balls[1]
            and passes[0] > 0.0
        ):
            long_ball_shares[team_id] = long_balls[0] / passes[0]
            sample_counts["build_up"][team_id] = passes[1]

        crosses = _entry_metric(profile, "crosses")
        if (
            passes is not None
            and crosses is not None
            and passes[1] == crosses[1]
            and passes[0] > 0.0
        ):
            cross_shares[team_id] = crosses[0] / passes[0]
            sample_counts["attacking_area"][team_id] = passes[1]
            sample_counts["containment_area"][team_id] = passes[1]

        final_third = _entry_metric(profile, "final_third_wins")
        if final_third is not None:
            final_third_wins[team_id] = final_third[0]
            for setting in (
                "defensive_style",
                "pressuring",
                "defensive_line",
                "compactness",
            ):
                sample_counts[setting][team_id] = final_third[1]

    decisions_by_setting = {
        "attacking_style": _quartile_decisions(
            possessions,
            low_value=0,  # counter attack
            high_value=1,  # possession game
        ),
        "build_up": _quartile_decisions(
            long_ball_shares,
            low_value=1,  # short pass
            high_value=0,  # long pass
        ),
        "attacking_area": _quartile_decisions(
            cross_shares,
            low_value=1,  # centre
            high_value=0,  # wide
        ),
        "defensive_style": _quartile_decisions(
            final_third_wins,
            low_value=1,  # all-out defense
            high_value=0,  # frontline pressure
        ),
        "containment_area": _quartile_decisions(
            cross_shares,
            low_value=0,  # middle
            high_value=1,  # wide
        ),
        "pressuring": _quartile_decisions(
            final_third_wins,
            low_value=1,  # conservative
            high_value=0,  # aggressive
        ),
        "defensive_line": _league_slider_decisions(final_third_wins),
        "compactness": _league_slider_decisions(final_third_wins),
    }

    result: dict[int, tuple[dict[str, int], int]] = {}
    for team_id in normalized:
        settings = {
            setting: decisions[team_id]
            for setting, decisions in decisions_by_setting.items()
            if team_id in decisions
        }
        if not settings:
            continue
        matches = [
            sample_counts[setting][team_id]
            for setting in settings
            if team_id in sample_counts[setting]
        ]
        result[team_id] = (settings, min(matches) if matches else 0)
    return result


async def _fetch_tactical_updates(
    snapshots: tuple[SquadSnapshot, ...] | list[SquadSnapshot],
    *,
    cache_path: Path,
    now: datetime,
) -> tuple[TacticalUpdate, ...]:
    targets: dict[int, tuple[str, int]] = {}
    for snapshot in snapshots:
        league_id = _parse_positive_int(snapshot.primary_league_id)
        team_id = _parse_positive_int(snapshot.team_id_fotmob)
        if league_id is None or team_id is None:
            continue
        targets[team_id] = (snapshot.club_name.strip(), league_id)
    if not targets:
        return ()

    cache = _load_cache(cache_path)
    league_cache = cache["leagues"]
    requested_leagues = sorted({league_id for _, league_id in targets.values()})
    fetched_entries: dict[int, dict] = {}
    stale_leagues: list[int] = []
    for league_id in requested_leagues:
        entry = league_cache.get(str(league_id))
        if _cached_entry_is_fresh(entry, now):
            fetched_entries[league_id] = entry
        else:
            stale_leagues.append(league_id)

    timeout = aiohttp.ClientTimeout(total=25)
    semaphore = asyncio.Semaphore(_REQUEST_CONCURRENCY)
    async with aiohttp.ClientSession(headers=DEFAULT_HEADERS, timeout=timeout) as session:
        results = await asyncio.gather(
            *(
                _fetch_league_entry(session, league_id, semaphore, now)
                for league_id in stale_leagues
            ),
            return_exceptions=True,
        )

    for league_id, entry in zip(stale_leagues, results, strict=True):
        if isinstance(entry, Exception):
            logger.warning(
                "FotMob tactical stats skipped for league %s: %s",
                league_id,
                entry,
            )
            continue
        fetched_entries[league_id] = entry
        league_cache[str(league_id)] = entry

    cutoff = now - _CACHE_MAX_AGE
    cache["leagues"] = {
        key: entry
        for key, entry in league_cache.items()
        if _cached_entry_is_fresh(entry, now)
        or _entry_is_within_age(entry, cutoff)
    }
    _save_cache(cache_path, cache)

    decisions_by_league = {
        league_id: _league_decisions(entry)
        for league_id, entry in fetched_entries.items()
    }
    updates: list[TacticalUpdate] = []
    for team_id, (club_name, league_id) in targets.items():
        entry = fetched_entries.get(league_id)
        if not isinstance(entry, dict):
            continue
        decision = decisions_by_league.get(league_id, {}).get(team_id)
        if decision is None:
            continue
        settings, matches = decision
        updates.append(
            TacticalUpdate(
                club_name=club_name,
                team_id_fotmob=team_id,
                league_id=league_id,
                settings=tuple(sorted(settings.items())),
                sample_matches=matches,
                source_urls=tuple(str(url) for url in entry.get("source_urls", ())),
            )
        )
    return tuple(updates)


def _entry_is_within_age(entry: object, cutoff: datetime) -> bool:
    if not isinstance(entry, dict):
        return False
    try:
        fetched_at = datetime.fromisoformat(
            str(entry.get("fetched_at", "")).replace("Z", "+00:00")
        )
    except ValueError:
        return False
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=timezone.utc)
    return fetched_at.astimezone(timezone.utc) >= cutoff


def fetch_fotmob_tactical_updates(
    snapshots: tuple[SquadSnapshot, ...] | list[SquadSnapshot],
    *,
    cache_path: Path | str | None = None,
    now: datetime | None = None,
) -> tuple[TacticalUpdate, ...]:
    """Select only strongly separated, current-season tactical signals.

    The supported choices are relative to the team's league distribution. Mid-
    table values and unavailable data produce no decision, preserving the save.
    """
    selected_cache = (
        Path(cache_path)
        if cache_path is not None
        else config.FOTMOB_TACTICS_CACHE_FILE
    )
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    return asyncio.run(
        _fetch_tactical_updates(
            snapshots,
            cache_path=selected_cache,
            now=current_time.astimezone(timezone.utc),
        )
    )
