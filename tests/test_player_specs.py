import hashlib
import json
import struct

import pytest


REVISION = "fl26-u2.2-national-squads"


def valid_marco_payload():
    return {
        "schema_version": 1,
        "operation": "update",
        "lifecycle": {"status": "active"},
        "applies_to": [REVISION],
        "identity": {
            "name": "Marco Palestra",
            "aliases": ["Marco Palestra"],
            "pes_id": 162196,
            "sortitoutsi_id": 2000136198,
        },
        "evidence": {
            "profile_url": "https://sortitoutsi.net/football-manager-data-update/person/2000136198",
            "proof_urls": [
                "https://sortitoutsi.net/football-manager-data-update/attributes/submission/526121"
            ],
            "effective_date": "2026-07-25",
            "reason": "Approved attribute submission",
        },
        "pes": {
            "abilities": {
                "speed": {"from": 77, "to": 80},
                "acceleration": {"from": 75, "to": 77},
                "defensive_awareness": {"from": 61, "to": 62},
                "ball_winning": {"from": 59, "to": 60},
            }
        },
    }


def valid_dastan_payload():
    return {
        "schema_version": 1,
        "operation": "create",
        "lifecycle": {
            "status": "active",
            "reason": "Missing from bundled FL26 base",
        },
        "applies_to": [REVISION],
        "identity": {
            "name": "Dastan Satpayev",
            "print_name": "SATPAYEV",
            "aliases": [
                "Dastan Satpayev",
                "Dastan Sätpayev",
                "Dastan Satpaev",
            ],
            "pes_id": 200000,
            "sortitoutsi_id": 2000370206,
        },
        "evidence": {
            "profile_url": "https://sortitoutsi.net/football-manager-data-update/person/2000370206",
            "proof_urls": [
                "https://sortitoutsi.net/football-manager-2026/person/2000370206/dastan-satpayev",
                "https://qjl.kz/en/news/official-dastan-satpayev-signed-a-contract-with-chelsea",
                "https://www.chelseafc.com/en/news/article/chelsea-squad-numbers-2026-pre-season-tour-confirmed",
            ],
            "effective_date": "2026-08-04",
            "reason": "Chelsea included Satpayev in its 2026 pre-season squad before his contractual transfer date.",
        },
        "pes": {
            "player_id": 200000,
            "name": "Dastan Satpayev",
            "print_name": "SATPAYEV",
            "team_id": 102,
            "team_name": "Chelsea FC",
            "preferred_shirt_number": 36,
            "nationality_id": 216,
            "age": 17,
            "height": 176,
            "weight": 73,
            "registered_position": "CF",
            "playing_style": 1,
            "strong_foot": 0,
            "weak_foot_usage": 2,
            "weak_foot_accuracy": 2,
            "form": 5,
            "injury_resistance": 2,
            "position_proficiency": {"LWF": 2, "RWF": 2, "SS": 1, "CF": 2},
            "abilities": {
                "attacking_awareness": 76,
                "ball_control": 74,
                "dribbling": 75,
                "tight_possession": 72,
                "low_pass": 68,
                "lofted_pass": 65,
                "finishing": 79,
                "heading": 68,
                "place_kicking": 65,
                "curl": 70,
                "speed": 80,
                "acceleration": 82,
                "kicking_power": 77,
                "jump": 70,
                "physical_contact": 68,
                "balance": 78,
                "stamina": 72,
                "defensive_awareness": 42,
                "ball_winning": 43,
                "aggression": 70,
                "gk_awareness": 40,
                "catching": 40,
                "clearing": 40,
                "reflexes": 40,
                "gk_reach": 40,
            },
            "player_skills": [],
            "com_styles": [],
            "skin_color": 2,
            "iris_color": 1,
        },
    }


def write_payload(directory, filename, payload):
    (directory / filename).write_text(json.dumps(payload), encoding="utf-8")


