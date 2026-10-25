//! Native PES 2021 / Football Life player ability and overall-rating decoding.
//!
//! The weights and rounding were recovered from Ejogc327's PES 2020 Editor
//! V0.12.10 (`GClass48.smethod_0`), which uses the same routine for PES 2020
//! and PES 2021. They reproduce pesdb.net PES 2021 overall ratings exactly.
//!
//! Two record layouts carry the same abilities:
//!
//! * EDIT `data.dat` player entries: 7-bit raw ability values.
//! * `Player.bin` records: 6-bit values stored minus 40.

use thiserror::Error;

use crate::model::Position;

pub const ABILITY_COUNT: usize = 20;
pub const GOALKEEPING_COUNT: usize = 5;
/// Grade value (above A) that marks the registered position.
const REGISTERED_GRADE: usize = 3;
pub const MIN_RATING: i32 = 40;

/// Per-position ability weights, positions in PES order GK..CF.
#[rustfmt::skip]
const ABILITY_WEIGHTS: [[f64; ABILITY_COUNT]; Position::COUNT] = [
    [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.11, 0.11, 0.0, 0.0, 0.0, 0.0, 0.0],
    [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.22, 0.0, 0.0, 0.1, 0.0, 0.0, 0.19, 0.19, 0.0, 0.09, 0.25, 0.17, 0.08],
    [0.06, 0.09, 0.09, 0.05, 0.0, 0.14, 0.0, 0.0, 0.0, 0.0, 0.16, 0.14, 0.0, 0.11, 0.08, 0.04, 0.14, 0.14, 0.08, 0.05],
    [0.06, 0.09, 0.09, 0.05, 0.0, 0.14, 0.0, 0.0, 0.0, 0.0, 0.16, 0.14, 0.0, 0.11, 0.08, 0.04, 0.14, 0.14, 0.08, 0.05],
    [0.07, 0.18, 0.1, 0.05, 0.18, 0.19, 0.0, 0.0, 0.0, 0.12, 0.03, 0.03, 0.0, 0.05, 0.09, 0.04, 0.14, 0.07, 0.03, 0.01],
    [0.05, 0.23, 0.16, 0.08, 0.23, 0.21, 0.0, 0.0, 0.0, 0.0, 0.04, 0.06, 0.0, 0.0, 0.03, 0.02, 0.17, 0.03, 0.0, 0.0],
    [0.07, 0.15, 0.17, 0.08, 0.07, 0.12, 0.0, 0.0, 0.0, 0.04, 0.24, 0.21, 0.0, 0.0, 0.0, 0.0, 0.13, 0.0, 0.0, 0.0],
    [0.07, 0.15, 0.17, 0.08, 0.07, 0.12, 0.0, 0.0, 0.0, 0.04, 0.24, 0.21, 0.0, 0.0, 0.0, 0.0, 0.13, 0.0, 0.0, 0.0],
    [0.14, 0.23, 0.17, 0.07, 0.21, 0.14, 0.17, 0.0, 0.0, 0.0, 0.05, 0.07, 0.0, 0.0, 0.02, 0.03, 0.03, 0.0, 0.0, 0.0],
    [0.17, 0.19, 0.15, 0.07, 0.05, 0.09, 0.11, 0.0, 0.0, 0.0, 0.15, 0.15, 0.05, 0.0, 0.02, 0.04, 0.06, 0.0, 0.0, 0.0],
    [0.17, 0.19, 0.15, 0.07, 0.05, 0.09, 0.11, 0.0, 0.0, 0.0, 0.15, 0.15, 0.05, 0.0, 0.02, 0.04, 0.06, 0.0, 0.0, 0.0],
    [0.15, 0.19, 0.13, 0.06, 0.09, 0.09, 0.14, 0.0, 0.0, 0.0, 0.09, 0.21, 0.06, 0.0, 0.02, 0.05, 0.04, 0.0, 0.0, 0.0],
    [0.31, 0.23, 0.09, 0.05, 0.0, 0.0, 0.36, 0.03, 0.0, 0.0, 0.05, 0.05, 0.03, 0.03, 0.08, 0.02, 0.0, 0.0, 0.0, 0.0],
];
const WEAK_FOOT_ACCURACY_WEIGHTS: [f64; Position::COUNT] = [
    0.0, 0.23, 0.56, 0.56, 0.23, 0.23, 0.56, 0.56, 0.37, 0.47, 0.47, 0.37, 0.38,
];
/// Bonus by playable-position grade C/B/A, then the registered-position bonus.
const POSITION_BONUS: [[i32; 4]; Position::COUNT] = [
    [0, 3, 5, 8],
    [0, 2, 4, 8],
    [0, 2, 4, 9],
    [0, 2, 4, 9],
    [0, 3, 5, 9],
    [0, 3, 5, 8],
    [0, 3, 5, 8],
    [0, 3, 5, 8],
    [0, 3, 5, 8],
    [0, 2, 4, 10],
    [0, 2, 4, 10],
    [0, 3, 5, 9],
    [0, 3, 5, 8],
];
/// Registered outfield ratings blend 40% of this curve, indexed by the rounded
/// 22-stat average minus [`MIN_RATING`] (averages past the end use the last entry).
#[rustfmt::skip]
const AVERAGE_CURVE: [i32; 60] = [
    31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50,
    51, 52, 56, 59, 62, 65, 67, 70, 73, 75, 77, 79, 82, 83, 85, 87, 89, 90, 92, 93,
    94, 95, 96, 97, 98, 99, 100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111, 112, 113,
];

