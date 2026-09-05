"""Best-effort Soccerway corroboration from relevant team transfer feeds."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
import logging
import re
import unicodedata
from typing import Any

import aiohttp

from scraper.models import Transfer
from scraper.source_utils import date_in_range, parse_external_date, resolve_source_date_range


logger = logging.getLogger(__name__)

SOCCERWAY_SEARCH_URL = "https://s.livesport.services/api/v2/search/"
SOCCERWAY_FEED_URL = (
    "https://global.flashscore.ninja/2020/x/feed/tetr_{team_id}_1_{page}"
)
SOCCERWAY_TEAM_TRANSFERS_URL = (
    "https://www.soccerway.com/team/{slug}/{team_id}/transfers/"
)
SOCCERWAY_FEED_SIGNATURE = "SW9D1eZo"
# Keep optional corroboration bounded; callers can pass a larger value for history.
SOCCERWAY_DEFAULT_MAX_PAGES = 3
SOCCERWAY_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.soccerway.com/",
}
_NON_SENIOR_RE = re.compile(
    r"\b(?:women|woman|ladies|feminine|femenino|feminino|frauen|academy|"
    r"youth|reserves?|primavera|u[ -]?\d{2}|ii|b)\b",
    re.IGNORECASE,
)
_CLUB_AFFIX_RE = re.compile(
    r"\b(?:fc|cf|sc|ac|cd|ud|fk|sk|as|us|ss|sv|vfl|vfb|afc|club|calcio|sad|kv)\b",
    re.IGNORECASE,
)
_NON_CLUB_NAMES = {
    "",
    "career break",
    "free agent",
    "retired",
    "unattached",
    "without club",
}


@dataclass(frozen=True)
class _SoccerwayTeam:
    name: str
    slug: str
    team_id: str


@dataclass
class _SoccerwayFeedTeam:
    name: str = ""
    url: str = ""


@dataclass
class _SoccerwayFeedRow:
    date_value: str = ""
    direction: str = ""
    transfer_type: str = ""
    fee: str = ""
    player_name: str = ""
    teams: list[_SoccerwayFeedTeam] = field(default_factory=list)


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value or "")
    plain = "".join(
        character
        for character in decomposed
        if not unicodedata.combining(character)
    )
    return " ".join(re.sub(r"[^a-z0-9]+", " ", plain.casefold()).split())


def _club_key(value: str) -> str:
    return " ".join(_CLUB_AFFIX_RE.sub(" ", _normalize(value)).split())


def _is_non_club(value: str) -> bool:
    return _normalize(value) in _NON_CLUB_NAMES


def _transfer_type(label: str, fee: str = "") -> tuple[str, bool]:
    normalized = _clean(f"{label} {fee}").casefold()
    if "end of loan" in normalized or "return" in normalized:
        return "end of loan", False
    if "loan" in normalized:
        return "loan", True
    if "free" in normalized or "released" in normalized:
        return "free transfer", False
    return "transfer", False


def _participant_id(url: str) -> str:
    match = re.search(r"/team/[^/]+/([^/]+)/", url or "")
    return match.group(1) if match else ""


def _parse_feed_rows(payload: str) -> list[_SoccerwayFeedRow]:
    """Decode Soccerway's public team-transfer feed records."""
    rows: list[_SoccerwayFeedRow] = []
    row: _SoccerwayFeedRow | None = None
    team: _SoccerwayFeedTeam | None = None
    section = ""
    pending_property = ""

    def finish_row() -> None:
        nonlocal row, team, section, pending_property
        if row is not None:
            rows.append(row)
        row = None
        team = None
        section = ""
        pending_property = ""

    for raw_token in (payload or "").split("¬"):
        token = raw_token.lstrip("~")
        if "÷" not in token:
            continue
        code, value = token.split("÷", 1)

        if code == "TS":
            pending_property = ""
            if value == "RTT":
                finish_row()
                row = _SoccerwayFeedRow()
            elif row is not None and value == "TEA":
                section = "team"
                team = _SoccerwayFeedTeam()
                row.teams.append(team)
            elif row is not None and value == "PLA":
                section = "player"
                team = None
            continue

        if code == "TE":
            pending_property = ""
            if value == "RTT":
                finish_row()
            elif value in {"TEA", "PLA"}:
                section = ""
                team = None
            continue

        if row is None:
            continue
        if code == "PT":
            pending_property = value
            continue
        if code != "PV" or not pending_property:
            continue

        if section == "team" and team is not None:
            if pending_property == "VA":
                team.name = _clean(value)
            elif pending_property == "TURL":
                team.url = _clean(value)
        elif section == "player":
            if pending_property == "VA":
                row.player_name = _clean(value)
        elif pending_property == "DATE":
            row.date_value = _clean(value)
        elif pending_property == "TD":
            row.direction = _clean(value).casefold()
        elif pending_property == "TT":
            row.transfer_type = _clean(value)
        elif pending_property == "TJ":
            row.fee = _clean(value)
        pending_property = ""

    finish_row()
    return rows


