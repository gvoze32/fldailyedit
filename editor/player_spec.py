"""Strict, revision-scoped player specification models and JSON loading."""

from dataclasses import dataclass
from datetime import date
import hashlib
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import TYPE_CHECKING, Mapping
import unicodedata
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from editor.editfile import EditFile
    from editor.models import PlayerInfo

import config
from editor.player_codec import (
    ABILITY_FIELDS,
    COM_STYLE_FIELDS,
    FIELD_SPECS,
    PLAYER_SKILL_FIELDS,
    POSITION_NAMES,
    decode_player_entry,
    patch_player_entry,
    serialize_created_player,
)
from scraper.pes_retro_snapshot import profile_from_snapshot
from scraper.pes21_proposal import map_pes21_proposal
from tools.player_proposal_review import validate_ovr_review_shape


class PlayerSpecError(ValueError):
    """Raised when a player specification is malformed or ambiguous."""


class _FrozenList(tuple[object, ...]):
    """A tuple-backed JSON-list-compatible immutable sequence."""

    def __new__(cls, values=()):
        return tuple.__new__(cls, values)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, (list, tuple)):
            return tuple.__eq__(self, tuple(other))
        return NotImplemented

    def __ne__(self, other: object) -> bool:
        result = self.__eq__(other)
        if result is NotImplemented:
            return NotImplemented
        return not result

    def _immutable(self, *args: object, **kwargs: object) -> None:
        raise TypeError("frozen proposal metadata")

    __setitem__ = _immutable
    __delitem__ = _immutable
    __iadd__ = _immutable
    __imul__ = _immutable
    append = _immutable
    clear = _immutable
    extend = _immutable
    insert = _immutable
    pop = _immutable
    remove = _immutable
    reverse = _immutable
    sort = _immutable

    def copy(self) -> "_FrozenList":
        return self


_MAX_PLAYER_SPEC_BYTES = 2 * 1024 * 1024


def _freeze_metadata(value: object) -> object:
    """Recursively freeze JSON containers while retaining list equality."""

    if isinstance(value, Mapping):
        return MappingProxyType(
            {key: _freeze_metadata(nested) for key, nested in value.items()}
        )
    if isinstance(value, list):
        return _FrozenList(_freeze_metadata(nested) for nested in value)
    if isinstance(value, tuple):
        return tuple(_freeze_metadata(nested) for nested in value)
    return value


@dataclass(frozen=True, slots=True)
class ProposalMetadata:
    generator: str
    needs_human_review: bool
    source_snapshot: Mapping[str, object]
    ovr_review: Mapping[str, object]
    issue_number: int
    issue_url: str
    submitted_team: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "source_snapshot", _freeze_metadata(self.source_snapshot)
        )
        object.__setattr__(
            self, "ovr_review", _freeze_metadata(self.ovr_review)
        )
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
    pes_retro_stats_id: str


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
    proposal: ProposalMetadata | None = None


@dataclass(frozen=True, slots=True)
class SpecResult:
    pes_id: int
    name: str
    status: str
    reason: str
    diagnostic: str | None = None



