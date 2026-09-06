#!/usr/bin/env python3
"""
FL Daily Edit — Main Entry Point

Usage:
    python run.py run --dry-run                       # Preview all changes; write nothing
    python run.py run --edit-file /path/to/EDIT00000000
    python run.py inspect --edit-file /path/to/EDIT00000000
    python run.py audit --edit-file /path/to/EDIT00000000 --json
    python run.py compare --left-cpk /path/to/data_s2526.cpk --right-cpk /path/to/data_extra.cpk --json
    python run.py validate --edit-file /path/to/EDIT00000000
    python run.py log                                 # Show recent transfer log

Workflow:
    1. Collect and reconcile FotMob with verified and corroboration-only
       supplemental transfer sources
    2. Decrypt and validate the edit file (pesXdecrypter)
    3. Load the selected save’s current player/roster state
    4. Match identities and plan safe roster actions
    5. Apply verified transfers, validate, re-encrypt, and log
"""
import argparse
import json
import logging
import shlex
import struct
import sys
import time
from collections import Counter
from datetime import date
from pathlib import Path

import native_metadata
import run_pipeline as pipeline
import config
from editor import backup as backup_mod
from editor import crypto
from editor.editfile import EditFile
from editor.roster import COMPETITION_SECTION_SIZE
from editor import logger as transfer_logger
from editor.player_catalog import PlayerCatalogError
from editor.metadata_audit import audit_metadata, format_metadata_audit
from editor.release_policy import ReleasePolicyError, import_usage_csv
from editor.metadata_diff import (
    compare_metadata_variants,
    format_metadata_variant_diff,
)
from editor.player_assignment import PlayerAssignmentDatabase
from editor.playerbin import PlayerBinDatabase
from editor.teambin import TeamBinDatabase
from editor.locking import EditLockError
from installer.paths import DestinationError, discover_game_cpk, reject_game_root_save
from tools.cpk_extract import read_file as read_cpk_file
from scraper.fotmob import IncompleteScrapeError, parse_iso_date
from local_update import LocalUpdateError, LocalUpdateRequest
logger = logging.getLogger(__name__)


def _resolve_run_paths(args) -> tuple[Path, Path]:
    """Resolve an incremental input and output without discarding prior runs."""
    explicit_input = Path(args.edit_file) if getattr(args, "edit_file", None) else None
    output_arg = getattr(args, "output", None)
    in_place = bool(getattr(args, "in_place", False))
    from_base = bool(getattr(args, "from_base", False))

    if output_arg:
        output_path = Path(output_arg)
    elif in_place:
        output_path = explicit_input or config.EDIT_FILE_PATH
    else:
        output_path = config.OUTPUT_FILE_PATH

    if explicit_input is not None:
        edit_path = explicit_input
    elif from_base or in_place:
        edit_path = config.EDIT_FILE_PATH
    elif output_path.exists():
        # Continue from the last successful output. Re-reading the pristine base
        # on every scheduled run would silently undo transfers that aged out of
        # the current scrape window.
        edit_path = output_path
    else:
        edit_path = config.EDIT_FILE_PATH

    reject_game_root_save(edit_path)
    reject_game_root_save(output_path)
    return edit_path, output_path
def _ensure_safe_edit_paths(*paths: Path) -> None:
    """Fail closed before any command reads or writes a game-root save."""
    try:
        for path in paths:
            reject_game_root_save(path)
    except DestinationError as error:
        print(f"Unsafe save path: {error}")
        raise SystemExit(2) from error





def _competition_section_bounds(edit_file: EditFile) -> tuple[int, int]:
    """Return the league-membership section without overlapping game plans."""
    start = edit_file.competition_entry_start
    return start, start + COMPETITION_SECTION_SIZE






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


def cmd_inspect(args):
    """Inspect an edit file — show structure, counts, offsets."""
    edit_path = Path(args.edit_file)
    _ensure_safe_edit_paths(edit_path)

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
        entry_start, entry_end = _competition_section_bounds(ef)
        entry_size = entry_end - entry_start
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




