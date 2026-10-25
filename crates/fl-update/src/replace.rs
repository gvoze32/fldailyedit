//! Swap the running executable for the staged one and restart it.
//!
//! The swap uses the rename-self technique: the running exe is renamed to
//! `<name>.old` (allowed on Windows while it executes, as long as it stays on the
//! same volume) and the new exe takes its original path. The new file is copied next
//! to the target *before* anything is renamed so the exposed window is two renames.

use std::ffi::OsString;
use std::fs;
use std::io;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, SystemTime};

use crate::error::{Error, Result};
use crate::update::{STAGING_PREFIX, Staged, cleanup_staged};

/// Staging directories older than this are considered abandoned.
const STALE_STAGING_AGE: Duration = Duration::from_secs(24 * 60 * 60);

/// Result of a successful [`replace_current_exe`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Replaced {
    /// The freshly installed executable (same path the old one had).
    pub executable: PathBuf,
    /// The previous executable, renamed to `<name>.old`; deleted by
    /// [`cleanup_after_update`] on the next launch.
    pub previous: PathBuf,
}

fn sibling_with_suffix(path: &Path, suffix: &str) -> PathBuf {
    let mut name: OsString = path.file_name().unwrap_or_default().to_owned();
    name.push(suffix);
    path.with_file_name(name)
}

fn remove_if_present(path: &Path) -> io::Result<()> {
    match fs::remove_file(path) {
        Err(error) if error.kind() != io::ErrorKind::NotFound => Err(error),
        _ => Ok(()),
    }
}

/// Replace `current_exe` (the running executable, whatever it is called) with the
/// staged executable and remove the staging directory.
///
/// On failure `current_exe` is left exactly as it was and the staging directory is
/// kept so the caller can retry or [`Staged::discard`] it. Unix permissions of the
/// old file are carried over to the new one.
pub fn replace_current_exe(staged: &Staged, current_exe: &Path) -> Result<Replaced> {
    replace_with(staged, current_exe, |from, to| fs::rename(from, to))
}

fn replace_with(
    staged: &Staged,
    current_exe: &Path,
    rename: impl Fn(&Path, &Path) -> io::Result<()>,
) -> Result<Replaced> {
    let metadata = fs::metadata(current_exe)
        .map_err(|error| Error::io(format!("cannot inspect {}", current_exe.display()), error))?;
    if !metadata.is_file() || current_exe.file_name().is_none() {
        return Err(Error::io(
            format!("{} is not a file", current_exe.display()),
            io::Error::from(io::ErrorKind::InvalidInput),
        ));
    }
    let previous = sibling_with_suffix(current_exe, ".old");
    let fresh = sibling_with_suffix(current_exe, ".new");

    remove_if_present(&previous)
        .map_err(|error| Error::io(format!("cannot remove stale {}", previous.display()), error))?;
    remove_if_present(&fresh)
        .map_err(|error| Error::io(format!("cannot remove stale {}", fresh.display()), error))?;

    if let Err(error) = prepare_fresh(&staged.executable, &fresh, &metadata) {
        let _ = fs::remove_file(&fresh);
        return Err(error);
    }
    rename(current_exe, &previous).map_err(|error| {
        let _ = fs::remove_file(&fresh);
        Error::io(
            format!("cannot move {} aside", current_exe.display()),
            error,
        )
    })?;
    if let Err(error) = rename(&fresh, current_exe) {
        let rollback = rename(&previous, current_exe);
        let _ = fs::remove_file(&fresh);
        let context = match rollback {
            Ok(()) => format!("cannot install the new {}", current_exe.display()),
            Err(rollback_error) => format!(
                "cannot install the new {} and rolling back failed ({rollback_error}); \
                 the previous version is at {}",
                current_exe.display(),
                previous.display()
            ),
        };
        return Err(Error::io(context, error));
    }

    cleanup_staged(&staged.executable);
    Ok(Replaced {
        executable: current_exe.to_path_buf(),
        previous,
    })
}

fn prepare_fresh(staged: &Path, fresh: &Path, current: &fs::Metadata) -> Result<()> {
    fs::copy(staged, fresh).map_err(|error| {
        Error::io(
            "cannot copy the staged executable next to the target",
            error,
        )
    })?;
    fs::set_permissions(fresh, current.permissions())
        .map_err(|error| Error::io("cannot copy file permissions to the new executable", error))?;
    fs::OpenOptions::new()
        .write(true)
        .open(fresh)
        .and_then(|file| file.sync_all())
        .map_err(|error| Error::io("cannot flush the new executable", error))
}

