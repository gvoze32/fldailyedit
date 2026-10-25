//! Replay of the oracle sequences recorded from the former Python implementation (removed).
//!
//! Fixtures: `testdata/roster/base.json.gz` (committed, base save) and
//! `testdata/local/save_roster/<label>.json.gz` (reference saves, local only;
//! tests skip when the save or fixture is absent). Every sequence starts from
//! the pristine save; after each step the SHA-256 of `data.dat` and the
//! step's return value must equal the Python run.
//!
//! Two deliberate differences. Rust refuses to remove a club's last goalkeeper
//! (`last_goalkeeper`); a sequence stops at the first such step.
//!
//! One more: Python skipped the role-byte remap when a
//! removal could not compact the lineup (`stale_roles_early_return`); Rust
//! always remaps. From the first such step of a sequence on, only the hash
//! with the role bytes masked is compared and role-sensitive results are not.

use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};
use std::fs;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::sync::Arc;

use serde_json::{Value, json};
use sha2::{Digest, Sha256};

use crate::cpk::Cpk;
use crate::crypto;
use crate::edit_file::EditFile;
use crate::layout::*;
use crate::model::Position;
use crate::native::PlayerBinDatabase;
use crate::release_policy::{PlayerUsage, ReleasePolicy};

use super::tests::support::{assign_missing_game_plan_roles, team_matchday};
use super::{AddRequest, MoveRequest, OverflowQuery, RepairOptions};

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

fn hex(bytes: &[u8]) -> String {
    use std::fmt::Write;
    bytes
        .iter()
        .fold(String::with_capacity(bytes.len() * 2), |mut out, b| {
            let _ = write!(out, "{b:02x}");
            out
        })
}

fn from_hex(text: &str) -> Vec<u8> {
    (0..text.len() / 2)
        .map(|i| u8::from_str_radix(&text[2 * i..2 * i + 2], 16).expect("hex"))
        .collect()
}

fn sha(bytes: &[u8]) -> String {
    hex(&Sha256::digest(bytes))
}

/// SHA-256 with the role bytes of every game plan zeroed.
fn masked_sha(file: &EditFile) -> String {
    let mut data = file.bytes().to_vec();
    for offset in file.game_plan_entry_offsets().collect::<Vec<_>>() {
        for role in super::game_plan::all_role_offsets() {
            data[offset + role] = 0;
        }
    }
    sha(&data)
}

struct Save {
    data: Vec<u8>,
    pes21: bool,
}

fn load_save(label: &str) -> Option<Save> {
    let path = if label == "base" {
        root().join("base/EDIT00000000")
    } else {
        root().join("reference").join(label)
    };
    let raw = fs::read(path).ok()?;
    let blocks = crypto::decrypt(&raw).expect("decrypt");
    Some(Save {
        pes21: blocks.save_header().is_pes21(),
        data: blocks.data,
    })
}

fn playerbin(
    source: &str,
    cache: &mut HashMap<String, Arc<PlayerBinDatabase>>,
) -> Option<Arc<PlayerBinDatabase>> {
    if let Some(database) = cache.get(source) {
        return Some(database.clone());
    }
    const MEMBER: &str = "common/etc/pesdb/Player.bin";
    let reference = root().join("reference");
    let cpk = |path: PathBuf| Cpk::open(&path).ok()?.read_file(MEMBER).ok();
    let raw = match source {
        "reference_player_bin" => fs::read(reference.join("Player.bin")).ok(),
        "data_extra" | "data_s2526" => cpk(reference.join(format!("{source}.cpk"))),
        other => cpk(reference.join("T99 Patch V10").join(format!("{other}.cpk"))),
    }?;
    let database = Arc::new(PlayerBinDatabase::from_bytes(&raw).expect("Player.bin"));
    cache.insert(source.to_owned(), database.clone());
    Some(database)
}

