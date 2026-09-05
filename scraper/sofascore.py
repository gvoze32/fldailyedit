"""Best-effort Sofascore corroboration from relevant team transfer APIs."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
import logging
import re
import unicodedata
from typing import Any

from curl_cffi import requests

from scraper.models import Transfer
from scraper.source_utils import date_in_range, parse_external_date, resolve_source_date_range


logger = logging.getLogger(__name__)

SOFASCORE_API_BASES = (
    ("https://api.sofascore.com/api/v1", "https://dns.google/dns-query"),
    ("https://api.sofascore.app/api/v1", None),
)
SOFASCORE_SEARCH_PATH = "/search/all"
SOFASCORE_TEAM_TRANSFERS_PATH = "/team/{team_id}/transfers"
SOFASCORE_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.sofascore.com/",
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

_TYPE_BY_NUMBER = {
    1: ("loan", True),
    2: ("end of loan", False),
    3: ("transfer", False),
    4: ("free transfer", False),
    5: ("transfer", False),
    6: ("free transfer", False),
    7: ("transfer", False),
    8: ("free transfer", False),
    9: ("transfer", False),
    10: ("transfer", False),
    11: ("free transfer", False),
    12: ("free transfer", False),
    13: ("free transfer", False),
    14: ("free transfer", False),
}


@dataclass(frozen=True, slots=True)
class _SofascoreTeam:
    name: str
    slug: str
    team_id: int


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split()).strip()


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


def _name(value: Any) -> str:
    if isinstance(value, dict):
        return _clean(value.get("name") or value.get("shortName"))
    return _clean(value)


def _transfer_type(value: Any) -> tuple[str, bool] | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return _TYPE_BY_NUMBER.get(int(value))
    normalized = re.sub(r"[^a-z]", "", _clean(value).casefold())
    if "endofloan" in normalized or "returnfromloan" in normalized:
        return "end of loan", False
    if normalized == "loan" or "loan" in normalized:
        return "loan", True
    if any(
        token in normalized
        for token in ("released", "waived", "retired", "contractexpired")
    ):
        return "free transfer", False
    if "transfer" in normalized or "traded" in normalized or "draft" in normalized:
        return "transfer", False
    if "signed" in normalized or "claimed" in normalized:
        return "transfer", False
    return None


def _transfer_items(payload: Any) -> list[dict[str, Any]]:
    if payload is None:
        return []

    result: list[dict[str, Any]] = []
    visited_containers: set[int] = set()
    visited_items: set[int] = set()

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for child in value.values():
                if isinstance(child, (dict, list)):
                    walk(child)
        elif isinstance(value, list):
            if id(value) in visited_containers:
                return
            visited_containers.add(id(value))
            if value and all(isinstance(item, dict) for item in value):
                for item in value:
                    if (
                        ("player" in item or "playerName" in item)
                        and ("transferFrom" in item or "fromTeamName" in item)
                        and id(item) not in visited_items
                    ):
                        visited_items.add(id(item))
                        result.append(item)
            for child in value:
                if isinstance(child, (dict, list)):
                    walk(child)

    walk(payload)
    return result


def _item_player(item: dict[str, Any]) -> str:
    return _name(item.get("player") or item.get("playerName"))


def _item_team(item: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = _name(item.get(key))
        if value:
            return value
    return ""


def _item_date(item: dict[str, Any]) -> date | None:
    for key in ("transferDateTimestamp", "transferDate", "date"):
        parsed = parse_external_date(item.get(key))
        if parsed is not None:
            return parsed
    return None


def _item_fee(item: dict[str, Any]) -> str:
    for key in ("transferFeeDescription", "transferFeeRaw", "transferFee"):
        value = item.get(key)
        if isinstance(value, dict):
            value = value.get("description") or value.get("value")
        clean = _clean(value)
        if clean:
            return clean
    return ""


def parse_sofascore_transfer_payload(
    payload: Any,
    source_url: str = "",
    *,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[Transfer]:
    """Parse Sofascore transfer objects as corroboration-only dated routes."""
    transfers: list[Transfer] = []
    seen: set[tuple[Any, ...]] = set()
    for item in _transfer_items(payload):
        player_name = _item_player(item)
        event_date = _item_date(item)
        if not player_name or not date_in_range(event_date, start_date, end_date):
            continue

        typed = _transfer_type(item.get("type") or item.get("transferType"))
        if typed is None:
            continue
        transfer_type, is_loan = typed
        from_club = _item_team(item, "fromTeamName", "transferFrom", "fromTeam")
        to_club = _item_team(item, "toTeamName", "transferTo", "toTeam")
        if not from_club and to_club:
            from_club = "Free Agent"
        if not to_club and from_club:
            to_club = "Free Agent"
        if not from_club or not to_club:
            continue

        transfer_id = item.get("id") or item.get("transferId")
        key = (
            transfer_id if transfer_id is not None else player_name.casefold(),
            from_club.casefold(),
            to_club.casefold(),
            event_date.isoformat(),
        )
        if key in seen:
            continue
        seen.add(key)
        player = item.get("player")
        position = _clean(player.get("position")) if isinstance(player, dict) else ""
        transfers.append(
            Transfer(
                player_name=player_name,
                from_club=from_club,
                to_club=to_club,
                date=event_date.isoformat(),
                transfer_type=transfer_type,
                fee=_item_fee(item),
                position=position,
                is_loan=is_loan,
                sources=("sofascore",),
                source_urls=(source_url,) if source_url else (),
                verification_status="corroborator",
            )
        )
    return transfers


parse_sofascore_transfers = parse_sofascore_transfer_payload


def _candidate_team(payload: Any, requested_name: str) -> _SofascoreTeam | None:
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        return None
    requested_has_category = bool(_NON_SENIOR_RE.search(requested_name))
    candidates: list[_SofascoreTeam] = []
    for item in payload["results"]:
        if not isinstance(item, dict) or item.get("type") != "team":
            continue
        entity = item.get("entity")
        if not isinstance(entity, dict):
            continue
        sport = entity.get("sport")
        name = _clean(entity.get("name"))
        slug = _clean(entity.get("slug"))
        team_id = entity.get("id")
        if (
            not isinstance(sport, dict)
            or sport.get("id") != 1
            or entity.get("gender") not in {None, "M"}
            or not isinstance(team_id, int)
            or not name
            or (not requested_has_category and _NON_SENIOR_RE.search(name))
        ):
            continue
        team = _SofascoreTeam(name=name, slug=slug, team_id=team_id)
        candidates.append(team)
        if _normalize(name) == _normalize(requested_name):
            return team
        if _club_key(name) == _club_key(requested_name):
            return team
    return candidates[0] if candidates else None


async def _fetch_json(
    session: requests.AsyncSession,
    url: str,
    *,
    params: dict[str, str] | None = None,
    doh_url: str | None = None,
) -> Any:
    options: dict[str, Any] = {}
    if doh_url is not None:
        options["doh_url"] = doh_url
    response = await session.get(url, params=params, **options)
    if response.status_code != 200:
        raise RuntimeError(f"HTTP {response.status_code}")
    return response.json()


async def _api_json(
    session: requests.AsyncSession,
    path: str,
    *,
    params: dict[str, str] | None = None,
) -> Any:
    last_error: Exception | None = None
    for base_url, doh_url in SOFASCORE_API_BASES:
        try:
            return await _fetch_json(
                session,
                f"{base_url}{path}",
                params=params,
                doh_url=doh_url,
            )
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"Sofascore API request failed: {path}") from last_error


async def _resolve_team(
    session: requests.AsyncSession,
    team_name: str,
) -> _SofascoreTeam | None:
    payload = await _api_json(
        session,
        SOFASCORE_SEARCH_PATH,
        params={"q": team_name},
    )
    return _candidate_team(payload, team_name)


async def _fetch_team_transfers(
    session: requests.AsyncSession,
    team: _SofascoreTeam,
    *,
    start_date: date | None,
    end_date: date | None,
) -> list[Transfer]:
    path = SOFASCORE_TEAM_TRANSFERS_PATH.format(team_id=team.team_id)
    payload = await _api_json(session, path)
    source_url = f"{SOFASCORE_API_BASES[0][0]}{path}"
    return parse_sofascore_transfer_payload(
        payload,
        source_url,
        start_date=start_date,
        end_date=end_date,
    )


def _transfer_key(transfer: Transfer) -> tuple[str, str, str, str]:
    return (
        _normalize(transfer.player_name),
        _club_key(transfer.from_club),
        _club_key(transfer.to_club),
        transfer.date,
    )


async def _fetch_sofascore_transfers_async(
    since_date: str | date | None = None,
    *,
    window: str = "auto",
    ref_date: date | None = None,
    club_names: Iterable[str] = (),
) -> list[Transfer]:
    start_date, end_date = resolve_source_date_range(
        since_date, window, ref_date=ref_date
    )
    targets = tuple(dict.fromkeys(_clean(name) for name in club_names if _clean(name)))
    if not targets:
        logger.debug(
            "Sofascore supplemental source skipped: no relevant transfer clubs"
        )
        return []

    gate = asyncio.Semaphore(6)
    async with requests.AsyncSession(
        headers=SOFASCORE_HEADERS,
        impersonate="chrome",
        timeout=30,
        max_clients=6,
    ) as session:
        async def fetch_club(team_name: str) -> list[Transfer]:
            async with gate:
                try:
                    team = await _resolve_team(session, team_name)
                    if team is None:
                        return []
                    return await _fetch_team_transfers(
                        session,
                        team,
                        start_date=start_date,
                        end_date=end_date,
                    )
                except Exception as exc:
                    logger.debug(
                        "Sofascore skipped %s: %s",
                        team_name,
                        exc,
                    )
                    return []

        batches = await asyncio.gather(*(fetch_club(name) for name in targets))

    transfers: list[Transfer] = []
    seen: set[tuple[str, str, str, str]] = set()
    for batch in batches:
        for transfer in batch:
            key = _transfer_key(transfer)
            if key in seen:
                continue
            seen.add(key)
            transfers.append(transfer)
    logger.info("Sofascore found %s dated corroboration routes", len(transfers))
    return transfers


def fetch_sofascore_transfers(
    since_date: str | date | None = None,
    *,
    window: str = "auto",
    club_names: Iterable[str] = (),
) -> list[Transfer]:
    """Fetch Sofascore without making the optional source load-bearing."""
    try:
        return asyncio.run(
            _fetch_sofascore_transfers_async(
                since_date=since_date,
                window=window,
                club_names=club_names,
            )
        )
    except Exception as exc:
        logger.warning("Sofascore supplemental source unavailable: %s", exc)
        return []
