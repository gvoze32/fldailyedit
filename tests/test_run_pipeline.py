"""Integration-style coverage for the scrape-to-roster planning pipeline."""

from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace


import pytest
import run_pipeline

from editor.models import TeamData
from scraper.models import (
    CaptainUpdate,
    MatchedTransfer,
    ScrapeResult,
    TacticalUpdate,
    Transfer,
)
from transfer_planning import PlannedRosterAction
from local_update import (
    CancellationToken,
    LocalUpdateProgress,
    LocalUpdateRequest,
    LocalUpdateStage,
)


class _FakeIdentity:
    """ClubIdentityIndex double with fixed bindings and save-name lookups."""

    def __init__(self, bindings=None, names=None):
        self.bindings = dict(bindings or {})
        self.names = dict(names or {})
        self.saved = 0

    def pes_for_fotmob(self, fotmob_id):
        return self.bindings.get(int(fotmob_id))

    def fotmob_for_pes(self, pes_team_id):
        return next(
            (fid for fid, tid in self.bindings.items() if tid == pes_team_id),
            None,
        )

    def resolve_name(self, name):
        return self.names.get(name)

    def entries(self):
        return [
            {"fotmob_id": fid, "pes_team_id": tid}
            for fid, tid in self.bindings.items()
        ]

    def aliases(self):
        return {}

    def learn_from_snapshot(self, snapshot, rosters, player_names):
        return self.bindings.get(snapshot.team_id_fotmob)

    def save(self):
        self.saved += 1


def _apply_prepared(tmp_path, edit_file, roster_plan=(), *, output_path=None, **attributes):
    """Real ``_RunPrepared`` around a fake edit file for apply-stage tests."""
    edit_path = tmp_path / "EDIT00000000"
    if not edit_path.exists():
        edit_path.write_bytes(b"encrypted-edit")
    data_dat = tmp_path / "apply-data.dat"
    data_dat.write_bytes(bytes(getattr(edit_file, "_data", b"")))
    prepared = run_pipeline._RunPrepared(
        temp_dir=tmp_path,
        data_dat=data_dat,
        edit_file=edit_file,
        edit_path=edit_path,
        output_path=output_path or tmp_path / "updated" / "EDIT00000000",
        input_digest="",
        same_input_output=False,
        output_existed=False,
        output_digest=None,
    )
    prepared.roster_plan = list(roster_plan)
    for name, value in attributes.items():
        setattr(prepared, name, value)
    return prepared


def test_gameplan_overrides_come_from_snapshot_positions_not_names():
    from scraper.matcher import NameMatcher
    from scraper.models import SquadMember, SquadSnapshot

    winger = SquadMember("Example Winger", player_id_fotmob=9001, position="RW")
    back = SquadMember("Example Back", player_id_fotmob=9002, position="LB")
    sub = SquadMember("Example Sub", player_id_fotmob=9003, position="CM")
    snapshot = SquadSnapshot(
        club_name="Example FC",
        team_id_fotmob=42,
        members=(winger, back, sub),
        starter_members=(winger, back),
        sub_members=(sub,),
        source_url="https://example.test/team",
        complete=True,
    )
    matcher = NameMatcher()
    matcher.load_player_db({"Example Back": 1002, "Example Sub": 1003})
    registered = {1001: "CMF", 1002: "LB", 1003: "CMF"}

    preferences = run_pipeline._plan_gameplan_preferences(
        (snapshot,),
        matcher,
        {101: [1001, 1002, 1003]},
        {101},
        {42: 101},
        80,
        # The winger resolves by FotMob identity even with no name match.
        fotmob_player_ids={9001: 1001},
        registered_position=registered.get,
    )

    assert preferences.starters == {101: (1001, 1002)}
    assert preferences.bench == {101: (1003,)}
    # Only the starter whose live position disagrees with Player.bin moves.
    assert preferences.position_overrides == {101: {1001: "RW"}}


