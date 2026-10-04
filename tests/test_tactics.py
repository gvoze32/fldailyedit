from datetime import datetime, timezone

from scraper.models import SquadSnapshot
from scraper.tactics import (
    _CACHE_VERSION,
    _league_decisions,
    _parse_metric_table,
    _quartile_decisions,
    _save_cache,
    fetch_fotmob_tactical_updates,
)


def _league_teams():
    return {
        str(team_id): {
            "possession": {"value": 30 + index * 5, "matches": 6},
            "passes": {"value": 500, "matches": 6},
            "long_balls": {"value": 20 + index * 10, "matches": 6},
            "crosses": {"value": 5 + index * 5, "matches": 6},
            "final_third_wins": {"value": index, "matches": 6},
        }
        for index, team_id in enumerate(range(101, 109))
    }


def test_league_tactics_follow_extreme_current_season_rates():
    teams = _league_teams()

    decisions = _league_decisions({"teams": teams})

    assert decisions[101] == (
        {
            "attacking_style": 0,
            "build_up": 1,
            "attacking_area": 1,
            "defensive_style": 1,
            "containment_area": 0,
            "pressuring": 1,
            "defensive_line": 1,
        },
        6,
    )
    assert decisions[108] == (
        {
            "attacking_style": 1,
            "build_up": 0,
            "attacking_area": 0,
            "defensive_style": 0,
            "containment_area": 1,
            "pressuring": 0,
            "defensive_line": 10,
        },
        6,
    )
    assert decisions[104] == (
        {"defensive_line": 5},
        6,
    )


    assert [
        decisions[team_id][0]["defensive_line"]
        for team_id in range(101, 109)
    ] == [1, 2, 4, 5, 6, 7, 9, 10]
    # No independent FotMob metric exists for compactness: never derived.
    assert all(
        "compactness" not in settings for settings, _ in decisions.values()
    )

def test_slider_decisions_require_eight_distinct_current_season_profiles():
    teams = _league_teams()
    del teams["108"]["final_third_wins"]
    insufficient_teams = _league_decisions({"teams": teams})
    assert all(
        "defensive_line" not in settings and "compactness" not in settings
        for settings, _ in insufficient_teams.values()
    )

    teams = _league_teams()
    for profile in teams.values():
        profile["final_third_wins"]["value"] = 4
    uniform_profiles = _league_decisions({"teams": teams})
    assert all(
        "defensive_line" not in settings and "compactness" not in settings
        for settings, _ in uniform_profiles.values()
    )


def test_tactical_evidence_requires_minimum_matches_and_distinct_quartiles():
    teams = _league_teams()
    teams["101"]["possession"]["matches"] = 2

    decisions = _league_decisions({"teams": teams})

    assert "attacking_style" not in decisions[101][0]
    assert "attacking_style" not in decisions[108][0]
    assert decisions[101][0]["build_up"] == 1
    assert _quartile_decisions(
        {team_id: 1.0 for team_id in range(101, 109)},
        low_value=0,
        high_value=1,
    ) == {}
    assert _quartile_decisions(
        {team_id: float(team_id) for team_id in range(101, 108)},
        low_value=0,
        high_value=1,
    ) == {}


def test_cached_fotmob_league_evidence_builds_public_tactical_update(tmp_path):
    now = datetime(2026, 2, 9, tzinfo=timezone.utc)
    source_url = "https://data.fotmob.com/stats/example/teams.json"
    cache_path = tmp_path / "tactics-cache.json"
    _save_cache(
        cache_path,
        {
            "version": _CACHE_VERSION,
            "leagues": {
                "10": {
                    "season": "2025/26",
                    "fetched_at": now.isoformat(),
                    "teams": _league_teams(),
                    "source_urls": [source_url],
                }
            },
        },
    )

    updates = fetch_fotmob_tactical_updates(
        (
            SquadSnapshot(
                club_name="Example FC",
                team_id_fotmob=101,
                members=(),
                source_url="https://www.fotmob.com/teams/101",
                primary_league_id=10,
            ),
        ),
        cache_path=cache_path,
        now=now,
    )

    assert len(updates) == 1
    assert updates[0].club_name == "Example FC"
    assert updates[0].league_id == 10
    assert dict(updates[0].settings) == {
        "attacking_style": 0,
        "build_up": 1,
        "attacking_area": 1,
        "defensive_style": 1,
        "containment_area": 0,
        "pressuring": 1,
        "defensive_line": 1,
    }
    assert updates[0].sample_matches == 6
    assert updates[0].source_urls == (source_url,)


def test_fotmob_metric_rows_reject_missing_or_invalid_observations():
    rows = _parse_metric_table(
        {
            "TopLists": [
                {
                    "StatList": [
                        {
                            "TeamId": "101",
                            "StatValue": "54.5",
                            "MatchesPlayed": 8,
                        },
                        {
                            "TeamId": "bad-id",
                            "StatValue": "42",
                            "MatchesPlayed": 8,
                        },
                        {
                            "TeamId": "102",
                            "StatValue": "NaN",
                            "MatchesPlayed": 8,
                        },
                    ]
                }
            ]
        }
    )

    assert rows == {101: {"value": 54.5, "matches": 8}}
