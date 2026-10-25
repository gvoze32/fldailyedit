//! Stage 5: the modified save must introduce no new integrity error, and
//! neither the input nor the output file may have changed under us.

use std::collections::HashSet;
use std::path::Path;

use fl_save::edit_file::EditFile;
use fl_save::validate::IntegrityError;

use super::prepare::{MAX_LISTED_ERRORS, OutputState, fail, sha256_file};
use crate::api::{ErrorCode, Stage, UpdateError};

/// Identity of an integrity error that ignores magnitudes (counts, sizes,
/// offsets). A pre-existing finding stays "pre-existing" when a legitimate
/// change alters its numbers; only a new kind of problem, or the same
/// kind on a new team/player, is a regression.
pub(crate) fn error_key(error: &IntegrityError) -> String {
    use IntegrityError as E;
    match error {
        E::DataSize { .. } => "data_size".into(),
        E::CountExceedsCapacity { table, .. } => format!("count_exceeds_capacity:{table}"),
        E::PlayerEntryOutOfBounds { index } => format!("player_entry_oob:{index}"),
        E::DuplicateEditedPlayer { player_id } => format!("duplicate_edited_player:{player_id}"),
        E::TeamEntryOutOfBounds { index } => format!("team_entry_oob:{index}"),
        E::DuplicateTeamEntry { team_id } => format!("duplicate_team:{team_id}"),
        E::TeamPlayerEntryOutOfBounds { index } => format!("team_player_entry_oob:{index}"),
        E::RosterUnknownTeam { team_id } => format!("roster_unknown_team:{team_id}"),
        E::DuplicateRoster { team_id } => format!("duplicate_roster:{team_id}"),
        E::RosterHoles { team_id, .. } => format!("roster_holes:{team_id}"),
        E::RosterDuplicatePlayer { team_id } => format!("roster_duplicate_player:{team_id}"),
        E::ShirtAboveMax { team_id } => format!("shirt_above_max:{team_id}"),
        E::DuplicateShirts { team_id } => format!("duplicate_shirts:{team_id}"),
        E::NoClubs => "no_clubs".into(),
        E::ClubUndersized { team_id, .. } => format!("club_undersized:{team_id}"),
        E::MultiClubPlayer { player_id, .. } => format!("multi_club_player:{player_id}"),
        E::GamePlanEntryOutOfBounds { index } => format!("game_plan_oob:{index}"),
        E::DuplicateGamePlan { team_id } => format!("duplicate_game_plan:{team_id}"),
        E::GamePlanUnknownTeam { team_id } => format!("game_plan_unknown_team:{team_id}"),
        E::GamePlanActivePrefix { team_id } => format!("game_plan_active_prefix:{team_id}"),
        E::GamePlanStarters { team_id } => format!("game_plan_starters:{team_id}"),
        E::GamePlanPositionArrayOutOfBounds {
            team_id,
            relative_offset,
        } => format!("game_plan_position_array_oob:{team_id}:{relative_offset}"),
        E::GamePlanGoalkeeperCodes {
            team_id,
            preset,
            phase,
            ..
        } => format!("game_plan_gk_codes:{team_id}:{preset}:{phase}"),
        E::GamePlanGoalkeeperAssignedCode {
            team_id,
            preset,
            phase,
            player_id,
            ..
        } => format!("game_plan_gk_assigned:{team_id}:{preset}:{phase}:{player_id}"),
        E::GamePlanOutfielderGoalkeeperCode {
            team_id,
            preset,
            phase,
            player_id,
            ..
        } => format!("game_plan_outfielder_gk:{team_id}:{preset}:{phase}:{player_id}"),
        E::GamePlanRoleInvalidSlot {
            team_id,
            role_offset,
            ..
        } => format!("game_plan_role_invalid:{team_id}:{role_offset}"),
        E::GamePlanRoleEmptySlot {
            team_id,
            role_offset,
            ..
        } => format!("game_plan_role_empty:{team_id}:{role_offset}"),
    }
}

/// Keys of the integrity errors present before the run changed anything.
pub(crate) fn baseline_keys(errors: &[IntegrityError]) -> HashSet<String> {
    errors.iter().map(error_key).collect()
}

