//! Where saves, game files and per-user state live, plus the guards that keep
//! the engine from writing somewhere it must not.
//!
//! Nothing here writes to disk: discovery and validation only `stat`/read, so
//! a read-only or cloud-synced Documents folder never gets probe files.

use std::ffi::OsString;
use std::fs;
use std::io;
use std::path::{Component, Path, PathBuf};

use fl_save::cpk::discover;

/// File name of the encrypted save inside a `save` folder.
pub const SAVE_FILE_NAME: &str = "EDIT00000000";

const STATE_DIR_NAME: &str = "FLDailyEdit";
const FL26_RELATIVE: [&str; 5] = [
    "Documents",
    "KONAMI",
    "eFootball PES 2021 SEASON UPDATE",
    "2026",
    "save",
];
const PES_PARENT_RELATIVE: [&str; 3] = ["Documents", "KONAMI", "eFootball PES 2021 SEASON UPDATE"];
const FL26_FOLDER: &str = "2026";
/// Roots that may hold `Documents` (OneDrive redirects it on many PCs).
const SAVE_ROOT_VARIABLES: [&str; 3] = ["USERPROFILE", "OneDrive", "OneDriveConsumer"];

type Env<'a> = &'a dyn Fn(&str) -> Option<OsString>;

fn process_env(name: &str) -> Option<OsString> {
    std::env::var_os(name).filter(|value| !value.is_empty())
}

// ── per-user state ────────────────────────────────────────────────────────

/// `%LOCALAPPDATA%\FLDailyEdit` on Windows, `~/Library/Application Support/FLDailyEdit`
/// on macOS, `$XDG_DATA_HOME/FLDailyEdit` (or `~/.local/share/FLDailyEdit`) elsewhere.
/// `None` when the environment names no home at all.
pub fn state_dir() -> Option<PathBuf> {
    state_dir_with(&process_env)
}

pub fn state_dir_with(env: Env<'_>) -> Option<PathBuf> {
    let base = if cfg!(windows) {
        PathBuf::from(env("LOCALAPPDATA")?)
    } else if cfg!(target_os = "macos") {
        PathBuf::from(env("HOME")?).join("Library/Application Support")
    } else if let Some(xdg) = env("XDG_DATA_HOME") {
        PathBuf::from(xdg)
    } else {
        PathBuf::from(env("HOME")?).join(".local/share")
    };
    Some(base.join(STATE_DIR_NAME))
}

// ── save discovery ────────────────────────────────────────────────────────

/// Game family a save folder belongs to, judged from where it lives.
/// The authoritative answer is [`inspect_save`] (the container header).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SaveFamily {
    FootballLife26,
    Pes2021,
}

impl SaveFamily {
    pub fn game_name(self) -> &'static str {
        match self {
            Self::FootballLife26 => "Football Life 2026",
            Self::Pes2021 => "PES 2021",
        }
    }
}

/// A save found in a well-known location.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SaveCandidate {
    /// The `EDIT00000000` file.
    pub edit_file: PathBuf,
    pub family: SaveFamily,
}

/// Saves of Football Life 2026 and vanilla PES 2021 below the user's
/// Documents folders, Football Life first, then in the order `USERPROFILE`,
/// `OneDrive`, `OneDriveConsumer` and ascending folder name.
pub fn discover_saves() -> Vec<SaveCandidate> {
    discover_saves_with(&process_env)
}

