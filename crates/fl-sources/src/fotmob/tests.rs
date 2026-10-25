//! Behavioral tests of the FotMob scraper on synthetic payloads; real-data
//! parity lives in `parity_fotmob.rs`.

use std::collections::{HashMap, VecDeque};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use chrono::{DateTime, Utc};
use serde_json::{Value, json};

use super::parse::{
    extract_captain, extract_shirt_number_updates, extract_squad_members, extract_squad_snapshot,
    extract_transfers_from_team_data, formation_from_match_page, latest_team_match_page,
    parse_fotmob_item,
};
use super::{ClubsRequest, FeedRequest, FotmobError, FotmobOptions, FotmobScraper, feed_url};
use crate::dates::{SinceDate, Window};
use crate::http::{BoxFuture, HttpClient, Request, Response, Transport, TransportError};
use crate::model::{Transfer, TransferType, merge_transfers};

fn now() -> DateTime<Utc> {
    DateTime::parse_from_rfc3339("2026-08-03T12:00:00Z")
        .unwrap()
        .with_timezone(&Utc)
}

fn day(text: &str) -> chrono::NaiveDate {
    chrono::NaiveDate::parse_from_str(text, "%Y-%m-%d").unwrap()
}

// ---------------------------------------------------------------------------
// Scripted transport
// ---------------------------------------------------------------------------

type Outcome = Result<Response, TransportError>;

fn json_response(status: u16, body: &Value) -> Outcome {
    Ok(Response {
        status,
        headers: Default::default(),
        body: body.to_string().into_bytes(),
    })
}

fn ok(body: &Value) -> Outcome {
    json_response(200, body)
}

fn status(code: u16) -> Outcome {
    json_response(code, &json!({}))
}

/// URL → queue of outcomes (the last one repeats); records every request.
#[derive(Default)]
struct Script {
    routes: Mutex<HashMap<String, VecDeque<Outcome>>>,
    requests: Mutex<Vec<Request>>,
}

impl Script {
    fn new(routes: Vec<(String, Vec<Outcome>)>) -> Arc<Self> {
        Arc::new(Self {
            routes: Mutex::new(
                routes
                    .into_iter()
                    .map(|(url, outcomes)| (url, outcomes.into()))
                    .collect(),
            ),
            requests: Mutex::new(Vec::new()),
        })
    }

    fn count(&self, url: &str) -> usize {
        self.requests
            .lock()
            .unwrap()
            .iter()
            .filter(|r| r.url == url)
            .count()
    }

    fn total(&self) -> usize {
        self.requests.lock().unwrap().len()
    }
}

impl Transport for Script {
    fn execute<'a>(&'a self, request: &'a Request) -> BoxFuture<'a, Outcome> {
        Box::pin(async move {
            self.requests.lock().unwrap().push(request.clone());
            let mut routes = self.routes.lock().unwrap();
            let Some(queue) = routes.get_mut(&request.url) else {
                return Err(TransportError::CassetteMiss(request.url.clone()));
            };
            if queue.len() > 1 {
                queue.pop_front().unwrap()
            } else {
                queue.front().cloned().unwrap()
            }
        })
    }
}

fn scraper_over(transport: Arc<dyn Transport>, options: FotmobOptions) -> FotmobScraper {
    let http = HttpClient::builder()
        .transport(transport)
        .skip_sleeps(true)
        .build()
        .unwrap();
    FotmobScraper::new(
        http,
        FotmobOptions {
            club_cooldown: Duration::ZERO,
            ..options
        },
    )
}

fn feed_scraper(pages: Vec<(u32, Vec<Outcome>)>) -> (FotmobScraper, Arc<Script>) {
    let script = Script::new(
        pages
            .into_iter()
            .map(|(page, outcomes)| (feed_url(page, false), outcomes))
            .collect(),
    );
    (
        scraper_over(script.clone(), FotmobOptions::default()),
        script,
    )
}

fn deal(name: &str, from: &str, to: &str, date: &str) -> Value {
    json!({"name": name, "fromClub": from, "toClub": to, "transferDate": date})
}

fn names(transfers: &[Transfer]) -> Vec<&str> {
    transfers.iter().map(|t| t.player_name.as_str()).collect()
}

// ---------------------------------------------------------------------------
// Feed items
// ---------------------------------------------------------------------------

#[test]
fn parses_free_transfer_item() {
    let item = json!({
        "name": "Kylian Mbappé", "playerId": 174119, "position": {"label": "cf", "key": "forward_short"},
        "fromClub": "Paris Saint-Germain", "fromClubFullName": "Paris Saint-Germain", "fromClubId": 9843,
        "toClub": "Real Madrid", "toClubFullName": "Real Madrid", "toClubId": 8633,
        "fee": {"feeText": "Free"}, "marketValue": 180_000_000, "onLoan": false, "contractExtension": false,
        "transferDate": "2024-07-01",
    });
    let t = parse_fotmob_item(&item).unwrap();
    assert_eq!(
        (t.position.as_str(), t.transfer_type, t.fee.as_str()),
        ("CF", TransferType::FreeTransfer, "Free")
    );
    assert_eq!(
        (t.from_club_id_fotmob, t.player_id_fotmob, t.market_value),
        (Some(9843), Some(174119), 180_000_000)
    );
    assert!(!t.is_loan);
    assert_eq!(t.date, "2024-07-01");
}

