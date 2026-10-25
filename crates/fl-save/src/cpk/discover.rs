//! Locating the game's database archives below an installation root.

use std::ffi::OsString;
use std::path::{Path, PathBuf};

/// Archives that carry the databases, in overlay precedence order. FL's
/// `data_extra.cpk` overrides `data_s2526.cpk` in-game: its Player.bin is a
/// superset (players referenced by FL26 national-squad saves exist only there)
/// and carries the in-game abilities.
const DATABASE_ARCHIVE_NAMES: [&str; 2] = ["data_extra.cpk", "data_s2526.cpk"];
/// Further archives, matched as `*liveupd*.cpk`, `*datapack*.cpk`, `*.cpk`.
const ARCHIVE_STEM_FILTERS: [Option<&str>; 3] = [Some("liveupd"), Some("datapack"), None];

const GAME_ROOT_VARIABLES: [&str; 3] = ["FL_GAME_ROOT", "FOOTBALL_LIFE_ROOT", "PES_GAME_ROOT"];
const GAME_ROOT_PARENT_VARIABLES: [&str; 3] = ["PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"];
const GAME_ROOT_NAMES: [&str; 3] = ["SP Football Life 2026", "Football Life 2026", "PES 2021"];

fn dedup_key(path: &Path) -> String {
    path.to_string_lossy().to_lowercase()
}

fn expand_user(path: &Path) -> PathBuf {
    let mut components = path.components();
    if components
        .next()
        .is_some_and(|first| first.as_os_str() == "~")
    {
        let home = std::env::var_os("HOME").or_else(|| std::env::var_os("USERPROFILE"));
        if let Some(home) = home {
            return PathBuf::from(home).join(components.as_path());
        }
    }
    path.to_path_buf()
}

fn is_file(path: &Path) -> bool {
    path.is_file()
}

/// `name` matches the glob `*{stem}*.cpk` (or `*.cpk` for `None`); file names
/// compare case-insensitively on Windows only, like `pathlib.glob`.
fn matches_pattern(name: &str, stem: Option<&str>) -> bool {
    let fold = |text: &str| {
        if cfg!(windows) {
            text.to_ascii_lowercase()
        } else {
            text.to_owned()
        }
    };
    let name = fold(name);
    let Some(base) = name.strip_suffix(".cpk") else {
        return false;
    };
    stem.is_none_or(|stem| base.contains(stem))
}

/// Database archives below one game root in precedence order: the named
/// archives first, then liveupd, datapack and any other `.cpk` files, each
/// group sorted by case-folded path. Missing roots yield an empty list.
pub fn find_game_cpks(game_root: &Path) -> Vec<PathBuf> {
    let root = expand_user(game_root);
    let is_download = root
        .file_name()
        .is_some_and(|name| name.to_string_lossy().to_lowercase() == "download");
    let mut download = if is_download {
        root.clone()
    } else {
        root.join("download")
    };
    if !download.is_dir() && root.is_dir() {
        download = root;
    }

    let mut candidates: Vec<PathBuf> = Vec::new();
    let mut seen = std::collections::HashSet::new();
    let mut add = |candidate: PathBuf| {
        if is_file(&candidate) && seen.insert(dedup_key(&candidate)) {
            candidates.push(candidate);
        }
    };

    for name in DATABASE_ARCHIVE_NAMES {
        add(download.join(name));
    }
    let names: Vec<OsString> = std::fs::read_dir(&download)
        .map(|entries| {
            entries
                .filter_map(|entry| entry.ok().map(|entry| entry.file_name()))
                .collect()
        })
        .unwrap_or_default();
    for stem in ARCHIVE_STEM_FILTERS {
        let mut discovered: Vec<PathBuf> = names
            .iter()
            .filter(|name| matches_pattern(&name.to_string_lossy(), stem))
            .map(|name| download.join(name))
            .collect();
        discovered
            .sort_by_cached_key(|path| (dedup_key(path), path.to_string_lossy().into_owned()));
        for candidate in discovered {
            add(candidate);
        }
    }
    candidates
}

