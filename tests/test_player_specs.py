import json
import struct
from copy import deepcopy
from dataclasses import FrozenInstanceError
from typing import Callable

import pytest


REVISION = "fl26-u2.2-national-squads"

PALESTRA_ENTRY = bytes.fromhex(
    "9479020094790200d700b444000041233f2512073c5f730948e1f0083b220a4528"
    "6a3c003dd44f424503121201ca8800100000100000"
) + bytes(186)


def valid_marco_payload():
    return {
        "schema_version": 2,
        "operation": "update",
        "lifecycle": {"status": "active"},
        "applies_to": [REVISION],
        "identity": {
            "name": "Marco Palestra",
            "aliases": ["Marco Palestra"],
            "pes_id": 162196,
            "pes_retro_stats_id": "0ce2dbde-9cd9-423c-a90a-35b07df6a967",
        },
        "evidence": {
            "profile_url": "https://pesretrostats.com/player/0ce2dbde-marco-palestra",
            "proof_urls": [
                "https://pesretrostats.com/player/0ce2dbde-marco-palestra"
            ],
            "effective_date": "2026-07-25",
            "reason": "Pes Retro Stats profile reviewed for attribute proposal",
        },
        "pes": {
            "abilities": {
                "speed": {"from": 77, "to": 80},
                "acceleration": {"from": 75, "to": 77},
                "defensive_awareness": {"from": 61, "to": 62},
                "ball_winning": {"from": 59, "to": 60},
            }
        },
    }


def valid_dastan_payload():
    return {
        "schema_version": 2,
        "operation": "create",
        "lifecycle": {
            "status": "active",
            "reason": "Missing from bundled FL26 base",
        },
        "applies_to": [REVISION],
        "identity": {
            "name": "Dastan Satpaev",
            "print_name": "SATPAEV",
            "aliases": ["Dastan Satpaev"],
            "pes_id": 200000,
            "pes_retro_stats_id": "f77d9c27-8f02-4dbe-b877-4c13724a4886",
        },
        "evidence": {
            "profile_url": "https://pesretrostats.com/player/f77d9c27-dastan-satpaev",
            "proof_urls": [
                "https://qjl.kz/en/news/official-dastan-satpayev-signed-a-contract-with-chelsea",
                "https://www.chelseafc.com/en/news/article/chelsea-squad-numbers-2026-pre-season-tour-confirmed",
            ],
            "effective_date": "2026-08-04",
            "reason": "Chelsea included Satpaev in its 2026 pre-season squad before his contractual transfer date.",
        },
        "pes": {
            "player_id": 200000,
            "name": "Dastan Satpaev",
            "print_name": "SATPAEV",
            "team_id": 102,
            "team_name": "Chelsea FC",
            "preferred_shirt_number": 36,
            "nationality_id": 216,
            "age": 17,
            "height": 176,
            "weight": 73,
            "registered_position": "CF",
            "playing_style": 1,
            "strong_foot": 0,
            "weak_foot_usage": 2,
            "weak_foot_accuracy": 2,
            "form": 5,
            "injury_resistance": 2,
            "position_proficiency": {"LWF": 2, "RWF": 2, "SS": 1, "CF": 2},
            "abilities": {
                "attacking_awareness": 76,
                "ball_control": 74,
                "dribbling": 75,
                "tight_possession": 72,
                "low_pass": 68,
                "lofted_pass": 65,
                "finishing": 79,
                "heading": 68,
                "place_kicking": 65,
                "curl": 70,
                "speed": 80,
                "acceleration": 82,
                "kicking_power": 77,
                "jump": 70,
                "physical_contact": 68,
                "balance": 78,
                "stamina": 72,
                "defensive_awareness": 42,
                "ball_winning": 43,
                "aggression": 70,
                "gk_awareness": 40,
                "catching": 40,
                "clearing": 40,
                "reflexes": 40,
                "gk_reach": 40,
            },
            "player_skills": [],
            "com_styles": [],
            "skin_color": 2,
            "iris_color": 1,
        },
    }


def write_payload(directory, filename, payload):
    (directory / filename).write_text(json.dumps(payload), encoding="utf-8")


def complete_generated_proposal(operation: str = "create") -> dict[str, object]:
    from tests.test_generate_player_draft import (
        FakeEditFile,
        build_create_kwargs,
        issue_event,
        make_source,
        marco_source,
        proposal_for,
        update_request,
    )
    from tools.generate_player_draft import (
        build_player_draft,
        parse_player_issue_event,
    )

    if operation == "create":
        source = make_source()
        proposal = proposal_for(source)
        return build_player_draft(
            parse_player_issue_event(issue_event()),
            source,
            proposal,
            **build_create_kwargs(proposal),
        )
    if operation == "update":
        source = marco_source()
        proposal = proposal_for(source)
        return build_player_draft(
            update_request(),
            source,
            proposal,
            edit_file=FakeEditFile(proposal),
        )
    raise AssertionError(f"unsupported test proposal operation: {operation}")


def write_generated_proposal(directory, operation: str = "create"):
    payload = complete_generated_proposal(operation)
    filename = (
        "dastan-satpaev.json"
        if operation == "create"
        else "marco-palestra.json"
    )
    write_payload(directory, filename, payload)
    return payload


def write_approval_proposal(directory, monkeypatch, operation):
    import config
    from tests.test_generate_player_draft import (
        FakeEditFile,
        make_source,
        marco_source,
        proposal_for,
    )

    monkeypatch.setattr(config, "PLAYER_SPECS_DIR", directory)
    payload = complete_generated_proposal(operation)
    payload["evidence"]["issue_number"] = 123
    payload["evidence"][
        "issue_url"
    ] = "https://github.com/gvoze32/fldailyedit/issues/123"
    filename = (
        "dastan-satpaev.json"
        if operation == "create"
        else "marco-palestra.json"
    )
    path = directory / filename
    path.write_text(json.dumps(payload), encoding="utf-8")
    source = make_source() if operation == "create" else marco_source()
    return path, payload, FakeEditFile(proposal_for(source))


@pytest.mark.parametrize("operation", ["update", "create"])
def test_apply_player_spec_rejects_human_review_proposal_before_edit_access(
    tmp_path,
    monkeypatch,
    operation,
):
    from editor.player_spec import apply_player_spec, load_player_specs

    path, _, _ = write_approval_proposal(tmp_path, monkeypatch, operation)
    spec = load_player_specs(tmp_path, allow_proposals=True)[0]

    class GuardedEditFile:
        guarded = {
            "_data",
            "_player_cache",
            "get_all_players",
            "get_player_ability_profile",
            "player_catalog_report",
            "transferred_player_ids",
        }

        def __init__(self):
            object.__setattr__(self, "accesses", [])
            object.__setattr__(self, "_data", bytearray(b"untouched proposal gate"))
            object.__setattr__(self, "_player_cache", object())
            object.__setattr__(self, "player_catalog_report", {"untouched": True})
            object.__setattr__(self, "transferred_player_ids", {999})

        def __getattribute__(self, name):
            if name in object.__getattribute__(self, "guarded"):
                object.__getattribute__(self, "accesses").append(name)
            return object.__getattribute__(self, name)

        def get_all_players(self, include_base_db=True):
            return {}

        def get_player_ability_profile(self, _player_id):
            return None

    edit_file = GuardedEditFile()
    before = bytes(object.__getattribute__(edit_file, "_data"))

    result = apply_player_spec(edit_file, spec, REVISION, {})

    assert (result.status, result.reason) == (
        "rejected",
        "human_review_required",
    )
    assert edit_file.accesses == []
    assert bytes(object.__getattribute__(edit_file, "_data")) == before
    assert path.read_bytes()


@pytest.mark.parametrize("operation", ["update", "create"])
def test_direct_apply_helpers_reject_human_review_proposals(
    tmp_path, monkeypatch, operation
):
    from editor.player_spec import (
        apply_create,
        apply_update,
        load_player_specs,
    )

    write_approval_proposal(tmp_path, monkeypatch, operation)
    spec = load_player_specs(tmp_path, allow_proposals=True)[0]
    if operation == "create":
        edit_file = make_player_spec_edit_file(roster_size=39)
        apply = apply_create
    else:
        edit_file = make_player_spec_edit_file_with_palestra()
        apply = apply_update

    before = bytes(edit_file._data)
    result = apply(edit_file, spec, current_players(edit_file))

    assert (result.status, result.reason) == (
        "rejected",
        "human_review_required",
    )
    assert bytes(edit_file._data) == before



