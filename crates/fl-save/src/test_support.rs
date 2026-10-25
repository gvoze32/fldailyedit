//! Test-only access to local data.
//!
//! The repository ships no test data: fixtures live under `testdata/`, game
//! files under `reference/`, and the owner's own save at
//! `testdata/local/base/EDIT00000000` (or the path in `FL_TEST_BASE_SAVE`).
//! All of it is gitignored. Every helper returns `None`, with a note on
//! stderr, when the file is absent so the test skips.

use std::path::{Path, PathBuf};

fn repo_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../..")
}

/// `rel` below the repository root (`testdata/...`, `reference/...`), when it exists.
pub(crate) fn local_path(rel: &str) -> Option<PathBuf> {
    let path = repo_root().join(rel);
    if path.exists() {
        Some(path)
    } else {
        eprintln!("skipping: {rel} not present");
        None
    }
}

/// The bytes of `rel` below the repository root, when it exists.
pub(crate) fn local_bytes(rel: &str) -> Option<Vec<u8>> {
    let path = local_path(rel)?;
    Some(std::fs::read(&path).unwrap_or_else(|e| panic!("read {}: {e}", path.display())))
}

/// Where the local base save is expected.
pub(crate) fn base_save_path() -> PathBuf {
    match std::env::var_os("FL_TEST_BASE_SAVE") {
        Some(path) => PathBuf::from(path),
        None => repo_root().join("testdata/local/base/EDIT00000000"),
    }
}

/// The base save's bytes, or `None` when it is absent.
pub(crate) fn base_save_bytes() -> Option<Vec<u8>> {
    let path = base_save_path();
    match std::fs::read(&path) {
        Ok(bytes) => Some(bytes),
        Err(_) => {
            eprintln!(
                "skipping: local base save not present at {}",
                path.display()
            );
            None
        }
    }
}
