//! Game-plan, overflow and repair behaviour.
//!
//! Contract under test:
//! * mutations are transactional and report typed errors;
//! * `find_overflow_release_candidate` yields `None` when no player can be
//!   released;
//! * catalog positions come from [`set_catalog_positions`] and native
//!   Player.bin metadata from [`FakeNative`];
//! * a player entry in the save makes `EditFile::abilities` answer (decoded
//!   from zero bytes), so tests that rank by rating keep unrated players out
//!   of the save.

use std::collections::{HashMap, HashSet};

use super::support::*;
use crate::edit_file::EditFile;
use crate::layout::*;
use crate::model::Position;
use crate::roster::{AddRequest, MoveRequest, OverflowQuery, RepairOptions, RoleGroup};

const TEAM: u32 = 101;
const EMPTY_ROLE: u8 = 0xFF;

// ── helpers ──────────────────────────────────────────────────────────────

fn pos(label: &str) -> Position {
    Position::from_label(label).unwrap_or_else(|| panic!("unknown position label {label}"))
}

/// A save with one club `TEAM` (shirts `1..=n`) and `num_players`
/// edited-player entries counted in the header (none are written).
fn club(ids: Vec<u32>, num_players: usize) -> EditFile {
    let shirts = (1..=ids.len() as u16).collect();
    let mut m = mock(vec![(TEAM, ids, shirts)], vec![TEAM]);
    m.players = num_players;
    open(&m)
}

/// Like [`club`] with one save player record per id (`players` entries).
fn club_with_records(ids: Vec<u32>) -> EditFile {
    let shirts = (1..=ids.len() as u16).collect();
    let mut m = mock(vec![(TEAM, ids.clone(), shirts)], vec![TEAM]);
    m.players = ids.len();
    m.player_entries = ids.iter().map(|&id| (id, format!("Player {id}"))).collect();
    open(&m)
}

fn ids_range(first: u32, end: u32) -> Vec<u32> {
    (first..end).collect()
}

/// Attach Player.bin records holding only a registered position.
fn attach_positions(file: &mut EditFile, records: &[(u32, &str)]) {
    let mut native = FakeNative::default();
    for &(id, label) in records {
        native.records.insert(id, FakeRecord::new(pos(label)));
    }
    native.attach(file);
}

/// Absolute offsets of every (preset, phase) position-code array of `team`.
fn phase_arrays(file: &EditFile, team: u32) -> Vec<usize> {
    let base = file.game_plan_offset(team).expect("game plan");
    GP_POSITION_PRESETS
        .iter()
        .flat_map(|&preset| {
            GP_POSITION_PHASE_OFFSETS
                .iter()
                .map(move |&phase| base + preset + phase)
        })
        .collect()
}

/// Write the same position codes into every (preset, phase) array.
fn set_position_arrays(file: &mut EditFile, team: u32, codes: &[u8]) {
    for at in phase_arrays(file, team) {
        file.data_mut()[at..at + codes.len()].copy_from_slice(codes);
    }
}

/// The first `len` position codes of every array.
fn position_arrays(file: &EditFile, team: u32, len: usize) -> Vec<Vec<u8>> {
    phase_arrays(file, team)
        .into_iter()
        .map(|at| file.bytes()[at..at + len].to_vec())
        .collect()
}

fn lineup_prefix(file: &EditFile, len: usize) -> Vec<u8> {
    lineup(file, TEAM)[..len].to_vec()
}

fn roster_ids(file: &EditFile) -> [u32; TP_MAX_PLAYERS] {
    file.roster(TEAM).expect("roster").player_ids
}

/// Player ids of the XI (lineup roles 0..11).
fn starter_ids(file: &EditFile) -> Vec<u32> {
    let ids = roster_ids(file);
    lineup_prefix(file, FIRST_TEAM_SLOT_COUNT)
        .into_iter()
        .map(|slot| ids[usize::from(slot)])
        .collect()
}

fn set_roster_slot(file: &mut EditFile, team: u32, slot: usize, player_id: u32) {
    let at = file.team_player_entry_offset(team).expect("roster") + TP_PLAYER_IDS + slot * 4;
    file.data_mut()[at..at + 4].copy_from_slice(&player_id.to_le_bytes());
}

fn lineup_bytes(head: &[u8]) -> Vec<u8> {
    let mut lineup = head.to_vec();
    lineup.extend((0..TP_MAX_PLAYERS as u8).filter(|slot| !head.contains(slot)));
    lineup
}

fn error_messages(file: &EditFile) -> Vec<String> {
    file.validate_integrity().error_messages()
}

fn assert_valid(file: &EditFile) {
    let report = file.validate_integrity();
    assert!(report.is_valid(), "{:?}", report.error_messages());
}

fn move_to(
    file: &mut EditFile,
    player_id: u32,
    from: u32,
    to: u32,
    position: &str,
) -> crate::roster::Placement {
    file.move_player(&MoveRequest {
        position,
        ..MoveRequest::new(player_id, from, to)
    })
    .expect("move")
}

fn add(file: &mut EditFile, player_id: u32, position: &str) -> crate::roster::Placement {
    file.add_player(&AddRequest {
        position,
        ..AddRequest::new(player_id, TEAM)
    })
    .expect("add")
}

/// `repair_game_plans` re-picking `TEAM`'s XI and bench.
fn aligned(preserve_existing_primary: bool) -> RepairOptions {
    RepairOptions {
        preserve_existing_primary,
        align_teams: HashSet::from([TEAM]),
    }
}

// ── `_game_plan_team` ────────────────────────────────────────────────────

const FOUR_THREE_THREE: [&str; 11] = [
    "GK", "CB", "CB", "RB", "LB", "DMF", "CMF", "CMF", "CF", "RWF", "LWF",
];

