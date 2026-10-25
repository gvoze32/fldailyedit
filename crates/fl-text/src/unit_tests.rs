//! Hand-written behaviour checks (readable examples; exhaustive parity lives in
//! `corpus_tests`).

use crate::fuzz;
use crate::{
    NON_CLUB_LABELS, TokenSetIndex, alnum_key, casefold, categories_compatible, category_markers,
    clean_club_name, club_key_narrow, fold_text, has_non_senior_marker, is_non_club,
    is_non_club_key, name_tokens, normalize_alnum, normalize_dotless, split_lines,
};

#[test]
fn fold_text_strips_diacritics_and_transliterates() {
    assert_eq!(fold_text("Łukasz Fabiański"), "lukasz fabianski");
    assert_eq!(fold_text("Ørjan  Nyland "), "orjan nyland");
    assert_eq!(fold_text("Straße"), "strasse");
    assert_eq!(fold_text("ＦＣ　Ｔｏｋｙｏ"), "fc tokyo");
    assert_eq!(fold_text(""), "");
    assert_eq!(fold_text(" \t\u{a0} "), "");
}

#[test]
fn normalizers_differ_only_on_punctuation() {
    assert_eq!(fold_text("A.C. Milan"), "a.c. milan");
    assert_eq!(normalize_dotless("A.C. Milan"), "a c milan");
    assert_eq!(normalize_alnum("St. Pauli's"), "st pauli s");
    assert_eq!(alnum_key("St. Pauli's"), "stpaulis");
}

#[test]
fn club_affix_lists_differ_between_variants() {
    assert_eq!(clean_club_name("FC Barcelona"), "barcelona");
    assert_eq!(clean_club_name("1. FC Köln"), "koln");
    assert_eq!(
        clean_club_name("Union Saint-Gilloise"),
        "union saint-gilloise"
    );
    // The narrow list keeps `1. fc` pieces it does not know and drops punctuation.
    assert_eq!(club_key_narrow("1. FC Köln"), "1 koln");
    assert_eq!(clean_club_name("TSV 1860"), "1860");
    assert_eq!(club_key_narrow("TSV 1860"), "tsv 1860");
    // Affixes only match as whole words.
    assert_eq!(clean_club_name("Asteras"), "asteras");
    assert_eq!(clean_club_name("Real Sociedad SAD"), "real sociedad");
}

#[test]
fn non_club_labels_are_one_canonical_set() {
    for label in NON_CLUB_LABELS {
        assert!(is_non_club_key(label));
    }
    for label in [
        "Free Agent",
        "Without Club",
        "Unattached",
        "Career Break",
        "Retired",
        " ",
    ] {
        assert!(is_non_club(label), "{label:?}");
    }
    assert!(!is_non_club("Retired FC"));
    assert!(is_non_club_key(&normalize_dotless("Retired.")));
    assert!(!is_non_club("Retired."));
}

#[test]
fn category_markers_gate_youth_and_women_clubs() {
    assert!(categories_compatible("Ajax", "AFC Ajax"));
    assert!(!categories_compatible("Ajax", "Ajax U19"));
    assert!(!categories_compatible("Barcelona", "FC Barcelona Femenino"));
    assert!(
        categories_compatible("Ajax U19", "Ajax u-19")
            == (category_markers("Ajax U19") == category_markers("Ajax u-19"))
    );
    assert!(has_non_senior_marker("Ajax II"));
    assert!(!has_non_senior_marker("Ajax Next Gen"));
    assert!(category_markers("Ajax Next  Gen").contains("next  gen"));
    assert!(!has_non_senior_marker("Bilbao Athletic"));
}

#[test]
fn name_tokens_split_on_punctuation() {
    assert_eq!(name_tokens("Hong Myung-Bo"), ["Hong", "Myung", "Bo"]);
    assert_eq!(name_tokens("a_b  c.d"), ["a", "b", "c", "d"]);
    assert!(name_tokens("--").is_empty());
}

#[test]
fn casefold_is_full_case_folding() {
    assert_eq!(casefold("Straße"), "strasse");
    assert_eq!(casefold("ΣΑΣ ς"), "σασ σ");
    assert_eq!(casefold("ꭰ"), "Ꭰ");
}

