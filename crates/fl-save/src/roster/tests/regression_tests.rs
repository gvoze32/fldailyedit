//! Tests of hardened roster behaviour: role bytes follow their players,
//! protected players are never released, and refused mutations write nothing.

use std::collections::{BTreeMap, BTreeSet, HashSet};

use crate::edit_file::EditFile;
use crate::layout::*;
use crate::release_policy::ReleasePolicy;
use crate::roster::shirt::assign_smart_shirt_number;
use crate::roster::{AddRequest, MoveRequest, OverflowQuery, RosterError, RosterErrorKind};

use super::support::*;

/// When the lineup's active prefix is not a permutation of the roster slots,
/// the captain / set-piece bytes must still be remapped after the
/// swap-with-last compaction; otherwise the captain byte would name the
/// copied player (a different person) and a role held by the copied player
/// would point at an emptied slot.
#[test]
fn role_bytes_follow_their_players_on_a_non_standard_lineup() {
    let mut file = single_club(101, 1000, 20);
    let mut expected: Vec<u8> = (0..40).collect();
    expected[3] = 4; // duplicate slot: the active prefix is not a permutation
    set_lineup(&mut file, 101, &expected);
    set_game_plan_byte(&mut file, 101, GP_CAPTAIN, 5); // the player released below
    set_game_plan_byte(&mut file, 101, GP_PK, 19); // the last slot: copied into slot 5
    set_game_plan_byte(&mut file, 101, GP_LEFT_CK, 7); // unrelated

    file.release_player(1005, 101).unwrap();

    let roster = file.roster(101).unwrap();
    assert_eq!(
        roster.player_ids[5], 1019,
        "the last player was copied into slot 5"
    );
    assert_eq!(roster.player_ids[19], 0);
    assert_eq!(
        game_plan_byte(&file, 101, GP_CAPTAIN),
        GP_EMPTY_ROLE,
        "the captain byte must not stay 5, which now names the copied player"
    );
    assert_eq!(
        game_plan_byte(&file, 101, GP_PK),
        5,
        "the penalty byte must not stay 19, an emptied slot"
    );
    assert_eq!(game_plan_byte(&file, 101, GP_LEFT_CK), 7);
    assert_eq!(
        lineup(&file, 101),
        expected,
        "the non-standard lineup is preserved"
    );
}

fn two_clubs(full_ids: std::ops::Range<u32>) -> EditFile {
    let shirts: Vec<u16> = (1..=full_ids.len() as u16).collect();
    open(&mock(
        vec![
            (101, vec![1001], vec![7]),
            (102, full_ids.collect(), shirts),
        ],
        vec![101, 102],
    ))
}

fn protect_everyone(file: &mut EditFile, team: u32, ids: std::ops::Range<u32>) {
    let policy = ReleasePolicy {
        protected_players: BTreeMap::from([(team, ids.collect::<BTreeSet<u32>>())]),
        ..ReleasePolicy::default()
    };
    file.attach_release_policy(Some(&policy));
}

/// The all-excluded fallback ("every player except the incoming one") must
/// not also drop the policy's protected players (that would return (39, 2039)).
#[test]
fn overflow_never_releases_an_explicitly_protected_player() {
    let mut file = two_clubs(2000..2040);
    protect_everyone(&mut file, 102, 2000..2040);

    let query = OverflowQuery {
        exclude_player_id: Some(1001),
        ..OverflowQuery::default()
    };
    assert_eq!(file.find_overflow_release_candidate(102, query), None);

    let before = file.bytes().to_vec();
    let error = file
        .move_player(&MoveRequest::new(1001, 101, 102))
        .unwrap_err();
    assert_eq!(error.code(), "no_safe_overflow_candidate");
    assert_eq!(
        file.bytes(),
        before,
        "a refused move leaves the save untouched"
    );
}

#[test]
fn protected_ids_passed_by_the_planner_are_hard_too() {
    let file = two_clubs(2000..2040);
    let protected: HashSet<u32> = (2000..2040).collect();
    let query = OverflowQuery {
        exclude_player_id: Some(1001),
        protected_player_ids: Some(&protected),
        ..OverflowQuery::default()
    };
    assert_eq!(file.find_overflow_release_candidate(102, query), None);
}

