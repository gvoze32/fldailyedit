//! Native game databases (`Player.bin`, `Team.bin`, `PlayerAssignment.bin`) for one run.
//!
//! Everything is resolved from explicit inputs (the request's game root plus
//! optional file overrides); there is no process-wide configuration.

use std::path::{Path, PathBuf};
use std::sync::Arc;

use fl_save::native::{
    DatabaseSource, LoadOutcome, NativeDatabase, NativeLocations, PlayerAssignmentDatabase,
    PlayerBinDatabase, TeamBinDatabase, load_database,
};
use serde_json::{Map, Value, json};

/// Files that beat the game archives (tests, power users).
#[derive(Debug, Clone, Default)]
pub struct NativeFiles {
    pub player_bin: Option<PathBuf>,
    pub team_bin: Option<PathBuf>,
    pub player_assignment: Option<PathBuf>,
}

/// One loaded database with its origin.
#[derive(Debug)]
pub struct Native<D> {
    pub database: Arc<D>,
    pub source: DatabaseSource,
}

/// The databases found for a run, plus non-fatal load warnings.
#[derive(Debug, Default)]
pub struct NativeData {
    pub player_bin: Option<Native<PlayerBinDatabase>>,
    pub team_bin: Option<Native<TeamBinDatabase>>,
    pub player_assignment: Option<Native<PlayerAssignmentDatabase>>,
    pub warnings: Vec<String>,
}

fn load<D: NativeDatabase>(
    game_root: Option<&Path>,
    explicit: Option<&Path>,
    warnings: &mut Vec<String>,
) -> Option<Native<D>> {
    let LoadOutcome {
        loaded,
        warnings: found,
    } = load_database::<D>(&NativeLocations {
        explicit: explicit.map(Path::to_path_buf),
        configured: None,
        game_root: game_root.map(Path::to_path_buf),
    });
    warnings.extend(found);
    loaded.map(|loaded| Native {
        database: Arc::new(loaded.database),
        source: loaded.source,
    })
}

/// Load the three databases for a run.
pub fn load_native(game_root: Option<&Path>, files: &NativeFiles) -> NativeData {
    let mut warnings = Vec::new();
    let player_bin = load(game_root, files.player_bin.as_deref(), &mut warnings);
    let team_bin = load(game_root, files.team_bin.as_deref(), &mut warnings);
    let player_assignment = load(game_root, files.player_assignment.as_deref(), &mut warnings);
    NativeData {
        player_bin,
        team_bin,
        player_assignment,
        warnings,
    }
}

impl NativeData {
    /// Read-only native metadata attached to a transfer's audit record
    /// (`native_metadata` in the transfer log).
    pub fn transfer_metadata(&self, player_id: u32) -> Value {
        let mut metadata = Map::new();
        if let Some(native) = &self.player_bin {
            let record = native.database.get(player_id);
            let mut payload = Map::new();
            payload.insert("source".into(), json!(native.source.to_string()));
            payload.insert("found".into(), json!(record.is_some()));
            if let Some(record) = record {
                let position = record.position().map_or_else(
                    || format!("UNKNOWN({})", record.position_index),
                    |position| position.name().to_owned(),
                );
                payload.insert("player_id".into(), json!(record.player_id));
                payload.insert("name".into(), json!(record.name));
                payload.insert("print_name".into(), json!(record.print_name));
                payload.insert("age".into(), json!(record.age));
                payload.insert("registered_position".into(), json!(position));
                payload.insert("market_value_eur".into(), json!(record.market_value_eur));
                payload.insert("contract_until".into(), json!(record.contract_until));
                payload.insert("loan_until".into(), json!(record.loan_until));
                payload.insert("is_on_loan".into(), json!(record.is_on_loan()));
                payload.insert("owner_team_key".into(), json!(record.owner_team_key));
                payload.insert("youth_team_id".into(), json!(record.youth_team_id));
                payload.insert("caps".into(), json!(record.caps));
            }
            metadata.insert("player_bin".into(), Value::Object(payload));
        }
        if let Some(assignment) = &self.player_assignment {
            let team_keys = assignment.database.team_keys_for(player_id);
            let mut payload = Map::new();
            payload.insert("source".into(), json!(assignment.source.to_string()));
            payload.insert("team_keys".into(), json!(team_keys));
            if let Some(teams) = &self.team_bin {
                let teams: Vec<Value> = team_keys
                    .iter()
                    .filter_map(|key| teams.database.get(*key))
                    .map(|team| {
                        json!({
                            "team_key": team.team_key,
                            "name": team.name,
                            "abbreviation": team.abbreviation,
                        })
                    })
                    .collect();
                payload.insert("teams".into(), Value::Array(teams));
            }
            metadata.insert("player_assignment".into(), Value::Object(payload));
        }
        Value::Object(metadata)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn nothing_found_yields_empty_metadata() {
        let data = load_native(None, &NativeFiles::default());
        assert!(data.player_bin.is_none() && data.team_bin.is_none());
        assert_eq!(data.transfer_metadata(1), json!({}));
    }

    #[test]
    fn invalid_override_is_reported_not_fatal() {
        let dir = tempfile::tempdir().unwrap();
        let bad = dir.path().join("Player.bin");
        std::fs::write(&bad, b"not a player bin").unwrap();
        let data = load_native(
            None,
            &NativeFiles {
                player_bin: Some(bad),
                ..NativeFiles::default()
            },
        );
        assert!(data.player_bin.is_none());
        assert_eq!(data.warnings.len(), 1);
    }
}
