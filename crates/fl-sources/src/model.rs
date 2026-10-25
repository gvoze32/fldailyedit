//! Domain models for scraped transfer data.
//!
//! `MatchedTransfer` is engine territory (it carries save-database ids) and is
//! not defined here.

use std::collections::{HashMap, HashSet};
use std::fmt;

use serde::{Deserialize, Serialize};

/// Lifecycle kind of a [`Transfer`]. The string form (`"free transfer"`,
/// `"end of loan"`, ...) is what gets persisted and what logs and reports
/// print.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Default, Serialize, Deserialize)]
pub enum TransferType {
    /// Paid permanent move (also the neutral "unspecified" kind).
    #[default]
    #[serde(rename = "transfer")]
    Transfer,
    #[serde(rename = "loan")]
    Loan,
    #[serde(rename = "end of loan")]
    EndOfLoan,
    #[serde(rename = "free transfer")]
    FreeTransfer,
    /// Roster observation: player is in the club's current squad.
    #[serde(rename = "squad_registration")]
    SquadRegistration,
    /// Roster observation: player left the club's current squad.
    #[serde(rename = "squad_release")]
    SquadRelease,
    /// Roster observation: current shirt number of a squad member.
    #[serde(rename = "shirt_number_update")]
    ShirtNumberUpdate,
}

impl TransferType {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Transfer => "transfer",
            Self::Loan => "loan",
            Self::EndOfLoan => "end of loan",
            Self::FreeTransfer => "free transfer",
            Self::SquadRegistration => "squad_registration",
            Self::SquadRelease => "squad_release",
            Self::ShirtNumberUpdate => "shirt_number_update",
        }
    }

    pub fn parse(value: &str) -> Option<Self> {
        Some(match value {
            "transfer" => Self::Transfer,
            "loan" => Self::Loan,
            "end of loan" => Self::EndOfLoan,
            "free transfer" => Self::FreeTransfer,
            "squad_registration" => Self::SquadRegistration,
            "squad_release" => Self::SquadRelease,
            "shirt_number_update" => Self::ShirtNumberUpdate,
            _ => return None,
        })
    }
}

impl fmt::Display for TransferType {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

/// How much trust a [`Transfer`] carries as evidence.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Default, Serialize, Deserialize)]
pub enum VerificationStatus {
    /// Primary-source event (FotMob, Transfermarkt).
    #[default]
    #[serde(rename = "verified")]
    Verified,
    /// Supplemental source that may only corroborate a primary event.
    #[serde(rename = "corroborator")]
    Corroborator,
    /// A corroborator that was confirmed by reconciliation.
    #[serde(rename = "corroborated")]
    Corroborated,
    /// Sortitoutsi fast signal (serialized as `"enabled"`).
    #[serde(rename = "enabled")]
    Enabled,
}

impl VerificationStatus {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Verified => "verified",
            Self::Corroborator => "corroborator",
            Self::Corroborated => "corroborated",
            Self::Enabled => "enabled",
        }
    }
}

/// A normalized transfer event (or roster observation) from one or more
/// sources. Contract extensions are skipped at parse time and never become
/// events.
///
/// Ids are positive `i64`s; `None` means unknown. `age == 0` and
/// `market_value == 0` mean unknown.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Transfer {
    pub player_name: String,
    pub from_club: String,
    pub to_club: String,
    /// ISO date or timestamp exactly as the provider gave it ("" when unknown).
    pub date: String,
    pub transfer_type: TransferType,
    pub fee: String,
    /// Upper-case position label ("GK", "CB", ...), "" when unknown.
    pub position: String,
    pub is_loan: bool,
    pub market_value: i64,
    pub from_club_id_fotmob: Option<i64>,
    pub to_club_id_fotmob: Option<i64>,
    pub from_club_full_name: String,
    pub to_club_full_name: String,
    pub nationality: String,
    pub age: u32,
    pub shirt_number: Option<u32>,
    pub player_id_fotmob: Option<i64>,
    pub sources: Vec<String>,
    pub source_urls: Vec<String>,
    pub proof_urls: Vec<String>,
    pub player_id_sortitoutsi: Option<i64>,
    pub player_id_transfermarkt: Option<i64>,
    pub from_club_id_transfermarkt: Option<i64>,
    pub to_club_id_transfermarkt: Option<i64>,
    pub transfer_id_transfermarkt: Option<i64>,
    pub verification_status: VerificationStatus,
    pub infer_from_current_roster: bool,
}

