# FL Daily Edit

[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

FL Daily Edit updates SP Football Life 2026 and eFootball PES 2021 squads by
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
   [Deep Sync](https://github.com/gvoze32/fldailyedit/actions/workflows/sync-deep.yml)
   or [Fast Sync](https://github.com/gvoze32/fldailyedit/actions/workflows/sync-fast.yml)
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
- Reviewed player creations and attribute corrections through explicit player-spec commands

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
# Preview changes without writing a save
python run.py run --dry-run --edit-file base/EDIT00000000

# Validate an existing save
python run.py validate --edit-file base/EDIT00000000

# Validate one-file-per-player specs against the pristine base revision
python run.py players validate

# Apply reviewed specs explicitly to an existing output save
python run.py players apply \
  --base-revision fl26-u2.2-national-squads \
  --edit-file output/EDIT00000000 \
  --in-place

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
| `run` | Apply verified transfers only |
| `players validate` | Validate all player specs against the pristine base |
| `players apply` | Apply reviewed player specs explicitly to one save |
| `log` | Show recently applied transfers |
| `inspect` | Inspect teams, player counts, and save offsets |
| `validate` | Check roster registrations and game-plan mappings |
| `repair` | Repair a legacy base using reference saves |


`run` handles transfers only: it never loads or applies player specs. To combine
both workflows, first run the transfer command against an output save, then run
`players apply --in-place` against that same save.

## Player-spec contributions

Each reviewed contribution is one JSON file per player under `players/`. The
engine accepts schema version 1 with an `operation` (`create` or `update`), a
lifecycle (`active`, `upstreamed`, or `retired`), exact `applies_to` base
revisions, stable player identity, cited evidence, and PES data. Creation specs
contain the complete player record and destination roster data. Update specs may
patch the whitelisted groups: abilities, position proficiency, playing style,
player skills, COM styles, nationality, and physical/basic settings. The
registered position is also whitelisted. Every patch requires literal `from`
and `to` values.

### Simple issue path

1. Open the [player-spec issue form](.github/ISSUE_TEMPLATE/player-spec.yml),
   provide the profile and proof URLs, and wait for a maintainer to apply the
   exact `generate-player-draft` label.
2. The configured generator workflow opens a draft PR containing one
   intentionally incomplete `players/<player-slug>.json` file. Source text is
   data, not approved PES values.
3. A contributor or maintainer replaces every listed placeholder with explicit
   PES values. Update specs must also state the expected current (`from`) value
   for every proposed (`to`) value.
4. CI accepts a player-spec change only when the PR adds or modifies exactly one
   canonical player JSON path and the shared semantic validator succeeds.
5. Merging the PR is the approval state. There is no separate `approved` flag
   in the JSON file.

The generated draft is expected to fail validation until all human fields named
by its `draft.missing` list have been completed and the draft-only metadata has
been removed.

### Direct one-file PR path

An advanced contributor may skip the issue-generated draft and directly open a
PR that adds or modifies exactly one `players/<player-slug>.json` file. Supply
the same cited evidence, explicit PES values, expected baselines, lifecycle,
and exact base revision, then run `python run.py players validate` before
requesting review. Keep other code or documentation changes out of that PR.

Application is always an explicit command and requires the exact revision from
`data/base_manifest.json`; a revision mismatch fails before decrypting the
target save.

### Revision lifecycle

When the official base changes, update `base/EDIT00000000` and
`data/base_manifest.json` together. Keep historical specs in `players/`; do not
delete them merely because the revision changed. An active spec whose
`applies_to` list does not contain the new revision is inactive: validation
reports `needs_review` and application skips it. After review, add the new
revision only when the spec still applies, mark it `upstreamed` when the
official base includes its change, or mark it `retired` when it no longer
applies.

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
seasonal lists, enabled SortitoutSI transfer submissions, and verified dated
Transfermarkt records supplement or confirm transfer routes. Approved
SortitoutSI ability submissions provide CA changes for read-only stat proposals.

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

FL Daily Edit is available under the [MIT License](LICENSE).
