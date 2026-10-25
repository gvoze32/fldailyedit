//! Transfers the plan did not apply, with stable reason codes.

use std::collections::HashSet;

use chrono::NaiveDate;
use fl_sources::dates::parse_iso_datetime;
use fl_sources::model::{SquadMember, Transfer};
use serde::{Deserialize, Serialize};

use super::teams::SaveClubs;
use super::types::{MatchedTransfer, Side};

/// Source label of rows derived from live squads rather than a feed event.
pub const SNAPSHOT_SOURCE: &str = "fotmob_squad";

/// Reasons that can resolve on their own: the evidence is merely missing or
/// not yet effective, so the next run should look at the event again. Every
/// other reason is a verdict on the event itself and must not keep the scrape
/// window open forever.
const RETRYABLE_REASONS: [&str; 5] = [
    "not_yet_effective",
    "source_snapshot_missing",
    "snapshot_incomplete",
    "snapshot_coverage_low",
    "destination_roster_full",
];

/// Whether a skip with this reason code is worth retrying on a later run.
pub fn is_retryable_reason(reason: &str) -> bool {
    RETRYABLE_REASONS.contains(&reason)
}

/// One transfer the plan did not apply.
///
/// `relevant` is false only when both clubs confidently sit outside the
/// selected save, so real misses are not hidden among foreign-league noise.
/// Serializes with the established JSON keys (plus `retryable`).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SkippedTransfer {
    pub player_name: String,
    pub from_team: String,
    pub to_team: String,
    pub date: String,
    pub source: String,
    pub reason: String,
    pub detail: String,
    pub relevant: bool,
    pub fotmob_player_id: Option<i64>,
    pub candidates: Vec<String>,
    #[serde(default)]
    pub retryable: bool,
}

/// A resolved side touches the save: a save club, or possibly one.
fn touches_save(side: Side, club_ids: Option<&HashSet<u32>>) -> bool {
    match side {
        Side::Outside => false,
        Side::Unresolved => true,
        Side::Team(team_id) => club_ids.is_none_or(|ids| ids.contains(&team_id)),
    }
}

fn label(full_name: &str, short_name: &str) -> String {
    if full_name.is_empty() {
        short_name
    } else {
        full_name
    }
    .to_owned()
}

impl SkippedTransfer {
    /// Row for a matched event. `club_ids = None` treats every resolved side
    /// as touching the save.
    pub fn from_match(
        matched: &MatchedTransfer,
        reason: &str,
        detail: &str,
        candidates: &[String],
        club_ids: Option<&HashSet<u32>>,
    ) -> Self {
        let transfer = &matched.transfer;
        Self {
            player_name: transfer.player_name.clone(),
            from_team: label(&transfer.from_club_full_name, &transfer.from_club),
            to_team: label(&transfer.to_club_full_name, &transfer.to_club),
            date: transfer.date.clone(),
            source: transfer.sources.join(","),
            reason: reason.to_owned(),
            detail: detail.to_owned(),
            relevant: touches_save(matched.from, club_ids) || touches_save(matched.to, club_ids),
            fotmob_player_id: transfer.player_id_fotmob.filter(|&id| id > 0),
            candidates: candidates.to_vec(),
            retryable: is_retryable_reason(reason),
        }
    }

    /// Row for a live-squad move into a save club that was not planned.
    pub(super) fn snapshot(
        member: &SquadMember,
        fotmob_player_id: i64,
        source_name: &str,
        destination_name: &str,
        reason: &str,
        detail: &str,
        candidates: &[String],
    ) -> Self {
        Self {
            player_name: member.player_name.clone(),
            from_team: source_name.to_owned(),
            to_team: destination_name.to_owned(),
            date: String::new(),
            source: SNAPSHOT_SOURCE.to_owned(),
            reason: reason.to_owned(),
            detail: detail.to_owned(),
            relevant: true,
            fotmob_player_id: Some(fotmob_player_id),
            candidates: candidates.to_vec(),
            retryable: is_retryable_reason(reason),
        }
    }
}

/// Provider events that were never applied because of their date.
///
/// `saves = None` marks every row relevant.
pub fn pending_skipped(
    pending: &[Transfer],
    saves: Option<&SaveClubs<'_>>,
    today: NaiveDate,
) -> Vec<SkippedTransfer> {
    pending
        .iter()
        .map(|transfer| {
            let parsed = parse_iso_datetime(&transfer.date);
            let (reason, detail) = match parsed {
                None => (
                    "undated_in_window",
                    "Provider gives no date inside the bounded scrape window".to_owned(),
                ),
                Some(when) if when.date_naive() > today => (
                    "not_yet_effective",
                    format!("Effective {}; applied once it starts", when.date_naive()),
                ),
                Some(_) => (
                    "outside_scrape_window",
                    "Dated outside this run's scrape window".to_owned(),
                ),
            };
            SkippedTransfer {
                player_name: transfer.player_name.clone(),
                from_team: label(&transfer.from_club_full_name, &transfer.from_club),
                to_team: label(&transfer.to_club_full_name, &transfer.to_club),
                date: transfer.date.clone(),
                source: transfer.sources.join(","),
                reason: reason.to_owned(),
                detail,
                relevant: saves.is_none_or(|saves| saves.touches_save(transfer)),
                fotmob_player_id: transfer.player_id_fotmob.filter(|&id| id > 0),
                candidates: Vec::new(),
                retryable: is_retryable_reason(reason),
            }
        })
        .collect()
}

/// Save-relevant rows first, then by reason and date (stable).
pub fn sort_skipped(rows: &mut [SkippedTransfer]) {
    rows.sort_by(|a, b| (!a.relevant, &a.reason, &a.date).cmp(&(!b.relevant, &b.reason, &b.date)));
}