/// Decoded rating inputs for one player.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PlayerAbilities {
    pub registered_position: Position,
    /// Playable-position grade per position (0 = C, 1 = B, 2 = A), indexed by
    /// [`Position::code`].
    pub position_grades: [u8; Position::COUNT],
    /// Outfield abilities: offensive awareness, ball control, dribbling, tight
    /// possession, low pass, lofted pass, finishing, heading, place kicking,
    /// curl, speed, acceleration, kicking power, jump, physical contact,
    /// balance, stamina, defensive awareness, ball winning, aggression.
    pub abilities: [u8; ABILITY_COUNT],
    /// Goalkeeping abilities: awareness, catching, clearing, reflexes, reach.
    pub goalkeeping: [u8; GOALKEEPING_COUNT],
    /// In-game value 1..=4.
    pub weak_foot_accuracy: u8,
}

impl PlayerAbilities {
    /// In-game overall rating at the registered position.
    pub fn overall(&self) -> i32 {
        self.position_rating(self.registered_position)
    }

    /// PES 2021 rating of this player at `position`.
    pub fn position_rating(&self, position: Position) -> i32 {
        let index = usize::from(position.code());
        let gk = &self.goalkeeping;
        let reach_awareness = (i32::from(gk[0]) + i32::from(gk[4])) / 2;
        let handling = (i32::from(gk[1]) + i32::from(gk[2]) + i32::from(gk[3])) / 3;

        let mut weighted = 0.0_f64;
        if position == Position::Gk {
            weighted += f64::from(reach_awareness - 25) * 0.5 + f64::from(handling - 25) * 0.48;
        }
        for (value, weight) in self.abilities.iter().zip(&ABILITY_WEIGHTS[index]) {
            weighted += f64::from(i32::from(*value) - 25) * weight;
        }
        weighted +=
            f64::from(i32::from(self.weak_foot_accuracy) - 1) * WEAK_FOOT_ACCURACY_WEIGHTS[index];
        weighted += 0.5;

        let bonus = &POSITION_BONUS[index];
        if position != self.registered_position {
            let grade = usize::from(self.position_grades[index]);
            return (weighted as i32 + bonus[grade]).max(MIN_RATING);
        }

        let mut rating = weighted as i32 + bonus[REGISTERED_GRADE];
        if position != Position::Gk {
            let total = reach_awareness
                + handling
                + self.abilities.iter().map(|&v| i32::from(v)).sum::<i32>();
            let average = ((f64::from(total) / 22.0 + 0.5) as i32).max(MIN_RATING);
            let curve_index = ((average - MIN_RATING) as usize).min(AVERAGE_CURVE.len() - 1);
            rating = (f64::from(rating) * 0.6 + f64::from(AVERAGE_CURVE[curve_index]) * 0.4 + 0.5)
                as i32;
        }
        rating.max(MIN_RATING)
    }
}

/// Failure to decode a player record.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Error)]
pub enum RatingError {
    #[error("player record at {offset} does not fit in {len} bytes")]
    OutOfBounds { offset: usize, len: usize },
    #[error("registered position out of range: {0}")]
    PositionOutOfRange(u8),
}

/// Bitfield descriptor: byte offset, bit shift, bit count, value added.
#[derive(Clone, Copy)]
struct Field {
    offset: usize,
    shift: u32,
    bits: u32,
    add: u8,
}

const fn f(offset: usize, shift: u32, bits: u32, add: u8) -> Field {
    Field {
        offset,
        shift,
        bits,
        add,
    }
}

