//! Behavioural tests of roster reads, game-plan repair, moves, releases and
//! additions.
//!
//! Contract under test: mutations are transactional (an `Err` leaves the
//! buffer byte-identical), failures are typed (`RosterError::code()`), and
//! `set_team_formation` / `set_team_tactical_settings` return `Err` for
//! unsupported input.

use std::collections::{HashMap, HashSet};

use crate::catalog::PlayerCatalog;
use crate::edit_file::{EditFile, testing::Mock};
use crate::layout::*;
use crate::model::{PlayerInfo, Position};
use crate::release_policy::{PlayerUsage, ReleasePolicy};
use crate::roster::formation::normalize_game_plan_formation;
use crate::roster::shirt::assign_smart_shirt_number;
use crate::roster::{
    AddRequest, MoveRequest, OverflowCandidate, OverflowQuery, RESERVED_PLAYER_ID_MIN,
    RepairOptions, RosterError,
};

use super::support::{
    game_plan_formation_layout, lineup, mock, open, set_catalog_positions, set_lineup,
};

// ── helpers ──────────────────────────────────────────────────────────────

/// `_write_player_slot`: write one roster slot of `team` (id + shirt number).
fn write_player_slot(file: &mut EditFile, team: u32, slot: usize, player_id: u32, shirt: u16) {
    let entry = file.team_player_entry_offset(team).expect("roster entry");
    let id_at = entry + TP_PLAYER_IDS + slot * 4;
    let shirt_at = entry + TP_SHIRT_NUMBERS + slot * 2;
    let data = file.data_mut();
    data[id_at..id_at + 4].copy_from_slice(&player_id.to_le_bytes());
    data[shirt_at..shirt_at + 2].copy_from_slice(&shirt.to_le_bytes());
}

/// Fill all 40 slots of `team` with ids `1000 + slot` and shirts `slot + 1`.
fn fill_roster(file: &mut EditFile, team: u32) {
    for slot in 0..TP_MAX_PLAYERS {
        write_player_slot(file, team, slot, 1000 + slot as u32, slot as u16 + 1);
    }
}

fn roster_ids(file: &EditFile, team: u32) -> Vec<u32> {
    file.roster(team).expect("roster").roster().collect()
}

fn snapshot(file: &EditFile) -> Vec<u8> {
    file.bytes().to_vec()
}

/// A refused mutation: typed code and a byte-identical buffer.
fn assert_refused<T: std::fmt::Debug>(
    file: &EditFile,
    before: &[u8],
    result: Result<T, RosterError>,
    code: &str,
) {
    let error = result.expect_err("mutation must be refused");
    assert_eq!(error.code(), code, "{error}");
    assert_eq!(
        file.bytes(),
        before,
        "a refused mutation must not touch the save"
    );
}

/// Catalog metadata for `ids` that carries no registered position.
fn set_catalog_without_positions(file: &mut EditFile, ids: impl IntoIterator<Item = u32>) {
    let players: HashMap<u32, PlayerInfo> = ids
        .into_iter()
        .map(|player_id| {
            (
                player_id,
                PlayerInfo {
                    player_id,
                    name: format!("P{player_id}"),
                    ..PlayerInfo::default()
                },
            )
        })
        .collect();
    file.set_player_catalog(Some(PlayerCatalog {
        players,
        report: Default::default(),
    }));
}

/// Team 101 with 40 players (ids 1000..1040, shirts 1..=40), no league.
fn full_club_101() -> EditFile {
    open(&mock(
        vec![(101, (1000..1040).collect(), (1..=40).collect())],
        vec![],
    ))
}

/// Lineup with `first` in role 0 followed by every other slot in order.
fn lineup_with_first(first: u8) -> Vec<u8> {
    std::iter::once(first)
        .chain((0..TP_MAX_PLAYERS as u8).filter(|&slot| slot != first))
        .collect()
}

fn lineup_of(file: &EditFile, team: u32, count: usize) -> Vec<u8> {
    lineup(file, team)[..count].to_vec()
}

// ── TestEditFileHeader: repair + game-plan tests ─────────────────────────

// test_repair_game_plan_preserves_valid_order_and_fills_missing_slots
#[test]
fn repair_game_plan_preserves_valid_order_and_fills_missing_slots() {
    let mut file = open(&Mock::club(101, (1001..1017).collect(), (1..=16).collect()));
    let base = file.game_plan_offset(101).expect("game plan");
    file.data_mut()[base + GP_LINEUP..base + GP_LINEUP + 4].copy_from_slice(&[2, 2, 39, 0]);
    file.data_mut()[base + GP_CAPTAIN] = 39;

    let metrics = file.repair_game_plans(&RepairOptions::default());
    let report = file.validate_integrity();

    assert_eq!(
        lineup_of(&file, 101, 16),
        [2, 0, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 1, 3]
    );
    assert_eq!(file.bytes()[base + GP_CAPTAIN], 0xFF);
    assert_eq!(metrics.repaired_lineups, 1);
    assert_eq!(metrics.reset_roles, 1);
    assert!(report.is_valid(), "{:?}", report.errors);
}

