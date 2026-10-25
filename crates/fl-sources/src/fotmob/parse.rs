//! Pure FotMob payload parsers: feed items, team payloads (transfers, squads,
//! last lineup, captain) and match-page formations. No I/O, no clocks.
//!
//! Every accessor is lenient: a malformed field degrades to "missing" and
//! never aborts the batch (a shifted payload never kills the whole club
//! gather).

use std::collections::{BTreeSet, HashMap, HashSet};
use std::sync::LazyLock;

use chrono::{DateTime, NaiveDate, Utc};
use regex::Regex;
use scraper::{Html, Node};
use serde_json::Value;

use crate::dates::{parse_iso_date, parse_iso_datetime};
use crate::lenient::{
    display_str, int_or_zero, is_truthy, lenient_int, non_negative_u32, optional_positive_int,
    str_or_empty, trimmed,
};
use crate::model::{CaptainUpdate, SquadMember, SquadSnapshot, Transfer, TransferType};

pub(crate) const MIN_COMPLETE_SQUAD_MEMBERS: usize = 11;

pub(crate) fn team_api_url(team_id: i64) -> String {
    format!("https://www.fotmob.com/api/data/teams?id={team_id}")
}

/// Canonical team name of a team payload (`details.name`, `name`, else `fallback`).
pub(crate) fn payload_team_name(data: &Value, fallback: &str) -> String {
    for value in [
        data.get("details").and_then(|d| d.get("name")),
        data.get("name"),
    ] {
        if let Some(value) = value.filter(|v| is_truthy(v)) {
            return display_str(value).trim().to_owned();
        }
    }
    fallback.trim().to_owned()
}

// ---------------------------------------------------------------------------
// Feed / club-transfer items
// ---------------------------------------------------------------------------

/// Parse one raw FotMob transfer item. `None` for contract extensions and
/// items without a player or any club.
pub(crate) fn parse_fotmob_item(item: &Value) -> Option<Transfer> {
    let item = item.as_object().map(|_| item)?;
    if is_truthy(item.get("contractExtension").unwrap_or(&Value::Null)) {
        return None;
    }
    let player_name = trimmed(item.get("name"));
    let mut from_club = trimmed(item.get("fromClub"));
    let mut to_club = trimmed(item.get("toClub"));
    if player_name.is_empty() || (from_club.is_empty() && to_club.is_empty()) {
        return None;
    }
    if from_club.is_empty() || fl_text::is_non_club(&from_club) {
        from_club = "Free Agent".to_owned();
    }
    if to_club.is_empty() || fl_text::is_non_club(&to_club) {
        to_club = "Free Agent".to_owned();
    }

    let position = match item.get("position") {
        Some(Value::Object(object)) => trimmed(object.get("label")).to_uppercase(),
        Some(Value::String(text)) => text.trim().to_uppercase(),
        _ => String::new(),
    };

    let mut is_loan = is_truthy(item.get("onLoan").unwrap_or(&Value::Null));
    let mut fee_text = String::new();
    match item.get("fee") {
        Some(Value::Object(fee)) => {
            fee_text = str_or_empty(fee.get("feeText"));
            if let Some(value) = fee.get("value").filter(|v| is_truthy(v)) {
                fee_text = match value {
                    Value::Number(number) => format_euro(number),
                    other => display_str(other),
                };
            }
        }
        Some(Value::String(text)) => fee_text.clone_from(text),
        _ => {}
    }

    let fee_lower = fee_text.to_lowercase();
    let transfer_type = if fee_lower.contains("end of loan")
        || fee_lower.contains("return")
        || fee_lower.contains("back from loan")
    {
        is_loan = false;
        TransferType::EndOfLoan
    } else if is_loan || fee_lower.contains("loan") {
        is_loan = true;
        TransferType::Loan
    } else if fee_lower.contains("free") || from_club == "Free Agent" || to_club == "Free Agent" {
        TransferType::FreeTransfer
    } else {
        TransferType::Transfer
    };

    let from_club_full = non_empty_or(item.get("fromClubFullName"), &from_club);
    let to_club_full = non_empty_or(item.get("toClubFullName"), &to_club);
    Some(Transfer {
        player_name,
        from_club,
        to_club,
        date: str_or_empty(item.get("transferDate")),
        transfer_type,
        fee: fee_text,
        position,
        is_loan,
        market_value: int_or_zero(item.get("marketValue")),
        from_club_id_fotmob: item.get("fromClubId").and_then(lenient_int),
        to_club_id_fotmob: item.get("toClubId").and_then(lenient_int),
        from_club_full_name: from_club_full,
        to_club_full_name: to_club_full,
        player_id_fotmob: optional_positive_int(item.get("playerId")),
        ..Transfer::default()
    })
}

