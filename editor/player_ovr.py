"""Deterministic PES 2021 player overall-rating calculations."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final
from editor.player_codec import ABILITY_FIELDS, POSITION_NAMES

__all__ = (
    "OVR_MODEL",
    "PlayerOvrError",
    "calculate_ovr_tenths",
    "ovr_weak_foot_accuracy",
    "position_rating_for",
    "relevant_ovr_positions",
)

OVR_MODEL: Final = "pes2021-verified-formula-v1"


class PlayerOvrError(ValueError):
    """Raised when OVR inputs are invalid or a position has no weights."""


# The PDF groups LB/RB, LMF/RMF, and LWF/RWF into shared tables.  The rows
# below are expanded into codec position order so every calculation is a
# direct table lookup.
_WEIGHTS: Mapping[str, tuple[int, ...]] = MappingProxyType(
    {
        "attacking_awareness": (0, 0, 6, 6, 7, 5, 7, 7, 14, 17, 17, 15, 31),
        "ball_control": (0, 0, 9, 9, 18, 23, 15, 15, 23, 19, 19, 19, 23),
        "dribbling": (0, 0, 9, 9, 10, 16, 17, 17, 17, 15, 15, 13, 9),
        "tight_possession": (0, 0, 5, 5, 5, 8, 8, 8, 7, 7, 7, 6, 5),
        "low_pass": (0, 0, 0, 0, 18, 23, 7, 7, 21, 5, 5, 9, 0),
        "lofted_pass": (0, 0, 14, 14, 19, 21, 12, 12, 14, 9, 9, 9, 0),
        "finishing": (0, 0, 0, 0, 0, 0, 0, 0, 17, 11, 11, 14, 36),
        "heading": (0, 22, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 3),
        "place_kicking": (0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
        "curl": (0, 0, 0, 0, 12, 0, 4, 4, 0, 0, 0, 0, 0),
        "speed": (0, 10, 16, 16, 3, 4, 24, 24, 5, 15, 15, 9, 5),
        "acceleration": (0, 0, 14, 14, 3, 6, 21, 21, 7, 15, 15, 21, 5),
        "kicking_power": (0, 0, 0, 0, 0, 0, 0, 0, 0, 5, 5, 6, 3),
        "jump": (11, 19, 11, 11, 5, 0, 0, 0, 0, 0, 0, 0, 3),
        "physical_contact": (11, 19, 8, 8, 9, 3, 0, 0, 2, 2, 2, 2, 8),
        "balance": (0, 0, 4, 4, 4, 2, 0, 0, 3, 4, 4, 5, 2),
        "stamina": (0, 9, 14, 14, 14, 17, 13, 13, 3, 6, 6, 4, 0),
        "defensive_awareness": (0, 25, 14, 14, 7, 3, 0, 0, 0, 0, 0, 0, 0),
        "ball_winning": (0, 17, 8, 8, 3, 0, 0, 0, 0, 0, 0, 0, 0),
        "aggression": (0, 8, 5, 5, 1, 0, 0, 0, 0, 0, 0, 0, 0),
        "gk_awareness": (50, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
        "reflexes": (48, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
        "weak_foot_accuracy": (0, 23, 56, 56, 23, 23, 56, 56, 37, 47, 47, 37, 38),
    }
)

_OUTFIELD_FIELDS = ABILITY_FIELDS[:20]
_POSITION_INDEX = MappingProxyType(
    {position: index for index, position in enumerate(POSITION_NAMES)}
)
_ABILITY_FIELD_SET = frozenset(ABILITY_FIELDS)
_POSITION_SET = frozenset(POSITION_NAMES)
_REGISTERED_BONUS = MappingProxyType(
    dict(
        zip(
            POSITION_NAMES,
            (8, 8, 9, 9, 9, 8, 8, 8, 8, 10, 10, 9, 8),
            strict=True,
        )
    )
)
_OFF_POSITION_BONUS = MappingProxyType(
    dict(
        zip(
            POSITION_NAMES,
            (
                (5, 3, 0),
                (5, 3, 0),
                (4, 2, 0),
                (4, 2, 0),
                (4, 2, 0),
                (5, 3, 0),
                (5, 3, 0),
                (5, 3, 0),
                (5, 3, 0),
                (4, 2, 0),
                (4, 2, 0),
                (5, 3, 0),
                (5, 3, 0),
            ),
            strict=True,
        )
    )
)
_POSITION_RATING_INDEX = MappingProxyType({"A": 0, "B": 1, "C": 2})


def _normalize_position(value: object) -> str:
    return value.upper() if isinstance(value, str) else ""


def _normalize_position_rating(value: object) -> str | None:
    if value is None:
        return None
    if type(value) is int:
        if value in (0, 1, 2):
            return ("C", "B", "A")[value]
    elif isinstance(value, str):
        normalized = value.upper()
        if normalized in _POSITION_RATING_INDEX:
            return normalized
    raise PlayerOvrError("OVR position rating must be A, B, C, or an integer from 0 to 2")


def _validate_abilities(abilities: Mapping[str, int]) -> None:
    if not isinstance(abilities, Mapping) or set(abilities) != _ABILITY_FIELD_SET:
        raise PlayerOvrError("OVR abilities must exactly match ABILITY_FIELDS")
    for field in ABILITY_FIELDS:
        value = abilities[field]
        if type(value) is not int or not 40 <= value <= 99:
            raise PlayerOvrError(
                f"OVR ability {field} must be an integer from 40 to 99"
            )


def _validate_weak_foot_accuracy(value: object) -> int:
    if type(value) is not int or not 1 <= value <= 4:
        raise PlayerOvrError(
            "OVR weak_foot_accuracy must be an integer from 1 to 4"
        )
    return value

def ovr_weak_foot_accuracy(codec_value: object) -> int:
    """Convert the codec's 0–3 weak-foot field to the formula's 1–4 scale."""
    if type(codec_value) is not int or not 0 <= codec_value <= 3:
        raise PlayerOvrError(
            "OVR codec weak_foot_accuracy must be an integer from 0 to 3"
        )
    return codec_value + 1


def _gk_composites(abilities: Mapping[str, int]) -> tuple[int, int]:
    gk_awareness = (abilities["gk_awareness"] + abilities["gk_reach"]) // 2
    gk_reflexes = (
        abilities["catching"] + abilities["clearing"] + abilities["reflexes"]
    ) // 3
    return gk_awareness, gk_reflexes


def _weighted_average(
    abilities: Mapping[str, int],
    position_index: int,
    weak_foot_accuracy: int,
    gk_composites: tuple[int, int],
) -> int:
    weighted_sum = sum(
        (abilities[field] - 25) * _WEIGHTS[field][position_index]
        for field in _OUTFIELD_FIELDS
    )
    if position_index == _POSITION_INDEX["GK"]:
        weighted_sum += (gk_composites[0] - 25) * _WEIGHTS["gk_awareness"][position_index]
        weighted_sum += (gk_composites[1] - 25) * _WEIGHTS["reflexes"][position_index]
    weighted_sum += (
        (weak_foot_accuracy - 1)
        * _WEIGHTS["weak_foot_accuracy"][position_index]
    )
    return (weighted_sum + 50) // 100


def _sum_average(
    abilities: Mapping[str, int], gk_composites: tuple[int, int]
) -> int:
    total = sum(abilities[field] for field in _OUTFIELD_FIELDS)
    total += sum(gk_composites)
    return (total * 100 // 22 + 50) // 100


def _curve_rating(sum_average: int) -> int:
    if sum_average <= 61:
        return sum_average - 9
    if sum_average == 62:
        return sum_average - 6
    if sum_average == 63:
        return sum_average - 4
    if sum_average == 64:
        return sum_average - 2
    if sum_average == 65:
        return sum_average
    if sum_average == 66:
        return sum_average + 1
    if sum_average == 67:
        return sum_average + 3
    if sum_average == 68:
        return sum_average + 5
    if sum_average == 69:
        return sum_average + 6
    if sum_average == 70:
        return sum_average + 7
    if sum_average == 71:
        return sum_average + 8
    if sum_average <= 73:
        return sum_average + 10
    if sum_average <= 75:
        return sum_average + 12
    if sum_average <= 77:
        return sum_average + 13
    return sum_average + 14


def calculate_ovr_tenths(
    abilities: Mapping[str, int],
    position: str,
    *,
    registered_position: str | None = None,
    position_rating: str | int | None = None,
    weak_foot_accuracy: int = 1,
) -> int:
    """Return the verified PES 2021 OVR for a query position in tenths.

    ``registered_position`` defaults to ``position`` for compatibility with
    callers that calculate a registered-position rating in isolation.  When
    querying another position, ``position_rating`` must identify the source
    proficiency as ``A``, ``B``, or ``C`` (or codec grade ``2``, ``1``, or
    ``0``).  Weak-foot accuracy uses the PES 2021 codec scale of 1–4.
    """
    _validate_abilities(abilities)
    weak_foot_accuracy = _validate_weak_foot_accuracy(weak_foot_accuracy)

    normalized_position = _normalize_position(position)
    if normalized_position not in _POSITION_SET:
        raise PlayerOvrError(f"unsupported OVR position: {position!r}")
    normalized_registered = (
        normalized_position
        if registered_position is None
        else _normalize_position(registered_position)
    )
    if normalized_registered not in _POSITION_SET:
        raise PlayerOvrError(
            f"unsupported OVR registered position: {registered_position!r}"
        )
    normalized_rating = _normalize_position_rating(position_rating)

    gk_composites = _gk_composites(abilities)
    position_index = _POSITION_INDEX[normalized_position]
    weighted_average = _weighted_average(
        abilities, position_index, weak_foot_accuracy, gk_composites
    )

    if normalized_position == normalized_registered:
        base = weighted_average + _REGISTERED_BONUS[normalized_position]
        if normalized_position == "GK":
            final_rating = base
        else:
            curved = _curve_rating(_sum_average(abilities, gk_composites))
            final_rating = (base * 60 + curved * 40 + 50) // 100
    else:
        if normalized_rating is None:
            raise PlayerOvrError(
                "OVR off-position calculations require position_rating"
            )
        final_rating = weighted_average + _OFF_POSITION_BONUS[normalized_position][
            _POSITION_RATING_INDEX[normalized_rating]
        ]

    return max(40, final_rating) * 10


def position_rating_for(
    position: str,
    registered_position: str,
    position_proficiency: Mapping[str, int],
) -> str | None:
    """Return the A/B/C rating used when ``position`` is off-position."""
    normalized_position = _normalize_position(position)
    normalized_registered = _normalize_position(registered_position)
    if normalized_position not in _POSITION_SET:
        raise PlayerOvrError(f"unsupported OVR position: {position!r}")
    if normalized_registered not in _POSITION_SET:
        raise PlayerOvrError(
            f"unsupported OVR registered position: {registered_position!r}"
        )
    if normalized_position == normalized_registered:
        return None
    if not isinstance(position_proficiency, Mapping):
        raise PlayerOvrError("OVR position proficiency must be a mapping")
    grade = position_proficiency.get(normalized_position, 0)
    if type(grade) is not int or not 0 <= grade <= 2:
        raise PlayerOvrError(
            f"OVR proficiency {position!r} must be an integer from 0 to 2"
        )
    return ("C", "B", "A")[grade]


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
