//! Shared HTTP layer: one retry/backoff implementation, per-host concurrency
//! and pacing, cooperative cancellation, and a [`Transport`] seam with live,
//! cassette-replay and cassette-record implementations.
//!
//! Every provider goes through the single retry loop in
//! [`HttpClient::execute`] with one shared User-Agent and error type.
//! Library code never prints: failures are returned as [`HttpError`].
//!
//! # Cassettes
//! `cassette.jsonl.gz` holds one JSON object per line
//! `{"method","url","status","headers":{lowercase name: value},"body_b64"}`
//! keyed by `method + url` (URL string exactly as requested, query included;
//! last write wins). Bodies are stored decoded (no `content-encoding`
//! handling on replay). A replay miss is [`TransportError::CassetteMiss`],
//! never retried.

use std::collections::{BTreeMap, HashMap};
use std::fmt;
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::pin::Pin;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use base64::Engine as _;
use base64::engine::general_purpose::STANDARD as BASE64;
use chrono::Utc;
use serde::{Deserialize, Serialize};
use tokio::sync::{Notify, Semaphore};
use tokio::time::Instant;

pub type BoxFuture<'a, T> = Pin<Box<dyn Future<Output = T> + Send + 'a>>;

/// Retryable status set shared by FotMob team/feed/league requests.
pub const RETRYABLE_STATUSES: &[u16] = &[408, 425, 429, 500, 502, 503, 504];

/// A GET request. Cassettes are keyed by `url` verbatim.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Request {
    pub url: String,
    /// Extra headers (names are case-insensitive; later duplicates win).
    pub headers: Vec<(String, String)>,
    /// Per-request total timeout; `None` uses the client default.
    pub timeout: Option<Duration>,
    /// `false` returns 3xx responses as-is instead of following them.
    pub follow_redirects: bool,
}

impl Request {
    pub fn get(url: impl Into<String>) -> Self {
        Self {
            url: url.into(),
            headers: Vec::new(),
            timeout: None,
            follow_redirects: true,
        }
    }

    #[must_use]
    pub fn header(mut self, name: impl Into<String>, value: impl Into<String>) -> Self {
        let name = name.into();
        self.headers
            .retain(|(existing, _)| !existing.eq_ignore_ascii_case(&name));
        self.headers.push((name, value.into()));
        self
    }

    #[must_use]
    pub fn headers<I, N, V>(mut self, headers: I) -> Self
    where
        I: IntoIterator<Item = (N, V)>,
        N: Into<String>,
        V: Into<String>,
    {
        for (name, value) in headers {
            self = self.header(name, value);
        }
        self
    }

    #[must_use]
    pub fn timeout(mut self, timeout: Duration) -> Self {
        self.timeout = Some(timeout);
        self
    }

    #[must_use]
    pub fn no_redirects(mut self) -> Self {
        self.follow_redirects = false;
        self
    }

    /// Host part of the URL (used for per-host limits); `""` when unparsable.
    pub fn host(&self) -> String {
        reqwest::Url::parse(&self.url)
            .ok()
            .and_then(|url| url.host_str().map(str::to_ascii_lowercase))
            .unwrap_or_default()
    }
}

/// A complete HTTP response with a decoded body.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Response {
    pub status: u16,
    /// Lowercase header names; repeated headers are joined with ", ".
    pub headers: BTreeMap<String, String>,
    pub body: Vec<u8>,
}

impl Response {
    pub fn is_success(&self) -> bool {
        (200..300).contains(&self.status)
    }

    /// Header lookup by (case-insensitive) name.
    pub fn header(&self, name: &str) -> Option<&str> {
        self.headers
            .get(&name.to_ascii_lowercase())
            .map(String::as_str)
    }

    /// Body as UTF-8 text (invalid sequences replaced).
    pub fn text(&self) -> String {
        String::from_utf8_lossy(&self.body).into_owned()
    }

    /// Body parsed as JSON, whatever the content type says.
    pub fn json(&self) -> Result<serde_json::Value, serde_json::Error> {
        serde_json::from_slice(&self.body)
    }
}

#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum TransportError {
    #[error("request timed out")]
    Timeout,
    #[error("connection failed: {0}")]
    Connect(String),
    #[error("request failed: {0}")]
    Io(String),
    #[error("invalid request: {0}")]
    InvalidRequest(String),
    #[error("no cassette entry for GET {0}")]
    CassetteMiss(String),
}

impl TransportError {
    /// Whether another attempt can help (network trouble, not a bad request or replay miss).
    pub fn is_retryable(&self) -> bool {
        matches!(self, Self::Timeout | Self::Connect(_) | Self::Io(_))
    }
}

/// One network round-trip. Implementations perform no retries or pacing.
pub trait Transport: Send + Sync + 'static {
    fn execute<'a>(
        &'a self,
        request: &'a Request,
    ) -> BoxFuture<'a, Result<Response, TransportError>>;
}

