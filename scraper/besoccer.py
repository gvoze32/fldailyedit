"""Best-effort BeSoccer transfer corroboration from the public transfer page."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date
from html.parser import HTMLParser
import logging
import re

import aiohttp

from scraper.models import Transfer
from scraper.source_utils import date_in_range, parse_external_date, resolve_source_date_range


logger = logging.getLogger(__name__)

BESOCCER_TRANSFERS_URL = "https://www.besoccer.com/transfers"
BESOCCER_HEADERS = {
    "User-Agent": "fldailyedit/0.1 (PES transfer updater; contact via project repository)",
    "Accept": "text/html, text/plain;q=0.9, */*;q=0.5",
}


@dataclass
class _BeSoccerRow:
    date_text: str = ""
    player_name: str = ""
    player_url: str = ""
    player_alt: str = ""
    action: str = ""
    position: str = ""
    club_names: list[str] = field(default_factory=list)
    fee: str = ""
    is_rumour: bool = False


class _BeSoccerParser(HTMLParser):
    """Extract semantic transfer rows without depending on a DOM package."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[_BeSoccerRow] = []
        self._panel_date = ""
        self._row: _BeSoccerRow | None = None
        self._row_depth = 0
        self._depth = 0
        self._capture_kind: str | None = None
        self._capture_tag: str | None = None
        self._capture_parts: list[str] = []
        self._rumour_depth = 0
        self._scope_stack: list[tuple[str, bool]] = []

    @staticmethod
    def _tokens(attrs: list[tuple[str, str | None]]) -> set[str]:
        classes = next((value for key, value in attrs if key == "class"), "")
        return set((classes or "").split())

    @staticmethod
    def _attr(attrs: list[tuple[str, str | None]], name: str) -> str:
        return next((value or "" for key, value in attrs if key == name), "")

    def _start_capture(self, kind: str, tag: str) -> None:
        if self._capture_kind is None:
            self._capture_kind = kind
            self._capture_tag = tag
            self._capture_parts = []

    def _finish_capture(self) -> None:
        if self._capture_kind is None:
            return
        value = " ".join("".join(self._capture_parts).split())
        kind = self._capture_kind
        if kind == "date":
            self._panel_date = value
        elif self._row is not None:
            if kind == "player" and not self._row.player_name:
                self._row.player_name = value
            elif kind == "action":
                self._row.action = value
            elif kind == "position":
                self._row.position = value
            elif kind == "fee":
                self._row.fee = value
        self._capture_kind = None
        self._capture_tag = None
        self._capture_parts = []

    def _finish_row(self) -> None:
        self._finish_capture()
        if self._row is not None:
            self.rows.append(self._row)
        self._row = None
        self._row_depth = 0

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self._depth += 1
        tokens = self._tokens(attrs)
        element_id = self._attr(attrs, "id").casefold()
        is_rumour_scope = (
            "rumour" in element_id
            or "rumor" in element_id
            or any(
                "rumour" in token.casefold() or "rumor" in token.casefold()
                for token in tokens
            )
        )
        self._scope_stack.append((tag, is_rumour_scope))
        if is_rumour_scope:
            self._rumour_depth += 1

        if "panel-title" in tokens:
            self._start_capture("date", tag)
        if "sign-list" in tokens:
            if self._row is not None:
                self._finish_row()
            self._row_depth = self._depth
            self._row = _BeSoccerRow(
                date_text=self._panel_date,
                is_rumour=self._rumour_depth > 0,
            )
        elif self._row is not None:
            if tag == "a" and "item-box" in tokens and not self._row.player_url:
                self._row.player_url = self._attr(attrs, "href")
            elif tag == "img" and "shield" in tokens:
                club_name = self._attr(attrs, "alt")
                if club_name:
                    self._row.club_names.append(club_name)
            elif tag == "img" and "player" in tokens:
                self._row.player_alt = self._attr(attrs, "alt")
            elif "bold" in tokens and not self._row.player_name:
                self._start_capture("player", tag)
            elif "action" in tokens:
                self._start_capture("action", tag)
            elif any(token in tokens for token in ("player-role", "role")):
                self._start_capture("position", tag)
            elif "money" in tokens:
                self._start_capture("fee", tag)

        if tag in {
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
        }:
            _, is_rumour_scope = self._scope_stack.pop()
            if is_rumour_scope:
                self._rumour_depth = max(0, self._rumour_depth - 1)
            self._depth = max(0, self._depth - 1)

    def handle_data(self, data: str) -> None:
        if self._capture_kind is not None:
            self._capture_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._capture_tag == tag:
            self._finish_capture()
        if self._row is not None and tag == "li" and self._depth == self._row_depth:
            self._finish_row()
        if self._scope_stack:
            _, is_rumour_scope = self._scope_stack.pop()
            if is_rumour_scope:
                self._rumour_depth = max(0, self._rumour_depth - 1)
        self._depth = max(0, self._depth - 1)

    def close(self) -> None:
        super().close()
        self._finish_row()
        self._finish_capture()


