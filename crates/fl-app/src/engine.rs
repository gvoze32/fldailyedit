//! The seam between the front ends and the update engine.
//!
//! The GUI and the CLI only know [`Engine`]; [`crate::wiring`] decides which
//! implementation the shipped binary uses. Tests and demos drive the same
//! screens with [`crate::scripted::ScriptedEngine`].

use fl_engine::api::{
    CancelToken, Environment, Progress, UpdateError, UpdateRequest, UpdateResult,
};

pub trait Engine: Send + Sync {
    /// Run one update. Must report progress through `progress`, honor
    /// `cancel` until it begins the commit (see [`CancelToken::begin_commit`])
    /// and return `ErrorCode::Cancelled` when it stops because of it.
    fn run(
        &self,
        request: &UpdateRequest,
        env: &Environment,
        progress: &mut dyn FnMut(Progress),
        cancel: &CancelToken,
    ) -> Result<UpdateResult, UpdateError>;

    /// The last `count` entries of the transfer log, formatted for a terminal.
    fn recent_log(&self, env: &Environment, count: usize) -> anyhow::Result<String>;
}
