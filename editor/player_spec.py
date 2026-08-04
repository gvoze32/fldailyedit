"""Strict, revision-scoped player specification models and JSON loading."""

from dataclasses import dataclass
from datetime import date
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import Mapping
import unicodedata
from urllib.parse import urlsplit

import config
from editor.player_codec import (
    ABILITY_FIELDS,
    COM_STYLE_FIELDS,
    FIELD_SPECS,
    PLAYER_SKILL_FIELDS,
    POSITION_NAMES,
)


class PlayerSpecError(ValueError):
    """Raised when a player specification is malformed or ambiguous."""


@dataclass(frozen=True, slots=True)
class BaseManifest:
    revision: str
    sha256: str


@dataclass(frozen=True, slots=True)
class FieldPatch:
    current: int
    target: int


@dataclass(frozen=True, slots=True)
class PlayerIdentity:
    name: str
    print_name: str | None
    aliases: tuple[str, ...]
    pes_id: int
    sortitoutsi_id: int


@dataclass(frozen=True, slots=True)
class Evidence:
    profile_url: str
    proof_urls: tuple[str, ...]
    effective_date: date
    reason: str


@dataclass(frozen=True, slots=True)
class CreatePlayerData:
    player_id: int
    name: str
    print_name: str
    team_id: int
    team_name: str
    preferred_shirt_number: int | None
    nationality_id: int
    age: int
    height: int
    weight: int
    registered_position: str
    playing_style: int
    strong_foot: int
    weak_foot_usage: int
    weak_foot_accuracy: int
    form: int
    injury_resistance: int
    position_proficiency: Mapping[str, int]
    abilities: Mapping[str, int]
    player_skills: tuple[str, ...]
    com_styles: tuple[str, ...]
    skin_color: int
    iris_color: int


@dataclass(frozen=True, slots=True)
class PlayerSpec:
    path: Path
    schema_version: int
    operation: str
    lifecycle_status: str
    lifecycle_reason: str
    superseded_by: str | None
    applies_to: tuple[str, ...]
    identity: PlayerIdentity
    evidence: Evidence
    create: CreatePlayerData | None
    patches: Mapping[str, FieldPatch]


_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
_TOP_LEVEL_FIELDS = frozenset(
    {
        "schema_version",
        "operation",
        "lifecycle",
        "applies_to",
        "identity",
        "evidence",
        "pes",
    }
)
_LIFECYCLE_FIELDS = frozenset({"status", "reason", "superseded_by"})
_IDENTITY_FIELDS = frozenset(
    {"name", "print_name", "aliases", "pes_id", "sortitoutsi_id"}
)
_EVIDENCE_FIELDS = frozenset(
    {"profile_url", "proof_urls", "effective_date", "reason"}
)
_CREATE_FIELDS = frozenset(
    {
        "player_id",
        "name",
        "print_name",
        "team_id",
        "team_name",
        "preferred_shirt_number",
        "nationality_id",
        "age",
        "height",
        "weight",
        "registered_position",
        "playing_style",
        "strong_foot",
        "weak_foot_usage",
        "weak_foot_accuracy",
        "form",
        "injury_resistance",
        "position_proficiency",
        "abilities",
        "player_skills",
        "com_styles",
        "skin_color",
        "iris_color",
    }
)
_CREATE_REQUIRED_FIELDS = _CREATE_FIELDS - {"preferred_shirt_number"}
_UPDATE_GROUP_FIELDS = frozenset(
    {"abilities", "position_proficiency", "player_skills", "com_styles"}
)
_UPDATE_DIRECT_FIELDS = frozenset(
    {
        "nationality_id",
        "age",
        "height",
        "weight",
        "registered_position",
        "playing_style",
        "strong_foot",
        "weak_foot_usage",
        "weak_foot_accuracy",
        "form",
        "injury_resistance",
    }
)
_UPDATE_FIELDS = _UPDATE_GROUP_FIELDS | _UPDATE_DIRECT_FIELDS


