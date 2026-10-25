//! Best-effort Soccerway corroboration from team transfer feeds.
//! Team search API plus Flashscore's tilde/pilcrow text protocol.

use std::collections::HashSet;
use std::sync::{Arc, LazyLock};
use std::time::Duration;

use chrono::NaiveDate;
use futures_util::StreamExt;
use futures_util::stream::FuturesUnordered;
use regex::Regex;
use serde_json::Value;
use tokio::sync::Semaphore;
use tokio::time::Instant;

use crate::dates::{
    SinceDate, Window, date_in_range, parse_external_date, parse_external_timestamp,
    resolve_date_range,
};
use crate::http::{HttpClient, Request, RetryPolicy};
use crate::model::{Transfer, TransferType, VerificationStatus};
use crate::supplemental::{SourceError, clean, clean_value, is_ascii_digits, url_with_query};
use fl_text::{club_key_narrow as club_key, is_non_club_key, normalize_alnum as normalize};

pub const SOCCERWAY_SEARCH_URL: &str = "https://s.livesport.services/api/v2/search/";
const FEED_URL: &str = "https://global.flashscore.ninja/2020/x/feed/tetr_{team_id}_1_{page}";
const TEAM_TRANSFERS_URL: &str = "https://www.soccerway.com/team/{slug}/{team_id}/transfers/";
/// Public Flashscore feed signature (an undocumented constant of the site).
const FEED_SIGNATURE: &str = "SW9D1eZo";
/// Keep optional corroboration bounded; callers can pass a larger value.
pub const SOCCERWAY_DEFAULT_MAX_PAGES: usize = 1;
const REQUEST_TIMEOUT: Duration = Duration::from_secs(15);
pub const SOCCERWAY_SOURCE_TIMEOUT: Duration = Duration::from_secs(60);
/// In-flight request cap; the shared client's per-host limit must allow it
/// (`host_concurrency` for `s.livesport.services` and `global.flashscore.ninja`).
pub const SOCCERWAY_CONCURRENCY: usize = 12;
const HEADERS: [(&str, &str); 3] = [
    (
        "User-Agent",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    ),
    ("Accept", "application/json, text/plain, */*"),
    ("Referer", "https://www.soccerway.com/"),
];

static NON_SENIOR: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"(?i)\b(?:women|woman|ladies|feminine|femenino|feminino|frauen|academy|youth|reserves?|primavera|u[ -]?\d{2}|ii|b)\b",
    )
    .expect("non senior pattern")
});
static PARTICIPANT: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"/team/[^/]+/([^/]+)/").expect("participant pattern"));

/// A team resolved through the search API.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SoccerwayTeam {
    pub name: String,
    pub slug: String,
    pub team_id: String,
}

#[derive(Debug, Default)]
struct FeedTeam {
    name: String,
    url: String,
}

#[derive(Debug, Default)]
struct FeedRow {
    date_value: String,
    direction: String,
    transfer_type: String,
    fee: String,
    player_name: String,
    teams: Vec<FeedTeam>,
}

/// Which block of a feed record the decoder is inside; `Team` carries the
/// index of the team being filled.
#[derive(Clone, Copy)]
enum Section {
    None,
    Team(usize),
    Player,
}

fn transfer_type(label: &str, fee: &str) -> (TransferType, bool) {
    let normalized = fl_text::casefold(&clean(&format!("{label} {fee}")));
    if normalized.contains("end of loan") || normalized.contains("return") {
        (TransferType::EndOfLoan, false)
    } else if normalized.contains("loan") {
        (TransferType::Loan, true)
    } else if normalized.contains("free") || normalized.contains("released") {
        (TransferType::FreeTransfer, false)
    } else {
        (TransferType::Transfer, false)
    }
}

fn participant_id(url: &str) -> String {
    PARTICIPANT
        .captures(url)
        .map(|found| found[1].to_owned())
        .unwrap_or_default()
}

