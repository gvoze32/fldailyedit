//! Choosing and simulating the roster mutation for each matched event.

use std::collections::{HashMap, HashSet};

use chrono::{DateTime, Utc};
use fl_save::layout::MIN_CLUB_ROSTER_SIZE;
use fl_sources::dates::parse_iso_datetime;
use fl_sources::model::TransferType;

use crate::log::repr_str;

use super::ports::OverflowSelector;
use super::roster_sim::{RosterSim, SlotMode};
use super::skipped::SkippedTransfer;
use super::types::{HistoryEntry, MatchedTransfer, PlannedRosterAction, RosterAction, Side};

/// Choose a roster mutation from the verified current state.
///
/// Returns `(action, reason)`; `reason` is a code, set for every skip.
/// `reconcilable` are clubs other than the named source from which evidence
/// (an earlier loan, the destination's live squad, or a later event from the
/// destination) authorizes moving the player.
pub fn decide_roster_action(
    current: Option<u32>,
    from: Option<u32>,
    to: Option<u32>,
    transfer_type: TransferType,
    reconcilable: &HashSet<u32>,
) -> (RosterAction, &'static str) {
    use RosterAction::{Add, Move, Noop, Release, ShirtUpdate, Skip};

    if transfer_type == TransferType::ShirtNumberUpdate {
        return if to.is_some() && current == to {
            (ShirtUpdate, "")
        } else {
            (Skip, "shirt_player_not_at_club")
        };
    }
    match (from, to) {
        (Some(from), Some(to)) => {
            if current == Some(to) {
                (Noop, "")
            } else if current == Some(from) || current.is_some_and(|c| reconcilable.contains(&c)) {
                (Move, "")
            } else if current.is_none() {
                // Unattached in the save (e.g. an earlier loan outside the
                // save released him): the known destination is authoritative.
                (Add, "")
            } else {
                (Skip, "current_club_mismatch")
            }
        }
        (None, Some(to)) => {
            if current == Some(to) {
                (Noop, "")
            } else if current.is_none() {
                (Add, "")
            } else if current.is_some_and(|c| reconcilable.contains(&c)) {
                (Move, "")
            } else {
                (Skip, "already_registered_elsewhere")
            }
        }
        (Some(from), None) => {
            if current == Some(from) {
                (Release, "")
            } else if current.is_none() {
                (Noop, "")
            } else {
                (Skip, "current_club_mismatch")
            }
        }
        (None, None) => (Skip, "outside_save"),
    }
}

/// An earlier loan: `(parent, loan club or None outside the save, date)`.
type PriorLoan = (u32, Option<u32>, DateTime<Utc>);

