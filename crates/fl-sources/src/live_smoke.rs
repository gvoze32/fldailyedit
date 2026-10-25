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
