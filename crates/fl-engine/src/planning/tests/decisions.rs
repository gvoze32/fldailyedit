//! Roster-action decisions and plans.

use std::collections::{HashMap, HashSet};
use std::ops::Range;

use fl_save::model::TeamData;
use fl_sources::model::{Transfer, TransferType};

use super::support::*;
use crate::planning::{
    MatchedTransfer, OverflowSelector, RosterAction, Side, SkippedTransfer,
    build_superseded_loan_sources, decide_roster_action, event_order_key, sort_events,
};

const PSG: u32 = 114;
const TOTTENHAM: u32 = 179;
const JUVENTUS: u32 = 120;

/// `[*head, *range]` as one roster list.
fn with(head: &[u32], range: Range<u32>) -> Vec<u32> {
    head.iter().copied().chain(range).collect()
}

fn club_ids_of(rosters: &[TeamData]) -> HashSet<u32> {
    rosters.iter().map(|t| t.team_id).collect()
}

fn no_live() -> HashMap<u32, HashSet<u32>> {
    HashMap::new()
}

/// A dated club move of `Player <id>`.
fn arrival(player_id: u32, source: u32, destination: u32) -> MatchedTransfer {
    matched(
        Transfer {
            player_name: format!("Player {player_id}"),
            from_club: "Source".to_owned(),
            to_club: "Destination".to_owned(),
            date: "2026-08-02".to_owned(),
            ..Transfer::default()
        },
        Some(player_id),
        Side::Team(source),
        Side::Team(destination),
    )
}

/// Always proposes the same release candidate.
struct FixedOverflow(u32);

impl OverflowSelector for FixedOverflow {
    fn find_candidate(&self, _: u32, _: u32, _: &[u32], _: &HashSet<u32>) -> Option<u32> {
        Some(self.0)
    }
}

/// Ranks overflow candidates by the live
/// file's slot-to-role map (highest role first).
struct LiveRoleOverflow {
    roles: Vec<usize>,
}

impl OverflowSelector for LiveRoleOverflow {
    fn find_candidate(
        &self,
        _team_id: u32,
        incoming: u32,
        roster: &[u32],
        protected: &HashSet<u32>,
    ) -> Option<u32> {
        roster
            .iter()
            .copied()
            .enumerate()
            .filter(|&(_, player)| {
                player != 0 && player != incoming && !protected.contains(&player)
            })
            .max_by_key(|&(slot, _)| self.roles[slot])
            .map(|(_, player)| player)
    }
}

fn identity_roles() -> LiveRoleOverflow {
    LiveRoleOverflow {
        roles: (0..40).collect(),
    }
}

/// `(action, overflow player)` pairs of a plan.
fn overflows(output: &crate::planning::RosterPlanOutput) -> Vec<(RosterAction, Option<u32>)> {
    output
        .actions
        .iter()
        .map(|a| (a.action, a.overflow_player_id))
        .collect()
}

// py: test_decide_roster_action_returns_explicit_reason
#[test]
fn decide_roster_action_returns_explicit_reason() {
    use RosterAction::{Add, Move, Noop, Release, ShirtUpdate, Skip};
    use TransferType::{FreeTransfer, ShirtNumberUpdate, Transfer};

    #[allow(clippy::type_complexity)]
    let cases: [(
        Option<u32>,
        Option<u32>,
        Option<u32>,
        TransferType,
        RosterAction,
        &str,
    ); 12] = [
        (Some(10), Some(10), Some(20), Transfer, Move, ""),
        (Some(20), Some(10), Some(20), Transfer, Noop, ""),
        (
            Some(30),
            Some(10),
            Some(20),
            Transfer,
            Skip,
            "current_club_mismatch",
        ),
        // Unattached in the save with a known save source: sign him.
        (None, Some(10), Some(20), Transfer, Add, ""),
        (None, None, Some(20), FreeTransfer, Add, ""),
        (Some(20), None, Some(20), FreeTransfer, Noop, ""),
        (
            Some(30),
            None,
            Some(20),
            FreeTransfer,
            Skip,
            "already_registered_elsewhere",
        ),
        (Some(10), Some(10), None, FreeTransfer, Release, ""),
        (None, Some(10), None, FreeTransfer, Noop, ""),
        (
            Some(30),
            Some(10),
            None,
            FreeTransfer,
            Skip,
            "current_club_mismatch",
        ),
        (
            Some(20),
            Some(20),
            Some(20),
            ShirtNumberUpdate,
            ShirtUpdate,
            "",
        ),
        (
            Some(30),
            Some(20),
            Some(20),
            ShirtNumberUpdate,
            Skip,
            "shirt_player_not_at_club",
        ),
    ];
    for (current, source, destination, transfer_type, action, reason) in cases {
        assert_eq!(
            decide_roster_action(current, source, destination, transfer_type, &HashSet::new()),
            (action, reason),
            "current={current:?} source={source:?} destination={destination:?} type={}",
            transfer_type.as_str(),
        );
    }
}

