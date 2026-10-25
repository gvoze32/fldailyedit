//! Parity of `fl_engine::matcher` and `fl_engine::club_identity` with recorded
//! reference results (recorded from the former Python implementation, now removed: the reference
//! matcher with the documented bug fixes applied), replayed over large query
//! corpora.
//!
//! `testdata/` is local (not in the repository): every test skips with a note when its corpus
//! or `testdata/fotmob_teams.json` is absent. Player.bin-backed variants live in
//! `testdata/local/matcher/`.
//!
//! The full corpora (10k+ player queries, 12k+ team queries, ~1000 sequential squad
//! replays per save) take minutes, so a plain `cargo test` replays a deterministic
//! sample (every 10th query, the first 60 squads, no end-state comparison). Set
//! `FL_PARITY_FULL=1` for the complete corpus:
//! `FL_PARITY_FULL=1 cargo test -p fl-engine --test matcher_parity --release`.

mod common;

use std::collections::{BTreeMap, HashMap};
use std::io::Read;
use std::path::{Path, PathBuf};

use common::testdata_path;
use fl_engine::club_identity::{ClubIdentityIndex, IdentityCache, IdentityEntry, NameResolution};
use fl_engine::matcher::{NameMatcher, PlayerAttrs, PlayerQuery};
use fl_sources::model::{SquadMember, SquadSnapshot};
use fl_sources::team_index::{TeamEntry, parse_catalog};
use serde_json::Value;

/// Replay everything (`FL_PARITY_FULL=1`) instead of a sample.
fn full() -> bool {
    std::env::var_os("FL_PARITY_FULL").is_some_and(|v| v != "0")
}

/// Every `stride`-th query of the corpus.
fn sampled(queries: &[Value]) -> impl Iterator<Item = &Value> {
    queries.iter().step_by(if full() { 1 } else { 10 })
}

fn load(path: &Path) -> Value {
    let file = std::fs::File::open(path).unwrap();
    let mut text = String::new();
    flate2::read::GzDecoder::new(file)
        .read_to_string(&mut text)
        .unwrap();
    serde_json::from_str(&text).unwrap()
}

fn u32_of(value: &Value) -> Option<u32> {
    value.as_u64().map(|v| v as u32)
}

fn conf(value: &Value) -> f64 {
    value.as_str().unwrap().parse().unwrap()
}

struct Db {
    players: Vec<(String, u32)>,
    attrs: HashMap<u32, PlayerAttrs>,
    teams: Vec<(String, u32)>,
    pes_clubs: BTreeMap<u32, String>,
    rosters: HashMap<u32, Vec<u32>>,
    player_names: HashMap<u32, String>,
}

fn parse_db(db: &Value) -> Db {
    let pairs = |key: &str| -> Vec<(String, u32)> {
        db[key]
            .as_array()
            .unwrap()
            .iter()
            .map(|p| (p[0].as_str().unwrap().to_owned(), u32_of(&p[1]).unwrap()))
            .collect()
    };
    let players = pairs("players");
    let mut player_names = HashMap::new();
    for (name, id) in &players {
        player_names.entry(*id).or_insert_with(|| name.clone());
    }
    Db {
        attrs: db["attrs"]
            .as_object()
            .unwrap()
            .iter()
            .map(|(id, a)| {
                (
                    id.parse().unwrap(),
                    PlayerAttrs {
                        position: a[0].as_str().unwrap().to_owned(),
                        nationality: a[1].as_str().unwrap().to_owned(),
                        age: a[2].as_u64().unwrap() as u32,
                    },
                )
            })
            .collect(),
        teams: pairs("teams"),
        pes_clubs: db["pes_clubs"]
            .as_object()
            .unwrap()
            .iter()
            .map(|(id, n)| (id.parse().unwrap(), n.as_str().unwrap().to_owned()))
            .collect(),
        rosters: db["rosters"]
            .as_object()
            .unwrap()
            .iter()
            .map(|(id, r)| {
                (
                    id.parse().unwrap(),
                    r.as_array()
                        .unwrap()
                        .iter()
                        .map(|p| u32_of(p).unwrap())
                        .collect(),
                )
            })
            .collect(),
        players,
        player_names,
    }
}

fn check_players(variant: &str, db: &Db, queries: &[Value]) {
    let matcher = NameMatcher::new(db.players.clone(), db.attrs.clone(), db.teams.clone());
    let mut failures = Vec::new();
    for q in sampled(queries) {
        let (name, threshold) = (q[0].as_str().unwrap(), q[1].as_f64().unwrap());
        let from = u32_of(&q[2]);
        let to = u32_of(&q[3]);
        let ctx = q[4].as_u64() == Some(1);
        let query = PlayerQuery {
            threshold,
            from_team_id: from,
            to_team_id: to,
            team_player_map: ctx.then_some(&db.rosters),
            position: q[5].as_str().unwrap(),
            nationality: q[6].as_str().unwrap(),
            age: q[7].as_u64().unwrap() as u32,
        };
        let got = matcher.match_player(name, &query);
        let want = &q[8];
        let expected = (u32_of(&want[0]), want[1].as_str().unwrap(), conf(&want[2]));
        if (got.id, got.name.as_str(), got.confidence) != expected {
            failures.push(format!(
                "{q} -> {:?} ({:?})",
                (got.id, &got.name, got.confidence),
                got.status
            ));
        }
    }
    assert!(
        failures.is_empty(),
        "{variant}: {} player queries differ; first: {:#?}",
        failures.len(),
        &failures[..failures.len().min(8)]
    );
}

