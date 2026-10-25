//! Team-name matching: aliases, exact, affix-cleaned and fuzzy steps.

use std::cmp::Ordering;
use std::collections::HashMap;

use fl_text::{clean_club_name, fold_text, fuzz, is_non_club};

use super::{MATCH_THRESHOLD_TEAM, MatchStatus, TeamMatch};

/// Best fuzzy club must beat the runner-up by this much.
const AMBIGUITY_MARGIN: f64 = 3.0;
/// Confidence of a match found through the affix-cleaned name.
const CLEANED_EXACT_CONFIDENCE: f64 = 98.0;

type Records = Vec<(String, u32)>;

pub(super) struct TeamDb {
    /// Normalized name → (name, id) records, ids unique per key.
    by_norm: HashMap<String, Records>,
    /// Normalized names in load order.
    names: Vec<String>,
    by_cleaned: HashMap<String, Records>,
    cleaned_names: Vec<String>,
    name_by_id: HashMap<u32, String>,
    /// Provider alias → save club name, by exact spelling and by folded spelling.
    aliases: HashMap<String, String>,
    folded_aliases: HashMap<String, String>,
}

fn push_unique(
    map: &mut HashMap<String, Records>,
    order: &mut Vec<String>,
    key: String,
    name: &str,
    id: u32,
) {
    let records = map.entry(key.clone()).or_insert_with(|| {
        order.push(key);
        Vec::new()
    });
    if !records.iter().any(|(_, existing)| *existing == id) {
        records.push((name.to_owned(), id));
    }
}

impl TeamDb {
    pub(super) fn new(teams: impl IntoIterator<Item = (String, u32)>) -> Self {
        let mut by_norm = HashMap::new();
        let mut names = Vec::new();
        let mut by_cleaned = HashMap::new();
        let mut cleaned_names = Vec::new();
        for (name, id) in teams {
            let norm = fold_text(&name);
            if norm.is_empty() {
                continue;
            }
            push_unique(&mut by_norm, &mut names, norm, &name, id);
            let cleaned = clean_club_name(&name);
            if !cleaned.is_empty() {
                push_unique(&mut by_cleaned, &mut cleaned_names, cleaned, &name, id);
            }
        }
        // The canonical name of an id is the first one in key load order.
        let mut name_by_id = HashMap::new();
        for key in &names {
            for (name, id) in &by_norm[key] {
                name_by_id.entry(*id).or_insert_with(|| name.clone());
            }
        }
        Self {
            by_norm,
            names,
            by_cleaned,
            cleaned_names,
            name_by_id,
            aliases: HashMap::new(),
            folded_aliases: HashMap::new(),
        }
    }

    pub(super) fn set_aliases(&mut self, aliases: impl IntoIterator<Item = (String, String)>) {
        self.aliases.clear();
        self.folded_aliases.clear();
        for (alias, target) in aliases {
            if alias.is_empty() || target.is_empty() {
                continue;
            }
            self.folded_aliases
                .insert(fold_text(&alias), target.clone());
            self.aliases.insert(alias, target);
        }
    }

    pub(super) fn name_of(&self, id: u32) -> Option<&str> {
        self.name_by_id.get(&id).map(String::as_str)
    }

    /// Two best `(score, name)` pairs by score, then name, descending.
    fn top_two(names: &[String], score: impl Fn(&str) -> f64) -> Vec<(f64, &str)> {
        let mut top: Vec<(f64, &str)> = Vec::with_capacity(3);
        for name in names {
            let entry = (score(name), name.as_str());
            let at = top.partition_point(|held| compare(held, &entry) == Ordering::Greater);
            if at < 2 {
                top.insert(at, entry);
                top.truncate(2);
            }
        }
        top
    }

