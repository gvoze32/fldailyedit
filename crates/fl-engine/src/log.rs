//! Structured save-change audit log (`transfer_log.jsonl`).
//!
//! Records are appended one JSON object per line, with `, ` and `: ` separators,
//! raw non-ASCII characters and shortest round-trip floats; this keeps the
//! established log format.

use std::fs::{self, OpenOptions};
use std::io::{self, Write};
use std::path::PathBuf;

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use serde_json::ser::Formatter;

/// Transfer types of removed features; lines carrying them are dropped on read.
const REMOVED_TRANSFER_TYPES: [&str; 2] = ["player_spec_create", "player_spec_update"];
const SHIRT_UPDATE_TYPES: [&str; 2] = ["shirt_number_update", "squad_update"];
const CAPTAIN_UPDATE_TYPE: &str = "captain_update";

/// Errors raised while appending to the audit log.
#[derive(Debug, thiserror::Error)]
pub enum LogError {
    #[error("transfer log I/O error: {0}")]
    Io(#[from] io::Error),
    #[error("transfer log serialization error: {0}")]
    Json(#[from] serde_json::Error),
}

fn empty_object() -> Value {
    Value::Object(serde_json::Map::new())
}

/// One audit line. Fields serialize in declaration order.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct LogRecord {
    #[serde(default)]
    pub timestamp: String,
    #[serde(default)]
    pub player_name: String,
    #[serde(default)]
    pub player_id: u32,
    #[serde(default)]
    pub from_team: String,
    #[serde(default)]
    pub from_team_id: u32,
    #[serde(default)]
    pub to_team: String,
    #[serde(default)]
    pub to_team_id: u32,
    #[serde(default)]
    pub confidence: f64,
    #[serde(default)]
    pub transfer_type: String,
    #[serde(default)]
    pub position: String,
    #[serde(default)]
    pub fee: String,
    #[serde(default)]
    pub market_value: i64,
    #[serde(default)]
    pub transfer_date: String,
    #[serde(default)]
    pub dry_run: bool,
    #[serde(default)]
    pub previous_shirt_number: Option<u32>,
    #[serde(default)]
    pub shirt_number: Option<u32>,
    #[serde(default)]
    pub roster_action: String,
    #[serde(default)]
    pub save_scope: String,
    #[serde(default)]
    pub fotmob_player_id: Option<i64>,
    #[serde(default)]
    pub sortitoutsi_player_id: Option<i64>,
    #[serde(default)]
    pub transfermarkt_player_id: Option<i64>,
    #[serde(default)]
    pub transfermarkt_from_club_id: Option<i64>,
    #[serde(default)]
    pub transfermarkt_to_club_id: Option<i64>,
    #[serde(default)]
    pub transfermarkt_transfer_id: Option<i64>,
    #[serde(default)]
    pub sources: Vec<String>,
    #[serde(default)]
    pub source_urls: Vec<String>,
    #[serde(default)]
    pub proof_urls: Vec<String>,
    #[serde(default = "empty_object")]
    pub native_metadata: Value,
}

impl LogRecord {
    /// Rounds to one decimal on the exact binary value, ties to even.
    /// Non-finite values are returned unchanged.
    pub fn round_confidence(confidence: f64) -> f64 {
        if !confidence.is_finite() {
            return confidence;
        }
        // Rust's exact-precision float formatting rounds half to even on the
        // exact binary expansion.
        format!("{confidence:.1}").parse().unwrap_or(confidence)
    }

    /// Records removed from the product (always dropped on read).
    fn is_removed_feature(&self) -> bool {
        let kind = self.transfer_type.trim().to_lowercase();
        REMOVED_TRANSFER_TYPES.contains(&kind.as_str())
    }

    /// Current and legacy shirt-number audit records.
    pub fn is_shirt_number_update(&self) -> bool {
        SHIRT_UPDATE_TYPES.contains(&self.transfer_type.as_str())
    }

    /// Captain-role audit records.
    pub fn is_captain_update(&self) -> bool {
        self.transfer_type == CAPTAIN_UPDATE_TYPE
    }

    /// Roster releases (rendered in their own report section).
    pub fn is_roster_release(&self) -> bool {
        let action = self.roster_action.trim().to_lowercase();
        let kind = self.transfer_type.trim().to_lowercase();
        action == "release" || kind == "release" || kind == "released"
    }