/// Authorize moves from the club a feed-omitted loan return left stale.
///
/// Transfer feeds commonly omit the synthetic loan-return event. For example,
/// PSG → Tottenham (loan) followed by PSG → Juventus (permanent) leaves a
/// current save roster at Tottenham even though the newer event names PSG as
/// its source. Only a strictly earlier loan from that same parent club can
/// authorize the stale loan club as the actual move source.
///
/// Likewise, for an event from a club outside the save, a strictly earlier
/// loan from a save club to a club outside the save authorizes moving the
/// player from that parent club, where the save still registers him.
///
/// The result is aligned with `matches`.
pub fn build_superseded_loan_sources(
    matches: &[MatchedTransfer],
    history: &[HistoryEntry],
) -> Vec<HashSet<u32>> {
    let mut prior_loans: HashMap<u32, Vec<PriorLoan>> = HashMap::new();
    for entry in history {
        if entry.type_lower() != "loan" {
            continue;
        }
        let (Some(player_id), Some(parent)) = (
            entry.player_id.filter(|&id| id > 0),
            entry.from_team_id.filter(|&id| id > 0),
        ) else {
            continue;
        };
        let Some(date) = parse_iso_datetime(entry.when()) else {
            continue;
        };
        let loan_team = entry.to_team_id.filter(|&id| id > 0);
        prior_loans
            .entry(player_id)
            .or_default()
            .push((parent, loan_team, date));
    }
    for item in matches {
        let (Some(player_id), Side::Team(from)) = (item.player_id, item.from) else {
            continue;
        };
        let Some(date) = parse_iso_datetime(&item.transfer.date) else {
            continue;
        };
        if item.to.is_unresolved() || !item.is_loan() {
            continue;
        }
        prior_loans
            .entry(player_id)
            .or_default()
            .push((from, item.to.team(), date));
    }

    matches
        .iter()
        .map(|item| {
            let (Some(player_id), Side::Team(to)) = (item.player_id, item.to) else {
                return HashSet::new();
            };
            let Some(date) = parse_iso_datetime(&item.transfer.date) else {
                return HashSet::new();
            };
            let loans = prior_loans.get(&player_id).map_or(&[][..], Vec::as_slice);
            match item.from {
                Side::Outside => loans
                    .iter()
                    .filter(|(parent, loan_team, loan_date)| {
                        loan_team.is_none() && *parent != to && *loan_date < date
                    })
                    .map(|(parent, _, _)| *parent)
                    .collect(),
                Side::Team(from) => loans
                    .iter()
                    .filter_map(|(parent, loan_team, loan_date)| {
                        let loan_team = (*loan_team)?;
                        (*parent == from && loan_team != to && *loan_date < date)
                            .then_some(loan_team)
                    })
                    .collect(),
                Side::Unresolved => HashSet::new(),
            }
        })
        .collect()
}

/// Per match, whether the transfer log already records the identical event:
/// same player, source, destination (a release has none) and event date.
/// Undated events and shirt observations are state-derived and never count.
/// The result is aligned with `matches`.
pub fn build_already_applied(matches: &[MatchedTransfer], history: &[HistoryEntry]) -> Vec<bool> {
    type Key<'a> = (u32, Option<u32>, Option<u32>, &'a str);
    let logged: HashSet<Key<'_>> = history
        .iter()
        .filter_map(|entry| {
            Some((
                entry.player_id?,
                entry.from_team_id,
                entry.to_team_id,
                entry.transfer_date.as_str(),
            ))
        })
        .filter(|(_, from, to, date)| (from.is_some() || to.is_some()) && !date.is_empty())
        .collect();
    matches
        .iter()
        .map(|item| {
            let Some(player_id) = item.player_id else {
                return false;
            };
            item.is_fully_matched()
                && !item.is_shirt_update()
                && !item.transfer.date.is_empty()
                && logged.contains(&(
                    player_id,
                    item.from.team(),
                    item.to.team(),
                    item.transfer.date.as_str(),
                ))
        })
        .collect()
}

/// Explain why matching could not produce an actionable event.
fn unmatched_skip_reason(item: &MatchedTransfer) -> (String, String) {
    let transfer = &item.transfer;
    let fotmob = |id: Option<i64>| id.map_or_else(|| "None".to_owned(), |id| id.to_string());
    if item.player_id.is_none() {
        let fotmob_id = transfer.player_id_fotmob.filter(|&id| id > 0);
        return (
            "player_not_matched".to_owned(),
            format!(
                "no unique save player matches {}{}",
                repr_str(&transfer.player_name),
                fotmob_id.map_or_else(String::new, |id| format!(" (FotMob {id})"))
            ),
        );
    }
    if item.from.is_unresolved() {
        return (
            "source_team_not_matched".to_owned(),
            format!(
                "club {} (FotMob {}) is not confidently resolved in the save",
                label_repr(&transfer.from_club_full_name, &transfer.from_club),
                fotmob(transfer.from_club_id_fotmob)
            ),
        );
    }
    if item.to.is_unresolved() {
        return (
            "destination_team_not_matched".to_owned(),
            format!(
                "club {} (FotMob {}) is not confidently resolved in the save",
                label_repr(&transfer.to_club_full_name, &transfer.to_club),
                fotmob(transfer.to_club_id_fotmob)
            ),
        );
    }
    (
        "outside_save".to_owned(),
        "neither club is in the save".to_owned(),
    )
}

