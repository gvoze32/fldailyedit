//! Confirmed transfers from Wikipedia's seasonal transfer lists (MediaWiki
//! API, wikitext parsing).
//!
//! Dated table rows become events; undated per-club In/Out lists become
//! corroboration-only routes.

use std::collections::{BTreeSet, HashMap, HashSet};
use std::sync::LazyLock;
use std::time::Duration;

use chrono::{Datelike, NaiveDate};
use regex::{Captures, Regex};
use serde_json::Value;

use crate::dates::{SinceDate, Window, parse_iso_date};
use crate::http::{HttpClient, Request, RetryPolicy};
use crate::model::{Transfer, TransferType, VerificationStatus};
use crate::supplemental::{
    SourceError, clean, dedupe_preserving_order, is_ascii_digits, parse_day_month_year,
    parse_month_day_year, parse_ymd, strip_ordinals, url_with_query,
};

pub const WIKIPEDIA_API_URL: &str = "https://en.wikipedia.org/w/api.php";
/// MediaWiki caps multi-value parameters such as `titles` at 50 for anonymous
/// clients and answers larger requests with an HTTP 200 `toomanyvalues` error.
pub const WIKIPEDIA_TITLES_PER_REQUEST: usize = 50;
const REQUEST_TIMEOUT: Duration = Duration::from_secs(30);
const HEADERS: [(&str, &str); 2] = [
    (
        "User-Agent",
        "FLDailyEdit/0.1 (https://github.com/gvoze32/fldailyedit)",
    ),
    ("Accept", "application/json"),
];
const API_ATTEMPTS: u32 = 4;
const MAX_RETRY_DELAY: Duration = Duration::from_secs(5);

fn compile(pattern: &str) -> Regex {
    Regex::new(pattern).expect("static wikipedia pattern")
}

static A_LEAGUE_TEMPLATE: LazyLock<Regex> =
    LazyLock::new(|| compile(r"(?is)\{\{\s*A-League team\s*\|\s*([^|{}]+)(?:\|[^{}]*)?\}\}"));
static EXPANSION_MARKER: LazyLock<Regex> = LazyLock::new(|| compile(r"@@FLDE(\d+)@@"));
static COMMENT: LazyLock<Regex> = LazyLock::new(|| compile(r"(?is)<!--.*?-->"));
static REF_BLOCK: LazyLock<Regex> = LazyLock::new(|| compile(r"(?is)<ref\b[^>]*>.*?</ref\s*>"));
static REF_SELF_CLOSING: LazyLock<Regex> = LazyLock::new(|| compile(r"(?is)<ref\b[^>]*/\s*>"));
static DTS: LazyLock<Regex> = LazyLock::new(|| compile(r"(?is)\{\{\s*dts\s*\|([^{}]*)\}\}"));
static SORTNAME: LazyLock<Regex> =
    LazyLock::new(|| compile(r"(?is)\{\{\s*sortname\s*\|\s*([^|{}]+)\|\s*([^|{}]+)(?:\|.*?)?\}\}"));
static FLAG: LazyLock<Regex> =
    LazyLock::new(|| compile(r"(?is)\{\{\s*flag(?:g|icon)?\b.*?\}\}\s*"));
static NTSH: LazyLock<Regex> = LazyLock::new(|| compile(r"(?is)\{\{\s*ntsh\s*\|.*?\}\}"));
static PIPED_LINK: LazyLock<Regex> = LazyLock::new(|| compile(r"\[\[([^\]|]+)\|([^\]]+)\]\]"));
static PLAIN_LINK: LazyLock<Regex> = LazyLock::new(|| compile(r"\[\[([^\]]+)\]\]"));
static NOWRAP_SMALL: LazyLock<Regex> =
    LazyLock::new(|| compile(r"(?is)\{\{\s*(?:nowrap|small)\s*\|\s*(.*?)\}\}"));
static GENERIC_TEMPLATE: LazyLock<Regex> = LazyLock::new(|| compile(r"\{\{([^{}]*)\}\}"));
static EXTERNAL_LINK: LazyLock<Regex> =
    LazyLock::new(|| compile(r"\[(?:https?://\S+)\s+([^\]]+)\]"));
static HTML_TAG: LazyLock<Regex> = LazyLock::new(|| compile(r"(?is)<[^>]+>"));
static ROUTE_SUFFIX: LazyLock<Regex> =
    LazyLock::new(|| compile(r"(?i)\s*\((?:end of loan|loan return|loan|free transfer)\)\s*$"));
