//! Signed self-update for FL Daily Edit.
//!
//! Flow (all APIs are blocking; call them from a background thread, they never print):
//!
//! 1. [`check_for_update`] fetches `update.json` + `update.json.sig` from the newest
//!    GitHub release (`releases/latest/download/…` redirects to the newest `vX.Y.Z`),
//!    verifies the Ed25519 signature, parses the manifest strictly and compares
//!    versions.
//! 2. [`download_and_stage`] streams `FLDailyEdit.exe` from the manifest's
//!    `download_url` into a private temp directory and verifies size and SHA-256.
//! 3. [`replace_current_exe`] swaps the running exe (rename-self), [`restart`] launches
//!    the new one; the caller then exits. [`cleanup_after_update`] runs at next launch.
//!
//! # Releases
//!
//! Every release `vX.Y.Z` carries four assets: `FLDailyEdit.exe` (what users download),
//! `FLDailyEdit.exe.sha256`, `update.json` and `update.json.sig`. `update.json` has
//! exactly the keys `schema_version` (1), `version`, `download_url`, `size` and
//! `sha256`; `download_url` must be exactly
//! `https://github.com/gvoze32/fldailyedit/releases/download/v<version>/FLDailyEdit.exe`
//! (pinned to the release tag, so the signed manifest and the executable always
//! match) and `update.json.sig` is the base64 Ed25519 signature of the raw manifest
//! bytes. Limits: executable ≤ 128 MiB, manifest ≤ 64 KiB, signature ≤ 1 KiB.
//! [`replace_current_exe`] replaces the running exe whatever it is called.
//!
//! The [`release`] module and the `sign-manifest` binary build and sign the release
//! manifest.

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
