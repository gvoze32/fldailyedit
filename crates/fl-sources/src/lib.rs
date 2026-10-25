//! Data sources: HTTP client, scrape models, FotMob and the other providers.

pub mod besoccer;
pub mod dates;
pub mod fotmob;
pub mod http;
mod lenient;
pub mod model;
pub mod reconcile;
pub mod soccerway;
pub mod sofascore;
pub mod sortitoutsi;
pub mod supplemental;
pub mod tactics;
pub mod team_index;
pub mod transfermarkt;
pub mod wikipedia;

#[cfg(test)]
mod parity_fotmob;

#[cfg(test)]
mod live_smoke;
