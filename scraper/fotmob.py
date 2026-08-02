"""
FotMob Transfer Scraper module.

Scrapes live, verified football transfer data directly from FotMob's API
using direct lightweight async HTTP requests.
"""
import asyncio
import logging
from typing import Optional
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


class FotmobScraper:
    """Scraper for football transfer data from FotMob."""

    def __init__(self, headers: dict | None = None):
        self.headers = headers or DEFAULT_HEADERS

    async def _fetch_transfers_async(
        self,
        max_pages: int = 2,
        popular_only: bool = False,
    ) -> list[Transfer]:
        """Fetch transfers asynchronously from FotMob API."""
        transfers: list[Transfer] = []
        popular_param = "true" if popular_only else "false"

        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(headers=self.headers, timeout=timeout) as session:
            for page_num in range(1, max_pages + 1):
                api_url = FOTMOB_API_TEMPLATE.format(page=page_num, popular=popular_param)
                logger.debug(f"Fetching FotMob API page {page_num}: {api_url}")

                try:
                    async with session.get(api_url) as resp:
                        if resp.status != 200:
                            logger.warning(f"FotMob page {page_num} returned HTTP {resp.status}")
                            continue
                        data = await resp.json(content_type=None)
                except Exception as e:
                    logger.error(f"Failed to fetch FotMob page {page_num}: {e}")
                    continue

                raw_transfers = data.get("transfers", [])
                logger.info(f"FotMob page {page_num} returned {len(raw_transfers)} items")

                for item in raw_transfers:
                    t = self._parse_fotmob_item(item)
                    if t:
                        transfers.append(t)

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
        max_pages: int = 2,
        popular_only: bool = False,
    ) -> list[Transfer]:
        """Synchronous wrapper to fetch transfers."""
        return asyncio.run(self._fetch_transfers_async(max_pages=max_pages, popular_only=popular_only))


def fetch_fotmob_transfers(max_pages: int = 2, popular_only: bool = False) -> list[Transfer]:
    """Convenience function to fetch transfers from FotMob."""
    scraper = FotmobScraper()
    return scraper.fetch_transfers(max_pages=max_pages, popular_only=popular_only)
