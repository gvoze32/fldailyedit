//! Fetch flows of the supplemental sources over a scripted transport:
//! request URLs/headers, paging, fallbacks and failure isolation.

mod common;
mod script;

use std::collections::HashMap;
use std::time::Duration;

use common::{assert_same, date, json, transfers, ymd};
use fl_sources::besoccer::fetch_besoccer_transfers;
use fl_sources::dates::{SinceDate, Window};
use fl_sources::http::{Request, TransportError};
use fl_sources::model::{Transfer, VerificationStatus};
use fl_sources::soccerway::{SoccerwayRequest, fetch_soccerway_transfers};
use fl_sources::sofascore::{SOFASCORE_TRANSFER_PAGE_URL, fetch_sofascore_transfers};
use fl_sources::sortitoutsi::{SORTITOUTSI_READER_URL, fetch_sortitoutsi_transfers};
use fl_sources::supplemental::{SourceError, url_with_query};
use fl_sources::transfermarkt::{TransfermarktRequest, fetch_transfermarkt_transfers};
use fl_sources::wikipedia::{WIKIPEDIA_API_URL, fetch_wikipedia_transfers};
use script::{header, ok, scripted, scripted_with_delay, status};
use serde_json::{Value, json};

fn synthetic(source: &str, name: &str) -> String {
    json("synthetic.json.gz")["bodies"][source][name]
        .as_str()
        .expect("synthetic body")
        .to_owned()
}

// ---------------------------------------------------------------- Transfermarkt

const TM_READER: &str = "https://r.jina.ai/https://www.transfermarkt.com/transfers/neuestetransfers/statistik?\
land_id=0&verein_land_id=0&wettbewerb_id=alle&plus=1";

fn tm_request(since: &str, today: &str, max_pages: Option<usize>) -> TransfermarktRequest {
    TransfermarktRequest {
        since: Some(SinceDate::from(since)),
        window: Window::Auto,
        today: ymd(today),
        max_pages,
    }
}

fn page_number(url: &str) -> Option<usize> {
    url.split("page=").nth(1)?.split('&').next()?.parse().ok()
}

#[tokio::test]
async fn transfermarkt_uses_detailed_pages_and_stops_below_cutoff() {
    let (page1, page2) = (
        synthetic("transfermarkt", "detailed"),
        synthetic("transfermarkt", "page2"),
    );
    let (client, log) = scripted(move |request| {
        Ok(ok(if request.url.contains("page=2") {
            page2.clone()
        } else {
            page1.clone()
        }))
    });
    let transfers =
        fetch_transfermarkt_transfers(&client, &tm_request("2026-08-03", "2026-08-04", Some(4)))
            .await
            .expect("fetch");
    let ids: Vec<_> = transfers
        .iter()
        .filter_map(|t| t.transfer_id_transfermarkt)
        .collect();
    assert_eq!(ids, [6481933, 6481832, 6481567]);
    assert_eq!(
        log.urls(),
        [TM_READER.to_owned(), format!("{TM_READER}&page=2")]
    );
    assert!(transfers[0].source_urls[0].ends_with("wettbewerb_id=alle&plus=1"));
    let first = &log.requests()[0];
    assert_eq!(header(first, "X-Retain-Images"), Some("none"));
    assert_eq!(header(first, "X-Cache-Tolerance"), Some("300"));
}

fn renumbered(markdown: &str, page: usize) -> String {
    ["6481933", "6481832", "6481567"]
        .iter()
        .enumerate()
        .fold(markdown.to_owned(), |text, (index, id)| {
            text.replace(id, &(7_000_000 + page * 10 + index + 1).to_string())
        })
}

