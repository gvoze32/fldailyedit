//! Best-effort Sofascore corroboration from its global transfer page.
//!
//! The host expects a browser fingerprint, so requests go through the shared
//! rustls client with a Chrome header set. Verified live (2026-10):
//! `www.sofascore.com` answers a rustls handshake with a 302 challenge and the
//! Jina reader gets a 403 wrapper, but the page is served in full (same
//! `__NEXT_DATA__` payload) by the `sofascore.app` hostname, which is one of
//! the candidates below. The candidate order is fixed so cassette replays keep
//! hitting the first request, and when every route is refused the batch is
//! a [`SourceError::Unavailable`] the caller logs: Sofascore only corroborates
//! events from stronger sources and never creates any, so losing it is safe.

use std::collections::HashSet;
use std::sync::LazyLock;
use std::time::Duration;

use chrono::NaiveDate;
use regex::Regex;
use serde_json::{Map, Value, json};

use crate::dates::{
    SinceDate, Window, date_in_range, parse_external_date, parse_external_timestamp,
    resolve_date_range,
};
use crate::http::{HttpClient, Request, RetryPolicy};
use crate::model::{Transfer, TransferType, VerificationStatus};
use crate::supplemental::{SourceError, clean, clean_value, or_value, reader_url};
use fl_text::{club_key_narrow as club_key, normalize_alnum as normalize};

pub const SOFASCORE_TRANSFER_PAGE_URL: &str = "https://www.sofascore.com/football/player-transfers";
const TRANSFER_API_URL: &str = "https://api.sofascore.app/api/v1/transfer?page=1&sort=-transferFee";
const TRANSFER_API_APP_URL: &str = "https://sofascore.app/api/v1/transfer?page=1&sort=-transferFee";
const TRANSFER_PAGE_APP_URL: &str = "https://sofascore.app/football/player-transfers";
const TRANSFER_PAGE_APP_WWW_URL: &str = "https://www.sofascore.app/football/player-transfers";
const TRANSFER_PAGE_RETRIES: usize = 2;
const REQUEST_TIMEOUT: Duration = Duration::from_secs(30);

/// Headers set on every request; the browser fingerprint headers stand in for
/// TLS impersonation.
const SESSION_HEADERS: [(&str, &str); 9] = [
    (
        "Accept",
        "text/html,application/xhtml+xml,application/json, text/plain, */*",
    ),
    ("Referer", "https://www.sofascore.com/"),
    (
        "User-Agent",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    ),
    ("Accept-Language", "en-US,en;q=0.9"),
    (
        "sec-ch-ua",
        "\"Chromium\";v=\"131\", \"Not_A Brand\";v=\"24\", \"Google Chrome\";v=\"131\"",
    ),
    ("sec-ch-ua-mobile", "?0"),
    ("sec-ch-ua-platform", "\"Windows\""),
    ("Sec-Fetch-Dest", "document"),
    ("Sec-Fetch-Mode", "navigate"),
];
const JINA_HEADERS: [(&str, &str); 2] = [
    (
        "Accept",
        "text/html, application/xhtml+xml, text/plain, */*",
    ),
    ("X-Respond-With", "html"),
];

static NEXT_DATA: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r#"(?is)<script\b[^>]*\bid=["']__NEXT_DATA__["'][^>]*>(.*?)</script>"#)
        .expect("next data pattern")
});

/// Numeric transfer-type codes used by the API.
fn type_by_number(code: i64) -> Option<(TransferType, bool)> {
    Some(match code {
        1 => (TransferType::Loan, true),
        2 => (TransferType::EndOfLoan, false),
        3 | 5 | 7 | 9 | 10 => (TransferType::Transfer, false),
        4 | 6 | 8 | 11..=14 => (TransferType::FreeTransfer, false),
        _ => return None,
    })
}

fn name_of(value: &Value) -> String {
    match value {
        Value::Object(map) => clean_value(or_value(map.get("name"), map.get("shortName"))),
        other => clean_value(other),
    }
}

