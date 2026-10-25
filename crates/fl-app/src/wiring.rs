//! The one place that decides which [`Engine`] the shipped binary uses.

use std::sync::Arc;

use fl_engine::api::{
    CancelToken, Environment, Progress, UpdateError, UpdateRequest, UpdateResult,
};

use crate::engine::Engine;

/// The engine the app ships with.
pub fn engine() -> Arc<dyn Engine> {
    Arc::new(LinkedEngine)
}

struct LinkedEngine;

impl Engine for LinkedEngine {
    fn run(
        &self,
        request: &UpdateRequest,
        env: &Environment,
        progress: &mut dyn FnMut(Progress),
        cancel: &CancelToken,
    ) -> Result<UpdateResult, UpdateError> {
        fl_engine::run_update(request, env, progress, cancel)
    }

    fn recent_log(&self, env: &Environment, count: usize) -> anyhow::Result<String> {
        let path = fl_engine::paths::StatePaths::new(&env.state_dir).transfer_log();
        Ok(fl_engine::log::TransferLog::new(path)
            .summary_lines(count)
            .join("\n"))
    }
}
