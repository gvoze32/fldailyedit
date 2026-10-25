//! End-to-end GUI flows against a scripted engine (no window, no network).

mod common;

use std::time::Duration;

use common::*;
use fl_app::gui::result::Kind;
use fl_app::gui::setup::{GameStatus, SaveStatus};
use fl_app::gui::{Action, Screen};
use fl_app::scripted::{Gate, ScriptedEngine, Step};
use fl_engine::api::{ChangeCounts, ErrorCode, Outcome, Stage, UpdateError, UpdateResult};
use fl_engine::locations::SaveKind;

fn updated() -> UpdateResult {
    UpdateResult {
        target_path: "C:/Users/Me/save/EDIT00000000".into(),
        outcome: Outcome::Updated,
        backup_path: Some("C:/Users/Me/AppData/Local/FLDailyEdit/backups/EDIT00000000.bak".into()),
        counts: ChangeCounts {
            transfers: 4,
            ..ChangeCounts::default()
        },
        report_paths: vec!["C:/reports/transfer_summary.md".into()],
        ..UpdateResult::default()
    }
}

fn step(stage: Stage, message: &str, current: u32, total: u32) -> Step {
    ScriptedEngine::step(stage, message, current, total)
}

#[test]
fn reference_save_is_recognized_and_remembered() {
    let mut fx = fixture(ScriptedEngine::new(Vec::new(), Ok(UpdateResult::default())));
    choose_reference_save(&mut fx.app);
    let SaveStatus::Ready { info, .. } = &fx.app.setup().save else {
        panic!("not ready")
    };
    assert_eq!(
        info.kind,
        SaveKind::FootballLife26,
        "FL26 saves need no game folder"
    );
    assert!(fx.app.setup().request(false).is_some());

    // The choice is persisted for the next launch.
    let settings = std::fs::read_to_string(fx.state_dir.path().join("settings.json")).unwrap();
    assert!(settings.contains("EDIT00000000"), "{settings}");
}

#[test]
fn garbage_and_missing_saves_block_the_buttons_with_a_reason() {
    let mut fx = fixture(ScriptedEngine::new(Vec::new(), Ok(UpdateResult::default())));
    let garbage = fx.state_dir.path().join("EDIT00000000");
    std::fs::write(&garbage, b"not a save").unwrap();
    fx.app.dispatch(Action::SaveChosen(garbage));
    wait_until(&mut fx.app, "check", |app| {
        matches!(app.setup().save, SaveStatus::Invalid(_))
    });
    assert!(!fx.app.setup().ready());
    fx.app.dispatch(Action::Start { dry_run: false });
    assert!(
        matches!(fx.app.screen(), Screen::Setup),
        "a blocked start must do nothing"
    );
}

#[test]
fn browse_buttons_use_the_platform_pickers() {
    let mut fx = fixture(ScriptedEngine::new(Vec::new(), Ok(UpdateResult::default())));
    *fx.platform.save.lock().unwrap() = Some(reference_save());
    *fx.platform.folder.lock().unwrap() = Some(fx.state_dir.path().into());
    fx.app.dispatch(Action::BrowseSave);
    fx.app.dispatch(Action::BrowseGame);
    assert_eq!(
        fx.app.setup().save_path.as_deref(),
        Some(reference_save().as_path())
    );
    // An empty folder is reported, not accepted.
    wait_until(&mut fx.app, "game check", |app| {
        app.setup().game == GameStatus::Unusable
    });
}

#[test]
fn game_folder_inside_the_save_guard_is_rejected() {
    let mut fx = fixture(ScriptedEngine::new(Vec::new(), Ok(UpdateResult::default())));
    let game = fx.state_dir.path().join("PES 2021");
    std::fs::create_dir_all(game.join("download")).unwrap();
    std::fs::write(game.join("download/data_extra.cpk"), b"x").unwrap();
    std::fs::copy(reference_save(), game.join("EDIT00000000")).unwrap();
    fx.app.dispatch(Action::GameChosen(game.clone()));
    fx.app
        .dispatch(Action::SaveChosen(game.join("EDIT00000000")));
    wait_until(&mut fx.app, "check", |app| {
        matches!(app.setup().save, SaveStatus::Invalid(_))
    });
    let SaveStatus::Invalid(reason) = &fx.app.setup().save else {
        unreachable!()
    };
    assert!(reason.contains("inside the game installation"), "{reason}");
}

