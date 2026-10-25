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
use fl_sources::team_index::{TeamEntry, parse_catalog};
use flate2::read::GzDecoder;

pub fn repo_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("..").join("..")
}

/// `path` when it exists; otherwise prints the skip note of the running test
/// and returns `None`. `testdata/`, `reference/` and the base save are local
/// files (not in the repository): tests that need one skip when it is absent.
pub fn existing(path: PathBuf) -> Option<PathBuf> {
    if path.exists() {
        return Some(path);
    }
    let thread = std::thread::current();
    let test = thread.name().unwrap_or("test");
    eprintln!("skipping {test}: {} not present", path.display());
    None
}

/// `<repo>/testdata/<rel>` when it exists; otherwise prints a skip note.
pub fn testdata_path(rel: &str) -> Option<PathBuf> {
    existing(repo_root().join("testdata").join(rel))
}

/// The base save a recording ran on: `FL_TEST_BASE_SAVE` if set, else
/// `input` of its `meta.json` relative to the repo root. `None` (with a skip
/// note) when absent.
pub fn base_save_path(meta: &serde_json::Value) -> Option<PathBuf> {
    let path = match std::env::var_os("FL_TEST_BASE_SAVE") {
        Some(custom) => repo_root().join(custom),
        None => repo_root().join(meta["input"].as_str().unwrap()),
    };
    existing(path)
}

pub fn gunzip(path: &Path) -> Vec<u8> {
    let raw = fs::read(path).unwrap_or_else(|e| panic!("{}: {e}", path.display()));
    let mut out = Vec::new();
    GzDecoder::new(&raw[..]).read_to_end(&mut out).unwrap();
    out
}

/// The recorded FotMob team list (`testdata/fotmob_teams.json`): the replays'
/// cassettes carry no sitemap requests, so the list is injected.
pub fn fixture_team_index() -> Arc<[TeamEntry]> {
    let text = fs::read_to_string(repo_root().join("testdata/fotmob_teams.json")).unwrap();
    parse_catalog(&text).unwrap().into()
}

/// One recorded scenario (`testdata/e2e/<name>` or `testdata/local/e2e/<name>`).
pub struct Scenario {
    pub dir: PathBuf,
    pub meta: serde_json::Value,
    pub input: PathBuf,
    pub player_bin_gz: PathBuf,
    pub team_bin_gz: PathBuf,
}

impl Scenario {
    /// `None` (with a skip note) when the scenario, its recording, its
    /// Player.bin / Team.bin inputs, the base save or the team list are not on
    /// this machine.
    pub fn open(committed: bool, name: &str) -> Option<Self> {
        let root = repo_root().join("testdata");
        let dir = if committed {
            root.join("e2e").join(name)
        } else {
            root.join("local").join("e2e").join(name)
        };
        let inputs = root.join("local").join("e2e").join(name).join("inputs");
        let meta_path = existing(dir.join("meta.json"))?;
        existing(dir.join("cassette.jsonl.gz"))?;
        let player_bin_gz = existing(inputs.join("Player.bin.gz"))?;
        let team_bin_gz = existing(inputs.join("Team.bin.gz"))?;
        existing(root.join("fotmob_teams.json"))?;
        let meta: serde_json::Value =
            serde_json::from_slice(&fs::read(meta_path).unwrap()).unwrap();
        let input = base_save_path(&meta)?;
        Some(Self {
            dir,
            meta,
            input,
            player_bin_gz,
            team_bin_gz,
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
    pub team_bin: PathBuf,
}

impl Workdir {
    pub fn new(scenario: &Scenario) -> Self {
        let tmp = tempfile::tempdir().unwrap();
        let root = fs::canonicalize(tmp.path()).unwrap();
        let input = root.join("input").join("EDIT00000000");
        fs::create_dir_all(input.parent().unwrap()).unwrap();
        fs::copy(&scenario.input, &input).unwrap();
        let player_bin = root.join("inputs").join("Player.bin");
        fs::create_dir_all(player_bin.parent().unwrap()).unwrap();
        fs::write(&player_bin, gunzip(&scenario.player_bin_gz)).unwrap();
        let team_bin = root.join("inputs").join("Team.bin");
        fs::write(&team_bin, gunzip(&scenario.team_bin_gz)).unwrap();
        let state = root.join("state");
        fs::create_dir_all(&state).unwrap();
        Self {
            output: root.join("output").join("EDIT00000000"),
            input,
            state,
            player_bin,
            team_bin,
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
                team_bin: Some(self.team_bin.clone()),
                ..NativeFiles::default()
            },
            skip_sleeps: true,
            team_index: Some(fixture_team_index()),
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
