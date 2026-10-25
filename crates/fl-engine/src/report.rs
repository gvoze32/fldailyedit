//! Run reports: `transfer_summary.md`, `transfer_summary.html` and the
//! `skipped_transfers.jsonl` hand-off to the next run.
//!
//! The text layouts (markdown, HTML, JSONL) keep the established report format.

use std::collections::BTreeMap;
use std::fmt::Display;
use std::fs;
use std::io;
use std::path::{Path, PathBuf};

use chrono::{DateTime, Utc};
use fl_fs::write_atomic;
use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::log::{LogRecord, display_value, fmt_percent0, is_truthy, to_json_line};

const REPORT_TITLE: &str = "Football Life Live Sync Report";
const MARKDOWN_FILENAME: &str = "transfer_summary.md";
const HTML_FILENAME: &str = "transfer_summary.html";
/// Name of the skipped-transfer hand-off file inside the output directory.
pub const SKIPPED_TRANSFERS_FILENAME: &str = "skipped_transfers.jsonl";

/// Errors raised while writing the report files.
#[derive(Debug, thiserror::Error)]
pub enum ReportError {
    #[error("report I/O error: {0}")]
    Io(#[from] io::Error),
    #[error("report serialization error: {0}")]
    Json(#[from] serde_json::Error),
}

fn is_false(value: &bool) -> bool {
    !*value
}

/// One skipped row as written to `skipped_transfers.jsonl` (fields serialize in
/// declaration order; `retryable` is only serialized when true).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SkippedRow {
    #[serde(default)]
    pub player_name: String,
    #[serde(default)]
    pub from_team: String,
    #[serde(default)]
    pub to_team: String,
    #[serde(default)]
    pub date: String,
    #[serde(default)]
    pub source: String,
    #[serde(default)]
    pub reason: String,
    #[serde(default)]
    pub detail: String,
    #[serde(default)]
    pub relevant: bool,
    #[serde(default)]
    pub fotmob_player_id: Option<i64>,
    #[serde(default)]
    pub candidates: Vec<String>,
    #[serde(default, skip_serializing_if = "is_false")]
    pub retryable: bool,
    #[serde(default)]
    pub save_scope: String,
}

/// Paths written by [`write_reports`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ReportPaths {
    pub markdown: PathBuf,
    pub html: PathBuf,
    pub skipped: PathBuf,
}

/// Orders skipped rows: save-relevant rows first, then by reason, then by date
/// (stable).
pub fn sort_skipped(rows: &mut [SkippedRow]) {
    rows.sort_by(|a, b| (!a.relevant, &a.reason, &a.date).cmp(&(!b.relevant, &b.reason, &b.date)));
}

/// Reads the previous run's skipped rows; a missing file yields no rows and
/// malformed lines are skipped.
pub fn read_skipped(path: &Path) -> Vec<SkippedRow> {
    let Ok(bytes) = fs::read(path) else {
        return Vec::new();
    };
    bytes
        .split(|b| *b == b'\n')
        .filter_map(|raw| std::str::from_utf8(raw).ok())
        .map(str::trim)
        .filter(|line| !line.is_empty())
        .filter_map(|line| serde_json::from_str::<SkippedRow>(line).ok())
        .collect()
}

/// Writes the markdown report, the HTML report and the skipped-transfer JSONL
/// (rewritten every run, empty when nothing was skipped) into `dir`.
/// Returns the markdown text and the written paths.
pub fn write_reports(
    dir: &Path,
    entries: &[LogRecord],
    skipped: &[SkippedRow],
    generated: DateTime<Utc>,
) -> Result<(String, ReportPaths), ReportError> {
    fs::create_dir_all(dir)?;
    let markdown = render_markdown(entries, skipped, generated);
    let html = render_html(entries, skipped, generated);
    let mut jsonl = String::new();
    for row in skipped {
        jsonl.push_str(&to_json_line(row)?);
        jsonl.push('\n');
    }
    let paths = ReportPaths {
        markdown: dir.join(MARKDOWN_FILENAME),
        html: dir.join(HTML_FILENAME),
        skipped: dir.join(SKIPPED_TRANSFERS_FILENAME),
    };
    write_atomic(&paths.markdown, markdown.as_bytes())?;
    write_atomic(&paths.html, html.as_bytes())?;
    write_atomic(&paths.skipped, jsonl.as_bytes())?;
    Ok((markdown, paths))
}

// ---------------------------------------------------------------------------
// Shared report model
// ---------------------------------------------------------------------------

/// Active entries split into the report sections.
struct Sections<'a> {
    all: Vec<&'a LogRecord>,
    captains: Vec<&'a LogRecord>,
    releases: Vec<&'a LogRecord>,
    club_transfers: Vec<&'a LogRecord>,
    shirts: Vec<&'a LogRecord>,
}

impl<'a> Sections<'a> {
    fn new(entries: &'a [LogRecord]) -> Self {
        let all: Vec<&LogRecord> = entries.iter().filter(|e| !is_removed(e)).collect();
        let captains = all
            .iter()
            .copied()
            .filter(|e| e.is_captain_update())
            .collect();
        let shirts = all
            .iter()
            .copied()
            .filter(|e| e.is_shirt_number_update())
            .collect();
        let transfers: Vec<&LogRecord> = all
            .iter()
            .copied()
            .filter(|e| !e.is_shirt_number_update() && !e.is_captain_update())
            .collect();
        let releases = transfers
            .iter()
            .copied()
            .filter(|e| e.is_roster_release())
            .collect();
        let club_transfers = transfers
            .iter()
            .copied()
            .filter(|e| !e.is_roster_release())
            .collect();
        Self {
            all,
            captains,
            releases,
            club_transfers,
            shirts,
        }
    }

