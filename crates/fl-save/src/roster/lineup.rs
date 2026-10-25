//! Keeping a club's lineup (the roster slot behind each role) consistent when
//! a roster slot is vacated or filled, plus the goalkeeper invariants and the
//! bench order.
//!
//! Everything here works on a [`GamePlanEntry`] copy and a [`Squad`] view of
//! the roster as it is AFTER the roster change.

use std::collections::{BTreeSet, HashMap, HashSet};

use crate::layout::*;
use crate::model::{Position, PositionLine};

use super::game_plan::{EMPTY, GamePlanEntry, Lineup, all_role_offsets};
use super::squad::{PositionOverrides, Squad};

/// The position array fallback for an outfield role without a known player.
const FALLBACK_OUTFIELD_CODE: u8 = Position::Cb.code();

/// How a lineup update ended; anything but `Updated` left the lineup as it was
/// (the caller keeps the save valid by preserving an unrecognised order).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum LineupUpdate {
    Updated,
    /// The active prefix of the lineup is not a permutation of the active
    /// roster slots, so no role can be tracked.
    NonStandardPrefix,
    /// The compacted role list does not cover exactly the active slots.
    CompactionUnverified,
    /// The rebuilt lineup would not fill all 40 entries.
    RebuildMismatch,
}

// ── goalkeeper invariants ────────────────────────────────────────────────

/// Keep known goalkeepers in role 0 and repair the goalkeeper position
/// markers. Returns `(repaired lineup roles, repaired position bytes)`.
///
/// With `preserve_existing_primary` a global reconciliation never invents a
/// different primary keeper when role 0 is already a known goalkeeper.
pub(crate) fn repair_goalkeeper_positions(
    entry: &mut GamePlanEntry,
    squad: &Squad,
    overrides: &PositionOverrides,
    preserve_existing_primary: bool,
) -> (usize, usize) {
    let active_count = squad.roster_size().min(TP_MAX_PLAYERS);
    let starter_count = FIRST_TEAM_SLOT_COUNT.min(active_count);
    let mut lineup = entry.lineup();

    let is_goalkeeper_role = |lineup: &Lineup, role: usize| {
        let slot = usize::from(lineup[role]);
        slot < TP_MAX_PLAYERS && squad.position_code(slot, overrides) == Some(Position::Gk.code())
    };
    let role_has_goalkeeper_codes = |role: usize| {
        GamePlanEntry::phases().all(|(preset, phase)| entry.position_code(preset, phase, role) == 0)
    };

    let role0_is_unknown_incumbent = starter_count > 0 && {
        let slot = usize::from(lineup[0]);
        slot < TP_MAX_PLAYERS
            && squad.id_at(slot) != 0
            && squad.position_code(slot, overrides).is_none()
            && role_has_goalkeeper_codes(0)
    };

    let starter_goalkeeper_roles: Vec<usize> = (0..starter_count)
        .filter(|&role| is_goalkeeper_role(&lineup, role))
        .collect();
    let bench_goalkeeper_roles: Vec<usize> = (starter_count..active_count)
        .filter(|&role| is_goalkeeper_role(&lineup, role))
        .collect();
    let goalkeeper_roles: Vec<usize> =
        if role0_is_unknown_incumbent && !bench_goalkeeper_roles.is_empty() {
            // A role-zero player with an unknown label and a valid keeper marker
            // is an incumbent, not a reason to promote an arbitrary reserve.
            vec![0]
        } else {
            [starter_goalkeeper_roles, bench_goalkeeper_roles].concat()
        };
    if goalkeeper_roles.is_empty() {
        return (0, 0);
    }

    let mut repaired_roles = 0;
    let mut lineup_changed = false;
    let primary_role = if preserve_existing_primary && goalkeeper_roles.contains(&0) {
        0
    } else {
        goalkeeper_roles
            .iter()
            .copied()
            .min_by_key(|&role| (lineup[role], role))
            .unwrap_or(0)
    };
    if primary_role != 0 {
        lineup.swap(0, primary_role);
        repaired_roles += 1;
        lineup_changed = true;
    }

    let mut bench_roles: Vec<usize> = (starter_count..active_count)
        .filter(|&role| {
            let slot = usize::from(lineup[role]);
            squad.position_code(slot, overrides) != Some(Position::Gk.code())
        })
        .collect();
    let extra_starter_goalkeeper_roles: Vec<usize> = (1..starter_count)
        .filter(|&role| is_goalkeeper_role(&lineup, role))
        .collect();
    for goalkeeper_role in extra_starter_goalkeeper_roles {
        if bench_roles.is_empty() {
            break;
        }
        let bench_role = bench_roles.remove(0);
        lineup.swap(goalkeeper_role, bench_role);
        repaired_roles += 1;
        lineup_changed = true;
    }

    if lineup_changed {
        entry.set_lineup(&lineup);
    }
    let position_repairs = repair_position_arrays(entry, squad, &lineup, overrides);
    (repaired_roles, position_repairs)
}

