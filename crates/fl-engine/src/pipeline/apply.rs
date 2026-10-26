//! Stage 4: apply the plan to the in-memory save.
//!
//! Order: formations → tactics → roster actions (shirt numbers per team in
//! one batch) → game-plan repair (XI and bench by native rating) → captains.
//! Nothing touches disk here.

use std::collections::{HashMap, HashSet};

use fl_save::edit_file::EditFile;
use fl_save::layout::TacticalField;
use fl_save::roster::{AddRequest, MoveRequest, RepairOptions, RosterErrorKind};
use fl_sources::model::ScrapeResult;

use super::Reporter;
use super::prepare::fail;
use super::records::{captain_entry, roster_map, transfer_entry};
use crate::api::{CancelToken, ChangeCounts, ErrorCode, Progress, Stage, UpdateError};
use crate::log::LogRecord;
use crate::native::NativeData;
use crate::planning::{
    Plan, PlayerMatcher, RosterAction, ShirtStatus, SkippedTransfer, plan_captain_updates,
    plan_lineup_clubs, plan_shirt_batch,
};

/// Refusals that skip one event instead of failing the run: the save state
/// simply no longer matches the plan.
fn is_safe_refusal(kind: RosterErrorKind) -> bool {
    use RosterErrorKind as K;
    matches!(
        kind,
        K::SameTeam
            | K::SourceTeamMissing
            | K::DestinationTeamMissing
            | K::SourcePlayerMissing
            | K::DestinationPlayerExists
            | K::DuplicateClubRegistration
            | K::OverflowNotAuthorized
            | K::OverflowCandidateStale
            | K::NoSafeOverflowCandidate
            | K::LastGoalkeeper
    )
}

/// Everything the apply stage reads besides the save.
pub(crate) struct ApplyInput<'a> {
    pub plan: &'a Plan,
    pub scrape: &'a ScrapeResult,
    pub matcher: &'a dyn PlayerMatcher,
    pub native: &'a NativeData,
    pub club_ids: &'a HashSet<u32>,
    pub threshold: f64,
    pub allow_overflow_release: bool,
    pub dry_run: bool,
}

/// What applying the plan changed.
#[derive(Debug, Default)]
pub(crate) struct Applied {
    pub(crate) counts: ChangeCounts,
    /// Applied-change records, transfers first, then captains.
    pub(crate) entries: Vec<LogRecord>,
    /// Events the save refused at apply time.
    pub(crate) refused: Vec<SkippedTransfer>,
    pub(crate) gameplan_changed: bool,
    pub(crate) warnings: Vec<String>,
}

impl Applied {
    /// Fold the next round of a run into the totals. Changes add up; what the
    /// round found already current or refused describes the save as it stands,
    /// so the latest round replaces it.
    pub(crate) fn absorb(&mut self, round: Applied) {
        let (total, latest) = (&mut self.counts, round.counts);
        total.transfers += latest.transfers;
        total.shirt_numbers += latest.shirt_numbers;
        total.captains += latest.captains;
        total.tactics += latest.tactics;
        total.formations += latest.formations;
        total.unchanged = latest.unchanged;
        total.safety_skipped = latest.safety_skipped;
        self.entries.extend(round.entries);
        self.refused = round.refused;
        self.gameplan_changed |= round.gameplan_changed;
    }

    /// Whether the save bytes differ from the input.
    pub(crate) fn is_effective(&self) -> bool {
        let counts = &self.counts;
        counts.transfers > 0
            || counts.shirt_numbers > 0
            || counts.captains > 0
            || counts.tactics > 0
            || counts.formations > 0
            || self.gameplan_changed
    }
}

fn apply_failed(matched_name: &str, action_type: &str, detail: &str) -> UpdateError {
    let reason = if detail.is_empty() {
        String::new()
    } else {
        format!(" Reason: {detail}")
    };
    fail(
        ErrorCode::ApplyFailed,
        Stage::Applying,
        format!(
            "Failed: {matched_name} ({action_type}); entire batch rolled back.{reason} No changes were published."
        ),
    )
}

