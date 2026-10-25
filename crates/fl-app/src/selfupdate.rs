//! App self-update: a background check that never gets in the way, a manual
//! "Check for updates", and download → verify → replace → restart.
//!
//! The wire protocol, signature and file replacement live in `fl-update`;
//! this module only runs them off the UI thread and keeps the state the UI shows.

use std::path::PathBuf;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{Receiver, Sender, channel};

use fl_update::{Available, ErrorKind, Fetch, Staged};

/// What the self-update needs from the outside world.
pub trait Backend: Send + Sync + 'static {
    fn check(&self, current: &str) -> fl_update::Result<Option<Available>>;
    fn download(
        &self,
        available: &Available,
        progress: &mut dyn FnMut(u64, u64),
        cancel: &AtomicBool,
    ) -> fl_update::Result<Staged>;
    /// Swap the running executable for `staged` and start the new one.
    fn replace_and_restart(&self, staged: &Staged) -> fl_update::Result<()>;
    /// Remove the leftovers of an earlier swap. Called once at launch.
    fn cleanup_previous(&self);
}

/// Real implementation: GitHub release over HTTPS, signed manifest.
pub struct SignedRelease<F: Fetch + Send + Sync + 'static = fl_update::HttpFetch> {
    fetch: F,
    current_exe: Option<PathBuf>,
}

impl SignedRelease {
    pub fn new() -> Self {
        Self {
            fetch: fl_update::HttpFetch,
            current_exe: std::env::current_exe().ok(),
        }
    }
}

impl Default for SignedRelease {
    fn default() -> Self {
        Self::new()
    }
}

impl<F: Fetch + Send + Sync + 'static> Backend for SignedRelease<F> {
    fn check(&self, current: &str) -> fl_update::Result<Option<Available>> {
        fl_update::check_for_update(current, &self.fetch)
    }

    fn download(
        &self,
        available: &Available,
        progress: &mut dyn FnMut(u64, u64),
        cancel: &AtomicBool,
    ) -> fl_update::Result<Staged> {
        fl_update::download_and_stage(available, &self.fetch, progress, cancel)
    }

    fn replace_and_restart(&self, staged: &Staged) -> fl_update::Result<()> {
        let current = self.current_exe.as_deref().ok_or_else(|| {
            fl_update::Error::Unsupported("the path of the running app is unknown".into())
        })?;
        let replaced = fl_update::replace_current_exe(staged, current)?;
        fl_update::restart(&replaced)
    }

    fn cleanup_previous(&self) {
        if let Some(current) = &self.current_exe {
            fl_update::cleanup_after_update(current);
        }
    }
}

#[derive(Clone)]
pub enum State {
    Idle,
    Checking {
        manual: bool,
    },
    UpToDate,
    Available(Arc<Available>),
    Downloading {
        done: u64,
        total: u64,
    },
    /// The new version is installed and starting; the app should exit.
    Restarting,
    /// Only reached through a manual action; silent checks go back to `Idle`.
    Failed(String),
}

enum Event {
    Checked {
        manual: bool,
        result: fl_update::Result<Option<Available>>,
    },
    Progress {
        done: u64,
        total: u64,
    },
    Installed(fl_update::Result<()>),
}

pub struct SelfUpdate {
    backend: Arc<dyn Backend>,
    version: String,
    state: State,
    events: Receiver<Event>,
    sender: Sender<Event>,
    cancel: Arc<AtomicBool>,
    wake: Arc<dyn Fn() + Send + Sync>,
}

impl SelfUpdate {
    pub fn new(
        backend: Arc<dyn Backend>,
        version: &str,
        wake: Arc<dyn Fn() + Send + Sync>,
    ) -> Self {
        let (sender, events) = channel();
        Self {
            backend,
            version: version.to_owned(),
            state: State::Idle,
            events,
            sender,
            cancel: Arc::new(AtomicBool::new(false)),
            wake,
        }
    }

    pub fn state(&self) -> &State {
        &self.state
    }

    pub fn version(&self) -> &str {
        &self.version
    }

    pub fn is_busy(&self) -> bool {
        matches!(
            self.state,
            State::Checking { .. } | State::Downloading { .. } | State::Restarting
        )
    }

