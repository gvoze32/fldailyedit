"""Best-effort BeSoccer corroboration from its current Spanish transfer feed."""

from __future__ import annotations

import asyncio
from datetime import date
import logging
import re

import aiohttp

from scraper.models import Transfer
from scraper.source_utils import date_in_range, resolve_source_date_range


logger = logging.getLogger(__name__)

BESOCCER_TRANSFERS_URL = "https://www.besoccer.es/fichajes"
BESOCCER_READER_URL = f"https://r.jina.ai/{BESOCCER_TRANSFERS_URL}"
BESOCCER_HEADERS = {
    "User-Agent": (
        "fldailyedit/0.1 (PES transfer updater; contact via project repository)"
    ),
    "Accept": "text/plain, */*;q=0.5",
}

_DATE_HEADING_RE = re.compile(
    r"^(?P<day>\d{1,2})\s+(?P<month>[A-ZÁÉÍÓÚ]{3})\s+(?P<year>\d{4})$",
    re.IGNORECASE,
)
_IMAGE_ALT_RE = re.compile(r"!\[Image \d+:\s*([^\]]+)\]\([^)]+\)")
_PLAYER_URL_RE = re.compile(
    r"\]\((https://(?:www\.)?besoccer\.(?:com|es)/jugador/[^)]+)\)\s*$",
    re.IGNORECASE,
)
_POSITION_RE = re.compile(
    r"\b(?:PT|GK|DF|DEF|MC|MF|MED|DC|FW|DEL)\s+\*\*",
    re.IGNORECASE,
)
_MONTHS = {
    "ENE": 1,
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "ABR": 4,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AGO": 8,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DIC": 12,
    "DEC": 12,
}
_ACTION_PREFIXES = (
    ("cesión o/c", "loan", True),
    ("cesión", "loan", True),
    ("fichaje gratis", "free transfer", False),
    ("agente libre", "free transfer", False),
    ("fichaje", "transfer", False),
)


def _clean(value: str) -> str:
    return " ".join((value or "").split()).strip()


def _heading_date(line: str) -> date | None:
    match = _DATE_HEADING_RE.fullmatch(_clean(line))
    if match is None:
        return None
    month = _MONTHS.get(match.group("month").upper())
    if month is None:
        return None
    try:
        return date(int(match.group("year")), month, int(match.group("day")))
    except ValueError:
        return None


def _label_details(label: str) -> tuple[str, str, bool] | None:
    clean = _clean(label)
    folded = clean.casefold()
    for prefix, transfer_type, is_loan in _ACTION_PREFIXES:
        if folded.startswith(prefix + " "):
            return clean[len(prefix) :].strip(), transfer_type, is_loan
    return None


def _position(line: str) -> str:
    match = _POSITION_RE.search(line)
    if match is None:
        return ""
    return match.group(0).split()[0].upper()


def parse_besoccer_transfer_markdown(
    markdown: str,
    source_url: str = BESOCCER_TRANSFERS_URL,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[Transfer]:
    """Parse dated confirmed routes from BeSoccer's current global feed."""
    transfers: list[Transfer] = []
    seen: set[tuple[str, str, str, str]] = set()
    event_date: date | None = None

    for line in markdown.splitlines():
        heading_date = _heading_date(line)
        if heading_date is not None:
            event_date = heading_date
            continue
        if event_date is None or not line.lstrip().startswith("*"):
            continue
        if not date_in_range(event_date, start_date, end_date):
            continue

        labels = [_clean(label) for label in _IMAGE_ALT_RE.findall(line)]
        if len(labels) < 3:
            continue
        details = _label_details(labels[0])
        proof_match = _PLAYER_URL_RE.search(line)
        if details is None or proof_match is None:
            continue

        player_name, transfer_type, is_loan = details
        if not player_name:
            continue
        if labels[0].casefold().startswith("agente libre "):
            from_club, to_club = "Free Agent", labels[-1]
        elif len(labels) >= 4:
            from_club, to_club = labels[-2], labels[-1]
        else:
            continue
        if not from_club or not to_club:
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
                position=_position(line),
                is_loan=is_loan,
                sources=("besoccer",),
                source_urls=(source_url,),
                proof_urls=(proof_match.group(1),),
                verification_status="corroborator",
            )
        )
    return transfers


parse_besoccer_transfers = parse_besoccer_transfer_markdown


async def _fetch_text(session: aiohttp.ClientSession, url: str) -> str:
    async with session.get(url) as response:
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status}")
        return await response.text()


async def _fetch_besoccer_transfers_async(
    since_date: str | date | None = None,
    *,
    window: str = "auto",
    ref_date: date | None = None,
    source_url: str = BESOCCER_TRANSFERS_URL,
) -> list[Transfer]:
    start_date, end_date = resolve_source_date_range(
        since_date, window, ref_date=ref_date
    )
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(
        headers=BESOCCER_HEADERS,
        timeout=timeout,
    ) as session:
        content = await _fetch_text(session, f"https://r.jina.ai/{source_url}")
    transfers = parse_besoccer_transfer_markdown(
        content,
        source_url,
        start_date=start_date,
        end_date=end_date,
    )
    logger.info("BeSoccer found %s dated corroboration routes", len(transfers))
    return transfers


def fetch_besoccer_transfers(
    since_date: str | date | None = None,
    *,
    window: str = "auto",
) -> list[Transfer]:
    """Fetch BeSoccer without making the optional source load-bearing."""
    try:
        return asyncio.run(
            _fetch_besoccer_transfers_async(since_date=since_date, window=window)
        )
    except Exception as exc:
        logger.warning("BeSoccer supplemental source unavailable: %s", exc)
        return []
