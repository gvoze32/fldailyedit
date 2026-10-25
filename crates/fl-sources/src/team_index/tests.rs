use std::collections::BTreeMap;

use super::*;
use crate::http::{CancelToken, ReplayTransport, Response};
use crate::test_support::read_fixture_text;

/// A recorded sitemap (`testdata/sources_fotmob/sitemaps/`), `None` when absent.
fn sitemap(name: &str) -> Option<String> {
    read_fixture_text(&format!("sources_fotmob/sitemaps/{name}"))
}

fn xml(body: String) -> Response {
    Response {
        status: 200,
        headers: BTreeMap::new(),
        body: body.into_bytes(),
    }
}

fn client(responses: Vec<(&str, Response)>, cancel: Option<CancelToken>) -> HttpClient {
    let transport = ReplayTransport::from_responses(
        responses
            .into_iter()
            .map(|(url, response)| (url.to_owned(), response)),
    );
    let mut builder = HttpClient::builder()
        .transport(std::sync::Arc::new(transport))
        .skip_sleeps(true);
    if let Some(cancel) = cancel {
        builder = builder.cancel_token(cancel);
    }
    builder.build().expect("client")
}

const CHILD_1: &str = "https://www.fotmob.com/sitemap/en/teams/1.xml";
const CHILD_2: &str = "https://www.fotmob.com/sitemap/en/teams/2.xml";

fn recorded() -> Option<Vec<(&'static str, Response)>> {
    Some(vec![
        (SITEMAP_INDEX_URL, xml(sitemap("teams.xml")?)),
        (CHILD_1, xml(sitemap("teams_1.xml")?)),
        (CHILD_2, xml(sitemap("teams_2.xml")?)),
    ])
}

type Crawled = (Result<Vec<TeamEntry>, CrawlError>, Vec<(usize, usize)>);

fn crawl_blocking(http: &HttpClient) -> Crawled {
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .expect("runtime");
    let mut calls = Vec::new();
    let result = runtime.block_on(crawl(http, &mut |done, total| calls.push((done, total))));
    (result, calls)
}

