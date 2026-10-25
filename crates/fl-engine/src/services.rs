//! Injectable ambient dependencies of an update: the clock, the HTTP
//! transport and native-database file overrides.
//!
//! Engine code never reads the wall clock or opens a socket on its own; the
//! production defaults live in [`Services::live`] and tests substitute
//! fixed/replay implementations.

use std::sync::Arc;

use chrono::{DateTime, NaiveDate, Utc};
use fl_sources::http::{HttpClient, LiveTransport, Transport};
use fl_sources::team_index::TeamEntry;

use crate::api::{CancelToken, ErrorCode, Stage, UpdateError};
use crate::native::NativeFiles;

/// Source of the current UTC time.
pub trait Clock: Send + Sync {
    fn now(&self) -> DateTime<Utc>;

    fn today(&self) -> NaiveDate {
        self.now().date_naive()
    }
}

/// The real clock.
#[derive(Debug, Clone, Copy, Default)]
pub struct SystemClock;

impl Clock for SystemClock {
    fn now(&self) -> DateTime<Utc> {
        Utc::now()
    }
}

/// A clock frozen at one instant.
#[derive(Debug, Clone, Copy)]
pub struct FixedClock(pub DateTime<Utc>);

impl Clock for FixedClock {
    fn now(&self) -> DateTime<Utc> {
        self.0
    }
}

/// Everything an update needs from the outside world besides its request.
#[derive(Clone)]
pub struct Services {
    pub clock: Arc<dyn Clock>,
    /// HTTP transport; `None` builds the live reqwest transport.
    pub transport: Option<Arc<dyn Transport>>,
    /// Native database files that beat the game archives.
    pub native_files: NativeFiles,
    /// Make provider back-off and pacing waits instant (offline replays).
    pub skip_sleeps: bool,
    /// FotMob team list to use as-is; `None` reads the state-directory copy or
    /// downloads it from FotMob (see [`crate::team_catalog`]).
    pub team_index: Option<Arc<[TeamEntry]>>,
}

impl Services {
    /// Production defaults: system clock, live network, game archives.
    pub fn live() -> Self {
        Self {
            clock: Arc::new(SystemClock),
            transport: None,
            native_files: NativeFiles::default(),
            skip_sleeps: false,
            team_index: None,
        }
    }

    /// The shared HTTP client of a run: the injected or live transport, with
    /// `cancel` aborting in-flight requests and back-off waits.
    pub(crate) fn http_client(
        &self,
        cancel: &CancelToken,
        stage: Stage,
    ) -> Result<HttpClient, UpdateError> {
        let failure = |error: &dyn std::fmt::Display| UpdateError {
            code: ErrorCode::Internal,
            stage: Some(stage),
            message: format!("Could not start the HTTP client: {error}"),
        };
        let transport: Arc<dyn Transport> = match &self.transport {
            Some(transport) => Arc::clone(transport),
            None => Arc::new(LiveTransport::new().map_err(|error| failure(&error))?),
        };
        HttpClient::builder()
            .transport(transport)
            .cancel_token(cancel.transport().clone())
            .skip_sleeps(self.skip_sleeps)
            .build()
            .map_err(|error| failure(&error))
    }
}

impl Default for Services {
    fn default() -> Self {
        Self::live()
    }
}
