//! Integrity validation of a supported PES edit-file layout: table bounds and
//! cross-table roster / game-plan invariants.
//!
//! Findings are structured ([`IntegrityError`], [`IntegrityWarning`]); their
//! `Display` text is the user-facing message; the pipeline compares it between
//! the input and the mutated save.

use std::collections::{BTreeMap, HashMap, HashSet};
use std::fmt;

use crate::edit_file::{EditFile, entry_offsets, read_team_player_entry, read_u32};
use crate::layout::*;
use crate::model::{Position, TeamData};

/// A violated invariant; the save must not be used.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum IntegrityError {
    DataSize {
        actual: usize,
        expected: usize,
    },
    CountExceedsCapacity {
        table: &'static str,
        count: usize,
        capacity: usize,
    },
    PlayerEntryOutOfBounds {
        index: usize,
    },
    DuplicateEditedPlayer {
        player_id: u32,
    },
    TeamEntryOutOfBounds {
        index: usize,
    },
    DuplicateTeamEntry {
        team_id: u32,
    },
    TeamPlayerEntryOutOfBounds {
        index: usize,
    },
    RosterUnknownTeam {
        team_id: u32,
    },
    DuplicateRoster {
        team_id: u32,
    },
    RosterHoles {
        team_id: u32,
        slots: Vec<usize>,
    },
    RosterDuplicatePlayer {
        team_id: u32,
    },
    ShirtAboveMax {
        team_id: u32,
    },
    DuplicateShirts {
        team_id: u32,
    },
    NoClubs,
    ClubUndersized {
        team_id: u32,
        size: usize,
    },
    MultiClubPlayer {
        player_id: u32,
        team_ids: Vec<u32>,
    },
    GamePlanEntryOutOfBounds {
        index: usize,
    },
    DuplicateGamePlan {
        team_id: u32,
    },
    GamePlanUnknownTeam {
        team_id: u32,
    },
    GamePlanActivePrefix {
        team_id: u32,
    },
    GamePlanStarters {
        team_id: u32,
    },
    GamePlanPositionArrayOutOfBounds {
        team_id: u32,
        relative_offset: usize,
    },
    GamePlanGoalkeeperCodes {
        team_id: u32,
        preset: usize,
        phase: usize,
        count: usize,
    },
    GamePlanGoalkeeperAssignedCode {
        team_id: u32,
        preset: usize,
        phase: usize,
        player_id: u32,
        code: u8,
    },
    GamePlanOutfielderGoalkeeperCode {
        team_id: u32,
        preset: usize,
        phase: usize,
        position: Position,
        player_id: u32,
    },
    GamePlanRoleInvalidSlot {
        team_id: u32,
        role_offset: usize,
        value: u8,
    },
    GamePlanRoleEmptySlot {
        team_id: u32,
        role_offset: usize,
        slot: u8,
    },
}

/// A suspicious but tolerated condition.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum IntegrityWarning {
    /// PES 2021 can retain stale shirt numbers in unused slots; the game ignores them.
    ShirtOnEmptySlot { team_id: u32 },
    EmptyRoles {
        team_id: u32,
        roles: Vec<&'static str>,
    },
}

fn phase_label(phase: usize) -> String {
    if phase == 0 {
        String::new()
    } else {
        format!(" phase 0x{phase:X}")
    }
}

fn bracketed_list<T: fmt::Display>(items: &[T]) -> String {
    let items: Vec<String> = items.iter().map(T::to_string).collect();
    format!("[{}]", items.join(", "))
}