def test_base_manifest_matches_bundled_edit():
    from editor.player_spec import load_base_manifest

    manifest = load_base_manifest()
    digest = hashlib.sha256()
    with open("base/EDIT00000000", "rb") as bundled:
        for chunk in iter(lambda: bundled.read(1024 * 1024), b""):
            digest.update(chunk)
    actual = digest.hexdigest()
    assert manifest.revision == REVISION
    assert manifest.sha256 == actual


def test_base_manifest_rejects_unknown_keys_and_malformed_digest(tmp_path):
    from editor.player_spec import PlayerSpecError, load_base_manifest

    manifest = tmp_path / "base_manifest.json"
    manifest.write_text(
        json.dumps({"revision": REVISION, "sha256": "not-a-digest", "extra": True}),
        encoding="utf-8",
    )
    with pytest.raises(PlayerSpecError, match="base manifest"):
        load_base_manifest(manifest)

    manifest.write_text(
        json.dumps({"revision": REVISION, "sha256": "not-a-digest"}),
        encoding="utf-8",
    )
    with pytest.raises(PlayerSpecError, match="sha256"):
        load_base_manifest(manifest)


def test_load_specs_rejects_filename_identity_and_duplicate_ids(tmp_path):
    from editor.player_spec import PlayerSpecError, load_player_specs

    write_payload(tmp_path, "wrong-name.json", valid_marco_payload())
    with pytest.raises(PlayerSpecError, match="filename"):
        load_player_specs(tmp_path)

    (tmp_path / "wrong-name.json").unlink()
    write_payload(tmp_path, "marco-palestra.json", valid_marco_payload())
    duplicate = valid_dastan_payload()
    duplicate["identity"]["pes_id"] = 162196
    duplicate["pes"]["player_id"] = 162196
    write_payload(tmp_path, "dastan-satpayev.json", duplicate)
    with pytest.raises(PlayerSpecError, match="PES ID"):
        load_player_specs(tmp_path)


def test_update_patch_requires_distinct_in_range_values(tmp_path):
    from editor.player_spec import PlayerSpecError, load_player_specs

    payload = valid_marco_payload()
    payload["pes"]["abilities"]["speed"] = {"from": 100, "to": 100}
    write_payload(tmp_path, "marco-palestra.json", payload)
    with pytest.raises(PlayerSpecError, match="speed"):
        load_player_specs(tmp_path)


def test_valid_create_and_update_specs_load_in_filename_order(tmp_path):
    from editor.player_spec import FieldPatch, load_player_specs

    write_payload(tmp_path, "marco-palestra.json", valid_marco_payload())
    write_payload(tmp_path, "dastan-satpayev.json", valid_dastan_payload())
    (tmp_path / "ignored.txt").write_text("not json", encoding="utf-8")

    specs = load_player_specs(tmp_path)

    assert tuple(spec.path.name for spec in specs) == (
        "dastan-satpayev.json",
        "marco-palestra.json",
    )
    dastan, marco = specs
    assert dastan.create is not None
    assert dastan.create.abilities["finishing"] == 79
    assert dastan.identity.aliases == (
        "Dastan Satpayev",
        "Dastan Sätpayev",
        "Dastan Satpaev",
    )
    assert marco.create is None
    assert marco.patches["speed"] == FieldPatch(current=77, target=80)
    assert marco.evidence.effective_date.isoformat() == "2026-07-25"


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda payload: payload.update({"unknown": True}), "unknown"),
        (lambda payload: payload["lifecycle"].update({"status": "paused"}), "lifecycle"),
        (lambda payload: payload["identity"].update({"aliases": []}), "aliases"),
        (
            lambda payload: payload["identity"].update(
                {"aliases": ["Marco Palestra", "Marco Palestra"]}
            ),
            "aliases",
        ),
        (
            lambda payload: payload["evidence"].update(
                {"profile_url": "http://example.com/player"}
            ),
            "HTTPS",
        ),
        (
            lambda payload: payload["pes"].update(
                {"overall_rating": {"from": 72, "to": 73}}
            ),
            "PES field",
        ),
        (
            lambda payload: payload["pes"]["abilities"].update(
                {"speed": {"from": 39, "to": 80}}
            ),
            "speed",
        ),
    ],
)
def test_update_specs_are_strict(tmp_path, mutate, message):
    from editor.player_spec import PlayerSpecError, load_player_specs

    payload = valid_marco_payload()
    mutate(payload)
    write_payload(tmp_path, "marco-palestra.json", payload)
    with pytest.raises(PlayerSpecError, match=message):
        load_player_specs(tmp_path)


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("age",), 64, "age"),
        (("playing_style",), 32, "playing_style"),
        (("position_proficiency", "CF"), 4, "CF"),
        (("abilities", "speed"), 100, "speed"),
        (("skin_color",), 256, "skin_color"),
    ],
)
def test_create_values_obey_codec_widths_and_ability_range(tmp_path, path, value, message):
    from editor.player_spec import PlayerSpecError, load_player_specs

    payload = valid_dastan_payload()
    target = payload["pes"]
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    write_payload(tmp_path, "dastan-satpayev.json", payload)
    with pytest.raises(PlayerSpecError, match=message):
        load_player_specs(tmp_path)


