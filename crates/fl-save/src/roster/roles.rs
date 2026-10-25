//! Filling empty captain, set-piece and attack-player roles from save data.

use std::collections::HashMap;

use crate::edit_file::PreferredFoot;
use crate::layout::*;
use crate::model::Position;

use super::game_plan::{EMPTY, GamePlanEntry, GamePlanRole};
use super::squad::{PositionOverrides, Squad};

/// Positions that take corners: CMF, LMF, RMF, AMF, LWF, RWF.
const CORNER_TAKERS: [Position; 6] = [
    Position::Cmf,
    Position::Lmf,
    Position::Rmf,
    Position::Amf,
    Position::Lwf,
    Position::Rwf,
];

/// A starter: its role index and roster slot.
#[derive(Clone, Copy)]
struct Starter {
    role: usize,
    slot: usize,
}

/// Fill every empty (`0xFF`) role byte from the starting XI:
///
/// * captain: most caps, then age, then market value (Player.bin); skipped
///   when no starter has any such metadata;
/// * corners: CMF/LMF/RMF/AMF/LWF/RWF starters whose strong foot matches the
///   side, then same-side position, then market value;
/// * penalty: CF/SS, else AMF/wingers, else any outfield starter, by market
///   value;
/// * attack players: the most advanced starter roles by the main preset's
///   vertical coordinate.
///
/// Positions come from player metadata, falling back to the formation role
/// code. Returns the roles that were filled, in fill order.
pub(crate) fn assign_missing_roles(entry: &mut GamePlanEntry, squad: &Squad) -> Vec<GamePlanRole> {
    let starter_count = FIRST_TEAM_SLOT_COUNT.min(squad.roster_size());
    let lineup = entry.lineup();
    let starters: Vec<Starter> = lineup[..starter_count]
        .iter()
        .enumerate()
        .filter(|&(_, &slot)| squad.id_at(usize::from(slot)) != 0)
        .map(|(role, &slot)| Starter {
            role,
            slot: usize::from(slot),
        })
        .collect();
    if starters.is_empty() {
        return Vec::new();
    }

    let single_roles = [
        GamePlanRole::Captain,
        GamePlanRole::LeftCorner,
        GamePlanRole::RightCorner,
        GamePlanRole::Penalty,
    ];
    let missing: Vec<GamePlanRole> = single_roles
        .into_iter()
        .filter(|role| entry.role_byte(role.offset()) == EMPTY)
        .collect();
    let attack_missing: Vec<u8> = (1..=GP_ATTACK_PLAYER_COUNT as u8)
        .filter(|&n| entry.role_byte(GamePlanRole::AttackPlayer(n).offset()) == EMPTY)
        .collect();
    if missing.is_empty() && attack_missing.is_empty() {
        return Vec::new();
    }

    let no_overrides = PositionOverrides::new();
    let code_of = |starter: &Starter| -> u8 {
        squad
            .position_code(starter.slot, &no_overrides)
            .unwrap_or_else(|| entry.formation_code(starter.role))
    };
    let codes: HashMap<usize, u8> = starters.iter().map(|s| (s.slot, code_of(s))).collect();
    let role_of: HashMap<usize, usize> = starters.iter().map(|s| (s.slot, s.role)).collect();
    let outfield: Vec<usize> = starters
        .iter()
        .map(|s| s.slot)
        .filter(|slot| codes[slot] != Position::Gk.code())
        .collect();
    let market_value = |slot: usize| -> i64 {
        squad
            .member(slot)
            .and_then(|m| m.career)
            .map_or(0, |career| i64::from(career.market_value_eur))
    };

    let mut filled = Vec::new();
    let mut write = |entry: &mut GamePlanEntry, role: GamePlanRole, slot: usize| {
        entry.set_role_byte(role.offset(), slot as u8);
        filled.push(role);
    };

    if missing.contains(&GamePlanRole::Captain) {
        let profile = |slot: usize| -> (i64, i64, i64) {
            let member = squad.member(slot);
            let caps = member
                .and_then(|m| m.career)
                .map_or(0, |career| i64::from(career.caps));
            let age = member.map_or(0, |m| i64::from(m.age));
            (caps, age, market_value(slot))
        };
        let best = starters
            .iter()
            .map(|s| s.slot)
            .filter(|&slot| profile(slot) != (0, 0, 0))
            .max_by_key(|&slot| (profile(slot), -(role_of[&slot] as i64)));
        if let Some(slot) = best {
            write(entry, GamePlanRole::Captain, slot);
        }
    }

    let corner_codes: Vec<u8> = CORNER_TAKERS.iter().map(|p| p.code()).collect();
    for (role, side, near, far) in [
        (
            GamePlanRole::LeftCorner,
            PreferredFoot::Left,
            [Position::Lmf.code(), Position::Lwf.code()],
            [Position::Rmf.code(), Position::Rwf.code()],
        ),
        (
            GamePlanRole::RightCorner,
            PreferredFoot::Right,
            [Position::Rmf.code(), Position::Rwf.code()],
            [Position::Lmf.code(), Position::Lwf.code()],
        ),
    ] {
        if !missing.contains(&role) {
            continue;
        }
        let takers: Vec<usize> = outfield
            .iter()
            .copied()
            .filter(|slot| corner_codes.contains(&codes[slot]))
            .collect();
        let pool = if takers.is_empty() {
            &outfield
        } else {
            &takers
        };
        let best = pool.iter().copied().min_by_key(|&slot| {
            let foot = squad.member(slot).and_then(|m| m.foot);
            let side_rank = if near.contains(&codes[&slot]) {
                0
            } else if far.contains(&codes[&slot]) {
                2
            } else {
                1
            };
            (
                foot != Some(side),
                side_rank,
                -market_value(slot),
                role_of[&slot],
            )
        });
        if let Some(slot) = best {
            write(entry, role, slot);
        }
    }

    if missing.contains(&GamePlanRole::Penalty) {
        let in_codes = |codes_wanted: &[Position]| -> Vec<usize> {
            outfield
                .iter()
                .copied()
                .filter(|slot| codes_wanted.iter().any(|p| p.code() == codes[slot]))
                .collect()
        };
        let mut pool = in_codes(&[Position::Ss, Position::Cf]);
        if pool.is_empty() {
            pool = in_codes(&[Position::Amf, Position::Lwf, Position::Rwf]);
        }
        if pool.is_empty() {
            pool = outfield.clone();
        }
        let best = pool.iter().copied().min_by_key(|&slot| {
            (
                -market_value(slot),
                -i64::from(codes[&slot]),
                role_of[&slot],
            )
        });
        if let Some(slot) = best {
            write(entry, GamePlanRole::Penalty, slot);
        }
    }

    if !attack_missing.is_empty() {
        let assigned: Vec<u8> = (1..=GP_ATTACK_PLAYER_COUNT as u8)
            .map(|n| entry.role_byte(GamePlanRole::AttackPlayer(n).offset()))
            .collect();
        let mut advanced: Vec<usize> = outfield
            .iter()
            .copied()
            .filter(|&slot| !assigned.contains(&(slot as u8)))
            .collect();
        advanced.sort_by_key(|&slot| {
            (
                -i64::from(entry.formation_vertical(role_of[&slot])),
                -i64::from(codes[&slot]),
                role_of[&slot],
            )
        });
        for (&n, &slot) in attack_missing.iter().zip(&advanced) {
            write(entry, GamePlanRole::AttackPlayer(n), slot);
        }
    }
    filled
}
