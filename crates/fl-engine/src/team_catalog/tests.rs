use std::collections::{BTreeMap, HashMap};
use std::sync::Mutex;
use std::sync::atomic::{AtomicUsize, Ordering};

use fl_sources::http::{BoxFuture, Request, Response, Transport, TransportError};
use fl_sources::team_index::SITEMAP_INDEX_URL;

use super::*;
use crate::services::FixedClock;

/// Serves the recorded sitemaps (unless `offline`) and counts requests.
struct Sitemaps {
    pages: HashMap<String, String>,
    offline: bool,
    requests: AtomicUsize,
    seen: Mutex<Vec<String>>,
}

impl Sitemaps {
    fn new(offline: bool) -> Arc<Self> {
        let dir =
            Path::new(env!("CARGO_MANIFEST_DIR")).join("../../testdata/sources_fotmob/sitemaps");
        let read = |name: &str| std::fs::read_to_string(dir.join(name)).expect("fixture");
        let pages = HashMap::from([
            (SITEMAP_INDEX_URL.to_owned(), read("teams.xml")),
            (
                "https://www.fotmob.com/sitemap/en/teams/1.xml".to_owned(),
                read("teams_1.xml"),
            ),
            (
                "https://www.fotmob.com/sitemap/en/teams/2.xml".to_owned(),
                read("teams_2.xml"),
            ),
        ]);
        Arc::new(Self {
            pages,
            offline,
            requests: AtomicUsize::new(0),
            seen: Mutex::new(Vec::new()),
        })
    }

    fn requests(&self) -> usize {
        self.requests.load(Ordering::SeqCst)
    }
}

impl Transport for Sitemaps {
    fn execute<'a>(
        &'a self,
        request: &'a Request,
    ) -> BoxFuture<'a, Result<Response, TransportError>> {
        Box::pin(async move {
            self.requests.fetch_add(1, Ordering::SeqCst);
            self.seen.lock().unwrap().push(request.url.clone());
            if self.offline {
                return Err(TransportError::Connect("offline".into()));
            }
            let body = self
                .pages
                .get(&request.url)
                .ok_or_else(|| TransportError::CassetteMiss(request.url.clone()))?;
            Ok(Response {
                status: 200,
                headers: BTreeMap::new(),
                body: body.clone().into_bytes(),
            })
        })
    }
}

struct Fixture {
    dir: tempfile::TempDir,
    paths: StatePaths,
}

impl Fixture {
    fn new() -> Self {
        let dir = tempfile::tempdir().unwrap();
        let paths = StatePaths::new(dir.path());
        Self { dir, paths }
    }

    fn services(&self, transport: &Arc<Sitemaps>, clock_offset: Duration) -> Services {
        Services {
            clock: Arc::new(FixedClock(Utc::now() + clock_offset)),
            transport: Some(Arc::clone(transport) as Arc<dyn Transport>),
            skip_sleeps: true,
            ..Services::live()
        }
    }

    /// Store a one-team list as if downloaded `now`.
    fn store(&self) {
        let teams = vec![TeamEntry {
            fotmob_id: 7,
            name: "Stored Fc".into(),
            slug: "stored-fc".into(),
            url: "https://www.fotmob.com/teams/7/overview/stored-fc".into(),
        }];
        fl_fs::write_atomic(&self.paths.team_index(), render_catalog(&teams).as_bytes()).unwrap();
    }

    fn load(
        &self,
        services: &Services,
        dry_run: bool,
        cancel: &CancelToken,
    ) -> (Result<TeamCatalog, UpdateError>, Vec<Progress>) {
        let mut events = Vec::new();
        let result = {
            let mut callback = |progress: Progress| events.push(progress);
            let mut reporter = Reporter::new(&mut callback);
            load(&self.paths, dry_run, services, &mut reporter, cancel)
        };
        (result, events)
    }

    fn stored_ids(&self) -> Option<Vec<i64>> {
        let text = std::fs::read_to_string(self.paths.team_index()).ok()?;
        Some(
            parse_catalog(&text)
                .unwrap()
                .iter()
                .map(|t| t.fotmob_id)
                .collect(),
        )
    }
}

const DOWNLOADED: [i64; 5] = [42, 1583, 1954, 9977, 900_001];
const AFTER_MAX_AGE: Duration = Duration::days(31);

fn ids(catalog: &TeamCatalog) -> Vec<i64> {
    catalog.teams.iter().map(|t| t.fotmob_id).collect()
}

#[test]
fn first_run_downloads_stores_and_reports_progress() {
    let fixture = Fixture::new();
    let transport = Sitemaps::new(false);
    let (result, events) = fixture.load(
        &fixture.services(&transport, Duration::zero()),
        false,
        &CancelToken::new(),
    );
    let catalog = result.unwrap();
    assert_eq!(ids(&catalog), DOWNLOADED);
    assert!(catalog.warnings.is_empty());
    assert_eq!(transport.requests(), 3);
    assert_eq!(fixture.stored_ids().unwrap(), DOWNLOADED);
    assert_eq!(events[0].message, "Downloading the FotMob club list…");
    assert_eq!(events[0].stage, Stage::Fetching);
    assert_eq!(events.last().map(|e| (e.current, e.total)), Some((2, 2)));
    // Only the list is stored, next to the other caches.
    assert!(
        fixture
            .dir
            .path()
            .join("storage/fotmob_teams.json")
            .is_file()
    );
}