    /// Tidy up after a previous swap, then look for a newer version in the
    /// background without ever reporting a failure.
    pub fn start_silent_check(&mut self) {
        let backend = Arc::clone(&self.backend);
        std::thread::spawn(move || backend.cleanup_previous());
        self.check(false);
    }

    pub fn check_now(&mut self) {
        self.check(true);
    }

    fn check(&mut self, manual: bool) {
        if self.is_busy() {
            return;
        }
        self.state = State::Checking { manual };
        let (backend, sender, wake, version) = (
            Arc::clone(&self.backend),
            self.sender.clone(),
            Arc::clone(&self.wake),
            self.version.clone(),
        );
        std::thread::spawn(move || {
            let result = backend.check(&version);
            let _ = sender.send(Event::Checked { manual, result });
            wake();
        });
    }

    /// Download the offered version and swap it in.
    pub fn install(&mut self) {
        let State::Available(available) = &self.state else {
            return;
        };
        let available = Arc::clone(available);
        self.state = State::Downloading {
            done: 0,
            total: available.size,
        };
        self.cancel.store(false, Ordering::Release);
        let (backend, sender, wake, cancel) = (
            Arc::clone(&self.backend),
            self.sender.clone(),
            Arc::clone(&self.wake),
            Arc::clone(&self.cancel),
        );
        std::thread::spawn(move || {
            let result = backend
                .download(
                    &available,
                    &mut |done, total| {
                        let _ = sender.send(Event::Progress { done, total });
                        wake();
                    },
                    &cancel,
                )
                .and_then(|staged| {
                    let installed = backend.replace_and_restart(&staged);
                    if installed.is_err() {
                        staged.discard();
                    }
                    installed
                });
            let _ = sender.send(Event::Installed(result));
            wake();
        });
    }

    /// Apply finished background work. Returns `true` once the app must exit
    /// because the new version has been started.
    pub fn poll(&mut self) -> bool {
        while let Ok(event) = self.events.try_recv() {
            match event {
                Event::Checked { manual, result } => {
                    self.state = match result {
                        Ok(Some(available)) => State::Available(Arc::new(available)),
                        Ok(None) if manual => State::UpToDate,
                        Err(error) if manual => State::Failed(describe(&error)),
                        Ok(None) | Err(_) => State::Idle,
                    };
                }
                Event::Progress { done, total } => {
                    if matches!(self.state, State::Downloading { .. }) {
                        self.state = State::Downloading { done, total };
                    }
                }
                Event::Installed(Ok(())) => self.state = State::Restarting,
                Event::Installed(Err(error)) => {
                    self.state = match error.kind() {
                        ErrorKind::Cancelled => State::Idle,
                        _ => State::Failed(describe(&error)),
                    };
                }
            }
        }
        matches!(self.state, State::Restarting)
    }
}

