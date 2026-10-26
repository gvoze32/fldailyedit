//! Deciding what to do: pure planning, no I/O.
//!
//! [`plan_transfers`] turns one scrape plus the save's current state into a
//! [`Plan`]: matched events, the chronological roster actions (skips with
//! reasons included), skipped rows, FotMob identity maps, formations and
//! tactics. Things that depend on the roster *after* this run's changes
//! (shirt batches, captains) are separate pure functions the pipeline calls
//! with the live state: [`plan_shirt_batch`], [`plan_captain_updates`].
//!
//! Stages: [`history`] replays the transfer log; [`identity`] binds FotMob
//! players to save players from live squads; [`matching`] binds events to
//! clubs and players while [`roster_sim`] tracks the simulated rosters;
//! [`snapshots`] derives moves/releases from live squads; [`roster_actions`]
//! decides and simulates the mutation per event; [`shirts`] dedupes and
//! batches shirt numbers; [`gameplan`] resolves formations, tactics, captains
//! and the clubs whose lineup is re-picked.

mod gameplan;
mod history;
mod identity;
mod matching;
mod order;
mod ports;
mod roster_actions;
mod roster_sim;
mod save_adapters;
mod shirts;
mod skipped;
mod snapshots;
mod teams;
mod types;

#[cfg(test)]
mod tests;

use std::collections::{HashMap, HashSet};

use chrono::NaiveDate;
use fl_save::model::TeamData;
use fl_sources::model::ScrapeResult;

pub use gameplan::{
    PlannedCaptainUpdate, plan_captain_updates, plan_gameplan_formations, plan_gameplan_tactics,
    plan_lineup_clubs,
};
pub use history::{History, LoanLedger, replay_history};
pub use identity::{
    IdentityMap, MIN_COMPLETE_SQUAD_MEMBERS, Observation, ShirtNumbers, SnapshotIdentities,
    all_roster_ids, build_fotmob_identity_index, match_snapshot_member,
};
pub use matching::{MatchInput, MatchOutput, MatchReport, match_transfers_statefully};
pub use order::{event_order_key, ordered_indices, sort_events};
pub use ports::{
    ClubIdentity, OverflowSelector, PlayerHit, PlayerMatcher, PlayerQuery, Resolution,
    RosterReader, ScoreContext, TeamHit, TeamMatcher,
};
pub use roster_actions::{
    RosterPlanInput, RosterPlanOutput, build_already_applied, build_superseded_loan_sources,
    decide_roster_action, plan_roster_actions,
};
pub use roster_sim::{RosterSim, SlotMode};
pub use shirts::{
    DroppedShirt, ShirtDedupe, ShirtStatus, dedupe_shirt_number_matches, plan_shirt_batch,
};
pub use skipped::{SkippedTransfer, is_retryable_reason, pending_skipped, sort_skipped};
pub use snapshots::{CoverageSet, SnapshotCoverage};
pub use teams::{
    SaveClubs, TeamResolution, fotmob_team_map, match_transfer_team, resolve_snapshot_club,
};
pub use types::{
    HistoryEntry, MatchSkip, MatchedTransfer, PlannedRosterAction, RosterAction, RosterMap, Side,
};

/// Everything one planning pass reads.
pub struct PlanInput<'a> {
    pub scrape: &'a ScrapeResult,
    pub matcher: &'a dyn PlayerMatcher,
    pub club_identity: Option<&'a dyn ClubIdentity>,
    /// Minimum player-name confidence (0–100).
    pub threshold: f64,
    /// Every team's roster as saved.
    pub rosters: &'a [TeamData],
    pub club_ids: &'a HashSet<u32>,
    pub history: &'a [HistoryEntry],
    pub player_names: &'a HashMap<u32, String>,
    pub overflow: &'a dyn OverflowSelector,
    pub allow_overflow_release: bool,
    /// Today in UTC; separates future-dated pending events from stale ones.
    pub today: NaiveDate,
}

/// Counts for the run summary.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct PlanCounts {
    pub fully_actionable: usize,
    pub club_transfers: usize,
    pub departures: usize,
    pub signings: usize,
    pub shirt_checks: usize,
    pub unmatched: usize,
    /// Shirt observations discarded as duplicate or ambiguous.
    pub duplicate_shirt_dropped: usize,
}

/// The decided plan for one run.
#[derive(Debug, Clone, Default)]
pub struct Plan {
    /// Matched events after shirt dedupe, in application order.
    pub matches: Vec<MatchedTransfer>,
    /// The fully matched subset of `matches`.
    pub fully_matched: Vec<MatchedTransfer>,
    /// Chronological roster actions, skips included.
    pub roster_actions: Vec<PlannedRosterAction>,
    /// Events not applied (planning, live-squad, shirt-dedupe and pending
    /// rows), unsorted; see [`sort_skipped`].
    pub skipped: Vec<SkippedTransfer>,
    /// FotMob player id → save player (live identity over the log).
    pub fotmob_player_ids: HashMap<i64, u32>,
    /// FotMob club id → save team id.
    pub fotmob_team_map: HashMap<i64, u32>,
    /// Each save club's complete live squad.
    pub live_squad_ids: HashMap<u32, HashSet<u32>>,
    /// Formations per club, in first-seen order.
    pub formations: Vec<(u32, String)>,
    /// Tactical settings per club, in first-seen order; empty when the scrape
    /// carried none.
    pub tactics: Vec<(u32, Vec<(String, i32)>)>,
    pub counts: PlanCounts,
}

