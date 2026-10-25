//! [`EditFile`]: typed, bounds-safe access to a decrypted `data.dat`.
//!
//! The file is one byte buffer with fixed-capacity tables (see [`crate::layout`]).
//! Header counts only bound iteration and are clamped to the table capacity, so
//! a corrupt count can never make a reader walk into the next table. Lookup
//! indexes (player/roster/game-plan offsets by id, league divisions, club ids)
//! are built once at load from fields that roster mutations never touch
//! (player ids, team ids, table order, the competition block), so they stay
//! valid across mutations and across [`EditFile::restore`].
//!
//! Mutation code (roster moves, game-plan repair) lives in other modules and
//! works through the `pub(crate)` byte accessors ([`EditFile::data_mut`] etc.).

use std::collections::{BTreeSet, HashMap, HashSet};
use std::fs;
use std::io;
use std::path::Path;
use std::sync::Arc;

use thiserror::Error;

use crate::catalog::{
    CatalogSources, PlayerCatalog, PlayerCatalogError, build_player_catalog, coverage,
};
use crate::layout::*;
use crate::model::{NativePlayers, PlayerInfo, Position, TeamData, TeamInfo};
use crate::rating::{PlayerAbilities, decode_edit_player};
use crate::release_policy::{PlayerUsage, ReleasePolicy};
use crate::roster::MutationState;

#[derive(Debug, Error)]
pub enum EditFileError {
    #[error("Data too small ({len} bytes), expected at least {HEADER_SIZE}")]
    TooSmall { len: usize },
    #[error("could not read {path}: {source}")]
    Read { path: String, source: io::Error },
}

/// Strong foot of a player (EDIT entry bit).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PreferredFoot {
    Left,
    Right,
}

/// Raw (unclamped) entry counts from the header.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct HeaderCounts {
    pub players: u16,
    pub teams: u16,
    pub managers: u16,
    pub stadiums: u16,
    pub competitions: u16,
    pub unknown: u16,
    pub team_players: u16,
    pub game_plans: u16,
}

impl HeaderCounts {
    fn parse(data: &[u8]) -> Self {
        let at = |offset| read_u16(data, offset);
        Self {
            players: at(HDR_PLAYER_COUNT),
            teams: at(HDR_TEAM_COUNT),
            managers: at(HDR_MANAGER_COUNT),
            stadiums: at(HDR_STADIUM_COUNT),
            competitions: at(HDR_COMPETITION_COUNT),
            unknown: at(HDR_UNKNOWN_COUNT),
            team_players: at(HDR_TEAM_PLAYER_COUNT),
            game_plans: at(HDR_GAME_PLAN_COUNT),
        }
    }

