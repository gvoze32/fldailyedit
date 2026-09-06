"""Integration-style coverage for the scrape-to-roster planning pipeline."""

from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace


import pytest
import run_pipeline

from editor.models import TeamData
from scraper.models import CaptainUpdate, MatchedTransfer, Transfer
from transfer_planning import PlannedRosterAction



def test_transfer_run_skips_save_work_when_no_transfers(
    monkeypatch, tmp_path, capsys
):
    import run

    edit_path = tmp_path / "EDIT00000000"
    edit_path.write_bytes(b"encrypted-edit")
    monkeypatch.setattr(run_pipeline, "_scrape_run_transfers", lambda _args: [])
    monkeypatch.setattr(
        run.crypto,
        "decrypt",
        lambda _path: (_ for _ in ()).throw(
            AssertionError("no-transfer run must not decrypt the save")
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

    monkeypatch.setattr(run.sys, "argv", ["run.py", "--help"])
    with pytest.raises(SystemExit) as exc:
        run.main()
    assert exc.value.code == 0
    assert "players" not in capsys.readouterr().out.lower()


def test_cmd_run_routes_through_shared_local_update_service(
    monkeypatch, tmp_path, capsys
):
    import run
    from local_update import LocalUpdateResult

    edit_path = tmp_path / "EDIT00000000"
    edit_path.write_bytes(b"encrypted-edit")
    requests = []

    class FakeService:
        def execute(self, request):
            requests.append(request)
            return LocalUpdateResult(
                target_path=edit_path,
                backup_path=tmp_path / "backup",
                installed_sha256="a" * 64,
                transfer_applied=2,
                shirt_numbers_changed=1,
                unchanged=3,
                safety_skipped=4,
                diagnostic="report warning",
            )

    monkeypatch.setattr(
        run_pipeline,
        "build_local_update_service",
        lambda: FakeService(),
        raising=False,
    )
    monkeypatch.setattr(run_pipeline, "_scrape_run_transfers", lambda _args: [])

    run.cmd_run(
        Namespace(
            dry_run=False,
            edit_file=str(edit_path),
            output=None,
            threshold=80,
            in_place=True,
            from_base=False,
            deep=True,
            window="auto",
            since=None,
            popular=False,
            fotmob_only=False,
            allow_overflow_release=False,
        )
    )

    assert len(requests) == 1
    assert requests[0].edit_path == edit_path
    assert requests[0].output_path == edit_path
    assert requests[0].deep is True
    output = capsys.readouterr().out
    assert "Done!" in output
    assert "Warning: report warning" in output


def test_match_database_uses_save_team_names_without_external_catalog(
    monkeypatch, tmp_path
):
    import run_pipeline as run
    from editor.models import PlayerInfo, TeamData, TeamInfo

    class Save:
        player_catalog_report = SimpleNamespace(current_entries=0)

        def get_all_players(self):
            return {1001: PlayerInfo(1001, "Vanilla Player")}

        def get_all_team_info(self):
            return {101: TeamInfo(101, "Vanilla FC")}

        def get_club_team_ids(self):
            return {101}

        def get_all_rosters(self):
            return {101: TeamData(101, [1001] + [0] * 39)}

    monkeypatch.setattr(
        run.config,
        "CURRENT_TEAMS_FILE",
        tmp_path / "missing-teams.txt",
    )

    matcher, _, _, _ = run._load_match_database(Save())

    assert matcher.match_team("Vanilla FC")[0] == 101


def test_match_database_keeps_external_team_catalog_strict_for_reference_save(
    monkeypatch, tmp_path
):
    import run_pipeline as run
    from editor.models import PlayerInfo, TeamData, TeamInfo

    class Save:
        player_catalog_report = SimpleNamespace(current_entries=1)

        def get_all_players(self):
            return {1001: PlayerInfo(1001, "Reference Player")}

        def get_all_team_info(self):
            return {101: TeamInfo(101, "Reference FC")}

        def get_club_team_ids(self):
            return {101}

        def get_all_rosters(self):
            return {101: TeamData(101, [1001] + [0] * 39)}

    monkeypatch.setattr(
        run.config,
        "CURRENT_TEAMS_FILE",
        tmp_path / "missing-teams.txt",
    )

    with pytest.raises(run.PlayerCatalogError, match="Could not read team catalog"):
        run._load_match_database(Save())


def test_local_runtime_rejects_invalid_save_without_backup_or_target_text(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import run_pipeline as run
    from local_update import (
        CancellationToken,
        LocalUpdateError,
        LocalUpdateRequest,
        LocalUpdateStage,
    )

    edit_path = tmp_path / "EDIT00000000"
    original = b"encrypted-save"
    edit_path.write_bytes(original)
    decrypted = tmp_path / "decrypted-invalid"
    decrypted.mkdir()
    (decrypted / "data.dat").write_bytes(b"decrypted")

    class InvalidEditFile:
        def load(self, _path: Path) -> None:
            pass

        def validate_integrity(self) -> dict[str, object]:
            return {
                "valid": False,
                "errors": ["bad common layout"],
                "warnings": [],
                "metrics": {},
            }

    monkeypatch.setattr(run, "EditFile", InvalidEditFile)
    monkeypatch.setattr(run.crypto, "decrypt", lambda _path: decrypted)
    monkeypatch.setattr(run.crypto, "cleanup_temp", lambda _path: None)
    backup_calls: list[Path] = []
    monkeypatch.setattr(run.backup_mod, "create_backup", backup_calls.append)

    with pytest.raises(LocalUpdateError) as caught:
        run._RunLocalUpdateRuntime().validate_and_prepare(
            LocalUpdateRequest(edit_path), (), CancellationToken()
        )

    assert caught.value.code == "invalid_save"
    assert caught.value.stage is LocalUpdateStage.VALIDATING
    assert "FL26" not in str(caught.value)
    assert "Football Life 2026" not in str(caught.value)
    assert backup_calls == []
    assert edit_path.read_bytes() == original


def test_local_runtime_accepts_non_blocking_roster_warnings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import run_pipeline as run
    from local_update import CancellationToken, LocalUpdateRequest

    edit_path = tmp_path / "EDIT00000000"
    edit_path.write_bytes(b"encrypted-save")
    decrypted = tmp_path / "decrypted-warning"
    decrypted.mkdir()
    (decrypted / "data.dat").write_bytes(b"decrypted")
    warning = "Team 11 has a shirt number assigned to an empty roster slot"

    class WarningEditFile:
        def load(self, _path: Path) -> None:
            pass

        def validate_integrity(self) -> dict[str, object]:
            return {
                "valid": True,
                "errors": [],
                "warnings": [warning],
                "metrics": {},
            }

    monkeypatch.setattr(run, "EditFile", WarningEditFile)
    monkeypatch.setattr(run.crypto, "decrypt", lambda _path: decrypted)
    monkeypatch.setattr(run.crypto, "cleanup_temp", lambda _path: None)

    runtime = run._RunLocalUpdateRuntime()
    prepared = runtime.validate_and_prepare(
        LocalUpdateRequest(edit_path), (), CancellationToken()
    )
    try:
        assert prepared.edit_file is not None
    finally:
        runtime.cleanup(prepared)


def test_local_runtime_attaches_save_header_profile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import run_pipeline as run
    from editor.save_metadata import FILE_HEADER_SIZE
    from local_update import CancellationToken, LocalUpdateRequest

    edit_path = tmp_path / "EDIT00000000"
    edit_path.write_bytes(b"encrypted-save")
    game_root = tmp_path / "pes2021"
    decrypted = tmp_path / "decrypted-profile"
    decrypted.mkdir()
    (decrypted / "data.dat").write_bytes(b"decrypted")
    header = bytearray(FILE_HEADER_SIZE)
    header[144:148] = b"EDIT"
    header[176 : 176 + len(b"eFootball PES 2021 SEASON UPDATE")] = (
        b"eFootball PES 2021 SEASON UPDATE"
    )
    (decrypted / "header.dat").write_bytes(header)

    class ProfileEditFile:
        def __init__(self) -> None:
            self.header = None

        def load(self, _path: Path) -> None:
            pass

        def attach_save_header(self, value) -> None:
            self.header = value

        def validate_integrity(self) -> dict[str, object]:
            return {"valid": True, "errors": [], "warnings": [], "metrics": {}}

    monkeypatch.setattr(run, "EditFile", ProfileEditFile)
    monkeypatch.setattr(run.crypto, "decrypt", lambda _path: decrypted)
    monkeypatch.setattr(run.crypto, "cleanup_temp", lambda _path: None)

    runtime = run._RunLocalUpdateRuntime()
    prepared = runtime.validate_and_prepare(
        LocalUpdateRequest(edit_path, game_root=game_root),
        (),
        CancellationToken(),
    )
    try:
        assert prepared.edit_file.header.is_pes21
        assert prepared.edit_file.game_root == game_root
    finally:
        runtime.cleanup(prepared)


def test_local_runtime_uses_selected_input(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import run_pipeline as run
    from local_update import CancellationToken, LocalUpdateRequest

    selected_input = tmp_path / "selected" / "EDIT00000000"
    selected_input.parent.mkdir()
    selected_input.write_bytes(b"selected-encrypted-save")
    decrypted = tmp_path / "decrypted-selected"
    decrypted.mkdir()
    data_dat = decrypted / "data.dat"
    data_dat.write_bytes(b"selected-decrypted-save")

    loaded_paths: list[Path] = []

    class FakeEditFile:
        def __init__(self) -> None:
            self._data = bytearray(b"selected-decrypted-save")

        def load(self, path: Path) -> None:
            loaded_paths.append(Path(path))

        def validate_integrity(self) -> dict[str, object]:
            return {"valid": True, "errors": [], "warnings": [], "metrics": {}}


    decrypt_paths: list[Path] = []

    def fake_decrypt(path: Path) -> Path:
        decrypt_paths.append(Path(path))
        return decrypted

    monkeypatch.setattr(run, "EditFile", FakeEditFile)
    monkeypatch.setattr(run.crypto, "decrypt", fake_decrypt)
    monkeypatch.setattr(run.crypto, "cleanup_temp", lambda _path: None)

    runtime = run._RunLocalUpdateRuntime()
    prepared = runtime.validate_and_prepare(
        LocalUpdateRequest(selected_input), (), CancellationToken()
    )
    try:
        assert decrypt_paths == [selected_input]
        assert loaded_paths == [data_dat]
    finally:
        runtime.cleanup(prepared)


def test_cmd_run_dry_run_resolves_stale_loan_chain(monkeypatch, tmp_path, capsys):
    import run
    import run_pipeline

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

    monkeypatch.setattr(run_pipeline, "EditFile", FakeEditFile)
    monkeypatch.setattr(run_pipeline.crypto, "decrypt", lambda _: decrypted)
    monkeypatch.setattr(run_pipeline.crypto, "cleanup_temp", lambda _: None)
    monkeypatch.setattr(
        run_pipeline,
        "fetch_transfers_for_club_names",
        lambda *_, **__: transfers,
    )
    monkeypatch.setattr(run_pipeline.transfer_logger, "read_log", lambda *_, **__: [])

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
    import run_pipeline

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
    plan = PlannedRosterAction(matched, "shirt_update", team_id)

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

    monkeypatch.setattr(run_pipeline, "EditFile", FakeEditFile)
    monkeypatch.setattr(run_pipeline, "_scrape_run_transfers", lambda _: [transfer])
    monkeypatch.setattr(run_pipeline, "_load_match_database", lambda _: (None, {}, {}, {team_id}))
    monkeypatch.setattr(
        run_pipeline,
        "_match_and_plan_transfers",
        lambda *_, **__: ([plan], [matched], str(output_path.resolve())),
    )
    monkeypatch.setattr(run_pipeline.crypto, "decrypt", lambda _: decrypted)
    monkeypatch.setattr(run_pipeline.crypto, "encrypt", lambda *_: None)
    monkeypatch.setattr(run_pipeline.crypto, "cleanup_temp", lambda _: None)
    monkeypatch.setattr(run_pipeline.backup_mod, "create_backup", lambda _: tmp_path / "backup")
    monkeypatch.setattr(run_pipeline.transfer_logger, "save_reports", lambda _: None)

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


def test_real_run_applies_shirt_number_swaps_as_one_batch(
    monkeypatch, tmp_path
):
    from local_update import CancellationToken, LocalUpdateRequest

    team_id = 100
    first_player_id, second_player_id = 100527, 168639
    edit_path = tmp_path / "EDIT00000000"
    output_path = tmp_path / "updated" / "EDIT00000000"
    edit_path.write_bytes(b"encrypted-edit")

    first_transfer = Transfer(
        "First Player",
        "Club",
        "Club",
        transfer_type="shirt_number_update",
        shirt_number=12,
    )
    second_transfer = Transfer(
        "Second Player",
        "Club",
        "Club",
        transfer_type="shirt_number_update",
        shirt_number=13,
    )
    first_match = MatchedTransfer(
        first_transfer,
        player_id=first_player_id,
        from_team_id=team_id,
        to_team_id=team_id,
        player_confidence=100,
        from_team_confidence=100,
        to_team_confidence=100,
        matched_player_name="First Player",
    )
    second_match = MatchedTransfer(
        second_transfer,
        player_id=second_player_id,
        from_team_id=team_id,
        to_team_id=team_id,
        player_confidence=100,
        from_team_confidence=100,
        to_team_confidence=100,
        matched_player_name="Second Player",
    )
    plan = [
        PlannedRosterAction(first_match, "shirt_update", team_id),
        PlannedRosterAction(second_match, "shirt_update", team_id),
    ]

    class FakeEditFile:
        def __init__(self):
            self._data = bytearray(b"decrypted-edit")
            self.shirts = {first_player_id: 13, second_player_id: 12}
            self.batch_calls = []

        def get_player_shirt_number(self, requested_team, player_id):
            assert requested_team == team_id
            return self.shirts.get(player_id)

        def get_team_roster(self, requested_team):
            assert requested_team == team_id
            return TeamData(
                team_id,
                [first_player_id, second_player_id] + [0] * 38,
                [self.shirts[first_player_id], self.shirts[second_player_id]]
                + [0] * 38,
            )

        def update_player_shirt_numbers(self, requested_team, updates):
            assert requested_team == team_id
            self.batch_calls.append(updates)
            for player_id, shirt_number in updates:
                self.shirts[player_id] = shirt_number
            return True

        def validate_integrity(self):
            return {"valid": True, "errors": [], "warnings": [], "metrics": {}}

    fake_edit_file = FakeEditFile()
    prepared = SimpleNamespace(
        edit_file=fake_edit_file,
        roster_plan=plan,
        original_data=bytes(fake_edit_file._data),
        edit_path=edit_path,
        output_path=output_path,
        backup_path=None,
        pending_logs=[],
        run_records=[],
    )
    monkeypatch.setattr(
        run_pipeline.backup_mod,
        "create_backup",
        lambda _: tmp_path / "backup",
    )

    mutation = run_pipeline._RunLocalUpdateRuntime().apply(
        LocalUpdateRequest(edit_path, output_path=output_path),
        prepared,
        plan,
        CancellationToken(),
    )

    assert fake_edit_file.batch_calls == [[
        (first_player_id, 12),
        (second_player_id, 13),
    ]]
    assert fake_edit_file.shirts == {first_player_id: 12, second_player_id: 13}
    assert mutation.shirt_numbers_changed == 2
    assert mutation.safety_skipped == 0

def test_real_run_applies_captain_update_without_transfer_actions(
    monkeypatch, tmp_path
):
    from local_update import CancellationToken, LocalUpdateRequest

    class FakeEditFile:
        def __init__(self):
            self._data = bytearray(b"original")
            self.captain = 1001
            self.calls = []

        def get_team_captain_player(self, team_id):
            assert team_id == 101
            return self.captain

        def set_team_captain(self, team_id, player_id):
            assert team_id == 101
            self.calls.append((team_id, player_id))
            self.captain = player_id
            return True

    edit_path = tmp_path / "EDIT00000000"
    output_path = tmp_path / "output" / "EDIT00000000"
    fake_edit_file = FakeEditFile()
    prepared = SimpleNamespace(
        edit_file=fake_edit_file,
        roster_plan=(),
        captain_plan=(
            run_pipeline._PlannedCaptainUpdate(
                source=CaptainUpdate(
                    club_name="Example FC",
                    team_id_fotmob=42,
                    player_name="Captain Player",
                    player_id_fotmob=987,
                ),
                team_id=101,
                player_id=1002,
                matched_player_name="Captain Player",
                confidence=100.0,
            ),
        ),
        captain_records=[],
        original_data=bytes(fake_edit_file._data),
        edit_path=edit_path,
        output_path=output_path,
        backup_path=None,
    )
    monkeypatch.setattr(
        run_pipeline.backup_mod,
        "create_backup",
        lambda _: tmp_path / "backup",
    )

    mutation = run_pipeline._RunLocalUpdateRuntime().apply(
        LocalUpdateRequest(edit_path, output_path=output_path),
        prepared,
        (),
        CancellationToken(),
    )

    assert fake_edit_file.calls == [(101, 1002)]
    assert fake_edit_file.captain == 1002
    assert mutation.transfer_applied == 0
    assert mutation.captains_changed == 1
    assert prepared.captain_records[0]["transfer_type"] == "captain_update"









def test_local_runtime_baselines_native_integrity_diagnostics_before_verify(
    monkeypatch, tmp_path
):
    import run_pipeline as run
    from local_update import CancellationToken, LocalUpdateRequest, LocalUpdateError

    edit_path = tmp_path / "EDIT00000000"
    edit_path.write_bytes(b"encrypted")
    data_dat = tmp_path / "data.dat"
    data_dat.write_bytes(b"original")
    semantic_error = (
        "Team 128 game-plan preset 0x4 assigns GK player 142128 position code 10"
    )
    errors = [semantic_error]

    class FakeEditFile:
        def __init__(self):
            self._data = bytearray(b"original")

        def validate_integrity(self):
            return {
                "valid": not errors,
                "errors": list(errors),
                "warnings": [],
                "metrics": {},
            }

    edit_file = FakeEditFile()
    prepared = run._RunPrepared(
        temp_dir=tmp_path,
        data_dat=data_dat,
        edit_file=edit_file,
        edit_path=edit_path,
        output_path=edit_path,
        input_digest=run._sha256_file(edit_path),
        same_input_output=True,
        output_existed=True,
        output_digest=run._sha256_file(edit_path),
    )
    monkeypatch.setattr(
        run,
        "_load_match_database",
        lambda _edit_file: (None, [], {}, set()),
    )
    monkeypatch.setattr(
        run,
        "_match_and_plan_transfers",
        lambda *args, **kwargs: ([], [], "scope"),
    )

    runtime = run._RunLocalUpdateRuntime()
    runtime.match_and_plan(
        LocalUpdateRequest(edit_path),
        prepared,
        [],
        CancellationToken(),
    )
    assert prepared.pre_mutation_integrity_errors == (semantic_error,)

    edit_file._data = bytearray(b"changed")
    runtime.verify(
        LocalUpdateRequest(edit_path),
        prepared,
        object(),
        CancellationToken(),
    )
    assert bytes(edit_file._data) == b"changed"

    errors.append("bad common layout")
    with pytest.raises(LocalUpdateError) as caught:
        runtime.verify(
            LocalUpdateRequest(edit_path),
            prepared,
            object(),
            CancellationToken(),
        )

    assert caught.value.code == "post_validation_failed"
    assert bytes(edit_file._data) == b"original"