// test_repair_game_plan_preserves_known_primary_goalkeeper
#[test]
fn repair_game_plan_preserves_known_primary_goalkeeper() {
    let mut file = full_club_101();
    set_lineup(&mut file, 101, &lineup_with_first(22));
    set_catalog_positions(&mut file, &[(1010, Position::Gk), (1022, Position::Gk)]);

    let metrics = file.repair_game_plans(&RepairOptions {
        preserve_existing_primary: true,
        ..RepairOptions::default()
    });

    let updated = lineup(&file, 101);
    assert_eq!(updated[0], 22);
    assert_eq!(updated[11], 10);
    assert_eq!(metrics.repaired_goalkeeper_roles, 0);
}

// test_roster_mutation_preserves_existing_primary_goalkeeper
#[test]
fn roster_mutation_preserves_existing_primary_goalkeeper() {
    let mut file = full_club_101();
    set_lineup(&mut file, 101, &lineup_with_first(22));
    set_catalog_positions(&mut file, &[(1010, Position::Gk), (1022, Position::Gk)]);

    file.release_player(1039, 101).expect("release");

    let updated = lineup(&file, 101);
    assert_eq!(updated[0], 22);
    assert_eq!(updated[11], 10);
}

// test_game_plan_removal_keeps_backup_goalkeeper_in_goalkeeper_role
#[test]
fn game_plan_removal_keeps_backup_goalkeeper_in_goalkeeper_role() {
    let mut file = full_club_101();
    let mut order = vec![1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 39, 0];
    order.extend(11..39);
    set_lineup(&mut file, 101, &order);
    set_catalog_positions(&mut file, &[(1000, Position::Gk), (1001, Position::Gk)]);

    file.release_player(1001, 101).expect("release");

    let roster = file.roster(101).expect("roster");
    assert_eq!(roster.player_ids[0], 1000);
    let updated = lineup(&file, 101);
    assert_eq!(updated[..11], [0, 2, 3, 4, 5, 6, 7, 8, 9, 10, 1]);
    assert_eq!(updated.iter().position(|&slot| slot == 0), Some(0));
    let mut sorted = updated.clone();
    sorted.sort_unstable();
    assert_eq!(sorted, (0..TP_MAX_PLAYERS as u8).collect::<Vec<_>>());
}

// test_game_plan_removal_does_not_promote_goalkeeper_to_outfield_role
#[test]
fn game_plan_removal_does_not_promote_goalkeeper_to_outfield_role() {
    let mut file = full_club_101();
    let mut order = vec![0, 2, 3, 4, 5, 6, 7, 8, 9, 39, 10, 1, 11];
    order.extend(12..39);
    set_lineup(&mut file, 101, &order);
    set_catalog_positions(
        &mut file,
        &[
            (1000, Position::Gk),
            (1001, Position::Gk),
            (1011, Position::Cf),
            (1010, Position::Cf),
        ],
    );

    file.release_player(1010, 101).expect("release");

    let roster = file.roster(101).expect("roster");
    let updated = lineup(&file, 101);
    assert_eq!(updated[..11], [0, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]);
    assert_eq!(roster.player_ids[10], 1039);
    assert_eq!(roster.player_ids[11], 1011);
    assert_eq!(updated.iter().position(|&slot| slot == 1), Some(11));
    let mut sorted = updated.clone();
    sorted.sort_unstable();
    assert_eq!(sorted, (0..TP_MAX_PLAYERS as u8).collect::<Vec<_>>());
}

// test_game_plan_removal_avoids_slot_zero_goalkeeper_without_metadata
#[test]
fn game_plan_removal_avoids_slot_zero_goalkeeper_without_metadata() {
    let mut file = full_club_101();
    let mut order = vec![1, 2, 3, 4, 5, 6, 7, 8, 9, 39, 10, 0, 11];
    order.extend(12..39);
    set_lineup(&mut file, 101, &order);

    file.release_player(1010, 101).expect("release");

    let updated = lineup(&file, 101);
    assert_eq!(updated[..11], [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]);
    assert_eq!(updated.iter().position(|&slot| slot == 0), Some(11));
    let mut sorted = updated.clone();
    sorted.sort_unstable();
    assert_eq!(sorted, (0..TP_MAX_PLAYERS as u8).collect::<Vec<_>>());
}