    fn metrics(&self) -> Metrics {
        let loans = self
            .club_transfers
            .iter()
            .filter(|e| e.transfer_type.to_lowercase().contains("loan"))
            .count();
        let club_transfers = self.club_transfers.len();
        Metrics {
            total_changes: self.all.len(),
            club_transfers,
            permanent: club_transfers - loans,
            loans,
            shirt_updates: self.shirts.len(),
            captain_updates: self.captains.len(),
            dry_run: self.all.iter().filter(|e| e.dry_run).count(),
            moves: self.count_club_action("move"),
            signings: self.count_club_action("add"),
            releases: self.releases.len(),
        }
    }

    fn count_club_action(&self, action: &str) -> usize {
        self.club_transfers
            .iter()
            .filter(|e| e.roster_action == action)
            .count()
    }
}

struct Metrics {
    total_changes: usize,
    club_transfers: usize,
    permanent: usize,
    loans: usize,
    shirt_updates: usize,
    captain_updates: usize,
    dry_run: usize,
    moves: usize,
    signings: usize,
    releases: usize,
}

/// Records of removed features never reach a report.
fn is_removed(entry: &LogRecord) -> bool {
    let kind = entry.transfer_type.trim().to_lowercase();
    kind == "player_spec_create" || kind == "player_spec_update"
}

/// Groups skipped rows by reason: save-relevant groups first, then larger
/// groups, then by reason; relevant rows lead each group.
fn skipped_groups(skipped: &[SkippedRow]) -> Vec<(String, Vec<&SkippedRow>)> {
    let mut groups: BTreeMap<String, Vec<&SkippedRow>> = BTreeMap::new();
    for item in skipped {
        let reason = if item.reason.is_empty() {
            "unknown"
        } else {
            item.reason.as_str()
        };
        groups.entry(reason.to_owned()).or_default().push(item);
    }
    let mut groups: Vec<_> = groups.into_iter().collect();
    for (_, items) in &mut groups {
        items.sort_by_key(|item| !item.relevant);
    }
    groups.sort_by(|a, b| {
        let key = |group: &(String, Vec<&SkippedRow>)| {
            (
                !group.1.iter().any(|item| item.relevant),
                std::cmp::Reverse(group.1.len()),
                group.0.clone(),
            )
        };
        key(a).cmp(&key(b))
    });
    groups
}

/// Summary of the native (Player.bin / team) metadata attached to a record.
fn native_metadata_summary(entry: &LogRecord) -> String {
    let empty = serde_json::Map::new();
    let native = entry.native_metadata.as_object().unwrap_or(&empty);
    let mut parts: Vec<String> = Vec::new();
    if let Some(player) = native.get("player_bin").and_then(Value::as_object)
        && !player.is_empty()
    {
        if player.get("found").is_some_and(is_truthy) {
            let identity = ["name", "player_id"]
                .iter()
                .filter_map(|key| player.get(*key))
                .find(|value| is_truthy(value))
                .map_or_else(|| "matched".to_owned(), display_value);
            let mut part = format!("Player.bin: {identity}");
            if let Some(position) = player.get("registered_position").filter(|v| is_truthy(v)) {
                part.push_str(&format!(" ({})", display_value(position)));
            }
            parts.push(part);
        } else {
            parts.push("Player.bin: not found".to_owned());
        }
    }
    let teams = native
        .get("player_assignment")
        .and_then(Value::as_object)
        .and_then(|assignment| assignment.get("teams"))
        .and_then(Value::as_array);
    if let Some(teams) = teams
        && !teams.is_empty()
    {
        let names: Vec<String> = teams.iter().map(team_label).collect();
        parts.push(format!("Assignment: {}", names.join(", ")));
    }
    if parts.is_empty() {
        "-".to_owned()
    } else {
        parts.join("; ")
    }
}

/// Team label: the first non-empty of `abbreviation` / `name`, else `team_key`
/// (`None` when absent).
fn team_label(team: &Value) -> String {
    let field = |key: &str| team.get(key);
    ["abbreviation", "name"]
        .iter()
        .filter_map(|key| field(key))
        .find(|value| is_truthy(value))
        .or_else(|| field("team_key"))
        .map_or_else(|| "None".to_owned(), display_value)
}

fn generated_label(generated: DateTime<Utc>) -> String {
    generated.format("%Y-%m-%d %H:%M UTC").to_string()
}

// ---------------------------------------------------------------------------
// Markdown
// ---------------------------------------------------------------------------

/// Markdown table cell for text: empty → `-`, `|` escaped, newlines flattened.
fn cell(value: &str) -> String {
    let text = if value.is_empty() { "-" } else { value };
    text.replace('|', "\\|").replace('\n', " ")
}

/// Markdown table cell for an optional number.
fn cell_num<T: Display>(value: Option<T>) -> String {
    value.map_or_else(|| "-".to_owned(), |n| cell(&n.to_string()))
}

