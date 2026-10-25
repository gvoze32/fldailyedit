//! The signed update manifest (`update.json`): strict parsing, canonical
//! serialization, Ed25519 signature verification and version ordering.

use std::fmt;
use std::str::FromStr;

use base64::Engine as _;
use base64::alphabet::STANDARD as STANDARD_ALPHABET;
use base64::engine::{DecodePaddingMode, GeneralPurpose, GeneralPurposeConfig};
use ed25519_dalek::{Signature, Verifier, VerifyingKey};
use serde::de::{self, MapAccess, Visitor};
use serde::{Deserialize, Deserializer, Serialize};
use serde_json::Value;

use crate::error::{Error, Result};

/// Name of the release executable asset, and of the staged copy of it.
pub const APP_EXECUTABLE_NAME: &str = "FLDailyEdit.exe";
/// Manifest of the newest release (GitHub redirects `latest` to the newest `vX.Y.Z`).
pub const APP_UPDATE_MANIFEST_URL: &str =
    "https://github.com/gvoze32/fldailyedit/releases/latest/download/update.json";
/// Detached signature of the manifest of the newest release.
pub const APP_UPDATE_SIGNATURE_URL: &str =
    "https://github.com/gvoze32/fldailyedit/releases/latest/download/update.json.sig";

/// Largest accepted executable (128 MiB).
pub const MAX_APP_EXECUTABLE_BYTES: u64 = 128 * 1024 * 1024;
/// Largest accepted manifest (64 KiB).
pub const MAX_MANIFEST_BYTES: u64 = 64 * 1024;
/// Largest accepted signature file (1 KiB).
pub const MAX_SIGNATURE_BYTES: u64 = 1024;

/// Raw Ed25519 public key (base64 `sYH8IUcmk160DJcjyu6TMRm4WEMtTYza4KL/kwKyYsI=`);
/// the release workflow signs `update.json` with the private half.
pub const APP_UPDATE_PUBLIC_KEY: [u8; 32] = [
    0xb1, 0x81, 0xfc, 0x21, 0x47, 0x26, 0x93, 0x5e, 0xb4, 0x0c, 0x97, 0x23, 0xca, 0xee, 0x93, 0x31,
    0x19, 0xb8, 0x58, 0x43, 0x2d, 0x4d, 0x8c, 0xda, 0xe0, 0xa2, 0xff, 0x93, 0x02, 0xb2, 0x62, 0xc2,
];

const ED25519_SIGNATURE_BYTES: usize = 64;

const MANIFEST_KEYS: [&str; 5] = [
    "download_url",
    "schema_version",
    "sha256",
    "size",
    "version",
];

/// The one URL a manifest for `version` may point at: the executable attached to the
/// `v{version}` release, so the signed manifest and the executable always match.
pub fn release_download_url(version: &Version) -> String {
    format!(
        "https://github.com/gvoze32/fldailyedit/releases/download/v{version}/{APP_EXECUTABLE_NAME}"
    )
}

/// Standard-alphabet base64 with required padding; non-zero trailing bits are
/// tolerated.
const SIGNATURE_BASE64: GeneralPurpose = GeneralPurpose::new(
    &STANDARD_ALPHABET,
    GeneralPurposeConfig::new()
        .with_decode_padding_mode(DecodePaddingMode::RequireCanonical)
        .with_decode_allow_trailing_bits(true),
);

/// Numeric `MAJOR.MINOR.PATCH` version; ordering is numeric per component.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct Version {
    pub major: u64,
    pub minor: u64,
    pub patch: u64,
}

impl Version {
    /// `true` when `self` is strictly newer than `current`.
    pub fn is_newer(&self, current: &Version) -> bool {
        self > current
    }
}

impl FromStr for Version {
    type Err = Error;

    /// Accepts exactly three dot-separated canonical decimal numbers (no sign, no
    /// leading zeros, no pre-release/build suffix).
    fn from_str(text: &str) -> Result<Self> {
        let invalid = || {
            Error::InvalidManifest("version must use the numeric MAJOR.MINOR.PATCH format".into())
        };
        let mut parts = text.split('.');
        let mut next = || -> Result<u64> {
            let part = parts.next().ok_or_else(invalid)?;
            let canonical = !part.is_empty()
                && part.bytes().all(|byte| byte.is_ascii_digit())
                && (part == "0" || !part.starts_with('0'));
            if !canonical {
                return Err(invalid());
            }
            part.parse().map_err(|_| invalid())
        };
        let version = Version {
            major: next()?,
            minor: next()?,
            patch: next()?,
        };
        if parts.next().is_some() {
            return Err(invalid());
        }
        Ok(version)
    }
}

impl fmt::Display for Version {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(formatter, "{}.{}.{}", self.major, self.minor, self.patch)
    }
}

/// A validated update manifest; construct it with [`Manifest::parse`] or
/// [`Manifest::new`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Manifest {
    pub version: Version,
    /// Always [`release_download_url`] of `version`.
    pub download_url: String,
    /// Size of the executable in bytes.
    pub size: u64,
    /// Lowercase hex SHA-256 of the executable.
    pub sha256: String,
}

/// Field order is alphabetical on purpose: keys are written sorted.
#[derive(Serialize)]
struct Document<'a> {
    download_url: &'a str,
    schema_version: u8,
    sha256: &'a str,
    size: u64,
    version: String,
}

/// JSON object whose duplicate keys are rejected instead of silently merged.
struct StrictObject(Vec<(String, Value)>);

impl<'de> Deserialize<'de> for StrictObject {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        struct ObjectVisitor;

        impl<'de> Visitor<'de> for ObjectVisitor {
            type Value = StrictObject;

