//! Promotion and relegation: clubs that play in a different league this
//! season are moved to the matching division of the save.
//!
//! The save's league membership is a list of divisions (Premier League,
//! Championship, ...). FotMob knows today's leagues. Each division is matched
//! to its FotMob league through the current league of a few of its bound
//! clubs, then confirmed against that league's current team list. Only
//! neighbouring divisions of one country are ever paired, and a club that is
//! now in the upper league but sits in the lower division swaps places with a
//! club that dropped the other way, so every division keeps its size. Clubs
//! the save does not have are never created; anything ambiguous, unpaired or
//! odd leaves its pair of divisions as it was and says why.

use std::collections::{BTreeSet, HashMap};

use fl_save::edit_file::EditFile;
use fl_sources::fotmob::FotmobError;
use fl_sources::leagues::{LeagueTeams, TeamLeague, fetch_leagues_teams, fetch_team_leagues};

use super::Reporter;
use crate::api::{CancelToken, ErrorCode, Stage, UpdateError};
use crate::club_identity::ClubIdentityIndex;
use crate::report::LeagueMove;
use crate::services::Services;

/// Clubs asked about their league per division.
const SAMPLES_PER_DIVISION: usize = 5;
/// Fewest answers a division needs before its country or league is trusted.
const MIN_ANSWERS: usize = 3;
/// Share of the answers (and of the division's clubs) that must agree.
const MIN_AGREEMENT: f64 = 0.6;
/// Leagues tried per division, most voted first.
const MAX_CANDIDATES: usize = 3;
/// Divisions outside this size are cups and mixed groups, not leagues.
const DIVISION_SIZES: std::ops::RangeInclusive<usize> = 8..=40;
/// Fewest clubs of a division that must be bound to FotMob.
const MIN_BOUND: usize = 8;
/// More clubs than this changing between two divisions means the data is
/// wrong, not that a season ended.
const MAX_SWAPS_PER_PAIR: usize = 6;

/// A division matched to a FotMob league.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct ResolvedLeague {
    pub(crate) id: i64,
    pub(crate) name: String,
    pub(crate) country: String,
    /// FotMob ids of the league's teams this season.
    pub(crate) teams: BTreeSet<i64>,
}

/// One club moving up and one moving down between neighbouring divisions.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct Swap {
    pub(crate) upper: usize,
    pub(crate) lower: usize,
    /// Save club promoted from the lower to the upper division.
    pub(crate) up: u32,
    /// Save club relegated from the upper to the lower division.
    pub(crate) down: u32,
}

#[derive(Debug, Default, PartialEq, Eq)]
pub(crate) struct SwapPlan {
    pub(crate) swaps: Vec<Swap>,
    pub(crate) warnings: Vec<String>,
}

/// What the league step did.
#[derive(Debug, Default)]
pub(crate) struct Moved {
    pub(crate) moves: Vec<LeagueMove>,
    pub(crate) warnings: Vec<String>,
}

/// `count` ids spread evenly over `ids` (all of them when there are few).
fn pick_samples(ids: &[i64], count: usize) -> Vec<i64> {
    if ids.len() <= count {
        return ids.to_vec();
    }
    (0..count)
        .map(|k| ids[k * (ids.len() - 1) / (count - 1)])
        .collect()
}

/// The country most of `answers` agree on, if enough of them answered.
fn agreed_country(answers: &[&TeamLeague]) -> Option<String> {
    if answers.len() < MIN_ANSWERS {
        return None;
    }
    let mut votes: HashMap<&str, usize> = HashMap::new();
    for answer in answers.iter().filter(|a| !a.country.is_empty()) {
        *votes.entry(&answer.country).or_default() += 1;
    }
    let (country, count) = votes
        .into_iter()
        .max_by(|a, b| a.1.cmp(&b.1).then(b.0.cmp(a.0)))?;
    (count as f64 >= MIN_AGREEMENT * answers.len() as f64).then(|| country.to_owned())
}

