//! Shared harness for the replay (golden) end-to-end tests.

// Each test binary uses a different subset of this shared harness.
#![allow(dead_code)]

use std::fs;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::sync::Arc;

use chrono::{DateTime, Utc};
use fl_engine::api::{CancelToken, Environment, Progress, UpdateRequest, UpdateResult, Window};
use fl_engine::native::NativeFiles;
use fl_engine::{FixedClock, Services};
use fl_sources::http::ReplayTransport;
use flate2::read::GzDecoder;

pub fn repo_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("..").join("..")
}

pub fn gunzip(path: &Path) -> Vec<u8> {
    let raw = fs::read(path).unwrap_or_else(|e| panic!("{}: {e}", path.display()));
    let mut out = Vec::new();
    GzDecoder::new(&raw[..]).read_to_end(&mut out).unwrap();
    out
}

/// One recorded scenario (`testdata/e2e/<name>` or `testdata/local/e2e/<name>`).
pub struct Scenario {
    pub dir: PathBuf,
    pub meta: serde_json::Value,
    pub player_bin_gz: PathBuf,
}

impl Scenario {
    /// `None` when the scenario or its Player.bin input is not on this machine.
    pub fn open(committed: bool, name: &str) -> Option<Self> {
        let root = repo_root().join("testdata");
        let dir = if committed {
            root.join("e2e").join(name)
        } else {
            root.join("local").join("e2e").join(name)
        };
        let player_bin_gz = root
            .join("local")
            .join("e2e")
            .join(name)
            .join("inputs")
            .join("Player.bin.gz");
        if !dir.join("meta.json").is_file() || !player_bin_gz.is_file() {
            eprintln!("skipping scenario {name}: recorded inputs not present");
            return None;
        }
        let meta = serde_json::from_slice(&fs::read(dir.join("meta.json")).unwrap()).unwrap();
        Some(Self {
            dir,
            meta,
            player_bin_gz,
        })
    }

    pub fn now(&self) -> DateTime<Utc> {
        self.meta["now_utc"].as_str().unwrap().parse().unwrap()
    }

    pub fn club(&self) -> Option<String> {
        let args = self.meta["args"].as_array().unwrap();
        let position = args.iter().position(|a| a == "--club")?;
        Some(args[position + 1].as_str().unwrap().to_owned())
    }

    pub fn expected_text(&self, name: &str) -> String {
        let plain = self.dir.join("expected").join(name);
        let bytes = if plain.is_file() {
            fs::read(plain).unwrap()
        } else {
            gunzip(&self.dir.join("expected").join(format!("{name}.gz")))
        };
        String::from_utf8(bytes).unwrap()
    }

    pub fn expected_save_sha256(&self) -> String {
        self.meta["expected_sha256"]["EDIT00000000"]
            .as_str()
            .unwrap()
            .to_owned()
    }
}

/// A scenario materialized in a temp work directory.
pub struct Workdir {
    pub tmp: tempfile::TempDir,
    pub input: PathBuf,
    pub output: PathBuf,
    pub state: PathBuf,
    pub player_bin: PathBuf,
}

impl Workdir {
    pub fn new(scenario: &Scenario) -> Self {
        let tmp = tempfile::tempdir().unwrap();
        let root = fs::canonicalize(tmp.path()).unwrap();
        let input = root.join("input").join("EDIT00000000");
        fs::create_dir_all(input.parent().unwrap()).unwrap();
        fs::copy(
            repo_root().join(scenario.meta["input"].as_str().unwrap()),
            &input,
        )
        .unwrap();
        let player_bin = root.join("inputs").join("Player.bin");
        fs::create_dir_all(player_bin.parent().unwrap()).unwrap();
        fs::write(&player_bin, gunzip(&scenario.player_bin_gz)).unwrap();
        let state = root.join("state");
        fs::create_dir_all(&state).unwrap();
        Self {
            output: root.join("output").join("EDIT00000000"),
            input,
            state,
            player_bin,
            tmp,
        }
    }

    pub fn root(&self) -> PathBuf {
        fs::canonicalize(self.tmp.path()).unwrap()
    }

    pub fn request(&self, scenario: &Scenario) -> UpdateRequest {
        UpdateRequest {
            edit_path: self.input.clone(),
            output_path: Some(self.output.clone()),
            game_root: None,
            window: Window::Auto,
            club: scenario.club(),
            threshold: None,
            allow_overflow_release: true,
            release_policy: None,
            dry_run: false,
        }
    }

    pub fn environment(&self) -> Environment {
        Environment {
            state_dir: self.state.clone(),
        }
    }

    pub fn services(&self, scenario: &Scenario) -> Services {
        let transport =
            ReplayTransport::from_path(&scenario.dir.join("cassette.jsonl.gz")).unwrap();
        Services {
            clock: Arc::new(FixedClock(scenario.now())),
            transport: Some(Arc::new(transport)),
            native_files: NativeFiles {
                player_bin: Some(self.player_bin.clone()),
                ..NativeFiles::default()
            },
            skip_sleeps: true,
        }
    }

    /// Replace this work directory with the `<WORKDIR>` placeholder of the goldens.
    pub fn normalize(&self, text: &str) -> String {
        text.replace(self.root().to_str().unwrap(), "<WORKDIR>")
    }
}

pub fn run(
    workdir: &Workdir,
    scenario: &Scenario,
    request: &UpdateRequest,
    cancel: &CancelToken,
    mut on_progress: impl FnMut(&Progress),
) -> Result<UpdateResult, fl_engine::api::UpdateError> {
    fl_engine::run_update_with(
        request,
        &workdir.environment(),
        &workdir.services(scenario),
        &mut |p| on_progress(&p),
        cancel,
    )
}

/// Every file below `dir` (relative, sorted); empty for a missing directory.
pub fn tree(dir: &Path) -> Vec<String> {
    fn walk(base: &Path, dir: &Path, out: &mut Vec<String>) {
        let Ok(entries) = fs::read_dir(dir) else {
            return;
        };
        for entry in entries.flatten() {
            let path = entry.path();
            out.push(
                path.strip_prefix(base)
                    .unwrap()
                    .to_string_lossy()
                    .into_owned(),
            );
            if path.is_dir() {
                walk(base, &path, out);
            }
        }
    }
    let mut out = Vec::new();
    walk(dir, dir, &mut out);
    out.sort();
    out
}
