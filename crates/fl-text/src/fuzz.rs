//! Indel-based ratio scorers with exact floating-point results.
//!
//! Scores are `f64` in `0..=100`. The scorers are `ratio`, `token_sort_ratio`,
//! `token_set_ratio` and `wratio`; strings are compared as sequences of Unicode
//! scalar values, tokens are split on Unicode whitespace (see
//! `unicode_str::split_tokens`) and every float expression is evaluated in a
//! fixed order so results are reproducible bit for bit, including at
//! `cutoff` rounding edges.
//!
//! `*_with_cutoff` returns `0.0` for a score below the cutoff. Any cutoff above
//! 100 yields `0.0`.

use crate::indel::{Pattern, Symbol, lcs};
use crate::unicode_str::split_tokens;

const UNBASE_SCALE: f64 = 0.95;

fn chars(value: &str) -> Vec<char> {
    value.chars().collect()
}

fn char_len(value: &str) -> usize {
    if value.is_ascii() {
        value.len()
    } else {
        value.chars().count()
    }
}

/// Evaluate `$body` with `$x`/`$y` bound to both strings as byte slices when
/// both are ASCII (no copy) and as `char` slices otherwise.
macro_rules! on_seqs {
    ($a:expr, $b:expr, |$x:ident, $y:ident| $body:expr) => {{
        let (a, b): (&str, &str) = ($a, $b);
        if a.is_ascii() && b.is_ascii() {
            let ($x, $y) = (a.as_bytes(), b.as_bytes());
            $body
        } else {
            let (chars_a, chars_b) = (chars(a), chars(b));
            let ($x, $y) = (&chars_a[..], &chars_b[..]);
            $body
        }
    }};
}

/// LCS length of two strings.
fn lcs_str(a: &str, b: &str) -> usize {
    on_seqs!(a, b, |x, y| lcs(x, y))
}

/// Normalized Indel similarity for a known LCS length (0..=1), without cutoff.
fn normalized_similarity(lcs: usize, len1: usize, len2: usize) -> f64 {
    let maximum = len1 + len2;
    let dist = maximum - 2 * lcs;
    let norm_dist = if maximum == 0 {
        0.0
    } else {
        dist as f64 / maximum as f64
    };
    1.0 - norm_dist
}

/// Normalized Indel similarity with a cutoff: the similarity, or `0.0`
/// when it is below `cutoff` (0..=1 scale).
fn similarity_with_cutoff(lcs: usize, len1: usize, len2: usize, cutoff: f64) -> f64 {
    let similarity = normalized_similarity(lcs, len1, len2);
    if similarity >= cutoff {
        similarity
    } else {
        0.0
    }
}

/// [`ratio`] on symbol slices; `cutoff` on the 0..=100 scale.
fn ratio_syms<T: Symbol>(a: &[T], b: &[T], cutoff: f64) -> f64 {
    let cutoff = cutoff / 100.0;
    // The LCS cannot exceed the shorter length, so the similarity cannot exceed
    // this bound; the formula is monotone, hence skipping is exact.
    let shorter = a.len().min(b.len());
    if cutoff > 0.0 && normalized_similarity(shorter, a.len(), b.len()) < cutoff {
        return 0.0;
    }
    similarity_with_cutoff(lcs(a, b), a.len(), b.len(), cutoff) * 100.0
}

/// Normalized Indel similarity.
pub fn ratio(a: &str, b: &str) -> f64 {
    ratio_with_cutoff(a, b, 0.0)
}

/// [`ratio`] that returns `0.0` below `cutoff`.
pub fn ratio_with_cutoff(a: &str, b: &str, cutoff: f64) -> f64 {
    on_seqs!(a, b, |x, y| ratio_syms(x, y, cutoff))
}

/// Whitespace-separated tokens, sorted, duplicates kept.
fn sorted_tokens(value: &str) -> Vec<&str> {
    let mut tokens: Vec<&str> = split_tokens(value).collect();
    tokens.sort_unstable();
    tokens
}

