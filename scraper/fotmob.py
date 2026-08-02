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
        return date(ref_date.year, 6, 1), None
    else:
        # Currently in or after winter window
        return date(ref_date.year, 1, 1), None


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

        start_date: Optional[date] = None
        end_date: Optional[date] = None

        if since_date:
            if isinstance(since_date, str):
                start_date = parse_iso_date(since_date)
            elif isinstance(since_date, date):
                start_date = since_date
        elif window and window != "all":
            start_date, end_date = get_transfer_window_range(window)

        if start_date:
            logger.info(f"Scraping FotMob transfers window: {start_date} to {end_date or 'latest'}")

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

                    item_date = parse_iso_date(t.date)
                    if item_date:
                        if end_date and item_date > end_date:
                            continue
                        if start_date and item_date < start_date:
                            reached_cutoff = True
                            continue

                    transfers.append(t)

                if reached_cutoff:
                    logger.info(f"Reached window start date {start_date} on page {page_num}. Stopping pagination.")
                    break

        return transfers

    def _parse_fotmob_item(self, item: dict, ignore_extensions: bool = True) -> Optional[Transfer]:
        """Parse a single raw FotMob transfer item into a Transfer dataclass."""
        # Check for contract extension
        is_extension = bool(item.get("contractExtension"))
        if ignore_extensions and is_extension:
            # Contract extensions are contract renewals at the same club, not transfers
            return None

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

        # Position extraction
        pos_obj = item.get("position")
        position = ""
        if isinstance(pos_obj, dict):
            position = (pos_obj.get("label") or "").strip().upper()
        elif isinstance(pos_obj, str):
            position = pos_obj.strip().upper()

        # Loan detection
        is_loan = bool(item.get("onLoan"))

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
        if "end of loan" in fee_lower or "return" in fee_lower or "back from loan" in fee_lower:
            transfer_type = "end of loan"
            is_loan = False
        elif is_loan or "loan" in fee_lower or "on loan" in fee_lower:
            transfer_type = "loan"
            is_loan = True
        elif "free" in fee_lower or from_club == "Free Agent" or to_club == "Free Agent":
            transfer_type = "free transfer"
        elif fee_text:
            transfer_type = "transfer"

        transfer_date = item.get("transferDate", "")
        market_val = item.get("marketValue") or 0
        try:
            market_val = int(market_val)
        except (ValueError, TypeError):
            market_val = 0

        from_club_id = item.get("fromClubId")
        to_club_id = item.get("toClubId")
        from_club_full = item.get("fromClubFullName") or from_club
        to_club_full = item.get("toClubFullName") or to_club

        return Transfer(
            player_name=player_name,
            from_club=from_club,
            to_club=to_club,
            date=transfer_date,
            transfer_type=transfer_type,
            fee=fee_text,
            league="",
            season="",
            position=position,
            is_loan=is_loan,
            is_contract_extension=is_extension,
            market_value=market_val,
            from_club_id_fotmob=from_club_id,
            to_club_id_fotmob=to_club_id,
            from_club_full_name=from_club_full,
            to_club_full_name=to_club_full,
        )

    def fetch_transfers(
        self,
        max_pages: int = 10,
        popular_only: bool = False,
        since_date: Optional[Union[str, date]] = None,
        window: str = "auto",
    ) -> list[Transfer]:
        """Synchronous wrapper to fetch global transfers."""
        return asyncio.run(
            self._fetch_transfers_async(
                max_pages=max_pages,
                popular_only=popular_only,
                since_date=since_date,
                window=window,
            )
        )

    async def _fetch_club_data_async(
        self,
        session: aiohttp.ClientSession,
        team_id: int,
    ) -> Optional[dict]:
        """Fetch raw team data JSON from FotMob API."""
        url = f"https://www.fotmob.com/api/data/teams?id={team_id}"
        try:
            async with session.get(url) as resp:
                if resp.status == 200:
                    return await resp.json(content_type=None)
                logger.warning(f"FotMob team API {team_id} returned HTTP {resp.status}")
        except Exception as e:
            logger.error(f"Error fetching FotMob team {team_id}: {e}")
        return None

    async def fetch_club_transfers_async(
        self,
        team_id: int,
        since_date: Optional[Union[str, date]] = None,
        window: str = "auto",
    ) -> list[Transfer]:
        """Fetch all verified transfers (in & out) for a specific club."""
        start_date: Optional[date] = None
        end_date: Optional[date] = None

        if since_date:
            if isinstance(since_date, str):
                start_date = parse_iso_date(since_date)
            elif isinstance(since_date, date):
                start_date = since_date
        elif window and window != "all":
            start_date, end_date = get_transfer_window_range(window)

        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(headers=self.headers, timeout=timeout) as session:
            data = await self._fetch_club_data_async(session, team_id)
            if not data:
                return []

            return self._extract_transfers_from_team_data(data, start_date, end_date)

    def _extract_transfers_from_team_data(
        self,
        data: dict,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> list[Transfer]:
        """Extract Transfer objects from FotMob team API payload."""
        results: list[Transfer] = []
        transfers_section = data.get("transfers", {})
        if not isinstance(transfers_section, dict):
            return results

        transfers_data = transfers_section.get("data", {})
        if not isinstance(transfers_data, dict):
            return results

        for category in ("Players in", "Players out"):
            items = transfers_data.get(category, [])
            if not isinstance(items, list):
                continue

            for raw_item in items:
                t = self._parse_fotmob_item(raw_item)
                if not t:
                    continue

                item_date = parse_iso_date(t.date)
                if item_date:
                    if end_date and item_date > end_date:
                        continue
                    if start_date and item_date < start_date:
                        continue

                results.append(t)

        return results

    def _extract_squad_from_team_data(self, data: dict, team_id: int, team_name: str) -> list[Transfer]:
        """Extract current squad members to sync real shirt numbers."""
        results: list[Transfer] = []
        try:
            squad_sections = data.get("squad", {}).get("squad", [])
            for section in squad_sections:
                members = section.get("members", [])
                for m in members:
                    name = m.get("name")
                    shirt = m.get("shirtNumber")
                    if not name or not shirt:
                        continue
                    
                    pos = m.get("role", {}).get("fallback", "")
                    
                    results.append(Transfer(
                        player_name=name.strip(),
                        from_club=team_name,
                        to_club=team_name,
                        transfer_type="squad_update",
                        shirt_number=int(shirt),
                        position=pos,
                        to_club_id_fotmob=team_id,
                        from_club_id_fotmob=team_id,
                    ))
        except Exception as e:
            logger.debug(f"Failed to parse squad data: {e}")
            
        return results

    async def _fetch_club_manager_async(self, team_id: int) -> Optional[str]:
        """Fetch current head coach/manager name for a club."""
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(headers=self.headers, timeout=timeout) as session:
            data = await self._fetch_club_data_async(session, team_id)
            if not data:
                return None

            coach_hist = data.get("overview", {}).get("coachHistory", [])
            if coach_hist and isinstance(coach_hist, list):
                latest = coach_hist[-1]
                if isinstance(latest, dict) and latest.get("name"):
                    return latest["name"].strip()
        return None

    async def fetch_major_clubs_transfers_safely_async(
        self,
        since_date: Optional[Union[str, date]] = None,
        window: str = "auto",
    ) -> list[Transfer]:
        """Fetch transfers for all major global clubs sequentially with a delay to avoid rate limits."""
        start_date: Optional[date] = None
        end_date: Optional[date] = None

        if since_date:
            if isinstance(since_date, str):
                start_date = parse_iso_date(since_date)
            elif isinstance(since_date, date):
                start_date = since_date
        elif window and window != "all":
            start_date, end_date = get_transfer_window_range(window)

        all_transfers: list[Transfer] = []
        timeout = aiohttp.ClientTimeout(total=15)
        
        clubs_to_sweep = get_deep_sweep_clubs()
        total_clubs = len(clubs_to_sweep)
        
        async with aiohttp.ClientSession(headers=self.headers, timeout=timeout) as session:
            for i, (club_name, tid) in enumerate(clubs_to_sweep.items(), 1):
                logger.info(f"Sweeping {club_name} (ID: {tid}) [{i}/{total_clubs}]...")
                try:
                    data = await self._fetch_club_data_async(session, tid)
                    if data:
                        # 1. Extract Transfers
                        club_transfers = self._extract_transfers_from_team_data(data, start_date, end_date)
                        all_transfers.extend(club_transfers)
                        
                        # 2. Extract Squad (Shirt Numbers)
                        club_squad = self._extract_squad_from_team_data(data, tid, club_name)
                        all_transfers.extend(club_squad)
                except Exception as e:
                    logger.warning(f"Failed to sweep {club_name}: {e}")
                
                # Sleep to prevent Cloudflare ban
                await asyncio.sleep(0.5)

        return merge_transfers([all_transfers])




def get_deep_sweep_clubs() -> dict[str, int]:
    import json
    from pathlib import Path
    
    clubs = {}
    
    # 1. Try to load data/major_clubs.json to override/prioritize
    major_path = Path("data/major_clubs.json")
    if major_path.exists():
        try:
            with open(major_path, "r", encoding="utf-8") as f:
                major_teams = json.load(f)
            clubs.update(major_teams)
        except Exception as e:
            logger.warning(f"Failed to load major_clubs.json: {e}")
            
    # 3. Load the rest of the teams from crawler
    json_path = Path("data/fotmob_teams.json")
    if json_path.exists():
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                teams = json.load(f)
            for t in teams:
                name = t.get("name") or t.get("slug", "Unknown")
                # Do not overwrite if it already exists in priority clubs (preserves order)
                if name not in clubs and t["fotmob_id"] not in clubs.values():
                    clubs[name] = t["fotmob_id"]
        except Exception as e:
            logger.warning(f"Failed to load fotmob_teams.json: {e}")
            
    return clubs


def merge_transfers(transfer_lists: list[list[Transfer]]) -> list[Transfer]:
    """
    Merge multiple transfer lists and deduplicate by player, from_club, to_club, and date.
    Preserves richer metadata when duplicate entries exist.
    """
    seen: dict[tuple[str, str, str, str], Transfer] = {}

    for t_list in transfer_lists:
        for t in t_list:
            key = (
                t.player_name.strip().lower(),
                t.from_club.strip().lower(),
                t.to_club.strip().lower(),
                (t.date or "").split("T")[0],
            )

            if key not in seen:
                seen[key] = t
            else:
                existing = seen[key]
                # Upgrade with position/fee/shirt if existing is empty
                if not existing.position and t.position:
                    existing.position = t.position
                if not existing.fee and t.fee:
                    existing.fee = t.fee
                if not existing.shirt_number and t.shirt_number:
                    existing.shirt_number = t.shirt_number
                if not existing.nationality and t.nationality:
                    existing.nationality = t.nationality
                if not existing.age and t.age:
                    existing.age = t.age

    return list(seen.values())


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


def fetch_major_clubs_transfers_safely(
    since_date: Optional[Union[str, date]] = None,
    window: str = "auto",
) -> list[Transfer]:
    """Deep sweep of transfers and squad data from all Major Global clubs."""
    scraper = FotmobScraper()
    return asyncio.run(
        scraper.fetch_major_clubs_transfers_safely_async(
            since_date=since_date,
            window=window,
        )
    )


def fetch_transfers_for_club_names(
    club_names: list[str],
    since_date: Optional[Union[str, date]] = None,
    window: str = "auto",
) -> list[Transfer]:
    """Fetch transfers for specific club names or FotMob IDs."""
    team_ids: list[int] = []
    for name in club_names:
        clean = name.strip()
        if clean.isdigit():
            team_ids.append(int(clean))
        else:
            # Look up in available clubs
            matched_id = None
            available_clubs = get_deep_sweep_clubs()
            for club, cid in available_clubs.items():
                if clean.lower() in club.lower() or club.lower() in clean.lower():
                    matched_id = cid
                    break
            if matched_id:
                team_ids.append(matched_id)
            else:
                logger.warning(f"Could not find FotMob ID for club '{clean}'")

    if not team_ids:
        logger.warning("No valid club IDs found to fetch")
        return []

    # Since fetch_transfers_for_clubs_async was removed, we just reuse the sequential logic for a subset
    scraper = FotmobScraper()
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    if since_date:
        if isinstance(since_date, str):
            start_date = parse_iso_date(since_date)
        elif isinstance(since_date, date):
            start_date = since_date
    elif window and window != "all":
        start_date, end_date = get_transfer_window_range(window)

    all_t = []
    async def fetch_subset():
        async with aiohttp.ClientSession(headers=scraper.headers, timeout=aiohttp.ClientTimeout(total=15)) as sess:
            for tid in team_ids:
                data = await scraper._fetch_club_data_async(sess, tid)
                if data:
                    all_t.extend(scraper._extract_transfers_from_team_data(data, start_date, end_date))
                    # We can also extract squad for specific club filter
                    all_t.extend(scraper._extract_squad_from_team_data(data, tid, ""))
    asyncio.run(fetch_subset())
    return merge_transfers([all_t])