/// Decode Soccerway's team-transfer feed records.
fn parse_feed_rows(payload: &str) -> Vec<FeedRow> {
    let mut rows: Vec<FeedRow> = Vec::new();
    let mut row: Option<FeedRow> = None;
    let mut section = Section::None;
    let mut pending_property = String::new();

    for raw_token in payload.split('¬') {
        let token = raw_token.trim_start_matches('~');
        let Some((code, value)) = token.split_once('÷') else {
            continue;
        };

        match code {
            "TS" => {
                pending_property.clear();
                if value == "RTT" {
                    rows.extend(row.take());
                    section = Section::None;
                    row = Some(FeedRow::default());
                } else if let (Some(current), "TEA") = (row.as_mut(), value) {
                    current.teams.push(FeedTeam::default());
                    section = Section::Team(current.teams.len() - 1);
                } else if row.is_some() && value == "PLA" {
                    section = Section::Player;
                }
                continue;
            }
            "TE" => {
                pending_property.clear();
                if value == "RTT" {
                    rows.extend(row.take());
                    section = Section::None;
                } else if value == "TEA" || value == "PLA" {
                    section = Section::None;
                }
                continue;
            }
            _ => {}
        }

        let Some(current) = row.as_mut() else {
            continue;
        };
        if code == "PT" {
            pending_property = value.to_owned();
            continue;
        }
        if code != "PV" || pending_property.is_empty() {
            continue;
        }

        match (section, pending_property.as_str()) {
            (Section::Team(index), "VA") => current.teams[index].name = clean(value),
            (Section::Team(index), "TURL") => current.teams[index].url = clean(value),
            (Section::Team(_), _) => {}
            (Section::Player, "VA") => current.player_name = clean(value),
            (Section::Player, _) => {}
            (Section::None, "DATE") => current.date_value = clean(value),
            (Section::None, "TD") => current.direction = fl_text::casefold(&clean(value)),
            (Section::None, "TT") => current.transfer_type = clean(value),
            (Section::None, "TJ") => current.fee = clean(value),
            (Section::None, _) => {}
        }
        pending_property.clear();
    }
    rows.extend(row);
    rows
}

fn row_date(row: &FeedRow) -> Option<NaiveDate> {
    if is_ascii_digits(&row.date_value) {
        // A date-only value is local midnight; noon keeps positive-offset
        // dates on that day after the UTC conversion.
        let seconds: f64 = row.date_value.parse().ok()?;
        return parse_external_timestamp(seconds + 12.0 * 3600.0);
    }
    parse_external_date(&row.date_value)
}

fn row_route(
    row: &FeedRow,
    current: &SoccerwayTeam,
    kind: TransferType,
) -> Option<(String, String)> {
    if row.direction != "in" && row.direction != "out" {
        return None;
    }
    let mut other_team = row
        .teams
        .iter()
        .find(|team| !team.name.is_empty() && participant_id(&team.url) != current.team_id)
        .map(|team| team.name.clone())
        .unwrap_or_default();
    if other_team.is_empty() {
        let current_key = club_key(&current.name);
        other_team = row
            .teams
            .iter()
            .find(|team| !team.name.is_empty() && club_key(&team.name) != current_key)
            .map(|team| team.name.clone())
            .unwrap_or_default();
    }
    if other_team.is_empty() && kind == TransferType::FreeTransfer {
        other_team = "Free Agent".to_owned();
    }
    if other_team.is_empty() {
        return None;
    }
    Some(if row.direction == "in" {
        (other_team, current.name.clone())
    } else {
        (current.name.clone(), other_team)
    })
}

fn transfers_from_rows(
    rows: &[FeedRow],
    current: &SoccerwayTeam,
    source_url: &str,
    start_date: Option<NaiveDate>,
    end_date: Option<NaiveDate>,
) -> Vec<Transfer> {
    let mut transfers = Vec::new();
    let mut seen: HashSet<(String, String, String, String)> = HashSet::new();
    for row in rows {
        let event_date = row_date(row);
        let Some(event_date) =
            event_date.filter(|date| date_in_range(Some(*date), start_date, end_date))
        else {
            continue;
        };
        let player_name = clean(&row.player_name);
        if player_name.is_empty() {
            continue;
        }
        let (kind, is_loan) = transfer_type(&row.transfer_type, &row.fee);
        let Some((from_club, to_club)) = row_route(row, current, kind) else {
            continue;
        };
        if club_key(&from_club) == club_key(&to_club) {
            continue;
        }
        let date = event_date.format("%Y-%m-%d").to_string();
        let key = (
            fl_text::casefold(&player_name),
            fl_text::casefold(&from_club),
            fl_text::casefold(&to_club),
            date.clone(),
        );
        if !seen.insert(key) {
            continue;
        }
        transfers.push(Transfer {
            player_name,
            from_club,
            to_club,
            date,
            transfer_type: kind,
            fee: clean(&row.fee),
            is_loan,
            sources: vec!["soccerway".to_owned()],
            source_urls: vec![source_url.to_owned()],
            verification_status: VerificationStatus::Corroborator,
            ..Transfer::default()
        });
    }
    transfers
}

/// Parse one team feed as corroboration-only routes for `team_name`.
pub fn parse_soccerway_transfer_feed(
    payload: &str,
    team_name: &str,
    team_id: &str,
    source_url: &str,
    start_date: Option<NaiveDate>,
    end_date: Option<NaiveDate>,
) -> Vec<Transfer> {
    let current = SoccerwayTeam {
        name: clean(team_name),
        slug: String::new(),
        team_id: clean(team_id),
    };
    transfers_from_rows(
        &parse_feed_rows(payload),
        &current,
        source_url,
        start_date,
        end_date,
    )
}

