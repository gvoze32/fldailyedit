"""
FotMob Transfer Scraper module.

Scrapes live, verified football transfer data directly from FotMob's API
using direct lightweight async HTTP requests with automatic transfer window
detection and date filtering.
"""
import asyncio
from calendar import monthrange
from datetime import date, datetime, timezone
import json
import logging
import unicodedata
from typing import Optional, Union
import aiohttp

import config
from scraper.models import Transfer

logger = logging.getLogger(__name__)

FOTMOB_TRANSFERS_URL = "https://www.fotmob.com/transfers"
FOTMOB_API_TEMPLATE = "https://www.fotmob.com/api/data/transfers?orderBy=lastModified&page={page}&minFeeCurrency=EUR&popular={popular}"
AUTO_PAGE_LIMIT = 250

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.fotmob.com/transfers",
}


class IncompleteScrapeError(RuntimeError):
    """Raised when a transfer source fails before a complete snapshot is read."""


def get_transfer_window_range(
    window: str = "auto",
    ref_date: Optional[date] = None,
) -> tuple[date, Optional[date]]:
    """
    Get the (start_date, end_date) for a football transfer window.

    - "summer": June 1 to Sept 30
    - "winter": Jan 1 to Feb 28/29
    - "auto": Full effective transfer history available from FotMob
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
        return date(year, 1, 1), date(year, 2, monthrange(year, 2)[1])

    if w != "auto":
        raise ValueError(f"Unknown transfer window: {window!r}")

    # Replay all effective history so a clean rebuild and an incremental run
    # observe the same lifecycle chain across changing transfer windows.
    return date(2000, 1, 1), None


def parse_iso_date(date_str: str) -> Optional[date]:
    """Parse an ISO date/timestamp string into a date object."""
    if not date_str:
        return None
    try:
        clean_str = date_str.split("T")[0].split(" ")[0]
        return datetime.strptime(clean_str, "%Y-%m-%d").date()
    except Exception:
        return None


def parse_iso_datetime(date_str: str) -> Optional[datetime]:
    """Parse an ISO date/timestamp while preserving same-day event order."""
    if not date_str:
        return None
    try:
        value = date_str.strip().replace("Z", "+00:00")
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        parsed_date = parse_iso_date(date_str)
        if parsed_date is None:
            return None
        return datetime.combine(parsed_date, datetime.min.time(), tzinfo=timezone.utc)


def _optional_positive_int(value) -> Optional[int]:
    """Normalize an external identifier without letting malformed API data abort a scrape."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _resolve_date_range(
    since_date: Optional[Union[str, date]],
    window: str,
    ref_date: Optional[date] = None,
) -> tuple[Optional[date], Optional[date]]:
    """Resolve date filters, rejecting invalid and not-yet-effective events."""
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
        raise TypeError("since_date must be a date, datetime, ISO date string, or None")

    if window and window.lower() != "all":
        start_date, window_end = get_transfer_window_range(window, ref_date=today)
        return start_date, min(window_end, today) if window_end else today
    return None, today


def _normalize_key_text(value: str) -> str:
    """Normalize human-readable names for deterministic deduplication."""
    decomposed = unicodedata.normalize("NFKD", value or "")
    ascii_text = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(ascii_text.casefold().split())