def test_approve_rejects_create_when_base_identity_appears(
    tmp_path, monkeypatch
):
    from editor.models import PlayerInfo
    from editor.player_spec import PlayerSpecError, approve_player_proposal

    path, _proposal, edit_file = write_approval_proposal(
        tmp_path, monkeypatch, "create"
    )
    edit_file.players[224999] = PlayerInfo(
        224999,
        "Dastan Satpaev",
        "SATPAEV",
    )
    before = path.read_bytes()

    with pytest.raises(PlayerSpecError, match="no longer applicable"):
        approve_player_proposal(path, edit_file)

    assert path.read_bytes() == before


def test_approve_allows_create_when_destination_roster_is_full(
    tmp_path, monkeypatch
):
    from editor.models import TeamData
    from editor.player_spec import approve_player_proposal

    path, _proposal, edit_file = write_approval_proposal(
        tmp_path, monkeypatch, "create"
    )
    edit_file.rosters[101] = TeamData(101, list(range(1, 41)))

    approve_player_proposal(path, edit_file)

    assert "draft" not in json.loads(path.read_text(encoding="utf-8"))

@pytest.mark.parametrize("operation", ["update", "create"])
def test_approve_player_proposal_transforms_exact_completed_shape(
    tmp_path,
    monkeypatch,
    operation,
):
    path, proposal, edit_file = write_approval_proposal(
        tmp_path, monkeypatch, operation
    )
    from editor.player_spec import approve_player_proposal, load_player_specs

    returned = approve_player_proposal(path, edit_file)

    expected = deepcopy(proposal)
    del expected["source"]
    del expected["draft"]
    expected["evidence"] = {
        "profile_url": proposal["evidence"]["profile_url"],
        "proof_urls": proposal["evidence"]["proof_urls"],
        "effective_date": proposal["evidence"]["effective_date"],
        "reason": (
            "Reviewed automated Pes Retro Stats proposal from issue #123"
        ),
    }
    completed = json.loads(path.read_text(encoding="utf-8"))
    assert returned == path
    assert completed == expected
    assert path.read_text(encoding="utf-8") == (
        json.dumps(expected, indent=2, sort_keys=True) + "\n"
    )
    assert set(completed["evidence"]) == {
        "profile_url",
        "proof_urls",
        "effective_date",
        "reason",
    }
    assert completed["identity"] == proposal["identity"]
    assert completed["pes"] == proposal["pes"]
    assert completed["applies_to"] == proposal["applies_to"]
    loaded = load_player_specs(tmp_path)
    assert len(loaded) == 1
    assert loaded[0].path == path
    assert loaded[0].proposal is None


@pytest.mark.parametrize("operation", ["update", "create"])
def test_approve_player_proposal_rejects_second_approval_without_mutation(
    tmp_path,
    monkeypatch,
    operation,
):
    from editor.player_spec import PlayerSpecError, approve_player_proposal
    from tools.generate_player_draft import PlayerDraftError

    path, _, edit_file = write_approval_proposal(
        tmp_path, monkeypatch, operation
    )
    approve_player_proposal(path, edit_file)
    approved_bytes = path.read_bytes()

    with pytest.raises((PlayerSpecError, PlayerDraftError)):
        approve_player_proposal(path, edit_file)

    assert path.read_bytes() == approved_bytes
    assert {item.name for item in tmp_path.iterdir()} == {path.name}


@pytest.mark.parametrize(
    "failure",
    ["stale-update", "tampered-update", "conflicting-create"],
)
def test_approve_player_proposal_rejects_untrusted_state_without_mutation(
    tmp_path,
    monkeypatch,
    failure,
):
    from editor.models import PlayerInfo
    from editor.player_spec import PlayerSpecError, approve_player_proposal
    from tests.test_generate_player_draft import (
        FakeEditFile,
        marco_source,
        proposal_for,
    )
    from tools.generate_player_draft import PlayerDraftError

    operation = "create" if failure == "conflicting-create" else "update"
    path, payload, edit_file = write_approval_proposal(
        tmp_path, monkeypatch, operation
    )
    if failure == "stale-update":
        edit_file = FakeEditFile(proposal_for(marco_source()), changed=False)
    elif failure == "tampered-update":
        payload["pes"]["abilities"]["speed"]["from"] -= 1
        path.write_text(json.dumps(payload), encoding="utf-8")
    else:
        edit_file.players[payload["identity"]["pes_id"]] = PlayerInfo(
            payload["identity"]["pes_id"],
            "Other Existing Player",
            "O. Existing",
        )
    original = path.read_bytes()

    with pytest.raises((PlayerSpecError, PlayerDraftError)):
        approve_player_proposal(path, edit_file)

    assert path.read_bytes() == original
    assert {item.name for item in tmp_path.iterdir()} == {path.name}


@pytest.mark.parametrize("failure", ["open", "write", "fsync", "replace"])
def test_approve_player_proposal_atomic_failures_preserve_original_and_clean_temp(
    tmp_path,
    monkeypatch,
    failure,
):
    import editor.player_spec as player_spec
    from editor.player_spec import PlayerSpecError, approve_player_proposal

    path, _, edit_file = write_approval_proposal(
        tmp_path, monkeypatch, "update"
    )
    original = path.read_bytes()
    real_named_temporary_file = player_spec.tempfile.NamedTemporaryFile

    if failure == "open":
        monkeypatch.setattr(
            player_spec.tempfile,
            "NamedTemporaryFile",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                OSError("temporary open failed")
            ),
        )
    elif failure == "write":
        class WriteFailure:
            def __init__(self, temporary):
                self.temporary = temporary
                self.name = temporary.name

            def __enter__(self):
                self.temporary.__enter__()
                return self

            def __exit__(self, *args):
                return self.temporary.__exit__(*args)

            def write(self, content):
                self.temporary.write(content[:17])
                raise OSError("temporary write failed")

            def __getattr__(self, name):
                return getattr(self.temporary, name)

        monkeypatch.setattr(
            player_spec.tempfile,
            "NamedTemporaryFile",
            lambda *args, **kwargs: WriteFailure(
                real_named_temporary_file(*args, **kwargs)
            ),
        )
    elif failure == "fsync":
        monkeypatch.setattr(
            player_spec.os,
            "fsync",
            lambda _descriptor: (_ for _ in ()).throw(
                OSError("temporary fsync failed")
            ),
        )
    else:
        monkeypatch.setattr(
            player_spec.os,
            "replace",
            lambda *_args: (_ for _ in ()).throw(
                OSError("atomic replace failed")
            ),
        )

    with pytest.raises((PlayerSpecError, OSError)):
        approve_player_proposal(path, edit_file)

    assert path.read_bytes() == original
    assert {item.name for item in tmp_path.iterdir()} == {path.name}


def test_approve_player_proposal_flushes_fsyncs_and_atomically_replaces(
    tmp_path,
    monkeypatch,
):
    import editor.player_spec as player_spec
    from editor.player_spec import approve_player_proposal

    path, _, edit_file = write_approval_proposal(
        tmp_path, monkeypatch, "update"
    )
    events = []
    allocations = []
    real_named_temporary_file = player_spec.tempfile.NamedTemporaryFile
    real_fsync = player_spec.os.fsync
    real_replace = player_spec.os.replace

    class TrackedTemporary:
        def __init__(self, temporary):
            self.temporary = temporary
            self.name = temporary.name

        def __enter__(self):
            self.temporary.__enter__()
            return self

        def __exit__(self, *args):
            return self.temporary.__exit__(*args)

        def flush(self):
            events.append("flush")
            return self.temporary.flush()

        def __getattr__(self, name):
            return getattr(self.temporary, name)

    def tracked_named_temporary_file(*args, **kwargs):
        allocations.append(kwargs)
        return TrackedTemporary(real_named_temporary_file(*args, **kwargs))

    def tracked_fsync(descriptor):
        events.append("fsync")
        return real_fsync(descriptor)

    def tracked_replace(source, destination):
        events.append("replace")
        assert Path(source).parent == path.parent
        assert Path(destination) == path
        return real_replace(source, destination)

    from pathlib import Path

    monkeypatch.setattr(
        player_spec.tempfile,
        "NamedTemporaryFile",
        tracked_named_temporary_file,
    )
    monkeypatch.setattr(player_spec.os, "fsync", tracked_fsync)
    monkeypatch.setattr(player_spec.os, "replace", tracked_replace)

    assert approve_player_proposal(path, edit_file) == path

    assert len(allocations) == 1
    assert allocations[0]["mode"] == "w"
    assert allocations[0]["encoding"] == "utf-8"
    assert Path(allocations[0]["dir"]) == tmp_path
    assert allocations[0]["delete"] is False
    assert events.index("flush") < events.index("fsync") < events.index("replace")
    assert {item.name for item in tmp_path.iterdir()} == {path.name}


