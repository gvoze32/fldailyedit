//! A club's roster with the per-player metadata game-plan logic ranks on.

use std::collections::HashMap;

use crate::edit_file::{EditFile, PreferredFoot};
use crate::layout::TP_MAX_PLAYERS;
use crate::model::{NativeCareer, Position, TeamData};
use crate::rating::PlayerAbilities;
use crate::release_policy::PlayerUsage;

/// Position labels that replace a player's registered position for one
/// operation (live-lineup positions, the position of a signing). A `None`
/// value is a label that names no position: it still hides the registered one.
pub(crate) type PositionOverrides = HashMap<u32, Option<Position>>;

/// Metadata of one rostered player.
#[derive(Debug, Clone)]
pub(crate) struct Member {
    pub(crate) id: u32,
    /// Native position first, then the player catalog.
    pub(crate) position: Option<Position>,
    /// In-game abilities (EDIT entry, else Player.bin).
    pub(crate) abilities: Option<PlayerAbilities>,
    pub(crate) foot: Option<PreferredFoot>,
    pub(crate) usage: Option<PlayerUsage>,
    pub(crate) career: Option<NativeCareer>,
    /// Native age, else the catalog age; 0 = unknown.
    pub(crate) age: u32,
}

/// One roster, slot by slot (`None` = empty slot).
#[derive(Debug, Clone)]
pub(crate) struct Squad {
    pub(crate) team: TeamData,
    members: [Option<Member>; TP_MAX_PLAYERS],
}

impl Squad {
    pub(crate) fn build(file: &EditFile, team: TeamData) -> Self {
        let members = std::array::from_fn(|slot| {
            let id = team.player_ids[slot];
            (id != 0).then(|| file.member(id))
        });
        Self { team, members }
    }

    /// Player id of a slot; 0 for empty or out-of-range slots.
    pub(crate) fn id_at(&self, slot: usize) -> u32 {
        self.team.player_ids.get(slot).copied().unwrap_or(0)
    }

    pub(crate) fn member(&self, slot: usize) -> Option<&Member> {
        self.members.get(slot)?.as_ref()
    }

    /// Occupied slots in slot order.
    pub(crate) fn active_slots(&self) -> Vec<usize> {
        (0..TP_MAX_PLAYERS)
            .filter(|&slot| self.team.player_ids[slot] != 0)
            .collect()
    }

    pub(crate) fn roster_size(&self) -> usize {
        self.team.roster_size()
    }

    /// Registered position of the player in `slot`, overridden by `overrides`.
    pub(crate) fn position(&self, slot: usize, overrides: &PositionOverrides) -> Option<Position> {
        let member = self.member(slot)?;
        match overrides.get(&member.id) {
            Some(position) => *position,
            None => member.position,
        }
    }

    /// The game-plan position code of the player in `slot`.
    pub(crate) fn position_code(&self, slot: usize, overrides: &PositionOverrides) -> Option<u8> {
        self.position(slot, overrides).map(Position::code)
    }

    /// Whether every listed (occupied) slot has native abilities. Ratings only
    /// rank a group when all members have one: a missing rating would
    /// otherwise read as the worst player of the group.
    pub(crate) fn all_rated(&self, slots: impl IntoIterator<Item = usize>) -> bool {
        slots.into_iter().all(|slot| match self.member(slot) {
            Some(member) => member.abilities.is_some(),
            None => true,
        })
    }

    /// Rating of the player in `slot` at a raw game-plan position code.
    pub(crate) fn role_rating(&self, slot: usize, code: Option<u8>) -> Option<i32> {
        let position = Position::from_index(code?)?;
        Some(
            self.member(slot)?
                .abilities
                .as_ref()?
                .position_rating(position),
        )
    }

    /// Native overall of the player in `slot`.
    pub(crate) fn overall(&self, slot: usize) -> Option<i32> {
        Some(self.member(slot)?.abilities.as_ref()?.overall())
    }
}

impl EditFile {
    /// All metadata the game-plan logic needs for one player.
    pub(crate) fn member(&self, player_id: u32) -> Member {
        let native = self.native();
        Member {
            id: player_id,
            position: self.player_position(player_id),
            abilities: self.abilities(player_id),
            foot: self.preferred_foot(player_id),
            usage: self.release_usage(player_id).copied(),
            career: native.and_then(|native| native.native_career(player_id)),
            age: self.player_age(player_id),
        }
    }

    /// Native age, else the catalog age; 0 when unknown.
    fn player_age(&self, player_id: u32) -> u32 {
        let native = self
            .native()
            .and_then(|native| native.native_player(player_id))
            .map_or(0, |record| record.age);
        if native != 0 {
            return native;
        }
        self.player_catalog()
            .and_then(|catalog| catalog.players.get(&player_id))
            .map_or(0, |info| info.age)
    }
}
