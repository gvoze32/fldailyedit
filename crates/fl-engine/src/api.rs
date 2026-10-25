//! Public contract between the update engine and its front ends (CLI, GUI).
//!
//! The engine runs one update through fixed stages:
//! validate → fetch → match → apply (in memory) → verify → commit.
//! Cancellation is honored at every point before commit; once the commit
//! starts the save is written atomically and cancellation is ignored.

use std::path::PathBuf;
use std::sync::atomic::{AtomicU8, Ordering};

/// What to update and how.
#[derive(Debug, Clone)]
pub struct UpdateRequest {
    /// Save to read (`EDIT00000000`).
    pub edit_path: PathBuf,
    /// Where to write the result; `None` updates `edit_path` in place.
    pub output_path: Option<PathBuf>,
    /// PES 2021 / T99 installation (folder with `download/*.cpk`) or a folder
    /// holding the patch CPKs. Required for non-FL26 saves.
    pub game_root: Option<PathBuf>,
    /// Transfer window selection.
    pub window: Window,
    /// Restrict the run to one club (FotMob id or club name). Debug aid.
    pub club: Option<String>,
    /// Minimum player-name match confidence (0-100). `None` = default (80).
    pub threshold: Option<f64>,
    /// Release a squad player when the destination roster is full.
    pub allow_overflow_release: bool,
    /// Optional JSON protected-player / usage policy for overflow releases.
    pub release_policy: Option<PathBuf>,
    /// Plan and report without writing anything (no save, log, cache, backup).
    pub dry_run: bool,
}

impl UpdateRequest {
    pub fn target_path(&self) -> &std::path::Path {
        self.output_path.as_deref().unwrap_or(&self.edit_path)
    }
}

/// Per-user locations the engine may write to: caches (FotMob payloads,
/// learned club identities), the transfer log, reports and backups.
#[derive(Debug, Clone)]
pub struct Environment {
    /// `%LOCALAPPDATA%\FLDailyEdit` on Windows,
    /// `~/Library/Application Support/FLDailyEdit` on macOS,
    /// `$XDG_DATA_HOME/FLDailyEdit` elsewhere.
    pub state_dir: PathBuf,
}

/// Transfer window selection.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub enum Window {
    /// Continue from the save's last applied update (default).
    #[default]
    Auto,
    /// Replay all available transfer history.
    All,
    /// The current/most recent summer window (Jun 1 - Sep 30).
    Summer,
    /// The current/most recent winter window (Jan 1 - Feb 28/29).
    Winter,
    /// Events on or after this date.
    Since(chrono::NaiveDate),
}

/// Pipeline stage, in execution order.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum Stage {
    Validating,
    Fetching,
    Matching,
    Applying,
    Verifying,
    Committing,
    Complete,
}

impl Stage {
    pub const ALL: [Stage; 7] = [
        Stage::Validating,
        Stage::Fetching,
        Stage::Matching,
        Stage::Applying,
        Stage::Verifying,
        Stage::Committing,
        Stage::Complete,
    ];
}

/// Progress event. `current`/`total` are set when a stage knows its size
/// (e.g. clubs fetched); `message` is a short human-readable line suitable for
/// a log pane.
#[derive(Debug, Clone)]
pub struct Progress {
    pub stage: Stage,
    pub message: String,
    pub current: u32,
    pub total: u32,
}

/// Outcome of a successful run (including "nothing to change" and dry runs).
#[derive(Debug, Clone, Default)]
pub struct UpdateResult {
    pub target_path: PathBuf,
    pub outcome: Outcome,
    pub backup_path: Option<PathBuf>,
    /// SHA-256 (hex) of the written save.
    pub written_sha256: Option<String>,
    pub counts: ChangeCounts,
    /// Markdown transfer report for this run.
    pub report_markdown: String,
    /// Report files written for this run (markdown/html), if any.
    pub report_paths: Vec<PathBuf>,
    /// Events that were not applied, save-relevant rows first.
    pub skipped: Vec<SkippedTransfer>,
    /// Non-blocking warnings worth showing to the user.
    pub warnings: Vec<String>,
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub enum Outcome {
    /// The save was written.
    Updated,
    /// Nothing to change; the save was not touched.
    #[default]
    NoChanges,
    /// Dry run: changes were planned, nothing was written.
    Preview,
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct ChangeCounts {
    pub transfers: u32,
    pub shirt_numbers: u32,
    pub captains: u32,
    pub formations: u32,
    pub tactics: u32,
    pub unchanged: u32,
    pub safety_skipped: u32,
}

/// A transfer event that was not applied, with a stable reason code.
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct SkippedTransfer {
    pub player: String,
    pub from_club: String,
    pub to_club: String,
    pub date: Option<String>,
    pub transfer_type: String,
    pub reason: String,
    pub detail: String,
    /// Touches a club in the selected save.
    pub relevant: bool,
}

/// Stable failure codes for front-end copy.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ErrorCode {
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
}

#[derive(Debug, Clone, thiserror::Error)]
#[error("{message}")]
pub struct UpdateError {
    pub code: ErrorCode,
    pub stage: Option<Stage>,
    pub message: String,
}

/// Cancellation handoff shared between a front end and a running update.
/// Requests are ignored once the commit has started. A request also aborts
/// in-flight HTTP requests and back-off waits of the fetch stage.
#[derive(Debug, Default)]
pub struct CancelToken {
    state: AtomicU8,
    transport: fl_sources::http::CancelToken,
}

const IDLE: u8 = 0;
const REQUESTED: u8 = 1;
const COMMITTING: u8 = 2;

impl CancelToken {
    pub fn new() -> Self {
        Self::default()
    }

    /// Ask the update to stop. Returns `false` if the commit already started.
    pub fn request(&self) -> bool {
        let accepted = self
            .state
            .compare_exchange(IDLE, REQUESTED, Ordering::AcqRel, Ordering::Acquire)
            .map_or_else(|state| state == REQUESTED, |_| true);
        if accepted {
            self.transport.cancel();
        }
        accepted
    }

    pub fn is_requested(&self) -> bool {
        self.state.load(Ordering::Acquire) == REQUESTED
    }

    /// Freeze the token: after this, `request` has no effect.
    /// Returns `false` if cancellation was requested first.
    pub fn begin_commit(&self) -> bool {
        self.state
            .compare_exchange(IDLE, COMMITTING, Ordering::AcqRel, Ordering::Acquire)
            .is_ok()
    }

    pub fn commit_started(&self) -> bool {
        self.state.load(Ordering::Acquire) == COMMITTING
    }

    pub fn check(&self, stage: Stage) -> Result<(), UpdateError> {
        if self.is_requested() {
            return Err(UpdateError {
                code: ErrorCode::Cancelled,
                stage: Some(stage),
                message: "Update cancelled".into(),
            });
        }
        Ok(())
    }

    /// The token the HTTP client of the fetch stage observes.
    pub(crate) fn transport(&self) -> &fl_sources::http::CancelToken {
        &self.transport
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cancel_is_ignored_after_commit_starts() {
        let token = CancelToken::new();
        assert!(token.begin_commit());
        assert!(!token.request());
        assert!(token.check(Stage::Committing).is_ok());
    }

    #[test]
    fn commit_is_refused_after_cancel() {
        let token = CancelToken::new();
        assert!(token.request());
        assert!(token.request());
        assert!(!token.begin_commit());
        assert_eq!(
            token.check(Stage::Fetching).unwrap_err().code,
            ErrorCode::Cancelled
        );
    }
}
