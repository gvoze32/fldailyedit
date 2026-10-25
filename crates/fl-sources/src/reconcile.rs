//! Cross-source reconciliation of normalized transfer events.
//!
//! Complete routes (FotMob, Transfermarkt, Wikipedia tables) are merged first;
//! Sortitoutsi signals may enrich or infer a route under their adapter's
//! explicit-date rules; every other source corroborates: it merges provenance
//! into a verified event, and an unmatched route becomes an event only when at
//! least two independent corroborating sources agree on player, destination
//! and (when dated) a date within three days.
//!
//! [`merge_provenance`] intentionally stays separate from
//! `model::merge_transfers`: reconciliation upgrades a different field set
//! (Transfermarkt ids, free-agent source clubs) and must not copy FotMob ids,
//! shirt numbers or club full names from lower-trust corroborators.
//!
//! Rust representation: transfers live in an arena (`Vec<Transfer>`) and the
//! candidate index stores arena positions, so buckets are keyed by identity
//! rather than by value.

use std::collections::{HashMap, HashSet};
use std::rc::Rc;

use fl_text::fuzz::{token_set_ratio, token_sort_ratio};
use fl_text::{TokenSetIndex, is_non_club_key, normalize_dotless};

use crate::dates::parse_iso_date;
use crate::model::{
    Transfer, TransferType, VerificationStatus, adopt_event_type, merge_transfers, source_priority,
};

/// Club names at or above this `token_set_ratio` are "the same club".
const CLUB_MATCH_CUTOFF: f64 = 92.0;
/// Player names at or above this `token_sort_ratio` are "the same player".
const PLAYER_MATCH_CUTOFF: f64 = 95.0;
const CORROBORATION_DATE_TOLERANCE_DAYS: i64 = 3;
const MIN_INDEPENDENT_SOURCES: usize = 2;

fn normalize(value: &str) -> String {
    normalize_dotless(value)
}

fn is_non_club(value: &str) -> bool {
    is_non_club_key(&normalize(value))
}

