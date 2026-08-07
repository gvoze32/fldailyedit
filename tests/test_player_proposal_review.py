"""Behavioral contracts for proposal OVR review construction and validation."""

from copy import deepcopy

import pytest

from editor.player_codec import ABILITY_FIELDS
from editor.player_ovr import OVR_MODEL, calculate_ovr_tenths
from tools.player_proposal_review import build_ovr_review, validate_ovr_review_shape


REGISTERED_POSITION = "RB"
POSITION_PROFICIENCY = {"RB": 2, "RWF": 1}


def ability_vector(value: int = 60) -> dict[str, int]:
    return {field: value for field in ABILITY_FIELDS}


def proposal_vector() -> dict[str, int]:
    abilities = ability_vector()
    abilities.update({"speed": 82, "acceleration": 78, "finishing": 73})
    return abilities


def expected_update_review() -> dict[str, object]:
    base = ability_vector()
    proposal = proposal_vector()
    return {
        "model": "pes2021-community-estimate-v2",
        "mode": "comparison",
        "positions": [
            {
                "position": position,
                "base_tenths": calculate_ovr_tenths(base, position),
                "proposal_tenths": calculate_ovr_tenths(proposal, position),
                "delta_tenths": (
                    calculate_ovr_tenths(proposal, position)
                    - calculate_ovr_tenths(base, position)
                ),
            }
            for position in ("RB", "RWF")
        ],
    }


def expected_create_review() -> dict[str, object]:
    proposal = proposal_vector()
    return {
        "model": "pes2021-community-estimate-v2",
        "mode": "new_player",
        "positions": [
            {
                "position": position,
                "proposal_tenths": calculate_ovr_tenths(proposal, position),
            }
            for position in ("RB", "RWF")
        ],
    }


def validate(review: dict[str, object], operation: str) -> None:
    validate_ovr_review_shape(
        review,
        operation=operation,
        registered_position=REGISTERED_POSITION,
        position_proficiency=POSITION_PROFICIENCY,
    )


def test_update_ovr_review_has_the_exact_comparison_shape() -> None:
    base = ability_vector()
    proposal = proposal_vector()

    review = build_ovr_review(
        operation="update",
        base_abilities=base,
        proposal_abilities=proposal,
        registered_position=REGISTERED_POSITION,
        position_proficiency=POSITION_PROFICIENCY,
    )

    assert OVR_MODEL == "pes2021-community-estimate-v2"
    assert review == expected_update_review()
    assert validate(review, "update") is None


def test_create_ovr_review_has_exact_new_player_rows_without_base_or_delta() -> None:
    proposal = proposal_vector()

    review = build_ovr_review(
        operation="create",
        proposal_abilities=proposal,
        registered_position=REGISTERED_POSITION,
        position_proficiency=POSITION_PROFICIENCY,
    )

    assert review == expected_create_review()
    assert validate(review, "create") is None
    assert all(
        set(row) == {"position", "proposal_tenths"}
        for row in review["positions"]
    )


def _set_model(review: dict[str, object], value: object) -> None:
    review["model"] = value


def _set_mode(review: dict[str, object], value: object) -> None:
    review["mode"] = value


def _set_row_value(
    review: dict[str, object], row_index: int, key: str, value: object
) -> None:
    positions = review["positions"]
    assert isinstance(positions, list)
    row = positions[row_index]
    assert isinstance(row, dict)
    row[key] = value


def _duplicate_first_position(review: dict[str, object]) -> None:
    positions = review["positions"]
    assert isinstance(positions, list)
    positions[1] = deepcopy(positions[0])


def _reverse_positions(review: dict[str, object]) -> None:
    positions = review["positions"]
    assert isinstance(positions, list)
    positions.reverse()


def _make_position_irrelevant(review: dict[str, object]) -> None:
    _set_row_value(review, 1, "position", "CF")


def _add_top_level_key(review: dict[str, object]) -> None:
    review["unexpected"] = True


def _add_row_key(review: dict[str, object]) -> None:
    _set_row_value(review, 0, "unexpected", True)


def _remove_row_key(review: dict[str, object]) -> None:
    positions = review["positions"]
    assert isinstance(positions, list)
    row = positions[0]
    assert isinstance(row, dict)
    del row["proposal_tenths"]


@pytest.mark.parametrize(
    ("mutation", "value"),
    [
        (_set_model, "pes2021-community-estimate-v1"),
        (_set_model, True),
        (_set_mode, "new_player"),
        (_set_mode, True),
    ],
)
def test_update_review_rejects_wrong_model_and_mode(mutation, value) -> None:
    review = expected_update_review()
    mutation(review, value)

    with pytest.raises(ValueError):
        validate(review, "update")


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("base_tenths", True),
        ("proposal_tenths", False),
        ("delta_tenths", True),
        ("base_tenths", 399),
        ("base_tenths", 991),
        ("proposal_tenths", 399),
        ("proposal_tenths", 991),
        ("delta_tenths", -591),
        ("delta_tenths", 591),
    ],
)
def test_update_review_rejects_bool_and_out_of_range_numeric_values(
    key: str, value: object
) -> None:
    review = expected_update_review()
    _set_row_value(review, 0, key, value)

    with pytest.raises(ValueError):
        validate(review, "update")


def test_update_review_rejects_a_delta_inconsistent_with_base_and_proposal() -> None:
    review = expected_update_review()
    positions = review["positions"]
    assert isinstance(positions, list)
    row = positions[0]
    assert isinstance(row, dict)
    delta = row["delta_tenths"]
    assert type(delta) is int
    row["delta_tenths"] = delta + 1

    with pytest.raises(ValueError):
        validate(review, "update")


@pytest.mark.parametrize(
    "mutation",
    [
        _duplicate_first_position,
        _reverse_positions,
        _make_position_irrelevant,
        _add_top_level_key,
        _add_row_key,
        _remove_row_key,
    ],
)
def test_update_review_rejects_nonexact_positions_and_unknown_or_missing_keys(
    mutation,
) -> None:
    review = expected_update_review()
    mutation(review)

    with pytest.raises(ValueError):
        validate(review, "update")


@pytest.mark.parametrize("value", [True, False, 399, 991])
def test_create_review_rejects_bool_and_out_of_range_proposal_values(value) -> None:
    review = expected_create_review()
    _set_row_value(review, 0, "proposal_tenths", value)

    with pytest.raises(ValueError):
        validate(review, "create")


@pytest.mark.parametrize(
    "mutation",
    [
        _duplicate_first_position,
        _reverse_positions,
        _make_position_irrelevant,
        _add_top_level_key,
        _add_row_key,
        _remove_row_key,
    ],
)
def test_create_review_rejects_nonexact_positions_and_unknown_or_missing_keys(
    mutation,
) -> None:
    review = expected_create_review()
    mutation(review)

    with pytest.raises(ValueError):
        validate(review, "create")


@pytest.mark.parametrize(
    ("review", "operation"),
    [
        (expected_update_review(), "create"),
        (expected_create_review(), "update"),
    ],
)
def test_review_rejects_operation_mode_mismatch(
    review: dict[str, object], operation: str
) -> None:
    with pytest.raises(ValueError):
        validate(review, operation)