    /// `(native_metadata or {}).get("previous_captain_player_id")`.
    pub(crate) fn previous_captain_player_id(&self) -> Option<&Value> {
        self.native_metadata.get("previous_captain_player_id")
    }
}

/// Append-only JSONL audit log.
#[derive(Debug, Clone)]
pub struct TransferLog {
    path: PathBuf,
}

impl TransferLog {
    pub fn new(path: impl Into<PathBuf>) -> Self {
        Self { path: path.into() }
    }

    /// Appends all records in ONE write while holding an exclusive advisory
    /// lock on the `<name>.lock` sidecar, so concurrent processes never
    /// interleave lines. Parent directories are created. An empty slice is a
    /// no-op.
    pub fn append(&self, records: &[LogRecord]) -> Result<(), LogError> {
        if records.is_empty() {
            return Ok(());
        }
        let mut buffer = String::new();
        for record in records {
            buffer.push_str(&to_json_line(record)?);
            buffer.push('\n');
        }
        if let Some(parent) = self.path.parent() {
            fs::create_dir_all(parent)?;
        }
        let lock = OpenOptions::new()
            .create(true)
            .append(true)
            .open(self.lock_path())?;
        lock.lock()?;
        let mut file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&self.path)?;
        file.write_all(buffer.as_bytes())?;
        file.flush()?;
        // Dropping `lock` releases the advisory lock.
        drop(lock);
        Ok(())
    }

    fn lock_path(&self) -> PathBuf {
        let mut name = self.path.file_name().unwrap_or_default().to_os_string();
        name.push(".lock");
        self.path.with_file_name(name)
    }

    /// Reads audit records, optionally isolated to one output save. Missing or
    /// unreadable file → empty; blank, malformed and undeserializable lines
    /// and removed-feature records are skipped. `save_scope = None` keeps
    /// everything; `Some(scope)` keeps matching records plus, with
    /// `include_legacy`, records without a scope.
    pub fn read(&self, save_scope: Option<&str>, include_legacy: bool) -> Vec<LogRecord> {
        let Ok(bytes) = fs::read(&self.path) else {
            return Vec::new();
        };
        let mut entries = Vec::new();
        for raw in bytes.split(|b| *b == b'\n') {
            let Ok(line) = std::str::from_utf8(raw) else {
                continue;
            };
            let line = line.trim();
            if line.is_empty() {
                continue;
            }
            let Ok(record) = serde_json::from_str::<LogRecord>(line) else {
                continue;
            };
            if record.is_removed_feature() {
                continue;
            }
            let keep = match save_scope {
                None => true,
                Some(scope) => {
                    record.save_scope == scope || (include_legacy && record.save_scope.is_empty())
                }
            };
            if keep {
                entries.push(record);
            }
        }
        entries
    }

    /// The lines printed for the last `last_n` entries. `last_n == 0` keeps
    /// every entry.
    pub fn summary_lines(&self, last_n: usize) -> Vec<String> {
        let entries = self.read(None, false);
        if entries.is_empty() {
            return vec!["No save changes logged yet.".to_owned()];
        }
        let start = if last_n == 0 {
            0
        } else {
            entries.len().saturating_sub(last_n)
        };
        entries[start..].iter().map(summary_line).collect()
    }
}

fn summary_line(entry: &LogRecord) -> String {
    let dry = if entry.dry_run { " [DRY-RUN]" } else { "" };
    let timestamp: String = entry
        .timestamp
        .chars()
        .take(19)
        .map(|c| if c == 'T' { ' ' } else { c })
        .collect();
    let confidence = fmt_percent0(entry.confidence);
    if entry.is_captain_update() {
        let previous = entry
            .previous_captain_player_id()
            .map_or_else(|| "?".to_owned(), display_value);
        format!(
            "  {timestamp}  {} captain for {}: {previous} → {} (conf={confidence}%){dry}",
            entry.player_name, entry.to_team, entry.player_id
        )
    } else if entry.is_shirt_number_update() {
        format!(
            "  {timestamp}  {} at {}: #{} → #{} (conf={confidence}%){dry}",
            entry.player_name,
            entry.to_team,
            display_opt(entry.previous_shirt_number),
            display_opt(entry.shirt_number),
        )
    } else {
        format!(
            "  {timestamp}  {}: {} → {} (conf={confidence}%){dry}",
            entry.player_name, entry.from_team, entry.to_team
        )
    }
}

