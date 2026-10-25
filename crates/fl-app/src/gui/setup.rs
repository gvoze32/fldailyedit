//! Setup screen: choose the save (and, when the save needs it, the game
//! folder), then preview or update.

use std::path::{Path, PathBuf};

use eframe::egui::{self, RichText, TextEdit, Ui, vec2};
use fl_engine::api::{UpdateRequest, Window};
use fl_engine::locations::{self, SaveCandidate, SaveInfo};

use super::Action;
use super::theme::{self, Tone};

/// Result of checking the chosen save.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SaveStatus {
    Empty,
    Checking,
    Ready { resolved: PathBuf, info: SaveInfo },
    Invalid(String),
}

/// Result of checking the chosen game folder.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum GameStatus {
    Empty,
    Checking,
    Usable { archives: Vec<PathBuf> },
    Unusable,
}

/// Why the action buttons are disabled.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Blocker {
    ChooseSave,
    Checking,
    SaveInvalid(String),
    ChooseGameFolder,
    GameFolderInvalid,
}

impl Blocker {
    pub fn text(&self) -> String {
        match self {
            Self::ChooseSave => "Choose your save file to continue.".into(),
            Self::Checking => "Checking your choices…".into(),
            Self::SaveInvalid(reason) => reason.clone(),
            Self::ChooseGameFolder => {
                "Choose the game folder: this save needs the game's player database.".into()
            }
            Self::GameFolderInvalid => {
                "The game folder has no data_extra.cpk or data_s2526.cpk in its download folder."
                    .into()
            }
        }
    }
}

/// Everything the Setup screen shows and edits.
#[derive(Debug, Clone)]
pub struct SetupState {
    pub save_text: String,
    pub game_text: String,
    pub save_path: Option<PathBuf>,
    pub game_path: Option<PathBuf>,
    pub detected: Vec<SaveCandidate>,
    pub save: SaveStatus,
    pub game: GameStatus,
    /// Which probe's answer is still wanted; older answers are dropped.
    pub generation: u64,
    /// Scroll the game-folder card into view on the next frame.
    pub focus_game_folder: bool,
}

impl SetupState {
    pub fn new(save: Option<PathBuf>, game: Option<PathBuf>) -> Self {
        let text = |path: &Option<PathBuf>| {
            path.as_deref()
                .map(|path| path.display().to_string())
                .unwrap_or_default()
        };
        Self {
            save_text: text(&save),
            game_text: text(&game),
            save_path: save,
            game_path: game,
            detected: Vec::new(),
            save: SaveStatus::Empty,
            game: GameStatus::Empty,
            generation: 0,
            focus_game_folder: false,
        }
    }

    pub fn set_save(&mut self, path: Option<PathBuf>) {
        self.save_text = path
            .as_deref()
            .map(|p| p.display().to_string())
            .unwrap_or_default();
        self.save_path = path;
    }

    pub fn set_game(&mut self, path: Option<PathBuf>) {
        self.game_text = path
            .as_deref()
            .map(|p| p.display().to_string())
            .unwrap_or_default();
        self.game_path = path;
    }

    /// The save is a vanilla PES 2021 save, which cannot be updated without
    /// the game's own database files.
    pub fn needs_game_folder(&self) -> bool {
        matches!(&self.save, SaveStatus::Ready { info, .. } if info.kind.needs_game_folder())
    }

    pub fn blockers(&self) -> Vec<Blocker> {
        let mut blockers = Vec::new();
        match &self.save {
            SaveStatus::Empty => blockers.push(Blocker::ChooseSave),
            SaveStatus::Checking => blockers.push(Blocker::Checking),
            SaveStatus::Invalid(reason) => blockers.push(Blocker::SaveInvalid(reason.clone())),
            SaveStatus::Ready { .. } => {}
        }
        if self.needs_game_folder() {
            match &self.game {
                GameStatus::Empty => blockers.push(Blocker::ChooseGameFolder),
                GameStatus::Checking => blockers.push(Blocker::Checking),
                GameStatus::Unusable => blockers.push(Blocker::GameFolderInvalid),
                GameStatus::Usable { .. } => {}
            }
        }
        blockers.dedup();
        blockers
    }

