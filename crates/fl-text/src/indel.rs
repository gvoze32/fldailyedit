//! Bit-parallel longest common subsequence (Hyyrö) over `u8` or `char` symbols.
//!
//! The Indel similarity is `len1 + len2 - 2 * LCS`, so everything in
//! `fuzz` reduces to [`Pattern::lcs`]. ASCII strings run on their bytes
//! without copying; everything else on collected `char`s.

use std::collections::HashMap;

/// One comparable unit of a string: a byte of an ASCII string or a `char`.
pub(crate) trait Symbol: Copy {
    /// The code point.
    fn code(self) -> u32;
}

impl Symbol for u8 {
    fn code(self) -> u32 {
        u32::from(self)
    }
}

impl Symbol for char {
    fn code(self) -> u32 {
        u32::from(self)
    }
}

/// Match masks of one string, reusable against many texts.
pub(crate) struct Pattern {
    len: usize,
    words: usize,
    /// ASCII masks of a single-word pattern (the common case), no allocation.
    single: [u64; 128],
    /// `words` masks per ASCII symbol (`[code * words + word]`) of longer patterns.
    ascii: Vec<u64>,
    other: HashMap<u32, Vec<u64>>,
}

impl Pattern {
    pub(crate) fn new<T: Symbol>(pattern: &[T]) -> Self {
        let words = pattern.len().div_ceil(64);
        let mut this = Self {
            len: pattern.len(),
            words,
            single: [0; 128],
            ascii: if words > 1 {
                vec![0; 128 * words]
            } else {
                Vec::new()
            },
            other: HashMap::new(),
        };
        for (index, &symbol) in pattern.iter().enumerate() {
            let (word, bit) = (index / 64, 1u64 << (index % 64));
            let code = symbol.code();
            if code < 128 {
                if words == 1 {
                    this.single[code as usize] |= bit;
                } else {
                    this.ascii[code as usize * words + word] |= bit;
                }
            } else {
                this.other.entry(code).or_insert_with(|| vec![0; words])[word] |= bit;
            }
        }
        this
    }

    /// Mask of `code` for `word` (`0` when absent).
    fn mask(&self, code: u32, word: usize) -> u64 {
        if code < 128 {
            if self.words == 1 {
                self.single[code as usize]
            } else {
                self.ascii[code as usize * self.words + word]
            }
        } else {
            self.other.get(&code).map_or(0, |masks| masks[word])
        }
    }

    /// Whether the symbol occurs in the pattern.
    pub(crate) fn contains<T: Symbol>(&self, symbol: T) -> bool {
        let code = symbol.code();
        (0..self.words).any(|word| self.mask(code, word) != 0)
    }

    /// Length of the longest common subsequence of the pattern and `text`.
    pub(crate) fn lcs<T: Symbol>(&self, text: &[T]) -> usize {
        if self.len == 0 || text.is_empty() {
            return 0;
        }
        if self.words == 1 {
            let mut s = u64::MAX;
            for &symbol in text {
                let u = s & self.mask(symbol.code(), 0);
                s = s.wrapping_add(u) | (s & !u);
            }
            let mask = if self.len == 64 {
                u64::MAX
            } else {
                (1u64 << self.len) - 1
            };
            return (!s & mask).count_ones() as usize;
        }
        let mut s = vec![u64::MAX; self.words];
        for &symbol in text {
            let code = symbol.code();
            let mut carry = false;
            for (index, word) in s.iter_mut().enumerate() {
                let u = *word & self.mask(code, index);
                let (sum, c1) = word.overflowing_add(u);
                let (sum, c2) = sum.overflowing_add(u64::from(carry));
                carry = c1 | c2;
                *word = sum | (*word & !u);
            }
        }
        let mut lcs = 0;
        for (index, word) in s.iter().enumerate() {
            let bits = if index + 1 == self.words && !self.len.is_multiple_of(64) {
                (1u64 << (self.len % 64)) - 1
            } else {
                u64::MAX
            };
            lcs += (!word & bits).count_ones() as usize;
        }
        lcs
    }
}

/// LCS length of two sequences (the shorter one is used as the pattern).
pub(crate) fn lcs<T: Symbol>(a: &[T], b: &[T]) -> usize {
    if a.len() <= b.len() {
        Pattern::new(a).lcs(b)
    } else {
        Pattern::new(b).lcs(a)
    }
}