/// `(namespace, id)` of an exact source player id.
type IdentityKey = (&'static str, i64);
type CompositeKey = (String, String, String, String, String);

/// Positive source ids of a transfer, namespaced and in a fixed order.
fn player_identity_keys(transfer: &Transfer) -> Vec<IdentityKey> {
    [
        ("player_id_fotmob", transfer.player_id_fotmob),
        ("player_id_transfermarkt", transfer.player_id_transfermarkt),
        ("player_id_sortitoutsi", transfer.player_id_sortitoutsi),
    ]
    .into_iter()
    .filter_map(|(field, value)| value.filter(|id| *id > 0).map(|id| (field, id)))
    .collect()
}

/// Exact source identity first, then a safe name fallback.
fn same_player(left: &Transfer, right: &Transfer) -> bool {
    let left_ids: HashMap<_, _> = player_identity_keys(left).into_iter().collect();
    let right_ids: HashMap<_, _> = player_identity_keys(right).into_iter().collect();
    let mut shared = false;
    for (field, id) in &left_ids {
        if let Some(other) = right_ids.get(field) {
            if other != id {
                return false;
            }
            shared = true;
        }
    }
    shared || same_player_name(&left.player_name, &right.player_name)
}

fn same_player_name(left: &str, right: &str) -> bool {
    let (left, right) = (normalize(left), normalize(right));
    if left.is_empty() || right.is_empty() {
        return false;
    }
    left == right || token_sort_ratio(&left, &right) >= PLAYER_MATCH_CUTOFF
}

fn same_club_name(left: &str, right: &str) -> bool {
    let (left, right) = (normalize(left), normalize(right));
    if left.is_empty() || right.is_empty() {
        return false;
    }
    left == right || token_set_ratio(&left, &right) >= CLUB_MATCH_CUTOFF
}

fn source_name(transfer: &Transfer) -> &str {
    if transfer.from_club_full_name.is_empty() {
        &transfer.from_club
    } else {
        &transfer.from_club_full_name
    }
}

fn destination_name(transfer: &Transfer) -> &str {
    if transfer.to_club_full_name.is_empty() {
        &transfer.to_club
    } else {
        &transfer.to_club_full_name
    }
}

fn same_source(left: &Transfer, right: &Transfer) -> bool {
    same_club_name(source_name(left), source_name(right))
}

fn same_destination(left: &Transfer, right: &Transfer) -> bool {
    same_club_name(destination_name(left), destination_name(right))
}

fn same_route(left: &Transfer, right: &Transfer) -> bool {
    same_source(left, right) && same_destination(left, right)
}

fn compatible_source(left: &Transfer, right: &Transfer) -> bool {
    same_source(left, right)
        || (left.transfer_type == TransferType::FreeTransfer
            && right.transfer_type == TransferType::FreeTransfer)
}

fn compatible_event_type(left: &Transfer, right: &Transfer) -> bool {
    left.transfer_type == right.transfer_type
        || left.transfer_type == TransferType::Transfer
        || right.transfer_type == TransferType::Transfer
}

fn days_apart(left: &str, right: &str) -> Option<i64> {
    Some(
        (parse_iso_date(left)? - parse_iso_date(right)?)
            .num_days()
            .abs(),
    )
}

fn same_or_adjacent_date(left: &str, right: &str) -> bool {
    days_apart(left, right).is_none_or(|days| days <= 1)
}

fn within_corroboration_window(left: &str, right: &str) -> bool {
    days_apart(left, right).is_none_or(|days| days <= CORROBORATION_DATE_TOLERANCE_DAYS)
}

fn club_key(transfer: &Transfer, source: bool) -> String {
    normalize(if source {
        source_name(transfer)
    } else {
        destination_name(transfer)
    })
}

fn event_date_key(transfer: &Transfer) -> String {
    parse_iso_date(&transfer.date)
        .map(|date| date.format("%Y-%m-%d").to_string())
        .unwrap_or_default()
}

fn composite_key(transfer: &Transfer, player_key: String) -> CompositeKey {
    (
        player_key,
        club_key(transfer, true),
        club_key(transfer, false),
        event_date_key(transfer),
        transfer.transfer_type.as_str().to_owned(),
    )
}

/// Fold a lower-priority duplicate's provenance and missing details into
/// `target` (the reconciliation field set; see the module docs).
pub fn merge_provenance(target: &mut Transfer, source: &Transfer) {
    adopt_event_type(target, source);
    union_in_place(&mut target.sources, &source.sources);
    union_in_place(&mut target.source_urls, &source.source_urls);
    union_in_place(&mut target.proof_urls, &source.proof_urls);
    let source_club = source_name(source).to_owned();
    if target.transfer_type == TransferType::FreeTransfer
        && source.transfer_type == TransferType::FreeTransfer
        && is_non_club(source_name(target))
        && !is_non_club(&source_club)
    {
        target.from_club = source.from_club.clone();
        target.from_club_full_name = source_club;
    }
    target.player_id_sortitoutsi = target
        .player_id_sortitoutsi
        .or(source.player_id_sortitoutsi);
    target.player_id_transfermarkt = target
        .player_id_transfermarkt
        .or(source.player_id_transfermarkt);
    target.from_club_id_transfermarkt = target
        .from_club_id_transfermarkt
        .or(source.from_club_id_transfermarkt);
    target.to_club_id_transfermarkt = target
        .to_club_id_transfermarkt
        .or(source.to_club_id_transfermarkt);
    target.transfer_id_transfermarkt = target
        .transfer_id_transfermarkt
        .or(source.transfer_id_transfermarkt);
    if target.position.is_empty() {
        target.position.clone_from(&source.position);
    }
    if target.fee.is_empty() {
        target.fee.clone_from(&source.fee);
    }
    if target.nationality.is_empty() {
        target.nationality.clone_from(&source.nationality);
    }
    if target.age == 0 {
        target.age = source.age;
    }
    if target.market_value == 0 {
        target.market_value = source.market_value;
    }
}

/// Append `extra` to `target`, dropping repeats while keeping first-seen order.
fn union_in_place(target: &mut Vec<String>, extra: &[String]) {
    target.extend(extra.iter().cloned());
    let mut seen = HashSet::new();
    target.retain(|item| seen.insert(item.clone()));
}

/// Normalized club names with a cached fuzzy lookup of every name that is the
/// same club at [`CLUB_MATCH_CUTOFF`].
pub struct FuzzyKeyIndex {
    choices: Vec<String>,
    index: TokenSetIndex,
    matches: HashMap<String, Rc<[String]>>,
}

impl FuzzyKeyIndex {
    fn new<'a>(values: impl IntoIterator<Item = &'a str>) -> Self {
        let mut seen = HashSet::new();
        let choices: Vec<String> = values
            .into_iter()
            .map(normalize)
            .filter(|key| !key.is_empty() && seen.insert(key.clone()))
            .collect();
        let index = TokenSetIndex::new(choices.iter().map(String::as_str));
        Self {
            choices,
            index,
            matches: HashMap::new(),
        }
    }

    /// Indexed keys equal or fuzzily equal to `value`, best score first.
    fn matching_keys(&mut self, value: &str) -> Rc<[String]> {
        let query = normalize(value);
        if query.is_empty() {
            return Rc::from(Vec::new());
        }
        if let Some(cached) = self.matches.get(&query) {
            return Rc::clone(cached);
        }
        let keys: Rc<[String]> = self
            .index
            .extract(&query, CLUB_MATCH_CUTOFF)
            .into_iter()
            .map(|(position, _)| self.choices[position].clone())
            .collect();
        self.matches.insert(query, Rc::clone(&keys));
        keys
    }
}

/// Everything the index captured about a transfer when it was added.
struct Location {
    source_key: String,
    destination_key: String,
    is_free: bool,
    identity_keys: Vec<IdentityKey>,
    alias_key: String,
    composite_keys: Vec<CompositeKey>,
}

/// Index of IDs, safe aliases, exact event keys and fuzzy route candidates
/// over an arena of transfers.
struct CandidateIndex {
    club_index: FuzzyKeyIndex,
    by_route: HashMap<(String, String), Vec<usize>>,
    by_destination: HashMap<String, Vec<usize>>,
    free_by_destination: HashMap<String, Vec<usize>>,
    by_identity: HashMap<IdentityKey, Vec<usize>>,
    by_alias: HashMap<String, Vec<usize>>,
    by_composite: HashMap<CompositeKey, Vec<usize>>,
    safe_aliases: HashSet<String>,
    locations: HashMap<usize, Location>,
}

fn remove_from_bucket<K: std::hash::Hash + Eq>(
    buckets: &mut HashMap<K, Vec<usize>>,
    key: &K,
    position: usize,
) {
    let Some(bucket) = buckets.get_mut(key) else {
        return;
    };
    if let Some(at) = bucket.iter().position(|candidate| *candidate == position) {
        bucket.remove(at);
    }
    if bucket.is_empty() {
        buckets.remove(key);
    }
}

fn collect_into<'k, K: std::hash::Hash + Eq + 'k>(
    buckets: &HashMap<K, Vec<usize>>,
    keys: impl IntoIterator<Item = &'k K>,
    seen: &mut HashSet<usize>,
    out: &mut Vec<usize>,
) {
    for key in keys {
        for &position in buckets.get(key).into_iter().flatten() {
            if seen.insert(position) {
                out.push(position);
            }
        }
    }
}