struct Layout {
    registered_position: Field,
    position_grades: [Field; Position::COUNT],
    abilities: [Field; ABILITY_COUNT],
    goalkeeping: [Field; GOALKEEPING_COUNT],
    weak_foot_accuracy: Field,
    /// Bytes from the record start that the fields touch.
    extent: usize,
}

const EDIT_LAYOUT: Layout = Layout {
    registered_position: f(0x21, 5, 4, 0),
    position_grades: [
        f(0x29, 5, 2, 0),
        f(0x29, 7, 2, 0),
        f(0x2A, 1, 2, 0),
        f(0x2A, 3, 2, 0),
        f(0x2A, 5, 2, 0),
        f(0x2A, 7, 2, 0),
        f(0x2B, 1, 2, 0),
        f(0x2B, 3, 2, 0),
        f(0x2B, 5, 2, 0),
        f(0x2E, 4, 2, 0),
        f(0x2C, 0, 2, 0),
        f(0x2C, 2, 2, 0),
        f(0x2C, 4, 2, 0),
    ],
    abilities: [
        f(0x0E, 0, 7, 0),
        f(0x0E, 7, 7, 0),
        f(0x28, 0, 7, 0),
        f(0x10, 0, 7, 0),
        f(0x10, 7, 7, 0),
        f(0x11, 6, 7, 0),
        f(0x12, 5, 7, 0),
        f(0x25, 6, 7, 0),
        f(0x14, 0, 7, 0),
        f(0x14, 7, 7, 0),
        f(0x15, 6, 7, 0),
        f(0x16, 5, 7, 0),
        f(0x2D, 5, 7, 0),
        f(0x18, 0, 7, 0),
        f(0x18, 7, 7, 0),
        f(0x19, 6, 7, 0),
        f(0x1A, 5, 7, 0),
        f(0x24, 0, 7, 0),
        f(0x1C, 0, 7, 0),
        f(0x1C, 7, 7, 0),
    ],
    goalkeeping: [
        f(0x1D, 6, 7, 0),
        f(0x1E, 5, 7, 0),
        f(0x24, 7, 7, 0),
        f(0x2C, 6, 7, 0),
        f(0x20, 0, 7, 0),
    ],
    weak_foot_accuracy: f(0x27, 6, 2, 1),
    extent: 0x30,
};

const PLAYERBIN_LAYOUT: Layout = Layout {
    registered_position: f(0x36, 2, 4, 0),
    position_grades: [
        f(0x2B, 6, 2, 0),
        f(0x3A, 4, 2, 0),
        f(0x27, 6, 2, 0),
        f(0x3B, 2, 2, 0),
        f(0x33, 6, 2, 0),
        f(0x39, 0, 2, 0),
        f(0x3A, 2, 2, 0),
        f(0x39, 4, 2, 0),
        f(0x3A, 0, 2, 0),
        f(0x3B, 0, 2, 0),
        f(0x3B, 4, 2, 0),
        f(0x3B, 6, 2, 0),
        f(0x3A, 6, 2, 0),
    ],
    abilities: [
        f(0x2E, 2, 6, 40),
        f(0x23, 1, 6, 40),
        f(0x2C, 0, 6, 40),
        f(0x34, 0, 6, 40),
        f(0x20, 7, 6, 40),
        f(0x32, 2, 6, 40),
        f(0x31, 4, 6, 40),
        f(0x24, 0, 6, 40),
        f(0x1F, 2, 6, 40),
        f(0x29, 4, 6, 40),
        f(0x26, 2, 6, 40),
        f(0x2B, 0, 6, 40),
        f(0x2C, 6, 6, 40),
        f(0x24, 6, 6, 40),
        f(0x30, 6, 6, 40),
        f(0x2F, 0, 6, 40),
        f(0x2A, 2, 6, 40),
        f(0x22, 3, 6, 40),
        f(0x27, 0, 6, 40),
        f(0x30, 0, 6, 40),
    ],
    goalkeeping: [
        f(0x28, 6, 6, 40),
        f(0x2D, 4, 6, 40),
        f(0x21, 5, 6, 40),
        f(0x28, 0, 6, 40),
        f(0x25, 4, 6, 40),
    ],
    weak_foot_accuracy: f(0x39, 6, 2, 1),
    extent: 0x3D,
};

/// Read one bitfield from the little-endian u16 window at `base + field.offset`.
/// The caller has checked that the layout extent fits.
fn read_field(record: &[u8], field: Field) -> u8 {
    let window = u16::from_le_bytes([record[field.offset], record[field.offset + 1]]);
    (((window >> field.shift) & ((1 << field.bits) - 1)) as u8) + field.add
}

