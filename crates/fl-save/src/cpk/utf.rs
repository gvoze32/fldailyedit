//! CRI `@UTF` tables as found in CPK headers and table-of-contents chunks.

use super::{CpkError, require_bounds};

/// One cell of an `@UTF` table.
#[derive(Debug, Clone, PartialEq)]
pub(super) enum Value {
    /// Column declared without storage.
    None,
    Int(i128),
    Float(f64),
    Str(String),
    Bytes(Vec<u8>),
}

impl Value {
    /// Non-negative integer value (floats truncate toward zero).
    pub(super) fn as_u64(&self) -> Option<u64> {
        match self {
            Value::Int(value) => u64::try_from(*value).ok(),
            Value::Float(value) if *value >= 0.0 && *value < u64::MAX as f64 => Some(*value as u64),
            _ => None,
        }
    }

    /// Text of a string cell; any other kind reads as empty.
    pub(super) fn as_text(&self) -> &str {
        match self {
            Value::Str(text) => text,
            _ => "",
        }
    }
}

/// Decoded rows with their column names (last duplicate column wins on lookup).
#[derive(Debug, Default)]
pub(super) struct Table {
    columns: Vec<String>,
    rows: Vec<Vec<Value>>,
}

impl Table {
    pub(super) fn from_parts(columns: Vec<String>, rows: Vec<Vec<Value>>) -> Self {
        Self { columns, rows }
    }

    pub(super) fn len(&self) -> usize {
        self.rows.len()
    }

    pub(super) fn get(&self, row: usize, column: &str) -> Option<&Value> {
        let index = self.columns.iter().rposition(|name| name == column)?;
        self.rows[row].get(index)
    }
}

fn be_u16(data: &[u8], offset: usize) -> u16 {
    u16::from_be_bytes([data[offset], data[offset + 1]])
}

pub(super) fn be_u32(data: &[u8], offset: usize) -> u32 {
    u32::from_be_bytes([
        data[offset],
        data[offset + 1],
        data[offset + 2],
        data[offset + 3],
    ])
}

pub(super) fn be_u64(data: &[u8], offset: usize) -> u64 {
    let mut raw = [0u8; 8];
    raw.copy_from_slice(&data[offset..offset + 8]);
    u64::from_be_bytes(raw)
}

/// Strings are NUL-terminated inside the table's string pool.
pub(super) fn read_string(
    table: &[u8],
    strings_offset: usize,
    string_offset: usize,
    what: &str,
) -> Result<String, CpkError> {
    let start = strings_offset + string_offset;
    if start >= table.len() {
        return Err(CpkError::format(format!(
            "{what} string offset {string_offset} is out of bounds"
        )));
    }
    let end = table[start..].iter().position(|&b| b == 0).ok_or_else(|| {
        CpkError::format(format!(
            "{what} string at offset {string_offset} is unterminated"
        ))
    })?;
    Ok(String::from_utf8_lossy(&table[start..start + end]).into_owned())
}

/// Table header fields shared by the standard and the protected TOC readers.
pub(super) struct TableHeader {
    pub rows_offset: usize,
    pub strings_offset: usize,
    pub data_offset: usize,
    pub columns: usize,
    pub row_length: usize,
    pub rows: usize,
}

pub(super) fn table_header(table: &[u8]) -> Result<TableHeader, CpkError> {
    if table.len() < 24 {
        return Err(CpkError::format("@UTF table header is truncated"));
    }
    Ok(TableHeader {
        rows_offset: be_u32(table, 0) as usize,
        strings_offset: be_u32(table, 4) as usize,
        data_offset: be_u32(table, 8) as usize,
        columns: be_u16(table, 16) as usize,
        row_length: be_u16(table, 18) as usize,
        rows: be_u32(table, 20) as usize,
    })
}

/// Parse a standard `@UTF` table body (the bytes after the 8-byte `@UTF`+size prefix).
pub(super) fn parse(table: &[u8]) -> Result<Table, CpkError> {
    let header = table_header(table)?;
    for (name, value) in [
        ("rows", header.rows_offset),
        ("strings", header.strings_offset),
        ("data", header.data_offset),
    ] {
        if value > table.len() {
            return Err(CpkError::format(format!(
                "@UTF {name} offset {value} exceeds table size {}",
                table.len()
            )));
        }
    }

    struct Column {
        name: String,
        content_type: u8,
        storage: u8,
        constant: Value,
    }
    let mut columns: Vec<Column> = Vec::with_capacity(header.columns);
    let mut cursor = 24;
    for _ in 0..header.columns {
        require_bounds(table.len(), cursor, 5, "@UTF column descriptor")?;
        let flags = table[cursor];
        let name_offset = be_u32(table, cursor + 1) as usize;
        cursor += 5;
        let storage = flags & 0xF0;
        let content_type = flags & 0x0F;
        let mut constant = Value::None;
        if storage == 0x30 {
            let (value, next) = read_value(
                table,
                header.data_offset,
                header.strings_offset,
                cursor,
                content_type,
            )?;
            constant = value;
            cursor = next;
        } else if !matches!(storage, 0x00 | 0x10 | 0x50) {
            return Err(CpkError::format(format!(
                "Unsupported @UTF storage type 0x{storage:02X}"
            )));
        }
        columns.push(Column {
            name: read_string(table, header.strings_offset, name_offset, "@UTF")?,
            content_type,
            storage,
            constant,
        });
    }

    let mut rows = Vec::new();
    for row_number in 0..header.rows {
        let row_offset = header.rows_offset + row_number * header.row_length;
        require_bounds(table.len(), row_offset, header.row_length, "@UTF row")?;
        let mut cursor = row_offset;
        let mut row = Vec::with_capacity(columns.len());
        for column in &columns {
            row.push(match column.storage {
                0x00 => Value::None,
                0x10 => Value::Int(0),
                0x30 => column.constant.clone(),
                _ => {
                    let (value, next) = read_value(
                        table,
                        header.data_offset,
                        header.strings_offset,
                        cursor,
                        column.content_type,
                    )?;
                    cursor = next;
                    value
                }
            });
        }
        rows.push(row);
    }

    Ok(Table::from_parts(
        columns.into_iter().map(|c| c.name).collect(),
        rows,
    ))
}

