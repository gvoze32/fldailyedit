//! FotMob: the live transfer feed, per-club team payloads (transfers, squads,
//! shirt numbers, captains, last lineup, formation) and their parsers.
//!
//! Failure policy: any failed *fetch* of the feed or of
//! a requested club aborts the scrape with [`FotmobError::Incomplete`] — a
//! partial snapshot must never be applied. A payload that *parses* oddly never
//! aborts anything: the affected fields are simply missing (one shifted
//! payload never kills every club's gather).

pub(crate) mod cache;
pub(crate) mod parse;
#[cfg(test)]
mod tests;

use std::collections::HashSet;
use std::path::PathBuf;
use std::sync::Arc;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::Duration as StdDuration;

use chrono::{DateTime, Duration, Utc};
use futures_util::future::try_join_all;
use serde_json::Value;
use sha2::{Digest, Sha256};
use tokio::sync::Semaphore;

use self::cache::{CachedFormation, TEAM_CACHE_TTL, TeamPayloadCache};
pub use self::parse::window_disposition;
use self::parse::{
    Disposition, extract_captain, extract_shirt_number_updates, extract_squad_snapshot,
    extract_transfers_from_team_data, formation_from_match_page, latest_team_match_page,
    parse_fotmob_item, payload_team_name, team_api_url,
};
use crate::dates::{DateError, DateRange, SinceDate, Window, resolve_date_range};
use crate::http::{HttpClient, HttpError, Request, Response, RetryPolicy};
use crate::model::{CaptainUpdate, ScrapeResult, SquadSnapshot, Transfer, merge_transfers};

pub const TRANSFERS_URL: &str = "https://www.fotmob.com/transfers";
/// Safety bound on feed pagination (a broken endpoint must not loop forever).
pub const AUTO_PAGE_LIMIT: u32 = 250;

/// Headers FotMob API requests carry.
pub const DEFAULT_HEADERS: [(&str, &str); 3] = [
    (
        "User-Agent",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) \
         Chrome/128.0.0.0 Safari/537.36",
    ),
    ("Accept", "application/json, text/plain, */*"),
    ("Referer", TRANSFERS_URL),
];

/// Headers for rendered match pages (override Accept/Referer of the defaults).
const PAGE_HEADERS: [(&str, &str); 2] = [
    ("Accept", "text/html,application/xhtml+xml"),
    ("Referer", "https://www.fotmob.com/"),
];

/// Feed page URL (`orderBy=lastModified`, EUR fees).
pub fn feed_url(page: u32, popular_only: bool) -> String {
    format!(
        "https://www.fotmob.com/api/data/transfers?orderBy=lastModified&page={page}&minFeeCurrency=EUR&popular={popular_only}"
    )
}

#[derive(Debug, thiserror::Error)]
pub enum FotmobError {
    /// The source failed before a complete snapshot was read; apply nothing.
    #[error("{0}")]
    Incomplete(String),
    #[error("scrape cancelled")]
    Cancelled,
}

impl From<DateError> for FotmobError {
    fn from(error: DateError) -> Self {
        Self::Incomplete(error.to_string())
    }
}

fn map_http(error: HttpError, label: &str) -> FotmobError {
    match error {
        HttpError::Cancelled => FotmobError::Cancelled,
        HttpError::Status { status, .. } => {
            FotmobError::Incomplete(format!("{label} returned HTTP {status}"))
        }
        HttpError::Transport { source, .. } => {
            FotmobError::Incomplete(format!("Error fetching {label}: {source}"))
        }
        HttpError::Json { message, .. } => {
            FotmobError::Incomplete(format!("Error fetching {label}: {message}"))
        }
    }
}

/// Tunables of a [`FotmobScraper`].
#[derive(Debug, Clone)]
pub struct FotmobOptions {
    /// Directory of the per-team payload cache; `None` disables caching.
    pub cache_dir: Option<PathBuf>,
    /// Cached payloads younger than this are reused without a request.
    pub cache_ttl: Duration,
    /// Clubs fetched concurrently.
    pub club_concurrency: usize,
    /// Pause after each club that needed a network request (held while the
    /// club's concurrency slot is taken).
    pub club_cooldown: StdDuration,
    pub retry: RetryPolicy,
}

impl Default for FotmobOptions {
    fn default() -> Self {
        Self {
            cache_dir: None,
            cache_ttl: TEAM_CACHE_TTL,
            club_concurrency: 4,
            club_cooldown: StdDuration::from_millis(500),
            retry: RetryPolicy::FOTMOB,
        }
    }
}

