//! Blocking HTTP transport abstraction plus the reqwest/rustls implementation.

use std::io::Read;
use std::time::Duration;

use reqwest::Url;
use reqwest::blocking::Client;
use reqwest::redirect::Policy;

use crate::error::{Error, Result};

/// Timeout for the manifest and signature requests (connect and each body read).
pub const MANIFEST_TIMEOUT: Duration = Duration::from_secs(15);
/// Timeout for the executable download (connect and each body read).
pub const DOWNLOAD_TIMEOUT: Duration = Duration::from_secs(60);

const MAX_REDIRECTS: usize = 10;

/// An open HTTP response with a successful status.
pub struct Response {
    /// The declared `Content-Length`, if the server sent one.
    pub content_length: Option<u64>,
    /// The (possibly lying) body stream; callers bound what they read from it.
    pub body: Box<dyn Read>,
}

/// Minimal transport used by the update flow. Implementations must map a non-success
/// HTTP status to [`Error::Http`], timeouts to [`Error::Timeout`] and enforce the
/// GitHub redirect allow-list; tests substitute an in-memory implementation.
pub trait Fetch {
    /// `GET url`; `timeout` bounds the connection and every individual body read.
    fn get(&self, url: &str, timeout: Duration) -> Result<Response>;
}

fn is_trusted_redirect_host(host: &str) -> bool {
    host == "github.com"
        || host == "githubusercontent.com"
        || host.ends_with(".githubusercontent.com")
}

fn trusted(url: &Url, initial: bool) -> bool {
    let Some(host) = url.host_str() else {
        return false;
    };
    let trusted_host = if initial {
        host == "github.com"
    } else {
        is_trusted_redirect_host(host)
    };
    url.scheme() == "https"
        && trusted_host
        && url.port().is_none()
        && url.username().is_empty()
        && url.password().is_none()
}

/// The update URL allow-list: `https` on the default port without credentials, host
/// `github.com` for the initial request (`initial = true`) or, for redirect hops,
/// `github.com`, `githubusercontent.com` or any `*.githubusercontent.com`.
pub fn is_trusted_url(url: &str, initial: bool) -> bool {
    Url::parse(url).is_ok_and(|parsed| trusted(&parsed, initial))
}

fn redirect_policy() -> Policy {
    Policy::custom(|attempt| {
        if attempt.previous().len() >= MAX_REDIRECTS {
            return attempt.error("too many redirects");
        }
        let from_trusted = attempt
            .previous()
            .last()
            .is_some_and(|previous| trusted(previous, false));
        if from_trusted && trusted(attempt.url(), false) {
            attempt.follow()
        } else {
            attempt.error("redirect target is not trusted")
        }
    })
}

fn map_reqwest_error(error: reqwest::Error) -> Error {
    if error.is_timeout() {
        Error::Timeout
    } else if error.is_redirect() {
        Error::UntrustedUrl(format!("update redirect rejected: {error}"))
    } else {
        Error::Network(error.without_url().to_string())
    }
}

/// The production transport: reqwest (blocking, rustls) restricted to the trusted
/// GitHub hosts. Stateless; a client is built per request.
#[derive(Debug, Clone, Copy, Default)]
pub struct HttpFetch;

impl Fetch for HttpFetch {
    fn get(&self, url: &str, timeout: Duration) -> Result<Response> {
        let parsed =
            Url::parse(url).map_err(|_| Error::UntrustedUrl("update URL is malformed".into()))?;
        if !trusted(&parsed, true) {
            return Err(Error::UntrustedUrl("update URL is not trusted".into()));
        }
        let client = Client::builder()
            .redirect(redirect_policy())
            .timeout(timeout)
            .connect_timeout(timeout)
            .user_agent(concat!("fldailyedit-updater/", env!("CARGO_PKG_VERSION")))
            .build()
            .map_err(map_reqwest_error)?;
        let response = client.get(parsed).send().map_err(map_reqwest_error)?;
        let status = response.status();
        if !status.is_success() {
            return Err(Error::Http(status.as_u16()));
        }
        Ok(Response {
            content_length: response.content_length(),
            body: Box::new(response),
        })
    }
}

