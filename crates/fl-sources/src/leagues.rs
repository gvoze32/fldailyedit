//! FotMob league membership: which league a team plays in this season, and
//! which teams a league has this season.
//!
//! Both come from the endpoints the rest of the app already uses (team and
//! league payloads), so a team's `primaryLeagueId` and a league's current
//! table agree with what FotMob shows today. Anything that does not look
//! like one plain, current-season league table is refused rather than guessed.

use std::collections::BTreeMap;

use futures_util::future::join_all;
use futures_util::stream::{self, StreamExt};
use serde_json::Value;

use crate::fotmob::FotmobError;
use crate::http::HttpClient;
use crate::lenient::{lenient_int, str_or_empty};
use crate::tactics::{LEAGUE_URL, get_object};

const TEAM_URL: &str = "https://www.fotmob.com/api/data/teams";
/// Team pages fetched at the same time.
const TEAM_CONCURRENCY: usize = 4;

/// The league FotMob lists a team under right now.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TeamLeague {
    pub team_id: i64,
    pub league_id: i64,
    /// FotMob country code of the team (`ENG`), empty when absent.
    pub country: String,
}

/// `details.primaryLeagueId` and `details.country` of a team payload.
pub fn parse_team_league(team_id: i64, payload: &Value) -> Option<TeamLeague> {
    let details = payload.get("details")?;
    let league_id = details
        .get("primaryLeagueId")
        .and_then(lenient_int)
        .filter(|id| *id > 0)?;
    Some(TeamLeague {
        team_id,
        league_id,
        country: str_or_empty(details.get("country")).trim().to_owned(),
    })
}

/// Current league of each of `team_ids`, in request order. A team whose page
/// cannot be read or names no league is left out (the caller decides what a
/// thin sample is worth); only cancellation is an error.
pub async fn fetch_team_leagues(
    client: &HttpClient,
    team_ids: &[i64],
) -> Result<Vec<TeamLeague>, FotmobError> {
    let results: Vec<(i64, Result<Value, FotmobError>)> = stream::iter(team_ids.iter().copied())
        .map(|team_id| async move {
            let url = format!("{TEAM_URL}?id={team_id}");
            (team_id, get_object(client, &url).await)
        })
        .buffered(TEAM_CONCURRENCY)
        .collect()
        .await;
    let mut leagues = Vec::with_capacity(results.len());
    for (team_id, result) in results {
        match result {
            Ok(payload) => leagues.extend(parse_team_league(team_id, &payload)),
            Err(FotmobError::Cancelled) => return Err(FotmobError::Cancelled),
            Err(error) => tracing::warn!("FotMob team {team_id} has no league info: {error}"),
        }
    }
    Ok(leagues)
}

/// A league's teams for the current season.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LeagueTeams {
    pub league_id: i64,
    pub name: String,
    pub country: String,
    pub season: String,
    /// FotMob team id → team name.
    pub teams: BTreeMap<i64, String>,
}

/// The teams of a plain league table. Refused: a season that is not the
/// latest one, leagues with several tables (groups, play-offs, conferences,
/// Apertura/Clausura) and empty tables.
pub fn parse_league_teams(payload: &Value) -> Result<LeagueTeams, String> {
    let details = payload.get("details").ok_or("no league details")?;
    let league_id = details
        .get("id")
        .and_then(lenient_int)
        .filter(|id| *id > 0)
        .ok_or("no league id")?;
    let name = str_or_empty(details.get("name")).trim().to_owned();
    let season = str_or_empty(details.get("selectedSeason"));
    let latest = str_or_empty(details.get("latestSeason"));
    if season.is_empty() || season != latest {
        return Err(format!(
            "league {league_id} shows season '{season}', not the current '{latest}'"
        ));
    }
    let data = match payload
        .get("table")
        .and_then(Value::as_array)
        .map(Vec::as_slice)
    {
        Some([only]) => only.get("data").ok_or("league table has no data")?,
        _ => return Err(format!("league {league_id} has no single table")),
    };
    if data.get("composite").and_then(Value::as_bool) == Some(true) || data.get("tables").is_some()
    {
        return Err(format!("league {league_id} is split into several tables"));
    }
    if data.get("isCurrentSeason").and_then(Value::as_bool) == Some(false) {
        return Err(format!(
            "league {league_id} table is not the current season"
        ));
    }
    let rows = data
        .get("table")
        .and_then(|table| table.get("all"))
        .and_then(Value::as_array)
        .filter(|rows| !rows.is_empty())
        .ok_or_else(|| format!("league {league_id} has an empty table"))?;
    let mut teams = BTreeMap::new();
    for row in rows {
        let id = row
            .get("id")
            .and_then(lenient_int)
            .filter(|id| *id > 0)
            .ok_or_else(|| format!("league {league_id} table row without a team id"))?;
        teams.insert(id, str_or_empty(row.get("name")).trim().to_owned());
    }
    Ok(LeagueTeams {
        league_id,
        name,
        country: str_or_empty(details.get("country")).trim().to_owned(),
        season,
        teams,
    })
}