    pub fn ready(&self) -> bool {
        self.blockers().is_empty()
    }

    /// The engine request for the current choices, or `None` while blocked.
    pub fn request(&self, dry_run: bool) -> Option<UpdateRequest> {
        if !self.ready() {
            return None;
        }
        let SaveStatus::Ready { resolved, .. } = &self.save else {
            return None;
        };
        let game_root = match (&self.game, &self.game_path) {
            (GameStatus::Usable { .. }, Some(path)) => Some(path.clone()),
            _ => None,
        };
        Some(UpdateRequest {
            edit_path: resolved.clone(),
            output_path: None,
            game_root,
            window: Window::Auto,
            club: None,
            threshold: None,
            allow_overflow_release: true,
            release_policy: None,
            dry_run,
        })
    }
}

/// Check the chosen paths. Reads the save (decrypting it in memory) and
/// looks at the game folder; never writes.
pub fn probe(save: Option<&Path>, game: Option<&Path>) -> (SaveStatus, GameStatus) {
    let game_status = match game {
        None => GameStatus::Empty,
        Some(path) => {
            let folder = locations::check_game_folder(path);
            if folder.is_usable() {
                GameStatus::Usable {
                    archives: folder.archives,
                }
            } else {
                GameStatus::Unusable
            }
        }
    };
    let save_status = match save {
        None => SaveStatus::Empty,
        Some(path) => match locations::resolve_save_file(path, game) {
            Err(error) => SaveStatus::Invalid(error.to_string()),
            Ok(resolved) => match locations::inspect_save(&resolved) {
                Err(error) => SaveStatus::Invalid(error.to_string()),
                Ok(info) if !info.problems.is_empty() => SaveStatus::Invalid(format!(
                    "This save failed the integrity check ({} problem{}): {}",
                    info.problems.len(),
                    if info.problems.len() == 1 { "" } else { "s" },
                    info.problems[0]
                )),
                Ok(info) => SaveStatus::Ready { resolved, info },
            },
        },
    };
    (save_status, game_status)
}

pub fn ui(ui: &mut Ui, state: &mut SetupState, actions: &mut Vec<Action>) {
    ui.add_space(6.0);
    ui.label(RichText::new("Update your save").heading().strong());
    ui.label(
        RichText::new(
            "Bring your Football Life 2026 or PES 2021 save up to date with real-world transfers.",
        )
        .color(theme::muted(ui)),
    );
    ui.add_space(10.0);
    theme::callout(ui, Tone::Warn, |ui| {
        ui.label(RichText::new("Close the game first").strong());
        ui.label("FL Daily Edit changes your save file directly. A backup is made before anything is written.");
    });
    ui.add_space(12.0);

    save_card(ui, state, actions);
    ui.add_space(12.0);
    game_card(ui, state, actions);
    ui.add_space(16.0);

    let blockers = state.blockers();
    let enabled = blockers.is_empty();
    ui.horizontal(|ui| {
        let preview = ui.add_enabled(enabled, theme::secondary_button("Preview changes"));
        if preview
            .on_hover_text("See what would change. Nothing is written.")
            .clicked()
        {
            actions.push(Action::Start { dry_run: true });
        }
        let update = ui.add_enabled(enabled, theme::primary_button(ui, "Update save"));
        if update.clicked() {
            actions.push(Action::Start { dry_run: false });
        }
    });
    if let Some(blocker) = blockers.first() {
        ui.add_space(4.0);
        let tone = match blocker {
            Blocker::Checking | Blocker::ChooseSave => Tone::Muted,
            _ => Tone::Error,
        };
        if matches!(blocker, Blocker::Checking) {
            theme::spinner_chip(ui, &blocker.text());
        } else if tone == Tone::Muted {
            ui.label(RichText::new(blocker.text()).color(theme::muted(ui)));
        } else {
            theme::chip(ui, tone, blocker.text());
        }
    }
}