def cmd_metadata_audit(args):
    """Audit one save against the selected native game metadata variant."""
    edit_path = Path(args.edit_file)
    _ensure_safe_edit_paths(edit_path)
    json_output = bool(getattr(args, "json", False))
    if not json_output:
        print(f"Decrypting {edit_path}...")
    try:
        temp_dir = crypto.decrypt(edit_path)
    except Exception as exc:
        print(f"Decryption failed: {exc}")
        print("Make sure pesXdecrypter is installed. See MEMORY.md §4.")
        sys.exit(1)

    try:
        data_dat = temp_dir / "data.dat"
        if not data_dat.exists():
            dat_files = list(temp_dir.glob("*.dat"))
            if dat_files:
                data_dat = max(dat_files, key=lambda path: path.stat().st_size)
            else:
                print(f"No .dat files found in {temp_dir}")
                sys.exit(1)

        edit_file = EditFile()
        edit_file.load(data_dat)
        game_root = getattr(args, "game_root", None)
        playerbin_db, playerbin_source = native_metadata._load_playerbin_database(
            getattr(args, "player_bin", None),
            game_root=game_root,
        )
        teambin_db, teambin_source = native_metadata._load_teambin_database(
            getattr(args, "team_bin", None),
            game_root=game_root,
        )
        assignment_db, assignment_source = native_metadata._load_player_assignment_database(
            getattr(args, "player_assignment", None),
            game_root=game_root,
        )
        as_of = date.fromisoformat(
            getattr(args, "as_of", None) or date.today().isoformat()
        )
        report = audit_metadata(
            edit_file,
            playerbin_db,
            teambin_db,
            assignment_db,
            as_of=as_of,
        )
        sources = {
            "Player.bin": playerbin_source,
            "Team.bin": teambin_source,
            "PlayerAssignment.bin": assignment_source,
        }
        if json_output:
            payload = report.to_dict()
            payload["sources"] = sources
            print(json.dumps(payload, sort_keys=True))
        else:
            print(format_metadata_audit(report))
            print("\nSources:")
            for label, source in sources.items():
                print(f"  {label}: {source or 'unavailable'}")
    finally:
        crypto.cleanup_temp(temp_dir)




def cmd_usage_import(args) -> None:
    """Merge CSV player usage counters into the offline release policy."""
    try:
        policy = import_usage_csv(
            args.input,
            getattr(args, "output", None),
            source=getattr(args, "source", ""),
            season=getattr(args, "season", ""),
            as_of=getattr(args, "as_of", ""),
        )
        print(
            f"Imported {len(policy.usage)} usage snapshots into "
            f"{getattr(args, 'output', None) or config.RELEASE_POLICY_FILE}"
        )
    except ReleasePolicyError as exc:
        print(f"Usage import failed: {exc}")
        raise SystemExit(2) from exc
def _resolve_comparison_cpk(
    explicit_path: Path | str | None,
    game_root: Path | str | None,
    label: str,
) -> Path:
    if explicit_path is not None:
        path = Path(explicit_path)
        if not path.is_file():
            raise FileNotFoundError(f"{label} CPK not found: {path}")
        return path
    path = discover_game_cpk(None if game_root is None else Path(game_root))
    if path is None:
        raise FileNotFoundError(
            f"{label} CPK not found below {game_root or 'configured game roots'}"
        )
    return path


def _load_metadata_variant_from_cpk(path: Path):
    def load(database_type, member: str):
        return database_type.from_bytes(read_cpk_file(path, member))

    return (
        load(PlayerBinDatabase, _PLAYER_BIN_CPK_MEMBER),
        load(TeamBinDatabase, _TEAM_BIN_CPK_MEMBER),
        load(PlayerAssignmentDatabase, _PLAYER_ASSIGNMENT_CPK_MEMBER),
    )


def cmd_compare_metadata(args):
    """Compare supported native metadata between two CPK variants."""
    try:
        left_path = _resolve_comparison_cpk(
            getattr(args, "left_cpk", None),
            getattr(args, "left_game_root", None),
            "Left",
        )
        right_path = _resolve_comparison_cpk(
            getattr(args, "right_cpk", None),
            getattr(args, "right_game_root", None),
            "Right",
        )
        left_databases = _load_metadata_variant_from_cpk(left_path)
        right_databases = _load_metadata_variant_from_cpk(right_path)
        report = compare_metadata_variants(
            str(left_path),
            *left_databases,
            str(right_path),
            *right_databases,
        )
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise SystemExit(f"Metadata comparison failed: {exc}") from exc

    if getattr(args, "json", False):
        print(json.dumps(report.to_dict(), sort_keys=True))
    else:
        print(format_metadata_variant_diff(report))




def cmd_validate(args):
    """Validate an encrypted edit file with a supported PES edit-file layout."""
    edit_path = Path(args.edit_file)
    _ensure_safe_edit_paths(edit_path)
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
            print("PASS: save structure matches the supported PES edit-file layout")
            return
        print(
            f"FAIL: {len(report['errors'])} supported PES edit-file layout "
            "error(s)"
        )
        raise SystemExit(2)
    finally:
        crypto.cleanup_temp(temp_dir)


