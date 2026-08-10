"""Pure construction and strict validation of proposal OVR reviews."""

from __future__ import annotations

from collections.abc import Mapping

from editor.player_ovr import (
    OVR_MODEL,
    calculate_ovr_tenths,
    ovr_weak_foot_accuracy,
    position_rating_for,
    relevant_ovr_positions,
)

_REVIEW_KEYS = frozenset({"model", "mode", "positions"})
_CREATE_ROW_KEYS = frozenset({"position", "proposal_tenths"})
_UPDATE_ROW_KEYS = frozenset(
    {"position", "base_tenths", "proposal_tenths", "delta_tenths"}
)
_MODES = {"create": "new_player", "update": "comparison"}
_MIN_OVR_TENTHS = 400
_MAX_OVR_TENTHS = 1200
_MAX_OVR_DELTA_TENTHS = _MAX_OVR_TENTHS - _MIN_OVR_TENTHS


def _operation_mode(operation: str) -> str:
    try:
        return _MODES[operation]
    except (KeyError, TypeError):
        raise ValueError(f"unsupported proposal operation: {operation!r}") from None

def _ovr_tenths(value: object, context: str) -> int:
    if type(value) is not int or not _MIN_OVR_TENTHS <= value <= _MAX_OVR_TENTHS:
        raise ValueError(
            f"{context} must be an integer from {_MIN_OVR_TENTHS} to "
            f"{_MAX_OVR_TENTHS}"
        )
    return value




def validate_ovr_review_shape(
    review: object,
    *,
    operation: str,
    registered_position: str,
    position_proficiency: Mapping[str, int],
) -> None:
    """Reject any review that is not the exact operation-specific JSON shape."""

    mode = _operation_mode(operation)
    if not isinstance(review, Mapping) or set(review) != _REVIEW_KEYS:
        raise ValueError("OVR review must have exact top-level keys")
    if review["model"] != OVR_MODEL:
        raise ValueError(f"OVR review model must be {OVR_MODEL!r}")
    if review["mode"] != mode:
        raise ValueError(
            f"OVR review mode must be {mode!r} for operation {operation!r}"
        )

    expected_positions = relevant_ovr_positions(
        registered_position, position_proficiency
    )
    rows = review["positions"]
    if not isinstance(rows, list) or len(rows) != len(expected_positions):
        raise ValueError("OVR review positions must exactly match relevant positions")

    expected_row_keys = (
        _CREATE_ROW_KEYS if operation == "create" else _UPDATE_ROW_KEYS
    )
    for row, expected_position in zip(rows, expected_positions, strict=True):
        if not isinstance(row, Mapping) or set(row) != expected_row_keys:
            raise ValueError("OVR review row has invalid keys")
        if row["position"] != expected_position:
            raise ValueError("OVR review positions are not in exact codec order")

        proposal_tenths = _ovr_tenths(
            row["proposal_tenths"], "proposal OVR tenths"
        )
        if operation == "update":
            base_tenths = _ovr_tenths(row["base_tenths"], "base OVR tenths")
            delta_tenths = row["delta_tenths"]
            if (
                type(delta_tenths) is not int
                or not -_MAX_OVR_DELTA_TENTHS
                <= delta_tenths
                <= _MAX_OVR_DELTA_TENTHS
            ):
                raise ValueError(
                    "OVR delta tenths must be an integer from "
                    f"{-_MAX_OVR_DELTA_TENTHS} to {_MAX_OVR_DELTA_TENTHS}"
                )
            if delta_tenths != proposal_tenths - base_tenths:
                raise ValueError("OVR delta tenths does not match proposal minus base")


def build_ovr_review(
    *,
    operation: str,
    proposal_abilities: Mapping[str, int],
    registered_position: str,
    position_proficiency: Mapping[str, int],
    base_abilities: Mapping[str, int] | None = None,
    proposal_weak_foot_accuracy: int = 0,
    base_weak_foot_accuracy: int | None = None,
) -> dict[str, object]:
    """Build one validated OVR review in player-codec position order."""

    mode = _operation_mode(operation)
    positions = relevant_ovr_positions(
        registered_position, position_proficiency
    )
    proposal_weak_foot_accuracy = ovr_weak_foot_accuracy(
        proposal_weak_foot_accuracy
    )
    base_weak_foot_accuracy = ovr_weak_foot_accuracy(
        proposal_weak_foot_accuracy - 1
        if base_weak_foot_accuracy is None
        else base_weak_foot_accuracy
    )
    rows: list[dict[str, object]] = []
    for position in positions:
        rating = position_rating_for(
            position, registered_position, position_proficiency
        )
        proposal_tenths = calculate_ovr_tenths(
            proposal_abilities,
            position,
            registered_position=registered_position,
            position_rating=rating,
            weak_foot_accuracy=proposal_weak_foot_accuracy,
        )
        if operation == "create":
            rows.append(
                {"position": position, "proposal_tenths": proposal_tenths}
            )
            continue
        if base_abilities is None:
            raise ValueError("update OVR review requires base abilities")
        base_tenths = calculate_ovr_tenths(
            base_abilities,
            position,
            registered_position=registered_position,
            position_rating=rating,
            weak_foot_accuracy=base_weak_foot_accuracy,
        )
        rows.append(
            {
                "position": position,
                "base_tenths": base_tenths,
                "proposal_tenths": proposal_tenths,
                "delta_tenths": proposal_tenths - base_tenths,
            }
        )

    review: dict[str, object] = {
        "model": OVR_MODEL,
        "mode": mode,
        "positions": rows,
    }
    validate_ovr_review_shape(
        review,
        operation=operation,
        registered_position=registered_position,
        position_proficiency=position_proficiency,
    )
    return review


__all__ = ["build_ovr_review", "validate_ovr_review_shape"]
