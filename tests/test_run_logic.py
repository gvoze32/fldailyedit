"""Regression tests for fail-closed roster mutation decisions."""

import argparse
import json
from types import SimpleNamespace

import pytest

from run import (
    _competition_section_bounds,
    _iso_date_arg,
    _percentage_arg,
    _positive_int_arg,
    _resolve_run_paths,
)
from run_pipeline import (
    _RunLocalUpdateRuntime,
    _find_shirt_number_conflict,
    _match_and_plan_transfers,
    _plan_captain_updates,
    _fast_squad_target_ids,
)
from transfer_planning import (
    PlannedRosterAction,
    PlanningReport,
    SkippedTransfer,
    _build_superseded_loan_sources,
    _decide_roster_action,
    _dedupe_shirt_number_matches,
    _match_transfer_team,
    _match_transfers_statefully,
    _plan_roster_actions,
    _transfer_sort_key,
)
from scraper.club_identity import UNRESOLVED
from scraper.models import (
    CaptainUpdate,
    MatchedTransfer,
    ScrapeResult,
    SquadMember,
    SquadSnapshot,
    Transfer,
)
from editor.models import TeamData


class _FakeClubIdentity:
    """ClubIdentityIndex double: fixed FotMob bindings plus save-name lookup."""

    def __init__(self, bindings, matcher=None, names=None):
        self._bindings = dict(bindings)
        self._matcher = matcher
        self._names = dict(names or {})

    def pes_for_fotmob(self, fotmob_id):
        return self._bindings.get(int(fotmob_id))

    def fotmob_for_pes(self, pes_team_id):
        return next(
            (
                fotmob_id
                for fotmob_id, team_id in self._bindings.items()
                if team_id == pes_team_id
            ),
            None,
        )

    def resolve_name(self, name):
        if name in self._names:
            return self._names[name]
        if self._matcher is None:
            return None
        team_id, _, confidence = self._matcher.match_team(name)
        if team_id is None:
            return UNRESOLVED if confidence >= 75 else None
        return team_id if confidence >= 98 else UNRESOLVED

    def entries(self):
        return [
            {"fotmob_id": fotmob_id, "pes_team_id": team_id}
            for fotmob_id, team_id in self._bindings.items()
        ]

    def aliases(self):
        return {}

    def learn_from_snapshot(self, snapshot, rosters, player_names):
        return self._bindings.get(snapshot.team_id_fotmob)

    def save(self):
        return None


def _identity(bindings, matcher=None, names=None):
    return _FakeClubIdentity(bindings, matcher, names)


def _scrape_context(bindings, names=None, club_ids=None, save_since_date=None):
    import run_pipeline as run

    return run._SaveScrapeContext(
        club_identity=_identity(bindings, names=names),
        club_ids=frozenset(
            club_ids if club_ids is not None else bindings.values()
        ),
        save_since_date=save_since_date,
    )


def _runtime_prepared(
    tmp_path,
    edit_file,
    *,
    roster_plan=(),
    original_data=b"original",
    same_input_output=False,
    output_existed=False,
    output_path=None,
    **attributes,
):
    """Real ``_RunPrepared`` around a fake edit file, as the runtime builds it."""
    import run_pipeline as run

    edit_path = tmp_path / "input"
    edit_path.write_bytes(b"encrypted")
    data_dat = tmp_path / "data.dat"
    data_dat.write_bytes(original_data)
    prepared = run._RunPrepared(
        temp_dir=tmp_path,
        data_dat=data_dat,
        edit_file=edit_file,
        edit_path=edit_path,
        output_path=output_path or (edit_path if same_input_output else tmp_path / "output"),
        input_digest="",
        same_input_output=same_input_output,
        output_existed=output_existed,
        output_digest=None,
    )
    prepared.original_data = original_data
    prepared.roster_plan = list(roster_plan)
    for name, value in attributes.items():
        setattr(prepared, name, value)
    return prepared


def test_local_update_enables_overflow_release_by_default(tmp_path):
    from local_update import LocalUpdateRequest

    request = LocalUpdateRequest(tmp_path / "EDIT00000000")

    assert request.allow_overflow_release is True

@pytest.mark.parametrize(
    ("current", "source", "destination", "transfer_type", "expected"),
    [
        (10, 10, 20, "transfer", ("move", "")),
        (20, 10, 20, "transfer", ("noop", "")),
        (30, 10, 20, "transfer", ("skip", "current_club_mismatch")),
        # Unattached in the save with a known save source: sign him.
        (None, 10, 20, "transfer", ("add", "")),
        (None, None, 20, "free transfer", ("add", "")),
        (20, None, 20, "free transfer", ("noop", "")),
        (30, None, 20, "free transfer", ("skip", "already_registered_elsewhere")),
        (10, 10, None, "free transfer", ("release", "")),
        (None, 10, None, "free transfer", ("noop", "")),
        (30, 10, None, "free transfer", ("skip", "current_club_mismatch")),
        (20, 20, 20, "shirt_number_update", ("shirt_update", "")),
        (30, 20, 20, "shirt_number_update", ("skip", "shirt_player_not_at_club")),
    ],
)
def test_decide_roster_action_returns_explicit_reason(
    current, source, destination, transfer_type, expected
):
    assert _decide_roster_action(current, source, destination, transfer_type) == expected


def _club_match(
    *,
    source: int,
    destination: int,
    date: str,
    transfer_type: str = "transfer",
    is_loan: bool = False,
) -> MatchedTransfer:
    return MatchedTransfer(
        transfer=Transfer(
            player_name="Randal Kolo Muani",
            from_club="Source",
            to_club="Destination",
            date=date,
            transfer_type=transfer_type,
            is_loan=is_loan,
        ),
        player_id=115254,
        from_team_id=source,
        to_team_id=destination,
        player_confidence=100,
        from_team_confidence=100,
        to_team_confidence=100,
    )


def test_new_parent_club_transfer_can_reconcile_stale_loan_roster():
    psg, tottenham, juventus = 114, 179, 120
    loan = _club_match(
        source=psg,
        destination=tottenham,
        date="2025-09-01T19:27:00Z",
        transfer_type="loan",
        is_loan=True,
    )
    permanent = _club_match(
        source=psg,
        destination=juventus,
        date="2026-08-02T18:40:10Z",
    )

    # Source authorization is date-based, not dependent on API item ordering.
    allowed = _build_superseded_loan_sources([permanent, loan])

    assert allowed[id(permanent)] == frozenset({tottenham})
    assert _decide_roster_action(
        tottenham,
        psg,
        juventus,
        "transfer",
        allowed[id(permanent)],
    ) == ("move", "")


def test_unrelated_stale_roster_remains_fail_closed():
    psg, tottenham, juventus, unrelated = 114, 179, 120, 999
    loan = _club_match(
        source=psg,
        destination=tottenham,
        date="2025-09-01",
        transfer_type="loan",
        is_loan=True,
    )
    permanent = _club_match(
        source=psg,
        destination=juventus,
        date="2026-08-02",
    )
    allowed = _build_superseded_loan_sources([loan, permanent])

    assert _decide_roster_action(
        unrelated,
        psg,
        juventus,
        "transfer",
        allowed[id(permanent)],
    ) == ("skip", "current_club_mismatch")


def test_historical_loan_log_can_reconcile_a_later_run():
    psg, tottenham, juventus = 114, 179, 120
    permanent = _club_match(
        source=psg,
        destination=juventus,
        date="2026-08-02T18:40:10Z",
    )
    history = [{
        "player_id": 115254,
        "from_team_id": psg,
        "to_team_id": tottenham,
        "transfer_type": "loan",
        "transfer_date": "2025-09-01T19:27:00Z",
    }]

    allowed = _build_superseded_loan_sources(
        [permanent], historical_entries=history
    )

    assert allowed[id(permanent)] == frozenset({tottenham})


def test_roster_plan_simulates_chained_moves_chronologically():
    psg, tottenham, juventus = 114, 179, 120
    loan = _club_match(
        source=psg,
        destination=tottenham,
        date="2025-09-01T19:27:00Z",
        transfer_type="loan",
        is_loan=True,
    )
    permanent = _club_match(
        source=psg,
        destination=juventus,
        date="2026-08-02T18:40:10Z",
    )
    rosters = {
        psg: TeamData(psg, [115254] + list(range(200001, 200017)) + [0] * 23),
        tottenham: TeamData(
            tottenham, list(range(300001, 300017)) + [0] * 24
        ),
        juventus: TeamData(juventus, list(range(400001, 400017)) + [0] * 24),
    }
    superseded = _build_superseded_loan_sources([loan, permanent])

    plan = _plan_roster_actions(
        [loan, permanent], rosters, set(rosters), object(), superseded
    )

    assert [(item.action, item.current_team_id) for item in plan] == [
        ("move", psg),
        ("move", tottenham),
    ]


def test_roster_plan_refuses_to_reduce_a_club_below_sixteen_players():
    source, destination = 10, 20
    transfer = _club_match(
        source=source,
        destination=destination,
        date="2026-08-02",
    )
    rosters = {
        source: TeamData(
            source, [115254] + list(range(200001, 200016)) + [0] * 24
        ),
        destination: TeamData(
            destination, list(range(300001, 300017)) + [0] * 24
        ),
    }

    report = PlanningReport()
    plan = _plan_roster_actions(
        [transfer], rosters, set(rosters), object(), {}, report=report
    )

    assert (plan[0].action, plan[0].reason) == ("skip", "roster_minimum")
    assert [(item.reason, item.relevant) for item in report.skipped] == [
        ("roster_minimum", True)
    ]


def test_roster_plan_enables_overflow_release_by_default():
    source, destination = 10, 20
    transfer = _club_match(
        source=source,
        destination=destination,
        date="2026-08-02",
    )
    rosters = {
        source: TeamData(
            source, [115254] + list(range(200001, 200017)) + [0] * 23
        ),
        destination: TeamData(destination, list(range(1000, 1040))),
    }

    class FakeEditFile:
        def find_overflow_release_candidate(self, *_, **__):
            return 30, 1030

    allowed = _plan_roster_actions(
        [transfer],
        rosters,
        set(rosters),
        FakeEditFile(),
        {},
    )
    blocked = _plan_roster_actions(
        [transfer],
        rosters,
        set(rosters),
        FakeEditFile(),
        {},
        allow_overflow_release=False,
    )

    assert allowed[0].action == "move"
    assert allowed[0].overflow_player_id == 1030
    assert (blocked[0].action, blocked[0].reason) == (
        "skip",
        "destination_roster_full",
    )

def test_local_runtime_apply_forwards_overflow_release_permission(
    monkeypatch, tmp_path
):
    from local_update import CancellationToken, LocalUpdateRequest
    import run_pipeline as run

    class FakeEditFile:
        _data = bytearray(b"updated")

        def move_player(self, *args, **kwargs):
            self.move_kwargs = kwargs
            return True


    edit_file = FakeEditFile()
    prepared = _runtime_prepared(
        tmp_path,
        edit_file,
        roster_plan=(
            PlannedRosterAction(
                match=_club_match(source=10, destination=20, date="2026-08-02"),
                action="move",
                current_team_id=10,
                overflow_player_id=1030,
            ),
        ),
        same_input_output=True,
        output_existed=True,
    )
    edit_path = prepared.edit_path
    monkeypatch.setattr(
        run.backup_mod,
        "create_backup",
        lambda _path: tmp_path / "backup",
    )

    result = _RunLocalUpdateRuntime().apply(
        LocalUpdateRequest(edit_path, allow_overflow_release=True),
        prepared,
        None,
        CancellationToken(),
    )

    assert edit_file.move_kwargs["allow_overflow_release"] is True
    assert edit_file.move_kwargs["planned_overflow_player_id"] == 1030
    assert result.transfer_applied == 1