def test_gameplan_formations_require_supported_shapes_and_unambiguous_mapping():
    from scraper.models import SquadSnapshot

    def snapshot(team_id, formation, *, complete=True):
        return SquadSnapshot(
            club_name=f"Team {team_id}",
            team_id_fotmob=team_id,
            members=(),
            source_url=f"https://example.test/teams/{team_id}",
            complete=complete,
            formation=formation,
        )

    snapshots = (
        snapshot(42, "04-2-03-1"),
        snapshot(43, "4-3-3", complete=False),
        snapshot(44, "2-4-4"),
        snapshot(45, "4-3-3"),
        snapshot(46, "4-4-2"),
        snapshot(46, "4-2-3-1"),
    )

    planned = run_pipeline._plan_gameplan_formations(
        snapshots,
        {101, 102, 103, 104},
        {42: 101, 43: 102, 44: 103, 46: 104},
    )

    assert planned == {101: "4-2-3-1"}


class _PostMutationEditFile:
    """Roster/game-plan double whose rosters change as moves apply."""

    def __init__(self):
        self._data = bytearray(b"original")
        self.rosters = {10: [2001, 1001], 20: [3001, 3002]}
        self.captains = {20: 3001}
        self.repair_kwargs = None
        self.preferred_starter_hint = None

    def get_all_rosters(self):
        return {
            team_id: SimpleNamespace(roster=list(players), player_ids=list(players))
            for team_id, players in self.rosters.items()
        }

    def set_game_plan_preferred_starters(self, mapping):
        self.preferred_starter_hint = dict(mapping)

    def move_player(self, player_id, source, destination, **_kwargs):
        self.rosters[source].remove(player_id)
        self.rosters[destination].append(player_id)
        return True

    def get_player_position(self, player_id):
        return {2001: "CB", 3001: "GK", 3002: "CMF"}.get(player_id)

    def repair_game_plans(self, **kwargs):
        self.repair_kwargs = kwargs
        return {"checked": 2, "repaired_lineups": 1}

    def get_team_captain_player(self, team_id):
        return self.captains.get(team_id)

    def set_team_captain(self, team_id, player_id):
        if player_id not in self.rosters.get(team_id, ()):
            return False
        self.captains[team_id] = player_id
        return True


def test_post_mutation_game_plan_includes_new_signing_and_captain(
    monkeypatch, tmp_path
):
    from scraper.matcher import NameMatcher
    from scraper.models import SquadMember, SquadSnapshot

    signing = SquadMember("New Signing", player_id_fotmob=555, position="RW")
    keeper = SquadMember("Keeper", player_id_fotmob=556, position="GK")
    sub = SquadMember("Bench Mid", player_id_fotmob=557, position="CM")
    snapshot = SquadSnapshot(
        club_name="Destination FC",
        team_id_fotmob=42,
        members=(signing, keeper, sub),
        starter_members=(keeper, signing),
        sub_members=(sub,),
        source_url="https://example.test/team",
        complete=True,
    )
    move = MatchedTransfer(
        transfer=Transfer("New Signing", "Source FC", "Destination FC", date="2026-08-02"),
        player_id=2001,
        from_team_id=10,
        to_team_id=20,
        player_confidence=100.0,
        from_team_confidence=100.0,
        to_team_confidence=100.0,
    )
    edit_file = _PostMutationEditFile()
    data_dat = tmp_path / "data.dat"
    data_dat.write_bytes(b"original")
    edit_path = tmp_path / "EDIT00000000"
    edit_path.write_bytes(b"encrypted")
    prepared = run_pipeline._RunPrepared(
        temp_dir=tmp_path,
        data_dat=data_dat,
        edit_file=edit_file,
        edit_path=edit_path,
        output_path=edit_path,
        input_digest="",
        same_input_output=True,
        output_existed=True,
        output_digest=None,
    )
    prepared.matcher = NameMatcher()
    prepared.team_player_map = {10: [2001, 1001], 20: [3001, 3002]}
    prepared.club_ids = {10, 20}
    prepared.fotmob_team_map = {42: 20}
    prepared.fotmob_player_ids = {555: 2001, 556: 3001, 557: 3002}
    prepared.squad_snapshots = (snapshot,)
    prepared.captain_sources = (
        CaptainUpdate(
            club_name="Destination FC",
            team_id_fotmob=42,
            player_name="New Signing",
            player_id_fotmob=555,
        ),
    )
    prepared.roster_plan = [PlannedRosterAction(move, "move", 10)]
    monkeypatch.setattr(
        run_pipeline.backup_mod, "create_backup", lambda _path: tmp_path / "backup"
    )

    mutation = run_pipeline._RunLocalUpdateRuntime().apply(
        LocalUpdateRequest(edit_path),
        prepared,
        None,
        CancellationToken(),
    )

    # Before the move, the signing is not on the destination roster.
    assert edit_file.preferred_starter_hint == {20: (3001,)}
    kwargs = edit_file.repair_kwargs
    assert kwargs["align_positions"] is True
    assert kwargs["preferred_starters"] == {10: (), 20: (3001, 2001)}
    assert kwargs["preferred_bench"] == {20: (3002,)}
    assert kwargs["position_overrides"] == {20: {2001: "RW"}}
    assert edit_file.captains[20] == 2001
    assert mutation.transfer_applied == 1
    assert mutation.captains_changed == 1


