//! Choosing the player a full club releases for an incoming signing.

use std::collections::HashSet;

use crate::edit_file::EditFile;
use crate::layout::*;
use crate::model::Position;
use crate::release_policy::PlayerUsage;

use super::{MIN_GOALKEEPERS, RESERVED_PLAYER_ID_MIN};

/// Inputs of an overflow search beyond the team.
#[derive(Debug, Clone, Copy, Default)]
pub struct OverflowQuery<'a> {
    /// The incoming player: never a candidate.
    pub exclude_player_id: Option<u32>,
    /// A virtual roster (40 slots, 0 = empty) instead of the saved one.
    pub roster_player_ids: Option<&'a [u32]>,
    /// Extra players that must not be released.
    pub protected_player_ids: Option<&'a HashSet<u32>>,
}

/// The selected release candidate.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct OverflowCandidate {
    /// Persisted roster slot.
    pub slot: usize,
    pub player_id: u32,
}

/// Where a candidate sits in the game plan.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RoleGroup {
    FirstTeam,
    MatchdayBench,
    Reserve,
}

impl RoleGroup {
    fn of(role: usize) -> Self {
        if role < FIRST_TEAM_SLOT_COUNT {
            Self::FirstTeam
        } else if role < MATCHDAY_SQUAD_SLOT_COUNT {
            Self::MatchdayBench
        } else {
            Self::Reserve
        }
    }

    pub const fn name(self) -> &'static str {
        match self {
            Self::FirstTeam => "first_team",
            Self::MatchdayBench => "matchday_bench",
            Self::Reserve => "reserve",
        }
    }
}

/// Why a player was selected for release.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OverflowExplanation {
    pub team_id: u32,
    pub slot: usize,
    pub role: usize,
    pub role_group: RoleGroup,
    pub player_id: u32,
    pub name: String,
    pub is_reserved: bool,
    pub is_goalkeeper: bool,
    pub usage: Option<PlayerUsage>,
    pub overall: Option<i32>,
    /// `lowest_overall`, `lowest_known_usage` and `deepest_role` as applicable,
    /// joined by `_then_`.
    pub selection_basis: String,
}

/// A ranked candidate with the facts its rank was built from.
struct Ranked {
    slot: usize,
    player_id: u32,
    role: usize,
    is_goalkeeper: bool,
    overall: Option<i32>,
    usage: Option<PlayerUsage>,
}

impl EditFile {
    /// Find the safest player to release when a roster is full.
    ///
    /// 1. The incoming player, players transferred in this session, and
    ///    explicitly protected players (policy and `protected_player_ids`) are
    ///    outside the pool. If that leaves nobody, only the session's
    ///    transferred players come back into the pool: explicit protections
    ///    are never overridden.
    /// 2. The first-team roles and then the matchday bench are protected.
    /// 3. At least [`MIN_GOALKEEPERS`] goalkeepers stay when position metadata
    ///    identifies them (slot 0 counts as the historical keeper).
    /// 4. A native reserve is preferred over a high/reserved-id player.
    /// 5. Within a role tier the lowest native overall goes first (unknown
    ///    overalls after every known one); then known low usage, then the
    ///    deepest role.
    ///
    /// The game-plan order is used when it is a valid permutation of the
    /// roster slots; otherwise the persisted roster order. `None` when no
    /// player may be released.
    pub fn find_overflow_release_candidate(
        &self,
        team_id: u32,
        query: OverflowQuery<'_>,
    ) -> Option<OverflowCandidate> {
        self.rank_overflow(team_id, query)
            .map(|ranked| OverflowCandidate {
                slot: ranked.slot,
                player_id: ranked.player_id,
            })
    }

    /// Explain why `player_id` would be released from `team_id`; `None` when
    /// it is not the selected candidate.
    pub fn describe_overflow_release_candidate(
        &self,
        team_id: u32,
        player_id: u32,
        query: OverflowQuery<'_>,
    ) -> Option<OverflowExplanation> {
        let query = OverflowQuery {
            exclude_player_id: None,
            ..query
        };
        let ranked = self.rank_overflow(team_id, query)?;
        if ranked.player_id != player_id {
            return None;
        }
        let info = self.player_metadata(player_id);
        Some(OverflowExplanation {
            team_id,
            slot: ranked.slot,
            role: ranked.role,
            role_group: RoleGroup::of(ranked.role),
            player_id,
            name: info.map(|info| info.name).unwrap_or_default(),
            is_reserved: player_id >= RESERVED_PLAYER_ID_MIN,
            is_goalkeeper: ranked.is_goalkeeper,
            usage: ranked.usage,
            overall: ranked.overall,
            selection_basis: [
                ranked.overall.map(|_| "lowest_overall"),
                ranked.usage.map(|_| "lowest_known_usage"),
                Some("deepest_role"),
            ]
            .into_iter()
            .flatten()
            .collect::<Vec<_>>()
            .join("_then_"),
        })
    }

