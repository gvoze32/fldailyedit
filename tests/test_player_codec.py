"""Player codec decoding, patching, and EditFile lookup coverage."""

PALESTRA_ENTRY = bytes.fromhex(
    "9479020094790200d700b444000041233f2512073c5f730948e1f0083b220a4528"
    "6a3c003dd44f424503121201ca8800100000100000"
) + bytes(186)


def test_decode_player_entry_reads_pes21_bitfields():
    from editor.player_codec import decode_player_entry

    profile = decode_player_entry(PALESTRA_ENTRY)

    assert profile.player_id == 162196
    assert (profile.height, profile.weight, profile.age) == (180, 68, 20)
    assert profile.registered_position == "RB"
    assert profile.abilities == {
        "attacking_awareness": 65,
        "ball_control": 70,
        "dribbling": 69,
        "tight_possession": 63,
        "low_pass": 74,
        "lofted_pass": 72,
        "finishing": 56,
        "heading": 63,
        "place_kicking": 60,
        "curl": 62,
        "speed": 77,
        "acceleration": 75,
        "kicking_power": 70,
        "jump": 72,
        "physical_contact": 66,
        "balance": 67,
        "stamina": 71,
        "defensive_awareness": 61,
        "ball_winning": 59,
        "aggression": 68,
        "gk_awareness": 40,
        "catching": 40,
        "clearing": 40,
        "reflexes": 40,
        "gk_reach": 40,
    }
    assert profile.position_proficiency == {
        "GK": 0,
        "CB": 0,
        "LB": 1,
        "RB": 2,
        "DMF": 0,
        "CMF": 0,
        "LMF": 1,
        "RMF": 2,
        "AMF": 0,
        "RWF": 1,
        "SS": 0,
        "CF": 0,
        "LWF": 0,
    }



def test_patch_player_entry_changes_only_requested_bitfields():
    from editor.player_codec import decode_player_entry, patch_player_entry

    updated = patch_player_entry(
        PALESTRA_ENTRY,
        {"speed": 80, "defensive_awareness": 64},
    )

    assert PALESTRA_ENTRY != updated
    assert len(updated) == len(PALESTRA_ENTRY)
    profile = decode_player_entry(updated)
    assert profile.abilities["speed"] == 80
    assert profile.abilities["defensive_awareness"] == 64
    assert profile.abilities["acceleration"] == 75

    restored = patch_player_entry(
        updated,
        {"speed": 77, "defensive_awareness": 61},
    )
    assert restored == PALESTRA_ENTRY


def test_editfile_finds_decoded_profile_without_mutating_data():
    from editor.editfile import EditFile, PLAYER_APPEARANCE_SIZE

    edit_file = EditFile()
    edit_file._data = bytearray(PALESTRA_ENTRY + bytes(PLAYER_APPEARANCE_SIZE))
    edit_file.player_start = 0
    edit_file.player_count = 1
    before = bytes(edit_file._data)

    profile = edit_file.get_player_ability_profile(162196)

    assert profile is not None
    assert profile.registered_position == "RB"
    assert edit_file.get_player_ability_profile(999999) is None
    assert bytes(edit_file._data) == before


def test_editfile_reads_and_replaces_exact_player_entry_without_appearance_bytes():
    from editor.editfile import (
        PLAYER_APPEARANCE_SIZE,
        PLAYER_ENTRY_SIZE,
        EditFile,
    )
    from editor.player_codec import patch_player_entry

    appearance = bytes(range(PLAYER_APPEARANCE_SIZE))
    edit_file = EditFile()
    edit_file._data = bytearray(PALESTRA_ENTRY + appearance)
    edit_file.player_start = 0
    edit_file.player_count = 1
    edit_file._player_cache = {162196: object()}

    entry = edit_file.get_edited_player_entry(162196)
    assert entry == PALESTRA_ENTRY
    assert len(entry) == PLAYER_ENTRY_SIZE

    replacement = patch_player_entry(entry, {"speed": 80})
    edit_file.replace_edited_player_entry(162196, replacement)

    assert edit_file.get_edited_player_entry(162196) == replacement
    assert bytes(edit_file._data[PLAYER_ENTRY_SIZE:]) == appearance
    assert edit_file._player_cache is None


def test_editfile_entry_accessors_reject_missing_records_and_wrong_sizes():
    import pytest

    from editor.editfile import EditFile

    edit_file = EditFile()
    edit_file._data = bytearray(PALESTRA_ENTRY)
    edit_file.player_start = 0
    edit_file.player_count = 1

    assert edit_file.get_edited_player_entry(999999) is None
    with pytest.raises(ValueError, match="player entry must be 240 bytes"):
        edit_file.replace_edited_player_entry(162196, b"short")
    with pytest.raises(ValueError, match="edited-player record 999999 was not found"):
        edit_file.replace_edited_player_entry(999999, PALESTRA_ENTRY)


def test_editfile_entry_accessors_reject_truncated_edited_records():
    import pytest

    from editor.editfile import EditFile

    truncated = PALESTRA_ENTRY[:16]
    edit_file = EditFile()
    edit_file._data = bytearray(truncated)
    edit_file.player_start = 0
    edit_file.player_count = 1
    before = bytes(edit_file._data)

    assert edit_file.get_edited_player_entry(162196) is None
    with pytest.raises(ValueError, match="edited-player record 162196 was not found"):
        edit_file.replace_edited_player_entry(162196, PALESTRA_ENTRY)
    assert bytes(edit_file._data) == before