    /// `(table name, reported count, capacity)` in validation order.
    pub fn limits(&self) -> [(&'static str, usize, usize); 8] {
        [
            ("players", usize::from(self.players), MAX_PLAYERS),
            ("teams", usize::from(self.teams), MAX_TEAMS),
            ("managers", usize::from(self.managers), MAX_MANAGERS),
            (
                "competitions",
                usize::from(self.competitions),
                MAX_COMPETITIONS,
            ),
            ("stadiums", usize::from(self.stadiums), MAX_STADIUMS),
            ("unknown", usize::from(self.unknown), MAX_UNKNOWN),
            (
                "team-player",
                usize::from(self.team_players),
                MAX_TEAM_PLAYER,
            ),
            ("game plans", usize::from(self.game_plans), MAX_GAME_PLANS),
        ]
    }
}

pub(crate) fn read_u16(data: &[u8], offset: usize) -> u16 {
    u16::from_le_bytes([data[offset], data[offset + 1]])
}

pub(crate) fn read_u32(data: &[u8], offset: usize) -> u32 {
    u32::from_le_bytes([
        data[offset],
        data[offset + 1],
        data[offset + 2],
        data[offset + 3],
    ])
}

/// Offsets of the leading entries of a table that lie fully inside `len`
/// bytes; `bound` is the entry prefix that must fit.
pub(crate) fn entry_offsets(
    start: usize,
    entry_size: usize,
    bound: usize,
    count: usize,
    len: usize,
) -> impl Iterator<Item = usize> {
    (0..count)
        .map(move |index| start + index * entry_size)
        .take_while(move |&offset| offset + bound <= len)
}

/// NUL-terminated UTF-8 text in `data[offset..offset + max_len]` (clipped to the
/// buffer), invalid bytes replaced.
fn read_cstring(data: &[u8], offset: usize, max_len: usize) -> String {
    let end = (offset + max_len).min(data.len());
    let field = &data[offset.min(end)..end];
    let text_end = field.iter().position(|&b| b == 0).unwrap_or(field.len());
    String::from_utf8_lossy(&field[..text_end]).into_owned()
}

/// Lookup tables derived at load; see the module docs for why they stay valid.
#[derive(Debug, Default)]
struct Index {
    player_offsets: HashMap<u32, usize>,
    team_ids: HashSet<u32>,
    team_player_offsets: HashMap<u32, usize>,
    game_plan_offsets: HashMap<u32, usize>,
    divisions: Vec<Vec<u32>>,
    club_ids: BTreeSet<u32>,
}

impl Index {
    fn build(data: &[u8], counts: &HeaderCounts) -> Self {
        let len = data.len();
        let mut index = Index::default();

        for offset in entry_offsets(
            PLAYER_START,
            PLAYER_TOTAL_SIZE,
            PLAYER_ENTRY_SIZE,
            clamp(counts.players, MAX_PLAYERS),
            len,
        ) {
            let player_id = read_u32(data, offset + PE_PLAYER_ID);
            if player_id != 0 {
                index.player_offsets.entry(player_id).or_insert(offset);
            }
        }
        for offset in entry_offsets(
            TEAM_START,
            TEAM_ENTRY_SIZE,
            TEAM_ENTRY_SIZE,
            clamp(counts.teams, MAX_TEAMS),
            len,
        ) {
            index.team_ids.insert(read_u32(data, offset + TE_TEAM_ID));
        }
        for offset in entry_offsets(
            TEAM_PLAYER_START,
            TEAM_PLAYER_ENTRY_SIZE,
            TEAM_PLAYER_ENTRY_SIZE,
            clamp(counts.team_players, MAX_TEAM_PLAYER),
            len,
        ) {
            index
                .team_player_offsets
                .entry(read_u32(data, offset + TP_TEAM_ID))
                .or_insert(offset);
        }
        for offset in entry_offsets(
            GAME_PLAN_START,
            GAME_PLAN_ENTRY_SIZE,
            GAME_PLAN_ENTRY_SIZE,
            clamp(counts.game_plans, MAX_GAME_PLANS),
            len,
        ) {
            index
                .game_plan_offsets
                .entry(read_u32(data, offset + GP_TEAM_ID))
                .or_insert(offset);
        }

        // League membership: a division is a run of non-zero known team ids.
        let mut current: Vec<u32> = Vec::new();
        for slot in 0..COMPETITION_SLOT_COUNT {
            let offset = COMPETITION_ENTRY_START + slot * 4;
            if offset + 4 > len {
                break;
            }
            let team_id = read_u32(data, offset);
            if team_id != 0 && team_id != COMPETITION_SEPARATOR && index.team_ids.contains(&team_id)
            {
                current.push(team_id);
            } else if !current.is_empty() {
                index.divisions.push(std::mem::take(&mut current));
            }
        }
        if !current.is_empty() {
            index.divisions.push(current);
        }
        index.club_ids = index.divisions.iter().flatten().copied().collect();
        index
    }
}

fn clamp(count: u16, capacity: usize) -> usize {
    usize::from(count).min(capacity)
}

/// Saved bytes for [`EditFile::restore`].
#[derive(Debug, Clone)]
pub struct Snapshot(Vec<u8>);

/// A decrypted `data.dat` with typed table access.
pub struct EditFile {
    data: Vec<u8>,
    counts: HeaderCounts,
    index: Index,
    native: Option<Arc<dyn NativePlayers>>,
    pes21: bool,
    player_catalog: Option<PlayerCatalog>,
    release_protected: HashMap<u32, HashSet<u32>>,
    release_usage: HashMap<u32, PlayerUsage>,
    /// Session state of the roster mutations.
    pub(crate) mutation: MutationState,
}

impl EditFile {
    /// Parse a decrypted `data.dat`. Any length of at least [`HEADER_SIZE`] is
    /// accepted (readers are bounds-safe); [`EditFile::validate_integrity`]
    /// rejects unsupported sizes.
    pub fn from_bytes(data: Vec<u8>) -> Result<Self, EditFileError> {
        if data.len() < HEADER_SIZE {
            return Err(EditFileError::TooSmall { len: data.len() });
        }
        let counts = HeaderCounts::parse(&data);
        let index = Index::build(&data, &counts);
        Ok(Self {
            data,
            counts,
            index,
            native: None,
            pes21: false,
            player_catalog: None,
            release_protected: HashMap::new(),
            release_usage: HashMap::new(),
            mutation: MutationState::default(),
        })
    }

    pub fn load(path: &Path) -> Result<Self, EditFileError> {
        let data = fs::read(path).map_err(|source| EditFileError::Read {
            path: path.display().to_string(),
            source,
        })?;
        Self::from_bytes(data)
    }

    /// Atomically write the current bytes to `path`.
    pub fn save(&self, path: &Path) -> io::Result<()> {
        fl_fs::write_atomic(path, &self.data)
    }

    pub fn bytes(&self) -> &[u8] {
        &self.data
    }

    pub fn into_bytes(self) -> Vec<u8> {
        self.data
    }

    pub fn counts(&self) -> &HeaderCounts {
        &self.counts
    }

    /// Mutable view of the whole buffer for mutation modules.
    ///
    /// Writers must not change player ids, team ids, table order, header counts
    /// or the competition block: the lookup indexes are built once and are not
    /// refreshed.
    pub(crate) fn data_mut(&mut self) -> &mut [u8] {
        &mut self.data
    }

    // ── snapshots ────────────────────────────────────────────────────────

    /// Copy of the whole buffer.
    pub fn snapshot(&self) -> Snapshot {
        Snapshot(self.data.clone())
    }

