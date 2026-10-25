//! Football Life player/team name catalogs.
//!
//! Embedded reference data (`data/FL2622wc_players.txt`, `data/FL262_teams.txt`,
//! legacy `data/players.csv`) plus the logic that merges it with the players
//! edited inside a save. The text catalogs are strict `ID - Name` files: a
//! malformed, conflicting or too-small file is rejected, never partially used.

use std::collections::{HashMap, HashSet};

use thiserror::Error;
use unicode_normalization::UnicodeNormalization;

use crate::model::PlayerInfo;

/// Current FL player names (`ID - Name`, 29k entries).
pub const EMBEDDED_PLAYERS_TXT: &str = include_str!("../../../data/FL2622wc_players.txt");
/// Current FL team names (`ID - Name`, 749 entries).
pub const EMBEDDED_TEAMS_TXT: &str = include_str!("../../../data/FL262_teams.txt");
/// Legacy `PlayerID,PlayerName` CSV, only used for rostered ids missing from the
/// current catalog.
pub const EMBEDDED_LEGACY_PLAYERS_CSV: &str = include_str!("../../../data/players.csv");

pub const MIN_CURRENT_PLAYER_CATALOG_SIZE: usize = 25_000;
pub const MIN_TEAM_CATALOG_SIZE: usize = 700;

#[derive(Debug, Clone, PartialEq, Eq, Error)]
pub enum PlayerCatalogError {
    #[error("Malformed {label} catalog line {line}")]
    MalformedLine { label: String, line: usize },
    #[error("Conflicting {label} names for ID {id}: {previous:?} vs {name:?}")]
    ConflictingNames {
        label: String,
        id: u32,
        previous: String,
        name: String,
    },
    #[error("{label} catalog has only {found} entries; expected at least {minimum}")]
    TooFewEntries {
        label: String,
        found: String,
        minimum: String,
    },
    #[error("Legacy player CSV requires PlayerID and PlayerName columns")]
    LegacyCsvColumns,
    #[error("Could not read legacy player CSV: {0}")]
    LegacyCsvRead(String),
    #[error("Player catalog misses {count} roster IDs (first: {preview})")]
    MissingRosterIds { count: String, preview: String },
}

/// A built player catalog with its coverage report.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct PlayerCatalog {
    pub players: HashMap<u32, PlayerInfo>,
    pub report: PlayerCatalogReport,
}

/// Coverage summary of one catalog build.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct PlayerCatalogReport {
    pub current_entries: usize,
    pub legacy_roster_fallbacks: usize,
    pub edited_entries: usize,
    pub roster_entries: usize,
    /// Rostered ids absent from every source, ascending.
    pub missing_roster_ids: Vec<u32>,
    /// Rostered ids with a known position.
    pub positions: usize,
    /// Rostered ids with a known age.
    pub ages: usize,
}

/// Name sources for [`build_player_catalog`] (contents, not paths).
#[derive(Debug, Clone, Copy, Default)]
pub struct CatalogSources<'a> {
    /// `ID - Name` text; when present it is authoritative and every rostered id
    /// must be covered (by it, the legacy CSV or the save).
    pub current: Option<&'a str>,
    /// Legacy `PlayerID,PlayerName` CSV used only for rostered ids absent from `current`.
    pub legacy_csv: Option<&'a str>,
}

impl CatalogSources<'static> {
    /// The embedded FL catalog plus the embedded legacy CSV.
    pub fn embedded() -> Self {
        Self {
            current: Some(EMBEDDED_PLAYERS_TXT),
            legacy_csv: Some(EMBEDDED_LEGACY_PLAYERS_CSV),
        }
    }
}

fn transliterate(c: char) -> Option<&'static str> {
    Some(match c {
        'Æ' => "AE",
        'æ' => "ae",
        'Ð' | 'Đ' => "D",
        'ð' | 'đ' => "d",
        'Ł' => "L",
        'ł' => "l",
        'Ø' => "O",
        'ø' => "o",
        'Œ' => "OE",
        'œ' => "oe",
        'Þ' => "TH",
        'þ' => "th",
        'ß' => "ss",
        _ => return None,
    })
}