class FotmobScraper:
    """Scraper for football transfer data from FotMob."""

    def __init__(self, headers: dict | None = None):
        self.headers = headers or DEFAULT_HEADERS

    async def _fetch_transfers_async(
        self,
        max_pages: Optional[int] = None,
        popular_only: bool = False,
        since_date: Optional[Union[str, date]] = None,
        window: str = "auto",
    ) -> list[Transfer]:
        """
        Fetch transfers asynchronously from FotMob API.

        Pages are fetched until the feed is empty or repeats. The internal hard
        limit prevents a broken endpoint from looping forever. The endpoint is
        ordered by last modification, not transfer date, so an old item is not
        a safe pagination stop signal.
        """
        transfers: list[Transfer] = []
        popular_param = "true" if popular_only else "false"

        start_date, end_date = _resolve_date_range(since_date, window)

        if start_date:
            logger.info(f"Scraping FotMob transfers window: {start_date} to {end_date or 'latest'}")

        page_limit = max_pages if max_pages is not None else AUTO_PAGE_LIMIT
        seen_pages: set[str] = set()
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(headers=self.headers, timeout=timeout) as session:
            for page_num in range(1, page_limit + 1):
                api_url = FOTMOB_API_TEMPLATE.format(page=page_num, popular=popular_param)
                logger.debug(f"Fetching FotMob API page {page_num}: {api_url}")

                try:
                    async with session.get(api_url) as resp:
                        if resp.status != 200:
                            raise IncompleteScrapeError(
                                f"FotMob page {page_num} returned HTTP {resp.status}"
                            )
                        data = await resp.json(content_type=None)
                except IncompleteScrapeError:
                    raise
                except Exception as e:
                    raise IncompleteScrapeError(
                        f"Failed to fetch FotMob page {page_num}: {e}"
                    ) from e

                if not isinstance(data, dict):
                    raise IncompleteScrapeError(
                        f"FotMob page {page_num} returned a non-object JSON payload"
                    )
                raw_transfers = data.get("transfers", [])
                if not isinstance(raw_transfers, list):
                    raise IncompleteScrapeError(
                        f"FotMob page {page_num} returned an invalid transfers field"
                    )
                if not raw_transfers:
                    logger.info(f"FotMob page {page_num} returned empty transfers list. Stopping.")
                    break

                page_signature = repr(raw_transfers)
                if page_signature in seen_pages:
                    logger.warning(
                        "FotMob page %s repeated an earlier page. Stopping pagination.",
                        page_num,
                    )
                    break
                seen_pages.add(page_signature)

                logger.info(f"FotMob page {page_num} returned {len(raw_transfers)} items")

                eligible_items = 0
                parsed_items = 0
                malformed_items = 0
                for item in raw_transfers:
                    if not isinstance(item, dict):
                        malformed_items += 1
                        continue
                    if item.get("contractExtension"):
                        continue
                    eligible_items += 1
                    t = self._parse_fotmob_item(item)
                    if not t:
                        malformed_items += 1
                        continue
                    parsed_items += 1

                    item_date = parse_iso_date(t.date)
                    if (start_date or end_date) and item_date is None:
                        logger.warning(
                            "Skipping undated transfer inside a bounded window: %s (%s -> %s)",
                            t.player_name,
                            t.from_club,
                            t.to_club,
                        )
                        continue
                    if item_date:
                        if end_date and item_date > end_date:
                            continue
                        if start_date and item_date < start_date:
                            continue

                    transfers.append(t)

                if eligible_items and parsed_items == 0:
                    raise IncompleteScrapeError(
                        f"FotMob page {page_num} contained {eligible_items} transfer "
                        "items but none matched the expected schema"
                    )
                if malformed_items > max(5, len(raw_transfers) // 2):
                    raise IncompleteScrapeError(
                        f"FotMob page {page_num} was mostly malformed "
                        f"({malformed_items}/{len(raw_transfers)} items)"
                    )
            else:
                raise IncompleteScrapeError(
                    f"FotMob pagination reached safety limit ({page_limit} pages) "
                    "before an empty or repeated page"
                )

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
        player_id_fotmob = _optional_positive_int(item.get("playerId"))
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
            player_id_fotmob=player_id_fotmob,
        )

    def fetch_transfers(
        self,
        max_pages: Optional[int] = None,
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
    ) -> dict:
        """Fetch raw team data JSON from FotMob API."""
        url = f"https://www.fotmob.com/api/data/teams?id={team_id}"
        try:
            async with session.get(url) as resp:
                if resp.status != 200:
                    raise IncompleteScrapeError(
                        f"FotMob team API {team_id} returned HTTP {resp.status}"
                    )
                data = await resp.json(content_type=None)
                if not isinstance(data, dict):
                    raise IncompleteScrapeError(
                        f"FotMob team API {team_id} returned a non-object JSON payload"
                    )
                return data
        except IncompleteScrapeError:
            raise
        except Exception as e:
            raise IncompleteScrapeError(
                f"Error fetching FotMob team {team_id}: {e}"
            ) from e

    async def fetch_club_transfers_async(
        self,
        team_id: int,
        since_date: Optional[Union[str, date]] = None,
        window: str = "auto",
    ) -> list[Transfer]:
        """Fetch all verified transfers (in & out) for a specific club."""
        start_date, end_date = _resolve_date_range(since_date, window)

        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(headers=self.headers, timeout=timeout) as session:
            data = await self._fetch_club_data_async(session, team_id)
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
                if (start_date or end_date) and item_date is None:
                    logger.warning(
                        "Skipping undated club transfer inside a bounded window: %s (%s -> %s)",
                        t.player_name,
                        t.from_club,
                        t.to_club,
                    )
                    continue
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
        if not team_name.strip():
            logger.warning("Skipping squad sync for FotMob team %s without a team name", team_id)
            return results

        squad = data.get("squad", {})
        squad_sections = squad.get("squad", []) if isinstance(squad, dict) else []
        if not isinstance(squad_sections, list):
            return results

        for section in squad_sections:
            if not isinstance(section, dict):
                continue
            members = section.get("members", [])
            if not isinstance(members, list):
                continue
            for member in members:
                if not isinstance(member, dict):
                    continue
                name = str(member.get("name") or "").strip()
                shirt = member.get("shirtNumber")
                if not name or shirt in (None, ""):
                    continue
                try:
                    shirt_number = int(shirt)
                except (TypeError, ValueError):
                    logger.debug("Ignoring invalid shirt number %r for %s", shirt, name)
                    continue
                if not 1 <= shirt_number <= 99:
                    logger.debug("Ignoring out-of-range shirt number %r for %s", shirt, name)
                    continue

                role = member.get("role")
                position = role.get("fallback", "") if isinstance(role, dict) else ""
                player_id_fotmob = _optional_positive_int(member.get("id"))
                results.append(Transfer(
                    player_name=name,
                    from_club=team_name,
                    to_club=team_name,
                    transfer_type="shirt_number_update",
                    shirt_number=shirt_number,
                    position=position,
                    to_club_id_fotmob=team_id,
                    from_club_id_fotmob=team_id,
                    player_id_fotmob=player_id_fotmob,
                ))

        return results

    async def fetch_major_clubs_transfers_safely_async(
        self,
        since_date: Optional[Union[str, date]] = None,
        window: str = "auto",
    ) -> list[Transfer]:
        """Fetch transfers for all major global clubs sequentially with a delay to avoid rate limits."""
        start_date, end_date = _resolve_date_range(since_date, window)

        all_transfers: list[Transfer] = []
        timeout = aiohttp.ClientTimeout(total=15)
        
        deep_clubs = get_deep_clubs()
        total_clubs = len(deep_clubs)
        if total_clubs == 0:
            raise IncompleteScrapeError("Deep-club index is empty")
        
        async with aiohttp.ClientSession(headers=self.headers, timeout=timeout) as session:
            for i, (club_name, tid) in enumerate(deep_clubs.items(), 1):
                logger.info(f"Deep fetching {club_name} (ID: {tid}) [{i}/{total_clubs}]...")
                try:
                    data = await self._fetch_club_data_async(session, tid)
                except IncompleteScrapeError as e:
                    raise IncompleteScrapeError(
                        f"Deep scrape incomplete at {club_name} ({tid}): {e}"
                    ) from e

                # 1. Extract Transfers
                club_transfers = self._extract_transfers_from_team_data(data, start_date, end_date)
                all_transfers.extend(club_transfers)

                # 2. Extract Squad (Shirt Numbers)
                club_squad = self._extract_squad_from_team_data(data, tid, club_name)
                all_transfers.extend(club_squad)
                
                # Sleep to prevent Cloudflare ban
                await asyncio.sleep(0.5)

        return merge_transfers([all_transfers])




