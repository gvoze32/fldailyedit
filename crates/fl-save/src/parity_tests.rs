//! Parity of the save-table modules against the frozen Python implementation.
//!
//! Fixtures were recorded from the former Python implementation (removed; they are now frozen):
//! * `testdata/save_tables/{base,synthetic}.json.gz` (committed),
//! * `testdata/local/save_tables/*` (local only; tests skip when absent).
//!
//! Saves are decrypted in-process with [`crate::crypto`].

use std::collections::HashMap;
use std::fs;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::sync::Arc;

use serde_json::{Value, json};
use sha2::{Digest, Sha256};

use crate::catalog::PlayerCatalog;
use crate::cpk::Cpk;
use crate::edit_file::{EditFile, testing::Mock};
use crate::layout::*;
use crate::model::{NativePlayer, NativePlayers, PlayerInfo, Position};
use crate::native::PlayerBinDatabase;
use crate::rating::{PlayerAbilities, decode_playerbin_record};
use crate::release_policy::{
    ReleasePolicy, ReleasePolicyError, load_release_policy, parse_release_policy,
};
use crate::validate::IntegrityReport;
use crate::{crypto, wesys};

fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../..")
}

fn read_gz_json(path: &Path) -> Option<Value> {
    let file = fs::File::open(path).ok()?;
    let mut text = String::new();
    flate2::read::GzDecoder::new(file)
        .read_to_string(&mut text)
        .ok()?;
    Some(serde_json::from_str(&text).unwrap_or_else(|e| panic!("{}: {e}", path.display())))
}

/// First difference between two JSON values, for readable failures.
fn first_difference(path: &str, rust: &Value, python: &Value) -> Option<String> {
    if rust == python {
        return None;
    }
    match (rust, python) {
        (Value::Array(a), Value::Array(b)) => {
            for (index, (x, y)) in a.iter().zip(b).enumerate() {
                if let Some(diff) = first_difference(&format!("{path}[{index}]"), x, y) {
                    return Some(diff);
                }
            }
            Some(format!(
                "{path}: length rust={} python={}",
                a.len(),
                b.len()
            ))
        }
        (Value::Object(a), Value::Object(b)) => {
            for (key, x) in a {
                match b.get(key) {
                    Some(y) => {
                        if let Some(diff) = first_difference(&format!("{path}.{key}"), x, y) {
                            return Some(diff);
                        }
                    }
                    None => return Some(format!("{path}.{key}: missing in python")),
                }
            }
            b.keys()
                .find(|key| !a.contains_key(*key))
                .map(|key| format!("{path}.{key}: missing in rust"))
        }
        _ => Some(format!("{path}: rust={rust} python={python}")),
    }
}

#[track_caller]
fn assert_same(what: &str, rust: &Value, python: &Value) {
    if let Some(diff) = first_difference(what, rust, python) {
        let clip = |text: String| text.chars().take(600).collect::<String>();
        panic!("{}", clip(diff));
    }
}

fn position_label(position: Option<Position>) -> &'static str {
    position.map_or("", Position::name)
}

fn player_row(player: &PlayerInfo) -> Value {
    json!([
        player.player_id,
        player.name,
        player.print_name,
        position_label(player.position),
        "",
        player.age,
        player.overall
    ])
}

fn sorted_player_rows(players: &HashMap<u32, PlayerInfo>) -> Value {
    let mut ids: Vec<u32> = players.keys().copied().collect();
    ids.sort_unstable();
    Value::Array(ids.iter().map(|id| player_row(&players[id])).collect())
}

/// Rows (names blanked) of the players both the engine's catalog and the
/// Python catalog hold, by ascending id. The Python run took names and the
/// set of non-rostered players from the bundled FL catalog (removed); the
/// engine takes them from the save and Player.bin.
fn shared_unnamed_rows(players: &HashMap<u32, PlayerInfo>, python: &Value) -> (Value, Value) {
    let blank = |mut row: Value| {
        row[1] = json!("");
        row[2] = json!("");
        row
    };
    let python_rows: Vec<&Value> = python.as_array().unwrap().iter().collect();
    let python_ids: std::collections::HashSet<u32> = python_rows
        .iter()
        .map(|row| row[0].as_u64().unwrap() as u32)
        .collect();
    let mut ids: Vec<u32> = players
        .keys()
        .copied()
        .filter(|id| python_ids.contains(id))
        .collect();
    ids.sort_unstable();
    let rust: Vec<Value> = ids
        .iter()
        .map(|id| blank(player_row(&players[id])))
        .collect();
    let expected: Vec<Value> = python_rows
        .into_iter()
        .filter(|row| players.contains_key(&(row[0].as_u64().unwrap() as u32)))
        .map(|row| blank(row.clone()))
        .collect();
    (Value::Array(rust), Value::Array(expected))
}

