//! Text folding and the key normalizers built on it.
//!
//! Several normalizers are built on [`fold_text`]; they genuinely differ on
//! punctuation, so each keeps its own named function:
//!
//! | function | punctuation handling |
//! |---|---|
//! | [`fold_text`] | kept |
//! | [`normalize_dotless`] | `.` becomes a separator |
//! | [`normalize_alnum`] | every non `[a-z0-9]` run becomes a separator |
//! | [`alnum_key`] | everything but alphanumerics dropped, no separators |

use unicode_normalization::UnicodeNormalization;
use unicode_normalization::char::canonical_combining_class;

use crate::unicode_str::{collapse_whitespace, is_alnum, is_space, push_casefolded};

/// Letters NFKD leaves undecomposed, with their ASCII transliteration.
fn transliteration(c: char) -> Option<&'static str> {
    Some(match c {
        'ı' => "i",
        'Ł' => "L",
        'ł' => "l",
        'Đ' | 'Ð' => "D",
        'đ' | 'ð' => "d",
        'Þ' => "Th",
        'þ' => "th",
        'Æ' => "AE",
        'æ' => "ae",
        'Œ' => "OE",
        'œ' => "oe",
        'Ø' => "O",
        'ø' => "o",
        'Ħ' => "H",
        'ħ' => "h",
        'Ŧ' => "T",
        'ŧ' => "t",
        'Ŋ' => "N",
        'ŋ' => "n",
        'ĸ' => "k",
        _ => return None,
    })
}

/// Fold a human-readable name into its comparison form.
///
/// Strips diacritics (NFKD, then combining marks), transliterates letters NFKD
/// does not decompose (`ł` → `l`, `ø` → `o`, `ı` → `i`), casefolds, and
/// collapses whitespace, so `Łukasz Fabiański` and `Lukasz Fabianski` compare equal.
/// An absent value is the empty string at the caller.
pub fn fold_text(value: &str) -> String {
    let mut out = String::with_capacity(value.len());
    let mut pending_space = false;
    let mut push = |c: char, out: &mut String| {
        if is_space(c) {
            pending_space = true;
            return;
        }
        if pending_space && !out.is_empty() {
            out.push(' ');
        }
        pending_space = false;
        push_casefolded(out, c);
    };
    if value.is_ascii() {
        for c in value.chars() {
            push(c, &mut out);
        }
        return out;
    }
    for c in value.nfkd() {
        if canonical_combining_class(c) != 0 {
            continue;
        }
        match transliteration(c) {
            Some(replacement) => replacement.chars().for_each(|r| push(r, &mut out)),
            None => push(c, &mut out),
        }
    }
    out
}

/// [`fold_text`] with `.` treated as a separator.
pub fn normalize_dotless(value: &str) -> String {
    collapse_whitespace(&fold_text(value).replace('.', " "))
}

/// [`fold_text`] with every run of characters outside `[a-z0-9]` collapsed to
/// one space.
pub fn normalize_alnum(value: &str) -> String {
    let folded = fold_text(value);
    let mut out = String::with_capacity(folded.len());
    let mut pending_space = false;
    for c in folded.chars() {
        if c.is_ascii_lowercase() || c.is_ascii_digit() {
            if pending_space && !out.is_empty() {
                out.push(' ');
            }
            pending_space = false;
            out.push(c);
        } else {
            pending_space = true;
        }
    }
    out
}

/// [`fold_text`] keeping only alphanumeric characters, no separators
/// (team labels, table header keys).
pub fn alnum_key(value: &str) -> String {
    fold_text(value).chars().filter(|&c| is_alnum(c)).collect()
}
