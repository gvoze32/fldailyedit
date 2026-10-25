//! Per-save FotMob ↔ save club identity.
//!
//! Bindings come from two kinds of evidence only:
//!
//! * an unambiguous name pass between the save's clubs and the crawled FotMob
//!   team catalog ([`fl_sources::team_index`]), and
//! * squad overlap: a complete FotMob current squad whose players sit on one
//!   save club's roster. Overlap bindings are cached per save scope and win
//!   over name bindings.
//!
//! Building the index only reads the cache. [`ClubIdentityIndex::save`] is the
//! one place that writes it; callers skip it on dry runs.

use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::io;
use std::path::PathBuf;

use fl_fs::write_atomic;
use fl_sources::model::SquadSnapshot;
use fl_sources::team_index::TeamEntry;
use fl_text::{
    TokenSetIndex, categories_compatible, clean_club_name, fold_text, fuzz, is_non_club_key,
};
use serde_json::{Map, Value};

use crate::matcher::{MATCH_THRESHOLD_TEAM, NameMatcher, PlayerQuery};

/// Name-pass fuzzy candidates must reach `MATCH_THRESHOLD - FUZZY_MARGIN`.
const MATCH_THRESHOLD: f64 = 90.0;
const FUZZY_MARGIN: f64 = 5.0;
/// `resolve_name` trusts a club-name match only from this confidence.
const CONFIDENT_NAME_SCORE: f64 = 98.0;
const OVERLAP_MIN_SHARE: f64 = 0.75;
const OVERLAP_MIN_MEMBERS: usize = 11;
const OVERLAP_RUNNER_UP_FACTOR: usize = 3;
const OVERLAP_PLAYER_CONFIDENCE: f64 = 90.0;
/// FotMob's long-lived senior identities use ids below this; later
/// women/youth/duplicate sitemap identities use much larger ones.
const LEGACY_FOTMOB_ID_LIMIT: i64 = 200_000;

/// Where one save scope's learned bindings are stored.
#[derive(Debug, Clone)]
pub struct IdentityCache {
    /// `club_identity_cache.json` under the state directory.
    pub path: PathBuf,
    /// Identifies the save the bindings belong to.
    pub save_scope: String,
}

impl IdentityCache {
    pub fn new(path: PathBuf, save_scope: String) -> Self {
        Self { path, save_scope }
    }
}

/// How a binding was established.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum IdentitySource {
    UnambiguousName,
    SquadOverlap,
}

impl IdentitySource {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::UnambiguousName => "unambiguous_name",
            Self::SquadOverlap => "squad_overlap",
        }
    }
}

/// One FotMob club bound to one save club.
#[derive(Debug, Clone, PartialEq)]
pub struct IdentityEntry {
    pub fotmob_id: i64,
    pub pes_team_id: u32,
    pub name: String,
    pub slug: String,
    pub url: String,
    /// Name similarity (name pass) or overlap share in percent, one decimal.
    pub match_score: f64,
    pub source: IdentitySource,
}

/// Verdict on a club name.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum NameResolution {
    /// Might be a save club, but not provably one.
    Unresolved,
    /// Confidently not a save club (free agent, unknown outside club).
    Outside,
    Team(u32),
}

/// Save roster evidence for squad-overlap learning, prepared once per save.
pub struct RosterEvidence {
    /// Player → save clubs rostering them.
    owners: HashMap<u32, Vec<u32>>,
    matcher: NameMatcher,
}

pub struct ClubIdentityIndex {
    pes_clubs: BTreeMap<u32, String>,
    catalog: HashMap<i64, TeamEntry>,
    cache: IdentityCache,
    by_pes: HashMap<u32, IdentityEntry>,
    by_fotmob: HashMap<i64, u32>,
    learned: BTreeMap<i64, u32>,
    provider_names: HashMap<i64, BTreeSet<String>>,
    alias_ids: HashMap<String, BTreeSet<u32>>,
    clean_alias_ids: HashMap<String, BTreeSet<u32>>,
    matcher: NameMatcher,
    pes_folds: HashMap<String, BTreeSet<u32>>,
}

/// Rounds to one decimal on the exact binary value, ties to even.
fn round1(value: f64) -> f64 {
    format!("{value:.1}").parse().unwrap_or(value)
}

