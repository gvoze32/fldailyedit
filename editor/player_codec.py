"""Bit-preserving codec for PES 2021 / Football Life player entries."""
from dataclasses import dataclass
import struct
from typing import Mapping, Protocol

PLAYER_DATA_SIZE = 0xF0
PLAYER_APPEARANCE_SIZE = 0x48
PLAYER_NAME_OFFSET = 0x36
PLAYER_PRINT_NAME_OFFSET = 0x73
PLAYER_NATIONAL_PRINT_NAME_OFFSET = 0xB0


class CreatedPlayerRecord(Protocol):
    player_id: int
    name: str
    print_name: str
    nationality_id: int
    age: int
    height: int
    weight: int
    registered_position: str
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
    skin_color: int
    iris_color: int

ABILITY_FIELDS = (
    "attacking_awareness",
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
    "gk_awareness",
    "catching",
    "clearing",
    "reflexes",
    "gk_reach",
)

POSITION_NAMES = (
    "GK",
    "CB",
    "LB",
    "RB",
    "DMF",
    "CMF",
    "LMF",
    "RMF",
    "AMF",
    "LWF",
    "RWF",
    "SS",
    "CF",
)

COM_STYLE_FIELDS = (
    "trickster",
    "mazing_run",
    "speeding_bullet",
    "incisive_run",
    "long_ball_expert",
    "early_cross",
    "long_ranger",
)

PLAYER_SKILL_FIELDS = (
    "scissors_feint",
    "flip_flap",
    "marseille_turn",
    "sombrero",
    "cut_behind_and_turn",
    "scotch_move",
    "heading_skill",
    "long_range_drive",
    "knuckle_shot",
    "acrobatic_finishing",
    "heel_trick",
    "first_time_shot",
    "one_touch_pass",
    "weighted_pass",
    "pinpoint_crossing",
    "outside_curler",
    "rabona",
    "low_lofted_pass",
    "low_punt_trajectory",
    "long_throw",
    "gk_long_throw",
    "gamesmanship",
    "man_marking",
    "track_back",
    "acrobatic_clear",
    "captaincy",
    "super_sub",
    "fighting_spirit",
    "double_touch",
    "crossover_turn",
    "step_on_skill_control",
    "chip_shot_control",
    "dipping_shot",
    "rising_shot",
    "no_look_pass",
    "gk_high_punt",
    "penalty_specialist",
    "gk_penalty_saver",
    "interception",
    "long_range_shooting",
    "through_passing",
)

_PLAYER_SKILL_LAYOUT = (
    ("scissors_feint", 6),
    ("double_touch", 7),
    ("flip_flap", 0),
    ("marseille_turn", 1),
    ("sombrero", 2),
    ("crossover_turn", 3),
    ("cut_behind_and_turn", 4),
    ("scotch_move", 5),
    ("step_on_skill_control", 6),
    ("heading_skill", 7),
    ("long_range_drive", 0),
    ("chip_shot_control", 1),
    ("long_range_shooting", 2),
    ("knuckle_shot", 3),
    ("dipping_shot", 4),
    ("rising_shot", 5),
    ("acrobatic_finishing", 6),
    ("heel_trick", 7),
    ("first_time_shot", 0),
    ("one_touch_pass", 1),
    ("through_passing", 2),
    ("weighted_pass", 3),
    ("pinpoint_crossing", 4),
    ("outside_curler", 5),
    ("rabona", 6),
    ("no_look_pass", 7),
    ("low_lofted_pass", 0),
    ("low_punt_trajectory", 1),
    ("gk_high_punt", 2),
    ("long_throw", 3),
    ("gk_long_throw", 4),
    ("penalty_specialist", 5),
    ("gk_penalty_saver", 6),
    ("gamesmanship", 7),
    ("man_marking", 0),
    ("track_back", 1),
    ("interception", 2),
    ("acrobatic_clear", 3),
    ("captaincy", 4),
    ("super_sub", 5),
    ("fighting_spirit", 6),
)

_POSITION_FIELDS = (
    ("GK", "position_gk"),
    ("CB", "position_cb"),
    ("LB", "position_lb"),
    ("RB", "position_rb"),
    ("DMF", "position_dmf"),
    ("CMF", "position_cmf"),
    ("LMF", "position_lmf"),
    ("RMF", "position_rmf"),
    ("AMF", "position_amf"),
    ("RWF", "position_rwf"),
    ("SS", "position_ss"),
    ("CF", "position_cf"),
    ("LWF", "position_lwf"),
)


@dataclass(frozen=True, slots=True)
class FieldSpec:
    byte_offset: int
    bit_offset: int
    width: int