/// Scope of the global transfer-feed scrape.
#[derive(Debug, Clone, Default)]
pub struct FeedRequest {
    /// Page cap; `None` = [`AUTO_PAGE_LIMIT`] (reaching it is an error).
    pub max_pages: Option<u32>,
    pub popular_only: bool,
    pub since: Option<SinceDate>,
    pub window: Window,
}

/// Date scope for club scrapes.
#[derive(Debug, Clone, Default)]
pub struct ClubsRequest {
    pub since: Option<SinceDate>,
    pub window: Window,
}

/// Progress callback: `(message, index, total)`.
pub type ProgressFn<'a> = &'a (dyn Fn(&str, usize, usize) + Send + Sync);

pub struct FotmobScraper {
    http: HttpClient,
    options: FotmobOptions,
}

enum ObjectFetch {
    NotModified,
    Object(Value, Response),
}

struct ClubResult {
    transfers: Vec<Transfer>,
    pending: Vec<Transfer>,
    roster_updates: Vec<Transfer>,
    snapshot: SquadSnapshot,
    captains: Vec<CaptainUpdate>,
}

struct ClubRun<'a> {
    cache: &'a TeamPayloadCache,
    permits: Semaphore,
    range: DateRange,
    now: DateTime<Utc>,
    total: usize,
    /// Clubs whose payload has been processed so far (progress counter).
    done: AtomicUsize,
    progress: Option<ProgressFn<'a>>,
}

impl FotmobScraper {
    pub fn new(http: HttpClient, options: FotmobOptions) -> Self {
        Self { http, options }
    }

    async fn fetch_object(
        &self,
        url: &str,
        label: &str,
        extra_headers: &[(String, String)],
    ) -> Result<ObjectFetch, FotmobError> {
        let request = Request::get(url)
            .headers(DEFAULT_HEADERS)
            .headers(extra_headers.iter().cloned());
        let response = self
            .http
            .execute(&request, &self.options.retry)
            .await
            .map_err(|e| map_http(e, label))?;
        match response.status {
            304 => Ok(ObjectFetch::NotModified),
            200 => {
                let value: Value = response
                    .json()
                    .map_err(|e| FotmobError::Incomplete(format!("Error fetching {label}: {e}")))?;
                if value.is_object() {
                    Ok(ObjectFetch::Object(value, response))
                } else {
                    Err(FotmobError::Incomplete(format!(
                        "{label} returned a non-object JSON payload"
                    )))
                }
            }
            status => Err(FotmobError::Incomplete(format!(
                "{label} returned HTTP {status}"
            ))),
        }
    }

    /// Fetch the global transfer feed, page by page, until it is empty or
    /// repeats. The feed is ordered by modification time, so an old item is
    /// no stop signal.
    pub async fn fetch_transfers(
        &self,
        request: &FeedRequest,
        now: DateTime<Utc>,
    ) -> Result<ScrapeResult, FotmobError> {
        let today = now.date_naive();
        let (start, end) = resolve_date_range(request.since.as_ref(), request.window, today)?;
        if let Some(start) = start {
            tracing::info!(
                "Scraping FotMob transfers window: {start} to {}",
                end.map_or("latest".to_owned(), |e| e.to_string())
            );
        }
        let page_limit = request.max_pages.unwrap_or(AUTO_PAGE_LIMIT);
        let mut transfers = Vec::new();
        let mut pending = Vec::new();
        let mut seen_pages: HashSet<[u8; 32]> = HashSet::new();
        for page in 1..=page_limit {
            let label = format!("FotMob page {page}");
            let url = feed_url(page, request.popular_only);
            tracing::debug!("Fetching FotMob API page {page}: {url}");
            let ObjectFetch::Object(data, _) = self.fetch_object(&url, &label, &[]).await? else {
                return Err(FotmobError::Incomplete(format!(
                    "{label} returned HTTP 304"
                )));
            };
            let raw_transfers = match data.get("transfers") {
                None => &[][..],
                Some(Value::Array(items)) => items.as_slice(),
                Some(_) => {
                    return Err(FotmobError::Incomplete(format!(
                        "{label} returned an invalid transfers field"
                    )));
                }
            };
            if raw_transfers.is_empty() {
                tracing::info!("FotMob page {page} returned empty transfers list. Stopping.");
                return Ok(ScrapeResult {
                    transfers,
                    pending_transfers: pending,
                    ..ScrapeResult::default()
                });
            }
            let signature: [u8; 32] =
                Sha256::digest(Value::Array(raw_transfers.to_vec()).to_string().as_bytes()).into();
            if !seen_pages.insert(signature) {
                tracing::warn!("FotMob page {page} repeated an earlier page. Stopping pagination.");
                return Ok(ScrapeResult {
                    transfers,
                    pending_transfers: pending,
                    ..ScrapeResult::default()
                });
            }
            tracing::info!("FotMob page {page} returned {} items", raw_transfers.len());

            let (mut eligible, mut parsed, mut malformed) = (0usize, 0usize, 0usize);
            for item in raw_transfers {
                if !item.is_object() {
                    malformed += 1;
                    continue;
                }
                if crate::lenient::is_truthy(item.get("contractExtension").unwrap_or(&Value::Null))
                {
                    continue;
                }
                eligible += 1;
                let Some(transfer) = parse_fotmob_item(item) else {
                    malformed += 1;
                    continue;
                };
                parsed += 1;
                match window_disposition(&transfer, start, end, today) {
                    Disposition::Keep => transfers.push(transfer),
                    Disposition::Pending => pending.push(transfer),
                    Disposition::Drop => {}
                }
            }
            if eligible > 0 && parsed == 0 {
                return Err(FotmobError::Incomplete(format!(
                    "{label} contained {eligible} transfer items but none matched the expected schema"
                )));
            }
            if malformed > 5.max(raw_transfers.len() / 2) {
                return Err(FotmobError::Incomplete(format!(
                    "{label} was mostly malformed ({malformed}/{} items)",
                    raw_transfers.len()
                )));
            }
        }
        Err(FotmobError::Incomplete(format!(
            "FotMob pagination reached safety limit ({page_limit} pages) before an empty or repeated page"
        )))
    }

