//! Player-name matching: evidence scoring and the exact → roster → fuzzy cascade.
//!
//! Matching is a cascade of small steps over one [`Evidence`] value:
//!
//! 1. [`PlayerDb::resolve_exact`] narrows identical normalized names with roster
//!    context, nationality and age.
//! 2. [`PlayerDb::match_exact_name`] decides an exact-name query.
//! 3. [`PlayerDb::match_in_rosters`] scores fuzzy candidates inside the source /
//!    destination rosters (source first).
//! 4. [`PlayerDb::match_fuzzy`] scores every candidate and breaks name ties with
//!    metadata.
//!
//! Scores are composed from independent evidence functions (`name_score`,
//! `short_query_penalty`, `position_bonus`, `nationality_bonus`, `age_bonus`).

use std::collections::HashMap;

use fl_text::{TokenSetIndex, fold_text, fuzz, name_tokens};

use super::position::{self, Position};
use super::{
    MATCH_THRESHOLD_PLAYER, MatchStatus, PlayerAttrs, PlayerMatch, PlayerQuery, ScoreContext,
    nationality,
};

/// Every name that could reach the threshold after the metadata bonuses
/// (position +2, nationality +6, age +4) is a candidate.
const METADATA_BONUS_MAX: f64 = 12.0;
/// Roster-context matches below this confidence are rejected outright.
const CONTEXT_MIN_CONFIDENCE: f64 = 90.0;
/// Candidates closer than this are ambiguous.
const AMBIGUITY_MARGIN: f64 = 3.0;
/// Largest age gap between provider and database before a candidate is rejected.
const MAX_AGE_GAP: u32 = 4;

/// Suffix tokens that distinguish "Neymar Jr" from an arbitrary surname.
const NAME_SUFFIX_TOKENS: [&str; 10] = [
    "jr", "junior", "sr", "senior", "ii", "iii", "iv", "v", "filho", "neto",
];

/// One database name of one player (a player may have several, e.g. a legacy alias).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PlayerRecord {
    pub name: String,
    pub id: u32,
}

#[derive(Debug, Clone)]
struct Attr {
    position: Option<Position>,
    /// Folded; empty = unknown.
    nationality: String,
    /// 0 = unknown.
    age: u32,
}

/// Query metadata, parsed once per match.
struct Evidence {
    position: Option<Position>,
    /// Folded (possibly empty) when a nationality was supplied.
    nationality: Option<String>,
    /// 0 = unknown.
    age: u32,
}

impl Evidence {
    fn new(position: &str, nationality: &str, age: u32) -> Self {
        Self {
            position: Position::parse(position),
            nationality: (!nationality.is_empty()).then(|| fold_text(nationality)),
            age,
        }
    }
}

/// A scored candidate record.
#[derive(Debug, Clone, Copy)]
struct Scored<'a> {
    score: f64,
    name: &'a str,
    id: u32,
    /// [`suffix_preference`] of the candidate against the query.
    preference: u8,
}

pub(super) struct PlayerDb {
    by_norm: HashMap<String, Vec<PlayerRecord>>,
    index: TokenSetIndex,
    names_by_id: HashMap<u32, Vec<(String, String)>>,
    attrs: HashMap<u32, Attr>,
    no_names: Vec<(String, String)>,
}

impl PlayerDb {
    pub(super) fn new(
        players: impl IntoIterator<Item = (String, u32)>,
        attrs: HashMap<u32, PlayerAttrs>,
    ) -> Self {
        let mut by_norm: HashMap<String, Vec<PlayerRecord>> = HashMap::new();
        let mut order: Vec<String> = Vec::new();
        let mut names_by_id: HashMap<u32, Vec<(String, String)>> = HashMap::new();
        for (name, id) in players {
            let norm = fold_text(&name);
            if norm.is_empty() {
                continue;
            }
            let candidates = by_norm.entry(norm.clone()).or_insert_with(|| {
                order.push(norm.clone());
                Vec::new()
            });
            if !candidates.iter().any(|existing| existing.id == id) {
                candidates.push(PlayerRecord {
                    name: name.clone(),
                    id,
                });
            }
            names_by_id.entry(id).or_default().push((norm, name));
        }
        let attrs = attrs
            .into_iter()
            .map(|(id, attr)| {
                (
                    id,
                    Attr {
                        position: Position::parse(&attr.position),
                        nationality: fold_text(&attr.nationality),
                        age: attr.age,
                    },
                )
            })
            .collect();
        Self {
            by_norm,
            index: TokenSetIndex::new(order),
            names_by_id,
            attrs,
            no_names: Vec::new(),
        }
    }

