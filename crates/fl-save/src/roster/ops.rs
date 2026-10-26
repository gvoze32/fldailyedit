//! Roster mutations: move / add / release a player and batch shirt numbers.
//!
//! Every mutation first validates and plans against the current bytes and
//! only then writes, so an `Err` leaves the save untouched. The only state
//! outside the bytes is the session's set of transferred players, which is
//! updated on success only.

use std::collections::{HashMap, HashSet};

use crate::edit_file::EditFile;
use crate::layout::*;
use crate::model::{Position, TeamData};

use super::error::{RosterError, RosterErrorKind};
use super::lineup::{
    Removal, compact_lineup_after_removal, insert_after_addition, remap_roles_after_removal,
};
use super::overflow::OverflowQuery;
use super::shirt::assign_smart_shirt_number;
use super::squad::{PositionOverrides, Squad};

/// Move a player between two clubs.
#[derive(Debug, Clone)]
pub struct MoveRequest<'a> {
    pub player_id: u32,
    pub from_team_id: u32,
    pub to_team_id: u32,
    /// Preferred kit number; defaults to the player's current one.
    pub preferred_shirt_number: Option<u32>,
    /// Position label of the signing (feed label or registered position).
    pub position: &'a str,
    /// Whether a full destination may release a reserve.
    pub allow_overflow_release: bool,
    /// Candidate selected by the verified roster plan; it must still be on
    /// the destination roster.
    pub planned_overflow_player_id: Option<u32>,
}

impl<'a> MoveRequest<'a> {
    pub fn new(player_id: u32, from_team_id: u32, to_team_id: u32) -> Self {
        Self {
            player_id,
            from_team_id,
            to_team_id,
            preferred_shirt_number: None,
            position: "",
            allow_overflow_release: true,
            planned_overflow_player_id: None,
        }
    }
}

/// Sign a free agent.
#[derive(Debug, Clone)]
pub struct AddRequest<'a> {
    pub player_id: u32,
    pub to_team_id: u32,
    pub preferred_shirt_number: Option<u32>,
    pub position: &'a str,
    pub allow_overflow_release: bool,
    /// Players that must not be auto-released to make room.
    pub protected_player_ids: Option<&'a HashSet<u32>>,
    pub planned_overflow_player_id: Option<u32>,
}

impl<'a> AddRequest<'a> {
    pub fn new(player_id: u32, to_team_id: u32) -> Self {
        Self {
            player_id,
            to_team_id,
            preferred_shirt_number: None,
            position: "",
            allow_overflow_release: true,
            protected_player_ids: None,
            planned_overflow_player_id: None,
        }
    }
}

/// Where a moved or signed player ended up.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Placement {
    pub slot: usize,
    pub shirt_number: u32,
    /// The player released to a full destination's free-agent pool.
    pub released: Option<u32>,
}

/// The position a signing is registered with: a label (for shirt numbering)
/// and the position it names.
struct Effective {
    label: String,
    position: Option<Position>,
}

impl EditFile {
    /// Forget the session: players moved or signed so far lose their overflow
    /// protection and bench priority, so the next batch of mutations behaves
    /// like one on a freshly opened save.
    pub fn reset_session(&mut self) {
        self.mutation = super::MutationState::default();
    }

    /// Players moved or signed in this session.
    pub fn transferred_player_ids(&self) -> &HashSet<u32> {
        &self.mutation.transferred_player_ids
    }

    /// A player's current shirt number on a team.
    pub fn player_shirt_number(&self, team_id: u32, player_id: u32) -> Option<u16> {
        self.roster(team_id)?.shirt_number_of(player_id)
    }