#[test]
fn loan_end_of_loan_and_fee_text_rules() {
    let loan = parse_fotmob_item(&json!({"name": "A", "fromClub": "X", "toClub": "Y", "onLoan": true, "fee": {"feeText": "loan"}})).unwrap();
    assert_eq!(
        (loan.transfer_type, loan.is_loan),
        (TransferType::Loan, true)
    );
    let back = parse_fotmob_item(&json!({"name": "A", "fromClub": "X", "toClub": "Y", "onLoan": true, "fee": {"feeText": "End of loan"}})).unwrap();
    assert_eq!(
        (back.transfer_type, back.is_loan),
        (TransferType::EndOfLoan, false)
    );
    let paid = parse_fotmob_item(&json!({"name": "A", "fromClub": "X", "toClub": "Y", "fee": {"feeText": "fee", "value": 25_000_000}})).unwrap();
    assert_eq!(
        (paid.transfer_type, paid.fee.as_str()),
        (TransferType::Transfer, "€25,000,000")
    );
}

#[test]
fn extensions_and_nameless_items_are_skipped() {
    assert!(parse_fotmob_item(&json!({"name": "Saka", "fromClub": "Arsenal", "toClub": "Arsenal", "contractExtension": true})).is_none());
    assert!(parse_fotmob_item(&json!({"name": "", "fromClub": "PSG", "toClub": "Real"})).is_none());
    assert!(parse_fotmob_item(&json!({"name": "X"})).is_none());
    assert!(parse_fotmob_item(&json!("not an object")).is_none());
}

#[test]
fn non_club_labels_become_free_agent_on_both_sides() {
    // "retired"/"career break" labels are normalized on both sides, not just the destination.
    let t =
        parse_fotmob_item(&json!({"name": "A", "fromClub": "Retired", "toClub": "Career break"}))
            .unwrap();
    assert_eq!(
        (t.from_club.as_str(), t.to_club.as_str()),
        ("Free Agent", "Free Agent")
    );
    assert_eq!(t.transfer_type, TransferType::FreeTransfer);
    let t = parse_fotmob_item(&json!({"name": "A", "fromClub": "Without club", "toClub": "Y"}))
        .unwrap();
    assert_eq!(t.from_club, "Free Agent");
}

#[test]
fn club_ids_are_kept_raw_and_unparsable_ids_are_unknown() {
    // FotMob sends -1 for "no club": kept raw (reports print it);
    // merge_transfers treats non-positive ids as unknown.
    let t = parse_fotmob_item(&json!({
        "name": "A", "fromClub": "X", "toClub": "Y", "fromClubId": -1, "toClubId": "8633", "playerId": "oops",
    }))
    .unwrap();
    assert_eq!(
        (
            t.from_club_id_fotmob,
            t.to_club_id_fotmob,
            t.player_id_fotmob
        ),
        (Some(-1), Some(8633), None)
    );
}

#[test]
fn merge_treats_non_positive_club_ids_as_unknown() {
    let unknown = Transfer {
        from_club_id_fotmob: Some(-1),
        ..transfer("P", "Leça FC", "Alverca", "2026-07-13")
    };
    let known = Transfer {
        from_club_id_fotmob: Some(77),
        to_club_id_fotmob: Some(5),
        ..transfer("P", "Leça FC", "Alverca", "2026-07-13")
    };
    let merged = merge_transfers(vec![unknown, known]);
    assert_eq!(merged.len(), 1);
    assert_eq!(merged[0].from_club_id_fotmob, Some(77));
}

#[test]
fn non_string_fields_never_panic() {
    let t = parse_fotmob_item(&json!({
        "name": 12, "fromClub": ["x"], "toClub": "Y", "position": 5, "fee": 7, "marketValue": "abc", "transferDate": null,
    }));
    let t = t.unwrap();
    assert_eq!(
        (t.date.as_str(), t.market_value, t.fee.as_str()),
        ("", 0, "")
    );
}

// ---------------------------------------------------------------------------
// Feed pagination and window
// ---------------------------------------------------------------------------

#[tokio::test]
async fn feed_rejects_schema_drift() {
    for page in [
        json!(["not", "an", "object"]),
        json!({"transfers": {"unexpected": "object"}}),
        json!({"transfers": [{"unexpected": "schema"}]}),
    ] {
        let (scraper, _) = feed_scraper(vec![(1, vec![ok(&page)])]);
        let error = scraper
            .fetch_transfers(&FeedRequest::default(), now())
            .await
            .unwrap_err();
        assert!(
            matches!(error, FotmobError::Incomplete(_)),
            "{page}: {error}"
        );
    }
}