/// 4-3-3 followed by `extra` positions.
fn squad(extra: &[&'static str]) -> Vec<&'static str> {
    FOUR_THREE_THREE.iter().chain(extra).copied().collect()
}

#[derive(Default)]
struct Spec {
    caps: HashMap<u32, u32>,
    ages: HashMap<u32, u32>,
    values: HashMap<u32, u32>,
    abilities: HashMap<u32, u8>,
    /// Give every player a save record (needed for strong-foot metadata).
    save_records: bool,
}

fn hm<V: Copy>(pairs: &[(u32, V)]) -> HashMap<u32, V> {
    pairs.iter().copied().collect()
}

/// `_rated(count, value)`: abilities for the first `count` players.
fn rated(count: u32) -> HashMap<u32, u8> {
    (5000..5000 + count).map(|id| (id, 75)).collect()
}

/// `_game_plan_team` without attaching the Player.bin records.
fn game_plan_team_records(positions: &[&str], spec: Spec) -> (EditFile, Vec<u32>, FakeNative) {
    let ids = ids_range(5000, 5000 + positions.len() as u32);
    let file = if spec.save_records {
        club_with_records(ids.clone())
    } else {
        club(ids.clone(), 0)
    };
    let mut native = FakeNative::default();
    for (&id, &label) in ids.iter().zip(positions) {
        let position = pos(label);
        let mut record = FakeRecord::new(position);
        record.age = spec.ages.get(&id).copied().unwrap_or(24);
        record.caps = spec.caps.get(&id).copied().unwrap_or(0);
        record.market_value_eur = spec.values.get(&id).copied().unwrap_or(0);
        record.abilities = spec
            .abilities
            .get(&id)
            .map(|&value| uniform_abilities(position, value, &[]));
        native.records.insert(id, record);
    }
    (file, ids, native)
}

fn game_plan_team(positions: &[&str], spec: Spec) -> (EditFile, Vec<u32>) {
    let (mut file, ids, native) = game_plan_team_records(positions, spec);
    native.attach(&mut file);
    (file, ids)
}

// ── overflow ─────────────────────────────────────────────────────────────

// test_playerbin_position_protects_unedited_goalkeeper_from_overflow_release
#[test]
fn playerbin_position_protects_unedited_goalkeeper_from_overflow_release() {
    let mut file = single_club(TEAM, 1000, 40);
    // A name-only catalog carries no positions: nothing to set.
    attach_positions(&mut file, &[(1025, "GK")]);

    let candidate = file
        .find_overflow_release_candidate(TEAM, OverflowQuery::default())
        .expect("candidate");
    assert_eq!((candidate.slot, candidate.player_id), (39, 1039));
}

// test_playerbin_only_metadata_still_allows_role_based_overflow_release
#[test]
fn playerbin_only_metadata_still_allows_role_based_overflow_release() {
    let mut file = single_club(TEAM, 1000, 40);
    FakeNative::default().attach(&mut file);

    let candidate = file
        .find_overflow_release_candidate(TEAM, OverflowQuery::default())
        .expect("candidate");
    assert_eq!((candidate.slot, candidate.player_id), (39, 1039));
}

// test_overflow_releases_lowest_overall_reserve_but_protects_first_team
#[test]
fn overflow_releases_lowest_overall_reserve_but_protects_first_team() {
    // 11 starters, 7 matchday bench, 4 reserves (slots 18-21).
    let positions = squad(&[
        "CB", "CB", "CB", "CB", "CB", "CB", "CB", "CMF", "CMF", "CMF", "CMF",
    ]);
    let mut abilities = rated(22);
    abilities.insert(5005, 45); // lowest overall, but a first-team starter
    abilities.insert(5019, 50); // lowest overall among reserves
    let (file, ids) = game_plan_team(
        &positions,
        Spec {
            abilities,
            ..Spec::default()
        },
    );

    let candidate = file
        .find_overflow_release_candidate(TEAM, OverflowQuery::default())
        .expect("candidate");
    assert_eq!((candidate.slot, candidate.player_id), (19, ids[19]));

    let detail = file
        .describe_overflow_release_candidate(TEAM, ids[19], OverflowQuery::default())
        .expect("explanation");
    assert_eq!(detail.role_group, RoleGroup::Reserve);
    assert_eq!(detail.selection_basis, "lowest_overall_then_deepest_role");
}

// test_overflow_ranks_unknown_overall_after_known_reserves
#[test]
fn overflow_ranks_unknown_overall_after_known_reserves() {
    let positions = squad(&["CB", "CB", "CB", "CB", "CB", "CB", "CB", "CMF", "CMF"]);
    // Slot 19 (deepest) has no abilities; slot 18 is known and releasable.
    let (file, ids) = game_plan_team(
        &positions,
        Spec {
            abilities: rated(19),
            ..Spec::default()
        },
    );

    let candidate = file
        .find_overflow_release_candidate(TEAM, OverflowQuery::default())
        .expect("candidate");
    assert_eq!((candidate.slot, candidate.player_id), (18, ids[18]));
}

// ── compaction and role bytes ────────────────────────────────────────────

// test_game_plan_compaction_keeps_replacement_at_removed_ordinal
#[test]
fn game_plan_compaction_keeps_replacement_at_removed_ordinal() {
    let mut file = single_club(128, 1000, 40);
    let mut head = vec![1, 4, 6, 5, 14, 15, 16, 34, 23, 17, 24];
    head.push(39);
    set_lineup(&mut file, 128, &lineup_bytes(&head));

    file.release_player(1034, 128).expect("release");

    assert_eq!(
        lineup(&file, 128)[..11],
        [1, 4, 6, 5, 14, 15, 16, 34, 23, 17, 24]
    );
}

// test_game_plan_compaction_updates_role_ordinals
#[test]
fn game_plan_compaction_updates_role_ordinals() {
    let mut file = single_club(128, 1000, 40);
    set_game_plan_byte(&mut file, 128, GP_LEFT_CK, 34);
    set_game_plan_byte(&mut file, 128, GP_RIGHT_CK, 39);
    set_game_plan_byte(&mut file, 128, GP_PK, 39);
    set_game_plan_byte(&mut file, 128, GP_CAPTAIN, 34);
    for (i, value) in [34, 39, 7].into_iter().enumerate() {
        set_game_plan_byte(&mut file, 128, GP_ATTACK_PLAYERS + i, value);
    }

    file.release_player(1034, 128).expect("release");

    let byte = |offset| game_plan_byte(&file, 128, offset);
    assert_eq!(byte(GP_LEFT_CK), 0xFF);
    assert_eq!(byte(GP_RIGHT_CK), 34);
    assert_eq!(byte(GP_PK), 34);
    assert_eq!(byte(GP_CAPTAIN), 0xFF);
    assert_eq!(
        [
            byte(GP_ATTACK_PLAYERS),
            byte(GP_ATTACK_PLAYERS + 1),
            byte(GP_ATTACK_PLAYERS + 2)
        ],
        [0xFF, 34, 7]
    );
}

// ── repair: goalkeeper roles and arrays ──────────────────────────────────

// test_repair_game_plan_restores_missing_gk_arrays_and_starter
#[test]
fn repair_game_plan_restores_missing_gk_arrays_and_starter() {
    let roster: Vec<u32> = [108380, 165712, 131304]
        .into_iter()
        .chain(200000..200037)
        .collect();
    let starters = [0u8, 7, 3, 11, 8, 13, 16, 26, 22, 21, 17];
    let mut bench = vec![2u8, 1];
    bench.extend((0..40u8).filter(|slot| !starters.contains(slot) && ![1, 2].contains(slot)));
    let mut file = open(&{
        let mut m = mock(vec![(122, roster, (1..=40).collect())], vec![122]);
        m.players = 40;
        m
    });
    set_catalog_positions(
        &mut file,
        &[
            (108380, pos("LB")),
            (165712, pos("GK")),
            (131304, pos("GK")),
        ],
    );
    set_lineup(&mut file, 122, &[starters.as_slice(), &bench].concat());
    set_position_arrays(&mut file, 122, &[2, 1, 1, 3, 2, 4, 5, 5, 10, 9, 12]);

    let metrics = file.repair_game_plans(&RepairOptions::default());

    let ids = file.roster(122).expect("roster").player_ids;
    let first = lineup(&file, 122)[0];
    assert!([165712, 131304].contains(&ids[usize::from(first)]));
    assert_eq!(metrics.repaired_goalkeeper_roles, 1);
    assert_eq!(metrics.repaired_position_bytes, 9);
    for codes in position_arrays(&file, 122, 11) {
        assert_eq!(codes[0], 0);
        assert_eq!(codes.iter().filter(|&&code| code == 0).count(), 1);
    }
    assert_valid(&file);
}

// test_removal_repairs_goalkeeper_position_bytes_after_slot_compaction
#[test]
fn removal_repairs_goalkeeper_position_bytes_after_slot_compaction() {
    let mut file = single_club(TEAM, 1000, 40);
    attach_positions(&mut file, &[(1039, "GK")]);
    set_catalog_positions(&mut file, &[(1001, pos("CB")), (1039, pos("GK"))]);
    let mut codes = vec![0, 1];
    codes.extend([1; 9]);
    set_position_arrays(&mut file, TEAM, &codes);
    assert_valid(&file);

    file.release_player(1001, TEAM).expect("release");

    assert_eq!(roster_ids(&file)[1], 1039);
    // The vacated CB role is backfilled by the first substitute in place; the
    // copied backup GK keeps its own bench role instead of shifting roles.
    let updated = lineup(&file, TEAM);
    assert_eq!(updated[..2], [0, 11]);
    assert_eq!(updated.iter().position(|&slot| slot == 1), Some(38));
    for at in phase_arrays(&file, TEAM) {
        assert_eq!(file.bytes()[at], 0);
    }
    assert!(
        !error_messages(&file)
            .iter()
            .any(|e| e.contains("assigns GK"))
    );
}

// test_repair_game_plans_preserves_role_position_bytes_when_moving_goalkeeper
#[test]
fn repair_game_plans_preserves_role_position_bytes_when_moving_goalkeeper() {
    let mut file = single_club(TEAM, 1000, 40);
    attach_positions(&mut file, &[(1000, "GK")]);
    let mut head = vec![1u8, 0];
    head.extend(2..40);
    set_lineup(&mut file, TEAM, &head);
    let mut codes = vec![0, 12];
    codes.extend([1; 9]);
    set_position_arrays(&mut file, TEAM, &codes);

    let metrics = file.repair_game_plans(&RepairOptions::default());

    assert_eq!(lineup_prefix(&file, 2), [0, 1]);
    assert_eq!(metrics.repaired_goalkeeper_roles, 1);
    assert_eq!(metrics.repaired_position_bytes, 0);
    for at in phase_arrays(&file, TEAM) {
        assert_eq!(file.bytes()[at], 0);
        assert_eq!(file.bytes()[at + 1], 12);
    }
    assert_valid(&file);
}

// test_repair_game_plans_preserves_cross_line_tactical_positions
#[test]
fn repair_game_plans_preserves_cross_line_tactical_positions() {
    let mut file = single_club(TEAM, 1000, 40);
    attach_positions(&mut file, &[(1000, "GK"), (1001, "LB"), (1002, "RB")]);
    set_lineup(&mut file, TEAM, &(0..40).collect::<Vec<u8>>());
    let mut codes = vec![0, 8, 4];
    codes.extend([1; 8]);
    set_position_arrays(&mut file, TEAM, &codes);

    let metrics = file.repair_game_plans(&RepairOptions::default());

    assert_eq!(metrics.repaired_position_bytes, 0);
    for array in position_arrays(&file, TEAM, 3) {
        assert_eq!(array, [0, 8, 4]);
    }
}

// test_repair_game_plans_preserves_tactical_winger_variants
#[test]
fn repair_game_plans_preserves_tactical_winger_variants() {
    let mut file = single_club(TEAM, 1000, 40);
    attach_positions(&mut file, &[(1000, "GK"), (1007, "LWF"), (1008, "LWF")]);
    set_lineup(&mut file, TEAM, &(0..40).collect::<Vec<u8>>());
    let expected = [0u8, 1, 1, 1, 1, 1, 1, 7, 6, 12, 12];
    set_position_arrays(&mut file, TEAM, &expected);

    let metrics = file.repair_game_plans(&RepairOptions::default());

    assert_eq!(metrics.repaired_position_bytes, 0);
    for array in position_arrays(&file, TEAM, 11) {
        assert_eq!(array, expected);
    }
}

// test_repair_game_plans_moves_extra_goalkeeper_to_bench_without_relabeling_roles
#[test]
fn repair_game_plans_moves_extra_goalkeeper_to_bench_without_relabeling_roles() {
    let mut file = single_club(TEAM, 1000, 40);
    attach_positions(&mut file, &[(1000, "GK"), (1008, "GK"), (1011, "CF")]);
    set_lineup(&mut file, TEAM, &(0..40).collect::<Vec<u8>>());
    set_position_arrays(&mut file, TEAM, &[0, 1, 1, 1, 1, 1, 1, 1, 10, 9, 12]);
    let before = position_arrays(&file, TEAM, 11);

    let metrics = file.repair_game_plans(&RepairOptions::default());

    let updated = lineup(&file, TEAM);
    assert_eq!(updated[..12], [0, 1, 2, 3, 4, 5, 6, 7, 11, 9, 10, 8]);
    assert_eq!(updated[11], 8);
    assert_eq!(metrics.repaired_goalkeeper_roles, 1);
    assert_eq!(position_arrays(&file, TEAM, 11), before);
}

// ── repair: position-aware starters ──────────────────────────────────────

// test_position_aware_gameplan_repair_promotes_exact_roles
#[test]
fn position_aware_gameplan_repair_promotes_exact_roles() {
    let ids = ids_range(1000, 1013);
    let labels = [
        "GK", "CB", "CB", "RB", "LB", "DMF", "CMF", "LB", "RWF", "AMF", "CF", "RWF", "LWF",
    ];
    let mut file = club(ids.clone(), 0);
    let records: Vec<(u32, &str)> = ids.iter().copied().zip(labels).collect();
    attach_positions(&mut file, &records);
    let target = [0u8, 1, 1, 3, 2, 4, 5, 10, 9, 8, 12];
    set_position_arrays(&mut file, TEAM, &target);

    let metrics = file.repair_game_plans(&aligned(false));

    let starters = starter_ids(&file);
    // The RWF moves from the LWF role into the RWF role the LB held; the
    // reserve LWF takes the LWF role.
    assert_eq!(starters[7], 1008);
    assert_eq!(starters[8], 1012);
    assert!(!starters.contains(&1007));
    assert_eq!(metrics.repaired_lineups, 1);
    assert_eq!(metrics.repaired_position_bytes, 0);
    for array in position_arrays(&file, TEAM, 11) {
        assert_eq!(array, target);
    }
}

// test_cb_roles_prefer_matching_strong_foot_by_side
#[test]
fn cb_roles_prefer_matching_strong_foot_by_side() {
    let ids = ids_range(4000, 4012);
    let labels = [
        "GK", "CB", "CB", "CB", "RB", "LB", "DMF", "CMF", "AMF", "LWF", "CF", "RWF",
    ];
    let mut file = club_with_records(ids.clone());
    for (id, left) in ids.iter().zip([false, false, true, false, false, true]) {
        set_left_footed(&mut file, *id, left);
    }
    let records: Vec<(u32, &str)> = ids.iter().copied().zip(labels).collect();
    attach_positions(&mut file, &records);
    set_position_arrays(&mut file, TEAM, &[0, 1, 1, 1, 3, 2, 4, 5, 8, 10, 12]);
    set_lineup(&mut file, TEAM, &(0..11).collect::<Vec<u8>>());

    file.repair_game_plans(&aligned(false));

    assert_eq!(starter_ids(&file)[1..4], [4001, 4003, 4002]);

    set_position_arrays(&mut file, TEAM, &[0, 1, 1, 3, 2, 4, 5, 8, 10, 9, 12]);
    set_lineup(&mut file, TEAM, &[0, 2, 3, 4, 5, 6, 7, 8, 11, 9, 10]);

    file.repair_game_plans(&aligned(false));

    assert_eq!(starter_ids(&file)[1..3], [4003, 4002]);
}

// test_position_aware_repair_rejects_cross_line_and_opposite_wing_players
#[test]
fn position_aware_repair_rejects_cross_line_and_opposite_wing_players() {
    let ids = ids_range(2000, 2013);
    let labels = [
        "GK", "CB", "CB", "RB", "LB", "DMF", "CMF", "AMF", "RWF", "LWF", "CF", "CB", "LB",
    ];
    let mut file = club(ids.clone(), 0);
    let records: Vec<(u32, &str)> = ids.iter().copied().zip(labels).collect();
    attach_positions(&mut file, &records);
    set_position_arrays(&mut file, TEAM, &[0, 1, 1, 3, 2, 4, 5, 10, 9, 8, 12]);
    set_lineup(&mut file, TEAM, &[0, 1, 2, 3, 4, 5, 6, 11, 12, 7, 10, 8, 9]);

    file.repair_game_plans(&aligned(false));

    let repaired: Vec<&str> = starter_ids(&file)
        .into_iter()
        .map(|id| labels[(id - 2000) as usize])
        .collect();
    assert_eq!(repaired[7], "RWF");
    assert_eq!(repaired[8], "LWF");
    assert_eq!(repaired[9], "AMF");
    assert_eq!(repaired[10], "CF");
    assert!(!repaired[7..11].contains(&"CB"));
    assert!(!repaired[7..11].contains(&"LB"));
}

// test_position_aware_repair_moves_goalkeeper_out_of_cf_role
#[test]
fn position_aware_repair_moves_goalkeeper_out_of_cf_role() {
    let ids = ids_range(3000, 3012);
    let labels = [
        "CB", "CB", "CB", "RB", "LB", "DMF", "CMF", "AMF", "RWF", "LWF", "CF", "GK",
    ];
    let mut file = club(ids.clone(), 0);
    let records: Vec<(u32, &str)> = ids.iter().copied().zip(labels).collect();
    attach_positions(&mut file, &records);
    set_position_arrays(&mut file, TEAM, &[0, 1, 1, 3, 2, 4, 5, 10, 9, 8, 12]);
    set_lineup(&mut file, TEAM, &[0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 10]);

    file.repair_game_plans(&aligned(true));

    let starters: Vec<&str> = starter_ids(&file)
        .into_iter()
        .map(|id| labels[(id - 3000) as usize])
        .collect();
    assert_eq!(starters[0], "GK");
    assert!(!starters[1..].contains(&"GK"));
    assert_eq!(starters[10], "CF");
}

// ── removal / promotion ──────────────────────────────────────────────────

// test_removal_promotes_player_matching_vacated_role_position
#[test]
fn removal_promotes_player_matching_vacated_role_position() {
    let mut file = single_club(TEAM, 1000, 40);
    attach_positions(&mut file, &[(1001, "CF"), (1038, "CF"), (1039, "RB")]);
    let mut codes = vec![0, 12];
    codes.extend([1; 9]);
    set_position_arrays(&mut file, TEAM, &codes);

    file.release_player(1001, TEAM).expect("release");

    let ids = roster_ids(&file);
    let updated = lineup(&file, TEAM);
    assert_eq!(ids[1], 1039);
    assert_eq!(ids[38], 1038);
    assert_eq!(updated[1], 38);
    assert_eq!(updated.iter().position(|&slot| slot == 1), Some(38));
    for at in phase_arrays(&file, TEAM) {
        assert_eq!(file.bytes()[at + 1], 12);
    }
}

// test_promotion_uses_native_positions_over_stale_save_labels
//
// The promotion-slot choice is driven through `release_player`: the bench
// holds a native DMF (catalog says AMF) and a native CB (catalog says AMF).
#[test]
fn promotion_uses_native_positions_over_stale_save_labels() {
    let roster: Vec<u32> = (1000..1011).chain([143196, 148009]).collect();
    let setup = |codes: &[u8]| {
        let mut file = club(roster.clone(), 0);
        set_catalog_positions(&mut file, &[(143196, pos("AMF")), (148009, pos("AMF"))]);
        attach_positions(&mut file, &[(143196, "DMF"), (148009, "CB")]);
        set_position_arrays(&mut file, TEAM, codes);
        file
    };

    // Vacated CB role: the native CB (not the native DMF) takes it.
    let mut file = setup(&[0, 1, 1, 3, 2, 4, 5, 10, 9, 8, 12]);
    file.release_player(1001, TEAM).expect("release");
    assert_eq!(starter_ids(&file)[1], 148009);

    // Vacated DMF role: the native DMF takes it.
    let mut file = setup(&[0, 1, 1, 3, 2, 4, 5, 10, 9, 8, 12]);
    file.release_player(1005, TEAM).expect("release");
    assert_eq!(starter_ids(&file)[5], 143196);
}

// test_added_player_preserves_tactical_position_for_changed_game_plan_role
#[test]
fn added_player_preserves_tactical_position_for_changed_game_plan_role() {
    // (player id, native position, supplied position, expected role)
    let cases = [
        (151751, "GK", "AMF", 0),
        (138156, "AMF", "DMF", 10),
        (143196, "DMF", "AMF", 10),
        (117087, "RWF", "AMF", 10),
        (162196, "RB", "CF", 10),
        (148009, "CB", "ST", 10),
    ];
    for (player_id, native, supplied, expected_role) in cases {
        let mut file = club(ids_range(1000, 1010), 0);
        attach_positions(&mut file, &[(player_id, native)]);
        let tactical = Position::Cf.code();
        for at in phase_arrays(&file, TEAM) {
            file.data_mut()[at + 10] = tactical;
        }

        add(&mut file, player_id, supplied);

        assert_eq!(
            lineup_prefix(&file, 11).iter().position(|&slot| slot == 10),
            Some(expected_role),
            "{player_id} ({native}, supplied {supplied})"
        );
        let expected_code = if expected_role == 0 {
            Position::Gk.code()
        } else {
            tactical
        };
        for at in phase_arrays(&file, TEAM) {
            assert_eq!(
                file.bytes()[at + expected_role],
                expected_code,
                "{player_id} ({native}, supplied {supplied})"
            );
        }
    }
}

// test_native_playerbin_position_overrides_stale_save_position
//
// `get_all_players` is `build_catalog`, `get_player_position` is
// `player_position`; the mutation-side `_mutation_player_position` is covered
// by the added-player test above (native AMF beats the supplied DMF).
#[test]
fn native_playerbin_position_overrides_stale_save_position() {
    let mut m = mock(vec![(TEAM, vec![138156], vec![10])], vec![TEAM]);
    m.players = 1;
    m.player_entries = vec![(138156, "Cole Palmer".into())];
    let mut file = open(&m);
    let at = file.player_entry_offset(138156).expect("player record") + PE_REGISTERED_POSITION_BYTE;
    file.data_mut()[at..at + 2]
        .copy_from_slice(&(u16::from(Position::Gk.code()) << 5).to_le_bytes());
    attach_positions(&mut file, &[(138156, "AMF")]);

    let catalog = file.build_catalog();

    assert_eq!(catalog.players[&138156].position, Some(Position::Amf));
    assert_eq!(file.player_position(138156), Some(Position::Amf));
}

// test_removal_preserves_copied_player_tactical_position
#[test]
fn removal_preserves_copied_player_tactical_position() {
    let roster: Vec<u32> = (1000..1010).chain([162196]).collect();
    let mut file = club(roster, 0);
    attach_positions(&mut file, &[(1001, "CB"), (162196, "RB")]);
    for at in phase_arrays(&file, TEAM) {
        file.data_mut()[at + 1] = Position::Cb.code();
        file.data_mut()[at + 0x0B + 2] = 0x12;
        file.data_mut()[at + 0x0B + 3] = 0x34;
    }
    let coordinates = |file: &EditFile| -> Vec<Vec<u8>> {
        phase_arrays(file, TEAM)
            .into_iter()
            .map(|at| file.bytes()[at + 0x0B + 2..at + 0x0B + 4].to_vec())
            .collect()
    };
    let before = coordinates(&file);

    file.release_player(1001, TEAM).expect("release");

    assert_eq!(roster_ids(&file)[1], 162196);
    assert_eq!(lineup(&file, TEAM)[1], 1);
    assert_eq!(coordinates(&file), before);
    for at in phase_arrays(&file, TEAM) {
        assert_eq!(file.bytes()[at + 1], Position::Cb.code());
    }
}

// test_move_preserves_copied_player_tactical_position_after_source_compaction
#[test]
fn move_preserves_copied_player_tactical_position_after_source_compaction() {
    let source: Vec<u32> = (1000..1011).chain([162196]).collect();
    let mut destination = vec![2000];
    destination.resize(40, 0);
    let mut destination_shirts = vec![20u16];
    destination_shirts.resize(40, 0);
    let mut m = mock(
        vec![
            (101, source, (1..=12).collect()),
            (102, destination, destination_shirts),
        ],
        vec![101, 102],
    );
    m.players = 0;
    let mut file = open(&m);
    attach_positions(&mut file, &[(1001, "CB"), (162196, "RB"), (2000, "CF")]);
    for at in phase_arrays(&file, 101) {
        file.data_mut()[at + 1] = Position::Cb.code();
        file.data_mut()[at + 0x0B + 2] = 0x12;
        file.data_mut()[at + 0x0B + 3] = 0x34;
    }

    move_to(&mut file, 1001, 101, 102, "ST");

    assert_eq!(file.roster(101).expect("roster").player_ids[1], 162196);
    assert_eq!(lineup(&file, 101)[1], 1);
    for at in phase_arrays(&file, 101) {
        assert_eq!(file.bytes()[at + 1], Position::Cb.code());
        assert_eq!(file.bytes()[at + 0x0B + 2..at + 0x0B + 4], [0x12, 0x34]);
    }
}

// test_reserve_compaction_keeps_copied_starter_role
#[test]
fn reserve_compaction_keeps_copied_starter_role() {
    let mut file = club(ids_range(1000, 1016), 0);
    let mut head: Vec<u8> = vec![0, 1, 2, 3, 4, 5, 6, 7, 8, 15, 10, 11, 12, 13, 9, 14];
    head.extend(16..40);
    set_lineup(&mut file, TEAM, &head);

    file.release_player(1014, TEAM).expect("release");

    let updated = lineup(&file, TEAM);
    assert_eq!(
        updated[..15],
        [0, 1, 2, 3, 4, 5, 6, 7, 8, 14, 10, 11, 12, 13, 9]
    );
    let mut tail: Vec<u8> = (16..40).collect();
    tail.push(15);
    assert_eq!(updated[15..], tail[..]);
}

// test_removal_preserves_promoted_player_tactical_position
#[test]
fn removal_preserves_promoted_player_tactical_position() {
    let mut file = club(ids_range(1000, 1013), 0);
    attach_positions(&mut file, &[(1001, "CB"), (1011, "RB"), (1012, "LWF")]);
    for at in phase_arrays(&file, TEAM) {
        file.data_mut()[at + 1] = Position::Cb.code();
    }

    file.release_player(1001, TEAM).expect("release");

    let ids = roster_ids(&file);
    assert_eq!(ids[1], 1012);
    assert_eq!(ids[11], 1011);
    assert_eq!(lineup_prefix(&file, 2), [0, 11]);
    for at in phase_arrays(&file, TEAM) {
        assert_eq!(file.bytes()[at + 1], Position::Cb.code());
    }
}

// ── goalkeeper handling ──────────────────────────────────────────────────

// test_goalkeeper_removal_uses_first_reserve_without_metadata
#[test]
fn goalkeeper_removal_uses_first_reserve_without_metadata() {
    let mut file = club(ids_range(1000, 1013), 0);

    file.release_player(1000, TEAM).expect("release");

    let ids = roster_ids(&file);
    assert_eq!(ids[0], 1012);
    assert_eq!(ids[11], 1011);
    let updated = lineup(&file, TEAM);
    assert_eq!(updated[..2], [11, 1]);
    assert_eq!(updated.iter().position(|&slot| slot == 0), Some(11));
}

// test_unknown_role_zero_keeps_incumbent_over_reserve_goalkeepers
//
// The reserve goalkeepers are written into the save and the public repair
// runs.
#[test]
fn unknown_role_zero_keeps_incumbent_over_reserve_goalkeepers() {
    let mut file = club(ids_range(1000, 1013), 0);
    attach_positions(&mut file, &[(151751, "GK"), (101076, "GK")]);
    // 1000 has an empty catalog position: unknown.
    set_roster_slot(&mut file, TEAM, 11, 151751);
    set_roster_slot(&mut file, TEAM, 12, 101076);

    file.repair_game_plans(&RepairOptions::default());

    assert_eq!(lineup_prefix(&file, 2), [0, 1]);
}

// test_gameplan_uses_primary_native_goalkeeper_and_keeps_reserve
#[test]
fn gameplan_uses_primary_native_goalkeeper_and_keeps_reserve() {
    let roster: Vec<u32> = [101076, 151751].into_iter().chain(2000..2038).collect();
    let mut file = club(roster, 0);
    let mut native = FakeNative::default();
    let mut primary = FakeRecord::new(Position::Gk);
    primary.age = 33;
    primary.caps = 37;
    native.records.insert(101076, primary);
    native.records.insert(151751, FakeRecord::new(Position::Gk));
    native.attach(&mut file);
    let mut head = vec![1u8];
    head.extend(2..12);
    head.push(0);
    head.extend(12..40);
    set_lineup(&mut file, TEAM, &head);

    let metrics = file.repair_game_plans(&RepairOptions::default());

    let updated = lineup(&file, TEAM);
    assert_eq!(updated[0], 0);
    assert_eq!(updated[11], 1);
    assert_eq!(metrics.repaired_goalkeeper_roles, 1);
}

// test_gameplan_uses_club_roster_order_not_international_caps
#[test]
fn gameplan_uses_club_roster_order_not_international_caps() {
    let roster: Vec<u32> = [101520, 47242].into_iter().chain(2000..2038).collect();
    let mut file = club(roster, 0);
    let mut native = FakeNative::default();
    for (id, age, caps) in [(101520, 30, 8), (47242, 31, 15)] {
        let mut record = FakeRecord::new(Position::Gk);
        record.age = age;
        record.caps = caps;
        native.records.insert(id, record);
    }
    native.attach(&mut file);
    let mut head = vec![0u8];
    head.extend(2..12);
    head.push(1);
    head.extend(12..40);
    set_lineup(&mut file, TEAM, &head);

    let metrics = file.repair_game_plans(&RepairOptions::default());

    let updated = lineup(&file, TEAM);
    assert_eq!(updated[0], 0);
    assert_eq!(updated[11], 1);
    assert_eq!(metrics.repaired_goalkeeper_roles, 0);
}

// test_gameplan_moves_every_extra_goalkeeper_out_of_starters
#[test]
fn gameplan_moves_every_extra_goalkeeper_out_of_starters() {
    let roster: Vec<u32> = [101076, 151751].into_iter().chain(2000..2038).collect();
    let mut file = club(roster, 0);
    attach_positions(&mut file, &[(101076, "GK"), (151751, "GK"), (2011, "CB")]);

    let metrics = file.repair_game_plans(&RepairOptions::default());

    let updated = lineup(&file, TEAM);
    assert_eq!(updated[0], 0);
    assert_eq!(updated[1], 11);
    assert_eq!(updated[11], 1);
    assert_eq!(metrics.repaired_goalkeeper_roles, 1);
}

// test_gameplan_does_not_promote_native_outfield_player_to_goalkeeper
#[test]
fn gameplan_does_not_promote_native_outfield_player_to_goalkeeper() {
    let roster: Vec<u32> = [138156, 151751].into_iter().chain(2000..2038).collect();
    let mut file = club(roster, 0);
    attach_positions(&mut file, &[(138156, "AMF"), (151751, "GK")]);
    let mut head = vec![0u8];
    head.extend(2..12);
    head.push(1);
    head.extend(12..40);
    set_lineup(&mut file, TEAM, &head);

    file.repair_game_plans(&RepairOptions::default());

    let updated = lineup(&file, TEAM);
    assert_eq!(updated[0], 1);
    assert_eq!(updated[11], 0);
}

// test_added_goalkeeper_stays_on_bench_when_primary_exists
#[test]
fn added_goalkeeper_stays_on_bench_when_primary_exists() {
    let roster: Vec<u32> = [101076].into_iter().chain(2000..2010).collect();
    let mut file = club(roster, 0);
    let mut native = FakeNative::default();
    let mut primary = FakeRecord::new(Position::Gk);
    primary.age = 33;
    primary.caps = 37;
    native.records.insert(101076, primary);
    native.records.insert(151751, FakeRecord::new(Position::Gk));
    native.attach(&mut file);

    add(&mut file, 151751, "GK");

    let updated = lineup(&file, TEAM);
    assert_eq!(updated[0], 0);
    assert_eq!(updated[11], 11);
}

// test_goalkeeper_addition_uses_transfer_position_for_game_plan_role
#[test]
fn goalkeeper_addition_uses_transfer_position_for_game_plan_role() {
    let mut file = club(ids_range(1000, 1010), 0);

    add(&mut file, 2000, "GK");

    let updated = lineup(&file, TEAM);
    assert_eq!(updated[..2], [10, 1]);
    assert_eq!(updated[10], 0);
}

// ── role filling ─────────────────────────────────────────────────────────

// test_repair_fills_empty_captain_set_pieces_and_attackers_from_save_data
#[test]
fn repair_fills_empty_captain_set_pieces_and_attackers_from_save_data() {
    let (mut file, ids) = game_plan_team(
        &squad(&["GK", "CB", "CMF", "CF", "RB"]),
        Spec {
            caps: hm(&[(5004, 80), (5002, 80), (5008, 10)]),
            ages: hm(&[(5004, 31), (5002, 28)]),
            values: hm(&[(5008, 90_000_000), (5009, 60_000_000), (5014, 99_000_000)]),
            save_records: true,
            ..Spec::default()
        },
    );
    assert!(file.set_team_formation(TEAM, "4-3-3").expect("formation") > 0);
    // The save record's strong-foot bit is the preferred-foot source
    // (unset = right foot).
    set_left_footed(&mut file, 5007, true);
    for role in [GP_CAPTAIN, GP_LEFT_CK, GP_RIGHT_CK, GP_PK] {
        set_game_plan_byte(&mut file, TEAM, role, EMPTY_ROLE);
    }
    for i in 0..3 {
        set_game_plan_byte(&mut file, TEAM, GP_ATTACK_PLAYERS + i, EMPTY_ROLE);
    }

    let warnings: Vec<String> = file
        .validate_integrity()
        .warnings
        .iter()
        .map(ToString::to_string)
        .collect();
    assert!(
        warnings
            .iter()
            .any(|w| w.contains("empty roles") && w.contains("captain")),
        "{warnings:?}"
    );

    let metrics = file.repair_game_plans(&RepairOptions::default());

    let slot_of = |id: u32| ids.iter().position(|&i| i == id).expect("id") as u8;
    let byte = |offset| game_plan_byte(&file, TEAM, offset);
    assert_eq!(metrics.filled_roles, 7);
    // Captain: most caps (5004 and 5002 tie), then the older player.
    assert_eq!(file.team_captain_player(TEAM), Some(5004));
    // Left corner: the left-footed CMF beats the right-footed LWF.
    assert_eq!(byte(GP_LEFT_CK), slot_of(5007));
    // Right corner: right-footed right winger.
    assert_eq!(byte(GP_RIGHT_CK), slot_of(5009));
    // Penalty: the starting CF, never the more valuable benched striker.
    assert_eq!(byte(GP_PK), slot_of(5008));
    // Attack players: CF row first, then the wingers.
    assert_eq!(
        [
            byte(GP_ATTACK_PLAYERS),
            byte(GP_ATTACK_PLAYERS + 1),
            byte(GP_ATTACK_PLAYERS + 2)
        ],
        [8, 9, 10]
    );
    assert!(
        !file
            .validate_integrity()
            .warnings
            .iter()
            .any(|w| w.to_string().contains("empty roles"))
    );
    assert!(assign_missing_game_plan_roles(&mut file, TEAM).is_empty());
}

// test_captain_stays_empty_without_any_metadata
#[test]
fn captain_stays_empty_without_any_metadata() {
    let mut file = club(ids_range(1000, 1016), 0);
    set_game_plan_byte(&mut file, TEAM, GP_CAPTAIN, EMPTY_ROLE);

    let filled = assign_missing_game_plan_roles(&mut file, TEAM);

    assert!(!filled.iter().any(|role| role.name() == "captain"));
    assert_eq!(game_plan_byte(&file, TEAM, GP_CAPTAIN), EMPTY_ROLE);
}

// ── bench and ratings ────────────────────────────────────────────────────

#[test]
fn signing_leads_bench_and_reserve_keeper_joins_matchday_squad() {
    let positions = squad(&["CB", "CB", "CB", "CB", "CB", "CB", "CB", "CB", "GK"]);
    let (mut file, ids) = game_plan_team(&positions, Spec::default());

    add(&mut file, 9999, "CMF");

    let signing_slot = roster_ids(&file)
        .iter()
        .position(|&id| id == 9999)
        .expect("signing") as u8;
    let updated = lineup(&file, TEAM);
    assert_eq!(updated[..11], (0..11).collect::<Vec<u8>>()[..]);
    assert_eq!(updated[11], signing_slot);
    let matchday = team_matchday(&file, TEAM).expect("matchday");
    assert_eq!(matchday.starters, ids[..11]);
    assert_eq!(matchday.bench[0], 9999);
    assert_eq!(matchday.bench.len(), 7);

    let metrics = file.repair_game_plans(&RepairOptions::default());

    let updated = lineup(&file, TEAM);
    assert_eq!(metrics.reordered_benches, 1);
    assert_eq!(updated[..11], (0..11).collect::<Vec<u8>>()[..]);
    assert_eq!(updated[11], signing_slot);
    // The only reserve goalkeeper (roster slot 19) is pulled into role 17.
    assert_eq!(updated[17], 19);
    assert_valid(&file);
}

// test_removing_last_slot_starter_backfills_role_without_shift
#[test]
fn removing_last_slot_starter_backfills_role_without_shift() {
    let positions = squad(&["GK", "CB", "CMF", "CB", "CF", "RB", "DMF"]);
    let (mut file, ids) = game_plan_team(&positions, Spec::default());
    // The DMF in roster slot 17 holds starter role 5 (code 1 in the mock).
    let mut head: Vec<u8> = vec![0, 1, 2, 3, 4, 17, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 5];
    head.extend(18..40);
    set_lineup(&mut file, TEAM, &head);

    file.release_player(ids[17], TEAM).expect("release");

    let updated = lineup(&file, TEAM);
    // Two unrated bench CBs exist (slots 12, 14); the first in bench order
    // takes the departed role index; no other starter role moves.
    assert_eq!(updated[..11], [0, 1, 2, 3, 4, 12, 6, 7, 8, 9, 10]);
    assert_eq!(updated[11..17], [11, 13, 14, 15, 16, 5]);
    assert_valid(&file);
}

// test_better_rated_reserve_replaces_weak_same_role_incumbent
#[test]
fn better_rated_reserve_replaces_weak_same_role_incumbent() {
    // Slots 6/7 are the CMF starters; slot 11 is a reserve CMF.
    let mut abilities = rated(12);
    abilities.extend([(5006, 60), (5007, 70), (5011, 85)]);
    let (mut file, ids) = game_plan_team(
        &squad(&["CMF"]),
        Spec {
            abilities,
            ..Spec::default()
        },
    );
    file.set_team_formation(TEAM, "4-3-3").expect("formation");

    file.repair_game_plans(&aligned(true));

    let starters = starter_ids(&file);
    assert!(starters.contains(&ids[11]));
    assert!(!starters.contains(&ids[6]));
    assert!(starters.contains(&ids[7]));
}

// test_lineup_ignores_ratings_when_a_player_is_unrated
#[test]
fn lineup_ignores_ratings_when_a_player_is_unrated() {
    let mut abilities = rated(12);
    abilities.extend([(5006, 60), (5011, 85)]);
    abilities.remove(&5005); // one unrated starter disables rating-based ranking
    let (mut file, ids) = game_plan_team(
        &squad(&["CMF"]),
        Spec {
            abilities,
            ..Spec::default()
        },
    );
    file.set_team_formation(TEAM, "4-3-3").expect("formation");

    file.repair_game_plans(&aligned(true));

    let starters = starter_ids(&file);
    assert!(starters.contains(&ids[5]));
    assert!(starters.contains(&ids[6]));
    assert!(!starters.contains(&ids[11]));
}

#[test]
fn small_rating_edge_beats_incumbent_starter() {
    // 70 vs 72 abilities: the incumbent gets no bonus, the better player starts.
    let mut abilities = rated(12);
    abilities.extend([(5006, 70), (5007, 85), (5011, 72)]);
    let (mut file, ids) = game_plan_team(
        &squad(&["CMF"]),
        Spec {
            abilities,
            ..Spec::default()
        },
    );
    file.set_team_formation(TEAM, "4-3-3").expect("formation");

    file.repair_game_plans(&aligned(true));

    let starters = starter_ids(&file);
    assert!(starters.contains(&ids[11]));
    assert!(!starters.contains(&ids[6]));
}

// test_best_rated_goalkeeper_takes_role_zero
#[test]
fn best_rated_goalkeeper_takes_role_zero() {
    let mut abilities = rated(12);
    abilities.extend([(5000, 60), (5011, 85)]);
    let (mut file, ids) = game_plan_team(
        &squad(&["GK"]),
        Spec {
            abilities,
            ..Spec::default()
        },
    );
    file.set_team_formation(TEAM, "4-3-3").expect("formation");

    file.repair_game_plans(&aligned(true));

    let starters = starter_ids(&file);
    assert_eq!(starters[0], ids[11]);
    assert!(!starters.contains(&ids[0]));
}

// test_rated_winger_needs_playable_grade_for_wide_midfield_role (parametrized)
#[test]
fn rated_winger_needs_playable_grade_for_wide_midfield_role() {
    for (lmf_grade, starts) in [(2u8, true), (0, false)] {
        let four_four_two = [
            "GK", "CB", "CB", "RB", "LB", "CMF", "CMF", "RMF", "LMF", "CF", "CF",
        ];
        let positions: Vec<&str> = four_four_two.into_iter().chain(["LWF"]).collect();
        let mut abilities = rated(11);
        abilities.insert(5008, 60);
        let (mut file, ids, mut native) = game_plan_team_records(
            &positions,
            Spec {
                abilities,
                ..Spec::default()
            },
        );
        let winger = ids[11];
        native.records.get_mut(&winger).expect("winger").abilities = Some(uniform_abilities(
            Position::Lwf,
            85,
            &[(Position::Lmf, lmf_grade)],
        ));
        native.attach(&mut file);
        file.set_team_formation(TEAM, "4-4-2").expect("formation");

        file.repair_game_plans(&aligned(true));

        let starters = starter_ids(&file);
        // LWF -> LMF crosses lines; only a native B/A grade at LMF allows it.
        assert_eq!(starters.contains(&winger), starts, "LMF grade {lmf_grade}");
        assert_eq!(starters[8] == winger, starts, "LMF grade {lmf_grade}");
    }
}

// test_bench_orders_remaining_reserves_by_overall
#[test]
fn bench_orders_remaining_reserves_by_overall() {
    let positions = squad(&["GK", "CB", "CMF", "CF"]);
    let (mut file, ids) = game_plan_team(
        &positions,
        Spec {
            abilities: hm(&[(5011, 60), (5012, 55), (5013, 80), (5014, 70)]),
            ..Spec::default()
        },
    );
    file.repair_game_plans(&aligned(false));

    let roster = roster_ids(&file);
    let bench: Vec<u32> = lineup(&file, TEAM)[11..15]
        .iter()
        .map(|&slot| roster[usize::from(slot)])
        .collect();
    // Highest overall first; the GK is already in the 18.
    assert_eq!(bench, [ids[13], ids[14], ids[11], ids[12]]);
}
