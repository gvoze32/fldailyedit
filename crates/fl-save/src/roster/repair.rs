//! Whole-save game-plan repair.

use std::collections::{HashMap, HashSet};

use crate::edit_file::{EditFile, read_u32};
use crate::layout::*;
use crate::model::TeamData;

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
    /// Teams whose XI is re-picked for the existing formation roles (best
    /// native rating per role; the position arrays stay untouched except for
    /// goalkeeper invariants) and whose bench is reordered by native overall.
    pub align_teams: HashSet<u32>,
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
    /// Repair roster mappings in every game plan and re-pick the XI and bench
    /// of `align_teams`.
    ///
    /// Valid roster-slot references keep their relative order. Aligned teams
    /// and teams with signings from this session get their matchday bench
    /// (roles 11-17) ordered signings first, then highest native overall,
    /// with a second goalkeeper guaranteed. Empty captain, set-piece and
    /// attack-player roles are filled from save data for every team.
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

            let align = options.align_teams.contains(&team_id);
            if align && repair_starter_assignments(&mut entry, &squad) {
                metrics.repaired_lineups += 1;
            }

            let (role_repairs, position_repairs) = repair_goalkeeper_positions(
                &mut entry,
                &squad,
                &no_overrides,
                options.preserve_existing_primary,
            );
            metrics.repaired_goalkeeper_roles += role_repairs;
            metrics.repaired_position_bytes += position_repairs;

            let team_signings: Vec<u32> = roster
                .player_ids
                .iter()
                .copied()
                .filter(|id| *id != 0 && signings.contains(id))
                .collect();
            if (align || !team_signings.is_empty())
                && order_bench(&mut entry, &squad, &team_signings)
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
