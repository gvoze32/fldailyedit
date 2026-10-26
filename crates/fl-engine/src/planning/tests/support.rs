//! Shared fixtures for the planning scenarios.

use std::collections::{HashMap, HashSet};

use fl_save::model::TeamData;
use fl_sources::model::{SquadMember, SquadSnapshot, Transfer, TransferType, VerificationStatus};

use crate::matcher::{NameMatcher, PlayerAttrs};
use crate::planning::{
    ClubIdentity, HistoryEntry, MatchInput, MatchOutput, MatchedTransfer, OverflowSelector,
    Resolution, RosterPlanInput, RosterPlanOutput, Side, build_already_applied,
    build_superseded_loan_sources, match_transfers_statefully, plan_roster_actions,
};

/// A matcher loaded with the given players and teams.
pub fn matcher(players: &[(&str, u32)], teams: &[(&str, u32)]) -> NameMatcher {
    matcher_with_attrs(players, HashMap::new(), teams)
}

pub fn matcher_with_attrs(
    players: &[(&str, u32)],
    attrs: HashMap<u32, PlayerAttrs>,
    teams: &[(&str, u32)],
) -> NameMatcher {
    NameMatcher::new(
        players.iter().map(|(name, id)| ((*name).to_owned(), *id)),
        attrs,
        teams.iter().map(|(name, id)| ((*name).to_owned(), *id)),
    )
}

/// Same, from owned names (for generated players).
pub fn matcher_owned(
    players: &[(String, u32)],
    attrs: HashMap<u32, PlayerAttrs>,
    teams: &[(&str, u32)],
) -> NameMatcher {
    NameMatcher::new(
        players.iter().cloned(),
        attrs,
        teams.iter().map(|(name, id)| ((*name).to_owned(), *id)),
    )
}

/// `ClubIdentityIndex` double: fixed FotMob bindings plus save-name lookup
/// (explicit names first, else the matcher's team match, else outside).
#[derive(Default)]
pub struct FakeIdentity<'a> {
    pub bindings: Vec<(i64, u32)>,
    pub names: HashMap<String, Resolution>,
    pub matcher: Option<&'a NameMatcher>,
}

pub fn identity<'a>(bindings: &[(i64, u32)], matcher: Option<&'a NameMatcher>) -> FakeIdentity<'a> {
    FakeIdentity {
        bindings: bindings.to_vec(),
        names: HashMap::new(),
        matcher,
    }
}

impl ClubIdentity for FakeIdentity<'_> {
    fn pes_for_fotmob(&self, fotmob_id: i64) -> Option<u32> {
        self.bindings
            .iter()
            .find(|(id, _)| *id == fotmob_id)
            .map(|(_, team)| *team)
    }

    fn fotmob_for_pes(&self, team_id: u32) -> Option<i64> {
        self.bindings
            .iter()
            .find(|(_, team)| *team == team_id)
            .map(|(id, _)| *id)
    }

    fn resolve_name(&self, name: &str) -> Resolution {
        if let Some(resolution) = self.names.get(name) {
            return *resolution;
        }
        let Some(matcher) = self.matcher else {
            return Resolution::Outside;
        };
        let found = matcher.match_team(name, None);
        match found.id {
            None if found.confidence >= 75.0 => Resolution::Unresolved,
            None => Resolution::Outside,
            Some(team) if found.confidence >= 98.0 => Resolution::Team(team),
            Some(_) => Resolution::Unresolved,
        }
    }

    fn bindings(&self) -> Vec<(i64, u32)> {
        self.bindings.clone()
    }
}

/// An overflow oracle that never finds a candidate.
pub struct NoOverflow;

impl OverflowSelector for NoOverflow {
    fn find_candidate(&self, _: u32, _: u32, _: &[u32], _: &HashSet<u32>) -> Option<u32> {
        None
    }
}

/// A club roster of `players` padded to 40 slots with zeros.
pub fn team(team_id: u32, players: &[u32]) -> TeamData {
    let mut data = TeamData::empty(team_id);
    for (slot, &player) in players.iter().enumerate() {
        data.player_ids[slot] = player;
    }
    data
}