def test_transfer_run_validates_save_before_scraping(
    monkeypatch, tmp_path, capsys
):
    import run

    edit_path = tmp_path / "EDIT00000000"
    edit_path.write_bytes(b"encrypted-edit")
    monkeypatch.setattr(
        run_pipeline,
        "_scrape_run_transfers",
        lambda *_args, **_kwargs: pytest.fail("scraped before the save was valid"),
    )
    monkeypatch.setattr(
        run_pipeline.crypto,
        "decrypt",
        lambda _path: (_ for _ in ()).throw(RuntimeError("bad save")),
    )

    with pytest.raises(SystemExit) as exc:
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

    assert exc.value.code == 1
    assert "Decryption failed" in capsys.readouterr().out

    monkeypatch.setattr(run.sys, "argv", ["run.py", "--help"])
    with pytest.raises(SystemExit) as exc:
        run.main()
    assert exc.value.code == 0
    assert "players" not in capsys.readouterr().out.lower()


def _scrape_ready_prepared(tmp_path, *, same_input_output=True):
    edit_path = tmp_path / "EDIT00000000"
    edit_path.write_bytes(b"encrypted-edit")
    data_dat = tmp_path / "data.dat"
    data_dat.write_bytes(b"data")
    prepared = run_pipeline._RunPrepared(
        temp_dir=tmp_path,
        data_dat=data_dat,
        edit_file=SimpleNamespace(_data=bytearray(b"data")),
        edit_path=edit_path,
        output_path=edit_path if same_input_output else tmp_path / "out",
        input_digest="",
        same_input_output=same_input_output,
        output_existed=True,
        output_digest=None,
    )
    prepared.club_identity = SimpleNamespace()
    prepared.club_ids = {101}
    return prepared


def test_runtime_forwards_deep_scrape_progress_to_service_callback(
    monkeypatch, tmp_path
):
    events = []
    seen_contexts = []

    def fake_scrape(_args, *, context, progress):
        seen_contexts.append(context)
        progress("Deep mode: checking indexed club 3/8 — Example FC", 3, 8)
        return []

    monkeypatch.setattr(run_pipeline, "_scrape_run_transfers", fake_scrape)
    monkeypatch.setattr(
        run_pipeline, "_save_since_date", lambda *_args, **_kwargs: "2026-09-26"
    )
    runtime = run_pipeline._RunLocalUpdateRuntime(progress=events.append)
    prepared = _scrape_ready_prepared(tmp_path)

    assert runtime.scrape(
        LocalUpdateRequest(prepared.edit_path),
        prepared,
        CancellationToken(),
    ) == []
    assert events == [
        LocalUpdateProgress(
            LocalUpdateStage.SCRAPING,
            detail="Deep mode: checking indexed club 3/8 — Example FC",
            current=3,
            total=8,
        )
    ]
    assert seen_contexts[0].club_ids == frozenset({101})
    assert seen_contexts[0].save_since_date == "2026-09-26"


