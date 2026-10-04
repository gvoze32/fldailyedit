"""
Central configuration for FL Daily Edit.
All paths, thresholds, and settings in one place.
"""
import os
import sys
from pathlib import Path


def _user_state_dir(platform: str, environ, home: Path) -> Path:
    """Persistent per-user directory for state that must outlive the process."""
    if platform == "win32":
        local_app_data = environ.get("LOCALAPPDATA")
        root = Path(local_app_data) if local_app_data else home / "AppData" / "Local"
    elif platform == "darwin":
        root = home / "Library" / "Application Support"
    else:
        xdg_data_home = environ.get("XDG_DATA_HOME")
        root = Path(xdg_data_home) if xdg_data_home else home / ".local" / "share"
    return root / "FLDailyEdit"


# --- Project paths ---
PROJECT_ROOT = Path(__file__).parent
# One-file PyInstaller builds run from a temp dir deleted on exit (sys._MEIPASS),
# so anything written there must live in a persistent per-user directory instead.
USER_STATE_DIR = (
    _user_state_dir(sys.platform, os.environ, Path.home())
    if getattr(sys, "frozen", False)
    else None
)
DATA_DIR = PROJECT_ROOT / "data"
VENDOR_DIR = PROJECT_ROOT / "vendor"
STORAGE_DIR = PROJECT_ROOT / "storage"
FOTMOB_TEAM_CACHE_DIR = STORAGE_DIR / "fotmob_team_cache"
FOTMOB_TACTICS_CACHE_FILE = STORAGE_DIR / "fotmob_tactics_cache.json"
# Learned FotMob ↔ save club bindings, keyed by save scope.
CLUB_IDENTITY_CACHE_FILE = (
    USER_STATE_DIR / "club_identity_cache.json"
    if USER_STATE_DIR
    else STORAGE_DIR / "club_identity_cache.json"
)
OUTPUT_DIR = PROJECT_ROOT / "output"
BASE_DIR = PROJECT_ROOT / "base"

# --- Edit file ---
# Canonical validated FL26 base. Override with --edit-file for another save.
EDIT_FILE_PATH = BASE_DIR / "EDIT00000000"
OUTPUT_FILE_PATH = OUTPUT_DIR / "EDIT00000000"

# --- pesXdecrypter ---
DECRYPTER_BIN = VENDOR_DIR / "pesXdecrypter" / "decrypter21"
ENCRYPTER_BIN = VENDOR_DIR / "pesXdecrypter" / "encrypter21"

# --- Backup ---
BACKUP_DIR = USER_STATE_DIR / "backups" if USER_STATE_DIR else PROJECT_ROOT / "backups"
MAX_BACKUPS = 10  # auto-delete oldest beyond this

# --- Fuzzy matching ---
MATCH_THRESHOLD_PLAYER = 80  # minimum confidence (0-100) for player name match
MATCH_THRESHOLD_TEAM = 75    # minimum confidence for team name match

# --- Data files ---
PLAYERS_CSV_FILE = DATA_DIR / "players.csv"
CURRENT_PLAYERS_FILE = DATA_DIR / "FL2622wc_players.txt"
CURRENT_TEAMS_FILE = DATA_DIR / "FL262_teams.txt"
PLAYER_BIN_FILE = DATA_DIR / "Player.bin"
TEAM_BIN_FILE = DATA_DIR / "Team.bin"
PLAYER_ASSIGNMENT_FILE = DATA_DIR / "PlayerAssignment.bin"
RELEASE_POLICY_FILE = DATA_DIR / "release_policy.json"
GAME_ROOT: Path | None = None


# --- Logging ---
TRANSFER_LOG_FILE = (
    USER_STATE_DIR / "transfer_log.jsonl"
    if USER_STATE_DIR
    else DATA_DIR / "transfer_log.jsonl"
)