def get_deep_clubs() -> dict[str, int]:
    """Load deep-scrape clubs from project-relative, validated data files."""
    clubs: dict[str, int] = {}
    
    # 1. Try to load data/major_clubs.json to override/prioritize
    major_path = config.DATA_DIR / "major_clubs.json"
    if major_path.exists():
        try:
            with open(major_path, "r", encoding="utf-8") as f:
                major_teams = json.load(f)
            if not isinstance(major_teams, dict):
                raise ValueError("expected a JSON object")
            parsed_major = {
                str(name): int(team_id)
                for name, team_id in major_teams.items()
            }
            clubs.update(parsed_major)
        except Exception as e:
            raise IncompleteScrapeError(f"Failed to load {major_path}: {e}") from e
            
    # 2. Try to load the validated FotMob teams (filtered by PES overlap)
    json_path = config.DATA_DIR / "fotmob_teams_validated.json"
    if json_path.exists():
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                teams = json.load(f)
            if not isinstance(teams, list):
                raise ValueError("expected a JSON array")
            parsed_teams: list[tuple[str, int]] = []
            for t in teams:
                if not isinstance(t, dict) or "fotmob_id" not in t:
                    raise ValueError("team entry is missing fotmob_id")
                name = t.get("name") or t.get("slug", "Unknown")
                parsed_teams.append((str(name), int(t["fotmob_id"])))
            for name, team_id in parsed_teams:
                # Do not overwrite if it already exists in priority clubs (preserves order)
                if name not in clubs and team_id not in clubs.values():
                    clubs[name] = team_id
        except Exception as e:
            raise IncompleteScrapeError(f"Failed to load {json_path}: {e}") from e
            
    return clubs