/// ASCII-ish lowercase alphanumeric name tokens for conservative cross-reference checks.
fn name_tokens(value: &str) -> Vec<String> {
    let mut translated = String::with_capacity(value.len());
    for c in value.chars() {
        match transliterate(c) {
            Some(replacement) => translated.push_str(replacement),
            None => translated.push(c),
        }
    }
    let ascii: String = translated
        .nfkd()
        .filter(char::is_ascii)
        .map(|c| c.to_ascii_lowercase())
        .collect();
    ascii
        .split(|c: char| !c.is_ascii_alphanumeric())
        .filter(|token| !token.is_empty())
        .map(str::to_owned)
        .collect()
}

/// Check that a legacy full name agrees with native display metadata.
pub fn legacy_name_matches_native(
    legacy_name: &str,
    native_name: &str,
    native_print_name: &str,
) -> bool {
    let long = |tokens: Vec<String>| -> HashSet<String> {
        tokens
            .into_iter()
            .filter(|token| token.len() >= 4)
            .collect()
    };
    let legacy_tokens = long(name_tokens(legacy_name));
    let mut native_tokens = long(name_tokens(native_name));
    native_tokens.extend(long(name_tokens(native_print_name)));
    if legacy_tokens.is_empty() || native_tokens.is_empty() {
        return false;
    }
    if legacy_tokens
        .iter()
        .any(|token| native_tokens.contains(token))
    {
        return true;
    }
    // Native print names often collapse initials and punctuation (e.g. M BOMA,
    // JSCHMIDT). Accept only a matching suffix, never a loose prefix.
    legacy_tokens.iter().any(|legacy| {
        native_tokens
            .iter()
            .any(|native| legacy.ends_with(native.as_str()) || native.ends_with(legacy.as_str()))
    })
}

/// `1234567` -> `"1,234,567"`.
pub(crate) fn with_separators(value: usize) -> String {
    let digits = value.to_string();
    let mut out = String::with_capacity(digits.len() + digits.len() / 3);
    for (index, c) in digits.chars().enumerate() {
        if index > 0 && (digits.len() - index).is_multiple_of(3) {
            out.push(',');
        }
        out.push(c);
    }
    out
}

fn title_case(label: &str) -> String {
    let mut chars = label.chars();
    chars
        .next()
        .map(|first| first.to_uppercase().collect::<String>() + chars.as_str())
        .unwrap_or_default()
}

/// Parse strict `ID - Name` reference data; no partial files are accepted.
///
/// IDs must fit `u32`. The same id repeated with the same name is fine; with a
/// different name it is a conflict.
pub fn load_id_name_text(
    text: &str,
    label: &str,
    minimum_entries: usize,
) -> Result<HashMap<u32, String>, PlayerCatalogError> {
    let malformed = |line: usize| PlayerCatalogError::MalformedLine {
        label: label.to_owned(),
        line,
    };
    let mut entries: HashMap<u32, String> = HashMap::new();
    for (index, raw_line) in fl_text::split_lines(text).into_iter().enumerate() {
        let line_number = index + 1;
        let line = fl_text::strip(raw_line);
        if line.is_empty() {
            continue;
        }
        let (raw_id, raw_name) = line.split_once('-').ok_or_else(|| malformed(line_number))?;
        let raw_id = fl_text::strip(raw_id);
        let name = fl_text::strip(raw_name);
        if name.is_empty() || raw_id.is_empty() || !raw_id.bytes().all(|b| b.is_ascii_digit()) {
            return Err(malformed(line_number));
        }
        let id: u32 = raw_id.parse().map_err(|_| malformed(line_number))?;
        if let Some(previous) = entries.get(&id)
            && previous != name
        {
            return Err(PlayerCatalogError::ConflictingNames {
                label: label.to_owned(),
                id,
                previous: previous.clone(),
                name: name.to_owned(),
            });
        }
        entries.insert(id, name.to_owned());
    }
    if entries.len() < minimum_entries {
        return Err(PlayerCatalogError::TooFewEntries {
            label: title_case(label),
            found: with_separators(entries.len()),
            minimum: with_separators(minimum_entries),
        });
    }
    Ok(entries)
}

