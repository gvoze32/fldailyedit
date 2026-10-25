//! Result screen: headline, counts and a one-screen summary. The full report
//! lives in the report files; nothing here scrolls.

use std::path::{Path, PathBuf};
use std::time::Duration;

use eframe::egui::{self, RichText, Ui, vec2};
use fl_engine::api::{ErrorCode, Outcome, Stage, UpdateError, UpdateRequest, UpdateResult};

use super::Action;
use super::copy::{ErrorCopy, error_copy, stage_label};
use super::theme::{self, Tone};
use crate::report::{format_elapsed, group_skipped, reason_label};

/// Not-applied reasons listed by name; the rest fold into one line so the
/// summary always fits the window.
const MAX_REASONS: usize = 5;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    Updated,
    NoChanges,
    Preview,
    Cancelled,
    Failed,
}

struct Group {
    reason: String,
    total: usize,
    relevant: usize,
}

pub struct ResultView {
    pub kind: Kind,
    pub request: UpdateRequest,
    pub outcome: Result<UpdateResult, UpdateError>,
    /// The last stage the engine reported before finishing.
    pub last_stage: Option<Stage>,
    pub elapsed: Duration,
    copy: Option<ErrorCopy>,
    groups: Vec<Group>,
}

impl ResultView {
    pub fn new(
        request: UpdateRequest,
        outcome: Result<UpdateResult, UpdateError>,
        last_stage: Option<Stage>,
        elapsed: Duration,
    ) -> Self {
        let kind = match &outcome {
            Ok(result) => match result.outcome {
                Outcome::Updated => Kind::Updated,
                Outcome::NoChanges => Kind::NoChanges,
                Outcome::Preview => Kind::Preview,
            },
            Err(error) if error.code == ErrorCode::Cancelled => Kind::Cancelled,
            Err(_) => Kind::Failed,
        };
        let copy = outcome
            .as_ref()
            .err()
            .map(|error| error_copy(error.code, error.stage.or(last_stage)));

        let groups = outcome.as_ref().map_or_else(
            |_| Vec::new(),
            |result| {
                group_skipped(&result.skipped)
                    .into_iter()
                    .map(|group| Group {
                        reason: group.reason.to_owned(),
                        total: group.items.len(),
                        relevant: group.relevant,
                    })
                    .collect()
            },
        );
        Self {
            kind,
            request,
            outcome,
            last_stage,
            elapsed,
            copy,
            groups,
        }
    }

    pub fn result(&self) -> Option<&UpdateResult> {
        self.outcome.as_ref().ok()
    }

    pub fn error(&self) -> Option<&UpdateError> {
        self.outcome.as_ref().err()
    }

    pub fn error_copy(&self) -> Option<&ErrorCopy> {
        self.copy.as_ref()
    }

    pub fn not_applied(&self) -> usize {
        self.result().map_or(0, |result| result.skipped.len())
    }

    pub fn headline(&self) -> String {
        match (&self.kind, &self.copy) {
            (Kind::Updated, _) => "Your save is updated".into(),
            (Kind::NoChanges, _) => "No changes needed".into(),
            (Kind::Preview, _) => "Preview ready".into(),
            (_, Some(copy)) => copy.title.into(),
            (_, None) => "Update failed".into(),
        }
    }

    pub fn subline(&self) -> String {
        let transfers = self.result().map_or(0, |result| result.counts.transfers);
        match (&self.kind, &self.copy) {
            (Kind::Updated, _) => format!(
                "{transfers} transfer{} applied. A backup of your original save was made first.",
                if transfers == 1 { "" } else { "s" }
            ),
            (Kind::NoChanges, _) => "Your save is already up to date. Nothing was written.".into(),
            (Kind::Preview, _) => {
                "This is what would change. Nothing was written to your save.".into()
            }
            (_, Some(copy)) => copy.explanation.into(),
            (_, None) => String::new(),
        }
    }

    pub fn tone(&self) -> Tone {
        match self.kind {
            Kind::Updated => Tone::Ok,
            Kind::NoChanges | Kind::Preview => Tone::Info,
            Kind::Cancelled => Tone::Muted,
            Kind::Failed => Tone::Error,
        }
    }