_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_PES_RETRO_STATS_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z"
)
_PES_RETRO_STATS_PROFILE_RE = re.compile(
    r"https://pesretrostats\.com/player/(?P<prefix>[0-9a-f]{8})-"
    r"[a-z0-9]+(?:-[a-z0-9]+)*\Z"
)
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
_PROPOSAL_DRAFT_FIELDS = frozenset(
    {"generator", "needs_human_review", "ovr_review"}
)
_DRAFT_TOP_LEVEL_FIELDS = _TOP_LEVEL_FIELDS | frozenset({"source", "draft"})
_DRAFT_EVIDENCE_FIELDS = frozenset(
    {
        "profile_url",
        "proof_urls",
        "effective_date",
        "current_team",
        "issue_number",
        "issue_url",
    }
)
_DRAFT_ISSUE_PATH_RE = re.compile(
    r"/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/issues/"
    r"(?P<issue_number>[1-9][0-9]*)\Z"
)
_LIFECYCLE_FIELDS = frozenset({"status", "reason", "superseded_by"})
_IDENTITY_FIELDS = frozenset(
    {"name", "print_name", "aliases", "pes_id", "pes_retro_stats_id"}
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
        with path.open("rb") as handle:
            raw_bytes = handle.read(_MAX_PLAYER_SPEC_BYTES + 1)
        if len(raw_bytes) > _MAX_PLAYER_SPEC_BYTES:
            raise PlayerSpecError(
                f"{subject} JSON exceeds the maximum size"
            )
        return json.loads(
            raw_bytes.decode("utf-8"),
            object_pairs_hook=_json_object,
        )
    except PlayerSpecError:
        raise
    except RecursionError:
        raise PlayerSpecError(
            f"could not load {subject} {path}: JSON is too deeply nested"
        ) from None
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

def _identity_text(value: str, context: str) -> str:
    if any(
        ord(character) < 0x20 or ord(character) == 0x7F
        for character in value
    ):
        raise PlayerSpecError(
            f"{context} must be canonical text without control characters"
        )
    return value


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


def normalize_player_identity(value: str) -> str:
    """Return the normalized identity key used for runtime matching."""
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

def verify_base_file(
    edit_path: str | Path,
    manifest_path: str | Path | None = None,
) -> BaseManifest:
    """Return the manifest after streaming and verifying the base-file digest."""
    manifest = load_base_manifest(manifest_path)
    path = Path(edit_path)
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise PlayerSpecError(f"could not read base file {path}: {exc}") from exc
    actual = digest.hexdigest()
    if actual != manifest.sha256:
        raise PlayerSpecError(
            "base file digest mismatch: "
            f"expected {manifest.sha256}, found {actual}"
        )
    return manifest


def _load_identity(value: object) -> PlayerIdentity:
    raw = _object(value, "identity")
    required = frozenset({"name", "aliases", "pes_id", "pes_retro_stats_id"})
    _validate_keys(raw, _IDENTITY_FIELDS, required, "identity")
    raw_name = raw.get("name")
    if isinstance(raw_name, str):
        _identity_text(raw_name, "identity name")
    name = _text(raw, "name", "identity")

    raw_print_name = raw.get("print_name")
    if isinstance(raw_print_name, str):
        _identity_text(raw_print_name, "identity print_name")
    print_name = _optional_text(raw, "print_name", "identity")

    raw_aliases = raw.get("aliases")
    if isinstance(raw_aliases, list):
        for alias in raw_aliases:
            if isinstance(alias, str):
                _identity_text(alias, "identity aliases")
    aliases = _string_list(
        raw_aliases, "identity aliases", normalized_unique=True
    )
    canonical = normalize_player_identity(name)
    if not canonical or canonical not in {
        normalize_player_identity(alias) for alias in aliases
    }:
        raise PlayerSpecError("identity aliases must include the canonical name")
    raw_pes_retro_stats_id = raw.get("pes_retro_stats_id")
    if (
        isinstance(raw_pes_retro_stats_id, str)
        and raw_pes_retro_stats_id != raw_pes_retro_stats_id.strip()
    ):
        raise PlayerSpecError(
            "identity pes_retro_stats_id must not have surrounding whitespace"
        )
    pes_retro_stats_id = _text(raw, "pes_retro_stats_id", "identity")
    if not _PES_RETRO_STATS_UUID_RE.fullmatch(pes_retro_stats_id):
        raise PlayerSpecError(
            "identity pes_retro_stats_id must be a canonical lowercase UUID"
        )
    return PlayerIdentity(
        name=name,
        print_name=print_name,
        aliases=aliases,
        pes_id=_integer(raw, "pes_id", 1, 0xFFFFFFFF, "identity"),
        pes_retro_stats_id=pes_retro_stats_id,
    )


def _load_evidence(value: object, identity: PlayerIdentity) -> Evidence:
    raw = _object(value, "evidence")
    _validate_keys(raw, _EVIDENCE_FIELDS, _EVIDENCE_FIELDS, "evidence")
    raw_profile_url = raw.get("profile_url")
    if (
        isinstance(raw_profile_url, str)
        and raw_profile_url != raw_profile_url.strip()
    ):
        raise PlayerSpecError(
            "evidence profile_url must not have surrounding whitespace"
        )
    profile_url = _https_url(raw_profile_url, "evidence profile_url")
    profile_match = _PES_RETRO_STATS_PROFILE_RE.fullmatch(profile_url)
    if (
        profile_match is None
        or profile_match.group("prefix") != identity.pes_retro_stats_id[:8]
    ):
        raise PlayerSpecError(
            "evidence profile_url must be the canonical Pes Retro Stats profile "
            "for identity pes_retro_stats_id"
        )
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
        profile_url=profile_url,
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
            raw, "preferred_shirt_number", 1, 99, "create PES data"
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


def _generated_draft_text(
    value: object, context: str, maximum: int | None
) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or (maximum is not None and len(value) > maximum)
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
    ):
        raise PlayerSpecError(f"{context} must be canonical text")
    return value