impl ClubIdentityIndex {
    /// Name pass first, then cached overlap bindings on top. Never writes.
    pub fn build(
        pes_clubs: &BTreeMap<u32, String>,
        catalog: &[TeamEntry],
        cache: IdentityCache,
    ) -> Self {
        let mut index = Self::empty(pes_clubs, catalog, cache);
        for entry in name_bindings(pes_clubs, catalog) {
            index.bind(entry);
        }
        for (fotmob_id, pes_team_id) in load_cache(&index.cache) {
            if !index.pes_clubs.contains_key(&pes_team_id) {
                tracing::warn!(
                    "Dropping cached club binding FotMob {fotmob_id} -> PES {pes_team_id}: club not in save"
                );
                continue;
            }
            let entry = index.overlap_entry(fotmob_id, pes_team_id, "", "", 100.0);
            index.bind(entry);
            index.learned.insert(fotmob_id, pes_team_id);
        }
        index.rebuild_aliases();
        tracing::info!(
            "Club identity index: {}/{} save clubs bound ({} learned from squad overlap)",
            index.by_pes.len(),
            index.pes_clubs.len(),
            index.learned.len()
        );
        index
    }

    fn empty(
        pes_clubs: &BTreeMap<u32, String>,
        catalog: &[TeamEntry],
        cache: IdentityCache,
    ) -> Self {
        let mut pes_folds: HashMap<String, BTreeSet<u32>> = HashMap::new();
        for (id, name) in pes_clubs {
            pes_folds.entry(fold_text(name)).or_default().insert(*id);
        }
        Self {
            pes_clubs: pes_clubs.clone(),
            catalog: catalog.iter().map(|t| (t.fotmob_id, t.clone())).collect(),
            cache,
            by_pes: HashMap::new(),
            by_fotmob: HashMap::new(),
            learned: BTreeMap::new(),
            provider_names: HashMap::new(),
            alias_ids: HashMap::new(),
            clean_alias_ids: HashMap::new(),
            matcher: NameMatcher::new(
                std::iter::empty(),
                HashMap::new(),
                pes_clubs.iter().map(|(id, name)| (name.clone(), *id)),
            ),
            pes_folds,
        }
    }

    // ── binding ───────────────────────────────────────────────────────────

    fn bind(&mut self, entry: IdentityEntry) {
        if let Some(previous_pes) = self.by_fotmob.remove(&entry.fotmob_id) {
            self.by_pes.remove(&previous_pes);
        }
        if let Some(previous) = self.by_pes.remove(&entry.pes_team_id) {
            self.by_fotmob.remove(&previous.fotmob_id);
            self.learned.remove(&previous.fotmob_id);
        }
        self.by_fotmob.insert(entry.fotmob_id, entry.pes_team_id);
        self.by_pes.insert(entry.pes_team_id, entry);
    }

    fn overlap_entry(
        &self,
        fotmob_id: i64,
        pes_team_id: u32,
        name: &str,
        url: &str,
        score: f64,
    ) -> IdentityEntry {
        let (entry_name, slug, entry_url) = match self.catalog.get(&fotmob_id) {
            Some(team) => (team.name.clone(), team.slug.clone(), team.url.clone()),
            None => (
                if name.is_empty() {
                    self.pes_clubs[&pes_team_id].clone()
                } else {
                    name.to_owned()
                },
                String::new(),
                if url.is_empty() {
                    format!("https://www.fotmob.com/teams/{fotmob_id}/overview")
                } else {
                    url.to_owned()
                },
            ),
        };
        IdentityEntry {
            fotmob_id,
            pes_team_id,
            name: entry_name,
            slug,
            url: entry_url,
            match_score: round1(score),
            source: IdentitySource::SquadOverlap,
        }
    }

    fn apply_learned(&mut self, fotmob_id: i64, pes_team_id: u32, entry: IdentityEntry) {
        let current = self.by_fotmob.get(&fotmob_id).copied();
        let holder = self.by_pes.get(&pes_team_id).map(|e| e.fotmob_id);
        if current.is_some_and(|c| c != pes_team_id) || holder.is_some_and(|h| h != fotmob_id) {
            tracing::warn!(
                "Squad overlap rebinds FotMob {fotmob_id} to PES {pes_team_id} (was FotMob->PES {current:?}, PES held FotMob {holder:?})"
            );
        }
        self.bind(entry);
        self.learned.insert(fotmob_id, pes_team_id);
        self.rebuild_aliases();
    }

    /// Names an entry is known by: catalog name, slug words, provider names.
    fn entry_names(&self, entry: &IdentityEntry) -> BTreeSet<String> {
        let mut names = BTreeSet::from([entry.name.clone()]);
        if !entry.slug.is_empty() {
            names.insert(entry.slug.replace('-', " "));
        }
        if let Some(provider) = self.provider_names.get(&entry.fotmob_id) {
            names.extend(provider.iter().cloned());
        }
        names
    }

