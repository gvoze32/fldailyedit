//! Club-name helpers: non-club labels, affix stripping, youth/women category markers.
//!
//! The patterns are matched by hand (the crate has no regex dependency); word
//! boundaries, `\d`, `\s` and case-insensitivity follow Unicode string semantics.

use std::collections::BTreeSet;

use crate::fold::{fold_text, normalize_alnum};
use crate::unicode_str::{casefold, collapse_whitespace, is_decimal, is_space, is_word};

/// The one canonical set of "not a real club" labels, in folded form.
///
/// Every consumer shares this set: a `from` of "Retired"/"Career break" is a
/// non-club label just like "Free Agent", "Without Club" and "Unattached".
pub const NON_CLUB_LABELS: [&str; 6] = [
    "",
    "free agent",
    "without club",
    "unattached",
    "career break",
    "retired",
];

/// Whether an already-normalized key is a non-club label.
///
/// Callers that normalize with [`crate::normalize_dotless`] or
/// [`crate::normalize_alnum`] use this on their own key; everyone else uses
/// [`is_non_club`].
pub fn is_non_club_key(key: &str) -> bool {
    NON_CLUB_LABELS.contains(&key)
}

/// Whether a raw club label is a non-club label after [`fold_text`].
///
/// Diacritics and inner whitespace runs are folded, so it accepts a superset
/// of a plain casefolded comparison.
pub fn is_non_club(value: &str) -> bool {
    is_non_club_key(&fold_text(value))
}

/// Wide club affix alternatives (the `1\.\s*fc` tail is handled separately).
const WIDE_AFFIXES: &[&str] = &[
    "fc", "cf", "sc", "ac", "cd", "ud", "fk", "sk", "as", "us", "ss", "sv", "vfl", "vfb", "spvgg",
    "kaa", "krc", "rsc", "vv", "afc", "bsc", "ogc", "club", "calcio", "sad", "kv", "tsv", "fsv",
];

/// Narrow club affix alternatives.
const NARROW_AFFIXES: &[&str] = &[
    "fc", "cf", "sc", "ac", "cd", "ud", "fk", "sk", "as", "us", "ss", "sv", "vfl", "vfb", "afc",
    "club", "calcio", "sad", "kv",
];

/// End of `lit` matched at `chars[at..]`, if present.
fn match_literal(chars: &[char], at: usize, lit: &str) -> Option<usize> {
    let mut pos = at;
    for expected in lit.chars() {
        if chars.get(pos) != Some(&expected) {
            return None;
        }
        pos += 1;
    }
    Some(pos)
}

/// End of `1\.\s*fc` matched at `chars[at..]`.
fn match_one_dot_fc(chars: &[char], at: usize) -> Option<usize> {
    let mut pos = match_literal(chars, at, "1.")?;
    while chars.get(pos).is_some_and(|&c| is_space(c)) {
        pos += 1;
    }
    match_literal(chars, pos, "fc")
}

/// Replace every `\b(?:alt|...)\b` match in `folded` (already lowercase) with a
/// space and collapse whitespace.
fn strip_affixes(folded: &str, affixes: &[&str], one_dot_fc: bool) -> String {
    let chars: Vec<char> = folded.chars().collect();
    let mut out = String::with_capacity(folded.len());
    let mut i = 0;
    while i < chars.len() {
        let boundary_before = i == 0 || !is_word(chars[i - 1]);
        let end = if boundary_before {
            affixes
                .iter()
                .filter_map(|lit| match_literal(&chars, i, lit))
                .chain(one_dot_fc.then(|| match_one_dot_fc(&chars, i)).flatten())
                // Alternatives are tried in order; the first whose trailing `\b` holds wins.
                .find(|&end| chars.get(end).is_none_or(|&next| !is_word(next)))
        } else {
            None
        };
        match end {
            Some(end) => {
                out.push(' ');
                i = end;
            }
            None => {
                out.push(chars[i]);
                i += 1;
            }
        }
    }
    collapse_whitespace(&out)
}

/// Fold a club name and strip the wide affix list:
/// `FC Barcelona` → `barcelona`, `1. FC Köln` → `koln`.
pub fn clean_club_name(name: &str) -> String {
    strip_affixes(&fold_text(name), WIDE_AFFIXES, true)
}

/// [`normalize_alnum`] then strip the narrow affix list
/// ([`club_key_narrow`]). Unlike
/// [`clean_club_name`] it keeps `vv`, `tsv`, `1. fc`, ... and removes punctuation.
pub fn club_key_narrow(value: &str) -> String {
    strip_affixes(&normalize_alnum(value), NARROW_AFFIXES, false)
}

