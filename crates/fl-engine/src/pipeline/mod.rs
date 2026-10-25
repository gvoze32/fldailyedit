//! The update pipeline: validate → fetch → match → apply (in memory) →
//! verify → commit.
//!
//! [`run_update`] is the single entry point used by every front end. Each
//! stage observes the [`CancelToken`]; once the commit starts, cancellation is
//! ignored and the save is written atomically. A dry run goes through every
//! stage up to verification and writes nothing: no save, backup, log, report,
//! cache or lock file.

pub(crate) mod apply;
pub(crate) mod commit;
pub(crate) mod context;
pub(crate) mod prepare;
mod records;
pub(crate) mod verify;
pub mod window;

use std::collections::HashSet;

use chrono::NaiveDate;
use fl_sources::dates::parse_iso_datetime;
use fl_sources::model::ScrapeResult;

use self::apply::{Applied, ApplyInput};
use self::commit::{Committed, Reports};
use self::context::Context;
use self::prepare::{Prepared, fail};
use crate::api::{
    CancelToken, ChangeCounts, Environment, ErrorCode, Outcome, Progress, SkippedTransfer, Stage,
    UpdateError, UpdateRequest, UpdateResult,
};
use crate::club_identity::NameResolution;
use crate::log::{LogRecord, TransferLog};
use crate::paths::StatePaths;
use crate::planning::{
    self, HistoryEntry, Plan, PlanInput, SkippedTransfer as PlannedSkip, sort_events,
};
use crate::report::{self, SkippedRow, read_skipped, sort_skipped};
use crate::scrape::{Emit, ScrapePlan, Scraped, scrape};
use crate::services::Services;

/// Rounds of planning and applying one run may take before it stops and asks
/// for another run. A typical run settles in a few; the first run over a stale
/// save, whose live-squad coverage opens club by club, needs more.
const MAX_ROUNDS: usize = 16;

/// Progress sink handed through the stages.
pub(crate) struct Reporter<'a> {
    callback: &'a mut dyn FnMut(Progress),
}

impl<'a> Reporter<'a> {
    pub(crate) fn new(callback: &'a mut dyn FnMut(Progress)) -> Self {
        Self { callback }
    }

    pub(crate) fn emit(&mut self, progress: Progress) {
        (self.callback)(progress);
    }

    pub(crate) fn say(&mut self, stage: Stage, message: impl Into<String>) {
        self.emit(Progress {
            stage,
            message: message.into(),
            current: 0,
            total: 0,
        });
    }
}

/// Run one update with the production clock, network and game archives.
pub fn run_update(
    request: &UpdateRequest,
    env: &Environment,
    progress: &mut dyn FnMut(Progress),
    cancel: &CancelToken,
) -> Result<UpdateResult, UpdateError> {
    run_update_with(request, env, &Services::live(), progress, cancel)
}

/// Run one update with injected services (tests, offline replays).
pub fn run_update_with(
    request: &UpdateRequest,
    env: &Environment,
    services: &Services,
    progress: &mut dyn FnMut(Progress),
    cancel: &CancelToken,
) -> Result<UpdateResult, UpdateError> {
    let mut reporter = Reporter::new(progress);
    let paths = StatePaths::new(&env.state_dir);
    let mut run = Run::start(request, &paths, services, &mut reporter, cancel)?;
    let result = run.finish(request, &paths, services, &mut reporter, cancel);
    if result.is_ok() {
        reporter.say(Stage::Complete, "Update complete");
    }
    result
}

/// One update in flight.
struct Run {
    prepared: Prepared,
    context: Context,
}

fn failure(code: ErrorCode, stage: Stage, message: impl Into<String>) -> UpdateError {
    fail(code, stage, message)
}

impl Run {
    fn start(
        request: &UpdateRequest,
        paths: &StatePaths,
        services: &Services,
        reporter: &mut Reporter<'_>,
        cancel: &CancelToken,
    ) -> Result<Self, UpdateError> {
        reporter.say(Stage::Validating, "Validating the selected save");
        let mut prepared = prepare::prepare(request, reporter, cancel)?;
        cancel.check(Stage::Validating)?;
        let context = context::load(&mut prepared, request, services, paths, reporter, cancel)?;
        cancel.check(Stage::Validating)?;
        Ok(Self { prepared, context })
    }

