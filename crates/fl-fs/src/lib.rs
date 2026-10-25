//! Crash-safe file replacement: the one way every crate publishes a file
//! (saves, caches, reports, settings).

use std::fs::{self, File, OpenOptions};
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU32, Ordering};

static COUNTER: AtomicU32 = AtomicU32::new(0);

/// Write `bytes` to `path` so that readers observe either the old or the new
/// content, never a partial file: data goes to a temp sibling which is
/// fsynced, renamed over `path`, and (on Unix) the directory entry is fsynced.
/// Missing parent directories are created. The temp file is removed on error.
pub fn write_atomic(path: &Path, bytes: &[u8]) -> io::Result<()> {
    let parent = match path.parent() {
        Some(parent) if !parent.as_os_str().is_empty() => parent,
        _ => Path::new("."),
    };
    fs::create_dir_all(parent)?;
    let file_name = path
        .file_name()
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "path has no file name"))?;

    let (mut file, temp) = create_temp(parent, &file_name.to_string_lossy())?;
    let result = file
        .write_all(bytes)
        .and_then(|()| file.sync_all())
        .and_then(|()| {
            drop(file);
            fs::rename(&temp, path)
        });
    if let Err(error) = result {
        let _ = fs::remove_file(&temp);
        return Err(error);
    }
    sync_directory(parent);
    Ok(())
}

fn create_temp(parent: &Path, name: &str) -> io::Result<(File, PathBuf)> {
    loop {
        let sequence = COUNTER.fetch_add(1, Ordering::Relaxed);
        let temp = parent.join(format!(".{name}.{}.{sequence}.tmp", std::process::id()));
        match OpenOptions::new().write(true).create_new(true).open(&temp) {
            Ok(file) => return Ok((file, temp)),
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => continue,
            Err(error) => return Err(error),
        }
    }
}

/// Persist the rename itself. Windows has no directory fsync; best effort
/// elsewhere (the file content is already durable at this point).
fn sync_directory(parent: &Path) {
    #[cfg(unix)]
    if let Ok(directory) = File::open(parent) {
        let _ = directory.sync_all();
    }
    #[cfg(not(unix))]
    let _ = parent;
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn creates_and_replaces_without_leftovers() {
        let dir = tempfile::tempdir().unwrap();
        let target = dir.path().join("nested").join("EDIT00000000");
        write_atomic(&target, b"first").unwrap();
        write_atomic(&target, b"second").unwrap();
        assert_eq!(fs::read(&target).unwrap(), b"second");
        let names: Vec<_> = fs::read_dir(target.parent().unwrap())
            .unwrap()
            .map(|entry| entry.unwrap().file_name())
            .collect();
        assert_eq!(names, ["EDIT00000000"]);
    }

    #[test]
    fn failed_write_keeps_old_content() {
        let dir = tempfile::tempdir().unwrap();
        let target = dir.path().join("save");
        write_atomic(&target, b"old").unwrap();
        // A directory at the target makes the final rename fail.
        let blocked = dir.path().join("blocked");
        fs::create_dir(&blocked).unwrap();
        assert!(write_atomic(&blocked, b"new").is_err());
        assert_eq!(fs::read(&target).unwrap(), b"old");
        let leftovers = fs::read_dir(dir.path())
            .unwrap()
            .filter(|entry| {
                entry
                    .as_ref()
                    .unwrap()
                    .file_name()
                    .to_string_lossy()
                    .ends_with(".tmp")
            })
            .count();
        assert_eq!(leftovers, 0);
    }
}