#[tokio::test]
async fn transfermarkt_continues_past_four_pages_until_old_rows() {
    let detailed = synthetic("transfermarkt", "detailed");
    let old = detailed.replace("03/08/2026", "01/08/2026");
    let (client, log) = scripted(move |request| {
        let page = page_number(&request.url).unwrap_or(1);
        Ok(ok(if page <= 5 {
            renumbered(&detailed, page)
        } else {
            old.clone()
        }))
    });
    let transfers =
        fetch_transfermarkt_transfers(&client, &tm_request("2026-08-03", "2026-08-04", None))
            .await
            .expect("fetch");
    assert_eq!(transfers.len(), 15);
    assert_eq!(log.urls().len(), 6);
    assert!(log.urls()[4].contains("page=5"));
}

#[tokio::test]
async fn transfermarkt_has_no_250_transfer_cap() {
    let detailed = synthetic("transfermarkt", "detailed");
    let (client, _log) = scripted(move |request| {
        Ok(ok(renumbered(
            &detailed,
            page_number(&request.url).unwrap_or(1).min(84),
        )))
    });
    let transfers =
        fetch_transfermarkt_transfers(&client, &tm_request("2026-08-03", "2026-08-04", Some(85)))
            .await
            .expect("fetch");
    assert_eq!(transfers.len(), 252);
    let unique: std::collections::HashSet<_> = transfers
        .iter()
        .map(|t| t.transfer_id_transfermarkt)
        .collect();
    assert_eq!(unique.len(), 252);
}

#[tokio::test]
async fn transfermarkt_continues_past_future_dated_first_page() {
    let future = synthetic("transfermarkt", "detailed").replace("03/08/2026", "04/08/2026");
    let valid = synthetic("transfermarkt", "page2").replace("02/08/2026", "03/08/2026");
    let (client, log) = scripted(move |request| {
        Ok(ok(if request.url.contains("page=2") {
            valid.clone()
        } else {
            future.clone()
        }))
    });
    let transfers =
        fetch_transfermarkt_transfers(&client, &tm_request("2026-08-03", "2026-08-03", Some(2)))
            .await
            .unwrap();
    let ids: Vec<_> = transfers
        .iter()
        .filter_map(|t| t.transfer_id_transfermarkt)
        .collect();
    assert_eq!(ids, [6481688]);
    assert_eq!(log.urls().len(), 2);
}

#[tokio::test]
async fn transfermarkt_stops_when_reader_repeats_future_dated_page() {
    let future = synthetic("transfermarkt", "detailed").replace("03/08/2026", "04/08/2026");
    let (client, log) = scripted(move |_| Ok(ok(future.clone())));
    let transfers =
        fetch_transfermarkt_transfers(&client, &tm_request("2026-08-03", "2026-08-03", None))
            .await
            .unwrap();
    assert!(transfers.is_empty());
    assert_eq!(log.urls().len(), 2);
}

#[tokio::test]
async fn transfermarkt_refreshes_empty_reader_response() {
    let detailed = synthetic("transfermarkt", "detailed");
    let (client, log) = scripted(move |request| {
        Ok(ok(if request.url.contains("fldailyedit_refresh=") {
            detailed.clone()
        } else {
            String::new()
        }))
    });
    let transfers =
        fetch_transfermarkt_transfers(&client, &tm_request("2026-08-03", "2026-08-03", Some(1)))
            .await
            .unwrap();
    assert_eq!(transfers.len(), 3);
    let urls = log.urls();
    assert_eq!(urls.len(), 2);
    assert!(urls[1].contains("fldailyedit_refresh="));
}

#[tokio::test]
async fn transfermarkt_retries_unparseable_first_page() {
    let detailed = synthetic("transfermarkt", "detailed");
    let counter = std::sync::atomic::AtomicUsize::new(0);
    let (client, log) = scripted(move |_| {
        let call = counter.fetch_add(1, std::sync::atomic::Ordering::SeqCst) + 1;
        Ok(ok(if call == 4 {
            detailed.clone()
        } else {
            "not a table".to_owned()
        }))
    });
    let transfers =
        fetch_transfermarkt_transfers(&client, &tm_request("2026-08-03", "2026-08-03", Some(1)))
            .await
            .unwrap();
    assert_eq!(transfers.len(), 3);
    assert_eq!(log.urls().len(), 4);
}

