//! Exclusive per-save lock: one update may write a given save at a time.
//!
//! The lock is an OS advisory lock on a hidden sibling file (`.<name>.lock`).
//! It is released and the file removed when the guard is dropped.

use std::fs::{self, File, OpenOptions, TryLockError};
use std::io::{self, Write};
use std::path::{Path, PathBuf};

#[derive(Debug, thiserror::Error)]
pub enum LockError {
    #[error("Another update is already using {}", target.display())]
    Held { target: PathBuf },
    #[error("Could not lock {}: {source}", target.display())]
    Io { target: PathBuf, source: io::Error },
}

/// Guard holding the lock for one save target.
#[derive(Debug)]
pub struct SaveLock {
    path: PathBuf,
    file: Option<File>,
}

/// Path of the lock file guarding `target`.
fn lock_path(target: &Path) -> PathBuf {
    let name = target.file_name().map_or_else(
        || "save".to_owned(),
        |name| name.to_string_lossy().into_owned(),
    );
    target.with_file_name(format!(".{name}.lock"))
}

impl SaveLock {
    /// Take the lock for `target`, creating its parent directory if needed.
    pub fn acquire(target: &Path) -> Result<Self, LockError> {
        let io_error = |source| LockError::Io {
            target: target.to_path_buf(),
            source,
        };
        let path = lock_path(target);
        if let Some(parent) = path.parent().filter(|p| !p.as_os_str().is_empty()) {
            fs::create_dir_all(parent).map_err(io_error)?;
        }
        let mut file = OpenOptions::new()
            .create(true)
            .truncate(false)
            .read(true)
            .write(true)
            .open(&path)
            .map_err(io_error)?;
        match file.try_lock() {
            Ok(()) => {}
            Err(TryLockError::WouldBlock) => {
                return Err(LockError::Held {
                    target: target.to_path_buf(),
                });
            }
            Err(TryLockError::Error(source)) => return Err(io_error(source)),
        }
        file.set_len(0).map_err(io_error)?;
        // Diagnostic only: who holds the lock.
        write!(file, "pid={}", std::process::id()).map_err(io_error)?;
        file.flush().map_err(io_error)?;
        Ok(Self {
            path,
            file: Some(file),
        })
    }
}

impl Drop for SaveLock {
    fn drop(&mut self) {
        // Unix: remove while still holding the lock so no other process can
        // lock the inode we are deleting. Elsewhere an open file cannot be
        // removed, so release first.
        #[cfg(unix)]
        {
            let _ = fs::remove_file(&self.path);
            self.file.take();
        }
        #[cfg(not(unix))]
        {
            self.file.take();
            let _ = fs::remove_file(&self.path);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn second_acquire_fails_until_first_is_dropped_and_file_is_removed() {
        let dir = tempfile::tempdir().unwrap();
        let target = dir.path().join("out").join("EDIT00000000");
        let first = SaveLock::acquire(&target).unwrap();
        let lock_file = lock_path(&target);
        assert!(lock_file.is_file());
        assert!(matches!(
            SaveLock::acquire(&target),
            Err(LockError::Held { .. })
        ));
        drop(first);
        assert!(!lock_file.exists());
        drop(SaveLock::acquire(&target).unwrap());
        assert!(!lock_file.exists());
    }

    #[test]
    fn distinct_saves_do_not_block_each_other() {
        let dir = tempfile::tempdir().unwrap();
        let _a = SaveLock::acquire(&dir.path().join("A")).unwrap();
        let _b = SaveLock::acquire(&dir.path().join("B")).unwrap();
    }
}
