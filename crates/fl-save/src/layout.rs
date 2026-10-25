//! Binary layout of the decrypted `data.dat` of a PES 2021 / Football Life
//! edit file.
//!
//! Every table is allocated at a FIXED capacity regardless of how many entries
//! the header reports as used, so every table start below is a compile-time
//! constant. Header counts only bound iteration. All multi-byte integers are
//! little-endian.
//!
//! Layout order: header, players (data + appearance interleaved), teams,
//! managers, competitions, stadiums, unknown, team-player, competition entry
//! (league membership), game plans.

// ── entry sizes ───────────────────────────────────────────────────────────

pub const HEADER_SIZE: usize = 0x7C;
/// Player data part (ids, names, abilities).
pub const PLAYER_ENTRY_SIZE: usize = 0xF0;
pub const PLAYER_APPEARANCE_SIZE: usize = 0x48;
/// Data + appearance, interleaved per player.
pub const PLAYER_TOTAL_SIZE: usize = PLAYER_ENTRY_SIZE + PLAYER_APPEARANCE_SIZE;
pub const TEAM_ENTRY_SIZE: usize = 0x24C;
pub const MANAGER_ENTRY_SIZE: usize = 0x58;
pub const COMPETITION_ENTRY_SIZE: usize = 0x2F8;
/// The wiki says 0xBB; block arithmetic proves 0xBC.
pub const STADIUM_ENTRY_SIZE: usize = 0xBC;
pub const UNKNOWN_ENTRY_SIZE: usize = 0x84;
pub const TEAM_PLAYER_ENTRY_SIZE: usize = 0x11C;
/// Flat league-membership section between the team-player and game-plan tables.
pub const COMPETITION_SECTION_SIZE: usize = 0x1230;
pub const GAME_PLAN_ENTRY_SIZE: usize = 0x274;

// ── fixed capacities ──────────────────────────────────────────────────────

pub const MAX_PLAYERS: usize = 30_000;
pub const MAX_TEAMS: usize = 750;
pub const MAX_MANAGERS: usize = 1_300;
pub const MAX_COMPETITIONS: usize = 65;
pub const MAX_STADIUMS: usize = 65;
pub const MAX_UNKNOWN: usize = 2_500;
pub const MAX_TEAM_PLAYER: usize = 750;
/// Fixed block; FL26 currently populates 749 entries.
pub const MAX_GAME_PLANS: usize = 750;

// ── table starts (derived from the capacities above) ─────────────────────

pub const PLAYER_START: usize = HEADER_SIZE;
pub const TEAM_START: usize = PLAYER_START + MAX_PLAYERS * PLAYER_TOTAL_SIZE;
pub const MANAGER_START: usize = TEAM_START + MAX_TEAMS * TEAM_ENTRY_SIZE;
pub const COMPETITION_START: usize = MANAGER_START + MAX_MANAGERS * MANAGER_ENTRY_SIZE;
pub const STADIUM_START: usize = COMPETITION_START + MAX_COMPETITIONS * COMPETITION_ENTRY_SIZE;
pub const UNKNOWN_START: usize = STADIUM_START + MAX_STADIUMS * STADIUM_ENTRY_SIZE;
pub const TEAM_PLAYER_START: usize = UNKNOWN_START + MAX_UNKNOWN * UNKNOWN_ENTRY_SIZE;
pub const COMPETITION_ENTRY_START: usize =
    TEAM_PLAYER_START + MAX_TEAM_PLAYER * TEAM_PLAYER_ENTRY_SIZE;
pub const GAME_PLAN_START: usize = COMPETITION_ENTRY_START + COMPETITION_SECTION_SIZE;
/// Exact byte length of a supported `data.dat`.
pub const EXPECTED_DATA_SIZE: usize = GAME_PLAN_START + MAX_GAME_PLANS * GAME_PLAN_ENTRY_SIZE;

// ── header fields (u16 LE) ───────────────────────────────────────────────

pub const HDR_PLAYER_COUNT: usize = 0x60;
pub const HDR_TEAM_COUNT: usize = 0x64;
pub const HDR_MANAGER_COUNT: usize = 0x66;
pub const HDR_STADIUM_COUNT: usize = 0x68;
pub const HDR_COMPETITION_COUNT: usize = 0x6A;
pub const HDR_UNKNOWN_COUNT: usize = 0x6C;
pub const HDR_TEAM_PLAYER_COUNT: usize = 0x70;
pub const HDR_GAME_PLAN_COUNT: usize = 0x74;

