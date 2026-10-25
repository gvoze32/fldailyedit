//! Helpers for tests that read local recorded data (`testdata/`, not in the
//! repository). Absent data skips the test with a note; present-but-broken
//! data still fails loudly.

use std::path::{Path, PathBuf};

fn testdata_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../testdata")
}

/// `testdata/<rel>` if it exists; otherwise prints a skip note and returns `None`.
pub(crate) fn testdata_path(rel: &str) -> Option<PathBuf> {
    let path = testdata_root().join(rel);
    if path.exists() {
        return Some(path);
    }
    let thread = std::thread::current();
    eprintln!(
        "skipping {}: testdata/{rel} not present",
        thread.name().unwrap_or("test")
    );
    None
}

/// The text of `testdata/<rel>`, or `None` (with a skip note) when absent.
pub(crate) fn read_fixture_text(rel: &str) -> Option<String> {
    let path = testdata_path(rel)?;
    Some(std::fs::read_to_string(&path).unwrap_or_else(|error| panic!("{rel}: {error}")))
}

/// The JSON in the gzip file `testdata/<rel>`, or `None` (with a skip note) when absent.
pub(crate) fn read_gz_json(rel: &str) -> Option<serde_json::Value> {
    use std::io::Read;
    let path = testdata_path(rel)?;
    let file = std::fs::File::open(&path).unwrap_or_else(|error| panic!("{rel}: {error}"));
    let mut text = String::new();
    flate2::read::GzDecoder::new(file)
        .read_to_string(&mut text)
        .unwrap_or_else(|error| panic!("gunzip {rel}: {error}"));
    Some(serde_json::from_str(&text).unwrap_or_else(|error| panic!("parse {rel}: {error}")))
}
