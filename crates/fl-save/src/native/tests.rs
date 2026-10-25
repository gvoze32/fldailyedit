use super::*;
use crate::cpk::testing::{Entry, build_cpk};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::fs;
use std::path::PathBuf;

fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..")
}

/// The recorded native-database fixtures (local `testdata/`); `None` when absent.
fn fixtures() -> Option<Value> {
    let raw = crate::test_support::local_bytes("testdata/native/fixtures.json")?;
    Some(serde_json::from_slice(&raw).unwrap())
}

fn sha(text: &str) -> String {
    Sha256::digest(text.as_bytes())
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

// ---- canonical dumps (must equal the `dump_lines` recorded from the former Python implementation) ----

fn player_lines(database: &PlayerBinDatabase) -> Vec<String> {
    database
        .iter()
        .map(|r| {
            let abilities = r.abilities.as_ref().map_or(Value::Null, |a| {
                json!([
                    a.registered_position.code(),
                    a.position_grades,
                    a.abilities,
                    a.goalkeeping,
                    a.weak_foot_accuracy,
                    a.overall()
                ])
            });
            let position = r.position().map_or_else(
                || format!("UNKNOWN({})", r.position_index),
                |p| p.name().to_owned(),
            );
            json!([
                r.player_id,
                r.name,
                r.print_name,
                r.age,
                position,
                r.market_value_eur,
                r.youth_team_id,
                r.owner_team_key,
                r.contract_until,
                r.loan_until,
                r.caps,
                abilities
            ])
            .to_string()
        })
        .collect()
}

fn team_lines(database: &TeamBinDatabase) -> Vec<String> {
    database
        .iter()
        .map(|r| json!([r.team_key, r.name, r.abbreviation]).to_string())
        .collect()
}

fn assignment_lines(database: &PlayerAssignmentDatabase) -> Vec<String> {
    let mut lines: Vec<String> = database
        .records()
        .iter()
        .map(|r| json!([r.record_key, r.player_id, r.team_key, r.auxiliary]).to_string())
        .collect();
    // First 50 distinct non-zero players in file order.
    let mut players: Vec<u32> = Vec::new();
    for record in database.records() {
        if record.player_id != 0 && !players.contains(&record.player_id) {
            players.push(record.player_id);
            if players.len() == 50 {
                break;
            }
        }
    }
    let sample: Vec<Value> = players
        .iter()
        .map(|&id| json!([id, database.team_keys_for(id)]))
        .collect();
    lines.push(json!(["players", database.player_count(), sample]).to_string());
    lines
}

fn check_summary(name: &str, lines: &[String], entry: &Value) {
    assert_eq!(
        lines.len() as u64,
        entry["records"].as_u64().unwrap(),
        "{name}: record count"
    );
    let head: Vec<&str> = entry["head"]
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_str().unwrap())
        .collect();
    let tail: Vec<&str> = entry["tail"]
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_str().unwrap())
        .collect();
    assert_eq!(
        lines
            .iter()
            .take(head.len())
            .map(String::as_str)
            .collect::<Vec<_>>(),
        head,
        "{name}: head"
    );
    assert_eq!(
        lines
            .iter()
            .rev()
            .take(tail.len())
            .rev()
            .map(String::as_str)
            .collect::<Vec<_>>(),
        tail,
        "{name}: tail"
    );
    let text = lines.join("\n") + "\n";
    assert_eq!(
        sha(&text),
        entry["sha256"].as_str().unwrap(),
        "{name}: canonical dump sha256"
    );
}

fn check_database(label: &str, payload: &[u8], entry: &Value, name: &str) {
    match label {
        "Player.bin" => check_summary(
            name,
            &player_lines(&PlayerBinDatabase::from_bytes(payload).unwrap()),
            entry,
        ),
        "Team.bin" => check_summary(
            name,
            &team_lines(&TeamBinDatabase::from_bytes(payload).unwrap()),
            entry,
        ),
        "PlayerAssignment.bin" => {
            check_summary(
                name,
                &assignment_lines(&PlayerAssignmentDatabase::from_bytes(payload).unwrap()),
                entry,
            );
        }
        other => panic!("unknown label {other}"),
    }
}

