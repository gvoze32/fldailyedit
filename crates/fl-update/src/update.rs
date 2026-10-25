//! Check for, download and stage an update.

use std::fs::{self, File};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};

use sha2::{Digest, Sha256};

use crate::archive::extract_executable;
use crate::error::{Error, Result};
use crate::fetch::{DOWNLOAD_TIMEOUT, Fetch, MANIFEST_TIMEOUT, fetch_document};
use crate::manifest::{
    APP_EXECUTABLE_ENTRY, APP_UPDATE_MANIFEST_URL, APP_UPDATE_PUBLIC_KEY, APP_UPDATE_SIGNATURE_URL,
    MAX_APP_ARCHIVE_BYTES, MAX_MANIFEST_BYTES, MAX_SIGNATURE_BYTES, Manifest, Version,
    verify_with_key,
};

/// Prefix of every staging directory created in the OS temp directory.
pub const STAGING_PREFIX: &str = "fldailyedit-app-update-";

const DOWNLOAD_CHUNK_BYTES: usize = 64 * 1024;

/// A verified newer release. Only constructible from a validated [`Manifest`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Available {
    /// Version offered by the manifest.
    pub version: Version,
    /// Size of the installer zip in bytes.
    pub size: u64,
    manifest: Manifest,
}

impl Available {
    /// Wrap a manifest after re-validating it.
    pub fn new(manifest: Manifest) -> Result<Self> {
        manifest.validate()?;
        Ok(Self {
            version: manifest.version,
            size: manifest.archive_size,
            manifest,
        })
    }

    /// The signed manifest this update was derived from.
    pub fn manifest(&self) -> &Manifest {
        &self.manifest
    }
}

/// A fully verified executable extracted to a private staging directory.
#[derive(Debug, PartialEq, Eq)]
pub struct Staged {
    /// Path of the staged `FLDailyEditInstaller.exe` (whatever the running exe is named).
    pub executable: PathBuf,
    /// Version that was staged.
    pub version: Version,
}

impl Staged {
    /// Remove the staging directory (best effort).
    pub fn discard(self) {
        cleanup_staged(&self.executable);
    }
}

/// Fetch `installer-update.json` and its signature, verify, parse, and compare with
/// `current` (`MAJOR.MINOR.PATCH`). Returns `None` when the release is not newer.
pub fn check_for_update(current: &str, fetch: &dyn Fetch) -> Result<Option<Available>> {
    check_for_update_with_key(current, fetch, &APP_UPDATE_PUBLIC_KEY)
}

/// [`check_for_update`] verifying against an explicit raw Ed25519 public key instead
/// of the embedded production key (for tests and alternate release channels).
pub fn check_for_update_with_key(
    current: &str,
    fetch: &dyn Fetch,
    public_key: &[u8; 32],
) -> Result<Option<Available>> {
    let current: Version = current.parse()?;
    let payload = fetch_document(
        fetch,
        APP_UPDATE_MANIFEST_URL,
        MANIFEST_TIMEOUT,
        MAX_MANIFEST_BYTES,
    )?;
    let signature = fetch_document(
        fetch,
        APP_UPDATE_SIGNATURE_URL,
        MANIFEST_TIMEOUT,
        MAX_SIGNATURE_BYTES,
    )
    .map_err(|error| match error {
        Error::Http(404) => Error::MissingSignature,
        Error::TooLarge(_) => {
            Error::InvalidSignature("update manifest signature is malformed".into())
        }
        other => other,
    })?;
    verify_with_key(public_key, &payload, &signature)?;
    let manifest = Manifest::parse(&payload)?;
    if !manifest.version.is_newer(&current) {
        return Ok(None);
    }
    Available::new(manifest).map(Some)
}

