//! The operating-system services the screens use, behind a trait so tests
//! can answer pickers and observe "open" requests.

use std::path::{Path, PathBuf};

pub trait Platform: Send + Sync {
    /// Let the user choose the save file.
    fn pick_save(&self) -> Option<PathBuf>;
    /// Let the user choose a folder.
    fn pick_folder(&self) -> Option<PathBuf>;
    /// Open a file with its default program (or a folder in the file manager).
    fn open(&self, path: &Path) -> Result<(), String>;
    /// Show a file selected in the file manager.
    fn reveal(&self, path: &Path) -> Result<(), String>;
}

pub struct Native;

impl Platform for Native {
    fn pick_save(&self) -> Option<PathBuf> {
        rfd::FileDialog::new()
            .set_title("Choose your save file (EDIT00000000)")
            .pick_file()
    }

    fn pick_folder(&self) -> Option<PathBuf> {
        rfd::FileDialog::new()
            .set_title("Choose the game folder")
            .pick_folder()
    }

    fn open(&self, path: &Path) -> Result<(), String> {
        opener::open(path).map_err(|error| error.to_string())
    }

    fn reveal(&self, path: &Path) -> Result<(), String> {
        opener::reveal(path).map_err(|error| error.to_string())
    }
}