@pytest.mark.parametrize("operation", ["create", "update"])
def test_mature_proposals_require_human_approval_by_default(tmp_path, operation):
    from editor.player_spec import PlayerSpecError, load_player_specs

    write_generated_proposal(tmp_path, operation)

    with pytest.raises(PlayerSpecError, match="requires human approval"):
        load_player_specs(tmp_path)


@pytest.mark.parametrize("operation", ["create", "update"])
def test_proposal_aware_loader_returns_exact_immutable_metadata(tmp_path, operation):
    from editor.player_spec import load_player_specs

    payload = write_generated_proposal(tmp_path, operation)

    spec = load_player_specs(tmp_path, allow_proposals=True)[0]

    assert spec.operation == operation
    assert spec.proposal is not None
    assert spec.proposal.generator == "pes-retro-mature-proposal-v1"
    assert spec.proposal.needs_human_review is True
    assert spec.proposal.source_snapshot == payload["source"]
    assert spec.proposal.ovr_review == payload["draft"]["ovr_review"]
    assert spec.proposal.issue_number == 42
    assert (
        spec.proposal.issue_url
        == "https://github.com/gvoze32/fldailyedit/issues/42"
    )
    assert spec.proposal.submitted_team == "Chelsea FC"
    with pytest.raises(FrozenInstanceError):
        setattr(spec.proposal, "generator", "untrusted-generator")
    with pytest.raises(TypeError):
        spec.proposal.source_snapshot["model"] = "untrusted-model"
    with pytest.raises(TypeError):
        spec.proposal.ovr_review["mode"] = "untrusted-mode"
    with pytest.raises(TypeError):
        spec.proposal.source_snapshot["data"]["current_club"] = "untrusted-club"
    with pytest.raises(TypeError):
        spec.proposal.ovr_review["positions"][0]["proposal_tenths"] = 1


def test_proposal_metadata_lists_reject_unbound_list_mutation(tmp_path):
    from editor.player_spec import load_player_specs

    payload = write_generated_proposal(tmp_path)
    spec = load_player_specs(tmp_path, allow_proposals=True)[0]
    assert spec.proposal is not None
    positions = spec.proposal.ovr_review["positions"]
    expected_positions = payload["draft"]["ovr_review"]["positions"]

    assert positions == expected_positions
    assert expected_positions == positions
    with pytest.raises(TypeError):
        list.__setitem__(positions, 0, {"untrusted": True})
    assert positions == expected_positions


def test_proposal_update_with_unsupported_source_position_uses_any_codec_candidate(
    tmp_path,
):
    from dataclasses import replace
    from types import MappingProxyType

    from editor.player_spec import load_player_specs
    from tests.test_generate_player_draft import (
        FakeEditFile,
        SOURCE_POSITIONS,
        current_profile,
        marco_source,
        proposal_for,
        update_request,
    )
    from tools.generate_player_draft import (
        build_player_draft,
        validate_generated_proposal,
    )

    positions = {position: None for position in SOURCE_POSITIONS}
    positions.update({"RB": "A", "RWB": "★", "RMF": "B"})
    source = marco_source(positions=MappingProxyType(positions))
    proposal = proposal_for(source)
    assert proposal.registered_position is None
    edit_file = FakeEditFile(proposal)
    edit_file.profiles[162196] = replace(
        current_profile(proposal, speed=proposal.abilities["speed"] - 1),
        registered_position="CF",
        registered_position_id=12,
    )
    payload = build_player_draft(
        update_request(),
        source,
        proposal,
        edit_file=edit_file,
    )
    path = tmp_path / "marco-palestra.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    spec = load_player_specs(tmp_path, allow_proposals=True)[0]

    assert spec.identity.pes_id == 162196
    assert validate_generated_proposal(path, edit_file) == payload


def test_player_spec_loader_rejects_oversized_json_before_decoding(tmp_path):
    from editor.player_spec import PlayerSpecError, load_player_specs

    payload = json.dumps(complete_generated_proposal())
    assert len(payload.encode("utf-8")) < 2 * 1024 * 1024
    path = tmp_path / "dastan-satpaev.json"
    path.write_text(payload + " " * (2 * 1024 * 1024), encoding="utf-8")

    with pytest.raises(PlayerSpecError, match="exceeds the maximum size"):
        load_player_specs(tmp_path, allow_proposals=True)


def test_player_spec_loader_translates_deep_json_recursion(tmp_path, monkeypatch):
    import editor.player_spec as player_spec
    from editor.player_spec import PlayerSpecError, load_player_specs

    path = tmp_path / "deep.json"
    path.write_text("{}", encoding="utf-8")

    def raise_recursion(*args, **kwargs):
        raise RecursionError("too deeply nested")

    monkeypatch.setattr(player_spec.json, "loads", raise_recursion)
    with pytest.raises(PlayerSpecError, match="JSON is too deeply nested"):
        load_player_specs(tmp_path, allow_proposals=True)



def _add_old_draft_missing(payload: dict[str, object]) -> None:
    payload["draft"]["missing"] = []


def _null_issue_number(payload: dict[str, object]) -> None:
    payload["evidence"]["issue_number"] = None



def _null_review_flag(payload: dict[str, object]) -> None:
    payload["draft"]["needs_human_review"] = None

def _add_unknown_draft_key(payload: dict[str, object]) -> None:
    payload["draft"]["unexpected"] = True


def _malform_source_snapshot(payload: dict[str, object]) -> None:
    payload["source"]["snapshot_sha256"] = "not-a-sha256"


def _mismatch_operation_and_ovr_mode(payload: dict[str, object]) -> None:
    payload["draft"]["ovr_review"]["mode"] = "comparison"


def _invalidate_ovr_shape(payload: dict[str, object]) -> None:
    del payload["draft"]["ovr_review"]["positions"]


@pytest.mark.parametrize(
    ("mutate", "diagnostic"),
    [
        pytest.param(_add_old_draft_missing, "draft.*missing", id="old-missing"),
        pytest.param(_null_review_flag, "needs_human_review", id="null-review-flag"),
        pytest.param(
            _add_unknown_draft_key,
            "draft.*unexpected",
            id="unknown-key",
        ),
        pytest.param(
            _malform_source_snapshot,
            "source|snapshot_sha256",
            id="malformed-snapshot",
        ),
        pytest.param(
            _mismatch_operation_and_ovr_mode,
            "ovr_review.*mode|mode.*create",
            id="operation-mode-mismatch",
        ),
        pytest.param(
            _invalidate_ovr_shape,
            "ovr_review.*positions",
            id="invalid-ovr-shape",
        ),
    ],
)
def test_proposal_aware_loader_rejects_noncanonical_schema(
    tmp_path,
    mutate: Callable[[dict[str, object]], None],
    diagnostic: str,
):
    from editor.player_spec import PlayerSpecError, load_player_specs

    payload = deepcopy(complete_generated_proposal())
    mutate(payload)
    write_payload(tmp_path, "dastan-satpaev.json", payload)

    with pytest.raises(PlayerSpecError, match=diagnostic):
        load_player_specs(tmp_path, allow_proposals=True)


def test_verify_base_file_matches_bundled_edit():
    from editor.player_spec import verify_base_file

    manifest = verify_base_file("base/EDIT00000000")

    assert manifest.revision == REVISION


def test_verify_base_file_rejects_mismatch_and_malformed_manifest(tmp_path):
    from editor.player_spec import PlayerSpecError, verify_base_file

    edit_file = tmp_path / "EDIT00000000"
    edit_file.write_bytes(b"wrong base")
    manifest = tmp_path / "base_manifest.json"
    manifest.write_text(
        json.dumps({"revision": REVISION, "sha256": "0" * 64}),
        encoding="utf-8",
    )
    with pytest.raises(PlayerSpecError, match="digest mismatch"):
        verify_base_file(edit_file, manifest)

    manifest.write_text(
        json.dumps({"revision": REVISION, "sha256": "not-a-digest"}),
        encoding="utf-8",
    )
    with pytest.raises(PlayerSpecError, match="sha256"):
        verify_base_file(edit_file, manifest)