/// League ids named by `answers`, most voted first (ties by id).
fn ranked_leagues(answers: &[&TeamLeague]) -> Vec<i64> {
    let mut votes: HashMap<i64, usize> = HashMap::new();
    for answer in answers {
        *votes.entry(answer.league_id).or_default() += 1;
    }
    let mut ranked: Vec<(i64, usize)> = votes.into_iter().collect();
    ranked.sort_by(|a, b| b.1.cmp(&a.1).then(a.0.cmp(&b.0)));
    ranked.into_iter().map(|(id, _)| id).collect()
}

/// Whether enough of the division's bound clubs play in `league` today.
fn confirms(bound: &[i64], league: &LeagueTeams) -> bool {
    let inside = bound
        .iter()
        .filter(|id| league.teams.contains_key(id))
        .count();
    bound.len() >= MIN_BOUND && inside as f64 >= MIN_AGREEMENT * bound.len() as f64
}

/// Which divisions are worth asking about: leagues of a plausible size with
/// enough clubs bound to FotMob. Index → the bound FotMob ids, in slot order.
fn bound_clubs(
    divisions: &[Vec<u32>],
    fotmob_of: &dyn Fn(u32) -> Option<i64>,
) -> Vec<Option<Vec<i64>>> {
    divisions
        .iter()
        .map(|members| {
            let bound: Vec<i64> = members.iter().filter_map(|&id| fotmob_of(id)).collect();
            (DIVISION_SIZES.contains(&members.len()) && bound.len() >= MIN_BOUND).then_some(bound)
        })
        .collect()
}

/// Pair every division with its neighbour when both resolved to different
/// leagues of one country, and swap the clubs that changed league.
pub(crate) fn plan_swaps(
    divisions: &[Vec<u32>],
    resolved: &[Option<ResolvedLeague>],
    fotmob_of: &dyn Fn(u32) -> Option<i64>,
) -> SwapPlan {
    let mut plan = SwapPlan::default();
    for upper in 0..divisions.len().saturating_sub(1) {
        let lower = upper + 1;
        let (Some(high), Some(low)) = (&resolved[upper], &resolved[lower]) else {
            continue;
        };
        if high.id == low.id || high.country.is_empty() || high.country != low.country {
            continue;
        }
        let up: Vec<u32> = divisions[lower]
            .iter()
            .copied()
            .filter(|&id| {
                fotmob_of(id).is_some_and(|fm| high.teams.contains(&fm) && !low.teams.contains(&fm))
            })
            .collect();
        let down: Vec<u32> = divisions[upper]
            .iter()
            .copied()
            .filter(|&id| {
                fotmob_of(id).is_some_and(|fm| low.teams.contains(&fm) && !high.teams.contains(&fm))
            })
            .collect();
        if up.is_empty() && down.is_empty() {
            continue;
        }
        let pair = format!("{} / {}", high.name, low.name);
        if up.len() != down.len() {
            plan.warnings.push(format!(
                "League update skipped for {pair}: {} clubs now play in {} but {} dropped to {}, \
                 so the swap would not balance",
                up.len(),
                high.name,
                down.len(),
                low.name,
            ));
            continue;
        }
        if up.len() > MAX_SWAPS_PER_PAIR {
            plan.warnings.push(format!(
                "League update skipped for {pair}: {} clubs would change place, which looks wrong",
                up.len()
            ));
            continue;
        }
        plan.swaps
            .extend(up.into_iter().zip(down).map(|(up, down)| Swap {
                upper,
                lower,
                up,
                down,
            }));
    }
    plan
}

/// `divisions` after every swap: the promoted club takes the relegated
/// club's slot and the other way round.
pub(crate) fn apply_swaps(divisions: &[Vec<u32>], swaps: &[Swap]) -> Vec<Vec<u32>> {
    let mut result = divisions.to_vec();
    for swap in swaps {
        let from_lower = result[swap.lower].iter().position(|&id| id == swap.up);
        let from_upper = result[swap.upper].iter().position(|&id| id == swap.down);
        if let (Some(low_at), Some(high_at)) = (from_lower, from_upper) {
            result[swap.lower][low_at] = swap.down;
            result[swap.upper][high_at] = swap.up;
        }
    }
    result
}

