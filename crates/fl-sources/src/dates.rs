//! Date parsing and transfer-window ranges, UTC everywhere.
//!
//! One owner for transfer-window ranges, source date-range resolution and
//! ISO/external date parsing. "Today" is always passed in; nothing here reads
//! the local time zone or the clock.
//!
//! An empty window string means `auto` in every path.

use chrono::{DateTime, Datelike, Duration, NaiveDate, NaiveDateTime, NaiveTime, Utc};

#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum DateError {
    #[error("Invalid since_date {0:?}; expected YYYY-MM-DD")]
    InvalidSince(String),
    #[error("Unknown transfer window: {0:?}")]
    UnknownWindow(String),
}

/// Named transfer window selector (`--window`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum Window {
    /// Full effective history (replayed from 2000-01-01, open end).
    #[default]
    Auto,
    /// No cutoff at all.
    All,
    /// June 1 – Sept 30 of the latest summer to open.
    Summer,
    /// Jan 1 – end of Feb of the reference year.
    Winter,
}

impl Window {
    /// Case-insensitive; the empty string is `Auto`.
    pub fn parse(value: &str) -> Result<Self, DateError> {
        match value.to_lowercase().as_str() {
            "" | "auto" => Ok(Self::Auto),
            "all" => Ok(Self::All),
            "summer" => Ok(Self::Summer),
            "winter" => Ok(Self::Winter),
            _ => Err(DateError::UnknownWindow(value.to_owned())),
        }
    }
}

fn ymd(year: i32, month: u32, day: u32) -> NaiveDate {
    NaiveDate::from_ymd_opt(year, month, day).expect("constant calendar date")
}

fn last_day_of_february(year: i32) -> u32 {
    if NaiveDate::from_ymd_opt(year, 2, 29).is_some() {
        29
    } else {
        28
    }
}

/// `(start, end)` of a transfer window; `end == None` is open-ended.
///
/// `Auto` and `All` both start at 2000-01-01 with no end (so a clean rebuild
/// and an incremental run replay the same lifecycle chain).
pub fn get_transfer_window_range(
    window: Window,
    ref_date: NaiveDate,
) -> (NaiveDate, Option<NaiveDate>) {
    match window {
        Window::Auto | Window::All => (ymd(2000, 1, 1), None),
        Window::Summer => {
            let year = if ref_date.month() >= 6 {
                ref_date.year()
            } else {
                ref_date.year() - 1
            };
            (ymd(year, 6, 1), Some(ymd(year, 9, 30)))
        }
        Window::Winter => {
            let year = ref_date.year();
            (
                ymd(year, 1, 1),
                Some(ymd(year, 2, last_day_of_february(year))),
            )
        }
    }
}

/// Opening date of the transfer window before the latest one to open.
///
/// Windows open on Jan 1 (winter) and Jun 1 (summer); during January–May the
/// previous summer window is still inside the lookback.
pub fn previous_window_start(today: NaiveDate) -> NaiveDate {
    if today.month() >= 6 {
        ymd(today.year(), 1, 1)
    } else {
        ymd(today.year() - 1, 6, 1)
    }
}

/// Explicit lower bound for a scrape (`--since`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SinceDate {
    Date(NaiveDate),
    /// ISO date or timestamp text; validated by [`resolve_date_range`].
    Text(String),
}

impl From<NaiveDate> for SinceDate {
    fn from(value: NaiveDate) -> Self {
        Self::Date(value)
    }
}

impl From<&str> for SinceDate {
    fn from(value: &str) -> Self {
        Self::Text(value.to_owned())
    }
}

impl From<String> for SinceDate {
    fn from(value: String) -> Self {
        Self::Text(value)
    }
}

/// Inclusive `(start, end)` bounds of a scrape; `None` start = unbounded.
pub type DateRange = (Option<NaiveDate>, Option<NaiveDate>);

/// Resolve the lower/upper bounds every provider filters with.
///
/// An explicit non-empty `since` wins (end = `today`); otherwise `window`
/// decides, with the end clamped to `today` (events after `today` are never
/// effective yet) and `All` meaning no lower bound.
pub fn resolve_date_range(
    since: Option<&SinceDate>,
    window: Window,
    today: NaiveDate,
) -> Result<DateRange, DateError> {
    match since {
        Some(SinceDate::Date(date)) => return Ok((Some(*date), Some(today))),
        Some(SinceDate::Text(text)) if !text.is_empty() => {
            let parsed =
                parse_iso_date(text).ok_or_else(|| DateError::InvalidSince(text.clone()))?;
            return Ok((Some(parsed), Some(today)));
        }
        _ => {}
    }
    if window == Window::All {
        return Ok((None, Some(today)));
    }
    let (start, window_end) = get_transfer_window_range(window, today);
    Ok((
        Some(start),
        Some(window_end.map_or(today, |end| end.min(today))),
    ))
}