/// Display text of an optional int (`None` prints as `None`).
fn display_opt(value: Option<u32>) -> String {
    value.map_or_else(|| "None".to_owned(), |n| n.to_string())
}

/// ISO-8601 timestamp of a UTC datetime: `+00:00` offset, with six fractional
/// digits only when the microseconds are non-zero.
pub fn isoformat_utc(t: DateTime<Utc>) -> String {
    let micros = t.timestamp_subsec_micros();
    let base = t.format("%Y-%m-%dT%H:%M:%S");
    if micros == 0 {
        format!("{base}+00:00")
    } else {
        format!("{base}.{micros:06}+00:00")
    }
}

/// Formats with no decimals. Rust's exact-precision formatting rounds half to
/// even on the exact binary value (86.5 → "86", 87.5 → "88", 0.5 → "0").
pub(crate) fn fmt_percent0(value: f64) -> String {
    format!("{value:.0}")
}

/// Shortest round-trip float text: fixed notation (always with a fractional
/// part) for decimal exponents in `-4..16`, otherwise scientific with a signed
/// two-digit exponent (`1e-05`, `1.5e+16`); `nan`, `inf` and `-inf` for
/// non-finite values.
pub(crate) fn shortest_float_repr(value: f64) -> String {
    if value.is_nan() {
        return "nan".to_owned();
    }
    if value.is_infinite() {
        return if value < 0.0 { "-inf" } else { "inf" }.to_owned();
    }
    let scientific = format!("{value:e}");
    let Some((mantissa, exponent)) = scientific.split_once('e') else {
        return scientific;
    };
    let Ok(exponent) = exponent.parse::<i32>() else {
        return scientific;
    };
    if (-4..16).contains(&exponent) {
        let mut fixed = format!("{value}");
        if !fixed.contains('.') {
            fixed.push_str(".0");
        }
        fixed
    } else {
        let sign = if exponent < 0 { '-' } else { '+' };
        format!("{mantissa}e{sign}{:02}", exponent.abs())
    }
}

/// Truthiness of a JSON value: null, `false`, zero and empty strings, arrays
/// and objects are falsy.
pub(crate) fn is_truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(flag) => *flag,
        Value::Number(number) => number.as_f64().is_none_or(|n| n != 0.0),
        Value::String(text) => !text.is_empty(),
        Value::Array(items) => !items.is_empty(),
        Value::Object(map) => !map.is_empty(),
    }
}

/// Display text of a JSON value: strings verbatim, everything else as
/// [`repr_value`].
pub(crate) fn display_value(value: &Value) -> String {
    match value {
        Value::String(text) => text.clone(),
        other => repr_value(other),
    }
}

/// Debug-style text of a JSON value: `None`/`True`/`False`, quoted strings,
/// `[a, b]` lists and `{'key': value}` maps.
fn repr_value(value: &Value) -> String {
    match value {
        Value::Null => "None".to_owned(),
        Value::Bool(true) => "True".to_owned(),
        Value::Bool(false) => "False".to_owned(),
        Value::Number(number) => number
            .as_i64()
            .map(|n| n.to_string())
            .or_else(|| number.as_u64().map(|n| n.to_string()))
            .or_else(|| number.as_f64().map(shortest_float_repr))
            .unwrap_or_else(|| number.to_string()),
        Value::String(text) => repr_str(text),
        Value::Array(items) => {
            let inner: Vec<String> = items.iter().map(repr_value).collect();
            format!("[{}]", inner.join(", "))
        }
        Value::Object(map) => {
            let inner: Vec<String> = map
                .iter()
                .map(|(key, item)| format!("{}: {}", repr_str(key), repr_value(item)))
                .collect();
            format!("{{{}}}", inner.join(", "))
        }
    }
}

/// Quoted text of a string: single quotes unless the text holds a single quote
/// and no double quote; backslash, `\n`, `\r`, `\t` and other control
/// characters are escaped.
pub(crate) fn repr_str(text: &str) -> String {
    let quote = if text.contains('\'') && !text.contains('"') {
        '"'
    } else {
        '\''
    };
    let mut out = String::with_capacity(text.len() + 2);
    out.push(quote);
    for c in text.chars() {
        match c {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c == quote => {
                out.push('\\');
                out.push(c);
            }
            c if (c as u32) < 0x20 || c as u32 == 0x7f => {
                out.push_str(&format!("\\x{:02x}", c as u32));
            }
            c => out.push(c),
        }
    }
    out.push(quote);
    out
}