fn collect<'k, K: std::hash::Hash + Eq + 'k>(
    buckets: &HashMap<K, Vec<usize>>,
    keys: impl IntoIterator<Item = &'k K>,
) -> Vec<usize> {
    let mut out = Vec::new();
    collect_into(buckets, keys, &mut HashSet::new(), &mut out);
    out
}

/// Player name keys whose source ids never contradict each other, so the name
/// itself can be treated as an alias of the id.
fn build_safe_aliases<'a>(transfers: impl IntoIterator<Item = &'a Transfer>) -> HashSet<String> {
    let mut parent: HashMap<IdentityKey, IdentityKey> = HashMap::new();
    let mut identities_by_name: HashMap<String, HashSet<IdentityKey>> = HashMap::new();
    type Signature = (String, String, String, String, String);
    let mut signature_fields: HashMap<Signature, HashMap<&'static str, HashSet<i64>>> =
        HashMap::new();

    fn find(parent: &mut HashMap<IdentityKey, IdentityKey>, identity: IdentityKey) -> IdentityKey {
        let mut root = identity;
        while let Some(&next) = parent.get(&root) {
            if next == root {
                break;
            }
            root = next;
        }
        parent.entry(identity).or_insert(identity);
        let mut cursor = identity;
        while cursor != root {
            let next = parent[&cursor];
            parent.insert(cursor, root);
            cursor = next;
        }
        root
    }
    fn union(
        parent: &mut HashMap<IdentityKey, IdentityKey>,
        left: IdentityKey,
        right: IdentityKey,
    ) {
        let (left_root, right_root) = (find(parent, left), find(parent, right));
        if left_root != right_root {
            parent.insert(right_root, left_root);
        }
    }

    for transfer in transfers {
        let name_key = normalize(&transfer.player_name);
        let identity_keys = player_identity_keys(transfer);
        if name_key.is_empty() || identity_keys.is_empty() {
            continue;
        }
        for key in &identity_keys {
            parent.entry(*key).or_insert(*key);
            identities_by_name
                .entry(name_key.clone())
                .or_default()
                .insert(*key);
        }
        for key in &identity_keys[1..] {
            union(&mut parent, identity_keys[0], *key);
        }
        let signature = (
            name_key,
            club_key(transfer, true),
            club_key(transfer, false),
            event_date_key(transfer),
            transfer.transfer_type.as_str().to_owned(),
        );
        for (field, value) in identity_keys {
            signature_fields
                .entry(signature.clone())
                .or_default()
                .entry(field)
                .or_default()
                .insert(value);
        }
    }

    for fields in signature_fields.values() {
        if fields.values().any(|values| values.len() > 1) {
            continue;
        }
        let identity_keys: Vec<IdentityKey> = fields
            .iter()
            .flat_map(|(field, values)| values.iter().map(move |value| (*field, *value)))
            .collect();
        if let Some((first, rest)) = identity_keys.split_first() {
            for key in rest {
                union(&mut parent, *first, *key);
            }
        }
    }

    identities_by_name
        .into_iter()
        .filter(|(_, identities)| {
            let roots: HashSet<IdentityKey> =
                identities.iter().map(|id| find(&mut parent, *id)).collect();
            roots.len() == 1
        })
        .map(|(name, _)| name)
        .collect()
}