fn id_is(value: Option<&Value>, expected: f64) -> bool {
    value.and_then(Value::as_f64) == Some(expected)
}

/// The senior men's team of a search response matching `requested_name`.
pub fn candidate_team(payload: &Value, requested_name: &str) -> Option<SoccerwayTeam> {
    let items = payload.as_array()?;
    let requested_has_category = NON_SENIOR.is_match(requested_name);
    let requested_key = normalize(requested_name);
    let requested_club_key = club_key(requested_name);
    for item in items {
        let Some(item) = item.as_object() else {
            continue;
        };
        let is_team = item
            .get("type")
            .is_some_and(|kind| kind.is_object() && id_is(kind.get("id"), 2.0));
        let is_football = item
            .get("sport")
            .is_some_and(|sport| sport.is_object() && id_is(sport.get("id"), 1.0));
        let is_men = match item.get("gender") {
            Some(gender @ Value::Object(_)) => gender
                .get("id")
                .is_none_or(|id| id.is_null() || id.as_f64() == Some(1.0)),
            _ => true,
        };
        if !is_team || !is_football || !is_men {
            continue;
        }
        let name = item.get("name").map(clean_value).unwrap_or_default();
        let team_id = item.get("id").map(clean_value).unwrap_or_default();
        let slug = item.get("url").map(clean_value).unwrap_or_default();
        if name.is_empty()
            || team_id.is_empty()
            || (!requested_has_category && NON_SENIOR.is_match(&name))
        {
            continue;
        }
        if normalize(&name) == requested_key || club_key(&name) == requested_club_key {
            return Some(SoccerwayTeam {
                name: requested_name.to_owned(),
                slug,
                team_id,
            });
        }
    }
    None
}

/// Distinct usable club names (placeholders and duplicates removed).
fn club_names(names: &[String]) -> Vec<String> {
    let mut seen = HashSet::new();
    names
        .iter()
        .map(|value| clean(value))
        .filter(|name| {
            !name.is_empty() && !is_non_club_key(&normalize(name)) && seen.insert(normalize(name))
        })
        .collect()
}

async fn get_ok_text(client: &HttpClient, request: Request) -> Result<String, SourceError> {
    let response = client
        .execute(&request.timeout(REQUEST_TIMEOUT), &RetryPolicy::NONE)
        .await?;
    if response.status != 200 {
        return Err(SourceError::Unavailable(format!(
            "HTTP {}",
            response.status
        )));
    }
    Ok(response.text())
}

async fn resolve_team(
    client: &HttpClient,
    team_name: &str,
) -> Result<Option<SoccerwayTeam>, SourceError> {
    let url = url_with_query(
        SOCCERWAY_SEARCH_URL,
        &[
            ("q", team_name),
            ("lang-id", "1"),
            ("type-ids", "2"),
            ("project-id", "2020"),
            ("project-type-id", "1"),
            ("sport-ids", "1"),
        ],
    );
    let body = get_ok_text(client, Request::get(url).headers(HEADERS)).await?;
    let payload: Value = serde_json::from_str(&body)
        .map_err(|error| SourceError::Unavailable(format!("invalid search JSON: {error}")))?;
    Ok(candidate_team(&payload, team_name))
}

/// One resolved club's feed: its routes and how many raw feed rows it had.
struct ClubFeed {
    transfers: Vec<Transfer>,
    rows: usize,
}

async fn fetch_team_transfers(
    client: &HttpClient,
    team: &SoccerwayTeam,
    start_date: Option<NaiveDate>,
    end_date: Option<NaiveDate>,
    max_pages: usize,
) -> Result<ClubFeed, SourceError> {
    let source_url = TEAM_TRANSFERS_URL
        .replace("{slug}", &team.slug)
        .replace("{team_id}", &team.team_id);
    let mut feed = ClubFeed {
        transfers: Vec::new(),
        rows: 0,
    };
    for page in 1..=max_pages {
        let url = FEED_URL
            .replace("{team_id}", &team.team_id)
            .replace("{page}", &page.to_string());
        let payload = get_ok_text(
            client,
            Request::get(url)
                .headers(HEADERS)
                .header("X-Fsign", FEED_SIGNATURE),
        )
        .await?;
        let rows = parse_feed_rows(&payload);
        if rows.is_empty() {
            break;
        }
        feed.rows += rows.len();
        feed.transfers.extend(transfers_from_rows(
            &rows,
            team,
            &source_url,
            start_date,
            end_date,
        ));
        let oldest = rows.iter().filter_map(row_date).min();
        if let (Some(start), Some(oldest)) = (start_date, oldest)
            && oldest < start
        {
            break;
        }
    }
    Ok(feed)
}

