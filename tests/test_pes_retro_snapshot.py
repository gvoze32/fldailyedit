from collections.abc import Callable
from copy import deepcopy
from datetime import date
import hashlib
import json
from types import MappingProxyType

import pytest

from scraper.pes_retro_snapshot import (
    SOURCE_MODEL,
    PesRetroSnapshotError,
    profile_from_snapshot,
    profile_to_snapshot,
)
from scraper.pes_retro_stats import PesRetroStatsProfile


PROFILE_URL = "https://pesretrostats.com/player/0ce2dbde-marco-palestra"
STAT_KEYS = (
    "attacking_prowess",
    "technique",
    "dribbling",
    "dribble_accuracy",
    "short_pass_accuracy",
    "long_pass_accuracy",
    "shot_accuracy",
    "heading",
    "free_kick_accuracy",
    "swerve",
    "top_speed",
    "acceleration",
    "shot_power",
    "jump",
    "physical_contact",
    "body_control",
    "stamina",
    "defensive_awareness",
    "ball_winning",
    "new_aggression",
    "gk_awareness",
    "gk_catching",
    "gk_clearing",
    "gk_reflexes",
    "gk_reach",
)
POSITION_KEYS = (
    "GK",
    "CB",
    "LB",
    "RB",
    "CWP",
    "DMF",
    "LWB",
    "RWB",
    "CMF",
    "LMF",
    "RMF",
    "AMF",
    "LWF",
    "RWF",
    "SS",
    "CF",
)
PROFILE_DATA_KEYS = {
    "player_id",
    "short_id",
    "name",
    "full_name",
    "profile_url",
    "birth_date",
    "nationality",
    "current_club",
    "shirt_number",
    "height",
    "weight",
    "strong_foot",
    "weak_foot_accuracy",
    "weak_foot_frequency",
    "form",
    "injury_tolerance",
    "playing_style",
    "positions",
    "stats",
    "player_skill_codes",
    "com_playing_styles",
}
RAW_PAYLOAD_SENTINEL = "Private Payload Sentinel 7f3a9c"
Snapshot = dict[str, object]
Mutation = Callable[[Snapshot], None]


def complete_profile() -> PesRetroStatsProfile:
    positions = {key: None for key in POSITION_KEYS}
    positions.update({"RB": "A", "RWB": "★", "RMF": "A"})
    stats = {key: 40 + index for index, key in enumerate(STAT_KEYS)}
    return PesRetroStatsProfile(
        player_id="0ce2dbde-9cd9-423c-a90a-35b07df6a967",
        short_id="0ce2dbde",
        name="Marco Palestra",
        full_name="Marco Palestra",
        profile_url=PROFILE_URL,
        birth_date=date(2005, 3, 3),
        nationality="Italy",
        current_club="Chelsea FC",
        shirt_number=2,
        height=186,
        weight=80,
        strong_foot="R",
        weak_foot_accuracy=7,
        weak_foot_frequency=7,
        form=4,
        injury_tolerance="A",
        playing_style="Offensive Full-back",
        positions=MappingProxyType(positions),
        stats=MappingProxyType(stats),
        player_skill_codes=("S01", "S07"),
        com_playing_styles=("Speeding Bullet", "Mazing Run"),
    )


def _data(snapshot: Snapshot) -> dict[str, object]:
    value = snapshot["data"]
    assert isinstance(value, dict)
    return value


def _nested_mapping(snapshot: Snapshot, key: str) -> dict[str, object]:
    value = _data(snapshot)[key]
    assert isinstance(value, dict)
    return value