static CELL_ATTRIBUTES: LazyLock<Regex> = LazyLock::new(|| {
    compile(r"(?is)^(?:(?:rowspan|colspan|style|class|data-sort-value)\s*=.+?)\|(.*)$")
});
static WIKI_TABLE: LazyLock<Regex> = LazyLock::new(|| compile(r"(?ms)^\{\|.*?^\|\}"));
static HEADING: LazyLock<Regex> = LazyLock::new(|| compile(r"^===\s*(.*?)\s*===$"));
static ROUTE_FROM: LazyLock<Regex> = LazyLock::new(|| compile(r"(?i)\bfrom\s+(.+)"));
static ROUTE_TO: LazyLock<Regex> = LazyLock::new(|| compile(r"(?i)\bto\s+(.+)"));
static ROUTE_TAIL: LazyLock<Regex> =
    LazyLock::new(|| compile(r"(?i),\s*(?:previously|after|following)\b"));
static PROOF_URL_CASELESS: LazyLock<Regex> =
    LazyLock::new(|| compile(r"(?i)\burl\s*=\s*(https?://[^|}\s]+)"));
static PROOF_URL: LazyLock<Regex> = LazyLock::new(|| compile(r"\burl\s*=\s*(https?://[^|}\s]+)"));
static FS_PLAYER: LazyLock<Regex> = LazyLock::new(|| compile(r"(?i)\{\{\s*fs player\b"));

/// `quote(title.replace(" ", "_"))`: percent-encode all but unreserved and `/`.
fn page_url(page_title: &str) -> String {
    let mut url = String::from("https://en.wikipedia.org/wiki/");
    for byte in page_title.replace(' ', "_").bytes() {
        match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'_' | b'.' | b'-' | b'~' | b'/' => {
                url.push(byte as char)
            }
            other => url.push_str(&format!("%{other:02X}")),
        }
    }
    url
}

fn clean_text(value: &str) -> String {
    clean(&value.replace('\u{a0}', " "))
}

fn parse_human_date(value: &str) -> Option<NaiveDate> {
    let text = strip_ordinals(&clean_text(value));
    parse_day_month_year(&text, true)
        .or_else(|| parse_month_day_year(&text))
        .or_else(|| parse_ymd(&text))
}

/// Index of the first header whose letters-only lowercase form is in `choices`.
fn header_index(headers: &[String], choices: &[&str]) -> Option<usize> {
    headers.iter().position(|header| {
        let mut normalized = String::new();
        let mut pending_space = false;
        for ch in header.to_lowercase().chars() {
            if ch.is_ascii_lowercase() {
                if pending_space {
                    normalized.push(' ');
                }
                pending_space = false;
                normalized.push(ch);
            } else {
                pending_space = !normalized.is_empty() || pending_space;
            }
        }
        choices.contains(&normalized.as_str())
    })
}

fn transfer_type(fee: &str) -> (TransferType, bool) {
    let normalized = fl_text::casefold(fee);
    if normalized.contains("end of loan") || normalized.contains("loan return") {
        (TransferType::EndOfLoan, false)
    } else if normalized.contains("loan") {
        (TransferType::Loan, true)
    } else if normalized.contains("free") {
        (TransferType::FreeTransfer, false)
    } else {
        (TransferType::Transfer, false)
    }
}

/// Split wiki cells without cutting delimiters inside links/templates.
fn split_top_level<'a>(value: &'a str, delimiter: &str) -> Vec<&'a str> {
    let bytes = value.as_bytes();
    let mut parts = Vec::new();
    let (mut start, mut index) = (0usize, 0usize);
    let (mut template_depth, mut link_depth) = (0usize, 0usize);
    while index < bytes.len() {
        let pair = bytes.get(index..index + 2).unwrap_or(&[]);
        match pair {
            b"{{" => {
                template_depth += 1;
                index += 2;
            }
            b"}}" => {
                template_depth = template_depth.saturating_sub(1);
                index += 2;
            }
            b"[[" => {
                link_depth += 1;
                index += 2;
            }
            b"]]" => {
                link_depth = link_depth.saturating_sub(1);
                index += 2;
            }
            _ if template_depth == 0
                && link_depth == 0
                && bytes[index..].starts_with(delimiter.as_bytes()) =>
            {
                parts.push(&value[start..index]);
                index += delimiter.len();
                start = index;
            }
            _ => index += 1,
        }
    }
    parts.push(&value[start..]);
    parts
}

