"""Behavioral contracts for deterministic create-field resolution."""

from copy import deepcopy
from dataclasses import FrozenInstanceError
from datetime import date
import hashlib
import json
from pathlib import Path
from types import MappingProxyType
import unicodedata

import pytest

import config
from editor.editfile import EditFile
from editor.models import PlayerInfo, TeamData, TeamInfo
from scraper.pes_retro_stats import PesRetroStatsProfile
from tools.player_proposal_resolution import (
    CreateResolution,
    NationalityCatalog,
    allocate_created_player_id,
    derive_generic_appearance,
    derive_print_name,
    load_nationality_catalog,
    resolve_create_fields,
    resolve_create_team,
)


UUID_TEXT = "f77d9c27-8f02-4dbe-b877-4c13724a4886"
CREATED_PLAYER_IDS = range(200_000, 300_000)
APPEARANCE_PALETTE_V1 = (
    (3, 17),
    (2, 17),
    (4, 17),
    (1, 17),
    (5, 17),
    (12, 16),
    (30, 17),
    (9, 17),
)
CATALOG_SOURCE = {
    "url": (
        "https://github.com/xAranaktu/PES-2021-Cheat-Table/blob/master/"
        "PES%202021%20-%20v21.1.0.CT"
    ),
    "license": "MIT",
    "copyright": "Copyright (c) 2020 Paweł",
}
TEAM_ALIASES = {
    "The Blues": "Chelsea FC",
    "Bayern": "FC Bayern München",
    "Bayern Munich": "FC Bayern München",
}


class FixtureEditFile(EditFile):
    """In-memory EditFile using the real editor model and accessor contracts."""

    def __init__(
        self,
        *,
        players: dict[int, PlayerInfo] | None = None,
        teams: dict[int, TeamInfo] | None = None,
        rosters: dict[int, TeamData] | None = None,
    ) -> None:
        super().__init__()
        self.players = dict(players or {})
        self.teams = dict(teams or {})
        self.rosters = dict(rosters or {})

    def get_all_players(self, csv_path=None, include_base_db=True):
        del csv_path, include_base_db
        return self.players

    def get_all_team_info(self):
        return self.teams

    def get_team_roster(self, team_id):
        return self.rosters.get(team_id)

    def get_all_rosters(self):
        return self.rosters


@pytest.fixture
def edit_file() -> FixtureEditFile:
    return FixtureEditFile(
        players={501: PlayerInfo(501, "Existing Player", "EXISTING")},
        teams={
            102: TeamInfo(102, "Chelsea FC", "CHE"),
            205: TeamInfo(205, "FC Bayern München", "FCB"),
        },
        rosters={
            102: TeamData(102, [501] + [0] * 39),
            205: TeamData(205, [501] + [0] * 39),
        },
    )


def make_source_profile(**changes) -> PesRetroStatsProfile:
    values = {
        "player_id": UUID_TEXT,
        "short_id": "f77d9c27",
        "name": "Dastan Satpaev",
        "full_name": "Dastan Satpaev",
        "profile_url": "https://pesretrostats.com/player/f77d9c27-dastan-satpaev",
        "birth_date": date(2008, 8, 12),
        "nationality": "Kazakhstan",
        "current_club": "Chelsea FC",
        "shirt_number": 36,
        "height": 176,
        "weight": 73,
        "strong_foot": "R",
        "weak_foot_accuracy": 5,
        "weak_foot_frequency": 5,
        "form": 5,
        "injury_tolerance": "B",
        "playing_style": "Goal Poacher",
        "positions": MappingProxyType({"CF": "A"}),
        "stats": MappingProxyType({"attacking_prowess": 72}),
        "player_skill_codes": (),
        "com_playing_styles": (),
    }
    values.update(changes)
    return PesRetroStatsProfile(**values)


def _catalog_payload(nationalities: list[dict[str, object]]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "source": dict(CATALOG_SOURCE),
        "nationalities": nationalities,
    }


