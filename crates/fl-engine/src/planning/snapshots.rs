//! Moves and releases derived from complete live squad snapshots.

use std::collections::{HashMap, HashSet};

use fl_sources::model::{SquadMember, SquadSnapshot, Transfer, TransferType, VerificationStatus};

use super::identity::{
    IdentityMap, MIN_COMPLETE_SQUAD_MEMBERS, ShirtNumbers, SnapshotIdentities, all_roster_ids,
    match_snapshot_member, positive,
};
use super::ports::{ClubIdentity, PlayerMatcher};
use super::roster_sim::RosterSim;
use super::skipped::SkippedTransfer;
use super::teams::resolve_snapshot_club;
use super::types::{MatchedTransfer, Side};

/// A snapshot must confirm at least this share of the current roster before
/// it may release anyone.
const MIN_CURRENT_SQUAD_MATCH_RATIO: f64 = 0.75;

/// Quality of one provider snapshot against the current local roster.
#[derive(Debug, Clone)]
pub struct SnapshotCoverage<'a> {
    pub snapshot: &'a SquadSnapshot,
    pub team_id: u32,
    pub team_name: String,
    pub current_player_ids: HashSet<u32>,
    pub snapshot_player_ids: HashSet<u32>,
    pub healthy: bool,
}

/// Coverage per save club, in snapshot order.
#[derive(Debug, Default)]
pub struct CoverageSet<'a> {
    items: Vec<SnapshotCoverage<'a>>,
}

impl<'a> CoverageSet<'a> {
    pub fn get(&self, team_id: u32) -> Option<&SnapshotCoverage<'a>> {
        self.items.iter().find(|item| item.team_id == team_id)
    }

    pub fn iter(&self) -> impl Iterator<Item = &SnapshotCoverage<'a>> {
        self.items.iter()
    }
}

/// Classify snapshots before allowing destructive roster reconciliation.
#[allow(clippy::too_many_arguments)]
pub fn build_snapshot_roster_coverage<'a>(
    matcher: &dyn PlayerMatcher,
    threshold: f64,
    sim: &RosterSim,
    club_ids: &HashSet<u32>,
    snapshots: &'a [SquadSnapshot],
    fotmob_to_pes: &HashMap<i64, u32>,
    club_identity: Option<&dyn ClubIdentity>,
    team_shirt_numbers: Option<&ShirtNumbers>,
) -> CoverageSet<'a> {
    let mut set = CoverageSet::default();
    let mut seen_team_ids: HashSet<u32> = HashSet::new();
    let all_ids = all_roster_ids(sim.map());

    for snapshot in snapshots {
        if !snapshot.complete || snapshot.members.len() < MIN_COMPLETE_SQUAD_MEMBERS {
            continue;
        }
        let Some(fotmob_team_id) = positive(Some(snapshot.team_id_fotmob)) else {
            continue;
        };
        let Some(team_id) =
            resolve_snapshot_club(matcher, club_identity, fotmob_team_id, &snapshot.club_name)
        else {
            continue;
        };
        if !club_ids.contains(&team_id) || !seen_team_ids.insert(team_id) {
            continue;
        }

        let current_ids: HashSet<u32> = sim
            .roster(team_id)
            .unwrap_or(&[])
            .iter()
            .copied()
            .filter(|&p| p > 0)
            .collect();
        let mut snapshot_player_ids: HashSet<u32> = HashSet::new();
        for member in &snapshot.members {
            let known =
                positive(member.player_id_fotmob).and_then(|id| fotmob_to_pes.get(&id).copied());
            if let Some(known) = known
                && current_ids.contains(&known)
            {
                snapshot_player_ids.insert(known);
                continue;
            }
            let found = match_snapshot_member(
                matcher,
                member,
                team_id,
                sim.map(),
                threshold,
                team_shirt_numbers,
                &all_ids,
            );
            if let Some(player_id) = found.id
                && current_ids.contains(&player_id)
                && found.confidence >= threshold.max(90.0)
            {
                snapshot_player_ids.insert(player_id);
            }
        }

        let minimum_current_matches = MIN_COMPLETE_SQUAD_MEMBERS
            .max((current_ids.len() as f64 * MIN_CURRENT_SQUAD_MATCH_RATIO).ceil() as usize);
        let healthy =
            !current_ids.is_empty() && snapshot_player_ids.len() >= minimum_current_matches;
        if !healthy {
            tracing::warn!(
                "Skipping roster reconciliation for {} ({team_id}): only {}/{} current roster \
                 players matched",
                snapshot.club_name,
                snapshot_player_ids.len(),
                current_ids.len()
            );
        }
        set.items.push(SnapshotCoverage {
            snapshot,
            team_id,
            team_name: snapshot.club_name.clone(),
            current_player_ids: current_ids,
            snapshot_player_ids,
            healthy,
        });
    }
    set
}

