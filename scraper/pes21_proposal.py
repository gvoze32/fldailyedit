"""Pure Pes Retro Stats to PES 2021 proposal mappings."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from types import MappingProxyType

from editor.player_codec import (
    ABILITY_FIELDS,
    COM_STYLE_FIELDS,
    FIELD_SPECS,
    PLAYER_SKILL_FIELDS,
    POSITION_NAMES,
)
from scraper.pes_retro_stats import PesRetroStatsError, PesRetroStatsProfile


_ABILITY_SOURCE_MAP = {
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

_PLAYING_STYLE_IDS = {
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

_POSITION_GRADE = {None: 0, "B": 1, "A": 2, "★": 2}
_STRONG_FOOT = {"R": 0, "L": 1}
_INJURY_RESISTANCE = {"C": 0, "B": 1, "A": 2}
_SOURCE_POSITIONS = (
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
_UNSUPPORTED_POSITIONS = frozenset({"CWP", "LWB", "RWB"})

_PES_RETRO_SKILLS = dict(
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

_PES_RETRO_COM_STYLES = {
    "Trickster": "trickster",
    "Mazing Run": "mazing_run",
    "Speeding Bullet": "speeding_bullet",
    "Incisive Run": "incisive_run",
    "Long Ball Expert": "long_ball_expert",
    "Early Cross": "early_cross",
    "Long Ranger": "long_ranger",
}

_INVALID_PROFILE = "Pes Retro Stats profile cannot be mapped to PES 2021"


@dataclass(frozen=True, slots=True)
class Pes21Proposal:
    age: int
    height: int
    weight: int
    registered_position: str | None
    unsupported_positions: tuple[str, ...]
    playing_style: int
    strong_foot: int
    weak_foot_usage: int
    weak_foot_accuracy: int
    form: int
    injury_resistance: int
    position_proficiency: Mapping[str, int]
    abilities: Mapping[str, int]
    player_skills: tuple[str, ...]
    com_styles: tuple[str, ...]


def _invalid() -> PesRetroStatsError:
    return PesRetroStatsError(_INVALID_PROFILE)


def _codec_integer(field: str, value: object) -> int:
    if type(value) is not int or not 0 <= value < 1 << FIELD_SPECS[field].width:
        raise _invalid()
    return value


def _source_scale(value: object, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise _invalid()
    return value


def age_on(birth_date: date, effective_date: date) -> int:
    if type(birth_date) is not date or type(effective_date) is not date:
        raise _invalid()
    if effective_date < birth_date:
        raise PesRetroStatsError("Pes Retro Stats birth date is invalid")
    return effective_date.year - birth_date.year - (
        (effective_date.month, effective_date.day)
        < (birth_date.month, birth_date.day)
    )


def _map_abilities(stats: Mapping[str, object]) -> Mapping[str, int]:
    if not isinstance(stats, Mapping) or set(stats) != set(_ABILITY_SOURCE_MAP):
        raise _invalid()

    abilities: dict[str, int] = {}
    for source, target in _ABILITY_SOURCE_MAP.items():
        abilities[target] = _source_scale(stats[source], 40, 99)
    if tuple(abilities) != ABILITY_FIELDS:
        raise _invalid()
    return MappingProxyType(abilities)


def _map_positions(
    positions: Mapping[str, object],
) -> tuple[str | None, tuple[str, ...], Mapping[str, int]]:
    if not isinstance(positions, Mapping) or set(positions) != set(_SOURCE_POSITIONS):
        raise _invalid()

    registered: list[str] = []
    for position in _SOURCE_POSITIONS:
        grade = positions[position]
        if (grade is not None and type(grade) is not str) or grade not in _POSITION_GRADE:
            raise _invalid()
        if grade == "★":
            registered.append(position)
    if len(registered) > 1:
        raise _invalid()

    position_proficiency = {
        position: _POSITION_GRADE[positions[position]] for position in POSITION_NAMES
    }
    registered_position = None
    unsupported_positions: tuple[str, ...] = ()
    if registered:
        if registered[0] in _UNSUPPORTED_POSITIONS:
            unsupported_positions = (registered[0],)
        else:
            registered_position = registered[0]
    return (
        registered_position,
        unsupported_positions,
        MappingProxyType(position_proficiency),
    )


def _map_named_tuple(
    values: object, source_map: Mapping[str, str], allowed: tuple[str, ...]
) -> tuple[str, ...]:
    if type(values) is not tuple or any(type(value) is not str for value in values):
        raise _invalid()
    if len(values) != len(set(values)):
        raise _invalid()

    try:
        mapped = tuple(source_map[value] for value in values)
    except KeyError:
        raise _invalid() from None
    if any(value not in allowed for value in mapped):
        raise _invalid()
    return mapped


def _map_enum(value: object, source_map: Mapping[object, int]) -> int:
    try:
        mapped = source_map[value]
    except (KeyError, TypeError):
        raise _invalid() from None
    return mapped


def map_pes21_proposal(
    profile: PesRetroStatsProfile, *, effective_date: date
) -> Pes21Proposal:
    """Return a validated immutable PES 2021 proposal for one source profile."""
    age = _codec_integer("age", age_on(profile.birth_date, effective_date))
    height = _codec_integer("height", profile.height)
    weight = _codec_integer("weight", profile.weight)
    registered_position, unsupported_positions, position_proficiency = _map_positions(
        profile.positions
    )

    playing_style = profile.playing_style
    if playing_style is not None and type(playing_style) is not str:
        raise _invalid()
    strong_foot = profile.strong_foot
    if type(strong_foot) is not str:
        raise _invalid()
    injury_tolerance = profile.injury_tolerance
    if type(injury_tolerance) is not str:
        raise _invalid()

    weak_foot_accuracy = (
        _source_scale(profile.weak_foot_accuracy, 1, 8) - 1
    ) // 2
    weak_foot_usage = (
        _source_scale(profile.weak_foot_frequency, 1, 8) - 1
    ) // 2
    form = _source_scale(profile.form, 1, 8) - 1

    return Pes21Proposal(
        age=age,
        height=height,
        weight=weight,
        registered_position=registered_position,
        unsupported_positions=unsupported_positions,
        playing_style=_map_enum(playing_style, _PLAYING_STYLE_IDS),
        strong_foot=_map_enum(strong_foot, _STRONG_FOOT),
        weak_foot_usage=weak_foot_usage,
        weak_foot_accuracy=weak_foot_accuracy,
        form=form,
        injury_resistance=_map_enum(injury_tolerance, _INJURY_RESISTANCE),
        position_proficiency=position_proficiency,
        abilities=_map_abilities(profile.stats),
        player_skills=_map_named_tuple(
            profile.player_skill_codes, _PES_RETRO_SKILLS, PLAYER_SKILL_FIELDS
        ),
        com_styles=_map_named_tuple(
            profile.com_playing_styles, _PES_RETRO_COM_STYLES, COM_STYLE_FIELDS
        ),
    )


__all__ = ["Pes21Proposal", "age_on", "map_pes21_proposal"]