fn date_template(found: &Captures) -> String {
    let parts: Vec<&str> = split_top_level(&found[1], "|")
        .into_iter()
        .map(str::trim)
        .filter(|p| !p.is_empty() && !p.contains('='))
        .collect();
    if parts.len() < 3
        || !parts[..3]
            .iter()
            .all(|part| crate::supplemental::is_ascii_digits(part))
    {
        return String::new();
    }
    let number = |part: &str| part.parse::<u32>().ok();
    match (
        parts[0].parse::<i32>().ok().filter(|year| *year >= 1),
        number(parts[1]),
        number(parts[2]),
    ) {
        (Some(year), Some(month), Some(day)) => NaiveDate::from_ymd_opt(year, month, day)
            .map(|date| date.format("%Y-%m-%d").to_string())
            .unwrap_or_default(),
        _ => String::new(),
    }
}

fn generic_template(found: &Captures) -> String {
    let parts: Vec<&str> = split_top_level(&found[1], "|")
        .into_iter()
        .map(str::trim)
        .collect();
    let name = parts.first().copied().unwrap_or("");
    let lowered = name.to_lowercase();
    if ["loan", "free transfer", "free", "released"].contains(&lowered.as_str()) {
        return name.to_owned();
    }
    if let Some(last) = parts
        .iter()
        .skip(1)
        .rfind(|part| !part.is_empty() && !part.contains('='))
    {
        return (*last).to_owned();
    }
    if ["cite", "ref", "flag"]
        .iter()
        .any(|token| lowered.contains(token))
    {
        return String::new();
    }
    if parts.len() == 1 {
        name.to_owned()
    } else {
        String::new()
    }
}

/// Wikitext -> plain text: templates, links, refs and markup removed.
fn clean_wikitext(value: &str) -> String {
    let value = COMMENT.replace_all(value, "");
    let value = REF_BLOCK.replace_all(&value, "");
    let value = REF_SELF_CLOSING.replace_all(&value, "");
    let value =
        A_LEAGUE_TEMPLATE.replace_all(&value, |found: &Captures| found[1].trim().to_owned());
    let value = DTS.replace_all(&value, |found: &Captures| date_template(found));
    let value = SORTNAME.replace_all(&value, |found: &Captures| {
        format!("{} {}", found[1].trim(), found[2].trim())
    });
    let value = FLAG.replace_all(&value, "");
    let value = NTSH.replace_all(&value, "");
    let value = PIPED_LINK.replace_all(&value, "$2");
    let value = PLAIN_LINK.replace_all(&value, "$1");
    let value = NOWRAP_SMALL.replace_all(&value, "$1");
    let mut value = value.into_owned();
    for _ in 0..4 {
        let updated = GENERIC_TEMPLATE
            .replace_all(&value, |found: &Captures| generic_template(found))
            .into_owned();
        if updated == value {
            break;
        }
        value = updated;
    }
    let value = EXTERNAL_LINK.replace_all(&value, "$1");
    let value = HTML_TAG.replace_all(&value, "");
    let value = value.replace("'''", "").replace("''", "");
    clean_text(&html_escape::decode_html_entities(&value))
}

fn clean_route_club(value: &str) -> String {
    ROUTE_SUFFIX
        .replace(&clean_wikitext(value), "")
        .trim()
        .to_owned()
}

/// Strip table-cell attributes such as `rowspan="4" |` while keeping the
/// first pipe that belongs to a template or wikilink.
fn wiki_cell(value: &str) -> String {
    let stripped = value.trim();
    match CELL_ATTRIBUTES.captures(stripped) {
        Some(found) => found[1].trim().to_owned(),
        None => stripped.to_owned(),
    }
}

type WikiTable = (Vec<String>, Vec<Vec<String>>);

fn wikitext_tables(wikitext: &str) -> Vec<WikiTable> {
    let mut tables = Vec::new();
    for table in WIKI_TABLE.find_iter(wikitext) {
        let mut headers: Vec<String> = Vec::new();
        let mut rows: Vec<Vec<String>> = Vec::new();
        let mut current: Vec<String> = Vec::new();
        let mut in_header = true;
        for raw_line in fl_text::split_lines(table.as_str()).into_iter().skip(1) {
            let line = raw_line.trim();
            if line.starts_with("|-") {
                if !current.is_empty() {
                    rows.push(std::mem::take(&mut current));
                }
                if !headers.is_empty() {
                    in_header = false;
                }
                continue;
            }
            if line == "|}" {
                if !current.is_empty() {
                    rows.push(std::mem::take(&mut current));
                }
                break;
            }
            if let (Some(cells), true) = (line.strip_prefix('!'), in_header) {
                headers.extend(
                    split_top_level(cells, "!!")
                        .into_iter()
                        .map(|part| clean_wikitext(&wiki_cell(part))),
                );
            } else if let Some(cells) = line.strip_prefix('|') {
                current.extend(split_top_level(cells, "||").into_iter().map(wiki_cell));
            } else if let Some(last) = current.last_mut() {
                *last = format!("{last} {line}");
            }
        }
        if !headers.is_empty() && !rows.is_empty() {
            tables.push((headers, rows));
        }
    }
    tables
}