fn first_shirt_update(matched: &[MatchedTransfer]) -> usize {
    matched
        .iter()
        .position(MatchedTransfer::is_shirt_update)
        .unwrap_or(matched.len())
}

fn team_label(matcher: &dyn PlayerMatcher, team_id: u32) -> String {
    let name = matcher.team_name(team_id);
    if name.is_empty() {
        format!("Team {team_id}")
    } else {
        name.to_owned()
    }
}

/// Create safe moves and destination-only registrations from snapshots.
///
/// Every live-squad player who sits elsewhere in the save but cannot be moved
/// safely is recorded in `skipped` with a reason code.
#[allow(clippy::too_many_arguments)]
pub fn append_current_squad_moves(
    matched: &mut Vec<MatchedTransfer>,
    matcher: &dyn PlayerMatcher,
    sim: &mut RosterSim,
    club_ids: &HashSet<u32>,
    identity_map: &IdentityMap<'_>,
    snapshot_identities: &SnapshotIdentities,
    club_identity: Option<&dyn ClubIdentity>,
    coverage: &CoverageSet<'_>,
    skipped: &mut Vec<SkippedTransfer>,
) {
    let mut seen: HashSet<(u32, u32)> = HashSet::new();
    let mut uncovered_source_ids: HashSet<u32> = HashSet::new();
    let mut destination_cache: HashMap<i64, Option<u32>> = HashMap::new();

    for (fotmob_player_id, observations) in identity_map.iter() {
        let Some(&player_id) = snapshot_identities.fotmob_to_pes.get(&fotmob_player_id) else {
            continue;
        };

        // (destination, raw fotmob team id, member)
        let mut destination_observations: Vec<(u32, i64, &SquadMember)> = Vec::new();
        let mut destination_ids: Vec<u32> = Vec::new();
        for observation in observations {
            let Some(team_id) = positive(Some(observation.fotmob_team_id)) else {
                continue;
            };
            let destination = *destination_cache.entry(team_id).or_insert_with(|| {
                resolve_snapshot_club(matcher, club_identity, team_id, observation.club_name)
            });
            let Some(destination) = destination else {
                continue;
            };
            if !destination_ids.contains(&destination) {
                destination_ids.push(destination);
            }
            destination_observations.push((
                destination,
                observation.fotmob_team_id,
                observation.member,
            ));
        }

        let save_destination_ids: Vec<u32> = destination_ids
            .iter()
            .copied()
            .filter(|id| club_ids.contains(id))
            .collect();
        if save_destination_ids.is_empty() {
            continue;
        }
        let current_clubs: Vec<u32> = sim.clubs_of(player_id).to_vec();
        let first_member = destination_observations
            .iter()
            .find(|(team, _, _)| save_destination_ids.contains(team))
            .map(|(_, _, member)| *member)
            .expect("a save destination has an observation");

        let current_label = || {
            let labels: Vec<String> = current_clubs
                .iter()
                .map(|&team| team_label(matcher, team))
                .collect();
            if labels.is_empty() {
                "Free Agent".to_owned()
            } else {
                labels.join(", ")
            }
        };
        let mut record =
            |reason: &str, detail: &str, destination_name: &str, candidates: &[String]| {
                skipped.push(SkippedTransfer::snapshot(
                    first_member,
                    fotmob_player_id,
                    &current_label(),
                    destination_name,
                    reason,
                    detail,
                    candidates,
                ));
            };

        if destination_ids.len() != 1 {
            if !save_destination_ids
                .iter()
                .all(|id| current_clubs.contains(id))
            {
                let mut sorted = destination_ids.clone();
                sorted.sort_unstable();
                let names: Vec<String> = sorted.iter().map(|&t| team_label(matcher, t)).collect();
                record(
                    "snapshot_conflicting_clubs",
                    "player appears in several live squads",
                    &names.join(", "),
                    &names,
                );
            }
            continue;
        }
        let destination_id = destination_ids[0];
        let destination_label = team_label(matcher, destination_id);

        if current_clubs.contains(&destination_id) && current_clubs.len() == 1 {
            continue;
        }
        if current_clubs.len() > 1 {
            let mut sorted = current_clubs.clone();
            sorted.sort_unstable();
            let labels: Vec<String> = current_clubs
                .iter()
                .map(|&t| team_label(matcher, t))
                .collect();
            record(
                "duplicate_registration",
                &format!("save registers player at {sorted:?}"),
                &destination_label,
                &labels,
            );
            continue;
        }
        let source_id = current_clubs.first().copied();

        let Some(destination_coverage) = coverage.get(destination_id) else {
            record(
                "snapshot_incomplete",
                "destination live squad is incomplete or duplicated",
                &destination_label,
                &[],
            );
            continue;
        };
        // A complete destination snapshot is sufficient evidence for a
        // non-destructive registration of a uniquely resolved catalog
        // player. Stale local academy entries can make the coverage ratio
        // unhealthy; they must not prevent adding a player absent from every
        // local roster.
        if !destination_coverage.healthy
            && !destination_coverage.current_player_ids.is_empty()
            && source_id.is_some()
        {
            record(
                "snapshot_coverage_low",
                &format!(
                    "{}/{} destination roster players confirmed by live squad",
                    destination_coverage.snapshot_player_ids.len(),
                    destination_coverage.current_player_ids.len()
                ),
                &destination_label,
                &[],
            );
            continue;
        }

        // A healthy complete destination snapshot is authoritative for
        // current membership, even when the player's local source team was
        // not indexed. Moves stay gated when destination coverage is also
        // unhealthy.
        if let Some(source) = source_id
            && coverage.get(source).is_none()
            && !destination_coverage.healthy
        {
            if uncovered_source_ids.insert(source) {
                tracing::warn!(
                    "Skipping current-squad moves from {} ({source}): source roster snapshot is \
                     missing",
                    team_label(matcher, source)
                );
            }
            record(
                "source_snapshot_missing",
                "source club has no live squad and destination coverage is low",
                &destination_label,
                &[],
            );
            continue;
        }
        if seen.contains(&(player_id, destination_id)) {
            continue;
        }

        let source_name = match source_id {
            Some(source) => matcher.team_name(source).to_owned(),
            None => "Free Agent".to_owned(),
        };
        let destination_name = matcher.team_name(destination_id).to_owned();
        if (source_id.is_some() && source_name.is_empty()) || destination_name.is_empty() {
            record(
                "team_name_missing",
                "save club has no loaded name",
                &destination_label,
                &[],
            );
            continue;
        }
        seen.insert((player_id, destination_id));
        let (_, raw_team_id, member) = destination_observations[0];
        let source_url = format!("https://www.fotmob.com/api/data/teams?id={raw_team_id}");
        let transfer = Transfer {
            player_name: member.player_name.clone(),
            from_club: source_name.clone(),
            to_club: destination_name.clone(),
            transfer_type: TransferType::SquadRegistration,
            shirt_number: member.shirt_number,
            position: member.position.clone(),
            age: member.age,
            nationality: member.nationality.clone(),
            to_club_id_fotmob: Some(raw_team_id),
            player_id_fotmob: Some(fotmob_player_id),
            from_club_full_name: source_name.clone(),
            to_club_full_name: destination_name.clone(),
            source_urls: vec![source_url.clone()],
            proof_urls: vec![source_url],
            verification_status: VerificationStatus::Enabled,
            infer_from_current_roster: source_id.is_some(),
            ..Transfer::default()
        };
        let mut moved = MatchedTransfer::new(transfer);
        moved.player_id = Some(player_id);
        moved.from = source_id.map_or(Side::Outside, Side::Team);
        moved.to = Side::Team(destination_id);
        moved.player_confidence = 100.0;
        moved.from_confidence = 100.0;
        moved.to_confidence = 100.0;
        moved.matched_player_name = snapshot_identities
            .names
            .get(&fotmob_player_id)
            .filter(|name| !name.is_empty())
            .cloned()
            .unwrap_or_else(|| member.player_name.clone());
        moved.matched_from_team = source_name;
        moved.matched_to_team = destination_name;
        let insert_at = first_shirt_update(matched);
        matched.insert(insert_at, moved);
        if let Some(source) = source_id {
            sim.remove(source, player_id);
        }
        sim.add(destination_id, player_id);
    }
}