#[tokio::test]
async fn transfermarkt_falls_back_to_german_reader_domain() {
    let localized = synthetic("transfermarkt", "localized");
    let (client, log) = scripted(move |request| {
        Ok(ok(if request.url.contains("transfermarkt.de") {
            localized.clone()
        } else {
            String::new()
        }))
    });
    let transfers =
        fetch_transfermarkt_transfers(&client, &tm_request("2026-09-04", "2026-09-04", Some(1)))
            .await
            .unwrap();
    assert_eq!(transfers.len(), 1);
    assert!(transfers[0].source_urls[0].starts_with("https://www.transfermarkt.de/"));
    let urls = log.urls();
    assert_eq!(urls.len(), 3);
    assert!(urls[0].contains("transfermarkt.com"));
    assert!(urls[1].contains("fldailyedit_refresh="));
    assert!(urls[2].contains("transfermarkt.de"));
}

#[tokio::test]
async fn transfermarkt_later_page_failure_keeps_collected_rows() {
    let detailed = synthetic("transfermarkt", "detailed");
    let (client, _log) = scripted(move |request| {
        if request.url.contains("page=2") {
            Ok(status(429))
        } else {
            Ok(ok(detailed.clone()))
        }
    });
    let transfers =
        fetch_transfermarkt_transfers(&client, &tm_request("2026-08-03", "2026-08-04", Some(4)))
            .await
            .unwrap();
    assert_eq!(transfers.len(), 3);
}

#[tokio::test]
async fn transfermarkt_first_page_outage_is_an_error_not_a_panic() {
    let (client, _log) = scripted(|_| Err(TransportError::Timeout));
    let error =
        fetch_transfermarkt_transfers(&client, &tm_request("2026-08-03", "2026-08-04", Some(2)))
            .await
            .unwrap_err();
    assert!(matches!(error, SourceError::Http(_)), "{error}");

    let (client, _log) = scripted(|_| Ok(status(503)));
    assert!(
        fetch_transfermarkt_transfers(&client, &tm_request("2026-08-03", "2026-08-04", Some(2)))
            .await
            .is_err()
    );
}

#[tokio::test]
async fn transfermarkt_rejects_invalid_since_without_requesting() {
    let (client, log) = scripted(|_| Ok(status(500)));
    let error =
        fetch_transfermarkt_transfers(&client, &tm_request("not-a-date", "2026-08-04", None))
            .await
            .unwrap_err();
    assert!(matches!(error, SourceError::Date(_)), "{error}");
    assert!(log.urls().is_empty());
}

fn tm_row(player: i64, transfer: i64, date: &str) -> String {
    format!(
        "| [P{player}](https://www.transfermarkt.com/p/profil/spieler/{player} \"P{player}\") Centre-Back | 25 | Spain | \
         [Old](https://www.transfermarkt.com/old/startseite/verein/10/saison_id/2026 \"Old FC\") | \
         [New](https://www.transfermarkt.com/new/startseite/verein/20/saison_id/2026 \"New FC\") | {date} | €1.00m | \
         [free transfer](https://www.transfermarkt.com/jumplist/transfers/spieler/{player}/transfer_id/{transfer}) |\n"
    )
}

const TM_HEADER: &str = "| Player | Age | Nat. | Left | Joined | Transfer date | Market value | Fee |\n\
| --- | --- | --- | --- | --- | --- | --- | --- |\n";