#[test]
fn a_fresh_copy_is_reused_without_network() {
    let fixture = Fixture::new();
    fixture.store();
    let transport = Sitemaps::new(false);
    let near_limit = Duration::days(29);
    let (result, events) = fixture.load(
        &fixture.services(&transport, near_limit),
        false,
        &CancelToken::new(),
    );
    assert_eq!(ids(&result.unwrap()), [7]);
    assert_eq!(transport.requests(), 0);
    assert!(events.is_empty());
}

#[test]
fn a_stale_copy_is_refreshed() {
    let fixture = Fixture::new();
    fixture.store();
    let transport = Sitemaps::new(false);
    let (result, _) = fixture.load(
        &fixture.services(&transport, AFTER_MAX_AGE),
        false,
        &CancelToken::new(),
    );
    assert_eq!(ids(&result.unwrap()), DOWNLOADED);
    assert_eq!(fixture.stored_ids().unwrap(), DOWNLOADED);
}

#[test]
fn a_failed_refresh_falls_back_to_the_stale_copy_with_a_warning() {
    let fixture = Fixture::new();
    fixture.store();
    let transport = Sitemaps::new(true);
    let (result, _) = fixture.load(
        &fixture.services(&transport, AFTER_MAX_AGE),
        false,
        &CancelToken::new(),
    );
    let catalog = result.unwrap();
    assert_eq!(ids(&catalog), [7]);
    assert_eq!(catalog.warnings.len(), 1);
    assert!(catalog.warnings[0].contains("Could not refresh the FotMob club list"));
    assert_eq!(fixture.stored_ids().unwrap(), [7]);
}

#[test]
fn without_any_copy_a_failed_download_is_a_fetch_failure() {
    let fixture = Fixture::new();
    let transport = Sitemaps::new(true);
    let (result, _) = fixture.load(
        &fixture.services(&transport, Duration::zero()),
        false,
        &CancelToken::new(),
    );
    let error = result.err().expect("must fail");
    assert_eq!(error.code, ErrorCode::FetchFailed);
    assert_eq!(error.stage, Some(Stage::Fetching));
    assert!(
        error
            .message
            .contains("Could not download the FotMob club list"),
        "{}",
        error.message
    );
    assert!(fixture.stored_ids().is_none());
}

#[test]
fn a_corrupt_copy_counts_as_missing() {
    let fixture = Fixture::new();
    fl_fs::write_atomic(&fixture.paths.team_index(), b"[{\"fotmob_id\": 1").unwrap();
    let transport = Sitemaps::new(false);
    let (result, _) = fixture.load(
        &fixture.services(&transport, Duration::zero()),
        false,
        &CancelToken::new(),
    );
    assert_eq!(ids(&result.unwrap()), DOWNLOADED);
    assert_eq!(fixture.stored_ids().unwrap(), DOWNLOADED);
}

#[test]
fn a_dry_run_downloads_into_memory_only() {
    let fixture = Fixture::new();
    let transport = Sitemaps::new(false);
    let (result, _) = fixture.load(
        &fixture.services(&transport, Duration::zero()),
        true,
        &CancelToken::new(),
    );
    assert_eq!(ids(&result.unwrap()), DOWNLOADED);
    assert!(
        std::fs::read_dir(fixture.dir.path())
            .unwrap()
            .next()
            .is_none()
    );

    // It still reads an existing copy, and leaves a stale one alone.
    fixture.store();
    let (result, _) = fixture.load(
        &fixture.services(&transport, AFTER_MAX_AGE),
        true,
        &CancelToken::new(),
    );
    assert_eq!(ids(&result.unwrap()), DOWNLOADED);
    assert_eq!(fixture.stored_ids().unwrap(), [7]);
}

#[test]
fn an_injected_list_wins_over_cache_and_network() {
    let fixture = Fixture::new();
    fixture.store();
    let transport = Sitemaps::new(false);
    let injected: Arc<[TeamEntry]> = Vec::new().into();
    let services = Services {
        team_index: Some(Arc::clone(&injected)),
        ..fixture.services(&transport, AFTER_MAX_AGE)
    };
    let (result, events) = fixture.load(&services, false, &CancelToken::new());
    assert!(result.unwrap().teams.is_empty());
    assert_eq!(transport.requests(), 0);
    assert!(events.is_empty());
    assert_eq!(fixture.stored_ids().unwrap(), [7]);
}

#[test]
fn cancellation_stops_the_download_and_keeps_the_stale_copy() {
    let fixture = Fixture::new();
    fixture.store();
    let transport = Sitemaps::new(false);
    let cancel = CancelToken::new();
    assert!(cancel.request());
    let (result, _) = fixture.load(&fixture.services(&transport, AFTER_MAX_AGE), false, &cancel);
    assert_eq!(result.err().expect("cancelled").code, ErrorCode::Cancelled);
    assert_eq!(transport.requests(), 0);
    assert_eq!(fixture.stored_ids().unwrap(), [7]);
}

#[test]
fn a_copy_stamped_in_the_future_counts_as_fresh() {
    let fixture = Fixture::new();
    fixture.store();
    let transport = Sitemaps::new(true);
    let past = Duration::days(-400);
    let (result, _) = fixture.load(
        &fixture.services(&transport, past),
        false,
        &CancelToken::new(),
    );
    assert_eq!(ids(&result.unwrap()), [7]);
    assert_eq!(transport.requests(), 0);
}