    /// Put the bytes saved by [`snapshot`](Self::snapshot) back.
    pub fn restore(&mut self, snapshot: Snapshot) {
        debug_assert_eq!(snapshot.0.len(), self.data.len());
        self.data = snapshot.0;
    }

    // ── attached metadata ────────────────────────────────────────────────

    /// Mark this save as vanilla PES 2021 (disables the FL26 name catalog and
    /// the FL26 position-code invariant). Derived from the container header.
    pub fn set_pes21(&mut self, pes21: bool) {
        self.pes21 = pes21;
    }

    pub fn is_pes21(&self) -> bool {
        self.pes21
    }

    /// Attach native Player.bin metadata. Rebuild the player catalog afterwards.
    pub fn attach_native(&mut self, native: Option<Arc<dyn NativePlayers>>) {
        self.native = native;
    }

    pub fn has_native(&self) -> bool {
        self.native.is_some()
    }

    pub(crate) fn native(&self) -> Option<&dyn NativePlayers> {
        self.native.as_deref()
    }

    pub fn attach_release_policy(&mut self, policy: Option<&ReleasePolicy>) {
        match policy {
            None => {
                self.release_protected.clear();
                self.release_usage.clear();
            }
            Some(policy) => {
                self.release_protected = policy
                    .protected_players
                    .iter()
                    .map(|(&team, players)| (team, players.iter().copied().collect()))
                    .collect();
                self.release_usage = policy
                    .usage
                    .iter()
                    .map(|(&id, &usage)| (id, usage))
                    .collect();
            }
        }
    }

    /// Players explicitly protected from auto-release at `team_id`.
    pub fn release_protected(&self, team_id: u32) -> Option<&HashSet<u32>> {
        self.release_protected.get(&team_id)
    }

    pub fn release_usage(&self, player_id: u32) -> Option<&PlayerUsage> {
        self.release_usage.get(&player_id)
    }

    // ── players ──────────────────────────────────────────────────────────

    /// Byte offset of the (first) player entry with this id.
    pub fn player_entry_offset(&self, player_id: u32) -> Option<usize> {
        self.index.player_offsets.get(&player_id).copied()
    }

    /// Players edited inside the save, keyed by id (a repeated id keeps the last entry).
    ///
    /// The EDIT age field is the real age (it equals the Player.bin age, which
    /// stores `age - 15`, for the 2007 of 2009 base-save players present in both).
    pub fn edited_players(&self) -> HashMap<u32, PlayerInfo> {
        let data = &self.data;
        let count = clamp(self.counts.players, MAX_PLAYERS);
        let mut players = HashMap::new();
        for offset in entry_offsets(
            PLAYER_START,
            PLAYER_TOTAL_SIZE,
            PLAYER_ENTRY_SIZE,
            count,
            data.len(),
        ) {
            let player_id = read_u32(data, offset + PE_PLAYER_ID);
            if player_id == 0 {
                continue;
            }
            let position_code =
                ((read_u16(data, offset + PE_REGISTERED_POSITION_BYTE) >> 5) & 0x0F) as u8;
            let position = Position::from_index(position_code);
            let overall = match position {
                Some(_) => {
                    decode_edit_player(data, offset).map_or(0, |a| a.overall().max(0) as u32)
                }
                None => 0,
            };
            players.insert(
                player_id,
                PlayerInfo {
                    player_id,
                    name: read_cstring(data, offset + PE_PLAYER_NAME, PE_NAME_SIZE),
                    print_name: read_cstring(data, offset + PE_PRINT_NAME, PE_NAME_SIZE),
                    position,
                    age: u32::from((read_u16(data, offset + PE_AGE_BYTE) >> 7) & 0x3F),
                    overall,
                },
            );
        }
        players
    }

    /// Build the full player catalog: name catalog (unless PES 2021), edited
    /// players, then native Player.bin metadata for rostered players.
    ///
    /// Player.bin is authoritative for position/age; EDIT entries override its
    /// overall (they replace the database record in-game).
    pub fn build_catalog(
        &self,
        sources: CatalogSources<'_>,
    ) -> Result<PlayerCatalog, PlayerCatalogError> {
        let edited = self.edited_players();
        let roster_ids: HashSet<u32> = self.rosters().iter().flat_map(TeamData::roster).collect();
        let sources = if self.pes21 {
            // PES 2021/T99 ids come from the matching native Player.bin; the
            // bundled FL26 catalog uses a different id universe.
            CatalogSources::default()
        } else {
            sources
        };
        let Some(native) = &self.native else {
            let (players, report) = build_player_catalog(sources, &edited, &roster_ids)?;
            return Ok(PlayerCatalog { players, report });
        };

        let (native_ids, catalog_ids): (HashSet<u32>, HashSet<u32>) = roster_ids
            .iter()
            .partition(|&&id| native.native_player(id).is_some());
        let (mut players, mut report) = build_player_catalog(sources, &edited, &catalog_ids)?;

        let mut native_ids: Vec<u32> = native_ids.into_iter().collect();
        native_ids.sort_unstable();
        for player_id in native_ids {
            let Some(record) = native.native_player(player_id) else {
                continue;
            };
            let record_print = if record.print_name.is_empty() {
                record.name
            } else {
                record.print_name
            };
            let merged = match players.get(&player_id) {
                None => PlayerInfo {
                    player_id,
                    name: record.name.to_owned(),
                    print_name: record_print.to_owned(),
                    position: record.position,
                    age: record.age,
                    overall: record.overall(),
                },
                Some(existing) => PlayerInfo {
                    player_id,
                    name: if existing.name.is_empty() {
                        record.name.to_owned()
                    } else {
                        existing.name.clone()
                    },
                    print_name: if existing.print_name.is_empty() {
                        record_print.to_owned()
                    } else {
                        existing.print_name.clone()
                    },
                    position: record.position.or(existing.position),
                    age: if record.age != 0 {
                        record.age
                    } else {
                        existing.age
                    },
                    // EDIT entries override Player.bin abilities in-game.
                    overall: if existing.overall != 0 {
                        existing.overall
                    } else {
                        record.overall()
                    },
                },
            };
            players.insert(player_id, merged);
        }
        report.roster_entries = roster_ids.len();
        report.positions = coverage(&players, &roster_ids, |p| p.position.is_some());
        report.ages = coverage(&players, &roster_ids, |p| p.age != 0);
        Ok(PlayerCatalog { players, report })
    }