/// Outcome of one club lookup; `feed` is `None` when the club could not be
/// resolved or fetched.
async fn fetch_club(
    client: &HttpClient,
    gate: &Semaphore,
    team_name: &str,
    start_date: Option<NaiveDate>,
    end_date: Option<NaiveDate>,
    max_pages: usize,
) -> Option<ClubFeed> {
    let outcome = async {
        let team = {
            let _permit = gate.acquire().await.expect("gate is never closed");
            resolve_team(client, team_name).await?
        };
        let Some(team) = team else {
            tracing::debug!(team = team_name, "Soccerway could not resolve team");
            return Ok(None);
        };
        let _permit = gate.acquire().await.expect("gate is never closed");
        fetch_team_transfers(client, &team, start_date, end_date, max_pages)
            .await
            .map(Some)
    };
    match outcome.await {
        Ok(feed) => feed,
        Err::<_, SourceError>(error) => {
            tracing::debug!(team = team_name, %error, "Soccerway team unavailable");
            None
        }
    }
}

/// What to fetch from Soccerway.
#[derive(Debug, Clone)]
pub struct SoccerwayRequest {
    pub since: Option<SinceDate>,
    pub window: Window,
    pub today: NaiveDate,
    /// Clubs to look up (placeholders and duplicates are dropped).
    pub club_names: Vec<String>,
    /// Feed pages per club ([`SOCCERWAY_DEFAULT_MAX_PAGES`] by default).
    pub max_pages: usize,
    /// Budget for the whole source; clubs still pending are skipped.
    pub deadline: Duration,
}

impl SoccerwayRequest {
    /// Defaults: `Auto` window, no `since`, one page per club, 60 s budget.
    pub fn new(today: NaiveDate, club_names: Vec<String>) -> Self {
        Self {
            since: None,
            window: Window::Auto,
            today,
            club_names,
            max_pages: SOCCERWAY_DEFAULT_MAX_PAGES,
            deadline: SOCCERWAY_SOURCE_TIMEOUT,
        }
    }
}

/// Fetch Soccerway routes for the requested clubs. Clubs that cannot be
/// resolved or fetched are skipped; clubs still pending at the deadline are
/// dropped (finished ones are kept, in input order).
pub async fn fetch_soccerway_transfers(
    client: &HttpClient,
    request: &SoccerwayRequest,
) -> Result<Vec<Transfer>, SourceError> {
    let (start_date, end_date) =
        resolve_date_range(request.since.as_ref(), request.window, request.today)?;
    let max_pages = request.max_pages;
    let targets = club_names(&request.club_names);
    tracing::info!(
        clubs = targets.len(),
        max_pages,
        "Soccerway scraping relevant clubs"
    );
    if targets.is_empty() {
        tracing::debug!("Soccerway skipped: no relevant transfer clubs");
        return Ok(Vec::new());
    }

    let gate = Arc::new(Semaphore::new(SOCCERWAY_CONCURRENCY));
    let mut pending: FuturesUnordered<_> = targets
        .iter()
        .enumerate()
        .map(|(index, name)| {
            let gate = Arc::clone(&gate);
            async move {
                (
                    index,
                    fetch_club(client, &gate, name, start_date, end_date, max_pages).await,
                )
            }
        })
        .collect();

    let deadline = Instant::now() + request.deadline;
    let mut feeds: Vec<Option<ClubFeed>> = (0..targets.len()).map(|_| None).collect();
    loop {
        match tokio::time::timeout_at(deadline, pending.next()).await {
            Ok(Some((index, feed))) => feeds[index] = feed,
            Ok(None) => break,
            Err(_) => {
                tracing::warn!(
                    skipped = pending.len(),
                    total = targets.len(),
                    "Soccerway source deadline reached"
                );
                break;
            }
        }
    }

    let resolved = feeds.iter().flatten().count();
    if resolved > 0 && feeds.iter().flatten().all(|feed| feed.rows == 0) {
        // Every resolved club answered with an empty feed: the site changed or
        // the feed signature was rotated.
        tracing::warn!(
            resolved,
            "Soccerway feeds had no rows for any resolved club"
        );
    }
    let mut seen: HashSet<(String, String, String, String)> = HashSet::new();
    let unique: Vec<Transfer> = feeds
        .into_iter()
        .flatten()
        .flat_map(|feed| feed.transfers)
        .filter(|t| {
            seen.insert((
                fl_text::casefold(&t.player_name),
                fl_text::casefold(&t.from_club),
                fl_text::casefold(&t.to_club),
                t.date.clone(),
            ))
        })
        .collect();
    tracing::info!(
        found = unique.len(),
        clubs = targets.len(),
        "Soccerway dated corroboration routes"
    );
    Ok(unique)
}