/// Whether a dated event fits an inclusive range; undated events never do.
pub fn date_in_range(
    event: Option<NaiveDate>,
    start: Option<NaiveDate>,
    end: Option<NaiveDate>,
) -> bool {
    match event {
        None => false,
        Some(date) => !(start.is_some_and(|s| date < s) || end.is_some_and(|e| date > e)),
    }
}

/// Exactly `digits` ASCII digits, or 1..=2 digits when `digits == 0`.
fn small_number(text: &str, min_len: usize, max_len: usize) -> Option<u32> {
    if text.len() < min_len || text.len() > max_len || !text.bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    text.parse().ok()
}

/// Parse the date part of an ISO date/timestamp string (`%Y-%m-%d`; month and
/// day may be 1–2 digits). Everything after the first `T` or space is ignored.
pub fn parse_iso_date(text: &str) -> Option<NaiveDate> {
    let head = text.split('T').next()?.split(' ').next()?;
    let mut parts = head.split('-');
    let year = small_number(parts.next()?, 4, 4)?;
    let month = small_number(parts.next()?, 1, 2)?;
    let day = small_number(parts.next()?, 1, 2)?;
    if parts.next().is_some() {
        return None;
    }
    NaiveDate::from_ymd_opt(i32::try_from(year).ok()?, month, day)
}

struct IsoTime {
    time: NaiveTime,
    offset_seconds: Option<i32>,
}

fn parse_iso_time(text: &str) -> Option<IsoTime> {
    // Split a trailing UTC offset (`+HH[:MM[:SS]]`, `-HH...`) from the clock.
    let (clock, offset) = match text.find(['+', '-']) {
        Some(index) => (&text[..index], Some(&text[index..])),
        None => (text, None),
    };
    let offset_seconds = match offset {
        None => None,
        Some(raw) => {
            let sign = if raw.starts_with('-') { -1 } else { 1 };
            let digits: String = raw[1..].chars().filter(|c| *c != ':').collect();
            if !digits.bytes().all(|b| b.is_ascii_digit()) || ![2, 4, 6].contains(&digits.len()) {
                return None;
            }
            let hours: i32 = digits[0..2].parse().ok()?;
            let minutes: i32 = digits.get(2..4).map_or(Some(0), |v| v.parse().ok())?;
            let seconds: i32 = digits.get(4..6).map_or(Some(0), |v| v.parse().ok())?;
            if hours > 23 || minutes > 59 || seconds > 59 {
                return None;
            }
            Some(sign * (hours * 3600 + minutes * 60 + seconds))
        }
    };
    let (whole, fraction) = match clock.split_once(['.', ',']) {
        Some((whole, fraction)) => (whole, Some(fraction)),
        None => (clock, None),
    };
    let fields: Vec<&str> = if whole.contains(':') {
        whole.split(':').collect()
    } else if whole.is_ascii() && matches!(whole.len(), 2 | 4 | 6) {
        (0..whole.len())
            .step_by(2)
            .map(|at| &whole[at..at + 2])
            .collect()
    } else {
        return None;
    };
    if fields.len() > 3 {
        return None;
    }
    let hour = small_number(fields[0], 2, 2)?;
    let minute = fields
        .get(1)
        .map_or(Some(0), |field| small_number(field, 2, 2))?;
    let second = fields
        .get(2)
        .map_or(Some(0), |field| small_number(field, 2, 2))?;
    let micros = match fraction {
        None => 0,
        Some(digits) => {
            if digits.is_empty() || !digits.bytes().all(|b| b.is_ascii_digit()) {
                return None;
            }
            let padded: String = digits.chars().chain("000000".chars()).take(6).collect();
            padded.parse().ok()?
        }
    };
    Some(IsoTime {
        time: NaiveTime::from_hms_micro_opt(hour, minute, second, micros)?,
        offset_seconds,
    })
}