pub fn discover_saves_with(env: Env<'_>) -> Vec<SaveCandidate> {
    let mut found: Vec<SaveCandidate> = Vec::new();
    let mut add = |family, save_dir: PathBuf| {
        let edit_file = save_dir.join(SAVE_FILE_NAME);
        let key = fold_key(&edit_file);
        if edit_file.is_file() && !found.iter().any(|c| fold_key(&c.edit_file) == key) {
            found.push(SaveCandidate { edit_file, family });
        }
    };

    for variable in SAVE_ROOT_VARIABLES {
        let Some(root) = env(variable).map(PathBuf::from) else {
            continue;
        };
        add(
            SaveFamily::FootballLife26,
            FL26_RELATIVE.iter().fold(root.clone(), |p, c| p.join(c)),
        );
        let parent = PES_PARENT_RELATIVE.iter().fold(root, |p, c| p.join(c));
        let Ok(children) = fs::read_dir(&parent) else {
            continue;
        };
        let mut folders: Vec<PathBuf> = children
            .filter_map(|entry| entry.ok())
            .filter(|entry| {
                let name = entry.file_name();
                let name = name.to_string_lossy();
                name != FL26_FOLDER
                    && !name.is_empty()
                    && name.bytes().all(|byte| byte.is_ascii_digit())
            })
            .map(|entry| entry.path())
            .collect();
        folders.sort_by_cached_key(|path| fold_key(path));
        for folder in folders {
            add(SaveFamily::Pes2021, folder.join("save"));
        }
    }

    // Stable: within a family, `USERPROFILE` results precede the OneDrive ones.
    found.sort_by_key(|c| match c.family {
        SaveFamily::FootballLife26 => 0,
        SaveFamily::Pes2021 => 1,
    });
    found
}

fn fold_key(path: &Path) -> String {
    path.to_string_lossy().to_lowercase()
}

// ── guards ────────────────────────────────────────────────────────────────

#[derive(Debug, thiserror::Error)]
pub enum SaveFileError {
    #[error("{0} does not exist")]
    Missing(PathBuf),
    #[error("{0} is not a file")]
    NotAFile(PathBuf),
    #[error("{0} is a symbolic link or junction; choose the real file")]
    Link(PathBuf),
    #[error(
        "{0} is inside the game installation. Choose the save in your Documents folder \
         (…\\KONAMI\\eFootball PES 2021 SEASON UPDATE\\…\\save\\EDIT00000000)"
    )]
    InsideGame(PathBuf),
    #[error("{path} cannot be read: {source}")]
    Unreadable { path: PathBuf, source: io::Error },
}

/// Turn a user's choice into the save file to use.
///
/// A folder is accepted when it holds `EDIT00000000`. The file may have any
/// name (copies and backups are fine), may live on any drive and is never
/// written to. Links and game-installation paths are refused.
pub fn resolve_save_file(
    chosen: &Path,
    game_root: Option<&Path>,
) -> Result<PathBuf, SaveFileError> {
    let meta = link_free_metadata(chosen)?;
    let file = if meta.is_dir() {
        chosen.join(SAVE_FILE_NAME)
    } else {
        chosen.to_path_buf()
    };
    if !meta.is_dir() && !meta.is_file() {
        return Err(SaveFileError::NotAFile(file));
    }
    let meta = if file == chosen {
        meta
    } else {
        link_free_metadata(&file)?
    };
    if !meta.is_file() {
        return Err(SaveFileError::NotAFile(file));
    }
    reject_game_root_save(&file, game_root)?;
    Ok(file)
}

fn link_free_metadata(path: &Path) -> Result<fs::Metadata, SaveFileError> {
    match fs::symlink_metadata(path) {
        Ok(meta) if meta.file_type().is_symlink() => Err(SaveFileError::Link(path.to_path_buf())),
        Ok(meta) => Ok(meta),
        Err(source) if source.kind() == io::ErrorKind::NotFound => {
            Err(SaveFileError::Missing(path.to_path_buf()))
        }
        Err(source) => Err(SaveFileError::Unreadable {
            path: path.to_path_buf(),
            source,
        }),
    }
}

