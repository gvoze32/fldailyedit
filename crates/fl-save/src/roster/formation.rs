//! Formation shapes: role codes and pitch coordinates of the 11 starters.

use crate::model::Position;

/// Layout of one formation row family.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum LineKind {
    Defense,
    Midfield,
    /// Five-row shapes only: the central band between the holding and the
    /// attacking midfielders (the "2" in 4-1-2-1-2).
    Central,
    Attacking,
    Forward,
    /// Five-row shapes only: a withdrawn striker band (3-4-1-1-1).
    Support,
}

/// `(position, horizontal, vertical adjustment of the row)`.
type Slot = (Position, u8, i8);

use Position::{Amf, Cb, Cf, Cmf, Dmf, Lb, Lmf, Lwf, Rb, Rmf, Rwf, Ss};

fn row_slots(kind: LineKind, count: u8) -> Option<&'static [Slot]> {
    let slots: &'static [Slot] = match (kind, count) {
        (LineKind::Defense, 3) => &[(Cb, 52, 0), (Cb, 68, 0), (Cb, 36, 0)],
        (LineKind::Defense, 4) => &[(Cb, 64, 0), (Cb, 40, 0), (Rb, 88, 2), (Lb, 16, 2)],
        (LineKind::Defense, 5) => &[
            (Cb, 52, 0),
            (Cb, 68, 0),
            (Cb, 36, 0),
            (Rb, 92, 2),
            (Lb, 12, 2),
        ],

        (LineKind::Midfield, 1) => &[(Dmf, 52, 0)],
        (LineKind::Midfield, 2) => &[(Dmf, 64, 0), (Dmf, 40, 3)],
        (LineKind::Midfield, 3) => &[(Dmf, 52, -2), (Cmf, 64, 2), (Cmf, 40, 2)],
        (LineKind::Midfield, 4) => &[(Cmf, 40, 0), (Cmf, 64, 0), (Rmf, 88, 2), (Lmf, 16, 2)],
        (LineKind::Midfield, 5) => &[
            (Dmf, 52, -2),
            (Cmf, 64, 2),
            (Cmf, 40, 2),
            (Rmf, 88, 3),
            (Lmf, 16, 3),
        ],

        (LineKind::Central, 1) => &[(Cmf, 52, 0)],
        (LineKind::Central, 2) => &[(Cmf, 64, 0), (Cmf, 40, 0)],
        (LineKind::Central, 3) => &[(Cmf, 52, 0), (Rmf, 84, 2), (Lmf, 20, 2)],
        (LineKind::Central, 4) => &[(Cmf, 40, 0), (Cmf, 64, 0), (Rmf, 88, 2), (Lmf, 16, 2)],

        (LineKind::Attacking, 1) => &[(Amf, 52, 0)],
        (LineKind::Attacking, 2) => &[(Amf, 40, 0), (Amf, 64, 0)],
        (LineKind::Attacking, 3) => &[(Amf, 52, 0), (Rwf, 84, 8), (Lwf, 20, 8)],
        (LineKind::Attacking, 4) => &[(Amf, 40, 0), (Amf, 64, 0), (Rwf, 88, 8), (Lwf, 16, 8)],
        (LineKind::Attacking, 5) => &[
            (Amf, 52, 0),
            (Amf, 40, 0),
            (Amf, 64, 0),
            (Rwf, 88, 8),
            (Lwf, 16, 8),
        ],

        (LineKind::Forward, 1) => &[(Cf, 52, 0)],
        (LineKind::Forward, 2) => &[(Cf, 64, 0), (Cf, 40, 0)],
        (LineKind::Forward, 3) => &[(Cf, 52, 2), (Rwf, 84, -2), (Lwf, 20, -2)],
        (LineKind::Forward, 4) => &[(Cf, 40, 0), (Cf, 64, 0), (Rwf, 88, -2), (Lwf, 16, -2)],

        (LineKind::Support, 1) => &[(Ss, 52, 0)],
        (LineKind::Support, 2) => &[(Ss, 64, 0), (Ss, 40, 0)],

        _ => return None,
    };
    Some(slots)
}

/// Vertical coordinate of each formation row, by row count.
fn row_positions(rows: usize) -> &'static [u8] {
    match rows {
        3 => &[11, 24, 43],
        4 => &[11, 19, 33, 43],
        _ => &[11, 17, 24, 33, 43],
    }
}