// ---------------------------------------------------------------------------
// Live transport (reqwest + rustls)
// ---------------------------------------------------------------------------

/// Real network access through reqwest with rustls (no OpenSSL).
pub struct LiveTransport {
    following: reqwest::Client,
    plain: reqwest::Client,
}

impl LiveTransport {
    pub fn new() -> Result<Self, TransportError> {
        let build = |policy: reqwest::redirect::Policy| {
            reqwest::Client::builder()
                .use_rustls_tls()
                .redirect(policy)
                .connect_timeout(Duration::from_secs(10))
                .build()
                .map_err(|e| TransportError::Io(e.to_string()))
        };
        Ok(Self {
            following: build(reqwest::redirect::Policy::limited(10))?,
            plain: build(reqwest::redirect::Policy::none())?,
        })
    }
}

fn classify(error: &reqwest::Error) -> TransportError {
    if error.is_timeout() {
        TransportError::Timeout
    } else if error.is_connect() {
        TransportError::Connect(error.to_string())
    } else if error.is_builder() {
        TransportError::InvalidRequest(error.to_string())
    } else {
        TransportError::Io(error.to_string())
    }
}

impl Transport for LiveTransport {
    fn execute<'a>(
        &'a self,
        request: &'a Request,
    ) -> BoxFuture<'a, Result<Response, TransportError>> {
        Box::pin(async move {
            let client = if request.follow_redirects {
                &self.following
            } else {
                &self.plain
            };
            let mut builder = client.get(&request.url);
            if let Some(timeout) = request.timeout {
                builder = builder.timeout(timeout);
            }
            for (name, value) in &request.headers {
                builder = builder.header(name.as_str(), value.as_str());
            }
            let response = builder.send().await.map_err(|e| classify(&e))?;
            let status = response.status().as_u16();
            let mut headers: BTreeMap<String, String> = BTreeMap::new();
            for (name, value) in response.headers() {
                let value = String::from_utf8_lossy(value.as_bytes()).into_owned();
                headers
                    .entry(name.as_str().to_ascii_lowercase())
                    .and_modify(|joined| {
                        joined.push_str(", ");
                        joined.push_str(&value);
                    })
                    .or_insert(value);
            }
            let body = response.bytes().await.map_err(|e| classify(&e))?.to_vec();
            Ok(Response {
                status,
                headers,
                body,
            })
        })
    }
}

// ---------------------------------------------------------------------------
// Cassettes
// ---------------------------------------------------------------------------

/// Query parameters that never take part in cassette keys (Transfermarkt's
/// per-request cache buster).
const IGNORED_QUERY_PARAMS: &[&str] = &["fldailyedit_refresh"];

/// Failure a recorded request replays as.
#[derive(Debug, Serialize, Deserialize)]
struct RecordedError {
    kind: String,
    #[serde(default, rename = "type")]
    error_type: String,
    #[serde(default)]
    message: String,
}

impl RecordedError {
    fn to_transport_error(&self) -> TransportError {
        match self.kind.as_str() {
            "timeout" => TransportError::Timeout,
            "connection" => TransportError::Connect(self.message.clone()),
            _ => TransportError::Io(self.message.clone()),
        }
    }

    fn from_transport_error(error: &TransportError) -> Self {
        let (kind, error_type) = match error {
            TransportError::Timeout => ("timeout", "Timeout"),
            TransportError::Connect(_) => ("connection", "Connect"),
            _ => ("other", "Io"),
        };
        Self {
            kind: kind.to_owned(),
            error_type: error_type.to_owned(),
            message: error.to_string(),
        }
    }
}

#[derive(Debug, Serialize, Deserialize)]
struct CassetteLine {
    method: String,
    url: String,
    status: u16,
    headers: BTreeMap<String, String>,
    body_b64: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    error: Option<RecordedError>,
}

#[derive(Debug, thiserror::Error)]
pub enum CassetteError {
    #[error("cassette I/O error at {path}: {source}")]
    Io {
        path: PathBuf,
        source: std::io::Error,
    },
    #[error("cassette {path} line {line}: {message}")]
    Format {
        path: PathBuf,
        line: usize,
        message: String,
    },
}

fn cassette_key(method: &str, url: &str) -> String {
    format!("{method} {url}")
}

/// `url` without ignored query parameters; everything else verbatim.
fn strip_ignored_params(url: &str) -> String {
    let Some((base, rest)) = url.split_once('?') else {
        return url.to_owned();
    };
    let (query, fragment) = match rest.split_once('#') {
        Some((query, fragment)) => (query, Some(fragment)),
        None => (rest, None),
    };
    let kept: Vec<&str> = query
        .split('&')
        .filter(|pair| {
            let name = pair.split('=').next().unwrap_or_default();
            !IGNORED_QUERY_PARAMS.contains(&name)
        })
        .collect();
    let mut out = base.to_owned();
    if !kept.is_empty() {
        out.push('?');
        out.push_str(&kept.join("&"));
    }
    if let Some(fragment) = fragment {
        out.push('#');
        out.push_str(fragment);
    }
    out
}

