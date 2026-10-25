//! Pure-function checks against the recorded fixture `testdata/roster/pure.json.gz`.

use std::collections::HashSet;
use std::io::Read;
use std::path::Path;

use serde_json::Value;

use crate::model::Position;
use crate::roster::formation::normalize_game_plan_formation;
use crate::roster::hungarian::max_weight_assignment;
use crate::roster::shirt::assign_smart_shirt_number;

use super::support::game_plan_formation_layout;

fn fixture() -> Option<Value> {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../testdata/roster/pure.json.gz");
    let file = std::fs::File::open(path).ok()?;
    let mut text = String::new();
    flate2::read::GzDecoder::new(file)
        .read_to_string(&mut text)
        .ok()?;
    Some(serde_json::from_str(&text).expect("pure.json"))
}

#[test]
fn formation_normalization_and_layouts_match_fixture() {
    let Some(fixture) = fixture() else {
        eprintln!("skip: testdata/roster/pure.json.gz absent");
        return;
    };
    let rows = fixture["formations"].as_array().expect("formations");
    let mut checked = 0;
    for row in rows {
        // Non-string inputs cannot be expressed through the typed API.
        let Some(input) = row["input"].as_str() else {
            continue;
        };
        let normalized = normalize_game_plan_formation(input);
        assert_eq!(
            normalized.as_deref(),
            row["normalized"].as_str(),
            "normalize {input:?}"
        );
        let layout = game_plan_formation_layout(input);
        match (&row["layout"], layout) {
            (Value::Null, None) => {}
            (expected, Some(layout)) => {
                let roles: Vec<u64> = layout.roles.iter().map(|p| u64::from(p.code())).collect();
                let coordinates: Vec<[u64; 2]> = layout
                    .coordinates
                    .iter()
                    .map(|&(v, h)| [u64::from(v), u64::from(h)])
                    .collect();
                assert_eq!(
                    serde_json::json!(roles),
                    expected["roles"],
                    "roles {input:?}"
                );
                assert_eq!(
                    serde_json::json!(coordinates),
                    expected["coordinates"],
                    "coordinates {input:?}"
                );
            }
            (expected, None) => panic!("layout {input:?}: fixture {expected}, got none"),
        }
        checked += 1;
    }
    assert!(checked > 30, "only {checked} formation rows");
}

#[test]
fn smart_shirt_numbers_match_fixture() {
    let Some(fixture) = fixture() else {
        return;
    };
    let rows = fixture["shirt_numbers"].as_array().expect("shirt_numbers");
    for row in rows {
        let used: HashSet<u32> = row["used"]
            .as_array()
            .expect("used")
            .iter()
            .map(|n| n.as_u64().expect("used number") as u32)
            .collect();
        let preferred = row["preferred"]
            .as_i64()
            .and_then(|n| u32::try_from(n).ok());
        let position = row["position"].as_str().expect("position");
        let is_gk = row["is_gk"].as_bool().expect("is_gk");
        let number = assign_smart_shirt_number(&used, preferred, position, is_gk);
        assert_eq!(
            u64::from(number),
            row["result"].as_u64().expect("result"),
            "used={used:?} preferred={preferred:?} position={position:?} gk={is_gk}"
        );
    }
    assert!(rows.len() > 1000, "only {} shirt rows", rows.len());
}

#[test]
fn max_weight_assignment_matches_fixture() {
    let Some(fixture) = fixture() else {
        return;
    };
    let rows = fixture["assignment"].as_array().expect("assignment");
    for row in rows {
        let weights: Vec<Vec<Option<i64>>> = row["weights"]
            .as_array()
            .expect("weights")
            .iter()
            .map(|line| {
                line.as_array()
                    .expect("row")
                    .iter()
                    .map(Value::as_i64)
                    .collect()
            })
            .collect();
        let expected: Option<Vec<usize>> = row["result"].as_array().map(|columns| {
            columns
                .iter()
                .map(|c| c.as_u64().expect("column") as usize)
                .collect()
        });
        assert_eq!(
            max_weight_assignment(&weights),
            expected,
            "weights {weights:?}"
        );
    }
    assert!(rows.len() > 100, "only {} assignment rows", rows.len());
}

#[test]
fn position_labels_map_like_fixture() {
    let Some(fixture) = fixture() else {
        return;
    };
    let rows = fixture["position_codes"]
        .as_array()
        .expect("position_codes");
    for row in rows {
        let label = row["label"].as_str().expect("label");
        let expected = row["code"].as_u64().map(|c| c as u8);
        assert_eq!(
            Position::from_label(label).map(Position::code),
            expected,
            "label {label:?}"
        );
    }
}
