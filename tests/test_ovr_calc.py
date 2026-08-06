import json

import pytest

from editor.player_codec import ABILITY_FIELDS, POSITION_NAMES
from tools import ovr_calc


def test_print_ovr_table_renders_integer_tenths(capsys):
    abilities = {field: 60 for field in ABILITY_FIELDS}

    ovr_calc._print_ovr_table(abilities)

    assert capsys.readouterr().out.count("60.0") == len(POSITION_NAMES)


def test_cli_adapter_does_not_own_the_ovr_formula():
    assert not hasattr(ovr_calc, "calc_ovr")


def test_partial_spec_ability_map_fails_closed(
    monkeypatch, tmp_path, capsys
):
    spec_path = tmp_path / "partial-player.json"
    spec_path.write_text(
        '{"pes": {"abilities": {"speed": {"to": 60}}}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        ovr_calc,
        "_load_base_abilities",
        lambda player_id: ({}, "CF"),
    )
    monkeypatch.setattr(
        ovr_calc.sys,
        "argv",
        ["ovr_calc.py", "1", "--spec", str(spec_path)],
    )

    ovr_calc.main()

    output = capsys.readouterr().out
    assert "OVR abilities must exactly match ABILITY_FIELDS" in output
    assert len(output.splitlines()) == 1
    assert "40.0" not in output


@pytest.mark.parametrize("target", [60.9, "60"])
def test_spec_target_must_be_a_strict_integer(
    target, monkeypatch, tmp_path, capsys
):
    spec_path = tmp_path / "non-integer-player.json"
    spec_path.write_text(
        json.dumps({"pes": {"abilities": {"speed": {"to": target}}}}),
        encoding="utf-8",
    )
    base_abilities = {field: 60 for field in ABILITY_FIELDS}
    monkeypatch.setattr(
        ovr_calc,
        "_load_base_abilities",
        lambda player_id: (base_abilities, "CF"),
    )
    monkeypatch.setattr(
        ovr_calc.sys,
        "argv",
        ["ovr_calc.py", "1", "--spec", str(spec_path)],
    )

    ovr_calc.main()

    output = capsys.readouterr().out
    assert "OVR ability speed must be an integer from 40 to 99" in output
    assert len(output.splitlines()) == 1
    assert "OVR estimasi semua posisi" not in output
