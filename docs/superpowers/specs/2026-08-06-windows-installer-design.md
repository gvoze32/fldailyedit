# Windows Installer/Downloader Design

## Goal

Provide a beginner-friendly, portable Windows `.exe` that downloads and safely installs the latest compatible FL Daily Edit option file without requiring a GitHub login. Users explicitly choose **Fast** or **Deep**, confirm a detected SP Football Life 2026 or eFootball PES 2021 save folder, and receive a verified backup before replacement.

## Confirmed decisions

- Implementation: Python standard library plus Tkinter, packaged as a one-file windowed executable with PyInstaller.
- Download access: public GitHub Release assets; no GitHub account, token, or browser authentication.
- Target handling: detect SP Football Life 2026 and vanilla eFootball PES 2021 save folders, but enable installation only when the selected release record explicitly declares compatibility with that target.
- UI language for the first release: English only.
- Installation scope: one selected save folder per run.
- Privileges: no administrator elevation.
- Signing: the initial executable is unsigned because no code-signing certificate is available. Publish a SHA-256 digest and document the possible Windows SmartScreen warning.

## Non-goals

- Editing or generating option files on the user's computer.
- Installing game executables, patches, sider content, or files outside the selected save directory.
- GitHub authentication or private-repository support.
- User-initiated rollback after a successfully completed install.
- Installing to multiple save folders in one transaction.
- Publishing vanilla PES 2021 or UML saves until each separate validated base exists. The installer must fail closed for detected targets that have no compatible release record.
- Background services, auto-start, analytics, or telemetry.

## Distribution architecture

The existing Fast and Deep GitHub Actions workflows continue to generate option files. After all mutations, each workflow must run the final save validator. Only a successful final validation may publish or replace a public asset.

A rolling GitHub Release with stable tag `latest` exposes:

- `fldailyedit-fl2026-fast.zip`
- `fldailyedit-fl2026-deep.zip`
- `catalog.json`
- `FLDailyEditInstaller.exe`
- `FLDailyEditInstaller.exe.sha256`

Fast, Deep, and installer release writers use the same GitHub Actions concurrency group with cancellation disabled. This serializes release updates and prevents one workflow from clobbering another workflow's assets or catalog changes.

`catalog.json` is the machine-readable source for target and channel metadata. It may omit targets or channels that do not yet have a validated release. Each available record contains:

- schema version
- target identifier and display name
- channel identifier (`fast` or `deep`)
- generated UTC timestamp
- archive asset name and public download URL
- archive byte size and SHA-256
- inner `EDIT00000000` byte size and SHA-256

The publisher validates the existing catalog before updating one target/channel record. The first successful publisher may initialize an empty catalog, but later updates must preserve every unrelated valid record. A malformed catalog fails publishing rather than silently dropping another target or channel. The desktop client only accepts HTTPS release-download URLs belonging to the configured public repository and the exact asset name declared for the selected target/channel.

Existing transfer reports may remain short-lived Actions artifacts. They are not required by the installer.

## Application architecture

The installer package has four boundaries:

- `installer/catalog.py`: fetch, parse, and validate the catalog; select Fast or Deep metadata; download the selected archive with byte limits and progress callbacks.
- `installer/paths.py`: enumerate candidate FL 2026 and PES 2021 save directories under normal Documents and OneDrive Documents locations; validate a manually browsed destination.
- `installer/install.py`: validate the archive, verify hashes, create and verify a backup, stage the new save on the destination filesystem, and atomically replace the target.
- `installer/app.py`: Tkinter wizard and controller. Network and filesystem work runs outside the Tk event thread. Widgets do not contain download or installation policy.

A small entry point supports the normal windowed launch, `--version`, and a non-interactive `--self-test` used by Windows CI.

## User flow

The application is a single-window wizard with a visible four-step indicator.

### 1. Choose update

Two explicit choices are shown:

- **Fast — Recommended**: “Standard daily update from the live transfer feed.”
- **Deep — Expanded coverage**: “Checks every locally indexed FotMob club for maximum coverage.”

Supporting text states that Fast and Deep describe the generated save's coverage, not executable download speed. Each choice shows its compatible target and latest generation timestamp. An unavailable, stale, or target-incompatible channel is disabled with an explanation rather than silently falling back to another channel or game target.

### 2. Choose game save

The installer searches normal and OneDrive Documents roots for `Documents\KONAMI\eFootball PES 2021 SEASON UPDATE\2026\save` (FL 2026) and `Documents\KONAMI\eFootball PES 2021 SEASON UPDATE\<user_id>\save` (vanilla PES 2021). Every valid result is listed with its game label and full target path. When multiple results exist, none is silently preferred. A detected vanilla PES 2021 folder remains visible but cannot proceed until `catalog.json` contains a compatible validated PES 2021 record.