    /// Game-plan role per persisted roster slot: the lineup order when its
    /// active prefix is a valid permutation of the occupied slots, else the
    /// slot itself.
    fn overflow_roles(&self, team_id: u32, player_ids: &[u32]) -> Vec<usize> {
        let mut roles: Vec<usize> = (0..player_ids.len()).collect();
        let Some(plan) = self.read_game_plan(team_id) else {
            return roles;
        };
        let active: Vec<usize> = (0..player_ids.len())
            .filter(|&s| player_ids[s] != 0)
            .collect();
        let lineup = plan.lineup();
        let Some(prefix) = lineup.get(..active.len()) else {
            return roles;
        };
        let mut sorted: Vec<usize> = prefix.iter().map(|&s| usize::from(s)).collect();
        sorted.sort_unstable();
        if sorted.windows(2).any(|w| w[0] == w[1]) || sorted != active {
            return roles;
        }
        for (role, &slot) in prefix.iter().enumerate() {
            roles[usize::from(slot)] = role;
        }
        roles
    }

    fn rank_overflow(&self, team_id: u32, query: OverflowQuery<'_>) -> Option<Ranked> {
        let saved;
        let player_ids: &[u32] = match query.roster_player_ids {
            Some(ids) => ids,
            None => {
                saved = self.roster(team_id)?;
                &saved.player_ids
            }
        };

        let policy = self.release_protected(team_id);
        let explicit = |id: u32| {
            policy.is_some_and(|set| set.contains(&id))
                || query
                    .protected_player_ids
                    .is_some_and(|set| set.contains(&id))
        };
        let mut pool: Vec<(usize, u32)> = player_ids
            .iter()
            .copied()
            .enumerate()
            .filter(|&(_, id)| {
                id != 0
                    && Some(id) != query.exclude_player_id
                    && !explicit(id)
                    && !self.mutation.transferred_player_ids.contains(&id)
            })
            .collect();
        if pool.is_empty() {
            // Everyone left was signed in this session: they may be released,
            // but never the incoming player or an explicitly protected one.
            pool = player_ids
                .iter()
                .copied()
                .enumerate()
                .filter(|&(_, id)| id != 0 && Some(id) != query.exclude_player_id && !explicit(id))
                .collect();
        }
        if pool.is_empty() {
            return None;
        }

        let roles = self.overflow_roles(team_id, player_ids);
        // Slot zero is the historical PES fallback for the starting keeper.
        let is_goalkeeper =
            |slot: usize, id: u32| slot == 0 || self.player_position(id) == Some(Position::Gk);
        let total_goalkeepers = pool
            .iter()
            .filter(|&&(slot, id)| is_goalkeeper(slot, id))
            .count();

        pool.into_iter()
            .map(|(slot, player_id)| {
                let role = roles[slot];
                Ranked {
                    slot,
                    player_id,
                    role,
                    is_goalkeeper: is_goalkeeper(slot, player_id),
                    overall: self.abilities(player_id).map(|a| a.overall()),
                    usage: self.release_usage(player_id).copied(),
                }
            })
            .min_by_key(|ranked| {
                let tier = if ranked.role < FIRST_TEAM_SLOT_COUNT {
                    3
                } else if ranked.role < MATCHDAY_SQUAD_SLOT_COUNT
                    || (ranked.is_goalkeeper && total_goalkeepers <= MIN_GOALKEEPERS)
                {
                    2
                } else if ranked.player_id >= RESERVED_PLAYER_ID_MIN {
                    // High/reserved ids are preserved over native deep
                    // reserves, but stay releasable if no native one exists.
                    1
                } else {
                    0
                };
                let overall_key = ranked.overall.map_or((1, 0), |overall| (0, overall));
                let usage_key = ranked.usage.map_or((1, 0, 0, 0, 0), |usage| {
                    (
                        0,
                        usage.minutes,
                        usage.starts,
                        usage.appearances,
                        usage.news_mentions,
                    )
                });
                (
                    tier,
                    overall_key,
                    usage_key,
                    -(ranked.role as i64),
                    -(ranked.slot as i64),
                )
            })
    }
}
