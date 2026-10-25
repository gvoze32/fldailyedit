//! Parser parity of the supplemental sources against the frozen Python
//! implementation. Fixtures are real recorded responses
//! (`testdata/sources_other/<source>/`); expected outputs are dumped by
//! the former Python implementation (removed).

use std::collections::HashMap;

mod common;

use common::{assert_same, date, json, read, transfers};

use chrono::NaiveDate;
use fl_sources::besoccer::parse_besoccer_transfer_markdown;
use fl_sources::dates::{SinceDate, Window, resolve_date_range};
use fl_sources::model::Transfer;
use fl_sources::soccerway::{SoccerwayTeam, candidate_team, parse_soccerway_transfer_feed};
use fl_sources::sofascore::{
    SOFASCORE_TRANSFER_PAGE_URL, filter_for_clubs, global_transfer_payload,
    parse_sofascore_transfer_payload,
};
use fl_sources::sortitoutsi::parse_sortitoutsi_markdown;
use fl_sources::transfermarkt::parse_transfermarkt_markdown;
use fl_sources::wikipedia::{
    a_league_codes, category_candidates, category_titles, page_wikitexts,
    parse_wikipedia_transfer_wikitext, read_template_expansion,
};
use serde_json::Value;

/// Replay every recorded date-range variant of a case through `parse`.
fn check_case(
    label: &str,
    case: &Value,
    parse: impl Fn(Option<NaiveDate>, Option<NaiveDate>) -> Vec<Transfer>,
) {
    assert_same(
        &format!("{label}/full"),
        &parse(None, None),
        &transfers(&case["full"]),
    );
    for (name, variant) in case["variants"].as_object().expect("variants") {
        let actual = parse(date(&variant["start"]), date(&variant["end"]));
        assert_same(
            &format!("{label}/{name}"),
            &actual,
            &transfers(&variant["transfers"]),
        );
    }
}

#[test]
fn transfermarkt_pages_match_expected() {
    for page in 1..=3 {
        let body = read(&format!("transfermarkt/page{page}.md"));
        let url = read(&format!("transfermarkt/page{page}.url"));
        let case = json(&format!("expected/transfermarkt_page{page}.json.gz"));
        assert!(
            !case["full"].as_array().unwrap().is_empty(),
            "fixture has rows"
        );
        check_case(
            &format!("transfermarkt page {page}"),
            &case,
            |start, end| parse_transfermarkt_markdown(&body, &url, start, end),
        );
    }
}

#[test]
fn besoccer_feed_matches_expected() {
    let case = json("expected/besoccer_page.json.gz");
    let legacy = read("besoccer/page_legacy_hosts.md");
    assert!(!case["full"].as_array().unwrap().is_empty());
    check_case("besoccer", &case, |start, end| {
        parse_besoccer_transfer_markdown(&legacy, "https://www.besoccer.es/fichajes", start, end)
    });
}

#[test]
fn besoccer_accepts_current_player_link_host() {
    // The legacy pattern rejected `es.besoccer.com` links and returned nothing on
    // today's page; only the proof URL host differs from the legacy fixture.
    let live = read("besoccer/page.md");
    let parsed =
        parse_besoccer_transfer_markdown(&live, "https://www.besoccer.es/fichajes", None, None);
    let expected = transfers(&json("expected/besoccer_page.json.gz")["full"]);
    assert_eq!(parsed.len(), expected.len());
    for (actual, legacy) in parsed.iter().zip(&expected) {
        let rewritten: Vec<String> = legacy
            .proof_urls
            .iter()
            .map(|url| {
                url.replace(
                    "https://www.besoccer.es/jugador/",
                    "https://es.besoccer.com/jugador/",
                )
            })
            .collect();
        assert_eq!(actual.proof_urls, rewritten);
        let mut stripped = actual.clone();
        stripped.proof_urls.clone_from(&legacy.proof_urls);
        assert_eq!(&stripped, legacy);
    }
}

#[test]
fn sortitoutsi_feed_matches_expected() {
    let body = read("sortitoutsi/page.md");
    let case = json("expected/sortitoutsi_page.json.gz");
    assert!(!case["full"].as_array().unwrap().is_empty());
    check_case("sortitoutsi", &case, |start, end| {
        parse_sortitoutsi_markdown(&body, start, end)
    });
}

