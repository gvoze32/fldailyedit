//! Regression: a club must never lose its last goalkeeper.
//!
//! The second in-place pass of the full e2e scenario released both of
//! Al-Gharafa's goalkeepers (the live squad lists keepers the save cannot
//! match), compaction promoted a centre-forward into role zero and the
//! game-plan position arrays kept goalkeeper code 0 for him; integrity
//! validation then rolled the whole run back. The fixture
//! `testdata/roster/al_gharafa_pass1.json` is the club's roster and game plan
//! as the first pass saved them (extracted from the pass-1 output), with the
//! registered position of every player.

use std::path::Path;

use serde_json::Value;

use crate::edit_file::{EditFile, testing::Mock};
use crate::layout::*;
use crate::model::Position;
use crate::roster::{AddRequest, MoveRequest, RepairOptions, RosterErrorKind};

use super::support::*;

struct Fixture {
    file: EditFile,
    doc: Value,
}

fn from_hex(text: &str) -> Vec<u8> {
    (0..text.len() / 2)
        .map(|i| u8::from_str_radix(&text[2 * i..2 * i + 2], 16).expect("hex"))
        .collect()
}

fn ids_of(value: &Value) -> Vec<u32> {
    value
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_u64().unwrap() as u32)
        .collect()
}

/// Team 67024 exactly as the first full pass saved it.
fn al_gharafa(with_abilities: bool) -> Fixture {
    let path =
        Path::new(env!("CARGO_MANIFEST_DIR")).join("../../testdata/roster/al_gharafa_pass1.json");
    let doc: Value = serde_json::from_slice(&std::fs::read(path).unwrap()).unwrap();
    let team = doc["team_id"].as_u64().unwrap() as u32;
    let ids = ids_of(&doc["roster_ids"]);
    let shirts: Vec<u16> = ids_of(&doc["shirt_numbers"])
        .into_iter()
        .map(|n| n as u16)
        .collect();
    let mut data = Mock::club(team, ids, shirts).build();
    let plan = from_hex(doc["game_plan_hex"].as_str().unwrap());
    assert_eq!(plan.len(), GAME_PLAN_ENTRY_SIZE);
    data[GAME_PLAN_START..GAME_PLAN_START + GAME_PLAN_ENTRY_SIZE].copy_from_slice(&plan);
    let mut file = EditFile::from_bytes(data).unwrap();
    let mut native = FakeNative::default();
    for (id, label) in doc["positions"].as_object().unwrap() {
        let position = Position::from_label(label.as_str().unwrap()).unwrap();
        let mut record = FakeRecord::new(position);
        if with_abilities {
            record.abilities = Some(uniform_abilities(position, 60, &[]));
        }
        native.records.insert(id.parse().unwrap(), record);
    }
    native.attach(&mut file);
    Fixture { file, doc }
}

impl Fixture {
    fn team(&self) -> u32 {
        self.doc["team_id"].as_u64().unwrap() as u32
    }

    fn position(&self, player: u32) -> Position {
        self.file.player_position(player).unwrap()
    }

    fn repair_options(&self) -> RepairOptions {
        let repair = &self.doc["repair"];
        let team = self.team();
        RepairOptions {
            preserve_existing_primary: true,
            preferred_starters: Some([(team, ids_of(&repair["preferred_starters"]))].into()),
            preferred_bench: [(team, ids_of(&repair["preferred_bench"]))].into(),
            position_overrides: [(
                team,
                repair["position_overrides"]
                    .as_object()
                    .unwrap()
                    .iter()
                    .map(|(id, label)| {
                        (
                            id.parse().unwrap(),
                            Position::from_label(label.as_str().unwrap()).unwrap(),
                        )
                    })
                    .collect(),
            )]
            .into(),
            align_positions: true,
        }
    }

    /// Integrity errors that concern goalkeeper position codes.
    fn goalkeeper_errors(&self) -> Vec<String> {
        self.file
            .validate_integrity()
            .error_messages()
            .into_iter()
            .filter(|message| message.contains("goalkeeper"))
            .collect()
    }

    /// The registered position of the player in each starting role.
    fn starters(&self) -> Vec<Position> {
        let roster = self.file.roster(self.team()).unwrap();
        lineup(&self.file, self.team())[..FIRST_TEAM_SLOT_COUNT]
            .iter()
            .map(|&slot| self.position(roster.player_ids[usize::from(slot)]))
            .collect()
    }
}

fn assert_one_goalkeeper_code_per_phase(fixture: &Fixture) {
    let plan = fixture.file.game_plan(fixture.team()).unwrap();
    for preset in GP_POSITION_PRESETS {
        for phase in GP_POSITION_PHASE_OFFSETS {
            let codes = &plan[preset + phase..preset + phase + FIRST_TEAM_SLOT_COUNT];
            assert_eq!(
                codes.iter().filter(|&&code| code == 0).count(),
                1,
                "preset {preset:#x} phase {phase:#x}: {codes:?}"
            );
            assert_eq!(codes[0], 0, "the goalkeeper code belongs to role zero");
        }
    }
}

#[test]
fn the_fixture_is_valid_before_any_release() {
    for with_abilities in [false, true] {
        let fixture = al_gharafa(with_abilities);
        assert_eq!(fixture.goalkeeper_errors(), Vec::<String>::new());
        assert_eq!(fixture.starters()[0], Position::Gk);
        assert_one_goalkeeper_code_per_phase(&fixture);
    }
}

