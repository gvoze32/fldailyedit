//! Typed view of one game-plan entry and the scalar game-plan accessors:
//! matchday squad, captain, tactical settings and formation.

use crate::edit_file::EditFile;
use crate::layout::*;

use super::error::{RosterError, RosterErrorKind};
use super::formation::{Formation, FormationLayout};

/// The empty-slot value of a lineup tail and of the role bytes.
pub(crate) const EMPTY: u8 = GP_EMPTY_ROLE;

pub(crate) type Lineup = [u8; TP_MAX_PLAYERS];

/// A role byte of the game plan: a roster slot or empty.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum GamePlanRole {
    Captain,
    LeftCorner,
    RightCorner,
    Penalty,
    /// One of the three attack players, `1..=3`.
    AttackPlayer(u8),
}

impl GamePlanRole {
    /// Report name (`"captain"`, `"attack_player_2"`, ...).
    #[cfg(test)]
    pub fn name(self) -> String {
        match self {
            Self::Captain => "captain".into(),
            Self::LeftCorner => "left_corner".into(),
            Self::RightCorner => "right_corner".into(),
            Self::Penalty => "penalty".into(),
            Self::AttackPlayer(n) => format!("attack_player_{n}"),
        }
    }

    /// Byte offset inside the game-plan entry.
    pub(crate) const fn offset(self) -> usize {
        match self {
            Self::Captain => GP_CAPTAIN,
            Self::LeftCorner => GP_LEFT_CK,
            Self::RightCorner => GP_RIGHT_CK,
            Self::Penalty => GP_PK,
            Self::AttackPlayer(n) => GP_ATTACK_PLAYERS + n as usize - 1,
        }
    }
}

/// Every role byte offset: the single-player roles, then the attack players.
pub(crate) fn all_role_offsets() -> impl Iterator<Item = usize> {
    GP_SINGLE_PLAYER_ROLES
        .into_iter()
        .chain((0..GP_ATTACK_PLAYER_COUNT).map(|i| GP_ATTACK_PLAYERS + i))
}

/// One 628-byte game-plan entry, copied out of the save so game-plan logic
/// can work on it while reading the rest of the file.
#[derive(Clone, PartialEq, Eq)]
pub(crate) struct GamePlanEntry {
    bytes: [u8; GAME_PLAN_ENTRY_SIZE],
}

impl GamePlanEntry {
    pub(crate) fn from_slice(bytes: &[u8]) -> Self {
        let mut entry = Self {
            bytes: [0; GAME_PLAN_ENTRY_SIZE],
        };
        entry.bytes.copy_from_slice(bytes);
        entry
    }

    pub(crate) fn as_bytes(&self) -> &[u8] {
        &self.bytes
    }

    pub(crate) fn lineup(&self) -> Lineup {
        let mut lineup = [0; TP_MAX_PLAYERS];
        lineup.copy_from_slice(&self.bytes[GP_LINEUP..GP_LINEUP + TP_MAX_PLAYERS]);
        lineup
    }

    pub(crate) fn set_lineup(&mut self, lineup: &Lineup) {
        self.bytes[GP_LINEUP..GP_LINEUP + TP_MAX_PLAYERS].copy_from_slice(lineup);
    }

    /// A role byte by its offset (`GP_CAPTAIN`, ...).
    pub(crate) fn role_byte(&self, offset: usize) -> u8 {
        self.bytes[offset]
    }

    pub(crate) fn set_role_byte(&mut self, offset: usize, value: u8) {
        self.bytes[offset] = value;
    }

    /// Position code of `role` (0..11) in `phase` (index into
    /// [`GP_POSITION_PHASE_OFFSETS`]) of `preset` (index into
    /// [`GP_POSITION_PRESETS`]).
    pub(crate) fn position_code(&self, preset: usize, phase: usize, role: usize) -> u8 {
        self.bytes[Self::position_address(preset, phase, role)]
    }

    pub(crate) fn set_position_code(&mut self, preset: usize, phase: usize, role: usize, code: u8) {
        self.bytes[Self::position_address(preset, phase, role)] = code;
    }

    /// Position code of `role` in the main preset's first phase: the formation
    /// slot the role renders in.
    pub(crate) fn formation_code(&self, role: usize) -> u8 {
        self.position_code(0, 0, role)
    }

    /// Vertical coordinate of `role` in the main preset's first phase.
    pub(crate) fn formation_vertical(&self, role: usize) -> u8 {
        self.bytes[GP_POSITION_PRESETS[0]
            + GP_POSITION_COORDINATE_OFFSETS[0]
            + role * GP_POSITION_COORDINATE_ENTRY_SIZE]
    }

    fn position_address(preset: usize, phase: usize, role: usize) -> usize {
        GP_POSITION_PRESETS[preset] + GP_POSITION_PHASE_OFFSETS[phase] + role
    }

    /// Visit every (preset, phase) pair.
    pub(crate) fn phases() -> impl Iterator<Item = (usize, usize)> {
        (0..GP_POSITION_PRESETS.len()).flat_map(|preset| {
            (0..GP_POSITION_PHASE_OFFSETS.len()).map(move |phase| (preset, phase))
        })
    }
}

