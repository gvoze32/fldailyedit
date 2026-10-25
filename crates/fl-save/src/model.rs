//! Domain types shared by the save-table modules.

use std::fmt;

use crate::layout::TP_MAX_PLAYERS;
use crate::rating::PlayerAbilities;

/// Registered player position, in Player.bin / game-plan position-code order.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord)]
#[repr(u8)]
pub enum Position {
    Gk = 0,
    Cb = 1,
    Lb = 2,
    Rb = 3,
    Dmf = 4,
    Cmf = 5,
    Lmf = 6,
    Rmf = 7,
    Amf = 8,
    Lwf = 9,
    Rwf = 10,
    Ss = 11,
    Cf = 12,
}

/// Broad pitch line of a [`Position`].
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum PositionLine {
    Goalkeeper,
    Defense,
    Midfield,
    Forward,
}

impl Position {
    pub const COUNT: usize = 13;
    pub const ALL: [Position; Position::COUNT] = [
        Position::Gk,
        Position::Cb,
        Position::Lb,
        Position::Rb,
        Position::Dmf,
        Position::Cmf,
        Position::Lmf,
        Position::Rmf,
        Position::Amf,
        Position::Lwf,
        Position::Rwf,
        Position::Ss,
        Position::Cf,
    ];

    /// Position for a code in `0..13`.
    pub fn from_index(index: u8) -> Option<Position> {
        Self::ALL.get(usize::from(index)).copied()
    }

    /// The numeric code stored in saves (0 = GK .. 12 = CF).
    pub const fn code(self) -> u8 {
        self as u8
    }

    /// Canonical label as used by Player.bin (`"GK"`, `"CB"`, ...).
    pub const fn name(self) -> &'static str {
        match self {
            Position::Gk => "GK",
            Position::Cb => "CB",
            Position::Lb => "LB",
            Position::Rb => "RB",
            Position::Dmf => "DMF",
            Position::Cmf => "CMF",
            Position::Lmf => "LMF",
            Position::Rmf => "RMF",
            Position::Amf => "AMF",
            Position::Lwf => "LWF",
            Position::Rwf => "RWF",
            Position::Ss => "SS",
            Position::Cf => "CF",
        }
    }

    /// Map a registered or transfer-feed label to a position: trimmed,
    /// upper-cased, with common aliases (`ST` -> CF, `LWB` -> LB, `CDM` -> DMF,
    /// goalkeeper synonyms ...). Unknown labels yield `None`.
    pub fn from_label(label: &str) -> Option<Position> {
        let upper = fl_text::strip(label).to_uppercase();
        let canonical = match upper.as_str() {
            "AM" | "CAM" => "AMF",
            "CM" => "CMF",
            "CDM" | "DM" => "DMF",
            "F" | "ST" => "CF",
            "GOALIE" | "GOALKEEPER" | "KEEPER" => "GK",
            "LM" => "LMF",
            "LW" => "LWF",
            "LWB" => "LB",
            "RM" => "RMF",
            "RW" => "RWF",
            "RWB" => "RB",
            other => other,
        };
        Self::ALL.into_iter().find(|p| p.name() == canonical)
    }

    pub const fn line(self) -> PositionLine {
        match self {
            Position::Gk => PositionLine::Goalkeeper,
            Position::Cb | Position::Lb | Position::Rb => PositionLine::Defense,
            Position::Dmf | Position::Cmf | Position::Lmf | Position::Rmf | Position::Amf => {
                PositionLine::Midfield
            }
            Position::Lwf | Position::Rwf | Position::Ss | Position::Cf => PositionLine::Forward,
        }
    }

    pub const fn is_goalkeeper(self) -> bool {
        matches!(self, Position::Gk)
    }
}

impl fmt::Display for Position {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.name())
    }
}

/// Minimal player info from the edit file, the catalog, or Player.bin.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct PlayerInfo {
    pub player_id: u32,
    pub name: String,
    /// Shirt/club print name.
    pub print_name: String,
    pub position: Option<Position>,
    /// Age in years; 0 = unknown.
    pub age: u32,
    /// Native in-game overall at the registered position; 0 = unknown.
    pub overall: u32,
}

impl PlayerInfo {
    pub fn is_goalkeeper(&self) -> bool {
        self.position.is_some_and(Position::is_goalkeeper)
    }
}

/// One Team-Player table entry: a club's 40 roster slots.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TeamData {
    pub team_id: u32,
    /// Player id per slot; 0 = empty.
    pub player_ids: [u32; TP_MAX_PLAYERS],
    /// Shirt number per slot (parallel to `player_ids`).
    pub shirt_numbers: [u16; TP_MAX_PLAYERS],
}

impl TeamData {
    pub fn empty(team_id: u32) -> Self {
        Self {
            team_id,
            player_ids: [0; TP_MAX_PLAYERS],
            shirt_numbers: [0; TP_MAX_PLAYERS],
        }
    }

