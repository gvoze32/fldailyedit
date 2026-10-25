//! Shirt-number dedupe and batch classification.

use std::collections::{HashMap, HashSet};

use fl_sources::model::{Transfer, TransferType};

use super::support::*;
use crate::planning::{
    MatchedTransfer, PlannedRosterAction, RosterAction, RosterReader, Side,
    dedupe_shirt_number_matches, plan_shirt_batch,
};

const TEAM: u32 = 100;

struct Reader(HashMap<u32, (Vec<u32>, Vec<u32>)>);

impl Reader {
    fn one(players: &[(u32, u32)]) -> Self {
        let mut ids: Vec<u32> = players.iter().map(|(p, _)| *p).collect();
        let mut numbers: Vec<u32> = players.iter().map(|(_, n)| *n).collect();
        ids.resize(40, 0);
        numbers.resize(40, 0);
        Self(HashMap::from([(TEAM, (ids, numbers))]))
    }
}

impl RosterReader for Reader {
    fn roster(&self, team_id: u32) -> Option<(Vec<u32>, Vec<u32>)> {
        self.0.get(&team_id).cloned()
    }
}

fn shirt_match(name: &str, player: u32, number: Option<u32>, confidence: f64) -> MatchedTransfer {
    let mut item = matched(
        Transfer {
            player_name: name.to_owned(),
            from_club: "Club".to_owned(),
            to_club: "Club".to_owned(),
            transfer_type: TransferType::ShirtNumberUpdate,
            shirt_number: number,
            ..Transfer::default()
        },
        Some(player),
        Side::Team(TEAM),
        Side::Team(TEAM),
    );
    item.player_confidence = confidence;
    item
}

fn shirt_action(name: &str, player: u32, number: u32) -> PlannedRosterAction {
    PlannedRosterAction::new(
        shirt_match(name, player, Some(number), 100.0),
        RosterAction::ShirtUpdate,
        Some(TEAM),
    )
}

fn classify(
    reader: &Reader,
    actions: &[PlannedRosterAction],
    arriving: &HashSet<(u32, u32)>,
) -> Vec<(Option<u32>, Option<u32>, String)> {
    let refs: Vec<&PlannedRosterAction> = actions.iter().collect();
    plan_shirt_batch(reader, &refs, arriving)
        .into_iter()
        .map(|status| {
            let status = status.expect("shirt action");
            (status.previous, status.conflict_player, status.reason)
        })
        .collect()
}

// py: test_real_run_applies_shirt_number_swaps_as_one_batch
#[test]
fn planned_number_swap_is_conflict_free() {
    let reader = Reader::one(&[(100527, 13), (168639, 12)]);
    let actions = [
        shirt_action("First", 100527, 12),
        shirt_action("Second", 168639, 13),
    ];
    let statuses = classify(&reader, &actions, &HashSet::new());
    assert_eq!(
        statuses,
        [
            (Some(13), None, String::new()),
            (Some(12), None, String::new())
        ]
    );
}

// py: test_real_run_skips_shirt_conflict_without_rolling_back
#[test]
fn shirt_held_by_a_player_outside_the_batch_is_a_conflict() {
    let reader = Reader::one(&[(100527, 1), (168639, 12)]);
    let actions = [shirt_action("Karl Darlow", 100527, 12)];
    let statuses = classify(&reader, &actions, &HashSet::new());
    assert_eq!(
        statuses,
        [(
            Some(1),
            Some(168639),
            "shirt_number_conflict:168639".to_owned()
        )]
    );
}

// py: test_shirt_update_for_player_not_on_team_is_safety_skipped
#[test]
fn shirt_update_for_a_player_not_on_the_roster_is_refused_unless_arriving() {
    let reader = Reader::one(&[(100527, 5)]);
    let actions = [
        shirt_action("Member", 100527, 12),
        shirt_action("Departed", 168639, 13),
    ];
    let statuses = classify(&reader, &actions, &HashSet::new());
    assert_eq!(statuses[0].2, "");
    assert_eq!(statuses[1], (None, None, "player_not_on_team".to_owned()));

    let arriving = HashSet::from([(TEAM, 168639)]);
    let statuses = classify(&reader, &actions, &arriving);
    assert_eq!(statuses[1].2, "");
}

