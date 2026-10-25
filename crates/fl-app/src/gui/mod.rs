//! The desktop app: Setup → Progress → Result, local-first, one update mode.
//!
//! `App` owns all state and is driven by `Action`s, so tests (and the
//! scripted demo) can run complete flows without a window.

pub mod copy;
pub mod platform;
pub mod progress;
pub mod result;
pub mod setup;
pub mod theme;

use std::path::PathBuf;
use std::sync::mpsc::{Receiver, Sender, channel};
use std::sync::{Arc, OnceLock};
use std::time::{Duration, Instant};

use eframe::egui::{self, CentralPanel, Panel, RichText, ScrollArea, Ui};
use fl_engine::api::{Environment, UpdateRequest};
use fl_engine::locations::{self, SaveCandidate};

use self::platform::Platform;
use self::progress::{ProgressInfo, RunView};
use self::result::ResultView;
use self::setup::{GameStatus, SaveStatus, SetupState};
use crate::engine::Engine;
use crate::selfupdate::{self, SelfUpdate};
use crate::session::{RunEvent, RunSession};
use crate::settings::Settings;

/// Everything a click can ask for.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Action {
    BrowseSave,
    BrowseGame,
    SaveChosen(PathBuf),
    SaveCleared,
    GameChosen(PathBuf),
    GameCleared,
    Start { dry_run: bool },
    Cancel,
    LogToggled(bool),
    BackToSetup,
    UpdateAfterPreview,
    ChooseGameFolderAfterFailure,
    OpenSaveFolder,
    OpenBackup,
    OpenReport,
    CopyDiagnostics,
    CheckForUpdates,
    InstallUpdate,
}

pub enum Screen {
    Setup,
    Running,
    Result,
}

struct ActiveRun {
    session: RunSession,
    request: UpdateRequest,
    view: RunView,
    started: Instant,
}

/// Answers from background work, delivered to the UI thread.
enum Background {
    Detected {
        saves: Vec<SaveCandidate>,
        game: Option<PathBuf>,
    },
    Probed {
        generation: u64,
        save: SaveStatus,
        game: GameStatus,
    },
}

pub struct App {
    engine: Arc<dyn Engine>,
    env: Environment,
    platform: Arc<dyn Platform>,
    settings: Settings,
    screen: Screen,
    setup: SetupState,
    run: Option<ActiveRun>,
    result: Option<ResultView>,
    update: SelfUpdate,
    background: Receiver<Background>,
    background_sender: Sender<Background>,
    /// Set on the first frame; background threads use it to wake the UI.
    ctx: Arc<OnceLock<egui::Context>>,
    /// Text shown briefly after a click ("Copied").
    notice: Option<(String, Instant)>,
    /// The window was asked to close while a run could still be cancelled.
    close_when_idle: bool,
    log_open: bool,
    /// Autodetection (saves, game folder) has been started.
    started: bool,
    silent_update_check: bool,
    detect: Detect,
}

/// A callback that repaints the UI once its context is known.
fn wake_fn(ctx: &Arc<OnceLock<egui::Context>>) -> impl Fn() + Send + Sync + 'static + use<> {
    let ctx = Arc::clone(ctx);
    move || {
        if let Some(ctx) = ctx.get() {
            ctx.request_repaint();
        }
    }
}

/// Collaborators of [`App`].
pub struct Services {
    pub engine: Arc<dyn Engine>,
    pub env: Environment,
    pub platform: Arc<dyn Platform>,
    pub update_backend: Arc<dyn selfupdate::Backend>,
    /// Check for a newer app version at launch.
    pub silent_update_check: bool,
    /// Find the saves and the game folder on this PC (see [`detect_installed`]).
    pub detect: Detect,
}

/// Finds saves and the game folder; runs on a background thread.
pub type Detect = Arc<dyn Fn() -> (Vec<SaveCandidate>, Option<PathBuf>) + Send + Sync>;