def test_rebuild_from_other_input_ignores_save_log_window(monkeypatch, tmp_path):
    contexts = []
    monkeypatch.setattr(
        run_pipeline,
        "_scrape_run_transfers",
        lambda _args, *, context: contexts.append(context) or [],
    )
    monkeypatch.setattr(
        run_pipeline,
        "_save_since_date",
        lambda *_args, **_kwargs: pytest.fail("rebuild must not narrow by log"),
    )
    prepared = _scrape_ready_prepared(tmp_path, same_input_output=False)

    run_pipeline._RunLocalUpdateRuntime().scrape(
        LocalUpdateRequest(prepared.edit_path, output_path=prepared.output_path),
        prepared,
        CancellationToken(),
    )

    assert contexts[0].save_since_date is None



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
                skipped=tuple(
                    {
                        "player_name": f"Missing Player {index}",
                        "from_team": "Foreign FC",
                        "to_team": "Save FC",
                        "date": "2026-08-02",
                        "source": "fotmob",
                        "reason": "player_not_matched",
                        "detail": "not in this save",
                        "relevant": True,
                        "fotmob_player_id": 9000 + index,
                        "candidates": [],
                    }
                    for index in range(12)
                )
                + (
                    {
                        "player_name": "Future Player",
                        "from_team": "A",
                        "to_team": "B",
                        "date": "2026-12-01",
                        "source": "fotmob",
                        "reason": "not_yet_effective",
                        "detail": "",
                        "relevant": False,
                        "fotmob_player_id": None,
                        "candidates": [],
                    },
                ),
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
    # The CLI prints the full not-applied list grouped by reason.
    assert "Not applied: 13 transfers (12 touch this save" in output
    assert "player_not_matched (12):" in output
    assert "not_yet_effective (1):" in output
    assert all(f"Missing Player {index}:" in output for index in range(12))
    assert "[FotMob 9011]" in output
    assert output.index("player_not_matched (12):") < output.index(
        "not_yet_effective (1):"
    )


def test_run_cli_forwards_release_policy_to_local_update(
    monkeypatch, tmp_path, capsys
):
    import run
    from local_update import LocalUpdateResult

    edit_path = tmp_path / "EDIT00000000"
    edit_path.write_bytes(b"encrypted-edit")
    policy_path = tmp_path / "policy.json"
    requests = []

    class FakeService:
        def execute(self, request):
            requests.append(request)
            return LocalUpdateResult(
                target_path=edit_path,
                backup_path=None,
                installed_sha256=None,
                transfer_applied=0,
                shirt_numbers_changed=0,
                unchanged=0,
                safety_skipped=0,
                no_changes=True,
            )

    monkeypatch.setattr(run_pipeline, "build_local_update_service", FakeService)
    monkeypatch.setattr(
        run.sys,
        "argv",
        [
            "run.py",
            "run",
            "--edit-file",
            str(edit_path),
            "--in-place",
            "--release-policy",
            str(policy_path),
        ],
    )

    run.main()

    assert [request.release_policy_file for request in requests] == [policy_path]
    capsys.readouterr()


@pytest.mark.parametrize(
    "argv",
    [
        ["-v"],
        ["--verbose", "--dry-run"],
        ["-v", "run", "--dry-run"],
        ["run", "--dry-run", "-v"],
    ],
)
def test_verbose_flag_parses_before_or_after_subcommand(monkeypatch, argv):
    import run

    seen = []
    monkeypatch.setattr(run, "cmd_run", lambda args: seen.append(args))
    monkeypatch.setattr(run, "setup_logging", lambda verbose: seen.append(verbose))
    monkeypatch.setattr(run.sys, "argv", ["run.py", *argv])

    run.main()

    assert seen[0] is True
    assert seen[1].command == "run"


def test_inspect_subcommand_is_registered(monkeypatch):
    import run

    seen = []
    monkeypatch.setattr(run, "cmd_inspect", seen.append)
    monkeypatch.setattr(
        run.sys, "argv", ["run.py", "inspect", "--edit-file", "EDIT00000000"]
    )

    run.main()

    assert [args.edit_file for args in seen] == ["EDIT00000000"]


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