/// Markdown table cell for an optional JSON value.
fn cell_value(value: Option<&Value>) -> String {
    match value {
        None | Some(Value::Null) => "-".to_owned(),
        Some(other) => cell(&display_value(other)),
    }
}

fn skipped_markdown(skipped: &[SkippedRow]) -> Vec<String> {
    if skipped.is_empty() {
        return Vec::new();
    }
    let relevant = skipped.iter().filter(|item| item.relevant).count();
    let mut md = vec![
        format!("### Not applied ({})", skipped.len()),
        String::new(),
        format!(
            "> {relevant} touch a club in this save. Players missing from the save are never \
             created; they are listed here instead."
        ),
        String::new(),
    ];
    for (reason, items) in skipped_groups(skipped) {
        md.push(format!("#### `{}` ({})", cell(&reason), items.len()));
        md.push(String::new());
        md.push(
            "| Save club | Player | FotMob ID | From | To | Date | Source | Detail |".to_owned(),
        );
        md.push("|:---:|---|---:|---|---|---|---|---|".to_owned());
        for item in items {
            md.push(format!(
                "| {} | **{}** | {} | {} | {} | {} | {} | {} |",
                if item.relevant { "yes" } else { "no" },
                cell(&item.player_name),
                cell_num(item.fotmob_player_id),
                cell(&item.from_team),
                cell(&item.to_team),
                cell(&item.date),
                cell(&item.source),
                cell(&item.detail),
            ));
        }
        md.push(String::new());
    }
    md
}

/// Renders the markdown report: transfer tables for `entries` plus the skipped
/// section.
pub fn render_markdown(
    entries: &[LogRecord],
    skipped: &[SkippedRow],
    generated: DateTime<Utc>,
) -> String {
    let now = generated_label(generated);
    let sections = Sections::new(entries);
    let skipped_lines = skipped_markdown(skipped);
    if sections.all.is_empty() {
        let mut lines = vec![
            format!("## ⚽ {REPORT_TITLE}"),
            String::new(),
            format!("**Generated:** `{now}`"),
            String::new(),
            "> No save changes were needed in this run.".to_owned(),
        ];
        if !skipped_lines.is_empty() {
            lines.push(String::new());
            lines.extend(skipped_lines);
        }
        let joined = lines.join("\n");
        return format!("{}\n", joined.trim_end_matches('\n'));
    }

    let m = sections.metrics();
    let mut md = vec![
        format!("## ⚽ {REPORT_TITLE}"),
        format!("**Generated:** `{now}`"),
        String::new(),
        "### Run overview".to_owned(),
        String::new(),
        "| Save changes | Club transfers | Permanent | Loans / returns | Shirt numbers | Captains | Dry-run |".to_owned(),
        "|---:|---:|---:|---:|---:|---:|---:|".to_owned(),
        format!(
            "| **{}** | **{}** | {} | {} | **{}** | **{}** | {} |",
            m.total_changes, m.club_transfers, m.permanent, m.loans, m.shirt_updates, m.captain_updates, m.dry_run
        ),
        "> Captain updates use the latest verified captain marker from the club's most recent lineup.".to_owned(),
        String::new(),
        "> Shirt-number updates only change kit numbers. They never move a player between clubs.".to_owned(),
        format!(
            "> Roster actions: {} direct moves · {} signings · {} releases.",
            m.moves, m.signings, m.releases
        ),
        String::new(),
    ];

    if !sections.club_transfers.is_empty() {
        md.push(format!(
            "### Club transfers ({})",
            sections.club_transfers.len()
        ));
        md.push(String::new());
        md.push(
            "| Status | Player | Pos | From | To | Deal | Native metadata | Match |".to_owned(),
        );
        md.push("|:---:|---|:---:|---|---|---|---|---:|".to_owned());
        for entry in &sections.club_transfers {
            let status = if entry.dry_run {
                "🧪 Dry-run"
            } else {
                "✅ Applied"
            };
            let deal = if entry.fee.is_empty() {
                &entry.transfer_type
            } else {
                &entry.fee
            };
            md.push(format!(
                "| {status} | **{}** | `{}` | {} | {} | {} | {} | {}% |",
                cell(&entry.player_name),
                cell(&entry.position),
                cell(&entry.from_team),
                cell(&entry.to_team),
                cell(deal),
                cell(&native_metadata_summary(entry)),
                fmt_percent0(entry.confidence),
            ));
        }
        md.push(String::new());
    }

    if !sections.releases.is_empty() {
        md.push(format!("### Player releases ({})", sections.releases.len()));
        md.push(String::new());
        md.push("| Status | Player | Pos | Released from | Native metadata | Match |".to_owned());
        md.push("|:---:|---|:---:|---|---|---:|".to_owned());
        for entry in &sections.releases {
            let status = if entry.dry_run { "Dry-run" } else { "Released" };
            md.push(format!(
                "| {status} | **{}** | `{}` | {} | {} | {}% |",
                cell(&entry.player_name),
                cell(&entry.position),
                cell(&entry.from_team),
                cell(&native_metadata_summary(entry)),
                fmt_percent0(entry.confidence),
            ));
        }
        md.push(String::new());
    }

    if !sections.shirts.is_empty() {
        md.push(format!(
            "### Shirt-number changes ({})",
            sections.shirts.len()
        ));
        md.push(String::new());
        md.push("| Status | Player | Club | Previous | New | Match |".to_owned());
        md.push("|:---:|---|---|---:|---:|---:|".to_owned());
        for entry in &sections.shirts {
            let status = if entry.dry_run {
                "🧪 Dry-run"
            } else {
                "🔢 Updated"
            };
            md.push(format!(
                "| {status} | **{}** | {} | #{} | #{} | {}% |",
                cell(&entry.player_name),
                cell(&entry.to_team),
                cell_num(entry.previous_shirt_number),
                cell_num(entry.shirt_number),
                fmt_percent0(entry.confidence),
            ));
        }
        md.push(String::new());
    }

    if !sections.captains.is_empty() {
        md.push(format!("### Captain changes ({})", sections.captains.len()));
        md.push(String::new());
        md.push("| Status | Club | New captain | Previous player ID | Match |".to_owned());
        md.push("|:---:|---|---|---:|---:|".to_owned());
        for entry in &sections.captains {
            let status = if entry.dry_run { "Dry-run" } else { "Updated" };
            md.push(format!(
                "| {status} | {} | **{}** | {} | {}% |",
                cell(&entry.to_team),
                cell(&entry.player_name),
                cell_value(entry.previous_captain_player_id()),
                fmt_percent0(entry.confidence),
            ));
        }
        md.push(String::new());
    }

    md.extend(skipped_lines);
    format!("{}\n", md.join("\n"))
}