def _write_catalog(
    tmp_path: Path, nationalities: list[dict[str, object]]
) -> Path:
    path = tmp_path / "nationalities.json"
    path.write_text(
        json.dumps(_catalog_payload(nationalities), ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def _seeded_player_id(
    uuid_text: str, id_range: range = CREATED_PLAYER_IDS
) -> int:
    seed = int.from_bytes(
        hashlib.sha256(uuid_text.encode("utf-8")).digest()[:8],
        byteorder="big",
        signed=False,
    )
    return id_range.start + (seed % len(id_range))


def _circular_expected_id(
    uuid_text: str, unavailable: set[int], id_range: range = CREATED_PLAYER_IDS
) -> int:
    initial = _seeded_player_id(uuid_text, id_range)
    initial_index = initial - id_range.start
    for offset in range(len(id_range)):
        candidate = id_range.start + ((initial_index + offset) % len(id_range))
        if candidate not in unavailable:
            return candidate
    raise AssertionError("test fixture exhausted its expected ID range")


def _expected_appearance(uuid_text: str) -> tuple[int, int]:
    digest = hashlib.sha256(uuid_text.encode("utf-8")).digest()
    return APPEARANCE_PALETTE_V1[digest[8] % len(APPEARANCE_PALETTE_V1)]


def test_committed_catalog_has_exact_schema_provenance_and_220_nonzero_records():
    path = config.DATA_DIR / "pes21_nationalities.json"
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert set(payload) == {"schema_version", "source", "nationalities"}
    assert payload["schema_version"] == 1
    assert payload["source"] == CATALOG_SOURCE

    nationalities = payload["nationalities"]
    assert isinstance(nationalities, list)
    assert len(nationalities) == 220
    assert all(
        set(record) == {"id", "name", "aliases"} for record in nationalities
    )
    assert all(
        type(record["id"]) is int and 1 <= record["id"] <= 65_535
        for record in nationalities
    )
    assert len({record["id"] for record in nationalities}) == 220
    assert all(
        isinstance(record["name"], str)
        and record["name"].strip()
        and isinstance(record["aliases"], list)
        for record in nationalities
    )

    catalog = load_nationality_catalog()
    assert isinstance(catalog, NationalityCatalog)
    assert len(catalog) == 220


@pytest.mark.parametrize(
    ("name", "nationality_id"),
    [
        ("Indonesia", 10),
        ("Kazakhstan", 216),
        ("Italy", 215),
        ("United Arab Emirates", 37),
        ("DR Congo", 55),
        ("Ivory Coast", 56),
        ("North Macedonia", 221),
        ("United States of America", 135),
    ],
)
def test_catalog_resolves_required_pes_and_retro_names(name, nationality_id):
    assert load_nationality_catalog().resolve(name) == nationality_id


@pytest.mark.parametrize(
    ("alias", "nationality_id"),
    [
        ("Comoros", 54),
        ("Gambia", 63),
        ("Eswatini", 89),
        ("Congo Republic", 98),
        ("Guyana", 159),
        ("São Tomé and Príncipe", 82),
    ],
)
def test_catalog_resolves_required_retro_aliases(alias, nationality_id):
    assert load_nationality_catalog().resolve(alias) == nationality_id


def test_catalog_normalizes_case_punctuation_and_whitespace_for_explicit_aliases():
    catalog = load_nationality_catalog()

    assert catalog.resolve("  north-macedonia\t") == 221
    assert catalog.resolve("são tomé and príncipe") == 82


def test_injected_catalog_loads_valid_records_and_resolves_alias(tmp_path):
    path = _write_catalog(
        tmp_path,
        [
            {"id": 7, "name": "Alpha Republic", "aliases": ["Alpha"]},
            {"id": 9, "name": "Beta Islands", "aliases": []},
        ],
    )

    catalog = load_nationality_catalog(path)

    assert isinstance(catalog, NationalityCatalog)
    assert len(catalog) == 2
    assert catalog.resolve("alpha") == 7
    assert catalog.resolve("Beta Islands") == 9


@pytest.mark.parametrize("bad_id", [0, 65_536, True, False])
def test_catalog_rejects_zero_out_of_range_and_bool_ids(tmp_path, bad_id):
    path = _write_catalog(
        tmp_path,
        [{"id": bad_id, "name": "Alpha Republic", "aliases": []}],
    )

    with pytest.raises(ValueError):
        load_nationality_catalog(path)


def test_catalog_rejects_duplicate_ids(tmp_path):
    path = _write_catalog(
        tmp_path,
        [
            {"id": 7, "name": "Alpha Republic", "aliases": []},
            {"id": 7, "name": "Beta Islands", "aliases": []},
        ],
    )

    with pytest.raises(ValueError):
        load_nationality_catalog(path)


def test_catalog_rejects_duplicate_normalized_canonical_names(tmp_path):
    path = _write_catalog(
        tmp_path,
        [
            {"id": 7, "name": "Alpha Republic", "aliases": []},
            {"id": 9, "name": "alpha-republic", "aliases": []},
        ],
    )

    with pytest.raises(ValueError):
        load_nationality_catalog(path)


def test_catalog_rejects_duplicate_normalized_aliases(tmp_path):
    path = _write_catalog(
        tmp_path,
        [
            {
                "id": 7,
                "name": "Alpha Republic",
                "aliases": ["Shared Name", "shared-name"],
            }
        ],
    )

    with pytest.raises(ValueError):
        load_nationality_catalog(path)


def test_catalog_rejects_alias_ambiguous_between_nationalities(tmp_path):
    path = _write_catalog(
        tmp_path,
        [
            {"id": 7, "name": "Alpha Republic", "aliases": ["Shared"]},
            {"id": 9, "name": "Beta Islands", "aliases": ["shared"]},
        ],
    )

    with pytest.raises(ValueError):
        load_nationality_catalog(path)


@pytest.mark.parametrize(
    "record",
    [
        {"id": 7, "name": "", "aliases": []},
        {"id": 7, "name": "   ", "aliases": []},
        {"id": 7, "name": "Alpha Republic", "aliases": [""]},
        {"id": 7, "name": "Alpha Republic", "aliases": ["  "]},
    ],
)
def test_catalog_rejects_empty_canonical_names_and_aliases(tmp_path, record):
    path = _write_catalog(tmp_path, [record])

    with pytest.raises(ValueError):
        load_nationality_catalog(path)


@pytest.mark.parametrize("query", ["", "   "])
def test_catalog_rejects_empty_resolution_queries(tmp_path, query):
    path = _write_catalog(
        tmp_path,
        [{"id": 7, "name": "Alpha Republic", "aliases": []}],
    )
    catalog = load_nationality_catalog(path)

    with pytest.raises(ValueError):
        catalog.resolve(query)


def test_catalog_rejects_unknown_nationality_with_human_readable_name(tmp_path):
    path = _write_catalog(
        tmp_path,
        [{"id": 7, "name": "Alpha Republic", "aliases": []}],
    )
    catalog = load_nationality_catalog(path)

    with pytest.raises(ValueError, match="Atlantis"):
        catalog.resolve("Atlantis")


def test_player_id_starts_from_independently_derived_uuid_seed():
    expected = _seeded_player_id(UUID_TEXT)

    assert allocate_created_player_id(UUID_TEXT, set()) == expected


def test_player_id_probes_the_next_circular_slot_after_a_collision():
    initial = _seeded_player_id(UUID_TEXT)
    expected = CREATED_PLAYER_IDS.start + (
        (initial - CREATED_PLAYER_IDS.start + 1) % len(CREATED_PLAYER_IDS)
    )

    assert allocate_created_player_id(UUID_TEXT, {initial}) == expected


def test_player_id_probe_wraps_from_the_range_end_to_the_start():
    seed = int.from_bytes(
        hashlib.sha256(UUID_TEXT.encode("utf-8")).digest()[:8], "big"
    )
    size = 2
    while seed % size == 0:
        size += 1
    small_range = range(900, 900 + size)
    initial = _seeded_player_id(UUID_TEXT, small_range)
    assert initial > small_range.start
    occupied_through_end = set(range(initial, small_range.stop))

    assert (
        allocate_created_player_id(UUID_TEXT, occupied_through_end, small_range)
        == small_range.start
    )


def test_player_id_allocation_fails_when_an_injected_range_is_exhausted():
    small_range = range(700, 704)

    with pytest.raises(ValueError, match="exhaust"):
        allocate_created_player_id(UUID_TEXT, set(small_range), small_range)


@pytest.mark.parametrize(
    "uuid_text",
    [
        UUID_TEXT,
        "0ce2dbde-9cd9-423c-a90a-35b07df6a967",
    ],
)
def test_generic_appearance_uses_the_uuid_seeded_versioned_palette(uuid_text):
    expected = _expected_appearance(uuid_text)

    assert derive_generic_appearance(uuid_text) == expected
    assert derive_generic_appearance(uuid_text) == expected
    assert expected in APPEARANCE_PALETTE_V1


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Dastan Satpaev", "SATPAEV"),
        ("Pelé", "PELÉ"),
        ("Pele\u0301", "PELÉ"),
        ("  João\t da\n “Silva…”  ", "SILVA"),
        ("Jean-Pierre ‘O’Neill’", "O’NEILL"),
    ],
)
def test_print_name_is_unicode_safe_and_uses_the_trimmed_final_token(name, expected):
    result = derive_print_name(name)

    assert result == expected
    assert unicodedata.is_normalized("NFC", result)

