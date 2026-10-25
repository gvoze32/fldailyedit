//! Reader for CRIWARE CPK archives as used by PES/Football Life
//! (`data_extra.cpk`, patch datapacks): `@UTF` header, table of contents
//! (standard or the protected "mode 5" shape) and stored (uncompressed) files.
//!
//! Only the header and TOC are read when an archive is opened; payloads are
//! read on demand, so multi-hundred-megabyte archives stay cheap.

pub mod discover;
mod utf;

use std::fs::File;
use std::io::{self, Read, Seek, SeekFrom};
use std::path::Path;
use utf::Table;

const DATA_BASE: u64 = 0x800;

#[derive(Debug, thiserror::Error)]
pub enum CpkError {
    #[error(transparent)]
    Io(#[from] io::Error),
    /// The archive is malformed or unsupported.
    #[error("{0}")]
    Format(String),
    /// The requested member is not in the archive.
    #[error("File {0:?} not found in CPK")]
    NotFound(String),
}

impl CpkError {
    fn format(message: impl Into<String>) -> Self {
        Self::Format(message.into())
    }
}

fn require_bounds(len: usize, offset: usize, size: usize, label: &str) -> Result<(), CpkError> {
    match offset.checked_add(size) {
        Some(end) if end <= len => Ok(()),
        _ => Err(CpkError::format(format!(
            "{label} at 0x{offset:X} with size {size} exceeds {len} bytes"
        ))),
    }
}

/// An opened archive: parsed table of contents over a seekable reader.
pub struct Cpk<R> {
    reader: R,
    len: u64,
    toc_offset: u64,
    content_offset: u64,
    table: Table,
    names: Vec<String>,
}

impl Cpk<File> {
    pub fn open(path: &Path) -> Result<Self, CpkError> {
        Self::from_reader(File::open(path)?)
    }
}

impl<R: Read + Seek> Cpk<R> {
    pub fn from_reader(mut reader: R) -> Result<Self, CpkError> {
        let len = reader.seek(SeekFrom::End(0))?;
        let header = read_utf_chunk(&mut reader, len, 16)?;
        let header = utf::parse(&header)?;
        if header.len() != 1 {
            return Err(CpkError::format(format!(
                "CPK header must contain one row; got {}",
                header.len()
            )));
        }

        let toc_offset = match header.get(0, "TocOffset") {
            Some(value) => match value {
                utf::Value::Int(raw) if *raw == u64::MAX as i128 => {
                    find_marker(&mut reader, len, b"TOC ")?
                        .ok_or_else(|| CpkError::format("TOC marker not found in CPK"))?
                }
                other => other
                    .as_u64()
                    .ok_or_else(|| CpkError::format("CPK header has no valid TocOffset"))?,
            },
            None => return Err(CpkError::format("CPK header has no valid TocOffset")),
        };
        let toc = usize::try_from(toc_offset).unwrap_or(usize::MAX);
        require_bounds(len as usize, toc, 16, "CPK TOC chunk")?;
        let content_offset = match header.get(0, "ContentOffset") {
            None => DATA_BASE,
            Some(value) => value
                .as_u64()
                .ok_or_else(|| CpkError::format("CPK header has invalid ContentOffset"))?,
        };

        let table = read_toc(&mut reader, len, toc_offset)?;
        let names = (0..table.len()).map(|row| full_name(&table, row)).collect();
        Ok(Self {
            reader,
            len,
            toc_offset,
            content_offset,
            table,
            names,
        })
    }

    /// Full archive paths of every entry, in TOC order.
    pub fn list_files(&self) -> &[String] {
        &self.names
    }

    /// Read one stored file into memory. `member` may be a full path
    /// (`common/etc/pesdb/Player.bin`) or, when unique, a trailing path/basename.
    pub fn read_file(&mut self, member: &str) -> Result<Vec<u8>, CpkError> {
        let row = self.select_row(member)?;
        let invalid =
            || CpkError::format(format!("CPK row for {member:?} has invalid offset/size"));
        let file_offset = self
            .table
            .get(row, "FileOffset")
            .and_then(utf::Value::as_u64)
            .ok_or_else(invalid)?;
        let size_cell = self
            .table
            .get(row, "FileSize")
            .or_else(|| self.table.get(row, "ExtractSize"));
        let file_size = match size_cell {
            Some(value) => value.as_u64().ok_or_else(invalid)?,
            None => 0,
        };
        let start = self.payload_start(file_offset, file_size)?;
        self.reader.seek(SeekFrom::Start(start))?;
        let mut payload = vec![0u8; file_size as usize];
        self.reader.read_exact(&mut payload)?;
        Ok(payload)
    }

