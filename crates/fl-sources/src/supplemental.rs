//! Shared plumbing for the supplemental transfer sources (Transfermarkt,
//! Wikipedia, Soccerway, Sofascore, BeSoccer, Sortitoutsi): one error type,
//! the Jina reader URL wrapper, the text and date helpers they all share, and
//! a query-string encoder whose URLs match the recorded cassettes exactly.

use std::sync::LazyLock;

use chrono::NaiveDate;
use regex::Regex;
use serde_json::Value;

use crate::dates::DateError;
use crate::http::HttpError;
use crate::lenient::{display_str, is_truthy};

/// Why a supplemental source produced nothing. Every supplemental source is
/// best-effort: callers log the error and continue with an empty batch.
#[derive(Debug, thiserror::Error)]
pub enum SourceError {
    #[error(transparent)]
    Http(#[from] HttpError),
    #[error(transparent)]
    Date(#[from] DateError),
    /// The upstream answered but not with what the adapter understands
    /// (schema drift, blocked client, empty page).
    #[error("{0}")]
    Unavailable(String),
}

impl SourceError {
    /// Whether the run was cancelled (callers should stop, not just skip).
    pub fn is_cancelled(&self) -> bool {
        matches!(self, Self::Http(HttpError::Cancelled))
    }
}

/// Prefix of the anonymous Jina reader that turns pages into markdown.
pub const READER_PREFIX: &str = "https://r.jina.ai/";

/// `https://r.jina.ai/<url>`.
pub fn reader_url(url: &str) -> String {
    format!("{READER_PREFIX}{url}")
}

/// Collapses every whitespace run to a single space and trims the ends.
pub fn clean(value: &str) -> String {
    value.split_whitespace().collect::<Vec<_>>().join(" ")
}

/// First truthy of two optional JSON values, else null.
pub fn or_value<'a>(first: Option<&'a Value>, second: Option<&'a Value>) -> &'a Value {
    static NULL: Value = Value::Null;
    match first {
        Some(value) if is_truthy(value) => value,
        _ => second.unwrap_or(&NULL),
    }
}

/// `""` for null, else the whitespace-squeezed display text of the value.
pub fn clean_value(value: &Value) -> String {
    match value {
        Value::Null => String::new(),
        other => clean(&display_str(other)),
    }
}

/// Whole-string ASCII digits (non-empty).
pub fn is_ascii_digits(text: &str) -> bool {
    !text.is_empty() && text.bytes().all(|byte| byte.is_ascii_digit())
}

/// Order-preserving dedupe.
pub fn dedupe_preserving_order(items: impl IntoIterator<Item = String>) -> Vec<String> {
    let mut seen = std::collections::HashSet::new();
    items
        .into_iter()
        .filter(|item| seen.insert(item.clone()))
        .collect()
}

const FULL_MONTHS: [&str; 12] = [
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
];
const SHORT_MONTHS: [&str; 12] = [
    "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec",
];

/// 1-based month number of an English month name; `full` selects full names
/// (`August`) over three-letter abbreviations (`Aug`).
pub fn english_month(name: &str, full: bool) -> Option<u32> {
    let lowered = name.to_lowercase();
    let table = if full { &FULL_MONTHS } else { &SHORT_MONTHS };
    table
        .iter()
        .position(|month| *month == lowered)
        .map(|index| index as u32 + 1)
}

/// Day of month: one or two digits, 1..=31 (no `00`).
fn parse_day_digits(text: &str) -> Option<u32> {
    if !(1..=2).contains(&text.len()) || !is_ascii_digits(text) {
        return None;
    }
    text.parse().ok().filter(|day| (1..=31).contains(day))
}

/// Month number: one or two digits, 1..=12.
fn parse_month_digits(text: &str) -> Option<u32> {
    if !(1..=2).contains(&text.len()) || !is_ascii_digits(text) {
        return None;
    }
    text.parse().ok().filter(|month| (1..=12).contains(month))
}

/// Four-digit year.
fn parse_year_digits(text: &str) -> Option<i32> {
    if text.len() != 4 || !is_ascii_digits(text) {
        return None;
    }
    text.parse().ok()
}

/// Strip `st|nd|rd|th` that directly follows a digit and ends a word.
pub fn strip_ordinals(value: &str) -> String {
    static ORDINAL: LazyLock<Regex> =
        LazyLock::new(|| Regex::new(r"(?i)(\d)(?:st|nd|rd|th)\b").expect("ordinal pattern"));
    // `(?<=\d)` lookbehind emulated by re-emitting the captured digit.
    ORDINAL.replace_all(value, "$1").into_owned()
}

/// `DD{sep}MM{sep}YYYY` (day and month may be one digit).
pub fn parse_dmy(text: &str, separator: char) -> Option<NaiveDate> {
    let mut parts = text.split(separator);
    let (day, month, year) = (parts.next()?, parts.next()?, parts.next()?);
    if parts.next().is_some() {
        return None;
    }
    NaiveDate::from_ymd_opt(
        parse_year_digits(year)?,
        parse_month_digits(month)?,
        parse_day_digits(day)?,
    )
}

/// `YYYY-MM-DD` (month and day may be one digit).
pub fn parse_ymd(text: &str) -> Option<NaiveDate> {
    let mut parts = text.split('-');
    let (year, month, day) = (parts.next()?, parts.next()?, parts.next()?);
    if parts.next().is_some() {
        return None;
    }
    NaiveDate::from_ymd_opt(
        parse_year_digits(year)?,
        parse_month_digits(month)?,
        parse_day_digits(day)?,
    )
}

/// `D Month YYYY` (full month name) or `D Mon YYYY` (abbreviation).
pub fn parse_day_month_year(text: &str, full_month: bool) -> Option<NaiveDate> {
    let mut parts = text.split(' ');
    let (day, month, year) = (parts.next()?, parts.next()?, parts.next()?);
    if parts.next().is_some() {
        return None;
    }
    NaiveDate::from_ymd_opt(
        parse_year_digits(year)?,
        english_month(month, full_month)?,
        parse_day_digits(day)?,
    )
}

/// `Month D, YYYY` (full month name).
pub fn parse_month_day_year(text: &str) -> Option<NaiveDate> {
    let (month, rest) = text.split_once(' ')?;
    let (day, year) = rest.split_once(", ")?;
    NaiveDate::from_ymd_opt(
        parse_year_digits(year)?,
        english_month(month, true)?,
        parse_day_digits(day)?,
    )
}

/// Percent-encode a query component the way the recorded cassettes were
/// requested: unreserved characters and `/:?@!$'()*,` stay, space becomes `+`,
/// everything else is `%XX` over the UTF-8 bytes.
fn encode_query_component(value: &str, out: &mut String) {
    for byte in value.bytes() {
        match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'.' | b'_' | b'~' => {
                out.push(byte as char)
            }
            b'/' | b':' | b'?' | b'@' | b'!' | b'$' | b'\'' | b'(' | b')' | b'*' | b',' => {
                out.push(byte as char)
            }
            b' ' => out.push('+'),
            other => {
                out.push('%');
                out.push_str(&format!("{other:02X}"));
            }
        }
    }
}

/// `base?k1=v1&k2=v2` (parameter order preserved).
pub fn url_with_query<K, V>(base: &str, params: &[(K, V)]) -> String
where
    K: AsRef<str>,
    V: AsRef<str>,
{
    let mut url = String::from(base);
    for (index, (key, value)) in params.iter().enumerate() {
        url.push(if index == 0 { '?' } else { '&' });
        encode_query_component(key.as_ref(), &mut url);
        url.push('=');
        encode_query_component(value.as_ref(), &mut url);
    }
    url
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn date_parsers_accept_documented_shapes() {
        let date = |y, m, d| NaiveDate::from_ymd_opt(y, m, d);
        assert_eq!(parse_dmy("01/08/2026", '/'), date(2026, 8, 1));
        assert_eq!(parse_dmy("1/8/2026", '/'), date(2026, 8, 1));
        assert_eq!(parse_dmy("32/08/2026", '/'), None);
        assert_eq!(parse_dmy("00/08/2026", '/'), None);
        assert_eq!(parse_ymd("2026-08-01"), date(2026, 8, 1));
        assert_eq!(
            parse_day_month_year("3 August 2026", true),
            date(2026, 8, 3)
        );
        assert_eq!(parse_day_month_year("3 Aug 2026", false), date(2026, 8, 3));
        assert_eq!(parse_day_month_year("3 Aug 2026", true), None);
        assert_eq!(parse_month_day_year("August 3, 2026"), date(2026, 8, 3));
        assert_eq!(
            strip_ordinals("1st August 2026 and 22nd"),
            "1 August 2026 and 22"
        );
    }

    #[test]
    fn query_encoding_keeps_reserved_characters_and_plus_for_space() {
        let url = url_with_query(
            "https://x.org/w",
            &[
                (
                    "cmtitle",
                    "Category:Football transfers winter 2025\u{2013}26",
                ),
                ("titles", "A|B C&D=E+F;G"),
            ],
        );
        assert_eq!(
            url,
            "https://x.org/w?cmtitle=Category:Football+transfers+winter+2025%E2%80%9326&titles=A%7CB+C%26D%3DE%2BF%3BG"
        );
    }
}
