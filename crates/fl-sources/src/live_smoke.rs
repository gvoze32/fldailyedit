//! Network smoke test (ignored by default): one real request through the
//! rustls transport.
//!
//!     cargo test -p fl-sources live_feed_page -- --ignored

use crate::fotmob::parse::parse_fotmob_item;
use crate::fotmob::{DEFAULT_HEADERS, feed_url};
use crate::http::{HttpClient, Request, RetryPolicy};

#[tokio::test]
#[ignore = "needs network access"]
async fn live_feed_page() {
    let client = HttpClient::live().expect("rustls client");
    let request = Request::get(feed_url(1, false)).headers(DEFAULT_HEADERS);
    let body = client
        .get_json(&request, &RetryPolicy::FOTMOB)
        .await
        .expect("FotMob feed page 1");
    let items = body["transfers"].as_array().expect("transfers array");
    assert!(!items.is_empty());
    let parsed = items
        .iter()
        .filter(|item| parse_fotmob_item(item).is_some())
        .count();
    assert!(
        parsed * 2 > items.len(),
        "{parsed}/{} items parsed",
        items.len()
    );
}

/// Live crawl of the FotMob team sitemaps, compared with the recorded
/// catalog (local `testdata/fotmob_teams.json`, not in the repository; skipped
/// when absent). Prints request count, duration and
/// the id/name differences (`--nocapture`).
///
///     cargo test -p fl-sources live_team_index -- --ignored --nocapture
#[tokio::test]
#[ignore = "needs network access"]
async fn live_team_index() {
    use std::collections::BTreeMap;
    use std::sync::Arc;
    use std::sync::atomic::{AtomicUsize, Ordering};

    use crate::http::{BoxFuture, LiveTransport, Response, Transport, TransportError};
    use crate::team_index::{crawl, parse_catalog};
    use crate::test_support::read_fixture_text;

    struct Counting {
        inner: LiveTransport,
        requests: AtomicUsize,
    }
    impl Transport for Counting {
        fn execute<'a>(
            &'a self,
            request: &'a Request,
        ) -> BoxFuture<'a, Result<Response, TransportError>> {
            self.requests.fetch_add(1, Ordering::SeqCst);
            self.inner.execute(request)
        }
    }
    let Some(recorded_text) = read_fixture_text("fotmob_teams.json") else {
        return;
    };
    let transport = Arc::new(Counting {
        inner: LiveTransport::new().expect("rustls client"),
        requests: AtomicUsize::new(0),
    });
    let client = HttpClient::builder()
        .transport(transport.clone())
        .build()
        .expect("client");
    let started = std::time::Instant::now();
    let live = crawl(&client, &mut |done, total| {
        eprintln!("sitemap {done}/{total}")
    })
    .await
    .expect("live crawl");
    eprintln!(
        "crawled {} teams with {} requests in {:.1}s",
        live.len(),
        transport.requests.load(Ordering::SeqCst),
        started.elapsed().as_secs_f64()
    );

    let recorded: BTreeMap<i64, _> = parse_catalog(&recorded_text)
        .expect("recorded catalog parses")
        .into_iter()
        .map(|team| (team.fotmob_id, team))
        .collect();
    let live: BTreeMap<i64, _> = live
        .into_iter()
        .map(|team| (team.fotmob_id, team))
        .collect();
    let added: Vec<_> = live
        .keys()
        .filter(|id| !recorded.contains_key(id))
        .collect();
    let removed: Vec<_> = recorded
        .keys()
        .filter(|id| !live.contains_key(id))
        .collect();
    let renamed: Vec<_> = live
        .iter()
        .filter_map(|(id, team)| {
            let old = recorded.get(id)?;
            (old.slug != team.slug).then(|| format!("{id}: {} -> {}", old.slug, team.slug))
        })
        .collect();
    eprintln!(
        "recorded {} / live {}: {} added, {} removed, {} slug changes",
        recorded.len(),
        live.len(),
        added.len(),
        removed.len(),
        renamed.len()
    );
    eprintln!("added (first 20): {:?}", &added[..added.len().min(20)]);
    eprintln!(
        "removed (first 20): {:?}",
        &removed[..removed.len().min(20)]
    );
    eprintln!(
        "renamed (first 20): {:?}",
        &renamed[..renamed.len().min(20)]
    );
    assert!(live.len() > 5000);
}
