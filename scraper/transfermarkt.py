"""Verified dated Transfermarkt events from Jina Reader markdown."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, datetime, timezone
from urllib.parse import urlencode

import aiohttp

from scraper.models import Transfer


TRANSFERMARKT_URL = "https://www.transfermarkt.com/transfers/neuestetransfers/statistik"
TRANSFERMARKT_READER_PREFIX = "https://r.jina.ai/"
TRANSFERMARKT_HEADERS = {
    "User-Agent": "fleditscrape/0.1 (PES transfer updater; contact via project repository)",
    "Accept": "text/markdown, text/plain;q=0.9",
    "X-Cache-Tolerance": "300",
    "X-Retain-Images": "none",
}

logger = logging.getLogger(__name__)

_LINK_RE = re.compile(
    r"\[([^\]]+)\]\((https?://[^\s)]+)(?:\s+\"([^\"]*)\")?\)"
)
_PLAYER_RE = re.compile(
    r"\[([^\]]+)\]\((https?://www\.transfermarkt\.com/[^\s)]*?/profil/spieler/(\d+))"
    r"(?:\s+\"[^\"]*\")?\)"
)
_CLUB_RE = re.compile(
    r"\[([^\]]+)\]\((https?://www\.transfermarkt\.com/[^\s)]*?/startseite/verein/(\d+)"
    r"[^\s)]*)(?:\s+\"([^\"]*)\")?\)"
)
_TRANSFER_RE = re.compile(
    r"\[([^\]]+)\]\((https?://www\.transfermarkt\.com/[^\s)]*?/transfer_id/(\d+))\)"
)
_IMAGE_LINK_RE = re.compile(r"\[!\[[^\]]*\]\([^)]*\)\]\([^)]*\)")
_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")


def _plain_markdown(value: str) -> str:
    value = _IMAGE_LINK_RE.sub("", value)
    value = _IMAGE_RE.sub("", value)
    value = _LINK_RE.sub(lambda match: match.group(1), value)
    return " ".join(value.replace("_", "").split())


def _table_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _club(cell: str) -> tuple[str, int] | None:
    clean = _IMAGE_LINK_RE.sub("", cell)
    matches = list(_CLUB_RE.finditer(clean))
    if not matches:
        return None
    match = matches[-1]
    display_name = _plain_markdown(match.group(1))
    canonical_name = " ".join((match.group(4) or display_name).split())
    return canonical_name, int(match.group(3))


def _transfer_type(fee: str) -> tuple[str, bool]:
    normalized = fee.casefold()
    if "end of loan" in normalized:
        return "end of loan", False
    if "loan" in normalized:
        return "loan", True
    if "free transfer" in normalized:
        return "free transfer", False
    return "transfer", False


def _parse_transfer_date(value: str) -> date | None:
    try:
        return datetime.strptime(_plain_markdown(value), "%d/%m/%Y").date()
    except ValueError:
        return None


def _parse_euro_amount(value: str) -> int:
    match = re.search(r"([\d.]+)\s*([mk])?", _plain_markdown(value).casefold())
    if not match:
        return 0
    multiplier = {"m": 1_000_000, "k": 1_000}.get(match.group(2), 1)
    return round(float(match.group(1)) * multiplier)


def parse_transfermarkt_markdown(
    markdown: str,
    source_url: str,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[Transfer]:
    """Parse verified dated events from Transfermarkt's detailed table."""
    lines = markdown.splitlines()
    header_index = next(
        (
            index
            for index, line in enumerate(lines)
            if line.lstrip().startswith("|")
            and [
                re.sub(r"[^a-z]+", "", _plain_markdown(cell).casefold())
                for cell in _table_cells(line)
            ]
            == [
                "player",
                "age",
                "nat",
                "left",
                "joined",
                "transferdate",
                "marketvalue",
                "fee",
            ]
        ),
        None,
    )
    if header_index is None:
        return []

    transfers: list[Transfer] = []
    seen_transfer_ids: set[int] = set()
    for line in lines[header_index + 2 :]:
        if not line.lstrip().startswith("|"):
            break
        cells = _table_cells(line)
        if len(cells) != 8:
            continue
        player_match = _PLAYER_RE.search(cells[0])
        from_club = _club(cells[3])
        to_club = _club(cells[4])
        event_date = _parse_transfer_date(cells[5])
        transfer_match = _TRANSFER_RE.search(cells[7])
        if (
            not player_match
            or not from_club
            or not to_club
            or event_date is None
            or not transfer_match
        ):
            continue
        if start_date and event_date < start_date:
            continue
        if end_date and event_date > end_date:
            continue

        transfer_id = int(transfer_match.group(3))
        if transfer_id in seen_transfer_ids:
            continue
        seen_transfer_ids.add(transfer_id)

        player_name = " ".join(player_match.group(1).split())
        player_cell_text = _plain_markdown(cells[0])
        position = player_cell_text[len(player_name) :].strip() if player_cell_text.startswith(player_name) else ""
        market_value = _parse_euro_amount(cells[6])
        fee = _plain_markdown(transfer_match.group(1))
        transfer_type, is_loan = _transfer_type(fee)
        from_name, from_id = from_club
        to_name, to_id = to_club
        transfer_url = transfer_match.group(2)
        age_text = _plain_markdown(cells[1])

        transfers.append(
            Transfer(
                player_name=player_name,
                from_club=from_name,
                to_club=to_name,
                date=event_date.isoformat(),
                transfer_type=transfer_type,
                fee=fee,
                position=position,
                is_loan=is_loan,
                market_value=market_value,
                from_club_full_name=from_name,
                to_club_full_name=to_name,
                nationality=_plain_markdown(cells[2]),
                age=int(age_text) if age_text.isdigit() else 0,
                sources=("transfermarkt",),
                source_urls=(source_url, transfer_url),
                player_id_transfermarkt=int(player_match.group(3)),
                from_club_id_transfermarkt=from_id,
                to_club_id_transfermarkt=to_id,
                transfer_id_transfermarkt=transfer_id,
                verification_status="verified",
            )
        )
    return transfers


