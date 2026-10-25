//! Shirt-number observations: dedupe per player and club, and batch
//! classification so planned swaps can be applied together.

use std::collections::{BTreeSet, HashMap, HashSet};

use super::ports::RosterReader;
use super::types::{MatchedTransfer, PlannedRosterAction, RosterAction, Side};

/// Two observations for one player at one club whose confidences differ by
/// less than this are ambiguous when their numbers disagree.
pub const SHIRT_CONFIDENCE_GAP: f64 = 3.0;

/// Highest shirt number the game accepts.
pub const MAX_SHIRT_NUMBER: u32 = 999;

/// A shirt observation the dedupe pass discarded.
#[derive(Debug, Clone, PartialEq)]
pub struct DroppedShirt {
    pub matched: MatchedTransfer,
    /// `shirt_number_ambiguous` (the whole group was dropped) or
    /// `shirt_number_outvoted` (a stronger observation disagreed).
    pub reason: &'static str,
    pub detail: String,
}

#[derive(Debug, Clone, Default)]
pub struct ShirtDedupe {
    /// Non-shirt events in input order, then one winner per player and club.
    pub kept: Vec<MatchedTransfer>,
    /// Observations dropped with a *different* number than the winner (or
    /// every member of an ambiguous group). Same-number duplicates are
    /// harmless and only counted.
    pub dropped: Vec<DroppedShirt>,
    /// Every discarded observation, harmless duplicates included.
    pub discarded: usize,
}

/// Keep one fail-closed shirt-number observation per player and club.
pub fn dedupe_shirt_number_matches(matches: Vec<MatchedTransfer>) -> ShirtDedupe {
    let mut result = ShirtDedupe::default();
    let mut group_order: Vec<(u32, Side)> = Vec::new();
    let mut groups: HashMap<(u32, Side), Vec<MatchedTransfer>> = HashMap::new();

    for item in matches {
        match (item.is_shirt_update(), item.player_id, item.to) {
            (true, Some(player_id), to) if !to.is_outside() => {
                let key = (player_id, to);
                if !groups.contains_key(&key) {
                    group_order.push(key);
                }
                groups.entry(key).or_default().push(item);
            }
            _ => result.kept.push(item),
        }
    }

    for key in group_order {
        let mut ranked = groups.remove(&key).expect("group exists");
        let group_len = ranked.len();
        // Stable: equal confidences keep input order.
        ranked.sort_by(|a, b| b.min_confidence().total_cmp(&a.min_confidence()));
        let winner_number = ranked[0].transfer.shirt_number;
        let winner_confidence = ranked[0].min_confidence();
        let conflicting_confidence = ranked[1..]
            .iter()
            .find(|item| item.transfer.shirt_number != winner_number)
            .map(MatchedTransfer::min_confidence);
        if let Some(conflicting) = conflicting_confidence
            && winner_confidence - conflicting < SHIRT_CONFIDENCE_GAP
        {
            result.discarded += group_len;
            let detail = format!(
                "{group_len} conflicting shirt-number observations for this player at this club; none trusted"
            );
            result
                .dropped
                .extend(ranked.into_iter().map(|matched| DroppedShirt {
                    matched,
                    reason: "shirt_number_ambiguous",
                    detail: detail.clone(),
                }));
            continue;
        }
        result.discarded += group_len - 1;
        let mut ranked = ranked.into_iter();
        let winner = ranked.next().expect("non-empty group");
        for loser in ranked {
            if loser.transfer.shirt_number != winner_number {
                result.dropped.push(DroppedShirt {
                    matched: loser,
                    reason: "shirt_number_outvoted",
                    detail: format!(
                        "a stronger observation gives shirt {}",
                        winner_number.map_or_else(|| "none".to_owned(), |n| n.to_string())
                    ),
                });
            }
        }
        result.kept.push(winner);
    }
    result
}

/// Outcome of classifying one shirt update.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct ShirtStatus {
    /// Shirt number the player wears now.
    pub previous: Option<u32>,
    /// Another player already wearing the requested number.
    pub conflict_player: Option<u32>,
    /// Reason code; empty when the update may be applied.
    pub reason: String,
}

impl ShirtStatus {
    /// The update may be applied.
    pub fn is_clear(&self) -> bool {
        self.conflict_player.is_none() && self.reason.is_empty()
    }
}

/// `(player ids, shirt numbers)` per roster slot.
type Slots = (Vec<u32>, Vec<u32>);