/// Normalized Indel similarity of the sorted-token strings.
pub fn token_sort_ratio(a: &str, b: &str) -> f64 {
    token_sort_ratio_with_cutoff(a, b, 0.0)
}

/// [`token_sort_ratio`] that returns `0.0` below `cutoff`.
pub fn token_sort_ratio_with_cutoff(a: &str, b: &str, cutoff: f64) -> f64 {
    if cutoff > 100.0 {
        return 0.0;
    }
    ratio_with_cutoff(
        &sorted_tokens(a).join(" "),
        &sorted_tokens(b).join(" "),
        cutoff,
    )
}

/// `100 - 100 * dist / lensum`, zeroed under the cutoff.
fn norm_distance(dist: usize, lensum: usize, cutoff: f64) -> f64 {
    let score = if lensum == 0 {
        100.0
    } else {
        100.0 - 100.0 * dist as f64 / lensum as f64
    };
    if score >= cutoff { score } else { 0.0 }
}

/// Sorted tokens without duplicates (the token *set*).
fn dedup_sorted<'a>(sorted: &[&'a str]) -> Vec<&'a str> {
    let mut unique = sorted.to_vec();
    unique.dedup();
    unique
}

/// Sizes of the decomposition of two token sets (`set_decomposition`), gathered
/// without building any string.
struct SetStats {
    shared: usize,
    /// Characters of the shared tokens, without separators.
    sect_chars: usize,
    ab_tokens: usize,
    ab_chars: usize,
    ba_tokens: usize,
    ba_chars: usize,
}

impl SetStats {
    /// Merge two sorted, de-duplicated token lists.
    fn new<A: AsRef<str>, B: AsRef<str>>(a: &[A], b: &[B]) -> Self {
        let mut stats = Self {
            shared: 0,
            sect_chars: 0,
            ab_tokens: 0,
            ab_chars: 0,
            ba_tokens: 0,
            ba_chars: 0,
        };
        let (mut i, mut j) = (0, 0);
        while i < a.len() && j < b.len() {
            let (left, right) = (a[i].as_ref(), b[j].as_ref());
            match left.cmp(right) {
                std::cmp::Ordering::Equal => {
                    stats.shared += 1;
                    stats.sect_chars += char_len(left);
                    i += 1;
                    j += 1;
                }
                std::cmp::Ordering::Less => {
                    stats.ab_tokens += 1;
                    stats.ab_chars += char_len(left);
                    i += 1;
                }
                std::cmp::Ordering::Greater => {
                    stats.ba_tokens += 1;
                    stats.ba_chars += char_len(right);
                    j += 1;
                }
            }
        }
        for token in &a[i..] {
            stats.ab_tokens += 1;
            stats.ab_chars += char_len(token.as_ref());
        }
        for token in &b[j..] {
            stats.ba_tokens += 1;
            stats.ba_chars += char_len(token.as_ref());
        }
        stats
    }

    /// One token set is part of the other (and they share a token).
    fn subset(&self) -> bool {
        self.shared > 0 && (self.ab_tokens == 0 || self.ba_tokens == 0)
    }

    /// Length of the joined shared tokens (0 when none).
    fn sect_len(&self) -> usize {
        if self.shared == 0 {
            0
        } else {
            self.sect_chars + self.shared - 1
        }
    }

    /// Length of the joined tokens only in `a`.
    fn ab_len(&self) -> usize {
        self.ab_chars + self.ab_tokens.saturating_sub(1)
    }

    /// Length of the joined tokens only in `b`.
    fn ba_len(&self) -> usize {
        self.ba_chars + self.ba_tokens.saturating_sub(1)
    }
}