def _row_date(row: _SoccerwayFeedRow) -> date | None:
    if row.date_value.isdigit():
        # Soccerway encodes a date-only value as local midnight. Moving it to
        # noon before UTC conversion keeps positive-offset dates on that day.
        return parse_external_date(int(row.date_value) + 12 * 60 * 60)
    return parse_external_date(row.date_value)


def _row_route(
    row: _SoccerwayFeedRow,
    current_team: _SoccerwayTeam,
    transfer_type: str,
) -> tuple[str, str] | None:
    if row.direction not in {"in", "out"}:
        return None

    other_team = next(
        (
            team.name
            for team in row.teams
            if team.name and _participant_id(team.url) != current_team.team_id
        ),
        "",
    )
    if not other_team:
        other_team = next(
            (
                team.name
                for team in row.teams
                if team.name
                and _club_key(team.name) != _club_key(current_team.name)
            ),
            "",
        )
    if not other_team and transfer_type == "free transfer":
        other_team = "Free Agent"
    if not other_team:
        return None

    if row.direction == "in":
        return other_team, current_team.name
    return current_team.name, other_team


def _transfers_from_rows(
    rows: Iterable[_SoccerwayFeedRow],
    current_team: _SoccerwayTeam,
    source_url: str,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[Transfer]:
    transfers: list[Transfer] = []
    seen: set[tuple[str, str, str, str]] = set()
    for row in rows:
        event_date = _row_date(row)
        if not date_in_range(event_date, start_date, end_date):
            continue
        player_name = _clean(row.player_name)
        if not player_name:
            continue
        transfer_type, is_loan = _transfer_type(row.transfer_type, row.fee)
        route = _row_route(row, current_team, transfer_type)
        if route is None:
            continue
        from_club, to_club = route
        if _club_key(from_club) == _club_key(to_club):
            continue
        key = (
            player_name.casefold(),
            from_club.casefold(),
            to_club.casefold(),
            event_date.isoformat(),
        )
        if key in seen:
            continue
        seen.add(key)
        transfers.append(
            Transfer(
                player_name=player_name,
                from_club=from_club,
                to_club=to_club,
                date=event_date.isoformat(),
                transfer_type=transfer_type,
                fee=_clean(row.fee),
                is_loan=is_loan,
                sources=("soccerway",),
                source_urls=(source_url,),
                verification_status="corroborator",
            )
        )
    return transfers


def parse_soccerway_transfer_feed(
    payload: str,
    team_name: str,
    team_id: str,
    source_url: str,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[Transfer]:
    """Parse one current Soccerway team feed as corroboration-only routes."""
    current_team = _SoccerwayTeam(
        name=_clean(team_name),
        slug="",
        team_id=_clean(team_id),
    )
    return _transfers_from_rows(
        _parse_feed_rows(payload),
        current_team,
        source_url,
        start_date=start_date,
        end_date=end_date,
    )


def _candidate_team(payload: Any, requested_name: str) -> _SoccerwayTeam | None:
    if not isinstance(payload, list):
        return None
    requested_has_category = bool(_NON_SENIOR_RE.search(requested_name))
    candidates: list[_SoccerwayTeam] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        sport = item.get("sport")
        gender = item.get("gender")
        if (
            not isinstance(item_type, dict)
            or item_type.get("id") != 2
            or not isinstance(sport, dict)
            or sport.get("id") != 1
            or (isinstance(gender, dict) and gender.get("id") not in {None, 1})
        ):
            continue
        name = _clean(item.get("name"))
        team_id = _clean(item.get("id"))
        slug = _clean(item.get("url"))
        if (
            not name
            or not team_id
            or (not requested_has_category and _NON_SENIOR_RE.search(name))
        ):
            continue
        candidates.append(_SoccerwayTeam(name=requested_name, slug=slug, team_id=team_id))
        if _normalize(name) == _normalize(requested_name):
            return candidates[-1]
        if _club_key(name) == _club_key(requested_name):
            return candidates[-1]
    return candidates[0] if candidates else None


async def _fetch_json(
    session: aiohttp.ClientSession,
    url: str,
    *,
    params: dict[str, Any] | None = None,
) -> Any:
    async with session.get(url, params=params) as response:
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status}")
        return await response.json(content_type=None)


async def _fetch_text(
    session: aiohttp.ClientSession,
    url: str,
    *,
    headers: dict[str, str] | None = None,
) -> str:
    async with session.get(url, headers=headers) as response:
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status}")
        return await response.text()