def _generated_source_text(value: object, context: str) -> str:
    text = _generated_draft_text(value, context, None)
    if " ".join(text.split()) != text:
        raise PlayerSpecError(f"{context} must use normalized source text")
    return text


def _generated_draft_https_url(
    value: object, context: str, maximum: int | None
) -> str:
    url = _generated_draft_text(value, context, maximum)
    if any(character.isspace() for character in url):
        raise PlayerSpecError(f"{context} must be a canonical HTTPS URL")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except (TypeError, ValueError):
        raise PlayerSpecError(
            f"{context} must be a canonical HTTPS URL"
        ) from None
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
    ):
        raise PlayerSpecError(f"{context} must be a canonical HTTPS URL")
    return url


def _reject_proposal_nulls(value: object, context: str) -> None:
    if value is None:
        raise PlayerSpecError(f"{context} must not be null")
    if isinstance(value, Mapping):
        for key, nested in value.items():
            _reject_proposal_nulls(nested, f"{context}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _reject_proposal_nulls(nested, f"{context}[{index}]")


def _load_proposal_spec(path: Path, raw: Mapping[str, object]) -> PlayerSpec:
    context = f"player spec {path}"
    _validate_keys(raw, _DRAFT_TOP_LEVEL_FIELDS, _DRAFT_TOP_LEVEL_FIELDS, context)
    for field in ("lifecycle", "applies_to", "identity", "evidence", "pes", "draft"):
        _reject_proposal_nulls(raw[field], f"{context}.{field}")

    schema_version = _integer(raw, "schema_version", 2, 2, context)
    operation = _text(raw, "operation", context)
    if operation not in {"create", "update"}:
        raise PlayerSpecError(f"{context} operation must be create or update")

    lifecycle_context = f"{context} lifecycle"
    lifecycle = _object(raw.get("lifecycle"), lifecycle_context)
    _validate_keys(
        lifecycle,
        frozenset({"status"}),
        frozenset({"status"}),
        lifecycle_context,
    )
    if lifecycle["status"] != "active":
        raise PlayerSpecError(f"{lifecycle_context} status must be active")

    applies_to = _string_list(raw.get("applies_to"), f"{context} applies_to")
    if len(applies_to) != 1 or raw["applies_to"] != list(applies_to):
        raise PlayerSpecError(
            f"{context} applies_to must contain one exact revision"
        )

    identity_context = f"{context} identity"
    identity = _load_identity(raw.get("identity"))
    if (
        identity.name != " ".join(identity.name.split())
        or len(identity.name.encode("utf-8")) > 60
        or not normalize_player_identity(identity.name)
    ):
        raise PlayerSpecError(f"{identity_context} name is not canonical")
    if identity.print_name is None:
        raise PlayerSpecError(
            f"{identity_context} print_name is required for a complete proposal"
        )
    if (
        identity.print_name != " ".join(identity.print_name.split())
        or len(identity.print_name.encode("utf-8")) > 60
    ):
        raise PlayerSpecError(f"{identity_context} print_name is not canonical")
    if raw["identity"].get("aliases") != [identity.name]:
        raise PlayerSpecError(
            f"{identity_context} aliases must contain the exact submitted name"
        )
    if path.stem != player_slug(identity.name):
        raise PlayerSpecError(
            f"player spec filename {path.name!r} does not match identity name "
            f"{identity.name!r}"
        )
    if len(path.name.encode("utf-8")) > 240:
        raise PlayerSpecError(
            f"player spec filename {path.name!r} exceeds 240 UTF-8 bytes"
        )

    source_context = f"{context} source"
    source_raw = _object(raw.get("source"), source_context)
    try:
        source = profile_from_snapshot(source_raw)
    except (KeyError, TypeError, ValueError) as exc:
        message = str(exc)
        if "snapshot_sha256 mismatch" in message:
            evidence_candidate = raw.get("evidence")
            candidate_team = (
                evidence_candidate.get("current_team")
                if isinstance(evidence_candidate, Mapping)
                else None
            )
            source_data = source_raw.get("data")
            source_club = (
                source_data.get("current_club")
                if isinstance(source_data, Mapping)
                else None
            )
            field = (
                "data.current_club"
                if isinstance(candidate_team, str) and source_club != candidate_team
                else "snapshot_sha256"
            )
            raise PlayerSpecError(
                f"{source_context}.{field}: {message}"
            ) from None
        raise PlayerSpecError(f"{source_context} is invalid: {exc}") from None
    if source.player_id != identity.pes_retro_stats_id:
        raise PlayerSpecError(
            f"{source_context} player_id must match identity pes_retro_stats_id"
        )
    if source.profile_url != source.profile_url.strip():
        raise PlayerSpecError(f"{source_context} profile_url is not canonical")
    if normalize_player_identity(source.name) != normalize_player_identity(
        identity.name
    ):
        raise PlayerSpecError(f"{source_context} name must match identity name")

    evidence_context = f"{context} evidence"
    evidence_raw = _object(raw.get("evidence"), evidence_context)
    _validate_keys(
        evidence_raw,
        _DRAFT_EVIDENCE_FIELDS,
        _DRAFT_EVIDENCE_FIELDS,
        evidence_context,
    )
    evidence_profile = _generated_draft_https_url(
        evidence_raw["profile_url"], f"{evidence_context} profile_url", None
    )
    if evidence_profile != source.profile_url:
        raise PlayerSpecError(
            f"{evidence_context} profile_url must match source profile_url"
        )
    proof_raw = evidence_raw["proof_urls"]
    if not isinstance(proof_raw, list) or not proof_raw or len(proof_raw) > 10:
        raise PlayerSpecError(
            f"{evidence_context} proof_urls must be a non-empty list"
        )
    proof_urls = tuple(
        _generated_draft_https_url(
            item, f"{evidence_context} proof_urls[{index}]", 300
        )
        for index, item in enumerate(proof_raw)
    )
    if len(set(proof_urls)) != len(proof_urls):
        raise PlayerSpecError(
            f"{evidence_context} proof_urls contains duplicate URLs"
        )
    effective_date_raw = _generated_draft_text(
        evidence_raw["effective_date"], f"{evidence_context} effective_date", 10
    )
    if not _ISO_DATE_RE.fullmatch(effective_date_raw):
        raise PlayerSpecError(
            f"{evidence_context} effective_date must use YYYY-MM-DD"
        )
    try:
        effective_date = date.fromisoformat(effective_date_raw)
    except ValueError:
        raise PlayerSpecError(
            f"{evidence_context} effective_date must be a valid date"
        ) from None
    submitted_team = _generated_draft_text(
        evidence_raw["current_team"], f"{evidence_context} current_team", 100
    )
    issue_number = evidence_raw["issue_number"]
    if (
        isinstance(issue_number, bool)
        or not isinstance(issue_number, int)
        or issue_number <= 0
    ):
        raise PlayerSpecError(
            f"{evidence_context} issue_number must be a positive integer"
        )
    issue_url = _generated_draft_https_url(
        evidence_raw["issue_url"], f"{evidence_context} issue_url", 500
    )
    parsed_issue_url = urlsplit(issue_url)
    issue_match = _DRAFT_ISSUE_PATH_RE.fullmatch(parsed_issue_url.path)
    if (
        parsed_issue_url.hostname != "github.com"
        or parsed_issue_url.query
        or parsed_issue_url.fragment
        or issue_match is None
        or int(issue_match.group("issue_number")) != issue_number
    ):
        raise PlayerSpecError(
            f"{evidence_context} issue_url must match issue_number"
        )

    try:
        mapped = map_pes21_proposal(source, effective_date=effective_date)
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise PlayerSpecError(
            f"{source_context} cannot be mapped to PES 2021: {exc}"
        ) from None

    if operation == "create":
        try:
            create = _load_create(raw.get("pes"), identity)
        except PlayerSpecError as exc:
            message = str(exc)
            if "player_id must match identity pes_id" in message:
                raise PlayerSpecError(
                    f"{context} identity.pes_id: {message}"
                ) from None
            raise
        patches: Mapping[str, FieldPatch] = MappingProxyType({})
        registered_position = create.registered_position
        position_proficiency = create.position_proficiency
    else:
        create = None
        patches = _load_update(raw.get("pes"))
        registered_position = mapped.registered_position
        position_proficiency = mapped.position_proficiency

    draft_context = f"{context} draft"
    draft = _object(raw.get("draft"), draft_context)
    _validate_keys(
        draft,
        _PROPOSAL_DRAFT_FIELDS,
        _PROPOSAL_DRAFT_FIELDS,
        draft_context,
    )
    generator = _generated_draft_text(
        draft["generator"], f"{draft_context} generator", 100
    )
    if generator != "pes-retro-mature-proposal-v1":
        raise PlayerSpecError(
            f"{draft_context} generator must be "
            "'pes-retro-mature-proposal-v1'"
        )
    if draft["needs_human_review"] is not True:
        raise PlayerSpecError(
            f"{draft_context} needs_human_review must be true"
        )
    review = _object(draft["ovr_review"], f"{draft_context} ovr_review")
    if isinstance(registered_position, str):
        registered_position_candidates = (registered_position,)
    elif operation == "update":
        registered_position_candidates = POSITION_NAMES
    else:
        raise PlayerSpecError(
            f"{draft_context}.ovr_review.positions: requires a registered position"
        )
    ovr_validation_error: Exception | None = None
    for candidate_position in registered_position_candidates:
        try:
            validate_ovr_review_shape(
                review,
                operation=operation,
                registered_position=candidate_position,
                position_proficiency=position_proficiency,
            )
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            ovr_validation_error = exc
        else:
            ovr_validation_error = None
            break
    if ovr_validation_error is not None:
        message = str(ovr_validation_error)
        field = (
            "positions[0].proposal_tenths"
            if "tenths" in message and "delta" not in message
            else "positions[0].delta_tenths"
            if "delta" in message
            else "positions"
            if "positions" not in review
            else "mode"
            if "mode" in message
            else "positions"
            if "position" in message
            else "model"
        )
        raise PlayerSpecError(
            f"{draft_context}.ovr_review.{field}: {message}"
        ) from None
    evidence = Evidence(
        profile_url=evidence_profile,
        proof_urls=proof_urls,
        effective_date=effective_date,
        reason="",
    )
    proposal = ProposalMetadata(
        generator=generator,
        needs_human_review=True,
        source_snapshot=source_raw,
        ovr_review=review,
        issue_number=issue_number,
        issue_url=issue_url,
        submitted_team=submitted_team,
    )
    return PlayerSpec(
        path=path,
        schema_version=schema_version,
        operation=operation,
        lifecycle_status="active",
        lifecycle_reason="",
        superseded_by=None,
        applies_to=applies_to,
        identity=identity,
        evidence=evidence,
        create=create,
        patches=patches,
        proposal=proposal,
    )

def _load_one_spec(
    path: Path, *, allow_proposals: bool = False
) -> PlayerSpec:
    raw = _object(_read_json(path, "player spec"), f"player spec {path}")
    if "draft" in raw:
        if not allow_proposals:
            raise PlayerSpecError(
                f"player spec {path.name} requires human approval"
            )
        return _load_proposal_spec(path, raw)

    _validate_keys(raw, _TOP_LEVEL_FIELDS, _TOP_LEVEL_FIELDS, f"player spec {path}")

    schema_version = _integer(raw, "schema_version", 2, 2, f"player spec {path}")
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
    evidence = _load_evidence(raw.get("evidence"), identity)

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
        proposal=None,
    )