    pub(super) fn is_empty(&self) -> bool {
        self.index.names().is_empty()
    }

    pub(super) fn exact_candidates(&self, folded: &str) -> &[PlayerRecord] {
        self.by_norm.get(folded).map_or(&[], Vec::as_slice)
    }

    pub(super) fn names_of(&self, id: u32) -> &[(String, String)] {
        self.names_by_id.get(&id).unwrap_or(&self.no_names)
    }

    pub(super) fn position_of(&self, id: u32) -> &str {
        self.attrs
            .get(&id)
            .and_then(|attr| attr.position.as_ref())
            .map_or("", Position::key)
    }

    // ── metadata gates ────────────────────────────────────────────────────

    pub(super) fn age_compatible(&self, id: u32, age: u32) -> bool {
        if age > 0 {
            let database_age = self.age_of(id);
            if database_age > 0 && age.abs_diff(database_age) > MAX_AGE_GAP {
                return false;
            }
        }
        true
    }

    fn age_of(&self, id: u32) -> u32 {
        self.attrs.get(&id).map_or(0, |attr| attr.age)
    }

    fn nationality_of(&self, id: u32) -> &str {
        self.attrs
            .get(&id)
            .map_or("", |attr| attr.nationality.as_str())
    }

    fn position_ref(&self, id: u32) -> Option<&Position> {
        self.attrs.get(&id).and_then(|attr| attr.position.as_ref())
    }

    /// Reject a same-name candidate with contradictory position or age.
    fn metadata_compatible(&self, id: u32, evidence: &Evidence) -> bool {
        if let (Some(wanted), Some(actual)) = (&evidence.position, self.position_ref(id))
            && !position::compatible(wanted, actual)
        {
            return false;
        }
        self.age_compatible(id, evidence.age)
    }

    pub(super) fn metadata_compatible_raw(&self, id: u32, position: &str, age: u32) -> bool {
        self.metadata_compatible(id, &Evidence::new(position, "", age))
    }

    // ── evidence scoring ──────────────────────────────────────────────────

    /// Fuzzy name similarity (best of three scorers) lifted by the token rules:
    /// shared prefix, shared first+last token, initial + surname.
    fn name_score(query: &str, candidate: &str) -> f64 {
        let base = fuzz::token_set_ratio(query, candidate)
            .max(fuzz::token_sort_ratio(query, candidate))
            .max(fuzz::wratio(query, candidate));
        let q = name_tokens(query);
        let c = name_tokens(candidate);
        let mut score = base;

        // Provider and local catalogs may disagree on suffix components
        // ("Neymar" vs "Neymar Jr") while preserving the same name prefix, and
        // feeds may insert a middle name while keeping first and last token.
        if !q.is_empty() && c.len() > q.len() && q[..] == c[..q.len()]
            || c.len() >= 2 && q.len() > c.len() && q[..c.len()] == c[..]
            || q.len() >= 2 && c.len() >= 2 && q[0] == c[0] && q[q.len() - 1] == c[c.len() - 1]
        {
            score = score.max(95.0);
        }

        // "k mbappe" vs "kylian mbappe".
        if q.len() >= 2 && c.len() >= 2 {
            let initial = q[0];
            if initial.chars().count() == 1
                && c[0].chars().next() == initial.chars().next()
                && q[q.len() - 1] == c[c.len() - 1]
            {
                score = score.max(90.0);
            }
        }
        score
    }

    /// Very short queries must not match long candidates convincingly.
    fn short_query_penalty(query: &str, candidate: &str, score: f64) -> f64 {
        let query_len = query.chars().count();
        if query_len < 5 && candidate.chars().count() as f64 >= query_len as f64 * 2.5 {
            score * 0.75
        } else {
            score
        }
    }

    /// +2 when provider and PES positions are on the same broad line.
    fn position_bonus(&self, id: u32, evidence: &Evidence) -> f64 {
        match (&evidence.position, self.position_ref(id)) {
            (Some(wanted), Some(actual)) if wanted.category() == actual.category() => 2.0,
            _ => 0.0,
        }
    }