// test_game_plan_addition_uses_active_bench_role_for_sparse_roster_slot
#[test]
fn game_plan_addition_uses_active_bench_role_for_sparse_roster_slot() {
    let ids: Vec<u32> = std::iter::once(0).chain(1001..1040).collect();
    let mut file = open(&mock(vec![(101, ids, (0..40).collect())], vec![]));
    let mut order: Vec<u8> = (1..40).collect();
    order.push(0);
    set_lineup(&mut file, 101, &order);

    file.add_player(&AddRequest::new(9999, 101)).expect("add");

    let updated = lineup(&file, 101);
    // The signing (roster slot 0) becomes the first substitute, role 11.
    assert_eq!(updated[..11], (1..12).collect::<Vec<u8>>()[..]);
    assert_eq!(updated[11], 0);
    assert_eq!(updated[12..], (12..40).collect::<Vec<u8>>()[..]);
}

// test_game_plan_removal_backfills_starter_role_without_shift
#[test]
fn game_plan_removal_backfills_starter_role_without_shift() {
    let mut file = open(&Mock::club(101, (1000..1017).collect(), (1..=17).collect()));
    let mut order = vec![0, 1, 16];
    order.extend(2..16);
    order.resize(TP_MAX_PLAYERS, 0xFF);
    set_lineup(&mut file, 101, &order);

    file.release_player(1005, 101).expect("release");

    // The copied last-slot player (old slot 16, now slot 5) keeps role 2;
    // the departed role 6 is backfilled by the first substitute (slot 10)
    // and no other starter role moves.
    assert_eq!(
        lineup_of(&file, 101, 16),
        [0, 1, 5, 2, 3, 4, 10, 6, 7, 8, 9, 11, 12, 13, 14, 15]
    );
    let report = file.validate_integrity();
    assert!(report.is_valid(), "{:?}", report.errors);
}

// test_game_plan_addition_places_signing_on_first_bench_role
#[test]
fn game_plan_addition_places_signing_on_first_bench_role() {
    let mut file = open(&Mock::club(101, (1000..1015).collect(), (1..=15).collect()));
    let mut order: Vec<u8> = (0..15).collect();
    order.resize(TP_MAX_PLAYERS, 0xFF);
    set_lineup(&mut file, 101, &order);

    file.add_player(&AddRequest::new(9999, 101)).expect("add");

    let mut expected: Vec<u8> = (0..11).collect();
    expected.extend([15, 11, 12, 13, 14]);
    assert_eq!(lineup_of(&file, 101, 16), expected);
    let report = file.validate_integrity();
    assert!(report.is_valid(), "{:?}", report.errors);
}

// ── TestTeamRosters ──────────────────────────────────────────────────────

/// The `ef_with_rosters` fixture.
fn ef_with_rosters() -> EditFile {
    open(&mock(
        vec![
            (101, vec![1001, 1002, 1003, 0], vec![10, 7, 11, 0]),
            (102, vec![2001, 2002, 0, 0], vec![9, 5, 0, 0]),
        ],
        vec![],
    ))
}

// test_read_roster
#[test]
fn read_roster() {
    let file = ef_with_rosters();
    let roster = file.roster(101).expect("roster");
    assert_eq!(roster.team_id, 101);
    assert_eq!(roster.roster().collect::<Vec<_>>(), [1001, 1002, 1003]);
    assert_eq!(roster.roster_size(), 3);
}

// test_roster_not_found
#[test]
fn roster_not_found() {
    assert!(ef_with_rosters().roster(999).is_none());
}

// test_get_all_rosters
#[test]
fn get_all_rosters() {
    let rosters = ef_with_rosters().rosters();
    assert_eq!(rosters.len(), 2);
    let teams: HashSet<u32> = rosters.iter().map(|roster| roster.team_id).collect();
    assert!(teams.contains(&101));
    assert!(teams.contains(&102));
}

// test_shirt_numbers
#[test]
fn shirt_numbers() {
    let roster = ef_with_rosters().roster(101).expect("roster");
    assert_eq!(roster.shirt_numbers[0], 10);
    assert_eq!(roster.shirt_numbers[1], 7);
    assert_eq!(roster.shirt_numbers[2], 11);
}

// test_captain_role_reads_and_writes_active_roster_slot
#[test]
fn captain_role_reads_and_writes_active_roster_slot() {
    let mut file = ef_with_rosters();
    let base = file.game_plan_offset(101).expect("game plan");

    assert_eq!(file.team_captain_player(101), Some(1001));
    file.set_team_captain(101, 1003).expect("captain");
    assert_eq!(file.team_captain_player(101), Some(1003));
    assert_eq!(file.bytes()[base + GP_CAPTAIN], 2);

    let before = snapshot(&file);
    let result = file.set_team_captain(101, 9999);
    assert_refused(&file, &before, result, "captain_not_on_roster");
    assert_eq!(file.team_captain_player(101), Some(1003));
}