fn display_name(matched: &crate::planning::MatchedTransfer) -> &str {
    if matched.matched_player_name.is_empty() {
        &matched.transfer.player_name
    } else {
        &matched.matched_player_name
    }
}

/// Apply `input.plan` to `edit`. On `Err` the caller restores its snapshot.
pub(crate) fn apply(
    edit: &mut EditFile,
    input: &ApplyInput<'_>,
    cancel: &CancelToken,
    reporter: &mut Reporter<'_>,
) -> Result<Applied, UpdateError> {
    let stage = Stage::Applying;
    let plan = input.plan;
    let mut applied = Applied::default();
    let counts = &mut applied.counts;

    for (team_id, formation) in &plan.formations {
        cancel.check(stage)?;
        match edit.set_team_formation(*team_id, formation) {
            Ok(changed) if changed > 0 => counts.formations += 1,
            Ok(_) => {}
            Err(error) => applied.warnings.push(format!(
                "Formation {formation} not applied to team {team_id}: {error}"
            )),
        }
    }
    if counts.formations > 0 {
        reporter.say(
            stage,
            format!("Game-plan formations changed: {} clubs", counts.formations),
        );
    }

    for (team_id, settings) in &plan.tactics {
        cancel.check(stage)?;
        let fields: Option<Vec<(TacticalField, u8)>> = settings
            .iter()
            .map(|(name, value)| {
                Some((TacticalField::from_name(name)?, u8::try_from(*value).ok()?))
            })
            .collect();
        let Some(fields) = fields else {
            applied.warnings.push(format!(
                "Tactical profile for team {team_id} has an unknown setting"
            ));
            continue;
        };
        match edit.set_team_tactical_settings(*team_id, &fields) {
            Ok(changed) => counts.tactics += u32::try_from(changed).unwrap_or(u32::MAX),
            Err(error) => applied.warnings.push(format!(
                "Tactical settings not applied to team {team_id}: {error}"
            )),
        }
    }
    if counts.tactics > 0 {
        reporter.say(
            stage,
            format!("Tactical settings changed: {}", counts.tactics),
        );
    }

    let mut touched: HashSet<u32> = HashSet::new();
    let mut shirt_batches: HashMap<u32, HashMap<usize, ShirtStatus>> = HashMap::new();
    let total = plan.roster_actions.len();

    for (index, planned) in plan.roster_actions.iter().enumerate() {
        cancel.check(stage)?;
        if index % 25 == 0 && total > 0 {
            reporter.emit(Progress {
                stage,
                message: format!("Applying changes {index}/{total}"),
                current: u32::try_from(index).unwrap_or(u32::MAX),
                total: u32::try_from(total).unwrap_or(u32::MAX),
            });
        }
        let matched = &planned.matched;
        let transfer = &matched.transfer;
        if planned.action == RosterAction::Skip {
            counts.safety_skipped += 1;
            let reason = if planned.reason.is_empty() {
                "state mismatch"
            } else {
                &planned.reason
            };
            reporter.say(
                stage,
                format!("⚠ Safety skip {}: {reason}", display_name(matched)),
            );
            continue;
        }
        let Some(player_id) = matched.player_id else {
            continue;
        };
        let to_team = matched.to.team();
        if planned.action == RosterAction::Noop {
            counts.unchanged += 1;
            continue;
        }
        let native_metadata = input.native.transfer_metadata(player_id);
        let preferred_shirt = transfer.shirt_number;
        let mut previous_shirt = None;

        let outcome: Result<(), (RosterErrorKind, String)> = match planned.action {
            RosterAction::ShirtUpdate => {
                let Some(target) = preferred_shirt else {
                    counts.unchanged += 1;
                    continue;
                };
                if let Some(team) = to_team
                    && !shirt_batches.contains_key(&team)
                {
                    let statuses = shirt_batch_for(edit, plan, team);
                    let updates: Vec<(u32, u32)> = statuses
                        .iter()
                        .filter_map(|(&action_index, status)| {
                            let item = &plan.roster_actions[action_index].matched;
                            let (player, number) = (item.player_id?, item.transfer.shirt_number?);
                            (status.is_clear() && status.previous != Some(number))
                                .then_some((player, number))
                        })
                        .collect();
                    if let Err(error) = sorted_batch_update(edit, team, updates) {
                        return Err(apply_failed(
                            display_name(matched),
                            matched.action_type(),
                            error.detail(),
                        ));
                    }
                    shirt_batches.insert(team, statuses);
                }
                let status = to_team
                    .and_then(|team| shirt_batches.get(&team))
                    .and_then(|batch| batch.get(&index));
                previous_shirt = match status {
                    Some(status) => status.previous,
                    None => to_team
                        .and_then(|team| edit.player_shirt_number(team, player_id))
                        .map(u32::from),
                };
                if previous_shirt == Some(target) {
                    counts.unchanged += 1;
                    continue;
                }
                if let Some(status) = status.filter(|status| !status.is_clear()) {
                    counts.safety_skipped += 1;
                    let detail = match status.conflict_player {
                        Some(other) => format!(
                            "shirt #{target} is already assigned to player {other} on team {}",
                            to_team.unwrap_or(0)
                        ),
                        None => status.reason.clone(),
                    };
                    reporter.say(
                        stage,
                        format!("⚠ Safety skip {}: {detail}", display_name(matched)),
                    );
                    continue;
                }
                Ok(())
            }
            RosterAction::Move => {
                let (Some(from), Some(to)) = (planned.current_team_id, to_team) else {
                    return Err(apply_failed(
                        display_name(matched),
                        matched.action_type(),
                        "missing team",
                    ));
                };
                let mut request = MoveRequest::new(player_id, from, to);
                request.preferred_shirt_number = preferred_shirt;
                request.position = &transfer.position;
                request.allow_overflow_release = input.allow_overflow_release;
                request.planned_overflow_player_id = planned.overflow_player_id;
                edit.move_player(&request)
                    .map(|_| ())
                    .map_err(|e| (e.kind(), e.detail().to_owned()))
            }
            RosterAction::Add => {
                let Some(to) = to_team else {
                    return Err(apply_failed(
                        display_name(matched),
                        matched.action_type(),
                        "missing team",
                    ));
                };
                let mut request = AddRequest::new(player_id, to);
                request.preferred_shirt_number = preferred_shirt;
                request.position = &transfer.position;
                request.allow_overflow_release = input.allow_overflow_release;
                request.planned_overflow_player_id = planned.overflow_player_id;
                edit.add_player(&request)
                    .map(|_| ())
                    .map_err(|e| (e.kind(), e.detail().to_owned()))
            }
            RosterAction::Release => {
                let Some(from) = matched.from.team() else {
                    return Err(apply_failed(
                        display_name(matched),
                        matched.action_type(),
                        "missing team",
                    ));
                };
                edit.release_player(player_id, from)
                    .map_err(|e| (e.kind(), e.detail().to_owned()))
            }
            RosterAction::Skip | RosterAction::Noop => continue,
        };

        if let Err((kind, detail)) = outcome {
            if is_safe_refusal(kind) {
                counts.safety_skipped += 1;
                let shown = if detail.is_empty() {
                    kind.as_str()
                } else {
                    &detail
                };
                reporter.say(
                    stage,
                    format!("⚠ Safety skip {}: {shown}", display_name(matched)),
                );
                applied.refused.push(SkippedTransfer::from_match(
                    matched,
                    kind.as_str(),
                    &detail,
                    &[],
                    Some(input.club_ids),
                ));
                continue;
            }
            return Err(apply_failed(
                display_name(matched),
                matched.action_type(),
                &detail,
            ));
        }

        if planned.action == RosterAction::ShirtUpdate {
            counts.shirt_numbers += 1;
        } else {
            counts.transfers += 1;
            touched.extend(
                [planned.current_team_id, matched.from.team(), to_team]
                    .into_iter()
                    .flatten()
                    .filter(|team| input.club_ids.contains(team)),
            );
        }
        applied.entries.push(transfer_entry(
            matched,
            planned.action,
            previous_shirt,
            native_metadata,
            input.dry_run,
        ));
    }

    applied.gameplan_changed = align_game_plans(edit, input, &touched, reporter);
    let counts = &mut applied.counts;

    let current_rosters = roster_map(&edit.rosters());
    let captains = if input.scrape.captain_updates.is_empty() {
        Vec::new()
    } else {
        plan_captain_updates(
            &input.scrape.captain_updates,
            input.matcher,
            &current_rosters,
            input.club_ids,
            &plan.fotmob_team_map,
            input.threshold,
            &plan.fotmob_player_ids,
        )
    };
    for captain in &captains {
        cancel.check(stage)?;
        let previous = edit.team_captain_player(captain.team_id);
        if previous == Some(captain.player_id) {
            continue;
        }
        if edit
            .set_team_captain(captain.team_id, captain.player_id)
            .is_err()
        {
            counts.safety_skipped += 1;
            reporter.say(
                stage,
                format!(
                    "⚠ Captain safety skip {}: {} is not in the current roster or game plan",
                    captain.source.club_name, captain.matched_player_name
                ),
            );
            continue;
        }
        counts.captains += 1;
        reporter.say(
            stage,
            format!(
                "Captain updated: {} → {}",
                captain.source.club_name, captain.matched_player_name
            ),
        );
        applied
            .entries
            .push(captain_entry(captain, previous, input.dry_run));
    }

    reporter.say(
        stage,
        format!(
            "Transfers applied: {}, shirt numbers changed: {}, captains changed: {}, tactical settings changed: {}, formations changed: {}, already current: {}, safety-skipped: {}",
            counts.transfers,
            counts.shirt_numbers,
            counts.captains,
            counts.tactics,
            counts.formations,
            counts.unchanged,
            counts.safety_skipped
        ),
    );
    Ok(applied)
}

