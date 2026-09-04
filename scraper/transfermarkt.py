"""Verified dated Transfermarkt events from Jina Reader markdown."""

from __future__ import annotations

import asyncio
import logging
import re
import time
import unicodedata
from datetime import date, datetime, timezone
from urllib.parse import urlencode

import aiohttp

from scraper.models import Transfer


TRANSFERMARKT_URL = "https://www.transfermarkt.com/transfers/neuestetransfers/statistik"
AUTO_PAGE_LIMIT = 250

TRANSFERMARKT_READER_PREFIX = "https://r.jina.ai/"
TRANSFERMARKT_FALLBACK_DOMAIN = "www.transfermarkt.de"
TRANSFERMARKT_HEADERS = {
    "User-Agent": "fldailyedit/0.1 (PES transfer updater; contact via project repository)",
    "Accept": "text/markdown, text/plain;q=0.9",
    "X-Cache-Tolerance": "300",
    "X-Retain-Images": "none",
}

logger = logging.getLogger(__name__)


class TransfermarktUnavailableError(RuntimeError):
    """Expected upstream failure for the optional Transfermarkt source."""


_TRANSFERMARKT_HOST = r"(?:www\.)?transfermarkt\.[^/\s)]+"
_LINK_RE = re.compile(
    r"\[([^\]]+)\]\((https?://[^\s)]+)(?:\s+\"([^\"]*)\")?\)"
)
_PLAYER_RE = re.compile(
    rf"\[([^\]]+)\]\((https?://{_TRANSFERMARKT_HOST}/[^\s)]*?/profil/spieler/(\d+))"
    rf"(?:\s+\"[^\"]*\")?\)"
)
_CLUB_RE = re.compile(
    rf"\[([^\]]+)\]\((https?://{_TRANSFERMARKT_HOST}/[^\s)]*?/startseite/verein/(\d+)"
    rf"[^\s)]*)(?:\s+\"([^\"]*)\")?\)"
)
_TRANSFER_RE = re.compile(
    rf"\[([^\]]+)\]\((https?://{_TRANSFERMARKT_HOST}/[^\s)]*?/transfer_id/(\d+))"
    rf"(?:\s+\"[^\"]*\")?\)"
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


_TABLE_COLUMN_ALIASES = {
    "player": frozenset({"player", "spieler"}),
    "age": frozenset({"age", "alter"}),
    "nationality": frozenset({"nat", "nationality", "nationalitat"}),
    "left": frozenset(
        {"left", "from", "fromclub", "previousclub", "abgebenderverein"}
    ),
    "joined": frozenset(
        {"joined", "to", "toclub", "newclub", "aufnehmenderverein"}
    ),
    "date": frozenset({"transferdate", "transferdatum", "date", "datum"}),
    "market_value": frozenset({"marketvalue", "marktwert"}),
    "fee": frozenset({"fee", "transferfee", "ablose", "gebuhr"}),
}
_REQUIRED_TABLE_COLUMNS = ("player", "left", "joined", "date", "fee")
_TABLE_SEPARATOR_RE = re.compile(r":?-{3,}:?$")


def _header_key(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", _plain_markdown(value).casefold())
    return "".join(
        character
        for character in decomposed
        if character.isalnum() and not unicodedata.combining(character)
    )


def _table_columns(line: str) -> dict[str, int] | None:
    if not line.lstrip().startswith("|"):
        return None
    keys = [_header_key(cell) for cell in _table_cells(line)]
    columns: dict[str, int] = {}
    for column, aliases in _TABLE_COLUMN_ALIASES.items():
        match = next(
            (index for index, key in enumerate(keys) if key in aliases),
            None,
        )
        if match is not None:
            columns[column] = match
    if not all(column in columns for column in _REQUIRED_TABLE_COLUMNS):
        return None
    return columns


def _is_table_separator(cells: list[str]) -> bool:
    return bool(cells) and all(_TABLE_SEPARATOR_RE.fullmatch(cell) for cell in cells)


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
    normalized = _header_key(fee)
    if "endofloan" in normalized or "leihende" in normalized:
        return "end of loan", False
    if "loan" in normalized or "leih" in normalized:
        return "loan", True
    if "freetransfer" in normalized or "ablosefrei" in normalized:
        return "free transfer", False
    return "transfer", False


def _parse_transfer_date(value: str) -> date | None:
    clean = _plain_markdown(value).strip()
    for fmt in ("%d/%m/%Y", "%d.%m.%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(clean, fmt).date()
        except ValueError:
            continue
    return None


def _parse_euro_amount(value: str) -> int:
    match = re.search(r"([\d.,]+)\s*([mk])?", _plain_markdown(value).casefold())
    if not match:
        return 0
    number = match.group(1)
    if "," in number and "." in number:
        if number.rfind(",") > number.rfind("."):
            number = number.replace(".", "").replace(",", ".")
        else:
            number = number.replace(",", "")
    else:
        number = number.replace(",", ".")
    multiplier = {"m": 1_000_000, "k": 1_000}.get(match.group(2), 1)
    try:
        return round(float(number) * multiplier)
    except ValueError:
        return 0


def parse_transfermarkt_markdown(
    markdown: str,
    source_url: str,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[Transfer]:
    """Parse verified dated events from a localized detailed table."""
    lines = markdown.splitlines()
    header_index: int | None = None
    columns: dict[str, int] | None = None
    for index, line in enumerate(lines):
        candidate = _table_columns(line)
        if candidate is not None:
            header_index = index
            columns = candidate
            break
    if header_index is None or columns is None:
        return []

    transfers: list[Transfer] = []
    seen_transfer_ids: set[int] = set()
    max_column = max(columns.values())
    data_started = False
    for line in lines[header_index + 1 :]:
        if not line.lstrip().startswith("|"):
            if data_started:
                break
            continue
        cells = _table_cells(line)
        if _is_table_separator(cells):
            continue
        data_started = True
        if len(cells) <= max_column:
            continue

        player_cell = cells[columns["player"]]
        player_match = _PLAYER_RE.search(player_cell)
        from_club = _club(cells[columns["left"]])
        to_club = _club(cells[columns["joined"]])
        event_date = _parse_transfer_date(cells[columns["date"]])
        transfer_match = _TRANSFER_RE.search(cells[columns["fee"]])
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
        player_cell_text = _plain_markdown(player_cell)
        position = (
            player_cell_text[len(player_name) :].strip()
            if player_cell_text.startswith(player_name)
            else ""
        )
        market_value = _parse_euro_amount(
            cells[columns["market_value"]]
            if "market_value" in columns
            else ""
        )
        fee = _plain_markdown(transfer_match.group(1))
        transfer_type, is_loan = _transfer_type(fee)
        from_name, from_id = from_club
        to_name, to_id = to_club
        transfer_url = transfer_match.group(2)
        age_text = (
            _plain_markdown(cells[columns["age"]])
            if "age" in columns
            else ""
        )
        nationality = (
            _plain_markdown(cells[columns["nationality"]])
            if "nationality" in columns
            else ""
        )

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
                nationality=nationality,
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
            raise TransfermarktUnavailableError(f"HTTP {response.status}")
        return await response.text()


def _fresh_reader_url(source_url: str) -> str:
    """Build a cache-busting Reader URL for an empty or stale response."""
    separator = "&" if "?" in source_url else "?"
    return (
        f"{TRANSFERMARKT_READER_PREFIX}{source_url}"
        f"{separator}fldailyedit_refresh={time.time_ns()}"
    )


def _fallback_source_url(source_url: str) -> str | None:
    """Switch to Transfermarkt's German mirror when the primary reader is empty."""
    primary_domain = "www.transfermarkt.com"
    if primary_domain not in source_url:
        return None
    return source_url.replace(primary_domain, TRANSFERMARKT_FALLBACK_DOMAIN, 1)

async def _fetch_transfermarkt_transfers_async(
    max_pages: int | None = None,
    since_date: str | date | None = None,
    *,
    ref_date: date | None = None,
) -> list[Transfer]:
    """Read pages until the cutoff, repetition, or the safety page limit."""
    page_limit = AUTO_PAGE_LIMIT if max_pages is None else max_pages
    if page_limit <= 0:
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
        for page in range(1, page_limit + 1):
            params: dict[str, str | int] = {
                "land_id": 0,
                "verein_land_id": 0,
                "wettbewerb_id": "alle",
                "plus": 1,
            }
            if page > 1:
                params["page"] = page
            source_url = f"{TRANSFERMARKT_URL}?{urlencode(params)}"
            reader_candidates = [
                (source_url, f"{TRANSFERMARKT_READER_PREFIX}{source_url}"),
                (source_url, _fresh_reader_url(source_url)),
            ]
            fallback_source_url = _fallback_source_url(source_url)
            if fallback_source_url is not None:
                reader_candidates.append(
                    (
                        fallback_source_url,
                        _fresh_reader_url(fallback_source_url),
                    )
                )

            batch: list[Transfer] = []
            fetched_response = False
            last_fetch_error: Exception | None = None
            for candidate_source_url, candidate_reader_url in reader_candidates:
                try:
                    markdown = await _fetch_text(session, candidate_reader_url)
                except (
                    TransfermarktUnavailableError,
                    aiohttp.ClientError,
                    TimeoutError,
                ) as exc:
                    last_fetch_error = exc
                    continue
                fetched_response = True
                batch = parse_transfermarkt_markdown(
                    markdown,
                    candidate_source_url,
                )
                if batch:
                    if candidate_source_url != source_url:
                        logger.info(
                            "Transfermarkt primary reader empty; "
                            "using fallback domain for page %s",
                            page,
                        )
                    break

            if not batch:
                if last_fetch_error is not None and not fetched_response:
                    raise last_fetch_error
                logger.warning(
                    "Transfermarkt page %s returned no parseable dated rows",
                    page,
                )
                break

            eligible = [
                transfer
                for transfer in batch
                if (
                    (
                        start_date is None
                        or date.fromisoformat(transfer.date) >= start_date
                    )
                    and date.fromisoformat(transfer.date) <= today
                )
            ]
            if not eligible:
                if start_date and all(
                    date.fromisoformat(transfer.date) < start_date
                    for transfer in batch
                ):
                    break
                continue

            new = [
                transfer
                for transfer in eligible
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
    max_pages: int | None = None,
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
    except (TransfermarktUnavailableError, aiohttp.ClientError, TimeoutError) as exc:
        detail = str(exc) or type(exc).__name__
        logger.debug(
            "Transfermarkt supplemental source unavailable: %s",
            detail,
        )
        return []
    except Exception as exc:
        logger.warning("Transfermarkt supplemental source unavailable: %s", exc)
        return []
