//! Formations, tactics and captains from live data, resolved to save clubs
//! and players (fail-closed), and the clubs whose lineup gets re-picked.

use std::collections::{HashMap, HashSet};

use fl_save::roster::formation::normalize_game_plan_formation;
use fl_sources::model::{CaptainUpdate, SquadSnapshot, TacticalUpdate};

use super::identity::positive;
use super::ports::{PlayerMatcher, PlayerQuery};
use super::types::RosterMap;

const CAPTAIN_NAME_CONFIDENCE: f64 = 90.0;

fn non_empty(text: &str) -> &str {
    text
}

/// Map verified current match shapes to represented save clubs, in the order
/// clubs first appear. Conflicting observations for a club drop it.
pub fn plan_gameplan_formations(
    snapshots: &[SquadSnapshot],
    club_ids: &HashSet<u32>,
    fotmob_team_map: &HashMap<i64, u32>,
) -> Vec<(u32, String)> {
    let mut planned: Vec<(u32, String)> = Vec::new();
    let mut conflicted: HashSet<u32> = HashSet::new();
    for snapshot in snapshots.iter().filter(|snapshot| snapshot.complete) {
        let Some(formation) = snapshot
            .formation
            .as_deref()
            .and_then(normalize_game_plan_formation)
        else {
            continue;
        };
        let Some(&team_id) = fotmob_team_map.get(&snapshot.team_id_fotmob) else {
            continue;
        };
        if !club_ids.contains(&team_id) || conflicted.contains(&team_id) {
            continue;
        }
        match planned.iter().position(|(team, _)| *team == team_id) {
            Some(slot) if planned[slot].1 != formation => {
                tracing::warn!("Skipping conflicting formation observations for team {team_id}");
                planned.remove(slot);
                conflicted.insert(team_id);
            }
            Some(_) => {}
            None => planned.push((team_id, formation)),
        }
    }
    planned
}

/// Resolve FotMob tactical profiles to represented save clubs, failing
/// closed on conflicting profiles.
pub fn plan_gameplan_tactics(
    updates: &[TacticalUpdate],
    club_ids: &HashSet<u32>,
    fotmob_team_map: &HashMap<i64, u32>,
) -> Vec<(u32, Vec<(String, i32)>)> {
    let mut planned: Vec<(u32, Vec<(String, i32)>)> = Vec::new();
    let mut conflicted: HashSet<u32> = HashSet::new();
    for update in updates {
        let Some(&team_id) = fotmob_team_map.get(&update.team_id_fotmob) else {
            continue;
        };
        if !club_ids.contains(&team_id) || conflicted.contains(&team_id) {
            continue;
        }
        if update.settings.is_empty() {
            continue;
        }
        let mut settings = update.settings.clone();
        settings.sort();
        match planned.iter().position(|(team, _)| *team == team_id) {
            Some(slot) if planned[slot].1 != settings => {
                tracing::warn!("Skipping conflicting tactical profiles for team {team_id}");
                planned.remove(slot);
                conflicted.insert(team_id);
            }
            Some(_) => {}
            None => planned.push((team_id, settings)),
        }
    }
    planned
}

/// A captain marker resolved to a local team and player.
#[derive(Debug, Clone, PartialEq)]
pub struct PlannedCaptainUpdate {
    pub source: CaptainUpdate,
    pub team_id: u32,
    pub player_id: u32,
    pub matched_player_name: String,
    pub confidence: f64,
}

/// Resolve live captain markers to fail-closed local roster targets.
///
/// `team_player_map` is the roster state the captain will be set against
/// (after this run's roster changes).
pub fn plan_captain_updates(
    updates: &[CaptainUpdate],
    matcher: &dyn PlayerMatcher,
    team_player_map: &RosterMap,
    club_ids: &HashSet<u32>,
    fotmob_team_map: &HashMap<i64, u32>,
    threshold: f64,
    fotmob_player_ids: &HashMap<i64, u32>,
) -> Vec<PlannedCaptainUpdate> {
    // team → marker; a conflicting second marker removes the team's entry
    // (a third one starts over), in first-seen order.
    let mut by_team: Vec<(u32, &CaptainUpdate)> = Vec::new();
    for source in updates {
        let team_id = fotmob_team_map
            .get(&source.team_id_fotmob)
            .copied()
            .filter(|team| club_ids.contains(team));
        let Some(team_id) = team_id else {
            tracing::warn!(
                "Skipping captain for {} ({}): club identity is not represented",
                if source.club_name.is_empty() {
                    source.team_id_fotmob.to_string()
                } else {
                    source.club_name.clone()
                },
                source.team_id_fotmob
            );
            continue;
        };
        match by_team.iter().position(|(team, _)| *team == team_id) {
            Some(slot) if by_team[slot].1.player_id_fotmob != source.player_id_fotmob => {
                tracing::warn!(
                    "Skipping conflicting captain markers for team {team_id}: {} vs {}",
                    by_team[slot].1.player_name,
                    source.player_name
                );
                by_team.remove(slot);
            }
            Some(slot) => by_team[slot].1 = source,
            None => by_team.push((team_id, source)),
        }
    }

    let mut planned = Vec::new();
    for (team_id, source) in by_team {
        let roster_ids: HashSet<u32> = team_player_map
            .get(&team_id)
            .into_iter()
            .flatten()
            .copied()
            .collect();
        let identity = positive(source.player_id_fotmob)
            .and_then(|id| fotmob_player_ids.get(&id).copied())
            .filter(|id| roster_ids.contains(id));
        let (player_id, name, confidence) = match identity {
            Some(identity) => (Some(identity), source.player_name.clone(), 100.0),
            None => {
                let found = matcher.match_player(
                    &source.player_name,
                    &PlayerQuery {
                        threshold: threshold.max(CAPTAIN_NAME_CONFIDENCE),
                        to_team_id: Some(team_id),
                        team_player_map: Some(team_player_map),
                        nationality: non_empty(&source.nationality),
                        age: source.age,
                        ..PlayerQuery::default()
                    },
                );
                (found.id, found.name, found.confidence)
            }
        };
        let Some(player_id) = player_id else {
            tracing::warn!(
                "Skipping captain for {}: could not safely match {}",
                if source.club_name.is_empty() {
                    team_id.to_string()
                } else {
                    source.club_name.clone()
                },
                source.player_name
            );
            continue;
        };
        planned.push(PlannedCaptainUpdate {
            source: source.clone(),
            team_id,
            player_id,
            matched_player_name: if name.is_empty() {
                source.player_name.clone()
            } else {
                name
            },
            confidence,
        });
    }
    planned
}

/// Save clubs with a complete live squad snapshot: their XI and bench are
/// re-picked by native rating (never from a match lineup).
pub fn plan_lineup_clubs(
    snapshots: &[SquadSnapshot],
    club_ids: &HashSet<u32>,
    fotmob_team_map: &HashMap<i64, u32>,
) -> HashSet<u32> {
    snapshots
        .iter()
        .filter(|snapshot| snapshot.complete)
        .filter_map(|snapshot| fotmob_team_map.get(&snapshot.team_id_fotmob).copied())
        .filter(|team_id| club_ids.contains(team_id))
        .collect()
}