/// A supported 10-outfield-player formation: 3-5 rows of 1-5 players.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Formation {
    rows: [u8; 5],
    len: usize,
}

impl Formation {
    /// Parse `"4-2-3-1"`-style text (surrounding whitespace allowed). Only
    /// shapes with 10 outfield players, a 3-5 back line and 1-4 forwards are
    /// accepted.
    pub fn parse(text: &str) -> Option<Self> {
        let mut rows = [0u8; 5];
        let mut len = 0;
        for part in fl_text::strip(text).split('-') {
            if len == rows.len() || part.is_empty() || !part.bytes().all(|b| b.is_ascii_digit()) {
                return None;
            }
            // Leading zeros are allowed ("04" is 4); anything longer than two
            // significant digits is out of range and fails the checks below.
            let significant = part.trim_start_matches('0');
            rows[len] = if significant.len() > 2 {
                u8::MAX
            } else {
                significant.parse::<u8>().unwrap_or(0)
            };
            len += 1;
        }
        let counts = &rows[..len];
        if !(3..=5).contains(&len)
            || counts.iter().map(|&c| u32::from(c)).sum::<u32>() != 10
            || !(3..=5).contains(&counts[0])
            || !(1..=4).contains(&counts[len - 1])
            || counts.iter().any(|&c| !(1..=5).contains(&c))
        {
            return None;
        }
        Some(Self { rows, len })
    }

    /// Players per row, back line first.
    pub fn counts(&self) -> &[u8] {
        &self.rows[..self.len]
    }

    /// Canonical `"4-2-3-1"` text.
    pub fn canonical(&self) -> String {
        let counts: Vec<String> = self.counts().iter().map(u8::to_string).collect();
        counts.join("-")
    }

    /// Row families, back line first.
    fn line_kinds(&self) -> Vec<LineKind> {
        let counts = self.counts();
        if counts.len() == 5 {
            // 4-1-2-1-2: holding / central / attacking; 3-4-1-1-1: the single
            // third-row player is an AMF and the fourth row a second striker.
            return if counts[2] >= 2 {
                vec![
                    LineKind::Defense,
                    LineKind::Midfield,
                    LineKind::Central,
                    LineKind::Attacking,
                    LineKind::Forward,
                ]
            } else {
                vec![
                    LineKind::Defense,
                    LineKind::Midfield,
                    LineKind::Attacking,
                    LineKind::Support,
                    LineKind::Forward,
                ]
            };
        }
        let middle = counts.len() - 2;
        let mut kinds = vec![LineKind::Defense];
        kinds.extend((0..middle).map(|index| {
            if index == 0 || middle == 1 || counts[index + 1] >= 4 {
                LineKind::Midfield
            } else {
                LineKind::Attacking
            }
        }));
        kinds.push(LineKind::Forward);
        kinds
    }

    /// Role codes and `(vertical, horizontal)` coordinates of the 11 starters,
    /// goalkeeper first. `None` when a row has no layout.
    pub fn layout(&self) -> Option<FormationLayout> {
        let mut roles = Vec::with_capacity(11);
        let mut coordinates = Vec::with_capacity(11);
        roles.push(Position::Gk);
        coordinates.push((3u8, 52u8));
        let positions = row_positions(self.len);
        for ((&count, kind), &row) in self.counts().iter().zip(self.line_kinds()).zip(positions) {
            for &(position, horizontal, adjustment) in row_slots(kind, count)? {
                let vertical = i16::from(row) + i16::from(adjustment);
                if !(0..=0x64).contains(&vertical) {
                    return None;
                }
                roles.push(position);
                coordinates.push((vertical as u8, horizontal));
            }
        }
        Some(FormationLayout {
            roles: roles.try_into().ok()?,
            coordinates: coordinates.try_into().ok()?,
        })
    }
}

/// Role codes and coordinates of a formation's 11 starters.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct FormationLayout {
    pub roles: [Position; 11],
    /// `(vertical, horizontal)` per role.
    pub coordinates: [(u8, u8); 11],
}

/// Canonical text of a supported formation, or `None`.
pub fn normalize_game_plan_formation(text: &str) -> Option<String> {
    Formation::parse(text).map(|formation| formation.canonical())
}
