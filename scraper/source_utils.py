"""Shared date and filtering helpers for optional transfer sources."""

from __future__ import annotations

from datetime import date, datetime, timezone
import html
import re
from typing import Any

from scraper.fotmob import get_transfer_window_range, parse_iso_date


_ORDINAL_RE = re.compile(r"(?<=\d)(?:st|nd|rd|th)\b", re.IGNORECASE)


def resolve_source_date_range(
    since_date: str | date | datetime | None,
    window: str = "auto",
    *,
    ref_date: date | None = None,
) -> tuple[date | None, date | None]:
    """Resolve the same lower/upper bounds used by the primary FotMob feed."""
    today = ref_date or datetime.now(timezone.utc).date()
    if since_date:
        if isinstance(since_date, datetime):
            return since_date.date(), today
        if isinstance(since_date, date):
            return since_date, today
        if isinstance(since_date, str):
            parsed = parse_iso_date(since_date)
            if parsed is None:
                raise ValueError(
                    f"Invalid since_date {since_date!r}; expected YYYY-MM-DD"
                )
            return parsed, today
        raise TypeError(
            "since_date must be a date, datetime, ISO date string, or None"
        )

    if (window or "auto").casefold() != "all":
        start_date, window_end = get_transfer_window_range(window, ref_date=today)
        return start_date, min(window_end, today) if window_end else today
    return None, today


def parse_external_date(value: Any) -> date | None:
    """Parse common HTML/API date forms without accepting malformed values."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        timestamp = float(value)
        if timestamp > 1_000_000_000_000:
            timestamp /= 1000
        try:
            return datetime.fromtimestamp(timestamp, tz=timezone.utc).date()
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str):
        return None

    clean = html.unescape(" ".join(value.split())).strip()
    if not clean:
        return None
    clean = _ORDINAL_RE.sub("", clean)
    try:
        return datetime.fromisoformat(clean.replace("Z", "+00:00")).date()
    except ValueError:
        pass

    for fmt in (
        "%d.%m.%Y",
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%d %B %Y",
        "%d %b %Y",
        "%B %d, %Y",
        "%b %d, %Y",
    ):
        try:
            return datetime.strptime(clean, fmt).date()
        except ValueError:
            continue
    return None


def date_in_range(
    event_date: date | None,
    start_date: date | None,
    end_date: date | None,
) -> bool:
    """Return whether an event date fits an inclusive source range."""
    if event_date is None:
        return False
    return not (
        (start_date is not None and event_date < start_date)
        or (end_date is not None and event_date > end_date)
    )