def test_load_specs_rejects_filename_identity_and_duplicate_ids(tmp_path):
    from editor.player_spec import PlayerSpecError, load_player_specs

    write_payload(tmp_path, "wrong-name.json", valid_marco_payload())
    with pytest.raises(PlayerSpecError, match="filename"):
        load_player_specs(tmp_path)

    (tmp_path / "wrong-name.json").unlink()
    write_payload(tmp_path, "marco-palestra.json", valid_marco_payload())
    duplicate = valid_dastan_payload()
    duplicate["identity"]["pes_id"] = 162196
    duplicate["pes"]["player_id"] = 162196
    write_payload(tmp_path, "dastan-satpaev.json", duplicate)
    with pytest.raises(PlayerSpecError, match="PES ID"):
        load_player_specs(tmp_path)

def test_completed_specs_reject_schema_version_1(tmp_path):
    from editor.player_spec import PlayerSpecError, load_player_specs

    payload = valid_marco_payload()
    payload["schema_version"] = 1
    write_payload(tmp_path, "marco-palestra.json", payload)

    with pytest.raises(PlayerSpecError, match="schema_version"):
        load_player_specs(tmp_path)


def test_completed_specs_reject_sortitoutsi_id(tmp_path):
    from editor.player_spec import PlayerSpecError, load_player_specs

    payload = valid_marco_payload()
    payload["identity"]["sortitoutsi_id"] = 2000136198
    write_payload(tmp_path, "marco-palestra.json", payload)

    with pytest.raises(PlayerSpecError, match="sortitoutsi_id"):
        load_player_specs(tmp_path)


@pytest.mark.parametrize(
    "pes_retro_stats_id",
    (
        "not-a-uuid",
        "0CE2DBDE-9CD9-423C-A90A-35B07DF6A967",
    ),
)
def test_completed_specs_reject_noncanonical_pes_retro_stats_uuid(
    tmp_path, pes_retro_stats_id
):
    from editor.player_spec import PlayerSpecError, load_player_specs

    payload = valid_marco_payload()
    payload["identity"]["pes_retro_stats_id"] = pes_retro_stats_id
    write_payload(tmp_path, "marco-palestra.json", payload)

    with pytest.raises(PlayerSpecError, match="pes_retro_stats_id"):
        load_player_specs(tmp_path)


@pytest.mark.parametrize(
    "profile_url",
    (
        "https://pesretrostats.com/player/f77d9c27-marco-palestra",
        "https://www.pesretrostats.com/player/0ce2dbde-marco-palestra",
        "https://pesretrostats.com/player/0ce2dbde/Marco-Palestra",
    ),
)
def test_completed_specs_require_canonical_matching_pes_retro_stats_profile(
    tmp_path, profile_url
):
    from editor.player_spec import PlayerSpecError, load_player_specs

    payload = valid_marco_payload()
    payload["evidence"]["profile_url"] = profile_url
    write_payload(tmp_path, "marco-palestra.json", payload)

    with pytest.raises(PlayerSpecError, match="profile_url"):
        load_player_specs(tmp_path)

@pytest.mark.parametrize(
    ("pes_retro_stats_id", "profile_url", "message"),
    (
        (
            " 0ce2dbde-9cd9-423c-a90a-35b07df6a967",
            "https://pesretrostats.com/player/0ce2dbde-marco-palestra",
            "pes_retro_stats_id",
        ),
        (
            "0ce2dbde-9cd9-423c-a90a-35b07df6a967",
            " https://pesretrostats.com/player/0ce2dbde-marco-palestra",
            "profile_url",
        ),
    ),
)
def test_completed_specs_reject_whitespace_wrapped_source_identity(
    tmp_path, pes_retro_stats_id, profile_url, message
):
    from editor.player_spec import PlayerSpecError, load_player_specs

    payload = valid_marco_payload()
    payload["identity"]["pes_retro_stats_id"] = pes_retro_stats_id
    payload["evidence"]["profile_url"] = profile_url
    write_payload(tmp_path, "marco-palestra.json", payload)

    with pytest.raises(PlayerSpecError, match=message):
        load_player_specs(tmp_path)


@pytest.mark.parametrize(
    ("field", "control"),
    (("name", "\x00"), ("print_name", "\x1f"), ("aliases", "\x7f")),
)
def test_create_identity_rejects_embedded_c0_and_del_before_serialization(
    tmp_path, field, control
):
    from editor.player_spec import PlayerSpecError, load_player_specs

    payload = valid_dastan_payload()
    if field == "aliases":
        value = payload["identity"]["aliases"][0]
        payload["identity"]["aliases"][0] = value[:1] + control + value[1:]
    else:
        value = payload["identity"][field]
        controlled = value[:1] + control + value[1:]
        payload["identity"][field] = controlled
        payload["pes"][field] = controlled
    write_payload(tmp_path, "dastan-satpaev.json", payload)

    with pytest.raises(PlayerSpecError, match="canonical text"):
        load_player_specs(tmp_path)

@pytest.mark.parametrize(
    ("field", "controlled"),
    (
        ("name", "Dastan Satpaev\n"),
        ("print_name", "\tSATPAEV"),
        ("aliases", "Dastan Satpaev\r"),
    ),
)
def test_identity_rejects_boundary_controls_instead_of_trimming_them(
    tmp_path, field, controlled
):
    from editor.player_spec import PlayerSpecError, load_player_specs

    payload = valid_dastan_payload()
    if field == "aliases":
        payload["identity"]["aliases"][0] = controlled
    else:
        payload["identity"][field] = controlled
        payload["pes"][field] = controlled
    write_payload(tmp_path, "dastan-satpaev.json", payload)

    with pytest.raises(PlayerSpecError, match="canonical text"):
        load_player_specs(tmp_path)




def test_update_patch_requires_distinct_in_range_values(tmp_path):
    from editor.player_spec import PlayerSpecError, load_player_specs

    payload = valid_marco_payload()
    payload["pes"]["abilities"]["speed"] = {"from": 100, "to": 100}
    write_payload(tmp_path, "marco-palestra.json", payload)
    with pytest.raises(PlayerSpecError, match="speed"):
        load_player_specs(tmp_path)


def test_valid_create_and_update_specs_load_in_filename_order(tmp_path):
    from editor.player_spec import FieldPatch, load_player_specs

    write_payload(tmp_path, "marco-palestra.json", valid_marco_payload())
    write_payload(tmp_path, "dastan-satpaev.json", valid_dastan_payload())
    (tmp_path / "ignored.txt").write_text("not json", encoding="utf-8")

    specs = load_player_specs(tmp_path)

    assert tuple(spec.path.name for spec in specs) == (
        "dastan-satpaev.json",
        "marco-palestra.json",
    )
    dastan, marco = specs
    assert dastan.create is not None
    assert dastan.create.abilities["finishing"] == 79
    assert dastan.identity.aliases == ("Dastan Satpaev",)
    assert dastan.identity.pes_retro_stats_id == (
        "f77d9c27-8f02-4dbe-b877-4c13724a4886"
    )
    assert marco.create is None
    assert marco.patches["speed"] == FieldPatch(current=77, target=80)
    assert marco.evidence.effective_date.isoformat() == "2026-07-25"


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda payload: payload.update({"unknown": True}), "unknown"),
        (lambda payload: payload["lifecycle"].update({"status": "paused"}), "lifecycle"),
        (lambda payload: payload["identity"].update({"aliases": []}), "aliases"),
        (
            lambda payload: payload["identity"].update(
                {"aliases": ["Marco Palestra", "Marco Palestra"]}
            ),
            "aliases",
        ),
        (
            lambda payload: payload["evidence"].update(
                {"profile_url": "http://example.com/player"}
            ),
            "HTTPS",
        ),
        (
            lambda payload: payload["pes"].update(
                {"overall_rating": {"from": 72, "to": 73}}
            ),
            "PES field",
        ),
        (
            lambda payload: payload["pes"]["abilities"].update(
                {"speed": {"from": 39, "to": 80}}
            ),
            "speed",
        ),
    ],
)
def test_update_specs_are_strict(tmp_path, mutate, message):
    from editor.player_spec import PlayerSpecError, load_player_specs

    payload = valid_marco_payload()
    mutate(payload)
    write_payload(tmp_path, "marco-palestra.json", payload)
    with pytest.raises(PlayerSpecError, match=message):
        load_player_specs(tmp_path)


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("age",), 64, "age"),
        (("playing_style",), 32, "playing_style"),
        (("position_proficiency", "CF"), 4, "CF"),
        (("abilities", "speed"), 100, "speed"),
        (("skin_color",), 256, "skin_color"),
    ],
)
def test_create_values_obey_codec_widths_and_ability_range(tmp_path, path, value, message):
    from editor.player_spec import PlayerSpecError, load_player_specs

    payload = valid_dastan_payload()
    target = payload["pes"]
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    write_payload(tmp_path, "dastan-satpaev.json", payload)
    with pytest.raises(PlayerSpecError, match=message):
        load_player_specs(tmp_path)


