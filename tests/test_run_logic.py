"""Regression tests for fail-closed roster mutation decisions."""

import argparse
import json
from types import SimpleNamespace

import pytest

from run import (
    PlannedRosterAction,
    _RunLocalUpdateRuntime,
    _build_superseded_loan_sources,
    _competition_section_bounds,
    _decide_roster_action,
    _dedupe_shirt_number_matches,
    _iso_date_arg,
    _match_and_plan_transfers,
    _match_transfer_team,
    _match_transfers_statefully,
    _load_represented_fotmob_club_ids,
    _percentage_arg,
    _plan_roster_actions,
    _positive_int_arg,
    _resolve_run_paths,
)
from scraper.models import MatchedTransfer, Transfer
from editor.models import TeamData



def test_local_update_enables_overflow_release_by_default(tmp_path):
    from local_update import LocalUpdateRequest

    request = LocalUpdateRequest(tmp_path / "EDIT00000000")

    assert request.allow_overflow_release is True

@pytest.mark.parametrize(
    ("current", "source", "destination", "transfer_type", "expected"),
    [
        (10, 10, 20, "transfer", "move"),
        (20, 10, 20, "transfer", "noop"),
        (30, 10, 20, "transfer", "skip"),
        (None, 10, 20, "transfer", "skip"),
        (None, None, 20, "free transfer", "add"),
        (20, None, 20, "free transfer", "noop"),
        (30, None, 20, "free transfer", "skip"),
        (10, 10, None, "free transfer", "release"),
        (None, 10, None, "free transfer", "noop"),
        (30, 10, None, "free transfer", "skip"),
        (20, 20, 20, "shirt_number_update", "shirt_update"),
        (30, 20, 20, "shirt_number_update", "skip"),
    ],
)
def test_decide_roster_action_is_fail_closed(
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
    ) == "move"


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
    ) == "skip"


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

    plan = _plan_roster_actions(
        [transfer], rosters, set(rosters), object(), {}
    )

    assert (plan[0].action, plan[0].reason) == (
        "skip",
        "source_roster_minimum",
    )


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
    import run

    class FakeEditFile:
        _data = bytearray(b"updated")

        def move_player(self, *args, **kwargs):
            self.move_kwargs = kwargs
            return True

    edit_path = tmp_path / "EDIT00000000"
    edit_path.write_bytes(b"encrypted")
    edit_file = FakeEditFile()
    prepared = SimpleNamespace(
        edit_file=edit_file,
        edit_path=edit_path,
        output_path=edit_path,
        original_data=b"original",
        roster_plan=(
            PlannedRosterAction(
                match=_club_match(source=10, destination=20, date="2026-08-02"),
                action="move",
                current_team_id=10,
            ),
        ),
        run_records=[],
        backup_path=None,
        pending_logs=[],
        save_scope=str(edit_path),
    )
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
    assert result.transfer_applied == 1


def test_same_day_transfers_sort_by_timestamp():
    from run import _transfer_sort_key

    later = Transfer("Player", "B", "C", date="2026-08-02T18:00:00Z")
    earlier = Transfer("Player", "A", "B", date="2026-08-02T09:00:00Z")

    assert sorted([later, earlier], key=_transfer_sort_key) == [earlier, later]


def test_shirt_number_conflict_identifies_other_player():
    from run import _find_shirt_number_conflict

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
    assert _match_transfer_team(
        matcher,
        "PSG",
        "Paris Saint-Germain",
        fotmob_id=9847,
        validated_fotmob_ids={1234},
    ) == (-1, "", 100.0)
    assert _match_transfer_team(matcher, "Similar Club")[0] == -1
    assert _match_transfer_team(matcher, "Ambiguous Club")[0] == -1
    assert _match_transfer_team(
        matcher,
        "Free Agent",
        "Free Agent",
        fotmob_id=2,
        validated_fotmob_ids={1234},
    ) == (None, "", 100.0)

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

    prepared = SimpleNamespace(
        edit_file=FakeEditFile(),
        roster_plan=[
            PlannedRosterAction(skipped_match, "skip", None, "player_not_matched"),
            PlannedRosterAction(moved_match, "move", 10),
        ],
        output_path=tmp_path / "output",
        edit_path=tmp_path / "input",
        original_data=b"original",
        backup_path=None,
        pending_logs=[],
        run_records=[],
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


def test_runtime_club_identity_index_must_be_one_to_one(monkeypatch, tmp_path):
    import config
    from scraper.fotmob import IncompleteScrapeError

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    (tmp_path / "fotmob_teams_validated.json").write_text(
        json.dumps([
            {"fotmob_id": 10, "pes_team_id": 1},
            {"fotmob_id": 11, "pes_team_id": 1},
        ]),
        encoding="utf-8",
    )

    with pytest.raises(IncompleteScrapeError, match="not one-to-one"):
        _load_represented_fotmob_club_ids()
    
def test_transfer_run_includes_current_squad_numbers_by_default(monkeypatch):
    import run

    shirt = Transfer(
        "Player One",
        "Club",
        "Club",
        transfer_type="shirt_number_update",
        shirt_number=7,
        player_id_fotmob=1,
    )
    transfer = Transfer("Player Two", "A", "B")
    monkeypatch.setattr(run, "fetch_fotmob_transfers", lambda **_kwargs: [transfer])
    monkeypatch.setattr(
        run,
        "fetch_major_clubs_transfers_safely",
        lambda **_kwargs: [shirt, transfer],
    )

    result = run._scrape_run_transfers(
        argparse.Namespace(
            club=None,
            deep=False,
            fotmob_only=True,
            popular=False,
            window="auto",
            since=None,
        )
    )
    assert result == [transfer, shirt]


def test_deep_auto_restricts_club_history_to_current_year(monkeypatch):
    import run

    calls = {}
    monkeypatch.setattr(
        run,
        "fetch_major_clubs_transfers_safely",
        lambda **kwargs: calls.update(kwargs) or [],
    )
    monkeypatch.setattr(run, "fetch_fotmob_transfers", lambda **_kwargs: [])

    transfers = run._scrape_run_transfers(
        SimpleNamespace(
            club=None,
            deep=True,
            fotmob_only=True,
            popular=False,
            window="auto",
            since=None,
        )
    )

    assert transfers == []
    assert calls["window"] == "auto"
    assert calls["since_date"] == (
        run.date.today().replace(month=1, day=1).isoformat()
    )