fn check_teams(variant: &str, db: &Db, aliases: &[(String, String)], queries: &[Value]) {
    let plain = NameMatcher::new(Vec::new(), HashMap::new(), db.teams.clone());
    let mut aliased = NameMatcher::new(Vec::new(), HashMap::new(), db.teams.clone());
    aliased.set_team_aliases(aliases.iter().cloned());
    let mut failures = Vec::new();
    for q in sampled(queries) {
        let matcher = if q[2].as_u64() == Some(1) {
            &aliased
        } else {
            &plain
        };
        let threshold = q[1].as_f64().unwrap();
        let got = matcher.match_team(q[0].as_str().unwrap(), Some(threshold));
        let want = &q[3];
        let expected = (u32_of(&want[0]), want[1].as_str().unwrap(), conf(&want[2]));
        if (got.id, got.name.as_str(), got.confidence) != expected {
            failures.push(format!(
                "{q} -> {:?} ({:?})",
                (got.id, &got.name, got.confidence),
                got.status
            ));
        }
    }
    assert!(
        failures.is_empty(),
        "{variant}: {} team queries differ; first: {:#?}",
        failures.len(),
        &failures[..failures.len().min(8)]
    );
}

fn entry_json(e: &IdentityEntry) -> Value {
    serde_json::json!({
        "fotmob_id": e.fotmob_id,
        "pes_team_id": e.pes_team_id,
        "name": e.name,
        "slug": e.slug,
        "url": e.url,
        "match_score": e.match_score,
        "identity_source": e.source.as_str(),
    })
}

fn state_json(index: &ClubIdentityIndex) -> Value {
    serde_json::json!({
        "entries": index.entries().iter().map(entry_json).collect::<Vec<_>>(),
        "learned": index.learned().iter().map(|(f, p)| serde_json::json!([f, p])).collect::<Vec<_>>(),
        "aliases": index.aliases().into_iter().map(|(a, t)| serde_json::json!([a, t])).collect::<Vec<_>>(),
    })
}

fn assert_state(label: &str, got: Value, want: &Value) {
    // The recorded `aliases` is sorted by item; compare as sorted pair lists.
    for key in ["entries", "learned", "aliases"] {
        let (got, want) = (got[key].as_array().unwrap(), want[key].as_array().unwrap());
        let differing: Vec<String> = got
            .iter()
            .zip(want)
            .filter(|(g, w)| g != w)
            .map(|(g, w)| format!("{g} != {w}"))
            .collect();
        assert!(
            differing.is_empty() && got.len() == want.len(),
            "{label}: {key}: {} vs {} items; first differences: {:#?}",
            got.len(),
            want.len(),
            &differing[..differing.len().min(5)]
        );
    }
}

fn resolution_json(r: NameResolution) -> Value {
    match r {
        NameResolution::Unresolved => Value::from("unresolved"),
        NameResolution::Outside => Value::Null,
        NameResolution::Team(id) => Value::from(id),
    }
}

fn check_resolves(label: &str, index: &ClubIdentityIndex, rows: &[Value]) {
    let mut bad = Vec::new();
    for row in sampled(rows) {
        let name = row[0].as_str().unwrap();
        if resolution_json(index.resolve_name(name)) != row[1] {
            bad.push(format!(
                "{name:?}: {:?} != {}",
                index.resolve_name(name),
                row[1]
            ));
        }
    }
    assert!(
        bad.is_empty(),
        "{label}: {} resolve_name differ: {:#?}",
        bad.len(),
        &bad[..bad.len().min(8)]
    );
}

fn snapshot(squad: &Value) -> SquadSnapshot {
    SquadSnapshot {
        club_name: squad["club_name"].as_str().unwrap().to_owned(),
        team_id_fotmob: squad["team_id"].as_i64().unwrap(),
        members: squad["members"]
            .as_array()
            .unwrap()
            .iter()
            .map(|m| SquadMember {
                player_name: m[0].as_str().unwrap().to_owned(),
                nationality: m[1].as_str().unwrap().to_owned(),
                age: m[2].as_u64().unwrap() as u32,
                position: m[3].as_str().unwrap().to_owned(),
                ..SquadMember::default()
            })
            .collect(),
        source_url: squad["source_url"].as_str().unwrap().to_owned(),
        complete: squad["complete"].as_bool().unwrap(),
        starter_members: Vec::new(),
        sub_members: Vec::new(),
        primary_league_id: None,
        formation: None,
        formation_source_url: String::new(),
    }
}