@pytest.mark.parametrize("preferred_shirt_number", [1, 99])
def test_create_preferred_shirt_number_accepts_allocator_boundaries(
    tmp_path, preferred_shirt_number
):
    from editor.player_spec import load_player_specs

    payload = valid_dastan_payload()
    payload["pes"]["preferred_shirt_number"] = preferred_shirt_number
    write_payload(tmp_path, "dastan-satpaev.json", payload)

    spec = load_player_specs(tmp_path)[0]

    assert spec.create is not None
    assert spec.create.preferred_shirt_number == preferred_shirt_number


@pytest.mark.parametrize("preferred_shirt_number", [0, 100])
def test_create_preferred_shirt_number_rejects_values_outside_allocator_range(
    tmp_path, preferred_shirt_number
):
    from editor.player_spec import PlayerSpecError, load_player_specs

    payload = valid_dastan_payload()
    payload["pes"]["preferred_shirt_number"] = preferred_shirt_number
    write_payload(tmp_path, "dastan-satpaev.json", payload)

    with pytest.raises(PlayerSpecError, match="preferred_shirt_number"):
        load_player_specs(tmp_path)


def test_validate_spec_set_rejects_normalized_aliases_and_pes_retro_stats_ids(
    tmp_path,
):
    from editor.player_spec import PlayerSpecError, load_player_specs

    marco = valid_marco_payload()
    dastan = valid_dastan_payload()
    dastan["identity"]["aliases"].append("Márco Palestra")
    write_payload(tmp_path, "marco-palestra.json", marco)
    write_payload(tmp_path, "dastan-satpaev.json", dastan)
    with pytest.raises(PlayerSpecError, match="alias"):
        load_player_specs(tmp_path)

    dastan["identity"]["aliases"] = ["Dastan Satpaev"]
    dastan["identity"]["pes_retro_stats_id"] = (
        "0ce2dbde-9cd9-423c-a90a-35b07df6a967"
    )
    dastan["evidence"]["profile_url"] = (
        "https://pesretrostats.com/player/0ce2dbde-dastan-satpaev"
    )
    write_payload(tmp_path, "dastan-satpaev.json", dastan)
    with pytest.raises(PlayerSpecError, match="Pes Retro Stats ID"):
        load_player_specs(tmp_path)


def test_player_slug_normalizes_unicode_to_ascii_tokens():
    from editor.player_spec import player_slug

    assert player_slug("  Dastan Sätpaev -- U-21  ") == "dastan-satpaev-u-21"


def dastan_spec(tmp_path):
    from editor.player_spec import load_player_specs

    write_payload(tmp_path, "dastan-satpaev.json", valid_dastan_payload())
    return next(
        spec for spec in load_player_specs(tmp_path) if spec.identity.pes_id == 200000
    )


def marco_spec(tmp_path):
    from editor.player_spec import load_player_specs

    write_payload(tmp_path, "marco-palestra.json", valid_marco_payload())
    return next(
        spec for spec in load_player_specs(tmp_path) if spec.identity.pes_id == 162196
    )


def make_player_spec_edit_file(roster_size: int):
    from editor.editfile import (
        GAME_PLAN_ENTRY_SIZE,
        GP_LINEUP,
        HEADER_SIZE,
        HDR_GAME_PLAN_COUNT,
        HDR_PLAYER_COUNT,
        HDR_TEAM_COUNT,
        HDR_TEAM_PLAYER_COUNT,
        MAX_GAME_PLANS,
        PE_PLAYER_ID,
        PE_PLAYER_NAME,
        PE_PRINT_NAME,
        PLAYER_APPEARANCE_SIZE,
        PLAYER_ENTRY_SIZE,
        PLAYER_TOTAL_SIZE,
        TEAM_PLAYER_ENTRY_SIZE,
        TE_TEAM_ID,
        TE_TEAM_NAME,
        TP_PLAYER_IDS,
        TP_SHIRT_NUMBERS,
        TP_TEAM_ID,
        EditFile,
    )

    edit_file = EditFile()
    edit_file._calculate_offsets()
    edit_file._data = bytearray(
        edit_file.game_plan_start + MAX_GAME_PLANS * GAME_PLAN_ENTRY_SIZE
    )
    struct.pack_into("<H", edit_file._data, HDR_PLAYER_COUNT, 1)
    struct.pack_into("<H", edit_file._data, HDR_TEAM_COUNT, 1)
    struct.pack_into("<H", edit_file._data, HDR_TEAM_PLAYER_COUNT, 1)
    struct.pack_into("<H", edit_file._data, HDR_GAME_PLAN_COUNT, 1)
    edit_file._parse_header()

    existing_id = 181639
    player_offset = HEADER_SIZE
    struct.pack_into("<I", edit_file._data, player_offset + PE_PLAYER_ID, existing_id)
    struct.pack_into("<I", edit_file._data, player_offset + 4, existing_id)
    edit_file._data[
        player_offset + PE_PLAYER_NAME : player_offset + PE_PLAYER_NAME + 16
    ] = b"Existing Player\0"
    edit_file._data[
        player_offset + PE_PRINT_NAME : player_offset + PE_PRINT_NAME + 9
    ] = b"EXISTING\0"
    struct.pack_into(
        "<I",
        edit_file._data,
        player_offset + PLAYER_ENTRY_SIZE,
        existing_id,
    )
    assert PLAYER_TOTAL_SIZE == PLAYER_ENTRY_SIZE + PLAYER_APPEARANCE_SIZE

    struct.pack_into("<I", edit_file._data, edit_file.team_start + TE_TEAM_ID, 102)
    edit_file._data[
        edit_file.team_start + TE_TEAM_NAME : edit_file.team_start + TE_TEAM_NAME + 11
    ] = b"Chelsea FC\0"

    roster_offset = edit_file.team_player_start
    struct.pack_into("<I", edit_file._data, roster_offset + TP_TEAM_ID, 102)
    player_ids = list(range(100001, 100001 + roster_size))
    available_shirts = list(range(1, 36)) + list(range(37, 42))
    for slot, player_id in enumerate(player_ids):
        struct.pack_into(
            "<I",
            edit_file._data,
            roster_offset + TP_PLAYER_IDS + slot * 4,
            player_id,
        )
        struct.pack_into(
            "<H",
            edit_file._data,
            roster_offset + TP_SHIRT_NUMBERS + slot * 2,
            available_shirts[slot],
        )

    struct.pack_into("<I", edit_file._data, edit_file.game_plan_start, 102)
    lineup = bytes(range(roster_size)) + bytes([0xFF] * (40 - roster_size))
    edit_file._data[
        edit_file.game_plan_start
        + GP_LINEUP : edit_file.game_plan_start
        + GP_LINEUP
        + 40
    ] = lineup
    return edit_file


def make_player_spec_edit_file_with_palestra(**updates):
    from editor.editfile import (
        PE_PLAYER_NAME,
        PE_PRINT_NAME,
        PLAYER_APPEARANCE_SIZE,
        EditFile,
    )
    from editor.player_codec import patch_player_entry

    entry = bytearray(patch_player_entry(PALESTRA_ENTRY, updates))
    entry[PE_PLAYER_NAME : PE_PLAYER_NAME + 15] = b"Marco Palestra\0"
    entry[PE_PRINT_NAME : PE_PRINT_NAME + 9] = b"PALESTRA\0"
    appearance = bytes(
        (index * 3 + 7) % 256 for index in range(PLAYER_APPEARANCE_SIZE)
    )

    edit_file = EditFile()
    edit_file._data = entry + bytearray(appearance)
    edit_file.player_start = 0
    edit_file.player_count = 1
    return edit_file