    /// +6 when the provider nationality names the database nationality's country.
    fn nationality_bonus(&self, id: u32, evidence: &Evidence) -> f64 {
        let database = self.nationality_of(id);
        match &evidence.nationality {
            Some(wanted) if !database.is_empty() && nationality::matches(wanted, database) => 6.0,
            _ => 0.0,
        }
    }

    /// +4 when ages differ by at most one year.
    fn age_bonus(&self, id: u32, evidence: &Evidence) -> f64 {
        let database_age = self.age_of(id);
        if evidence.age > 0 && database_age > 0 && evidence.age.abs_diff(database_age) <= 1 {
            4.0
        } else {
            0.0
        }
    }

    /// Composite score of one candidate record (0 when metadata contradicts).
    fn score(&self, query: &str, candidate: &str, id: Option<u32>, evidence: &Evidence) -> f64 {
        if let Some(id) = id
            && !self.metadata_compatible(id, evidence)
        {
            return 0.0;
        }
        let mut score =
            Self::short_query_penalty(query, candidate, Self::name_score(query, candidate));
        // An id of 0 carries no evidence.
        if let Some(id) = id.filter(|&id| id != 0) {
            score = (score + self.position_bonus(id, evidence)).min(100.0);
            score = (score + self.nationality_bonus(id, evidence)).min(100.0);
            score = (score + self.age_bonus(id, evidence)).min(100.0);
        }
        score.min(100.0)
    }

    pub(super) fn score_public(&self, query: &str, candidate: &str, ctx: &ScoreContext) -> f64 {
        self.score(
            query,
            candidate,
            ctx.candidate_pid,
            &Evidence::new(ctx.position, ctx.nationality, ctx.age),
        )
    }

    // ── tie-breaking ──────────────────────────────────────────────────────

    /// Narrow equally-named candidates by nationality, then age. Both need
    /// complete coverage among the candidates; unknowns never lose to knowns.
    fn narrow_by_nationality_and_age<T: Copy>(
        &self,
        items: Vec<T>,
        id_of: impl Fn(&T) -> u32,
        evidence: &Evidence,
    ) -> Vec<T> {
        let mut items = items;
        if let Some(wanted) = &evidence.nationality
            && items.len() > 1
            && items
                .iter()
                .all(|i| !self.nationality_of(id_of(i)).is_empty())
        {
            let matching: Vec<T> = items
                .iter()
                .copied()
                .filter(|i| nationality::matches(wanted, self.nationality_of(id_of(i))))
                .collect();
            if !matching.is_empty() {
                items = matching;
            }
        }
        if evidence.age > 0 && items.len() > 1 && items.iter().all(|i| self.age_of(id_of(i)) > 0) {
            let gap = |i: &T| evidence.age.abs_diff(self.age_of(id_of(i)));
            let best = items.iter().map(gap).min().unwrap_or(0);
            items.retain(|i| gap(i) == best);
        }
        items
    }

