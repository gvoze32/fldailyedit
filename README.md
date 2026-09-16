# FL Daily Edit

[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Update SP Football Life 2026 and eFootball PES 2021 `EDIT00000000` saves with
verified real-world transfers, squad numbers, captain roles, and current club
managers.

## Installation

**Installer (recommended)**

1. Download and extract [FLDailyEditInstaller.zip](https://github.com/gvoze32/fldailyedit/releases/download/latest/FLDailyEditInstaller.zip).
2. Close the game and choose **Fast** or **Deep**.
3. Confirm the Football Life folder, then select **Download and install**.

The installer verifies the release, backs up the current save, and replaces it
atomically. For vanilla PES 2021 or T99, choose **Update my local save**, select
the save and matching PES 2021 game folder containing `download/*.cpk`, then
choose **Apply update**. Prebuilt releases are for FL26 saves only.

The installer is unsigned. Verify `FLDailyEditInstaller.zip` against the
published `FLDailyEditInstaller.zip.sha256` on the
[latest release](https://github.com/gvoze32/fldailyedit/releases/tag/latest)
before running it.

**Manual installation:** Download the [Fast release ZIP](https://github.com/gvoze32/fldailyedit/releases/download/latest/fldailyedit-fl2026-fast.zip)
or [Deep release ZIP](https://github.com/gvoze32/fldailyedit/releases/download/latest/fldailyedit-fl2026-deep.zip).
Back up your save, extract `EDIT00000000`, and copy it to:

`Documents\KONAMI\eFootball PES 2021 SEASON UPDATE\2026\save\`

For custom club lists or on-demand runs, fork the repository and use
**Run workflow** in the Actions tab.

## Compatibility & Update Modes

The bundled [base save](base/EDIT00000000) requires:

- **SP Football Life 2026 Update 2.2**
- **SmokePatch's National Squads Update**

It is based on [Gondowan's final FL26 EDIT file](https://www.reddit.com/r/SPFootballLife/comments/1wfnjz6/release_gondowan_final_edit_file_for_fl26/),
its tactical game plans use [Klashman69's PES 2021 real-teams tactics](https://evoweb.uk/threads/pes-2021-tactics-discussion-real-teams-thread.84178/page-29).
EPL coverage is complete, other top leagues remain beta while tactics are refined.
The base save is incompatible with UML, older FL26 versions, and installations
without the national-squad update. Start a new Master League or Become a Legend
career after installing it.

### Fast vs Deep

| Mode                | Coverage                                                                                                          | Best for        |
| ------------------- | ----------------------------------------------------------------------------------------------------------------- | --------------- |
| **Fast (default)**  | Live transfer feed plus squad membership, shirt numbers, and captain roles for up to 32 clubs found in that feed. | Daily updates   |
| **Deep (`--deep`)** | Current squad membership, shirt numbers, and captain roles for every indexed club.                                | Broad refreshes |

Use **Fast** for routine updates, choose **Deep** for broader coverage.

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
- Indexed-club rosters, shirt numbers, affected lineups, and game plans
- Current captain roles and club managers (`manager-update --auto`)
- Transfer reports, audit logs, and daily prebuilt saves through GitHub Actions

The updater checks each player's current club and never overwrites an occupied
shirt number. Clean PES21 saves may retain numbers in empty roster slots, these
are reported as non-blocking warnings.

## Transfer logs

Successful `run` and `manager-update` commands append applied transfer, roster,
captain, and manager changes to `data/transfer_log.jsonl`. `run` also refreshes
`output/transfer_summary.md` and `output/transfer_summary.html`.

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

# Preview current club managers
python run.py manager-update \
  --edit-file /path/to/EDIT00000000 \
  --auto \
  --dry-run

# Apply current managers in place
python run.py manager-update \
  --edit-file /path/to/EDIT00000000 \
  --auto \
  --in-place

python run.py run --help
```

`run` applies verified transfers, releases, loans, returns, squad-number updates,
and captain roles. `manager-update --auto` syncs FotMob managers to existing
Manager Entry records. Other audit, comparison, logging, and repair commands are
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
