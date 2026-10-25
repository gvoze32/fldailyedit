//! The small WESYS/zlib wrapper used by the game's database files.

use flate2::read::ZlibDecoder;
use std::borrow::Cow;
use std::io::Read;

/// Size of the WESYS header: 3 opaque bytes, `WESYS`, compressed size,
/// expected decompressed size (both little-endian u32).
pub const WESYS_HEADER_SIZE: usize = 16;
const WESYS_MAGIC: &[u8; 5] = b"WESYS";

#[derive(Debug, thiserror::Error, PartialEq, Eq)]
pub enum WesysError {
    #[error("{label} WESYS header is truncated")]
    TruncatedHeader { label: &'static str },
    #[error("{label} WESYS compressed payload is truncated")]
    TruncatedPayload { label: &'static str },
    #[error("{label} WESYS payload is not valid zlib data")]
    InvalidZlib { label: &'static str },
    #[error("{label} decompressed size is {actual}; expected {expected}")]
    SizeMismatch {
        label: &'static str,
        actual: usize,
        expected: usize,
    },
}

/// Return the payload of a WESYS/zlib container, or `raw` itself (borrowed)
/// when it is not wrapped.
pub fn decompress_wesys<'a>(
    raw: &'a [u8],
    label: &'static str,
) -> Result<Cow<'a, [u8]>, WesysError> {
    if raw.len() < 8 || raw[3..8] != WESYS_MAGIC[..] {
        return Ok(Cow::Borrowed(raw));
    }
    if raw.len() < WESYS_HEADER_SIZE {
        return Err(WesysError::TruncatedHeader { label });
    }
    let word = |offset: usize| {
        u32::from_le_bytes([
            raw[offset],
            raw[offset + 1],
            raw[offset + 2],
            raw[offset + 3],
        ]) as usize
    };
    let compressed_size = word(8);
    let expected_size = word(12);
    let compressed_end = WESYS_HEADER_SIZE + compressed_size;
    if compressed_size == 0 || compressed_end > raw.len() {
        return Err(WesysError::TruncatedPayload { label });
    }

    let mut result = Vec::with_capacity(expected_size);
    ZlibDecoder::new(&raw[WESYS_HEADER_SIZE..compressed_end])
        .read_to_end(&mut result)
        .map_err(|_| WesysError::InvalidZlib { label })?;
    if expected_size != 0 && result.len() != expected_size {
        return Err(WesysError::SizeMismatch {
            label,
            actual: result.len(),
            expected: expected_size,
        });
    }
    Ok(Cow::Owned(result))
}

#[cfg(test)]
mod tests {
    use super::*;
    use flate2::{Compression, write::ZlibEncoder};
    use std::io::Write;

    fn wrap(payload: &[u8], expected: u32) -> Vec<u8> {
        let mut encoder = ZlibEncoder::new(Vec::new(), Compression::default());
        encoder.write_all(payload).unwrap();
        let compressed = encoder.finish().unwrap();
        let mut out = vec![0u8, 0, 0];
        out.extend_from_slice(WESYS_MAGIC);
        out.extend_from_slice(&(compressed.len() as u32).to_le_bytes());
        out.extend_from_slice(&expected.to_le_bytes());
        out.extend_from_slice(&compressed);
        out
    }

    #[test]
    fn unwrapped_data_is_returned_as_is() {
        let raw = b"plain database bytes";
        assert!(matches!(decompress_wesys(raw, "X"), Ok(Cow::Borrowed(b)) if b == raw));
        assert!(matches!(
            decompress_wesys(b"short", "X"),
            Ok(Cow::Borrowed(_))
        ));
    }

    #[test]
    fn unwraps_zlib_payload() {
        let payload: Vec<u8> = (0..5000u32).map(|i| (i % 251) as u8).collect();
        let wrapped = wrap(&payload, payload.len() as u32);
        assert_eq!(decompress_wesys(&wrapped, "X").unwrap(), payload);
        // expected size 0 disables the size check
        assert_eq!(decompress_wesys(&wrap(&payload, 0), "X").unwrap(), payload);
    }

    #[test]
    fn detects_corruption() {
        let payload = vec![7u8; 1000];
        let wrapped = wrap(&payload, 999);
        assert_eq!(
            decompress_wesys(&wrapped, "Player.bin"),
            Err(WesysError::SizeMismatch {
                label: "Player.bin",
                actual: 1000,
                expected: 999
            })
        );
        let truncated = &wrap(&payload, 1000)[..20];
        assert_eq!(
            decompress_wesys(truncated, "X"),
            Err(WesysError::TruncatedPayload { label: "X" })
        );
        assert_eq!(
            decompress_wesys(&wrap(&payload, 1000)[..12], "X"),
            Err(WesysError::TruncatedHeader { label: "X" })
        );
        let mut garbage = wrap(&payload, 1000);
        garbage[WESYS_HEADER_SIZE + 4] ^= 0xFF;
        garbage[WESYS_HEADER_SIZE + 5] ^= 0xFF;
        assert_eq!(
            decompress_wesys(&garbage, "X"),
            Err(WesysError::InvalidZlib { label: "X" })
        );
    }
}