def _json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise PlayerSpecError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _read_json(path: Path, subject: str) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_json_object)
    except PlayerSpecError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PlayerSpecError(f"could not load {subject} {path}: {exc}") from exc


def _object(value: object, context: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise PlayerSpecError(f"{context} must be an object")
    return value


def _validate_keys(
    raw: Mapping[str, object],
    allowed: frozenset[str],
    required: frozenset[str],
    context: str,
) -> None:
    unknown = set(raw) - allowed
    missing = required - set(raw)
    if unknown:
        raise PlayerSpecError(f"{context} has unknown fields: {sorted(unknown)}")
    if missing:
        raise PlayerSpecError(f"{context} is missing fields: {sorted(missing)}")


def _text(raw: Mapping[str, object], field: str, context: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value.strip():
        raise PlayerSpecError(f"{context} {field} must be a non-empty string")
    return value.strip()


def _optional_text(raw: Mapping[str, object], field: str, context: str) -> str | None:
    value = raw.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise PlayerSpecError(f"{context} {field} must be null or a non-empty string")
    return value.strip()


def _integer(
    raw: Mapping[str, object], field: str, minimum: int, maximum: int, context: str
) -> int:
    value = raw.get(field)
    if isinstance(value, bool) or not isinstance(value, int):
        raise PlayerSpecError(f"{context} {field} must be an integer")
    if not minimum <= value <= maximum:
        raise PlayerSpecError(
            f"{context} {field} must be in {minimum}..{maximum}; got {value}"
        )
    return value


def _codec_integer(raw: Mapping[str, object], field: str, context: str) -> int:
    width = FIELD_SPECS[field].width
    return _integer(raw, field, 0, (1 << width) - 1, context)


def _ascii_tokens(value: str) -> tuple[str, ...]:
    decomposed = unicodedata.normalize("NFKD", value)
    ascii_text = decomposed.encode("ascii", "ignore").decode("ascii").lower()
    return tuple(re.findall(r"[a-z0-9]+", ascii_text))


def _normalized_alias(value: str) -> str:
    return " ".join(_ascii_tokens(value))


def player_slug(name: str) -> str:
    """Return a canonical ASCII filename slug for a player name."""
    if not isinstance(name, str):
        raise TypeError("player name must be a string")
    return "-".join(_ascii_tokens(name))


def _https_url(value: object, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PlayerSpecError(f"{context} must be a non-empty HTTPS URL")
    url = value.strip()
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise PlayerSpecError(f"{context} must use a valid HTTPS URL")
    return url


def _string_list(
    value: object, context: str, *, normalized_unique: bool = False
) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise PlayerSpecError(f"{context} must be a non-empty list")
    items: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise PlayerSpecError(f"{context} contains an empty or invalid value")
        item = item.strip()
        key = item.casefold() if normalized_unique else item
        if not key or key in seen:
            raise PlayerSpecError(f"{context} contains duplicate values")
        seen.add(key)
        items.append(item)
    return tuple(items)


def load_base_manifest(path: str | Path | None = None) -> BaseManifest:
    """Load and validate the bundled-base revision manifest."""
    manifest_path = Path(path) if path is not None else config.BASE_MANIFEST_FILE
    raw = _object(_read_json(manifest_path, "base manifest"), "base manifest")
    fields = frozenset({"revision", "sha256"})
    _validate_keys(raw, fields, fields, "base manifest")
    revision = _text(raw, "revision", "base manifest")
    sha256 = _text(raw, "sha256", "base manifest")
    if not _SHA256_RE.fullmatch(sha256):
        raise PlayerSpecError("base manifest sha256 must be 64 lowercase hexadecimal characters")
    return BaseManifest(revision=revision, sha256=sha256)


def _load_identity(value: object) -> PlayerIdentity:
    raw = _object(value, "identity")
    required = frozenset({"name", "aliases", "pes_id", "sortitoutsi_id"})
    _validate_keys(raw, _IDENTITY_FIELDS, required, "identity")
    name = _text(raw, "name", "identity")
    print_name = _optional_text(raw, "print_name", "identity")
    aliases = _string_list(raw.get("aliases"), "identity aliases", normalized_unique=True)
    canonical = _normalized_alias(name)
    if not canonical or canonical not in {_normalized_alias(alias) for alias in aliases}:
        raise PlayerSpecError("identity aliases must include the canonical name")
    return PlayerIdentity(
        name=name,
        print_name=print_name,
        aliases=aliases,
        pes_id=_integer(raw, "pes_id", 1, 0xFFFFFFFF, "identity"),
        sortitoutsi_id=_integer(raw, "sortitoutsi_id", 1, 0x7FFFFFFF, "identity"),
    )


def _load_evidence(value: object) -> Evidence:
    raw = _object(value, "evidence")
    _validate_keys(raw, _EVIDENCE_FIELDS, _EVIDENCE_FIELDS, "evidence")
    proof_raw = raw.get("proof_urls")
    if not isinstance(proof_raw, list) or not proof_raw:
        raise PlayerSpecError("evidence proof_urls must be a non-empty list")
    proof_urls = tuple(
        _https_url(item, f"evidence proof_urls[{index}]")
        for index, item in enumerate(proof_raw)
    )
    if len(set(proof_urls)) != len(proof_urls):
        raise PlayerSpecError("evidence proof_urls contains duplicate URLs")
    effective_raw = _text(raw, "effective_date", "evidence")
    if not _ISO_DATE_RE.fullmatch(effective_raw):
        raise PlayerSpecError("evidence effective_date must use YYYY-MM-DD")
    try:
        effective_date = date.fromisoformat(effective_raw)
    except ValueError as exc:
        raise PlayerSpecError("evidence effective_date must be a valid date") from exc
    return Evidence(
        profile_url=_https_url(raw.get("profile_url"), "evidence profile_url"),
        proof_urls=proof_urls,
        effective_date=effective_date,
        reason=_text(raw, "reason", "evidence"),
    )


def _validate_pes_string(value: str, field: str) -> str:
    if len(value.encode("utf-8")) > 60:
        raise PlayerSpecError(f"PES field {field} exceeds 60 UTF-8 bytes")
    return value


def _named_values(
    value: object,
    context: str,
    allowed: tuple[str, ...],
) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise PlayerSpecError(f"{context} must be a list")
    if any(not isinstance(item, str) or not item for item in value):
        raise PlayerSpecError(f"{context} contains an empty or invalid value")
    items = tuple(value)
    if len(set(items)) != len(items):
        raise PlayerSpecError(f"{context} contains duplicate values")
    unknown = set(items) - set(allowed)
    if unknown:
        raise PlayerSpecError(f"{context} has unknown values: {sorted(unknown)}")
    return items


def _load_create(value: object, identity: PlayerIdentity) -> CreatePlayerData:
    raw = _object(value, "create PES data")
    _validate_keys(raw, _CREATE_FIELDS, _CREATE_REQUIRED_FIELDS, "create PES data")

    player_id = _integer(raw, "player_id", 1, 0xFFFFFFFF, "create PES data")
    name = _validate_pes_string(_text(raw, "name", "create PES data"), "name")
    print_name = _validate_pes_string(
        _text(raw, "print_name", "create PES data"), "print_name"
    )
    if player_id != identity.pes_id:
        raise PlayerSpecError("create PES player_id must match identity pes_id")
    if name != identity.name:
        raise PlayerSpecError("create PES name must match identity name")
    if identity.print_name is None or print_name != identity.print_name:
        raise PlayerSpecError("create PES print_name must match identity print_name")

    registered_position = _text(raw, "registered_position", "create PES data").upper()
    if registered_position not in POSITION_NAMES:
        raise PlayerSpecError(
            f"create PES data registered_position {registered_position!r} is unknown"
        )

    positions_raw = _object(
        raw.get("position_proficiency"), "create PES position_proficiency"
    )
    unknown_positions = set(positions_raw) - set(POSITION_NAMES)
    if unknown_positions:
        raise PlayerSpecError(
            f"create PES position_proficiency has unknown positions: {sorted(unknown_positions)}"
        )
    positions: dict[str, int] = {}
    for position in POSITION_NAMES:
        if position in positions_raw:
            field = f"position_{position.lower()}"
            positions[position] = _integer(
                positions_raw,
                position,
                0,
                (1 << FIELD_SPECS[field].width) - 1,
                "create PES position_proficiency",
            )
    if positions.get(registered_position) != 2:
        raise PlayerSpecError(
            "create PES registered_position must have position_proficiency 2"
        )

    abilities_raw = _object(raw.get("abilities"), "create PES abilities")
    missing_abilities = set(ABILITY_FIELDS) - set(abilities_raw)
    extra_abilities = set(abilities_raw) - set(ABILITY_FIELDS)
    if missing_abilities or extra_abilities:
        raise PlayerSpecError(
            "create PES abilities mismatch; "
            f"missing={sorted(missing_abilities)}, extra={sorted(extra_abilities)}"
        )
    abilities = {
        field: _integer(abilities_raw, field, 40, 99, "create PES abilities")
        for field in ABILITY_FIELDS
    }

    preferred_shirt_number = raw.get("preferred_shirt_number")
    if preferred_shirt_number is not None:
        preferred_shirt_number = _integer(
            raw, "preferred_shirt_number", 1, 999, "create PES data"
        )

    return CreatePlayerData(
        player_id=player_id,
        name=name,
        print_name=print_name,
        team_id=_integer(raw, "team_id", 1, 0xFFFFFFFF, "create PES data"),
        team_name=_text(raw, "team_name", "create PES data"),
        preferred_shirt_number=preferred_shirt_number,
        nationality_id=_codec_integer(raw, "nationality_id", "create PES data"),
        age=_codec_integer(raw, "age", "create PES data"),
        height=_codec_integer(raw, "height", "create PES data"),
        weight=_codec_integer(raw, "weight", "create PES data"),
        registered_position=registered_position,
        playing_style=_codec_integer(raw, "playing_style", "create PES data"),
        strong_foot=_codec_integer(raw, "strong_foot", "create PES data"),
        weak_foot_usage=_codec_integer(raw, "weak_foot_usage", "create PES data"),
        weak_foot_accuracy=_codec_integer(
            raw, "weak_foot_accuracy", "create PES data"
        ),
        form=_codec_integer(raw, "form", "create PES data"),
        injury_resistance=_codec_integer(
            raw, "injury_resistance", "create PES data"
        ),
        position_proficiency=MappingProxyType(positions),
        abilities=MappingProxyType(abilities),
        player_skills=_named_values(
            raw.get("player_skills"),
            "create PES player_skills",
            PLAYER_SKILL_FIELDS,
        ),
        com_styles=_named_values(
            raw.get("com_styles"), "create PES com_styles", COM_STYLE_FIELDS
        ),
        skin_color=_integer(raw, "skin_color", 0, 0xFF, "create PES data"),
        iris_color=_integer(raw, "iris_color", 0, 0xFF, "create PES data"),
    )


def _patch_pair(
    value: object,
    field: str,
    minimum: int,
    maximum: int,
) -> FieldPatch:
    raw = _object(value, f"PES patch {field}")
    patch_fields = frozenset({"from", "to"})
    _validate_keys(raw, patch_fields, patch_fields, f"PES patch {field}")
    current = _integer(raw, "from", minimum, maximum, f"PES patch {field}")
    target = _integer(raw, "to", minimum, maximum, f"PES patch {field}")
    if current == target:
        raise PlayerSpecError(f"PES patch {field} must change its value")
    return FieldPatch(current=current, target=target)


def _codec_patch(value: object, field: str) -> FieldPatch:
    width = FIELD_SPECS[field].width
    return _patch_pair(value, field, 0, (1 << width) - 1)


def _load_update(value: object) -> Mapping[str, FieldPatch]:
    raw = _object(value, "update PES data")
    unknown = set(raw) - _UPDATE_FIELDS
    if unknown:
        raise PlayerSpecError(f"update has unknown PES fields: {sorted(unknown)}")
    patches: dict[str, FieldPatch] = {}

    if "abilities" in raw:
        abilities = _object(raw["abilities"], "update PES abilities")
        if not abilities:
            raise PlayerSpecError("update PES abilities must not be empty")
        unknown_abilities = set(abilities) - set(ABILITY_FIELDS)
        if unknown_abilities:
            raise PlayerSpecError(
                f"update has unknown PES ability fields: {sorted(unknown_abilities)}"
            )
        for field, patch in abilities.items():
            patches[field] = _patch_pair(patch, field, 40, 99)

    if "position_proficiency" in raw:
        positions = _object(
            raw["position_proficiency"], "update PES position_proficiency"
        )
        if not positions:
            raise PlayerSpecError("update PES position_proficiency must not be empty")
        unknown_positions = set(positions) - set(POSITION_NAMES)
        if unknown_positions:
            raise PlayerSpecError(
                f"update has unknown PES positions: {sorted(unknown_positions)}"
            )
        for position, patch in positions.items():
            field = f"position_{position.lower()}"
            patches[field] = _codec_patch(patch, field)

    for group, allowed, prefix in (
        ("player_skills", PLAYER_SKILL_FIELDS, "skill_"),
        ("com_styles", COM_STYLE_FIELDS, "com_style_"),
    ):
        if group not in raw:
            continue
        values = _object(raw[group], f"update PES {group}")
        if not values:
            raise PlayerSpecError(f"update PES {group} must not be empty")
        unknown_values = set(values) - set(allowed)
        if unknown_values:
            raise PlayerSpecError(
                f"update has unknown PES {group}: {sorted(unknown_values)}"
            )
        for name, patch in values.items():
            field = f"{prefix}{name}"
            patches[field] = _codec_patch(patch, field)

    for field in _UPDATE_DIRECT_FIELDS - {"registered_position"}:
        if field in raw:
            patches[field] = _codec_patch(raw[field], field)

    if "registered_position" in raw:
        position_patch = _object(
            raw["registered_position"], "PES patch registered_position"
        )
        patch_fields = frozenset({"from", "to"})
        _validate_keys(
            position_patch,
            patch_fields,
            patch_fields,
            "PES patch registered_position",
        )
        values: list[int] = []
        for key in ("from", "to"):
            position = position_patch[key]
            if not isinstance(position, str) or position.upper() not in POSITION_NAMES:
                raise PlayerSpecError(
                    f"PES patch registered_position {key} is not a known position"
                )
            values.append(POSITION_NAMES.index(position.upper()))
        if values[0] == values[1]:
            raise PlayerSpecError("PES patch registered_position must change its value")
        patches["registered_position"] = FieldPatch(*values)

    if not patches:
        raise PlayerSpecError("update PES data must contain at least one patch")
    return MappingProxyType(patches)


def _load_one_spec(path: Path) -> PlayerSpec:
    raw = _object(_read_json(path, "player spec"), f"player spec {path}")
    _validate_keys(raw, _TOP_LEVEL_FIELDS, _TOP_LEVEL_FIELDS, f"player spec {path}")

    schema_version = _integer(raw, "schema_version", 1, 1, f"player spec {path}")
    operation = _text(raw, "operation", f"player spec {path}")
    if operation not in {"create", "update"}:
        raise PlayerSpecError(f"player spec {path} operation must be create or update")

    lifecycle = _object(raw.get("lifecycle"), f"player spec {path} lifecycle")
    _validate_keys(
        lifecycle,
        _LIFECYCLE_FIELDS,
        frozenset({"status"}),
        f"player spec {path} lifecycle",
    )
    lifecycle_status = _text(lifecycle, "status", f"player spec {path} lifecycle")
    if lifecycle_status not in {"active", "upstreamed", "retired"}:
        raise PlayerSpecError(
            f"player spec {path} has unsupported lifecycle status {lifecycle_status!r}"
        )
    lifecycle_reason = (
        _text(lifecycle, "reason", f"player spec {path} lifecycle")
        if "reason" in lifecycle
        else ""
    )
    superseded_by = _optional_text(
        lifecycle, "superseded_by", f"player spec {path} lifecycle"
    )

    applies_to = _string_list(
        raw.get("applies_to"), f"player spec {path} applies_to"
    )
    if len(set(applies_to)) != len(applies_to):
        raise PlayerSpecError(f"player spec {path} applies_to contains duplicates")

    identity = _load_identity(raw.get("identity"))
    if path.stem != player_slug(identity.name):
        raise PlayerSpecError(
            f"player spec filename {path.name!r} does not match identity name {identity.name!r}"
        )
    evidence = _load_evidence(raw.get("evidence"))

    if operation == "create":
        create = _load_create(raw.get("pes"), identity)
        patches: Mapping[str, FieldPatch] = MappingProxyType({})
    else:
        create = None
        patches = _load_update(raw.get("pes"))

    return PlayerSpec(
        path=path,
        schema_version=schema_version,
        operation=operation,
        lifecycle_status=lifecycle_status,
        lifecycle_reason=lifecycle_reason,
        superseded_by=superseded_by,
        applies_to=applies_to,
        identity=identity,
        evidence=evidence,
        create=create,
        patches=patches,
    )


def validate_spec_set(specs: tuple[PlayerSpec, ...]) -> None:
    """Reject identities that are ambiguous across player spec files."""
    pes_ids: dict[int, Path] = {}
    sortitoutsi_ids: dict[int, Path] = {}
    aliases: dict[str, Path] = {}
    for spec in specs:
        identity = spec.identity
        if identity.pes_id in pes_ids:
            raise PlayerSpecError(
                f"duplicate PES ID {identity.pes_id} in {pes_ids[identity.pes_id]} and {spec.path}"
            )
        pes_ids[identity.pes_id] = spec.path
        if identity.sortitoutsi_id in sortitoutsi_ids:
            raise PlayerSpecError(
                "duplicate SortitoutSI ID "
                f"{identity.sortitoutsi_id} in "
                f"{sortitoutsi_ids[identity.sortitoutsi_id]} and {spec.path}"
            )
        sortitoutsi_ids[identity.sortitoutsi_id] = spec.path
        for alias in identity.aliases:
            normalized = _normalized_alias(alias)
            if normalized in aliases and aliases[normalized] != spec.path:
                raise PlayerSpecError(
                    f"duplicate normalized alias {alias!r} in {aliases[normalized]} and {spec.path}"
                )
            aliases[normalized] = spec.path


def load_player_specs(
    directory: str | Path | None = None,
) -> tuple[PlayerSpec, ...]:
    """Load all player spec JSON files in deterministic filename order."""
    specs_dir = Path(directory) if directory is not None else config.PLAYER_SPECS_DIR
    try:
        paths = sorted(specs_dir.glob("*.json"), key=lambda path: path.name)
    except OSError as exc:
        raise PlayerSpecError(f"could not list player spec directory {specs_dir}: {exc}") from exc
    specs = tuple(_load_one_spec(path) for path in paths)
    validate_spec_set(specs)
    return specs
