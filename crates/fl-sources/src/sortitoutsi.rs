//! Fast, moderated transfer signals from Sortitoutsi's public activity page
//! (via the Jina reader).

use std::sync::LazyLock;
use std::time::Duration;

use chrono::NaiveDate;
use regex::Regex;

use crate::dates::{SinceDate, parse_iso_date};
use crate::http::{HttpClient, Request, RetryPolicy};
use crate::model::{Transfer, TransferType, VerificationStatus};
use crate::supplemental::{SourceError, clean, parse_day_month_year, parse_ymd, strip_ordinals};

/// Reader URL (note the plain `http://` target).
pub const SORTITOUTSI_READER_URL: &str =
    "https://r.jina.ai/http://sortitoutsi.net/football-manager-data-update";
const REQUEST_TIMEOUT: Duration = Duration::from_secs(30);
const HEADERS: [(&str, &str); 2] = [
    (
        "User-Agent",
        "fldailyedit/0.1 (PES transfer updater; contact via project repository)",
    ),
    ("Accept", "text/html, text/markdown;q=0.9, */*;q=0.5"),
];

fn compile(pattern: &str) -> Regex {
    Regex::new(pattern).expect("static sortitoutsi pattern")
}

static STATUS: LazyLock<Regex> = LazyLock::new(|| {
    compile(r"(?m)^\s*(Enabled|Pending|Disabled|Unconfirmed|Wrong Proof|Duplicate|Spam)\s*$")
});
static PERSON_LINK: LazyLock<Regex> = LazyLock::new(|| {
    compile(
        r#"\[([^\[\]]+?)\]\(https?://sortitoutsi\.net/football-manager-data-update/person/(\d+)(?:\s+"[^"]*")?\)"#,
    )
});
static TEAM_LINK: LazyLock<Regex> = LazyLock::new(|| {
    compile(
        r#"\[([^\[\]]+?)\]\(https?://sortitoutsi\.net/football-manager-data-update/team/(\d+)(?:\s+"[^"]*")?\)"#,
    )
});
static SUBMISSION: LazyLock<Regex> = LazyLock::new(|| {
    compile(
        r"\[([^\]]+)\]\((https?://sortitoutsi\.net/football-manager-data-update/submission/\d+)\)",
    )
});
static PROOF: LazyLock<Regex> =
    LazyLock::new(|| compile(r"(?i)\[Proof \([^\]]+\)\]\((https?://[^)]+)\)"));
static STARTING_ON: LazyLock<Regex> =
    LazyLock::new(|| compile(r"(?i)starting on (\d{1,2}(?:st|nd|rd|th)? [A-Za-z]+ \d{4})"));
static SUBMISSION_STAMP: LazyLock<Regex> = LazyLock::new(|| compile(r"^(\d{1,2} [A-Za-z]+ \d{4})"));
static RELEASED_FREE: LazyLock<Regex> =
    LazyLock::new(|| compile(r"(?s)\bReleased\b.+? on a free transfer"));

fn parse_human_date(value: &str) -> Option<NaiveDate> {
    let text = strip_ordinals(&clean(value));
    parse_day_month_year(&text, true)
        .or_else(|| parse_day_month_year(&text, false))
        .or_else(|| parse_ymd(&text))
}

fn effective_date(block: &str) -> Option<NaiveDate> {
    parse_human_date(&STARTING_ON.captures(block)?[1])
}

fn submission_date(submission_label: &str) -> Option<NaiveDate> {
    parse_human_date(&SUBMISSION_STAMP.captures(submission_label)?[1])
}

/// Parse only moderated `Enabled` transfer/loan submissions that carry a
/// submission link and proof.
pub fn parse_sortitoutsi_markdown(
    markdown: &str,
    start_date: Option<NaiveDate>,
    end_date: Option<NaiveDate>,
) -> Vec<Transfer> {
    let Some(marker_index) = markdown.find("Recent Submissions") else {
        return Vec::new();
    };
    let section = &markdown[marker_index..];
    let statuses: Vec<_> = STATUS.captures_iter(section).collect();
    let mut transfers = Vec::new();

    for (index, status) in statuses.iter().enumerate() {
        if &status[1] != "Enabled" {
            continue;
        }
        let whole = status.get(0).expect("group 0 always matches");
        let block_end = statuses.get(index + 1).map_or(section.len(), |next| {
            next.get(0).expect("group 0 always matches").start()
        });
        let block = &section[whole.end()..block_end];
        let Some(person) = PERSON_LINK.captures(block) else {
            continue;
        };
        let (Some(submission), Some(proof)) = (SUBMISSION.captures(block), PROOF.captures(block))
        else {
            // Enabled without traceable evidence is a human hint, not safe
            // enough for an automated roster mutation.
            continue;
        };

        let effective = effective_date(block);
        let Some(event_date) = effective.or_else(|| submission_date(&submission[1])) else {
            continue;
        };
        if start_date.is_some_and(|start| event_date < start)
            || end_date.is_some_and(|end| event_date > end)
        {
            continue;
        }

        let (kind, is_loan, mut to_club) = if block.contains(" is now on loan to ") {
            (TransferType::Loan, true, String::new())
        } else if block.contains(" has been transferred to ") {
            (TransferType::Transfer, false, String::new())
        } else if RELEASED_FREE.is_match(block) {
            (TransferType::FreeTransfer, false, "Free Agent".to_owned())
        } else {
            continue;
        };
        if matches!(kind, TransferType::Loan | TransferType::Transfer) {
            let Some(team) = TEAM_LINK.captures(block) else {
                continue;
            };
            to_club = team[1].to_owned();
        }

        let Ok(player_id) = person[2].parse::<i64>() else {
            continue;
        };
        transfers.push(Transfer {
            player_name: person[1].trim().to_owned(),
            from_club: String::new(),
            to_club: to_club.trim().to_owned(),
            date: event_date.format("%Y-%m-%d").to_string(),
            transfer_type: kind,
            is_loan,
            sources: vec!["sortitoutsi".to_owned()],
            source_urls: vec![submission[2].to_owned()],
            proof_urls: vec![proof[1].to_owned()],
            player_id_sortitoutsi: Some(player_id),
            verification_status: VerificationStatus::Enabled,
            infer_from_current_roster: effective.is_some(),
            ..Transfer::default()
        });
    }
    transfers
}

/// Fetch the latest enabled signals. The window is only bounded by
/// `since` (lower) and `today` (upper).
///
/// An unparseable `since` text means "no lower bound".
pub async fn fetch_sortitoutsi_transfers(
    client: &HttpClient,
    since: Option<&SinceDate>,
    today: NaiveDate,
) -> Result<Vec<Transfer>, SourceError> {
    let start_date = match since {
        Some(SinceDate::Date(date)) => Some(*date),
        Some(SinceDate::Text(text)) => parse_iso_date(text),
        None => None,
    };
    // Sortitoutsi rejects non-browser clients; the public reader endpoint
    // keeps moderation labels, proof links and submission URLs.
    let request = Request::get(SORTITOUTSI_READER_URL)
        .headers(HEADERS)
        .timeout(REQUEST_TIMEOUT);
    let response = client.execute(&request, &RetryPolicy::NONE).await?;
    if response.status != 200 {
        return Err(SourceError::Unavailable(format!(
            "HTTP {}",
            response.status
        )));
    }
    let transfers = parse_sortitoutsi_markdown(&response.text(), start_date, Some(today));
    tracing::info!(
        found = transfers.len(),
        "Sortitoutsi enabled transfer signals"
    );
    Ok(transfers)
}
