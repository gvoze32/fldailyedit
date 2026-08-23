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
DASTAN_ID = 1_073_003



def _assert_decoded_profile_matches_proposal(
    profile, proposal, *, compare_registered_position: bool
):
    assert profile is not None
    assert profile.age == proposal.age
    assert profile.height == proposal.height
    assert profile.weight == proposal.weight
    assert profile.playing_style == proposal.playing_style
    assert profile.strong_foot == proposal.strong_foot
    assert profile.weak_foot_usage == proposal.weak_foot_usage
    assert profile.weak_foot_accuracy == proposal.weak_foot_accuracy
    assert profile.form == proposal.form
    assert profile.injury_resistance == proposal.injury_resistance
    assert dict(profile.position_proficiency) == dict(proposal.position_proficiency)
    assert dict(profile.abilities) == dict(proposal.abilities)
    assert profile.player_skills == proposal.player_skills
    assert profile.com_styles == proposal.com_styles
    if compare_registered_position:
        assert profile.registered_position == proposal.registered_position


def _assert_complete_generated_payload(payload):
    from tests.test_generate_player_draft import (
        assert_no_missing_keys,
        assert_no_nulls,
    )

    assert set(payload) == {
        "schema_version",
        "operation",
        "lifecycle",
        "applies_to",
        "identity",
        "source",
        "evidence",
        "pes",
        "draft",
    }
    assert_no_missing_keys(payload)
    for key, value in payload.items():
        if key != "source":
            assert_no_nulls(value)


def _assert_completed_payload_has_no_proposal_metadata(payload):
    assert "source" not in payload
    assert "draft" not in payload
    assert "ovr_review" not in payload
    assert set(payload["evidence"]) == {
        "profile_url",
        "proof_urls",
        "effective_date",
        "reason",
    }


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
        from editor.player_spec import (
            approve_player_proposal,
            assess_update,
        )
        from scraper.pes_retro_snapshot import profile_from_snapshot
        from tools.generate_player_draft import validate_generated_proposal

        assert profile_from_snapshot(payload["source"]) == source_profile
        _assert_complete_generated_payload(payload)
        assert validate_generated_proposal(proposal_path, edit_file) == payload

        unapproved = load_player_specs(
            proposal_dir,
            allow_proposals=True,
        )[0]
        before_unapproved = bytes(edit_file._data)
        blocked = apply_player_spec(
            edit_file,
            unapproved,
            load_base_manifest().revision,
            edit_file.get_all_players(),
        )
        assert (blocked.status, blocked.reason) == (
            "rejected",
            "human_review_required",
        )
        assert bytes(edit_file._data) == before_unapproved
        assert proposal_path.read_bytes() == unapproved_bytes

        assert approve_player_proposal(proposal_path, edit_file) == proposal_path
        completed_payload = json.loads(proposal_path.read_text(encoding="utf-8"))
        _assert_completed_payload_has_no_proposal_metadata(completed_payload)
        completed_specs = load_player_specs(proposal_dir)
        assert len(completed_specs) == 1
        completed = completed_specs[0]
        assert completed.proposal is None
        assessment = assess_update(
            edit_file,
            completed,
            edit_file.get_all_players(),
        )
        assert (assessment.status, assessment.reason) == ("ready", "all_current")
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
        assert proposal.registered_position is None
        assert "registered_position" not in payload["pes"]
        _assert_decoded_profile_matches_proposal(
            verified.get_player_ability_profile(162196),
            proposal,
            compare_registered_position=False,
        )
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