@dataclass(frozen=True, slots=True)
class PlayerAbilityProfile:
    player_id: int
    nationality_id: int
    height: int
    weight: int
    age: int
    registered_position: str
    registered_position_id: int
    playing_style: int
    strong_foot: int
    weak_foot_usage: int
    weak_foot_accuracy: int
    form: int
    injury_resistance: int
    abilities: Mapping[str, int]
    position_proficiency: Mapping[str, int]
    player_skills: tuple[str, ...]
    com_styles: tuple[str, ...]


def _build_field_specs() -> dict[str, FieldSpec]:
    specs: dict[str, FieldSpec] = {}
    cursor = 0

    def add(name: str, bit_offset: int, width: int) -> None:
        nonlocal cursor
        specs[name] = FieldSpec(cursor, bit_offset, width)
        cursor += (bit_offset + width) // 8

    add("player_id", 0, 32)
    cursor += 4  # The entry repeats the player ID in this reserved word.
    add("nationality_id", 0, 16)
    add("height", 0, 8)
    add("weight", 0, 8)
    add("goal_celebration_1", 0, 8)
    add("goal_celebration_2", 0, 8)

    for field in (
        ("attacking_awareness", 0, 7),
        ("ball_control", 7, 7),
        ("weak_foot_usage", 6, 2),
        ("tight_possession", 0, 7),
        ("low_pass", 7, 7),
        ("lofted_pass", 6, 7),
        ("finishing", 5, 7),
        ("motion_arm_down", 4, 4),
        ("place_kicking", 0, 7),
        ("curl", 7, 7),
        ("speed", 6, 7),
        ("acceleration", 5, 7),
        ("motion_arm_running", 4, 4),
        ("jump", 0, 7),
        ("physical_contact", 7, 7),
        ("balance", 6, 7),
        ("stamina", 5, 7),
        ("motion_corner_kick", 4, 4),
        ("ball_winning", 0, 7),
        ("aggression", 7, 7),
        ("gk_awareness", 6, 7),
        ("catching", 5, 7),
        ("form", 4, 3),
        ("edited_player", 7, 1),
        ("gk_reach", 0, 7),
        ("age", 7, 6),
        ("registered_position", 5, 4),
        ("playing_style", 2, 5),
        ("motion_free_kick", 7, 5),
        ("star_player", 4, 3),
        ("edited_basic_settings", 7, 1),
        ("defensive_awareness", 0, 7),
        ("clearing", 7, 7),
        ("heading", 6, 7),
        ("motion_hunch_down", 5, 3),
        ("motion_hunch_running", 0, 3),
        ("motion_penalty_kick", 3, 3),
        ("weak_foot_accuracy", 6, 2),
        ("dribbling", 0, 7),
        ("injury_resistance", 7, 2),
        ("playing_attitude", 1, 2),
        ("motion_dribbling", 3, 2),
        ("position_gk", 5, 2),
        ("position_cb", 7, 2),
        ("position_lb", 1, 2),
        ("position_rb", 3, 2),
        ("position_dmf", 5, 2),
        ("position_cmf", 7, 2),
        ("position_lmf", 1, 2),
        ("position_rmf", 3, 2),
        ("position_amf", 5, 2),
        ("edited_registered_position", 7, 1),
        ("position_rwf", 0, 2),
        ("position_ss", 2, 2),
        ("position_cf", 4, 2),
        ("reflexes", 6, 7),
        ("kicking_power", 5, 7),
        ("position_lwf", 4, 2),
        ("edited_playable_positions", 6, 1),
        ("edited_abilities", 7, 1),
    ):
        add(*field)

    add("edited_skills", 0, 1)
    add("edited_playing_style", 1, 1)
    add("edited_com_styles", 2, 1)
    add("edited_motion", 3, 1)
    add("strong_foot", 5, 1)
    add("strong_hand", 6, 1)
    for index, name in enumerate(COM_STYLE_FIELDS):
        add(f"com_style_{name}", 7 if index == 0 else index - 1, 1)
    for name, bit_offset in _PLAYER_SKILL_LAYOUT:
        add(f"skill_{name}", bit_offset, 1)
    return specs


FIELD_SPECS = _build_field_specs()


def _read_field(entry: bytes | bytearray, spec: FieldSpec) -> int:
    byte_count = (spec.bit_offset + spec.width + 7) // 8
    chunk = int.from_bytes(
        entry[spec.byte_offset : spec.byte_offset + byte_count], "little"
    )
    return (chunk >> spec.bit_offset) & ((1 << spec.width) - 1)


