//! The one canonical event order.
//!
//! Dated events apply chronologically (same-day events by their timestamp),
//! undated events after every dated one, and shirt-number observations last.
//! Ties keep input order (the sorts are stable). The date key and the
//! shirt-observation key agree because shirt observations are never dated.

use chrono::{DateTime, Utc};
use fl_sources::dates::parse_iso_datetime;
use fl_sources::model::{Transfer, TransferType};

use super::history::epoch_2000;

type EventKey = (bool, bool, DateTime<Utc>);

/// Sort key of one event.
pub fn event_order_key(transfer: &Transfer) -> impl Ord + use<> {
    let parsed = parse_iso_datetime(&transfer.date);
    let key: EventKey = (
        transfer.transfer_type == TransferType::ShirtNumberUpdate,
        parsed.is_none(),
        parsed.unwrap_or_else(epoch_2000),
    );
    key
}

/// Sort events in place into canonical order.
pub fn sort_events(transfers: &mut [Transfer]) {
    transfers.sort_by_cached_key(event_order_key);
}

/// Indices of `transfers` in canonical order.
pub fn ordered_indices(transfers: &[Transfer]) -> Vec<usize> {
    let mut indices: Vec<usize> = (0..transfers.len()).collect();
    indices.sort_by_cached_key(|&index| event_order_key(&transfers[index]));
    indices
}
