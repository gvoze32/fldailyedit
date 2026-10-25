//! Release-side tooling: build and sign `installer-update.json`.

use std::fs::{self, File};
use std::io::Read;
use std::path::Path;

use base64::Engine as _;
use base64::engine::general_purpose::STANDARD;
use ed25519_dalek::pkcs8::DecodePrivateKey;
use ed25519_dalek::{Signer, SigningKey};
use sha2::{Digest, Sha256};

use crate::archive::validate_installer_archive;
use crate::error::{Error, Result};
use crate::manifest::{
    APP_INSTALLER_ASSET_NAME, APP_INSTALLER_URL, APP_UPDATE_PUBLIC_KEY, Manifest, Version,
    verify_with_key,
};

/// Environment variable holding the PEM PKCS#8 Ed25519 private key.
pub const SIGNING_KEY_ENVIRONMENT: &str = "INSTALLER_SIGNING_KEY";

fn write_creating_parents(path: &Path, bytes: &[u8]) -> Result<()> {
    if let Some(parent) = path
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
    {
        fs::create_dir_all(parent)
            .map_err(|error| Error::io(format!("cannot create {}", parent.display()), error))?;
    }
    fs::write(path, bytes)
        .map_err(|error| Error::io(format!("cannot write {}", path.display()), error))
}

fn sha256_of_file(path: &Path) -> Result<String> {
    let mut file = File::open(path)
        .map_err(|error| Error::io(format!("cannot open {}", path.display()), error))?;
    let mut digest = Sha256::new();
    let mut buffer = vec![0_u8; 64 * 1024];
    loop {
        let read = file
            .read(&mut buffer)
            .map_err(|error| Error::io(format!("cannot read {}", path.display()), error))?;
        if read == 0 {
            return Ok(format!("{:x}", digest.finalize()));
        }
        digest.update(&buffer[..read]);
    }
}

/// Describe `asset` (which must be named `FLDailyEditInstaller.zip`, be non-empty and
/// contain exactly `FLDailyEditInstaller.exe`) as a manifest for `version`.
pub fn manifest_for_asset(asset: &Path, version: &str) -> Result<Manifest> {
    if asset
        .file_name()
        .is_none_or(|name| name != APP_INSTALLER_ASSET_NAME)
    {
        return Err(Error::InvalidManifest(format!(
            "installer asset must be named {APP_INSTALLER_ASSET_NAME}"
        )));
    }
    let size = fs::metadata(asset)
        .map_err(|error| Error::io(format!("cannot inspect {}", asset.display()), error))?
        .len();
    if size == 0 {
        return Err(Error::InvalidManifest(
            "installer asset must not be empty".into(),
        ));
    }
    validate_installer_archive(asset)?;
    Manifest::new(
        version.parse::<Version>()?,
        APP_INSTALLER_ASSET_NAME.to_owned(),
        APP_INSTALLER_URL.to_owned(),
        size,
        sha256_of_file(asset)?,
    )
}

/// Write the manifest for `asset` to `output` (parents are created) and return it.
/// The file is byte-compatible with manifests written by 0.2.x release tooling.
pub fn build_manifest(asset: &Path, output: &Path, version: &str) -> Result<Manifest> {
    let manifest = manifest_for_asset(asset, version)?;
    write_creating_parents(output, &manifest.to_bytes())?;
    Ok(manifest)
}

/// Sign raw `payload`; the result is base64 plus a trailing newline, as 0.2.x
/// release tooling wrote it.
pub fn sign_with(key: &SigningKey, payload: &[u8]) -> Vec<u8> {
    let mut signature = STANDARD.encode(key.sign(payload).to_bytes()).into_bytes();
    signature.push(b'\n');
    signature
}

/// Sign `manifest_path` with the PEM PKCS#8 Ed25519 key and write the signature to
/// `output`. Fails (writing nothing) unless the key matches the public key embedded
/// in the app.
pub fn sign_manifest_file(
    manifest_path: &Path,
    output: &Path,
    private_key_pem: &str,
) -> Result<()> {
    sign_manifest_file_with_public_key(
        manifest_path,
        output,
        private_key_pem,
        &APP_UPDATE_PUBLIC_KEY,
    )
}

/// [`sign_manifest_file`] checked against an explicit public key (tests).
pub fn sign_manifest_file_with_public_key(
    manifest_path: &Path,
    output: &Path,
    private_key_pem: &str,
    public_key: &[u8; 32],
) -> Result<()> {
    if private_key_pem.trim().is_empty() {
        return Err(Error::InvalidSignature(format!(
            "{SIGNING_KEY_ENVIRONMENT} is not set"
        )));
    }
    let key = SigningKey::from_pkcs8_pem(private_key_pem).map_err(|_| {
        Error::InvalidSignature(format!(
            "{SIGNING_KEY_ENVIRONMENT} must be a PEM PKCS#8 Ed25519 private key"
        ))
    })?;
    let payload = fs::read(manifest_path)
        .map_err(|error| Error::io(format!("cannot read {}", manifest_path.display()), error))?;
    let signature = sign_with(&key, &payload);
    // Fails when the key does not match the public key embedded in the app.
    verify_with_key(public_key, &payload, &signature)?;
    write_creating_parents(output, &signature)
}