/// Download the installer zip into a fresh `fldailyedit-app-update-*` temp directory
/// (streaming, size-capped, SHA-256 verified), validate that it contains exactly one
/// `FLDailyEditInstaller.exe`, extract it with fsync and delete the archive.
///
/// `progress(done, total)` is called with `(0, total)` first and after every chunk;
/// `cancel` is polled before the request and before every chunk. On any failure the
/// staging directory is removed.
pub fn download_and_stage(
    available: &Available,
    fetch: &dyn Fetch,
    progress: &mut dyn FnMut(u64, u64),
    cancel: &AtomicBool,
) -> Result<Staged> {
    let manifest = available.manifest();
    manifest.validate()?;
    let staging = tempfile::Builder::new()
        .prefix(STAGING_PREFIX)
        .tempdir()
        .map_err(|error| Error::io("cannot create the update staging directory", error))?;
    let archive_path = staging.path().join(&manifest.asset_name);
    let executable = staging.path().join(APP_EXECUTABLE_ENTRY);

    download_archive(manifest, fetch, &archive_path, progress, cancel)?;
    extract_executable(&archive_path, &executable)?;
    fs::remove_file(&archive_path)
        .map_err(|error| Error::io("cannot remove the downloaded archive", error))?;

    // Success: the caller now owns the directory (see `Staged::discard`).
    let _ = staging.keep();
    Ok(Staged {
        executable,
        version: manifest.version,
    })
}

fn download_archive(
    manifest: &Manifest,
    fetch: &dyn Fetch,
    destination: &Path,
    progress: &mut dyn FnMut(u64, u64),
    cancel: &AtomicBool,
) -> Result<()> {
    if cancel.load(Ordering::Relaxed) {
        return Err(Error::Cancelled);
    }
    let total = manifest.archive_size;
    let response = fetch.get(&manifest.download_url, DOWNLOAD_TIMEOUT)?;
    if let Some(length) = response.content_length {
        if length > MAX_APP_ARCHIVE_BYTES {
            return Err(Error::TooLarge(
                "installer archive exceeds the download limit".into(),
            ));
        }
        if length > total {
            return Err(Error::SizeMismatch(
                "installer archive exceeds its declared size".into(),
            ));
        }
    }

    let mut body = response.body;
    let mut output = File::create(destination)
        .map_err(|error| Error::io("cannot create the archive download file", error))?;
    let mut digest = Sha256::new();
    let mut buffer = vec![0_u8; DOWNLOAD_CHUNK_BYTES];
    let mut downloaded = 0_u64;
    progress(0, total);
    loop {
        if cancel.load(Ordering::Relaxed) {
            return Err(Error::Cancelled);
        }
        // Read at most one byte past the declared size so an overlong body is detected.
        let want = (total - downloaded + 1).min(DOWNLOAD_CHUNK_BYTES as u64) as usize;
        let read = match body.read(&mut buffer[..want]) {
            Ok(0) => break,
            Ok(read) => read,
            Err(error) if error.kind() == std::io::ErrorKind::Interrupted => continue,
            Err(error) => return Err(Error::from_body_read(error)),
        };
        let next = downloaded + read as u64;
        if next > MAX_APP_ARCHIVE_BYTES {
            return Err(Error::TooLarge(
                "installer archive exceeds the download limit".into(),
            ));
        }
        if next > total {
            return Err(Error::SizeMismatch(
                "installer archive exceeds its declared size".into(),
            ));
        }
        output
            .write_all(&buffer[..read])
            .map_err(|error| Error::io("cannot write the archive download file", error))?;
        digest.update(&buffer[..read]);
        downloaded = next;
        progress(downloaded, total);
    }
    if downloaded != total {
        return Err(Error::SizeMismatch(format!(
            "received {downloaded} of {total} bytes"
        )));
    }
    if format!("{:x}", digest.finalize()) != manifest.archive_sha256 {
        return Err(Error::ChecksumMismatch);
    }
    output
        .sync_all()
        .map_err(|error| Error::io("cannot flush the archive download file", error))
}

/// Delete the staging directory that holds `staged_executable`. Does nothing unless
/// the file is named `FLDailyEditInstaller.exe` inside a `fldailyedit-app-update-*`
/// directory; errors are ignored.
pub fn cleanup_staged(staged_executable: &Path) {
    let is_installer_exe = staged_executable
        .file_name()
        .is_some_and(|name| name == APP_EXECUTABLE_ENTRY);
    let Some(directory) = staged_executable.parent() else {
        return;
    };
    let named_like_staging = directory
        .file_name()
        .and_then(|name| name.to_str())
        .is_some_and(|name| name.starts_with(STAGING_PREFIX));
    if is_installer_exe && named_like_staging {
        let _ = fs::remove_dir_all(directory);
    }
}
