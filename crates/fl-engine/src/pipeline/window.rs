//! Scrape window derivation: which transfer events a run asks the providers for.
//!
//! Everything is UTC and pure: "today", the transfer log and the previous
//! run's not-applied rows are passed in.

use chrono::{DateTime, Duration, NaiveDate, Utc};
use fl_sources::dates::{self, previous_window_start};

use crate::api::Window;

/// Re-scan a little before the last applied run so events published late
/// (backdated announcements) are still seen.
pub const SINCE_SAFETY_MARGIN_DAYS: i64 = 7;

/// Provider-facing scope of a scrape.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ScrapeScope {
    /// Explicit lower bound; `None` lets `window` decide.
    pub since: Option<NaiveDate>,
    pub window: dates::Window,
}

impl ScrapeScope {
    /// Human-readable cutoff for progress lines.
    pub fn describe(&self, today: NaiveDate) -> String {
        match self.since {
            Some(since) => format!("since {since}"),
            None => {
                let (start, end) = dates::get_transfer_window_range(self.window, today);
                format!(
                    "window '{}' ({start} to {})",
                    window_name(self.window),
                    end.map_or_else(|| "latest".to_owned(), |end| end.to_string())
                )
            }
        }
    }
}

fn window_name(window: dates::Window) -> &'static str {
    match window {
        dates::Window::Auto => "auto",
        dates::Window::All => "all",
        dates::Window::Summer => "summer",
        dates::Window::Winter => "winter",
    }
}

/// Derive the automatic scrape cutoff from a save's own history.
///
/// Starts a safety margin before the last applied (non-dry-run) change, never
/// later than the oldest event still *retryable* from the previous run
/// (events that can never apply must not pin the window open), and never
/// earlier than the previous-window rule, which is also the fallback when the
/// save has no applied history.
pub fn save_since_date(
    applied: &[DateTime<Utc>],
    retryable_pending: &[NaiveDate],
    today: NaiveDate,
) -> NaiveDate {
    let floor = previous_window_start(today);
    let Some(last) = applied.iter().max() else {
        return floor;
    };
    let mut since = last.date_naive() - Duration::days(SINCE_SAFETY_MARGIN_DAYS);
    if let Some(oldest) = retryable_pending.iter().min() {
        since = since.min(*oldest);
    }
    since.max(floor)
}

/// The scope of one run.
///
/// With a club filter only an explicit `Since` applies. Otherwise an `Auto`
/// window without explicit date continues from `save_since` (the save's own
/// history) or the previous-window start.
pub fn resolve_scope(
    window: &Window,
    club_filter: bool,
    save_since: Option<NaiveDate>,
    today: NaiveDate,
) -> ScrapeScope {
    let (explicit, named) = match window {
        Window::Auto => (None, dates::Window::Auto),
        Window::All => (None, dates::Window::All),
        Window::Summer => (None, dates::Window::Summer),
        Window::Winter => (None, dates::Window::Winter),
        Window::Since(date) => (Some(*date), dates::Window::Auto),
    };
    let since = if club_filter || explicit.is_some() || named != dates::Window::Auto {
        explicit
    } else {
        Some(save_since.unwrap_or_else(|| previous_window_start(today)))
    };
    ScrapeScope {
        since,
        window: named,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::TimeZone;

    fn day(y: i32, m: u32, d: u32) -> NaiveDate {
        NaiveDate::from_ymd_opt(y, m, d).unwrap()
    }

    fn at(y: i32, m: u32, d: u32) -> DateTime<Utc> {
        Utc.with_ymd_and_hms(y, m, d, 23, 30, 0).unwrap()
    }

    #[test]
    fn previous_window_boundaries() {
        assert_eq!(previous_window_start(day(2026, 6, 1)), day(2026, 1, 1));
        assert_eq!(previous_window_start(day(2026, 5, 31)), day(2025, 6, 1));
        assert_eq!(previous_window_start(day(2026, 1, 1)), day(2025, 6, 1));
        assert_eq!(previous_window_start(day(2026, 12, 31)), day(2026, 1, 1));
    }

    #[test]
    fn no_history_falls_back_to_previous_window() {
        assert_eq!(
            save_since_date(&[], &[], day(2026, 10, 24)),
            day(2026, 1, 1)
        );
    }

    #[test]
    fn applies_margin_and_clamps_to_floor() {
        let today = day(2026, 10, 24);
        assert_eq!(
            save_since_date(&[at(2026, 10, 20), at(2026, 10, 1)], &[], today),
            day(2026, 10, 13)
        );
        assert_eq!(
            save_since_date(&[at(2026, 1, 3)], &[], today),
            day(2026, 1, 1)
        );
    }

    #[test]
    fn retryable_pending_widens_window_but_not_past_floor() {
        let today = day(2026, 10, 24);
        let applied = [at(2026, 10, 20)];
        assert_eq!(
            save_since_date(&applied, &[day(2026, 9, 1), day(2026, 10, 2)], today),
            day(2026, 9, 1)
        );
        assert_eq!(
            save_since_date(&applied, &[day(2020, 1, 1)], today),
            day(2026, 1, 1)
        );
    }

    #[test]
    fn day_is_taken_in_utc_not_local_time() {
        // 23:30 UTC on Oct 20 minus 7 days is Oct 13 in UTC regardless of the host zone.
        let applied = [at(2026, 10, 20)];
        assert_eq!(
            save_since_date(&applied, &[], day(2026, 10, 24)),
            day(2026, 10, 13)
        );
    }

    #[test]
    fn scope_rules() {
        let today = day(2026, 10, 24);
        let auto = resolve_scope(&Window::Auto, false, Some(day(2026, 10, 1)), today);
        assert_eq!(auto.since, Some(day(2026, 10, 1)));
        let rebuilt = resolve_scope(&Window::Auto, false, None, today);
        assert_eq!(rebuilt.since, Some(day(2026, 1, 1)));
        let club = resolve_scope(&Window::Auto, true, Some(day(2026, 10, 1)), today);
        assert_eq!(club.since, None);
        let summer = resolve_scope(&Window::Summer, false, Some(day(2026, 10, 1)), today);
        assert_eq!((summer.since, summer.window), (None, dates::Window::Summer));
        let since = resolve_scope(&Window::Since(day(2026, 3, 3)), true, None, today);
        assert_eq!(since.since, Some(day(2026, 3, 3)));
    }
}
