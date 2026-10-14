"""Native PES 2021 / Football Life overall-rating decoding."""

import struct

import pytest

from editor.player_rating import (
    PlayerAbilities,
    _EDIT_LAYOUT,
    _PLAYERBIN_LAYOUT,
    decode_edit_player,
    decode_playerbin_record,
    position_rating,
)
from editor.playerbin import RECORD_SIZE, PlayerBinDatabase

GK, CB, DMF, AMF, RWF, SS = 0, 1, 4, 8, 10, 11
NO_GK = (40, 40, 40, 40, 40)


def _player(position, abilities, *, goalkeeping=NO_GK, wfa=2, grades=None):
    return PlayerAbilities(
        registered_position=position,
        position_grades=tuple(grades or [0] * 13),
        abilities=tuple(abilities),
        goalkeeping=tuple(goalkeeping),
        weak_foot_accuracy=wfa,
    )


# Abilities and overalls from pesdb.net PES 2021 player pages.
SALAH = _player(
    RWF,
    [89, 90, 87, 90, 80, 78, 85, 61, 75, 84, 92, 88, 78, 66, 69, 90, 84, 50, 51, 62],
    grades=[0, 0, 0, 0, 0, 0, 0, 1, 1, 0, 2, 1, 1],
)


ALISSON = _player(
    GK,
    [40, 68, 65, 67, 69, 72, 43, 70, 65, 53, 65, 68, 84, 86, 87, 68, 65, 51, 50, 50],
    goalkeeping=(97, 90, 94, 98, 94),
    grades=[2] + [0] * 12,
)
VAN_DIJK = _player(
    CB,
    [62, 76, 75, 80, 80, 84, 59, 91, 76, 70, 79, 74, 85, 90, 95, 65, 84, 95, 93, 88],
    wfa=3,
    grades=[0, 2] + [0] * 11,
)
KIMMICH = _player(
    DMF,
    [74, 86, 80, 84, 88, 86, 73, 73, 79, 81, 80, 85, 78, 82, 70, 85, 90, 75, 80, 77],
    wfa=3,
    grades=[0, 1, 0, 2, 2, 2, 0, 1, 2, 1, 1, 0, 0],
)


@pytest.mark.parametrize(
    ("player", "expected"),
    [(SALAH, 89), (ALISSON, 90), (VAN_DIJK, 91), (KIMMICH, 89)],
)
def test_registered_overall_matches_pesdb(player, expected):
    assert player.overall == expected


def test_secondary_position_uses_grade_bonus_and_floor():
    grade_c_at_ss = PlayerAbilities(
        registered_position=SALAH.registered_position,
        position_grades=SALAH.position_grades[:SS] + (0,) + SALAH.position_grades[SS + 1 :],
        abilities=SALAH.abilities,
        goalkeeping=SALAH.goalkeeping,
        weak_foot_accuracy=SALAH.weak_foot_accuracy,
    )
    # SS bonus: C=0, B=3. Non-registered ratings skip the average blend.
    assert position_rating(SALAH, SS) - position_rating(grade_c_at_ss, SS) == 3
    assert position_rating(SALAH, GK) == 40


def _pack(buffer, base, field, value):
    offset, shift, bits, add = field
    window = int.from_bytes(buffer[base + offset : base + offset + 2], "little")
    mask = ((1 << bits) - 1) << shift
    window = (window & ~mask) | (((value - add) << shift) & mask)
    buffer[base + offset : base + offset + 2] = window.to_bytes(2, "little")


def _encode(layout, player, size, base=0):
    buffer = bytearray(size)
    _pack(buffer, base, layout.registered_position, player.registered_position)
    for field, value in zip(layout.position_grades, player.position_grades):
        _pack(buffer, base, field, value)
    for field, value in zip(layout.abilities, player.abilities):
        _pack(buffer, base, field, value)
    for field, value in zip(layout.goalkeeping, player.goalkeeping):
        _pack(buffer, base, field, value)
    _pack(buffer, base, layout.weak_foot_accuracy, player.weak_foot_accuracy)
    return buffer


@pytest.mark.parametrize(
    ("layout", "decode"),
    [(_EDIT_LAYOUT, decode_edit_player), (_PLAYERBIN_LAYOUT, decode_playerbin_record)],
)
def test_layout_fields_do_not_overlap(layout, decode):
    # Distinct values per field: any overlapping bitfield corrupts a neighbour.
    player = _player(
        AMF,
        [41 + i * 2 for i in range(20)],
        goalkeeping=(42, 47, 53, 59, 61),
        wfa=3,
        grades=[i % 3 for i in range(13)],
    )
    assert decode(_encode(layout, player, RECORD_SIZE + 8, base=8), 8) == player


def test_playerbin_database_exposes_native_overall():
    raw = _encode(_PLAYERBIN_LAYOUT, SALAH, RECORD_SIZE)
    struct.pack_into("<I", raw, 0x08, 57123)
    raw[0x44 : 0x44 + len(b"Mohamed Salah")] = b"Mohamed Salah"

    record = PlayerBinDatabase.from_bytes(raw).get(57123)

    assert record is not None
    assert record.registered_position == "RWF"
    assert record.overall == 89