/// Refuse a save that sits inside the game installation: either below the
/// selected `game_root`, or below any folder that looks like an installation
/// (holds `download/*.cpk`).
pub fn reject_game_root_save(path: &Path, game_root: Option<&Path>) -> Result<(), SaveFileError> {
    let candidate = normalize(path);
    if let Some(root) = game_root {
        let root = normalize(root);
        if !root.as_os_str().is_empty() && starts_with_fold(&candidate, &root) {
            return Err(SaveFileError::InsideGame(path.to_path_buf()));
        }
    }
    if candidate
        .ancestors()
        .skip(1)
        .any(|ancestor| !ancestor.as_os_str().is_empty() && looks_like_game_root(ancestor))
    {
        return Err(SaveFileError::InsideGame(path.to_path_buf()));
    }
    Ok(())
}

fn looks_like_game_root(folder: &Path) -> bool {
    fs::read_dir(folder.join("download")).is_ok_and(|entries| {
        entries.filter_map(|e| e.ok()).any(|entry| {
            entry
                .path()
                .extension()
                .is_some_and(|ext| ext.eq_ignore_ascii_case("cpk"))
        })
    })
}

/// Canonical form when the path exists; otherwise the lexically cleaned path.
fn normalize(path: &Path) -> PathBuf {
    if let Ok(real) = fs::canonicalize(path) {
        return real;
    }
    // Canonicalize the deepest existing ancestor so `\\?\` prefixes and
    // symlinked temp folders compare equal, then re-attach the rest.
    let mut tail = Vec::new();
    let mut head = path;
    while let Some(parent) = head.parent() {
        if let Some(name) = head.file_name() {
            tail.push(name.to_os_string());
        }
        if let Ok(real) = fs::canonicalize(parent) {
            return tail.iter().rev().fold(real, |p, name| p.join(name));
        }
        head = parent;
    }
    path.components()
        .filter(|component| !matches!(component, Component::CurDir))
        .collect()
}

fn starts_with_fold(path: &Path, prefix: &Path) -> bool {
    let mut path_components = path.components();
    prefix.components().all(|expected| {
        path_components.next().is_some_and(|actual| {
            actual == expected
                || actual
                    .as_os_str()
                    .to_string_lossy()
                    .eq_ignore_ascii_case(&expected.as_os_str().to_string_lossy())
                    && cfg!(any(windows, target_os = "macos"))
        })
    })
}

// ── save inspection ───────────────────────────────────────────────────────

/// Which game wrote the save, from the container header.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SaveKind {
    /// Football Life 2026 (and other non-vanilla patches): player data is
    /// embedded in the app.
    FootballLife26,
    /// Vanilla PES 2021 / T99: player data comes from the game's CPK files,
    /// so the game folder is required.
    Pes2021,
}

impl SaveKind {
    pub fn needs_game_folder(self) -> bool {
        self == Self::Pes2021
    }

