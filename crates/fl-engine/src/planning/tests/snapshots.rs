//! Scenarios for moves and releases derived from complete live squads.

use std::collections::HashMap;

use fl_sources::model::{SquadMember, Transfer, TransferType};

use super::support::*;
use crate::matcher::PlayerAttrs;
use crate::planning::{
    HistoryEntry, MatchedTransfer, RosterMap, ShirtNumbers, Side, all_roster_ids,
    match_snapshot_member,
};

/// `(f"{prefix} {id}", id)` catalog entries.
fn named(prefix: &str, ids: impl IntoIterator<Item = u32>) -> Vec<(String, u32)> {
    ids.into_iter()
        .map(|id| (format!("{prefix} {id}"), id))
        .collect()
}

/// `{id: f"{prefix} {id}"}` display names.
fn labels(prefix: &str, ids: impl IntoIterator<Item = u32>) -> HashMap<u32, String> {
    named(prefix, ids)
        .into_iter()
        .map(|(name, id)| (id, name))
        .collect()
}

/// Squad members `f"{prefix} {id}"` with FotMob id `fotmob(index)`, `index`
/// counting from zero.
fn squad(
    prefix: &str,
    ids: impl IntoIterator<Item = u32>,
    fotmob: impl Fn(usize) -> i64,
) -> Vec<SquadMember> {
    ids.into_iter()
        .enumerate()
        .map(|(index, id)| member(&format!("{prefix} {id}"), Some(fotmob(index))))
        .collect()
}

fn attrs(position: &str, age: u32) -> PlayerAttrs {
    PlayerAttrs {
        position: position.to_owned(),
        age,
        ..PlayerAttrs::default()
    }
}

fn roster(ids: impl IntoIterator<Item = u32>) -> Vec<u32> {
    ids.into_iter().collect()
}

fn rows(matched: &[MatchedTransfer]) -> Vec<(TransferType, Option<u32>, Side, Side)> {
    matched
        .iter()
        .map(|item| {
            (
                item.transfer.transfer_type,
                item.player_id,
                item.from,
                item.to,
            )
        })
        .collect()
}

fn kinds(matched: &[MatchedTransfer]) -> Vec<(Option<u32>, TransferType)> {
    matched
        .iter()
        .map(|item| (item.player_id, item.transfer.transfer_type))
        .collect()
}

fn shirt_update(
    name: &str,
    club: &str,
    number: u32,
    fotmob_team: i64,
    fotmob_player: i64,
) -> Transfer {
    Transfer {
        transfer_type: TransferType::ShirtNumberUpdate,
        shirt_number: Some(number),
        from_club_id_fotmob: Some(fotmob_team),
        to_club_id_fotmob: Some(fotmob_team),
        player_id_fotmob: Some(fotmob_player),
        ..transfer(name, club, club)
    }
}

// py: test_stateful_matching_uses_snapshot_identity_map_for_current_move
#[test]
fn stateful_matching_uses_snapshot_identity_map_for_current_move() {
    let mut players = vec![("Anderson Lopes".to_owned(), 3001)];
    players.extend(named("Source Player", 3002..3019));
    let matcher = matcher_owned(
        &players,
        HashMap::new(),
        &[("Old Club", 10), ("Vissel Kobe", 20)],
    );
    let source_snapshot = snapshot(
        "Old Club",
        777,
        squad("Source Player", 3002..3013, |index| {
            6000 + 3002 + index as i64
        }),
    );
    let mut destination_members = vec![member("Anderson Lopes", Some(498456))];
    destination_members
        .extend((0..10).map(|index| member(&format!("Snapshot Player {index}"), None)));
    let destination_snapshot = snapshot("Vissel Kobe", 4688, destination_members);

    let club_identity = identity(&[(4688, 20), (777, 10)], Some(&matcher));
    let mut run = MatchRun::new(
        &matcher,
        vec![(10, roster(3001..3019)), (20, Vec::new())],
        &[10, 20],
    );
    run.identity = Some(&club_identity);
    run.snapshots = vec![source_snapshot, destination_snapshot];
    let matched = run.run().matched;

    assert_eq!(matched.len(), 1);
    assert_eq!(
        matched[0].transfer.transfer_type,
        TransferType::SquadRegistration
    );
    assert_eq!(matched[0].player_id, Some(3001));
    assert_eq!(matched[0].from, Side::Team(10));
    assert_eq!(matched[0].to, Side::Team(20));
}

