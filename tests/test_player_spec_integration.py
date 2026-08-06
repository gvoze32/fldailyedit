import json
import shutil

from dataclasses import replace

from editor import crypto
from editor.editfile import EditFile
from editor.player_spec import (
    apply_player_spec,
    apply_player_specs,
    load_base_manifest,
    load_player_specs,
)


def test_approved_update_proposal_applies_and_survives_encryption_roundtrip(
    tmp_path, monkeypatch
):
    import config
    from tests.test_generate_player_draft import (
        marco_source,
        proposal_for,
        update_request,
    )
    from tools.generate_player_draft import build_player_draft

    proposal_dir = tmp_path / "proposals"
    proposal_dir.mkdir()
    monkeypatch.setattr(config, "PLAYER_SPECS_DIR", proposal_dir)
    source = tmp_path / "EDIT00000000"
    shutil.copy2("base/EDIT00000000", source)
    decrypted = crypto.decrypt(source)
    reopened = None
    try:
        edit_file = EditFile()
        edit_file.load(decrypted / "data.dat")
        source_profile = marco_source()
        proposal = proposal_for(source_profile)
        request = replace(
            update_request(),
            issue_number=123,
            issue_url="https://github.com/gvoze32/fldailyedit/issues/123",
        )
        payload = build_player_draft(
            request,
            source_profile,
            proposal,
            edit_file=edit_file,
        )
        proposal_path = proposal_dir / "marco-palestra.json"
        proposal_path.write_text(json.dumps(payload), encoding="utf-8")
        unapproved_bytes = proposal_path.read_bytes()
        from editor.player_spec import approve_player_proposal

        assert approve_player_proposal(proposal_path, edit_file) == proposal_path
        completed = load_player_specs(proposal_dir)[0]
        result = apply_player_spec(
            edit_file,
            completed,
            load_base_manifest().revision,
            edit_file.get_all_players(),
        )
        assert (result.status, result.reason) == ("updated", "patched")
        assert edit_file.validate_integrity()["valid"] is True
        edit_file.save(decrypted / "data.dat")

        output = tmp_path / "updated-EDIT00000000"
        crypto.encrypt(decrypted, output)
        reopened = crypto.decrypt(output)
        verified = EditFile()
        verified.load(reopened / "data.dat")
        assert verified.validate_integrity()["valid"] is True
        assert verified.get_player_ability_profile(162196).abilities["speed"] == 80

        unapproved_dir = tmp_path / "unapproved"
        unapproved_dir.mkdir()
        unapproved_path = unapproved_dir / proposal_path.name
        unapproved_path.write_bytes(unapproved_bytes)
        unapproved = load_player_specs(
            unapproved_dir,
            allow_proposals=True,
        )[0]
        before_unapproved = bytes(verified._data)
        blocked = apply_player_spec(
            verified,
            unapproved,
            load_base_manifest().revision,
            verified.get_all_players(),
        )
        assert (blocked.status, blocked.reason) == (
            "rejected",
            "human_review_required",
        )
        assert bytes(verified._data) == before_unapproved
    finally:
        crypto.cleanup_temp(decrypted)
        if reopened is not None:
            crypto.cleanup_temp(reopened)


def test_approved_create_proposal_frees_slot_applies_and_survives_roundtrip(
    tmp_path, monkeypatch
):
    import config
    from tests.test_generate_player_draft import (
        build_create_kwargs,
        issue_event,
        make_source,
        proposal_for,
    )
    from tools.generate_player_draft import (
        build_player_draft,
        parse_player_issue_event,
    )

    proposal_dir = tmp_path / "proposals"
    proposal_dir.mkdir()
    monkeypatch.setattr(config, "PLAYER_SPECS_DIR", proposal_dir)
    source = tmp_path / "EDIT00000000"
    shutil.copy2("base/EDIT00000000", source)
    decrypted = crypto.decrypt(source)
    reopened = None
    try:
        edit_file = EditFile()
        edit_file.load(decrypted / "data.dat")
        all_team_info = edit_file.get_all_team_info()
        edit_file.get_all_team_info = lambda: {
            team_id: team
            for team_id, team in all_team_info.items()
            if any(character.isalnum() for character in team.name)
        }
        assert edit_file.release_player(126925, 102) is True
        source_profile = make_source()
        proposal = proposal_for(source_profile)
        request = replace(
            parse_player_issue_event(issue_event()),
            issue_number=123,
            issue_url="https://github.com/gvoze32/fldailyedit/issues/123",
        )
        kwargs = build_create_kwargs(proposal)
        kwargs["edit_file"] = edit_file
        kwargs["completed_player_ids"] = set()
        payload = build_player_draft(
            request,
            source_profile,
            proposal,
            **kwargs,
        )
        proposal_path = proposal_dir / "dastan-satpaev.json"
        proposal_path.write_text(json.dumps(payload), encoding="utf-8")
        unapproved_bytes = proposal_path.read_bytes()
        from editor.player_spec import approve_player_proposal

        assert approve_player_proposal(proposal_path, edit_file) == proposal_path
        completed = load_player_specs(proposal_dir)[0]
        result = apply_player_spec(
            edit_file,
            completed,
            load_base_manifest().revision,
            edit_file.get_all_players(),
        )
        assert result.status == "created"
        assert edit_file.get_player_ability_profile(
            completed.identity.pes_id
        ) is not None
        assert edit_file.validate_integrity()["valid"] is True
        edit_file.save(decrypted / "data.dat")

        output = tmp_path / "created-EDIT00000000"
        crypto.encrypt(decrypted, output)
        reopened = crypto.decrypt(output)
        verified = EditFile()
        verified.load(reopened / "data.dat")
        assert verified.validate_integrity()["valid"] is True
        assert verified.get_team_roster(102).player_index(
            completed.identity.pes_id
        ) != -1

        unapproved_dir = tmp_path / "unapproved"
        unapproved_dir.mkdir()
        unapproved_path = unapproved_dir / proposal_path.name
        unapproved_path.write_bytes(unapproved_bytes)
        unapproved = load_player_specs(
            unapproved_dir,
            allow_proposals=True,
        )[0]
        before_unapproved = bytes(verified._data)
        blocked = apply_player_spec(
            verified,
            unapproved,
            load_base_manifest().revision,
            verified.get_all_players(),
        )
        assert (blocked.status, blocked.reason) == (
            "rejected",
            "human_review_required",
        )
        assert bytes(verified._data) == before_unapproved
    finally:
        crypto.cleanup_temp(decrypted)
        if reopened is not None:
            crypto.cleanup_temp(reopened)



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
