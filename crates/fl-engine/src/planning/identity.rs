//! FotMob ↔ save player identity from complete live squad snapshots.

use std::collections::{BTreeSet, HashMap, HashSet};

use fl_sources::model::{SquadMember, SquadSnapshot};
use fl_text::fold_text;

use super::ports::{
    ClubIdentity, PlayerHit, PlayerMatcher, PlayerQuery, PlayerRecord, ScoreContext,
};
use super::teams::resolve_snapshot_club;
use super::types::RosterMap;

/// Current shirt number per player per team, positive numbers only.
pub type ShirtNumbers = HashMap<u32, HashMap<u32, u32>>;

/// Minimum members for a snapshot to count as a complete squad.
pub const MIN_COMPLETE_SQUAD_MEMBERS: usize = 11;

const MIN_SHORT_ALIAS_SCORE: f64 = 80.0;
/// A contextual winner must beat the runner-up by at least this much.
const CONTEXT_MARGIN: f64 = 3.0;

const ATTACKING_SHORT_ALIAS_POSITIONS: [&str; 10] = [
    "AMF", "CAM", "AM", "SS", "LWF", "RWF", "LW", "RW", "LMF", "RMF",
];

/// One sighting of a FotMob player inside a complete snapshot.
#[derive(Debug, Clone, Copy)]
pub struct Observation<'a> {
    pub fotmob_team_id: i64,
    pub club_name: &'a str,
    pub member: &'a SquadMember,
}

/// FotMob player id → sightings, in first-seen order (the order moves are
/// planned in).
#[derive(Debug, Default)]
pub struct IdentityMap<'a> {
    order: Vec<i64>,
    map: HashMap<i64, Vec<Observation<'a>>>,
}

impl<'a> IdentityMap<'a> {
    /// Complete snapshots only; players without a positive FotMob id are
    /// skipped.
    pub fn from_snapshots(snapshots: &'a [SquadSnapshot]) -> Self {
        let mut identity = Self::default();
        for snapshot in snapshots.iter().filter(|snapshot| snapshot.complete) {
            for member in &snapshot.members {
                let Some(player_id) = positive(member.player_id_fotmob) else {
                    continue;
                };
                identity
                    .map
                    .entry(player_id)
                    .or_insert_with(|| {
                        identity.order.push(player_id);
                        Vec::new()
                    })
                    .push(Observation {
                        fotmob_team_id: snapshot.team_id_fotmob,
                        club_name: &snapshot.club_name,
                        member,
                    });
            }
        }
        identity
    }