    /// Transfer a player between clubs.
    ///
    /// The player leaves the source roster (compacting its slots), joins the
    /// first empty destination slot with a conflict-free shirt number, and the
    /// game plans are updated. A full destination releases one player first
    /// (the planned candidate, else the safest one).
    pub fn move_player(&mut self, request: &MoveRequest<'_>) -> Result<Placement, RosterError> {
        let MoveRequest {
            player_id,
            from_team_id,
            to_team_id,
            ..
        } = *request;
        if from_team_id == to_team_id {
            return Err(RosterError::new(
                RosterErrorKind::SameTeam,
                format!("Source and destination are the same team ({from_team_id})"),
            ));
        }
        let from_entry = self.team_player_entry_offset(from_team_id).ok_or_else(|| {
            RosterError::new(
                RosterErrorKind::SourceTeamMissing,
                format!("Source team {from_team_id} not found in Team-Player table"),
            )
        })?;
        let to_entry = self.team_player_entry_offset(to_team_id).ok_or_else(|| {
            RosterError::new(
                RosterErrorKind::DestinationTeamMissing,
                format!("Destination team {to_team_id} not found in Team-Player table"),
            )
        })?;
        let from_roster = self.team_at(from_entry);
        let to_roster = self.team_at(to_entry);

        let player_index = from_roster.player_index(player_id).ok_or_else(|| {
            RosterError::new(
                RosterErrorKind::SourcePlayerMissing,
                format!("Player {player_id} not found on team {from_team_id}"),
            )
        })?;
        if to_roster.has_player(player_id) {
            return Err(RosterError::new(
                RosterErrorKind::DestinationPlayerExists,
                format!("Player {player_id} already on destination team {to_team_id}"),
            ));
        }
        self.refuse_last_goalkeeper(&from_roster, from_team_id, player_id)?;
        // A player may also be registered for a national team, but must never
        // occur in two club rosters: refuse to amplify a corrupt input.
        let unexpected: Vec<u32> = self
            .find_player_teams(player_id, true)
            .into_iter()
            .filter(|&team| team != from_team_id)
            .collect();
        if !unexpected.is_empty() {
            return Err(RosterError::new(
                RosterErrorKind::DuplicateClubRegistration,
                format!(
                    "Player {player_id} is already registered to club(s) {unexpected:?}; transfer aborted"
                ),
            ));
        }

        let preferred_shirt = request.preferred_shirt_number.or_else(|| {
            let current = from_roster.shirt_numbers[player_index];
            (current > 0).then_some(u32::from(current))
        });
        let overflow = self.resolve_overflow(
            &to_roster,
            to_team_id,
            &OverflowRequest {
                player_id,
                allowed: request.allow_overflow_release,
                planned: request.planned_overflow_player_id,
                protected: None,
                unauthorized: format!(
                    "Destination team {to_team_id} is full (40/40); overflow release was not authorized"
                ),
            },
        )?;
        let destination_slot = destination_slot(&to_roster, overflow).ok_or_else(|| {
            RosterError::new(
                RosterErrorKind::DestinationNoEmptySlot,
                format!("No empty slot in destination team {to_team_id}"),
            )
        })?;

        // Validated: everything below is infallible.
        let effective = self.effective_position(player_id, request.position);
        if let Some(released) = overflow {
            self.release_from(to_team_id, to_entry, &to_roster, released);
        }
        self.remove_roster_player(
            from_team_id,
            from_entry,
            &from_roster,
            player_index,
            player_id,
        );
        Ok(self.place_player(
            to_team_id,
            to_entry,
            player_id,
            destination_slot,
            preferred_shirt,
            &effective,
            overflow,
        ))
    }

    /// Sign a free agent into a club (see [`move_player`](Self::move_player)
    /// for overflow and shirt handling). The player must not be registered to
    /// any club.
    pub fn add_player(&mut self, request: &AddRequest<'_>) -> Result<Placement, RosterError> {
        let AddRequest {
            player_id,
            to_team_id,
            ..
        } = *request;
        if player_id == 0 {
            return Err(RosterError::new(
                RosterErrorKind::InvalidPlayerId,
                "Player id 0 marks an empty roster slot and cannot be signed",
            ));
        }
        let to_entry = self.team_player_entry_offset(to_team_id).ok_or_else(|| {
            RosterError::new(
                RosterErrorKind::DestinationTeamMissing,
                format!("Team {to_team_id} not found in Team-Player table"),
            )
        })?;
        let to_roster = self.team_at(to_entry);
        if to_roster.has_player(player_id) {
            return Err(RosterError::new(
                RosterErrorKind::DestinationPlayerExists,
                format!("Player {player_id} already on team {to_team_id}"),
            ));
        }
        let clubs = self.find_player_teams(player_id, true);
        if !clubs.is_empty() {
            return Err(RosterError::new(
                RosterErrorKind::DuplicateClubRegistration,
                format!(
                    "Player {player_id} is already registered to club(s) {clubs:?}; cannot add to team {to_team_id} as a free agent"
                ),
            ));
        }
        let overflow = self.resolve_overflow(
            &to_roster,
            to_team_id,
            &OverflowRequest {
                player_id,
                allowed: request.allow_overflow_release,
                planned: request.planned_overflow_player_id,
                protected: request.protected_player_ids,
                unauthorized: format!(
                    "Team {to_team_id} roster is full (40/40); overflow release was not authorized"
                ),
            },
        )?;
        let destination_slot = destination_slot(&to_roster, overflow).ok_or_else(|| {
            RosterError::new(
                RosterErrorKind::DestinationNoEmptySlot,
                format!("Team {to_team_id} roster is full (40 players)"),
            )
        })?;

        let effective = self.effective_position(player_id, request.position);
        if let Some(released) = overflow {
            self.release_from(to_team_id, to_entry, &to_roster, released);
        }
        Ok(self.place_player(
            to_team_id,
            to_entry,
            player_id,
            destination_slot,
            request.preferred_shirt_number,
            &effective,
            overflow,
        ))
    }

