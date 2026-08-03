#!/usr/bin/env python3
"""
FL26 Transfer Automation Tool — Main Entry Point

Usage:
    python run.py run --dry-run                       # Scrape + match only, no file changes
    python run.py run --edit-file /path/to/EDIT00000000
    python run.py inspect --edit-file /path/to/EDIT00000000
    python run.py validate --edit-file /path/to/EDIT00000000
    python run.py log                                 # Show recent transfer log

Workflow:
    1. Load league config from data/leagues.json
    2. Scrape live transfers from FotMob API
    3. Decrypt the edit file (pesXdecrypter)
    4. Read FL26 player/team database from decrypted data
    5. Match scraped names to FL26 IDs (fuzzy matching)
    6. Apply transfers (move player IDs between teams)
    7. Re-encrypt and save
    8. Log everything
"""
import argparse
import json
import logging
import struct
import sys
import time
from collections import Counter
from datetime import date
from pathlib import Path

import config
from editor import backup as backup_mod
from editor import crypto
from editor.editfile import EditFile
from editor import logger as transfer_logger
from scraper.fotmob import (
    fetch_fotmob_transfers,
    get_transfer_window_range,
    merge_transfers,
    parse_iso_date,
    fetch_transfers_for_club_names,
    fetch_major_clubs_transfers_safely,
)
from scraper.matcher import NameMatcher
from scraper.models import MatchedTransfer

logger = logging.getLogger(__name__)


def _transfer_sort_key(transfer):
    """Apply dated transfers chronologically and shirt-number updates last."""
    parsed_date = parse_iso_date(transfer.date)
    return (
        transfer.transfer_type == "shirt_number_update",
        parsed_date is None,
        parsed_date or parse_iso_date("2000-01-01"),
    )


def _decide_roster_action(
    current_team_id: int | None,
    from_team_id: int | None,
    to_team_id: int | None,
    transfer_type: str,
    superseded_loan_team_ids: frozenset[int] = frozenset(),
) -> str:
    """Choose a fail-closed roster mutation from the verified current state."""
    if transfer_type == "shirt_number_update":
        return "shirt_update" if to_team_id is not None and current_team_id == to_team_id else "skip"

    if from_team_id is not None and to_team_id is not None:
        if current_team_id == to_team_id:
            return "noop"
        if (
            current_team_id == from_team_id
            or current_team_id in superseded_loan_team_ids
        ):
            return "move"
        return "skip"

    if from_team_id is None and to_team_id is not None:
        if current_team_id == to_team_id:
            return "noop"
        if current_team_id is None:
            return "add"
        return "skip"

    if from_team_id is not None and to_team_id is None:
        if current_team_id == from_team_id:
            return "release"
        if current_team_id is None:
            return "noop"
        return "skip"

    return "skip"


def _build_superseded_loan_sources(
    matches: list[MatchedTransfer],
) -> dict[int, frozenset[int]]:
    """Authorize newer parent-club moves from an earlier loan destination.

    Transfer feeds commonly omit the synthetic loan-return event. For example,
    PSG -> Tottenham (loan) followed by PSG -> Juventus (permanent) leaves a
    current PES roster at Tottenham even though the newer event names PSG as
    its source. Only a strictly earlier, fully matched loan from that same
    parent club can authorize the stale loan club as the actual move source.
    """
    prior_loans: dict[int, list[tuple[int, int, date]]] = {}
    allowed_sources: dict[int, frozenset[int]] = {}

    for match in matches:
        player_id = match.player_id
        transfer_date = parse_iso_date(match.transfer.date)
        if (
            player_id is not None
            and match.from_team_id is not None
            and match.to_team_id is not None
            and transfer_date is not None
            and (match.transfer.is_loan or match.transfer.transfer_type == "loan")
        ):
            prior_loans.setdefault(player_id, []).append(
                (match.from_team_id, match.to_team_id, transfer_date)
            )

    for match in matches:
        player_id = match.player_id
        transfer_date = parse_iso_date(match.transfer.date)
        if (
            player_id is not None
            and match.from_team_id is not None
            and match.to_team_id is not None
            and transfer_date is not None
        ):
            allowed_sources[id(match)] = frozenset(
                loan_team_id
                for parent_team_id, loan_team_id, loan_date in prior_loans.get(
                    player_id, []
                )
                if parent_team_id == match.from_team_id
                and loan_team_id != match.to_team_id
                and loan_date < transfer_date
            )

    return allowed_sources


