"""Integration-style coverage for the scrape-to-roster planning pipeline."""

from argparse import Namespace
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace


import pytest

from editor.models import TeamData
from scraper.models import MatchedTransfer, Transfer


def test_transfer_run_never_loads_or_applies_player_specs(
    monkeypatch, tmp_path, capsys
):
    import run

    edit_path = tmp_path / "EDIT00000000"
    edit_path.write_bytes(b"encrypted-edit")
    monkeypatch.setattr(run, "_scrape_run_transfers", lambda _args: [])
    monkeypatch.setattr(
        run,
        "load_player_specs",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("implicit player specs")
        ),
        raising=False,
    )
    monkeypatch.setattr(
        run.crypto,
        "decrypt",
        lambda _path: (_ for _ in ()).throw(
            AssertionError("no-transfer run must not decrypt solely for player specs")
        ),
    )

    run.cmd_run(
        Namespace(
            dry_run=False,
            edit_file=str(edit_path),
            output=None,
            threshold=80,
            in_place=True,
            from_base=False,
            allow_overflow_release=False,
        )
    )

    assert "No verified transfers found. Nothing to apply." in capsys.readouterr().out

    monkeypatch.setattr(run.sys, "argv", ["run.py", "run", "--help"])
    try:
        run.main()
    except SystemExit as exc:
        assert exc.code == 0
    else:
        raise AssertionError("run --help must exit after rendering help")
    help_output = capsys.readouterr().out
    assert "--player-spec" not in help_output
    assert "players apply" not in help_output


def test_players_help_uses_player_update_language(monkeypatch, capsys):
    import run

    monkeypatch.setattr(run.sys, "argv", ["run.py", "players", "--help"])
    with pytest.raises(SystemExit) as exc_info:
        run.main()

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert "Validate or apply revision-scoped Player Updates" in output
    assert "Validate Player Updates against the pristine base" in output
    assert "Generate a reviewable Pes Retro Stats proposal from an issue event" in output
    assert "Apply reviewed Player Updates to an EDIT file" in output


def test_cmd_run_dry_run_resolves_stale_loan_chain(monkeypatch, tmp_path, capsys):
    import run

    psg, tottenham, juventus = 114, 179, 120
    player_id = 115254
    edit_path = tmp_path / "EDIT00000000"
    edit_path.write_bytes(b"test")
    decrypted = tmp_path / "decrypted"
    decrypted.mkdir()
    (decrypted / "data.dat").write_bytes(b"test")

    transfers = [
        Transfer(
            "Randal Kolo Muani",
            "PSG",
            "Tottenham",
            date="2025-09-01T19:27:00Z",
            transfer_type="loan",
            is_loan=True,
            from_club_full_name="Paris Saint-Germain",
            to_club_full_name="Tottenham Hotspur",
        ),
        Transfer(
            "Randal Kolo Muani",
            "PSG",
            "Juventus",
            date="2026-08-02T18:40:10Z",
            from_club_full_name="Paris Saint-Germain",
            to_club_full_name="Juventus FC",
        ),
    ]

    class FakeEditFile:
        def load(self, _):
            return None

        def validate_integrity(self):
            return {"valid": True, "errors": [], "warnings": [], "metrics": {}}

        def get_all_players(self):
            return {
                player_id: SimpleNamespace(
                    name="Randal Kolo Muani",
                    position="CF",
                    nationality="France",
                    age=27,
                )
            }

        def get_all_team_info(self):
            return {
                psg: SimpleNamespace(name="Paris Saint-Germain"),
                tottenham: SimpleNamespace(name="Tottenham Hotspur"),
                juventus: SimpleNamespace(name="Juventus FC"),
            }

        def get_club_team_ids(self):
            return {psg, tottenham, juventus}

        def get_all_rosters(self):
            return {
                psg: TeamData(psg, list(range(200001, 200017)) + [0] * 24),
                tottenham: TeamData(
                    tottenham,
                    [player_id] + list(range(300001, 300017)) + [0] * 23,
                ),
                juventus: TeamData(
                    juventus, list(range(400001, 400017)) + [0] * 24
                ),
            }

        def find_overflow_release_candidate(self, *_, **__):
            return 39, 0

        def get_player_shirt_number(self, *_):
            return None

    monkeypatch.setattr(run, "EditFile", FakeEditFile)
    monkeypatch.setattr(run.crypto, "decrypt", lambda _: decrypted)
    monkeypatch.setattr(run.crypto, "cleanup_temp", lambda _: None)
    monkeypatch.setattr(
        run,
        "fetch_transfers_for_club_names",
        lambda *_, **__: transfers,
    )
    monkeypatch.setattr(run.transfer_logger, "read_log", lambda *_, **__: [])

    run.cmd_run(Namespace(
        dry_run=True,
        edit_file=str(edit_path),
        threshold=80,
        output=None,
        in_place=False,
        popular=False,
        window="auto",
        since=None,
        club="Paris Saint-Germain,Tottenham Hotspur,Juventus",
        deep=False,
        allow_overflow_release=False,
    ))

    output = capsys.readouterr().out
    assert "ALREADY CURRENT" in output
    assert "PSG → Juventus" in output
    assert "WOULD MOVE" in output
    assert "safety-skipped: 0" in output