fn label_repr(full: &str, short: &str) -> String {
    repr_str(if full.is_empty() { short } else { full })
}

/// Everything roster-action planning reads.
pub struct RosterPlanInput<'a> {
    /// Matched events, in application order.
    pub matches: &'a [MatchedTransfer],
    /// Club rosters as 40 slots (0 = empty), table order. Teams outside
    /// `club_ids` are ignored.
    pub teams: &'a [(u32, Vec<u32>)],
    pub club_ids: &'a HashSet<u32>,
    pub overflow: &'a dyn OverflowSelector,
    /// Per match, clubs besides the named source that may authorize a move.
    pub superseded_loan_sources: &'a [HashSet<u32>],
    /// Per match, whether the transfer log already holds this exact event
    /// (see [`build_already_applied`]).
    pub already_applied: &'a [bool],
    pub allow_overflow_release: bool,
    /// Each save club's complete live squad.
    pub live_squad_ids: &'a HashMap<u32, HashSet<u32>>,
}

#[derive(Debug, Clone, Default)]
pub struct RosterPlanOutput {
    /// Chronological actions, skips included.
    pub actions: Vec<PlannedRosterAction>,
    /// One row per skipped non-shirt action.
    pub skipped: Vec<SkippedTransfer>,
}

struct Planner<'a> {
    input: &'a RosterPlanInput<'a>,
    sim: RosterSim,
    team_names: HashMap<u32, String>,
    /// Per player: `(match index, club)` of later events proving where he
    /// went — an event leaving club X, or a live shirt observation at X.
    later_evidence: HashMap<u32, Vec<(usize, u32)>>,
    /// Per player: the index of his last event the log already holds. Earlier
    /// events of his history are obsolete: he has moved on since.
    last_applied: HashMap<u32, usize>,
    transferred_in_plan: HashSet<u32>,
    /// Per club: players whose last matched event of this feed names it as
    /// the destination. The feed says they belong there, so a signing never
    /// displaces them, whether this plan moves them in or they already sit
    /// there (a `noop`).
    feed_members: HashMap<u32, HashSet<u32>>,
    /// Players a later plan step releases from a club. A full club drops one
    /// of them before any player the club still wants.
    pending_releases: HashMap<u32, HashSet<u32>>,
}

impl<'a> Planner<'a> {
    fn new(input: &'a RosterPlanInput<'a>) -> Self {
        let sim = RosterSim::new(
            SlotMode::Slots,
            input
                .teams
                .iter()
                .filter(|(team_id, _)| input.club_ids.contains(team_id))
                .cloned(),
            input.club_ids,
        );
        let mut team_names: HashMap<u32, String> = HashMap::new();
        for item in input.matches {
            for (side, name) in [
                (item.from, &item.matched_from_team),
                (item.to, &item.matched_to_team),
            ] {
                if let Side::Team(team_id) = side
                    && !name.is_empty()
                {
                    team_names.entry(team_id).or_insert_with(|| name.clone());
                }
            }
        }
        let mut later_evidence: HashMap<u32, Vec<(usize, u32)>> = HashMap::new();
        let mut pending_releases: HashMap<u32, HashSet<u32>> = HashMap::new();
        let mut last_applied: HashMap<u32, usize> = HashMap::new();
        let mut last_destination: HashMap<u32, Option<u32>> = HashMap::new();
        for (index, item) in input.matches.iter().enumerate() {
            if input.already_applied.get(index).copied().unwrap_or(false)
                && let Some(player_id) = item.player_id
            {
                last_applied.insert(player_id, index);
            }
            if let Some(player_id) = item.player_id
                && item.is_fully_matched()
                && !item.is_shirt_update()
            {
                last_destination.insert(player_id, item.to.team());
            }
            if let Some(player_id) = item.player_id
                && item.is_fully_matched()
            {
                let evidence = if item.is_shirt_update() {
                    item.to
                } else {
                    item.from
                };
                if let Side::Team(team_id) = evidence {
                    later_evidence
                        .entry(player_id)
                        .or_default()
                        .push((index, team_id));
                }
            }
            if item.is_release()
                && let (Some(player_id), Side::Team(from)) = (item.player_id, item.from)
            {
                pending_releases.entry(from).or_default().insert(player_id);
            }
        }
        let mut feed_members: HashMap<u32, HashSet<u32>> = HashMap::new();
        for (player_id, destination) in last_destination {
            if let Some(team_id) = destination {
                feed_members.entry(team_id).or_default().insert(player_id);
            }
        }
        Self {
            input,
            sim,
            team_names,
            later_evidence,
            last_applied,
            transferred_in_plan: HashSet::new(),
            feed_members,
            pending_releases,
        }
    }

