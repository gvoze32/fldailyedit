use std::io;

/// Stable, coarse classification of an update failure, meant for choosing UI copy.
///
/// The set of variants and their [`code`](ErrorKind::code) strings are part of the
/// public contract; the human-readable [`Error`] messages are not.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum ErrorKind {
    /// Connection, DNS, TLS or other transport failure.
    Network,
    /// A request or a body read timed out.
    Timeout,
    /// The server answered with a non-success HTTP status.
    Http,
    /// `update.json.sig` does not exist (HTTP 404).
    MissingSignature,
    /// The signature is malformed, oversized, or does not verify.
    InvalidSignature,
    /// The manifest is not valid or violates the strict schema.
    InvalidManifest,
    /// A URL or redirect target is outside the allow-list.
    UntrustedUrl,
    /// A document or executable exceeds its size limit.
    TooLarge,
    /// The downloaded byte count differs from the declared size.
    SizeMismatch,
    /// The SHA-256 of the executable differs from the signed manifest.
    ChecksumMismatch,
    /// A local filesystem or process operation failed.
    Io,
    /// The manifest schema or the operation is not supported here.
    Unsupported,
    /// The caller requested cancellation.
    Cancelled,
}

impl ErrorKind {
    /// Short stable identifier (`snake_case`) for logs and UI lookup tables.
    pub const fn code(self) -> &'static str {
        match self {
            Self::Network => "network",
            Self::Timeout => "timeout",
            Self::Http => "http",
            Self::MissingSignature => "missing_signature",
            Self::InvalidSignature => "invalid_signature",
            Self::InvalidManifest => "invalid_manifest",
            Self::UntrustedUrl => "untrusted_url",
            Self::TooLarge => "too_large",
            Self::SizeMismatch => "size_mismatch",
            Self::ChecksumMismatch => "checksum_mismatch",
            Self::Io => "io",
            Self::Unsupported => "unsupported",
            Self::Cancelled => "cancelled",
        }
    }
}

/// Every way the update flow can fail.
#[derive(Debug, thiserror::Error)]
pub enum Error {
    #[error("update request failed: {0}")]
    Network(String),
    #[error("update request timed out")]
    Timeout,
    #[error("update server answered HTTP {0}")]
    Http(u16),
    #[error("update manifest signature is missing")]
    MissingSignature,
    #[error("{0}")]
    InvalidSignature(String),
    #[error("{0}")]
    InvalidManifest(String),
    #[error("{0}")]
    UntrustedUrl(String),
    #[error("{0}")]
    TooLarge(String),
    #[error("{0}")]
    SizeMismatch(String),
    #[error("downloaded update checksum does not match the signed manifest")]
    ChecksumMismatch,
    #[error("{context}: {source}")]
    Io {
        context: String,
        #[source]
        source: io::Error,
    },
    #[error("{0}")]
    Unsupported(String),
    #[error("update cancelled")]
    Cancelled,
}

impl Error {
    /// The stable classification of this error.
    pub fn kind(&self) -> ErrorKind {
        match self {
            Self::Network(_) => ErrorKind::Network,
            Self::Timeout => ErrorKind::Timeout,
            Self::Http(_) => ErrorKind::Http,
            Self::MissingSignature => ErrorKind::MissingSignature,
            Self::InvalidSignature(_) => ErrorKind::InvalidSignature,
            Self::InvalidManifest(_) => ErrorKind::InvalidManifest,
            Self::UntrustedUrl(_) => ErrorKind::UntrustedUrl,
            Self::TooLarge(_) => ErrorKind::TooLarge,
            Self::SizeMismatch(_) => ErrorKind::SizeMismatch,
            Self::ChecksumMismatch => ErrorKind::ChecksumMismatch,
            Self::Io { .. } => ErrorKind::Io,
            Self::Unsupported(_) => ErrorKind::Unsupported,
            Self::Cancelled => ErrorKind::Cancelled,
        }
    }

    pub(crate) fn io(context: impl Into<String>, source: io::Error) -> Self {
        Self::Io {
            context: context.into(),
            source,
        }
    }

    /// Classify an I/O error raised while reading a network body.
    pub(crate) fn from_body_read(error: io::Error) -> Self {
        match error.kind() {
            io::ErrorKind::TimedOut | io::ErrorKind::WouldBlock => Self::Timeout,
            io::ErrorKind::UnexpectedEof => {
                Self::SizeMismatch("update response ended early".to_owned())
            }
            _ => Self::Network(error.to_string()),
        }
    }
}

/// Result alias used across the crate.
pub type Result<T, E = Error> = std::result::Result<T, E>;