    fn finish(
        &mut self,
        request: &UpdateRequest,
        paths: &StatePaths,
        services: &Services,
        reporter: &mut Reporter<'_>,
        cancel: &CancelToken,
    ) -> Result<UpdateResult, UpdateError> {
        let mut warnings = std::mem::take(&mut self.context.warnings);

        // Fetch.
        reporter.say(Stage::Fetching, "Fetching transfers");
        let Scraped {
            result: mut scraped,
            warnings: scrape_warnings,
        } = self.fetch(request, paths, services, reporter, cancel)?;
        warnings.extend(scrape_warnings);
        cancel.check(Stage::Fetching)?;
        sort_events(&mut scraped.transfers);

        // Plan and apply, round after round, until nothing moves. Evidence
        // feeds on the roster it describes (a live squad's coverage gate
        // opens once its first registrations landed, a free agent's identity
        // follows his native metadata), so one pass over a save leaves work
        // that the next run would do. Settling here makes a rerun a no-op.
        let mut applied = Applied::default();
        let mut round_history: Vec<HistoryEntry> = Vec::new();
        let mut round = 0;
        let plan = loop {
            round += 1;
            if round > 1 {
                // A fresh run starts without a session, and so does this round:
                // what it finds must be what the next run would find.
                self.prepared.edit.reset_session();
                context::refresh(&mut self.context, &mut self.prepared);
            }
            reporter.say(
                Stage::Matching,
                if round == 1 {
                    "Matching transfers with roster-aware identity verification".to_owned()
                } else {
                    format!("Matching again on the updated rosters (round {round})")
                },
            );
            let plan = self.plan(
                request,
                paths,
                services,
                &scraped,
                &round_history,
                reporter,
                cancel,
            )?;
            cancel.check(Stage::Matching)?;

            reporter.say(Stage::Applying, "Applying verified changes to the save");
            let result = {
                let input = ApplyInput {
                    plan: &plan,
                    scrape: &scraped,
                    matcher: &self.context.matcher,
                    native: &self.context.native,
                    club_ids: &self.context.club_ids,
                    threshold: self.context.threshold,
                    allow_overflow_release: request.allow_overflow_release,
                    dry_run: request.dry_run,
                };
                apply::apply(
                    &mut self.prepared.edit,
                    &input,
                    &self.context.rosters,
                    cancel,
                    reporter,
                )
            };
            let this_round = match result {
                Ok(this_round) => this_round,
                Err(error) => {
                    self.rollback();
                    return Err(error);
                }
            };
            warnings.extend(this_round.warnings.iter().cloned());
            cancel.check(Stage::Applying)?;

            let settled = !this_round.is_effective();
            if !settled {
                // The next round plans against this round's records, exactly as
                // a rerun reads them back from the log.
                let stamped = commit::log_records(
                    &this_round.entries,
                    &self.prepared.scope,
                    services.clock.now(),
                );
                round_history.extend(stamped.iter().map(history_entry));
            }
            applied.absorb(this_round);
            if settled {
                break plan;
            }
            if round == MAX_ROUNDS {
                let warning = format!(
                    "Updates were still arriving after {MAX_ROUNDS} rounds; run the update again to continue"
                );
                reporter.say(Stage::Applying, format!("⚠ {warning}"));
                warnings.push(warning);
                break plan;
            }
        };
        if round > 1 {
            reporter.say(
                Stage::Applying,
                format!("Settled after {round} rounds of changes"),
            );
        }

        let skipped = self.skipped_rows(&plan, &applied);
        let counts = applied.counts;

        if !applied.is_effective() {
            reporter.say(Stage::Applying, "No effective changes to apply");
            return self.unchanged(
                request, paths, services, skipped, counts, warnings, reporter,
            );
        }

        // Verify.
        reporter.say(Stage::Verifying, "Verifying the modified save");
        self.verify(request, cancel)?;
        cancel.check(Stage::Verifying)?;

        let now = services.clock.now();
        if request.dry_run {
            let markdown = report::render_markdown(&applied.entries, &skipped, now);
            return Ok(UpdateResult {
                target_path: request.target_path().to_path_buf(),
                outcome: Outcome::Preview,
                counts,
                report_markdown: markdown,
                skipped: public_skipped(&skipped),
                warnings,
                ..UpdateResult::default()
            });
        }

        // Commit.
        reporter.say(Stage::Committing, "Writing the updated save");
        let Committed {
            backup_path,
            written_sha256,
        } = commit::write_save(&mut self.prepared, paths, services.clock.now(), cancel)
            .inspect_err(|_| self.rollback())?;
        let (reports, publish_warnings) = commit::publish_records(
            paths,
            &self.prepared.scope,
            &applied.entries,
            &skipped,
            services.clock.now(),
        );
        warnings.extend(publish_warnings);
        let (report_markdown, report_paths) = reports
            .map(|Reports { markdown, paths }| (markdown, paths))
            .unwrap_or_default();
        Ok(UpdateResult {
            target_path: request.target_path().to_path_buf(),
            outcome: Outcome::Updated,
            backup_path,
            written_sha256,
            counts,
            report_markdown,
            report_paths,
            skipped: public_skipped(&skipped),
            warnings,
        })
    }