/// Parse an ISO timestamp to UTC (`Z` accepted; a timestamp without an offset
/// is taken as UTC). Falls back to midnight UTC of the date part, so dates
/// with garbage after them still order by day.
pub fn parse_iso_datetime(text: &str) -> Option<DateTime<Utc>> {
    if text.is_empty() {
        return None;
    }
    let value = text.trim().replace('Z', "+00:00");
    if let Some(parsed) = parse_full_iso_datetime(&value) {
        return Some(parsed);
    }
    let date = parse_iso_date(text)?;
    Some(date.and_time(NaiveTime::MIN).and_utc())
}

/// Like [`parse_iso_datetime`] but without the date-only fallback: the whole
/// string must be a valid ISO timestamp (cache `fetched_at` fields).
pub fn parse_strict_iso_datetime(text: &str) -> Option<DateTime<Utc>> {
    parse_full_iso_datetime(&text.trim().replace('Z', "+00:00"))
}

/// ISO-8601 form of a UTC instant: `+00:00` offset, fractional
/// seconds only when non-zero.
pub fn format_utc_iso(instant: DateTime<Utc>) -> String {
    instant.to_rfc3339_opts(chrono::SecondsFormat::AutoSi, false)
}

/// Leading ISO date (`YYYY-MM-DD` or basic `YYYYMMDD`) and the text after it.
fn split_iso_date(value: &str) -> Option<(NaiveDate, &str)> {
    if let Some(head) = value.get(..10)
        && let Ok(date) = NaiveDate::parse_from_str(head, "%Y-%m-%d")
    {
        return Some((date, &value[10..]));
    }
    let head = value.get(..8)?;
    if !head.bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    Some((NaiveDate::parse_from_str(head, "%Y%m%d").ok()?, &value[8..]))
}

fn parse_full_iso_datetime(value: &str) -> Option<DateTime<Utc>> {
    let (date, rest) = split_iso_date(value)?;
    if rest.is_empty() {
        return Some(date.and_time(NaiveTime::MIN).and_utc());
    }
    // Any single separator character (`T`, space, ...).
    let mut chars = rest.chars();
    chars.next()?;
    let parsed = parse_iso_time(chars.as_str())?;
    let naive = NaiveDateTime::new(date, parsed.time);
    let offset = Duration::seconds(i64::from(parsed.offset_seconds.unwrap_or(0)));
    Some((naive - offset).and_utc())
}

const EXTERNAL_FORMATS: [&str; 7] = [
    "%d.%m.%Y",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%d %B %Y",
    "%d %b %Y",
    "%B %d, %Y",
    "%b %d, %Y",
];

/// Parse common HTML/API date text: ISO forms (date part only, the offset is
/// not applied), dotted/slashed day-first dates, and English month names, with
/// ordinal suffixes (`5th`) and HTML entities tolerated.
pub fn parse_external_date(value: &str) -> Option<NaiveDate> {
    let squeezed = value.split_whitespace().collect::<Vec<_>>().join(" ");
    let decoded = html_escape::decode_html_entities(&squeezed);
    let clean = strip_ordinals(decoded.trim());
    if clean.is_empty() {
        return None;
    }
    if let Some(date) = iso_date_part(&clean) {
        return Some(date);
    }
    EXTERNAL_FORMATS
        .iter()
        .find_map(|format| parse_with_format(&clean, format))
}

