//! Resolving club names and ids from transfer events to save clubs.

use std::collections::{HashMap, HashSet};

use fl_sources::model::Transfer;
use fl_text::{NON_CLUB_LABELS, casefold, strip};

use super::ports::{ClubIdentity, Resolution, TeamMatcher};
use super::types::Side;

/// A club-name match at or above this confidence is trusted outright.
pub const STRONG_TEAM_NAME_SCORE: f64 = 98.0;

/// Below the strong score but at or above this, a name is "similar to some
/// save club, none uniquely": unsure rather than outside.
/// (Same value as the matcher's team threshold.)
const UNSURE_TEAM_NAME_SCORE: f64 = 75.0;

/// A resolved transfer side.
#[derive(Debug, Clone, PartialEq)]
pub struct TeamResolution {
    pub side: Side,
    pub name: String,
    pub confidence: f64,
}

impl TeamResolution {
    fn outside(confidence: f64) -> Self {
        Self {
            side: Side::Outside,
            name: String::new(),
            confidence,
        }
    }

    fn unresolved(confidence: f64) -> Self {
        Self {
            side: Side::Unresolved,
            name: String::new(),
            confidence,
        }
    }
}

/// `strip().casefold()` is a non-club label (free agent, retired, ...).
pub(super) fn is_non_club_label(name: &str) -> bool {
    NON_CLUB_LABELS.contains(&casefold(strip(name)).as_str())
}

fn is_blank(name: &str) -> bool {
    strip(name).is_empty()
}

/// Resolve a club via learned FotMob bindings, then selected-save names.
fn resolve_identity_team(
    matcher: &dyn TeamMatcher,
    identity: &dyn ClubIdentity,
    fotmob_id: Option<i64>,
    names: &[String],
) -> TeamResolution {
    if let Some(fotmob_id) = fotmob_id
        && let Some(team_id) = identity.pes_for_fotmob(fotmob_id)
    {
        return TeamResolution {
            side: Side::Team(team_id),
            name: names.first().cloned().unwrap_or_default(),
            confidence: 100.0,
        };
    }

    let mut resolved_ids: Vec<u32> = Vec::new();
    let mut unsure = false;
    for name in names {
        match identity.resolve_name(name) {
            Resolution::Unresolved => unsure = true,
            Resolution::Outside => {}
            Resolution::Team(resolved) => {
                let bound = identity.fotmob_for_pes(resolved);
                if let (Some(fotmob_id), Some(bound)) = (fotmob_id, bound)
                    && bound != fotmob_id
                {
                    // The name points at a save club already bound to another
                    // provider club: a different (possibly reserve) club.
                    unsure = true;
                } else if !resolved_ids.contains(&resolved) {
                    resolved_ids.push(resolved);
                }
            }
        }
    }
    if resolved_ids.len() > 1 {
        resolved_ids.sort_unstable();
        tracing::warn!("Conflicting club identities for {names:?}: {resolved_ids:?}");
        return TeamResolution::unresolved(0.0);
    }
    if let Some(&team_id) = resolved_ids.first() {
        let display = matcher.team_name(team_id);
        let name = if display.is_empty() {
            names.first().cloned().unwrap_or_default()
        } else {
            display.to_owned()
        };
        return TeamResolution {
            side: Side::Team(team_id),
            name,
            confidence: 100.0,
        };
    }
    if unsure {
        return TeamResolution::unresolved(0.0);
    }
    TeamResolution::outside(100.0)
}

