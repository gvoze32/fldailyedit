//! Whole-save game-plan repair.

use std::collections::{HashMap, HashSet};

use crate::edit_file::{EditFile, read_u32};
use crate::layout::*;
use crate::model::{Position, TeamData};

use super::game_plan::{EMPTY, GamePlanEntry, all_role_offsets};
use super::lineup::{order_bench, repair_goalkeeper_positions};
use super::roles::assign_missing_roles;
use super::squad::{PositionOverrides, Squad};
use super::starters::repair_starter_assignments;

/// What [`EditFile::repair_game_plans`] should reconcile.
#[derive(Debug, Clone, Default)]
pub struct RepairOptions {
    /// Never replace a role-0 goalkeeper that is already a known keeper.
    pub preserve_existing_primary: bool,
    /// Live-XI player ids per team. With `align_positions`, candidates can be
    /// promoted into the existing formation roles (the position arrays stay
    /// untouched except for goalkeeper invariants) and a live goalkeeper takes
    /// role zero. `None` disables XI alignment.
    pub preferred_starters: Option<HashMap<u32, Vec<u32>>>,
    /// Live-lineup positions that replace a player's registered one, per team.
    pub position_overrides: HashMap<u32, HashMap<u32, Position>>,
    pub align_positions: bool,
    /// Live substitutes per team, ordered onto the matchday bench first.
    pub preferred_bench: HashMap<u32, Vec<u32>>,
}

/// Counters of one repair run.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct RepairMetrics {
    pub checked_game_plans: usize,
    /// Game plans whose bytes ended up different. The counters below also
    /// count steps that a later step undid (the keeper swapped into role zero
    /// after the starter alignment moved him out), so only this one says
    /// whether the save changed.
    pub changed_game_plans: usize,
    pub repaired_lineups: usize,
    pub reset_roles: usize,
    pub filled_roles: usize,
    pub reordered_benches: usize,
    pub repaired_goalkeeper_roles: usize,
    pub repaired_position_bytes: usize,
}

impl RepairMetrics {
    /// `(report key, count)` pairs in report order.
    pub fn entries(&self) -> [(&'static str, usize); 7] {
        [
            ("checked_game_plans", self.checked_game_plans),
            ("repaired_lineups", self.repaired_lineups),
            ("reset_roles", self.reset_roles),
            ("filled_roles", self.filled_roles),
            ("reordered_benches", self.reordered_benches),
            ("repaired_goalkeeper_roles", self.repaired_goalkeeper_roles),
            ("repaired_position_bytes", self.repaired_position_bytes),
        ]
    }
}

impl EditFile {
    /// Repair roster mappings in every game plan and optionally reconcile the
    /// current starters.
    ///
    /// Valid roster-slot references keep their relative order. Teams with
    /// `preferred_bench` entries or signings from this session get their
    /// matchday bench (roles 11-17) ordered live substitutes first, then
    /// signings, then highest native overall, with a second goalkeeper
    /// guaranteed. Empty captain, set-piece and attack-player roles are
    /// filled from save data for every team.
    pub fn repair_game_plans(&mut self, options: &RepairOptions) -> RepairMetrics {
        // A repeated team entry resolves to the last one.
        let rosters: HashMap<u32, TeamData> = self
            .rosters()
            .into_iter()
            .map(|team| (team.team_id, team))
            .collect();
        let signings: HashSet<u32> = self.mutation.transferred_player_ids.clone();
        let offsets: Vec<usize> = self.game_plan_entry_offsets().collect();
        let no_overrides = PositionOverrides::new();
        let mut metrics = RepairMetrics::default();

        for offset in offsets {
            let team_id = read_u32(self.bytes(), offset + GP_TEAM_ID);
            let Some(roster) = rosters.get(&team_id) else {
                continue;
            };
            if roster.roster_size() == 0 {
                continue;
            }
            metrics.checked_game_plans += 1;
            let squad = Squad::build(self, roster.clone());
            let mut entry =
                GamePlanEntry::from_slice(&self.bytes()[offset..offset + GAME_PLAN_ENTRY_SIZE]);
            let original = entry.clone();

            let active_slots = squad.active_slots();
            let active_count = active_slots.len();

            // Valid slot references keep their order; missing ones follow.
            let mut lineup = entry.lineup();
            let mut preserved: Vec<u8> = Vec::with_capacity(active_count);
            for &slot in &lineup {
                if active_slots.binary_search(&usize::from(slot)).is_ok()
                    && !preserved.contains(&slot)
                {
                    preserved.push(slot);
                }
            }
            preserved.extend(
                active_slots
                    .iter()
                    .map(|&slot| slot as u8)
                    .filter(|slot| !preserved.contains(slot))
                    .collect::<Vec<_>>(),
            );
            if lineup[..active_count] != preserved[..] {
                lineup[..active_count].copy_from_slice(&preserved);
                entry.set_lineup(&lineup);
                metrics.repaired_lineups += 1;
            }

            let team_preferred: &[u32] = options
                .preferred_starters
                .as_ref()
                .and_then(|map| map.get(&team_id))
                .map_or(&[], Vec::as_slice);
            if options.align_positions && options.preferred_starters.is_some() {
                let overrides: PositionOverrides = options
                    .position_overrides
                    .get(&team_id)
                    .map(|map| {
                        map.iter()
                            .map(|(&id, &position)| (id, Some(position)))
                            .collect()
                    })
                    .unwrap_or_default();
                if repair_starter_assignments(&mut entry, &squad, &overrides, team_preferred) {
                    metrics.repaired_lineups += 1;
                }
            }

            // A live first-choice keeper placed in role zero must not be
            // swapped back for the lowest-slot goalkeeper.
            let role0_slot = usize::from(entry.lineup()[0]);
            let live_primary = options.align_positions
                && squad.id_at(role0_slot) != 0
                && team_preferred.contains(&squad.id_at(role0_slot))
                && squad.position(role0_slot, &no_overrides) == Some(Position::Gk);
            let (role_repairs, position_repairs) = repair_goalkeeper_positions(
                &mut entry,
                &squad,
                &no_overrides,
                options.preserve_existing_primary || live_primary,
            );
            metrics.repaired_goalkeeper_roles += role_repairs;
            metrics.repaired_position_bytes += position_repairs;

            let team_bench: &[u32] = options
                .preferred_bench
                .get(&team_id)
                .map_or(&[], Vec::as_slice);
            let team_signings: Vec<u32> = roster
                .player_ids
                .iter()
                .copied()
                .filter(|id| *id != 0 && signings.contains(id))
                .collect();
            if (!team_bench.is_empty() || !team_signings.is_empty())
                && order_bench(&mut entry, &squad, team_bench, &team_signings)
            {
                metrics.reordered_benches += 1;
            }

            for role_offset in all_role_offsets() {
                let value = entry.role_byte(role_offset);
                if value != EMPTY && active_slots.binary_search(&usize::from(value)).is_err() {
                    entry.set_role_byte(role_offset, EMPTY);
                    metrics.reset_roles += 1;
                }
            }
            metrics.filled_roles += assign_missing_roles(&mut entry, &squad).len();

            if entry != original {
                metrics.changed_game_plans += 1;
                self.data_mut()[offset..offset + GAME_PLAN_ENTRY_SIZE]
                    .copy_from_slice(entry.as_bytes());
            }
        }
        metrics
    }
}
