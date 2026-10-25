//! Where the engine keeps its per-user state, relative to `Environment::state_dir`.

use std::path::{Path, PathBuf};

use sha2::{Digest, Sha256};

/// Layout of the state directory.
#[derive(Debug, Clone)]
pub struct StatePaths {
    root: PathBuf,
}

/// Stable short key of a save target (its resolved path): keeps the reports
/// and backups of different saves apart.
pub fn scope_key(save_scope: &str) -> String {
    let mut key = format!("{:x}", Sha256::digest(save_scope.as_bytes()));
    key.truncate(12);
    key
}

impl StatePaths {
    pub fn new(root: impl Into<PathBuf>) -> Self {
        Self { root: root.into() }
    }

    pub fn root(&self) -> &Path {
        &self.root
    }

    /// Append-only audit log of applied changes (all saves; records carry their scope).
    pub fn transfer_log(&self) -> PathBuf {
        self.root.join("transfer_log.jsonl")
    }

    /// Learned FotMob ↔ save club bindings (all saves; keyed by scope inside).
    pub fn club_identity_cache(&self) -> PathBuf {
        self.root.join("club_identity_cache.json")
    }

    /// FotMob per-team payload cache directory.
    pub fn team_cache_dir(&self) -> PathBuf {
        self.root.join("storage").join("fotmob_team_cache")
    }

    /// FotMob team list (club catalog) downloaded from FotMob's sitemaps.
    pub fn team_index(&self) -> PathBuf {
        self.root.join("storage").join("fotmob_teams.json")
    }

    /// FotMob league-tactics cache file.
    pub fn tactics_cache(&self) -> PathBuf {
        self.root.join("storage").join("fotmob_tactics_cache.json")
    }

    /// Reports of the latest run for one save.
    pub fn reports_dir(&self, save_scope: &str) -> PathBuf {
        self.root.join("reports").join(scope_key(save_scope))
    }

    /// Backups of one save.
    pub fn backups_dir(&self, save_scope: &str) -> PathBuf {
        self.root.join("backups").join(scope_key(save_scope))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn keys_are_stable_and_distinct() {
        assert_eq!(scope_key("/a/EDIT"), scope_key("/a/EDIT"));
        assert_ne!(scope_key("/a/EDIT"), scope_key("/b/EDIT"));
        assert_eq!(scope_key("x").len(), 12);
    }

    #[test]
    fn paths_live_under_the_state_dir() {
        let paths = StatePaths::new("/state");
        assert_eq!(paths.transfer_log(), Path::new("/state/transfer_log.jsonl"));
        assert_eq!(
            paths.reports_dir("s").parent().unwrap(),
            Path::new("/state/reports")
        );
    }
}
