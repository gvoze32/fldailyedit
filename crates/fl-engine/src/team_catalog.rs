//! The FotMob team list club identity binds save clubs against.
//!
//! The app bundles no club data: the list is downloaded from FotMob's team
//! sitemaps ([`fl_sources::team_index::crawl`]) and kept in the state
//! directory (`storage/fotmob_teams.json`, the catalog JSON as the crawler
//! renders it). A copy younger than [`MAX_AGE`] is reused as is; an older one
//! is refreshed, and if the refresh fails the old copy still serves (with a
//! warning). Without any copy a failed download fails the run. A dry run
//! reads the cache but never writes it.

use std::path::Path;
use std::sync::Arc;

use chrono::{DateTime, Duration, Utc};
use fl_sources::team_index::{CrawlError, TeamEntry, crawl, parse_catalog, render_catalog};

use crate::api::{CancelToken, ErrorCode, Progress, Stage, UpdateError};
use crate::paths::StatePaths;
use crate::pipeline::Reporter;
use crate::services::Services;

/// A stored team list older than this is downloaded again.
pub(crate) const MAX_AGE: Duration = Duration::days(30);

const STAGE: Stage = Stage::Fetching;
const DOWNLOADING: &str = "Downloading the FotMob club list…";

/// The team list of a run and what went wrong on the way (non-fatal).
pub(crate) struct TeamCatalog {
    pub(crate) teams: Arc<[TeamEntry]>,
    pub(crate) warnings: Vec<String>,
}

/// A stored copy of the list.
struct StoredCopy {
    teams: Vec<TeamEntry>,
    stored_at: DateTime<Utc>,
}

fn read_stored(path: &Path) -> Option<StoredCopy> {
    let stored_at = DateTime::<Utc>::from(std::fs::metadata(path).ok()?.modified().ok()?);
    let teams = parse_catalog(&std::fs::read_to_string(path).ok()?).ok()?;
    (!teams.is_empty()).then_some(StoredCopy { teams, stored_at })
}

fn error(code: ErrorCode, message: impl Into<String>) -> UpdateError {
    UpdateError {
        code,
        stage: Some(STAGE),
        message: message.into(),
    }
}

fn cancelled() -> UpdateError {
    error(ErrorCode::Cancelled, "Update cancelled")
}

/// Download the whole list. Failing to read it is [`ErrorCode::FetchFailed`];
/// cancellation and setup problems keep their own codes.
fn download(
    services: &Services,
    reporter: &mut Reporter<'_>,
    cancel: &CancelToken,
) -> Result<Vec<TeamEntry>, UpdateError> {
    let http = services.http_client(cancel, STAGE)?;
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .map_err(|e| {
            error(
                ErrorCode::Internal,
                format!("Could not start the async runtime: {e}"),
            )
        })?;
    reporter.say(STAGE, DOWNLOADING);
    let crawled = runtime.block_on(crawl(&http, &mut |done, total| {
        if done > 0 {
            reporter.emit(Progress {
                stage: STAGE,
                message: format!("{DOWNLOADING} ({done}/{total})"),
                current: u32::try_from(done).unwrap_or(u32::MAX),
                total: u32::try_from(total).unwrap_or(u32::MAX),
            });
        }
    }));
    match crawled {
        Ok(teams) => Ok(teams),
        Err(CrawlError::Cancelled) => Err(cancelled()),
        Err(CrawlError::Incomplete(message)) => Err(error(ErrorCode::FetchFailed, message)),
    }
}

/// The team list for this run: injected, stored, or downloaded (in that order
/// of preference); see the module docs for the failure policy.
pub(crate) fn load(
    paths: &StatePaths,
    dry_run: bool,
    services: &Services,
    reporter: &mut Reporter<'_>,
    cancel: &CancelToken,
) -> Result<TeamCatalog, UpdateError> {
    if let Some(teams) = &services.team_index {
        return Ok(TeamCatalog {
            teams: Arc::clone(teams),
            warnings: Vec::new(),
        });
    }
    let path = paths.team_index();
    let now = services.clock.now();
    let stored = read_stored(&path);
    if let Some(copy) = &stored
        && now - copy.stored_at < MAX_AGE
    {
        return Ok(TeamCatalog {
            teams: copy.teams.clone().into(),
            warnings: Vec::new(),
        });
    }
    cancel.check(STAGE)?;

    let mut warnings = Vec::new();
    match download(services, reporter, cancel) {
        Ok(teams) => {
            if !dry_run
                && let Err(failure) = fl_fs::write_atomic(&path, render_catalog(&teams).as_bytes())
            {
                warnings.push(format!(
                    "Could not store the FotMob club list in {}: {failure}",
                    path.display()
                ));
            }
            Ok(TeamCatalog {
                teams: teams.into(),
                warnings,
            })
        }
        Err(failure) if failure.code != ErrorCode::FetchFailed => Err(failure),
        Err(failure) => match stored {
            Some(copy) => {
                warnings.push(format!(
                    "Could not refresh the FotMob club list ({}); using the copy from {}",
                    failure.message,
                    copy.stored_at.format("%Y-%m-%d")
                ));
                Ok(TeamCatalog {
                    teams: copy.teams.into(),
                    warnings,
                })
            }
            None => Err(error(
                ErrorCode::FetchFailed,
                format!(
                    "Could not download the FotMob club list ({}). It is needed once to match \
                     your save's clubs with FotMob; check your internet connection and try again.",
                    failure.message
                ),
            )),
        },
    }
}

#[cfg(test)]
mod tests;
