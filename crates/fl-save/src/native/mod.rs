//! Read-only game databases (`Player.bin`, `Team.bin`, `PlayerAssignment.bin`)
//! and the search order for finding them: explicit file → game-root CPK
//! archives → none.

mod player_assignment;
mod player_bin;
mod team_bin;

pub use player_assignment::{PlayerAssignmentDatabase, PlayerAssignmentRecord};
pub use player_bin::{PlayerBinDatabase, PlayerBinRecord};
pub use team_bin::{TeamBinDatabase, TeamBinRecord};

use crate::cpk::{Cpk, CpkError, discover};
use crate::rating::RatingError;
use crate::wesys::WesysError;
use std::fmt;
use std::path::{Path, PathBuf};

#[derive(Debug, thiserror::Error)]
pub enum NativeError {
    #[error(transparent)]
    Wesys(#[from] WesysError),
    #[error("{label} data must be divisible by {record_size}; got {len} bytes")]
    BadLength {
        label: &'static str,
        len: usize,
        record_size: usize,
    },
    #[error(transparent)]
    Rating(#[from] RatingError),
}

fn check_record_multiple(
    label: &'static str,
    len: usize,
    record_size: usize,
) -> Result<(), NativeError> {
    if len.is_multiple_of(record_size) {
        Ok(())
    } else {
        Err(NativeError::BadLength {
            label,
            len,
            record_size,
        })
    }
}

/// A database the loader knows how to find inside a CPK archive.
pub trait NativeDatabase: Sized {
    /// Short name used in diagnostics (`Player.bin`).
    const LABEL: &'static str;
    /// Path of the file inside the game's CPK archives.
    const CPK_MEMBER: &'static str;

    fn from_bytes(raw: &[u8]) -> Result<Self, NativeError>;
}

impl NativeDatabase for PlayerBinDatabase {
    const LABEL: &'static str = "Player.bin";
    const CPK_MEMBER: &'static str = "common/etc/pesdb/Player.bin";
    fn from_bytes(raw: &[u8]) -> Result<Self, NativeError> {
        Self::from_bytes(raw)
    }
}

impl NativeDatabase for TeamBinDatabase {
    const LABEL: &'static str = "Team.bin";
    const CPK_MEMBER: &'static str = "common/etc/pesdb/Team.bin";
    fn from_bytes(raw: &[u8]) -> Result<Self, NativeError> {
        Self::from_bytes(raw)
    }
}

impl NativeDatabase for PlayerAssignmentDatabase {
    const LABEL: &'static str = "PlayerAssignment.bin";
    const CPK_MEMBER: &'static str = "common/etc/pesdb/PlayerAssignment.bin";
    fn from_bytes(raw: &[u8]) -> Result<Self, NativeError> {
        Self::from_bytes(raw)
    }
}

/// Where the loaded database came from.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DatabaseSource {
    File(PathBuf),
    /// Member of a CPK archive.
    Archive {
        cpk: PathBuf,
        label: &'static str,
    },
}

impl fmt::Display for DatabaseSource {
    /// `path` for files, `archive.cpk::Label` for archive members (audit-trail format).
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::File(path) => write!(f, "{}", path.display()),
            Self::Archive { cpk, label } => write!(f, "{}::{label}", cpk.display()),
        }
    }
}

/// Where a database may live. Precedence: `explicit` file, then archives below
/// `game_root` (data_extra.cpk before data_s2526.cpk), then the `configured`
/// file; with no `explicit` and no `game_root` only `configured` is tried.
#[derive(Debug, Clone, Default)]
pub struct NativeLocations {
    /// File chosen by the caller for this run; beats the game archives.
    pub explicit: Option<PathBuf>,
    /// Persistent-setting file; only a fallback when a game root is known.
    pub configured: Option<PathBuf>,
    /// Game installation root whose CPK archives hold the databases.
    pub game_root: Option<PathBuf>,
}

/// A database together with its origin.
#[derive(Debug)]
pub struct Loaded<D> {
    pub database: D,
    pub source: DatabaseSource,
}

/// Result of a search: at most one database plus every candidate that was
/// skipped as unreadable or invalid (the library never logs).
#[derive(Debug)]
pub struct LoadOutcome<D> {
    pub loaded: Option<Loaded<D>>,
    pub warnings: Vec<String>,
}

/// Find and parse database `D` following the [`NativeLocations`] precedence.
/// Candidates that are missing are skipped silently; unreadable or invalid
/// ones are skipped with a warning.
pub fn load_database<D: NativeDatabase>(locations: &NativeLocations) -> LoadOutcome<D> {
    let file = locations
        .explicit
        .as_deref()
        .or(locations.configured.as_deref());
    let archives_first = locations.explicit.is_none() && locations.game_root.is_some();
    let mut warnings = Vec::new();

    let from_archives = |warnings: &mut Vec<String>| {
        let root = locations.game_root.as_deref()?;
        load_from_archives::<D>(root, warnings)
    };
    let from_file =
        |warnings: &mut Vec<String>| file.and_then(|path| load_from_file::<D>(path, warnings));

    let loaded = if archives_first {
        from_archives(&mut warnings).or_else(|| from_file(&mut warnings))
    } else {
        from_file(&mut warnings).or_else(|| from_archives(&mut warnings))
    };
    LoadOutcome { loaded, warnings }
}

fn load_from_file<D: NativeDatabase>(path: &Path, warnings: &mut Vec<String>) -> Option<Loaded<D>> {
    if !path.is_file() {
        return None;
    }
    let parsed = std::fs::read(path)
        .map_err(|error| error.to_string())
        .and_then(|raw| D::from_bytes(&raw).map_err(|error| error.to_string()));
    match parsed {
        Ok(database) => Some(Loaded {
            database,
            source: DatabaseSource::File(path.to_path_buf()),
        }),
        Err(error) => {
            warnings.push(format!(
                "Ignoring invalid {} metadata {}: {error}",
                D::LABEL,
                path.display()
            ));
            None
        }
    }
}

fn load_from_archives<D: NativeDatabase>(
    game_root: &Path,
    warnings: &mut Vec<String>,
) -> Option<Loaded<D>> {
    for cpk_path in discover::discover_game_cpks(Some(game_root)) {
        let payload = Cpk::open(&cpk_path).and_then(|mut cpk| cpk.read_file(D::CPK_MEMBER));
        let payload = match payload {
            Ok(payload) => payload,
            Err(CpkError::NotFound(_)) => continue,
            Err(error) => {
                warnings.push(format!(
                    "Ignoring unreadable {} metadata in {}: {error}",
                    D::LABEL,
                    cpk_path.display()
                ));
                continue;
            }
        };
        match D::from_bytes(&payload) {
            Ok(database) => {
                return Some(Loaded {
                    database,
                    source: DatabaseSource::Archive {
                        cpk: cpk_path,
                        label: D::LABEL,
                    },
                });
            }
            Err(error) => warnings.push(format!(
                "Ignoring invalid {} metadata in {}: {error}",
                D::LABEL,
                cpk_path.display()
            )),
        }
    }
    None
}

#[cfg(test)]
mod tests;