def test_validate_spec_set_rejects_normalized_aliases_and_sortitoutsi_ids(tmp_path):
    from editor.player_spec import PlayerSpecError, load_player_specs

    marco = valid_marco_payload()
    dastan = valid_dastan_payload()
    dastan["identity"]["aliases"].append("Márco Palestra")
    write_payload(tmp_path, "marco-palestra.json", marco)
    write_payload(tmp_path, "dastan-satpayev.json", dastan)
    with pytest.raises(PlayerSpecError, match="alias"):
        load_player_specs(tmp_path)

    dastan["identity"]["aliases"] = ["Dastan Satpayev"]
    dastan["identity"]["sortitoutsi_id"] = 2000136198
    write_payload(tmp_path, "dastan-satpayev.json", dastan)
    with pytest.raises(PlayerSpecError, match="SortitoutSI ID"):
        load_player_specs(tmp_path)


def test_player_slug_normalizes_unicode_to_ascii_tokens():
    from editor.player_spec import player_slug

    assert player_slug("  Dastan Sätpayev -- U-21  ") == "dastan-satpayev-u-21"


def dastan_spec(tmp_path):
    from editor.player_spec import load_player_specs

    write_payload(tmp_path, "dastan-satpayev.json", valid_dastan_payload())
    return load_player_specs(tmp_path)[0]