fn transfer_type(value: &Value) -> Option<(TransferType, bool)> {
    match value {
        Value::Bool(_) => None,
        Value::Number(number) => {
            let code = number
                .as_i64()
                .or_else(|| number.as_f64().map(|float| float.trunc() as i64))?;
            type_by_number(code)
        }
        other => {
            let normalized: String = fl_text::casefold(&clean_value(other))
                .chars()
                .filter(char::is_ascii_lowercase)
                .collect();
            let has = |token: &str| normalized.contains(token);
            if has("endofloan") || has("returnfromloan") {
                Some((TransferType::EndOfLoan, false))
            } else if has("loan") {
                Some((TransferType::Loan, true))
            } else if ["released", "waived", "retired", "contractexpired"]
                .iter()
                .any(|token| has(token))
            {
                Some((TransferType::FreeTransfer, false))
            } else if has("transfer")
                || has("traded")
                || has("draft")
                || has("signed")
                || has("claimed")
            {
                Some((TransferType::Transfer, false))
            } else {
                None
            }
        }
    }
}

/// Transfer objects anywhere in the payload: objects inside homogeneous object
/// lists that carry a player and a source team, in document order.
fn transfer_items(payload: &Value) -> Vec<&Map<String, Value>> {
    fn walk<'a>(value: &'a Value, result: &mut Vec<&'a Map<String, Value>>) {
        match value {
            Value::Object(map) => {
                for child in map
                    .values()
                    .filter(|child| matches!(child, Value::Object(_) | Value::Array(_)))
                {
                    walk(child, result);
                }
            }
            Value::Array(items) => {
                if !items.is_empty() && items.iter().all(Value::is_object) {
                    for item in items.iter().filter_map(Value::as_object) {
                        let has_player =
                            item.contains_key("player") || item.contains_key("playerName");
                        let has_source =
                            item.contains_key("transferFrom") || item.contains_key("fromTeamName");
                        if has_player && has_source {
                            result.push(item);
                        }
                    }
                }
                for child in items
                    .iter()
                    .filter(|child| matches!(child, Value::Object(_) | Value::Array(_)))
                {
                    walk(child, result);
                }
            }
            _ => {}
        }
    }
    let mut result = Vec::new();
    walk(payload, &mut result);
    result
}

fn item_team(item: &Map<String, Value>, keys: &[&str]) -> String {
    keys.iter()
        .map(|key| item.get(*key).map(name_of).unwrap_or_default())
        .find(|name| !name.is_empty())
        .unwrap_or_default()
}

fn item_date(item: &Map<String, Value>) -> Option<NaiveDate> {
    ["transferDateTimestamp", "transferDate", "date"]
        .iter()
        .find_map(|key| match item.get(*key)? {
            Value::Number(number) => parse_external_timestamp(number.as_f64()?),
            Value::String(text) => parse_external_date(text),
            _ => None,
        })
}

fn item_fee(item: &Map<String, Value>) -> String {
    ["transferFeeDescription", "transferFeeRaw", "transferFee"]
        .iter()
        .find_map(|key| {
            let value = item.get(*key)?;
            let value = match value {
                Value::Object(map) => or_value(map.get("description"), map.get("value")),
                other => other,
            };
            Some(clean_value(value)).filter(|fee| !fee.is_empty())
        })
        .unwrap_or_default()
}