/// Fetch a small document, enforcing `limit` by `Content-Length` and while reading.
pub(crate) fn fetch_document(
    fetch: &dyn Fetch,
    url: &str,
    timeout: Duration,
    limit: u64,
) -> Result<Vec<u8>> {
    let response = fetch.get(url, timeout)?;
    let too_large = || Error::TooLarge("update response is too large".into());
    if response.content_length.is_some_and(|length| length > limit) {
        return Err(too_large());
    }
    let mut payload = Vec::new();
    response
        .body
        .take(limit + 1)
        .read_to_end(&mut payload)
        .map_err(Error::from_body_read)?;
    if payload.len() as u64 > limit {
        return Err(too_large());
    }
    Ok(payload)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn url(text: &str) -> Url {
        Url::parse(text).unwrap()
    }

    #[test]
    fn initial_requests_must_target_github_com_over_https() {
        assert!(is_trusted_url(
            "https://github.com/gvoze32/fldailyedit/releases/latest/download/update.json",
            true
        ));
        for rejected in [
            "http://github.com/x",
            "https://objects.githubusercontent.com/x",
            "https://github.com.evil.example/x",
            "https://evilgithub.com/x",
            "https://github.com:8443/x",
            "https://user@github.com/x",
            "https://user:pw@github.com/x",
            "ftp://github.com/x",
            "not a url",
        ] {
            assert!(!is_trusted_url(rejected, true), "{rejected}");
        }
    }

    #[test]
    fn redirects_may_reach_the_githubusercontent_family() {
        for accepted in [
            "https://github.com/a",
            "https://githubusercontent.com/a",
            "https://objects.githubusercontent.com/a",
            "https://release-assets.githubusercontent.com/a?token=1",
        ] {
            assert!(trusted(&url(accepted), false), "{accepted}");
        }
        for rejected in [
            "http://objects.githubusercontent.com/a",
            "https://evilgithubusercontent.com/a",
            "https://githubusercontent.com.evil.example/a",
            "https://example.com/a",
            "https://objects.githubusercontent.com:444/a",
            "https://user@objects.githubusercontent.com/a",
        ] {
            assert!(!trusted(&url(rejected), false), "{rejected}");
        }
    }

    struct Fixed(Option<u64>, Vec<u8>);

    impl Fetch for Fixed {
        fn get(&self, _url: &str, _timeout: Duration) -> Result<Response> {
            Ok(Response {
                content_length: self.0,
                body: Box::new(std::io::Cursor::new(self.1.clone())),
            })
        }
    }

    #[test]
    fn documents_are_bounded_by_header_and_by_actual_bytes() {
        let at_limit = Fixed(Some(4), b"abcd".to_vec());
        assert_eq!(
            fetch_document(&at_limit, "u", MANIFEST_TIMEOUT, 4).unwrap(),
            b"abcd"
        );

        let header_too_big = Fixed(Some(5), b"abcd".to_vec());
        let error = fetch_document(&header_too_big, "u", MANIFEST_TIMEOUT, 4).unwrap_err();
        assert_eq!(error.kind(), crate::ErrorKind::TooLarge);

        let header_lies = Fixed(Some(1), b"abcde".to_vec());
        let error = fetch_document(&header_lies, "u", MANIFEST_TIMEOUT, 4).unwrap_err();
        assert_eq!(error.kind(), crate::ErrorKind::TooLarge);

        let no_header = Fixed(None, vec![0; 5]);
        let error = fetch_document(&no_header, "u", MANIFEST_TIMEOUT, 4).unwrap_err();
        assert_eq!(error.kind(), crate::ErrorKind::TooLarge);
    }
}
