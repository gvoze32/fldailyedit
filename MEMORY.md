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

Source: <https://implyingrigged.info/wiki/Pro_Evolution_Soccer_2021/Edit_file>
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
team_player_start      = unknown_start + 2500 * 132       # = 0x9D4648
competition_entry_start = team_player_start + 750 * 284  # = 0xA08650
game_plan_start        = competition_entry_start + 0x1230 # = 0xA09880
```

**FL26 note:** These capacities and offsets were verified against multiple FL26
Update 2.2 saves. A future layout revision must be detected by validation rather
than inferred from header counts.

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

The live wiki also labels `0x209..0x20B` as free-kick roles, but those bytes
overlap the documented 40-byte lineup (`0x1E4..0x20B`). Actual FL26 saves show
them behaving as lineup tail bytes, so this project deliberately does not parse
or rewrite them as independent role fields.

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

**FL26 compatibility:** Verified by decrypt → encrypt → decrypt round trips on
multiple actual FL26 Update 2.2 saves.

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

### What was broken/missing in that reference project:
- **All files truncated on GitHub** — code cuts off mid-function (corrupted commits)
- No backup system (this repository now has rolling backups)
- No logging/audit
- Windows-only (hardcoded `.exe` paths)
- Hardcoded offsets (won't work with FL26 if table sizes differ)
- No pagination in scraper
- No rate limiting or retry handling
- No error handling for edge cases (player not found, team full, etc.)
- `team_utils.py` uses magic numbers: `f.seek(100, 1)` to skip to team name, `f.read(70)` for name — these correspond to: Team entry offset 0x068 for name (0x068 - 0x004 = 0x64 = 100 bytes after team ID), 70 bytes for name field. Correct for vanilla PES21 but not dynamically calculated.

Those dependencies describe the historical reference project, not this one.
FLEditScrape's runtime dependencies are `aiohttp` and `rapidfuzz`; `pytest` is
the development dependency.

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

## 8. Current Project Structure

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
│   ├── editfile.py          # Binary edit file reader/writer + validation
│   ├── backup.py            # Timestamped backup management
│   ├── player_catalog.py    # Current FL26 catalog + roster coverage gate
│   ├── locking.py           # Per-output cross-process lock
│   ├── logger.py            # Structured transfer logging (JSONL)
│   └── models.py            # TeamData / PlayerInfo dataclasses
├── data/
│   ├── team_aliases.json    # FotMob → FL26 team name mapping
│   ├── name_overrides.json  # Player name manual overrides
│   ├── FL2622wc_players.txt # Canonical Update 2.2 player-name reference
│   └── players.csv          # Roster-only legacy-ID fallback
├── tests/                   # Unit, regression, and pipeline tests
├── vendor/
│   └── pesXdecrypter/       # Compiled decrypter/encrypter binary
└── MEMORY.md                 # Architecture notes; binary layout links to the live wiki
```

---

## 9. Operational State

- The canonical input is `base/EDIT00000000`; successful default runs continue
  from `output/EDIT00000000` unless `--from-base` is explicit.
- FotMob is the transfer source. There is no legacy per-league scrape config.
- Loans, permanent transfers, releases, signings, and shirt-number observations
  use one chronological roster planner.
- A process lock prevents two runs from targeting the same output concurrently.
- The canonical name catalog is coverage-checked against every rostered ID.
- Overflow auto-release remains unavailable until complete roster position and
  OVR metadata is supplied and validated.

---

## 10. Key Technical Constants

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

## 11. Vanilla PES21 Verification Values

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

## 12. FL26 Known-Good Validation (2026-08-03)

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
- The first default run uses `base/`; later default/scheduled runs continue from
  the last verified output. `--from-base` explicitly requests a clean rebuild.

Final ingestion/mutation safety rules:

- Canonical `auto` cumulatively replays every available dated FotMob transfer
  through today. It does not depend on transfer-window calendars, base
  provenance, metadata, workflow dates, or a user-supplied page count.
- Global FotMob pagination continues until an empty/repeated page, guarded by
  an internal 250-page safety cap. The endpoint is sorted by `lastModified`,
  not `transferDate`; an old corrected record must not terminate pagination.
- Explicit `summer` and `winter` modes remain bounded manual filters;
  malformed `since_date` values are rejected instead of disabling filtering.
- Undated transactional events are excluded whenever a bounded date filter is
  active. Shirt-number observations remain separate `shirt_number_update`
  records and are never reported as club transfers.
- Every resolved fetch range is capped at the current UTC date. Pre-agreements
  may be visible in a source before registration opens, but must not mutate the
  game roster until FotMob's effective `transferDate` is reached.
- Manual club filters prefer normalized exact names and accept a substring only
  when it resolves uniquely; any unresolved requested club aborts that focused
  scrape instead of silently producing a partial result.
- FotMob club IDs are reduced to a conservative one-ID-per-PES-club allowlist.
  Manual major-club mappings win over similarly named women, youth, reserve, or
  duplicate sitemap teams; ID-less fuzzy club matches require at least 98%.
- FotMob `playerId` values are retained through deduplication and persisted in
  the per-save JSONL audit history. A unique historical FotMob-to-PES mapping
  can recover a renamed player; conflicting mappings or a disagreement with a
  current name/roster match are rejected without mutation.
- A transfer mutates the roster only if the player's actual current club equals
  the matched source. Signings require the player to be unregistered, releases
  require the expected source, already-applied events are no-ops, and any other
  state is a safety skip.
- The dry run uses the same decision gate as the real mutation path.
- Club/national-team classification must come from the league-membership block,
  not a numeric ID threshold. FL26 has playable clubs at IDs `<=100` (Manchester
  United is ID 100 in the supplied team database).
- Deep mode names shirt-only observations `shirt_number_update`, logs them only
  when the number really changes, deduplicates each player/club observation,
  and reports them separately from club transfers in HTML, Markdown, and GitHub
  Step Summary.
- A shirt-number observation that conflicts with another current squad member
  is a non-fatal safety skip. Known source-data conflicts must never roll back
  an otherwise valid transfer batch; unexpected binary mutation failures still do.
- Canonical base and output validate at 10,995,800 bytes, 749 rosters, 583
  clubs, zero duplicate club registrations, and 747 checked game plans.
- `data/FL2622wc_players.txt` is the authoritative current player-name catalog
  (29,502 IDs). `players.csv` is consulted only for rostered IDs absent from
  that reference; stale unrostered CSV players are not imported.
- Catalog construction must prove 100% coverage of roster IDs before matching.
  The verified canonical output needs one legacy roster fallback and has no
  missing roster identity.
- Name-only catalogs must not pretend to supply position, nationality, age, or
  OVR. Overflow auto-release fails closed until position and OVR coverage is
  complete for every rostered player.

Persistent agent preferences:

- Use RTK (Rust Token Killer) wrappers for shell work whenever an equivalent
  RTK command exists; use `rtk run` or `rtk proxy` when no filtered wrapper fits.
- Keep caveman full mode active for every response and task: terse Indonesian,
  no filler, full technical accuracy. Disable only when user explicitly asks.