#[test]
fn reference_databases_match_fixture() {
    let Some(fixtures) = fixtures() else {
        return;
    };
    let mut compared = 0;
    for (name, entry) in fixtures["sources"].as_object().unwrap() {
        let (path, label) = match name.split_once("::") {
            Some((cpk, label)) => (cpk, label),
            None => (name.as_str(), "Player.bin"),
        };
        let path = repo_root().join(path);
        if !path.is_file() {
            continue;
        }
        let payload = if name.contains("::") {
            let member =
                <PlayerBinDatabase as NativeDatabase>::CPK_MEMBER.replace("Player.bin", label);
            let payload = Cpk::open(&path).unwrap().read_file(&member).unwrap();
            assert_eq!(
                payload.len() as u64,
                entry["payload_size"].as_u64().unwrap(),
                "{name}: payload size"
            );
            assert_eq!(
                Sha256::digest(&payload)
                    .iter()
                    .map(|b| format!("{b:02x}"))
                    .collect::<String>(),
                entry["payload_sha256"].as_str().unwrap(),
                "{name}: payload sha256"
            );
            payload
        } else {
            fs::read(&path).unwrap()
        };
        check_database(label, &payload, entry, name);
        compared += 1;
    }
    eprintln!("reference databases compared with fixture: {compared}");
}

#[test]
fn reference_cpk_listings_match_fixture() {
    let Some(fixtures) = fixtures() else {
        return;
    };
    for (name, entry) in fixtures["cpks"].as_object().unwrap() {
        let path = repo_root().join(name);
        if !path.is_file() {
            continue;
        }
        let cpk = Cpk::open(&path).unwrap();
        let files = cpk.list_files();
        assert_eq!(
            files.len() as u64,
            entry["files"].as_u64().unwrap(),
            "{name}"
        );
        assert_eq!(
            sha(&files.join("\n")),
            entry["sha256"].as_str().unwrap(),
            "{name}: listing"
        );
        let head: Vec<&str> = entry["head"]
            .as_array()
            .unwrap()
            .iter()
            .map(|v| v.as_str().unwrap())
            .collect();
        assert_eq!(
            files
                .iter()
                .take(head.len())
                .map(String::as_str)
                .collect::<Vec<_>>(),
            head
        );
    }
}

#[test]
fn reference_game_root_discovery_and_load_order_match_fixture() {
    let Some(fixtures) = fixtures() else {
        return;
    };
    let root = repo_root().canonicalize().unwrap();
    let rel = |path: &Path| {
        path.strip_prefix(&root)
            .unwrap_or(path)
            .display()
            .to_string()
    };

    for (game_root, expected) in fixtures["game_roots"].as_object().unwrap() {
        let expected: Vec<&str> = expected
            .as_array()
            .unwrap()
            .iter()
            .map(|v| v.as_str().unwrap())
            .collect();
        if !expected.is_empty() && !root.join(game_root).is_dir() {
            continue;
        }
        let found: Vec<String> = discover::find_game_cpks(&root.join(game_root))
            .iter()
            .map(|p| rel(p))
            .collect();
        assert_eq!(found, expected, "find_game_cpks({game_root})");
    }

    for (game_root, expected) in fixtures["loads"].as_object().unwrap() {
        let locations = if game_root == "explicit-file-with-root" {
            NativeLocations {
                explicit: Some(root.join("reference/Player.bin")),
                game_root: Some(root.join("reference")),
                ..NativeLocations::default()
            }
        } else {
            NativeLocations {
                game_root: Some(root.join(game_root)),
                ..NativeLocations::default()
            }
        };
        if !locations.game_root.as_ref().is_some_and(|dir| dir.is_dir()) {
            continue;
        }
        for (label, want) in expected.as_object().unwrap() {
            let loaded = match label.as_str() {
                "Player.bin" => load_database::<PlayerBinDatabase>(&locations)
                    .loaded
                    .map(|l| (l.source, l.database.len())),
                "Team.bin" => load_database::<TeamBinDatabase>(&locations)
                    .loaded
                    .map(|l| (l.source, l.database.len())),
                _ => load_database::<PlayerAssignmentDatabase>(&locations)
                    .loaded
                    .map(|l| (l.source, l.database.len())),
            };
            let (source, records) =
                loaded.unwrap_or_else(|| panic!("{game_root} {label}: no database loaded"));
            let source = match &source {
                DatabaseSource::File(path) => rel(path),
                DatabaseSource::Archive { cpk, label } => format!("{}::{label}", rel(cpk)),
            };
            assert_eq!(
                source,
                want["source"].as_str().unwrap(),
                "{game_root} {label}: source"
            );
            assert_eq!(
                records as u64,
                want["records"].as_u64().unwrap(),
                "{game_root} {label}: records"
            );
        }
    }
}

// ---- loader behaviour on synthetic inputs ----