impl CandidateIndex {
    /// `known` is every transfer that may later be matched (indexed ones and
    /// the ones only looked up), used for the club index and safe aliases;
    /// `indexed` are added to the buckets now.
    fn new<'a>(
        known: impl IntoIterator<Item = &'a Transfer> + Clone,
        indexed: &[Transfer],
    ) -> Self {
        let club_values = known
            .clone()
            .into_iter()
            .flat_map(|transfer| [source_name(transfer), destination_name(transfer)]);
        let mut index = Self {
            club_index: FuzzyKeyIndex::new(club_values),
            by_route: HashMap::new(),
            by_destination: HashMap::new(),
            free_by_destination: HashMap::new(),
            by_identity: HashMap::new(),
            by_alias: HashMap::new(),
            by_composite: HashMap::new(),
            safe_aliases: build_safe_aliases(known),
            locations: HashMap::new(),
        };
        for position in 0..indexed.len() {
            index.add(indexed, position);
        }
        index
    }

    fn composite_index_keys(&self, transfer: &Transfer) -> Vec<CompositeKey> {
        let name_key = normalize(&transfer.player_name);
        let mut player_keys: Vec<String> = Vec::new();
        if self.safe_aliases.contains(&name_key) {
            player_keys.push(format!("alias:{name_key}"));
        }
        player_keys.extend(
            player_identity_keys(transfer)
                .into_iter()
                .map(|(field, id)| format!("id:{field}:{id}")),
        );
        if !name_key.is_empty() {
            player_keys.push(format!("name:{name_key}"));
        }
        let mut seen = HashSet::new();
        player_keys
            .into_iter()
            .filter(|key| seen.insert(key.clone()))
            .map(|player_key| composite_key(transfer, player_key))
            .collect()
    }

    /// Add `arena[position]` using its current route and identity fields.
    fn add(&mut self, arena: &[Transfer], position: usize) {
        if self.locations.contains_key(&position) {
            self.remove(position);
        }
        let transfer = &arena[position];
        let source_key = club_key(transfer, true);
        let destination_key = club_key(transfer, false);
        let is_free = transfer.transfer_type == TransferType::FreeTransfer;
        let identity_keys = player_identity_keys(transfer);
        let name_key = normalize(&transfer.player_name);
        let alias_key = if self.safe_aliases.contains(&name_key) {
            name_key
        } else {
            String::new()
        };
        let composite_keys = self.composite_index_keys(transfer);

        if !destination_key.is_empty() {
            self.by_destination
                .entry(destination_key.clone())
                .or_default()
                .push(position);
            if is_free {
                self.free_by_destination
                    .entry(destination_key.clone())
                    .or_default()
                    .push(position);
            }
        }
        if !source_key.is_empty() && !destination_key.is_empty() {
            self.by_route
                .entry((source_key.clone(), destination_key.clone()))
                .or_default()
                .push(position);
        }
        for key in &identity_keys {
            self.by_identity.entry(*key).or_default().push(position);
        }
        if !alias_key.is_empty() {
            self.by_alias
                .entry(alias_key.clone())
                .or_default()
                .push(position);
        }
        for key in &composite_keys {
            self.by_composite
                .entry(key.clone())
                .or_default()
                .push(position);
        }
        self.locations.insert(
            position,
            Location {
                source_key,
                destination_key,
                is_free,
                identity_keys,
                alias_key,
                composite_keys,
            },
        );
    }

    /// Remove a transfer using the fields captured when it was indexed.
    fn remove(&mut self, position: usize) {
        let Some(location) = self.locations.remove(&position) else {
            return;
        };
        if !location.destination_key.is_empty() {
            remove_from_bucket(
                &mut self.by_destination,
                &location.destination_key,
                position,
            );
            if location.is_free {
                remove_from_bucket(
                    &mut self.free_by_destination,
                    &location.destination_key,
                    position,
                );
            }
        }
        if !location.source_key.is_empty() && !location.destination_key.is_empty() {
            remove_from_bucket(
                &mut self.by_route,
                &(location.source_key, location.destination_key),
                position,
            );
        }
        for key in &location.identity_keys {
            remove_from_bucket(&mut self.by_identity, key, position);
        }
        if !location.alias_key.is_empty() {
            remove_from_bucket(&mut self.by_alias, &location.alias_key, position);
        }
        for key in &location.composite_keys {
            remove_from_bucket(&mut self.by_composite, key, position);
        }
    }

    /// Re-index after provenance enrichment changed the transfer.
    fn refresh(&mut self, arena: &[Transfer], position: usize) {
        self.remove(position);
        self.add(arena, position);
    }

    /// Candidates the player is decisively known by: exact ids, then a safe
    /// alias, then the exact player/route/date/type composite key.
    fn decisive_candidates(&self, transfer: &Transfer) -> Option<Vec<usize>> {
        let identity_keys = player_identity_keys(transfer);
        let by_identity = collect(&self.by_identity, &identity_keys);
        if !by_identity.is_empty() {
            return Some(by_identity);
        }
        let name_key = normalize(&transfer.player_name);
        if self.safe_aliases.contains(&name_key) {
            let by_alias = collect(&self.by_alias, [&name_key]);
            if !by_alias.is_empty() {
                return Some(by_alias);
            }
        }
        let composite = collect(&self.by_composite, &self.composite_index_keys(transfer));
        (!composite.is_empty()).then_some(composite)
    }

    fn destination_candidates(&mut self, transfer: &Transfer) -> Vec<usize> {
        if let Some(candidates) = self.decisive_candidates(transfer) {
            return candidates;
        }
        let destination_key = club_key(transfer, false);
        if destination_key.is_empty() {
            return Vec::new();
        }
        let destination_keys = self.club_index.matching_keys(&destination_key);
        collect(&self.by_destination, destination_keys.iter())
    }

    fn route_candidates(&mut self, transfer: &Transfer) -> Vec<usize> {
        if let Some(candidates) = self.decisive_candidates(transfer) {
            return candidates;
        }
        let source_key = club_key(transfer, true);
        let destination_key = club_key(transfer, false);
        if destination_key.is_empty() {
            return Vec::new();
        }
        let free = transfer.transfer_type == TransferType::FreeTransfer;
        if source_key.is_empty() && !free {
            return Vec::new();
        }
        let destination_keys = self.club_index.matching_keys(&destination_key);
        if destination_keys.is_empty() {
            return Vec::new();
        }

        let mut candidates = Vec::new();
        let mut seen = HashSet::new();
        if !source_key.is_empty() {
            let source_keys = self.club_index.matching_keys(&source_key);
            for source_match in source_keys.iter() {
                for destination_match in destination_keys.iter() {
                    let route = (source_match.clone(), destination_match.clone());
                    collect_into(&self.by_route, [&route], &mut seen, &mut candidates);
                }
            }
        }
        if free {
            collect_into(
                &self.free_by_destination,
                destination_keys.iter(),
                &mut seen,
                &mut candidates,
            );
        }
        candidates
    }
}

/// Drop lower-trust routes that contradict a stronger same-day event.
///
/// Undated transfers come first in the result, then each (player, date)
/// group in first-seen order.
fn prefer_primary_routes(transfers: Vec<Transfer>) -> Vec<Transfer> {
    let mut ungrouped = Vec::new();
    let mut group_of: HashMap<(String, chrono::NaiveDate), usize> = HashMap::new();
    let mut groups: Vec<Vec<Transfer>> = Vec::new();
    for transfer in transfers {
        let Some(event_date) = parse_iso_date(&transfer.date) else {
            ungrouped.push(transfer);
            continue;
        };
        let group = *group_of
            .entry((normalize(&transfer.player_name), event_date))
            .or_insert_with(|| {
                groups.push(Vec::new());
                groups.len() - 1
            });
        groups[group].push(transfer);
    }

    let mut selected = ungrouped;
    for group in groups {
        let mut distinct_routes: Vec<&Transfer> = Vec::new();
        for transfer in &group {
            if !distinct_routes
                .iter()
                .any(|route| same_route(transfer, route))
            {
                distinct_routes.push(transfer);
            }
        }
        if distinct_routes.len() <= 1 {
            selected.extend(group);
            continue;
        }
        let strongest = distinct_routes
            .iter()
            .map(|t| source_priority(t))
            .min()
            .unwrap_or(3);
        let preferred_routes: Vec<&Transfer> = distinct_routes
            .iter()
            .copied()
            .filter(|t| source_priority(t) == strongest)
            .collect();
        let keep: Vec<bool> = group
            .iter()
            .map(|transfer| {
                preferred_routes
                    .iter()
                    .any(|route| same_route(transfer, route))
            })
            .collect();
        let discarded = keep.iter().filter(|kept| !**kept).count();
        if discarded > 0 {
            tracing::warn!(
                discarded,
                player = %group[0].player_name,
                date = %group[0].date,
                "Discarding lower-priority conflicting route(s)"
            );
        }
        selected.extend(
            group
                .into_iter()
                .zip(keep)
                .filter_map(|(transfer, kept)| kept.then_some(transfer)),
        );
    }
    selected
}

