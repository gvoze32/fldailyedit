"""Regression tests for the per-save FotMob/PES club identity index."""

import json

import pytest

from scraper.club_identity import UNRESOLVED, build_club_identity_index
from scraper.models import SquadMember, SquadSnapshot


def _team(team_id: int, name: str) -> dict:
    return {
        "fotmob_id": team_id,
        "name": name,
        "slug": name.lower().replace(" ", "-"),
        "url": f"https://www.fotmob.com/teams/{team_id}/overview/x",
    }


_FIRST = (
    "Aaron", "Bruno", "Carlos", "Dmitri", "Emil", "Fabio", "Goran", "Hugo",
    "Ivan", "Jonas", "Karim", "Luca", "Mateo", "Nils", "Oscar", "Pavel",
    "Quentin", "Rafael", "Stefan", "Tomas",
)
_LAST = (
    "Abernathy", "Brightwell", "Castellanos", "Dragomir", "Eriksen", "Fontaine",
    "Gallagher", "Hrubesch", "Iwobi", "Jablonski", "Kowalczyk", "Lindqvist",
    "Mendieta", "Nakamura", "Okonkwo", "Pellegrini", "Quaresma", "Rautavaara",
    "Szczesny", "Takahashi", "Underwood", "Valbuena", "Whitfield", "Xhaka",
    "Yamaguchi", "Zubizarreta", "Arbeloa", "Benzema", "Cuadrado", "Dembele",
    "Etxeberria", "Fernandinho", "Gundogan", "Hazard", "Immobile", "Jorginho",
    "Kimmich", "Lewandowski", "Modric", "Neuer",
)


def _players(start: int, count: int) -> dict[int, str]:
    return {
        1000 + index: f"{_FIRST[index % len(_FIRST)]} {_LAST[index]}"
        for index in range(start, start + count)
    }


def _snapshot(fotmob_id: int, club_name: str, names) -> SquadSnapshot:
    return SquadSnapshot(
        club_name=club_name,
        team_id_fotmob=fotmob_id,
        members=tuple(SquadMember(player_name=name) for name in names),
        source_url=f"https://www.fotmob.com/teams/{fotmob_id}/squad",
        complete=True,
    )


def _build(pes, fotmob, tmp_path, scope="save-a"):
    return build_club_identity_index(
        pes,
        fotmob,
        cache_path=tmp_path / "club_identity_cache.json",
        save_scope=scope,
    )


def test_name_pass_binds_unambiguous_names_and_rejects_ties(tmp_path):
    pes = {
        2: "Alpha Club",
        3: "Beta FC",
        4: "Gamma FC",
        5: "Racing Santander",
        6: "Delta FC",
    }
    fotmob = [
        _team(20, "Alpha Club"),
        _team(30, "Beta FC"),
        _team(31, "Beta FC"),
        _team(40, "Gamma Women"),
        _team(50, "Racing"),
        _team(51, "Racing Santander"),
        _team(60, "Delta FC"),
        _team(600_000, "Delta FC"),
    ]

    index = _build(pes, fotmob, tmp_path)
    by_pes_id = {item["pes_team_id"]: item for item in index.entries()}

    assert by_pes_id[2]["fotmob_id"] == 20
    assert by_pes_id[2]["identity_source"] == "unambiguous_name"
    assert 3 not in by_pes_id
    assert 4 not in by_pes_id
    assert by_pes_id[5]["fotmob_id"] == 51
    assert by_pes_id[6]["fotmob_id"] == 60
    assert index.pes_for_fotmob(51) == 5
    assert index.fotmob_for_pes(6) == 60
    assert index.pes_for_fotmob(31) is None


def test_fuzzy_index_rejects_short_unrelated_club_name(tmp_path):
    assert _build({200: "Ceres Negros"}, [_team(2313, "Os")], tmp_path).entries() == []


def test_pes_reserve_team_does_not_claim_senior_fotmob_club(tmp_path):
    index = _build(
        {100: "Real Sociedad", 101: "Real Sociedad B"},
        [_team(8560, "Real Sociedad")],
        tmp_path,
    )

    assert index.pes_for_fotmob(8560) == 100
    assert index.fotmob_for_pes(101) is None


