"""Fast, moderated transfer signals from Sortitoutsi's public activity page."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
import logging
import re

import aiohttp

from scraper.fotmob import parse_iso_date
from scraper.models import Transfer


logger = logging.getLogger(__name__)

SORTITOUTSI_READER_URL = (
    "https://r.jina.ai/http://sortitoutsi.net/football-manager-data-update"
)
SORTITOUTSI_HEADERS = {
    "User-Agent": "fldailyedit/0.1 (PES transfer updater; contact via project repository)",
    "Accept": "text/html, text/markdown;q=0.9, */*;q=0.5",
}

_STATUS_RE = re.compile(
    r"(?m)^\s*(Enabled|Pending|Disabled|Unconfirmed|Wrong Proof|Duplicate|Spam)\s*$"
)
_PERSON_LINK_RE = re.compile(
    r"\[([^\[\]]+?)\]\(https?://sortitoutsi\.net/football-manager-data-update/"
    r"person/(\d+)(?:\s+\"[^\"]*\")?\)"
)
_TEAM_LINK_RE = re.compile(
    r"\[([^\[\]]+?)\]\(https?://sortitoutsi\.net/football-manager-data-update/"
    r"team/(\d+)(?:\s+\"[^\"]*\")?\)"
)
_SUBMISSION_RE = re.compile(
    r"\[([^\]]+)\]\((https?://sortitoutsi\.net/football-manager-data-update/"
    r"submission/\d+)\)"
)
_PROOF_RE = re.compile(r"\[Proof \([^\]]+\)\]\((https?://[^)]+)\)", re.IGNORECASE)


def _parse_human_date(value: str) -> date | None:
    clean = re.sub(r"(?<=\d)(?:st|nd|rd|th)\b", "", " ".join(value.split()))
    for fmt in ("%d %B %Y", "%d %b %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(clean, fmt).date()
        except ValueError:
            continue
    return None


def _effective_date(block: str) -> date | None:
    start_match = re.search(
        r"starting on (\d{1,2}(?:st|nd|rd|th)? [A-Za-z]+ \d{4})",
        block,
        re.IGNORECASE,
    )
    if start_match:
        parsed = _parse_human_date(start_match.group(1))
        if parsed:
            return parsed
    return None


def _submission_date(block: str) -> date | None:
    submission = _SUBMISSION_RE.search(block)
    if not submission:
        return None
    timestamp = submission.group(1)
    match = re.match(r"(\d{1,2} [A-Za-z]+ \d{4})", timestamp)
    return _parse_human_date(match.group(1)) if match else None


def parse_sortitoutsi_markdown(
    markdown: str,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[Transfer]:
    """Parse only moderated ``Enabled`` transfer/loan submissions."""
    marker = "Recent Submissions"
    marker_index = markdown.find(marker)
    if marker_index < 0:
        return []
    section = markdown[marker_index:]
    status_matches = list(_STATUS_RE.finditer(section))
    transfers: list[Transfer] = []

    for index, status_match in enumerate(status_matches):
        if status_match.group(1) != "Enabled":
            continue
        block_end = (
            status_matches[index + 1].start()
            if index + 1 < len(status_matches)
            else len(section)
        )
        block = section[status_match.end() : block_end]
        people = _PERSON_LINK_RE.findall(block)
        if not people:
            continue
        player_name, player_id_text = people[0]
        submission_match = _SUBMISSION_RE.search(block)
        proof_match = _PROOF_RE.search(block)
        if not submission_match or not proof_match:
            # Enabled without traceable evidence is useful as a human hint, but
            # not safe enough for an automated roster mutation.
            continue

        effective_date = _effective_date(block)
        event_date = effective_date or _submission_date(block)
        if event_date is None:
            continue
        if start_date and event_date < start_date:
            continue
        if end_date and event_date > end_date:
            continue

        event_type = ""
        is_loan = False
        to_club = ""
        if " is now on loan to " in block:
            event_type = "loan"
            is_loan = True
        elif " has been transferred to " in block:
            event_type = "transfer"
        elif re.search(r"\bReleased\b.+? on a free transfer", block, re.DOTALL):
            event_type = "free transfer"
            to_club = "Free Agent"
        else:
            continue

        if event_type in {"loan", "transfer"}:
            teams = _TEAM_LINK_RE.findall(block)
            if not teams:
                continue
            to_club = teams[0][0]

        transfers.append(
            Transfer(
                player_name=player_name.strip(),
                from_club="",
                to_club=to_club.strip(),
                date=event_date.isoformat(),
                transfer_type=event_type,
                is_loan=is_loan,
                sources=("sortitoutsi",),
                source_urls=(submission_match.group(2),),
                proof_urls=(proof_match.group(1),),
                player_id_sortitoutsi=int(player_id_text),
                verification_status="enabled",
                infer_from_current_roster=effective_date is not None,
            )
        )
    return transfers


async def _fetch_text(session: aiohttp.ClientSession, url: str) -> str:
    async with session.get(url) as response:
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status}")
        return await response.text()


async def _fetch_sortitoutsi_transfers_async(
    since_date: str | date | None = None,
    *,
    ref_date: date | None = None,
) -> list[Transfer]:
    today = ref_date or datetime.now(timezone.utc).date()
    start_date = parse_iso_date(since_date) if isinstance(since_date, str) else since_date
    if isinstance(start_date, datetime):
        start_date = start_date.date()
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(headers=SORTITOUTSI_HEADERS, timeout=timeout) as session:
        # Sortitoutsi rejects non-browser clients. The public reader endpoint
        # preserves moderation labels, proof links, and submission URLs while
        # avoiding browser automation in GitHub Actions.
        content = await _fetch_text(session, SORTITOUTSI_READER_URL)

    transfers = parse_sortitoutsi_markdown(
        content, start_date=start_date, end_date=today
    )
    logger.info("Sortitoutsi found %s enabled transfer signals", len(transfers))
    return transfers


def fetch_sortitoutsi_transfers(
    since_date: str | date | None = None,
) -> list[Transfer]:
    """Fetch the latest enabled signals without failing the main pipeline."""
    try:
        return asyncio.run(_fetch_sortitoutsi_transfers_async(since_date=since_date))
    except Exception as exc:
        logger.warning("Sortitoutsi supplemental source unavailable: %s", exc)
        return []
