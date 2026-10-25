//! matching scenarios.

use std::collections::{HashMap, HashSet};

use chrono::NaiveDate;
use fl_sources::model::{ScrapeResult, Transfer, TransferType, VerificationStatus};

use super::support::*;
use crate::matcher::PlayerAttrs;
use crate::planning::{
    ClubIdentity, HistoryEntry, PlanInput, PlayerHit, PlayerMatcher, PlayerQuery, Resolution, Side,
    TeamHit, TeamMatcher, TeamResolution, build_superseded_loan_sources, match_transfer_team,
    plan_transfers,
};

fn resolved(side: Side, name: &str, confidence: f64) -> TeamResolution {
    TeamResolution {
        side,
        name: name.to_owned(),
        confidence,
    }
}

/// One player id per matched event.
fn player_ids(events: &[crate::planning::MatchedTransfer]) -> Vec<Option<u32>> {
    events.iter().map(|item| item.player_id).collect()
}

/// No superseded loan sources for `count` matches.
fn no_superseded(count: usize) -> Vec<HashSet<u32>> {
    vec![HashSet::new(); count]
}

fn attrs(position: &str, age: u32) -> PlayerAttrs {
    PlayerAttrs {
        position: position.to_owned(),
        nationality: String::new(),
        age,
    }
}

/// Scripted club-name answers.
struct ScriptedTeams;

impl TeamMatcher for ScriptedTeams {
    fn match_team(&self, name: &str, _threshold: Option<f64>) -> TeamHit {
        let (id, display, confidence) = match name {
            "Paris Saint-Germain" | "PSG" => (Some(114), "Paris Saint-Germain", 100.0),
            "Conflicting short" => (Some(999), "Wrong Club", 100.0),
            "Similar Club" => (Some(114), "Paris Saint-Germain", 90.0),
            "Ambiguous Club" => (None, "", 98.0),
            _ => (None, "", 0.0),
        };
        TeamHit {
            id,
            name: display.to_owned(),
            confidence,
        }
    }

    fn team_name(&self, _team_id: u32) -> &str {
        ""
    }
}

/// `ClubIdentityIndex` double whose name lookup goes through any team matcher.
struct BoundIdentity<'a> {
    bindings: Vec<(i64, u32)>,
    matcher: &'a dyn TeamMatcher,
}

impl ClubIdentity for BoundIdentity<'_> {
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
        let found = self.matcher.match_team(name, None);
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

// py: test_team_matching_uses_full_name_and_rejects_conflicts
#[test]
fn team_matching_uses_full_name_and_rejects_conflicts() {
    let matcher = ScriptedTeams;
    assert_eq!(
        match_transfer_team(&matcher, "PSG", "Paris Saint-Germain", None, None),
        resolved(Side::Team(114), "Paris Saint-Germain", 100.0)
    );
    assert_eq!(
        match_transfer_team(
            &matcher,
            "Conflicting short",
            "Paris Saint-Germain",
            None,
            None
        )
        .side,
        Side::Unresolved
    );
    // A FotMob club whose save-name match is already bound to another
    // FotMob club is a different club: unsure, never a confident match.
    let bound_elsewhere = BoundIdentity {
        bindings: vec![(1234, 114)],
        matcher: &matcher,
    };
    assert_eq!(
        match_transfer_team(
            &matcher,
            "PSG",
            "Paris Saint-Germain",
            Some(9847),
            Some(&bound_elsewhere),
        ),
        resolved(Side::Unresolved, "", 0.0)
    );
    assert_eq!(
        match_transfer_team(&matcher, "Similar Club", "", None, None).side,
        Side::Unresolved
    );
    assert_eq!(
        match_transfer_team(&matcher, "Ambiguous Club", "", None, None).side,
        Side::Unresolved
    );
    assert_eq!(
        match_transfer_team(
            &matcher,
            "Free Agent",
            "Free Agent",
            Some(2),
            Some(&bound_elsewhere)
        ),
        resolved(Side::Outside, "", 100.0)
    );

    let barcelona = BoundIdentity {
        bindings: vec![(8634, 108)],
        matcher: &matcher,
    };
    assert_eq!(
        match_transfer_team(
            &matcher,
            "Barcelona",
            "Barcelona",
            Some(8634),
            Some(&barcelona)
        ),
        resolved(Side::Team(108), "Barcelona", 100.0)
    );
}

/// A matcher that must never be asked for a club name.
struct IdOnlyMatcher;

impl TeamMatcher for IdOnlyMatcher {
    fn match_team(&self, _name: &str, _threshold: Option<f64>) -> TeamHit {
        panic!("validated team IDs must bypass name matching");
    }