// test_tactical_settings_only_change_supported_main_preset
#[test]
fn tactical_settings_only_change_supported_main_preset() {
    let mut file = ef_with_rosters();
    let base = file.game_plan_offset(101).expect("game plan");
    let expected = [
        (TacticalField::AttackingStyle, 1u8),
        (TacticalField::BuildUp, 0),
        (TacticalField::AttackingArea, 0),
        (TacticalField::DefensiveStyle, 1),
        (TacticalField::ContainmentArea, 1),
        (TacticalField::Pressuring, 0),
        (TacticalField::DefensiveLine, 1),
        (TacticalField::Compactness, 10),
    ];
    let address = |field: TacticalField| GP_POSITION_PRESETS[0] + field.offset();
    let tactical_offsets: HashSet<usize> =
        expected.iter().map(|&(field, _)| address(field)).collect();
    for &(field, value) in &expected {
        file.data_mut()[base + address(field)] = match field {
            TacticalField::DefensiveLine | TacticalField::Compactness => value.saturating_sub(1),
            _ => 1 - value,
        };
    }

    let before = file.game_plan(101).expect("game plan").to_vec();
    assert_eq!(file.set_team_tactical_settings(101, &expected), Ok(8));
    let after = file.game_plan(101).expect("game plan").to_vec();
    let changed: HashSet<usize> = before
        .iter()
        .zip(&after)
        .enumerate()
        .filter(|(_, (old, new))| old != new)
        .map(|(offset, _)| offset)
        .collect();
    assert_eq!(changed, tactical_offsets);
    for &(field, value) in &expected {
        assert_eq!(after[address(field)], value, "{}", field.name());
    }
    assert_eq!(file.set_team_tactical_settings(101, &expected), Ok(0));

    // `{"attacking_style": 1, "unsupported": 0}` changes nothing: an unknown
    // setting name is not representable as a `TacticalField`.
    assert_eq!(TacticalField::from_name("unsupported"), None);
    assert_eq!(
        file.set_team_tactical_settings(101, &[(TacticalField::AttackingStyle, 1)]),
        Ok(0)
    );

    let unchanged = snapshot(&file);
    // A boolean setting (`{"pressuring": true}`) has no typed counterpart (`u8` values).
    for invalid in [
        (TacticalField::DefensiveLine, 0),
        (TacticalField::DefensiveLine, 11),
        (TacticalField::Compactness, 0),
        (TacticalField::Compactness, 11),
        (TacticalField::Pressuring, 2),
    ] {
        let result = file.set_team_tactical_settings(101, &[invalid]);
        assert_refused(&file, &unchanged, result, "invalid_tactical_setting");
    }
}

// test_game_plan_formation_mirrors_roles_and_geometry_into_all_presets
#[test]
fn game_plan_formation_mirrors_roles_and_geometry_into_all_presets() {
    let mut file = ef_with_rosters();
    let before = file.game_plan(101).expect("game plan").to_vec();
    let layout = game_plan_formation_layout("4-2-3-1").expect("layout");
    assert_eq!(
        layout.roles.map(|position| position.code()),
        [0, 1, 1, 3, 2, 4, 4, 8, 10, 9, 12]
    );
    assert_eq!(
        layout.coordinates,
        [
            (3, 52),
            (11, 64),
            (11, 40),
            (13, 88),
            (13, 16),
            (19, 64),
            (22, 40),
            (33, 52),
            (41, 84),
            (41, 20),
            (43, 52),
        ]
    );

    let changed = file.set_team_formation(101, "4-2-3-1").expect("formation");
    let after = file.game_plan(101).expect("game plan").to_vec();
    let expected_roles: Vec<u8> = layout
        .roles
        .iter()
        .map(|position| position.code())
        .collect();
    let expected_coordinates: Vec<u8> = layout
        .coordinates
        .iter()
        .flat_map(|&(vertical, horizontal)| [vertical, horizontal])
        .collect();
    let mut allowed: HashSet<usize> = HashSet::new();
    for preset in GP_POSITION_PRESETS {
        for (phase_index, phase_offset) in GP_POSITION_PHASE_OFFSETS.into_iter().enumerate() {
            let role_offset = preset + phase_offset;
            allowed.extend(role_offset..role_offset + 11);
            let coordinate_offset = preset + GP_POSITION_COORDINATE_OFFSETS[phase_index];
            allowed.extend(coordinate_offset..coordinate_offset + 22);

            assert_eq!(after[role_offset..role_offset + 11], expected_roles[..]);
            assert_eq!(
                after[coordinate_offset..coordinate_offset + 22],
                expected_coordinates[..]
            );
        }
    }

    let changed_offsets: HashSet<usize> = before
        .iter()
        .zip(&after)
        .enumerate()
        .filter(|(_, (old, new))| old != new)
        .map(|(offset, _)| offset)
        .collect();
    assert!(changed > 0);
    assert_eq!(changed, changed_offsets.len());
    assert!(changed_offsets.is_subset(&allowed));
    assert_eq!(file.set_team_formation(101, "4-2-3-1"), Ok(0));
}