#[test]
fn sofascore_page_matches_expected() {
    let html = read("sofascore/page.html");
    let case = json("expected/sofascore_page.json.gz");
    let payload = global_transfer_payload(&html).expect("embedded transfers");
    assert_eq!(
        payload["transfers"].as_array().unwrap().len() as u64,
        case["payload_transfer_count"].as_u64().unwrap()
    );
    check_case("sofascore", &case, |start, end| {
        parse_sofascore_transfer_payload(&payload, SOFASCORE_TRANSFER_PAGE_URL, start, end)
    });

    let filter = &case["club_filter"];
    let clubs: Vec<String> = filter["clubs"]
        .as_array()
        .unwrap()
        .iter()
        .map(|c| c.as_str().unwrap().to_owned())
        .collect();
    let all = parse_sofascore_transfer_payload(&payload, SOFASCORE_TRANSFER_PAGE_URL, None, None);
    assert_same(
        "sofascore/club_filter",
        &filter_for_clubs(all, &clubs),
        &transfers(&filter["transfers"]),
    );
}

#[test]
fn soccerway_feeds_match_expected() {
    let expected = json("expected/soccerway.json.gz");
    for (club, team) in expected["teams"].as_object().unwrap() {
        let slug: String = club
            .chars()
            .map(|c| if c.is_alphanumeric() { c } else { '_' })
            .collect();
        let search: Value = json(&format!("soccerway/search_{slug}.json"));
        let resolved = candidate_team(&search, club);
        let expected_team = team.as_object().map(|t| SoccerwayTeam {
            name: t["name"].as_str().unwrap().to_owned(),
            slug: t["slug"].as_str().unwrap().to_owned(),
            team_id: t["team_id"].as_str().unwrap().to_owned(),
        });
        assert_eq!(resolved, expected_team, "team resolution for {club}");
        let Some(team) = resolved else { continue };

        let case = &expected["feeds"][club];
        let feed = read(&format!("soccerway/{}", case["file"].as_str().unwrap()));
        let url = case["url"].as_str().unwrap().to_owned();
        check_case(&format!("soccerway {club}"), case, |start, end| {
            parse_soccerway_transfer_feed(&feed, &team.name, &team.team_id, &url, start, end)
        });
    }
}

#[test]
fn wikipedia_pages_match_expected() {
    let expected = json("expected/wikipedia.json.gz");
    let category = json("wikipedia/category.json");
    let titles = category_titles(category.as_array().unwrap());
    let expected_titles: Vec<&str> = expected["category_titles"]
        .as_array()
        .unwrap()
        .iter()
        .map(|t| t.as_str().unwrap())
        .collect();
    assert_eq!(titles, expected_titles);

    let revisions = json("wikipedia/revisions.json");
    let pages = page_wikitexts(revisions.as_array().unwrap());
    let codes: Vec<String> = pages
        .iter()
        .flat_map(|(_, text)| a_league_codes(text))
        .collect::<std::collections::BTreeSet<_>>()
        .into_iter()
        .collect();
    let expected_codes: Vec<&str> = expected["a_league_codes"]
        .as_array()
        .unwrap()
        .iter()
        .map(|c| c.as_str().unwrap())
        .collect();
    assert_eq!(codes, expected_codes);

    let pending: Vec<String> =
        serde_json::from_value(json("wikipedia/expandtemplates.codes.json")).unwrap();
    let mut team_names = HashMap::new();
    read_template_expansion(
        &json("wikipedia/expandtemplates.json"),
        &pending,
        &mut team_names,
    );
    let expected_names: HashMap<String, String> =
        serde_json::from_value(expected["team_names"].clone()).unwrap();
    assert_eq!(team_names, expected_names);
    assert!(!team_names.is_empty());

    assert_eq!(pages.len(), expected["pages"].as_object().unwrap().len());
    let mut total_undated = 0;
    let mut total_dated = 0;
    for (title, wikitext) in &pages {
        let case = &expected["pages"][title];
        check_case(&format!("wikipedia {title}"), case, |start, end| {
            parse_wikipedia_transfer_wikitext(wikitext, title, start, end, &team_names)
        });
        assert_same(
            &format!("wikipedia {title} without team names"),
            &parse_wikipedia_transfer_wikitext(wikitext, title, None, None, &HashMap::new()),
            &transfers(&case["no_team_names"]),
        );
        let full = parse_wikipedia_transfer_wikitext(wikitext, title, None, None, &team_names);
        total_undated += full.iter().filter(|t| t.date.is_empty()).count();
        total_dated += full.iter().filter(|t| !t.date.is_empty()).count();
    }
    assert!(
        total_undated > 0 && total_dated > 0,
        "fixtures cover both club lists and dated tables"
    );
}