impl fmt::Display for IntegrityError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::DataSize { actual, expected } => write!(
                f,
                "data.dat size is {}; expected {} bytes for the supported PES edit-file layout",
                crate::catalog::with_separators(*actual),
                crate::catalog::with_separators(*expected)
            ),
            Self::CountExceedsCapacity {
                table,
                count,
                capacity,
            } => {
                write!(
                    f,
                    "Header {table} count {count} exceeds block capacity {capacity}"
                )
            }
            Self::PlayerEntryOutOfBounds { index } => {
                write!(f, "Player entry {index} exceeds data.dat bounds")
            }
            Self::DuplicateEditedPlayer { player_id } => {
                write!(f, "Duplicate edited-player ID {player_id}")
            }
            Self::TeamEntryOutOfBounds { index } => {
                write!(f, "Team entry {index} exceeds data.dat bounds")
            }
            Self::DuplicateTeamEntry { team_id } => {
                write!(f, "Duplicate team entry for team {team_id}")
            }
            Self::TeamPlayerEntryOutOfBounds { index } => {
                write!(f, "Team-player entry {index} exceeds data.dat bounds")
            }
            Self::RosterUnknownTeam { team_id } => {
                write!(f, "Team-player entry references unknown team {team_id}")
            }
            Self::DuplicateRoster { team_id } => {
                write!(f, "Duplicate team-player entry for team {team_id}")
            }
            Self::RosterHoles { team_id, slots } => {
                write!(
                    f,
                    "Team {team_id} roster has empty holes at slots {}",
                    bracketed_list(slots)
                )
            }
            Self::RosterDuplicatePlayer { team_id } => {
                write!(f, "Team {team_id} contains the same player more than once")
            }
            Self::ShirtAboveMax { team_id } => {
                write!(
                    f,
                    "Team {team_id} contains a shirt number above {MAX_SHIRT_NUMBER}"
                )
            }
            Self::DuplicateShirts { team_id } => {
                write!(
                    f,
                    "Team {team_id} contains duplicate non-zero shirt numbers"
                )
            }
            Self::NoClubs => f.write_str("Competition membership does not identify any clubs"),
            Self::ClubUndersized { team_id, size } => write!(
                f,
                "Club {team_id} roster has {size} players; minimum is {MIN_CLUB_ROSTER_SIZE}"
            ),
            Self::MultiClubPlayer {
                player_id,
                team_ids,
            } => write!(
                f,
                "Player {player_id} is registered to multiple clubs: {}",
                bracketed_list(team_ids)
            ),
            Self::GamePlanEntryOutOfBounds { index } => {
                write!(f, "Game-plan entry {index} exceeds data.dat bounds")
            }
            Self::DuplicateGamePlan { team_id } => {
                write!(f, "Duplicate game-plan entry for team {team_id}")
            }
            Self::GamePlanUnknownTeam { team_id } => {
                write!(f, "Game-plan entry references unknown team {team_id}")
            }
            Self::GamePlanActivePrefix { team_id } => write!(
                f,
                "Team {team_id} game-plan active prefix does not map its roster one-to-one"
            ),
            Self::GamePlanStarters { team_id } => {
                write!(
                    f,
                    "Team {team_id} game plan has duplicate or empty starting slots"
                )
            }
            Self::GamePlanPositionArrayOutOfBounds {
                team_id,
                relative_offset,
            } => write!(
                f,
                "Team {team_id} game-plan position array at 0x{relative_offset:X} exceeds data.dat bounds"
            ),
            Self::GamePlanGoalkeeperCodes {
                team_id,
                preset,
                phase,
                count,
            } => write!(
                f,
                "Team {team_id} game-plan preset 0x{preset:X}{} has {count} goalkeeper position codes; expected exactly 1",
                phase_label(*phase)
            ),
            Self::GamePlanGoalkeeperAssignedCode {
                team_id,
                preset,
                phase,
                player_id,
                code,
            } => write!(
                f,
                "Team {team_id} game-plan preset 0x{preset:X}{} assigns GK player {player_id} position code {code}",
                phase_label(*phase)
            ),
            Self::GamePlanOutfielderGoalkeeperCode {
                team_id,
                preset,
                phase,
                position,
                player_id,
            } => write!(
                f,
                "Team {team_id} game-plan preset 0x{preset:X}{} assigns {position} player {player_id} goalkeeper position code 0",
                phase_label(*phase)
            ),
            Self::GamePlanRoleInvalidSlot {
                team_id,
                role_offset,
                value,
            } => write!(
                f,
                "Team {team_id} game-plan role at 0x{role_offset:X} has invalid slot {value}"
            ),
            Self::GamePlanRoleEmptySlot {
                team_id,
                role_offset,
                slot,
            } => write!(
                f,
                "Team {team_id} game-plan role at 0x{role_offset:X} points to empty roster slot {slot}"
            ),
        }
    }
}