/// The coverage counters both implementations report. `missing_roster_ids`
/// is not one of them: the Python run filled ids the old Player.bin variants
/// lack from the bundled FL catalog.
fn report_value(catalog: &PlayerCatalog) -> Value {
    let r = &catalog.report;
    json!({
        "edited_entries": r.edited_entries,
        "roster_entries": r.roster_entries,
        "positions": r.positions,
        "ages": r.ages,
    })
}

/// The same counters of a Python `catalog_report`.
fn python_report_value(report: &Value) -> Value {
    json!({
        "edited_entries": report["edited_entries"],
        "roster_entries": report["roster_entries"],
        "positions": report["positions"],
        "ages": report["ages"],
    })
}

fn integrity_value(report: &IntegrityReport) -> Value {
    let m = &report.metrics;
    json!({
        "valid": report.is_valid(),
        "errors": report.error_messages(),
        "warnings": report.warnings.iter().map(ToString::to_string).collect::<Vec<_>>(),
        "metrics": {
            "data_size": m.data_size,
            "expected_data_size": m.expected_data_size,
            "rosters": m.rosters,
            "clubs": m.clubs,
            "duplicate_club_players": m.duplicate_club_players,
            "undersized_clubs": m.undersized_clubs,
            "checked_game_plans": m.checked_game_plans,
            "checked_position_arrays": m.checked_position_arrays,
            "semantic_position_checks": m.semantic_position_checks,
        },
    })
}

fn ratings_of(abilities: &PlayerAbilities) -> Vec<i32> {
    Position::ALL
        .iter()
        .map(|&p| abilities.position_rating(p))
        .collect()
}

struct Loaded {
    data: Vec<u8>,
    pes21: bool,
}

fn load_save(label: &str) -> Option<Loaded> {
    let path = if label == "base" {
        root().join("base/EDIT00000000")
    } else {
        root().join("reference").join(label)
    };
    let raw = fs::read(path).ok()?;
    let blocks = crypto::decrypt(&raw).expect("decrypt");
    Some(Loaded {
        pes21: blocks.save_header().is_pes21(),
        data: blocks.data,
    })
}

fn open(loaded: &Loaded) -> EditFile {
    let mut file = EditFile::from_bytes(loaded.data.clone()).unwrap();
    file.set_pes21(loaded.pes21);
    file
}

fn playerbin_raw(source: &str) -> Option<Vec<u8>> {
    const MEMBER: &str = "common/etc/pesdb/Player.bin";
    let reference = root().join("reference");
    let cpk = |path: PathBuf| Cpk::open(&path).ok()?.read_file(MEMBER).ok();
    match source {
        "reference_player_bin" => fs::read(reference.join("Player.bin")).ok(),
        "data_extra" => cpk(reference.join("data_extra.cpk")),
        "data_s2526" => cpk(reference.join("data_s2526.cpk")),
        other => cpk(reference.join("T99 Patch V10").join(format!("{other}.cpk"))),
    }
}