fn fetch_failure(error: FotmobError) -> UpdateError {
    match error {
        FotmobError::Cancelled => UpdateError {
            code: ErrorCode::Cancelled,
            stage: Some(Stage::Fetching),
            message: "Update cancelled".into(),
        },
        FotmobError::Incomplete(message) => UpdateError {
            code: ErrorCode::FetchFailed,
            stage: Some(Stage::Fetching),
            message,
        },
    }
}

/// Match divisions to leagues and move the clubs that changed league.
/// Network trouble and ambiguity are warnings, never a failed run; only
/// cancellation is an error. With `dry_run` the in-memory save is still
/// changed (it is never written).
pub(crate) fn run(
    edit: &mut EditFile,
    identity: &ClubIdentityIndex,
    services: &Services,
    reporter: &mut Reporter<'_>,
    cancel: &CancelToken,
    dry_run: bool,
) -> Result<Moved, UpdateError> {
    let mut moved = Moved::default();
    let divisions: Vec<Vec<u32>> = edit.league_divisions().to_vec();
    let fotmob_of = |team: u32| identity.fotmob_for_pes(team);
    let bound = bound_clubs(&divisions, &fotmob_of);
    if bound.iter().all(Option::is_none) {
        moved.warnings.push(
            "League update skipped: no league division has enough clubs matched to FotMob".into(),
        );
        return Ok(moved);
    }

    let http = services.http_client(cancel, Stage::Fetching)?;
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .map_err(|error| UpdateError {
            code: ErrorCode::Internal,
            stage: Some(Stage::Fetching),
            message: format!("Could not start the async runtime: {error}"),
        })?;

    // Which league does each division play in? Ask a few clubs.
    reporter.say(
        Stage::Fetching,
        "Checking which league each division plays in",
    );
    let mut samples: Vec<Vec<i64>> = Vec::with_capacity(divisions.len());
    for clubs in &bound {
        samples.push(
            clubs
                .as_deref()
                .map_or_else(Vec::new, |ids| pick_samples(ids, SAMPLES_PER_DIVISION)),
        );
    }
    let mut to_ask: Vec<i64> = samples.iter().flatten().copied().collect();
    to_ask.sort_unstable();
    to_ask.dedup();
    let answers: HashMap<i64, TeamLeague> = runtime
        .block_on(fetch_team_leagues(&http, &to_ask))
        .map_err(fetch_failure)?
        .into_iter()
        .map(|answer| (answer.team_id, answer))
        .collect();
    cancel.check(Stage::Fetching)?;
    if answers.len() * 2 < to_ask.len() {
        moved.warnings.push(format!(
            "League update skipped: FotMob answered for only {} of {} clubs, not enough to tell the leagues apart",
            answers.len(),
            to_ask.len()
        ));
        return Ok(moved);
    }

    struct Candidate {
        country: String,
        leagues: Vec<i64>,
    }
    let candidates: Vec<Option<Candidate>> = samples
        .iter()
        .map(|ids| {
            let found: Vec<&TeamLeague> = ids.iter().filter_map(|id| answers.get(id)).collect();
            let country = agreed_country(&found)?;
            let mut leagues = ranked_leagues(&found);
            leagues.truncate(MAX_CANDIDATES);
            Some(Candidate { country, leagues })
        })
        .collect();

    // Only divisions with a same-country neighbour can swap anyone.
    let paired: Vec<bool> = (0..divisions.len())
        .map(|i| {
            let same = |j: usize| match (&candidates[i], &candidates[j]) {
                (Some(a), Some(b)) => a.country == b.country,
                _ => false,
            };
            (i > 0 && same(i - 1)) || (i + 1 < divisions.len() && same(i + 1))
        })
        .collect();
    let mut wanted: Vec<i64> = candidates
        .iter()
        .zip(&paired)
        .filter(|(_, paired)| **paired)
        .flat_map(|(candidate, _)| candidate.iter().flat_map(|c| c.leagues.iter().copied()))
        .collect();
    wanted.sort_unstable();
    wanted.dedup();
    if wanted.is_empty() {
        reporter.say(
            Stage::Fetching,
            "No division has a neighbour in the same country",
        );
        return Ok(moved);
    }

    reporter.say(
        Stage::Fetching,
        format!("Fetching the current teams of {} leagues", wanted.len()),
    );
    let fetched = runtime.block_on(fetch_leagues_teams(&http, &wanted));
    cancel.check(Stage::Fetching)?;
    let mut tables: HashMap<i64, LeagueTeams> = HashMap::new();
    // Leagues that are no plain table (or failed to load): only worth a
    // word when a division needed them.
    let mut refused: HashMap<i64, String> = HashMap::new();
    for (id, result) in wanted.iter().zip(fetched) {
        match result {
            Ok(table) => {
                tables.insert(*id, table);
            }
            Err(FotmobError::Cancelled) => return Err(fetch_failure(FotmobError::Cancelled)),
            Err(error) => {
                refused.insert(*id, error.to_string());
            }
        }
    }

    let team_names: HashMap<u32, String> = edit
        .teams()
        .into_iter()
        .map(|team| (team.team_id, team.name))
        .collect();
    let label = |index: usize| -> String {
        let first = divisions[index]
            .first()
            .and_then(|id| team_names.get(id))
            .map_or("?", String::as_str);
        format!("the division of {first}")
    };
    let mut resolved: Vec<Option<ResolvedLeague>> = vec![None; divisions.len()];
    for index in 0..divisions.len() {
        if !paired[index] {
            continue;
        }
        let (Some(candidate), Some(clubs)) = (&candidates[index], &bound[index]) else {
            continue;
        };
        let found = candidate.leagues.iter().find_map(|id| {
            tables
                .get(id)
                .filter(|table| table.country == candidate.country && confirms(clubs, table))
        });
        match found {
            Some(table) => {
                reporter.say(
                    Stage::Fetching,
                    format!(
                        "{} plays in {} ({})",
                        label(index),
                        table.name,
                        table.country
                    ),
                );
                resolved[index] = Some(ResolvedLeague {
                    id: table.league_id,
                    name: table.name.clone(),
                    country: table.country.clone(),
                    teams: table.teams.keys().copied().collect(),
                });
            }
            None => {
                let reasons: Vec<String> = candidate
                    .leagues
                    .iter()
                    .filter_map(|id| refused.get(id))
                    .cloned()
                    .collect();
                let detail = if reasons.is_empty() {
                    String::new()
                } else {
                    format!(" ({})", reasons.join("; "))
                };
                moved.warnings.push(format!(
                    "League update skipped for {}: FotMob's current leagues do not clearly match it{detail}",
                    label(index)
                ));
            }
        }
    }

    reporter.say(
        Stage::Applying,
        "Planning league promotions and relegations",
    );
    let plan = plan_swaps(&divisions, &resolved, &fotmob_of);
    moved.warnings.extend(plan.warnings);
    if plan.swaps.is_empty() {
        reporter.say(Stage::Applying, "No club changed league");
        return Ok(moved);
    }
    let updated = apply_swaps(&divisions, &plan.swaps);
    if let Err(error) = edit.set_league_divisions(&updated) {
        moved
            .warnings
            .push(format!("League update skipped: {error}"));
        return Ok(moved);
    }

    let name_of = |team: u32| {
        team_names
            .get(&team)
            .cloned()
            .unwrap_or_else(|| format!("#{team}"))
    };
    for swap in &plan.swaps {
        let (Some(high), Some(low)) = (&resolved[swap.upper], &resolved[swap.lower]) else {
            continue;
        };
        for (team, from, to) in [
            (swap.up, &low.name, &high.name),
            (swap.down, &high.name, &low.name),
        ] {
            reporter.say(
                Stage::Applying,
                format!("League move: {} from {from} to {to}", name_of(team)),
            );
            moved.moves.push(LeagueMove {
                club: name_of(team),
                from: from.clone(),
                to: to.clone(),
                dry_run,
            });
        }
    }
    Ok(moved)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn league(id: i64, name: &str, country: &str, teams: &[i64]) -> Option<ResolvedLeague> {
        Some(ResolvedLeague {
            id,
            name: name.into(),
            country: country.into(),
            teams: teams.iter().copied().collect(),
        })
    }

    /// Save club `n` is FotMob team `1000 + n`; 99 is not bound.
    fn fotmob_of(team: u32) -> Option<i64> {
        (team != 99).then_some(i64::from(team) + 1000)
    }

    fn fm(ids: &[i64]) -> Vec<i64> {
        ids.iter().map(|id| id + 1000).collect()
    }

    #[test]
    fn promoted_and_relegated_clubs_trade_slots_and_sizes_hold() {
        // Premier League 1..=4, Championship 5..=9. Club 3 dropped, 7 came up.
        let divisions = vec![vec![1, 2, 3, 4], vec![5, 6, 7, 8, 9]];
        let resolved = vec![
            league(47, "Premier League", "ENG", &fm(&[1, 2, 4, 7])),
            league(48, "Championship", "ENG", &fm(&[3, 5, 6, 8, 9])),
        ];
        let plan = plan_swaps(&divisions, &resolved, &fotmob_of);
        assert!(plan.warnings.is_empty(), "{:?}", plan.warnings);
        assert_eq!(
            plan.swaps,
            vec![Swap {
                upper: 0,
                lower: 1,
                up: 7,
                down: 3
            }]
        );
        let updated = apply_swaps(&divisions, &plan.swaps);
        assert_eq!(updated, vec![vec![1, 2, 7, 4], vec![5, 6, 3, 8, 9]]);
        assert_eq!(
            updated.iter().map(Vec::len).collect::<Vec<_>>(),
            vec![4, 5],
            "sizes are unchanged"
        );
    }

    #[test]
    fn several_swaps_pair_in_slot_order() {
        let divisions = vec![vec![1, 2, 3, 4], vec![5, 6, 7, 8]];
        let resolved = vec![
            league(1, "Top", "ENG", &fm(&[3, 4, 5, 6])),
            league(2, "Second", "ENG", &fm(&[1, 2, 7, 8])),
        ];
        let plan = plan_swaps(&divisions, &resolved, &fotmob_of);
        let pairs: Vec<(u32, u32)> = plan.swaps.iter().map(|s| (s.up, s.down)).collect();
        assert_eq!(pairs, vec![(5, 1), (6, 2)]);
        assert_eq!(
            apply_swaps(&divisions, &plan.swaps),
            vec![vec![5, 6, 3, 4], vec![1, 2, 7, 8]]
        );
    }

    #[test]
    fn an_unbalanced_pair_is_skipped_with_a_warning() {
        // Two clubs now play in the top league but only one dropped: the
        // other relegated club is not in the save.
        let divisions = vec![vec![1, 2, 3], vec![5, 6, 7]];
        let resolved = vec![
            league(1, "Top", "ENG", &fm(&[1, 2, 5, 6])),
            league(2, "Second", "ENG", &fm(&[3, 7])),
        ];
        let plan = plan_swaps(&divisions, &resolved, &fotmob_of);
        assert!(plan.swaps.is_empty());
        assert_eq!(plan.warnings.len(), 1);
        assert!(
            plan.warnings[0].contains("Top / Second"),
            "{:?}",
            plan.warnings
        );
    }

    #[test]
    fn unbound_clubs_are_never_moved() {
        // Club 99 has no FotMob id: it stays, so one promotion has no partner.
        let divisions = vec![vec![1, 99], vec![5, 6]];
        let resolved = vec![
            league(1, "Top", "ENG", &fm(&[1, 5])),
            league(2, "Second", "ENG", &fm(&[6])),
        ];
        let plan = plan_swaps(&divisions, &resolved, &fotmob_of);
        assert!(plan.swaps.is_empty());
        assert_eq!(plan.warnings.len(), 1);
    }

    #[test]
    fn only_neighbouring_divisions_of_one_country_are_paired() {
        let divisions = vec![vec![1, 2], vec![5, 6], vec![9, 10]];
        // Club 5 plays in the top league now, but the neighbours are in
        // different countries; the unresolved third division is ignored.
        let resolved = vec![
            league(1, "Top", "ENG", &fm(&[1, 5])),
            league(2, "Other", "FRA", &fm(&[2, 6])),
            None,
        ];
        let plan = plan_swaps(&divisions, &resolved, &fotmob_of);
        assert_eq!(plan, SwapPlan::default());
    }

    #[test]
    fn an_implausible_number_of_changes_is_refused() {
        let upper: Vec<u32> = (1..=8).collect();
        let lower: Vec<u32> = (11..=18).collect();
        let divisions = vec![upper.clone(), lower.clone()];
        let resolved = vec![
            league(
                1,
                "Top",
                "ENG",
                &fm(&lower.iter().map(|&id| i64::from(id)).collect::<Vec<_>>()),
            ),
            league(
                2,
                "Second",
                "ENG",
                &fm(&upper.iter().map(|&id| i64::from(id)).collect::<Vec<_>>()),
            ),
        ];
        let plan = plan_swaps(&divisions, &resolved, &fotmob_of);
        assert!(plan.swaps.is_empty());
        assert!(plan.warnings[0].contains("looks wrong"));
    }

    #[test]
    fn samples_spread_over_the_division() {
        let ids: Vec<i64> = (0..20).collect();
        assert_eq!(pick_samples(&ids, 5), vec![0, 4, 9, 14, 19]);
        assert_eq!(pick_samples(&ids[..3], 5), vec![0, 1, 2]);
    }

    #[test]
    fn a_division_needs_a_clear_majority_for_country_and_league() {
        let answer = |team_id, league_id, country: &str| TeamLeague {
            team_id,
            league_id,
            country: country.into(),
        };
        let mixed = [
            answer(1, 47, "ENG"),
            answer(2, 47, "ENG"),
            answer(3, 48, "ENG"),
            answer(4, 87, "ESP"),
            answer(5, 87, "ESP"),
        ];
        let refs: Vec<&TeamLeague> = mixed.iter().collect();
        assert_eq!(
            agreed_country(&refs).as_deref(),
            Some("ENG"),
            "3 of 5 is exactly the bar"
        );
        assert_eq!(ranked_leagues(&refs), vec![47, 87, 48]);
        assert_eq!(agreed_country(&refs[..2]), None, "too few answers");
        let split = [
            answer(1, 47, "ENG"),
            answer(2, 47, "ENG"),
            answer(4, 87, "ESP"),
            answer(5, 87, "ESP"),
            answer(6, 53, "FRA"),
        ];
        let refs: Vec<&TeamLeague> = split.iter().collect();
        assert_eq!(agreed_country(&refs), None);
    }

    #[test]
    fn a_league_must_contain_most_of_the_divisions_bound_clubs() {
        let table = |ids: &[i64]| LeagueTeams {
            league_id: 1,
            name: "L".into(),
            country: "ENG".into(),
            season: "2026/2027".into(),
            teams: ids.iter().map(|&id| (id, String::new())).collect(),
        };
        let bound: Vec<i64> = (1..=10).collect();
        assert!(confirms(&bound, &table(&[1, 2, 3, 4, 5, 6, 77])));
        assert!(!confirms(&bound, &table(&[1, 2, 3, 4, 5, 77, 78])));
        assert!(
            !confirms(&bound[..5], &table(&[1, 2, 3, 4, 5])),
            "too few bound clubs"
        );
    }
}