impl IntegrityWarning {
    pub fn team_id(&self) -> u32 {
        match self {
            Self::ShirtOnEmptySlot { team_id } | Self::EmptyRoles { team_id, .. } => *team_id,
        }
    }

    /// What is wrong, independent of the team: warnings with the same text
    /// are one kind.
    pub fn kind(&self) -> String {
        match self {
            Self::ShirtOnEmptySlot { .. } => {
                "shirt number assigned to an empty roster slot".to_owned()
            }
            Self::EmptyRoles { roles, .. } => {
                format!("game plan has empty roles: {}", roles.join(", "))
            }
        }
    }
}

impl fmt::Display for IntegrityWarning {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::ShirtOnEmptySlot { team_id } => {
                write!(f, "Team {team_id} has a {}", self.kind())
            }
            Self::EmptyRoles { team_id, .. } => write!(f, "Team {team_id} {}", self.kind()),
        }
    }
}

/// Counters describing what was checked.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct IntegrityMetrics {
    pub data_size: usize,
    pub expected_data_size: usize,
    pub rosters: usize,
    pub clubs: usize,
    pub duplicate_club_players: usize,
    pub undersized_clubs: usize,
    pub checked_game_plans: usize,
    pub checked_position_arrays: usize,
    pub semantic_position_checks: usize,
}

#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct IntegrityReport {
    pub errors: Vec<IntegrityError>,
    pub warnings: Vec<IntegrityWarning>,
    pub metrics: IntegrityMetrics,
}

impl IntegrityReport {
    pub fn is_valid(&self) -> bool {
        self.errors.is_empty()
    }

    /// The error messages, as shown to users.
    pub fn error_messages(&self) -> Vec<String> {
        self.errors.iter().map(ToString::to_string).collect()
    }
}

/// Roster entries keyed by team id, in first-seen order, last duplicate winning.
#[derive(Default)]
struct RosterTable {
    order: Vec<TeamData>,
    position: HashMap<u32, usize>,
}

impl RosterTable {
    fn insert(&mut self, roster: TeamData) {
        match self.position.get(&roster.team_id) {
            Some(&index) => self.order[index] = roster,
            None => {
                self.position.insert(roster.team_id, self.order.len());
                self.order.push(roster);
            }
        }
    }

    fn get(&self, team_id: u32) -> Option<&TeamData> {
        self.position.get(&team_id).map(|&index| &self.order[index])
    }
}

