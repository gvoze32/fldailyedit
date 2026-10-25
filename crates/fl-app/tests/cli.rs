//! The command line against the owner's local reference save (skipped when absent).

mod common;

use std::ffi::OsString;

use common::reference_save;
use fl_app::cli::run_cli;
use fl_app::scripted::ScriptedEngine;
use fl_app::wiring;
use fl_engine::api::{Environment, UpdateResult};

fn run(args: &[&str], env: &Environment) -> (i32, String) {
    let args: Vec<OsString> = std::iter::once("FLDailyEdit")
        .chain(args.iter().copied())
        .map(OsString::from)
        .collect();
    let engine = ScriptedEngine::new(Vec::new(), Ok(UpdateResult::default()));
    let mut out = Vec::new();
    let code = run_cli(args, &engine, env, &mut out);
    (code, String::from_utf8(out).unwrap())
}

#[test]
fn validate_passes_on_the_reference_save() {
    let Some(save) = reference_save() else {
        return;
    };
    let env = Environment {
        state_dir: std::env::temp_dir(),
    };
    let (code, text) = run(&["validate", "--edit-file", save.to_str().unwrap()], &env);
    assert_eq!(code, 0, "{text}");
    assert!(text.contains("PASS: save structure matches"), "{text}");
}

#[test]
fn inspect_summarizes_the_reference_save() {
    let Some(save) = reference_save() else {
        return;
    };
    let env = Environment {
        state_dir: std::env::temp_dir(),
    };
    let (code, text) = run(&["inspect", "--edit-file", save.to_str().unwrap()], &env);
    assert_eq!(code, 0, "{text}");
    for expected in [
        "Game version:",
        "Players:",
        "Integrity: PASS",
        "Division",
        "Total clubs:",
    ] {
        assert!(text.contains(expected), "{expected} missing in\n{text}");
    }
}

#[test]
fn log_reads_the_transfer_log_of_the_state_dir() {
    let dir = tempfile::tempdir().unwrap();
    let env = Environment {
        state_dir: dir.path().into(),
    };
    let engine = wiring::engine();
    let text = engine.recent_log(&env, 5).unwrap();
    assert_eq!(text, "No save changes logged yet.");
}
