//! Presentation helpers shared by the CLI and the GUI result screen.

use std::time::Duration;

use fl_engine::api::SkippedTransfer;

/// Transfers not applied for one reason.
#[derive(Debug, Clone, PartialEq)]
pub struct SkippedGroup<'a> {
    pub reason: &'a str,
    /// Rows touching a club in the save come first.
    pub items: Vec<&'a SkippedTransfer>,
    pub relevant: usize,
}

/// Group `skipped` by reason: groups with the most save-relevant rows first,
/// then the largest, then by name.
pub fn group_skipped(skipped: &[SkippedTransfer]) -> Vec<SkippedGroup<'_>> {
    let mut groups: Vec<SkippedGroup<'_>> = Vec::new();
    for item in skipped {
        let reason = if item.reason.is_empty() {
            "unknown"
        } else {
            item.reason.as_str()
        };
        match groups.iter_mut().find(|group| group.reason == reason) {
            Some(group) => group.items.push(item),
            None => groups.push(SkippedGroup {
                reason,
                items: vec![item],
                relevant: 0,
            }),
        }
    }
    for group in &mut groups {
        group.items.sort_by_key(|item| !item.relevant);
        group.relevant = group.items.iter().filter(|item| item.relevant).count();
    }
    groups.sort_by(|a, b| {
        b.relevant
            .cmp(&a.relevant)
            .then(b.items.len().cmp(&a.items.len()))
            .then(a.reason.cmp(b.reason))
    });
    groups
}

/// `player_not_in_save` → `Player not in save`.
pub fn reason_label(reason: &str) -> String {
    let spaced = reason.replace(['_', '-'], " ");
    let mut chars = spaced.chars();
    chars
        .next()
        .map(|first| first.to_uppercase().chain(chars).collect())
        .unwrap_or_default()
}

/// `Player: From -> To (date) — detail`
pub fn skipped_line(item: &SkippedTransfer) -> String {
    let mut line = format!(
        "{}: {} -> {} ({})",
        item.player,
        non_empty(&item.from_club, "?"),
        non_empty(&item.to_club, "?"),
        item.date
            .as_deref()
            .filter(|d| !d.is_empty())
            .unwrap_or("undated"),
    );
    if !item.detail.is_empty() {
        line.push_str(" — ");
        line.push_str(&item.detail);
    }
    line
}

fn non_empty<'a>(text: &'a str, fallback: &'a str) -> &'a str {
    if text.is_empty() { fallback } else { text }
}

/// `1m 05s`, `42s`, `1h 02m`.
pub fn format_elapsed(elapsed: Duration) -> String {
    let seconds = elapsed.as_secs();
    match seconds {
        0..60 => format!("{seconds}s"),
        60..3600 => format!("{}m {:02}s", seconds / 60, seconds % 60),
        _ => format!("{}h {:02}m", seconds / 3600, seconds / 60 % 60),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn item(player: &str, reason: &str, relevant: bool) -> SkippedTransfer {
        SkippedTransfer {
            player: player.into(),
            from_club: "A".into(),
            to_club: String::new(),
            date: None,
            transfer_type: "transfer".into(),
            reason: reason.into(),
            detail: String::new(),
            relevant,
        }
    }

    #[test]
    fn groups_put_save_relevant_reasons_first() {
        let skipped = vec![
            item("a", "not_in_save", false),
            item("b", "not_in_save", false),
            item("c", "not_in_save", false),
            item("d", "roster_full", true),
            item("e", "not_in_save", true),
            item("f", "", false),
        ];
        let groups = group_skipped(&skipped);
        let order: Vec<_> = groups
            .iter()
            .map(|g| (g.reason, g.items.len(), g.relevant))
            .collect();
        assert_eq!(
            order,
            [
                ("not_in_save", 4, 1),
                ("roster_full", 1, 1),
                ("unknown", 1, 0)
            ]
        );
        assert!(groups[0].items[0].relevant, "relevant rows lead");
    }

    #[test]
    fn labels_and_lines_read_naturally() {
        assert_eq!(reason_label("player_not_in_save"), "Player not in save");
        assert_eq!(
            skipped_line(&item("Joe", "x", false)),
            "Joe: A -> ? (undated)"
        );
        assert_eq!(format_elapsed(Duration::from_secs(65)), "1m 05s");
        assert_eq!(format_elapsed(Duration::from_secs(7)), "7s");
        assert_eq!(format_elapsed(Duration::from_secs(3720)), "1h 02m");
    }
}