def cmd_repair(args):
    """Repair a legacy base using consensus registrations from valid references."""
    edit_path = Path(args.edit_file)
    output_path = Path(args.output) if args.output else config.OUTPUT_FILE_PATH
    reference_paths = [Path(path) for path in args.reference]
    _ensure_safe_edit_paths(edit_path, output_path, *reference_paths)

    base_temp = crypto.decrypt(edit_path)
    reference_temps: list[Path] = []
    try:
        ef = EditFile(base_temp / "data.dat")
        ef.load()
        league_block_start, league_block_end = _competition_section_bounds(ef)
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
        ef._player_cache = players
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
    """CLI adapter for the shared verified-transfer service."""
    dry_run = bool(getattr(args, "dry_run", False))
    try:
        edit_path, output_path = _resolve_run_paths(args)
    except DestinationError as error:
        print(f"Unsafe save path: {error}")
        raise SystemExit(2) from error
    game_root = getattr(args, "game_root", None)
    if game_root is not None:
        config.GAME_ROOT = Path(game_root)

    if not dry_run and not edit_path.exists():
        print(f"Edit file not found: {edit_path}")
        print("Use --edit-file to specify the path, or set EDIT_FILE_PATH in config.py")
        sys.exit(1)

    if dry_run and not edit_path.exists():
        transfers = pipeline._scrape_run_transfers(args)
        if not transfers:
            print("No verified transfers or captain updates found. Nothing to apply.")
            return
        print("\n⚠ Dry-run mode without edit file — showing scraped data only.")
        print(f"\nAll {len(transfers)} transfers:")
        for transfer in transfers:
            print(f"  {transfer}")
        captain_updates = getattr(transfers, "captain_updates", ())
        if captain_updates:
            print(f"\nCurrent captain markers ({len(captain_updates)}):")
            for captain in captain_updates:
                print(f"  {captain.club_name}: {captain.player_name}")
        return

    request = LocalUpdateRequest(
        edit_path=edit_path,
        output_path=output_path,
        deep=bool(getattr(args, "deep", False)),
        window=getattr(args, "window", "auto") or "auto",
        since=getattr(args, "since", None),
        club=getattr(args, "club", None),
        threshold=getattr(args, "threshold", None) or config.MATCH_THRESHOLD_PLAYER,
        popular=bool(getattr(args, "popular", False)),
        fotmob_only=bool(getattr(args, "fotmob_only", False)),
        allow_overflow_release=bool(
            getattr(args, "allow_overflow_release", True)
        ),
        release_policy_file=getattr(args, "release_policy_file", None),
        dry_run=dry_run,
    )

    try:
        result = pipeline.build_local_update_service().execute(request)
    except LocalUpdateError as error:
        print(f"\n❌ {error}")
        sys.exit(1 if error.code in {"missing_input", "decrypt_failed"} else 2)

    if dry_run:
        return
    if result.no_changes:
        if (
            result.transfer_applied == 0
            and result.shirt_numbers_changed == 0
            and result.captains_changed == 0
            and result.unchanged == 0
            and result.safety_skipped == 0
        ):
            print(
                "No verified transfers found. Nothing to apply. "
                "No captain updates were available."
            )
        return

    print(
        f"\n✅ Done! {result.transfer_applied} transfers applied; "
        f"{result.shirt_numbers_changed} shirt numbers changed; "
        f"{result.captains_changed} captains changed."
    )

    if result.diagnostic:
        print(f"   Warning: {result.diagnostic}")
    if result.target_path.resolve() != edit_path.resolve():
        print(f"   Input (base/pristine):   {edit_path}")
        print(f"   Output (updated save):   {result.target_path}")
    else:
        print(f"   Updated file:            {result.target_path}")
    print(f"   Backup at:               {result.backup_path}")
    print(f"   Log at:                  {config.TRANSFER_LOG_FILE}")
    print(f"   Visual Summary Report:   {config.OUTPUT_DIR / 'transfer_summary.md'}")





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
        except SystemExit as e:
            # cmd_run uses non-zero SystemExit for fail-closed operational
            # aborts. A scheduler must record that run and try again later.
            if e.code in (None, 0):
                raise
            logger.error("Scheduler run #%s aborted with exit code %s", iteration, e.code)
            print(f"✗ Run #{iteration} aborted safely (exit code {e.code})")
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
    cron_line = (
        f"{cron_expr} cd {shlex.quote(str(cwd_path))} && "
        f"{shlex.quote(str(py_path))} {shlex.quote(str(script_path))} run >> "
        f"{shlex.quote(str(config.DATA_DIR / 'cron.log'))} 2>&1"
    )

    print("\n📅 Automated Cron Configuration")
    print("================================")
    print(f"Schedule: Every {interval_hours} hours (`{cron_expr}`)")
    print(f"\nCrontab entry:\n\n  {cron_line}\n")
    print("To install automatically, run:")
    print(f'  (crontab -l 2>/dev/null; echo "{cron_line}") | crontab -')