def test_approved_create_proposal_is_valid_but_apply_is_disabled(
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
        validate_generated_proposal,
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
        assert edit_file.release_player(126925, 102) is True
        assert edit_file.get_team_roster(102).player_index(126925) == -1
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
        from editor.player_spec import (
            approve_player_proposal,
            apply_player_spec,
            assess_create,
        )
        from scraper.pes_retro_snapshot import profile_from_snapshot

        assert profile_from_snapshot(payload["source"]) == source_profile
        _assert_complete_generated_payload(payload)
        assert validate_generated_proposal(proposal_path, edit_file) == payload

        unapproved = load_player_specs(
            proposal_dir,
            allow_proposals=True,
        )[0]
        before_unapproved = bytes(edit_file._data)
        blocked = apply_player_spec(
            edit_file,
            unapproved,
            load_base_manifest().revision,
            edit_file.get_all_players(),
        )
        assert (blocked.status, blocked.reason) == (
            "rejected",
            "human_review_required",
        )
        assert bytes(edit_file._data) == before_unapproved
        assert proposal_path.read_bytes() == unapproved_bytes

        assert approve_player_proposal(proposal_path, edit_file) == proposal_path
        completed_payload = json.loads(proposal_path.read_text(encoding="utf-8"))
        _assert_completed_payload_has_no_proposal_metadata(completed_payload)
        completed_specs = load_player_specs(proposal_dir)
        assert len(completed_specs) == 1
        completed = completed_specs[0]
        assert completed.proposal is None
        assessment = assess_create(
            edit_file,
            completed,
            edit_file.get_all_players(),
        )
        assert (assessment.status, assessment.reason) == ("ready", "eligible")
        before_apply = bytes(edit_file._data)
        result = apply_player_spec(
            edit_file,
            completed,
            load_base_manifest().revision,
            edit_file.get_all_players(),
        )
        assert (result.status, result.reason) == (
            "rejected",
            "create_temporarily_unavailable",
        )
        assert bytes(edit_file._data) == before_apply
        assert edit_file.validate_integrity()["valid"] is True
        edit_file.save(decrypted / "data.dat")

        output = tmp_path / "unchanged-EDIT00000000"
        crypto.encrypt(decrypted, output)
        reopened = crypto.decrypt(output)
        verified = EditFile()
        verified.load(reopened / "data.dat")
        assert verified.validate_integrity()["valid"] is True
        assert verified.get_all_players().get(completed.identity.pes_id) is None
        assert verified.get_team_roster(102).player_index(
            completed.identity.pes_id
        ) == -1
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
        before_data = bytes(edit_file._data)
        results = apply_player_specs(
            edit_file,
            load_player_specs(),
            load_base_manifest().revision,
            edit_file.get_all_players(),
        )
        assert {result.name: (result.status, result.reason) for result in results} == {
            "Dastan Satpaev": ("rejected", "create_temporarily_unavailable"),
            "Kennet Eichhorn": ("rejected", "create_temporarily_unavailable"),
            "Marco Palestra": (
                "retired",
                "Superseded by Gondowan's 22 August 2026 EDIT base",
            ),
        }
        assert bytes(edit_file._data) == before_data
        assert edit_file.validate_integrity()["valid"] is True
        edit_file.save(decrypted / "data.dat")

        output = tmp_path / "updated-EDIT00000000"
        crypto.encrypt(decrypted, output)
        reopened = crypto.decrypt(output)
        verified = EditFile()
        verified.load(reopened / "data.dat")
        assert verified.validate_integrity()["valid"] is True
        assert bytes(verified._data) == before_data
        assert verified.get_all_players().get(DASTAN_ID) is None
    finally:
        crypto.cleanup_temp(decrypted)
        if reopened is not None:
            crypto.cleanup_temp(reopened)


def test_bundled_base_create_is_rejected_without_mutation(tmp_path):
    source = tmp_path / "EDIT00000000"
    shutil.copy2("base/EDIT00000000", source)
    decrypted = crypto.decrypt(source)
    reopened = None
    try:
        edit_file = EditFile()
        edit_file.load(decrypted / "data.dat")
        assert edit_file.release_player(126925, 102) is True
        dastan = next(
            spec for spec in load_player_specs() if spec.identity.pes_id == DASTAN_ID
        )
        before = bytes(edit_file._data)
        result = apply_player_spec(
            edit_file,
            dastan,
            load_base_manifest().revision,
            edit_file.get_all_players(),
        )
        assert (result.status, result.reason) == (
            "rejected",
            "create_temporarily_unavailable",
        )
        assert bytes(edit_file._data) == before
        assert edit_file.validate_integrity()["valid"] is True
        edit_file.save(decrypted / "data.dat")

        output = tmp_path / "unchanged-EDIT00000000"
        crypto.encrypt(decrypted, output)
        reopened = crypto.decrypt(output)
        verified = EditFile()
        verified.load(reopened / "data.dat")
        assert verified.validate_integrity()["valid"] is True
        assert verified.get_all_players().get(DASTAN_ID) is None
        assert verified.get_team_roster(378).player_index(DASTAN_ID) == -1
    finally:
        crypto.cleanup_temp(decrypted)
        if reopened is not None:
            crypto.cleanup_temp(reopened)
