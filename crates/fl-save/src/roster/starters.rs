//! Aligning the starting XI with the formation roles.
//!
//! The position arrays of a game plan are formation slots: they decide where
//! a role renders on the Game Plan screen and are never rewritten from a
//! player's registered position. Only the lineup permutation changes. Roles
//! are filled by one optimal assignment. When every squad member has native
//! abilities, every active player competes and the score is the player's
//! rating at the role's position alone (a native B/A playable grade counts as
//! a line match); the current XI only breaks ties. Without full ratings the
//! score is role fit (exact position vs same line) plus incumbency.

use std::collections::HashMap;

use crate::edit_file::PreferredFoot;
use crate::layout::*;
use crate::model::Position;

use super::game_plan::GamePlanEntry;
use super::hungarian::max_weight_assignment;
use super::squad::{PositionOverrides, Squad};

/// With native ratings, starter scoring is this multiple of the player's
/// rating at the role's position, so the CB strong-foot bonus is worth about
/// 13 rating points when it permutes the chosen starters.
const ROLE_RATING_WEIGHT: i64 = 3;
const EXACT_FIT: i64 = 100;
const LINE_FIT: i64 = 55;
/// Native playable grade without a line match, and the save's own choice for
/// its incumbent: both only make the player eligible for the role.
const PLAYABLE_FIT: i64 = 55;
const INCUMBENT_FIT: i64 = 30;
/// Without ratings, a current starter keeps the XI over an equal fit.
const INCUMBENT_BONUS: i64 = 8;
const INCUMBENT_ROLE_BONUS: i64 = 12;
const STRONG_FOOT_BONUS: i64 = 40;
/// Weights are scaled so the summed tie-breaks of all 11 roles (incumbent
/// keeps its role, then current starter, then slot) stay below one score
/// point.
const SCORE_SCALE: i64 = 100_000;
const SLOT_RADIX: i64 = 41;

/// Everything the XI alignment ranks on.
struct Problem<'a> {
    squad: &'a Squad,
    /// Always empty: positions come from the save and Player.bin only.
    overrides: PositionOverrides,
    active_slots: Vec<usize>,
    starter_count: usize,
    /// Slot per role in the current lineup (the first `starter_count`).
    current_slots: Vec<usize>,
    /// Raw formation code per starter role.
    target_codes: Vec<u8>,
    /// The side a CB role prefers (formation-specific).
    cb_side: HashMap<usize, PreferredFoot>,
    use_ratings: bool,
    candidate_slots: Vec<usize>,
}

