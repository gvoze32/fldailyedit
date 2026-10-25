//! Parity tests against the frozen Python scraper.
//!
//! The former Python implementation (removed) recorded real FotMob responses into
//! the local (`testdata/`, not in the repository) `sources_fotmob/cassette.jsonl.gz`
//! and dumped what the unchanged
//! Python code produces from them (clock pinned to `meta.json`) into
//! `expected/*.json`. These tests replay the same cassette through the Rust
//! code and demand identical results.

use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;

use chrono::{DateTime, Utc};
use serde_json::{Value, json};

use crate::dates::{
    SinceDate, Window, get_transfer_window_range, parse_external_date, parse_external_timestamp,
    parse_iso_date, parse_iso_datetime, previous_window_start, resolve_date_range,
};
use crate::fotmob::cache::TeamPayloadCache;
use crate::fotmob::parse::{
    extract_squad_snapshot, latest_team_match_page, parse_fotmob_item, payload_team_name,
};
use crate::fotmob::{ClubsRequest, FeedRequest, FotmobOptions, FotmobScraper, feed_url};
use crate::http::{HttpClient, ReplayTransport, Request, RetryPolicy};
use crate::model::{ScrapeResult, SquadSnapshot, Transfer, merge_transfers};
use crate::tactics::fetch_tactical_updates;
use crate::test_support::testdata_path;

fn fixture_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../testdata/sources_fotmob")
}

/// True, after printing a skip note, when the local recorded fixtures are absent.
fn fixtures_absent() -> bool {
    testdata_path("sources_fotmob").is_none()
}

fn expected(name: &str) -> Value {
    let path = fixture_dir().join("expected").join(format!("{name}.json"));
    let text = std::fs::read_to_string(&path).unwrap_or_else(|e| panic!("{}: {e}", path.display()));
    serde_json::from_str(&text).unwrap()
}

fn meta() -> (DateTime<Utc>, Vec<i64>) {
    let meta: Value =
        serde_json::from_str(&std::fs::read_to_string(fixture_dir().join("meta.json")).unwrap())
            .unwrap();
    let now = DateTime::parse_from_rfc3339(meta["now_utc"].as_str().unwrap())
        .unwrap()
        .with_timezone(&Utc);
    let clubs = meta["clubs"]
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_i64().unwrap())
        .collect();
    (now, clubs)
}

fn client() -> HttpClient {
    HttpClient::replay(&fixture_dir().join("cassette.jsonl.gz")).unwrap()
}

fn scraper(cache_dir: Option<&Path>) -> FotmobScraper {
    FotmobScraper::new(
        client(),
        FotmobOptions {
            cache_dir: cache_dir.map(Path::to_path_buf),
            club_cooldown: Duration::ZERO,
            ..FotmobOptions::default()
        },
    )
}

fn to_values<T: serde::Serialize>(items: &[T]) -> Vec<Value> {
    items
        .iter()
        .map(|i| serde_json::to_value(i).unwrap())
        .collect()
}

fn result_json(result: &ScrapeResult) -> Value {
    json!({
        "transfers": to_values(&result.transfers),
        "pending_transfers": to_values(&result.pending_transfers),
        "roster_updates": to_values(&result.roster_updates),
        "captain_updates": to_values(&result.captain_updates),
        "squad_snapshots": to_values(&result.squad_snapshots),
    })
}

/// Element-wise comparison with a readable first difference.
fn assert_same(label: &str, actual: &Value, wanted: &Value) {
    match (actual, wanted) {
        (Value::Array(a), Value::Array(w)) => {
            assert_eq!(a.len(), w.len(), "{label}: length");
            for (index, (a, w)) in a.iter().zip(w).enumerate() {
                assert_same(&format!("{label}[{index}]"), a, w);
            }
        }
        (Value::Object(a), Value::Object(w)) => {
            let keys: std::collections::BTreeSet<_> = a.keys().chain(w.keys()).collect();
            for key in keys {
                assert_same(
                    &format!("{label}.{key}"),
                    a.get(key).unwrap_or(&Value::Null),
                    w.get(key).unwrap_or(&Value::Null),
                );
            }
        }
        _ => assert_eq!(actual, wanted, "{label}"),
    }
}

#[tokio::test]
async fn feed_matches_expected() {
    if fixtures_absent() {
        return;
    }
    let (now, _) = meta();
    let scraper = scraper(None);
    let auto = scraper
        .fetch_transfers(&FeedRequest::default(), now)
        .await
        .unwrap();
    assert_same("feed_auto", &result_json(&auto), &expected("feed_auto"));
    let since = FeedRequest {
        since: Some(SinceDate::from("2026-10-20")),
        ..FeedRequest::default()
    };
    let recent = scraper.fetch_transfers(&since, now).await.unwrap();
    assert_same("feed_since", &result_json(&recent), &expected("feed_since"));
    assert_eq!(recent.transfers.len(), 4, "since filter drops older items");
}