/// Ensure each non-empty plan has one goalkeeper code per phase: role 0 is
/// always code 0, and empty outfield codes are filled from the player in the
/// role. Without player metadata an all-outfield array is ambiguous and left
/// untouched, so integrity validation fails closed instead of inventing a
/// goalkeeper.
fn repair_position_arrays(
    entry: &mut GamePlanEntry,
    squad: &Squad,
    lineup: &Lineup,
    overrides: &PositionOverrides,
) -> usize {
    let active_count = FIRST_TEAM_SLOT_COUNT.min(squad.roster_size());
    if active_count == 0 {
        return 0;
    }
    let role_code = |role: usize| -> Option<u8> {
        if role >= active_count {
            return None;
        }
        let slot = usize::from(lineup[role]);
        (slot < TP_MAX_PLAYERS)
            .then(|| squad.position_code(slot, overrides))
            .flatten()
    };
    if role_code(0) != Some(Position::Gk.code()) {
        return 0;
    }

    let mut repaired = 0;
    for (preset, phase) in GamePlanEntry::phases() {
        for role in 0..FIRST_TEAM_SLOT_COUNT {
            let current = entry.position_code(preset, phase, role);
            let expected = if role == 0 {
                0
            } else if current != 0 {
                continue;
            } else {
                // A goalkeeper outside role 0 gets the outfield fallback too.
                match role_code(role) {
                    Some(code) if code != Position::Gk.code() => code,
                    _ => FALLBACK_OUTFIELD_CODE,
                }
            };
            if current != expected {
                entry.set_position_code(preset, phase, role, expected);
                repaired += 1;
            }
        }
    }
    repaired
}

// ── removal ──────────────────────────────────────────────────────────────

/// A vacated roster slot.
pub(crate) struct Removal<'a> {
    /// The slot that was vacated (it may now hold the copied last player).
    pub(crate) removed_idx: usize,
    /// The slot whose player was copied into `removed_idx`, if any.
    pub(crate) replacement_idx: Option<usize>,
    /// Registered position of the removed player, if known.
    pub(crate) removed_position: Option<Position>,
    /// Live-XI ids used to rank backfills.
    pub(crate) preferred_starters: &'a [u32],
}