def _dedupe_shirt_number_matches(
    matches: list[MatchedTransfer],
) -> tuple[list[MatchedTransfer], int]:
    """Keep one fail-closed shirt-number observation per player and club."""
    regular: list[MatchedTransfer] = []
    groups: dict[tuple[int, int], list[MatchedTransfer]] = {}

    for match in matches:
        if (
            match.transfer.transfer_type != "shirt_number_update"
            or match.player_id is None
            or match.to_team_id is None
        ):
            regular.append(match)
            continue
        groups.setdefault((match.player_id, match.to_team_id), []).append(match)

    skipped = 0
    for group in groups.values():
        ranked = sorted(group, key=lambda item: item.min_confidence, reverse=True)
        winner = ranked[0]
        conflicting = [
            item
            for item in ranked[1:]
            if item.transfer.shirt_number != winner.transfer.shirt_number
        ]
        if conflicting and winner.min_confidence - conflicting[0].min_confidence < 3.0:
            skipped += len(group)
            continue
        regular.append(winner)
        skipped += len(group) - 1

    return regular, skipped


def _iso_date_arg(value: str) -> str:
    parsed = parse_iso_date(value)
    if parsed is None or value != parsed.isoformat():
        raise argparse.ArgumentTypeError("expected an ISO date in YYYY-MM-DD format")
    return value


def _percentage_arg(value: str) -> float:
    try:
        number = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected a number from 0 to 100") from exc
    if not 0 <= number <= 100:
        raise argparse.ArgumentTypeError("expected a number from 0 to 100")
    return number


