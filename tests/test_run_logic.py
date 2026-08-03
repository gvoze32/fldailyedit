"""Regression tests for fail-closed roster mutation decisions."""

import argparse

import pytest

from run import (
    _decide_roster_action,
    _dedupe_shirt_number_matches,
    _iso_date_arg,
    _percentage_arg,
    _positive_int_arg,
)
from scraper.models import MatchedTransfer, Transfer


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
