"""Enforce the changed-file boundary for player specification pull requests."""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence
from pathlib import Path, PurePosixPath


_PLAYER_PATH = re.compile(r"players/[a-z0-9]+(?:-[a-z0-9]+)*\.json")
_SCORED_STATUS = re.compile(r"[CR](?:100|0[0-9]{2})")
_SINGLE_PATH_STATUSES = frozenset("ADMTUX")


class PlayerContributionError(ValueError):
    """A name-status change set violates the player contribution boundary."""


def _validated_path(raw_path: str) -> PurePosixPath:
    if not raw_path:
        raise PlayerContributionError("changed path must not be empty")
    if any(ord(character) < 32 or ord(character) == 127 for character in raw_path):
        raise PlayerContributionError("changed path contains a control character")
    if raw_path.startswith('"') or raw_path.endswith('"'):
        raise PlayerContributionError("Git-quoted changed paths are not accepted")

    path = PurePosixPath(raw_path)
    if path.is_absolute():
        raise PlayerContributionError("changed path must be relative")
    if ".." in path.parts:
        raise PlayerContributionError("changed path must not contain '..'")
    if path.as_posix() != raw_path:
        raise PlayerContributionError("changed path must use canonical POSIX spelling")
    return path


def _parse_record(record: str) -> tuple[str, tuple[PurePosixPath, ...]]:
    fields = record.split("\t")
    status = fields[0] if fields else ""
    if status in _SINGLE_PATH_STATUSES:
        expected_fields = 2
    elif _SCORED_STATUS.fullmatch(status):
        expected_fields = 3
    else:
        raise PlayerContributionError(f"malformed name-status record: {record!r}")
    if len(fields) != expected_fields:
        raise PlayerContributionError(f"malformed name-status record: {record!r}")
    return status, tuple(_validated_path(field) for field in fields[1:])


def validate_player_pr_changes(changes: Sequence[str]) -> Path | None:
    """Return the sole canonical player path, or ``None`` for non-player changes.

    Records use Git's tab-delimited ``--name-status`` representation. All parsing
    is local and side-effect free; the trusted caller is responsible for writing
    the change set.
    """

    parsed = tuple(_parse_record(record) for record in changes)
    player_records = tuple(
        (status, paths)
        for status, paths in parsed
        if any(path.parts and path.parts[0] == "players" for path in paths)
    )
    if not player_records:
        return None
    if len(parsed) != 1:
        raise PlayerContributionError(
            "a player specification pull request must change exactly one file"
        )

    status, paths = player_records[0]
    if status not in {"A", "M"} or len(paths) != 1:
        raise PlayerContributionError(
            "a player specification must be added or modified, not deleted, copied, or renamed"
        )

    path_text = paths[0].as_posix()
    if _PLAYER_PATH.fullmatch(path_text) is None:
        raise PlayerContributionError(
            "player specification path must match players/<canonical-slug>.json"
        )
    return Path(path_text)


def _read_changes(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8", newline="") as changes_file:
        text = changes_file.read()
    if not text:
        return []
    if text.endswith("\n"):
        text = text[:-1]
    return text.split("\n")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate the changed-file boundary for a player spec PR."
    )
    parser.add_argument(
        "--changes-file",
        required=True,
        type=Path,
        help="Trusted caller-written tab-delimited git name-status file",
    )
    args = parser.parse_args(argv)

    try:
        player_path = validate_player_pr_changes(_read_changes(args.changes_file))
    except (OSError, UnicodeError, PlayerContributionError) as error:
        print(f"player contribution guard: {error}", file=sys.stderr)
        return 1

    if player_path is not None:
        print(player_path.as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