// py: test_fast_snapshot_move_allows_uncovered_source_and_shirt_identity
// Deep behavior only: the source club is also covered by a complete snapshot
// (the fast `allow_uncovered_source` mode is not ported).
#[test]
fn fast_snapshot_move_allows_uncovered_source_and_shirt_identity() {
    let source_ids = roster(3001..3019);
    let destination_ids = roster(4001..4017);
    let mut players = vec![("Current Player".to_owned(), 3001)];
    players.extend(named("Source Player", source_ids[1..].iter().copied()));
    players.extend(named("Destination Player", destination_ids.iter().copied()));
    let matcher = matcher_owned(
        &players,
        HashMap::new(),
        &[("Source FC", 10), ("Destination FC", 20)],
    );
    let source_snapshot = snapshot(
        "Source FC",
        100,
        squad("Source Player", source_ids[1..].iter().copied(), |index| {
            7001 + index as i64
        }),
    );
    let mut destination_members = vec![SquadMember {
        shirt_number: Some(7),
        ..member("Current Player", Some(9001))
    }];
    destination_members.extend(squad(
        "Destination Player",
        destination_ids.iter().copied(),
        |index| 9002 + index as i64,
    ));
    let destination_snapshot = snapshot("Destination FC", 200, destination_members);

    let club_identity = identity(&[(200, 20), (100, 10)], Some(&matcher));
    let mut run = MatchRun::new(
        &matcher,
        vec![(10, source_ids.clone()), (20, destination_ids.clone())],
        &[10, 20],
    );
    run.transfers = vec![shirt_update(
        "Current Player",
        "Destination FC",
        7,
        200,
        9001,
    )];
    run.identity = Some(&club_identity);
    run.snapshots = vec![source_snapshot, destination_snapshot];
    let matched = run.run().matched;

    let found: Vec<_> = matched
        .iter()
        .map(|item| {
            (
                item.transfer.transfer_type,
                item.player_id,
                item.from,
                item.to,
                item.transfer.shirt_number,
            )
        })
        .collect();
    assert_eq!(
        found,
        vec![
            (
                TransferType::SquadRegistration,
                Some(3001),
                Side::Team(10),
                Side::Team(20),
                Some(7)
            ),
            (
                TransferType::ShirtNumberUpdate,
                Some(3001),
                Side::Team(20),
                Side::Team(20),
                Some(7)
            ),
        ]
    );
}

// py: test_current_squad_registers_catalog_player_missing_from_local_roster
#[test]
fn current_squad_registers_catalog_player_missing_from_local_roster() {
    let existing_ids = roster(3001..3026);
    let mut players = vec![("Missing Catalog Player".to_owned(), 3999)];
    players.extend(named("Destination Player", existing_ids.iter().copied()));
    let matcher = matcher_owned(&players, HashMap::new(), &[("Destination FC", 20)]);
    let mut members = vec![member("Missing Catalog Player", Some(9001))];
    members.extend(squad(
        "Destination Player",
        existing_ids.iter().copied(),
        |index| 9002 + index as i64,
    ));
    let destination = snapshot("Destination FC", 200, members);

    let club_identity = identity(&[(200, 20)], Some(&matcher));
    let mut run = MatchRun::new(&matcher, vec![(20, existing_ids.clone())], &[20]);
    run.identity = Some(&club_identity);
    run.snapshots = vec![destination];
    let matched = run.run().matched;

    assert_eq!(
        rows(&matched),
        vec![(
            TransferType::SquadRegistration,
            Some(3999),
            Side::Outside,
            Side::Team(20)
        )]
    );
    let output = plan(
        &matched,
        &[team(20, &existing_ids)],
        &club_set(&[20]),
        &NoOverflow,
        &[],
        true,
        &HashMap::new(),
    );
    assert_eq!(actions(&output), vec![("add", None)]);
}

