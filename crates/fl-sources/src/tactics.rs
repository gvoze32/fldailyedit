//! Evidence-gated game-plan settings from FotMob league aggregates.
//!
//! Only strongly separated, current-season signals produce a decision:
//! settings are relative to the team's league distribution (top/bottom
//! quartile), mid-table values and missing data decide nothing, so the saved
//! game plan is preserved.

use std::collections::{BTreeMap, HashMap};
use std::path::Path;

use chrono::{DateTime, Duration, Utc};
use futures_util::future::join_all;
use serde_json::{Map, Value, json};

use crate::dates::{format_utc_iso, parse_strict_iso_datetime};
use crate::fotmob::{DEFAULT_HEADERS, FotmobError};
use crate::http::{HttpClient, HttpError, Request, RetryPolicy};
use crate::lenient::{finite_number, is_truthy, lenient_int, str_or_empty};
use crate::model::{SquadSnapshot, TacticalUpdate};

pub const LEAGUE_URL: &str = "https://www.fotmob.com/api/data/leagues";
const CACHE_VERSION: i64 = 1;
const CACHE_TTL: Duration = Duration::hours(20);
const CACHE_MAX_AGE: Duration = Duration::days(60);
const MIN_MATCHES: u32 = 3;
const MIN_LEAGUE_TEAMS: usize = 8;
const REQUEST_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(25);

/// Current-season team aggregates supplied by FotMob's league stats endpoint.
const METRICS: [(&str, &str); 5] = [
    ("possession", "possession_percentage_team"),
    ("passes", "accurate_pass_team"),
    ("long_balls", "accurate_long_balls_team"),
    ("crosses", "accurate_cross_team"),
    ("final_third_wins", "poss_won_att_3rd_team"),
];

/// Tactical settings in decision order.
const SETTINGS: [&str; 7] = [
    "attacking_style",
    "build_up",
    "attacking_area",
    "defensive_style",
    "containment_area",
    "pressuring",
    "defensive_line",
];

#[derive(Debug, Clone, Copy, PartialEq)]
struct Metric {
    value: f64,
    matches: u32,
}

/// `_parse_positive_int`: bool and non-integral floats are rejected.
fn positive_int(value: Option<&Value>) -> Option<i64> {
    match value? {
        Value::Bool(_) => None,
        Value::Number(number)
            if number.as_i64().is_none() && number.as_f64().is_some_and(|f| f.fract() != 0.0) =>
        {
            None
        }
        other => lenient_int(other).filter(|n| *n > 0),
    }
}

/// Team id → metric rows of one `TopLists` stat table.
fn parse_metric_table(payload: &Value) -> HashMap<i64, Metric> {
    let mut rows = HashMap::new();
    let Some(tables) = payload.get("TopLists").and_then(Value::as_array) else {
        return rows;
    };
    for table in tables {
        let Some(stat_rows) = table.get("StatList").and_then(Value::as_array) else {
            continue;
        };
        for row in stat_rows.iter().filter(|r| r.is_object()) {
            let matches_played = row
                .get("MatchesPlayed")
                .filter(|v| is_truthy(v))
                .or_else(|| row.get("StatValueCount"));
            let (Some(team_id), Some(value), Some(matches)) = (
                positive_int(row.get("TeamId")),
                finite_number(row.get("StatValue")),
                positive_int(matches_played),
            ) else {
                continue;
            };
            let Ok(matches) = u32::try_from(matches) else {
                continue;
            };
            rows.insert(team_id, Metric { value, matches });
        }
    }
    rows
}

fn valid_stat_url(value: Option<&Value>) -> Option<String> {
    let text = value?.as_str()?;
    let url = reqwest::Url::parse(text).ok()?;
    (url.scheme() == "https"
        && url
            .host_str()
            .is_some_and(|h| h.eq_ignore_ascii_case("data.fotmob.com"))
        && url.path().starts_with("/stats/")
        && url.path().ends_with(".json"))
    .then(|| text.to_owned())
}