fn non_empty_or(value: Option<&Value>, fallback: &str) -> String {
    let text = str_or_empty(value);
    if text.is_empty() {
        fallback.to_owned()
    } else {
        text
    }
}

/// `f"€{value:,.0f}"`.
fn format_euro(number: &serde_json::Number) -> String {
    let rounded = number
        .as_f64()
        .map_or_else(|| number.to_string(), |float| format!("{float:.0}"));
    let (sign, digits) = match rounded.strip_prefix('-') {
        Some(rest) => ("-", rest),
        None => ("", rounded.as_str()),
    };
    let mut grouped = String::with_capacity(digits.len() + digits.len() / 3);
    for (index, digit) in digits.chars().enumerate() {
        if index > 0 && (digits.len() - index) % 3 == 0 {
            grouped.push(',');
        }
        grouped.push(digit);
    }
    // Tiny negatives print as "-0" as well.
    format!("€{sign}{grouped}")
}

/// Where a provider event falls relative to a scrape window.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Disposition {
    /// Inside the window: applied.
    Keep,
    /// Not yet effective (future-dated) or undated inside a bounded window:
    /// never applied, but reported.
    Pending,
    /// Effective before the window or after a closed past window.
    Drop,
}

/// Classify a dated event against `[start, end]` as of `today` (UTC).
pub fn window_disposition(
    transfer: &Transfer,
    start: Option<NaiveDate>,
    end: Option<NaiveDate>,
    today: NaiveDate,
) -> Disposition {
    let Some(date) = parse_iso_date(&transfer.date) else {
        return if start.is_some() || end.is_some() {
            Disposition::Pending
        } else {
            Disposition::Keep
        };
    };
    if date > today {
        Disposition::Pending
    } else if end.is_some_and(|e| date > e) || start.is_some_and(|s| date < s) {
        Disposition::Drop
    } else {
        Disposition::Keep
    }
}

/// Transfers of a team payload (`transfers.data["Players in"|"Players out"]`),
/// split into applied events and pending ones.
pub(crate) fn extract_transfers_from_team_data(
    data: &Value,
    start: Option<NaiveDate>,
    end: Option<NaiveDate>,
    today: NaiveDate,
) -> (Vec<Transfer>, Vec<Transfer>) {
    let mut kept = Vec::new();
    let mut pending = Vec::new();
    let Some(groups) = data
        .get("transfers")
        .and_then(|t| t.get("data"))
        .and_then(Value::as_object)
    else {
        return (kept, pending);
    };
    for category in ["Players in", "Players out"] {
        let Some(items) = groups.get(category).and_then(Value::as_array) else {
            continue;
        };
        for raw in items {
            let Some(transfer) = parse_fotmob_item(raw) else {
                continue;
            };
            match window_disposition(&transfer, start, end, today) {
                Disposition::Keep => kept.push(transfer),
                Disposition::Pending => pending.push(transfer),
                Disposition::Drop => {}
            }
        }
    }
    (kept, pending)
}

// ---------------------------------------------------------------------------
// Squads
// ---------------------------------------------------------------------------

/// FotMob's detailed position line preferred over its broad role label.
fn primary_squad_position(raw_member: &Value) -> String {
    let first_detailed = match raw_member.get("positionIdsDesc") {
        Some(Value::String(text)) => text
            .split(',')
            .map(|p| p.trim().to_uppercase())
            .find(|p| !p.is_empty()),
        Some(Value::Array(items)) => items
            .iter()
            .map(|p| str_or_empty(Some(p)).trim().to_uppercase())
            .find(|p| !p.is_empty()),
        _ => None,
    };
    first_detailed.unwrap_or_else(|| match raw_member.get("role") {
        Some(role @ Value::Object(_)) => trimmed(role.get("fallback")),
        _ => String::new(),
    })
}

fn member_identity(player_id: Option<i64>, name: &str) -> (bool, i64, String) {
    match player_id {
        Some(id) => (true, id, String::new()),
        None => (false, 0, fl_text::casefold(name)),
    }
}