/// Classify shirt updates so planned number swaps can be applied together.
///
/// `actions` are the plan's actions (non-shirt ones are ignored); the result
/// is aligned with them. `arriving` holds `(team, player)` pairs not yet on
/// the roster that an earlier planned move/add will register; any other
/// non-member is skipped.
pub fn plan_shirt_batch(
    reader: &dyn RosterReader,
    actions: &[&PlannedRosterAction],
    arriving: &HashSet<(u32, u32)>,
) -> Vec<Option<ShirtStatus>> {
    let mut statuses: Vec<Option<ShirtStatus>> = vec![None; actions.len()];
    let mut team_order: Vec<u32> = Vec::new();
    let mut grouped: HashMap<u32, Vec<usize>> = HashMap::new();
    let mut rosters: HashMap<u32, Option<Slots>> = HashMap::new();

    for (index, item) in actions.iter().enumerate() {
        if item.action != RosterAction::ShirtUpdate {
            continue;
        }
        let team = item.matched.to.team();
        let player = item.matched.player_id;
        let previous = match (team, player) {
            (Some(team), Some(player)) => {
                let roster = rosters.entry(team).or_insert_with(|| reader.roster(team));
                roster.as_ref().and_then(|(ids, numbers)| {
                    ids.iter()
                        .position(|&id| id == player)
                        .and_then(|slot| numbers.get(slot).copied())
                })
            }
            _ => None,
        };
        statuses[index] = Some(ShirtStatus {
            previous,
            ..ShirtStatus::default()
        });
        if let Some(team) = team {
            if !grouped.contains_key(&team) {
                team_order.push(team);
            }
            grouped.entry(team).or_default().push(index);
        }
    }

    for team in team_order {
        let group = &grouped[&team];
        let roster = rosters.get(&team).and_then(Option::as_ref);
        let members: HashSet<u32> =
            roster.map_or_else(HashSet::new, |(ids, _)| ids.iter().copied().collect());
        let mut candidates: Vec<usize> = Vec::new();
        let mut by_target: HashMap<u32, Vec<usize>> = HashMap::new();
        for &index in group {
            let matched = &actions[index].matched;
            let status = statuses[index].as_mut().expect("shirt action has a status");
            let previous = status.previous;
            let Some(target) = matched.transfer.shirt_number else {
                continue;
            };
            if previous == Some(target) {
                continue;
            }
            let player = matched.player_id.unwrap_or(0);
            if !members.contains(&player) && !arriving.contains(&(team, player)) {
                status.reason = "player_not_on_team".to_owned();
                continue;
            }
            if !(1..=MAX_SHIRT_NUMBER).contains(&target) {
                status.reason = "invalid_shirt_number".to_owned();
                continue;
            }
            candidates.push(index);
            by_target.entry(target).or_default().push(index);
        }

        let duplicates: HashSet<usize> = by_target
            .values()
            .filter(|same| same.len() > 1)
            .flatten()
            .copied()
            .collect();
        for &index in &duplicates {
            let requested = actions[index]
                .matched
                .transfer
                .shirt_number
                .expect("candidate has a number");
            statuses[index].as_mut().expect("status").reason =
                format!("duplicate_shirt_number:{requested}");
        }

        let mut occupants: HashMap<u32, BTreeSet<u32>> = HashMap::new();
        if let Some((ids, numbers)) = roster {
            for (&player, &number) in ids.iter().zip(numbers) {
                if player != 0 {
                    occupants.entry(number).or_default().insert(player);
                }
            }
        }

        let mut survivors: HashSet<usize> = candidates
            .iter()
            .copied()
            .filter(|index| !duplicates.contains(index))
            .collect();
        loop {
            let survivor_players: HashSet<u32> = candidates
                .iter()
                .filter(|index| survivors.contains(index))
                .filter_map(|&index| actions[index].matched.player_id)
                .collect();
            let blocked: Vec<usize> = candidates
                .iter()
                .copied()
                .filter(|index| survivors.contains(index))
                .filter(|&index| {
                    let matched = &actions[index].matched;
                    let target = matched
                        .transfer
                        .shirt_number
                        .expect("candidate has a number");
                    let player = matched.player_id;
                    let conflicting: Vec<u32> = occupants
                        .get(&target)
                        .into_iter()
                        .flatten()
                        .copied()
                        .filter(|&other| Some(other) != player)
                        .collect();
                    !conflicting.is_empty()
                        && !conflicting.iter().all(|c| survivor_players.contains(c))
                })
                .collect();
            if blocked.is_empty() {
                break;
            }
            for index in blocked {
                survivors.remove(&index);
            }
        }

        for &index in &candidates {
            if duplicates.contains(&index) || survivors.contains(&index) {
                continue;
            }
            let matched = &actions[index].matched;
            let target = matched
                .transfer
                .shirt_number
                .expect("candidate has a number");
            let conflict_player = occupants
                .get(&target)
                .into_iter()
                .flatten()
                .copied()
                .find(|&other| Some(other) != matched.player_id);
            let status = statuses[index].as_mut().expect("status");
            status.conflict_player = conflict_player;
            status.reason = match conflict_player {
                Some(player) => format!("shirt_number_conflict:{player}"),
                None => format!("shirt_number_dependency:{target}"),
            };
        }
    }
    statuses
}
