//! Stage 6: backup → atomic encrypted write → audit log → reports.
//!
//! The save is the only thing that must succeed: once it is written, a
//! failing log or report becomes a warning, never an error.

use std::path::{Path, PathBuf};

use chrono::{DateTime, Utc};
use fl_save::crypto;

use super::prepare::{Prepared, fail, sha256_file};
use crate::api::{CancelToken, ErrorCode, Stage, UpdateError, UpdateParts};
use crate::backup;
use crate::log::{LogRecord, TransferLog, isoformat_utc};
use crate::paths::StatePaths;
use crate::report::{self, LeagueMove, SkippedRow};

/// What the commit stage produced.
#[derive(Debug, Default)]
pub(crate) struct Committed {
    pub(crate) backup_path: Option<PathBuf>,
    pub(crate) written_sha256: Option<String>,
}

/// The file `publish` is about to overwrite: the existing separate output, or
/// the input when writing in place or to a new output.
fn backup_source(prepared: &Prepared) -> &Path {
    if !prepared.same_input_output && prepared.output.existed {
        &prepared.target
    } else {
        &prepared.edit_path
    }
}

/// Freeze cancellation, back up the file about to be replaced, and write the
/// modified save atomically. Nothing was written when this returns `Err`.
pub(crate) fn write_save(
    prepared: &mut Prepared,
    paths: &StatePaths,
    now: DateTime<Utc>,
    cancel: &CancelToken,
) -> Result<Committed, UpdateError> {
    let stage = Stage::Committing;
    cancel.check(stage)?;
    if !cancel.begin_commit() {
        // A cancel request raced the commit: it wins, nothing is written yet.
        cancel.check(stage)?;
    }
    let backup_path =
        backup::create_backup(paths.root(), &prepared.scope, backup_source(prepared), now)
            .map_err(|error| {
                fail(
                    ErrorCode::WriteFailed,
                    stage,
                    format!("Backup failed: {error}"),
                )
            })?;

    if let Some(parent) = prepared
        .target
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
    {
        std::fs::create_dir_all(parent).map_err(|error| {
            fail(
                ErrorCode::WriteFailed,
                stage,
                format!("Could not create {}: {error}", parent.display()),
            )
        })?;
    }
    prepared.blocks.data = prepared.edit.bytes().to_vec();
    crypto::encrypt_to_file(&prepared.blocks, &prepared.target).map_err(|error| {
        fail(
            ErrorCode::WriteFailed,
            stage,
            format!("Could not publish verified save: {error}"),
        )
    })?;
    let written_sha256 = sha256_file(&prepared.target).ok();
    Ok(Committed {
        backup_path: Some(backup_path),
        written_sha256,
    })
}

/// Records as they are appended to the log: stamped, scoped, confidence
/// rounded to the log precision.
pub(crate) fn log_records(
    entries: &[LogRecord],
    scope: &str,
    now: DateTime<Utc>,
) -> Vec<LogRecord> {
    let timestamp = isoformat_utc(now);
    entries
        .iter()
        .map(|entry| LogRecord {
            timestamp: timestamp.clone(),
            confidence: LogRecord::round_confidence(entry.confidence),
            save_scope: scope.to_owned(),
            ..entry.clone()
        })
        .collect()
}

/// Append the audit log and write the reports. Failures are returned as
/// warnings: the save itself is already safely written. Returns the report
/// files written (empty when writing them failed).
pub(crate) fn publish_records(
    paths: &StatePaths,
    scope: &str,
    entries: &[LogRecord],
    skipped: &[SkippedRow],
    now: DateTime<Utc>,
    parts: &UpdateParts,
    league_moves: &[LeagueMove],
) -> (Vec<PathBuf>, Vec<String>) {
    let mut warnings = Vec::new();
    let log = TransferLog::new(paths.transfer_log());
    if let Err(error) = log.append(&log_records(entries, scope, now)) {
        warnings.push(format!(
            "Save published, but the transfer log could not be updated: {error}"
        ));
    }
    let reports = write_reports(paths, scope, entries, skipped, now, parts, league_moves)
        .unwrap_or_else(|error| {
            warnings.push(format!(
                "Save published, but report generation failed: {error}"
            ));
            Vec::new()
        });
    (reports, warnings)
}