def validate_spec_set(specs: tuple[PlayerSpec, ...]) -> None:
    """Reject identities that are ambiguous across player spec files."""
    pes_ids: dict[int, Path] = {}
    pes_retro_stats_ids: dict[str, Path] = {}
    aliases: dict[str, Path] = {}
    for spec in specs:
        identity = spec.identity
        if identity.pes_id in pes_ids:
            raise PlayerSpecError(
                f"duplicate PES ID {identity.pes_id} in "
                f"{pes_ids[identity.pes_id]} and {spec.path}"
            )
        pes_ids[identity.pes_id] = spec.path
        if identity.pes_retro_stats_id in pes_retro_stats_ids:
            raise PlayerSpecError(
                "duplicate Pes Retro Stats ID "
                f"{identity.pes_retro_stats_id} in "
                f"{pes_retro_stats_ids[identity.pes_retro_stats_id]} and {spec.path}"
            )
        pes_retro_stats_ids[identity.pes_retro_stats_id] = spec.path
        for alias in identity.aliases:
            normalized = normalize_player_identity(alias)
            if normalized in aliases and aliases[normalized] != spec.path:
                raise PlayerSpecError(
                    f"duplicate normalized alias {alias!r} in "
                    f"{aliases[normalized]} and {spec.path}"
                )
            aliases[normalized] = spec.path