    fn rebuild_aliases(&mut self) {
        let mut alias_ids: HashMap<String, BTreeSet<u32>> = HashMap::new();
        let mut clean_alias_ids: HashMap<String, BTreeSet<u32>> = HashMap::new();
        for (&pes_team_id, entry) in &self.by_pes {
            for alias in self.entry_names(entry) {
                let folded = fold_text(&alias);
                if folded.is_empty() || !self.is_only_pes_fold(&folded, pes_team_id) {
                    continue;
                }
                alias_ids.entry(folded).or_default().insert(pes_team_id);
                let cleaned = clean_club_name(&alias);
                if !cleaned.is_empty() {
                    clean_alias_ids
                        .entry(cleaned)
                        .or_default()
                        .insert(pes_team_id);
                }
            }
        }
        self.alias_ids = alias_ids;
        self.clean_alias_ids = clean_alias_ids;
    }

    /// No other save club is literally named `folded`.
    fn is_only_pes_fold(&self, folded: &str, pes_team_id: u32) -> bool {
        self.pes_folds
            .get(folded)
            .is_none_or(|ids| ids.len() == 1 && ids.contains(&pes_team_id))
    }

    // ── lookups ───────────────────────────────────────────────────────────

    pub fn pes_for_fotmob(&self, fotmob_id: i64) -> Option<u32> {
        self.by_fotmob.get(&fotmob_id).copied()
    }

    pub fn fotmob_for_pes(&self, pes_team_id: u32) -> Option<i64> {
        self.by_pes.get(&pes_team_id).map(|e| e.fotmob_id)
    }

    /// Verdict on a club name: a save club, provably outside the save, or unsure.
    pub fn resolve_name(&self, name: &str) -> NameResolution {
        let clean = fl_text::strip(name);
        let folded = fold_text(clean);
        if is_non_club_key(&folded) {
            return NameResolution::Outside;
        }
        let hits = self
            .alias_ids
            .get(&folded)
            .or_else(|| self.clean_alias_ids.get(&clean_club_name(clean)));
        if let Some(hits) = hits {
            return match (hits.len(), hits.first()) {
                (1, Some(&id)) => NameResolution::Team(id),
                _ => NameResolution::Unresolved,
            };
        }
        let found = self.matcher.match_team(clean, None);
        match found.id {
            Some(id) if found.confidence >= CONFIDENT_NAME_SCORE => NameResolution::Team(id),
            Some(_) => NameResolution::Unresolved,
            None if found.confidence >= MATCH_THRESHOLD_TEAM => NameResolution::Unresolved,
            None => NameResolution::Outside,
        }
    }

    /// Bindings sorted by FotMob id.
    pub fn entries(&self) -> Vec<IdentityEntry> {
        let mut entries: Vec<IdentityEntry> = self.by_pes.values().cloned().collect();
        entries.sort_by_key(|e| e.fotmob_id);
        entries
    }

    /// Bindings learned from squad overlap (FotMob id → save club), the part that is cached.
    pub fn learned(&self) -> &BTreeMap<i64, u32> {
        &self.learned
    }

    /// Provider-name aliases → save club name, sorted by alias, for
    /// [`NameMatcher::set_team_aliases`].
    pub fn aliases(&self) -> Vec<(String, String)> {
        let mut aliases = BTreeMap::new();
        for (&pes_team_id, entry) in &self.by_pes {
            let mut names = BTreeSet::from([entry.name.clone()]);
            if let Some(provider) = self.provider_names.get(&entry.fotmob_id) {
                names.extend(provider.iter().cloned());
            }
            for alias in names {
                let folded = fold_text(&alias);
                let owned_alone = self
                    .alias_ids
                    .get(&folded)
                    .is_some_and(|ids| ids.len() == 1 && ids.contains(&pes_team_id));
                if !folded.is_empty() && owned_alone {
                    aliases.insert(alias, self.pes_clubs[&pes_team_id].clone());
                }
            }
        }
        aliases.into_iter().collect()
    }

    // ── learning ──────────────────────────────────────────────────────────

    /// Index the save's rosters: who owns each player, and a player matcher over
    /// rostered players. Only rosters of this index's clubs count.
    pub fn prepare_rosters(
        &self,
        rosters: &HashMap<u32, Vec<u32>>,
        player_names: &HashMap<u32, String>,
    ) -> RosterEvidence {
        let mut owners: HashMap<u32, Vec<u32>> = HashMap::new();
        for (&team_id, players) in rosters {
            if !self.pes_clubs.contains_key(&team_id) {
                continue;
            }
            for &player in players {
                let teams = owners.entry(player).or_default();
                if !teams.contains(&team_id) {
                    teams.push(team_id);
                }
            }
        }
        let mut rostered: Vec<u32> = owners.keys().copied().collect();
        rostered.sort_unstable();
        let records: Vec<(String, u32)> = rostered
            .into_iter()
            .filter_map(|id| {
                let name = player_names.get(&id).filter(|n| !n.is_empty())?;
                Some((name.clone(), id))
            })
            .collect();
        RosterEvidence {
            owners,
            matcher: NameMatcher::new(records, HashMap::new(), std::iter::empty()),
        }
    }

