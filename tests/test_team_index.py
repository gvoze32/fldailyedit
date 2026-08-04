"""Regression tests for fail-closed FotMob/PES club identity generation."""

import pytest

from scraper.validate_teams import build_validated_club_index


def _team(team_id: int, name: str) -> dict:
    return {
        "fotmob_id": team_id,
        "name": name,
        "slug": name.lower().replace(" ", "-"),
        "url": f"https://www.fotmob.com/teams/{team_id}/overview/x",
    }


def test_curated_identity_wins_and_ambiguous_names_are_rejected():
    pes = {
        1: "Juventus FC",
        2: "Alpha Club",
        3: "Beta FC",
        4: "Gamma FC",
        5: "Racing Santander",
        6: "Delta FC",
    }
    fotmob = [
        _team(10, "Juventus"),
        _team(11, "Juventus FC"),
        _team(20, "Alpha Club"),
        _team(30, "Beta FC"),
        _team(31, "Beta FC"),
        _team(40, "Gamma Women"),
        _team(50, "Racing"),
        _team(51, "Racing Santander"),
        _team(60, "Delta FC"),
        _team(600_000, "Delta FC"),
    ]

    result = build_validated_club_index(pes, fotmob, {"Juventus": 10})
    by_pes_id = {item["pes_team_id"]: item for item in result}

    assert by_pes_id[1]["fotmob_id"] == 10
    assert by_pes_id[1]["identity_source"] == "major_clubs"
    assert by_pes_id[2]["fotmob_id"] == 20
    assert 3 not in by_pes_id
    assert 4 not in by_pes_id
    assert by_pes_id[5]["fotmob_id"] == 51
    assert by_pes_id[6]["fotmob_id"] == 60
    assert all(item["fotmob_id"] != 11 for item in result)


def test_sitemap_crawl_never_overwrites_index_after_partial_failure(
    monkeypatch, tmp_path
):
    import config
    from scraper import fotmob_teams

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(fotmob_teams.time, "sleep", lambda _: None)
    (tmp_path / "fotmob_teams.json").write_text("old-json", encoding="utf-8")
    (tmp_path / "fotmob_teams.csv").write_text("old-csv", encoding="utf-8")
    index = (
        "<loc>https://www.fotmob.com/sitemap/en/teams/1.xml</loc>"
        "<loc>https://www.fotmob.com/sitemap/en/teams/2.xml</loc>"
    )

    def fetch(url: str) -> str:
        if url == fotmob_teams.SITEMAP_INDEX_URL:
            return index
        if url.endswith("/1.xml"):
            return "<loc>https://www.fotmob.com/teams/10/overview/alpha</loc>"
        raise OSError("network down")

    monkeypatch.setattr(fotmob_teams, "_fetch_text", fetch)

    with pytest.raises(RuntimeError, match="crawl incomplete"):
        fotmob_teams.crawl_sitemaps()

    assert (tmp_path / "fotmob_teams.json").read_text() == "old-json"
    assert (tmp_path / "fotmob_teams.csv").read_text() == "old-csv"