/// Parse Sofascore transfer objects as corroboration-only dated routes.
pub fn parse_sofascore_transfer_payload(
    payload: &Value,
    source_url: &str,
    start_date: Option<NaiveDate>,
    end_date: Option<NaiveDate>,
) -> Vec<Transfer> {
    let mut transfers = Vec::new();
    let mut seen: HashSet<(String, String, String, String)> = HashSet::new();
    for item in transfer_items(payload) {
        let player_name = name_of(or_value(item.get("player"), item.get("playerName")));
        let event_date = item_date(item);
        let Some(event_date) = event_date.filter(|_| !player_name.is_empty()) else {
            continue;
        };
        if !date_in_range(Some(event_date), start_date, end_date) {
            continue;
        }
        let Some((kind, is_loan)) =
            transfer_type(or_value(item.get("type"), item.get("transferType")))
        else {
            continue;
        };
        let mut from_club = item_team(item, &["fromTeamName", "transferFrom", "fromTeam"]);
        let mut to_club = item_team(item, &["toTeamName", "transferTo", "toTeam"]);
        if from_club.is_empty() && !to_club.is_empty() {
            from_club = "Free Agent".to_owned();
        }
        if to_club.is_empty() && !from_club.is_empty() {
            to_club = "Free Agent".to_owned();
        }
        if from_club.is_empty() || to_club.is_empty() {
            continue;
        }

        let date = event_date.format("%Y-%m-%d").to_string();
        let transfer_id = or_value(item.get("id"), item.get("transferId"));
        let identity = if matches!(transfer_id, Value::Null) {
            format!("name:{}", fl_text::casefold(&player_name))
        } else {
            format!("id:{transfer_id}")
        };
        if !seen.insert((
            identity,
            fl_text::casefold(&from_club),
            fl_text::casefold(&to_club),
            date.clone(),
        )) {
            continue;
        }
        let position = match item.get("player") {
            Some(Value::Object(player)) => {
                player.get("position").map(clean_value).unwrap_or_default()
            }
            _ => String::new(),
        };
        transfers.push(Transfer {
            player_name,
            from_club,
            to_club,
            date,
            transfer_type: kind,
            fee: item_fee(item),
            position,
            is_loan,
            sources: vec!["sofascore".to_owned()],
            source_urls: if source_url.is_empty() {
                Vec::new()
            } else {
                vec![source_url.to_owned()]
            },
            verification_status: VerificationStatus::Corroborator,
            ..Transfer::default()
        });
    }
    transfers
}

/// Next.js page props of a rendered page.
fn next_page_props(page_html: &str) -> Option<Map<String, Value>> {
    let raw = NEXT_DATA.captures(page_html)?[1].trim().to_owned();
    let next_data: Value = serde_json::from_str(&raw)
        .or_else(|_| serde_json::from_str(&html_escape::decode_html_entities(&raw)))
        .ok()?;
    match next_data.get("props")?.get("pageProps")? {
        Value::Object(props) => Some(props.clone()),
        _ => None,
    }
}

/// `{"transfers": [...]}` extracted from a rendered Sofascore page, or `None`
/// when the page carries no recognizable transfer data.
pub fn global_transfer_payload(page_html: &str) -> Option<Value> {
    let page_props = next_page_props(page_html)?;
    let fallback = page_props.get("fallbackData");
    let recognized = matches!(fallback, Some(Value::Array(_) | Value::Object(_)));
    let containers: Vec<&Value> = match fallback {
        Some(Value::Array(items)) => items.iter().collect(),
        Some(object @ Value::Object(_)) => vec![object],
        _ => Vec::new(),
    };

    let objects_in = |value: &Value| -> Vec<Value> {
        value
            .as_array()
            .map(|items| {
                items
                    .iter()
                    .filter(|item| item.is_object())
                    .cloned()
                    .collect()
            })
            .unwrap_or_default()
    };
    let mut transfers: Vec<Value> = containers
        .iter()
        .filter_map(|container| container.get("transfers"))
        .filter(|items| items.is_array())
        .flat_map(objects_in)
        .collect();

    let mut recognized = recognized;
    if let Some(direct) = page_props
        .get("transfers")
        .filter(|direct| direct.is_array())
        .filter(|_| transfers.is_empty())
    {
        transfers.extend(objects_in(direct));
        recognized = true;
    }
    if !recognized {
        let embedded: Vec<Value> = transfer_items(&Value::Object(page_props))
            .into_iter()
            .map(|item| Value::Object(item.clone()))
            .collect();
        if !embedded.is_empty() {
            return Some(json!({ "transfers": embedded }));
        }
    }
    recognized.then(|| json!({ "transfers": transfers }))
}

/// One place to try, in order.
struct Candidate {
    url: &'static str,
    attempts: usize,
    jina: bool,
}

const CANDIDATES: [Candidate; 6] = [
    Candidate {
        url: SOFASCORE_TRANSFER_PAGE_URL,
        attempts: 1,
        jina: false,
    },
    Candidate {
        url: "",
        attempts: 1,
        jina: true,
    },
    Candidate {
        url: TRANSFER_PAGE_APP_URL,
        attempts: TRANSFER_PAGE_RETRIES,
        jina: false,
    },
    Candidate {
        url: TRANSFER_PAGE_APP_WWW_URL,
        attempts: 1,
        jina: false,
    },
    Candidate {
        url: TRANSFER_API_URL,
        attempts: 1,
        jina: false,
    },
    Candidate {
        url: TRANSFER_API_APP_URL,
        attempts: 1,
        jina: false,
    },
];