// ---------------------------------------------------------------------------
// HTML
// ---------------------------------------------------------------------------

/// HTML-escapes text, including both quote characters.
fn escape(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for c in text.chars() {
        match c {
            '&' => out.push_str("&amp;"),
            '<' => out.push_str("&lt;"),
            '>' => out.push_str("&gt;"),
            '"' => out.push_str("&quot;"),
            '\'' => out.push_str("&#x27;"),
            c => out.push(c),
        }
    }
    out
}

/// Report `value(raw, fallback)`: empty text falls back, then HTML-escaped.
fn value(raw: &str, fallback: &str) -> String {
    escape(if raw.is_empty() { fallback } else { raw })
}

fn value_num<T: Display>(raw: Option<T>) -> String {
    raw.map_or_else(|| "-".to_owned(), |n| escape(&n.to_string()))
}

fn badge(entry: &LogRecord, shirt: bool, captain: bool, released: bool) -> &'static str {
    if entry.dry_run {
        "<span class=\"badge badge-dry\">Dry-run</span>"
    } else if released {
        "<span class=\"badge badge-release\">Released</span>"
    } else if captain {
        "<span class=\"badge badge-captain\">Captain updated</span>"
    } else if shirt {
        "<span class=\"badge badge-number\">Number changed</span>"
    } else {
        "<span class=\"badge badge-applied\">Transfer applied</span>"
    }
}

