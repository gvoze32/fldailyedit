//! The 208-byte decrypted container header (`header.dat`): sizes of the other
//! blocks plus file-type / game-version strings.

/// Size of the decrypted file header block.
pub const FILE_HEADER_SIZE: usize = 208;
pub(crate) const DATA_SIZE_OFFSET: usize = 64;
pub(crate) const LOGO_SIZE_OFFSET: usize = 68;
pub(crate) const DESCRIPTION_SIZE_OFFSET: usize = 72;
pub(crate) const SERIAL_LENGTH_OFFSET: usize = 76;
const FILE_TYPE_OFFSET: usize = 144;
const GAME_VERSION_OFFSET: usize = 176;
const FIXED_STRING_SIZE: usize = 32;

#[derive(Debug, thiserror::Error, PartialEq, Eq)]
pub enum HeaderError {
    #[error("save header must contain at least {FILE_HEADER_SIZE} bytes; got {0}")]
    TooShort(usize),
}

/// Stable metadata identifying the game family of an EDIT container.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SaveHeader {
    pub file_type: String,
    pub game_version: String,
    pub data_size: u32,
    pub logo_size: u32,
    pub description_size: u32,
    pub serial_length: u32,
}

impl SaveHeader {
    /// Parse a decrypted `header.dat` block (extra trailing bytes are ignored).
    pub fn parse(raw: &[u8]) -> Result<Self, HeaderError> {
        if raw.len() < FILE_HEADER_SIZE {
            return Err(HeaderError::TooShort(raw.len()));
        }
        Ok(Self {
            file_type: fixed_string(raw, FILE_TYPE_OFFSET),
            game_version: fixed_string(raw, GAME_VERSION_OFFSET),
            data_size: read_u32(raw, DATA_SIZE_OFFSET),
            logo_size: read_u32(raw, LOGO_SIZE_OFFSET),
            description_size: read_u32(raw, DESCRIPTION_SIZE_OFFSET),
            serial_length: read_u32(raw, SERIAL_LENGTH_OFFSET),
        })
    }

    /// Whether the container identifies the vanilla PES 2021 save format.
    pub fn is_pes21(&self) -> bool {
        self.game_version.to_lowercase().contains("pes 2021")
    }
}

pub(crate) fn read_u32(raw: &[u8], offset: usize) -> u32 {
    u32::from_le_bytes([
        raw[offset],
        raw[offset + 1],
        raw[offset + 2],
        raw[offset + 3],
    ])
}

fn fixed_string(raw: &[u8], offset: usize) -> String {
    crate::binstr::read_text(raw, offset, FIXED_STRING_SIZE)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn header(version: &[u8], file_type: &[u8]) -> Vec<u8> {
        let mut raw = vec![0u8; FILE_HEADER_SIZE];
        raw[DATA_SIZE_OFFSET..][..4].copy_from_slice(&10_995_800u32.to_le_bytes());
        raw[LOGO_SIZE_OFFSET..][..4].copy_from_slice(&7u32.to_le_bytes());
        raw[DESCRIPTION_SIZE_OFFSET..][..4].copy_from_slice(&9u32.to_le_bytes());
        raw[SERIAL_LENGTH_OFFSET..][..4].copy_from_slice(&3u32.to_le_bytes());
        raw[FILE_TYPE_OFFSET..][..file_type.len()].copy_from_slice(file_type);
        raw[GAME_VERSION_OFFSET..][..version.len()].copy_from_slice(version);
        raw
    }

    #[test]
    fn parses_fields_and_detects_pes21() {
        let parsed = SaveHeader::parse(&header(b"  PES 2021 \0junk", b"EDIT")).unwrap();
        assert_eq!(parsed.game_version, "PES 2021");
        assert_eq!(parsed.file_type, "EDIT");
        assert_eq!(parsed.data_size, 10_995_800);
        assert_eq!(parsed.logo_size, 7);
        assert_eq!(parsed.description_size, 9);
        assert_eq!(parsed.serial_length, 3);
        assert!(parsed.is_pes21());
    }

    #[test]
    fn other_versions_are_not_pes21() {
        let parsed = SaveHeader::parse(&header(b"eFootball PES 2020", b"EDIT")).unwrap();
        assert!(!parsed.is_pes21());
        let blank = SaveHeader::parse(&header(b"", b"")).unwrap();
        assert!(!blank.is_pes21());
    }

    #[test]
    fn rejects_short_header() {
        assert_eq!(
            SaveHeader::parse(&[0u8; 207]),
            Err(HeaderError::TooShort(207))
        );
    }
}