def load_player_specs(
    directory: str | Path | None = None,
    *,
    allow_proposals: bool = False,
) -> tuple[PlayerSpec, ...]:
    """Load all player spec JSON files in deterministic filename order."""
    specs_dir = Path(directory) if directory is not None else config.PLAYER_SPECS_DIR
    try:
        paths = sorted(specs_dir.glob("*.json"), key=lambda path: path.name)
    except OSError as exc:
        raise PlayerSpecError(
            f"could not list player spec directory {specs_dir}: {exc}"
        ) from exc
    specs = tuple(
        _load_one_spec(path, allow_proposals=allow_proposals) for path in paths
    )
    validate_spec_set(specs)
    return specs


def _result(
    spec: PlayerSpec,
    status: str,
    reason: str,
    *,
    pes_id: int | None = None,
    diagnostic: str | None = None,
) -> SpecResult:
    return SpecResult(
        pes_id=spec.identity.pes_id if pes_id is None else pes_id,
        name=spec.identity.name,
        status=status,
        reason=reason,
        diagnostic=diagnostic,
    )


def _matches_identity(player: "PlayerInfo", identity_keys: set[str]) -> bool:
    if normalize_player_identity(player.name) in identity_keys:
        return True
    return bool(
        player.print_name
        and normalize_player_identity(player.print_name) in identity_keys
    )