    /// `(fotmob player id, sightings)` in first-seen order.
    pub fn iter(&self) -> impl Iterator<Item = (i64, &[Observation<'a>])> {
        self.order.iter().map(|id| (*id, self.map[id].as_slice()))
    }
}

pub(super) fn positive(value: Option<i64>) -> Option<i64> {
    value.filter(|&v| v > 0)
}

pub(super) fn positive_u32(value: Option<u32>) -> Option<u32> {
    value.filter(|&v| v > 0)
}

/// Every player registered anywhere in `rosters`.
pub fn all_roster_ids(rosters: &RosterMap) -> HashSet<u32> {
    rosters
        .values()
        .flatten()
        .copied()
        .filter(|&p| p != 0)
        .collect()
}

fn split_len(folded: &str) -> usize {
    folded.split_whitespace().count()
}

/// Resolve a single-token alias only with unique roster and shirt evidence.
fn match_short_name_by_current_shirt(
    matcher: &dyn PlayerMatcher,
    member: &SquadMember,
    team_id: u32,
    team_player_map: &RosterMap,
    team_shirt_numbers: Option<&ShirtNumbers>,
) -> Option<PlayerHit> {
    let folded = fold_text(&member.player_name);
    let query_tokens: Vec<&str> = folded.split_whitespace().collect();
    let shirt_number = member.shirt_number.filter(|&n| n > 0);
    let (&[query_token], Some(shirt_number), Some(team_shirt_numbers)) =
        (query_tokens.as_slice(), shirt_number, team_shirt_numbers)
    else {
        return None;
    };

    let empty = HashMap::new();
    let local_shirt_numbers = team_shirt_numbers.get(&team_id).unwrap_or(&empty);
    let provider_position = fl_text::strip(&member.position).to_uppercase();
    let mut candidates: Vec<(u32, f64, &str)> = Vec::new();
    for &player_id in team_player_map.get(&team_id).map_or(&[][..], Vec::as_slice) {
        let local = local_shirt_numbers
            .get(&player_id)
            .copied()
            .filter(|&n| n > 0);
        if local != Some(shirt_number) || !matcher.is_player_age_compatible(player_id, member.age) {
            continue;
        }
        let position_compatible =
            matcher.is_player_metadata_compatible(player_id, &member.position, member.age);
        let candidate_position = fl_text::strip(matcher.player_position(player_id)).to_uppercase();
        let adjacent_attacking = ATTACKING_SHORT_ALIAS_POSITIONS
            .contains(&provider_position.as_str())
            && ATTACKING_SHORT_ALIAS_POSITIONS.contains(&candidate_position.as_str());
        for (candidate_norm, candidate_name) in matcher.player_names(player_id) {
            let Some(first_token) = candidate_norm.split_whitespace().next() else {
                continue;
            };
            let exact_first_token = first_token == query_token;
            if !position_compatible && !(adjacent_attacking && exact_first_token) {
                continue;
            }
            let score = matcher.score_player(query_token, candidate_norm, &ScoreContext::default());
            if score < MIN_SHORT_ALIAS_SCORE {
                continue;
            }
            match candidates.iter_mut().find(|(id, _, _)| *id == player_id) {
                Some(previous) if score > previous.1 => {
                    *previous = (player_id, score, candidate_name)
                }
                Some(_) => {}
                None => candidates.push((player_id, score, candidate_name)),
            }
        }
    }
    let [(player_id, _, player_name)] = candidates.as_slice() else {
        return None;
    };
    Some(PlayerHit {
        id: Some(*player_id),
        name: (*player_name).to_owned(),
        confidence: 95.0,
    })
}

fn hit(id: u32, name: &str, confidence: f64) -> PlayerHit {
    PlayerHit {
        id: Some(id),
        name: name.to_owned(),
        confidence,
    }
}

/// Resolve a snapshot identity before using a guarded fuzzy fallback.
///
/// `all_roster_ids` is every player registered in `team_player_map`; callers
/// matching many members against an unchanged map compute it once.
pub fn match_snapshot_member(
    matcher: &dyn PlayerMatcher,
    member: &SquadMember,
    team_id: u32,
    team_player_map: &RosterMap,
    threshold: f64,
    team_shirt_numbers: Option<&ShirtNumbers>,
    all_roster_ids: &HashSet<u32>,
) -> PlayerHit {
    let team_roster_ids: HashSet<u32> = team_player_map
        .get(&team_id)
        .map(|roster| roster.iter().copied().filter(|&p| p != 0).collect())
        .unwrap_or_default();
    let query_norm = fold_text(&member.player_name);
    let query_len = split_len(&query_norm);
    let metadata_ok = |player_id: u32| {
        matcher.is_player_metadata_compatible(player_id, &member.position, member.age)
    };
    let alias = || {
        match_short_name_by_current_shirt(
            matcher,
            member,
            team_id,
            team_player_map,
            team_shirt_numbers,
        )
    };

    let exact_records = matcher.exact_player_candidates(&query_norm);
    let mut exact_global_candidate: Option<&PlayerRecord> = None;
    if !exact_records.is_empty() {
        // Prefer an exact identity already registered at this snapshot's
        // club. A provider can shorten a name ("Endrick" vs "Endrick
        // Felipe") while an unrelated player elsewhere has the shorter exact
        // name.
        let age_compatible: Vec<&PlayerRecord> = exact_records
            .iter()
            .filter(|candidate| matcher.is_player_age_compatible(candidate.id, member.age))
            .collect();
        let team_candidates: Vec<&PlayerRecord> = age_compatible
            .iter()
            .copied()
            .filter(|candidate| team_roster_ids.contains(&candidate.id))
            .collect();
        if let [only] = team_candidates.as_slice() {
            if let Some(found) = alias()
                && found.id != Some(only.id)
            {
                return found;
            }
            return hit(only.id, &only.name, 100.0);
        }
        if team_candidates.len() > 1 {
            let metadata: Vec<_> = team_candidates
                .iter()
                .copied()
                .filter(|c| metadata_ok(c.id))
                .collect();
            if let [only] = metadata.as_slice() {
                return hit(only.id, &only.name, 100.0);
            }
            if let Some(found) = alias() {
                return found;
            }
            return PlayerHit::none(100.0);
        }

        // A unique exact candidate already in any local roster is safe when
        // the snapshot club has no stronger contextual candidate. This covers
        // single-name players such as "Allan" and "Sávio" without guessing
        // among homonyms.
        if let [candidate] = age_compatible.as_slice()
            && all_roster_ids.contains(&candidate.id)
            && metadata_ok(candidate.id)
        {
            exact_global_candidate = Some(candidate);
        }
        // A unique exact catalog identity absent from every local roster is
        // safe to restore from a complete current-squad snapshot only when
        // the provider supplies a multi-token identity.
        if let [candidate] = age_compatible.as_slice()
            && query_len >= 2
            && !all_roster_ids.contains(&candidate.id)
            && metadata_ok(candidate.id)
        {
            return hit(candidate.id, &candidate.name, 100.0);
        }

        // Multi-token exact names are safe enough to resolve from another
        // current club (the normal inferred-move case).
        if query_len > 1 {
            let roster_candidates: Vec<&PlayerRecord> = age_compatible
                .iter()
                .copied()
                .filter(|candidate| all_roster_ids.contains(&candidate.id))
                .collect();
            if let [only] = roster_candidates.as_slice() {
                return hit(only.id, &only.name, 100.0);
            }
            if roster_candidates.len() > 1 {
                let metadata: Vec<_> = roster_candidates
                    .iter()
                    .copied()
                    .filter(|c| metadata_ok(c.id))
                    .collect();
                if let [only] = metadata.as_slice() {
                    return hit(only.id, &only.name, 100.0);
                }
                return PlayerHit::none(100.0);
            }
        }
    }

    if let Some(found) = alias() {
        return found;
    }

    // Search only the snapshot club's local roster before falling back to a
    // global fuzzy match. This handles abbreviated provider names without
    // allowing a short exact name from an unrelated club to win.
    let mut contextual: Vec<(f64, &str, u32)> = Vec::new();
    for &candidate_id in &team_roster_ids {
        let mut best: Option<(f64, &str)> = None;
        for (candidate_norm, candidate_name) in matcher.player_names(candidate_id) {
            let score = matcher.score_player(
                &query_norm,
                candidate_norm,
                &ScoreContext {
                    position: &member.position,
                    candidate_pid: Some(candidate_id),
                    nationality: &member.nationality,
                    age: member.age,
                },
            );
            if best.is_none_or(|(previous, _)| score > previous) {
                best = Some((score, candidate_name));
            }
        }
        if let Some((score, name)) = best {
            contextual.push((score, name, candidate_id));
        }
    }
    // (score, name, id) descending.
    contextual.sort_by(|a, b| {
        b.0.partial_cmp(&a.0)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| b.1.cmp(a.1))
            .then_with(|| b.2.cmp(&a.2))
    });
    let minimum_context_confidence = threshold.max(90.0);
    if let Some(&(best_score, best_name, best_id)) = contextual.first()
        && best_score >= minimum_context_confidence
    {
        let runner_up = contextual.iter().skip(1).find(|item| item.2 != best_id);
        if runner_up.is_none_or(|(score, _, _)| best_score - score >= CONTEXT_MARGIN) {
            return hit(best_id, best_name, best_score);
        }
        return PlayerHit::none(best_score);
    }