            fn expecting(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
                formatter.write_str("a JSON object")
            }

            fn visit_map<A: MapAccess<'de>>(self, mut map: A) -> Result<StrictObject, A::Error> {
                let mut entries: Vec<(String, Value)> = Vec::new();
                while let Some((key, value)) = map.next_entry::<String, Value>()? {
                    if entries.iter().any(|(existing, _)| *existing == key) {
                        return Err(de::Error::custom(format!("duplicate JSON key: {key}")));
                    }
                    entries.push((key, value));
                }
                Ok(StrictObject(entries))
            }
        }

        deserializer.deserialize_map(ObjectVisitor)
    }
}

fn required_string(entries: &[(String, Value)], field: &str) -> Result<String> {
    let value = entries
        .iter()
        .find_map(|(key, value)| (key == field).then_some(value));
    match value {
        Some(Value::String(text)) if !text.is_empty() => Ok(text.clone()),
        _ => Err(Error::InvalidManifest(format!(
            "{field} must be a non-empty string"
        ))),
    }
}

fn is_lowercase_sha256(text: &str) -> bool {
    text.len() == 64
        && text
            .bytes()
            .all(|byte| matches!(byte, b'0'..=b'9' | b'a'..=b'f'))
}

impl Manifest {
    /// Validate field values (URL pinned to the release of `version`, size bounds,
    /// digest format).
    pub fn new(version: Version, download_url: String, size: u64, sha256: String) -> Result<Self> {
        if download_url != release_download_url(&version) {
            return Err(Error::UntrustedUrl("update URL is not trusted".into()));
        }
        if size == 0 {
            return Err(Error::InvalidManifest("size must be positive".into()));
        }
        if size > MAX_APP_EXECUTABLE_BYTES {
            return Err(Error::TooLarge(format!(
                "update executable exceeds {MAX_APP_EXECUTABLE_BYTES} byte limit"
            )));
        }
        if !is_lowercase_sha256(&sha256) {
            return Err(Error::InvalidManifest(
                "sha256 must be a lowercase SHA-256 digest".into(),
            ));
        }
        Ok(Self {
            version,
            download_url,
            size,
            sha256,
        })
    }

    /// Re-run the field validation on an already built value.
    pub fn validate(&self) -> Result<()> {
        Self::new(
            self.version,
            self.download_url.clone(),
            self.size,
            self.sha256.clone(),
        )
        .map(drop)
    }

    /// Strictly parse manifest bytes: UTF-8 JSON object with exactly the five expected
    /// keys, no duplicates, `schema_version == 1`, and valid field values.
    pub fn parse(payload: &[u8]) -> Result<Self> {
        let text = std::str::from_utf8(payload).map_err(|_| {
            Error::InvalidManifest("update manifest is not valid UTF-8 JSON".into())
        })?;
        let StrictObject(entries) = serde_json::from_str(text).map_err(|error| {
            Error::InvalidManifest(format!("update manifest is not valid JSON: {error}"))
        })?;

        let mut keys: Vec<&str> = entries.iter().map(|(key, _)| key.as_str()).collect();
        keys.sort_unstable();
        if keys != MANIFEST_KEYS {
            return Err(Error::InvalidManifest(format!(
                "manifest must contain exactly {MANIFEST_KEYS:?}"
            )));
        }
        let value_of = |field: &str| {
            entries
                .iter()
                .find_map(|(key, value)| (key == field).then_some(value))
        };

        if value_of("schema_version").and_then(Value::as_u64) != Some(1) {
            return Err(Error::Unsupported(
                "manifest schema_version must be 1".into(),
            ));
        }
        let version: Version = required_string(&entries, "version")?.parse()?;
        let download_url = required_string(&entries, "download_url")?;
        let size = match value_of("size") {
            Some(Value::Number(number)) if number.is_u64() => number.as_u64().unwrap_or_default(),
            _ => {
                return Err(Error::InvalidManifest("size must be positive".into()));
            }
        };
        let sha256 = required_string(&entries, "sha256")?;
        Self::new(version, download_url, size, sha256)
    }

    /// Canonical bytes: keys sorted, two-space indent, non-ASCII unescaped, trailing
    /// newline. These are the bytes that get signed.
    pub fn to_bytes(&self) -> Vec<u8> {
        let document = Document {
            download_url: &self.download_url,
            schema_version: 1,
            sha256: &self.sha256,
            size: self.size,
            version: self.version.to_string(),
        };
        let mut bytes = serde_json::to_vec_pretty(&document)
            .expect("a struct of strings and integers always serializes");
        bytes.push(b'\n');
        bytes
    }
}

/// Verify the detached base64 Ed25519 `signature` over the raw manifest `payload`
/// with the embedded production key.
pub fn verify_signature(payload: &[u8], signature: &[u8]) -> Result<()> {
    verify_with_key(&APP_UPDATE_PUBLIC_KEY, payload, signature)
}

/// Like [`verify_signature`] with an explicit raw public key (used by tests and the
/// release tool).
pub fn verify_with_key(public_key: &[u8; 32], payload: &[u8], signature: &[u8]) -> Result<()> {
    let malformed = || Error::InvalidSignature("update manifest signature is malformed".into());
    let raw = SIGNATURE_BASE64
        .decode(signature.trim_ascii())
        .map_err(|_| malformed())?;
    let raw: [u8; ED25519_SIGNATURE_BYTES] = raw.try_into().map_err(|_| malformed())?;
    let key = VerifyingKey::from_bytes(public_key)
        .map_err(|_| Error::InvalidSignature("update public key is invalid".into()))?;
    key.verify(payload, &Signature::from_bytes(&raw))
        .map_err(|_| Error::InvalidSignature("update manifest signature is invalid".into()))
}
