//! Fixed-width, NUL-terminated string fields shared by the binary formats.

/// Lossy UTF-8 decode with surrounding whitespace removed.
pub(crate) fn decode_trimmed(bytes: &[u8]) -> String {
    fl_text::strip(&String::from_utf8_lossy(bytes)).to_owned()
}

/// Text in `data[offset..offset + size]`, cut at the first NUL.
///
/// The caller guarantees the field lies inside `data`.
pub(crate) fn read_text(data: &[u8], offset: usize, size: usize) -> String {
    let field = &data[offset..offset + size];
    let end = field.iter().position(|&b| b == 0).unwrap_or(field.len());
    decode_trimmed(&field[..end])
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cuts_at_nul_and_trims() {
        assert_eq!(read_text(b"  ab c \0zz", 0, 10), "ab c");
        assert_eq!(read_text(b"\x1fab\x1c", 0, 4), "ab");
        assert_eq!(read_text(b"abc", 1, 2), "bc");
    }

    #[test]
    fn invalid_utf8_is_replaced() {
        assert_eq!(read_text(b"a\xffb", 0, 3), "a\u{fffd}b");
    }
}