#[test]
fn two_players_asking_for_one_number_are_both_refused() {
    let reader = Reader::one(&[(1, 5), (2, 6)]);
    let actions = [shirt_action("A", 1, 9), shirt_action("B", 2, 9)];
    let statuses = classify(&reader, &actions, &HashSet::new());
    assert_eq!(statuses[0].2, "duplicate_shirt_number:9");
    assert_eq!(statuses[1].2, "duplicate_shirt_number:9");
}

#[test]
fn chain_blocked_by_a_player_who_keeps_his_number_reports_dependency() {
    // A wants B's 12, B wants C's 13, C keeps 13: B is blocked by C, so A's
    // dependency on B (who does not move) blocks A as well.
    let reader = Reader::one(&[(1, 11), (2, 12), (3, 13)]);
    let actions = [shirt_action("A", 1, 12), shirt_action("B", 2, 13)];
    let statuses = classify(&reader, &actions, &HashSet::new());
    assert_eq!(
        statuses[0],
        (Some(11), Some(2), "shirt_number_conflict:2".to_owned())
    );
    assert_eq!(
        statuses[1],
        (Some(12), Some(3), "shirt_number_conflict:3".to_owned())
    );
}

#[test]
fn out_of_range_numbers_are_invalid_and_unchanged_numbers_are_clear() {
    let reader = Reader::one(&[(1, 5), (2, 6)]);
    let actions = [shirt_action("A", 1, 1000), shirt_action("B", 2, 6)];
    let statuses = classify(&reader, &actions, &HashSet::new());
    assert_eq!(statuses[0].2, "invalid_shirt_number");
    assert_eq!(statuses[1], (Some(6), None, String::new()));
}

#[test]
fn non_shirt_actions_get_no_status() {
    let reader = Reader::one(&[(1, 5)]);
    let mut other = shirt_action("A", 1, 9);
    other.action = RosterAction::Move;
    let refs = [&other];
    assert_eq!(plan_shirt_batch(&reader, &refs, &HashSet::new()), [None]);
}

// py: test_shirt_number_matches_are_eligible_for_roster_planning
#[test]
fn shirt_number_matches_are_eligible_for_roster_planning() {
    assert!(shirt_match("A", 1, Some(7), 100.0).is_fully_matched());
    let mut outside = shirt_match("A", 1, Some(7), 100.0);
    outside.to = Side::Unresolved;
    assert!(!outside.is_fully_matched());
}

// py: test_duplicate_shirt_matches_keep_stronger_observation
#[test]
fn duplicate_shirt_matches_keep_stronger_observation() {
    let weak = shirt_match("A", 1, Some(7), 90.0);
    let strong = shirt_match("A", 1, Some(8), 100.0);
    let out = dedupe_shirt_number_matches(vec![weak, strong.clone()]);
    assert_eq!(out.kept, vec![strong]);
    assert_eq!(out.discarded, 1);
    // B14: the outvoted observation disagreed, so it is surfaced.
    assert_eq!(out.dropped.len(), 1);
    assert_eq!(out.dropped[0].reason, "shirt_number_outvoted");
}

// py: test_ambiguous_shirt_numbers_fail_closed
#[test]
fn ambiguous_shirt_numbers_fail_closed() {
    let a = shirt_match("A", 1, Some(7), 100.0);
    let b = shirt_match("A", 1, Some(8), 99.0);
    let out = dedupe_shirt_number_matches(vec![a, b]);
    assert!(out.kept.is_empty());
    assert_eq!(out.discarded, 2);
    assert_eq!(out.dropped.len(), 2);
    assert!(
        out.dropped
            .iter()
            .all(|d| d.reason == "shirt_number_ambiguous")
    );
}

#[test]
fn same_number_duplicates_are_counted_but_not_reported() {
    let a = shirt_match("A", 1, Some(7), 100.0);
    let b = shirt_match("A", 1, Some(7), 95.0);
    let out = dedupe_shirt_number_matches(vec![a.clone(), b]);
    assert_eq!(out.kept, vec![a]);
    assert_eq!(out.discarded, 1);
    assert!(out.dropped.is_empty());
}

#[test]
fn dedupe_keeps_regular_events_first_and_winners_after() {
    let shirt = shirt_match("A", 1, Some(7), 100.0);
    let regular = club_match(10, 20, "2026-08-02", TransferType::Transfer, false);
    let out = dedupe_shirt_number_matches(vec![shirt.clone(), regular.clone()]);
    assert_eq!(out.kept, vec![regular, shirt]);
}