/// Start the replaced executable with no arguments, fully detached from this process
/// (no console, own process group, null stdio). The caller should exit right after.
pub fn restart(replaced: &Replaced) -> Result<()> {
    let mut command = Command::new(&replaced.executable);
    command
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    if let Some(directory) = replaced
        .executable
        .parent()
        .filter(|path| !path.as_os_str().is_empty())
    {
        command.current_dir(directory);
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const DETACHED_PROCESS: u32 = 0x0000_0008;
        const CREATE_NEW_PROCESS_GROUP: u32 = 0x0000_0200;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        // CREATE_NO_WINDOW is ignored next to DETACHED_PROCESS; it only documents intent.
        command.creation_flags(DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW);
    }
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        command.process_group(0);
    }
    // The child is intentionally not waited for: the caller exits immediately.
    #[allow(clippy::zombie_processes)]
    command.spawn().map(drop).map_err(|error| {
        Error::io(
            format!("cannot start {}", replaced.executable.display()),
            error,
        )
    })
}

/// Call at startup: delete the `<name>.old`/`<name>.new` leftovers next to
/// `current_exe` and abandoned staging directories (older than 24 h) in the temp dir.
/// All errors are ignored; a locked file is simply retried on the next launch.
pub fn cleanup_after_update(current_exe: &Path) {
    let _ = fs::remove_file(sibling_with_suffix(current_exe, ".old"));
    let _ = fs::remove_file(sibling_with_suffix(current_exe, ".new"));
    cleanup_stale_staging(&std::env::temp_dir());
}

fn cleanup_stale_staging(temp_dir: &Path) {
    let Ok(entries) = fs::read_dir(temp_dir) else {
        return;
    };
    for entry in entries.flatten() {
        let is_staging = entry
            .file_name()
            .to_str()
            .is_some_and(|name| name.starts_with(STAGING_PREFIX));
        let is_stale = entry
            .metadata()
            .ok()
            .filter(fs::Metadata::is_dir)
            .and_then(|metadata| metadata.modified().ok())
            .and_then(|modified| SystemTime::now().duration_since(modified).ok())
            .is_some_and(|age| age >= STALE_STAGING_AGE);
        if is_staging && is_stale {
            let _ = fs::remove_dir_all(entry.path());
        }
    }
}

#[cfg(test)]
mod tests {
    use std::cell::Cell;

    use super::*;
    use crate::manifest::Version;

    fn staged_in(dir: &Path, content: &[u8]) -> Staged {
        let staging = dir.join(format!("{STAGING_PREFIX}test"));
        fs::create_dir_all(&staging).unwrap();
        let executable = staging.join("FLDailyEdit.exe");
        fs::write(&executable, content).unwrap();
        Staged {
            executable,
            version: Version {
                major: 2,
                minor: 0,
                patch: 0,
            },
        }
    }

    #[test]
    fn replaces_any_exe_name_and_keeps_the_old_one_aside() {
        let dir = tempfile::tempdir().unwrap();
        let current = dir.path().join("FLDailyEdit.exe");
        fs::write(&current, b"old").unwrap();
        let staged = staged_in(dir.path(), b"new");
        let staging_dir = staged.executable.parent().unwrap().to_path_buf();

        let replaced = replace_current_exe(&staged, &current).unwrap();

        assert_eq!(replaced.executable, current);
        assert_eq!(fs::read(&current).unwrap(), b"new");
        assert_eq!(
            fs::read(dir.path().join("FLDailyEdit.exe.old")).unwrap(),
            b"old"
        );
        assert!(!dir.path().join("FLDailyEdit.exe.new").exists());
        assert!(
            !staging_dir.exists(),
            "staging directory is removed on success"
        );

        cleanup_after_update(&current);
        assert!(!replaced.previous.exists());
    }

    #[test]
    fn a_stale_old_file_is_cleared_first() {
        let dir = tempfile::tempdir().unwrap();
        let current = dir.path().join("FLDailyEdit.exe");
        fs::write(&current, b"old").unwrap();
        fs::write(dir.path().join("FLDailyEdit.exe.old"), b"ancient").unwrap();
        let staged = staged_in(dir.path(), b"new");

        replace_current_exe(&staged, &current).unwrap();

        assert_eq!(
            fs::read(dir.path().join("FLDailyEdit.exe.old")).unwrap(),
            b"old"
        );
    }

    #[cfg(unix)]
    #[test]
    fn unix_permissions_are_carried_over() {
        use std::os::unix::fs::PermissionsExt;
        let dir = tempfile::tempdir().unwrap();
        let current = dir.path().join("app");
        fs::write(&current, b"old").unwrap();
        fs::set_permissions(&current, fs::Permissions::from_mode(0o755)).unwrap();
        let staged = staged_in(dir.path(), b"new");

        replace_current_exe(&staged, &current).unwrap();

        let mode = fs::metadata(&current).unwrap().permissions().mode();
        assert_eq!(mode & 0o777, 0o755);
    }