    fn team_name(&self, _team_id: u32) -> &str {
        ""
    }
}

// py: test_validated_fotmob_team_id_skips_ambiguous_name_lookup
#[test]
fn validated_fotmob_team_id_skips_ambiguous_name_lookup() {
    let bound = identity(&[(8634, 108)], None);
    assert_eq!(
        match_transfer_team(
            &IdOnlyMatcher,
            "Barcelona",
            "Barcelona",
            Some(8634),
            Some(&bound)
        ),
        resolved(Side::Team(108), "Barcelona", 100.0)
    );

    let unresolved = matched(
        transfer("Player", "A", "B"),
        Some(1),
        Side::Team(10),
        Side::Unresolved,
    );
    assert!(!unresolved.is_fully_matched());
}

/// Matches `Destination` and nothing else; never finds a player.
struct DestinationOnly;

impl TeamMatcher for DestinationOnly {
    fn match_team(&self, name: &str, _threshold: Option<f64>) -> TeamHit {
        if name == "Destination" {
            return TeamHit {
                id: Some(102),
                name: "Destination".to_owned(),
                confidence: 100.0,
            };
        }
        TeamHit::none(100.0)
    }

    fn team_name(&self, team_id: u32) -> &str {
        if team_id == 102 { "Destination" } else { "" }
    }
}

impl PlayerMatcher for DestinationOnly {
    fn match_player(&self, _name: &str, _query: &PlayerQuery<'_>) -> PlayerHit {
        PlayerHit::none(0.0)
    }
}

// py: test_match_and_plan_retains_partial_matches_for_safety_accounting
#[test]
fn match_and_plan_retains_partial_matches_for_safety_accounting() {
    let scrape = ScrapeResult {
        transfers: vec![transfer("Unknown Player", "Free Agent", "Destination")],
        ..ScrapeResult::default()
    };
    let rosters = [team(102, &[])];
    let clubs = club_set(&[102]);
    let names = HashMap::new();
    let out = plan_transfers(&PlanInput {
        scrape: &scrape,
        matcher: &DestinationOnly,
        club_identity: None,
        threshold: 80.0,
        rosters: &rosters,
        club_ids: &clubs,
        history: &[],
        player_names: &names,
        overflow: &NoOverflow,
        allow_overflow_release: false,
        today: NaiveDate::from_ymd_opt(2026, 10, 24).unwrap(),
    });

    assert!(out.fully_matched.is_empty());
    let planned: Vec<_> = out
        .roster_actions
        .iter()
        .map(|item| (item.action.as_str(), item.reason.as_str()))
        .collect();
    assert_eq!(planned, [("skip", "player_not_matched")]);
}

/// Bayern Munich at 88%: similar to a save club, never confidently it.
struct WeakMatcher;

impl TeamMatcher for WeakMatcher {
    fn match_team(&self, name: &str, _threshold: Option<f64>) -> TeamHit {
        if name == "Bayern Munich" {
            return TeamHit {
                id: Some(30),
                name: "Bayern München".to_owned(),
                confidence: 88.0,
            };
        }
        TeamHit::none(0.0)
    }

    fn team_name(&self, _team_id: u32) -> &str {
        ""
    }
}

// py: test_weak_unvalidated_club_name_is_unresolved_never_a_release
#[test]
fn weak_unvalidated_club_name_is_unresolved_never_a_release() {
    // Without an identity index, a weak name stays unresolved even when the
    // FotMob club ID is unknown.
    assert_eq!(
        match_transfer_team(&WeakMatcher, "Bayern Munich", "", Some(9823), None).side,
        Side::Unresolved
    );

    let m = matcher(
        &[("Known Player", 3001)],
        &[("Save FC", 10), ("Bayern München", 30)],
    );
    let mut roster = vec![3001];
    roster.extend(3002..3020);
    let bayern = ids(4001..4020);
    let mut known = transfer("Known Player", "Save FC", "Bayern Munich");
    known.date = "2026-08-01".to_owned();
    known.from_club_id_fotmob = Some(111);
    known.to_club_id_fotmob = Some(9823);

    let mut names = HashMap::new();
    names.insert("Bayern Munich".to_owned(), Resolution::Unresolved);
    let bound = FakeIdentity {
        bindings: vec![(111, 10)],
        names,
        matcher: Some(&m),
    };
    let mut run = MatchRun::new(
        &m,
        vec![(10, roster.clone()), (30, bayern.clone())],
        &[10, 30],
    );
    run.transfers = vec![known];
    run.identity = Some(&bound);
    let out = run.run();

    let rosters = [team(10, &roster), team(30, &bayern)];
    let planned = plan_with_superseded(
        &out.matched,
        &rosters,
        &club_set(&[10, 30]),
        &NoOverflow,
        &no_superseded(out.matched.len()),
        true,
        &HashMap::new(),
    );

    assert_eq!(out.matched[0].to, Side::Unresolved);
    assert!(!out.matched[0].is_release());
    let steps: Vec<_> = planned
        .actions
        .iter()
        .map(|item| (item.action.as_str(), item.reason.as_str()))
        .collect();
    assert_eq!(steps, [("skip", "destination_team_not_matched")]);
    let skipped: Vec<_> = out
        .report
        .skipped
        .iter()
        .chain(&planned.skipped)
        .map(|item| (item.reason.as_str(), item.relevant))
        .collect();
    assert_eq!(skipped, [("destination_team_not_matched", true)]);
}