def assess_create(
    edit_file: "EditFile",
    spec: PlayerSpec,
    all_players: Mapping[int, "PlayerInfo"],
) -> SpecResult:
    """Assess create idempotency and destination safety without mutation."""
    if spec.lifecycle_status != "active":
        return _result(
            spec,
            spec.lifecycle_status,
            spec.lifecycle_reason or f"lifecycle_{spec.lifecycle_status}",
        )
    if spec.operation != "create" or spec.create is None:
        return _result(spec, "rejected", "operation_is_not_create")

    create = spec.create
    teams = edit_file.get_all_team_info()
    destination = teams.get(create.team_id)
    if destination is None:
        return _result(spec, "rejected", "destination_team_missing")
    if destination.name != create.team_name:
        return _result(spec, "rejected", "destination_team_name_mismatch")

    identity = spec.identity
    identity_keys = {
        normalize_player_identity(value)
        for value in (identity.name, *identity.aliases)
    }
    id_match = all_players.get(identity.pes_id)
    if id_match is not None:
        if not _matches_identity(id_match, identity_keys):
            return _result(spec, "rejected", "pes_id_identity_mismatch")
        return _result(spec, "already_applied", "matching_player_exists")

    for player in all_players.values():
        if _matches_identity(player, identity_keys):
            return _result(
                spec,
                "already_applied",
                "matching_identity_exists",
                pes_id=player.player_id,
            )

    roster = edit_file.get_team_roster(create.team_id)
    if roster is None:
        return _result(spec, "rejected", "destination_roster_missing")
    if roster.is_full:
        return _result(spec, "waiting", "destination_roster_full")
    return _result(spec, "ready", "eligible")


def apply_create(
    edit_file: "EditFile",
    spec: PlayerSpec,
    all_players: Mapping[int, "PlayerInfo"],
) -> SpecResult:
    """Atomically serialize and register one reviewed created player."""
    assessment = assess_create(edit_file, spec, all_players)
    if assessment.status != "ready":
        return assessment

    create = spec.create
    if create is None:
        raise AssertionError("ready create assessment requires create data")

    original_data = bytes(edit_file._data)
    original_catalog_report = edit_file.player_catalog_report
    original_transferred_ids = set(edit_file.transferred_player_ids)
    try:
        player_entry, appearance_entry = serialize_created_player(create)
        edit_file.append_created_player(
            spec.identity.pes_id,
            player_entry,
            appearance_entry,
        )
        added = edit_file.add_player(
            spec.identity.pes_id,
            create.team_id,
            preferred_shirt_number=create.preferred_shirt_number,
            position=create.registered_position,
            allow_overflow_release=False,
        )
        if not added:
            raise PlayerSpecError(
                f"could not register created player {spec.identity.name} "
                f"to team {create.team_id}"
            )
    except Exception:
        edit_file._data = bytearray(original_data)
        edit_file._parse_header()
        edit_file._calculate_offsets()
        edit_file.player_catalog_report = original_catalog_report
        edit_file.transferred_player_ids.clear()
        edit_file.transferred_player_ids.update(original_transferred_ids)
        raise

    return _result(spec, "created", "created_and_registered")


