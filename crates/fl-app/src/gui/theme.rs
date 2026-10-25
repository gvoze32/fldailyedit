//! Flat look for both system themes, plus the few widgets the screens share.
//! Icons are drawn with the painter so they never depend on font coverage.

use eframe::egui::{
    self, Align2, Button, Color32, CornerRadius, FontFamily, FontId, Frame, Margin, Pos2, Rect,
    RichText, Sense, Stroke, StrokeKind, Theme, Ui, Vec2, vec2,
};

pub const ACCENT_LIGHT: Color32 = Color32::from_rgb(0x12, 0x7A, 0x5C);
pub const ACCENT_DARK: Color32 = Color32::from_rgb(0x2E, 0xB8, 0x7E);
const RADIUS: u8 = 8;

/// Tone of a status chip or icon.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Tone {
    Ok,
    Warn,
    Error,
    Info,
    Muted,
}

pub fn apply(ctx: &egui::Context) {
    install_fallback_fonts(ctx);
    for theme in [Theme::Light, Theme::Dark] {
        ctx.style_mut_of(theme, |style| {
            let dark = theme == Theme::Dark;
            let accent = accent_of(dark);
            let v = &mut style.visuals;
            v.panel_fill = if dark {
                rgb(24, 26, 29)
            } else {
                rgb(244, 245, 247)
            };
            v.window_fill = if dark {
                rgb(33, 36, 40)
            } else {
                Color32::WHITE
            };
            v.extreme_bg_color = if dark {
                rgb(18, 20, 22)
            } else {
                Color32::WHITE
            };
            v.faint_bg_color = if dark {
                rgb(30, 33, 37)
            } else {
                rgb(238, 240, 243)
            };
            v.window_stroke = Stroke::new(1.0, border_of(dark));
            v.selection.bg_fill = accent.gamma_multiply(if dark { 0.55 } else { 0.9 });
            v.selection.stroke = Stroke::new(1.0, accent);
            v.hyperlink_color = accent;
            let radius = CornerRadius::same(RADIUS - 2);
            for widget in [
                &mut v.widgets.noninteractive,
                &mut v.widgets.inactive,
                &mut v.widgets.hovered,
                &mut v.widgets.active,
                &mut v.widgets.open,
            ] {
                widget.corner_radius = radius;
            }
            v.widgets.noninteractive.bg_stroke = Stroke::new(1.0, border_of(dark));
            v.widgets.inactive.weak_bg_fill = if dark {
                rgb(44, 48, 54)
            } else {
                rgb(232, 235, 239)
            };
            v.widgets.inactive.bg_fill = v.widgets.inactive.weak_bg_fill;
            v.widgets.inactive.bg_stroke = Stroke::new(1.0, border_of(dark));
            v.widgets.hovered.weak_bg_fill = if dark {
                rgb(56, 61, 68)
            } else {
                rgb(220, 224, 229)
            };
            v.widgets.hovered.bg_stroke = Stroke::NONE;
            v.widgets.active.weak_bg_fill = if dark {
                rgb(66, 72, 80)
            } else {
                rgb(208, 213, 219)
            };
            v.window_corner_radius = CornerRadius::same(RADIUS + 2);
            v.menu_corner_radius = radius;

            style.spacing.item_spacing = vec2(10.0, 8.0);
            style.spacing.button_padding = vec2(14.0, 7.0);
            style.spacing.interact_size.y = 30.0;
            style.spacing.scroll.bar_width = 8.0;
            for (text_style, size) in [
                (egui::TextStyle::Heading, 24.0),
                (egui::TextStyle::Body, 14.5),
                (egui::TextStyle::Button, 14.5),
                (egui::TextStyle::Monospace, 13.0),
                (egui::TextStyle::Small, 12.0),
            ] {
                let family = if text_style == egui::TextStyle::Monospace {
                    FontFamily::Monospace
                } else {
                    FontFamily::Proportional
                };
                style
                    .text_styles
                    .insert(text_style, FontId::new(size, family));
            }
        });
    }
}

