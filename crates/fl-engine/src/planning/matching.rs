//! Binding transfer events to save players and clubs, one event at a time,
//! against a simulated roster.

use std::collections::{HashMap, HashSet};

use chrono::{DateTime, Utc};
use fl_sources::dates::parse_iso_datetime;
use fl_sources::model::{SquadSnapshot, Transfer, TransferType};

use super::history::{History, replay_history};
use super::identity::{
    IdentityMap, ShirtNumbers, SnapshotIdentities, build_fotmob_identity_index, positive,
};
use super::order::ordered_indices;
use super::ports::{ClubIdentity, PlayerHit, PlayerMatcher, PlayerQuery};
use super::roster_sim::{RosterSim, SlotMode};
use super::skipped::SkippedTransfer;
use super::snapshots::{
    append_current_squad_moves, append_current_squad_releases, build_snapshot_roster_coverage,
};
use super::teams::match_transfer_team;
use super::types::{HistoryEntry, MatchSkip, MatchedTransfer, RosterMap, Side};

/// Everything matching reads.
pub struct MatchInput<'a> {
    /// Scraped events plus roster observations; any order (sorted here).
    pub transfers: &'a [Transfer],
    pub matcher: &'a dyn PlayerMatcher,
    pub threshold: f64,
    /// Present players per team, in table order.
    pub teams: &'a [(u32, Vec<u32>)],
    pub club_ids: &'a HashSet<u32>,
    pub history: &'a [HistoryEntry],
    pub club_identity: Option<&'a dyn ClubIdentity>,
    pub squad_snapshots: &'a [SquadSnapshot],
    pub player_names: &'a HashMap<u32, String>,
    pub team_shirt_numbers: Option<&'a ShirtNumbers>,
}

/// Matching-stage diagnostics.
#[derive(Debug, Default, Clone)]
pub struct MatchReport {
    /// Live-squad moves that could not be planned.
    pub skipped: Vec<SkippedTransfer>,
    /// Each save club → the save players its complete live squad contains.
    pub live_squad_ids: HashMap<u32, HashSet<u32>>,
    /// FotMob player id → unique save player (live identity preferred over
    /// the transfer log).
    pub fotmob_player_ids: HashMap<i64, u32>,
}

#[derive(Debug, Clone)]
pub struct MatchOutput {
    pub matched: Vec<MatchedTransfer>,
    pub report: MatchReport,
}

/// Evidence about FotMob player identity: the transfer log (mutable as the
/// run learns) and the live squads (fixed).
struct IdentityBook<'a> {
    live: &'a SnapshotIdentities,
    fotmob_to_pes: HashMap<i64, u32>,
    names: HashMap<i64, String>,
}

impl IdentityBook<'_> {
    fn name_or(&self, fotmob_id: i64, fallback: &str) -> String {
        self.names
            .get(&fotmob_id)
            .cloned()
            .unwrap_or_else(|| fallback.to_owned())
    }
}

/// A learned logged identity to record (stale log entry superseded by the
/// event clubs' rosters).
struct LearnedIdentity {
    fotmob_id: i64,
    player_id: u32,
    name: String,
}

struct IdentityDecision {
    player_id: Option<u32>,
    name: String,
    confidence: f64,
    skip: Option<MatchSkip>,
    learned: Option<LearnedIdentity>,
}