/// The real detection: well-known Documents locations and install folders.
pub fn detect_installed() -> (Vec<SaveCandidate>, Option<PathBuf>) {
    (locations::discover_saves(), locations::detect_game_folder())
}

impl App {
    pub fn new(services: Services) -> Self {
        let settings = Settings::load(&services.env.state_dir);
        let (background_sender, background) = channel();
        let ctx = Arc::new(OnceLock::new());
        let update = SelfUpdate::new(
            services.update_backend,
            env!("CARGO_PKG_VERSION"),
            Arc::new(wake_fn(&ctx)),
        );
        Self {
            engine: services.engine,
            env: services.env,
            platform: services.platform,
            setup: SetupState::new(settings.save_path.clone(), settings.game_root.clone()),
            log_open: settings.log_open,
            settings,
            screen: Screen::Setup,
            run: None,
            result: None,
            update,
            background,
            background_sender,
            ctx,
            notice: None,
            close_when_idle: false,
            started: false,
            silent_update_check: services.silent_update_check,
            detect: services.detect,
        }
    }

    pub fn screen(&self) -> &Screen {
        &self.screen
    }

    pub fn setup(&self) -> &SetupState {
        &self.setup
    }

    pub fn result(&self) -> Option<&ResultView> {
        self.result.as_ref()
    }

    /// The run is writing the save, so the window must stay open.
    pub fn committing(&self) -> bool {
        self.run
            .as_ref()
            .is_some_and(|run| run.view.committing() || run.session.commit_started())
    }

    pub fn running(&self) -> bool {
        self.run.is_some()
    }

    /// Test and demo hook: edit the setup state directly.
    #[doc(hidden)]
    pub fn setup_mut(&mut self) -> &mut SetupState {
        &mut self.setup
    }

    /// Test and demo hook: pretend the current run began `ago` earlier.
    #[doc(hidden)]
    pub fn backdate_run(&mut self, ago: Duration) {
        if let Some(run) = &mut self.run {
            run.started = Instant::now() - ago;
        }
    }

    /// Finish pending work and remember the egui context for wake-ups.
    /// Called at the start of every frame; also useful to pump tests.
    pub fn poll(&mut self) {
        if !self.started {
            self.started = true;
            if self.silent_update_check {
                self.update.start_silent_check();
            }
            self.start_detection();
            self.probe();
        }
        while let Ok(message) = self.background.try_recv() {
            match message {
                Background::Detected { saves, game } => self.detected(saves, game),
                Background::Probed {
                    generation,
                    save,
                    game,
                } => {
                    if generation == self.setup.generation {
                        self.setup.save = save;
                        self.setup.game = game;
                    }
                }
            }
        }
        self.poll_run();
        if self.update.poll() {
            self.close_when_idle = true;
        }
        if self
            .notice
            .as_ref()
            .is_some_and(|(_, at)| at.elapsed() > Duration::from_secs(3))
        {
            self.notice = None;
        }
    }

    fn start_detection(&self) {
        let sender = self.background_sender.clone();
        let wake = self.waker();
        let detect = Arc::clone(&self.detect);
        std::thread::spawn(move || {
            let (saves, game) = detect();
            let _ = sender.send(Background::Detected { saves, game });
            wake();
        });
    }

