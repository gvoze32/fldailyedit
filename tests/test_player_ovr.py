import pytest

from editor.player_codec import ABILITY_FIELDS, POSITION_NAMES
from editor.player_ovr import (
    PlayerOvrError,
    calculate_ovr_tenths,
    relevant_ovr_positions,
)


def test_verified_formula_has_position_specific_registered_results():
    abilities = {field: 60 for field in ABILITY_FIELDS}
    assert {
        position: calculate_ovr_tenths(abilities, position)
        for position in POSITION_NAMES
    } == {
        "GK": 500,
        "CB": 520,
        "LB": 550,
        "RB": 550,
        "DMF": 550,
        "CMF": 530,
        "LMF": 520,
        "RMF": 520,
        "AMF": 530,
        "LWF": 540,
        "RWF": 540,
        "SS": 530,
        "CF": 530,
    }


def test_asymmetric_vector_has_exact_position_weighted_results():
    abilities = {
        field: 40 + index * 2 for index, field in enumerate(ABILITY_FIELDS)
    }
    assert {
        position: calculate_ovr_tenths(abilities, position)
        for position in POSITION_NAMES
    } == {
        "GK": 750,
        "CB": 590,
        "LB": 560,
        "RB": 560,
        "DMF": 510,
        "CMF": 470,
        "LMF": 480,
        "RMF": 480,
        "AMF": 450,
        "LWF": 480,
        "RWF": 480,
        "SS": 480,
        "CF": 450,
    }
def test_off_position_uses_proficiency_bonus_without_curve():
    abilities = {field: 60 for field in ABILITY_FIELDS}

    assert calculate_ovr_tenths(
        abilities,
        "RWF",
        registered_position="RB",
        position_rating="A",
        weak_foot_accuracy=3,
    ) == 500
    assert calculate_ovr_tenths(
        abilities,
        "RWF",
        registered_position="RB",
        position_rating="B",
        weak_foot_accuracy=3,
    ) == 480
    assert calculate_ovr_tenths(
        abilities,
        "RWF",
        registered_position="RB",
        position_rating="C",
        weak_foot_accuracy=3,
    ) == 460
    with pytest.raises(PlayerOvrError, match="position_rating"):
        calculate_ovr_tenths(
            abilities, "RWF", registered_position="RB", weak_foot_accuracy=3
        )


def test_weak_foot_accuracy_uses_the_one_to_four_scale():
    abilities = {field: 60 for field in ABILITY_FIELDS}
    assert calculate_ovr_tenths(abilities, "RB", weak_foot_accuracy=1) == 550
    assert calculate_ovr_tenths(abilities, "RB", weak_foot_accuracy=4) == 560


def test_relevant_positions_are_unique_and_codec_ordered():
    assert relevant_ovr_positions(
        "RB", {"CF": 2, "RB": 2, "LWF": 1, "GK": 0}
    ) == ("RB", "LWF", "CF")


@pytest.mark.parametrize(
    "abilities, position",
    [
        ({field: 60 for field in ABILITY_FIELDS[:-1]}, "CF"),
        ({**{field: 60 for field in ABILITY_FIELDS}, "extra": 60}, "CF"),
        ({**{field: 60 for field in ABILITY_FIELDS}, "speed": True}, "CF"),
        ({**{field: 60 for field in ABILITY_FIELDS}, "speed": 100}, "CF"),
        ({field: 60 for field in ABILITY_FIELDS}, "RWB"),
    ],
)
def test_ovr_fails_closed_for_invalid_inputs(abilities, position):
    with pytest.raises(PlayerOvrError):
        calculate_ovr_tenths(abilities, position)

PALESTRA_RB_ABILITIES = {
    "attacking_awareness": 72,
    "ball_control": 80,
    "dribbling": 86,
    "tight_possession": 81,
    "low_pass": 72,
    "lofted_pass": 83,
    "finishing": 64,
    "heading": 70,
    "place_kicking": 63,
    "curl": 72,
    "speed": 90,
    "acceleration": 85,
    "kicking_power": 79,
    "jump": 81,
    "physical_contact": 77,
    "balance": 70,
    "stamina": 85,
    "defensive_awareness": 65,
    "ball_winning": 69,
    "aggression": 71,
    "gk_awareness": 40,
    "catching": 40,
    "clearing": 40,
    "reflexes": 40,
    "gk_reach": 40,
}


def test_palestra_source_stats_use_verified_formula_at_rb():
    ovr_tenths = calculate_ovr_tenths(
        PALESTRA_RB_ABILITIES, "RB", weak_foot_accuracy=3
    )
    assert ovr_tenths == 840
    assert ovr_tenths // 10 == 84