#[tokio::test]
async fn feed_failure_mid_pagination_rejects_partial_results() {
    let (scraper, script) = feed_scraper(vec![
        (
            1,
            vec![ok(
                &json!({"transfers": [deal("Partial Deal", "A", "B", "2026-08-01")]}),
            )],
        ),
        (2, vec![status(503)]),
    ]);
    let error = scraper
        .fetch_transfers(&FeedRequest::default(), now())
        .await
        .unwrap_err();
    assert!(error.to_string().contains("page 2"), "{error}");
    assert_eq!(
        script.count(&feed_url(2, false)),
        3,
        "three attempts before giving up"
    );
}

#[tokio::test]
async fn feed_retries_transient_page_failure() {
    let (scraper, _) = feed_scraper(vec![
        (
            1,
            vec![ok(
                &json!({"transfers": [deal("First Deal", "A", "B", "2026-08-01")]}),
            )],
        ),
        (
            2,
            vec![
                status(429),
                ok(&json!({"transfers": [deal("Retried Deal", "A", "B", "2026-08-01")]})),
            ],
        ),
        (3, vec![ok(&json!({"transfers": []}))]),
    ]);
    let request = FeedRequest {
        window: Window::All,
        ..FeedRequest::default()
    };
    let result = scraper.fetch_transfers(&request, now()).await.unwrap();
    assert_eq!(names(&result.transfers), ["First Deal", "Retried Deal"]);
}

#[tokio::test]
async fn feed_does_not_stop_on_an_old_item() {
    let (scraper, _) = feed_scraper(vec![
        (
            1,
            vec![ok(&json!({"transfers": [
                deal("Recently Corrected Old Deal", "A", "B", "2026-01-01"),
                deal("Current Page Deal", "A", "B", "2026-08-01"),
            ]}))],
        ),
        (
            2,
            vec![ok(
                &json!({"transfers": [deal("Next Page Deal", "C", "D", "2026-08-02")]}),
            )],
        ),
        (3, vec![ok(&json!({"transfers": []}))]),
    ]);
    let request = FeedRequest {
        since: Some(SinceDate::from("2026-07-28")),
        ..FeedRequest::default()
    };
    let result = scraper.fetch_transfers(&request, now()).await.unwrap();
    assert_eq!(
        names(&result.transfers),
        ["Current Page Deal", "Next Page Deal"]
    );
}

#[tokio::test]
async fn feed_stops_on_repeated_page() {
    let page = ok(&json!({"transfers": [deal("Repeated Deal", "A", "B", "2026-08-01")]}));
    let (scraper, script) = feed_scraper(vec![(1, vec![page.clone()]), (2, vec![page])]);
    let result = scraper
        .fetch_transfers(&FeedRequest::default(), now())
        .await
        .unwrap();
    assert_eq!(names(&result.transfers), ["Repeated Deal"]);
    assert_eq!(script.total(), 2);
}

#[tokio::test]
async fn feed_page_limit_is_an_error() {
    let (scraper, _) = feed_scraper(vec![(
        1,
        vec![ok(
            &json!({"transfers": [deal("A", "A", "B", "2026-08-01")]}),
        )],
    )]);
    let request = FeedRequest {
        max_pages: Some(1),
        ..FeedRequest::default()
    };
    let error = scraper.fetch_transfers(&request, now()).await.unwrap_err();
    assert!(error.to_string().contains("safety limit"), "{error}");
}

#[tokio::test]
async fn feed_exposes_future_dated_transfers_as_pending() {
    let (scraper, _) = feed_scraper(vec![
        (
            1,
            vec![ok(&json!({"transfers": [
                deal("Effective", "A", "B", "2026-08-03"),
                {"name": "Pre Contract", "playerId": 77, "fromClub": "A", "toClub": "C", "transferDate": "2026-10-02"},
            ]}))],
        ),
        (2, vec![ok(&json!({"transfers": []}))]),
    ]);
    let request = FeedRequest {
        since: Some(SinceDate::from("2026-07-24")),
        ..FeedRequest::default()
    };
    let result = scraper.fetch_transfers(&request, now()).await.unwrap();
    assert_eq!(names(&result.transfers), ["Effective"]);
    assert_eq!(result.pending_transfers.len(), 1);
    assert_eq!(result.pending_transfers[0].player_id_fotmob, Some(77));
}

#[test]
fn bounded_window_keeps_undated_and_future_transfers_pending() {
    let payload = json!({"transfers": {"data": {"Players in": [
        {"name": "No Date", "fromClub": "A", "toClub": "B"},
        deal("Dated", "A", "B", "2026-07-01"),
        deal("Signed Ahead", "A", "B", "2026-09-03"),
        deal("Too Old", "A", "B", "2026-01-01"),
        "garbage",
    ]}}});
    let (kept, pending) = extract_transfers_from_team_data(
        &payload,
        Some(day("2026-06-01")),
        Some(day("2026-09-30")),
        day("2026-08-03"),
    );
    assert_eq!(names(&kept), ["Dated"]);
    assert_eq!(names(&pending), ["No Date", "Signed Ahead"]);
}