async def _resolve_team(
    session: aiohttp.ClientSession,
    team_name: str,
) -> _SoccerwayTeam | None:
    payload = await _fetch_json(
        session,
        SOCCERWAY_SEARCH_URL,
        params={
            "q": team_name,
            "lang-id": 1,
            "type-ids": 2,
            "project-id": 2020,
            "project-type-id": 1,
            "sport-ids": 1,
        },
    )
    return _candidate_team(payload, team_name)


async def _fetch_team_transfers(
    session: aiohttp.ClientSession,
    team: _SoccerwayTeam,
    *,
    start_date: date | None,
    end_date: date | None,
    max_pages: int,
) -> list[Transfer]:
    source_url = SOCCERWAY_TEAM_TRANSFERS_URL.format(
        slug=team.slug,
        team_id=team.team_id,
    )
    feed_headers = {"X-Fsign": SOCCERWAY_FEED_SIGNATURE}
    transfers: list[Transfer] = []
    for page in range(1, max_pages + 1):
        payload = await _fetch_text(
            session,
            SOCCERWAY_FEED_URL.format(team_id=team.team_id, page=page),
            headers=feed_headers,
        )
        rows = _parse_feed_rows(payload)
        if not rows:
            break
        transfers.extend(
            _transfers_from_rows(
                rows,
                team,
                source_url,
                start_date=start_date,
                end_date=end_date,
            )
        )
        row_dates = [event_date for row in rows if (event_date := _row_date(row))]
        if start_date is not None and row_dates and min(row_dates) < start_date:
            break
    return transfers


def _club_names(club_names: Iterable[str] | None) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for value in club_names or ():
        name = _clean(value)
        key = _normalize(name)
        if name and key not in seen and not _is_non_club(name):
            seen.add(key)
            unique.append(name)
    return unique


async def _fetch_soccerway_transfers_async(
    since_date: str | date | None = None,
    *,
    window: str = "auto",
    ref_date: date | None = None,
    club_names: Iterable[str] | None = None,
    max_pages: int = SOCCERWAY_DEFAULT_MAX_PAGES,
) -> list[Transfer]:
    start_date, end_date = resolve_source_date_range(
        since_date, window, ref_date=ref_date
    )
    targets = _club_names(club_names)
    logger.info(
        "Soccerway scraping %s relevant clubs (up to %s pages each)",
        len(targets),
        max_pages,
    )
    if not targets:
        logger.debug(
            "Soccerway supplemental source skipped: no relevant transfer clubs"
        )
        return []

    timeout = aiohttp.ClientTimeout(total=45)
    gate = asyncio.Semaphore(6)
    async with aiohttp.ClientSession(
        headers=SOCCERWAY_HEADERS,
        timeout=timeout,
        cookie_jar=aiohttp.DummyCookieJar(),
    ) as session:
        async def fetch_club(team_name: str) -> list[Transfer]:
            try:
                async with gate:
                    team = await _resolve_team(session, team_name)
                if team is None:
                    logger.debug("Soccerway could not resolve team %r", team_name)
                    return []
                async with gate:
                    return await _fetch_team_transfers(
                        session,
                        team,
                        start_date=start_date,
                        end_date=end_date,
                        max_pages=max_pages,
                    )
            except Exception as exc:
                logger.debug(
                    "Soccerway team unavailable (%s): %s",
                    team_name,
                    exc,
                )
                return []

        batches = await asyncio.gather(*(fetch_club(name) for name in targets))

    unique: list[Transfer] = []
    seen: set[tuple[str, str, str, str]] = set()
    for transfer in (item for batch in batches for item in batch):
        key = (
            transfer.player_name.casefold(),
            transfer.from_club.casefold(),
            transfer.to_club.casefold(),
            transfer.date,
        )
        if key not in seen:
            seen.add(key)
            unique.append(transfer)
    logger.info(
        "Soccerway found %s dated corroboration routes across %s relevant clubs",
        len(unique),
        len(targets),
    )
    return unique


def fetch_soccerway_transfers(
    since_date: str | date | None = None,
    *,
    window: str = "auto",
    club_names: Iterable[str] | None = None,
    max_pages: int = SOCCERWAY_DEFAULT_MAX_PAGES,
) -> list[Transfer]:
    """Fetch Soccerway routes for clubs already present in verified events."""
    try:
        return asyncio.run(
            _fetch_soccerway_transfers_async(
                since_date=since_date,
                window=window,
                club_names=club_names,
                max_pages=max_pages,
            )
        )
    except Exception as exc:
        logger.warning("Soccerway supplemental source unavailable: %s", exc)
        return []
