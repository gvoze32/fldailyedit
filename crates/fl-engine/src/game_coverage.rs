//! Does the chosen game folder know the players of the chosen save?
//!
//! A save's rosters only hold player ids; names, ages and ratings come from the
//! game's `Player.bin`. Several archives can carry one (`data_extra.cpk` is the
//! newest, `data_s2526.cpk` the older base), and a save made on the newer game
//! lists players the older one has never heard of. This module scores every
//! archive against the save and words the result for people.

use std::fs;
use std::path::{Path, PathBuf};

use fl_save::catalog::with_separators;
use fl_save::cpk::discover;
use fl_save::native::{PlayerBinScan, scan_player_bins};

use crate::locations::InspectError;

/// File name of the newest game data archive.
const DATA_EXTRA: &str = "data_extra.cpk";

/// One game archive with a `Player.bin`, and how many of the save's players it lacks.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ArchiveCoverage {
    pub archive: PathBuf,
    pub missing: usize,
}

impl ArchiveCoverage {
    fn name(&self) -> String {
        self.archive.file_name().map_or_else(
            || self.archive.display().to_string(),
            |name| name.to_string_lossy().into_owned(),
        )
    }
}

/// How well a game folder covers the players a save needs from `Player.bin`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GameCoverage {
    /// The folder the archives were looked for in.
    pub download: PathBuf,
    /// Whether `data_extra.cpk` exists there.
    pub data_extra_found: bool,
    /// Archives that hold a `Player.bin`, in the order the app prefers them.
    pub archives: Vec<ArchiveCoverage>,
    /// Index into `archives` of the one the app uses (fewest missing players).
    pub chosen: Option<usize>,
}

impl GameCoverage {
    /// Summarize a scan of `game_root`.
    pub fn from_scan(game_root: &Path, scan: &PlayerBinScan) -> Self {
        let download = discover::download_folder(game_root);
        Self {
            data_extra_found: download.join(DATA_EXTRA).is_file(),
            download,
            archives: scan
                .candidates
                .iter()
                .map(|candidate| ArchiveCoverage {
                    archive: candidate.cpk.clone(),
                    missing: candidate.missing,
                })
                .collect(),
            chosen: scan.best(),
        }
    }

    /// The archive the app would read `Player.bin` from.
    pub fn chosen_archive(&self) -> Option<&ArchiveCoverage> {
        self.chosen.and_then(|index| self.archives.get(index))
    }

    /// True when the chosen archive knows every player the save needs.
    pub fn is_complete(&self) -> bool {
        self.chosen_archive()
            .is_some_and(|archive| archive.missing == 0)
    }

    /// Plain-words explanation of why the game folder does not fit the save;
    /// empty when it does.
    pub fn problem(&self) -> String {
        if self.is_complete() {
            return String::new();
        }
        let download = self.download.display();
        let mut text = match self.chosen_archive() {
            None => format!("No game file in {download} holds the player list (Player.bin)."),
            Some(best) => {
                let per_archive: Vec<String> = self
                    .archives
                    .iter()
                    .map(|archive| {
                        format!(
                            "{} is missing {}",
                            archive.name(),
                            with_separators(archive.missing)
                        )
                    })
                    .collect();
                format!(
                    "The game files do not match this save: {} players on its rosters are not in \
                     the game's player list. Checked {download}: {}.",
                    with_separators(best.missing),
                    per_archive.join("; ")
                )
            }
        };
        if self.data_extra_found {
            text.push_str(" Choose the folder of the game this save was made with.");
        } else {
            text.push_str(&format!(
                " {DATA_EXTRA} was not found in {download}. Install the latest Football Life \
                 update there, or choose the folder of the game you actually play."
            ));
        }
        text
    }
}

/// Score the game folder `game_root` against the save file at `save`.
/// Read-only; decrypts the save in memory and reads each archive's `Player.bin`.
pub fn check_game_coverage(save: &Path, game_root: &Path) -> Result<GameCoverage, InspectError> {
    let bytes = fs::read(save)?;
    let blocks = fl_save::crypto::decrypt(&bytes)?;
    let edit = fl_save::edit_file::EditFile::from_bytes(blocks.data)?;
    let scan = scan_player_bins(game_root, &edit.roster_ids_needing_native());
    Ok(GameCoverage::from_scan(game_root, &scan))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn coverage(missing: &[usize], data_extra_found: bool) -> GameCoverage {
        let archives: Vec<ArchiveCoverage> = missing
            .iter()
            .enumerate()
            .map(|(index, &missing)| ArchiveCoverage {
                archive: PathBuf::from(format!("/g/download/pack{index}.cpk")),
                missing,
            })
            .collect();
        GameCoverage {
            download: PathBuf::from("/g/download"),
            data_extra_found,
            chosen: fl_save::native::best_by_coverage(archives.iter().map(|a| a.missing)),
            archives,
        }
    }

    #[test]
    fn a_complete_archive_has_no_problem() {
        let covered = coverage(&[1540, 0], true);
        assert!(covered.is_complete());
        assert_eq!(covered.chosen, Some(1));
        assert!(covered.problem().is_empty());
    }

    #[test]
    fn the_problem_names_each_archive_and_the_missing_update() {
        let text = coverage(&[1540], false).problem();
        assert!(text.contains("1,540 players"), "{text}");
        assert!(text.contains("pack0.cpk is missing 1,540"), "{text}");
        assert!(text.contains("/g/download"), "{text}");
        assert!(text.contains("data_extra.cpk was not found in"), "{text}");
        assert!(
            text.contains("Install the latest Football Life update"),
            "{text}"
        );

        let text = coverage(&[30, 12], true).problem();
        assert!(text.contains("12 players"), "{text}");
        assert!(!text.contains("was not found"), "{text}");
        assert!(text.contains("this save was made with"), "{text}");
    }

    #[test]
    fn a_folder_without_any_player_list_says_so() {
        let text = coverage(&[], false).problem();
        assert!(text.contains("holds the player list"), "{text}");
        assert!(text.contains("data_extra.cpk was not found"), "{text}");
    }
}

#[cfg(test)]
mod real_data_tests {
    use super::*;

    /// The base save needs players only `data_extra.cpk` carries
    /// (MEMORY.md: `reference/Player.bin` does not cover it).
    #[test]
    fn the_older_archive_alone_does_not_cover_the_base_save() {
        let Some(save) = crate::test_support::base_save_path() else {
            return;
        };
        let older = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../reference/data_s2526.cpk");
        if !older.is_file() {
            eprintln!("skipping: {} not present", older.display());
            return;
        }
        let game = tempfile::tempdir().unwrap();
        let download = game.path().join("download");
        fs::create_dir_all(&download).unwrap();
        fs::copy(&older, download.join("data_s2526.cpk")).unwrap();

        let coverage = check_game_coverage(&save, game.path()).unwrap();
        assert!(!coverage.is_complete());
        assert!(!coverage.data_extra_found);
        assert_eq!(coverage.archives.len(), 1);
        assert!(coverage.archives[0].missing > 0);
        let text = coverage.problem();
        assert!(text.contains("data_s2526.cpk is missing"), "{text}");
        assert!(text.contains("data_extra.cpk was not found"), "{text}");
    }
}