// py: test_new_parent_club_transfer_can_reconcile_stale_loan_roster
#[test]
fn new_parent_club_transfer_can_reconcile_stale_loan_roster() {
    let loan = club_match(
        PSG,
        TOTTENHAM,
        "2025-09-01T19:27:00Z",
        TransferType::Loan,
        true,
    );
    let permanent = club_match(
        PSG,
        JUVENTUS,
        "2026-08-02T18:40:10Z",
        TransferType::Transfer,
        false,
    );

    // Source authorization is date-based, not dependent on API item ordering.
    let allowed = build_superseded_loan_sources(&[permanent, loan], &[]);

    assert_eq!(allowed[0], club_set(&[TOTTENHAM]));
    assert_eq!(
        decide_roster_action(
            Some(TOTTENHAM),
            Some(PSG),
            Some(JUVENTUS),
            TransferType::Transfer,
            &allowed[0],
        ),
        (RosterAction::Move, "")
    );
}

// py: test_unrelated_stale_roster_remains_fail_closed
#[test]
fn unrelated_stale_roster_remains_fail_closed() {
    let unrelated = 999;
    let loan = club_match(PSG, TOTTENHAM, "2025-09-01", TransferType::Loan, true);
    let permanent = club_match(PSG, JUVENTUS, "2026-08-02", TransferType::Transfer, false);
    let allowed = build_superseded_loan_sources(&[loan, permanent], &[]);

    assert_eq!(
        decide_roster_action(
            Some(unrelated),
            Some(PSG),
            Some(JUVENTUS),
            TransferType::Transfer,
            &allowed[1],
        ),
        (RosterAction::Skip, "current_club_mismatch")
    );
}

// py: test_historical_loan_log_can_reconcile_a_later_run
#[test]
fn historical_loan_log_can_reconcile_a_later_run() {
    let permanent = club_match(
        PSG,
        JUVENTUS,
        "2026-08-02T18:40:10Z",
        TransferType::Transfer,
        false,
    );
    let history = [history_entry(
        115254,
        PSG,
        TOTTENHAM,
        "loan",
        "2025-09-01T19:27:00Z",
    )];

    let allowed = build_superseded_loan_sources(&[permanent], &history);

    assert_eq!(allowed[0], club_set(&[TOTTENHAM]));
}

// py: test_roster_plan_simulates_chained_moves_chronologically
#[test]
fn roster_plan_simulates_chained_moves_chronologically() {
    let loan = club_match(
        PSG,
        TOTTENHAM,
        "2025-09-01T19:27:00Z",
        TransferType::Loan,
        true,
    );
    let permanent = club_match(
        PSG,
        JUVENTUS,
        "2026-08-02T18:40:10Z",
        TransferType::Transfer,
        false,
    );
    let rosters = [
        team(PSG, &with(&[115254], 200001..200017)),
        team(TOTTENHAM, &ids(300001..300017)),
        team(JUVENTUS, &ids(400001..400017)),
    ];
    let club_ids = club_ids_of(&rosters);

    let output = plan(
        &[loan, permanent],
        &rosters,
        &club_ids,
        &NoOverflow,
        &[],
        true,
        &no_live(),
    );

    assert_eq!(
        actions(&output),
        vec![("move", Some(PSG)), ("move", Some(TOTTENHAM))]
    );
}

