"""Integration-style coverage for the scrape-to-roster planning pipeline."""

from argparse import Namespace
from types import SimpleNamespace

from editor.models import TeamData
from scraper.models import MatchedTransfer, Transfer


def test_cmd_run_dry_run_resolves_stale_loan_chain(monkeypatch, tmp_path, capsys):
    import run
    monkeypatch.setattr(run, "load_curated_players", lambda: ())

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
                psg: TeamData(psg),
                tottenham: TeamData(tottenham, [player_id] + [0] * 39),
                juventus: TeamData(juventus),
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
    monkeypatch.setattr(run, "load_curated_players", lambda: ())

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


def test_curated_player_planning_waits_until_transfer_frees_roster_slot():
    import run
    from editor.curated_player import load_curated_players

    dastan = load_curated_players()[0]

    class FakeEditFile:
        _player_cache = {}

        def get_all_team_info(self):
            return {
                101: SimpleNamespace(name="Arsenal FC"),
                102: SimpleNamespace(name="Chelsea FC"),
            }

        def get_team_roster(self, team_id):
            assert team_id == 102
            return TeamData(102, list(range(100001, 100041)))

        def find_player_teams(self, *_args, **_kwargs):
            return []

    edit_file = FakeEditFile()
    waiting = run._plan_curated_players(edit_file, (dastan,), [])
    assert waiting[0].status == "waiting"
    assert waiting[0].reason == "destination_roster_full"

    departure = MatchedTransfer(
        transfer=Transfer("Existing Player", "Chelsea", "Arsenal"),
        player_id=100001,
        from_team_id=102,
        to_team_id=101,
        player_confidence=100,
        from_team_confidence=100,
        to_team_confidence=100,
        matched_player_name="Existing Player",
    )
    after_departure = run._plan_curated_players(
        edit_file,
        (dastan,),
        [run.PlannedRosterAction(departure, "move", 102)],
    )
    assert after_departure[0].status == "ready"
    assert after_departure[0].reason == "eligible_after_planned_transfers"


