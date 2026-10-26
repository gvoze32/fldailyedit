//! Plan-level golden: Rust planning over the inputs of the recorded run.
//!
//! The recorded run (made with the former Python implementation, now removed) replays an e2e scenario and dumps, per
//! planning stage, the exact inputs and outputs (`plan.json.gz`). This test
//! rebuilds the match database and club identity from the scenario's base
//! save and Player.bin, feeds the dumped inputs to `fl_engine::planning`, and
//! compares every stage.
//!
//! `testdata/` is local (not in the repository): every test skips with a note when the plan
//! dump, the scenario's recorded inputs, the base save or `testdata/fotmob_teams.json` is absent.

mod common;

use std::collections::{BTreeMap, HashMap, HashSet};
use std::io::Read;
use std::path::Path;
use std::sync::Arc;

use common::{base_save_path, existing, repo_root, testdata_path};
use fl_engine::club_identity::{ClubIdentityIndex, IdentityCache};
use fl_engine::match_db::build_match_database;
use fl_engine::matcher::NameMatcher;
use fl_engine::native::{NativeFiles, load_native};
use fl_engine::planning::{
    HistoryEntry, MatchInput, MatchedTransfer, PlanInput, PlannedRosterAction, RosterAction,
    RosterPlanInput, RosterReader, Side, build_superseded_loan_sources,
    dedupe_shirt_number_matches, match_transfers_statefully, plan_captain_updates,
    plan_roster_actions, plan_shirt_batch, plan_transfers,
};
use fl_save::crypto;
use fl_save::edit_file::EditFile;
use fl_save::model::{NativePlayers, TeamData};
use fl_sources::model::{CaptainUpdate, ScrapeResult, SquadSnapshot, TacticalUpdate, Transfer};
use fl_sources::team_index::{TeamEntry, parse_catalog};
use serde_json::{Value, json};

/// The recorded FotMob team catalog (test fixture; the app downloads it at run time);
/// `None` (with a skip note) when it is not on this machine.
fn fixture_catalog() -> Option<Vec<TeamEntry>> {
    let text = std::fs::read_to_string(testdata_path("fotmob_teams.json")?).unwrap();
    Some(parse_catalog(&text).unwrap())
}

fn gunzip(path: &Path) -> Vec<u8> {
    let mut bytes = Vec::new();
    flate2::read::GzDecoder::new(std::fs::File::open(path).unwrap())
        .read_to_end(&mut bytes)
        .unwrap();
    bytes
}

// ── JSON helpers ─────────────────────────────────────────────────────────

fn json_eq(a: &Value, b: &Value) -> bool {
    match (a, b) {
        (Value::Number(x), Value::Number(y)) => match (x.as_i64(), y.as_i64()) {
            (Some(x), Some(y)) => x == y,
            _ => (x.as_f64().unwrap() - y.as_f64().unwrap()).abs() < 1e-9,
        },
        (Value::Array(x), Value::Array(y)) => {
            x.len() == y.len() && x.iter().zip(y).all(|(a, b)| json_eq(a, b))
        }
        (Value::Object(x), Value::Object(y)) => {
            x.len() == y.len()
                && x.iter()
                    .all(|(k, v)| y.get(k).is_some_and(|w| json_eq(v, w)))
        }
        _ => a == b,
    }
}

/// Path and values of the first difference between two JSON values.
fn first_diff(a: &Value, b: &Value, path: &str) -> String {
    match (a, b) {
        (Value::Object(x), Value::Object(y)) => {
            for (key, value) in x {
                match y.get(key) {
                    Some(other) if json_eq(value, other) => {}
                    Some(other) => return first_diff(value, other, &format!("{path}.{key}")),
                    None => return format!("{path}.{key} (missing in the golden dump)"),
                }
            }
            if let Some(key) = y.keys().find(|k| !x.contains_key(*k)) {
                return format!("{path}.{key} (missing in rust)");
            }
            path.to_owned()
        }
        (Value::Array(x), Value::Array(y)) => {
            for (index, (value, other)) in x.iter().zip(y).enumerate() {
                if !json_eq(value, other) {
                    return first_diff(value, other, &format!("{path}[{index}]"));
                }
            }
            format!("{path} (length {} vs {})", x.len(), y.len())
        }
        _ => format!("{path}: rust {a} vs golden {b}"),
    }
}

