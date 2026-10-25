//! Audit/report records of applied changes.
//!
//! One [`LogRecord`] per applied action. The report renders them as built
//! here (unrounded confidence); the transfer log gets them stamped and
//! rounded by [`super::commit::log_records`].

use fl_save::model::TeamData;
use fl_sources::model::TransferType;
use serde_json::{Value, json};

use crate::log::LogRecord;
use crate::planning::{MatchedTransfer, PlannedCaptainUpdate, RosterAction};

/// Record of one applied roster action (move, signing, release, shirt number).
pub(crate) fn transfer_entry(
    matched: &MatchedTransfer,
    action: RosterAction,
    previous_shirt: Option<u32>,
    native_metadata: Value,
    dry_run: bool,
) -> LogRecord {
    let transfer = &matched.transfer;
    let non_empty = |preferred: &str, fallback: &str| {
        if preferred.is_empty() {
            fallback
        } else {
            preferred
        }
        .to_owned()
    };
    let shirt_number = (transfer.transfer_type == TransferType::ShirtNumberUpdate
        || action == RosterAction::ShirtUpdate)
        .then_some(transfer.shirt_number)
        .flatten();
    LogRecord {
        timestamp: String::new(),
        player_name: non_empty(&matched.matched_player_name, &transfer.player_name),
        player_id: matched.player_id.unwrap_or(0),
        from_team: non_empty(&matched.matched_from_team, &transfer.from_club),
        from_team_id: matched.from.team().unwrap_or(0),
        to_team: non_empty(&matched.matched_to_team, &transfer.to_club),
        to_team_id: matched.to.team().unwrap_or(0),
        confidence: matched.min_confidence(),
        transfer_type: transfer.transfer_type.as_str().to_owned(),
        position: transfer.position.clone(),
        fee: transfer.fee.clone(),
        market_value: transfer.market_value,
        transfer_date: transfer.date.clone(),
        dry_run,
        previous_shirt_number: previous_shirt,
        shirt_number,
        roster_action: action.as_str().to_owned(),
        save_scope: String::new(),
        fotmob_player_id: transfer.player_id_fotmob,
        sortitoutsi_player_id: transfer.player_id_sortitoutsi,
        transfermarkt_player_id: transfer.player_id_transfermarkt,
        transfermarkt_from_club_id: transfer.from_club_id_transfermarkt,
        transfermarkt_to_club_id: transfer.to_club_id_transfermarkt,
        transfermarkt_transfer_id: transfer.transfer_id_transfermarkt,
        sources: transfer.sources.clone(),
        source_urls: transfer.source_urls.clone(),
        proof_urls: transfer.proof_urls.clone(),
        native_metadata,
    }
}

/// Record of one applied captain change.
pub(crate) fn captain_entry(
    planned: &PlannedCaptainUpdate,
    previous_captain: Option<u32>,
    dry_run: bool,
) -> LogRecord {
    let source = &planned.source;
    LogRecord {
        timestamp: String::new(),
        player_name: planned.matched_player_name.clone(),
        player_id: planned.player_id,
        from_team: source.club_name.clone(),
        from_team_id: planned.team_id,
        to_team: source.club_name.clone(),
        to_team_id: planned.team_id,
        confidence: planned.confidence,
        transfer_type: "captain_update".to_owned(),
        position: String::new(),
        fee: String::new(),
        market_value: 0,
        transfer_date: String::new(),
        dry_run,
        previous_shirt_number: None,
        shirt_number: None,
        roster_action: "captain".to_owned(),
        save_scope: String::new(),
        fotmob_player_id: source.player_id_fotmob,
        sortitoutsi_player_id: None,
        transfermarkt_player_id: None,
        transfermarkt_from_club_id: None,
        transfermarkt_to_club_id: None,
        transfermarkt_transfer_id: None,
        sources: vec![source.source.clone()],
        source_urls: if source.source_url.is_empty() {
            Vec::new()
        } else {
            vec![source.source_url.clone()]
        },
        proof_urls: Vec::new(),
        native_metadata: json!({ "previous_captain_player_id": previous_captain }),
    }
}

/// Roster map in the shape planners read: non-zero ids in slot order.
pub(crate) fn roster_map(teams: &[TeamData]) -> crate::planning::RosterMap {
    teams
        .iter()
        .map(|team| (team.team_id, team.roster().collect()))
        .collect()
}