def test_run_reports_waiting_curated_player_when_no_transfers_exist(
    monkeypatch, tmp_path, capsys
):
    import run

    edit_path = tmp_path / "EDIT00000000"
    edit_path.write_bytes(b"encrypted-edit")
    decrypted = tmp_path / "decrypted-curated"
    decrypted.mkdir()
    (decrypted / "data.dat").write_bytes(b"decrypted-edit")

    class FakeEditFile:
        _player_cache = {}

        def load(self, _path):
            return None

        def validate_integrity(self):
            return {"valid": True, "errors": [], "warnings": [], "metrics": {}}

        def get_all_team_info(self):
            return {102: SimpleNamespace(name="Chelsea FC")}

        def get_team_roster(self, team_id):
            assert team_id == 102
            return TeamData(102, list(range(100001, 100041)))

        def find_player_teams(self, *_args, **_kwargs):
            return []

    monkeypatch.setattr(run, "EditFile", FakeEditFile)
    monkeypatch.setattr(run, "_scrape_run_transfers", lambda _args: [])
    monkeypatch.setattr(
        run,
        "_load_match_database",
        lambda _edit_file: (None, {}, {}, set()),
    )
    monkeypatch.setattr(
        run,
        "_match_and_plan_transfers",
        lambda *_args, **_kwargs: ([], [], "test-scope"),
    )
    monkeypatch.setattr(run.crypto, "decrypt", lambda _path: decrypted)
    monkeypatch.setattr(run.crypto, "cleanup_temp", lambda _path: None)
    monkeypatch.setattr(
        run.backup_mod,
        "create_backup",
        lambda _path: (_ for _ in ()).throw(
            AssertionError("waiting-only run must not create a backup")
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

    output = capsys.readouterr().out
    assert "No verified transfers found; checking curated missing players." in output
    assert "CURATED WAITING (destination_roster_full)" in output
    assert "No effective roster or curated-player changes to apply" in output
    assert edit_path.read_bytes() == b"encrypted-edit"


def test_curated_creation_is_logged_after_successful_save(
    monkeypatch, tmp_path
):
    import run
    from editor.curated_player import CuratedPlayerResult

    edit_path = tmp_path / "EDIT00000000"
    edit_path.write_bytes(b"encrypted-edit")
    decrypted = tmp_path / "decrypted-created"
    decrypted.mkdir()
    (decrypted / "data.dat").write_bytes(b"decrypted-edit")
    audit_calls = []
    reports = []

    class FakeEditFile:
        _player_cache = {}

        def __init__(self):
            self._data = bytearray(b"decrypted-edit")

        def load(self, _path):
            return None

        def validate_integrity(self):
            return {"valid": True, "errors": [], "warnings": [], "metrics": {}}
        def get_player_shirt_number(self, team_id, player_id):
            assert (team_id, player_id) == (102, 200000)
            return 36


        def save(self, _path):
            return None

    dastan = run.load_curated_players()[0]
    ready = CuratedPlayerResult(
        dastan.player_id,
        dastan.name,
        "ready",
        "eligible",
        dastan.team_id,
    )
    created = CuratedPlayerResult(
        dastan.player_id,
        dastan.name,
        "created",
        "created_and_registered",
        dastan.team_id,
    )

    monkeypatch.setattr(run, "EditFile", FakeEditFile)
    monkeypatch.setattr(run, "_scrape_run_transfers", lambda _args: [])
    monkeypatch.setattr(
        run,
        "_load_match_database",
        lambda _edit_file: (None, {}, {}, set()),
    )
    monkeypatch.setattr(
        run,
        "_match_and_plan_transfers",
        lambda *_args, **_kwargs: ([], [], "test-scope"),
    )
    monkeypatch.setattr(
        run,
        "_plan_curated_players",
        lambda *_args, **_kwargs: [ready],
    )
    monkeypatch.setattr(
        run,
        "apply_curated_player",
        lambda *_args, **_kwargs: created,
    )
    monkeypatch.setattr(run.crypto, "decrypt", lambda _path: decrypted)
    monkeypatch.setattr(run.crypto, "encrypt", lambda *_args: None)
    monkeypatch.setattr(run.crypto, "cleanup_temp", lambda _path: None)
    monkeypatch.setattr(
        run.backup_mod,
        "create_backup",
        lambda _path: tmp_path / "backup",
    )
    monkeypatch.setattr(
        run.transfer_logger,
        "log_transfer",
        lambda **kwargs: audit_calls.append(kwargs),
    )
    monkeypatch.setattr(
        run.transfer_logger,
        "save_reports",
        lambda entries: reports.append(entries),
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

    assert len(audit_calls) == 1
    assert audit_calls[0]["player_id"] == dastan.player_id
    assert audit_calls[0]["transfer_type"] == "curated_player_creation"
    assert audit_calls[0]["roster_action"] == "create"
    assert audit_calls[0]["save_scope"] == "test-scope"
    assert reports[0][0]["transfer_type"] == "curated_player_creation"



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


def test_players_apply_audits_only_after_successful_output_roundtrip(
    monkeypatch, tmp_path
):
    import run
    from editor.player_spec import BaseManifest, SpecResult, load_player_specs

    calls = []
    source = tmp_path / "EDIT00000000"
    output = tmp_path / "updated"
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
    monkeypatch.setattr(
        run.transfer_logger,
        "log_transfer",
        lambda **record: calls.append(("audit", record)),
    )
    monkeypatch.setattr(
        run.transfer_logger,
        "save_reports",
        lambda records: calls.append(("report", records)),
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
    assert audit["field_changes"] == [
        {"field": "speed", "from": 77, "to": 80},
        {"field": "acceleration", "from": 75, "to": 77},
        {"field": "defensive_awareness", "from": 61, "to": 62},
        {"field": "ball_winning", "from": 59, "to": 60},
    ]
    assert calls.index("save") < calls.index("encrypt")
    assert calls.index("encrypt") < calls.index("decrypt-verify")
    audit_index = next(index for index, call in enumerate(calls) if isinstance(call, tuple) and call[0] == "audit")
    report_index = next(index for index, call in enumerate(calls) if isinstance(call, tuple) and call[0] == "report")
    assert max(index for index, call in enumerate(calls) if call == "validate") < audit_index
    assert audit_index < report_index