    /// Non-zero player ids in slot order.
    pub fn roster(&self) -> impl Iterator<Item = u32> + '_ {
        self.player_ids.iter().copied().filter(|&id| id != 0)
    }

    /// Number of occupied slots.
    pub fn roster_size(&self) -> usize {
        self.roster().count()
    }

    pub fn is_full(&self) -> bool {
        self.roster_size() >= TP_MAX_PLAYERS
    }

    /// First empty (0) slot anywhere in the 40 slots.
    pub fn first_empty_slot(&self) -> Option<usize> {
        self.player_ids.iter().position(|&id| id == 0)
    }

    pub fn has_player(&self, player_id: u32) -> bool {
        self.player_index(player_id).is_some()
    }

    /// Slot of a (non-zero) player id.
    pub fn player_index(&self, player_id: u32) -> Option<usize> {
        if player_id == 0 {
            return None;
        }
        self.player_ids.iter().position(|&id| id == player_id)
    }

    /// Shirt number of a registered player.
    pub fn shirt_number_of(&self, player_id: u32) -> Option<u16> {
        self.player_index(player_id)
            .map(|slot| self.shirt_numbers[slot])
    }
}

/// Team Entry table metadata (id, name, abbreviation).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TeamInfo {
    pub team_id: u32,
    pub name: String,
    pub abbreviation: String,
}

/// Native (Player.bin) metadata of one player, borrowed from the database.
#[derive(Debug, Clone, Copy)]
pub struct NativePlayer<'a> {
    pub player_id: u32,
    pub name: &'a str,
    pub print_name: &'a str,
    /// `None` when the record's position code is outside `0..13`.
    pub position: Option<Position>,
    pub age: u32,
    /// `None` when the abilities could not be decoded.
    pub abilities: Option<&'a PlayerAbilities>,
}

impl NativePlayer<'_> {
    /// Native overall at the registered position; 0 when undecodable.
    pub fn overall(&self) -> u32 {
        self.abilities.map_or(0, |a| a.overall().max(0) as u32)
    }
}

/// Career fields of a native record used to fill empty game-plan roles.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct NativeCareer {
    /// International caps.
    pub caps: u32,
    pub market_value_eur: u32,
}

/// Lookup of authoritative native player metadata (Player.bin).
pub trait NativePlayers: Send + Sync {
    fn native_player(&self, player_id: u32) -> Option<NativePlayer<'_>>;

    /// Ids of every player the database lists (every game player, rostered
    /// or free). Lookup-only sources answer with none.
    fn native_ids(&self) -> Vec<u32> {
        Vec::new()
    }

    /// Career fields of a player; `None` without a record (or a source that
    /// carries none).
    fn native_career(&self, _player_id: u32) -> Option<NativeCareer> {
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn labels_map_through_aliases() {
        assert_eq!(Position::from_label(" st "), Some(Position::Cf));
        assert_eq!(Position::from_label("lwb"), Some(Position::Lb));
        assert_eq!(Position::from_label("Keeper"), Some(Position::Gk));
        assert_eq!(Position::from_label("CDM"), Some(Position::Dmf));
        assert_eq!(Position::from_label("LMF"), Some(Position::Lmf));
        assert_eq!(Position::from_label("UNKNOWN(14)"), None);
        assert_eq!(Position::from_label(""), None);
    }

    #[test]
    fn codes_round_trip() {
        for (index, position) in Position::ALL.into_iter().enumerate() {
            assert_eq!(usize::from(position.code()), index);
            assert_eq!(Position::from_index(position.code()), Some(position));
            assert_eq!(Position::from_label(position.name()), Some(position));
        }
        assert_eq!(Position::from_index(13), None);
    }

    #[test]
    fn lines_match_position_codes() {
        let lines: Vec<_> = Position::ALL.iter().map(|p| p.line()).collect();
        use PositionLine::*;
        assert_eq!(
            lines,
            [
                Goalkeeper, Defense, Defense, Defense, Midfield, Midfield, Midfield, Midfield,
                Midfield, Forward, Forward, Forward, Forward
            ]
        );
    }

    #[test]
    fn team_data_slot_helpers() {
        let mut team = TeamData::empty(101);
        team.player_ids[0] = 7;
        team.player_ids[2] = 9;
        team.shirt_numbers[2] = 10;
        assert_eq!(team.roster_size(), 2);
        assert_eq!(team.first_empty_slot(), Some(1));
        assert_eq!(team.player_index(9), Some(2));
        assert_eq!(team.shirt_number_of(9), Some(10));
        assert!(!team.has_player(0), "0 is the empty marker, never a player");
        assert!(!team.is_full());
        team.player_ids = [1; TP_MAX_PLAYERS];
        assert!(team.is_full());
        assert_eq!(team.first_empty_slot(), None);
    }
}
