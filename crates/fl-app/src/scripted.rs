//! A scripted [`Engine`] for tests and UI demos: replays a list of steps and
//! then returns a canned result, honoring cancellation exactly like the real
//! engine (`begin_commit` freezes the token).

use std::sync::{Arc, Condvar, Mutex};
use std::time::Duration;

use fl_engine::api::{
    CancelToken, Environment, ErrorCode, Progress, Stage, UpdateError, UpdateRequest, UpdateResult,
};

use crate::engine::Engine;

/// A latch a test opens to let a paused script continue.
#[derive(Clone, Default)]
pub struct Gate(Arc<(Mutex<bool>, Condvar)>);

impl Gate {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn open(&self) {
        *self.0.0.lock().unwrap() = true;
        self.0.1.notify_all();
    }

    fn is_open(&self) -> bool {
        *self.0.0.lock().unwrap()
    }
}

#[derive(Clone)]
pub enum Step {
    /// Emit a progress event.
    Progress {
        stage: Stage,
        message: String,
        current: u32,
        total: u32,
    },
    /// Sleep (demo pacing).
    Delay(Duration),
    /// Block until the gate opens; a cancel request ends the wait with an error.
    Pause(Stage, Gate),
    /// Fail with `Cancelled` if a cancel was requested.
    Checkpoint(Stage),
    /// Freeze the cancel token like the real engine before it writes.
    BeginCommit,
}

pub struct ScriptedEngine {
    steps: Vec<Step>,
    result: Result<UpdateResult, UpdateError>,
    log: String,
}

impl ScriptedEngine {
    pub fn new(steps: Vec<Step>, result: Result<UpdateResult, UpdateError>) -> Self {
        Self {
            steps,
            result,
            log: String::new(),
        }
    }

    pub fn with_log(mut self, log: impl Into<String>) -> Self {
        self.log = log.into();
        self
    }

    pub fn step(stage: Stage, message: &str, current: u32, total: u32) -> Step {
        Step::Progress {
            stage,
            message: message.to_owned(),
            current,
            total,
        }
    }
}

fn cancelled(stage: Stage) -> UpdateError {
    UpdateError {
        code: ErrorCode::Cancelled,
        stage: Some(stage),
        message: "Update cancelled".into(),
    }
}

impl Engine for ScriptedEngine {
    fn run(
        &self,
        _request: &UpdateRequest,
        _env: &Environment,
        progress: &mut dyn FnMut(Progress),
        cancel: &CancelToken,
    ) -> Result<UpdateResult, UpdateError> {
        for step in &self.steps {
            match step {
                Step::Progress {
                    stage,
                    message,
                    current,
                    total,
                } => progress(Progress {
                    stage: *stage,
                    message: message.clone(),
                    current: *current,
                    total: *total,
                }),
                Step::Delay(duration) => std::thread::sleep(*duration),
                Step::Pause(stage, gate) => {
                    while !gate.is_open() {
                        cancel.check(*stage)?;
                        std::thread::sleep(Duration::from_millis(5));
                    }
                }
                Step::Checkpoint(stage) => cancel.check(*stage)?,
                Step::BeginCommit => {
                    if !cancel.begin_commit() {
                        return Err(cancelled(Stage::Committing));
                    }
                }
            }
        }
        self.result.clone()
    }

    fn recent_log(&self, _env: &Environment, _count: usize) -> anyhow::Result<String> {
        Ok(self.log.clone())
    }
}