async fn fetch_candidate(client: &HttpClient, url: &str, jina: bool) -> Result<Value, String> {
    let mut request = Request::get(url)
        .headers(SESSION_HEADERS)
        .timeout(REQUEST_TIMEOUT)
        .no_redirects();
    if jina {
        request = request.headers(JINA_HEADERS);
    }
    let response = client
        .execute(&request, &RetryPolicy::NONE)
        .await
        .map_err(|error| error.to_string())?;
    if response.status != 200 {
        return Err(format!("HTTP {}", response.status));
    }
    if url.contains("/api/v1/transfer") {
        match response.json() {
            Ok(payload @ (Value::Object(_) | Value::Array(_))) => Ok(payload),
            _ => Err("API response has no transfer payload".to_owned()),
        }
    } else {
        global_transfer_payload(&response.text())
            .ok_or_else(|| "page has no embedded transfers".to_owned())
    }
}

/// Fetch the global transfer payload from the first place that serves it.
async fn fetch_transfer_page_payload(client: &HttpClient) -> Result<Value, SourceError> {
    let jina_url = reader_url(SOFASCORE_TRANSFER_PAGE_URL);
    let mut errors = Vec::new();
    for candidate in &CANDIDATES {
        let url = if candidate.jina {
            jina_url.as_str()
        } else {
            candidate.url
        };
        for attempt in 0..candidate.attempts {
            match fetch_candidate(client, url, candidate.jina).await {
                Ok(payload) => return Ok(payload),
                Err(message) => {
                    let message: String = message.chars().take(160).collect();
                    errors.push(format!("{url} attempt {}: {message}", attempt + 1));
                }
            }
        }
    }
    Err(SourceError::Unavailable(format!(
        "Sofascore transfer page unavailable: {}",
        errors.join("; ")
    )))
}

fn transfer_key(transfer: &Transfer) -> (String, String, String, String) {
    (
        normalize(&transfer.player_name),
        club_key(&transfer.from_club),
        club_key(&transfer.to_club),
        transfer.date.clone(),
    )
}

/// Keep routes touching one of `club_names`, deduplicated by normalized route.
pub fn filter_for_clubs(transfers: Vec<Transfer>, club_names: &[String]) -> Vec<Transfer> {
    let targets: HashSet<String> = club_names.iter().map(|name| club_key(name)).collect();
    let mut seen = HashSet::new();
    transfers
        .into_iter()
        .filter(|t| {
            targets.contains(&club_key(&t.from_club)) || targets.contains(&club_key(&t.to_club))
        })
        .filter(|t| seen.insert(transfer_key(t)))
        .collect()
}

/// Fetch Sofascore corroboration routes for the clubs of interest. With no
/// clubs nothing is requested.
pub async fn fetch_sofascore_transfers(
    client: &HttpClient,
    since: Option<&SinceDate>,
    window: Window,
    today: NaiveDate,
    club_names: &[String],
) -> Result<Vec<Transfer>, SourceError> {
    let (start_date, end_date) = resolve_date_range(since, window, today)?;
    let mut targets: Vec<String> = Vec::new();
    for name in club_names
        .iter()
        .map(|name| clean(name))
        .filter(|name| !name.is_empty())
    {
        if !targets.contains(&name) {
            targets.push(name);
        }
    }
    if targets.is_empty() {
        tracing::debug!("Sofascore skipped: no relevant transfer clubs");
        return Ok(Vec::new());
    }
    let payload = fetch_transfer_page_payload(client).await?;
    let routes = parse_sofascore_transfer_payload(
        &payload,
        SOFASCORE_TRANSFER_PAGE_URL,
        start_date,
        end_date,
    );
    let unique = filter_for_clubs(routes, &targets);
    tracing::info!(found = unique.len(), "Sofascore dated corroboration routes");
    Ok(unique)
}