    /// [`build_catalog`](Self::build_catalog) and keep the result as this
    /// file's player catalog.
    pub fn load_player_catalog(
        &mut self,
        sources: CatalogSources<'_>,
    ) -> Result<&PlayerCatalog, PlayerCatalogError> {
        let catalog = self.build_catalog(sources)?;
        Ok(self.player_catalog.insert(catalog))
    }

    #[cfg(test)]
    pub fn set_player_catalog(&mut self, catalog: Option<PlayerCatalog>) {
        self.player_catalog = catalog;
    }

    pub fn player_catalog(&self) -> Option<&PlayerCatalog> {
        self.player_catalog.as_ref()
    }

    /// Strong-foot metadata from the save player record.
    pub fn preferred_foot(&self, player_id: u32) -> Option<PreferredFoot> {
        let offset = self.player_entry_offset(player_id)?;
        Some(
            if self.data[offset + PE_STRONG_FOOT_BYTE] & PE_STRONG_FOOT_MASK != 0 {
                PreferredFoot::Left
            } else {
                PreferredFoot::Right
            },
        )
    }

    /// In-game abilities: the save's EDIT entry (it replaces the database
    /// record in-game), else Player.bin.
    pub fn abilities(&self, player_id: u32) -> Option<PlayerAbilities> {
        if let Some(offset) = self.player_entry_offset(player_id)
            && let Ok(abilities) = decode_edit_player(&self.data, offset)
        {
            return Some(abilities);
        }
        self.native
            .as_ref()?
            .native_player(player_id)?
            .abilities
            .cloned()
    }

    /// Native registered position (Player.bin); `None` without a record or
    /// when the record's code is unknown.
    pub fn native_position(&self, player_id: u32) -> Option<Position> {
        self.native.as_ref()?.native_player(player_id)?.position
    }

    /// Native position first (a native record with an unknown code does NOT
    /// fall back), then the player catalog's position.
    pub fn player_position(&self, player_id: u32) -> Option<Position> {
        if let Some(record) = self
            .native
            .as_ref()
            .and_then(|n| n.native_player(player_id))
        {
            return record.position;
        }
        self.player_catalog
            .as_ref()?
            .players
            .get(&player_id)?
            .position
    }

    /// Catalog metadata enriched with authoritative native fields.
    pub fn player_metadata(&self, player_id: u32) -> Option<PlayerInfo> {
        let info = self
            .player_catalog
            .as_ref()
            .and_then(|c| c.players.get(&player_id));
        let record = self
            .native
            .as_ref()
            .and_then(|n| n.native_player(player_id));
        match (info, record) {
            (None, None) => None,
            (Some(info), None) => Some(info.clone()),
            (None, Some(record)) => Some(PlayerInfo {
                player_id: record.player_id,
                name: record.name.to_owned(),
                print_name: if record.print_name.is_empty() {
                    record.name
                } else {
                    record.print_name
                }
                .to_owned(),
                position: record.position,
                age: record.age,
                overall: record.overall(),
            }),
            (Some(info), Some(record)) => Some(PlayerInfo {
                player_id: info.player_id,
                name: if info.name.is_empty() {
                    record.name.to_owned()
                } else {
                    info.name.clone()
                },
                print_name: if !info.print_name.is_empty() {
                    info.print_name.clone()
                } else if !record.print_name.is_empty() {
                    record.print_name.to_owned()
                } else {
                    record.name.to_owned()
                },
                position: record.position.or(info.position),
                age: if record.age != 0 {
                    record.age
                } else {
                    info.age
                },
                overall: if info.overall != 0 {
                    info.overall
                } else {
                    record.overall()
                },
            }),
        }
    }

    // ── teams ────────────────────────────────────────────────────────────

