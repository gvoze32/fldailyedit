//! Text normalization, name/club folding and exact fuzzy scoring.
//!
//! This crate is the single home of the text helpers shared by the scrapers and
//! the matcher: `fold_text` and its variants, club affix stripping, non-club
//! labels, and the Indel-based fuzzy scorers (bit-reproducible results).

mod club;
mod fold;
pub mod fuzz;
mod indel;
mod index;
mod tables;
mod unicode_str;

#[cfg(test)]
mod corpus_tests;
#[cfg(test)]
mod unit_tests;

pub use club::{
    NON_CLUB_LABELS, categories_compatible, category_markers, clean_club_name, club_key_narrow,
    has_non_senior_marker, is_non_club, is_non_club_key, name_tokens,
};
pub use fold::{alnum_key, fold_text, normalize_alnum, normalize_dotless};
pub use index::TokenSetIndex;
pub use unicode_str::{casefold, split_lines, strip};
