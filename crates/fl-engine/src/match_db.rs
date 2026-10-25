//! The match database of one validated save: player and club name matcher plus
//! the club and roster tables the planning stages need.

use std::collections::{BTreeMap, HashMap};

use fl_save::edit_file::EditFile;
use fl_save::model::NativePlayers;
use fl_save::native::TeamBinDatabase;

use crate::matcher::{NameMatcher, PlayerAttrs};

/// Everything name matching needs from one save.
pub struct MatchDatabase {
    /// Player and club matcher. Provider team aliases are not loaded yet.
    pub matcher: NameMatcher,
    /// Playable clubs by team id: the input of the club identity index.
    pub club_names: BTreeMap<u32, String>,
    /// Roster player ids of every team.
    pub rosters: HashMap<u32, Vec<u32>>,
    /// Display name of every catalog player (one per id, for roster evidence).
    pub player_names: HashMap<u32, String>,
}

/// Display names of the save's playable clubs.
///
/// Football Life saves abbreviate their own club names, so the game's
/// `Team.bin` supplies them (a club it lacks keeps the save's name). PES 2021
/// saves use the save's names.
pub fn save_club_names(edit: &EditFile, teams: Option<&TeamBinDatabase>) -> BTreeMap<u32, String> {
    let native_names = if edit.is_pes21() { None } else { teams };
    let clubs = edit.club_team_ids();
    edit.teams()
        .into_iter()
        .filter(|team| clubs.contains(&team.team_id))
        .map(|team| {
            let name = native_names
                .and_then(|teams| teams.get(team.team_id))
                .map_or(team.name, |record| record.name.clone());
            (team.team_id, name)
        })
        .collect()
}

/// Build the match database of `edit`, whose player catalog must be loaded
/// (with the native Player.bin attached before that).
///
/// `native` is the same Player.bin source attached to `edit`; it supplies the
/// ages of players the catalog does not merge.
/// `teams` is the game's Team.bin (club names of Football Life saves).
pub fn build_match_database(
    edit: &EditFile,
    native: Option<&dyn NativePlayers>,
    teams: Option<&TeamBinDatabase>,
) -> MatchDatabase {
    let club_names = save_club_names(edit, teams);
    let rosters: HashMap<u32, Vec<u32>> = edit
        .rosters()
        .into_iter()
        .map(|team| (team.team_id, team.roster().collect()))
        .collect();

    let mut catalog: Vec<_> = edit
        .player_catalog()
        .map(|c| c.players.values().collect())
        .unwrap_or_default();
    catalog.sort_unstable_by_key(|player| player.player_id);
    let records: Vec<(String, u32)> = catalog
        .iter()
        .map(|p| (p.name.clone(), p.player_id))
        .collect();
    let player_names = catalog
        .iter()
        .map(|p| (p.player_id, p.name.clone()))
        .collect();

    // Age comes from Player.bin whenever it has the player, rostered or not.
    // The catalog only merges native metadata for rostered players, so a free
    // agent would read as "unknown age" (compatible with any provider age)
    // until a run registers him, and then turn incompatible: two namesakes
    // would trade places on every rerun. The registered position stays the
    // catalog's: provider position labels are loose, and widening that check
    // to every free agent would reject exact-name matches whose label differs.
    let attrs: HashMap<u32, PlayerAttrs> = catalog
        .iter()
        .filter_map(|p| {
            let age = native
                .and_then(|native| native.native_player(p.player_id))
                .map(|record| record.age)
                .filter(|&age| age != 0)
                .unwrap_or(p.age);
            (p.position.is_some() || age != 0).then(|| {
                (
                    p.player_id,
                    PlayerAttrs {
                        position: p
                            .position
                            .map(|pos| pos.name().to_owned())
                            .unwrap_or_default(),
                        nationality: String::new(),
                        age,
                    },
                )
            })
        })
        .collect();

    // The team database is keyed by club NAME, so of several clubs with the
    // same name only the last one stays matchable.
    let mut by_name: Vec<(String, u32)> = Vec::new();
    let mut position: HashMap<&str, usize> = HashMap::new();
    for (&id, name) in &club_names {
        match position.get(name.as_str()) {
            Some(&at) => by_name[at].1 = id,
            None => {
                position.insert(name, by_name.len());
                by_name.push((name.clone(), id));
            }
        }
    }
    MatchDatabase {
        matcher: NameMatcher::new(records, attrs, by_name),
        club_names,
        rosters,
        player_names,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use fl_save::crypto;
    use fl_save::model::{NativePlayer, Position};
    use std::collections::HashSet;
    use std::sync::Arc;

    /// Player.bin double that lists one free agent.
    struct OneRecord {
        player_id: u32,
        age: u32,
    }

    impl NativePlayers for OneRecord {
        fn native_player(&self, player_id: u32) -> Option<NativePlayer<'_>> {
            (player_id == self.player_id).then_some(NativePlayer {
                player_id,
                name: "Free Agent",
                print_name: "",
                position: Some(Position::Gk),
                age: self.age,
                abilities: None,
            })
        }

        fn native_ids(&self) -> Vec<u32> {
            vec![self.player_id]
        }
    }

    fn base_save() -> Option<EditFile> {
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../base/EDIT00000000");
        let raw = std::fs::read(path).ok()?;
        let blocks = crypto::decrypt(&raw).expect("base save decrypts");
        Some(EditFile::from_bytes(blocks.data).expect("base save loads"))
    }

    /// Native Player.bin ages apply to every catalog player, not only the
    /// rostered ones. Otherwise a free agent reads as "unknown age" (compatible
    /// with any provider age) until a run signs him, and then turns
    /// incompatible: two namesakes would trade places on every rerun.
    #[test]
    fn native_age_and_position_apply_to_free_agents_too() {
        let Some(mut edit) = base_save() else { return };
        let rostered: HashSet<u32> = edit
            .rosters()
            .iter()
            .flat_map(|team| team.roster())
            .collect();
        let free_agent = rostered.iter().max().unwrap() + 1000;
        let native = Arc::new(OneRecord {
            player_id: free_agent,
            age: 22,
        });
        edit.attach_native(Some(native.clone()));
        let catalog = edit.load_player_catalog();
        assert_eq!(catalog.players[&free_agent].name, "Free Agent");
        assert_eq!(catalog.players[&free_agent].position, None);

        let without = build_match_database(&edit, None, None);
        assert!(
            without.matcher.is_player_age_compatible(free_agent, 40),
            "without native metadata the free agent's age is unknown"
        );

        let with = build_match_database(&edit, Some(&*native), None);
        assert!(with.matcher.is_player_age_compatible(free_agent, 24));
        assert!(!with.matcher.is_player_age_compatible(free_agent, 40));
        assert_eq!(
            with.matcher.player_position(free_agent),
            "",
            "the registered position stays the catalog's"
        );
    }
}