def test_print_name_falls_back_to_normalized_full_name_when_final_token_is_punctuation():
    assert derive_print_name("  John   ...  ") == "JOHN ..."


def test_print_name_accepts_exactly_sixty_utf8_bytes():
    surname = "é" * 30

    result = derive_print_name(f"A {surname}")

    assert result == "É" * 30
    assert len(result.encode("utf-8")) == 60


def test_print_name_rejects_more_than_sixty_utf8_bytes():
    surname = "é" * 31

    with pytest.raises(ValueError, match="60"):
        derive_print_name(f"A {surname}")


@pytest.mark.parametrize(
    ("submitted_team", "source_team", "expected_team_id"),
    [
        ("Chelsea FC", "Chelsea FC", 102),
        ("Bayern Munich", "FC Bayern München", 205),
        ("FC Bayern München", "Bayern", 205),
    ],
)
def test_create_team_requires_exact_canonical_or_validated_alias_matches(
    edit_file, submitted_team, source_team, expected_team_id
):
    team = resolve_create_team(
        edit_file,
        submitted_team,
        source_team,
        TEAM_ALIASES,
    )

    assert team is edit_file.teams[expected_team_id]



def test_create_team_ignores_unaddressable_placeholder_team_names(edit_file):
    edit_file.teams[140] = TeamInfo(140, "-", "---")

    team = resolve_create_team(
        edit_file,
        "Chelsea FC",
        "Chelsea FC",
        TEAM_ALIASES,
    )

    assert team is edit_file.teams[102]

