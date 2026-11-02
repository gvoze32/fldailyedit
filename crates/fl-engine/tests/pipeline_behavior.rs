//! Behavior of the pipeline around the golden run: cancellation at every
//! stage before the commit, dry runs that write nothing, concurrent writers.

mod common;

use std::fs;

use common::{Scenario, Workdir, run, tree};
use fl_engine::api::{
    CancelToken, ErrorCode, Outcome, Progress, Stage, UpdateParts, UpdateRequest,
};
use fl_engine::native::NativeFiles;

fn small() -> Option<(Scenario, Workdir)> {
    let scenario = Scenario::open(true, "small")?;
    let workdir = Workdir::new(&scenario);
    Some((scenario, workdir))
}

fn assert_nothing_durable_written(workdir: &Workdir) {
    assert!(!workdir.output.exists(), "output save must not exist");
    assert!(
        !workdir
            .output
            .parent()
            .unwrap()
            .join(".EDIT00000000.lock")
            .exists(),
        "lock file removed"
    );
    let state = tree(&workdir.state);
    for forbidden in ["transfer_log.jsonl", "reports", "backups"] {
        assert!(
            !state.iter().any(|entry| entry.starts_with(forbidden)),
            "{forbidden} written: {state:?}"
        );
    }
}

fn cancelled_at(stage: Stage) {
    let Some((scenario, workdir)) = small() else {
        return;
    };
    let token = CancelToken::new();
    let input_before = fs::read(&workdir.input).unwrap();
    let request = workdir.request(&scenario);
    let error = run(&workdir, &scenario, &request, &token, |p: &Progress| {
        if p.stage == stage {
            token.request();
        }
    })
    .expect_err("cancelled run must fail");
    assert_eq!(error.code, ErrorCode::Cancelled, "{stage:?}: {error}");
    assert_nothing_durable_written(&workdir);
    assert_eq!(fs::read(&workdir.input).unwrap(), input_before);
    assert!(
        !tree(&workdir.state).contains(&"club_identity_cache.json".to_owned())
            || stage > Stage::Matching,
        "learned identities must not be cached by a run cancelled at {stage:?}"
    );
}

#[test]
fn cancel_while_validating() {
    cancelled_at(Stage::Validating);
}

#[test]
fn cancel_while_fetching() {
    cancelled_at(Stage::Fetching);
}

#[test]
fn cancel_while_matching() {
    cancelled_at(Stage::Matching);
}

#[test]
fn cancel_while_applying() {
    cancelled_at(Stage::Applying);
}

#[test]
fn cancel_while_verifying() {
    cancelled_at(Stage::Verifying);
}

#[test]
fn cancel_before_commit_writes_nothing() {
    cancelled_at(Stage::Committing);
}

#[test]
fn dry_run_writes_nothing_anywhere() {
    let Some((scenario, workdir)) = small() else {
        return;
    };
    let mut request: UpdateRequest = workdir.request(&scenario);
    request.dry_run = true;
    let state_before = tree(&workdir.state);
    let root_before = tree(&workdir.root());
    let input_before = fs::read(&workdir.input).unwrap();

    let result = run(&workdir, &scenario, &request, &CancelToken::new(), |_| {}).unwrap();

    assert_eq!(result.outcome, Outcome::Preview);
    assert!(
        result.counts.transfers > 0 && result.counts.shirt_numbers > 0,
        "{:?}",
        result.counts
    );
    assert!(
        result.report_paths.is_empty()
            && result.backup_path.is_none()
            && result.written_sha256.is_none()
    );
    assert_eq!(
        tree(&workdir.state),
        state_before,
        "state directory must be unchanged"
    );
    assert_eq!(
        tree(&workdir.root()),
        root_before,
        "no output dir, lock or other file may appear"
    );
    assert_eq!(fs::read(&workdir.input).unwrap(), input_before);
}

#[test]
fn concurrent_output_writer_wins() {
    let Some((scenario, workdir)) = small() else {
        return;
    };
    let request = workdir.request(&scenario);
    let output = workdir.output.clone();
    let error = run(&workdir, &scenario, &request, &CancelToken::new(), |p| {
        if p.stage == Stage::Verifying {
            fs::create_dir_all(output.parent().unwrap()).unwrap();
            fs::write(&output, b"someone else").unwrap();
        }
    })
    .expect_err("a changed output must stop the run");
    assert_eq!(error.code, ErrorCode::InputChanged);
    assert_eq!(fs::read(&workdir.output).unwrap(), b"someone else");
}