/// Merge complete-route batches into one deduplicated event list.
fn merge_verified_batches(verified_batches: Vec<Vec<Transfer>>) -> Vec<Transfer> {
    let incoming = merge_transfers(verified_batches.into_iter().flatten().collect());
    let mut index = CandidateIndex::new(&incoming, &[]);
    let mut merged: Vec<Transfer> = Vec::new();
    for transfer in incoming {
        let candidates: Vec<usize> = index
            .route_candidates(&transfer)
            .into_iter()
            .filter(|&position| {
                let existing = &merged[position];
                same_player(existing, &transfer)
                    && compatible_source(existing, &transfer)
                    && same_destination(existing, &transfer)
                    && same_or_adjacent_date(&existing.date, &transfer.date)
                    && compatible_event_type(existing, &transfer)
            })
            .collect();
        if let [target] = candidates[..] {
            merge_provenance(&mut merged[target], &transfer);
            index.refresh(&merged, target);
        } else {
            merged.push(transfer);
            index.add(&merged, merged.len() - 1);
        }
    }
    prefer_primary_routes(merged)
}

fn agrees_with_cluster(unmatched: &[Transfer], cluster: &[usize], transfer: &Transfer) -> bool {
    // Cheap date/type checks first; every predicate is pure, so order is free.
    cluster
        .iter()
        .map(|&position| &unmatched[position])
        .all(|member| {
            within_corroboration_window(&member.date, &transfer.date)
                && compatible_event_type(member, transfer)
                && same_destination(member, transfer)
                && same_player(member, transfer)
        })
}

/// The member naming the agreed source club, or `None` on conflict.
fn cluster_source_club(unmatched: &[Transfer], cluster: &[usize]) -> Option<usize> {
    let named: Vec<usize> = cluster
        .iter()
        .copied()
        .filter(|&position| !is_non_club(source_name(&unmatched[position])))
        .collect();
    for &left in &named {
        for &right in &named {
            if left != right && !same_source(&unmatched[left], &unmatched[right]) {
                return None;
            }
        }
    }
    Some(named.first().copied().unwrap_or(cluster[0]))
}

/// Create events from corroborator-only routes that independent sources
/// agree on. Returns the events and how many corroborators they consumed.
/// `club_index` must contain every unmatched destination club.
fn events_from_agreeing_corroborators(
    unmatched: &[Transfer],
    club_index: &mut FuzzyKeyIndex,
) -> (Vec<Transfer>, usize) {
    let mut clusters: Vec<Vec<usize>> = Vec::new();
    // A cluster only accepts a transfer whose destination fuzzily matches the
    // cluster's first member, so bucket clusters by that member's destination
    // key and scan only matching buckets, oldest cluster first.
    let mut clusters_by_destination: HashMap<String, Vec<usize>> = HashMap::new();
    for (position, transfer) in unmatched.iter().enumerate() {
        let destination_key = club_key(transfer, false);
        let mut cluster_id = None;
        if !destination_key.is_empty() {
            let mut candidate_ids: Vec<usize> = club_index
                .matching_keys(&destination_key)
                .iter()
                .flat_map(|key| {
                    clusters_by_destination
                        .get(key)
                        .into_iter()
                        .flatten()
                        .copied()
                })
                .collect();
            candidate_ids.sort_unstable();
            cluster_id = candidate_ids
                .into_iter()
                .find(|&id| agrees_with_cluster(unmatched, &clusters[id], transfer));
        }
        match cluster_id {
            Some(id) => clusters[id].push(position),
            None => {
                clusters_by_destination
                    .entry(destination_key)
                    .or_default()
                    .push(clusters.len());
                clusters.push(vec![position]);
            }
        }
    }

    let mut created = Vec::new();
    let mut consumed = 0;
    for cluster in &clusters {
        let independent_sources: HashSet<String> = cluster
            .iter()
            .flat_map(|&position| {
                unmatched[position]
                    .sources
                    .iter()
                    .map(|source| fl_text::casefold(source))
            })
            .collect();
        if independent_sources.len() < MIN_INDEPENDENT_SOURCES {
            continue;
        }
        let Some(source_member) = cluster_source_club(unmatched, cluster) else {
            tracing::warn!(
                player = %unmatched[cluster[0]].player_name,
                to_club = %unmatched[cluster[0]].to_club,
                "Corroborators agree on a route but name conflicting source clubs; not creating an event"
            );
            continue;
        };
        let mut dated: Vec<usize> = cluster
            .iter()
            .copied()
            .filter(|&position| parse_iso_date(&unmatched[position].date).is_some())
            .collect();
        dated.sort_by(|&a, &b| {
            (source_priority(&unmatched[a]), &unmatched[a].date)
                .cmp(&(source_priority(&unmatched[b]), &unmatched[b].date))
        });
        let representative = dated.first().copied().unwrap_or(cluster[0]);
        let mut event = Transfer {
            from_club: unmatched[source_member].from_club.clone(),
            from_club_full_name: unmatched[source_member].from_club_full_name.clone(),
            verification_status: VerificationStatus::Corroborated,
            infer_from_current_roster: false,
            ..unmatched[representative].clone()
        };
        for &member in cluster {
            if member != representative {
                merge_provenance(&mut event, &unmatched[member]);
            }
        }
        created.push(event);
        consumed += cluster.len();
    }
    (created, consumed)
}