impl Default for Transfer {
    /// Defaults: `sources=("fotmob",)`, type "transfer", ...
    fn default() -> Self {
        Self {
            player_name: String::new(),
            from_club: String::new(),
            to_club: String::new(),
            date: String::new(),
            transfer_type: TransferType::Transfer,
            fee: String::new(),
            position: String::new(),
            is_loan: false,
            market_value: 0,
            from_club_id_fotmob: None,
            to_club_id_fotmob: None,
            from_club_full_name: String::new(),
            to_club_full_name: String::new(),
            nationality: String::new(),
            age: 0,
            shirt_number: None,
            player_id_fotmob: None,
            sources: vec!["fotmob".to_owned()],
            source_urls: Vec::new(),
            proof_urls: Vec::new(),
            player_id_sortitoutsi: None,
            player_id_transfermarkt: None,
            from_club_id_transfermarkt: None,
            to_club_id_transfermarkt: None,
            transfer_id_transfermarkt: None,
            verification_status: VerificationStatus::Verified,
            infer_from_current_roster: false,
        }
    }
}

impl fmt::Display for Transfer {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let pos = if self.position.is_empty() {
            String::new()
        } else {
            format!(" [{}]", self.position)
        };
        write!(
            f,
            "{}{}: {} → {} ({})",
            self.player_name, pos, self.from_club, self.to_club, self.transfer_type
        )
    }
}

/// Provenance rank of a source name (lower wins): FotMob, Transfermarkt,
/// Wikipedia, then everything else.
fn source_rank(source: &str) -> u8 {
    match fl_text::casefold(source).as_str() {
        "fotmob" => 0,
        "transfermarkt" => 1,
        "wikipedia" => 2,
        _ => 3,
    }
}

/// Strongest provenance rank carried by an event (lower wins; 3 when it has
/// no sources).
pub fn source_priority(transfer: &Transfer) -> u8 {
    transfer
        .sources
        .iter()
        .map(|source| source_rank(source))
        .min()
        .unwrap_or(3)
}

fn push_unique(target: &mut Vec<String>, extra: &[String]) {
    for value in extra {
        if !target.contains(value) {
            target.push(value.clone());
        }
    }
}

fn fill_string(target: &mut String, source: &str) {
    if target.is_empty() && !source.is_empty() {
        source.clone_into(target);
    }
}

fn fill_id(target: &mut Option<i64>, source: Option<i64>) {
    if target.is_none_or(|v| v <= 0) && source.is_some_and(|v| v > 0) {
        *target = source;
    }
}

/// Union provenance (`sources`, `source_urls`, `proof_urls`, order kept) and
/// prefer a timestamped date over a bare one.
pub fn merge_provenance_into(existing: &mut Transfer, duplicate: &Transfer) {
    push_unique(&mut existing.sources, &duplicate.sources);
    push_unique(&mut existing.source_urls, &duplicate.source_urls);
    push_unique(&mut existing.proof_urls, &duplicate.proof_urls);
    if !existing.date.contains('T') && duplicate.date.contains('T') {
        existing.date.clone_from(&duplicate.date);
    }
}