fn squad_sections(data: &Value) -> &[Value] {
    data.get("squad")
        .and_then(|squad| squad.get("squad"))
        .and_then(Value::as_array)
        .map_or(&[], Vec::as_slice)
}

fn shirt_number(raw_member: &Value) -> Option<u32> {
    let shirt = raw_member
        .get("shirtNumber")
        .filter(|v| !matches!(v, Value::Null) && **v != Value::String(String::new()))?;
    let parsed = lenient_int(shirt).unwrap_or(0);
    (1..=99).contains(&parsed).then(|| non_negative_u32(parsed))
}

/// Deduplicated current-squad members (coaches/staff excluded). Empty when the
/// team name is blank.
pub(crate) fn extract_squad_members(
    data: &Value,
    team_id: i64,
    team_name: &str,
) -> Vec<SquadMember> {
    if team_name.trim().is_empty() {
        tracing::warn!("Skipping squad sync for FotMob team {team_id} without a team name");
        return Vec::new();
    }
    let mut members = Vec::new();
    let mut seen: HashSet<(bool, i64, String)> = HashSet::new();
    for section in squad_sections(data) {
        if !section.is_object() {
            continue;
        }
        let title = {
            let title = str_or_empty(section.get("title"));
            if title.is_empty() {
                str_or_empty(section.get("name"))
            } else {
                title
            }
        };
        if matches!(
            fl_text::casefold(title.trim()).as_str(),
            "coach" | "coaches" | "manager" | "managers" | "staff"
        ) {
            continue;
        }
        let Some(raw_members) = section.get("members").and_then(Value::as_array) else {
            continue;
        };
        for raw_member in raw_members {
            if !raw_member.is_object() {
                continue;
            }
            let name = trimmed(raw_member.get("name"));
            if name.is_empty() {
                continue;
            }
            if let Some(role @ Value::Object(_)) = raw_member.get("role") {
                let key = str_or_empty(role.get("key"));
                let role_name = if key.is_empty() {
                    str_or_empty(role.get("fallback"))
                } else {
                    key
                };
                if matches!(
                    fl_text::casefold(role_name.trim()).as_str(),
                    "coach" | "manager"
                ) {
                    continue;
                }
            }
            let player_id = optional_positive_int(raw_member.get("id"));
            if !seen.insert(member_identity(player_id, &name)) {
                continue;
            }
            members.push(SquadMember {
                player_name: name,
                player_id_fotmob: player_id,
                position: primary_squad_position(raw_member),
                nationality: trimmed(raw_member.get("countryName")),
                age: non_negative_u32(int_or_zero(raw_member.get("age"))),
                shirt_number: shirt_number(raw_member),
            });
        }
    }
    members
}

/// FotMob `positionId → label` learned from this payload's own squad pairs
/// (`positionIds` / `positionIdsDesc`); ambiguous ids are dropped.
fn payload_position_labels(data: &Value) -> HashMap<i64, String> {
    let mut observed: HashMap<i64, BTreeSet<String>> = HashMap::new();
    for section in squad_sections(data) {
        let Some(raw_members) = section.get("members").and_then(Value::as_array) else {
            continue;
        };
        for raw_member in raw_members {
            let (Some(Value::String(raw_ids)), Some(Value::String(raw_labels))) = (
                raw_member.get("positionIds"),
                raw_member.get("positionIdsDesc"),
            ) else {
                continue;
            };
            let ids: Vec<&str> = raw_ids.split(',').collect();
            let labels: Vec<&str> = raw_labels.split(',').collect();
            if ids.len() != labels.len() {
                continue;
            }
            for (raw_id, raw_label) in ids.into_iter().zip(labels) {
                let position_id = raw_id.trim().parse::<i64>().ok().filter(|id| *id > 0);
                let label = raw_label.trim().to_uppercase();
                if let (Some(id), false) = (position_id, label.is_empty()) {
                    observed.entry(id).or_default().insert(label);
                }
            }
        }
    }
    observed
        .into_iter()
        .filter_map(|(id, labels)| {
            (labels.len() == 1).then(|| (id, labels.into_iter().next().expect("one label")))
        })
        .collect()
}

