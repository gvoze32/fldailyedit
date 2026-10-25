//! Scripted HTTP transport for the supplemental-source fetch tests.

use std::sync::{Arc, Mutex};
use std::time::Duration;

use fl_sources::http::{BoxFuture, HttpClient, Request, Response, Transport, TransportError};

pub fn ok(body: impl Into<Vec<u8>>) -> Response {
    Response {
        status: 200,
        headers: Default::default(),
        body: body.into(),
    }
}

pub fn status(code: u16) -> Response {
    Response {
        status: code,
        headers: Default::default(),
        body: Vec::new(),
    }
}

type Handler = dyn Fn(&Request) -> Result<Response, TransportError> + Send + Sync;
type Delay = dyn Fn(&Request) -> Duration + Send + Sync;

struct Scripted {
    handler: Box<Handler>,
    delay: Box<Delay>,
    log: Arc<Mutex<Vec<Request>>>,
}

impl Transport for Scripted {
    fn execute<'a>(
        &'a self,
        request: &'a Request,
    ) -> BoxFuture<'a, Result<Response, TransportError>> {
        self.log.lock().expect("log lock").push(request.clone());
        let result = (self.handler)(request);
        let delay = (self.delay)(request);
        Box::pin(async move {
            if !delay.is_zero() {
                tokio::time::sleep(delay).await;
            }
            result
        })
    }
}

/// Every request a client made, in order.
#[derive(Clone)]
pub struct RequestLog(Arc<Mutex<Vec<Request>>>);

impl RequestLog {
    pub fn requests(&self) -> Vec<Request> {
        self.0.lock().expect("log lock").clone()
    }

    pub fn urls(&self) -> Vec<String> {
        self.requests()
            .into_iter()
            .map(|request| request.url)
            .collect()
    }
}

/// A client whose transport answers through `handler`.
pub fn scripted(
    handler: impl Fn(&Request) -> Result<Response, TransportError> + Send + Sync + 'static,
) -> (HttpClient, RequestLog) {
    scripted_with_delay(handler, |_| Duration::ZERO)
}

pub fn scripted_with_delay(
    handler: impl Fn(&Request) -> Result<Response, TransportError> + Send + Sync + 'static,
    delay: impl Fn(&Request) -> Duration + Send + Sync + 'static,
) -> (HttpClient, RequestLog) {
    let log = Arc::new(Mutex::new(Vec::new()));
    let transport = Scripted {
        handler: Box::new(handler),
        delay: Box::new(delay),
        log: Arc::clone(&log),
    };
    let client = HttpClient::builder()
        .transport(Arc::new(transport))
        .build()
        .expect("client");
    (client, RequestLog(log))
}

/// Header value of a request (case-insensitive name).
pub fn header<'a>(request: &'a Request, name: &str) -> Option<&'a str> {
    request
        .headers
        .iter()
        .rev()
        .find(|(key, _)| key.eq_ignore_ascii_case(name))
        .map(|(_, value)| value.as_str())
}
