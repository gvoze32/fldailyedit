//! Timestamped backups of the save a run is about to overwrite.
//!
//! Each save target has its own directory (`<state>/backups/<scope-hash>/`),
//! so backups of one save never evict those of another. Only the newest
//! [`MAX_BACKUPS`] per save are kept.

use std::fs;
use std::io;
use std::path::{Path, PathBuf};

use chrono::{DateTime, Utc};

use crate::paths::StatePaths;

/// Backups kept per save target.
pub const MAX_BACKUPS: usize = 10;

#[derive(Debug, thiserror::Error)]
pub enum BackupError {
    #[error("Cannot back up {}: {source}", path.display())]
    Io { path: PathBuf, source: io::Error },
    #[error("Backup verification failed for {}: content does not match", path.display())]
    Mismatch { path: PathBuf },
}

/// Directory holding the backups of the save identified by `save_scope`
/// (its resolved target path).
pub fn backup_dir(state_dir: &Path, save_scope: &str) -> PathBuf {
    StatePaths::new(state_dir).backups_dir(save_scope)
}

fn io_error(path: &Path) -> impl FnOnce(io::Error) -> BackupError + '_ {
    |source| BackupError::Io {
        path: path.to_path_buf(),
        source,
    }
}

/// Copy `source` into the backup directory of `save_scope`, verify the copy
/// byte for byte, and prune old backups. Returns the backup path.
pub fn create_backup(
    state_dir: &Path,
    save_scope: &str,
    source: &Path,
    now: DateTime<Utc>,
) -> Result<PathBuf, BackupError> {
    let dir = backup_dir(state_dir, save_scope);
    fs::create_dir_all(&dir).map_err(io_error(&dir))?;
    let file_name = source
        .file_name()
        .map_or_else(|| "save".into(), |n| n.to_string_lossy().into_owned());
    let stamp = now.format("%Y%m%d_%H%M%S_%6f");
    let mut backup = dir.join(format!("{file_name}.bak.{stamp}"));
    // Two backups in the same microsecond (frozen clocks) must not collide.
    let mut counter = 0u32;
    while backup.exists() {
        counter += 1;
        backup = dir.join(format!("{file_name}.bak.{stamp}.{counter}"));
    }

    fs::copy(source, &backup).map_err(io_error(source))?;
    let original = fs::read(source).map_err(io_error(source))?;
    let copy = fs::read(&backup).map_err(io_error(&backup))?;
    if original != copy {
        let _ = fs::remove_file(&backup);
        return Err(BackupError::Mismatch { path: backup });
    }
    prune(&dir, &file_name, &backup);
    Ok(backup)
}

/// Delete the oldest backups of `file_name` beyond [`MAX_BACKUPS`], never
/// `keep`. Order comes from the timestamp in the name, not file mtimes.
fn prune(dir: &Path, file_name: &str, keep: &Path) {
    let prefix = format!("{file_name}.bak.");
    let Ok(entries) = fs::read_dir(dir) else {
        return;
    };
    let mut backups: Vec<(String, PathBuf)> = entries
        .filter_map(Result::ok)
        .filter_map(|entry| {
            let name = entry.file_name().to_string_lossy().into_owned();
            let suffix = name.strip_prefix(&prefix)?.to_owned();
            Some((suffix, entry.path()))
        })
        .filter(|(_, path)| path != keep)
        .collect();
    backups.sort();
    let excess = (backups.len() + 1).saturating_sub(MAX_BACKUPS);
    for (_, path) in backups.into_iter().take(excess) {
        let _ = fs::remove_file(path);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::TimeZone;

    fn at(second: u32) -> DateTime<Utc> {
        Utc.with_ymd_and_hms(2026, 1, 1, 0, 0, second).unwrap()
    }

    #[test]
    fn backs_up_verifies_and_prunes_to_limit() {
        let dir = tempfile::tempdir().unwrap();
        let state = dir.path().join("state");
        let save = dir.path().join("EDIT00000000");
        fs::write(&save, b"save bytes").unwrap();
        let mut last = None;
        for second in 0..14 {
            last = Some(create_backup(&state, "scope-a", &save, at(second)).unwrap());
        }
        let folder = backup_dir(&state, "scope-a");
        let mut names: Vec<_> = fs::read_dir(&folder)
            .unwrap()
            .map(|e| e.unwrap().file_name().to_string_lossy().into_owned())
            .collect();
        names.sort();
        assert_eq!(names.len(), MAX_BACKUPS);
        assert!(names[0].contains("000004"), "oldest kept: {names:?}");
        assert_eq!(fs::read(last.unwrap()).unwrap(), b"save bytes");
    }

    #[test]
    fn saves_do_not_evict_each_other() {
        let dir = tempfile::tempdir().unwrap();
        let state = dir.path().join("state");
        let save = dir.path().join("EDIT00000000");
        fs::write(&save, b"x").unwrap();
        let first = create_backup(&state, "scope-a", &save, at(0)).unwrap();
        for second in 1..=12 {
            create_backup(&state, "scope-b", &save, at(second)).unwrap();
        }
        assert!(first.exists());
    }

    #[test]
    fn same_instant_backups_do_not_overwrite() {
        let dir = tempfile::tempdir().unwrap();
        let state = dir.path().join("state");
        let save = dir.path().join("EDIT00000000");
        fs::write(&save, b"x").unwrap();
        let a = create_backup(&state, "s", &save, at(0)).unwrap();
        let b = create_backup(&state, "s", &save, at(0)).unwrap();
        assert_ne!(a, b);
        assert!(a.exists() && b.exists());
    }

    #[test]
    fn missing_source_is_an_error() {
        let dir = tempfile::tempdir().unwrap();
        let error = create_backup(dir.path(), "s", &dir.path().join("nope"), at(0)).unwrap_err();
        assert!(matches!(error, BackupError::Io { .. }));
    }
}
