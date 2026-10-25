//! Position labels: broad line categories and provider ↔ PES compatibility.

use fl_text::strip;

/// Broad position line.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum Category {
    Goalkeeper,
    Defender,
    Midfielder,
    Forward,
    Unknown,
}

const GK: &[&str] = &["GK", "GOALKEEPER", "KEEPER", "GOALIE"];
const DEF: &[&str] = &[
    "CB", "LB", "RB", "DF", "DEF", "DEFENDER", "BACK", "FULLBACK",
];
const MID: &[&str] = &[
    "DMF",
    "CMF",
    "AMF",
    "LMF",
    "RMF",
    "DM",
    "CM",
    "CAM",
    "AM",
    "LM",
    "RM",
    "MF",
    "MIDFIELDER",
    "MIDFIELD",
];
const FWD: &[&str] = &[
    "LWF", "RWF", "SS", "CF", "ST", "LW", "RW", "FW", "FWD", "ATTACKER", "FORWARD", "STRIKER",
];

/// Labels that providers and PES legitimately place on different lines for the
/// same player: attacking midfielders/second strikers, wingers/wide
/// midfielders, and full-backs/wide midfielders (wing-backs).
const CROSS_LINE_GROUPS: [&[&str]; 3] = [
    &["AMF", "CAM", "AM", "SS"],
    &["LW", "RW", "LWF", "RWF", "LM", "RM", "LMF", "RMF"],
    &["LB", "RB", "FULLBACK", "LM", "RM", "LMF", "RMF"],
];

/// A non-empty position label, stripped and upper-cased once.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct Position {
    key: String,
    category: Category,
}

impl Position {
    /// `None` for an empty label; whitespace-only labels are kept and classify
    /// as unknown.
    pub(crate) fn parse(raw: &str) -> Option<Self> {
        if raw.is_empty() {
            return None;
        }
        let key = strip(raw).to_uppercase();
        let category = category_of(&key);
        Some(Self { key, category })
    }

    /// Stripped, upper-cased label.
    pub(crate) fn key(&self) -> &str {
        &self.key
    }

    pub(crate) fn category(&self) -> Category {
        self.category
    }
}

fn category_of(key: &str) -> Category {
    if GK.contains(&key) {
        Category::Goalkeeper
    } else if DEF.contains(&key) {
        Category::Defender
    } else if MID.contains(&key) {
        Category::Midfielder
    } else if FWD.contains(&key) {
        Category::Forward
    } else {
        Category::Unknown
    }
}

/// Reject known line mismatches while allowing missing metadata.
pub(crate) fn compatible(provider: &Position, pes: &Position) -> bool {
    if provider.category == Category::Unknown || pes.category == Category::Unknown {
        return true;
    }
    if CROSS_LINE_GROUPS
        .iter()
        .any(|group| group.contains(&provider.key.as_str()) && group.contains(&pes.key.as_str()))
    {
        return true;
    }
    provider.category == pes.category
}

#[cfg(test)]
mod tests {
    use super::*;

    /// `compatible` for raw labels; a missing label is compatible with anything.
    fn positions_compatible(provider: &str, pes: &str) -> bool {
        match (Position::parse(provider), Position::parse(pes)) {
            (Some(provider), Some(pes)) => compatible(&provider, &pes),
            _ => true,
        }
    }

    #[test]
    fn wide_players_match_across_lines() {
        for (provider, pes) in [("LW", "LMF"), ("RW", "RMF"), ("LM", "LWF"), ("RM", "RB")] {
            assert!(positions_compatible(provider, pes), "{provider} vs {pes}");
        }
    }

    #[test]
    fn central_lines_stay_incompatible() {
        assert!(!positions_compatible("CF", "DMF"));
        assert!(!positions_compatible("LW", "CB"));
    }

    #[test]
    fn keeper_labels_are_goalkeepers_and_unknown_is_permissive() {
        for label in ["Keeper", "Goalkeeper", "Goalie", " gk "] {
            assert!(!positions_compatible(label, "CF"), "{label}");
            assert!(positions_compatible(label, "GK"), "{label}");
        }
        assert!(positions_compatible("unknown", "CF"));
        assert!(positions_compatible("", "CF"));
    }
}