// ---------------------------------------------------------------------------
// Squads, lineups, captains, match pages
// ---------------------------------------------------------------------------

fn squad_payload(count: i64) -> Value {
    json!({
        "details": {"name": "Example FC", "primaryLeagueId": 47},
        "squad": {"squad": [{"title": "keepers", "members": (100..100 + count).map(|id| json!({
            "id": id, "name": format!("Player {id}"), "role": {"fallback": "CMF"}, "shirtNumber": id - 99,
        })).collect::<Vec<_>>()}]},
        "overview": {"lastLineupStats": {"formation": "4-2-3-1"}},
    })
}

#[test]
fn bad_squad_member_does_not_discard_valid_members() {
    let payload = json!({"squad": {"squad": [{"members": [
        {"name": "Broken", "shirtNumber": "not-a-number"},
        {"id": "invalid-player-id", "name": "Valid", "shirtNumber": "17", "role": null, "positionIdsDesc": "ST,CAM"},
    ]}]}});
    let results = extract_shirt_number_updates(&payload, 42, "Example FC");
    assert_eq!(results.len(), 1);
    let valid = &results[0];
    assert_eq!(
        (
            valid.player_name.as_str(),
            valid.shirt_number,
            valid.position.as_str()
        ),
        ("Valid", Some(17), "ST")
    );
    assert_eq!(
        (valid.to_club.as_str(), valid.player_id_fotmob),
        ("Example FC", None)
    );
    assert_eq!(valid.transfer_type, TransferType::ShirtNumberUpdate);
}

#[test]
fn complete_snapshot_for_full_payload_and_incomplete_for_short() {
    let snapshot = extract_squad_snapshot(&squad_payload(11), 42, "Example FC");
    assert!(snapshot.complete);
    assert_eq!(snapshot.primary_league_id, Some(47));
    assert_eq!(
        (snapshot.members.len(), snapshot.members[0].player_id_fotmob),
        (11, Some(100))
    );
    assert_eq!(snapshot.members[10].position, "CMF");
    assert_eq!(snapshot.formation.as_deref(), Some("4-2-3-1"));
    assert_eq!(
        snapshot.formation_source_url,
        "https://www.fotmob.com/api/data/teams?id=42"
    );
    assert!(!extract_squad_snapshot(&squad_payload(10), 42, "Example FC").complete);
    assert!(
        extract_squad_members(&squad_payload(11), 42, "  ").is_empty(),
        "blank team name yields nothing"
    );
}

#[test]
fn snapshot_carries_starting_xi_with_squad_metadata() {
    let mut payload = squad_payload(11);
    payload["overview"] = json!({"lastLineupStats": {"starters": [
        {"id": 100, "name": "Player 100"}, {"id": 101, "name": "Player 101"},
    ]}});
    let snapshot = extract_squad_snapshot(&payload, 42, "Example FC");
    let starters: Vec<_> = snapshot
        .starter_members
        .iter()
        .map(|m| m.player_name.as_str())
        .collect();
    assert_eq!(starters, ["Player 100", "Player 101"]);
    assert_eq!(snapshot.starter_members[0].position, "CMF");
    assert!(snapshot.sub_members.is_empty());
    assert_eq!(snapshot.formation, None);
}

#[test]
fn lineup_groups_carry_detailed_positions() {
    let mut members = vec![
        json!({"id": 100, "name": "Winger", "positionIds": "87,83", "positionIdsDesc": "LW,RW", "shirtNumber": 11}),
        json!({"id": 101, "name": "Fullback", "positionIds": "32", "positionIdsDesc": "RB"}),
        json!({"id": 102, "name": "Bench Mid", "positionIds": "73,84", "positionIdsDesc": "CM,CAM"}),
    ];
    members.extend((103..111).map(|id| json!({"id": id, "name": format!("Player {id}")})));
    let payload = json!({
        "details": {"name": "Example FC"},
        "squad": {"squad": [{"members": members}]},
        "overview": {"lastLineupStats": {
            "starters": [
                {"id": 100, "name": "Winger", "positionId": 83},
                {"id": 101, "name": "Fullback", "positionId": 999},
            ],
            "subs": [
                {"id": 102, "name": "Bench Mid"},
                {"id": 500, "name": "Unlisted Sub", "age": 19, "countryName": "Exampleland", "positionId": 32},
                {"id": 102, "name": "Bench Mid"},
            ],
        }},
    });
    let snapshot = extract_squad_snapshot(&payload, 42, "Example FC");
    let positions = |group: &[crate::model::SquadMember]| {
        group
            .iter()
            .map(|m| (m.player_id_fotmob.unwrap(), m.position.clone()))
            .collect::<Vec<_>>()
    };
    assert_eq!(
        positions(&snapshot.starter_members),
        [(100, "RW".to_owned()), (101, "RB".to_owned())]
    );
    assert_eq!(snapshot.starter_members[0].shirt_number, Some(11));
    assert_eq!(snapshot.members[0].position, "LW");
    assert_eq!(
        positions(&snapshot.sub_members),
        [(102, "CM".to_owned()), (500, "RB".to_owned())]
    );
    assert_eq!(
        (
            snapshot.sub_members[1].age,
            snapshot.sub_members[1].nationality.as_str()
        ),
        (19, "Exampleland")
    );
}