    /// Put the validated input bytes back (nothing was ever written to disk).
    fn rollback(&mut self) {
        self.prepared.edit.restore(self.prepared.baseline.clone());
    }

    fn verify(
        &mut self,
        request: &UpdateRequest,
        _cancel: &CancelToken,
    ) -> Result<(), UpdateError> {
        let (fresh, preserved) =
            verify::new_errors(&self.prepared.edit, &self.context.baseline_errors);
        if !fresh.is_empty() {
            self.rollback();
            return Err(verify::integrity_failure(&fresh, preserved));
        }
        let prepared = &self.prepared;
        verify::check_files_unchanged(
            &prepared.edit_path,
            &prepared.input_digest,
            request.target_path(),
            prepared.same_input_output,
            &prepared.output,
        )
        .inspect_err(|_| self.rollback())
    }

    /// The not-applied report outlives the run: it seeds the next run's window.
    #[allow(clippy::too_many_arguments)]
    fn unchanged(
        &self,
        request: &UpdateRequest,
        paths: &StatePaths,
        services: &Services,
        skipped: Vec<SkippedRow>,
        counts: ChangeCounts,
        mut warnings: Vec<String>,
        reporter: &mut Reporter<'_>,
    ) -> Result<UpdateResult, UpdateError> {
        let now = services.clock.now();
        let (report_markdown, report_paths) = if request.dry_run {
            (report::render_markdown(&[], &skipped, now), Vec::new())
        } else {
            match commit::write_reports(paths, &self.prepared.scope, &[], &skipped, now) {
                Ok(Reports { markdown, paths }) => (markdown, paths),
                Err(error) => {
                    let warning = format!("Could not write the not-applied report: {error}");
                    reporter.say(Stage::Complete, format!("⚠ {warning}"));
                    warnings.push(warning);
                    (report::render_markdown(&[], &skipped, now), Vec::new())
                }
            }
        };
        Ok(UpdateResult {
            target_path: request.target_path().to_path_buf(),
            outcome: Outcome::NoChanges,
            counts,
            report_markdown,
            report_paths,
            skipped: public_skipped(&skipped),
            warnings,
            ..UpdateResult::default()
        })
    }

    fn skipped_rows(&self, plan: &Plan, applied: &Applied) -> Vec<SkippedRow> {
        let mut rows: Vec<SkippedRow> = plan
            .skipped
            .iter()
            .chain(&applied.refused)
            .map(|item| skipped_row(item, &self.prepared.scope))
            .collect();
        sort_skipped(&mut rows);
        rows
    }

