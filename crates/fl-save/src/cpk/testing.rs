//! In-memory CPK archive builder shared by tests.

use super::utf;

enum Cell<'a> {
    U32(u32),
    U64(u64),
    Str(&'a str),
}

/// Build an `@UTF` chunk (`@UTF` + size + table body) with per-row storage for
/// every column. `flags` are the raw descriptor bytes (storage | content type).
fn utf_chunk(columns: &[(&str, u8)], rows: &[Vec<Cell<'_>>]) -> Vec<u8> {
    let mut strings = vec![0u8]; // offset 0: empty table name
    let mut intern = |text: &str| {
        let offset = strings.len() as u32;
        strings.extend_from_slice(text.as_bytes());
        strings.push(0);
        offset
    };
    let mut descriptors = Vec::new();
    for (name, flags) in columns {
        descriptors.push(*flags);
        descriptors.extend_from_slice(&intern(name).to_be_bytes());
    }
    let mut row_bytes = Vec::new();
    for row in rows {
        for cell in row {
            match cell {
                Cell::U32(v) => row_bytes.extend_from_slice(&v.to_be_bytes()),
                Cell::U64(v) => row_bytes.extend_from_slice(&v.to_be_bytes()),
                Cell::Str(s) => row_bytes.extend_from_slice(&intern(s).to_be_bytes()),
            }
        }
    }
    let row_length = row_bytes.len().checked_div(rows.len()).unwrap_or(0);
    let rows_offset = 24 + descriptors.len();
    let strings_offset = rows_offset + row_bytes.len();
    let data_offset = strings_offset + strings.len();

    let mut table = Vec::new();
    for value in [
        rows_offset as u32,
        strings_offset as u32,
        data_offset as u32,
        0,
    ] {
        table.extend_from_slice(&value.to_be_bytes());
    }
    table.extend_from_slice(&(columns.len() as u16).to_be_bytes());
    table.extend_from_slice(&(row_length as u16).to_be_bytes());
    table.extend_from_slice(&(rows.len() as u32).to_be_bytes());
    table.extend(descriptors);
    table.extend(row_bytes);
    table.extend(strings);

    let mut chunk = b"@UTF".to_vec();
    chunk.extend_from_slice(&(table.len() as u32).to_be_bytes());
    chunk.extend(table);
    chunk
}

const U32: u8 = 0x54; // per-row u32
const U64: u8 = 0x56; // per-row u64
const STR: u8 = 0x5A; // per-row string

pub(crate) struct Entry<'a> {
    pub dir: &'a str,
    pub name: &'a str,
    pub payload: &'a [u8],
}

/// Archive layout: `CPK ` header chunk, `TOC ` chunk, 0x800-aligned content.
/// File offsets are relative to `content_offset`.
pub(crate) fn build_cpk(
    entries: &[Entry<'_>],
    toc_offset_cell: Option<u64>,
    protected_toc: bool,
) -> Vec<u8> {
    const CONTENT: u64 = 0x800;
    let mut offsets = Vec::new();
    let mut cursor = 0u64;
    for entry in entries {
        offsets.push(cursor);
        cursor += entry.payload.len() as u64;
    }

    let toc_columns = [
        ("DirName", STR),
        ("FileName", STR),
        ("FileSize", U32),
        ("ExtractSize", U32),
        ("FileOffset", U64),
    ];
    let toc_rows: Vec<Vec<Cell<'_>>> = entries
        .iter()
        .zip(&offsets)
        .map(|(entry, offset)| {
            vec![
                Cell::Str(entry.dir),
                Cell::Str(entry.name),
                Cell::U32(entry.payload.len() as u32),
                Cell::U32(entry.payload.len() as u32),
                Cell::U64(*offset),
            ]
        })
        .collect();
    let mut toc_utf = utf_chunk(&toc_columns, &toc_rows);
    if protected_toc {
        // Rewrite as the 8-column / 32-byte-row compact shape with an unreadable schema.
        let strings_rows: Vec<Vec<Cell<'_>>> = entries
            .iter()
            .zip(&offsets)
            .map(|(entry, offset)| {
                vec![
                    Cell::Str(entry.dir),
                    Cell::Str(entry.name),
                    Cell::U32(entry.payload.len() as u32),
                    Cell::U32(entry.payload.len() as u32),
                    Cell::U64(*offset),
                    Cell::U32(7),
                    Cell::U32(0),
                ]
            })
            .collect();
        let columns = [
            ("a", 0x20 | 4), // storage 0x20 is unsupported by the standard reader
            ("b", U32),
            ("c", U32),
            ("d", U32),
            ("e", U64),
            ("f", U32),
            ("g", U32),
            ("h", U32),
        ];
        toc_utf = utf_chunk(&columns, &strings_rows);
        // 7 cells/row above = 4+4+4+4+8+4+4 = 32 bytes; column count must be 8.
        assert_eq!(utf::table_header(&toc_utf[8..]).unwrap().row_length, 32);
    }

    // Header chunk first so we know where the TOC lands.
    let header_for = |toc_offset: u64| {
        let toc_cell = toc_offset_cell.unwrap_or(toc_offset);
        utf_chunk(
            &[("ContentOffset", U32), ("TocOffset", U64)],
            &[vec![Cell::U32(CONTENT as u32), Cell::U64(toc_cell)]],
        )
    };
    let header_len = header_for(0).len() as u64;
    let toc_offset = 16 + header_len + 16; // keep a gap; marker must be findable
    let toc_offset = toc_offset.next_multiple_of(16);

    let mut archive = vec![0u8; CONTENT as usize];
    archive[..4].copy_from_slice(b"CPK ");
    let header = header_for(toc_offset);
    archive[16..16 + header.len()].copy_from_slice(&header);
    archive[toc_offset as usize..toc_offset as usize + 4].copy_from_slice(b"TOC ");
    let toc_end = toc_offset as usize + 16 + toc_utf.len();
    assert!(toc_end <= CONTENT as usize, "test archive TOC too large");
    archive[toc_offset as usize + 16..toc_end].copy_from_slice(&toc_utf);
    for entry in entries {
        archive.extend_from_slice(entry.payload);
    }
    archive
}
