//! Typed roster-mutation failures.

use std::fmt;

/// Why a mutation was refused. [`as_str`](Self::as_str) is the stable code
/// used in skip reports; kinds that have no counterpart in the earlier
/// tooling get their own descriptive code.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum RosterErrorKind {
    SameTeam,
    /// Player id 0 marks an empty roster slot, not a player.
    InvalidPlayerId,
    SourceTeamMissing,
    DestinationTeamMissing,
    SourcePlayerMissing,
    DestinationPlayerExists,
    DuplicateClubRegistration,
    OverflowNotAuthorized,
    OverflowCandidateStale,
    NoSafeOverflowCandidate,
    /// The player is the source club's last goalkeeper (nobody else is a
    /// goalkeeper or has an unknown position): the club's game plan could not
    /// keep a valid goalkeeper role.
    LastGoalkeeper,
    DestinationNoEmptySlot,
    TeamMissing,
    PlayerNotOnTeam,
    InvalidShirtNumber,
    ShirtConflict,
    GamePlanNotFound,
    UnsupportedFormation,
    InvalidTacticalSetting,
    CaptainNotOnRoster,
}

impl RosterErrorKind {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::SameTeam => "same_team",
            Self::InvalidPlayerId => "invalid_player_id",
            Self::SourceTeamMissing => "source_team_missing",
            Self::DestinationTeamMissing => "destination_team_missing",
            Self::SourcePlayerMissing => "source_player_missing",
            Self::DestinationPlayerExists => "destination_player_exists",
            Self::DuplicateClubRegistration => "duplicate_club_registration",
            Self::OverflowNotAuthorized => "overflow_not_authorized",
            Self::OverflowCandidateStale => "overflow_candidate_stale",
            Self::NoSafeOverflowCandidate => "no_safe_overflow_candidate",
            Self::LastGoalkeeper => "last_goalkeeper",
            Self::DestinationNoEmptySlot => "destination_no_empty_slot",
            Self::TeamMissing => "team_missing",
            Self::PlayerNotOnTeam => "player_not_on_team",
            Self::InvalidShirtNumber => "invalid_shirt_number",
            Self::ShirtConflict => "shirt_conflict",
            Self::GamePlanNotFound => "game_plan_not_found",
            Self::UnsupportedFormation => "unsupported_formation",
            Self::InvalidTacticalSetting => "invalid_tactical_setting",
            Self::CaptainNotOnRoster => "captain_not_on_roster",
        }
    }
}

/// A refused mutation: the bytes were not touched.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RosterError {
    kind: RosterErrorKind,
    detail: String,
}

impl RosterError {
    pub(crate) fn new(kind: RosterErrorKind, detail: impl Into<String>) -> Self {
        Self {
            kind,
            detail: detail.into(),
        }
    }

    pub fn kind(&self) -> RosterErrorKind {
        self.kind
    }

    /// Stable machine code, see [`RosterErrorKind::as_str`].
    pub fn code(&self) -> &'static str {
        self.kind.as_str()
    }

    /// Human-readable explanation (the text shown in skip reports).
    pub fn detail(&self) -> &str {
        &self.detail
    }
}

impl fmt::Display for RosterError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.detail)
    }
}

impl std::error::Error for RosterError {}