/// Compare two row lists, reporting the first difference.
fn assert_rows(label: &str, rust: &[Value], golden: &[Value]) {
    for (index, (r, p)) in rust.iter().zip(golden).enumerate() {
        if !json_eq(r, p) {
            panic!(
                "{label}: row {index} differs at {}\n rust:   {}\n golden: {}",
                first_diff(r, p, ""),
                serde_json::to_string(r).unwrap(),
                serde_json::to_string(p).unwrap()
            );
        }
    }
    assert_eq!(
        rust.len(),
        golden.len(),
        "{label}: row count (rust {} vs golden {}); first extra: {}",
        rust.len(),
        golden.len(),
        rust.get(golden.len())
            .or_else(|| golden.get(rust.len()))
            .map(|v| serde_json::to_string(v).unwrap())
            .unwrap_or_default()
    );
}

/// The recorded expectations of the plan stages. Normally each stage is
/// compared; with `FL_E2E_REBASELINE` set the engine's own rows replace them
/// (see `testdata/DEVIATIONS.md`, "Baseline") and the dump is rewritten.
struct Baseline {
    rewrite: bool,
    rows: serde_json::Map<String, Value>,
}

impl Baseline {
    fn new() -> Self {
        Self {
            rewrite: std::env::var_os("FL_E2E_REBASELINE").is_some(),
            rows: serde_json::Map::new(),
        }
    }

    /// Compare (or record) one stage's rows.
    fn rows(&mut self, key: &str, label: &str, rust: &[Value], golden: &[Value]) {
        if self.rewrite {
            self.rows
                .insert(key.to_owned(), Value::Array(rust.to_vec()));
        } else {
            assert_rows(label, rust, golden);
        }
    }

    /// Compare (or record) one stage's scalar or map value.
    fn value(&mut self, key: &str, label: &str, rust: Value, golden: &Value) {
        if self.rewrite {
            self.rows.insert(key.to_owned(), rust);
        } else {
            assert!(
                json_eq(&rust, golden),
                "{label}: rust {rust} vs golden {golden}"
            );
        }
    }
}

fn int(value: &Value) -> Option<i64> {
    match value {
        Value::Number(n) => n.as_i64().or_else(|| n.as_f64().map(|f| f as i64)),
        Value::String(s) => s.trim().parse().ok(),
        _ => None,
    }
}

fn positive_u32(value: &Value) -> Option<u32> {
    int(value).filter(|&v| v > 0).map(|v| v as u32)
}

fn side(value: &Value) -> Side {
    match int(value) {
        None => Side::Outside,
        Some(id) if id < 0 => Side::Unresolved,
        Some(id) => Side::Team(id as u32),
    }
}

fn side_json(side: Side) -> Value {
    match side {
        Side::Outside => Value::Null,
        Side::Unresolved => json!(-1),
        Side::Team(id) => json!(id),
    }
}

const RECORDED_ONLY_TRANSFER_KEYS: [&str; 3] = ["league", "season", "is_contract_extension"];

fn transfer_json(transfer: &Transfer) -> Value {
    serde_json::to_value(transfer).unwrap()
}

fn golden_transfer(value: &Value) -> Value {
    let mut value = value.clone();
    for key in RECORDED_ONLY_TRANSFER_KEYS {
        value.as_object_mut().unwrap().remove(key);
    }
    value
}