#[test]
fn coach_sections_and_roles_are_excluded() {
    let payload = json!({"squad": {"squad": [
        {"title": "coach", "members": [{"id": 1, "name": "Boss"}]},
        {"title": "Staff", "members": [{"id": 2, "name": "Physio"}]},
        {"title": "midfielders", "members": [
            {"id": 3, "name": "Mid", "role": {"key": "midfielder"}},
            {"id": 4, "name": "Assistant", "role": {"key": "coach"}},
            {"id": 5, "name": "Gaffer", "role": {"key": "", "fallback": "Manager"}},
        ]},
    ]}});
    let members = extract_squad_members(&payload, 42, "Example FC");
    assert_eq!(
        members
            .iter()
            .map(|m| m.player_name.as_str())
            .collect::<Vec<_>>(),
        ["Mid"]
    );
}

#[test]
fn squad_members_dedupe_by_id_then_name() {
    let payload = json!({"squad": {"squad": [{"members": [
        {"id": 1, "name": "A"}, {"id": 1, "name": "A again"}, {"name": "No Id"}, {"name": "no id"},
    ]}]}});
    let members = extract_squad_members(&payload, 42, "Example FC");
    assert_eq!(
        members
            .iter()
            .map(|m| m.player_name.as_str())
            .collect::<Vec<_>>(),
        ["A", "No Id"]
    );
}

#[test]
fn captain_requires_exactly_one_complete_marker() {
    let lineup = |starters: Value, subs: Value| json!({"overview": {"lastLineupStats": {"starters": starters, "subs": subs}}});
    let one = lineup(
        json!([{"id": 9, "name": "Cap", "isCaptain": true, "age": 30, "countryName": "Spain"}]),
        json!([]),
    );
    let captain = extract_captain(&one, 42, "Example FC").unwrap();
    assert_eq!(
        (
            captain.player_id_fotmob,
            captain.age,
            captain.nationality.as_str()
        ),
        (Some(9), 30, "Spain")
    );
    let two = lineup(
        json!([{"id": 9, "name": "Cap", "isCaptain": true}]),
        json!([{"id": 8, "name": "Vice", "isCaptain": true}]),
    );
    assert!(extract_captain(&two, 42, "Example FC").is_none());
    let no_id = lineup(json!([{"name": "Cap", "isCaptain": true}]), json!([]));
    assert!(extract_captain(&no_id, 42, "Example FC").is_none());
    assert!(extract_captain(&json!({}), 42, "Example FC").is_none());
}

#[test]
fn latest_match_page_uses_newest_finished_team_result() {
    let row = |result: &str,
               link: &str,
               time: &str,
               home: i64,
               away: i64,
               home_name: &str,
               away_name: &str| {
        json!({"resultString": result, "linkToMatch": link, "tooltipText": {
            "utcTime": time, "homeTeamId": home, "awayTeamId": away, "homeTeam": home_name, "awayTeam": away_name}})
    };
    let payload = json!({"table": {"teamForm": {"42": [
        row("W", "/matches/old-match/old#100", "2026-09-10T12:00:00Z", 42, 9, "Old Home", "Old Away"),
        row("D", "/matches/new-match/new#200", "2026-09-20T12:00:00Z", 8, 42, "Other FC", "Example FC"),
        row("", "/matches/future-match/future#300", "2026-10-20T12:00:00Z", 42, 7, "", ""),
        row("W", "/matches/unrelated/unrelated#400", "2026-09-25T12:00:00Z", 4, 5, "", ""),
        row("W", "https://evil.example/matches/x/y#500", "2026-09-30T12:00:00Z", 42, 3, "", ""),
        row("W", "/other/path#600", "2026-09-30T12:00:00Z", 42, 3, "", ""),
        row("W", "/matches/no-fragment/z", "2026-09-30T12:00:00Z", 42, 3, "", ""),
    ]}}});
    assert_eq!(
        latest_team_match_page(&payload, 42),
        Some((
            "https://www.fotmob.com/matches/new-match/new".to_owned(),
            Some("Example FC".to_owned())
        ))
    );
    assert_eq!(latest_team_match_page(&payload, 43), None);
}

#[test]
fn match_page_formation_requires_a_named_team_summary() {
    let html = r"<html><script>Éxample FC (4-3-3): hidden fake formation</script><main>
        <p>The lineups are:</p><p>Éxample FC (4-2-3-1): starting players</p>
        <p>Other FC (3-4-3): other players</p></main></html>";
    assert_eq!(
        formation_from_match_page(html, &["Example FC".to_owned()]).as_deref(),
        Some("4-2-3-1")
    );
    assert_eq!(
        formation_from_match_page(html, &["Missing FC".to_owned()]),
        None
    );
}