#[tokio::test]
async fn transfermarkt_honors_window_end_date() {
    // Winter window: Jan 1 – Feb 28. The end date bounds the window, not just "today".
    let page = format!(
        "{TM_HEADER}{}{}{}",
        tm_row(1, 101, "05/03/2026"),
        tm_row(2, 102, "10/02/2026"),
        tm_row(3, 103, "15/01/2026"),
    );
    let (client, _log) = scripted(move |request| {
        Ok(ok(if request.url.contains("page=") {
            String::new()
        } else {
            page.clone()
        }))
    });
    let request = TransfermarktRequest {
        since: None,
        window: Window::Winter,
        today: ymd("2026-03-20"),
        max_pages: Some(2),
    };
    let transfers = fetch_transfermarkt_transfers(&client, &request)
        .await
        .unwrap();
    let ids: Vec<_> = transfers
        .iter()
        .filter_map(|t| t.transfer_id_transfermarkt)
        .collect();
    assert_eq!(
        ids,
        [102, 103],
        "the 5 March row is after the winter window"
    );
}

// ---------------------------------------------------------------- BeSoccer / Sortitoutsi

#[tokio::test]
async fn besoccer_uses_the_spanish_reader_route() {
    let feed = synthetic("besoccer", "feed");
    let (client, log) = scripted(move |_| Ok(ok(feed.clone())));
    let transfers = fetch_besoccer_transfers(
        &client,
        Some(&SinceDate::from("2026-09-01")),
        Window::Auto,
        ymd("2026-09-02"),
    )
    .await
    .unwrap();
    assert_eq!(transfers.len(), 2);
    assert!(
        transfers
            .iter()
            .all(|t| t.verification_status == VerificationStatus::Corroborator)
    );
    assert_eq!(
        log.urls(),
        ["https://r.jina.ai/https://www.besoccer.es/fichajes"]
    );
    assert_eq!(
        header(&log.requests()[0], "Accept"),
        Some("text/plain, */*;q=0.5")
    );

    let (client, _log) = scripted(|_| Ok(status(500)));
    assert!(
        fetch_besoccer_transfers(&client, None, Window::Auto, ymd("2026-09-02"))
            .await
            .is_err()
    );
}

#[tokio::test]
async fn sortitoutsi_requests_the_reader_page_and_bounds_by_today() {
    let page = synthetic("sortitoutsi", "mixed");
    let (client, log) = scripted(move |_| Ok(ok(page.clone())));
    let signals = fetch_sortitoutsi_transfers(&client, None, ymd("2026-08-03"))
        .await
        .unwrap();
    assert_eq!(signals.len(), 1);
    assert_eq!(signals[0].player_id_sortitoutsi, Some(28005568));
    assert_eq!(log.urls(), [SORTITOUTSI_READER_URL]);

    // Events after "today" are not effective yet.
    let signals = fetch_sortitoutsi_transfers(&client, None, ymd("2026-08-02"))
        .await
        .unwrap();
    assert!(signals.is_empty());

    // An unparseable since is "no lower bound".
    let signals = fetch_sortitoutsi_transfers(
        &client,
        Some(&SinceDate::from("garbage")),
        ymd("2026-08-03"),
    )
    .await
    .unwrap();
    assert_eq!(signals.len(), 1);
}

// ---------------------------------------------------------------- Sofascore

fn next_data_page(transfers: Value) -> String {
    let data = json!({"props": {"pageProps": {"fallbackData": [{"transfers": transfers}]}}});
    format!("<script id=\"__NEXT_DATA__\" type=\"application/json\">{data}</script>")
}

fn sofa_item(id: i64, player: &str, from: &str, to: &str, day: &str) -> Value {
    json!({"id": id, "player": {"name": player, "position": "F"}, "transferFrom": {"name": from},
           "transferTo": {"name": to}, "type": 3, "transferDate": day})
}

