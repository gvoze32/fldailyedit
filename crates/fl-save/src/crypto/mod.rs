//! Port of the pesXdecrypter C implementation (see THIRD_PARTY_NOTICES.md; PES 2021 master key) operating on memory.
//!
//! An EDIT file is `encryption header (320) | file header (208) | description |
//! logo | data | serial`, each part stream-encrypted with an MT19937 keystream.
//! [`decrypt`] splits a file into [`Blocks`]; [`encrypt`] is its exact inverse
//! and is deterministic (no randomness anywhere in the scheme), so
//! `encrypt(decrypt(f)) == f` for an untouched file.

mod mt19937;

use crate::header::{
    DATA_SIZE_OFFSET, DESCRIPTION_SIZE_OFFSET, FILE_HEADER_SIZE, LOGO_SIZE_OFFSET,
    SERIAL_LENGTH_OFFSET, read_u32,
};
use mt19937::Mt19937;
use std::path::Path;

/// Size of the leading encryption header.
pub const ENCRYPTION_HEADER_SIZE: usize = 320;
const KEY_SIZE: usize = 64;
const PREFIX_SIZE: usize = ENCRYPTION_HEADER_SIZE + FILE_HEADER_SIZE;

/// PES 2021 regular edition master key (`MasterKeyPes21`).
const MASTER_KEY: [u8; KEY_SIZE] = [
    0x90, 0x61, 0xD8, 0x66, 0x43, 0x77, 0x24, 0xF8, 0x92, 0xBA, 0xB8, 0x71, 0x21, 0xC7, 0x60, 0x63,
    0xF0, 0x91, 0x9A, 0x7D, 0xED, 0x47, 0x80, 0xDE, 0x51, 0xF5, 0xDD, 0xD1, 0x08, 0xFE, 0x32, 0x84,
    0xF5, 0x09, 0x92, 0x00, 0xB2, 0x3E, 0x88, 0x9F, 0xEB, 0x24, 0x43, 0x05, 0x58, 0x76, 0x00, 0x22,
    0x9B, 0xFE, 0xEC, 0xF6, 0x50, 0x00, 0x29, 0xD3, 0x42, 0x75, 0x50, 0xB9, 0xEC, 0xD2, 0xF6, 0x75,
];