// py: test_stateful_matching_keeps_identity_across_loan_chain
#[test]
fn stateful_matching_keeps_identity_across_loan_chain() {
    let m = matcher(
        &[("Patrick", 3001), ("Patrick", 3002)],
        &[("Parent FC", 10), ("Loan FC", 20), ("Next FC", 30)],
    );
    let loan = dated(
        transfer("Patrick", "Parent FC", "Loan FC"),
        "2025-09-01T10:00:00Z",
        TransferType::Loan,
    );
    let sale = Transfer {
        date: "2026-08-02T10:00:00Z".to_owned(),
        ..transfer("Patrick", "Parent FC", "Next FC")
    };
    let mut run = MatchRun::new(
        &m,
        vec![
            (10, vec![3001]),
            (20, vec![]),
            (30, vec![]),
            (40, vec![3002]),
        ],
        &[10, 20, 30, 40],
    );
    run.transfers = vec![loan, sale];

    let out = run.run();

    assert_eq!(player_ids(&out.matched), [Some(3001), Some(3001)]);
}

// py: test_stateful_matching_moves_unique_current_squad_registration
#[test]
fn stateful_matching_moves_unique_current_squad_registration() {
    let m = matcher(
        &[("Anderson Lopes", 3001)],
        &[("Old Club", 10), ("Vissel Kobe", 20)],
    );
    let signal = registration("Anderson Lopes", "Vissel Kobe", 4688, 498456);
    let mut old_club = vec![3001];
    old_club.extend(3002..3019);
    let bound = identity(&[(4688, 20)], Some(&m));
    let mut run = MatchRun::new(&m, vec![(10, old_club.clone()), (20, vec![])], &[10, 20]);
    run.transfers = vec![signal];
    run.identity = Some(&bound);

    let out = run.run();

    assert_eq!(out.matched[0].player_id, Some(3001));
    assert_eq!(out.matched[0].from, Side::Team(10));
    assert_eq!(out.matched[0].to, Side::Team(20));
    let rosters = [team(10, &old_club), team(20, &[])];
    let planned = plan_with_superseded(
        &out.matched,
        &rosters,
        &club_set(&[10, 20]),
        &NoOverflow,
        &no_superseded(out.matched.len()),
        true,
        &HashMap::new(),
    );
    assert_eq!(actions(&planned), [("move", Some(10))]);
}

// py: test_stateful_matching_rejects_stale_provider_identity_with_conflicting_metadata
#[test]
fn stateful_matching_rejects_stale_provider_identity_with_conflicting_metadata() {
    let m = matcher_with_attrs(
        &[("Brian Arias", 90158)],
        HashMap::from([(90158, attrs("CB", 19))]),
        &[("Old FC", 10), ("New FC", 20)],
    );
    let event = Transfer {
        player_id_fotmob: Some(777),
        position: "CM".to_owned(),
        age: 20,
        ..transfer("Brian Fariñas", "Old FC", "New FC")
    };
    let mut run = MatchRun::new(&m, vec![(10, vec![90158]), (20, vec![])], &[10, 20]);
    run.transfers = vec![event];
    run.history = vec![HistoryEntry {
        player_id: Some(90158),
        fotmob_player_id: Some(777),
        player_name: "Brian Arias".to_owned(),
        from_team_id: Some(10),
        to_team_id: Some(20),
        transfer_type: "transfer".to_owned(),
        transfer_date: "2026-08-01".to_owned(),
        ..HistoryEntry::default()
    }];

    let out = run.run();

    assert_eq!(out.matched.len(), 1);
    assert_eq!(out.matched[0].player_id, None);
}

