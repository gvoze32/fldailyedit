"""Native PES 2021 / Football Life player ability and overall-rating decoding.

The weights and rounding below were recovered from Ejogc327's PES 2020 Editor
V0.12.10 (``GClass48.smethod_0``), which uses the same routine for PES 2020 and
PES 2021. They reproduce pesdb.net PES 2021 overall ratings exactly.

Two record layouts carry the same abilities:

* EDIT ``data.dat`` player entries (PES 2021 / FL edit layout): 7-bit raw
  ability values.
* ``Player.bin`` records (PES 2021 / FL database layout): 6-bit values stored
  minus 40.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

POSITION_COUNT: Final = 13
ABILITY_COUNT: Final = 20
GOALKEEPING_COUNT: Final = 5
REGISTERED_GRADE: Final = 3
MIN_RATING: Final = 40

# Outfield ability order (index into PlayerAbilities.abilities).
ABILITY_NAMES: Final[tuple[str, ...]] = (
    "offensive_awareness",
    "ball_control",
    "dribbling",
    "tight_possession",
    "low_pass",
    "lofted_pass",
    "finishing",
    "heading",
    "place_kicking",
    "curl",
    "speed",
    "acceleration",
    "kicking_power",
    "jump",
    "physical_contact",
    "balance",
    "stamina",
    "defensive_awareness",
    "ball_winning",
    "aggression",
)
GOALKEEPING_NAMES: Final[tuple[str, ...]] = (
    "gk_awareness",
    "gk_catching",
    "gk_clearing",
    "gk_reflexes",
    "gk_reach",
)

# Per-position ability weights, positions in PES order GK..CF.
_ABILITY_WEIGHTS: Final[tuple[tuple[float, ...], ...]] = (
    (0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0.11, 0.11, 0, 0, 0, 0, 0),
    (0, 0, 0, 0, 0, 0, 0, 0.22, 0, 0, 0.1, 0, 0, 0.19, 0.19, 0, 0.09, 0.25, 0.17, 0.08),
    (0.06, 0.09, 0.09, 0.05, 0, 0.14, 0, 0, 0, 0, 0.16, 0.14, 0, 0.11, 0.08, 0.04, 0.14, 0.14, 0.08, 0.05),
    (0.06, 0.09, 0.09, 0.05, 0, 0.14, 0, 0, 0, 0, 0.16, 0.14, 0, 0.11, 0.08, 0.04, 0.14, 0.14, 0.08, 0.05),
    (0.07, 0.18, 0.1, 0.05, 0.18, 0.19, 0, 0, 0, 0.12, 0.03, 0.03, 0, 0.05, 0.09, 0.04, 0.14, 0.07, 0.03, 0.01),
    (0.05, 0.23, 0.16, 0.08, 0.23, 0.21, 0, 0, 0, 0, 0.04, 0.06, 0, 0, 0.03, 0.02, 0.17, 0.03, 0, 0),
    (0.07, 0.15, 0.17, 0.08, 0.07, 0.12, 0, 0, 0, 0.04, 0.24, 0.21, 0, 0, 0, 0, 0.13, 0, 0, 0),
    (0.07, 0.15, 0.17, 0.08, 0.07, 0.12, 0, 0, 0, 0.04, 0.24, 0.21, 0, 0, 0, 0, 0.13, 0, 0, 0),
    (0.14, 0.23, 0.17, 0.07, 0.21, 0.14, 0.17, 0, 0, 0, 0.05, 0.07, 0, 0, 0.02, 0.03, 0.03, 0, 0, 0),
    (0.17, 0.19, 0.15, 0.07, 0.05, 0.09, 0.11, 0, 0, 0, 0.15, 0.15, 0.05, 0, 0.02, 0.04, 0.06, 0, 0, 0),
    (0.17, 0.19, 0.15, 0.07, 0.05, 0.09, 0.11, 0, 0, 0, 0.15, 0.15, 0.05, 0, 0.02, 0.04, 0.06, 0, 0, 0),
    (0.15, 0.19, 0.13, 0.06, 0.09, 0.09, 0.14, 0, 0, 0, 0.09, 0.21, 0.06, 0, 0.02, 0.05, 0.04, 0, 0, 0),
    (0.31, 0.23, 0.09, 0.05, 0, 0, 0.36, 0.03, 0, 0, 0.05, 0.05, 0.03, 0.03, 0.08, 0.02, 0, 0, 0, 0),
)
_WEAK_FOOT_ACCURACY_WEIGHTS: Final[tuple[float, ...]] = (
    0, 0.23, 0.56, 0.56, 0.23, 0.23, 0.56, 0.56, 0.37, 0.47, 0.47, 0.37, 0.38,
)
# Bonus by playable-position grade C/B/A, then the registered-position bonus.
_POSITION_BONUS: Final[tuple[tuple[int, int, int, int], ...]] = (
    (0, 3, 5, 8),
    (0, 2, 4, 8),
    (0, 2, 4, 9),
    (0, 2, 4, 9),
    (0, 3, 5, 9),
    (0, 3, 5, 8),
    (0, 3, 5, 8),
    (0, 3, 5, 8),
    (0, 3, 5, 8),
    (0, 2, 4, 10),
    (0, 2, 4, 10),
    (0, 3, 5, 9),
    (0, 3, 5, 8),
)
# Registered outfield ratings blend 40% of this curve, indexed by the
# rounded 22-stat average minus 40.
_AVERAGE_CURVE: Final[tuple[int, ...]] = (
    31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50,
    51, 52, 56, 59, 62, 65, 67, 70, 73, 75, 77, 79, 82, 83, 85, 87, 89, 90, 92, 93,
    94, 95, 96, 97, 98, 99, 100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111, 112, 113,
)


@dataclass(frozen=True, slots=True)
class PlayerAbilities:
    """Decoded rating inputs for one player.

    ``position_grades`` holds the playable-position grade per position
    (0 = C, 1 = B, 2 = A). ``weak_foot_accuracy`` is the in-game value 1-4.
    """

    registered_position: int
    position_grades: tuple[int, ...]
    abilities: tuple[int, ...]
    goalkeeping: tuple[int, ...]
    weak_foot_accuracy: int

    def __post_init__(self) -> None:
        if not 0 <= self.registered_position < POSITION_COUNT:
            raise ValueError(f"registered position out of range: {self.registered_position}")
        if len(self.position_grades) != POSITION_COUNT:
            raise ValueError("position_grades must have 13 entries")
        if len(self.abilities) != ABILITY_COUNT:
            raise ValueError("abilities must have 20 entries")
        if len(self.goalkeeping) != GOALKEEPING_COUNT:
            raise ValueError("goalkeeping must have 5 entries")

    @property
    def overall(self) -> int:
        """In-game overall rating at the registered position."""
        return position_rating(self, self.registered_position)


def position_rating(player: PlayerAbilities, position: int) -> int:
    """Return the PES 2021 rating of ``player`` at ``position`` (0=GK .. 12=CF)."""
    if not 0 <= position < POSITION_COUNT:
        raise ValueError(f"position out of range: {position}")
    gk = player.goalkeeping
    reach_awareness = (gk[0] + gk[4]) // 2
    handling = (gk[1] + gk[2] + gk[3]) // 3

    weighted = 0.0
    if position == 0:
        weighted += (reach_awareness - 25) * 0.5 + (handling - 25) * 0.48
    weights = _ABILITY_WEIGHTS[position]
    for value, weight in zip(player.abilities, weights):
        weighted += (value - 25) * weight
    weighted += (player.weak_foot_accuracy - 1) * _WEAK_FOOT_ACCURACY_WEIGHTS[position]
    weighted += 0.5

    bonus = _POSITION_BONUS[position]
    if position != player.registered_position:
        grade = player.position_grades[position]
        return max(int(weighted) + bonus[grade], MIN_RATING)

    rating = int(weighted) + bonus[REGISTERED_GRADE]
    if position != 0:
        total = reach_awareness + handling + sum(player.abilities)
        average = max(int(total / 22.0 + 0.5), MIN_RATING)
        rating = int(rating * 0.6 + _AVERAGE_CURVE[average - MIN_RATING] * 0.4 + 0.5)
    return max(rating, MIN_RATING)


# Bitfield descriptors: (byte offset, bit shift, bit count, value added).
_Field = tuple[int, int, int, int]


@dataclass(frozen=True, slots=True)
class _Layout:
    registered_position: _Field
    position_grades: tuple[_Field, ...]
    abilities: tuple[_Field, ...]
    goalkeeping: tuple[_Field, ...]
    weak_foot_accuracy: _Field


_EDIT_LAYOUT: Final = _Layout(
    registered_position=(0x21, 5, 4, 0),
    position_grades=(
        (0x29, 5, 2, 0), (0x29, 7, 2, 0), (0x2A, 1, 2, 0), (0x2A, 3, 2, 0),
        (0x2A, 5, 2, 0), (0x2A, 7, 2, 0), (0x2B, 1, 2, 0), (0x2B, 3, 2, 0),
        (0x2B, 5, 2, 0), (0x2E, 4, 2, 0), (0x2C, 0, 2, 0), (0x2C, 2, 2, 0),
        (0x2C, 4, 2, 0),
    ),
    abilities=(
        (0x0E, 0, 7, 0), (0x0E, 7, 7, 0), (0x28, 0, 7, 0), (0x10, 0, 7, 0),
        (0x10, 7, 7, 0), (0x11, 6, 7, 0), (0x12, 5, 7, 0), (0x25, 6, 7, 0),
        (0x14, 0, 7, 0), (0x14, 7, 7, 0), (0x15, 6, 7, 0), (0x16, 5, 7, 0),
        (0x2D, 5, 7, 0), (0x18, 0, 7, 0), (0x18, 7, 7, 0), (0x19, 6, 7, 0),
        (0x1A, 5, 7, 0), (0x24, 0, 7, 0), (0x1C, 0, 7, 0), (0x1C, 7, 7, 0),
    ),
    goalkeeping=(
        (0x1D, 6, 7, 0), (0x1E, 5, 7, 0), (0x24, 7, 7, 0), (0x2C, 6, 7, 0),
        (0x20, 0, 7, 0),
    ),
    weak_foot_accuracy=(0x27, 6, 2, 1),
)

_PLAYERBIN_LAYOUT: Final = _Layout(
    registered_position=(0x36, 2, 4, 0),
    position_grades=(
        (0x2B, 6, 2, 0), (0x3A, 4, 2, 0), (0x27, 6, 2, 0), (0x3B, 2, 2, 0),
        (0x33, 6, 2, 0), (0x39, 0, 2, 0), (0x3A, 2, 2, 0), (0x39, 4, 2, 0),
        (0x3A, 0, 2, 0), (0x3B, 0, 2, 0), (0x3B, 4, 2, 0), (0x3B, 6, 2, 0),
        (0x3A, 6, 2, 0),
    ),
    abilities=(
        (0x2E, 2, 6, 40), (0x23, 1, 6, 40), (0x2C, 0, 6, 40), (0x34, 0, 6, 40),
        (0x20, 7, 6, 40), (0x32, 2, 6, 40), (0x31, 4, 6, 40), (0x24, 0, 6, 40),
        (0x1F, 2, 6, 40), (0x29, 4, 6, 40), (0x26, 2, 6, 40), (0x2B, 0, 6, 40),
        (0x2C, 6, 6, 40), (0x24, 6, 6, 40), (0x30, 6, 6, 40), (0x2F, 0, 6, 40),
        (0x2A, 2, 6, 40), (0x22, 3, 6, 40), (0x27, 0, 6, 40), (0x30, 0, 6, 40),
    ),
    goalkeeping=(
        (0x28, 6, 6, 40), (0x2D, 4, 6, 40), (0x21, 5, 6, 40), (0x28, 0, 6, 40),
        (0x25, 4, 6, 40),
    ),
    weak_foot_accuracy=(0x39, 6, 2, 1),
)


def _read_field(data: bytes | bytearray | memoryview, base: int, field: _Field) -> int:
    offset, shift, bits, add = field
    window = int.from_bytes(data[base + offset : base + offset + 2], "little")
    return ((window >> shift) & ((1 << bits) - 1)) + add


def _decode(data: bytes | bytearray | memoryview, base: int, layout: _Layout) -> PlayerAbilities:
    return PlayerAbilities(
        registered_position=_read_field(data, base, layout.registered_position),
        position_grades=tuple(_read_field(data, base, f) for f in layout.position_grades),
        abilities=tuple(_read_field(data, base, f) for f in layout.abilities),
        goalkeeping=tuple(_read_field(data, base, f) for f in layout.goalkeeping),
        weak_foot_accuracy=_read_field(data, base, layout.weak_foot_accuracy),
    )


def decode_edit_player(data: bytes | bytearray | memoryview, entry_offset: int) -> PlayerAbilities:
    """Decode a PES 2021 / FL EDIT ``data.dat`` player entry at ``entry_offset``."""
    return _decode(data, entry_offset, _EDIT_LAYOUT)


def decode_playerbin_record(data: bytes | bytearray | memoryview, record_offset: int) -> PlayerAbilities:
    """Decode a PES 2021 / FL ``Player.bin`` record at ``record_offset``."""
    return _decode(data, record_offset, _PLAYERBIN_LAYOUT)


__all__ = (
    "ABILITY_NAMES",
    "GOALKEEPING_NAMES",
    "PlayerAbilities",
    "decode_edit_player",
    "decode_playerbin_record",
    "position_rating",
)