def _positive_int_arg(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected a positive integer") from exc
    if number < 1:
        raise argparse.ArgumentTypeError("expected a positive integer")
    return number


def _positive_float_arg(value: str) -> float:
    try:
        number = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected a positive number") from exc
    if number <= 0:
        raise argparse.ArgumentTypeError("expected a positive number")
    return number


def setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def load_leagues(filter_names: list[str] | None = None) -> list[dict]:
    """Load league config from JSON, optionally filtering by name."""
    with open(config.LEAGUES_FILE, "r") as f:
        leagues = json.load(f)

    if filter_names:
        filter_lower = [n.lower().strip() for n in filter_names]
        leagues = [l for l in leagues if l["name"].lower() in filter_lower]

    return leagues


def cmd_inspect(args):
    """Inspect an edit file — show structure, counts, offsets."""
    edit_path = Path(args.edit_file)

    print(f"Decrypting {edit_path}...")
    try:
        temp_dir = crypto.decrypt(edit_path)
    except Exception as e:
        print(f"Decryption failed: {e}")
        print("Make sure pesXdecrypter is installed. See MEMORY.md §4.")
        sys.exit(1)

    try:
        data_dat = temp_dir / "data.dat"
        if not data_dat.exists():
            dat_files = list(temp_dir.glob("*.dat"))
            if dat_files:
                data_dat = max(dat_files, key=lambda f: f.stat().st_size)
            else:
                print(f"No .dat files found in {temp_dir}")
                sys.exit(1)

        ef = EditFile()
        ef.load(data_dat)
        ef.print_summary()

        integrity = ef.validate_integrity()
        status = "PASS" if integrity["valid"] else "FAIL"
        print(f"\nIntegrity: {status}")
        print(f"  Metrics: {integrity['metrics']}")
        for error in integrity["errors"][:20]:
            print(f"  ERROR: {error}")
        if len(integrity["errors"]) > 20:
            print(f"  ... and {len(integrity['errors']) - 20} more errors")

        print("\n--- League Divisions in Save File ---")
        teams = ef.get_all_team_info()
        entry_start = 0xA08650
        entry_size = 0x1230
        num_slots = entry_size // 4

        clusters = []
        current_cluster = []
        for i in range(num_slots):
            tid = struct.unpack_from("<I", ef._data, entry_start + i * 4)[0]
            if tid != 0 and tid != 0xFFFF0300 and tid in teams:
                current_cluster.append(teams[tid].name)
            else:
                if current_cluster:
                    clusters.append(current_cluster)
                    current_cluster = []
        if current_cluster:
            clusters.append(current_cluster)

        for idx, cl in enumerate(clusters):
            preview = ", ".join(cl[:3])
            suffix = f" ... [{cl[-1]}]" if len(cl) > 3 else ""
            print(f"  Division {idx+1:2d} ({len(cl):2d} teams): {preview}{suffix}")

        club_ids = ef.get_club_team_ids()
        print(f"\nTotal Clubs: {len(club_ids)}")
        print(f"Total Other/National Teams: {len(set(teams) - club_ids)}")
        managers = ef.get_all_managers()
        print(f"Total Managers / Coaches: {len(managers)}")

    finally:
        crypto.cleanup_temp(temp_dir)


def cmd_validate(args):
    """Validate an encrypted FL26 edit file and return a shell-friendly status."""
    edit_path = Path(args.edit_file)
    print(f"Validating {edit_path}...")
    temp_dir = crypto.decrypt(edit_path)
    try:
        ef = EditFile(temp_dir / "data.dat")
        ef.load()
        report = ef.validate_integrity()
        print(f"Metrics: {report['metrics']}")
        for warning in report["warnings"]:
            print(f"WARNING: {warning}")
        for error in report["errors"]:
            print(f"ERROR: {error}")
        if report["valid"]:
            print("PASS: save structure matches known-good Football Life 2026 files")
            return
        print(f"FAIL: {len(report['errors'])} integrity error(s)")
        raise SystemExit(2)
    finally:
        crypto.cleanup_temp(temp_dir)


def cmd_repair(args):
    """Repair a legacy base using consensus registrations from valid references."""
    edit_path = Path(args.edit_file)
    output_path = Path(args.output) if args.output else config.OUTPUT_FILE_PATH
    reference_paths = [Path(path) for path in args.reference]

    base_temp = crypto.decrypt(edit_path)
    reference_temps: list[Path] = []
    try:
        ef = EditFile(base_temp / "data.dat")
        ef.load()
        league_block_start = ef.game_plan_start
        league_block_end = league_block_start + 0x1230
        original_league_block = bytes(ef._data[league_block_start:league_block_end])

        references: list[EditFile] = []
        for reference_path in reference_paths:
            reference_temp = crypto.decrypt(reference_path)
            reference_temps.append(reference_temp)
            reference = EditFile(reference_temp / "data.dat")
            reference.load()
            report = reference.validate_integrity()
            if not report["valid"]:
                raise ValueError(
                    f"Reference is not structurally valid: {reference_path} "
                    f"({len(report['errors'])} errors)"
                )
            references.append(reference)

        base_clubs = ef.get_club_team_ids()
        registrations: dict[int, list[int]] = {}
        for tid, roster in ef.get_all_rosters().items():
            if tid not in base_clubs:
                continue
            for player_id in roster.roster:
                registrations.setdefault(player_id, []).append(tid)
        duplicates = {
            player_id: teams
            for player_id, teams in registrations.items()
            if len(teams) > 1
        }

        repaired_duplicates = 0
        released_to_free_agent = 0
        players = ef.get_all_players()
        for player_id, current_teams in sorted(duplicates.items()):
            votes: list[int | None] = []
            for reference in references:
                reference_teams = reference.find_player_teams(player_id, club_only=True)
                if len(reference_teams) > 1:
                    raise ValueError(
                        f"Reference unexpectedly registers player {player_id} to multiple clubs"
                    )
                votes.append(reference_teams[0] if reference_teams else None)

            preferred_team, vote_count = Counter(votes).most_common(1)[0]
            if vote_count <= len(references) // 2:
                raise ValueError(
                    f"References do not agree how to repair player {player_id}: {votes}"
                )

            for team_id in list(current_teams):
                if team_id != preferred_team and not ef.release_player(player_id, team_id):
                    raise RuntimeError(f"Could not remove duplicate player {player_id} from {team_id}")

            if preferred_team is None:
                released_to_free_agent += 1
            elif preferred_team not in ef.find_player_teams(player_id, club_only=True):
                player = players.get(player_id)
                if not ef.add_player(
                    player_id,
                    preferred_team,
                    position=player.position if player else "",
                ):
                    raise RuntimeError(
                        f"Could not place duplicate player {player_id} on consensus team "
                        f"{preferred_team}"
                    )
            repaired_duplicates += 1

        game_plan_repairs = ef.repair_game_plans()
        if bytes(ef._data[league_block_start:league_block_end]) != original_league_block:
            raise RuntimeError("Repair attempted to change league promotion/division membership")

        final_report = ef.validate_integrity()
        if not final_report["valid"]:
            for error in final_report["errors"][:20]:
                print(f"ERROR: {error}")
            raise RuntimeError(
                f"Repair did not produce a valid save ({len(final_report['errors'])} errors remain)"
            )

        ef.save(base_temp / "data.dat")
        replaced_output_backup = None
        if output_path.exists():
            replaced_output_backup = backup_mod.create_backup(output_path)
        crypto.encrypt(base_temp, output_path)
        print(f"PASS: repaired legacy base → {output_path}")
        if replaced_output_backup is not None:
            print(f"  Previous output backup: {replaced_output_backup}")
        print(f"  Duplicate player registrations repaired: {repaired_duplicates}")
        print(f"  Players released by reference consensus: {released_to_free_agent}")
        print(f"  Game-plan repair: {game_plan_repairs}")
        print("  League/division membership: preserved byte-for-byte from the legacy base")
    finally:
        for reference_temp in reference_temps:
            crypto.cleanup_temp(reference_temp)
        crypto.cleanup_temp(base_temp)


def cmd_run(args):
    """Main pipeline — scrape, match, apply transfers."""
    dry_run = args.dry_run
    edit_path = Path(args.edit_file) if args.edit_file else config.EDIT_FILE_PATH
    threshold = args.threshold or config.MATCH_THRESHOLD_PLAYER

    output_arg = getattr(args, "output", None)
    in_place = getattr(args, "in_place", False)

    if output_arg:
        output_path = Path(output_arg)
    elif in_place:
        output_path = edit_path
    else:
        # Default behavior: write to output/EDIT00000000 to preserve base/ input
        output_path = config.OUTPUT_FILE_PATH

    if not dry_run and not edit_path.exists():
        print(f"Edit file not found: {edit_path}")
        print("Use --edit-file to specify the path, or set EDIT_FILE_PATH in config.py")
        sys.exit(1)

    # ── Step 1: Scrape live transfers from FotMob ──
    popular_only = getattr(args, "popular", False) or False
    window = getattr(args, "window", "auto") or "auto"
    since_date = getattr(args, "since", None)
    club_filter = getattr(args, "club", None)
    deep_mode = getattr(args, "deep", False)

    start_d, end_d = get_transfer_window_range(window)
    cutoff_info = (
        f"since {since_date}"
        if since_date
        else f"window '{window}' ({start_d} to {end_d or 'latest'})"
    )
    transfers_list = []
    if club_filter:
        clubs = [c.strip() for c in club_filter.split(",") if c.strip()]
        print(f"\n🎯 Scraping club-focused transfers for: {', '.join(clubs)} ({cutoff_info})...")
        transfers_list.append(fetch_transfers_for_club_names(clubs, since_date=since_date, window=window))
    elif deep_mode:
        print(f"\n🌪️ Deep Mode: Scraping transfers and squads for indexed clubs ({cutoff_info})...")
        transfers_list.append(fetch_major_clubs_transfers_safely(since_date=since_date, window=window))
        print(f"\n📡 Adding Live Global Feed to catch other minor leagues ({cutoff_info}, automatic pagination)...")
        transfers_list.append(fetch_fotmob_transfers(popular_only=popular_only, since_date=since_date, window=window))
    else:
        print(f"\n⚡ Fast Mode: Scraping live transfers from FotMob ({cutoff_info}, automatic pagination)...")
        transfers_list.append(fetch_fotmob_transfers(popular_only=popular_only, since_date=since_date, window=window))

    transfers = merge_transfers(transfers_list)
    # FotMob returns recent activity first. Apply historical moves oldest to
    # newest so the final roster reflects the latest destination. Current
    # squad shirt-number updates intentionally run last.
    transfers.sort(key=_transfer_sort_key)
    print(f"  FotMob found {len(transfers)} total unique verified transfers")

    print(f"\nTotal unique transfers to process: {len(transfers)}")

    if not transfers:
        print("No transfers found. Exiting.")
        return

    for t in transfers[:5]:
        print(f"  {t}")
    if len(transfers) > 5:
        print(f"  ... and {len(transfers) - 5} more")

    # ── Step 3: Decrypt and load edit file ──
    if dry_run and not edit_path.exists():
        print("\n⚠ Dry-run mode without edit file — showing scraped data only.")
        print(f"\nAll {len(transfers)} transfers:")
        for t in transfers:
            print(f"  {t}")
        return

    print(f"\n🔓 Decrypting {edit_path}...")
    try:
        temp_dir = crypto.decrypt(edit_path)
    except Exception as e:
        print(f"Decryption failed: {e}")
        sys.exit(1)

    try:
        data_dat = temp_dir / "data.dat"
        if not data_dat.exists():
            dat_files = list(temp_dir.glob("*.dat"))
            data_dat = max(dat_files, key=lambda f: f.stat().st_size)

        ef = EditFile()
        ef.load(data_dat)

        integrity = ef.validate_integrity()
        if not integrity["valid"]:
            print("\n❌ Input save failed FL26 integrity validation; no changes will be written.")
            for error in integrity["errors"][:20]:
                print(f"  - {error}")
            remaining = len(integrity["errors"]) - 20
            if remaining > 0:
                print(f"  ... and {remaining} more errors")
            print("Use a known-good Football Life 2026 EDIT00000000 as --edit-file.")
            sys.exit(2)

        # ── Step 4: Build name databases ──
        print("\n📋 Reading FL26 database...")
        players = ef.get_all_players()
        teams_info = ef.get_all_team_info()
        club_ids = ef.get_club_team_ids()

        # Try to load official Smokepatch names to get full names instead of PES short names
        sp_players_path = Path("data/FL2622wc_players.txt")
        sp_teams_path = Path("data/FL262_teams.txt")
        
        sp_player_names = {}
        if sp_players_path.exists():
            with open(sp_players_path, "r", encoding="utf-8") as f:
                for line in f:
                    if "-" in line:
                        parts = line.split("-", 1)
                        if parts[0].strip().isdigit():
                            sp_player_names[int(parts[0].strip())] = parts[1].strip()
                            
        sp_team_names = {}
        if sp_teams_path.exists():
            with open(sp_teams_path, "r", encoding="utf-8") as f:
                for line in f:
                    if "-" in line:
                        parts = line.split("-", 1)
                        if parts[0].strip().isdigit():
                            sp_team_names[int(parts[0].strip())] = parts[1].strip()

        # Build name dictionaries, preferring Smokepatch full names if available
        player_records = []
        for pid, p in players.items():
            name = sp_player_names.get(pid, p.name)
            player_records.append((name, pid))
            
        team_name_to_id = {}
        for tid, t in teams_info.items():
            if tid in club_ids:
                name = sp_team_names.get(tid, t.name)
                team_name_to_id[name] = tid

        player_positions = {pid: p.position for pid, p in players.items()}
        player_nationalities = {pid: p.nationality for pid, p in players.items() if p.nationality}
        player_ages = {pid: p.age for pid, p in players.items() if p.age}

        print(f"  {len(player_records)} players, {len(team_name_to_id)} playable clubs (national teams excluded)")

        matcher = NameMatcher()
        matcher.load_player_db(
            player_records,
            positions=player_positions,
            nationalities=player_nationalities,
            ages=player_ages,
        )
        # team_name_to_id is already filtered through the save's league
        # memberships. Do not apply the legacy ID<=100 heuristic: FL26 has
        # real clubs in that numeric range (for example Manchester United).
        matcher.load_team_db(team_name_to_id, clubs_only=False)

        # Build team_player_map for context-aware disambiguation
        all_rosters = ef.get_all_rosters()
        team_player_map = {
            tid: roster.roster
            for tid, roster in all_rosters.items()
        }

        # ── Step 5: Match scraped names to FL26 IDs ──
        print(f"\n🔍 Matching transfers with Tri-Factor verification (threshold={threshold}%)...")
        matched = []
        for t in transfers:
            ftid, ftname, ftconf = matcher.match_team(t.from_club)
            ttid, ttname, ttconf = matcher.match_team(t.to_club)
            pid, pname, pconf = matcher.match_player(
                t.player_name,
                threshold=threshold,
                from_team_id=ftid,
                to_team_id=ttid,
                team_player_map=team_player_map,
                position=t.position,
                nationality=t.nationality,
                age=t.age,
            )

            mt = MatchedTransfer(
                transfer=t,
                player_id=pid,
                from_team_id=ftid,
                to_team_id=ttid,
                player_confidence=pconf,
                from_team_confidence=ftconf,
                to_team_confidence=ttconf,
                matched_player_name=pname,
                matched_from_team=ftname,
                matched_to_team=ttname,
            )
            matched.append(mt)

        matched, duplicate_shirt_matches = _dedupe_shirt_number_matches(matched)
        superseded_loan_sources = _build_superseded_loan_sources(matched)
        if duplicate_shirt_matches:
            print(
                f"  ⚠ Skipped {duplicate_shirt_matches} duplicate or ambiguous "
                "shirt-number matches"
            )

        shirt_matches = [
            m for m in matched
            if m.transfer.transfer_type == "shirt_number_update" and m.is_fully_matched
        ]
        club_transfers = [
            m for m in matched
            if m.transfer.transfer_type != "shirt_number_update" and m.is_club_transfer
        ]
        releases = [
            m for m in matched
            if m.transfer.transfer_type != "shirt_number_update" and m.is_release
        ]
        signings = [
            m for m in matched
            if m.transfer.transfer_type != "shirt_number_update" and m.is_sign
        ]
        fully_matched = [m for m in matched if m.is_fully_matched]
        partial = [m for m in matched if not m.is_fully_matched]

        print(
            f"  ✓ Fully actionable: {len(fully_matched)} "
            f"(Club Transfers: {len(club_transfers)}, "
            f"Departures: {len(releases)}, Signings: {len(signings)}, "
            f"Shirt Number Checks: {len(shirt_matches)})"
        )
        print(f"  ✗ Unmatched: {len(partial)}")

        if partial:
            print("\n  Unmatched transfers (preview):")
            for m in partial[:10]:
                print(f"    {m}")

        if not fully_matched:
            print("\nNo fully matched transfers to apply. Exiting.")
            return

        # ── Step 6: Apply transfers ──
        run_records = []
        if dry_run:
            print("\n🔍 DRY-RUN — checking each match against the current roster:")
            would_apply = 0
            already_current = 0
            safety_skipped = 0
            for m in fully_matched:
                current_clubs = ef.find_player_teams(m.player_id, club_only=True)
                if len(current_clubs) > 1:
                    safety_skipped += 1
                    print(f"  SAFETY SKIP (duplicate registration): {m}")
                    continue
                current_tid = current_clubs[0] if current_clubs else None
                action = _decide_roster_action(
                    current_tid,
                    m.from_team_id,
                    m.to_team_id,
                    m.transfer.transfer_type,
                    superseded_loan_sources.get(id(m), frozenset()),
                )
                if action == "skip":
                    safety_skipped += 1
                    print(
                        f"  SAFETY SKIP (current={current_tid}, "
                        f"source={m.from_team_id}, destination={m.to_team_id}): {m}"
                    )
                    continue
                if action == "noop" or (
                    action == "shirt_update" and m.transfer.shirt_number is None
                ):
                    already_current += 1
                    print(f"  ALREADY CURRENT: {m}")
                    continue

                if action == "shirt_update":
                    current_shirt = ef.get_player_shirt_number(
                        m.to_team_id, m.player_id
                    )
                    if current_shirt == m.transfer.shirt_number:
                        already_current += 1
                        continue

                would_apply += 1
                print(f"  WOULD {action.upper()}: {m}")
            print(
                f"\nDry-run complete. Would apply: {would_apply}, "
                f"already current: {already_current}, safety-skipped: {safety_skipped}. "
                "No files were written."
            )
            return

        # Create backup before modifying
        print(f"\n💾 Creating backup...")
        backup_path = backup_mod.create_backup(edit_path)
        print(f"  Backup: {backup_path}")

        print(f"\n⚡ Applying verified transfers and shirt-number changes...")
        applied = 0
        transfer_applied = 0
        shirt_numbers_applied = 0
        unchanged = 0
        failed = 0
        original_data = bytes(ef._data)
        pending_logs = []

        for m in fully_matched:
            pid = m.player_id
            to_tid = m.to_team_id
            t = m.transfer
            
            # Auto-create player if missing (placeholder)
            if pid is None:
                continue
                
            current_clubs = ef.find_player_teams(pid, club_only=True)
            if len(current_clubs) > 1:
                failed += 1
                print(f"  ✗ Skipped {m.matched_player_name or t.player_name}: player is in multiple clubs {current_clubs}")
                continue
            current_tid = current_clubs[0] if current_clubs else None
            action = _decide_roster_action(
                current_tid,
                m.from_team_id,
                to_tid,
                t.transfer_type,
                superseded_loan_sources.get(id(m), frozenset()),
            )
            if action == "skip":
                failed += 1
                print(
                    f"  ✗ Safety skip {m.matched_player_name or t.player_name}: "
                    f"current={current_tid}, expected source={m.from_team_id}, destination={to_tid}"
                )
                continue
            if action == "noop":
                unchanged += 1
                continue

            ok = False
            pref_shirt = t.shirt_number
            previous_shirt = None
            if action == "shirt_update":
                if pref_shirt is None:
                    unchanged += 1
                    continue
                previous_shirt = ef.get_player_shirt_number(to_tid, pid)
                if previous_shirt == pref_shirt:
                    unchanged += 1
                    continue
                ok = ef.update_player_shirt_number(to_tid, pid, pref_shirt)
            elif action == "move":
                ok = ef.move_player(
                    pid,
                    current_tid,
                    to_tid,
                    shirt_number=pref_shirt,
                    position=t.position,
                )
            elif action == "add":
                ok = ef.add_player(
                    pid,
                    to_tid,
                    shirt_number=pref_shirt,
                    position=t.position,
                )
            elif action == "release":
                ok = ef.release_player(pid, m.from_team_id)

            if ok:
                applied += 1
                if action == "shirt_update":
                    shirt_numbers_applied += 1
                else:
                    transfer_applied += 1
                pending_logs.append((m, previous_shirt, action))
                run_records.append({
                    "player_name": m.matched_player_name or m.transfer.player_name,
                    "from_team": m.matched_from_team or m.transfer.from_club,
                    "to_team": m.matched_to_team or m.transfer.to_club,
                    "position": m.transfer.position,
                    "fee": m.transfer.fee,
                    "transfer_type": m.transfer.transfer_type,
                    "confidence": m.min_confidence,
                    "dry_run": False,
                    "previous_shirt_number": previous_shirt,
                    "shirt_number": pref_shirt if action == "shirt_update" else None,
                    "roster_action": action,
                })
            else:
                failed += 1
                print(f"  ✗ Failed: {m.matched_player_name or m.transfer.player_name} ({m.action_type})")

        print(
            f"\n  Transfers applied: {transfer_applied}, "
            f"shirt numbers changed: {shirt_numbers_applied}, "
            f"already current: {unchanged}, failed/skipped: {failed}"
        )

        post_integrity = ef.validate_integrity()
        if not post_integrity["valid"]:
            ef._data = bytearray(original_data)
            print("\n❌ Modified save failed integrity validation; changes were rolled back.")
            for error in post_integrity["errors"][:20]:
                print(f"  - {error}")
            remaining = len(post_integrity["errors"]) - 20
            if remaining > 0:
                print(f"  ... and {remaining} more errors")
            sys.exit(2)

        # Save modified data.dat
        ef.save(data_dat)

        # Re-encrypt
        output_path.parent.mkdir(parents=True, exist_ok=True)
        print(f"\n🔒 Re-encrypting → {output_path}...")
        crypto.encrypt(temp_dir, output_path)

        # Persist audit entries only after the binary passed validation and
        # verified encryption round-trip.
        for m, previous_shirt, action in pending_logs:
            transfer_logger.log_transfer(
                player_name=m.matched_player_name or m.transfer.player_name,
                player_id=m.player_id,
                from_team=m.matched_from_team or m.transfer.from_club,
                from_team_id=m.from_team_id or 0,
                to_team=m.matched_to_team or m.transfer.to_club,
                to_team_id=m.to_team_id or 0,
                confidence=m.min_confidence,
                transfer_type=m.transfer.transfer_type,
                dry_run=False,
                position=m.transfer.position,
                fee=m.transfer.fee,
                market_value=m.transfer.market_value,
                previous_shirt_number=previous_shirt,
                shirt_number=(
                    m.transfer.shirt_number
                    if m.transfer.transfer_type == "shirt_number_update"
                    else None
                ),
                roster_action=action,
            )

        # Save visual reports
        transfer_logger.save_reports(run_records)

        print(
            f"\n✅ Done! {transfer_applied} transfers applied; "
            f"{shirt_numbers_applied} shirt numbers changed."
        )
        if output_path.resolve() != edit_path.resolve():
            print(f"   Input (base/pristine):   {edit_path}")
            print(f"   Output (updated save):   {output_path}")
        else:
            print(f"   Updated file:            {output_path}")
        print(f"   Backup at:               {backup_path}")
        print(f"   Log at:                  {config.TRANSFER_LOG_FILE}")
        print(f"   Visual Summary Report:   {config.OUTPUT_DIR / 'transfer_summary.md'}")

    finally:
        crypto.cleanup_temp(temp_dir)


def cmd_log(args):
    """Show recent transfer log."""
    transfer_logger.print_summary(last_n=args.last or 20)


def cmd_schedule(args):
    """Run transfers on a recurring interval."""
    interval_sec = int(args.interval_hours * 3600)
    print(f"⏰ Starting transfer automation scheduler (interval: every {args.interval_hours} hours)...")
    print("Press Ctrl+C to stop.")

    iteration = 1
    while True:
        print(f"\n--- [Scheduler Run #{iteration}] {time.strftime('%Y-%m-%d %H:%M:%S')} ---")
        try:
            cmd_run(args)
        except Exception as e:
            logger.error(f"Scheduler run #{iteration} failed: {e}", exc_info=True)
            print(f"✗ Run #{iteration} encountered an error: {e}")

        iteration += 1
        print(f"\n💤 Sleeping for {args.interval_hours} hours until next run...")
        try:
            time.sleep(interval_sec)
        except KeyboardInterrupt:
            print("\nScheduler stopped by user.")
            break


def cmd_cron(args):
    """Generate or install crontab entry for automated transfers."""
    py_path = sys.executable
    script_path = (Path(__file__).parent / "run.py").resolve()
    cwd_path = Path(__file__).parent.resolve()
    
    interval_hours = args.interval_hours or 6
    cron_expr = f"0 */{interval_hours} * * *"
    cron_line = f"{cron_expr} cd {cwd_path} && {py_path} {script_path} run >> {config.DATA_DIR}/cron.log 2>&1"

    print("\n📅 Automated Cron Configuration")
    print("================================")
    print(f"Schedule: Every {interval_hours} hours (`{cron_expr}`)")
    print(f"\nCrontab entry:\n\n  {cron_line}\n")
    print("To install automatically, run:")
    print(f'  (crontab -l 2>/dev/null; echo "{cron_line}") | crontab -')


def main():
    parser = argparse.ArgumentParser(
        description="FL26 Transfer Automation Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")

    sub = parser.add_subparsers(dest="command")

    # run (default)
    p_run = sub.add_parser("run", help="Scrape + match + apply transfers")
    p_run.add_argument("--dry-run", action="store_true", help="Don't modify the edit file")
    p_run.add_argument("--edit-file", type=str, help="Path to input EDIT00000000 (default: base/EDIT00000000)")
    p_run.add_argument("-o", "--output", type=str, help="Path to output updated edit00000000 (default: output/EDIT00000000)")
    p_run.add_argument("--in-place", action="store_true", help="Overwrite input edit file in-place instead of writing to output/")
    p_run.add_argument("--club", type=str, help="Comma-separated club names to focus scrape (e.g. 'Chelsea,Arsenal')")
    p_run.add_argument("--deep", action="store_true", help="Deep fetch across all locally indexed FotMob clubs")
    p_run.add_argument("--window", type=str, choices=["auto", "summer", "winter", "all"], default="auto", help="Transfer window (default: auto)")
    p_run.add_argument("--since", type=_iso_date_arg, help="Scrape transfers since date (YYYY-MM-DD)")
    p_run.add_argument("--threshold", type=_percentage_arg, help="Fuzzy match confidence threshold (0-100)")
    p_run.add_argument("--popular", action="store_true", help="Only request FotMob popular transfers")
    p_run.set_defaults(func=cmd_run)

    # schedule
    p_sched = sub.add_parser("schedule", help="Run transfers continuously on a timer")
    p_sched.add_argument("--interval-hours", type=_positive_float_arg, default=6.0, help="Interval between runs in hours (default: 6.0)")
    p_sched.add_argument("--dry-run", action="store_true", help="Don't modify the edit file")
    p_sched.add_argument("--edit-file", type=str, help="Path to input edit00000000")
    p_sched.add_argument("-o", "--output", type=str, help="Path to output updated edit00000000 (default: output/EDIT00000000)")
    p_sched.add_argument("--in-place", action="store_true", help="Overwrite input edit file in-place")
    p_sched.add_argument("--club", type=str, help="Comma-separated club names to focus scrape (e.g. 'Chelsea,Arsenal')")
    p_sched.add_argument("--deep", action="store_true", help="Deep fetch across all locally indexed FotMob clubs")
    p_sched.add_argument("--window", type=str, choices=["auto", "summer", "winter", "all"], default="auto", help="Transfer window (default: auto)")
    p_sched.add_argument("--since", type=_iso_date_arg, help="Scrape transfers since date (YYYY-MM-DD)")
    p_sched.add_argument("--threshold", type=_percentage_arg, help="Fuzzy match confidence threshold (0-100)")
    p_sched.add_argument("--popular", action="store_true", help="Only request FotMob popular transfers")
    p_sched.set_defaults(func=cmd_schedule)

    # cron
    p_cron = sub.add_parser("cron", help="Generate crontab line for automated scheduling")
    p_cron.add_argument("--interval-hours", type=_positive_int_arg, default=6, help="Interval in hours (default: 6)")
    p_cron.set_defaults(func=cmd_cron)

    # inspect
    p_inspect = sub.add_parser("inspect", help="Inspect an edit file structure")
    p_inspect.add_argument("--edit-file", type=str, required=True, help="Path to edit00000000")
    p_inspect.set_defaults(func=cmd_inspect)

    # validate
    p_validate = sub.add_parser("validate", help="Validate an encrypted FL26 edit file")
    p_validate.add_argument("--edit-file", type=str, required=True, help="Path to edit00000000")
    p_validate.set_defaults(func=cmd_validate)

    # repair
    p_repair = sub.add_parser(
        "repair",
        help="Repair a legacy base without importing reference league memberships",
    )
    p_repair.add_argument("--edit-file", type=str, required=True, help="Legacy base EDIT00000000")
    p_repair.add_argument(
        "--reference",
        type=str,
        action="append",
        required=True,
        help="Known-good reference EDIT00000000; repeat for consensus",
    )
    p_repair.add_argument("-o", "--output", type=str, help="Repaired output path")
    p_repair.set_defaults(func=cmd_repair)

    # log
    p_log = sub.add_parser("log", help="Show recent transfer log")
    p_log.add_argument("--last", type=int, default=20, help="Number of recent entries (default: 20)")
    p_log.set_defaults(func=cmd_log)

    # Pre-parse argv: if first arg is a flag or omitted, default to 'run'
    subcommands = {
        "run", "schedule", "cron", "inspect", "validate", "repair", "log", "-h", "--help"
    }
    if len(sys.argv) > 1 and sys.argv[1] not in subcommands:
        sys.argv.insert(1, "run")
    elif len(sys.argv) == 1:
        sys.argv.append("run")

    args = parser.parse_args()
    setup_logging(args.verbose)

    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
