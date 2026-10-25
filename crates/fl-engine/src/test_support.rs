//! Test-only access to the local fixtures.
//!
//! `testdata/`, `reference/` and the base save are local files (not in the
//! repository), so a fresh clone has none of them. Tests that need one ask
//! here and skip with a note when it is absent; a present file that is
//! corrupt still fails loudly.

use std::path::{Path, PathBuf};

use fl_save::crypto;
use fl_save::edit_file::EditFile;

/// Where the base save lives unless `FL_TEST_BASE_SAVE` points elsewhere.
const DEFAULT_BASE_SAVE: &str = "testdata/local/base/EDIT00000000";

fn repo_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../..")
}

/// `path` when it exists; otherwise prints the skip note of the running test
/// and returns `None`.
fn existing(path: PathBuf) -> Option<PathBuf> {
    if path.exists() {
        return Some(path);
    }
    let thread = std::thread::current();
    let test = thread.name().unwrap_or("test");
    eprintln!("skipping {test}: {} not present", path.display());
    None
}

/// `<repo>/testdata/<rel>` when it exists; otherwise prints a skip note.
pub fn testdata_path(rel: &str) -> Option<PathBuf> {
    existing(repo_root().join("testdata").join(rel))
}

/// `<repo>/testdata/<rel>`, or `<rel>.gz` when only the compressed copy
/// exists; prints a skip note when neither does.
pub fn testdata_path_or_gz(rel: &str) -> Option<PathBuf> {
    let plain = repo_root().join("testdata").join(rel);
    if plain.exists() {
        return Some(plain);
    }
    let mut gz = plain.clone().into_os_string();
    gz.push(".gz");
    let gz = PathBuf::from(gz);
    if gz.exists() {
        return Some(gz);
    }
    existing(plain)
}

/// The base save: `FL_TEST_BASE_SAVE` if set, else
/// `testdata/local/base/EDIT00000000`. `None` (with a skip note) when absent.
pub fn base_save_path() -> Option<PathBuf> {
    let path = match std::env::var_os("FL_TEST_BASE_SAVE") {
        Some(custom) => repo_root().join(custom),
        None => repo_root().join(DEFAULT_BASE_SAVE),
    };
    existing(path)
}

/// The decrypted, parsed base save; `None` (with a skip note) when absent.
pub fn base_save() -> Option<EditFile> {
    let raw = std::fs::read(base_save_path()?).expect("base save is readable");
    let blocks = crypto::decrypt(&raw).expect("base save decrypts");
    Some(EditFile::from_bytes(blocks.data).expect("base save loads"))
}