    /// Previous run's window inputs → scope → providers.
    fn fetch(
        &mut self,
        request: &UpdateRequest,
        paths: &StatePaths,
        services: &Services,
        reporter: &mut Reporter<'_>,
        cancel: &CancelToken,
    ) -> Result<Scraped, UpdateError> {
        let stage = Stage::Fetching;
        let now = services.clock.now();
        let today = now.date_naive();

        let club_filter = request
            .club
            .as_deref()
            .map(|text| self.resolve_club_filter(text))
            .transpose()?;
        let save_since = self
            .prepared
            .same_input_output
            .then(|| self.save_since_date(paths, today));
        let scope =
            window::resolve_scope(&request.window, club_filter.is_some(), save_since, today);

        let mut bound: Vec<i64> = self
            .context
            .identity
            .entries()
            .iter()
            .map(|entry| entry.fotmob_id)
            .collect();
        bound.sort_unstable();
        bound.dedup();
        if club_filter.is_none() && bound.is_empty() {
            return Err(failure(
                ErrorCode::FetchFailed,
                stage,
                "No save club is bound to a FotMob club; there is nothing to scrape",
            ));
        }

        // A dry run keeps its caches out of the state directory.
        let scratch = if request.dry_run {
            Some(tempfile::tempdir().map_err(|error| {
                failure(
                    ErrorCode::Internal,
                    stage,
                    format!("Could not create a scratch directory: {error}"),
                )
            })?)
        } else {
            None
        };
        let (team_cache_dir, tactics_cache) = match &scratch {
            Some(dir) => (
                dir.path().join("fotmob_team_cache"),
                dir.path().join("fotmob_tactics_cache.json"),
            ),
            None => (paths.team_cache_dir(), paths.tactics_cache()),
        };
        let plan = ScrapePlan {
            scope,
            club_filter,
            bound_club_ids: bound,
            now,
            team_cache_dir: Some(team_cache_dir),
            tactics_cache: Some(tactics_cache),
        };
        run_scrape(&plan, services, reporter, cancel)
    }

    /// `--club` values: FotMob ids or names of save clubs.
    fn resolve_club_filter(&self, text: &str) -> Result<Vec<i64>, UpdateError> {
        let mut ids: Vec<i64> = Vec::new();
        let mut unresolved: Vec<&str> = Vec::new();
        for club in text
            .split(',')
            .map(str::trim)
            .filter(|club| !club.is_empty())
        {
            if !club.is_empty() && club.chars().all(|c| c.is_ascii_digit()) {
                match club.parse::<i64>() {
                    Ok(id) => ids.push(id),
                    Err(_) => unresolved.push(club),
                }
                continue;
            }
            let fotmob = match self.context.identity.resolve_name(club) {
                NameResolution::Team(team) => self.context.identity.fotmob_for_pes(team),
                _ => None,
            };
            match fotmob {
                Some(id) => ids.push(id),
                None => unresolved.push(club),
            }
        }
        if !unresolved.is_empty() {
            return Err(failure(
                ErrorCode::FetchFailed,
                Stage::Fetching,
                format!(
                    "Could not resolve requested clubs to FotMob IDs for this save: {} (use the FotMob team ID instead)",
                    unresolved.join(", ")
                ),
            ));
        }
        let mut seen = HashSet::new();
        ids.retain(|id| seen.insert(*id));
        Ok(ids)
    }

    /// The save's own history decides where an automatic scrape starts.
    fn save_since_date(&self, paths: &StatePaths, today: NaiveDate) -> NaiveDate {
        let scope = &self.prepared.scope;
        let applied: Vec<_> = TransferLog::new(paths.transfer_log())
            .read(Some(scope), false)
            .iter()
            .filter(|record| !record.dry_run)
            .filter_map(|record| parse_iso_datetime(&record.timestamp))
            .collect();
        let skipped_path = paths
            .reports_dir(scope)
            .join(report::SKIPPED_TRANSFERS_FILENAME);
        let pending: Vec<NaiveDate> = read_skipped(&skipped_path)
            .iter()
            .filter(|row| row.save_scope == *scope && row.relevant && row.retryable)
            .filter_map(|row| parse_iso_datetime(&row.date))
            .map(|date| date.date_naive())
            .collect();
        window::save_since_date(&applied, &pending, today)
    }

