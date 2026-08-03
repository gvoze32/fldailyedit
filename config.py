"""
Central configuration for FL Daily Edit.
All paths, thresholds, and settings in one place.
"""
from pathlib import Path

# --- Project paths ---
PROJECT_ROOT = Path(__file__).parent
DATA_DIR = PROJECT_ROOT / "data"
VENDOR_DIR = PROJECT_ROOT / "vendor"
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
BACKUP_DIR = PROJECT_ROOT / "backups"
MAX_BACKUPS = 10  # auto-delete oldest beyond this

# --- Fuzzy matching ---
MATCH_THRESHOLD_PLAYER = 80  # minimum confidence (0-100) for player name match
MATCH_THRESHOLD_TEAM = 75    # minimum confidence for team name match

# --- Data files ---
TEAM_ALIASES_FILE = DATA_DIR / "team_aliases.json"
NAME_OVERRIDES_FILE = DATA_DIR / "name_overrides.json"
PLAYERS_CSV_FILE = DATA_DIR / "players.csv"
CURRENT_PLAYERS_FILE = DATA_DIR / "FL2622wc_players.txt"
CURRENT_TEAMS_FILE = DATA_DIR / "FL262_teams.txt"

# --- Logging ---
TRANSFER_LOG_FILE = DATA_DIR / "transfer_log.jsonl"
