from __future__ import annotations

import argparse
import contextlib
from collections import Counter
from dataclasses import dataclass
from typing import Callable, Iterable, Mapping, Sequence
from datetime import date, datetime, timedelta, timezone
import hashlib
import io
import json
import logging
from pathlib import Path
import config
import native_metadata
import transfer_planning as planning
from editor import backup as backup_mod
from editor import crypto
from editor.editfile import EditFile
from editor.roster import _game_plan_position_code, normalize_game_plan_formation
from editor.save_metadata import read_save_header
from editor import logger as transfer_logger
from editor.locking import EditFileLock
from editor.player_catalog import (
    PlayerCatalogError,
    legacy_name_matches_native,
    load_id_name_text,
    load_legacy_player_names,
)
from editor.release_policy import ReleasePolicyError, load_release_policy
from scraper.club_identity import (
    UNRESOLVED,
    ClubIdentityIndex,
    build_club_identity_index,
    load_fotmob_teams,
)
from scraper.fotmob import (
    IncompleteScrapeError,
    fetch_clubs_transfers_safely,
    fetch_fotmob_transfers,
    fetch_squads_for_club_ids,
    get_transfer_window_range,
    merge_transfers,
    parse_iso_datetime,
)
from scraper.tactics import fetch_fotmob_tactical_updates
from scraper.besoccer import fetch_besoccer_transfers
from scraper.matcher import NameMatcher
from scraper.sortitoutsi import fetch_sortitoutsi_transfers
from scraper.soccerway import fetch_soccerway_transfers
from scraper.sofascore import fetch_sofascore_transfers
from scraper.sources import reconcile_transfer_sources
from scraper.models import (
    CaptainUpdate,
    ScrapeResult,
    SquadMember,
    SquadSnapshot,
    TacticalUpdate,
    Transfer,
)
from scraper.wikipedia import fetch_wikipedia_transfers
from scraper.transfermarkt import fetch_transfermarkt_transfers
from local_update import (
    CancellationToken,
    LocalUpdateError,
    LocalUpdateRequest,
    LocalUpdateResult,
    LocalUpdateProgress,
    LocalUpdateService,
    LocalUpdateStage,
    ProgressCallback,
)

logger = logging.getLogger(__name__)
# Re-scan a little before the last applied run so events published late
# (backdated announcements) are still seen.
_SINCE_SAFETY_MARGIN = timedelta(days=7)
_NON_CLUB_NAMES = frozenset(
    {
        "",
        "career break",
        "free agent",
        "retired",
        "unattached",
        "without club",
    }
)

_SAFE_MUTATION_FAILURE_CODES = frozenset(
    {
        "same_team",
        "source_team_missing",
        "destination_team_missing",
        "source_player_missing",
        "destination_player_exists",
        "duplicate_club_registration",
        "overflow_not_authorized",
        "overflow_candidate_stale",
        "no_safe_overflow_candidate",
        "overflow_release_failed",
    }
)


@dataclass(frozen=True, slots=True)
class _PlannedCaptainUpdate:
    """Captain source record resolved to a local team and player."""

    source: CaptainUpdate
    team_id: int
    player_id: int
    matched_player_name: str
    confidence: float



