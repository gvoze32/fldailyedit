//! Shared helpers of the supplemental-source integration tests.

use std::io::Read;
use std::path::PathBuf;

use chrono::NaiveDate;
use fl_sources::model::Transfer;
use serde_json::Value;

pub fn testdata() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../testdata/sources_other")
}

pub fn read(path: &str) -> String {
    std::fs::read_to_string(testdata().join(path))
        .unwrap_or_else(|error| panic!("read {path}: {error}"))
}

/// JSON fixture; `.gz` paths are decompressed first.
pub fn json(path: &str) -> Value {
    let text = if path.ends_with(".gz") {
        let file = std::fs::File::open(testdata().join(path))
            .unwrap_or_else(|error| panic!("open {path}: {error}"));
        let mut text = String::new();
        flate2::read::GzDecoder::new(file)
            .read_to_string(&mut text)
            .unwrap_or_else(|error| panic!("gunzip {path}: {error}"));
        text
    } else {
        read(path)
    };
    serde_json::from_str(&text).unwrap_or_else(|error| panic!("parse {path}: {error}"))
}

pub fn transfers(value: &Value) -> Vec<Transfer> {
    serde_json::from_value(value.clone()).expect("expected transfers deserialize")
}

pub fn date(value: &Value) -> Option<NaiveDate> {
    value.as_str().map(ymd)
}

pub fn ymd(text: &str) -> NaiveDate {
    NaiveDate::parse_from_str(text, "%Y-%m-%d").expect("fixture date")
}

/// Field-by-field comparison with a readable first difference.
pub fn assert_same(label: &str, actual: &[Transfer], expected: &[Transfer]) {
    for (index, (left, right)) in actual.iter().zip(expected).enumerate() {
        assert_eq!(left, right, "{label}: transfer #{index} differs");
    }
    assert_eq!(actual.len(), expected.len(), "{label}: transfer count");
}