    fn team_label(&self, team_id: u32) -> String {
        match self.team_names.get(&team_id) {
            Some(name) => format!("{name} ({team_id})"),
            None => format!("club {team_id}"),
        }
    }

    fn has_destination_evidence(&self, index: usize, item: &MatchedTransfer) -> bool {
        let Side::Team(destination) = item.to else {
            return false;
        };
        let Some(player_id) = item.player_id else {
            return false;
        };
        if self
            .input
            .live_squad_ids
            .get(&destination)
            .is_some_and(|ids| ids.contains(&player_id))
        {
            return true;
        }
        self.later_evidence.get(&player_id).is_some_and(|rows| {
            rows.iter()
                .any(|&(later, team)| later > index && team == destination)
        })
    }

    fn select_overflow_candidate(
        &self,
        team_id: u32,
        incoming: u32,
    ) -> (Option<u32>, HashSet<u32>) {
        let roster = self.sim.roster(team_id).unwrap_or(&[]);
        let roster_ids: HashSet<u32> = roster.iter().copied().filter(|&p| p != 0).collect();
        // Current live-squad members are never the overflow casualty, nor
        // are the players the feed puts at this club (a signing the save
        // already held must not be dropped for a later one: the next run
        // would re-add him and drop the next).
        let mut live_protected = self.transferred_in_plan.clone();
        if let Some(members) = self.feed_members.get(&team_id) {
            live_protected.extend(members.intersection(&roster_ids));
        }
        if let Some(live) = self.input.live_squad_ids.get(&team_id) {
            live_protected.extend(live.intersection(&roster_ids));
        }
        let releasable: HashSet<u32> = self
            .pending_releases
            .get(&team_id)
            .into_iter()
            .flatten()
            .copied()
            .filter(|player| {
                roster_ids.contains(player)
                    && *player != incoming
                    && !live_protected.contains(player)
                    && self.sim.clubs_of(*player) == [team_id]
            })
            .collect();
        let overflow = self.input.overflow;
        if !releasable.is_empty() {
            let mut protected = live_protected.clone();
            protected.extend(roster_ids.difference(&releasable));
            let candidate = overflow.find_candidate(team_id, incoming, roster, &protected);
            if let Some(candidate) = candidate.filter(|c| releasable.contains(c)) {
                return (Some(candidate), protected);
            }
        }
        let candidate = overflow.find_candidate(team_id, incoming, roster, &live_protected);
        (candidate.filter(|&c| c != 0), live_protected)
    }