    /// Release a player to the free-agent pool, compacting the roster slots.
    pub fn release_player(&mut self, player_id: u32, from_team_id: u32) -> Result<(), RosterError> {
        let entry = self.team_player_entry_offset(from_team_id).ok_or_else(|| {
            RosterError::new(
                RosterErrorKind::SourceTeamMissing,
                format!("Team {from_team_id} not found in Team-Player table"),
            )
        })?;
        let roster = self.team_at(entry);
        let index = roster.player_index(player_id).ok_or_else(|| {
            RosterError::new(
                RosterErrorKind::SourcePlayerMissing,
                format!("Player {player_id} not found on team {from_team_id}"),
            )
        })?;
        self.refuse_last_goalkeeper(&roster, from_team_id, player_id)?;
        self.remove_roster_player(from_team_id, entry, &roster, index, player_id);
        Ok(())
    }

    /// Apply several shirt-number changes at once (swaps allowed). Every
    /// request is validated first: numbers in `1..=999`, no conflicting or
    /// shared requests, and no collision with a player that keeps their
    /// number.
    pub fn update_player_shirt_numbers(
        &mut self,
        team_id: u32,
        updates: &[(u32, u32)],
    ) -> Result<(), RosterError> {
        if updates.is_empty() {
            return Ok(());
        }
        let entry = self.team_player_entry_offset(team_id).ok_or_else(|| {
            RosterError::new(
                RosterErrorKind::TeamMissing,
                format!("Team {team_id} not found in Team-Player table"),
            )
        })?;
        let roster = self.team_at(entry);
        let shirt_error = |kind, detail: String| Err(RosterError::new(kind, detail));

        // (player, slot, number) in request order.
        let mut planned: Vec<(u32, usize, u32)> = Vec::with_capacity(updates.len());
        let mut requested: HashMap<u32, u32> = HashMap::new();
        for &(player_id, number) in updates {
            if !(1..=u32::from(MAX_SHIRT_NUMBER)).contains(&number) {
                return shirt_error(
                    RosterErrorKind::InvalidShirtNumber,
                    format!("Invalid shirt number {number}; expected 1..999"),
                );
            }
            if let Some(&(_, _, existing)) = planned.iter().find(|(id, _, _)| *id == player_id) {
                if existing != number {
                    return shirt_error(
                        RosterErrorKind::ShirtConflict,
                        format!(
                            "Conflicting shirt updates requested for player {player_id} on team {team_id}"
                        ),
                    );
                }
                continue;
            }
            let Some(slot) = roster.player_index(player_id) else {
                return shirt_error(
                    RosterErrorKind::PlayerNotOnTeam,
                    format!("Player {player_id} not found on team {team_id}"),
                );
            };
            if requested
                .get(&number)
                .is_some_and(|&other| other != player_id)
            {
                return shirt_error(
                    RosterErrorKind::ShirtConflict,
                    format!("Shirt #{number} requested by multiple players on team {team_id}"),
                );
            }
            planned.push((player_id, slot, number));
            requested.insert(number, player_id);
        }

        let updating: HashSet<u32> = planned.iter().map(|&(id, _, _)| id).collect();
        for &(_, slot, number) in &planned {
            let taken = (0..TP_MAX_PLAYERS).any(|other| {
                other != slot
                    && roster.player_ids[other] != 0
                    && u32::from(roster.shirt_numbers[other]) == number
                    && !updating.contains(&roster.player_ids[other])
            });
            if taken {
                return shirt_error(
                    RosterErrorKind::ShirtConflict,
                    format!("Shirt #{number} is already used on team {team_id}"),
                );
            }
        }

        for (player_id, slot, number) in planned {
            self.write_slot(entry, slot, player_id, number as u16);
        }
        Ok(())
    }