def _sha256_file(path: Path) -> str:
    """Return a stable digest without loading a large EDIT file into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _save_club_names(edit_file: EditFile, club_ids: Iterable[int]) -> dict[int, str]:
    """Return display names for the selected save's playable clubs.

    The external FL26 team reference is tied to the external player
    reference; PES21 saves and saves without that catalog use their own
    team names.
    """
    is_pes21_save = bool(getattr(edit_file, "is_pes21_save", False))
    catalog_report = getattr(edit_file, "player_catalog_report", None)
    current_catalog_entries = (
        getattr(catalog_report, "current_entries", None)
        if catalog_report is not None
        else None
    )
    use_external_team_names = (
        not is_pes21_save
        and (current_catalog_entries is None or current_catalog_entries > 0)
    )
    current_team_names = (
        load_id_name_text(
            config.CURRENT_TEAMS_FILE,
            label="team",
            minimum_entries=700,
        )
        if use_external_team_names
        else {}
    )
    teams_info = edit_file.get_all_team_info()
    clubs = set(club_ids)
    return {
        team_id: current_team_names.get(team_id, team.name)
        for team_id, team in teams_info.items()
        if team_id in clubs
    }


def _load_club_identity(
    edit_file: EditFile,
    club_ids: Iterable[int],
    save_scope: str,
) -> ClubIdentityIndex:
    """Build the FotMob ↔ save club index for the selected save."""
    try:
        fotmob_teams = load_fotmob_teams()
    except (OSError, TypeError, ValueError) as exc:
        raise IncompleteScrapeError(
            f"Could not load FotMob club identity data: {exc}"
        ) from exc
    index = build_club_identity_index(
        _save_club_names(edit_file, club_ids),
        fotmob_teams,
        cache_path=config.CLUB_IDENTITY_CACHE_FILE,
        save_scope=save_scope,
    )
    print(f"  Club identity index: {len(index.entries())} save clubs bound to FotMob")
    return index


def _previous_window_start(today: date) -> date:
    """Opening date of the transfer window before the latest one to open.

    Windows open on Jan 1 (winter) and Jun 1 (summer). Rebuilds start from the
    base save, so the default lookback must still cover the previous summer
    window during January–May.
    """
    if today.month >= 6:
        return date(today.year, 1, 1)
    return date(today.year - 1, 6, 1)


def _default_transfer_since_date(
    window: str,
    since_date: str | None,
    save_since_date: str | None = None,
) -> str | None:
    """Avoid replaying stale history in default global runs."""
    if since_date is not None or (window or "auto").casefold() != "auto":
        return since_date
    if save_since_date is not None:
        return save_since_date
    return _previous_window_start(date.today()).isoformat()


def _pending_skipped_dates(save_scope: str) -> list[date]:
    """Event dates of relevant transfers the previous run left unapplied."""
    path = config.OUTPUT_DIR / transfer_logger.SKIPPED_TRANSFERS_FILENAME
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    dates: list[date] = []
    for line in lines:
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (
            not isinstance(item, dict)
            or item.get("save_scope") != save_scope
            or not item.get("relevant")
        ):
            continue
        parsed = parse_iso_datetime(str(item.get("date") or ""))
        if parsed is not None:
            dates.append(parsed.date())
    return dates


def _save_since_date(
    save_scope: str,
    *,
    include_legacy: bool,
    today: date | None = None,
) -> str:
    """Derive the automatic scrape cutoff from this save's own history.

    Starts a safety margin before the last applied change for the save scope,
    and never later than the oldest relevant event still pending from the
    previous run. Never earlier than the previous-window rule, which is also
    the fallback when the save has no applied history.
    """
    current = today or date.today()
    floor = _previous_window_start(current)
    applied: list[datetime] = []
    for entry in transfer_logger.read_log(
        save_scope=save_scope,
        include_legacy=include_legacy,
    ):
        if entry.get("dry_run"):
            continue
        parsed = parse_iso_datetime(str(entry.get("timestamp") or ""))
        if parsed is not None:
            applied.append(parsed)
    if not applied:
        return floor.isoformat()
    since = max(applied).date() - _SINCE_SAFETY_MARGIN
    pending = _pending_skipped_dates(save_scope)
    if pending:
        since = min(since, min(pending))
    return max(since, floor).isoformat()


def _positive_int(value) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


@dataclass(frozen=True, slots=True)
class _SaveScrapeContext:
    """Selected-save knowledge the scrape needs to target club squads."""

    club_identity: ClubIdentityIndex
    club_ids: frozenset[int]
    save_since_date: str | None = None

    def save_club(self, names: Sequence[str], fotmob_id) -> int | None | object:
        """Resolve one transfer side to a save club, None, or UNRESOLVED."""
        fotmob_team_id = _positive_int(fotmob_id)
        if fotmob_team_id is not None:
            bound = self.club_identity.pes_for_fotmob(fotmob_team_id)
            if bound is not None:
                return bound if bound in self.club_ids else None
        unsure = False
        for name in names:
            clean = (name or "").strip()
            if clean.casefold() in _NON_CLUB_NAMES:
                continue
            resolved = self.club_identity.resolve_name(clean)
            if resolved is UNRESOLVED:
                unsure = True
            elif resolved is not None and resolved in self.club_ids:
                return resolved
        return UNRESOLVED if unsure else None

    def touches_save(self, transfer) -> bool:
        """True when either side is (or may be) a club of the selected save."""
        return any(
            self.save_club(names, fotmob_id) is not None
            for names, fotmob_id in _transfer_sides(transfer)
        )


def _transfer_sides(transfer) -> tuple[tuple[tuple[str, ...], object], ...]:
    return (
        (
            (
                getattr(transfer, "to_club_full_name", "") or "",
                getattr(transfer, "to_club", "") or "",
            ),
            getattr(transfer, "to_club_id_fotmob", None),
        ),
        (
            (
                getattr(transfer, "from_club_full_name", "") or "",
                getattr(transfer, "from_club", "") or "",
            ),
            getattr(transfer, "from_club_id_fotmob", None),
        ),
    )


def _supplemental_target_clubs(transfer_batches) -> tuple[str, ...]:
    """Return relevant clubs used to filter supplemental transfer routes."""
    targets: list[str] = []
    seen: set[str] = set()
    for batch in transfer_batches:
        for transfer in batch:
            if not transfer.date:
                continue
            destination = (
                transfer.to_club_full_name or transfer.to_club
            ).strip()
            source = (
                transfer.from_club_full_name or transfer.from_club
            ).strip()
            target = (
                source
                if destination.casefold() in _NON_CLUB_NAMES
                else destination
            )
            key = target.casefold()
            if key and key not in _NON_CLUB_NAMES and key not in seen:
                seen.add(key)
                targets.append(target)
    return tuple(targets)


def _fast_squad_target_ids(
    transfer_batches,
    context: _SaveScrapeContext,
) -> tuple[int, ...]:
    """FotMob IDs of every save club touched by any merged transfer event.

    Unbound FotMob clubs whose name may be a save club are included too: their
    current squad is the evidence that binds them to a save club.
    """
    identity = context.club_identity
    targets: list[int] = []
    seen: set[int] = set()

    def add(fotmob_team_id: int | None) -> None:
        if fotmob_team_id is not None and fotmob_team_id not in seen:
            seen.add(fotmob_team_id)
            targets.append(fotmob_team_id)

    for batch in transfer_batches:
        for transfer in batch:
            for names, raw_fotmob_id in _transfer_sides(transfer):
                fotmob_team_id = _positive_int(raw_fotmob_id)
                resolved = context.save_club(names, fotmob_team_id)
                if resolved is None:
                    continue
                if fotmob_team_id is not None:
                    add(fotmob_team_id)
                elif resolved is not UNRESOLVED:
                    add(identity.fotmob_for_pes(resolved))
    return tuple(targets)


def _club_filter_fotmob_ids(
    clubs: Sequence[str],
    context: _SaveScrapeContext | None,
) -> tuple[int, ...]:
    """Resolve ``--club`` values (FotMob IDs or save club names) to FotMob IDs."""
    resolved_ids: list[int] = []
    unresolved: list[str] = []
    for club in clubs:
        if club.isdigit():
            resolved_ids.append(int(club))
            continue
        pes_team_id = (
            context.club_identity.resolve_name(club)
            if context is not None
            else None
        )
        fotmob_team_id = (
            context.club_identity.fotmob_for_pes(pes_team_id)
            if isinstance(pes_team_id, int)
            else None
        )
        if fotmob_team_id is None:
            unresolved.append(club)
        else:
            resolved_ids.append(fotmob_team_id)
    if unresolved:
        raise IncompleteScrapeError(
            "Could not resolve requested clubs to FotMob IDs for this save: "
            + ", ".join(unresolved)
            + " (use the FotMob team ID instead)"
        )
    return tuple(dict.fromkeys(resolved_ids))






_ROSTER_OBSERVATION_TYPES = frozenset(
    {"squad_registration", "shirt_number_update"}
)


def _scrape_transfer_events(batch) -> list:
    """Keep roster observations out of cross-source transfer reconciliation."""
    return [
        transfer
        for transfer in batch
        if getattr(transfer, "transfer_type", "") not in _ROSTER_OBSERVATION_TYPES
    ]


def _scrape_roster_updates(batch) -> tuple:
    """Read explicit roster updates, with compatibility for older scrape results."""
    explicit = tuple(getattr(batch, "roster_updates", ()))
    if explicit:
        return explicit
    return tuple(
        transfer
        for transfer in batch
        if getattr(transfer, "transfer_type", "") == "shirt_number_update"
    )


def _scrape_run_transfers(
    args,
    *,
    context: _SaveScrapeContext | None = None,
    progress: Callable[[str, int, int], None] | None = None,
):
    """Fetch, merge, order, and preview transfers for one pipeline run.

    ``context`` describes the selected save. Without it (a dry run with no
    save) squads cannot be targeted, so only provider transfer feeds run.
    """
    popular_only = bool(getattr(args, "popular", False))
    window = getattr(args, "window", "auto") or "auto"
    since_date = getattr(args, "since", None)
    club_filter = getattr(args, "club", None)
    deep_mode = bool(getattr(args, "deep", False))
    fotmob_only = bool(getattr(args, "fotmob_only", False))
    scrape_since_date = (
        since_date
        if club_filter
        else _default_transfer_since_date(
            window,
            since_date,
            context.save_since_date if context is not None else None,
        )
    )

    start_date, end_date = get_transfer_window_range(window)
    cutoff_info = (
        f"since {scrape_since_date}"
        if scrape_since_date
        else f"window '{window}' ({start_date} to {end_date or 'latest'})"
    )
    transfer_batches = []
    roster_updates = []
    captain_updates: list[CaptainUpdate] = []
    squad_snapshots = []
    pending_transfers: list[Transfer] = []

    def collect_club_batch(batch) -> None:
        transfer_batches.append(_scrape_transfer_events(batch))
        roster_updates.extend(_scrape_roster_updates(batch))
        squad_snapshots.extend(getattr(batch, "squad_snapshots", ()))
        captain_updates.extend(getattr(batch, "captain_updates", ()))
        pending_transfers.extend(getattr(batch, "pending_transfers", ()))

    if club_filter:
        clubs = [club.strip() for club in club_filter.split(",") if club.strip()]
        club_ids = _club_filter_fotmob_ids(clubs, context)
        print(
            f"\n🎯 Scraping club-focused transfers for: {', '.join(clubs)} "
            f"({cutoff_info})..."
        )
        collect_club_batch(
            fetch_clubs_transfers_safely(
                club_ids,
                since_date=since_date,
                window=window,
            )
        )
    elif deep_mode:
        if context is None:
            raise IncompleteScrapeError(
                "Deep mode needs the selected save to know which clubs to scrape"
            )
        deep_club_ids = sorted(
            {
                int(entry["fotmob_id"])
                for entry in context.club_identity.entries()
            }
        )
        if not deep_club_ids:
            raise IncompleteScrapeError(
                "No save club is bound to a FotMob club; deep mode has nothing to scrape"
            )
        print(
            "\n🌪️ Deep Mode: Scraping transfers and squads for "
            f"{len(deep_club_ids)} save clubs ({cutoff_info})..."
        )
        deep_batch = fetch_clubs_transfers_safely(
            deep_club_ids,
            since_date=scrape_since_date,
            window=window,
            progress=progress,
        )
        collect_club_batch(deep_batch)
        print(
            "  Deep captain sync found "
            f"{len(getattr(deep_batch, 'captain_updates', ()))} markers"
        )
        print(
            "\n📡 Adding Live Global Feed to catch other minor leagues "
            f"({cutoff_info}, automatic pagination)..."
        )
        live_transfers = fetch_fotmob_transfers(
            popular_only=popular_only,
            since_date=scrape_since_date,
            window=window,
        )
        transfer_batches.append(live_transfers)
        pending_transfers.extend(getattr(live_transfers, "pending_transfers", ()))
    else:
        print(
            f"\n⚡ Fast Mode: Scraping live transfers from FotMob "
            f"({cutoff_info}, automatic pagination)..."
        )
        live_transfers = fetch_fotmob_transfers(
            popular_only=popular_only,
            since_date=scrape_since_date,
            window=window,
        )
        transfer_batches.append(live_transfers)
        pending_transfers.extend(getattr(live_transfers, "pending_transfers", ()))
    fast_signals = []
    corroborators = []
    if not club_filter and not fotmob_only:
        print(
            "\n🌐 Adding confirmed Wikipedia transfer lists "
            f"({cutoff_info})..."
        )
        wikipedia_transfers = fetch_wikipedia_transfers(
            since_date=scrape_since_date,
            window=window,
        )
        wikipedia_events = [
            transfer for transfer in wikipedia_transfers if transfer.date
        ]
        wikipedia_corroborators = [
            transfer
            for transfer in wikipedia_transfers
            if not transfer.date
            and transfer.verification_status == "corroborator"
        ]
        transfer_batches.append(wikipedia_events)
        corroborators.extend(wikipedia_corroborators)
        print(
            f"  Wikipedia found {len(wikipedia_events)} dated transfers and "
            f"{len(wikipedia_corroborators)} undated route corroborators"
        )

        print("\n🚦 Adding moderated Sortitoutsi fast signals...")
        fast_signals = fetch_sortitoutsi_transfers(since_date=scrape_since_date)
        print(f"  Sortitoutsi found {len(fast_signals)} enabled signals")

        print("\n🔎 Adding verified Transfermarkt detailed transfers...")
        transfermarkt_events = fetch_transfermarkt_transfers(
            since_date=scrape_since_date or start_date,
        )
        transfer_batches.append(transfermarkt_events)
        print(
            f"  Transfermarkt found {len(transfermarkt_events)} dated transfers"
        )

        primary_target_clubs = _supplemental_target_clubs(transfer_batches[:1])

        print("\n🧭 Adding BeSoccer corroboration routes...")
        besoccer_corroborators = fetch_besoccer_transfers(
            since_date=scrape_since_date,
            window=window,
        )
        corroborators.extend(besoccer_corroborators)
        print(
            f"  BeSoccer found {len(besoccer_corroborators)} corroboration routes"
        )

        print("\n📊 Adding Sofascore corroboration routes...")
        sofascore_corroborators = fetch_sofascore_transfers(
            since_date=scrape_since_date,
            window=window,
            club_names=primary_target_clubs,
        )
        corroborators.extend(sofascore_corroborators)
        print(
            f"  Sofascore found {len(sofascore_corroborators)} corroboration routes"
        )

        print("\n🛣️ Adding Soccerway team-page corroboration routes...")
        # Both optional route sources only corroborate primary events.
        soccerway_clubs = primary_target_clubs
        soccerway_corroborators = fetch_soccerway_transfers(
            since_date=scrape_since_date,
            window=window,
            club_names=soccerway_clubs,
        )
        corroborators.extend(soccerway_corroborators)
        print(
            f"  Soccerway found {len(soccerway_corroborators)} corroboration routes"
        )

    if not club_filter and not deep_mode:
        _refresh_fast_squads(
            [*transfer_batches, fast_signals, pending_transfers],
            context,
            collect_club_batch,
        )

    transfers = (
        reconcile_transfer_sources(
            transfer_batches,
            fast_signals,
            corroborators,
        )
        if fast_signals or corroborators or len(transfer_batches) > 1
        else merge_transfers(transfer_batches)
    )
    # Apply historical moves oldest-to-newest. Current squad shirt-number
    # updates intentionally run last.
    transfers.sort(key=planning._transfer_sort_key)
    source_counts = Counter(
        source
        for transfer in transfers
        for source in transfer.sources
    )
    source_summary = ", ".join(
        f"{source}={count}" for source, count in sorted(source_counts.items())
    )
    print(f"  Reconciled sources: {source_summary or 'none'}")
    print(f"\nTotal unique transfers to process: {len(transfers)}")
    for transfer in transfers[:5]:
        print(f"  {transfer}")
    if len(transfers) > 5:
        print(f"  ... and {len(transfers) - 5} more")
    if pending_transfers:
        print(
            f"Provider events not yet effective or undated: {len(pending_transfers)}"
        )
    print(f"Current captain markers to process: {len(captain_updates)}")
    roster_updates = merge_transfers([roster_updates])
    print(f"Current roster updates to process: {len(roster_updates)}")
    tactical_updates = ()
    if squad_snapshots:
        print("\n📊 Preparing evidence-gated tactical settings from FotMob...")
        try:
            tactical_updates = fetch_fotmob_tactical_updates(squad_snapshots)
        except Exception as error:
            logger.warning(
                "Tactical stats unavailable; preserving saved game plans: %s",
                error,
            )
        print(
            "Current-season tactical settings to process: "
            f"{len(tactical_updates)}"
        )
    return ScrapeResult(
        transfers,
        captain_updates,
        squad_snapshots,
        roster_updates,
        tactical_updates=tactical_updates,
        pending_transfers=pending_transfers,
    )


def _refresh_fast_squads(
    transfer_batches,
    context: _SaveScrapeContext | None,
    collect_club_batch: Callable[[ScrapeResult], None],
) -> None:
    """Refresh current squads of every save club touched by any event."""
    if context is None:
        print(
            "\n👕 Fast Mode: no selected save; current squads are not refreshed."
        )
        return
    squad_targets = _fast_squad_target_ids(transfer_batches, context)
    if not squad_targets:
        return
    print(
        "\n👕 Fast Mode: Refreshing current squad membership, numbers, and captains for "
        f"{len(squad_targets)} save clubs touched by this run's transfers..."
    )
    try:
        squad_updates = fetch_squads_for_club_ids(squad_targets)
    except IncompleteScrapeError as error:
        logger.warning("Fast squad sync skipped: %s", error)
        return
    collect_club_batch(squad_updates)
    snapshots = getattr(squad_updates, "squad_snapshots", ())
    membership_updates = sum(len(snapshot.members) for snapshot in snapshots)
    shirt_updates = len(_scrape_roster_updates(squad_updates))
    print(
        f"  Squad sync found {len(snapshots)} squads, "
        f"{membership_updates} memberships and {shirt_updates} shirt numbers"
    )
    print(
        "  Captain sync found "
        f"{len(getattr(squad_updates, 'captain_updates', ()))} markers"
    )

def _load_match_database(
    edit_file: EditFile,
    release_policy_file: str | Path | None = None,
    *,
    game_root: str | Path | None = None,
):
    """Build roster-aware player and club indexes from one validated save."""
    selected_game_root = game_root
    if selected_game_root is None:
        selected_game_root = getattr(edit_file, "game_root", None)
    print("\n📋 Reading selected save database...")
    playerbin_database, playerbin_source = native_metadata._load_playerbin_database(
        game_root=selected_game_root
    )
    edit_file.playerbin_source = playerbin_source
    attach_playerbin = getattr(edit_file, "attach_playerbin", None)
    if playerbin_database is not None and callable(attach_playerbin):
        attach_playerbin(playerbin_database)
        print(f"  Loaded Player.bin metadata from {playerbin_source}")
    teambin_database, teambin_source = native_metadata._load_teambin_database(
        game_root=selected_game_root
    )
    edit_file.teambin_source = teambin_source
    if teambin_database is not None:
        edit_file.attach_teambin(teambin_database)
        print(f"  Loaded Team.bin metadata from {teambin_source}")
    assignment_database, assignment_source = (
        native_metadata._load_player_assignment_database(
            game_root=selected_game_root
        )
    )
    edit_file.player_assignment_source = assignment_source
    if assignment_database is not None:
        edit_file.attach_player_assignment(assignment_database)
        print(
            "  Loaded PlayerAssignment.bin metadata "
            f"from {assignment_source}"
        )
    players = edit_file.get_all_players()
    edit_file._player_cache = players
    is_pes21_save = bool(getattr(edit_file, "is_pes21_save", False))
    catalog_report = getattr(edit_file, "player_catalog_report", None)
    if is_pes21_save:
        missing_roster_ids = tuple(
            getattr(catalog_report, "missing_roster_ids", ()) or ()
        )
        if missing_roster_ids:
            source = getattr(edit_file, "playerbin_source", None) or "unavailable"
            sample = ", ".join(str(player_id) for player_id in missing_roster_ids[:8])
            root_hint = (
                f" Selected game root: {selected_game_root}."
                if selected_game_root is not None
                else (
                    " In Local Run, choose the PES 2021/T99 game folder "
                    "containing download/*.cpk."
                )
            )
            raise PlayerCatalogError(
                "PES 2021/T99 save requires matching native Player.bin metadata; "
                f"{len(missing_roster_ids)} roster IDs are missing "
                f"(source: {source}; first IDs: {sample}).{root_hint}"
            )
    try:
        release_policy = load_release_policy(release_policy_file)
    except ReleasePolicyError as exc:
        raise PlayerCatalogError(str(exc)) from exc
    attach_policy = getattr(edit_file, "attach_release_policy", None)
    if callable(attach_policy):
        attach_policy(release_policy)
    if release_policy.protected_players or release_policy.usage:
        print(
            "  Release policy: "
            f"{len(release_policy.protected_players)} protected clubs, "
            f"{len(release_policy.usage)} usage snapshots"
        )
    club_ids = edit_file.get_club_team_ids()
    all_rosters = edit_file.get_all_rosters()
    team_player_map = {
        team_id: roster.roster for team_id, roster in all_rosters.items()
    }
    current_catalog_entries = (
        getattr(catalog_report, "current_entries", None)
        if catalog_report is not None
        else None
    )
    team_name_to_id = {
        name: team_id
        for team_id, name in _save_club_names(edit_file, club_ids).items()
    }

    # T99 stores localized full names and often surname-only print names.
    # Reuse legacy English names only after native ID and display agreement;
    # never let a stale catalog invent a player outside the native roster.
    player_records = [
        (player.name, player_id) for player_id, player in players.items()
    ]
    native_aliases: dict[int, str] = {}
    native_playerbin = getattr(edit_file, "playerbin_db", None)
    if is_pes21_save and native_playerbin is not None:
        roster_ids = {
            player_id
            for roster in all_rosters.values()
            for player_id in roster.roster
        }
        legacy_names = load_legacy_player_names(
            config.PLAYERS_CSV_FILE,
            roster_ids,
        )
        for player_id, legacy_name in legacy_names.items():
            native_record = native_playerbin.get(player_id)
            if native_record is None or not legacy_name_matches_native(
                legacy_name,
                native_record.name,
                native_record.print_name,
            ):
                continue
            native_aliases[player_id] = legacy_name
            player_records.append((legacy_name, player_id))
        if native_aliases:
            print(
                "  Loaded "
                f"{len(native_aliases)} verified native player-name aliases"
            )

    matcher = NameMatcher()
    matcher.load_player_db(
        player_records,
        positions={
            player_id: player.position
            for player_id, player in players.items()
            if player.position
        },
        nationalities={
            player_id: player.nationality
            for player_id, player in players.items()
            if player.nationality
        },
        ages={
            player_id: player.age
            for player_id, player in players.items()
            if player.age
        },
    )
    matcher.load_team_db(team_name_to_id, clubs_only=False)
    if current_catalog_entries == 0:
        print(
            "  ⚠ External player catalog unavailable; using names from selected save"
        )
    print(
        f"  {len(players)} players, {len(team_name_to_id)} playable clubs "
        "(national teams excluded)"
    )
    return matcher, all_rosters, team_player_map, club_ids


def _match_and_plan_transfers(
    transfers,
    matcher,
    threshold,
    team_player_map,
    all_rosters,
    club_ids,
    edit_file,
    output_path,
    *,
    club_identity: ClubIdentityIndex | None,
    report: planning.PlanningReport,
    allow_overflow_release,
    allow_uncovered_source=False,
):
    """Match scraped identities, classify them, and create safe roster actions.

    Every event that cannot be applied is recorded on ``report.skipped``.
    """

    print(
        "\n🔍 Matching transfers with roster-aware identity verification "
        f"(threshold={threshold}%)..."
    )
    save_scope = str(output_path.resolve())
    historical_entries = transfer_logger.read_log(
        save_scope=save_scope,
        include_legacy=(output_path.resolve() == config.OUTPUT_FILE_PATH.resolve()),
    )
    player_names = _player_names(edit_file)
    planning_transfers = [
        *transfers,
        *getattr(transfers, "roster_updates", ()),
    ]
    team_shirt_numbers = {
        team_id: {
            player_id: shirt_number
            for player_id, shirt_number in zip(
                roster.player_ids,
                getattr(roster, "shirt_numbers", ()),
            )
            if player_id and shirt_number > 0
        }
        for team_id, roster in all_rosters.items()
    }

    matched = planning._match_transfers_statefully(
        planning_transfers,
        matcher,
        threshold,
        team_player_map,
        club_ids,
        historical_entries=historical_entries,
        club_identity=club_identity,
        squad_snapshots=getattr(transfers, "squad_snapshots", ()),
        fotmob_identity_map=getattr(transfers, "fotmob_identity_map", None),
        player_names=player_names,
        allow_uncovered_source=allow_uncovered_source,
        team_shirt_numbers=team_shirt_numbers,
        report=report,
    )
    matched, duplicate_shirt_matches = planning._dedupe_shirt_number_matches(matched)
    superseded_loan_sources = planning._build_superseded_loan_sources(
        matched,
        historical_entries=historical_entries,
    )
    if duplicate_shirt_matches:
        print(
            f"  ⚠ Skipped {duplicate_shirt_matches} duplicate or ambiguous "
            "shirt-number matches"
        )

    non_shirt = [
        match
        for match in matched
        if match.transfer.transfer_type != "shirt_number_update"
    ]
    fully_matched = [match for match in matched if match.is_fully_matched]
    partial = [match for match in matched if not match.is_fully_matched]
    roster_plan = planning._plan_roster_actions(
        matched,
        all_rosters,
        club_ids,
        edit_file,
        superseded_loan_sources,
        allow_overflow_release=allow_overflow_release,
        report=report,
    )
    print(
        f"  ✓ Fully actionable: {len(fully_matched)} "
        f"(Club Transfers: {sum(match.is_club_transfer for match in non_shirt)}, "
        f"Departures: {sum(match.is_release for match in non_shirt)}, "
        f"Signings: {sum(match.is_sign for match in non_shirt)}, "
        "Shirt Number Checks: "
        f"{sum(match.transfer.transfer_type == 'shirt_number_update' and match.is_fully_matched for match in matched)})"
    )
    print(f"  ✗ Unmatched: {len(partial)}")
    return roster_plan, fully_matched, save_scope


def _player_names(edit_file) -> dict[int, str]:
    return {
        player_id: player.name
        for player_id, player in getattr(edit_file, "_player_cache", {}).items()
        if getattr(player, "name", "")
    }


def _pending_skipped(
    pending_transfers: Iterable[Transfer],
    context: _SaveScrapeContext | None,
    today: date | None = None,
) -> list[planning.SkippedTransfer]:
    """Report provider events that were never applied because of their date."""
    current = today or datetime.now(timezone.utc).date()
    skipped: list[planning.SkippedTransfer] = []
    for transfer in pending_transfers:
        parsed = parse_iso_datetime(transfer.date or "")
        if not transfer.date or parsed is None:
            reason = "undated_in_window"
            detail = "Provider gives no date inside the bounded scrape window"
        elif parsed.date() > current:
            reason = "not_yet_effective"
            detail = f"Effective {parsed.date().isoformat()}; applied once it starts"
        else:
            reason = "outside_scrape_window"
            detail = "Dated outside this run's scrape window"
        skipped.append(
            planning.SkippedTransfer(
                player_name=transfer.player_name,
                from_team=transfer.from_club_full_name or transfer.from_club,
                to_team=transfer.to_club_full_name or transfer.to_club,
                date=transfer.date,
                source=",".join(transfer.sources),
                reason=reason,
                detail=detail,
                relevant=context is None or context.touches_save(transfer),
                fotmob_player_id=_positive_int(transfer.player_id_fotmob),
                candidates=(),
            )
        )
    return skipped


def _skipped_rows(
    skipped: Iterable[planning.SkippedTransfer],
    save_scope: str,
) -> tuple[dict, ...]:
    """Serialize skipped transfers, save-relevant rows first."""
    rows = [
        {**item.to_dict(), "save_scope": save_scope}
        for item in skipped
    ]
    rows.sort(key=lambda row: (not row["relevant"], row["reason"], row["date"]))
    return tuple(rows)

@dataclass(frozen=True, slots=True)
class _GameplanPreferences:
    """Live matchday hints resolved to one roster state of the save."""

    starters: dict[int, tuple[int, ...]]
    bench: dict[int, tuple[int, ...]]
    position_overrides: dict[int, dict[int, str]]


def _resolve_lineup_member(
    member: SquadMember,
    matcher: NameMatcher,
    team_id: int,
    team_player_map: Mapping[int, Sequence[int]],
    fotmob_player_ids: Mapping[int, int],
    threshold: float,
) -> int | None:
    """Resolve one lineup member to a player of ``team_id``'s roster.

    The FotMob player identity resolved during matching wins; the guarded
    name match (with age and nationality) is only a fallback.
    """
    roster_ids = set(team_player_map.get(team_id, ()))
    fotmob_player_id = _positive_int(member.player_id_fotmob)
    if fotmob_player_id is not None:
        player_id = fotmob_player_ids.get(fotmob_player_id)
        if player_id is not None:
            return player_id if player_id in roster_ids else None
    minimum = max(float(threshold), 85.0)
    player_id, _, confidence = matcher.match_player(
        member.player_name,
        threshold=minimum,
        to_team_id=team_id,
        team_player_map=team_player_map,
        nationality=member.nationality or None,
        age=member.age or None,
    )
    if player_id is None or confidence < minimum or player_id not in roster_ids:
        return None
    return player_id


def _plan_gameplan_preferences(
    snapshots: Sequence[SquadSnapshot],
    matcher: NameMatcher,
    team_player_map: Mapping[int, Sequence[int]],
    club_ids: Iterable[int],
    fotmob_team_map: Mapping[int, int],
    threshold: float,
    *,
    fotmob_player_ids: Mapping[int, int] | None = None,
    registered_position: Callable[[int], str | None] | None = None,
) -> _GameplanPreferences:
    """Resolve the live XI, bench, and positions into local game-plan hints.

    A starter's detailed matchday position overrides the registered
    Player.bin position only when the two map to different game-plan codes.
    """
    clubs = set(club_ids)
    identities = fotmob_player_ids or {}
    starters: dict[int, tuple[int, ...]] = {}
    bench: dict[int, tuple[int, ...]] = {}
    position_overrides: dict[int, dict[int, str]] = {}

    for snapshot in snapshots:
        if not snapshot.complete:
            continue
        team_id = fotmob_team_map.get(snapshot.team_id_fotmob)
        if team_id is None or team_id not in clubs:
            continue

        def resolve(member: SquadMember) -> int | None:
            return _resolve_lineup_member(
                member,
                matcher,
                team_id,
                team_player_map,
                identities,
                threshold,
            )

        starter_ids: list[int] = []
        overrides: dict[int, str] = {}
        for member in snapshot.starter_members:
            player_id = resolve(member)
            if player_id is None or player_id in starter_ids:
                continue
            starter_ids.append(player_id)
            live_code = _game_plan_position_code(member.position)
            if live_code is None:
                continue
            registered = (
                registered_position(player_id)
                if registered_position is not None
                else None
            )
            if _game_plan_position_code(registered) != live_code:
                overrides[player_id] = member.position

        bench_ids: list[int] = []
        for member in snapshot.sub_members:
            player_id = resolve(member)
            if (
                player_id is None
                or player_id in starter_ids
                or player_id in bench_ids
            ):
                continue
            bench_ids.append(player_id)

        # Keep a key even when no XI identity resolved: the team is still
        # aligned to its current formation roles.
        starters[team_id] = tuple(starter_ids)
        bench[team_id] = tuple(bench_ids)
        if overrides:
            position_overrides[team_id] = overrides
        else:
            position_overrides.pop(team_id, None)
    return _GameplanPreferences(starters, bench, position_overrides)


def _plan_gameplan_formations(
    snapshots: Sequence[SquadSnapshot],
    club_ids: Iterable[int],
    fotmob_team_map: Mapping[int, int],
) -> dict[int, str]:
    """Map verified current match shapes to represented local clubs."""
    clubs = set(club_ids)
    planned: dict[int, str] = {}
    conflicted: set[int] = set()
    for snapshot in snapshots:
        if not snapshot.complete:
            continue
        formation = normalize_game_plan_formation(snapshot.formation)
        if formation is None:
            continue
        team_id = fotmob_team_map.get(snapshot.team_id_fotmob)
        if team_id is None or team_id not in clubs or team_id in conflicted:
            continue

        previous = planned.get(team_id)
        if previous is not None and previous != formation:
            logger.warning(
                "Skipping conflicting formation observations for team %s",
                team_id,
            )
            planned.pop(team_id, None)
            conflicted.add(team_id)
            continue
        planned[team_id] = formation
    return planned


def _plan_gameplan_tactics(
    tactical_updates: Sequence[TacticalUpdate],
    club_ids: Iterable[int],
    fotmob_team_map: Mapping[int, int],
) -> dict[int, dict[str, int]]:
    """Resolve FotMob profiles to represented local clubs, failing closed."""
    clubs = set(club_ids)
    planned: dict[int, dict[str, int]] = {}
    conflicted: set[int] = set()
    for source in tactical_updates:
        team_id = fotmob_team_map.get(source.team_id_fotmob)
        if team_id is None or team_id not in clubs or team_id in conflicted:
            continue

        settings = dict(source.settings)
        if not settings:
            continue
        previous = planned.get(team_id)
        if previous is not None and previous != settings:
            logger.warning(
                "Skipping conflicting tactical profiles for team %s",
                team_id,
            )
            planned.pop(team_id, None)
            conflicted.add(team_id)
            continue
        planned[team_id] = settings
    return planned


def _plan_captain_updates(
    captain_updates: Sequence[CaptainUpdate],
    matcher: NameMatcher,
    team_player_map: Mapping[int, Sequence[int]],
    club_ids: Iterable[int],
    fotmob_team_map: Mapping[int, int],
    threshold: float,
    *,
    fotmob_player_ids: Mapping[int, int] | None = None,
) -> tuple[_PlannedCaptainUpdate, ...]:
    """Resolve live captain markers to fail-closed local roster targets."""
    clubs = set(club_ids)
    identities = fotmob_player_ids or {}
    by_team: dict[int, CaptainUpdate] = {}
    for source in captain_updates:
        team_id = fotmob_team_map.get(source.team_id_fotmob)
        if team_id is None or team_id not in clubs:
            logger.warning(
                "Skipping captain for %s (%s): club identity is not represented",
                source.club_name or source.team_id_fotmob,
                source.team_id_fotmob,
            )
            continue

        previous = by_team.get(team_id)
        if previous is not None and (
            previous.player_id_fotmob != source.player_id_fotmob
        ):
            logger.warning(
                "Skipping conflicting captain markers for team %s: %s vs %s",
                team_id,
                previous.player_name,
                source.player_name,
            )
            by_team.pop(team_id, None)
            continue
        by_team[team_id] = source

    planned: list[_PlannedCaptainUpdate] = []
    for team_id, source in by_team.items():
        roster_ids = set(team_player_map.get(team_id, ()))
        identity = identities.get(_positive_int(source.player_id_fotmob) or 0)
        if identity is not None and identity in roster_ids:
            player_id, player_name, confidence = identity, source.player_name, 100.0
        else:
            player_id, player_name, confidence = matcher.match_player(
                source.player_name,
                threshold=max(float(threshold), 90.0),
                to_team_id=team_id,
                team_player_map=team_player_map,
                nationality=source.nationality or None,
                age=source.age or None,
            )
        if player_id is None:
            logger.warning(
                "Skipping captain for %s: could not safely match %s",
                source.club_name or team_id,
                source.player_name,
            )
            continue
        planned.append(
            _PlannedCaptainUpdate(
                source=source,
                team_id=team_id,
                player_id=player_id,
                matched_player_name=player_name or source.player_name,
                confidence=confidence,
            )
        )
    return tuple(planned)


def _print_dry_run(
    edit_file: EditFile,
    roster_plan,
    tactical_profiles: dict[int, dict[str, int]] | None = None,
    gameplan_formations: dict[int, str] | None = None,
) -> None:
    """Render roster, tactical, and formation actions.

    Captains, XI, and bench depend on the post-transfer rosters; the
    game-plan preview prints them from an in-memory simulation.
    """
    print("\n🔍 DRY-RUN — checking each match against the current roster:")
    would_apply = 0
    already_current = 0
    safety_skipped = 0
    arriving = frozenset(
        (item.match.to_team_id, item.match.player_id)
        for item in roster_plan
        if item.action in ("move", "add")
    )
    shirt_statuses = _plan_shirt_number_batch(edit_file, roster_plan, arriving)
    for planned_action in roster_plan:
        match = planned_action.match
        action = planned_action.action
        if action == "skip":
            safety_skipped += 1
            print(
                f"  SAFETY SKIP ({planned_action.reason or 'state_mismatch'}, "
                f"current={planned_action.current_team_id}, source={match.from_team_id}, "
                f"destination={match.to_team_id}): {match}"
            )
            continue
        if action == "noop" or (
            action == "shirt_update" and match.transfer.shirt_number is None
        ):
            already_current += 1
            print(f"  ALREADY CURRENT: {match}")
            continue
        if action == "shirt_update":
            status = shirt_statuses.get(id(planned_action))
            current_shirt = (
                status[0]
                if status is not None
                else edit_file.get_player_shirt_number(
                    match.to_team_id, match.player_id
                )
            )
            if current_shirt == match.transfer.shirt_number:
                already_current += 1
                continue
            conflict_player = status[1] if status is not None else None
            reason = status[2] if status is not None else ""
            if conflict_player is not None:
                safety_skipped += 1
                print(
                    f"  SAFETY SKIP (shirt_number_conflict:{conflict_player}): "
                    f"{match}"
                )
                continue
            if reason:
                safety_skipped += 1
                print(f"  SAFETY SKIP ({reason}): {match}")
                continue

        would_apply += 1
        if planned_action.overflow_player_id is not None:
            details = planned_action.overflow_details or {}
            name = details.get("name") or "unknown player"
            role_group = details.get("role_group", "unknown")
            role = details.get("role", "?")
            usage = details.get("usage")
            if isinstance(usage, dict):
                usage_text = (
                    f"minutes={usage.get('minutes', '?')}, "
                    f"starts={usage.get('starts', '?')}, "
                    f"apps={usage.get('appearances', '?')}, "
                    f"news={usage.get('news_mentions', '?')}"
                )
            else:
                usage_text = "usage=unavailable"
            print(
                f"  WOULD AUTO-RELEASE: {name} "
                f"(id={planned_action.overflow_player_id}, "
                f"role={role_group}:{role}, {usage_text}) "
                f"from team {match.to_team_id}"
            )
        print(f"  WOULD {action.upper()}: {match}")
    if tactical_profiles:
        print("\nEvidence-gated tactical profiles (main preset):")
        for team_id, settings in sorted(tactical_profiles.items()):
            print(
                f"  Team {team_id}: {settings} "
                "(only differing supported fields would be written)"
            )
    if gameplan_formations:
        print("\nEvidence-derived formations (main preset):")
        for team_id, formation in sorted(gameplan_formations.items()):
            print(f"  Team {team_id}: {formation}")
    print(
        f"\nDry-run roster check complete. Would apply: {would_apply}, "
        f"already current: {already_current}, safety-skipped: {safety_skipped}."
    )


def _print_gameplan_diffs(
    before: Mapping[int, tuple],
    after: Mapping[int, tuple],
    player_names: Mapping[int, str],
    team_names: Mapping[int, str],
) -> None:
    """Print the XI, bench, and captain changes a run would make per team."""

    def name(player_id: int | None) -> str:
        if player_id is None:
            return "none"
        return player_names.get(player_id) or f"#{player_id}"

    def delta(old: Sequence[int], new: Sequence[int]) -> str:
        removed = [name(pid) for pid in old if pid not in new]
        added = [name(pid) for pid in new if pid not in old]
        if not removed and not added:
            return "same players, new order/roles"
        return ", ".join(
            [*(f"-{player}" for player in removed), *(f"+{player}" for player in added)]
        )

    changed = [team_id for team_id in sorted(after) if after[team_id] != before.get(team_id)]
    if not changed:
        print("\nGame plans: no XI, bench, or captain changes.")
        return
    print(f"\nGame-plan changes ({len(changed)} teams):")
    for team_id in changed:
        old_matchday, old_captain = before.get(team_id, (None, None))
        new_matchday, new_captain = after[team_id]
        old_xi, old_bench = old_matchday or ((), ())
        new_xi, new_bench = new_matchday or ((), ())
        print(f"  {team_names.get(team_id) or 'Team'} ({team_id}):")
        if tuple(old_xi) != tuple(new_xi):
            print(f"    XI: {delta(old_xi, new_xi)}")
        if tuple(old_bench) != tuple(new_bench):
            print(f"    Bench: {delta(old_bench, new_bench)}")
        if old_captain != new_captain:
            print(f"    Captain: {name(old_captain)} → {name(new_captain)}")


def _plan_shirt_number_batch(
    edit_file: EditFile,
    actions: list[planning.PlannedRosterAction],
    arriving: frozenset[tuple[int, int]] = frozenset(),
) -> dict[int, tuple[int | None, int | None, str]]:
    """Classify shirt updates so planned number swaps can be applied together.

    ``arriving`` holds (team_id, player_id) pairs not yet on the live roster that
    an earlier planned move/add will register; any other non-member is skipped.
    """
    statuses: dict[int, tuple[int | None, int | None, str]] = {}
    grouped: dict[int, list[planning.PlannedRosterAction]] = {}

    for item in actions:
        if item.action != "shirt_update":
            continue
        match = item.match
        team_id = match.to_team_id
        player_id = match.player_id
        previous = (
            edit_file.get_player_shirt_number(team_id, player_id)
            if team_id is not None and player_id is not None
            else None
        )
        statuses[id(item)] = (previous, None, "")
        if team_id is not None:
            grouped.setdefault(team_id, []).append(item)

    for team_id, group in grouped.items():
        roster = edit_file.get_team_roster(team_id)
        members = set(roster.player_ids) if roster is not None else set()
        candidates: list[planning.PlannedRosterAction] = []
        by_target: dict[int, list[planning.PlannedRosterAction]] = {}
        for item in group:
            match = item.match
            previous, _, _ = statuses[id(item)]
            target = match.transfer.shirt_number
            if target is None or previous == target:
                continue
            if (
                match.player_id not in members
                and (team_id, match.player_id) not in arriving
            ):
                statuses[id(item)] = (previous, None, "player_not_on_team")
                continue
            try:
                valid_target = 1 <= target <= 999
            except TypeError:
                valid_target = False
            if not valid_target:
                statuses[id(item)] = (
                    previous,
                    None,
                    "invalid_shirt_number",
                )
                continue
            candidates.append(item)
            by_target.setdefault(target, []).append(item)

        duplicate_ids = {
            id(item)
            for same_target in by_target.values()
            if len(same_target) > 1
            for item in same_target
        }
        for item_id in duplicate_ids:
            previous = statuses[item_id][0]
            requested = next(
                item.match.transfer.shirt_number
                for item in candidates
                if id(item) == item_id
            )
            statuses[item_id] = (
                previous,
                None,
                f"duplicate_shirt_number:{requested}",
            )

        occupants: dict[int, set[int]] = {}
        if roster is not None:
            for player_id, shirt_number in zip(
                roster.player_ids,
                roster.shirt_numbers,
            ):
                if player_id:
                    occupants.setdefault(shirt_number, set()).add(player_id)

        survivor_ids = {
            id(item) for item in candidates if id(item) not in duplicate_ids
        }
        while True:
            survivor_player_ids = {
                item.match.player_id
                for item in candidates
                if id(item) in survivor_ids and item.match.player_id is not None
            }
            blocked_ids = set()
            for item in candidates:
                item_id = id(item)
                if item_id not in survivor_ids:
                    continue
                player_id = item.match.player_id
                target = item.match.transfer.shirt_number
                conflicting_players = occupants.get(target, set()) - {player_id}
                if conflicting_players and not (
                    conflicting_players <= survivor_player_ids
                ):
                    blocked_ids.add(item_id)
            if not blocked_ids:
                break
            survivor_ids.difference_update(blocked_ids)

        for item in candidates:
            item_id = id(item)
            if item_id in duplicate_ids:
                continue
            if item_id in survivor_ids:
                continue
            player_id = item.match.player_id
            target = item.match.transfer.shirt_number
            conflicting_players = sorted(
                occupants.get(target, set()) - {player_id}
            )
            conflict_player = conflicting_players[0] if conflicting_players else None
            previous = statuses[item_id][0]
            reason = (
                f"shirt_number_conflict:{conflict_player}"
                if conflict_player is not None
                else f"shirt_number_dependency:{target}"
            )
            statuses[item_id] = (previous, conflict_player, reason)

    return statuses


def _apply_shirt_number_batch(
    edit_file: EditFile,
    team_id: int,
    actions: list[planning.PlannedRosterAction],
    statuses: dict[int, tuple[int | None, int | None, str]],
) -> bool:
    """Apply one conflict-free team batch after its safety checks pass."""
    updates = []
    for item in actions:
        status = statuses.get(id(item))
        if status is None or status[1] is not None or status[2]:
            continue
        previous, _, _ = status
        player_id = item.match.player_id
        target = item.match.transfer.shirt_number
        if (
            player_id is not None
            and target is not None
            and previous != target
        ):
            updates.append((player_id, target))
    if not updates:
        return True

    batch_updater = getattr(edit_file, "update_player_shirt_numbers", None)
    if callable(batch_updater):
        return bool(batch_updater(team_id, updates))

    return all(
        edit_file.update_player_shirt_number(team_id, player_id, shirt_number)
        for player_id, shirt_number in updates
    )



def _find_shirt_number_conflict(
    edit_file: EditFile,
    team_id: int | None,
    player_id: int | None,
    shirt_number: int | None,
) -> int | None:
    """Return the other player already using a requested shirt number."""
    if team_id is None or player_id is None or shirt_number is None:
        return None
    roster = edit_file.get_team_roster(team_id)
    if roster is None:
        return None
    for other_player_id, other_shirt_number in zip(
        roster.player_ids, roster.shirt_numbers
    ):
        if (
            other_player_id not in (0, player_id)
            and other_shirt_number == shirt_number
        ):
            return other_player_id
    return None

def _native_transfer_metadata(edit_file: EditFile, player_id: int) -> dict[str, object]:
    """Capture read-only native metadata for one transfer report row."""
    metadata: dict[str, object] = {}
    playerbin_db = getattr(edit_file, "playerbin_db", None)
    playerbin_source = getattr(edit_file, "playerbin_source", None)
    if playerbin_db is not None:
        record = playerbin_db.get(player_id)
        player_payload: dict[str, object] = {
            "source": playerbin_source,
            "found": record is not None,
        }
        if record is not None:
            player_payload.update(
                {
                    "player_id": record.player_id,
                    "name": record.name,
                    "print_name": record.print_name,
                    "age": record.age,
                    "registered_position": record.registered_position,
                    "market_value_eur": record.market_value_eur,
                    "contract_until": record.contract_until,
                    "loan_until": record.loan_until,
                    "is_on_loan": record.is_on_loan,
                    "owner_team_key": record.owner_team_key,
                    "youth_team_id": record.youth_team_id,
                    "caps": record.caps,
                }
            )
        metadata["player_bin"] = player_payload

    assignment_db = getattr(edit_file, "player_assignment_db", None)
    if assignment_db is not None:
        team_keys = assignment_db.team_keys_for(player_id)
        assignment_payload: dict[str, object] = {
            "source": getattr(edit_file, "player_assignment_source", None),
            "team_keys": list(team_keys),
        }
        teambin_db = getattr(edit_file, "teambin_db", None)
        if teambin_db is not None:
            assignment_payload["teams"] = [
                {
                    "team_key": team.team_key,
                    "name": team.name,
                    "abbreviation": team.abbreviation,
                }
                for team_key in team_keys
                if (team := teambin_db.get(team_key)) is not None
            ]
        metadata["player_assignment"] = assignment_payload
    return metadata



class _RunPrepared:
    def __init__(
        self,
        *,
        temp_dir: Path,
        data_dat: Path,
        edit_file: EditFile,
        edit_path: Path,
        output_path: Path,
        input_digest: str,
        same_input_output: bool,
        output_existed: bool,
        output_digest: str | None,
    ) -> None:
        self.temp_dir = temp_dir
        self.data_dat = data_dat
        self.edit_file = edit_file
        self.edit_path = edit_path
        self.output_path = output_path
        self.input_digest = input_digest
        self.same_input_output = same_input_output
        self.output_existed = output_existed
        self.output_digest = output_digest
        self.output_lock: EditFileLock | None = None
        self.save_scope = str(output_path.resolve())
        # Selected-save match context, loaded once before scraping.
        self.matcher: NameMatcher | None = None
        self.all_rosters: dict = {}
        self.team_player_map: dict[int, list[int]] = {}
        self.club_ids: set[int] = set()
        self.club_identity: ClubIdentityIndex | None = None
        self.scrape_context: _SaveScrapeContext | None = None
        self.match_threshold: float = float(config.MATCH_THRESHOLD_PLAYER)
        # FotMob club → save club and FotMob player → save player identities.
        self.fotmob_team_map: dict[int, int] = {}
        self.fotmob_player_ids: dict[int, int] = {}
        self.squad_snapshots: tuple[SquadSnapshot, ...] = ()
        self.captain_sources: tuple[CaptainUpdate, ...] = ()
        self.roster_plan: list[planning.PlannedRosterAction] = []
        self.gameplan_formations: dict[int, str] = {}
        self.gameplan_tactics: dict[int, dict[str, int]] = {}
        self.skipped: list[planning.SkippedTransfer] = []
        self.backup_path: Path | None = None
        self.original_data = bytes(
            getattr(edit_file, "_data", data_dat.read_bytes())
        )
        # Native Player.bin metadata can expose semantic issues already present
        # in the selected save.  Keep those diagnostics as a baseline so a
        # transfer is rejected only when it introduces a new integrity error.
        self.pre_mutation_integrity_errors: tuple[str, ...] = ()
        self.pending_logs = []
        self.captain_records = []
        self.run_records = []

    def skipped_rows(self) -> tuple[dict, ...]:
        return _skipped_rows(self.skipped, self.save_scope)


class _RunMutation:
    def __init__(
        self,
        *,
        transfer_applied: int,
        shirt_numbers_changed: int,
        unchanged: int,
        safety_skipped: int,
        captains_changed: int = 0,
        tactics_changed: int = 0,
        formations_changed: int = 0,
        gameplan_changed: bool = False,
    ) -> None:
        self.transfer_applied = transfer_applied
        self.shirt_numbers_changed = shirt_numbers_changed
        self.unchanged = unchanged
        self.safety_skipped = safety_skipped
        self.captains_changed = captains_changed
        self.tactics_changed = tactics_changed
        self.formations_changed = formations_changed
        self.gameplan_changed = gameplan_changed


class _RunLocalUpdateRuntime:
    """Adapter from the shared service lifecycle to the verified edit-file pipeline."""

    def __init__(self, progress: ProgressCallback | None = None) -> None:
        self._progress = progress

    @staticmethod
    def _args(request: LocalUpdateRequest) -> argparse.Namespace:
        return argparse.Namespace(
            popular=request.popular,
            window=request.window,
            since=request.since,
            club=request.club,
            deep=request.deep,
            fotmob_only=request.fotmob_only,
            allow_overflow_release=request.allow_overflow_release,
        )

    def _report_scrape_progress(
        self,
        detail: str,
        current: int,
        total: int,
    ) -> None:
        if self._progress is not None:
            self._progress(
                LocalUpdateProgress(
                    LocalUpdateStage.SCRAPING,
                    detail=detail,
                    current=current,
                    total=total,
                )
            )

    @staticmethod
    def _release_lock(prepared: _RunPrepared) -> None:
        if prepared.output_lock is not None:
            prepared.output_lock.release()
            prepared.output_lock = None

    def _ensure_context(
        self,
        request: LocalUpdateRequest,
        prepared: _RunPrepared,
    ) -> None:
        """Load the save's match database and club identity index once."""
        if prepared.club_identity is not None:
            return
        try:
            if request.release_policy_file is None:
                loaded = _load_match_database(prepared.edit_file)
            else:
                loaded = _load_match_database(
                    prepared.edit_file,
                    request.release_policy_file,
                )
            matcher, all_rosters, team_player_map, club_ids = loaded
            club_identity = _load_club_identity(
                prepared.edit_file,
                club_ids,
                prepared.save_scope,
            )
        except LocalUpdateError:
            raise
        except Exception as error:
            raise LocalUpdateError(
                "matching_failed",
                f"Transfer matching failed: {error}",
                stage=LocalUpdateStage.MATCHING,
            ) from error
        if matcher is not None:
            matcher.load_team_aliases(club_identity.aliases())
        prepared.matcher = matcher
        prepared.all_rosters = all_rosters
        prepared.team_player_map = team_player_map
        prepared.club_ids = set(club_ids)
        prepared.club_identity = club_identity
        prepared.match_threshold = float(
            request.threshold or config.MATCH_THRESHOLD_PLAYER
        )

    def _scrape_context(
        self,
        request: LocalUpdateRequest,
        prepared: _RunPrepared,
    ) -> _SaveScrapeContext:
        self._ensure_context(request, prepared)
        if prepared.scrape_context is None:
            # Narrow the window from history only when this run continues
            # the save that history describes; a rebuild from a different
            # input must replay the previous-window range.
            save_since_date = (
                _save_since_date(
                    prepared.save_scope,
                    include_legacy=(
                        prepared.output_path.resolve()
                        == config.OUTPUT_FILE_PATH.resolve()
                    ),
                )
                if prepared.same_input_output
                else None
            )
            prepared.scrape_context = _SaveScrapeContext(
                club_identity=prepared.club_identity,
                club_ids=frozenset(prepared.club_ids),
                save_since_date=save_since_date,
            )
        return prepared.scrape_context

    def scrape(
        self,
        request: LocalUpdateRequest,
        prepared: _RunPrepared,
        _token: CancellationToken,
    ):
        context = self._scrape_context(request, prepared)
        args = self._args(request)
        if self._progress is None:
            return _scrape_run_transfers(args, context=context)
        return _scrape_run_transfers(
            args,
            context=context,
            progress=self._report_scrape_progress,
        )

    def validate_and_prepare(
        self,
        request: LocalUpdateRequest,
        _token: CancellationToken,
    ) -> _RunPrepared:
        if not request.edit_path.exists():
            raise LocalUpdateError(
                "missing_input",
                f"Edit file not found: {request.edit_path}",
                stage=LocalUpdateStage.VALIDATING,
            )
        output_path = request.target_path
        prepared: _RunPrepared | None = None
        lock = EditFileLock(output_path)
        try:
            lock.acquire()
        except Exception as error:
            raise LocalUpdateError(
                "target_locked",
                str(error),
                stage=LocalUpdateStage.VALIDATING,
            ) from error

        try:
            input_digest = _sha256_file(request.edit_path)
            same_input_output = output_path.resolve() == request.edit_path.resolve()
            output_existed = output_path.exists()
            output_digest = (
                input_digest
                if same_input_output
                else _sha256_file(output_path) if output_existed else None
            )

            print(f"\n🔓 Decrypting {request.edit_path}...")
            try:
                temp_dir = crypto.decrypt(request.edit_path)
            except Exception as error:
                raise LocalUpdateError(
                    "decrypt_failed",
                    f"Decryption failed: {error}",
                    stage=LocalUpdateStage.VALIDATING,
                ) from error

            data_dat = temp_dir / "data.dat"

            edit_file = EditFile()
            edit_file.load(data_dat)
            edit_file.game_root = request.game_root
            header_path = temp_dir / "header.dat"
            if header_path.is_file():
                try:
                    edit_file.attach_save_header(read_save_header(header_path))
                except (OSError, ValueError) as error:
                    raise LocalUpdateError(
                        "invalid_save",
                        f"Invalid decrypted save header: {error}",
                        stage=LocalUpdateStage.VALIDATING,
                    ) from error

            integrity = edit_file.validate_integrity()
            if not integrity["valid"]:
                details = [
                    "Input save failed supported edit-file integrity validation; no changes were written."
                ]
                details.extend(f"  - {error}" for error in integrity["errors"][:20])
                remaining = len(integrity["errors"]) - 20
                if remaining > 0:
                    details.append(f"  ... and {remaining} more errors")
                details.append(
                    "Use a standard EDIT00000000 save with a supported layout."
                )
                raise LocalUpdateError(
                    "invalid_save",
                    "\n".join(details),
                    stage=LocalUpdateStage.VALIDATING,
                )

            prepared = _RunPrepared(
                temp_dir=temp_dir,
                data_dat=data_dat,
                edit_file=edit_file,
                edit_path=request.edit_path,
                output_path=output_path,
                input_digest=input_digest,
                same_input_output=same_input_output,
                output_existed=output_existed,
                output_digest=output_digest,
            )
            prepared.output_lock = lock
            return prepared
        except LocalUpdateError:
            if prepared is not None:
                crypto.cleanup_temp(prepared.temp_dir)
            else:
                temp_dir = locals().get("temp_dir")
                if temp_dir is not None:
                    crypto.cleanup_temp(temp_dir)
            lock.release()
            raise
        except Exception as error:
            temp_dir = locals().get("temp_dir")
            if temp_dir is not None:
                crypto.cleanup_temp(temp_dir)
            lock.release()
            raise LocalUpdateError(
                "invalid_save",
                f"Could not load the selected save: {error}",
                stage=LocalUpdateStage.VALIDATING,
            ) from error

    def match_and_plan(
        self,
        request: LocalUpdateRequest,
        prepared: _RunPrepared,
        transfers,
        _token: CancellationToken,
    ):
        self._ensure_context(request, prepared)
        try:
            edit_file = prepared.edit_file
            matcher = prepared.matcher
            club_identity = prepared.club_identity
            baseline_integrity = edit_file.validate_integrity()
            prepared.pre_mutation_integrity_errors = tuple(
                str(error) for error in baseline_integrity.get("errors", [])
            )
            squad_snapshots = tuple(getattr(transfers, "squad_snapshots", ()))
            player_names = _player_names(edit_file)
            learned = 0
            for snapshot in squad_snapshots:
                if snapshot.complete and club_identity.learn_from_snapshot(
                    snapshot,
                    prepared.team_player_map,
                    player_names,
                ) is not None:
                    learned += 1
            try:
                club_identity.save()
            except OSError as error:
                logger.warning("Could not cache learned club identities: %s", error)
            if learned:
                print(f"  Club identities confirmed from live squads: {learned}")

            report = planning.PlanningReport()
            roster_plan, fully_matched, save_scope = _match_and_plan_transfers(
                transfers,
                matcher,
                prepared.match_threshold,
                prepared.team_player_map,
                prepared.all_rosters,
                prepared.club_ids,
                edit_file,
                prepared.output_path,
                club_identity=club_identity,
                report=report,
                allow_overflow_release=request.allow_overflow_release,
                allow_uncovered_source=not request.deep,
            )
            context = prepared.scrape_context or _SaveScrapeContext(
                club_identity=club_identity,
                club_ids=frozenset(prepared.club_ids),
            )
            prepared.skipped = [
                *report.skipped,
                *_pending_skipped(
                    getattr(transfers, "pending_transfers", ()),
                    context,
                ),
            ]
            prepared.fotmob_player_ids = dict(report.fotmob_player_ids)
            prepared.fotmob_team_map = {
                int(entry["fotmob_id"]): int(entry["pes_team_id"])
                for entry in club_identity.entries()
            }
            prepared.squad_snapshots = squad_snapshots
            prepared.captain_sources = tuple(
                getattr(transfers, "captain_updates", ())
            )
            prepared.gameplan_formations = _plan_gameplan_formations(
                squad_snapshots,
                prepared.club_ids,
                prepared.fotmob_team_map,
            )
            tactical_sources = tuple(getattr(transfers, "tactical_updates", ()))
            if tactical_sources:
                prepared.gameplan_tactics = _plan_gameplan_tactics(
                    tactical_sources,
                    prepared.club_ids,
                    prepared.fotmob_team_map,
                )
                print(
                    "Tactical profiles safely mapped to local clubs: "
                    f"{len(prepared.gameplan_tactics)}"
                )
            relevant = sum(item.relevant for item in prepared.skipped)
            print(
                f"  Not applied: {len(prepared.skipped)} transfers "
                f"({relevant} touch this save)"
            )
            prepared.roster_plan = roster_plan
            prepared.save_scope = save_scope
            return roster_plan, fully_matched
        except LocalUpdateError:
            raise
        except Exception as error:
            raise LocalUpdateError(
                "matching_failed",
                f"Transfer matching failed: {error}",
                stage=LocalUpdateStage.MATCHING,
            ) from error

    def _gameplan_preferences(
        self,
        prepared: _RunPrepared,
        team_player_map: Mapping[int, Sequence[int]],
    ) -> _GameplanPreferences:
        return _plan_gameplan_preferences(
            prepared.squad_snapshots,
            prepared.matcher,
            team_player_map,
            prepared.club_ids,
            prepared.fotmob_team_map,
            prepared.match_threshold,
            fotmob_player_ids=prepared.fotmob_player_ids,
            registered_position=getattr(
                prepared.edit_file, "get_player_position", None
            ),
        )

    def apply(
        self,
        request: LocalUpdateRequest,
        prepared: _RunPrepared,
        _plan,
        token: CancellationToken,
    ):
        print(
            "\n⚡ Applying verified transfers, squad membership, shirt-number, "
            "game-plan, and captain changes..."
        )
        mutation = self._mutate(request, prepared, token)
        effective = (
            mutation.transfer_applied
            or mutation.shirt_numbers_changed
            or mutation.captains_changed
            or mutation.tactics_changed
            or mutation.formations_changed
            or mutation.gameplan_changed
        )
        if not effective:
            print(
                "No effective transfer, squad, captain, tactical, or "
                "formation changes to apply. Exiting."
            )
            try:
                # Keep the not-applied report current: it also seeds the next
                # run's scrape window with still-pending relevant events.
                transfer_logger.save_reports([], skipped=prepared.skipped_rows())
            except OSError as error:
                print(f"\n⚠ Could not write the not-applied report: {error}")
            return LocalUpdateResult(
                target_path=prepared.output_path,
                backup_path=None,
                installed_sha256=None,
                transfer_applied=0,
                shirt_numbers_changed=0,
                unchanged=mutation.unchanged,
                safety_skipped=mutation.safety_skipped,
                no_changes=True,
                skipped=prepared.skipped_rows(),
            )

        token.raise_if_cancelled()
        print("\n💾 Creating backup...")
        # Back up the save publish() overwrites: when writing to a separate,
        # existing output, that file (not the unchanged input) is replaced.
        backup_source = (
            prepared.output_path
            if not prepared.same_input_output and prepared.output_existed
            else prepared.edit_path
        )
        try:
            prepared.backup_path = backup_mod.create_backup(backup_source)
        except Exception as error:
            raise LocalUpdateError(
                "backup_failed",
                f"Backup failed: {error}",
                stage=LocalUpdateStage.APPLYING,
            ) from error
        print(f"  Backup: {prepared.backup_path}")
        return mutation

    def _mutate(
        self,
        request: LocalUpdateRequest,
        prepared: _RunPrepared,
        token: CancellationToken,
    ) -> _RunMutation:
        """Apply every planned change to the in-memory save only."""
        edit_file = prepared.edit_file
        formations_changed = 0
        formation_setter = getattr(edit_file, "set_team_formation", None)
        if callable(formation_setter):
            for team_id, formation in prepared.gameplan_formations.items():
                token.raise_if_cancelled()
                changed = formation_setter(team_id, formation)
                if type(changed) is int and changed > 0:
                    formations_changed += 1
        if formations_changed:
            print(f"  Game-plan formations changed: {formations_changed} clubs")

        tactics_changed = 0
        tactical_setter = getattr(edit_file, "set_team_tactical_settings", None)
        if callable(tactical_setter):
            for team_id, settings in prepared.gameplan_tactics.items():
                token.raise_if_cancelled()
                changed = tactical_setter(team_id, settings)
                if type(changed) is int and changed > 0:
                    tactics_changed += changed
        if tactics_changed:
            print(f"  Tactical settings changed: {tactics_changed}")

        # Removal backfills prefer the live XI of the pre-transfer roster.
        starter_hint = getattr(edit_file, "set_game_plan_preferred_starters", None)
        if callable(starter_hint) and prepared.squad_snapshots:
            starter_hint(
                self._gameplan_preferences(
                    prepared,
                    prepared.team_player_map,
                ).starters
            )

        transfer_applied = 0
        shirt_numbers_applied = 0
        captains_changed = 0
        unchanged = 0
        safety_skipped = 0
        original_data = prepared.original_data
        touched_teams: set[int] = set()
        shirt_batch_states: dict[
            int, dict[int, tuple[int | None, int | None, str]]
        ] = {}
        shirt_batch_applied: set[int] = set()

        for planned_action in prepared.roster_plan:
            token.raise_if_cancelled()
            match = planned_action.match
            to_team_id = match.to_team_id
            transfer = match.transfer
            action = planned_action.action
            if action == "skip":
                safety_skipped += 1
                print(
                    f"  ⚠ Safety skip {match.matched_player_name or transfer.player_name}: "
                    f"{planned_action.reason or 'state mismatch'}"
                )
                continue

            player_id = match.player_id
            if player_id is None:
                continue

            current_team_id = planned_action.current_team_id
            if action == "noop":
                unchanged += 1
                continue
            native_metadata = _native_transfer_metadata(
                prepared.edit_file,
                player_id,
            )
            prepared.edit_file.last_mutation_error_code = None
            prepared.edit_file.last_mutation_error = ""

            ok = False
            preferred_shirt = transfer.shirt_number
            previous_shirt = None
            if action == "shirt_update":
                if preferred_shirt is None:
                    unchanged += 1
                    continue
                if (
                    to_team_id is not None
                    and to_team_id not in shirt_batch_applied
                ):
                    shirt_actions = [
                        candidate
                        for candidate in prepared.roster_plan
                        if (
                            candidate.action == "shirt_update"
                            and candidate.match.to_team_id == to_team_id
                        )
                    ]
                    statuses = _plan_shirt_number_batch(
                        prepared.edit_file,
                        shirt_actions,
                    )
                    if not _apply_shirt_number_batch(
                        prepared.edit_file,
                        to_team_id,
                        shirt_actions,
                        statuses,
                    ):
                        prepared.edit_file._data = bytearray(original_data)
                        raise LocalUpdateError(
                            "apply_failed",
                            f"Failed: {match.matched_player_name or transfer.player_name} "
                            f"({match.action_type}); entire batch rolled back. "
                            "No changes were published.",
                            stage=LocalUpdateStage.APPLYING,
                        )
                    shirt_batch_states[to_team_id] = statuses
                    shirt_batch_applied.add(to_team_id)

                status = (
                    shirt_batch_states.get(to_team_id, {}).get(id(planned_action))
                    if to_team_id is not None
                    else None
                )
                previous_shirt = (
                    status[0]
                    if status is not None
                    else prepared.edit_file.get_player_shirt_number(
                        to_team_id,
                        player_id,
                    )
                )
                if previous_shirt == preferred_shirt:
                    unchanged += 1
                    continue
                conflict_player = status[1] if status is not None else (
                    _find_shirt_number_conflict(
                        prepared.edit_file,
                        to_team_id,
                        player_id,
                        preferred_shirt,
                    )
                )
                reason = status[2] if status is not None else ""
                if conflict_player is not None or reason:
                    safety_skipped += 1
                    if conflict_player is not None:
                        detail = (
                            f"shirt #{preferred_shirt} is already assigned to player "
                            f"{conflict_player} on team {to_team_id}"
                        )
                    else:
                        detail = reason
                    print(
                        f"  ⚠ Safety skip {match.matched_player_name or transfer.player_name}: "
                        f"{detail}"
                    )
                    continue
                ok = True
            elif action == "move":
                ok = prepared.edit_file.move_player(
                    player_id,
                    current_team_id,
                    to_team_id,
                    shirt_number=preferred_shirt,
                    position=transfer.position,
                    allow_overflow_release=request.allow_overflow_release,
                    planned_overflow_player_id=planned_action.overflow_player_id,
                )
            elif action == "add":
                ok = prepared.edit_file.add_player(
                    player_id,
                    to_team_id,
                    shirt_number=preferred_shirt,
                    position=transfer.position,
                    allow_overflow_release=request.allow_overflow_release,
                    planned_overflow_player_id=planned_action.overflow_player_id,
                )
            elif action == "release":
                ok = prepared.edit_file.release_player(
                    player_id,
                    match.from_team_id,
                )

            if not ok:
                failure_code = getattr(
                    prepared.edit_file,
                    "last_mutation_error_code",
                    None,
                )
                failure_detail = getattr(
                    prepared.edit_file,
                    "last_mutation_error",
                    "",
                )
                if failure_code in _SAFE_MUTATION_FAILURE_CODES:
                    safety_skipped += 1
                    print(
                        f"  ⚠ Safety skip "
                        f"{match.matched_player_name or transfer.player_name}: "
                        f"{failure_detail or failure_code}"
                    )
                    if action != "shirt_update":
                        prepared.skipped.append(
                            planning.SkippedTransfer.from_match(
                                match,
                                failure_code,
                                failure_detail,
                                club_ids=prepared.club_ids,
                            )
                        )
                    continue
                prepared.edit_file._data = bytearray(original_data)
                detail = f" Reason: {failure_detail}" if failure_detail else ""
                raise LocalUpdateError(
                    "apply_failed",
                    f"Failed: {match.matched_player_name or transfer.player_name} "
                    f"({match.action_type}); entire batch rolled back."
                    f"{detail} No changes were published.",
                    stage=LocalUpdateStage.APPLYING,
                )

            if action == "shirt_update":
                shirt_numbers_applied += 1
            else:
                transfer_applied += 1
                touched_teams.update(
                    team_id
                    for team_id in (current_team_id, match.from_team_id, to_team_id)
                    if team_id is not None and team_id in prepared.club_ids
                )
            prepared.pending_logs.append((match, previous_shirt, action))
            prepared.run_records.append(
                {
                    "player_name": match.matched_player_name or transfer.player_name,
                    "from_team": match.matched_from_team or transfer.from_club,
                    "to_team": match.matched_to_team or transfer.to_club,
                    "position": transfer.position,
                    "fee": transfer.fee,
                    "transfer_type": transfer.transfer_type,
                    "confidence": match.min_confidence,
                    "dry_run": False,
                    "previous_shirt_number": previous_shirt,
                    "shirt_number": (
                        preferred_shirt if action == "shirt_update" else None
                    ),
                    "roster_action": action,
                    "sources": list(transfer.sources),
                    "source_urls": list(transfer.source_urls),
                    "proof_urls": list(transfer.proof_urls),
                    "native_metadata": native_metadata,
                }
            )

        gameplan_changed = self._align_game_plans(prepared, touched_teams)

        captain_getter = getattr(edit_file, "get_team_captain_player", None)
        captain_setter = getattr(edit_file, "set_team_captain", None)
        captain_plan = _plan_captain_updates(
            prepared.captain_sources,
            prepared.matcher,
            self._current_team_player_map(edit_file),
            prepared.club_ids,
            prepared.fotmob_team_map,
            prepared.match_threshold,
            fotmob_player_ids=prepared.fotmob_player_ids,
        ) if prepared.captain_sources else ()
        for planned_captain in captain_plan:
            token.raise_if_cancelled()
            current_player_id = (
                captain_getter(planned_captain.team_id)
                if callable(captain_getter)
                else None
            )
            if current_player_id == planned_captain.player_id:
                continue
            if not callable(captain_setter) or not captain_setter(
                planned_captain.team_id,
                planned_captain.player_id,
            ):
                safety_skipped += 1
                print(
                    "  ⚠ Captain safety skip "
                    f"{planned_captain.source.club_name}: "
                    f"{planned_captain.matched_player_name} is not in the "
                    "current roster or game plan"
                )
                continue

            captains_changed += 1
            print(
                f"  Captain updated: {planned_captain.source.club_name} → "
                f"{planned_captain.matched_player_name}"
            )
            prepared.captain_records.append(
                {
                    "player_name": planned_captain.matched_player_name,
                    "player_id": planned_captain.player_id,
                    "from_team": planned_captain.source.club_name,
                    "from_team_id": planned_captain.team_id,
                    "to_team": planned_captain.source.club_name,
                    "to_team_id": planned_captain.team_id,
                    "team_name": planned_captain.source.club_name,
                    "team_id": planned_captain.team_id,
                    "previous_player_id": current_player_id,
                    "confidence": planned_captain.confidence,
                    "transfer_type": "captain_update",
                    "dry_run": False,
                    "position": "",
                    "fee": "",
                    "roster_action": "captain",
                    "sources": [planned_captain.source.source],
                    "source_urls": (
                        [planned_captain.source.source_url]
                        if planned_captain.source.source_url
                        else []
                    ),
                    "proof_urls": [],
                    "native_metadata": {
                        "previous_captain_player_id": current_player_id,
                    },
                    "source": planned_captain.source.source,
                    "source_url": planned_captain.source.source_url,
                    "fotmob_player_id": planned_captain.source.player_id_fotmob,
                }
            )

        if captains_changed:
            print(f"  Captains changed: {captains_changed}")

        print(
            f"\n  Transfers applied: {transfer_applied}, "
            f"shirt numbers changed: {shirt_numbers_applied}, "
            f"captains changed: {captains_changed}, "
            f"tactical settings changed: {tactics_changed}, "
            f"formations changed: {formations_changed}, "
            f"already current: {unchanged}, safety-skipped: {safety_skipped}"
        )
        return _RunMutation(
            transfer_applied=transfer_applied,
            shirt_numbers_changed=shirt_numbers_applied,
            unchanged=unchanged,
            safety_skipped=safety_skipped,
            captains_changed=captains_changed,
            tactics_changed=tactics_changed,
            formations_changed=formations_changed,
            gameplan_changed=gameplan_changed,
        )

    @staticmethod
    def _current_team_player_map(edit_file) -> dict[int, list[int]]:
        return {
            team_id: list(roster.roster)
            for team_id, roster in edit_file.get_all_rosters().items()
        }

    def _align_game_plans(
        self,
        prepared: _RunPrepared,
        touched_teams: set[int],
    ) -> bool:
        """Resolve the live matchday against post-transfer rosters and align.

        Runs after every roster mutation so this run's signings can enter
        the XI and bench; teams changed by roster actions are always aligned.
        """
        edit_file = prepared.edit_file
        repair_game_plans = getattr(edit_file, "repair_game_plans", None)
        if not callable(repair_game_plans):
            return False
        repair_kwargs: dict[str, object] = {"preserve_existing_primary": True}
        if prepared.squad_snapshots or touched_teams or prepared.gameplan_formations:
            preferences = (
                self._gameplan_preferences(
                    prepared,
                    self._current_team_player_map(edit_file),
                )
                if prepared.squad_snapshots
                else _GameplanPreferences({}, {}, {})
            )
            repair_kwargs.update(
                preferred_starters={
                    **{team_id: () for team_id in touched_teams},
                    **preferences.starters,
                },
                preferred_bench=preferences.bench,
                position_overrides=preferences.position_overrides,
                align_positions=True,
            )
        metrics = repair_game_plans(**repair_kwargs)
        changed = {
            key: value
            for key, value in (metrics or {}).items()
            if key != "checked" and value
        }
        if changed:
            print(
                "  Game-plan repairs: "
                + ", ".join(f"{key}={value}" for key, value in sorted(changed.items()))
            )
        return bool(changed)

    def verify(
        self,
        _request: LocalUpdateRequest,
        prepared: _RunPrepared,
        _mutation: _RunMutation,
        _token: CancellationToken,
    ) -> None:
        post_integrity = prepared.edit_file.validate_integrity()
        post_errors = tuple(
            str(error) for error in post_integrity.get("errors", [])
        )
        baseline_errors = set(prepared.pre_mutation_integrity_errors)
        new_errors = tuple(
            error for error in post_errors if error not in baseline_errors
        )
        if new_errors:
            prepared.edit_file._data = bytearray(prepared.original_data)
            details = [
                "Modified save failed integrity validation; changes were rolled back."
            ]
            details.extend(f"  - {error}" for error in new_errors[:20])
            remaining = len(new_errors) - 20
            if remaining > 0:
                details.append(f"  ... and {remaining} more errors")
            preserved = len(post_errors) - len(new_errors)
            if preserved > 0:
                details.append(
                    f"  Preserved {preserved} pre-existing integrity diagnostics."
                )
            raise LocalUpdateError(
                "post_validation_failed",
                "\n".join(details),
                stage=LocalUpdateStage.VERIFYING,
            )
        if post_errors:
            print(
                f"\n  Preserved {len(post_errors)} pre-existing "
                "integrity diagnostics."
            )

        if _sha256_file(prepared.edit_path) != prepared.input_digest:
            prepared.edit_file._data = bytearray(prepared.original_data)
            raise LocalUpdateError(
                "input_changed",
                "Input EDIT file changed while this run was processing; "
                "stale output was not written.",
                stage=LocalUpdateStage.VERIFYING,
            )

        if not prepared.same_input_output:
            output_changed = prepared.output_path.exists() != prepared.output_existed
            if prepared.output_existed and prepared.output_path.exists():
                output_changed = (
                    _sha256_file(prepared.output_path) != prepared.output_digest
                )
            if output_changed:
                prepared.edit_file._data = bytearray(prepared.original_data)
                raise LocalUpdateError(
                    "output_changed",
                    "Output EDIT file changed while this run was processing; "
                    "concurrent output was preserved.",
                    stage=LocalUpdateStage.VERIFYING,
                )

    def publish(
        self,
        _request: LocalUpdateRequest,
        prepared: _RunPrepared,
        mutation: _RunMutation,
        _token: CancellationToken,
    ) -> LocalUpdateResult:
        try:
            prepared.edit_file.save(prepared.data_dat)
            prepared.output_path.parent.mkdir(parents=True, exist_ok=True)
            print(f"\n🔒 Re-encrypting → {prepared.output_path}...")
            crypto.encrypt(prepared.temp_dir, prepared.output_path)
        except Exception as error:
            prepared.edit_file._data = bytearray(prepared.original_data)
            raise LocalUpdateError(
                "publish_failed",
                f"Could not publish verified save: {error}",
                stage=LocalUpdateStage.ENCRYPTING,
            ) from error

        diagnostic: str | None = None
        transfer_log_content: str | None = None
        try:
            for (match, previous_shirt, action), run_record in zip(
                prepared.pending_logs,
                prepared.run_records,
                strict=True,
            ):
                transfer = match.transfer
                transfer_logger.log_transfer(
                    player_name=match.matched_player_name or transfer.player_name,
                    player_id=match.player_id,
                    from_team=match.matched_from_team or transfer.from_club,
                    from_team_id=match.from_team_id or 0,
                    to_team=match.matched_to_team or transfer.to_club,
                    to_team_id=match.to_team_id or 0,
                    confidence=match.min_confidence,
                    transfer_type=transfer.transfer_type,
                    dry_run=False,
                    position=transfer.position,
                    fee=transfer.fee,
                    market_value=transfer.market_value,
                    transfer_date=transfer.date,
                    previous_shirt_number=previous_shirt,
                    shirt_number=(
                        transfer.shirt_number
                        if transfer.transfer_type == "shirt_number_update"
                        else run_record.get("shirt_number")
                        if action == "shirt_update"
                        else None
                    ),
                    roster_action=action,
                    save_scope=prepared.save_scope,
                    fotmob_player_id=transfer.player_id_fotmob,
                    sortitoutsi_player_id=transfer.player_id_sortitoutsi,
                    transfermarkt_player_id=transfer.player_id_transfermarkt,
                    transfermarkt_from_club_id=transfer.from_club_id_transfermarkt,
                    transfermarkt_to_club_id=transfer.to_club_id_transfermarkt,
                    transfermarkt_transfer_id=transfer.transfer_id_transfermarkt,
                    sources=transfer.sources,
                    source_urls=transfer.source_urls,
                    proof_urls=transfer.proof_urls,
                    native_metadata=run_record.get("native_metadata"),
                )
            captain_records = list(getattr(prepared, "captain_records", ()))
            for record in captain_records:
                source_url = str(record.get("source_url") or "")
                source = str(record.get("source") or "fotmob")
                transfer_logger.log_transfer(
                    player_name=str(record["player_name"]),
                    player_id=int(record["player_id"]),
                    from_team=str(record["team_name"]),
                    from_team_id=int(record["team_id"]),
                    to_team=str(record["team_name"]),
                    to_team_id=int(record["team_id"]),
                    confidence=float(record.get("confidence") or 0),
                    transfer_type="captain_update",
                    dry_run=False,
                    roster_action="captain",
                    save_scope=prepared.save_scope,
                    fotmob_player_id=record.get("fotmob_player_id"),
                    sources=(source,),
                    source_urls=(source_url,) if source_url else (),
                    native_metadata={
                        "previous_captain_player_id": record.get(
                            "previous_player_id"
                        )
                    },
                )
            report_records = [
                *prepared.run_records,
                *captain_records,
            ]
            transfer_log_content = transfer_logger.save_reports(
                report_records,
                skipped=prepared.skipped_rows(),
            )
        except Exception as error:
            diagnostic = (
                "Save published, but transfer logging/report generation failed: "
                f"{error}"
            )
            print(f"\n⚠ {diagnostic}")
        installed_sha256 = (
            _sha256_file(prepared.output_path)
            if prepared.output_path.exists()
            else None
        )
        return LocalUpdateResult(
            target_path=prepared.output_path,
            backup_path=prepared.backup_path,
            installed_sha256=installed_sha256,
            transfer_applied=mutation.transfer_applied,
            shirt_numbers_changed=mutation.shirt_numbers_changed,
            unchanged=mutation.unchanged,
            safety_skipped=mutation.safety_skipped,
            diagnostic=diagnostic,
            transfer_log_content=transfer_log_content,
            captains_changed=mutation.captains_changed,
            tactics_changed=mutation.tactics_changed,
            formations_changed=mutation.formations_changed,
            skipped=prepared.skipped_rows(),
        )

    def preview(
        self,
        request: LocalUpdateRequest,
        prepared: _RunPrepared,
        plan,
        token: CancellationToken,
    ) -> LocalUpdateResult:
        _print_dry_run(
            prepared.edit_file,
            plan[0] if isinstance(plan, tuple) else plan,
            prepared.gameplan_tactics,
            prepared.gameplan_formations,
        )
        self._print_gameplan_preview(request, prepared, token)
        print("No files were written.")
        return LocalUpdateResult(
            target_path=prepared.output_path,
            backup_path=None,
            installed_sha256=None,
            transfer_applied=0,
            shirt_numbers_changed=0,
            unchanged=0,
            safety_skipped=0,
            no_changes=True,
            skipped=prepared.skipped_rows(),
        )

    def _print_gameplan_preview(
        self,
        request: LocalUpdateRequest,
        prepared: _RunPrepared,
        token: CancellationToken,
    ) -> None:
        """Simulate the run in memory and print per-team XI/bench/captain diffs."""
        edit_file = prepared.edit_file
        matchday = getattr(edit_file, "get_team_matchday", None)
        captain_getter = getattr(edit_file, "get_team_captain_player", None)
        if not callable(matchday) or not callable(captain_getter):
            return

        def state() -> dict[int, tuple]:
            return {
                team_id: (matchday(team_id), captain_getter(team_id))
                for team_id in sorted(prepared.club_ids)
            }

        before = state()
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                self._mutate(request, prepared, token)
            after = state()
        except LocalUpdateError as error:
            print(f"\n⚠ Game-plan preview unavailable: {error}")
            return
        finally:
            edit_file._data = bytearray(prepared.original_data)
            prepared.pending_logs.clear()
            prepared.run_records.clear()
            prepared.captain_records.clear()
        _print_gameplan_diffs(
            before,
            after,
            _player_names(edit_file),
            _save_club_names(edit_file, prepared.club_ids),
        )

    @staticmethod
    def cleanup(prepared: _RunPrepared) -> None:
        crypto.cleanup_temp(prepared.temp_dir)
        if prepared.output_lock is not None:
            prepared.output_lock.release()
            prepared.output_lock = None


def build_local_update_service(
    *,
    progress: ProgressCallback | None = None,
) -> LocalUpdateService:
    """Return the shared local update service used by CLI and installer GUI."""

    return LocalUpdateService(_RunLocalUpdateRuntime(progress=progress))