def test_local_runtime_publishes_gameplan_repair_without_roster_action(
    monkeypatch, tmp_path
):
    from local_update import CancellationToken, LocalUpdateRequest

    class FakeEditFile:
        def __init__(self):
            self._data = bytearray(b"original")
            self.repair_calls = 0
            self.repair_kwargs = {}

        def repair_game_plans(self, **kwargs):
            self.repair_calls += 1
            self.repair_kwargs = kwargs
            self._data[:] = b"repaired"
            return {
                "repaired_lineups": 1,
                "repaired_goalkeeper_roles": 0,
                "repaired_position_bytes": 0,
                "reset_roles": 0,
            }

    edit_file = FakeEditFile()
    prepared = _runtime_prepared(tmp_path, edit_file)
    edit_path = prepared.edit_path
    monkeypatch.setattr(
        "run_pipeline.backup_mod.create_backup",
        lambda _path: tmp_path / "backup",
    )

    result = _RunLocalUpdateRuntime().apply(
        LocalUpdateRequest(edit_path),
        prepared,
        None,
        CancellationToken(),
    )

    assert bytes(edit_file._data) == b"repaired"
    assert edit_file.repair_calls == 1
    assert edit_file.repair_kwargs == {"preserve_existing_primary": True}
    assert result.transfer_applied == 0
    assert result.shirt_numbers_changed == 0


def test_same_day_transfers_sort_by_timestamp():
    later = Transfer("Player", "B", "C", date="2026-08-02T18:00:00Z")
    earlier = Transfer("Player", "A", "B", date="2026-08-02T09:00:00Z")

    assert sorted([later, earlier], key=_transfer_sort_key) == [earlier, later]


def test_shirt_number_conflict_identifies_other_player():
    class FakeEditFile:
        def get_team_roster(self, team_id):
            assert team_id == 100
            return TeamData(
                team_id=100,
                player_ids=[10, 20] + [0] * 38,
                shirt_numbers=[1, 12] + [0] * 38,
            )

    edit_file = FakeEditFile()
    assert _find_shirt_number_conflict(edit_file, 100, 10, 12) == 20
    assert _find_shirt_number_conflict(edit_file, 100, 20, 12) is None
    assert _find_shirt_number_conflict(edit_file, 100, 10, 7) is None


def test_team_matching_uses_full_name_and_rejects_conflicts():
    class FakeMatcher:
        def match_team(self, name):
            return {
                "Paris Saint-Germain": (114, "Paris Saint-Germain", 100.0),
                "PSG": (114, "Paris Saint-Germain", 100.0),
                "Conflicting short": (999, "Wrong Club", 100.0),
                "Similar Club": (114, "Paris Saint-Germain", 90.0),
                "Ambiguous Club": (None, "", 98.0),
            }.get(name, (None, "", 0.0))

    matcher = FakeMatcher()
    assert _match_transfer_team(matcher, "PSG", "Paris Saint-Germain") == (
        114,
        "Paris Saint-Germain",
        100.0,
    )
    assert _match_transfer_team(
        matcher, "Conflicting short", "Paris Saint-Germain"
    )[0] == -1
    # A FotMob club whose save-name match is already bound to another
    # FotMob club is a different club: unsure, never a confident match.
    assert _match_transfer_team(
        matcher,
        "PSG",
        "Paris Saint-Germain",
        fotmob_id=9847,
        club_identity=_identity({1234: 114}, matcher),
    ) == (-1, "", 0.0)
    assert _match_transfer_team(matcher, "Similar Club")[0] == -1
    assert _match_transfer_team(matcher, "Ambiguous Club")[0] == -1
    assert _match_transfer_team(
        matcher,
        "Free Agent",
        "Free Agent",
        fotmob_id=2,
        club_identity=_identity({1234: 114}, matcher),
    ) == (None, "", 100.0)

    assert _match_transfer_team(
        matcher,
        "Barcelona",
        "Barcelona",
        fotmob_id=8634,
        club_identity=_identity({8634: 108}, matcher),
    ) == (108, "Barcelona", 100.0)


def test_validated_fotmob_team_id_skips_ambiguous_name_lookup():
    class IdOnlyMatcher:
        def match_team(self, _name):
            raise AssertionError("validated team IDs must bypass name matching")

    assert _match_transfer_team(
        IdOnlyMatcher(),
        "Barcelona",
        "Barcelona",
        fotmob_id=8634,
        club_identity=_identity({8634: 108}),
    ) == (108, "Barcelona", 100.0)

    unresolved = MatchedTransfer(
        transfer=Transfer("Player", "A", "B"),
        player_id=1,
        from_team_id=10,
        to_team_id=-1,
    )
    assert unresolved.is_fully_matched is False


def test_match_and_plan_retains_partial_matches_for_safety_accounting(
    monkeypatch, tmp_path
):
    import run as run_module

    class FakeMatcher:
        def match_team(self, name):
            if name == "Destination":
                return 102, "Destination", 100.0
            return None, "", 100.0

        def match_player(self, *args, **kwargs):
            return None, "", 0.0

        def get_team_name(self, team_id):
            return "Destination" if team_id == 102 else ""

    monkeypatch.setattr(run_module.transfer_logger, "read_log", lambda **kwargs: [])
    transfer = Transfer("Unknown Player", "Free Agent", "Destination")
    roster_plan, fully_matched, _ = _match_and_plan_transfers(
        [transfer],
        FakeMatcher(),
        80,
        {102: [0] * 40},
        {102: SimpleNamespace(player_ids=[0] * 40)},
        {102},
        SimpleNamespace(player_catalog_report=None),
        tmp_path / "EDIT00000000",
        club_identity=None,
        report=PlanningReport(),
        allow_overflow_release=False,
    )

    assert fully_matched == []
    assert [(item.action, item.reason) for item in roster_plan] == [
        ("skip", "player_not_matched")
    ]

def test_planner_never_mutates_unresolved_team_identity():
    partial = MatchedTransfer(
        transfer=Transfer("Known Player", "Unknown Source", "Destination"),
        player_id=1,
        from_team_id=-1,
        to_team_id=30,
        player_confidence=100.0,
        from_team_confidence=100.0,
        to_team_confidence=100.0,
    )

    plan = _plan_roster_actions(
        [partial],
        {
            20: SimpleNamespace(player_ids=list(range(1, 18)) + [0] * 23),
            30: SimpleNamespace(player_ids=[0] * 40),
        },
        {20, 30},
        SimpleNamespace(),
        {id(partial): frozenset({20})},
    )

    assert [(item.action, item.reason) for item in plan] == [
        ("skip", "source_team_not_matched")
    ]

def test_apply_counts_partial_skip_alongside_action(monkeypatch, tmp_path):
    import run as run_module
    from local_update import CancellationToken, LocalUpdateRequest

    skipped_match = MatchedTransfer(
        transfer=Transfer("Unknown Player", "Free Agent", "Destination"),
        player_id=None,
        from_team_id=None,
        to_team_id=20,
    )
    moved_match = MatchedTransfer(
        transfer=Transfer("Known Player", "Source", "Destination"),
        player_id=1,
        from_team_id=10,
        to_team_id=20,
        player_confidence=100.0,
        from_team_confidence=100.0,
        to_team_confidence=100.0,
    )

    class FakeEditFile:
        def __init__(self):
            self._data = bytearray(b"original")

        def move_player(self, *args, **kwargs):
            return True

    prepared = _runtime_prepared(
        tmp_path,
        FakeEditFile(),
        roster_plan=[
            PlannedRosterAction(skipped_match, "skip", None, "player_not_matched"),
            PlannedRosterAction(moved_match, "move", 10),
        ],
    )
    monkeypatch.setattr(
        run_module.backup_mod,
        "create_backup",
        lambda path: tmp_path / "backup",
    )

    result = _RunLocalUpdateRuntime().apply(
        LocalUpdateRequest(prepared.edit_path),
        prepared,
        prepared.roster_plan,
        CancellationToken(),
    )

    assert result.transfer_applied == 1
    assert result.safety_skipped == 1