fn transfer_from(value: &Value) -> Transfer {
    serde_json::from_value(value.clone()).unwrap_or_else(|e| panic!("transfer {value}: {e}"))
}

fn match_json(item: &MatchedTransfer) -> Value {
    json!({
        "transfer": transfer_json(&item.transfer),
        "player_id": item.player_id,
        "from_team_id": side_json(item.from),
        "to_team_id": side_json(item.to),
        "player_confidence": item.player_confidence,
        "from_team_confidence": item.from_confidence,
        "to_team_confidence": item.to_confidence,
        "matched_player_name": item.matched_player_name,
        "matched_from_team": item.matched_from_team,
        "matched_to_team": item.matched_to_team,
        "skip": item.skip.as_ref().map(|s| json!({
            "reason": s.reason, "detail": s.detail, "candidates": s.candidates,
        })),
    })
}

fn golden_match(value: &Value) -> Value {
    let mut value = value.clone();
    let transfer = golden_transfer(&value["transfer"]);
    value["transfer"] = transfer;
    value
}

fn matched_from_json(value: &Value) -> MatchedTransfer {
    let mut item = MatchedTransfer::new(transfer_from(&value["transfer"]));
    item.player_id = positive_u32(&value["player_id"]);
    item.from = side(&value["from_team_id"]);
    item.to = side(&value["to_team_id"]);
    item.player_confidence = value["player_confidence"].as_f64().unwrap();
    item.from_confidence = value["from_team_confidence"].as_f64().unwrap();
    item.to_confidence = value["to_team_confidence"].as_f64().unwrap();
    item.matched_player_name = value["matched_player_name"].as_str().unwrap().to_owned();
    item.matched_from_team = value["matched_from_team"].as_str().unwrap().to_owned();
    item.matched_to_team = value["matched_to_team"].as_str().unwrap().to_owned();
    item
}

fn action_json(item: &PlannedRosterAction) -> Value {
    // The recorded action row carries no matching-stage skip.
    let mut matched = match_json(&item.matched);
    matched["skip"] = Value::Null;
    json!({
        "match": matched,
        "action": item.action.as_str(),
        "current_team_id": item.current_team_id,
        "reason": item.reason,
        "detail": item.detail,
        "overflow_player_id": item.overflow_player_id,
        // The recorded dict is compared by presence and player; the field-level
        // shape is pinned by fl-save's overflow parity tests.
        "overflow_details": item.overflow.as_ref().map(|d| json!({"player_id": d.player_id})),
    })
}

fn golden_action(value: &Value) -> Value {
    let mut value = value.clone();
    let matched = golden_match(&value["match"]);
    value["match"] = matched;
    let details = value["overflow_details"]
        .as_object()
        .map(|d| json!({"player_id": d["player_id"]}));
    value["overflow_details"] = details.unwrap_or(Value::Null);
    value
}

fn skipped_json(row: &fl_engine::planning::SkippedTransfer) -> Value {
    let mut value = serde_json::to_value(row).unwrap();
    value.as_object_mut().unwrap().remove("retryable");
    value
}

// ── input parsing ───────────────────────────────────────────────────────

fn rosters_from(value: &Value) -> Vec<TeamData> {
    value
        .as_object()
        .unwrap()
        .iter()
        .map(|(team, roster)| {
            let mut data = TeamData::empty(team.parse().unwrap());
            for (slot, id) in roster["player_ids"].as_array().unwrap().iter().enumerate() {
                data.player_ids[slot] = int(id).unwrap() as u32;
            }
            for (slot, n) in roster["shirt_numbers"]
                .as_array()
                .unwrap()
                .iter()
                .enumerate()
            {
                data.shirt_numbers[slot] = int(n).unwrap() as u16;
            }
            data
        })
        .collect()
}