// test_unsupported_game_plan_formation_preserves_all_bytes
// (`set_team_formation` takes `&str`, so the empty string stands in for a
// missing formation.)
#[test]
fn unsupported_game_plan_formation_preserves_all_bytes() {
    for formation in [
        "",
        "4-2-3",
        "2-4-4",
        "4-2-2-3",
        "4-2-3-1-0",
        "4-1-1-1-1-2",
        "4-2-three-1",
    ] {
        let mut file = ef_with_rosters();
        let before = snapshot(&file);
        assert_eq!(
            normalize_game_plan_formation(formation),
            None,
            "{formation:?}"
        );
        let result = file.set_team_formation(101, formation);
        assert_refused(&file, &before, result, "unsupported_formation");
    }
}

// An unknown team yields a typed error and leaves the bytes alone.
#[test]
fn unknown_team_formation_preserves_all_bytes() {
    let mut file = ef_with_rosters();
    let before = snapshot(&file);
    let result = file.set_team_formation(999, "4-2-3-1");
    assert_refused(&file, &before, result, "game_plan_not_found");
}

// test_game_plan_formation_layout_supports_common_line_shapes
#[test]
fn game_plan_formation_layout_supports_common_line_shapes() {
    for formation in ["3-5-2", "4-4-2", "5-3-2", "4-2-3-1"] {
        let layout = game_plan_formation_layout(formation).expect(formation);
        assert_eq!(layout.roles.len(), 11);
        assert_eq!(layout.coordinates.len(), 11);
    }
    let layout = game_plan_formation_layout("4-1-4-1").expect("layout");
    assert_eq!(
        layout.roles.map(|position| position.code()),
        [0, 1, 1, 3, 2, 4, 5, 5, 7, 6, 12]
    );
}

// test_game_plan_formation_layout_supports_five_row_shapes
#[test]
fn game_plan_formation_layout_supports_five_row_shapes() {
    // Diamond: DMF / two CMFs / AMF / two CFs, rows strictly advancing.
    let layout = game_plan_formation_layout("4-1-2-1-2").expect("layout");
    assert_eq!(
        layout.roles.map(|position| position.code()),
        [0, 1, 1, 3, 2, 4, 5, 5, 8, 12, 12]
    );
    let verticals: Vec<u8> = layout
        .coordinates
        .iter()
        .map(|&(vertical, _)| vertical)
        .collect();
    assert!(verticals[5] < verticals[6]);
    assert!(verticals[6] < verticals[8]);
    assert!(verticals[8] < verticals[9]);
    assert_eq!(
        game_plan_formation_layout("3-4-1-1-1")
            .expect("layout")
            .roles
            .map(|position| position.code()),
        [0, 1, 1, 1, 5, 5, 7, 6, 8, 11, 12]
    );
}

// test_five_row_formation_is_written_to_every_preset
#[test]
fn five_row_formation_is_written_to_every_preset() {
    let mut file = ef_with_rosters();
    let base = file.game_plan_offset(101).expect("game plan");
    assert!(
        file.set_team_formation(101, "4-1-2-1-2")
            .expect("formation")
            > 0
    );
    let roles: Vec<u8> = game_plan_formation_layout("4-1-2-1-2")
        .expect("layout")
        .roles
        .iter()
        .map(|position| position.code())
        .collect();
    for preset in GP_POSITION_PRESETS {
        for phase_offset in GP_POSITION_PHASE_OFFSETS {
            let start = base + preset + phase_offset;
            assert_eq!(file.bytes()[start..start + 11], roles[..]);
        }
    }
}

// test_batch_shirt_updates_support_swaps
#[test]
fn batch_shirt_updates_support_swaps() {
    let mut file = ef_with_rosters();
    file.update_player_shirt_numbers(101, &[(1001, 7), (1002, 10)])
        .expect("swap");
    let roster = file.roster(101).expect("roster");
    assert_eq!(roster.shirt_numbers[..3], [7, 10, 11]);
}

// test_batch_shirt_updates_reject_unplanned_conflicts
#[test]
fn batch_shirt_updates_reject_unplanned_conflicts() {
    let mut file = ef_with_rosters();
    let before = snapshot(&file);
    let result = file.update_player_shirt_numbers(101, &[(1001, 11)]);
    assert_refused(&file, &before, result, "shirt_conflict");
    let roster = file.roster(101).expect("roster");
    assert_eq!(roster.shirt_numbers[..3], [10, 7, 11]);
}

// ── TestMovePlayer ───────────────────────────────────────────────────────

/// `_build_transfer_data`.
fn transfer_file() -> EditFile {
    ef_with_rosters()
}

