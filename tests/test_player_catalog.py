"""Regression tests for the versioned Football Life player catalog."""

import pytest

from editor.models import PlayerInfo
from editor import player_catalog


def _write_current(path, rows):
    path.write_text(
        "\n".join(f"{player_id} - {name}" for player_id, name in rows) + "\n",
        encoding="utf-8",
    )


def test_current_catalog_covers_rosters_without_reintroducing_stale_free_agents(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(player_catalog, "MIN_CURRENT_PLAYER_CATALOG_SIZE", 2)
    current = tmp_path / "current.txt"
    legacy = tmp_path / "legacy.csv"
    _write_current(current, [(1, "Current One"), (2, "Current Two")])
    legacy.write_text(
        "PlayerID,PlayerName\n3,Roster Fallback\n99,Stale Free Agent\n",
        encoding="utf-8",
    )

    players, report = player_catalog.build_player_catalog(
        current_path=current,
        legacy_csv_path=legacy,
        edited_players={
            1: PlayerInfo(1, "Abbreviated", position="CM", overall_rating=82),
            4: PlayerInfo(4, "Edited Player"),
        },
        roster_ids={1, 3, 4},
    )

    assert set(players) == {1, 2, 3, 4}
    assert players[1].name == "Current One"
    assert players[1].position == "CM"
    assert report.legacy_roster_fallbacks == 1
    assert report.missing_roster_ids == ()
    assert report.positions == 1
    assert report.overall_ratings == 1


def test_catalog_uses_save_players_without_external_reference():
    players, report = player_catalog.build_player_catalog(
        current_path=None,
        legacy_csv_path=None,
        edited_players={
            42: PlayerInfo(42, "Vanilla Player", "V. Player"),
        },
        roster_ids={42, 99},
    )

    assert set(players) == {42}
    assert players[42].name == "Vanilla Player"
    assert report.current_entries == 0
    assert report.missing_roster_ids == (99,)

def test_catalog_rejects_missing_roster_id(monkeypatch, tmp_path):
    monkeypatch.setattr(player_catalog, "MIN_CURRENT_PLAYER_CATALOG_SIZE", 1)
    current = tmp_path / "current.txt"
    _write_current(current, [(1, "Known")])

    with pytest.raises(player_catalog.PlayerCatalogError, match="misses 1 roster IDs"):
        player_catalog.build_player_catalog(
            current_path=current,
            legacy_csv_path=None,
            edited_players={},
            roster_ids={1, 2},
        )


def test_catalog_rejects_partial_or_conflicting_reference(monkeypatch, tmp_path):
    monkeypatch.setattr(player_catalog, "MIN_CURRENT_PLAYER_CATALOG_SIZE", 2)
    current = tmp_path / "current.txt"
    _write_current(current, [(1, "First")])
    with pytest.raises(player_catalog.PlayerCatalogError, match="only 1 entries"):
        player_catalog.load_id_name_text(
            current, label="player", minimum_entries=2
        )

    _write_current(current, [(1, "First"), (1, "Different")])
    with pytest.raises(player_catalog.PlayerCatalogError, match="Conflicting"):
        player_catalog.load_id_name_text(current, label="player")