/// The exact second-pass release sequence: the last keeper's release is
/// refused, everything else applies, and the game plan stays valid.
#[test]
fn live_squad_releases_never_remove_the_last_goalkeeper() {
    for with_abilities in [false, true] {
        let mut fixture = al_gharafa(with_abilities);
        let team = fixture.team();
        let preferred = fixture.doc["preferred_starters"].clone();
        fixture
            .file
            .set_game_plan_preferred_starters([(team, ids_of(&preferred))].into());

        let mut refused = Vec::new();
        for player in ids_of(&fixture.doc["releases"]) {
            let before = fixture.file.bytes().to_vec();
            match fixture.file.release_player(player, team) {
                Ok(()) => {}
                Err(error) => {
                    assert_eq!(error.kind(), RosterErrorKind::LastGoalkeeper);
                    assert_eq!(fixture.file.bytes(), before, "a refusal writes nothing");
                    refused.push(player);
                }
            }
        }
        assert_eq!(
            refused,
            [171_369],
            "only the second keeper's release is refused"
        );

        let options = fixture.repair_options();
        fixture.file.repair_game_plans(&options);

        let roster = fixture.file.roster(team).unwrap();
        assert!(roster.has_player(171_369), "the last goalkeeper stays");
        assert!(!roster.has_player(133_193), "the first keeper was released");
        assert_eq!(fixture.starters()[0], Position::Gk);
        assert_eq!(fixture.goalkeeper_errors(), Vec::<String>::new());
        assert_one_goalkeeper_code_per_phase(&fixture);
    }
}

/// With a second keeper on the bench the released role-zero keeper is
/// replaced by him, not by an outfield player.
#[test]
fn releasing_the_starting_goalkeeper_promotes_the_other_one() {
    for with_abilities in [false, true] {
        let mut fixture = al_gharafa(with_abilities);
        let team = fixture.team();
        fixture.file.release_player(133_193, team).unwrap();

        assert_eq!(fixture.starters()[0], Position::Gk);
        let roster = fixture.file.roster(team).unwrap();
        let role0 = roster.player_ids[usize::from(lineup(&fixture.file, team)[0])];
        assert_eq!(role0, 171_369);
        assert_eq!(fixture.goalkeeper_errors(), Vec::<String>::new());
        assert_one_goalkeeper_code_per_phase(&fixture);

        let options = fixture.repair_options();
        fixture.file.repair_game_plans(&options);
        assert_eq!(fixture.goalkeeper_errors(), Vec::<String>::new());
        assert_one_goalkeeper_code_per_phase(&fixture);
    }
}

fn two_clubs_with_one_keeper() -> EditFile {
    let mut file = open(&mock(
        vec![
            (101, (1000..1020).collect(), (1..=20).collect()),
            (102, (2000..2020).collect(), (1..=20).collect()),
        ],
        vec![101, 102],
    ));
    let mut native = FakeNative::default();
    for id in (1000..1020).chain(2000..2020).chain([5000]) {
        let position = if matches!(id, 1000 | 2000 | 5000) {
            Position::Gk
        } else {
            Position::Cb
        };
        native.records.insert(id, FakeRecord::new(position));
    }
    native.attach(&mut file);
    file
}

#[test]
fn moving_the_last_goalkeeper_away_is_refused_unless_a_replacement_arrived() {
    let mut file = two_clubs_with_one_keeper();
    let before = file.bytes().to_vec();
    let error = file
        .move_player(&MoveRequest::new(1000, 101, 102))
        .unwrap_err();
    assert_eq!(error.kind(), RosterErrorKind::LastGoalkeeper);
    assert_eq!(error.code(), "last_goalkeeper");
    assert_eq!(file.bytes(), before);
    assert_eq!(
        file.release_player(1000, 101).unwrap_err().kind(),
        RosterErrorKind::LastGoalkeeper
    );
    assert_eq!(file.bytes(), before);

    // A replacement signed earlier in the batch lifts the refusal.
    file.add_player(&AddRequest::new(5000, 101)).unwrap();
    file.move_player(&MoveRequest::new(1000, 101, 102)).unwrap();
    assert!(file.roster(102).unwrap().has_player(1000));
    // Outfield players are never affected.
    file.release_player(1001, 101).unwrap();
}

/// A player without a known position may be the real keeper (the first full
/// pass leaves Olympiakos with one after its registered goalkeeper moved):
/// integrity validation does not judge him, so the removal is not refused.
#[test]
fn an_unknown_position_stand_in_lets_the_registered_goalkeeper_go() {
    let mut file = open(&mock(
        vec![(101, (1000..1020).collect(), (1..=20).collect())],
        vec![101],
    ));
    let mut native = FakeNative::default();
    for id in 1000..1019 {
        let position = if id == 1000 {
            Position::Gk
        } else {
            Position::Cb
        };
        native.records.insert(id, FakeRecord::new(position));
    }
    // Player 1019 has no Player.bin record: unknown position.
    native.attach(&mut file);
    file.release_player(1000, 101).unwrap();
    assert!(!file.roster(101).unwrap().has_player(1000));
}
