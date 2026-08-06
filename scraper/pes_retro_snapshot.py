"""Canonical offline snapshots for normalized Pes Retro Stats profiles."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
import hashlib
import hmac
import json
import re
from types import MappingProxyType
from uuid import UUID

from scraper.pes_retro_stats import (
    _POSITION_KEYS,
    _STAT_KEYS,
    PesRetroStatsError,
    PesRetroStatsProfile,
    parse_pes_retro_stats_url,
)


SOURCE_MODEL = "pes-retro-normalized-v1"

_SNAPSHOT_KEYS = ("model", "data", "snapshot_sha256")
_DATA_KEYS = (
    "player_id",
    "short_id",
    "name",
    "full_name",
    "profile_url",
    "birth_date",
    "nationality",
    "current_club",
    "shirt_number",
    "height",
    "weight",
    "strong_foot",
    "weak_foot_accuracy",
    "weak_foot_frequency",
    "form",
    "injury_tolerance",
    "playing_style",
    "positions",
    "stats",
    "player_skill_codes",
    "com_playing_styles",
)
_POSITION_SNAPSHOT_KEYS = tuple(key.upper() for key in _POSITION_KEYS)
_SHA256_RE = re.compile(r"[0-9a-f]{64}")


class PesRetroSnapshotError(ValueError):
    """Raised when a profile or snapshot is not canonical and trustworthy."""


def _invalid(reason: str) -> PesRetroSnapshotError:
    return PesRetroSnapshotError(f"Invalid Pes Retro snapshot: {reason}")


def _canonical_bytes(model: str, data: Mapping[str, object]) -> bytes:
    return json.dumps(
        {"model": model, "data": data},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _has_exact_keys(value: dict[object, object], expected: tuple[str, ...]) -> bool:
    return len(value) == len(expected) and set(value) == set(expected)


def _required_text(data: dict[str, object], key: str) -> str:
    value = data[key]
    if type(value) is not str or not value or value != " ".join(value.split()):
        raise _invalid(f"noncanonical {key}")
    return value


def _optional_text(data: dict[str, object], key: str) -> str | None:
    value = data[key]
    if value is None:
        return None
    if type(value) is not str or not value or value != " ".join(value.split()):
        raise _invalid(f"noncanonical {key}")
    return value


def _required_integer(data: dict[str, object], key: str) -> int:
    value = data[key]
    if type(value) is not int:
        raise _invalid(f"non-integer {key}")
    return value


def _optional_integer(data: dict[str, object], key: str) -> int | None:
    value = data[key]
    if value is None:
        return None
    if type(value) is not int:
        raise _invalid(f"non-integer {key}")
    return value


def _string_tuple(data: dict[str, object], key: str) -> tuple[str, ...]:
    value = data[key]
    if type(value) is not list:
        raise _invalid(f"non-list {key}")

    items: list[str] = []
    for item in value:
        if type(item) is not str or not item or item != " ".join(item.split()):
            raise _invalid(f"noncanonical {key}")
        items.append(item)
    if len(items) != len(set(items)):
        raise _invalid(f"duplicate {key}")
    return tuple(items)


def _positions(data: dict[str, object]) -> Mapping[str, str | None]:
    value = data["positions"]
    if type(value) is not dict or not _has_exact_keys(
        value, _POSITION_SNAPSHOT_KEYS
    ):
        raise _invalid("incomplete positions")

    positions: dict[str, str | None] = {}
    for key in _POSITION_SNAPSHOT_KEYS:
        raw_position = value[key]
        if raw_position is None:
            positions[key] = None
        elif (
            type(raw_position) is str
            and raw_position
            and raw_position == " ".join(raw_position.split())
        ):
            positions[key] = raw_position
        else:
            raise _invalid("noncanonical position")
    return MappingProxyType(positions)


def _stats(data: dict[str, object]) -> Mapping[str, int]:
    value = data["stats"]
    if type(value) is not dict or not _has_exact_keys(value, _STAT_KEYS):
        raise _invalid("incomplete stats")

    stats: dict[str, int] = {}
    for key in _STAT_KEYS:
        stat = value[key]
        if type(stat) is not int:
            raise _invalid("non-integer stat")
        stats[key] = stat
    return MappingProxyType(stats)


def _profile_from_data(data: dict[str, object]) -> PesRetroStatsProfile:
    if not _has_exact_keys(data, _DATA_KEYS):
        raise _invalid("incomplete data")

    player_id = _required_text(data, "player_id")
    try:
        parsed_player_id = UUID(player_id)
    except (AttributeError, ValueError):
        raise _invalid("invalid player_id") from None
    if str(parsed_player_id) != player_id:
        raise _invalid("noncanonical player_id")
    if parsed_player_id.version not in (1, 2, 3, 4, 5):
        raise _invalid("unsupported player_id UUID version")

    short_id = _required_text(data, "short_id")
    profile_url = _required_text(data, "profile_url")
    try:
        url_short_id, canonical_url = parse_pes_retro_stats_url(profile_url)
    except PesRetroStatsError:
        raise _invalid("invalid profile_url") from None
    if (
        canonical_url != profile_url
        or url_short_id != short_id
        or player_id[:8] != short_id
    ):
        raise _invalid("mismatched profile identity")

    raw_birth_date = _required_text(data, "birth_date")
    try:
        birth_date = date.fromisoformat(raw_birth_date)
    except ValueError:
        raise _invalid("invalid birth_date") from None
    if birth_date.isoformat() != raw_birth_date:
        raise _invalid("noncanonical birth_date")

    return PesRetroStatsProfile(
        player_id=player_id,
        short_id=short_id,
        name=_required_text(data, "name"),
        full_name=_optional_text(data, "full_name"),
        profile_url=profile_url,
        birth_date=birth_date,
        nationality=_required_text(data, "nationality"),
        current_club=_required_text(data, "current_club"),
        shirt_number=_optional_integer(data, "shirt_number"),
        height=_required_integer(data, "height"),
        weight=_required_integer(data, "weight"),
        strong_foot=_required_text(data, "strong_foot"),
        weak_foot_accuracy=_required_integer(data, "weak_foot_accuracy"),
        weak_foot_frequency=_required_integer(data, "weak_foot_frequency"),
        form=_required_integer(data, "form"),
        injury_tolerance=_required_text(data, "injury_tolerance"),
        playing_style=_optional_text(data, "playing_style"),
        positions=_positions(data),
        stats=_stats(data),
        player_skill_codes=_string_tuple(data, "player_skill_codes"),
        com_playing_styles=_string_tuple(data, "com_playing_styles"),
    )


def _ordered_mapping(
    value: Mapping[str, object], expected: tuple[str, ...], field: str
) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise _invalid(f"non-mapping {field}")
    try:
        if len(value) != len(expected) or set(value) != set(expected):
            raise _invalid(f"incomplete {field}")
        return {key: value[key] for key in expected}
    except PesRetroSnapshotError:
        raise
    except (KeyError, TypeError, ValueError):
        raise _invalid(f"invalid {field}") from None


def profile_to_snapshot(profile: PesRetroStatsProfile) -> dict[str, object]:
    """Serialize one normalized profile into a canonical, hashed snapshot."""

    if not isinstance(profile, PesRetroStatsProfile):
        raise _invalid("invalid profile")
    if type(profile.birth_date) is not date:
        raise _invalid("invalid birth_date")
    if type(profile.player_skill_codes) is not tuple:
        raise _invalid("non-tuple player_skill_codes")
    if type(profile.com_playing_styles) is not tuple:
        raise _invalid("non-tuple com_playing_styles")

    positions = _ordered_mapping(
        profile.positions, _POSITION_SNAPSHOT_KEYS, "positions"
    )
    stats = _ordered_mapping(profile.stats, _STAT_KEYS, "stats")
    data: dict[str, object] = {
        "player_id": profile.player_id,
        "short_id": profile.short_id,
        "name": profile.name,
        "full_name": profile.full_name,
        "profile_url": profile.profile_url,
        "birth_date": profile.birth_date.isoformat(),
        "nationality": profile.nationality,
        "current_club": profile.current_club,
        "shirt_number": profile.shirt_number,
        "height": profile.height,
        "weight": profile.weight,
        "strong_foot": profile.strong_foot,
        "weak_foot_accuracy": profile.weak_foot_accuracy,
        "weak_foot_frequency": profile.weak_foot_frequency,
        "form": profile.form,
        "injury_tolerance": profile.injury_tolerance,
        "playing_style": profile.playing_style,
        "positions": positions,
        "stats": stats,
        "player_skill_codes": list(profile.player_skill_codes),
        "com_playing_styles": list(profile.com_playing_styles),
    }
    _profile_from_data(data)
    snapshot_hash = hashlib.sha256(_canonical_bytes(SOURCE_MODEL, data)).hexdigest()
    return {
        "model": SOURCE_MODEL,
        "data": data,
        "snapshot_sha256": snapshot_hash,
    }


def profile_from_snapshot(value: object) -> PesRetroStatsProfile:
    """Validate and reconstruct one canonical normalized profile snapshot."""

    if type(value) is not dict or not _has_exact_keys(value, _SNAPSHOT_KEYS):
        raise _invalid("incomplete envelope")

    model = value["model"]
    data = value["data"]
    supplied_hash = value["snapshot_sha256"]
    if type(model) is not str or model != SOURCE_MODEL:
        raise _invalid("unsupported model")
    if type(data) is not dict or not _has_exact_keys(data, _DATA_KEYS):
        raise _invalid("incomplete data")
    if type(supplied_hash) is not str or _SHA256_RE.fullmatch(supplied_hash) is None:
        raise _invalid("invalid snapshot_sha256")

    try:
        expected_hash = hashlib.sha256(_canonical_bytes(model, data)).hexdigest()
    except (RecursionError, TypeError, ValueError):
        raise _invalid("noncanonical data") from None
    if not hmac.compare_digest(supplied_hash, expected_hash):
        raise _invalid("snapshot_sha256 mismatch")

    return _profile_from_data(data)


__all__ = [
    "SOURCE_MODEL",
    "PesRetroSnapshotError",
    "profile_from_snapshot",
    "profile_to_snapshot",
]
