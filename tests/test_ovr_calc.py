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
