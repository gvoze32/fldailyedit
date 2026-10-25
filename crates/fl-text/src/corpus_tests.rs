//! Parity against the Python oracle corpus (local `testdata/text_fuzzy/corpus.json.gz`, not
//! in the repository; tests skip when absent), recorded from the former Python implementation,
//! now removed. Every float is compared with `==`.

use std::collections::HashMap;
use std::sync::LazyLock;

use serde::Deserialize;

use crate::fuzz;
use crate::test_support::{read_gz, testdata_path};
use crate::unicode_str;
use crate::unit_tests::full_scan;
use crate::{
    TokenSetIndex, alnum_key, casefold, categories_compatible, category_markers, clean_club_name,
    club_key_narrow, fold_text, has_non_senior_marker, is_non_club, is_non_club_key, name_tokens,
    normalize_alnum, normalize_dotless, strip,
};

#[derive(Deserialize)]
struct Norm {
    fold: Vec<String>,
    casefold: Vec<String>,
    dotless: Vec<String>,
    alnum: Vec<String>,
    alnum_key: Vec<String>,
    clean_club: Vec<String>,
    club_key_narrow: Vec<String>,
    category: Vec<Vec<String>>,
    non_senior: Vec<bool>,
    name_tokens: Vec<Vec<String>>,
    non_club: Vec<bool>,
    non_club_dotless: Vec<bool>,
    non_club_alnum: Vec<bool>,
    strip: Vec<String>,
}

#[derive(Deserialize)]
struct CutoffCase {
    p: (usize, usize),
    c: f64,
    ratio: String,
    token_sort_ratio: String,
    token_set_ratio: String,
    wratio: String,
    partial_ratio: String,
}

#[derive(Deserialize)]
struct Codepoints {
    isalnum: Vec<(u32, u32)>,
    isspace: Vec<(u32, u32)>,
    isdecimal: Vec<(u32, u32)>,
    assigned: Vec<(u32, u32)>,
    fold_changed: HashMap<String, String>,
    casefold_changed: HashMap<String, String>,
}

#[derive(Deserialize)]
struct IndexCase {
    q: String,
    c: f64,
    hits: Vec<(usize, String)>,
}

#[derive(Deserialize)]
struct IndexSuite {
    label: String,
    names: Vec<String>,
    cases: Vec<IndexCase>,
}

#[derive(Deserialize)]
struct KeyCase {
    q: String,
    keys: Vec<String>,
}

#[derive(Deserialize)]
struct KeyIndex {
    raw_clubs: Vec<String>,
    cases: Vec<KeyCase>,
}

#[derive(Deserialize)]
struct Corpus {
    strings: Vec<String>,
    pairs: Vec<(usize, usize)>,
    scores: HashMap<String, Vec<String>>,
    cutoff_cases: Vec<CutoffCase>,
    norm: Norm,
    category_pairs: Vec<(usize, usize, bool)>,
    codepoints: Codepoints,
    index_cases: Vec<IndexSuite>,
    key_index: KeyIndex,
}

const CORPUS_PATH: &str = "text_fuzzy/corpus.json.gz";

static CORPUS: LazyLock<Corpus> = LazyLock::new(|| {
    let json = read_gz(CORPUS_PATH).expect("corpus fixture present");
    serde_json::from_slice(&json).expect("corpus json")
});

/// The recorded oracle corpus, or `None` (with a skip note) when `testdata/` lacks it.
fn corpus() -> Option<&'static Corpus> {
    testdata_path(CORPUS_PATH)?;
    Some(&CORPUS)
}

fn f(text: &str) -> f64 {
    text.parse().expect("float repr")
}

fn check_all(name: &str, actual: impl Iterator<Item = f64>, expected: &[String], corpus: &Corpus) {
    let mut failures = Vec::new();
    let mut count = 0;
    for (index, (got, want)) in actual.zip(expected).enumerate() {
        count += 1;
        let want = f(want);
        if got != want {
            let (a, b) = corpus.pairs[index];
            failures.push(format!(
                "{name} {:?} vs {:?}: got {got:?} want {want:?}",
                corpus.strings[a], corpus.strings[b]
            ));
        }
    }
    assert_eq!(count, corpus.pairs.len());
    assert!(
        failures.is_empty(),
        "{} of {count} mismatches, first: {:#?}",
        failures.len(),
        &failures[..failures.len().min(10)]
    );
}

