# FL26 Transfer Automation Tool — Project Memory

> This file is a persistent memory for AI assistants working on this project.
> Read this FIRST before doing anything. It contains the full project context,
> goals, technical specifications, and findings from previous research sessions.

---

## 1. Project Goal

Build an automated system (daily cron) that:
1. Scrapes latest player transfer data from FotMob (direct async HTTP)
2. Matches scraped player names to Player IDs in the Football Life 26 (FL26) database
3. Decrypts the FL26 save file (`edit00000000`) using pesXdecrypter
4. Modifies team-player associations in the binary data (move player IDs between teams)
5. Re-encrypts into a new `edit00000000`
6. Auto-backs up the old file before overwriting
7. Logs all changes for auditing/rollback

**Scope:** Player transfers ONLY (club A → club B). NO player stats/ability editing.

**Tech stack:** Python, subprocess for pesXdecrypter binary, modular structure (scraper/ and editor/ independent).

---

## 2. File Context — CRITICAL

The edit file (`edit00000000`) comes from **SP Football Life 26 (FL26)**, NOT standard PES 2021.
FL26 is built on the PES 2021 engine but the SmokePatch team has modified:
- Player IDs (different/additional players)
- Team IDs (different/additional teams)
- Possibly different table sizes (more entries in each table)

The validated FL26 files use the same fixed table capacities, entry sizes, field
offsets, and table starts as vanilla PES 2021. Header counts describe populated
entries and must only bound iteration; they do not determine table starts.

**Strategy:** Use the fixed capacity layout for offsets, read header counts to
bound populated-entry iteration, and validate both against the actual file size.

---

## 3. Edit File Binary Structure (from implyingrigged.info wiki)

Source: `implyingrigged-info-wiki-Pro-Evolution-Soccer-2021-Edit-file.txt` in project root.
Note: "beginning of the file" = what pesXdecrypter exports as `data.dat`.
Same structure applies to PES 2020.

### 3.1 Table Layout (sequential in data.dat)

| # | Table | Vanilla PES21 Start | Vanilla PES21 End | Entry Size | Default Count |
|---|-------|--------------------|--------------------|------------|---------------|
| 0 | Header | 0x000000 | 0x00007B | 124 (0x7C) bytes | 1 |
| 1 | Player Entries | 0x00007C | 0x8ED2FB | 240 (0xF0) bytes | 4830 (0x12DE) |
| 2 | Player Appearance | (interleaved with player entries) | | 72 (0x48) bytes | 4830 |
| 3 | Team Entries | 0x8ED2FC | 0x958DA3 | 588 (0x24C) bytes | 210 (0xD2) |
| 4 | Manager Entries | 0x958DA4 | 0x974C83 | 88 (0x58) bytes | 231 (0xE7) |
| 5 | Competitions | 0x974C84 | 0x980D7B | 760 (0x2F8) bytes | 46 (0x2E) |
| 6 | Stadium Entries | 0x980D7C | 0x983D37 | 188 (0xBC) bytes | 55 (0x37) |
| 7 | Unknown Section | 0x983D38 | 0x9D4647 | 132 (0x84) bytes | 79 (0x4F) |
| 8 | **Team-Player Table** | **0x9D4648** | **0xA0864F** | **284 (0x11C) bytes** | **210 (0xD2)** |
| 9 | Competition Entry | 0xA08650 | 0xA0987F | 4656 bytes total | 1 (flat section) |
| 10 | Team Game Plan | 0xA09880 | 0xA7C857 | 628 (0x274) bytes | 210 (0xD2) |

### 3.2 How Table Offsets Work (CRITICAL DISCOVERY)

**The header counts are NOT used for block sizing.** Each table block is allocated at a
FIXED maximum capacity, regardless of how many entries are "used". The header counts
at 0x60-0x7B just say how many entries are **populated**, not the block size.

Header fields (all little-endian uint16):

