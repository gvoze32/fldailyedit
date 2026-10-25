//! Shared helpers: an `App` wired to a scripted engine, fake OS services and
//! no network.

// Compiled into every test binary, each of which uses a different subset.
#![allow(dead_code)]

use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use fl_app::gui::platform::Platform;
use fl_app::gui::{App, Screen, Services};
use fl_app::scripted::ScriptedEngine;
use fl_app::selfupdate::Backend;
use fl_engine::api::{Environment, UpdateError, UpdateResult};
use fl_update::{Available, Staged};

/// The owner's local Football Life 2026 save (`testdata/local/base/EDIT00000000`,
/// or `FL_TEST_BASE_SAVE`). `None`, with a note on stderr, when it is absent: the
/// repository ships no save and tests that need one skip.
pub fn reference_save() -> Option<PathBuf> {
    let path = match std::env::var_os("FL_TEST_BASE_SAVE") {
        Some(path) => PathBuf::from(path),
        None => {
            Path::new(env!("CARGO_MANIFEST_DIR")).join("../../testdata/local/base/EDIT00000000")
        }
    };
    if path.is_file() {
        Some(path)
    } else {
        eprintln!(
            "skipping: local base save not present at {}",
            path.display()
        );
        None
    }
}

#[derive(Default)]
pub struct FakePlatform {
    pub save: Mutex<Option<PathBuf>>,
    pub folder: Mutex<Option<PathBuf>>,
    pub opened: Mutex<Vec<String>>,
}

impl Platform for FakePlatform {
    fn pick_save(&self) -> Option<PathBuf> {
        self.save.lock().unwrap().take()
    }
    fn pick_folder(&self) -> Option<PathBuf> {
        self.folder.lock().unwrap().take()
    }
    fn open(&self, path: &Path) -> Result<(), String> {
        self.opened
            .lock()
            .unwrap()
            .push(format!("open {}", path.display()));
        Ok(())
    }
    fn reveal(&self, path: &Path) -> Result<(), String> {
        self.opened
            .lock()
            .unwrap()
            .push(format!("reveal {}", path.display()));
        Ok(())
    }
}

/// A self-update backend that never finds anything.
pub struct NoUpdates;

impl Backend for NoUpdates {
    fn check(&self, _current: &str) -> fl_update::Result<Option<Available>> {
        Ok(None)
    }
    fn download(
        &self,
        _available: &Available,
        _progress: &mut dyn FnMut(u64, u64),
        _cancel: &std::sync::atomic::AtomicBool,
    ) -> fl_update::Result<Staged> {
        unreachable!("no update is ever offered")
    }
    fn replace_and_restart(&self, _staged: &Staged) -> fl_update::Result<()> {
        unreachable!("no update is ever offered")
    }
    fn cleanup_previous(&self) {}
}

pub struct Fixture {
    pub app: App,
    pub platform: Arc<FakePlatform>,
    pub state_dir: tempfile::TempDir,
}

pub fn fixture(engine: ScriptedEngine) -> Fixture {
    let state_dir = tempfile::tempdir().unwrap();
    let platform = Arc::new(FakePlatform::default());
    let app = App::new(Services {
        engine: Arc::new(engine),
        env: Environment {
            state_dir: state_dir.path().into(),
        },
        platform: platform.clone(),
        update_backend: Arc::new(NoUpdates),
        silent_update_check: false,
        detect: Arc::new(|| (Vec::new(), None)),
    });
    Fixture {
        app,
        platform,
        state_dir,
    }
}

/// Pump the app until `done` holds.
pub fn wait_until(app: &mut App, what: &str, done: impl Fn(&App) -> bool) {
    let start = Instant::now();
    while start.elapsed() < Duration::from_secs(60) {
        app.poll();
        if done(app) {
            return;
        }
        std::thread::sleep(Duration::from_millis(5));
    }
    panic!("timed out waiting for {what}");
}

pub fn on_result_screen(app: &App) -> bool {
    matches!(app.screen(), Screen::Result)
}

/// A stand-in game folder: `download/data_extra.cpk` exists, which is all the
/// setup screen checks before the engine runs.
pub fn fake_game_folder() -> PathBuf {
    let download = Path::new(env!("CARGO_TARGET_TMPDIR")).join("fake_game/download");
    std::fs::create_dir_all(&download).unwrap();
    std::fs::write(download.join("data_extra.cpk"), b"x").unwrap();
    download.parent().unwrap().to_owned()
}

/// Point the app at the reference save and the stand-in game folder, and wait
/// for both checks to finish.
pub fn choose_reference_save(app: &mut App, save: &Path) {
    app.dispatch(fl_app::gui::Action::GameChosen(fake_game_folder()));
    app.dispatch(fl_app::gui::Action::SaveChosen(save.to_owned()));
    wait_until(app, "the save and game checks", |app| app.setup().ready());
}

pub fn result_ok(result: UpdateResult) -> Result<UpdateResult, UpdateError> {
    Ok(result)
}
