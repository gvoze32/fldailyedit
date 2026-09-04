from __future__ import annotations

import logging
from pathlib import Path

import config
from editor.player_assignment import PlayerAssignmentDatabase
from editor.playerbin import PlayerBinDatabase
from editor.teambin import TeamBinDatabase
from installer.paths import discover_game_cpk
from tools.cpk_extract import read_file as read_cpk_file

logger = logging.getLogger(__name__)

def _game_database_archives(
    game_root: Path | str | None = None,
) -> tuple[Path, ...]:
    """Return the one selected game database archive."""
    selected_root = (
        game_root if game_root is not None else getattr(config, "GAME_ROOT", None)
    )
    primary = discover_game_cpk(selected_root)
    return (primary,) if primary is not None else ()


_PLAYER_BIN_CPK_MEMBER = "common/etc/pesdb/Player.bin"
_TEAM_BIN_CPK_MEMBER = "common/etc/pesdb/Team.bin"
_PLAYER_ASSIGNMENT_CPK_MEMBER = "common/etc/pesdb/PlayerAssignment.bin"


def _load_binary_database(
    configured_path: Path | str | None,
    database_type,
    cpk_member: str,
    label: str,
    *,
    game_root: Path | str | None = None,
):
    """Load one binary database from an extracted file or game CPK."""
    if configured_path is not None:
        candidate = Path(configured_path)
        if candidate.is_file():
            try:
                return database_type.load(candidate), str(candidate)
            except (OSError, ValueError) as exc:
                logger.warning(
                    "Ignoring invalid %s metadata %s: %s", label, candidate, exc
                )

    for cpk_path in _game_database_archives(game_root):
        try:
            payload = read_cpk_file(cpk_path, cpk_member)
        except FileNotFoundError:
            continue
        except (OSError, ValueError) as exc:
            logger.warning(
                "Ignoring unreadable %s metadata in %s: %s", label, cpk_path, exc
            )
            continue
        try:
            return database_type.from_bytes(payload), f"{cpk_path}::{label}"
        except (OSError, ValueError) as exc:
            logger.warning(
                "Ignoring invalid %s metadata in %s: %s", label, cpk_path, exc
            )
    return None, None


def _load_playerbin_database(
    player_bin_path: Path | str | None = None,
    *,
    game_root: Path | str | None = None,
) -> tuple[PlayerBinDatabase | None, str | None]:
    """Load Player.bin from configured, game, or local reference metadata."""
    configured_path = (
        getattr(config, "PLAYER_BIN_FILE", None)
        if player_bin_path is None
        else player_bin_path
    )
    database, source = _load_binary_database(
        configured_path,
        PlayerBinDatabase,
        _PLAYER_BIN_CPK_MEMBER,
        "Player.bin",
        game_root=game_root,
    )
    if database is not None or player_bin_path is not None:
        return database, source

    reference_path = Path(config.PROJECT_ROOT) / "reference" / "Player.bin"
    return _load_binary_database(
        reference_path,
        PlayerBinDatabase,
        _PLAYER_BIN_CPK_MEMBER,
        "Player.bin",
        game_root=game_root,
    )


def _load_teambin_database(
    team_bin_path: Path | str | None = None,
    *,
    game_root: Path | str | None = None,
) -> tuple[TeamBinDatabase | None, str | None]:
    """Load Team.bin from an explicit path or selected game database CPK."""
    configured_path = (
        getattr(config, "TEAM_BIN_FILE", None)
        if team_bin_path is None
        else team_bin_path
    )
    return _load_binary_database(
        configured_path,
        TeamBinDatabase,
        _TEAM_BIN_CPK_MEMBER,
        "Team.bin",
        game_root=game_root,
    )


def _load_player_assignment_database(
    assignment_path: Path | str | None = None,
    *,
    game_root: Path | str | None = None,
) -> tuple[PlayerAssignmentDatabase | None, str | None]:
    """Load PlayerAssignment.bin from an explicit path or selected CPK."""
    configured_path = (
        getattr(config, "PLAYER_ASSIGNMENT_FILE", None)
        if assignment_path is None
        else assignment_path
    )
    return _load_binary_database(
        configured_path,
        PlayerAssignmentDatabase,
        _PLAYER_ASSIGNMENT_CPK_MEMBER,
        "PlayerAssignment.bin",
        game_root=game_root,
    )
