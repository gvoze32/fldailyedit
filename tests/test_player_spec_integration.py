import shutil

from editor import crypto
from editor.editfile import EditFile
from editor.player_spec import (
    apply_player_spec,
    apply_player_specs,
    load_base_manifest,
    load_player_specs,
)


def test_bundled_base_batch_survives_encryption_roundtrip(tmp_path):
    source = tmp_path / "EDIT00000000"
    shutil.copy2("base/EDIT00000000", source)
    decrypted = crypto.decrypt(source)
    reopened = None
    try:
        edit_file = EditFile()
        edit_file.load(decrypted / "data.dat")
        results = apply_player_specs(
            edit_file,
            load_player_specs(),
            load_base_manifest().revision,
            edit_file.get_all_players(),
        )
        assert {result.name: result.status for result in results} == {
            "Dastan Satpaev": "waiting",
            "Marco Palestra": "updated",
        }
        assert edit_file.validate_integrity()["valid"] is True
        edit_file.save(decrypted / "data.dat")

        output = tmp_path / "updated-EDIT00000000"
        crypto.encrypt(decrypted, output)
        reopened = crypto.decrypt(output)
        verified = EditFile()
        verified.load(reopened / "data.dat")
        assert verified.validate_integrity()["valid"] is True
        palestra = verified.get_player_ability_profile(162196)
        assert palestra.abilities["speed"] == 90
        assert palestra.abilities["acceleration"] == 85
        assert palestra.abilities["defensive_awareness"] == 65
        assert palestra.abilities["ball_winning"] == 69
        assert palestra.abilities["dribbling"] == 86
        assert palestra.abilities["stamina"] == 85
        assert palestra.abilities["ball_control"] == 80
        assert palestra.abilities["tight_possession"] == 81
        assert palestra.abilities["lofted_pass"] == 83
        assert verified.get_all_players().get(200000) is None
    finally:
        crypto.cleanup_temp(decrypted)
        if reopened is not None:
            crypto.cleanup_temp(reopened)


def test_bundled_base_create_survives_encryption_roundtrip(tmp_path):
    source = tmp_path / "EDIT00000000"
    shutil.copy2("base/EDIT00000000", source)
    decrypted = crypto.decrypt(source)
    reopened = None
    try:
        edit_file = EditFile()
        edit_file.load(decrypted / "data.dat")
        assert edit_file.release_player(126925, 102) is True
        dastan = next(
            spec for spec in load_player_specs() if spec.identity.pes_id == 200000
        )
        result = apply_player_spec(
            edit_file,
            dastan,
            load_base_manifest().revision,
            edit_file.get_all_players(),
        )
        assert result.status == "created"
        assert len(edit_file.get_team_roster(102).roster) == 40
        assert edit_file.validate_integrity()["valid"] is True
        edit_file.save(decrypted / "data.dat")

        output = tmp_path / "created-EDIT00000000"
        crypto.encrypt(decrypted, output)
        reopened = crypto.decrypt(output)
        verified = EditFile()
        verified.load(reopened / "data.dat")
        assert verified.validate_integrity()["valid"] is True
        players = verified.get_all_players()
        roster = verified.get_team_roster(102)
        assert roster.player_index(200000) != -1
        assert players[200000].name == "Dastan Satpaev"
        assert players[200000].print_name == "SATPAEV"
        assert [
            players[player_id].name
            for player_id in roster.roster
            if player_id == 200000
        ] == ["Dastan Satpaev"]
        before_rerun = bytes(verified._data)
        rerun = apply_player_spec(
            verified,
            dastan,
            load_base_manifest().revision,
            verified.get_all_players(),
        )
        assert (rerun.status, rerun.reason) == (
            "already_applied",
            "matching_player_exists",
        )
        assert bytes(verified._data) == before_rerun

    finally:
        crypto.cleanup_temp(decrypted)
        if reopened is not None:
            crypto.cleanup_temp(reopened)