    /// Team payload through the cache: revalidate with ETag/Last-Modified when
    /// a validator is stored, never accepting a stale payload after a failure.
    async fn fetch_team(
        &self,
        team_id: i64,
        cache: &TeamPayloadCache,
        now: DateTime<Utc>,
    ) -> Result<Arc<Value>, FotmobError> {
        let url = team_api_url(team_id);
        let label = format!("FotMob team API {team_id}");
        let conditional = cache.conditional_headers(team_id);
        let fetched = self.fetch_object(&url, &label, &conditional).await?;
        match fetched {
            ObjectFetch::Object(data, response) => Ok(cache.update(team_id, data, &response, now)),
            ObjectFetch::NotModified if !conditional.is_empty() => {
                cache.touch(team_id, now).ok_or_else(|| {
                    FotmobError::Incomplete(format!(
                        "FotMob team cache {team_id} is not a JSON object"
                    ))
                })
            }
            ObjectFetch::NotModified => Err(FotmobError::Incomplete(format!(
                "FotMob team API {team_id} returned HTTP 304"
            ))),
        }
    }

    /// Read a team's shape from its latest finished match page (optional;
    /// every failure but cancellation just means "no formation").
    pub(crate) async fn fetch_latest_team_formation(
        &self,
        data: &Value,
        team_id: i64,
        team_name: &str,
        cache: &TeamPayloadCache,
    ) -> Result<Option<(String, String)>, FotmobError> {
        let Some((page_url, match_team_name)) = latest_team_match_page(data, team_id) else {
            return Ok(None);
        };
        let details = data.get("details");
        let mut names: Vec<String> = vec![team_name.to_owned()];
        names.extend(match_team_name);
        for key in ["name", "shortName"] {
            names.push(crate::lenient::str_or_empty(
                details.and_then(|d| d.get(key)),
            ));
        }
        let mut visible_names: Vec<String> = Vec::new();
        for name in names.into_iter().filter(|n| !n.is_empty()) {
            if !visible_names.contains(&name) {
                visible_names.push(name);
            }
        }
        match cache.formation(team_id, &page_url, &visible_names) {
            CachedFormation::Known(Some(formation)) => return Ok(Some((formation, page_url))),
            CachedFormation::Known(None) => return Ok(None),
            CachedFormation::Missing => {}
        }
        let request = Request::get(page_url.as_str())
            .headers(DEFAULT_HEADERS)
            .headers(PAGE_HEADERS);
        let html = match self.http.execute(&request, &self.options.retry).await {
            Ok(response) if response.status == 200 => response.text(),
            Ok(response) => {
                tracing::debug!(
                    "Skipping optional FotMob formation page {page_url}: HTTP {}",
                    response.status
                );
                return Ok(None);
            }
            Err(HttpError::Cancelled) => return Err(FotmobError::Cancelled),
            Err(error) => {
                tracing::debug!("Skipping optional FotMob formation page {page_url}: {error}");
                return Ok(None);
            }
        };
        let formation = formation_from_match_page(&html, &visible_names);
        cache.set_formation(team_id, &page_url, &visible_names, formation.clone());
        if formation.is_none() {
            tracing::debug!("No named lineup formation found on FotMob match page {page_url}");
        }
        Ok(formation.map(|f| (f, page_url)))
    }

