//! Verified dated Transfermarkt events from Jina Reader markdown.
//!
//! The window END date is honoured, and no failure here can abort the run:
//! every error is a [`SourceError`] the caller logs.

use std::collections::HashSet;
use std::sync::LazyLock;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use chrono::NaiveDate;
use regex::Regex;

use crate::dates::{SinceDate, Window, date_in_range, resolve_date_range};
use crate::http::{HttpClient, Request, RetryPolicy};
use crate::model::{Transfer, TransferType, VerificationStatus};
use crate::supplemental::{SourceError, clean, parse_dmy, parse_ymd, reader_url};

pub const TRANSFERMARKT_URL: &str =
    "https://www.transfermarkt.com/transfers/neuestetransfers/statistik";
/// Safety cap on scanned pages when the caller does not set one.
pub const AUTO_PAGE_LIMIT: usize = 250;
const FIRST_PAGE_RETRIES: usize = 2;
const FALLBACK_DOMAIN: &str = "www.transfermarkt.de";
const PRIMARY_DOMAIN: &str = "www.transfermarkt.com";
const REQUEST_TIMEOUT: Duration = Duration::from_secs(30);
const HEADERS: [(&str, &str); 4] = [
    (
        "User-Agent",
        "fldailyedit/0.1 (PES transfer updater; contact via project repository)",
    ),
    ("Accept", "text/markdown, text/plain;q=0.9"),
    ("X-Cache-Tolerance", "300"),
    ("X-Retain-Images", "none"),
];

const HOST: &str = r"(?:www\.)?transfermarkt\.[^/\s)]+";

fn compile(pattern: &str) -> Regex {
    Regex::new(pattern).expect("static transfermarkt pattern")
}