def make_player_spec_edit_file(roster_size: int):
    from editor.editfile import (
        GAME_PLAN_ENTRY_SIZE,
        GP_LINEUP,
        HEADER_SIZE,
        HDR_GAME_PLAN_COUNT,
        HDR_PLAYER_COUNT,
        HDR_TEAM_COUNT,
        HDR_TEAM_PLAYER_COUNT,
        MAX_GAME_PLANS,
        PE_PLAYER_ID,
        PE_PLAYER_NAME,
        PE_PRINT_NAME,
        PLAYER_APPEARANCE_SIZE,
        PLAYER_ENTRY_SIZE,
        PLAYER_TOTAL_SIZE,
        TEAM_PLAYER_ENTRY_SIZE,
        TE_TEAM_ID,
        TE_TEAM_NAME,
        TP_PLAYER_IDS,
        TP_SHIRT_NUMBERS,
        TP_TEAM_ID,
        EditFile,
    )

    edit_file = EditFile()
    edit_file._calculate_offsets()
    edit_file._data = bytearray(
        edit_file.game_plan_start + MAX_GAME_PLANS * GAME_PLAN_ENTRY_SIZE
    )
    struct.pack_into("<H", edit_file._data, HDR_PLAYER_COUNT, 1)
    struct.pack_into("<H", edit_file._data, HDR_TEAM_COUNT, 1)
    struct.pack_into("<H", edit_file._data, HDR_TEAM_PLAYER_COUNT, 1)
    struct.pack_into("<H", edit_file._data, HDR_GAME_PLAN_COUNT, 1)
    edit_file._parse_header()

    existing_id = 181639
    player_offset = HEADER_SIZE
    struct.pack_into("<I", edit_file._data, player_offset + PE_PLAYER_ID, existing_id)
    struct.pack_into("<I", edit_file._data, player_offset + 4, existing_id)
    edit_file._data[
        player_offset + PE_PLAYER_NAME : player_offset + PE_PLAYER_NAME + 16
    ] = b"Existing Player\0"
    edit_file._data[
        player_offset + PE_PRINT_NAME : player_offset + PE_PRINT_NAME + 9
    ] = b"EXISTING\0"
    struct.pack_into(
        "<I",
        edit_file._data,
        player_offset + PLAYER_ENTRY_SIZE,
        existing_id,
    )
    assert PLAYER_TOTAL_SIZE == PLAYER_ENTRY_SIZE + PLAYER_APPEARANCE_SIZE

    struct.pack_into("<I", edit_file._data, edit_file.team_start + TE_TEAM_ID, 102)
    edit_file._data[
        edit_file.team_start + TE_TEAM_NAME : edit_file.team_start + TE_TEAM_NAME + 11
    ] = b"Chelsea FC\0"

    roster_offset = edit_file.team_player_start
    struct.pack_into("<I", edit_file._data, roster_offset + TP_TEAM_ID, 102)
    player_ids = list(range(100001, 100001 + roster_size))
    available_shirts = list(range(1, 36)) + list(range(37, 42))
    for slot, player_id in enumerate(player_ids):
        struct.pack_into(
            "<I",
            edit_file._data,
            roster_offset + TP_PLAYER_IDS + slot * 4,
            player_id,
        )
        struct.pack_into(
            "<H",
            edit_file._data,
            roster_offset + TP_SHIRT_NUMBERS + slot * 2,
            available_shirts[slot],
        )

    struct.pack_into("<I", edit_file._data, edit_file.game_plan_start, 102)
    lineup = bytes(range(roster_size)) + bytes([0xFF] * (40 - roster_size))
    edit_file._data[
        edit_file.game_plan_start
        + GP_LINEUP : edit_file.game_plan_start
        + GP_LINEUP
        + 40
    ] = lineup
    return edit_file