#[tokio::test]
async fn sofascore_scrapes_global_page_and_filters_relevant_clubs() {
    let page = next_data_page(json!([
        sofa_item(1, "Gabriel Martinelli", "Arsenal", "Al-Hilal", "2026-09-02"),
        sofa_item(2, "Other Player", "Chelsea", "Other Club", "2026-09-02"),
    ]));
    let (client, log) = scripted(move |_| Ok(ok(page.clone())));
    let clubs = vec!["Arsenal".to_owned()];
    let found = fetch_sofascore_transfers(
        &client,
        Some(&SinceDate::from("2026-09-02")),
        Window::Auto,
        ymd("2026-09-02"),
        &clubs,
    )
    .await
    .unwrap();
    assert_eq!(found.len(), 1);
    assert_eq!(
        (found[0].player_name.as_str(), found[0].from_club.as_str()),
        ("Gabriel Martinelli", "Arsenal")
    );
    assert_eq!(found[0].source_urls, [SOFASCORE_TRANSFER_PAGE_URL]);
    assert_eq!(log.urls().len(), 1);

    // No clubs of interest: nothing is requested.
    let (client, log) = scripted(|_| Ok(status(500)));
    let none = fetch_sofascore_transfers(&client, None, Window::Auto, ymd("2026-09-02"), &[])
        .await
        .unwrap();
    assert!(none.is_empty() && log.urls().is_empty());
}

const JINA_PAGE: &str = "https://r.jina.ai/https://www.sofascore.com/football/player-transfers";
const APP_PAGE: &str = "https://sofascore.app/football/player-transfers";
const APP_WWW_PAGE: &str = "https://www.sofascore.app/football/player-transfers";
const API: &str = "https://api.sofascore.app/api/v1/transfer?page=1&sort=-transferFee";
const API_APP: &str = "https://sofascore.app/api/v1/transfer?page=1&sort=-transferFee";

async fn sofascore_urls(
    responder: impl Fn(&Request) -> Option<String> + Send + Sync + 'static,
) -> (Vec<Request>, Result<Vec<Transfer>, SourceError>) {
    let (client, log) =
        scripted(move |request| Ok(responder(request).map_or_else(|| status(403), ok)));
    let clubs = vec!["Old FC".to_owned(), "Parent FC".to_owned()];
    let result =
        fetch_sofascore_transfers(&client, None, Window::Auto, ymd("2026-09-03"), &clubs).await;
    (log.requests(), result)
}

#[tokio::test]
async fn sofascore_falls_back_to_jina_then_app_hostname_then_api() {
    let page = next_data_page(json!([sofa_item(
        1,
        "Jina Player",
        "Old FC",
        "New FC",
        "2026-09-02"
    )]));

    // Direct page blocked, reader answers.
    let served = page.clone();
    let (requests, result) =
        sofascore_urls(move |r| (r.url == JINA_PAGE).then(|| served.clone())).await;
    assert_eq!(result.unwrap().len(), 1);
    let urls: Vec<&str> = requests.iter().map(|r| r.url.as_str()).collect();
    assert_eq!(urls, [SOFASCORE_TRANSFER_PAGE_URL, JINA_PAGE]);
    assert_eq!(header(&requests[1], "X-Respond-With"), Some("html"));
    assert_eq!(
        header(&requests[1], "Accept"),
        Some("text/html, application/xhtml+xml, text/plain, */*")
    );
    assert!(
        requests.iter().all(|r| !r.follow_redirects),
        "redirects are never followed"
    );

    // App hostname answers after the reader.
    let served = page.clone();
    let (requests, result) =
        sofascore_urls(move |r| (r.url == APP_PAGE).then(|| served.clone())).await;
    assert_eq!(result.unwrap().len(), 1);
    let urls: Vec<&str> = requests.iter().map(|r| r.url.as_str()).collect();
    assert_eq!(urls, [SOFASCORE_TRANSFER_PAGE_URL, JINA_PAGE, APP_PAGE]);

    // Only the JSON API answers: every HTML route is tried (app host twice).
    let api = json!({"transfers": [sofa_item(42, "API Player", "Old FC", "New FC", "2026-09-02")]})
        .to_string();
    let (requests, result) = sofascore_urls(move |r| (r.url == API).then(|| api.clone())).await;
    assert_eq!(result.unwrap().len(), 1);
    let urls: Vec<&str> = requests.iter().map(|r| r.url.as_str()).collect();
    assert_eq!(
        urls,
        [
            SOFASCORE_TRANSFER_PAGE_URL,
            JINA_PAGE,
            APP_PAGE,
            APP_PAGE,
            APP_WWW_PAGE,
            API
        ]
    );
    let _ = API_APP;
}

