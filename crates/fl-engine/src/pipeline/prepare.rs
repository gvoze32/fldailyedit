//! Stage 1: lock → hash → decrypt (in memory) → load → header → validate.

use std::fs::{self, File};
use std::io::Read;
use std::path::{Component, Path, PathBuf};

use fl_save::crypto::{self, Blocks};
use fl_save::edit_file::{EditFile, Snapshot};
use sha2::{Digest, Sha256};

use super::Reporter;
use crate::api::{CancelToken, ErrorCode, Stage, UpdateError, UpdateRequest};
use crate::lock::{LockError, SaveLock};

/// Errors listed in a validation failure message before "... and N more".
pub(crate) const MAX_LISTED_ERRORS: usize = 20;

/// State of the output file when the run started, to detect concurrent writers.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct OutputState {
    pub(crate) existed: bool,
    pub(crate) digest: Option<String>,
}

/// A validated input save, ready for context loading and planning.
pub(crate) struct Prepared {
    pub(crate) edit_path: PathBuf,
    pub(crate) target: PathBuf,
    /// Resolved target path: partitions the transfer log and backups per save.
    pub(crate) scope: String,
    pub(crate) input_digest: String,
    pub(crate) same_input_output: bool,
    pub(crate) output: OutputState,
    /// Decrypted container; `data` is emptied while `edit` owns the bytes.
    pub(crate) blocks: Blocks,
    pub(crate) edit: EditFile,
    /// The validated input bytes, for rollback.
    pub(crate) baseline: Snapshot,
    /// Held until the run ends (never taken on dry runs).
    pub(crate) _lock: Option<SaveLock>,
}

pub(crate) fn fail(code: ErrorCode, stage: Stage, message: impl Into<String>) -> UpdateError {
    UpdateError {
        code,
        stage: Some(stage),
        message: message.into(),
    }
}