/// Candidate roots in lookup order: the given root, the game-root
/// environment variables, then the usual install parents.
fn candidate_game_roots(
    game_root: Option<&Path>,
    environment: &dyn Fn(&str) -> Option<OsString>,
) -> Vec<PathBuf> {
    let mut roots: Vec<PathBuf> = game_root.map(Path::to_path_buf).into_iter().collect();
    roots.extend(
        GAME_ROOT_VARIABLES
            .iter()
            .filter_map(|variable| environment(variable))
            .filter(|value| !value.is_empty())
            .map(PathBuf::from),
    );
    for variable in GAME_ROOT_PARENT_VARIABLES {
        let Some(parent) = environment(variable).filter(|value| !value.is_empty()) else {
            continue;
        };
        let parent = PathBuf::from(parent);
        roots.extend(GAME_ROOT_NAMES.iter().map(|name| parent.join(name)));
    }
    roots
}

/// First candidate root (see [`candidate_game_roots`]) that has database
/// archives, with `environment` supplying variables (tests inject a map).
pub fn discover_game_cpks_with(
    game_root: Option<&Path>,
    environment: &dyn Fn(&str) -> Option<OsString>,
) -> Vec<PathBuf> {
    let mut seen = std::collections::HashSet::new();
    for root in candidate_game_roots(game_root, environment) {
        if !seen.insert(dedup_key(&root)) {
            continue;
        }
        let cpks = find_game_cpks(&root);
        if !cpks.is_empty() {
            return cpks;
        }
    }
    Vec::new()
}

/// [`discover_game_cpks_with`] using the process environment.
pub fn discover_game_cpks(game_root: Option<&Path>) -> Vec<PathBuf> {
    discover_game_cpks_with(game_root, &|name| std::env::var_os(name))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    fn touch(path: &Path) {
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, b"x").unwrap();
    }

    fn names(paths: &[PathBuf]) -> Vec<String> {
        paths
            .iter()
            .map(|p| p.file_name().unwrap().to_string_lossy().into_owned())
            .collect()
    }

    #[test]
    fn data_extra_precedes_data_s2526_then_patterns() {
        let root = tempfile::tempdir().unwrap();
        let download = root.path().join("download");
        for name in [
            "z_other.cpk",
            "data_s2526.cpk",
            "a_datapack2.cpk",
            "patch_liveupd.cpk",
            "data_extra.cpk",
            "A_datapack1.cpk",
            "notes.txt",
        ] {
            touch(&download.join(name));
        }
        fs::create_dir(download.join("sub.cpk")).unwrap();
        assert_eq!(
            names(&find_game_cpks(root.path())),
            [
                "data_extra.cpk",
                "data_s2526.cpk",
                "patch_liveupd.cpk",
                "A_datapack1.cpk",
                "a_datapack2.cpk",
                "z_other.cpk"
            ]
        );
    }

    #[test]
    fn accepts_download_dir_or_flat_dir() {
        let root = tempfile::tempdir().unwrap();
        touch(&root.path().join("download").join("data_extra.cpk"));
        assert_eq!(find_game_cpks(&root.path().join("download")).len(), 1);

        let flat = tempfile::tempdir().unwrap();
        touch(&flat.path().join("data_s2526.cpk"));
        assert_eq!(names(&find_game_cpks(flat.path())), ["data_s2526.cpk"]);
        assert!(find_game_cpks(&flat.path().join("missing")).is_empty());
    }

    #[test]
    fn discovery_falls_back_to_environment_roots() {
        let empty = tempfile::tempdir().unwrap();
        let env_root = tempfile::tempdir().unwrap();
        touch(&env_root.path().join("download").join("data_extra.cpk"));
        let env_path = env_root.path().as_os_str().to_owned();
        let environment = move |name: &str| (name == "FL_GAME_ROOT").then(|| env_path.clone());

        let found = discover_game_cpks_with(Some(empty.path()), &environment);
        assert_eq!(names(&found), ["data_extra.cpk"]);
        assert!(found[0].starts_with(env_root.path()));
        assert!(discover_game_cpks_with(Some(empty.path()), &|_| None).is_empty());
    }

    #[test]
    fn explicit_root_wins_over_environment() {
        let explicit = tempfile::tempdir().unwrap();
        touch(&explicit.path().join("download").join("data_s2526.cpk"));
        let env_root = tempfile::tempdir().unwrap();
        touch(&env_root.path().join("download").join("data_extra.cpk"));
        let env_path = env_root.path().as_os_str().to_owned();
        let found = discover_game_cpks_with(Some(explicit.path()), &move |name| {
            (name == "PES_GAME_ROOT").then(|| env_path.clone())
        });
        assert_eq!(names(&found), ["data_s2526.cpk"]);
    }
}