// py: test_stateful_matching_rejects_same_name_player_from_another_age_group
#[test]
fn stateful_matching_rejects_same_name_player_from_another_age_group() {
    let m = matcher_with_attrs(
        &[("Reece James", 126046)],
        HashMap::from([(126046, attrs("RB", 26))]),
        &[("Chelsea", 102), ("Sheffield Wednesday", 394)],
    );
    let signal = Transfer {
        position: "Defender".to_owned(),
        age: 32,
        ..registration("Reece James", "Sheffield Wednesday", 10163, 463871)
    };
    let bound = identity(&[(10163, 394)], Some(&m));
    let mut run = MatchRun::new(&m, vec![(102, vec![126046]), (394, vec![])], &[102, 394]);
    run.transfers = vec![signal];
    run.identity = Some(&bound);

    let out = run.run();

    assert_eq!(out.matched[0].player_id, None);
    assert!(!out.matched[0].is_fully_matched());
}

/// An enabled destination-only registration with explicit proof.
fn registration_with_metadata(
    club: &str,
    position: &str,
    age: u32,
    to_fotmob: i64,
    fotmob_player: i64,
    proof: &str,
) -> Transfer {
    Transfer {
        player_name: "João Pedro".to_owned(),
        to_club: club.to_owned(),
        transfer_type: TransferType::SquadRegistration,
        position: position.to_owned(),
        age,
        to_club_id_fotmob: Some(to_fotmob),
        player_id_fotmob: Some(fotmob_player),
        proof_urls: vec![proof.to_owned()],
        verification_status: VerificationStatus::Enabled,
        infer_from_current_roster: true,
        ..Transfer::default()
    }
}

// py: test_stateful_matching_rejects_duplicate_name_registration_chain
#[test]
fn stateful_matching_rejects_duplicate_name_registration_chain() {
    let m = matcher_with_attrs(
        &[
            ("João Pedro", 3001),
            ("João Pedro", 3002),
            ("João Pedro", 3003),
        ],
        HashMap::from([(3001, attrs("CF", 24))]),
        &[("Chelsea", 10), ("Casa Pia", 20), ("Sao Bernardo", 30)],
    );
    let registrations = vec![
        registration_with_metadata(
            "Chelsea",
            "Attacker",
            24,
            100,
            1001,
            "https://example.test/chelsea",
        ),
        registration_with_metadata(
            "Casa Pia",
            "Midfielder",
            21,
            200,
            1002,
            "https://example.test/casa-pia",
        ),
        registration_with_metadata(
            "Sao Bernardo",
            "Defender",
            21,
            300,
            1003,
            "https://example.test/sao-bernardo",
        ),
    ];
    let bound = identity(&[(100, 10), (200, 20), (300, 30)], Some(&m));
    let mut run = MatchRun::new(
        &m,
        vec![(10, vec![3001]), (20, vec![]), (30, vec![])],
        &[10, 20, 30],
    );
    run.transfers = registrations;
    run.identity = Some(&bound);
    run.history = [1001, 1002, 1003]
        .into_iter()
        .map(|fotmob| HistoryEntry {
            player_id: Some(3001),
            fotmob_player_id: Some(fotmob),
            ..HistoryEntry::default()
        })
        .collect();
    run.player_names = HashMap::from([(3001, "João Pedro".to_owned())]);

    let out = run.run();

    assert_eq!(player_ids(&out.matched), [Some(3001), None, None]);
}

// py: test_stateful_matching_reconciles_parent_sale_before_loan
#[test]
fn stateful_matching_reconciles_parent_sale_before_loan() {
    let (parent, loan_club, new_parent) = (234, 382, 102);
    let player_id = 172021;
    let m = matcher_with_attrs(
        &[("Honest Ahanor", player_id)],
        HashMap::from([(player_id, attrs("CB", 0))]),
        &[
            ("Atalanta", parent),
            ("Crystal Palace", loan_club),
            ("Chelsea", new_parent),
        ],
    );
    let loan = Transfer {
        player_id_fotmob: Some(1669629),
        position: "CB".to_owned(),
        ..dated(
            transfer("Honest Ahanor", "Atalanta", "Crystal Palace"),
            "2026-09-01T15:26:32Z",
            TransferType::Loan,
        )
    };
    let sale = Transfer {
        date: "2026-09-01T13:21:39Z".to_owned(),
        player_id_fotmob: Some(1669629),
        position: "CB".to_owned(),
        ..transfer("Honest Ahanor", "Atalanta", "Chelsea")
    };
    let mut parent_roster = vec![player_id];
    parent_roster.extend(200001..200017);
    let player_map = vec![
        (parent, parent_roster),
        (loan_club, ids(300001..300017)),
        (new_parent, ids(400001..400017)),
    ];
    let all: Vec<u32> = player_map.iter().map(|(team_id, _)| *team_id).collect();
    let mut run = MatchRun::new(&m, player_map.clone(), &all);
    run.transfers = vec![loan, sale];

    let out = run.run();

    let routes: Vec<_> = out
        .matched
        .iter()
        .map(|item| (item.transfer.transfer_type.as_str(), item.from, item.to))
        .collect();
    assert_eq!(
        routes,
        [
            ("transfer", Side::Team(parent), Side::Team(new_parent)),
            ("loan", Side::Team(new_parent), Side::Team(loan_club)),
        ]
    );

    let rosters: Vec<_> = player_map
        .iter()
        .map(|(team_id, roster)| team(*team_id, roster))
        .collect();
    let planned = plan_with_superseded(
        &out.matched,
        &rosters,
        &club_set(&all),
        &NoOverflow,
        &no_superseded(out.matched.len()),
        true,
        &HashMap::new(),
    );
    assert_eq!(
        actions(&planned),
        [("move", Some(parent)), ("move", Some(new_parent))]
    );
}