def _clean(value: str) -> str:
    return " ".join((value or "").split()).strip()


def _transfer_type(action: str) -> tuple[str, bool] | None:
    normalized = _clean(action).casefold()
    if not normalized:
        return None
    if "renew" in normalized or "promot" in normalized or "retir" in normalized:
        return None
    if "end of loan" in normalized or ("return" in normalized and "loan" in normalized):
        return "end of loan", False
    if "loan" in normalized:
        return "loan", True
    if "free" in normalized or "release" in normalized:
        return "free transfer", False
    if "transfer" in normalized:
        return "transfer", False
    return None


def _unique_clubs(names: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for name in names:
        clean = _clean(name)
        key = clean.casefold()
        if clean and key not in seen:
            result.append(clean)
            seen.add(key)
    return result


def _route(row: _BeSoccerRow, transfer_type: str) -> tuple[str, str] | None:
    clubs = _unique_clubs(row.club_names)
    if len(clubs) >= 2:
        return clubs[0], clubs[1]
    if len(clubs) != 1 or transfer_type != "free transfer":
        return None
    action = row.action.casefold()
    if "release" in action:
        return clubs[0], "Free Agent"
    return "Free Agent", clubs[0]


def _player_name(row: _BeSoccerRow) -> str:
    if row.player_name:
        return _clean(row.player_name)
    alt = _clean(row.player_alt)
    return re.sub(r"^(?:transfer|loan|free agent)\s+", "", alt, flags=re.IGNORECASE)


def parse_besoccer_transfer_html(
    html: str,
    source_url: str = BESOCCER_TRANSFERS_URL,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[Transfer]:
    """Parse dated official BeSoccer rows as corroboration-only transfers."""
    parser = _BeSoccerParser()
    try:
        parser.feed(html)
        parser.close()
    except (TypeError, ValueError):
        return []

    transfers: list[Transfer] = []
    seen: set[tuple[str, str, str, str]] = set()
    for row in parser.rows:
        if row.is_rumour:
            continue
        event_date = parse_external_date(row.date_text or "")
        if not date_in_range(event_date, start_date, end_date):
            continue
        player_name = _player_name(row)
        typed = _transfer_type(row.action)
        if not player_name or typed is None:
            continue
        transfer_type, is_loan = typed
        route = _route(row, transfer_type)
        if route is None:
            continue
        from_club, to_club = route
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
                position=_clean(row.position),
                is_loan=is_loan,
                sources=("besoccer",),
                source_urls=(source_url,),
                verification_status="corroborator",
            )
        )
    return transfers


# Keep the short plural name available to callers that treat every adapter alike.
parse_besoccer_transfers = parse_besoccer_transfer_html


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
        content = await _fetch_text(session, source_url)
    transfers = parse_besoccer_transfer_html(
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
