[![English](https://img.shields.io/badge/%F0%9F%87%AC%F0%9F%87%A7_English-012169?style=flat-square)](README.md) [![Indonesian](https://img.shields.io/badge/%F0%9F%87%AE%F0%9F%87%A9_Indonesian-ce1126?style=flat-square)](README.id.md) [![Español](https://img.shields.io/badge/%F0%9F%87%AA%F0%9F%87%B8_Espa%C3%B1ol-aa151b?style=flat-square)](README.es.md) [![Français](https://img.shields.io/badge/%F0%9F%87%AB%F0%9F%87%B7_Fran%C3%A7ais-002395?style=flat-square)](README.fr.md) [![Português](https://img.shields.io/badge/%F0%9F%87%B5%F0%9F%87%B9_Portugu%C3%AAs-006600?style=flat-square)](README.pt.md) [![Deutsch](https://img.shields.io/badge/%F0%9F%87%A9%F0%9F%87%AA_Deutsch-000000?style=flat-square)](README.de.md) [![Italiano](https://img.shields.io/badge/%F0%9F%87%AE%F0%9F%87%B9_Italiano-009246?style=flat-square)](README.it.md) [![Русский](https://img.shields.io/badge/%F0%9F%87%B7%F0%9F%87%BA_%D0%A0%D1%83%D1%81%D1%81%D0%BA%D0%B8%D0%B9-d52b1e?style=flat-square)](README.ru.md) [![Türkçe](https://img.shields.io/badge/%F0%9F%87%B9%F0%9F%87%B7_T%C3%BCrk%C3%A7e-e30a17?style=flat-square)](README.tr.md) [![العربية](https://img.shields.io/badge/%F0%9F%87%B8%F0%9F%87%A6_%D8%A7%D9%84%D8%B9%D8%B1%D8%A8%D9%8A%D8%A9-006c35?style=flat-square)](README.ar.md) [![中文](https://img.shields.io/badge/%F0%9F%87%A8%F0%9F%87%B3_%E4%B8%AD%E6%96%87-de2910?style=flat-square)](README.zh.md)

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

## Windows installer

The Windows installer is the recommended option for beginners. The installer interface is currently available in English only. Current validated downloads support **Football Life 2026 Update 2.2 + SmokePatch's National Squads Update only**. Detection for vanilla eFootball PES 2021 is present, but installation remains disabled until a matching validated base is published.

1. Download [FLDailyEditInstaller.exe](https://github.com/gvoze32/fldailyedit/releases/download/latest/FLDailyEditInstaller.exe).
2. Close the game.
3. Choose **Fast** or **Deep**. They are separate update-coverage choices, and each displays its generation timestamp.
4. Confirm the detected Football Life 2026 folder, or use **Browse** if needed.
5. Select **Download and install**. The installer verifies the download, backs up the current save, and replaces it atomically.

**Update an existing save through the GUI:** The installer can also update a
user-selected common-layout `EDIT00000000` instead of installing a prebuilt
release. Choose **Update my local save**, select a detected location or use
**Browse**, choose **Fast** or **Deep**, then review and choose **Apply update**.
The wizard validates the save before mutation, creates an in-place backup, and
reports progress, results, or diagnostics. Local eligibility does not depend on
the SPFL/PES/UML label, and this path does not download a prebuilt remote release.
When those optional external SPFL catalogs are unavailable, the local matcher
falls back to player and team names embedded in the selected save, so the
packaged local-update path can run without them.

> [!WARNING]
> The initial executable is unsigned, so Windows SmartScreen may display a warning. Before continuing, compare the downloaded file against the published `FLDailyEditInstaller.exe.sha256` on the [latest release](https://github.com/gvoze32/fldailyedit/releases/tag/latest).
> If Windows blocks the installer through Smart App Control, open **Settings → Privacy & security → Windows Security → App & browser control → Smart App Control settings** and switch it to **Off**. Alternatively, right-click the downloaded file, open **Properties**, and check **Unblock** if available.

For a manual installation without the installer, download the public [Fast release ZIP](https://github.com/gvoze32/fldailyedit/releases/download/latest/fldailyedit-fl2026-fast.zip) or [Deep release ZIP](https://github.com/gvoze32/fldailyedit/releases/download/latest/fldailyedit-fl2026-deep.zip). Extract `EDIT00000000`, back up your current save, then copy the extracted file to:

`Documents\KONAMI\eFootball PES 2021 SEASON UPDATE\2026\save\`

For an on-demand run or a custom club list, fork the repository and use **Run workflow** from the Actions tab.

## What it updates

- Transfers, releases, loans, and loan returns
- Available squad numbers from FotMob squad data
- Player identities checked against the current FL26 roster
- Lineups and game plans affected by roster changes
- Transfer reports and JSON Lines audit logs
- Daily prebuilt saves through GitHub Actions
- Reviewed player creations and attribute corrections through explicit Player Update commands

The updater does not overwrite a shirt number already used by another squad
member. It also checks the player's current club before applying a move.

## Roadmap / Complete for now

All current roadmap items are complete. We are waiting for the next useful idea.

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

# Validate one-file-per-player updates against the pristine base revision
python run.py players validate

# Apply reviewed Player Updates explicitly to an existing output save
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
| `players validate` | Validate all Player Updates against the pristine base |
| `players apply` | Apply reviewed Player Updates explicitly to one save |
| `log` | Show recently applied transfers |
| `inspect` | Inspect teams, player counts, and save offsets |
| `validate` | Check roster registrations and game-plan mappings |
| `repair` | Repair a legacy base using reference saves |


`run` handles transfers only: it never loads or applies Player Updates. To combine
both workflows, first run the transfer command against an output save, then run
`players apply --in-place` against that same save.

## Player Updates

Each reviewed Player Update is one completed schema-version-2 JSON file per
player under `players/`. It records an `operation` (`create` or `update`), a
lifecycle (`active`, `upstreamed`, or `retired`), exact `applies_to` base
revisions, stable player identity and Pes Retro Stats UUID/profile provenance,
cited evidence, and reviewed PES data. Create updates contain a proposed
complete player record and destination roster data. Existing-player updates
contain only supported values that differ from the verified base; every change
records literal `from` and `to` values.
Supported update groups are abilities, position proficiency, playing style,
player skills, COM styles, nationality, physical/basic settings, and
registered position.

### Simple issue path

1. Open the [player update issue form](.github/ISSUE_TEMPLATE/player-update.yml).
   Enter the `Player name` exactly as shown on one canonical `Pes Retro Stats
   profile`, provide the proof URLs, and wait for a maintainer to apply the
   exact `generate-player-draft` label.
2. The configured generator workflow fetches that profile and opens a draft PR
   containing one schema-version-2 `players/<player-slug>.json` proposal. It
   derives the source snapshot, identity, physical settings, position data,
   abilities, playing style, skills, and COM styles from the profile.
3. For a create, only game-local values unavailable from the source remain
   listed in `draft.missing`: the PES IDs and print names for the identity and
   player, team ID and name, nationality ID, skin color, and iris color. A
   contributor or maintainer must supply them. For an update, the generator
   resolves the player in the verified base and emits only actual `from`/`to`
   differences. A source position unsupported by PES 2021, such as `RWB`, is
   omitted rather than remapped, including from the registered-position change.
4. A contributor and maintainer review every generated value as an unapproved
   proposal. CI accepts a Player Update only when the PR adds or modifies
   exactly one canonical player JSON path and the shared semantic validator
   succeeds.
5. Merging the PR remains the human approval state. There is no separate
   `approved` flag in the JSON file.

Every generated proposal is expected to fail completed-file validation. To
convert its generated evidence to completed schema v2, remove the draft-only
`evidence.current_team`, `evidence.issue_number`, and `evidence.issue_url`
fields; retain the canonical `evidence.profile_url`, reviewed
`evidence.proof_urls`, and `evidence.effective_date`; and add a reviewed,
non-empty `evidence.reason`. Persist the canonical profile UUID as
`identity.pes_retro_stats_id` and only the reviewed gameplay values in `pes`.
For a create, also complete every game-local field named by `draft.missing`.
Then remove the top-level `source` and `draft` objects, which are review-only
generated-draft metadata, before completed validation.

### Direct one-file PR path

An advanced contributor may skip the issue-generated draft and directly open a
PR that adds or modifies exactly one completed
`players/<player-slug>.json` file. Supply the canonical UUID/profile provenance
in `identity` and `evidence`, cited proof, reviewed PES values, expected update
baselines, lifecycle, and exact base revision, then run
`python run.py players validate` before requesting review. Do not include the
generated draft's top-level `source` or `draft` metadata. Keep other code or
documentation changes out of that PR.

Application is always an explicit command and requires the exact revision from
`data/base_manifest.json`; a revision mismatch fails before decrypting the
target save.

### Revision lifecycle

When the official base changes, update `base/EDIT00000000` and
`data/base_manifest.json` together. Keep historical Player Updates in
`players/`; do not delete them merely because the revision changed. An active
Player Update whose `applies_to` list does not contain the new revision is
inactive: validation reports `needs_review` and application skips it. After
review, add the new revision only when the Player Update still applies, mark it
`upstreamed` when the official base includes its change, or mark it `retired`
when it no longer applies.

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
Transfermarkt records supplement or confirm transfer routes. Pes Retro Stats
profiles provide source-derived, unapproved proposals for Player Update drafts.

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