impl EditFile {
    /// Validate table bounds and cross-table invariants. Never panics on
    /// corrupt data; every violation is reported.
    pub fn validate_integrity(&self) -> IntegrityReport {
        let data = self.bytes();
        let len = data.len();
        let counts = self.counts();
        let mut report = IntegrityReport::default();
        let errors = &mut report.errors;
        let warnings = &mut report.warnings;

        if len != EXPECTED_DATA_SIZE {
            errors.push(IntegrityError::DataSize {
                actual: len,
                expected: EXPECTED_DATA_SIZE,
            });
        }
        for (table, count, capacity) in counts.limits() {
            if count > capacity {
                errors.push(IntegrityError::CountExceedsCapacity {
                    table,
                    count,
                    capacity,
                });
            }
        }
        let clamp = |count: u16, capacity: usize| usize::from(count).min(capacity);

        // Edited players.
        let player_count = clamp(counts.players, MAX_PLAYERS);
        let mut seen_players = HashSet::new();
        let mut walked = 0;
        for offset in entry_offsets(
            PLAYER_START,
            PLAYER_TOTAL_SIZE,
            PLAYER_ENTRY_SIZE,
            player_count,
            len,
        ) {
            walked += 1;
            let player_id = read_u32(data, offset + PE_PLAYER_ID);
            if player_id != 0 && !seen_players.insert(player_id) {
                errors.push(IntegrityError::DuplicateEditedPlayer { player_id });
            }
        }
        if walked < player_count {
            errors.push(IntegrityError::PlayerEntryOutOfBounds { index: walked });
        }

        // Teams.
        let team_count = clamp(counts.teams, MAX_TEAMS);
        let mut team_ids = HashSet::new();
        let mut walked = 0;
        for offset in entry_offsets(
            TEAM_START,
            TEAM_ENTRY_SIZE,
            TEAM_ENTRY_SIZE,
            team_count,
            len,
        ) {
            walked += 1;
            let team_id = read_u32(data, offset + TE_TEAM_ID);
            if !team_ids.insert(team_id) {
                errors.push(IntegrityError::DuplicateTeamEntry { team_id });
            }
        }
        if walked < team_count {
            errors.push(IntegrityError::TeamEntryOutOfBounds { index: walked });
        }

        // Rosters.
        let roster_count = clamp(counts.team_players, MAX_TEAM_PLAYER);
        let mut rosters = RosterTable::default();
        let mut seen_rosters = HashSet::new();
        let mut walked = 0;
        for offset in entry_offsets(
            TEAM_PLAYER_START,
            TEAM_PLAYER_ENTRY_SIZE,
            TEAM_PLAYER_ENTRY_SIZE,
            roster_count,
            len,
        ) {
            walked += 1;
            let roster = read_team_player_entry(data, offset);
            let team_id = roster.team_id;
            if !team_ids.contains(&team_id) {
                errors.push(IntegrityError::RosterUnknownTeam { team_id });
            }
            if !seen_rosters.insert(team_id) {
                errors.push(IntegrityError::DuplicateRoster { team_id });
            }

            if let Some(last_slot) = roster.player_ids.iter().rposition(|&id| id != 0) {
                let holes: Vec<usize> = (0..last_slot)
                    .filter(|&slot| roster.player_ids[slot] == 0)
                    .collect();
                if !holes.is_empty() {
                    errors.push(IntegrityError::RosterHoles {
                        team_id,
                        slots: holes,
                    });
                }
            }
            let active: Vec<u32> = roster.roster().collect();
            if active.iter().collect::<HashSet<_>>().len() != active.len() {
                errors.push(IntegrityError::RosterDuplicatePlayer { team_id });
            }
            let shirts: Vec<u16> = roster
                .player_ids
                .iter()
                .zip(&roster.shirt_numbers)
                .filter(|&(&id, &shirt)| id != 0 && shirt != 0)
                .map(|(_, &shirt)| shirt)
                .collect();
            if shirts.iter().any(|&shirt| shirt > MAX_SHIRT_NUMBER) {
                errors.push(IntegrityError::ShirtAboveMax { team_id });
            }
            if shirts.iter().collect::<HashSet<_>>().len() != shirts.len() {
                errors.push(IntegrityError::DuplicateShirts { team_id });
            }
            if roster
                .player_ids
                .iter()
                .zip(&roster.shirt_numbers)
                .any(|(&id, &shirt)| id == 0 && shirt != 0)
            {
                warnings.push(IntegrityWarning::ShirtOnEmptySlot { team_id });
            }
            rosters.insert(roster);
        }
        if walked < roster_count {
            errors.push(IntegrityError::TeamPlayerEntryOutOfBounds { index: walked });
        }

        // Clubs.
        let club_ids = self.club_team_ids();
        if club_ids.is_empty() {
            errors.push(IntegrityError::NoClubs);
        }
        let mut undersized = 0;
        for &team_id in club_ids {
            let size = rosters.get(team_id).map_or(0, TeamData::roster_size);
            if size < MIN_CLUB_ROSTER_SIZE {
                undersized += 1;
                errors.push(IntegrityError::ClubUndersized { team_id, size });
            }
        }
        let mut registrations: BTreeMap<u32, Vec<u32>> = BTreeMap::new();
        for roster in &rosters.order {
            if !club_ids.contains(&roster.team_id) {
                continue;
            }
            for player_id in roster.roster() {
                registrations
                    .entry(player_id)
                    .or_default()
                    .push(roster.team_id);
            }
        }
        let mut duplicate_club_players = 0;
        for (player_id, team_ids) in registrations {
            if team_ids.len() > 1 {
                duplicate_club_players += 1;
                errors.push(IntegrityError::MultiClubPlayer {
                    player_id,
                    team_ids,
                });
            }
        }

        // Game plans.
        let plan_count = clamp(counts.game_plans, MAX_GAME_PLANS);
        let mut checked_game_plans = 0;
        let mut checked_position_arrays = 0;
        let mut semantic_position_checks = 0;
        let mut seen_plans = HashSet::new();
        let mut walked = 0;
        for offset in entry_offsets(
            GAME_PLAN_START,
            GAME_PLAN_ENTRY_SIZE,
            GAME_PLAN_ENTRY_SIZE,
            plan_count,
            len,
        ) {
            walked += 1;
            let team_id = read_u32(data, offset + GP_TEAM_ID);
            if !seen_plans.insert(team_id) {
                errors.push(IntegrityError::DuplicateGamePlan { team_id });
            }
            let Some(roster) = rosters.get(team_id) else {
                errors.push(IntegrityError::GamePlanUnknownTeam { team_id });
                continue;
            };
            if roster.roster_size() == 0 {
                continue;
            }

            checked_game_plans += 1;
            let mut active_slots = [false; TP_MAX_PLAYERS];
            for (slot, &id) in roster.player_ids.iter().enumerate() {
                active_slots[slot] = id != 0;
            }
            let active_count = active_slots.iter().filter(|&&active| active).count();
            let lineup = &data[offset + GP_LINEUP..offset + GP_LINEUP + TP_MAX_PLAYERS];
            let is_active = |slot: u8| {
                active_slots
                    .get(usize::from(slot))
                    .copied()
                    .unwrap_or(false)
            };

            let prefix = &lineup[..active_count];
            let distinct_prefix = prefix.iter().collect::<HashSet<_>>();
            if distinct_prefix.len() != prefix.len()
                || distinct_prefix.len() != active_count
                || !prefix.iter().all(|&slot| is_active(slot))
            {
                errors.push(IntegrityError::GamePlanActivePrefix { team_id });
            }
            let starters = &lineup[..FIRST_TEAM_SLOT_COUNT.min(active_count)];
            if starters.iter().collect::<HashSet<_>>().len() != starters.len()
                || !starters.iter().all(|&slot| is_active(slot))
            {
                errors.push(IntegrityError::GamePlanStarters { team_id });
            }

            checked_position_arrays += check_position_arrays(data, offset, team_id, errors);
            semantic_position_checks +=
                self.check_position_semantics(offset, team_id, roster, lineup, errors);

            let mut empty_roles = Vec::new();
            for (role_offset, role_name) in GP_ROLE_BYTES {
                let value = data[offset + role_offset];
                if value == GP_EMPTY_ROLE {
                    empty_roles.push(role_name);
                } else if usize::from(value) >= TP_MAX_PLAYERS {
                    errors.push(IntegrityError::GamePlanRoleInvalidSlot {
                        team_id,
                        role_offset,
                        value,
                    });
                } else if !is_active(value) {
                    errors.push(IntegrityError::GamePlanRoleEmptySlot {
                        team_id,
                        role_offset,
                        slot: value,
                    });
                }
            }
            if !empty_roles.is_empty() && active_count >= FIRST_TEAM_SLOT_COUNT {
                warnings.push(IntegrityWarning::EmptyRoles {
                    team_id,
                    roles: empty_roles,
                });
            }
        }
        if walked < plan_count {
            errors.push(IntegrityError::GamePlanEntryOutOfBounds { index: walked });
        }

        report.metrics = IntegrityMetrics {
            data_size: len,
            expected_data_size: EXPECTED_DATA_SIZE,
            rosters: rosters.order.len(),
            clubs: club_ids.len(),
            duplicate_club_players,
            undersized_clubs: undersized,
            checked_game_plans,
            checked_position_arrays,
            semantic_position_checks,
        };
        report
    }