fn core_checks(label: &str, loaded: &Loaded, py: &Value) {
    let file = open(loaded);
    let sha = hex(&Sha256::digest(&loaded.data));
    assert_same(&format!("{label} sha"), &json!(sha), &py["data_sha256"]);
    assert_same(
        &format!("{label} len"),
        &json!(loaded.data.len()),
        &py["data_len"],
    );
    assert_same(
        &format!("{label} pes21"),
        &json!(loaded.pes21),
        &py["is_pes21"],
    );

    let c = file.counts();
    assert_same(
        &format!("{label} counts"),
        &json!([
            c.players,
            c.teams,
            c.managers,
            c.stadiums,
            c.competitions,
            c.unknown,
            c.team_players,
            c.game_plans
        ]),
        &py["counts"],
    );
    assert_same(
        &format!("{label} offsets"),
        &json!([
            PLAYER_START,
            TEAM_START,
            MANAGER_START,
            COMPETITION_START,
            STADIUM_START,
            UNKNOWN_START,
            TEAM_PLAYER_START,
            COMPETITION_ENTRY_START,
            GAME_PLAN_START
        ]),
        &py["offsets"],
    );

    // Edited players (Python dict order = first appearance; compare by id).
    let edited = file.edited_players();
    let mut python_edited: Vec<Value> = py["edited_players"].as_array().unwrap().clone();
    python_edited.sort_by_key(|row| row[0].as_u64().unwrap());
    assert_same(
        &format!("{label} edited_players"),
        &sorted_player_rows(&edited),
        &Value::Array(python_edited.clone()),
    );
    assert!(
        python_edited.iter().all(|row| row[4] == ""),
        "python nationality is never populated"
    );

    let teams: Vec<Value> = file
        .teams()
        .iter()
        .map(|t| json!([t.team_id, t.name, t.abbreviation]))
        .collect();
    assert_same(
        &format!("{label} teams"),
        &Value::Array(teams),
        &py["teams"],
    );

    let rosters: Vec<Value> = file
        .rosters()
        .iter()
        .map(|r| json!([r.team_id, &r.player_ids[..], &r.shirt_numbers[..]]))
        .collect();
    assert_same(
        &format!("{label} rosters"),
        &Value::Array(rosters),
        &py["rosters"],
    );

    assert_same(
        &format!("{label} divisions"),
        &json!(file.league_divisions()),
        &py["divisions"],
    );
    assert_same(
        &format!("{label} club_ids"),
        &json!(file.club_team_ids().iter().collect::<Vec<_>>()),
        &py["club_ids"],
    );

    let plans: Vec<Value> = file
        .game_plans()
        .map(|(team_id, bytes)| json!([team_id, hex(bytes)]))
        .collect();
    assert_same(
        &format!("{label} game_plans"),
        &Value::Array(plans),
        &py["game_plans"],
    );

    // Integrity without native metadata or a catalog.
    assert_same(
        &format!("{label} integrity"),
        &integrity_value(&file.validate_integrity()),
        &py["integrity"],
    );

    // Catalog without native data: the players edited inside the save. The
    // Python catalog also held the bundled FL names (removed), which never
    // carried positions or ages, so the integrity verdict is unchanged.
    let mut file = open(loaded);
    let catalog = file.build_catalog();
    file.set_player_catalog(Some(catalog));
    assert_same(
        &format!("{label} catalog_integrity"),
        &integrity_value(&file.validate_integrity()),
        &py["catalog_integrity"],
    );

    // EDIT ratings.
    let mut rows = Vec::new();
    let mut ids: Vec<u32> = edited.keys().copied().collect();
    ids.sort_unstable();
    for id in ids {
        rows.push(match file.abilities(id) {
            Some(a) => json!([id, a.overall(), ratings_of(&a)]),
            None => json!([id, null]),
        });
    }
    let mut python_ratings: Vec<Value> = py["edit_ratings"].as_array().unwrap().clone();
    python_ratings.sort_by_key(|row| row[0].as_u64().unwrap());
    assert_same(
        &format!("{label} edit_ratings"),
        &Value::Array(rows),
        &Value::Array(python_ratings),
    );
}

fn playerbin_checks(label: &str, loaded: &Loaded, py: &Value) -> usize {
    let mut compared = 0;
    for (source, expected) in py.as_object().unwrap() {
        let Some(raw) = playerbin_raw(source) else {
            eprintln!("skip {label}/{source}: Player.bin source unavailable");
            continue;
        };
        let database = PlayerBinDatabase::from_bytes(&raw).expect("Player.bin");
        let mut file = open(loaded);
        file.attach_native(Some(Arc::new(database)));
        let catalog = file.build_catalog();
        let what = format!("{label}+{source}");
        let (rust_rows, python_rows) = shared_unnamed_rows(&catalog.players, &expected["players"]);
        assert_same(&format!("{what} players"), &rust_rows, &python_rows);
        assert_same(
            &format!("{what} report"),
            &report_value(&catalog),
            &python_report_value(&expected["report"]),
        );
        file.set_player_catalog(Some(catalog));
        assert_same(
            &format!("{what} integrity"),
            &integrity_value(&file.validate_integrity()),
            &expected["integrity"],
        );
        compared += 1;
    }
    compared
}

