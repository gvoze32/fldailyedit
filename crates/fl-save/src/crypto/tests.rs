use super::*;
use base64::Engine as _;
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::path::PathBuf;

fn sha(bytes: &[u8]) -> String {
    Sha256::digest(bytes)
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..")
}

/// The C-oracle fixtures (local `testdata/`); `None` when absent.
fn fixtures() -> Option<Value> {
    let raw = crate::test_support::local_bytes("testdata/crypto/fixtures.json")?;
    Some(serde_json::from_slice(&raw).unwrap())
}

fn b64(value: &Value) -> Vec<u8> {
    base64::engine::general_purpose::STANDARD
        .decode(value.as_str().unwrap())
        .unwrap()
}

fn block_list(blocks: &Blocks) -> [(&'static str, &[u8]); 6] {
    [
        ("encryptHeader.dat", &blocks.encryption_header),
        ("header.dat", &blocks.header),
        ("description.dat", &blocks.description),
        ("logo.png", &blocks.logo),
        ("data.dat", &blocks.data),
        ("version.txt", &blocks.serial),
    ]
}

/// Compare one save against the C pesXdecrypter/encrypter21 fixtures.
fn check_save(relative: &str, input: Vec<u8>, entry: &Value, verified: bool) {
    assert_eq!(
        sha(&input),
        entry["input_sha256"].as_str().unwrap(),
        "{relative}: input differs from fixture"
    );

    let mut blocks = decrypt(&input).unwrap();
    for (name, bytes) in block_list(&blocks) {
        let expected = &entry["blocks"][name];
        assert_eq!(
            bytes.len() as u64,
            expected["size"].as_u64().unwrap(),
            "{relative}: {name} size"
        );
        assert_eq!(
            sha(bytes),
            expected["sha256"].as_str().unwrap(),
            "{relative}: {name} sha256"
        );
    }

    // The scheme is deterministic: untouched blocks re-encrypt to the input, as with the C encrypter.
    let reencrypted = encrypt(&blocks).unwrap();
    assert_eq!(
        sha(&reencrypted),
        entry["reencrypt_sha256"].as_str().unwrap(),
        "{relative}"
    );
    assert_eq!(reencrypted, input, "{relative}: encrypt(decrypt(x)) != x");

    // Edited data.dat matches the C encrypter output.
    for edit in entry["edited"]["data_edits"].as_array().unwrap() {
        let offset = edit[0].as_i64().unwrap();
        let index = if offset < 0 {
            blocks.data.len() - (-offset) as usize
        } else {
            offset as usize
        };
        blocks.data[index] ^= edit[1].as_u64().unwrap() as u8;
    }
    let edited = if verified {
        encrypt_verified(&blocks)
    } else {
        encrypt(&blocks)
    }
    .unwrap();
    assert_eq!(
        edited.len() as u64,
        entry["edited"]["encrypted_size"].as_u64().unwrap()
    );
    assert_eq!(
        sha(&edited),
        entry["edited"]["encrypted_sha256"].as_str().unwrap(),
        "{relative}: edited"
    );
}

#[test]
fn base_save_matches_c_oracle() {
    let Some(input) = crate::test_support::base_save_bytes() else {
        return;
    };
    let Some(fixtures) = fixtures() else {
        return;
    };
    let relative = "testdata/local/base/EDIT00000000";
    let entry = &fixtures["saves"][relative];
    assert!(entry.is_object(), "base save missing from fixtures");
    check_save(relative, input, entry, true);
}

#[test]
fn reference_saves_match_c_oracle() {
    let Some(fixtures) = fixtures() else {
        return;
    };
    let mut checked = 0;
    for (relative, entry) in fixtures["saves"].as_object().unwrap() {
        if relative.starts_with("reference/") && repo_root().join(relative).is_file() {
            let input = std::fs::read(repo_root().join(relative)).unwrap();
            check_save(relative, input, entry, false);
            checked += 1;
        }
    }
    eprintln!("reference saves compared with C oracle: {checked}");
}

#[test]
fn synthetic_odd_sizes_match_c_oracle() {
    let Some(fixtures) = fixtures() else {
        return;
    };
    let cases = fixtures["synthetic"].as_array().unwrap();
    assert!(!cases.is_empty());
    for case in cases {
        let sizes: Vec<usize> = case["sizes"]
            .as_array()
            .unwrap()
            .iter()
            .map(|s| s.as_u64().unwrap() as usize)
            .collect();
        let pattern = |len: usize, seed: usize| -> Vec<u8> {
            (0..len).map(|i| ((i * 31 + seed) % 256) as u8).collect()
        };
        let blocks = Blocks {
            encryption_header: b64(&case["encryption_header_b64"]).try_into().unwrap(),
            header: b64(&case["header_b64"]).try_into().unwrap(),
            description: pattern(sizes[0], 1),
            logo: pattern(sizes[1], 2),
            data: pattern(sizes[2], 3),
            serial: pattern(sizes[3], 4),
        };
        let encrypted = encrypt(&blocks).unwrap();
        assert_eq!(encrypted, b64(&case["encrypted_b64"]), "sizes {sizes:?}");

        let back = decrypt(&encrypted).unwrap();
        assert_eq!(back.header.to_vec(), b64(&case["effective_header_b64"]));
        assert_eq!(back.description, blocks.description);
        assert_eq!(back.logo, blocks.logo);
        assert_eq!(back.data, blocks.data);
        assert_eq!(back.serial, blocks.serial);
        assert_eq!(back.save_header().data_size as usize, sizes[2]);
        assert_eq!(back.save_header().serial_length as usize * 2, sizes[3]);
    }
}

fn small_blocks() -> Option<Blocks> {
    let fixtures = fixtures()?;
    let case = &fixtures["synthetic"][0];
    Some(Blocks {
        encryption_header: b64(&case["encryption_header_b64"]).try_into().unwrap(),
        header: b64(&case["header_b64"]).try_into().unwrap(),
        description: vec![1; 7],
        logo: vec![2; 13],
        data: vec![3; 1001],
        serial: vec![4; 6],
    })
}

#[test]
fn truncated_or_inconsistent_input_is_an_error_not_a_panic() {
    let Some(blocks) = small_blocks() else {
        return;
    };
    let encrypted = encrypt(&blocks).unwrap();
    for len in [0, 100, PREFIX_SIZE - 1, PREFIX_SIZE, encrypted.len() - 1] {
        assert!(
            matches!(
                decrypt(&encrypted[..len]),
                Err(CryptoError::Truncated { .. })
            ),
            "len {len}"
        );
    }
    assert!(decrypt(&encrypted).is_ok());
    // Corrupting the encrypted file header scrambles the sizes: must not panic or over-read.
    let mut scrambled = encrypted.clone();
    for byte in &mut scrambled[ENCRYPTION_HEADER_SIZE..PREFIX_SIZE] {
        *byte ^= 0xA5;
    }
    let _ = decrypt(&scrambled);
}

#[test]
fn encrypt_rejects_odd_serial_and_rewrites_header_sizes() {
    let Some(mut blocks) = small_blocks() else {
        return;
    };
    blocks.serial.push(0);
    assert!(matches!(
        encrypt(&blocks),
        Err(CryptoError::OddSerialLength(7))
    ));

    blocks.serial.pop();
    blocks.data.extend_from_slice(&[9; 10]);
    let back = decrypt(&encrypt_verified(&blocks).unwrap()).unwrap();
    assert_eq!(back.data.len(), 1011);
    assert_eq!(back.save_header().data_size, 1011);
}

#[test]
fn round_trip_verification_reports_the_failing_block() {
    let Some(blocks) = small_blocks() else {
        return;
    };
    let mut encrypted = encrypt(&blocks).unwrap();
    assert!(verify_round_trip(&blocks, &encrypted).is_ok());

    // Flip a ciphertext byte inside the data block: it decrypts to different data.
    let data_start = PREFIX_SIZE + blocks.description.len() + blocks.logo.len();
    encrypted[data_start + 5] ^= 1;
    assert!(matches!(
        verify_round_trip(&blocks, &encrypted),
        Err(CryptoError::RoundTripMismatch("data.dat"))
    ));
}

#[test]
fn encrypt_to_file_is_atomic_and_verified() {
    let dir = tempfile::tempdir().unwrap();
    let target = dir.path().join("out").join("EDIT00000000");
    let Some(blocks) = small_blocks() else {
        return;
    };
    encrypt_to_file(&blocks, &target).unwrap();
    assert_eq!(std::fs::read(&target).unwrap(), encrypt(&blocks).unwrap());

    let mut odd = blocks;
    odd.serial.push(1);
    let before = std::fs::read(&target).unwrap();
    assert!(encrypt_to_file(&odd, &target).is_err());
    assert_eq!(
        std::fs::read(&target).unwrap(),
        before,
        "failed encrypt must not touch the target"
    );
}
