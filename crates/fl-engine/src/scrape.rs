//! The fetch stage: one full-coverage scrape.
//!
//! Order: FotMob club pages (squads, transfers, captains) → global FotMob feed
//! → supplemental sources (Wikipedia, Sortitoutsi, Transfermarkt, BeSoccer,
//! Sofascore, Soccerway) → reconciliation → tactical profiles. A failed
//! FotMob fetch aborts the run; supplemental sources are best-effort.
//! Cancellation is observed inside every request and backoff wait (through the
//! HTTP client's token) and between providers.

use std::collections::HashSet;
use std::path::PathBuf;

use chrono::{DateTime, NaiveDate, Utc};
use fl_sources::dates::SinceDate;
use fl_sources::fotmob::{
    ClubsRequest, FeedRequest, FotmobError, FotmobOptions, FotmobScraper, ProgressFn,
};
use fl_sources::http::HttpClient;
use fl_sources::model::{
    ScrapeResult, Transfer, TransferType, VerificationStatus, merge_transfers,
};
use fl_sources::reconcile::reconcile_transfer_sources;
use fl_sources::soccerway::{SoccerwayRequest, fetch_soccerway_transfers};
use fl_sources::supplemental::SourceError;
use fl_sources::tactics::fetch_tactical_updates;
use fl_sources::transfermarkt::{TransfermarktRequest, fetch_transfermarkt_transfers};
use fl_sources::{besoccer, sofascore, sortitoutsi, wikipedia};
use tokio::sync::mpsc::UnboundedSender;

use crate::api::{CancelToken, ErrorCode, Progress, Stage, UpdateError};
use crate::pipeline::window::ScrapeScope;

/// What to fetch.
#[derive(Debug, Clone)]
pub struct ScrapePlan {
    pub scope: ScrapeScope,
    /// A club-focused debug run: only these FotMob clubs, no supplemental sources.
    pub club_filter: Option<Vec<i64>>,
    /// FotMob ids of every save club bound to a FotMob club (full coverage).
    pub bound_club_ids: Vec<i64>,
    pub now: DateTime<Utc>,
    /// FotMob per-team payload cache; `None` disables caching (dry runs).
    pub team_cache_dir: Option<PathBuf>,
    /// FotMob league-tactics cache file; `None` disables caching (dry runs).
    pub tactics_cache: Option<PathBuf>,
}

/// Outcome of the fetch stage; transfers are reconciled but not yet ordered.
#[derive(Debug, Default)]
pub struct Scraped {
    pub result: ScrapeResult,
    /// Non-fatal problems worth showing (unavailable supplemental sources...).
    pub warnings: Vec<String>,
}

/// Channel the scrape reports progress through. The receiving side forwards
/// to the front end on its own thread.
#[derive(Clone)]
pub struct Emit(pub UnboundedSender<Progress>);

impl Emit {
    fn say(&self, message: impl Into<String>) {
        self.counted(message, 0, 0);
    }

    fn counted(&self, message: impl Into<String>, current: usize, total: usize) {
        // A closed receiver means the run already ended; nothing to report to.
        let _ = self.0.send(Progress {
            stage: Stage::Fetching,
            message: message.into(),
            current: u32::try_from(current).unwrap_or(u32::MAX),
            total: u32::try_from(total).unwrap_or(u32::MAX),
        });
    }
}

fn is_non_club_name(name: &str) -> bool {
    let key = fl_text::casefold(name.trim());
    fl_text::NON_CLUB_LABELS.contains(&key.as_str())
}

/// Clubs of the dated events of `batch`, used to filter supplemental routes:
/// the destination, or the source when the destination is not a club.
pub fn supplemental_target_clubs(batch: &[Transfer]) -> Vec<String> {
    let mut targets: Vec<String> = Vec::new();
    let mut seen: HashSet<String> = HashSet::new();
    for transfer in batch.iter().filter(|t| !t.date.is_empty()) {
        let pick =
            |full: &str, short: &str| if full.is_empty() { short } else { full }.trim().to_owned();
        let destination = pick(&transfer.to_club_full_name, &transfer.to_club);
        let source = pick(&transfer.from_club_full_name, &transfer.from_club);
        let target = if is_non_club_name(&destination) {
            source
        } else {
            destination
        };
        let key = fl_text::casefold(&target);
        if !key.is_empty() && !is_non_club_name(&target) && seen.insert(key) {
            targets.push(target);
        }
    }
    targets
}

/// Roster observations never take part in cross-source transfer reconciliation.
fn without_roster_observations(batch: Vec<Transfer>) -> Vec<Transfer> {
    batch
        .into_iter()
        .filter(|t| {
            !matches!(
                t.transfer_type,
                TransferType::SquadRegistration | TransferType::ShirtNumberUpdate
            )
        })
        .collect()
}

fn fotmob_failure(error: FotmobError) -> UpdateError {
    match error {
        FotmobError::Cancelled => cancelled(),
        FotmobError::Incomplete(message) => UpdateError {
            code: ErrorCode::FetchFailed,
            stage: Some(Stage::Fetching),
            message: format!("Scrape incomplete; no save changes were written. {message}"),
        },
    }
}