/// One last-lineup group (`"starters"` or `"subs"`) enriched with squad
/// metadata. Positions are the match-day detailed labels when resolvable.
pub(crate) fn extract_last_lineup_members(
    data: &Value,
    members: &[SquadMember],
    group: &str,
) -> Vec<SquadMember> {
    let Some(raw_group) = data
        .get("overview")
        .and_then(|o| o.get("lastLineupStats"))
        .and_then(|l| l.get(group))
        .and_then(Value::as_array)
    else {
        return Vec::new();
    };
    let position_labels = payload_position_labels(data);
    // Later duplicates win.
    let by_id: HashMap<i64, &SquadMember> = members
        .iter()
        .filter_map(|m| m.player_id_fotmob.map(|id| (id, m)))
        .collect();
    let by_name: HashMap<String, &SquadMember> = members
        .iter()
        .map(|m| (fl_text::casefold(&m.player_name), m))
        .collect();

    let mut lineup = Vec::new();
    let mut seen: HashSet<(bool, i64, String)> = HashSet::new();
    for raw_member in raw_group {
        if !raw_member.is_object() {
            continue;
        }
        let name = trimmed(raw_member.get("name"));
        if name.is_empty() {
            continue;
        }
        let player_id = optional_positive_int(raw_member.get("id"));
        if !seen.insert(member_identity(player_id, &name)) {
            continue;
        }
        let match_position = optional_positive_int(raw_member.get("positionId"))
            .and_then(|id| position_labels.get(&id))
            .cloned()
            .unwrap_or_default();
        let squad_member = match player_id {
            Some(id) => by_id.get(&id).copied(),
            None => by_name.get(&fl_text::casefold(&name)).copied(),
        };
        if let Some(squad_member) = squad_member {
            let mut member = squad_member.clone();
            if !match_position.is_empty() && match_position != member.position {
                member.position = match_position;
            }
            lineup.push(member);
            continue;
        }
        lineup.push(SquadMember {
            player_name: name,
            player_id_fotmob: player_id,
            position: if match_position.is_empty() {
                primary_squad_position(raw_member)
            } else {
                match_position
            },
            nationality: trimmed(raw_member.get("countryName")),
            age: non_negative_u32(int_or_zero(raw_member.get("age"))),
            shirt_number: None,
        });
    }
    lineup
}

/// `\d+(-\d+){2,3}` not embedded in a longer digit run (the regex crate has
/// no lookaround, so the boundaries are checked by hand).
pub(crate) fn formation_label(value: &Value) -> Option<String> {
    let text = value.as_str()?;
    let bytes = text.as_bytes();
    let digits_from = |mut at: usize| {
        let start = at;
        while at < bytes.len() && bytes[at].is_ascii_digit() {
            at += 1;
        }
        (at > start).then_some(at)
    };
    for start in 0..bytes.len() {
        if !bytes[start].is_ascii_digit() || (start > 0 && bytes[start - 1].is_ascii_digit()) {
            continue;
        }
        let Some(mut end) = digits_from(start) else {
            continue;
        };
        let mut groups = 0;
        while groups < 3 && bytes.get(end) == Some(&b'-') {
            let Some(next) = digits_from(end + 1) else {
                break;
            };
            end = next;
            groups += 1;
        }
        if groups >= 2 {
            return Some(text[start..end].to_owned());
        }
    }
    None
}

/// Current-squad snapshot; undersized payloads are marked incomplete.
pub(crate) fn extract_squad_snapshot(data: &Value, team_id: i64, team_name: &str) -> SquadSnapshot {
    let members = extract_squad_members(data, team_id, team_name);
    let starter_members = extract_last_lineup_members(data, &members, "starters");
    let sub_members = extract_last_lineup_members(data, &members, "subs");
    let formation = data
        .get("overview")
        .and_then(|o| o.get("lastLineupStats"))
        .and_then(|l| l.get("formation"))
        .and_then(formation_label);
    let source_url = team_api_url(team_id);
    let primary_league_id = data
        .get("details")
        .and_then(|d| d.get("primaryLeagueId"))
        .and_then(lenient_int)
        .filter(|id| *id > 0);
    SquadSnapshot {
        club_name: team_name.trim().to_owned(),
        team_id_fotmob: team_id,
        complete: members.len() >= MIN_COMPLETE_SQUAD_MEMBERS,
        members,
        formation_source_url: if formation.is_some() {
            source_url.clone()
        } else {
            String::new()
        },
        source_url,
        starter_members,
        sub_members,
        primary_league_id,
        formation,
    }
}