def _add_transfer_feed_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--game-root",
        type=Path,
        help="Game installation root; auto-load Player.bin from download/",
    )
    parser.add_argument(
        "--club",
        type=str,
        help="Comma-separated club names to focus scrape (e.g. 'Chelsea,Arsenal')",
    )
    parser.add_argument(
        "--deep",
        action="store_true",
        help="Deep fetch across all locally indexed FotMob clubs",
    )
    parser.add_argument(
        "--window",
        choices=["auto", "summer", "winter", "all"],
        default="auto",
        help="Transfer window (default: auto)",
    )
    parser.add_argument(
        "--since",
        type=_iso_date_arg,
        help="Scrape transfers since date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--threshold",
        type=_percentage_arg,
        help="Fuzzy match confidence threshold (0-100)",
    )
    parser.add_argument(
        "--popular",
        action="store_true",
        help="Only request FotMob popular transfers",
    )
    parser.add_argument(
        "--fotmob-only",
        action="store_true",
        help=(
            "Disable all supplemental Wikipedia, Sortitoutsi, Transfermarkt, "
            "BeSoccer, Sofascore, and Soccerway sources"
        ),
    )
    parser.add_argument(
        "--allow-overflow-release",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Allow role-based overflow release (default); use "
            "--no-allow-overflow-release to keep full rosters unchanged"
        ),
    )
    parser.add_argument(
        "--release-policy",
        type=Path,
        help="JSON protected-player and offline-usage snapshot (default: data/release_policy.json)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="FL Daily Edit",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")

    sub = parser.add_subparsers(dest="command")

    # run (default)
    p_run = sub.add_parser(
        "run", help="Apply verified transfers and current squad numbers"
    )
    p_run.add_argument("--dry-run", action="store_true", help="Don't modify the edit file")
    run_source = p_run.add_mutually_exclusive_group()
    run_source.add_argument("--edit-file", type=str, help="Path to input EDIT00000000")
    run_source.add_argument(
        "--from-base",
        action="store_true",
        help="Rebuild from base/EDIT00000000 instead of continuing from an existing output",
    )
    run_target = p_run.add_mutually_exclusive_group()
    run_target.add_argument("-o", "--output", type=str, help="Path to output updated edit00000000 (default: output/EDIT00000000)")
    run_target.add_argument("--in-place", action="store_true", help="Overwrite input edit file in-place instead of writing to output/")
    _add_transfer_feed_arguments(p_run)
    p_run.set_defaults(func=cmd_run)


    # schedule
    p_sched = sub.add_parser(
        "schedule",
        help="Run transfers and squad-number sync continuously on a timer",
    )
    p_sched.add_argument("--interval-hours", type=_positive_float_arg, default=6.0, help="Interval between runs in hours (default: 6.0)")
    p_sched.add_argument("--dry-run", action="store_true", help="Don't modify the edit file")
    schedule_source = p_sched.add_mutually_exclusive_group()
    schedule_source.add_argument("--edit-file", type=str, help="Path to input edit00000000")
    schedule_source.add_argument(
        "--from-base",
        action="store_true",
        help="Rebuild from base/EDIT00000000 on every scheduled run",
    )
    schedule_target = p_sched.add_mutually_exclusive_group()
    schedule_target.add_argument("-o", "--output", type=str, help="Path to output updated edit00000000 (default: output/EDIT00000000)")
    schedule_target.add_argument("--in-place", action="store_true", help="Overwrite input edit file in-place")
    _add_transfer_feed_arguments(p_sched)
    p_sched.set_defaults(func=cmd_schedule)

    # cron
    p_cron = sub.add_parser("cron", help="Generate crontab line for automated scheduling")
    p_cron.add_argument("--interval-hours", type=_positive_int_arg, default=6, help="Interval in hours (default: 6)")
    p_cron.set_defaults(func=cmd_cron)

    # inspect
    p_inspect = sub.add_parser("inspect", help="Inspect an edit file structure")
    p_inspect.add_argument("--edit-file", type=str, required=True, help="Path to edit00000000")
    p_inspect.set_defaults(func=cmd_inspect)


    # metadata audit
    p_audit = sub.add_parser(
        "audit",
        help="Audit a save against native Player/Team metadata",
    )
    p_audit.add_argument(
        "--edit-file",
        type=str,
        required=True,
        help="Path to input EDIT00000000",
    )
    p_audit.add_argument(
        "--game-root",
        type=Path,
        help="Game installation root containing download/*.cpk",
    )
    p_audit.add_argument(
        "--player-bin",
        type=Path,
        help="Explicit Player.bin path",
    )
    p_audit.add_argument(
        "--team-bin",
        type=Path,
        help="Explicit Team.bin path",
    )
    p_audit.add_argument(
        "--player-assignment",
        type=Path,
        help="Explicit PlayerAssignment.bin path",
    )
    p_audit.add_argument(
        "--as-of",
        type=_iso_date_arg,
        default=date.today().isoformat(),
        help="Contract report date (YYYY-MM-DD)",
    )
    p_audit.add_argument(
        "--json",
        action="store_true",
        help="Emit only the bounded audit report as JSON",
    )
    p_audit.set_defaults(func=cmd_metadata_audit)


    # offline usage snapshot importer
    p_usage_import = sub.add_parser(
        "usage-import",
        help="Merge CSV usage counters into the release policy",
    )
    p_usage_import.add_argument(
        "--input",
        type=Path,
        required=True,
        help="CSV with player_id, minutes, starts, appearances, news_mentions",
    )
    p_usage_import.add_argument(
        "--output",
        type=Path,
        help="Output policy path (default: data/release_policy.json)",
    )
    p_usage_import.add_argument("--source", default="", help="Usage source label")
    p_usage_import.add_argument("--season", default="", help="Season label")
    p_usage_import.add_argument("--as-of", default="", help="Snapshot date label")
    p_usage_import.set_defaults(func=cmd_usage_import)
    # native metadata variant comparison
    p_compare = sub.add_parser(
        "compare",
        help="Compare native metadata between two CPK variants",
    )
    left_source = p_compare.add_mutually_exclusive_group(required=True)
    left_source.add_argument(
        "--left-cpk",
        type=Path,
        help="Left data_s2526.cpk or data_extra.cpk path",
    )
    left_source.add_argument(
        "--left-game-root",
        type=Path,
        help="Left game installation root containing download/*.cpk",
    )
    right_source = p_compare.add_mutually_exclusive_group(required=True)
    right_source.add_argument(
        "--right-cpk",
        type=Path,
        help="Right data_s2526.cpk or data_extra.cpk path",
    )
    right_source.add_argument(
        "--right-game-root",
        type=Path,
        help="Right game installation root containing download/*.cpk",
    )
    p_compare.add_argument(
        "--json",
        action="store_true",
        help="Emit the bounded comparison as JSON",
    )
    p_compare.set_defaults(func=cmd_compare_metadata)


    # validate
    p_validate = sub.add_parser(
        "validate", help="Validate an encrypted edit file with a supported PES edit-file layout"
    )
    p_validate.add_argument("--edit-file", type=str, required=True, help="Path to edit00000000")
    p_validate.set_defaults(func=cmd_validate)

    # repair
    p_repair = sub.add_parser(
        "repair",
        help="Repair a legacy base without importing reference league memberships",
    )
    p_repair.add_argument(
        "--edit-file",
        type=str,
        required=True,
        help="Legacy base EDIT00000000",
    )
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
    p_log.add_argument(
        "--last", type=int, default=20, help="Number of recent entries (default: 20)"
    )
    p_log.set_defaults(func=cmd_log)
    return parser


def main() -> None:
    parser = build_parser()

    subcommands = {
        "run", "schedule", "cron", "inspect", "audit", "usage-import",
        "compare", "validate", "repair", "log", "-h", "--help",
    }
    if len(sys.argv) > 1 and sys.argv[1] not in subcommands:
        sys.argv.insert(1, "run")
    elif len(sys.argv) == 1:
        sys.argv.append("run")

    args = parser.parse_args()
    setup_logging(args.verbose)

    if hasattr(args, "func"):
        try:
            args.func(args)
        except IncompleteScrapeError as exc:
            print(f"\n❌ Scrape incomplete; no roster changes were written: {exc}")
            raise SystemExit(2) from exc
        except PlayerCatalogError as exc:
            print(f"\n❌ Player catalog invalid; no roster changes were written: {exc}")
            raise SystemExit(2) from exc
        except EditLockError as exc:
            print(f"\n❌ Concurrent run rejected: {exc}")
            raise SystemExit(2) from exc
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