| Header Offset | Field | Vanilla Default |
|---------------|-------|-----------------|
| 0x60 | player_count (used) | 4830 |
| 0x64 | team_count (used) | 210 |
| 0x66 | manager_count (used) | 231 |
| 0x68 | stadium_count (used) | 55 |
| 0x6A | competition_count (used) | 46 |
| 0x6C | unknown_section_count (used) | 79 |
| 0x70 | team_player_count (used) | 210 |
| 0x74 | game_plan_count (used) | 210 |

**MAX slot allocations** (derived from wiki documented start/end offsets):

| Table | MAX Slots | Entry Size | Block Size (bytes) |
|-------|-----------|------------|-------------------|
| Players | 30,000 | 312 | 9,360,000 |
| Teams | 750 | 588 | 441,000 |
| Managers | 1,300 | 88 | 114,400 |
| Competitions | 65 | 760 | 49,400 |
| Stadiums | 65 | 188 | 12,220 |
| Unknown | 2,500 | 132 | 330,000 |
| Team-Player | 750 | 284 | 213,000 |

**Offset calculation (use MAX, not header count):**
```python
player_start     = 0x7C  # fixed, right after header
team_start       = player_start + 30000 * 312      # = 0x8ED2FC
manager_start    = team_start + 750 * 588           # = 0x958DA4
competition_start= manager_start + 1300 * 88        # = 0x974C84
stadium_start    = competition_start + 65 * 760     # = 0x980D7C
unknown_start    = stadium_start + 65 * 188         # = 0x983D38
team_player_start= unknown_start + 2500 * 132       # = 0x9D4648
game_plan_start  = team_player_start + 750 * 284    # = 0xA08650
# Note: competition entry section (0x1230 bytes) sits between team_player and game_plan
```

**FL26 note:** If SmokePatch expanded the database (more player/team slots), these MAX
values will be different. Validate by checking `ef.validate_offsets()` against an actual file.

### 3.3 Team-Player Table Entry Structure (0x11C = 284 bytes per entry)

THIS IS THE CRITICAL TABLE FOR TRANSFERS:

| Offset | Size | Description |
|--------|------|-------------|
| 0x00 | 4 bytes | Team ID (uint32 LE) |
| 0x04 | 160 bytes | 40 × Player ID slots (each 4 bytes uint32 LE). 0x00000000 = empty slot. Index position (0-39) is the "Index ID" used by game plans. |
| 0xA4 | 80 bytes | 40 × Shirt Number slots (each 2 bytes uint16 LE). Range [0, 999]. |
| 0xF4 | 40 bytes | Unknown A (possibly reserved/padding) |

**Transfer operation = move a Player ID from one team's slot array to another's:**
1. Find player_id in source team's 40 slots
2. Zero out that slot (and its shirt number)
3. Compact: shift last non-zero entry into the gap
4. Find first empty (0x00000000) slot in destination team
5. Write player_id there, assign an unused shirt number
6. Update game plan if needed (Index IDs may shift due to compaction)

### 3.4 Team Entry Structure (selected fields relevant to transfers)

Entry size: 588 (0x24C) bytes

| Offset | Size | Description |
|--------|------|-------------|
| 0x000 | 4 bytes | Team ID (uint32 LE) |
| 0x068 | 70 bytes | Team Name (null-terminated UTF-8 string) |
| 0x0AE | 4 bytes | Scoreboard name (null-terminated, max 3 chars) |

### 3.5 Player Entry Structure (selected fields relevant to identification)

Entry size: 240 (0xF0) bytes. Appearance entry (72 bytes, 0x48) follows immediately after.

| Offset | Size | Description |
|--------|------|-------------|
| 0x00 | 4 bytes | Player ID (uint32 LE) |
| 0x36 | 61 bytes | Player Name (null-terminated string) |
| 0x73 | 61 bytes | Print Name - Club (null-terminated, uppercase) |
| 0xB0 | 64 bytes | Print Name - National Team (null-terminated, uppercase) |

