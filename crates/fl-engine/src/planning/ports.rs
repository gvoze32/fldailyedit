//! What planning needs from the matcher, the club-identity index and the save.
//!
//! Narrow traits instead of the concrete types so planning compiles and tests
//! without a player catalog, and so every optional probe of a matcher has one
//! explicit, defaulted method.

use std::collections::HashSet;

use fl_save::roster::OverflowExplanation;

/// A club match: `id` is `None` when no save club matched; `confidence` is
/// meaningful either way.
#[derive(Debug, Clone, PartialEq)]
pub struct TeamHit {
    pub id: Option<u32>,
    pub name: String,
    pub confidence: f64,
}

impl TeamHit {
    pub fn none(confidence: f64) -> Self {
        Self {
            id: None,
            name: String::new(),
            confidence,
        }
    }
}

/// A player match, shaped like [`TeamHit`].
#[derive(Debug, Clone, PartialEq)]
pub struct PlayerHit {
    pub id: Option<u32>,
    pub name: String,
    pub confidence: f64,
}

impl PlayerHit {
    pub fn none(confidence: f64) -> Self {
        Self {
            id: None,
            name: String::new(),
            confidence,
        }
    }
}

pub use crate::club_identity::NameResolution as Resolution;
pub use crate::matcher::{PlayerQuery, PlayerRecord, ScoreContext};

/// Club-name matching against the save's playable clubs.
pub trait TeamMatcher {
    /// Best save club for `name`. `threshold: None` uses the matcher default.
    fn match_team(&self, name: &str, threshold: Option<f64>) -> TeamHit;

    /// Display name of a save club; empty when unknown.
    fn team_name(&self, team_id: u32) -> &str;
}

/// Player matching and the metadata probes snapshot identity resolution uses.
pub trait PlayerMatcher: TeamMatcher {
    fn match_player(&self, name: &str, query: &PlayerQuery<'_>) -> PlayerHit;

    /// Catalog `(name, id)` records whose folded name equals `folded_name`.
    fn exact_player_candidates(&self, _folded_name: &str) -> &[PlayerRecord] {
        &[]
    }

    fn is_player_age_compatible(&self, _player_id: u32, _age: u32) -> bool {
        true
    }

    fn is_player_metadata_compatible(&self, _player_id: u32, _position: &str, _age: u32) -> bool {
        true
    }

    fn score_player(
        &self,
        _query_norm: &str,
        _candidate_norm: &str,
        _ctx: &ScoreContext<'_>,
    ) -> f64 {
        0.0
    }

    /// `(folded, display)` names of a player.
    fn player_names(&self, _player_id: u32) -> &[(String, String)] {
        &[]
    }

    /// Registered position label; empty when unknown.
    fn player_position(&self, _player_id: u32) -> &str {
        ""
    }
}

/// Learned FotMob ↔ save club bindings plus save-name lookup.
pub trait ClubIdentity {
    fn pes_for_fotmob(&self, fotmob_id: i64) -> Option<u32>;
    fn fotmob_for_pes(&self, team_id: u32) -> Option<i64>;
    fn resolve_name(&self, name: &str) -> Resolution;
    /// `(fotmob_id, save team id)` bindings.
    fn bindings(&self) -> Vec<(i64, u32)>;
}

/// Chooses the player a full club releases for an incoming signing.
pub trait OverflowSelector {
    /// Best release candidate at `team_id` for `incoming`, given a virtual
    /// 40-slot roster (0 = empty) and extra protected players.
    fn find_candidate(
        &self,
        team_id: u32,
        incoming: u32,
        roster: &[u32],
        protected: &HashSet<u32>,
    ) -> Option<u32>;

    /// Why `player_id` is the candidate; `None` when it is not.
    fn describe(
        &self,
        _team_id: u32,
        _player_id: u32,
        _roster: &[u32],
        _protected: &HashSet<u32>,
    ) -> Option<OverflowExplanation> {
        None
    }
}

/// Read access to the current shirt numbers of a club.
pub trait RosterReader {
    /// `(player ids, shirt numbers)` per slot (0 = empty), `None` for an
    /// unknown team.
    fn roster(&self, team_id: u32) -> Option<(Vec<u32>, Vec<u32>)>;
}
