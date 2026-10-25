//! The match database of one validated save: player and club name matcher plus
//! the club and roster tables the planning stages need.

use std::collections::{BTreeMap, HashMap, HashSet};

use fl_save::catalog::{
    EMBEDDED_LEGACY_PLAYERS_CSV, PlayerCatalogError, embedded_team_names,
    legacy_name_matches_native, load_legacy_player_names,
};
use fl_save::edit_file::EditFile;
use fl_save::model::NativePlayers;

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
    /// Legacy English names added for native players (PES 2021/T99 saves).
    pub native_aliases: usize,
}

/// Display names of the save's playable clubs.
///
/// FL26 saves use the external team reference (the save's own names are
/// abbreviated); PES 2021/T99 saves, and saves whose player catalog has no
/// current reference, use the save's names.
pub fn save_club_names(edit: &EditFile) -> Result<BTreeMap<u32, String>, PlayerCatalogError> {
    let use_external = !edit.is_pes21()
        && edit
            .player_catalog()
            .is_none_or(|catalog| catalog.report.current_entries > 0);
    let external = if use_external {
        embedded_team_names()?
    } else {
        HashMap::new()
    };
    let clubs = edit.club_team_ids();
    Ok(edit
        .teams()
        .into_iter()
        .filter(|team| clubs.contains(&team.team_id))
        .map(|team| {
            let name = external.get(&team.team_id).cloned().unwrap_or(team.name);
            (team.team_id, name)
        })
        .collect())
}

/// Build the match database of `edit`, whose player catalog must be loaded
/// (and native Player.bin attached before that, when the save needs it).
///
/// `native` is the same Player.bin source attached to `edit`; with it, PES
/// 2021/T99 saves also get verified legacy English names as extra player names.
pub fn build_match_database(
    edit: &EditFile,
    native: Option<&dyn NativePlayers>,
) -> Result<MatchDatabase, PlayerCatalogError> {
    let club_names = save_club_names(edit)?;
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
    let mut records: Vec<(String, u32)> = catalog
        .iter()
        .map(|p| (p.name.clone(), p.player_id))
        .collect();
    let player_names = catalog
        .iter()
        .map(|p| (p.player_id, p.name.clone()))
        .collect();

    // T99 stores localized full names and often surname-only print names.
    // Reuse legacy English names only after native id and display agreement;
    // never let a stale catalog invent a player outside the native roster.
    let mut native_aliases = 0;
    if let (true, Some(native)) = (edit.is_pes21(), native) {
        let roster_ids: HashSet<u32> = rosters.values().flatten().copied().collect();
        let mut legacy: Vec<(u32, String)> =
            load_legacy_player_names(Some(EMBEDDED_LEGACY_PLAYERS_CSV), &roster_ids)?
                .into_iter()
                .collect();
        legacy.sort_unstable_by_key(|(id, _)| *id);
        for (id, name) in legacy {
            let agrees = native.native_player(id).is_some_and(|record| {
                legacy_name_matches_native(&name, record.name, record.print_name)
            });
            if agrees {
                records.push((name, id));
                native_aliases += 1;
            }
        }
    }

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
    Ok(MatchDatabase {
        matcher: NameMatcher::new(records, attrs, by_name),
        club_names,
        rosters,
        player_names,
        native_aliases,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use fl_save::catalog::CatalogSources;
    use fl_save::crypto;
    use fl_save::model::{NativePlayer, Position};

    /// Player.bin double with one record.
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
        edit.load_player_catalog(CatalogSources::embedded())
            .expect("catalog loads");
        let rostered: HashSet<u32> = edit
            .rosters()
            .iter()
            .flat_map(|team| team.roster())
            .collect();
        let free_agent = edit
            .player_catalog()
            .expect("catalog")
            .players
            .keys()
            .copied()
            .filter(|id| !rostered.contains(id))
            .min()
            .expect("the base save has free agents");

        let without = build_match_database(&edit, None).unwrap();
        assert!(
            without.matcher.is_player_age_compatible(free_agent, 40),
            "without native metadata the free agent's age is unknown"
        );

        let native = OneRecord {
            player_id: free_agent,
            age: 22,
        };
        let with = build_match_database(&edit, Some(&native)).unwrap();
        assert!(with.matcher.is_player_age_compatible(free_agent, 24));
        assert!(!with.matcher.is_player_age_compatible(free_agent, 40));
        assert_eq!(
            with.matcher.player_position(free_agent),
            "",
            "the registered position stays the catalog's"
        );
    }
}