fn save_card(ui: &mut Ui, state: &mut SetupState, actions: &mut Vec<Action>) {
    theme::card(ui, |ui| {
        theme::step_title(ui, 1, "Your save", None);
        ui.add_space(4.0);
        ui.label(RichText::new("The EDIT00000000 file the game loads.").color(theme::muted(ui)));

        if !state.detected.is_empty() {
            ui.add_space(2.0);
            ui.label(
                RichText::new("Found on this PC")
                    .small()
                    .color(theme::muted(ui)),
            );
            let selected = state.save_path.clone();
            for candidate in state.detected.clone() {
                let is_selected = selected.as_deref() == Some(candidate.edit_file.as_path());
                let label = format!(
                    "{}   {}",
                    candidate.family.game_name(),
                    candidate.edit_file.display()
                );
                if ui.radio(is_selected, label).clicked() && !is_selected {
                    actions.push(Action::SaveChosen(candidate.edit_file.clone()));
                }
            }
            ui.add_space(2.0);
        }

        ui.horizontal(|ui| {
            let width = (ui.available_width() - 110.0).max(120.0);
            let edit = ui.add(
                TextEdit::singleline(&mut state.save_text)
                    .desired_width(width)
                    .hint_text("Path to EDIT00000000")
                    .margin(vec2(8.0, 6.0)),
            );
            if edit.lost_focus() && edit.changed() {
                let text = state.save_text.trim().trim_matches('"');
                actions.push(if text.is_empty() {
                    Action::SaveCleared
                } else {
                    Action::SaveChosen(PathBuf::from(text))
                });
            }
            if ui
                .add(theme::secondary_button("Browse…").min_size(vec2(96.0, 30.0)))
                .clicked()
            {
                actions.push(Action::BrowseSave);
            }
        });
        ui.add_space(2.0);
        save_status(ui, state);
    });
}

fn save_status(ui: &mut Ui, state: &SetupState) {
    match &state.save {
        SaveStatus::Empty => {
            if state.detected.is_empty() {
                ui.label(
                    RichText::new("No save was found automatically. Use Browse to pick it.")
                        .color(theme::muted(ui)),
                );
            }
        }
        SaveStatus::Checking => theme::spinner_chip(ui, "Reading the save…"),
        SaveStatus::Invalid(reason) => theme::chip(ui, Tone::Error, reason.clone()),
        SaveStatus::Ready { info, .. } => theme::chip(
            ui,
            Tone::Ok,
            format!(
                "{} save · {} clubs · {} players",
                info.kind.game_name(),
                info.clubs,
                info.players
            ),
        ),
    }
}