/// Audit item 3.9: the EDIT age field needs no +15 offset. Pin it against
/// Player.bin for the players present in both.
#[test]
fn edit_age_equals_playerbin_age() {
    let (Some(loaded), Some(raw)) = (load_save("base"), playerbin_raw("data_extra")) else {
        eprintln!("skip: reference Player.bin unavailable");
        return;
    };
    let database = PlayerBinDatabase::from_bytes(&raw).unwrap();
    let (mut both, mut equal) = (0, 0);
    for player in open(&loaded).edited_players().values() {
        let Some(record) = database.get(player.player_id) else {
            continue;
        };
        if player.age != 0 {
            both += 1;
            equal += usize::from(player.age == record.age);
        }
    }
    assert!(both > 1_000, "{both} players in both");
    assert!(equal * 100 >= both * 99, "{equal}/{both} ages equal");
}

fn hex(bytes: &[u8]) -> String {
    use std::fmt::Write;
    bytes
        .iter()
        .fold(String::with_capacity(bytes.len() * 2), |mut out, b| {
            let _ = write!(out, "{b:02x}");
            out
        })
}

fn run_label(label: &str, fixture: Option<Value>, playerbin_fixture: Option<Value>) {
    let Some(py) = fixture else {
        eprintln!("skip {label}: no fixture");
        return;
    };
    let Some(loaded) = load_save(label) else {
        eprintln!("skip {label}: save not present");
        return;
    };
    core_checks(label, &loaded, &py);
    if let Some(playerbin) = playerbin_fixture {
        let compared = playerbin_checks(label, &loaded, &playerbin);
        eprintln!("{label}: compared {compared} Player.bin variants");
    }
}

fn local_fixture(name: &str) -> Option<Value> {
    read_gz_json(&root().join("testdata/local/save_tables").join(name))
}

#[test]
fn base_matches_committed_python_dump() {
    let fixture = read_gz_json(&root().join("testdata/save_tables/base.json.gz"))
        .expect("committed base fixture");
    assert!(
        load_save("base").is_some(),
        "base/EDIT00000000 is committed"
    );
    run_label(
        "base",
        Some(fixture),
        local_fixture("base.playerbin.json.gz"),
    );
}

macro_rules! reference_save_test {
    ($name:ident, $label:literal) => {
        #[test]
        fn $name() {
            run_label(
                $label,
                local_fixture(concat!($label, ".json.gz")),
                local_fixture(concat!($label, ".playerbin.json.gz")),
            );
        }
    };
}

reference_save_test!(reference_ramsdale_test, "EDIT00000000-ramsdale-test");
reference_save_test!(reference_salah_87to85, "EDIT00000000-salah-87to85");
reference_save_test!(reference_epltactic, "EDIT00000000epltactic");
reference_save_test!(reference_gondowan, "EDIT00000000gondowan");
reference_save_test!(reference_orifl, "EDIT00000000orifl");
reference_save_test!(reference_t99, "EDIT00000000t99");

#[test]
fn playerbin_record_ratings_match_python() {
    let mut compared = 0;
    for source in [
        "reference_player_bin",
        "data_extra",
        "data_s2526",
        "t99p21_v10_liveupd",
    ] {
        let Some(py) = local_fixture(&format!("playerbin_{source}.json.gz")) else {
            continue;
        };
        let Some(raw) = playerbin_raw(source) else {
            continue;
        };
        assert_same(
            &format!("{source} sha"),
            &json!(hex(&Sha256::digest(&raw))),
            &py["sha256"],
        );
        let data = wesys::decompress_wesys(&raw, "Player.bin").expect("wesys");
        let mut rows = Vec::new();
        for (index, record) in data.as_chunks::<312>().0.iter().enumerate() {
            let offset = index * 312;
            let position_index = (record[0x36] >> 2) & 0x0F;
            if position_index >= 13 {
                assert!(decode_playerbin_record(&data, offset).is_err());
                continue;
            }
            let abilities = decode_playerbin_record(&data, offset).unwrap();
            let id = u32::from_le_bytes(record[8..12].try_into().unwrap());
            rows.push(json!([
                index,
                id,
                abilities.overall(),
                ratings_of(&abilities)
            ]));
        }
        assert_same(
            &format!("{source} ratings"),
            &Value::Array(rows),
            &py["ratings"],
        );
        compared += 1;
    }
    eprintln!("compared Player.bin rating dumps: {compared}");
}

