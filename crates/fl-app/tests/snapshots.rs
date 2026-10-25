//! Renders every screen to PNG under `tests/snapshots` (compared on later runs;
//! refresh with `UPDATE_SNAPSHOTS=1 cargo test -p fl-app --test snapshots`).

mod common;

use std::path::PathBuf;
use std::time::Duration;

use common::*;
use eframe::egui::{self, Theme, vec2};
use egui_kittest::{Harness, SnapshotOptions};
use fl_app::gui::setup::{GameStatus, SaveStatus};
use fl_app::gui::{Action, App, theme};
use fl_app::scripted::{Gate, ScriptedEngine, Step};
use fl_engine::api::{
    ChangeCounts, ErrorCode, Outcome, SkippedTransfer, Stage, UpdateError, UpdateResult,
};
use fl_engine::locations::{SaveCandidate, SaveFamily, SaveInfo, SaveKind};

const SIZE: egui::Vec2 = vec2(780.0, 860.0);

fn harness(app: App, theme_choice: Theme) -> Harness<'static, App> {
    Harness::builder()
        .with_size(SIZE)
        .with_theme(theme_choice)
        .with_context_setup(theme::apply)
        .wgpu()
        .build_ui_state(|ui, app: &mut App| app.show(ui), app)
}

fn options() -> SnapshotOptions {
    SnapshotOptions::new().threshold(1.5).max_failed_pixels(400)
}

fn settle(harness: &mut Harness<'_, App>) {
    harness.run_steps(6);
}

fn skipped(
    player: &str,
    from: &str,
    to: &str,
    reason: &str,
    detail: &str,
    relevant: bool,
) -> SkippedTransfer {
    SkippedTransfer {
        player: player.into(),
        from_club: from.into(),
        to_club: to.into(),
        date: Some("2026-01-14".into()),
        transfer_type: "transfer".into(),
        reason: reason.into(),
        detail: detail.into(),
        relevant,
    }
}

const REPORT: &str = "\
# Transfer summary

**Window:** since 2026-01-01 · **Source:** FotMob, Transfermarkt

## Applied (4)

| Player | From | To | Date |
|---|---|---|---|
| Marc Guéhi | Crystal Palace | Manchester City | 2026-01-14 |
| Antoine Semenyo | Bournemouth | Manchester City | 2026-01-09 |
| Conor Gallagher | Atlético Madrid | Tottenham Hotspur | 2026-01-05 |
| Jan Bednarek | Southampton | Aston Villa | 2026-01-03 |

## Shirt numbers

- Manchester City: Guéhi **#15**, Semenyo **#42**
- Tottenham Hotspur: Gallagher **#22**
";

fn updated_result() -> UpdateResult {
    UpdateResult {
        target_path: r"C:\Users\Player\Documents\KONAMI\eFootball PES 2021 SEASON UPDATE\2026\save\EDIT00000000".into(),
        outcome: Outcome::Updated,
        backup_path: Some(r"C:\Users\Player\AppData\Local\FLDailyEdit\backups\EDIT00000000-2026-10-25.bak".into()),
        written_sha256: Some("9f2c1a7d0b6e44f1a3c58e90d2b7a6c4f1e83d5a0b9c7e2f4d6a8b1c3e5f7092".into()),
        counts: ChangeCounts { transfers: 4, shirt_numbers: 3, captains: 1, formations: 2, tactics: 0, unchanged: 311, safety_skipped: 0 },
        report_markdown: REPORT.into(),
        report_paths: vec![r"C:\Users\Player\AppData\Local\FLDailyEdit\reports\transfer_summary.md".into()],
        skipped: vec![
            skipped("Kamaldeen Sulemana", "Southampton", "Atalanta", "player_not_in_save", "no player with this name in the save", false),
            skipped("Amad Diallo", "Manchester United", "Sunderland", "roster_full", "Sunderland already has 32 players", true),
            skipped("Tyrick Mitchell", "Crystal Palace", "Arsenal", "club_not_in_save", "Arsenal is not a club in this save", true),
            skipped("Lamine Yamal", "Barcelona", "Barcelona", "unchanged", "already at the club", false),
        ],
        warnings: vec!["2 clubs could not be matched to FotMob; their squads were left as they were.".into()],
    }
}

