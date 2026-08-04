from dataclasses import FrozenInstanceError
from datetime import date
from types import MappingProxyType

import pytest

from editor.player_codec import (
    ABILITY_FIELDS,
    COM_STYLE_FIELDS,
    PLAYER_SKILL_FIELDS,
    POSITION_NAMES,
)
from scraper.pes21_proposal import Pes21Proposal, map_pes21_proposal
from scraper.pes_retro_stats import PesRetroStatsError, PesRetroStatsProfile


ABILITY_SOURCE_MAP = {
    "attacking_prowess": "attacking_awareness",
    "technique": "ball_control",
    "dribbling": "dribbling",
    "dribble_accuracy": "tight_possession",
    "short_pass_accuracy": "low_pass",
    "long_pass_accuracy": "lofted_pass",
    "shot_accuracy": "finishing",
    "heading": "heading",
    "free_kick_accuracy": "place_kicking",
    "swerve": "curl",
    "top_speed": "speed",
    "acceleration": "acceleration",
    "shot_power": "kicking_power",
    "jump": "jump",
    "physical_contact": "physical_contact",
    "body_control": "balance",
    "stamina": "stamina",
    "defensive_awareness": "defensive_awareness",
    "ball_winning": "ball_winning",
    "new_aggression": "aggression",
    "gk_awareness": "gk_awareness",
    "gk_catching": "catching",
    "gk_clearing": "clearing",
    "gk_reflexes": "reflexes",
    "gk_reach": "gk_reach",
}

PLAYING_STYLE_IDS = {
    None: 0,
    "Goal Poacher": 1,
    "Dummy Runner": 2,
    "Fox in the Box": 3,
    "Target Man": 4,
    "Creative Playmaker": 5,
    "Prolific Winger": 6,
    "Roaming Flank": 7,
    "Cross Specialist": 8,
    "Classic No. 10": 9,
    "Hole Player": 10,
    "Box-to-Box": 11,
    "The Destroyer": 12,
    "Orchestrator": 13,
    "Anchor Man": 14,
    "Offensive Full-back": 15,
    "Full-back Finisher": 16,
    "Defensive Full-back": 17,
    "Build Up": 18,
    "Extra Frontman": 19,
    "Offensive Goalkeeper": 20,
    "Defensive Goalkeeper": 21,
}

PES_RETRO_SKILLS = dict(
    zip(
        (f"S{number:02d}" for number in range(1, 42)),
        (
            "scissors_feint",
            "double_touch",
            "flip_flap",
            "marseille_turn",
            "sombrero",
            "crossover_turn",
            "cut_behind_and_turn",
            "scotch_move",
            "step_on_skill_control",
            "heading_skill",
            "long_range_drive",
            "chip_shot_control",
            "long_range_shooting",
            "knuckle_shot",
            "dipping_shot",
            "rising_shot",
            "acrobatic_finishing",
            "heel_trick",
            "first_time_shot",
            "one_touch_pass",
            "through_passing",
            "weighted_pass",
            "pinpoint_crossing",
            "outside_curler",
            "rabona",
            "no_look_pass",
            "low_lofted_pass",
            "low_punt_trajectory",
            "gk_high_punt",
            "long_throw",
            "gk_long_throw",
            "penalty_specialist",
            "gk_penalty_saver",
            "gamesmanship",
            "man_marking",
            "track_back",
            "interception",
            "acrobatic_clear",
            "captaincy",
            "super_sub",
            "fighting_spirit",
        ),
        strict=True,
    )
)

PES_RETRO_COM_STYLES = {
    "Trickster": "trickster",
    "Mazing Run": "mazing_run",
    "Speeding Bullet": "speeding_bullet",
    "Incisive Run": "incisive_run",
    "Long Ball Expert": "long_ball_expert",
    "Early Cross": "early_cross",
    "Long Ranger": "long_ranger",
}