fn pair_scores(corpus: &Corpus, score: impl Fn(&str, &str) -> f64) -> impl Iterator<Item = f64> {
    corpus
        .pairs
        .iter()
        .map(move |&(a, b)| score(&corpus.strings[a], &corpus.strings[b]))
}

#[test]
fn pair_scorers_match_corpus() {
    let Some(corpus) = corpus() else {
        return;
    };
    assert!(corpus.pairs.len() >= 20_000);
    let scores = &corpus.scores;
    check_all(
        "ratio",
        pair_scores(corpus, fuzz::ratio),
        &scores["ratio"],
        corpus,
    );
    check_all(
        "token_sort_ratio",
        pair_scores(corpus, fuzz::token_sort_ratio),
        &scores["token_sort_ratio"],
        corpus,
    );
    check_all(
        "token_set_ratio",
        pair_scores(corpus, fuzz::token_set_ratio),
        &scores["token_set_ratio"],
        corpus,
    );
    check_all(
        "wratio",
        pair_scores(corpus, fuzz::wratio),
        &scores["wratio"],
        corpus,
    );
}

#[test]
fn internal_scorers_match_corpus() {
    let Some(corpus) = corpus() else {
        return;
    };
    let scores = &corpus.scores;
    check_all(
        "partial_ratio",
        pair_scores(corpus, |a, b| fuzz::partial_ratio(a, b, 0.0)),
        &scores["partial_ratio"],
        corpus,
    );
    check_all(
        "token_ratio",
        pair_scores(corpus, |a, b| fuzz::token_ratio(a, b, 0.0)),
        &scores["token_ratio"],
        corpus,
    );
    check_all(
        "partial_token_ratio",
        pair_scores(corpus, |a, b| fuzz::partial_token_ratio(a, b, 0.0)),
        &scores["partial_token_ratio"],
        corpus,
    );
}

#[test]
fn cutoff_scorers_match_corpus() {
    let Some(corpus) = corpus() else {
        return;
    };
    let mut failures = Vec::new();
    for case in &corpus.cutoff_cases {
        let (a, b) = (&corpus.strings[case.p.0], &corpus.strings[case.p.1]);
        let checks = [
            ("ratio", fuzz::ratio_with_cutoff(a, b, case.c), &case.ratio),
            (
                "token_sort_ratio",
                fuzz::token_sort_ratio_with_cutoff(a, b, case.c),
                &case.token_sort_ratio,
            ),
            (
                "token_set_ratio",
                fuzz::token_set_ratio_with_cutoff(a, b, case.c),
                &case.token_set_ratio,
            ),
            (
                "wratio",
                fuzz::wratio_with_cutoff(a, b, case.c),
                &case.wratio,
            ),
            (
                "partial_ratio",
                fuzz::partial_ratio(a, b, case.c),
                &case.partial_ratio,
            ),
        ];
        for (name, got, want) in checks {
            if got != f(want) {
                failures.push(format!(
                    "{name} c={} {a:?} vs {b:?}: got {got:?} want {want}",
                    case.c
                ));
            }
        }
    }
    assert!(
        failures.is_empty(),
        "{} mismatches, first: {:#?}",
        failures.len(),
        &failures[..failures.len().min(10)]
    );
}

fn norm_check<T: PartialEq + std::fmt::Debug>(
    name: &str,
    corpus: &Corpus,
    expected: &[T],
    actual: impl Fn(&str) -> T,
) {
    assert_eq!(expected.len(), corpus.strings.len());
    let mut failures = Vec::new();
    for (value, want) in corpus.strings.iter().zip(expected) {
        let got = actual(value);
        if &got != want {
            failures.push(format!("{name}({value:?}): got {got:?} want {want:?}"));
        }
    }
    assert!(
        failures.is_empty(),
        "{} mismatches, first: {:#?}",
        failures.len(),
        &failures[..failures.len().min(10)]
    );
}