/// Balanced body (between `{{` and `}}`) of the first `{{name ...}}` template.
fn template_body<'a>(value: &'a str, pattern: &Regex) -> Option<&'a str> {
    let start = pattern.find(value)?.start();
    let bytes = value.as_bytes();
    let mut depth = 0i32;
    let mut index = start;
    while index + 1 < bytes.len() {
        match &bytes[index..index + 2] {
            b"{{" => {
                depth += 1;
                index += 2;
            }
            b"}}" => {
                depth -= 1;
                if depth == 0 {
                    return Some(&value[start + 2..index]);
                }
                index += 2;
            }
            _ => index += 1,
        }
    }
    None
}

/// Undated per-club In/Out lists as corroboration-only routes.
fn parse_club_lists(wikitext: &str, source_url: &str) -> Vec<Transfer> {
    let mut parsed = Vec::new();
    let mut club = String::new();
    let mut direction = "";

    for raw_line in fl_text::split_lines(wikitext) {
        let line = raw_line.trim();
        if let Some(heading) = HEADING.captures(line) {
            club = clean_wikitext(&heading[1]);
            direction = "";
            continue;
        }
        let marker = fl_text::casefold(&clean_wikitext(line));
        match marker.trim_end_matches(':') {
            "in" => {
                direction = "in";
                continue;
            }
            "out" => {
                direction = "out";
                continue;
            }
            _ => {}
        }
        if club.is_empty() || direction.is_empty() {
            continue;
        }
        let Some(body) = template_body(line, &FS_PLAYER) else {
            continue;
        };
        let mut params: HashMap<String, String> = HashMap::new();
        for part in split_top_level(body, "|").into_iter().skip(1) {
            if let Some((name, value)) = part.split_once('=') {
                params.insert(fl_text::casefold(name.trim()), value.trim().to_owned());
            }
        }

        let player = clean_wikitext(params.get("name").map_or("", String::as_str));
        let other = clean_wikitext(params.get("other").map_or("", String::as_str));
        let route_pattern = if direction == "in" {
            &ROUTE_FROM
        } else {
            &ROUTE_TO
        };
        let Some(route) = route_pattern.captures(&other) else {
            continue;
        };
        if player.is_empty() {
            continue;
        }
        let route = &route[1];
        let other_club = match ROUTE_TAIL.find(route) {
            Some(tail) => &route[..tail.start()],
            None => route,
        }
        .trim()
        .to_owned();
        if other_club.is_empty() || other_club == club {
            continue;
        }

        let lowered = fl_text::casefold(&other);
        let (kind, is_loan) = if lowered.contains("loan return") {
            (TransferType::EndOfLoan, false)
        } else if lowered.contains("loan") {
            (TransferType::Loan, true)
        } else if lowered.contains("free") {
            (TransferType::FreeTransfer, false)
        } else {
            (TransferType::Transfer, false)
        };
        let (from_club, to_club) = if direction == "in" {
            (other_club, club.clone())
        } else {
            (club.clone(), other_club)
        };
        let proof_urls = dedupe_preserving_order(
            PROOF_URL_CASELESS
                .captures_iter(line)
                .map(|found| found[1].to_owned()),
        );
        parsed.push(Transfer {
            player_name: player,
            from_club,
            to_club,
            transfer_type: kind,
            position: clean_wikitext(params.get("pos").map_or("", String::as_str)),
            is_loan,
            sources: vec!["wikipedia".to_owned()],
            source_urls: vec![source_url.to_owned()],
            proof_urls,
            verification_status: VerificationStatus::Corroborator,
            ..Transfer::default()
        });
    }
    parsed
}