#[test]
fn parse_catalog_validates() {
    assert!(matches!(
        parse_catalog("{}"),
        Err(TeamIndexError::NotAnArray)
    ));
    assert!(matches!(
        parse_catalog("[{}]"),
        Err(TeamIndexError::MissingId)
    ));
    assert!(matches!(
        parse_catalog(r#"[{"fotmob_id": 1, "name": " "}]"#),
        Err(TeamIndexError::InvalidEntry(1))
    ));
    assert!(matches!(
        parse_catalog(r#"[{"fotmob_id": 1, "name": "A"}, {"fotmob_id": "1", "slug": "b"}]"#),
        Err(TeamIndexError::InvalidEntry(1))
    ));
    let parsed = parse_catalog(r#"[{"fotmob_id": "7", "slug": " x-y "}]"#).unwrap();
    assert_eq!((parsed[0].fotmob_id, parsed[0].name.as_str()), (7, "x-y"));
}

#[test]
fn clean_name_decodes_and_title_cases_each_word() {
    assert_eq!(clean_name("santa-clara"), "Santa Clara");
    assert_eq!(clean_name("fk-radnicki-1923"), "Fk Radnicki 1923");
    assert_eq!(clean_name("kazakhstan-u21"), "Kazakhstan U21");
    // A letter after a digit starts a new word, as `str.title()` has it.
    assert_eq!(clean_name("the-3rd-team"), "The 3Rd Team");
    assert_eq!(clean_name("caf%C3%A9-o%27neil"), "Café O'Neil");
    // Malformed escapes stay literal; invalid UTF-8 is replaced.
    assert_eq!(clean_name("a%zz-b%"), "A%Zz B%");
    assert_eq!(clean_name("x%FF"), "X\u{fffd}");
}

#[test]
fn sitemap_parsers_read_only_loc_elements() {
    let (Some(index), Some(first)) = (sitemap("teams.xml"), sitemap("teams_1.xml")) else {
        return;
    };
    let children = parse_sitemap_index(&index);
    assert_eq!(children, [CHILD_1, CHILD_2]);

    // The alternate-language `xhtml:link` hrefs repeat each URL; only `<loc>` counts.
    let teams = parse_team_sitemap(&first);
    let ids: Vec<i64> = teams.iter().map(|team| team.fotmob_id).collect();
    assert_eq!(ids, [1583, 1954, 9977]);
    assert_eq!(
        teams[0],
        TeamEntry {
            fotmob_id: 1583,
            name: "Wsg Tirol".into(),
            slug: "wsg-tirol".into(),
            url: "https://www.fotmob.com/teams/1583/overview/wsg-tirol".into(),
        }
    );
    assert!(parse_team_sitemap("<urlset></urlset>").is_empty());
}

#[test]
fn crawl_merges_children_sorted_and_deduplicated() {
    let Some(responses) = recorded() else {
        return;
    };
    let (result, progress) = crawl_blocking(&client(responses, None));
    let teams = result.expect("crawl");
    let listed: Vec<(i64, &str)> = teams
        .iter()
        .map(|team| (team.fotmob_id, team.name.as_str()))
        .collect();
    assert_eq!(
        listed,
        [
            (42, "The 3Rd Team"),
            (1583, "Wsg Tirol"),
            (1954, "Wolfsberger Ac"),
            (9977, "Lask"),
            (900_001, "Café O'Neil Fc"),
        ]
    );
    assert_eq!(progress, [(0, 2), (1, 2), (2, 2)]);

    // What the crawl produces is what the cache reads back.
    assert_eq!(parse_catalog(&render_catalog(&teams)).unwrap(), teams);
}

#[test]
fn crawl_is_all_or_nothing() {
    let Some(mut missing_child) = recorded() else {
        return;
    };
    missing_child.pop();
    let (result, _) = crawl_blocking(&client(missing_child, None));
    let Err(CrawlError::Incomplete(message)) = result else {
        panic!("a missing child sitemap must fail the crawl");
    };
    assert!(message.contains(CHILD_2), "{message}");

    let mut empty_child = recorded().expect("recorded sitemaps");
    empty_child[2].1 = xml("<urlset></urlset>".into());
    let (result, _) = crawl_blocking(&client(empty_child, None));
    assert!(matches!(result, Err(CrawlError::Incomplete(m)) if m.contains("lists no teams")));

    let mut bad_status = recorded().expect("recorded sitemaps");
    bad_status[1].1.status = 404;
    let (result, _) = crawl_blocking(&client(bad_status, None));
    assert!(matches!(result, Err(CrawlError::Incomplete(m)) if m.contains("HTTP 404")));

    let (result, _) = crawl_blocking(&client(
        vec![(SITEMAP_INDEX_URL, xml("<sitemapindex/>".into()))],
        None,
    ));
    assert!(matches!(result, Err(CrawlError::Incomplete(_))));
}

#[test]
fn crawl_observes_cancellation() {
    let cancel = CancelToken::new();
    cancel.cancel();
    let Some(responses) = recorded() else {
        return;
    };
    let (result, _) = crawl_blocking(&client(responses, Some(cancel)));
    assert!(matches!(result, Err(CrawlError::Cancelled)));
}

/// The local recorded full catalog (`testdata/fotmob_teams.json`, a crawl of the
/// live sitemaps; not in the repository) is exactly what the crawler's naming
/// and rendering produce.
#[test]
fn recorded_catalog_round_trips() {
    let Some(text) = read_fixture_text("fotmob_teams.json") else {
        return;
    };
    let teams = parse_catalog(&text).expect("valid catalog");
    assert!(teams.len() > 5000);
    assert!(
        teams
            .windows(2)
            .all(|pair| pair[0].fotmob_id < pair[1].fotmob_id)
    );
    for team in &teams {
        assert_eq!(clean_name(&team.slug), team.name, "team {}", team.fotmob_id);
        assert_eq!(
            team.url,
            format!(
                "https://www.fotmob.com/teams/{}/overview/{}",
                team.fotmob_id, team.slug
            )
        );
    }
    // (The recorded file lacks the final newline the renderer appends.)
    assert_eq!(render_catalog(&teams).trim_end(), text.trim_end());
}