/// Integrity errors the modified save has beyond `baseline`; the second
/// value counts the tolerated pre-existing ones.
pub(crate) fn new_errors(
    edit: &EditFile,
    baseline: &HashSet<String>,
) -> (Vec<IntegrityError>, usize) {
    let post = edit.validate_integrity().errors;
    let total = post.len();
    let fresh: Vec<IntegrityError> = post
        .into_iter()
        .filter(|error| !baseline.contains(&error_key(error)))
        .collect();
    let preserved = total - fresh.len();
    (fresh, preserved)
}

/// Why verification refused the result.
pub(crate) fn integrity_failure(fresh: &[IntegrityError], preserved: usize) -> UpdateError {
    let mut lines =
        vec!["Modified save failed integrity validation; changes were rolled back.".to_owned()];
    lines.extend(
        fresh
            .iter()
            .take(MAX_LISTED_ERRORS)
            .map(|error| format!("  - {error}")),
    );
    if fresh.len() > MAX_LISTED_ERRORS {
        lines.push(format!(
            "  ... and {} more errors",
            fresh.len() - MAX_LISTED_ERRORS
        ));
    }
    if preserved > 0 {
        lines.push(format!(
            "  Preserved {preserved} pre-existing integrity diagnostics."
        ));
    }
    fail(
        ErrorCode::VerificationFailed,
        Stage::Verifying,
        lines.join("\n"),
    )
}

/// The input must be byte-identical to what the run read; the output must not
/// have appeared, vanished or changed (a concurrent writer wins).
pub(crate) fn check_files_unchanged(
    edit_path: &Path,
    input_digest: &str,
    target: &Path,
    same_input_output: bool,
    output: &OutputState,
) -> Result<(), UpdateError> {
    let stage = Stage::Verifying;
    let read_failure = |path: &Path, error: std::io::Error| {
        fail(
            ErrorCode::InputChanged,
            stage,
            format!("Could not re-read {}: {error}", path.display()),
        )
    };
    let current = sha256_file(edit_path).map_err(|e| read_failure(edit_path, e))?;
    if current != input_digest {
        return Err(fail(
            ErrorCode::InputChanged,
            stage,
            "Input EDIT file changed while this run was processing; stale output was not written.",
        ));
    }
    if same_input_output {
        return Ok(());
    }
    let changed = match (target.is_file(), output.existed) {
        (false, false) => false,
        (true, true) => {
            sha256_file(target).map_err(|e| read_failure(target, e))?
                != output.digest.clone().unwrap_or_default()
        }
        _ => true,
    };
    if changed {
        return Err(fail(
            ErrorCode::InputChanged,
            stage,
            "Output EDIT file changed while this run was processing; concurrent output was preserved.",
        ));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn magnitude_changes_keep_the_same_key() {
        let before = IntegrityError::ClubUndersized {
            team_id: 7,
            size: 15,
        };
        let after = IntegrityError::ClubUndersized {
            team_id: 7,
            size: 14,
        };
        assert_ne!(
            before.to_string(),
            after.to_string(),
            "string compare would flag it"
        );
        assert_eq!(error_key(&before), error_key(&after));
        assert_ne!(
            error_key(&before),
            error_key(&IntegrityError::ClubUndersized {
                team_id: 8,
                size: 15
            })
        );
    }

    #[test]
    fn unchanged_files_pass_and_modified_ones_fail() {
        let dir = tempfile::tempdir().unwrap();
        let input = dir.path().join("in");
        let output = dir.path().join("out");
        std::fs::write(&input, b"one").unwrap();
        let digest = super::super::prepare::sha256_hex(b"one");
        let absent = OutputState {
            existed: false,
            digest: None,
        };
        check_files_unchanged(&input, &digest, &output, false, &absent).unwrap();

        std::fs::write(&output, b"x").unwrap();
        let error = check_files_unchanged(&input, &digest, &output, false, &absent).unwrap_err();
        assert!(error.message.starts_with("Output EDIT file changed"));
        assert_eq!(error.code, ErrorCode::InputChanged);

        std::fs::write(&input, b"two").unwrap();
        let error = check_files_unchanged(&input, &digest, &output, true, &absent).unwrap_err();
        assert!(error.message.starts_with("Input EDIT file changed"));
    }
}
