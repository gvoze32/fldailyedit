//! Player and team name matching against one save's database.
//!
//! Provider names (FotMob, Transfermarkt, ...) are mapped to save player and
//! club ids with exact, roster-context, and fuzzy steps. Metadata (position,
//! nationality, age) only confirms or breaks ties between name candidates; it
//! never replaces name evidence.
//!
//! Results are typed: every [`PlayerMatch`]/[`TeamMatch`] carries a
//! [`MatchStatus`], and `id` is `Some` exactly when the status is
//! [`MatchStatus::Matched`]. `confidence` keeps its established score semantics
//! (the best evidence seen, e.g. 100 for a rejected exact name) so reports are
//! unchanged, but callers must decide on `id`/`status`, never on `confidence`.

mod nationality;
mod player;
mod position;
mod team;

#[cfg(test)]
mod tests;

use std::collections::HashMap;

pub use player::PlayerRecord;

/// Default minimum player-name confidence (0-100).
pub const MATCH_THRESHOLD_PLAYER: f64 = 80.0;
/// Default minimum team-name confidence (0-100).
pub const MATCH_THRESHOLD_TEAM: f64 = 75.0;

/// Database metadata of one player. Empty / 0 = unknown.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct PlayerAttrs {
    pub position: String,
    pub nationality: String,
    pub age: u32,
}

/// Why a match succeeded or not.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MatchStatus {
    Matched,
    EmptyDatabase,
    /// The name is a "free agent"/"retired"-style label, not a club.
    NonClub,
    /// Several players/clubs share the exact (or affix-cleaned) name.
    AmbiguousExact,
    AmbiguousCleaned,
    /// Exact player name, but every candidate's age contradicts the provider's.
    ExactAgeConflict,
    /// Best roster candidate scored under the context minimum.
    WeakContext,
    /// Two roster candidates are too close to call.
    AmbiguousContext,
    /// Two fuzzy candidates are too close to call, even with metadata.
    AmbiguousFuzzy,
    BelowThreshold,
}

macro_rules! match_result {
    ($(#[$meta:meta])* $name:ident) => {
        $(#[$meta])*
        #[derive(Debug, Clone, PartialEq)]
        pub struct $name {
            /// The matched id; `Some` exactly when `status` is `Matched`.
            pub id: Option<u32>,
            /// The matched database name (empty when unmatched).
            pub name: String,
            /// Evidence score, 0-100. Not a success indicator.
            pub confidence: f64,
            pub status: MatchStatus,
        }

        impl $name {
            pub(crate) fn matched(id: u32, name: &str, confidence: f64) -> Self {
                Self { id: Some(id), name: name.to_owned(), confidence, status: MatchStatus::Matched }
            }

            pub(crate) fn unmatched(status: MatchStatus, confidence: f64) -> Self {
                Self { id: None, name: String::new(), confidence, status }
            }
        }
    };
}

match_result!(
    /// Outcome of [`NameMatcher::match_player`].
    PlayerMatch
);
match_result!(
    /// Outcome of [`NameMatcher::match_team`].
    TeamMatch
);

/// One player-name query with its optional context.
#[derive(Debug, Clone, Copy, Default)]
pub struct PlayerQuery<'a> {
    /// Minimum confidence; 0 = [`MATCH_THRESHOLD_PLAYER`].
    pub threshold: f64,
    /// Origin club: its roster is the authoritative context.
    pub from_team_id: Option<u32>,
    /// Destination club: roster context for events the save already reflects.
    pub to_team_id: Option<u32>,
    pub team_player_map: Option<&'a HashMap<u32, Vec<u32>>>,
    /// Provider position label ("" = unknown).
    pub position: &'a str,
    /// Provider nationality ("" = unknown).
    pub nationality: &'a str,
    /// Provider age (0 = unknown).
    pub age: u32,
}

/// Evidence for [`NameMatcher::score_player`].
#[derive(Debug, Clone, Copy, Default)]
pub struct ScoreContext<'a> {
    pub position: &'a str,
    pub candidate_pid: Option<u32>,
    pub nationality: &'a str,
    pub age: u32,
}

/// Matches provider names to the player and club ids of one save.
pub struct NameMatcher {
    players: player::PlayerDb,
    teams: team::TeamDb,
}

impl NameMatcher {
    /// `players`: (name, id) pairs; one id may appear with several names.
    /// `attrs`: metadata by player id. `teams`: (name, id) pairs.
    pub fn new(
        players: impl IntoIterator<Item = (String, u32)>,
        attrs: HashMap<u32, PlayerAttrs>,
        teams: impl IntoIterator<Item = (String, u32)>,
    ) -> Self {
        Self {
            players: player::PlayerDb::new(players, attrs),
            teams: team::TeamDb::new(teams),
        }
    }

    /// Replace the provider-name aliases (alias → save club name).
    pub fn set_team_aliases(&mut self, aliases: impl IntoIterator<Item = (String, String)>) {
        self.teams.set_aliases(aliases);
    }

    /// Match a player name. See the module docs for the result contract.
    pub fn match_player(&self, name: &str, query: &PlayerQuery) -> PlayerMatch {
        self.players.match_player(name, query)
    }

    /// Match a club name; `threshold` `None` (or 0) = [`MATCH_THRESHOLD_TEAM`].
    pub fn match_team(&self, name: &str, threshold: Option<f64>) -> TeamMatch {
        self.teams.match_team(name, threshold)
    }

    /// The canonical loaded club name of an id.
    pub fn team_name(&self, id: u32) -> Option<&str> {
        self.teams.name_of(id)
    }

    /// Every database name that folds exactly to `folded`.
    pub fn exact_player_candidates(&self, folded: &str) -> &[PlayerRecord] {
        self.players.exact_candidates(folded)
    }

    /// Whether the provider age is within four years of the database age
    /// (unknown on either side is compatible).
    pub fn is_player_age_compatible(&self, id: u32, age: u32) -> bool {
        self.players.age_compatible(id, age)
    }

    /// Whether provider position and age do not contradict the database.
    pub fn is_player_metadata_compatible(&self, id: u32, position: &str, age: u32) -> bool {
        self.players.metadata_compatible_raw(id, position, age)
    }

    /// Composite score of one folded query against one folded database name.
    pub fn score_player(&self, query_norm: &str, candidate_norm: &str, ctx: &ScoreContext) -> f64 {
        self.players.score_public(query_norm, candidate_norm, ctx)
    }

    /// Every (folded, original) name loaded for a player id.
    pub fn player_names(&self, id: u32) -> &[(String, String)] {
        self.players.names_of(id)
    }

    /// The database position of a player, stripped and upper-cased ("" = unknown).
    pub fn player_position(&self, id: u32) -> &str {
        self.players.position_of(id)
    }
}
