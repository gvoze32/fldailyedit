//! The one virtual-roster simulator.
//!
//! Matching and roster-action planning both replay events against a copy of
//! the save's rosters. This type is the single owner of "who is registered
//! where". The two modes differ
//! only in how a leaving player's slot is treated:
//!
//! * [`SlotMode::Compact`] (matching): rosters are lists of present players;
//!   leaving shifts the list, joining appends.
//! * [`SlotMode::Slots`] (action planning): rosters are fixed 40-slot arrays;
//!   leaving leaves a hole (0) so the overflow ranking, which reads game-plan
//!   roles from the unmodified live file, keeps every remaining slot; joining
//!   fills the first hole.

use std::collections::{HashMap, HashSet};

use super::types::RosterMap;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SlotMode {
    Compact,
    Slots,
}

#[derive(Debug, Clone)]
pub struct RosterSim {
    mode: SlotMode,
    rosters: RosterMap,
    /// Teams in first-seen order; defines the order of [`Self::clubs_of`].
    rank: HashMap<u32, usize>,
    /// Teams whose membership is indexed (the save's clubs).
    tracked: HashSet<u32>,
    /// Player → tracked teams currently holding him, in rank order.
    held_by: HashMap<u32, Vec<u32>>,
}

impl RosterSim {
    /// Build from `(team, roster)` pairs in table order. `tracked` are the
    /// clubs whose membership [`Self::clubs_of`] reports; other teams are only
    /// carried for context.
    pub fn new(
        mode: SlotMode,
        teams: impl IntoIterator<Item = (u32, Vec<u32>)>,
        tracked: &HashSet<u32>,
    ) -> Self {
        let mut sim = Self {
            mode,
            rosters: HashMap::new(),
            rank: HashMap::new(),
            tracked: tracked.clone(),
            held_by: HashMap::new(),
        };
        for (team_id, roster) in teams {
            let next = sim.rank.len();
            sim.rank.entry(team_id).or_insert(next);
            sim.rosters.insert(team_id, roster);
        }
        sim.reindex();
        sim
    }

    fn reindex(&mut self) {
        self.held_by.clear();
        let mut teams: Vec<(usize, u32)> = self.rank.iter().map(|(&t, &r)| (r, t)).collect();
        teams.sort_unstable();
        for (_, team_id) in teams {
            if !self.tracked.contains(&team_id) {
                continue;
            }
            let mut seen = HashSet::new();
            for &player_id in &self.rosters[&team_id] {
                if player_id != 0 && seen.insert(player_id) {
                    self.held_by.entry(player_id).or_default().push(team_id);
                }
            }
        }
    }

    pub fn mode(&self) -> SlotMode {
        self.mode
    }

    /// Roster lists as the matcher reads them.
    pub fn map(&self) -> &RosterMap {
        &self.rosters
    }

    pub fn roster(&self, team_id: u32) -> Option<&[u32]> {
        self.rosters.get(&team_id).map(Vec::as_slice)
    }

    pub fn has_team(&self, team_id: u32) -> bool {
        self.rosters.contains_key(&team_id)
    }

    pub fn contains(&self, team_id: u32, player_id: u32) -> bool {
        player_id != 0
            && self
                .rosters
                .get(&team_id)
                .is_some_and(|r| r.contains(&player_id))
    }

    /// Tracked clubs currently holding the player, in table order.
    pub fn clubs_of(&self, player_id: u32) -> &[u32] {
        self.held_by.get(&player_id).map_or(&[], Vec::as_slice)
    }

    /// Present players of a team.
    pub fn occupied(&self, team_id: u32) -> usize {
        self.rosters
            .get(&team_id)
            .map_or(0, |r| r.iter().filter(|&&p| p != 0).count())
    }

    pub fn has_hole(&self, team_id: u32) -> bool {
        self.rosters.get(&team_id).is_some_and(|r| r.contains(&0))
    }

    /// Remove the first occurrence of `player_id` from `team_id`. No-op when
    /// he is not there.
    pub fn remove(&mut self, team_id: u32, player_id: u32) {
        let Some(roster) = self.rosters.get_mut(&team_id) else {
            return;
        };
        let Some(index) = roster.iter().position(|&p| p == player_id) else {
            return;
        };
        match self.mode {
            SlotMode::Compact => {
                roster.remove(index);
            }
            SlotMode::Slots => roster[index] = 0,
        }
        if !roster.contains(&player_id)
            && let Some(teams) = self.held_by.get_mut(&player_id)
        {
            teams.retain(|&t| t != team_id);
        }
    }

    /// Register the player at `team_id` (creating an empty roster for an
    /// unknown team in compact mode). In slot mode the caller guarantees a
    /// hole; without one the call does nothing.
    pub fn add(&mut self, team_id: u32, player_id: u32) {
        if !self.rank.contains_key(&team_id) {
            let next = self.rank.len();
            self.rank.insert(team_id, next);
        }
        let roster = self.rosters.entry(team_id).or_default();
        match self.mode {
            SlotMode::Compact => roster.push(player_id),
            SlotMode::Slots => match roster.iter().position(|&p| p == 0) {
                Some(hole) => roster[hole] = player_id,
                None => return,
            },
        }
        if self.tracked.contains(&team_id) {
            let teams = self.held_by.entry(player_id).or_default();
            if !teams.contains(&team_id) {
                teams.push(team_id);
                let rank = &self.rank;
                teams.sort_by_key(|t| rank[t]);
            }
        }
    }

    /// Move the player from `from` to `to`; joins `to` only when absent
    /// (matching never duplicates a registration).
    pub fn transfer(&mut self, from: u32, to: u32, player_id: u32) {
        self.remove(from, player_id);
        if !self.contains(to, player_id) {
            self.add(to, player_id);
        }
    }
}