def _resolve_club_targets(
    club_names: list[str],
    available_clubs: dict[str, int],
) -> list[tuple[str, int]]:
    """Resolve requested club names without arbitrary first-substring matches."""
    targets: list[tuple[str, int]] = []
    seen_ids: set[int] = set()
    normalized = [(_normalize_key_text(name), name, int(team_id)) for name, team_id in available_clubs.items()]

    for requested in club_names:
        clean = requested.strip()
        if not clean:
            continue

        if clean.isdigit():
            team_id = int(clean)
            known_name = next((name for name, cid in available_clubs.items() if int(cid) == team_id), clean)
        else:
            query = _normalize_key_text(clean)
            exact = [(name, team_id) for norm, name, team_id in normalized if norm == query]
            if len(exact) == 1:
                known_name, team_id = exact[0]
            else:
                partial = [
                    (name, candidate_id)
                    for norm, name, candidate_id in normalized
                    if query and (query in norm or norm in query)
                ]
                if len(partial) != 1:
                    reason = "ambiguous" if partial else "not found"
                    logger.warning("Club %r is %s in the FotMob club index; skipping", clean, reason)
                    continue
                known_name, team_id = partial[0]

        if team_id not in seen_ids:
            targets.append((known_name, team_id))
            seen_ids.add(team_id)

    return targets


def _payload_team_name(data: dict, fallback: str) -> str:
    """Extract the canonical team name from a FotMob team response."""
    details = data.get("details")
    if isinstance(details, dict) and details.get("name"):
        return str(details["name"]).strip()
    if data.get("name"):
        return str(data["name"]).strip()
    return fallback.strip()