/// Shirt-number observations for squad members that carry a valid number.
pub(crate) fn extract_shirt_number_updates(
    data: &Value,
    team_id: i64,
    team_name: &str,
) -> Vec<Transfer> {
    let source_url = team_api_url(team_id);
    extract_squad_members(data, team_id, team_name)
        .into_iter()
        .filter(|member| member.shirt_number.is_some())
        .map(|member| Transfer {
            player_name: member.player_name,
            from_club: team_name.to_owned(),
            to_club: team_name.to_owned(),
            transfer_type: TransferType::ShirtNumberUpdate,
            shirt_number: member.shirt_number,
            position: member.position,
            age: member.age,
            nationality: member.nationality,
            from_club_id_fotmob: Some(team_id),
            to_club_id_fotmob: Some(team_id),
            from_club_full_name: team_name.to_owned(),
            to_club_full_name: team_name.to_owned(),
            player_id_fotmob: member.player_id_fotmob,
            source_urls: vec![source_url.clone()],
            proof_urls: vec![source_url.clone()],
            ..Transfer::default()
        })
        .collect()
}

/// The single captain marker of the latest lineup; ambiguous or incomplete
/// markers yield nothing.
pub(crate) fn extract_captain(
    data: &Value,
    team_id: i64,
    team_name: &str,
) -> Option<CaptainUpdate> {
    let last_lineup = data.get("overview")?.get("lastLineupStats")?;
    last_lineup.as_object()?;
    let mut candidates = Vec::new();
    for group in ["starters", "subs"] {
        if let Some(members) = last_lineup.get(group).and_then(Value::as_array) {
            candidates.extend(
                members
                    .iter()
                    .filter(|m| m.is_object() && m.get("isCaptain") == Some(&Value::Bool(true))),
            );
        }
    }
    let label = if team_name.is_empty() {
        team_id.to_string()
    } else {
        team_name.to_owned()
    };
    if candidates.len() != 1 {
        if !candidates.is_empty() {
            tracing::warn!(
                "Skipping ambiguous captain data for {label} ({team_id}): {} markers",
                candidates.len()
            );
        }
        return None;
    }
    let member = candidates[0];
    let player_name = trimmed(member.get("name"));
    let player_id = optional_positive_int(member.get("id"));
    if player_name.is_empty() || player_id.is_none() {
        tracing::warn!("Skipping incomplete captain data for {label} ({team_id})");
        return None;
    }
    Some(CaptainUpdate {
        club_name: team_name.trim().to_owned(),
        team_id_fotmob: team_id,
        player_name,
        player_id_fotmob: player_id,
        age: non_negative_u32(int_or_zero(member.get("age"))),
        nationality: trimmed(member.get("countryName")),
        source: "fotmob".to_owned(),
        source_url: team_api_url(team_id),
    })
}

// ---------------------------------------------------------------------------
// Match page formation
// ---------------------------------------------------------------------------

struct UrlParts<'a> {
    netloc: &'a str,
    path: &'a str,
    fragment: &'a str,
}

/// Minimal `urllib.parse.urlsplit` for the fields the match-link filter needs.
fn split_url(link: &str) -> UrlParts<'_> {
    let (rest, fragment) = link.split_once('#').unwrap_or((link, ""));
    let rest = rest.split_once('?').map_or(rest, |(before, _)| before);
    let after_scheme = match rest.split_once(':') {
        Some((scheme, tail))
            if scheme
                .chars()
                .next()
                .is_some_and(|c| c.is_ascii_alphabetic())
                && scheme
                    .chars()
                    .all(|c| c.is_ascii_alphanumeric() || "+-.".contains(c)) =>
        {
            tail
        }
        _ => rest,
    };
    match after_scheme.strip_prefix("//") {
        Some(authority) => {
            let split = authority.find('/').unwrap_or(authority.len());
            UrlParts {
                netloc: &authority[..split],
                path: &authority[split..],
                fragment,
            }
        }
        None => UrlParts {
            netloc: "",
            path: after_scheme,
            fragment,
        },
    }
}

fn netloc_hostname(netloc: &str) -> String {
    let host_port = netloc.rsplit_once('@').map_or(netloc, |(_, host)| host);
    let host = match host_port.strip_prefix('[') {
        Some(rest) => rest.split(']').next().unwrap_or_default(),
        None => host_port.split(':').next().unwrap_or_default(),
    };
    host.to_lowercase()
}