fn read_value(
    table: &[u8],
    data_offset: usize,
    strings_offset: usize,
    offset: usize,
    content_type: u8,
) -> Result<(Value, usize), CpkError> {
    match content_type {
        10 => {
            require_bounds(table.len(), offset, 4, "@UTF string value")?;
            let string_offset = be_u32(table, offset) as usize;
            Ok((
                Value::Str(read_string(table, strings_offset, string_offset, "@UTF")?),
                offset + 4,
            ))
        }
        11 => {
            require_bounds(table.len(), offset, 8, "@UTF data value")?;
            let start = data_offset + be_u32(table, offset) as usize;
            let size = be_u32(table, offset + 4) as usize;
            require_bounds(table.len(), start, size, "@UTF data payload")?;
            Ok((
                Value::Bytes(table[start..start + size].to_vec()),
                offset + 8,
            ))
        }
        0..=9 => {
            let size = [1usize, 1, 2, 2, 4, 4, 8, 8, 4, 8][content_type as usize];
            require_bounds(table.len(), offset, size, "@UTF value")?;
            let raw = &table[offset..offset + size];
            let mut be = [0u8; 8];
            be[8 - size..].copy_from_slice(raw);
            let unsigned = u64::from_be_bytes(be);
            let sign_extend = |bits: u32| (unsigned << (64 - bits)) as i64 >> (64 - bits);
            let value = match content_type {
                0 | 2 | 4 | 6 => Value::Int(unsigned as i128),
                1 => Value::Int(sign_extend(8) as i128),
                3 => Value::Int(sign_extend(16) as i128),
                5 => Value::Int(sign_extend(32) as i128),
                7 => Value::Int(unsigned as i64 as i128),
                8 => Value::Float(f32::from_bits(unsigned as u32) as f64),
                _ => Value::Float(f64::from_bits(unsigned)),
            };
            Ok((value, offset + size))
        }
        other => Err(CpkError::format(format!(
            "Unsupported @UTF content type {other}"
        ))),
    }
}

/// Compact TOC used by PES CPK "mode 5" archives: the schema descriptor is
/// protected but the row layout is fixed (directory, filename, packed size,
/// extract size, 64-bit offset, ID, omitted user string, CRC).
pub(super) fn parse_protected_toc(table: &[u8]) -> Result<Table, CpkError> {
    let header = table_header(table)?;
    if (header.columns, header.row_length) != (8, 32) {
        return Err(CpkError::format(format!(
            "Unsupported protected CPK TOC shape: {} columns, {}-byte rows",
            header.columns, header.row_length
        )));
    }
    if header.strings_offset > table.len() || header.data_offset > table.len() {
        return Err(CpkError::format(
            "CPK mode 5 TOC string/data section is out of bounds",
        ));
    }

    let columns = [
        "DirName",
        "FileName",
        "FileSize",
        "ExtractSize",
        "FileOffset",
        "ID",
        "UserString",
        "CRC",
    ]
    .map(String::from)
    .to_vec();
    let mut rows = Vec::new();
    for row_number in 0..header.rows {
        let row_offset = header.rows_offset + row_number * header.row_length;
        require_bounds(table.len(), row_offset, 32, "CPK mode 5 TOC row")?;
        let string = |offset: usize| {
            read_string(
                table,
                header.strings_offset,
                be_u32(table, offset) as usize,
                "CPK mode 5 TOC",
            )
        };
        rows.push(vec![
            Value::Str(string(row_offset)?),
            Value::Str(string(row_offset + 4)?),
            Value::Int(be_u32(table, row_offset + 8) as i128),
            Value::Int(be_u32(table, row_offset + 12) as i128),
            Value::Int(be_u64(table, row_offset + 16) as i128),
            Value::Int(be_u32(table, row_offset + 24) as i128),
            Value::None,
            Value::Int(be_u32(table, row_offset + 28) as i128),
        ]);
    }
    Ok(Table::from_parts(columns, rows))
}