// py: test_stateful_matching_recovers_renamed_player_from_fotmob_history
#[test]
fn stateful_matching_recovers_renamed_player_from_fotmob_history() {
    let m = matcher(
        &[("Legacy Database Name", 3001)],
        &[("Old FC", 10), ("New FC", 20)],
    );
    let renamed = Transfer {
        player_id_fotmob: Some(777),
        ..transfer("Completely New Public Name", "Old FC", "New FC")
    };
    let mut run = MatchRun::new(&m, vec![(10, vec![3001]), (20, vec![])], &[10, 20]);
    run.transfers = vec![renamed];
    // The logged source team id was malformed (so absent) but the identity
    // stays valid.
    run.history = vec![HistoryEntry {
        player_id: Some(3001),
        fotmob_player_id: Some(777),
        player_name: "Legacy Database Name".to_owned(),
        ..HistoryEntry::default()
    }];

    let out = run.run();

    assert_eq!(out.matched[0].player_id, Some(3001));
    assert_eq!(out.matched[0].player_confidence, 100.0);
}

// py: test_stateful_matching_rejects_conflicting_fotmob_history
#[test]
fn stateful_matching_rejects_conflicting_fotmob_history() {
    let m = matcher(
        &[("First Player", 3001), ("Second Player", 3002)],
        &[("Old FC", 10), ("New FC", 20)],
    );
    let alias = Transfer {
        player_id_fotmob: Some(777),
        ..transfer("Unknown Alias", "Old FC", "New FC")
    };
    let mut run = MatchRun::new(&m, vec![(10, vec![3001]), (20, vec![3002])], &[10, 20]);
    run.transfers = vec![alias];
    run.history = [3001, 3002]
        .into_iter()
        .map(|player| HistoryEntry {
            player_id: Some(player),
            fotmob_player_id: Some(777),
            ..HistoryEntry::default()
        })
        .collect();

    let out = run.run();

    assert_eq!(out.matched[0].player_id, None);
}

// py: test_return_from_outside_club_moves_from_parent_with_earlier_loan
#[test]
fn return_from_outside_club_moves_from_parent_with_earlier_loan() {
    let (parent, destination) = (30, 20);
    let mut event = crate::planning::MatchedTransfer::new(Transfer {
        date: "2026-08-02".to_owned(),
        ..transfer("Randal Kolo Muani", "Outside Club", "Destination")
    });
    event.player_id = Some(115254);
    event.to = Side::Team(destination);
    event.player_confidence = 100.0;
    event.to_confidence = 100.0;
    let history = vec![HistoryEntry {
        player_id: Some(115254),
        from_team_id: Some(parent),
        to_team_id: None,
        transfer_type: "loan".to_owned(),
        transfer_date: "2026-01-10".to_owned(),
        ..HistoryEntry::default()
    }];
    let mut parent_roster = vec![115254];
    parent_roster.extend(3000..3019);
    let rosters = [
        team(parent, &parent_roster),
        team(destination, &ids(2000..2020)),
    ];
    let clubs = club_set(&[parent, destination]);
    let events = [event];

    let reconciled = plan_with_superseded(
        &events,
        &rosters,
        &clubs,
        &NoOverflow,
        &build_superseded_loan_sources(&events, &history),
        true,
        &HashMap::new(),
    );
    let unproven = plan_with_superseded(
        &events,
        &rosters,
        &clubs,
        &NoOverflow,
        &build_superseded_loan_sources(&events, &[]),
        true,
        &HashMap::new(),
    );

    assert_eq!(actions(&reconciled), [("move", Some(parent))]);
    let refusals: Vec<_> = unproven
        .actions
        .iter()
        .map(|item| (item.action.as_str(), item.reason.as_str()))
        .collect();
    assert_eq!(refusals, [("skip", "already_registered_elsewhere")]);
}