#[test]
fn preview_ends_on_a_result_screen_with_an_update_now_path() {
    let preview = UpdateResult {
        outcome: Outcome::Preview,
        ..updated()
    };
    let engine = ScriptedEngine::new(vec![step(Stage::Fetching, "Fetching", 1, 2)], Ok(preview));
    let mut fx = fixture(engine);
    choose_reference_save(&mut fx.app);
    fx.app.dispatch(Action::Start { dry_run: true });
    assert!(matches!(fx.app.screen(), Screen::Running));
    wait_until(&mut fx.app, "result", on_result_screen);
    let view = fx.app.result().unwrap();
    assert_eq!(view.kind, Kind::Preview);
    assert!(view.request.dry_run);

    // "Update now" re-runs the same request for real.
    fx.app.dispatch(Action::UpdateAfterPreview);
    assert!(matches!(fx.app.screen(), Screen::Running));
    wait_until(&mut fx.app, "second result", on_result_screen);
    assert!(!fx.app.result().unwrap().request.dry_run);
}

#[test]
fn cancel_before_commit_stops_the_run() {
    let gate = Gate::new();
    let engine = ScriptedEngine::new(
        vec![
            step(Stage::Fetching, "Fetching Chelsea", 1, 20),
            Step::Pause(Stage::Fetching, gate),
            Step::BeginCommit,
            step(Stage::Committing, "Writing", 0, 0),
        ],
        Ok(updated()),
    );
    let mut fx = fixture(engine);
    choose_reference_save(&mut fx.app);
    fx.app.dispatch(Action::Start { dry_run: false });
    wait_until(&mut fx.app, "fetching", |app| app.running());
    std::thread::sleep(Duration::from_millis(50));
    fx.app.poll();
    assert!(!fx.app.committing());

    fx.app.dispatch(Action::Cancel);
    wait_until(&mut fx.app, "result", on_result_screen);
    let view = fx.app.result().unwrap();
    assert_eq!(view.kind, Kind::Cancelled);
    assert_eq!(view.error().unwrap().code, ErrorCode::Cancelled);
    assert!(view.error_copy().unwrap().save_untouched);
}

#[test]
fn cancel_is_ignored_once_the_commit_has_started() {
    let gate = Gate::new();
    let engine = ScriptedEngine::new(
        vec![
            step(Stage::Fetching, "Fetching", 1, 1),
            Step::BeginCommit,
            step(Stage::Committing, "Writing the save", 0, 0),
            Step::Pause(Stage::Committing, gate.clone()),
            Step::Checkpoint(Stage::Committing),
            step(Stage::Complete, "Done", 0, 0),
        ],
        Ok(updated()),
    );
    let mut fx = fixture(engine);
    choose_reference_save(&mut fx.app);
    fx.app.dispatch(Action::Start { dry_run: false });
    wait_until(&mut fx.app, "commit", |app| app.committing());

    fx.app.dispatch(Action::Cancel);
    std::thread::sleep(Duration::from_millis(100));
    fx.app.poll();
    assert!(fx.app.running(), "the commit must not be interrupted");
    assert!(fx.app.committing());

    gate.open();
    wait_until(&mut fx.app, "result", on_result_screen);
    assert_eq!(fx.app.result().unwrap().kind, Kind::Updated);
}