impl Problem<'_> {
    fn current_role(&self, slot: usize) -> Option<usize> {
        // A repeated slot keeps its last role.
        self.current_slots.iter().rposition(|&s| s == slot)
    }

    fn player_code(&self, slot: usize) -> Option<u8> {
        self.squad.position_code(slot, &self.overrides)
    }

    /// How well the player in `slot` fits the formation role, if at all.
    fn compatibility(&self, role: usize, slot: usize) -> Option<i64> {
        let player = Position::from_index(self.player_code(slot)?)?;
        let target = self.target_codes[role];
        let fit = line_compatibility(player, target);
        if fit.is_some() || !self.use_ratings || player == Position::Gk {
            return fit;
        }
        let abilities = self.squad.member(slot)?.abilities.as_ref()?;
        if let Some(target) = Position::from_index(target).filter(|&t| t != Position::Gk)
            && abilities.position_grades[usize::from(target.code())] > 0
        {
            // A native B/A playable position counts as a line match.
            return Some(PLAYABLE_FIT);
        }
        // The save's own choice stays valid for its incumbent.
        (self.current_role(slot) == Some(role)).then_some(INCUMBENT_FIT)
    }

    fn role_rating(&self, role: usize, slot: usize) -> i64 {
        if !self.use_ratings {
            return 0;
        }
        i64::from(
            self.squad
                .role_rating(slot, Some(self.target_codes[role]))
                .unwrap_or(0),
        )
    }

    fn score(&self, role: usize, slot: usize, foot: bool) -> Option<i64> {
        let fit = self.compatibility(role, slot)?;
        let mut value = if self.use_ratings {
            // Best at the role wins; fit only gates eligibility.
            ROLE_RATING_WEIGHT * self.role_rating(role, slot)
        } else {
            let current = self.current_role(slot);
            fit + if current.is_some() {
                INCUMBENT_BONUS
            } else {
                0
            } + if current == Some(role) {
                INCUMBENT_ROLE_BONUS
            } else {
                0
            }
        };
        if foot
            && let Some(&side) = self.cb_side.get(&role)
            && self.player_code(slot) == Some(Position::Cb.code())
            && self.squad.member(slot).and_then(|m| m.foot) == Some(side)
        {
            // Strong-foot alignment wins over incumbent tie-breaks, while
            // unknown or opposite-foot CBs remain valid fallbacks.
            value += STRONG_FOOT_BONUS;
        }
        Some(value)
    }

    /// Assignment weight: score first; ties keep an incumbent in its own role,
    /// then prefer current starters, then the lower slot.
    fn weight(&self, role: usize, slot: usize, foot: bool) -> Option<i64> {
        let score = self.score(role, slot, foot)?;
        let current_role = self.current_role(slot);
        let moved = i64::from(current_role != Some(role));
        let current = current_role.map_or(TP_MAX_PLAYERS as i64, |r| r as i64);
        let tie_break = (moved * SLOT_RADIX + current) * SLOT_RADIX + slot as i64;
        Some(score * SCORE_SCALE - tie_break)
    }

    /// Optimal slot per role among `slots`.
    fn assign(&self, roles: &[usize], slots: &[usize], foot: bool) -> Option<Vec<usize>> {
        let weights: Vec<Vec<Option<i64>>> = roles
            .iter()
            .map(|&role| {
                slots
                    .iter()
                    .map(|&slot| self.weight(role, slot, foot))
                    .collect()
            })
            .collect();
        let columns = max_weight_assignment(&weights)?;
        Some(columns.into_iter().map(|column| slots[column]).collect())
    }

    /// The keeper for role 0: when role 0 holds a known player, the
    /// best-rated registered keeper (the incumbent wins ties), else the
    /// incumbent.
    fn primary_slot(&self) -> usize {
        let incumbent = self.current_slots[0];
        if self.player_code(incumbent).is_none() {
            return incumbent;
        }
        let key = |slot: usize| {
            let rating = if self.use_ratings {
                self.squad
                    .role_rating(slot, Some(Position::Gk.code()))
                    .unwrap_or(0)
            } else {
                0
            };
            (rating, slot == incumbent, -(slot as i64))
        };
        self.active_slots
            .iter()
            .copied()
            .filter(|&slot| self.player_code(slot) == Some(Position::Gk.code()))
            .max_by_key(|&slot| key(slot))
            .unwrap_or(incumbent)
    }
}

/// Position fit of a player at a formation role. LWF/RWF are literal sides and
/// never interchangeable; other roles accept any player of the same line.
fn line_compatibility(player: Position, target: u8) -> Option<i64> {
    if target == player.code() {
        return Some(EXACT_FIT);
    }
    let code = player.code();
    match target {
        9 | 10 => None,
        1..=3 => (1..=3).contains(&code).then_some(LINE_FIT),
        4..=8 => (4..=8).contains(&code).then_some(LINE_FIT),
        11 | 12 => (9..=12).contains(&code).then_some(LINE_FIT),
        _ => None,
    }
}

/// CB role order is formation-specific. In a two-CB line the first slot
/// renders right and the second left; in a three-CB line the first slot is
/// central, then right, then left. Limited to CB slots, never LB/RB.
fn cb_sides(target_codes: &[u8]) -> HashMap<usize, PreferredFoot> {
    let cb_roles: Vec<usize> = target_codes
        .iter()
        .enumerate()
        .filter(|&(_, &code)| code == Position::Cb.code())
        .map(|(role, _)| role)
        .collect();
    let mut sides = HashMap::new();
    match cb_roles.len() {
        2 => {
            sides.insert(cb_roles[0], PreferredFoot::Right);
            sides.insert(cb_roles[1], PreferredFoot::Left);
        }
        3.. => {
            sides.insert(cb_roles[1], PreferredFoot::Right);
            sides.insert(cb_roles[cb_roles.len() - 1], PreferredFoot::Left);
        }
        _ => {}
    }
    sides
}

fn build_problem<'a>(entry: &GamePlanEntry, squad: &'a Squad) -> Option<Problem<'a>> {
    let active_slots = squad.active_slots();
    let starter_count = FIRST_TEAM_SLOT_COUNT.min(active_slots.len());
    if starter_count == 0 {
        return None;
    }
    let lineup = entry.lineup();
    let current_slots: Vec<usize> = lineup[..starter_count]
        .iter()
        .map(|&s| usize::from(s))
        .collect();
    let target_codes: Vec<u8> = (0..starter_count)
        .map(|role| entry.formation_code(role))
        .collect();
    let cb_side = cb_sides(&target_codes);

    let use_ratings = squad.all_rated(active_slots.iter().copied());
    let mut problem = Problem {
        squad,
        overrides: PositionOverrides::new(),
        active_slots,
        starter_count,
        current_slots,
        target_codes,
        cb_side,
        use_ratings,
        candidate_slots: Vec::new(),
    };
    problem.candidate_slots = candidates(&problem);
    Some(problem)
}