/// Upper-case codes of every `{{A-League team|CODE}}` in `wikitext`.
pub fn a_league_codes(wikitext: &str) -> BTreeSet<String> {
    A_LEAGUE_TEMPLATE
        .captures_iter(wikitext)
        .map(|found| found[1].trim().to_uppercase())
        .collect()
}

fn substitute_a_league_teams(wikitext: &str, team_names: &HashMap<String, String>) -> String {
    if team_names.is_empty() {
        return wikitext.to_owned();
    }
    A_LEAGUE_TEMPLATE
        .replace_all(wikitext, |found: &Captures| {
            team_names
                .get(&found[1].trim().to_uppercase())
                .cloned()
                .unwrap_or_else(|| found[0].to_owned())
        })
        .into_owned()
}

/// Parse dated table events and undated club-list corroborators from one
/// page. `team_names` expands `{{A-League team|CODE}}` placeholders.
pub fn parse_wikipedia_transfer_wikitext(
    wikitext: &str,
    page_title: &str,
    start_date: Option<NaiveDate>,
    end_date: Option<NaiveDate>,
    team_names: &HashMap<String, String>,
) -> Vec<Transfer> {
    let source_url = page_url(page_title);
    let wikitext = substitute_a_league_teams(wikitext, team_names);
    let mut parsed = parse_club_lists(&wikitext, &source_url);
    for (headers, rows) in wikitext_tables(&wikitext) {
        let (Some(date_index), Some(player_index), Some(from_index), Some(to_index)) = (
            header_index(&headers, &["date"]),
            header_index(&headers, &["player", "name"]),
            header_index(&headers, &["from", "moving from", "previous club"]),
            header_index(&headers, &["to", "moving to", "new club"]),
        ) else {
            continue;
        };
        let fee_index = header_index(&headers, &["fee", "transfer fee"]);
        let required = date_index.max(player_index).max(from_index).max(to_index);
        let mut last_date_text = String::new();
        for mut values in rows {
            if values.len() + 1 == headers.len() && !last_date_text.is_empty() {
                values.insert(date_index.min(values.len()), last_date_text.clone());
            }
            if values.len() <= required {
                continue;
            }
            let Some(event_date) = parse_human_date(&clean_wikitext(&values[date_index])) else {
                continue;
            };
            last_date_text = values[date_index].clone();
            if start_date.is_some_and(|start| event_date < start)
                || end_date.is_some_and(|end| event_date > end)
            {
                continue;
            }
            let player = clean_wikitext(&values[player_index]);
            let (from_raw, to_raw) = (&values[from_index], &values[to_index]);
            let (from_club, to_club) = (clean_route_club(from_raw), clean_route_club(to_raw));
            let fee_raw = fee_index
                .and_then(|index| values.get(index))
                .map_or("", String::as_str);
            let fee = clean_wikitext(fee_raw);
            if player.is_empty()
                || from_club.is_empty()
                || to_club.is_empty()
                || from_club == to_club
            {
                continue;
            }
            let route_detail = clean_wikitext(&format!("{from_raw} {to_raw} {fee_raw}"));
            let (kind, is_loan) = transfer_type(&route_detail);
            let proof_urls = dedupe_preserving_order(
                PROOF_URL
                    .captures_iter(&values.join(" "))
                    .map(|found| found[1].to_owned()),
            );
            parsed.push(Transfer {
                player_name: player,
                from_club,
                to_club,
                date: event_date.format("%Y-%m-%d").to_string(),
                transfer_type: kind,
                fee,
                is_loan,
                sources: vec!["wikipedia".to_owned()],
                source_urls: vec![source_url.clone()],
                proof_urls,
                ..Transfer::default()
            });
        }
    }
    parsed
}

/// Category pages that list the seasonal transfer pages worth reading.
///
/// Supplemental sources serve recent freshness only (FotMob stays the
/// historical replay source), so discovery is capped to two seasons.
pub fn category_candidates(
    today: NaiveDate,
    since: Option<NaiveDate>,
    window: Window,
) -> Vec<String> {
    let years = match since {
        Some(since) => (since.year().max(today.year() - 1))..=today.year(),
        None => today.year()..=today.year(),
    };
    let periods: &[&str] = match window {
        Window::Summer => &["summer"],
        Window::Winter => &["winter"],
        Window::All => &["winter", "summer"],
        Window::Auto if today.month() <= 3 => &["winter"],
        Window::Auto => &["summer"],
    };
    let mut categories = Vec::new();
    for year in years {
        for period in periods {
            categories.push(format!("Category:Football transfers {period} {year}"));
            if *period == "winter" {
                categories.push(format!(
                    "Category:Football transfers winter {}\u{2013}{:02}",
                    year - 1,
                    year.rem_euclid(100)
                ));
            }
        }
    }
    dedupe_preserving_order(categories)
}