    /// Simulate one match; `None` means deferred for the roster minimum.
    fn plan_match(&mut self, index: usize, may_defer: bool) -> Option<PlannedRosterAction> {
        let input = self.input;
        let item = &input.matches[index];
        if !item.is_fully_matched() {
            let (reason, detail) = match &item.skip {
                Some(skip) => (skip.reason.clone(), skip.detail.clone()),
                None => unmatched_skip_reason(item),
            };
            let mut action = PlannedRosterAction::new(item.clone(), RosterAction::Skip, None);
            action.reason = reason;
            action.detail = detail;
            return Some(action);
        }

        let player_id = item.player_id.expect("fully matched events have a player");
        let mut current_clubs = self.sim.clubs_of(player_id).to_vec();
        current_clubs.sort_unstable();
        if current_clubs.len() > 1 {
            let labels: Vec<String> = current_clubs.iter().map(|&t| self.team_label(t)).collect();
            let mut action = PlannedRosterAction::new(item.clone(), RosterAction::Skip, None);
            action.reason = "duplicate_registration".to_owned();
            action.detail = format!("save registers player at {}", labels.join(", "));
            return Some(action);
        }

        let current = current_clubs.first().copied();
        let mut reconcilable: HashSet<u32> = input
            .superseded_loan_sources
            .get(index)
            .cloned()
            .unwrap_or_default();
        if let Some(current) = current
            && Some(current) != item.from.team()
            && Some(current) != item.to.team()
            && !item.is_shirt_update()
            && self.has_destination_evidence(index, item)
        {
            reconcilable.insert(current);
        }
        // The log already holds this exact event, or a later event of the same
        // player: it was applied to this save before, and the player has moved
        // on since (a later link of his chain, a loan return, an overflow
        // release). Applying it again would shuttle him back through the old
        // clubs or re-add a player an earlier run had to release, so a rerun
        // would never settle.
        let applied_before = input.already_applied.get(index).copied().unwrap_or(false)
            || (!item.is_shirt_update()
                && self
                    .last_applied
                    .get(&player_id)
                    .is_some_and(|&last| last > index));
        let (decided, reason) = if applied_before {
            (RosterAction::Noop, "")
        } else {
            decide_roster_action(
                current,
                item.from.team(),
                item.to.team(),
                item.transfer.transfer_type,
                &reconcilable,
            )
        };
        let mut action = PlannedRosterAction::new(item.clone(), decided, current);
        action.reason = reason.to_owned();
        if !reason.is_empty()
            && let Some(current) = current
        {
            action.detail = format!("save registers player at {}", self.team_label(current));
        }
        if action.action == RosterAction::Move && current != item.from.team() {
            tracing::info!(
                "Reconciled {} from actual save club {current:?} (event source {:?})",
                item.transfer.player_name,
                item.from.team()
            );
        }
        if matches!(action.action, RosterAction::Move | RosterAction::Release)
            && let Some(current) = current
            && self.sim.has_team(current)
            && self.sim.occupied(current) <= MIN_CLUB_ROSTER_SIZE
        {
            if may_defer {
                return None;
            }
            action.action = RosterAction::Skip;
            action.reason = "roster_minimum".to_owned();
            action.detail = format!(
                "{} would drop below {MIN_CLUB_ROSTER_SIZE} players and this run signs no replacement",
                self.team_label(current)
            );
        }

        // Validate everything this step needs before touching the simulated
        // rosters, so a refused step leaves no trace (removing the overflow
        // player first could otherwise be followed by a skipped move).
        if action.action == RosterAction::Move
            && !current.is_some_and(|team| self.sim.has_team(team))
        {
            action.action = RosterAction::Skip;
            action.reason = "source_roster_missing".to_owned();
        }
        if matches!(action.action, RosterAction::Move | RosterAction::Add) {
            match item.to.team().filter(|team| self.sim.has_team(*team)) {
                None => {
                    action.action = RosterAction::Skip;
                    action.reason = "destination_roster_missing".to_owned();
                }
                Some(destination) if !self.sim.has_hole(destination) => {
                    if !input.allow_overflow_release {
                        action.action = RosterAction::Skip;
                        action.reason = "destination_roster_full".to_owned();
                    } else {
                        let (candidate, protected) =
                            self.select_overflow_candidate(destination, player_id);
                        match candidate {
                            None => {
                                action.action = RosterAction::Skip;
                                action.reason = "no_safe_overflow_candidate".to_owned();
                                action.detail = format!(
                                    "{} is full and every player is protected",
                                    self.team_label(destination)
                                );
                            }
                            Some(candidate) => {
                                action.overflow_player_id = Some(candidate);
                                action.overflow = input.overflow.describe(
                                    destination,
                                    candidate,
                                    self.sim.roster(destination).unwrap_or(&[]),
                                    &protected,
                                );
                                self.sim.remove(destination, candidate);
                            }
                        }
                    }
                }
                Some(_) => {}
            }
        }

        match action.action {
            RosterAction::Move => {
                let to = item.to.team().expect("move has a destination");
                self.sim.remove(current.expect("validated"), player_id);
                self.sim.add(to, player_id);
                self.transferred_in_plan.insert(player_id);
            }
            RosterAction::Add => {
                self.sim
                    .add(item.to.team().expect("add has a destination"), player_id);
                self.transferred_in_plan.insert(player_id);
            }
            RosterAction::Release => {
                if let Some(current) = current {
                    self.sim.remove(current, player_id);
                }
            }
            _ => {}
        }
        Some(action)
    }
}