/// Encoding-insensitive form of a URL: decoded query pairs, order-insensitive.
fn canonical_url(url: &str) -> String {
    let Ok(parsed) = reqwest::Url::parse(url) else {
        return url.to_owned();
    };
    let mut pairs: Vec<(String, String)> = parsed
        .query_pairs()
        .map(|(k, v)| (k.into_owned(), v.into_owned()))
        .collect();
    pairs.sort();
    let mut base = parsed.clone();
    base.set_query(None);
    base.set_fragment(None);
    let query: Vec<String> = pairs.iter().map(|(k, v)| format!("{k}={v}")).collect();
    format!("{base}?{}", query.join("&"))
}

enum Recorded {
    Response(Response),
    Failure(TransportError),
}

/// Replays recorded responses; any request without an entry is an error.
///
/// Lookup is by verbatim URL first, then by a canonical form (decoded,
/// order-insensitive query) so URL-encoding differences between the
/// recorder and reqwest never cause false misses. Ignored query
/// parameters (`fldailyedit_refresh`) are stripped before lookup.
pub struct ReplayTransport {
    exact: HashMap<String, Recorded>,
    canonical: HashMap<String, String>,
}

impl ReplayTransport {
    /// Load a `cassette.jsonl.gz` (a plain, uncompressed `.jsonl` also works).
    pub fn from_path(path: &Path) -> Result<Self, CassetteError> {
        let io_error = |source| CassetteError::Io {
            path: path.to_owned(),
            source,
        };
        let file = std::fs::File::open(path).map_err(io_error)?;
        let gzipped = path.extension().is_some_and(|ext| ext == "gz");
        let reader: Box<dyn BufRead> = if gzipped {
            Box::new(BufReader::new(flate2::read::MultiGzDecoder::new(file)))
        } else {
            Box::new(BufReader::new(file))
        };
        let mut transport = Self {
            exact: HashMap::new(),
            canonical: HashMap::new(),
        };
        for (index, line) in reader.lines().enumerate() {
            let line = line.map_err(io_error)?;
            if line.trim().is_empty() {
                continue;
            }
            let format_error = |message: String| CassetteError::Format {
                path: path.to_owned(),
                line: index + 1,
                message,
            };
            let parsed: CassetteLine =
                serde_json::from_str(&line).map_err(|e| format_error(e.to_string()))?;
            let recorded = match parsed.error {
                Some(error) => Recorded::Failure(error.to_transport_error()),
                None => {
                    let body = BASE64
                        .decode(parsed.body_b64.as_bytes())
                        .map_err(|e| format_error(e.to_string()))?;
                    Recorded::Response(Response {
                        status: parsed.status,
                        headers: parsed.headers,
                        body,
                    })
                }
            };
            transport.insert(&parsed.method, &parsed.url, recorded);
        }
        Ok(transport)
    }

    /// Build from in-memory `(url, response)` pairs (tests).
    #[cfg(test)]
    pub fn from_responses(responses: impl IntoIterator<Item = (String, Response)>) -> Self {
        let mut transport = Self {
            exact: HashMap::new(),
            canonical: HashMap::new(),
        };
        for (url, response) in responses {
            transport.insert("GET", &url, Recorded::Response(response));
        }
        transport
    }

    fn insert(&mut self, method: &str, url: &str, recorded: Recorded) {
        let method = method.to_ascii_uppercase();
        let url = strip_ignored_params(url);
        let key = cassette_key(&method, &url);
        self.canonical
            .insert(cassette_key(&method, &canonical_url(&url)), key.clone());
        self.exact.insert(key, recorded);
    }

    fn lookup(&self, url: &str) -> Option<&Recorded> {
        let url = strip_ignored_params(url);
        self.exact.get(&cassette_key("GET", &url)).or_else(|| {
            let key = self
                .canonical
                .get(&cassette_key("GET", &canonical_url(&url)))?;
            self.exact.get(key)
        })
    }
}

impl Transport for ReplayTransport {
    fn execute<'a>(
        &'a self,
        request: &'a Request,
    ) -> BoxFuture<'a, Result<Response, TransportError>> {
        Box::pin(async move {
            match self.lookup(&request.url) {
                Some(Recorded::Response(response)) => Ok(response.clone()),
                Some(Recorded::Failure(error)) => Err(error.clone()),
                None => Err(TransportError::CassetteMiss(request.url.clone())),
            }
        })
    }
}

/// Wraps another transport and records every outcome it returns.
pub struct RecordTransport {
    inner: Arc<dyn Transport>,
    path: PathBuf,
    recorded: Mutex<Vec<CassetteLine>>,
}