// py: test_current_squad_registers_catalog_player_with_stale_roster_extras
#[test]
fn current_squad_registers_catalog_player_with_stale_roster_extras() {
    let existing_ids = roster(3001..3026);
    let stale_ids = roster(5001..5011);
    let mut players = vec![("Missing Catalog Player".to_owned(), 3999)];
    players.extend(named("Destination Player", existing_ids.iter().copied()));
    players.extend(named("Stale Academy Player", stale_ids.iter().copied()));
    let matcher = matcher_owned(&players, HashMap::new(), &[("Destination FC", 20)]);
    let mut members = vec![member("Missing Catalog Player", Some(9001))];
    members.extend(squad(
        "Destination Player",
        existing_ids.iter().copied(),
        |index| 9002 + index as i64,
    ));
    let destination = snapshot("Destination FC", 200, members);

    let club_identity = identity(&[(200, 20)], Some(&matcher));
    let local: Vec<u32> = existing_ids.iter().chain(&stale_ids).copied().collect();
    let mut run = MatchRun::new(&matcher, vec![(20, local)], &[20]);
    run.identity = Some(&club_identity);
    run.snapshots = vec![destination];
    let matched = run.run().matched;

    assert_eq!(
        rows(&matched),
        vec![(
            TransferType::SquadRegistration,
            Some(3999),
            Side::Outside,
            Side::Team(20)
        )]
    );
}

// py: test_current_squad_moves_from_uncovered_source_with_healthy_destination
#[test]
fn current_squad_moves_from_uncovered_source_with_healthy_destination() {
    let source_player_id = 3001;
    let destination_ids = roster(4001..4017);
    let mut players = vec![("Current Player".to_owned(), source_player_id)];
    players.extend(named("Destination Player", destination_ids.iter().copied()));
    let matcher = matcher_owned(
        &players,
        HashMap::new(),
        &[("Source FC", 10), ("Destination FC", 20)],
    );
    let mut members = vec![member("Current Player", Some(9001))];
    members.extend(squad(
        "Destination Player",
        destination_ids.iter().copied(),
        |index| 9002 + index as i64,
    ));
    let destination = snapshot("Destination FC", 200, members);

    let club_identity = identity(&[(200, 20)], Some(&matcher));
    let mut run = MatchRun::new(
        &matcher,
        vec![(10, vec![source_player_id]), (20, destination_ids.clone())],
        &[10, 20],
    );
    run.identity = Some(&club_identity);
    run.snapshots = vec![destination];
    let matched = run.run().matched;

    assert_eq!(
        rows(&matched),
        vec![(
            TransferType::SquadRegistration,
            Some(source_player_id),
            Side::Team(10),
            Side::Team(20)
        )]
    );
}

// py: test_snapshot_identity_collision_keeps_current_roster_anchor
#[test]
fn snapshot_identity_collision_keeps_current_roster_anchor() {
    let source_ids = roster(3001..3018);
    let destination_ids = roster(4001..4017);
    let mut players = vec![("Allan".to_owned(), 3001)];
    players.extend(named("Source Player", source_ids[1..].iter().copied()));
    players.extend(named("Destination Player", destination_ids.iter().copied()));
    let matcher = matcher_owned(
        &players,
        HashMap::new(),
        &[("Lanus", 10), ("Manchester City", 20)],
    );
    let mut source_members = vec![member("Allan", Some(9000))];
    source_members.extend(squad(
        "Source Player",
        source_ids[1..].iter().copied(),
        |index| 9101 + index as i64,
    ));
    let mut destination_members = vec![member("Allan", Some(9001))];
    destination_members.extend(squad(
        "Destination Player",
        destination_ids.iter().copied(),
        |index| 9201 + index as i64,
    ));

    let club_identity = identity(&[(100, 10), (200, 20)], Some(&matcher));
    let mut run = MatchRun::new(
        &matcher,
        vec![(10, source_ids.clone()), (20, destination_ids.clone())],
        &[10, 20],
    );
    run.identity = Some(&club_identity);
    run.snapshots = vec![
        snapshot("Lanus", 100, source_members),
        snapshot("Manchester City", 200, destination_members),
    ];
    let matched = run.run().matched;

    assert!(matched.is_empty(), "unexpected matches: {matched:?}");
}

