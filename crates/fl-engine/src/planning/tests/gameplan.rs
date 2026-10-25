//! Captains, formations, tactics, lineup hints and pending-event reporting.

use std::collections::HashMap;

use chrono::NaiveDate;
use fl_save::model::Position;
use fl_sources::model::{CaptainUpdate, SquadMember, TacticalUpdate, Transfer};

use super::support::*;
use crate::planning::{
    RosterMap, SaveClubs, pending_skipped, plan_captain_updates, plan_gameplan_formations,
    plan_gameplan_preferences, plan_gameplan_tactics, sort_skipped,
};

fn captain(club: &str, fotmob_team: i64, name: &str, fotmob_player: Option<i64>) -> CaptainUpdate {
    CaptainUpdate {
        club_name: club.to_owned(),
        team_id_fotmob: fotmob_team,
        player_name: name.to_owned(),
        player_id_fotmob: fotmob_player,
        age: 0,
        nationality: String::new(),
        source: String::new(),
        source_url: String::new(),
    }
}

fn tactic(fotmob_team: i64, settings: &[(&str, i32)]) -> TacticalUpdate {
    TacticalUpdate {
        club_name: format!("Team {fotmob_team}"),
        team_id_fotmob: fotmob_team,
        league_id: 0,
        settings: settings
            .iter()
            .map(|(name, value)| ((*name).to_owned(), *value))
            .collect(),
        sample_matches: 0,
        source_urls: Vec::new(),
    }
}

fn positioned(name: &str, fotmob_id: i64, position: &str) -> SquadMember {
    SquadMember {
        position: position.to_owned(),
        ..member(name, Some(fotmob_id))
    }
}

// py: test_captain_planner_requires_validated_club_and_roster_match
#[test]
fn captain_planner_requires_validated_club_and_roster_match() {
    let matcher = matcher(&[("Captain Player", 1002)], &[("Example FC", 101)]);

    let planned = plan_captain_updates(
        &[captain("Example FC", 42, "Captain Player", Some(987))],
        &matcher,
        &RosterMap::from([(101, vec![1002])]),
        &club_set(&[101]),
        &HashMap::from([(42, 101)]),
        80.0,
        &HashMap::new(),
    );

    assert_eq!(planned.len(), 1);
    assert_eq!(planned[0].team_id, 101);
    assert_eq!(planned[0].player_id, 1002);
    assert_eq!(planned[0].matched_player_name, "Captain Player");
}

// py: test_captain_planner_prefers_resolved_fotmob_identity_over_name
#[test]
fn captain_planner_prefers_resolved_fotmob_identity_over_name() {
    let matcher = matcher(&[("Somebody Else", 1003), ("Captain Player", 1002)], &[]);

    let planned = plan_captain_updates(
        &[captain("Example FC", 42, "Capitão", Some(987))],
        &matcher,
        &RosterMap::from([(101, vec![1002, 1003])]),
        &club_set(&[101]),
        &HashMap::from([(42, 101)]),
        80.0,
        &HashMap::from([(987, 1002)]),
    );

    assert_eq!(
        planned
            .iter()
            .map(|item| (item.team_id, item.player_id))
            .collect::<Vec<_>>(),
        [(101, 1002)]
    );
}

#[test]
fn captain_planner_drops_a_club_with_conflicting_markers() {
    let matcher = matcher(&[], &[]);

    let planned = plan_captain_updates(
        &[
            captain("Example FC", 42, "First Captain", Some(987)),
            captain("Other FC", 43, "Other Captain", Some(990)),
            captain("Example FC", 42, "Second Captain", Some(988)),
        ],
        &matcher,
        &RosterMap::from([(101, vec![1001, 1002]), (102, vec![2001])]),
        &club_set(&[101, 102]),
        &HashMap::from([(42, 101), (43, 102)]),
        80.0,
        &HashMap::from([(987, 1001), (988, 1002), (990, 2001)]),
    );

    assert_eq!(
        planned
            .iter()
            .map(|item| (item.team_id, item.player_id))
            .collect::<Vec<_>>(),
        [(102, 2001)]
    );
}

#[test]
fn captain_planner_restarts_a_conflicted_club_on_a_third_marker() {
    let matcher = matcher(&[], &[]);

    let planned = plan_captain_updates(
        &[
            captain("Example FC", 42, "First Captain", Some(987)),
            captain("Other FC", 43, "Other Captain", Some(990)),
            captain("Example FC", 42, "Second Captain", Some(988)),
            captain("Example FC", 42, "Third Captain", Some(989)),
        ],
        &matcher,
        &RosterMap::from([(101, vec![1001, 1002, 1003]), (102, vec![2001])]),
        &club_set(&[101, 102]),
        &HashMap::from([(42, 101), (43, 102)]),
        80.0,
        &HashMap::from([(987, 1001), (988, 1002), (989, 1003), (990, 2001)]),
    );

    // The restarted club re-enters after the club that never conflicted.
    assert_eq!(
        planned
            .iter()
            .map(|item| (item.team_id, item.player_id))
            .collect::<Vec<_>>(),
        [(102, 2001), (101, 1003)]
    );
    assert_eq!(planned[1].source.player_name, "Third Captain");
}