/// One plain sentence per failure class.
fn describe(error: &fl_update::Error) -> String {
    match error.kind() {
        ErrorKind::Network | ErrorKind::Timeout | ErrorKind::Http => {
            "Couldn't reach the update server. Check your internet connection and try again.".into()
        }
        ErrorKind::MissingSignature
        | ErrorKind::InvalidSignature
        | ErrorKind::InvalidManifest
        | ErrorKind::UntrustedUrl => {
            "The update could not be verified, so it was not installed.".into()
        }
        ErrorKind::TooLarge
        | ErrorKind::SizeMismatch
        | ErrorKind::ChecksumMismatch
        | ErrorKind::InvalidArchive => {
            "The downloaded update was damaged, so it was not installed. Try again later.".into()
        }
        ErrorKind::Io | ErrorKind::Unsupported => format!(
            "The update could not be installed: {error}. You can download the new version from the project page instead."
        ),
        ErrorKind::Cancelled => "The update was cancelled.".into(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use fl_update::Manifest;
    use std::sync::Mutex;
    use std::time::{Duration, Instant};

    struct Fake {
        check: Mutex<Option<fl_update::Result<Option<Available>>>>,
        install: Mutex<Option<fl_update::Result<()>>>,
        calls: Mutex<Vec<&'static str>>,
    }

    impl Fake {
        fn new(
            check: fl_update::Result<Option<Available>>,
            install: fl_update::Result<()>,
        ) -> Arc<Self> {
            Arc::new(Self {
                check: Mutex::new(Some(check)),
                install: Mutex::new(Some(install)),
                calls: Mutex::new(Vec::new()),
            })
        }
    }

    impl Backend for Fake {
        fn check(&self, _current: &str) -> fl_update::Result<Option<Available>> {
            self.calls.lock().unwrap().push("check");
            self.check.lock().unwrap().take().unwrap()
        }
        fn download(
            &self,
            _available: &Available,
            progress: &mut dyn FnMut(u64, u64),
            _cancel: &AtomicBool,
        ) -> fl_update::Result<Staged> {
            self.calls.lock().unwrap().push("download");
            progress(5, 10);
            Ok(Staged {
                executable: std::env::temp_dir().join("fldailyedit-fake-staged"),
                version: "9.9.9".parse().unwrap(),
            })
        }
        fn replace_and_restart(&self, _staged: &Staged) -> fl_update::Result<()> {
            self.calls.lock().unwrap().push("replace");
            self.install.lock().unwrap().take().unwrap()
        }
        fn cleanup_previous(&self) {
            self.calls.lock().unwrap().push("cleanup");
        }
    }

    fn available() -> Available {
        Available::new(
            Manifest::new(
                "9.9.9".parse().unwrap(),
                fl_update::manifest::APP_INSTALLER_ASSET_NAME.into(),
                fl_update::manifest::APP_INSTALLER_URL.into(),
                10,
                "0".repeat(64),
            )
            .unwrap(),
        )
        .unwrap()
    }

    fn wait_for(update: &mut SelfUpdate, done: impl Fn(&State) -> bool) -> bool {
        let start = Instant::now();
        let mut exit = false;
        while start.elapsed() < Duration::from_secs(5) {
            exit |= update.poll();
            if done(update.state()) {
                return exit;
            }
            std::thread::sleep(Duration::from_millis(2));
        }
        panic!(
            "timed out in state {:?}",
            std::mem::discriminant(update.state())
        );
    }

    fn controller(fake: &Arc<Fake>) -> SelfUpdate {
        SelfUpdate::new(fake.clone(), "1.0.0", Arc::new(|| {}))
    }

    #[test]
    fn silent_failure_leaves_no_trace_but_manual_failure_explains() {
        let fake = Fake::new(Err(fl_update::Error::Timeout), Ok(()));
        let mut update = controller(&fake);
        update.start_silent_check();
        wait_for(&mut update, |s| matches!(s, State::Idle));

        let fake = Fake::new(Err(fl_update::Error::Timeout), Ok(()));
        let mut update = controller(&fake);
        update.check_now();
        wait_for(&mut update, |s| matches!(s, State::Failed(_)));
        let State::Failed(message) = update.state() else {
            unreachable!()
        };
        assert!(message.contains("internet connection"), "{message}");
    }

    #[test]
    fn up_to_date_is_only_reported_for_manual_checks() {
        let fake = Fake::new(Ok(None), Ok(()));
        let mut update = controller(&fake);
        update.start_silent_check();
        wait_for(&mut update, |s| matches!(s, State::Idle));

        let fake = Fake::new(Ok(None), Ok(()));
        let mut update = controller(&fake);
        update.check_now();
        wait_for(&mut update, |s| matches!(s, State::UpToDate));
    }

    #[test]
    fn available_update_installs_and_asks_the_app_to_exit() {
        let fake = Fake::new(Ok(Some(available())), Ok(()));
        let mut update = controller(&fake);
        update.start_silent_check();
        wait_for(&mut update, |s| matches!(s, State::Available(_)));
        update.install();
        assert!(matches!(
            update.state(),
            State::Downloading { total: 10, .. }
        ));
        let exit = wait_for(&mut update, |s| matches!(s, State::Restarting));
        assert!(exit);
        let calls = fake.calls.lock().unwrap().clone();
        assert!(calls.ends_with(&["download", "replace"]), "{calls:?}");
    }

    #[test]
    fn failed_install_is_reported_and_app_keeps_running() {
        let fake = Fake::new(
            Ok(Some(available())),
            Err(fl_update::Error::Unsupported("read-only folder".into())),
        );
        let mut update = controller(&fake);
        update.check_now();
        wait_for(&mut update, |s| matches!(s, State::Available(_)));
        update.install();
        let exit = wait_for(&mut update, |s| matches!(s, State::Failed(_)));
        assert!(!exit);
    }
}
