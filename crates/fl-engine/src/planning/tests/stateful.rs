//! Behavior pinned by the Rust planner alone: loan markers, inferred sources,
//! retryable skips, ambiguous shirt numbers and the canonical event order.

use std::collections::HashMap;

use fl_sources::model::{ScrapeResult, Transfer, TransferType};

use super::support::*;
use crate::planning::{
    PlanInput, Side, SkippedTransfer, is_retryable_reason, plan_transfers, replay_history,
    sort_events,
};

// B9: a returned loan must clear the *parent's* marker.
#[test]
fn history_end_of_loan_clears_the_parent_marker() {
    let (parent, borrower, player) = (10, 20, 3001);
    let history = vec![
        history_entry(player, parent, borrower, "loan", "2025-01-01"),
        history_entry(player, borrower, parent, "end of loan", "2025-06-01"),
    ];
    let replayed = replay_history(&history);
    assert!(!replayed.loans.is_loaned(parent, player));
    assert!(!replayed.loans.is_loaned(borrower, player));
}

#[test]
fn history_loan_without_return_keeps_the_parent_marker() {
    let history = vec![history_entry(3001, 10, 20, "loan", "2025-01-01")];
    assert!(replay_history(&history).loans.is_loaned(10, 3001));
}

// B9 (live loop): after loan A→B and return B→A, a later free-agent signing
// at C must not move the player in the simulation, so a following
// destination-only signal infers A (where the save still has him), not C.
#[test]
fn returned_loan_does_not_let_a_later_event_move_the_player() {
    let players = [("Patrick Finn", 3001)];
    let teams = [
        ("Arsenal", 10),
        ("Brentford", 20),
        ("Chelsea", 30),
        ("Dortmund", 40),
    ];
    let matcher = matcher(&players, &teams);
    let mut run = MatchRun::new(
        &matcher,
        vec![(10, vec![3001]), (20, vec![]), (30, vec![]), (40, vec![])],
        &[10, 20, 30, 40],
    );
    run.transfers = vec![
        dated(
            transfer("Patrick Finn", "Arsenal", "Brentford"),
            "2025-01-01",
            TransferType::Loan,
        ),
        dated(
            transfer("Patrick Finn", "Brentford", "Arsenal"),
            "2025-06-01",
            TransferType::EndOfLoan,
        ),
        dated(
            transfer("Patrick Finn", "Free Agent", "Chelsea"),
            "2025-08-01",
            TransferType::FreeTransfer,
        ),
        {
            let mut signal = registration("Patrick Finn", "Dortmund", 4, 99);
            signal.date = "2025-09-01".to_owned();
            signal.player_id_fotmob = None;
            signal
        },
    ];

    let out = run.run();
    let signal = out.matched.last().expect("signal matched");
    assert_eq!(signal.player_id, Some(3001));
    assert_eq!(signal.from, Side::Team(10), "player still sits at Arsenal");
    assert_eq!(signal.transfer.from_club, "Arsenal");
}

// B10: inferred sources rewrite the matched copy, never the scraped event.
#[test]
fn inferred_source_never_mutates_the_scraped_event() {
    let matcher = matcher(
        &[("Jordan Henderson", 7)],
        &[("Brentford", 10), ("Chelsea", 20)],
    );
    let mut run = MatchRun::new(&matcher, vec![(10, vec![7]), (20, vec![])], &[10, 20]);
    let mut signal = registration("Jordan Henderson", "Chelsea", 20, 1);
    signal.player_id_fotmob = None;
    signal.date = "2026-08-03".to_owned();
    run.transfers = vec![signal.clone()];

    let out = run.run();
    assert_eq!(out.matched[0].transfer.from_club, "Brentford");
    assert_eq!(out.matched[0].transfer.from_club_full_name, "Brentford");
    assert_eq!(run.transfers[0], signal, "input event untouched");
    assert_eq!(run.transfers[0].from_club, "");
}

// B6: only skips that can resolve on their own are retryable.
#[test]
fn only_self_resolving_reasons_are_retryable() {
    for reason in [
        "not_yet_effective",
        "source_snapshot_missing",
        "snapshot_incomplete",
        "snapshot_coverage_low",
        "destination_roster_full",
    ] {
        assert!(is_retryable_reason(reason), "{reason}");
    }
    for reason in [
        "player_not_matched",
        "current_club_mismatch",
        "outside_save",
        "roster_minimum",
        "undated_in_window",
        "outside_scrape_window",
        "provider_identity_conflict",
    ] {
        assert!(!is_retryable_reason(reason), "{reason}");
    }
}

