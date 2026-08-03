"""Confirmed transfer adapter for Wikipedia's seasonal transfer lists."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from html import unescape
from html.parser import HTMLParser
import logging
import re
from typing import Iterable
from urllib.parse import quote

import aiohttp

from scraper.fotmob import parse_iso_date
from scraper.models import Transfer


logger = logging.getLogger(__name__)

WIKIPEDIA_API_URL = "https://en.wikipedia.org/w/api.php"
WIKIPEDIA_HEADERS = {
    "User-Agent": "fleditscrape/0.1 (PES transfer updater; contact via project repository)",
    "Accept": "application/json",
}

_A_LEAGUE_TEAM_NAMES = {
    "AIS": "Australian Institute of Sport",
    "AU": "Adelaide United",
    "AUC": "Auckland FC",
    "BR": "Brisbane Roar",
    "CCM": "Central Coast Mariners",
    "GC": "Gold Coast United",
    "GCU": "Gold Coast United",
    "MAC": "Macarthur FC",
    "MC": "Melbourne City",
    "MH": "Melbourne Heart",
    "MV": "Melbourne Victory",
    "NJ": "Newcastle Jets",
    "NQ": "North Queensland Fury",
    "NQF": "North Queensland Fury",
    "NQT": "North Queensland Thunder",
    "NUJ": "Newcastle Jets",
    "NZK": "New Zealand Knights",
    "PG": "Perth Glory",
    "QR": "Queensland Roar",
    "SFC": "Sydney FC",
    "SR": "Sydney Rovers",
    "WP": "Wellington Phoenix",
    "WSW": "Western Sydney Wanderers",
    "WU": "Western United",
}


def _clean_text(value: str) -> str:
    return " ".join(value.replace("\xa0", " ").split()).strip()


class _TransferTableParser(HTMLParser):
    """Extract table cell text while ignoring citation and hidden sort text."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[tuple[str, str]]]] = []
        self._table_depth = 0
        self._rows: list[list[tuple[str, str]]] = []
        self._row: list[tuple[str, str]] | None = None
        self._cell_tag: str | None = None
        self._cell_parts: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs):
        attrs_map = dict(attrs)
        if tag == "table":
            self._table_depth += 1
            if self._table_depth == 1:
                self._rows = []
            return
        if self._table_depth != 1:
            return
        if tag == "tr":
            self._row = []
        elif tag in {"th", "td"} and self._row is not None:
            self._cell_tag = tag
            self._cell_parts = []
            self._ignored_depth = 0
        elif self._cell_tag and (
            tag == "sup" or "display:none" in attrs_map.get("style", "").replace(" ", "")
        ):
            self._ignored_depth += 1

    def handle_endtag(self, tag: str):
        if tag == "table":
            if self._table_depth == 1 and self._rows:
                self.tables.append(self._rows)
            self._table_depth = max(0, self._table_depth - 1)
            return
        if self._table_depth != 1:
            return
        if self._cell_tag and self._ignored_depth:
            if tag in {"sup", "span"}:
                self._ignored_depth -= 1
            return
        if tag == self._cell_tag and self._row is not None:
            self._row.append((self._cell_tag, _clean_text("".join(self._cell_parts))))
            self._cell_tag = None
            self._cell_parts = []
        elif tag == "tr" and self._row is not None:
            if self._row:
                self._rows.append(self._row)
            self._row = None

    def handle_data(self, data: str):
        if self._cell_tag and not self._ignored_depth:
            self._cell_parts.append(data)