#[tokio::test]
async fn sofascore_blocked_everywhere_is_a_source_failure() {
    let (requests, result) = sofascore_urls(|_| None).await;
    let error = result.unwrap_err();
    assert!(
        matches!(&error, SourceError::Unavailable(message) if message.contains("HTTP 403")),
        "{error}"
    );
    let urls: Vec<&str> = requests.iter().map(|r| r.url.as_str()).collect();
    assert_eq!(
        urls,
        [
            SOFASCORE_TRANSFER_PAGE_URL,
            JINA_PAGE,
            APP_PAGE,
            APP_PAGE,
            APP_WWW_PAGE,
            API,
            API_APP
        ]
    );
}

// ---------------------------------------------------------------- Soccerway

const SOCCERWAY_FEED_URL: &str = "https://global.flashscore.ninja/2020/x/feed/tetr_current-id_1_1";

fn search_hit(id: &str, slug: &str, name: &str) -> String {
    json!([{"id": id, "url": slug, "name": name, "type": {"id": 2}, "sport": {"id": 1}, "gender": {"id": 1}}]).to_string()
}

#[tokio::test]
async fn soccerway_resolves_relevant_club_and_reads_feed() {
    let feed = synthetic("soccerway", "feed");
    let (client, log) = scripted(move |request| {
        if request
            .url
            .starts_with("https://s.livesport.services/api/v2/search/")
        {
            Ok(ok(search_hit("current-id", "current-fc", "Current FC")))
        } else {
            Ok(ok(feed.clone()))
        }
    });
    let mut request = SoccerwayRequest::new(ymd("2026-08-04"), vec!["Current FC".to_owned()]);
    request.since = Some(SinceDate::from("2026-08-03"));
    let transfers = fetch_soccerway_transfers(&client, &request).await.unwrap();
    let names: Vec<_> = transfers.iter().map(|t| t.player_name.as_str()).collect();
    assert_eq!(names, ["Ada Example", "Loan Example"]);

    let requests = log.requests();
    assert_eq!(
        requests[0].url,
        "https://s.livesport.services/api/v2/search/?q=Current+FC&lang-id=1&type-ids=2&project-id=2020&project-type-id=1&sport-ids=1"
    );
    assert_eq!(requests[1].url, SOCCERWAY_FEED_URL);
    assert_eq!(header(&requests[1], "X-Fsign"), Some("SW9D1eZo"));
    assert_eq!(
        header(&requests[1], "Referer"),
        Some("https://www.soccerway.com/")
    );
    assert_eq!(requests.len(), 2);
}

#[tokio::test]
async fn soccerway_skips_unresolvable_and_failing_clubs() {
    let feed = synthetic("soccerway", "feed");
    let (client, _log) = scripted(move |request| {
        let url = &request.url;
        if url.contains("q=Broken") {
            Ok(status(500))
        } else if url.contains("q=Unknown") {
            Ok(ok("[]"))
        } else if url.contains("/search/") {
            Ok(ok(search_hit("current-id", "current-fc", "Current FC")))
        } else {
            Ok(ok(feed.clone()))
        }
    });
    let clubs = [
        "Broken",
        "Unknown",
        "Free Agent",
        "Current FC",
        "current fc",
    ]
    .map(str::to_owned)
    .to_vec();
    let mut request = SoccerwayRequest::new(ymd("2026-08-04"), clubs);
    request.since = Some(SinceDate::from("2026-08-03"));
    let transfers = fetch_soccerway_transfers(&client, &request).await.unwrap();
    assert_eq!(transfers.len(), 2);
}