/// Titles of men's pages in category-member payloads.
pub fn category_titles(payloads: &[Value]) -> Vec<String> {
    let titles = payloads
        .iter()
        .flat_map(|payload| {
            payload["query"]["categorymembers"]
                .as_array()
                .into_iter()
                .flatten()
        })
        .filter_map(|member| member["title"].as_str())
        .filter(|title| !title.is_empty() && !title.to_lowercase().contains("women"))
        .map(str::to_owned);
    dedupe_preserving_order(titles)
}

/// `(title, wikitext)` of every page with content in revision payloads.
pub fn page_wikitexts(payloads: &[Value]) -> Vec<(String, String)> {
    let mut pages = Vec::new();
    for payload in payloads {
        let Some(map) = payload["query"]["pages"].as_object() else {
            continue;
        };
        for page in map.values() {
            let title = page["title"].as_str().unwrap_or("");
            let slot = &page["revisions"][0]["slots"]["main"];
            let wikitext = ["*", "content"]
                .iter()
                .filter_map(|key| slot[*key].as_str())
                .find(|text| !text.is_empty())
                .unwrap_or("");
            if !title.is_empty() && !wikitext.is_empty() {
                pages.push((title.to_owned(), wikitext.to_owned()));
            }
        }
    }
    pages
}

/// Record `code -> name` pairs of an `expandtemplates` response for `pending`.
pub fn read_template_expansion(
    payload: &Value,
    pending: &[String],
    cache: &mut HashMap<String, String>,
) {
    let expanded = payload["expandtemplates"]["wikitext"]
        .as_str()
        .unwrap_or("");
    let markers: Vec<_> = EXPANSION_MARKER.captures_iter(expanded).collect();
    for (position, marker) in markers.iter().enumerate() {
        let whole = marker.get(0).expect("group 0 always matches");
        let end = markers
            .get(position + 1)
            .map_or(expanded.len(), |next| next.get(0).expect("group 0").start());
        let Some(code) = marker[1]
            .parse::<usize>()
            .ok()
            .and_then(|index| pending.get(index))
        else {
            continue;
        };
        let name = clean_wikitext(&expanded[whole.end()..end]);
        if !name.is_empty() && name.to_uppercase() != *code {
            cache.insert(code.clone(), name);
        }
    }
}

fn param_text(value: &Value) -> String {
    match value {
        Value::String(text) => text.clone(),
        other => other.to_string(),
    }
}

/// `Retry-After` as whole seconds or an HTTP date.
fn retry_after(value: &str) -> Option<Duration> {
    let value = value.trim();
    if is_ascii_digits(value) {
        return value.parse().ok().map(Duration::from_secs);
    }
    let when = chrono::DateTime::parse_from_rfc2822(value).ok()?;
    Some(
        (when.with_timezone(&chrono::Utc) - chrono::Utc::now())
            .to_std()
            .unwrap_or(Duration::ZERO),
    )
}

/// One MediaWiki API call (always `format=json&maxlag=5` first, then `params`
/// in order), retried: up to 4 attempts on `maxlag` errors and
/// HTTP 429/503 with `Retry-After` (seconds or 2^attempt, capped at 5 s).
async fn api_json(client: &HttpClient, params: &[(&str, String)]) -> Result<Value, SourceError> {
    let mut query: Vec<(&str, String)> =
        vec![("format", "json".to_owned()), ("maxlag", "5".to_owned())];
    query.extend(params.iter().cloned());
    let request = Request::get(url_with_query(WIKIPEDIA_API_URL, &query))
        .headers(HEADERS)
        .timeout(REQUEST_TIMEOUT);
    for attempt in 0..API_ATTEMPTS {
        let last = attempt + 1 == API_ATTEMPTS;
        let response = client.execute(&request, &RetryPolicy::NONE).await?;
        if response.status == 200 {
            let payload: Value = response.json().map_err(|error| {
                SourceError::Unavailable(format!("Wikipedia API invalid JSON: {error}"))
            })?;
            match payload
                .get("error")
                .filter(|error| crate::lenient::is_truthy(error))
            {
                None => return Ok(payload),
                Some(error) if error["code"] != "maxlag" || last => {
                    return Err(SourceError::Unavailable(format!(
                        "Wikipedia API error {}: {}",
                        error["code"].as_str().unwrap_or("None"),
                        error["info"].as_str().unwrap_or("None")
                    )));
                }
                Some(_) => {}
            }
        } else if !matches!(response.status, 429 | 503) || last {
            return Err(SourceError::Unavailable(format!(
                "Wikipedia API returned HTTP {}",
                response.status
            )));
        }
        let delay = response
            .header("retry-after")
            .and_then(retry_after)
            .unwrap_or(Duration::from_secs(1 << attempt))
            .min(MAX_RETRY_DELAY);
        tokio::select! {
            () = tokio::time::sleep(delay) => {}
            () = client.cancel_token().cancelled() => {
                return Err(SourceError::Http(crate::http::HttpError::Cancelled));
            }
        }
    }
    Err(SourceError::Unavailable(
        "Wikipedia API retry limit reached".to_owned(),
    ))
}

