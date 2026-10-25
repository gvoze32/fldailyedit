//! User-facing wording for failures, keyed on the engine's stable error code.

use fl_engine::api::{ErrorCode, Stage};

/// What to tell the user when an update fails.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ErrorCopy {
    pub title: &'static str,
    /// Plain-language cause.
    pub explanation: &'static str,
    /// What to try, most useful first.
    pub next_steps: &'static [&'static str],
    /// The failure is fixed by choosing the game folder.
    pub needs_game_folder: bool,
    /// The save was not touched (always true for failures before the commit).
    pub save_untouched: bool,
}

pub fn error_copy(code: ErrorCode, stage: Option<Stage>) -> ErrorCopy {
    let before_commit = !matches!(stage, Some(Stage::Committing | Stage::Complete));
    match code {
        ErrorCode::Cancelled => ErrorCopy {
            title: "Update cancelled",
            explanation: "You stopped the update before anything was written.",
            next_steps: &[],
            needs_game_folder: false,
            save_untouched: true,
        },
        ErrorCode::SaveNotFound => ErrorCopy {
            title: "Save file not found",
            explanation: "The save file could not be opened. It may have been moved, renamed or deleted.",
            next_steps: &[
                "Choose your EDIT00000000 file again.",
                "Make sure the drive or OneDrive folder holding it is available.",
            ],
            needs_game_folder: false,
            save_untouched: true,
        },
        ErrorCode::SaveLocked => ErrorCopy {
            title: "The save is in use",
            explanation: "Another program, usually the game, has the save open.",
            next_steps: &[
                "Close the game completely (check the system tray and Task Manager).",
                "Wait for OneDrive to finish syncing, then try again.",
            ],
            needs_game_folder: false,
            save_untouched: true,
        },
        ErrorCode::InvalidSave => ErrorCopy {
            title: "This save isn't supported",
            explanation: "The file is not a valid Football Life 2026 / PES 2021 save, or it is damaged.",
            next_steps: &[
                "Make sure you chose EDIT00000000, not another file.",
                "Try a recent backup of the save.",
                "Open the game once and save again, then retry.",
            ],
            needs_game_folder: false,
            save_untouched: true,
        },
        ErrorCode::NativeDatabaseMissing => ErrorCopy {
            title: "Game folder needed",
            explanation: "This save needs the game's own player database, which was not found.",
            next_steps: &[
                "Choose the game folder that contains the download folder (for example D:\\Games\\PES 2021).",
                "The folder must hold data_extra.cpk or data_s2526.cpk.",
            ],
            needs_game_folder: true,
            save_untouched: true,
        },
        ErrorCode::FetchFailed => ErrorCopy {
            title: "Couldn't download transfers",
            explanation: "The transfer sources could not be reached or returned unusable data.",
            next_steps: &[
                "Check your internet connection.",
                "Try again in a few minutes; the sources may be busy.",
            ],
            needs_game_folder: false,
            save_untouched: true,
        },
        ErrorCode::MatchingFailed => ErrorCopy {
            title: "Couldn't match players to your save",
            explanation: "The transfers could not be matched to the clubs and players in this save.",
            next_steps: &[
                "Use Copy diagnostics and share it with the project.",
                "Check that the save belongs to the game you selected.",
            ],
            needs_game_folder: false,
            save_untouched: true,
        },
        ErrorCode::ApplyFailed => ErrorCopy {
            title: "Couldn't apply the changes",
            explanation: "The planned changes could not be applied safely, so nothing was written.",
            next_steps: &[
                "Try again.",
                "If it keeps failing, use Copy diagnostics and share it with the project.",
            ],
            needs_game_folder: false,
            save_untouched: true,
        },
        ErrorCode::VerificationFailed => ErrorCopy {
            title: "The result failed its safety check",
            explanation: "The updated save did not pass verification, so it was discarded and your save was left as it was.",
            next_steps: &[
                "Try again.",
                "If it keeps failing, use Copy diagnostics and share it with the project.",
            ],
            needs_game_folder: false,
            save_untouched: true,
        },
        ErrorCode::InputChanged => ErrorCopy {
            title: "The save changed while updating",
            explanation: "The save file was modified by something else during the update, so nothing was written.",
            next_steps: &[
                "Close the game and any sync or backup tool touching the save.",
                "Run the update again.",
            ],
            needs_game_folder: false,
            save_untouched: true,
        },
        ErrorCode::WriteFailed => ErrorCopy {
            title: "Couldn't write the save",
            explanation: "The updated save could not be written to disk.",
            next_steps: &[
                "Close the game and check that the folder is writable and has free space.",
                "Your backup (if one was made) is listed in the details; restore it if the save looks wrong.",
            ],
            needs_game_folder: false,
            save_untouched: before_commit,
        },
        ErrorCode::Internal => ErrorCopy {
            title: "Something went wrong",
            explanation: "The update stopped because of an unexpected problem.",
            next_steps: &[
                "Try again.",
                "If it keeps failing, use Copy diagnostics and share it with the project.",
            ],
            needs_game_folder: false,
            save_untouched: before_commit,
        },
    }
}

/// Setup-screen wording for the stage names.
pub fn stage_label(stage: Stage) -> &'static str {
    match stage {
        Stage::Validating => "Checking your save",
        Stage::Fetching => "Fetching transfers",
        Stage::Matching => "Matching players",
        Stage::Applying => "Preparing changes",
        Stage::Verifying => "Verifying the result",
        Stage::Committing => "Writing your save safely",
        Stage::Complete => "Finished",
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_code_has_copy_and_missing_database_points_to_game_folder() {
        use ErrorCode::*;
        for code in [
            Cancelled,
            SaveNotFound,
            SaveLocked,
            InvalidSave,
            NativeDatabaseMissing,
            FetchFailed,
            MatchingFailed,
            ApplyFailed,
            VerificationFailed,
            InputChanged,
            WriteFailed,
            Internal,
        ] {
            let copy = error_copy(code, None);
            assert!(!copy.title.is_empty() && !copy.explanation.is_empty());
            assert_eq!(copy.needs_game_folder, code == NativeDatabaseMissing);
        }
        assert!(error_copy(ErrorCode::WriteFailed, Some(Stage::Fetching)).save_untouched);
        assert!(!error_copy(ErrorCode::WriteFailed, Some(Stage::Committing)).save_untouched);
    }
}
