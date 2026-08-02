#!/usr/bin/env python3
"""
FL26 Transfer Automation Tool — Main Entry Point

Usage:
    python run.py --dry-run                          # Scrape + match only, no file changes
    python run.py --edit-file /path/to/edit00000000  # Full pipeline
    python run.py --dry-run --leagues "Premier League,La Liga"
    python run.py --inspect --edit-file /path/to/edit00000000  # Just inspect the edit file
    python run.py --log                              # Show recent transfer log

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
    fetch_transfers_for_club_names,
    fetch_major_clubs_transfers_safely,
)
from scraper.matcher import NameMatcher
from scraper.models import MatchedTransfer

logger = logging.getLogger(__name__)


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
            sample = ", ".join(cl[:3])
            suffix = f" ... [{cl[-1]}]" if len(cl) > 3 else ""
            print(f"  Division {idx+1:2d} ({len(cl):2d} teams): {sample}{suffix}")

        print(f"\nTotal Clubs: {len([t for t in teams.values() if t.team_id > 100])}")
        print(f"Total National Teams: {len([t for t in teams.values() if t.team_id <= 100])}")
        managers = ef.get_all_managers()
        print(f"Total Managers / Coaches: {len(managers)}")

    finally:
        crypto.cleanup_temp(temp_dir)


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
        # Default behavior: write to output/EDIT00000000 to preserve sample/ input
        output_path = config.OUTPUT_FILE_PATH

    if not dry_run and not edit_path.exists():
        print(f"Edit file not found: {edit_path}")
        print("Use --edit-file to specify the path, or set EDIT_FILE_PATH in config.py")
        sys.exit(1)

    # ── Step 1: Scrape live transfers from FotMob ──
    pages = getattr(args, "pages", 10) or 10
    popular_only = getattr(args, "popular", False) or False
    window = getattr(args, "window", "auto") or "auto"
    since_date = getattr(args, "since", None)
    club_filter = getattr(args, "club", None)
    deep_mode = getattr(args, "deep", False)

    start_d, end_d = get_transfer_window_range(window)
    cutoff_info = f"since {since_date}" if since_date else f"window '{window}' (from {start_d})"
    pages = 200
    popular_only = False

    transfers_list = []
    if club_filter:
        clubs = [c.strip() for c in club_filter.split(",") if c.strip()]
        print(f"\n🎯 Scraping club-focused transfers for: {', '.join(clubs)} ({cutoff_info})...")
        transfers_list.append(fetch_transfers_for_club_names(clubs, since_date=since_date, window=window))
    elif deep_mode:
        print(f"\n🌪️ Deep Mode: Scraping transfers and squad for 150+ Major Global Clubs ({cutoff_info})...")
        transfers_list.append(fetch_major_clubs_transfers_safely(since_date=since_date, window=window))
        print(f"\n📡 Adding Live Global Feed to catch other minor leagues ({cutoff_info}, max_pages={pages})...")
        transfers_list.append(fetch_fotmob_transfers(max_pages=pages, popular_only=popular_only, since_date=since_date, window=window))
    else:
        print(f"\n⚡ Fast Mode: Scraping live transfers from FotMob ({cutoff_info}, max_pages={pages})...")
        transfers_list.append(fetch_fotmob_transfers(max_pages=pages, popular_only=popular_only, since_date=since_date, window=window))

    transfers = merge_transfers(transfers_list)
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
        player_name_to_id = {}
        for pid, p in players.items():
            name = sp_player_names.get(pid, p.name)
            player_name_to_id[name] = pid
            
        team_name_to_id = {}
        for tid, t in teams_info.items():
            if tid in club_ids:
                name = sp_team_names.get(tid, t.name)
                team_name_to_id[name] = tid

        player_positions = {pid: p.position for pid, p in players.items()}
        player_nationalities = {pid: p.nationality for pid, p in players.items() if p.nationality}
        player_ages = {pid: p.age for pid, p in players.items() if p.age}

        print(f"  {len(player_name_to_id)} players, {len(team_name_to_id)} playable clubs (national teams excluded)")

        matcher = NameMatcher()
        matcher.load_player_db(
            player_name_to_id,
            positions=player_positions,
            nationalities=player_nationalities,
            ages=player_ages,
        )
        matcher.load_team_db(team_name_to_id)

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

        club_transfers = [m for m in matched if m.is_club_transfer]
        releases = [m for m in matched if m.is_release]
        signings = [m for m in matched if m.is_sign]
        fully_matched = [m for m in matched if m.is_fully_matched]
        partial = [m for m in matched if not m.is_fully_matched]

        print(f"  ✓ Fully actionable: {len(fully_matched)} (Club Transfers: {len(club_transfers)}, Departures to Free Agent: {len(releases)}, Signings: {len(signings)})")
        print(f"  ✗ Unmatched: {len(partial)}")

        if partial:
            print("\n  Unmatched transfers (sample):")
            for m in partial[:10]:
                print(f"    {m}")

        if not fully_matched:
            print("\nNo fully matched transfers to apply. Exiting.")
            return

        # ── Step 6: Apply transfers ──
        run_records = []
        if dry_run:
            print(f"\n🔍 DRY-RUN — would apply {len(fully_matched)} transfers:")
            for m in fully_matched:
                print(f"  {m}")
                transfer_logger.log_transfer(
                    player_name=m.matched_player_name or m.transfer.player_name,
                    player_id=m.player_id,
                    from_team=m.matched_from_team or m.transfer.from_club,
                    from_team_id=m.from_team_id or 0,
                    to_team=m.matched_to_team or m.transfer.to_club,
                    to_team_id=m.to_team_id or 0,
                    confidence=m.min_confidence,
                    transfer_type=m.transfer.transfer_type,
                    dry_run=True,
                    position=m.transfer.position,
                    fee=m.transfer.fee,
                    market_value=m.transfer.market_value,
                )
                run_records.append({
                    "player_name": m.matched_player_name or m.transfer.player_name,
                    "from_team": m.matched_from_team or m.transfer.from_club,
                    "to_team": m.matched_to_team or m.transfer.to_club,
                    "position": m.transfer.position,
                    "fee": m.transfer.fee,
                    "transfer_type": m.transfer.transfer_type,
                    "confidence": m.min_confidence,
                    "dry_run": True,
                })
            transfer_logger.save_reports(run_records)
            print(f"\nDry-run complete. {len(fully_matched)} transfers validated and logged.")
            return

        # Create backup before modifying
        print(f"\n💾 Creating backup...")
        backup_path = backup_mod.create_backup(edit_path)
        print(f"  Backup: {backup_path}")

        print(f"\n⚡ Applying {len(fully_matched)} transfers with Smart Shirt Numbers & Game Plan Doctor...")
        applied = 0
        failed = 0

        # Sort transfers: releases first, then club transfers, then signings to avoid slot overflow
        fully_matched.sort(key=lambda m: 0 if m.is_release else (1 if m.is_club_transfer else 2))

        for m in fully_matched:
            pid = m.player_id
            to_tid = m.to_team_id
            t = m.transfer
            
            # Auto-create player if missing (placeholder)
            if pid is None:
                continue
                
            is_squad_update = (t.transfer_type == "squad_update")
            if is_squad_update:
                if to_tid and t.shirt_number is not None:
                    if ef.update_player_shirt_number(to_tid, pid, t.shirt_number):
                        applied += 1
                continue
                
            ok = False
            pref_shirt = m.transfer.shirt_number
            if m.is_club_transfer:
                to_roster = ef.get_team_roster(m.to_team_id)
                if to_roster and to_roster.has_player(m.player_id):
                    ok = True
                else:
                    ok = ef.move_player(m.player_id, m.from_team_id, m.to_team_id, shirt_number=pref_shirt)
                    if not ok:
                        current_tid = ef.find_player_team(m.player_id, club_only=True)
                        if current_tid == m.to_team_id:
                            ok = True
                        elif current_tid is not None:
                            ok = ef.move_player(m.player_id, current_tid, m.to_team_id, shirt_number=pref_shirt)
                        else:
                            ok = ef.add_player(m.player_id, m.to_team_id, shirt_number=pref_shirt)
            elif m.is_release:
                from_roster = ef.get_team_roster(m.from_team_id)
                if from_roster and not from_roster.has_player(m.player_id):
                    ok = True
                else:
                    ok = ef.release_player(m.player_id, m.from_team_id)
                    if not ok:
                        current_tid = ef.find_player_team(m.player_id, club_only=True)
                        if current_tid is not None:
                            ok = ef.release_player(m.player_id, current_tid)
                        else:
                            ok = True
            elif m.is_sign:
                to_roster = ef.get_team_roster(m.to_team_id)
                if to_roster and to_roster.has_player(m.player_id):
                    ok = True
                else:
                    ok = ef.add_player(m.player_id, m.to_team_id, shirt_number=pref_shirt)
                    if not ok:
                        current_tid = ef.find_player_team(m.player_id, club_only=True)
                        if current_tid == m.to_team_id:
                            ok = True
                        elif current_tid is not None:
                            ok = ef.move_player(m.player_id, current_tid, m.to_team_id, shirt_number=pref_shirt)

            if ok:
                applied += 1
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
                )
                run_records.append({
                    "player_name": m.matched_player_name or m.transfer.player_name,
                    "from_team": m.matched_from_team or m.transfer.from_club,
                    "to_team": m.matched_to_team or m.transfer.to_club,
                    "position": m.transfer.position,
                    "fee": m.transfer.fee,
                    "transfer_type": m.transfer.transfer_type,
                    "confidence": m.min_confidence,
                    "dry_run": False,
                })
            else:
                failed += 1
                print(f"  ✗ Failed: {m.matched_player_name or m.transfer.player_name} ({m.action_type})")

        print(f"\n  Applied: {applied}, Failed: {failed}")

        # Save modified data.dat
        ef.save(data_dat)

        # Re-encrypt
        output_path.parent.mkdir(parents=True, exist_ok=True)
        print(f"\n🔒 Re-encrypting → {output_path}...")
        crypto.encrypt(temp_dir, output_path)

        # Save visual reports
        transfer_logger.save_reports(run_records)

        print(f"\n✅ Done! {applied} transfers applied successfully.")
        if output_path.resolve() != edit_path.resolve():
            print(f"   Input (sample/pristine): {edit_path}")
            print(f"   Output (updated save):   {output_path}")
        else:
            print(f"   Updated file:            {output_path}")
        print(f"   Backup at:               {backup_path}")
        print(f"   Log at:                  {config.TRANSFER_LOG_FILE}")
        print(f"   Visual Summary Report:   {config.OUTPUT_DIR / 'transfer_summary.md'}")
        print(f"   Backup at: {backup_path}")
        print(f"   Log at: {config.TRANSFER_LOG_FILE}")

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
    p_run.add_argument("--edit-file", type=str, help="Path to input edit00000000 (default: config.EDIT_FILE_PATH or sample/EDIT00000000)")
    p_run.add_argument("-o", "--output", type=str, help="Path to output updated edit00000000 (default: output/EDIT00000000)")
    p_run.add_argument("--in-place", action="store_true", help="Overwrite input edit file in-place instead of writing to output/")
    p_run.add_argument("--club", type=str, help="Comma-separated club names to focus scrape (e.g. 'Chelsea,Arsenal')")
    p_run.add_argument("--deep", action="store_true", help="Deep fetch across all 5,700+ Global Clubs directly")
    p_run.add_argument("--window", type=str, choices=["auto", "summer", "winter", "all"], default="auto", help="Transfer window (default: auto)")
    p_run.add_argument("--since", type=str, help="Scrape transfers since date (YYYY-MM-DD)")
    p_run.add_argument("--threshold", type=float, help="Fuzzy match confidence threshold (0-100)")
    p_run.set_defaults(func=cmd_run)

    # schedule
    p_sched = sub.add_parser("schedule", help="Run transfers continuously on a timer")
    p_sched.add_argument("--interval-hours", type=float, default=6.0, help="Interval between runs in hours (default: 6.0)")
    p_sched.add_argument("--dry-run", action="store_true", help="Don't modify the edit file")
    p_sched.add_argument("--edit-file", type=str, help="Path to input edit00000000")
    p_sched.add_argument("-o", "--output", type=str, help="Path to output updated edit00000000 (default: output/EDIT00000000)")
    p_sched.add_argument("--in-place", action="store_true", help="Overwrite input edit file in-place")
    p_sched.add_argument("--club", type=str, help="Comma-separated club names to focus scrape (e.g. 'Chelsea,Arsenal')")
    p_sched.add_argument("--deep", action="store_true", help="Deep fetch across all 5,700+ Global Clubs directly")
    p_sched.add_argument("--window", type=str, choices=["auto", "summer", "winter", "all"], default="auto", help="Transfer window (default: auto)")
    p_sched.add_argument("--since", type=str, help="Scrape transfers since date (YYYY-MM-DD)")
    p_sched.add_argument("--threshold", type=float, help="Fuzzy match confidence threshold (0-100)")
    p_sched.set_defaults(func=cmd_schedule)

    # cron
    p_cron = sub.add_parser("cron", help="Generate crontab line for automated scheduling")
    p_cron.add_argument("--interval-hours", type=int, default=6, help="Interval in hours (default: 6)")
    p_cron.set_defaults(func=cmd_cron)

    # inspect
    p_inspect = sub.add_parser("inspect", help="Inspect an edit file structure")
    p_inspect.add_argument("--edit-file", type=str, required=True, help="Path to edit00000000")
    p_inspect.set_defaults(func=cmd_inspect)

    # Pre-parse argv: if first arg is a flag or omitted, default to 'run'
    subcommands = {"run", "schedule", "cron", "inspect", "log", "-h", "--help"}
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
