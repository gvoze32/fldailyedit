//! Prefilter index for `token_set_ratio >= cutoff` searches over a fixed name list.
//!
//! Exact search over a set of names: the result always equals a full scan
//! with `token_set_ratio`, sorted by score (best first) then name position.

use std::collections::HashMap;

use crate::fuzz::{disjoint_token_set_score, token_set, token_set_ratio_tokens};
use crate::indel::Pattern;

/// Search index over normalized names.
#[derive(Debug, Clone)]
pub struct TokenSetIndex {
    names: Vec<String>,
    /// Sorted, de-duplicated tokens of each name.
    tokens: Vec<Vec<String>>,
    /// Character length of each name's sorted, de-duplicated token string.
    sorted_token_lens: Vec<usize>,
    /// The sorted, de-duplicated tokens of each name joined by spaces.
    joined: Vec<Joined>,
    by_token: HashMap<String, Vec<usize>>,
}

/// A joined token string, bytes when ASCII so scoring needs no copy.
#[derive(Debug, Clone)]
enum Joined {
    Ascii(Box<[u8]>),
    Wide(Box<[char]>),
}

/// Character length of `" ".join(sorted(set(value.split())))`.
fn sorted_token_len<T: AsRef<str>>(tokens: &[T]) -> usize {
    tokens
        .iter()
        .map(|t| t.as_ref().chars().count())
        .sum::<usize>()
        + tokens.len().saturating_sub(1)
}

impl TokenSetIndex {
    /// Index `names` (positions in the iterator become the result indices).
    pub fn new<S: Into<String>>(names: impl IntoIterator<Item = S>) -> Self {
        let names: Vec<String> = names.into_iter().map(Into::into).collect();
        let mut tokens = Vec::with_capacity(names.len());
        let mut sorted_token_lens = Vec::with_capacity(names.len());
        let mut joined = Vec::with_capacity(names.len());
        let mut by_token: HashMap<String, Vec<usize>> = HashMap::new();
        for (index, name) in names.iter().enumerate() {
            let name_tokens: Vec<String> = token_set(name).into_iter().map(str::to_owned).collect();
            sorted_token_lens.push(sorted_token_len(&name_tokens));
            let text = name_tokens.join(" ");
            joined.push(if text.is_ascii() {
                Joined::Ascii(text.into_bytes().into_boxed_slice())
            } else {
                Joined::Wide(text.chars().collect())
            });
            for token in &name_tokens {
                by_token.entry(token.clone()).or_default().push(index);
            }
            tokens.push(name_tokens);
        }
        Self {
            names,
            tokens,
            sorted_token_lens,
            joined,
            by_token,
        }
    }

    /// The indexed names.
    pub fn names(&self) -> &[String] {
        &self.names
    }

    /// Every `(index, score)` with `token_set_ratio(query, name) >= cutoff`,
    /// best score first, ties by index.
    ///
    /// Names sharing a token with the query are scored directly. For the
    /// rest the token intersection is empty, so `token_set_ratio` equals
    /// `ratio` of the sorted token strings, which cannot reach the cutoff when
    /// even a perfect subsequence of the shorter string would fall short;
    /// that length bound skips most of the catalog before any scoring.
    pub fn extract(&self, query: &str, cutoff: f64) -> Vec<(usize, f64)> {
        let query_tokens = token_set(query);
        let mut hits: Vec<(usize, f64)> = Vec::new();
        if cutoff <= 0.0 {
            // Everything qualifies (a score of 0 included).
            hits.extend(self.tokens.iter().enumerate().map(|(index, tokens)| {
                (index, token_set_ratio_tokens(&query_tokens, tokens, cutoff))
            }));
        } else if !query_tokens.is_empty() {
            let mut sharing = vec![false; self.names.len()];
            for token in &query_tokens {
                for &index in self.by_token.get(*token).into_iter().flatten() {
                    sharing[index] = true;
                }
            }
            let query_len = sorted_token_len(&query_tokens);
            // Without a shared token only the LCS of the two joined strings
            // matters; the query side is prepared once for all of them.
            let query_chars: Vec<char> = query_tokens.join(" ").chars().collect();
            let query_pattern = Pattern::new(&query_chars);
            for (index, tokens) in self.tokens.iter().enumerate() {
                let score = if sharing[index] {
                    token_set_ratio_tokens(&query_tokens, tokens, cutoff)
                } else if self.may_reach(query_len, index, cutoff) {
                    let lcs = match &self.joined[index] {
                        Joined::Ascii(text) => query_pattern.lcs(text),
                        Joined::Wide(text) => query_pattern.lcs(text),
                    };
                    disjoint_token_set_score(query_len, self.sorted_token_lens[index], lcs, cutoff)
                } else {
                    continue;
                };
                if score >= cutoff {
                    hits.push((index, score));
                }
            }
        }
        hits.sort_by(|a, b| b.1.total_cmp(&a.1).then(a.0.cmp(&b.0)));
        hits
    }

    /// Upper bound of `ratio` between the sorted token strings from their lengths.
    fn may_reach(&self, query_len: usize, index: usize, cutoff: f64) -> bool {
        let name_len = self.sorted_token_lens[index];
        let total = query_len + name_len;
        if total == 0 {
            return true;
        }
        let shorter = query_len.min(name_len);
        let bound = 100.0 * (2 * shorter) as f64 / total as f64;
        // Margin absorbs float rounding between this bound and the exact score.
        bound + 1e-6 >= cutoff
    }
}