#[derive(Debug, thiserror::Error)]
pub enum CryptoError {
    #[error("encrypted file is truncated: need at least {needed} bytes, got {actual}")]
    Truncated { needed: u64, actual: usize },
    #[error("serial block must have an even length (2 bytes per serial character); got {0}")]
    OddSerialLength(usize),
    #[error("{name} block of {len} bytes does not fit the 32-bit size field")]
    BlockTooLarge { name: &'static str, len: usize },
    #[error("round-trip verification failed for block: {0}")]
    RoundTripMismatch(&'static str),
    #[error(transparent)]
    Io(#[from] std::io::Error),
}

/// The decrypted logical parts of an EDIT file. `data` is the game database
/// edited by the rest of the application; the other parts are carried through
/// untouched.
#[derive(Clone, PartialEq, Eq)]
pub struct Blocks {
    /// `encryptHeader.dat`: key material for the remaining parts.
    pub encryption_header: [u8; ENCRYPTION_HEADER_SIZE],
    /// `header.dat`; its size fields are rewritten from the block lengths on
    /// [`encrypt`], like the C encrypter does.
    pub header: [u8; FILE_HEADER_SIZE],
    /// `description.dat`.
    pub description: Vec<u8>,
    /// `logo.png`.
    pub logo: Vec<u8>,
    /// `data.dat`.
    pub data: Vec<u8>,
    /// `version.txt` (UTF-16 serial, two bytes per character).
    pub serial: Vec<u8>,
}

impl std::fmt::Debug for Blocks {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Blocks")
            .field("description", &self.description.len())
            .field("logo", &self.logo.len())
            .field("data", &self.data.len())
            .field("serial", &self.serial.len())
            .finish_non_exhaustive()
    }
}

impl Blocks {
    /// `header` with the size fields set from the block lengths.
    fn synced_header(&self) -> Result<[u8; FILE_HEADER_SIZE], CryptoError> {
        if !self.serial.len().is_multiple_of(2) {
            return Err(CryptoError::OddSerialLength(self.serial.len()));
        }
        let size = |name, len: usize| {
            u32::try_from(len).map_err(|_| CryptoError::BlockTooLarge { name, len })
        };
        let mut header = self.header;
        let mut put = |offset: usize, value: u32| {
            header[offset..offset + 4].copy_from_slice(&value.to_le_bytes());
        };
        put(DATA_SIZE_OFFSET, size("data", self.data.len())?);
        put(LOGO_SIZE_OFFSET, size("logo", self.logo.len())?);
        put(
            DESCRIPTION_SIZE_OFFSET,
            size("description", self.description.len())?,
        );
        put(SERIAL_LENGTH_OFFSET, size("serial", self.serial.len() / 2)?);
        Ok(header)
    }

    /// Parsed view of [`Blocks::header`].
    pub fn save_header(&self) -> crate::header::SaveHeader {
        crate::header::SaveHeader::parse(&self.header)
            .expect("header is exactly FILE_HEADER_SIZE bytes")
    }
}

/// Decrypt a whole EDIT file.
pub fn decrypt(input: &[u8]) -> Result<Blocks, CryptoError> {
    if input.len() < PREFIX_SIZE {
        return Err(CryptoError::Truncated {
            needed: PREFIX_SIZE as u64,
            actual: input.len(),
        });
    }
    let (encrypted_header, rest) = input.split_at(ENCRYPTION_HEADER_SIZE);

    let encryption_header = crypt_header(encrypted_header);
    let rolling = rolling_key(&encryption_header);

    let (encrypted_file_header, mut rest) = rest.split_at(FILE_HEADER_SIZE);
    let mut header = [0u8; FILE_HEADER_SIZE];
    crypt_stream(
        &section_key(&rolling, FILE_HEADER_SIZE as u64),
        encrypted_file_header,
        &mut header,
    );

    let data_size = read_u32(&header, DATA_SIZE_OFFSET) as usize;
    let logo_size = read_u32(&header, LOGO_SIZE_OFFSET) as usize;
    let description_size = read_u32(&header, DESCRIPTION_SIZE_OFFSET) as usize;
    let serial_size = read_u32(&header, SERIAL_LENGTH_OFFSET) as usize * 2;
    let needed = PREFIX_SIZE as u64
        + data_size as u64
        + logo_size as u64
        + description_size as u64
        + serial_size as u64;
    if needed > input.len() as u64 {
        return Err(CryptoError::Truncated {
            needed,
            actual: input.len(),
        });
    }

    let mut part = |param: u64, len: usize| {
        let (encrypted, tail) = rest.split_at(len);
        rest = tail;
        let mut out = vec![0u8; len];
        crypt_stream(&section_key(&rolling, param), encrypted, &mut out);
        out
    };
    let description = part(0, description_size);
    let logo = part(1, logo_size);
    let data = part(2, data_size);
    let serial = part(3, serial_size);

    Ok(Blocks {
        encryption_header,
        header,
        description,
        logo,
        data,
        serial,
    })
}

/// Encrypt blocks into the on-disk EDIT file layout.
pub fn encrypt(blocks: &Blocks) -> Result<Vec<u8>, CryptoError> {
    let header = blocks.synced_header()?;
    let total = PREFIX_SIZE
        + blocks.description.len()
        + blocks.logo.len()
        + blocks.data.len()
        + blocks.serial.len();
    let mut output = vec![0u8; total];

    let (header_out, mut rest) = output.split_at_mut(ENCRYPTION_HEADER_SIZE);
    header_out.copy_from_slice(&crypt_header(&blocks.encryption_header));

    let rolling = rolling_key(&blocks.encryption_header);
    let (file_header_out, tail) = std::mem::take(&mut rest).split_at_mut(FILE_HEADER_SIZE);
    rest = tail;
    crypt_stream(
        &section_key(&rolling, FILE_HEADER_SIZE as u64),
        &header,
        file_header_out,
    );

    let mut part = |param: u64, plain: &[u8]| {
        let (out, tail) = std::mem::take(&mut rest).split_at_mut(plain.len());
        rest = tail;
        crypt_stream(&section_key(&rolling, param), plain, out);
    };
    part(0, &blocks.description);
    part(1, &blocks.logo);
    part(2, &blocks.data);
    part(3, &blocks.serial);
    Ok(output)
}

/// [`encrypt`], then decrypt the result and require every block to survive.
/// Catches key, packaging and truncation faults before anything is published.
pub fn encrypt_verified(blocks: &Blocks) -> Result<Vec<u8>, CryptoError> {
    let encrypted = encrypt(blocks)?;
    verify_round_trip(blocks, &encrypted)?;
    Ok(encrypted)
}

/// Require `encrypted` to decrypt back to exactly `blocks` (with the header
/// sizes the encrypter writes).
pub fn verify_round_trip(blocks: &Blocks, encrypted: &[u8]) -> Result<(), CryptoError> {
    let back = decrypt(encrypted)?;
    let header = blocks.synced_header()?;
    let checks: [(&'static str, bool); 6] = [
        (
            "encryptHeader.dat",
            back.encryption_header == blocks.encryption_header,
        ),
        ("header.dat", back.header == header),
        ("description.dat", back.description == blocks.description),
        ("logo.png", back.logo == blocks.logo),
        ("data.dat", back.data == blocks.data),
        ("version.txt", back.serial == blocks.serial),
    ];
    match checks.iter().find(|(_, ok)| !ok) {
        Some((name, _)) => Err(CryptoError::RoundTripMismatch(name)),
        None => Ok(()),
    }
}

/// [`encrypt_verified`] and publish atomically (temp sibling, fsync, rename).
/// An existing `path` is replaced only after verification succeeded.
pub fn encrypt_to_file(blocks: &Blocks, path: &Path) -> Result<(), CryptoError> {
    let encrypted = encrypt_verified(blocks)?;
    fl_fs::write_atomic(path, &encrypted)?;
    Ok(())
}

/// Decrypt the 320-byte encryption header (also its own inverse): bytes
/// 256..320 stay in clear and, mixed with the master key, seed the keystream
/// for the rest.
fn crypt_header(input: &[u8]) -> [u8; ENCRYPTION_HEADER_SIZE] {
    debug_assert_eq!(input.len(), ENCRYPTION_HEADER_SIZE);
    let mut header_key = [0u8; KEY_SIZE];
    header_key.copy_from_slice(&input[256..]);
    // reverseLongs(masterKey): reverse the bytes of every 8-byte group.
    let mut shuffled = MASTER_KEY;
    for group in shuffled.as_chunks_mut::<8>().0 {
        group.reverse();
    }
    xor_repeating(&mut header_key, &shuffled);

    let mut output = [0u8; ENCRYPTION_HEADER_SIZE];
    crypt_stream(&header_key, input, &mut output);
    output[256..].copy_from_slice(&input[256..]);
    output
}

/// `rollingKey`: first 64 bytes of the encryption header folded with the
/// following 256 bytes.
fn rolling_key(encryption_header: &[u8; ENCRYPTION_HEADER_SIZE]) -> [u8; KEY_SIZE] {
    let mut rolling = [0u8; KEY_SIZE];
    rolling.copy_from_slice(&encryption_header[..KEY_SIZE]);
    xor_repeating(&mut rolling, &encryption_header[KEY_SIZE..]);
    rolling
}

fn xor_repeating(output: &mut [u8; KEY_SIZE], input: &[u8]) {
    for (i, byte) in input.iter().enumerate() {
        output[i & (KEY_SIZE - 1)] ^= byte;
    }
}

/// `xorWithLongParam`: every little-endian u64 of the rolling key xor `param`.
fn section_key(rolling: &[u8; KEY_SIZE], param: u64) -> [u8; KEY_SIZE] {
    let mut key = [0u8; KEY_SIZE];
    for (out, word) in key
        .as_chunks_mut::<8>()
        .0
        .iter_mut()
        .zip(rolling.as_chunks::<8>().0)
    {
        *out = (u64::from_le_bytes(*word) ^ param).to_le_bytes();
    }
    key
}

/// `cryptStream`: xor with a keystream derived from MT19937 seeded by `key`.
/// Symmetric, so it both encrypts and decrypts. `output.len() == input.len()`.
fn crypt_stream(key: &[u8; KEY_SIZE], input: &[u8], output: &mut [u8]) {
    debug_assert_eq!(input.len(), output.len());
    let mut words = [0u32; KEY_SIZE / 4];
    for (word, chunk) in words.iter_mut().zip(key.as_chunks::<4>().0) {
        *word = u32::from_le_bytes(*chunk);
    }
    let mut mt = Mt19937::from_key(&words);
    let mut c0 = mt.next_u32();
    let mut c1 = mt.next_u32();
    let mut c2 = mt.next_u32();
    let mut c3 = mt.next_u32();

    let (in_words, in_tail) = input.as_chunks::<4>();
    let (out_words, out_tail) = output.as_chunks_mut::<4>();
    for (src, dst) in in_words.iter().zip(out_words) {
        let c4 = mt.next_u32();
        *dst = (c4 ^ c3 ^ c2 ^ c1 ^ c0 ^ u32::from_le_bytes(*src)).to_le_bytes();
        c0 = c1.rotate_right(15);
        c1 = c2.rotate_left(11);
        c2 = c3.rotate_left(7);
        c3 = c4.rotate_right(13);
    }

    if !in_tail.is_empty() {
        let mut rest = [0u8; 4];
        rest[..in_tail.len()].copy_from_slice(in_tail);
        let value = u32::from_le_bytes(rest) ^ mt.next_u32() ^ c3 ^ c2 ^ c1 ^ c0;
        out_tail.copy_from_slice(&value.to_le_bytes()[..in_tail.len()]);
    }
}

#[cfg(test)]
mod tests;
