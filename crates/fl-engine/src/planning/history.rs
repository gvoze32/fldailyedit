//! Replaying the persisted transfer log into planning state.

use std::collections::{BTreeSet, HashMap};

use chrono::{DateTime, TimeZone, Utc};
use fl_sources::dates::parse_iso_datetime;

use super::identity::positive_u32;
use super::types::HistoryEntry;

/// `parse_iso_datetime("2000-01-01")`: the key of an undated history entry.
pub(super) fn epoch_2000() -> DateTime<Utc> {
    Utc.with_ymd_and_hms(2000, 1, 1, 0, 0, 0)
        .single()
        .expect("valid date")
}

/// Players currently on loan, by their parent club.
#[derive(Debug, Clone, Default)]
pub struct LoanLedger {
    by_parent: HashMap<u32, BTreeSet<u32>>,
}

impl LoanLedger {
    pub fn lend(&mut self, parent: u32, player_id: u32) {
        self.by_parent.entry(parent).or_default().insert(player_id);
    }

    pub fn clear(&mut self, parent: u32, player_id: u32) {
        if let Some(players) = self.by_parent.get_mut(&parent) {
            players.remove(&player_id);
        }
    }

    pub fn is_loaned(&self, parent: u32, player_id: u32) -> bool {
        self.by_parent
            .get(&parent)
            .is_some_and(|players| players.contains(&player_id))
    }

    /// Players the club has out on loan, ascending.
    pub fn players(&self, parent: u32) -> impl Iterator<Item = u32> + '_ {
        self.by_parent.get(&parent).into_iter().flatten().copied()
    }

    pub fn has_loans(&self, parent: u32) -> bool {
        self.by_parent
            .get(&parent)
            .is_some_and(|players| !players.is_empty())
    }

    /// Record a lifecycle event: a loan marks the player as lent by the
    /// source; any other event clears the source's marker. A returning loan
    /// names the borrower as its source, so it also clears the *parent's*
    /// marker (the destination); otherwise the stale marker would let a
    /// later wrong-source event move the player from wherever he sits.
    pub fn record(
        &mut self,
        is_loan: bool,
        is_end_of_loan: bool,
        source: u32,
        destination: Option<u32>,
        player_id: u32,
    ) {
        if is_loan {
            self.lend(source, player_id);
            return;
        }
        self.clear(source, player_id);
        if is_end_of_loan && let Some(parent) = destination {
            self.clear(parent, player_id);
        }
    }
}

/// What the log says about players before this run's events.
#[derive(Debug, Clone, Default)]
pub struct History {
    pub loans: LoanLedger,
    /// `(player, source)` → permanent moves `(destination, when)` in log order.
    pub permanent_routes: HashMap<(u32, u32), Vec<Route>>,
    /// FotMob player id → every save player the log tied to it.
    pub identity_candidates: HashMap<i64, BTreeSet<u32>>,
    /// FotMob player id → last logged display name.
    pub identity_names: HashMap<i64, String>,
}

/// A permanent move: `(destination, when)`.
pub type Route = (u32, DateTime<Utc>);

type DatedEntry<'a> = (&'a HistoryEntry, Option<DateTime<Utc>>);

/// Replay `entries` in chronological order (undated entries sort as
/// 2000-01-01, stable).
pub fn replay_history(entries: &[HistoryEntry]) -> History {
    let mut ordered: Vec<DatedEntry<'_>> = entries
        .iter()
        .map(|entry| (entry, parse_iso_datetime(entry.when())))
        .collect();
    ordered.sort_by_key(|(_, when)| when.unwrap_or_else(epoch_2000));

    let mut history = History::default();
    for (entry, when) in ordered {
        let player_id = positive_u32(entry.player_id);
        let fotmob_player_id = entry.fotmob_player_id.filter(|&id| id > 0);
        if let (Some(player_id), Some(fotmob_player_id)) = (player_id, fotmob_player_id) {
            history
                .identity_candidates
                .entry(fotmob_player_id)
                .or_default()
                .insert(player_id);
            if !entry.player_name.is_empty() {
                history
                    .identity_names
                    .insert(fotmob_player_id, entry.player_name.clone());
            }
        }
        let (Some(player_id), Some(source)) = (player_id, positive_u32(entry.from_team_id)) else {
            continue;
        };
        let transfer_type = entry.type_lower();
        let destination = positive_u32(entry.to_team_id);
        let is_loan = transfer_type == "loan";
        let is_end_of_loan = transfer_type == "end of loan";
        history
            .loans
            .record(is_loan, is_end_of_loan, source, destination, player_id);
        if !is_loan
            && let (Some(destination), Some(when)) = (destination, when)
            && !is_end_of_loan
            && transfer_type != "shirt_number_update"
        {
            history
                .permanent_routes
                .entry((player_id, source))
                .or_default()
                .push((destination, when));
        }
    }
    history
}