    #[test]
    fn a_missing_staged_file_leaves_the_current_exe_untouched() {
        let dir = tempfile::tempdir().unwrap();
        let current = dir.path().join("app.exe");
        fs::write(&current, b"old").unwrap();
        let staged = staged_in(dir.path(), b"new");
        fs::remove_file(&staged.executable).unwrap();

        let error = replace_current_exe(&staged, &current).unwrap_err();

        assert_eq!(error.kind(), crate::ErrorKind::Io);
        assert_eq!(fs::read(&current).unwrap(), b"old");
        assert!(!dir.path().join("app.exe.new").exists());
        assert!(!dir.path().join("app.exe.old").exists());
    }

    #[test]
    fn a_missing_current_exe_is_an_io_error() {
        let dir = tempfile::tempdir().unwrap();
        let staged = staged_in(dir.path(), b"new");
        let error = replace_current_exe(&staged, &dir.path().join("absent.exe")).unwrap_err();
        assert_eq!(error.kind(), crate::ErrorKind::Io);
        assert!(staged.executable.exists());
    }

    #[test]
    fn failing_to_install_rolls_the_old_exe_back() {
        let dir = tempfile::tempdir().unwrap();
        let current = dir.path().join("app.exe");
        fs::write(&current, b"old").unwrap();
        let staged = staged_in(dir.path(), b"new");
        let calls = Cell::new(0);

        let error = replace_with(&staged, &current, |from, to| {
            calls.set(calls.get() + 1);
            if calls.get() == 2 {
                Err(io::Error::from(io::ErrorKind::PermissionDenied))
            } else {
                fs::rename(from, to)
            }
        })
        .unwrap_err();

        assert_eq!(error.kind(), crate::ErrorKind::Io);
        assert_eq!(calls.get(), 3, "move aside, install (fails), roll back");
        assert_eq!(fs::read(&current).unwrap(), b"old");
        assert!(!dir.path().join("app.exe.new").exists());
        assert!(!dir.path().join("app.exe.old").exists());
        assert!(staged.executable.exists(), "staging kept for retry");
    }

    #[test]
    fn failing_to_move_aside_leaves_everything_in_place() {
        let dir = tempfile::tempdir().unwrap();
        let current = dir.path().join("app.exe");
        fs::write(&current, b"old").unwrap();
        let staged = staged_in(dir.path(), b"new");

        let error = replace_with(&staged, &current, |_, _| {
            Err(io::Error::from(io::ErrorKind::PermissionDenied))
        })
        .unwrap_err();

        assert_eq!(error.kind(), crate::ErrorKind::Io);
        assert_eq!(fs::read(&current).unwrap(), b"old");
        assert!(!dir.path().join("app.exe.new").exists());
    }

    #[test]
    fn only_old_staging_directories_are_swept() {
        let temp = tempfile::tempdir().unwrap();
        let fresh = temp.path().join(format!("{STAGING_PREFIX}fresh"));
        let stale = temp.path().join(format!("{STAGING_PREFIX}stale"));
        let unrelated = temp.path().join("something-else");
        for path in [&fresh, &stale, &unrelated] {
            fs::create_dir(path).unwrap();
        }
        let old = SystemTime::now() - Duration::from_secs(3 * 24 * 60 * 60);
        fs::File::open(&stale).unwrap().set_modified(old).unwrap();
        fs::File::open(&unrelated)
            .unwrap()
            .set_modified(old)
            .unwrap();

        cleanup_stale_staging(temp.path());

        assert!(fresh.exists());
        assert!(!stale.exists());
        assert!(unrelated.exists());
    }

    #[cfg(unix)]
    #[test]
    fn restart_launches_the_new_executable_without_arguments() {
        use std::os::unix::fs::PermissionsExt;
        let dir = tempfile::tempdir().unwrap();
        let marker = dir.path().join("marker");
        let script = dir.path().join("app.sh");
        fs::write(
            &script,
            format!("#!/bin/sh\necho \"args=$#\" > '{}'\n", marker.display()),
        )
        .unwrap();
        fs::set_permissions(&script, fs::Permissions::from_mode(0o755)).unwrap();

        restart(&Replaced {
            executable: script,
            previous: dir.path().join("app.sh.old"),
        })
        .unwrap();

        for _ in 0..100 {
            if marker.exists() && fs::metadata(&marker).unwrap().len() > 0 {
                break;
            }
            std::thread::sleep(Duration::from_millis(50));
        }
        assert_eq!(fs::read_to_string(&marker).unwrap(), "args=0\n");
    }
}
