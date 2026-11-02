//! League membership rules: which proposals for the competition block are safe.
//!
//! A division is a run of team ids in the competition block (see
//! [`crate::layout`]); [`crate::edit_file::EditFile::set_league_divisions`]
//! rewrites the ids in place. The block has no counts or headers, so the only
//! thing keeping the game's league structure intact is that every division
//! keeps its size and every club stays listed exactly once.

use std::collections::HashSet;

use thiserror::Error;

/// Why a membership proposal was refused.
#[derive(Debug, Clone, PartialEq, Eq, Error)]
pub enum LeagueError {
    #[error("the save has {expected} league divisions, the proposal has {found}")]
    DivisionCount { expected: usize, found: usize },
    #[error("division {division} has {expected} teams, the proposal has {found}")]
    DivisionSize {
        division: usize,
        expected: usize,
        found: usize,
    },
    #[error("team {team_id} is listed more than once")]
    Duplicate { team_id: u32 },
    #[error("team {team_id} is not a member of any division of the save")]
    UnknownTeam { team_id: u32 },
}

/// Check that `proposed` reorganises `current` without changing any division
/// size and without adding, dropping or duplicating a club.
pub(crate) fn check_membership(
    current: &[Vec<u32>],
    proposed: &[Vec<u32>],
) -> Result<(), LeagueError> {
    if current.len() != proposed.len() {
        return Err(LeagueError::DivisionCount {
            expected: current.len(),
            found: proposed.len(),
        });
    }
    for (index, (old, new)) in current.iter().zip(proposed).enumerate() {
        if old.len() != new.len() {
            return Err(LeagueError::DivisionSize {
                division: index + 1,
                expected: old.len(),
                found: new.len(),
            });
        }
    }
    let members: HashSet<u32> = current.iter().flatten().copied().collect();
    let mut seen: HashSet<u32> = HashSet::with_capacity(members.len());
    for &team_id in proposed.iter().flatten() {
        if !members.contains(&team_id) {
            return Err(LeagueError::UnknownTeam { team_id });
        }
        if !seen.insert(team_id) {
            return Err(LeagueError::Duplicate { team_id });
        }
    }
    // Equal total size, all members, no repeats: every member is listed once.
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn current() -> Vec<Vec<u32>> {
        vec![vec![1, 2, 3], vec![4, 5], vec![6, 7]]
    }

    #[test]
    fn a_swap_between_divisions_is_accepted() {
        let proposed = vec![vec![1, 5, 3], vec![4, 2], vec![6, 7]];
        assert_eq!(check_membership(&current(), &proposed), Ok(()));
    }

    #[test]
    fn the_unchanged_membership_is_accepted() {
        assert_eq!(check_membership(&current(), &current()), Ok(()));
    }

    #[test]
    fn sizes_must_not_change() {
        let proposed = vec![vec![1, 2], vec![3, 4, 5], vec![6, 7]];
        assert_eq!(
            check_membership(&current(), &proposed),
            Err(LeagueError::DivisionSize {
                division: 1,
                expected: 3,
                found: 2
            })
        );
        let missing = vec![vec![1, 2, 3], vec![4, 5]];
        assert_eq!(
            check_membership(&current(), &missing),
            Err(LeagueError::DivisionCount {
                expected: 3,
                found: 2
            })
        );
    }

    #[test]
    fn a_club_cannot_appear_twice_or_be_created() {
        let twice = vec![vec![1, 2, 3], vec![4, 1], vec![6, 7]];
        assert_eq!(
            check_membership(&current(), &twice),
            Err(LeagueError::Duplicate { team_id: 1 })
        );
        let invented = vec![vec![1, 2, 3], vec![4, 99], vec![6, 7]];
        assert_eq!(
            check_membership(&current(), &invented),
            Err(LeagueError::UnknownTeam { team_id: 99 })
        );
    }
}
