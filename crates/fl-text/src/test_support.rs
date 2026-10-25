//! Helpers for tests that read local recorded data (`testdata/`, not in the
//! repository). Absent data skips the test with a note; present-but-broken
//! data still fails loudly.

use std::io::Read;
use std::path::{Path, PathBuf};

/// `testdata/<rel>` if it exists; otherwise prints a skip note and returns `None`.
pub(crate) fn testdata_path(rel: &str) -> Option<PathBuf> {
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../testdata")
        .join(rel);
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

/// The decompressed bytes of the gzip file `testdata/<rel>`, or `None` (with a
/// skip note) when absent.
pub(crate) fn read_gz(rel: &str) -> Option<Vec<u8>> {
    let path = testdata_path(rel)?;
    let file = std::fs::File::open(&path).unwrap_or_else(|error| panic!("{rel}: {error}"));
    let mut bytes = Vec::new();
    flate2::read::GzDecoder::new(file)
        .read_to_end(&mut bytes)
        .unwrap_or_else(|error| panic!("gunzip {rel}: {error}"));
    Some(bytes)
}