fn history_from(value: &Value) -> Vec<HistoryEntry> {
    value
        .as_array()
        .unwrap()
        .iter()
        .map(|entry| HistoryEntry {
            player_id: positive_u32(&entry["player_id"]),
            from_team_id: positive_u32(&entry["from_team_id"]),
            to_team_id: positive_u32(&entry["to_team_id"]),
            fotmob_player_id: int(&entry["fotmob_player_id"]).filter(|&v| v > 0),
            player_name: entry["player_name"].as_str().unwrap_or("").to_owned(),
            transfer_type: entry["transfer_type"].as_str().unwrap_or("").to_owned(),
            transfer_date: entry["transfer_date"].as_str().unwrap_or("").to_owned(),
            timestamp: entry["timestamp"].as_str().unwrap_or("").to_owned(),
        })
        .collect()
}

fn list<T: serde::de::DeserializeOwned>(value: &Value) -> Vec<T> {
    value
        .as_array()
        .unwrap()
        .iter()
        .map(|v| serde_json::from_value(v.clone()).unwrap_or_else(|e| panic!("{v}: {e}")))
        .collect()
}

fn scrape_from(value: &Value) -> ScrapeResult {
    ScrapeResult {
        transfers: value["transfers"]
            .as_array()
            .unwrap()
            .iter()
            .map(transfer_from)
            .collect(),
        roster_updates: value["roster_updates"]
            .as_array()
            .unwrap()
            .iter()
            .map(transfer_from)
            .collect(),
        pending_transfers: value["pending_transfers"]
            .as_array()
            .unwrap()
            .iter()
            .map(transfer_from)
            .collect(),
        squad_snapshots: list::<SquadSnapshot>(&value["squad_snapshots"]),
        captain_updates: list::<CaptainUpdate>(&value["captain_updates"]),
        tactical_updates: list::<TacticalUpdate>(&value["tactical_updates"]),
    }
}

fn id_map(value: &Value) -> HashMap<i64, u32> {
    value
        .as_object()
        .unwrap()
        .iter()
        .map(|(k, v)| (k.parse().unwrap(), int(v).unwrap() as u32))
        .collect()
}

fn roster_map(value: &Value) -> HashMap<u32, Vec<u32>> {
    value
        .as_object()
        .unwrap()
        .iter()
        .map(|(k, v)| {
            (
                k.parse().unwrap(),
                v.as_array()
                    .unwrap()
                    .iter()
                    .map(|p| int(p).unwrap() as u32)
                    .collect(),
            )
        })
        .collect()
}

fn id_set(value: &Value) -> HashSet<u32> {
    value
        .as_array()
        .unwrap()
        .iter()
        .map(|v| int(v).unwrap() as u32)
        .collect()
}

// ── the world a scenario ran in ──────────────────────────────────────────

struct World {
    edit: EditFile,
    matcher: NameMatcher,
    identity: ClubIdentityIndex,
    player_names: HashMap<u32, String>,
    club_ids: HashSet<u32>,
    _dir: tempfile::TempDir,
}