/// A permanent move followed by a loan out, both already in the log, leaves the
/// player at the loan club. Rerunning the same feed must not walk him back to
/// the permanent club and out again.
#[test]
fn rerun_does_not_replay_logged_chain() {
    let (origin, permanent, loan_club) = (10, 20, 40);
    let player = 115254;
    let events = [
        club_match(
            origin,
            permanent,
            "2026-05-22T11:52:57Z",
            TransferType::Transfer,
            false,
        ),
        club_match(
            permanent,
            loan_club,
            "2026-09-03T07:49:15Z",
            TransferType::Loan,
            true,
        ),
    ];
    let logged = |event: &crate::planning::MatchedTransfer| HistoryEntry {
        player_id: event.player_id,
        from_team_id: event.from.team(),
        to_team_id: event.to.team(),
        transfer_type: event.transfer.transfer_type.as_str().to_owned(),
        transfer_date: event.transfer.date.clone(),
        ..HistoryEntry::default()
    };
    let mut at_loan_club = vec![player];
    at_loan_club.extend(4000..4019);
    let rosters = [
        team(origin, &ids(1000..1020)),
        team(permanent, &ids(2000..2020)),
        team(loan_club, &at_loan_club),
    ];
    let clubs = club_set(&[origin, permanent, loan_club]);

    let first_pass = plan_with_log(&events, &rosters, &clubs, &[]);
    let rerun = plan_with_log(
        &events,
        &rosters,
        &clubs,
        &[logged(&events[0]), logged(&events[1])],
    );

    // Unlogged, the reconcile rule walks him loan club -> permanent -> loan club.
    assert_eq!(
        actions(&first_pass),
        [("move", Some(loan_club)), ("move", Some(permanent))]
    );
    assert_eq!(
        actions(&rerun),
        [("noop", Some(loan_club)), ("noop", Some(loan_club))]
    );
}

/// The first link of a chain was only confirmed (never logged) by the first
/// pass, the second link was applied and logged. A rerun must not drag the
/// player back to the first link's destination.
#[test]
fn rerun_treats_events_before_a_logged_one_as_obsolete() {
    let (origin, middle, last) = (10, 20, 40);
    let player = 115254;
    let events = [
        club_match(
            origin,
            middle,
            "2026-05-22T11:52:57Z",
            TransferType::Transfer,
            false,
        ),
        club_match(
            middle,
            last,
            "2026-09-03T07:49:15Z",
            TransferType::Transfer,
            false,
        ),
    ];
    let history = [HistoryEntry {
        player_id: Some(player),
        from_team_id: Some(middle),
        to_team_id: Some(last),
        transfer_type: "transfer".to_owned(),
        transfer_date: "2026-09-03T07:49:15Z".to_owned(),
        ..HistoryEntry::default()
    }];
    let mut at_last = vec![player];
    at_last.extend(4000..4019);
    let rosters = [
        team(origin, &ids(1000..1020)),
        team(middle, &ids(2000..2020)),
        team(last, &at_last),
    ];
    let clubs = club_set(&[origin, middle, last]);

    let unlogged = plan_with_log(&events, &rosters, &clubs, &[]);
    let rerun = plan_with_log(&events, &rosters, &clubs, &history);

    assert_eq!(
        actions(&unlogged),
        [("move", Some(last)), ("move", Some(middle))]
    );
    assert_eq!(
        actions(&rerun),
        [("noop", Some(last)), ("noop", Some(last))]
    );
}

/// A logged signing whose player has since been released (for example by an
/// overflow release making room for a later signing) is not added again.
#[test]
fn rerun_does_not_readd_a_released_logged_signing() {
    let (origin, destination) = (10, 20);
    let player = 151949;
    let event = matched(
        dated(
            transfer("Signing", "Free Agent", "Destination"),
            "2026-06-30T22:00:00Z",
            TransferType::FreeTransfer,
        ),
        Some(player),
        Side::Outside,
        Side::Team(destination),
    );
    let rosters = [
        team(origin, &ids(1000..1020)),
        team(destination, &ids(2000..2020)),
    ];
    let clubs = club_set(&[origin, destination]);
    let history = [HistoryEntry {
        player_id: Some(player),
        from_team_id: None,
        to_team_id: Some(destination),
        transfer_type: "free transfer".to_owned(),
        transfer_date: event.transfer.date.clone(),
        ..HistoryEntry::default()
    }];

    let unlogged = plan_with_log(std::slice::from_ref(&event), &rosters, &clubs, &[]);
    let logged = plan_with_log(std::slice::from_ref(&event), &rosters, &clubs, &history);

    assert_eq!(actions(&unlogged), [("add", None)]);
    assert_eq!(actions(&logged), [("noop", None)]);
}