    fn select_row(&self, member: &str) -> Result<usize, CpkError> {
        let requested = member.replace('\\', "/");
        let requested = requested.trim_matches('/');
        let exact: Vec<usize> = (0..self.names.len())
            .filter(|&row| self.names[row] == requested)
            .collect();
        match exact.as_slice() {
            [row] => return Ok(*row),
            [] => {}
            _ => {
                return Err(CpkError::format(format!(
                    "CPK contains duplicate path {member:?}"
                )));
            }
        }

        let suffix = format!("/{requested}");
        let matches: Vec<usize> = (0..self.names.len())
            .filter(|&row| {
                let file_name = self
                    .table
                    .get(row, "FileName")
                    .map_or("", utf::Value::as_text);
                file_name.trim_matches('/') == requested || self.names[row].ends_with(&suffix)
            })
            .collect();
        match matches.as_slice() {
            [] => Err(CpkError::NotFound(member.to_owned())),
            [row] => Ok(*row),
            rows => Err(CpkError::format(format!(
                "CPK filename {member:?} is ambiguous: {}",
                rows.iter()
                    .map(|&row| self.names[row].as_str())
                    .collect::<Vec<_>>()
                    .join(", ")
            ))),
        }
    }

    /// Archives disagree on what a file offset is relative to; take the first
    /// base that keeps the payload inside the archive.
    fn payload_start(&self, file_offset: u64, file_size: u64) -> Result<u64, CpkError> {
        [self.content_offset, DATA_BASE, self.toc_offset]
            .into_iter()
            .filter_map(|base| file_offset.checked_add(base))
            .find(|&start| start <= self.len && start.checked_add(file_size).is_some_and(|end| end <= self.len))
            .ok_or_else(|| {
                CpkError::format(format!(
                    "CPK payload offset 0x{file_offset:X} and size {file_size} exceed archive bounds"
                ))
            })
    }
}

fn full_name(table: &Table, row: usize) -> String {
    let text = |column| {
        table
            .get(row, column)
            .map_or("", utf::Value::as_text)
            .trim_matches('/')
    };
    let (directory, filename) = (text("DirName"), text("FileName"));
    if !directory.is_empty() && !filename.is_empty() {
        format!("{directory}/{filename}")
    } else if !filename.is_empty() {
        filename.to_owned()
    } else {
        directory.to_owned()
    }
}

/// Read the table body of the `@UTF` chunk at `offset` (the bytes after its
/// 8-byte `@UTF` + size prefix).
fn read_utf_chunk<R: Read + Seek>(
    reader: &mut R,
    len: u64,
    offset: u64,
) -> Result<Vec<u8>, CpkError> {
    let offset_usize = usize::try_from(offset).unwrap_or(usize::MAX);
    require_bounds(len as usize, offset_usize, 24, "@UTF header")?;
    let mut prefix = [0u8; 8];
    reader.seek(SeekFrom::Start(offset))?;
    reader.read_exact(&mut prefix)?;
    if &prefix[..4] != b"@UTF" {
        return Err(CpkError::format(format!(
            "Expected @UTF at 0x{offset:X}, got {:?}",
            &prefix[..4]
        )));
    }
    let size = utf::be_u32(&prefix, 4) as usize;
    require_bounds(len as usize, offset_usize + 8, size, "@UTF table")?;
    if size < 24 {
        return Err(CpkError::format("@UTF table header is truncated"));
    }
    let mut table = vec![0u8; size];
    reader.read_exact(&mut table)?;
    Ok(table)
}

fn read_toc<R: Read + Seek>(reader: &mut R, len: u64, toc_offset: u64) -> Result<Table, CpkError> {
    let wrap = |error: CpkError| match error {
        CpkError::Format(message) => CpkError::format(format!(
            "Could not parse CPK TOC at 0x{toc_offset:X}: {message}"
        )),
        other => other,
    };
    let chunk = read_utf_chunk(reader, len, toc_offset + 16).map_err(wrap)?;
    match utf::parse(&chunk) {
        Ok(table) => Ok(table),
        Err(standard) => utf::parse_protected_toc(&chunk).map_err(|_| wrap(standard)),
    }
}

/// Offset of the first occurrence of `marker` in the stream.
fn find_marker<R: Read + Seek>(
    reader: &mut R,
    len: u64,
    marker: &[u8],
) -> Result<Option<u64>, CpkError> {
    const CHUNK: usize = 1 << 20;
    let mut buffer = vec![0u8; CHUNK + marker.len() - 1];
    let mut position = 0u64;
    reader.seek(SeekFrom::Start(0))?;
    let mut carried = 0usize;
    while position < len {
        let want = CHUNK.min((len - position) as usize);
        reader.read_exact(&mut buffer[carried..carried + want])?;
        let filled = carried + want;
        if let Some(found) = buffer[..filled]
            .windows(marker.len())
            .position(|w| w == marker)
        {
            return Ok(Some(position - carried as u64 + found as u64));
        }
        carried = (marker.len() - 1).min(filled);
        buffer.copy_within(filled - carried..filled, 0);
        position += want as u64;
    }
    Ok(None)
}

#[cfg(test)]
pub(crate) mod testing;
#[cfg(test)]
mod tests;