def test_scheduler_survives_fail_closed_run(monkeypatch, capsys):
    import run

    attempts = []

    def abort_once(_):
        attempts.append(1)
        raise SystemExit(2)

    monkeypatch.setattr(run, "cmd_run", abort_once)
    monkeypatch.setattr(
        run.time,
        "sleep",
        lambda _: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    run.cmd_schedule(Namespace(interval_hours=1))

    assert len(attempts) == 1
    assert "aborted safely" in capsys.readouterr().out


def test_real_run_skips_shirt_conflict_without_rolling_back(
    monkeypatch, tmp_path, capsys
):
    import run

    player_id, conflicting_player_id, team_id = 100527, 168639, 100
    edit_path = tmp_path / "EDIT00000000"
    output_path = tmp_path / "updated" / "EDIT00000000"
    edit_path.write_bytes(b"encrypted-edit")
    decrypted = tmp_path / "decrypted-real"
    decrypted.mkdir()
    (decrypted / "data.dat").write_bytes(b"decrypted-edit")

    transfer = Transfer(
        "Karl Darlow",
        "Manchester United",
        "Manchester United",
        transfer_type="shirt_number_update",
        shirt_number=12,
    )
    matched = MatchedTransfer(
        transfer=transfer,
        player_id=player_id,
        from_team_id=team_id,
        to_team_id=team_id,
        player_confidence=100,
        from_team_confidence=100,
        to_team_confidence=100,
        matched_player_name="Karl Darlow",
    )
    plan = run.PlannedRosterAction(matched, "shirt_update", team_id)

    class FakeEditFile:
        def __init__(self):
            self._data = bytearray(b"decrypted-edit")

        def load(self, _):
            return None

        def validate_integrity(self):
            return {"valid": True, "errors": [], "warnings": [], "metrics": {}}

        def get_player_shirt_number(self, requested_team, requested_player):
            assert (requested_team, requested_player) == (team_id, player_id)
            return 1

        def get_team_roster(self, requested_team):
            assert requested_team == team_id
            return TeamData(
                team_id,
                [player_id, conflicting_player_id] + [0] * 38,
                [1, 12] + [0] * 38,
            )

        def update_player_shirt_number(self, *_):
            raise AssertionError("known shirt conflicts must not reach mutation")

        def save(self, _):
            return None

    monkeypatch.setattr(run, "EditFile", FakeEditFile)
    monkeypatch.setattr(run, "_scrape_run_transfers", lambda _: [transfer])
    monkeypatch.setattr(run, "_load_match_database", lambda _: (None, {}, {}, {team_id}))
    monkeypatch.setattr(
        run,
        "_match_and_plan_transfers",
        lambda *_, **__: ([plan], [matched], str(output_path.resolve())),
    )
    monkeypatch.setattr(run.crypto, "decrypt", lambda _: decrypted)
    monkeypatch.setattr(run.crypto, "encrypt", lambda *_: None)
    monkeypatch.setattr(run.crypto, "cleanup_temp", lambda _: None)
    monkeypatch.setattr(run.backup_mod, "create_backup", lambda _: tmp_path / "backup")
    monkeypatch.setattr(run.transfer_logger, "save_reports", lambda _: None)

    run.cmd_run(Namespace(
        dry_run=False,
        edit_file=str(edit_path),
        output=str(output_path),
        threshold=80,
        in_place=False,
        from_base=False,
        allow_overflow_release=False,
    ))

    output = capsys.readouterr().out
    assert "Safety skip Karl Darlow" in output
    assert "shirt #12 is already assigned" in output
    assert "entire batch rolled back" not in output
    assert "✅ Done!" in output









def test_players_validate_rejects_wrong_pristine_base_digest_before_decrypt(
    monkeypatch, tmp_path
):
    import run

    base = tmp_path / "EDIT00000000"
    base.write_bytes(b"wrong")
    monkeypatch.setattr(run.config, "BASE_EDIT_PATH", base, raising=False)
    monkeypatch.setattr(
        run.crypto,
        "decrypt",
        lambda _path: (_ for _ in ()).throw(
            AssertionError("digest mismatch must fail before decrypt")
        ),
    )

    try:
        run.cmd_players_validate(Namespace())
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("wrong pristine-base digest must exit")


def _prepare_players_validate_result(
    monkeypatch,
    tmp_path,
    *,
    result_status,
    result_reason,
    lifecycle_status="active",
    applies_to=("expected-revision",),
):
    import run
    from editor.player_spec import BaseManifest, SpecResult

    base = tmp_path / "EDIT00000000"
    base.write_bytes(b"pristine")
    decrypted = tmp_path / "decrypted-validate"
    decrypted.mkdir()
    (decrypted / "data.dat").write_bytes(b"decrypted")
    spec = SimpleNamespace(
        lifecycle_status=lifecycle_status,
        operation="update",
        applies_to=applies_to,
    )

    class FakeEditFile:
        def load(self, _path):
            return None

        def validate_integrity(self):
            return {"valid": True, "errors": [], "warnings": [], "metrics": {}}

    monkeypatch.setattr(run.config, "BASE_EDIT_PATH", base, raising=False)
    monkeypatch.setattr(
        run,
        "verify_base_file",
        lambda path: (
            BaseManifest("expected-revision", "irrelevant")
            if path == base
            else (_ for _ in ()).throw(AssertionError("unexpected base path"))
        ),
    )
    monkeypatch.setattr(
        run,
        "load_player_specs",
        lambda *_args, **_kwargs: (spec,),
    )
    monkeypatch.setattr(run, "validate_spec_set", lambda _specs: None)
    monkeypatch.setattr(
        run,
        "_assess_player_specs",
        lambda *_args: (
            SpecResult(162196, "Marco Palestra", result_status, result_reason),
        ),
    )
    monkeypatch.setattr(run, "EditFile", FakeEditFile)
    monkeypatch.setattr(run.crypto, "decrypt", lambda _path: decrypted)
    monkeypatch.setattr(run.crypto, "cleanup_temp", lambda _path: None)
    return run.cmd_players_validate


def _materialize_generated_proposal_validation_fixture(
    tmp_path: Path,
    *,
    operation: str = "create",
) -> dict[str, object]:
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
        payload = build_player_draft(
            parse_player_issue_event(issue_event()),
            source,
            proposal,
            **build_create_kwargs(proposal),
        )
        filename = "dastan-satpaev.json"
    elif operation == "update":
        source = marco_source()
        proposal = proposal_for(source)
        payload = build_player_draft(
            update_request(),
            source,
            proposal,
            edit_file=FakeEditFile(proposal),
        )
        filename = "marco-palestra.json"
    else:
        raise AssertionError(f"unsupported test proposal operation: {operation}")

    proposal_dir = tmp_path / "players"
    proposal_dir.mkdir()
    proposal_path = proposal_dir / filename
    proposal_path.write_text(json.dumps(payload), encoding="utf-8")

    base = tmp_path / "EDIT00000000"
    base.write_bytes(b"pristine")
    decrypted = tmp_path / "decrypted-proposal"
    decrypted.mkdir()
    (decrypted / "data.dat").write_bytes(b"decrypted")

    class ValidatedFakeEditFile(FakeEditFile):
        def load(self, path: Path) -> None:
            self.loaded_path = Path(path)

        def validate_integrity(self):
            return {"valid": True, "errors": [], "warnings": [], "metrics": {}}

    return {
        "payload": payload,
        "proposal_dir": proposal_dir,
        "proposal_path": proposal_path,
        "base": base,
        "decrypted": decrypted,
        "edit_file": ValidatedFakeEditFile(proposal),
    }


def _configure_proposal_validation(monkeypatch, fixture: dict[str, object]) -> None:
    import run
    from editor.player_spec import BaseManifest

    base = fixture["base"]
    decrypted = fixture["decrypted"]
    assert isinstance(base, Path)
    assert isinstance(decrypted, Path)
    monkeypatch.setattr(run.config, "BASE_EDIT_PATH", base, raising=False)
    monkeypatch.setattr(
        run.config,
        "PLAYER_SPECS_DIR",
        fixture["proposal_dir"],
        raising=False,
    )
    monkeypatch.setattr(
        run,
        "verify_base_file",
        lambda path: (
            BaseManifest("fl26-u2.2-national-squads", "irrelevant")
            if path == base
            else (_ for _ in ()).throw(AssertionError("unexpected base path"))
        ),
    )
    monkeypatch.setattr(run, "EditFile", lambda: fixture["edit_file"])
    monkeypatch.setattr(run.crypto, "decrypt", lambda _path: decrypted)
    monkeypatch.setattr(run.crypto, "cleanup_temp", lambda _path: None)


def _forbid_profile_fetch(monkeypatch) -> None:
    async def fail_fetch(_url: str):
        pytest.fail("validation must not fetch")

    monkeypatch.setattr(
        "tools.generate_player_draft.fetch_pes_retro_stats_profile",
        fail_fetch,
    )


def test_cmd_players_validate_accepts_one_materialized_proposal_offline(
    monkeypatch,
    tmp_path,
    capsys,
):
    import run

    fixture = _materialize_generated_proposal_validation_fixture(tmp_path)
    _configure_proposal_validation(monkeypatch, fixture)
    _forbid_profile_fetch(monkeypatch)

    run.cmd_players_validate(Namespace())

    output = capsys.readouterr().out
    assert "Dastan Satpaev" in output
    assert "proposal_ready" in output



def test_cmd_players_validate_rejects_existing_identity_create_proposal(
    monkeypatch,
    tmp_path,
    capsys,
):
    import run
    from editor.models import PlayerInfo

    fixture = _materialize_generated_proposal_validation_fixture(tmp_path)
    edit_file = fixture["edit_file"]
    edit_file.players[100] = PlayerInfo(100, "Dastan Satpaev", "D. Satpaev")
    _configure_proposal_validation(monkeypatch, fixture)
    _forbid_profile_fetch(monkeypatch)

    with pytest.raises(SystemExit) as exc_info:
        run.cmd_players_validate(Namespace())

    assert exc_info.value.code == 2
    output = capsys.readouterr().out
    assert "matching_identity_exists" in output
    assert "proposal_ready" not in output

def _tamper_source_field(payload: dict[str, object]) -> None:
    payload["source"]["data"]["current_club"] = "Inter Milan"


def _tamper_source_hash(payload: dict[str, object]) -> None:
    payload["source"]["snapshot_sha256"] = "0" * 64


def _tamper_converted_pes_field(payload: dict[str, object]) -> None:
    payload["pes"]["abilities"]["speed"] += 1


def _tamper_allocated_id(payload: dict[str, object]) -> None:
    payload["identity"]["pes_id"] += 1


def _tamper_update_patch_baseline(payload: dict[str, object]) -> None:
    payload["pes"]["abilities"]["speed"]["from"] -= 1


def _tamper_ovr_value(payload: dict[str, object]) -> None:
    payload["draft"]["ovr_review"]["positions"][0]["proposal_tenths"] += 1


def _tamper_canonical_order(payload: dict[str, object]) -> None:
    positions = payload["draft"]["ovr_review"]["positions"]
    payload["draft"]["ovr_review"]["positions"] = list(reversed(positions))


@pytest.mark.parametrize(
    ("operation", "mutate", "json_path"),
    [
        pytest.param(
            "create",
            _tamper_source_field,
            "source.data.current_club",
            id="source-field",
        ),
        pytest.param(
            "create",
            _tamper_source_hash,
            "source.snapshot_sha256",
            id="source-hash",
        ),
        pytest.param(
            "create",
            _tamper_converted_pes_field,
            "pes.abilities.speed",
            id="converted-pes-field",
        ),
        pytest.param(
            "create",
            _tamper_allocated_id,
            "identity.pes_id",
            id="allocated-id",
        ),
        pytest.param(
            "update",
            _tamper_update_patch_baseline,
            "pes.abilities.speed.from",
            id="update-patch-baseline",
        ),
        pytest.param(
            "create",
            _tamper_ovr_value,
            "draft.ovr_review.positions[0].proposal_tenths",
            id="ovr-value",
        ),
        pytest.param(
            "create",
            _tamper_canonical_order,
            "draft.ovr_review.positions",
            id="canonical-order",
        ),
    ],
)
def test_cmd_players_validate_rejects_tampered_proposal_offline(
    monkeypatch,
    tmp_path,
    capsys,
    operation,
    mutate,
    json_path,
):
    import run

    fixture = _materialize_generated_proposal_validation_fixture(
        tmp_path,
        operation=operation,
    )
    payload = deepcopy(fixture["payload"])
    mutate(payload)
    proposal_path = fixture["proposal_path"]
    assert isinstance(proposal_path, Path)
    proposal_path.write_text(json.dumps(payload), encoding="utf-8")
    _configure_proposal_validation(monkeypatch, fixture)
    _forbid_profile_fetch(monkeypatch)

    with pytest.raises(SystemExit) as exc_info:
        run.cmd_players_validate(Namespace())

    assert exc_info.value.code == 2
    output = capsys.readouterr().out
    assert json_path in output



@pytest.mark.parametrize(
    ("result_status", "result_reason"),
    [
        ("already_applied", "matching_player_exists"),
        ("conflict", "mixed_or_unexpected_values"),
        ("rejected", "pes_id_missing"),
    ],
)
def test_players_validate_rejects_invalid_current_active_state(
    monkeypatch,
    tmp_path,
    capsys,
    result_status,
    result_reason,
):
    command = _prepare_players_validate_result(
        monkeypatch,
        tmp_path,
        result_status=result_status,
        result_reason=result_reason,
    )

    with pytest.raises(SystemExit) as exc_info:
        command(Namespace())

    assert exc_info.value.code == 2
    output = capsys.readouterr().out
    assert result_status in output
    assert "Player Update validation failed" in output


@pytest.mark.parametrize(
    ("result_status", "result_reason", "lifecycle_status", "applies_to"),
    [
        ("ready", "eligible", "active", ("expected-revision",)),
        ("waiting", "destination_roster_full", "active", ("expected-revision",)),
        ("needs_review", "base_revision_not_reviewed", "active", ("old-revision",)),
        ("upstreamed", "included upstream", "upstreamed", ("expected-revision",)),
        ("retired", "historical record", "retired", ("expected-revision",)),
    ],
)
def test_players_validate_permits_applicable_and_history_states(
    monkeypatch,
    tmp_path,
    result_status,
    result_reason,
    lifecycle_status,
    applies_to,
):
    command = _prepare_players_validate_result(
        monkeypatch,
        tmp_path,
        result_status=result_status,
        result_reason=result_reason,
        lifecycle_status=lifecycle_status,
        applies_to=applies_to,
    )

    command(Namespace())


def test_players_parser_dispatches_nested_apply(monkeypatch):
    import run

    dispatched = []
    monkeypatch.setattr(run, "cmd_players_apply", lambda args: dispatched.append(args))
    monkeypatch.setattr(
        run.sys,
        "argv",
        [
            "run.py",
            "players",
            "apply",
            "--base-revision",
            "revision-1",
            "--edit-file",
            "source",
            "--output",
            "destination",
        ],
    )

    run.main()

    assert len(dispatched) == 1
    assert dispatched[0].base_revision == "revision-1"
    assert dispatched[0].edit_file == "source"
    assert dispatched[0].output == "destination"
    assert dispatched[0].in_place is False


def test_players_apply_rejects_wrong_revision_before_decrypt(monkeypatch, tmp_path):
    import run
    from editor.player_spec import BaseManifest

    source = tmp_path / "EDIT00000000"
    source.write_bytes(b"encrypted")
    monkeypatch.setattr(
        run,
        "load_base_manifest",
        lambda: BaseManifest("expected-revision", "0" * 64),
    )
    monkeypatch.setattr(
        run.crypto,
        "decrypt",
        lambda _path: (_ for _ in ()).throw(
            AssertionError("revision mismatch must fail before decrypt")
        ),
    )

    try:
        run.cmd_players_apply(
            Namespace(
                edit_file=str(source),
                output=str(tmp_path / "updated"),
                in_place=False,
                base_revision="wrong-revision",
            )
        )
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("wrong base revision must exit")


def test_players_apply_no_change_writes_nothing(monkeypatch, tmp_path, capsys):
    import run
    from editor.player_spec import BaseManifest, SpecResult

    source = tmp_path / "EDIT00000000"
    output = tmp_path / "updated"
    source.write_bytes(b"encrypted")
    decrypted = tmp_path / "decrypted"
    decrypted.mkdir()
    (decrypted / "data.dat").write_bytes(b"decrypted")

    class FakeEditFile:
        def load(self, _path):
            return None

        def get_all_players(self, include_base_db=True):
            return {}

        def validate_integrity(self):
            return {"valid": True, "errors": [], "warnings": [], "metrics": {}}

    monkeypatch.setattr(run, "EditFile", FakeEditFile)
    monkeypatch.setattr(
        run,
        "load_base_manifest",
        lambda: BaseManifest("expected-revision", "0" * 64),
    )
    monkeypatch.setattr(run, "load_player_specs", lambda: ())
    monkeypatch.setattr(
        run,
        "apply_player_specs",
        lambda *_args: (
            SpecResult(162196, "Marco Palestra", "needs_review", "base_revision_not_reviewed"),
        ),
    )
    monkeypatch.setattr(run.crypto, "decrypt", lambda _path: decrypted)
    monkeypatch.setattr(run.crypto, "cleanup_temp", lambda _path: None)
    monkeypatch.setattr(
        run.backup_mod,
        "create_backup",
        lambda _path: (_ for _ in ()).throw(
            AssertionError("no-change batches must not create backups")
        ),
    )
    monkeypatch.setattr(
        run.crypto,
        "encrypt",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("no-change batches must not write output")
        ),
    )

    run.cmd_players_apply(
        Namespace(
            edit_file=str(source),
            output=str(output),
            in_place=False,
            base_revision="expected-revision",
        )
    )

    assert output.exists() is False
    assert source.read_bytes() == b"encrypted"
    assert "needs_review" in capsys.readouterr().out