/// Fold `duplicate` into `existing`: fill every missing field from the richer
/// duplicate (FotMob and Transfermarkt ids, Sortitoutsi id, shirt number,
/// metadata), refine the event type, union provenance.
pub fn merge_duplicate_into(existing: &mut Transfer, duplicate: &Transfer) {
    fill_string(&mut existing.position, &duplicate.position);
    fill_string(&mut existing.fee, &duplicate.fee);
    if existing.shirt_number.is_none_or(|v| v == 0)
        && duplicate.shirt_number.is_some_and(|v| v != 0)
    {
        existing.shirt_number = duplicate.shirt_number;
    }
    fill_string(&mut existing.nationality, &duplicate.nationality);
    if existing.age == 0 {
        existing.age = duplicate.age;
    }
    if existing.market_value == 0 {
        existing.market_value = duplicate.market_value;
    }
    fill_id(
        &mut existing.from_club_id_fotmob,
        duplicate.from_club_id_fotmob,
    );
    fill_id(&mut existing.to_club_id_fotmob, duplicate.to_club_id_fotmob);
    fill_string(
        &mut existing.from_club_full_name,
        &duplicate.from_club_full_name,
    );
    fill_string(
        &mut existing.to_club_full_name,
        &duplicate.to_club_full_name,
    );
    fill_id(&mut existing.player_id_fotmob, duplicate.player_id_fotmob);
    fill_id(
        &mut existing.player_id_sortitoutsi,
        duplicate.player_id_sortitoutsi,
    );
    fill_id(
        &mut existing.player_id_transfermarkt,
        duplicate.player_id_transfermarkt,
    );
    fill_id(
        &mut existing.from_club_id_transfermarkt,
        duplicate.from_club_id_transfermarkt,
    );
    fill_id(
        &mut existing.to_club_id_transfermarkt,
        duplicate.to_club_id_transfermarkt,
    );
    fill_id(
        &mut existing.transfer_id_transfermarkt,
        duplicate.transfer_id_transfermarkt,
    );
    adopt_event_type(existing, duplicate);
    merge_provenance_into(existing, duplicate);
}

type MergeKey = (String, String, String, String, bool, Option<TransferType>);

/// Deduplicate events by player, route and day, keeping first-seen order and
/// the richest metadata.
///
/// Players and clubs are identified by FotMob id when present, otherwise by
/// the id their (folded) name unambiguously maps to elsewhere in the input,
/// otherwise by folded name. Two explicitly different lifecycle types on the
/// same route and day (an end-of-loan and a new loan) stay separate.
pub fn merge_transfers(transfers: Vec<Transfer>) -> Vec<Transfer> {
    let mut player_ids_by_name: HashMap<String, HashSet<i64>> = HashMap::new();
    let mut club_ids_by_name: HashMap<String, HashSet<i64>> = HashMap::new();
    for transfer in &transfers {
        if let Some(id) = transfer.player_id_fotmob {
            player_ids_by_name
                .entry(fl_text::fold_text(&transfer.player_name))
                .or_default()
                .insert(id);
        }
        for (club_id, short, full) in [
            (
                transfer.from_club_id_fotmob,
                &transfer.from_club,
                &transfer.from_club_full_name,
            ),
            (
                transfer.to_club_id_fotmob,
                &transfer.to_club,
                &transfer.to_club_full_name,
            ),
        ] {
            let Some(club_id) = club_id.filter(|id| *id > 0) else {
                continue;
            };
            for name in [short, full] {
                let folded = fl_text::fold_text(name);
                if !folded.is_empty() {
                    club_ids_by_name.entry(folded).or_default().insert(club_id);
                }
            }
        }
    }
    let unique = |map: HashMap<String, HashSet<i64>>| -> HashMap<String, i64> {
        map.into_iter()
            .filter_map(|(name, ids)| {
                (ids.len() == 1).then(|| (name, *ids.iter().next().expect("one id")))
            })
            .collect()
    };
    let canonical_players = unique(player_ids_by_name);
    let canonical_clubs = unique(club_ids_by_name);

    let club_key = |club_id: Option<i64>, short: &str, full: &str| -> String {
        if let Some(id) = club_id.filter(|id| *id > 0) {
            return format!("id:{id}");
        }
        for name in [full, short] {
            if let Some(id) = canonical_clubs.get(&fl_text::fold_text(name)) {
                return format!("id:{id}");
            }
        }
        format!(
            "name:{}",
            fl_text::fold_text(if full.is_empty() { short } else { full })
        )
    };
    let player_key = |transfer: &Transfer| -> String {
        if let Some(id) = transfer.player_id_fotmob {
            return format!("id:{id}");
        }
        let folded = fl_text::fold_text(&transfer.player_name);
        match canonical_players.get(&folded) {
            Some(id) => format!("id:{id}"),
            None => format!("name:{folded}"),
        }
    };

    let mut order: Vec<Transfer> = Vec::new();
    let mut index: HashMap<MergeKey, usize> = HashMap::new();
    for transfer in transfers {
        let mut key: MergeKey = (
            player_key(&transfer),
            club_key(
                transfer.from_club_id_fotmob,
                &transfer.from_club,
                &transfer.from_club_full_name,
            ),
            club_key(
                transfer.to_club_id_fotmob,
                &transfer.to_club,
                &transfer.to_club_full_name,
            ),
            transfer
                .date
                .split('T')
                .next()
                .unwrap_or_default()
                .to_owned(),
            transfer.transfer_type == TransferType::ShirtNumberUpdate,
            None,
        );
        let mut slot = index.get(&key).copied();
        if let Some(existing) = slot.map(|i| &order[i])
            && existing.transfer_type != transfer.transfer_type
            && existing.transfer_type != TransferType::Transfer
            && transfer.transfer_type != TransferType::Transfer
        {
            key.5 = Some(transfer.transfer_type);
            slot = index.get(&key).copied();
        }
        match slot {
            Some(i) => merge_duplicate_into(&mut order[i], &transfer),
            None => {
                index.insert(key, order.len());
                order.push(transfer);
            }
        }
    }
    order
}

