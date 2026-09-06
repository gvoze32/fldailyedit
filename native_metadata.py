from __future__ import annotations

import logging
from pathlib import Path

import config
from editor.player_assignment import PlayerAssignmentDatabase
from editor.playerbin import PlayerBinDatabase
from editor.teambin import TeamBinDatabase
from installer.paths import discover_game_cpks
from tools.cpk_extract import read_file as read_cpk_file
logger = logging.getLogger(__name__)

def _game_database_archives(
    game_root: Path | str | None = None,
) -> tuple[Path, ...]:
    """Return database archives in the selected game's overlay order."""
    selected_root = (
        game_root if game_root is not None else getattr(config, "GAME_ROOT", None)
    )
    if selected_root is None:
        return ()
    return discover_game_cpks(Path(selected_root))
def _prefer_game_database(
    explicit_path: Path | str | None,
    game_root: Path | str | None,
) -> bool:
    """Prefer selected game archives unless the caller supplied a file."""
    return explicit_path is None and (
        game_root is not None or getattr(config, "GAME_ROOT", None) is not None
    )


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
    prefer_game_database: bool = False,
):
    """Load one binary database from an extracted file or game CPK."""

    def load_configured():
        if configured_path is None:
            return None
        candidate = Path(configured_path)
        if not candidate.is_file():
            return None
        try:
            return database_type.load(candidate), str(candidate)
        except (OSError, ValueError) as exc:
            logger.warning(
                "Ignoring invalid %s metadata %s: %s", label, candidate, exc
            )
            return None

    if not prefer_game_database:
        configured = load_configured()
        if configured is not None:
            return configured

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

    if prefer_game_database:
        configured = load_configured()
        if configured is not None:
            return configured
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
        prefer_game_database=_prefer_game_database(player_bin_path, game_root),
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
        prefer_game_database=_prefer_game_database(team_bin_path, game_root),
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
        prefer_game_database=_prefer_game_database(assignment_path, game_root),
    )