async def _fetch_text(session: aiohttp.ClientSession, url: str) -> str:
    async with session.get(url) as response:
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status}")
        return await response.text()


async def _fetch_transfermarkt_transfers_async(
    max_pages: int = 4,
    since_date: str | date | None = None,
    *,
    ref_date: date | None = None,
) -> list[Transfer]:
    """Read detailed pages until cutoff, empty data, or repeated IDs."""
    if max_pages <= 0:
        return []

    start_date = (
        date.fromisoformat(since_date)
        if isinstance(since_date, str)
        else since_date
    )
    if isinstance(start_date, datetime):
        start_date = start_date.date()
    today = ref_date or datetime.now(timezone.utc).date()
    timeout = aiohttp.ClientTimeout(total=30)
    transfers: list[Transfer] = []
    seen_transfer_ids: set[int] = set()
    async with aiohttp.ClientSession(
        headers=TRANSFERMARKT_HEADERS,
        timeout=timeout,
    ) as session:
        for page in range(1, max_pages + 1):
            params: dict[str, str | int] = {
                "land_id": 0,
                "verein_land_id": 0,
                "wettbewerb_id": "alle",
                "plus": 1,
            }
            if page > 1:
                params["page"] = page
            source_url = f"{TRANSFERMARKT_URL}?{urlencode(params)}"
            markdown = await _fetch_text(
                session,
                f"{TRANSFERMARKT_READER_PREFIX}{source_url}",
            )
            batch = parse_transfermarkt_markdown(
                markdown,
                source_url,
                start_date=start_date,
                end_date=today,
            )
            if not batch:
                break
            new = [
                transfer
                for transfer in batch
                if transfer.transfer_id_transfermarkt not in seen_transfer_ids
            ]
            if not new:
                break
            transfers.extend(new)
            seen_transfer_ids.update(
                transfer.transfer_id_transfermarkt
                for transfer in new
                if transfer.transfer_id_transfermarkt is not None
            )

    logger.info(
        "Transfermarkt found %s verified dated transfers",
        len(transfers),
    )
    return transfers


def fetch_transfermarkt_transfers(
    max_pages: int = 4,
    since_date: str | date | None = None,
) -> list[Transfer]:
    """Fetch verified dated events without failing the primary pipeline."""
    try:
        return asyncio.run(
            _fetch_transfermarkt_transfers_async(
                max_pages=max_pages,
                since_date=since_date,
            )
        )
    except Exception as exc:
        logger.warning("Transfermarkt supplemental source unavailable: %s", exc)
        return []
