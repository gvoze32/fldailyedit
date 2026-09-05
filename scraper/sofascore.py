"""Best-effort Sofascore transfer corroboration from its public web page."""

from __future__ import annotations

import asyncio
from datetime import date
import html
import json
import logging
import re
from typing import Any

import aiohttp

from scraper.models import Transfer
from scraper.source_utils import date_in_range, parse_external_date, resolve_source_date_range


logger = logging.getLogger(__name__)

SOFASCORE_TRANSFERS_URL = "https://www.sofascore.com/football/player-transfers"
SOFASCORE_HEADERS = {
    "User-Agent": "fldailyedit/0.1 (PES transfer updater; contact via project repository)",
    "Accept": "text/html, application/xhtml+xml;q=0.9, */*;q=0.5",
}

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


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split()).strip()


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
    if any(token in normalized for token in ("released", "waived", "retired", "contractexpired")):
        return "free transfer", False
    if "transfer" in normalized or "traded" in normalized or "draft" in normalized:
        return "transfer", False
    if "signed" in normalized or "claimed" in normalized:
        return "transfer", False
    return None


def _payload_from_html(document: str) -> Any:
    match = re.search(
        r"<script[^>]+id=[\"']__NEXT_DATA__[\"'][^>]*>(.*?)</script>",
        document,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return None
    try:
        return json.loads(html.unescape(match.group(1)))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _transfer_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = _payload_from_html(payload)
    if payload is None:
        return []

    result: list[dict[str, Any]] = []
    visited_containers: set[int] = set()
    visited_items: set[int] = set()

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            transfers = value.get("transfers")
            if isinstance(transfers, list) and id(transfers) not in visited_containers:
                visited_containers.add(id(transfers))
                for item in transfers:
                    if isinstance(item, dict) and id(item) not in visited_items:
                        visited_items.add(id(item))
                        result.append(item)
            for child in value.values():
                if isinstance(child, (dict, list)):
                    walk(child)
        elif isinstance(value, list):
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
    source_url: str = SOFASCORE_TRANSFERS_URL,
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
                source_urls=(source_url,),
                verification_status="corroborator",
            )
        )
    return transfers


def parse_sofascore_transfer_html(
    document: str,
    source_url: str = SOFASCORE_TRANSFERS_URL,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[Transfer]:
    """Parse the server-rendered ``__NEXT_DATA__`` transfer payload."""
    return parse_sofascore_transfer_payload(
        document,
        source_url,
        start_date=start_date,
        end_date=end_date,
    )


parse_sofascore_transfers = parse_sofascore_transfer_payload


async def _fetch_text(session: aiohttp.ClientSession, url: str) -> str:
    async with session.get(url) as response:
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status}")
        return await response.text()


async def _fetch_sofascore_transfers_async(
    since_date: str | date | None = None,
    *,
    window: str = "auto",
    ref_date: date | None = None,
    source_url: str = SOFASCORE_TRANSFERS_URL,
) -> list[Transfer]:
    start_date, end_date = resolve_source_date_range(
        since_date, window, ref_date=ref_date
    )
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(
        headers=SOFASCORE_HEADERS,
        timeout=timeout,
    ) as session:
        document = await _fetch_text(session, source_url)
    transfers = parse_sofascore_transfer_html(
        document,
        source_url,
        start_date=start_date,
        end_date=end_date,
    )
    logger.info("Sofascore found %s dated corroboration routes", len(transfers))
    return transfers


def fetch_sofascore_transfers(
    since_date: str | date | None = None,
    *,
    window: str = "auto",
) -> list[Transfer]:
    """Fetch Sofascore's public page without making it load-bearing."""
    try:
        return asyncio.run(
            _fetch_sofascore_transfers_async(since_date=since_date, window=window)
        )
    except Exception as exc:
        logger.warning("Sofascore supplemental source unavailable: %s", exc)
        return []