/// `(season, metric → fetchAll url)` from a league payload, in payload order.
fn league_stat_urls(payload: &Value) -> (String, Vec<(&'static str, String)>) {
    let details = payload.get("details");
    let season = {
        let selected = str_or_empty(details.and_then(|d| d.get("selectedSeason")));
        if selected.is_empty() {
            str_or_empty(details.and_then(|d| d.get("latestSeason")))
        } else {
            selected
        }
    };
    let Some(team_stats) = payload
        .get("stats")
        .and_then(|s| s.get("teams"))
        .and_then(Value::as_array)
    else {
        return (season, Vec::new());
    };
    let mut urls: Vec<(&'static str, String)> = Vec::new();
    for item in team_stats.iter().filter(|i| i.is_object()) {
        let stat = item.get("participant").and_then(|p| p.get("stat"));
        let name = match stat {
            Some(stat) if stat.is_object() => stat.get("name"),
            _ => item.get("name"),
        };
        let Some(key) = METRICS
            .iter()
            .find(|(_, stat_name)| name.and_then(Value::as_str) == Some(*stat_name))
            .map(|(k, _)| *k)
        else {
            continue;
        };
        if let Some(url) = valid_stat_url(item.get("fetchAllUrl")) {
            match urls.iter_mut().find(|(k, _)| *k == key) {
                Some(slot) => slot.1 = url,
                None => urls.push((key, url)),
            }
        }
    }
    (season, urls)
}

async fn get_object(client: &HttpClient, url: &str) -> Result<Value, FotmobError> {
    let request = Request::get(url)
        .headers(DEFAULT_HEADERS)
        .timeout(REQUEST_TIMEOUT);
    let value = client
        .get_json(&request, &RetryPolicy::FOTMOB)
        .await
        .map_err(|error| match error {
            HttpError::Cancelled => FotmobError::Cancelled,
            other => FotmobError::Incomplete(format!("FotMob request failed for {url}: {other}")),
        })?;
    if value.is_object() {
        Ok(value)
    } else {
        Err(FotmobError::Incomplete(format!(
            "FotMob returned non-object JSON from {url}"
        )))
    }
}

async fn fetch_league_entry(
    client: &HttpClient,
    league_id: i64,
    now: DateTime<Utc>,
) -> Result<Value, FotmobError> {
    let league_url = format!("{LEAGUE_URL}?id={league_id}&ccode3=GBR");
    let league_payload = get_object(client, &league_url).await?;
    let (season, stat_urls) = league_stat_urls(&league_payload);
    if stat_urls.is_empty() {
        return Err(FotmobError::Incomplete(format!(
            "FotMob league {league_id} has no team stat links"
        )));
    }
    let payloads = join_all(stat_urls.iter().map(|(_, url)| get_object(client, url))).await;
    let mut team_metrics: BTreeMap<i64, Map<String, Value>> = BTreeMap::new();
    let mut failed = false;
    for ((metric, _), payload) in stat_urls.iter().zip(payloads) {
        match payload {
            Ok(payload) => {
                for (team_id, metric_row) in parse_metric_table(&payload) {
                    team_metrics.entry(team_id).or_default().insert(
                        (*metric).to_owned(),
                        json!({"value": metric_row.value, "matches": metric_row.matches}),
                    );
                }
            }
            Err(FotmobError::Cancelled) => return Err(FotmobError::Cancelled),
            Err(error) => {
                failed = true;
                tracing::warn!("FotMob team stat {metric} failed for league {league_id}: {error}");
            }
        }
    }
    if team_metrics.is_empty() {
        return Err(FotmobError::Incomplete(format!(
            "FotMob league {league_id} returned no team statistics"
        )));
    }
    let teams: Map<String, Value> = team_metrics
        .into_iter()
        .map(|(id, metrics)| (id.to_string(), Value::Object(metrics)))
        .collect();
    let mut source_urls = vec![league_url];
    source_urls.extend(stat_urls.into_iter().map(|(_, url)| url));
    Ok(json!({
        "season": season,
        "fetched_at": format_utc_iso(now),
        "teams": teams,
        "source_urls": source_urls,
        "partial": failed,
    }))
}

/// Metric of one team profile if it has enough matches and a sane value.
fn entry_metric(profile: &Value, metric: &str, minimum_matches: u32) -> Option<Metric> {
    let item = profile.get(metric).filter(|i| i.is_object())?;
    let value = finite_number(item.get("value"))?;
    let matches = u32::try_from(positive_int(item.get("matches"))?).ok()?;
    (value >= 0.0 && matches >= minimum_matches).then_some(Metric { value, matches })
}

fn percentile(sorted: &[f64], fraction: f64) -> f64 {
    let position = fraction * (sorted.len() - 1) as f64;
    let (lower, upper) = (position.floor() as usize, position.ceil() as usize);
    if lower == upper {
        return sorted[lower];
    }
    let weight = position - lower as f64;
    sorted[lower] * (1.0 - weight) + sorted[upper] * weight
}

fn sorted_values(values: &HashMap<i64, f64>) -> Vec<f64> {
    let mut ordered: Vec<f64> = values.values().copied().collect();
    ordered.sort_by(f64::total_cmp);
    ordered
}

fn quartile_decisions(
    values: &HashMap<i64, f64>,
    low_value: i32,
    high_value: i32,
) -> HashMap<i64, i32> {
    if values.len() < MIN_LEAGUE_TEAMS {
        return HashMap::new();
    }
    let ordered = sorted_values(values);
    let (low, high) = (percentile(&ordered, 0.25), percentile(&ordered, 0.75));
    if low >= high {
        return HashMap::new();
    }
    values
        .iter()
        .filter(|(_, v)| **v >= high || **v <= low)
        .map(|(team, v)| (*team, if *v >= high { high_value } else { low_value }))
        .collect()
}

/// Scale a league-relative profile to the game's 1–10 slider range.
fn slider_decisions(values: &HashMap<i64, f64>) -> HashMap<i64, i32> {
    if values.len() < MIN_LEAGUE_TEAMS {
        return HashMap::new();
    }
    let low = values.values().copied().fold(f64::INFINITY, f64::min);
    let high = values.values().copied().fold(f64::NEG_INFINITY, f64::max);
    if low >= high {
        return HashMap::new();
    }
    let spread = high - low;
    values
        .iter()
        .map(|(team, v)| (*team, 1 + ((v - low) / spread * 9.0 + 0.5) as i32))
        .collect()
}

/// Per-team `(settings, sample matches)` decided from one league entry.
fn league_decisions(entry: &Value) -> HashMap<i64, (BTreeMap<&'static str, i32>, u32)> {
    let Some(teams) = entry.get("teams").and_then(Value::as_object) else {
        return HashMap::new();
    };
    let profiles: HashMap<i64, &Value> = teams
        .iter()
        .filter_map(|(raw_id, profile)| {
            let id = positive_int(Some(&Value::String(raw_id.clone())))?;
            profile.is_object().then_some((id, profile))
        })
        .collect();

    let mut possessions: HashMap<i64, f64> = HashMap::new();
    let mut long_ball_shares: HashMap<i64, f64> = HashMap::new();
    let mut cross_shares: HashMap<i64, f64> = HashMap::new();
    let mut final_third_wins: HashMap<i64, f64> = HashMap::new();
    let mut sample_counts: HashMap<&'static str, HashMap<i64, u32>> =
        SETTINGS.iter().map(|s| (*s, HashMap::new())).collect();
    let mut sample = |setting: &'static str, team: i64, matches: u32| {
        sample_counts
            .get_mut(setting)
            .expect("known setting")
            .insert(team, matches);
    };

    for (&team_id, profile) in &profiles {
        if let Some(possession) =
            entry_metric(profile, "possession", MIN_MATCHES).filter(|m| m.value <= 100.0)
        {
            possessions.insert(team_id, possession.value);
            sample("attacking_style", team_id, possession.matches);
        }
        let passes = entry_metric(profile, "passes", MIN_MATCHES);
        if let (Some(passes), Some(long_balls)) =
            (passes, entry_metric(profile, "long_balls", MIN_MATCHES))
            && passes.matches == long_balls.matches
            && passes.value > 0.0
        {
            long_ball_shares.insert(team_id, long_balls.value / passes.value);
            sample("build_up", team_id, passes.matches);
        }
        if let (Some(passes), Some(crosses)) =
            (passes, entry_metric(profile, "crosses", MIN_MATCHES))
            && passes.matches == crosses.matches
            && passes.value > 0.0
        {
            cross_shares.insert(team_id, crosses.value / passes.value);
            sample("attacking_area", team_id, passes.matches);
            sample("containment_area", team_id, passes.matches);
        }
        if let Some(final_third) = entry_metric(profile, "final_third_wins", MIN_MATCHES) {
            final_third_wins.insert(team_id, final_third.value);
            for setting in ["defensive_style", "pressuring", "defensive_line"] {
                sample(setting, team_id, final_third.matches);
            }
        }
    }

    let decisions: [(&'static str, HashMap<i64, i32>); 7] = [
        // 0 counter attack, 1 possession game
        ("attacking_style", quartile_decisions(&possessions, 0, 1)),
        // 1 short pass, 0 long pass
        ("build_up", quartile_decisions(&long_ball_shares, 1, 0)),
        // 1 centre, 0 wide
        ("attacking_area", quartile_decisions(&cross_shares, 1, 0)),
        // 1 all-out defense, 0 frontline pressure
        (
            "defensive_style",
            quartile_decisions(&final_third_wins, 1, 0),
        ),
        // 0 middle, 1 wide
        ("containment_area", quartile_decisions(&cross_shares, 0, 1)),
        // 1 conservative, 0 aggressive
        ("pressuring", quartile_decisions(&final_third_wins, 1, 0)),
        // No parsed FotMob metric measures block compactness independently of
        // final-third recoveries; compactness stays as saved.
        ("defensive_line", slider_decisions(&final_third_wins)),
    ];

    let mut result = HashMap::new();
    for &team_id in profiles.keys() {
        let settings: BTreeMap<&'static str, i32> = decisions
            .iter()
            .filter_map(|(setting, by_team)| by_team.get(&team_id).map(|v| (*setting, *v)))
            .collect();
        if settings.is_empty() {
            continue;
        }
        let matches = settings
            .keys()
            .filter_map(|setting| sample_counts[setting].get(&team_id))
            .min()
            .copied()
            .unwrap_or(0);
        result.insert(team_id, (settings, matches));
    }
    result
}

fn entry_age_ok(entry: &Value, now: DateTime<Utc>, accept: impl Fn(Duration) -> bool) -> bool {
    entry
        .get("fetched_at")
        .and_then(Value::as_str)
        .and_then(parse_strict_iso_datetime)
        .is_some_and(|fetched| accept(now - fetched))
}

fn entry_is_fresh(entry: &Value, now: DateTime<Utc>) -> bool {
    entry.is_object()
        && entry_age_ok(entry, now, |age| age >= Duration::zero() && age < CACHE_TTL)
        && entry.get("teams").is_some_and(Value::is_object)
        && entry.get("partial") != Some(&Value::Bool(true))
}

fn entry_is_within_age(entry: &Value, now: DateTime<Utc>) -> bool {
    entry.is_object() && entry_age_ok(entry, now, |age| age <= CACHE_MAX_AGE)
}

fn load_cache(path: &Path) -> Map<String, Value> {
    let empty = || Map::new();
    let bytes = match std::fs::read(path) {
        Ok(bytes) => bytes,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return empty(),
        Err(error) => {
            tracing::warn!("Ignoring unreadable FotMob tactics cache: {error}");
            return empty();
        }
    };
    match serde_json::from_slice::<Value>(&bytes) {
        Ok(Value::Object(mut payload))
            if payload.get("version").and_then(Value::as_i64) == Some(CACHE_VERSION) =>
        {
            match payload.remove("leagues") {
                Some(Value::Object(leagues)) => leagues,
                _ => {
                    tracing::warn!("Ignoring incompatible FotMob tactics cache");
                    empty()
                }
            }
        }
        Ok(_) => {
            tracing::warn!("Ignoring incompatible FotMob tactics cache");
            empty()
        }
        Err(error) => {
            tracing::warn!("Ignoring unreadable FotMob tactics cache: {error}");
            empty()
        }
    }
}

fn save_cache(path: &Path, leagues: Map<String, Value>) {
    let payload = json!({"version": CACHE_VERSION, "leagues": leagues});
    let mut bytes = payload.to_string().into_bytes();
    bytes.push(b'\n');
    if let Err(error) = fl_fs::write_atomic(path, &bytes) {
        tracing::warn!("Could not save FotMob tactics cache: {error}");
    }
}

/// Decide evidence-backed tactical settings for the clubs of `snapshots`.
///
/// League aggregates are cached in `cache_path` (one JSON file) for 20 hours;
/// a league whose fetch fails is skipped with a warning and decides nothing.
/// Only cancellation is an error.
pub async fn fetch_tactical_updates(
    client: &HttpClient,
    snapshots: &[SquadSnapshot],
    cache_path: &Path,
    now: DateTime<Utc>,
) -> Result<Vec<TacticalUpdate>, FotmobError> {
    // team id → (club name, league id); later snapshots override, first position kept.
    let mut targets: Vec<(i64, String, i64)> = Vec::new();
    for snapshot in snapshots {
        let Some(league_id) = snapshot.primary_league_id.filter(|id| *id > 0) else {
            continue;
        };
        if snapshot.team_id_fotmob <= 0 {
            continue;
        }
        let name = snapshot.club_name.trim().to_owned();
        match targets
            .iter_mut()
            .find(|(team, ..)| *team == snapshot.team_id_fotmob)
        {
            Some(slot) => *slot = (snapshot.team_id_fotmob, name, league_id),
            None => targets.push((snapshot.team_id_fotmob, name, league_id)),
        }
    }
    if targets.is_empty() {
        return Ok(Vec::new());
    }

    let mut league_cache = load_cache(cache_path);
    let mut requested: Vec<i64> = targets.iter().map(|(_, _, league)| *league).collect();
    requested.sort_unstable();
    requested.dedup();
    let mut entries: HashMap<i64, Value> = HashMap::new();
    let mut stale: Vec<i64> = Vec::new();
    for league_id in requested {
        match league_cache
            .get(&league_id.to_string())
            .filter(|entry| entry_is_fresh(entry, now))
        {
            Some(entry) => {
                entries.insert(league_id, entry.clone());
            }
            None => stale.push(league_id),
        }
    }

    let results = join_all(
        stale
            .iter()
            .map(|league_id| fetch_league_entry(client, *league_id, now)),
    )
    .await;
    for (league_id, result) in stale.into_iter().zip(results) {
        match result {
            Ok(entry) => {
                league_cache.insert(league_id.to_string(), entry.clone());
                entries.insert(league_id, entry);
            }
            Err(FotmobError::Cancelled) => return Err(FotmobError::Cancelled),
            Err(error) => {
                tracing::warn!("FotMob tactical stats skipped for league {league_id}: {error}")
            }
        }
    }

    league_cache.retain(|_, entry| entry_is_fresh(entry, now) || entry_is_within_age(entry, now));
    save_cache(cache_path, league_cache);

    let decisions: HashMap<i64, _> = entries
        .iter()
        .map(|(league_id, entry)| (*league_id, league_decisions(entry)))
        .collect();
    let mut updates = Vec::new();
    for (team_id, club_name, league_id) in targets {
        let Some(entry) = entries.get(&league_id) else {
            continue;
        };
        let Some((settings, matches)) = decisions.get(&league_id).and_then(|d| d.get(&team_id))
        else {
            continue;
        };
        updates.push(TacticalUpdate {
            club_name,
            team_id_fotmob: team_id,
            league_id,
            settings: settings
                .iter()
                .map(|(name, value)| ((*name).to_owned(), *value))
                .collect(),
            sample_matches: *matches,
            source_urls: entry
                .get("source_urls")
                .and_then(Value::as_array)
                .map(|urls| urls.iter().map(crate::lenient::display_str).collect())
                .unwrap_or_default(),
        });
    }
    Ok(updates)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn profile(
        possession: f64,
        passes: f64,
        long_balls: f64,
        crosses: f64,
        final_third: f64,
        matches: u32,
    ) -> Value {
        let metric = |value: f64| json!({"value": value, "matches": matches});
        json!({
            "possession": metric(possession),
            "passes": metric(passes),
            "long_balls": metric(long_balls),
            "crosses": metric(crosses),
            "final_third_wins": metric(final_third),
        })
    }

    fn league(count: i64, matches: u32) -> Value {
        let teams: Map<String, Value> = (1..=count)
            .map(|i| {
                let f = i as f64;
                (
                    i.to_string(),
                    profile(
                        40.0 + f,
                        300.0 + f * 10.0,
                        30.0 + f,
                        10.0 + f * 2.0,
                        5.0 + f,
                        matches,
                    ),
                )
            })
            .collect();
        json!({"teams": teams})
    }

    #[test]
    fn decisions_pick_quartile_extremes_only() {
        let decisions = league_decisions(&league(12, 10));
        // Highest possession => possession game; lowest => counter attack.
        assert_eq!(decisions[&12].0["attacking_style"], 1);
        assert_eq!(decisions[&1].0["attacking_style"], 0);
        // Mid-table teams decide nothing for quartile settings.
        assert!(!decisions[&6].0.contains_key("attacking_style"));
        // Slider covers every team: lowest 1, highest 10.
        assert_eq!(decisions[&1].0["defensive_line"], 1);
        assert_eq!(decisions[&12].0["defensive_line"], 10);
        assert_eq!(decisions[&12].1, 10);
    }

    #[test]
    fn small_leagues_and_thin_samples_decide_nothing() {
        assert!(league_decisions(&league(7, 10)).is_empty());
        assert!(league_decisions(&league(12, 2)).is_empty());
    }

    #[test]
    fn percentile_interpolates() {
        assert_eq!(percentile(&[1.0, 2.0, 3.0, 4.0], 0.25), 1.75);
        assert_eq!(percentile(&[1.0, 2.0, 3.0, 4.0, 5.0], 0.5), 3.0);
    }

    #[test]
    fn metric_table_requires_valid_rows() {
        let payload = json!({"TopLists": [{"StatList": [
            {"TeamId": 5, "StatValue": 55.5, "MatchesPlayed": 10},
            {"TeamId": 6, "StatValue": "61", "StatValueCount": 9},
            {"TeamId": 0, "StatValue": 1.0, "MatchesPlayed": 3},
            {"TeamId": 7, "StatValue": "x", "MatchesPlayed": 3},
            {"TeamId": 8, "StatValue": 1.0, "MatchesPlayed": 2.5},
        ]}]});
        let rows = parse_metric_table(&payload);
        assert_eq!(rows.len(), 2);
        assert_eq!(
            rows[&5],
            Metric {
                value: 55.5,
                matches: 10
            }
        );
        assert_eq!(
            rows[&6],
            Metric {
                value: 61.0,
                matches: 9
            }
        );
    }

    #[test]
    fn stat_urls_are_validated() {
        let payload = json!({
            "details": {"selectedSeason": "2025/2026"},
            "stats": {"teams": [
                {"participant": {"stat": {"name": "possession_percentage_team"}}, "fetchAllUrl": "https://data.fotmob.com/stats/47/season/1/possession.json"},
                {"name": "accurate_pass_team", "fetchAllUrl": "https://evil.example/stats/x.json"},
                {"name": "accurate_cross_team", "fetchAllUrl": "https://data.fotmob.com/stats/47/season/1/cross.json"},
            ]},
        });
        let (season, urls) = league_stat_urls(&payload);
        assert_eq!(season, "2025/2026");
        assert_eq!(
            urls.iter().map(|(k, _)| *k).collect::<Vec<_>>(),
            ["possession", "crosses"]
        );
    }

    #[test]
    fn cache_freshness_rules() {
        let now = DateTime::parse_from_rfc3339("2026-08-10T12:00:00Z")
            .unwrap()
            .with_timezone(&Utc);
        let entry = |ago: Duration, partial: bool| json!({"fetched_at": format_utc_iso(now - ago), "teams": {}, "partial": partial});
        assert!(entry_is_fresh(&entry(Duration::hours(19), false), now));
        assert!(!entry_is_fresh(&entry(Duration::hours(21), false), now));
        assert!(!entry_is_fresh(&entry(Duration::hours(1), true), now));
        assert!(entry_is_within_age(&entry(Duration::days(59), true), now));
        assert!(!entry_is_within_age(&entry(Duration::days(61), false), now));
    }
}