fn game_card(ui: &mut Ui, state: &mut SetupState, actions: &mut Vec<Action>) {
    let needed = state.needs_game_folder();
    theme::card(ui, |ui| {
        if std::mem::take(&mut state.focus_game_folder) {
            ui.scroll_to_cursor(Some(egui::Align::Center));
        }
        let badge = match (&state.save, needed) {
            (SaveStatus::Ready { .. }, true) => Some(("Required for this save", Tone::Warn)),
            (SaveStatus::Ready { .. }, false) => Some(("Recommended", Tone::Info)),
            _ => None,
        };
        theme::step_title(ui, 2, "Game folder", badge);
        ui.add_space(4.0);
        let help = if needed {
            "Required for this save: it is matched against the game's own player database. Choose the folder that contains the download folder."
        } else {
            "Choose the folder that contains the download folder. The app then reads player details such as age and rating from your game, which makes matching more accurate."
        };
        ui.label(RichText::new(help).color(theme::muted(ui)));
        ui.add_space(2.0);
        ui.horizontal(|ui| {
            let width = (ui.available_width() - 110.0).max(120.0);
            let edit = ui.add(
                TextEdit::singleline(&mut state.game_text)
                    .desired_width(width)
                    .hint_text("e.g. D:\\Games\\PES 2021")
                    .margin(vec2(8.0, 6.0)),
            );
            if edit.lost_focus() && edit.changed() {
                let text = state.game_text.trim().trim_matches('"');
                actions.push(if text.is_empty() {
                    Action::GameCleared
                } else {
                    Action::GameChosen(PathBuf::from(text))
                });
            }
            if ui
                .add(theme::secondary_button("Browse…").min_size(vec2(96.0, 30.0)))
                .clicked()
            {
                actions.push(Action::BrowseGame);
            }
        });
        ui.add_space(2.0);
        match &state.game {
            GameStatus::Empty => {
                theme::chip(
                    ui,
                    if needed { Tone::Warn } else { Tone::Muted },
                    "Not chosen yet",
                );
            }
            GameStatus::Checking => theme::spinner_chip(ui, "Looking for the game files…"),
            GameStatus::Unusable => theme::chip(
                ui,
                Tone::Error,
                "No data_extra.cpk or data_s2526.cpk found in the download folder here.",
            ),
            GameStatus::Usable { archives } => {
                let first = archives
                    .first()
                    .and_then(|path| path.file_name())
                    .map(|name| name.to_string_lossy().into_owned())
                    .unwrap_or_default();
                theme::chip(ui, Tone::Ok, format!("Game files found ({first})"));
            }
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;
    use fl_engine::locations::SaveKind;

    fn ready(kind: SaveKind) -> SaveStatus {
        SaveStatus::Ready {
            resolved: "C:/save/EDIT00000000".into(),
            info: SaveInfo {
                kind,
                game_version: String::new(),
                teams: 10,
                clubs: 8,
                players: 100,
                problems: Vec::new(),
            },
        }
    }

    #[test]
    fn fl26_save_runs_without_a_game_folder_pes21_does_not() {
        let mut state = SetupState::new(None, None);
        assert_eq!(state.blockers(), [Blocker::ChooseSave]);
        assert!(state.request(false).is_none());

        state.save = ready(SaveKind::FootballLife26);
        assert!(state.ready());
        let request = state.request(true).unwrap();
        assert!(request.dry_run && request.game_root.is_none());
        assert!(
            request.output_path.is_none(),
            "the app always updates in place"
        );

        state.save = ready(SaveKind::Pes2021);
        assert_eq!(state.blockers(), [Blocker::ChooseGameFolder]);
        state.game = GameStatus::Unusable;
        assert_eq!(state.blockers(), [Blocker::GameFolderInvalid]);
        state.game_path = Some("D:/Games/PES".into());
        state.game = GameStatus::Usable {
            archives: vec!["D:/Games/PES/download/data_extra.cpk".into()],
        };
        assert_eq!(
            state.request(false).unwrap().game_root.as_deref(),
            Some(Path::new("D:/Games/PES"))
        );
    }

    #[test]
    fn unusable_optional_game_folder_is_ignored_for_fl26() {
        let mut state = SetupState::new(None, Some("D:/nowhere".into()));
        state.save = ready(SaveKind::FootballLife26);
        state.game = GameStatus::Unusable;
        assert!(state.ready());
        assert!(state.request(false).unwrap().game_root.is_none());
    }

    #[test]
    fn probe_reports_unreadable_saves_and_missing_files_without_writing() {
        let dir = tempfile::tempdir().unwrap();
        let garbage = dir.path().join("EDIT00000000");
        std::fs::write(&garbage, b"nope").unwrap();
        let (save, game) = probe(Some(&garbage), None);
        assert!(matches!(save, SaveStatus::Invalid(_)));
        assert_eq!(game, GameStatus::Empty);
        let (save, _) = probe(Some(&dir.path().join("missing")), None);
        assert!(matches!(save, SaveStatus::Invalid(text) if text.contains("does not exist")));
        assert_eq!(std::fs::read_dir(dir.path()).unwrap().count(), 1);
        assert_eq!(probe(None, None), (SaveStatus::Empty, GameStatus::Empty));
    }
}