/// Case-insensitive view of a character against lowercase ASCII literals
/// (simple lowercase plus the `i`/`ı` and `s`/`ſ` equivalences).
fn re_matches(c: char, literal: char) -> bool {
    let lower = c.to_lowercase().next().unwrap_or(c);
    lower == literal || (literal == 'i' && lower == 'ı') || (literal == 's' && lower == 'ſ')
}

fn match_literal_ignorecase(chars: &[char], at: usize, lit: &str) -> Option<usize> {
    let mut pos = at;
    for expected in lit.chars() {
        if !chars.get(pos).is_some_and(|&c| re_matches(c, expected)) {
            return None;
        }
        pos += 1;
    }
    Some(pos)
}

/// Candidate ends, in regex preference order, of one alternative of the
/// category regex at `at`. The trailing `\b` is checked by the caller.
fn category_alternative(chars: &[char], at: usize, lit: &str) -> Vec<usize> {
    let literal = |text: &str| match_literal_ignorecase(chars, at, text);
    match lit {
        // reserves?
        "reserves?" => {
            let mut ends = Vec::new();
            if let Some(end) = literal("reserve") {
                if chars.get(end).is_some_and(|&c| re_matches(c, 's')) {
                    ends.push(end + 1);
                }
                ends.push(end);
            }
            ends
        }
        // next\s+gen
        "next\\s+gen" => {
            let Some(mut pos) = literal("next") else {
                return Vec::new();
            };
            let spaces_start = pos;
            while chars.get(pos).is_some_and(|&c| is_space(c)) {
                pos += 1;
            }
            if pos == spaces_start {
                return Vec::new();
            }
            match_literal_ignorecase(chars, pos, "gen")
                .into_iter()
                .collect()
        }
        // u[ -]?\d{2}
        "u[ -]?\\d{2}" => {
            let Some(after_u) = literal("u") else {
                return Vec::new();
            };
            let digits_at = |pos: usize| {
                (chars.get(pos).is_some_and(|&c| is_decimal(c))
                    && chars.get(pos + 1).is_some_and(|&c| is_decimal(c)))
                .then_some(pos + 2)
            };
            let mut ends = Vec::new();
            if chars.get(after_u).is_some_and(|&c| c == ' ' || c == '-') {
                ends.extend(digits_at(after_u + 1));
            }
            ends.extend(digits_at(after_u));
            ends
        }
        plain => literal(plain).into_iter().collect(),
    }
}

const CATEGORY_ALTERNATIVES: &[&str] = &[
    "women",
    "woman",
    "ladies",
    "feminine",
    "femenino",
    "feminino",
    "frauen",
    "academy",
    "youth",
    "reserves?",
    "primavera",
    "next\\s+gen",
    "u[ -]?\\d{2}",
    "ii",
    "b",
];

/// Finds every `\b(?:women|...|u[ -]?\d{2}|ii|b)\b` match (case-insensitive);
/// `next_gen` adds the `next\s+gen` alternative. Returns matched substrings.
fn category_matches(name: &str, next_gen: bool) -> Vec<String> {
    let chars: Vec<char> = name.chars().collect();
    let mut found = Vec::new();
    let mut i = 0;
    while i < chars.len() {
        let boundary_before = i == 0 || is_word(chars[i - 1]) != is_word(chars[i]);
        let end = if boundary_before {
            CATEGORY_ALTERNATIVES
                .iter()
                .filter(|alt| next_gen || **alt != "next\\s+gen")
                .flat_map(|alt| category_alternative(&chars, i, alt))
                .find(|&end| {
                    end > i
                        && match chars.get(end) {
                            None => is_word(chars[end - 1]),
                            Some(&next) => is_word(chars[end - 1]) != is_word(next),
                        }
                })
        } else {
            None
        };
        match end {
            Some(end) => {
                found.push(chars[i..end].iter().collect());
                i = end;
            }
            None => i += 1,
        }
    }
    found
}

/// Casefolded youth/women/reserve markers in a club name
/// (as a set).
pub fn category_markers(name: &str) -> BTreeSet<String> {
    category_matches(name, true)
        .into_iter()
        .map(|marker| casefold(&marker))
        .collect()
}

/// Both names carry the same markers.
pub fn categories_compatible(left: &str, right: &str) -> bool {
    category_markers(left) == category_markers(right)
}

/// Whether the name has a non-senior marker;
/// the same markers as [`category_markers`] except `next gen`.
pub fn has_non_senior_marker(name: &str) -> bool {
    !category_matches(name, false).is_empty()
}

/// Maximal alphanumeric runs of a name, without empty strings.
pub fn name_tokens(value: &str) -> Vec<&str> {
    value
        .split(|c: char| !crate::unicode_str::is_alnum(c))
        .filter(|token| !token.is_empty())
        .collect()
}
