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
use std::collections::HashSet;
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
    /// Archive tried before the others when it holds the database (Team.bin
    /// follows the archive the chosen Player.bin came from).
    pub preferred_archive: Option<PathBuf>,
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
        load_from_archives::<D>(root, locations.preferred_archive.as_deref(), warnings)
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
    preferred: Option<&Path>,
    warnings: &mut Vec<String>,
) -> Option<Loaded<D>> {
    let mut cpk_paths = discover::discover_game_cpks(Some(game_root));
    if let Some(position) =
        preferred.and_then(|preferred| cpk_paths.iter().position(|path| path == preferred))
    {
        let first = cpk_paths.remove(position);
        cpk_paths.insert(0, first);
    }
    cpk_paths
        .iter()
        .find_map(|cpk_path| load_from_archive::<D>(cpk_path, warnings))
}

/// `D` from one archive; a missing member is skipped silently, an unreadable
/// or invalid one with a warning.
fn load_from_archive<D: NativeDatabase>(
    cpk_path: &Path,
    warnings: &mut Vec<String>,
) -> Option<Loaded<D>> {
    let payload = Cpk::open(cpk_path).and_then(|mut cpk| cpk.read_file(D::CPK_MEMBER));
    let payload = match payload {
        Ok(payload) => payload,
        Err(CpkError::NotFound(_)) => return None,
        Err(error) => {
            warnings.push(format!(
                "Ignoring unreadable {} metadata in {}: {error}",
                D::LABEL,
                cpk_path.display()
            ));
            return None;
        }
    };
    match D::from_bytes(&payload) {
        Ok(database) => Some(Loaded {
            database,
            source: DatabaseSource::Archive {
                cpk: cpk_path.to_path_buf(),
                label: D::LABEL,
            },
        }),
        Err(error) => {
            warnings.push(format!(
                "Ignoring invalid {} metadata in {}: {error}",
                D::LABEL,
                cpk_path.display()
            ));
            None
        }
    }
}

/// One game archive that carries a `Player.bin`, scored against the players a
/// save needs.
#[derive(Debug)]
pub struct PlayerBinCandidate {
    pub cpk: PathBuf,
    pub database: PlayerBinDatabase,
    /// Needed player ids this archive's `Player.bin` does not know.
    pub missing: usize,
}

/// Every archive below a game root with a readable `Player.bin`, in discovery
/// precedence order, plus the archives skipped as unreadable or invalid.
#[derive(Debug, Default)]
pub struct PlayerBinScan {
    pub candidates: Vec<PlayerBinCandidate>,
    pub warnings: Vec<String>,
}

impl PlayerBinScan {
    /// The candidate that misses the fewest needed players; ties keep
    /// discovery order.
    pub fn best(&self) -> Option<usize> {
        best_by_coverage(self.candidates.iter().map(|candidate| candidate.missing))
    }
}

/// Index of the smallest count; the first one wins a tie.
pub fn best_by_coverage(missing: impl IntoIterator<Item = usize>) -> Option<usize> {
    missing
        .into_iter()
        .enumerate()
        .min_by_key(|&(_, missing)| missing)
        .map(|(index, _)| index)
}

/// Read the `Player.bin` of every archive below `game_root` and count how many
/// of the `needed` player ids each one lacks.
pub fn scan_player_bins(game_root: &Path, needed: &HashSet<u32>) -> PlayerBinScan {
    let mut scan = PlayerBinScan::default();
    for cpk in discover::discover_game_cpks(Some(game_root)) {
        let Some(loaded) = load_from_archive::<PlayerBinDatabase>(&cpk, &mut scan.warnings) else {
            continue;
        };
        let missing = needed
            .iter()
            .filter(|&&id| !loaded.database.contains(id))
            .count();
        scan.candidates.push(PlayerBinCandidate {
            cpk,
            database: loaded.database,
            missing,
        });
    }
    scan
}

#[cfg(test)]
mod tests;
