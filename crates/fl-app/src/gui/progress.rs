//! Progress screen: stage checklist, bar, elapsed time, collapsible log.

use std::time::Duration;

use eframe::egui::{self, Color32, CornerRadius, RichText, ScrollArea, Ui};
use fl_engine::api::{Progress, Stage};

use super::Action;
use super::copy::stage_label;
use super::theme::{self, Icon, Tone};
use crate::report::format_elapsed;

const MAX_LOG_LINES: usize = 5_000;

/// How far a stage has got.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RowState {
    Pending,
    Active,
    Done,
}

/// What the engine has told us so far.
#[derive(Debug, Clone, Default)]
pub struct RunView {
    pub stage: Option<Stage>,
    pub current: u32,
    pub total: u32,
    pub message: String,
    pub log: Vec<String>,
    pub dropped_log_lines: usize,
}

impl RunView {
    pub fn apply(&mut self, progress: &Progress) {
        // Stages only move forward; a late event from an earlier one is just a log line.
        if self.stage.is_none_or(|stage| progress.stage >= stage) {
            if self.stage != Some(progress.stage) {
                self.current = 0;
                self.total = 0;
            }
            self.stage = Some(progress.stage);
            self.current = progress.current;
            self.total = progress.total;
        }
        if !progress.message.is_empty() && progress.message != self.message {
            self.message.clone_from(&progress.message);
            self.log.push(progress.message.clone());
            if self.log.len() > MAX_LOG_LINES {
                let excess = self.log.len() - MAX_LOG_LINES;
                self.log.drain(..excess);
                self.dropped_log_lines += excess;
            }
        }
    }

    pub fn row(&self, stage: Stage) -> RowState {
        match self.stage {
            None => RowState::Pending,
            Some(Stage::Complete) => RowState::Done,
            Some(current) if stage < current => RowState::Done,
            Some(current) if stage == current => RowState::Active,
            Some(_) => RowState::Pending,
        }
    }

    /// The engine is writing the save; nothing can be cancelled any more.
    pub fn committing(&self) -> bool {
        self.stage.is_some_and(|stage| stage >= Stage::Committing)
    }

    /// Overall progress in `0.0..=1.0`: finished stages plus the known
    /// fraction of the current one. Never moves backwards within a run.
    pub fn fraction(&self) -> f32 {
        let steps = (Stage::ALL.len() - 1) as f32;
        let Some(stage) = self.stage else {
            return 0.0;
        };
        let index = Stage::ALL.iter().position(|s| *s == stage).unwrap_or(0) as f32;
        if stage == Stage::Complete {
            return 1.0;
        }
        let inner = if self.total > 0 {
            (self.current as f32 / self.total as f32).clamp(0.0, 1.0)
        } else {
            0.0
        };
        ((index + inner) / steps).clamp(0.0, 1.0)
    }
}

/// What the Progress screen needs besides the engine's events.
pub struct ProgressInfo<'a> {
    pub view: &'a RunView,
    pub preview: bool,
    pub elapsed: Duration,
    pub cancel_requested: bool,
    pub committing: bool,
}

pub fn ui(ui: &mut Ui, info: &ProgressInfo<'_>, log_open: &mut bool, actions: &mut Vec<Action>) {
    ui.add_space(6.0);
    ui.label(
        RichText::new(if info.preview {
            "Previewing changes"
        } else {
            "Updating your save"
        })
        .heading()
        .strong(),
    );
    ui.label(
        RichText::new(if info.preview {
            "Working out what would change. Nothing is written."
        } else {
            "This can take a few minutes while transfers are collected. Keep this window open."
        })
        .color(theme::muted(ui)),
    );
    ui.add_space(12.0);

    theme::card(ui, |ui| {
        let view = info.view;
        for stage in Stage::ALL {
            let state = view.row(stage);
            ui.horizontal(|ui| {
                match state {
                    RowState::Done => {
                        let color = theme::tone_color(ui, Tone::Ok);
                        theme::icon(ui, Icon::Check, color, 18.0);
                    }
                    RowState::Active => {
                        ui.add(egui::Spinner::new().size(18.0).color(theme::accent(ui)));
                    }
                    RowState::Pending => {
                        let color = theme::muted(ui).gamma_multiply(0.6);
                        theme::icon(ui, Icon::Ring, color, 18.0);
                    }
                }
                let text = RichText::new(stage_label(stage));
                ui.label(match state {
                    RowState::Active => text.strong(),
                    RowState::Pending => text.color(theme::muted(ui)),
                    RowState::Done => text,
                });
                if state == RowState::Active && view.total > 0 {
                    ui.label(
                        RichText::new(format!("{} of {}", view.current, view.total))
                            .color(theme::muted(ui)),
                    );
                }
            });
        }
        ui.add_space(8.0);
        ui.add(
            egui::ProgressBar::new(view.fraction())
                .desired_height(8.0)
                .corner_radius(CornerRadius::same(4))
                .fill(theme::accent(ui)),
        );
        ui.add_space(4.0);
        ui.horizontal(|ui| {
            let line = if view.message.is_empty() {
                "Starting…"
            } else {
                view.message.as_str()
            };
            ui.add(egui::Label::new(RichText::new(line).color(theme::muted(ui))).truncate());
            ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                ui.label(
                    RichText::new(format!("Elapsed {}", format_elapsed(info.elapsed)))
                        .color(theme::muted(ui)),
                );
            });
        });
    });

    ui.add_space(10.0);
    let header = egui::CollapsingHeader::new(RichText::new("Log").strong())
        .open(Some(*log_open))
        .show(ui, |ui| log_pane(ui, info.view));
    if header.header_response.clicked() {
        *log_open = !*log_open;
        actions.push(Action::LogToggled(*log_open));
    }

    ui.add_space(12.0);
    ui.horizontal(|ui| {
        if info.committing {
            ui.add(egui::Spinner::new().size(18.0));
            ui.add_enabled(
                false,
                theme::secondary_button("Finishing safely…").min_size(egui::vec2(170.0, 36.0)),
            );
            ui.label(
                RichText::new("Your save is being written. Don't close the app.")
                    .color(theme::tone_color(ui, Tone::Warn)),
            );
        } else if info.cancel_requested {
            ui.add(egui::Spinner::new().size(18.0));
            ui.add_enabled(false, theme::secondary_button("Cancelling…"));
            ui.label(
                RichText::new("Stopping at the next safe point. Your save is untouched.")
                    .color(theme::muted(ui)),
            );
        } else if ui.add(theme::secondary_button("Cancel")).clicked() {
            actions.push(Action::Cancel);
        }
    });
}