/// Releases the first roster player that is neither incoming nor protected.
struct FirstUnprotected;

impl crate::planning::OverflowSelector for FirstUnprotected {
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
            .find(|&player| player != 0 && player != incoming && !protected.contains(&player))
    }
}

/// A full club that signs a player makes room by releasing somebody. A signing
/// the feed lists for that club and the save already holds (a `noop`, which
/// the log never records) is never the casualty: the next run would add him
/// again, drop the next one, and never settle.
#[test]
fn overflow_never_releases_a_player_the_feed_puts_at_the_club() {
    let destination = 20;
    let (held, newcomer) = (2000, 3000);
    let early = matched(
        dated(
            transfer("Held", "Free Agent", "Destination"),
            "2026-01-07T10:46:08Z",
            TransferType::FreeTransfer,
        ),
        Some(held),
        Side::Outside,
        Side::Team(destination),
    );
    let late = matched(
        dated(
            transfer("Newcomer", "Free Agent", "Destination"),
            "2026-07-01T10:00:00Z",
            TransferType::FreeTransfer,
        ),
        Some(newcomer),
        Side::Outside,
        Side::Team(destination),
    );
    let events = [early, late];
    let rosters = [team(destination, &ids(2000..2040))];
    let clubs = club_set(&[destination]);
    let slots: Vec<(u32, Vec<u32>)> = rosters
        .iter()
        .map(|t| (t.team_id, t.player_ids.to_vec()))
        .collect();
    let plan = |overflow: &dyn crate::planning::OverflowSelector, events: &[_]| {
        crate::planning::plan_roster_actions(&crate::planning::RosterPlanInput {
            matches: events,
            teams: &slots,
            club_ids: &clubs,
            overflow,
            superseded_loan_sources: &no_superseded(events.len()),
            already_applied: &[],
            allow_overflow_release: true,
            live_squad_ids: &HashMap::new(),
        })
    };

    let output = plan(&FirstUnprotected, &events);
    assert_eq!(
        actions(&output),
        [("noop", Some(destination)), ("add", None)]
    );
    assert_eq!(
        output.actions[1].overflow_player_id,
        Some(2001),
        "player 2000 is the feed's signing and stays; the next one goes"
    );

    // His own later departure from the club takes the protection away.
    let leaving = matched(
        dated(
            transfer("Held", "Destination", "Free Agent"),
            "2026-08-01T10:00:00Z",
            TransferType::FreeTransfer,
        ),
        Some(held),
        Side::Team(destination),
        Side::Outside,
    );
    let with_departure = [events[0].clone(), events[1].clone(), leaving];
    let output = plan(&FirstUnprotected, &with_departure);
    assert_eq!(output.actions[1].overflow_player_id, Some(2000));
}

// py: test_stale_fotmob_history_identity_does_not_block_player_forever
#[test]
fn stale_fotmob_history_identity_does_not_block_player_forever() {
    let m = matcher(
        &[("First Player", 3001), ("Second Player", 3002)],
        &[("Old FC", 10), ("New FC", 20), ("Other FC", 30)],
    );
    let event = Transfer {
        player_id_fotmob: Some(777),
        ..transfer("Second Player", "Old FC", "New FC")
    };
    let mut run = MatchRun::new(
        &m,
        vec![(10, vec![3002]), (20, vec![]), (30, vec![3001])],
        &[10, 20, 30],
    );
    run.transfers = vec![event];
    run.history = vec![HistoryEntry {
        player_id: Some(3001),
        fotmob_player_id: Some(777),
        ..HistoryEntry::default()
    }];

    let out = run.run();

    // The event's clubs register the name-matched player, not the logged one.
    assert_eq!(out.matched[0].player_id, Some(3002));
    assert_eq!(out.report.fotmob_player_ids.get(&777), Some(&3002));
}

// py: test_live_squad_identity_overrides_conflicting_history
#[test]
fn live_squad_identity_overrides_conflicting_history() {
    let m = matcher(
        &[("First Player", 3001), ("Second Player", 3002)],
        &[("Old FC", 10), ("New FC", 20), ("Other FC", 30)],
    );
    let live = snapshot("Old FC", 100, vec![member("Second Player", Some(777))]);
    let event = Transfer {
        player_id_fotmob: Some(777),
        ..transfer("First Player", "Old FC", "New FC")
    };
    let bound = identity(&[(100, 10)], Some(&m));
    let mut run = MatchRun::new(
        &m,
        vec![(10, vec![3002]), (20, vec![]), (30, vec![3001])],
        &[10, 20, 30],
    );
    run.transfers = vec![event];
    run.history = vec![HistoryEntry {
        player_id: Some(3001),
        fotmob_player_id: Some(777),
        ..HistoryEntry::default()
    }];
    run.identity = Some(&bound);
    run.snapshots = vec![live];

    let out = run.run();

    assert_eq!(out.matched[0].player_id, Some(3002));
    assert_eq!(out.report.fotmob_player_ids.get(&777), Some(&3002));
    assert_eq!(out.report.live_squad_ids.get(&10), Some(&club_set(&[3002])));
}