/// The recorded FotMob team catalog (test fixture; the app downloads it at run time);
/// `None` (with a skip note) when it is not on this machine.
fn fixture_catalog() -> Option<Vec<TeamEntry>> {
    let text = std::fs::read_to_string(testdata_path("fotmob_teams.json")?).unwrap();
    Some(parse_catalog(&text).unwrap())
}

fn check_identity(variant: &str, db: &Db, catalog: &[TeamEntry], identity: &Value) {
    let dir = tempfile::tempdir().unwrap();
    let build = |file: &str, scope: &str| {
        ClubIdentityIndex::build(
            &db.pes_clubs,
            catalog,
            IdentityCache::new(dir.path().join(file), scope.to_owned()),
        )
    };
    let names = build("names.json", "scope-a");
    assert_state(
        &format!("{variant} name bindings"),
        state_json(&names),
        &identity["name_bindings"],
    );
    check_resolves(
        &format!("{variant} resolve after names"),
        &names,
        identity["resolve_after_names"].as_array().unwrap(),
    );
    assert!(
        !dir.path().join("names.json").exists(),
        "building must not write the cache"
    );

    for scenario in identity["scenarios"].as_array().unwrap() {
        let label = scenario["label"].as_str().unwrap();
        let file = format!("{label}.json");
        let scope = format!("scope-{label}");
        let mut index = build(&file, &scope);
        let evidence = index.prepare_rosters(&db.rosters, &db.player_names);
        let squads = scenario["squads"].as_array().unwrap();
        let steps = scenario["steps"].as_array().unwrap();
        let mut bad = Vec::new();
        let replay = if full() { squads.len() } else { 60 };
        for (squad, step) in squads.iter().zip(steps).take(replay) {
            let got = index.learn_from_snapshot(&snapshot(squad), &evidence);
            if got != u32_of(&step[0]) {
                bad.push(format!(
                    "{} ({}): {got:?} != {}",
                    squad["club_name"], squad["team_id"], step[0]
                ));
            }
        }
        assert!(
            bad.is_empty(),
            "{variant}/{label}: {} learn results differ: {:#?}",
            bad.len(),
            &bad[..bad.len().min(8)]
        );
        if !full() {
            continue;
        }
        assert_state(
            &format!("{variant}/{label} state"),
            state_json(&index),
            &scenario["state"],
        );
        check_resolves(
            &format!("{variant}/{label} resolve"),
            &index,
            scenario["resolve"].as_array().unwrap(),
        );

        index.save().unwrap();
        let text = std::fs::read_to_string(dir.path().join(&file)).unwrap();
        assert_eq!(
            text,
            scenario["cache_text"].as_str().unwrap(),
            "{variant}/{label}: cache file bytes"
        );
        let reloaded = build(&file, &scope);
        assert_state(
            &format!("{variant}/{label} reloaded"),
            state_json(&reloaded),
            &scenario["reloaded"],
        );
    }
}

fn check_fixture(path: &Path) {
    let Some(catalog) = fixture_catalog() else {
        return;
    };
    let fixture = load(path);
    let variant = fixture["variant"].as_str().unwrap();
    let db = parse_db(&fixture["db"]);
    let aliases: Vec<(String, String)> = fixture["aliases"]
        .as_array()
        .unwrap()
        .iter()
        .map(|p| {
            (
                p[0].as_str().unwrap().to_owned(),
                p[1].as_str().unwrap().to_owned(),
            )
        })
        .collect();
    let players = fixture["player_queries"].as_array().unwrap();
    let teams = fixture["team_queries"].as_array().unwrap();
    eprintln!(
        "{variant}: {} player queries, {} team queries",
        players.len(),
        teams.len()
    );
    let started = std::time::Instant::now();
    check_players(variant, &db, players);
    eprintln!("  players {:?}", started.elapsed());
    check_teams(variant, &db, &aliases, teams);
    eprintln!("  teams {:?}", started.elapsed());
    check_identity(variant, &db, &catalog, &fixture["identity"]);
    eprintln!("  identity {:?}", started.elapsed());
}

#[test]
fn base_corpus_matches_reference() {
    if let Some(path) = testdata_path("matcher/base.json.gz") {
        check_fixture(&path);
    }
}

#[test]
fn local_variant_corpora_match_reference() {
    let Some(dir) = testdata_path("local/matcher") else {
        return;
    };
    let mut files: Vec<PathBuf> = std::fs::read_dir(&dir)
        .unwrap()
        .map(|e| e.unwrap().path())
        .collect();
    files.sort();
    for file in files
        .iter()
        .filter(|f| f.to_string_lossy().ends_with(".json.gz"))
    {
        check_fixture(file);
    }
}