def test_create_team_rejects_conflicting_submitted_and_source_teams(edit_file):
    with pytest.raises(ValueError) as exc_info:
        resolve_create_team(
            edit_file,
            "Chelsea FC",
            "Bayern",
            TEAM_ALIASES,
        )

    message = str(exc_info.value)
    assert "Chelsea FC" in message
    assert "Bayern" in message


def test_create_team_rejects_ambiguous_normalized_team_names(edit_file):
    edit_file.teams[999] = TeamInfo(999, "Chelsea-FC", "CHF")
    edit_file.rosters[999] = TeamData(999, [501] + [0] * 39)

    with pytest.raises(ValueError, match="Chelsea FC"):
        resolve_create_team(
            edit_file,
            "Chelsea FC",
            "Chelsea FC",
            TEAM_ALIASES,
        )


def test_create_team_rejects_a_team_without_a_roster(edit_file):
    edit_file.rosters.pop(102)

    with pytest.raises(ValueError, match="roster"):
        resolve_create_team(
            edit_file,
            "The Blues",
            "Chelsea FC",
            TEAM_ALIASES,
        )


def test_create_team_rejects_a_team_with_an_empty_roster(edit_file):
    edit_file.rosters[102] = TeamData(102, [0] * 40)

    with pytest.raises(ValueError, match="roster"):
        resolve_create_team(
            edit_file,
            "Chelsea FC",
            "Chelsea FC",
            TEAM_ALIASES,
        )


def test_create_team_rejects_unknown_human_readable_names(edit_file):
    with pytest.raises(ValueError, match="Neverland United"):
        resolve_create_team(
            edit_file,
            "Neverland United",
            "Neverland United",
            TEAM_ALIASES,
        )


def test_create_resolution_is_immutable_and_slotted():
    resolution = CreateResolution(
        player_id=200_001,
        print_name="SATPAEV",
        team_id=102,
        team_name="Chelsea FC",
        nationality_id=216,
        skin_color=3,
        iris_color=17,
    )

    with pytest.raises(FrozenInstanceError):
        resolution.player_id = 200_002
    assert not hasattr(resolution, "__dict__")


def test_resolve_create_fields_populates_every_field_skips_base_and_spec_ids_and_preserves_edit_file(
    edit_file,
):
    source = make_source_profile()
    initial = _seeded_player_id(source.player_id)
    following = CREATED_PLAYER_IDS.start + (
        (initial - CREATED_PLAYER_IDS.start + 1) % len(CREATED_PLAYER_IDS)
    )
    edit_file.players[initial] = PlayerInfo(initial, "Base Collision")
    completed_spec_ids = {following}
    unavailable = set(edit_file.players) | completed_spec_ids
    expected_player_id = _circular_expected_id(source.player_id, unavailable)
    expected_skin, expected_iris = _expected_appearance(source.player_id)
    before = (
        deepcopy(edit_file.players),
        deepcopy(edit_file.teams),
        deepcopy(edit_file.rosters),
        bytes(edit_file._data),
    )

    resolution = resolve_create_fields(
        edit_file,
        source=source,
        submitted_team="The Blues",
        completed_player_ids=completed_spec_ids,
        nationality_catalog=load_nationality_catalog(),
        team_aliases=TEAM_ALIASES,
    )

    assert resolution == CreateResolution(
        player_id=expected_player_id,
        print_name="SATPAEV",
        team_id=102,
        team_name="Chelsea FC",
        nationality_id=216,
        skin_color=expected_skin,
        iris_color=expected_iris,
    )
    assert (
        edit_file.players,
        edit_file.teams,
        edit_file.rosters,
        bytes(edit_file._data),
    ) == before
    assert completed_spec_ids == {following}