### 3.6 Team Game Plan Structure (relevant to transfers)

Entry size: 628 (0x274) bytes.

| Offset | Size | Description |
|--------|------|-------------|
| 0x000 | 4 bytes | Team ID |
| 0x1E4 | 40 bytes | Player Lineup - 40 Index IDs (1 byte each). Determines GK, outfield, bench order. |
| 0x212 | 1 byte | Captain Index ID |

**When a player is removed and slots are compacted, the Index IDs in the game plan
become stale.** The game plan should be updated to reflect new index positions.
The kickoffsage reference code handles this via `update_tactics_for_team()`.

---

## 4. pesXdecrypter — Decrypt/Encrypt Tool

- Repo: https://github.com/the4chancup/pesXdecrypter
- Written in C, public domain license
- Title says "PES 2016-2020" but PES 2021 binaries exist (kickoffsage bundles `decrypter21.exe` / `encrypter21.exe`)
- Splits encrypted edit file into blocks: encryption header, file header, thumbnail, description, **data** (= data.dat), serial
- We edit `data.dat`, then re-encrypt all blocks back

**Usage:**
```
decrypter21 <input_file> <output_directory>    # decrypt
encrypter21 <input_directory> <output_file>    # encrypt
```

**macOS note:** Bundled binaries are `.exe` (Windows). Options:
1. Compile from C source with CMake + clang (recommended, no Windows deps in core lib)
2. Use Wine

**FL26 compatibility:** Needs verification — FL26 may use the same encryption as PES21 (likely),
but should be tested with an actual FL26 `edit00000000` file before relying on it.

---

## 5. Reference Code Analysis — kickoffsage/pes2021-transfer-tool

Repo: https://github.com/kickoffsage/pes2021-transfer-tool

### What exists (partially working):
- `fetch_latest_transfers.py` — scrapes web transfer lists, uses rapidfuzz for fuzzy matching
- `fetch_team_transfers.py` — scrapes specific team's transfer page
- `src/crypt_utils.py` — subprocess wrapper around pesXdecrypter
- `src/team_utils.py` — reads team IDs + names from binary (hardcoded offset jumps)
- `src/transfer_utils.py` — add/remove player from team (replace-with-last-non-zero approach)
- `src/csv_utils.py` — CSV I/O for player/team data