// ── player entry fields ──────────────────────────────────────────────────

/// u32 player id; 0 marks an unused entry.
pub const PE_PLAYER_ID: usize = 0x00;
/// 61-byte NUL-terminated UTF-8 name.
pub const PE_PLAYER_NAME: usize = 0x36;
pub const PE_NAME_SIZE: usize = 61;
/// 61-byte NUL-terminated UTF-8 shirt/print name.
pub const PE_PRINT_NAME: usize = 0x73;
/// 6-bit age field in the u16 at this byte, starting at bit 7. It holds the
/// real age (equal to the Player.bin age, which stores `age - 15`, for 2007 of
/// the 2009 base-save players present in both), so no offset is added.
pub const PE_AGE_BYTE: usize = 0x20;
/// 4-bit registered-position field in the u16 at this byte, starting at bit 5.
pub const PE_REGISTERED_POSITION_BYTE: usize = 0x21;
pub const PE_STRONG_FOOT_BYTE: usize = 0x2F;
pub const PE_STRONG_FOOT_MASK: u8 = 1 << 5;

// ── team entry fields ────────────────────────────────────────────────────

pub const TE_TEAM_ID: usize = 0x000;
/// 70-byte NUL-terminated name.
pub const TE_TEAM_NAME: usize = 0x068;
pub const TE_NAME_SIZE: usize = 70;
/// 4-byte NUL-terminated abbreviation.
pub const TE_ABBREVIATION: usize = 0x0AE;
pub const TE_ABBREVIATION_SIZE: usize = 4;

// ── team-player entry fields ─────────────────────────────────────────────

pub const TP_TEAM_ID: usize = 0x00;
/// 40 × u32 player ids (slot order; 0 = empty).
pub const TP_PLAYER_IDS: usize = 0x04;
/// 40 × u16 shirt numbers, parallel to the ids.
pub const TP_SHIRT_NUMBERS: usize = 0xA4;
pub const TP_MAX_PLAYERS: usize = 40;
pub const MAX_SHIRT_NUMBER: u16 = 999;

/// Every club must register at least this many players.
pub const MIN_CLUB_ROSTER_SIZE: usize = 16;
pub const FIRST_TEAM_SLOT_COUNT: usize = 11;
pub const MATCHDAY_SQUAD_SLOT_COUNT: usize = 18;

// ── competition (league membership) section ──────────────────────────────

/// u32 slots in the section; a division is a run of non-zero team ids.
pub const COMPETITION_SLOT_COUNT: usize = COMPETITION_SECTION_SIZE / 4;
/// Separator value that never names a team.
pub const COMPETITION_SEPARATOR: u32 = 0xFFFF_0300;

// ── game plan entry fields ───────────────────────────────────────────────

pub const GP_TEAM_ID: usize = 0x000;
/// 40 × u8: roster slot per lineup role (0..10 XI, 11..17 matchday bench).
pub const GP_LINEUP: usize = 0x1E4;
pub const GP_LEFT_CK: usize = 0x20C;
pub const GP_RIGHT_CK: usize = 0x20D;
pub const GP_PK: usize = 0x20E;
/// 3 × u8 attack-player slots (0x20F..=0x211).
pub const GP_ATTACK_PLAYERS: usize = 0x20F;
pub const GP_ATTACK_PLAYER_COUNT: usize = 3;
pub const GP_CAPTAIN: usize = 0x212;
/// Role byte meaning "no player".
pub const GP_EMPTY_ROLE: u8 = 0xFF;

/// Single-slot roles, in the order used by validation.
pub const GP_SINGLE_PLAYER_ROLES: [usize; 4] = [GP_LEFT_CK, GP_RIGHT_CK, GP_PK, GP_CAPTAIN];

/// Every role byte (single-player roles, then the attack players) with its
/// report name, in validation order.
pub const GP_ROLE_BYTES: [(usize, &str); 7] = [
    (GP_LEFT_CK, "left_corner"),
    (GP_RIGHT_CK, "right_corner"),
    (GP_PK, "penalty"),
    (GP_CAPTAIN, "captain"),
    (GP_ATTACK_PLAYERS, "attack_player_1"),
    (GP_ATTACK_PLAYERS + 1, "attack_player_2"),
    (GP_ATTACK_PLAYERS + 2, "attack_player_3"),
];