    fn waker(&self) -> impl Fn() + Send + Sync + 'static + use<> {
        wake_fn(&self.ctx)
    }

    fn detected(&mut self, saves: Vec<SaveCandidate>, game: Option<PathBuf>) {
        self.setup.detected = saves;
        let mut changed = false;
        let remembered_gone = matches!(
            (&self.setup.save, &self.setup.save_path),
            (SaveStatus::Invalid(_), Some(_))
        );
        if (self.setup.save_path.is_none() || remembered_gone)
            && let Some(first) = self.setup.detected.first()
        {
            self.setup.set_save(Some(first.edit_file.clone()));
            changed = true;
        }
        if self.setup.game_path.is_none()
            && let Some(game) = game
        {
            self.setup.set_game(Some(game));
            changed = true;
        }
        if changed {
            self.probe();
        }
    }

    /// Re-check the current choices off the UI thread.
    fn probe(&mut self) {
        self.setup.generation += 1;
        let generation = self.setup.generation;
        self.setup.save = if self.setup.save_path.is_some() {
            SaveStatus::Checking
        } else {
            SaveStatus::Empty
        };
        self.setup.game = if self.setup.game_path.is_some() {
            GameStatus::Checking
        } else {
            GameStatus::Empty
        };
        let (save, game) = (self.setup.save_path.clone(), self.setup.game_path.clone());
        let sender = self.background_sender.clone();
        let wake = self.waker();
        std::thread::spawn(move || {
            let (save, game) = setup::probe(save.as_deref(), game.as_deref());
            let _ = sender.send(Background::Probed {
                generation,
                save,
                game,
            });
            wake();
        });
    }

    fn poll_run(&mut self) {
        let Some(run) = &mut self.run else { return };
        let mut finished = None;
        while let Some(event) = run.session.poll() {
            match event {
                RunEvent::Progress(progress) => run.view.apply(&progress),
                RunEvent::Finished(outcome) => {
                    finished = Some(outcome);
                    break;
                }
            }
        }
        if let Some(outcome) = finished
            && let Some(run) = self.run.take()
        {
            let view = ResultView::new(run.request, outcome, run.view.stage, run.started.elapsed());
            self.result = Some(view);
            self.screen = Screen::Result;
        }
    }

    /// Carry out one user action.
    pub fn dispatch(&mut self, action: Action) {
        match action {
            Action::BrowseSave => {
                if let Some(path) = self.platform.pick_save() {
                    self.dispatch(Action::SaveChosen(path));
                }
            }
            Action::BrowseGame => {
                if let Some(path) = self.platform.pick_folder() {
                    self.dispatch(Action::GameChosen(path));
                }
            }
            Action::SaveChosen(path) => {
                self.setup.set_save(Some(path));
                self.probe();
                self.remember();
            }
            Action::SaveCleared => {
                self.setup.set_save(None);
                self.probe();
                self.remember();
            }
            Action::GameChosen(path) => {
                self.setup.set_game(Some(path));
                self.probe();
                self.remember();
            }
            Action::GameCleared => {
                self.setup.set_game(None);
                self.probe();
                self.remember();
            }
            Action::Start { dry_run } => self.start(dry_run),
            Action::Cancel => {
                if let Some(run) = &self.run {
                    // `false` means the commit began first; the screen then shows "Finishing safely…".
                    run.session.request_cancel();
                }
            }
            Action::LogToggled(open) => {
                self.log_open = open;
                self.settings.log_open = open;
                self.save_settings();
            }
            Action::BackToSetup => {
                self.result = None;
                self.screen = Screen::Setup;
                self.probe();
            }
            Action::UpdateAfterPreview => {
                if let Some(view) = &self.result {
                    let mut request = view.request.clone();
                    request.dry_run = false;
                    self.begin(request);
                }
            }
            Action::ChooseGameFolderAfterFailure => {
                self.result = None;
                self.screen = Screen::Setup;
                self.setup.focus_game_folder = true;
                self.probe();
                self.dispatch(Action::BrowseGame);
            }
            Action::OpenSaveFolder => {
                if let Some(view) = &self.result {
                    let path = view.save_file().to_path_buf();
                    self.open_result(|platform| platform.reveal(&path));
                }
            }
            Action::OpenBackup => {
                if let Some(path) = self
                    .result
                    .as_ref()
                    .and_then(|v| v.backup())
                    .map(PathBuf::from)
                {
                    self.open_result(|platform| platform.reveal(&path));
                }
            }
            Action::OpenReport => {
                if let Some(path) = self
                    .result
                    .as_ref()
                    .and_then(|v| v.report_file())
                    .map(PathBuf::from)
                {
                    self.open_result(|platform| platform.open(&path));
                }
            }
            Action::CopyDiagnostics => {
                if let Some(view) = &self.result
                    && let Some(ctx) = self.ctx.get()
                {
                    ctx.copy_text(view.diagnostics(env!("CARGO_PKG_VERSION")));
                    self.notice =
                        Some(("Diagnostics copied to the clipboard".into(), Instant::now()));
                }
            }
            Action::CheckForUpdates => self.update.check_now(),
            Action::InstallUpdate => self.update.install(),
        }
    }

    /// Diagnostics text of the current result (what the button copies).
    pub fn diagnostics_text(&self) -> Option<String> {
        self.result
            .as_ref()
            .map(|view| view.diagnostics(env!("CARGO_PKG_VERSION")))
    }

    fn open_result(&mut self, open: impl FnOnce(&dyn Platform) -> Result<(), String>) {
        if let Err(error) = open(self.platform.as_ref()) {
            self.notice = Some((format!("Couldn't open it: {error}"), Instant::now()));
        }
    }

    fn remember(&mut self) {
        self.settings.save_path = self.setup.save_path.clone();
        self.settings.game_root = self.setup.game_path.clone();
        self.save_settings();
    }

    fn save_settings(&self) {
        // Remembering choices is a convenience; a read-only state folder must not stop an update.
        let _ = self.settings.save(&self.env.state_dir);
    }

    fn start(&mut self, dry_run: bool) {
        let Some(request) = self.setup.request(dry_run) else {
            return;
        };
        self.remember();
        self.begin(request);
    }

    fn begin(&mut self, request: UpdateRequest) {
        if self.run.is_some() {
            return;
        }
        let session = RunSession::spawn(
            Arc::clone(&self.engine),
            request.clone(),
            self.env.clone(),
            self.waker(),
        );
        self.run = Some(ActiveRun {
            session,
            request,
            view: RunView::default(),
            started: Instant::now(),
        });
        self.result = None;
        self.screen = Screen::Running;
    }

    /// Respond to the window's close button. A run that can still be
    /// cancelled is cancelled first; a commit is waited out. Either way the
    /// window closes by itself afterwards.
    pub fn handle_close_request(&mut self, ctx: &egui::Context) {
        if ctx.input(|input| input.viewport().close_requested()) && self.run.is_some() {
            ctx.send_viewport_cmd(egui::ViewportCommand::CancelClose);
            self.close_when_idle = true;
            if self.committing() {
                self.notice = Some((
                    "Your save is being written; the app closes as soon as that finishes.".into(),
                    Instant::now(),
                ));
            } else if let Some(run) = &self.run {
                run.session.request_cancel();
            }
        }
        if self.close_when_idle && self.run.is_none() {
            ctx.send_viewport_cmd(egui::ViewportCommand::Close);
        }
    }

    /// Draw the current screen.
    pub fn show(&mut self, ui: &mut Ui) {
        let _ = self.ctx.set(ui.ctx().clone());
        self.poll();
        let mut actions = Vec::new();
        if self.running() || self.update.is_busy() {
            ui.ctx().request_repaint_after(Duration::from_millis(200));
        }

        Panel::bottom("footer").show(ui, |ui| self.footer(ui, &mut actions));
        if matches!(self.screen, Screen::Result)
            && let Some(view) = &self.result
        {
            Panel::bottom("result_actions").show(ui, |ui| {
                result::buttons(ui, view, &mut actions);
                ui.add_space(8.0);
            });
        }
        CentralPanel::default().show(ui, |ui| match self.screen {
            Screen::Setup => scroll_column(ui, |ui| setup::ui(ui, &mut self.setup, &mut actions)),
            Screen::Running => {
                if let Some(run) = &self.run {
                    let info = ProgressInfo {
                        view: &run.view,
                        preview: run.request.dry_run,
                        elapsed: run.started.elapsed(),
                        cancel_requested: run.session.cancel_requested(),
                        committing: run.view.committing() || run.session.commit_started(),
                    };
                    let mut log_open = self.log_open;
                    scroll_column(ui, |ui| {
                        progress::ui(ui, &info, &mut log_open, &mut actions)
                    });
                }
            }
            Screen::Result => {
                if let Some(view) = &mut self.result {
                    column(ui, |ui| result::ui(ui, view, &mut actions));
                }
            }
        });
        for action in actions {
            self.dispatch(action);
        }
    }

    fn footer(&self, ui: &mut Ui, actions: &mut Vec<Action>) {
        use crate::selfupdate::State;
        let idle = !self.running();
        ui.add_space(6.0);
        ui.horizontal(|ui| {
            ui.label(
                RichText::new(format!("FL Daily Edit {}", self.update.version()))
                    .small()
                    .color(theme::muted(ui)),
            );
            match self.update.state() {
                State::Idle | State::Checking { manual: false } => {}
                State::Checking { manual: true } => {
                    theme::spinner_chip(ui, "Checking for updates…")
                }
                State::UpToDate => {
                    ui.label(
                        RichText::new("You have the latest version.")
                            .small()
                            .color(theme::muted(ui)),
                    );
                }
                State::Available(available) => {
                    theme::chip(
                        ui,
                        theme::Tone::Info,
                        format!("Version {} is available", available.version),
                    );
                    if ui
                        .add_enabled(idle, egui::Button::new("Update and restart"))
                        .clicked()
                    {
                        actions.push(Action::InstallUpdate);
                    }
                }
                State::Downloading { done, total } => {
                    let percent = if *total > 0 { done * 100 / total } else { 0 };
                    theme::spinner_chip(ui, &format!("Downloading update… {percent}%"));
                }
                State::Restarting => theme::spinner_chip(ui, "Restarting…"),
                State::Failed(message) => theme::chip(ui, theme::Tone::Warn, message.clone()),
            }
            ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                if let Some((text, _)) = &self.notice {
                    ui.label(RichText::new(text).small().color(theme::muted(ui)));
                } else if idle
                    && !self.update.is_busy()
                    && ui.small_button("Check for updates").clicked()
                {
                    actions.push(Action::CheckForUpdates);
                }
            });
        });
        ui.add_space(4.0);
    }
}