    /// Bind a FotMob club to the one save club its complete current squad overlaps.
    pub fn learn_from_snapshot(
        &mut self,
        snapshot: &SquadSnapshot,
        evidence: &RosterEvidence,
    ) -> Option<u32> {
        let fotmob_id = snapshot.team_id_fotmob;
        if fotmob_id <= 0 || !snapshot.complete || snapshot.members.len() < OVERLAP_MIN_MEMBERS {
            return None;
        }
        if !snapshot.club_name.is_empty() {
            self.provider_names
                .entry(fotmob_id)
                .or_default()
                .insert(snapshot.club_name.clone());
        }
        if evidence.owners.is_empty() {
            return None;
        }

        let mut counts: HashMap<u32, usize> = HashMap::new();
        let mut matched = 0usize;
        let mut seen = std::collections::HashSet::new();
        for member in &snapshot.members {
            let found = evidence.matcher.match_player(
                &member.player_name,
                &PlayerQuery {
                    threshold: OVERLAP_PLAYER_CONFIDENCE,
                    nationality: &member.nationality,
                    age: member.age,
                    ..PlayerQuery::default()
                },
            );
            let Some(player_id) = found.id else { continue };
            if found.confidence < OVERLAP_PLAYER_CONFIDENCE || !seen.insert(player_id) {
                continue;
            }
            let [team] = evidence
                .owners
                .get(&player_id)
                .map_or(&[][..], Vec::as_slice)
            else {
                continue;
            };
            matched += 1;
            *counts.entry(*team).or_default() += 1;
        }
        let mut ranked: Vec<(u32, usize)> = counts.into_iter().collect();
        ranked.sort_by(|a, b| b.1.cmp(&a.1).then(a.0.cmp(&b.0)));
        let &(best_team, best_count) = ranked.first()?;
        let runner_count = ranked.get(1).map_or(0, |r| r.1);
        if best_count < OVERLAP_MIN_MEMBERS
            || (best_count as f64) < OVERLAP_MIN_SHARE * matched as f64
            || best_count < OVERLAP_RUNNER_UP_FACTOR * runner_count
            || !categories_compatible(&self.pes_clubs[&best_team], &snapshot.club_name)
        {
            tracing::info!(
                "FotMob club {} ({fotmob_id}) squad overlap inconclusive: best PES {best_team} {best_count}/{matched}, runner-up {runner_count}",
                snapshot.club_name
            );
            return None;
        }
        let entry = self.overlap_entry(
            fotmob_id,
            best_team,
            &snapshot.club_name,
            &snapshot.source_url,
            100.0 * best_count as f64 / matched as f64,
        );
        self.apply_learned(fotmob_id, best_team, entry);
        Some(best_team)
    }

    // ── persistence ───────────────────────────────────────────────────────

    /// Persist this scope's learned bindings (atomic; other scopes are kept).
    ///
    /// The only write the index ever makes: callers skip it on dry runs.
    pub fn save(&self) -> io::Result<()> {
        let mut payload = match std::fs::read_to_string(&self.cache.path) {
            Ok(text) => match serde_json::from_str::<Value>(&text) {
                Ok(Value::Object(map)) => map,
                Ok(_) => Map::new(),
                Err(error) => {
                    tracing::warn!(
                        "Replacing unreadable club identity cache {}: {error}",
                        self.cache.path.display()
                    );
                    Map::new()
                }
            },
            Err(error) if error.kind() == io::ErrorKind::NotFound => Map::new(),
            Err(error) => {
                tracing::warn!(
                    "Replacing unreadable club identity cache {}: {error}",
                    self.cache.path.display()
                );
                Map::new()
            }
        };
        let scope: Map<String, Value> = self
            .learned
            .iter()
            .map(|(fotmob_id, pes)| (fotmob_id.to_string(), Value::from(*pes)))
            .collect();
        payload.insert(self.cache.save_scope.clone(), Value::Object(scope));
        let mut text = serde_json::to_string_pretty(&sorted(Value::Object(payload)))
            .map_err(io::Error::other)?;
        text.push('\n');
        write_atomic(&self.cache.path, text.as_bytes())
    }
}

/// Recursively sorts object keys.
fn sorted(value: Value) -> Value {
    match value {
        Value::Object(map) => {
            let ordered: BTreeMap<String, Value> =
                map.into_iter().map(|(k, v)| (k, sorted(v))).collect();
            Value::Object(ordered.into_iter().collect())
        }
        Value::Array(items) => Value::Array(items.into_iter().map(sorted).collect()),
        other => other,
    }
}