    pub(super) fn match_team(&self, scraped: &str, threshold: Option<f64>) -> TeamMatch {
        let threshold = threshold
            .filter(|&t| t != 0.0)
            .unwrap_or(MATCH_THRESHOLD_TEAM);
        // These values deliberately mean "no roster": never let fuzzy matching
        // reinterpret one as a club with a similar name.
        if is_non_club(scraped) {
            return TeamMatch::unmatched(MatchStatus::NonClub, 100.0);
        }
        if self.names.is_empty() {
            tracing::warn!("Team database is empty - load it first");
            return TeamMatch::unmatched(MatchStatus::EmptyDatabase, 0.0);
        }

        // Step 1: provider aliases (exact spelling first, then folded).
        let alias_target = self
            .aliases
            .get(scraped)
            .or_else(|| self.folded_aliases.get(&fold_text(scraped)))
            .map(String::as_str);
        let query_name = alias_target.unwrap_or(scraped);
        let norm_query = fold_text(query_name);

        // Step 2: exact name.
        match self.by_norm.get(&norm_query).map(Vec::as_slice) {
            Some([(name, id)]) => return TeamMatch::matched(*id, name, 100.0),
            Some(many) if many.len() > 1 => {
                tracing::warn!("Ambiguous exact team name '{scraped}'; skipping");
                return TeamMatch::unmatched(MatchStatus::AmbiguousExact, 100.0);
            }
            _ => {}
        }

        // Step 3: exact name after stripping FC, CF, AC, ...
        let cleaned_query = clean_club_name(query_name);
        match self.by_cleaned.get(&cleaned_query).map(Vec::as_slice) {
            Some([(name, id)]) => {
                let confidence = if alias_target.is_some() {
                    100.0
                } else {
                    CLEANED_EXACT_CONFIDENCE
                };
                return TeamMatch::matched(*id, name, confidence);
            }
            Some(many) if many.len() > 1 => {
                tracing::warn!(
                    "Ambiguous cleaned team name '{scraped}' matches {} clubs; skipping",
                    many.len()
                );
                return TeamMatch::unmatched(
                    MatchStatus::AmbiguousCleaned,
                    CLEANED_EXACT_CONFIDENCE,
                );
            }
            _ => {}
        }

        // Step 4: fuzzy over standard names.
        let standard = Self::top_two(&self.names, |candidate| {
            club_score(&norm_query, candidate).max(fuzz::wratio(&norm_query, candidate))
        });
        let (mut best_conf, mut best_name) = standard.first().copied().unwrap_or((0.0, ""));
        let runner_up = standard.get(1).map_or(-1.0, |item| item.0);
        if best_conf >= threshold
            && best_conf - runner_up >= AMBIGUITY_MARGIN
            && let Some([(name, id)]) = self.by_norm.get(best_name).map(Vec::as_slice)
        {
            return TeamMatch::matched(*id, name, best_conf);
        }

        // Step 5: fuzzy over affix-cleaned names.
        if !cleaned_query.is_empty() && !self.cleaned_names.is_empty() {
            let cleaned = Self::top_two(&self.cleaned_names, |candidate| {
                club_score(&cleaned_query, candidate)
            });
            let (clean_conf, clean_name) = cleaned[0];
            let clean_runner = cleaned.get(1).map_or(-1.0, |item| item.0);
            if clean_conf > best_conf {
                (best_conf, best_name) = (clean_conf, clean_name);
            }
            if clean_conf >= threshold
                && clean_conf - clean_runner >= AMBIGUITY_MARGIN
                && let Some([(name, id)]) = self.by_cleaned.get(clean_name).map(Vec::as_slice)
            {
                return TeamMatch::matched(*id, name, clean_conf);
            }
        }
        tracing::debug!(
            "No team match for '{scraped}' (best: '{best_name}' at {best_conf:.0}%, threshold: {threshold}%)"
        );
        TeamMatch::unmatched(MatchStatus::BelowThreshold, best_conf)
    }
}

/// Order of `(score, name)` tuples: score, then name.
fn compare(a: &(f64, &str), b: &(f64, &str)) -> Ordering {
    a.0.total_cmp(&b.0).then_with(|| a.1.cmp(b.1))
}

/// Word-order and extra-word tolerant club similarity.
fn club_score(query: &str, candidate: &str) -> f64 {
    fuzz::token_set_ratio(query, candidate).max(fuzz::token_sort_ratio(query, candidate))
}