    // A unique exact name already present in another local roster is safe
    // after contextual matching; do not use a fuzzy one-token global guess.
    if let Some(candidate) = exact_global_candidate {
        return hit(candidate.id, &candidate.name, 100.0);
    }
    if split_len(&query_norm) < 2 {
        let best = contextual.iter().map(|item| item.0).fold(0.0_f64, f64::max);
        return PlayerHit::none(best);
    }
    let global_threshold = threshold.max(95.0);
    let found = matcher.match_player(
        &member.player_name,
        &PlayerQuery {
            threshold: global_threshold,
            from_team_id: Some(team_id),
            team_player_map: Some(team_player_map),
            position: &member.position,
            nationality: &member.nationality,
            age: member.age,
            ..PlayerQuery::default()
        },
    );
    match found.id {
        Some(player_id)
            if all_roster_ids.contains(&player_id) && found.confidence >= global_threshold =>
        {
            found
        }
        _ => PlayerHit::none(found.confidence),
    }
}

/// Resolved FotMob player identities of the current live squads.
#[derive(Debug, Default)]
pub struct SnapshotIdentities {
    /// FotMob player id → unique save player id.
    pub fotmob_to_pes: HashMap<i64, u32>,
    /// Display name for identities matched with high confidence.
    pub names: HashMap<i64, String>,
    /// Each save club's complete live squad as unique save player ids.
    pub live_squad_ids: HashMap<u32, HashSet<u32>>,
}

