"""
Transfermarkt scraper — fetches transfer data using Crawlee (ParselCrawler).

Uses Crawlee for robust scraping:
- Automatic rate limiting and concurrency control
- Built-in retry with exponential backoff
- Anti-bot fingerprint handling via impit
- Parsel selectors (CSS/XPath, same as Scrapy)

Selectors adapted from dcaribou/transfermarkt-scraper reference project.
Uses .co.uk domain for better bot avoidance.
"""
import asyncio
import json
import logging
from typing import Optional

from crawlee import ConcurrencySettings
from crawlee.crawlers import ParselCrawler, ParselCrawlingContext
from crawlee.configuration import Configuration

from scraper.models import Transfer
import config

logger = logging.getLogger(__name__)

# Transfermarkt .co.uk tends to block less aggressively
BASE_URL = "https://www.transfermarkt.co.uk"


async def _scrape_league_transfers(
    league_url: str,
    league_name: str = "",
    season: str = "",
) -> list[Transfer]:
    """
    Scrape transfers for a league using Crawlee ParselCrawler.

    Args:
        league_url: Transfermarkt league transfers URL.
        league_name: Human-readable name for logging.
        season: Season label (e.g. "2025/26").

    Returns:
        List of Transfer objects.
    """
    transfers: list[Transfer] = []
    pages_scraped = 0

    crawler = ParselCrawler(
        concurrency_settings=ConcurrencySettings(
            max_concurrency=1,  # one request at a time to avoid blocks
            max_tasks_per_minute=15,  # ~4s between requests
        ),
        max_request_retries=3,
        configuration=Configuration(persist_storage=False),
    )

    @crawler.router.default_handler
    async def handle_page(context: ParselCrawlingContext) -> None:
        nonlocal pages_scraped
        pages_scraped += 1
        sel = context.selector
        page_url = context.request.url

        logger.info(f"Scraping {league_name or 'transfers'} page {pages_scraped}: {page_url}")

        # ── Parse club sections ──
        # Each club on the transfers page has a section with the club name
        # and two tables: Arrivals (Zugänge) and Departures (Abgänge)
        club_boxes = sel.css("div.box")

        for box in club_boxes:
            # Club name from header link
            club_link = box.css("a.vereinprofil_tooltip::text").get()
            if not club_link:
                # Try alternate selector
                club_link = box.css("div.table-header a::text").get()
            if not club_link:
                continue
            club_name = club_link.strip()

            # Find transfer tables in this club section
            tables = box.css("table.items")

            for table_idx, table in enumerate(tables):
                rows = table.css("tbody tr")

                for row in rows:
                    transfer = _parse_transfer_row(
                        row, club_name, table_idx, league_name, season
                    )
                    if transfer:
                        transfers.append(transfer)

        # ── Pagination ──
        next_link = sel.css(
            "li.tm-pagination__list-item--icon-next-page a::attr(href)"
        ).get()
        if not next_link:
            next_link = sel.css("a[title='Go to the next page']::attr(href)").get()

        if next_link:
            next_url = next_link if next_link.startswith("http") else BASE_URL + next_link
            await context.add_requests([next_url])

    await crawler.run([league_url])

    logger.info(f"Done: {len(transfers)} transfers from {league_name} ({pages_scraped} pages)")
    return transfers


def _parse_transfer_row(row, club_name: str, table_idx: int, league_name: str, season: str) -> Optional[Transfer]:
    """
    Parse a single transfer row using Parsel selectors.

    table_idx 0 = Arrivals (IN), table_idx 1 = Departures (OUT).
    """
    try:
        # Player name — use the hauptlink class (same as reference project)
        player_name = row.css("td.hauptlink a.spielprofil_tooltip::text").get()
        if not player_name:
            player_name = row.css("td.hauptlink a::text").get()
        if not player_name:
            # Fallback: try inline-table pattern from reference
            player_name = row.css("table.inline-table td.hauptlink a::text").get()
        if not player_name:
            return None
        player_name = player_name.strip()

        if not player_name or len(player_name) < 2:
            return None

        # Other club (where from / where to)
        # The other club has an img with class "tiny_wappen" or "vereinswappen"
        other_club = ""

        # Try the club column — usually has img with title attribute
        club_imgs = row.css("img.tiny_wappen")
        if not club_imgs:
            club_imgs = row.css("td.zentriert img[title]")

        for img in club_imgs:
            title = img.attrib.get("title", "").strip()
            if title and title != club_name and title != player_name:
                other_club = title
                break

        if not other_club:
            # Fallback: look for links in the last columns
            all_links = row.css("td a.vereinprofil_tooltip::text").getall()
            for link_text in all_links:
                text = link_text.strip()
                if text and text != club_name and text != player_name and len(text) > 2:
                    other_club = text
                    break

        if not other_club:
            return None

        # Determine from/to
        if table_idx == 0:  # Arrivals
            from_club = other_club
            to_club = club_name
        else:  # Departures
            from_club = club_name
            to_club = other_club

        # Transfer type
        transfer_type = "transfer"
        row_text = row.css("::text").getall()
        row_text_joined = " ".join(row_text).lower()

        if "loan" in row_text_joined:
            if "end of loan" in row_text_joined or "loan return" in row_text_joined:
                transfer_type = "end of loan"
            else:
                transfer_type = "loan"
        elif "free transfer" in row_text_joined or "free" in row_text_joined:
            transfer_type = "free transfer"

        # Fee
        fee = ""
        fee_cell = row.css("td.rechts a::text").get()
        if not fee_cell:
            fee_cell = row.css("td.rechts::text").get()
        if fee_cell:
            fee = fee_cell.strip()

        return Transfer(
            player_name=player_name,
            from_club=from_club,
            to_club=to_club,
            transfer_type=transfer_type,
            fee=fee,
            league=league_name,
            season=season,
        )

    except Exception as e:
        logger.debug(f"Failed to parse transfer row: {e}")
        return None