def _resign(snapshot: Snapshot) -> None:
    canonical = json.dumps(
        {"model": snapshot["model"], "data": snapshot["data"]},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    snapshot["snapshot_sha256"] = hashlib.sha256(canonical).hexdigest()


def test_snapshot_has_exact_model_canonical_hash_and_roundtrips() -> None:
    profile = complete_profile()

    snapshot = profile_to_snapshot(profile)

    assert SOURCE_MODEL == "pes-retro-normalized-v1"
    assert set(snapshot) == {"model", "data", "snapshot_sha256"}
    assert snapshot["model"] == "pes-retro-normalized-v1"
    data = _data(snapshot)
    assert set(data) == PROFILE_DATA_KEYS
    assert data["birth_date"] == "2005-03-03"
    assert data["player_skill_codes"] == ["S01", "S07"]
    assert data["com_playing_styles"] == ["Speeding Bullet", "Mazing Run"]
    assert tuple(_nested_mapping(snapshot, "stats")) == STAT_KEYS
    assert tuple(_nested_mapping(snapshot, "positions")) == POSITION_KEYS
    canonical = json.dumps(
        {"model": snapshot["model"], "data": snapshot["data"]},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert snapshot["snapshot_sha256"] == hashlib.sha256(canonical).hexdigest()

    restored = profile_from_snapshot(snapshot)

    assert restored == profile
    assert type(restored.positions) is MappingProxyType
    assert type(restored.stats) is MappingProxyType
    assert isinstance(restored.player_skill_codes, tuple)
    assert isinstance(restored.com_playing_styles, tuple)


def _tamper_one_field(snapshot: Snapshot) -> None:
    _data(snapshot)["current_club"] = "Inter Milan"


def _use_wrong_model(snapshot: Snapshot) -> None:
    snapshot["model"] = "pes-retro-normalized-v2"


def _add_unknown_snapshot_key(snapshot: Snapshot) -> None:
    snapshot["unexpected"] = True


def _remove_snapshot_key(snapshot: Snapshot) -> None:
    del snapshot["snapshot_sha256"]


def _add_unknown_data_key(snapshot: Snapshot) -> None:
    _data(snapshot)["unexpected"] = True


def _remove_data_key(snapshot: Snapshot) -> None:
    del _data(snapshot)["current_club"]


def _add_unknown_stat_key(snapshot: Snapshot) -> None:
    _nested_mapping(snapshot, "stats")["finishing"] = 99


def _add_unknown_position_key(snapshot: Snapshot) -> None:
    _nested_mapping(snapshot, "positions")["SW"] = "A"


def _use_bool_for_required_integer(snapshot: Snapshot) -> None:
    _data(snapshot)["height"] = True


def _use_bool_for_optional_integer(snapshot: Snapshot) -> None:
    _data(snapshot)["shirt_number"] = False


def _use_bool_for_stat(snapshot: Snapshot) -> None:
    _nested_mapping(snapshot, "stats")["stamina"] = True


def _remove_complete_stat(snapshot: Snapshot) -> None:
    del _nested_mapping(snapshot, "stats")["gk_reach"]


def _remove_complete_position(snapshot: Snapshot) -> None:
    del _nested_mapping(snapshot, "positions")["CF"]


def _duplicate_player_skill(snapshot: Snapshot) -> None:
    _data(snapshot)["player_skill_codes"] = ["S01", "S01"]


def _duplicate_com_playing_style(snapshot: Snapshot) -> None:
    _data(snapshot)["com_playing_styles"] = ["Mazing Run", "Mazing Run"]


def _use_noncanonical_url(snapshot: Snapshot) -> None:
    _data(snapshot)["profile_url"] = PROFILE_URL + "?source=test"


def _use_noncanonical_uuid(snapshot: Snapshot) -> None:
    _data(snapshot)["player_id"] = "0ce2dbde-9CD9-423C-A90A-35B07DF6A967"


def _use_unsupported_uuid_version(snapshot: Snapshot) -> None:
    _data(snapshot)["player_id"] = "0ce2dbde-6cd9-623c-a90a-35b07df6a967"


def _mismatch_uuid_prefix(snapshot: Snapshot) -> None:
    _data(snapshot)["player_id"] = "f77d9c27-9cd9-423c-a90a-35b07df6a967"


def _mismatch_url_short_id(snapshot: Snapshot) -> None:
    _data(snapshot)["short_id"] = "f77d9c27"


def _use_invalid_hash(snapshot: Snapshot) -> None:
    snapshot["snapshot_sha256"] = "not-a-sha256"


@pytest.mark.parametrize(
    ("mutate", "resign"),
    [
        pytest.param(_tamper_one_field, False, id="one-field-tampering"),
        pytest.param(_use_wrong_model, True, id="wrong-model"),
        pytest.param(_add_unknown_snapshot_key, True, id="unknown-snapshot-key"),
        pytest.param(_remove_snapshot_key, False, id="missing-snapshot-key"),
        pytest.param(_add_unknown_data_key, True, id="unknown-data-key"),
        pytest.param(_remove_data_key, True, id="missing-data-key"),
        pytest.param(_add_unknown_stat_key, True, id="unknown-stat-key"),
        pytest.param(_add_unknown_position_key, True, id="unknown-position-key"),
        pytest.param(
            _use_bool_for_required_integer, True, id="bool-required-integer"
        ),
        pytest.param(
            _use_bool_for_optional_integer, True, id="bool-optional-integer"
        ),
        pytest.param(_use_bool_for_stat, True, id="bool-stat"),
        pytest.param(_remove_complete_stat, True, id="missing-complete-stat"),
        pytest.param(
            _remove_complete_position, True, id="missing-complete-position"
        ),
        pytest.param(_duplicate_player_skill, True, id="duplicate-player-skill"),
        pytest.param(
            _duplicate_com_playing_style, True, id="duplicate-com-playing-style"
        ),
        pytest.param(_use_noncanonical_url, True, id="noncanonical-url"),
        pytest.param(_use_noncanonical_uuid, True, id="noncanonical-uuid"),
        pytest.param(
            _use_unsupported_uuid_version, True, id="unsupported-uuid-version"
        ),
        pytest.param(_mismatch_uuid_prefix, True, id="uuid-prefix-mismatch"),
        pytest.param(_mismatch_url_short_id, True, id="url-short-id-mismatch"),
        pytest.param(_use_invalid_hash, False, id="invalid-hash"),
    ],
)
def test_profile_from_snapshot_rejects_invalid_payloads_without_dumping_them(
    mutate: Mutation, resign: bool
) -> None:
    snapshot = deepcopy(profile_to_snapshot(complete_profile()))
    _data(snapshot)["name"] = RAW_PAYLOAD_SENTINEL
    _resign(snapshot)
    mutate(snapshot)
    if resign:
        _resign(snapshot)

    with pytest.raises(PesRetroSnapshotError) as raised:
        profile_from_snapshot(snapshot)

    message = str(raised.value)
    assert RAW_PAYLOAD_SENTINEL not in message
    assert repr(snapshot) not in message