/// Who may start: the current XI first; with native ratings every active
/// player; otherwise exact formation-role players (so a winger, defender or
/// reserve keeper cannot occupy an unrelated slot) and then anyone to fill
/// the XI.
fn candidates(problem: &Problem<'_>) -> Vec<usize> {
    let mut slots: Vec<usize> = Vec::new();
    for &slot in &problem.current_slots {
        if !slots.contains(&slot) {
            slots.push(slot);
        }
    }
    if problem.use_ratings {
        let rest: Vec<usize> = problem
            .active_slots
            .iter()
            .copied()
            .filter(|slot| !slots.contains(slot))
            .collect();
        slots.extend(rest);
        return slots;
    }
    for &slot in &problem.active_slots {
        let exact = problem
            .player_code(slot)
            .is_some_and(|code| problem.target_codes.contains(&code));
        if !slots.contains(&slot) && exact {
            slots.push(slot);
        }
    }
    for &slot in &problem.active_slots {
        if slots.len() >= problem.starter_count {
            break;
        }
        if !slots.contains(&slot) {
            slots.push(slot);
        }
    }
    slots
}

/// Align the XI with the existing formation roles. Returns whether the lineup
/// changed. Nothing is written when no complete safe assignment exists.
pub(crate) fn repair_starter_assignments(entry: &mut GamePlanEntry, squad: &Squad) -> bool {
    let Some(problem) = build_problem(entry, squad) else {
        return false;
    };
    let starter_count = problem.starter_count;
    let mut lineup = entry.lineup();
    let primary = problem.primary_slot();

    let mut assigned: HashMap<usize, usize> = HashMap::from([(0, primary)]);
    let mut used: Vec<usize> = vec![primary];

    // Reserve roles with no safe candidate: other roles must not steal their
    // incumbent and force a cross-line or opposite-wing fallback.
    for role in 1..starter_count {
        let incumbent = problem.current_slots[role];
        if used.contains(&incumbent)
            || problem
                .candidate_slots
                .iter()
                .any(|&slot| problem.compatibility(role, slot).is_some())
        {
            continue;
        }
        assigned.insert(role, incumbent);
        used.push(incumbent);
    }

    let open_roles: Vec<usize> = (1..starter_count)
        .filter(|role| !assigned.contains_key(role))
        .collect();
    let available: Vec<usize> = problem
        .candidate_slots
        .iter()
        .copied()
        .filter(|slot| !used.contains(slot))
        .collect();
    if !open_roles.is_empty() {
        // Maximise the total score over every role at once; a per-role greedy
        // pick can spend the best player on the wrong role.
        let matched = if problem.use_ratings {
            // Ratings pick who starts; CB strong-foot sides then only permute
            // the chosen starters and never pull in a worse CB.
            problem
                .assign(&open_roles, &available, false)
                .and_then(|chosen| problem.assign(&open_roles, &chosen, true))
        } else {
            problem.assign(&open_roles, &available, true)
        };
        let Some(matched) = matched else {
            return false;
        };
        for (&role, &slot) in open_roles.iter().zip(&matched) {
            assigned.insert(role, slot);
            used.push(slot);
        }
    }
    if assigned.len() != starter_count {
        return false;
    }

    let active_count = problem.active_slots.len();
    let mut repaired: Vec<usize> = (0..starter_count).map(|role| assigned[&role]).collect();
    let mut remaining: Vec<usize> = lineup[..active_count]
        .iter()
        .map(|&s| usize::from(s))
        .filter(|slot| !used.contains(slot))
        .collect();
    let extra: Vec<usize> = problem
        .active_slots
        .iter()
        .copied()
        .filter(|slot| !used.contains(slot) && !remaining.contains(slot))
        .collect();
    remaining.extend(extra);
    repaired.extend(remaining);
    if repaired.len() != active_count {
        return false;
    }
    let repaired: Vec<u8> = repaired.into_iter().map(|slot| slot as u8).collect();
    if lineup[..active_count] == repaired[..] {
        return false;
    }
    lineup[..active_count].copy_from_slice(&repaired);
    entry.set_lineup(&lineup);
    true
}
