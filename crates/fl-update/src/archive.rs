//! Layout rules for `FLDailyEditInstaller.zip`.

use std::fs::File;
use std::io::{self, Read, Seek, Write};
use std::path::Path;

use zip::ZipArchive;

use crate::error::{Error, Result};
use crate::manifest::{APP_EXECUTABLE_ENTRY, MAX_APP_EXECUTABLE_BYTES};

const COPY_CHUNK_BYTES: usize = 64 * 1024;

fn invalid(message: &str) -> Error {
    Error::InvalidArchive(message.to_owned())
}

/// Open the archive and check it holds exactly one non-empty, size-bounded entry named
/// [`APP_EXECUTABLE_ENTRY`]. Returns the archive and the entry's declared size.
pub(crate) fn open_installer_archive<R: Read + Seek>(reader: R) -> Result<(ZipArchive<R>, u64)> {
    let mut archive = ZipArchive::new(reader)
        .map_err(|_| invalid("downloaded installer archive could not be opened"))?;
    if archive.len() != 1 {
        return Err(invalid(
            "installer archive must contain exactly FLDailyEditInstaller.exe",
        ));
    }
    let entry = archive
        .by_index(0)
        .map_err(|_| invalid("downloaded installer archive could not be opened"))?;
    if entry
        .name()
        .map_or(true, |name| name != APP_EXECUTABLE_ENTRY)
    {
        return Err(invalid(
            "installer archive must contain exactly FLDailyEditInstaller.exe",
        ));
    }
    let size = entry.size();
    if entry.is_dir() || size == 0 {
        return Err(invalid("installer executable is invalid"));
    }
    if size > MAX_APP_EXECUTABLE_BYTES {
        return Err(Error::TooLarge(
            "installer executable exceeds the size limit".into(),
        ));
    }
    drop(entry);
    Ok((archive, size))
}

/// Check the zip at `path` has the exact installer layout.
pub(crate) fn validate_installer_archive(path: &Path) -> Result<()> {
    let file = File::open(path)
        .map_err(|error| Error::io(format!("cannot open {}", path.display()), error))?;
    open_installer_archive(file).map(drop)
}

/// Extract the single executable to `destination` and fsync it.
pub(crate) fn extract_executable(archive_path: &Path, destination: &Path) -> Result<()> {
    let file = File::open(archive_path)
        .map_err(|error| Error::io("cannot open downloaded archive", error))?;
    let (mut archive, size) = open_installer_archive(file)?;
    let entry = archive
        .by_index(0)
        .map_err(|_| invalid("downloaded installer archive could not be opened"))?;

    let mut target = File::create(destination)
        .map_err(|error| Error::io("cannot create staged executable", error))?;
    // The declared size is only a claim: never read more than size + 1 bytes.
    let mut source = entry.take(size + 1);
    let mut buffer = vec![0_u8; COPY_CHUNK_BYTES];
    let mut written = 0_u64;
    loop {
        let read = match source.read(&mut buffer) {
            Ok(0) => break,
            Ok(read) => read,
            Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
            Err(_) => return Err(invalid("installer archive entry is corrupt")),
        };
        target
            .write_all(&buffer[..read])
            .map_err(|error| Error::io("cannot write staged executable", error))?;
        written += read as u64;
    }
    if written != size {
        return Err(invalid(
            "installer executable does not match its declared size",
        ));
    }
    target
        .sync_all()
        .map_err(|error| Error::io("cannot flush staged executable", error))
}
