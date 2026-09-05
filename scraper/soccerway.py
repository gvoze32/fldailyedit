"""Soccerway team-transfer corroboration for explicitly supplied team pages."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date
from html.parser import HTMLParser
import logging
import re
from collections.abc import Iterable, Mapping

import aiohttp

from scraper.models import Transfer
from scraper.source_utils import date_in_range, parse_external_date, resolve_source_date_range


logger = logging.getLogger(__name__)

SOCCERWAY_TEAM_TRANSFERS_URL = "https://www.soccerway.com/team/{slug}/{team_id}/transfers/"
SOCCERWAY_HEADERS = {
    "User-Agent": "fldailyedit/0.1 (PES transfer updater; contact via project repository)",
    "Accept": "text/html, application/xhtml+xml;q=0.9, */*;q=0.5",
}
_VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}


@dataclass
class _SoccerwayRow:
    date_text: str = ""
    player_name: str = ""
    player_url: str = ""
    team_links: list[tuple[str, str]] = field(default_factory=list)
    direction: str = ""
    fee: str = ""
    fee_type: str = ""


class _SoccerwayParser(HTMLParser):
    """Extract the semantic transfer table from a rendered team page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[_SoccerwayRow] = []
        self._row: _SoccerwayRow | None = None
        self._row_depth = 0
        self._depth = 0
        self._capture_kind: str | None = None
        self._capture_tag: str | None = None
        self._capture_parts: list[str] = []
        self._capture_href = ""

    @staticmethod
    def _tokens(attrs: list[tuple[str, str | None]]) -> set[str]:
        classes = next((value for key, value in attrs if key == "class"), "")
        return set((classes or "").split())

    @staticmethod
    def _attr(attrs: list[tuple[str, str | None]], name: str) -> str:
        return next((value or "" for key, value in attrs if key == name), "")

    def _start_capture(
        self,
        kind: str,
        tag: str,
        *,
        href: str = "",
    ) -> None:
        if self._capture_kind is None:
            self._capture_kind = kind
            self._capture_tag = tag
            self._capture_parts = []
            self._capture_href = href

    def _finish_capture(self) -> None:
        if self._capture_kind is None:
            return
        value = " ".join("".join(self._capture_parts).split()).strip()
        kind = self._capture_kind
        if self._row is not None:
            if kind == "date":
                self._row.date_text = value
            elif kind == "player":
                self._row.player_name = value
                self._row.player_url = self._capture_href
            elif kind == "team":
                self._row.team_links.append((value, self._capture_href))
            elif kind == "fee":
                self._row.fee = value
            elif kind == "fee_type":
                self._row.fee_type = value
        self._capture_kind = None
        self._capture_tag = None
        self._capture_parts = []
        self._capture_href = ""

    def _finish_row(self) -> None:
        if self._row is not None:
            self.rows.append(self._row)
        self._row = None
        self._row_depth = 0
        self._finish_capture()

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self._depth += 1
        tokens = self._tokens(attrs)
        if (
            tag == "div"
            and "transferTab__row" in tokens
            and "transferTab__row--team" in tokens
            and "transferTab__row--main" not in tokens
        ):
            if self._row is not None:
                self._finish_row()
            self._row = _SoccerwayRow()
            self._row_depth = self._depth
        elif self._row is not None:
            if "transferTab__typeIcon--in" in tokens:
                self._row.direction = "in"
            elif "transferTab__typeIcon--out" in tokens:
                self._row.direction = "out"
            elif "transferTab__date" in tokens:
                self._start_capture("date", tag)
            elif tag == "a" and "transferTab__teamHref" in tokens:
                href = self._attr(attrs, "href")
                kind = "player" if not self._row.player_name else "team"
                self._start_capture(kind, tag, href=href)
            elif "transferTab__feePrizeType" in tokens:
                self._start_capture("fee_type", tag)
            elif "transferTab__feePrize" in tokens:
                self._start_capture("fee", tag)

        if tag in _VOID_TAGS:
            self._depth = max(0, self._depth - 1)

    def handle_data(self, data: str) -> None:
        if self._capture_kind is not None:
            self._capture_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._capture_tag == tag:
            self._finish_capture()
        if self._row is not None and tag == "div" and self._depth == self._row_depth:
            self._finish_row()
        self._depth = max(0, self._depth - 1)

    def close(self) -> None:
        super().close()
        self._finish_row()
        self._finish_capture()


def _clean(value: str) -> str:
    return " ".join((value or "").split()).strip()


def _transfer_type(fee_type: str, fee: str) -> tuple[str, bool]:
    normalized = _clean(f"{fee_type} {fee}").casefold()
    if "end of loan" in normalized or "return" in normalized:
        return "end of loan", False
    if "loan" in normalized:
        return "loan", True
    if "free" in normalized or "released" in normalized:
        return "free transfer", False
    return "transfer", False