/// Match the scraped events, derive live-squad moves and releases, and
/// simulate the roster actions.
pub fn plan_transfers(input: &PlanInput<'_>) -> Plan {
    let scrape = input.scrape;

    let events: Vec<_> = scrape
        .transfers
        .iter()
        .chain(&scrape.roster_updates)
        .cloned()
        .collect();
    let present: Vec<(u32, Vec<u32>)> = input
        .rosters
        .iter()
        .map(|team| (team.team_id, team.roster().collect()))
        .collect();
    let shirt_numbers: ShirtNumbers = input
        .rosters
        .iter()
        .map(|team| {
            let numbers = team
                .player_ids
                .iter()
                .zip(&team.shirt_numbers)
                .filter(|(player, number)| **player != 0 && **number > 0)
                .map(|(player, number)| (*player, u32::from(*number)))
                .collect();
            (team.team_id, numbers)
        })
        .collect();

    let matched_out = match_transfers_statefully(&MatchInput {
        transfers: &events,
        matcher: input.matcher,
        threshold: input.threshold,
        teams: &present,
        club_ids: input.club_ids,
        history: input.history,
        club_identity: input.club_identity,
        squad_snapshots: &scrape.squad_snapshots,
        player_names: input.player_names,
        team_shirt_numbers: Some(&shirt_numbers),
    });
    let MatchOutput { matched, report } = matched_out;

    let deduped = dedupe_shirt_number_matches(matched);
    let superseded = build_superseded_loan_sources(&deduped.kept, input.history);
    let already_applied = build_already_applied(&deduped.kept, input.history);
    let slots: Vec<(u32, Vec<u32>)> = input
        .rosters
        .iter()
        .map(|team| (team.team_id, team.player_ids.to_vec()))
        .collect();
    let actions = plan_roster_actions(&RosterPlanInput {
        matches: &deduped.kept,
        teams: &slots,
        club_ids: input.club_ids,
        overflow: input.overflow,
        superseded_loan_sources: &superseded,
        already_applied: &already_applied,
        allow_overflow_release: input.allow_overflow_release,
        live_squad_ids: &report.live_squad_ids,
    });

    let non_shirt = || deduped.kept.iter().filter(|item| !item.is_shirt_update());
    let fully_matched: Vec<MatchedTransfer> = deduped
        .kept
        .iter()
        .filter(|item| item.is_fully_matched())
        .cloned()
        .collect();
    let counts = PlanCounts {
        fully_actionable: fully_matched.len(),
        club_transfers: non_shirt().filter(|item| item.is_club_transfer()).count(),
        departures: non_shirt().filter(|item| item.is_release()).count(),
        signings: non_shirt().filter(|item| item.is_sign()).count(),
        shirt_checks: deduped
            .kept
            .iter()
            .filter(|item| item.is_shirt_update() && item.is_fully_matched())
            .count(),
        unmatched: deduped.kept.len() - fully_matched.len(),
        duplicate_shirt_dropped: deduped.discarded,
    };

    let fotmob_team_map = input.club_identity.map(fotmob_team_map).unwrap_or_default();
    let formations =
        plan_gameplan_formations(&scrape.squad_snapshots, input.club_ids, &fotmob_team_map);
    let tactics = if scrape.tactical_updates.is_empty() {
        Vec::new()
    } else {
        plan_gameplan_tactics(&scrape.tactical_updates, input.club_ids, &fotmob_team_map)
    };

    let saves = input.club_identity.map(|identity| SaveClubs {
        identity,
        club_ids: input.club_ids,
    });
    let mut skipped = report.skipped;
    skipped.extend(actions.skipped);
    skipped.extend(deduped.dropped.iter().map(|dropped| {
        SkippedTransfer::from_match(
            &dropped.matched,
            dropped.reason,
            &dropped.detail,
            &[],
            Some(input.club_ids),
        )
    }));
    skipped.extend(pending_skipped(
        &scrape.pending_transfers,
        saves.as_ref(),
        input.today,
    ));

    Plan {
        matches: deduped.kept,
        fully_matched,
        roster_actions: actions.actions,
        skipped,
        fotmob_player_ids: report.fotmob_player_ids,
        fotmob_team_map,
        live_squad_ids: report.live_squad_ids,
        formations,
        tactics,
        counts,
    }
}