    /// Same-name candidates → the one the context identifies, if any.
    fn resolve_exact<'a>(
        &self,
        records: &'a [PlayerRecord],
        rosters: &[&[u32]],
        evidence: &Evidence,
    ) -> Option<&'a PlayerRecord> {
        let mut compatible: Vec<&PlayerRecord> = records
            .iter()
            .filter(|c| self.metadata_compatible(c.id, evidence))
            .collect();
        for roster in rosters {
            let contextual: Vec<&PlayerRecord> = compatible
                .iter()
                .copied()
                .filter(|c| roster.contains(&c.id))
                .collect();
            if contextual.len() == 1 {
                return Some(contextual[0]);
            }
            if !contextual.is_empty() {
                compatible = contextual;
                break;
            }
        }
        let compatible = self.narrow_by_nationality_and_age(compatible, |c| c.id, evidence);
        (compatible.len() == 1).then(|| compatible[0])
    }

    /// Narrow name-tied fuzzy contenders by position, nationality and age.
    fn break_tie<'a>(&self, contenders: Vec<Scored<'a>>, evidence: &Evidence) -> Vec<Scored<'a>> {
        let mut contenders = contenders;
        if let Some(wanted) = &evidence.position
            && wanted.category() != position::Category::Unknown
        {
            let compatible: Vec<Scored> = contenders
                .iter()
                .copied()
                .filter(|item| {
                    self.position_ref(item.id)
                        .is_some_and(|actual| position::compatible(wanted, actual))
                })
                .collect();
            if !compatible.is_empty() {
                contenders = compatible;
            }
        }
        self.narrow_by_nationality_and_age(contenders, |item| item.id, evidence)
    }

    // ── cascade ───────────────────────────────────────────────────────────

    pub(super) fn match_player(&self, scraped: &str, query: &PlayerQuery) -> PlayerMatch {
        let threshold = if query.threshold == 0.0 {
            MATCH_THRESHOLD_PLAYER
        } else {
            query.threshold
        };
        if self.is_empty() {
            tracing::warn!("Player database is empty - load it first");
            return PlayerMatch::unmatched(MatchStatus::EmptyDatabase, 0.0);
        }
        let evidence = Evidence::new(query.position, query.nationality, query.age);
        // Source context is authoritative for a pending transfer; destination
        // context is only a fallback for an event the base already reflects.
        let rosters: Vec<&[u32]> = [query.from_team_id, query.to_team_id]
            .into_iter()
            .filter_map(|team| {
                let map = query.team_player_map?;
                map.get(&team?).map(Vec::as_slice)
            })
            .filter(|roster| !roster.is_empty())
            .collect();
        let norm_query = fold_text(scraped);

        if let Some(records) = self.by_norm.get(&norm_query)
            && let Some(outcome) = self.match_exact_name(scraped, records, &rosters, &evidence)
        {
            return outcome;
        }

        let candidate_names = self
            .index
            .extract(&norm_query, (threshold - METADATA_BONUS_MAX).max(0.0));
        let query_tokens = fold_text(&norm_query);
        let query_tokens = name_tokens(&query_tokens);
        if let Some(outcome) = self.match_in_rosters(
            scraped,
            &norm_query,
            &query_tokens,
            &candidate_names,
            &rosters,
            &evidence,
            threshold,
        ) {
            return outcome;
        }
        self.match_fuzzy(
            scraped,
            &norm_query,
            &query_tokens,
            &candidate_names,
            &evidence,
            threshold,
        )
    }

    /// Exact normalized-name hit. `None` = continue with fuzzy matching.
    fn match_exact_name(
        &self,
        scraped: &str,
        records: &[PlayerRecord],
        rosters: &[&[u32]],
        evidence: &Evidence,
    ) -> Option<PlayerMatch> {
        if let Some(chosen) = self.resolve_exact(records, rosters, evidence) {
            return Some(PlayerMatch::matched(chosen.id, &chosen.name, 100.0));
        }
        let compatible = records
            .iter()
            .filter(|c| self.metadata_compatible(c.id, evidence))
            .count();
        // A material age mismatch must not fall through to fuzzy matching and
        // re-introduce the same false identity.
        if compatible == 0
            && !records
                .iter()
                .any(|c| self.age_compatible(c.id, evidence.age))
        {
            tracing::warn!(
                "Rejecting exact player match '{scraped}': age conflicts with every candidate"
            );
            return Some(PlayerMatch::unmatched(MatchStatus::ExactAgeConflict, 100.0));
        }
        // A position-only mismatch may still resolve to a longer,
        // position-compatible fuzzy candidate; unresolved ties are unsafe.
        if compatible > 1 {
            tracing::warn!(
                "Ambiguous exact player name '{scraped}' matches {compatible} players; skipping without roster context"
            );
            return Some(PlayerMatch::unmatched(MatchStatus::AmbiguousExact, 100.0));
        }
        None
    }

    fn scored_candidates<'a>(
        &'a self,
        norm_query: &str,
        query_tokens: &[&str],
        candidate_names: &[(usize, f64)],
        evidence: &Evidence,
        only_in: Option<&[u32]>,
    ) -> Vec<Scored<'a>> {
        let names = self.index.names();
        let mut scored = Vec::new();
        for &(index, _) in candidate_names {
            let norm = &names[index];
            let Some(records) = self.by_norm.get(norm) else {
                continue;
            };
            let preference = suffix_preference(query_tokens, norm);
            for record in records {
                if only_in.is_some_and(|roster| !roster.contains(&record.id)) {
                    continue;
                }
                scored.push(Scored {
                    score: self.score(norm_query, norm, Some(record.id), evidence),
                    name: &record.name,
                    id: record.id,
                    preference,
                });
            }
        }
        // Best score first, then recognized suffix; stable for equal keys.
        scored.sort_by(|a, b| {
            b.score
                .total_cmp(&a.score)
                .then(b.preference.cmp(&a.preference))
        });
        scored
    }

    /// Score candidates inside each context roster; the first roster with a
    /// confident candidate decides. `None` = no roster decided.
    #[allow(clippy::too_many_arguments)]
    fn match_in_rosters(
        &self,
        scraped: &str,
        norm_query: &str,
        query_tokens: &[&str],
        candidate_names: &[(usize, f64)],
        rosters: &[&[u32]],
        evidence: &Evidence,
        threshold: f64,
    ) -> Option<PlayerMatch> {
        for roster in rosters {
            let scored = self.scored_candidates(
                norm_query,
                query_tokens,
                candidate_names,
                evidence,
                Some(roster),
            );
            let Some(top) = scored.first().filter(|top| top.score >= threshold) else {
                continue;
            };
            if top.score < CONTEXT_MIN_CONFIDENCE {
                tracing::warn!(
                    "Rejecting weak roster match '{scraped}': '{}' ({:.1})",
                    top.name,
                    top.score
                );
                return Some(PlayerMatch::unmatched(MatchStatus::WeakContext, top.score));
            }
            let runner_up = scored[1..].iter().find(|item| item.id != top.id);
            let clear = runner_up.is_none_or(|runner| {
                top.score - runner.score >= AMBIGUITY_MARGIN
                    || (top.preference > runner.preference && top.score == runner.score)
            });
            if clear {
                return Some(PlayerMatch::matched(top.id, top.name, top.score));
            }
            tracing::warn!(
                "Ambiguous roster match '{scraped}': '{}' ({:.1}); skipping",
                top.name,
                top.score
            );
            return Some(PlayerMatch::unmatched(
                MatchStatus::AmbiguousContext,
                top.score,
            ));
        }
        None
    }

    /// Global fuzzy match: name evidence picks the contenders, metadata only
    /// breaks ties inside the ambiguity margin.
    fn match_fuzzy(
        &self,
        scraped: &str,
        norm_query: &str,
        query_tokens: &[&str],
        candidate_names: &[(usize, f64)],
        evidence: &Evidence,
        threshold: f64,
    ) -> PlayerMatch {
        let scored =
            self.scored_candidates(norm_query, query_tokens, candidate_names, evidence, None);
        let Some(&best) = scored.first() else {
            return PlayerMatch::unmatched(MatchStatus::BelowThreshold, 0.0);
        };
        if best.score >= threshold {
            let contenders: Vec<Scored> = std::iter::once(best)
                .chain(scored[1..].iter().copied().filter(|item| {
                    item.id != best.id
                        && best.score - item.score < AMBIGUITY_MARGIN
                        && !(item.score == best.score && item.preference < best.preference)
                }))
                .collect();
            let contenders = self.break_tie(contenders, evidence);
            let chosen = contenders[0];
            match contenders[1..].iter().find(|item| item.id != chosen.id) {
                None if chosen.score >= threshold => {
                    return PlayerMatch::matched(chosen.id, chosen.name, chosen.score);
                }
                Some(runner) => {
                    tracing::warn!(
                        "Ambiguous player match '{scraped}': '{}' ({:.1}) vs '{}' ({:.1}); skipping",
                        chosen.name,
                        chosen.score,
                        runner.name,
                        runner.score
                    );
                    return PlayerMatch::unmatched(MatchStatus::AmbiguousFuzzy, best.score);
                }
                None => {}
            }
        }
        PlayerMatch::unmatched(MatchStatus::BelowThreshold, best.score)
    }
}

/// 1 when the query is one token and the candidate is that token followed only
/// by name suffixes ("Neymar" → "Neymar Jr"); such a candidate beats an
/// arbitrary surname.
fn suffix_preference(query_tokens: &[&str], candidate_norm: &str) -> u8 {
    let candidate = name_tokens(candidate_norm);
    u8::from(
        query_tokens.len() == 1
            && candidate.len() > 1
            && candidate[0] == query_tokens[0]
            && candidate[1..]
                .iter()
                .all(|token| NAME_SUFFIX_TOKENS.contains(token)),
    )
}