/// Arbitrate between the name match and FotMob identity evidence: the live
/// squad beats the transfer log, which beats the name alone; conflicts are
/// settled by which identity the event's clubs register, else skipped.
fn resolve_player_identity(
    matcher: &dyn PlayerMatcher,
    book: &IdentityBook<'_>,
    transfer: &Transfer,
    name_hit: PlayerHit,
    from: Side,
    to: Side,
    context: &RosterMap,
) -> IdentityDecision {
    let fotmob_player_id = positive(transfer.player_id_fotmob);
    let snapshot_known = fotmob_player_id.and_then(|id| book.live.fotmob_to_pes.get(&id).copied());
    let known = fotmob_player_id.and_then(|id| book.fotmob_to_pes.get(&id).copied());
    let mut decision = IdentityDecision {
        player_id: name_hit.id,
        name: name_hit.name,
        confidence: name_hit.confidence,
        skip: None,
        learned: None,
    };
    let fotmob_label = || {
        transfer
            .player_id_fotmob
            .map(|id| id.to_string())
            .unwrap_or_default()
    };

    if transfer.transfer_type == TransferType::ShirtNumberUpdate
        && let (Some(snapshot_known), Some(fid)) = (snapshot_known, fotmob_player_id)
    {
        // The complete current-squad snapshot is the authoritative provider
        // identity for its own shirt observations. A stale historical route
        // must not make the number update ambiguous.
        decision.player_id = Some(snapshot_known);
        decision.name = book
            .live
            .names
            .get(&fid)
            .cloned()
            .unwrap_or_else(|| transfer.player_name.clone());
        decision.confidence = 100.0;
        return decision;
    }
    let (Some(known), Some(fid)) = (known, fotmob_player_id) else {
        return decision;
    };

    match decision.player_id {
        None => {
            if transfer.infer_from_current_roster {
                tracing::warn!(
                    "FotMob player {} has no independently matched save identity; ignoring \
                     stale history for destination-only registration",
                    fotmob_label()
                );
            } else if (!transfer.position.is_empty() || transfer.age != 0)
                && !matcher.is_player_metadata_compatible(known, &transfer.position, transfer.age)
            {
                tracing::warn!(
                    "Rejecting stale provider identity {} for {:?}: metadata conflicts with save \
                     player {known}",
                    fotmob_label(),
                    transfer.player_name
                );
                decision.name.clear();
                decision.skip = Some(MatchSkip {
                    reason: "provider_identity_metadata_conflict".to_owned(),
                    detail: format!(
                        "FotMob identity maps to PES player {known} whose position/age \
                         contradicts this event"
                    ),
                    candidates: vec![book.names.get(&fid).cloned().unwrap_or_default()],
                });
            } else {
                // Historical provider ids remain the only evidence for
                // legitimate public-name changes when no metadata contradicts
                // the established identity.
                decision.player_id = Some(known);
                decision.name = book.name_or(fid, &transfer.player_name);
                decision.confidence = 100.0;
            }
        }
        Some(named) if named != known => {
            if Some(known) == snapshot_known {
                tracing::warn!(
                    "FotMob player {}: name matched PES {named} but the live squad identifies \
                     {known}; using live squad identity",
                    fotmob_label()
                );
                decision.player_id = Some(known);
                decision.name = book.name_or(fid, &transfer.player_name);
                decision.confidence = 100.0;
            } else {
                let on_route = |player: u32| {
                    [from, to].into_iter().filter_map(Side::team).any(|team| {
                        context
                            .get(&team)
                            .is_some_and(|roster| roster.contains(&player))
                    })
                };
                let name_on_route = on_route(named);
                let logged_on_route = on_route(known);
                if name_on_route && !logged_on_route {
                    tracing::warn!(
                        "FotMob player {}: transfer log identity {known} is stale; event clubs \
                         register name-matched PES {named}",
                        fotmob_label()
                    );
                    let name = if decision.name.is_empty() {
                        transfer.player_name.clone()
                    } else {
                        decision.name.clone()
                    };
                    decision.learned = Some(LearnedIdentity {
                        fotmob_id: fid,
                        player_id: named,
                        name,
                    });
                } else if logged_on_route && !name_on_route {
                    decision.player_id = Some(known);
                    decision.name = book.name_or(fid, &transfer.player_name);
                    decision.confidence = 100.0;
                } else {
                    tracing::warn!(
                        "FotMob player {} conflicts with save history ({known} vs {named}); no \
                         roster evidence decides",
                        fotmob_label()
                    );
                    let candidates = [
                        book.names.get(&fid).cloned().unwrap_or_default(),
                        decision.name.clone(),
                    ]
                    .into_iter()
                    .filter(|name| !name.is_empty())
                    .collect();
                    decision.skip = Some(MatchSkip {
                        reason: "provider_identity_conflict".to_owned(),
                        detail: format!(
                            "transfer log maps FotMob player to PES {known}, name matches PES {named}"
                        ),
                        candidates,
                    });
                    decision.player_id = None;
                    decision.name.clear();
                }
            }
        }
        Some(_) => {}
    }
    decision
}