/// Keep roles attached to players after roster compaction.
///
/// Compaction copies the last active player into the vacated slot; that
/// player keeps their own role. A vacated starter role is backfilled in place
/// from the non-starters, so no other role index ever shifts. A lineup whose
/// active prefix is not a permutation of the active slots is preserved.
pub(crate) fn compact_lineup_after_removal(
    entry: &mut GamePlanEntry,
    squad: &Squad,
    removal: &Removal<'_>,
) -> LineupUpdate {
    let lineup = entry.lineup();
    let new_active: BTreeSet<usize> = squad.active_slots().into_iter().collect();
    let stale_slot = removal.replacement_idx.unwrap_or(removal.removed_idx);
    let mut old_active = new_active.clone();
    old_active.insert(stale_slot);
    let old_count = old_active.len();
    if !(1..=TP_MAX_PLAYERS).contains(&old_count) {
        return LineupUpdate::NonStandardPrefix;
    }

    let active_order: Vec<usize> = lineup[..old_count]
        .iter()
        .map(|&s| usize::from(s))
        .collect();
    let order_set: BTreeSet<usize> = active_order.iter().copied().collect();
    if order_set.len() != old_count
        || order_set != old_active
        || !order_set.contains(&removal.removed_idx)
        || removal
            .replacement_idx
            .is_some_and(|replacement| !order_set.contains(&replacement))
    {
        return LineupUpdate::NonStandardPrefix;
    }

    let remap = |slot: usize| -> Option<usize> {
        if slot == removal.removed_idx {
            None
        } else if Some(slot) == removal.replacement_idx {
            Some(removal.removed_idx)
        } else {
            Some(slot)
        }
    };
    let mut roles: Vec<Option<usize>> = active_order.iter().map(|&slot| remap(slot)).collect();
    let removed_role = active_order
        .iter()
        .position(|&slot| slot == removal.removed_idx)
        .unwrap_or(0);

    let target_position_code = (removed_role < FIRST_TEAM_SLOT_COUNT)
        .then(|| entry.formation_code(removed_role))
        .filter(|&raw| removed_role == 0 || raw != 0);
    let promoted = if removed_role < FIRST_TEAM_SLOT_COUNT && old_count > FIRST_TEAM_SLOT_COUNT {
        let candidates: Vec<usize> = roles[FIRST_TEAM_SLOT_COUNT..]
            .iter()
            .flatten()
            .copied()
            .collect();
        select_promotion_slot(
            squad,
            &Promotion {
                preferred_starters: removal.preferred_starters,
                removed_position: removal.removed_position,
                removed_role,
                target_position_code,
                copied_slot: removal.replacement_idx.map(|_| removal.removed_idx),
            },
            &candidates,
        )
    } else {
        None
    };

    if let Some(promoted) = promoted {
        if let Some(at) = roles[FIRST_TEAM_SLOT_COUNT..]
            .iter()
            .position(|&role| role == Some(promoted))
        {
            roles.remove(FIRST_TEAM_SLOT_COUNT + at);
        }
        roles[removed_role] = Some(promoted);
    } else if removed_role < FIRST_TEAM_SLOT_COUNT && removed_role + 1 < roles.len() {
        // No non-starter can take the role: move the last role's player into
        // the vacated index so no starter role shifts.
        if let Some(last) = roles.pop() {
            roles[removed_role] = last;
        }
    }
    let compacted: Vec<usize> = roles.into_iter().flatten().collect();
    let compacted_set: BTreeSet<usize> = compacted.iter().copied().collect();
    if compacted.len() != new_active.len() || compacted_set != new_active {
        return LineupUpdate::CompactionUnverified;
    }

    // Keep the inactive tactical bytes in place: appending the stale roster
    // slot preserves the full-permutation layout when the tail is one, while
    // still allowing legacy 0xFF tails.
    let mut new_lineup: Vec<u8> = compacted.iter().map(|&slot| slot as u8).collect();
    new_lineup.extend_from_slice(&lineup[old_count..]);
    new_lineup.push(stale_slot as u8);
    let Ok(new_lineup) = Lineup::try_from(new_lineup) else {
        return LineupUpdate::RebuildMismatch;
    };
    entry.set_lineup(&new_lineup);
    repair_goalkeeper_positions(entry, squad, &PositionOverrides::new(), true);
    LineupUpdate::Updated
}

/// Re-point the captain / set-piece / attack-player role bytes after a
/// compaction: roles of the removed slot become empty, roles of the copied
/// player follow it into its new slot. Independent of the lineup.
pub(crate) fn remap_roles_after_removal(
    entry: &mut GamePlanEntry,
    removed_idx: usize,
    replacement_idx: Option<usize>,
) {
    for offset in all_role_offsets() {
        let value = usize::from(entry.role_byte(offset));
        if value == removed_idx {
            entry.set_role_byte(offset, EMPTY);
        } else if Some(value) == replacement_idx {
            entry.set_role_byte(offset, removed_idx as u8);
        }
    }
}

// ── promotion ────────────────────────────────────────────────────────────

/// What role is being backfilled.
pub(crate) struct Promotion<'a> {
    pub(crate) preferred_starters: &'a [u32],
    pub(crate) removed_position: Option<Position>,
    pub(crate) removed_role: usize,
    /// Raw formation code of the vacated role, when it carries one.
    pub(crate) target_position_code: Option<u8>,
    /// Slot that now holds the player copied from the last slot.
    pub(crate) copied_slot: Option<usize>,
}