/// Every payload of a MediaWiki query, following `continue`.
async fn query_all(
    client: &HttpClient,
    params: &[(&str, String)],
) -> Result<Vec<Value>, SourceError> {
    let mut payloads = Vec::new();
    let mut continuation: Vec<(String, String)> = Vec::new();
    let mut seen: HashSet<Vec<(String, String)>> = HashSet::new();
    loop {
        let mut all: Vec<(&str, String)> = vec![("action", "query".to_owned())];
        all.extend(params.iter().cloned());
        all.extend(
            continuation
                .iter()
                .map(|(key, value)| (key.as_str(), value.clone())),
        );
        let payload = api_json(client, &all).await?;
        let next: Vec<(String, String)> = payload
            .get("continue")
            .and_then(Value::as_object)
            .map(|map| {
                map.iter()
                    .map(|(key, value)| (key.clone(), param_text(value)))
                    .collect()
            })
            .unwrap_or_default();
        payloads.push(payload);
        if next.is_empty() {
            return Ok(payloads);
        }
        let mut marker = next.clone();
        marker.sort();
        if !seen.insert(marker) {
            return Err(SourceError::Unavailable(format!(
                "Wikipedia query repeated continuation {next:?}"
            )));
        }
        continuation = next;
    }
}

async fn expand_a_league_teams(
    client: &HttpClient,
    codes: &BTreeSet<String>,
    cache: &mut HashMap<String, String>,
) -> Result<(), SourceError> {
    let pending: Vec<String> = codes
        .iter()
        .filter(|code| !code.is_empty() && !cache.contains_key(*code))
        .cloned()
        .collect();
    if pending.is_empty() {
        return Ok(());
    }
    let text = pending
        .iter()
        .enumerate()
        .map(|(index, code)| format!("@@FLDE{index}@@{{{{A-League team|{code}}}}}"))
        .collect::<Vec<_>>()
        .join("\n");
    let payload = api_json(
        client,
        &[
            ("action", "expandtemplates".to_owned()),
            ("prop", "wikitext".to_owned()),
            ("title", "Template:A-League team".to_owned()),
            ("text", text),
        ],
    )
    .await?;
    read_template_expansion(&payload, &pending, cache);
    Ok(())
}

/// Lower bound of the Wikipedia scan: `since`, else the opening of the
/// current window (`None` for `All`).
fn scan_start(since: Option<NaiveDate>, window: Window, today: NaiveDate) -> Option<NaiveDate> {
    if since.is_some() || window == Window::All {
        return since;
    }
    let ymd = |year, month| NaiveDate::from_ymd_opt(year, month, 1).expect("first of month");
    if window == Window::Winter || (window == Window::Auto && today.month() <= 3) {
        Some(ymd(today.year(), 1))
    } else {
        Some(ymd(
            if today.month() >= 6 {
                today.year()
            } else {
                today.year() - 1
            },
            6,
        ))
    }
}