#[tokio::test]
async fn every_feed_item_parses_as_expected() {
    if fixtures_absent() {
        return;
    }
    let wanted = expected("feed_items");
    let wanted = wanted.as_array().unwrap();
    let client = client();
    let mut index = 0;
    for page in 1..=3 {
        let request = Request::get(feed_url(page, false));
        let body = client.get_json(&request, &RetryPolicy::NONE).await.unwrap();
        for item in body["transfers"].as_array().unwrap() {
            let actual =
                parse_fotmob_item(item).map_or(Value::Null, |t| serde_json::to_value(t).unwrap());
            assert_same(&format!("feed_items[{index}]"), &actual, &wanted[index]);
            index += 1;
        }
    }
    assert_eq!(index, wanted.len());
}

#[tokio::test]
async fn clubs_match_expected() {
    if fixtures_absent() {
        return;
    }
    let (now, clubs) = meta();
    let dir = tempfile::tempdir().unwrap();
    let scraper = scraper(Some(dir.path()));
    let auto = scraper
        .fetch_clubs(&clubs, &ClubsRequest::default(), now, None)
        .await
        .unwrap();
    assert_same("clubs_auto", &result_json(&auto), &expected("clubs_auto"));
    assert_eq!(
        auto.squad_snapshots.len(),
        4,
        "the sparse club is not a complete snapshot"
    );

    // A second run is served from the fresh cache and must not change anything.
    let cached = scraper
        .fetch_clubs(&clubs, &ClubsRequest::default(), now, None)
        .await
        .unwrap();
    assert_same(
        "clubs_cached",
        &result_json(&cached),
        &expected("clubs_auto"),
    );
    assert_eq!(std::fs::read_dir(dir.path()).unwrap().count(), clubs.len());

    let recent = ClubsRequest {
        since: Some(SinceDate::from("2026-10-20")),
        ..ClubsRequest::default()
    };
    let since = scraper
        .fetch_clubs(&clubs, &recent, now, None)
        .await
        .unwrap();
    assert_same(
        "clubs_since",
        &result_json(&since),
        &expected("clubs_since"),
    );
}

#[tokio::test]
async fn formation_enrichment_matches_expected() {
    if fixtures_absent() {
        return;
    }
    let (now, _) = meta();
    let scraper = scraper(None);
    let cache = TeamPayloadCache::new(None, chrono::Duration::hours(6));
    let wanted = expected("enrichment");
    for (team_id, wanted) in wanted.as_object().unwrap() {
        let team_id: i64 = team_id.parse().unwrap();
        let request = Request::get(format!(
            "https://www.fotmob.com/api/data/teams?id={team_id}"
        ));
        let data = client()
            .get_json(&request, &RetryPolicy::NONE)
            .await
            .unwrap();
        let name = payload_team_name(&data, &team_id.to_string());
        let snapshot = extract_squad_snapshot(&data, team_id, &name);
        assert_eq!(
            Value::Bool(snapshot.complete),
            wanted["complete"],
            "complete {team_id}"
        );
        assert_eq!(
            json!(snapshot.formation),
            wanted["payload_formation"],
            "payload formation {team_id}"
        );
        let page = latest_team_match_page(&data, team_id);
        assert_eq!(
            json!(page),
            wanted["latest_match_page"],
            "latest page {team_id}"
        );
        if !snapshot.complete {
            assert!(wanted["enriched_formation"].is_null());
            continue;
        }
        let found = scraper
            .fetch_latest_team_formation(&data, team_id, &snapshot.club_name, &cache)
            .await
            .unwrap();
        let (formation, url) =
            found.map_or((Value::Null, json!("")), |(f, u)| (json!(f), json!(u)));
        assert_eq!(
            formation, wanted["enriched_formation"],
            "enriched formation {team_id}"
        );
        assert_eq!(url, wanted["enriched_source_url"], "enriched url {team_id}");
    }
    let _ = now;
}

