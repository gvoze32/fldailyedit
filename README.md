# FL Daily Edit

[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Update SP Football Life 2026 and eFootball PES 2021 `EDIT00000000` saves with
verified real-world transfers and squad-number updates.

## Compatibility

The bundled [base save](base/EDIT00000000) requires:

- **SP Football Life 2026 Update 2.2**
- **SmokePatch's National Squads Update**

The bundled base save is based on [Gondowan's latest EDIT file](https://www.reddit.com/r/SPFootballLife/comments/1vvh129/release_gondowans_edit_file_22082026_latest/).
The Premier League game plans combine [MG-FOXHOUND's Reddit tactics update](https://www.reddit.com/r/SPFootballLife/comments/1vzspt0/download_real_2627_premier_league_tactics_updated/) with [Klashman69's EPL 26/27 tactics release](https://evoweb.uk/threads/pes-2021-tactics-discussion-real-teams-thread.84178/page-29), using Klashman's tactical settings while preserving the base's current FL26 roster data and EPL lineups.

It is not compatible with UML, older FL26 versions, or installations without
the national-squad update. Start a new Master League or Become a Legend career
after installing it.

## Install on Windows

The installer is the easiest option:

1. Download and extract [FLDailyEditInstaller.zip](https://github.com/gvoze32/fldailyedit/releases/download/latest/FLDailyEditInstaller.zip).
2. Close the game and choose **Fast** or **Deep**.
3. Confirm the Football Life folder, then select **Download and install**.

The installer verifies the release, backs up the current save, and replaces it
atomically. To update an existing save, choose **Update my local save**, select
the save, then choose **Apply update**.

The installer is unsigned. Verify `FLDailyEditInstaller.zip` against the
published `FLDailyEditInstaller.zip.sha256` on the
[latest release](https://github.com/gvoze32/fldailyedit/releases/tag/latest)
before running it; Windows SmartScreen may show a warning.

For manual installation, download the [Fast release ZIP](https://github.com/gvoze32/fldailyedit/releases/download/latest/fldailyedit-fl2026-fast.zip)
or [Deep release ZIP](https://github.com/gvoze32/fldailyedit/releases/download/latest/fldailyedit-fl2026-deep.zip).
Back up your save, extract `EDIT00000000`, and copy it to:

`Documents\KONAMI\eFootball PES 2021 SEASON UPDATE\2026\save\`

For a custom club list or on-demand run, fork the repository and use
**Run workflow** in the Actions tab.

## Fast vs Deep

- **Fast:** Quicker, but only checks the most recent transfers.
- **Deep:** Takes longer, but checks every team's full roster to make sure nothing is missed.

Fast is the default. Add `--deep` when you want broader coverage.

## What it updates

- Transfers, releases, loans, and loan returns
- Shirt numbers, lineups, and game plans affected by roster changes
- Transfer reports and audit logs
- Daily prebuilt saves through GitHub Actions

It checks the player's current club and never overwrites a shirt number already
used by another squad member.

Clean PES21 saves may retain shirt numbers in empty roster slots. These are
reported as non-blocking warnings and do not prevent a local update.

## Transfer logs

Each successful updater run records applied changes in
`data/transfer_log.jsonl` and refreshes `output/transfer_summary.md` plus
`output/transfer_summary.html`.

When the Windows installer applies a prebuilt Fast or Deep release, it writes
the bundled transfer report as a timestamped Markdown file under
`FLDailyEditLogs` beside `EDIT00000000`.
The installer also displays that report directly on its completion screen.

## Transfer sources

FotMob is the primary source for current transfer events. The other sources
supplement it and help resolve incomplete or ambiguous coverage:

- Wikipedia's confirmed seasonal transfer lists corroborate transfer routes and
  dated moves.
- Sortitoutsi activity provides fast transfer signals that can
  corroborate or safely enrich a verified event.
- Transfermarkt provides additional dated transfer details, fees, and stable
  player/club identifiers.
- BeSoccer's current confirmed-transfer feed corroborates transfer routes,
  dates, fees, and transfer types.
- Sofascore's rendered team pages corroborate dated transfer routes.
- Soccerway resolves relevant clubs and corroborates their dated transfer routes.

The optional Soccerway corroboration scan reads the first transfer page per
relevant primary club by default; deeper history can request a larger
`max_pages` value.
Soccerway has a 60-second source budget so a blocked feed cannot hold the
pipeline indefinitely.

BeSoccer, Sofascore, and Soccerway can add provenance to an existing verified
FotMob/Transfermarkt/Wikipedia route, but never create a new transfer event.
Failures or missing data from these optional sources do not block a run.
Events that remain incomplete or ambiguous are skipped rather than forced into
the save.

## Run locally

Supported on macOS, Linux, and Windows through WSL. Python 3.10 or newer is
required.

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
# Preview transfers without writing a save
python run.py run --dry-run --edit-file base/EDIT00000000

# Apply all available transfers
python run.py run --window auto

# Rebuild from the bundled base
python run.py run --from-base --window auto

# Update a specific save in place
python run.py run --edit-file /path/to/EDIT00000000 --in-place

# Validate a save
python run.py validate --edit-file /path/to/EDIT00000000

# Show command options
python run.py run --help
```

`run` applies verified transfers, releases, loans, returns, and squad-number
updates. Use `python run.py <command> --help` for audit, comparison, logging,
and repair tools.

## Safety

- Saves are validated before and after changes.
- Local runs create rolling backups and use atomic, verified encryption.
- A process lock prevents concurrent writes to the same output.
- FotMob/primary-source failures can abort a run; optional supplemental-source
  failures are isolated and produce no corroboration instead.
- Incomplete source data and ambiguous matches are skipped rather than forced
  into the save.
- Roster compaction preserves existing tactical game-plan positions while
  updating lineup slot references and goalkeeper placement.

## Development

```bash
pytest -v
```

## License

FL Daily Edit is available under the [MIT License](LICENSE).