#[test]
fn wikipedia_category_discovery_matches_expected() {
    for case in json("expected/wikipedia.json.gz")["category_cases"]
        .as_array()
        .unwrap()
    {
        let today = date(&case["today"]).unwrap();
        let since = date(&case["since"]);
        let window = Window::parse(case["window"].as_str().unwrap()).unwrap();
        let expected: Vec<&str> = case["categories"]
            .as_array()
            .unwrap()
            .iter()
            .map(|c| c.as_str().unwrap())
            .collect();
        assert_eq!(
            category_candidates(today, since, window),
            expected,
            "{case}"
        );
    }
}

#[test]
fn source_date_ranges_match_expected() {
    for case in json("expected/source_ranges.json.gz").as_array().unwrap() {
        let today = date(&case["today"]).unwrap();
        let since = case["since"].as_str().map(SinceDate::from);
        let window = Window::parse(case["window"].as_str().unwrap()).unwrap();
        let range = resolve_date_range(since.as_ref(), window, today).unwrap();
        assert_eq!(
            (range.0, range.1),
            (date(&case["start"]), date(&case["end"])),
            "{case}"
        );
    }
}

fn transfer_batch(value: &Value) -> Vec<Transfer> {
    transfers(value)
}

#[test]
fn reconcile_matches_expected_on_every_case() {
    let cases = json("expected/reconcile_cases.json.gz");
    let cases = cases.as_object().expect("cases");
    assert!(cases.len() > 40, "scenario and generated cases present");
    for (name, case) in cases.iter().filter(|(name, _)| !name.starts_with('_')) {
        let batches: Vec<Vec<Transfer>> = case["batches"]
            .as_array()
            .unwrap()
            .iter()
            .map(transfer_batch)
            .collect();
        let result = fl_sources::reconcile::reconcile_transfer_sources(
            batches,
            transfers(&case["fast_signals"]),
            transfers(&case["corroborators"]),
        );
        assert_same(
            &format!("reconcile {name}"),
            &result,
            &transfers(&case["expected"]),
        );
    }
}

fn string_map(value: &Value) -> HashMap<String, String> {
    serde_json::from_value(value.clone()).expect("string map")
}

#[test]
fn synthetic_bodies_match_expected() {
    let synthetic = json("synthetic.json.gz");
    let bodies = &synthetic["bodies"];
    let text = |source: &str, name: &str| bodies[source][name].as_str().unwrap().to_owned();
    for (index, case) in synthetic["cases"].as_array().unwrap().iter().enumerate() {
        let source = case["source"].as_str().unwrap();
        let name = case["body"].as_str().unwrap();
        let args = &case["args"];
        let (start, end) = (date(&args["start"]), date(&args["end"]));
        let body = text(source, name);
        let actual = match source {
            "wikipedia" => parse_wikipedia_transfer_wikitext(
                &body,
                args["title"].as_str().unwrap(),
                start,
                end,
                &string_map(&args["team_names"]),
            ),
            "sortitoutsi" => parse_sortitoutsi_markdown(&body, start, end),
            "besoccer" => {
                parse_besoccer_transfer_markdown(&body, args["url"].as_str().unwrap(), start, end)
            }
            "transfermarkt" => {
                parse_transfermarkt_markdown(&body, args["url"].as_str().unwrap(), start, end)
            }
            "soccerway" => parse_soccerway_transfer_feed(
                &body,
                args["team_name"].as_str().unwrap(),
                args["team_id"].as_str().unwrap(),
                args["url"].as_str().unwrap(),
                start,
                end,
            ),
            other => panic!("unknown source {other}"),
        };
        assert_same(
            &format!("synthetic #{index} {source}/{name} {args}"),
            &actual,
            &transfers(&case["expected"]),
        );
    }

    for (index, case) in synthetic["sofascore"]
        .as_array()
        .unwrap()
        .iter()
        .enumerate()
    {
        let actual = parse_sofascore_transfer_payload(
            &case["payload"],
            "https://sofa.example/",
            date(&case["start"]),
            date(&case["end"]),
        );
        assert_same(
            &format!("sofascore payload #{index}"),
            &actual,
            &transfers(&case["expected"]),
        );
    }
    for case in synthetic["sofascore_pages"].as_array().unwrap() {
        let payload = global_transfer_payload(case["html"].as_str().unwrap());
        let expected = if case["payload"].is_null() {
            None
        } else {
            Some(case["payload"].clone())
        };
        assert_eq!(payload, expected, "sofascore page {}", case["name"]);
    }
    for case in synthetic["soccerway_search"].as_array().unwrap() {
        let found = candidate_team(&case["payload"], case["requested"].as_str().unwrap()).map(|team| {
            serde_json::json!({ "name": team.name, "slug": team.slug, "team_id": team.team_id })
        });
        let expected = if case["expected"].is_null() {
            None
        } else {
            Some(case["expected"].clone())
        };
        assert_eq!(found, expected, "soccerway search {}", case["requested"]);
    }
}