pub fn ids(range: std::ops::Range<u32>) -> Vec<u32> {
    range.collect()
}

pub fn club_set(ids: &[u32]) -> HashSet<u32> {
    ids.iter().copied().collect()
}

/// `Transfer("name", from, to)` with default metadata.
pub fn transfer(name: &str, from: &str, to: &str) -> Transfer {
    Transfer {
        player_name: name.to_owned(),
        from_club: from.to_owned(),
        to_club: to.to_owned(),
        ..Transfer::default()
    }
}

pub fn dated(mut transfer: Transfer, date: &str, transfer_type: TransferType) -> Transfer {
    transfer.date = date.to_owned();
    transfer.transfer_type = transfer_type;
    transfer.is_loan = transfer_type == TransferType::Loan;
    transfer
}

/// A fully matched club-to-club event for the standard test player
/// (`Randal Kolo Muani`, id 115254).
pub fn club_match(
    source: u32,
    destination: u32,
    date: &str,
    transfer_type: TransferType,
    is_loan: bool,
) -> MatchedTransfer {
    let mut item = MatchedTransfer::new(Transfer {
        player_name: "Randal Kolo Muani".to_owned(),
        from_club: "Source".to_owned(),
        to_club: "Destination".to_owned(),
        date: date.to_owned(),
        transfer_type,
        is_loan,
        ..Transfer::default()
    });
    item.player_id = Some(115254);
    item.from = Side::Team(source);
    item.to = Side::Team(destination);
    item.player_confidence = 100.0;
    item.from_confidence = 100.0;
    item.to_confidence = 100.0;
    item
}

/// A matched event with explicit ids and 100% confidences.
pub fn matched(transfer: Transfer, player: Option<u32>, from: Side, to: Side) -> MatchedTransfer {
    let mut item = MatchedTransfer::new(transfer);
    item.player_id = player;
    item.from = from;
    item.to = to;
    item.player_confidence = 100.0;
    item.from_confidence = 100.0;
    item.to_confidence = 100.0;
    item
}

pub fn member(name: &str, fotmob_id: Option<i64>) -> SquadMember {
    SquadMember {
        player_name: name.to_owned(),
        player_id_fotmob: fotmob_id,
        ..SquadMember::default()
    }
}

pub fn snapshot(club: &str, fotmob_team_id: i64, members: Vec<SquadMember>) -> SquadSnapshot {
    SquadSnapshot {
        club_name: club.to_owned(),
        team_id_fotmob: fotmob_team_id,
        members,
        source_url: format!("https://www.fotmob.com/api/data/teams?id={fotmob_team_id}"),
        complete: true,
        primary_league_id: None,
        formation: None,
        formation_source_url: String::new(),
    }
}

/// An enabled destination-only roster signal.
pub fn registration(name: &str, to_club: &str, to_fotmob: i64, fotmob_player: i64) -> Transfer {
    let url = format!("https://www.fotmob.com/api/data/teams?id={to_fotmob}");
    Transfer {
        player_name: name.to_owned(),
        to_club: to_club.to_owned(),
        transfer_type: TransferType::SquadRegistration,
        to_club_id_fotmob: Some(to_fotmob),
        player_id_fotmob: Some(fotmob_player),
        source_urls: vec![url.clone()],
        proof_urls: vec![url],
        verification_status: VerificationStatus::Enabled,
        infer_from_current_roster: true,
        ..Transfer::default()
    }
}

pub fn history_entry(
    player: u32,
    from: u32,
    to: u32,
    transfer_type: &str,
    date: &str,
) -> HistoryEntry {
    HistoryEntry {
        player_id: Some(player),
        from_team_id: Some(from),
        to_team_id: Some(to),
        transfer_type: transfer_type.to_owned(),
        transfer_date: date.to_owned(),
        ..HistoryEntry::default()
    }
}

