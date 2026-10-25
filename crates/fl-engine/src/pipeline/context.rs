//! Selected-save context: native databases, player catalog, match database,
//! club identity and the integrity baseline. Loaded once, before fetching.

use std::collections::{HashMap, HashSet};
use std::sync::Arc;

use fl_save::catalog::CatalogSources;
use fl_save::model::NativePlayers;
use fl_save::release_policy::load_release_policy;
use fl_sources::team_index::embedded_catalog;

use super::Reporter;
use super::prepare::{Prepared, fail};
use super::verify::baseline_keys;
use crate::api::{ErrorCode, Stage, UpdateError, UpdateRequest};
use crate::club_identity::{ClubIdentityIndex, IdentityCache};
use crate::match_db::build_match_database;
use crate::matcher::{MATCH_THRESHOLD_PLAYER, NameMatcher};
use crate::native::{NativeData, NativeFiles, load_native};
use crate::paths::StatePaths;
use crate::planning::RosterMap;

/// Everything about the selected save that fetching and planning read.
pub(crate) struct Context {
    pub(crate) native: NativeData,
    pub(crate) matcher: NameMatcher,
    pub(crate) identity: ClubIdentityIndex,
    /// Roster player ids per team as saved (before any change).
    pub(crate) rosters: RosterMap,
    pub(crate) club_ids: HashSet<u32>,
    pub(crate) player_names: HashMap<u32, String>,
    /// Integrity findings the input already has (compared structurally later).
    pub(crate) baseline_errors: HashSet<String>,
    pub(crate) threshold: f64,
    pub(crate) warnings: Vec<String>,
}

fn matching_failed(message: String) -> UpdateError {
    fail(
        ErrorCode::MatchingFailed,
        Stage::Matching,
        format!("Transfer matching failed: {message}"),
    )
}

/// The three-line hint appended to the missing-Player.bin failure.
fn missing_native_message(
    missing: &[u32],
    source: Option<String>,
    game_root: Option<&std::path::Path>,
) -> String {
    let sample: Vec<String> = missing.iter().take(8).map(u32::to_string).collect();
    let root_hint = match game_root {
        Some(root) => format!(" Selected game root: {}.", root.display()),
        None => " Choose the PES 2021/T99 game folder containing download/*.cpk.".to_owned(),
    };
    format!(
        "PES 2021/T99 save requires matching native Player.bin metadata; {} roster IDs are missing (source: {}; first IDs: {}).{root_hint}",
        missing.len(),
        source.unwrap_or_else(|| "unavailable".to_owned()),
        sample.join(", ")
    )
}

/// (Re)load the save's player catalog: names plus the native metadata of the
/// players currently rostered.
fn load_catalog(
    prepared: &mut Prepared,
) -> Result<fl_save::catalog::PlayerCatalogReport, UpdateError> {
    let sources = if prepared.edit.is_pes21() {
        // PES 2021/T99 ids come from the matching native Player.bin; the
        // bundled FL26 catalog uses a different id universe.
        CatalogSources::default()
    } else {
        CatalogSources::embedded()
    };
    prepared
        .edit
        .load_player_catalog(sources)
        .map(|catalog| catalog.report.clone())
        .map_err(|error| matching_failed(error.to_string()))
}

/// Rebuild everything that depends on who sits in which roster, after a round
/// of changes to the in-memory save: the player catalog (native metadata
/// follows the rosters), the player matcher and the roster tables. This is
/// what a fresh run on the changed save would load; club identities, the
/// integrity baseline and the configuration stay.
pub(crate) fn refresh(context: &mut Context, prepared: &mut Prepared) -> Result<(), UpdateError> {
    load_catalog(prepared)?;
    let native_players: Option<Arc<dyn NativePlayers>> = context
        .native
        .player_bin
        .as_ref()
        .map(|loaded| Arc::clone(&loaded.database) as Arc<dyn NativePlayers>);
    let database = build_match_database(&prepared.edit, native_players.as_deref())
        .map_err(|error| matching_failed(error.to_string()))?;
    let mut matcher = database.matcher;
    matcher.set_team_aliases(context.identity.aliases());
    context.matcher = matcher;
    context.rosters = database.rosters;
    context.player_names = database.player_names;
    Ok(())
}

pub(crate) fn load(
    prepared: &mut Prepared,
    request: &UpdateRequest,
    native_files: &NativeFiles,
    paths: &StatePaths,
    reporter: &mut Reporter<'_>,
) -> Result<Context, UpdateError> {
    let stage = Stage::Validating;
    reporter.say(stage, "Reading the selected save database");
    let native = load_native(request.game_root.as_deref(), native_files);
    let mut warnings = native.warnings.clone();
    if let Some(player_bin) = &native.player_bin {
        reporter.say(
            stage,
            format!("Loaded Player.bin metadata from {}", player_bin.source),
        );
    }
    let native_players: Option<Arc<dyn NativePlayers>> = native
        .player_bin
        .as_ref()
        .map(|loaded| Arc::clone(&loaded.database) as Arc<dyn NativePlayers>);
    prepared.edit.attach_native(native_players.clone());

    let policy = match &request.release_policy {
        Some(path) => {
            Some(load_release_policy(path).map_err(|error| matching_failed(error.to_string()))?)
        }
        None => None,
    };
    prepared.edit.attach_release_policy(policy.as_ref());

    let report = load_catalog(prepared)?;
    if prepared.edit.is_pes21() && !report.missing_roster_ids.is_empty() {
        return Err(fail(
            ErrorCode::NativeDatabaseMissing,
            Stage::Matching,
            missing_native_message(
                &report.missing_roster_ids,
                native
                    .player_bin
                    .as_ref()
                    .map(|loaded| loaded.source.to_string()),
                request.game_root.as_deref(),
            ),
        ));
    }

    let database = build_match_database(&prepared.edit, native_players.as_deref())
        .map_err(|error| matching_failed(error.to_string()))?;
    if report.current_entries == 0 && !prepared.edit.is_pes21() {
        warnings
            .push("External player catalog unavailable; using names from the selected save".into());
    }
    let club_ids: HashSet<u32> = prepared.edit.club_team_ids().iter().copied().collect();

    let mut matcher = database.matcher;
    let identity = ClubIdentityIndex::build(
        &database.club_names,
        embedded_catalog(),
        IdentityCache::new(paths.club_identity_cache(), prepared.scope.clone()),
    );
    matcher.set_team_aliases(identity.aliases());
    reporter.say(
        stage,
        format!(
            "{} players, {} playable clubs ({} bound to FotMob)",
            database.player_names.len(),
            database.club_names.len(),
            identity.entries().len()
        ),
    );

    // Native metadata can expose semantic issues already present in the
    // save. They are the baseline: a run is rejected only for new problems.
    let baseline_errors = baseline_keys(&prepared.edit.validate_integrity().errors);

    Ok(Context {
        native,
        matcher,
        identity,
        rosters: database.rosters,
        club_ids,
        player_names: database.player_names,
        baseline_errors,
        threshold: request.threshold.unwrap_or(MATCH_THRESHOLD_PLAYER),
        warnings,
    })
}