/// Player and club names come from the internet. egui's bundled font covers
/// Latin, Greek and Cyrillic; on Windows add the system fonts for CJK,
/// Korean and extra symbols so such names render instead of boxes.
fn install_fallback_fonts(ctx: &egui::Context) {
    let Some(windir) = std::env::var_os("WINDIR") else {
        return;
    };
    let fonts_dir = std::path::Path::new(&windir).join("Fonts");
    let candidates: [(&str, &str); 5] = [
        ("segoeui.ttf", "system-segoe-ui"),
        ("seguisym.ttf", "system-segoe-symbols"),
        ("YuGothM.ttc", "system-yu-gothic"),
        ("msyh.ttc", "system-ms-yahei"),
        ("malgun.ttf", "system-malgun"),
    ];
    let mut definitions = egui::FontDefinitions::default();
    let mut added = false;
    for (file, name) in candidates {
        let Ok(bytes) = std::fs::read(fonts_dir.join(file)) else {
            continue;
        };
        definitions.font_data.insert(
            name.to_owned(),
            std::sync::Arc::new(egui::FontData::from_owned(bytes)),
        );
        for family in [egui::FontFamily::Proportional, egui::FontFamily::Monospace] {
            definitions
                .families
                .entry(family)
                .or_default()
                .push(name.to_owned());
        }
        added = true;
    }
    if added {
        ctx.set_fonts(definitions);
    }
}

const fn rgb(r: u8, g: u8, b: u8) -> Color32 {
    Color32::from_rgb(r, g, b)
}

fn accent_of(dark: bool) -> Color32 {
    if dark { ACCENT_DARK } else { ACCENT_LIGHT }
}

fn border_of(dark: bool) -> Color32 {
    if dark {
        rgb(54, 58, 64)
    } else {
        rgb(221, 225, 230)
    }
}

pub fn accent(ui: &Ui) -> Color32 {
    accent_of(ui.visuals().dark_mode)
}

pub fn tone_color(ui: &Ui, tone: Tone) -> Color32 {
    let dark = ui.visuals().dark_mode;
    match (tone, dark) {
        (Tone::Ok, false) => rgb(0x1B, 0x8A, 0x4B),
        (Tone::Ok, true) => rgb(0x4C, 0xD1, 0x8A),
        (Tone::Warn, false) => rgb(0xB2, 0x6A, 0x00),
        (Tone::Warn, true) => rgb(0xF2, 0xB1, 0x3B),
        (Tone::Error, false) => rgb(0xC6, 0x28, 0x28),
        (Tone::Error, true) => rgb(0xFF, 0x7A, 0x70),
        (Tone::Info, false) => rgb(0x1F, 0x5F, 0xBF),
        (Tone::Info, true) => rgb(0x6C, 0xA8, 0xFF),
        (Tone::Muted, false) => rgb(0x6B, 0x72, 0x7B),
        (Tone::Muted, true) => rgb(0x9A, 0xA1, 0xAA),
    }
}

pub fn muted(ui: &Ui) -> Color32 {
    tone_color(ui, Tone::Muted)
}

/// A white-ish surface with a hairline border.
pub fn card<R>(ui: &mut Ui, add: impl FnOnce(&mut Ui) -> R) -> R {
    let dark = ui.visuals().dark_mode;
    Frame::new()
        .fill(if dark {
            rgb(33, 36, 40)
        } else {
            Color32::WHITE
        })
        .stroke(Stroke::new(1.0, border_of(dark)))
        .corner_radius(CornerRadius::same(RADIUS + 2))
        .inner_margin(Margin::same(16))
        .show(ui, |ui| {
            ui.set_width(ui.available_width());
            add(ui)
        })
        .inner
}

/// Tinted full-width message box with a leading icon.
pub fn callout(ui: &mut Ui, tone: Tone, add: impl FnOnce(&mut Ui)) {
    let color = tone_color(ui, tone);
    Frame::new()
        .fill(color.gamma_multiply(if ui.visuals().dark_mode { 0.16 } else { 0.10 }))
        .stroke(Stroke::new(1.0, color.gamma_multiply(0.45)))
        .corner_radius(CornerRadius::same(RADIUS))
        .inner_margin(Margin::symmetric(12, 10))
        .show(ui, |ui| {
            ui.set_width(ui.available_width());
            ui.horizontal_top(|ui| {
                icon(ui, tone_icon(tone), color, 18.0);
                ui.vertical(|ui| add(ui));
            });
        });
}

/// Inline status: icon + text.
pub fn chip(ui: &mut Ui, tone: Tone, text: impl Into<String>) {
    let color = tone_color(ui, tone);
    ui.horizontal_wrapped(|ui| {
        icon(ui, tone_icon(tone), color, 16.0);
        ui.label(RichText::new(text).color(color));
    });
}