/// Refine a generic `Transfer` with a duplicate's explicit lifecycle type.
///
/// Only an equally or more trusted duplicate may refine the type: FotMob uses
/// `transfer` for paid permanent moves, so a corroborator's `loan` must not
/// turn a verified permanent move into a loan. Call before merging sources.
pub fn adopt_event_type(target: &mut Transfer, duplicate: &Transfer) {
    if target.transfer_type == TransferType::Transfer
        && duplicate.transfer_type != TransferType::Transfer
        && source_priority(duplicate) <= source_priority(target)
    {
        target.transfer_type = duplicate.transfer_type;
        target.is_loan = duplicate.is_loan;
    }
}

/// One player entry from a complete current-squad payload.
#[derive(Debug, Clone, PartialEq, Eq, Default, Serialize, Deserialize)]
pub struct SquadMember {
    pub player_name: String,
    pub player_id_fotmob: Option<i64>,
    pub position: String,
    pub nationality: String,
    pub age: u32,
    pub shirt_number: Option<u32>,
}

/// Authoritative current-squad membership for one indexed club.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SquadSnapshot {
    pub club_name: String,
    pub team_id_fotmob: i64,
    pub members: Vec<SquadMember>,
    pub source_url: String,
    pub complete: bool,
    /// Last-lineup starters in FotMob order (squad metadata merged in).
    pub starter_members: Vec<SquadMember>,
    pub sub_members: Vec<SquadMember>,
    pub primary_league_id: Option<i64>,
    pub formation: Option<String>,
    pub formation_source_url: String,
}

/// Current captain marker extracted from a club's live team payload.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CaptainUpdate {
    pub club_name: String,
    pub team_id_fotmob: i64,
    pub player_name: String,
    pub player_id_fotmob: Option<i64>,
    pub age: u32,
    pub nationality: String,
    pub source: String,
    pub source_url: String,
}

/// Evidence-backed main-preset settings for one FotMob club.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct TacticalUpdate {
    pub club_name: String,
    pub team_id_fotmob: i64,
    pub league_id: i64,
    /// `(setting, value)` sorted by setting name.
    pub settings: Vec<(String, i32)>,
    pub sample_matches: u32,
    pub source_urls: Vec<String>,
}

/// Everything one scrape produced.
///
/// A plain struct: no side channel is lost by copying or slicing, and an
/// empty result is simply empty (see [`ScrapeResult::is_empty`]).
#[derive(Debug, Clone, Default, PartialEq)]
pub struct ScrapeResult {
    pub transfers: Vec<Transfer>,
    pub captain_updates: Vec<CaptainUpdate>,
    pub squad_snapshots: Vec<SquadSnapshot>,
    pub roster_updates: Vec<Transfer>,
    pub tactical_updates: Vec<TacticalUpdate>,
    /// Provider events not yet effective (future-dated) or undated inside a
    /// bounded window. Never applied; surfaced so callers can report them.
    pub pending_transfers: Vec<Transfer>,
}