/// The tokens only in `a` and only in `b`, each joined by spaces.
fn diff_strings<A: AsRef<str>, B: AsRef<str>>(a: &[A], b: &[B]) -> (String, String) {
    let (mut diff_ab, mut diff_ba) = (String::new(), String::new());
    let push = |out: &mut String, token: &str| {
        if !out.is_empty() {
            out.push(' ');
        }
        out.push_str(token);
    };
    let (mut i, mut j) = (0, 0);
    while i < a.len() && j < b.len() {
        let (left, right) = (a[i].as_ref(), b[j].as_ref());
        match left.cmp(right) {
            std::cmp::Ordering::Equal => {
                i += 1;
                j += 1;
            }
            std::cmp::Ordering::Less => {
                push(&mut diff_ab, left);
                i += 1;
            }
            std::cmp::Ordering::Greater => {
                push(&mut diff_ba, right);
                j += 1;
            }
        }
    }
    a[i..].iter().for_each(|t| push(&mut diff_ab, t.as_ref()));
    b[j..].iter().for_each(|t| push(&mut diff_ba, t.as_ref()));
    (diff_ab, diff_ba)
}

/// `token_set_ratio` of two non-empty token sets without a shared token, from
/// the lengths of their joined sorted tokens and the LCS of those strings.
pub(crate) fn disjoint_token_set_score(a_len: usize, b_len: usize, lcs: usize, cutoff: f64) -> f64 {
    let lensum = a_len + b_len;
    let cutoff_distance = (lensum as f64 * (1.0 - cutoff / 100.0)).ceil();
    let dist = lensum - 2 * lcs;
    if dist as f64 <= cutoff_distance {
        norm_distance(dist, lensum, cutoff)
    } else {
        0.0
    }
}

/// Best of `base` and the three set ratios (`sect+ab`/`sect+ba` and `sect`).
fn set_score<A: AsRef<str>, B: AsRef<str>>(
    a: &[A],
    b: &[B],
    stats: &SetStats,
    base: f64,
    cutoff: f64,
) -> f64 {
    let (sect_len, ab_len, ba_len) = (stats.sect_len(), stats.ab_len(), stats.ba_len());
    let sect_gap = usize::from(sect_len != 0);
    // String length sect+ab <-> sect and sect+ba <-> sect.
    let sect_ab_len = sect_len + sect_gap + ab_len;
    let sect_ba_len = sect_len + sect_gap + ba_len;

    let mut result = base;
    let lensum = sect_ab_len + sect_ba_len;
    let cutoff_distance = (lensum as f64 * (1.0 - cutoff / 100.0)).ceil();
    // The distance is at least the length difference; skip building the
    // strings and the LCS when even that exceeds the cutoff distance.
    if ab_len.abs_diff(ba_len) as f64 <= cutoff_distance {
        let (diff_ab, diff_ba) = diff_strings(a, b);
        let dist = ab_len + ba_len - 2 * lcs_str(&diff_ab, &diff_ba);
        if dist as f64 <= cutoff_distance {
            result = result.max(norm_distance(dist, lensum, cutoff));
        }
    }
    // Exit early: the other ratios are 0 without a common token.
    if sect_len == 0 {
        return result;
    }
    // Only the shared part is similar, so the distances follow from the lengths.
    let sect_ab_ratio = norm_distance(sect_gap + ab_len, sect_len + sect_ab_len, cutoff);
    let sect_ba_ratio = norm_distance(sect_gap + ba_len, sect_len + sect_ba_len, cutoff);
    result.max(sect_ab_ratio).max(sect_ba_ratio)
}

/// Token-set similarity: the best of the intersection/remainder comparisons.
pub fn token_set_ratio(a: &str, b: &str) -> f64 {
    token_set_ratio_with_cutoff(a, b, 0.0)
}

/// [`token_set_ratio`] that returns `0.0` below `cutoff`.
pub fn token_set_ratio_with_cutoff(a: &str, b: &str, cutoff: f64) -> f64 {
    if cutoff > 100.0 {
        return 0.0;
    }
    token_set_ratio_tokens(&token_set(a), &token_set(b), cutoff)
}

/// The sorted, de-duplicated tokens of a string (its token *set*).
pub(crate) fn token_set(value: &str) -> Vec<&str> {
    dedup_sorted(&sorted_tokens(value))
}

