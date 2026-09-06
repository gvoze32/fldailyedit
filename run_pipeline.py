from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import date
import hashlib
import json
import logging
from pathlib import Path

import config
import native_metadata
import transfer_planning as planning
from editor import backup as backup_mod
from editor import crypto
from editor.editfile import EditFile
from editor import logger as transfer_logger
from editor.locking import EditFileLock
from editor.player_catalog import PlayerCatalogError, load_id_name_text
from editor.release_policy import ReleasePolicyError, load_release_policy
from scraper.fotmob import (
    IncompleteScrapeError,
    fetch_fotmob_transfers,
    get_transfer_window_range,
    merge_transfers,
    fetch_transfers_for_club_names,
    fetch_squads_for_club_names,
    fetch_major_clubs_transfers_safely,
)
from scraper.besoccer import fetch_besoccer_transfers
from scraper.matcher import NameMatcher
from scraper.sortitoutsi import fetch_sortitoutsi_transfers
from scraper.soccerway import fetch_soccerway_transfers
from scraper.sofascore import fetch_sofascore_transfers
from scraper.sources import reconcile_transfer_sources
from scraper.models import CaptainUpdate, ScrapeResult
from scraper.wikipedia import fetch_wikipedia_transfers
from scraper.transfermarkt import fetch_transfermarkt_transfers
from local_update import (
    CancellationToken,
    LocalUpdateError,
    LocalUpdateRequest,
    LocalUpdateResult,
    LocalUpdateService,
    LocalUpdateStage,
)

logger = logging.getLogger(__name__)
_FAST_SQUAD_CLUB_LIMIT = 32


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
def _load_represented_fotmob_club_map() -> dict[int, int]:
    """Load the generated one-to-one FotMob ↔ PES club identity index."""
    validated_path = config.DATA_DIR / "fotmob_teams_validated.json"
    try:
        validated_payload = json.loads(validated_path.read_text(encoding="utf-8"))
        if not isinstance(validated_payload, list):
            raise ValueError(f"{validated_path} must contain a JSON array")
    except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        raise IncompleteScrapeError(
            f"Could not load FotMob/PES club identity data: {exc}"
        ) from exc

    represented: dict[int, int] = {}
    represented_pes_ids: set[int] = set()
    for item in validated_payload:
        if (
            not isinstance(item, dict)
            or "fotmob_id" not in item
            or "pes_team_id" not in item
        ):
            raise IncompleteScrapeError(
                f"Malformed club identity entry in {validated_path}"
            )
        try:
            fotmob_id = int(item["fotmob_id"])
            pes_team_id = int(item["pes_team_id"])
        except (TypeError, ValueError) as exc:
            raise IncompleteScrapeError(
                f"Non-numeric club identity in {validated_path}: {exc}"
            ) from exc
        if fotmob_id in represented or pes_team_id in represented_pes_ids:
            raise IncompleteScrapeError(
                f"Club identity index is not one-to-one at FotMob {fotmob_id} / "
                f"PES {pes_team_id}"
            )
        represented[fotmob_id] = pes_team_id
        represented_pes_ids.add(pes_team_id)

    if not represented:
        raise IncompleteScrapeError("Represented FotMob club ID allowlist is empty")
    return represented


def _load_represented_fotmob_club_ids() -> set[int]:
    """Return the validated FotMob club IDs used by the transfer guard."""
    return set(_load_represented_fotmob_club_map())

def _default_transfer_since_date(
    window: str,
    since_date: str | None,
) -> str | None:
    """Avoid replaying stale history in default global runs."""
    if since_date is not None or (window or "auto").casefold() != "auto":
        return since_date
    return date.today().replace(month=1, day=1).isoformat()


