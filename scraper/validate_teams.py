#!/usr/bin/env python3
"""Diagnostics: print the FotMob ↔ PES club identity index for one save."""

import argparse
import json
import logging
from pathlib import Path

import config
from editor import crypto
from editor.editfile import EditFile
from scraper.club_identity import build_club_identity_index, load_fotmob_teams

logger = logging.getLogger("validate_teams")


def get_pes_clubs(edit_file_path: str | Path) -> dict[int, str]:
    """Decrypt a save, validate it, and return club IDs/names only."""
    path = Path(edit_file_path)
    if not path.exists():
        raise FileNotFoundError(f"Edit file not found: {path}")

    temp_dir = crypto.decrypt(path)
    try:
        data_dat = temp_dir / "data.dat"
        if not data_dat.exists():
            raise RuntimeError(f"Decrypted save has no data.dat: {temp_dir}")

        edit_file = EditFile()
        edit_file.load(data_dat)
        integrity = edit_file.validate_integrity()
        if not integrity["valid"]:
            preview = "; ".join(integrity["errors"][:5])
            raise RuntimeError(f"Input save failed integrity validation: {preview}")

        teams = edit_file.get_all_team_info()
        return {
            team_id: teams[team_id].name
            for team_id in sorted(edit_file.get_club_team_ids())
            if team_id in teams and teams[team_id].name
        }
    finally:
        crypto.cleanup_temp(temp_dir)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--edit-file", type=Path, default=config.EDIT_FILE_PATH)
    parser.add_argument(
        "--save-scope",
        default="",
        help="Learned-binding cache scope (the pipeline uses the resolved output path)",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    pes_clubs = get_pes_clubs(args.edit_file)
    index = build_club_identity_index(
        pes_clubs,
        load_fotmob_teams(),
        cache_path=config.CLUB_IDENTITY_CACHE_FILE,
        save_scope=args.save_scope or str(args.edit_file.resolve()),
    )
    entries = index.entries()
    print(json.dumps(entries, indent=2, ensure_ascii=False))
    logger.info("Bound %s/%s save clubs", len(entries), len(pes_clubs))


if __name__ == "__main__":
    main()