/// An id from the fixture; a negative id (never a real player) maps to a
/// value that no roster holds.
fn u32_of(value: &Value) -> u32 {
    let number = value
        .as_i64()
        .unwrap_or_else(|| panic!("not an id: {value}"));
    u32::try_from(number).unwrap_or(u32::MAX)
}

fn opt_u32(value: &Value) -> Option<u32> {
    (!value.is_null()).then(|| u32_of(value))
}

/// A requested shirt number; negative numbers are requests that can never be
/// granted but still replace the player's current number as the preference.
fn opt_shirt(value: &Value) -> Option<u32> {
    value.as_i64().map(|n| u32::try_from(n).unwrap_or(u32::MAX))
}

fn id_list(value: &Value) -> Vec<u32> {
    value
        .as_array()
        .expect("array")
        .iter()
        .map(u32_of)
        .collect()
}

fn opt_id_list(value: &Value) -> Option<Vec<u32>> {
    (!value.is_null()).then(|| id_list(value))
}

fn team_map(value: &Value) -> Option<HashMap<u32, Vec<u32>>> {
    (!value.is_null()).then(|| {
        value
            .as_object()
            .expect("object")
            .iter()
            .map(|(team, ids)| (team.parse().expect("team id"), id_list(ids)))
            .collect()
    })
}

fn ok_result(result: Result<(), super::RosterError>, with_error: bool) -> Value {
    match result {
        Ok(()) => json!({"ok": true, "error": null}),
        Err(error) => json!({"ok": false, "error": with_error.then(|| error.code())}),
    }
}

fn apply_policy(file: &mut EditFile, policy: &Value) {
    if policy.is_null() {
        return;
    }
    let mut protected: BTreeMap<u32, BTreeSet<u32>> = BTreeMap::new();
    for (team, ids) in policy["protected"].as_object().expect("protected") {
        protected.insert(
            team.parse().expect("team"),
            id_list(ids).into_iter().collect(),
        );
    }
    let mut usage = BTreeMap::new();
    for (player, counters) in policy["usage"].as_object().expect("usage") {
        let c: Vec<u64> = counters
            .as_array()
            .expect("usage")
            .iter()
            .map(|v| v.as_u64().expect("u64"))
            .collect();
        usage.insert(
            player.parse().expect("player"),
            PlayerUsage {
                minutes: c[0],
                starts: c[1],
                appearances: c[2],
                news_mentions: c[3],
            },
        );
    }
    file.attach_release_policy(Some(&ReleasePolicy {
        protected_players: protected,
        usage,
        ..ReleasePolicy::default()
    }));
}

fn describe_value(explanation: Option<super::OverflowExplanation>) -> Value {
    let Some(e) = explanation else {
        return Value::Null;
    };
    json!({
        "team_id": e.team_id,
        "slot": e.slot,
        "role": e.role,
        "role_group": e.role_group.name(),
        "player_id": e.player_id,
        "name": e.name,
        "is_reserved": e.is_reserved,
        "is_goalkeeper": e.is_goalkeeper,
        "usage": e.usage.map(|u| json!({
            "minutes": u.minutes,
            "starts": u.starts,
            "appearances": u.appearances,
            "news_mentions": u.news_mentions,
        })),
        "overall": e.overall,
        "selection_basis": e.selection_basis,
    })
}

fn tactics(settings: &Value) -> Option<Vec<(TacticalField, u8)>> {
    settings
        .as_object()?
        .iter()
        .map(|(name, value)| {
            let field = TacticalField::from_name(name)?;
            let number = u8::try_from(value.as_u64()?).ok()?;
            Some((field, number))
        })
        .collect()
}

