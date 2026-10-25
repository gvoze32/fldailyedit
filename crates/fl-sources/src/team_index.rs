//! The embedded FotMob team catalog (`data/fotmob_teams.json`).
//!
//! The catalog is the only input club-identity binding has for mapping save
//! clubs to FotMob ids; the engine builds its index from [`embedded_catalog`].

use std::sync::LazyLock;

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::lenient::{lenient_int, trimmed};

const EMBEDDED_CATALOG: &str = include_str!("../../../data/fotmob_teams.json");

/// One FotMob club. `name` is the title-cased club slug (the identity
/// binder's only name source), not FotMob's display name.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct TeamEntry {
    pub fotmob_id: i64,
    pub name: String,
    #[serde(default)]
    pub slug: String,
    #[serde(default)]
    pub url: String,
}

#[derive(Debug, thiserror::Error)]
pub enum TeamIndexError {
    #[error("FotMob team catalog is not valid JSON: {0}")]
    Json(#[from] serde_json::Error),
    #[error("FotMob team catalog must contain a JSON array")]
    NotAnArray,
    #[error("FotMob team entry is missing fotmob_id")]
    MissingId,
    #[error("Invalid or duplicate FotMob team entry: {0}")]
    InvalidEntry(i64),
}

/// Parse and validate catalog JSON (array of entries; ids unique, names non-empty).
pub fn parse_catalog(json: &str) -> Result<Vec<TeamEntry>, TeamIndexError> {
    let Value::Array(items) = serde_json::from_str(json)? else {
        return Err(TeamIndexError::NotAnArray);
    };
    let mut seen = std::collections::HashSet::new();
    let mut entries = Vec::with_capacity(items.len());
    for item in &items {
        let id = item
            .as_object()
            .and_then(|object| object.get("fotmob_id"))
            .ok_or(TeamIndexError::MissingId)
            .and_then(|value| lenient_int(value).ok_or(TeamIndexError::MissingId))?;
        let name = {
            let name = trimmed(item.get("name"));
            if name.is_empty() {
                trimmed(item.get("slug"))
            } else {
                name
            }
        };
        if name.is_empty() || !seen.insert(id) {
            return Err(TeamIndexError::InvalidEntry(id));
        }
        entries.push(TeamEntry {
            fotmob_id: id,
            name,
            slug: trimmed(item.get("slug")),
            url: trimmed(item.get("url")),
        });
    }
    Ok(entries)
}

static EMBEDDED: LazyLock<Vec<TeamEntry>> = LazyLock::new(|| {
    parse_catalog(EMBEDDED_CATALOG)
        .expect("embedded data/fotmob_teams.json is valid (checked by tests)")
});

/// The catalog shipped inside the binary.
pub fn embedded_catalog() -> &'static [TeamEntry] {
    &EMBEDDED
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn embedded_catalog_is_valid() {
        assert!(embedded_catalog().len() > 5000);
    }

    #[test]
    fn parse_catalog_validates() {
        assert!(matches!(
            parse_catalog("{}"),
            Err(TeamIndexError::NotAnArray)
        ));
        assert!(matches!(
            parse_catalog("[{}]"),
            Err(TeamIndexError::MissingId)
        ));
        assert!(matches!(
            parse_catalog(r#"[{"fotmob_id": 1, "name": " "}]"#),
            Err(TeamIndexError::InvalidEntry(1))
        ));
        assert!(matches!(
            parse_catalog(r#"[{"fotmob_id": 1, "name": "A"}, {"fotmob_id": "1", "slug": "b"}]"#),
            Err(TeamIndexError::InvalidEntry(1))
        ));
        let parsed = parse_catalog(r#"[{"fotmob_id": "7", "slug": " x-y "}]"#).unwrap();
        assert_eq!((parsed[0].fotmob_id, parsed[0].name.as_str()), (7, "x-y"));
    }
}
