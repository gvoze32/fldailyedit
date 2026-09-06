"""Best-effort Sofascore corroboration by scraping its global transfer page."""

from __future__ import annotations

import asyncio
import html
import json
import logging
import re
import unicodedata
from collections.abc import Iterable
from datetime import date
from typing import Any

from curl_cffi import requests

from scraper.models import Transfer
from scraper.source_utils import date_in_range, parse_external_date, resolve_source_date_range


logger = logging.getLogger(__name__)

SOFASCORE_TRANSFER_PAGE_URL = "https://www.sofascore.com/football/player-transfers"
SOFASCORE_TRANSFER_API_URL = (
    "https://api.sofascore.app/api/v1/transfer?page=1&sort=-transferFee"
)
SOFASCORE_TRANSFER_API_APP_URL = (
    "https://sofascore.app/api/v1/transfer?page=1&sort=-transferFee"
)
SOFASCORE_TRANSFER_PAGE_APP_URL = "https://sofascore.app/football/player-transfers"
SOFASCORE_TRANSFER_PAGE_APP_WWW_URL = (
    "https://www.sofascore.app/football/player-transfers"
)
SOFASCORE_TRANSFER_PAGE_RETRIES = 2
SOFASCORE_TRANSFER_PAGE_JINA_URL = (
    "https://r.jina.ai/https://www.sofascore.com/football/player-transfers"
)
SOFASCORE_JINA_HEADERS = {
    "Accept": "text/html, application/xhtml+xml, text/plain, */*",
    "X-Respond-With": "html",
}
SOFASCORE_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/json, text/plain, */*",
    "Referer": "https://www.sofascore.com/",
}
_NEXT_DATA_RE = re.compile(
    r"<script\b[^>]*\bid=[\"']__NEXT_DATA__[\"'][^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
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


def _next_page_props(page_html: str) -> dict[str, Any] | None:
    """Extract Next.js page props from rendered HTML."""
    if not isinstance(page_html, str):
        return None
    match = _NEXT_DATA_RE.search(page_html)
    if match is None:
        return None

    raw_payload = match.group(1).strip()
    try:
        next_data = json.loads(raw_payload)
    except json.JSONDecodeError:
        try:
            next_data = json.loads(html.unescape(raw_payload))
        except json.JSONDecodeError:
            return None
    if not isinstance(next_data, dict):
        return None

    props = next_data.get("props")
    page_props = props.get("pageProps") if isinstance(props, dict) else None
    return page_props if isinstance(page_props, dict) else None


def _global_transfer_payload(page_html: str) -> Any:
    """Extract global transfer data from a rendered Sofascore page."""
    page_props = _next_page_props(page_html)
    if page_props is None:
        return None

    fallback_data = page_props.get("fallbackData")
    if isinstance(fallback_data, list):
        containers = fallback_data
    elif isinstance(fallback_data, dict):
        containers = [fallback_data]
    else:
        containers = []
    recognized = isinstance(fallback_data, (list, dict))

    transfers: list[dict[str, Any]] = []
    for container in containers:
        if not isinstance(container, dict):
            continue
        items = container.get("transfers")
        if isinstance(items, list):
            transfers.extend(item for item in items if isinstance(item, dict))

    direct_transfers = page_props.get("transfers")
    if not transfers and isinstance(direct_transfers, list):
        transfers.extend(item for item in direct_transfers if isinstance(item, dict))
        recognized = True
    if not recognized:
        embedded_transfers = _transfer_items(page_props)
        if embedded_transfers:
            return {"transfers": embedded_transfers}
    return {"transfers": transfers} if recognized else None


def parse_sofascore_transfer_page(
    page_html: str,
    source_url: str = "",
    *,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[Transfer]:
    """Parse dated transfer routes embedded in the global transfer page."""
    return parse_sofascore_transfer_payload(
        _global_transfer_payload(page_html),
        source_url,
        start_date=start_date,
        end_date=end_date,
    )


parse_sofascore_page = parse_sofascore_transfer_page


async def _fetch_transfer_page_payload(
    session: requests.AsyncSession,
) -> Any:
    """Fetch the global transfer page or its JSON backing endpoint."""
    page_requests = (
        (SOFASCORE_TRANSFER_PAGE_URL, 1, None),
        (
            SOFASCORE_TRANSFER_PAGE_JINA_URL,
            1,
            SOFASCORE_JINA_HEADERS,
        ),
        (
            SOFASCORE_TRANSFER_PAGE_APP_URL,
            SOFASCORE_TRANSFER_PAGE_RETRIES,
            None,
        ),
        (SOFASCORE_TRANSFER_PAGE_APP_WWW_URL, 1, None),
        (SOFASCORE_TRANSFER_API_URL, 1, None),
        (SOFASCORE_TRANSFER_API_APP_URL, 1, None),
    )
    last_error: Exception | None = None
    errors: list[str] = []
    for page_url, attempts, request_headers in page_requests:
        for attempt in range(attempts):
            try:
                options: dict[str, Any] = {"allow_redirects": False}
                if request_headers is not None:
                    options["headers"] = request_headers
                response = await session.get(page_url, **options)
                if response.status_code != 200:
                    raise RuntimeError(f"HTTP {response.status_code}")
                if "/api/v1/transfer" in page_url:
                    payload = response.json()
                    if not isinstance(payload, (dict, list)):
                        raise RuntimeError("API response has no transfer payload")
                else:
                    payload = _global_transfer_payload(response.text)
                    if payload is None:
                        raise RuntimeError("page has no embedded transfers")
                return payload
            except Exception as exc:
                last_error = exc
                errors.append(
                    f"{page_url} attempt {attempt + 1}: "
                    f"{type(exc).__name__}: {str(exc)[:160]}"
                )
    detail = "; ".join(errors)
    raise RuntimeError(f"Sofascore transfer page unavailable: {detail}") from last_error


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
    logger.info(
        "Sofascore scraping global transfer page for %s relevant clubs",
        len(targets),
    )
    if not targets:
        logger.debug(
            "Sofascore supplemental source skipped: no relevant transfer clubs"
        )
        return []

    async with requests.AsyncSession(
        headers=SOFASCORE_HEADERS,
        impersonate="chrome",
        timeout=30,
        max_clients=1,
    ) as session:
        payload = await _fetch_transfer_page_payload(session)

    source_url = SOFASCORE_TRANSFER_PAGE_URL
    target_keys = {_club_key(name) for name in targets}
    transfers = [
        transfer
        for transfer in parse_sofascore_transfer_payload(
            payload,
            source_url,
            start_date=start_date,
            end_date=end_date,
        )
        if _club_key(transfer.from_club) in target_keys
        or _club_key(transfer.to_club) in target_keys
    ]

    unique: list[Transfer] = []
    seen: set[tuple[str, str, str, str]] = set()
    for transfer in transfers:
        key = _transfer_key(transfer)
        if key in seen:
            continue
        seen.add(key)
        unique.append(transfer)
    logger.info("Sofascore found %s dated corroboration routes", len(unique))
    return unique


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