// test_basic_transfer
#[test]
fn basic_transfer() {
    let mut file = open(&mock(
        vec![
            (101, (1001..1018).collect(), (1..=17).collect()),
            (102, (2001..2017).collect(), (18..=33).collect()),
        ],
        vec![101, 102],
    ));

    file.move_player(&MoveRequest::new(1002, 101, 102))
        .expect("move");
    let report = file.validate_integrity();
    assert!(report.is_valid(), "{:?}", report.errors);

    // Source: 1002 removed, 1017 compacted into slot 1.
    let src = file.roster(101).expect("roster");
    assert!(!src.has_player(1002));
    assert_eq!(src.roster_size(), 16);
    assert_eq!(src.player_ids[0], 1001);
    assert_eq!(src.player_ids[1], 1017);

    // Destination: 1002 added.
    let dst = file.roster(102).expect("roster");
    assert!(dst.has_player(1002));
    assert_eq!(dst.roster_size(), 17);
}

// test_transfer_last_player
#[test]
fn transfer_last_player() {
    let mut file = transfer_file();
    file.move_player(&MoveRequest::new(1003, 101, 102))
        .expect("move");

    let src = file.roster(101).expect("roster");
    assert_eq!(src.roster_size(), 2);
    assert!(!src.has_player(1003));
    let dst = file.roster(102).expect("roster");
    assert_eq!(dst.roster_size(), 3);
    assert!(dst.has_player(1003));
}

// test_transfer_player_not_on_team
#[test]
fn transfer_player_not_on_team() {
    let mut file = transfer_file();
    let before = snapshot(&file);
    let result = file.move_player(&MoveRequest::new(9999, 101, 102));
    assert_refused(&file, &before, result, "source_player_missing");
}

// test_transfer_already_on_dest (moving 2001 from 102 to 102 is refused)
#[test]
fn transfer_already_on_dest() {
    let mut file = transfer_file();
    let before = snapshot(&file);
    let result = file.move_player(&MoveRequest::new(2001, 102, 102));
    assert_refused(&file, &before, result, "same_team");
}

// test_transfer_source_team_not_found
#[test]
fn transfer_source_team_not_found() {
    let mut file = transfer_file();
    let before = snapshot(&file);
    let result = file.move_player(&MoveRequest::new(1001, 999, 102));
    assert_refused(&file, &before, result, "source_team_missing");
}

// test_transfer_dest_team_not_found
#[test]
fn transfer_dest_team_not_found() {
    let mut file = transfer_file();
    let before = snapshot(&file);
    let result = file.move_player(&MoveRequest::new(1001, 101, 999));
    assert_refused(&file, &before, result, "destination_team_missing");
}

// test_transfer_assigns_shirt_number
#[test]
fn transfer_assigns_shirt_number() {
    let mut file = transfer_file();
    file.move_player(&MoveRequest::new(1001, 101, 102))
        .expect("move");

    let dst = file.roster(102).expect("roster");
    let index = dst.player_index(1001).expect("moved player");
    assert!(dst.shirt_numbers[index] > 0);
}

// test_keeper_alias_uses_goalkeeper_shirt_priority
#[test]
fn keeper_alias_uses_goalkeeper_shirt_priority() {
    assert_eq!(
        assign_smart_shirt_number(&HashSet::from([1]), None, "Keeper", false),
        12
    );
}

// test_transfer_ignores_stale_shirt_numbers_in_empty_slots
#[test]
fn transfer_ignores_stale_shirt_numbers_in_empty_slots() {
    let stale_shirts: Vec<u16> = [1, 2].into_iter().chain(3..41).collect();
    let mut file = open(&mock(
        vec![
            (101, vec![1001, 1002, 1003], vec![10, 7, 11]),
            (102, vec![2001, 2002], stale_shirts),
        ],
        vec![101, 102],
    ));

    file.move_player(&MoveRequest::new(1001, 101, 102))
        .expect("move");

    let destination = file.roster(102).expect("roster");
    let index = destination.player_index(1001).expect("moved player");
    assert_eq!(destination.shirt_numbers[index], 10);
}

// test_multiple_transfers
#[test]
fn multiple_transfers() {
    let mut file = transfer_file();
    file.move_player(&MoveRequest::new(1001, 101, 102))
        .expect("move");
    file.move_player(&MoveRequest::new(2001, 102, 101))
        .expect("move back");

    let src = file.roster(101).expect("roster");
    let dst = file.roster(102).expect("roster");
    assert!(!src.has_player(1001));
    assert!(src.has_player(2001));
    assert!(dst.has_player(1001));
    assert!(!dst.has_player(2001));
}

/// 101 holds one player, 102 is full (2000..2040); league [101, 102].
fn full_destination_file() -> EditFile {
    open(&mock(
        vec![
            (101, vec![1001], vec![7]),
            (102, (2000..2040).collect(), (1..=40).collect()),
        ],
        vec![101, 102],
    ))
}

// test_move_player_uses_planned_overflow_candidate
#[test]
fn move_player_uses_planned_overflow_candidate() {
    let mut file = full_destination_file();

    file.move_player(&MoveRequest {
        planned_overflow_player_id: Some(2038),
        ..MoveRequest::new(1001, 101, 102)
    })
    .expect("move");

    let destination = file.roster(102).expect("roster");
    assert!(!destination.has_player(2038));
    assert!(destination.has_player(2039));
    assert!(destination.has_player(1001));
}

