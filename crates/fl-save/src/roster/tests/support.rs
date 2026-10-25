//! Shared fixtures for the roster tests: mock saves, catalog and Player.bin
//! fakes.

use std::collections::HashMap;
use std::sync::Arc;

use crate::catalog::PlayerCatalog;
use crate::edit_file::{EditFile, testing::Mock};
use crate::layout::*;
use crate::model::{NativeCareer, NativePlayer, NativePlayers, PlayerInfo, Position};
use crate::rating::{ABILITY_COUNT, GOALKEEPING_COUNT, PlayerAbilities};
use crate::roster::formation::{Formation, FormationLayout};
use crate::roster::game_plan::GamePlanRole;
use crate::roster::roles::assign_missing_roles;
use crate::roster::squad::Squad;

/// Role codes and coordinates for a formation text, or `None` when the shape
/// is unsupported.
pub(crate) fn game_plan_formation_layout(text: &str) -> Option<FormationLayout> {
    Formation::parse(text)?.layout()
}

/// Player ids of the XI (roles 0-10) and the matchday bench (roles 11-17).
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct Matchday {
    pub(crate) starters: Vec<u32>,
    pub(crate) bench: Vec<u32>,
}

/// The team's matchday squad; `None` without a roster or game plan.
pub(crate) fn team_matchday(file: &EditFile, team_id: u32) -> Option<Matchday> {
    let plan = file.read_game_plan(team_id)?;
    let roster = file.roster(team_id)?;
    let count = MATCHDAY_SQUAD_SLOT_COUNT.min(roster.roster_size());
    let ids: Vec<u32> = plan.lineup()[..count]
        .iter()
        .map(|&slot| {
            roster
                .player_ids
                .get(usize::from(slot))
                .copied()
                .unwrap_or(0)
        })
        .collect();
    let split = FIRST_TEAM_SLOT_COUNT.min(ids.len());
    Some(Matchday {
        starters: ids[..split].to_vec(),
        bench: ids[split..].to_vec(),
    })
}

/// Fill empty (`0xFF`) captain, set-piece and attack-player roles of one
/// team; returns the roles that were filled.
pub(crate) fn assign_missing_game_plan_roles(
    file: &mut EditFile,
    team_id: u32,
) -> Vec<GamePlanRole> {
    let (Some(mut entry), Some(roster)) = (file.read_game_plan(team_id), file.roster(team_id))
    else {
        return Vec::new();
    };
    let squad = Squad::build(file, roster);
    let filled = assign_missing_roles(&mut entry, &squad);
    if !filled.is_empty() {
        file.write_game_plan(team_id, &entry);
    }
    filled
}

pub(crate) type RosterSpec = (u32, Vec<u32>, Vec<u16>);

/// `mock`: one team entry, roster entry and game plan per roster (game plans
/// are built for the first `game_plans` rosters).
pub(crate) fn mock(rosters: Vec<RosterSpec>, league: Vec<u32>) -> Mock {
    let count = rosters.len();
    Mock {
        players: rosters.iter().map(|(_, ids, _)| ids.len()).sum(),
        teams: count,
        team_players: count,
        game_plans: count,
        team_entries: rosters
            .iter()
            .map(|(team, _, _)| (*team, format!("Team {team}")))
            .collect(),
        rosters,
        league,
        ..Mock::default()
    }
}

pub(crate) fn open(mock: &Mock) -> EditFile {
    EditFile::from_bytes(mock.build()).expect("mock save")
}

/// One club `team` with ids `first..first+count` and shirts `1..=count`.
pub(crate) fn single_club(team: u32, first: u32, count: u32) -> EditFile {
    open(&mock(
        vec![(
            team,
            (first..first + count).collect(),
            (1..=count as u16).collect(),
        )],
        vec![team],
    ))
}