    // ── internals ────────────────────────────────────────────────────────

    /// A club must keep someone who can stand in role zero: its game plan
    /// carries one goalkeeper position code, and an outfield player holding it
    /// fails the goalkeeper-code integrity checks. Removing a registered
    /// goalkeeper is refused when nobody else is a goalkeeper or has an
    /// unknown position (integrity validation does not judge those); a
    /// replacement signed earlier in the same batch counts.
    fn refuse_last_goalkeeper(
        &self,
        roster: &TeamData,
        team_id: u32,
        player_id: u32,
    ) -> Result<(), RosterError> {
        if self.player_position(player_id) != Some(Position::Gk) {
            return Ok(());
        }
        let can_keep_goal = |id: u32| {
            self.player_position(id)
                .is_none_or(|position| position == Position::Gk)
        };
        if roster
            .roster()
            .any(|other| other != player_id && can_keep_goal(other))
        {
            return Ok(());
        }
        Err(RosterError::new(
            RosterErrorKind::LastGoalkeeper,
            format!("Player {player_id} is the last goalkeeper of team {team_id}"),
        ))
    }

    fn team_at(&self, entry: usize) -> TeamData {
        crate::edit_file::read_team_player_entry(self.bytes(), entry)
    }

    /// Native position first, then the feed label, then the catalog.
    fn effective_position(&self, player_id: u32, supplied: &str) -> Effective {
        if let Some(position) = self.native_position(player_id) {
            return Effective {
                label: position.name().to_owned(),
                position: Some(position),
            };
        }
        let supplied = fl_text::strip(supplied);
        if !supplied.is_empty() {
            return Effective {
                label: supplied.to_owned(),
                position: Position::from_label(supplied),
            };
        }
        match self.player_position(player_id) {
            Some(position) => Effective {
                label: position.name().to_owned(),
                position: Some(position),
            },
            None => Effective {
                label: String::new(),
                position: None,
            },
        }
    }

    /// Decide which player a full destination releases. `Ok(None)` when the
    /// destination has room.
    fn resolve_overflow(
        &self,
        roster: &TeamData,
        team_id: u32,
        request: &OverflowRequest<'_>,
    ) -> Result<Option<u32>, RosterError> {
        if !roster.is_full() {
            return Ok(None);
        }
        if !request.allowed {
            return Err(RosterError::new(
                RosterErrorKind::OverflowNotAuthorized,
                request.unauthorized.clone(),
            ));
        }
        let candidate = match request.planned {
            Some(planned) => {
                if planned == request.player_id || !roster.has_player(planned) {
                    return Err(RosterError::new(
                        RosterErrorKind::OverflowCandidateStale,
                        format!(
                            "Planned overflow player {planned} is not present on destination team {team_id}"
                        ),
                    ));
                }
                Some(planned)
            }
            None => self
                .find_overflow_release_candidate(
                    team_id,
                    OverflowQuery {
                        exclude_player_id: Some(request.player_id),
                        roster_player_ids: None,
                        protected_player_ids: request.protected,
                    },
                )
                .map(|candidate| candidate.player_id),
        };
        candidate.map(Some).ok_or_else(|| {
            RosterError::new(
                RosterErrorKind::NoSafeOverflowCandidate,
                format!("No safe overflow release candidate for team {team_id}"),
            )
        })
    }

    /// Release the overflow casualty of a full destination.
    fn release_from(&mut self, team_id: u32, entry: usize, roster: &TeamData, player_id: u32) {
        if let Some(index) = roster.player_index(player_id) {
            self.remove_roster_player(team_id, entry, roster, index, player_id);
        }
    }

    /// Write the signing into its slot and register it in the game plan.
    #[allow(clippy::too_many_arguments)]
    fn place_player(
        &mut self,
        team_id: u32,
        entry: usize,
        player_id: u32,
        slot: usize,
        preferred_shirt: Option<u32>,
        effective: &Effective,
        released: Option<u32>,
    ) -> Placement {
        let roster = self.team_at(entry);
        let used: HashSet<u32> = (0..TP_MAX_PLAYERS)
            .filter(|&s| roster.player_ids[s] != 0 && roster.shirt_numbers[s] > 0)
            .map(|s| u32::from(roster.shirt_numbers[s]))
            .collect();
        let is_goalkeeper = effective.position == Some(Position::Gk);
        let shirt =
            assign_smart_shirt_number(&used, preferred_shirt, &effective.label, is_goalkeeper);

        self.mutation.transferred_player_ids.insert(player_id);
        self.write_slot(entry, slot, player_id, shirt as u16);
        self.update_game_plan_after_addition(team_id, slot, player_id, effective);
        Placement {
            slot,
            shirt_number: shirt,
            released,
        }
    }