def _parse_human_date(value: str) -> date | None:
    clean = re.sub(r"(?<=\d)(?:st|nd|rd|th)\b", "", _clean_text(value))
    for fmt in ("%d %B %Y", "%B %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(clean, fmt).date()
        except ValueError:
            continue
    return None


def _header_index(headers: list[str], choices: set[str]) -> int | None:
    for index, header in enumerate(headers):
        normalized = re.sub(r"[^a-z]+", " ", header.casefold()).strip()
        if normalized in choices:
            return index
    return None


def _transfer_type(fee: str) -> tuple[str, bool]:
    normalized = fee.casefold()
    if "end of loan" in normalized or "loan return" in normalized:
        return "end of loan", False
    if "loan" in normalized:
        return "loan", True
    if "free" in normalized:
        return "free transfer", False
    return "transfer", False


def parse_wikipedia_transfer_html(
    html: str,
    page_title: str,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[Transfer]:
    """Parse only tables with an explicit Date/Player/From/To schema."""
    parser = _TransferTableParser()
    parser.feed(html)
    source_url = f"https://en.wikipedia.org/wiki/{quote(page_title.replace(' ', '_'))}"
    parsed: list[Transfer] = []

    for table in parser.tables:
        header_row_index = next(
            (
                index
                for index, row in enumerate(table)
                if row and all(tag == "th" for tag, _ in row)
            ),
            None,
        )
        if header_row_index is None:
            continue
        headers = [text for _, text in table[header_row_index]]
        date_index = _header_index(headers, {"date"})
        player_index = _header_index(headers, {"player", "name"})
        from_index = _header_index(headers, {"from", "moving from", "previous club"})
        to_index = _header_index(headers, {"to", "moving to", "new club"})
        fee_index = _header_index(headers, {"fee", "transfer fee"})
        if None in {date_index, player_index, from_index, to_index}:
            continue

        last_date_text = ""
        for row in table[header_row_index + 1 :]:
            values = [text for tag, text in row if tag == "td"]
            if not values:
                continue
            if len(values) == len(headers) - 1 and last_date_text:
                values.insert(int(date_index), last_date_text)
            if len(values) <= max(int(date_index), int(player_index), int(from_index), int(to_index)):
                continue

            date_text = values[int(date_index)]
            event_date = _parse_human_date(date_text)
            if event_date is not None:
                last_date_text = date_text
            if event_date is None:
                continue
            if start_date and event_date < start_date:
                continue
            if end_date and event_date > end_date:
                continue

            player = _clean_text(values[int(player_index)])
            from_club = _clean_text(values[int(from_index)])
            to_club = _clean_text(values[int(to_index)])
            if not player or not from_club or not to_club or from_club == to_club:
                continue
            fee = values[int(fee_index)] if fee_index is not None and len(values) > fee_index else ""
            transfer_type, is_loan = _transfer_type(fee)
            parsed.append(
                Transfer(
                    player_name=player,
                    from_club=from_club,
                    to_club=to_club,
                    date=event_date.isoformat(),
                    transfer_type=transfer_type,
                    fee=fee,
                    is_loan=is_loan,
                    sources=("wikipedia",),
                    source_urls=(source_url,),
                )
            )
    return parsed


def _split_top_level(value: str, delimiter: str) -> list[str]:
    """Split wiki cells without cutting delimiters inside links/templates."""
    parts: list[str] = []
    start = 0
    template_depth = 0
    link_depth = 0
    index = 0
    while index < len(value):
        pair = value[index : index + 2]
        if pair == "{{":
            template_depth += 1
            index += 2
            continue
        if pair == "}}":
            template_depth = max(0, template_depth - 1)
            index += 2
            continue
        if pair == "[[":
            link_depth += 1
            index += 2
            continue
        if pair == "]]":
            link_depth = max(0, link_depth - 1)
            index += 2
            continue
        if not template_depth and not link_depth and value.startswith(delimiter, index):
            parts.append(value[start:index])
            index += len(delimiter)
            start = index
            continue
        index += 1
    parts.append(value[start:])
    return parts


def _clean_wikitext(value: str) -> str:
    value = re.sub(r"(?is)<!--.*?-->", "", value)
    value = re.sub(r"(?is)<ref\b[^>]*>.*?</ref\s*>", "", value)
    value = re.sub(r"(?is)<ref\b[^>]*/\s*>", "", value)
    value = re.sub(
        r"(?is)\{\{\s*A-League team\s*\|\s*([^|{}]+)(?:\|[^{}]*)?\}\}",
        lambda match: _A_LEAGUE_TEAM_NAMES.get(
            match.group(1).strip().upper(),
            match.group(1).strip(),
        ),
        value,
    )

    def date_template(match: re.Match) -> str:
        parts = [
            part.strip()
            for part in _split_top_level(match.group(1), "|")
            if part.strip() and "=" not in part
        ]
        if len(parts) < 3 or not all(part.isdigit() for part in parts[:3]):
            return ""
        try:
            return date(*(int(part) for part in parts[:3])).isoformat()
        except ValueError:
            return ""

    value = re.sub(
        r"(?is)\{\{\s*dts\s*\|([^{}]*)\}\}",
        date_template,
        value,
    )
    value = re.sub(
        r"(?is)\{\{\s*sortname\s*\|\s*([^|{}]+)\|\s*([^|{}]+)(?:\|.*?)?\}\}",
        lambda match: f"{match.group(1).strip()} {match.group(2).strip()}",
        value,
    )
    value = re.sub(r"(?is)\{\{\s*flag(?:g|icon)?\b.*?\}\}\s*", "", value)
    value = re.sub(r"(?is)\{\{\s*ntsh\s*\|.*?\}\}", "", value)
    value = re.sub(
        r"\[\[([^\]|]+)\|([^\]]+)\]\]", lambda match: match.group(2), value
    )
    value = re.sub(r"\[\[([^\]]+)\]\]", lambda match: match.group(1), value)
    value = re.sub(
        r"(?is)\{\{\s*(?:nowrap|small)\s*\|\s*(.*?)\}\}",
        lambda match: match.group(1),
        value,
    )

    def generic_template(match: re.Match) -> str:
        body = match.group(1)
        parts = [part.strip() for part in _split_top_level(body, "|")]
        name = parts[0] if parts else ""
        positional = [part for part in parts[1:] if part and "=" not in part]
        lowered = name.casefold()
        if lowered in {"loan", "free transfer", "free", "released"}:
            return name
        if positional:
            return positional[-1]
        if any(token in lowered for token in ("cite", "ref", "flag")):
            return ""
        return name if len(parts) == 1 else ""

    for _ in range(4):
        updated = re.sub(r"\{\{([^{}]*)\}\}", generic_template, value)
        if updated == value:
            break
        value = updated
    value = re.sub(r"\[(?:https?://\S+)\s+([^\]]+)\]", r"\1", value)
    value = re.sub(r"(?is)<[^>]+>", "", value)
    value = value.replace("'''", "").replace("''", "")
    return _clean_text(unescape(value))


def _clean_route_club(value: str) -> str:
    return re.sub(
        r"\s*\((?:end of loan|loan return|loan|free transfer)\)\s*$",
        "",
        _clean_wikitext(value),
        flags=re.I,
    ).strip()


def _wiki_cell(value: str) -> str:
    # Strip table-cell attributes such as rowspan="4" | while preserving the
    # first pipe that belongs to a template or wikilink.
    stripped = value.strip()
    attribute_match = re.match(
        r"^(?:(?:rowspan|colspan|style|class|data-sort-value)\s*=.+?)\|(.*)$",
        stripped,
        re.IGNORECASE | re.DOTALL,
    )
    return attribute_match.group(1).strip() if attribute_match else stripped


def _wikitext_tables(wikitext: str) -> list[tuple[list[str], list[list[str]]]]:
    tables: list[tuple[list[str], list[list[str]]]] = []
    for table_match in re.finditer(r"(?ms)^\{\|.*?^\|\}", wikitext):
        headers: list[str] = []
        rows: list[list[str]] = []
        current: list[str] = []
        in_header = True
        for raw_line in table_match.group(0).splitlines()[1:]:
            line = raw_line.strip()
            if line.startswith("|-"):
                if current:
                    rows.append(current)
                    current = []
                if headers:
                    in_header = False
                continue
            if line == "|}":
                if current:
                    rows.append(current)
                break
            if line.startswith("!") and in_header:
                headers.extend(
                    _clean_wikitext(_wiki_cell(part))
                    for part in _split_top_level(line[1:], "!!")
                )
            elif line.startswith("|"):
                current.extend(
                    _wiki_cell(part) for part in _split_top_level(line[1:], "||")
                )
            elif current:
                current[-1] = f"{current[-1]} {line}"
        if headers and rows:
            tables.append((headers, rows))
    return tables


def _template_body(value: str, template_name: str) -> str | None:
    """Return one balanced template body, including nested templates."""
    match = re.search(rf"\{{\{{\s*{re.escape(template_name)}\b", value, re.I)
    if match is None:
        return None
    start = match.start()
    depth = 0
    index = start
    while index < len(value) - 1:
        pair = value[index : index + 2]
        if pair == "{{":
            depth += 1
            index += 2
            continue
        if pair == "}}":
            depth -= 1
            if depth == 0:
                return value[start + 2 : index]
            index += 2
            continue
        index += 1
    return None


def _parse_wikipedia_club_lists(
    wikitext: str,
    source_url: str,
) -> list[Transfer]:
    """Parse undated club In/Out lists as corroboration-only routes."""
    parsed: list[Transfer] = []
    club = ""
    direction = ""

    for raw_line in wikitext.splitlines():
        line = raw_line.strip()
        heading_match = re.fullmatch(r"===\s*(.*?)\s*===", line)
        if heading_match:
            club = _clean_wikitext(heading_match.group(1))
            direction = ""
            continue

        marker = _clean_wikitext(line).casefold().rstrip(":")
        if marker in {"in", "out"}:
            direction = marker
            continue
        if not club or direction not in {"in", "out"}:
            continue

        body = _template_body(line, "fs player")
        if body is None:
            continue
        parts = _split_top_level(body, "|")
        params: dict[str, str] = {}
        for part in parts[1:]:
            if "=" not in part:
                continue
            name, value = part.split("=", 1)
            params[name.strip().casefold()] = value.strip()

        player = _clean_wikitext(params.get("name", ""))
        other_raw = params.get("other", "")
        other = _clean_wikitext(other_raw)
        route_match = re.search(
            r"\bfrom\s+(.+)" if direction == "in" else r"\bto\s+(.+)",
            other,
            re.I,
        )
        if not player or route_match is None:
            continue
        other_club = re.split(
            r",\s*(?:previously|after|following)\b",
            route_match.group(1),
            maxsplit=1,
            flags=re.I,
        )[0].strip()
        if not other_club or other_club == club:
            continue

        normalized_other = other.casefold()
        if "loan return" in normalized_other:
            transfer_type, is_loan = "end of loan", False
        elif "loan" in normalized_other:
            transfer_type, is_loan = "loan", True
        elif "free" in normalized_other:
            transfer_type, is_loan = "free transfer", False
        else:
            transfer_type, is_loan = "transfer", False
        from_club, to_club = (
            (other_club, club) if direction == "in" else (club, other_club)
        )
        proof_urls = tuple(
            dict.fromkeys(
                re.findall(r"\burl\s*=\s*(https?://[^|}\s]+)", line, re.I)
            )
        )
        parsed.append(
            Transfer(
                player_name=player,
                from_club=from_club,
                to_club=to_club,
                transfer_type=transfer_type,
                position=_clean_wikitext(params.get("pos", "")),
                is_loan=is_loan,
                sources=("wikipedia",),
                source_urls=(source_url,),
                proof_urls=proof_urls,
                verification_status="corroborator",
            )
        )
    return parsed


def parse_wikipedia_transfer_wikitext(
    wikitext: str,
    page_title: str,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[Transfer]:
    """Parse dated table events and undated club-list corroborators."""
    source_url = f"https://en.wikipedia.org/wiki/{quote(page_title.replace(' ', '_'))}"
    parsed: list[Transfer] = _parse_wikipedia_club_lists(wikitext, source_url)
    for headers, rows in _wikitext_tables(wikitext):
        date_index = _header_index(headers, {"date"})
        player_index = _header_index(headers, {"player", "name"})
        from_index = _header_index(headers, {"from", "moving from", "previous club"})
        to_index = _header_index(headers, {"to", "moving to", "new club"})
        fee_index = _header_index(headers, {"fee", "transfer fee"})
        if None in {date_index, player_index, from_index, to_index}:
            continue
        last_date_text = ""
        for raw_values in rows:
            values = list(raw_values)
            if len(values) == len(headers) - 1 and last_date_text:
                values.insert(int(date_index), last_date_text)
            if len(values) <= max(int(date_index), int(player_index), int(from_index), int(to_index)):
                continue
            date_text = _clean_wikitext(values[int(date_index)])
            event_date = _parse_human_date(date_text)
            if event_date:
                last_date_text = values[int(date_index)]
            if event_date is None:
                continue
            if start_date and event_date < start_date:
                continue
            if end_date and event_date > end_date:
                continue
            player = _clean_wikitext(values[int(player_index)])
            from_raw = values[int(from_index)]
            to_raw = values[int(to_index)]
            from_club = _clean_route_club(from_raw)
            to_club = _clean_route_club(to_raw)
            fee_raw = values[int(fee_index)] if fee_index is not None and len(values) > fee_index else ""
            fee = _clean_wikitext(fee_raw)
            if not player or not from_club or not to_club or from_club == to_club:
                continue
            route_detail = _clean_wikitext(" ".join((from_raw, to_raw, fee_raw)))
            transfer_type, is_loan = _transfer_type(route_detail)
            proof_urls = tuple(
                dict.fromkeys(
                    re.findall(
                        r"\burl\s*=\s*(https?://[^|}\s]+)",
                        " ".join(values),
                    )
                )
            )
            parsed.append(
                Transfer(
                    player_name=player,
                    from_club=from_club,
                    to_club=to_club,
                    date=event_date.isoformat(),
                    transfer_type=transfer_type,
                    fee=fee,
                    is_loan=is_loan,
                    sources=("wikipedia",),
                    source_urls=(source_url,),
                    proof_urls=proof_urls,
                )
            )
    return parsed


def _category_candidates(
    today: date,
    since_date: str | date | None,
    window: str,
) -> list[str]:
    if isinstance(since_date, str):
        since = parse_iso_date(since_date)
    elif isinstance(since_date, datetime):
        since = since_date.date()
    else:
        since = since_date

    years: Iterable[int]
    if since:
        # Supplemental sources are for recent freshness. FotMob remains the
        # historical replay source, so cap category discovery to two seasons.
        first_year = max(since.year, today.year - 1)
        years = range(first_year, today.year + 1)
    else:
        years = (today.year,)

    requested = (window or "auto").casefold()
    if requested == "summer":
        periods = ("summer",)
    elif requested == "winter":
        periods = ("winter",)
    elif requested == "all":
        periods = ("winter", "summer")
    else:
        periods = ("winter",) if today.month <= 3 else ("summer",)

    categories: list[str] = []
    for year in years:
        for period in periods:
            categories.append(f"Category:Football transfers {period} {year}")
            if period == "winter":
                categories.append(
                    f"Category:Football transfers winter {year - 1}\u2013{str(year)[-2:]}"
                )
    return list(dict.fromkeys(categories))


async def _fetch_json(session: aiohttp.ClientSession, **params):
    for attempt in range(4):
        retry_delay = 0.0
        async with session.get(
            WIKIPEDIA_API_URL,
            params={"format": "json", "maxlag": "5", **params},
        ) as response:
            if response.status == 200:
                return await response.json(content_type=None)
            if response.status in {429, 503} and attempt < 3:
                retry_header = response.headers.get("Retry-After", "")
                retry_delay = min(
                    float(retry_header) if retry_header.isdigit() else 2**attempt,
                    5.0,
                )
            else:
                raise RuntimeError(f"Wikipedia API returned HTTP {response.status}")
        await asyncio.sleep(retry_delay)
    raise RuntimeError("Wikipedia API retry limit reached")


async def _fetch_wikipedia_transfers_async(
    since_date: str | date | None = None,
    window: str = "auto",
    *,
    ref_date: date | None = None,
) -> list[Transfer]:
    today = ref_date or datetime.now(timezone.utc).date()
    start_date = parse_iso_date(since_date) if isinstance(since_date, str) else since_date
    if isinstance(start_date, datetime):
        start_date = start_date.date()
    if start_date is None and (window or "auto").casefold() != "all":
        if (window or "auto").casefold() == "winter" or (
            (window or "auto").casefold() == "auto" and today.month <= 3
        ):
            start_date = date(today.year, 1, 1)
        else:
            year = today.year if today.month >= 6 else today.year - 1
            start_date = date(year, 6, 1)

    timeout = aiohttp.ClientTimeout(total=30)
    connector = aiohttp.TCPConnector(limit=6)
    async with aiohttp.ClientSession(
        headers=WIKIPEDIA_HEADERS,
        timeout=timeout,
        connector=connector,
        cookie_jar=aiohttp.DummyCookieJar(),
    ) as session:
        page_titles: list[str] = []
        for category in _category_candidates(today, since_date, window):
            try:
                payload = await _fetch_json(
                    session,
                    action="query",
                    list="categorymembers",
                    cmtitle=category,
                    cmnamespace="0",
                    cmlimit="max",
                )
            except Exception as exc:
                logger.warning("Wikipedia category %s unavailable: %s", category, exc)
                continue
            members = payload.get("query", {}).get("categorymembers", [])
            page_titles.extend(
                str(member.get("title", ""))
                for member in members
                if member.get("title")
                and "women" not in str(member.get("title", "")).casefold()
            )

        # Category membership defines coverage. Fetch every men's page in one
        # bulk revision request; women's pages are outside FL26's player pool.
        page_titles = list(dict.fromkeys(page_titles))
        batches: list[list[Transfer]] = []
        if page_titles:
            try:
                payload = await _fetch_json(
                    session,
                    action="query",
                    prop="revisions",
                    rvprop="content",
                    rvslots="main",
                    titles="|".join(page_titles),
                )
                pages = payload.get("query", {}).get("pages", {})
                for page in pages.values():
                    title = str(page.get("title", ""))
                    revisions = page.get("revisions") or []
                    slot = revisions[0].get("slots", {}).get("main", {}) if revisions else {}
                    wikitext = slot.get("*") or slot.get("content") or ""
                    if title and wikitext:
                        batches.append(
                            parse_wikipedia_transfer_wikitext(
                                wikitext,
                                title,
                                start_date=start_date,
                                end_date=today,
                            )
                        )
            except Exception as exc:
                logger.warning("Wikipedia bulk transfer fetch unavailable: %s", exc)
    transfers = [transfer for batch in batches for transfer in batch]
    logger.info(
        "Wikipedia found %s effective transfers across %s seasonal pages",
        len(transfers),
        len(page_titles),
    )
    return transfers


def fetch_wikipedia_transfers(
    since_date: str | date | None = None,
    window: str = "auto",
) -> list[Transfer]:
    """Fetch recent confirmed transfers; failures are isolated to this source."""
    try:
        return asyncio.run(
            _fetch_wikipedia_transfers_async(since_date=since_date, window=window)
        )
    except Exception as exc:
        logger.warning("Wikipedia supplemental source unavailable: %s", exc)
        return []