#[test]
fn normalizers_match_corpus() {
    let Some(corpus) = corpus() else {
        return;
    };
    let norm = &corpus.norm;
    norm_check("fold_text", corpus, &norm.fold, fold_text);
    norm_check("casefold", corpus, &norm.casefold, casefold);
    norm_check(
        "normalize_dotless",
        corpus,
        &norm.dotless,
        normalize_dotless,
    );
    norm_check("normalize_alnum", corpus, &norm.alnum, normalize_alnum);
    norm_check("alnum_key", corpus, &norm.alnum_key, alnum_key);
    norm_check("clean_club_name", corpus, &norm.clean_club, clean_club_name);
    norm_check(
        "club_key_narrow",
        corpus,
        &norm.club_key_narrow,
        club_key_narrow,
    );
    norm_check("category_markers", corpus, &norm.category, |v| {
        category_markers(v).into_iter().collect::<Vec<_>>()
    });
    norm_check(
        "has_non_senior_marker",
        corpus,
        &norm.non_senior,
        has_non_senior_marker,
    );
    norm_check("name_tokens", corpus, &norm.name_tokens, |v| {
        name_tokens(v)
            .into_iter()
            .map(str::to_owned)
            .collect::<Vec<_>>()
    });
    norm_check("is_non_club", corpus, &norm.non_club, is_non_club);
    norm_check(
        "is_non_club(dotless)",
        corpus,
        &norm.non_club_dotless,
        |v| is_non_club_key(&normalize_dotless(v)),
    );
    norm_check("is_non_club(alnum)", corpus, &norm.non_club_alnum, |v| {
        is_non_club_key(&normalize_alnum(v))
    });
    norm_check("strip", corpus, &norm.strip, |v| strip(v).to_owned());
}

#[test]
fn category_compatibility_matches_corpus() {
    let Some(corpus) = corpus() else {
        return;
    };
    for &(a, b, want) in &corpus.category_pairs {
        let (a, b) = (&corpus.strings[a], &corpus.strings[b]);
        assert_eq!(categories_compatible(a, b), want, "{a:?} vs {b:?}");
    }
}

fn in_ranges(ranges: &[(u32, u32)], cp: u32) -> bool {
    ranges.iter().any(|&(lo, hi)| (lo..=hi).contains(&cp))
}

#[test]
fn every_code_point_matches_corpus() {
    let Some(corpus) = corpus() else {
        return;
    };
    let cps = &corpus.codepoints;
    let mut failures = Vec::new();
    for cp in (0..=0x10FFFFu32).filter_map(char::from_u32) {
        let n = cp as u32;
        // Unicode 16 is the reference; newer std/normalization data may
        // give code points it left unassigned a meaning.
        if !in_ranges(&cps.assigned, n) {
            continue;
        }
        let key = n.to_string();
        let mut check = |name: &str, got: String, want: String| {
            if got != want {
                failures.push(format!("{name}(U+{n:04X}): got {got:?} want {want:?}"));
            }
        };
        check(
            "isalnum",
            unicode_str::is_alnum(cp).to_string(),
            in_ranges(&cps.isalnum, n).to_string(),
        );
        check(
            "isspace",
            unicode_str::is_space(cp).to_string(),
            in_ranges(&cps.isspace, n).to_string(),
        );
        check(
            "isdecimal",
            unicode_str::is_decimal(cp).to_string(),
            in_ranges(&cps.isdecimal, n).to_string(),
        );
        let text = cp.to_string();
        check(
            "fold_text",
            fold_text(&text),
            cps.fold_changed.get(&key).cloned().unwrap_or(text.clone()),
        );
        check(
            "casefold",
            casefold(&text),
            cps.casefold_changed.get(&key).cloned().unwrap_or(text),
        );
    }
    assert!(
        failures.is_empty(),
        "{} mismatches, first: {:#?}",
        failures.len(),
        &failures[..failures.len().min(20)]
    );
}

#[test]
fn token_set_index_matches_corpus_and_full_scan() {
    let Some(corpus) = corpus() else {
        return;
    };
    for suite in &corpus.index_cases {
        let index = TokenSetIndex::new(suite.names.iter().map(String::as_str));
        for case in &suite.cases {
            let got = index.extract(&case.q, case.c);
            let want: Vec<(usize, f64)> = case.hits.iter().map(|(i, s)| (*i, f(s))).collect();
            assert_eq!(
                got, want,
                "{} query {:?} cutoff {}",
                suite.label, case.q, case.c
            );
        }
        // Independent brute force on a sample: the index must equal a full scan.
        for case in suite.cases.iter().step_by(37) {
            let full = full_scan(&case.q, suite.names.iter().map(String::as_str), case.c);
            assert_eq!(
                index.extract(&case.q, case.c),
                full,
                "full scan {:?}",
                case.q
            );
        }
    }
}