/// Learned bindings of the cache's scope, in file order. Unreadable files and
/// malformed entries are ignored.
fn load_cache(cache: &IdentityCache) -> Vec<(i64, u32)> {
    let text = match std::fs::read_to_string(&cache.path) {
        Ok(text) => text,
        Err(error) => {
            if error.kind() != io::ErrorKind::NotFound {
                tracing::warn!(
                    "Ignoring unreadable club identity cache {}: {error}",
                    cache.path.display()
                );
            }
            return Vec::new();
        }
    };
    let payload: Value = match serde_json::from_str(&text) {
        Ok(payload) => payload,
        Err(error) => {
            tracing::warn!(
                "Ignoring unreadable club identity cache {}: {error}",
                cache.path.display()
            );
            return Vec::new();
        }
    };
    let Some(Value::Object(scoped)) = payload.get(&cache.save_scope) else {
        return Vec::new();
    };
    let mut learned = Vec::new();
    for (raw_fotmob_id, raw_pes_id) in scoped {
        let parsed = raw_fotmob_id
            .trim()
            .parse::<i64>()
            .ok()
            .zip(match raw_pes_id {
                Value::Number(n) => n.as_i64().or_else(|| n.as_f64().map(|f| f as i64)),
                Value::String(s) => s.trim().parse::<i64>().ok(),
                _ => None,
            });
        match parsed.and_then(|(f, p)| Some((f, u32::try_from(p).ok()?))) {
            Some(pair) => learned.push(pair),
            None => {
                tracing::warn!("Ignoring malformed club identity cache entry {raw_fotmob_id:?}")
            }
        }
    }
    learned
}

// ── name pass ─────────────────────────────────────────────────────────────

/// Substantive token link for fuzzy-only matches: a shared token of 3+
/// characters, or one token being a 4+ character prefix of another.
fn has_meaningful_fuzzy_overlap(pes_name: &str, fotmob_name: &str) -> bool {
    let pes = clean_club_name(pes_name);
    let fotmob = clean_club_name(fotmob_name);
    let pes_tokens: BTreeSet<&str> = pes.split_whitespace().collect();
    let fotmob_tokens: BTreeSet<&str> = fotmob.split_whitespace().collect();
    if pes_tokens
        .intersection(&fotmob_tokens)
        .any(|token| token.chars().count() >= 3)
    {
        return true;
    }
    pes_tokens.iter().any(|&a| {
        fotmob_tokens.iter().any(|&b| {
            let (shorter, longer) = if a.chars().count() <= b.chars().count() {
                (a, b)
            } else {
                (b, a)
            };
            shorter.chars().count() >= 4 && longer.starts_with(shorter)
        })
    })
}

#[derive(Clone, Copy)]
struct Proposal {
    /// 3 = identical folded name, 2 = identical after affix removal, 1 = fuzzy.
    tier: u8,
    score: f64,
    index: usize,
}

