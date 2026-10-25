//! Offline behavior tests: manifest matrix, signatures, check/download/stage with a
//! fake transport, and the release tool round trip.

use std::cell::RefCell;
use std::collections::HashMap;
use std::io::Cursor;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;

use ed25519_dalek::SigningKey;
use ed25519_dalek::pkcs8::EncodePrivateKey;
use ed25519_dalek::pkcs8::spki::der::pem::LineEnding;
use fl_update::manifest::{
    APP_EXECUTABLE_NAME, APP_UPDATE_MANIFEST_URL, APP_UPDATE_PUBLIC_KEY, APP_UPDATE_SIGNATURE_URL,
    MAX_APP_EXECUTABLE_BYTES, release_download_url,
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

/// URL the `0.3.0` release serves its executable from.
fn download_url() -> String {
    release_download_url(&"0.3.0".parse().unwrap())
}

/// Single-line JSON with `, ` and `: ` separators (the default compact-with-spaces shape).
fn manifest_payload(executable: &[u8], version: &str) -> Vec<u8> {
    format!(
        "{{\"schema_version\": 1, \"version\": \"{version}\", \
         \"download_url\": \"https://github.com/gvoze32/fldailyedit/releases/download/v{version}/{APP_EXECUTABLE_NAME}\", \
         \"size\": {}, \"sha256\": \"{}\"}}",
        executable.len(),
        sha256_hex(executable)
    )
    .into_bytes()
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

fn available_for(executable: &[u8]) -> Available {
    let manifest = Manifest::parse(&manifest_payload(executable, "0.3.0")).unwrap();
    Available::new(manifest).unwrap()
}

fn stage(executable: &[u8], fetch: &FakeFetch) -> Result<Staged, Error> {
    let available = available_for(executable);
    download_and_stage(&available, fetch, &mut |_, _| {}, &AtomicBool::new(false))
}

fn json_with(mutate: impl FnOnce(&mut serde_json::Value)) -> Vec<u8> {
    let mut document: serde_json::Value =
        serde_json::from_slice(&manifest_payload(b"release executable", "0.3.0")).unwrap();
    mutate(&mut document);
    serde_json::to_vec(&document).unwrap()
}

fn kind_of(result: Result<impl std::fmt::Debug, Error>) -> ErrorKind {
    result.unwrap_err().kind()
}

// ------------------------------------------------------------ manifest

#[test]
fn accepts_a_wellformed_manifest() {
    let manifest = Manifest::parse(&manifest_payload(b"release executable", "0.3.0")).unwrap();
    assert_eq!(
        manifest.version,
        Version {
            major: 0,
            minor: 3,
            patch: 0
        }
    );
    assert_eq!(manifest.download_url, download_url());
    assert_eq!(
        manifest.download_url,
        "https://github.com/gvoze32/fldailyedit/releases/download/v0.3.0/FLDailyEdit.exe"
    );
    assert_eq!(manifest.size, 18);
    assert_eq!(manifest.sha256, sha256_hex(b"release executable"));
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
                d.as_object_mut().unwrap().remove("size");
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
            "url other host",
            json_with(|d| d["download_url"] = "https://example.com/FLDailyEdit.exe".into()),
            ErrorKind::UntrustedUrl,
        ),
        (
            "url http",
            json_with(|d| d["download_url"] = download_url().replacen("https", "http", 1).into()),
            ErrorKind::UntrustedUrl,
        ),
        (
            "url query",
            json_with(|d| d["download_url"] = format!("{}?x=1", download_url()).into()),
            ErrorKind::UntrustedUrl,
        ),
        (
            "url fragment",
            json_with(|d| d["download_url"] = format!("{}#x", download_url()).into()),
            ErrorKind::UntrustedUrl,
        ),
        (
            "url other port",
            json_with(|d| {
                d["download_url"] = download_url().replacen(".com", ".com:8443", 1).into()
            }),
            ErrorKind::UntrustedUrl,
        ),
        (
            "url credentials",
            json_with(|d| d["download_url"] = download_url().replacen("//", "//user@", 1).into()),
            ErrorKind::UntrustedUrl,
        ),
        (
            "url lookalike host",
            json_with(|d| {
                d["download_url"] = download_url()
                    .replacen("github.com", "github.com.evil.example", 1)
                    .into()
            }),
            ErrorKind::UntrustedUrl,
        ),
        (
            "url other repository",
            json_with(|d| {
                d["download_url"] = download_url()
                    .replacen("gvoze32/fldailyedit", "evil/fldailyedit", 1)
                    .into()
            }),
            ErrorKind::UntrustedUrl,
        ),
        (
            "url other tag",
            json_with(|d| {
                d["download_url"] = download_url().replacen("v0.3.0", "v0.2.0", 1).into()
            }),
            ErrorKind::UntrustedUrl,
        ),
        (
            "url rolling latest tag",
            json_with(|d| {
                d["download_url"] = download_url().replacen("v0.3.0", "latest", 1).into()
            }),
            ErrorKind::UntrustedUrl,
        ),
        (
            "url untagged version",
            json_with(|d| d["download_url"] = download_url().replacen("v0.3.0", "0.3.0", 1).into()),
            ErrorKind::UntrustedUrl,
        ),
        (
            "url other asset",
            json_with(|d| {
                d["download_url"] = download_url()
                    .replacen("FLDailyEdit.exe", "other.exe", 1)
                    .into()
            }),
            ErrorKind::UntrustedUrl,
        ),
        (
            "url trailing slash",
            json_with(|d| d["download_url"] = format!("{}/", download_url()).into()),
            ErrorKind::UntrustedUrl,
        ),
        (
            "url empty",
            json_with(|d| d["download_url"] = "".into()),
            ErrorKind::InvalidManifest,
        ),
        (
            "legacy asset_name key",
            json_with(|d| d["asset_name"] = "FLDailyEditInstaller.zip".into()),
            ErrorKind::InvalidManifest,
        ),
        (
            "legacy archive keys",
            json_with(|d| {
                let object = d.as_object_mut().unwrap();
                let size = object.remove("size").unwrap();
                let sha = object.remove("sha256").unwrap();
                object.insert("archive_size".into(), size);
                object.insert("archive_sha256".into(), sha);
            }),
            ErrorKind::InvalidManifest,
        ),
        (
            "size zero",
            json_with(|d| d["size"] = 0.into()),
            ErrorKind::InvalidManifest,
        ),
        (
            "size negative",
            json_with(|d| d["size"] = (-1).into()),
            ErrorKind::InvalidManifest,
        ),
        (
            "size string",
            json_with(|d| d["size"] = "5".into()),
            ErrorKind::InvalidManifest,
        ),
        (
            "size float",
            json_with(|d| d["size"] = serde_json::json!(5.5)),
            ErrorKind::InvalidManifest,
        ),
        (
            "size bool",
            json_with(|d| d["size"] = true.into()),
            ErrorKind::InvalidManifest,
        ),
        (
            "size too large",
            json_with(|d| d["size"] = (MAX_APP_EXECUTABLE_BYTES + 1).into()),
            ErrorKind::TooLarge,
        ),
        (
            "sha uppercase",
            json_with(|d| d["sha256"] = sha256_hex(b"x").to_uppercase().into()),
            ErrorKind::InvalidManifest,
        ),
        (
            "sha short",
            json_with(|d| d["sha256"] = "abc".into()),
            ErrorKind::InvalidManifest,
        ),
        (
            "sha not hex",
            json_with(|d| d["sha256"] = "g".repeat(64).into()),
            ErrorKind::InvalidManifest,
        ),
        (
            "sha trailing newline",
            json_with(|d| d["sha256"] = format!("{}\n", sha256_hex(b"x")).into()),
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
fn accepts_the_maximum_executable_size() {
    let payload = json_with(|d| d["size"] = MAX_APP_EXECUTABLE_BYTES.into());
    assert_eq!(
        Manifest::parse(&payload).unwrap().size,
        MAX_APP_EXECUTABLE_BYTES
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
fn canonical_bytes_are_sorted_indented_and_newline_terminated() {
    let manifest = Manifest::parse(&manifest_payload(b"abc", "1.2.3")).unwrap();
    let expected = format!(
        "{{\n  \"download_url\": \"https://github.com/gvoze32/fldailyedit/releases/download/v1.2.3/FLDailyEdit.exe\",\n  \
         \"schema_version\": 1,\n  \"sha256\": \"{}\",\n  \"size\": 3,\n  \"version\": \"1.2.3\"\n}}\n",
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
    let executable = b"exe".to_vec();
    let payload = manifest_payload(&executable, "0.3.0");
    let fetch = signed_fetch(&payload);

    let available = check_for_update_with_key("0.2.0", &fetch, &public_key())
        .unwrap()
        .unwrap();

    assert_eq!(available.version.to_string(), "0.3.0");
    assert_eq!(available.size, executable.len() as u64);
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
fn downloads_and_verifies_the_executable() {
    let executable = b"new executable bytes".repeat(1000);
    let fetch = FakeFetch::default().with(&download_url(), &executable);
    let available = available_for(&executable);
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
    assert_eq!(staged.executable.file_name().unwrap(), APP_EXECUTABLE_NAME);
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
        "only the executable is staged"
    );
    assert_eq!(seen.first(), Some(&(0, executable.len() as u64)));
    assert_eq!(
        seen.last(),
        Some(&(executable.len() as u64, executable.len() as u64))
    );
    assert!(seen.windows(2).all(|pair| pair[0].0 <= pair[1].0));
    assert_eq!(*fetch.calls.borrow(), [download_url()]);

    staged.discard();
    assert!(!directory.exists());
}

#[test]
fn checksum_mismatch_is_rejected() {
    let executable = b"genuine".to_vec();
    let mut forged = executable.clone();
    let last = forged.len() - 1;
    forged[last] ^= 0xff;
    let fetch = FakeFetch::default().with(&download_url(), &forged);
    assert_eq!(
        kind_of(stage(&executable, &fetch)),
        ErrorKind::ChecksumMismatch
    );
}

#[test]
fn truncated_and_overlong_downloads_are_rejected() {
    let executable = b"genuine executable".to_vec();

    let short = FakeFetch::default().with(&download_url(), &executable[..executable.len() - 1]);
    assert_eq!(kind_of(stage(&executable, &short)), ErrorKind::SizeMismatch);

    let mut longer = executable.clone();
    longer.push(0);
    let long_with_header = FakeFetch::default().with(&download_url(), &longer);
    assert_eq!(
        kind_of(stage(&executable, &long_with_header)),
        ErrorKind::SizeMismatch
    );

    // Header claims the right size but the stream keeps going.
    let lying = FakeFetch::default().with_lying_length(
        &download_url(),
        Some(executable.len() as u64),
        &longer,
    );
    assert_eq!(kind_of(stage(&executable, &lying)), ErrorKind::SizeMismatch);

    // No header at all.
    let headerless = FakeFetch::default().with_lying_length(&download_url(), None, &longer);
    assert_eq!(
        kind_of(stage(&executable, &headerless)),
        ErrorKind::SizeMismatch
    );
}

#[test]
fn content_length_beyond_the_hard_cap_is_too_large() {
    let executable = b"genuine".to_vec();
    let fetch = FakeFetch::default().with_lying_length(
        &download_url(),
        Some(MAX_APP_EXECUTABLE_BYTES + 1),
        &executable,
    );
    assert_eq!(kind_of(stage(&executable, &fetch)), ErrorKind::TooLarge);
}

#[test]
fn download_http_errors_are_reported() {
    let executable = b"x".to_vec();
    let fetch = FakeFetch::default().with_status(&download_url(), 502);
    assert!(matches!(
        stage(&executable, &fetch).unwrap_err(),
        Error::Http(502)
    ));
}

#[test]
fn cancellation_stops_the_download() {
    let executable = vec![1_u8; 300_000];
    let fetch = FakeFetch::default().with(&download_url(), &executable);
    let available = available_for(&executable);

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
    manifest.download_url = "https://example.com/FLDailyEdit.exe".into();
    assert_eq!(kind_of(Available::new(manifest)), ErrorKind::UntrustedUrl);
}

// ------------------------------------------------------ release tooling

fn write_asset(directory: &Path, executable: &[u8]) -> PathBuf {
    let asset = directory.join(APP_EXECUTABLE_NAME);
    std::fs::write(&asset, executable).unwrap();
    asset
}

fn pem(key: &SigningKey) -> String {
    key.to_pkcs8_pem(LineEnding::LF).unwrap().to_string()
}

#[test]
fn build_sign_verify_parse_round_trip() {
    let directory = tempfile::tempdir().unwrap();
    let executable = b"rust gui exe".to_vec();
    let asset = write_asset(directory.path(), &executable);
    let manifest_path = directory.path().join("out/update.json");
    let signature_path = directory.path().join("out/update.json.sig");

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
    assert!(payload.ends_with(b"}\n"));
    assert!(payload.len() <= 64 * 1024);
    assert_eq!(signature.len(), 89, "88 base64 chars + newline");
    assert!(signature.ends_with(b"\n"));
    verify_with_key(&public_key(), &payload, &signature).unwrap();
    let parsed = Manifest::parse(&payload).unwrap();
    assert_eq!(parsed, built);
    assert_eq!(parsed.size, executable.len() as u64);
    assert_eq!(parsed.sha256, sha256_hex(&executable));
    assert_eq!(
        parsed.download_url,
        "https://github.com/gvoze32/fldailyedit/releases/download/v1.0.0/FLDailyEdit.exe"
    );
    let document: serde_json::Value = serde_json::from_slice(&payload).unwrap();
    assert_eq!(document.as_object().unwrap().len(), 5);

    // And the whole client path accepts it.
    let fetch = FakeFetch::default()
        .with(APP_UPDATE_MANIFEST_URL, &payload)
        .with(APP_UPDATE_SIGNATURE_URL, &signature)
        .with(&parsed.download_url, &executable);
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

    let wrong_name = directory.path().join("other.exe");
    std::fs::write(&wrong_name, b"exe").unwrap();
    assert_eq!(
        kind_of(build_manifest(&wrong_name, &output, "1.0.0")),
        ErrorKind::InvalidManifest
    );

    let asset = write_asset(directory.path(), b"exe");
    for bad_version in ["1.0", "v1.0.0", "1.0.0-beta", ""] {
        assert_eq!(
            kind_of(build_manifest(&asset, &output, bad_version)),
            ErrorKind::InvalidManifest,
            "{bad_version:?}"
        );
    }

    std::fs::write(&asset, b"").unwrap();
    assert_eq!(
        kind_of(build_manifest(&asset, &output, "1.0.0")),
        ErrorKind::InvalidManifest
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
    let manifest_path = directory.path().join("update.json");
    let signature_path = directory.path().join("update.json.sig");
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