// py: test_snapshot_prefers_club_roster_over_unrelated_exact_name
#[test]
fn snapshot_prefers_club_roster_over_unrelated_exact_name() {
    let current_ids = roster(4001..4018);
    let mut players = vec![
        ("Endrick Felipe".to_owned(), 4001),
        ("Endrick".to_owned(), 5001),
    ];
    players.extend(named("Player", current_ids[1..].iter().copied()));
    let matcher = matcher_owned(
        &players,
        HashMap::new(),
        &[("Real Madrid", 10), ("Malaysia", 20)],
    );
    let mut members = vec![member("Endrick", Some(9001))];
    members.extend(squad("Player", current_ids[1..].iter().copied(), |index| {
        9001 + index as i64
    }));

    let club_identity = identity(&[(8633, 10)], Some(&matcher));
    let mut run = MatchRun::new(
        &matcher,
        vec![(10, current_ids.clone()), (20, vec![5001])],
        &[10, 20],
    );
    run.identity = Some(&club_identity);
    run.snapshots = vec![snapshot("Real Madrid", 8633, members)];
    let matched = run.run().matched;

    assert!(matched.is_empty(), "unexpected matches: {matched:?}");
}

// py: test_snapshot_short_name_prefers_local_long_identity_over_catalog_name
#[test]
fn snapshot_short_name_prefers_local_long_identity_over_catalog_name() {
    let matcher = matcher_with_attrs(
        &[("Gabriel", 90866), ("Gabriel Magalhães", 111207)],
        HashMap::from([(111207, attrs("CB", 28))]),
        &[],
    );
    let candidate = SquadMember {
        position: "CB".to_owned(),
        age: 28,
        ..member("Gabriel", None)
    };
    let team_player_map: RosterMap = HashMap::from([(10, vec![111207]), (20, Vec::new())]);

    let found = match_snapshot_member(
        &matcher,
        &candidate,
        10,
        &team_player_map,
        80.0,
        None,
        &all_roster_ids(&team_player_map),
    );

    assert_eq!(
        (found.id, found.name.as_str()),
        (Some(111207), "Gabriel Magalhães")
    );
    assert_eq!(found.confidence, 100.0);
}

// py: test_snapshot_global_fuzzy_rejects_unsafe_near_name
#[test]
fn snapshot_global_fuzzy_rejects_unsafe_near_name() {
    let matcher = matcher(
        &[("Peque Fernández", 131798), ("Ezequiel Fernández", 140305)],
        &[],
    );
    let candidate = SquadMember {
        position: "CDM".to_owned(),
        age: 24,
        ..member("Equi Fernández", None)
    };
    let team_player_map: RosterMap = HashMap::from([(10, Vec::new()), (20, vec![131798])]);

    let found = match_snapshot_member(
        &matcher,
        &candidate,
        10,
        &team_player_map,
        80.0,
        None,
        &all_roster_ids(&team_player_map),
    );

    assert_eq!((found.id, found.name.as_str()), (None, ""));
    assert!(found.confidence < 95.0, "confidence {}", found.confidence);
}

