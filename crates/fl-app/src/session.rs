//! One engine run on a worker thread, with progress streamed back to the UI.

use std::panic::{AssertUnwindSafe, catch_unwind};
use std::sync::Arc;
use std::sync::mpsc::{Receiver, channel};
use std::thread::JoinHandle;

use fl_engine::api::{
    CancelToken, Environment, ErrorCode, Progress, UpdateError, UpdateRequest, UpdateResult,
};

use crate::engine::Engine;

pub enum RunEvent {
    Progress(Progress),
    Finished(Result<UpdateResult, UpdateError>),
}

pub struct RunSession {
    events: Receiver<RunEvent>,
    cancel: Arc<CancelToken>,
    _worker: JoinHandle<()>,
}

impl RunSession {
    /// Start `request` on its own thread. `wake` is called after every event
    /// so an idle UI repaints.
    pub fn spawn(
        engine: Arc<dyn Engine>,
        request: UpdateRequest,
        env: Environment,
        wake: impl Fn() + Send + 'static,
    ) -> Self {
        let (sender, events) = channel();
        let cancel = Arc::new(CancelToken::new());
        let token = Arc::clone(&cancel);
        let worker = std::thread::Builder::new()
            .name("fl-update".into())
            .spawn(move || {
                let outcome = catch_unwind(AssertUnwindSafe(|| {
                    engine.run(
                        &request,
                        &env,
                        &mut |progress| {
                            // The receiver only disappears when the app is closing.
                            let _ = sender.send(RunEvent::Progress(progress));
                            wake();
                        },
                        &token,
                    )
                }))
                .unwrap_or_else(|panic| {
                    let detail = panic
                        .downcast_ref::<&str>()
                        .map(|text| (*text).to_owned())
                        .or_else(|| panic.downcast_ref::<String>().cloned())
                        .unwrap_or_else(|| "unknown panic".to_owned());
                    Err(UpdateError {
                        code: ErrorCode::Internal,
                        stage: None,
                        message: format!("the update engine crashed: {detail}"),
                    })
                });
                let _ = sender.send(RunEvent::Finished(outcome));
                wake();
            })
            .expect("spawn update thread");
        Self {
            events,
            cancel,
            _worker: worker,
        }
    }

    /// Next pending event, if any. After `Finished` the channel is closed.
    pub fn poll(&self) -> Option<RunEvent> {
        self.events.try_recv().ok()
    }

    /// Ask the engine to stop. `false` once it has begun committing.
    pub fn request_cancel(&self) -> bool {
        self.cancel.request()
    }

    pub fn cancel_requested(&self) -> bool {
        self.cancel.is_requested()
    }

    /// The engine is writing the save; closing the app now is unsafe.
    pub fn commit_started(&self) -> bool {
        self.cancel.commit_started()
    }
}
