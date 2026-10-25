//! Last choices, remembered in `state_dir/settings.json`.

use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

const FILE_NAME: &str = "settings.json";

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default)]
pub struct Settings {
    /// The save (`EDIT00000000`) chosen last time.
    pub save_path: Option<PathBuf>,
    /// The game folder chosen last time.
    pub game_root: Option<PathBuf>,
    /// Whether the progress log pane was left open.
    pub log_open: bool,
}

impl Settings {
    /// Settings are a convenience: a missing or unreadable file means defaults.
    pub fn load(state_dir: &Path) -> Self {
        std::fs::read(state_dir.join(FILE_NAME))
            .ok()
            .and_then(|bytes| serde_json::from_slice(&bytes).ok())
            .unwrap_or_default()
    }

    pub fn save(&self, state_dir: &Path) -> std::io::Result<()> {
        std::fs::create_dir_all(state_dir)?;
        let json = serde_json::to_vec_pretty(self).map_err(std::io::Error::other)?;
        fl_fs::write_atomic(&state_dir.join(FILE_NAME), &json)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn round_trips_and_tolerates_garbage() {
        let dir = tempfile::tempdir().unwrap();
        assert_eq!(Settings::load(dir.path()), Settings::default());

        let settings = Settings {
            save_path: Some("C:/save/EDIT00000000".into()),
            game_root: Some("D:/Games/PES 2021".into()),
            log_open: true,
        };
        settings.save(&dir.path().join("nested")).unwrap();
        assert_eq!(Settings::load(&dir.path().join("nested")), settings);

        std::fs::write(dir.path().join(FILE_NAME), b"{not json").unwrap();
        assert_eq!(Settings::load(dir.path()), Settings::default());
        std::fs::write(
            dir.path().join(FILE_NAME),
            br#"{"save_path":"x","future":1}"#,
        )
        .unwrap();
        assert_eq!(Settings::load(dir.path()).save_path, Some("x".into()));
    }
}
