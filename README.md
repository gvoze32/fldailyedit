# FLEditScrape

[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

FLEditScrape updates SP Football Life 2026 and eFootball PES 2021 squads by
applying real-world transfers to an `EDIT00000000` save file.

## Compatibility

The bundled base targets **SP Football Life 2026**. It requires:

- Football Life 26 Update 2.2
- SmokePatch's National Squads Update

It is not compatible with UML, older FL26 versions, or installations without
the national-squad update. Start a new Master League or Become a Legend career
after installing the save.

The [bundled base](base/EDIT00000000) is
[Gondowan's Mid-Summer EDIT](https://www.reddit.com/r/SPFootballLife/comments/1v7z782/release_gondowans_midsummer_edit_file_more_than/),
dated 27 July 2026. It includes more than 500 transfers, updated ratings,
positions, squad numbers, loan returns, managers, lineups, and promotion or
relegation changes. It does not create players or add promoted clubs from third
divisions.

## Download the latest save

GitHub Actions generates an updated save and transfer reports each day.

> [!NOTE]
> GitHub requires you to sign in before downloading workflow artifacts.

1. Open the latest successful
   [Deep Sync](https://github.com/gvoze32/fleditscrape/actions/workflows/sync-deep.yml)
   or [Fast Sync](https://github.com/gvoze32/fleditscrape/actions/workflows/sync-fast.yml)
   run.
2. Download `updated-fl-save-and-reports.zip` from the **Artifacts** section.
3. Extract `EDIT00000000`.
4. Back up your current save, then copy the extracted file to the appropriate
   directory:

| Game | Save directory on Windows |
|---|---|
| SP Football Life 2026 | `Documents\KONAMI\eFootball PES 2021 SEASON UPDATE\2026\save\` |
| eFootball PES 2021 | `Documents\KONAMI\eFootball PES 2021 SEASON UPDATE\<user_id>\save\` |

For an on-demand run or a custom club list, fork the repository and use
**Run workflow** from the Actions tab.

## What it updates

- Transfers, releases, loans, and loan returns
- Available squad numbers from FotMob squad data
- Player identities checked against the current FL26 roster
- Lineups and game plans affected by roster changes
- Transfer reports and JSON Lines audit logs
- Daily prebuilt saves through GitHub Actions

The updater does not overwrite a shirt number already used by another squad
member. It also checks the player's current club before applying a move.

## Safety and limitations

- Local runs create rolling backups and use atomic, verified encryption.
- Saves are validated before and after roster changes.
- A process lock prevents two runs from writing the same output at once.
- Incomplete FotMob snapshots abort the run instead of producing a partial save.
- Ambiguous player matches, source-club mismatches, and full destination squads
  are skipped.
- Wikipedia, Sortitoutsi, and Transfermarkt are supplemental. An outage in one
  of these sources does not invalidate a complete FotMob snapshot.
- `--allow-overflow-release` fails closed because the bundled catalog does not
  contain complete position and OVR data for every player.

## Run locally

Local setup is supported on macOS, Linux, and Windows through WSL. Python 3.10
or newer is required.

```bash
git clone https://github.com/gvoze32/fleditscrape.git
cd fleditscrape

python3 -m venv .venv
source .venv/bin/activate
pip install -e .

cd vendor/pesXdecrypter
make
cd ../..
```

## Common commands

```bash
# Preview changes without writing a save
python run.py run --dry-run --edit-file base/EDIT00000000

# Validate an existing save
python run.py validate --edit-file base/EDIT00000000

# Apply all effective transfers available through today
python run.py run --window auto

# Rebuild from the bundled base
python run.py run --from-base --window auto

# Update a specific save in place
python run.py run --edit-file /path/to/EDIT00000000 --in-place

# Show every run option
python run.py run --help
```

| Command | Purpose |
|---|---|
| `run` | Collect, match, and apply transfers |
| `log` | Show recently applied transfers |
| `inspect` | Inspect teams, player counts, and save offsets |
| `validate` | Check roster registrations and game-plan mappings |
| `repair` | Repair a legacy base using reference saves |

Common `run` options:

| Option | Purpose |
|---|---|
| `--deep` | Fetch every locally indexed FotMob club |
| `--club "Chelsea,Arsenal"` | Limit the run to selected clubs |
| `--window auto` | Replay all dated transfers available through today |
| `--window summer` | Use the latest 1 June–30 September range |
| `--window winter` | Use the selected year's January–February range |
| `--since YYYY-MM-DD` | Set a manual lower date bound |
| `--dry-run` | Plan changes without writing a save |
| `--from-base` | Start from `base/EDIT00000000` |
| `--fotmob-only` | Run without supplemental transfer sources |

Without `--from-base`, a normal run continues from the last verified output.
This prevents transfers from disappearing when a later scheduled run reads the
cumulative history again.

## Transfer sources

FotMob provides the primary transfer history and squad metadata. Wikipedia
seasonal lists, enabled Sortitoutsi submissions, and verified dated
Transfermarkt records supplement or confirm transfer routes.

Records from different sources are reconciled without discarding their dates,
IDs, citations, or proof links. Undated, future-effective, conflicting, or
ambiguous events cannot update the save on their own.

Player matching starts with the source roster and uses the destination roster
as an idempotent fallback. Position, nationality, and age are considered only
when that information is available.

## Development

Run the test suite with:

```bash
pytest -v
```

The suite covers save parsing and validation, transfer reconciliation, roster
planning, loan history, player matching, squad limits, reporting, backups, and
process locking.

## License

FLEditScrape is available under the [MIT License](LICENSE).