const HTML_STYLE: &str = r##"  :root { --ink:#f5f7f2; --muted:#a9b3aa; --pitch:#07130e; --surface:#101d17; --surface-2:#16261e; --line:#2a3c31; --lime:#b8f34a; --cyan:#62d9ff; --amber:#ffd166; --green:#70e19a; }
  * { box-sizing:border-box; }
  body { margin:0; padding:clamp(1rem,3vw,3rem); background:radial-gradient(circle at 85% -10%,#1a3928 0,transparent 34rem),var(--pitch); color:var(--ink); font:15px/1.55 Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif; }
  .shell { width:min(1180px,100%); margin:auto; }
  .masthead { position:relative; overflow:hidden; padding:clamp(1.5rem,4vw,3rem); background:#0d1b14; border:1px solid var(--line); border-radius:16px; box-shadow:0 22px 60px rgba(0,0,0,.28); }
  .masthead::after { content:""; position:absolute; inset:auto -8% -70% auto; width:24rem; aspect-ratio:1; border:1px solid rgba(184,243,74,.24); border-radius:50%; box-shadow:0 16px 48px rgba(0,0,0,.22); }
  .kicker { margin:0 0 .55rem; color:var(--lime); font-size:.78rem; font-weight:800; letter-spacing:.12em; text-transform:uppercase; }
  h1 { max-width:18ch; margin:0; font-size:clamp(2rem,5vw,4.5rem); line-height:.98; letter-spacing:-.035em; text-wrap:balance; }
  .generated { margin:1rem 0 0; color:var(--muted); }
  .summary { display:grid; grid-template-columns:repeat(7,1fr); margin:1.25rem 0 2.5rem; border:1px solid var(--line); border-radius:14px; overflow:hidden; background:var(--surface); }
  .metric strong { display:block; color:var(--lime); font-size:clamp(1.7rem,3vw,2.45rem); line-height:1; letter-spacing:-.03em; }
  .metric span { display:block; margin-top:.45rem; color:var(--muted); font-size:.82rem; }
  .action-line { margin:-1.3rem 0 2.5rem; color:var(--muted); text-align:right; font-size:.86rem; } .action-line strong { color:var(--ink); }
  .report-section { margin-top:1.5rem; background:var(--surface); border:1px solid var(--line); border-radius:16px; overflow:hidden; box-shadow:0 16px 44px rgba(0,0,0,.18); }
  .section-heading { display:flex; align-items:center; justify-content:space-between; gap:1rem; padding:1.25rem 1.4rem; background:var(--surface-2); }
  .section-heading h2 { margin:0; font-size:1.18rem; letter-spacing:-.015em; } .section-heading p { margin:.2rem 0 0; color:var(--muted); font-size:.86rem; }
  .count { min-width:2.3rem; padding:.28rem .65rem; border-radius:999px; background:var(--lime); color:#132008; text-align:center; font-weight:850; }
  .table-wrap { overflow-x:auto; } table { width:100%; min-width:780px; border-collapse:collapse; }
  .table-wrap:focus-visible { outline:2px solid var(--cyan); outline-offset:-2px; }
  th,td { padding:.88rem 1rem; text-align:left; border-bottom:1px solid var(--line); } th { color:var(--muted); font-size:.72rem; letter-spacing:.08em; text-transform:uppercase; } tbody tr:last-child td { border-bottom:0; } tbody tr:hover { background:#18291f; }
  .badge { display:inline-flex; align-items:center; white-space:nowrap; padding:.24rem .58rem; border-radius:999px; font-size:.72rem; font-weight:750; }
  .badge-applied { color:#baf8ce; background:#174b2c; } .badge-number { color:#c9f3ff; background:#124354; } .badge-captain { color:#e2d3ff; background:#3c2860; } .badge-release { color:#ffd2c8; background:#5b2118; } .badge-dry { color:#ffe5a3; background:#55400e; }
  .reason { margin:0; padding:1rem 1.4rem .4rem; font-size:.9rem; color:var(--amber); } .reason span { color:var(--muted); font-weight:500; }
"##;

/// One report section: heading, count and a table.
fn html_section(
    class: &str,
    title: &str,
    blurb: &str,
    count: usize,
    label: &str,
    head: &str,
    rows: &str,
) -> String {
    format!(
        "<section class=\"{class}\">\n        <div class=\"section-heading\"><div><h2>{title}</h2><p>{blurb}</p></div><span class=\"count\">{count}</span></div>\n        <div class=\"table-wrap\" role=\"region\" aria-label=\"{label}\" tabindex=\"0\"><table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table></div>\n      </section>"
    )
}

fn skipped_html(skipped: &[SkippedRow]) -> String {
    if skipped.is_empty() {
        return String::new();
    }
    let relevant = skipped.iter().filter(|item| item.relevant).count();
    let mut groups_html = String::new();
    for (reason, items) in skipped_groups(skipped) {
        let reason_html = value(&reason, "-");
        groups_html.push_str(&format!(
            "<h3 class='reason'><code>{reason_html}</code> <span>{}</span></h3>\
             <div class='table-wrap' role='region' aria-label='Not applied: {reason_html}' tabindex='0'><table><thead><tr>\
             <th>Save club</th><th>Player</th><th>FotMob ID</th><th>From</th><th>To</th>\
             <th>Date</th><th>Source</th><th>Detail</th></tr></thead><tbody>",
            items.len()
        ));
        for item in items {
            let relevant_cell = if item.relevant {
                "<span class=\"badge badge-dry\">Yes</span>"
            } else {
                "No"
            };
            groups_html.push_str(&format!(
                "<tr><td>{relevant_cell}</td><td><strong>{}</strong></td><td class='numeric'>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>",
                value(&item.player_name, "Unknown"),
                value_num(item.fotmob_player_id),
                value(&item.from_team, "-"),
                value(&item.to_team, "-"),
                value(&item.date, "-"),
                value(&item.source, "-"),
                value(&item.detail, "-"),
            ));
        }
        groups_html.push_str("</tbody></table></div>");
    }
    format!(
        "<section class=\"report-section skipped-section\">\n        <div class=\"section-heading\"><div><h2>Not applied</h2><p>{relevant} touch a club in this save. Players missing from the save are never created.</p></div><span class=\"count\">{}</span></div>\n        {groups_html}\n      </section>",
        skipped.len()
    )
}

/// Renders the HTML report: transfer tables for `entries` plus the skipped
/// section.
pub fn render_html(
    entries: &[LogRecord],
    skipped: &[SkippedRow],
    generated: DateTime<Utc>,
) -> String {
    let now = generated_label(generated);
    let title = escape(REPORT_TITLE);
    let sections = Sections::new(entries);
    let m = sections.metrics();

    let mut transfer_rows = String::new();
    for entry in &sections.club_transfers {
        let deal = if entry.fee.is_empty() {
            &entry.transfer_type
        } else {
            &entry.fee
        };
        transfer_rows.push_str(&format!(
            "<tr><td>{}</td><td><strong>{}</strong></td><td><span class='position'>{}</span></td><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td class='numeric'>{}%</td></tr>",
            badge(entry, false, false, false),
            value(&entry.player_name, "Unknown"),
            value(&entry.position, "-"),
            value(&entry.from_team, "-"),
            value(&entry.to_team, "-"),
            value(deal, "Transfer"),
            value(&native_metadata_summary(entry), "-"),
            fmt_percent0(entry.confidence),
        ));
    }
    let mut release_rows = String::new();
    for entry in &sections.releases {
        release_rows.push_str(&format!(
            "<tr><td>{}</td><td><strong>{}</strong></td><td><span class='position'>{}</span></td><td>{}</td><td>{}</td><td class='numeric'>{}%</td></tr>",
            badge(entry, false, false, true),
            value(&entry.player_name, "Unknown"),
            value(&entry.position, "-"),
            value(&entry.from_team, "-"),
            value(&native_metadata_summary(entry), "-"),
            fmt_percent0(entry.confidence),
        ));
    }
    let mut shirt_rows = String::new();
    for entry in &sections.shirts {
        shirt_rows.push_str(&format!(
            "<tr><td>{}</td><td><strong>{}</strong></td><td>{}</td><td class='shirt old'>#{}</td><td class='shirt new'>#{}</td><td class='numeric'>{}%</td></tr>",
            badge(entry, true, false, false),
            value(&entry.player_name, "Unknown"),
            value(&entry.to_team, "-"),
            value_num(entry.previous_shirt_number),
            value_num(entry.shirt_number),
            fmt_percent0(entry.confidence),
        ));
    }
    let mut captain_rows = String::new();
    for entry in &sections.captains {
        let previous = match entry.previous_captain_player_id() {
            None | Some(Value::Null) => "-".to_owned(),
            Some(other) => value(&display_value(other), "-"),
        };
        captain_rows.push_str(&format!(
            "<tr><td>{}</td><td>{}</td><td><strong>{}</strong></td><td>{previous}</td><td class='numeric'>{}%</td></tr>",
            badge(entry, false, true, false),
            value(&entry.to_team, "-"),
            value(&entry.player_name, "Unknown"),
            fmt_percent0(entry.confidence),
        ));
    }

    let transfer_section = if sections.club_transfers.is_empty() {
        String::new()
    } else {
        html_section(
            "report-section",
            "Club transfers",
            "Players moved or signed.",
            sections.club_transfers.len(),
            "Club transfer details",
            "<th>Status</th><th>Player</th><th>Pos</th><th>From</th><th>To</th><th>Deal</th><th>Native metadata</th><th>Match</th>",
            &transfer_rows,
        )
    };
    let release_section = if sections.releases.is_empty() {
        String::new()
    } else {
        html_section(
            "report-section release-section",
            "Player releases",
            "Players removed from a club roster and returned to free agency.",
            sections.releases.len(),
            "Player release details",
            "<th>Status</th><th>Player</th><th>Pos</th><th>Released from</th><th>Native metadata</th><th>Match</th>",
            &release_rows,
        )
    };
    let shirt_section = if sections.shirts.is_empty() {
        String::new()
    } else {
        html_section(
            "report-section number-section",
            "Shirt-number changes",
            "Kit numbers only. No club movement.",
            sections.shirts.len(),
            "Shirt-number change details",
            "<th>Status</th><th>Player</th><th>Club</th><th>Previous</th><th>New</th><th>Match</th>",
            &shirt_rows,
        )
    };
    let captain_section = if sections.captains.is_empty() {
        String::new()
    } else {
        html_section(
            "report-section captain-section",
            "Captain changes",
            "Captain role updates from the latest verified lineup marker.",
            sections.captains.len(),
            "Captain change details",
            "<th>Status</th><th>Club</th><th>New captain</th><th>Previous player ID</th><th>Match</th>",
            &captain_rows,
        )
    };
    let empty_state = if sections.all.is_empty() {
        "<section class=\"empty\"><strong>Everything already current.</strong><p>No save changes were needed in this run.</p></section>"
    } else {
        ""
    };
    let skipped_section = skipped_html(skipped);

    format!(
        "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n<meta charset=\"UTF-8\">\n<meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">\n<title>{title}</title>\n<style>\n{HTML_STYLE}</style>\n</head>\n<body>\n  <main class=\"shell\">\n    <header class=\"masthead\"><p class=\"kicker\">FL26 · verified sync</p><h1>{title}</h1><p class=\"generated\">Generated {now} · {total} save changes</p></header>\n    <section class=\"summary\" aria-label=\"Run summary\">\n      <div class=\"metric\"><strong>{club}</strong><span>Club transfers</span></div>\n      <div class=\"metric\"><strong>{releases}</strong><span>Releases</span></div>\n      <div class=\"metric\"><strong>{permanent}</strong><span>Permanent moves</span></div>\n      <div class=\"metric\"><strong>{loans}</strong><span>Loans / returns</span></div>\n      <div class=\"metric\"><strong>{shirts}</strong><span>Shirt numbers changed</span></div>\n      <div class=\"metric\"><strong>{captains}</strong><span>Captains changed</span></div>\n      <div class=\"metric\"><strong>{dry}</strong><span>Dry-run</span></div>\n    </section>\n    <p class=\"action-line\">Roster actions · <strong>{moves}</strong> direct moves · <strong>{signings}</strong> signings · <strong>{releases}</strong> releases</p>\n    {transfer_section}{release_section}{shirt_section}{captain_section}{empty_state}{skipped_section}\n  </main>\n</body>\n</html>\n",
        total = m.total_changes,
        club = m.club_transfers,
        releases = m.releases,
        permanent = m.permanent,
        loans = m.loans,
        shirts = m.shirt_updates,
        captains = m.captain_updates,
        dry = m.dry_run,
        moves = m.moves,
        signings = m.signings,
    )
}

#[cfg(test)]
mod tests {
    use std::io::Read;

    use chrono::TimeZone;

    use super::*;
    use crate::log::TransferLog;
    use crate::test_support::{testdata_path, testdata_path_or_gz};

    /// Reads `testdata/<rel>`, or `<rel>.gz` when only the compressed copy
    /// exists; `None` (with a skip note) when neither is present.
    fn read_text(rel: &str) -> Option<String> {
        let path = testdata_path_or_gz(rel)?;
        if path.extension().is_some_and(|ext| ext == "gz") {
            let mut text = String::new();
            flate2::read::GzDecoder::new(fs::File::open(&path).unwrap())
                .read_to_string(&mut text)
                .unwrap();
            Some(text)
        } else {
            Some(fs::read_to_string(path).unwrap())
        }
    }

    fn parse_lines<T: serde::de::DeserializeOwned>(text: &str) -> Vec<T> {
        text.lines()
            .map(|line| serde_json::from_str(line).expect("valid jsonl line"))
            .collect()
    }

    fn reserialize<T: Serialize>(rows: &[T]) -> String {
        rows.iter()
            .map(|row| to_json_line(row).unwrap() + "\n")
            .collect()
    }

    /// Replaces every `NN%` with `%` and returns the numbers.
    fn split_percents(line: &str) -> (String, Vec<u32>) {
        let mut mask = String::new();
        let mut numbers = Vec::new();
        let mut digits = String::new();
        for c in line.chars() {
            if c.is_ascii_digit() {
                digits.push(c);
                continue;
            }
            match (c, digits.parse::<u32>()) {
                ('%', Ok(number)) => {
                    numbers.push(number);
                    mask.push('%');
                }
                _ => {
                    mask.push_str(&digits);
                    mask.push(c);
                }
            }
            digits.clear();
        }
        mask.push_str(&digits);
        (mask, numbers)
    }

    /// Asserts equality, reporting the first differing line. With
    /// `tie_rounding`, a line may differ by +1 in each percentage.
    fn assert_same(actual: &str, expected: &str, what: &str, tie_rounding: bool) {
        if actual == expected {
            return;
        }
        let mut actual_lines = actual.split('\n');
        let mut expected_lines = expected.split('\n');
        for number in 1.. {
            let (a, e) = (actual_lines.next(), expected_lines.next());
            if tie_rounding
                && let (Some(a), Some(e)) = (a, e)
                && a != e
                && let (a_mask, a_pcts) = split_percents(a)
                && let (e_mask, e_pcts) = split_percents(e)
                && a_mask == e_mask
                && a_pcts.len() == e_pcts.len()
                && a_pcts
                    .iter()
                    .zip(&e_pcts)
                    .all(|(a, e)| a == e || *a == e + 1)
            {
                continue;
            }
            assert_eq!(a, e, "{what}: first difference at line {number}");
            if a.is_none() {
                break;
            }
        }
    }

    fn frozen(meta: &Path) -> DateTime<Utc> {
        let meta: Value = serde_json::from_str(&fs::read_to_string(meta).unwrap()).unwrap();
        DateTime::parse_from_rfc3339(meta["now_utc"].as_str().unwrap())
            .unwrap()
            .with_timezone(&Utc)
    }

    /// Checks one e2e scenario (`testdata/<rel>`) against its goldens; absent
    /// scenarios are skipped.
    fn check_scenario(rel: &str) {
        let expected = format!("{rel}/expected");
        let (Some(log_text), Some(skipped_text), Some(md), Some(html), Some(meta)) = (
            read_text(&format!("{expected}/transfer_log.jsonl")),
            read_text(&format!("{expected}/skipped_transfers.jsonl")),
            read_text(&format!("{expected}/transfer_summary.md")),
            read_text(&format!("{expected}/transfer_summary.html")),
            testdata_path(&format!("{rel}/meta.json")),
        ) else {
            return;
        };
        let dir = Path::new(rel);
        let generated = frozen(&meta);
        let temp = tempfile::tempdir().unwrap();
        let log_path = temp.path().join("transfer_log.jsonl");
        fs::write(&log_path, &log_text).unwrap();
        let entries = TransferLog::new(&log_path).read(None, false);
        let skipped_path = temp.path().join("skipped_transfers.jsonl");
        fs::write(&skipped_path, &skipped_text).unwrap();
        let skipped = read_skipped(&skipped_path);

        assert_eq!(
            reserialize(&entries),
            log_text,
            "{}: log jsonl",
            dir.display()
        );
        assert_eq!(
            reserialize(&skipped),
            skipped_text,
            "{}: skipped jsonl",
            dir.display()
        );
        // Reports render the in-memory (unrounded) confidence while the log
        // stores it rounded to one decimal; a log value like 81.5 (from 81.45)
        // therefore prints "81%" in the report but "82%" from the log.
        assert_same(
            &render_markdown(&entries, &skipped, generated),
            &md,
            "markdown",
            true,
        );
        assert_same(
            &render_html(&entries, &skipped, generated),
            &html,
            "html",
            true,
        );
    }

    #[test]
    fn small_e2e_reports_match_goldens() {
        check_scenario("e2e/small");
    }

    #[test]
    fn full_e2e_reports_match_goldens() {
        check_scenario("local/e2e/full");
    }

    #[test]
    fn edge_case_reports_match_goldens() {
        let Some(root) = testdata_path("log_report") else {
            return;
        };
        let generated = Utc.with_ymd_and_hms(2026, 10, 24, 22, 25, 55).unwrap();
        let mut cases = 0;
        for case in ["empty", "empty_with_skipped", "mixed", "mixed_with_skipped"] {
            let dir = root.join(case);
            let Some(entries_text) = read_text(&format!("log_report/{case}/entries.jsonl")) else {
                continue;
            };
            let skipped_text = fs::read_to_string(dir.join("skipped.jsonl")).unwrap();
            let entries: Vec<LogRecord> = parse_lines(&entries_text);
            let skipped: Vec<SkippedRow> = parse_lines(&skipped_text);
            assert_eq!(reserialize(&entries), entries_text, "{case}: entries");
            assert_eq!(reserialize(&skipped), skipped_text, "{case}: skipped");
            let md = fs::read_to_string(dir.join("transfer_summary.md")).unwrap();
            let html = fs::read_to_string(dir.join("transfer_summary.html")).unwrap();
            assert_same(
                &render_markdown(&entries, &skipped, generated),
                &md,
                case,
                false,
            );
            assert_same(
                &render_html(&entries, &skipped, generated),
                &html,
                case,
                false,
            );
            cases += 1;
        }
        assert!(cases == 0 || cases == 4, "partial edge fixtures");
    }

    fn row(reason: &str, relevant: bool, date: &str) -> SkippedRow {
        SkippedRow {
            player_name: "P".to_owned(),
            from_team: String::new(),
            to_team: String::new(),
            date: date.to_owned(),
            source: String::new(),
            reason: reason.to_owned(),
            detail: String::new(),
            relevant,
            fotmob_player_id: None,
            candidates: Vec::new(),
            retryable: false,
            save_scope: "s".to_owned(),
        }
    }

    #[test]
    fn sort_skipped_orders_relevant_then_reason_then_date_stably() {
        let mut rows = vec![
            row("b", false, "2"),
            row("b", true, "9"),
            row("a", false, "1"),
            row("a", false, ""),
            row("b", true, "1"),
        ];
        sort_skipped(&mut rows);
        let keys: Vec<_> = rows
            .iter()
            .map(|r| format!("{}{}{}", u8::from(r.relevant), r.reason, r.date))
            .collect();
        assert_eq!(keys, ["1b1", "1b9", "0a", "0a1", "0b2"]);
    }

    #[test]
    fn retryable_is_serialized_only_when_true() {
        let mut item = row("r", true, "d");
        let plain = to_json_line(&item).unwrap();
        assert_eq!(
            plain,
            "{\"player_name\": \"P\", \"from_team\": \"\", \"to_team\": \"\", \"date\": \"d\", \"source\": \"\", \"reason\": \"r\", \"detail\": \"\", \"relevant\": true, \"fotmob_player_id\": null, \"candidates\": [], \"save_scope\": \"s\"}"
        );
        item.retryable = true;
        let retry = to_json_line(&item).unwrap();
        assert!(retry.contains("\"candidates\": [], \"retryable\": true, \"save_scope\": \"s\"}"));
        assert_eq!(serde_json::from_str::<SkippedRow>(&retry).unwrap(), item);
        assert!(
            !serde_json::from_str::<SkippedRow>(&plain)
                .unwrap()
                .retryable
        );
    }

    #[test]
    fn read_skipped_tolerates_missing_and_malformed() {
        let dir = tempfile::tempdir().unwrap();
        assert!(read_skipped(&dir.path().join("none.jsonl")).is_empty());
        let good = to_json_line(&row("r", true, "d")).unwrap();
        let path = dir.path().join("s.jsonl");
        fs::write(&path, format!("garbage\n\n{good}\n[1]\n")).unwrap();
        assert_eq!(read_skipped(&path), vec![row("r", true, "d")]);
    }

    #[test]
    fn write_reports_rewrites_all_three_files() {
        let dir = tempfile::tempdir().unwrap();
        let out = dir.path().join("nested").join("output");
        let generated = Utc.with_ymd_and_hms(2026, 10, 24, 22, 25, 55).unwrap();
        fs::create_dir_all(&out).unwrap();
        fs::write(out.join(SKIPPED_TRANSFERS_FILENAME), "{\"stale\": true}\n").unwrap();

        let (markdown, paths) = write_reports(&out, &[], &[], generated).unwrap();
        assert_eq!(markdown, render_markdown(&[], &[], generated));
        assert_eq!(fs::read_to_string(&paths.markdown).unwrap(), markdown);
        assert_eq!(
            fs::read_to_string(&paths.html).unwrap(),
            render_html(&[], &[], generated)
        );
        assert_eq!(fs::read_to_string(&paths.skipped).unwrap(), "");
        assert!(paths.markdown.ends_with("transfer_summary.md"));
        assert!(paths.html.ends_with("transfer_summary.html"));

        let skipped = vec![row("r", true, "d")];
        write_reports(&out, &[], &skipped, generated).unwrap();
        assert_eq!(read_skipped(&paths.skipped), skipped);
        let leftovers = fs::read_dir(&out).unwrap().count();
        assert_eq!(leftovers, 3, "no temp files left behind");
    }

    #[test]
    fn html_escape_covers_quotes() {
        assert_eq!(
            escape("<a href=\"x\">'&'</a>"),
            "&lt;a href=&quot;x&quot;&gt;&#x27;&amp;&#x27;&lt;/a&gt;"
        );
        assert_eq!(cell("a|b\nc"), "a\\|b c");
        assert_eq!(cell(""), "-");
    }
}