// ── synthetic fixtures ───────────────────────────────────────────────────

fn synthetic() -> Value {
    read_gz_json(&root().join("testdata/save_tables/synthetic.json.gz"))
        .expect("committed synthetic fixture")
}

struct LabelledNative(HashMap<u32, Option<Position>>);

impl NativePlayers for LabelledNative {
    fn native_player(&self, id: u32) -> Option<NativePlayer<'_>> {
        self.0.get(&id).map(|&position| NativePlayer {
            player_id: id,
            name: "n",
            print_name: "",
            position,
            age: 20,
            abilities: None,
        })
    }
}

fn mock_from_kwargs(kwargs: &Value) -> Mock {
    let count = |key: &str| kwargs[key].as_u64().unwrap() as usize;
    let mut mock = Mock {
        players: count("num_players"),
        teams: count("num_teams"),
        team_players: count("num_team_player"),
        game_plans: count("num_game_plans"),
        ..Mock::default()
    };
    if let Some(entries) = kwargs["team_entries"].as_array() {
        mock.team_entries = entries
            .iter()
            .map(|e| {
                (
                    e[0].as_u64().unwrap() as u32,
                    e[1].as_str().unwrap().to_owned(),
                )
            })
            .collect();
    }
    if let Some(entries) = kwargs["team_player_entries"].as_array() {
        mock.rosters = entries
            .iter()
            .map(|e| {
                (
                    e[0].as_u64().unwrap() as u32,
                    e[1].as_array()
                        .unwrap()
                        .iter()
                        .map(|v| v.as_u64().unwrap() as u32)
                        .collect(),
                    e[2].as_array()
                        .unwrap()
                        .iter()
                        .map(|v| v.as_u64().unwrap() as u16)
                        .collect(),
                )
            })
            .collect();
    }
    if let Some(entries) = kwargs["player_entries"].as_array() {
        mock.player_entries = entries
            .iter()
            .map(|e| {
                (
                    e[0].as_u64().unwrap() as u32,
                    e[1].as_str().unwrap().to_owned(),
                )
            })
            .collect();
    }
    if let Some(ids) = kwargs["league_team_ids"].as_array() {
        mock.league = ids.iter().map(|v| v.as_u64().unwrap() as u32).collect();
    }
    mock
}

fn unhex(text: &str) -> Vec<u8> {
    (0..text.len() / 2)
        .map(|i| u8::from_str_radix(&text[i * 2..i * 2 + 2], 16).unwrap())
        .collect()
}

#[test]
fn synthetic_validation_cases_match_python() {
    let fixture = synthetic();
    let cases = fixture["validate"].as_array().unwrap();
    assert!(cases.len() >= 25);
    for case in cases {
        let name = case["name"].as_str().unwrap();
        let spec = &case["case"];
        let mut data = mock_from_kwargs(&spec["kwargs"]).build();
        for patch in spec["patches"].as_array().into_iter().flatten() {
            let base = match patch[0].as_str().unwrap() {
                "game_plan_start" => GAME_PLAN_START,
                "team_player_start" => TEAM_PLAYER_START,
                other => panic!("unknown patch base {other}"),
            };
            let offset = base + patch[1].as_u64().unwrap() as usize;
            let bytes = unhex(patch[2].as_str().unwrap());
            data[offset..offset + bytes.len()].copy_from_slice(&bytes);
        }
        for patch in spec["header_patches"].as_array().into_iter().flatten() {
            let offset = patch[0].as_u64().unwrap() as usize;
            let bytes = unhex(patch[1].as_str().unwrap());
            data[offset..offset + bytes.len()].copy_from_slice(&bytes);
        }
        if let Some(cut) = spec["truncate"].as_u64() {
            data.truncate(data.len() - cut as usize);
        }
        if let Some(extra) = spec["append"].as_str() {
            data.extend(unhex(extra));
        }
        let mut file = EditFile::from_bytes(data).unwrap();
        if let Some(positions) = spec["playerbin"].as_object() {
            let native = positions
                .iter()
                .map(|(id, label)| {
                    (
                        id.parse().unwrap(),
                        Position::from_label(label.as_str().unwrap()),
                    )
                })
                .collect();
            file.attach_native(Some(Arc::new(LabelledNative(native))));
        }
        assert_same(
            name,
            &integrity_value(&file.validate_integrity()),
            &case["integrity"],
        );
    }
}