fn log_pane(ui: &mut Ui, view: &RunView) {
    let fill = if ui.visuals().dark_mode {
        Color32::from_rgb(18, 20, 22)
    } else {
        Color32::from_rgb(250, 250, 251)
    };
    egui::Frame::new()
        .fill(fill)
        .corner_radius(CornerRadius::same(6))
        .inner_margin(egui::Margin::same(8))
        .show(ui, |ui| {
            ui.set_width(ui.available_width());
            ScrollArea::vertical()
                .max_height(150.0)
                .auto_shrink([false, true])
                .stick_to_bottom(true)
                .show(ui, |ui| {
                    if view.log.is_empty() {
                        ui.label(RichText::new("Nothing yet.").color(theme::muted(ui)));
                    }
                    if view.dropped_log_lines > 0 {
                        ui.label(
                            RichText::new(format!(
                                "… {} earlier lines not shown",
                                view.dropped_log_lines
                            ))
                            .color(theme::muted(ui)),
                        );
                    }
                    for line in &view.log {
                        ui.label(RichText::new(line).monospace().size(12.5));
                    }
                });
        });
}

#[cfg(test)]
mod tests {
    use super::*;

    fn event(stage: Stage, message: &str, current: u32, total: u32) -> Progress {
        Progress {
            stage,
            message: message.into(),
            current,
            total,
        }
    }

    #[test]
    fn rows_advance_and_fraction_never_moves_backwards() {
        let mut view = RunView::default();
        assert_eq!(view.row(Stage::Validating), RowState::Pending);
        assert_eq!(view.fraction(), 0.0);

        let mut last = 0.0;
        for step in [
            event(Stage::Validating, "Reading save", 0, 0),
            event(Stage::Fetching, "Chelsea", 1, 4),
            event(Stage::Fetching, "Arsenal", 3, 4),
            event(Stage::Matching, "Matching", 0, 0),
            event(Stage::Complete, "Done", 0, 0),
        ] {
            view.apply(&step);
            assert!(view.fraction() >= last, "{step:?}");
            last = view.fraction();
        }
        assert_eq!(view.fraction(), 1.0);
        assert!(Stage::ALL.iter().all(|s| view.row(*s) == RowState::Done));
    }

    #[test]
    fn row_states_follow_the_current_stage() {
        let mut view = RunView::default();
        view.apply(&event(Stage::Fetching, "x", 2, 8));
        assert_eq!(view.row(Stage::Validating), RowState::Done);
        assert_eq!(view.row(Stage::Fetching), RowState::Active);
        assert_eq!(view.row(Stage::Matching), RowState::Pending);
        assert!((view.fraction() - (1.0 + 0.25) / 6.0).abs() < 1e-6);
        assert!(!view.committing());
        view.apply(&event(Stage::Committing, "Writing", 0, 0));
        assert!(view.committing());
    }

    #[test]
    fn late_events_from_earlier_stages_do_not_rewind_and_log_dedups() {
        let mut view = RunView::default();
        view.apply(&event(Stage::Matching, "Matching", 0, 0));
        view.apply(&event(Stage::Fetching, "late", 9, 9));
        assert_eq!(view.stage, Some(Stage::Matching));
        assert_eq!(view.log, ["Matching", "late"]);
        view.apply(&event(Stage::Matching, "late", 0, 0));
        assert_eq!(view.log.len(), 2);
    }

    #[test]
    fn log_is_bounded() {
        let mut view = RunView::default();
        for i in 0..(MAX_LOG_LINES + 10) {
            view.apply(&event(Stage::Fetching, &format!("line {i}"), 0, 0));
        }
        assert_eq!(view.log.len(), MAX_LOG_LINES);
        assert_eq!(view.dropped_log_lines, 10);
        assert_eq!(
            view.log.last().unwrap(),
            &format!("line {}", MAX_LOG_LINES + 9)
        );
    }
}