/// Insert safe releases before shirt updates for healthy snapshots.
pub fn append_current_squad_releases(
    matched: &mut Vec<MatchedTransfer>,
    sim: &RosterSim,
    coverage: &CoverageSet<'_>,
    player_names: &HashMap<u32, String>,
) {
    let mut released_routes: HashSet<(u32, u32)> = matched
        .iter()
        .filter(|m| m.is_release())
        .filter_map(|m| Some((m.player_id?, m.from.team()?)))
        .collect();
    let mut protected_destination_ids: HashMap<u32, HashSet<u32>> = HashMap::new();
    for item in matched.iter() {
        let (Some(player_id), Some(to_team)) = (item.player_id, item.to.team()) else {
            continue;
        };
        if item.is_shirt_update() {
            continue;
        }
        if sim.contains(to_team, player_id) {
            protected_destination_ids
                .entry(to_team)
                .or_default()
                .insert(player_id);
        }
    }
    let mut insert_at = first_shirt_update(matched);

    for item in coverage.iter().filter(|item| item.healthy) {
        let team_id = item.team_id;
        let current_ids: HashSet<u32> = sim
            .roster(team_id)
            .unwrap_or(&[])
            .iter()
            .copied()
            .filter(|&p| p > 0)
            .collect();
        let mut leaving: Vec<u32> = current_ids
            .difference(&item.snapshot_player_ids)
            .copied()
            .collect();
        leaving.sort_unstable();
        for player_id in leaving {
            if released_routes.contains(&(player_id, team_id))
                || protected_destination_ids
                    .get(&team_id)
                    .is_some_and(|p| p.contains(&player_id))
            {
                continue;
            }
            let player_name = player_names
                .get(&player_id)
                .filter(|name| !name.is_empty())
                .cloned()
                .unwrap_or_else(|| format!("Player {player_id}"));
            let transfer = Transfer {
                player_name: player_name.clone(),
                from_club: item.team_name.clone(),
                to_club: "Free Agent".to_owned(),
                transfer_type: TransferType::SquadRelease,
                from_club_id_fotmob: Some(item.snapshot.team_id_fotmob),
                from_club_full_name: item.team_name.clone(),
                source_urls: vec![item.snapshot.source_url.clone()],
                proof_urls: vec![item.snapshot.source_url.clone()],
                verification_status: VerificationStatus::Enabled,
                infer_from_current_roster: true,
                ..Transfer::default()
            };
            let mut release = MatchedTransfer::new(transfer);
            release.player_id = Some(player_id);
            release.from = Side::Team(team_id);
            release.from_confidence = 100.0;
            release.player_confidence = 100.0;
            release.matched_player_name = player_name;
            release.matched_from_team = item.team_name.clone();
            matched.insert(insert_at, release);
            insert_at += 1;
            released_routes.insert((player_id, team_id));
        }
    }
}