/// Latest finished match page of a team (`teamForm` rows), as
/// `(absolute page url, FotMob's label for this team in that match)`.
pub(crate) fn latest_team_match_page(
    data: &Value,
    team_id: i64,
) -> Option<(String, Option<String>)> {
    struct Candidate {
        played_at: DateTime<Utc>,
        match_id: i64,
        url: String,
        label: Option<String>,
    }
    fn visit(value: &Value, team_id: i64, out: &mut Vec<Candidate>) {
        match value {
            Value::Object(map) => {
                if let Some(rows) = map
                    .get("teamForm")
                    .and_then(|forms| forms.get(team_id.to_string()))
                    .and_then(Value::as_array)
                {
                    out.extend(rows.iter().filter_map(|row| candidate(row, team_id)));
                }
                for nested in map.values() {
                    visit(nested, team_id, out);
                }
            }
            Value::Array(items) => {
                for nested in items {
                    visit(nested, team_id, out);
                }
            }
            _ => {}
        }
    }
    fn candidate(row: &Value, team_id: i64) -> Option<Candidate> {
        let tooltip = row.get("tooltipText").filter(|t| t.is_object())?;
        let home = optional_positive_int(tooltip.get("homeTeamId"));
        let away = optional_positive_int(tooltip.get("awayTeamId"));
        if home != Some(team_id) && away != Some(team_id) {
            return None;
        }
        if !matches!(
            str_or_empty(row.get("resultString"))
                .to_uppercase()
                .as_str(),
            "W" | "D" | "L"
        ) {
            return None;
        }
        let played_at = parse_iso_datetime(&str_or_empty(tooltip.get("utcTime")))?;
        let link = trimmed(row.get("linkToMatch"));
        if link.is_empty() {
            return None;
        }
        let parts = split_url(&link);
        let foreign_host = !parts.netloc.is_empty()
            && !matches!(
                netloc_hostname(parts.netloc).as_str(),
                "fotmob.com" | "www.fotmob.com"
            );
        if foreign_host
            || !parts.path.starts_with("/matches/")
            || parts.fragment.is_empty()
            || !parts.fragment.bytes().all(|b| b.is_ascii_digit())
        {
            return None;
        }
        let label = if home == Some(team_id) {
            tooltip.get("homeTeam")
        } else {
            tooltip.get("awayTeam")
        };
        Some(Candidate {
            played_at,
            match_id: parts.fragment.parse().ok()?,
            url: format!("https://www.fotmob.com{}", parts.path),
            label: label
                .filter(|l| is_truthy(l))
                .map(|l| display_str(l).trim().to_owned()),
        })
    }

    let mut candidates = Vec::new();
    visit(data, team_id, &mut candidates);
    let mut best: Option<Candidate> = None;
    for candidate in candidates {
        let better = best.as_ref().is_none_or(|current| {
            (candidate.played_at, candidate.match_id, &candidate.url)
                > (current.played_at, current.match_id, &current.url)
        });
        if better {
            best = Some(candidate);
        }
    }
    best.map(|c| (c.url, c.label))
}

/// Visible text of a rendered page (script/style/noscript removed), pieces
/// trimmed and joined with single spaces.
fn visible_text(html: &str) -> String {
    let document = Html::parse_document(html);
    let mut parts: Vec<&str> = Vec::new();
    for node in document.tree.nodes() {
        let Node::Text(text) = node.value() else {
            continue;
        };
        let hidden = node.ancestors().any(|ancestor| {
            matches!(ancestor.value(), Node::Element(element) if matches!(element.name(), "script" | "style" | "noscript"))
        });
        let trimmed = text.trim();
        if !hidden && !trimmed.is_empty() {
            parts.push(trimmed);
        }
    }
    parts.join(" ")
}

static FORMATION_LINE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?P<team>[^:.;]{1,100}?)\s*\((?P<formation>\d+(?:-\d+){2,3})\)\s*:")
        .expect("static regex")
});

/// The named team's formation from FotMob's rendered match summary
/// ("Team Name (4-3-3):").
pub(crate) fn formation_from_match_page(html: &str, team_names: &[String]) -> Option<String> {
    let pattern = &*FORMATION_LINE;
    let known: HashSet<String> = team_names
        .iter()
        .filter(|name| !name.trim().is_empty())
        .map(|name| fl_text::alnum_key(name))
        .collect();
    let text = visible_text(html);
    pattern
        .captures_iter(&text)
        .find(|caps| known.contains(&fl_text::alnum_key(caps["team"].trim())))
        .map(|caps| caps["formation"].to_owned())
}