/// `serde_json` formatter for the log layout: `", "` / `": "` separators,
/// shortest round-trip floats, raw non-ASCII.
struct LogFormatter;

impl Formatter for LogFormatter {
    fn begin_array_value<W: ?Sized + Write>(
        &mut self,
        writer: &mut W,
        first: bool,
    ) -> io::Result<()> {
        if first {
            Ok(())
        } else {
            writer.write_all(b", ")
        }
    }

    fn begin_object_key<W: ?Sized + Write>(
        &mut self,
        writer: &mut W,
        first: bool,
    ) -> io::Result<()> {
        if first {
            Ok(())
        } else {
            writer.write_all(b", ")
        }
    }

    fn begin_object_value<W: ?Sized + Write>(&mut self, writer: &mut W) -> io::Result<()> {
        writer.write_all(b": ")
    }

    fn write_f64<W: ?Sized + Write>(&mut self, writer: &mut W, value: f64) -> io::Result<()> {
        writer.write_all(shortest_float_repr(value).as_bytes())
    }
}

/// Serializes `value` as one log line in the log layout (no trailing newline).
pub(crate) fn to_json_line<T: Serialize>(value: &T) -> Result<String, serde_json::Error> {
    let mut buffer = Vec::new();
    let mut serializer = serde_json::Serializer::with_formatter(&mut buffer, LogFormatter);
    value.serialize(&mut serializer)?;
    String::from_utf8(buffer)
        .map_err(|error| serde_json::Error::io(io::Error::new(io::ErrorKind::InvalidData, error)))
}

#[cfg(test)]
mod tests {
    use std::sync::Arc;
    use std::thread;

    use chrono::TimeZone;
    use serde_json::json;

    use super::*;

    fn record(name: &str, kind: &str) -> LogRecord {
        LogRecord {
            timestamp: "2026-10-24T22:25:55+00:00".to_owned(),
            player_name: name.to_owned(),
            player_id: 7,
            from_team: "Alpha".to_owned(),
            from_team_id: 1,
            to_team: "Beta".to_owned(),
            to_team_id: 2,
            confidence: 86.5,
            transfer_type: kind.to_owned(),
            position: "ST".to_owned(),
            fee: String::new(),
            market_value: 0,
            transfer_date: String::new(),
            dry_run: false,
            previous_shirt_number: None,
            shirt_number: None,
            roster_action: "move".to_owned(),
            save_scope: "scope".to_owned(),
            fotmob_player_id: None,
            sortitoutsi_player_id: None,
            transfermarkt_player_id: None,
            transfermarkt_from_club_id: None,
            transfermarkt_to_club_id: None,
            transfermarkt_transfer_id: None,
            sources: Vec::new(),
            source_urls: Vec::new(),
            proof_urls: Vec::new(),
            native_metadata: empty_object(),
        }
    }

    #[test]
    fn percent_rounds_half_even() {
        let cases = [
            (86.5, "86"),
            (87.5, "88"),
            (0.5, "0"),
            (1.5, "2"),
            (2.5, "2"),
            (100.0, "100"),
            (-0.5, "-0"),
            (-0.3, "-0"),
            (99.99, "100"),
        ];
        for (value, expected) in cases {
            assert_eq!(fmt_percent0(value), expected, "{value}");
        }
    }

    #[test]
    fn round_confidence_rounds_half_even() {
        let cases = [
            (0.25, 0.2),
            (0.35, 0.3),
            (0.75, 0.8),
            (81.45, 81.5),
            (0.05, 0.1),
            (0.15, 0.1),
            (2.675, 2.7),
            (100.0, 100.0),
            (0.0, 0.0),
        ];
        for (value, expected) in cases {
            assert_eq!(LogRecord::round_confidence(value), expected, "{value}");
        }
        assert!(LogRecord::round_confidence(f64::NAN).is_nan());
    }

    #[test]
    fn float_repr_is_shortest_round_trip() {
        let cases = [
            (0.0, "0.0"),
            (-0.0, "-0.0"),
            (1.0, "1.0"),
            (100.0, "100.0"),
            (81.5, "81.5"),
            (1e-5, "1e-05"),
            (1.5e-7, "1.5e-07"),
            (0.0001, "0.0001"),
            (0.00012, "0.00012"),
            (1e15, "1000000000000000.0"),
            (1e16, "1e+16"),
            (1.5e16, "1.5e+16"),
            (123_456_789_012_345_678.0, "1.2345678901234568e+17"),
            (1e22, "1e+22"),
            (5e-324, "5e-324"),
            (f64::MAX, "1.7976931348623157e+308"),
            (0.1, "0.1"),
            (2.5e-5, "2.5e-05"),
        ];
        for (value, expected) in cases {
            assert_eq!(shortest_float_repr(value), expected, "{value}");
        }
    }