def test_create_serializer_builds_linked_player_and_appearance_records(tmp_path):
    from editor.player_codec import (
        FIELD_SPECS,
        PLAYER_APPEARANCE_SIZE,
        PLAYER_DATA_SIZE,
        _read_field,
        decode_player_entry,
        serialize_created_player,
    )

    spec = dastan_spec(tmp_path)
    assert spec.create is not None

    player_entry, appearance_entry = serialize_created_player(spec.create)
    profile = decode_player_entry(player_entry)

    assert len(player_entry) == PLAYER_DATA_SIZE
    assert len(appearance_entry) == PLAYER_APPEARANCE_SIZE
    assert int.from_bytes(player_entry[:4], "little") == spec.identity.pes_id
    assert int.from_bytes(player_entry[4:8], "little") == spec.identity.pes_id
    assert int.from_bytes(appearance_entry[:4], "little") == spec.identity.pes_id
    assert int.from_bytes(appearance_entry[8:12], "little") == 0
    assert appearance_entry[4] & (1 << 2)
    assert appearance_entry[12:19] == bytes([0x77] * 7)
    assert appearance_entry[45] == spec.create.skin_color
    assert appearance_entry[64] == spec.create.iris_color
    assert (
        player_entry[0x36:0x73].split(b"\0", 1)[0].decode()
        == spec.identity.name
    )
    assert (
        player_entry[0x73:0xB0].split(b"\0", 1)[0].decode()
        == spec.identity.print_name
    )
    assert (
        player_entry[0xB0:0xF0].split(b"\0", 1)[0].decode()
        == spec.identity.print_name
    )
    assert profile.player_id == spec.identity.pes_id
    assert profile.nationality_id == spec.create.nationality_id
    assert (profile.age, profile.height, profile.weight) == (
        spec.create.age,
        spec.create.height,
        spec.create.weight,
    )
    assert profile.registered_position == spec.create.registered_position
    assert profile.playing_style == spec.create.playing_style
    assert profile.abilities == spec.create.abilities
    assert profile.position_proficiency == {
        "GK": 0,
        "CB": 0,
        "LB": 0,
        "RB": 0,
        "DMF": 0,
        "CMF": 0,
        "LMF": 0,
        "RMF": 0,
        "AMF": 0,
        "RWF": 2,
        "SS": 1,
        "CF": 2,
        "LWF": 2,
    }
    for flag in (
        "edited_player",
        "edited_basic_settings",
        "edited_registered_position",
        "edited_playable_positions",
        "edited_abilities",
    ):
        assert _read_field(player_entry, FIELD_SPECS[flag]) == 1
    assert _read_field(player_entry, FIELD_SPECS["strong_foot"]) == 0


def test_full_roster_returns_waiting_without_mutation(tmp_path, monkeypatch):
    from editor.player_spec import apply_create

    edit_file = make_player_spec_edit_file(roster_size=40)
    before = bytes(edit_file._data)
    spec = dastan_spec(tmp_path)
    monkeypatch.setattr(
        edit_file,
        "release_player",
        lambda *args, **kwargs: pytest.fail("create must never release a player"),
    )

    result = apply_create(edit_file, spec, {})

    assert (result.status, result.reason) == (
        "waiting",
        "destination_roster_full",
    )
    assert edit_file.player_count == 1
    assert bytes(edit_file._data) == before


def test_create_registers_linked_roster_and_game_plan_and_is_idempotent(
    tmp_path,
):
    from editor.editfile import GP_LINEUP
    from editor.player_spec import apply_create

    edit_file = make_player_spec_edit_file(roster_size=39)
    spec = dastan_spec(tmp_path)
    assert spec.create is not None

    result = apply_create(edit_file, spec, {})

    assert result.status == "created"
    assert edit_file.player_count == 2
    assert struct.unpack_from("<H", edit_file._data, 0x60)[0] == 2
    profile = edit_file.get_player_ability_profile(spec.identity.pes_id)
    assert profile is not None
    assert profile.abilities == spec.create.abilities
    roster = edit_file.get_team_roster(spec.create.team_id)
    assert roster is not None
    assert roster.player_ids[39] == spec.identity.pes_id
    assert roster.shirt_numbers[39] == spec.create.preferred_shirt_number
    assert edit_file._data[edit_file.game_plan_start + GP_LINEUP + 39] == 39

    before_second_run = bytes(edit_file._data)
    current_players = edit_file.get_all_players(include_base_db=False)
    second_result = apply_create(edit_file, spec, current_players)

    assert second_result.status == "already_applied"
    assert edit_file.player_count == 2
    assert bytes(edit_file._data) == before_second_run