#[test]
fn fuzzy_key_index_semantics_match_corpus() {
    // Key index = normalize_dotless + dedup + token_set >= 92.
    let Some(corpus) = corpus() else {
        return;
    };
    let key_index = &corpus.key_index;
    let mut keys: Vec<String> = Vec::new();
    for raw in &key_index.raw_clubs {
        let key = normalize_dotless(raw);
        if !key.is_empty() && !keys.contains(&key) {
            keys.push(key);
        }
    }
    let index = TokenSetIndex::new(keys.iter().map(String::as_str));
    for case in &key_index.cases {
        let query = normalize_dotless(&case.q);
        let got: Vec<&str> = if query.is_empty() {
            Vec::new()
        } else {
            index
                .extract(&query, 92.0)
                .into_iter()
                .map(|(i, _)| keys[i].as_str())
                .collect()
        };
        assert_eq!(got, case.keys, "query {:?}", case.q);
    }
}

/// `[a, b, cutoff, ratio, token_sort, token_set, wratio, partial, token, partial_token]`.
type StressRow = (
    String,
    String,
    f64,
    String,
    String,
    String,
    String,
    String,
    String,
    String,
);

/// Optional 300k-row random differential corpus, local only (gitignored); it can no longer be
/// regenerated. Skipped when absent.
#[test]
fn stress_corpus_matches_reference() {
    let Some(json) = read_gz("local/text_fuzzy_stress.json.gz") else {
        return;
    };
    let rows: Vec<StressRow> = serde_json::from_slice(&json).expect("stress json");
    let mut failures = Vec::new();
    for (a, b, cutoff, ratio, sort, set, w, partial, token, partial_token) in &rows {
        let checks = [
            ("ratio", fuzz::ratio_with_cutoff(a, b, *cutoff), ratio),
            (
                "token_sort_ratio",
                fuzz::token_sort_ratio_with_cutoff(a, b, *cutoff),
                sort,
            ),
            (
                "token_set_ratio",
                fuzz::token_set_ratio_with_cutoff(a, b, *cutoff),
                set,
            ),
            ("wratio", fuzz::wratio_with_cutoff(a, b, *cutoff), w),
            ("partial_ratio", fuzz::partial_ratio(a, b, *cutoff), partial),
            ("token_ratio", fuzz::token_ratio(a, b, *cutoff), token),
            (
                "partial_token_ratio",
                fuzz::partial_token_ratio(a, b, *cutoff),
                partial_token,
            ),
        ];
        for (name, got, want) in checks {
            if got != f(want) {
                failures.push(format!(
                    "{name} c={cutoff} {a:?} vs {b:?}: got {got:?} want {want}"
                ));
            }
        }
    }
    assert!(
        failures.is_empty(),
        "{} of {} rows mismatch, first: {:#?}",
        failures.len(),
        rows.len(),
        &failures[..failures.len().min(15)]
    );
}

/// Optional random normalizer corpus, local only; it can no longer be regenerated. Skipped when absent.
#[test]
fn norm_stress_corpus_matches_reference() {
    type Row = (
        String,
        String,
        String,
        String,
        String,
        String,
        String,
        Vec<String>,
        bool,
        Vec<String>,
        String,
        String,
    );
    let Some(json) = read_gz("local/text_fuzzy_norm_stress.json.gz") else {
        return;
    };
    let rows: Vec<Row> = serde_json::from_slice(&json).expect("norm stress json");
    let mut failures = Vec::new();
    for (text, fold, dotless, alnum, key, clean, narrow, markers, senior, tokens, cf, stripped) in
        &rows
    {
        let got_markers: Vec<String> = category_markers(text).into_iter().collect();
        let got_tokens: Vec<&str> = name_tokens(text);
        let checks = [
            ("fold", fold_text(text) == *fold),
            ("dotless", normalize_dotless(text) == *dotless),
            ("alnum", normalize_alnum(text) == *alnum),
            ("alnum_key", alnum_key(text) == *key),
            ("clean_club_name", clean_club_name(text) == *clean),
            ("club_key_narrow", club_key_narrow(text) == *narrow),
            ("category_markers", got_markers == *markers),
            ("non_senior", has_non_senior_marker(text) == *senior),
            (
                "name_tokens",
                got_tokens == tokens.iter().map(String::as_str).collect::<Vec<_>>(),
            ),
            ("casefold", casefold(text) == *cf),
            ("strip", strip(text) == stripped),
        ];
        for (name, ok) in checks {
            if !ok {
                failures.push(format!("{name}({text:?})"));
            }
        }
    }
    assert!(
        failures.is_empty(),
        "{} mismatches, first: {:#?}",
        failures.len(),
        &failures[..failures.len().min(15)]
    );
}