    async fn fetch_club(&self, run: &ClubRun<'_>, team_id: i64) -> Result<ClubResult, FotmobError> {
        let _slot = run
            .permits
            .acquire()
            .await
            .expect("semaphore is never closed");
        tracing::info!("Fetching FotMob club {team_id}...");
        let cached = run.cache.fresh_payload(team_id, run.now);
        let used_network = cached.is_none();
        let data = match cached {
            Some(data) => data,
            None => {
                self.fetch_team(team_id, run.cache, run.now)
                    .await
                    .map_err(|error| match error {
                        FotmobError::Incomplete(message) => FotmobError::Incomplete(format!(
                            "Club scrape incomplete at FotMob team {team_id}: {message}"
                        )),
                        other => other,
                    })?
            }
        };

        let team_name = payload_team_name(&data, &team_id.to_string());
        if let Some(progress) = run.progress {
            let done = run.done.fetch_add(1, Ordering::Relaxed) + 1;
            progress(&format!("FotMob squads — {team_name}"), done, run.total);
        }
        let today = run.now.date_naive();
        let (transfers, pending) =
            extract_transfers_from_team_data(&data, run.range.0, run.range.1, today);
        let roster_updates = extract_shirt_number_updates(&data, team_id, &team_name);
        let mut snapshot = extract_squad_snapshot(&data, team_id, &team_name);
        let found_formation = if snapshot.complete && snapshot.formation.is_none() {
            self.fetch_latest_team_formation(&data, team_id, &snapshot.club_name, run.cache)
                .await?
        } else {
            None
        };
        if let Some((formation, source_url)) = found_formation {
            snapshot.formation = Some(formation);
            snapshot.formation_source_url = source_url;
        }
        let captains = extract_captain(&data, team_id, &team_name)
            .into_iter()
            .collect();

        // Cooldown while independent clubs progress concurrently; payloads
        // served from the fresh cache made no team API request.
        if used_network {
            self.http
                .pause(self.options.club_cooldown)
                .await
                .map_err(|e| map_http(e, "club cooldown"))?;
        }
        Ok(ClubResult {
            transfers,
            pending,
            roster_updates,
            snapshot,
            captains,
        })
    }

    /// Fetch cached team payloads for FotMob club ids and extract dated
    /// transfers, squads, shirt numbers and captains. Any failed club aborts
    /// the whole scrape. Only complete squads become snapshots.
    pub async fn fetch_clubs(
        &self,
        club_ids: &[i64],
        request: &ClubsRequest,
        now: DateTime<Utc>,
        progress: Option<ProgressFn<'_>>,
    ) -> Result<ScrapeResult, FotmobError> {
        let mut seen = HashSet::new();
        let targets: Vec<i64> = club_ids
            .iter()
            .copied()
            .filter(|id| *id > 0 && seen.insert(*id))
            .collect();
        if targets.is_empty() {
            return Err(FotmobError::Incomplete(
                "No FotMob club IDs requested".to_owned(),
            ));
        }
        let range = resolve_date_range(request.since.as_ref(), request.window, now.date_naive())?;
        let cache =
            TeamPayloadCache::new(self.options.cache_dir.as_deref(), self.options.cache_ttl);
        let run = ClubRun {
            cache: &cache,
            permits: Semaphore::new(self.options.club_concurrency.max(1)),
            range,
            now,
            total: targets.len(),
            done: AtomicUsize::new(0),
            progress,
        };
        let results = try_join_all(
            targets
                .iter()
                .map(|team_id| self.fetch_club(&run, *team_id)),
        )
        .await;
        cache.save();
        let results = results?;

        let mut out = ScrapeResult::default();
        let (mut transfers, mut pending, mut roster) = (Vec::new(), Vec::new(), Vec::new());
        for club in results {
            transfers.extend(club.transfers);
            pending.extend(club.pending);
            roster.extend(club.roster_updates);
            if club.snapshot.complete {
                out.squad_snapshots.push(club.snapshot);
            }
            out.captain_updates.extend(club.captains);
        }
        out.transfers = merge_transfers(transfers);
        out.roster_updates = merge_transfers(roster);
        out.pending_transfers = merge_transfers(pending);
        Ok(out)
    }
}