// ---------------------------------------------------------------------------
// Club fetching
// ---------------------------------------------------------------------------

fn team_url(id: i64) -> String {
    format!("https://www.fotmob.com/api/data/teams?id={id}")
}

fn club_payload(id: i64) -> Value {
    let mut payload = squad_payload(12);
    payload["details"]["name"] = json!(format!("Club {id}"));
    payload["transfers"] = json!({"data": {"Players in": [deal(&format!("Signing {id}"), "A", &format!("Club {id}"), "2026-08-01")]}});
    payload["overview"]["lastLineupStats"]["starters"] =
        json!([{"id": 100, "name": "Player 100", "isCaptain": true}]);
    payload
}

fn etag_response(body: &Value, etag: &str) -> Outcome {
    Ok(Response {
        status: 200,
        headers: [("etag".to_owned(), etag.to_owned())].into_iter().collect(),
        body: body.to_string().into_bytes(),
    })
}

fn club_scraper(ids: &[i64], cache_dir: Option<&std::path::Path>) -> (FotmobScraper, Arc<Script>) {
    let script = Script::new(
        ids.iter()
            .map(|id| (team_url(*id), vec![ok(&club_payload(*id))]))
            .collect(),
    );
    let options = FotmobOptions {
        cache_dir: cache_dir.map(std::path::Path::to_path_buf),
        ..FotmobOptions::default()
    };
    (scraper_over(script.clone(), options), script)
}

#[tokio::test]
async fn clubs_collect_transfers_squads_and_captains_in_request_order() {
    let (scraper, _) = club_scraper(&[3, 1, 2], None);
    let result = scraper
        .fetch_clubs(&[3, 1, 3, 2, 0, -5], &ClubsRequest::default(), now(), None)
        .await
        .unwrap();
    assert_eq!(
        names(&result.transfers),
        ["Signing 3", "Signing 1", "Signing 2"]
    );
    assert_eq!(
        result
            .squad_snapshots
            .iter()
            .map(|s| s.team_id_fotmob)
            .collect::<Vec<_>>(),
        [3, 1, 2]
    );
    assert_eq!(result.captain_updates.len(), 3);
    assert_eq!(result.roster_updates.len(), 36, "12 shirt numbers per club");
    assert!(result.pending_transfers.is_empty());
}

#[tokio::test]
async fn clubs_require_valid_ids() {
    let (scraper, script) = club_scraper(&[1], None);
    let error = scraper
        .fetch_clubs(&[0, -1], &ClubsRequest::default(), now(), None)
        .await
        .unwrap_err();
    assert!(error.to_string().contains("No FotMob club IDs"), "{error}");
    assert_eq!(script.total(), 0);
}

#[tokio::test]
async fn one_failed_club_aborts_the_scrape() {
    let script = Script::new(vec![
        (team_url(1), vec![ok(&club_payload(1))]),
        (team_url(2), vec![status(404)]),
    ]);
    let scraper = scraper_over(script, FotmobOptions::default());
    let error = scraper
        .fetch_clubs(&[1, 2], &ClubsRequest::default(), now(), None)
        .await
        .unwrap_err();
    assert!(
        error
            .to_string()
            .contains("Club scrape incomplete at FotMob team 2"),
        "{error}"
    );
}

#[tokio::test]
async fn malformed_club_payload_does_not_abort_other_clubs() {
    // An extractor error on one shifted payload must not kill the whole gather.
    let weird = json!({
        "details": [], "name": 5, "squad": "x", "overview": {"lastLineupStats": []},
        "transfers": {"data": {"Players in": [1, "x", null, {"name": "Ok", "fromClub": "A", "toClub": "B", "transferDate": "2026-08-01"}]}},
    });
    let script = Script::new(vec![
        (team_url(1), vec![ok(&club_payload(1))]),
        (team_url(2), vec![ok(&weird)]),
    ]);
    let scraper = scraper_over(script, FotmobOptions::default());
    let result = scraper
        .fetch_clubs(&[1, 2], &ClubsRequest::default(), now(), None)
        .await
        .unwrap();
    assert_eq!(names(&result.transfers), ["Signing 1", "Ok"]);
    assert_eq!(
        result.squad_snapshots.len(),
        1,
        "the malformed club has no complete squad"
    );
}

