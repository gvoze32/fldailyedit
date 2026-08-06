"""Deterministic PES 2021 player overall-rating calculations."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from editor.player_codec import ABILITY_FIELDS, POSITION_NAMES

__all__ = (
    "OVR_MODEL",
    "PlayerOvrError",
    "calculate_ovr_tenths",
    "relevant_ovr_positions",
)

OVR_MODEL: Final = "pes2021-community-estimate-v1"


class PlayerOvrError(ValueError):
    """Raised when OVR inputs are invalid or a position has no weights."""


# Values are the prototype weights expressed as integer hundredths.  The
# position order is deliberately inherited from editor.player_codec.
_WEIGHTS: Mapping[str, tuple[int, ...]] = MappingProxyType(
    {
        "attacking_awareness": (5, 5, 5, 5, 5, 5, 5, 5, 10, 10, 10, 12, 15),
        "ball_control": (3, 8, 8, 8, 10, 15, 12, 12, 15, 14, 14, 14, 10),
        "dribbling": (3, 4, 8, 8, 5, 10, 12, 12, 14, 18, 18, 14, 10),
        "tight_possession": (3, 4, 4, 4, 5, 8, 8, 8, 10, 8, 8, 8, 5),
        "low_pass": (3, 5, 10, 10, 10, 14, 12, 12, 14, 8, 8, 10, 5),
        "lofted_pass": (3, 5, 10, 10, 5, 5, 8, 8, 5, 5, 5, 5, 5),
        "finishing": (3, 3, 3, 3, 3, 4, 4, 4, 10, 14, 14, 18, 25),
        "heading": (3, 10, 5, 5, 5, 5, 5, 5, 5, 5, 5, 6, 10),
        "place_kicking": (3, 3, 3, 3, 4, 4, 4, 4, 4, 4, 4, 4, 4),
        "curl": (3, 3, 4, 4, 4, 4, 4, 4, 5, 5, 5, 5, 5),
        "speed": (3, 5, 10, 10, 5, 5, 10, 10, 10, 15, 15, 10, 10),
        "acceleration": (3, 5, 10, 10, 5, 5, 10, 10, 10, 15, 15, 10, 10),
        "kicking_power": (3, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 5),
        "jump": (3, 10, 5, 5, 5, 5, 5, 5, 4, 4, 4, 4, 6),
        "physical_contact": (3, 10, 10, 10, 10, 5, 5, 5, 4, 4, 4, 4, 4),
        "balance": (3, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4),
        "stamina": (3, 5, 10, 10, 10, 10, 10, 10, 8, 8, 8, 8, 6),
        "defensive_awareness": (5, 20, 16, 16, 16, 10, 10, 10, 5, 4, 4, 4, 3),
        "ball_winning": (3, 15, 10, 10, 15, 10, 5, 5, 4, 4, 4, 4, 3),
        "aggression": (3, 5, 5, 5, 6, 5, 5, 5, 4, 4, 4, 4, 4),
        "gk_awareness": (32, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
        "catching": (15, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
        "clearing": (5, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
        "reflexes": (15, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
        "gk_reach": (10, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
    }
)

_ABILITY_FIELD_SET = frozenset(ABILITY_FIELDS)
_POSITION_SET = frozenset(POSITION_NAMES)


def _normalize_position(value: object) -> str:
    return value.upper() if isinstance(value, str) else ""


def calculate_ovr_tenths(
    abilities: Mapping[str, int], position: str
) -> int:
    """Return the weighted OVR for ``position`` in integer tenths."""
    if not isinstance(abilities, Mapping) or set(abilities) != _ABILITY_FIELD_SET:
        raise PlayerOvrError("OVR abilities must exactly match ABILITY_FIELDS")
    for field in ABILITY_FIELDS:
        value = abilities[field]
        if type(value) is not int or not 40 <= value <= 99:
            raise PlayerOvrError(
                f"OVR ability {field} must be an integer from 40 to 99"
            )

    normalized = _normalize_position(position)
    if normalized not in _POSITION_SET:
        raise PlayerOvrError(f"unsupported OVR position: {position!r}")
    index = POSITION_NAMES.index(normalized)
    total_weight = sum(_WEIGHTS[field][index] for field in ABILITY_FIELDS)
    if total_weight <= 0:
        raise PlayerOvrError(f"OVR position {normalized} has no weights")
    weighted_sum = sum(
        _WEIGHTS[field][index] * abilities[field] for field in ABILITY_FIELDS
    )
    return (weighted_sum * 20 + total_weight) // (2 * total_weight)


def relevant_ovr_positions(
    registered_position: str,
    position_proficiency: Mapping[str, int],
) -> tuple[str, ...]:
    """Return registered and proficient positions in codec order."""
    registered = _normalize_position(registered_position)
    if registered not in _POSITION_SET:
        raise PlayerOvrError(
            f"unsupported OVR registered position: {registered_position!r}"
        )
    if not isinstance(position_proficiency, Mapping):
        raise PlayerOvrError("OVR position proficiency must be a mapping")

    selected = {registered}
    for raw_position, grade in position_proficiency.items():
        position = _normalize_position(raw_position)
        if position not in _POSITION_SET:
            raise PlayerOvrError(
                f"unsupported OVR proficiency position: {raw_position!r}"
            )
        if type(grade) is not int or not 0 <= grade <= 2:
            raise PlayerOvrError(
                f"OVR proficiency {raw_position!r} must be an integer from 0 to 2"
            )
        if grade:
            selected.add(position)

    return tuple(position for position in POSITION_NAMES if position in selected)