def _prepare_players_apply_results(monkeypatch, tmp_path, results):
    import run
    from editor.player_spec import BaseManifest, load_player_specs

    calls = []
    source = tmp_path / "EDIT00000000"
    output = tmp_path / "updated"
    source.write_bytes(b"encrypted")
    decrypted = tmp_path / "decrypted-apply-results"
    decrypted.mkdir()
    (decrypted / "data.dat").write_bytes(b"decrypted")
    result_ids = {result.pes_id for result in results}
    specs = tuple(
        spec for spec in load_player_specs() if spec.identity.pes_id in result_ids
    )

    class FakeEditFile:
        def load(self, _path):
            return None

        def get_all_players(self, include_base_db=True):
            return {}

        def validate_integrity(self):
            calls.append("validate")
            return {"valid": True, "errors": [], "warnings": [], "metrics": {}}

        def save(self, _path):
            calls.append("save")

    def encrypt(_decrypted, destination):
        calls.append("encrypt")
        destination.write_bytes(b"encrypted-output")

    monkeypatch.setattr(run, "EditFile", FakeEditFile)
    monkeypatch.setattr(
        run,
        "load_base_manifest",
        lambda: BaseManifest("expected-revision", "0" * 64),
    )
    monkeypatch.setattr(run, "load_player_specs", lambda: specs)
    monkeypatch.setattr(run, "validate_spec_set", lambda _specs: None)
    monkeypatch.setattr(run, "apply_player_specs", lambda *_args: results)
    monkeypatch.setattr(run.crypto, "decrypt", lambda _path: decrypted)
    monkeypatch.setattr(run.crypto, "encrypt", encrypt)
    monkeypatch.setattr(run.crypto, "cleanup_temp", lambda _path: None)
    monkeypatch.setattr(
        run.backup_mod,
        "create_backup",
        lambda _path: calls.append("backup") or tmp_path / "backup",
    )
    monkeypatch.setattr(
        run,
        "_verify_player_spec_output",
        lambda _path: calls.append("verify"),
    )
    monkeypatch.setattr(
        run.transfer_logger,
        "log_transfer",
        lambda **_record: calls.append("audit"),
    )
    monkeypatch.setattr(run.transfer_logger, "read_log", lambda _scope: [])
    monkeypatch.setattr(
        run.transfer_logger,
        "save_reports",
        lambda _records: calls.append("report"),
    )

    def invoke():
        run.cmd_players_apply(
            Namespace(
                edit_file=str(source),
                output=str(output),
                in_place=False,
                base_revision="expected-revision",
            )
        )

    return invoke, calls, source, output