/// Write the report files of a run (also used for runs that changed
/// nothing, so the not-applied report and the next run's window stay current).
/// A run without transfers leaves the skipped-transfer file alone: it has
/// checked no transfer, and that file seeds the next run's window.
pub(crate) fn write_reports(
    paths: &StatePaths,
    scope: &str,
    entries: &[LogRecord],
    skipped: &[SkippedRow],
    now: DateTime<Utc>,
    parts: &UpdateParts,
    league_moves: &[LeagueMove],
) -> Result<Vec<PathBuf>, report::ReportError> {
    let rows = parts.transfers.then_some(skipped);
    let note = parts_note(parts);
    let written = report::write_reports_noted(
        &paths.reports_dir(scope),
        entries,
        rows,
        league_moves,
        note.as_deref(),
        now,
    )?;
    let mut files = vec![written.markdown, written.html];
    if parts.transfers {
        files.push(written.skipped);
    }
    Ok(files)
}

/// The report line naming what a partial run updated. `None` when every part
/// the transfer reports describe ran (league moves have their own section).
fn parts_note(parts: &UpdateParts) -> Option<String> {
    let named = [
        (parts.transfers, "transfers"),
        (parts.shirt_numbers, "shirt numbers"),
        (parts.captains, "captains"),
        (parts.formations, "formations"),
        (parts.tactics, "tactics"),
        (parts.lineups, "lineups"),
        (parts.leagues, "league moves"),
    ];
    if named[..6].iter().all(|(on, _)| *on) {
        return None;
    }
    let mut ran: Vec<&str> = named
        .iter()
        .filter(|(on, _)| *on)
        .map(|(_, name)| *name)
        .collect();
    let last = ran.pop().unwrap_or("nothing");
    let list = if ran.is_empty() {
        last.to_owned()
    } else {
        format!("{} and {last}", ran.join(", "))
    };
    let unchecked = if parts.transfers {
        ""
    } else {
        " Transfers were not checked."
    };
    Some(format!(
        "Partial update: only {list} were updated.{unchecked} Formation, tactics and lineup changes are not itemised in this report."
    ))
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::TimeZone;

    fn record(confidence: f64) -> LogRecord {
        serde_json::from_value(serde_json::json!({
            "player_name": "A", "confidence": confidence
        }))
        .unwrap()
    }

    #[test]
    fn log_records_are_stamped_scoped_and_rounded() {
        let now = Utc.with_ymd_and_hms(2026, 10, 24, 22, 25, 55).unwrap();
        let rows = log_records(&[record(81.46), record(100.0)], "/s/EDIT", now);
        assert_eq!(rows[0].timestamp, "2026-10-24T22:25:55+00:00");
        assert_eq!(rows[0].confidence, 81.5);
        assert_eq!(rows[1].confidence, 100.0);
        assert_eq!(rows[0].save_scope, "/s/EDIT");
    }

    fn lineups_only() -> UpdateParts {
        UpdateParts {
            transfers: false,
            shirt_numbers: false,
            captains: false,
            leagues: false,
            ..UpdateParts::FULL
        }
    }

    #[test]
    fn the_note_names_the_parts_that_ran() {
        assert_eq!(parts_note(&UpdateParts::FULL), None);
        let without_leagues = UpdateParts {
            leagues: false,
            ..UpdateParts::FULL
        };
        assert_eq!(parts_note(&without_leagues), None);
        let note = parts_note(&lineups_only()).unwrap();
        assert!(
            note.starts_with("Partial update: only formations, tactics and lineups were updated. Transfers were not checked."),
            "{note}"
        );
    }

    #[test]
    fn a_run_without_transfers_keeps_the_skipped_transfer_file() {
        let dir = tempfile::tempdir().unwrap();
        let paths = StatePaths::new(dir.path());
        let reports = paths.reports_dir("/s/EDIT");
        std::fs::create_dir_all(&reports).unwrap();
        let skipped_file = reports.join(report::SKIPPED_TRANSFERS_FILENAME);
        std::fs::write(&skipped_file, "kept\n").unwrap();
        let now = Utc.with_ymd_and_hms(2026, 10, 24, 22, 25, 55).unwrap();

        let written =
            write_reports(&paths, "/s/EDIT", &[], &[], now, &lineups_only(), &[]).unwrap();
        assert_eq!(written.len(), 2, "no skipped-transfer file reported");
        assert_eq!(std::fs::read_to_string(&skipped_file).unwrap(), "kept\n");
        let markdown = std::fs::read_to_string(&written[0]).unwrap();
        assert!(markdown.contains("> Partial update: only"), "{markdown}");
        assert!(
            !markdown.contains("No save changes were needed"),
            "{markdown}"
        );

        let written =
            write_reports(&paths, "/s/EDIT", &[], &[], now, &UpdateParts::FULL, &[]).unwrap();
        assert_eq!(written.len(), 3);
        assert_eq!(std::fs::read_to_string(&skipped_file).unwrap(), "");
    }
}