def test_create_rolls_back_bytes_header_counts_and_offsets_when_roster_add_fails(
    tmp_path,
    monkeypatch,
):
    from editor.editfile import HDR_PLAYER_COUNT
    from editor.player_spec import PlayerSpecError, apply_create

    edit_file = make_player_spec_edit_file(roster_size=39)
    before = bytes(edit_file._data)
    before_count = edit_file.player_count
    offset_fields = (
        "player_start",
        "team_start",
        "manager_start",
        "competition_start",
        "stadium_start",
        "unknown_start",
        "team_player_start",
        "competition_entry_start",
        "game_plan_start",
    )
    before_offsets = tuple(getattr(edit_file, field) for field in offset_fields)
    monkeypatch.setattr(edit_file, "add_player", lambda *args, **kwargs: False)

    with pytest.raises(PlayerSpecError, match="could not register"):
        apply_create(edit_file, dastan_spec(tmp_path), {})

    assert bytes(edit_file._data) == before
    assert edit_file.player_count == before_count
    assert struct.unpack_from("<H", edit_file._data, HDR_PLAYER_COUNT)[0] == before_count
    assert tuple(getattr(edit_file, field) for field in offset_fields) == before_offsets


def test_create_allows_an_unused_id_below_existing_created_ids(tmp_path):
    from editor.editfile import PLAYER_ENTRY_SIZE
    from editor.player_spec import apply_create

    edit_file = make_player_spec_edit_file(roster_size=39)
    struct.pack_into("<I", edit_file._data, edit_file.player_start, 200001)
    struct.pack_into("<I", edit_file._data, edit_file.player_start + 4, 200001)
    struct.pack_into(
        "<I",
        edit_file._data,
        edit_file.player_start + PLAYER_ENTRY_SIZE,
        200001,
    )
    spec = dastan_spec(tmp_path)

    result = apply_create(edit_file, spec, {})

    assert result.status == "created"
    assert edit_file.player_count == 2
    assert edit_file.get_player_ability_profile(200000) is not None


def test_create_rejects_id_collision_with_different_normalized_identity(tmp_path):
    from editor.models import PlayerInfo
    from editor.player_spec import assess_create

    edit_file = make_player_spec_edit_file(roster_size=39)
    spec = dastan_spec(tmp_path)
    occupied = PlayerInfo(
        player_id=spec.identity.pes_id,
        name="Another Player",
        print_name=spec.identity.print_name or "",
    )

    result = assess_create(edit_file, spec, {occupied.player_id: occupied})

    assert (result.status, result.reason) == (
        "rejected",
        "pes_id_identity_mismatch",
    )


def test_create_rejects_destination_team_name_mismatch(tmp_path):
    from editor.editfile import TE_TEAM_NAME
    from editor.models import PlayerInfo
    from editor.player_spec import assess_create

    edit_file = make_player_spec_edit_file(roster_size=39)
    edit_file._data[
        edit_file.team_start + TE_TEAM_NAME : edit_file.team_start + TE_TEAM_NAME + 11
    ] = b"Arsenal FC\0"

    spec = dastan_spec(tmp_path)
    existing = PlayerInfo(
        player_id=spec.identity.pes_id,
        name=spec.identity.name,
    )
    result = assess_create(edit_file, spec, {existing.player_id: existing})

    assert (result.status, result.reason) == (
        "rejected",
        "destination_team_name_mismatch",
    )


def test_inactive_create_returns_before_identity_or_edit_access(tmp_path, monkeypatch):
    from dataclasses import replace

    from editor.player_spec import assess_create

    edit_file = make_player_spec_edit_file(roster_size=39)
    spec = replace(
        dastan_spec(tmp_path),
        lifecycle_status="retired",
        lifecycle_reason="Historical record",
    )
    monkeypatch.setattr(
        edit_file,
        "get_all_team_info",
        lambda: pytest.fail("inactive create must not inspect teams"),
    )
    monkeypatch.setattr(
        edit_file,
        "get_team_roster",
        lambda *args: pytest.fail("inactive create must not inspect rosters"),
    )

    class InaccessiblePlayers(dict):
        def get(self, *args, **kwargs):
            pytest.fail("inactive create must not inspect identities")

        def values(self):
            pytest.fail("inactive create must not inspect identities")

    result = assess_create(edit_file, spec, InaccessiblePlayers())

    assert (result.status, result.reason) == ("retired", "Historical record")