#[tokio::test]
async fn soccerway_source_deadline_keeps_completed_routes() {
    let feed = synthetic("soccerway", "feed");
    let (client, _log) = scripted_with_delay(
        move |request| {
            let url = &request.url;
            if url.contains("/search/") {
                let name = if url.contains("q=Slow") {
                    "Slow"
                } else {
                    "Current FC"
                };
                let id = if name == "Slow" {
                    "slow-id"
                } else {
                    "current-id"
                };
                Ok(ok(search_hit(
                    id,
                    &name.to_lowercase().replace(' ', "-"),
                    name,
                )))
            } else {
                Ok(ok(feed.clone()))
            }
        },
        |request| {
            if request.url.contains("slow-id") {
                Duration::from_secs(5)
            } else {
                Duration::ZERO
            }
        },
    );
    let mut request = SoccerwayRequest::new(
        ymd("2026-08-04"),
        vec!["Current FC".to_owned(), "Slow".to_owned()],
    );
    request.since = Some(SinceDate::from("2026-08-03"));
    request.deadline = Duration::from_millis(300);
    let started = std::time::Instant::now();
    let transfers = fetch_soccerway_transfers(&client, &request).await.unwrap();
    assert!(
        started.elapsed() < Duration::from_secs(3),
        "deadline cut the slow club off"
    );
    assert_eq!(transfers.len(), 2, "only the fast club's routes");
    assert!(
        transfers
            .iter()
            .all(|t| t.to_club == "Current FC" || t.from_club == "Current FC")
    );
}

// ---------------------------------------------------------------- Wikipedia

fn query_params(url: &str) -> HashMap<String, String> {
    reqwest::Url::parse(url)
        .expect("url")
        .query_pairs()
        .map(|(k, v)| (k.into_owned(), v.into_owned()))
        .collect()
}

fn category_page(members: &[Value], first: bool) -> Value {
    let chunk: Vec<&Value> = if first {
        members[..20].iter().collect()
    } else {
        members[20..].iter().collect()
    };
    let mut payload = json!({"batchcomplete": "", "query": {"categorymembers": chunk}});
    if first {
        payload["continue"] = json!({"cmcontinue": "page|4f4c|2", "continue": "-||"});
    }
    payload
}

fn revision_page(pages: &[(String, Value)], first: bool) -> Value {
    let chunk: serde_json::Map<String, Value> = (if first { &pages[..3] } else { &pages[3..] })
        .iter()
        .cloned()
        .collect();
    let mut payload = json!({"batchcomplete": "", "query": {"pages": chunk}});
    if first {
        payload["continue"] = json!({"rvcontinue": "123|456", "continue": "||"});
    }
    payload
}