pub(crate) fn lineup(file: &EditFile, team: u32) -> Vec<u8> {
    file.game_plan(team).expect("game plan")[GP_LINEUP..GP_LINEUP + TP_MAX_PLAYERS].to_vec()
}

pub(crate) fn set_lineup(file: &mut EditFile, team: u32, lineup: &[u8]) {
    let offset = file.game_plan_offset(team).expect("game plan") + GP_LINEUP;
    file.data_mut()[offset..offset + lineup.len()].copy_from_slice(lineup);
}

pub(crate) fn game_plan_byte(file: &EditFile, team: u32, offset: usize) -> u8 {
    file.game_plan(team).expect("game plan")[offset]
}

pub(crate) fn set_game_plan_byte(file: &mut EditFile, team: u32, offset: usize, value: u8) {
    let base = file.game_plan_offset(team).expect("game plan");
    file.data_mut()[base + offset] = value;
}

/// Catalog metadata with registered positions only.
pub(crate) fn set_catalog_positions(file: &mut EditFile, positions: &[(u32, Position)]) {
    let players: HashMap<u32, PlayerInfo> = positions
        .iter()
        .map(|&(player_id, position)| {
            (
                player_id,
                PlayerInfo {
                    player_id,
                    name: format!("P{player_id}"),
                    position: Some(position),
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

/// Set the strong-foot bit of the player's save record (left = bit set).
/// The player must have a record (a named entry in the mock).
pub(crate) fn set_left_footed(file: &mut EditFile, player_id: u32, left: bool) {
    let at = file.player_entry_offset(player_id).expect("player record") + PE_STRONG_FOOT_BYTE;
    let byte = &mut file.data_mut()[at];
    if left {
        *byte |= PE_STRONG_FOOT_MASK;
    } else {
        *byte &= !PE_STRONG_FOOT_MASK;
    }
}

/// Every ability (incl. goalkeeping) at `value`; playable grades from `grades`.
pub(crate) fn uniform_abilities(
    position: Position,
    value: u8,
    grades: &[(Position, u8)],
) -> PlayerAbilities {
    let mut position_grades = [0; Position::COUNT];
    for &(position, grade) in grades {
        position_grades[usize::from(position.code())] = grade;
    }
    PlayerAbilities {
        registered_position: position,
        position_grades,
        abilities: [value; ABILITY_COUNT],
        goalkeeping: [value; GOALKEEPING_COUNT],
        weak_foot_accuracy: 2,
    }
}

/// One fake Player.bin record.
#[derive(Debug, Clone)]
pub(crate) struct FakeRecord {
    pub(crate) position: Option<Position>,
    pub(crate) age: u32,
    pub(crate) caps: u32,
    pub(crate) market_value_eur: u32,
    pub(crate) abilities: Option<PlayerAbilities>,
}

impl FakeRecord {
    pub(crate) fn new(position: Position) -> Self {
        Self {
            position: Some(position),
            age: 24,
            caps: 0,
            market_value_eur: 0,
            abilities: None,
        }
    }
}

/// `PlayerBinDatabase` stand-in keyed by player id.
#[derive(Debug, Default)]
pub(crate) struct FakeNative {
    pub(crate) records: HashMap<u32, FakeRecord>,
}

impl FakeNative {
    pub(crate) fn attach(self, file: &mut EditFile) {
        file.attach_native(Some(Arc::new(self)));
    }
}

impl NativePlayers for FakeNative {
    fn native_player(&self, player_id: u32) -> Option<NativePlayer<'_>> {
        let record = self.records.get(&player_id)?;
        Some(NativePlayer {
            player_id,
            name: "Player",
            print_name: "",
            position: record.position,
            age: record.age,
            abilities: record.abilities.as_ref(),
        })
    }

    fn native_career(&self, player_id: u32) -> Option<NativeCareer> {
        let record = self.records.get(&player_id)?;
        Some(NativeCareer {
            caps: record.caps,
            market_value_eur: record.market_value_eur,
        })
    }
}