    /// Each starter's registered position must agree with the goalkeeper
    /// position code in every preset phase. Skipped for PES 2021 saves (the
    /// FL26 position-code invariant is not portable) and without native
    /// metadata. Returns the number of codes compared.
    fn check_position_semantics(
        &self,
        offset: usize,
        team_id: u32,
        roster: &TeamData,
        lineup: &[u8],
        errors: &mut Vec<IntegrityError>,
    ) -> usize {
        if self.is_pes21() || !self.has_native() {
            return 0;
        }
        let data = self.bytes();
        let starter_slots = &lineup[..FIRST_TEAM_SLOT_COUNT.min(roster.roster_size())];
        let mut checks = 0;
        for (role, &slot) in starter_slots.iter().enumerate() {
            let player_id = roster
                .player_ids
                .get(usize::from(slot))
                .copied()
                .unwrap_or(0);
            if player_id == 0 {
                continue;
            }
            let Some(registered) = self.player_position(player_id) else {
                continue;
            };
            for preset in GP_POSITION_PRESETS {
                for phase in GP_POSITION_PHASE_OFFSETS {
                    let address = offset + preset + phase + role;
                    if address >= data.len() {
                        continue;
                    }
                    let code = data[address];
                    checks += 1;
                    if registered.is_goalkeeper() && code != 0 {
                        errors.push(IntegrityError::GamePlanGoalkeeperAssignedCode {
                            team_id,
                            preset,
                            phase,
                            player_id,
                            code,
                        });
                    } else if !registered.is_goalkeeper() && code == 0 {
                        errors.push(IntegrityError::GamePlanOutfielderGoalkeeperCode {
                            team_id,
                            preset,
                            phase,
                            position: registered,
                            player_id,
                        });
                    }
                }
            }
        }
        checks
    }
}