    /// Learn club identities from live squads, then match and plan.
    #[allow(clippy::too_many_arguments)]
    fn plan(
        &mut self,
        request: &UpdateRequest,
        paths: &StatePaths,
        services: &Services,
        scraped: &ScrapeResult,
        round_history: &[HistoryEntry],
        reporter: &mut Reporter<'_>,
        cancel: &CancelToken,
    ) -> Result<Plan, UpdateError> {
        let stage = Stage::Matching;
        cancel.check(stage)?;
        let context = &mut self.context;

        let evidence = context
            .identity
            .prepare_rosters(&context.rosters, &context.player_names);
        let learned = scraped
            .squad_snapshots
            .iter()
            .filter(|snapshot| snapshot.complete)
            .filter(|snapshot| {
                context
                    .identity
                    .learn_from_snapshot(snapshot, &evidence)
                    .is_some()
            })
            .count();
        if learned > 0 {
            reporter.say(
                stage,
                format!("Club identities confirmed from live squads: {learned}"),
            );
        }
        if !request.dry_run
            && let Err(error) = context.identity.save()
        {
            reporter.say(
                stage,
                format!("⚠ Could not cache learned club identities: {error}"),
            );
        }

        let history: Vec<HistoryEntry> = TransferLog::new(paths.transfer_log())
            .read(Some(&self.prepared.scope), false)
            .iter()
            .map(history_entry)
            .chain(round_history.iter().cloned())
            .collect();
        let rosters = self.prepared.edit.rosters();
        let plan = planning::plan_transfers(&PlanInput {
            scrape: scraped,
            matcher: &context.matcher,
            club_identity: Some(&context.identity),
            threshold: context.threshold,
            rosters: &rosters,
            club_ids: &context.club_ids,
            history: &history,
            player_names: &context.player_names,
            overflow: &self.prepared.edit,
            allow_overflow_release: request.allow_overflow_release,
            today: services.clock.today(),
        });
        let counts = plan.counts;
        reporter.say(
            stage,
            format!(
                "Fully actionable: {} (club transfers: {}, departures: {}, signings: {}, shirt-number checks: {}); unmatched: {}",
                counts.fully_actionable,
                counts.club_transfers,
                counts.departures,
                counts.signings,
                counts.shirt_checks,
                counts.unmatched
            ),
        );
        let relevant = plan.skipped.iter().filter(|item| item.relevant).count();
        reporter.say(
            stage,
            format!(
                "Not applied: {} transfers ({relevant} touch this save)",
                plan.skipped.len()
            ),
        );
        Ok(plan)
    }
}

fn history_entry(record: &LogRecord) -> HistoryEntry {
    let positive = |id: u32| (id > 0).then_some(id);
    HistoryEntry {
        player_id: positive(record.player_id),
        from_team_id: positive(record.from_team_id),
        to_team_id: positive(record.to_team_id),
        fotmob_player_id: record.fotmob_player_id.filter(|&id| id > 0),
        player_name: record.player_name.clone(),
        transfer_type: record.transfer_type.clone(),
        transfer_date: record.transfer_date.clone(),
        timestamp: record.timestamp.clone(),
    }
}

fn skipped_row(item: &PlannedSkip, scope: &str) -> SkippedRow {
    SkippedRow {
        player_name: item.player_name.clone(),
        from_team: item.from_team.clone(),
        to_team: item.to_team.clone(),
        date: item.date.clone(),
        source: item.source.clone(),
        reason: item.reason.clone(),
        detail: item.detail.clone(),
        relevant: item.relevant,
        fotmob_player_id: item.fotmob_player_id,
        candidates: item.candidates.clone(),
        retryable: item.retryable,
        save_scope: scope.to_owned(),
    }
}

fn public_skipped(rows: &[SkippedRow]) -> Vec<SkippedTransfer> {
    rows.iter()
        .map(|row| SkippedTransfer {
            player: row.player_name.clone(),
            from_club: row.from_team.clone(),
            to_club: row.to_team.clone(),
            date: (!row.date.is_empty()).then(|| row.date.clone()),
            transfer_type: String::new(),
            reason: row.reason.clone(),
            detail: row.detail.clone(),
            relevant: row.relevant,
        })
        .collect()
}

/// Drive the async fetch stage from this synchronous call: progress events
/// are forwarded on the calling thread, and a cancel request aborts in-flight
/// requests and back-off waits.
fn run_scrape(
    plan: &ScrapePlan,
    services: &Services,
    reporter: &mut Reporter<'_>,
    cancel: &CancelToken,
) -> Result<Scraped, UpdateError> {
    let stage = Stage::Fetching;
    let internal = |message: String| failure(ErrorCode::Internal, stage, message);
    let http = services.http_client(cancel, stage)?;
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()
        .map_err(|error| internal(format!("Could not start the async runtime: {error}")))?;

    runtime.block_on(async {
        let (sender, mut receiver) = tokio::sync::mpsc::unbounded_channel();
        let emit = Emit(sender);
        let work = scrape(&http, plan, cancel, &emit);
        tokio::pin!(work);
        let result = loop {
            tokio::select! {
                result = &mut work => break result,
                Some(event) = receiver.recv() => reporter.emit(event),
            }
        };
        while let Ok(event) = receiver.try_recv() {
            reporter.emit(event);
        }
        result
    })
}