/// Resolve a transfer club to a save club, [`Side::Outside`], or
/// [`Side::Unresolved`].
///
/// A weak or unvalidated name never becomes "outside the save": that would
/// turn a move into a save club into a release. It is `Unresolved` instead.
pub fn match_transfer_team(
    matcher: &dyn TeamMatcher,
    short_name: &str,
    full_name: &str,
    fotmob_id: Option<i64>,
    identity: Option<&dyn ClubIdentity>,
) -> TeamResolution {
    if [full_name, short_name]
        .into_iter()
        .any(|name| !is_blank(name) && is_non_club_label(name))
    {
        return TeamResolution::outside(100.0);
    }

    let mut names: Vec<String> = Vec::new();
    for value in [full_name, short_name] {
        let clean = strip(value);
        if clean.is_empty() {
            continue;
        }
        let folded = casefold(clean);
        if !names.iter().any(|existing| casefold(existing) == folded) {
            names.push(clean.to_owned());
        }
    }

    if let Some(identity) = identity {
        return resolve_identity_team(matcher, identity, fotmob_id, &names);
    }

    let results: Vec<_> = names
        .iter()
        .map(|name| matcher.match_team(name, None))
        .collect();
    let mut matched_ids: Vec<u32> = results.iter().filter_map(|hit| hit.id).collect();
    matched_ids.sort_unstable();
    matched_ids.dedup();
    let best_confidence = results
        .iter()
        .map(|hit| hit.confidence)
        .fold(None, |best: Option<f64>, c| {
            Some(best.map_or(c, |b| b.max(c)))
        });
    if matched_ids.len() > 1 {
        tracing::warn!("Conflicting club identities for {names:?}: {matched_ids:?}");
        return TeamResolution::unresolved(best_confidence.unwrap_or(0.0));
    }
    if let Some(&team_id) = matched_ids.first() {
        // The first hit with the maximal confidence wins ties.
        let mut best = None::<&super::ports::TeamHit>;
        for hit in results.iter().filter(|hit| hit.id == Some(team_id)) {
            if best.is_none_or(|current| hit.confidence > current.confidence) {
                best = Some(hit);
            }
        }
        let best = best.expect("a hit carries the matched id");
        if best.confidence < STRONG_TEAM_NAME_SCORE {
            let label = if full_name.is_empty() {
                short_name
            } else {
                full_name
            };
            tracing::warn!(
                "Rejecting weak club name match for {label} at {:.1}%",
                best.confidence
            );
            return TeamResolution::unresolved(best.confidence);
        }
        return TeamResolution {
            side: Side::Team(team_id),
            name: best.name.clone(),
            confidence: best.confidence,
        };
    }
    let confidence = best_confidence.unwrap_or(0.0);
    if confidence >= UNSURE_TEAM_NAME_SCORE {
        // Similar save clubs exist but none uniquely: unsure, not outside.
        return TeamResolution::unresolved(confidence);
    }
    TeamResolution::outside(confidence)
}

/// Map one live FotMob club to a save club, or `None` when not provably one.
pub fn resolve_snapshot_club(
    matcher: &dyn TeamMatcher,
    identity: Option<&dyn ClubIdentity>,
    fotmob_team_id: i64,
    club_name: &str,
) -> Option<u32> {
    if let Some(identity) = identity {
        if let Some(team_id) = identity.pes_for_fotmob(fotmob_team_id) {
            return Some(team_id);
        }
        let Resolution::Team(resolved) = identity.resolve_name(club_name) else {
            return None;
        };
        let bound = identity.fotmob_for_pes(resolved);
        return (bound.is_none() || bound == Some(fotmob_team_id)).then_some(resolved);
    }
    let hit = matcher.match_team(club_name, Some(STRONG_TEAM_NAME_SCORE));
    if hit.confidence >= STRONG_TEAM_NAME_SCORE {
        hit.id
    } else {
        None
    }
}

/// Selected-save club knowledge: which transfer sides touch the save.
pub struct SaveClubs<'a> {
    pub identity: &'a dyn ClubIdentity,
    pub club_ids: &'a HashSet<u32>,
}

impl SaveClubs<'_> {
    /// One transfer side: a save club, `Outside`, or `Unresolved`.
    pub fn save_club(&self, names: &[&str], fotmob_id: Option<i64>) -> Side {
        if let Some(bound) = fotmob_id.and_then(|id| self.identity.pes_for_fotmob(id)) {
            return if self.club_ids.contains(&bound) {
                Side::Team(bound)
            } else {
                Side::Outside
            };
        }
        let mut unsure = false;
        for name in names {
            let clean = strip(name);
            if NON_CLUB_LABELS.contains(&casefold(clean).as_str()) {
                continue;
            }
            match self.identity.resolve_name(clean) {
                Resolution::Unresolved => unsure = true,
                Resolution::Team(resolved) if self.club_ids.contains(&resolved) => {
                    return Side::Team(resolved);
                }
                _ => {}
            }
        }
        if unsure {
            Side::Unresolved
        } else {
            Side::Outside
        }
    }

    /// Either side is (or may be) a club of the selected save.
    pub fn touches_save(&self, transfer: &Transfer) -> bool {
        let to = self.save_club(
            &[&transfer.to_club_full_name, &transfer.to_club],
            transfer.to_club_id_fotmob,
        );
        let from = self.save_club(
            &[&transfer.from_club_full_name, &transfer.from_club],
            transfer.from_club_id_fotmob,
        );
        !to.is_outside() || !from.is_outside()
    }
}

/// FotMob club id → save team id for every identity binding.
pub fn fotmob_team_map(identity: &dyn ClubIdentity) -> HashMap<i64, u32> {
    identity.bindings().into_iter().collect()
}