#[test]
fn missing_input_is_reported_before_anything_else() {
    let Some((scenario, workdir)) = small() else {
        return;
    };
    let mut request = workdir.request(&scenario);
    request.edit_path = workdir.root().join("nope").join("EDIT00000000");
    let error = run(&workdir, &scenario, &request, &CancelToken::new(), |_| {}).unwrap_err();
    assert_eq!(error.code, ErrorCode::SaveNotFound);
    assert_eq!(error.stage, Some(Stage::Validating));
}

#[test]
fn garbage_input_is_an_invalid_save() {
    let Some((scenario, workdir)) = small() else {
        return;
    };
    fs::write(&workdir.input, vec![7u8; 1000]).unwrap();
    let request = workdir.request(&scenario);
    let error = run(&workdir, &scenario, &request, &CancelToken::new(), |_| {}).unwrap_err();
    assert_eq!(error.code, ErrorCode::InvalidSave);
    assert_nothing_durable_written(&workdir);
}

#[test]
fn second_run_on_the_same_output_is_locked_out() {
    let Some((scenario, workdir)) = small() else {
        return;
    };
    let request = workdir.request(&scenario);
    let _held = fl_engine::lock::SaveLock::acquire(&workdir.output).unwrap();
    let error = run(&workdir, &scenario, &request, &CancelToken::new(), |_| {}).unwrap_err();
    assert_eq!(error.code, ErrorCode::SaveLocked);
}

#[test]
fn in_place_rerun_backs_up_the_previous_save_and_extends_the_log() {
    let Some((scenario, workdir)) = small() else {
        return;
    };
    let request = workdir.request(&scenario);
    let first = run(&workdir, &scenario, &request, &CancelToken::new(), |_| {}).unwrap();
    assert_eq!(first.outcome, Outcome::Updated);
    let updated = fs::read(&workdir.output).unwrap();
    let log_lines = |workdir: &Workdir| {
        fs::read_to_string(workdir.state.join("transfer_log.jsonl"))
            .unwrap()
            .lines()
            .count()
    };
    let logged = log_lines(&workdir);

    // Second run: the updated save is both input and output (in place).
    let mut again = workdir.request(&scenario);
    again.edit_path = workdir.output.clone();
    again.output_path = None;
    let second = run(&workdir, &scenario, &again, &CancelToken::new(), |_| {}).unwrap();

    match second.outcome {
        Outcome::NoChanges => {
            assert_eq!(
                fs::read(&workdir.output).unwrap(),
                updated,
                "save untouched"
            );
            assert!(second.backup_path.is_none());
            assert_eq!(log_lines(&workdir), logged);
        }
        Outcome::Updated => {
            let backup = second.backup_path.expect("an in-place update is backed up");
            assert_eq!(
                fs::read(backup).unwrap(),
                updated,
                "backup holds the previous save"
            );
            assert_ne!(fs::read(&workdir.output).unwrap(), updated);
            assert!(log_lines(&workdir) > logged);
        }
        Outcome::Preview => unreachable!("not a dry run"),
    }
    // Whatever happened, the result is a valid, loadable save.
    let raw = fs::read(&workdir.output).unwrap();
    let blocks = fl_save::crypto::decrypt(&raw).unwrap();
    let edit = fl_save::edit_file::EditFile::from_bytes(blocks.data).unwrap();
    assert!(edit.validate_integrity().is_valid());
    assert!(!workdir.output.with_file_name(".EDIT00000000.lock").exists());
}