def _raw_codec_field(entry: bytes, field: str) -> int:
    field_spec = FIELD_SPECS[field]
    byte_count = (field_spec.bit_offset + field_spec.width + 7) // 8
    chunk = int.from_bytes(
        entry[
            field_spec.byte_offset : field_spec.byte_offset + byte_count
        ],
        "little",
    )
    return (chunk >> field_spec.bit_offset) & ((1 << field_spec.width) - 1)


def _decoded_patch_value(profile, entry: bytes, field: str) -> int:
    if field in ABILITY_FIELDS:
        return profile.abilities[field]
    if field.startswith("position_"):
        return profile.position_proficiency[field.removeprefix("position_").upper()]
    if field == "registered_position":
        return profile.registered_position_id
    if field in {"nationality_id", "age", "height", "weight", "playing_style"}:
        return getattr(profile, field)
    return _raw_codec_field(entry, field)


def _required_update_markers(fields: set[str]) -> dict[str, int]:
    markers: dict[str, int] = {}
    if fields.intersection(ABILITY_FIELDS):
        markers["edited_abilities"] = 1
    if fields.intersection(
        _UPDATE_DIRECT_FIELDS - {"registered_position", "playing_style"}
    ):
        markers["edited_basic_settings"] = 1
    if "registered_position" in fields:
        markers["edited_registered_position"] = 1
    if any(field.startswith("position_") for field in fields):
        markers["edited_playable_positions"] = 1
    if "playing_style" in fields:
        markers["edited_playing_style"] = 1
    if any(field.startswith("skill_") for field in fields):
        markers["edited_skills"] = 1
    if any(field.startswith("com_style_") for field in fields):
        markers["edited_com_styles"] = 1
    return markers


def _assess_update_state(
    edit_file: "EditFile",
    spec: PlayerSpec,
    all_players: Mapping[int, "PlayerInfo"],
) -> tuple[SpecResult, bytes | None]:
    if spec.lifecycle_status != "active":
        return _result(spec, "rejected", "lifecycle_is_not_active"), None
    if spec.operation != "update" or not spec.patches:
        return _result(spec, "rejected", "operation_is_not_update"), None

    identity = spec.identity
    identity_keys = {
        normalize_player_identity(value)
        for value in (identity.name, *identity.aliases)
    }
    player = all_players.get(identity.pes_id)
    if player is None:
        return _result(spec, "rejected", "pes_id_missing"), None
    if not _matches_identity(player, identity_keys):
        return _result(spec, "rejected", "pes_id_identity_mismatch"), None

    entry = edit_file.get_edited_player_entry(identity.pes_id)
    if entry is None:
        return _result(spec, "rejected", "edited_player_record_missing"), None

    profile = decode_player_entry(entry)
    if profile.player_id != identity.pes_id:
        return _result(spec, "rejected", "edited_player_identity_mismatch"), None

    all_current = True
    all_target = True
    for field, patch in spec.patches.items():
        value = _decoded_patch_value(profile, entry, field)
        all_current = all_current and value == patch.current
        all_target = all_target and value == patch.target

    if all_current:
        return _result(spec, "ready", "all_current"), entry
    if all_target:
        markers = _required_update_markers(set(spec.patches))
        if any(
            _decoded_patch_value(profile, entry, field) != target
            for field, target in markers.items()
        ):
            return _result(spec, "ready", "required_edit_marker_missing"), entry
        return _result(spec, "already_applied", "all_target"), entry
    return _result(spec, "conflict", "mixed_or_unexpected_values"), entry


