//! Optional offline release protection and player-usage metadata.
//!
//! A strict JSON snapshot lists per-club protected players (never auto-released
//! on roster overflow) and season usage counters (a reserve tie-breaker). A
//! missing file means "no policy"; a malformed file fails closed.

use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::Path;

use serde_json::{Map, Value};
use thiserror::Error;

#[derive(Debug, Clone, PartialEq, Eq, Error)]
pub enum ReleasePolicyError {
    /// The file exists but could not be read or is not JSON.
    #[error("could not read release policy {path}: {reason}")]
    Read { path: String, reason: String },
    /// The document violates the schema.
    #[error("{0}")]
    Invalid(String),
}

fn invalid<T>(message: impl Into<String>) -> Result<T, ReleasePolicyError> {
    Err(ReleasePolicyError::Invalid(message.into()))
}

/// Season usage counters, used only as a reserve tie-breaker.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PlayerUsage {
    pub minutes: u64,
    pub starts: u64,
    pub appearances: u64,
    pub news_mentions: u64,
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct ReleasePolicy {
    /// Club (team id) -> player ids that must never be auto-released there.
    pub protected_players: BTreeMap<u32, BTreeSet<u32>>,
    pub usage: BTreeMap<u32, PlayerUsage>,
    pub source: String,
    pub season: String,
    pub as_of: String,
}

/// Load an optional policy file; a missing file yields the empty policy.
pub fn load_release_policy(path: &Path) -> Result<ReleasePolicy, ReleasePolicyError> {
    if !path.is_file() {
        return Ok(ReleasePolicy::default());
    }
    let text = fs::read_to_string(path).map_err(|error| ReleasePolicyError::Read {
        path: path.display().to_string(),
        reason: error.to_string(),
    })?;
    let document: Value =
        serde_json::from_str(&text).map_err(|error| ReleasePolicyError::Read {
            path: path.display().to_string(),
            reason: error.to_string(),
        })?;
    parse_release_policy(&document)
}

/// Validate a parsed policy document.
///
/// Integers must be JSON integers: booleans and floats (`1.0`) are rejected
/// everywhere, including `schema_version` (`true` and `1.0` are not accepted
/// as `1`).
pub fn parse_release_policy(document: &Value) -> Result<ReleasePolicy, ReleasePolicyError> {
    let document = object(Some(document), "release policy")?;
    const ALLOWED: [&str; 6] = [
        "schema_version",
        "source",
        "season",
        "as_of",
        "protected_players",
        "usage",
    ];
    let mut unknown: Vec<&str> = document
        .keys()
        .map(String::as_str)
        .filter(|key| !ALLOWED.contains(key))
        .collect();
    if !unknown.is_empty() {
        unknown.sort_unstable();
        return invalid(format!(
            "release policy has unknown fields: {}",
            quoted_list(&unknown)
        ));
    }
    if document.get("schema_version").and_then(Value::as_i64) != Some(1) {
        return invalid("release policy schema_version must be 1");
    }

    let protected_raw = object(document.get("protected_players"), "protected_players")?;
    let mut protected = BTreeMap::new();
    for (raw_team_id, raw_players) in protected_raw {
        let team_id = id_key(raw_team_id, "protected_players team ID")?;
        let Value::Array(players) = raw_players else {
            return invalid(format!("protected_players[{raw_team_id}] must be a list"));
        };
        let context = format!("protected_players[{raw_team_id}]");
        let mut ids = BTreeSet::new();
        for player in players {
            ids.insert(positive_id(player, &context)?);
        }
        if ids.len() != players.len() {
            return invalid(format!(
                "protected_players[{raw_team_id}] contains duplicates"
            ));
        }
        protected.insert(team_id, ids);
    }

    let usage_raw = object(document.get("usage"), "usage")?;
    let mut usage = BTreeMap::new();
    const USAGE_FIELDS: [&str; 4] = ["minutes", "starts", "appearances", "news_mentions"];
    const REQUIRED_USAGE: [&str; 3] = ["minutes", "starts", "appearances"];
    for (raw_player_id, raw_values) in usage_raw {
        let player_id = id_key(raw_player_id, "usage player ID")?;
        let values = object(Some(raw_values), &format!("usage[{raw_player_id}]"))?;
        let mut unknown: Vec<&str> = values
            .keys()
            .map(String::as_str)
            .filter(|key| !USAGE_FIELDS.contains(key))
            .collect();
        if !unknown.is_empty() {
            unknown.sort_unstable();
            return invalid(format!(
                "usage[{raw_player_id}] has unknown fields: {}",
                quoted_list(&unknown)
            ));
        }
        let missing: Vec<&str> = {
            let mut missing: Vec<&str> = REQUIRED_USAGE
                .into_iter()
                .filter(|key| !values.contains_key(*key))
                .collect();
            missing.sort_unstable();
            missing
        };
        if !missing.is_empty() {
            return invalid(format!(
                "usage[{raw_player_id}] is missing fields: {}",
                quoted_list(&missing)
            ));
        }
        let counter = |field: &str| -> Result<u64, ReleasePolicyError> {
            match values.get(field) {
                Some(value) => nonnegative_int(value, &format!("usage[{raw_player_id}].{field}")),
                None => Ok(0),
            }
        };
        usage.insert(
            player_id,
            PlayerUsage {
                minutes: counter("minutes")?,
                starts: counter("starts")?,
                appearances: counter("appearances")?,
                news_mentions: counter("news_mentions")?,
            },
        );
    }

    Ok(ReleasePolicy {
        protected_players: protected,
        usage,
        source: text(document.get("source"), "release policy source")?,
        season: text(document.get("season"), "release policy season")?,
        as_of: text(document.get("as_of"), "release policy as_of")?,
    })
}