def fetch_league_transfers(
    league_url: str,
    league_name: str = "",
    season: str = "",
) -> list[Transfer]:
    """
    Synchronous wrapper for the async Crawlee scraper.

    Args:
        league_url: Transfermarkt league transfers URL.
        league_name: Human-readable league name.
        season: Season label.

    Returns:
        List of Transfer objects.
    """
    # Convert .com URLs to .co.uk for better bot avoidance
    league_url = league_url.replace(
        "www.transfermarkt.com", "www.transfermarkt.co.uk"
    )

    return asyncio.run(_scrape_league_transfers(league_url, league_name, season))


def fetch_all_league_transfers(leagues: list[dict]) -> list[Transfer]:
    """
    Fetch transfers from all configured leagues.

    Args:
        leagues: List of dicts with keys: url, name, season.

    Returns:
        Deduplicated list of all transfers.
    """
    all_transfers = []
    seen = set()

    for league in leagues:
        transfers = fetch_league_transfers(
            league_url=league["url"],
            season=league.get("season", ""),
            league_name=league.get("name", ""),
        )

        for t in transfers:
            key = (t.player_name.lower(), t.from_club.lower(), t.to_club.lower())
            if key not in seen:
                seen.add(key)
                all_transfers.append(t)

    return all_transfers


# ──────────────────────────────────────────────────────────────
# Legacy requests-based scraper (fallback if Crawlee has issues)
# ──────────────────────────────────────────────────────────────

class TransfermarktScraper:
    """
    Fallback scraper using requests + BeautifulSoup.
    Use this if Crawlee has compatibility issues on your platform.

    Usage:
        scraper = TransfermarktScraper()
        transfers = scraper.fetch_league_transfers(url, ...)
    """

    def __init__(self):
        import requests
        self.session = requests.Session()
        self.session.headers.update(config.REQUEST_HEADERS)

    def _fetch(self, url: str) -> Optional[str]:
        import random
        import time

        for attempt in range(1, config.MAX_RETRIES + 1):
            try:
                delay = random.uniform(*config.REQUEST_DELAY)
                time.sleep(delay)

                resp = self.session.get(url, timeout=15)
                if resp.status_code == 429:
                    wait = 2 ** attempt * 5
                    logger.warning(f"Rate limited (429), waiting {wait}s")
                    time.sleep(wait)
                    continue
                if resp.status_code == 403:
                    logger.error(f"Blocked (403): {url}")
                    return None
                resp.raise_for_status()
                return resp.text

            except Exception as e:
                wait = 2 ** attempt
                logger.warning(f"Request failed (attempt {attempt}): {e}")
                if attempt < config.MAX_RETRIES:
                    time.sleep(wait)

        return None

    def fetch_league_transfers(
        self,
        league_url: str,
        season: str = "",
        league_name: str = "",
    ) -> list[Transfer]:
        from bs4 import BeautifulSoup
        from urllib.parse import urljoin

        all_transfers = []
        url = league_url.replace("www.transfermarkt.com", "www.transfermarkt.co.uk")
        page = 1

        while url and page <= 20:
            logger.info(f"[fallback] Fetching {league_name} page {page}")
            html = self._fetch(url)
            if not html:
                break

            soup = BeautifulSoup(html, "lxml")

            # Parse club sections
            for box in soup.find_all("div", class_="box"):
                header = box.find("a", class_="vereinprofil_tooltip")
                if not header:
                    continue
                club_name = header.text.strip()

                for i, table in enumerate(box.find_all("table", class_="items")):
                    for row in table.find_all("tr", class_=["odd", "even"]):
                        player_link = row.find("a", class_="spielprofil_tooltip")
                        if not player_link:
                            continue
                        player_name = player_link.text.strip()
                        if not player_name:
                            continue

                        other_club = ""
                        for img in row.find_all("img", class_="tiny_wappen"):
                            title = img.get("title", "").strip()
                            if title and title != club_name:
                                other_club = title
                                break

                        if not other_club:
                            continue

                        if i == 0:
                            from_club, to_club = other_club, club_name
                        else:
                            from_club, to_club = club_name, other_club

                        transfer_type = "transfer"
                        row_text = row.get_text(separator=" ").lower()
                        if "loan" in row_text:
                            transfer_type = "loan" if "end of loan" not in row_text else "end of loan"
                        elif "free" in row_text:
                            transfer_type = "free transfer"

                        all_transfers.append(Transfer(
                            player_name=player_name,
                            from_club=from_club,
                            to_club=to_club,
                            transfer_type=transfer_type,
                            league=league_name,
                            season=season,
                        ))

            # Next page
            next_btn = soup.select_one("li.tm-pagination__list-item--icon-next-page > a")
            if next_btn and next_btn.get("href"):
                url = urljoin(BASE_URL, next_btn["href"])
            else:
                url = None
            page += 1

        return all_transfers
