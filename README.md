# FL Daily Edit

[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Update SP Football Life 2026 and eFootball PES 2021 `EDIT00000000` saves with
verified real-world transfers, squad numbers, and captain roles.

## Project status

FL Daily Edit is no longer actively developed, and the daily prebuilt saves
are no longer updated. The last published releases remain available, but new
features, bug fixes, and issue responses should not be expected. Forks can
still enable the workflows and run them on demand.

My Evoweb account was banned for "stealing other people's work and claiming
it as your own." I dispute this. FL Daily Edit was written from scratch, and
its full development history, from the
[first commit](https://github.com/gvoze32/fldailyedit/commit/07dfac340f307eb881f6d680288b99eeb8ea75ba)
on 2026-08-02 onward, is public in the
[commit log](https://github.com/gvoze32/fldailyedit/commits). Third-party
components under `vendor/` keep their original licenses and attribution.

This may be both my first and last PES mod. Thanks to everyone who used the
project, reported issues, and helped improve it.

## Installation

**Installer (recommended)**

1. Download and extract [FLDailyEditInstaller.zip](https://github.com/gvoze32/fldailyedit/releases/download/latest/FLDailyEditInstaller.zip).
2. Close the game and choose **Fast** or **Deep**.
3. Confirm the Football Life folder, then select **Download and install**.

The packaged Windows app checks for a newer installer when it starts. Use the
**Check for app updates** button to download, verify, and install the latest
app version; the app restarts automatically after the verified download. App
updates are accepted only when `installer-update.json` carries a valid Ed25519
signature (`installer-update.json.sig`) from the project release key.

The installer verifies the release, backs up the current save, and replaces it
atomically. For vanilla PES 2021 or T99, choose **Update my local save**, select
the save and matching PES 2021 game folder containing `download/*.cpk`, then
choose **Apply update**. Prebuilt releases are for FL26 saves only.

The installer is unsigned. Verify `FLDailyEditInstaller.zip` against the
published `FLDailyEditInstaller.zip.sha256` on the
[latest release](https://github.com/gvoze32/fldailyedit/releases/tag/latest)
before running it, Windows SmartScreen may warn.

**Manual installation:** Download the [Fast release ZIP](https://github.com/gvoze32/fldailyedit/releases/download/latest/fldailyedit-fl2026-fast.zip)
or [Deep release ZIP](https://github.com/gvoze32/fldailyedit/releases/download/latest/fldailyedit-fl2026-deep.zip).
Back up your save, extract `EDIT00000000`, and copy it to:

`Documents\KONAMI\eFootball PES 2021 SEASON UPDATE\2026\save\`

For on-demand runs, fork the repository and use
**Run workflow** in the Actions tab. Building the Windows installer additionally
requires an `INSTALLER_SIGNING_KEY` secret (PEM PKCS#8 Ed25519 private key
matching the public key in `installer/update.py`).

## Compatibility & Update Modes

The bundled [base save](base/EDIT00000000) requires:

- **SP Football Life 2026 Update 2.2**
- **SmokePatch's National Teams Selection Update**

The base EDIT file is from Gondowan's [Road to FL27 MiniPatch](https://www.reddit.com/r/SPFootballLife/comments/1wssmur/release_minipatch_road_to_fl27_for_football_life/).
It updates transfers, overalls, selected lineups, and national-team call-ups.

The base is incompatible with UML, other patches that modify the database, older FL26 versions, and installations without the National Teams update.

Start a new Master League or Become a Legend career after installing it.

### Fast vs Deep

| Mode                | Coverage                                                                                                     | Best for        |
| ------------------- | ------------------------------------------------------------------------------------------------------------ | --------------- |
| **Fast (default)**  | Live transfer feed plus squad membership, shirt numbers, and captain roles for every save club touched by any source's transfer events. | Daily updates   |
| **Deep (`--deep`)** | Current squad membership, shirt numbers, and captain roles for every save club matched to FotMob.            | Broad refreshes |

Use **Fast** for routine updates, choose **Deep** for broader coverage.

Roster changes come from verified transfer events or complete FotMob squad
snapshots matched to existing local identities. Short provider aliases require
matching shirt, age, and compatible-role evidence; ambiguous or low-coverage
evidence does not force player-specific corrections.

Club identity is learned per selected save, not from curated club lists: save
club names are matched against the generated FotMob team index
(`data/fotmob_teams.json`) and confirmed by FotMob squad overlap. Learned
bindings are cached under `storage/` (or the installer's user-state directory).

Each run loads and validates the save before scraping. When a run continues the
save it last wrote (in-place or the default incremental output), the transfer
window starts 7 days before that save's last applied log entry, extended back to
the oldest still-pending relevant transfer that was not applied. Rebuilds from a
different input, and saves without a log, use the previous window start
(Jan 1 / Jun 1); the automatic window never starts earlier than that.

### PES 2021/T99 patch saves

`run` supports vanilla PES 2021 and T99 patch `EDIT00000000` files when matching
native database CPKs are available. Point `--game-root` at the PES installation
(with `download`) or a directory containing the patch CPKs:

```bash
python run.py run \
  --edit-file "/path/to/T99/EDIT00000000" \
  --game-root "/path/to/T99 Patch V10" \
  --dry-run
```

Use the save and CPKs from the same patch generation. The updater reads native
`Player.bin`, `Team.bin`, and `PlayerAssignment.bin`, it never uses the bundled
FL26 player catalog. A missing or mismatched native `Player.bin` is rejected
before any save mutation.

## What it updates

- Verified transfers, releases, loans, and loan returns
- Evidence-driven roster reconciliation for indexed clubs: shirt numbers,
  affected lineups, and game-plan repairs
- Current captain roles
- Evidence-gated tactical choices in the main game-plan preset
- FotMob-derived formations and role/coordinate layouts in the main game-plan preset
- Transfer reports, audit logs, and daily prebuilt saves through GitHub Actions

Tactical settings are generated from FotMob current-season league aggregates:
possession share, accurate long-ball/pass rate, accurate-cross/pass rate, and
possessions won in the attacking third. These are league-relative proxies, not
tracking or PPDA measurements. For a team with at least three matches and
evidence from at least eight league teams, categorical choices change only at
the top or bottom quartile. Containment area uses cross/pass rate as a width
proxy (low = Middle, high = Wide); pressuring uses attacking-third recoveries
(low = Conservative, high = Aggressive). Defensive Line and Compactness both
scale those recoveries across the league to the game's 1-10 range; they are
not independent spatial measurements. Missing or uniform evidence preserves
the existing value. The updater can change all eight supported controls in the
main preset: attacking style, build-up, attacking area, defensive style,
containment area, pressuring, defensive line, and compactness.

Formations, starting XI, bench, and detailed positions come from FotMob's last
lineup (starters and substitutes), otherwise the latest finished match page
with an unambiguous team-name match. The game plan is resolved after the run's
roster changes. Valid 10-player shapes are converted into PES role and
coordinate layouts for all three phases of the main preset; the two alternate
presets are untouched. Empty captain and set-piece roles are filled from save
data. Fast mode applies formations only to clubs with refreshed squad
snapshots, while Deep mode covers all matched clubs with complete snapshots.
Missing or unsupported formation evidence preserves the existing bytes.

The updater checks each player's current club and never overwrites an occupied
shirt number. Clean PES21 saves may retain numbers in empty roster slots, these
are reported as non-blocking warnings.

## Transfer logs

Successful `run` commands append applied transfer, roster, and captain changes
to `data/transfer_log.jsonl`. `run` also refreshes
`output/transfer_summary.md` and `output/transfer_summary.html`.

Transfers that could not be applied are never forced into the save: players
missing from the save are reported, never created. Each run lists them, with a
reason code, in a **Not applied** report section and in
`skipped_transfers.jsonl` beside the reports, with transfers touching save
clubs listed first. The CLI prints the full list grouped by reason, and the
installer shows the same list after a local update. `--dry-run` also prints
the per-team XI, bench, and captain changes a run would make.

When applying a prebuilt release, the installer writes and displays the
bundled transfer report as a timestamped Markdown file under `FLDailyEditLogs`
beside `EDIT00000000`.

## Transfer sources

FotMob is the primary source for current transfer events. Transfermarkt and
Wikipedia provide dated routes and details, Sortitoutsi, BeSoccer, Sofascore,
and Soccerway corroborate them. For same-day route conflicts, precedence is
FotMob > Transfermarkt > Wikipedia. Corroboration sources never override a
primary route or create transfer events.

Sofascore and Soccerway scan only relevant primary-source clubs. Soccerway reads
the first transfer page per club by default, supports deeper history through
`max_pages`, and has a 60-second source budget. Optional-source failures do not
block a run, incomplete or ambiguous events are skipped.

Transfermarkt scans continue back to the run's since-date in both modes, so
events on later pages are not dropped by a time budget. Fast mode has no club
cap: every save club touched by a transfer event from any source is refreshed.

## Run locally

Python 3.10+ is required.

```bash
git clone https://github.com/gvoze32/fldailyedit.git
cd fldailyedit
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cd vendor/pesXdecrypter
make
cd ../..
```

## Common commands

```bash
# Preview current-cycle transfers
python run.py run --dry-run --edit-file base/EDIT00000000

# Apply current-cycle transfers (default window)
python run.py run --window auto

# Replay all available transfer history
python run.py run --window all

# Update a specific save in place
python run.py run --edit-file /path/to/EDIT00000000 --in-place

# Validate a save
python run.py validate --edit-file /path/to/EDIT00000000


python run.py run --help
```

`run` applies verified transfers, releases, loans, returns, squad-number updates,
and captain roles. Other audit, comparison, logging, and repair commands are
listed by `python run.py <command> --help`.

## Safety

- Saves are validated before and after changes.
- Local runs create rolling backups and use atomic, verified encryption.
- A process lock prevents concurrent writes to the same output.
- Primary-source failures may abort a run, optional-source failures are isolated.
- Incomplete or ambiguous data is skipped rather than forced into the save.
- Roster compaction preserves tactical game-plan positions, updates lineup slots
  and goalkeeper placement, and repairs role zero from known goalkeeper metadata.
  Validation rejects non-empty plans without exactly one goalkeeper marker per
  tactical phase.

## Development

```bash
pytest -v
```

## License

FL Daily Edit is available under the [MIT License](LICENSE).