/// Run one op; returns its JSON result.
fn apply(file: &mut EditFile, op: &Value) -> Value {
    let kind = op["op"].as_str().expect("op kind");
    match kind {
        "move" => {
            let mut request = MoveRequest::new(
                u32_of(&op["player"]),
                u32_of(&op["from"]),
                u32_of(&op["to"]),
            );
            request.preferred_shirt_number =
                opt_shirt(&op["shirt"]).or_else(|| opt_shirt(&op["preferred_shirt"]));
            request.position = op["position"].as_str().unwrap_or("");
            request.allow_overflow_release = op["allow_overflow"].as_bool().unwrap_or(true);
            request.planned_overflow_player_id = opt_u32(&op["planned_overflow"]);
            ok_result(file.move_player(&request).map(|_| ()), true)
        }
        "add" => {
            let protected: Option<HashSet<u32>> =
                opt_id_list(&op["protected"]).map(|ids| ids.into_iter().collect());
            let mut request = AddRequest::new(u32_of(&op["player"]), u32_of(&op["to"]));
            request.preferred_shirt_number =
                opt_shirt(&op["shirt"]).or_else(|| opt_shirt(&op["preferred_shirt"]));
            request.position = op["position"].as_str().unwrap_or("");
            request.allow_overflow_release = op["allow_overflow"].as_bool().unwrap_or(true);
            request.protected_player_ids = protected.as_ref();
            request.planned_overflow_player_id = opt_u32(&op["planned_overflow"]);
            ok_result(file.add_player(&request).map(|_| ()), true)
        }
        "release" => ok_result(
            file.release_player(u32_of(&op["player"]), u32_of(&op["team"])),
            true,
        ),
        "shirt_update" => {
            let updates: Vec<(u32, u32)> = op["updates"]
                .as_array()
                .expect("updates")
                .iter()
                .map(|pair| {
                    let number = pair[1].as_i64().expect("number");
                    // Negative numbers are invalid wherever they appear.
                    (u32_of(&pair[0]), u32::try_from(number).unwrap_or(u32::MAX))
                })
                .collect();
            ok_result(
                file.update_player_shirt_numbers(u32_of(&op["team"]), &updates),
                false,
            )
        }
        "set_captain" => ok_result(
            file.set_team_captain(u32_of(&op["team"]), u32_of(&op["player"])),
            false,
        ),
        "set_formation" => {
            let team = u32_of(&op["team"]);
            match op["formation"].as_str() {
                Some(text) => match file.set_team_formation(team, text) {
                    Ok(changed) => json!({"changed": changed, "error": null}),
                    Err(error) => json!({"changed": 0, "error": error.code()}),
                },
                // A non-string formation is unsupported (Python: normalize returns None).
                None => json!({"changed": 0, "error": "unsupported_formation"}),
            }
        }
        "set_tactics" => {
            let team = u32_of(&op["team"]);
            let changed = tactics(&op["settings"])
                .and_then(|settings| file.set_team_tactical_settings(team, &settings).ok())
                .unwrap_or(0);
            json!({"changed": changed})
        }
        "preferred_starters" => {
            file.set_game_plan_preferred_starters(team_map(&op["starters"]).unwrap_or_default());
            Value::Null
        }
        "repair" => {
            let overrides = (!op["position_overrides"].is_null()).then(|| {
                op["position_overrides"]
                    .as_object()
                    .expect("overrides")
                    .iter()
                    .map(|(team, labels)| {
                        let map: HashMap<u32, Position> = labels
                            .as_object()
                            .expect("labels")
                            .iter()
                            .map(|(player, label)| {
                                let position = Position::from_label(label.as_str().expect("label"))
                                    .expect("oracle overrides name known positions");
                                (player.parse().expect("player"), position)
                            })
                            .collect();
                        (team.parse::<u32>().expect("team"), map)
                    })
                    .collect::<HashMap<_, _>>()
            });
            let options = RepairOptions {
                preserve_existing_primary: op["preserve_existing_primary"]
                    .as_bool()
                    .unwrap_or(false),
                preferred_starters: team_map(&op["preferred_starters"]),
                position_overrides: overrides.unwrap_or_default(),
                align_positions: op["align_positions"].as_bool().unwrap_or(false),
                preferred_bench: team_map(&op["preferred_bench"]).unwrap_or_default(),
            };
            let metrics = file.repair_game_plans(&options);
            Value::Object(
                metrics
                    .entries()
                    .iter()
                    .map(|&(k, v)| (k.to_owned(), json!(v)))
                    .collect(),
            )
        }
        "assign_missing" => {
            let roles = assign_missing_game_plan_roles(file, u32_of(&op["team"]));
            json!(roles.iter().map(|role| role.name()).collect::<Vec<_>>())
        }
        "poke" => {
            let bytes = from_hex(op["hex"].as_str().expect("hex"));
            let offset = op["offset"].as_u64().expect("offset") as usize;
            file.data_mut()[offset..offset + bytes.len()].copy_from_slice(&bytes);
            Value::Null
        }
        "find_overflow" | "describe_overflow" => {
            let roster = opt_id_list(&op["roster"]);
            let protected: Option<HashSet<u32>> =
                opt_id_list(&op["protected"]).map(|ids| ids.into_iter().collect());
            let team = u32_of(&op["team"]);
            let query = OverflowQuery {
                exclude_player_id: opt_u32(&op["exclude"]),
                roster_player_ids: roster.as_deref(),
                protected_player_ids: protected.as_ref(),
            };
            if kind == "find_overflow" {
                let found = file.find_overflow_release_candidate(team, query);
                json!(found.map_or([39, 0], |c| [c.slot as u64, u64::from(c.player_id)]))
            } else {
                describe_value(file.describe_overflow_release_candidate(
                    team,
                    u32_of(&op["player"]),
                    query,
                ))
            }
        }
        "matchday" => match team_matchday(file, u32_of(&op["team"])) {
            Some(day) => json!([day.starters, day.bench]),
            None => Value::Null,
        },
        "captain" => json!(file.team_captain_player(u32_of(&op["team"]))),
        "shirt_number" => {
            json!(file.player_shirt_number(u32_of(&op["team"]), u32_of(&op["player"])))
        }
        "find_teams" => json!(file.find_player_teams(
            u32_of(&op["player"]),
            op["club_only"].as_bool().unwrap_or(true)
        )),
        other => panic!("unknown op {other}"),
    }
}