    /// The report file worth opening, HTML first.
    pub fn report_file(&self) -> Option<&Path> {
        let paths = &self.result()?.report_paths;
        paths
            .iter()
            .find(|path| {
                path.extension()
                    .is_some_and(|ext| ext.eq_ignore_ascii_case("html"))
            })
            .or_else(|| paths.first())
            .map(PathBuf::as_path)
    }

    pub fn backup(&self) -> Option<&Path> {
        self.result()?.backup_path.as_deref()
    }

    /// Where to look at the saved file: the written save, else the input.
    pub fn save_file(&self) -> &Path {
        self.result()
            .map(|result| result.target_path.as_path())
            .filter(|path| !path.as_os_str().is_empty())
            .unwrap_or(&self.request.edit_path)
    }

    /// Plain text for support: everything needed to understand the run.
    pub fn diagnostics(&self, version: &str) -> String {
        let mut lines = vec![
            format!("FL Daily Edit {version}"),
            format!("OS: {} {}", std::env::consts::OS, std::env::consts::ARCH),
            format!(
                "Run: {}",
                if self.request.dry_run {
                    "Preview (dry run)"
                } else {
                    "Update"
                }
            ),
            format!("Elapsed: {}", format_elapsed(self.elapsed)),
            format!("Save: {}", self.request.edit_path.display()),
            format!(
                "Game folder: {}",
                self.request
                    .game_root
                    .as_deref()
                    .map_or_else(|| "(none)".to_owned(), |path| path.display().to_string())
            ),
            format!(
                "Last stage: {}",
                self.last_stage.map_or("(none)", |stage| stage_label(stage))
            ),
        ];
        match &self.outcome {
            Ok(result) => {
                lines.push(format!("Outcome: {:?}", result.outcome));
                let c = &result.counts;
                lines.push(format!(
                    "Counts: transfers={} shirt_numbers={} captains={} formations={} tactics={} unchanged={} safety_skipped={} not_applied={}",
                    c.transfers, c.shirt_numbers, c.captains, c.formations, c.tactics, c.unchanged, c.safety_skipped, result.skipped.len()
                ));
                lines.push(format!("Target: {}", result.target_path.display()));
                if let Some(backup) = &result.backup_path {
                    lines.push(format!("Backup: {}", backup.display()));
                }
                if let Some(hash) = &result.written_sha256 {
                    lines.push(format!("Written SHA-256: {hash}"));
                }
                for path in &result.report_paths {
                    lines.push(format!("Report: {}", path.display()));
                }
                for warning in &result.warnings {
                    lines.push(format!("Warning: {warning}"));
                }
            }
            Err(error) => {
                lines.push(format!("Error code: {:?}", error.code));
                lines.push(format!(
                    "Error stage: {}",
                    error.stage.map_or("(unknown)", |stage| stage_label(stage))
                ));
                lines.push(format!("Error message: {}", error.message));
            }
        }
        lines.join("\n")
    }
}

pub fn ui(ui: &mut Ui, view: &ResultView, actions: &mut Vec<Action>) {
    ui.add_space(6.0);
    headline(ui, view);

    if let Some(result) = view.result() {
        for warning in &result.warnings {
            ui.add_space(6.0);
            theme::callout(ui, Tone::Warn, |ui| {
                ui.label(warning);
            });
        }
        ui.add_space(10.0);
        counts(ui, view);
        ui.add_space(12.0);
        ui.columns(2, |columns| {
            not_applied(&mut columns[0], view);
            summary(&mut columns[1], view, result);
        });
    } else {
        ui.add_space(10.0);
        failure(ui, view, actions);
    }
}

fn headline(ui: &mut Ui, view: &ResultView) {
    let tone = view.tone();
    ui.horizontal(|ui| {
        let color = theme::tone_color(ui, tone);
        let icon = match tone {
            Tone::Ok => theme::Icon::Check,
            Tone::Error => theme::Icon::Cross,
            Tone::Info => theme::Icon::Info,
            _ => theme::Icon::Ring,
        };
        theme::icon(ui, icon, color, 34.0);
        ui.vertical(|ui| {
            ui.label(RichText::new(view.headline()).heading().strong());
            ui.label(RichText::new(view.subline()).color(theme::muted(ui)));
        });
    });
}