/// The embedded current player catalog.
pub fn embedded_player_names() -> Result<HashMap<u32, String>, PlayerCatalogError> {
    load_id_name_text(
        EMBEDDED_PLAYERS_TXT,
        "player",
        MIN_CURRENT_PLAYER_CATALOG_SIZE,
    )
}

/// The embedded current team catalog.
pub fn embedded_team_names() -> Result<HashMap<u32, String>, PlayerCatalogError> {
    load_id_name_text(EMBEDDED_TEAMS_TXT, "team", MIN_TEAM_CATALOG_SIZE)
}

/// Recover rostered ids missing from the current reference, never stale free agents.
fn legacy_roster_fallbacks(
    csv_text: Option<&str>,
    roster_ids: &HashSet<u32>,
    known_ids: &HashSet<u32>,
) -> Result<HashMap<u32, PlayerInfo>, PlayerCatalogError> {
    let Some(csv_text) = csv_text else {
        return Ok(HashMap::new());
    };
    let csv_text = csv_text.strip_prefix('\u{feff}').unwrap_or(csv_text);
    let mut reader = csv::ReaderBuilder::new()
        .has_headers(true)
        .flexible(true)
        .from_reader(csv_text.as_bytes());
    let read_error = |error: csv::Error| PlayerCatalogError::LegacyCsvRead(error.to_string());
    let headers = reader.headers().map_err(read_error)?.clone();
    // DictReader semantics: with duplicate headers the last column wins.
    let column = |name: &str| {
        headers
            .iter()
            .enumerate()
            .filter(|(_, header)| *header == name)
            .map(|(index, _)| index)
            .last()
    };
    let (Some(id_column), Some(name_column)) = (column("PlayerID"), column("PlayerName")) else {
        return Err(PlayerCatalogError::LegacyCsvColumns);
    };

    let mut fallbacks = HashMap::new();
    for record in reader.records() {
        let record = record.map_err(read_error)?;
        let raw_id = fl_text::strip(record.get(id_column).unwrap_or(""));
        let name = fl_text::strip(record.get(name_column).unwrap_or(""));
        if raw_id.is_empty() || !raw_id.bytes().all(|b| b.is_ascii_digit()) || name.is_empty() {
            continue;
        }
        // Ids beyond u32 can never be in `roster_ids`.
        let Ok(player_id) = raw_id.parse::<u32>() else {
            continue;
        };
        if roster_ids.contains(&player_id) && !known_ids.contains(&player_id) {
            fallbacks.insert(
                player_id,
                PlayerInfo {
                    player_id,
                    name: name.to_owned(),
                    print_name: name.to_owned(),
                    ..PlayerInfo::default()
                },
            );
        }
    }
    Ok(fallbacks)
}

/// Legacy names for an explicit set of caller-validated ids.
pub fn load_legacy_player_names(
    csv_text: Option<&str>,
    player_ids: &HashSet<u32>,
) -> Result<HashMap<u32, String>, PlayerCatalogError> {
    Ok(
        legacy_roster_fallbacks(csv_text, player_ids, &HashSet::new())?
            .into_iter()
            .map(|(id, player)| (id, player.name))
            .collect(),
    )
}