/// `token_set_ratio` of two token sets from [`token_set`] (or owned equivalents).
pub(crate) fn token_set_ratio_tokens<A: AsRef<str>, B: AsRef<str>>(
    a: &[A],
    b: &[B],
    cutoff: f64,
) -> f64 {
    // FuzzyWuzzy compatibility: an empty token set scores 0.
    if a.is_empty() || b.is_empty() {
        return 0.0;
    }
    let stats = SetStats::new(a, b);
    if stats.subset() {
        return 100.0;
    }
    set_score(a, b, &stats, 0.0, cutoff)
}

/// The better of token_set and token_sort in one pass.
pub(crate) fn token_ratio(a: &str, b: &str, cutoff: f64) -> f64 {
    if cutoff > 100.0 {
        return 0.0;
    }
    let (sorted_a, sorted_b) = (sorted_tokens(a), sorted_tokens(b));
    let (unique_a, unique_b) = (dedup_sorted(&sorted_a), dedup_sorted(&sorted_b));
    let stats = SetStats::new(&unique_a, &unique_b);
    if stats.subset() {
        return 100.0;
    }
    let base = ratio_with_cutoff(&sorted_a.join(" "), &sorted_b.join(" "), cutoff);
    set_score(&unique_a, &unique_b, &stats, base, cutoff)
}

/// Converts a similarity cutoff to a distance cutoff, relaxed by a `1e-5` imprecision.
fn norm_sim_to_norm_dist(cutoff: f64) -> f64 {
    (1.0 - cutoff + 0.00001).min(1.0)
}

/// Best alignment of `s1` inside `s2`
/// (`0 < len(s1) <= len(s2)`); `cutoff` and the result on the 0..=100 scale.
fn partial_ratio_impl<T: Symbol>(s1: &[T], s2: &[T], pattern: &Pattern, mut cutoff: f64) -> f64 {
    let (len1, len2) = (s1.len(), s2.len());
    let mut score = 0.0;

    if len2 > len1 {
        // Windows of exactly len1 characters: bisect the start positions,
        // pruning ranges that cannot beat the best distance so far.
        let maximum = len1 * 2;
        let mut cutoff_dist =
            (maximum as f64 * norm_sim_to_norm_dist(cutoff / 100.0)).ceil() as usize;
        let mut best_dist = usize::MAX;
        let mut scores = vec![usize::MAX; len2 - len1];
        let mut windows = vec![(0, len2 - len1 - 1)];
        let mut new_windows = Vec::new();
        let distance = |start: usize| maximum - 2 * pattern.lcs(&s2[start..start + len1]);
        while !windows.is_empty() {
            for &(first, second) in &windows {
                for position in [first, second] {
                    if scores[position] == usize::MAX {
                        scores[position] = distance(position);
                        if scores[position] < cutoff_dist {
                            cutoff_dist = scores[position];
                            best_dist = scores[position];
                            if best_dist == 0 {
                                return 100.0;
                            }
                        }
                    }
                }
                let cell_diff = second - first;
                if cell_diff == 1 {
                    continue;
                }
                // The minimum score possible between first and second: half of
                // the cells not needed for the known edits can improve it.
                let known_edits = scores[first].abs_diff(scores[second]);
                let max_improvement = (cell_diff - known_edits / 2) / 2 * 2;
                let min_score = scores[first].min(scores[second]) as i64 - max_improvement as i64;
                if min_score < cutoff_dist as i64 {
                    let center = cell_diff / 2;
                    new_windows.push((first, first + center));
                    new_windows.push((first + center, second));
                }
            }
            std::mem::swap(&mut windows, &mut new_windows);
            new_windows.clear();
        }
        let best = (1.0 - best_dist as f64 / maximum as f64) * 100.0;
        if best >= cutoff {
            cutoff = best;
            score = best;
        }
    }

    // Windows cut off at either end of the longer string, anchored on a
    // character of the needle.
    let consider = |window: &[T], cutoff: &mut f64, score: &mut f64| -> bool {
        let similarity =
            similarity_with_cutoff(pattern.lcs(window), len1, window.len(), *cutoff / 100.0)
                * 100.0;
        if similarity > *score {
            *cutoff = similarity;
            *score = similarity;
        }
        *score == 100.0
    };
    for i in 1..len1 {
        if pattern.contains(s2[i - 1]) && consider(&s2[..i], &mut cutoff, &mut score) {
            return 100.0;
        }
    }
    for i in len2 - len1..len2 {
        if pattern.contains(s2[i]) && consider(&s2[i..], &mut cutoff, &mut score) {
            return 100.0;
        }
    }
    score
}