/// Fetch recent confirmed transfers: dated table events and undated club-list
/// corroborators from every men's seasonal page in the window's categories.
///
/// Individual category/page failures are logged and skipped; the result only
/// errors when cancelled.
pub async fn fetch_wikipedia_transfers(
    client: &HttpClient,
    since: Option<&SinceDate>,
    window: Window,
    today: NaiveDate,
) -> Result<Vec<Transfer>, SourceError> {
    let since = match since {
        Some(SinceDate::Date(date)) => Some(*date),
        Some(SinceDate::Text(text)) => parse_iso_date(text),
        None => None,
    };
    let start_date = scan_start(since, window, today);

    let mut a_league_names: HashMap<String, String> = HashMap::new();
    let mut category_payloads = Vec::new();
    for category in category_candidates(today, since, window) {
        let params = [
            ("list", "categorymembers".to_owned()),
            ("cmtitle", category.clone()),
            ("cmnamespace", "0".to_owned()),
            ("cmlimit", "max".to_owned()),
        ];
        match query_all(client, &params).await {
            Ok(payloads) => category_payloads.extend(payloads),
            Err(error) if error.is_cancelled() => return Err(error),
            Err(error) => tracing::warn!(%category, %error, "Wikipedia category unavailable"),
        }
    }

    // Category membership defines coverage. Fetch every men's page through
    // bulk revision requests capped at the API's per-request title limit;
    // women's pages are outside FL26's player pool.
    let page_titles = category_titles(&category_payloads);
    let mut transfers = Vec::new();
    for titles in page_titles.chunks(WIKIPEDIA_TITLES_PER_REQUEST) {
        let params = [
            ("prop", "revisions".to_owned()),
            ("rvprop", "content".to_owned()),
            ("rvslots", "main".to_owned()),
            ("titles", titles.join("|")),
        ];
        let payloads = match query_all(client, &params).await {
            Ok(payloads) => payloads,
            Err(error) if error.is_cancelled() => return Err(error),
            Err(error) => {
                tracing::warn!(pages = titles.len(), %error, "Wikipedia bulk transfer fetch unavailable");
                continue;
            }
        };
        let pages = page_wikitexts(&payloads);
        let codes: BTreeSet<String> = pages
            .iter()
            .flat_map(|(_, wikitext)| a_league_codes(wikitext))
            .collect();
        match expand_a_league_teams(client, &codes, &mut a_league_names).await {
            Ok(()) => {}
            Err(error) if error.is_cancelled() => return Err(error),
            Err(error) => tracing::warn!(%error, "Wikipedia A-League team expansion unavailable"),
        }
        for (title, wikitext) in &pages {
            transfers.extend(parse_wikipedia_transfer_wikitext(
                wikitext,
                title,
                start_date,
                Some(today),
                &a_league_names,
            ));
        }
    }
    tracing::info!(
        found = transfers.len(),
        pages = page_titles.len(),
        "Wikipedia effective transfers"
    );
    Ok(transfers)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn retry_after_reads_seconds_and_http_dates() {
        assert_eq!(retry_after("3"), Some(Duration::from_secs(3)));
        assert_eq!(retry_after(" 0 "), Some(Duration::ZERO));
        assert_eq!(
            retry_after("Wed, 21 Oct 2015 07:28:00 GMT"),
            Some(Duration::ZERO),
            "past dates wait nothing"
        );
        assert_eq!(retry_after("soon"), None);
        let future = (chrono::Utc::now() + chrono::Duration::seconds(90)).to_rfc2822();
        let wait = retry_after(&future).expect("date parses");
        assert!(
            wait > Duration::from_secs(80) && wait <= Duration::from_secs(90),
            "{wait:?}"
        );
    }

    #[test]
    fn page_urls_are_percent_encoded_like_urllib_quote() {
        assert_eq!(
            page_url("A-League Men transfers for 2026\u{2013}27 season"),
            "https://en.wikipedia.org/wiki/A-League_Men_transfers_for_2026%E2%80%9327_season"
        );
        assert_eq!(
            page_url("List (x)/y"),
            "https://en.wikipedia.org/wiki/List_%28x%29/y"
        );
    }

    #[test]
    fn wikitext_cleaning_resolves_templates_links_and_refs() {
        assert_eq!(
            clean_wikitext(
                "{{flagg|cxx|ENG}} {{sortname|Jordan|Henderson}}<ref>{{cite web|url=https://x}}</ref>"
            ),
            "Jordan Henderson"
        );
        assert_eq!(
            clean_wikitext("[[Brentford F.C.|Brentford]] ''(on loan)''"),
            "Brentford (on loan)"
        );
        assert_eq!(
            clean_wikitext("'''{{dts|format=dmy|2026|8|3}}'''"),
            "2026-08-03"
        );
        assert_eq!(clean_wikitext("{{dts|2026|13|40}}"), "");
        assert_eq!(clean_wikitext("{{Loan}}"), "Loan");
        assert_eq!(
            split_top_level("a|[[b|c]]|{{d|e}}|f", "|"),
            ["a", "[[b|c]]", "{{d|e}}", "f"]
        );
    }
}