def make_combined_fixture(chelsea_roster_size: int, **updates):
    from editor.editfile import (
        HEADER_SIZE,
        PE_PLAYER_NAME,
        PE_PRINT_NAME,
        PLAYER_APPEARANCE_SIZE,
        PLAYER_ENTRY_SIZE,
    )
    from editor.player_codec import patch_player_entry

    edit_file = make_player_spec_edit_file(roster_size=chelsea_roster_size)
    entry = bytearray(patch_player_entry(PALESTRA_ENTRY, updates))
    entry[PE_PLAYER_NAME : PE_PLAYER_NAME + 15] = b"Marco Palestra\0"
    entry[PE_PRINT_NAME : PE_PRINT_NAME + 9] = b"PALESTRA\0"
    appearance = bytes(
        (index * 3 + 7) % 256 for index in range(PLAYER_APPEARANCE_SIZE)
    )
    edit_file._data[HEADER_SIZE : HEADER_SIZE + PLAYER_ENTRY_SIZE] = entry
    edit_file._data[
        HEADER_SIZE + PLAYER_ENTRY_SIZE :
        HEADER_SIZE + PLAYER_ENTRY_SIZE + PLAYER_APPEARANCE_SIZE
    ] = appearance
    return edit_file


def current_players(edit_file):
    return edit_file.get_all_players(include_base_db=False)


def unchanged_bits(before, after, changed_fields):
    from editor.player_codec import ABILITY_FIELDS, FIELD_SPECS

    allowed_fields = set(changed_fields)
    if allowed_fields.intersection(ABILITY_FIELDS):
        allowed_fields.add("edited_abilities")

    allowed_mask = 0
    for field in allowed_fields:
        field_spec = FIELD_SPECS[field]
        allowed_mask |= (
            ((1 << field_spec.width) - 1)
            << (field_spec.byte_offset * 8 + field_spec.bit_offset)
        )
    changed_mask = int.from_bytes(before, "little") ^ int.from_bytes(after, "little")
    return changed_mask & ~allowed_mask == 0


def test_create_serializer_builds_linked_player_and_appearance_records(tmp_path):
    from editor.player_codec import (
        FIELD_SPECS,
        PLAYER_APPEARANCE_SIZE,
        PLAYER_DATA_SIZE,
        _read_field,
        decode_player_entry,
        serialize_created_player,
    )

    spec = dastan_spec(tmp_path)
    assert spec.create is not None

    player_entry, appearance_entry = serialize_created_player(spec.create)
    profile = decode_player_entry(player_entry)

    assert len(player_entry) == PLAYER_DATA_SIZE
    assert len(appearance_entry) == PLAYER_APPEARANCE_SIZE
    assert int.from_bytes(player_entry[:4], "little") == spec.identity.pes_id
    assert int.from_bytes(player_entry[4:8], "little") == spec.identity.pes_id
    assert int.from_bytes(appearance_entry[:4], "little") == spec.identity.pes_id
    assert int.from_bytes(appearance_entry[8:12], "little") == 0
    assert appearance_entry[4] & (1 << 2)
    assert appearance_entry[12:19] == bytes([0x77] * 7)
    assert appearance_entry[45] == spec.create.skin_color
    assert appearance_entry[64] == spec.create.iris_color
    assert (
        player_entry[0x36:0x73].split(b"\0", 1)[0].decode()
        == spec.identity.name
    )
    assert (
        player_entry[0x73:0xB0].split(b"\0", 1)[0].decode()
        == spec.identity.print_name
    )
    assert (
        player_entry[0xB0:0xF0].split(b"\0", 1)[0].decode()
        == spec.identity.print_name
    )
    assert profile.player_id == spec.identity.pes_id
    assert profile.nationality_id == spec.create.nationality_id
    assert (profile.age, profile.height, profile.weight) == (
        spec.create.age,
        spec.create.height,
        spec.create.weight,
    )
    assert profile.registered_position == spec.create.registered_position
    assert profile.playing_style == spec.create.playing_style
    assert profile.abilities == spec.create.abilities
    assert profile.position_proficiency == {
        "GK": 0,
        "CB": 0,
        "LB": 0,
        "RB": 0,
        "DMF": 0,
        "CMF": 0,
        "LMF": 0,
        "RMF": 0,
        "AMF": 0,
        "RWF": 2,
        "SS": 1,
        "CF": 2,
        "LWF": 2,
    }
    for flag in (
        "edited_player",
        "edited_basic_settings",
        "edited_registered_position",
        "edited_playable_positions",
        "edited_abilities",
    ):
        assert _read_field(player_entry, FIELD_SPECS[flag]) == 1
    assert _read_field(player_entry, FIELD_SPECS["strong_foot"]) == 0


def test_full_roster_returns_waiting_without_mutation(tmp_path, monkeypatch):
    from editor.player_spec import apply_create

    edit_file = make_player_spec_edit_file(roster_size=40)
    before = bytes(edit_file._data)
    spec = dastan_spec(tmp_path)
    monkeypatch.setattr(
        edit_file,
        "release_player",
        lambda *args, **kwargs: pytest.fail("create must never release a player"),
    )

    result = apply_create(edit_file, spec, {})

    assert (result.status, result.reason) == (
        "waiting",
        "destination_roster_full",
    )
    assert edit_file.player_count == 1
    assert bytes(edit_file._data) == before


def test_create_registers_linked_roster_and_game_plan_and_is_idempotent(
    tmp_path,
):
    from editor.editfile import GP_LINEUP
    from editor.player_spec import apply_create

    edit_file = make_player_spec_edit_file(roster_size=39)
    spec = dastan_spec(tmp_path)
    assert spec.create is not None

    result = apply_create(edit_file, spec, {})

    assert result.status == "created"
    assert edit_file.player_count == 2
    assert struct.unpack_from("<H", edit_file._data, 0x60)[0] == 2
    profile = edit_file.get_player_ability_profile(spec.identity.pes_id)
    assert profile is not None
    assert profile.abilities == spec.create.abilities
    roster = edit_file.get_team_roster(spec.create.team_id)
    assert roster is not None
    assert roster.player_ids[39] == spec.identity.pes_id
    assert roster.shirt_numbers[39] == spec.create.preferred_shirt_number
    assert edit_file._data[edit_file.game_plan_start + GP_LINEUP + 39] == 39

    before_second_run = bytes(edit_file._data)
    current_players = edit_file.get_all_players(include_base_db=False)
    second_result = apply_create(edit_file, spec, current_players)

    assert second_result.status == "already_applied"
    assert edit_file.player_count == 2
    assert bytes(edit_file._data) == before_second_run


def test_create_rolls_back_bytes_header_counts_and_offsets_when_roster_add_fails(
    tmp_path,
    monkeypatch,
):
    from editor.editfile import HDR_PLAYER_COUNT
    from editor.player_spec import PlayerSpecError, apply_create

    edit_file = make_player_spec_edit_file(roster_size=39)
    before = bytes(edit_file._data)
    before_count = edit_file.player_count
    offset_fields = (
        "player_start",
        "team_start",
        "manager_start",
        "competition_start",
        "stadium_start",
        "unknown_start",
        "team_player_start",
        "competition_entry_start",
        "game_plan_start",
    )
    before_offsets = tuple(getattr(edit_file, field) for field in offset_fields)
    monkeypatch.setattr(edit_file, "add_player", lambda *args, **kwargs: False)

    with pytest.raises(PlayerSpecError, match="could not register"):
        apply_create(edit_file, dastan_spec(tmp_path), {})

    assert bytes(edit_file._data) == before
    assert edit_file.player_count == before_count
    assert struct.unpack_from("<H", edit_file._data, HDR_PLAYER_COUNT)[0] == before_count
    assert tuple(getattr(edit_file, field) for field in offset_fields) == before_offsets


def test_create_allows_an_unused_id_below_existing_created_ids(tmp_path):
    from editor.editfile import PLAYER_ENTRY_SIZE
    from editor.player_spec import apply_create

    edit_file = make_player_spec_edit_file(roster_size=39)
    struct.pack_into("<I", edit_file._data, edit_file.player_start, 200001)
    struct.pack_into("<I", edit_file._data, edit_file.player_start + 4, 200001)
    struct.pack_into(
        "<I",
        edit_file._data,
        edit_file.player_start + PLAYER_ENTRY_SIZE,
        200001,
    )
    spec = dastan_spec(tmp_path)

    result = apply_create(edit_file, spec, {})

    assert result.status == "created"
    assert edit_file.player_count == 2
    assert edit_file.get_player_ability_profile(200000) is not None