/// Names rendered as a bracketed list of single-quoted strings: `['a', 'b']`.
fn quoted_list(items: &[&str]) -> String {
    let quoted: Vec<String> = items.iter().map(|item| format!("'{item}'")).collect();
    format!("[{}]", quoted.join(", "))
}

fn object<'a>(
    value: Option<&'a Value>,
    context: &str,
) -> Result<&'a Map<String, Value>, ReleasePolicyError> {
    match value {
        Some(Value::Object(map)) => Ok(map),
        _ => invalid(format!("{context} must be an object")),
    }
}

fn text(value: Option<&Value>, context: &str) -> Result<String, ReleasePolicyError> {
    match value {
        None => Ok(String::new()),
        Some(Value::String(s)) if fl_text::strip(s) == s => Ok(s.clone()),
        Some(_) => invalid(format!("{context} must be canonical text")),
    }
}

fn nonnegative_int(value: &Value, context: &str) -> Result<u64, ReleasePolicyError> {
    match value.as_u64() {
        Some(number) => Ok(number),
        None => invalid(format!("{context} must be a non-negative integer")),
    }
}

fn positive_id(value: &Value, context: &str) -> Result<u32, ReleasePolicyError> {
    match value.as_u64() {
        Some(number) if number > 0 && number <= u64::from(u32::MAX) => Ok(number as u32),
        _ => invalid(format!("{context} must be a positive uint32")),
    }
}

fn id_key(value: &str, context: &str) -> Result<u32, ReleasePolicyError> {
    if value.is_empty() || !value.bytes().all(|b| b.is_ascii_digit()) {
        return invalid(format!("{context} must be a decimal ID key"));
    }
    match value.parse::<u64>() {
        Ok(number) if number > 0 && number <= u64::from(u32::MAX) => Ok(number as u32),
        _ => invalid(format!("{context} must be a positive uint32")),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn parse(value: Value) -> Result<ReleasePolicy, String> {
        parse_release_policy(&value).map_err(|e| e.to_string())
    }

    #[test]
    fn valid_policy_round_trips_fields() {
        let policy = parse(json!({
            "schema_version": 1,
            "source": "x",
            "season": "2025/26",
            "as_of": "2026-01-02",
            "protected_players": {"101": [5, 3, 9], "7": []},
            "usage": {
                "5": {"minutes": 90, "starts": 1, "appearances": 2},
                "9": {"minutes": 0, "starts": 0, "appearances": 0, "news_mentions": 4}
            }
        }))
        .unwrap();
        assert!(policy.protected_players[&101].contains(&3));
        assert!(!policy.protected_players[&101].contains(&4));
        assert!(policy.protected_players[&7].is_empty());
        assert_eq!(policy.usage[&5].news_mentions, 0);
        assert_eq!(policy.usage[&9].news_mentions, 4);
        assert_eq!(policy.season, "2025/26");
    }

    #[test]
    fn missing_file_is_empty_policy() {
        let dir = tempfile::tempdir().unwrap();
        let policy = load_release_policy(&dir.path().join("none.json")).unwrap();
        assert_eq!(policy, ReleasePolicy::default());
    }

    #[test]
    fn malformed_json_fails_closed() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("p.json");
        fs::write(&path, "{not json").unwrap();
        assert!(matches!(
            load_release_policy(&path),
            Err(ReleasePolicyError::Read { .. })
        ));
    }

    #[test]
    fn rejects_bad_documents() {
        let base = |extra: Value| {
            let mut doc = json!({"schema_version": 1, "protected_players": {}, "usage": {}});
            for (key, value) in extra.as_object().unwrap() {
                doc[key] = value.clone();
            }
            doc
        };
        assert_eq!(
            parse(base(json!({"schema_version": 2}))).unwrap_err(),
            "release policy schema_version must be 1"
        );
        assert_eq!(
            parse(base(json!({"extra": 1}))).unwrap_err(),
            "release policy has unknown fields: ['extra']"
        );
        assert_eq!(
            parse(base(json!({"source": " padded"}))).unwrap_err(),
            "release policy source must be canonical text"
        );
        assert_eq!(
            parse(base(json!({"protected_players": {"1": [1, 1]}}))).unwrap_err(),
            "protected_players[1] contains duplicates"
        );
        assert_eq!(
            parse(base(json!({"protected_players": {"1": [true]}}))).unwrap_err(),
            "protected_players[1] must be a positive uint32"
        );
        assert_eq!(
            parse(base(json!({"usage": {"1": {"minutes": 1, "starts": 1}}}))).unwrap_err(),
            "usage[1] is missing fields: ['appearances']"
        );
        assert_eq!(
            parse(base(
                json!({"usage": {"1": {"minutes": 1.5, "starts": 1, "appearances": 1}}})
            ))
            .unwrap_err(),
            "usage[1].minutes must be a non-negative integer"
        );
        assert_eq!(
            parse(json!([1])).unwrap_err(),
            "release policy must be an object"
        );
        assert_eq!(
            parse(json!({"schema_version": true, "protected_players": {}, "usage": {}}))
                .unwrap_err(),
            "release policy schema_version must be 1"
        );
    }
}