def _supplemental_target_clubs(transfer_batches) -> tuple[str, ...]:
    """Return relevant clubs used to filter supplemental transfer routes."""
    non_clubs = {
        "",
        "career break",
        "free agent",
        "retired",
        "unattached",
        "without club",
    }
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
                if destination.casefold() in non_clubs
                else destination
            )
            key = target.casefold()
            if key and key not in non_clubs and key not in seen:
                seen.add(key)
                targets.append(target)
    return tuple(targets)

def _fast_squad_target_clubs(transfer_batches) -> tuple[str, ...]:
    """Return recent transfer clubs whose current squads should be refreshed."""
    non_clubs = {
        "",
        "career break",
        "free agent",
        "retired",
        "unattached",
        "without club",
    }
    targets: list[str] = []
    seen: set[str] = set()
    for batch in transfer_batches:
        for transfer in batch:
            for candidate in (
                transfer.to_club_full_name or transfer.to_club,
                transfer.from_club_full_name or transfer.from_club,
            ):
                clean = candidate.strip()
                key = clean.casefold()
                if (
                    not clean
                    or key in non_clubs
                    or key in seen
                ):
                    continue
                seen.add(key)
                targets.append(clean)
                if len(targets) >= _FAST_SQUAD_CLUB_LIMIT:
                    return tuple(targets)
    return tuple(targets)






def _scrape_run_transfers(args):
    """Fetch, merge, order, and preview transfers for one pipeline run."""
    popular_only = bool(getattr(args, "popular", False))
    window = getattr(args, "window", "auto") or "auto"
    since_date = getattr(args, "since", None)
    club_filter = getattr(args, "club", None)
    deep_mode = bool(getattr(args, "deep", False))
    fotmob_only = bool(getattr(args, "fotmob_only", False))
    scrape_since_date = (
        since_date
        if club_filter
        else _default_transfer_since_date(window, since_date)
    )

    start_date, end_date = get_transfer_window_range(window)
    cutoff_info = (
        f"since {scrape_since_date}"
        if scrape_since_date
        else f"window '{window}' ({start_date} to {end_date or 'latest'})"
    )
    transfer_batches = []
    captain_updates: list[CaptainUpdate] = []
    if club_filter:
        clubs = [club.strip() for club in club_filter.split(",") if club.strip()]
        print(
            f"\n🎯 Scraping club-focused transfers for: {', '.join(clubs)} "
            f"({cutoff_info})..."
        )
        club_batch = fetch_transfers_for_club_names(
            clubs,
            since_date=since_date,
            window=window,
        )
        transfer_batches.append(club_batch)
        captain_updates.extend(getattr(club_batch, "captain_updates", ()))
    elif deep_mode:
        print(
            "\n🌪️ Deep Mode: Scraping transfers and squads for indexed clubs "
            f"({cutoff_info})..."
        )
        deep_batch = fetch_major_clubs_transfers_safely(
            since_date=scrape_since_date,
            window=window,
        )
        transfer_batches.append(deep_batch)
        deep_captains = getattr(deep_batch, "captain_updates", ())
        captain_updates.extend(deep_captains)
        print(f"  Deep captain sync found {len(deep_captains)} markers")
        print(
            "\n📡 Adding Live Global Feed to catch other minor leagues "
            f"({cutoff_info}, automatic pagination)..."
        )
        transfer_batches.append(
            fetch_fotmob_transfers(
                popular_only=popular_only,
                since_date=scrape_since_date,
                window=window,
            )
        )
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

        squad_targets = _fast_squad_target_clubs((live_transfers,))
        if squad_targets:
            print(
                "\n👕 Fast Mode: Refreshing current squad numbers and captains for "
                f"{len(squad_targets)} affected clubs..."
            )
            try:
                squad_updates = fetch_squads_for_club_names(list(squad_targets))
            except IncompleteScrapeError as error:
                logger.warning("Fast squad sync skipped: %s", error)
                squad_updates = []
            transfer_batches.append(squad_updates)
            fast_captains = getattr(squad_updates, "captain_updates", ())
            captain_updates.extend(fast_captains)
            print(f"  Squad sync found {len(squad_updates)} shirt numbers")
            print(f"  Captain sync found {len(fast_captains)} markers")
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
            since_date=scrape_since_date or start_date
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
    print(f"Current captain markers to process: {len(captain_updates)}")
    return ScrapeResult(transfers, captain_updates)