#[test]
fn scorers_match_documented_values() {
    assert_eq!(
        fuzz::ratio("this is a test", "this is a test!"),
        96.55172413793103
    );
    assert_eq!(
        fuzz::token_sort_ratio("fuzzy wuzzy was a bear", "wuzzy fuzzy was a bear"),
        100.0
    );
    assert_eq!(
        fuzz::token_set_ratio(
            "fuzzy was a bear but not a dog",
            "fuzzy was a bear but not a cat"
        ),
        92.3076923076923
    );
    assert_eq!(fuzz::ratio("", ""), 100.0);
    assert_eq!(fuzz::wratio("", "a"), 0.0);
    assert_eq!(fuzz::token_set_ratio("", ""), 0.0);
    assert_eq!(fuzz::wratio("neymar", "neymar jr"), 90.0);
}

#[test]
fn token_set_ratio_treats_token_subsets_as_identical() {
    // Known weakness of the token-set scorer: callers must gate on it.
    assert_eq!(fuzz::token_set_ratio("inter", "inter miami"), 100.0);
    assert_eq!(
        fuzz::token_set_ratio("real madrid", "real madrid castilla"),
        100.0
    );
    assert!(fuzz::ratio("inter", "inter miami") < 100.0);
}

#[test]
fn cutoffs_zero_scores_below_and_above_100() {
    assert_eq!(fuzz::ratio_with_cutoff("abcd", "abcf", 75.0), 75.0);
    assert_eq!(fuzz::ratio_with_cutoff("abcd", "abcf", 75.5), 0.0);
    assert_eq!(fuzz::token_set_ratio_with_cutoff("a b", "a b", 100.5), 0.0);
    assert_eq!(fuzz::wratio_with_cutoff("a b", "a b", 100.5), 0.0);
}

#[test]
fn token_whitespace_depends_on_string_width() {
    // U+00A0 separates tokens only when the string has a character above U+00FF.
    assert_eq!(fuzz::token_sort_ratio("xx\u{a0}yy", "xx yy"), 80.0);
    assert_eq!(
        fuzz::token_sort_ratio("xx\u{a0}yy\u{142}", "xx yy\u{142}"),
        100.0
    );
}

/// Reference for [`TokenSetIndex::extract`]: score every choice, best score
/// first, ties in choice order.
pub(crate) fn full_scan<'a>(
    query: &str,
    choices: impl IntoIterator<Item = &'a str>,
    cutoff: f64,
) -> Vec<(usize, f64)> {
    let mut hits: Vec<(usize, f64)> = choices
        .into_iter()
        .enumerate()
        .filter_map(|(index, choice)| {
            let score = fuzz::token_set_ratio_with_cutoff(query, choice, cutoff);
            (score >= cutoff).then_some((index, score))
        })
        .collect();
    hits.sort_by(|a, b| b.1.total_cmp(&a.1).then(a.0.cmp(&b.0)));
    hits
}

#[test]
fn token_set_index_equals_full_scan() {
    let names = [
        "real madrid",
        "real madrid castilla",
        "atletico madrid",
        "madrid",
        "",
        "fc barcelona",
        "barcelona",
    ];
    let index = TokenSetIndex::new(names);
    for query in [
        "real madrid",
        "madrid",
        "barca",
        "",
        "barcelona fc",
        "castilla real",
    ] {
        for cutoff in [0.0, 60.0, 85.0, 100.0] {
            assert_eq!(
                index.extract(query, cutoff),
                full_scan(query, names, cutoff),
                "{query:?} @ {cutoff}"
            );
        }
    }
}

#[test]
fn split_lines_honours_every_line_boundary() {
    assert_eq!(
        split_lines("a\rb\r\nc\x0cd\u{2028}e\n"),
        ["a", "b", "c", "d", "e"]
    );
    assert_eq!(split_lines("a\nb\r\nc\rd"), ["a", "b", "c", "d"]);
    assert_eq!(split_lines("a\n\nb\u{2028}c"), ["a", "", "b", "c"]);
    assert_eq!(split_lines("\n\n"), ["", ""]);
    assert!(split_lines("").is_empty());
}