impl EditFile {
    pub(crate) fn read_game_plan(&self, team_id: u32) -> Option<GamePlanEntry> {
        self.game_plan(team_id).map(GamePlanEntry::from_slice)
    }

    /// Copy `entry` back over the team's game plan.
    pub(crate) fn write_game_plan(&mut self, team_id: u32, entry: &GamePlanEntry) {
        if let Some(offset) = self.game_plan_offset(team_id) {
            self.data_mut()[offset..offset + GAME_PLAN_ENTRY_SIZE]
                .copy_from_slice(entry.as_bytes());
        }
    }

    /// The player currently stored as captain.
    pub fn team_captain_player(&self, team_id: u32) -> Option<u32> {
        let plan = self.read_game_plan(team_id)?;
        let roster = self.roster(team_id)?;
        let slot = plan.role_byte(GP_CAPTAIN);
        if slot == EMPTY {
            return None;
        }
        roster
            .player_ids
            .get(usize::from(slot))
            .copied()
            .filter(|&id| id != 0)
    }

    /// Make `player_id` the captain (by its single roster slot).
    pub fn set_team_captain(&mut self, team_id: u32, player_id: u32) -> Result<(), RosterError> {
        let Some(mut plan) = self.read_game_plan(team_id) else {
            return Err(game_plan_missing(team_id));
        };
        let roster = self.roster(team_id).filter(|_| player_id != 0);
        let mut slots = roster.iter().flat_map(|roster| {
            roster
                .player_ids
                .iter()
                .enumerate()
                .filter(|&(_, &id)| id == player_id)
                .map(|(slot, _)| slot)
        });
        let (Some(slot), None) = (slots.next(), slots.next()) else {
            return Err(RosterError::new(
                RosterErrorKind::CaptainNotOnRoster,
                format!("Player {player_id} is not on team {team_id} exactly once"),
            ));
        };
        plan.set_role_byte(GP_CAPTAIN, slot as u8);
        self.write_game_plan(team_id, &plan);
        Ok(())
    }

    /// Set main-preset tactical fields; returns how many bytes changed. Every
    /// value is validated before anything is written.
    pub fn set_team_tactical_settings(
        &mut self,
        team_id: u32,
        settings: &[(TacticalField, u8)],
    ) -> Result<usize, RosterError> {
        let Some(mut plan) = self.read_game_plan(team_id) else {
            return Err(game_plan_missing(team_id));
        };
        for &(field, value) in settings {
            let (low, high) = field.value_range();
            if !(low..=high).contains(&value) {
                return Err(RosterError::new(
                    RosterErrorKind::InvalidTacticalSetting,
                    format!("{} must be {low}..={high}, got {value}", field.name()),
                ));
            }
        }
        let mut changed = 0;
        for &(field, value) in settings {
            let address = GP_POSITION_PRESETS[0] + field.offset();
            if plan.bytes[address] != value {
                plan.bytes[address] = value;
                changed += 1;
            }
        }
        if changed > 0 {
            self.write_game_plan(team_id, &plan);
        }
        Ok(changed)
    }

    /// Write the role and coordinate arrays of `formation` to every preset and
    /// phase (the other presets mirror the main one so switching presets
    /// in-game never shows a stale shape); returns how many bytes changed.
    pub fn set_team_formation(
        &mut self,
        team_id: u32,
        formation: &str,
    ) -> Result<usize, RosterError> {
        let Some(layout) = Formation::parse(formation).and_then(|f| f.layout()) else {
            return Err(RosterError::new(
                RosterErrorKind::UnsupportedFormation,
                format!("Unsupported formation: '{formation}'"),
            ));
        };
        let Some(mut plan) = self.read_game_plan(team_id) else {
            return Err(game_plan_missing(team_id));
        };
        let changed = write_layout(&mut plan, &layout);
        if changed > 0 {
            self.write_game_plan(team_id, &plan);
        }
        Ok(changed)
    }
}

fn game_plan_missing(team_id: u32) -> RosterError {
    RosterError::new(
        RosterErrorKind::GamePlanNotFound,
        format!("No game plan found for team {team_id}"),
    )
}

fn write_layout(plan: &mut GamePlanEntry, layout: &FormationLayout) -> usize {
    let mut changed = 0;
    for (preset, phase) in GamePlanEntry::phases() {
        for (role, &position) in layout.roles.iter().enumerate() {
            if plan.position_code(preset, phase, role) != position.code() {
                plan.set_position_code(preset, phase, role, position.code());
                changed += 1;
            }
        }
        let base = GP_POSITION_PRESETS[preset] + GP_POSITION_COORDINATE_OFFSETS[phase];
        for (role, &(vertical, horizontal)) in layout.coordinates.iter().enumerate() {
            let address = base + role * GP_POSITION_COORDINATE_ENTRY_SIZE;
            if plan.bytes[address..address + 2] != [vertical, horizontal] {
                plan.bytes[address..address + 2].copy_from_slice(&[vertical, horizontal]);
                changed += GP_POSITION_COORDINATE_ENTRY_SIZE;
            }
        }
    }
    changed
}