def test_local_runtime_safety_skips_known_move_state_failure(
    monkeypatch, tmp_path
):
    import run as run_module
    from local_update import CancellationToken, LocalUpdateRequest

    first_match = _club_match(source=10, destination=20, date="2026-08-02")
    second_match = MatchedTransfer(
        transfer=Transfer("Second Player", "Source", "Destination"),
        player_id=2,
        from_team_id=10,
        to_team_id=20,
        player_confidence=100.0,
        from_team_confidence=100.0,
        to_team_confidence=100.0,
    )

    class FakeEditFile:
        def __init__(self):
            self._data = bytearray(b"original")
            self.calls = 0
            self.last_mutation_error_code = None
            self.last_mutation_error = ""

        def move_player(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 1:
                self.last_mutation_error_code = "source_player_missing"
                self.last_mutation_error = "Player 115254 not found on team 10"
                return False
            self.last_mutation_error_code = None
            self.last_mutation_error = ""
            return True

    prepared = _runtime_prepared(
        tmp_path,
        FakeEditFile(),
        roster_plan=(
            PlannedRosterAction(first_match, "move", 10),
            PlannedRosterAction(second_match, "move", 10),
        ),
        club_ids={10, 20},
    )
    monkeypatch.setattr(
        run_module.backup_mod,
        "create_backup",
        lambda path: tmp_path / "backup",
    )

    result = _RunLocalUpdateRuntime().apply(
        LocalUpdateRequest(prepared.edit_path),
        prepared,
        prepared.roster_plan,
        CancellationToken(),
    )

    assert result.transfer_applied == 1
    assert result.safety_skipped == 1
    assert prepared.edit_file.calls == 2
    # Editor refusals are reported as not-applied transfers with their code.
    assert [
        (row["reason"], row["relevant"], row["detail"])
        for row in prepared.skipped_rows()
    ] == [
        ("source_player_missing", True, "Player 115254 not found on team 10")
    ]


def test_stateful_matching_keeps_identity_across_loan_chain():
    from scraper.matcher import NameMatcher

    matcher = NameMatcher()
    matcher.load_player_db([("Patrick", 3001), ("Patrick", 3002)])
    matcher.load_team_db({"Parent FC": 10, "Loan FC": 20, "Next FC": 30})
    transfers = [
        Transfer(
            "Patrick",
            "Parent FC",
            "Loan FC",
            date="2025-09-01T10:00:00Z",
            transfer_type="loan",
            is_loan=True,
        ),
        Transfer(
            "Patrick",
            "Parent FC",
            "Next FC",
            date="2026-08-02T10:00:00Z",
        ),
    ]

    matched = _match_transfers_statefully(
        transfers,
        matcher,
        80,
        {10: [3001], 20: [], 30: [], 40: [3002]},
        {10, 20, 30, 40},
    )

    assert [item.player_id for item in matched] == [3001, 3001]


def test_stateful_matching_moves_unique_current_squad_registration():
    from scraper.matcher import NameMatcher

    matcher = NameMatcher()
    matcher.load_player_db([("Anderson Lopes", 3001)])
    matcher.load_team_db({"Old Club": 10, "Vissel Kobe": 20})
    source_url = "https://www.fotmob.com/api/data/teams?id=4688"
    registration = Transfer(
        "Anderson Lopes",
        "",
        "Vissel Kobe",
        transfer_type="squad_registration",
        to_club_id_fotmob=4688,
        player_id_fotmob=498456,
        source_urls=(source_url,),
        proof_urls=(source_url,),
        verification_status="enabled",
        infer_from_current_roster=True,
    )

    matched = _match_transfers_statefully(
        [registration],
        matcher,
        80,
        {10: [3001] + list(range(3002, 3019)), 20: []},
        {10, 20},
        club_identity=_identity({4688: 20}, matcher),
    )

    assert matched[0].player_id == 3001
    assert matched[0].from_team_id == 10
    assert matched[0].to_team_id == 20
    plan = _plan_roster_actions(
        matched,
        {
            10: TeamData(10, [3001] + list(range(3002, 3019)) + [0] * 23),
            20: TeamData(20, [0] * 40),
        },
        {10, 20},
        object(),
        {},
    )
    assert [(item.action, item.current_team_id) for item in plan] == [("move", 10)]

def test_stateful_matching_uses_snapshot_identity_map_for_current_move():
    from scraper.matcher import NameMatcher

    matcher = NameMatcher()
    matcher.load_player_db(
        [
            ("Anderson Lopes", 3001),
            *[
                (f"Source Player {player_id}", player_id)
                for player_id in range(3002, 3019)
            ],
        ]
    )
    matcher.load_team_db({"Old Club": 10, "Vissel Kobe": 20})
    source_snapshot = SquadSnapshot(
        club_name="Old Club",
        team_id_fotmob=777,
        members=tuple(
            SquadMember(
                f"Source Player {player_id}",
                player_id_fotmob=6000 + player_id,
            )
            for player_id in range(3002, 3013)
        ),
        source_url="https://www.fotmob.com/api/data/teams?id=777",
        complete=True,
    )
    destination_snapshot = SquadSnapshot(
        club_name="Vissel Kobe",
        team_id_fotmob=4688,
        members=(
            SquadMember("Anderson Lopes", player_id_fotmob=498456),
            *(
                SquadMember(f"Snapshot Player {index}")
                for index in range(10)
            ),
        ),
        source_url="https://www.fotmob.com/api/data/teams?id=4688",
        complete=True,
    )

    matched = _match_transfers_statefully(
        [],
        matcher,
        80,
        {10: [3001] + list(range(3002, 3019)), 20: []},
        {10, 20},
        club_identity=_identity({4688: 20, 777: 10}, matcher),
        squad_snapshots=(source_snapshot, destination_snapshot),
    )

    assert len(matched) == 1
    assert matched[0].transfer.transfer_type == "squad_registration"
    assert matched[0].player_id == 3001
    assert matched[0].from_team_id == 10
    assert matched[0].to_team_id == 20



def test_fast_snapshot_move_allows_uncovered_source_and_shirt_identity():
    from scraper.matcher import NameMatcher

    source_ids = [3001, *range(3002, 3019)]
    destination_ids = list(range(4001, 4017))
    matcher = NameMatcher()
    matcher.load_player_db(
        [
            ("Current Player", 3001),
            *[
                (f"Source Player {player_id}", player_id)
                for player_id in source_ids[1:]
            ],
            *[
                (f"Destination Player {player_id}", player_id)
                for player_id in destination_ids
            ],
        ]
    )
    matcher.load_team_db({"Source FC": 10, "Destination FC": 20})
    snapshot = SquadSnapshot(
        club_name="Destination FC",
        team_id_fotmob=200,
        members=(
            SquadMember("Current Player", player_id_fotmob=9001, shirt_number=7),
            *(
                SquadMember(
                    f"Destination Player {player_id}",
                    player_id_fotmob=9001 + index,
                )
                for index, player_id in enumerate(destination_ids, 1)
            ),
        ),
        source_url="https://www.fotmob.com/api/data/teams?id=200",
        complete=True,
    )
    shirt_update = Transfer(
        "Current Player",
        "Destination FC",
        "Destination FC",
        transfer_type="shirt_number_update",
        shirt_number=7,
        from_club_id_fotmob=200,
        to_club_id_fotmob=200,
        player_id_fotmob=9001,
    )

    matched = _match_transfers_statefully(
        [shirt_update],
        matcher,
        80,
        {10: source_ids, 20: destination_ids},
        {10, 20},
        club_identity=_identity({200: 20}, matcher),
        squad_snapshots=(snapshot,),
        allow_uncovered_source=True,
    )

    assert [
        (
            item.transfer.transfer_type,
            item.player_id,
            item.from_team_id,
            item.to_team_id,
            item.transfer.shirt_number,
        )
        for item in matched
    ] == [
        ("squad_registration", 3001, 10, 20, 7),
        ("shirt_number_update", 3001, 20, 20, 7),
    ]

def test_current_squad_registers_catalog_player_missing_from_local_roster():
    from scraper.matcher import NameMatcher

    existing_ids = list(range(3001, 3026))
    matcher = NameMatcher()
    matcher.load_player_db(
        [
            ("Missing Catalog Player", 3999),
            *[
                (f"Destination Player {player_id}", player_id)
                for player_id in existing_ids
            ],
        ]
    )
    matcher.load_team_db({"Destination FC": 20})
    snapshot = SquadSnapshot(
        club_name="Destination FC",
        team_id_fotmob=200,
        members=(
            SquadMember("Missing Catalog Player", player_id_fotmob=9001),
            *[
                SquadMember(
                    f"Destination Player {player_id}",
                    player_id_fotmob=9001 + index,
                )
                for index, player_id in enumerate(existing_ids, 1)
            ],
        ),
        source_url="https://www.fotmob.com/api/data/teams?id=200",
        complete=True,
    )

    matched = _match_transfers_statefully(
        [],
        matcher,
        80,
        {20: existing_ids},
        {20},
        club_identity=_identity({200: 20}, matcher),
        squad_snapshots=(snapshot,),
    )

    assert [
        (item.transfer.transfer_type, item.player_id, item.from_team_id, item.to_team_id)
        for item in matched
    ] == [("squad_registration", 3999, None, 20)]
    plan = _plan_roster_actions(
        matched,
        {20: TeamData(20, existing_ids + [0] * 15)},
        {20},
        object(),
        {},
    )
    assert [(item.action, item.current_team_id) for item in plan] == [("add", None)]

def test_current_squad_registers_catalog_player_with_stale_roster_extras():
    from scraper.matcher import NameMatcher

    existing_ids = list(range(3001, 3026))
    stale_ids = list(range(5001, 5011))
    matcher = NameMatcher()
    matcher.load_player_db(
        [
            ("Missing Catalog Player", 3999),
            *[
                (f"Destination Player {player_id}", player_id)
                for player_id in existing_ids
            ],
            *[
                (f"Stale Academy Player {player_id}", player_id)
                for player_id in stale_ids
            ],
        ]
    )
    matcher.load_team_db({"Destination FC": 20})
    snapshot = SquadSnapshot(
        club_name="Destination FC",
        team_id_fotmob=200,
        members=(
            SquadMember("Missing Catalog Player", player_id_fotmob=9001),
            *[
                SquadMember(
                    f"Destination Player {player_id}",
                    player_id_fotmob=9001 + index,
                )
                for index, player_id in enumerate(existing_ids, 1)
            ],
        ),
        source_url="https://www.fotmob.com/api/data/teams?id=200",
        complete=True,
    )

    matched = _match_transfers_statefully(
        [],
        matcher,
        80,
        {20: existing_ids + stale_ids},
        {20},
        club_identity=_identity({200: 20}, matcher),
        squad_snapshots=(snapshot,),
    )

    assert [
        (item.transfer.transfer_type, item.player_id, item.from_team_id, item.to_team_id)
        for item in matched
    ] == [("squad_registration", 3999, None, 20)]

def test_current_squad_moves_from_uncovered_source_with_healthy_destination():
    from scraper.matcher import NameMatcher

    source_player_id = 3001
    destination_ids = list(range(4001, 4017))
    matcher = NameMatcher()
    matcher.load_player_db(
        [
            ("Current Player", source_player_id),
            *[
                (f"Destination Player {player_id}", player_id)
                for player_id in destination_ids
            ],
        ]
    )
    matcher.load_team_db({"Source FC": 10, "Destination FC": 20})
    snapshot = SquadSnapshot(
        club_name="Destination FC",
        team_id_fotmob=200,
        members=(
            SquadMember("Current Player", player_id_fotmob=9001),
            *[
                SquadMember(
                    f"Destination Player {player_id}",
                    player_id_fotmob=9001 + index,
                )
                for index, player_id in enumerate(destination_ids, 1)
            ],
        ),
        source_url="https://www.fotmob.com/api/data/teams?id=200",
        complete=True,
    )

    matched = _match_transfers_statefully(
        [],
        matcher,
        80,
        {10: [source_player_id], 20: destination_ids},
        {10, 20},
        club_identity=_identity({200: 20}, matcher),
        squad_snapshots=(snapshot,),
    )

    assert [
        (item.transfer.transfer_type, item.player_id, item.from_team_id, item.to_team_id)
        for item in matched
    ] == [("squad_registration", source_player_id, 10, 20)]


def test_snapshot_identity_collision_keeps_current_roster_anchor():
    from scraper.matcher import NameMatcher

    source_ids = list(range(3001, 3018))
    destination_ids = list(range(4001, 4017))
    matcher = NameMatcher()
    matcher.load_player_db(
        [
            ("Allan", 3001),
            *[
                (f"Source Player {player_id}", player_id)
                for player_id in source_ids[1:]
            ],
            *[
                (f"Destination Player {player_id}", player_id)
                for player_id in destination_ids
            ],
        ]
    )
    matcher.load_team_db({"Lanus": 10, "Manchester City": 20})
    source_snapshot = SquadSnapshot(
        club_name="Lanus",
        team_id_fotmob=100,
        members=(
            SquadMember("Allan", player_id_fotmob=9000),
            *[
                SquadMember(
                    f"Source Player {player_id}",
                    player_id_fotmob=9100 + index,
                )
                for index, player_id in enumerate(source_ids[1:], 1)
            ],
        ),
        source_url="https://www.fotmob.com/api/data/teams?id=100",
        complete=True,
    )
    destination_snapshot = SquadSnapshot(
        club_name="Manchester City",
        team_id_fotmob=200,
        members=(
            SquadMember("Allan", player_id_fotmob=9001),
            *[
                SquadMember(
                    f"Destination Player {player_id}",
                    player_id_fotmob=9200 + index,
                )
                for index, player_id in enumerate(destination_ids, 1)
            ],
        ),
        source_url="https://www.fotmob.com/api/data/teams?id=200",
        complete=True,
    )

    matched = _match_transfers_statefully(
        [],
        matcher,
        80,
        {10: source_ids, 20: destination_ids},
        {10, 20},
        club_identity=_identity({100: 10, 200: 20}, matcher),
        squad_snapshots=(source_snapshot, destination_snapshot),
    )

    assert matched == []





def test_snapshot_prefers_club_roster_over_unrelated_exact_name():
    from scraper.matcher import NameMatcher

    current_ids = [4001, *range(4002, 4018)]
    matcher = NameMatcher()
    matcher.load_player_db(
        [
            ("Endrick Felipe", 4001),
            ("Endrick", 5001),
            *[
                (f"Player {player_id}", player_id)
                for player_id in current_ids[1:]
            ],
        ]
    )
    matcher.load_team_db({"Real Madrid": 10, "Malaysia": 20})
    snapshot = SquadSnapshot(
        club_name="Real Madrid",
        team_id_fotmob=8633,
        members=(
            SquadMember("Endrick", player_id_fotmob=9001),
            *(
                SquadMember(
                    f"Player {player_id}",
                    player_id_fotmob=9000 + index,
                )
                for index, player_id in enumerate(current_ids[1:], 1)
            ),
        ),
        source_url="https://www.fotmob.com/api/data/teams?id=8633",
        complete=True,
    )

    matched = _match_transfers_statefully(
        [],
        matcher,
        80,
        {10: current_ids, 20: [5001]},
        {10, 20},
        club_identity=_identity({8633: 10}, matcher),
        squad_snapshots=(snapshot,),
    )

    assert matched == []


def test_snapshot_short_name_prefers_local_long_identity_over_catalog_name():
    from scraper.matcher import NameMatcher
    from transfer_planning import _match_snapshot_member

    matcher = NameMatcher()
    matcher.load_player_db(
        [
            ("Gabriel", 90866),
            ("Gabriel Magalhães", 111207),
        ],
        positions={111207: "CB"},
        ages={111207: 28},
    )
    member = SquadMember("Gabriel", position="CB", age=28)

    player_id, player_name, confidence = _match_snapshot_member(
        matcher,
        member,
        10,
        {10: [111207], 20: []},
        80,
    )

    assert (player_id, player_name) == (111207, "Gabriel Magalhães")
    assert confidence == 100.0


def test_snapshot_global_fuzzy_rejects_unsafe_near_name():
    from scraper.matcher import NameMatcher
    from transfer_planning import _match_snapshot_member

    matcher = NameMatcher()
    matcher.load_player_db(
        [
            ("Peque Fernández", 131798),
            ("Ezequiel Fernández", 140305),
        ]
    )

    player_id, player_name, confidence = _match_snapshot_member(
        matcher,
        SquadMember("Equi Fernández", position="CDM", age=24),
        10,
        {10: [], 20: [131798]},
        80,
    )

    assert (player_id, player_name) == (None, "")
    assert confidence < 95


def test_stateful_matching_rejects_stale_provider_identity_with_conflicting_metadata():
    from scraper.matcher import NameMatcher

    matcher = NameMatcher()
    matcher.load_player_db(
        [("Brian Arias", 90158)],
        positions={90158: "CB"},
        ages={90158: 19},
    )
    matcher.load_team_db({"Old FC": 10, "New FC": 20})
    transfer = Transfer(
        "Brian Fariñas",
        "Old FC",
        "New FC",
        player_id_fotmob=777,
        position="CM",
        age=20,
    )
    history = [
        {
            "player_id": 90158,
            "fotmob_player_id": 777,
            "player_name": "Brian Arias",
            "from_team_id": 10,
            "to_team_id": 20,
            "transfer_type": "transfer",
            "transfer_date": "2026-08-01",
        }
    ]

    matched = _match_transfers_statefully(
        [transfer],
        matcher,
        80,
        {10: [90158], 20: []},
        {10, 20},
        history,
    )

    assert len(matched) == 1
    assert matched[0].player_id is None



def test_unhealthy_snapshot_skips_inferred_moves_and_releases():
    from scraper.matcher import NameMatcher

    source_ids = list(range(5001, 5039))
    destination_ids = list(range(6001, 6017))
    matcher = NameMatcher()
    matcher.load_player_db(
        [
            ("Move Me", 5001),
            *[
                (f"Source Player {player_id}", player_id)
                for player_id in source_ids[1:]
            ],
            *[
                (f"Destination Player {player_id}", player_id)
                for player_id in destination_ids
            ],
        ]
    )
    matcher.load_team_db({"Source FC": 10, "Destination FC": 20})
    source_snapshot = SquadSnapshot(
        club_name="Source FC",
        team_id_fotmob=100,
        members=tuple(
            SquadMember(
                f"Source Player {player_id}",
                player_id_fotmob=7000 + index,
            )
            for index, player_id in enumerate(source_ids[1:26], 1)
        ),
        source_url="https://www.fotmob.com/api/data/teams?id=100",
        complete=True,
    )
    destination_snapshot = SquadSnapshot(
        club_name="Destination FC",
        team_id_fotmob=200,
        members=(
            SquadMember("Move Me", player_id_fotmob=9001),
            *(
                SquadMember(
                    f"Destination Player {player_id}",
                    player_id_fotmob=9001 + index,
                )
                for index, player_id in enumerate(destination_ids[:10], 1)
            ),
        ),
        source_url="https://www.fotmob.com/api/data/teams?id=200",
        complete=True,
    )

    matched = _match_transfers_statefully(
        [],
        matcher,
        80,
        {10: source_ids, 20: destination_ids},
        {10, 20},
        club_identity=_identity({100: 10, 200: 20}, matcher),
        squad_snapshots=(source_snapshot, destination_snapshot),
        player_names={
            player_id: (
                "Move Me"
                if player_id == 5001
                else f"Source Player {player_id}"
                if player_id in source_ids
                else f"Destination Player {player_id}"
            )
            for player_id in [*source_ids, *destination_ids]
        },
    )

    assert matched == []

def test_stateful_matching_rejects_same_name_player_from_another_age_group():
    from scraper.matcher import NameMatcher

    matcher = NameMatcher()
    matcher.load_player_db(
        {"Reece James": 126046},
        positions={126046: "RB"},
        ages={126046: 26},
    )
    matcher.load_team_db({"Chelsea": 102, "Sheffield Wednesday": 394})
    registration = Transfer(
        "Reece James",
        "",
        "Sheffield Wednesday",
        transfer_type="squad_registration",
        position="Defender",
        age=32,
        to_club_id_fotmob=10163,
        player_id_fotmob=463871,
        source_urls=("https://www.fotmob.com/api/data/teams?id=10163",),
        proof_urls=("https://www.fotmob.com/api/data/teams?id=10163",),
        verification_status="enabled",
        infer_from_current_roster=True,
    )

    matched = _match_transfers_statefully(
        [registration],
        matcher,
        80,
        {102: [126046], 394: []},
        {102, 394},
        club_identity=_identity({10163: 394}, matcher),
    )

    assert matched[0].player_id is None
    assert matched[0].is_fully_matched is False


def test_stateful_matching_rejects_duplicate_name_registration_chain():
    from scraper.matcher import NameMatcher

    matcher = NameMatcher()
    matcher.load_player_db(
        players=[("João Pedro", 3001), ("João Pedro", 3002), ("João Pedro", 3003)],
        positions={3001: "CF"},
        ages={3001: 24},
    )
    matcher.load_team_db({"Chelsea": 10, "Casa Pia": 20, "Sao Bernardo": 30})
    registrations = [
        Transfer(
            "João Pedro",
            "",
            "Chelsea",
            transfer_type="squad_registration",
            position="Attacker",
            age=24,
            to_club_id_fotmob=100,
            player_id_fotmob=1001,
            proof_urls=("https://example.test/chelsea",),
            verification_status="enabled",
            infer_from_current_roster=True,
        ),
        Transfer(
            "João Pedro",
            "",
            "Casa Pia",
            transfer_type="squad_registration",
            position="Midfielder",
            age=21,
            to_club_id_fotmob=200,
            player_id_fotmob=1002,
            proof_urls=("https://example.test/casa-pia",),
            verification_status="enabled",
            infer_from_current_roster=True,
        ),
        Transfer(
            "João Pedro",
            "",
            "Sao Bernardo",
            transfer_type="squad_registration",
            position="Defender",
            age=21,
            to_club_id_fotmob=300,
            player_id_fotmob=1003,
            proof_urls=("https://example.test/sao-bernardo",),
            verification_status="enabled",
            infer_from_current_roster=True,
        ),
    ]
    history = [
        {"player_id": 3001, "fotmob_player_id": 1001},
        {"player_id": 3001, "fotmob_player_id": 1002},
        {"player_id": 3001, "fotmob_player_id": 1003},
    ]

    matched = _match_transfers_statefully(
        registrations,
        matcher,
        80,
        {10: [3001], 20: [], 30: []},
        {10, 20, 30},
        club_identity=_identity({100: 10, 200: 20, 300: 30}, matcher),
        historical_entries=history,
        player_names={3001: "João Pedro"},
    )

    assert [item.player_id for item in matched] == [3001, None, None]


def test_complete_squad_snapshot_releases_stale_current_roster_player():
    from scraper.matcher import NameMatcher

    current_ids = list(range(3001, 3018))
    matcher = NameMatcher()
    matcher.load_player_db(
        [(f"Player {player_id}", player_id) for player_id in current_ids]
    )
    matcher.load_team_db({"Example FC": 10})
    snapshot = SquadSnapshot(
        club_name="Example FC",
        team_id_fotmob=42,
        members=tuple(
            SquadMember(
                player_name=f"Player {player_id}",
                player_id_fotmob=5000 + index,
            )
            for index, player_id in enumerate(current_ids[:-1])
        ),
        source_url="https://www.fotmob.com/api/data/teams?id=42",
        complete=True,
    )

    matched = _match_transfers_statefully(
        [],
        matcher,
        80,
        {10: current_ids},
        {10},
        club_identity=_identity({42: 10}, matcher),
        squad_snapshots=(snapshot,),
        player_names={player_id: f"Player {player_id}" for player_id in current_ids},
    )

    assert [
        (match.player_id, match.transfer.transfer_type)
        for match in matched
    ] == [(3017, "squad_release")]
    plan = _plan_roster_actions(
        matched,
        {10: TeamData(10, current_ids + [0] * 23)},
        {10},
        object(),
        {},
    )
    assert [(item.action, item.current_team_id) for item in plan] == [
        ("release", 10)
    ]

def test_snapshot_exact_name_survives_position_label_mismatch():
    from scraper.matcher import NameMatcher

    baturina_id = 141911
    current_ids = [baturina_id, *range(3001, 3017)]
    matcher = NameMatcher()
    matcher.load_player_db(
        [
            ("Martin Baturina", baturina_id),
            *[(f"Player {player_id}", player_id) for player_id in current_ids[1:]],
        ],
        positions={baturina_id: "AMF"},
        ages={baturina_id: 22},
    )
    matcher.load_team_db({"Example FC": 10})
    snapshot = SquadSnapshot(
        club_name="Example FC",
        team_id_fotmob=42,
        members=(
            SquadMember(
                player_name="Martin Baturina",
                player_id_fotmob=9001,
                position="LW",
                age=23,
            ),
            *(
                SquadMember(
                    player_name=f"Player {player_id}",
                    player_id_fotmob=9002 + index,
                )
                for index, player_id in enumerate(current_ids[1:-1])
            ),
        ),
        source_url="https://www.fotmob.com/api/data/teams?id=42",
        complete=True,
    )

    matched = _match_transfers_statefully(
        [],
        matcher,
        80,
        {10: current_ids},
        {10},
        club_identity=_identity({42: 10}, matcher),
        squad_snapshots=(snapshot,),
        player_names={player_id: f"Player {player_id}" for player_id in current_ids},
    )

    assert [
        (match.player_id, match.transfer.transfer_type)
        for match in matched
    ] == [(3016, "squad_release")]


def test_snapshot_short_aliases_use_local_shirt_and_position_before_releases():
    from scraper.matcher import NameMatcher

    allan_id = 35001
    wrong_savio_id = 35002
    savinho_id = 35003
    city_extras = list(range(35100, 35115))
    spurs_extras = list(range(35200, 35215))
    unrelated_allan_id = 35300
    city_ids = [allan_id, *city_extras]
    spurs_ids = [wrong_savio_id, savinho_id, *spurs_extras]
    player_names = {
        allan_id: "Allan Andrade",
        wrong_savio_id: "Sávio",
        savinho_id: "Savinho",
        unrelated_allan_id: "Allan",
        **{
            player_id: f"City Player {index}"
            for index, player_id in enumerate(city_extras)
        },
        **{
            player_id: f"Spurs Player {index}"
            for index, player_id in enumerate(spurs_extras)
        },
    }
    matcher = NameMatcher()
    matcher.load_player_db(
        [(name, player_id) for player_id, name in player_names.items()],
        positions={
            allan_id: "AMF",
            wrong_savio_id: "RB",
            savinho_id: "LWF",
            unrelated_allan_id: "RW",
            **{
                player_id: "CM"
                for player_id in [*city_extras, *spurs_extras]
            },
        },
        ages={
            allan_id: 21,
            wrong_savio_id: 22,
            savinho_id: 20,
            unrelated_allan_id: 35,
            **{player_id: 25 for player_id in [*city_extras, *spurs_extras]},
        },
    )
    matcher.load_team_db({"Manchester City": 10, "Tottenham": 20, "Other": 30})
    city_snapshot = SquadSnapshot(
        club_name="Manchester City",
        team_id_fotmob=42,
        members=(
            SquadMember("Allan", 9001, "RW", age=22, shirt_number=37),
            *(
                SquadMember(
                    player_names[player_id],
                    9100 + index,
                    "CM",
                    age=25,
                    shirt_number=index + 1,
                )
                for index, player_id in enumerate(city_extras)
            ),
        ),
        source_url="https://www.fotmob.com/api/data/teams?id=42",
        complete=True,
    )
    spurs_snapshot = SquadSnapshot(
        club_name="Tottenham",
        team_id_fotmob=43,
        members=(
            SquadMember("Sávio", 9201, "RW", age=22, shirt_number=17),
            *(
                SquadMember(
                    player_names[player_id],
                    9300 + index,
                    "CM",
                    age=25,
                    shirt_number=index + 1,
                )
                for index, player_id in enumerate(spurs_extras)
            ),
        ),
        source_url="https://www.fotmob.com/api/data/teams?id=43",
        complete=True,
    )
    team_player_map = {
        10: city_ids,
        20: spurs_ids,
        30: [unrelated_allan_id],
    }
    team_shirt_numbers = {
        10: {
            allan_id: 37,
            **{
                player_id: index + 1
                for index, player_id in enumerate(city_extras)
            },
        },
        20: {
            wrong_savio_id: 2,
            savinho_id: 17,
            **{player_id: index + 1 for index, player_id in enumerate(spurs_extras)},
        },
        30: {unrelated_allan_id: 10},
    }

    matched = _match_transfers_statefully(
        [],
        matcher,
        80,
        team_player_map,
        {10, 20, 30},
        club_identity=_identity({42: 10, 43: 20}, matcher),
        squad_snapshots=(city_snapshot, spurs_snapshot),
        player_names=player_names,
        team_shirt_numbers=team_shirt_numbers,
    )

    assert [
        (match.player_id, match.transfer.transfer_type)
        for match in matched
    ] == [(wrong_savio_id, "squad_release")]



def test_snapshot_does_not_release_player_from_current_transfer_event():
    from scraper.matcher import NameMatcher

    source_ids = list(range(3301, 3314))
    destination_ids = list(range(3401, 3417))
    incoming_id = source_ids[0]
    matcher = NameMatcher()
    matcher.load_player_db(
        [
            (f"Player {player_id}", player_id)
            for player_id in [*source_ids, *destination_ids]
        ]
    )
    matcher.load_team_db({"Source FC": 10, "Destination FC": 20})
    transfer = Transfer(
        player_name=f"Player {incoming_id}",
        from_club="Source FC",
        to_club="Destination FC",
        date="2026-09-01",
        transfer_type="transfer",
    )
    snapshot = SquadSnapshot(
        club_name="Destination FC",
        team_id_fotmob=42,
        members=tuple(
            SquadMember(
                player_name=f"Player {player_id}",
                player_id_fotmob=9500 + index,
            )
            for index, player_id in enumerate(destination_ids)
        ),
        source_url="https://www.fotmob.com/api/data/teams?id=42",
        complete=True,
    )

    matched = _match_transfers_statefully(
        [transfer],
        matcher,
        80,
        {10: source_ids, 20: destination_ids},
        {10, 20},
        club_identity=_identity({42: 20}, matcher),
        squad_snapshots=(snapshot,),
        player_names={
            player_id: f"Player {player_id}"
            for player_id in [*source_ids, *destination_ids]
        },
    )

    assert [
        (match.player_id, match.transfer.transfer_type)
        for match in matched
    ] == [(incoming_id, "transfer")]


def test_squad_releases_precede_shirt_updates():
    from scraper.matcher import NameMatcher

    current_ids = list(range(3301, 3319))
    matcher = NameMatcher()
    matcher.load_player_db(
        [(f"Player {player_id}", player_id) for player_id in current_ids]
    )
    matcher.load_team_db({"Example FC": 10})
    snapshot = SquadSnapshot(
        club_name="Example FC",
        team_id_fotmob=42,
        members=tuple(
            SquadMember(
                player_name=f"Player {player_id}",
                player_id_fotmob=9000 + index,
            )
            for index, player_id in enumerate(current_ids[:-1], 1)
        ),
        source_url="https://www.fotmob.com/api/data/teams?id=42",
        complete=True,
    )
    shirt_update = Transfer(
        "Player 3301",
        "Example FC",
        "Example FC",
        transfer_type="shirt_number_update",
        shirt_number=17,
        from_club_id_fotmob=42,
        to_club_id_fotmob=42,
        player_id_fotmob=9001,
    )

    matched = _match_transfers_statefully(
        [shirt_update],
        matcher,
        80,
        {10: current_ids},
        {10},
        club_identity=_identity({42: 10}, matcher),
        squad_snapshots=(snapshot,),
        player_names={player_id: f"Player {player_id}" for player_id in current_ids},
    )

    assert [
        (match.player_id, match.transfer.transfer_type)
        for match in matched
    ] == [(3318, "squad_release"), (3301, "shirt_number_update")]

def test_squad_snapshot_falls_back_when_historical_identity_is_stale():
    from scraper.matcher import NameMatcher

    current_ids = list(range(3201, 3218))
    stale_external_id = 5000
    matcher = NameMatcher()
    matcher.load_player_db(
        [(f"Player {player_id}", player_id) for player_id in current_ids]
    )
    matcher.load_team_db({"Example FC": 10})
    snapshot = SquadSnapshot(
        club_name="Example FC",
        team_id_fotmob=42,
        members=tuple(
            SquadMember(
                player_name=f"Player {player_id}",
                player_id_fotmob=(
                    stale_external_id
                    if player_id == current_ids[-1]
                    else 6000 + index
                ),
            )
            for index, player_id in enumerate(current_ids)
        ),
        source_url="https://www.fotmob.com/api/data/teams?id=42",
        complete=True,
    )

    matched = _match_transfers_statefully(
        [],
        matcher,
        80,
        {10: current_ids},
        {10},
        historical_entries=[
            {"player_id": 9999, "fotmob_player_id": stale_external_id}
        ],
        club_identity=_identity({42: 10}, matcher),
        squad_snapshots=(snapshot,),
        player_names={player_id: f"Player {player_id}" for player_id in current_ids},
    )

    assert matched == []


def test_incomplete_squad_snapshot_does_not_release_roster_players():
    from scraper.matcher import NameMatcher

    current_ids = list(range(3101, 3118))
    matcher = NameMatcher()
    matcher.load_player_db(
        [(f"Player {player_id}", player_id) for player_id in current_ids]
    )
    matcher.load_team_db({"Example FC": 10})
    snapshot = SquadSnapshot(
        club_name="Example FC",
        team_id_fotmob=42,
        members=tuple(
            SquadMember(player_name=f"Player {player_id}")
            for player_id in current_ids[:10]
        ),
        source_url="https://www.fotmob.com/api/data/teams?id=42",
        complete=False,
    )

    matched = _match_transfers_statefully(
        [],
        matcher,
        80,
        {10: current_ids},
        {10},
        club_identity=_identity({42: 10}, matcher),
        squad_snapshots=(snapshot,),
    )

    assert matched == []


def test_local_runtime_rolls_back_unexpected_move_failure(
    monkeypatch, tmp_path
):
    import run as run_module
    from local_update import CancellationToken, LocalUpdateError, LocalUpdateRequest

    class FakeEditFile:
        def __init__(self):
            self._data = bytearray(b"mutated-before-failure")

        def move_player(self, *args, **kwargs):
            return False

    prepared = _runtime_prepared(
        tmp_path,
        FakeEditFile(),
        roster_plan=(
            PlannedRosterAction(
                _club_match(source=10, destination=20, date="2026-08-02"),
                "move",
                10,
            ),
        ),
    )
    monkeypatch.setattr(
        run_module.backup_mod,
        "create_backup",
        lambda path: tmp_path / "backup",
    )

    with pytest.raises(LocalUpdateError, match="entire batch rolled back"):
        _RunLocalUpdateRuntime().apply(
            LocalUpdateRequest(prepared.edit_path),
            prepared,
            prepared.roster_plan,
            CancellationToken(),
        )

    assert prepared.edit_file._data == bytearray(b"original")



def test_stateful_matching_reconciles_parent_sale_before_loan():
    from scraper.matcher import NameMatcher

    parent, loan_club, new_parent = 234, 382, 102
    player_id = 172021
    matcher = NameMatcher()
    matcher.load_player_db(
        [("Honest Ahanor", player_id)],
        positions={player_id: "CB"},
    )
    matcher.load_team_db(
        {
            "Atalanta": parent,
            "Crystal Palace": loan_club,
            "Chelsea": new_parent,
        }
    )
    transfers = [
        Transfer(
            "Honest Ahanor",
            "Atalanta",
            "Crystal Palace",
            date="2026-09-01T15:26:32Z",
            transfer_type="loan",
            is_loan=True,
            player_id_fotmob=1669629,
            position="CB",
        ),
        Transfer(
            "Honest Ahanor",
            "Atalanta",
            "Chelsea",
            date="2026-09-01T13:21:39Z",
            player_id_fotmob=1669629,
            position="CB",
        ),
    ]
    player_map = {
        parent: [player_id] + list(range(200001, 200017)),
        loan_club: list(range(300001, 300017)),
        new_parent: list(range(400001, 400017)),
    }

    matched = _match_transfers_statefully(
        transfers,
        matcher,
        80,
        player_map,
        set(player_map),
    )

    assert [
        (item.transfer.transfer_type, item.from_team_id, item.to_team_id)
        for item in matched
    ] == [
        ("transfer", parent, new_parent),
        ("loan", new_parent, loan_club),
    ]

    rosters = {
        team_id: TeamData(team_id, ids + [0] * (40 - len(ids)))
        for team_id, ids in player_map.items()
    }
    plan = _plan_roster_actions(
        matched,
        rosters,
        set(rosters),
        object(),
        {},
    )

    assert [
        (item.action, item.current_team_id)
        for item in plan
    ] == [
        ("move", parent),
        ("move", new_parent),
    ]


def test_stateful_matching_recovers_renamed_player_from_fotmob_history():
    from scraper.matcher import NameMatcher

    matcher = NameMatcher()
    matcher.load_player_db([("Legacy Database Name", 3001)])
    matcher.load_team_db({"Old FC": 10, "New FC": 20})
    transfer = Transfer(
        "Completely New Public Name",
        "Old FC",
        "New FC",
        player_id_fotmob=777,
    )
    history = [{
        "player_id": 3001,
        "from_team_id": "malformed-but-identity-still-valid",
        "fotmob_player_id": 777,
        "player_name": "Legacy Database Name",
    }]

    matched = _match_transfers_statefully(
        [transfer], matcher, 80, {10: [3001], 20: []}, {10, 20}, history
    )

    assert matched[0].player_id == 3001
    assert matched[0].player_confidence == 100.0


def test_stateful_matching_rejects_conflicting_fotmob_history():
    from scraper.matcher import NameMatcher

    matcher = NameMatcher()
    matcher.load_player_db([("First Player", 3001), ("Second Player", 3002)])
    matcher.load_team_db({"Old FC": 10, "New FC": 20})
    transfer = Transfer(
        "Unknown Alias", "Old FC", "New FC", player_id_fotmob=777
    )
    history = [
        {"player_id": 3001, "fotmob_player_id": 777},
        {"player_id": 3002, "fotmob_player_id": 777},
    ]

    matched = _match_transfers_statefully(
        [transfer], matcher, 80, {10: [3001], 20: [3002]}, {10, 20}, history
    )

    assert matched[0].player_id is None


def test_cli_date_validation_is_strict():
    assert _iso_date_arg("2026-08-03") == "2026-08-03"
    with pytest.raises(argparse.ArgumentTypeError):
        _iso_date_arg("03/08/2026")


def test_cli_threshold_validation_rejects_out_of_range():
    assert _percentage_arg("80") == 80.0
    with pytest.raises(argparse.ArgumentTypeError):
        _percentage_arg("101")


def test_cli_page_validation_rejects_zero():
    assert _positive_int_arg("3") == 3
    with pytest.raises(argparse.ArgumentTypeError):
        _positive_int_arg("0")


def _shirt_match(number: int, confidence: float) -> MatchedTransfer:
    return MatchedTransfer(
        transfer=Transfer(
            player_name="Player",
            from_club="Club",
            to_club="Club",
            transfer_type="shirt_number_update",
            shirt_number=number,
        ),
        player_id=100,
        from_team_id=10,
        to_team_id=10,
        player_confidence=confidence,
        from_team_confidence=100,
        to_team_confidence=100,
    )


def test_shirt_number_matches_are_eligible_for_roster_planning():
    match = _shirt_match(7, 100)
    planned = _plan_roster_actions(
        [match],
        {10: TeamData(10, [100] + [0] * 39)},
        {10},
        object(),
        {},
    )

    assert match.is_fully_matched
    assert planned[0].action == "shirt_update"
    assert planned[0].current_team_id == 10


def test_duplicate_shirt_matches_keep_stronger_observation():
    matches, skipped = _dedupe_shirt_number_matches([
        _shirt_match(7, 100),
        _shirt_match(7, 80),
    ])

    assert len(matches) == 1
    assert matches[0].transfer.shirt_number == 7
    assert skipped == 1


def test_ambiguous_shirt_numbers_fail_closed():
    matches, skipped = _dedupe_shirt_number_matches([
        _shirt_match(7, 100),
        _shirt_match(10, 98),
    ])

    assert matches == []
    assert skipped == 2


def test_default_run_continues_from_existing_output(monkeypatch, tmp_path):
    import config

    base = tmp_path / "base" / "EDIT00000000"
    output = tmp_path / "output" / "EDIT00000000"
    base.parent.mkdir()
    output.parent.mkdir()
    base.write_bytes(b"base")
    monkeypatch.setattr(config, "EDIT_FILE_PATH", base)
    monkeypatch.setattr(config, "OUTPUT_FILE_PATH", output)
    args = argparse.Namespace(
        edit_file=None,
        output=None,
        in_place=False,
        from_base=False,
    )

    assert _resolve_run_paths(args) == (base, output)
    output.write_bytes(b"updated")
    assert _resolve_run_paths(args) == (output, output)

    args.from_base = True
    assert _resolve_run_paths(args) == (base, output)


def test_competition_section_ends_where_game_plans_begin():
    fake_edit = type(
        "FakeEdit", (), {"competition_entry_start": 0xA08650, "game_plan_start": 0xA09880}
    )()

    start, end = _competition_section_bounds(fake_edit)

    assert start == 0xA08650
    assert end == fake_edit.game_plan_start


def test_transfer_run_syncs_squad_numbers_in_fast_mode(monkeypatch):
    import run_pipeline as run

    transfer = Transfer(
        "Player Two",
        "A",
        "B",
        from_club_id_fotmob=41,
        to_club_id_fotmob=42,
    )
    shirt = Transfer(
        "Squad Player",
        "B",
        "B",
        transfer_type="shirt_number_update",
        shirt_number=7,
    )
    captain = CaptainUpdate(
        club_name="B",
        team_id_fotmob=42,
        player_name="Captain Player",
        player_id_fotmob=987,
    )
    snapshot = SquadSnapshot(
        club_name="B",
        team_id_fotmob=42,
        members=tuple(
            SquadMember(player_name=f"Squad Player {index}")
            for index in range(11)
        ),
        source_url="https://www.fotmob.com/api/data/teams?id=42",
        complete=True,
    )
    calls = []
    monkeypatch.setattr(run, "fetch_fotmob_transfers", lambda **_kwargs: [transfer])
    monkeypatch.setattr(
        run,
        "fetch_squads_for_club_ids",
        lambda clubs: calls.append(tuple(clubs)) or ScrapeResult(
            [shirt],
            [captain],
            [snapshot],
        ),
    )

    def fail_deep_fetch(*_args, **_kwargs):
        raise AssertionError("Fast mode must not fetch every indexed club")

    monkeypatch.setattr(run, "fetch_clubs_transfers_safely", fail_deep_fetch)

    result = run._scrape_run_transfers(
        argparse.Namespace(
            club=None,
            deep=False,
            fotmob_only=True,
            popular=False,
            window="auto",
            since=None,
        ),
        context=_scrape_context({41: 10, 42: 20}),
    )

    assert calls == [(42, 41)]
    assert [item.player_name for item in result] == ["Player Two"]
    assert [item.player_name for item in result.roster_updates] == ["Squad Player"]

    assert len(result.captain_updates) == 1
    assert result.captain_updates[0].player_name == "Captain Player"
    assert result.squad_snapshots == (snapshot,)


def test_fast_squad_sync_incomplete_scrape_is_skipped(monkeypatch):
    import run_pipeline as run

    transfer = Transfer("Player Two", "A", "B", to_club_id_fotmob=42)
    monkeypatch.setattr(run, "fetch_fotmob_transfers", lambda **_kwargs: [transfer])

    def incomplete_squads(_clubs):
        raise run.IncompleteScrapeError("partial squad scrape")

    monkeypatch.setattr(run, "fetch_squads_for_club_ids", incomplete_squads)

    result = run._scrape_run_transfers(
        argparse.Namespace(
            club=None,
            deep=False,
            fotmob_only=True,
            popular=False,
            window="auto",
            since=None,
        ),
        context=_scrape_context({42: 20}),
    )

    assert [item.player_name for item in result] == ["Player Two"]
    assert result.roster_updates == ()
    assert result.squad_snapshots == ()


def test_auto_since_date_keeps_previous_summer_window_in_january():
    import run_pipeline as run

    assert run._previous_window_start(run.date(2027, 1, 15)) == run.date(2026, 6, 1)
    assert run._previous_window_start(run.date(2027, 5, 31)) == run.date(2026, 6, 1)
    assert run._previous_window_start(run.date(2026, 10, 3)) == run.date(2026, 1, 1)


def test_fast_squad_targets_include_every_touched_save_club_without_cap():
    # 40 save clubs touched by FotMob-ID events: all are refreshed, no cap.
    bindings = {1000 + index: 100 + index for index in range(40)}
    feed_events = [
        Transfer(
            f"Player {index}",
            "Foreign Club",
            f"Save Club {index}",
            from_club_id_fotmob=900_000 + index,
            to_club_id_fotmob=1000 + index,
        )
        for index in range(40)
    ]
    # A Transfermarkt event without FotMob IDs resolves by save club name.
    transfermarkt_event = Transfer("Name Player", "Nowhere FC", "Named Save Club")
    # An unbound FotMob club that may be a save club is fetched to learn it.
    unsure_event = Transfer(
        "Unsure Player",
        "Foreign Club",
        "Maybe Save Club",
        to_club_id_fotmob=777,
    )
    context = _scrape_context(
        {**bindings, 555: 500},
        names={"Named Save Club": 500, "Maybe Save Club": UNRESOLVED},
    )

    targets = _fast_squad_target_ids(
        (feed_events, [transfermarkt_event, unsure_event]),
        context,
    )

    assert set(targets) == {*bindings, 555, 777}
    assert len(targets) == 42
    assert not any(900_000 <= target for target in targets)


def test_deep_mode_targets_every_identity_bound_save_club(monkeypatch):
    import run_pipeline as run

    calls = {}
    captain = CaptainUpdate(
        club_name="Indexed FC",
        team_id_fotmob=42,
        player_name="Deep Captain",
        player_id_fotmob=987,
    )

    def fetch_clubs(club_ids, **kwargs):
        calls.update(kwargs, club_ids=tuple(club_ids))
        return ScrapeResult([], [captain])

    monkeypatch.setattr(run, "fetch_clubs_transfers_safely", fetch_clubs)
    monkeypatch.setattr(run, "fetch_fotmob_transfers", lambda **_kwargs: [])

    transfers = run._scrape_run_transfers(
        SimpleNamespace(
            club=None,
            deep=True,
            fotmob_only=True,
            popular=False,
            window="auto",
            since=None,
        ),
        context=_scrape_context({43: 102, 42: 101}),
    )

    assert transfers == []
    assert len(transfers.captain_updates) == 1
    assert transfers.captain_updates[0].player_name == "Deep Captain"
    assert calls["club_ids"] == (42, 43)
    assert calls["window"] == "auto"
    assert calls["since_date"] == (
        run._previous_window_start(run.date.today()).isoformat()
    )


def test_club_filter_resolves_save_names_and_rejects_unknown_clubs():
    import run_pipeline as run

    context = _scrape_context({42: 101}, names={"Example FC": 101})

    assert run._club_filter_fotmob_ids(["Example FC", "8456"], context) == (42, 8456)
    with pytest.raises(run.IncompleteScrapeError, match="Unknown FC"):
        run._club_filter_fotmob_ids(["Unknown FC"], context)


def test_captain_planner_requires_validated_club_and_roster_match():
    from scraper.matcher import NameMatcher

    matcher = NameMatcher()
    matcher.load_player_db([("Captain Player", 1002)])
    matcher.load_team_db({"Example FC": 101})

    planned = _plan_captain_updates(
        [
            CaptainUpdate(
                club_name="Example FC",
                team_id_fotmob=42,
                player_name="Captain Player",
                player_id_fotmob=987,
            )
        ],
        matcher,
        {101: [1002]},
        {101},
        {42: 101},
        80,
    )

    assert len(planned) == 1
    assert planned[0].team_id == 101
    assert planned[0].player_id == 1002
    assert planned[0].matched_player_name == "Captain Player"


def test_captain_planner_prefers_resolved_fotmob_identity_over_name():
    from scraper.matcher import NameMatcher

    matcher = NameMatcher()
    matcher.load_player_db([("Somebody Else", 1003), ("Captain Player", 1002)])

    planned = _plan_captain_updates(
        [
            CaptainUpdate(
                club_name="Example FC",
                team_id_fotmob=42,
                player_name="Capitão",
                player_id_fotmob=987,
            )
        ],
        matcher,
        {101: [1002, 1003]},
        {101},
        {42: 101},
        80,
        fotmob_player_ids={987: 1002},
    )

    assert [(item.team_id, item.player_id) for item in planned] == [(101, 1002)]


def test_fast_auto_restricts_live_feed_to_previous_window(monkeypatch):
    import run_pipeline as run

    calls = {}
    monkeypatch.setattr(
        run,
        "fetch_fotmob_transfers",
        lambda **kwargs: calls.update(kwargs) or [],
    )

    transfers = run._scrape_run_transfers(
        argparse.Namespace(
            club=None,
            deep=False,
            fotmob_only=True,
            popular=False,
            window="auto",
            since=None,
        )
    )

    assert transfers == []
    assert calls["window"] == "auto"
    assert calls["since_date"] == (
        run._previous_window_start(run.date.today()).isoformat()
    )


def test_fast_auto_uses_save_derived_since_window(monkeypatch):
    import run_pipeline as run

    calls = {}
    monkeypatch.setattr(
        run,
        "fetch_fotmob_transfers",
        lambda **kwargs: calls.update(kwargs) or [],
    )

    run._scrape_run_transfers(
        argparse.Namespace(
            club=None,
            deep=False,
            fotmob_only=True,
            popular=False,
            window="auto",
            since=None,
        ),
        context=_scrape_context({}, save_since_date="2026-09-20"),
    )

    assert calls["since_date"] == "2026-09-20"


def test_save_since_date_comes_from_log_and_pending_skips(monkeypatch, tmp_path):
    import config
    import run_pipeline as run

    scope = str(tmp_path / "EDIT00000000")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(
        run.transfer_logger,
        "read_log",
        lambda **kwargs: [
            {"timestamp": "2026-08-01T10:00:00+00:00"},
            {"timestamp": "2026-09-30T10:00:00+00:00"},
            {"timestamp": "2026-10-02T10:00:00+00:00", "dry_run": True},
        ]
        if kwargs["save_scope"] == scope
        else [],
    )
    today = run.date(2026, 10, 3)

    # Last applied change minus the safety margin.
    assert run._save_since_date(scope, include_legacy=False, today=today) == "2026-09-23"

    skipped_path = tmp_path / run.transfer_logger.SKIPPED_TRANSFERS_FILENAME
    skipped_path.write_text(
        "\n".join(
            json.dumps(row)
            for row in (
                {"save_scope": scope, "relevant": True, "date": "2026-09-05"},
                {"save_scope": scope, "relevant": False, "date": "2026-02-01"},
                {"save_scope": "other", "relevant": True, "date": "2026-02-01"},
            )
        ),
        encoding="utf-8",
    )
    # The oldest still-pending relevant event of this save widens the window.
    assert run._save_since_date(scope, include_legacy=False, today=today) == "2026-09-05"

    # Without applied history the previous-window rule applies.
    assert run._save_since_date("unknown", include_legacy=False, today=today) == "2026-01-01"


def test_pending_provider_events_are_reported_with_reason_and_relevance():
    import run_pipeline as run

    context = _scrape_context({42: 101})
    future = Transfer(
        "Future Player",
        "Foreign Club",
        "Save Club",
        date="2026-12-01",
        to_club_id_fotmob=42,
        player_id_fotmob=555,
    )
    undated = Transfer("Undated Player", "Foreign A", "Foreign B")

    skipped = run._pending_skipped(
        [future, undated],
        context,
        today=run.date(2026, 10, 3),
    )

    assert [
        (item.player_name, item.reason, item.relevant, item.fotmob_player_id)
        for item in skipped
    ] == [
        ("Future Player", "not_yet_effective", True, 555),
        ("Undated Player", "undated_in_window", False, None),
    ]
    rows = run._skipped_rows(reversed(skipped), "scope")
    assert [row["player_name"] for row in rows] == ["Future Player", "Undated Player"]
    assert all(row["save_scope"] == "scope" for row in rows)

class _LiveRoleOverflowEditFile:
    """Rank overflow candidates by the live file's slot-to-role map."""

    def __init__(self, live_roles: dict[int, int]):
        self.live_roles = live_roles

    def find_overflow_release_candidate(
        self,
        team_id,
        exclude_player_id=None,
        roster_player_ids=None,
        protected_player_ids=None,
    ):
        protected = {exclude_player_id} | set(protected_player_ids or ())
        candidates = [
            (slot, player_id)
            for slot, player_id in enumerate(roster_player_ids)
            if player_id and player_id not in protected
        ]
        return max(
            candidates,
            key=lambda item: self.live_roles[item[0]],
            default=(39, 0),
        )


def _arrival(player_id: int, *, source: int, destination: int) -> MatchedTransfer:
    return MatchedTransfer(
        transfer=Transfer(
            player_name=f"Player {player_id}",
            from_club="Source",
            to_club="Destination",
            date="2026-08-02",
        ),
        player_id=player_id,
        from_team_id=source,
        to_team_id=destination,
        player_confidence=100,
        from_team_confidence=100,
        to_team_confidence=100,
    )


def test_roster_plan_second_overflow_keeps_live_roles_of_remaining_players():
    source, destination = 30, 10
    live_roles = {slot: slot for slot in range(40)}
    # Slot 39 holds a bench player and slot 20 the deepest reserve.
    live_roles[20], live_roles[39] = 39, 20
    rosters = {
        source: TeamData(source, [501, 502, *range(2001, 2019)] + [0] * 20),
        destination: TeamData(destination, list(range(1000, 1040))),
    }

    plan = _plan_roster_actions(
        [
            _arrival(501, source=source, destination=destination),
            _arrival(502, source=source, destination=destination),
        ],
        rosters,
        set(rosters),
        _LiveRoleOverflowEditFile(live_roles),
        {},
    )

    assert [(item.action, item.overflow_player_id) for item in plan] == [
        ("move", 1020),
        ("move", 1038),
    ]


def test_roster_plan_overflow_prefers_player_released_later_in_plan():
    source, destination = 30, 10
    rosters = {
        source: TeamData(source, [501, *range(2001, 2019)] + [0] * 21),
        destination: TeamData(destination, list(range(1000, 1040))),
    }
    stale_release = MatchedTransfer(
        transfer=Transfer(
            player_name="Player 1005",
            from_club="Destination",
            to_club="Free Agent",
            transfer_type="squad_release",
        ),
        player_id=1005,
        from_team_id=destination,
        player_confidence=100,
        from_team_confidence=100,
    )

    plan = _plan_roster_actions(
        [_arrival(501, source=source, destination=destination), stale_release],
        rosters,
        set(rosters),
        _LiveRoleOverflowEditFile({slot: slot for slot in range(40)}),
        {},
    )

    assert [(item.action, item.overflow_player_id) for item in plan] == [
        ("move", 1005),
        ("noop", None),
    ]


def test_snapshot_release_not_suppressed_by_release_from_other_club():
    from scraper.matcher import NameMatcher

    current_ids = list(range(3001, 3018))
    other_ids = list(range(4001, 4018))
    matcher = NameMatcher()
    matcher.load_player_db(
        [(f"Player {player_id}", player_id) for player_id in current_ids + other_ids]
    )
    matcher.load_team_db({"Example FC": 10, "Other FC": 20})
    snapshot = SquadSnapshot(
        club_name="Example FC",
        team_id_fotmob=42,
        members=tuple(
            SquadMember(
                player_name=f"Player {player_id}",
                player_id_fotmob=5000 + index,
            )
            for index, player_id in enumerate(current_ids[:-1])
        ),
        source_url="https://www.fotmob.com/api/data/teams?id=42",
        complete=True,
    )
    stale_feed_release = Transfer(
        "Player 3017",
        "Other FC",
        "Free Agent",
        date="2026-08-01",
        transfer_type="free transfer",
    )

    matched = _match_transfers_statefully(
        [stale_feed_release],
        matcher,
        80,
        {10: current_ids, 20: other_ids},
        {10, 20},
        club_identity=_identity({42: 10}, matcher),
        squad_snapshots=(snapshot,),
        player_names={player_id: f"Player {player_id}" for player_id in current_ids},
    )

    assert (3017, 10, "squad_release") in [
        (match.player_id, match.from_team_id, match.transfer.transfer_type)
        for match in matched
    ]


def _team_rosters(**rosters: list[int]) -> dict[int, TeamData]:
    return {
        int(team_id.removeprefix("t")): TeamData(
            int(team_id.removeprefix("t")), ids + [0] * (40 - len(ids))
        )
        for team_id, ids in rosters.items()
    }


def test_plan_reports_every_skip_with_reason_code_and_relevance():
    save_club = 20
    not_in_save = MatchedTransfer(
        transfer=Transfer(
            "Youth Prospect",
            "Outside FC",
            "Save FC",
            date="2026-08-01",
            player_id_fotmob=901,
        ),
        to_team_id=save_club,
        to_team_confidence=100,
    )
    foreign_unknown = MatchedTransfer(
        transfer=Transfer(
            "Foreign Player", "Abroad A", "Abroad B", player_id_fotmob=902
        ),
    )
    foreign_known = MatchedTransfer(
        transfer=Transfer("Known Abroad", "Abroad A", "Abroad B"),
        player_id=7,
        player_confidence=100,
    )
    unresolved_destination = MatchedTransfer(
        transfer=Transfer(
            "Known Player", "Save FC", "Bayern Munich", to_club_id_fotmob=9823
        ),
        player_id=1,
        from_team_id=save_club,
        to_team_id=-1,
        player_confidence=100,
        from_team_confidence=100,
    )
    report = PlanningReport()

    plan = _plan_roster_actions(
        [not_in_save, foreign_unknown, foreign_known, unresolved_destination],
        _team_rosters(t20=[1, *range(100, 120)]),
        {save_club},
        object(),
        {},
        report=report,
    )

    assert all(item.action == "skip" for item in plan)
    assert [
        (item.player_name, item.reason, item.relevant, item.fotmob_player_id)
        for item in report.skipped
    ] == [
        ("Youth Prospect", "player_not_matched", True, 901),
        ("Foreign Player", "player_not_matched", False, 902),
        ("Known Abroad", "outside_save", False, None),
        ("Known Player", "destination_team_not_matched", True, None),
    ]
    assert "9823" in report.skipped[3].detail
    payload = report.skipped[0].to_dict()
    assert set(payload) == {
        "player_name",
        "from_team",
        "to_team",
        "date",
        "source",
        "reason",
        "detail",
        "relevant",
        "fotmob_player_id",
        "candidates",
    }
    assert json.loads(json.dumps(payload))["to_team"] == "Save FC"


def test_weak_unvalidated_club_name_is_unresolved_never_a_release():
    class WeakMatcher:
        def match_team(self, name):
            return (30, "Bayern München", 88.0) if name == "Bayern Munich" else (
                None,
                "",
                0.0,
            )

    # Without an identity index, a weak name stays unresolved even when the
    # FotMob club ID is unknown.
    assert _match_transfer_team(
        WeakMatcher(), "Bayern Munich", fotmob_id=9823
    )[0] == -1

    from scraper.matcher import NameMatcher

    matcher = NameMatcher()
    roster = [3001, *range(3002, 3020)]
    matcher.load_player_db([("Known Player", 3001)])
    matcher.load_team_db({"Save FC": 10, "Bayern München": 30})
    transfer = Transfer(
        "Known Player",
        "Save FC",
        "Bayern Munich",
        date="2026-08-01",
        from_club_id_fotmob=111,
        to_club_id_fotmob=9823,
    )
    report = PlanningReport()

    matched = _match_transfers_statefully(
        [transfer],
        matcher,
        80,
        {10: roster, 30: list(range(4001, 4020))},
        {10, 30},
        club_identity=_identity(
            {111: 10}, matcher, names={"Bayern Munich": UNRESOLVED}
        ),
        report=report,
    )
    plan = _plan_roster_actions(
        matched,
        _team_rosters(t10=roster, t30=list(range(4001, 4020))),
        {10, 30},
        object(),
        {},
        report=report,
    )

    assert matched[0].to_team_id == -1
    assert not matched[0].is_release
    assert [(item.action, item.reason) for item in plan] == [
        ("skip", "destination_team_not_matched")
    ]
    assert [(item.reason, item.relevant) for item in report.skipped] == [
        ("destination_team_not_matched", True)
    ]


def test_plan_reconciles_stale_source_from_destination_live_squad():
    source, actual, destination = 10, 30, 20
    match = _club_match(source=source, destination=destination, date="2026-08-02")
    rosters = _team_rosters(
        t10=list(range(1000, 1020)),
        t30=[115254, *range(2000, 2019)],
        t20=list(range(3000, 3020)),
    )
    report = PlanningReport(live_squad_ids={destination: frozenset({115254})})

    plan = _plan_roster_actions(
        [match], rosters, set(rosters), object(), {}, report=report
    )

    assert [(item.action, item.current_team_id) for item in plan] == [
        ("move", actual)
    ]
    assert report.skipped == []

    # Without destination evidence the mismatch is reported, not guessed.
    blocked_report = PlanningReport()
    blocked = _plan_roster_actions(
        [match], rosters, set(rosters), object(), {}, report=blocked_report
    )
    assert (blocked[0].action, blocked[0].reason) == (
        "skip",
        "current_club_mismatch",
    )
    assert "club 30" in blocked[0].detail
    assert [item.reason for item in blocked_report.skipped] == [
        "current_club_mismatch"
    ]


def test_plan_reconciles_stale_source_from_later_event_at_destination():
    first = _club_match(source=10, destination=20, date="2026-07-01")
    second = _club_match(source=20, destination=40, date="2026-08-01")
    rosters = _team_rosters(
        t10=list(range(1000, 1020)),
        t20=list(range(2000, 2020)),
        t30=[115254, *range(3000, 3019)],
        t40=list(range(4000, 4020)),
    )

    plan = _plan_roster_actions(
        [first, second], rosters, set(rosters), object(), {}
    )

    assert [(item.action, item.current_team_id) for item in plan] == [
        ("move", 30),
        ("move", 20),
    ]


def test_plan_adds_save_free_agent_with_known_source():
    match = _club_match(source=10, destination=20, date="2026-08-02")
    rosters = _team_rosters(
        t10=list(range(1000, 1020)), t20=list(range(2000, 2020))
    )

    plan = _plan_roster_actions([match], rosters, set(rosters), object(), {})

    assert [(item.action, item.current_team_id) for item in plan] == [
        ("add", None)
    ]


def test_return_from_outside_club_moves_from_parent_with_earlier_loan():
    parent, destination = 30, 20
    match = MatchedTransfer(
        transfer=Transfer(
            "Randal Kolo Muani", "Outside Club", "Destination", date="2026-08-02"
        ),
        player_id=115254,
        to_team_id=destination,
        player_confidence=100,
        to_team_confidence=100,
    )
    history = [{
        "player_id": 115254,
        "from_team_id": parent,
        "to_team_id": None,
        "transfer_type": "loan",
        "transfer_date": "2026-01-10",
    }]
    rosters = _team_rosters(
        t30=[115254, *range(3000, 3019)], t20=list(range(2000, 2020))
    )

    reconciled = _plan_roster_actions(
        [match],
        rosters,
        set(rosters),
        object(),
        _build_superseded_loan_sources([match], historical_entries=history),
    )
    unproven = _plan_roster_actions(
        [match],
        rosters,
        set(rosters),
        object(),
        _build_superseded_loan_sources([match]),
    )

    assert [(item.action, item.current_team_id) for item in reconciled] == [
        ("move", parent)
    ]
    assert [(item.action, item.reason) for item in unproven] == [
        ("skip", "already_registered_elsewhere")
    ]


def test_stale_fotmob_history_identity_does_not_block_player_forever():
    from scraper.matcher import NameMatcher

    matcher = NameMatcher()
    matcher.load_player_db([("First Player", 3001), ("Second Player", 3002)])
    matcher.load_team_db({"Old FC": 10, "New FC": 20, "Other FC": 30})
    transfer = Transfer(
        "Second Player", "Old FC", "New FC", player_id_fotmob=777
    )
    poisoned_history = [{"player_id": 3001, "fotmob_player_id": 777}]
    report = PlanningReport()

    matched = _match_transfers_statefully(
        [transfer],
        matcher,
        80,
        {10: [3002], 20: [], 30: [3001]},
        {10, 20, 30},
        poisoned_history,
        report=report,
    )

    # The event's clubs register the name-matched player, not the logged one.
    assert matched[0].player_id == 3002
    assert report.fotmob_player_ids[777] == 3002


def test_live_squad_identity_overrides_conflicting_history():
    from scraper.matcher import NameMatcher

    matcher = NameMatcher()
    matcher.load_player_db([("First Player", 3001), ("Second Player", 3002)])
    matcher.load_team_db({"Old FC": 10, "New FC": 20, "Other FC": 30})
    snapshot = SquadSnapshot(
        club_name="Old FC",
        team_id_fotmob=100,
        members=(SquadMember("Second Player", player_id_fotmob=777),),
        source_url="https://www.fotmob.com/api/data/teams?id=100",
        complete=True,
    )
    transfer = Transfer("First Player", "Old FC", "New FC", player_id_fotmob=777)
    report = PlanningReport()

    matched = _match_transfers_statefully(
        [transfer],
        matcher,
        80,
        {10: [3002], 20: [], 30: [3001]},
        {10, 20, 30},
        [{"player_id": 3001, "fotmob_player_id": 777}],
        club_identity=_identity({100: 10}, matcher),
        squad_snapshots=(snapshot,),
        report=report,
    )

    assert matched[0].player_id == 3002
    assert report.fotmob_player_ids[777] == 3002
    assert report.live_squad_ids[10] == frozenset({3002})


def test_undecidable_identity_conflict_is_reported_with_reason():
    from scraper.matcher import NameMatcher

    matcher = NameMatcher()
    matcher.load_player_db([("First Player", 3001), ("Second Player", 3002)])
    matcher.load_team_db({"Old FC": 10, "New FC": 20})
    transfer = Transfer(
        "Second Player",
        "Old FC",
        "New FC",
        date="2026-08-01",
        player_id_fotmob=777,
    )
    rosters = {
        10: [3002, *range(5000, 5017)],
        20: [3001, *range(6000, 6017)],
    }
    report = PlanningReport()

    matched = _match_transfers_statefully(
        [transfer],
        matcher,
        80,
        rosters,
        set(rosters),
        [{"player_id": 3001, "fotmob_player_id": 777}],
        report=report,
    )
    _plan_roster_actions(
        matched,
        _team_rosters(t10=rosters[10], t20=rosters[20]),
        set(rosters),
        object(),
        {},
        report=report,
    )

    assert matched[0].player_id is None
    assert [
        (item.reason, item.relevant, item.fotmob_player_id)
        for item in report.skipped
    ] == [("provider_identity_conflict", True, 777)]


def test_roster_minimum_departure_waits_for_same_plan_backfill():
    departure = _club_match(source=10, destination=20, date="2026-07-01")
    arrival = _arrival(501, source=30, destination=10)
    rosters = _team_rosters(
        t10=[115254, *range(1000, 1015)],
        t20=list(range(2000, 2020)),
        t30=[501, *range(3000, 3019)],
    )
    report = PlanningReport()

    plan = _plan_roster_actions(
        [departure, arrival], rosters, set(rosters), object(), {}, report=report
    )

    assert [(item.match.player_id, item.action) for item in plan] == [
        (501, "move"),
        (115254, "move"),
    ]
    assert report.skipped == []


def test_overflow_ranking_protects_live_squad_members():
    source, destination = 30, 10
    rosters = _team_rosters(
        t30=[501, *range(2001, 2019)],
        t10=list(range(1000, 1040)),
    )
    report = PlanningReport(live_squad_ids={destination: frozenset({1039})})

    plan = _plan_roster_actions(
        [_arrival(501, source=source, destination=destination)],
        rosters,
        set(rosters),
        _LiveRoleOverflowEditFile({slot: slot for slot in range(40)}),
        {},
        report=report,
    )

    # Slot 39 ranks first, but its player is in the live squad.
    assert [(item.action, item.overflow_player_id) for item in plan] == [
        ("move", 1038)
    ]


def test_live_squad_move_blocked_by_low_coverage_is_reported():
    from scraper.matcher import NameMatcher

    destination_ids = list(range(4001, 4031))
    source_ids = [3001, *range(3002, 3020)]
    matcher = NameMatcher()
    matcher.load_player_db(
        [("Mover Guy", 3001)]
        + [(f"Player {player_id}", player_id) for player_id in destination_ids]
    )
    matcher.load_team_db({"Source FC": 10, "Destination FC": 20})
    snapshot = SquadSnapshot(
        club_name="Destination FC",
        team_id_fotmob=200,
        members=(
            SquadMember("Mover Guy", player_id_fotmob=9001),
            *(
                SquadMember(f"Player {player_id}", player_id_fotmob=player_id)
                for player_id in destination_ids[:11]
            ),
        ),
        source_url="https://www.fotmob.com/api/data/teams?id=200",
        complete=True,
    )
    report = PlanningReport()

    matched = _match_transfers_statefully(
        [],
        matcher,
        80,
        {10: source_ids, 20: destination_ids},
        {10, 20},
        club_identity=_identity({200: 20}, matcher),
        squad_snapshots=(snapshot,),
        report=report,
    )

    assert not any(match.player_id == 3001 for match in matched)
    assert [
        (item.player_name, item.reason, item.relevant, item.fotmob_player_id)
        for item in report.skipped
    ] == [("Mover Guy", "snapshot_coverage_low", True, 9001)]