impl RecordTransport {
    pub fn new(inner: Arc<dyn Transport>, path: impl Into<PathBuf>) -> Self {
        Self {
            inner,
            path: path.into(),
            recorded: Mutex::new(Vec::new()),
        }
    }

    /// Number of recorded outcomes (including superseded duplicates).
    #[cfg(test)]
    pub fn recorded_len(&self) -> usize {
        self.recorded.lock().map_or(0, |lines| lines.len())
    }

    /// Write the cassette (gzipped JSONL, last write per key wins).
    pub fn save(&self) -> Result<(), CassetteError> {
        let io_error = |source| CassetteError::Io {
            path: self.path.clone(),
            source,
        };
        let lines = self
            .recorded
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        let mut latest: BTreeMap<String, &CassetteLine> = BTreeMap::new();
        for line in lines.iter() {
            latest.insert(cassette_key(&line.method, &line.url), line);
        }
        if let Some(parent) = self.path.parent() {
            std::fs::create_dir_all(parent).map_err(io_error)?;
        }
        let file = std::fs::File::create(&self.path).map_err(io_error)?;
        let mut encoder = flate2::write::GzEncoder::new(file, flate2::Compression::default());
        for line in latest.values() {
            let json = serde_json::to_string(line).map_err(|e| io_error(e.into()))?;
            encoder.write_all(json.as_bytes()).map_err(io_error)?;
            encoder.write_all(b"\n").map_err(io_error)?;
        }
        encoder.finish().map_err(io_error)?;
        Ok(())
    }
}

impl Transport for RecordTransport {
    fn execute<'a>(
        &'a self,
        request: &'a Request,
    ) -> BoxFuture<'a, Result<Response, TransportError>> {
        Box::pin(async move {
            let outcome = self.inner.execute(request).await;
            let url = strip_ignored_params(&request.url);
            let line = match &outcome {
                Ok(response) => CassetteLine {
                    method: "GET".to_owned(),
                    url,
                    status: response.status,
                    headers: response.headers.clone(),
                    body_b64: BASE64.encode(&response.body),
                    error: None,
                },
                Err(error) => CassetteLine {
                    method: "GET".to_owned(),
                    url,
                    status: 0,
                    headers: BTreeMap::new(),
                    body_b64: String::new(),
                    error: Some(RecordedError::from_transport_error(error)),
                },
            };
            if let Ok(mut lines) = self.recorded.lock() {
                lines.push(line);
            }
            outcome
        })
    }
}

// ---------------------------------------------------------------------------
// Cancellation
// ---------------------------------------------------------------------------

/// Cooperative cancel flag shared between the UI/CLI and in-flight scrapes.
#[derive(Clone, Default)]
pub struct CancelToken {
    inner: Arc<CancelInner>,
}

#[derive(Default)]
struct CancelInner {
    flag: AtomicBool,
    notify: Notify,
}

impl CancelToken {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn cancel(&self) {
        self.inner.flag.store(true, Ordering::SeqCst);
        self.inner.notify.notify_waiters();
    }

    pub fn is_cancelled(&self) -> bool {
        self.inner.flag.load(Ordering::SeqCst)
    }

    /// Resolves once cancelled.
    pub async fn cancelled(&self) {
        loop {
            let notified = self.inner.notify.notified();
            if self.is_cancelled() {
                return;
            }
            notified.await;
        }
    }
}

impl fmt::Debug for CancelToken {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_tuple("CancelToken")
            .field(&self.is_cancelled())
            .finish()
    }
}

// ---------------------------------------------------------------------------
// Retry policy and client
// ---------------------------------------------------------------------------

/// Bounded retry with exponential backoff (`base * 2^attempt`, capped) that
/// honours `Retry-After` (seconds or HTTP-date, also capped).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct RetryPolicy {
    pub max_attempts: u32,
    pub base_delay: Duration,
    pub max_delay: Duration,
    pub retryable_statuses: &'static [u16],
}

impl RetryPolicy {
    /// FotMob feed, team, league and match-page requests: 3 attempts,
    /// 0.5 s · 2^n capped at 8 s.
    pub const FOTMOB: Self = Self {
        max_attempts: 3,
        base_delay: Duration::from_millis(500),
        max_delay: Duration::from_secs(8),
        retryable_statuses: RETRYABLE_STATUSES,
    };

    /// Single attempt; transport errors and HTTP statuses surface unchanged.
    pub const NONE: Self = Self {
        max_attempts: 1,
        base_delay: Duration::ZERO,
        max_delay: Duration::ZERO,
        retryable_statuses: &[],
    };

    #[cfg(test)]
    #[must_use]
    pub const fn with_delays(mut self, base_delay: Duration, max_delay: Duration) -> Self {
        self.base_delay = base_delay;
        self.max_delay = max_delay;
        self
    }

