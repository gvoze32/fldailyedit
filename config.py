"""
Central configuration for the FL26 Transfer Automation Tool.
All paths, thresholds, and settings in one place.
"""
from pathlib import Path

# --- Project paths ---
PROJECT_ROOT = Path(__file__).parent
DATA_DIR = PROJECT_ROOT / "data"
VENDOR_DIR = PROJECT_ROOT / "vendor"

# --- Edit file ---
# Set this to your actual FL26 edit file path
EDIT_FILE_PATH = Path.home() / "Documents" / "KONAMI" / "eFootball PES 2021 SEASON UPDATE" / "save" / "edit00000000"

# --- pesXdecrypter ---
DECRYPTER_BIN = VENDOR_DIR / "pesXdecrypter" / "decrypter21"
ENCRYPTER_BIN = VENDOR_DIR / "pesXdecrypter" / "encrypter21"

# --- Backup ---
BACKUP_DIR = PROJECT_ROOT / "backups"
MAX_BACKUPS = 10  # auto-delete oldest beyond this

# --- Scraper ---
TRANSFERMARKT_BASE = "https://www.transfermarkt.co.uk"
REQUEST_DELAY = (1.5, 3.0)  # random delay range in seconds between requests
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}
MAX_RETRIES = 3

# --- Fuzzy matching ---
MATCH_THRESHOLD_PLAYER = 80  # minimum confidence (0-100) for player name match
MATCH_THRESHOLD_TEAM = 75    # minimum confidence for team name match

# --- Data files ---
TEAM_ALIASES_FILE = DATA_DIR / "team_aliases.json"
NAME_OVERRIDES_FILE = DATA_DIR / "name_overrides.json"
LEAGUES_FILE = DATA_DIR / "leagues.json"

# --- Logging ---
TRANSFER_LOG_FILE = DATA_DIR / "transfer_log.jsonl"