    #[test]
    fn json_line_layout() {
        let value = json!({
            "a": "é\n\t\"\\\u{1}\u{7f}😀",
            "b": [1, 2.5, {"x": null, "y": true}],
            "c": {},
            "d": [],
        });
        assert_eq!(
            to_json_line(&value).unwrap(),
            "{\"a\": \"é\\n\\t\\\"\\\\\\u0001\u{7f}😀\", \"b\": [1, 2.5, {\"x\": null, \"y\": true}], \"c\": {}, \"d\": []}"
        );
        assert_eq!(to_json_line(&json!({"f": 1e-5})).unwrap(), "{\"f\": 1e-05}");
    }

    #[test]
    fn isoformat_utc_format() {
        let whole = Utc.with_ymd_and_hms(2026, 10, 24, 22, 25, 55).unwrap();
        assert_eq!(isoformat_utc(whole), "2026-10-24T22:25:55+00:00");
        let micros = whole + chrono::Duration::microseconds(1234);
        assert_eq!(isoformat_utc(micros), "2026-10-24T22:25:55.001234+00:00");
    }

    #[test]
    fn display_value_of_values() {
        assert_eq!(display_value(&json!(null)), "None");
        assert_eq!(display_value(&json!(true)), "True");
        assert_eq!(display_value(&json!(12)), "12");
        assert_eq!(display_value(&json!(1.0)), "1.0");
        assert_eq!(display_value(&json!("it's")), "it's");
        assert_eq!(
            display_value(&json!(["it's", "a\"b", {"k": [null]}])),
            "[\"it's\", 'a\"b', {'k': [None]}]"
        );
        assert!(!is_truthy(&json!(0.0)));
        assert!(!is_truthy(&json!([])));
        assert!(is_truthy(&json!("0")));
    }

    #[test]
    fn classification_helpers() {
        assert!(record("a", "shirt_number_update").is_shirt_number_update());
        assert!(record("a", "squad_update").is_shirt_number_update());
        assert!(!record("a", " squad_update").is_shirt_number_update());
        assert!(record("a", "captain_update").is_captain_update());
        assert!(record("a", " Released ").is_roster_release());
        let mut moved = record("a", "transfer");
        assert!(!moved.is_roster_release());
        moved.roster_action = " RELEASE".to_owned();
        assert!(moved.is_roster_release());
    }

    #[test]
    fn append_and_read_roundtrip_keeps_log_format() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("state").join("transfer_log.jsonl");
        let log = TransferLog::new(path.clone());
        assert!(log.read(None, false).is_empty());
        log.append(&[]).unwrap();
        assert!(!path.exists(), "empty append creates nothing");

        let mut first = record("Zoë | Ünïcode", "transfer");
        first.confidence = LogRecord::round_confidence(81.45);
        first.sources = vec!["fotmob".to_owned()];
        first.native_metadata =
            json!({"player_bin": {"found": true, "name": "Zoë"}, "z": 1, "a": 2});
        let second = record("Second", "loan");
        log.append(&[first.clone(), second.clone()]).unwrap();