    /// Delay before retry number `attempt + 1` (`attempt` is zero-based).
    fn delay(&self, attempt: u32, response: Option<&Response>) -> Duration {
        let requested = response
            .and_then(|r| r.header("retry-after"))
            .and_then(parse_retry_after)
            .unwrap_or_else(|| self.base_delay.saturating_mul(2u32.saturating_pow(attempt)));
        requested.min(self.max_delay)
    }
}

/// `Retry-After` as seconds (`"3"`, `"0.5"`) or an HTTP-date.
fn parse_retry_after(value: &str) -> Option<Duration> {
    let value = value.trim();
    if let Ok(seconds) = value.parse::<f64>() {
        return seconds
            .is_finite()
            .then(|| Duration::from_secs_f64(seconds.max(0.0)));
    }
    let date = chrono::DateTime::parse_from_rfc2822(value).ok()?;
    let wait = date.with_timezone(&Utc) - Utc::now();
    Some(wait.to_std().unwrap_or(Duration::ZERO))
}

#[derive(Debug, thiserror::Error)]
pub enum HttpError {
    /// Final HTTP status was an error (or a retryable status that never recovered).
    #[error("HTTP {status} for {url}")]
    Status { status: u16, url: String },
    #[error("request to {url} failed: {source}")]
    Transport { url: String, source: TransportError },
    #[error("invalid JSON from {url}: {message}")]
    Json { url: String, message: String },
    #[error("request cancelled")]
    Cancelled,
}

#[derive(Default)]
struct HostState {
    permits: Option<Arc<Semaphore>>,
    min_interval: Duration,
    next_slot: Option<Instant>,
}

struct ClientInner {
    transport: Arc<dyn Transport>,
    default_timeout: Duration,
    default_concurrency: usize,
    hosts: Mutex<HashMap<String, HostState>>,
    cancel: CancelToken,
    skip_sleeps: bool,
}

/// Cheaply cloneable client; clones share limits, cancellation and transport.
#[derive(Clone)]
pub struct HttpClient {
    inner: Arc<ClientInner>,
}

/// Builder for [`HttpClient`].
pub struct HttpClientBuilder {
    transport: Option<Arc<dyn Transport>>,
    default_timeout: Duration,
    default_concurrency: usize,
    host_concurrency: Vec<(String, usize)>,
    host_min_interval: Vec<(String, Duration)>,
    cancel: CancelToken,
    skip_sleeps: bool,
}

impl HttpClient {
    pub fn builder() -> HttpClientBuilder {
        HttpClientBuilder {
            transport: None,
            default_timeout: Duration::from_secs(15),
            default_concurrency: 4,
            host_concurrency: Vec::new(),
            host_min_interval: Vec::new(),
            cancel: CancelToken::new(),
            skip_sleeps: false,
        }
    }

    /// Live client with defaults (15 s timeout, 4 concurrent requests per host).
    pub fn live() -> Result<Self, TransportError> {
        Self::builder()
            .transport(Arc::new(LiveTransport::new()?))
            .build()
    }

    /// Offline client serving a recorded cassette (`cassette.jsonl.gz`): no
    /// network, backoff/pacing sleeps skipped, misses are errors.
    pub fn replay(cassette: &Path) -> Result<Self, CassetteError> {
        let transport = ReplayTransport::from_path(cassette)?;
        Ok(Self::builder()
            .transport(Arc::new(transport))
            .skip_sleeps(true)
            .build()
            .expect("a transport was supplied"))
    }

    /// The token that aborts this client's requests and backoff sleeps.
    pub fn cancel_token(&self) -> &CancelToken {
        &self.inner.cancel
    }

    fn host_permits(&self, host: &str) -> (Arc<Semaphore>, Option<Instant>) {
        let mut hosts = self
            .inner
            .hosts
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        let state = hosts.entry(host.to_owned()).or_default();
        let permits = state
            .permits
            .get_or_insert_with(|| Arc::new(Semaphore::new(self.inner.default_concurrency)))
            .clone();
        let slot = (!state.min_interval.is_zero()).then(|| {
            let now = Instant::now();
            let slot = state.next_slot.map_or(now, |next| next.max(now));
            state.next_slot = Some(slot + state.min_interval);
            slot
        });
        (permits, slot)
    }

    async fn sleep(&self, duration: Duration) -> Result<(), HttpError> {
        if duration.is_zero() || self.inner.skip_sleeps {
            return if self.inner.cancel.is_cancelled() {
                Err(HttpError::Cancelled)
            } else {
                Ok(())
            };
        }
        tokio::select! {
            () = tokio::time::sleep(duration) => Ok(()),
            () = self.inner.cancel.cancelled() => Err(HttpError::Cancelled),
        }
    }