#[tokio::test]
async fn fresh_cache_needs_no_network_and_stale_cache_revalidates() {
    let dir = tempfile::tempdir().unwrap();
    let payload = club_payload(7);
    let script = Script::new(vec![(
        team_url(7),
        vec![etag_response(&payload, "\"v1\""), status(304)],
    )]);
    let scraper = scraper_over(
        script.clone(),
        FotmobOptions {
            cache_dir: Some(dir.path().to_path_buf()),
            ..FotmobOptions::default()
        },
    );

    let first = scraper
        .fetch_clubs(&[7], &ClubsRequest::default(), now(), None)
        .await
        .unwrap();
    assert_eq!(script.total(), 1);

    let soon = now() + chrono::Duration::hours(5);
    let cached = scraper
        .fetch_clubs(&[7], &ClubsRequest::default(), soon, None)
        .await
        .unwrap();
    assert_eq!(script.total(), 1, "served from the fresh cache");
    assert_eq!(cached, first);

    let later = now() + chrono::Duration::hours(7);
    let revalidated = scraper
        .fetch_clubs(&[7], &ClubsRequest::default(), later, None)
        .await
        .unwrap();
    assert_eq!(script.total(), 2);
    assert_eq!(revalidated, first, "304 reuses the cached payload");
    let conditional = script.requests.lock().unwrap()[1].clone();
    assert!(
        conditional
            .headers
            .iter()
            .any(|(k, v)| k == "If-None-Match" && v == "\"v1\""),
        "{:?}",
        conditional.headers
    );

    // The 304 refreshed the timestamp: fresh again an hour later, no new request.
    scraper
        .fetch_clubs(
            &[7],
            &ClubsRequest::default(),
            later + chrono::Duration::hours(1),
            None,
        )
        .await
        .unwrap();
    assert_eq!(script.total(), 2);
}

#[tokio::test]
async fn stale_cache_is_never_used_after_retry_exhaustion() {
    let dir = tempfile::tempdir().unwrap();
    let script = Script::new(vec![(
        team_url(7),
        vec![etag_response(&club_payload(7), "\"v1\""), status(503)],
    )]);
    let scraper = scraper_over(
        script,
        FotmobOptions {
            cache_dir: Some(dir.path().to_path_buf()),
            ..FotmobOptions::default()
        },
    );
    scraper
        .fetch_clubs(&[7], &ClubsRequest::default(), now(), None)
        .await
        .unwrap();
    let later = now() + chrono::Duration::hours(7);
    let error = scraper
        .fetch_clubs(&[7], &ClubsRequest::default(), later, None)
        .await
        .unwrap_err();
    assert!(error.to_string().contains("HTTP 503"), "{error}");
}

#[tokio::test]
async fn cache_directory_is_caller_supplied_and_populated() {
    let dir = tempfile::tempdir().unwrap();
    let (scraper, _) = club_scraper(&[4, 5], Some(&dir.path().join("nested/cache")));
    scraper
        .fetch_clubs(&[4, 5], &ClubsRequest::default(), now(), None)
        .await
        .unwrap();
    let mut files: Vec<_> = std::fs::read_dir(dir.path().join("nested/cache"))
        .unwrap()
        .map(|e| e.unwrap().file_name().into_string().unwrap())
        .collect();
    files.sort();
    assert_eq!(files, ["4.json", "5.json"]);
}

#[tokio::test]
async fn progress_reports_every_club() {
    let (scraper, _) = club_scraper(&[1, 2], None);
    let seen = Mutex::new(Vec::new());
    let progress = |message: &str, index: usize, total: usize| {
        seen.lock()
            .unwrap()
            .push((message.to_owned(), index, total))
    };
    scraper
        .fetch_clubs(&[1, 2], &ClubsRequest::default(), now(), Some(&progress))
        .await
        .unwrap();
    let mut seen = seen.into_inner().unwrap();
    seen.sort_by_key(|entry| entry.1);
    assert_eq!(
        seen.iter().map(|e| (e.1, e.2)).collect::<Vec<_>>(),
        [(1, 2), (2, 2)]
    );
    assert!(seen[0].0.starts_with("FotMob squads — "), "{}", seen[0].0);
    assert!(!seen[0].0.contains("1/2"), "{}", seen[0].0);
}

#[tokio::test]
async fn club_fetching_bounds_concurrency() {
    struct Gauge {
        current: AtomicUsize,
        peak: AtomicUsize,
    }
    impl Transport for Gauge {
        fn execute<'a>(&'a self, request: &'a Request) -> BoxFuture<'a, Outcome> {
            Box::pin(async move {
                let now = self.current.fetch_add(1, Ordering::SeqCst) + 1;
                self.peak.fetch_max(now, Ordering::SeqCst);
                tokio::time::sleep(Duration::from_millis(15)).await;
                self.current.fetch_sub(1, Ordering::SeqCst);
                let id: i64 = request.url.rsplit('=').next().unwrap().parse().unwrap();
                ok(&club_payload(id))
            })
        }
    }
    let gauge = Arc::new(Gauge {
        current: AtomicUsize::new(0),
        peak: AtomicUsize::new(0),
    });
    let http = HttpClient::builder()
        .transport(gauge.clone())
        .default_concurrency(64)
        .skip_sleeps(true)
        .build()
        .unwrap();
    let scraper = FotmobScraper::new(
        http,
        FotmobOptions {
            club_concurrency: 2,
            club_cooldown: Duration::ZERO,
            ..FotmobOptions::default()
        },
    );
    let ids: Vec<i64> = (1..=8).collect();
    let result = scraper
        .fetch_clubs(&ids, &ClubsRequest::default(), now(), None)
        .await
        .unwrap();
    assert_eq!(gauge.peak.load(Ordering::SeqCst), 2);
    let order: Vec<i64> = result
        .squad_snapshots
        .iter()
        .map(|s| s.team_id_fotmob)
        .collect();
    assert_eq!(order, ids);
}