def test_players_apply_failure_only_batch_exits_nonzero_without_output(
    monkeypatch,
    tmp_path,
    capsys,
):
    from editor.player_spec import SpecResult

    invoke, calls, source, output = _prepare_players_apply_results(
        monkeypatch,
        tmp_path,
        (
            SpecResult(
                162196,
                "Marco Palestra",
                "rejected",
                "mutation_failed",
            ),
        ),
    )

    with pytest.raises(SystemExit) as exc_info:
        invoke()

    assert exc_info.value.code == 2
    assert calls == ["validate"]
    assert source.read_bytes() == b"encrypted"
    assert output.exists() is False
    assert "mutation_failed" in capsys.readouterr().out


def test_players_apply_mixed_success_and_mutation_failure_persists_verified_success(
    monkeypatch,
    tmp_path,
    capsys,
):
    from editor.player_spec import SpecResult

    invoke, calls, _source, output = _prepare_players_apply_results(
        monkeypatch,
        tmp_path,
        (
            SpecResult(162196, "Marco Palestra", "updated", "patched"),
            SpecResult(200000, "Dastan Satpaev", "rejected", "mutation_failed"),
        ),
    )

    with pytest.raises(SystemExit) as exc_info:
        invoke()

    assert exc_info.value.code == 2
    assert calls == [
        "validate",
        "validate",
        "backup",
        "save",
        "encrypt",
        "verify",
        "audit",
        "report",
    ]
    assert output.read_bytes() == b"encrypted-output"
    rendered = capsys.readouterr().out
    assert "mutation_failed" in rendered
    assert "Applied 1 Player Update" in rendered
    assert "player specs" not in rendered.lower()


