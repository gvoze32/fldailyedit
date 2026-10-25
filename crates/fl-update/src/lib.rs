//! Signed self-update for FL Daily Edit.
//!
//! Flow (all APIs are blocking; call them from a background thread, they never print):
//!
//! 1. [`check_for_update`] fetches `installer-update.json` + `.sig`, verifies the
//!    Ed25519 signature, parses the manifest strictly and compares versions.
//! 2. [`download_and_stage`] streams `FLDailyEditInstaller.zip` into a private temp
//!    directory, verifies size and SHA-256, validates the zip layout and extracts the
//!    executable.
//! 3. [`replace_current_exe`] swaps the running exe (rename-self), [`restart`] launches
//!    the new one; the caller then exits. [`cleanup_after_update`] runs at next launch.
//!
//! # Compatibility with 0.2.x installs
//!
//! Release assets are unchanged so already-installed 0.2.x builds can upgrade:
//! `FLDailyEditInstaller.zip` holds exactly one entry named
//! `FLDailyEditInstaller.exe` (for the current app, the GUI exe),
//! `installer-update.json` has exactly the keys `schema_version` (1), `version`,
//! `asset_name`, `download_url`, `archive_size`, `archive_sha256`, and
//! `installer-update.json.sig` is the base64 Ed25519 signature of the raw manifest
//! bytes. The 0.2.x swap script moves the staged exe over the *current* exe path and
//! refuses unless the current file name is `FLDailyEditInstaller.exe`; 0.2.x installs
//! therefore keep that file name, while fresh installs may be named
//! `FLDailyEdit.exe`. [`replace_current_exe`] replaces the running exe whatever it is
//! called. The 0.2.x limits apply: archive ≤ 128 MiB, manifest ≤ 64 KiB,
//! signature ≤ 1 KiB.
//!
//! The [`release`] module and the `sign-manifest` binary build and sign the release
//! manifest.

mod archive;
mod error;
mod fetch;
pub mod manifest;
pub mod release;
mod replace;
mod update;

pub use error::{Error, ErrorKind, Result};
pub use fetch::{DOWNLOAD_TIMEOUT, Fetch, HttpFetch, MANIFEST_TIMEOUT, Response, is_trusted_url};
pub use manifest::{Manifest, Version, verify_signature, verify_with_key};
pub use replace::{Replaced, cleanup_after_update, replace_current_exe, restart};
pub use update::{
    Available, STAGING_PREFIX, Staged, check_for_update, check_for_update_with_key, cleanup_staged,
    download_and_stage,
};