/// Build a player catalog from the optional current reference, the legacy
/// fallbacks for rostered ids, and the players edited inside the save.
///
/// The current reference is authoritative for names: edited records embedded in
/// a save are often abbreviated, but may carry metadata (position, age,
/// overall) the name-only reference lacks. With a current reference every
/// rostered id must be covered.
pub fn build_player_catalog(
    sources: CatalogSources<'_>,
    edited_players: &HashMap<u32, PlayerInfo>,
    roster_ids: &HashSet<u32>,
) -> Result<(HashMap<u32, PlayerInfo>, PlayerCatalogReport), PlayerCatalogError> {
    let current_names = match sources.current {
        Some(text) => load_id_name_text(text, "player", MIN_CURRENT_PLAYER_CATALOG_SIZE)?,
        None => HashMap::new(),
    };
    let mut players: HashMap<u32, PlayerInfo> = current_names
        .iter()
        .map(|(&player_id, name)| {
            (
                player_id,
                PlayerInfo {
                    player_id,
                    name: name.clone(),
                    print_name: name.clone(),
                    ..PlayerInfo::default()
                },
            )
        })
        .collect();
    let known: HashSet<u32> = players.keys().copied().collect();
    let fallbacks = legacy_roster_fallbacks(sources.legacy_csv, roster_ids, &known)?;
    let fallback_count = fallbacks.len();
    players.extend(fallbacks);

    for (&player_id, edited) in edited_players {
        match players.get_mut(&player_id) {
            None => {
                players.insert(player_id, edited.clone());
            }
            Some(existing) => {
                if existing.print_name.is_empty() {
                    existing.print_name.clone_from(&edited.print_name);
                }
                existing.position = edited.position.or(existing.position);
                existing.age = if edited.age != 0 {
                    edited.age
                } else {
                    existing.age
                };
                existing.overall = if edited.overall != 0 {
                    edited.overall
                } else {
                    existing.overall
                };
            }
        }
    }

    let mut missing: Vec<u32> = roster_ids
        .iter()
        .copied()
        .filter(|id| !players.contains_key(id))
        .collect();
    missing.sort_unstable();
    let report = PlayerCatalogReport {
        current_entries: current_names.len(),
        legacy_roster_fallbacks: fallback_count,
        edited_entries: edited_players.len(),
        roster_entries: roster_ids.len(),
        positions: coverage(&players, roster_ids, |p| p.position.is_some()),
        ages: coverage(&players, roster_ids, |p| p.age != 0),
        missing_roster_ids: missing,
    };
    if sources.current.is_some() && !report.missing_roster_ids.is_empty() {
        let preview: Vec<String> = report
            .missing_roster_ids
            .iter()
            .take(10)
            .map(u32::to_string)
            .collect();
        return Err(PlayerCatalogError::MissingRosterIds {
            count: with_separators(report.missing_roster_ids.len()),
            preview: preview.join(", "),
        });
    }
    Ok((players, report))
}