def merge_transfers(transfer_lists: list[list[Transfer]]) -> list[Transfer]:
    """
    Merge multiple transfer lists and deduplicate by player, from_club, to_club, and date.
    Preserves richer metadata when duplicate entries exist.
    """
    all_transfers = [transfer for group in transfer_lists for transfer in group]
    player_ids_by_name: dict[str, set[int]] = {}
    for transfer in all_transfers:
        if transfer.player_id_fotmob is not None:
            player_ids_by_name.setdefault(
                _normalize_key_text(transfer.player_name), set()
            ).add(int(transfer.player_id_fotmob))
    canonical_player_ids = {
        name: next(iter(player_ids))
        for name, player_ids in player_ids_by_name.items()
        if len(player_ids) == 1
    }
    club_ids_by_name: dict[str, set[int]] = {}
    for transfer in all_transfers:
        for club_id, short_name, full_name in (
            (
                transfer.from_club_id_fotmob,
                transfer.from_club,
                transfer.from_club_full_name,
            ),
            (
                transfer.to_club_id_fotmob,
                transfer.to_club,
                transfer.to_club_full_name,
            ),
        ):
            if club_id is None:
                continue
            for club_name in (short_name, full_name):
                normalized = _normalize_key_text(club_name)
                if normalized:
                    club_ids_by_name.setdefault(normalized, set()).add(club_id)

    canonical_club_ids = {
        name: next(iter(ids))
        for name, ids in club_ids_by_name.items()
        if len(ids) == 1
    }

    def club_key(club_id: int | None, short_name: str, full_name: str) -> str:
        if club_id is not None:
            return f"id:{club_id}"
        for club_name in (full_name, short_name):
            normalized = _normalize_key_text(club_name)
            inferred_id = canonical_club_ids.get(normalized)
            if inferred_id is not None:
                return f"id:{inferred_id}"
        return f"name:{_normalize_key_text(full_name or short_name)}"

    def player_key(transfer: Transfer) -> str:
        if transfer.player_id_fotmob is not None:
            return f"id:{int(transfer.player_id_fotmob)}"
        normalized = _normalize_key_text(transfer.player_name)
        inferred_id = canonical_player_ids.get(normalized)
        return f"id:{inferred_id}" if inferred_id is not None else f"name:{normalized}"

    seen: dict[tuple[str, ...], Transfer] = {}

    for t in all_transfers:
        from_key = club_key(
            t.from_club_id_fotmob, t.from_club, t.from_club_full_name
        )
        to_key = club_key(
            t.to_club_id_fotmob, t.to_club, t.to_club_full_name
        )
        key = (
            player_key(t),
            from_key,
            to_key,
            (t.date or "").split("T")[0],
            "shirt_number_update"
            if t.transfer_type == "shirt_number_update"
            else "transfer_event",
        )

        existing = seen.get(key)
        if (
            existing is not None
            and existing.transfer_type != t.transfer_type
            and existing.transfer_type != "transfer"
            and t.transfer_type != "transfer"
        ):
            # Two explicitly different lifecycle events can legitimately share
            # a player, route, and effective date. Do not collapse (for example)
            # an end-of-loan and a new loan into one record.
            key = (*key, t.transfer_type)
            existing = seen.get(key)

        if existing is None:
            seen[key] = t
        else:
            # Upgrade every missing field from the richer duplicate.
            for attr in (
                "position", "fee", "shirt_number", "nationality", "age",
                "market_value", "from_club_id_fotmob", "to_club_id_fotmob",
                "from_club_full_name", "to_club_full_name",
                "player_id_fotmob", "player_id_sortitoutsi",
            ):
                if not getattr(existing, attr) and getattr(t, attr):
                    setattr(existing, attr, getattr(t, attr))
            existing.sources = tuple(dict.fromkeys((*existing.sources, *t.sources)))
            existing.source_urls = tuple(
                dict.fromkeys((*existing.source_urls, *t.source_urls))
            )
            existing.proof_urls = tuple(
                dict.fromkeys((*existing.proof_urls, *t.proof_urls))
            )
            if "T" not in (existing.date or "") and "T" in (t.date or ""):
                existing.date = t.date
            if existing.transfer_type == "transfer" and t.transfer_type != "transfer":
                existing.transfer_type = t.transfer_type
                existing.is_loan = t.is_loan

    return list(seen.values())


def fetch_fotmob_transfers(
    max_pages: Optional[int] = None,
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
    """Deep fetch of transfers and squad data from all Major Global clubs."""
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
    requested = {name.strip().casefold() for name in club_names if name.strip()}
    targets = _resolve_club_targets(club_names, get_deep_clubs())
    if not targets or len(targets) < len(requested):
        resolved = ", ".join(name for name, _ in targets) or "none"
        raise IncompleteScrapeError(
            f"Could not resolve every requested club safely (resolved: {resolved})"
        )

    scraper = FotmobScraper()
    start_date, end_date = _resolve_date_range(since_date, window)

    all_t: list[Transfer] = []

    async def fetch_subset():
        async with aiohttp.ClientSession(headers=scraper.headers, timeout=aiohttp.ClientTimeout(total=15)) as sess:
            for requested_name, tid in targets:
                data = await scraper._fetch_club_data_async(sess, tid)
                if data:
                    all_t.extend(scraper._extract_transfers_from_team_data(data, start_date, end_date))
                    team_name = _payload_team_name(data, requested_name)
                    all_t.extend(scraper._extract_squad_from_team_data(data, tid, team_name))

    asyncio.run(fetch_subset())
    return merge_transfers([all_t])
