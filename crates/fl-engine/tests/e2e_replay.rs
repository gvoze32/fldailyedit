//! Golden end-to-end: replay the recorded HTTP cassette through the whole
//! pipeline with the pinned clock and compare every artifact with the baseline
//! in `expected/`.
//!
//! The baseline is this engine's own reviewed output (`testdata/DEVIATIONS.md`,
//! "Baseline"). After an intended change, regenerate it with
//! `FL_E2E_REBASELINE=1 cargo test -p fl-engine --test e2e_replay -- --include-ignored`
//! and review the diff.

mod common;

use std::fs;
use std::io::Write;

use base64::Engine as _;
use common::{Scenario, Workdir, run};
use fl_engine::api::{CancelToken, Outcome, Stage};
use sha2::{Digest, Sha256};

fn sha256_hex(bytes: &[u8]) -> String {
    Sha256::digest(bytes)
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

/// A description of the first difference, or `None` when equal.
fn first_difference(label: &str, actual: &str, expected: &str) -> Option<String> {
    if actual == expected {
        return None;
    }
    let position = actual
        .lines()
        .zip(expected.lines())
        .position(|(a, b)| a != b)
        .unwrap_or_else(|| actual.lines().count().min(expected.lines().count()));
    Some(format!(
        "{label} differs at line {} ({} actual lines, {} expected):\n  actual:   {:?}\n  expected: {:?}",
        position + 1,
        actual.lines().count(),
        expected.lines().count(),
        actual.lines().nth(position),
        expected.lines().nth(position),
    ))
}

/// Gzip as the recorded artifacts are stored: level 9, no timestamp.
fn gzip(bytes: &[u8]) -> Vec<u8> {
    let mut encoder = flate2::GzBuilder::new().write(Vec::new(), flate2::Compression::best());
    encoder.write_all(bytes).unwrap();
    encoder.finish().unwrap()
}

/// The sparse byte patch that turns `base` into `new` (the format of
/// `expected/EDIT00000000.delta.json.gz`): runs of changed bytes, runs closer
/// than 8 bytes merged.
fn save_delta(base: &[u8], new: &[u8]) -> serde_json::Value {
    const MERGE_GAP: usize = 8;
    assert_eq!(base.len(), new.len(), "a save keeps its size");
    let encode = |from: usize, to: usize| {
        serde_json::json!([
            from,
            base64::engine::general_purpose::STANDARD.encode(&new[from..=to])
        ])
    };
    let mut runs = Vec::new();
    let mut open: Option<(usize, usize)> = None;
    for index in 0..base.len() {
        if base[index] != new[index] {
            open = Some(match open {
                Some((start, _)) => (start, index),
                None => (index, index),
            });
        } else if let Some((start, last)) = open
            && index - last > MERGE_GAP
        {
            runs.push(encode(start, last));
            open = None;
        }
    }
    if let Some((start, last)) = open {
        runs.push(encode(start, last));
    }
    serde_json::json!({
        "format": "fldailyedit-save-delta-v1",
        "base_sha256": sha256_hex(base),
        "size": new.len(),
        "sha256": sha256_hex(new),
        "runs": runs,
    })
}

/// Replace the scenario's expected artifacts with this run's output.
fn write_baseline(scenario: &Scenario, input: &[u8], save: &[u8], texts: &[(&str, String)]) {
    let expected = scenario.dir.join("expected");
    let mut hashes = serde_json::Map::new();
    hashes.insert("EDIT00000000".into(), sha256_hex(save).into());
    let write = |name: &str, bytes: &[u8]| fs::write(expected.join(name), bytes).unwrap();
    write(
        "EDIT00000000.delta.json.gz",
        &gzip(save_delta(input, save).to_string().as_bytes()),
    );
    if expected.join("EDIT00000000.gz").is_file() {
        write("EDIT00000000.gz", &gzip(save));
    }
    for (name, text) in texts {
        hashes.insert((*name).into(), sha256_hex(text.as_bytes()).into());
        let _ = fs::remove_file(expected.join(name));
        let _ = fs::remove_file(expected.join(format!("{name}.gz")));
        if text.len() > 256 * 1024 {
            write(&format!("{name}.gz"), &gzip(text.as_bytes()));
        } else {
            write(name, text.as_bytes());
        }
    }
    let mut meta = scenario.meta.clone();
    meta["expected_sha256"] = hashes.into();
    meta["expected_save"] = "expected/ is the engine's own reviewed output (testdata/DEVIATIONS.md, Baseline); EDIT00000000.delta.json.gz is a sparse patch over `input`".into();
    let mut text = serde_json::to_string_pretty(&meta).unwrap();
    text.push('\n');
    fs::write(scenario.dir.join("meta.json"), text).unwrap();
}

fn replay_matches_golden(scenario: Scenario) {
    let workdir = Workdir::new(&scenario);
    let request = workdir.request(&scenario);
    let input_before = fs::read(&workdir.input).unwrap();
    let mut events = Vec::new();
    let result = run(&workdir, &scenario, &request, &CancelToken::new(), |p| {
        events.push(p.clone())
    })
    .unwrap_or_else(|e| panic!("run failed: {e}"));

    assert_eq!(result.outcome, Outcome::Updated);
    assert_eq!(
        fs::read(&workdir.input).unwrap(),
        input_before,
        "input save must stay untouched"
    );

    let written = fs::read(&workdir.output).unwrap();
    assert_eq!(
        result.written_sha256.as_deref(),
        Some(sha256_hex(&written).as_str())
    );

    let paths = &result.report_paths;
    let report = |name: &str| {
        let path = paths
            .iter()
            .find(|p| p.file_name().unwrap() == name)
            .unwrap();
        workdir.normalize(&fs::read_to_string(path).unwrap())
    };
    let state =
        |name: &str| workdir.normalize(&fs::read_to_string(workdir.state.join(name)).unwrap());
    let texts: [(&str, String); 5] = [
        ("transfer_summary.md", report("transfer_summary.md")),
        ("transfer_summary.html", report("transfer_summary.html")),
        ("skipped_transfers.jsonl", report("skipped_transfers.jsonl")),
        ("transfer_log.jsonl", state("transfer_log.jsonl")),
        (
            "club_identity_cache.json",
            state("club_identity_cache.json"),
        ),
    ];
    if std::env::var_os("FL_E2E_REBASELINE").is_some() {
        write_baseline(&scenario, &input_before, &written, &texts);
        return;
    }

    let mut differences: Vec<String> = Vec::new();
    if sha256_hex(&written) != scenario.expected_save_sha256() {
        differences.push("output save differs from the baseline save".to_owned());
    }
    for (name, actual) in &texts {
        differences.extend(first_difference(
            name,
            actual,
            &scenario.expected_text(name),
        ));
    }
    if let (false, Some(dir)) = (differences.is_empty(), std::env::var_os("FL_E2E_DUMP")) {
        let dir = std::path::PathBuf::from(dir);
        fs::create_dir_all(&dir).unwrap();
        for (name, text) in &texts {
            fs::write(dir.join(name), text).unwrap();
        }
        fs::write(dir.join("EDIT00000000"), &written).unwrap();
    }
    assert!(
        differences.is_empty(),
        "baseline mismatches:\n{}",
        differences.join("\n")
    );

    assert_eq!(
        result.report_markdown,
        fs::read_to_string(&paths[0]).unwrap()
    );
    assert!(result.backup_path.as_ref().is_some_and(|p| p.is_file()));
    assert!(
        !workdir.output.with_file_name(".EDIT00000000.lock").exists(),
        "lock file removed"
    );
    assert!(
        events
            .iter()
            .any(|p| p.stage == Stage::Fetching && p.total > 0),
        "fetch progress with totals"
    );
    assert_eq!(events.last().unwrap().stage, Stage::Complete);
    // Stages only advance, except that a run which has more to settle goes
    // back from applying to matching for its next round.
    let stages: Vec<Stage> = events.iter().map(|p| p.stage).collect();
    assert!(
        stages
            .windows(2)
            .all(|w| w[0] <= w[1] || (w[0], w[1]) == (Stage::Applying, Stage::Matching)),
        "stages never go backwards: {stages:?}"
    );
}

#[test]
fn small_scenario_matches_the_golden() {
    if let Some(scenario) = Scenario::open(true, "small") {
        replay_matches_golden(scenario);
    }
}

/// Full-coverage run (3235 requests); slow, needs the gitignored recording.
#[test]
#[ignore = "slow: replays the full recorded run"]
fn full_scenario_matches_the_golden() {
    if let Some(scenario) = Scenario::open(false, "full") {
        replay_matches_golden(scenario);
    }
}

/// Dated events of the transfer log as `(player, from, to, date)` keys.
fn logged_events(workdir: &Workdir) -> Vec<(u64, u64, u64, String)> {
    let text = fs::read_to_string(workdir.state.join("transfer_log.jsonl")).unwrap();
    text.lines()
        .map(|line| serde_json::from_str::<serde_json::Value>(line).unwrap())
        .filter(|row| !row["transfer_date"].as_str().unwrap_or_default().is_empty())
        .map(|row| {
            (
                row["player_id"].as_u64().unwrap_or(0),
                row["from_team_id"].as_u64().unwrap_or(0),
                row["to_team_id"].as_u64().unwrap_or(0),
                row["transfer_date"].as_str().unwrap().to_owned(),
            )
        })
        .collect()
}

/// An update immediately followed by identical reruns (same cassette, same
/// pinned clock, state directory kept so the transfer log is present, each
/// rerun in place on the previous output): no rerun replays a dated event the
/// log already holds, and within `allowed_reruns` one finds nothing left to do
/// (`NoChanges`: no change counts, save and transfer log byte for byte as the
/// run before left them).
///
/// A run settles its own changes before it writes, so what a rerun can still
/// find is evidence the first run could not see: a later run scrapes a
/// narrower event window (it starts a week before the previous run), so
/// events that protected a signing from the live-squad diff are gone. That is
/// why the full scenario gets one extra rerun (`testdata/DEVIATIONS.md`,
/// "Idempotency").
fn rerun_settles(scenario: Scenario, allowed_reruns: usize) {
    let workdir = Workdir::new(&scenario);
    let first = run(
        &workdir,
        &scenario,
        &workdir.request(&scenario),
        &CancelToken::new(),
        |p| {
            if p.message.starts_with("Settled") {
                eprintln!("first run: {}", p.message);
            }
        },
    )
    .unwrap_or_else(|e| panic!("first run failed: {e}"));
    assert_eq!(first.outcome, Outcome::Updated);
    eprintln!("first run: {:?}", first.counts);

    let mut in_place = workdir.request(&scenario);
    in_place.edit_path = workdir.output.clone();
    in_place.output_path = None;
    let mut history = Vec::new();
    for rerun in 1..=allowed_reruns {
        let before = logged_events(&workdir);
        let (save, log) = (
            fs::read(&workdir.output).unwrap(),
            fs::read(workdir.state.join("transfer_log.jsonl")).unwrap(),
        );
        let result = run(&workdir, &scenario, &in_place, &CancelToken::new(), |_| {})
            .unwrap_or_else(|e| panic!("rerun {rerun} failed: {e}"));
        let after = logged_events(&workdir);
        let known: std::collections::HashSet<_> = before.iter().collect();
        let replayed: Vec<_> = after[before.len()..]
            .iter()
            .filter(|event| known.contains(event))
            .collect();
        eprintln!("rerun {rerun}: {:?} {:?}", result.outcome, result.counts);
        history.push((result.outcome, result.counts));
        if result.outcome == Outcome::NoChanges {
            let counts = result.counts;
            assert_eq!(
                (
                    counts.transfers,
                    counts.shirt_numbers,
                    counts.captains,
                    counts.formations,
                    counts.tactics
                ),
                (0, 0, 0, 0, 0),
                "{counts:?}"
            );
            assert_eq!(fs::read(&workdir.output).unwrap(), save, "save untouched");
            assert_eq!(
                fs::read(workdir.state.join("transfer_log.jsonl")).unwrap(),
                log,
                "nothing was logged"
            );
            return;
        }
        assert!(
            replayed.is_empty(),
            "rerun {rerun} replayed dated events the log already held: {replayed:?}"
        );
    }
    panic!("no rerun settled to NoChanges within {allowed_reruns}: {history:?}");
}

#[test]
fn small_scenario_rerun_changes_nothing() {
    if let Some(scenario) = Scenario::open(true, "small") {
        rerun_settles(scenario, 1);
    }
}

/// Full-coverage reruns (3235 requests per run); slow, needs the gitignored recording.
#[test]
#[ignore = "slow: replays the full recorded run three times"]
fn full_scenario_rerun_settles() {
    if let Some(scenario) = Scenario::open(false, "full") {
        rerun_settles(scenario, 2);
    }
}