impl ScrapeResult {
    pub fn is_empty(&self) -> bool {
        self.transfers.is_empty()
            && self.captain_updates.is_empty()
            && self.squad_snapshots.is_empty()
            && self.roster_updates.is_empty()
            && self.tactical_updates.is_empty()
            && self.pending_transfers.is_empty()
    }

    /// Append everything from `other`.
    pub fn extend(&mut self, other: ScrapeResult) {
        self.transfers.extend(other.transfers);
        self.captain_updates.extend(other.captain_updates);
        self.squad_snapshots.extend(other.squad_snapshots);
        self.roster_updates.extend(other.roster_updates);
        self.tactical_updates.extend(other.tactical_updates);
        self.pending_transfers.extend(other.pending_transfers);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn with_sources(transfer_type: TransferType, sources: &[&str]) -> Transfer {
        Transfer {
            transfer_type,
            sources: sources.iter().map(|s| (*s).to_owned()).collect(),
            ..Transfer::default()
        }
    }

    #[test]
    fn source_priority_is_best_rank() {
        assert_eq!(
            source_priority(&with_sources(
                TransferType::Transfer,
                &["Wikipedia", "FotMob"]
            )),
            0
        );
        assert_eq!(
            source_priority(&with_sources(TransferType::Transfer, &["wikipedia"])),
            2
        );
        assert_eq!(
            source_priority(&with_sources(TransferType::Transfer, &["besoccer"])),
            3
        );
        assert_eq!(
            source_priority(&with_sources(TransferType::Transfer, &[])),
            3
        );
    }

    #[test]
    fn corroborator_loan_cannot_turn_fotmob_move_into_loan() {
        let mut target = with_sources(TransferType::Transfer, &["fotmob"]);
        let mut duplicate = with_sources(TransferType::Loan, &["wikipedia"]);
        duplicate.is_loan = true;
        adopt_event_type(&mut target, &duplicate);
        assert_eq!(target.transfer_type, TransferType::Transfer);
        assert!(!target.is_loan);

        // An equally trusted duplicate may refine.
        let mut equal = with_sources(TransferType::Loan, &["fotmob"]);
        equal.is_loan = true;
        adopt_event_type(&mut target, &equal);
        assert_eq!(target.transfer_type, TransferType::Loan);
        assert!(target.is_loan);
    }

    #[test]
    fn untrusted_target_is_refined_by_trusted_duplicate() {
        let mut target = with_sources(TransferType::Transfer, &["besoccer"]);
        let duplicate = with_sources(TransferType::FreeTransfer, &["wikipedia"]);
        adopt_event_type(&mut target, &duplicate);
        assert_eq!(target.transfer_type, TransferType::FreeTransfer);
    }

    #[test]
    fn transfer_type_strings_round_trip() {
        for kind in [
            TransferType::Transfer,
            TransferType::Loan,
            TransferType::EndOfLoan,
            TransferType::FreeTransfer,
            TransferType::SquadRegistration,
            TransferType::SquadRelease,
            TransferType::ShirtNumberUpdate,
        ] {
            assert_eq!(TransferType::parse(kind.as_str()), Some(kind));
            let json = serde_json::to_string(&kind).unwrap();
            assert_eq!(json, format!("\"{}\"", kind.as_str()));
        }
        assert_eq!(TransferType::parse("bogus"), None);
    }

    #[test]
    fn display_format() {
        let transfer = Transfer {
            player_name: "A B".into(),
            from_club: "X".into(),
            to_club: "Y".into(),
            position: "GK".into(),
            transfer_type: TransferType::Loan,
            ..Transfer::default()
        };
        assert_eq!(transfer.to_string(), "A B [GK]: X → Y (loan)");
    }
}
