//! Shirt-number allocation for signings.

use std::collections::HashSet;

/// Which priority list a position label selects.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum ShirtLine {
    Goalkeeper,
    Defender,
    Midfielder,
    Forward,
    Other,
}

const GOALKEEPER: &[u32] = &[1, 12, 13, 22, 23, 25, 30, 31, 33, 40, 99];
const DEFENDER: &[u32] = &[
    2, 3, 4, 5, 6, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30,
];
const MIDFIELDER: &[u32] = &[
    6, 7, 8, 10, 11, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30,
];
const FORWARD: &[u32] = &[
    9, 7, 10, 11, 14, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30,
];
const OTHER: &[u32] = &[
    7, 8, 9, 10, 11, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30,
];

/// Classify a position label. The label sets are those of the original
/// allocator, verbatim: save labels `LMF` / `RMF` (and the aliases `CAM`,
/// `CDM`, `F`) are NOT in them and therefore use the generic list. This is a
/// known label mismatch carried over for byte parity (it changes which free
/// number an LMF/RMF signing receives).
fn shirt_line(label: &str, is_goalkeeper: bool) -> ShirtLine {
    let upper = fl_text::strip(label).to_uppercase();
    if is_goalkeeper || matches!(upper.as_str(), "GK" | "GOALKEEPER" | "KEEPER" | "GOALIE") {
        ShirtLine::Goalkeeper
    } else if matches!(
        upper.as_str(),
        "CB" | "LB" | "RB" | "CB/LB" | "CB/RB" | "LWB" | "RWB"
    ) {
        ShirtLine::Defender
    } else if matches!(
        upper.as_str(),
        "CMF" | "DMF" | "AMF" | "LM" | "RM" | "DM" | "AM" | "CM"
    ) {
        ShirtLine::Midfielder
    } else if matches!(
        upper.as_str(),
        "CF" | "SS" | "LWF" | "RWF" | "ST" | "LW" | "RW"
    ) {
        ShirtLine::Forward
    } else {
        ShirtLine::Other
    }
}

/// Allocate a conflict-free shirt number in `1..=99`.
///
/// 1. `preferred` when it is in `1..=99` and vacant.
/// 2. Otherwise the first vacant number of the position's priority list
///    (goalkeeper / defender / midfielder / forward / generic).
/// 3. Otherwise the lowest vacant number; `99` when every number is taken.
pub fn assign_smart_shirt_number(
    used_numbers: &HashSet<u32>,
    preferred: Option<u32>,
    position: &str,
    is_goalkeeper: bool,
) -> u32 {
    if let Some(number) = preferred
        && (1..=99).contains(&number)
        && !used_numbers.contains(&number)
    {
        return number;
    }
    let priority = match shirt_line(position, is_goalkeeper) {
        ShirtLine::Goalkeeper => GOALKEEPER,
        ShirtLine::Defender => DEFENDER,
        ShirtLine::Midfielder => MIDFIELDER,
        ShirtLine::Forward => FORWARD,
        ShirtLine::Other => OTHER,
    };
    priority
        .iter()
        .copied()
        .find(|number| !used_numbers.contains(number))
        .or_else(|| (1..100).find(|number| !used_numbers.contains(number)))
        .unwrap_or(99)
}