/// Resolve current FotMob ids to unique save players once per snapshot set.
#[allow(clippy::too_many_arguments)]
pub fn build_fotmob_identity_index(
    matcher: &dyn PlayerMatcher,
    team_player_map: &RosterMap,
    club_ids: &HashSet<u32>,
    threshold: f64,
    club_identity: Option<&dyn ClubIdentity>,
    identity_map: &IdentityMap<'_>,
    team_shirt_numbers: Option<&ShirtNumbers>,
) -> SnapshotIdentities {
    let mut candidates: HashMap<i64, BTreeSet<u32>> = HashMap::new();
    let mut candidate_order: Vec<i64> = Vec::new();
    let mut names: HashMap<i64, String> = HashMap::new();
    let mut team_cache: HashMap<i64, Option<u32>> = HashMap::new();
    let mut provider_local_teams: HashMap<i64, BTreeSet<u32>> = HashMap::new();
    let all_ids = all_roster_ids(team_player_map);
    let name_confidence = threshold.max(95.0);

    for (fotmob_player_id, observations) in identity_map.iter() {
        for observation in observations {
            let Some(fotmob_team_id) = positive(Some(observation.fotmob_team_id)) else {
                continue;
            };
            let local_team_id = *team_cache.entry(fotmob_team_id).or_insert_with(|| {
                resolve_snapshot_club(
                    matcher,
                    club_identity,
                    fotmob_team_id,
                    observation.club_name,
                )
            });
            let Some(local_team_id) = local_team_id.filter(|id| club_ids.contains(id)) else {
                continue;
            };
            let found = match_snapshot_member(
                matcher,
                observation.member,
                local_team_id,
                team_player_map,
                threshold,
                team_shirt_numbers,
                &all_ids,
            );
            let Some(player_id) = found.id else { continue };
            if !candidates.contains_key(&fotmob_player_id) {
                candidate_order.push(fotmob_player_id);
            }
            candidates
                .entry(fotmob_player_id)
                .or_default()
                .insert(player_id);
            provider_local_teams
                .entry(fotmob_player_id)
                .or_default()
                .insert(local_team_id);
            if found.confidence >= name_confidence {
                let name = if found.name.is_empty() {
                    observation.member.player_name.clone()
                } else {
                    found.name
                };
                names.insert(fotmob_player_id, name);
            }
        }
    }

    let mut unique: HashMap<i64, u32> = candidate_order
        .iter()
        .filter_map(|id| {
            let ids = &candidates[id];
            (ids.len() == 1).then(|| (*id, *ids.iter().next().expect("one id")))
        })
        .collect();

    // One save identity must not be claimed by multiple distinct provider
    // identities. Prefer the provider identity anchored to that player's
    // current local roster; otherwise reject the ambiguous mappings.
    let mut provider_ids_by_player: HashMap<u32, BTreeSet<i64>> = HashMap::new();
    for (&fotmob_player_id, &player_id) in &unique {
        provider_ids_by_player
            .entry(player_id)
            .or_default()
            .insert(fotmob_player_id);
    }
    let mut rejected: HashSet<i64> = HashSet::new();
    let mut collisions: Vec<(&u32, &BTreeSet<i64>)> = provider_ids_by_player
        .iter()
        .filter(|(_, ids)| ids.len() > 1)
        .collect();
    collisions.sort_by_key(|(player_id, _)| **player_id);
    for (&player_id, provider_ids) in collisions {
        let current_team_ids: HashSet<u32> = club_ids
            .iter()
            .copied()
            .filter(|team_id| {
                team_player_map
                    .get(team_id)
                    .is_some_and(|roster| roster.contains(&player_id))
            })
            .collect();
        let anchored: Vec<i64> = provider_ids
            .iter()
            .copied()
            .filter(|provider_id| {
                provider_local_teams
                    .get(provider_id)
                    .is_some_and(|teams| teams.iter().any(|t| current_team_ids.contains(t)))
            })
            .collect();
        if let [keep] = anchored.as_slice() {
            rejected.extend(provider_ids.iter().copied().filter(|id| id != keep));
            tracing::warn!(
                "Provider identity collision for save player {player_id}; keeping provider \
                 {keep} anchored to current team"
            );
        } else {
            rejected.extend(provider_ids.iter().copied());
            tracing::warn!(
                "Rejecting ambiguous provider identities {:?} for save player {player_id}",
                provider_ids
            );
        }
    }
    unique.retain(|id, _| !rejected.contains(id));
    names.retain(|id, _| unique.contains_key(id));

    let mut live_squad_ids: HashMap<u32, HashSet<u32>> = HashMap::new();
    for teams in provider_local_teams.values() {
        for &team_id in teams {
            live_squad_ids.entry(team_id).or_default();
        }
    }
    for (fotmob_player_id, &player_id) in &unique {
        if let Some(teams) = provider_local_teams.get(fotmob_player_id) {
            for team_id in teams {
                live_squad_ids
                    .entry(*team_id)
                    .or_default()
                    .insert(player_id);
            }
        }
    }
    SnapshotIdentities {
        fotmob_to_pes: unique,
        names,
        live_squad_ids,
    }
}