def test_match_database_uses_verified_native_player_alias(
    monkeypatch, tmp_path
):
    import run_pipeline as run
    from editor.models import PlayerInfo, TeamInfo
    from editor.playerbin import PlayerBinDatabase, PlayerBinRecord

    legacy = tmp_path / "players.csv"
    legacy.write_text(
        "PlayerID,PlayerName\n1001,Cole Palmer\n",
        encoding="utf-8",
    )
    native = PlayerBinDatabase(
        {
            1001: PlayerBinRecord(
                1001,
                "コール パーマー",
                23,
                "AMF",
                0,
                "PALMER",
            )
        }
    )

    class Save:
        is_pes21_save = True
        player_catalog_report = SimpleNamespace(
            current_entries=0,
            missing_roster_ids=(),
        )
        playerbin_db = native

        def attach_playerbin(self, database):
            self.playerbin_db = database

        def attach_teambin(self, _database):
            pass

        def attach_player_assignment(self, _database):
            pass

        def get_all_players(self):
            return {
                1001: PlayerInfo(
                    1001,
                    "コール パーマー",
                    "PALMER",
                    position="AMF",
                    age=23,
                )
            }

        def get_all_team_info(self):
            return {101: TeamInfo(101, "Chelsea FC")}

        def get_club_team_ids(self):
            return {101}

        def get_all_rosters(self):
            from editor.models import TeamData

            return {101: TeamData(101, [1001] + [0] * 39)}

    monkeypatch.setattr(run.config, "PLAYERS_CSV_FILE", legacy)
    monkeypatch.setattr(
        run.native_metadata,
        "_load_playerbin_database",
        lambda **_kwargs: (native, "fixture::Player.bin"),
    )
    monkeypatch.setattr(
        run.native_metadata,
        "_load_teambin_database",
        lambda **_kwargs: (None, None),
    )
    monkeypatch.setattr(
        run.native_metadata,
        "_load_player_assignment_database",
        lambda **_kwargs: (None, None),
    )

    matcher, _, team_player_map, _ = run._load_match_database(Save())

    player_id, _, confidence = matcher.match_player(
        "Cole Palmer",
        threshold=80,
        from_team_id=101,
        team_player_map=team_player_map,
        position="AMF",
    )

    assert player_id == 1001
    assert confidence == 100.0


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
            LocalUpdateRequest(edit_path), CancellationToken()
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
        LocalUpdateRequest(edit_path), CancellationToken()
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
        LocalUpdateRequest(selected_input), CancellationToken()
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
        "_load_club_identity",
        lambda *_args: _FakeIdentity(
            {9847: psg, 8586: tottenham, 9885: juventus},
            {
                "PSG": psg,
                "Paris Saint-Germain": psg,
                "Tottenham": tottenham,
                "Tottenham Hotspur": tottenham,
                "Juventus": juventus,
                "Juventus FC": juventus,
            },
        ),
    )
    fetched = []
    monkeypatch.setattr(
        run_pipeline,
        "fetch_clubs_transfers_safely",
        lambda club_ids, **__: fetched.append(tuple(club_ids)) or transfers,
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
    assert fetched == [(9847, 8586, 9885)]


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
    monkeypatch.setattr(run_pipeline, "_scrape_run_transfers", lambda *_, **__: [transfer])
    monkeypatch.setattr(run_pipeline, "_load_match_database", lambda _: (None, {}, {}, {team_id}))
    monkeypatch.setattr(run_pipeline, "_load_club_identity", lambda *_: _FakeIdentity())
    monkeypatch.setattr(
        run_pipeline,
        "_match_and_plan_transfers",
        lambda *_, **__: ([plan], [matched], str(output_path.resolve())),
    )
    monkeypatch.setattr(run_pipeline.crypto, "decrypt", lambda _: decrypted)
    monkeypatch.setattr(run_pipeline.crypto, "encrypt", lambda *_: None)
    monkeypatch.setattr(run_pipeline.crypto, "cleanup_temp", lambda _: None)
    monkeypatch.setattr(run_pipeline.backup_mod, "create_backup", lambda _: tmp_path / "backup")
    monkeypatch.setattr(run_pipeline.transfer_logger, "save_reports", lambda *_, **__: None)

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
    # A run whose only action was safety-skipped changes nothing, so the
    # save is neither backed up nor republished.
    assert "No effective transfer" in output
    assert "✅ Done!" not in output


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
    prepared = _apply_prepared(
        tmp_path, fake_edit_file, plan, output_path=output_path
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

def test_shirt_update_for_player_not_on_team_is_safety_skipped(
    monkeypatch, tmp_path
):
    from local_update import CancellationToken, LocalUpdateRequest

    team_id = 100
    member_id, departed_id = 100527, 168639
    edit_path = tmp_path / "EDIT00000000"
    output_path = tmp_path / "updated" / "EDIT00000000"
    edit_path.write_bytes(b"encrypted-edit")

    def shirt_action(name, player_id, shirt_number):
        match = MatchedTransfer(
            Transfer(
                name,
                "Club",
                "Club",
                transfer_type="shirt_number_update",
                shirt_number=shirt_number,
            ),
            player_id=player_id,
            from_team_id=team_id,
            to_team_id=team_id,
            player_confidence=100,
            from_team_confidence=100,
            to_team_confidence=100,
            matched_player_name=name,
        )
        return PlannedRosterAction(match, "shirt_update", team_id)

    # The departed player's earlier move was safety-skipped, so the planned
    # shirt update targets a player who never joined this roster.
    plan = [
        shirt_action("Member Player", member_id, 12),
        shirt_action("Departed Player", departed_id, 7),
    ]

    class FakeEditFile:
        def __init__(self):
            self._data = bytearray(b"decrypted-edit")
            self.shirts = {member_id: 13}

        def get_player_shirt_number(self, requested_team, player_id):
            assert requested_team == team_id
            return self.shirts.get(player_id)

        def get_team_roster(self, requested_team):
            assert requested_team == team_id
            return TeamData(
                team_id,
                [member_id] + [0] * 39,
                [self.shirts[member_id]] + [0] * 39,
            )

        def update_player_shirt_numbers(self, requested_team, updates):
            if any(player_id not in self.shirts for player_id, _ in updates):
                return False
            for player_id, shirt_number in updates:
                self.shirts[player_id] = shirt_number
            return True

        def validate_integrity(self):
            return {"valid": True, "errors": [], "warnings": [], "metrics": {}}

    fake_edit_file = FakeEditFile()
    prepared = _apply_prepared(
        tmp_path, fake_edit_file, plan, output_path=output_path
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

    assert fake_edit_file.shirts == {member_id: 12}
    assert mutation.shirt_numbers_changed == 1
    assert mutation.safety_skipped == 1


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

        def get_all_rosters(self):
            return {101: SimpleNamespace(roster=[1001, 1002])}

    from scraper.matcher import NameMatcher

    output_path = tmp_path / "output" / "EDIT00000000"
    fake_edit_file = FakeEditFile()
    prepared = _apply_prepared(
        tmp_path,
        fake_edit_file,
        output_path=output_path,
        output_existed=True,
        matcher=NameMatcher(),
        club_ids={101},
        fotmob_team_map={42: 101},
        fotmob_player_ids={987: 1002},
        captain_sources=(
            CaptainUpdate(
                club_name="Example FC",
                team_id_fotmob=42,
                player_name="Captain Player",
                player_id_fotmob=987,
            ),
        ),
    )
    edit_path = prepared.edit_path
    backup_calls = []
    monkeypatch.setattr(
        run_pipeline.backup_mod,
        "create_backup",
        lambda path: backup_calls.append(path) or tmp_path / "backup",
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
    # The existing separate output is what publish() overwrites.
    assert backup_calls == [output_path]


def test_tactical_only_scrape_result_plans_and_applies_settings(
    monkeypatch, tmp_path
):
    class FakeEditFile:
        def __init__(self):
            self._data = bytearray(b"original")
            self.settings = {}
            self.is_pes21_save = False

        def validate_integrity(self):
            return {"errors": []}

        def set_team_tactical_settings(self, team_id, settings):
            assert team_id == 101
            changed = sum(
                self.settings.get(name) != value
                for name, value in settings.items()
            )
            self.settings.update(settings)
            if changed:
                self._data[0] = settings["attacking_style"]
            return changed

    edit_path = tmp_path / "EDIT00000000"
    output_path = tmp_path / "output" / "EDIT00000000"
    data_dat = tmp_path / "data.dat"
    data_dat.write_bytes(b"original")
    edit_file = FakeEditFile()
    prepared = run_pipeline._RunPrepared(
        temp_dir=tmp_path,
        data_dat=data_dat,
        edit_file=edit_file,
        edit_path=edit_path,
        output_path=output_path,
        input_digest="input",
        same_input_output=False,
        output_existed=False,
        output_digest=None,
    )
    monkeypatch.setattr(
        run_pipeline,
        "_load_match_database",
        lambda _edit_file: (None, [], {}, {101}),
    )
    monkeypatch.setattr(
        run_pipeline,
        "_match_and_plan_transfers",
        lambda *args, **kwargs: ((), (), "scope"),
    )
    monkeypatch.setattr(
        run_pipeline,
        "_load_club_identity",
        lambda *_args: _FakeIdentity({42: 101}),
    )
    backup_calls = []
    backup_path = tmp_path / "backup"
    monkeypatch.setattr(
        run_pipeline.backup_mod,
        "create_backup",
        lambda path: backup_calls.append(path) or backup_path,
    )
    tactical_update = TacticalUpdate(
        club_name="Example FC",
        team_id_fotmob=42,
        league_id=10,
        settings=(
            ("attacking_style", 1),
            ("build_up", 0),
            ("attacking_area", 1),
            ("defensive_style", 0),
            ("containment_area", 1),
            ("pressuring", 0),
            ("defensive_line", 7),
            ("compactness", 6),
        ),
        sample_matches=8,
    )
    tactical_only = ScrapeResult(tactical_updates=(tactical_update,))
    runtime = run_pipeline._RunLocalUpdateRuntime()
    request = LocalUpdateRequest(edit_path, output_path=output_path)

    plan = runtime.match_and_plan(
        request,
        prepared,
        tactical_only,
        CancellationToken(),
    )
    mutation = runtime.apply(
        request,
        prepared,
        plan,
        CancellationToken(),
    )

    assert bool(tactical_only)
    expected_settings = {
        "attacking_style": 1,
        "build_up": 0,
        "attacking_area": 1,
        "defensive_style": 0,
        "containment_area": 1,
        "pressuring": 0,
        "defensive_line": 7,
        "compactness": 6,
    }
    assert prepared.gameplan_tactics == {101: expected_settings}
    assert edit_file.settings == expected_settings
    assert mutation.tactics_changed == 8
    assert backup_calls == [edit_path]
    assert prepared.backup_path == backup_path


def test_skipped_transfers_surface_in_local_update_result_and_reports(
    monkeypatch, tmp_path
):
    from transfer_planning import SkippedTransfer

    class FakeEditFile:
        def __init__(self):
            self._data = bytearray(b"original")

        def validate_integrity(self):
            return {"errors": []}

    edit_file = FakeEditFile()
    prepared = _apply_prepared(tmp_path, edit_file)
    monkeypatch.setattr(
        run_pipeline,
        "_load_match_database",
        lambda _edit_file: (None, [], {}, {101}),
    )
    monkeypatch.setattr(
        run_pipeline,
        "_load_club_identity",
        lambda *_args: _FakeIdentity({42: 101}),
    )
    missing = SkippedTransfer(
        player_name="Missing Player",
        from_team="Foreign FC",
        to_team="Example FC",
        date="2026-08-02",
        source="fotmob",
        reason="player_not_matched",
        detail="Player is not in this save",
        relevant=True,
        fotmob_player_id=9001,
        candidates=(),
    )

    def plan_with_skip(*_args, report, **_kwargs):
        report.skipped.append(missing)
        return [], [], "scope"

    monkeypatch.setattr(run_pipeline, "_match_and_plan_transfers", plan_with_skip)
    reports = []
    monkeypatch.setattr(
        run_pipeline.transfer_logger,
        "save_reports",
        lambda entries, **kwargs: reports.append((entries, kwargs["skipped"])),
    )
    future = Transfer(
        "Future Player",
        "Foreign A",
        "Foreign B",
        date="2099-01-01",
        player_id_fotmob=77,
    )
    scrape = ScrapeResult(pending_transfers=(future,))
    runtime = run_pipeline._RunLocalUpdateRuntime()
    request = LocalUpdateRequest(prepared.edit_path, output_path=prepared.output_path)

    plan = runtime.match_and_plan(request, prepared, scrape, CancellationToken())
    result = runtime.apply(request, prepared, plan, CancellationToken())

    assert result.no_changes is True
    assert [
        (row["player_name"], row["reason"], row["relevant"], row["fotmob_player_id"])
        for row in result.skipped
    ] == [
        ("Missing Player", "player_not_matched", True, 9001),
        ("Future Player", "not_yet_effective", False, 77),
    ]
    assert all(row["save_scope"] == "scope" for row in result.skipped)
    # The not-applied report is written even when nothing else changed.
    assert reports == [([], result.skipped)]


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
        "_load_club_identity",
        lambda *_args: _FakeIdentity(),
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