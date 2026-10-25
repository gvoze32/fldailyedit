//! Types shared by every planning stage.

use std::collections::HashMap;

use fl_save::roster::OverflowExplanation;
use fl_sources::model::{Transfer, TransferType};

/// Roster lists per team (non-zero player ids in slot order), the shape the
/// matcher reads as context.
pub type RosterMap = HashMap<u32, Vec<u32>>;

/// Where one side of a transfer sits relative to the save.
///
/// A side is either confidently outside the save, not confidently resolved,
/// or a save team.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Default)]
pub enum Side {
    /// Free agent or a club the save does not contain.
    #[default]
    Outside,
    /// The name or id could not be pinned to one save club. Never treated as
    /// "outside": that would turn a move into a save club into a release.
    Unresolved,
    Team(u32),
}

impl Side {
    pub fn team(self) -> Option<u32> {
        match self {
            Self::Team(id) => Some(id),
            _ => None,
        }
    }

    pub fn is_team(self) -> bool {
        matches!(self, Self::Team(_))
    }

    pub fn is_outside(self) -> bool {
        self == Self::Outside
    }

    pub fn is_unresolved(self) -> bool {
        self == Self::Unresolved
    }
}

/// Why matching refused to produce an actionable event.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MatchSkip {
    pub reason: String,
    pub detail: String,
    pub candidates: Vec<String>,
}

/// A transfer event bound to save ids.
///
/// Owns its [`Transfer`]: inferred sources rewrite the owned copy and the
/// scraped input is never mutated.
#[derive(Debug, Clone, PartialEq)]
pub struct MatchedTransfer {
    pub transfer: Transfer,
    pub player_id: Option<u32>,
    pub from: Side,
    pub to: Side,
    pub player_confidence: f64,
    pub from_confidence: f64,
    pub to_confidence: f64,
    /// Save names the ids were matched to.
    pub matched_player_name: String,
    pub matched_from_team: String,
    pub matched_to_team: String,
    /// Set by the matching stage when it already knows why the event cannot
    /// be applied.
    pub skip: Option<MatchSkip>,
}

impl MatchedTransfer {
    /// An unmatched event: no player, both sides outside the save.
    pub fn new(transfer: Transfer) -> Self {
        Self {
            transfer,
            player_id: None,
            from: Side::Outside,
            to: Side::Outside,
            player_confidence: 0.0,
            from_confidence: 0.0,
            to_confidence: 0.0,
            matched_player_name: String::new(),
            matched_from_team: String::new(),
            matched_to_team: String::new(),
            skip: None,
        }
    }

    pub fn is_shirt_update(&self) -> bool {
        self.transfer.transfer_type == TransferType::ShirtNumberUpdate
    }

    /// Loan by flag or by type.
    pub fn is_loan(&self) -> bool {
        self.transfer.is_loan || self.transfer.transfer_type == TransferType::Loan
    }

    /// Both clubs are save teams.
    pub fn is_club_transfer(&self) -> bool {
        self.player_id.is_some() && self.from.is_team() && self.to.is_team()
    }

    /// Source is a save team, destination is a free agent or foreign club.
    pub fn is_release(&self) -> bool {
        self.player_id.is_some() && self.from.is_team() && self.to.is_outside()
    }

    /// Destination is a save team, source a free agent or foreign club.
    pub fn is_sign(&self) -> bool {
        self.player_id.is_some() && self.from.is_outside() && self.to.is_team()
    }

    /// The player and at least one valid roster action are known.
    pub fn is_fully_matched(&self) -> bool {
        self.is_club_transfer()
            || self.is_release()
            || self.is_sign()
            || (self.is_shirt_update() && self.player_id.is_some() && self.to.is_team())
    }

    pub fn action_type(&self) -> &'static str {
        if self.is_shirt_update() {
            "shirt_number_update"
        } else if self.is_club_transfer() {
            "transfer"
        } else if self.is_release() {
            "release_to_free_agent"
        } else if self.is_sign() {
            "sign_from_free_agent"
        } else {
            "unmatched"
        }
    }

    /// Lowest confidence among the matched components.
    pub fn min_confidence(&self) -> f64 {
        let mut lowest = self.player_confidence;
        if self.from.is_team() {
            lowest = lowest.min(self.from_confidence);
        }
        if self.to.is_team() {
            lowest = lowest.min(self.to_confidence);
        }
        lowest
    }
}

/// Roster mutation chosen for one event.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum RosterAction {
    Skip,
    /// The save already reflects the event.
    Noop,
    ShirtUpdate,
    Move,
    Add,
    Release,
}

impl RosterAction {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Skip => "skip",
            Self::Noop => "noop",
            Self::ShirtUpdate => "shirt_update",
            Self::Move => "move",
            Self::Add => "add",
            Self::Release => "release",
        }
    }
}

/// One planned step with the simulated state it was decided against.
#[derive(Debug, Clone, PartialEq)]
pub struct PlannedRosterAction {
    pub matched: MatchedTransfer,
    pub action: RosterAction,
    /// The club the save registers the player at when the step was decided.
    pub current_team_id: Option<u32>,
    /// Reason code; set for every skip (and for noop/skip decisions that
    /// carry one).
    pub reason: String,
    pub detail: String,
    /// Player a full destination releases to make room.
    pub overflow_player_id: Option<u32>,
    pub overflow: Option<OverflowExplanation>,
}

impl PlannedRosterAction {
    pub fn new(
        matched: MatchedTransfer,
        action: RosterAction,
        current_team_id: Option<u32>,
    ) -> Self {
        Self {
            matched,
            action,
            current_team_id,
            reason: String::new(),
            detail: String::new(),
            overflow_player_id: None,
            overflow: None,
        }
    }
}

/// One entry of the persisted transfer log, as the planner replays it.
///
/// Ids are `None` when missing or not positive.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct HistoryEntry {
    pub player_id: Option<u32>,
    pub from_team_id: Option<u32>,
    pub to_team_id: Option<u32>,
    pub fotmob_player_id: Option<i64>,
    pub player_name: String,
    /// As logged; compared case-insensitively.
    pub transfer_type: String,
    pub transfer_date: String,
    pub timestamp: String,
}

impl HistoryEntry {
    /// Event time text: the transfer date, else the log timestamp.
    pub fn when(&self) -> &str {
        if self.transfer_date.is_empty() {
            &self.timestamp
        } else {
            &self.transfer_date
        }
    }

    pub fn type_lower(&self) -> String {
        self.transfer_type.to_lowercase()
    }
}