/// An app on the setup screen with a ready FL26 save, with stable display text.
fn ready_setup(engine: ScriptedEngine) -> Fixture {
    let mut fx = fixture(engine);
    // Let the first-frame detection and probe start so they cannot overwrite the staged state.
    fx.app.poll();
    std::thread::sleep(Duration::from_millis(200));
    fx.app.poll();
    let shown = PathBuf::from(
        r"C:\Users\Player\Documents\KONAMI\eFootball PES 2021 SEASON UPDATE\2026\save\EDIT00000000",
    );
    let setup = fx.app.setup_mut();
    setup.detected = vec![
        SaveCandidate {
            edit_file: shown.clone(),
            family: SaveFamily::FootballLife26,
        },
        SaveCandidate {
            edit_file: PathBuf::from(
                r"C:\Users\Player\Documents\KONAMI\eFootball PES 2021 SEASON UPDATE\0123\save\EDIT00000000",
            ),
            family: SaveFamily::Pes2021,
        },
    ];
    setup.set_save(Some(shown.clone()));
    setup.save = SaveStatus::Ready {
        resolved: reference_save(),
        info: SaveInfo {
            kind: SaveKind::FootballLife26,
            game_version: "FL26".into(),
            teams: 747,
            clubs: 540,
            players: 21_640,
            problems: vec![],
        },
    };
    let game = PathBuf::from(r"D:\Games\Football Life 2026");
    let setup = fx.app.setup_mut();
    setup.set_game(Some(game.clone()));
    setup.game = GameStatus::Usable {
        archives: vec![game.join("download").join("data_extra.cpk")],
    };
    fx.app.setup_mut().generation += 1; // drop answers of probes that started before the staging
    fx
}

fn start_and_run(fx: &mut Fixture, dry_run: bool) {
    fx.app.dispatch(Action::Start { dry_run });
}

#[test]
fn setup_light() {
    let fx = ready_setup(ScriptedEngine::new(Vec::new(), Ok(UpdateResult::default())));
    let mut harness = harness(fx.app, Theme::Light);
    settle(&mut harness);
    harness.snapshot_options("setup_light", &options());
}

#[test]
fn setup_dark() {
    let fx = ready_setup(ScriptedEngine::new(Vec::new(), Ok(UpdateResult::default())));
    let mut harness = harness(fx.app, Theme::Dark);
    settle(&mut harness);
    harness.snapshot_options("setup_dark", &options());
}

#[test]
fn setup_game_folder_not_chosen() {
    let mut fx = ready_setup(ScriptedEngine::new(Vec::new(), Ok(UpdateResult::default())));
    let setup = fx.app.setup_mut();
    setup.set_game(None);
    setup.game = GameStatus::Empty;
    setup.generation += 1;
    let mut harness = harness(fx.app, Theme::Light);
    settle(&mut harness);
    harness.snapshot_options("setup_game_folder_not_chosen", &options());
}

#[test]
fn setup_pes21_needs_game_folder() {
    let mut fx = ready_setup(ScriptedEngine::new(Vec::new(), Ok(UpdateResult::default())));
    let setup = fx.app.setup_mut();
    setup.set_save(Some(PathBuf::from(
        r"C:\Users\Player\Documents\KONAMI\eFootball PES 2021 SEASON UPDATE\0123\save\EDIT00000000",
    )));
    setup.save = SaveStatus::Ready {
        resolved: reference_save(),
        info: SaveInfo {
            kind: SaveKind::Pes2021,
            game_version: "PES 2021".into(),
            teams: 700,
            clubs: 500,
            players: 18_000,
            problems: vec![],
        },
    };
    setup.set_game(Some(PathBuf::from(r"D:\Games\PES 2021")));
    setup.game = GameStatus::Unusable;
    setup.generation += 1;
    let mut harness = harness(fx.app, Theme::Light);
    settle(&mut harness);
    harness.snapshot_options("setup_pes21_game_folder_invalid", &options());
}