def decode_player_entry(entry: bytes | bytearray) -> PlayerAbilityProfile:
    """Decode known player fields without interpreting unknown or appearance data."""
    if len(entry) < PLAYER_DATA_SIZE:
        raise ValueError(
            f"Player entry must contain at least {PLAYER_DATA_SIZE} bytes; got {len(entry)}"
        )

    values = {name: _read_field(entry, spec) for name, spec in FIELD_SPECS.items()}
    position_id = values["registered_position"]
    position = (
        POSITION_NAMES[position_id]
        if position_id < len(POSITION_NAMES)
        else f"UNKNOWN({position_id})"
    )

    return PlayerAbilityProfile(
        player_id=values["player_id"],
        nationality_id=values["nationality_id"],
        height=values["height"],
        weight=values["weight"],
        age=values["age"],
        registered_position=position,
        registered_position_id=position_id,
        playing_style=values["playing_style"],
        strong_foot=values["strong_foot"],
        weak_foot_usage=values["weak_foot_usage"],
        weak_foot_accuracy=values["weak_foot_accuracy"],
        form=values["form"],
        injury_resistance=values["injury_resistance"],
        abilities={name: values[name] for name in ABILITY_FIELDS},
        position_proficiency={
            label: values[field_name] for label, field_name in _POSITION_FIELDS
        },
        player_skills=tuple(
            name for name in PLAYER_SKILL_FIELDS if values[f"skill_{name}"]
        ),
        com_styles=tuple(
            name for name in COM_STYLE_FIELDS if values[f"com_style_{name}"]
        ),
    )


def _write_field(entry: bytearray, spec: FieldSpec, value: int) -> None:
    maximum = (1 << spec.width) - 1
    if not 0 <= value <= maximum:
        raise ValueError(
            f"Value {value} does not fit in the {spec.width}-bit field "
            f"(expected 0..{maximum})"
        )

    byte_count = (spec.bit_offset + spec.width + 7) // 8
    start = spec.byte_offset
    chunk = int.from_bytes(entry[start : start + byte_count], "little")
    value_mask = maximum << spec.bit_offset
    chunk = (chunk & ~value_mask) | (value << spec.bit_offset)
    entry[start : start + byte_count] = chunk.to_bytes(byte_count, "little")


def patch_player_entry(
    entry: bytes | bytearray,
    updates: Mapping[str, int],
) -> bytes:
    """Return an entry with selected known bitfields changed.

    Unknown bits and the appearance record remain byte-for-byte identical.
    Ability values use PES' displayed 40..99 range.
    """
    if len(entry) < PLAYER_DATA_SIZE:
        raise ValueError(
            f"Player entry must contain at least {PLAYER_DATA_SIZE} bytes; got {len(entry)}"
        )

    patched = bytearray(entry)
    ability_changed = False
    for field_name, value in updates.items():
        spec = FIELD_SPECS.get(field_name)
        if spec is None:
            raise KeyError(f"Unknown PES player field: {field_name}")
        if field_name in ABILITY_FIELDS and not 40 <= value <= 99:
            raise ValueError(
                f"{field_name} must be in the PES ability range 40..99; got {value}"
            )
        _write_field(patched, spec, value)
        ability_changed = ability_changed or field_name in ABILITY_FIELDS

    if ability_changed:
        _write_field(patched, FIELD_SPECS["edited_abilities"], 1)
    return bytes(patched)


def _fixed_utf8(value: str, size: int) -> bytes:
    encoded = value.encode("utf-8")
    if len(encoded) >= size:
        raise ValueError(f"{value!r} exceeds the {size - 1}-byte PES string limit")
    return encoded + bytes(size - len(encoded))


