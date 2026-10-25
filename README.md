# FL Daily Edit

A simple Windows app that brings your **Football Life** or **PES 2021**
save up to date with real-world football.

What it updates:

- **Transfers:** signings, releases, loans and loan returns.
- **Squads:** shirt numbers and captains.
- **Tactics and formations** based on how each club really plays.

The app updates itself when a new version is available.

## Project status

FL Daily Edit is actively maintained. It is a local app: it updates your own
save on your own PC. Bug reports and suggestions are welcome in
[Issues](https://github.com/gvoze32/fldailyedit/issues). Daily prebuilt saves
are no longer published.

My Evoweb account was banned for "stealing other people's work and claiming it
as your own." I dispute this. FL Daily Edit was written from scratch, and its
full development history, from the
[first commit](https://github.com/gvoze32/fldailyedit/commit/07dfac340f307eb881f6d680288b99eeb8ea75ba)
on 2026-08-02 onward, is public in the
[commit log](https://github.com/gvoze32/fldailyedit/commits). Third-party
components keep their original licenses and attribution, see
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

A note for anyone quick to cry "copy": two tools that edit the same
`EDIT00000000` format will look alike, the same way every car has four wheels.
Similar goals are not stolen code. Before calling something a copy, open it and
point to the lines, it takes less time than writing the report.

## Download

Download
[FLDailyEdit.exe](https://github.com/gvoze32/fldailyedit/releases/latest/download/FLDailyEdit.exe)
from the [Releases](https://github.com/gvoze32/fldailyedit/releases/latest)
page. Nothing to install: just open it.

Windows may show "Windows protected your PC" the first time. Click
**More info**, then **Run anyway**.

## How to use

1. **Close the game.**
2. Open `FLDailyEdit.exe`. It finds your save by itself. Saves are in
   `Documents\KONAMI\eFootball PES 2021 SEASON UPDATE\<folder>\save\` and are
   named `EDIT00000000`. If the app can't find yours, click **Browse…**.
3. Choose your **game folder**: the folder where the game is installed, the
   one that contains the `download` folder. The app often finds it by itself.
   It is required for every save: the app reads the player and club lists from
   your game.
4. Click **Update save** and wait a few minutes. A copy of your old save is
   made first, just in case.
5. The app shows what changed and which transfers could not be applied.
6. Start a **new** Master League or Become a Legend career to use the update.

Want to see the changes first? Click **Preview changes**. Nothing is saved.

Run it again whenever you like, for example once a week. Each run picks up
where the last one stopped.

## Good to know

- **Supported:** Football Life and PES 2021. Saves from patches that change
  the database a lot (such as UML) may not work.
- **The game folder is required for every save.** The app reads the player and
  club lists from your own game installation; it does not ship any. Choose the
  game that matches your save, or the update stops before anything is written.
- **New players are never created.** If a player isn't in your save, his
  transfer is skipped and counted under **Not applied**.
- **The result screen shows a summary.** Every change and every skipped
  transfer is listed in the full report: click **Open report**.
- **Your save is checked before and after every update.** If anything looks
  wrong, nothing is written.
- **Backups** of your old saves, reports and logs are kept in
  `%LOCALAPPDATA%\FLDailyEdit`.
- Transfer data comes from FotMob, checked against Transfermarkt, Wikipedia
  and other football sites. The first update also downloads FotMob's club list
  (a few seconds) and keeps it for 30 days, so it needs an
  internet connection.

## For developers

The app is written in Rust. The same executable also works from the command
line (`FLDailyEdit run --help`).

```bash
cargo build --release -p fl-app                                   # this OS
cargo build --release -p fl-app --target x86_64-pc-windows-gnu    # Windows exe (needs mingw-w64)
cargo test --workspace
```

Tests that need local data (fixtures, recorded runs, a save or game files) skip
when it is absent; the repository ships none, so a fresh clone runs only the
self-contained tests.

To release, bump `version` in `Cargo.toml` and push to `main`. GitHub Actions
tests, builds the Windows exe and publishes `vX.Y.Z`.

## Credits

Credits and third-party licences:
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). FL Daily Edit is MIT
licensed ([LICENSE](LICENSE)).