def test_create_rejects_id_collision_with_different_normalized_identity(tmp_path):
    from editor.models import PlayerInfo
    from editor.player_spec import assess_create

    edit_file = make_player_spec_edit_file(roster_size=39)
    spec = dastan_spec(tmp_path)
    occupied = PlayerInfo(
        player_id=spec.identity.pes_id,
        name="Another Player",
        print_name=spec.identity.print_name or "",
    )

    result = assess_create(edit_file, spec, {occupied.player_id: occupied})

    assert (result.status, result.reason) == (
        "rejected",
        "pes_id_identity_mismatch",
    )


def test_create_rejects_destination_team_name_mismatch(tmp_path):
    from editor.editfile import TE_TEAM_NAME
    from editor.models import PlayerInfo
    from editor.player_spec import assess_create

    edit_file = make_player_spec_edit_file(roster_size=39)
    edit_file._data[
        edit_file.team_start + TE_TEAM_NAME : edit_file.team_start + TE_TEAM_NAME + 11
    ] = b"Arsenal FC\0"

    spec = dastan_spec(tmp_path)
    existing = PlayerInfo(
        player_id=spec.identity.pes_id,
        name=spec.identity.name,
    )
    result = assess_create(edit_file, spec, {existing.player_id: existing})

    assert (result.status, result.reason) == (
        "rejected",
        "destination_team_name_mismatch",
    )


def test_inactive_create_returns_before_identity_or_edit_access(tmp_path, monkeypatch):
    from dataclasses import replace

    from editor.player_spec import assess_create

    edit_file = make_player_spec_edit_file(roster_size=39)
    spec = replace(
        dastan_spec(tmp_path),
        lifecycle_status="retired",
        lifecycle_reason="Historical record",
    )
    monkeypatch.setattr(
        edit_file,
        "get_all_team_info",
        lambda: pytest.fail("inactive create must not inspect teams"),
    )
    monkeypatch.setattr(
        edit_file,
        "get_team_roster",
        lambda *args: pytest.fail("inactive create must not inspect rosters"),
    )

    class InaccessiblePlayers(dict):
        def get(self, *args, **kwargs):
            pytest.fail("inactive create must not inspect identities")

        def values(self):
            pytest.fail("inactive create must not inspect identities")

    result = assess_create(edit_file, spec, InaccessiblePlayers())

    assert (result.status, result.reason) == ("retired", "Historical record")



def test_assess_update_reports_applicable_current_state_without_mutation(tmp_path):
    from editor.player_spec import assess_update

    edit_file = make_player_spec_edit_file_with_palestra()
    before = bytes(edit_file._data)

    result = assess_update(edit_file, marco_spec(tmp_path), current_players(edit_file))

    assert result.status == "ready"
    assert bytes(edit_file._data) == before


def test_update_applies_only_when_all_current_values_match(tmp_path):
    from editor.editfile import PLAYER_ENTRY_SIZE
    from editor.player_codec import decode_player_entry
    from editor.player_spec import apply_update

    edit_file = make_player_spec_edit_file_with_palestra()
    spec = marco_spec(tmp_path)
    before = edit_file.get_edited_player_entry(162196)
    appearance_before = bytes(edit_file._data[PLAYER_ENTRY_SIZE:])

    result = apply_update(edit_file, spec, current_players(edit_file))
    after = edit_file.get_edited_player_entry(162196)

    assert result.status == "updated"
    assert before is not None
    assert after is not None
    profile = decode_player_entry(after)
    assert {
        field: profile.abilities[field] for field in spec.patches
    } == {
        "speed": 80,
        "acceleration": 77,
        "defensive_awareness": 62,
        "ball_winning": 60,
    }
    assert unchanged_bits(before, after, changed_fields=spec.patches)
    assert bytes(edit_file._data[PLAYER_ENTRY_SIZE:]) == appearance_before


def test_update_all_target_values_are_already_applied_without_mutation(tmp_path):
    from editor.player_spec import apply_update

    edit_file = make_player_spec_edit_file_with_palestra(
        speed=80,
        acceleration=77,
        defensive_awareness=62,
        ball_winning=60,
    )
    before = bytes(edit_file._data)

    result = apply_update(
        edit_file,
        marco_spec(tmp_path),
        current_players(edit_file),
    )

    assert result.status == "already_applied"
    assert bytes(edit_file._data) == before


@pytest.mark.parametrize(
    ("field", "current", "target", "required_marker"),
    [
        ("speed", 77, 80, "edited_abilities"),
        ("nationality_id", 215, 216, "edited_basic_settings"),
        ("skill_scissors_feint", 0, 1, "edited_skills"),
    ],
)
def test_update_all_target_values_restore_missing_required_marker(
    tmp_path,
    field,
    current,
    target,
    required_marker,
):
    from dataclasses import replace

    from editor.player_codec import FIELD_SPECS, _read_field, patch_player_entry
    from editor.player_spec import FieldPatch, apply_update

    spec = replace(
        marco_spec(tmp_path),
        patches={field: FieldPatch(current=current, target=target)},
    )
    edit_file = make_player_spec_edit_file_with_palestra(**{field: target})
    entry = edit_file.get_edited_player_entry(162196)
    assert entry is not None
    edit_file.replace_edited_player_entry(
        162196,
        patch_player_entry(entry, {required_marker: 0}),
    )

    result = apply_update(edit_file, spec, current_players(edit_file))

    assert result.status == "updated"
    updated_entry = edit_file.get_edited_player_entry(162196)
    assert updated_entry is not None
    assert _read_field(updated_entry, FIELD_SPECS[field]) == target
    assert _read_field(updated_entry, FIELD_SPECS[required_marker]) == 1

    before_second_run = bytes(edit_file._data)
    second_result = apply_update(edit_file, spec, current_players(edit_file))
    assert second_result.status == "already_applied"
    assert bytes(edit_file._data) == before_second_run


def test_update_mixed_current_and_target_values_conflict_without_mutation(tmp_path):
    from editor.player_spec import apply_update

    edit_file = make_player_spec_edit_file_with_palestra(speed=80)
    before = bytes(edit_file._data)

    result = apply_update(
        edit_file,
        marco_spec(tmp_path),
        current_players(edit_file),
    )

    assert result.status == "conflict"
    assert bytes(edit_file._data) == before


def test_update_third_value_conflicts_without_mutation(tmp_path):
    from editor.player_spec import apply_update

    edit_file = make_player_spec_edit_file_with_palestra(speed=79)
    before = bytes(edit_file._data)

    result = apply_update(
        edit_file,
        marco_spec(tmp_path),
        current_players(edit_file),
    )

    assert result.status == "conflict"
    assert bytes(edit_file._data) == before


def test_update_identity_mismatch_is_rejected_without_mutation(tmp_path):
    from editor.models import PlayerInfo
    from editor.player_spec import apply_update

    edit_file = make_player_spec_edit_file_with_palestra()
    before = bytes(edit_file._data)
    different_player = PlayerInfo(player_id=162196, name="Different Player")

    result = apply_update(
        edit_file,
        marco_spec(tmp_path),
        {different_player.player_id: different_player},
    )

    assert result.status == "rejected"
    assert bytes(edit_file._data) == before


def test_update_catalog_only_player_is_rejected_without_synthesizing_record(tmp_path):
    from editor.editfile import EditFile
    from editor.models import PlayerInfo
    from editor.player_spec import apply_update

    edit_file = EditFile()
    edit_file._data = bytearray(b"catalog-only")
    edit_file.player_start = 0
    edit_file.player_count = 0
    before = bytes(edit_file._data)
    catalog_player = PlayerInfo(player_id=162196, name="Marco Palestra")

    result = apply_update(
        edit_file,
        marco_spec(tmp_path),
        {catalog_player.player_id: catalog_player},
    )

    assert result.status == "rejected"
    assert bytes(edit_file._data) == before
    assert edit_file.player_count == 0


@pytest.mark.parametrize(
    ("field", "edited_flag"),
    [
        ("nationality_id", "edited_basic_settings"),
        ("registered_position", "edited_registered_position"),
        ("position_rb", "edited_playable_positions"),
        ("playing_style", "edited_playing_style"),
        ("skill_scissors_feint", "edited_skills"),
        ("com_style_trickster", "edited_com_styles"),
    ],
)
def test_update_activates_the_matching_edit_category(
    tmp_path,
    field,
    edited_flag,
):
    from dataclasses import replace

    from editor.player_codec import FIELD_SPECS, _read_field
    from editor.player_spec import FieldPatch, apply_update

    current = _read_field(PALESTRA_ENTRY, FIELD_SPECS[field])
    target = (current + 1) % (1 << FIELD_SPECS[field].width)
    spec = replace(
        marco_spec(tmp_path),
        patches={field: FieldPatch(current=current, target=target)},
    )
    edit_file = make_player_spec_edit_file_with_palestra()

    result = apply_update(edit_file, spec, current_players(edit_file))

    assert result.status == "updated"
    entry = edit_file.get_edited_player_entry(162196)
    assert entry is not None
    assert _read_field(entry, FIELD_SPECS[field]) == target
    assert _read_field(entry, FIELD_SPECS[edited_flag]) == 1