fn decode(data: &[u8], base: usize, layout: &Layout) -> Result<PlayerAbilities, RatingError> {
    let len = data.len();
    let record = base
        .checked_add(layout.extent)
        .filter(|&end| end <= len)
        .map(|end| &data[base..end])
        .ok_or(RatingError::OutOfBounds { offset: base, len })?;
    let position_code = read_field(record, layout.registered_position);
    let registered_position = Position::from_index(position_code)
        .ok_or(RatingError::PositionOutOfRange(position_code))?;
    Ok(PlayerAbilities {
        registered_position,
        position_grades: layout
            .position_grades
            .map(|field| read_field(record, field)),
        abilities: layout.abilities.map(|field| read_field(record, field)),
        goalkeeping: layout.goalkeeping.map(|field| read_field(record, field)),
        weak_foot_accuracy: read_field(record, layout.weak_foot_accuracy),
    })
}

/// Decode an EDIT `data.dat` player entry at `entry_offset`.
pub fn decode_edit_player(
    data: &[u8],
    entry_offset: usize,
) -> Result<PlayerAbilities, RatingError> {
    decode(data, entry_offset, &EDIT_LAYOUT)
}

/// Decode a `Player.bin` record at `record_offset`.
pub fn decode_playerbin_record(
    data: &[u8],
    record_offset: usize,
) -> Result<PlayerAbilities, RatingError> {
    decode(data, record_offset, &PLAYERBIN_LAYOUT)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn flat(registered: Position, value: u8) -> PlayerAbilities {
        PlayerAbilities {
            registered_position: registered,
            position_grades: [1; Position::COUNT],
            abilities: [value; ABILITY_COUNT],
            goalkeeping: [value; GOALKEEPING_COUNT],
            weak_foot_accuracy: 2,
        }
    }

    /// Averages >= 100, which 7-bit EDIT values reach, index past the
    /// 60-entry curve.
    #[test]
    fn extreme_edit_values_clamp_to_the_last_curve_entry() {
        for position in Position::ALL {
            let high = flat(position, 127);
            let rating = high.position_rating(position);
            assert!(rating >= MIN_RATING);
            let higher = flat(position, 126).position_rating(position);
            assert!(rating >= higher);
        }
        // Average past the curve end behaves exactly like the last entry.
        let at_end = flat(Position::Cf, 99);
        let beyond = flat(Position::Cf, 127);
        assert!(beyond.overall() > at_end.overall());
    }

    #[test]
    fn rating_is_floored() {
        assert_eq!(flat(Position::Cb, 0).overall(), MIN_RATING);
        assert_eq!(flat(Position::Gk, 0).overall(), MIN_RATING);
    }

    #[test]
    fn decode_rejects_bad_position_and_short_buffers() {
        let mut entry = vec![0u8; 240];
        let window = (15u16 << 5).to_le_bytes(); // position nibble 15, out of range
        entry[0x21] = window[0];
        entry[0x22] = window[1];
        assert_eq!(
            decode_edit_player(&entry, 0),
            Err(RatingError::PositionOutOfRange(15))
        );
        assert!(matches!(
            decode_edit_player(&entry[..0x20], 0),
            Err(RatingError::OutOfBounds { .. })
        ));
        assert!(matches!(
            decode_playerbin_record(&[0; 0x3C], usize::MAX),
            Err(RatingError::OutOfBounds { .. })
        ));
    }

    #[test]
    fn edit_and_playerbin_fields_decode_independently() {
        let mut entry = vec![0u8; 312];
        // EDIT: registered position CF (12) at byte 0x21 bit 5 (u16 window), speed 77 at 0x15 bit 6.
        let window = (12u16 << 5).to_le_bytes();
        entry[0x21] = window[0];
        entry[0x22] = window[1];
        let speed = (77u16 << 6).to_le_bytes();
        entry[0x15] = speed[0];
        entry[0x16] |= speed[1];
        let decoded = decode_edit_player(&entry, 0).unwrap();
        assert_eq!(decoded.registered_position, Position::Cf);
        assert_eq!(decoded.abilities[10], 77);
        assert_eq!(decoded.weak_foot_accuracy, 1);

        // Player.bin: position at 0x36 bits 2..6, ability = 6 bits + 40.
        let mut record = vec![0u8; 312];
        record[0x36] = 4 << 2; // DMF
        let decoded = decode_playerbin_record(&record, 0).unwrap();
        assert_eq!(decoded.registered_position, Position::Dmf);
        assert_eq!(decoded.abilities, [40; ABILITY_COUNT]);
    }
}