const MONTH_NAMES: [&str; 12] = [
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

/// Case-insensitive month name (`abbreviated` = 3-letter form) at the start
/// of `text`: `(month, rest)`.
fn match_month_name(text: &str, abbreviated: bool) -> Option<(u32, &str)> {
    MONTH_NAMES.iter().zip(1u32..).find_map(|(name, month)| {
        let name = if abbreviated { &name[..3] } else { name };
        let head = text.get(..name.len())?;
        head.eq_ignore_ascii_case(name)
            .then(|| (month, &text[name.len()..]))
    })
}

/// `%d` / `%m`: a two-digit value in `1..=maximum`, else one digit `1..=9`
/// (no backtracking needed: a literal always follows).
fn match_number(text: &str, maximum: u32) -> Option<(u32, &str)> {
    let digits = |count: usize| {
        text.get(..count)
            .filter(|head| head.bytes().all(|b| b.is_ascii_digit()))
            .and_then(|head| head.parse::<u32>().ok())
    };
    if let Some(value) = digits(2).filter(|v| (1..=maximum).contains(v)) {
        return Some((value, &text[2..]));
    }
    digits(1)
        .filter(|v| *v >= 1)
        .map(|value| (value, &text[1..]))
}

/// Parse `text` against a `%`-directive `format` over the directives
/// `%d %m %Y %B %b` and literals; a space matches any whitespace run, the
/// whole text must match and the date must exist.
fn parse_with_format(text: &str, format: &str) -> Option<NaiveDate> {
    let (mut day, mut month, mut year) = (None, None, None);
    let mut rest = text;
    let mut directives = format.chars();
    while let Some(token) = directives.next() {
        match token {
            '%' => match directives.next()? {
                'd' => {
                    let (value, tail) = match_number(rest, 31)?;
                    (day, rest) = (Some(value), tail);
                }
                'm' => {
                    let (value, tail) = match_number(rest, 12)?;
                    (month, rest) = (Some(value), tail);
                }
                'Y' => {
                    let head = rest
                        .get(..4)
                        .filter(|h| h.bytes().all(|b| b.is_ascii_digit()))?;
                    (year, rest) = (Some(head.parse::<i32>().ok()?), &rest[4..]);
                }
                name @ ('B' | 'b') => {
                    let (value, tail) = match_month_name(rest, name == 'b')?;
                    (month, rest) = (Some(value), tail);
                }
                _ => return None,
            },
            ' ' => {
                let trimmed = rest.trim_start();
                if trimmed.len() == rest.len() {
                    return None;
                }
                rest = trimmed;
            }
            literal => rest = rest.strip_prefix(literal)?,
        }
    }
    if !rest.is_empty() {
        return None;
    }
    NaiveDate::from_ymd_opt(year?, month?, day?)
}

/// The calendar date written at the start of an ISO date/timestamp, regardless of offset.
fn iso_date_part(text: &str) -> Option<NaiveDate> {
    let value = text.replace('Z', "+00:00");
    let (date, rest) = split_iso_date(&value)?;
    if rest.is_empty() {
        return Some(date);
    }
    let mut chars = rest.chars();
    chars.next()?;
    parse_iso_time(chars.as_str()).map(|_| date)
}

/// Remove `st|nd|rd|th` (any case) directly after a digit and at a word end.
fn strip_ordinals(text: &str) -> String {
    let bytes = text.as_bytes();
    let mut out = String::with_capacity(text.len());
    let mut index = 0;
    while index < text.len() {
        let ch = text[index..]
            .chars()
            .next()
            .expect("index at char boundary");
        if index > 0
            && bytes[index - 1].is_ascii_digit()
            && let Some(suffix) = text.get(index..index + 2)
            && matches!(
                suffix.to_ascii_lowercase().as_str(),
                "st" | "nd" | "rd" | "th"
            )
            && text[index + 2..]
                .chars()
                .next()
                .is_none_or(|c| !(c.is_alphanumeric() || c == '_'))
        {
            index += 2;
            continue;
        }
        out.push(ch);
        index += ch.len_utf8();
    }
    out
}

/// Calendar date (UTC) of a Unix timestamp in seconds or milliseconds
/// (values above 1e12 are milliseconds).
pub fn parse_external_timestamp(value: f64) -> Option<NaiveDate> {
    if !value.is_finite() {
        return None;
    }
    let seconds = if value > 1_000_000_000_000.0 {
        value / 1000.0
    } else {
        value
    };
    let whole = seconds.floor();
    DateTime::from_timestamp(whole as i64, ((seconds - whole) * 1e9) as u32)
        .map(|d| d.date_naive())
        // Only years 1..=9999 are representable.
        .filter(|d| (1..=9999).contains(&d.year()))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn d(y: i32, m: u32, day: u32) -> NaiveDate {
        NaiveDate::from_ymd_opt(y, m, day).unwrap()
    }

    #[test]
    fn windows() {
        assert_eq!(
            get_transfer_window_range(Window::Summer, d(2026, 7, 4)),
            (d(2026, 6, 1), Some(d(2026, 9, 30)))
        );
        assert_eq!(
            get_transfer_window_range(Window::Summer, d(2026, 3, 4)),
            (d(2025, 6, 1), Some(d(2025, 9, 30)))
        );
        assert_eq!(
            get_transfer_window_range(Window::Winter, d(2028, 3, 4)),
            (d(2028, 1, 1), Some(d(2028, 2, 29)))
        );
        assert_eq!(
            get_transfer_window_range(Window::Winter, d(2027, 3, 4)),
            (d(2027, 1, 1), Some(d(2027, 2, 28)))
        );
        assert_eq!(
            get_transfer_window_range(Window::Auto, d(2027, 3, 4)),
            (d(2000, 1, 1), None)
        );
        assert_eq!(Window::parse("").unwrap(), Window::Auto);
        assert_eq!(Window::parse("SUMMER").unwrap(), Window::Summer);
        assert!(Window::parse("spring").is_err());
    }

    #[test]
    fn previous_window() {
        assert_eq!(previous_window_start(d(2026, 6, 1)), d(2026, 1, 1));
        assert_eq!(previous_window_start(d(2026, 5, 31)), d(2025, 6, 1));
        assert_eq!(previous_window_start(d(2026, 1, 1)), d(2025, 6, 1));
    }

    #[test]
    fn resolve_range() {
        let today = d(2026, 8, 10);
        assert_eq!(
            resolve_date_range(None, Window::Auto, today).unwrap(),
            (Some(d(2000, 1, 1)), Some(today))
        );
        assert_eq!(
            resolve_date_range(None, Window::All, today).unwrap(),
            (None, Some(today))
        );
        assert_eq!(
            resolve_date_range(None, Window::Summer, today).unwrap(),
            (Some(d(2026, 6, 1)), Some(d(2026, 8, 10)))
        );
        assert_eq!(
            resolve_date_range(Some(&"2026-08-01T10:00:00Z".into()), Window::Winter, today)
                .unwrap(),
            (Some(d(2026, 8, 1)), Some(today))
        );
        assert_eq!(
            resolve_date_range(Some(&SinceDate::Text(String::new())), Window::All, today).unwrap(),
            (None, Some(today))
        );
        assert_eq!(
            resolve_date_range(Some(&"nope".into()), Window::Auto, today),
            Err(DateError::InvalidSince("nope".into()))
        );
    }

    #[test]
    fn iso_date() {
        assert_eq!(
            parse_iso_date("2026-08-01T10:00:00.000Z"),
            Some(d(2026, 8, 1))
        );
        assert_eq!(parse_iso_date("2026-8-1 junk"), Some(d(2026, 8, 1)));
        assert_eq!(parse_iso_date("2026-02-30"), None);
        assert_eq!(parse_iso_date("26-02-03"), None);
        assert_eq!(parse_iso_date(" 2026-02-03"), None);
        assert_eq!(parse_iso_date(""), None);
    }

    #[test]
    fn iso_datetime() {
        let dt = parse_iso_datetime("2026-08-01T10:00:00.5+02:00").unwrap();
        assert_eq!(dt.to_rfc3339(), "2026-08-01T08:00:00.500+00:00");
        assert_eq!(
            parse_iso_datetime("2026-08-01T10:00:00Z")
                .unwrap()
                .to_rfc3339(),
            "2026-08-01T10:00:00+00:00"
        );
        assert_eq!(
            parse_iso_datetime("2026-08-01").unwrap().to_rfc3339(),
            "2026-08-01T00:00:00+00:00"
        );
        assert_eq!(
            parse_iso_datetime("2026-08-01 garbage")
                .unwrap()
                .to_rfc3339(),
            "2026-08-01T00:00:00+00:00"
        );
        assert!(parse_iso_datetime("garbage").is_none());
    }

    #[test]
    fn external_dates() {
        assert_eq!(parse_external_date("5th March 2026"), Some(d(2026, 3, 5)));
        assert_eq!(parse_external_date("March 5, 2026"), Some(d(2026, 3, 5)));
        assert_eq!(parse_external_date("05.03.2026"), Some(d(2026, 3, 5)));
        assert_eq!(
            parse_external_date("2026-03-05T23:00:00-05:00"),
            Some(d(2026, 3, 5))
        );
        assert_eq!(parse_external_date("garbage"), None);
        assert_eq!(parse_external_date("   "), None);
    }

    #[test]
    fn timestamps() {
        assert_eq!(
            parse_external_timestamp(1_785_000_000.0),
            parse_external_timestamp(1_785_000_000_000.0)
        );
        assert_eq!(parse_external_timestamp(0.0), Some(d(1970, 1, 1)));
        assert_eq!(parse_external_timestamp(f64::NAN), None);
    }

    #[test]
    fn range_membership() {
        assert!(!date_in_range(None, None, None));
        assert!(date_in_range(
            Some(d(2026, 1, 2)),
            Some(d(2026, 1, 2)),
            Some(d(2026, 1, 2))
        ));
        assert!(!date_in_range(
            Some(d(2026, 1, 3)),
            None,
            Some(d(2026, 1, 2))
        ));
    }
}