fn line_of(code: Option<u8>) -> Option<PositionLine> {
    Position::from_index(code?).map(Position::line)
}

/// Rank every non-starter for a vacated starter role.
///
/// Exact formation-role players win, then same-line players, then any other
/// outfield player, so the vacated role is backfilled in place. Within one
/// tier: live-XI players, then the highest native rating at the vacated
/// role, then offline usage, then the existing bench order. `None` only when
/// no candidate can legally take the role.
fn select_promotion_slot(
    squad: &Squad,
    promotion: &Promotion<'_>,
    candidate_slots: &[usize],
) -> Option<usize> {
    let removed_code = promotion.removed_position.map(Position::code);
    let role_code = promotion.target_position_code.or(removed_code);
    let target_is_goalkeeper = role_code == Some(Position::Gk.code())
        || (role_code.is_none()
            && promotion.removed_position.is_none()
            && promotion.removed_role == 0);
    let target_line = line_of(role_code);

    let preferred: HashMap<u32, usize> = promotion
        .preferred_starters
        .iter()
        .enumerate()
        .map(|(rank, &id)| (id, rank))
        .collect();
    let rated = squad.all_rated(
        candidate_slots
            .iter()
            .copied()
            .filter(|&slot| slot < TP_MAX_PLAYERS),
    );
    let rating_code = rated.then(|| {
        if target_is_goalkeeper {
            Some(Position::Gk.code())
        } else {
            role_code
        }
    });
    // Slot zero is the historical keeper only while it still holds its
    // original player, not the copied last-slot player after compaction.
    let native_slot_zero = promotion.copied_slot != Some(0);

    type Key = (u8, usize, i64, i64, i64, usize);
    let mut best: Option<(Key, usize)> = None;
    for (order, &slot) in candidate_slots.iter().enumerate() {
        let Some(member) = squad.member(slot) else {
            continue;
        };
        let code = member.position.map(Position::code);
        let tier = if target_is_goalkeeper {
            match code {
                Some(c) if c == Position::Gk.code() => 0,
                None if slot == 0 && native_slot_zero => 1,
                None => 2,
                Some(_) => continue,
            }
        } else if code == Some(Position::Gk.code()) {
            continue;
        } else if role_code.is_some() && code == role_code {
            0
        } else if target_line.is_some() && line_of(code) == target_line {
            1
        } else if code.is_none() && slot == 0 && native_slot_zero {
            // Unknown slot-zero players are usually goalkeepers.
            3
        } else {
            2
        };
        let rating = rating_code
            .and_then(|code| squad.role_rating(slot, code))
            .unwrap_or(0);
        let (starts, minutes) = member
            .usage
            .map_or((0, 0), |usage| (usage.starts as i64, usage.minutes as i64));
        let key = (
            tier,
            preferred
                .get(&member.id)
                .copied()
                .unwrap_or(preferred.len()),
            -i64::from(rating),
            -starts,
            -minutes,
            order,
        );
        if best.as_ref().is_none_or(|(best_key, _)| key < *best_key) {
            best = Some((key, slot));
        }
    }
    best.map(|(_, slot)| slot)
}

// ── addition ─────────────────────────────────────────────────────────────