#[test]
fn plan_rows_carry_the_retryable_flag() {
    let mismatch = club_match(10, 20, "2026-08-02", TransferType::Transfer, false);
    let rosters = [
        team(10, &ids(1..20)),
        team(20, &ids(100..119)),
        team(30, &[115254]),
    ];
    let output = plan(
        &[mismatch],
        &rosters,
        &club_set(&[10, 20, 30]),
        &NoOverflow,
        &[],
        false,
        &HashMap::new(),
    );
    let rows: Vec<(&str, bool)> = output
        .skipped
        .iter()
        .map(|row| (row.reason.as_str(), row.retryable))
        .collect();
    assert_eq!(rows, [("current_club_mismatch", false)]);

    // Full destination with overflow disabled is retryable.
    let full: Vec<u32> = ids(100..140);
    let mut source = ids(1..20);
    source.push(115254);
    let mover = club_match(10, 20, "2026-08-02", TransferType::Transfer, false);
    let rosters = [team(10, &source), team(20, &full)];
    let output = plan(
        &[mover],
        &rosters,
        &club_set(&[10, 20]),
        &NoOverflow,
        &[],
        false,
        &HashMap::new(),
    );
    let reasons: Vec<(&str, bool)> = output
        .skipped
        .iter()
        .map(|row| (row.reason.as_str(), row.retryable))
        .collect();
    assert_eq!(reasons, [("destination_roster_full", true)]);
}

#[test]
fn skipped_rows_serialize_with_established_keys_plus_retryable() {
    let row = SkippedTransfer {
        player_name: "P".into(),
        from_team: "A".into(),
        to_team: "B".into(),
        date: "2026-01-01".into(),
        source: "fotmob".into(),
        reason: "not_yet_effective".into(),
        detail: "d".into(),
        relevant: true,
        fotmob_player_id: Some(5),
        candidates: vec!["x".into()],
        retryable: true,
    };
    let json = serde_json::to_string(&row).unwrap();
    assert_eq!(
        json,
        r#"{"player_name":"P","from_team":"A","to_team":"B","date":"2026-01-01","source":"fotmob","reason":"not_yet_effective","detail":"d","relevant":true,"fotmob_player_id":5,"candidates":["x"],"retryable":true}"#
    );
    // Rows written before the flag existed still load.
    let old = r#"{"player_name":"P","from_team":"","to_team":"","date":"","source":"","reason":"outside_save","detail":"","relevant":false,"fotmob_player_id":null,"candidates":[]}"#;
    assert!(
        !serde_json::from_str::<SkippedTransfer>(old)
            .unwrap()
            .retryable
    );
}

// B14: ambiguous shirt observations surface as skipped rows.
#[test]
fn ambiguous_shirt_numbers_are_reported_as_skipped_rows() {
    let matcher = matcher(&[("Pat Shirt", 1)], &[("Arsenal", 10)]);
    let shirt = |number| Transfer {
        player_name: "Pat Shirt".to_owned(),
        from_club: "Arsenal".to_owned(),
        to_club: "Arsenal".to_owned(),
        transfer_type: TransferType::ShirtNumberUpdate,
        shirt_number: Some(number),
        ..Transfer::default()
    };
    let scrape = ScrapeResult {
        roster_updates: vec![shirt(7), shirt(8)],
        ..ScrapeResult::default()
    };
    let rosters = [team(10, &ids(1..20))];
    let clubs = club_set(&[10]);
    let names = HashMap::new();
    let plan = plan_transfers(&PlanInput {
        scrape: &scrape,
        matcher: &matcher,
        club_identity: None,
        threshold: 80.0,
        rosters: &rosters,
        club_ids: &clubs,
        history: &[],
        player_names: &names,
        overflow: &NoOverflow,
        allow_overflow_release: true,
        today: chrono::NaiveDate::from_ymd_opt(2026, 10, 24).unwrap(),
    });
    assert_eq!(plan.counts.duplicate_shirt_dropped, 2);
    assert!(plan.roster_actions.is_empty());
    let reasons: Vec<&str> = plan.skipped.iter().map(|row| row.reason.as_str()).collect();
    assert_eq!(
        reasons,
        ["shirt_number_ambiguous", "shirt_number_ambiguous"]
    );
    assert!(
        plan.skipped
            .iter()
            .all(|row| row.relevant && !row.retryable)
    );
}

// One canonical order: dated chronologically, undated after, shirts last.
#[test]
fn events_sort_dated_then_undated_then_shirt_updates() {
    let mut events = vec![
        transfer("shirt", "A", "A"),
        transfer("undated", "A", "B"),
        transfer("late", "A", "B"),
        transfer("early", "A", "B"),
    ];
    events[0].transfer_type = TransferType::ShirtNumberUpdate;
    events[2].date = "2026-08-02T12:00:00Z".to_owned();
    events[3].date = "2026-08-02T09:00:00Z".to_owned();
    sort_events(&mut events);
    let names: Vec<&str> = events.iter().map(|e| e.player_name.as_str()).collect();
    assert_eq!(names, ["early", "late", "undated", "shirt"]);
}

#[test]
fn history_entries_without_dates_replay_before_dated_ones() {
    let mut undated = history_entry(1, 10, 20, "transfer", "");
    undated.timestamp = String::new();
    let dated = history_entry(1, 20, 30, "transfer", "2026-01-01");
    let replayed = replay_history(&[dated, undated]);
    // The undated 10→20 move has no timestamp, so it records no route.
    assert!(!replayed.permanent_routes.contains_key(&(1, 10)));
    assert_eq!(replayed.permanent_routes[&(1, 20)].len(), 1);
    assert_eq!(replayed.permanent_routes.len(), 1);
}