#[tokio::test]
async fn tactics_match_expected() {
    if fixtures_absent() {
        return;
    }
    let (now, _) = meta();
    let clubs = expected("clubs_auto");
    let snapshots: Vec<SquadSnapshot> = clubs["squad_snapshots"]
        .as_array()
        .unwrap()
        .iter()
        .map(|s| SquadSnapshot {
            club_name: s["club_name"].as_str().unwrap().to_owned(),
            team_id_fotmob: s["team_id_fotmob"].as_i64().unwrap(),
            members: vec![],
            source_url: String::new(),
            complete: true,
            starter_members: vec![],
            sub_members: vec![],
            primary_league_id: s["primary_league_id"].as_i64(),
            formation: None,
            formation_source_url: String::new(),
        })
        .collect();
    let dir = tempfile::tempdir().unwrap();
    let cache_path = dir.path().join("tactics.json");
    let updates = fetch_tactical_updates(&client(), &snapshots, &cache_path, now)
        .await
        .unwrap();
    let wanted = expected("tactics");

    let actual: Vec<Value> = updates
        .iter()
        .map(|u| {
            json!({
                "club_name": u.club_name,
                "team_id_fotmob": u.team_id_fotmob,
                "league_id": u.league_id,
                "settings": u.settings.iter().map(|(k, v)| json!([k, v])).collect::<Vec<_>>(),
                "sample_matches": u.sample_matches,
                "source_urls": u.source_urls,
            })
        })
        .collect();
    assert_same("tactics.updates", &Value::Array(actual), &wanted["updates"]);
    assert!(!updates.is_empty());

    let cache: Value =
        serde_json::from_str(&std::fs::read_to_string(&cache_path).unwrap()).unwrap();
    assert_same(
        "tactics.cache",
        &cache["leagues"],
        &wanted["cache"]["leagues"],
    );

    // Warm cache: same answers without any network (empty replay would fail).
    let offline = HttpClient::builder()
        .transport(Arc::new(ReplayTransport::from_responses([])))
        .build()
        .unwrap();
    let again = fetch_tactical_updates(&offline, &snapshots, &cache_path, now)
        .await
        .unwrap();
    assert_eq!(again, updates);
}

#[test]
fn dates_match_expected() {
    if fixtures_absent() {
        return;
    }
    let wanted = expected("dates");
    let text =
        |value: Option<chrono::NaiveDate>| value.map_or(Value::Null, |d| json!(d.to_string()));

    for (input, want) in wanted["iso_date"].as_object().unwrap() {
        assert_eq!(
            &text(parse_iso_date(input)),
            want,
            "parse_iso_date({input:?})"
        );
    }
    for (input, want) in wanted["iso_datetime"].as_object().unwrap() {
        let want = want
            .as_str()
            .map(|s| DateTime::parse_from_rfc3339(s).unwrap().with_timezone(&Utc));
        assert_eq!(
            parse_iso_datetime(input),
            want,
            "parse_iso_datetime({input:?})"
        );
    }
    for (input, want) in wanted["external"].as_object().unwrap() {
        assert_eq!(
            &text(parse_external_date(input)),
            want,
            "parse_external_date({input:?})"
        );
    }
    for pair in wanted["timestamp"].as_array().unwrap() {
        let input = pair[0].as_f64().unwrap();
        assert_eq!(
            text(parse_external_timestamp(input)),
            pair[1],
            "timestamp {input}"
        );
    }
    let day = |s: &str| chrono::NaiveDate::parse_from_str(s, "%Y-%m-%d").unwrap();
    for (key, want) in wanted["previous_window_start"].as_object().unwrap() {
        assert_eq!(
            previous_window_start(day(key)).to_string(),
            want.as_str().unwrap(),
            "previous_window_start {key}"
        );
    }
    for (key, want) in wanted["windows"].as_object().unwrap() {
        let (window, reference) = key.split_once('@').unwrap();
        let (start, end) =
            get_transfer_window_range(Window::parse(window).unwrap(), day(reference));
        assert_eq!(
            json!([start.to_string(), end.map(|e| e.to_string())]),
            *want,
            "window {key}"
        );
    }
    for case in wanted["resolve"].as_array().unwrap() {
        let since = case["since"].as_str().map(SinceDate::from);
        let window = Window::parse(case["window"].as_str().unwrap()).unwrap();
        let result = resolve_date_range(since.as_ref(), window, day(case["ref"].as_str().unwrap()));
        match case.get("error") {
            Some(_) => assert!(result.is_err(), "{case}"),
            None => {
                let (start, end) = result.unwrap_or_else(|e| panic!("{case}: {e}"));
                assert_eq!(
                    json!([start.map(|d| d.to_string()), end.map(|d| d.to_string())]),
                    case["result"],
                    "{case}"
                );
            }
        }
    }
}

#[test]
fn merge_matches_expected() {
    if fixtures_absent() {
        return;
    }
    for case in expected("merge").as_array().unwrap() {
        let input: Vec<Transfer> = serde_json::from_value(case["input"].clone()).unwrap();
        let merged = merge_transfers(input);
        assert_same(
            case["name"].as_str().unwrap(),
            &Value::Array(to_values(&merged)),
            &case["expected"],
        );
    }
}