    /// Wait `duration` (instant when sleeps are skipped), aborting on cancel.
    /// For pacing outside request retries, e.g. per-club cooldowns.
    pub async fn pause(&self, duration: Duration) -> Result<(), HttpError> {
        self.sleep(duration).await
    }

    /// Send `request`, retrying per `policy`.
    ///
    /// Returns the response for any status that is not retryable (including
    /// 3xx/4xx/5xx outside the policy set and 304); the caller decides what a
    /// status means. A retryable status that persists through the last attempt
    /// is [`HttpError::Status`].
    pub async fn execute(
        &self,
        request: &Request,
        policy: &RetryPolicy,
    ) -> Result<Response, HttpError> {
        let mut request = request.clone();
        request.timeout.get_or_insert(self.inner.default_timeout);
        let timeout = request.timeout.unwrap_or(self.inner.default_timeout);
        let host = request.host();
        let attempts = policy.max_attempts.max(1);
        for attempt in 0..attempts {
            if self.inner.cancel.is_cancelled() {
                return Err(HttpError::Cancelled);
            }
            let last = attempt + 1 == attempts;
            let (permits, slot) = self.host_permits(&host);
            if let Some(slot) = slot {
                self.sleep(slot.saturating_duration_since(Instant::now()))
                    .await?;
            }
            let outcome = {
                let _permit = permits.acquire().await.expect("semaphore is never closed");
                tokio::select! {
                    // Backstop for transports that ignore `Request::timeout`.
                    result = tokio::time::timeout(timeout + Duration::from_secs(5), self.inner.transport.execute(&request)) => {
                        result.unwrap_or(Err(TransportError::Timeout))
                    }
                    () = self.inner.cancel.cancelled() => return Err(HttpError::Cancelled),
                }
            };
            match outcome {
                Ok(response) if policy.retryable_statuses.contains(&response.status) => {
                    if last {
                        return Err(HttpError::Status {
                            status: response.status,
                            url: request.url,
                        });
                    }
                    tracing::debug!(url = %request.url, status = response.status, attempt, "retrying HTTP status");
                    self.sleep(policy.delay(attempt, Some(&response))).await?;
                }
                Ok(response) => return Ok(response),
                Err(error) if error.is_retryable() && !last => {
                    tracing::debug!(url = %request.url, %error, attempt, "retrying transport error");
                    self.sleep(policy.delay(attempt, None)).await?;
                }
                Err(source) => {
                    return Err(HttpError::Transport {
                        url: request.url,
                        source,
                    });
                }
            }
        }
        unreachable!("the last attempt always returns")
    }

    /// `execute` + require a 2xx status.
    pub async fn get_ok(
        &self,
        request: &Request,
        policy: &RetryPolicy,
    ) -> Result<Response, HttpError> {
        let response = self.execute(request, policy).await?;
        if response.is_success() {
            Ok(response)
        } else {
            Err(HttpError::Status {
                status: response.status,
                url: request.url.clone(),
            })
        }
    }

    /// `get_ok` + body as JSON (any content type).
    pub async fn get_json(
        &self,
        request: &Request,
        policy: &RetryPolicy,
    ) -> Result<serde_json::Value, HttpError> {
        let response = self.get_ok(request, policy).await?;
        response.json().map_err(|e| HttpError::Json {
            url: request.url.clone(),
            message: e.to_string(),
        })
    }
}

impl HttpClientBuilder {
    #[must_use]
    pub fn transport(mut self, transport: Arc<dyn Transport>) -> Self {
        self.transport = Some(transport);
        self
    }

    /// Total timeout for requests that do not set their own.
    #[must_use]
    pub fn default_timeout(mut self, timeout: Duration) -> Self {
        self.default_timeout = timeout;
        self
    }

    /// Concurrent in-flight requests per host unless overridden.
    #[must_use]
    pub fn default_concurrency(mut self, limit: usize) -> Self {
        self.default_concurrency = limit.max(1);
        self
    }

    /// Concurrency limit for one host (e.g. `"en.wikipedia.org"`).
    #[must_use]
    pub fn host_concurrency(mut self, host: &str, limit: usize) -> Self {
        self.host_concurrency
            .push((host.to_ascii_lowercase(), limit.max(1)));
        self
    }

    /// Minimum spacing between request starts to one host.
    #[must_use]
    pub fn host_min_interval(mut self, host: &str, interval: Duration) -> Self {
        self.host_min_interval
            .push((host.to_ascii_lowercase(), interval));
        self
    }

    #[must_use]
    pub fn cancel_token(mut self, token: CancelToken) -> Self {
        self.cancel = token;
        self
    }

    /// Make backoff and pacing waits instant (cassette replay: no real time
    /// passes). Cancellation is still honoured.
    #[must_use]
    pub fn skip_sleeps(mut self, skip: bool) -> Self {
        self.skip_sleeps = skip;
        self
    }