def _team_name_from_url(source_url: str) -> str:
    match = re.search(r"/team/([^/]+)/[^/]+/transfers", source_url)
    if not match:
        return ""
    return match.group(1).replace("-", " ")


def _route(
    row: _SoccerwayRow,
    current_team: str,
    source_url: str,
) -> tuple[str, str] | None:
    current = _clean(current_team) or _team_name_from_url(source_url)
    links = []
    seen: set[str] = set()
    for name, href in row.team_links:
        clean = _clean(name)
        if clean and clean.casefold() not in seen:
            links.append((clean, href))
            seen.add(clean.casefold())
    if not current or not row.direction or not links:
        return None

    current_key = current.casefold()
    other = next((name for name, _ in links if name.casefold() != current_key), "")
    if not other:
        other = links[0][0]
    if row.direction == "in":
        return other, current
    return current, other


def parse_soccerway_transfer_html(
    html: str,
    team_name: str,
    source_url: str,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[Transfer]:
    """Parse one Soccerway team transfer tab as corroboration-only routes."""
    parser = _SoccerwayParser()
    try:
        parser.feed(html)
        parser.close()
    except (TypeError, ValueError):
        return []

    transfers: list[Transfer] = []
    seen: set[tuple[str, str, str, str]] = set()
    for row in parser.rows:
        event_date = parse_external_date(row.date_text)
        if not date_in_range(event_date, start_date, end_date):
            continue
        route = _route(row, team_name, source_url)
        if route is None or not row.player_name:
            continue
        transfer_type, is_loan = _transfer_type(row.fee_type, row.fee)
        from_club, to_club = route
        key = (
            _clean(row.player_name).casefold(),
            from_club.casefold(),
            to_club.casefold(),
            event_date.isoformat(),
        )
        if key in seen:
            continue
        seen.add(key)
        transfers.append(
            Transfer(
                player_name=_clean(row.player_name),
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


parse_soccerway_transfers = parse_soccerway_transfer_html


async def _fetch_text(session: aiohttp.ClientSession, url: str) -> str:
    async with session.get(url) as response:
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status}")
        return await response.text()


def _team_targets(
    team_urls: Mapping[str, str] | Iterable[tuple[str, str]] | None,
) -> list[tuple[str, str]]:
    if team_urls is None:
        return []
    if isinstance(team_urls, Mapping):
        return [(str(name), str(url)) for name, url in team_urls.items()]
    return [(str(name), str(url)) for name, url in team_urls]


async def _fetch_soccerway_transfers_async(
    since_date: str | date | None = None,
    *,
    window: str = "auto",
    ref_date: date | None = None,
    team_urls: Mapping[str, str] | Iterable[tuple[str, str]] | None = None,
) -> list[Transfer]:
    start_date, end_date = resolve_source_date_range(
        since_date, window, ref_date=ref_date
    )
    targets = _team_targets(team_urls)
    if not targets:
        logger.debug(
            "Soccerway supplemental source skipped: no team transfer URL mapping"
        )
        return []

    timeout = aiohttp.ClientTimeout(total=30)
    transfers: list[Transfer] = []
    async with aiohttp.ClientSession(
        headers=SOCCERWAY_HEADERS,
        timeout=timeout,
    ) as session:
        for team_name, source_url in targets:
            try:
                document = await _fetch_text(session, source_url)
                transfers.extend(
                    parse_soccerway_transfer_html(
                        document,
                        team_name,
                        source_url,
                        start_date=start_date,
                        end_date=end_date,
                    )
                )
            except Exception as exc:
                logger.warning(
                    "Soccerway team page unavailable (%s): %s", source_url, exc
                )

    unique: list[Transfer] = []
    seen: set[tuple[str, str, str, str]] = set()
    for transfer in transfers:
        key = (
            transfer.player_name.casefold(),
            transfer.from_club.casefold(),
            transfer.to_club.casefold(),
            transfer.date,
        )
        if key not in seen:
            seen.add(key)
            unique.append(transfer)
    logger.info("Soccerway found %s dated corroboration routes", len(unique))
    return unique


def fetch_soccerway_transfers(
    since_date: str | date | None = None,
    *,
    window: str = "auto",
    team_urls: Mapping[str, str] | Iterable[tuple[str, str]] | None = None,
) -> list[Transfer]:
    """Fetch configured Soccerway team pages without a global-feed assumption."""
    try:
        return asyncio.run(
            _fetch_soccerway_transfers_async(
                since_date=since_date,
                window=window,
                team_urls=team_urls,
            )
        )
    except Exception as exc:
        logger.warning("Soccerway supplemental source unavailable: %s", exc)
        return []
