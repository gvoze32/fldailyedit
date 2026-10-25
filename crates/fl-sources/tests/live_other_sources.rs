//! One live request per supplemental source. Network-dependent, so ignored by
//! default: `cargo test -p fl-sources --test live_other_sources -- --ignored --nocapture`.

use chrono::Utc;
use fl_sources::besoccer::fetch_besoccer_transfers;
use fl_sources::dates::{SinceDate, Window};
use fl_sources::http::HttpClient;
use fl_sources::soccerway::{SoccerwayRequest, fetch_soccerway_transfers};
use fl_sources::sofascore::fetch_sofascore_transfers;
use fl_sources::sortitoutsi::fetch_sortitoutsi_transfers;
use fl_sources::transfermarkt::{TransfermarktRequest, fetch_transfermarkt_transfers};
use fl_sources::wikipedia::fetch_wikipedia_transfers;

fn client() -> HttpClient {
    HttpClient::builder()
        .transport(std::sync::Arc::new(
            fl_sources::http::LiveTransport::new().expect("transport"),
        ))
        .default_timeout(std::time::Duration::from_secs(60))
        .build()
        .expect("client")
}

fn today() -> chrono::NaiveDate {
    Utc::now().date_naive()
}

#[tokio::test]
#[ignore = "network"]
async fn live_transfermarkt_first_page() {
    let request = TransfermarktRequest {
        since: Some(SinceDate::Date(today() - chrono::Duration::days(30))),
        window: Window::Auto,
        today: today(),
        max_pages: Some(1),
    };
    let found = fetch_transfermarkt_transfers(&client(), &request).await;
    println!("transfermarkt: {:?}", found.as_ref().map(Vec::len));
    found.expect("transfermarkt reachable");
}

#[tokio::test]
#[ignore = "network"]
async fn live_wikipedia_summer_categories() {
    let found = fetch_wikipedia_transfers(&client(), None, Window::Summer, today())
        .await
        .expect("wikipedia");
    println!("wikipedia: {} routes", found.len());
    assert!(!found.is_empty());
}

#[tokio::test]
#[ignore = "network"]
async fn live_besoccer_feed() {
    let found = fetch_besoccer_transfers(&client(), None, Window::Auto, today())
        .await
        .expect("besoccer");
    println!("besoccer: {} routes", found.len());
}

#[tokio::test]
#[ignore = "network"]
async fn live_sortitoutsi_feed() {
    let found = fetch_sortitoutsi_transfers(&client(), None, today())
        .await
        .expect("sortitoutsi");
    println!("sortitoutsi: {} signals", found.len());
}

#[tokio::test]
#[ignore = "network"]
async fn live_soccerway_one_club() {
    let request = SoccerwayRequest::new(today(), vec!["Chelsea".to_owned()]);
    let found = fetch_soccerway_transfers(&client(), &request)
        .await
        .expect("soccerway");
    println!("soccerway: {} routes", found.len());
}

#[tokio::test]
#[ignore = "network"]
async fn live_sofascore_global_page() {
    let clubs = vec!["Chelsea".to_owned(), "Manchester City".to_owned()];
    let found = fetch_sofascore_transfers(&client(), None, Window::Auto, today(), &clubs).await;
    println!(
        "sofascore: {:?}",
        found.as_ref().map(Vec::len).map_err(ToString::to_string)
    );
}