    /// Fails only when no transport was supplied.
    pub fn build(self) -> Result<HttpClient, TransportError> {
        let transport = self.transport.ok_or_else(|| {
            TransportError::InvalidRequest("HttpClient needs a transport".to_owned())
        })?;
        let mut hosts: HashMap<String, HostState> = HashMap::new();
        for (host, limit) in self.host_concurrency {
            hosts.entry(host).or_default().permits = Some(Arc::new(Semaphore::new(limit)));
        }
        for (host, interval) in self.host_min_interval {
            hosts.entry(host).or_default().min_interval = interval;
        }
        Ok(HttpClient {
            inner: Arc::new(ClientInner {
                transport,
                default_timeout: self.default_timeout,
                default_concurrency: self.default_concurrency,
                hosts: Mutex::new(hosts),
                cancel: self.cancel,
                skip_sleeps: self.skip_sleeps,
            }),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::AtomicUsize;

    /// Scripted transport: pops one outcome per call and counts calls.
    struct Script {
        outcomes: Mutex<Vec<Result<Response, TransportError>>>,
        calls: AtomicUsize,
    }

    impl Script {
        fn new(mut outcomes: Vec<Result<Response, TransportError>>) -> Arc<Self> {
            outcomes.reverse();
            Arc::new(Self {
                outcomes: Mutex::new(outcomes),
                calls: AtomicUsize::new(0),
            })
        }
    }

    impl Transport for Script {
        fn execute<'a>(
            &'a self,
            _request: &'a Request,
        ) -> BoxFuture<'a, Result<Response, TransportError>> {
            Box::pin(async move {
                self.calls.fetch_add(1, Ordering::SeqCst);
                self.outcomes
                    .lock()
                    .unwrap()
                    .pop()
                    .expect("scripted outcome")
            })
        }
    }

    fn response(status: u16, body: &str) -> Response {
        Response {
            status,
            headers: BTreeMap::new(),
            body: body.as_bytes().to_vec(),
        }
    }

    fn client(script: Arc<Script>) -> HttpClient {
        HttpClient::builder().transport(script).build().unwrap()
    }

    const FAST: RetryPolicy = RetryPolicy::FOTMOB.with_delays(Duration::ZERO, Duration::ZERO);

    #[tokio::test]
    async fn retries_retryable_status_then_succeeds() {
        let script = Script::new(vec![
            Ok(response(503, "")),
            Ok(response(429, "")),
            Ok(response(200, "{\"a\":1}")),
        ]);
        let value = client(script.clone())
            .get_json(&Request::get("https://x.test/a"), &FAST)
            .await
            .unwrap();
        assert_eq!(value["a"], 1);
        assert_eq!(script.calls.load(Ordering::SeqCst), 3);
    }

    #[tokio::test]
    async fn exhausted_retryable_status_is_error_and_404_is_not_retried() {
        let script = Script::new(vec![
            Ok(response(502, "")),
            Ok(response(502, "")),
            Ok(response(502, "")),
        ]);
        let err = client(script.clone())
            .execute(&Request::get("https://x.test/a"), &FAST)
            .await
            .unwrap_err();
        assert!(matches!(err, HttpError::Status { status: 502, .. }));
        assert_eq!(script.calls.load(Ordering::SeqCst), 3);

        let script = Script::new(vec![Ok(response(404, "nope"))]);
        let ok = client(script.clone())
            .execute(&Request::get("https://x.test/a"), &FAST)
            .await
            .unwrap();
        assert_eq!(ok.status, 404);
        assert_eq!(script.calls.load(Ordering::SeqCst), 1);
        let script = Script::new(vec![Ok(response(404, "nope"))]);
        let err = client(script)
            .get_ok(&Request::get("https://x.test/a"), &FAST)
            .await
            .unwrap_err();
        assert!(matches!(err, HttpError::Status { status: 404, .. }));
    }

    #[tokio::test]
    async fn transport_errors_retry_but_cassette_miss_does_not() {
        let script = Script::new(vec![Err(TransportError::Timeout), Ok(response(200, "ok"))]);
        let text = client(script.clone())
            .get_ok(&Request::get("https://x.test/a"), &FAST)
            .await
            .unwrap()
            .text();
        assert_eq!(text, "ok");

        let script = Script::new(vec![Err(TransportError::CassetteMiss("u".into()))]);
        let err = client(script.clone())
            .execute(&Request::get("https://x.test/a"), &FAST)
            .await
            .unwrap_err();
        assert!(matches!(
            err,
            HttpError::Transport {
                source: TransportError::CassetteMiss(_),
                ..
            }
        ));
        assert_eq!(script.calls.load(Ordering::SeqCst), 1);
    }

    #[test]
    fn retry_after_seconds_and_http_date() {
        let policy = RetryPolicy::FOTMOB;
        let mut hinted = response(429, "");
        hinted.headers.insert("retry-after".into(), "3".into());
        assert_eq!(policy.delay(0, Some(&hinted)), Duration::from_secs(3));
        hinted.headers.insert("retry-after".into(), "999".into());
        assert_eq!(policy.delay(0, Some(&hinted)), Duration::from_secs(8));
        let future = (Utc::now() + chrono::Duration::seconds(5)).to_rfc2822();
        hinted.headers.insert("retry-after".into(), future);
        let delay = policy.delay(0, Some(&hinted));
        assert!(
            delay > Duration::from_secs(3) && delay <= Duration::from_secs(5),
            "{delay:?}"
        );
        hinted
            .headers
            .insert("retry-after".into(), "garbage".into());
        assert_eq!(policy.delay(2, Some(&hinted)), Duration::from_secs(2));
        assert_eq!(policy.delay(0, None), Duration::from_millis(500));
        assert_eq!(policy.delay(9, None), Duration::from_secs(8));
    }

    #[tokio::test]
    async fn cancellation_aborts_backoff_sleep() {
        let script = Script::new(vec![Ok(response(503, "")), Ok(response(200, ""))]);
        let token = CancelToken::new();
        let client = HttpClient::builder()
            .transport(script)
            .cancel_token(token.clone())
            .build()
            .unwrap();
        let policy =
            RetryPolicy::FOTMOB.with_delays(Duration::from_secs(60), Duration::from_secs(60));
        let canceller = tokio::spawn({
            let token = token.clone();
            async move {
                tokio::time::sleep(Duration::from_millis(50)).await;
                token.cancel();
            }
        });
        let err = client
            .execute(&Request::get("https://x.test/a"), &policy)
            .await
            .unwrap_err();
        assert!(matches!(err, HttpError::Cancelled));
        canceller.await.unwrap();
    }

    #[tokio::test]
    async fn per_host_concurrency_limit_is_enforced() {
        struct Gauge {
            current: AtomicUsize,
            peak: AtomicUsize,
        }
        impl Transport for Gauge {
            fn execute<'a>(
                &'a self,
                _r: &'a Request,
            ) -> BoxFuture<'a, Result<Response, TransportError>> {
                Box::pin(async move {
                    let now = self.current.fetch_add(1, Ordering::SeqCst) + 1;
                    self.peak.fetch_max(now, Ordering::SeqCst);
                    tokio::time::sleep(Duration::from_millis(20)).await;
                    self.current.fetch_sub(1, Ordering::SeqCst);
                    Ok(response(200, ""))
                })
            }
        }
        let gauge = Arc::new(Gauge {
            current: AtomicUsize::new(0),
            peak: AtomicUsize::new(0),
        });
        let client = HttpClient::builder()
            .transport(gauge.clone())
            .host_concurrency("limited.test", 2)
            .build()
            .unwrap();
        let tasks: Vec<_> = (0..8)
            .map(|i| {
                let client = client.clone();
                tokio::spawn(async move {
                    client
                        .execute(
                            &Request::get(format!("https://limited.test/{i}")),
                            &RetryPolicy::NONE,
                        )
                        .await
                })
            })
            .collect();
        for task in tasks {
            task.await.unwrap().unwrap();
        }
        assert_eq!(gauge.peak.load(Ordering::SeqCst), 2);
    }

    #[tokio::test]
    async fn record_then_replay_round_trips_and_misses_error() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("sub/cassette.jsonl.gz");
        let mut first = response(200, "hello");
        first.headers.insert("etag".into(), "\"x\"".into());
        let script = Script::new(vec![
            Ok(first.clone()),
            Ok(response(200, "second")),
            Ok(response(404, "")),
        ]);
        let recorder = Arc::new(RecordTransport::new(script, &path));
        let client = HttpClient::builder()
            .transport(recorder.clone())
            .build()
            .unwrap();
        for url in [
            "https://x.test/a?b=1",
            "https://x.test/a?b=1",
            "https://x.test/missing",
        ] {
            client
                .execute(&Request::get(url), &RetryPolicy::NONE)
                .await
                .unwrap();
        }
        assert_eq!(recorder.recorded_len(), 3);
        recorder.save().unwrap();

        let replay = ReplayTransport::from_path(&path).unwrap();
        let client = HttpClient::builder()
            .transport(Arc::new(replay))
            .build()
            .unwrap();
        let got = client
            .execute(&Request::get("https://x.test/a?b=1"), &RetryPolicy::NONE)
            .await
            .unwrap();
        assert_eq!(got.text(), "second", "last write wins");
        let miss = client
            .execute(&Request::get("https://x.test/a"), &RetryPolicy::NONE)
            .await
            .unwrap_err();
        assert!(matches!(
            miss,
            HttpError::Transport {
                source: TransportError::CassetteMiss(_),
                ..
            }
        ));
    }
}