/// Build one chronological roster plan and simulate every accepted action.
///
/// A move whose named source disagrees with the save is reconciled from the
/// player's actual club when the destination's live squad, a later event from
/// the destination, or an earlier loan proves where he belongs. A departure
/// that would leave a club below the roster minimum is deferred to the end of
/// the plan and applied only if the plan backfilled the club. Every skipped
/// action is recorded in the output's `skipped` rows.
pub fn plan_roster_actions(input: &RosterPlanInput<'_>) -> RosterPlanOutput {
    let mut planner = Planner::new(input);
    let mut planned: Vec<PlannedRosterAction> = Vec::new();

    // Matches waiting for the roster minimum, in order; a deferred player's
    // later events wait too so his own history stays chronological.
    let mut deferred: Vec<usize> = Vec::new();
    let mut deferred_players: HashSet<Option<u32>> = HashSet::new();
    for (index, item) in input.matches.iter().enumerate() {
        if item.is_release()
            && let (Side::Team(from), Some(player_id)) = (item.from, item.player_id)
            && let Some(players) = planner.pending_releases.get_mut(&from)
        {
            players.remove(&player_id);
        }
        if item.player_id.is_some() && deferred_players.contains(&item.player_id) {
            deferred.push(index);
            continue;
        }
        match planner.plan_match(index, true) {
            Some(action) => planned.push(action),
            None => {
                deferred.push(index);
                deferred_players.insert(item.player_id);
            }
        }
    }

    while !deferred.is_empty() {
        let mut waiting: Vec<usize> = Vec::new();
        let mut blocked_players: HashSet<Option<u32>> = HashSet::new();
        for &index in &deferred {
            let player = input.matches[index].player_id;
            if blocked_players.contains(&player) {
                waiting.push(index);
                continue;
            }
            match planner.plan_match(index, true) {
                Some(action) => planned.push(action),
                None => {
                    waiting.push(index);
                    blocked_players.insert(player);
                }
            }
        }
        if waiting.len() == deferred.len() {
            for index in waiting {
                planned.extend(planner.plan_match(index, false));
            }
            break;
        }
        deferred = waiting;
    }

    let skipped = planned
        .iter()
        .filter(|item| item.action == RosterAction::Skip && !item.matched.is_shirt_update())
        .map(|item| {
            let candidates = item
                .matched
                .skip
                .as_ref()
                .map_or(&[][..], |s| s.candidates.as_slice());
            SkippedTransfer::from_match(
                &item.matched,
                &item.reason,
                &item.detail,
                candidates,
                Some(input.club_ids),
            )
        })
        .collect();
    RosterPlanOutput {
        actions: planned,
        skipped,
    }
}