// py: test_unhealthy_snapshot_skips_inferred_moves_and_releases
#[test]
fn unhealthy_snapshot_skips_inferred_moves_and_releases() {
    let source_ids = roster(5001..5039);
    let destination_ids = roster(6001..6017);
    let mut players = vec![("Move Me".to_owned(), 5001)];
    players.extend(named("Source Player", source_ids[1..].iter().copied()));
    players.extend(named("Destination Player", destination_ids.iter().copied()));
    let matcher = matcher_owned(
        &players,
        HashMap::new(),
        &[("Source FC", 10), ("Destination FC", 20)],
    );
    let source_snapshot = snapshot(
        "Source FC",
        100,
        squad("Source Player", 5002..5027, |index| 7001 + index as i64),
    );
    let mut destination_members = vec![member("Move Me", Some(9001))];
    destination_members.extend(squad(
        "Destination Player",
        destination_ids[..10].iter().copied(),
        |index| 9002 + index as i64,
    ));
    let destination_snapshot = snapshot("Destination FC", 200, destination_members);
    let mut player_names = labels("Destination Player", destination_ids.iter().copied());
    player_names.extend(labels("Source Player", source_ids.iter().copied()));
    player_names.insert(5001, "Move Me".to_owned());

    let club_identity = identity(&[(100, 10), (200, 20)], Some(&matcher));
    let mut run = MatchRun::new(
        &matcher,
        vec![(10, source_ids.clone()), (20, destination_ids.clone())],
        &[10, 20],
    );
    run.identity = Some(&club_identity);
    run.snapshots = vec![source_snapshot, destination_snapshot];
    run.player_names = player_names;
    let matched = run.run().matched;

    assert!(matched.is_empty(), "unexpected matches: {matched:?}");
}

// py: test_complete_squad_snapshot_releases_stale_current_roster_player
#[test]
fn complete_squad_snapshot_releases_stale_current_roster_player() {
    let current_ids = roster(3001..3018);
    let matcher = matcher_owned(
        &named("Player", current_ids.iter().copied()),
        HashMap::new(),
        &[("Example FC", 10)],
    );
    let members = squad(
        "Player",
        current_ids[..current_ids.len() - 1].iter().copied(),
        |index| 5000 + index as i64,
    );

    let club_identity = identity(&[(42, 10)], Some(&matcher));
    let mut run = MatchRun::new(&matcher, vec![(10, current_ids.clone())], &[10]);
    run.identity = Some(&club_identity);
    run.snapshots = vec![snapshot("Example FC", 42, members)];
    run.player_names = labels("Player", current_ids.iter().copied());
    let matched = run.run().matched;

    assert_eq!(
        kinds(&matched),
        vec![(Some(3017), TransferType::SquadRelease)]
    );
    let output = plan(
        &matched,
        &[team(10, &current_ids)],
        &club_set(&[10]),
        &NoOverflow,
        &[],
        true,
        &HashMap::new(),
    );
    assert_eq!(actions(&output), vec![("release", Some(10))]);
}

// py: test_snapshot_exact_name_survives_position_label_mismatch
#[test]
fn snapshot_exact_name_survives_position_label_mismatch() {
    let baturina_id = 141911;
    let mut current_ids = vec![baturina_id];
    current_ids.extend(3001..3017);
    let mut players = vec![("Martin Baturina".to_owned(), baturina_id)];
    players.extend(named("Player", current_ids[1..].iter().copied()));
    let matcher = matcher_owned(
        &players,
        HashMap::from([(baturina_id, attrs("AMF", 22))]),
        &[("Example FC", 10)],
    );
    let mut members = vec![SquadMember {
        position: "LW".to_owned(),
        age: 23,
        ..member("Martin Baturina", Some(9001))
    }];
    members.extend(squad(
        "Player",
        current_ids[1..current_ids.len() - 1].iter().copied(),
        |index| 9002 + index as i64,
    ));

    let club_identity = identity(&[(42, 10)], Some(&matcher));
    let mut run = MatchRun::new(&matcher, vec![(10, current_ids.clone())], &[10]);
    run.identity = Some(&club_identity);
    run.snapshots = vec![snapshot("Example FC", 42, members)];
    run.player_names = labels("Player", current_ids.iter().copied());
    let matched = run.run().matched;

    assert_eq!(
        kinds(&matched),
        vec![(Some(3016), TransferType::SquadRelease)]
    );
}