#[test]
fn query_encoding_matches_expected() {
    for case in json("expected/url_encoding.json.gz").as_array().unwrap() {
        let params: Vec<(String, String)> = case["params"]
            .as_array()
            .unwrap()
            .iter()
            .map(|pair| {
                (
                    pair[0].as_str().unwrap().to_owned(),
                    pair[1].as_str().unwrap().to_owned(),
                )
            })
            .collect();
        let url = fl_sources::supplemental::url_with_query(case["path"].as_str().unwrap(), &params);
        assert_eq!(url, case["target"].as_str().unwrap());
    }
}

/// Cut a body at many char boundaries; parsers must never panic on partial or
/// garbled payloads (HTTP bodies are untrusted).
fn truncations(body: &str) -> impl Iterator<Item = &str> {
    let step = (body.len() / 97).max(1);
    (0..=body.len())
        .step_by(step)
        .filter(|index| body.is_char_boundary(*index))
        .map(|index| &body[..index])
}

#[test]
fn parsers_survive_truncated_and_garbled_bodies() {
    let team_names = HashMap::new();
    for page in 1..=2 {
        for cut in truncations(&read(&format!("transfermarkt/page{page}.md"))) {
            parse_transfermarkt_markdown(cut, "u", None, None);
        }
    }
    for cut in truncations(&read("besoccer/page.md")) {
        parse_besoccer_transfer_markdown(cut, "u", None, None);
    }
    for cut in truncations(&read("sortitoutsi/page.md")) {
        parse_sortitoutsi_markdown(cut, None, None);
    }
    for cut in truncations(&read("sofascore/page.html")) {
        if let Some(payload) = global_transfer_payload(cut) {
            parse_sofascore_transfer_payload(&payload, "u", None, None);
        }
    }
    for entry in std::fs::read_dir(common::testdata().join("soccerway"))
        .unwrap()
        .flatten()
    {
        let name = entry.file_name().to_string_lossy().into_owned();
        if name.starts_with("feed_") {
            for cut in truncations(&read(&format!("soccerway/{name}"))) {
                parse_soccerway_transfer_feed(cut, "Chelsea", "id", "u", None, None);
            }
        }
    }
    let revisions = json("wikipedia/revisions.json");
    for (title, wikitext) in page_wikitexts(revisions.as_array().unwrap()).iter().take(3) {
        for cut in truncations(wikitext) {
            parse_wikipedia_transfer_wikitext(cut, title, None, None, &team_names);
        }
    }
    // Multibyte-heavy and unbalanced markup.
    for junk in [
        "{{{{ [[ ]] }}",
        "|-\n{|\n!a!!b\n|c||d|}",
        "===\u{e9}===\n'''In:'''\n{{fs player|name=|other=from }}",
        "\u{2028}\u{85}",
    ] {
        parse_wikipedia_transfer_wikitext(junk, "t", None, None, &team_names);
        parse_transfermarkt_markdown(junk, "u", None, None);
        parse_besoccer_transfer_markdown(junk, "u", None, None);
        parse_sortitoutsi_markdown(junk, None, None);
        parse_soccerway_transfer_feed(junk, "a", "b", "u", None, None);
    }
}