/// Merge complete routes, then reconcile destination-only community signals
/// and corroborating routes.
///
/// * `verified_batches`: complete dated routes (FotMob, Transfermarkt,
///   Wikipedia tables); merged and deduplicated first.
/// * `fast_signals`: Sortitoutsi signals; each either corroborates exactly one
///   event, becomes an event itself when it carries its own effective date
///   (`infer_from_current_roster`), or is ignored.
/// * `corroborators`: other sources' routes; each merges into exactly one
///   matching event, and leftovers become events only when two independent
///   sources agree.
pub fn reconcile_transfer_sources(
    verified_batches: Vec<Vec<Transfer>>,
    fast_signals: Vec<Transfer>,
    corroborators: Vec<Transfer>,
) -> Vec<Transfer> {
    let mut verified = merge_verified_batches(verified_batches);
    let (
        mut inferred_signals,
        mut corroborated_signals,
        mut ambiguous_signals,
        mut ignored_signals,
    ) = (0, 0, 0, 0);
    let (mut corroborated_routes, mut ignored_routes) = (0, 0);
    let mut index = CandidateIndex::new(
        verified.iter().chain(&fast_signals).chain(&corroborators),
        &verified,
    );

    for signal in fast_signals {
        let candidates: Vec<usize> = index
            .destination_candidates(&signal)
            .into_iter()
            .filter(|&position| {
                let transfer = &verified[position];
                same_player(transfer, &signal)
                    && same_destination(transfer, &signal)
                    && same_or_adjacent_date(&transfer.date, &signal.date)
                    && compatible_event_type(transfer, &signal)
            })
            .collect();
        match candidates[..] {
            [target] => {
                merge_provenance(&mut verified[target], &signal);
                index.refresh(&verified, target);
                corroborated_signals += 1;
            }
            [] if signal.infer_from_current_roster => {
                verified.push(signal);
                index.add(&verified, verified.len() - 1);
                inferred_signals += 1;
            }
            [] => ignored_signals += 1,
            _ => {
                ambiguous_signals += 1;
                tracing::warn!(player = %signal.player_name, to_club = %signal.to_club, "Ignoring ambiguous Sortitoutsi signal");
            }
        }
    }

    let mut unmatched_routes: Vec<Transfer> = Vec::new();
    for corroborator in corroborators {
        let candidates: Vec<usize> = index
            .route_candidates(&corroborator)
            .into_iter()
            .filter(|&position| {
                let transfer = &verified[position];
                same_player(transfer, &corroborator)
                    && same_or_adjacent_date(&transfer.date, &corroborator.date)
                    && same_source(transfer, &corroborator)
                    && same_destination(transfer, &corroborator)
                    && compatible_event_type(transfer, &corroborator)
            })
            .collect();
        match candidates[..] {
            [target] => {
                merge_provenance(&mut verified[target], &corroborator);
                index.refresh(&verified, target);
                corroborated_routes += 1;
            }
            [] => unmatched_routes.push(corroborator),
            _ => {
                ignored_routes += 1;
                tracing::warn!(
                    player = %corroborator.player_name,
                    from = %corroborator.from_club,
                    to = %corroborator.to_club,
                    "Ignoring ambiguous route corroborator"
                );
            }
        }
    }

    let (agreed_events, agreed_routes) =
        events_from_agreeing_corroborators(&unmatched_routes, &mut index.club_index);
    ignored_routes += unmatched_routes.len() - agreed_routes;
    let created = agreed_events.len();
    verified.extend(agreed_events);

    tracing::info!(
        corroborated_signals,
        inferred_signals,
        ignored_signals,
        ambiguous_signals,
        corroborated_routes,
        created,
        ignored_routes,
        "Cross-source reconciliation"
    );
    verified
}

#[cfg(test)]
mod tests {
    use super::*;

    fn route(player: &str, from: &str, to: &str, date: &str) -> Transfer {
        Transfer {
            player_name: player.to_owned(),
            from_club: from.to_owned(),
            to_club: to.to_owned(),
            date: date.to_owned(),
            ..Transfer::default()
        }
    }

    fn from_source(source: &str, mut transfer: Transfer) -> Transfer {
        transfer.sources = vec![source.to_owned()];
        transfer.verification_status = VerificationStatus::Corroborator;
        transfer
    }

    fn ada() -> Transfer {
        route("Ada Example", "Old FC", "New FC", "2026-08-03")
    }

    fn sources(transfer: &Transfer) -> Vec<&str> {
        transfer.sources.iter().map(String::as_str).collect()
    }

    fn reconcile(
        batches: Vec<Vec<Transfer>>,
        signals: Vec<Transfer>,
        corroborators: Vec<Transfer>,
    ) -> Vec<Transfer> {
        reconcile_transfer_sources(batches, signals, corroborators)
    }

    #[test]
    fn corroborators_merge_provenance_without_creating_events() {
        let corroborators = vec![
            from_source(
                "besoccer",
                route("Example Ada", "Old FC", "New FC", "2026-08-03"),
            ),
            from_source(
                "sofascore",
                route("Ada Example", "Old FC", "New FC", "2026-08-04"),
            ),
            from_source(
                "soccerway",
                route("Unmatched Player", "Old FC", "New FC", "2026-08-03"),
            ),
        ];
        let result = reconcile(vec![vec![ada()]], vec![], corroborators);
        assert_eq!(result.len(), 1);
        assert_eq!(sources(&result[0]), ["fotmob", "besoccer", "sofascore"]);
    }