// py: test_roster_plan_refuses_to_reduce_a_club_below_sixteen_players
#[test]
fn roster_plan_refuses_to_reduce_a_club_below_sixteen_players() {
    let (source, destination) = (10, 20);
    let event = club_match(
        source,
        destination,
        "2026-08-02",
        TransferType::Transfer,
        false,
    );
    let rosters = [
        team(source, &with(&[115254], 200001..200016)),
        team(destination, &ids(300001..300017)),
    ];
    let club_ids = club_ids_of(&rosters);

    let output = plan(
        &[event],
        &rosters,
        &club_ids,
        &NoOverflow,
        &[],
        true,
        &no_live(),
    );

    assert_eq!(
        (output.actions[0].action, output.actions[0].reason.as_str()),
        (RosterAction::Skip, "roster_minimum")
    );
    let rows: Vec<(&str, bool)> = output
        .skipped
        .iter()
        .map(|row| (row.reason.as_str(), row.relevant))
        .collect();
    assert_eq!(rows, vec![("roster_minimum", true)]);
}

// py: test_roster_plan_enables_overflow_release_by_default
#[test]
fn roster_plan_enables_overflow_release_by_default() {
    let (source, destination) = (10, 20);
    let event = club_match(
        source,
        destination,
        "2026-08-02",
        TransferType::Transfer,
        false,
    );
    let rosters = [
        team(source, &with(&[115254], 200001..200017)),
        team(destination, &ids(1000..1040)),
    ];
    let club_ids = club_ids_of(&rosters);
    let overflow = FixedOverflow(1030);

    let allowed = plan(
        std::slice::from_ref(&event),
        &rosters,
        &club_ids,
        &overflow,
        &[],
        true,
        &no_live(),
    );
    let blocked = plan(
        std::slice::from_ref(&event),
        &rosters,
        &club_ids,
        &overflow,
        &[],
        false,
        &no_live(),
    );

    assert_eq!(allowed.actions[0].action, RosterAction::Move);
    assert_eq!(allowed.actions[0].overflow_player_id, Some(1030));
    assert_eq!(
        (
            blocked.actions[0].action,
            blocked.actions[0].reason.as_str()
        ),
        (RosterAction::Skip, "destination_roster_full")
    );
}

// py: test_same_day_transfers_sort_by_timestamp
#[test]
fn same_day_transfers_sort_by_timestamp() {
    let later = Transfer {
        date: "2026-08-02T18:00:00Z".to_owned(),
        ..transfer("Player", "B", "C")
    };
    let earlier = Transfer {
        date: "2026-08-02T09:00:00Z".to_owned(),
        ..transfer("Player", "A", "B")
    };
    assert!(event_order_key(&earlier) < event_order_key(&later));

    let mut events = vec![later.clone(), earlier.clone()];
    sort_events(&mut events);

    assert_eq!(events, vec![earlier, later]);
}

// py: test_planner_never_mutates_unresolved_team_identity
#[test]
fn planner_never_mutates_unresolved_team_identity() {
    let partial = matched(
        transfer("Known Player", "Unknown Source", "Destination"),
        Some(1),
        Side::Unresolved,
        Side::Team(30),
    );
    let rosters = [team(20, &ids(1..18)), team(30, &[])];
    let club_ids = club_set(&[20, 30]);

    let output = plan_with_superseded(
        &[partial],
        &rosters,
        &club_ids,
        &NoOverflow,
        &[club_set(&[20])],
        true,
        &no_live(),
    );

    let decided: Vec<(RosterAction, &str)> = output
        .actions
        .iter()
        .map(|a| (a.action, a.reason.as_str()))
        .collect();
    assert_eq!(
        decided,
        vec![(RosterAction::Skip, "source_team_not_matched")]
    );
}

// py: test_roster_plan_second_overflow_keeps_live_roles_of_remaining_players
#[test]
fn roster_plan_second_overflow_keeps_live_roles_of_remaining_players() {
    let (source, destination) = (30, 10);
    // Slot 39 holds a bench player and slot 20 the deepest reserve.
    let mut roles: Vec<usize> = (0..40).collect();
    roles.swap(20, 39);
    let rosters = [
        team(source, &with(&[501, 502], 2001..2019)),
        team(destination, &ids(1000..1040)),
    ];
    let club_ids = club_ids_of(&rosters);

    let output = plan(
        &[
            arrival(501, source, destination),
            arrival(502, source, destination),
        ],
        &rosters,
        &club_ids,
        &LiveRoleOverflow { roles },
        &[],
        true,
        &no_live(),
    );

    assert_eq!(
        overflows(&output),
        vec![
            (RosterAction::Move, Some(1020)),
            (RosterAction::Move, Some(1038))
        ]
    );
}