#[test]
fn missing_database_failure_leads_back_to_the_game_folder_picker() {
    let engine = ScriptedEngine::new(
        vec![step(Stage::Validating, "Reading save", 0, 0)],
        Err(UpdateError {
            code: ErrorCode::NativeDatabaseMissing,
            stage: Some(Stage::Validating),
            message: "Player.bin not found".into(),
        }),
    );
    let mut fx = fixture(engine);
    choose_reference_save(&mut fx.app);
    fx.app.dispatch(Action::Start { dry_run: false });
    wait_until(&mut fx.app, "result", on_result_screen);
    let view = fx.app.result().unwrap();
    assert_eq!(view.kind, Kind::Failed);
    assert!(view.error_copy().unwrap().needs_game_folder);
    assert!(
        fx.app
            .diagnostics_text()
            .unwrap()
            .contains("NativeDatabaseMissing")
    );

    *fx.platform.folder.lock().unwrap() = Some(fx.state_dir.path().into());
    fx.app.dispatch(Action::ChooseGameFolderAfterFailure);
    assert!(matches!(fx.app.screen(), Screen::Setup));
    assert_eq!(
        fx.app.setup().game_path.as_deref(),
        Some(fx.state_dir.path())
    );
}

#[test]
fn engine_panic_becomes_an_error_result_not_a_hang() {
    struct Panics;
    impl fl_app::engine::Engine for Panics {
        fn run(
            &self,
            _: &fl_engine::api::UpdateRequest,
            _: &fl_engine::api::Environment,
            _: &mut dyn FnMut(fl_engine::api::Progress),
            _: &fl_engine::api::CancelToken,
        ) -> Result<UpdateResult, UpdateError> {
            panic!("boom");
        }
        fn recent_log(&self, _: &fl_engine::api::Environment, _: usize) -> anyhow::Result<String> {
            Ok(String::new())
        }
    }
    let state_dir = tempfile::tempdir().unwrap();
    let mut app = fl_app::gui::App::new(fl_app::gui::Services {
        engine: std::sync::Arc::new(Panics),
        env: fl_engine::api::Environment {
            state_dir: state_dir.path().into(),
        },
        platform: std::sync::Arc::new(FakePlatform::default()),
        update_backend: std::sync::Arc::new(NoUpdates),
        silent_update_check: false,
        detect: std::sync::Arc::new(|| (Vec::new(), None)),
    });
    choose_reference_save(&mut app);
    app.dispatch(Action::Start { dry_run: false });
    wait_until(&mut app, "result", on_result_screen);
    let view = app.result().unwrap();
    assert_eq!(view.error().unwrap().code, ErrorCode::Internal);
    assert!(view.error().unwrap().message.contains("boom"));
}

#[test]
fn result_buttons_open_the_right_places() {
    let mut fx = fixture(ScriptedEngine::new(Vec::new(), Ok(updated())));
    choose_reference_save(&mut fx.app);
    fx.app.dispatch(Action::Start { dry_run: false });
    wait_until(&mut fx.app, "result", on_result_screen);
    for action in [
        Action::OpenSaveFolder,
        Action::OpenBackup,
        Action::OpenReport,
    ] {
        fx.app.dispatch(action);
    }
    let opened = fx.platform.opened.lock().unwrap().clone();
    assert_eq!(
        opened,
        [
            "reveal C:/Users/Me/save/EDIT00000000",
            "reveal C:/Users/Me/AppData/Local/FLDailyEdit/backups/EDIT00000000.bak",
            "open C:/reports/transfer_summary.md",
        ]
    );
    fx.app.dispatch(Action::BackToSetup);
    assert!(matches!(fx.app.screen(), Screen::Setup));
}

#[test]
fn remembered_choices_survive_a_restart() {
    let mut fx = fixture(ScriptedEngine::new(Vec::new(), Ok(UpdateResult::default())));
    choose_reference_save(&mut fx.app);
    let dir = fx.state_dir.path().to_path_buf();
    let settings = fl_app::settings::Settings::load(&dir);
    assert_eq!(
        settings.save_path.as_deref(),
        Some(reference_save().as_path())
    );
    let again = fl_app::gui::App::new(fl_app::gui::Services {
        engine: std::sync::Arc::new(ScriptedEngine::new(Vec::new(), Ok(UpdateResult::default()))),
        env: fl_engine::api::Environment { state_dir: dir },
        platform: std::sync::Arc::new(FakePlatform::default()),
        update_backend: std::sync::Arc::new(NoUpdates),
        silent_update_check: false,
        detect: std::sync::Arc::new(|| (Vec::new(), None)),
    });
    assert_eq!(
        again.setup().save_path.as_deref(),
        Some(reference_save().as_path())
    );
}
