//! Unicode string semantics (whitespace, alphanumerics, decimals, case folding, line
//! splitting) reproduced on Rust strings.

use std::cmp::Ordering;

use crate::tables::{ALNUM_RANGES, CASEFOLD_OVERRIDES, DECIMAL_RANGES};

/// Whether `c` is Unicode whitespace (also the token separator set of [`split_whitespace`]).
pub(crate) fn is_space(c: char) -> bool {
    matches!(
        c,
        '\t'..='\r'
            | '\u{1c}'..='\u{20}'
            | '\u{85}'
            | '\u{a0}'
            | '\u{1680}'
            | '\u{2000}'..='\u{200a}'
            | '\u{2028}'
            | '\u{2029}'
            | '\u{202f}'
            | '\u{205f}'
            | '\u{3000}'
    )
}

fn in_ranges(ranges: &[(u32, u32)], c: char) -> bool {
    let cp = c as u32;
    ranges
        .binary_search_by(|&(lo, hi)| {
            if cp < lo {
                Ordering::Greater
            } else if cp > hi {
                Ordering::Less
            } else {
                Ordering::Equal
            }
        })
        .is_ok()
}

/// Whether `c` is alphanumeric (regex `\w` minus `_`).
///
/// Table driven: std's `is_alphanumeric` also accepts combining marks with the
/// Other_Alphabetic property (U+0345, U+05B0, ...), which this definition rejects.
pub(crate) fn is_alnum(c: char) -> bool {
    if c.is_ascii() {
        return c.is_ascii_alphanumeric();
    }
    in_ranges(ALNUM_RANGES, c)
}

/// Regex `\w`: alphanumeric or underscore.
pub(crate) fn is_word(c: char) -> bool {
    c == '_' || is_alnum(c)
}

/// Whether `c` is a decimal digit (regex `\d` on Unicode strings).
pub(crate) fn is_decimal(c: char) -> bool {
    if c.is_ascii() {
        return c.is_ascii_digit();
    }
    in_ranges(DECIMAL_RANGES, c)
}

/// Runs of Unicode whitespace separate tokens; empty tokens are dropped.
pub(crate) fn split_whitespace(value: &str) -> impl Iterator<Item = &str> {
    value.split(is_space).filter(|token| !token.is_empty())
}

/// Tokens as the fuzzy `token_*` scorers split them.
///
/// Splits on the whitespace set of [`is_space`], except that strings made only
/// of characters below U+0100 (8-bit strings) do not split on U+0085
/// and U+00A0.
pub(crate) fn split_tokens(value: &str) -> impl Iterator<Item = &str> {
    let wide = value.chars().any(|c| c as u32 > 0xFF);
    value
        .split(move |c: char| is_space(c) && (wide || (c != '\u{85}' && c != '\u{a0}')))
        .filter(|token| !token.is_empty())
}

/// Whitespace-separated tokens joined by single spaces.
pub(crate) fn collapse_whitespace(value: &str) -> String {
    let mut out = String::with_capacity(value.len());
    for token in split_whitespace(value) {
        if !out.is_empty() {
            out.push(' ');
        }
        out.push_str(token);
    }
    out
}

/// Trims leading and trailing Unicode whitespace.
pub fn strip(value: &str) -> &str {
    value.trim_matches(is_space)
}

/// Full Unicode case folding.
pub fn casefold(value: &str) -> String {
    let mut out = String::with_capacity(value.len());
    for c in value.chars() {
        push_casefolded(&mut out, c);
    }
    out
}

pub(crate) fn push_casefolded(out: &mut String, c: char) {
    if c.is_ascii() {
        out.push(c.to_ascii_lowercase());
        return;
    }
    match CASEFOLD_OVERRIDES.binary_search_by_key(&(c as u32), |&(key, _)| key) {
        Ok(index) => out.push_str(CASEFOLD_OVERRIDES[index].1),
        Err(_) => out.extend(c.to_lowercase()),
    }
}

/// Split on every line boundary (`\n`, `\r`, `\r\n`,
/// VT, FF, FS, GS, RS, NEL, LS, PS); no empty trailing line.
pub fn split_lines(text: &str) -> Vec<&str> {
    let mut lines = Vec::new();
    let mut start = 0;
    let mut chars = text.char_indices().peekable();
    while let Some((index, ch)) = chars.next() {
        let width = match ch {
            '\r' => {
                if matches!(chars.peek(), Some((_, '\n'))) {
                    chars.next();
                    2
                } else {
                    1
                }
            }
            '\n' | '\x0b' | '\x0c' | '\x1c' | '\x1d' | '\x1e' | '\u{85}' | '\u{2028}'
            | '\u{2029}' => ch.len_utf8(),
            _ => continue,
        };
        lines.push(&text[start..index]);
        start = index + width;
    }
    if start < text.len() {
        lines.push(&text[start..]);
    }
    lines
}