/// Whether the overflow search of `op` hits Python's fallback: every
/// candidate is the incoming player, explicitly protected, or signed in this
/// session, and at least one is explicitly protected. Python then picked from
/// everybody (releasing a protected player); Rust only relaxes the session
/// signings. Where this holds, a mismatch is the documented deviation.
fn overflow_fallback_applies(file: &EditFile, op: &Value) -> bool {
    let kind = op["op"].as_str().unwrap_or("");
    let (team, exclude) = match kind {
        "find_overflow" => (&op["team"], opt_u32(&op["exclude"])),
        "describe_overflow" => (&op["team"], None),
        "move" | "add" if op["planned_overflow"].is_null() => (&op["to"], opt_u32(&op["player"])),
        _ => return false,
    };
    let team = u32_of(team);
    let ids: Vec<u32> = match opt_id_list(&op["roster"]) {
        Some(ids) => ids,
        None => file
            .roster(team)
            .map(|r| r.player_ids.to_vec())
            .unwrap_or_default(),
    };
    let protected: HashSet<u32> = opt_id_list(&op["protected"])
        .unwrap_or_default()
        .into_iter()
        .collect();
    let policy = file.release_protected(team);
    let explicit = |id: &u32| protected.contains(id) || policy.is_some_and(|set| set.contains(id));
    let others: Vec<u32> = ids
        .into_iter()
        .filter(|&id| id != 0 && Some(id) != exclude)
        .collect();
    others
        .iter()
        .all(|id| explicit(id) || file.transferred_player_ids().contains(id))
        && others.iter().any(explicit)
}