    /// Team entries in table order.
    pub fn teams(&self) -> Vec<TeamInfo> {
        let count = clamp(self.counts.teams, MAX_TEAMS);
        entry_offsets(
            TEAM_START,
            TEAM_ENTRY_SIZE,
            TEAM_ENTRY_SIZE,
            count,
            self.data.len(),
        )
        .map(|offset| TeamInfo {
            team_id: read_u32(&self.data, offset + TE_TEAM_ID),
            name: read_cstring(&self.data, offset + TE_TEAM_NAME, TE_NAME_SIZE),
            abbreviation: read_cstring(&self.data, offset + TE_ABBREVIATION, TE_ABBREVIATION_SIZE),
        })
        .collect()
    }

    /// League divisions (runs of team ids in the competition-membership block).
    pub fn league_divisions(&self) -> &[Vec<u32>] {
        &self.index.divisions
    }

    /// Ids of all club teams: every member of a league division. National
    /// teams are not in the block. Empty when the block identifies no clubs
    /// (there is deliberately no id-threshold fallback: Manchester United is
    /// team 100).
    pub fn club_team_ids(&self) -> &BTreeSet<u32> {
        &self.index.club_ids
    }

    // ── rosters ──────────────────────────────────────────────────────────

    pub(crate) fn team_player_entry_offset(&self, team_id: u32) -> Option<usize> {
        self.index.team_player_offsets.get(&team_id).copied()
    }

    /// Roster of a team (first matching entry).
    pub fn roster(&self, team_id: u32) -> Option<TeamData> {
        let offset = self.team_player_entry_offset(team_id)?;
        Some(read_team_player_entry(&self.data, offset))
    }

    /// Every team-player entry in table order (a repeated team appears repeatedly;
    /// validation rejects such saves).
    pub fn rosters(&self) -> Vec<TeamData> {
        let count = clamp(self.counts.team_players, MAX_TEAM_PLAYER);
        entry_offsets(
            TEAM_PLAYER_START,
            TEAM_PLAYER_ENTRY_SIZE,
            TEAM_PLAYER_ENTRY_SIZE,
            count,
            self.data.len(),
        )
        .map(|offset| read_team_player_entry(&self.data, offset))
        .collect()
    }

    /// Teams currently registering `player_id`, in table order. With
    /// `club_only`, national teams are skipped (no filter when the save has no
    /// club classification).
    pub fn find_player_teams(&self, player_id: u32, club_only: bool) -> Vec<u32> {
        if player_id == 0 {
            return Vec::new();
        }
        let filter = club_only && !self.index.club_ids.is_empty();
        let count = clamp(self.counts.team_players, MAX_TEAM_PLAYER);
        entry_offsets(
            TEAM_PLAYER_START,
            TEAM_PLAYER_ENTRY_SIZE,
            TEAM_PLAYER_ENTRY_SIZE,
            count,
            self.data.len(),
        )
        .filter_map(|offset| {
            let team_id = read_u32(&self.data, offset + TP_TEAM_ID);
            if filter && !self.index.club_ids.contains(&team_id) {
                return None;
            }
            (0..TP_MAX_PLAYERS)
                .any(|slot| read_u32(&self.data, offset + TP_PLAYER_IDS + slot * 4) == player_id)
                .then_some(team_id)
        })
        .collect()
    }

    // ── game plans ───────────────────────────────────────────────────────

    pub(crate) fn game_plan_offset(&self, team_id: u32) -> Option<usize> {
        self.index.game_plan_offsets.get(&team_id).copied()
    }

    /// Offsets of every game-plan entry in table order (a repeated team appears
    /// repeatedly).
    pub(crate) fn game_plan_entry_offsets(&self) -> impl Iterator<Item = usize> {
        entry_offsets(
            GAME_PLAN_START,
            GAME_PLAN_ENTRY_SIZE,
            GAME_PLAN_ENTRY_SIZE,
            clamp(self.counts.game_plans, MAX_GAME_PLANS),
            self.data.len(),
        )
    }

    /// Raw game-plan entry bytes of a team.
    pub fn game_plan(&self, team_id: u32) -> Option<&[u8]> {
        let offset = self.game_plan_offset(team_id)?;
        Some(&self.data[offset..offset + GAME_PLAN_ENTRY_SIZE])
    }

    /// `(team id, raw bytes)` of every game-plan entry in table order.
    pub fn game_plans(&self) -> impl Iterator<Item = (u32, &[u8])> {
        let count = clamp(self.counts.game_plans, MAX_GAME_PLANS);
        entry_offsets(
            GAME_PLAN_START,
            GAME_PLAN_ENTRY_SIZE,
            GAME_PLAN_ENTRY_SIZE,
            count,
            self.data.len(),
        )
        .map(|offset| {
            (
                read_u32(&self.data, offset + GP_TEAM_ID),
                &self.data[offset..offset + GAME_PLAN_ENTRY_SIZE],
            )
        })
    }
}

/// Parse the team-player entry at `offset` (the caller checked bounds).
pub(crate) fn read_team_player_entry(data: &[u8], offset: usize) -> TeamData {
    let mut team = TeamData::empty(read_u32(data, offset + TP_TEAM_ID));
    for slot in 0..TP_MAX_PLAYERS {
        team.player_ids[slot] = read_u32(data, offset + TP_PLAYER_IDS + slot * 4);
        team.shirt_numbers[slot] = read_u16(data, offset + TP_SHIRT_NUMBERS + slot * 2);
    }
    team
}

#[cfg(test)]
pub(crate) mod testing {
    //! Synthetic saves for unit tests.