SOURCE_POSITIONS = (
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


def _profile(**overrides):
    values = {
        "player_id": "12345678-1234-1234-1234-123456789abc",
        "short_id": "12345678",
        "name": "Test Player",
        "full_name": None,
        "profile_url": "https://pesretrostats.com/player/12345678-test-player",
        "birth_date": date(2000, 1, 2),
        "nationality": "Testland",
        "current_club": "Test FC",
        "shirt_number": 10,
        "height": 180,
        "weight": 75,
        "strong_foot": "R",
        "weak_foot_accuracy": 5,
        "weak_foot_frequency": 6,
        "form": 6,
        "injury_tolerance": "B",
        "playing_style": None,
        "positions": MappingProxyType(
            {position: "★" if position == "CF" else None for position in SOURCE_POSITIONS}
        ),
        "stats": MappingProxyType({source: 70 for source in ABILITY_SOURCE_MAP}),
        "player_skill_codes": (),
        "com_playing_styles": (),
    }
    values.update(overrides)
    return PesRetroStatsProfile(**values)


@pytest.fixture
def profile_factory():
    return _profile


def _map(profile):
    return map_pes21_proposal(profile, effective_date=date(2026, 8, 4))


def test_map_pes21_proposal_maps_every_ability(profile_factory):
    source = {name: 40 + index for index, name in enumerate(ABILITY_SOURCE_MAP)}
    proposal = _map(profile_factory(stats=MappingProxyType(source)))

    assert proposal.abilities == {
        target: source[source_name]
        for source_name, target in ABILITY_SOURCE_MAP.items()
    }
    assert tuple(proposal.abilities) == ABILITY_FIELDS


@pytest.mark.parametrize(
    "stats",
    [
        pytest.param(
            {name: 70 for name in tuple(ABILITY_SOURCE_MAP)[1:]}, id="missing-key"
        ),
        pytest.param(
            {**{name: 70 for name in ABILITY_SOURCE_MAP}, "ball_control": 70},
            id="extra-pes-2021-key",
        ),
    ],
)
def test_ability_keys_must_match_the_complete_source_map(profile_factory, stats):
    with pytest.raises(PesRetroStatsError):
        _map(profile_factory(stats=MappingProxyType(stats)))


@pytest.mark.parametrize(
    "value", [True, False, 70.0, "70", None, 39, 100]
)
def test_abilities_reject_non_integers_and_values_outside_40_to_99(
    profile_factory, value
):
    stats = {name: 70 for name in ABILITY_SOURCE_MAP}
    stats["attacking_prowess"] = value

    with pytest.raises(PesRetroStatsError):
        _map(profile_factory(stats=MappingProxyType(stats)))


def test_ability_and_position_outputs_are_immutable(profile_factory):
    proposal = _map(profile_factory())

    with pytest.raises(TypeError):
        proposal.abilities["speed"] = 99
    with pytest.raises(TypeError):
        proposal.position_proficiency["CF"] = 0
    with pytest.raises(FrozenInstanceError):
        proposal.form = 0
    assert Pes21Proposal.__slots__


@pytest.mark.parametrize("source, encoded", PLAYING_STYLE_IDS.items())
def test_every_playing_style_maps_to_its_codec_id(profile_factory, source, encoded):
    assert _map(profile_factory(playing_style=source)).playing_style == encoded


@pytest.mark.parametrize("source", ["Unknown", "goal poacher", 1, True])
def test_playing_style_rejects_unknown_names_and_non_strings(profile_factory, source):
    with pytest.raises(PesRetroStatsError):
        _map(profile_factory(playing_style=source))


@pytest.mark.parametrize("source, encoded", [("R", 0), ("L", 1)])
def test_strong_foot_maps_to_one_bit(profile_factory, source, encoded):
    assert _map(profile_factory(strong_foot=source)).strong_foot == encoded


@pytest.mark.parametrize("source", ["Right", "r", 0, True, None])
def test_strong_foot_rejects_unknown_values(profile_factory, source):
    with pytest.raises(PesRetroStatsError):
        _map(profile_factory(strong_foot=source))


@pytest.mark.parametrize("source, encoded", [("C", 0), ("B", 1), ("A", 2)])
def test_injury_tolerance_maps_to_two_bits(profile_factory, source, encoded):
    assert _map(profile_factory(injury_tolerance=source)).injury_resistance == encoded


@pytest.mark.parametrize("source", ["D", "c", 0, True, None])
def test_injury_tolerance_rejects_unknown_values(profile_factory, source):
    with pytest.raises(PesRetroStatsError):
        _map(profile_factory(injury_tolerance=source))


@pytest.mark.parametrize(
    ("source", "encoded"),
    [(1, 0), (2, 0), (3, 1), (4, 1), (5, 2), (6, 2), (7, 3), (8, 3)],
)
def test_weak_foot_eight_point_scale_maps_to_two_bits(
    profile_factory, source, encoded
):
    proposal = _map(
        profile_factory(
            weak_foot_accuracy=source,
            weak_foot_frequency=source,
        )
    )
    assert proposal.weak_foot_accuracy == encoded
    assert proposal.weak_foot_usage == encoded


@pytest.mark.parametrize("field", ["weak_foot_accuracy", "weak_foot_frequency"])
@pytest.mark.parametrize("source", [True, False, 1.0, "1", None, 0, 9])
def test_weak_foot_rejects_non_integers_and_out_of_range_values(
    profile_factory, field, source
):
    with pytest.raises(PesRetroStatsError):
        _map(profile_factory(**{field: source}))


@pytest.mark.parametrize("source", range(1, 9))
def test_form_maps_from_one_to_eight_into_zero_to_seven(profile_factory, source):
    assert _map(profile_factory(form=source)).form == source - 1


@pytest.mark.parametrize("source", [True, False, 1.0, "1", None, 0, 9])
def test_form_rejects_non_integers_and_out_of_range_values(profile_factory, source):
    with pytest.raises(PesRetroStatsError):
        _map(profile_factory(form=source))


def test_supported_registered_and_proficient_positions_are_mapped(profile_factory):
    positions = {position: None for position in SOURCE_POSITIONS}
    positions.update({"SS": "★", "CF": "A", "AMF": "B"})

    proposal = _map(profile_factory(positions=MappingProxyType(positions)))

    assert proposal.registered_position == "SS"
    assert proposal.unsupported_positions == ()
    assert tuple(proposal.position_proficiency) == POSITION_NAMES
    assert proposal.position_proficiency == {
        position: {"SS": 2, "CF": 2, "AMF": 1}.get(position, 0)
        for position in POSITION_NAMES
    }


def test_unsupported_registered_position_is_not_remapped(profile_factory):
    positions = {position: None for position in SOURCE_POSITIONS}
    positions.update({"RWB": "★", "RB": "A", "RMF": "B"})

    proposal = _map(profile_factory(positions=MappingProxyType(positions)))

    assert proposal.registered_position is None
    assert proposal.unsupported_positions == ("RWB",)
    assert proposal.position_proficiency["RB"] == 2
    assert proposal.position_proficiency["RMF"] == 1
    assert "RWB" not in proposal.position_proficiency


@pytest.mark.parametrize("unsupported", ["CWP", "LWB", "RWB"])
def test_each_unsupported_registered_position_is_reported(profile_factory, unsupported):
    positions = {position: None for position in SOURCE_POSITIONS}
    positions[unsupported] = "★"

    proposal = _map(profile_factory(positions=MappingProxyType(positions)))

    assert proposal.registered_position is None
    assert proposal.unsupported_positions == (unsupported,)


@pytest.mark.parametrize("grade, encoded", [(None, 0), ("B", 1), ("A", 2), ("★", 2)])
def test_every_supported_position_grade_maps(profile_factory, grade, encoded):
    positions = {position: None for position in SOURCE_POSITIONS}
    positions["CF"] = "★"
    positions["RB"] = grade
    if grade == "★":
        positions["CF"] = None

    proposal = _map(profile_factory(positions=MappingProxyType(positions)))

    assert proposal.position_proficiency["RB"] == encoded
    if grade == "★":
        assert proposal.registered_position == "RB"


def test_more_than_one_registered_position_is_rejected(profile_factory):
    positions = {position: None for position in SOURCE_POSITIONS}
    positions.update({"SS": "★", "RWB": "★"})

    with pytest.raises(PesRetroStatsError):
        _map(profile_factory(positions=MappingProxyType(positions)))


@pytest.mark.parametrize("grade", ["C", "a", 2, True, False, []])
def test_position_grades_reject_unknown_values(profile_factory, grade):
    positions = {position: None for position in SOURCE_POSITIONS}
    positions.update({"CF": "★", "RB": grade})

    with pytest.raises(PesRetroStatsError):
        _map(profile_factory(positions=MappingProxyType(positions)))


@pytest.mark.parametrize("mutation", ["missing", "extra"])
def test_position_keys_must_match_the_source_position_set(profile_factory, mutation):
    positions = {position: None for position in SOURCE_POSITIONS}
    positions["CF"] = "★"
    if mutation == "missing":
        positions.pop("GK")
    else:
        positions["SWP"] = None

    with pytest.raises(PesRetroStatsError):
        _map(profile_factory(positions=MappingProxyType(positions)))


@pytest.mark.parametrize(
    "birth_date, effective_date, expected",
    [
        (date(2004, 2, 29), date(2026, 2, 28), 21),
        (date(2004, 2, 29), date(2026, 3, 1), 22),
        (date(2004, 2, 29), date(2024, 2, 29), 20),
    ],
)
def test_age_handles_leap_day_birthdays(
    profile_factory, birth_date, effective_date, expected
):
    proposal = map_pes21_proposal(
        profile_factory(birth_date=birth_date), effective_date=effective_date
    )
    assert proposal.age == expected


def test_future_birth_date_is_rejected(profile_factory):
    with pytest.raises(
        PesRetroStatsError, match="^Pes Retro Stats birth date is invalid$"
    ):
        map_pes21_proposal(
            profile_factory(birth_date=date(2026, 8, 5)),
            effective_date=date(2026, 8, 4),
        )


@pytest.mark.parametrize("birth_date", ["2000-01-02", None, 0, True])
def test_birth_date_requires_an_exact_date(profile_factory, birth_date):
    with pytest.raises(PesRetroStatsError):
        _map(profile_factory(birth_date=birth_date))


@pytest.mark.parametrize("effective_date", ["2026-08-04", None, 0, True])
def test_effective_date_requires_an_exact_date(profile_factory, effective_date):
    with pytest.raises(PesRetroStatsError):
        map_pes21_proposal(profile_factory(), effective_date=effective_date)


@pytest.mark.parametrize(
    "birth_date, effective_date, expected",
    [
        (date(2026, 8, 4), date(2026, 8, 4), 0),
        (date(1962, 8, 4), date(2026, 8, 3), 63),
    ],
)
def test_age_accepts_codec_boundaries(
    profile_factory, birth_date, effective_date, expected
):
    assert (
        map_pes21_proposal(
            profile_factory(birth_date=birth_date), effective_date=effective_date
        ).age
        == expected
    )


def test_age_rejects_values_above_codec_range(profile_factory):
    with pytest.raises(PesRetroStatsError):
        map_pes21_proposal(
            profile_factory(birth_date=date(1962, 8, 3)),
            effective_date=date(2026, 8, 4),
        )


@pytest.mark.parametrize("field", ["height", "weight"])
@pytest.mark.parametrize("value", [0, 255])
def test_physical_values_accept_codec_boundaries(profile_factory, field, value):
    proposal = _map(profile_factory(**{field: value}))
    assert getattr(proposal, field) == value


@pytest.mark.parametrize("field", ["height", "weight"])
@pytest.mark.parametrize("value", [True, False, 1.0, "1", None, -1, 256])
def test_physical_values_reject_non_integers_and_out_of_range_values(
    profile_factory, field, value
):
    with pytest.raises(PesRetroStatsError):
        _map(profile_factory(**{field: value}))


def test_all_41_skill_codes_map_in_source_order(profile_factory):
    source_codes = tuple(PES_RETRO_SKILLS)
    proposal = _map(profile_factory(player_skill_codes=source_codes))

    assert proposal.player_skills == tuple(PES_RETRO_SKILLS.values())
    assert len(proposal.player_skills) == 41
    assert all(skill in PLAYER_SKILL_FIELDS for skill in proposal.player_skills)


def test_skill_mapping_preserves_arbitrary_source_order(profile_factory):
    proposal = _map(profile_factory(player_skill_codes=("S41", "S02", "S13")))
    assert proposal.player_skills == (
        "fighting_spirit",
        "double_touch",
        "long_range_shooting",
    )


@pytest.mark.parametrize(
    "codes", [("S42",), ("s01",), ("S01", "S01"), ("S01", 1)]
)
def test_skills_reject_unknown_duplicate_and_non_string_inputs(profile_factory, codes):
    with pytest.raises(PesRetroStatsError):
        _map(profile_factory(player_skill_codes=codes))


@pytest.mark.parametrize("codes", [["S01"], "S01", None])
def test_skill_collection_requires_a_tuple(profile_factory, codes):
    with pytest.raises(PesRetroStatsError):
        _map(profile_factory(player_skill_codes=codes))


def test_all_seven_com_styles_map_in_source_order(profile_factory):
    source_styles = tuple(PES_RETRO_COM_STYLES)
    proposal = _map(profile_factory(com_playing_styles=source_styles))

    assert proposal.com_styles == tuple(PES_RETRO_COM_STYLES.values())
    assert len(proposal.com_styles) == 7
    assert all(style in COM_STYLE_FIELDS for style in proposal.com_styles)


def test_com_style_mapping_preserves_arbitrary_source_order(profile_factory):
    proposal = _map(
        profile_factory(com_playing_styles=("Long Ranger", "Trickster", "Early Cross"))
    )
    assert proposal.com_styles == ("long_ranger", "trickster", "early_cross")


@pytest.mark.parametrize(
    "styles",
    [
        ("Unknown",),
        ("trickster",),
        ("Trickster", "Trickster"),
        ("Trickster", 1),
    ],
)
def test_com_styles_reject_unknown_duplicate_and_non_string_inputs(
    profile_factory, styles
):
    with pytest.raises(PesRetroStatsError):
        _map(profile_factory(com_playing_styles=styles))


@pytest.mark.parametrize("styles", [["Trickster"], "Trickster", None])
def test_com_style_collection_requires_a_tuple(profile_factory, styles):
    with pytest.raises(PesRetroStatsError):
        _map(profile_factory(com_playing_styles=styles))