fn counts(ui: &mut Ui, view: &ResultView) {
    let Some(result) = view.result() else { return };
    let c = &result.counts;
    let tiles = [
        ("Transfers", c.transfers),
        ("Shirt numbers", c.shirt_numbers),
        ("Captains", c.captains),
        ("Formations", c.formations),
        ("Tactics", c.tactics),
        ("Not applied", view.not_applied() as u32),
    ];
    ui.columns(tiles.len(), |columns| {
        for (column, (label, value)) in columns.iter_mut().zip(tiles) {
            theme::card(column, |ui| {
                ui.vertical_centered(|ui| {
                    ui.label(RichText::new(value.to_string()).size(24.0).strong());
                    ui.label(RichText::new(label).small().color(theme::muted(ui)));
                });
            });
        }
    });
}

/// Text-height rows: the summary cards must fit the window without scrolling.
fn compact(ui: &mut Ui) {
    let spacing = ui.spacing_mut();
    spacing.item_spacing.y = 6.0;
    spacing.interact_size.y = 0.0;
}

/// Not-applied transfers by reason; the transfers themselves are in the report.
fn not_applied(ui: &mut Ui, view: &ResultView) {
    theme::card(ui, |ui| {
        ui.set_width(ui.available_width());
        compact(ui);
        ui.label(RichText::new(format!("Not applied ({})", view.not_applied())).strong());
        if view.groups.is_empty() {
            ui.label(RichText::new("Every transfer found was applied.").color(theme::muted(ui)));
            return;
        }
        let shown = view.groups.len().min(MAX_REASONS);
        for group in &view.groups[..shown] {
            ui.horizontal(|ui| {
                ui.label(format!("{} ({})", reason_label(&group.reason), group.total));
                if group.relevant > 0 {
                    ui.label(
                        RichText::new(format!("{} touch your save", group.relevant))
                            .color(theme::tone_color(ui, Tone::Warn)),
                    );
                }
            });
        }
        let rest = &view.groups[shown..];
        if !rest.is_empty() {
            let transfers: usize = rest.iter().map(|group| group.total).sum();
            ui.label(
                RichText::new(format!(
                    "+{} more reason{} ({transfers} transfer{})",
                    rest.len(),
                    if rest.len() == 1 { "" } else { "s" },
                    if transfers == 1 { "" } else { "s" },
                ))
                .color(theme::muted(ui)),
            );
        }
    });
}

/// One-line facts about the run and where the full report is.
fn summary(ui: &mut Ui, view: &ResultView, result: &UpdateResult) {
    theme::card(ui, |ui| {
        ui.set_width(ui.available_width());
        compact(ui);
        egui::Grid::new("summary")
            .num_columns(2)
            .spacing(vec2(12.0, 6.0))
            .show(ui, |ui| {
                let row = |ui: &mut Ui, key: &str, value: String| {
                    ui.label(RichText::new(key).color(theme::muted(ui)));
                    ui.add(egui::Label::new(value).truncate());
                    ui.end_row();
                };
                row(ui, "Time taken", format_elapsed(view.elapsed));
                row(
                    ui,
                    "Already up to date",
                    result.counts.unchanged.to_string(),
                );
                row(
                    ui,
                    "Skipped for safety",
                    result.counts.safety_skipped.to_string(),
                );
                // File names only; the buttons below open them and
                // "Copy diagnostics" carries the full paths.
                let file_name = |path: &Path| {
                    path.file_name().map_or_else(
                        || path.display().to_string(),
                        |name| name.to_string_lossy().into_owned(),
                    )
                };
                if let Some(backup) = view.backup() {
                    row(ui, "Backup", file_name(backup));
                }
                let report = match (view.report_file(), view.kind) {
                    (Some(path), _) => file_name(path),
                    (None, Kind::Preview) => "Not written for a preview".to_owned(),
                    (None, _) => "Not written".to_owned(),
                };
                row(ui, "Full report", report);
            });
    });
}