def _build_generic_appearance(player: CreatedPlayerRecord) -> bytes:
    appearance = bytearray(PLAYER_APPEARANCE_SIZE)
    _write_field(appearance, FieldSpec(0, 0, 32), player.player_id)
    
    # All edited flags must be 0 to use default values from wiki.
    _write_field(appearance, FieldSpec(4, 0, 1), 0)  # Edited face settings.
    _write_field(appearance, FieldSpec(4, 1, 1), 0)  # Edited hairstyle settings.
    _write_field(appearance, FieldSpec(4, 2, 1), 0)  # Edited physique settings.
    _write_field(appearance, FieldSpec(4, 3, 1), 0)  # Edited strip style settings.

    # Populate the non-zero defaults from a newly created edited player.  The
    # fields are sparse bitfields, so preserve the documented unknown bits.
    for byte_offset, bit_offset, width, value in (
        (4, 4, 14, 23),  # Boots.
        (6, 2, 10, 10),  # Goalkeeper gloves.
        (8, 0, 32, player.player_id),  # Defaulted players reference themselves.
        (22, 4, 4, 7),
        (23, 0, 4, 7),
        (23, 4, 4, 7),
        (24, 0, 4, 7),
        (25, 0, 4, 7),
        (26, 4, 4, 7),
        (27, 0, 4, 7),
        (27, 4, 4, 7),
        (28, 0, 4, 7),
        (28, 4, 4, 7),
        (29, 4, 4, 7),
        (30, 0, 4, 7),
        (30, 4, 4, 7),
        (31, 0, 4, 7),
        (31, 4, 4, 7),
        (32, 0, 4, 7),
        (32, 4, 4, 7),
        (33, 0, 4, 7),
        (33, 4, 4, 7),
        (34, 0, 4, 7),
        (35, 0, 4, 7),
        (35, 4, 4, 7),
        (36, 0, 4, 7),
        (36, 4, 4, 7),
        (37, 0, 4, 7),
        (37, 4, 4, 7),
        (38, 4, 4, 7),
        (39, 0, 4, 7),
        (40, 0, 4, 7),
        (40, 4, 4, 7),
        (41, 0, 4, 7),
        (42, 0, 4, 7),
        (42, 4, 4, 7),
        (43, 0, 4, 7),
        (43, 4, 4, 7),
        (44, 0, 4, 7),
        (44, 4, 4, 7),
        (48, 4, 4, 7),
        (49, 0, 4, 7),
        (49, 4, 4, 7),
        (51, 0, 4, 7),
        (51, 4, 4, 7),
        (56, 0, 3, 1),  # Hairstyle.
        (56, 4, 3, 1),  # Cropped hairstyle.
        (58, 6, 2, 3),  # Eyebrow density.
        (59, 6, 2, 1),  # Eyebrow thickness.
        (60, 0, 3, 2),  # Hair length.
        (60, 3, 3, 1),  # Wave level.
        (63, 6, 2, 3),  # Facial-hair thickness.
        (64, 0, 4, player.iris_color & 0x0F),
        (64, 4, 5, 1),  # Hair variation.
    ):
        _write_field(
            appearance,
            FieldSpec(byte_offset, bit_offset, width),
            value,
        )

    for offset in range(12, 19):
        appearance[offset] = 0x77  # Neutral value for both signed physique nibbles.
    appearance[45] = player.skin_color & 0xFF
    return bytes(appearance)


def serialize_created_player(
    player: CreatedPlayerRecord,
) -> tuple[bytes, bytes]:
    """Serialize one reviewed created-player record and its generic appearance."""
    entry = bytearray(PLAYER_DATA_SIZE)
    struct.pack_into("<I", entry, 0, player.player_id)
    struct.pack_into("<I", entry, 4, player.player_id)

    updates: dict[str, int] = {
        "nationality_id": player.nationality_id,
        "height": player.height,
        "weight": player.weight,
        "age": player.age,
        "registered_position": POSITION_NAMES.index(player.registered_position),
        "playing_style": player.playing_style,
        "star_player": 7,
        "strong_foot": player.strong_foot,
        "weak_foot_usage": player.weak_foot_usage,
        "weak_foot_accuracy": player.weak_foot_accuracy,
        "form": player.form,
        "injury_resistance": player.injury_resistance,
        "edited_player": 1,
        "edited_basic_settings": 1,
        "edited_registered_position": 1,
        "edited_playable_positions": 1,
        "edited_skills": 1,
        "edited_playing_style": 1,
        "edited_com_styles": 1,
    }
    updates.update(player.abilities)
    for label, field_name in _POSITION_FIELDS:
        updates[field_name] = player.position_proficiency.get(label, 0)
    for name in COM_STYLE_FIELDS:
        updates[f"com_style_{name}"] = int(name in player.com_styles)
    for name in PLAYER_SKILL_FIELDS:
        updates[f"skill_{name}"] = int(name in player.player_skills)

    entry[:] = patch_player_entry(entry, updates)
    entry[PLAYER_NAME_OFFSET:PLAYER_PRINT_NAME_OFFSET] = _fixed_utf8(
        player.name, PLAYER_PRINT_NAME_OFFSET - PLAYER_NAME_OFFSET
    )
    entry[PLAYER_PRINT_NAME_OFFSET:PLAYER_NATIONAL_PRINT_NAME_OFFSET] = _fixed_utf8(
        player.print_name,
        PLAYER_NATIONAL_PRINT_NAME_OFFSET - PLAYER_PRINT_NAME_OFFSET,
    )
    entry[PLAYER_NATIONAL_PRINT_NAME_OFFSET:PLAYER_DATA_SIZE] = _fixed_utf8(
        player.print_name,
        PLAYER_DATA_SIZE - PLAYER_NATIONAL_PRINT_NAME_OFFSET,
    )
    return bytes(entry), _build_generic_appearance(player)
