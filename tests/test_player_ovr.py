import pytest

from editor.player_codec import ABILITY_FIELDS, POSITION_NAMES
from editor.player_ovr import (
    PlayerOvrError,
    calculate_ovr_tenths,
    relevant_ovr_positions,
)


def test_equal_abilities_produce_the_same_ovr_for_every_position():
    abilities = {field: 60 for field in ABILITY_FIELDS}
    assert {
        position: calculate_ovr_tenths(abilities, position)
        for position in POSITION_NAMES
    } == {position: 600 for position in POSITION_NAMES}


def test_asymmetric_vector_has_exact_position_weighted_results():
    abilities = {
        field: 40 + index * 2 for index, field in enumerate(ABILITY_FIELDS)
    }
    assert {
        position: calculate_ovr_tenths(abilities, position)
        for position in POSITION_NAMES
    } == {
        "GK": 720,
        "CB": 623,
        "LB": 604,
        "RB": 612,
        "DMF": 614,
        "CMF": 578,
        "LMF": 575,
        "RMF": 575,
        "AMF": 549,
        "LWF": 553,
        "RWF": 553,
        "SS": 548,
        "CF": 551,
    }


def test_exact_midpoint_rounds_half_up():
    abilities = {field: 60 for field in ABILITY_FIELDS}
    abilities["speed"] = 63
    abilities["finishing"] = 62

    assert calculate_ovr_tenths(abilities, "LB") == 603


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


def test_palestra_source_stats_display_as_81_at_rb():
    ovr_tenths = calculate_ovr_tenths(PALESTRA_RB_ABILITIES, "RB")
    assert ovr_tenths == 811
    assert ovr_tenths // 10 == 81
