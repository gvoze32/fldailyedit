[![English](https://img.shields.io/badge/%F0%9F%87%AC%F0%9F%87%A7_English-012169?style=flat-square)](README.md) [![Indonesian](https://img.shields.io/badge/%F0%9F%87%AE%F0%9F%87%A9_Indonesian-ce1126?style=flat-square)](docs/readmes/README.id.md) [![Español](https://img.shields.io/badge/%F0%9F%87%AA%F0%9F%87%B8_Espa%C3%B1ol-aa151b?style=flat-square)](docs/readmes/README.es.md) [![Français](https://img.shields.io/badge/%F0%9F%87%AB%F0%9F%87%B7_Fran%C3%A7ais-002395?style=flat-square)](docs/readmes/README.fr.md) [![Português](https://img.shields.io/badge/%F0%9F%87%B5%F0%9F%87%B9_Portugu%C3%AAs-006600?style=flat-square)](docs/readmes/README.pt.md) [![Deutsch](https://img.shields.io/badge/%F0%9F%87%A9%F0%9F%87%AA_Deutsch-000000?style=flat-square)](docs/readmes/README.de.md) [![Italiano](https://img.shields.io/badge/%F0%9F%87%AE%F0%9F%87%B9_Italiano-009246?style=flat-square)](docs/readmes/README.it.md) [![Русский](https://img.shields.io/badge/%F0%9F%87%B7%F0%9F%87%BA_%D0%A0%D1%83%D1%81%D1%81%D0%BA%D0%B8%D0%B9-d52b1e?style=flat-square)](docs/readmes/README.ru.md) [![Türkçe](https://img.shields.io/badge/%F0%9F%87%B9%F0%9F%87%B7_T%C3%BCrk%C3%A7e-e30a17?style=flat-square)](docs/readmes/README.tr.md) [![العربية](https://img.shields.io/badge/%F0%9F%87%B8%F0%9F%87%A6_%D8%A7%D9%84%D8%B9%D8%B1%D8%A8%D9%8A%D8%A9-006c35?style=flat-square)](docs/readmes/README.ar.md) [![中文](https://img.shields.io/badge/%F0%9F%87%A8%F0%9F%87%B3_%E4%B8%AD%E6%96%87-de2910?style=flat-square)](docs/readmes/README.zh.md)

# FL Daily Edit

[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Update SP Football Life 2026 and eFootball PES 2021 `EDIT00000000` saves with
verified real-world transfers and reviewed player updates.

> **Beta:** Releases and save compatibility are still being tested.
>
> **New-player creation is disabled for now.** Transfers and reviewed updates
> for players already in the save are supported. Missing or ambiguous players
> are skipped. Full destination squads release a role-safe reserve by default;
> use `--no-allow-overflow-release` to leave full squads unchanged.

## Compatibility

The bundled [base save](base/EDIT00000000) requires:

- **SP Football Life 2026 Update 2.2**
- **SmokePatch's National Squads Update**

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

## What it updates

- Transfers, releases, loans, and loan returns
- Shirt numbers, lineups, and game plans affected by roster changes
- Transfer reports and audit logs
- Daily prebuilt saves through GitHub Actions

It checks the player's current club and never overwrites a shirt number already
used by another squad member.

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

# Validate Player Updates
python run.py players validate

# Apply reviewed Player Updates
python run.py players apply \
  --base-revision fl26-u2.2-national-squads \
  --edit-file /path/to/EDIT00000000 \
  --in-place

# Show command options
python run.py run --help
```

`run` applies transfers only. `players apply` is separate. To combine both,
run the transfer command first, then apply Player Updates to the same save.
Use `python run.py <command> --help` for audit, comparison, logging, and repair
tools.

## Player Updates

Reviewed updates live as one JSON file per player in `players/`. Existing-player
`update` records can be applied. New-player `create` records are review-only and
are currently rejected by `players apply` with
`create_temporarily_unavailable`.

To propose an update:

1. Open the [player update issue form](.github/ISSUE_TEMPLATE/player-update.yml).
2. Enter the player's name exactly as shown on the Pes Retro Stats profile and
   include proof URLs.
3. Review the generated draft, run `python run.py players validate`, and submit
   one player JSON file.

## Safety

- Saves are validated before and after changes.
- Local runs create rolling backups and use atomic, verified encryption.
- A process lock prevents concurrent writes to the same output.
- Incomplete source data aborts the run; ambiguous matches are skipped.
- FotMob is the primary source. Other sources only supplement or confirm it.

## Development

```bash
pytest -v
```

## License

FL Daily Edit is available under the [MIT License](LICENSE).
