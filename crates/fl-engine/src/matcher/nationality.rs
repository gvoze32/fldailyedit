//! Nationality evidence.
//!
//! Folded nationalities are not compared by substring containment (that would
//! make "niger" match "nigeria", "oman" match "romania" and "ireland" match
//! "northern ireland"). Two folded nationalities match only when they are equal
//! or one is a run of whole tokens inside the other and the remaining tokens do
//! not name a different country ("south", "equatorial", "bissau", ...):
//! "republic of ireland" ~ "ireland", "korea republic" ~ "korea", but never
//! "guinea" ~ "equatorial guinea".

use fl_text::name_tokens;

/// Tokens that turn a country name into a different country.
const DISTINCTION_TOKENS: [&str; 16] = [
    "american",
    "bissau",
    "central",
    "democratic",
    "dr",
    "east",
    "eastern",
    "equatorial",
    "new",
    "north",
    "northern",
    "papua",
    "south",
    "southern",
    "west",
    "western",
];

/// Whether two already-folded nationalities denote the same country.
pub(crate) fn matches(left: &str, right: &str) -> bool {
    if left == right {
        return !left.is_empty();
    }
    let left_tokens = name_tokens(left);
    let right_tokens = name_tokens(right);
    if left_tokens.is_empty() || right_tokens.is_empty() {
        return false;
    }
    if left_tokens == right_tokens {
        return true;
    }
    if left_tokens.len() == right_tokens.len() {
        return false;
    }
    let (short, long) = if left_tokens.len() < right_tokens.len() {
        (&left_tokens, &right_tokens)
    } else {
        (&right_tokens, &left_tokens)
    };
    long.windows(short.len())
        .enumerate()
        .any(|(start, window)| {
            window == short.as_slice()
                && long[..start]
                    .iter()
                    .chain(&long[start + short.len()..])
                    .all(|token| !DISTINCTION_TOKENS.contains(token))
        })
}

#[cfg(test)]
mod tests {
    use super::matches;

    #[test]
    fn substring_countries_do_not_match() {
        assert!(!matches("niger", "nigeria"));
        assert!(!matches("nigeria", "niger"));
        assert!(!matches("oman", "romania"));
        assert!(!matches("india", "indonesia"));
    }

    #[test]
    fn qualified_countries_are_distinct() {
        assert!(!matches("guinea", "equatorial guinea"));
        assert!(!matches("guinea", "guinea bissau"));
        assert!(!matches("guinea", "papua new guinea"));
        assert!(!matches("ireland", "northern ireland"));
        assert!(!matches("congo", "dr congo"));
        assert!(!matches("sudan", "south sudan"));
        assert!(!matches("africa", "south africa"));
    }

    #[test]
    fn same_country_spellings_match() {
        assert!(matches("spain", "spain"));
        assert!(matches("ireland", "republic of ireland"));
        assert!(matches("korea", "korea republic"));
        assert!(matches("korea republic", "korea"));
        assert!(matches("guinea bissau", "guinea-bissau"));
    }

    #[test]
    fn empty_never_matches() {
        assert!(!matches("", ""));
        assert!(!matches("", "spain"));
        assert!(!matches("spain", ""));
    }
}