/// Partial ratio on symbol slices.
fn partial_ratio_syms<T: Symbol>(a: &[T], b: &[T], cutoff: f64) -> f64 {
    let (shorter, longer) = if a.len() <= b.len() { (a, b) } else { (b, a) };
    if cutoff > 100.0 {
        return 0.0;
    }
    if shorter.is_empty() {
        return if longer.is_empty() { 100.0 } else { 0.0 };
    }
    let mut score = partial_ratio_impl(shorter, longer, &Pattern::new(shorter), cutoff);
    if score != 100.0 && shorter.len() == longer.len() {
        let swapped = partial_ratio_impl(longer, shorter, &Pattern::new(longer), cutoff.max(score));
        if swapped > score {
            score = swapped;
        }
    }
    score
}

/// Best partial-window ratio, `0.0` below `cutoff`.
pub(crate) fn partial_ratio(a: &str, b: &str, cutoff: f64) -> f64 {
    on_seqs!(a, b, |x, y| partial_ratio_syms(x, y, cutoff))
}

/// Partial ratio over sorted tokens.
pub(crate) fn partial_token_ratio(a: &str, b: &str, mut cutoff: f64) -> f64 {
    if cutoff > 100.0 {
        return 0.0;
    }
    let (sorted_a, sorted_b) = (sorted_tokens(a), sorted_tokens(b));
    let (unique_a, unique_b) = (dedup_sorted(&sorted_a), dedup_sorted(&sorted_b));
    // Exit early when there is a common word in both sequences.
    if SetStats::new(&unique_a, &unique_b).shared > 0 {
        return 100.0;
    }
    let result = partial_ratio(&sorted_a.join(" "), &sorted_b.join(" "), cutoff);
    // Do not calculate the same partial_ratio twice.
    if sorted_a.len() == unique_a.len() && sorted_b.len() == unique_b.len() {
        return result;
    }
    cutoff = cutoff.max(result);
    let (diff_ab, diff_ba) = diff_strings(&unique_a, &unique_b);
    result.max(partial_ratio(&diff_ab, &diff_ba, cutoff))
}

/// Weighted ratio: the best of the plain, token and partial scorers, scaled.
pub fn wratio(a: &str, b: &str) -> f64 {
    wratio_with_cutoff(a, b, 0.0)
}

/// [`wratio`] that returns `0.0` below `cutoff`.
pub fn wratio_with_cutoff(a: &str, b: &str, cutoff: f64) -> f64 {
    if cutoff > 100.0 {
        return 0.0;
    }
    // An empty string scores 0.
    if a.is_empty() || b.is_empty() {
        return 0.0;
    }
    let (len1, len2) = (char_len(a), char_len(b));
    let len_ratio = if len1 > len2 {
        len1 as f64 / len2 as f64
    } else {
        len2 as f64 / len1 as f64
    };
    let mut score_cutoff = cutoff;
    let mut end_ratio = ratio_with_cutoff(a, b, score_cutoff);
    if len_ratio < 1.5 {
        score_cutoff = score_cutoff.max(end_ratio) / UNBASE_SCALE;
        return end_ratio.max(token_ratio(a, b, score_cutoff) * UNBASE_SCALE);
    }
    let partial_scale = if len_ratio <= 8.0 { 0.9 } else { 0.6 };
    score_cutoff = score_cutoff.max(end_ratio) / partial_scale;
    end_ratio = end_ratio.max(partial_ratio(a, b, score_cutoff) * partial_scale);
    score_cutoff = score_cutoff.max(end_ratio) / UNBASE_SCALE;
    end_ratio.max(partial_token_ratio(a, b, score_cutoff) * UNBASE_SCALE * partial_scale)
}
