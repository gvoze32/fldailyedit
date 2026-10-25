//! Open the real window against a scripted engine (no network, nothing written):
//!
//! ```text
//! cargo run -p fl-app --example demo -- [fail|nochanges|preview]
//! ```
//!
//! Choose any save (your own `EDIT00000000`), press Update and
//! watch the progress and result screens.

use std::sync::Arc;
use std::time::Duration;

use fl_app::gui::{self, platform};
use fl_app::scripted::{ScriptedEngine, Step};
use fl_app::selfupdate::SignedRelease;
use fl_engine::api::{
    ChangeCounts, Environment, ErrorCode, Outcome, Stage, UpdateError, UpdateResult,
};

fn main() -> eframe::Result {
    let mode = std::env::args().nth(1).unwrap_or_default();
    let step = |stage, message: &str, current, total| {
        [
            ScriptedEngine::step(stage, message, current, total),
            Step::Delay(Duration::from_millis(350)),
        ]
    };
    let mut steps: Vec<Step> = Vec::new();
    steps.extend(step(Stage::Validating, "Checking the save", 0, 0));
    for club in 1..=12 {
        steps.extend(step(
            Stage::Fetching,
            &format!("Fetched club {club}"),
            club,
            12,
        ));
    }
    steps.extend(step(Stage::Matching, "Matching players", 0, 0));
    steps.extend(step(Stage::Applying, "Preparing changes", 0, 0));
    steps.push(Step::Checkpoint(Stage::Applying));
    steps.extend(step(Stage::Verifying, "Verifying the result", 0, 0));
    steps.push(Step::BeginCommit);
    steps.extend(step(Stage::Committing, "Writing the verified save", 0, 0));

    let result = match mode.as_str() {
        "fail" => Err(UpdateError {
            code: ErrorCode::FetchFailed,
            stage: Some(Stage::Fetching),
            message: "demo failure".into(),
        }),
        other => Ok(UpdateResult {
            outcome: match other {
                "nochanges" => Outcome::NoChanges,
                "preview" => Outcome::Preview,
                _ => Outcome::Updated,
            },
            counts: ChangeCounts {
                transfers: 3,
                shirt_numbers: 2,
                ..ChangeCounts::default()
            },
            report_markdown: "# Demo report\n\n- one\n- two\n".into(),
            ..UpdateResult::default()
        }),
    };
    let state_dir = std::env::temp_dir().join("FLDailyEditDemo");
    gui::run(gui::Services {
        engine: Arc::new(ScriptedEngine::new(steps, result)),
        env: Environment { state_dir },
        platform: Arc::new(platform::Native),
        update_backend: Arc::new(SignedRelease::new()),
        silent_update_check: false,
        detect: Arc::new(gui::detect_installed),
    })
}