/// Each of the three presets stores 3 phases of 11 tactical position codes
/// (Player.bin registered-position order; 0 = GK) before [`GP_LINEUP`].
pub const GP_POSITION_PRESETS: [usize; 3] = [0x004, 0x0A4, 0x144];
/// Offsets of the position-code arrays relative to a preset.
pub const GP_POSITION_PHASE_OFFSETS: [usize; 3] = [0x000, 0x021, 0x042];
/// 11 (vertical, horizontal) u8 coordinate pairs per phase, relative to a preset.
pub const GP_POSITION_COORDINATE_OFFSETS: [usize; 3] = [0x00B, 0x02C, 0x04D];
pub const GP_POSITION_COORDINATE_ENTRY_SIZE: usize = 2;

/// Tactical byte fields of the main preset (offsets relative to the preset at
/// [`GP_POSITION_PRESETS`]`[0]`). Binary encodings: containment 0 = middle /
/// 1 = wide; pressuring 0 = aggressive / 1 = conservative.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum TacticalField {
    AttackingStyle,
    BuildUp,
    AttackingArea,
    DefensiveStyle,
    ContainmentArea,
    Pressuring,
    DefensiveLine,
    Compactness,
}

impl TacticalField {
    pub const ALL: [TacticalField; 8] = [
        TacticalField::AttackingStyle,
        TacticalField::BuildUp,
        TacticalField::AttackingArea,
        TacticalField::DefensiveStyle,
        TacticalField::ContainmentArea,
        TacticalField::Pressuring,
        TacticalField::DefensiveLine,
        TacticalField::Compactness,
    ];

    /// Byte offset inside the game-plan entry's main preset.
    pub const fn offset(self) -> usize {
        match self {
            Self::AttackingStyle => 0x063,
            Self::BuildUp => 0x064,
            Self::AttackingArea => 0x065,
            Self::DefensiveStyle => 0x067,
            Self::ContainmentArea => 0x068,
            Self::Pressuring => 0x069,
            Self::DefensiveLine => 0x08E,
            Self::Compactness => 0x08F,
        }
    }

    /// Inclusive range of legal byte values.
    pub const fn value_range(self) -> (u8, u8) {
        match self {
            Self::DefensiveLine | Self::Compactness => (1, 10),
            _ => (0, 1),
        }
    }

    pub const fn name(self) -> &'static str {
        match self {
            Self::AttackingStyle => "attacking_style",
            Self::BuildUp => "build_up",
            Self::AttackingArea => "attacking_area",
            Self::DefensiveStyle => "defensive_style",
            Self::ContainmentArea => "containment_area",
            Self::Pressuring => "pressuring",
            Self::DefensiveLine => "defensive_line",
            Self::Compactness => "compactness",
        }
    }

    pub fn from_name(name: &str) -> Option<Self> {
        Self::ALL.into_iter().find(|field| field.name() == name)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The documented wiki addresses of the vanilla PES 2021 layout.
    #[test]
    fn table_starts_match_vanilla_addresses() {
        assert_eq!(PLAYER_START, 0x7C);
        assert_eq!(TEAM_START, 0x8E_D2FC);
        assert_eq!(MANAGER_START, 0x95_8DA4);
        assert_eq!(COMPETITION_START, 0x97_4C84);
        assert_eq!(STADIUM_START, 0x98_0D7C);
        assert_eq!(UNKNOWN_START, 0x98_3D38);
        assert_eq!(TEAM_PLAYER_START, 0x9D_4648);
        assert_eq!(COMPETITION_ENTRY_START, 0xA0_8650);
        assert_eq!(GAME_PLAN_START, 0xA0_9880);
        assert_eq!(EXPECTED_DATA_SIZE, 10_995_800);
    }

    #[test]
    fn game_plan_role_bytes_do_not_overlap_lineup() {
        assert_eq!(GP_LINEUP + TP_MAX_PLAYERS, GP_LEFT_CK);
        const { assert!(GP_CAPTAIN < GAME_PLAN_ENTRY_SIZE) };
        assert_eq!(GP_ROLE_BYTES[0].0, GP_SINGLE_PLAYER_ROLES[0]);
    }

    #[test]
    fn tactical_fields_round_trip_names() {
        for field in TacticalField::ALL {
            assert_eq!(TacticalField::from_name(field.name()), Some(field));
        }
        assert_eq!(TacticalField::DefensiveLine.value_range(), (1, 10));
        assert_eq!(TacticalField::BuildUp.value_range(), (0, 1));
    }
}