// py: test_snapshot_short_aliases_use_local_shirt_and_position_before_releases
#[test]
fn snapshot_short_aliases_use_local_shirt_and_position_before_releases() {
    let allan_id = 35001;
    let wrong_savio_id = 35002;
    let savinho_id = 35003;
    let city_extras = roster(35100..35115);
    let spurs_extras = roster(35200..35215);
    let unrelated_allan_id = 35300;
    let city_ids: Vec<u32> = [allan_id]
        .into_iter()
        .chain(city_extras.iter().copied())
        .collect();
    let spurs_ids: Vec<u32> = [wrong_savio_id, savinho_id]
        .into_iter()
        .chain(spurs_extras.iter().copied())
        .collect();

    let mut player_names: Vec<(u32, String)> = vec![
        (allan_id, "Allan Andrade".to_owned()),
        (wrong_savio_id, "Sávio".to_owned()),
        (savinho_id, "Savinho".to_owned()),
        (unrelated_allan_id, "Allan".to_owned()),
    ];
    player_names.extend(
        city_extras
            .iter()
            .enumerate()
            .map(|(index, &id)| (id, format!("City Player {index}"))),
    );
    player_names.extend(
        spurs_extras
            .iter()
            .enumerate()
            .map(|(index, &id)| (id, format!("Spurs Player {index}"))),
    );
    let name_of = |id: u32| -> &str {
        &player_names
            .iter()
            .find(|(player, _)| *player == id)
            .expect("known player")
            .1
    };

    let players: Vec<(String, u32)> = player_names
        .iter()
        .map(|(id, name)| (name.clone(), *id))
        .collect();
    let mut player_attrs = HashMap::from([
        (allan_id, attrs("AMF", 21)),
        (wrong_savio_id, attrs("RB", 22)),
        (savinho_id, attrs("LWF", 20)),
        (unrelated_allan_id, attrs("RW", 35)),
    ]);
    for &id in city_extras.iter().chain(&spurs_extras) {
        player_attrs.insert(id, attrs("CM", 25));
    }
    let matcher = matcher_owned(
        &players,
        player_attrs,
        &[("Manchester City", 10), ("Tottenham", 20), ("Other", 30)],
    );

    let sighting = |name: &str, fotmob: i64, position: &str, age: u32, shirt: u32| SquadMember {
        position: position.to_owned(),
        age,
        shirt_number: Some(shirt),
        ..member(name, Some(fotmob))
    };
    let mut city_members = vec![sighting("Allan", 9001, "RW", 22, 37)];
    city_members.extend(city_extras.iter().enumerate().map(|(index, &id)| {
        sighting(name_of(id), 9100 + index as i64, "CM", 25, index as u32 + 1)
    }));
    let mut spurs_members = vec![sighting("Sávio", 9201, "RW", 22, 17)];
    spurs_members.extend(spurs_extras.iter().enumerate().map(|(index, &id)| {
        sighting(name_of(id), 9300 + index as i64, "CM", 25, index as u32 + 1)
    }));

    let mut city_shirts: HashMap<u32, u32> = HashMap::from([(allan_id, 37)]);
    city_shirts.extend(
        city_extras
            .iter()
            .enumerate()
            .map(|(index, &id)| (id, index as u32 + 1)),
    );
    let mut spurs_shirts: HashMap<u32, u32> =
        HashMap::from([(wrong_savio_id, 2), (savinho_id, 17)]);
    spurs_shirts.extend(
        spurs_extras
            .iter()
            .enumerate()
            .map(|(index, &id)| (id, index as u32 + 1)),
    );
    let shirt_numbers: ShirtNumbers = HashMap::from([
        (10, city_shirts),
        (20, spurs_shirts),
        (30, HashMap::from([(unrelated_allan_id, 10)])),
    ]);

    let club_identity = identity(&[(42, 10), (43, 20)], Some(&matcher));
    let mut run = MatchRun::new(
        &matcher,
        vec![
            (10, city_ids),
            (20, spurs_ids),
            (30, vec![unrelated_allan_id]),
        ],
        &[10, 20, 30],
    );
    run.identity = Some(&club_identity);
    run.snapshots = vec![
        snapshot("Manchester City", 42, city_members),
        snapshot("Tottenham", 43, spurs_members),
    ];
    run.player_names = player_names.iter().cloned().collect();
    run.shirt_numbers = Some(shirt_numbers);
    let matched = run.run().matched;

    assert_eq!(
        kinds(&matched),
        vec![(Some(wrong_savio_id), TransferType::SquadRelease)]
    );
}