static LINK_RE: LazyLock<Regex> =
    LazyLock::new(|| compile(r#"\[([^\]]+)\]\((https?://[^\s)]+)(?:\s+"([^"]*)")?\)"#));
static PLAYER_RE: LazyLock<Regex> = LazyLock::new(|| {
    compile(&format!(
        r#"\[([^\]]+)\]\((https?://{HOST}/[^\s)]*?/profil/spieler/(\d+))(?:\s+"[^"]*")?\)"#
    ))
});
static CLUB_RE: LazyLock<Regex> = LazyLock::new(|| {
    compile(&format!(
        r#"\[([^\]]+)\]\((https?://{HOST}/[^\s)]*?/startseite/verein/(\d+)[^\s)]*)(?:\s+"([^"]*)")?\)"#
    ))
});
static TRANSFER_RE: LazyLock<Regex> = LazyLock::new(|| {
    compile(&format!(
        r#"\[([^\]]+)\]\((https?://{HOST}/[^\s)]*?/transfer_id/(\d+))(?:\s+"[^"]*")?\)"#
    ))
});
static IMAGE_LINK_RE: LazyLock<Regex> =
    LazyLock::new(|| compile(r"\[!\[[^\]]*\]\([^)]*\)\]\([^)]*\)"));
static IMAGE_RE: LazyLock<Regex> = LazyLock::new(|| compile(r"!\[[^\]]*\]\([^)]*\)"));
static SEPARATOR_RE: LazyLock<Regex> = LazyLock::new(|| compile(r"^:?-{3,}:?$"));
static AMOUNT_RE: LazyLock<Regex> = LazyLock::new(|| compile(r"([\d.,]+)\s*([mk])?"));

fn plain_markdown(value: &str) -> String {
    let value = IMAGE_LINK_RE.replace_all(value, "");
    let value = IMAGE_RE.replace_all(&value, "");
    let value = LINK_RE.replace_all(&value, "$1");
    clean(&value.replace('_', ""))
}

fn table_cells(line: &str) -> Vec<&str> {
    line.trim()
        .trim_matches('|')
        .split('|')
        .map(str::trim)
        .collect()
}

/// Folded header text reduced to letters and digits.
fn header_key(value: &str) -> String {
    fl_text::alnum_key(&plain_markdown(value))
}

#[derive(Debug, Clone, Copy)]
struct Columns {
    player: usize,
    left: usize,
    joined: usize,
    date: usize,
    fee: usize,
    age: Option<usize>,
    nationality: Option<usize>,
    market_value: Option<usize>,
}

impl Columns {
    fn max_index(&self) -> usize {
        [
            Some(self.player),
            Some(self.left),
            Some(self.joined),
            Some(self.date),
            Some(self.fee),
        ]
        .into_iter()
        .chain([self.age, self.nationality, self.market_value])
        .flatten()
        .max()
        .unwrap_or(0)
    }
}

fn find_column(keys: &[String], aliases: &[&str]) -> Option<usize> {
    keys.iter().position(|key| aliases.contains(&key.as_str()))
}

fn table_columns(line: &str) -> Option<Columns> {
    if !line.trim_start().starts_with('|') {
        return None;
    }
    let keys: Vec<String> = table_cells(line).into_iter().map(header_key).collect();
    Some(Columns {
        player: find_column(&keys, &["player", "spieler"])?,
        left: find_column(
            &keys,
            &[
                "left",
                "from",
                "fromclub",
                "previousclub",
                "abgebenderverein",
            ],
        )?,
        joined: find_column(
            &keys,
            &["joined", "to", "toclub", "newclub", "aufnehmenderverein"],
        )?,
        date: find_column(&keys, &["transferdate", "transferdatum", "date", "datum"])?,
        fee: find_column(&keys, &["fee", "transferfee", "ablose", "gebuhr"])?,
        age: find_column(&keys, &["age", "alter"]),
        nationality: find_column(&keys, &["nat", "nationality", "nationalitat"]),
        market_value: find_column(&keys, &["marketvalue", "marktwert"]),
    })
}

fn is_table_separator(cells: &[&str]) -> bool {
    !cells.is_empty() && cells.iter().all(|cell| SEPARATOR_RE.is_match(cell))
}

/// `(canonical name, Transfermarkt club id)` of the last club link in a cell.
fn club(cell: &str) -> Option<(String, i64)> {
    let cleaned = IMAGE_LINK_RE.replace_all(cell, "");
    let found = CLUB_RE.captures_iter(&cleaned).last()?;
    let display = plain_markdown(&found[1]);
    let title = found.get(4).map_or("", |m| m.as_str());
    let canonical = clean(if title.is_empty() { &display } else { title });
    Some((canonical, found[3].parse().ok()?))
}

fn transfer_type(fee: &str) -> (TransferType, bool) {
    let normalized = header_key(fee);
    if normalized.contains("endofloan") || normalized.contains("leihende") {
        (TransferType::EndOfLoan, false)
    } else if normalized.contains("loan") || normalized.contains("leih") {
        (TransferType::Loan, true)
    } else if normalized.contains("freetransfer") || normalized.contains("ablosefrei") {
        (TransferType::FreeTransfer, false)
    } else {
        (TransferType::Transfer, false)
    }
}

fn parse_transfer_date(value: &str) -> Option<NaiveDate> {
    let text = plain_markdown(value);
    let text = text.trim();
    parse_dmy(text, '/')
        .or_else(|| parse_dmy(text, '.'))
        .or_else(|| parse_dmy(text, '-'))
        .or_else(|| parse_ymd(text))
}

/// Euro amount of a market-value cell (`€25.00m` -> 25_000_000); 0 if unreadable.
fn parse_euro_amount(value: &str) -> i64 {
    let lowered = plain_markdown(value).to_lowercase();
    let Some(found) = AMOUNT_RE.captures(&lowered) else {
        return 0;
    };
    let raw = &found[1];
    let number = if raw.contains(',') && raw.contains('.') {
        if raw.rfind(',') > raw.rfind('.') {
            raw.replace('.', "").replace(',', ".")
        } else {
            raw.replace(',', "")
        }
    } else {
        raw.replace(',', ".")
    };
    let multiplier = match found.get(2).map(|m| m.as_str()) {
        Some("m") => 1_000_000.0,
        Some("k") => 1_000.0,
        _ => 1.0,
    };
    number
        .parse::<f64>()
        .map_or(0, |amount| (amount * multiplier).round_ties_even() as i64)
}

/// Parse verified dated events from a localized detailed transfer table.
///
/// Rows outside `[start_date, end_date]` (inclusive, either optional) and rows
/// whose links are incomplete are skipped; repeated transfer ids are dropped.
pub fn parse_transfermarkt_markdown(
    markdown: &str,
    source_url: &str,
    start_date: Option<NaiveDate>,
    end_date: Option<NaiveDate>,
) -> Vec<Transfer> {
    let lines = fl_text::split_lines(markdown);
    let Some((header_index, columns)) = lines
        .iter()
        .enumerate()
        .find_map(|(index, line)| table_columns(line).map(|columns| (index, columns)))
    else {
        return Vec::new();
    };

    let mut transfers = Vec::new();
    let mut seen_transfer_ids = HashSet::new();
    let max_column = columns.max_index();
    let mut data_started = false;
    for line in &lines[header_index + 1..] {
        if !line.trim_start().starts_with('|') {
            if data_started {
                break;
            }
            continue;
        }
        let cells = table_cells(line);
        if is_table_separator(&cells) {
            continue;
        }
        data_started = true;
        if cells.len() <= max_column {
            continue;
        }

        let player_cell = cells[columns.player];
        let (Some(player), Some(from), Some(to), Some(event_date), Some(transfer)) = (
            PLAYER_RE.captures(player_cell),
            club(cells[columns.left]),
            club(cells[columns.joined]),
            parse_transfer_date(cells[columns.date]),
            TRANSFER_RE.captures(cells[columns.fee]),
        ) else {
            continue;
        };
        if start_date.is_some_and(|start| event_date < start)
            || end_date.is_some_and(|end| event_date > end)
        {
            continue;
        }
        let Ok(transfer_id) = transfer[3].parse::<i64>() else {
            continue;
        };
        if !seen_transfer_ids.insert(transfer_id) {
            continue;
        }

        let player_name = clean(&player[1]);
        let player_text = plain_markdown(player_cell);
        let position = player_text
            .strip_prefix(player_name.as_str())
            .map(|rest| rest.trim().to_owned())
            .unwrap_or_default();
        let fee = plain_markdown(&transfer[1]);
        let (kind, is_loan) = transfer_type(&fee);
        let age_text = columns
            .age
            .map(|index| plain_markdown(cells[index]))
            .unwrap_or_default();
        let (from_name, from_id) = from;
        let (to_name, to_id) = to;
        transfers.push(Transfer {
            player_name,
            from_club: from_name.clone(),
            to_club: to_name.clone(),
            date: event_date.format("%Y-%m-%d").to_string(),
            transfer_type: kind,
            fee,
            position,
            is_loan,
            market_value: columns
                .market_value
                .map_or(0, |index| parse_euro_amount(cells[index])),
            from_club_full_name: from_name,
            to_club_full_name: to_name,
            nationality: columns
                .nationality
                .map(|index| plain_markdown(cells[index]))
                .unwrap_or_default(),
            age: if crate::supplemental::is_ascii_digits(&age_text) {
                age_text.parse().unwrap_or(0)
            } else {
                0
            },
            sources: vec!["transfermarkt".to_owned()],
            source_urls: vec![source_url.to_owned(), transfer[2].to_owned()],
            player_id_transfermarkt: player[3].parse().ok(),
            from_club_id_transfermarkt: Some(from_id),
            to_club_id_transfermarkt: Some(to_id),
            transfer_id_transfermarkt: Some(transfer_id),
            verification_status: VerificationStatus::Verified,
            ..Transfer::default()
        });
    }
    transfers
}

/// What to fetch: the scan stops at the lower bound, never keeps events past
/// the upper bound.
#[derive(Debug, Clone)]
pub struct TransfermarktRequest {
    /// Explicit lower bound (`--since`); otherwise the window decides.
    pub since: Option<SinceDate>,
    pub window: Window,
    pub today: NaiveDate,
    /// Page cap; `None` means [`AUTO_PAGE_LIMIT`].
    pub max_pages: Option<usize>,
}

fn page_url(page: usize) -> String {
    let mut url =
        format!("{TRANSFERMARKT_URL}?land_id=0&verein_land_id=0&wettbewerb_id=alle&plus=1");
    if page > 1 {
        url.push_str(&format!("&page={page}"));
    }
    url
}

fn fresh_reader_url(source_url: &str) -> String {
    let separator = if source_url.contains('?') { '&' } else { '?' };
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |elapsed| elapsed.as_nanos());
    format!(
        "{}{separator}fldailyedit_refresh={nanos}",
        reader_url(source_url)
    )
}

fn fallback_source_url(source_url: &str) -> Option<String> {
    source_url
        .contains(PRIMARY_DOMAIN)
        .then(|| source_url.replacen(PRIMARY_DOMAIN, FALLBACK_DOMAIN, 1))
}

async fn fetch_text(client: &HttpClient, url: String) -> Result<String, SourceError> {
    let request = Request::get(url).headers(HEADERS).timeout(REQUEST_TIMEOUT);
    let response = client.execute(&request, &RetryPolicy::NONE).await?;
    if response.status != 200 {
        return Err(SourceError::Unavailable(format!(
            "HTTP {}",
            response.status
        )));
    }
    Ok(response.text())
}

/// Result of walking one page through the reader candidates.
enum PageOutcome {
    Rows(Vec<Transfer>),
    /// Some candidate answered, but none had parseable rows.
    Empty,
    /// No candidate answered at all.
    Unavailable(SourceError),
}

async fn fetch_page(client: &HttpClient, page: usize) -> PageOutcome {
    let source_url = page_url(page);
    let attempts = if page == 1 { FIRST_PAGE_RETRIES } else { 1 };
    let mut fetched = false;
    let mut last_error = None;
    for attempt in 0..attempts {
        let mut candidates = vec![
            (source_url.clone(), reader_url(&source_url)),
            (source_url.clone(), fresh_reader_url(&source_url)),
        ];
        if let Some(fallback) = fallback_source_url(&source_url) {
            let reader = fresh_reader_url(&fallback);
            candidates.push((fallback, reader));
        }
        for (candidate_source, candidate_reader) in candidates {
            match fetch_text(client, candidate_reader).await {
                Err(error) => last_error = Some(error),
                Ok(markdown) => {
                    fetched = true;
                    let batch =
                        parse_transfermarkt_markdown(&markdown, &candidate_source, None, None);
                    if !batch.is_empty() {
                        if candidate_source != source_url {
                            tracing::info!(
                                page,
                                "Transfermarkt primary reader empty; using fallback domain"
                            );
                        }
                        return PageOutcome::Rows(batch);
                    }
                }
            }
        }
        if attempt + 1 < attempts {
            tracing::info!(
                page,
                "Transfermarkt page had no parseable rows; refreshing reader response"
            );
        }
    }
    match last_error {
        Some(error) if !fetched => PageOutcome::Unavailable(error),
        _ => PageOutcome::Empty,
    }
}

/// Read pages until the lower-bound boundary, a repeated page, or the page
/// limit, returning verified dated transfers inside the resolved date range.
///
/// Errors only when the very first page cannot be fetched at all; later
/// failures end the scan with what was collected.
pub async fn fetch_transfermarkt_transfers(
    client: &HttpClient,
    request: &TransfermarktRequest,
) -> Result<Vec<Transfer>, SourceError> {
    let page_limit = request.max_pages.unwrap_or(AUTO_PAGE_LIMIT);
    if page_limit == 0 {
        return Ok(Vec::new());
    }
    let (start_date, end_date) =
        resolve_date_range(request.since.as_ref(), request.window, request.today)?;

    let mut transfers: Vec<Transfer> = Vec::new();
    let mut seen_transfer_ids: HashSet<i64> = HashSet::new();
    let mut seen_row_ids: HashSet<i64> = HashSet::new();
    let mut unique_pages = 0usize;
    for page in 1..=page_limit {
        let batch = match fetch_page(client, page).await {
            PageOutcome::Rows(batch) => batch,
            PageOutcome::Unavailable(error) if page == 1 => return Err(error),
            PageOutcome::Unavailable(error) => {
                tracing::warn!(page, unique_pages, found = transfers.len(), %error,
                    "Transfermarkt scan incomplete: page unavailable");
                break;
            }
            PageOutcome::Empty => {
                tracing::warn!(page, "Transfermarkt page returned no parseable dated rows");
                break;
            }
        };

        let batch_ids: HashSet<i64> = batch
            .iter()
            .filter_map(|t| t.transfer_id_transfermarkt)
            .collect();
        if !batch_ids.is_empty() && batch_ids.is_subset(&seen_row_ids) {
            // The reader can ignore the page parameter and serve the same page
            // again; without this the scan walks every page limit.
            tracing::info!(
                page,
                unique_pages,
                "Transfermarkt page repeated an earlier page"
            );
            break;
        }
        seen_row_ids.extend(batch_ids);

        let event_date =
            |transfer: &Transfer| NaiveDate::parse_from_str(&transfer.date, "%Y-%m-%d").ok();
        let eligible: Vec<&Transfer> = batch
            .iter()
            .filter(|transfer| date_in_range(event_date(transfer), start_date, end_date))
            .collect();
        if eligible.is_empty() {
            if start_date.is_some_and(|start| {
                batch
                    .iter()
                    .all(|t| event_date(t).is_some_and(|d| d < start))
            }) {
                break;
            }
            continue;
        }

        let fresh: Vec<&Transfer> = eligible
            .into_iter()
            .filter(|transfer| {
                transfer
                    .transfer_id_transfermarkt
                    .is_none_or(|id| !seen_transfer_ids.contains(&id))
            })
            .collect();
        if fresh.is_empty() {
            tracing::info!(
                page,
                unique_pages,
                "Transfermarkt page repeated prior transfer IDs; latest-feed boundary reached"
            );
            break;
        }
        seen_transfer_ids.extend(fresh.iter().filter_map(|t| t.transfer_id_transfermarkt));
        transfers.extend(fresh.into_iter().cloned());
        unique_pages += 1;
    }
    tracing::info!(
        found = transfers.len(),
        unique_pages,
        "Transfermarkt verified dated transfers"
    );
    Ok(transfers)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn euro_amounts_round_half_to_even_and_accept_both_separators() {
        assert_eq!(parse_euro_amount("€18.00m"), 18_000_000);
        assert_eq!(parse_euro_amount("€1,20m"), 1_200_000);
        assert_eq!(parse_euro_amount("€600k"), 600_000);
        // Rounding is half-to-even: halves go to the even integer.
        assert_eq!(parse_euro_amount("1.234,5"), 1_234);
        assert_eq!(parse_euro_amount("1.235,5"), 1_236);
        assert_eq!(parse_euro_amount("1,234.5"), 1_234);
        assert_eq!(parse_euro_amount("-"), 0);
        assert_eq!(parse_euro_amount(""), 0);
        assert_eq!(parse_euro_amount("..."), 0);
    }

    #[test]
    fn header_matching_is_language_and_markup_tolerant() {
        let columns = table_columns(
            "| Spieler | Alter | Abgebender Verein | Aufnehmender Verein | Transferdatum | Ablöse |",
        );
        assert!(columns.is_some());
        assert!(table_columns("| Player | Age |").is_none());
        assert!(
            table_columns("Player | Left | Joined | Date | Fee").is_none(),
            "needs a leading pipe"
        );
    }

    #[test]
    fn row_dates_accept_all_reader_formats() {
        let expected = NaiveDate::from_ymd_opt(2026, 8, 3);
        for text in [
            "03/08/2026",
            "3.8.2026",
            "03-08-2026",
            "2026-08-03",
            "[03/08/2026](https://x)",
        ] {
            assert_eq!(parse_transfer_date(text), expected, "{text}");
        }
        assert_eq!(parse_transfer_date("31/02/2026"), None);
        assert_eq!(parse_transfer_date("soon"), None);
    }
}