        let text = fs::read_to_string(&path).unwrap();
        let lines: Vec<&str> = text.lines().collect();
        assert_eq!(lines.len(), 2);
        assert!(lines[0].starts_with(
            "{\"timestamp\": \"2026-10-24T22:25:55+00:00\", \"player_name\": \"Zoë | Ünïcode\", \"player_id\": 7, "
        ));
        assert!(lines[0].contains("\"confidence\": 81.5, "));
        assert!(lines[0].ends_with(
            "\"native_metadata\": {\"player_bin\": {\"found\": true, \"name\": \"Zoë\"}, \"z\": 1, \"a\": 2}}"
        ));
        assert_eq!(log.read(None, false), vec![first, second]);
    }

    #[test]
    fn read_skips_garbage_removed_types_and_filters_scope() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("log.jsonl");
        let mut legacy = record("Legacy", "transfer");
        legacy.save_scope = String::new();
        let mut other = record("Other", "transfer");
        other.save_scope = "other".to_owned();
        let mut text = String::new();
        for line in [
            to_json_line(&record("Mine", "transfer")).unwrap(),
            "not json".to_owned(),
            String::new(),
            "   ".to_owned(),
            "[1, 2]".to_owned(),
            "{\"player_id\": \"x\"}".to_owned(),
            to_json_line(&record("Gone", " Player_Spec_Create ")).unwrap(),
            to_json_line(&record("Gone", "player_spec_update")).unwrap(),
            to_json_line(&legacy).unwrap(),
            to_json_line(&other).unwrap(),
            "{\"player_name\": \"Sparse\", \"unknown_key\": 1}".to_owned(),
        ] {
            text.push_str(&line);
            text.push_str("\r\n");
        }
        fs::write(&path, text).unwrap();
        let log = TransferLog::new(&path);
        let names = |records: Vec<LogRecord>| -> Vec<String> {
            records.into_iter().map(|r| r.player_name).collect()
        };
        assert_eq!(
            names(log.read(None, false)),
            ["Mine", "Legacy", "Other", "Sparse"]
        );
        assert_eq!(names(log.read(Some("scope"), false)), ["Mine"]);
        assert_eq!(
            names(log.read(Some("scope"), true)),
            ["Mine", "Legacy", "Sparse"]
        );
        assert_eq!(
            names(log.read(Some("missing"), false)),
            Vec::<String>::new()
        );
        let sparse = log.read(None, false).pop().unwrap();
        assert_eq!(sparse.native_metadata, json!({}));
        assert_eq!(sparse.confidence, 0.0);
    }

    #[test]
    fn summary_lines_format() {
        let dir = tempfile::tempdir().unwrap();
        let log = TransferLog::new(dir.path().join("log.jsonl"));
        assert_eq!(log.summary_lines(20), ["No save changes logged yet."]);

        let mut transfer = record("Zoë", "transfer");
        transfer.dry_run = true;
        let mut shirt = record("Shirt", "shirt_number_update");
        shirt.previous_shirt_number = Some(9);
        shirt.shirt_number = Some(10);
        shirt.confidence = 87.5;
        let mut captain = record("Cap", "captain_update");
        captain.confidence = 0.5;
        captain.native_metadata = json!({"previous_captain_player_id": 42});
        let mut captain_none = record("Cap2", "captain_update");
        captain_none.native_metadata = json!({"previous_captain_player_id": null});
        let captain_missing = record("Cap3", "captain_update");
        log.append(&[transfer, shirt, captain, captain_none, captain_missing])
            .unwrap();

        assert_eq!(
            log.summary_lines(20),
            [
                "  2026-10-24 22:25:55  Zoë: Alpha → Beta (conf=86%) [DRY-RUN]",
                "  2026-10-24 22:25:55  Shirt at Beta: #9 → #10 (conf=88%)",
                "  2026-10-24 22:25:55  Cap captain for Beta: 42 → 7 (conf=0%)",
                "  2026-10-24 22:25:55  Cap2 captain for Beta: None → 7 (conf=86%)",
                "  2026-10-24 22:25:55  Cap3 captain for Beta: ? → 7 (conf=86%)",
            ]
        );
        assert_eq!(log.summary_lines(2).len(), 2);
        assert_eq!(log.summary_lines(0).len(), 5, "last_n == 0 keeps all");
    }

    #[test]
    fn concurrent_appends_keep_lines_intact() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("log.jsonl");
        let log = Arc::new(TransferLog::new(path.clone()));
        let handles: Vec<_> = (0..2)
            .map(|t| {
                let log = Arc::clone(&log);
                thread::spawn(move || {
                    for i in 0..50 {
                        let batch: Vec<LogRecord> = (0..4)
                            .map(|j| {
                                let mut r = record(&format!("t{t}-{i}-{j}"), "transfer");
                                r.proof_urls = vec!["x".repeat(4096)];
                                r
                            })
                            .collect();
                        log.append(&batch).unwrap();
                    }
                })
            })
            .collect();
        for handle in handles {
            handle.join().unwrap();
        }
        let text = fs::read_to_string(&path).unwrap();
        assert_eq!(text.lines().count(), 400);
        for line in text.lines() {
            serde_json::from_str::<LogRecord>(line).expect("intact line");
        }
        assert_eq!(log.read(None, false).len(), 400);
    }
}