fn file_hash_differs(file: &EditFile, step: &Value, diverged: bool) -> bool {
    let (key, actual) = if diverged {
        ("sha256_masked", masked_sha(file))
    } else {
        ("sha256", sha(file.bytes()))
    };
    step.get(key).unwrap_or(&step["sha256"]) != &json!(actual)
}

fn is_mutation(kind: &str) -> bool {
    matches!(
        kind,
        "move" | "add" | "release" | "set_captain" | "shirt_update"
    )
}

/// Whether an op names player id 0.
fn uses_player_zero(op: &Value) -> bool {
    op["player"] == json!(0)
        || op["updates"]
            .as_array()
            .is_some_and(|updates| updates.iter().any(|pair| pair[0] == json!(0)))
}

/// Results that depend on role bytes, which diverge after a stale-roles step.
fn role_sensitive(kind: &str) -> bool {
    matches!(kind, "repair" | "assign_missing" | "captain")
}

fn open(
    save: &Save,
    context: &Value,
    databases: &mut HashMap<String, Arc<PlayerBinDatabase>>,
) -> Option<EditFile> {
    let mut file = EditFile::from_bytes(save.data.clone()).expect("save");
    file.set_pes21(save.pes21);
    if let Some(source) = context["native"].as_str() {
        let database = playerbin(source, databases)?;
        file.attach_native(Some(database));
    }
    if context["catalog"].as_bool().unwrap_or(false) {
        file.load_player_catalog();
    }
    apply_policy(&mut file, &context["policy"]);
    Some(file)
}

/// `value` with every string `name` field blanked. The Python run named
/// catalog players from the bundled FL catalog (removed); without Player.bin
/// the engine only knows the names edited inside the save.
fn without_names(value: &Value) -> Value {
    match value {
        Value::Object(map) => Value::Object(
            map.iter()
                .map(|(key, inner)| {
                    let inner = if key == "name" && inner.is_string() {
                        json!("")
                    } else {
                        without_names(inner)
                    };
                    (key.clone(), inner)
                })
                .collect(),
        ),
        Value::Array(items) => Value::Array(items.iter().map(without_names).collect()),
        other => other.clone(),
    }
}

