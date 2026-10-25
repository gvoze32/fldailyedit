//! Offline behavior tests: manifest matrix, signatures, check/download/stage with a
//! fake transport, and the release tool round trip.

use std::cell::RefCell;
use std::collections::HashMap;
use std::io::{Cursor, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;

use ed25519_dalek::SigningKey;
use ed25519_dalek::pkcs8::EncodePrivateKey;
use ed25519_dalek::pkcs8::spki::der::pem::LineEnding;
use fl_update::manifest::{
    APP_EXECUTABLE_ENTRY, APP_INSTALLER_ASSET_NAME, APP_INSTALLER_URL, APP_UPDATE_MANIFEST_URL,
    APP_UPDATE_PUBLIC_KEY, APP_UPDATE_SIGNATURE_URL, MAX_APP_ARCHIVE_BYTES,
};
use fl_update::release::{
    build_manifest, sign_manifest_file, sign_manifest_file_with_public_key, sign_with,
};
use fl_update::{
    Available, Error, ErrorKind, Fetch, Manifest, Response, Staged, Version, check_for_update,
    check_for_update_with_key, download_and_stage, verify_signature, verify_with_key,
};
use sha2::{Digest, Sha256};

// ---------------------------------------------------------------- helpers

fn signing_key() -> SigningKey {
    SigningKey::from_bytes(&[7_u8; 32])
}

fn public_key() -> [u8; 32] {
    signing_key().verifying_key().to_bytes()
}

fn sha256_hex(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

/// Single-line JSON with `, ` and `: ` separators (the default compact-with-spaces shape).
fn manifest_payload(archive: &[u8], version: &str) -> Vec<u8> {
    format!(
        "{{\"schema_version\": 1, \"version\": \"{version}\", \"asset_name\": \"{APP_INSTALLER_ASSET_NAME}\", \
         \"download_url\": \"{APP_INSTALLER_URL}\", \"archive_size\": {}, \"archive_sha256\": \"{}\"}}",
        archive.len(),
        sha256_hex(archive)
    )
    .into_bytes()
}

fn zip_payload(entries: &[(&str, &[u8])]) -> Vec<u8> {
    let mut writer = zip::ZipWriter::new(Cursor::new(Vec::new()));
    for (name, content) in entries {
        writer
            .start_file(*name, zip::write::SimpleFileOptions::default())
            .unwrap();
        writer.write_all(content).unwrap();
    }
    writer.finish().unwrap().into_inner()
}

fn installer_zip(executable: &[u8]) -> Vec<u8> {
    zip_payload(&[(APP_EXECUTABLE_ENTRY, executable)])
}

enum Route {
    Body {
        content_length: Option<u64>,
        body: Vec<u8>,
    },
    Status(u16),
}

#[derive(Default)]
struct FakeFetch {
    routes: HashMap<String, Route>,
    calls: RefCell<Vec<String>>,
}

impl FakeFetch {
    fn with(mut self, url: &str, body: &[u8]) -> Self {
        self.routes.insert(
            url.to_owned(),
            Route::Body {
                content_length: Some(body.len() as u64),
                body: body.to_vec(),
            },
        );
        self
    }

    fn with_lying_length(mut self, url: &str, content_length: Option<u64>, body: &[u8]) -> Self {
        self.routes.insert(
            url.to_owned(),
            Route::Body {
                content_length,
                body: body.to_vec(),
            },
        );
        self
    }

    fn with_status(mut self, url: &str, status: u16) -> Self {
        self.routes.insert(url.to_owned(), Route::Status(status));
        self
    }
}

impl Fetch for FakeFetch {
    fn get(&self, url: &str, _timeout: Duration) -> Result<Response, Error> {
        self.calls.borrow_mut().push(url.to_owned());
        match self.routes.get(url) {
            Some(Route::Body {
                content_length,
                body,
            }) => Ok(Response {
                content_length: *content_length,
                body: Box::new(Cursor::new(body.clone())),
            }),
            Some(Route::Status(status)) => Err(Error::Http(*status)),
            None => Err(Error::Http(404)),
        }
    }
}

fn signed_fetch(payload: &[u8]) -> FakeFetch {
    FakeFetch::default()
        .with(APP_UPDATE_MANIFEST_URL, payload)
        .with(
            APP_UPDATE_SIGNATURE_URL,
            &sign_with(&signing_key(), payload),
        )
}

fn available_for(archive: &[u8]) -> Available {
    let manifest = Manifest::parse(&manifest_payload(archive, "0.3.0")).unwrap();
    Available::new(manifest).unwrap()
}

fn stage(archive: &[u8], fetch: &FakeFetch) -> Result<Staged, Error> {
    let available = available_for(archive);
    download_and_stage(&available, fetch, &mut |_, _| {}, &AtomicBool::new(false))
}

fn json_with(mutate: impl FnOnce(&mut serde_json::Value)) -> Vec<u8> {
    let mut document: serde_json::Value =
        serde_json::from_slice(&manifest_payload(b"installer archive", "0.3.0")).unwrap();
    mutate(&mut document);
    serde_json::to_vec(&document).unwrap()
}

fn kind_of(result: Result<impl std::fmt::Debug, Error>) -> ErrorKind {
    result.unwrap_err().kind()
}

// ------------------------------------------------------------ manifest

#[test]
fn accepts_a_wellformed_manifest() {
    let manifest = Manifest::parse(&manifest_payload(b"installer archive", "0.3.0")).unwrap();
    assert_eq!(
        manifest.version,
        Version {
            major: 0,
            minor: 3,
            patch: 0
        }
    );
    assert_eq!(manifest.asset_name, APP_INSTALLER_ASSET_NAME);
    assert_eq!(manifest.download_url, APP_INSTALLER_URL);
    assert_eq!(manifest.archive_size, 17);
    assert_eq!(manifest.archive_sha256, sha256_hex(b"installer archive"));
}

#[test]
fn rejects_malformed_documents() {
    let valid = String::from_utf8(manifest_payload(b"x", "1.0.0")).unwrap();
    let duplicate = valid.replacen(
        "\"version\": \"1.0.0\"",
        "\"version\": \"1.0.0\", \"version\": \"1.0.1\"",
        1,
    );
    let cases: Vec<(&str, Vec<u8>, ErrorKind)> = vec![
        ("empty", b"".to_vec(), ErrorKind::InvalidManifest),
        ("not json", b"nope".to_vec(), ErrorKind::InvalidManifest),
        (
            "invalid utf8",
            vec![0xff, 0xfe, b'{', b'}'],
            ErrorKind::InvalidManifest,
        ),
        ("array", b"[]".to_vec(), ErrorKind::InvalidManifest),
        ("string", b"\"x\"".to_vec(), ErrorKind::InvalidManifest),
        ("empty object", b"{}".to_vec(), ErrorKind::InvalidManifest),
        (
            "duplicate key",
            duplicate.into_bytes(),
            ErrorKind::InvalidManifest,
        ),
        (
            "trailing garbage",
            [valid.as_bytes(), b" x"].concat(),
            ErrorKind::InvalidManifest,
        ),
        (
            "missing key",
            json_with(|d| {
                d.as_object_mut().unwrap().remove("archive_size");
            }),
            ErrorKind::InvalidManifest,
        ),
        (
            "extra key",
            json_with(|d| {
                d["extra"] = "x".into();
            }),
            ErrorKind::InvalidManifest,
        ),
        (
            "schema 2",
            json_with(|d| d["schema_version"] = 2.into()),
            ErrorKind::Unsupported,
        ),
        (
            "schema true",
            json_with(|d| d["schema_version"] = true.into()),
            ErrorKind::Unsupported,
        ),
        (
            "schema string",
            json_with(|d| d["schema_version"] = "1".into()),
            ErrorKind::Unsupported,
        ),
        (
            "schema float",
            json_with(|d| d["schema_version"] = serde_json::json!(1.0)),
            ErrorKind::Unsupported,
        ),
        (
            "version number",
            json_with(|d| d["version"] = 1.into()),
            ErrorKind::InvalidManifest,
        ),
        (
            "version empty",
            json_with(|d| d["version"] = "".into()),
            ErrorKind::InvalidManifest,
        ),
        (
            "asset renamed",
            json_with(|d| d["asset_name"] = "other.zip".into()),
            ErrorKind::UntrustedUrl,
        ),
        (
            "asset empty",
            json_with(|d| d["asset_name"] = "".into()),
            ErrorKind::InvalidManifest,
        ),
        (
            "url other host",
            json_with(|d| d["download_url"] = "https://example.com/installer.zip".into()),
            ErrorKind::UntrustedUrl,
        ),
        (
            "url http",
            json_with(|d| {
                d["download_url"] = APP_INSTALLER_URL.replacen("https", "http", 1).into()
            }),
            ErrorKind::UntrustedUrl,
        ),
        (
            "url query",
            json_with(|d| d["download_url"] = format!("{APP_INSTALLER_URL}?x=1").into()),
            ErrorKind::UntrustedUrl,
        ),
        (
            "size zero",
            json_with(|d| d["archive_size"] = 0.into()),
            ErrorKind::InvalidManifest,
        ),
        (
            "size negative",
            json_with(|d| d["archive_size"] = (-1).into()),
            ErrorKind::InvalidManifest,
        ),
        (
            "size string",
            json_with(|d| d["archive_size"] = "5".into()),
            ErrorKind::InvalidManifest,
        ),
        (
            "size float",
            json_with(|d| d["archive_size"] = serde_json::json!(5.5)),
            ErrorKind::InvalidManifest,
        ),
        (
            "size bool",
            json_with(|d| d["archive_size"] = true.into()),
            ErrorKind::InvalidManifest,
        ),
        (
            "size too large",
            json_with(|d| d["archive_size"] = (MAX_APP_ARCHIVE_BYTES + 1).into()),
            ErrorKind::TooLarge,
        ),
        (
            "sha uppercase",
            json_with(|d| d["archive_sha256"] = sha256_hex(b"x").to_uppercase().into()),
            ErrorKind::InvalidManifest,
        ),
        (
            "sha short",
            json_with(|d| d["archive_sha256"] = "abc".into()),
            ErrorKind::InvalidManifest,
        ),
        (
            "sha not hex",
            json_with(|d| d["archive_sha256"] = "g".repeat(64).into()),
            ErrorKind::InvalidManifest,
        ),
        (
            "sha trailing newline",
            json_with(|d| d["archive_sha256"] = format!("{}\n", sha256_hex(b"x")).into()),
            ErrorKind::InvalidManifest,
        ),
    ];
    for (name, payload, expected) in cases {
        let result = Manifest::parse(&payload);
        assert_eq!(
            result.as_ref().err().map(Error::kind),
            Some(expected),
            "{name}: {result:?}"
        );
    }
}

#[test]
fn accepts_the_maximum_archive_size() {
    let payload = json_with(|d| d["archive_size"] = MAX_APP_ARCHIVE_BYTES.into());
    assert_eq!(
        Manifest::parse(&payload).unwrap().archive_size,
        MAX_APP_ARCHIVE_BYTES
    );
}

#[test]
fn version_requires_canonical_numeric_triples() {
    for bad in [
        "",
        "1",
        "1.2",
        "1.2.3.4",
        "01.2.3",
        "1.02.3",
        "1.2.03",
        "1.2.3-beta",
        "1.2.3+b",
        "v1.2.3",
        " 1.2.3",
        "1.2.3 ",
        "1.2.3\n",
        "+1.2.3",
        "-1.2.3",
        "1..3",
        "a.b.c",
        "1.2.99999999999999999999",
        "\u{0661}.0.0",
    ] {
        let parsed = bad.parse::<Version>();
        assert_eq!(
            parsed.as_ref().err().map(Error::kind),
            Some(ErrorKind::InvalidManifest),
            "{bad:?}"
        );
    }
    assert_eq!("0.0.0".parse::<Version>().unwrap().to_string(), "0.0.0");
    assert_eq!(
        "10.20.30".parse::<Version>().unwrap().to_string(),
        "10.20.30"
    );
}

#[test]
fn versions_compare_numerically_per_component() {
    let v = |text: &str| text.parse::<Version>().unwrap();
    assert!(v("0.3.0").is_newer(&v("0.2.0")));
    assert!(
        v("0.10.0").is_newer(&v("0.9.0")),
        "numeric, not lexicographic"
    );
    assert!(v("1.0.0").is_newer(&v("0.99.99")));
    assert!(v("1.0.1").is_newer(&v("1.0.0")));
    assert!(!v("1.0.0").is_newer(&v("1.0.0")));
    assert!(!v("0.2.0").is_newer(&v("0.3.0")));
    assert!(v("2.0.0") > v("1.99.99"));
}

#[test]
fn canonical_bytes_match_the_0_2_generator() {
    let manifest = Manifest::parse(&manifest_payload(b"abc", "1.2.3")).unwrap();
    let expected = format!(
        "{{\n  \"archive_sha256\": \"{}\",\n  \"archive_size\": 3,\n  \"asset_name\": \"FLDailyEditInstaller.zip\",\n  \
         \"download_url\": \"https://github.com/gvoze32/fldailyedit/releases/download/latest/FLDailyEditInstaller.zip\",\n  \
         \"schema_version\": 1,\n  \"version\": \"1.2.3\"\n}}\n",
        sha256_hex(b"abc")
    );
    assert_eq!(String::from_utf8(manifest.to_bytes()).unwrap(), expected);
    assert_eq!(Manifest::parse(&manifest.to_bytes()).unwrap(), manifest);
}

// ----------------------------------------------------------- signatures

#[test]
fn embedded_public_key_matches_the_shipped_constant() {
    use base64::Engine as _;
    let decoded = base64::engine::general_purpose::STANDARD
        .decode("sYH8IUcmk160DJcjyu6TMRm4WEMtTYza4KL/kwKyYsI=")
        .unwrap();
    assert_eq!(decoded, APP_UPDATE_PUBLIC_KEY);
    let error = verify_signature(b"payload", &sign_with(&signing_key(), b"payload")).unwrap_err();
    assert_eq!(
        error.kind(),
        ErrorKind::InvalidSignature,
        "throwaway key must not verify"
    );
}

#[test]
fn signatures_verify_only_for_the_signed_bytes_and_key() {
    let payload = b"manifest bytes";
    let signature = sign_with(&signing_key(), payload);
    assert!(signature.ends_with(b"\n"));
    verify_with_key(&public_key(), payload, &signature).unwrap();
    // Whitespace around the base64 is tolerated.
    verify_with_key(
        &public_key(),
        payload,
        &[b" \r\n".as_slice(), &signature, b"\t "].concat(),
    )
    .unwrap();

    assert_eq!(
        kind_of(verify_with_key(&public_key(), b"tampered", &signature)),
        ErrorKind::InvalidSignature
    );
    let other = SigningKey::from_bytes(&[9_u8; 32]);
    assert_eq!(
        kind_of(verify_with_key(
            &public_key(),
            payload,
            &sign_with(&other, payload)
        )),
        ErrorKind::InvalidSignature
    );
}

#[test]
fn malformed_signatures_are_rejected() {
    let payload = b"manifest bytes";
    let good = String::from_utf8(sign_with(&signing_key(), payload)).unwrap();
    let good = good.trim();
    for bad in [
        "".to_owned(),
        "not base64!".to_owned(),
        "c2hvcnQ=".to_owned(),
        good[..good.len() - 2].to_owned(), // unpadded
        format!("{good}{good}"),
        good.replace('=', "").replace('A', "*"),
    ] {
        let kind = kind_of(verify_with_key(&public_key(), payload, bad.as_bytes()));
        assert_eq!(kind, ErrorKind::InvalidSignature, "{bad:?}");
    }
}

// ------------------------------------------------------- check_for_update

#[test]
fn check_returns_a_newer_verified_release() {
    let archive = installer_zip(b"exe");
    let payload = manifest_payload(&archive, "0.3.0");
    let fetch = signed_fetch(&payload);

    let available = check_for_update_with_key("0.2.0", &fetch, &public_key())
        .unwrap()
        .unwrap();

    assert_eq!(available.version.to_string(), "0.3.0");
    assert_eq!(available.size, archive.len() as u64);
    assert_eq!(
        *fetch.calls.borrow(),
        [APP_UPDATE_MANIFEST_URL, APP_UPDATE_SIGNATURE_URL]
    );
}

#[test]
fn check_returns_none_when_not_newer() {
    let payload = manifest_payload(b"x", "0.3.0");
    let fetch = signed_fetch(&payload);
    assert!(
        check_for_update_with_key("0.3.0", &fetch, &public_key())
            .unwrap()
            .is_none()
    );
    assert!(
        check_for_update_with_key("1.0.0", &fetch, &public_key())
            .unwrap()
            .is_none()
    );
}

#[test]
fn check_with_the_production_key_rejects_unsigned_by_it() {
    let payload = manifest_payload(b"x", "0.3.0");
    let fetch = signed_fetch(&payload);
    assert_eq!(
        kind_of(check_for_update("0.2.0", &fetch)),
        ErrorKind::InvalidSignature
    );
}

#[test]
fn check_rejects_an_invalid_current_version() {
    let fetch = signed_fetch(&manifest_payload(b"x", "0.3.0"));
    assert_eq!(
        kind_of(check_for_update_with_key("dev", &fetch, &public_key())),
        ErrorKind::InvalidManifest
    );
}

#[test]
fn check_reports_a_missing_signature() {
    let fetch =
        FakeFetch::default().with(APP_UPDATE_MANIFEST_URL, &manifest_payload(b"x", "0.3.0"));
    assert_eq!(
        kind_of(check_for_update_with_key("0.2.0", &fetch, &public_key())),
        ErrorKind::MissingSignature
    );
}

#[test]
fn check_reports_http_failures() {
    let fetch = FakeFetch::default().with_status(APP_UPDATE_MANIFEST_URL, 500);
    let error = check_for_update_with_key("0.2.0", &fetch, &public_key()).unwrap_err();
    assert!(matches!(error, Error::Http(500)));
    assert_eq!(error.kind(), ErrorKind::Http);

    let payload = manifest_payload(b"x", "0.3.0");
    let fetch = FakeFetch::default()
        .with(APP_UPDATE_MANIFEST_URL, &payload)
        .with_status(APP_UPDATE_SIGNATURE_URL, 503);
    let error = check_for_update_with_key("0.2.0", &fetch, &public_key()).unwrap_err();
    assert!(matches!(error, Error::Http(503)));
}

#[test]
fn check_rejects_a_tampered_manifest() {
    let signed = manifest_payload(b"original", "0.3.0");
    let fetch = FakeFetch::default()
        .with(
            APP_UPDATE_MANIFEST_URL,
            &manifest_payload(b"tampered", "0.3.0"),
        )
        .with(
            APP_UPDATE_SIGNATURE_URL,
            &sign_with(&signing_key(), &signed),
        );
    assert_eq!(
        kind_of(check_for_update_with_key("0.2.0", &fetch, &public_key())),
        ErrorKind::InvalidSignature
    );
}

#[test]
fn check_enforces_size_limits_on_header_and_stream() {
    let big = vec![b' '; 64 * 1024 + 1];
    let by_header = FakeFetch::default().with(APP_UPDATE_MANIFEST_URL, &big);
    assert_eq!(
        kind_of(check_for_update_with_key(
            "0.2.0",
            &by_header,
            &public_key()
        )),
        ErrorKind::TooLarge
    );

    let by_stream = FakeFetch::default().with_lying_length(APP_UPDATE_MANIFEST_URL, Some(10), &big);
    assert_eq!(
        kind_of(check_for_update_with_key(
            "0.2.0",
            &by_stream,
            &public_key()
        )),
        ErrorKind::TooLarge
    );

    let no_header = FakeFetch::default().with_lying_length(APP_UPDATE_MANIFEST_URL, None, &big);
    assert_eq!(
        kind_of(check_for_update_with_key(
            "0.2.0",
            &no_header,
            &public_key()
        )),
        ErrorKind::TooLarge
    );

    let payload = manifest_payload(b"x", "0.3.0");
    let big_signature = vec![b'A'; 1025];
    let fetch = FakeFetch::default()
        .with(APP_UPDATE_MANIFEST_URL, &payload)
        .with(APP_UPDATE_SIGNATURE_URL, &big_signature);
    assert_eq!(
        kind_of(check_for_update_with_key("0.2.0", &fetch, &public_key())),
        ErrorKind::InvalidSignature
    );
}

// --------------------------------------------------------------- staging

#[test]
fn downloads_verifies_and_extracts_only_the_executable() {
    let executable = b"new executable bytes".repeat(1000);
    let archive = installer_zip(&executable);
    let fetch = FakeFetch::default().with(APP_INSTALLER_URL, &archive);
    let available = available_for(&archive);
    let mut seen = Vec::new();

    let staged = download_and_stage(
        &available,
        &fetch,
        &mut |done, total| seen.push((done, total)),
        &AtomicBool::new(false),
    )
    .unwrap();

    assert_eq!(staged.version.to_string(), "0.3.0");
    assert_eq!(std::fs::read(&staged.executable).unwrap(), executable);
    assert_eq!(staged.executable.file_name().unwrap(), APP_EXECUTABLE_ENTRY);
    let directory = staged.executable.parent().unwrap().to_path_buf();
    assert!(
        directory
            .file_name()
            .unwrap()
            .to_str()
            .unwrap()
            .starts_with("fldailyedit-app-update-")
    );
    assert_eq!(
        std::fs::read_dir(&directory).unwrap().count(),
        1,
        "archive is deleted"
    );
    assert_eq!(seen.first(), Some(&(0, archive.len() as u64)));
    assert_eq!(
        seen.last(),
        Some(&(archive.len() as u64, archive.len() as u64))
    );
    assert!(seen.windows(2).all(|pair| pair[0].0 <= pair[1].0));
    assert_eq!(*fetch.calls.borrow(), [APP_INSTALLER_URL]);

    staged.discard();
    assert!(!directory.exists());
}

#[test]
fn checksum_mismatch_is_rejected() {
    let archive = installer_zip(b"genuine");
    let mut forged = archive.clone();
    let last = forged.len() - 1;
    forged[last] ^= 0xff;
    let fetch = FakeFetch::default().with(APP_INSTALLER_URL, &forged);
    assert_eq!(
        kind_of(stage(&archive, &fetch)),
        ErrorKind::ChecksumMismatch
    );
}

#[test]
fn truncated_and_overlong_downloads_are_rejected() {
    let archive = installer_zip(b"genuine executable");

    let short = FakeFetch::default().with(APP_INSTALLER_URL, &archive[..archive.len() - 1]);
    assert_eq!(kind_of(stage(&archive, &short)), ErrorKind::SizeMismatch);

    let mut longer = archive.clone();
    longer.push(0);
    let long_with_header = FakeFetch::default().with(APP_INSTALLER_URL, &longer);
    assert_eq!(
        kind_of(stage(&archive, &long_with_header)),
        ErrorKind::SizeMismatch
    );

    // Header claims the right size but the stream keeps going.
    let lying = FakeFetch::default().with_lying_length(
        APP_INSTALLER_URL,
        Some(archive.len() as u64),
        &longer,
    );
    assert_eq!(kind_of(stage(&archive, &lying)), ErrorKind::SizeMismatch);

    // No header at all.
    let headerless = FakeFetch::default().with_lying_length(APP_INSTALLER_URL, None, &longer);
    assert_eq!(
        kind_of(stage(&archive, &headerless)),
        ErrorKind::SizeMismatch
    );
}

#[test]
fn content_length_beyond_the_hard_cap_is_too_large() {
    let archive = installer_zip(b"genuine");
    let fetch = FakeFetch::default().with_lying_length(
        APP_INSTALLER_URL,
        Some(MAX_APP_ARCHIVE_BYTES + 1),
        &archive,
    );
    assert_eq!(kind_of(stage(&archive, &fetch)), ErrorKind::TooLarge);
}

#[test]
fn download_http_errors_are_reported() {
    let archive = installer_zip(b"x");
    let fetch = FakeFetch::default().with_status(APP_INSTALLER_URL, 502);
    assert!(matches!(
        stage(&archive, &fetch).unwrap_err(),
        Error::Http(502)
    ));
}

#[test]
fn bad_zip_layouts_are_rejected() {
    let layouts: Vec<(&str, Vec<u8>)> = vec![
        ("not a zip", b"definitely not a zip file".to_vec()),
        ("no entries", zip_payload(&[])),
        ("wrong name", zip_payload(&[("other.exe", b"x")])),
        (
            "nested path",
            zip_payload(&[("dir/FLDailyEditInstaller.exe", b"x")]),
        ),
        (
            "wrong case",
            zip_payload(&[("fldailyeditinstaller.exe", b"x")]),
        ),
        (
            "two entries",
            zip_payload(&[(APP_EXECUTABLE_ENTRY, b"x"), ("readme.txt", b"y")]),
        ),
        (
            "empty executable",
            zip_payload(&[(APP_EXECUTABLE_ENTRY, b"")]),
        ),
        (
            "directory entry",
            zip_payload(&[("FLDailyEditInstaller.exe/", b"")]),
        ),
    ];
    for (name, archive) in layouts {
        let fetch = FakeFetch::default().with(APP_INSTALLER_URL, &archive);
        let kind = kind_of(stage(&archive, &fetch));
        assert_eq!(kind, ErrorKind::InvalidArchive, "{name}");
    }
}

#[test]
fn corrupted_zip_payload_is_rejected() {
    let executable = vec![0x5a_u8; 4096];
    let mut archive = installer_zip(&executable);
    // Flip a byte inside the stored data: the CRC check must catch it.
    archive[64] ^= 0xff;
    let fetch = FakeFetch::default().with(APP_INSTALLER_URL, &archive);
    assert_eq!(kind_of(stage(&archive, &fetch)), ErrorKind::InvalidArchive);
}

#[test]
fn cancellation_stops_the_download() {
    let archive = installer_zip(&vec![1_u8; 300_000]);
    let fetch = FakeFetch::default().with(APP_INSTALLER_URL, &archive);
    let available = available_for(&archive);

    let cancelled = AtomicBool::new(true);
    let error = download_and_stage(&available, &fetch, &mut |_, _| {}, &cancelled).unwrap_err();
    assert_eq!(error.kind(), ErrorKind::Cancelled);
    assert!(
        fetch.calls.borrow().is_empty(),
        "no request when already cancelled"
    );

    let cancel = AtomicBool::new(false);
    let mut chunks = 0;
    let error = download_and_stage(
        &available,
        &fetch,
        &mut |done, _| {
            if done > 0 {
                chunks += 1;
                cancel.store(true, Ordering::Relaxed);
            }
        },
        &cancel,
    )
    .unwrap_err();
    assert_eq!(error.kind(), ErrorKind::Cancelled);
    assert_eq!(chunks, 1);
}

#[test]
fn available_cannot_be_built_from_an_invalid_manifest() {
    let mut manifest = Manifest::parse(&manifest_payload(b"x", "0.3.0")).unwrap();
    manifest.download_url = "https://example.com/x.zip".into();
    assert_eq!(kind_of(Available::new(manifest)), ErrorKind::UntrustedUrl);
}

// ------------------------------------------------------ release tooling

fn write_asset(directory: &Path, archive: &[u8]) -> PathBuf {
    let asset = directory.join(APP_INSTALLER_ASSET_NAME);
    std::fs::write(&asset, archive).unwrap();
    asset
}

fn pem(key: &SigningKey) -> String {
    key.to_pkcs8_pem(LineEnding::LF).unwrap().to_string()
}

#[test]
fn build_sign_verify_parse_round_trip() {
    let directory = tempfile::tempdir().unwrap();
    let archive = installer_zip(b"rust gui exe");
    let asset = write_asset(directory.path(), &archive);
    let manifest_path = directory.path().join("out/installer-update.json");
    let signature_path = directory.path().join("out/installer-update.json.sig");

    let built = build_manifest(&asset, &manifest_path, "1.0.0").unwrap();
    sign_manifest_file_with_public_key(
        &manifest_path,
        &signature_path,
        &pem(&signing_key()),
        &public_key(),
    )
    .unwrap();

    let payload = std::fs::read(&manifest_path).unwrap();
    let signature = std::fs::read(&signature_path).unwrap();
    // Rules the 0.2.x parser enforces.
    assert!(payload.ends_with(b"}\n"));
    assert!(payload.len() <= 64 * 1024);
    assert_eq!(signature.len(), 89, "88 base64 chars + newline");
    assert!(signature.ends_with(b"\n"));
    verify_with_key(&public_key(), &payload, &signature).unwrap();
    let parsed = Manifest::parse(&payload).unwrap();
    assert_eq!(parsed, built);
    assert_eq!(parsed.archive_size, archive.len() as u64);
    assert_eq!(parsed.archive_sha256, sha256_hex(&archive));
    assert_eq!(parsed.download_url, APP_INSTALLER_URL);
    let document: serde_json::Value = serde_json::from_slice(&payload).unwrap();
    assert_eq!(document.as_object().unwrap().len(), 6);

    // And the whole client path accepts it.
    let fetch = FakeFetch::default()
        .with(APP_UPDATE_MANIFEST_URL, &payload)
        .with(APP_UPDATE_SIGNATURE_URL, &signature)
        .with(APP_INSTALLER_URL, &archive);
    let available = check_for_update_with_key("0.2.0", &fetch, &public_key())
        .unwrap()
        .unwrap();
    let staged =
        download_and_stage(&available, &fetch, &mut |_, _| {}, &AtomicBool::new(false)).unwrap();
    assert_eq!(std::fs::read(&staged.executable).unwrap(), b"rust gui exe");
    staged.discard();
}

#[test]
fn build_rejects_bad_assets_and_versions() {
    let directory = tempfile::tempdir().unwrap();
    let output = directory.path().join("m.json");
    let good = installer_zip(b"exe");

    let wrong_name = directory.path().join("other.zip");
    std::fs::write(&wrong_name, &good).unwrap();
    assert_eq!(
        kind_of(build_manifest(&wrong_name, &output, "1.0.0")),
        ErrorKind::InvalidManifest
    );

    let asset = write_asset(directory.path(), &good);
    assert_eq!(
        kind_of(build_manifest(&asset, &output, "1.0")),
        ErrorKind::InvalidManifest
    );

    std::fs::write(&asset, b"").unwrap();
    assert_eq!(
        kind_of(build_manifest(&asset, &output, "1.0.0")),
        ErrorKind::InvalidManifest
    );

    std::fs::write(
        &asset,
        zip_payload(&[(APP_EXECUTABLE_ENTRY, b"x"), ("extra.txt", b"y")]),
    )
    .unwrap();
    assert_eq!(
        kind_of(build_manifest(&asset, &output, "1.0.0")),
        ErrorKind::InvalidArchive
    );

    std::fs::write(&asset, zip_payload(&[("FLDailyEdit.exe", b"x")])).unwrap();
    assert_eq!(
        kind_of(build_manifest(&asset, &output, "1.0.0")),
        ErrorKind::InvalidArchive
    );

    std::fs::remove_file(&asset).unwrap();
    assert_eq!(
        kind_of(build_manifest(&asset, &output, "1.0.0")),
        ErrorKind::Io
    );
    assert!(!output.exists(), "nothing is written on failure");
}

#[test]
fn signing_refuses_missing_malformed_or_mismatched_keys() {
    let directory = tempfile::tempdir().unwrap();
    let manifest_path = directory.path().join("installer-update.json");
    let signature_path = directory.path().join("installer-update.json.sig");
    std::fs::write(&manifest_path, b"{}\n").unwrap();

    assert_eq!(
        kind_of(sign_manifest_file(&manifest_path, &signature_path, "  \n")),
        ErrorKind::InvalidSignature
    );
    assert_eq!(
        kind_of(sign_manifest_file(
            &manifest_path,
            &signature_path,
            "-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----\n"
        )),
        ErrorKind::InvalidSignature
    );
    // A valid Ed25519 key that is not the production key must be refused.
    assert_eq!(
        kind_of(sign_manifest_file(
            &manifest_path,
            &signature_path,
            &pem(&signing_key())
        )),
        ErrorKind::InvalidSignature
    );
    let other = SigningKey::from_bytes(&[9_u8; 32]);
    assert_eq!(
        kind_of(sign_manifest_file_with_public_key(
            &manifest_path,
            &signature_path,
            &pem(&other),
            &public_key()
        )),
        ErrorKind::InvalidSignature
    );
    assert!(
        !signature_path.exists(),
        "no signature is written for a mismatched key"
    );
}