/// Classify every shirt update of `team` against the live roster.
fn shirt_batch_for(edit: &EditFile, plan: &Plan, team: u32) -> HashMap<usize, ShirtStatus> {
    let indices: Vec<usize> = plan
        .roster_actions
        .iter()
        .enumerate()
        .filter(|(_, item)| {
            item.action == RosterAction::ShirtUpdate && item.matched.to.team() == Some(team)
        })
        .map(|(index, _)| index)
        .collect();
    let actions: Vec<&crate::planning::PlannedRosterAction> = indices
        .iter()
        .map(|&index| &plan.roster_actions[index])
        .collect();
    let statuses = plan_shirt_batch(edit, &actions, &HashSet::new());
    indices
        .into_iter()
        .zip(statuses)
        .filter_map(|(index, status)| Some((index, status?)))
        .collect()
}

/// Apply a team's clear shirt updates together (swaps allowed), in plan order.
fn sorted_batch_update(
    edit: &mut EditFile,
    team: u32,
    mut updates: Vec<(u32, u32)>,
) -> Result<(), fl_save::roster::RosterError> {
    // HashMap iteration order is arbitrary; the write order must not be.
    updates.sort_unstable();
    edit.update_player_shirt_numbers(team, &updates)
}

/// Re-pick the XI and bench of every club with a live squad snapshot or a
/// roster change by native rating, and repair every game plan. Returns
/// whether any game plan's bytes changed.
fn align_game_plans(
    edit: &mut EditFile,
    input: &ApplyInput<'_>,
    touched: &HashSet<u32>,
    reporter: &mut Reporter<'_>,
) -> bool {
    let mut align_teams = plan_lineup_clubs(
        &input.scrape.squad_snapshots,
        input.club_ids,
        &input.plan.fotmob_team_map,
    );
    align_teams.extend(touched);
    let options = RepairOptions {
        preserve_existing_primary: true,
        align_teams,
    };
    let metrics = edit.repair_game_plans(&options);
    if metrics.changed_game_plans == 0 {
        return false;
    }
    let mut parts: Vec<String> = metrics
        .entries()
        .into_iter()
        .filter(|(key, value)| *key != "checked_game_plans" && *value > 0)
        .map(|(key, value)| format!("{key}={value}"))
        .collect();
    parts.sort();
    reporter.say(
        Stage::Applying,
        format!(
            "Game-plan repairs: {} clubs changed ({})",
            metrics.changed_game_plans,
            parts.join(", ")
        ),
    );
    true
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::matcher::NameMatcher;
    use crate::planning::{MatchedTransfer, PlannedRosterAction, Side};
    use crate::test_support::base_save;

    fn run_plan(
        edit: &mut EditFile,
        actions: Vec<PlannedRosterAction>,
        cancel: &CancelToken,
    ) -> Result<Applied, UpdateError> {
        let plan = Plan {
            roster_actions: actions,
            ..Plan::default()
        };
        let scrape = ScrapeResult::default();
        let matcher = NameMatcher::new(std::iter::empty(), HashMap::new(), std::iter::empty());
        let native = NativeData::default();
        let club_ids: HashSet<u32> = edit.club_team_ids().iter().copied().collect();
        let input = ApplyInput {
            plan: &plan,
            scrape: &scrape,
            matcher: &matcher,
            native: &native,
            club_ids: &club_ids,
            threshold: 80.0,
            allow_overflow_release: true,
            dry_run: false,
        };
        let mut sink = |_: Progress| {};
        apply(edit, &input, cancel, &mut Reporter::new(&mut sink))
    }

    /// A move of a player who is not on the planned source club.
    fn stale_move(edit: &EditFile) -> PlannedRosterAction {
        let clubs: Vec<u32> = edit.club_team_ids().iter().copied().take(3).collect();
        let (from, to, other) = (clubs[0], clubs[1], clubs[2]);
        let player = edit.roster(other).unwrap().roster().next().unwrap();
        let mut matched = MatchedTransfer::new(Default::default());
        matched.player_id = Some(player);
        matched.from = Side::Team(from);
        matched.to = Side::Team(to);
        PlannedRosterAction::new(matched, RosterAction::Move, Some(from))
    }

    #[test]
    fn a_save_that_no_longer_matches_the_plan_skips_the_event() {
        let Some(mut edit) = base_save() else { return };
        let action = stale_move(&edit);
        let applied = run_plan(&mut edit, vec![action], &CancelToken::new()).unwrap();
        assert_eq!(applied.counts.safety_skipped, 1);
        assert_eq!(applied.counts.transfers, 0);
        assert!(applied.entries.is_empty());
        assert_eq!(applied.refused.len(), 1);
        assert_eq!(applied.refused[0].reason, "source_player_missing");
    }

    /// Releasing a club's last goalkeeper (a live squad that lists keepers the
    /// save cannot match) skips that event; it must not fail the whole run.
    #[test]
    fn a_refused_last_goalkeeper_skips_the_event() {
        assert!(is_safe_refusal(RosterErrorKind::LastGoalkeeper));
    }

    #[test]
    fn rounds_add_up_changes_but_the_latest_round_describes_the_save() {
        let counts = |transfers, unchanged, safety_skipped| ChangeCounts {
            transfers,
            unchanged,
            safety_skipped,
            ..ChangeCounts::default()
        };
        let round = |transfers, unchanged, safety_skipped| Applied {
            counts: counts(transfers, unchanged, safety_skipped),
            ..Applied::default()
        };
        let mut total = Applied::default();
        total.absorb(round(5, 100, 7));
        total.absorb(round(3, 108, 4));
        total.absorb(round(0, 111, 4));
        assert_eq!(total.counts, counts(8, 111, 4));
        assert!(total.is_effective());
        assert!(!round(0, 1, 0).is_effective());
    }

    #[test]
    fn cancellation_stops_applying() {
        let Some(mut edit) = base_save() else { return };
        let action = stale_move(&edit);
        let before = edit.bytes().to_vec();
        let token = CancelToken::new();
        token.request();
        let error = run_plan(&mut edit, vec![action], &token).unwrap_err();
        assert_eq!(error.code, ErrorCode::Cancelled);
        assert_eq!(edit.bytes(), before, "nothing was applied");
    }
}