def _load_match_database(
    edit_file: EditFile,
    release_policy_file: str | Path | None = None,
):
    """Build roster-aware player and club indexes from one validated save."""
    print("\n📋 Reading selected save database...")
    playerbin_database, playerbin_source = native_metadata._load_playerbin_database()
    edit_file.playerbin_source = playerbin_source
    attach_playerbin = getattr(edit_file, "attach_playerbin", None)
    if playerbin_database is not None and callable(attach_playerbin):
        attach_playerbin(playerbin_database)
        print(f"  Loaded Player.bin metadata from {playerbin_source}")
    teambin_database, teambin_source = native_metadata._load_teambin_database()
    edit_file.teambin_source = teambin_source
    if teambin_database is not None:
        edit_file.attach_teambin(teambin_database)
        print(f"  Loaded Team.bin metadata from {teambin_source}")
    assignment_database, assignment_source = native_metadata._load_player_assignment_database()
    edit_file.player_assignment_source = assignment_source
    if assignment_database is not None:
        edit_file.attach_player_assignment(assignment_database)
        print(
            "  Loaded PlayerAssignment.bin metadata "
            f"from {assignment_source}"
        )
    players = edit_file.get_all_players()
    edit_file._player_cache = players
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
    teams_info = edit_file.get_all_team_info()
    club_ids = edit_file.get_club_team_ids()

    catalog_report = getattr(edit_file, "player_catalog_report", None)
    current_catalog_entries = (
        getattr(catalog_report, "current_entries", None)
        if catalog_report is not None
        else None
    )
    # The team reference is tied to the current player reference. If that
    # SPFL catalog is unavailable, ignore any bundled team file as well: it
    # may describe a different base than a ULM/vanilla save.
    use_external_team_names = (
        current_catalog_entries is None or current_catalog_entries > 0
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

    team_name_to_id = {
        current_team_names.get(team_id, team.name): team_id
        for team_id, team in teams_info.items()
        if team_id in club_ids
    }
    matcher = NameMatcher()
    matcher.load_player_db(
        [(player.name, player_id) for player_id, player in players.items()],
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
    # The save's league memberships already filter national teams. Numeric
    # club-ID heuristics are invalid for FL26 (some real clubs have low IDs).
    matcher.load_team_db(team_name_to_id, clubs_only=False)

    all_rosters = edit_file.get_all_rosters()
    team_player_map = {
        team_id: roster.roster for team_id, roster in all_rosters.items()
    }
    if current_catalog_entries == 0:
        print("  ⚠ External player catalog unavailable; using names from selected save")
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
    allow_overflow_release,
):
    """Match scraped identities, classify them, and create safe roster actions."""

    print(
        "\n🔍 Matching transfers with roster-aware identity verification "
        f"(threshold={threshold}%)..."
    )
    save_scope = str(output_path.resolve())
    historical_entries = transfer_logger.read_log(
        save_scope=save_scope,
        include_legacy=(output_path.resolve() == config.OUTPUT_FILE_PATH.resolve()),
    )
    validated_fotmob_teams = _load_represented_fotmob_club_map()
    matched = planning._match_transfers_statefully(
        transfers,
        matcher,
        threshold,
        team_player_map,
        club_ids,
        historical_entries=historical_entries,
        validated_fotmob_ids=set(validated_fotmob_teams),
        validated_fotmob_teams=validated_fotmob_teams,
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
    if partial:
        print("\n  Unmatched transfers (preview):")
        for match in partial[:10]:
            print(f"    {match}")
    return roster_plan, fully_matched, save_scope

def _plan_captain_updates(
    captain_updates: list[CaptainUpdate] | tuple[CaptainUpdate, ...],
    matcher: NameMatcher,
    team_player_map: dict[int, list[int]],
    club_ids: set[int],
    validated_fotmob_teams: dict[int, int],
    threshold: float,
) -> tuple[_PlannedCaptainUpdate, ...]:
    """Resolve live captain markers to fail-closed local roster targets."""
    by_team: dict[int, CaptainUpdate] = {}
    for source in captain_updates:
        team_id = validated_fotmob_teams.get(source.team_id_fotmob)
        if team_id is None or team_id not in club_ids:
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
    captain_plan=(),
) -> None:
    """Render planned roster and captain actions without mutating."""

    print("\n🔍 DRY-RUN — checking each match against the current roster:")
    would_apply = 0
    already_current = 0
    safety_skipped = 0
    shirt_statuses = _plan_shirt_number_batch(edit_file, roster_plan)
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
    for planned_captain in captain_plan:
        current_player_id = edit_file.get_team_captain_player(
            planned_captain.team_id
        )
        if current_player_id == planned_captain.player_id:
            already_current += 1
            print(
                f"  ALREADY CURRENT CAPTAIN: {planned_captain.source.club_name} "
                f"→ {planned_captain.matched_player_name}"
            )
            continue

        roster = edit_file.get_team_roster(planned_captain.team_id)
        if roster is None or roster.player_ids.count(planned_captain.player_id) != 1:
            safety_skipped += 1
            print(
                f"  SAFETY SKIP CAPTAIN ({planned_captain.source.club_name}): "
                f"{planned_captain.matched_player_name} is not in the current roster"
            )
            continue

        would_apply += 1
        print(
            f"  WOULD SET CAPTAIN: {planned_captain.source.club_name} → "
            f"{planned_captain.matched_player_name}"
        )
    print(
        f"\nDry-run complete. Would apply: {would_apply}, "
        f"already current: {already_current}, safety-skipped: {safety_skipped}. "
        "No files were written."
    )


def _plan_shirt_number_batch(
    edit_file: EditFile,
    actions: list[planning.PlannedRosterAction],
) -> dict[int, tuple[int | None, int | None, str]]:
    """Classify shirt updates so planned number swaps can be applied together."""
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
        candidates: list[planning.PlannedRosterAction] = []
        by_target: dict[int, list[planning.PlannedRosterAction]] = {}
        for item in group:
            match = item.match
            previous, _, _ = statuses[id(item)]
            target = match.transfer.shirt_number
            if target is None or previous == target:
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

        roster = edit_file.get_team_roster(team_id)
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
        self.roster_plan = ()
        self.captain_plan: tuple[_PlannedCaptainUpdate, ...] = ()
        self.save_scope = str(output_path.resolve())
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


class _RunMutation:
    def __init__(
        self,
        *,
        transfer_applied: int,
        shirt_numbers_changed: int,
        unchanged: int,
        safety_skipped: int,
        captains_changed: int = 0,
    ) -> None:
        self.transfer_applied = transfer_applied
        self.shirt_numbers_changed = shirt_numbers_changed
        self.unchanged = unchanged
        self.safety_skipped = safety_skipped
        self.captains_changed = captains_changed


class _RunLocalUpdateRuntime:
    """Adapter from the shared service lifecycle to the verified edit-file pipeline."""

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

    @staticmethod
    def _release_lock(prepared: _RunPrepared) -> None:
        if prepared.output_lock is not None:
            prepared.output_lock.release()
            prepared.output_lock = None

    def scrape(
        self,
        request: LocalUpdateRequest,
        _token: CancellationToken,
    ):
        if not request.dry_run and not request.edit_path.exists():
            raise LocalUpdateError(
                "missing_input",
                f"Edit file not found: {request.edit_path}",
                stage=LocalUpdateStage.SCRAPING,
            )
        return _scrape_run_transfers(self._args(request))

    def validate_and_prepare(
        self,
        request: LocalUpdateRequest,
        _transfers,
        _token: CancellationToken,
    ) -> _RunPrepared:
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
            if not data_dat.exists():
                dat_files = list(temp_dir.glob("*.dat"))
                if not dat_files:
                    raise LocalUpdateError(
                        "invalid_save",
                        f"Decryption produced no data block in {temp_dir}",
                        stage=LocalUpdateStage.VALIDATING,
                    )
                data_dat = max(dat_files, key=lambda path: path.stat().st_size)

            edit_file = EditFile()
            edit_file.load(data_dat)
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
        try:
            if request.release_policy_file is None:
                matcher, all_rosters, team_player_map, club_ids = (
                    _load_match_database(prepared.edit_file)
                )
            else:
                matcher, all_rosters, team_player_map, club_ids = (
                    _load_match_database(
                        prepared.edit_file,
                        request.release_policy_file,
                    )
                )
            baseline_integrity = prepared.edit_file.validate_integrity()
            prepared.pre_mutation_integrity_errors = tuple(
                str(error) for error in baseline_integrity.get("errors", [])
            )
            match_threshold = request.threshold or config.MATCH_THRESHOLD_PLAYER
            roster_plan, fully_matched, save_scope = _match_and_plan_transfers(
                transfers,
                matcher,
                match_threshold,
                team_player_map,
                all_rosters,
                club_ids,
                prepared.edit_file,
                prepared.output_path,
                allow_overflow_release=request.allow_overflow_release,
            )
            captain_sources = getattr(transfers, "captain_updates", ())
            if captain_sources:
                prepared.captain_plan = _plan_captain_updates(
                    captain_sources,
                    matcher,
                    team_player_map,
                    club_ids,
                    _load_represented_fotmob_club_map(),
                    match_threshold,
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

    def apply(
        self,
        request: LocalUpdateRequest,
        prepared: _RunPrepared,
        _plan,
        token: CancellationToken,
    ):
        captain_plan = tuple(getattr(prepared, "captain_plan", ()))
        captain_getter = getattr(
            prepared.edit_file,
            "get_team_captain_player",
            None,
        )
        captain_actionable = bool(captain_plan) and (
            not callable(captain_getter)
            or any(
                captain_getter(item.team_id) != item.player_id
                for item in captain_plan
            )
        )
        actionable_roster = any(
            item.action in {"move", "add", "release", "shirt_update"}
            for item in prepared.roster_plan
        )
        repair_game_plans = getattr(prepared.edit_file, "repair_game_plans", None)
        repair_metrics = (
            repair_game_plans()
            if not actionable_roster and callable(repair_game_plans)
            else {}
        )
        gameplan_changed = any(
            repair_metrics.get(key, 0)
            for key in (
                "repaired_lineups",
                "repaired_goalkeeper_roles",
                "repaired_position_bytes",
                "reset_roles",
            )
        )
        if not actionable_roster and not gameplan_changed and not captain_actionable:
            unchanged = sum(
                item.action == "noop" for item in prepared.roster_plan
            )
            safety_skipped = sum(
                item.action == "skip" for item in prepared.roster_plan
            )
            print("\nNo effective transfer, shirt-number, or captain changes to apply. Exiting.")
            return LocalUpdateResult(
                target_path=prepared.output_path,
                backup_path=None,
                installed_sha256=None,
                transfer_applied=0,
                shirt_numbers_changed=0,
                unchanged=unchanged,
                safety_skipped=safety_skipped,
                no_changes=True,
            )
        if gameplan_changed:
            print(
                "\n🧭 Repairing game-plan lineup mappings: "
                f"{repair_metrics.get('repaired_lineups', 0)} lineups, "
                f"{repair_metrics.get('repaired_goalkeeper_roles', 0)} goalkeeper roles, "
                f"{repair_metrics.get('repaired_position_bytes', 0)} position bytes"
            )

        token.raise_if_cancelled()
        print("\n💾 Creating backup...")
        try:
            prepared.backup_path = backup_mod.create_backup(prepared.edit_path)
        except Exception as error:
            raise LocalUpdateError(
                "backup_failed",
                f"Backup failed: {error}",
                stage=LocalUpdateStage.APPLYING,
            ) from error
        print(f"  Backup: {prepared.backup_path}")

        print("\n⚡ Applying verified transfers, shirt-number, and captain changes...")
        transfer_applied = 0
        shirt_numbers_applied = 0
        captains_changed = 0
        unchanged = 0
        safety_skipped = 0
        original_data = prepared.original_data
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
                            f"({match.action_type}); entire batch rolled back",
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
                )
            elif action == "add":
                ok = prepared.edit_file.add_player(
                    player_id,
                    to_team_id,
                    shirt_number=preferred_shirt,
                    position=transfer.position,
                    allow_overflow_release=request.allow_overflow_release,
                )
            elif action == "release":
                ok = prepared.edit_file.release_player(
                    player_id,
                    match.from_team_id,
                    position=transfer.position,
                )

            if not ok:
                prepared.edit_file._data = bytearray(original_data)
                raise LocalUpdateError(
                    "apply_failed",
                    f"Failed: {match.matched_player_name or transfer.player_name} "
                    f"({match.action_type}); entire batch rolled back",
                    stage=LocalUpdateStage.APPLYING,
                )

            if action == "shirt_update":
                shirt_numbers_applied += 1
            else:
                transfer_applied += 1
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



        if callable(repair_game_plans) and actionable_roster:
            repair_metrics = repair_game_plans()
        if actionable_roster:
            repaired_roles = repair_metrics.get("repaired_goalkeeper_roles", 0)
            repaired_lineups = repair_metrics.get("repaired_lineups", 0)
            repaired_positions = repair_metrics.get("repaired_position_bytes", 0)
            if repaired_roles or repaired_lineups or repaired_positions:
                print(
                    "  Game-plan repairs: "
                    f"{repaired_lineups} lineups, {repaired_roles} goalkeeper roles, "
                    f"{repaired_positions} position bytes"
                )
        captain_setter = getattr(prepared.edit_file, "set_team_captain", None)
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
            f"already current: {unchanged}, safety-skipped: {safety_skipped}"
        )
        return _RunMutation(
            transfer_applied=transfer_applied,
            shirt_numbers_changed=shirt_numbers_applied,
            unchanged=unchanged,
            safety_skipped=safety_skipped,
            captains_changed=captains_changed,
        )

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
            transfer_log_content = transfer_logger.save_reports(report_records)
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
        )

    def preview(
        self,
        _request: LocalUpdateRequest,
        prepared: _RunPrepared,
        plan,
        _token: CancellationToken,
    ) -> LocalUpdateResult:
        _print_dry_run(
            prepared.edit_file,
            plan[0] if isinstance(plan, tuple) else plan,
            getattr(prepared, "captain_plan", ()),
        )
        return LocalUpdateResult(
            target_path=prepared.output_path,
            backup_path=None,
            installed_sha256=None,
            transfer_applied=0,
            shirt_numbers_changed=0,
            unchanged=0,
            safety_skipped=0,
            no_changes=True,
        )

    @staticmethod
    def cleanup(prepared: _RunPrepared) -> None:
        crypto.cleanup_temp(prepared.temp_dir)
        if prepared.output_lock is not None:
            prepared.output_lock.release()
            prepared.output_lock = None


def build_local_update_service() -> LocalUpdateService:
    """Return the shared local update service used by CLI and installer GUI."""

    return LocalUpdateService(_RunLocalUpdateRuntime())