/// One-to-one save club → FotMob club mappings that names alone prove.
fn name_bindings(pes_clubs: &BTreeMap<u32, String>, catalog: &[TeamEntry]) -> Vec<IdentityEntry> {
    let folded_names: Vec<String> = catalog.iter().map(|t| fold_text(&t.name)).collect();
    let mut by_fold: HashMap<&str, Vec<usize>> = HashMap::new();
    let mut by_clean: HashMap<String, Vec<usize>> = HashMap::new();
    for (index, team) in catalog.iter().enumerate() {
        by_fold.entry(&folded_names[index]).or_default().push(index);
        by_clean
            .entry(clean_club_name(&team.name))
            .or_default()
            .push(index);
    }
    let token_set_index = TokenSetIndex::new(folded_names.iter().map(String::as_str));
    let cutoff = MATCH_THRESHOLD - FUZZY_MARGIN;

    let mut proposals: Vec<(u32, f64, usize)> = Vec::new();
    for (&pes_team_id, pes_name) in pes_clubs {
        let norm_pes = fold_text(pes_name);
        let mut tiers: HashMap<usize, (u8, f64)> = HashMap::new();
        for &index in by_fold.get(norm_pes.as_str()).into_iter().flatten() {
            tiers.insert(index, (3, 100.0));
        }
        for &index in by_clean
            .get(&clean_club_name(pes_name))
            .into_iter()
            .flatten()
        {
            tiers.entry(index).or_insert((2, 98.0));
        }
        let mut fuzzy: HashMap<usize, f64> = HashMap::new();
        let mut take = |index: usize, score: f64| {
            let held = fuzzy.entry(index).or_insert(0.0);
            *held = held.max(score);
        };
        for (index, score) in token_set_index.extract(&norm_pes, cutoff) {
            take(index, score);
        }
        for (index, name) in folded_names.iter().enumerate() {
            for score in [
                fuzz::token_sort_ratio_with_cutoff(&norm_pes, name, cutoff),
                fuzz::wratio_with_cutoff(&norm_pes, name, cutoff),
            ] {
                if score >= cutoff {
                    take(index, score);
                }
            }
        }
        for (index, score) in fuzzy {
            tiers.entry(index).or_insert((1, score));
        }

        let mut scored: Vec<Proposal> = tiers
            .into_iter()
            .filter(|&(index, _)| categories_compatible(pes_name, &catalog[index].name))
            .map(|(index, (tier, score))| Proposal { tier, score, index })
            .collect();
        scored.sort_by(|a, b| {
            b.tier
                .cmp(&a.tier)
                .then(b.score.total_cmp(&a.score))
                .then(catalog[a.index].fotmob_id.cmp(&catalog[b.index].fotmob_id))
        });
        let Some(&(mut best)) = scored.first() else {
            continue;
        };
        if best.score < MATCH_THRESHOLD {
            continue;
        }
        let same_tier_runner = scored[1..].iter().find(|c| c.tier == best.tier);
        if same_tier_runner.is_some_and(|runner| best.score - runner.score < FUZZY_MARGIN) {
            let legacy: Vec<Proposal> = scored
                .iter()
                .copied()
                .filter(|c| {
                    c.tier == best.tier
                        && best.score - c.score < FUZZY_MARGIN
                        && catalog[c.index].fotmob_id < LEGACY_FOTMOB_ID_LIMIT
                })
                .collect();
            let [only] = legacy[..] else { continue };
            best = only;
        }
        if best.tier == 1 && !has_meaningful_fuzzy_overlap(pes_name, &catalog[best.index].name) {
            continue;
        }
        proposals.push((pes_team_id, best.score, best.index));
    }

    let mut proposed: HashMap<i64, usize> = HashMap::new();
    for &(_, _, index) in &proposals {
        *proposed.entry(catalog[index].fotmob_id).or_default() += 1;
    }
    proposals
        .into_iter()
        .filter(|&(_, _, index)| proposed[&catalog[index].fotmob_id] == 1)
        .map(|(pes_team_id, score, index)| {
            let team = &catalog[index];
            IdentityEntry {
                fotmob_id: team.fotmob_id,
                pes_team_id,
                name: team.name.clone(),
                slug: team.slug.clone(),
                url: team.url.clone(),
                match_score: round1(score),
                source: IdentitySource::UnambiguousName,
            }
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use fl_sources::model::SquadMember;

    fn team(id: i64, name: &str) -> TeamEntry {
        TeamEntry {
            fotmob_id: id,
            name: name.to_owned(),
            slug: name.to_lowercase().replace(' ', "-"),
            url: format!("https://www.fotmob.com/teams/{id}/overview/x"),
        }
    }

    const FIRST: [&str; 20] = [
        "Aaron", "Bruno", "Carlos", "Dmitri", "Emil", "Fabio", "Goran", "Hugo", "Ivan", "Jonas",
        "Karim", "Luca", "Mateo", "Nils", "Oscar", "Pavel", "Quentin", "Rafael", "Stefan", "Tomas",
    ];
    const LAST: [&str; 40] = [
        "Abernathy",
        "Brightwell",
        "Castellanos",
        "Dragomir",
        "Eriksen",
        "Fontaine",
        "Gallagher",
        "Hrubesch",
        "Iwobi",
        "Jablonski",
        "Kowalczyk",
        "Lindqvist",
        "Mendieta",
        "Nakamura",
        "Okonkwo",
        "Pellegrini",
        "Quaresma",
        "Rautavaara",
        "Szczesny",
        "Takahashi",
        "Underwood",
        "Valbuena",
        "Whitfield",
        "Xhaka",
        "Yamaguchi",
        "Zubizarreta",
        "Arbeloa",
        "Benzema",
        "Cuadrado",
        "Dembele",
        "Etxeberria",
        "Fernandinho",
        "Gundogan",
        "Hazard",
        "Immobile",
        "Jorginho",
        "Kimmich",
        "Lewandowski",
        "Modric",
        "Neuer",
    ];

    fn players(start: usize, count: usize) -> Vec<(u32, String)> {
        (start..start + count)
            .map(|i| {
                (
                    1000 + i as u32,
                    format!("{} {}", FIRST[i % FIRST.len()], LAST[i]),
                )
            })
            .collect()
    }

    fn snapshot(fotmob_id: i64, club: &str, names: &[String]) -> SquadSnapshot {
        SquadSnapshot {
            club_name: club.to_owned(),
            team_id_fotmob: fotmob_id,
            members: names
                .iter()
                .map(|n| SquadMember {
                    player_name: n.clone(),
                    ..SquadMember::default()
                })
                .collect(),
            source_url: format!("https://www.fotmob.com/teams/{fotmob_id}/squad"),
            complete: true,
            starter_members: Vec::new(),
            sub_members: Vec::new(),
            primary_league_id: None,
            formation: None,
            formation_source_url: String::new(),
        }
    }

    fn build(
        dir: &std::path::Path,
        pes: &[(u32, &str)],
        catalog: &[TeamEntry],
        scope: &str,
    ) -> ClubIdentityIndex {
        let clubs = pes.iter().map(|(id, n)| (*id, (*n).to_owned())).collect();
        ClubIdentityIndex::build(
            &clubs,
            catalog,
            IdentityCache::new(dir.join("club_identity_cache.json"), scope.to_owned()),
        )
    }

    fn rosters_of(
        groups: &[(u32, &[(u32, String)])],
    ) -> (HashMap<u32, Vec<u32>>, HashMap<u32, String>) {
        let mut rosters = HashMap::new();
        let mut names = HashMap::new();
        for (team, group) in groups {
            rosters.insert(*team, group.iter().map(|(id, _)| *id).collect());
            names.extend(group.iter().cloned());
        }
        (rosters, names)
    }

    fn only_names(group: &[(u32, String)]) -> Vec<String> {
        group.iter().map(|(_, n)| n.clone()).collect()
    }

    #[test]
    fn name_pass_binds_unambiguous_names_and_rejects_ties() {
        let dir = tempfile::tempdir().unwrap();
        let pes = [
            (2, "Alpha Club"),
            (3, "Beta FC"),
            (4, "Gamma FC"),
            (5, "Racing Santander"),
            (6, "Delta FC"),
        ];
        let catalog = [
            team(20, "Alpha Club"),
            team(30, "Beta FC"),
            team(31, "Beta FC"),
            team(40, "Gamma Women"),
            team(50, "Racing"),
            team(51, "Racing Santander"),
            team(60, "Delta FC"),
            team(600_000, "Delta FC"),
        ];
        let index = build(dir.path(), &pes, &catalog, "save-a");
        let by_pes: HashMap<u32, IdentityEntry> = index
            .entries()
            .into_iter()
            .map(|e| (e.pes_team_id, e))
            .collect();
        assert_eq!(by_pes[&2].fotmob_id, 20);
        assert_eq!(by_pes[&2].source, IdentitySource::UnambiguousName);
        assert!(!by_pes.contains_key(&3));
        assert!(!by_pes.contains_key(&4));
        assert_eq!(by_pes[&5].fotmob_id, 51);
        assert_eq!(by_pes[&6].fotmob_id, 60);
        assert_eq!(index.pes_for_fotmob(51), Some(5));
        assert_eq!(index.fotmob_for_pes(6), Some(60));
        assert_eq!(index.pes_for_fotmob(31), None);
    }

    #[test]
    fn fuzzy_index_rejects_short_unrelated_club_name() {
        let dir = tempfile::tempdir().unwrap();
        let index = build(
            dir.path(),
            &[(200, "Ceres Negros")],
            &[team(2313, "Os")],
            "s",
        );
        assert!(index.entries().is_empty());
    }

    #[test]
    fn pes_reserve_team_does_not_claim_senior_fotmob_club() {
        let dir = tempfile::tempdir().unwrap();
        let index = build(
            dir.path(),
            &[(100, "Real Sociedad"), (101, "Real Sociedad B")],
            &[team(8560, "Real Sociedad")],
            "s",
        );
        assert_eq!(index.pes_for_fotmob(8560), Some(100));
        assert_eq!(index.fotmob_for_pes(101), None);
    }

    #[test]
    fn squad_overlap_binds_renamed_club() {
        let dir = tempfile::tempdir().unwrap();
        let bayern = players(0, 20);
        let other = players(20, 20);
        let (rosters, names) = rosters_of(&[(1, &bayern), (2, &other)]);
        let mut index = build(
            dir.path(),
            &[(1, "FC Bayern München"), (2, "Olympique Lyonnais")],
            &[team(9823, "Bayern Munich")],
            "s",
        );
        let mut squad = only_names(&bayern[..18]);
        squad.extend(only_names(&other[..2]));
        squad.push("Unknown Youngster".to_owned());
        let evidence = index.prepare_rosters(&rosters, &names);

        assert_eq!(
            index.learn_from_snapshot(&snapshot(9823, "Bayern Munich", &squad), &evidence),
            Some(1)
        );

        assert_eq!(index.pes_for_fotmob(9823), Some(1));
        let entry = index
            .entries()
            .into_iter()
            .find(|e| e.fotmob_id == 9823)
            .unwrap();
        assert_eq!(entry.source, IdentitySource::SquadOverlap);
        assert_eq!(index.resolve_name("Bayern Munich"), NameResolution::Team(1));
        assert!(
            index
                .aliases()
                .contains(&("Bayern Munich".to_owned(), "FC Bayern München".to_owned()))
        );
    }

    #[test]
    fn ambiguous_and_small_overlaps_are_not_bound() {
        let dir = tempfile::tempdir().unwrap();
        let first = players(0, 20);
        let second = players(20, 20);
        let (rosters, names) = rosters_of(&[(1, &first), (2, &second)]);
        let mut index = build(dir.path(), &[(1, "Alpha Club"), (2, "Beta Club")], &[], "s");
        let evidence = index.prepare_rosters(&rosters, &names);

        let mut squad = only_names(&first[..12]);
        squad.extend(only_names(&second[..12]));
        assert_eq!(
            index.learn_from_snapshot(&snapshot(777, "Gamma United", &squad), &evidence),
            None
        );
        assert_eq!(index.pes_for_fotmob(777), None);
        assert!(index.entries().is_empty());

        let mut small = only_names(&first[..5]);
        small.extend("ABCDEFGHIJ".chars().map(|c| format!("Stranger {c}")));
        assert_eq!(
            index.learn_from_snapshot(&snapshot(5, "Alpha", &small), &evidence),
            None
        );
    }

    #[test]
    fn overlap_evidence_overrides_name_binding() {
        let dir = tempfile::tempdir().unwrap();
        let squad = players(0, 20);
        let (rosters, names) = rosters_of(&[(4219, &squad)]);
        let mut index = build(
            dir.path(),
            &[(4219, "Como 1907")],
            &[team(10171, "Como"), team(1_802_179, "Calcio Como 1907")],
            "s",
        );
        assert_eq!(index.pes_for_fotmob(1_802_179), Some(4219));
        let evidence = index.prepare_rosters(&rosters, &names);

        let learned =
            index.learn_from_snapshot(&snapshot(10171, "Como", &only_names(&squad)), &evidence);

        assert_eq!(learned, Some(4219));
        assert_eq!(index.pes_for_fotmob(10171), Some(4219));
        assert_eq!(index.pes_for_fotmob(1_802_179), None);
        assert_eq!(index.fotmob_for_pes(4219), Some(10171));
    }

    #[test]
    fn resolve_name_never_treats_weak_match_as_absent() {
        let dir = tempfile::tempdir().unwrap();
        let index = build(
            dir.path(),
            &[(1, "FC Bayern München"), (2, "Manchester United")],
            &[],
            "s",
        );
        assert_eq!(
            index.resolve_name("FC Bayern München"),
            NameResolution::Team(1)
        );
        assert_eq!(
            index.resolve_name("Bayern München"),
            NameResolution::Team(1)
        );
        assert_eq!(
            index.resolve_name("Bayern Munich"),
            NameResolution::Unresolved
        );
        assert_eq!(
            index.resolve_name("Manchester Utd"),
            NameResolution::Unresolved
        );
        assert_eq!(index.resolve_name("Free agent"), NameResolution::Outside);
        assert_eq!(
            index.resolve_name("Kawasaki Frontale"),
            NameResolution::Outside
        );
    }

    #[test]
    fn learned_bindings_round_trip_per_save_scope_and_only_save_writes() {
        let dir = tempfile::tempdir().unwrap();
        let squad = players(0, 20);
        let (rosters, names) = rosters_of(&[(1, &squad)]);
        let pes = [(1, "FC Bayern München")];
        let catalog = [team(9823, "Bayern Munich")];
        let cache = dir.path().join("club_identity_cache.json");

        let mut index = build(dir.path(), &pes, &catalog, "save-a");
        let evidence = index.prepare_rosters(&rosters, &names);
        index.learn_from_snapshot(
            &snapshot(9823, "Bayern Munich", &only_names(&squad)),
            &evidence,
        );
        assert!(!cache.exists(), "learning must not write; only save() does");
        index.save().unwrap();
        build(dir.path(), &pes, &catalog, "save-b").save().unwrap();

        let text = std::fs::read_to_string(&cache).unwrap();
        assert_eq!(
            text,
            "{\n  \"save-a\": {\n    \"9823\": 1\n  },\n  \"save-b\": {}\n}\n"
        );
        let reloaded = build(dir.path(), &pes, &catalog, "save-a");
        assert_eq!(reloaded.pes_for_fotmob(9823), Some(1));
        assert_eq!(reloaded.entries()[0].source, IdentitySource::SquadOverlap);
        assert_eq!(
            build(dir.path(), &pes, &catalog, "save-b").pes_for_fotmob(9823),
            None
        );
        assert!(
            build(dir.path(), &[(2, "Other Club")], &catalog, "save-a")
                .entries()
                .is_empty()
        );
    }

    #[test]
    fn unreadable_cache_is_ignored_and_replaced() {
        let dir = tempfile::tempdir().unwrap();
        let cache = dir.path().join("club_identity_cache.json");
        std::fs::write(&cache, "{not json").unwrap();
        let index = build(dir.path(), &[(1, "Alpha")], &[], "s");
        assert!(index.entries().is_empty());
        index.save().unwrap();
        assert_eq!(
            std::fs::read_to_string(&cache).unwrap(),
            "{\n  \"s\": {}\n}\n"
        );
    }
}