fn world(scenario_dir: &Path, local_dir: &Path, snapshots: &[SquadSnapshot]) -> Option<World> {
    let player_bin_gz = existing(local_dir.join("inputs/Player.bin.gz"))?;
    let team_bin_gz = existing(local_dir.join("inputs/Team.bin.gz"))?;
    let meta_path = existing(scenario_dir.join("meta.json"))?;
    let meta: Value = serde_json::from_slice(&std::fs::read(meta_path).unwrap()).unwrap();
    let input = base_save_path(&meta)?;
    let catalog = fixture_catalog()?;
    let dir = tempfile::tempdir().unwrap();
    let player_bin = dir.path().join("Player.bin");
    std::fs::write(&player_bin, gunzip(&player_bin_gz)).unwrap();
    let team_bin = dir.path().join("Team.bin");
    std::fs::write(&team_bin, gunzip(&team_bin_gz)).unwrap();
    let native = load_native(
        None,
        &NativeFiles {
            player_bin: Some(player_bin),
            team_bin: Some(team_bin),
            ..NativeFiles::default()
        },
    );
    let native_players: Option<Arc<dyn NativePlayers>> = native
        .player_bin
        .as_ref()
        .map(|loaded| Arc::clone(&loaded.database) as Arc<dyn NativePlayers>);

    let raw = std::fs::read(input).unwrap();
    let mut blocks = crypto::decrypt(&raw).unwrap();
    let header = blocks.save_header();
    let mut edit = EditFile::from_bytes(std::mem::take(&mut blocks.data)).unwrap();
    edit.set_pes21(header.is_pes21());
    edit.attach_native(native_players.clone());
    edit.load_player_catalog();

    let teams = native.team_bin.as_ref().map(|loaded| &*loaded.database);
    let database = build_match_database(&edit, native_players.as_deref(), teams);
    let club_ids: HashSet<u32> = edit.club_team_ids().iter().copied().collect();
    let mut matcher = database.matcher;
    let mut identity = ClubIdentityIndex::build(
        &database.club_names,
        &catalog,
        IdentityCache::new(
            dir.path().join("club_identity_cache.json"),
            "golden".to_owned(),
        ),
    );
    matcher.set_team_aliases(identity.aliases());
    // The pipeline confirms club identities from complete live squads before
    // planning.
    let evidence = identity.prepare_rosters(&database.rosters, &database.player_names);
    for snapshot in snapshots.iter().filter(|snapshot| snapshot.complete) {
        identity.learn_from_snapshot(snapshot, &evidence);
    }
    Some(World {
        edit,
        matcher,
        identity,
        player_names: database.player_names,
        club_ids,
        _dir: dir,
    })
}

struct Reader(HashMap<u32, (Vec<u32>, Vec<u32>)>);

impl RosterReader for Reader {
    fn roster(&self, team_id: u32) -> Option<(Vec<u32>, Vec<u32>)> {
        self.0.get(&team_id).cloned()
    }
}