fn details_tab(ui: &mut Ui, view: &ResultView) {
    egui::Grid::new("details")
        .num_columns(2)
        .spacing(vec2(16.0, 6.0))
        .show(ui, |ui| {
            let row = |ui: &mut Ui, key: &str, value: String| {
                ui.label(RichText::new(key).color(theme::muted(ui)));
                ui.add(egui::Label::new(value).wrap());
                ui.end_row();
            };
            row(
                ui,
                "Run",
                if view.request.dry_run {
                    "Preview".into()
                } else {
                    "Update".into()
                },
            );
            row(ui, "Time taken", format_elapsed(view.elapsed));
            row(ui, "Save", view.save_file().display().to_string());
            if let Some(result) = view.result() {
                if let Some(backup) = &result.backup_path {
                    row(ui, "Backup", backup.display().to_string());
                }
                if let Some(hash) = &result.written_sha256 {
                    row(ui, "SHA-256", hash.clone());
                }
                for path in &result.report_paths {
                    row(ui, "Report file", path.display().to_string());
                }
                let c = &result.counts;
                row(ui, "Already up to date", c.unchanged.to_string());
                row(ui, "Skipped for safety", c.safety_skipped.to_string());
            }
            if let Some(error) = view.error() {
                row(ui, "Error code", format!("{:?}", error.code));
                if let Some(stage) = error.stage.or(view.last_stage) {
                    row(ui, "Stopped during", stage_label(stage).into());
                }
                row(ui, "Message", error.message.clone());
            }
        });
}

fn failure(ui: &mut Ui, view: &ResultView, actions: &mut Vec<Action>) {
    let Some(copy) = view.error_copy() else {
        return;
    };
    if view.kind == Kind::Cancelled {
        return;
    }
    theme::card(ui, |ui| {
        if copy.save_untouched {
            theme::chip(ui, Tone::Ok, "Your save was not changed.");
        } else {
            theme::chip(
                ui,
                Tone::Warn,
                "Your save may have been partly written. Restore the backup if it looks wrong.",
            );
        }
        if !copy.next_steps.is_empty() {
            ui.add_space(8.0);
            ui.label(RichText::new("What to do").strong());
            for step in copy.next_steps {
                ui.horizontal_top(|ui| {
                    ui.label("•");
                    ui.add(egui::Label::new(*step).wrap());
                });
            }
        }
        if copy.needs_game_folder {
            ui.add_space(8.0);
            if ui
                .add(theme::primary_button(ui, "Choose game folder"))
                .clicked()
            {
                actions.push(Action::ChooseGameFolderAfterFailure);
            }
        }
    });
    ui.add_space(10.0);
    egui::CollapsingHeader::new(RichText::new("Technical details").strong())
        .default_open(false)
        .show(ui, |ui| details_tab(ui, view));
}