    #[test]
    fn route_corroborator_matches_fuzzy_club_names() {
        let corroborator = from_source(
            "soccerway",
            route("Example Ada", "Old", "New", "2026-08-03"),
        );
        let result = reconcile(vec![vec![ada()]], vec![], vec![corroborator]);
        assert_eq!(result.len(), 1);
        assert_eq!(sources(&result[0]), ["fotmob", "soccerway"]);
    }

    #[test]
    fn primary_source_wins_conflicting_same_day_route() {
        let fotmob = route(
            "Rolando Mandragora",
            "ACF Fiorentina",
            "Torino FC",
            "2026-09-01T20:46:40Z",
        );
        let wikipedia = from_source(
            "wikipedia",
            route(
                "Rolando Mandragora",
                "Torino FC",
                "ACF Fiorentina",
                "2026-09-01",
            ),
        );
        let result = reconcile(vec![vec![fotmob], vec![wikipedia]], vec![], vec![]);
        assert_eq!(result.len(), 1);
        assert_eq!(
            (result[0].from_club.as_str(), result[0].to_club.as_str()),
            ("ACF Fiorentina", "Torino FC")
        );
        assert_eq!(sources(&result[0]), ["fotmob"]);
    }

    #[test]
    fn free_transfer_merges_free_agent_and_previous_club_routes() {
        let free = |mut transfer: Transfer| {
            transfer.transfer_type = TransferType::FreeTransfer;
            transfer
        };
        let fotmob = free(route(
            "Jordan Henderson",
            "Free Agent",
            "Chelsea",
            "2026-08-03T17:16:20Z",
        ));
        let mut transfermarkt = from_source(
            "transfermarkt",
            free(route(
                "Jordan Henderson",
                "Brentford FC",
                "Chelsea FC",
                "2026-08-03",
            )),
        );
        transfermarkt.verification_status = VerificationStatus::Verified;
        transfermarkt.transfer_id_transfermarkt = Some(6_481_567);
        let result = reconcile(vec![vec![fotmob], vec![transfermarkt]], vec![], vec![]);
        assert_eq!(result.len(), 1);
        assert_eq!(sources(&result[0]), ["fotmob", "transfermarkt"]);
        assert_eq!(result[0].transfer_id_transfermarkt, Some(6_481_567));
        assert_eq!(result[0].from_club, "Brentford FC");
    }

    #[test]
    fn lower_priority_sources_cannot_turn_a_paid_move_into_a_loan() {
        let loan = |source: &str| {
            let mut transfer = from_source(source, ada());
            transfer.transfer_type = TransferType::Loan;
            transfer.is_loan = true;
            transfer
        };
        let mut fotmob = ada();
        fotmob.fee = "€30M".to_owned();
        let result = reconcile(
            vec![vec![fotmob], vec![loan("wikipedia")]],
            vec![],
            vec![loan("besoccer")],
        );
        assert_eq!(result.len(), 1);
        assert_eq!(sources(&result[0]), ["fotmob", "wikipedia", "besoccer"]);
        assert_eq!(
            (result[0].transfer_type, result[0].is_loan),
            (TransferType::Transfer, false)
        );
    }

    #[test]
    fn transliterated_player_names_merge_across_sources() {
        let free = |name: &str, source: &str| {
            let mut transfer = from_source(
                source,
                route(name, "West Ham United", "Free Agent", "2026-07-01"),
            );
            transfer.transfer_type = TransferType::FreeTransfer;
            transfer.verification_status = VerificationStatus::Verified;
            transfer
        };
        let result = reconcile(
            vec![
                vec![free("Łukasz Fabiański", "fotmob")],
                vec![free("Lukasz Fabianski", "transfermarkt")],
            ],
            vec![],
            vec![],
        );
        assert_eq!(result.len(), 1);
        assert_eq!(sources(&result[0]), ["fotmob", "transfermarkt"]);
    }

    #[test]
    fn fast_signal_enriches_a_complete_route_or_infers_one_event() {
        let wikipedia = from_source(
            "wikipedia",
            route("Jordan Henderson", "Brentford", "Chelsea", "2026-08-03"),
        );
        let signal = |roster: bool| Transfer {
            player_id_sortitoutsi: Some(28_005_568),
            verification_status: VerificationStatus::Enabled,
            infer_from_current_roster: roster,
            ..from_source(
                "sortitoutsi",
                route("Jordan Henderson", "", "Chelsea", "2026-08-03"),
            )
        };
        let merged = reconcile(vec![vec![wikipedia.clone()]], vec![signal(false)], vec![]);
        assert_eq!(merged.len(), 1);
        assert_eq!(
            (merged[0].from_club.as_str(), merged[0].to_club.as_str()),
            ("Brentford", "Chelsea")
        );
        assert_eq!(sources(&merged[0]), ["wikipedia", "sortitoutsi"]);
        assert_eq!(merged[0].player_id_sortitoutsi, Some(28_005_568));

        // Without a matching event: kept only when it carries its own effective date.
        assert!(reconcile(vec![], vec![signal(false)], vec![]).is_empty());
        let inferred = reconcile(vec![], vec![signal(true)], vec![]);
        assert_eq!(inferred.len(), 1);
        assert!(inferred[0].infer_from_current_roster);
    }