fn player_bin_with(id: u32, name: &str) -> Vec<u8> {
    let mut raw = vec![0u8; player_bin::RECORD_SIZE];
    raw[8..12].copy_from_slice(&id.to_le_bytes());
    raw[0x44..0x44 + name.len()].copy_from_slice(name.as_bytes());
    raw[0x36] = 1 << 2;
    raw
}

fn cpk_with_player_bin(payload: &[u8]) -> Vec<u8> {
    build_cpk(
        &[Entry {
            dir: "common/etc/pesdb",
            name: "Player.bin",
            payload,
        }],
        None,
        false,
    )
}

fn game_root_with(archives: &[(&str, Vec<u8>)]) -> tempfile::TempDir {
    let root = tempfile::tempdir().unwrap();
    for (name, bytes) in archives {
        let path = root.path().join("download").join(name);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, bytes).unwrap();
    }
    root
}

#[test]
fn archives_are_searched_in_overlay_order_and_skip_missing_members() {
    let empty_cpk = build_cpk(
        &[Entry {
            dir: "x",
            name: "y.bin",
            payload: b"y",
        }],
        None,
        false,
    );
    let root = game_root_with(&[
        ("data_extra.cpk", empty_cpk),
        (
            "data_s2526.cpk",
            cpk_with_player_bin(&player_bin_with(7, "From s2526")),
        ),
    ]);
    let locations = NativeLocations {
        game_root: Some(root.path().to_path_buf()),
        ..Default::default()
    };
    let outcome = load_database::<PlayerBinDatabase>(&locations);
    let loaded = outcome.loaded.unwrap();
    assert!(outcome.warnings.is_empty());
    assert_eq!(loaded.database.get(7).unwrap().name, "From s2526");
    assert!(
        matches!(&loaded.source, DatabaseSource::Archive { cpk, .. } if cpk.ends_with("data_s2526.cpk"))
    );
    assert!(
        loaded
            .source
            .to_string()
            .ends_with("data_s2526.cpk::Player.bin")
    );

    let both = game_root_with(&[
        (
            "data_extra.cpk",
            cpk_with_player_bin(&player_bin_with(1, "Extra")),
        ),
        (
            "data_s2526.cpk",
            cpk_with_player_bin(&player_bin_with(2, "S2526")),
        ),
    ]);
    let locations = NativeLocations {
        game_root: Some(both.path().to_path_buf()),
        ..Default::default()
    };
    let loaded = load_database::<PlayerBinDatabase>(&locations)
        .loaded
        .unwrap();
    assert!(loaded.database.contains(1) && !loaded.database.contains(2));
}

#[test]
fn explicit_file_beats_archives_but_configured_file_does_not() {
    let root = game_root_with(&[(
        "data_extra.cpk",
        cpk_with_player_bin(&player_bin_with(1, "Archive")),
    )]);
    let files = tempfile::tempdir().unwrap();
    let explicit = files.path().join("explicit.bin");
    let configured = files.path().join("configured.bin");
    fs::write(&explicit, player_bin_with(2, "Explicit")).unwrap();
    fs::write(&configured, player_bin_with(3, "Configured")).unwrap();

    let locations = NativeLocations {
        explicit: Some(explicit.clone()),
        configured: Some(configured.clone()),
        game_root: Some(root.path().to_path_buf()),
    };
    let loaded = load_database::<PlayerBinDatabase>(&locations)
        .loaded
        .unwrap();
    assert_eq!(loaded.source, DatabaseSource::File(explicit));
    assert!(loaded.database.contains(2));

    let locations = NativeLocations {
        explicit: None,
        ..locations
    };
    let loaded = load_database::<PlayerBinDatabase>(&locations)
        .loaded
        .unwrap();
    assert!(
        loaded.database.contains(1),
        "archive wins over configured file"
    );

    let no_root = NativeLocations {
        configured: Some(configured.clone()),
        ..Default::default()
    };
    assert_eq!(
        load_database::<PlayerBinDatabase>(&no_root)
            .loaded
            .unwrap()
            .source,
        DatabaseSource::File(configured)
    );
}