#[test]
fn progress_mid_fetch() {
    let gate = Gate::new();
    let engine = ScriptedEngine::new(
        vec![
            ScriptedEngine::step(
                Stage::Validating,
                "Save checked: 540 clubs, 21640 players",
                0,
                0,
            ),
            ScriptedEngine::step(Stage::Fetching, "Fetching squads and transfers", 0, 0),
            ScriptedEngine::step(Stage::Fetching, "Fetched Manchester City (8456)", 12, 30),
            ScriptedEngine::step(Stage::Fetching, "Fetched Arsenal (9825)", 13, 30),
            Step::Pause(Stage::Fetching, gate),
        ],
        Ok(updated_result()),
    );
    let mut fx = ready_setup(engine);
    start_and_run(&mut fx, false);
    wait_until(&mut fx.app, "fetch progress", |app| app.running());
    std::thread::sleep(Duration::from_millis(150));
    fx.app.poll();
    fx.app.backdate_run(Duration::from_secs(83));
    let mut harness = harness(fx.app, Theme::Light);
    settle(&mut harness);
    harness.snapshot_options("progress_mid_fetch", &options());
}

#[test]
fn progress_while_committing_dark() {
    let gate = Gate::new();
    let engine = ScriptedEngine::new(
        vec![
            ScriptedEngine::step(Stage::Fetching, "Fetched 30 of 30 clubs", 30, 30),
            Step::BeginCommit,
            ScriptedEngine::step(Stage::Committing, "Writing the verified save", 0, 0),
            Step::Pause(Stage::Committing, gate),
        ],
        Ok(updated_result()),
    );
    let mut fx = ready_setup(engine);
    start_and_run(&mut fx, false);
    wait_until(&mut fx.app, "commit", |app| app.committing());
    fx.app.backdate_run(Duration::from_secs(172));
    let mut harness = harness(fx.app, Theme::Dark);
    settle(&mut harness);
    harness.snapshot_options("progress_committing_dark", &options());
}

fn finished(
    result: Result<UpdateResult, UpdateError>,
    theme_choice: Theme,
    name: &str,
    tweak: impl FnOnce(&mut App),
) {
    let mut fx = ready_setup(ScriptedEngine::new(
        vec![ScriptedEngine::step(Stage::Fetching, "Fetching", 1, 2)],
        result,
    ));
    start_and_run(&mut fx, false);
    wait_until(&mut fx.app, "result", on_result_screen);
    tweak(&mut fx.app);
    let mut harness = harness(fx.app, theme_choice);
    settle(&mut harness);
    harness.snapshot_options(name, &options());
}

#[test]
fn result_updated() {
    finished(Ok(updated_result()), Theme::Light, "result_updated", |_| {});
}

#[test]
fn result_updated_dark() {
    finished(
        Ok(updated_result()),
        Theme::Dark,
        "result_updated_dark",
        |_| {},
    );
}

#[test]
fn result_updated_not_applied_tab() {
    finished(
        Ok(updated_result()),
        Theme::Light,
        "result_not_applied",
        |app| {
            app.show_result_tab(fl_app::gui::result::Tab::NotApplied);
        },
    );
}

#[test]
fn result_no_changes() {
    let result = UpdateResult {
        outcome: Outcome::NoChanges,
        target_path: updated_result().target_path,
        counts: ChangeCounts {
            unchanged: 311,
            ..ChangeCounts::default()
        },
        ..UpdateResult::default()
    };
    finished(Ok(result), Theme::Light, "result_no_changes", |_| {});
}

#[test]
fn result_preview() {
    let result = UpdateResult {
        outcome: Outcome::Preview,
        backup_path: None,
        report_paths: vec![],
        ..updated_result()
    };
    finished(Ok(result), Theme::Light, "result_preview", |_| {});
}

#[test]
fn result_failed_fetch() {
    let error = UpdateError {
        code: ErrorCode::FetchFailed,
        stage: Some(Stage::Fetching),
        message: "fotmob: connection timed out after 3 retries".into(),
    };
    finished(Err(error), Theme::Light, "result_failed", |_| {});
}

#[test]
fn result_failed_needs_game_folder_dark() {
    let error = UpdateError {
        code: ErrorCode::NativeDatabaseMissing,
        stage: Some(Stage::Validating),
        message: "Player.bin not found in any game archive".into(),
    };
    finished(
        Err(error),
        Theme::Dark,
        "result_failed_game_folder_dark",
        |_| {},
    );
}

#[test]
fn result_cancelled() {
    let error = UpdateError {
        code: ErrorCode::Cancelled,
        stage: Some(Stage::Fetching),
        message: "Update cancelled".into(),
    };
    finished(Err(error), Theme::Light, "result_cancelled", |_| {});
}