// py: test_roster_plan_overflow_prefers_player_released_later_in_plan
#[test]
fn roster_plan_overflow_prefers_player_released_later_in_plan() {
    let (source, destination) = (30, 10);
    let rosters = [
        team(source, &with(&[501], 2001..2019)),
        team(destination, &ids(1000..1040)),
    ];
    let club_ids = club_ids_of(&rosters);
    let mut stale = transfer("Player 1005", "Destination", "Free Agent");
    stale.transfer_type = TransferType::SquadRelease;
    let stale_release = matched(stale, Some(1005), Side::Team(destination), Side::Outside);

    let output = plan(
        &[arrival(501, source, destination), stale_release],
        &rosters,
        &club_ids,
        &identity_roles(),
        &[],
        true,
        &no_live(),
    );

    assert_eq!(
        overflows(&output),
        vec![(RosterAction::Move, Some(1005)), (RosterAction::Noop, None)]
    );
}

// py: test_plan_reports_every_skip_with_reason_code_and_relevance
#[test]
fn plan_reports_every_skip_with_reason_code_and_relevance() {
    let save_club = 20;
    let not_in_save = matched(
        Transfer {
            date: "2026-08-01".to_owned(),
            player_id_fotmob: Some(901),
            ..transfer("Youth Prospect", "Outside FC", "Save FC")
        },
        None,
        Side::Outside,
        Side::Team(save_club),
    );
    let foreign_unknown = matched(
        Transfer {
            player_id_fotmob: Some(902),
            ..transfer("Foreign Player", "Abroad A", "Abroad B")
        },
        None,
        Side::Outside,
        Side::Outside,
    );
    let foreign_known = matched(
        transfer("Known Abroad", "Abroad A", "Abroad B"),
        Some(7),
        Side::Outside,
        Side::Outside,
    );
    let unresolved_destination = matched(
        Transfer {
            to_club_id_fotmob: Some(9823),
            ..transfer("Known Player", "Save FC", "Bayern Munich")
        },
        Some(1),
        Side::Team(save_club),
        Side::Unresolved,
    );
    let rosters = [team(save_club, &with(&[1], 100..120))];
    let club_ids = club_set(&[save_club]);

    let output = plan(
        &[
            not_in_save,
            foreign_unknown,
            foreign_known,
            unresolved_destination,
        ],
        &rosters,
        &club_ids,
        &NoOverflow,
        &[],
        true,
        &no_live(),
    );

    assert!(
        output
            .actions
            .iter()
            .all(|a| a.action == RosterAction::Skip)
    );
    let rows: Vec<(&str, &str, bool, Option<i64>)> = output
        .skipped
        .iter()
        .map(|row| {
            (
                row.player_name.as_str(),
                row.reason.as_str(),
                row.relevant,
                row.fotmob_player_id,
            )
        })
        .collect();
    assert_eq!(
        rows,
        vec![
            ("Youth Prospect", "player_not_matched", true, Some(901)),
            ("Foreign Player", "player_not_matched", false, Some(902)),
            ("Known Abroad", "outside_save", false, None),
            ("Known Player", "destination_team_not_matched", true, None),
        ]
    );
    assert!(output.skipped[3].detail.contains("9823"));

    let payload = serde_json::to_value(&output.skipped[0]).unwrap();
    let mut keys: Vec<&str> = payload
        .as_object()
        .unwrap()
        .keys()
        .map(String::as_str)
        .collect();
    keys.sort_unstable();
    // The established keys, plus `retryable`.
    assert_eq!(
        keys,
        vec![
            "candidates",
            "date",
            "detail",
            "fotmob_player_id",
            "from_team",
            "player_name",
            "reason",
            "relevant",
            "retryable",
            "source",
            "to_team",
        ]
    );
    let text = serde_json::to_string(&output.skipped[0]).unwrap();
    let round_trip: SkippedTransfer = serde_json::from_str(&text).unwrap();
    assert_eq!(round_trip.to_team, "Save FC");
    assert_eq!(round_trip, output.skipped[0]);
}