#[tokio::test]
async fn wikipedia_fetch_follows_category_and_page_flows() {
    let category = json("wikipedia/category.json")[0].clone();
    let members: Vec<Value> = category["query"]["categorymembers"]
        .as_array()
        .unwrap()
        .clone();
    let revisions = json("wikipedia/revisions.json")[0].clone();
    let pages: Vec<(String, Value)> = revisions["query"]["pages"]
        .as_object()
        .unwrap()
        .iter()
        .map(|(k, v)| (k.clone(), v.clone()))
        .collect();
    let expansion = json("wikipedia/expandtemplates.json");

    for flow in json("expected/wikipedia_flow.json.gz").as_array().unwrap() {
        let failing: Vec<String> = flow["failing_categories"]
            .as_array()
            .unwrap()
            .iter()
            .map(|c| c.as_str().unwrap().to_owned())
            .collect();
        let (members, pages, expansion) = (members.clone(), pages.clone(), expansion.clone());
        let (client, log) = scripted(move |request| {
            assert!(
                request.url.starts_with(WIKIPEDIA_API_URL),
                "{}",
                request.url
            );
            let params = query_params(&request.url);
            let body = if params["action"] == "expandtemplates" {
                expansion.clone()
            } else if params.get("list").map(String::as_str) == Some("categorymembers") {
                if failing.contains(&params["cmtitle"]) {
                    json!({"error": {"code": "boom", "info": "scripted"}})
                } else {
                    category_page(&members, !params.contains_key("cmcontinue"))
                }
            } else {
                revision_page(&pages, !params.contains_key("rvcontinue"))
            };
            Ok(ok(body.to_string()))
        });

        let since = flow["since"].as_str().map(SinceDate::from);
        let window = Window::parse(flow["window"].as_str().unwrap()).unwrap();
        let actual = fetch_wikipedia_transfers(
            &client,
            since.as_ref(),
            window,
            date(&flow["today"]).unwrap(),
        )
        .await
        .unwrap();
        let name = flow["name"].as_str().unwrap();
        assert_same(
            &format!("wikipedia flow {name}"),
            &actual,
            &transfers(&flow["transfers"]),
        );

        let expected_urls: Vec<String> = flow["calls"]
            .as_array()
            .unwrap()
            .iter()
            .map(|call| {
                let mut query = vec![
                    ("format".to_owned(), "json".to_owned()),
                    ("maxlag".to_owned(), "5".to_owned()),
                ];
                query.extend(call.as_array().unwrap().iter().map(|p| {
                    (
                        p[0].as_str().unwrap().to_owned(),
                        p[1].as_str().unwrap().to_owned(),
                    )
                }));
                url_with_query(WIKIPEDIA_API_URL, &query)
            })
            .collect();
        assert_eq!(log.urls(), expected_urls, "request sequence of flow {name}");
        assert!(
            log.requests().iter().all(
                |r| header(r, "User-Agent").is_some_and(|ua| ua.starts_with("FLDailyEdit/0.1"))
            )
        );
    }
}

#[tokio::test]
async fn wikipedia_api_error_payload_is_not_treated_as_empty() {
    // A non-maxlag API error fails the category, which is logged and skipped:
    // the fetch yields nothing rather than inventing data.
    let (client, log) = scripted(|_| {
        Ok(ok(
            json!({"error": {"code": "readonly", "info": "no"}}).to_string()
        ))
    });
    let found = fetch_wikipedia_transfers(&client, None, Window::Summer, ymd("2026-10-25"))
        .await
        .unwrap();
    assert!(found.is_empty());
    assert_eq!(log.urls().len(), 1, "no retry for a non-maxlag error");
}

#[tokio::test]
async fn wikipedia_retries_maxlag_and_rate_limits() {
    let members = json!({"query": {"categorymembers": []}}).to_string();
    let calls = std::sync::atomic::AtomicUsize::new(0);
    let (client, log) = scripted(move |_| {
        let call = calls.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        let mut response = match call {
            0 => ok(json!({"error": {"code": "maxlag", "info": "lagged"}}).to_string()),
            1 => status(429),
            _ => ok(members.clone()),
        };
        response
            .headers
            .insert("retry-after".to_owned(), "0".to_owned());
        Ok(response)
    });
    let found = fetch_wikipedia_transfers(&client, None, Window::Summer, ymd("2026-10-25"))
        .await
        .unwrap();
    assert!(found.is_empty());
    assert_eq!(
        log.urls().len(),
        3,
        "maxlag then 429 then success, all for the same category request"
    );
}

// ---------------------------------------------------------------- misc

#[test]
fn expected_fixture_shapes_are_loadable() {
    // Guards the fixture schema the tests above rely on.
    let flows = json("expected/wikipedia_flow.json.gz");
    assert_eq!(flows.as_array().unwrap().len(), 3);
    let all: Vec<Transfer> = transfers(&flows[0]["transfers"]);
    assert!(!all.is_empty());
}