// py: test_pending_provider_events_are_reported_with_reason_and_relevance
#[test]
fn pending_provider_events_are_reported_with_reason_and_relevance() {
    let identity = identity(&[(42, 101)], None);
    let club_ids = club_set(&[101]);
    let saves = SaveClubs {
        identity: &identity,
        club_ids: &club_ids,
    };
    let future = Transfer {
        date: "2026-12-01".to_owned(),
        to_club_id_fotmob: Some(42),
        player_id_fotmob: Some(555),
        ..transfer("Future Player", "Foreign Club", "Save Club")
    };
    let undated = transfer("Undated Player", "Foreign A", "Foreign B");

    let skipped = pending_skipped(
        &[future, undated],
        Some(&saves),
        NaiveDate::from_ymd_opt(2026, 10, 3).unwrap(),
    );

    assert_eq!(
        skipped
            .iter()
            .map(|item| {
                (
                    item.player_name.as_str(),
                    item.reason.as_str(),
                    item.relevant,
                    item.fotmob_player_id,
                )
            })
            .collect::<Vec<_>>(),
        [
            ("Future Player", "not_yet_effective", true, Some(555)),
            ("Undated Player", "undated_in_window", false, None),
        ]
    );
    // Only the date-pending event can resolve on its own.
    assert_eq!(
        skipped
            .iter()
            .map(|item| item.retryable)
            .collect::<Vec<_>>(),
        [true, false]
    );

    let mut reversed: Vec<_> = skipped.into_iter().rev().collect();
    sort_skipped(&mut reversed);
    assert_eq!(
        reversed
            .iter()
            .map(|item| item.player_name.as_str())
            .collect::<Vec<_>>(),
        ["Future Player", "Undated Player"]
    );
}

// py: test_gameplan_overrides_come_from_snapshot_positions_not_names
#[test]
fn gameplan_overrides_come_from_snapshot_positions_not_names() {
    let winger = positioned("Example Winger", 9001, "RW");
    let back = positioned("Example Back", 9002, "LB");
    let sub = positioned("Example Sub", 9003, "CM");
    let mut snapshot = snapshot(
        "Example FC",
        42,
        vec![winger.clone(), back.clone(), sub.clone()],
    );
    snapshot.starter_members = vec![winger, back];
    snapshot.sub_members = vec![sub];
    let matcher = matcher(&[("Example Back", 1002), ("Example Sub", 1003)], &[]);
    let registered = |player_id: u32| match player_id {
        1001 | 1003 => Some(Position::Cmf),
        1002 => Some(Position::Lb),
        _ => None,
    };

    let preferences = plan_gameplan_preferences(
        &[snapshot],
        &matcher,
        &RosterMap::from([(101, vec![1001, 1002, 1003])]),
        &club_set(&[101]),
        &HashMap::from([(42, 101)]),
        80.0,
        // The winger resolves by FotMob identity even with no name match.
        &HashMap::from([(9001, 1001)]),
        &registered,
    );

    assert_eq!(
        preferences.starters,
        HashMap::from([(101, vec![1001, 1002])])
    );
    assert_eq!(preferences.bench, HashMap::from([(101, vec![1003])]));
    // Only the starter whose live position disagrees with Player.bin moves.
    assert_eq!(
        preferences.position_overrides,
        HashMap::from([(101, HashMap::from([(1001, "RW".to_owned())]))])
    );
}

// py: test_gameplan_formations_require_supported_shapes_and_unambiguous_mapping
#[test]
fn gameplan_formations_require_supported_shapes_and_unambiguous_mapping() {
    let shape = |team_id: i64, formation: &str, complete: bool| {
        let mut snapshot = snapshot(&format!("Team {team_id}"), team_id, Vec::new());
        snapshot.complete = complete;
        snapshot.formation = Some(formation.to_owned());
        snapshot
    };
    let snapshots = [
        shape(42, "04-2-03-1", true),
        shape(43, "4-3-3", false),
        shape(44, "2-4-4", true),
        shape(45, "4-3-3", true),
        shape(46, "4-4-2", true),
        shape(46, "4-2-3-1", true),
    ];

    let planned = plan_gameplan_formations(
        &snapshots,
        &club_set(&[101, 102, 103, 104]),
        &HashMap::from([(42, 101), (43, 102), (44, 103), (46, 104)]),
    );

    assert_eq!(planned, [(101, "4-2-3-1".to_owned())]);
}

#[test]
fn gameplan_tactics_fail_closed_on_conflicting_profiles_and_keep_order() {
    let updates = [
        // Unsorted input comes back sorted by setting name.
        tactic(43, &[("press", 3), ("line", 2)]),
        tactic(42, &[("press", 1)]),
        tactic(44, &[("line", 4)]),
        tactic(42, &[("press", 2)]),
        // Same profile again (any order) is not a conflict.
        tactic(43, &[("line", 2), ("press", 3)]),
        // A conflicted club stays dropped, even when the first profile returns.
        tactic(42, &[("press", 1)]),
        // Not a represented club / not bound / no settings.
        tactic(45, &[("line", 1)]),
        tactic(99, &[("line", 1)]),
        tactic(46, &[]),
    ];

    let planned = plan_gameplan_tactics(
        &updates,
        &club_set(&[101, 103, 104, 105]),
        &HashMap::from([(42, 101), (43, 103), (44, 104), (45, 999), (46, 105)]),
    );

    assert_eq!(
        planned,
        [
            (103, vec![("line".to_owned(), 2), ("press".to_owned(), 3)]),
            (104, vec![("line".to_owned(), 4)]),
        ]
    );
}