/// Content centered in a column of readable width.
fn column(ui: &mut Ui, add: impl FnOnce(&mut Ui)) {
    const MAX_WIDTH: f32 = 760.0;
    let available = ui.available_rect_before_wrap();
    let width = available.width().min(MAX_WIDTH);
    let left = available.center().x - width / 2.0;
    let rect = egui::Rect::from_min_size(
        egui::pos2(left, available.top()),
        egui::vec2(width, available.height()),
    );
    ui.scope_builder(egui::UiBuilder::new().max_rect(rect), add);
}

fn scroll_column(ui: &mut Ui, add: impl FnOnce(&mut Ui)) {
    ScrollArea::vertical()
        .auto_shrink([false, false])
        .show(ui, |ui| column(ui, add));
}

impl eframe::App for App {
    fn logic(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        self.handle_close_request(ctx);
    }

    fn ui(&mut self, ui: &mut Ui, _frame: &mut eframe::Frame) {
        self.show(ui);
    }
}

/// Open the window and run until it closes.
pub fn run(services: Services) -> eframe::Result {
    let mut viewport = egui::ViewportBuilder::default()
        .with_title("FL Daily Edit")
        .with_inner_size([780.0, 780.0])
        .with_min_inner_size([640.0, 540.0]);
    if let Ok(icon) = eframe::icon_data::from_png_bytes(include_bytes!("../../assets/icon.png")) {
        viewport = viewport.with_icon(icon);
    }
    let options = eframe::NativeOptions {
        viewport,
        ..Default::default()
    };
    eframe::run_native(
        "FL Daily Edit",
        options,
        Box::new(move |cc| {
            theme::apply(&cc.egui_ctx);
            Ok(Box::new(App::new(services)))
        }),
    )
}