fn run_scenario(scenario: &str, scenario_dir: &Path, local_dir: &Path, plan_path: &Path) {
    if existing(plan_path.to_path_buf()).is_none() {
        return;
    }
    let doc: Value = serde_json::from_slice(&gunzip(plan_path)).unwrap();
    let plan = &doc["plan"];
    let expected = &plan["expected"];
    let scrape = scrape_from(&plan["scrape"]);
    let started = std::time::Instant::now();
    let lap = |what: &str| eprintln!("[{scenario}] {what}: {:.1?}", started.elapsed());
    let Some(world) = world(scenario_dir, local_dir, &scrape.squad_snapshots) else {
        return;
    };

    let rosters = rosters_from(&plan["rosters"]);
    // The dumped rosters are the recorded inputs; the save must agree.
    let from_save: Vec<TeamData> = world.edit.rosters();
    assert_eq!(rosters.len(), from_save.len(), "{scenario}: roster count");
    for (dumped, saved) in rosters.iter().zip(&from_save) {
        assert_eq!(
            dumped, saved,
            "{scenario}: roster of team {}",
            dumped.team_id
        );
    }
    let identity_bindings: BTreeMap<i64, u32> = plan["club_identity"]
        .as_array()
        .unwrap()
        .iter()
        .map(|pair| (int(&pair[0]).unwrap(), int(&pair[1]).unwrap() as u32))
        .collect();
    let rust_bindings: BTreeMap<i64, u32> = world
        .identity
        .entries()
        .into_iter()
        .map(|e| (e.fotmob_id, e.pes_team_id))
        .collect();
    assert_eq!(
        rust_bindings, identity_bindings,
        "{scenario}: club identity bindings"
    );
    assert_eq!(
        world.club_ids,
        id_set(&plan["club_ids"]),
        "{scenario}: club ids"
    );

    let history = history_from(&plan["history"]);
    let threshold = plan["threshold"].as_f64().unwrap();
    let present: Vec<(u32, Vec<u32>)> = rosters
        .iter()
        .map(|t| (t.team_id, t.roster().collect()))
        .collect();
    let shirt_numbers: fl_engine::planning::ShirtNumbers = rosters
        .iter()
        .map(|team| {
            let numbers = team
                .player_ids
                .iter()
                .zip(&team.shirt_numbers)
                .filter(|(p, n)| **p != 0 && **n > 0)
                .map(|(p, n)| (*p, u32::from(*n)))
                .collect();
            (team.team_id, numbers)
        })
        .collect();
    let events: Vec<Transfer> = scrape
        .transfers
        .iter()
        .chain(&scrape.roster_updates)
        .cloned()
        .collect();
    // The names come from the game's Player.bin like in a real run; the dump
    // used to carry the removed bundled catalog.
    let dump_names = &world.player_names;

    lap("world built and inputs parsed");
    // Stage 1: matching.
    let out = match_transfers_statefully(&MatchInput {
        transfers: &events,
        matcher: &world.matcher,
        threshold,
        teams: &present,
        club_ids: &world.club_ids,
        history: &history,
        club_identity: Some(&world.identity),
        squad_snapshots: &scrape.squad_snapshots,
        player_names: dump_names,
        team_shirt_numbers: Some(&shirt_numbers),
    });
    let mut baseline = Baseline::new();
    let rust: Vec<Value> = out.matched.iter().map(match_json).collect();
    let golden: Vec<Value> = expected["matched_before_dedupe"]
        .as_array()
        .unwrap()
        .iter()
        .map(golden_match)
        .collect();
    baseline.rows(
        "matched_before_dedupe",
        &format!("{scenario}: matched events"),
        &rust,
        &golden,
    );
    baseline.value(
        "fotmob_player_ids",
        &format!("{scenario}: fotmob player ids"),
        serde_json::to_value(sorted_map(&out.report.fotmob_player_ids)).unwrap(),
        &sorted_object_map(&expected["fotmob_player_ids"]),
    );

    lap("matching compared");
    // Stage 2: shirt dedupe.
    let deduped = dedupe_shirt_number_matches(out.matched.clone());
    let rust: Vec<Value> = deduped.kept.iter().map(match_json).collect();
    let golden: Vec<Value> = expected["matched_after_dedupe"]
        .as_array()
        .unwrap()
        .iter()
        .map(golden_match)
        .collect();
    baseline.rows(
        "matched_after_dedupe",
        &format!("{scenario}: deduped events"),
        &rust,
        &golden,
    );
    baseline.value(
        "duplicate_shirt_dropped",
        &format!("{scenario}: discarded shirt observations"),
        json!(deduped.discarded),
        &expected["duplicate_shirt_dropped"],
    );

    lap("dedupe compared");
    // Stage 3: superseded loan sources.
    let superseded = build_superseded_loan_sources(&deduped.kept, &history);
    let rust: Vec<Value> = superseded
        .iter()
        .map(|set| {
            let mut ids: Vec<u32> = set.iter().copied().collect();
            ids.sort_unstable();
            json!(ids)
        })
        .collect();
    baseline.rows(
        "superseded",
        &format!("{scenario}: superseded loan sources"),
        &rust,
        expected["superseded"].as_array().unwrap(),
    );

    lap("superseded compared");
    // Stage 4: roster actions.
    let slots: Vec<(u32, Vec<u32>)> = rosters
        .iter()
        .map(|t| (t.team_id, t.player_ids.to_vec()))
        .collect();
    let allow_overflow = plan["allow_overflow_release"].as_bool().unwrap();
    let actions = plan_roster_actions(&RosterPlanInput {
        matches: &deduped.kept,
        teams: &slots,
        club_ids: &world.club_ids,
        overflow: &world.edit,
        superseded_loan_sources: &superseded,
        already_applied: &[],
        allow_overflow_release: allow_overflow,
        live_squad_ids: &out.report.live_squad_ids,
    });
    let rust: Vec<Value> = actions.actions.iter().map(action_json).collect();
    let golden: Vec<Value> = expected["roster_actions"]
        .as_array()
        .unwrap()
        .iter()
        .map(golden_action)
        .collect();
    baseline.rows(
        "roster_actions",
        &format!("{scenario}: roster actions"),
        &rust,
        &golden,
    );

    lap("roster actions compared");
    // Stage 5: the whole plan.
    let planned = plan_transfers(&PlanInput {
        scrape: &scrape,
        matcher: &world.matcher,
        club_identity: Some(&world.identity),
        threshold,
        rosters: &rosters,
        club_ids: &world.club_ids,
        history: &history,
        player_names: dump_names,
        overflow: &world.edit,
        allow_overflow_release: allow_overflow,
        today: plan["today"].as_str().unwrap().parse().unwrap(),
    });
    let rust: Vec<Value> = planned
        .skipped
        .iter()
        .filter(|row| !row.reason.starts_with("shirt_number_"))
        .map(skipped_json)
        .collect();
    baseline.rows(
        "skipped",
        &format!("{scenario}: skipped rows"),
        &rust,
        expected["skipped"].as_array().unwrap(),
    );
    let formations: Vec<Value> = planned
        .formations
        .iter()
        .map(|(t, f)| json!([t, f]))
        .collect();
    baseline.rows(
        "formations",
        &format!("{scenario}: formations"),
        &formations,
        expected["formations"].as_array().unwrap(),
    );
    let tactics: Vec<Value> = planned.tactics.iter().map(|(t, s)| json!([t, s])).collect();
    baseline.rows(
        "tactics",
        &format!("{scenario}: tactics"),
        &tactics,
        expected["tactics"].as_array().unwrap(),
    );
    baseline.value(
        "fotmob_team_map",
        &format!("{scenario}: fotmob team map"),
        serde_json::to_value(sorted_map(&planned.fotmob_team_map)).unwrap(),
        &sorted_object_map(&expected["fotmob_team_map"]),
    );
    lap("whole plan compared");
    // Shirt batches (rosters as they stood when the recorded run applied them).
    for (index, batch) in doc["shirt_batches"].as_array().unwrap().iter().enumerate() {
        let team = int(&batch["team_id"]).map(|t| t as u32);
        let mut rosters = HashMap::new();
        if let (Some(team), false) = (team, batch["roster"].is_null()) {
            let ids = batch["roster"]["player_ids"].as_array().unwrap();
            let numbers = batch["roster"]["shirt_numbers"].as_array().unwrap();
            rosters.insert(
                team,
                (
                    ids.iter().map(|v| int(v).unwrap() as u32).collect(),
                    numbers.iter().map(|v| int(v).unwrap() as u32).collect(),
                ),
            );
        }
        let actions: Vec<PlannedRosterAction> = batch["actions"]
            .as_array()
            .unwrap()
            .iter()
            .map(|a| {
                let mut action = PlannedRosterAction::new(
                    matched_from_json(&golden_match(&a["match"])),
                    RosterAction::ShirtUpdate,
                    int(&a["current_team_id"]).map(|t| t as u32),
                );
                action.reason = a["reason"].as_str().unwrap().to_owned();
                action
            })
            .collect();
        let arriving: HashSet<(u32, u32)> = batch["arriving"]
            .as_array()
            .unwrap()
            .iter()
            .map(|pair| (int(&pair[0]).unwrap() as u32, int(&pair[1]).unwrap() as u32))
            .collect();
        let refs: Vec<&PlannedRosterAction> = actions.iter().collect();
        let rust: Vec<Value> = plan_shirt_batch(&Reader(rosters), &refs, &arriving)
            .into_iter()
            .map(|status| match status {
                Some(s) => json!([s.previous, s.conflict_player, s.reason]),
                None => json!([]),
            })
            .collect();
        assert_rows(
            &format!("{scenario}: shirt batch {index}"),
            &rust,
            batch["statuses"].as_array().unwrap(),
        );
    }

    lap("shirt batches compared");
    // Captains, after the run's roster changes.
    let mut captain_rows: Vec<Value> = Vec::new();
    for (index, call) in doc["captains"].as_array().unwrap().iter().enumerate() {
        let updates: Vec<CaptainUpdate> = list(&call["updates"]);
        let result = plan_captain_updates(
            &updates,
            &world.matcher,
            &roster_map(&call["team_player_map"]),
            &id_set(&call["club_ids"]),
            &id_map(&call["fotmob_team_map"]),
            call["threshold"].as_f64().unwrap(),
            &id_map(&call["fotmob_player_ids"]),
        );
        let rust: Vec<Value> = result
            .iter()
            .map(|item| {
                json!({
                    "source": serde_json::to_value(&item.source).unwrap(),
                    "team_id": item.team_id,
                    "player_id": item.player_id,
                    "matched_player_name": item.matched_player_name,
                    "confidence": item.confidence,
                })
            })
            .collect();
        if baseline.rewrite {
            captain_rows.push(Value::Array(rust));
        } else {
            assert_rows(
                &format!("{scenario}: captains {index}"),
                &rust,
                call["result"].as_array().unwrap(),
            );
        }
    }

    lap("captains compared");
    let _ = RosterAction::Skip;

    if baseline.rewrite {
        let mut rewritten = doc.clone();
        rewritten["plan"]["expected"] = Value::Object(baseline.rows);
        rewritten["plan"]
            .as_object_mut()
            .unwrap()
            .remove("player_names");
        // The engine no longer reads match lineups.
        rewritten.as_object_mut().unwrap().remove("preferences");
        for (call, rows) in rewritten["captains"]
            .as_array_mut()
            .unwrap()
            .iter_mut()
            .zip(captain_rows)
        {
            call["result"] = rows;
        }
        let mut encoder = flate2::GzBuilder::new().write(Vec::new(), flate2::Compression::best());
        std::io::Write::write_all(&mut encoder, rewritten.to_string().as_bytes()).unwrap();
        std::fs::write(plan_path, encoder.finish().unwrap()).unwrap();
    }
}