// py: test_plan_reconciles_stale_source_from_destination_live_squad
#[test]
fn plan_reconciles_stale_source_from_destination_live_squad() {
    let (source, actual, destination) = (10, 30, 20);
    let event = club_match(
        source,
        destination,
        "2026-08-02",
        TransferType::Transfer,
        false,
    );
    let rosters = [
        team(10, &ids(1000..1020)),
        team(30, &with(&[115254], 2000..2019)),
        team(20, &ids(3000..3020)),
    ];
    let club_ids = club_ids_of(&rosters);
    let live = HashMap::from([(destination, club_set(&[115254]))]);

    let output = plan(
        std::slice::from_ref(&event),
        &rosters,
        &club_ids,
        &NoOverflow,
        &[],
        true,
        &live,
    );

    assert_eq!(actions(&output), vec![("move", Some(actual))]);
    assert!(output.skipped.is_empty());

    // Without destination evidence the mismatch is reported, not guessed.
    let blocked = plan(
        &[event],
        &rosters,
        &club_ids,
        &NoOverflow,
        &[],
        true,
        &no_live(),
    );
    assert_eq!(
        (
            blocked.actions[0].action,
            blocked.actions[0].reason.as_str()
        ),
        (RosterAction::Skip, "current_club_mismatch")
    );
    assert!(blocked.actions[0].detail.contains("club 30"));
    let reasons: Vec<&str> = blocked
        .skipped
        .iter()
        .map(|row| row.reason.as_str())
        .collect();
    assert_eq!(reasons, vec!["current_club_mismatch"]);
}

// py: test_plan_reconciles_stale_source_from_later_event_at_destination
#[test]
fn plan_reconciles_stale_source_from_later_event_at_destination() {
    let first = club_match(10, 20, "2026-07-01", TransferType::Transfer, false);
    let second = club_match(20, 40, "2026-08-01", TransferType::Transfer, false);
    let rosters = [
        team(10, &ids(1000..1020)),
        team(20, &ids(2000..2020)),
        team(30, &with(&[115254], 3000..3019)),
        team(40, &ids(4000..4020)),
    ];
    let club_ids = club_ids_of(&rosters);

    let output = plan(
        &[first, second],
        &rosters,
        &club_ids,
        &NoOverflow,
        &[],
        true,
        &no_live(),
    );

    assert_eq!(
        actions(&output),
        vec![("move", Some(30)), ("move", Some(20))]
    );
}

// py: test_plan_adds_save_free_agent_with_known_source
#[test]
fn plan_adds_save_free_agent_with_known_source() {
    let event = club_match(10, 20, "2026-08-02", TransferType::Transfer, false);
    let rosters = [team(10, &ids(1000..1020)), team(20, &ids(2000..2020))];
    let club_ids = club_ids_of(&rosters);

    let output = plan(
        &[event],
        &rosters,
        &club_ids,
        &NoOverflow,
        &[],
        true,
        &no_live(),
    );

    assert_eq!(actions(&output), vec![("add", None)]);
}

// py: test_roster_minimum_departure_waits_for_same_plan_backfill
#[test]
fn roster_minimum_departure_waits_for_same_plan_backfill() {
    let departure = club_match(10, 20, "2026-07-01", TransferType::Transfer, false);
    let backfill = arrival(501, 30, 10);
    let rosters = [
        team(10, &with(&[115254], 1000..1015)),
        team(20, &ids(2000..2020)),
        team(30, &with(&[501], 3000..3019)),
    ];
    let club_ids = club_ids_of(&rosters);

    let output = plan(
        &[departure, backfill],
        &rosters,
        &club_ids,
        &NoOverflow,
        &[],
        true,
        &no_live(),
    );

    let order: Vec<(Option<u32>, RosterAction)> = output
        .actions
        .iter()
        .map(|a| (a.matched.player_id, a.action))
        .collect();
    assert_eq!(
        order,
        vec![
            (Some(501), RosterAction::Move),
            (Some(115254), RosterAction::Move)
        ]
    );
    assert!(output.skipped.is_empty());
}

// py: test_overflow_ranking_protects_live_squad_members
#[test]
fn overflow_ranking_protects_live_squad_members() {
    let (source, destination) = (30, 10);
    let rosters = [
        team(source, &with(&[501], 2001..2019)),
        team(destination, &ids(1000..1040)),
    ];
    let club_ids = club_ids_of(&rosters);
    let live = HashMap::from([(destination, club_set(&[1039]))]);

    let output = plan(
        &[arrival(501, source, destination)],
        &rosters,
        &club_ids,
        &identity_roles(),
        &[],
        true,
        &live,
    );

    // Slot 39 ranks first, but its player is in the live squad.
    assert_eq!(overflows(&output), vec![(RosterAction::Move, Some(1038))]);
}