/// Bottom button row for the result screen.
pub fn buttons(ui: &mut Ui, view: &ResultView, actions: &mut Vec<Action>) {
    ui.add_space(8.0);
    ui.horizontal_wrapped(|ui| {
        match view.kind {
            Kind::Preview => {
                if ui.add(theme::primary_button(ui, "Update now")).clicked() {
                    actions.push(Action::UpdateAfterPreview);
                }
                if ui.add(theme::secondary_button("Back")).clicked() {
                    actions.push(Action::BackToSetup);
                }
            }
            Kind::Failed if view.error_copy().is_some_and(|copy| copy.needs_game_folder) => {
                if ui.add(theme::secondary_button("Back")).clicked() {
                    actions.push(Action::BackToSetup);
                }
            }
            Kind::Failed | Kind::Cancelled => {
                if ui.add(theme::primary_button(ui, "Try again")).clicked() {
                    actions.push(Action::BackToSetup);
                }
            }
            Kind::Updated | Kind::NoChanges => {
                if ui.add(theme::primary_button(ui, "Update again")).clicked() {
                    actions.push(Action::BackToSetup);
                }
            }
        }
        if matches!(view.kind, Kind::Updated | Kind::NoChanges | Kind::Preview)
            && ui
                .add(theme::secondary_button("Open save folder"))
                .clicked()
        {
            actions.push(Action::OpenSaveFolder);
        }
        if view.backup().is_some() && ui.add(theme::secondary_button("Open backup")).clicked() {
            actions.push(Action::OpenBackup);
        }
        if view.report_file().is_some() && ui.add(theme::secondary_button("Open report")).clicked()
        {
            actions.push(Action::OpenReport);
        }
        if ui
            .add(theme::secondary_button("Copy diagnostics"))
            .clicked()
        {
            actions.push(Action::CopyDiagnostics);
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;
    use fl_engine::api::{ChangeCounts, SkippedTransfer};

    fn request() -> UpdateRequest {
        UpdateRequest {
            edit_path: "C:/save/EDIT00000000".into(),
            output_path: None,
            game_root: Some("D:/Games/PES".into()),
            window: fl_engine::api::Window::Auto,
            club: None,
            threshold: None,
            allow_overflow_release: true,
            release_policy: None,
            dry_run: false,
        }
    }

    fn skipped(player: &str, reason: &str, relevant: bool) -> SkippedTransfer {
        SkippedTransfer {
            player: player.into(),
            from_club: "A".into(),
            to_club: "B".into(),
            date: None,
            transfer_type: "transfer".into(),
            reason: reason.into(),
            detail: String::new(),
            relevant,
        }
    }

    #[test]
    fn classifies_outcomes() {
        let ok = |outcome| {
            ResultView::new(
                request(),
                Ok(UpdateResult {
                    outcome,
                    ..UpdateResult::default()
                }),
                None,
                Duration::ZERO,
            )
        };
        assert_eq!(ok(Outcome::Updated).kind, Kind::Updated);
        assert_eq!(ok(Outcome::NoChanges).headline(), "No changes needed");
        assert_eq!(ok(Outcome::Preview).headline(), "Preview ready");

        let err = |code| {
            ResultView::new(
                request(),
                Err(UpdateError {
                    code,
                    stage: Some(Stage::Fetching),
                    message: "m".into(),
                }),
                Some(Stage::Fetching),
                Duration::from_secs(61),
            )
        };
        assert_eq!(err(ErrorCode::Cancelled).kind, Kind::Cancelled);
        let failed = err(ErrorCode::NativeDatabaseMissing);
        assert_eq!(failed.kind, Kind::Failed);
        assert_eq!(failed.headline(), "Game folder needed");
        assert!(failed.error_copy().unwrap().needs_game_folder);
    }

    #[test]
    fn not_applied_groups_put_relevant_reasons_first() {
        let result = UpdateResult {
            outcome: Outcome::Updated,
            counts: ChangeCounts::default(),
            skipped: vec![
                skipped("a", "not_in_save", false),
                skipped("b", "roster_full", true),
                skipped("c", "not_in_save", false),
            ],
            ..UpdateResult::default()
        };
        let view = ResultView::new(request(), Ok(result), None, Duration::ZERO);
        assert_eq!(view.not_applied(), 3);
        let groups: Vec<_> = view
            .groups
            .iter()
            .map(|group| (group.reason.as_str(), group.total, group.relevant))
            .collect();
        assert_eq!(groups, [("roster_full", 1, 1), ("not_in_save", 2, 0)]);
    }

    #[test]
    fn diagnostics_carry_version_os_stage_error_and_paths() {
        let view = ResultView::new(
            request(),
            Err(UpdateError {
                code: ErrorCode::FetchFailed,
                stage: Some(Stage::Fetching),
                message: "timeout talking to host".into(),
            }),
            Some(Stage::Fetching),
            Duration::from_secs(5),
        );
        let text = view.diagnostics("1.2.3");
        for expected in [
            "FL Daily Edit 1.2.3",
            std::env::consts::OS,
            "Last stage: Fetching transfers",
            "Error code: FetchFailed",
            "timeout talking to host",
            "EDIT00000000",
            "Game folder: D:/Games/PES",
        ] {
            assert!(text.contains(expected), "{expected} missing in\n{text}");
        }
    }

    #[test]
    fn report_file_prefers_html() {
        let result = UpdateResult {
            outcome: Outcome::Updated,
            report_paths: vec!["r/summary.md".into(), "r/summary.html".into()],
            ..UpdateResult::default()
        };
        let view = ResultView::new(request(), Ok(result), None, Duration::ZERO);
        assert_eq!(view.report_file(), Some(Path::new("r/summary.html")));
    }
}