A **Browse…** action supports non-standard locations. The screen always previews the resulting target as `...\save\EDIT00000000`. The destination directory must already exist and be writable. The installer does not create arbitrary game-profile directory trees.

### 3. Review

The review screen displays:

- selected Fast or Deep channel
- channel generation timestamp
- detected game label or manual-folder label
- destination file
- whether an existing save will be backed up

Users are told to close the game. The primary action is **Download and install**.

### 4. Result

The progress view reports download, verification, backup, and installation stages without blocking the UI. Closing or canceling before the replacement stage leaves the destination unchanged. Cancellation is disabled while atomic replacement and post-replacement verification are in progress.

Success shows the installed path and, when applicable, the backup path, plus **Open save folder**. Failure preserves the selection and destination, identifies the failed stage, and offers retry or navigation back to the relevant step. A **Copy diagnostic details** action provides the technical error and selected path without secrets.

## Safety and transaction contract

1. Validate that the selected destination exists and is writable.
2. Download to a temporary file, never over the current save.
3. Enforce compressed and extracted byte limits.
4. Reject invalid ZIP structures, path traversal, links, directories, extra members, and any member not named exactly `EDIT00000000`.
5. Verify archive byte size and SHA-256 against the catalog.
6. Verify the extracted save byte size and SHA-256 against the catalog.
7. If the target exists, copy it to `save\FLDailyEditBackups\EDIT00000000.<UTC timestamp>.bak`.
8. Flush the backup and verify its size and SHA-256 against the original.
9. Stage the verified new save in the selected destination directory so replacement stays on one filesystem.
10. Flush the staged file and atomically replace `EDIT00000000`.
11. Verify the installed file size and SHA-256.
12. If post-replacement verification fails, atomically restore the verified backup. If no original save existed, remove the invalid newly installed target. Report a recovery failure explicitly if either cleanup action cannot complete.

A failure before replacement leaves the original file untouched. Automatic rollback belongs only to a transaction that has not completed successfully; the application does not offer later user-initiated rollback. A Windows sharing violation or permission error produces a specific “Close the game and retry” or permission message. The installer never deletes a valid backup during the transaction.

No credentials, analytics, or telemetry are collected. Logs may contain the chosen local path and technical exception details, but no token or unrelated user data.

## Error model

The controller maps typed failures to actionable UI messages:

- network unavailable or timeout
- release/catalog unavailable
- unsupported catalog schema
- unavailable Fast or Deep channel
- untrusted asset URL or name
- archive too large or malformed
- archive/save checksum mismatch
- destination missing or not writable
- insufficient free space
- current save locked by the game
- backup creation or verification failure
- atomic replacement or final verification failure

Unexpected exceptions use a generic failure heading while retaining diagnostic details. No failure is converted into success or an automatic channel fallback.

## Build and publishing

A Windows workflow runs on relevant installer-source changes and manual dispatch. It:

1. checks out the repository;
2. installs the supported Python version and project dependencies;
3. runs installer-focused tests;
4. builds a one-file windowed executable with PyInstaller;
5. executes the packaged `--self-test` path and checks its exit status;
6. computes `FLDailyEditInstaller.exe.sha256`;
7. updates the rolling release only after all checks pass.

The build and both save publishers share the release concurrency group. Release commands use the workflow-scoped `GITHUB_TOKEN` with minimal `contents: write` permission. The desktop application never receives that token.

## Verification

Core tests use a local HTTP server and temporary directories; they do not depend on live GitHub availability. Observable contracts cover:

- parsing valid Fast and Deep metadata
- rejecting malformed catalogs, schema changes, untrusted URLs, and unavailable channels
- interrupted, oversized, corrupt, and checksum-mismatched downloads
- rejecting ZIP traversal, links, directories, extras, and incorrect filenames
- normal Documents and OneDrive save discovery
- multiple-candidate behavior and manual destination validation
- existing-save backup and verification
- installation when no existing save exists
- unwritable, insufficient-space, and locked destinations
- atomic replacement and post-install verification
- preservation of the current save on every pre-replacement failure
- controller navigation, retry, progress, success, and error states without rendering Tk widgets

Windows CI smoke-runs the packaged executable's `--self-test`. The existing project suite remains green. The publishing workflows validate generated saves immediately before public release.

## Documentation

The main and localized READMEs replace the installer roadmap item with a beginner download section linking directly to `FLDailyEditInstaller.exe`. Instructions cover Fast/Deep selection, detected-folder confirmation, mandatory backups, the unsigned SmartScreen warning, and manual ZIP installation as a fallback.