/// Fetch one league's current-season teams. A payload that is not a plain
/// league table is [`FotmobError::Incomplete`].
async fn fetch_league_teams(
    client: &HttpClient,
    league_id: i64,
) -> Result<LeagueTeams, FotmobError> {
    let url = format!("{LEAGUE_URL}?id={league_id}&ccode3=GBR");
    let payload = get_object(client, &url).await?;
    parse_league_teams(&payload).map_err(FotmobError::Incomplete)
}

/// Fetch the current-season teams of several leagues at once, in request
/// order. Each league succeeds or fails on its own; a payload that is not a
/// plain league table is [`FotmobError::Incomplete`].
pub async fn fetch_leagues_teams(
    client: &HttpClient,
    league_ids: &[i64],
) -> Vec<Result<LeagueTeams, FotmobError>> {
    join_all(league_ids.iter().map(|&id| fetch_league_teams(client, id))).await
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn league(rows: Value) -> Value {
        json!({
            "details": {"id": 47, "name": "Premier League", "country": "ENG",
                        "selectedSeason": "2026/2027", "latestSeason": "2026/2027"},
            "table": [{"data": {"isCurrentSeason": true, "composite": false,
                                "table": {"all": rows}}}],
        })
    }

    #[test]
    fn a_plain_current_table_lists_its_teams() {
        let payload = league(json!([
            {"id": 9825, "name": "Arsenal"},
            {"id": "8667", "name": " Hull City "},
        ]));
        let parsed = parse_league_teams(&payload).unwrap();
        assert_eq!(parsed.league_id, 47);
        assert_eq!(parsed.country, "ENG");
        assert_eq!(
            parsed.teams.into_iter().collect::<Vec<_>>(),
            vec![(8667, "Hull City".to_owned()), (9825, "Arsenal".to_owned())]
        );
    }

    #[test]
    fn leagues_that_are_not_one_current_table_are_refused() {
        let mut old_season = league(json!([{"id": 1, "name": "A"}]));
        old_season["details"]["selectedSeason"] = json!("2025/2026");
        let mut composite = league(json!([{"id": 1, "name": "A"}]));
        composite["table"][0]["data"]["composite"] = json!(true);
        let mut grouped = league(json!([{"id": 1, "name": "A"}]));
        grouped["table"][0]["data"]["tables"] = json!([]);
        let mut two_tables = league(json!([{"id": 1, "name": "A"}]));
        two_tables["table"] = json!([{"data": {}}, {"data": {}}]);
        for payload in [
            old_season,
            composite,
            grouped,
            two_tables,
            league(json!([])),
            league(json!([{"name": "No id"}])),
            json!({}),
        ] {
            assert!(parse_league_teams(&payload).is_err(), "{payload}");
        }
    }

    #[test]
    fn a_team_names_its_primary_league_and_country() {
        let payload = json!({"details": {"primaryLeagueId": 48, "country": "ENG"}});
        assert_eq!(
            parse_team_league(8602, &payload),
            Some(TeamLeague {
                team_id: 8602,
                league_id: 48,
                country: "ENG".into()
            })
        );
        assert_eq!(parse_team_league(1, &json!({"details": {}})), None);
        assert_eq!(parse_team_league(1, &json!({})), None);
    }
}