def test_squad_overlap_binds_renamed_club(tmp_path):
    bayern = _players(0, 20)
    other = _players(20, 20)
    rosters = {1: list(bayern), 2: list(other)}
    names = {**bayern, **other}
    index = _build(
        {1: "FC Bayern München", 2: "Olympique Lyonnais"},
        [_team(9823, "Bayern Munich")],
        tmp_path,
    )
    squad = [*list(bayern.values())[:18], *list(other.values())[:2], "Unknown Youngster"]

    assert index.learn_from_snapshot(_snapshot(9823, "Bayern Munich", squad), rosters, names) == 1

    assert index.pes_for_fotmob(9823) == 1
    entry = next(item for item in index.entries() if item["fotmob_id"] == 9823)
    assert entry["identity_source"] == "squad_overlap"
    assert entry["pes_team_name"] == "FC Bayern München"
    assert index.resolve_name("Bayern Munich") == 1
    assert index.aliases()["Bayern Munich"] == "FC Bayern München"


def test_ambiguous_squad_overlap_is_not_bound(tmp_path):
    first = _players(0, 20)
    second = _players(20, 20)
    index = _build({1: "Alpha Club", 2: "Beta Club"}, [], tmp_path)
    squad = [*list(first.values())[:12], *list(second.values())[:12]]

    learned = index.learn_from_snapshot(
        _snapshot(777, "Gamma United", squad),
        {1: list(first), 2: list(second)},
        {**first, **second},
    )

    assert learned is None
    assert index.pes_for_fotmob(777) is None
    assert index.entries() == []


def test_small_overlap_is_not_bound(tmp_path):
    players = _players(0, 20)
    index = _build({1: "Alpha Club"}, [], tmp_path)
    squad = [*list(players.values())[:5], *(f"Stranger {n}" for n in "ABCDEFGHIJ")]

    assert index.learn_from_snapshot(_snapshot(5, "Alpha", squad), {1: list(players)}, players) is None


def test_overlap_evidence_overrides_name_binding(tmp_path):
    players = _players(0, 20)
    index = _build(
        {4219: "Como 1907"},
        [_team(10171, "Como"), _team(1_802_179, "Calcio Como 1907")],
        tmp_path,
    )
    assert index.pes_for_fotmob(1_802_179) == 4219

    learned = index.learn_from_snapshot(
        _snapshot(10171, "Como", list(players.values())),
        {4219: list(players)},
        players,
    )

    assert learned == 4219
    assert index.pes_for_fotmob(10171) == 4219
    assert index.pes_for_fotmob(1_802_179) is None
    assert index.fotmob_for_pes(4219) == 10171


def test_resolve_name_never_treats_weak_match_as_absent(tmp_path):
    index = _build({1: "FC Bayern München", 2: "Manchester United"}, [], tmp_path)

    assert index.resolve_name("FC Bayern München") == 1
    assert index.resolve_name("Bayern München") == 1
    assert index.resolve_name("Bayern Munich") is UNRESOLVED
    assert index.resolve_name("Manchester Utd") is UNRESOLVED
    assert index.resolve_name("Free agent") is None
    assert index.resolve_name("Kawasaki Frontale") is None


def test_learned_bindings_round_trip_per_save_scope(tmp_path):
    players = _players(0, 20)
    pes = {1: "FC Bayern München"}
    fotmob = [_team(9823, "Bayern Munich")]
    index = _build(pes, fotmob, tmp_path, scope="save-a")
    index.learn_from_snapshot(
        _snapshot(9823, "Bayern Munich", list(players.values())),
        {1: list(players)},
        players,
    )
    index.save()
    _build(pes, fotmob, tmp_path, scope="save-b").save()

    cache = json.loads((tmp_path / "club_identity_cache.json").read_text(encoding="utf-8"))
    assert cache == {"save-a": {"9823": 1}, "save-b": {}}

    reloaded = _build(pes, fotmob, tmp_path, scope="save-a")
    assert reloaded.pes_for_fotmob(9823) == 1
    assert reloaded.entries()[0]["identity_source"] == "squad_overlap"
    assert _build(pes, fotmob, tmp_path, scope="save-b").pes_for_fotmob(9823) is None
    assert _build({2: "Other Club"}, fotmob, tmp_path, scope="save-a").entries() == []


def test_sitemap_crawl_never_overwrites_index_after_partial_failure(
    monkeypatch, tmp_path
):
    import config
    from scraper import fotmob_teams

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(fotmob_teams.time, "sleep", lambda _: None)
    (tmp_path / "fotmob_teams.json").write_text("old-json", encoding="utf-8")
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
