//! Player catalog of one save: the players edited inside the save merged with
//! the native `Player.bin` metadata of the game (see `EditFile::build_catalog`).

use std::collections::{HashMap, HashSet};

use crate::model::PlayerInfo;

/// A built player catalog with its coverage report.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct PlayerCatalog {
    pub players: HashMap<u32, PlayerInfo>,
    pub report: PlayerCatalogReport,
}

/// Coverage summary of one catalog build.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct PlayerCatalogReport {
    pub edited_entries: usize,
    pub roster_entries: usize,
    /// Rostered ids absent from every source, ascending.
    pub missing_roster_ids: Vec<u32>,
    /// Rostered ids with a known position.
    pub positions: usize,
    /// Rostered ids with a known age.
    pub ages: usize,
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

/// The players edited inside the save, with the coverage report of the
/// rostered ids. `EditFile::build_catalog` calls this for the ids Player.bin
/// does not know and merges the native records on top.
pub fn build_player_catalog(
    edited_players: &HashMap<u32, PlayerInfo>,
    roster_ids: &HashSet<u32>,
) -> (HashMap<u32, PlayerInfo>, PlayerCatalogReport) {
    let players = edited_players.clone();
    let mut missing: Vec<u32> = roster_ids
        .iter()
        .copied()
        .filter(|id| !players.contains_key(id))
        .collect();
    missing.sort_unstable();
    let report = PlayerCatalogReport {
        edited_entries: edited_players.len(),
        roster_entries: roster_ids.len(),
        positions: coverage(&players, roster_ids, |p| p.position.is_some()),
        ages: coverage(&players, roster_ids, |p| p.age != 0),
        missing_roster_ids: missing,
    };
    (players, report)
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
    fn edited_players_are_the_catalog_and_missing_ids_are_reported() {
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
        let roster: HashSet<u32> = [1, 9, 77, 5].into();

        let (players, report) = build_player_catalog(&edited, &roster);
        assert_eq!(players[&1].name, "Abbrev");
        assert_eq!(report.edited_entries, 2);
        assert_eq!(report.roster_entries, 4);
        assert_eq!(report.positions, 1);
        assert_eq!(report.ages, 1);
        assert_eq!(report.missing_roster_ids, [5, 77]);
    }
}