/// Players signed in this session are protected, but when nobody else is
/// left they are the fallback pool.
#[test]
fn overflow_falls_back_to_session_signings_when_nobody_else_is_left() {
    let mut file = two_clubs(2000..2040);
    file.mutation.transferred_player_ids.extend(2000..2040);

    let query = OverflowQuery {
        exclude_player_id: Some(1001),
        ..OverflowQuery::default()
    };
    let candidate = file.find_overflow_release_candidate(102, query).unwrap();
    assert_eq!((candidate.slot, candidate.player_id), (39, 2039));
}

/// A move that fails after the source/overflow mutation must not leave a
/// half-applied change; every refusal is checked before any write.
#[test]
fn refused_mutations_leave_the_bytes_untouched() {
    let mut file = two_clubs(2000..2040);
    let before = file.bytes().to_vec();

    let cases: Vec<(&str, Result<(), RosterError>)> = vec![
        (
            "same_team",
            file.move_player(&MoveRequest::new(1001, 101, 101))
                .map(|_| ()),
        ),
        (
            "source_team_missing",
            file.move_player(&MoveRequest::new(1001, 999, 102))
                .map(|_| ()),
        ),
        (
            "destination_team_missing",
            file.move_player(&MoveRequest::new(1001, 101, 999))
                .map(|_| ()),
        ),
        (
            "source_player_missing",
            file.move_player(&MoveRequest::new(777, 101, 102))
                .map(|_| ()),
        ),
        (
            "overflow_not_authorized",
            file.move_player(&MoveRequest {
                allow_overflow_release: false,
                ..MoveRequest::new(1001, 101, 102)
            })
            .map(|_| ()),
        ),
        (
            "overflow_candidate_stale",
            file.move_player(&MoveRequest {
                planned_overflow_player_id: Some(9999),
                ..MoveRequest::new(1001, 101, 102)
            })
            .map(|_| ()),
        ),
        (
            "overflow_candidate_stale",
            file.move_player(&MoveRequest {
                planned_overflow_player_id: Some(1001),
                ..MoveRequest::new(1001, 101, 102)
            })
            .map(|_| ()),
        ),
        (
            "duplicate_club_registration",
            file.add_player(&AddRequest::new(1001, 102)).map(|_| ()),
        ),
        (
            "destination_player_exists",
            file.add_player(&AddRequest::new(2001, 102)).map(|_| ()),
        ),
        (
            "destination_team_missing",
            file.add_player(&AddRequest::new(5000, 999)).map(|_| ()),
        ),
        ("source_player_missing", file.release_player(777, 101)),
        ("source_team_missing", file.release_player(1001, 999)),
    ];
    for (code, result) in cases {
        let error = result.expect_err(code);
        assert_eq!(error.code(), code);
        assert_eq!(file.bytes(), before, "{code}");
    }
    assert!(file.transferred_player_ids().is_empty());
}

#[test]
fn refused_shirt_batches_write_nothing() {
    let mut file = single_club(101, 1000, 16);
    let before = file.bytes().to_vec();
    let batches: [&[(u32, u32)]; 6] = [
        &[(1000, 5), (1001, 17)], // shirt 5 is worn by 1004, who keeps it
        &[(1000, 1000)],
        &[(1000, 0)],
        &[(1000, 20), (1000, 21)],
        &[(1000, 20), (1001, 20)],
        &[(9999, 20)],
    ];
    for updates in batches {
        let error = file.update_player_shirt_numbers(101, updates).unwrap_err();
        assert!(
            matches!(
                error.kind(),
                RosterErrorKind::ShirtConflict
                    | RosterErrorKind::InvalidShirtNumber
                    | RosterErrorKind::PlayerNotOnTeam
            ),
            "{updates:?}: {error}"
        );
        assert_eq!(file.bytes(), before, "{updates:?}");
    }
}

/// The shirt allocator's label sets contain `LM`/`RM` but the save labels
/// are `LMF`/`RMF`, so they use the generic list (see DEVIATIONS.md).
#[test]
fn lmf_and_rmf_use_the_generic_shirt_list() {
    let used: HashSet<u32> = HashSet::from([7]);
    assert_eq!(assign_smart_shirt_number(&used, None, "LM", false), 6);
    assert_eq!(assign_smart_shirt_number(&used, None, "LMF", false), 8);
    assert_eq!(assign_smart_shirt_number(&used, None, "RMF", false), 8);
    assert_eq!(assign_smart_shirt_number(&used, None, "CMF", false), 6);
}