    use super::*;

    #[derive(Default)]
    pub struct Mock {
        pub players: usize,
        pub teams: usize,
        pub team_players: usize,
        pub game_plans: usize,
        pub team_entries: Vec<(u32, String)>,
        pub rosters: Vec<(u32, Vec<u32>, Vec<u16>)>,
        pub player_entries: Vec<(u32, String)>,
        pub league: Vec<u32>,
    }

    impl Mock {
        pub fn club(team_id: u32, ids: Vec<u32>, shirts: Vec<u16>) -> Self {
            Self {
                players: ids.len(),
                teams: 1,
                team_players: 1,
                game_plans: 1,
                team_entries: vec![(team_id, "Alpha FC".into())],
                rosters: vec![(team_id, ids, shirts)],
                league: vec![team_id],
                ..Self::default()
            }
        }

        pub fn build(&self) -> Vec<u8> {
            let mut data = vec![0u8; EXPECTED_DATA_SIZE];
            let put16 = |data: &mut Vec<u8>, off: usize, v: usize| {
                data[off..off + 2].copy_from_slice(&(v as u16).to_le_bytes());
            };
            put16(&mut data, HDR_PLAYER_COUNT, self.players);
            put16(&mut data, HDR_TEAM_COUNT, self.teams);
            put16(&mut data, HDR_MANAGER_COUNT, 1);
            put16(&mut data, HDR_STADIUM_COUNT, 1);
            put16(&mut data, HDR_COMPETITION_COUNT, 1);
            put16(&mut data, HDR_UNKNOWN_COUNT, 1);
            put16(&mut data, HDR_TEAM_PLAYER_COUNT, self.team_players);
            put16(&mut data, HDR_GAME_PLAN_COUNT, self.game_plans);
            for (i, (id, name)) in self.player_entries.iter().enumerate() {
                let off = PLAYER_START + i * PLAYER_TOTAL_SIZE;
                data[off + PE_PLAYER_ID..off + PE_PLAYER_ID + 4].copy_from_slice(&id.to_le_bytes());
                let bytes = &name.as_bytes()[..name.len().min(60)];
                data[off + PE_PLAYER_NAME..off + PE_PLAYER_NAME + bytes.len()]
                    .copy_from_slice(bytes);
            }
            for (i, (id, name)) in self.team_entries.iter().enumerate() {
                let off = TEAM_START + i * TEAM_ENTRY_SIZE;
                data[off + TE_TEAM_ID..off + TE_TEAM_ID + 4].copy_from_slice(&id.to_le_bytes());
                let bytes = &name.as_bytes()[..name.len().min(69)];
                data[off + TE_TEAM_NAME..off + TE_TEAM_NAME + bytes.len()].copy_from_slice(bytes);
            }
            for (i, (team_id, ids, shirts)) in self.rosters.iter().enumerate() {
                let off = TEAM_PLAYER_START + i * TEAM_PLAYER_ENTRY_SIZE;
                data[off + TP_TEAM_ID..off + TP_TEAM_ID + 4]
                    .copy_from_slice(&team_id.to_le_bytes());
                for (j, id) in ids.iter().take(TP_MAX_PLAYERS).enumerate() {
                    let at = off + TP_PLAYER_IDS + j * 4;
                    data[at..at + 4].copy_from_slice(&id.to_le_bytes());
                }
                for (j, shirt) in shirts.iter().take(TP_MAX_PLAYERS).enumerate() {
                    let at = off + TP_SHIRT_NUMBERS + j * 2;
                    data[at..at + 2].copy_from_slice(&shirt.to_le_bytes());
                }
            }
            for (i, team_id) in self.league.iter().enumerate() {
                let at = COMPETITION_ENTRY_START + i * 4;
                data[at..at + 4].copy_from_slice(&team_id.to_le_bytes());
            }
            for (i, (team_id, _, _)) in self.rosters.iter().take(self.game_plans).enumerate() {
                let off = GAME_PLAN_START + i * GAME_PLAN_ENTRY_SIZE;
                data[off + GP_TEAM_ID..off + GP_TEAM_ID + 4]
                    .copy_from_slice(&team_id.to_le_bytes());
                for slot in 0..TP_MAX_PLAYERS {
                    data[off + GP_LINEUP + slot] = slot as u8;
                }
                for preset in GP_POSITION_PRESETS {
                    for phase in GP_POSITION_PHASE_OFFSETS {
                        let at = off + preset + phase;
                        data[at..at + 11].copy_from_slice(&[0, 1, 1, 1, 1, 1, 1, 1, 10, 9, 12]);
                    }
                }
            }
            data
        }
    }
}

#[cfg(test)]
mod tests {
    use super::testing::Mock;
    use super::*;

    fn ids(range: std::ops::Range<u32>) -> Vec<u32> {
        range.collect()
    }

    #[test]
    fn rejects_buffers_smaller_than_the_header() {
        assert!(matches!(
            EditFile::from_bytes(vec![0; HEADER_SIZE - 1]),
            Err(EditFileError::TooSmall { len: 123 })
        ));
    }