def assess_update(
    edit_file: "EditFile",
    spec: PlayerSpec,
    all_players: Mapping[int, "PlayerInfo"],
) -> SpecResult:
    """Assess a whole-spec update without mutating the edited player record."""
    result, _ = _assess_update_state(edit_file, spec, all_players)
    return result


def _effective_update_targets(spec: PlayerSpec) -> dict[str, int]:
    targets = {field: patch.target for field, patch in spec.patches.items()}
    targets.update(_required_update_markers(set(spec.patches)))
    return targets


def apply_update(
    edit_file: "EditFile",
    spec: PlayerSpec,
    all_players: Mapping[int, "PlayerInfo"],
) -> SpecResult:
    """Apply a whole-spec patch only when every current value matches."""
    assessment, entry = _assess_update_state(edit_file, spec, all_players)
    if assessment.status != "ready":
        return assessment
    if entry is None:
        raise AssertionError("applicable update assessment requires an edited record")

    targets = _effective_update_targets(spec)
    patched_entry = patch_player_entry(entry, targets)
    edit_file.replace_edited_player_entry(spec.identity.pes_id, patched_entry)
    return _result(spec, "updated", "patched")


def _restore_spec_mutation(
    edit_file: "EditFile",
    original_data: bytes,
    original_catalog_report: object,
    original_transferred_ids: set[int],
    had_player_cache: bool,
    original_player_cache: object,
) -> None:
    edit_file._data = bytearray(original_data)
    edit_file._parse_header()
    edit_file._calculate_offsets()
    edit_file.player_catalog_report = original_catalog_report
    edit_file.transferred_player_ids.clear()
    edit_file.transferred_player_ids.update(original_transferred_ids)
    if had_player_cache:
        edit_file._player_cache = original_player_cache
    elif hasattr(edit_file, "_player_cache"):
        del edit_file._player_cache


def apply_player_spec(
    edit_file: "EditFile",
    spec: PlayerSpec,
    base_revision: str,
    all_players: Mapping[int, "PlayerInfo"],
) -> SpecResult:
    """Apply one lifecycle-compatible spec with mutation isolation."""
    if spec.lifecycle_status != "active":
        return _result(
            spec,
            spec.lifecycle_status,
            spec.lifecycle_reason or f"lifecycle_{spec.lifecycle_status}",
        )
    if base_revision not in spec.applies_to:
        return _result(spec, "needs_review", "base_revision_not_reviewed")

    original_data = bytes(edit_file._data)
    original_catalog_report = edit_file.player_catalog_report
    original_transferred_ids = set(edit_file.transferred_player_ids)
    had_player_cache = hasattr(edit_file, "_player_cache")
    original_player_cache = getattr(edit_file, "_player_cache", None)

    try:
        if spec.operation == "create":
            result = apply_create(edit_file, spec, all_players)
        elif spec.operation == "update":
            result = apply_update(edit_file, spec, all_players)
        else:
            result = _result(spec, "rejected", "unsupported_operation")
    except Exception as exc:
        result = _result(
            spec,
            "rejected",
            "mutation_failed",
            diagnostic=f"{type(exc).__name__}: {exc}",
        )

    mutated = (
        edit_file._data != original_data
        or edit_file.player_catalog_report != original_catalog_report
        or edit_file.transferred_player_ids != original_transferred_ids
        or hasattr(edit_file, "_player_cache") != had_player_cache
        or (
            had_player_cache
            and getattr(edit_file, "_player_cache", None) is not original_player_cache
        )
    )
    if result.status == "rejected" and mutated:
        _restore_spec_mutation(
            edit_file,
            original_data,
            original_catalog_report,
            original_transferred_ids,
            had_player_cache,
            original_player_cache,
        )
    return result


def apply_player_specs(
    edit_file: "EditFile",
    specs: tuple[PlayerSpec, ...],
    base_revision: str,
    all_players: Mapping[int, "PlayerInfo"],
) -> tuple[SpecResult, ...]:
    """Apply independent specs in deterministic filename order."""
    ordered_specs = sorted(specs, key=lambda spec: spec.path.name)
    current_players = dict(all_players)
    results: list[SpecResult] = []
    for spec in ordered_specs:
        result = apply_player_spec(edit_file, spec, base_revision, current_players)
        results.append(result)
        if result.status == "created":
            current_players.update(
                edit_file.get_all_players(include_base_db=False)
            )
    return tuple(results)