/// A run that checks no transfers must leave everything the next automatic
/// transfer window is derived from exactly as it was: the transfer log and
/// the skipped-transfer file of the last full run.
#[test]
fn a_lineups_only_run_leaves_the_transfer_window_inputs_alone() {
    let Some((scenario, workdir)) = small() else {
        return;
    };
    let first = run(
        &workdir,
        &scenario,
        &workdir.request(&scenario),
        &CancelToken::new(),
        |_| {},
    )
    .unwrap();
    assert_eq!(first.outcome, Outcome::Updated);

    let reports = fs::read_dir(workdir.state.join("reports"))
        .unwrap()
        .next()
        .expect("the full run wrote its reports")
        .unwrap()
        .path();
    let skipped_file = reports.join("skipped_transfers.jsonl");
    // A retryable row of the last full run, as it would seed the next window.
    let sentinel = r#"{"player_name": "Sentinel", "from_team": "A", "to_team": "B", "date": "2026-10-01", "source": "s", "reason": "r", "detail": "", "relevant": true, "fotmob_player_id": null, "candidates": [], "retryable": true, "save_scope": "x"}"#;
    fs::write(&skipped_file, format!("{sentinel}\n")).unwrap();
    let log_file = workdir.state.join("transfer_log.jsonl");
    let (log_before, skipped_before) = (
        fs::read(&log_file).unwrap(),
        fs::read(&skipped_file).unwrap(),
    );

    let mut lineups = workdir.request(&scenario);
    lineups.edit_path = workdir.output.clone();
    lineups.output_path = None;
    lineups.parts = UpdateParts {
        transfers: false,
        shirt_numbers: false,
        captains: false,
        leagues: false,
        ..UpdateParts::FULL
    };
    let mut fetching = Vec::new();
    let second = run(&workdir, &scenario, &lineups, &CancelToken::new(), |p| {
        if p.stage == Stage::Fetching {
            fetching.push(p.message.clone());
        }
    })
    .unwrap();

    assert_eq!(fetching[0], "Fetching formations and tactics");
    assert!(
        fetching.iter().all(|line| !line.contains("transfer feed")
            && !line.contains("Wikipedia")
            && !line.contains("Transfermarkt")),
        "{fetching:?}"
    );
    let counts = second.counts;
    assert_eq!(
        (counts.transfers, counts.shirt_numbers, counts.captains),
        (0, 0, 0)
    );
    assert!(second.skipped.is_empty());
    assert_eq!(fs::read(&log_file).unwrap(), log_before, "transfer log");
    assert_eq!(
        fs::read(&skipped_file).unwrap(),
        skipped_before,
        "skipped-transfer file"
    );
    let summary = fs::read_to_string(reports.join("transfer_summary.md")).unwrap();
    assert!(summary.contains("Transfers were not checked"), "{summary}");

    let raw = fs::read(&workdir.output).unwrap();
    let blocks = fl_save::crypto::decrypt(&raw).unwrap();
    let edit = fl_save::edit_file::EditFile::from_bytes(blocks.data).unwrap();
    assert!(edit.validate_integrity().is_valid());
}

/// Run the small scenario with the native database files `native` instead of
/// the recorded ones and return the failure; nothing may be written.
fn native_failure(native: impl FnOnce(&Workdir) -> NativeFiles) -> fl_engine::api::UpdateError {
    let Some((scenario, workdir)) = small() else {
        panic!("skip");
    };
    let mut services = workdir.services(&scenario);
    services.native_files = native(&workdir);
    let input_before = fs::read(&workdir.input).unwrap();
    let error = fl_engine::run_update_with(
        &workdir.request(&scenario),
        &workdir.environment(),
        &services,
        &mut |_| {},
        &CancelToken::new(),
    )
    .expect_err("a run without matching game data must fail");
    assert_nothing_durable_written(&workdir);
    assert_eq!(fs::read(&workdir.input).unwrap(), input_before);
    error
}

/// A machine with the game installed finds it through the game-root
/// variables and install folders, which these tests must not see.
fn game_is_discoverable() -> bool {
    !fl_save::cpk::discover::discover_game_cpks(None).is_empty()
}

#[test]
fn a_run_without_game_data_fails_early_and_says_where_to_find_it() {
    if small().is_none() || game_is_discoverable() {
        return;
    }
    let error = native_failure(|_| NativeFiles::default());
    assert_eq!(error.code, ErrorCode::NativeDatabaseMissing, "{error}");
    assert_eq!(error.stage, Some(Stage::Validating));
    assert!(
        error
            .message
            .contains("game folder that contains the download folder"),
        "{error}"
    );
}

#[test]
fn a_football_life_run_needs_the_team_database_too() {
    if small().is_none() || game_is_discoverable() {
        return;
    }
    let error = native_failure(|workdir| NativeFiles {
        player_bin: Some(workdir.player_bin.clone()),
        ..NativeFiles::default()
    });
    assert_eq!(error.code, ErrorCode::NativeDatabaseMissing, "{error}");
    assert!(error.message.contains("Team.bin"), "{error}");
}

#[test]
fn a_player_database_of_another_game_is_rejected() {
    if small().is_none() {
        return;
    }
    // A valid but empty Player.bin knows none of the save's rostered players.
    let error = native_failure(|workdir| {
        let empty = workdir.root().join("inputs").join("Empty-Player.bin");
        fs::write(&empty, vec![0u8; 312 * 4]).unwrap();
        NativeFiles {
            player_bin: Some(empty),
            team_bin: Some(workdir.team_bin.clone()),
            ..NativeFiles::default()
        }
    });
    assert_eq!(error.code, ErrorCode::NativeDatabaseMissing, "{error}");
    assert!(error.message.contains("do not match this save"), "{error}");
}