    pub fn game_name(self) -> &'static str {
        match self {
            Self::FootballLife26 => "Football Life 2026",
            Self::Pes2021 => "PES 2021",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SaveInfo {
    pub kind: SaveKind,
    pub game_version: String,
    pub teams: usize,
    pub clubs: usize,
    pub players: usize,
    /// Integrity findings that make the save unusable (empty = valid).
    pub problems: Vec<String>,
}

#[derive(Debug, thiserror::Error)]
pub enum InspectError {
    #[error("the save cannot be read: {0}")]
    Read(#[from] io::Error),
    #[error("this is not a supported PES/Football Life edit file: {0}")]
    Decrypt(#[from] fl_save::crypto::CryptoError),
    #[error("the decrypted save is damaged: {0}")]
    Damaged(#[from] fl_save::edit_file::EditFileError),
}

/// Decrypt `path` in memory and summarize it. Read-only.
pub fn inspect_save(path: &Path) -> Result<SaveInfo, InspectError> {
    let bytes = fs::read(path)?;
    let blocks = fl_save::crypto::decrypt(&bytes)?;
    let header = blocks.save_header();
    let edit = fl_save::edit_file::EditFile::from_bytes(blocks.data)?;
    let report = edit.validate_integrity();
    Ok(SaveInfo {
        kind: if header.is_pes21() {
            SaveKind::Pes2021
        } else {
            SaveKind::FootballLife26
        },
        game_version: header.game_version,
        teams: edit.teams().len(),
        clubs: edit.club_team_ids().len(),
        players: usize::from(edit.counts().players),
        problems: report.error_messages(),
    })
}

// ── game folder ───────────────────────────────────────────────────────────

/// What a chosen game folder offers.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GameFolder {
    /// Database archives in precedence order (`data_extra.cpk` first).
    pub archives: Vec<PathBuf>,
}

impl GameFolder {
    pub fn is_usable(&self) -> bool {
        !self.archives.is_empty()
    }
}

/// Look for `download/*.cpk` below `folder`.
pub fn check_game_folder(folder: &Path) -> GameFolder {
    GameFolder {
        archives: discover::find_game_cpks(folder),
    }
}

/// The game installation found through the usual environment variables and
/// install locations, if any.
pub fn detect_game_folder() -> Option<PathBuf> {
    detect_game_folder_with(&process_env)
}

pub fn detect_game_folder_with(env: Env<'_>) -> Option<PathBuf> {
    let first = discover::discover_game_cpks_with(None, env)
        .into_iter()
        .next()?;
    let download = first.parent()?;
    let root = if download
        .file_name()
        .is_some_and(|name| name.eq_ignore_ascii_case("download"))
    {
        download.parent()?
    } else {
        download
    };
    Some(root.to_path_buf())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    fn touch(path: &Path) {
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, b"x").unwrap();
    }

    fn env_of(pairs: &[(&str, &Path)]) -> impl Fn(&str) -> Option<OsString> + use<> {
        let map: HashMap<String, OsString> = pairs
            .iter()
            .map(|(k, v)| ((*k).to_owned(), v.as_os_str().to_owned()))
            .collect();
        move |name| map.get(name).cloned()
    }

    #[test]
    fn discovers_fl26_and_vanilla_saves_including_onedrive_without_writing() {
        let home = tempfile::tempdir().unwrap();
        let onedrive = tempfile::tempdir().unwrap();
        let konami = "Documents/KONAMI/eFootball PES 2021 SEASON UPDATE";
        touch(
            &home
                .path()
                .join(konami)
                .join("2026/save")
                .join(SAVE_FILE_NAME),
        );
        touch(
            &home
                .path()
                .join(konami)
                .join("0123/save")
                .join(SAVE_FILE_NAME),
        );
        touch(
            &onedrive
                .path()
                .join(konami)
                .join("0999/save")
                .join(SAVE_FILE_NAME),
        );
        touch(
            &onedrive
                .path()
                .join(konami)
                .join("abc/save")
                .join(SAVE_FILE_NAME),
        );
        // A save folder without the file is not a save.
        fs::create_dir_all(home.path().join(konami).join("0555/save")).unwrap();

        let before: Vec<_> = walk(home.path());
        let env = env_of(&[
            ("USERPROFILE", home.path()),
            ("OneDrive", onedrive.path()),
            ("OneDriveConsumer", onedrive.path()),
        ]);
        let found = discover_saves_with(&env);
        assert_eq!(walk(home.path()), before, "discovery must not write");

        let summary: Vec<_> = found
            .iter()
            .map(|c| (c.family, c.edit_file.to_string_lossy().replace('\\', "/")))
            .collect();
        assert_eq!(summary.len(), 3);
        assert_eq!(summary[0].0, SaveFamily::FootballLife26);
        assert!(summary[0].1.ends_with("2026/save/EDIT00000000"));
        assert_eq!(summary[1].0, SaveFamily::Pes2021);
        assert!(summary[1].1.ends_with("0123/save/EDIT00000000"));
        assert!(summary[2].1.ends_with("0999/save/EDIT00000000"));
    }

    fn walk(root: &Path) -> Vec<PathBuf> {
        let mut out = Vec::new();
        let mut stack = vec![root.to_path_buf()];
        while let Some(dir) = stack.pop() {
            for entry in fs::read_dir(dir).unwrap().flatten() {
                out.push(entry.path());
                if entry.path().is_dir() {
                    stack.push(entry.path());
                }
            }
        }
        out.sort();
        out
    }

    #[test]
    fn state_dir_follows_platform_conventions() {
        let home = Path::new("/home/me");
        let env = env_of(&[("HOME", home), ("LOCALAPPDATA", Path::new("C:/Local"))]);
        let dir = state_dir_with(&env).unwrap();
        assert!(dir.ends_with("FLDailyEdit"));
        assert!(state_dir_with(&|_| None).is_none());
    }

    #[test]
    fn resolve_accepts_file_or_folder_with_any_name_and_location() {
        let dir = tempfile::tempdir().unwrap();
        let save = dir.path().join("T99").join(SAVE_FILE_NAME);
        touch(&save);
        let copy = dir.path().join("EDIT00000000 (copy)");
        touch(&copy);
        assert_eq!(resolve_save_file(&save, None).unwrap(), save);
        assert_eq!(
            resolve_save_file(save.parent().unwrap(), None).unwrap(),
            save
        );
        assert_eq!(resolve_save_file(&copy, None).unwrap(), copy);
        assert!(matches!(
            resolve_save_file(&dir.path().join("nope"), None),
            Err(SaveFileError::Missing(_))
        ));
        assert!(matches!(
            resolve_save_file(dir.path(), None),
            Err(SaveFileError::Missing(_))
        ));
    }

    #[test]
    fn game_root_is_rejected_whatever_the_folder_is_called() {
        let dir = tempfile::tempdir().unwrap();
        let game = dir.path().join("PES 2021");
        touch(&game.join("download/data_extra.cpk"));
        let inside = game.join("my backups").join(SAVE_FILE_NAME);
        touch(&inside);
        assert!(matches!(
            resolve_save_file(&inside, Some(&game)),
            Err(SaveFileError::InsideGame(_))
        ));
        // Even without the selected root: the folder holds download/*.cpk.
        assert!(matches!(
            resolve_save_file(&inside, None),
            Err(SaveFileError::InsideGame(_))
        ));
        // A user folder that merely has a `download` directory is fine.
        let user = dir.path().join("Documents");
        fs::create_dir_all(user.join("download")).unwrap();
        let ok = user.join("save").join(SAVE_FILE_NAME);
        touch(&ok);
        assert_eq!(resolve_save_file(&ok, Some(&game)).unwrap(), ok);
    }

    #[cfg(unix)]
    #[test]
    fn links_are_refused() {
        let dir = tempfile::tempdir().unwrap();
        let real = dir.path().join("real");
        touch(&real);
        let link = dir.path().join("link");
        std::os::unix::fs::symlink(&real, &link).unwrap();
        assert!(matches!(
            resolve_save_file(&link, None),
            Err(SaveFileError::Link(_))
        ));
    }

    #[test]
    fn game_folder_detection_finds_install_root() {
        let dir = tempfile::tempdir().unwrap();
        let game = dir.path().join("SP Football Life 2026");
        touch(&game.join("download/data_extra.cpk"));
        touch(&game.join("download/data_s2526.cpk"));
        let check = check_game_folder(&game);
        assert!(check.is_usable());
        assert_eq!(check.archives.len(), 2);
        assert!(!check_game_folder(dir.path()).is_usable());

        let env = env_of(&[("PROGRAMFILES", dir.path())]);
        assert_eq!(
            detect_game_folder_with(&env).as_deref(),
            Some(game.as_path())
        );
        assert_eq!(detect_game_folder_with(&|_| None), None);
    }

    #[test]
    fn inspect_rejects_garbage() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join(SAVE_FILE_NAME);
        fs::write(&path, b"not a save").unwrap();
        assert!(matches!(inspect_save(&path), Err(InspectError::Decrypt(_))));
        assert!(matches!(
            inspect_save(&dir.path().join("missing")),
            Err(InspectError::Read(_))
        ));
    }
}