// py: test_undecidable_identity_conflict_is_reported_with_reason
#[test]
fn undecidable_identity_conflict_is_reported_with_reason() {
    let m = matcher(
        &[("First Player", 3001), ("Second Player", 3002)],
        &[("Old FC", 10), ("New FC", 20)],
    );
    let mut event = transfer("Second Player", "Old FC", "New FC");
    event.date = "2026-08-01".to_owned();
    event.player_id_fotmob = Some(777);
    let mut old_roster = vec![3002];
    old_roster.extend(5000..5017);
    let mut new_roster = vec![3001];
    new_roster.extend(6000..6017);
    let mut run = MatchRun::new(
        &m,
        vec![(10, old_roster.clone()), (20, new_roster.clone())],
        &[10, 20],
    );
    run.transfers = vec![event];
    run.history = vec![HistoryEntry {
        player_id: Some(3001),
        fotmob_player_id: Some(777),
        ..HistoryEntry::default()
    }];

    let out = run.run();
    let rosters = [team(10, &old_roster), team(20, &new_roster)];
    let planned = plan_with_superseded(
        &out.matched,
        &rosters,
        &club_set(&[10, 20]),
        &NoOverflow,
        &no_superseded(out.matched.len()),
        true,
        &HashMap::new(),
    );

    assert_eq!(out.matched[0].player_id, None);
    let skipped: Vec<_> = out
        .report
        .skipped
        .iter()
        .chain(&planned.skipped)
        .map(|item| (item.reason.as_str(), item.relevant, item.fotmob_player_id))
        .collect();
    assert_eq!(skipped, [("provider_identity_conflict", true, Some(777))]);
}

fn sortitoutsi_signal() -> Transfer {
    Transfer {
        player_name: "Jordan Henderson".to_owned(),
        to_club: "Chelsea".to_owned(),
        date: "2026-08-03".to_owned(),
        sources: vec!["sortitoutsi".to_owned()],
        proof_urls: vec!["https://www.chelseafc.com/proof".to_owned()],
        verification_status: VerificationStatus::Enabled,
        infer_from_current_roster: true,
        ..Transfer::default()
    }
}

// py: test_enabled_destination_only_signal_infers_one_exact_current_roster
#[test]
fn enabled_destination_only_signal_infers_one_exact_current_roster() {
    let m = matcher(
        &[("Jordan Henderson", 7)],
        &[("Brentford", 10), ("Chelsea", 20)],
    );
    let mut run = MatchRun::new(&m, vec![(10, vec![7]), (20, vec![])], &[10, 20]);
    run.transfers = vec![sortitoutsi_signal()];

    let out = run.run();
    let item = &out.matched[0];

    assert!(item.is_club_transfer());
    assert_eq!(item.from, Side::Team(10));
    assert_eq!(item.to, Side::Team(20));
    assert_eq!(item.transfer.from_club, "Brentford");
}

// py: test_destination_only_signal_is_unactionable_when_current_roster_is_ambiguous
#[test]
fn destination_only_signal_is_unactionable_when_current_roster_is_ambiguous() {
    let m = matcher(
        &[("Jordan Henderson", 7)],
        &[("Brentford", 10), ("Chelsea", 20), ("Duplicate", 30)],
    );
    let mut run = MatchRun::new(
        &m,
        vec![(10, vec![7]), (20, vec![]), (30, vec![7])],
        &[10, 20, 30],
    );
    run.transfers = vec![sortitoutsi_signal()];

    let out = run.run();
    let item = &out.matched[0];

    assert!(!item.is_fully_matched());
    assert_eq!(item.from, Side::Unresolved);
}

// py: test_destination_only_signal_cannot_infer_source_for_unresolved_destination
#[test]
fn destination_only_signal_cannot_infer_source_for_unresolved_destination() {
    let m = matcher(&[("Jordan Henderson", 7)], &[("Brentford", 10)]);
    let mut run = MatchRun::new(&m, vec![(10, vec![7])], &[10]);
    run.transfers = vec![sortitoutsi_signal()];

    let out = run.run();
    let item = &out.matched[0];

    assert!(!item.is_fully_matched());
    assert_eq!(item.from, Side::Unresolved);
    assert_eq!(item.to, Side::Outside);
    assert_eq!(item.transfer.from_club, "");
}
