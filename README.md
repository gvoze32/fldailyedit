# FL Daily Edit

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Update SP Football Life 2026 and eFootball PES 2021 `EDIT00000000` saves with
verified real-world transfers, squad numbers, captain roles, tactics, and
formations.

## Project status

FL Daily Edit is back as a local app: it updates your own save on your own
PC. Daily prebuilt saves are no longer published, and the GitHub workflows that
built them are disabled.

My Evoweb account was banned for "stealing other people's work and claiming
it as your own." I dispute this. FL Daily Edit was written from scratch, and
its full development history, from the
[first commit](https://github.com/gvoze32/fldailyedit/commit/07dfac340f307eb881f6d680288b99eeb8ea75ba)
on 2026-08-02 onward, is public in the
[commit log](https://github.com/gvoze32/fldailyedit/commits). Third-party
components keep their original licenses and attribution, see
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

This may be both my first and last PES mod. Thanks to everyone who used the
project, reported issues, and helped improve it.

## Quick start (Windows)

1. Download
   [FLDailyEditInstaller.zip](https://github.com/gvoze32/fldailyedit/releases/download/latest/FLDailyEditInstaller.zip)
   from the [latest release](https://github.com/gvoze32/fldailyedit/releases/tag/latest)
   and extract it. It holds one file, the app itself: a single executable with
   nothing to install and no runtime to download. You can rename it to
   `FLDailyEdit.exe`.
2. Close the game and open the app. It looks for your save in your Documents
   folder (`KONAMI\eFootball PES 2021 SEASON UPDATE\2026\save\EDIT00000000`
   for Football Life 2026, and the numbered folders next to it for PES 2021)
   and remembers your last choices. If it finds none, or several, pick the
   `EDIT00000000` with **Browse**.
3. Press **Update save**. The app validates the save, fetches the current
   transfer data, shows its progress, backs up your save, writes the update,
   and then lists what changed and what could not be applied.

There is one update mode with full coverage: the live transfer feed plus
current squad membership, shirt numbers, captain roles, tactics, and
formations for every save club that can be matched to a FotMob club. There is
no Fast or Deep choice.

**Preview changes** runs the same plan and shows the result without writing
anything (no backup, no log entry, no report files). Use **Update now** from
the preview to apply it.

**Game folder:** a vanilla PES 2021 or T99 patch save carries no player
database of its own, so the app needs the game folder (the install that holds
`download\*.cpk`, or a directory with the patch CPKs) and asks for it. Football
Life 2026 saves do not need it.

**SmartScreen:** the executable is unsigned, so Windows SmartScreen may warn
before the first run (**More info**, then **Run anyway**). To check your
download, compare the SHA-256 of `FLDailyEditInstaller.zip` with the published
`FLDailyEditInstaller.zip.sha256` on the latest release.

### App updates

The app checks for a newer version when it starts, and the **Check for
updates** button checks on demand. **Update and restart** downloads
`FLDailyEditInstaller.zip`, verifies its size and SHA-256, checks that the
archive holds exactly the one expected executable, replaces the running
executable, and restarts. An update is accepted only when
`installer-update.json` carries a valid Ed25519 signature
(`installer-update.json.sig`) from the project release key embedded in the app.

## Compatibility

The bundled [base save](base/EDIT00000000) requires:

- **SP Football Life 2026 Update 2.2**
- **SmokePatch's National Teams Selection Update**

The base EDIT file is from Gondowan's [Road to FL27 MiniPatch](https://www.reddit.com/r/SPFootballLife/comments/1wssmur/release_minipatch_road_to_fl27_for_football_life/).
It updates transfers, overalls, selected lineups, and national-team call-ups.
The base is incompatible with UML, other patches that modify the database,
older FL26 versions, and installations without the National Teams update.

Start a new Master League or Become a Legend career after using it.

### PES 2021 / T99 patch saves

Vanilla PES 2021 and T99 patch `EDIT00000000` files are supported when the
matching native database CPKs are available. Point the game folder (GUI) or
`--game-root` (CLI) at the PES installation (with `download`) or at a directory
containing the patch CPKs:

```bash
FLDailyEdit run \
  --edit-file "/path/to/T99/EDIT00000000" \
  --game-root "/path/to/T99 Patch V10" \
  --dry-run
```

Use the save and CPKs from the same patch generation. The updater reads the
native `Player.bin`, `Team.bin`, and `PlayerAssignment.bin` from the CPKs and
never uses the bundled FL26 player catalog. A missing or mismatched native
`Player.bin` is rejected before any save mutation.

## Command line

The same executable is the command line: with no arguments it opens the app,
with arguments it runs a command. `FLDailyEdit --edit-file PATH` is shorthand
for `FLDailyEdit run --edit-file PATH`. (Built from source the binary is named
`fldailyedit`; the examples use the release name.)

```text
FLDailyEdit run       Apply verified transfers, squad numbers, captains and tactics (default)
FLDailyEdit validate  Check that a save matches the supported edit-file layout (exit 2 on FAIL)
FLDailyEdit inspect   Show what a save contains
FLDailyEdit log       Show the most recent save changes from the transfer log
```

`run` options:

| Option                 | Meaning                                                                                      |
| ---------------------- | -------------------------------------------------------------------------------------------- |
| `--edit-file <PATH>`   | Save to update (default: the only save found in your Documents folder)                       |
| `-o, --output <PATH>`  | Write the updated save here instead of updating the input save in place                      |
| `--game-root <DIR>`    | Game folder (PES 2021 / T99 install containing `download/*.cpk`); required for PES 2021 saves |
| `--window <WINDOW>`    | `auto` (continue from the save's last applied update), `all`, `summer`, `winter`              |
| `--since <DATE>`       | Only transfers on or after this date (`YYYY-MM-DD`); conflicts with `--window`               |
| `--club <CLUB>`        | Focus on one club (name or FotMob id)                                                        |
| `--threshold <PERCENT>`| Minimum player-name match confidence, 0-100 (default 80; 0 accepts any match)                |
| `--no-overflow-release`| Keep full rosters unchanged instead of releasing a squad player                              |
| `--release-policy <FILE>` | JSON protected-player / usage policy for overflow releases                                |
| `--dry-run`            | Plan and report without writing anything                                                     |

`validate` and `inspect` take `--edit-file <PATH>` (`validate -v, --verbose`
lists every team of a repeated warning instead of the first ten); `log` takes `-n, --last <N>`
(default 20). Exit codes: `0` success, `1` missing or unusable input save,
`2` failed validation or failed update, `130` cancelled. `--help` on any
command lists the details.

```bash
# Preview current-cycle transfers for the save the app found
FLDailyEdit run --dry-run

# Apply them (default window: continue from this save's last applied update)
FLDailyEdit run

# Replay all available transfer history into a copy
FLDailyEdit run --edit-file base/EDIT00000000 --window all --output /tmp/EDIT00000000

# Validate / inspect a save, show the last 50 applied changes
FLDailyEdit validate --edit-file /path/to/EDIT00000000
FLDailyEdit inspect --edit-file /path/to/EDIT00000000
FLDailyEdit log -n 50
```

Each run loads and validates the save before scraping. With `--window auto`,
when a run continues a save it applied before (per its entries in the transfer
log), the transfer window starts 7 days before that save's last applied entry,
extended back to the oldest still-retryable transfer that was not applied.
Saves without a log use the previous window start (Jan 1 / Jun 1), and the
automatic window never starts earlier than that.

## What it updates

- Verified transfers, releases, loans, and loan returns
- Evidence-driven roster reconciliation for indexed clubs: shirt numbers,
  affected lineups, and game-plan repairs
- Current captain roles
- Evidence-gated tactical choices in the main game-plan preset
- FotMob-derived formations and role/coordinate layouts in the main game-plan preset
- Transfer reports and an audit log of every applied change

Roster changes come from verified transfer events or complete FotMob squad
snapshots matched to existing local identities. Short provider aliases require
matching shirt, age, and compatible-role evidence; ambiguous or low-coverage
evidence does not force player-specific corrections.

Club identity is learned per selected save, not from curated club lists: save
club names are matched against the FotMob team index embedded in the app and
confirmed by FotMob squad overlap. Learned bindings are cached in the state
folder.

### Tactics

Tactical settings are generated from FotMob current-season league aggregates:
possession share, accurate long-ball/pass rate, accurate-cross/pass rate, and
possessions won in the attacking third. These are league-relative proxies, not
tracking or PPDA measurements. For a team with at least three matches and
evidence from at least eight league teams, categorical choices change only at
the top or bottom quartile. Containment area uses cross/pass rate as a width
proxy (low = Middle, high = Wide); pressuring uses attacking-third recoveries
(low = Conservative, high = Aggressive). Defensive Line scales those recoveries
across the league to the game's 1-10 range; it is not an independent spatial
measurement. Missing or uniform evidence preserves the existing value. The
updater can change seven controls in the main preset: attacking style,
build-up, attacking area, defensive style, containment area, pressuring, and
defensive line. Compactness is left as saved, because no parsed FotMob metric
measures it independently.

### Formations and game plans

Formations, starting XI, bench, and detailed positions come from FotMob's last
lineup (starters and substitutes), otherwise the latest finished match page
with an unambiguous team-name match. The game plan is resolved after the run's
roster changes. Valid 10-player shapes are converted into PES role and
coordinate layouts for all three phases of the main preset; the two alternate
presets are untouched. Empty captain and set-piece roles are filled from save
data. Formations are applied to every matched club with a complete squad
snapshot. Missing or unsupported formation evidence preserves the existing
bytes.

The updater checks each player's current club and never overwrites an occupied
shirt number. Clean PES21 saves may retain numbers in empty roster slots; these
are reported as non-blocking warnings.

### Transfer sources

FotMob is the primary source for current transfer events. Transfermarkt and
Wikipedia provide dated routes and details, Sortitoutsi, BeSoccer, Sofascore,
and Soccerway corroborate them. For same-day route conflicts, precedence is
FotMob > Transfermarkt > Wikipedia. Corroboration sources never override a
primary route or create transfer events.

Sofascore and Soccerway scan only relevant primary-source clubs. Soccerway reads
the first transfer page per club and has a 60-second source budget.
Transfermarkt scans continue back to the run's since-date, so events on later
pages are not dropped by a time budget. Every save club bound to a FotMob club
is refreshed.

A failed primary-source (FotMob) fetch aborts the run before anything is
written. Failures of the other sources are reported as warnings and do not
block a run; incomplete or ambiguous events are skipped.

## Reports, logs and backups

Everything the app keeps lives in one per-user state folder:

| OS      | State folder                                                        |
| ------- | ------------------------------------------------------------------- |
| Windows | `%LOCALAPPDATA%\FLDailyEdit`                                        |
| macOS   | `~/Library/Application Support/FLDailyEdit`                         |
| Linux   | `$XDG_DATA_HOME/FLDailyEdit` (default `~/.local/share/FLDailyEdit`) |

| Path (inside the state folder)                    | Content                                                                          |
| ------------------------------------------------- | -------------------------------------------------------------------------------- |
| `transfer_log.jsonl`                              | Append-only audit log of applied transfer, roster, and captain changes (all saves) |
| `reports/<save-id>/transfer_summary.md` and `.html` | Report of the latest run for that save                                          |
| `reports/<save-id>/skipped_transfers.jsonl`       | Transfers that were not applied, with a reason code                              |
| `backups/<save-id>/EDIT00000000.bak.<timestamp>`  | Backup taken right before a save is overwritten; the newest 10 per save are kept |
| `club_identity_cache.json`, `storage/`            | Learned club bindings and cached FotMob payloads                                 |
| `settings.json`                                   | Your last save and game-folder choices                                           |

`<save-id>` is a short hash of the save's resolved path, so different saves
never share reports or evict each other's backups. The `log` command reads
`transfer_log.jsonl`.

Transfers that could not be applied are never forced into the save: players
missing from the save are reported, never created. Each run lists them, with a
reason code, in a **Not applied** report section and in
`skipped_transfers.jsonl`, with transfers touching save clubs listed first. The
CLI prints the full list grouped by reason, and the app shows the same list
after an update. `--dry-run` (and **Preview changes**) also lists the per-team
XI, bench, and captain changes a run would make.

## Safety

- Saves are validated before and after changes; a failed check restores the
  validated input.
- Updates create a rolling backup, and the save is replaced atomically after
  the encrypted result is verified by decrypting it again.
- A per-save process lock prevents concurrent writes to the same save.
- Preview and `--dry-run` write nothing: no backup, log, report, or lock.
- The app refuses to read or write a save that sits inside the game installation.
- Incomplete or ambiguous data is skipped rather than forced into the save.
- Roster compaction preserves tactical game-plan positions, updates lineup slots
  and goalkeeper placement, and repairs role zero from known goalkeeper metadata.
  Validation rejects non-empty plans without exactly one goalkeeper marker per
  tactical phase.

## Build from source

Rust (stable, edition 2024) is required.

```bash
git clone https://github.com/gvoze32/fldailyedit.git
cd fldailyedit
cargo build --release -p fl-app
# target/release/fldailyedit (the app and the command line in one executable)
```

Windows executable from Linux or macOS with mingw-w64 (it provides the linker
and `windres`, which `crates/fl-app/build.rs` uses to embed the icon, version
information, and manifest):

```bash
rustup target add x86_64-pc-windows-gnu
# macOS: brew install mingw-w64    Debian/Ubuntu: apt install gcc-mingw-w64-x86-64
cargo build --release -p fl-app --target x86_64-pc-windows-gnu
# target/x86_64-pc-windows-gnu/release/fldailyedit.exe
```

The graphical app targets Windows; the engine and command line also build and
test on macOS and Linux. Release builds on Windows use the GUI subsystem and
attach to the parent console when started with arguments.

Release publishing (signed update manifest, `FLDailyEditInstaller.zip`) is the
manual `Build Windows Installer` workflow and needs an `INSTALLER_SIGNING_KEY`
secret (PEM PKCS#8 Ed25519 private key matching the public key embedded in
`crates/fl-update`). The signing tool is
`cargo run -p fl-update --bin sign-manifest -- build|sign`.

## Development

Workspace crates: `fl-save` (save file, crypto, tables), `fl-text` (name and
club matching text), `fl-sources` (data providers), `fl-engine` (matching,
planning, update pipeline), `fl-update` (signed self-update), `fl-app` (app and
command line).

```bash
cargo test --workspace
cargo fmt --all --check
cargo clippy --workspace --all-targets -- -D warnings
```

Parity and end-to-end golden fixtures live under `testdata/` (see
[testdata/e2e/README.md](testdata/e2e/README.md) and
[testdata/DEVIATIONS.md](testdata/DEVIATIONS.md)). Tests that need large local
game data (`testdata/local/`, `reference/`) skip when it is absent. The
`fl-app` screen snapshot test renders through a GPU adapter and compares PNGs
in `crates/fl-app/tests/snapshots`.

## License

FL Daily Edit is available under the [MIT License](LICENSE). Bundled and
ported third-party components are listed in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
