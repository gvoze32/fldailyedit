//! Best-effort BeSoccer corroboration from its Spanish transfer feed (via the
//! Jina reader).
//!
//! The proof-link pattern accepts any BeSoccer subdomain: BeSoccer links
//! players on `es.besoccer.com`, so a pattern limited to `www.` or no
//! subdomain would silently return nothing.

use std::collections::HashSet;
use std::sync::LazyLock;
use std::time::Duration;

use chrono::NaiveDate;
use regex::Regex;

use crate::dates::{SinceDate, Window, date_in_range, resolve_date_range};
use crate::http::{HttpClient, Request, RetryPolicy};
use crate::model::{Transfer, TransferType, VerificationStatus};
use crate::supplemental::{SourceError, clean, reader_url};

pub const BESOCCER_TRANSFERS_URL: &str = "https://www.besoccer.es/fichajes";
const REQUEST_TIMEOUT: Duration = Duration::from_secs(30);
const HEADERS: [(&str, &str); 2] = [
    (
        "User-Agent",
        "fldailyedit/0.1 (PES transfer updater; contact via project repository)",
    ),
    ("Accept", "text/plain, */*;q=0.5"),
];

static DATE_HEADING: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)^(\d{1,2})\s+([A-ZÁÉÍÓÚ]{3})\s+(\d{4})$")
        .expect("besoccer date heading pattern")
});
static IMAGE_ALT: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"!\[Image \d+:\s*([^\]]+)\]\([^)]+\)").expect("besoccer image pattern")
});
static PLAYER_URL: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)\]\((https://(?:[a-z0-9-]+\.)?besoccer\.(?:com|es)/jugador/[^)]+)\)\s*$")
        .expect("besoccer player url pattern")
});
static POSITION: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)\b(?:PT|GK|DF|DEF|MC|MF|MED|DC|FW|DEL)\s+\*\*")
        .expect("besoccer position pattern")
});

const MONTHS: [(&str, u32); 16] = [
    ("ENE", 1),
    ("JAN", 1),
    ("FEB", 2),
    ("MAR", 3),
    ("ABR", 4),
    ("APR", 4),
    ("MAY", 5),
    ("JUN", 6),
    ("JUL", 7),
    ("AGO", 8),
    ("AUG", 8),
    ("SEP", 9),
    ("OCT", 10),
    ("NOV", 11),
    ("DIC", 12),
    ("DEC", 12),
];
const ACTION_PREFIXES: [(&str, TransferType, bool); 5] = [
    ("cesión o/c", TransferType::Loan, true),
    ("cesión", TransferType::Loan, true),
    ("fichaje gratis", TransferType::FreeTransfer, false),
    ("agente libre", TransferType::FreeTransfer, false),
    ("fichaje", TransferType::Transfer, false),
];

fn heading_date(line: &str) -> Option<NaiveDate> {
    let line = clean(line);
    let found = DATE_HEADING.captures(&line)?;
    let month = found[2].to_uppercase();
    let month = MONTHS.iter().find(|(name, _)| *name == month)?.1;
    NaiveDate::from_ymd_opt(found[3].parse().ok()?, month, found[1].parse().ok()?)
}

/// `(player name, type, is_loan)` of an action label like `Cesión Jane Doe`.
fn label_details(label: &str) -> Option<(String, TransferType, bool)> {
    let clean_label = clean(label);
    let folded = clean_label.to_lowercase();
    ACTION_PREFIXES.iter().find_map(|(prefix, kind, is_loan)| {
        folded.strip_prefix(&format!("{prefix} ")).map(|_| {
            let name: String = clean_label.chars().skip(prefix.chars().count()).collect();
            (name.trim().to_owned(), *kind, *is_loan)
        })
    })
}

fn position(line: &str) -> String {
    POSITION
        .find(line)
        .and_then(|found| {
            found
                .as_str()
                .split_whitespace()
                .next()
                .map(str::to_uppercase)
        })
        .unwrap_or_default()
}

/// Parse dated confirmed routes from BeSoccer's global transfer feed.
pub fn parse_besoccer_transfer_markdown(
    markdown: &str,
    source_url: &str,
    start_date: Option<NaiveDate>,
    end_date: Option<NaiveDate>,
) -> Vec<Transfer> {
    let mut transfers = Vec::new();
    let mut seen: HashSet<(String, String, String, String)> = HashSet::new();
    let mut event_date: Option<NaiveDate> = None;

    for line in fl_text::split_lines(markdown) {
        if let Some(heading) = heading_date(line) {
            event_date = Some(heading);
            continue;
        }
        let Some(event_date) = event_date else {
            continue;
        };
        if !line.trim_start().starts_with('*')
            || !date_in_range(Some(event_date), start_date, end_date)
        {
            continue;
        }

        let labels: Vec<String> = IMAGE_ALT
            .captures_iter(line)
            .map(|found| clean(&found[1]))
            .collect();
        if labels.len() < 3 {
            continue;
        }
        let (Some((player_name, kind, is_loan)), Some(proof)) =
            (label_details(&labels[0]), PLAYER_URL.captures(line))
        else {
            continue;
        };
        if player_name.is_empty() {
            continue;
        }
        let (from_club, to_club) = if labels[0].to_lowercase().starts_with("agente libre ") {
            ("Free Agent".to_owned(), labels[labels.len() - 1].clone())
        } else if labels.len() >= 4 {
            (
                labels[labels.len() - 2].clone(),
                labels[labels.len() - 1].clone(),
            )
        } else {
            continue;
        };
        if from_club.is_empty() || to_club.is_empty() {
            continue;
        }

        let date = event_date.format("%Y-%m-%d").to_string();
        let key = (
            fl_text::fold_text(&player_name),
            fl_text::fold_text(&from_club),
            fl_text::fold_text(&to_club),
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
            position: position(line),
            is_loan,
            sources: vec!["besoccer".to_owned()],
            source_urls: vec![source_url.to_owned()],
            proof_urls: vec![proof[1].to_owned()],
            verification_status: VerificationStatus::Corroborator,
            ..Transfer::default()
        });
    }
    transfers
}

/// Fetch BeSoccer's feed through the reader and keep routes inside the range.
pub async fn fetch_besoccer_transfers(
    client: &HttpClient,
    since: Option<&SinceDate>,
    window: Window,
    today: NaiveDate,
) -> Result<Vec<Transfer>, SourceError> {
    let (start_date, end_date) = resolve_date_range(since, window, today)?;
    let request = Request::get(reader_url(BESOCCER_TRANSFERS_URL))
        .headers(HEADERS)
        .timeout(REQUEST_TIMEOUT);
    let response = client.execute(&request, &RetryPolicy::NONE).await?;
    if response.status != 200 {
        return Err(SourceError::Unavailable(format!(
            "HTTP {}",
            response.status
        )));
    }
    let transfers = parse_besoccer_transfer_markdown(
        &response.text(),
        BESOCCER_TRANSFERS_URL,
        start_date,
        end_date,
    );
    tracing::info!(
        found = transfers.len(),
        "BeSoccer dated corroboration routes"
    );
    Ok(transfers)
}