    #[test]
    fn reads_header_counts() {
        let mock = Mock {
            players: 100,
            teams: 20,
            ..Mock::default()
        };
        let file = EditFile::from_bytes(mock.build()).unwrap();
        assert_eq!(file.counts().players, 100);
        assert_eq!(file.counts().teams, 20);
        assert_eq!(file.counts().managers, 1);
    }

    #[test]
    fn reads_teams_players_and_rosters() {
        let mut mock = Mock::club(101, ids(1001..1017), (1..17).collect());
        mock.player_entries = vec![
            (1001, "Alpha One".into()),
            (0, "Gap".into()),
            (1002, "Alpha Two".into()),
        ];
        mock.players = 3;
        let file = EditFile::from_bytes(mock.build()).unwrap();

        let teams = file.teams();
        assert_eq!(
            teams,
            vec![TeamInfo {
                team_id: 101,
                name: "Alpha FC".into(),
                abbreviation: String::new()
            }]
        );

        let players = file.edited_players();
        assert_eq!(players.len(), 2);
        assert_eq!(players[&1002].name, "Alpha Two");
        assert_eq!(
            players[&1001].position,
            Some(Position::Gk),
            "position nibble 0 is GK"
        );
        assert_eq!(
            file.player_entry_offset(1002),
            Some(PLAYER_START + 2 * PLAYER_TOTAL_SIZE)
        );

        let roster = file.roster(101).unwrap();
        assert_eq!(roster.roster_size(), 16);
        assert_eq!(roster.shirt_number_of(1003), Some(3));
        assert!(file.roster(999).is_none());
        assert_eq!(file.rosters().len(), 1);
        assert_eq!(file.find_player_teams(1005, true), vec![101]);
        assert!(file.find_player_teams(7, true).is_empty());
        assert_eq!(
            file.club_team_ids().iter().copied().collect::<Vec<_>>(),
            vec![101]
        );
        assert_eq!(file.league_divisions(), &[vec![101]]);
    }

    /// A "every team with id > 100" fallback used to apply when the competition
    /// block named no clubs, misclassifying Manchester United (team 100).
    #[test]
    fn club_ids_have_no_id_threshold_fallback() {
        let mut mock = Mock::club(100, ids(1001..1017), (1..17).collect());
        mock.league.clear();
        let file = EditFile::from_bytes(mock.build()).unwrap();
        assert!(file.club_team_ids().is_empty());
        // Without any classification nothing is filtered out.
        assert_eq!(file.find_player_teams(1001, true), vec![100]);
    }

    #[test]
    fn national_teams_are_excluded_from_club_filter() {
        let mut mock = Mock::club(101, ids(1001..1017), (1..17).collect());
        mock.team_entries.push((5, "Nation".into()));
        mock.teams = 2;
        mock.rosters.push((5, ids(1001..1017), (1..17).collect()));
        mock.team_players = 2;
        let file = EditFile::from_bytes(mock.build()).unwrap();
        assert_eq!(file.find_player_teams(1001, true), vec![101]);
        assert_eq!(file.find_player_teams(1001, false), vec![101, 5]);
    }

    /// A corrupt header count used to walk past the table capacity.
    #[test]
    fn corrupt_header_counts_are_clamped_to_capacity() {
        let mut data = Mock::club(101, ids(1001..1017), (1..17).collect()).build();
        data[HDR_TEAM_PLAYER_COUNT..HDR_TEAM_PLAYER_COUNT + 2]
            .copy_from_slice(&760u16.to_le_bytes());
        data[HDR_GAME_PLAN_COUNT..HDR_GAME_PLAN_COUNT + 2]
            .copy_from_slice(&0xFFFFu16.to_le_bytes());
        data[HDR_TEAM_COUNT..HDR_TEAM_COUNT + 2].copy_from_slice(&0xFFFFu16.to_le_bytes());
        data[HDR_PLAYER_COUNT..HDR_PLAYER_COUNT + 2].copy_from_slice(&0xFFFFu16.to_le_bytes());
        let file = EditFile::from_bytes(data).unwrap();
        assert_eq!(file.rosters().len(), MAX_TEAM_PLAYER);
        assert_eq!(file.game_plans().count(), MAX_GAME_PLANS);
        assert_eq!(file.teams().len(), MAX_TEAMS);
    }

    #[test]
    fn truncated_data_stops_iteration_instead_of_panicking() {
        let mut data = Mock::club(101, ids(1001..1017), (1..17).collect()).build();
        data[HDR_TEAM_COUNT..HDR_TEAM_COUNT + 2].copy_from_slice(&5u16.to_le_bytes());
        data.truncate(TEAM_START + 3 * TEAM_ENTRY_SIZE + 10);
        let file = EditFile::from_bytes(data).unwrap();
        assert_eq!(file.teams().len(), 3);
        assert!(file.rosters().is_empty());
        assert!(file.roster(101).is_none());
        assert_eq!(file.game_plans().count(), 0);
        assert!(file.league_divisions().is_empty());
    }