#[tokio::test]
async fn cancellation_stops_a_club_scrape() {
    let (scraper, _) = club_scraper(&[1, 2], None);
    scraper.http.cancel_token().cancel();
    let error = scraper
        .fetch_clubs(&[1, 2], &ClubsRequest::default(), now(), None)
        .await
        .unwrap_err();
    assert!(matches!(error, FotmobError::Cancelled), "{error}");
}

// ---------------------------------------------------------------------------
// merge_transfers
// ---------------------------------------------------------------------------

fn transfer(name: &str, from: &str, to: &str, date: &str) -> Transfer {
    Transfer {
        player_name: name.to_owned(),
        from_club: from.to_owned(),
        to_club: to.to_owned(),
        date: date.to_owned(),
        ..Transfer::default()
    }
}

#[test]
fn merge_normalizes_diacritics_and_enriches_duplicate() {
    let first = transfer("Kylian Mbappé", "Paris SG", "Real Madrid", "2026-07-01");
    let second = Transfer {
        position: "CF".into(),
        market_value: 180_000_000,
        ..transfer(
            "Kylian Mbappe",
            "Paris SG",
            "Real Madrid",
            "2026-07-01T10:00:00Z",
        )
    };
    let merged = merge_transfers(vec![first, second]);
    assert_eq!(merged.len(), 1);
    assert_eq!(
        (merged[0].position.as_str(), merged[0].market_value),
        ("CF", 180_000_000)
    );
    assert_eq!(merged[0].date, "2026-07-01T10:00:00Z");
}

#[test]
fn merge_preserves_distinct_same_day_lifecycle_events() {
    let returned = Transfer {
        transfer_type: TransferType::EndOfLoan,
        ..transfer("Player", "Loan Club", "Parent Club", "2026-07-01")
    };
    let loaned = Transfer {
        transfer_type: TransferType::Loan,
        is_loan: true,
        ..transfer("Player", "Loan Club", "Parent Club", "2026-07-01T12:00:00Z")
    };
    assert_eq!(merge_transfers(vec![returned, loaned]).len(), 2);
}

#[test]
fn merge_reconciles_club_id_and_name_only_duplicates() {
    let with_ids = Transfer {
        from_club_id_fotmob: Some(9847),
        to_club_id_fotmob: Some(9885),
        from_club_full_name: "Paris Saint-Germain".into(),
        ..transfer("Player", "PSG", "Juventus", "2026-08-02T18:40:10Z")
    };
    let names_only = Transfer {
        transfer_type: TransferType::Loan,
        is_loan: true,
        ..transfer(
            "Player",
            "Paris Saint-Germain",
            "Juventus",
            "2026-08-02T18:40:10Z",
        )
    };
    let merged = merge_transfers(vec![with_ids, names_only]);
    assert_eq!(merged.len(), 1);
    assert_eq!(
        (merged[0].transfer_type, merged[0].is_loan),
        (TransferType::Loan, true)
    );
}

#[test]
fn merge_keeps_same_name_players_with_different_ids_and_enriches_unique_ones() {
    let with_id = |id| Transfer {
        player_id_fotmob: Some(id),
        ..transfer("Alex Smith", "Club A", "Club B", "2026-07-01")
    };
    assert_eq!(merge_transfers(vec![with_id(101), with_id(202)]).len(), 2);

    let identified = Transfer {
        player_id_fotmob: Some(823_274),
        ..transfer("Randal Kolo Muani", "PSG", "Juventus", "2026-08-02")
    };
    let names_only = transfer("Randal Kolo Muani", "PSG", "Juventus", "2026-08-02");
    let merged = merge_transfers(vec![names_only, identified]);
    assert_eq!(merged.len(), 1);
    assert_eq!(merged[0].player_id_fotmob, Some(823_274));
}

#[test]
fn merge_upgrades_transfermarkt_ids_from_either_side() {
    // `merge_transfers` copies them from either side (as does reconcile's own merge).
    let bare = transfer("P", "A", "B", "2026-07-01");
    let rich = Transfer {
        player_id_transfermarkt: Some(5),
        transfer_id_transfermarkt: Some(6),
        ..transfer("P", "A", "B", "2026-07-01")
    };
    let merged = merge_transfers(vec![bare, rich]);
    assert_eq!(
        (
            merged[0].player_id_transfermarkt,
            merged[0].transfer_id_transfermarkt
        ),
        (Some(5), Some(6))
    );
}
