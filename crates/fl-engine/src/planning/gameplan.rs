//! Formations, tactics, captains and lineup hints from live data, resolved to
//! save clubs and players (fail-closed).

use std::collections::{HashMap, HashSet};

use fl_save::model::Position;
use fl_save::roster::formation::normalize_game_plan_formation;
use fl_sources::model::{CaptainUpdate, SquadMember, SquadSnapshot, TacticalUpdate};

use super::identity::positive;
use super::ports::{PlayerMatcher, PlayerQuery};
use super::types::RosterMap;

/// A lineup member must match at least this well by name alone.
const LINEUP_NAME_CONFIDENCE: f64 = 85.0;
const CAPTAIN_NAME_CONFIDENCE: f64 = 90.0;

fn non_empty(text: &str) -> &str {
    text
}

fn game_plan_code(label: &str) -> Option<u8> {
    Position::from_label(label).map(Position::code)
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

/// Live matchday hints resolved to one roster state of the save.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct GameplanPreferences {
    pub starters: HashMap<u32, Vec<u32>>,
    pub bench: HashMap<u32, Vec<u32>>,
    /// Team → player → detailed matchday position label.
    pub position_overrides: HashMap<u32, HashMap<u32, String>>,
}

/// Resolve one lineup member to a player of `team_id`'s roster.
///
/// The FotMob identity resolved during matching wins; the guarded name match
/// (with age and nationality) is only a fallback.
fn resolve_lineup_member(
    member: &SquadMember,
    matcher: &dyn PlayerMatcher,
    team_id: u32,
    team_player_map: &RosterMap,
    fotmob_player_ids: &HashMap<i64, u32>,
    threshold: f64,
) -> Option<u32> {
    let roster = team_player_map.get(&team_id).map_or(&[][..], Vec::as_slice);
    if let Some(player_id) =
        positive(member.player_id_fotmob).and_then(|id| fotmob_player_ids.get(&id))
    {
        return roster.contains(player_id).then_some(*player_id);
    }
    let minimum = threshold.max(LINEUP_NAME_CONFIDENCE);
    let found = matcher.match_player(
        &member.player_name,
        &PlayerQuery {
            threshold: minimum,
            to_team_id: Some(team_id),
            team_player_map: Some(team_player_map),
            nationality: &member.nationality,
            age: member.age,
            ..PlayerQuery::default()
        },
    );
    let player_id = found.id?;
    (found.confidence >= minimum && roster.contains(&player_id)).then_some(player_id)
}

/// Resolve the live XI, bench and positions into local game-plan hints.
///
/// A starter's detailed matchday position overrides the registered position
/// only when the two map to different game-plan codes.
#[allow(clippy::too_many_arguments)]
pub fn plan_gameplan_preferences(
    snapshots: &[SquadSnapshot],
    matcher: &dyn PlayerMatcher,
    team_player_map: &RosterMap,
    club_ids: &HashSet<u32>,
    fotmob_team_map: &HashMap<i64, u32>,
    threshold: f64,
    fotmob_player_ids: &HashMap<i64, u32>,
    registered_position: &dyn Fn(u32) -> Option<Position>,
) -> GameplanPreferences {
    let mut preferences = GameplanPreferences::default();
    for snapshot in snapshots.iter().filter(|snapshot| snapshot.complete) {
        let Some(&team_id) = fotmob_team_map.get(&snapshot.team_id_fotmob) else {
            continue;
        };
        if !club_ids.contains(&team_id) {
            continue;
        }
        let resolve = |member: &SquadMember| {
            resolve_lineup_member(
                member,
                matcher,
                team_id,
                team_player_map,
                fotmob_player_ids,
                threshold,
            )
        };

        let mut starter_ids: Vec<u32> = Vec::new();
        let mut overrides: HashMap<u32, String> = HashMap::new();
        for member in &snapshot.starter_members {
            let Some(player_id) = resolve(member) else {
                continue;
            };
            if starter_ids.contains(&player_id) {
                continue;
            }
            starter_ids.push(player_id);
            let Some(live_code) = game_plan_code(&member.position) else {
                continue;
            };
            let registered = registered_position(player_id).map(Position::code);
            if registered != Some(live_code) {
                overrides.insert(player_id, member.position.clone());
            }
        }

        let mut bench_ids: Vec<u32> = Vec::new();
        for member in &snapshot.sub_members {
            let Some(player_id) = resolve(member) else {
                continue;
            };
            if starter_ids.contains(&player_id) || bench_ids.contains(&player_id) {
                continue;
            }
            bench_ids.push(player_id);
        }

        // Keep a key even when no XI identity resolved: the team is still
        // aligned to its current formation roles.
        preferences.starters.insert(team_id, starter_ids);
        preferences.bench.insert(team_id, bench_ids);
        if overrides.is_empty() {
            preferences.position_overrides.remove(&team_id);
        } else {
            preferences.position_overrides.insert(team_id, overrides);
        }
    }
    preferences
}