    #[test]
    fn snapshot_restores_whole_buffer() {
        let mut file =
            EditFile::from_bytes(Mock::club(101, ids(1001..1017), (1..17).collect()).build())
                .unwrap();
        let before = file.bytes().to_vec();
        let full = file.snapshot();

        let roster = file.team_player_entry_offset(101).unwrap();
        let plan = file.game_plan_offset(101).unwrap();
        file.data_mut()[roster + TP_PLAYER_IDS] = 0xAA;
        file.data_mut()[plan + GP_CAPTAIN] = 0x07;
        assert_ne!(file.bytes(), before);
        file.restore(full);
        assert_eq!(file.bytes(), before);
    }

    #[test]
    fn native_position_wins_and_unknown_native_does_not_fall_back() {
        use crate::model::NativePlayer;

        struct Native;
        impl NativePlayers for Native {
            fn native_player(&self, player_id: u32) -> Option<NativePlayer<'_>> {
                let position = match player_id {
                    1 => Some(Position::Rb),
                    2 => None,
                    _ => return None,
                };
                Some(NativePlayer {
                    player_id,
                    name: "n",
                    print_name: "",
                    position,
                    age: 20,
                    abilities: None,
                })
            }
        }

        let catalog = |positions: &[(u32, Position)]| PlayerCatalog {
            players: positions
                .iter()
                .map(|&(id, position)| {
                    (
                        id,
                        PlayerInfo {
                            player_id: id,
                            position: Some(position),
                            ..PlayerInfo::default()
                        },
                    )
                })
                .collect(),
            report: Default::default(),
        };
        let positions = [(1, Position::Cb), (2, Position::Cf), (3, Position::Cf)];
        let mut file =
            EditFile::from_bytes(Mock::club(101, ids(1001..1017), (1..17).collect()).build())
                .unwrap();
        file.set_player_catalog(Some(catalog(&positions)));
        assert_eq!(
            file.player_position(1),
            Some(Position::Cb),
            "no native attached: catalog wins"
        );

        file.attach_native(Some(Arc::new(Native)));
        assert_eq!(file.player_position(1), Some(Position::Rb), "native wins");
        assert_eq!(
            file.player_position(2),
            None,
            "unknown native position does not fall back"
        );
        assert_eq!(
            file.player_position(3),
            Some(Position::Cf),
            "no native record: catalog"
        );
        assert_eq!(file.native_position(3), None);
    }

    #[test]
    fn reads_edited_player_names() {
        let mock = Mock {
            players: 3,
            player_entries: vec![
                (1001, "Lionel Messi".into()),
                (1002, "Cristiano Ronaldo".into()),
                (1003, "Neymar".into()),
            ],
            ..Mock::default()
        };
        let players = EditFile::from_bytes(mock.build()).unwrap().edited_players();
        assert_eq!(players.len(), 3);
        assert_eq!(players[&1001].name, "Lionel Messi");
        assert_eq!(players[&1003].name, "Neymar");
    }

    #[test]
    fn catalog_without_external_sources_uses_only_save_names() {
        let mut mock = Mock::club(101, vec![1001, 1002], vec![1, 2]);
        mock.players = 2;
        mock.player_entries = vec![(1001, "Vanilla One".into()), (1002, "Vanilla Two".into())];
        let mut file = EditFile::from_bytes(mock.build()).unwrap();
        let catalog = file.load_player_catalog(CatalogSources::default()).unwrap();
        let mut ids: Vec<u32> = catalog.players.keys().copied().collect();
        ids.sort_unstable();
        assert_eq!(ids, [1001, 1002]);
        assert_eq!(catalog.report.current_entries, 0);
        assert!(file.player_catalog().is_some());
    }

    #[test]
    fn rosters_skip_empty_slots_and_keep_shirts() {
        let mock = Mock {
            team_players: 2,
            rosters: vec![
                (101, vec![1001, 1002, 1003, 0], vec![10, 7, 11, 0]),
                (102, vec![2001, 2002, 0, 0], vec![9, 5, 0, 0]),
            ],
            ..Mock::default()
        };
        let file = EditFile::from_bytes(mock.build()).unwrap();
        let roster = file.roster(101).unwrap();
        assert_eq!(roster.roster().collect::<Vec<_>>(), [1001, 1002, 1003]);
        assert_eq!(roster.shirt_numbers[..3], [10, 7, 11]);
        assert_eq!(file.rosters().len(), 2);
        assert!(file.roster(999).is_none());
    }

    #[test]
    fn reads_team_names_and_abbreviations() {
        let mock = Mock {
            teams: 2,
            team_entries: vec![(101, "FC Barcelona".into()), (102, "Real Madrid".into())],
            ..Mock::default()
        };
        let mut data = mock.build();
        let abbreviation = TEAM_START + TEAM_ENTRY_SIZE + TE_ABBREVIATION;
        data[abbreviation..abbreviation + 3].copy_from_slice(b"RMA");
        let teams = EditFile::from_bytes(data).unwrap().teams();
        assert_eq!(teams.len(), 2);
        assert_eq!(teams[0].name, "FC Barcelona");
        assert_eq!(
            (teams[1].name.as_str(), teams[1].abbreviation.as_str()),
            ("Real Madrid", "RMA")
        );
    }
}