/// Every non-empty phase needs exactly one goalkeeper position code. Returns
/// how many arrays were checked.
fn check_position_arrays(
    data: &[u8],
    offset: usize,
    team_id: u32,
    errors: &mut Vec<IntegrityError>,
) -> usize {
    let mut checked = 0;
    for preset in GP_POSITION_PRESETS {
        for phase in GP_POSITION_PHASE_OFFSETS {
            let start = offset + preset + phase;
            let end = start + FIRST_TEAM_SLOT_COUNT;
            if end > data.len() {
                errors.push(IntegrityError::GamePlanPositionArrayOutOfBounds {
                    team_id,
                    relative_offset: preset + phase,
                });
                continue;
            }
            checked += 1;
            let count = data[start..end].iter().filter(|&&code| code == 0).count();
            if count != 1 {
                errors.push(IntegrityError::GamePlanGoalkeeperCodes {
                    team_id,
                    preset,
                    phase,
                    count,
                });
            }
        }
    }
    checked
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::edit_file::testing::Mock;

    fn report(mock: &Mock, patch: impl FnOnce(&mut Vec<u8>)) -> IntegrityReport {
        let mut data = mock.build();
        patch(&mut data);
        EditFile::from_bytes(data).unwrap().validate_integrity()
    }

    fn club(ids: std::ops::Range<u32>) -> Mock {
        Mock::club(101, ids.clone().collect(), (1..=ids.len() as u16).collect())
    }

    fn messages(report: &IntegrityReport) -> Vec<String> {
        report.error_messages()
    }

    #[test]
    fn valid_two_club_mock_passes() {
        let mock = Mock {
            players: 32,
            teams: 2,
            team_players: 2,
            game_plans: 2,
            team_entries: vec![(101, "Alpha FC".into()), (102, "Beta FC".into())],
            rosters: vec![
                (101, (1001..1017).collect(), (1..17).collect()),
                (102, (2001..2017).collect(), (17..33).collect()),
            ],
            league: vec![101, 102],
            ..Mock::default()
        };
        let r = report(&mock, |_| {});
        assert!(r.is_valid(), "{:?}", messages(&r));
        assert_eq!(r.metrics.checked_game_plans, 2);
        assert_eq!(r.metrics.checked_position_arrays, 18);
        assert_eq!(r.metrics.clubs, 2);
    }

    #[test]
    fn shirt_on_empty_slot_only_warns() {
        let mut mock = club(1001..1017);
        mock.rosters[0].2.push(99);
        let r = report(&mock, |_| {});
        assert!(r.is_valid());
        assert_eq!(
            r.warnings
                .iter()
                .map(ToString::to_string)
                .collect::<Vec<_>>(),
            ["Team 101 has a shirt number assigned to an empty roster slot"]
        );
    }

    #[test]
    fn undersized_and_rosterless_clubs_are_rejected() {
        let r = report(&club(1001..1016), |_| {});
        assert!(messages(&r).contains(&"Club 101 roster has 15 players; minimum is 16".to_owned()));

        let mut mock = club(1001..1017);
        mock.rosters.clear();
        mock.players = 0;
        mock.team_players = 0;
        mock.game_plans = 0;
        let r = report(&mock, |_| {});
        assert_eq!(
            messages(&r),
            ["Club 101 roster has 0 players; minimum is 16"]
        );
        assert_eq!(r.metrics.undersized_clubs, 1);
    }

    #[test]
    fn missing_club_classification_is_rejected_even_for_low_ids() {
        let mut mock = Mock::club(100, (1001..1017).collect(), (1..17).collect());
        mock.league.clear();
        let r = report(&mock, |_| {});
        assert_eq!(
            messages(&r),
            ["Competition membership does not identify any clubs"]
        );
    }

    #[test]
    fn role_pointing_at_empty_or_invalid_slot_is_rejected() {
        let mock = Mock::club(101, vec![1001], vec![7]);
        let gp = GAME_PLAN_START;
        let r = report(&mock, |d| d[gp + GP_CAPTAIN] = 39);
        assert!(messages(&r).contains(
            &"Team 101 game-plan role at 0x212 points to empty roster slot 39".to_owned()
        ));

        let r = report(&club(1001..1017), |d| {
            d[gp + GP_PK] = 40;
            d[gp + GP_LEFT_CK] = 0xFF;
            d[gp + GP_ATTACK_PLAYERS + 1] = 0xFF;
        });
        assert_eq!(
            messages(&r),
            ["Team 101 game-plan role at 0x20E has invalid slot 40"]
        );
        assert_eq!(
            r.warnings[0].to_string(),
            "Team 101 game plan has empty roles: left_corner, attack_player_2"
        );
    }

    #[test]
    fn goalkeeper_code_must_be_unique_per_phase() {
        let gp = GAME_PLAN_START;
        let r = report(&club(1001..1017), |d| {
            for preset in GP_POSITION_PRESETS {
                for phase in GP_POSITION_PHASE_OFFSETS {
                    d[gp + preset + phase..gp + preset + phase + 11].fill(1);
                }
            }
        });
        assert_eq!(r.metrics.checked_position_arrays, 9);
        let gk_errors = messages(&r)
            .iter()
            .filter(|m| m.contains("expected exactly 1"))
            .count();
        assert_eq!(gk_errors, 9);
        assert!(messages(&r).contains(
            &"Team 101 game-plan preset 0x4 phase 0x21 has 0 goalkeeper position codes; expected exactly 1".to_owned()
        ));
    }

    #[test]
    fn roster_defects_are_each_reported() {
        let tp = TEAM_PLAYER_START;
        let r = report(&club(1001..1017), |d| {
            d[tp + TP_PLAYER_IDS + 20..tp + TP_PLAYER_IDS + 24].fill(0);
        });
        assert!(messages(&r).contains(&"Team 101 roster has empty holes at slots [5]".to_owned()));

        let r = report(&club(1001..1017), |d| {
            d[tp + TP_PLAYER_IDS + 20..tp + TP_PLAYER_IDS + 24]
                .copy_from_slice(&1001u32.to_le_bytes());
        });
        let m = messages(&r);
        assert!(m.contains(&"Team 101 contains the same player more than once".to_owned()));
        assert!(m.contains(&"Player 1001 is registered to multiple clubs: [101, 101]".to_owned()));

        let r = report(&club(1001..1017), |d| {
            d[tp + TP_SHIRT_NUMBERS..tp + TP_SHIRT_NUMBERS + 4]
                .copy_from_slice(&[0xE8, 0x03, 0xE8, 0x03]);
        });
        let m = messages(&r);
        assert!(m.contains(&"Team 101 contains a shirt number above 999".to_owned()));
        assert!(m.contains(&"Team 101 contains duplicate non-zero shirt numbers".to_owned()));
    }

    #[test]
    fn size_and_count_corruption_is_reported_without_panicking() {
        let mock = club(1001..1017);
        let r = report(&mock, |d| d.truncate(d.len() - 1));
        assert_eq!(
            messages(&r),
            [
                "data.dat size is 10,995,799; expected 10,995,800 bytes for the supported PES edit-file layout"
            ]
        );
        let r = report(&mock, |d| d.push(0));
        assert!(messages(&r)[0].starts_with("data.dat size is 10,995,801;"));

        let r = report(&mock, |d| {
            d[HDR_PLAYER_COUNT..HDR_PLAYER_COUNT + 2].copy_from_slice(&30_001u16.to_le_bytes());
            d[HDR_GAME_PLAN_COUNT..HDR_GAME_PLAN_COUNT + 2].copy_from_slice(&751u16.to_le_bytes());
        });
        let m = messages(&r);
        assert!(m.contains(&"Header players count 30001 exceeds block capacity 30000".to_owned()));
        assert!(m.contains(&"Header game plans count 751 exceeds block capacity 750".to_owned()));

        // Truncated deep into the team table: bounds errors, no panic.
        let r = report(&mock, |d| d.truncate(TEAM_START + 5));
        assert!(
            messages(&r)
                .iter()
                .any(|m| m == "Team entry 0 exceeds data.dat bounds")
        );
    }

    #[test]
    fn duplicate_tables_entries_are_rejected() {
        let mut mock = club(1001..1017);
        mock.players = 3;
        mock.player_entries = vec![(7, "A".into()), (7, "B".into()), (8, "C".into())];
        assert_eq!(
            messages(&report(&mock, |_| {})),
            ["Duplicate edited-player ID 7"]
        );

        let mock = Mock {
            players: 32,
            team_players: 2,
            game_plans: 2,
            rosters: vec![
                (101, (1001..1017).collect(), (1..17).collect()),
                (101, (1001..1017).collect(), (1..17).collect()),
            ],
            ..club(1001..1017)
        };
        let m = messages(&report(&mock, |_| {}));
        assert!(m.contains(&"Duplicate team-player entry for team 101".to_owned()));
        assert!(m.contains(&"Duplicate game-plan entry for team 101".to_owned()));
    }

    #[test]
    fn lineup_prefix_must_map_the_roster() {
        let gp = GAME_PLAN_START;
        let r = report(&club(1001..1017), |d| {
            d[gp + GP_LINEUP..gp + GP_LINEUP + 8].copy_from_slice(&[0, 0, 2, 3, 4, 5, 6, 7]);
        });
        let m = messages(&r);
        assert!(m.contains(
            &"Team 101 game-plan active prefix does not map its roster one-to-one".to_owned()
        ));
        assert!(m.contains(&"Team 101 game plan has duplicate or empty starting slots".to_owned()));
    }

    #[test]
    fn goalkeeper_codes_are_checked_against_native_positions() {
        use crate::model::{NativePlayer, NativePlayers};
        use std::sync::Arc;

        struct Native(HashMap<u32, Option<Position>>);
        impl NativePlayers for Native {
            fn native_player(&self, id: u32) -> Option<NativePlayer<'_>> {
                self.0.get(&id).map(|&position| NativePlayer {
                    player_id: id,
                    name: "n",
                    print_name: "",
                    position,
                    age: 20,
                    abilities: None,
                })
            }
        }
        let mut ids: Vec<u32> = (1001..1017).collect();
        ids[0] = 162_196;
        let mock = Mock::club(101, ids, (1..17).collect());
        let validate = |positions: &[(u32, Option<Position>)], pes21: bool| {
            let mut file = EditFile::from_bytes(mock.build()).unwrap();
            file.attach_native(Some(Arc::new(Native(positions.iter().copied().collect()))));
            file.set_pes21(pes21);
            file.validate_integrity()
        };

        // GK registered at role 0 (code 0): consistent.
        let r = validate(&[(162_196, Some(Position::Gk))], false);
        assert!(r.is_valid());
        assert_eq!(r.metrics.semantic_position_checks, 9);

        // An outfielder at role 0 sitting on the GK code is rejected in every preset phase.
        let r = validate(&[(162_196, Some(Position::Cb))], false);
        let m = messages(&r);
        assert_eq!(
            m.iter()
                .filter(|m| m.contains("assigns CB player 162196 goalkeeper position code 0"))
                .count(),
            9
        );
        assert!(m.contains(&"Team 101 game-plan preset 0x4 phase 0x21 assigns CB player 162196 goalkeeper position code 0".to_owned()));

        // Unknown native position: skipped (no catalog fallback).
        let r = validate(&[(162_196, None)], false);
        assert!(r.is_valid());
        assert_eq!(r.metrics.semantic_position_checks, 0);

        // PES 2021 saves skip the FL26 invariant entirely.
        assert_eq!(
            validate(&[(162_196, Some(Position::Cb))], true)
                .metrics
                .semantic_position_checks,
            0
        );
    }
}