    #[test]
    fn exact_source_identity_beats_route_fallback_and_conflicting_ids_stay_apart() {
        let with_id = |route: Transfer, id| Transfer {
            player_id_fotmob: Some(id),
            ..route
        };
        let first = with_id(route("Alex Example", "Old A", "New A", "2026-08-03"), 101);
        let second = with_id(route("Alex Example", "Old B", "New B", "2026-08-03"), 202);
        let corroborator = with_id(
            from_source(
                "soccerway",
                route("Alex Example", "Old B", "New B", "2026-08-03"),
            ),
            202,
        );
        let result = reconcile(vec![vec![first, second]], vec![], vec![corroborator]);
        assert_eq!(result.len(), 2);
        assert_eq!(sources(&result[0]), ["fotmob"]);
        assert_eq!(sources(&result[1]), ["fotmob", "soccerway"]);

        let a = with_id(route("Alex Example", "Old FC", "New FC", "2026-08-03"), 101);
        let b = with_id(route("Alex Example", "Old FC", "New FC", "2026-08-03"), 202);
        let result = reconcile(vec![vec![a, b]], vec![], vec![]);
        let ids: HashSet<_> = result.iter().map(|t| t.player_id_fotmob).collect();
        assert_eq!(ids, HashSet::from([Some(101), Some(202)]));
    }

    #[test]
    fn two_independent_corroborators_create_one_event() {
        let besoccer = Transfer {
            source_urls: vec!["https://besoccer.example/ada".to_owned()],
            ..from_source("besoccer", ada())
        };
        let sofascore = Transfer {
            fee: "€5M".to_owned(),
            source_urls: vec!["https://sofascore.example/ada".to_owned()],
            ..from_source(
                "sofascore",
                route("Ada Example", "Old", "New", "2026-08-06"),
            )
        };
        let undated = from_source("wikipedia", route("Ada Example", "Old FC", "New FC", ""));
        let result = reconcile(vec![], vec![], vec![besoccer, sofascore, undated]);
        assert_eq!(result.len(), 1);
        let event = &result[0];
        assert_eq!(
            (
                event.from_club.as_str(),
                event.to_club.as_str(),
                event.date.as_str()
            ),
            ("Old FC", "New FC", "2026-08-03")
        );
        assert_eq!(sources(event), ["besoccer", "sofascore", "wikipedia"]);
        assert_eq!(
            event.source_urls,
            [
                "https://besoccer.example/ada",
                "https://sofascore.example/ada"
            ]
        );
        assert_eq!(event.fee, "€5M");
        assert_eq!(event.verification_status, VerificationStatus::Corroborated);
        assert!(!event.infer_from_current_roster);
    }

    #[test]
    fn single_source_or_disagreeing_corroborators_never_create_events() {
        let base = from_source("besoccer", ada());
        let day = |source: &str, date: &str| {
            from_source(source, route("Ada Example", "Old FC", "New FC", date))
        };
        assert!(reconcile(vec![], vec![], vec![day("soccerway", "2026-08-03")]).is_empty());
        assert!(
            reconcile(
                vec![],
                vec![],
                vec![
                    day("soccerway", "2026-08-03"),
                    day("soccerway", "2026-08-04")
                ]
            )
            .is_empty()
        );
        // dates too far apart
        assert!(
            reconcile(
                vec![],
                vec![],
                vec![base.clone(), day("sofascore", "2026-08-07")]
            )
            .is_empty()
        );
        // different destination
        let elsewhere = from_source(
            "soccerway",
            route("Ada Example", "Old FC", "Elsewhere United", "2026-08-03"),
        );
        assert!(reconcile(vec![], vec![], vec![base.clone(), elsewhere]).is_empty());
        // conflicting named source clubs
        let conflicting = from_source(
            "wikipedia",
            route("Ada Example", "Different Origin", "New FC", "2026-08-03"),
        );
        assert!(reconcile(vec![], vec![], vec![base, conflicting]).is_empty());
    }

    fn load_fuzzy_fixture() -> serde_json::Value {
        use std::io::Read;
        let path = concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../testdata/sources_other/expected/fuzzy_key_index.json.gz"
        );
        let mut text = String::new();
        flate2::read::GzDecoder::new(std::fs::File::open(path).expect("fixture"))
            .read_to_string(&mut text)
            .expect("gunzip");
        serde_json::from_str(&text).expect("json")
    }

    #[test]
    fn fuzzy_key_index_equals_recorded_matches_and_a_full_scan() {
        let fixture = load_fuzzy_fixture();
        let clubs: Vec<&str> = fixture["clubs"]
            .as_array()
            .unwrap()
            .iter()
            .map(|c| c.as_str().unwrap())
            .collect();
        let mut index = FuzzyKeyIndex::new(clubs.iter().copied());
        for query in fixture["queries"].as_array().unwrap() {
            let query = query.as_str().unwrap();
            let expected: Vec<&str> = fixture["matches"][query]
                .as_array()
                .unwrap()
                .iter()
                .map(|k| k.as_str().unwrap())
                .collect();
            let found = index.matching_keys(query);
            assert_eq!(
                found.iter().map(String::as_str).collect::<Vec<_>>(),
                expected,
                "recorded result for {query:?}"
            );

            let normalized = normalize(query);
            if normalized.is_empty() {
                continue;
            }
            let scan: HashSet<&str> = index
                .choices
                .iter()
                .map(String::as_str)
                .filter(|key| token_set_ratio(&normalized, key) >= CLUB_MATCH_CUTOFF)
                .collect();
            let got: HashSet<&str> = found.iter().map(String::as_str).collect();
            assert_eq!(got, scan, "full scan for {query:?}");
        }
        assert!(
            index
                .matching_keys("Wolverhampton")
                .iter()
                .any(|key| key == "wolverhamptn")
        );
        assert!(
            index
                .matching_keys("Manchester City")
                .iter()
                .any(|key| key == "city")
        );
    }
}