fn replay_sequence(
    save: &Save,
    label: &str,
    sequence: &Value,
    databases: &mut HashMap<String, Arc<PlayerBinDatabase>>,
) -> usize {
    let name = sequence["name"].as_str().expect("name");
    let mask_names = sequence["context"]["catalog"].as_bool().unwrap_or(false);
    let Some(mut file) = open(save, &sequence["context"], databases) else {
        eprintln!("skip {label}/{name}: Player.bin source unavailable");
        return 0;
    };
    let mut diverged = false;
    let mut compared = 0;
    // Bytes of the last verified state: a step Python says changed nothing
    // only needs a memcmp, not a hash.
    let mut shadow = save.data.clone();
    for (index, step) in sequence["steps"]
        .as_array()
        .expect("steps")
        .iter()
        .enumerate()
    {
        let op = &step["op"];
        let kind = op["op"].as_str().expect("kind");
        let flags: Vec<&str> = step["flags"]
            .as_array()
            .map_or(vec![], |f| f.iter().filter_map(Value::as_str).collect());
        if flags.contains(&"raised") {
            break;
        }
        let fallback = overflow_fallback_applies(&file, op);
        let result = apply(&mut file, op);
        if result["error"] == json!("last_goalkeeper") && step["result"]["ok"] == json!(true) {
            // Rust refuses to remove a club's last goalkeeper (see
            // `testdata/DEVIATIONS.md`); the Python sequence went on to drain
            // the club, so the two part ways here.
            eprintln!("{label}/{name}: stopped at step {index} (last-goalkeeper refusal)");
            break;
        }
        if flags.contains(&"stale_roles_early_return") {
            if !diverged {
                eprintln!("{label}/{name}: masked role-byte comparison from step {index}");
            }
            diverged = true;
        }
        let context = format!("{label}/{name} step {index} {op}");
        let zero_player = uses_player_zero(op);
        if zero_player && is_mutation(kind) {
            // Player id 0 is an empty slot, not a player: Rust refuses it (and
            // asserts the refusal wrote nothing); Python's result is
            // meaningless, and if it changed the save the sequences part ways.
            assert_eq!(
                result["ok"],
                json!(false),
                "rust must refuse player 0: {context}"
            );
            if step["changed"].as_array().is_some_and(|c| !c.is_empty()) {
                eprintln!("{label}/{name}: stopped at step {index} (player id 0)");
                break;
            }
        }
        let check_result = !(diverged && role_sensitive(kind)) && !zero_player;
        let (result, expected_result) = if mask_names {
            (without_names(&result), without_names(&step["result"]))
        } else {
            (result, step["result"].clone())
        };
        if fallback && (result != expected_result || file_hash_differs(&file, step, diverged)) {
            if is_mutation(kind) {
                eprintln!(
                    "{label}/{name}: stopped at step {index} (documented overflow-fallback deviation)"
                );
                break;
            }
            continue;
        }
        if check_result {
            assert_eq!(result, expected_result, "result of {context}");
        }
        if !diverged && step["changed"].as_array().is_some_and(Vec::is_empty) {
            assert!(file.bytes() == &shadow[..], "data.dat changed in {context}");
        } else {
            let expected = if diverged {
                step.get("sha256_masked").unwrap_or(&step["sha256"])
            } else {
                &step["sha256"]
            };
            let actual = if diverged {
                masked_sha(&file)
            } else {
                sha(file.bytes())
            };
            assert_eq!(&json!(actual), expected, "data.dat hash after {context}");
            if !diverged {
                shadow.copy_from_slice(file.bytes());
            }
        }
        compared += 1;
    }
    compared
}

fn run(label: &str, fixture: &Path) {
    let Some(fixture) = read_gz_json(fixture) else {
        eprintln!("skip {label}: no fixture");
        return;
    };
    let Some(save) = load_save(label) else {
        eprintln!("skip {label}: save not present");
        return;
    };
    assert_eq!(
        fixture["input_sha256"],
        json!(sha(&save.data)),
        "{label}: input save differs from the fixture's"
    );
    let mut databases = HashMap::new();
    let mut steps = 0;
    let sequences = fixture["sequences"].as_array().expect("sequences");
    for sequence in sequences {
        let count = replay_sequence(&save, label, sequence, &mut databases);
        let total = sequence["steps"].as_array().map_or(0, Vec::len);
        if count < total {
            eprintln!(
                "{label}/{}: compared {count} of {total} steps",
                sequence["name"].as_str().unwrap_or("")
            );
        }
        steps += count;
    }
    eprintln!(
        "{label}: replayed {} sequences, {steps} steps",
        sequences.len()
    );
}

#[test]
fn base_sequences_match_python() {
    run("base", &root().join("testdata/roster/base.json.gz"));
}

fn run_reference(label: &str) {
    run(
        label,
        &root()
            .join("testdata/local/save_roster")
            .join(format!("{label}.json.gz")),
    );
}

macro_rules! reference_tests {
    ($($name:ident => $label:literal),* $(,)?) => {
        $(#[test] fn $name() { run_reference($label); })*
    };
}

reference_tests! {
    ramsdale_sequences_match_python => "EDIT00000000-ramsdale-test",
    salah_sequences_match_python => "EDIT00000000-salah-87to85",
    epltactic_sequences_match_python => "EDIT00000000epltactic",
    gondowan_sequences_match_python => "EDIT00000000gondowan",
    orifl_sequences_match_python => "EDIT00000000orifl",
    t99_sequences_match_python => "EDIT00000000t99",
}