    /// Write a player id and shirt number into a roster slot.
    fn write_slot(&mut self, entry: usize, slot: usize, player_id: u32, shirt: u16) {
        debug_assert!(slot < TP_MAX_PLAYERS);
        let id_at = entry + TP_PLAYER_IDS + slot * 4;
        let shirt_at = entry + TP_SHIRT_NUMBERS + slot * 2;
        let data = self.data_mut();
        data[id_at..id_at + 4].copy_from_slice(&player_id.to_le_bytes());
        data[shirt_at..shirt_at + 2].copy_from_slice(&shirt.to_le_bytes());
    }

    /// Compact one roster slot (the last active player moves into it) and keep
    /// the game-plan roles attached to their players.
    fn remove_roster_player(
        &mut self,
        team_id: u32,
        entry: usize,
        roster: &TeamData,
        index: usize,
        player_id: u32,
    ) {
        let last = (0..TP_MAX_PLAYERS)
            .rev()
            .find(|&slot| roster.player_ids[slot] != 0)
            .unwrap_or(index);
        let replacement = (last > index).then_some(last);
        match replacement {
            Some(last) => {
                self.write_slot(
                    entry,
                    index,
                    roster.player_ids[last],
                    roster.shirt_numbers[last],
                );
                self.write_slot(entry, last, 0, 0);
            }
            None => self.write_slot(entry, index, 0, 0),
        }
        self.update_game_plan_after_removal(team_id, index, replacement, player_id);
    }

    fn update_game_plan_after_removal(
        &mut self,
        team_id: u32,
        removed_idx: usize,
        replacement_idx: Option<usize>,
        removed_player_id: u32,
    ) {
        let Some(mut plan) = self.read_game_plan(team_id) else {
            return;
        };
        // Role bytes follow their players even when the lineup itself cannot
        // be compacted (a non-standard lineup is preserved as it is).
        remap_roles_after_removal(&mut plan, removed_idx, replacement_idx);
        if let Some(roster) = self.roster(team_id) {
            let squad = Squad::build(self, roster);
            compact_lineup_after_removal(
                &mut plan,
                &squad,
                &Removal {
                    removed_idx,
                    replacement_idx,
                    removed_position: self.player_position(removed_player_id),
                },
            );
        }
        self.write_game_plan(team_id, &plan);
    }

    fn update_game_plan_after_addition(
        &mut self,
        team_id: u32,
        added_slot: usize,
        added_player_id: u32,
        effective: &Effective,
    ) {
        let Some(mut plan) = self.read_game_plan(team_id) else {
            return;
        };
        let Some(roster) = self.roster(team_id) else {
            return;
        };
        let mut overrides = PositionOverrides::new();
        if added_player_id != 0 && !effective.label.is_empty() {
            overrides.insert(added_player_id, effective.position);
        }
        let squad = Squad::build(self, roster);
        insert_after_addition(&mut plan, &squad, added_slot, &overrides);
        self.write_game_plan(team_id, &plan);
    }
}

struct OverflowRequest<'a> {
    player_id: u32,
    allowed: bool,
    planned: Option<u32>,
    protected: Option<&'a HashSet<u32>>,
    unauthorized: String,
}

/// The slot a signing lands in once `released` (if any) has left `roster`.
fn destination_slot(roster: &TeamData, released: Option<u32>) -> Option<usize> {
    let mut after = roster.clone();
    if let Some(index) = released.and_then(|id| roster.player_index(id)) {
        let last = (0..TP_MAX_PLAYERS)
            .rev()
            .find(|&slot| after.player_ids[slot] != 0)?;
        if last > index {
            after.player_ids[index] = after.player_ids[last];
            after.shirt_numbers[index] = after.shirt_numbers[last];
            after.player_ids[last] = 0;
            after.shirt_numbers[last] = 0;
        } else {
            after.player_ids[index] = 0;
            after.shirt_numbers[index] = 0;
        }
    }
    after.first_empty_slot()
}
