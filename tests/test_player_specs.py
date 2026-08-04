import hashlib
import json

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