// test_add_player_uses_planned_overflow_candidate
#[test]
fn add_player_uses_planned_overflow_candidate() {
    let mut file = full_destination_file();

    file.add_player(&AddRequest {
        planned_overflow_player_id: Some(2038),
        ..AddRequest::new(3001, 102)
    })
    .expect("add");

    let destination = file.roster(102).expect("roster");
    assert!(!destination.has_player(2038));
    assert!(destination.has_player(2039));
    assert!(destination.has_player(3001));
}

// test_add_player_reports_stale_planned_overflow_candidate
// (`last_mutation_error_code` is now the typed error's `code()`.)
#[test]
fn add_player_reports_stale_planned_overflow_candidate() {
    let mut file = full_destination_file();
    let before = snapshot(&file);

    let result = file.add_player(&AddRequest {
        planned_overflow_player_id: Some(9999),
        ..AddRequest::new(3001, 102)
    });

    assert_refused(&file, &before, result, "overflow_candidate_stale");
    assert_eq!(roster_ids(&file, 102), (2000..2040).collect::<Vec<u32>>());
}

// ── TestReleaseAndAddPlayer ──────────────────────────────────────────────

/// `_build_test_data`: two small clubs without league membership.
fn release_file() -> EditFile {
    open(&mock(
        vec![
            (101, vec![1001, 1002, 1003], vec![7, 10, 11]),
            (102, vec![2001, 2002], vec![1, 9]),
        ],
        vec![],
    ))
}

// test_release_player_compaction
#[test]
fn release_player_compaction() {
    let mut file = release_file();
    file.release_player(1002, 101).expect("release");

    let roster = file.roster(101).expect("roster");
    assert!(!roster.has_player(1002));
    assert_eq!(roster.roster_size(), 2);
    assert_eq!(roster.player_ids[0], 1001);
    assert_eq!(roster.player_ids[1], 1003); // compacted from slot 2
}

// test_get_player_shirt_number
#[test]
fn get_player_shirt_number() {
    let file = release_file();
    assert_eq!(file.player_shirt_number(101, 1002), Some(10));
    assert_eq!(file.player_shirt_number(101, 9999), None);
    assert_eq!(file.player_shirt_number(999, 1002), None);
}

// test_release_player_not_found
#[test]
fn release_player_not_found() {
    let mut file = release_file();
    let before = snapshot(&file);
    let result = file.release_player(9999, 101);
    assert_refused(&file, &before, result, "source_player_missing");
}

// test_add_player_from_free_agent
#[test]
fn add_player_from_free_agent() {
    let mut file = release_file();
    file.add_player(&AddRequest::new(3001, 102)).expect("add");

    let roster = file.roster(102).expect("roster");
    assert!(roster.has_player(3001));
    assert_eq!(roster.roster_size(), 3);
}

// test_add_ignores_stale_shirt_numbers_in_empty_slots
#[test]
fn add_ignores_stale_shirt_numbers_in_empty_slots() {
    let stale_shirts: Vec<u16> = [1, 2].into_iter().chain(3..41).collect();
    let mut file = open(&mock(
        vec![
            (101, vec![1001, 1002, 1003], vec![7, 10, 11]),
            (102, vec![2001, 2002], stale_shirts),
        ],
        vec![101, 102],
    ));

    file.add_player(&AddRequest::new(3001, 102)).expect("add");

    let destination = file.roster(102).expect("roster");
    let index = destination.player_index(3001).expect("signing");
    assert_eq!(destination.shirt_numbers[index], 7);
}

// test_add_player_already_exists
#[test]
fn add_player_already_exists() {
    let mut file = release_file();
    let before = snapshot(&file);
    let result = file.add_player(&AddRequest::new(2001, 102));
    assert_refused(&file, &before, result, "destination_player_exists");
}

// test_add_player_rejects_existing_player_on_another_club
#[test]
fn add_player_rejects_existing_player_on_another_club() {
    let mut file = release_file();
    let before = snapshot(&file);
    let result = file.add_player(&AddRequest::new(1001, 102));
    assert_refused(&file, &before, result, "duplicate_club_registration");
    assert!(file.roster(101).expect("roster").has_player(1001));
    assert!(!file.roster(102).expect("roster").has_player(1001));
}

// test_overflow_releases_deepest_reserve
#[test]
fn overflow_releases_deepest_reserve() {
    let mut file = release_file();
    fill_roster(&mut file, 101);

    let roster = file.roster(101).expect("roster");
    assert!(roster.is_full());
    assert_eq!(roster.roster_size(), 40);

    // Metadata is intentionally irrelevant to overflow ranking.
    set_catalog_without_positions(&mut file, (0..40).map(|i| 1000 + i));

    let placement = file.add_player(&AddRequest::new(9999, 101)).expect("add");
    assert_eq!(placement.released, Some(1039));

    let new_roster = file.roster(101).expect("roster");
    assert_eq!(new_roster.roster_size(), 40);
    assert!(new_roster.has_player(9999));
    // The deepest reserve (slot 39) is released.
    assert!(!new_roster.has_player(1039));
}