def test_new_base_revision_skips_old_spec_before_mutation(tmp_path, monkeypatch):
    from editor.player_spec import apply_player_spec

    edit_file = make_player_spec_edit_file_with_palestra()
    before = bytes(edit_file._data)
    monkeypatch.setattr(
        edit_file,
        "get_edited_player_entry",
        lambda *args: pytest.fail("incompatible specs must not inspect save records"),
    )

    class InaccessiblePlayers(dict):
        def get(self, *args, **kwargs):
            pytest.fail("incompatible specs must not inspect save identities")

    result = apply_player_spec(
        edit_file,
        marco_spec(tmp_path),
        "fl26-u2.3",
        InaccessiblePlayers(),
    )

    assert (result.status, result.reason) == (
        "needs_review",
        "base_revision_not_reviewed",
    )
    assert bytes(edit_file._data) == before


@pytest.mark.parametrize(
    ("lifecycle_status", "lifecycle_reason"),
    [
        ("upstreamed", "Included by the upstream database"),
        ("retired", "Historical record only"),
    ],
)
def test_inactive_lifecycle_is_reported_before_revision_or_save_access(
    tmp_path,
    monkeypatch,
    lifecycle_status,
    lifecycle_reason,
):
    from dataclasses import replace

    from editor.player_spec import apply_player_spec

    edit_file = make_player_spec_edit_file_with_palestra()
    spec = replace(
        marco_spec(tmp_path),
        lifecycle_status=lifecycle_status,
        lifecycle_reason=lifecycle_reason,
    )
    monkeypatch.setattr(
        edit_file,
        "get_edited_player_entry",
        lambda *args: pytest.fail("inactive specs must not inspect save records"),
    )

    class InaccessiblePlayers(dict):
        def get(self, *args, **kwargs):
            pytest.fail("inactive specs must not inspect save identities")

    result = apply_player_spec(
        edit_file,
        spec,
        "unreviewed-revision",
        InaccessiblePlayers(),
    )

    assert (result.status, result.reason) == (
        lifecycle_status,
        lifecycle_reason,
    )


def test_waiting_create_does_not_block_valid_update(tmp_path):
    from editor.player_spec import apply_player_specs

    edit_file = make_combined_fixture(chelsea_roster_size=40)
    results = apply_player_specs(
        edit_file,
        (dastan_spec(tmp_path), marco_spec(tmp_path)),
        REVISION,
        current_players(edit_file),
    )

    assert [(result.name, result.status) for result in results] == [
        ("Dastan Satpaev", "waiting"),
        ("Marco Palestra", "updated"),
    ]
    assert edit_file.get_player_ability_profile(162196).abilities["speed"] == 80

def test_update_before_eligible_create_keeps_cache_mapping_safe(tmp_path):
    from dataclasses import replace

    from editor.player_spec import apply_player_specs

    edit_file = make_combined_fixture(chelsea_roster_size=39)
    players = current_players(edit_file)
    edit_file._player_cache = players
    update = replace(marco_spec(tmp_path), path=tmp_path / "a-update.json")
    create = replace(dastan_spec(tmp_path), path=tmp_path / "z-create.json")

    results = apply_player_specs(
        edit_file,
        (create, update),
        REVISION,
        players,
    )

    assert [(result.name, result.status) for result in results] == [
        ("Marco Palestra", "updated"),
        ("Dastan Satpaev", "created"),
    ]
    assert all(result.diagnostic is None for result in results)
    assert edit_file.get_all_players(include_base_db=False)[200000].name == (
        "Dastan Satpaev"
    )



def test_conflict_does_not_block_independent_waiting_spec_and_order_is_deterministic(
    tmp_path,
):
    from editor.player_spec import apply_player_specs

    edit_file = make_combined_fixture(chelsea_roster_size=40, speed=79)
    before = bytes(edit_file._data)
    results = apply_player_specs(
        edit_file,
        (marco_spec(tmp_path), dastan_spec(tmp_path)),
        REVISION,
        current_players(edit_file),
    )

    assert [(result.name, result.status) for result in results] == [
        ("Dastan Satpaev", "waiting"),
        ("Marco Palestra", "conflict"),
    ]
    assert bytes(edit_file._data) == before


def test_created_player_is_visible_to_later_identity_assessment(tmp_path):
    from dataclasses import replace

    from editor.player_spec import apply_player_specs

    first = dastan_spec(tmp_path)
    second = replace(
        first,
        path=tmp_path / "z-satpaev.json",
        identity=replace(
            first.identity,
            name="SATPAEV",
            print_name="OTHER",
            aliases=("Different Prospect",),
            pes_id=200001,
            pes_retro_stats_id="f77d9c28-8f02-4dbe-b877-4c13724a4886",
        ),
        create=replace(
            first.create,
            player_id=200001,
            name="SATPAEV",
            print_name="OTHER",
            preferred_shirt_number=37,
        ),
    )
    edit_file = make_player_spec_edit_file(roster_size=38)
    results = apply_player_specs(
        edit_file,
        (second, first),
        REVISION,
        current_players(edit_file),
    )

    assert [(result.status, result.reason, result.pes_id) for result in results] == [
        ("created", "created_and_registered", 200000),
        ("already_applied", "matching_identity_exists", 200000),
    ]
    assert len(edit_file.get_team_roster(102).roster) == 39
    assert edit_file.get_all_players(include_base_db=False).get(200001) is None


@pytest.mark.parametrize(
    ("failure_mode", "expected_reason"),
    [
        ("rejected", "backend_rejected"),
        ("exception", "mutation_failed"),
    ],
)
def test_failed_mutation_rolls_back_only_that_spec(
    tmp_path,
    monkeypatch,
    failure_mode,
    expected_reason,
):
    import editor.player_spec as player_spec
    from editor.editfile import HEADER_SIZE

    edit_file = make_combined_fixture(chelsea_roster_size=39)
    marco_before = edit_file.get_edited_player_entry(162196)

    def fail_update(mutating_file, spec, all_players):
        mutating_file._data[HEADER_SIZE] ^= 0xFF
        if failure_mode == "exception":
            raise RuntimeError("simulated backend failure")
        return player_spec.SpecResult(
            pes_id=spec.identity.pes_id,
            name=spec.identity.name,
            status="rejected",
            reason="backend_rejected",
        )

    monkeypatch.setattr(player_spec, "apply_update", fail_update)
    results = player_spec.apply_player_specs(
        edit_file,
        (marco_spec(tmp_path), dastan_spec(tmp_path)),
        REVISION,
        current_players(edit_file),
    )

    assert [(result.name, result.status, result.reason) for result in results] == [
        ("Dastan Satpaev", "created", "created_and_registered"),
        ("Marco Palestra", "rejected", expected_reason),
    ]
    if failure_mode == "exception":
        assert results[1].diagnostic == (
            "RuntimeError: simulated backend failure"
        )
    else:
        assert results[1].diagnostic is None
    assert edit_file.get_edited_player_entry(162196) == marco_before
    assert edit_file.get_team_roster(102).player_index(200000) != -1
    assert edit_file.get_all_players(include_base_db=False)[200000].name == (
        "Dastan Satpaev"
    )


def test_batch_is_a_byte_for_byte_noop_when_no_spec_changes(tmp_path):
    from editor.player_spec import apply_player_specs

    edit_file = make_combined_fixture(
        chelsea_roster_size=40,
        speed=80,
        acceleration=77,
        defensive_awareness=62,
        ball_winning=60,
    )
    before = bytes(edit_file._data)
    results = apply_player_specs(
        edit_file,
        (marco_spec(tmp_path), dastan_spec(tmp_path)),
        REVISION,
        current_players(edit_file),
    )

    assert [(result.name, result.status) for result in results] == [
        ("Dastan Satpaev", "waiting"),
        ("Marco Palestra", "already_applied"),
    ]
    assert bytes(edit_file._data) == before