pub fn spinner_chip(ui: &mut Ui, text: &str) {
    ui.horizontal(|ui| {
        ui.add(egui::Spinner::new().size(16.0));
        ui.label(RichText::new(text).color(muted(ui)));
    });
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Icon {
    Check,
    Cross,
    Info,
    Warn,
    Dot,
    Ring,
}

fn tone_icon(tone: Tone) -> Icon {
    match tone {
        Tone::Ok => Icon::Check,
        Tone::Error => Icon::Cross,
        Tone::Warn => Icon::Warn,
        Tone::Info => Icon::Info,
        Tone::Muted => Icon::Ring,
    }
}

/// A `size`-square glyph drawn from shapes.
pub fn icon(ui: &mut Ui, icon: Icon, color: Color32, size: f32) {
    let (rect, _) = ui.allocate_exact_size(Vec2::splat(size), Sense::hover());
    paint_icon(ui, rect, icon, color);
}

pub fn paint_icon(ui: &Ui, rect: Rect, icon: Icon, color: Color32) {
    let painter = ui.painter();
    let c = rect.center();
    let r = rect.width().min(rect.height()) / 2.0;
    let stroke = Stroke::new((r * 0.2).max(1.4), Color32::WHITE);
    let at = |dx: f32, dy: f32| Pos2::new(c.x + dx * r, c.y + dy * r);
    match icon {
        Icon::Check => {
            painter.circle_filled(c, r, color);
            painter.line_segment([at(-0.42, 0.02), at(-0.1, 0.36)], stroke);
            painter.line_segment([at(-0.1, 0.36), at(0.45, -0.3)], stroke);
        }
        Icon::Cross => {
            painter.circle_filled(c, r, color);
            painter.line_segment([at(-0.33, -0.33), at(0.33, 0.33)], stroke);
            painter.line_segment([at(-0.33, 0.33), at(0.33, -0.33)], stroke);
        }
        Icon::Info => {
            painter.circle_filled(c, r, color);
            painter.circle_filled(at(0.0, -0.4), r * 0.12, Color32::WHITE);
            painter.line_segment([at(0.0, -0.1), at(0.0, 0.42)], stroke);
        }
        Icon::Warn => {
            painter.add(egui::Shape::convex_polygon(
                vec![at(0.0, -0.95), at(0.95, 0.75), at(-0.95, 0.75)],
                color,
                Stroke::new(r * 0.2, color),
            ));
            painter.line_segment([at(0.0, -0.3), at(0.0, 0.25)], stroke);
            painter.circle_filled(at(0.0, 0.5), r * 0.1, Color32::WHITE);
        }
        Icon::Dot => {
            painter.circle_filled(c, r * 0.55, color);
        }
        Icon::Ring => {
            painter.circle_stroke(c, r * 0.8, Stroke::new((r * 0.18).max(1.2), color));
        }
    }
}

pub fn primary_button(ui: &Ui, text: &str) -> Button<'static> {
    Button::new(
        RichText::new(text.to_owned())
            .strong()
            .color(Color32::WHITE),
    )
    .fill(accent(ui))
    .min_size(vec2(150.0, 36.0))
    .corner_radius(CornerRadius::same(RADIUS))
}

pub fn secondary_button(text: &str) -> Button<'static> {
    Button::new(text.to_owned())
        .min_size(vec2(130.0, 36.0))
        .corner_radius(CornerRadius::same(RADIUS))
}

/// Numbered step title used by the setup cards.
pub fn step_title(ui: &mut Ui, number: u8, title: &str, badge: Option<(&str, Tone)>) {
    ui.horizontal(|ui| {
        let size = 24.0;
        let (rect, _) = ui.allocate_exact_size(Vec2::splat(size), Sense::hover());
        ui.painter()
            .circle_filled(rect.center(), size / 2.0, accent(ui));
        ui.painter().text(
            rect.center(),
            Align2::CENTER_CENTER,
            number.to_string(),
            FontId::proportional(13.0),
            Color32::WHITE,
        );
        ui.label(RichText::new(title).strong().size(16.0));
        if let Some((text, tone)) = badge {
            let color = tone_color(ui, tone);
            let galley =
                ui.painter()
                    .layout_no_wrap(text.to_owned(), FontId::proportional(12.0), color);
            let (rect, _) = ui.allocate_exact_size(galley.size() + vec2(14.0, 4.0), Sense::hover());
            ui.painter().rect(
                rect,
                CornerRadius::same(10),
                color.gamma_multiply(0.14),
                Stroke::new(1.0, color.gamma_multiply(0.5)),
                StrokeKind::Inside,
            );
            ui.painter()
                .galley(rect.center() - galley.size() / 2.0, galley, color);
        }
    });
}