/// How many `roster_ids` have a player satisfying `has`.
pub(crate) fn coverage(
    players: &HashMap<u32, PlayerInfo>,
    roster_ids: &HashSet<u32>,
    has: impl Fn(&PlayerInfo) -> bool,
) -> usize {
    roster_ids
        .iter()
        .filter(|id| players.get(id).is_some_and(&has))
        .count()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::Position;

    fn info(id: u32, name: &str) -> PlayerInfo {
        PlayerInfo {
            player_id: id,
            name: name.into(),
            print_name: name.into(),
            ..PlayerInfo::default()
        }
    }

    #[test]
    fn id_name_text_parses_crlf_blank_lines_and_hyphenated_names() {
        let entries =
            load_id_name_text("91 - Hong Myung-Bo\r\n\r\n 5 -  Pelé \n", "player", 2).unwrap();
        assert_eq!(entries[&91], "Hong Myung-Bo");
        assert_eq!(entries[&5], "Pelé");
    }

    #[test]
    fn id_name_text_rejects_partial_data() {
        for bad in ["nodash", "x - Name", "7 -", "- Name", "99999999999 - Big"] {
            assert!(
                matches!(
                    load_id_name_text(bad, "player", 1),
                    Err(PlayerCatalogError::MalformedLine { line: 1, .. })
                ),
                "{bad}"
            );
        }
        assert!(matches!(
            load_id_name_text("1 - A\n1 - B", "team", 1),
            Err(PlayerCatalogError::ConflictingNames { id: 1, .. })
        ));
        assert!(load_id_name_text("1 - A\n1 - A", "team", 1).is_ok());
        let error = load_id_name_text("1 - A", "player", 25_000).unwrap_err();
        assert_eq!(
            error.to_string(),
            "Player catalog has only 1 entries; expected at least 25,000"
        );
    }

    #[test]
    fn embedded_catalogs_are_valid() {
        let players = embedded_player_names().unwrap();
        assert!(players.len() >= MIN_CURRENT_PLAYER_CATALOG_SIZE);
        assert_eq!(players[&91], "Hong Myung-Bo");
        let teams = embedded_team_names().unwrap();
        assert!(teams.len() >= MIN_TEAM_CATALOG_SIZE);
        assert_eq!(teams[&1], "Ireland");
    }

    #[test]
    fn legacy_csv_only_recovers_rostered_unknown_ids() {
        let csv = "\u{feff}PlayerID,PlayerName\n1,Known Current\n2,\"Roster, Fallback\"\n3,Free Agent\nx,Bad\n,Blank\n";
        let roster: HashSet<u32> = [1, 2].into();
        let known: HashSet<u32> = [1].into();
        let found = legacy_roster_fallbacks(Some(csv), &roster, &known).unwrap();
        assert_eq!(found.len(), 1);
        assert_eq!(found[&2].name, "Roster, Fallback");
        assert!(matches!(
            legacy_roster_fallbacks(Some("PlayerID,Name\n1,A\n"), &roster, &known),
            Err(PlayerCatalogError::LegacyCsvColumns)
        ));
        assert!(
            legacy_roster_fallbacks(None, &roster, &known)
                .unwrap()
                .is_empty()
        );
    }

    #[test]
    fn edited_players_enrich_but_never_rename_current_entries() {
        let current = (1..=3)
            .map(|id| format!("{id} - Current {id}"))
            .collect::<Vec<_>>()
            .join("\n");
        let mut edited = HashMap::new();
        edited.insert(
            1,
            PlayerInfo {
                player_id: 1,
                name: "Abbrev".into(),
                print_name: "ABB".into(),
                position: Some(Position::Cf),
                age: 21,
                overall: 80,
            },
        );
        edited.insert(9, info(9, "Edited Only"));
        let roster: HashSet<u32> = [1, 9].into();
        let sources = CatalogSources {
            current: Some(&current),
            legacy_csv: None,
        };
        // 25_000-entry minimum applies to the real catalog; use no minimum via direct call.
        let err = build_player_catalog(sources, &edited, &roster).unwrap_err();
        assert!(matches!(err, PlayerCatalogError::TooFewEntries { .. }));

        let (players, report) =
            build_player_catalog(CatalogSources::default(), &edited, &roster).unwrap();
        assert_eq!(players[&1].name, "Abbrev");
        assert_eq!(report.edited_entries, 2);
        assert_eq!(report.positions, 1);
        assert_eq!(report.ages, 1);
        assert!(report.missing_roster_ids.is_empty());
    }

    #[test]
    fn real_catalog_merge_and_missing_roster_error() {
        let mut edited = HashMap::new();
        edited.insert(
            91,
            PlayerInfo {
                player_id: 91,
                name: "H. Myung".into(),
                print_name: String::new(),
                position: Some(Position::Cb),
                age: 30,
                overall: 70,
            },
        );
        let roster: HashSet<u32> = [91].into();
        let (players, report) =
            build_player_catalog(CatalogSources::embedded(), &edited, &roster).unwrap();
        let merged = &players[&91];
        assert_eq!(merged.name, "Hong Myung-Bo");
        assert_eq!(merged.print_name, "Hong Myung-Bo");
        assert_eq!(merged.position, Some(Position::Cb));
        assert_eq!((merged.age, merged.overall), (30, 70));
        assert_eq!(
            report.current_entries,
            embedded_player_names().unwrap().len()
        );

        let roster: HashSet<u32> = [4_000_000_000].into();
        let error =
            build_player_catalog(CatalogSources::embedded(), &HashMap::new(), &roster).unwrap_err();
        assert_eq!(
            error.to_string(),
            "Player catalog misses 1 roster IDs (first: 4000000000)"
        );
    }

    #[test]
    fn legacy_name_matching_accepts_suffix_not_prefix() {
        assert!(legacy_name_matches_native("Marco Silva", "M. Silva", ""));
        assert!(legacy_name_matches_native("Jörg Schmidt", "", "JSCHMIDT"));
        assert!(!legacy_name_matches_native(
            "Marco Silva",
            "Pedro Costa",
            "P COSTA"
        ));
        assert!(!legacy_name_matches_native("Al", "Al", "Al"));
        assert!(legacy_name_matches_native(
            "Søren Østergaard",
            "Soren Ostergaard",
            ""
        ));
    }
}