#[test]
fn invalid_candidates_are_skipped_with_warnings() {
    let files = tempfile::tempdir().unwrap();
    let broken = files.path().join("broken.bin");
    fs::write(&broken, [1u8; 13]).unwrap();
    let root = game_root_with(&[
        ("data_extra.cpk", cpk_with_player_bin(&[1u8; 13])),
        ("data_s2526.cpk", b"not a cpk".to_vec()),
        (
            "z_more.cpk",
            cpk_with_player_bin(&player_bin_with(9, "Fallback")),
        ),
    ]);
    let locations = NativeLocations {
        explicit: Some(broken),
        game_root: Some(root.path().to_path_buf()),
        ..Default::default()
    };
    let outcome = load_database::<PlayerBinDatabase>(&locations);
    assert!(outcome.loaded.unwrap().database.contains(9));
    assert_eq!(outcome.warnings.len(), 3, "{:?}", outcome.warnings);
    assert!(outcome.warnings[0].contains("invalid Player.bin metadata"));
    assert!(
        outcome
            .warnings
            .iter()
            .any(|w| w.contains("unreadable Player.bin metadata"))
    );

    let nothing = load_database::<TeamBinDatabase>(&NativeLocations::default());
    assert!(nothing.loaded.is_none() && nothing.warnings.is_empty());
    let missing = NativeLocations {
        explicit: Some(files.path().join("absent.bin")),
        ..Default::default()
    };
    assert!(
        load_database::<PlayerBinDatabase>(&missing)
            .loaded
            .is_none()
    );
}

// ---- ported behavioural checks against real game archives (skipped when absent) ----

fn member(label: &str) -> String {
    format!("common/etc/pesdb/{label}")
}

#[test]
fn data_s2526_archive_indexes_known_player() {
    let Some(path) = crate::test_support::local_path("reference/data_s2526.cpk") else {
        return;
    };
    let mut cpk = Cpk::open(&path).unwrap();
    assert_eq!(cpk.list_files().len(), 20);
    assert!(
        cpk.list_files()
            .iter()
            .any(|name| name.ends_with("/Player.bin"))
    );
    let payload = cpk.read_file("Player.bin").unwrap();
    assert!(payload.len() > 1_000_000);

    let database = PlayerBinDatabase::from_bytes(&payload).unwrap();
    assert!(database.len() > 25_000);
    let record = database.get(162_196).unwrap();
    assert_eq!(record.overall(), 69);
    assert_eq!(
        (record.name.as_str(), record.print_name.as_str(), record.age),
        ("Marco Palestra", "PALESTRA", 20)
    );
    assert_eq!(record.position(), Some(crate::model::Position::Rb));
    assert_eq!(
        (
            record.market_value_eur,
            record.youth_team_id,
            record.owner_team_key
        ),
        (0, 0, 0)
    );
    assert_eq!(
        (record.contract_until, record.loan_until, record.caps),
        (20_250_630, 0, 0)
    );
}

#[test]
fn data_extra_archive_indexes_teams_and_assignments() {
    let Some(path) = crate::test_support::local_path("reference/data_extra.cpk") else {
        return;
    };
    let mut cpk = Cpk::open(&path).unwrap();
    let teams = TeamBinDatabase::from_bytes(&cpk.read_file("Team.bin").unwrap()).unwrap();
    let assignments =
        PlayerAssignmentDatabase::from_bytes(&cpk.read_file("PlayerAssignment.bin").unwrap())
            .unwrap();
    assert_eq!(teams.len(), 749);
    let cagliari = teams.get(320).unwrap();
    assert_eq!(
        (cagliari.name.as_str(), cagliari.abbreviation.as_str()),
        ("Cagliari Calcio", "CAG")
    );
    assert_eq!(assignments.len(), 21_364);
    assert_eq!(assignments.player_count(), 19_415);
    assert_eq!(assignments.team_keys_for(162_196), [12, 320]);
    assert!(assignments.team_keys_for(1_073_003).is_empty());
}

#[test]
fn t99_protected_toc_archive_extracts_native_databases() {
    let Some(path) =
        crate::test_support::local_path("reference/T99 Patch V10/t99p21_v10_liveupd.cpk")
    else {
        return;
    };
    let mut cpk = Cpk::open(&path).unwrap();
    for label in ["Player.bin", "Team.bin", "PlayerAssignment.bin"] {
        assert!(cpk.list_files().contains(&member(label)), "{label}");
    }
    let players = PlayerBinDatabase::from_bytes(&cpk.read_file("Player.bin").unwrap()).unwrap();
    let teams = TeamBinDatabase::from_bytes(&cpk.read_file("Team.bin").unwrap()).unwrap();
    let assignments =
        PlayerAssignmentDatabase::from_bytes(&cpk.read_file("PlayerAssignment.bin").unwrap())
            .unwrap();
    assert_eq!(
        (players.len(), teams.len(), assignments.len()),
        (21_962, 698, 21_172)
    );
    assert!(players.contains(38_439));
}