def test_players_apply_audits_and_rebuilds_same_save_reports_after_roundtrip(
    monkeypatch, tmp_path
):
    import run
    from editor.player_spec import BaseManifest, SpecResult, load_player_specs

    calls = []
    source = tmp_path / "EDIT00000000"
    output = tmp_path / "updated"
    report_dir = tmp_path / "reports"
    save_scope = str(output.resolve())
    logged_records = [
        {
            "player_name": "Transfer Player",
            "player_id": 100001,
            "from_team": "Club A",
            "from_team_id": 1,
            "to_team": "Club B",
            "to_team_id": 2,
            "confidence": 100.0,
            "transfer_type": "transfer",
            "dry_run": False,
            "roster_action": "move",
            "save_scope": save_scope,
        }
    ]
    save_reports = run.transfer_logger.save_reports
    source.write_bytes(b"encrypted")
    input_dir = tmp_path / "decrypted-input"
    verify_dir = tmp_path / "decrypted-verify"
    input_dir.mkdir()
    verify_dir.mkdir()
    (input_dir / "data.dat").write_bytes(b"decrypted")
    (verify_dir / "data.dat").write_bytes(b"verified")
    marco = next(
        spec for spec in load_player_specs() if spec.identity.name == "Marco Palestra"
    )

    class FakeEditFile:
        def load(self, _path):
            calls.append("load")

        def get_all_players(self, include_base_db=True):
            return {}

        def validate_integrity(self):
            calls.append("validate")
            return {"valid": True, "errors": [], "warnings": [], "metrics": {}}

        def save(self, path):
            calls.append("save")
            path.write_bytes(b"changed")

    decrypt_count = 0

    def fake_decrypt(path):
        nonlocal decrypt_count
        decrypt_count += 1
        calls.append("decrypt-input" if decrypt_count == 1 else "decrypt-verify")
        return input_dir if decrypt_count == 1 else verify_dir

    def fake_encrypt(_source_path, output_path):
        calls.append("encrypt")
        output_path.write_bytes(b"encrypted-output")
    def fake_log_transfer(**record):
        calls.append(("audit", record))
        logged_records.append(record)

    def fake_read_log(requested_save_scope):
        assert requested_save_scope == save_scope
        calls.append(("read-log", requested_save_scope))
        return list(logged_records)

    def save_combined_reports(records):
        calls.append(("report", records))
        save_reports(
            records,
            output_dir=report_dir,
            write_github_summary=False,
        )


    monkeypatch.setattr(run, "EditFile", FakeEditFile)
    monkeypatch.setattr(
        run,
        "load_base_manifest",
        lambda: BaseManifest("expected-revision", "0" * 64),
    )
    monkeypatch.setattr(run, "load_player_specs", lambda: (marco,))
    monkeypatch.setattr(
        run,
        "apply_player_specs",
        lambda *_args: (
            SpecResult(162196, "Marco Palestra", "updated", "patched"),
        ),
    )
    monkeypatch.setattr(run.crypto, "decrypt", fake_decrypt)
    monkeypatch.setattr(run.crypto, "encrypt", fake_encrypt)
    monkeypatch.setattr(run.crypto, "cleanup_temp", lambda _path: None)
    monkeypatch.setattr(
        run.backup_mod, "create_backup", lambda _path: calls.append("backup")
    )
    monkeypatch.setattr(run.transfer_logger, "log_transfer", fake_log_transfer)
    monkeypatch.setattr(run.transfer_logger, "read_log", fake_read_log)
    monkeypatch.setattr(
        run.transfer_logger,
        "save_reports",
        save_combined_reports,
    )

    run.cmd_players_apply(
        Namespace(
            edit_file=str(source),
            output=str(output),
            in_place=False,
            base_revision="expected-revision",
        )
    )

    audit = next(
        call[1]
        for call in calls
        if isinstance(call, tuple) and call[0] == "audit"
    )
    assert audit["transfer_type"] == "player_spec_update"
    assert audit["pes_retro_stats_player_id"] == (
        "0ce2dbde-9cd9-423c-a90a-35b07df6a967"
    )
    assert "sortitoutsi_player_id" not in audit
    expected_fields = list(marco.patches)
    assert [change["field"] for change in audit["field_changes"]] == expected_fields
    changes_by_field = {
        change["field"]: (change["from"], change["to"])
        for change in audit["field_changes"]
    }
    assert changes_by_field["speed"] == (77, 90)
    assert changes_by_field["height"] == (180, 186)
    assert calls.index("save") < calls.index("encrypt")
    assert calls.index("encrypt") < calls.index("decrypt-verify")
    audit_index = next(index for index, call in enumerate(calls) if isinstance(call, tuple) and call[0] == "audit")
    report_index = next(index for index, call in enumerate(calls) if isinstance(call, tuple) and call[0] == "report")
    assert max(index for index, call in enumerate(calls) if call == "validate") < audit_index
    assert audit_index < report_index
    markdown = (report_dir / "transfer_summary.md").read_text(encoding="utf-8")
    html = (report_dir / "transfer_summary.html").read_text(encoding="utf-8")
    for report in (markdown, html):
        assert "Transfer Player" in report
        assert "Marco Palestra" in report


