//! The signed update manifest (`installer-update.json`): strict parsing, canonical
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

/// Name of the release asset: a zip holding exactly [`APP_EXECUTABLE_ENTRY`].
pub const APP_INSTALLER_ASSET_NAME: &str = "FLDailyEditInstaller.zip";
/// The only entry the asset zip may contain. Installed 0.2.x builds also expect this
/// exact name.
pub const APP_EXECUTABLE_ENTRY: &str = "FLDailyEditInstaller.exe";
/// Exact URL of the installer zip.
pub const APP_INSTALLER_URL: &str =
    "https://github.com/gvoze32/fldailyedit/releases/download/latest/FLDailyEditInstaller.zip";
/// Exact URL of the manifest.
pub const APP_UPDATE_MANIFEST_URL: &str =
    "https://github.com/gvoze32/fldailyedit/releases/download/latest/installer-update.json";
/// Exact URL of the detached signature.
pub const APP_UPDATE_SIGNATURE_URL: &str =
    "https://github.com/gvoze32/fldailyedit/releases/download/latest/installer-update.json.sig";

/// Largest accepted installer zip (128 MiB).
pub const MAX_APP_ARCHIVE_BYTES: u64 = 128 * 1024 * 1024;
/// Largest accepted extracted executable (128 MiB).
pub const MAX_APP_EXECUTABLE_BYTES: u64 = 128 * 1024 * 1024;
/// Largest accepted manifest (64 KiB).
pub const MAX_MANIFEST_BYTES: u64 = 64 * 1024;
/// Largest accepted signature file (1 KiB).
pub const MAX_SIGNATURE_BYTES: u64 = 1024;

/// Raw Ed25519 public key (base64 `sYH8IUcmk160DJcjyu6TMRm4WEMtTYza4KL/kwKyYsI=`);
/// the release workflow signs `installer-update.json` with the private half.
pub const APP_UPDATE_PUBLIC_KEY: [u8; 32] = [
    0xb1, 0x81, 0xfc, 0x21, 0x47, 0x26, 0x93, 0x5e, 0xb4, 0x0c, 0x97, 0x23, 0xca, 0xee, 0x93, 0x31,
    0x19, 0xb8, 0x58, 0x43, 0x2d, 0x4d, 0x8c, 0xda, 0xe0, 0xa2, 0xff, 0x93, 0x02, 0xb2, 0x62, 0xc2,
];

const ED25519_SIGNATURE_BYTES: usize = 64;

const MANIFEST_KEYS: [&str; 6] = [
    "archive_sha256",
    "archive_size",
    "asset_name",
    "download_url",
    "schema_version",
    "version",
];

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

/// A validated update manifest. Every field satisfies the rules the shipped 0.2.x
/// updater enforces; construct it with [`Manifest::parse`] or [`Manifest::new`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Manifest {
    pub version: Version,
    pub asset_name: String,
    pub download_url: String,
    pub archive_size: u64,
    pub archive_sha256: String,
}

/// Field order is alphabetical on purpose: keys are written sorted.
#[derive(Serialize)]
struct Document<'a> {
    archive_sha256: &'a str,
    archive_size: u64,
    asset_name: &'a str,
    download_url: &'a str,
    schema_version: u8,
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
    /// Validate field values (asset name, URL, size bounds, digest format).
    pub fn new(
        version: Version,
        asset_name: String,
        download_url: String,
        archive_size: u64,
        archive_sha256: String,
    ) -> Result<Self> {
        if asset_name != APP_INSTALLER_ASSET_NAME {
            return Err(Error::UntrustedUrl(
                "installer asset name is not trusted".into(),
            ));
        }
        if download_url != APP_INSTALLER_URL {
            return Err(Error::UntrustedUrl("update URL is not trusted".into()));
        }
        if archive_size == 0 {
            return Err(Error::InvalidManifest(
                "archive_size must be positive".into(),
            ));
        }
        if archive_size > MAX_APP_ARCHIVE_BYTES {
            return Err(Error::TooLarge(format!(
                "installer archive exceeds {MAX_APP_ARCHIVE_BYTES} byte limit"
            )));
        }
        if !is_lowercase_sha256(&archive_sha256) {
            return Err(Error::InvalidManifest(
                "archive_sha256 must be a lowercase SHA-256 digest".into(),
            ));
        }
        Ok(Self {
            version,
            asset_name,
            download_url,
            archive_size,
            archive_sha256,
        })
    }

    /// Re-run the field validation on an already built value.
    pub fn validate(&self) -> Result<()> {
        Self::new(
            self.version,
            self.asset_name.clone(),
            self.download_url.clone(),
            self.archive_size,
            self.archive_sha256.clone(),
        )
        .map(drop)
    }

    /// Strictly parse manifest bytes: UTF-8 JSON object with exactly the six expected
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
        let asset_name = required_string(&entries, "asset_name")?;
        if asset_name != APP_INSTALLER_ASSET_NAME {
            return Err(Error::UntrustedUrl(
                "installer asset name is not trusted".into(),
            ));
        }
        let download_url = required_string(&entries, "download_url")?;
        if download_url != APP_INSTALLER_URL {
            return Err(Error::UntrustedUrl("update URL is not trusted".into()));
        }
        let archive_size = match value_of("archive_size") {
            Some(Value::Number(number)) if number.is_u64() => number.as_u64().unwrap_or_default(),
            _ => {
                return Err(Error::InvalidManifest(
                    "archive_size must be positive".into(),
                ));
            }
        };
        let archive_sha256 = required_string(&entries, "archive_sha256")?;
        Self::new(
            version,
            asset_name,
            download_url,
            archive_size,
            archive_sha256,
        )
    }

    /// Canonical bytes, byte-compatible with 0.2.x release tooling: keys sorted, two-space
    /// indent, non-ASCII unescaped, trailing newline. These are the bytes that get signed.
    pub fn to_bytes(&self) -> Vec<u8> {
        let document = Document {
            archive_sha256: &self.archive_sha256,
            archive_size: self.archive_size,
            asset_name: &self.asset_name,
            download_url: &self.download_url,
            schema_version: 1,
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