// test_overflow_uses_game_plan_depth_over_roster_slot
#[test]
fn overflow_uses_game_plan_depth_over_roster_slot() {
    let mut file = release_file();
    fill_roster(&mut file, 101);
    let mut order = lineup(&file, 101);
    order.swap(18, 39);
    set_lineup(&mut file, 101, &order);

    assert_eq!(
        file.find_overflow_release_candidate(101, OverflowQuery::default()),
        Some(OverflowCandidate {
            slot: 18,
            player_id: 1018
        })
    );
}

// test_overflow_prefers_native_reserve_over_created_player
#[test]
fn overflow_prefers_native_reserve_over_created_player() {
    let mut file = release_file();
    for slot in 0..39 {
        write_player_slot(&mut file, 101, slot, 1000 + slot as u32, slot as u16 + 1);
    }
    write_player_slot(&mut file, 101, 39, RESERVED_PLAYER_ID_MIN, 40);

    assert_eq!(
        file.find_overflow_release_candidate(101, OverflowQuery::default()),
        Some(OverflowCandidate {
            slot: 38,
            player_id: 1038
        })
    );
}

// test_overflow_respects_per_club_protected_players
#[test]
fn overflow_respects_per_club_protected_players() {
    let mut file = release_file();
    fill_roster(&mut file, 101);
    file.attach_release_policy(Some(&ReleasePolicy {
        protected_players: [(101, [1039].into())].into(),
        ..ReleasePolicy::default()
    }));

    assert_eq!(
        file.find_overflow_release_candidate(101, OverflowQuery::default()),
        Some(OverflowCandidate {
            slot: 38,
            player_id: 1038
        })
    );
}

// test_overflow_prefers_low_usage_over_deeper_unknown_reserve
#[test]
fn overflow_prefers_low_usage_over_deeper_unknown_reserve() {
    let mut file = release_file();
    fill_roster(&mut file, 101);
    let usage = |minutes, starts, appearances| PlayerUsage {
        minutes,
        starts,
        appearances,
        news_mentions: 0,
    };
    file.attach_release_policy(Some(&ReleasePolicy {
        usage: [(1038, usage(0, 0, 0)), (1039, usage(900, 20, 30))].into(),
        ..ReleasePolicy::default()
    }));

    assert_eq!(
        file.find_overflow_release_candidate(101, OverflowQuery::default()),
        Some(OverflowCandidate {
            slot: 38,
            player_id: 1038
        })
    );
}

// test_overflow_protects_goalkeepers
#[test]
fn overflow_protects_goalkeepers() {
    let mut file = release_file();
    fill_roster(&mut file, 101);
    let positions: Vec<(u32, Position)> = (0..40)
        .map(|i| {
            let position = if i == 0 || i == 25 {
                Position::Gk
            } else {
                Position::Cf
            };
            (1000 + i, position)
        })
        .collect();
    set_catalog_positions(&mut file, &positions);

    file.add_player(&AddRequest {
        allow_overflow_release: true,
        ..AddRequest::new(9999, 101)
    })
    .expect("add");

    let new_roster = file.roster(101).expect("roster");
    assert!(new_roster.has_player(1025));
    assert!(!new_roster.has_player(1039));
}

// test_overflow_releases_deepest_reserve_with_excess_goalkeepers
#[test]
fn overflow_releases_deepest_reserve_with_excess_goalkeepers() {
    let mut file = release_file();
    fill_roster(&mut file, 101);
    let positions: Vec<(u32, Position)> = (0..40)
        .map(|i| {
            let position = if matches!(i, 0 | 11 | 39) {
                Position::Gk
            } else {
                Position::Cmf
            };
            (1000 + i, position)
        })
        .collect();
    set_catalog_positions(&mut file, &positions);

    file.add_player(&AddRequest {
        allow_overflow_release: true,
        ..AddRequest::new(9999, 101)
    })
    .expect("add");

    let new_roster = file.roster(101).expect("roster");
    assert!(!new_roster.has_player(1039));
    assert!(new_roster.has_player(1000));
    assert!(new_roster.has_player(1011));
}

// test_full_roster_rejects_explicitly_disabled_overflow_release
#[test]
fn full_roster_rejects_explicitly_disabled_overflow_release() {
    let mut file = release_file();
    fill_roster(&mut file, 101);
    let before = snapshot(&file);

    let result = file.add_player(&AddRequest {
        allow_overflow_release: false,
        ..AddRequest::new(9999, 101)
    });

    assert_refused(&file, &before, result, "overflow_not_authorized");
    assert_eq!(roster_ids(&file, 101), (1000..1040).collect::<Vec<u32>>());
}