// py: test_snapshot_does_not_release_player_from_current_transfer_event
#[test]
fn snapshot_does_not_release_player_from_current_transfer_event() {
    let source_ids = roster(3301..3314);
    let destination_ids = roster(3401..3417);
    let incoming_id = source_ids[0];
    let everyone: Vec<u32> = source_ids.iter().chain(&destination_ids).copied().collect();
    let matcher = matcher_owned(
        &named("Player", everyone.iter().copied()),
        HashMap::new(),
        &[("Source FC", 10), ("Destination FC", 20)],
    );
    let incoming = dated(
        transfer(
            &format!("Player {incoming_id}"),
            "Source FC",
            "Destination FC",
        ),
        "2026-09-01",
        TransferType::Transfer,
    );
    let members = squad("Player", destination_ids.iter().copied(), |index| {
        9500 + index as i64
    });

    let club_identity = identity(&[(42, 20)], Some(&matcher));
    let mut run = MatchRun::new(
        &matcher,
        vec![(10, source_ids.clone()), (20, destination_ids.clone())],
        &[10, 20],
    );
    run.transfers = vec![incoming];
    run.identity = Some(&club_identity);
    run.snapshots = vec![snapshot("Destination FC", 42, members)];
    run.player_names = labels("Player", everyone.iter().copied());
    let matched = run.run().matched;

    assert_eq!(
        kinds(&matched),
        vec![(Some(incoming_id), TransferType::Transfer)]
    );
}

// py: test_squad_releases_precede_shirt_updates
#[test]
fn squad_releases_precede_shirt_updates() {
    let current_ids = roster(3301..3319);
    let matcher = matcher_owned(
        &named("Player", current_ids.iter().copied()),
        HashMap::new(),
        &[("Example FC", 10)],
    );
    let members = squad(
        "Player",
        current_ids[..current_ids.len() - 1].iter().copied(),
        |index| 9001 + index as i64,
    );

    let club_identity = identity(&[(42, 10)], Some(&matcher));
    let mut run = MatchRun::new(&matcher, vec![(10, current_ids.clone())], &[10]);
    run.transfers = vec![shirt_update("Player 3301", "Example FC", 17, 42, 9001)];
    run.identity = Some(&club_identity);
    run.snapshots = vec![snapshot("Example FC", 42, members)];
    run.player_names = labels("Player", current_ids.iter().copied());
    let matched = run.run().matched;

    assert_eq!(
        kinds(&matched),
        vec![
            (Some(3318), TransferType::SquadRelease),
            (Some(3301), TransferType::ShirtNumberUpdate),
        ]
    );
}

// py: test_squad_snapshot_falls_back_when_historical_identity_is_stale
#[test]
fn squad_snapshot_falls_back_when_historical_identity_is_stale() {
    let current_ids = roster(3201..3218);
    let stale_external_id = 5000;
    let matcher = matcher_owned(
        &named("Player", current_ids.iter().copied()),
        HashMap::new(),
        &[("Example FC", 10)],
    );
    let last = current_ids.len() - 1;
    let members = squad("Player", current_ids.iter().copied(), |index| {
        if index == last {
            stale_external_id
        } else {
            6000 + index as i64
        }
    });

    let club_identity = identity(&[(42, 10)], Some(&matcher));
    let mut run = MatchRun::new(&matcher, vec![(10, current_ids.clone())], &[10]);
    run.history = vec![HistoryEntry {
        player_id: Some(9999),
        fotmob_player_id: Some(stale_external_id),
        ..HistoryEntry::default()
    }];
    run.identity = Some(&club_identity);
    run.snapshots = vec![snapshot("Example FC", 42, members)];
    run.player_names = labels("Player", current_ids.iter().copied());
    let matched = run.run().matched;

    assert!(matched.is_empty(), "unexpected matches: {matched:?}");
}

