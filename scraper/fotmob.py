"""
FotMob Transfer Scraper module.

Scrapes live, verified football transfer data directly from FotMob's API
using direct lightweight async HTTP requests with automatic transfer window
detection and date filtering.
"""
import asyncio
from datetime import date, datetime, timezone
import logging
from typing import Optional, Union
import aiohttp

from scraper.models import Transfer

logger = logging.getLogger(__name__)

FOTMOB_TRANSFERS_URL = "https://www.fotmob.com/transfers"
FOTMOB_API_TEMPLATE = "https://www.fotmob.com/api/data/transfers?orderBy=lastModified&page={page}&minFeeCurrency=EUR&popular={popular}"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.fotmob.com/transfers",
}


def get_transfer_window_range(
    window: str = "auto",
    ref_date: Optional[date] = None,
) -> tuple[date, Optional[date]]:
    """
    Get the (start_date, end_date) for a football transfer window.

    - "summer": June 1 to Sept 30
    - "winter": Jan 1 to Feb 28/29
    - "auto": Current or most recent active window based on ref_date
    - "all": No cutoff (start from 2000-01-01)
    """
    if ref_date is None:
        ref_date = datetime.now(timezone.utc).date()

    w = (window or "auto").lower()

    if w == "all":
        return date(2000, 1, 1), None

    if w == "summer":
        year = ref_date.year if ref_date.month >= 6 else ref_date.year - 1
        return date(year, 6, 1), date(year, 9, 30)

    if w == "winter":
        year = ref_date.year
        return date(year, 1, 1), date(year, 2, 28)

    # auto mode
    if ref_date.month >= 6:
        # Currently in or after summer window
        return date(ref_date.year, 6, 1), date(ref_date.year, 9, 30)
    else:
        # Currently in or after winter window
        return date(ref_date.year, 1, 1), date(ref_date.year, 2, 28)


def parse_iso_date(date_str: str) -> Optional[date]:
    """Parse an ISO date/timestamp string into a date object."""
    if not date_str:
        return None
    try:
        clean_str = date_str.split("T")[0].split(" ")[0]
        return datetime.strptime(clean_str, "%Y-%m-%d").date()
    except Exception:
        return None


class FotmobScraper:
    """Scraper for football transfer data from FotMob."""

    def __init__(self, headers: dict | None = None):
        self.headers = headers or DEFAULT_HEADERS

    async def _fetch_transfers_async(
        self,
        max_pages: int = 10,
        popular_only: bool = False,
        since_date: Optional[Union[str, date]] = None,
        window: str = "auto",
    ) -> list[Transfer]:
        """
        Fetch transfers asynchronously from FotMob API.

        If since_date or window is specified, automatically paginates until
        transfers older than the cutoff date are reached.
        """
        transfers: list[Transfer] = []
        popular_param = "true" if popular_only else "false"

        cutoff_date: Optional[date] = None
        if since_date:
            if isinstance(since_date, str):
                cutoff_date = parse_iso_date(since_date)
            elif isinstance(since_date, date):
                cutoff_date = since_date
        elif window and window != "all":
            cutoff_date, _ = get_transfer_window_range(window)

        if cutoff_date:
            logger.info(f"Scraping FotMob transfers with cutoff date since: {cutoff_date} (window='{window}')")

        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(headers=self.headers, timeout=timeout) as session:
            for page_num in range(1, max_pages + 1):
                api_url = FOTMOB_API_TEMPLATE.format(page=page_num, popular=popular_param)
                logger.debug(f"Fetching FotMob API page {page_num}: {api_url}")

                try:
                    async with session.get(api_url) as resp:
                        if resp.status != 200:
                            logger.warning(f"FotMob page {page_num} returned HTTP {resp.status}")
                            break
                        data = await resp.json(content_type=None)
                except Exception as e:
                    logger.error(f"Failed to fetch FotMob page {page_num}: {e}")
                    break

                raw_transfers = data.get("transfers", [])
                if not raw_transfers:
                    logger.info(f"FotMob page {page_num} returned empty transfers list. Stopping.")
                    break

                logger.info(f"FotMob page {page_num} returned {len(raw_transfers)} items")

                reached_cutoff = False
                for item in raw_transfers:
                    t = self._parse_fotmob_item(item)
                    if not t:
                        continue

                    # Check date cutoff
                    item_date = parse_iso_date(t.date)
                    if cutoff_date and item_date and item_date < cutoff_date:
                        reached_cutoff = True
                        continue

                    transfers.append(t)

                if reached_cutoff:
                    logger.info(f"Reached transfers older than cutoff {cutoff_date} on page {page_num}. Stopping.")
                    break

        return transfers

    def _parse_fotmob_item(self, item: dict) -> Optional[Transfer]:
        """Parse a single raw FotMob transfer item into a Transfer dataclass."""
        player_name = (item.get("name") or "").strip()
        from_club = (item.get("fromClub") or "").strip()
        to_club = (item.get("toClub") or "").strip()

        if not player_name or (not from_club and not to_club):
            return None

        # Normalize Free Agent representation
        if not from_club or from_club.lower() in ("free agent", "without club", "unattached"):
            from_club = "Free Agent"
        if not to_club or to_club.lower() in ("free agent", "without club", "unattached", "career break", "retired"):
            to_club = "Free Agent"

        # Transfer type & fee
        fee_obj = item.get("fee")
        fee_text = ""
        transfer_type = "transfer"

        if isinstance(fee_obj, dict):
            fee_text = fee_obj.get("feeText", "") or ""
            val = fee_obj.get("value")
            if val:
                fee_text = f"€{val:,.0f}" if isinstance(val, (int, float)) else str(val)
        elif isinstance(fee_obj, str):
            fee_text = fee_obj

        fee_lower = fee_text.lower()
        if "loan" in fee_lower or "on loan" in fee_lower:
            transfer_type = "loan"
        elif "free" in fee_lower or from_club == "Free Agent" or to_club == "Free Agent":
            transfer_type = "free transfer"
        elif fee_text:
            transfer_type = "transfer"

        transfer_date = item.get("transferDate", "")

        return Transfer(
            player_name=player_name,
            from_club=from_club,
            to_club=to_club,
            date=transfer_date,
            transfer_type=transfer_type,
            fee=fee_text,
            league="",
            season="",
        )

    def fetch_transfers(
        self,
        max_pages: int = 10,
        popular_only: bool = False,
        since_date: Optional[Union[str, date]] = None,
        window: str = "auto",
    ) -> list[Transfer]:
        """Synchronous wrapper to fetch transfers."""
        return asyncio.run(
            self._fetch_transfers_async(
                max_pages=max_pages,
                popular_only=popular_only,
                since_date=since_date,
                window=window,
            )
        )


def fetch_fotmob_transfers(
    max_pages: int = 10,
    popular_only: bool = False,
    since_date: Optional[Union[str, date]] = None,
    window: str = "auto",
) -> list[Transfer]:
    """Convenience function to fetch transfers from FotMob."""
    scraper = FotmobScraper()
    return scraper.fetch_transfers(
        max_pages=max_pages,
        popular_only=popular_only,
        since_date=since_date,
        window=window,
    )