fn sorted_map<K: Ord + std::hash::Hash + Eq + Clone + ToString, V: Clone + serde::Serialize>(
    map: &HashMap<K, V>,
) -> BTreeMap<String, V> {
    map.iter()
        .map(|(k, v)| (k.to_string(), v.clone()))
        .collect()
}

/// A JSON object as a key-sorted object (the maps compared here are
/// unordered).
fn sorted_object(value: &Value) -> Value {
    let sorted: BTreeMap<String, Value> = value
        .as_object()
        .unwrap()
        .iter()
        .map(|(k, v)| (k.clone(), v.clone()))
        .collect();
    serde_json::to_value(sorted).unwrap()
}

fn sorted_object_map(value: &Value) -> Value {
    sorted_object(value)
}

#[test]
fn plan_matches_golden_on_the_small_scenario() {
    let Some(dir) = testdata_path("e2e/small") else {
        return;
    };
    run_scenario(
        "small",
        &dir,
        &repo_root().join("testdata/local/e2e/small"),
        &dir.join("plan.json.gz"),
    );
}

// ~6 min with optimised deps; needs the local testdata/local/e2e/full inputs.
// Run: cargo test -p fl-engine --test planning_golden -- --ignored
#[test]
#[ignore = "slow (~6 min); run with --ignored when testdata/local/e2e/full exists"]
fn plan_matches_golden_on_the_full_scenario() {
    let Some(local) = testdata_path("local/e2e/full") else {
        return;
    };
    run_scenario("full", &local, &local, &local.join("plan.json.gz"));
}