// py: test_incomplete_squad_snapshot_does_not_release_roster_players
#[test]
fn incomplete_squad_snapshot_does_not_release_roster_players() {
    let current_ids = roster(3101..3118);
    let matcher = matcher_owned(
        &named("Player", current_ids.iter().copied()),
        HashMap::new(),
        &[("Example FC", 10)],
    );
    let members = squad("Player", current_ids[..10].iter().copied(), |_| 0)
        .into_iter()
        .map(|member| SquadMember {
            player_id_fotmob: None,
            ..member
        })
        .collect();
    let mut incomplete = snapshot("Example FC", 42, members);
    incomplete.complete = false;

    let club_identity = identity(&[(42, 10)], Some(&matcher));
    let mut run = MatchRun::new(&matcher, vec![(10, current_ids.clone())], &[10]);
    run.identity = Some(&club_identity);
    run.snapshots = vec![incomplete];
    let matched = run.run().matched;

    assert!(matched.is_empty(), "unexpected matches: {matched:?}");
}

// py: test_snapshot_release_not_suppressed_by_release_from_other_club
#[test]
fn snapshot_release_not_suppressed_by_release_from_other_club() {
    let current_ids = roster(3001..3018);
    let other_ids = roster(4001..4018);
    let everyone: Vec<u32> = current_ids.iter().chain(&other_ids).copied().collect();
    let matcher = matcher_owned(
        &named("Player", everyone.iter().copied()),
        HashMap::new(),
        &[("Example FC", 10), ("Other FC", 20)],
    );
    let members = squad(
        "Player",
        current_ids[..current_ids.len() - 1].iter().copied(),
        |index| 5000 + index as i64,
    );
    let stale_feed_release = dated(
        transfer("Player 3017", "Other FC", "Free Agent"),
        "2026-08-01",
        TransferType::FreeTransfer,
    );

    let club_identity = identity(&[(42, 10)], Some(&matcher));
    let mut run = MatchRun::new(
        &matcher,
        vec![(10, current_ids.clone()), (20, other_ids.clone())],
        &[10, 20],
    );
    run.transfers = vec![stale_feed_release];
    run.identity = Some(&club_identity);
    run.snapshots = vec![snapshot("Example FC", 42, members)];
    run.player_names = labels("Player", current_ids.iter().copied());
    let matched = run.run().matched;

    let found: Vec<_> = matched
        .iter()
        .map(|item| (item.player_id, item.from, item.transfer.transfer_type))
        .collect();
    assert!(
        found.contains(&(Some(3017), Side::Team(10), TransferType::SquadRelease)),
        "missing snapshot release in {found:?}"
    );
}

// py: test_live_squad_move_blocked_by_low_coverage_is_reported
#[test]
fn live_squad_move_blocked_by_low_coverage_is_reported() {
    let destination_ids = roster(4001..4031);
    let source_ids = roster(3001..3020);
    let mut players = vec![("Mover Guy".to_owned(), 3001)];
    players.extend(named("Player", destination_ids.iter().copied()));
    let matcher = matcher_owned(
        &players,
        HashMap::new(),
        &[("Source FC", 10), ("Destination FC", 20)],
    );
    let mut members = vec![member("Mover Guy", Some(9001))];
    members.extend(squad(
        "Player",
        destination_ids[..11].iter().copied(),
        |index| destination_ids[index] as i64,
    ));

    let club_identity = identity(&[(200, 20)], Some(&matcher));
    let mut run = MatchRun::new(
        &matcher,
        vec![(10, source_ids), (20, destination_ids.clone())],
        &[10, 20],
    );
    run.identity = Some(&club_identity);
    run.snapshots = vec![snapshot("Destination FC", 200, members)];
    let output = run.run();

    assert!(
        !output
            .matched
            .iter()
            .any(|item| item.player_id == Some(3001))
    );
    let skipped: Vec<_> = output
        .report
        .skipped
        .iter()
        .map(|item| {
            (
                item.player_name.as_str(),
                item.reason.as_str(),
                item.relevant,
                item.fotmob_player_id,
            )
        })
        .collect();
    assert_eq!(
        skipped,
        vec![("Mover Guy", "snapshot_coverage_low", true, Some(9001))]
    );
}