/// A loan whose named source is stale: the save already holds the player at a
/// club he permanently joined from that source, so he is loaned out from
/// there instead.
struct RebasedSource {
    team_id: u32,
    name: String,
}

#[allow(clippy::too_many_arguments)]
fn rebase_stale_loan_source(
    matcher: &dyn PlayerMatcher,
    history: &History,
    player_id: u32,
    is_loan: bool,
    from: Side,
    to: Side,
    current_team: Option<u32>,
    when: Option<DateTime<Utc>>,
    from_name: &str,
) -> Option<RebasedSource> {
    let (Side::Team(source), Side::Team(destination), Some(current), Some(when)) =
        (from, to, current_team, when)
    else {
        return None;
    };
    if !is_loan || current == source || current == destination {
        return None;
    }
    let joined_current_from_source = history
        .permanent_routes
        .get(&(player_id, source))
        .is_some_and(|routes| routes.iter().any(|&(to, at)| to == current && at <= when));
    if !joined_current_from_source {
        return None;
    }
    let name = matcher.team_name(current);
    let name = if name.is_empty() {
        from_name.to_owned()
    } else {
        name.to_owned()
    };
    Some(RebasedSource {
        team_id: current,
        name,
    })
}

/// Match transfer events and derive moves/releases from complete live squads.
pub fn match_transfers_statefully(input: &MatchInput<'_>) -> MatchOutput {
    let matcher = input.matcher;
    let identity_map = IdentityMap::from_snapshots(input.squad_snapshots);
    let mut history = replay_history(input.history);

    let team_player_map: RosterMap = input.teams.iter().cloned().collect();
    let live = build_fotmob_identity_index(
        matcher,
        &team_player_map,
        input.club_ids,
        input.threshold,
        input.club_identity,
        &identity_map,
        input.team_shirt_numbers,
    );

    let mut book = IdentityBook {
        live: &live,
        fotmob_to_pes: history
            .identity_candidates
            .iter()
            .filter(|(_, ids)| ids.len() == 1)
            .map(|(&fid, ids)| (fid, *ids.iter().next().expect("one id")))
            .collect(),
        names: std::mem::take(&mut history.identity_names),
    };
    book.names
        .extend(live.names.iter().map(|(&k, v)| (k, v.clone())));
    // The current live squad is fresher evidence than any logged identity: a
    // single bad log entry must not block a player forever.
    for (&fid, &player_id) in &live.fotmob_to_pes {
        if let Some(logged) = history.identity_candidates.get(&fid)
            && !logged.is_empty()
            && !(logged.len() == 1 && logged.contains(&player_id))
        {
            tracing::warn!(
                "FotMob player {fid}: live squad identity {player_id} overrides transfer log \
                 identities {logged:?}"
            );
        }
        book.fotmob_to_pes.insert(fid, player_id);
    }

    let mut sim = RosterSim::new(
        SlotMode::Compact,
        input.teams.iter().cloned(),
        input.club_ids,
    );
    // Live-squad coverage is judged against the rosters as saved: the events
    // of this run may have moved players around in `sim`, and a later run sees
    // fewer events (its window starts after the previous run), so the gate
    // must not depend on them.
    let saved = sim.clone();
    let mut matched: Vec<MatchedTransfer> = Vec::with_capacity(input.transfers.len());

    for index in ordered_indices(input.transfers) {
        let mut transfer = input.transfers[index].clone();
        let when = parse_iso_datetime(&transfer.date);
        let from_res = match_transfer_team(
            matcher,
            &transfer.from_club,
            &transfer.from_club_full_name,
            transfer.from_club_id_fotmob,
            input.club_identity,
        );
        let to_res = match_transfer_team(
            matcher,
            &transfer.to_club,
            &transfer.to_club_full_name,
            transfer.to_club_id_fotmob,
            input.club_identity,
        );
        let (mut from, mut from_name, mut from_conf) =
            (from_res.side, from_res.name, from_res.confidence);
        let (to, to_name, to_conf) = (to_res.side, to_res.name, to_res.confidence);
        let mut skip: Option<MatchSkip> = None;

        // A parent's loaned-out players still count as its roster when
        // matching an event that names the parent as source.
        let parent_context: Option<RosterMap> = from
            .team()
            .filter(|&parent| history.loans.has_loans(parent))
            .map(|parent| {
                let mut context = sim.map().clone();
                let roster = context.entry(parent).or_default();
                for player in history.loans.players(parent) {
                    if !roster.contains(&player) {
                        roster.push(player);
                    }
                }
                context
            });
        let context = parent_context.as_ref().unwrap_or_else(|| sim.map());

        let name_hit = matcher.match_player(
            &transfer.player_name,
            &PlayerQuery {
                threshold: input.threshold,
                from_team_id: from.team(),
                to_team_id: to.team(),
                team_player_map: Some(context),
                position: &transfer.position,
                nationality: &transfer.nationality,
                age: transfer.age,
            },
        );
        let decision =
            resolve_player_identity(matcher, &book, &transfer, name_hit, from, to, context);
        if let Some(learned) = decision.learned {
            book.fotmob_to_pes
                .insert(learned.fotmob_id, learned.player_id);
            book.names.insert(learned.fotmob_id, learned.name);
        }
        let (pid, pname, pconf) = (decision.player_id, decision.name, decision.confidence);
        skip = decision.skip.or(skip);

        let current_clubs: Vec<u32> = pid.map_or_else(Vec::new, |p| sim.clubs_of(p).to_vec());
        let current_team = (current_clubs.len() == 1).then(|| current_clubs[0]);

        if from.is_outside() && transfer.infer_from_current_roster {
            let inferred_name = current_team
                .map_or("", |team| matcher.team_name(team))
                .to_owned();
            let can_infer = transfer.verification_status
                == fl_sources::model::VerificationStatus::Enabled
                && !transfer.proof_urls.is_empty()
                && pconf == 100.0
                && to.is_team()
                && current_team.is_some()
                && !inferred_name.is_empty();
            if can_infer {
                from = Side::Team(current_team.expect("checked"));
                from_conf = 100.0;
                tracing::info!(
                    "Inferred moderated transfer source from unique current roster: {} ({inferred_name}) -> {}",
                    transfer.player_name,
                    transfer.to_club
                );
                transfer.from_club = inferred_name.clone();
                transfer.from_club_full_name = inferred_name.clone();
                from_name = inferred_name;
            } else {
                // A destination-only community signal must never degrade into
                // a generic free-agent signing or release when source
                // inference is ambiguous.
                from = Side::Unresolved;
                from_name = String::new();
                from_conf = 0.0;
                skip = Some(MatchSkip {
                    reason: "source_not_inferable".to_owned(),
                    detail: "destination-only signal needs a verified proof, an exact player \
                             match, and one unique current save club"
                        .to_owned(),
                    candidates: current_clubs
                        .iter()
                        .map(|&team| {
                            let name = matcher.team_name(team);
                            if name.is_empty() {
                                format!("Team {team}")
                            } else {
                                name.to_owned()
                            }
                        })
                        .collect(),
                });
            }
        }

        let is_loan = transfer.is_loan || transfer.transfer_type == TransferType::Loan;
        if let Some(pid) = pid
            && let Some(rebased) = rebase_stale_loan_source(
                matcher,
                &history,
                pid,
                is_loan,
                from,
                to,
                current_team,
                when,
                &from_name,
            )
        {
            tracing::info!(
                "Reconciled stale loan source for {}: {} -> {} ({})",
                transfer.player_name,
                if from_name.is_empty() {
                    &transfer.from_club
                } else {
                    &from_name
                },
                rebased.name,
                rebased.team_id
            );
            from = Side::Team(rebased.team_id);
            from_name = rebased.name;
            from_conf = 100.0;
        }

        if let (Some(pid), Some(fid)) = (pid, positive(transfer.player_id_fotmob))
            && !book.fotmob_to_pes.contains_key(&fid)
        {
            book.fotmob_to_pes.insert(fid, pid);
            book.names.insert(
                fid,
                if pname.is_empty() {
                    transfer.player_name.clone()
                } else {
                    pname.clone()
                },
            );
        }

        let transfer_type = transfer.transfer_type;
        let mut item = MatchedTransfer::new(transfer);
        item.player_id = pid;
        item.from = from;
        item.to = to;
        item.player_confidence = pconf;
        item.from_confidence = from_conf;
        item.to_confidence = to_conf;
        item.matched_player_name = pname;
        item.matched_from_team = from_name;
        item.matched_to_team = to_name;
        item.skip = skip;
        matched.push(item);

        let Some(pid) = pid else { continue };
        if from.is_unresolved()
            || to.is_unresolved()
            || transfer_type == TransferType::ShirtNumberUpdate
        {
            continue;
        }
        let (from_team, to_team) = (from.team(), to.team());

        if let (Some(source), Some(destination), Some(when)) = (from_team, to_team, when)
            && current_team == Some(source)
            && !is_loan
            && transfer_type != TransferType::EndOfLoan
        {
            history
                .permanent_routes
                .entry((pid, source))
                .or_default()
                .push((destination, when));
        }

        let can_move_from_parent = from_team
            .is_some_and(|source| history.loans.is_loaned(source, pid))
            && current_team.is_some();
        let live_at_destination = to_team
            .and_then(|team| live.live_squad_ids.get(&team))
            .is_some_and(|ids| ids.contains(&pid));

        // Later events see the roster as this event leaves it.
        match (from_team, to_team) {
            (Some(source), Some(destination)) => {
                if let Some(current) = current_team
                    && current != destination
                    && (current == source || can_move_from_parent || live_at_destination)
                {
                    sim.transfer(current, destination, pid);
                } else if current_clubs.is_empty() {
                    sim.add(destination, pid);
                }
            }
            (None, Some(destination)) => {
                if current_clubs.is_empty() {
                    sim.add(destination, pid);
                } else if let Some(current) = current_team
                    && current != destination
                    && (history.loans.is_loaned(current, pid) || live_at_destination)
                {
                    sim.transfer(current, destination, pid);
                }
            }
            (Some(source), None) if current_team == Some(source) => sim.remove(source, pid),
            _ => {}
        }

        if let Some(source) = from_team {
            history.loans.record(
                is_loan,
                transfer_type == TransferType::EndOfLoan,
                source,
                to_team,
                pid,
            );
        }
    }

    let mut report = MatchReport {
        live_squad_ids: live.live_squad_ids.clone(),
        ..MatchReport::default()
    };
    let coverage = build_snapshot_roster_coverage(
        matcher,
        input.threshold,
        &saved,
        input.club_ids,
        input.squad_snapshots,
        &book.fotmob_to_pes,
        input.club_identity,
        input.team_shirt_numbers,
    );
    append_current_squad_moves(
        &mut matched,
        matcher,
        &mut sim,
        input.club_ids,
        &identity_map,
        &live,
        input.club_identity,
        &coverage,
        &mut report.skipped,
    );
    append_current_squad_releases(&mut matched, &sim, &coverage, input.player_names);
    report.fotmob_player_ids = book.fotmob_to_pes;
    MatchOutput { matched, report }
}