/// Run `match_transfers_statefully` with defaults for everything optional.
pub struct MatchRun<'a> {
    pub transfers: Vec<Transfer>,
    pub matcher: &'a NameMatcher,
    pub threshold: f64,
    pub teams: Vec<(u32, Vec<u32>)>,
    pub clubs: HashSet<u32>,
    pub history: Vec<HistoryEntry>,
    pub identity: Option<&'a dyn ClubIdentity>,
    pub snapshots: Vec<SquadSnapshot>,
    pub player_names: HashMap<u32, String>,
    pub shirt_numbers: Option<crate::planning::ShirtNumbers>,
}

impl<'a> MatchRun<'a> {
    pub fn new(matcher: &'a NameMatcher, teams: Vec<(u32, Vec<u32>)>, clubs: &[u32]) -> Self {
        Self {
            transfers: Vec::new(),
            matcher,
            threshold: 80.0,
            teams,
            clubs: club_set(clubs),
            history: Vec::new(),
            identity: None,
            snapshots: Vec::new(),
            player_names: HashMap::new(),
            shirt_numbers: None,
        }
    }

    pub fn run(&self) -> MatchOutput {
        match_transfers_statefully(&MatchInput {
            transfers: &self.transfers,
            matcher: self.matcher,
            threshold: self.threshold,
            teams: &self.teams,
            club_ids: &self.clubs,
            history: &self.history,
            club_identity: self.identity,
            squad_snapshots: &self.snapshots,
            player_names: &self.player_names,
            team_shirt_numbers: self.shirt_numbers.as_ref(),
        })
    }
}

/// Plan roster actions for `matches` against `rosters` with the superseded
/// loan sources derived from `history`.
pub fn plan(
    matches: &[MatchedTransfer],
    rosters: &[TeamData],
    club_ids: &HashSet<u32>,
    overflow: &dyn OverflowSelector,
    history: &[HistoryEntry],
    allow_overflow_release: bool,
    live_squad_ids: &HashMap<u32, HashSet<u32>>,
) -> RosterPlanOutput {
    let superseded = build_superseded_loan_sources(matches, history);
    plan_with_superseded(
        matches,
        rosters,
        club_ids,
        overflow,
        &superseded,
        allow_overflow_release,
        live_squad_ids,
    )
}

pub fn plan_with_superseded(
    matches: &[MatchedTransfer],
    rosters: &[TeamData],
    club_ids: &HashSet<u32>,
    overflow: &dyn OverflowSelector,
    superseded: &[HashSet<u32>],
    allow_overflow_release: bool,
    live_squad_ids: &HashMap<u32, HashSet<u32>>,
) -> RosterPlanOutput {
    let slots: Vec<(u32, Vec<u32>)> = rosters
        .iter()
        .map(|t| (t.team_id, t.player_ids.to_vec()))
        .collect();
    plan_roster_actions(&RosterPlanInput {
        matches,
        teams: &slots,
        club_ids,
        overflow,
        superseded_loan_sources: superseded,
        already_applied: &[],
        allow_overflow_release,
        live_squad_ids,
    })
}

/// Plan roster actions like a rerun: `history` is the transfer log of the save,
/// which both authorizes loan-return moves and marks already applied events.
pub fn plan_with_log(
    matches: &[MatchedTransfer],
    rosters: &[TeamData],
    club_ids: &HashSet<u32>,
    history: &[HistoryEntry],
) -> RosterPlanOutput {
    let slots: Vec<(u32, Vec<u32>)> = rosters
        .iter()
        .map(|t| (t.team_id, t.player_ids.to_vec()))
        .collect();
    plan_roster_actions(&RosterPlanInput {
        matches,
        teams: &slots,
        club_ids,
        overflow: &NoOverflow,
        superseded_loan_sources: &build_superseded_loan_sources(matches, history),
        already_applied: &build_already_applied(matches, history),
        allow_overflow_release: true,
        live_squad_ids: &HashMap::new(),
    })
}

/// `(action, current_team)` pairs of a plan.
pub fn actions(output: &RosterPlanOutput) -> Vec<(&'static str, Option<u32>)> {
    output
        .actions
        .iter()
        .map(|a| (a.action.as_str(), a.current_team_id))
        .collect()
}