def _assert_players_apply_aborts_for_backup_race(
    monkeypatch, tmp_path, mutation_target
):
    import run
    from editor.player_spec import BaseManifest, SpecResult, load_player_specs

    calls = []
    source = tmp_path / "EDIT00000000"
    output = tmp_path / "updated"
    source.write_bytes(b"input-before")
    output.write_bytes(b"output-before")
    decrypted = tmp_path / "decrypted-race"
    decrypted.mkdir()
    (decrypted / "data.dat").write_bytes(b"decrypted")
    marco = next(
        spec for spec in load_player_specs() if spec.identity.name == "Marco Palestra"
    )

    class FakeEditFile:
        def load(self, _path):
            return None

        def get_all_players(self, include_base_db=True):
            return {}

        def validate_integrity(self):
            return {"valid": True, "errors": [], "warnings": [], "metrics": {}}

        def save(self, _path):
            calls.append("save")

    def mutate_during_backup(_path):
        calls.append("backup")
        target = source if mutation_target == "input" else output
        target.write_bytes(f"{mutation_target}-after".encode())
        return tmp_path / "backup"

    monkeypatch.setattr(run, "EditFile", FakeEditFile)
    monkeypatch.setattr(
        run,
        "load_base_manifest",
        lambda: BaseManifest("expected-revision", "0" * 64),
    )
    monkeypatch.setattr(run, "load_player_specs", lambda: (marco,))
    monkeypatch.setattr(
        run,
        "apply_player_specs",
        lambda *_args: (
            SpecResult(162196, "Marco Palestra", "updated", "patched"),
        ),
    )
    monkeypatch.setattr(run.crypto, "decrypt", lambda _path: decrypted)
    monkeypatch.setattr(
        run.crypto, "encrypt", lambda *_args: calls.append("encrypt")
    )
    monkeypatch.setattr(run.crypto, "cleanup_temp", lambda _path: None)
    monkeypatch.setattr(run.backup_mod, "create_backup", mutate_during_backup)
    monkeypatch.setattr(
        run.transfer_logger,
        "log_transfer",
        lambda **_record: calls.append("audit"),
    )
    monkeypatch.setattr(
        run.transfer_logger,
        "save_reports",
        lambda _records: calls.append("report"),
    )

    try:
        run.cmd_players_apply(
            Namespace(
                edit_file=str(source),
                output=str(output),
                in_place=False,
                base_revision="expected-revision",
            )
        )
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("backup-time concurrent replacement must abort apply")

    assert calls == ["backup"]


def test_players_apply_aborts_when_input_changes_during_backup(
    monkeypatch, tmp_path
):
    _assert_players_apply_aborts_for_backup_race(monkeypatch, tmp_path, "input")


def test_players_apply_aborts_when_existing_output_changes_during_backup(
    monkeypatch, tmp_path
):
    _assert_players_apply_aborts_for_backup_race(monkeypatch, tmp_path, "output")