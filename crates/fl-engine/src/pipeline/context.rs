//! Selected-save context: native databases, player catalog, match database,
//! club identity and the integrity baseline. Loaded once, before fetching.

use std::collections::{HashMap, HashSet};
use std::path::Path;
use std::sync::Arc;

use fl_save::model::NativePlayers;
use fl_save::release_policy::load_release_policy;

use super::Reporter;
use super::prepare::{Prepared, fail};
use super::verify::baseline_keys;
use crate::api::{CancelToken, ErrorCode, Stage, UpdateError, UpdateRequest};
use crate::club_identity::{ClubIdentityIndex, IdentityCache};
use crate::match_db::build_match_database;
use crate::matcher::{MATCH_THRESHOLD_PLAYER, NameMatcher};
use crate::native::{NativeData, load_native};
use crate::paths::StatePaths;
use crate::planning::RosterMap;
use crate::services::Services;
use crate::team_catalog;

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

/// The failure for game data that is absent or does not belong to the save.
/// Every save reads its players and clubs from the game's own database files.
fn native_missing(problem: String, game_root: Option<&Path>, warnings: &[String]) -> UpdateError {
    let root_hint = match game_root {
        Some(root) => format!(" Selected game folder: {}.", root.display()),
        None => String::new(),
    };
    let details = if warnings.is_empty() {
        String::new()
    } else {
        format!(" ({})", warnings.join("; "))
    };
    fail(
        ErrorCode::NativeDatabaseMissing,
        Stage::Validating,
        format!(
            "{problem}{details} Choose the game folder that contains the download folder \
             (the one with data_extra.cpk).{root_hint}"
        ),
    )
}

/// (Re)load the save's player catalog: the players edited in the save plus
/// the native metadata of the players currently rostered.
fn load_catalog(prepared: &mut Prepared) -> fl_save::catalog::PlayerCatalogReport {
    prepared.edit.load_player_catalog().report.clone()
}

/// Rebuild everything that depends on who sits in which roster, after a round
/// of changes to the in-memory save: the player catalog (native metadata
/// follows the rosters), the player matcher and the roster tables. This is
/// what a fresh run on the changed save would load; club identities, the
/// integrity baseline and the configuration stay.
pub(crate) fn refresh(context: &mut Context, prepared: &mut Prepared) {
    load_catalog(prepared);
    let native_players: Option<Arc<dyn NativePlayers>> = context
        .native
        .player_bin
        .as_ref()
        .map(|loaded| Arc::clone(&loaded.database) as Arc<dyn NativePlayers>);
    let teams = context.native.team_bin.as_ref().map(|t| &*t.database);
    let database = build_match_database(&prepared.edit, native_players.as_deref(), teams);
    let mut matcher = database.matcher;
    matcher.set_team_aliases(context.identity.aliases());
    context.matcher = matcher;
    context.rosters = database.rosters;
    context.player_names = database.player_names;
}

pub(crate) fn load(
    prepared: &mut Prepared,
    request: &UpdateRequest,
    services: &Services,
    paths: &StatePaths,
    reporter: &mut Reporter<'_>,
    cancel: &CancelToken,
) -> Result<Context, UpdateError> {
    let stage = Stage::Validating;
    reporter.say(stage, "Reading the selected save database");
    let native = load_native(request.game_root.as_deref(), &services.native_files);
    let mut warnings = native.warnings.clone();
    let Some(player_bin) = &native.player_bin else {
        return Err(native_missing(
            "The game's Player.bin was not found.".to_owned(),
            request.game_root.as_deref(),
            &native.warnings,
        ));
    };
    reporter.say(
        stage,
        format!("Loaded Player.bin metadata from {}", player_bin.source),
    );
    if !prepared.edit.is_pes21() && native.team_bin.is_none() {
        return Err(native_missing(
            "The game's Team.bin was not found.".to_owned(),
            request.game_root.as_deref(),
            &native.warnings,
        ));
    }
    let native_players: Arc<dyn NativePlayers> =
        Arc::clone(&player_bin.database) as Arc<dyn NativePlayers>;
    prepared
        .edit
        .attach_native(Some(Arc::clone(&native_players)));

    let policy = match &request.release_policy {
        Some(path) => {
            Some(load_release_policy(path).map_err(|error| matching_failed(error.to_string()))?)
        }
        None => None,
    };
    prepared.edit.attach_release_policy(policy.as_ref());

    let report = load_catalog(prepared);
    if !report.missing_roster_ids.is_empty() {
        let sample: Vec<String> = report
            .missing_roster_ids
            .iter()
            .take(8)
            .map(u32::to_string)
            .collect();
        return Err(native_missing(
            format!(
                "The game files do not match this save: {} players on its rosters are missing from \
                 Player.bin (from {}; first IDs: {}).",
                report.missing_roster_ids.len(),
                player_bin.source,
                sample.join(", ")
            ),
            request.game_root.as_deref(),
            &[],
        ));
    }

    let teams = native.team_bin.as_ref().map(|t| &*t.database);
    let database = build_match_database(&prepared.edit, Some(&*native_players), teams);
    let club_ids: HashSet<u32> = prepared.edit.club_team_ids().iter().copied().collect();

    // Club identity binds save clubs to FotMob ids through FotMob's team list,
    // which is downloaded (or read from the state directory) here, before any
    // squad is fetched.
    let catalog = team_catalog::load(paths, request.dry_run, services, reporter, cancel)?;
    warnings.extend(catalog.warnings);
    let mut matcher = database.matcher;
    let identity = ClubIdentityIndex::build(
        &database.club_names,
        &catalog.teams,
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