/// Register a newly signed player as the first substitute (role 11): starter
/// roles stay, later bench roles move down by one, so the signing is inside
/// the matchday 18. The roster slot can be sparse in a legacy save, so it is
/// never used as a formation-role index. `overrides` carries the signing's
/// position so the goalkeeper checks see it.
pub(crate) fn insert_after_addition(
    entry: &mut GamePlanEntry,
    squad: &Squad,
    added_slot: usize,
    overrides: &PositionOverrides,
) -> LineupUpdate {
    let active = squad.active_slots();
    if added_slot >= TP_MAX_PLAYERS || !active.contains(&added_slot) {
        return LineupUpdate::NonStandardPrefix;
    }
    let old_active: BTreeSet<usize> = active
        .into_iter()
        .filter(|&slot| slot != added_slot)
        .collect();
    let old_count = old_active.len();
    if old_count >= TP_MAX_PLAYERS {
        return LineupUpdate::NonStandardPrefix;
    }
    let lineup = entry.lineup();
    let prefix: Vec<u8> = lineup[..old_count].to_vec();
    let prefix_set: BTreeSet<usize> = prefix.iter().map(|&s| usize::from(s)).collect();
    if prefix_set.len() != old_count || prefix_set != old_active {
        return LineupUpdate::NonStandardPrefix;
    }

    let mut tail: Vec<u8> = lineup[old_count..].to_vec();
    match tail
        .iter()
        .position(|&slot| usize::from(slot) == added_slot)
    {
        Some(at) => {
            tail.remove(at);
        }
        None => {
            tail.remove(0);
        }
    }
    let insert_role = old_count.min(FIRST_TEAM_SLOT_COUNT);
    let mut rebuilt = Vec::with_capacity(TP_MAX_PLAYERS);
    rebuilt.extend_from_slice(&prefix[..insert_role]);
    rebuilt.push(added_slot as u8);
    rebuilt.extend_from_slice(&prefix[insert_role..]);
    rebuilt.extend_from_slice(&tail);
    let Ok(rebuilt) = Lineup::try_from(rebuilt) else {
        return LineupUpdate::RebuildMismatch;
    };
    entry.set_lineup(&rebuilt);
    repair_goalkeeper_positions(entry, squad, overrides, true);
    LineupUpdate::Updated
}

// ── bench order ──────────────────────────────────────────────────────────

/// Order roles 11+ as live substitutes, signings, then highest native overall
/// (when every remaining reserve is rated; otherwise the current order). A
/// second goalkeeper is guaranteed inside the matchday 18 when one is
/// registered. Starter roles are never touched. Returns whether the lineup
/// changed.
pub(crate) fn order_bench(
    entry: &mut GamePlanEntry,
    squad: &Squad,
    live_bench: &[u32],
    signings: &[u32],
) -> bool {
    let active_count = squad.roster_size();
    if active_count <= FIRST_TEAM_SLOT_COUNT {
        return false;
    }
    let mut lineup = entry.lineup();
    let bench: Vec<u8> = lineup[FIRST_TEAM_SLOT_COUNT..active_count].to_vec();
    let slot_by_player: HashMap<u32, usize> = (0..TP_MAX_PLAYERS)
        .filter(|&slot| squad.id_at(slot) != 0)
        .map(|slot| (squad.id_at(slot), slot))
        .collect();
    let bench_slots: HashSet<usize> = bench.iter().map(|&s| usize::from(s)).collect();

    let mut ordered: Vec<u8> = Vec::with_capacity(bench.len());
    for player_id in live_bench.iter().chain(signings) {
        if let Some(&slot) = slot_by_player.get(player_id)
            && bench_slots.contains(&slot)
            && !ordered.contains(&(slot as u8))
        {
            ordered.push(slot as u8);
        }
    }
    let mut remaining: Vec<u8> = bench
        .iter()
        .copied()
        .filter(|slot| !ordered.contains(slot))
        .collect();
    if squad.all_rated(remaining.iter().map(|&s| usize::from(s))) {
        remaining.sort_by_key(|&slot| -i64::from(squad.overall(usize::from(slot)).unwrap_or(0)));
    }
    ordered.extend(remaining);

    let matchday_bench = MATCHDAY_SQUAD_SLOT_COUNT - FIRST_TEAM_SLOT_COUNT;
    let is_goalkeeper =
        |slot: u8| squad.member(usize::from(slot)).and_then(|m| m.position) == Some(Position::Gk);
    let covered = &ordered[..matchday_bench.min(ordered.len())];
    if !covered.iter().any(|&slot| is_goalkeeper(slot))
        && let Some(at) = ordered
            .iter()
            .skip(matchday_bench)
            .position(|&slot| is_goalkeeper(slot))
    {
        let goalkeeper = ordered.remove(matchday_bench + at);
        ordered.insert(matchday_bench - 1, goalkeeper);
    }

    if ordered == bench {
        return false;
    }
    lineup[FIRST_TEAM_SLOT_COUNT..active_count].copy_from_slice(&ordered);
    entry.set_lineup(&lineup);
    true
}