### What's broken/missing:
- **All files truncated on GitHub** — code cuts off mid-function (corrupted commits)
- No backup system
- No logging/audit
- Windows-only (hardcoded `.exe` paths)
- Hardcoded offsets (won't work with FL26 if table sizes differ)
- No pagination in scraper
- No rate limiting or retry handling
- No error handling for edge cases (player not found, team full, etc.)
- `team_utils.py` uses magic numbers: `f.seek(100, 1)` to skip to team name, `f.read(70)` for name — these correspond to: Team entry offset 0x068 for name (0x068 - 0x004 = 0x64 = 100 bytes after team ID), 70 bytes for name field. Correct for vanilla PES21 but not dynamically calculated.

### Dependencies (from pyproject.toml):
- Python ^3.13
- beautifulsoup4 ^4.12.3
- requests ^2.32.3
- rapidfuzz ^3.10.1
- pytest (dev)

---

## 6. 4ccEditor Analysis (C++ reference for file structure)

Repo: https://github.com/the4chancup/4ccEditor

- `pes20.cpp` — handles both PES 20 and 21 (identical format)
- `editor.h` — defines `player_entry` struct with all fields
- `data_util.cpp` — bit-level read_data function (reads individual bits, not byte-aligned)
- Player stats are stored at BIT granularity (not byte-aligned) — e.g., "7 bits for Offensive Awareness starting at byte 0x0E bit 0"
- Team roster stored as array of player IDs + shirt numbers (confirmed by wiki)
- Uses `libpesXcrypter.dll` for decrypt/encrypt

---

## 7. Fuzzy Matching Strategy

FotMob names vs FL26 database names will differ:
- Format: "K. Mbappé" vs "Kylian Mbappé"
- Diacritics: "Müller" vs "Muller"
- Order: "Cristiano Ronaldo" vs "Ronaldo, Cristiano"
- Abbreviations: "Man Utd" vs "Manchester United"

**Multi-strategy approach using rapidfuzz:**
1. Normalize both strings (lowercase, strip diacritics via `unicodedata`)
2. Resolve exact duplicate names against the source roster first; use the
   destination roster only as an already-applied/idempotent fallback
3. Apply position compatibility, nationality, and age evidence
4. Combine `token_set_ratio`, `token_sort_ratio`, `WRatio`, and phonetic evidence
5. Require the configurable threshold (default 80%) and a 3-point runner-up margin
6. Treat `Free Agent`, `Without Club`, `Retired`, and equivalent sentinels as
   deliberately unmatched clubs, never fuzzy-match them
7. Manual override files for known mismatches (override keys are normalized):
   - `data/team_aliases.json` — team name aliases
   - `data/name_overrides.json` — player name overrides

---

## 8. Project Structure (Planned)

```
fleditscrape/
├── MEMORY.md                # THIS FILE — project context for AI continuity
├── config.py                # Central config (paths, thresholds, URLs)
├── run.py                   # Main CLI entry point (full pipeline)
├── pyproject.toml           # Python project + dependencies
├── scraper/
│   ├── __init__.py
│   ├── fotmob.py            # Direct async FotMob transfer scraper
│   ├── matcher.py           # Fuzzy name matching engine (position-aware, bidirectional context)
│   └── models.py            # Transfer / MatchedTransfer dataclasses
├── editor/
│   ├── __init__.py
│   ├── crypto.py            # pesXdecrypter subprocess wrapper
│   ├── editfile.py          # Binary edit file reader/writer (dynamic offsets)
│   ├── backup.py            # Timestamped backup management
│   ├── logger.py            # Structured transfer logging (JSONL)
│   └── models.py            # TeamData / PlayerInfo dataclasses
├── data/
│   ├── team_aliases.json    # FotMob → FL26 team name mapping
│   ├── name_overrides.json  # Player name manual overrides
│   └── leagues.json         # League URLs to scrape
├── tests/
│   ├── test_scraper.py
│   ├── test_matcher.py
│   ├── test_editor.py
│   ├── test_run_logic.py
│   └── fixtures/            # Saved HTML + mock binaries for tests
├── vendor/
│   └── pesXdecrypter/       # Compiled decrypter/encrypter binary
└── implyingrigged-info-wiki-Pro-Evolution-Soccer-2021-Edit-file.txt  # Wiki reference
```

---

## 9. Build Order

1. **Phase 1 — Scraper** (fully independent, no edit file needed)
   - `scraper/models.py`, `scraper/fotmob.py`, `scraper/matcher.py`
   - `data/team_aliases.json`, `data/leagues.json`
   - Tests with saved HTML fixtures

2. **Phase 2 — Editor** (needs a base FL26 edit file + pesXdecrypter)
   - `editor/models.py`, `editor/crypto.py`, `editor/editfile.py`, `editor/backup.py`
   - Dynamic offset calculation from header
   - Tests with mock binary data

3. **Phase 3 — Integration**
   - `run.py` full pipeline
   - `editor/logger.py`
   - `--dry-run` mode

4. **Phase 4 — Automation**
   - Cron/scheduler setup
   - Error notification

---

## 10. Open Questions (For User)

1. Where is the FL26 `edit00000000` file located on disk?
2. Which leagues to track? (Top 5 EU? All FL26 leagues? Configurable list?)
3. Is pesXdecrypter already compiled, or need setup?
4. Include loans or only permanent transfers?
5. Transfer window scope: current only, or catch-up on past?

---

## 11. Key Technical Constants

```python
# Entry sizes (bytes)
HEADER_SIZE = 0x7C           # 124
PLAYER_ENTRY_SIZE = 0xF0     # 240
PLAYER_APPEARANCE_SIZE = 0x48 # 72
PLAYER_TOTAL_SIZE = 0x138    # 312 (entry + appearance, interleaved)
TEAM_ENTRY_SIZE = 0x24C      # 588
MANAGER_ENTRY_SIZE = 0x58    # 88
COMPETITION_ENTRY_SIZE = 0x2F8  # 760
STADIUM_ENTRY_SIZE = 0xBC    # 188 (wiki says 0xBB=187, but likely padded to 188)
UNKNOWN_ENTRY_SIZE = 0x84    # 132
TEAM_PLAYER_ENTRY_SIZE = 0x11C  # 284
COMPETITION_SECTION_SIZE = 0x1230  # 4656 (flat)
GAME_PLAN_ENTRY_SIZE = 0x274  # 628

# Header field offsets (within data.dat)
HEADER_PLAYER_COUNT = 0x60
HEADER_TEAM_COUNT = 0x64
HEADER_MANAGER_COUNT = 0x66
HEADER_STADIUM_COUNT = 0x68
HEADER_COMPETITION_COUNT = 0x6A
HEADER_UNKNOWN_COUNT = 0x6C
HEADER_TEAM_PLAYER_COUNT = 0x70
HEADER_GAME_PLAN_COUNT = 0x74

# Team-Player table entry internal offsets
TP_TEAM_ID = 0x00           # 4 bytes
TP_PLAYER_IDS_START = 0x04  # 40 × 4 bytes = 160 bytes
TP_SHIRT_NUMBERS_START = 0xA4  # 40 × 2 bytes = 80 bytes
TP_MAX_PLAYERS = 40

# Team entry internal offsets (for reading team names)
TEAM_ID_OFFSET = 0x000       # 4 bytes
TEAM_NAME_OFFSET = 0x068     # 70 bytes null-terminated
TEAM_ABBREV_OFFSET = 0x0AE   # 4 bytes null-terminated

# Player entry internal offsets (for reading player names)
PLAYER_ID_OFFSET = 0x00      # 4 bytes
PLAYER_NAME_OFFSET = 0x36    # 61 bytes null-terminated
PLAYER_PRINT_NAME_OFFSET = 0x73  # 61 bytes null-terminated
```

---

## 12. Vanilla PES21 Verification Values

Use these to verify offset calculations against a known-good vanilla file:

| Check | Expected |
|-------|----------|
| Header byte 0x60-0x61 (player count) | 0xDE12 = 4830 |
| Header byte 0x64-0x65 (team count) | 0xD200 = 210 |
| Team-Player table start | 0x9D4648 |
| Team-Player table end | 0xA0864F |
| First team entry start | 0x8ED2FC |

Different populated counts do not shift table starts. A genuinely expanded future
FL database would require a separately validated layout version.

---

## 13. FL26 Known-Good Validation (2026-08-03)

Three independently sourced Football Life 2026 `EDIT00000000` files in
`reference/` were treated as known-good and compared after decryption.

Confirmed invariants shared by all three:

- `data.dat` is exactly 10,995,800 bytes.
- Fixed PES21 table starts/capacities are correct for these FL26 files.
- Header counts are populated counts; all three use 749 teams, 749 team-player
  entries, and 749 game-plan entries.
- Club rosters contain no duplicate player registration across two clubs.
- Rosters are compact (no zero slot before a later non-zero player), shirt
  numbers are unique per active roster, and empty slots have shirt number zero.
- The active game-plan prefix maps the compact active roster one-to-one.
  The full 40-byte lineup is **not always** a strict `0..39` permutation, so a
  validator that enforces that globally produces false corruption reports.
- Decrypting, re-encrypting, and decrypting again preserves all six logical
  blocks byte-for-byte. pesXdecrypter itself is therefore not the primary
  source of the observed corrupt output.

Repository findings:

- The original legacy base failed the new known-good-derived
  validator (duplicate club registrations and invalid active game-plan prefixes).
- The old generated `output/EDIT00000000` amplified these problems by adding
  over one hundred new cross-club duplicate registrations and modifying
  hundreds of game plans.
- The pipeline must validate input before edits, validate output after edits,
  reject ambiguous duplicate player names, resolve a player's actual current
  club before moving/signing, and verify encryption by round-trip decryption.

Reference season membership finding:

- All three references contain the same promotion/relegation membership changes
  relative to the legacy base for the English, French, Italian, and Spanish
  first/second divisions.
- The underlying team IDs and the union of playable club IDs are unchanged; the
  teams are only reassigned between division lists.
- Repairing the legacy base must preserve its 0x1230-byte league-membership
  block and use references only to resolve corrupt roster registrations.
- Before Gondowan was selected as the canonical base, the tracked legacy file
  was repaired using three-reference
  consensus: 21 duplicate registrations, 137 remaining active lineups, and 6
  inactive role pointers were corrected. The league-membership block remained
  byte-identical (`SHA-256 6f677bd02e34d0e5aa66e9d2247e09651e3a44a8f78bc0fb922e114ced5c2cb7`).

Canonical base decision:

- `base/EDIT00000000` is Gondowan's Mid-Summer FL26 2.2 EDIT dated 27/07/2026.
- Source: https://www.reddit.com/r/SPFootballLife/comments/1v7z782/release_gondowans_midsummer_edit_file_more_than/
- It includes 500+ transfers, rating/position changes, auto lineups, squad
  numbers, manager updates, loan returns, and promotions/relegations for the
  four first/second-division pairs listed above.
- Runtime defaults, workflows, and validation utilities must use `base/` as the
  single canonical input directory.

Final ingestion/mutation safety rules:

- Automatic windows are bounded to Jan 1–Feb 28/29 or Jun 1–Sep 30. Between
  windows, `auto` selects the most recently completed window; malformed
  `since_date` values are rejected instead of silently disabling filtering.
- `auto` depends only on today's date and window open/end boundaries, never on
  base provenance or sidecar metadata. Daily workflows need no season-specific
  edits. Global FotMob pages must all be scanned up to the configured limit
  because the endpoint is sorted by `lastModified`, not by `transferDate`; an
  old corrected record must not terminate pagination early.
- Undated transactional events are excluded whenever a bounded date filter is
  active. Squad-number observations remain separate `squad_update` records.
- Every resolved fetch range is capped at the current UTC date. Pre-agreements
  may be visible in a source before registration opens, but must not mutate the
  game roster until FotMob's effective `transferDate` is reached.
- Manual club filters prefer normalized exact names and accept a substring only
  when it resolves uniquely; ambiguous club filters are skipped.
- A transfer mutates the roster only if the player's actual current club equals
  the matched source. Signings require the player to be unregistered, releases
  require the expected source, already-applied events are no-ops, and any other
  state is a safety skip.
- The dry run uses the same decision gate as the real mutation path.
- Club/national-team classification must come from the league-membership block,
  not a numeric ID threshold. FL26 has playable clubs at IDs `<=100` (Manchester
  United is ID 100 in the supplied team database).
- Current regression baseline: 114 tests passing; canonical base and output
  both validate at 10,995,800 bytes, 749 rosters, 583 clubs, zero duplicate club
  registrations, and 747 checked game plans.

Persistent agent preferences:

- Use RTK (Rust Token Killer) wrappers for shell work whenever an equivalent
  RTK command exists; use `rtk run` or `rtk proxy` when no filtered wrapper fits.
- Keep caveman full mode active for every response and task: terse Indonesian,
  no filler, full technical accuracy. Disable only when user explicitly asks.