fn cancelled() -> UpdateError {
    UpdateError {
        code: ErrorCode::Cancelled,
        stage: Some(Stage::Fetching),
        message: "Update cancelled".into(),
    }
}

/// Unwrap a best-effort source: cancellation aborts, anything else becomes a
/// warning and an empty batch.
fn optional<T: Default>(
    name: &str,
    outcome: Result<T, SourceError>,
    warnings: &mut Vec<String>,
    emit: &Emit,
) -> Result<T, UpdateError> {
    match outcome {
        Ok(value) => Ok(value),
        Err(error) if error.is_cancelled() => Err(cancelled()),
        Err(error) => {
            let warning = format!("{name} unavailable, continuing without it: {error}");
            emit.say(format!("⚠ {warning}"));
            warnings.push(warning);
            Ok(T::default())
        }
    }
}

fn since(scope: &ScrapeScope) -> Option<SinceDate> {
    scope.since.map(SinceDate::Date)
}

/// Run the fetch stage.
pub async fn scrape(
    http: &HttpClient,
    plan: &ScrapePlan,
    cancel: &CancelToken,
    emit: &Emit,
) -> Result<Scraped, UpdateError> {
    let today: NaiveDate = plan.now.date_naive();
    let scope = &plan.scope;
    let cutoff = scope.describe(today);
    let mut warnings = Vec::new();
    let check = || cancel.check(Stage::Fetching);

    let fotmob = FotmobScraper::new(
        http.clone(),
        FotmobOptions {
            cache_dir: plan.team_cache_dir.clone(),
            ..FotmobOptions::default()
        },
    );
    let clubs_request = ClubsRequest {
        since: since(scope),
        window: scope.window,
    };

    let mut batches: Vec<Vec<Transfer>> = Vec::new();
    let mut roster_updates: Vec<Transfer> = Vec::new();
    let mut captains = Vec::new();
    let mut snapshots = Vec::new();
    let mut pending: Vec<Transfer> = Vec::new();

    check()?;
    let club_ids: &[i64] = plan.club_filter.as_deref().unwrap_or(&plan.bound_club_ids);
    if plan.club_filter.is_some() {
        emit.say(format!(
            "Fetching FotMob transfers for {} requested clubs ({cutoff})",
            club_ids.len()
        ));
    } else {
        emit.say(format!(
            "Fetching FotMob transfers and squads for {} save clubs ({cutoff})",
            club_ids.len()
        ));
    }
    let progress = |message: &str, index: usize, total: usize| {
        emit.counted(message, index, total);
    };
    let progress: ProgressFn<'_> = &progress;
    let club_batch = fotmob
        .fetch_clubs(club_ids, &clubs_request, plan.now, Some(progress))
        .await
        .map_err(fotmob_failure)?;
    emit.say(format!(
        "FotMob squads: {} squads, {} captain markers",
        club_batch.squad_snapshots.len(),
        club_batch.captain_updates.len()
    ));
    batches.push(without_roster_observations(club_batch.transfers));
    roster_updates.extend(club_batch.roster_updates);
    snapshots.extend(club_batch.squad_snapshots);
    captains.extend(club_batch.captain_updates);
    pending.extend(club_batch.pending_transfers);

    let mut fast_signals: Vec<Transfer> = Vec::new();
    let mut corroborators: Vec<Transfer> = Vec::new();
    if plan.club_filter.is_none() {
        check()?;
        emit.say(format!("Fetching the live FotMob transfer feed ({cutoff})"));
        let feed = fotmob
            .fetch_transfers(
                &FeedRequest {
                    max_pages: None,
                    popular_only: false,
                    since: since(scope),
                    window: scope.window,
                },
                plan.now,
            )
            .await
            .map_err(fotmob_failure)?;
        pending.extend(feed.pending_transfers);
        batches.push(feed.transfers);

        check()?;
        emit.say(format!("Fetching Wikipedia transfer lists ({cutoff})"));
        let wikipedia_transfers = optional(
            "Wikipedia",
            wikipedia::fetch_wikipedia_transfers(http, since(scope).as_ref(), scope.window, today)
                .await,
            &mut warnings,
            emit,
        )?;
        let (dated, undated): (Vec<_>, Vec<_>) = wikipedia_transfers
            .into_iter()
            .partition(|t| !t.date.is_empty());
        let wikipedia_corroborators: Vec<Transfer> = undated
            .into_iter()
            .filter(|t| t.verification_status == VerificationStatus::Corroborator)
            .collect();
        emit.say(format!(
            "Wikipedia: {} dated transfers, {} undated route corroborators",
            dated.len(),
            wikipedia_corroborators.len()
        ));
        batches.push(dated);
        corroborators.extend(wikipedia_corroborators);

        check()?;
        emit.say("Fetching Sortitoutsi fast signals");
        fast_signals = optional(
            "Sortitoutsi",
            sortitoutsi::fetch_sortitoutsi_transfers(http, since(scope).as_ref(), today).await,
            &mut warnings,
            emit,
        )?;
        emit.say(format!(
            "Sortitoutsi: {} enabled signals",
            fast_signals.len()
        ));

        check()?;
        emit.say("Fetching Transfermarkt transfers");
        let transfermarkt = optional(
            "Transfermarkt",
            fetch_transfermarkt_transfers(
                http,
                &TransfermarktRequest {
                    since: since(scope),
                    window: scope.window,
                    today,
                    max_pages: None,
                },
            )
            .await,
            &mut warnings,
            emit,
        )?;
        emit.say(format!(
            "Transfermarkt: {} dated transfers",
            transfermarkt.len()
        ));
        batches.push(transfermarkt);

        let target_clubs = supplemental_target_clubs(&batches[0]);

        check()?;
        emit.say("Fetching BeSoccer corroboration routes");
        let besoccer_routes = optional(
            "BeSoccer",
            besoccer::fetch_besoccer_transfers(http, since(scope).as_ref(), scope.window, today)
                .await,
            &mut warnings,
            emit,
        )?;
        emit.say(format!(
            "BeSoccer: {} corroboration routes",
            besoccer_routes.len()
        ));
        corroborators.extend(besoccer_routes);

        check()?;
        emit.say("Fetching Sofascore corroboration routes");
        let sofascore_routes = optional(
            "Sofascore",
            sofascore::fetch_sofascore_transfers(
                http,
                since(scope).as_ref(),
                scope.window,
                today,
                &target_clubs,
            )
            .await,
            &mut warnings,
            emit,
        )?;
        emit.say(format!(
            "Sofascore: {} corroboration routes",
            sofascore_routes.len()
        ));
        corroborators.extend(sofascore_routes);

        check()?;
        emit.say("Fetching Soccerway corroboration routes");
        let mut soccerway = SoccerwayRequest::new(today, target_clubs);
        soccerway.since = since(scope);
        soccerway.window = scope.window;
        let soccerway_routes = optional(
            "Soccerway",
            fetch_soccerway_transfers(http, &soccerway).await,
            &mut warnings,
            emit,
        )?;
        emit.say(format!(
            "Soccerway: {} corroboration routes",
            soccerway_routes.len()
        ));
        corroborators.extend(soccerway_routes);
    }

    let transfers = if !fast_signals.is_empty() || !corroborators.is_empty() || batches.len() > 1 {
        reconcile_transfer_sources(batches, fast_signals, corroborators)
    } else {
        merge_transfers(batches.into_iter().flatten().collect())
    };
    emit.say(format!("{} unique transfers to process", transfers.len()));
    let roster_updates = merge_transfers(roster_updates);

    let mut tactical_updates = Vec::new();
    if !snapshots.is_empty() {
        check()?;
        emit.say("Preparing evidence-gated tactical settings from FotMob");
        if let Some(cache) = &plan.tactics_cache {
            match fetch_tactical_updates(http, &snapshots, cache, plan.now).await {
                Ok(updates) => tactical_updates = updates,
                Err(FotmobError::Cancelled) => return Err(cancelled()),
                Err(error) => {
                    let warning =
                        format!("Tactical stats unavailable; preserving saved game plans: {error}");
                    emit.say(format!("⚠ {warning}"));
                    warnings.push(warning);
                }
            }
        }
    }

    Ok(Scraped {
        result: ScrapeResult {
            transfers,
            captain_updates: captains,
            squad_snapshots: snapshots,
            roster_updates,
            tactical_updates,
            pending_transfers: pending,
        },
        warnings,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn transfer(date: &str, from: &str, to: &str, to_full: &str) -> Transfer {
        Transfer {
            date: date.into(),
            from_club: from.into(),
            to_club: to.into(),
            to_club_full_name: to_full.into(),
            ..Transfer::default()
        }
    }

    #[test]
    fn target_clubs_prefer_destination_then_source_and_skip_undated() {
        let batch = vec![
            transfer("2026-01-02", "A FC", "B United", "B United FC"),
            transfer("2026-01-03", "C City", "Free agent", ""),
            transfer("", "X", "Y", ""),
            transfer("2026-01-04", "D", "b united fc", ""),
            transfer("2026-01-05", "Retired", "Unattached", ""),
        ];
        assert_eq!(
            supplemental_target_clubs(&batch),
            vec!["B United FC".to_owned(), "C City".to_owned()]
        );
    }

    #[test]
    fn roster_observations_are_removed_from_event_batches() {
        let shirt = Transfer {
            transfer_type: TransferType::ShirtNumberUpdate,
            ..Transfer::default()
        };
        let real = Transfer {
            transfer_type: TransferType::Loan,
            ..Transfer::default()
        };
        let kept = without_roster_observations(vec![shirt, real]);
        assert_eq!(kept.len(), 1);
        assert_eq!(kept[0].transfer_type, TransferType::Loan);
    }
}