pub(crate) fn sha256_hex(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

/// Stream a file through SHA-256.
pub(crate) fn sha256_file(path: &Path) -> std::io::Result<String> {
    let mut file = File::open(path)?;
    let mut hasher = Sha256::new();
    let mut buffer = vec![0u8; 1 << 20];
    loop {
        let read = file.read(&mut buffer)?;
        if read == 0 {
            return Ok(format!("{:x}", hasher.finalize()));
        }
        hasher.update(&buffer[..read]);
    }
}

/// Absolute, symlink-resolved form of a path whose tail may not exist yet
/// (`.` and `..` are folded lexically).
pub(crate) fn resolve_path(path: &Path) -> PathBuf {
    let absolute = if path.is_absolute() {
        path.to_path_buf()
    } else {
        std::env::current_dir().map_or_else(|_| path.to_path_buf(), |dir| dir.join(path))
    };
    let mut folded = PathBuf::new();
    for component in absolute.components() {
        match component {
            Component::CurDir => {}
            Component::ParentDir => {
                folded.pop();
            }
            other => folded.push(other),
        }
    }
    // Resolve symlinks of the deepest part that exists, keep the rest as is.
    let mut existing = folded.clone();
    let mut tail: Vec<std::ffi::OsString> = Vec::new();
    while !existing.exists() {
        match existing.file_name() {
            Some(name) => {
                tail.push(name.to_owned());
                existing.pop();
            }
            None => break,
        }
    }
    let mut resolved = fs::canonicalize(&existing).unwrap_or(existing);
    resolved.extend(tail.iter().rev());
    strip_verbatim(resolved)
}

/// Windows `canonicalize` returns `\\?\C:\...`; users and logs expect `C:\...`.
fn strip_verbatim(path: PathBuf) -> PathBuf {
    let text = path.to_string_lossy();
    if let Some(rest) = text.strip_prefix(r"\\?\UNC\") {
        return PathBuf::from(format!(r"\\{rest}"));
    }
    match text.strip_prefix(r"\\?\") {
        Some(rest) => PathBuf::from(rest),
        None => path,
    }
}

pub(crate) fn prepare(
    request: &UpdateRequest,
    reporter: &mut Reporter<'_>,
    cancel: &CancelToken,
) -> Result<Prepared, UpdateError> {
    let stage = Stage::Validating;
    cancel.check(stage)?;
    if !request.edit_path.is_file() {
        return Err(fail(
            ErrorCode::SaveNotFound,
            stage,
            format!("Edit file not found: {}", request.edit_path.display()),
        ));
    }
    let target = request.target_path().to_path_buf();
    // A dry run never writes, so it neither needs the lock nor may leave a
    // lock file or directories behind.
    let lock = if request.dry_run {
        None
    } else {
        Some(SaveLock::acquire(&target).map_err(|error| {
            let code = match error {
                LockError::Held { .. } => ErrorCode::SaveLocked,
                LockError::Io { .. } => ErrorCode::WriteFailed,
            };
            fail(code, stage, error.to_string())
        })?)
    };

    reporter.say(stage, format!("Reading {}", request.edit_path.display()));
    let raw = fs::read(&request.edit_path).map_err(|error| {
        fail(
            ErrorCode::InvalidSave,
            stage,
            format!("Could not read {}: {error}", request.edit_path.display()),
        )
    })?;
    let input_digest = sha256_hex(&raw);
    let resolved_target = resolve_path(&target);
    let same_input_output = resolved_target == resolve_path(&request.edit_path);
    let output = if same_input_output {
        OutputState {
            existed: true,
            digest: Some(input_digest.clone()),
        }
    } else if target.is_file() {
        OutputState {
            existed: true,
            digest: Some(sha256_file(&target).map_err(|error| {
                fail(
                    ErrorCode::WriteFailed,
                    stage,
                    format!("Could not read {}: {error}", target.display()),
                )
            })?),
        }
    } else {
        OutputState {
            existed: false,
            digest: None,
        }
    };
    cancel.check(stage)?;

    reporter.say(stage, "Decrypting save");
    let mut blocks = crypto::decrypt(&raw).map_err(|error| {
        fail(
            ErrorCode::InvalidSave,
            stage,
            format!("Decryption failed: {error}"),
        )
    })?;
    drop(raw);
    let header = blocks.save_header();
    let mut edit = EditFile::from_bytes(std::mem::take(&mut blocks.data)).map_err(|error| {
        fail(
            ErrorCode::InvalidSave,
            stage,
            format!("Could not load the selected save: {error}"),
        )
    })?;
    edit.set_pes21(header.is_pes21());

    reporter.say(stage, "Validating save integrity");
    let report = edit.validate_integrity();
    if !report.is_valid() {
        let mut lines = vec![
            "Input save failed supported edit-file integrity validation; no changes were written."
                .to_owned(),
        ];
        lines.extend(
            report
                .errors
                .iter()
                .take(MAX_LISTED_ERRORS)
                .map(|error| format!("  - {error}")),
        );
        if report.errors.len() > MAX_LISTED_ERRORS {
            lines.push(format!(
                "  ... and {} more errors",
                report.errors.len() - MAX_LISTED_ERRORS
            ));
        }
        lines.push("Use a standard EDIT00000000 save with a supported layout.".to_owned());
        return Err(fail(ErrorCode::InvalidSave, stage, lines.join("\n")));
    }
    let baseline = edit.snapshot();
    Ok(Prepared {
        edit_path: request.edit_path.clone(),
        target,
        scope: resolved_target.to_string_lossy().into_owned(),
        input_digest,
        same_input_output,
        output,
        blocks,
        edit,
        baseline,
        _lock: lock,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn resolve_path_handles_missing_files_and_dirs() {
        let dir = tempfile::tempdir().unwrap();
        let canonical = fs::canonicalize(dir.path()).unwrap();
        let existing = resolve_path(&dir.path().join("a").join("..").join("."));
        assert_eq!(existing, canonical);
        let missing = resolve_path(&dir.path().join("EDIT00000000"));
        assert_eq!(missing, canonical.join("EDIT00000000"));
        let deep = resolve_path(&dir.path().join("no").join("such").join("EDIT"));
        assert_eq!(deep, canonical.join("no").join("such").join("EDIT"));
    }

    #[test]
    fn hashes_match_for_bytes_and_files() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("f");
        fs::write(&path, b"abc").unwrap();
        assert_eq!(
            sha256_hex(b"abc"),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
        assert_eq!(sha256_file(&path).unwrap(), sha256_hex(b"abc"));
    }
}
