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

/// The committed Football Life 2026 reference save.
pub fn reference_save() -> PathBuf {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../base/EDIT00000000");
    assert!(path.is_file(), "reference save missing: {}", path.display());
    path
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

/// Point the app at the reference save and wait for the check to finish.
pub fn choose_reference_save(app: &mut App) {
    app.dispatch(fl_app::gui::Action::SaveChosen(reference_save()));
    wait_until(app, "the save check", |app| app.setup().ready());
}

pub fn result_ok(result: UpdateResult) -> Result<UpdateResult, UpdateError> {
    Ok(result)
}