#[test]
fn synthetic_release_policy_cases_match_python() {
    let fixture = synthetic();
    let dir = tempfile::tempdir().unwrap();
    for (index, case) in fixture["policy"].as_array().unwrap().iter().enumerate() {
        let text = case["text"].as_str().unwrap();
        if let Some(prefix) = case["error_prefix"].as_str() {
            let path = dir.path().join(format!("broken{index}.json"));
            fs::write(&path, text).unwrap();
            let error = load_release_policy(&path).unwrap_err();
            assert!(matches!(error, ReleasePolicyError::Read { .. }));
            let shown = error
                .to_string()
                .replace(&path.display().to_string(), "<path>");
            assert!(shown.starts_with(prefix), "{shown}");
            continue;
        }
        let document: Value = serde_json::from_str(text).unwrap();
        let result = parse_release_policy(&document);
        match (case.get("ok"), case.get("error")) {
            (Some(expected), None) => {
                let policy = result.unwrap_or_else(|e| panic!("case {index} ({text}): {e}"));
                assert_same(&format!("policy {index}"), &policy_value(&policy), expected);
            }
            (None, Some(message)) => {
                // Python accepted `true` / `1.0` as schema_version 1 and ids it
                // treats as decimal in other scripts; those are intentional
                // Rust rejections and do not appear in the corpus as ok-cases.
                assert_eq!(
                    result.unwrap_err().to_string(),
                    message.as_str().unwrap(),
                    "case {index}: {text}"
                );
            }
            _ => unreachable!(),
        }
    }
}

fn policy_value(policy: &ReleasePolicy) -> Value {
    let mut protected = serde_json::Map::new();
    for (team, players) in &policy.protected_players {
        protected.insert(team.to_string(), json!(players.iter().collect::<Vec<_>>()));
    }
    let mut usage = serde_json::Map::new();
    for (player, u) in &policy.usage {
        usage.insert(
            player.to_string(),
            json!({
                "minutes": u.minutes,
                "starts": u.starts,
                "appearances": u.appearances,
                "news_mentions": u.news_mentions,
            }),
        );
    }
    // Python emitted keys sorted numerically; compare as sets of entries.
    json!({
        "schema_version": 1,
        "source": policy.source,
        "season": policy.season,
        "as_of": policy.as_of,
        "protected_players": protected,
        "usage": usage,
    })
}

#[test]
fn synthetic_ratings_match_python_and_never_panic() {
    let fixture = synthetic();
    let mut compared = 0;
    let mut out_of_curve = 0;
    for case in fixture["rating"].as_array().unwrap() {
        let array = |key: &str| -> Vec<u8> {
            case[key]
                .as_array()
                .unwrap()
                .iter()
                .map(|v| v.as_u64().unwrap() as u8)
                .collect()
        };
        let abilities = PlayerAbilities {
            registered_position: Position::from_index(
                case["registered_position"].as_u64().unwrap() as u8,
            )
            .unwrap(),
            position_grades: array("grades").try_into().unwrap(),
            abilities: array("abilities").try_into().unwrap(),
            goalkeeping: array("goalkeeping").try_into().unwrap(),
            weak_foot_accuracy: case["weak_foot"].as_u64().unwrap() as u8,
        };
        let ratings = ratings_of(&abilities);
        assert!(ratings.iter().all(|&r| r >= 40));
        match case["ratings"].as_array() {
            Some(expected) => {
                assert_same("rating", &json!(ratings), &Value::Array(expected.clone()));
                compared += 1;
            }
            // Python raised IndexError (average >= 100): Rust clamps to the last curve entry.
            None => out_of_curve += 1,
        }
    }
    assert!(
        compared > 100 && out_of_curve > 0,
        "{compared} compared, {out_of_curve} out of curve"
    );
}
