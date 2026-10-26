//! Roster mutations and game-plan repair on top of [`crate::edit_file::EditFile`].
//!
//! * [`ops`]: `move_player` / `add_player` / `release_player`, shirt numbers,
//!   roster-slot compaction. Every mutation validates first and only then
//!   writes, so a refused operation leaves the bytes untouched.
//! * [`overflow`]: which player a full club releases for an incoming signing.
//! * [`game_plan`]: matchday, captain, tactical settings, formations and the
//!   typed view of one game-plan entry.
//! * [`lineup`]: keeping the lineup consistent when a roster slot is vacated or
//!   filled (promotion, goalkeeper invariants, bench order).
//! * [`starters`]: aligning the XI with the formation roles (optimal
//!   assignment, see [`hungarian`]).
//! * [`roles`]: filling empty captain / set-piece / attack-player roles.
//! * [`repair`]: `repair_game_plans` over every club.
//!
//! Failures are typed ([`RosterError`]); nothing is stored in hidden state.

mod error;
pub mod formation;
mod game_plan;
mod hungarian;
mod lineup;
mod ops;
mod overflow;
mod repair;
mod roles;
pub mod shirt;
mod squad;
mod starters;

#[cfg(test)]
mod parity_tests;
#[cfg(test)]
mod tests;

use std::collections::HashSet;

pub use error::{RosterError, RosterErrorKind};
pub use ops::{AddRequest, MoveRequest, Placement};
pub use overflow::{OverflowCandidate, OverflowExplanation, OverflowQuery, RoleGroup};
pub use repair::{RepairMetrics, RepairOptions};

/// A club keeps at least this many goalkeepers out of the overflow pool.
pub const MIN_GOALKEEPERS: usize = 2;
/// High/reserved ids stay out of the overflow pool while a native reserve exists.
pub const RESERVED_PLAYER_ID_MIN: u32 = 0x10_0000;

/// Per-session mutation state kept by [`crate::edit_file::EditFile`].
#[derive(Debug, Default)]
pub(crate) struct MutationState {
    /// Players signed or moved in this session: protected from overflow
    /// release, and ordered onto the bench by `repair_game_plans`.
    pub(crate) transferred_player_ids: HashSet<u32>,
}
