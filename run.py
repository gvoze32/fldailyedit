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
    2. Scrape transfers from Transfermarkt
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
import sys
from pathlib import Path

import config
from editor import backup as backup_mod
from editor import crypto
from editor.editfile import EditFile
from editor import logger as transfer_logger
from scraper.matcher import NameMatcher
from scraper.models import MatchedTransfer
from scraper.transfermarkt import TransfermarktScraper, fetch_all_league_transfers


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

        print("\n--- Sample teams ---")
        teams = ef.get_all_team_info()
        for i, (tid, t) in enumerate(teams.items()):
            if i >= 10:
                print(f"  ... and {len(teams) - 10} more")
                break
            roster = ef.get_team_roster(tid)
            size = roster.roster_size if roster else "?"
            print(f"  {tid}: {t.name} ({size} players)")

    finally:
        crypto.cleanup_temp(temp_dir)


def cmd_run(args):
    """Main pipeline — scrape, match, apply transfers."""
    dry_run = args.dry_run
    edit_path = Path(args.edit_file) if args.edit_file else config.EDIT_FILE_PATH
    threshold = args.threshold or config.MATCH_THRESHOLD_PLAYER

    if not dry_run and not edit_path.exists():
        print(f"Edit file not found: {edit_path}")
        print("Use --edit-file to specify the path, or set EDIT_FILE_PATH in config.py")
        sys.exit(1)

    # ── Step 1: Load league config ──
    filter_leagues = args.leagues.split(",") if args.leagues else None
    leagues = load_leagues(filter_leagues)
    if not leagues:
        print("No leagues configured. Check data/leagues.json")
        sys.exit(1)
    print(f"Leagues: {', '.join(l['name'] for l in leagues)}")

    # ── Step 2: Scrape transfers ──
    print("\n📡 Scraping Transfermarkt...")
    transfers = fetch_all_league_transfers(leagues)
    print(f"Found {len(transfers)} transfers")

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

        player_name_to_id = {p.name: pid for pid, p in players.items()}
        team_name_to_id = {t.name: tid for tid, t in teams_info.items()}

        print(f"  {len(player_name_to_id)} players, {len(team_name_to_id)} teams")

        matcher = NameMatcher()
        matcher.load_player_db(player_name_to_id)
        matcher.load_team_db(team_name_to_id)

        # ── Step 5: Match scraped names to FL26 IDs ──
        print(f"\n🔍 Matching transfers (threshold={threshold}%)...")
        matched = []
        for t in transfers:
            pid, pname, pconf = matcher.match_player(t.player_name, threshold=threshold)
            ftid, ftname, ftconf = matcher.match_team(t.from_club)
            ttid, ttname, ttconf = matcher.match_team(t.to_club)

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

        fully_matched = [m for m in matched if m.is_fully_matched]
        partial = [m for m in matched if not m.is_fully_matched]

        print(f"  ✓ Fully matched: {len(fully_matched)}")
        print(f"  ✗ Partial/unmatched: {len(partial)}")

        if partial:
            print("\n  Unmatched transfers:")
            for m in partial[:10]:
                print(f"    {m}")

        if not fully_matched:
            print("\nNo fully matched transfers to apply. Exiting.")
            return

        # ── Step 6: Apply transfers ──
        if dry_run:
            print(f"\n🔍 DRY-RUN — would apply {len(fully_matched)} transfers:")
            for m in fully_matched:
                print(f"  {m}")
                transfer_logger.log_transfer(
                    player_name=m.matched_player_name,
                    player_id=m.player_id,
                    from_team=m.matched_from_team,
                    from_team_id=m.from_team_id,
                    to_team=m.matched_to_team,
                    to_team_id=m.to_team_id,
                    confidence=m.min_confidence,
                    transfer_type=m.transfer.transfer_type,
                    dry_run=True,
                )
            print(f"\nDry-run complete. {len(fully_matched)} transfers would be applied.")
            return

        # Create backup before modifying
        print(f"\n💾 Creating backup...")
        backup_path = backup_mod.create_backup(edit_path)
        print(f"  Backup: {backup_path}")

        print(f"\n⚡ Applying {len(fully_matched)} transfers...")
        applied = 0
        failed = 0

        for m in fully_matched:
            ok = ef.move_player(m.player_id, m.from_team_id, m.to_team_id)
            if ok:
                applied += 1
                transfer_logger.log_transfer(
                    player_name=m.matched_player_name,
                    player_id=m.player_id,
                    from_team=m.matched_from_team,
                    from_team_id=m.from_team_id,
                    to_team=m.matched_to_team,
                    to_team_id=m.to_team_id,
                    confidence=m.min_confidence,
                    transfer_type=m.transfer.transfer_type,
                )
            else:
                failed += 1
                print(f"  ✗ Failed: {m.matched_player_name}")

        print(f"\n  Applied: {applied}, Failed: {failed}")

        # Save modified data.dat
        ef.save(data_dat)

        # Re-encrypt
        print(f"\n🔒 Re-encrypting → {edit_path}...")
        crypto.encrypt(temp_dir, edit_path)

        print(f"\n✅ Done! {applied} transfers applied successfully.")
        print(f"   Backup at: {backup_path}")
        print(f"   Log at: {config.TRANSFER_LOG_FILE}")

    finally:
        crypto.cleanup_temp(temp_dir)


def cmd_log(args):
    """Show recent transfer log."""
    transfer_logger.print_summary(last_n=args.last or 20)


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
    p_run.add_argument("--edit-file", type=str, help="Path to edit00000000")
    p_run.add_argument("--leagues", type=str, help="Comma-separated league names to scrape")
    p_run.add_argument("--threshold", type=float, help="Fuzzy match confidence threshold (0-100)")
    p_run.set_defaults(func=cmd_run)

    # inspect
    p_inspect = sub.add_parser("inspect", help="Inspect an edit file structure")
    p_inspect.add_argument("--edit-file", type=str, required=True, help="Path to edit00000000")
    p_inspect.set_defaults(func=cmd_inspect)

    # log
    p_log = sub.add_parser("log", help="Show recent transfer log")
    p_log.add_argument("--last", type=int, default=20, help="Number of entries to show")
    p_log.set_defaults(func=cmd_log)

    args = parser.parse_args()
    setup_logging(args.verbose)

    if not args.command:
        # Default to 'run' with remaining args
        args.command = "run"
        args.dry_run = "--dry-run" in sys.argv
        args.edit_file = None
        args.leagues = None
        args.threshold = None
        args.func = cmd_run

    args.func(args)


if __name__ == "__main__":
    main()
