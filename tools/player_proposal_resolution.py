"""Deterministic, offline resolution of fields required to create a PES player."""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from types import MappingProxyType
import unicodedata

import config
from editor.editfile import EditFile
from editor.models import TeamInfo
from editor.player_spec import normalize_player_identity


_CATALOG_SCHEMA_VERSION = 1
_CATALOG_SOURCE = {
    "url": (
        "https://github.com/xAranaktu/PES-2021-Cheat-Table/blob/master/"
        "PES%202021%20-%20v21.1.0.CT"
    ),
    "license": "MIT",
    "copyright": "Copyright (c) 2020 Paweł",
}
_CREATED_PLAYER_IDS = range(200_000, 300_000)
_APPEARANCE_PALETTE_V1 = (
    (3, 17),
    (2, 17),
    (4, 17),
    (1, 17),
    (5, 17),
    (12, 16),
    (30, 17),
    (9, 17),
)


@dataclass(frozen=True, slots=True)
class NationalityCatalog:
    """Immutable canonical-name and normalized lookup maps for nationalities."""

    id_to_name: Mapping[int, str]
    name_to_id: Mapping[str, int]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "id_to_name", MappingProxyType(dict(self.id_to_name))
        )
        object.__setattr__(
            self, "name_to_id", MappingProxyType(dict(self.name_to_id))
        )

    def __len__(self) -> int:
        return len(self.id_to_name)

    def resolve(self, query: str) -> int:
        """Resolve a canonical label or explicit alias to its PES nationality ID."""
        key = _normalize_lookup_text(query, "nationality query")
        try:
            return self.name_to_id[key]
        except KeyError:
            raise ValueError(f"unknown nationality {query!r}") from None


@dataclass(frozen=True, slots=True)
class CreateResolution:
    player_id: int
    print_name: str
    team_id: int
    team_name: str
    nationality_id: int
    skin_color: int
    iris_color: int


def _normalize_lookup_text(value: object, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context} must be a non-empty string")
    folded = unicodedata.normalize("NFKC", value).casefold()
    key = " ".join(
        "".join(character if character.isalnum() else " " for character in folded)
        .split()
    )
    if not key:
        raise ValueError(f"{context} must contain letters or numbers")
    return key


def _json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _require_exact_keys(
    value: object, expected: set[str], context: str
) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{context} must be an object")
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        details: list[str] = []
        if missing:
            details.append(f"missing fields {missing}")
        if unknown:
            details.append(f"unknown fields {unknown}")
        raise ValueError(f"{context} has " + " and ".join(details))
    return value


def _catalog_text(value: object, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context} must be a non-empty string")
    return value.strip()


def load_nationality_catalog(
    path: str | Path | None = None,
) -> NationalityCatalog:
    """Load and strictly validate the committed offline nationality catalog."""
    catalog_path = (
        Path(path)
        if path is not None
        else config.DATA_DIR / "pes21_nationalities.json"
    )
    try:
        raw = json.loads(
            catalog_path.read_text(encoding="utf-8"), object_pairs_hook=_json_object
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(
            f"could not load nationality catalog {catalog_path}: {error}"
        ) from error

    document = _require_exact_keys(
        raw, {"schema_version", "source", "nationalities"}, "nationality catalog"
    )
    schema_version = document["schema_version"]
    if type(schema_version) is not int or schema_version != _CATALOG_SCHEMA_VERSION:
        raise ValueError(
            f"nationality catalog schema_version must be {_CATALOG_SCHEMA_VERSION}"
        )

    source = _require_exact_keys(
        document["source"], {"url", "license", "copyright"}, "catalog source"
    )
    if source != _CATALOG_SOURCE:
        raise ValueError("nationality catalog source metadata is not authoritative")

    records = document["nationalities"]
    if not isinstance(records, list) or not records:
        raise ValueError("nationality catalog nationalities must be a non-empty list")

    id_to_name: dict[int, str] = {}
    name_to_id: dict[str, int] = {}
    for index, value in enumerate(records):
        context = f"nationality record {index}"
        record = _require_exact_keys(value, {"id", "name", "aliases"}, context)
        nationality_id = record["id"]
        if (
            type(nationality_id) is not int
            or nationality_id < 1
            or nationality_id > 65_535
        ):
            raise ValueError(f"{context} id must be an integer in 1..65535")
        if nationality_id in id_to_name:
            raise ValueError(f"duplicate nationality id {nationality_id}")

        canonical_name = _catalog_text(record["name"], f"{context} name")
        aliases = record["aliases"]
        if not isinstance(aliases, list):
            raise ValueError(f"{context} aliases must be a list")

        labels = [canonical_name]
        labels.extend(
            _catalog_text(alias, f"{context} alias") for alias in aliases
        )
        for label in labels:
            key = _normalize_lookup_text(label, f"{context} label")
            if key in name_to_id:
                raise ValueError(f"duplicate normalized nationality label {label!r}")
            name_to_id[key] = nationality_id
        id_to_name[nationality_id] = canonical_name

    return NationalityCatalog(id_to_name=id_to_name, name_to_id=name_to_id)


def _uuid_digest(uuid_text: str) -> bytes:
    if not isinstance(uuid_text, str) or not uuid_text:
        raise ValueError("player UUID must be a non-empty string")
    return hashlib.sha256(uuid_text.encode("utf-8")).digest()


def allocate_created_player_id(
    uuid_text: str,
    unavailable_ids: Collection[int],
    id_range: range = _CREATED_PLAYER_IDS,
) -> int:
    """Allocate a stable ID, circularly probing until an available slot is found."""
    if not isinstance(id_range, range) or len(id_range) == 0:
        raise ValueError("created-player ID range must be a non-empty range")
    if not isinstance(unavailable_ids, Collection):
        raise TypeError("unavailable_ids must support membership tests")

    seed = int.from_bytes(_uuid_digest(uuid_text)[:8], byteorder="big", signed=False)
    initial_index = seed % len(id_range)
    for offset in range(len(id_range)):
        candidate = id_range[(initial_index + offset) % len(id_range)]
        if candidate not in unavailable_ids:
            return candidate
    raise ValueError("created-player ID range is exhausted")


def derive_generic_appearance(uuid_text: str) -> tuple[int, int]:
    """Return the stable generic skin/iris pair for a source player UUID."""
    digest = _uuid_digest(uuid_text)
    return _APPEARANCE_PALETTE_V1[
        digest[8] % len(_APPEARANCE_PALETTE_V1)
    ]


def _meaningful_final_token(name: str) -> str:
    tokens = name.split()
    token = tokens[-1]
    start = 0
    end = len(token)
    while start < end and unicodedata.category(token[start])[0] in "CZPS":
        start += 1
    while end > start and unicodedata.category(token[end - 1])[0] in "CZPS":
        end -= 1
    candidate = token[start:end]
    if candidate:
        return candidate
    return " ".join(tokens)


def derive_print_name(player_name: str) -> str:
    """Derive the NFC uppercase final surname token used on the player's strip."""
    if not isinstance(player_name, str) or not player_name.strip():
        raise ValueError("player name must be a non-empty string")
    normalized_name = unicodedata.normalize("NFC", player_name)
    print_name = unicodedata.normalize(
        "NFC", _meaningful_final_token(normalized_name).upper()
    )
    encoded = print_name.encode("utf-8")
    if len(encoded) > 60:
        raise ValueError("print name must not exceed 60 UTF-8 bytes")
    return print_name


def _team_lookup_key(value: object, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context} must be a non-empty string")
    key = normalize_player_identity(unicodedata.normalize("NFKC", value))
    if not key:
        raise ValueError(f"{context} must contain letters or numbers")
    return key


def _team_index(edit_file: EditFile) -> dict[str, list[TeamInfo]]:
    teams = edit_file.get_all_team_info()
    if not isinstance(teams, Mapping):
        raise ValueError("EditFile team accessor must return a mapping")
    index: dict[str, list[TeamInfo]] = {}
    for team in teams.values():
        if not isinstance(team, TeamInfo):
            raise ValueError("EditFile team accessor returned invalid team metadata")
        if not isinstance(team.name, str):
            raise ValueError(f"team {team.team_id} name must be a string")
        normalized_name = unicodedata.normalize("NFKC", team.name)
        key = normalize_player_identity(normalized_name)
        if not key:
            continue
        index.setdefault(key, []).append(team)
    return index


def _alias_index(team_aliases: Mapping[str, str]) -> dict[str, tuple[str, str]]:
    if not isinstance(team_aliases, Mapping):
        raise ValueError("team_aliases must be a mapping")
    aliases: dict[str, tuple[str, str]] = {}
    for alias, canonical_name in team_aliases.items():
        alias_key = _team_lookup_key(alias, "team alias")
        target_key = _team_lookup_key(canonical_name, f"target for alias {alias!r}")
        if alias_key in aliases:
            raise ValueError(f"ambiguous normalized team alias {alias!r}")
        aliases[alias_key] = (target_key, canonical_name)
    return aliases


def _resolve_team_name(
    name: str,
    teams: Mapping[str, list[TeamInfo]],
    aliases: Mapping[str, tuple[str, str]],
) -> TeamInfo:
    query_key = _team_lookup_key(name, "team name")
    candidates = list(teams.get(query_key, ()))
    alias_target = aliases.get(query_key)
    if alias_target is not None:
        target_key, _ = alias_target
        candidates.extend(teams.get(target_key, ()))

    unique = {team.team_id: team for team in candidates}
    if not unique:
        if alias_target is not None:
            raise ValueError(
                f"unknown team {name!r}: alias targets {alias_target[1]!r}"
            )
        raise ValueError(f"unknown team {name!r}")
    if len(unique) != 1:
        raise ValueError(f"ambiguous team name {name!r}")
    return next(iter(unique.values()))


def resolve_create_team(
    edit_file: EditFile,
    submitted_team: str,
    source_team: str,
    team_aliases: Mapping[str, str],
) -> TeamInfo:
    """Resolve submitted/source names to one rostered canonical EditFile team."""
    teams = _team_index(edit_file)
    aliases = _alias_index(team_aliases)
    submitted = _resolve_team_name(submitted_team, teams, aliases)
    sourced = _resolve_team_name(source_team, teams, aliases)
    if submitted.team_id != sourced.team_id:
        raise ValueError(
            f"submitted team {submitted_team!r} conflicts with "
            f"source team {source_team!r}"
        )
    roster = edit_file.get_team_roster(submitted.team_id)
    if roster is None or roster.roster_size == 0:
        raise ValueError(
            f"team {submitted.name!r} ({submitted.team_id}) has no roster"
        )
    return submitted


def _source_text(source: object, field: str) -> str:
    if isinstance(source, Mapping):
        value = source.get(field)
    else:
        value = getattr(source, field, None)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"source {field} must be a non-empty string")
    return value.strip()


def resolve_create_fields(
    edit_file: EditFile,
    *,
    source: object,
    submitted_team: str,
    completed_player_ids: Iterable[int],
    nationality_catalog: NationalityCatalog | None = None,
    team_aliases: Mapping[str, str] | None = None,
) -> CreateResolution:
    """Resolve every deterministic create field without mutating the EditFile."""
    player_uuid = _source_text(source, "player_id")
    player_name = _source_text(source, "name")
    nationality = _source_text(source, "nationality")
    source_team = _source_text(source, "current_club")

    team = resolve_create_team(
        edit_file,
        submitted_team,
        source_team,
        team_aliases if team_aliases is not None else {},
    )
    catalog = (
        nationality_catalog
        if nationality_catalog is not None
        else load_nationality_catalog()
    )
    if not isinstance(catalog, NationalityCatalog):
        raise ValueError("nationality_catalog must be a NationalityCatalog")

    _missing_report = object()
    previous_catalog_report = getattr(
        edit_file, "player_catalog_report", _missing_report
    )
    try:
        base_players = edit_file.get_all_players()
    finally:
        if previous_catalog_report is _missing_report:
            if hasattr(edit_file, "player_catalog_report"):
                delattr(edit_file, "player_catalog_report")
        else:
            edit_file.player_catalog_report = previous_catalog_report
    if not isinstance(base_players, Mapping):
        raise ValueError("EditFile player accessor must return a mapping")
    unavailable_ids = set(base_players)
    unavailable_ids.update(completed_player_ids)
    player_id = allocate_created_player_id(player_uuid, unavailable_ids)
    skin_color, iris_color = derive_generic_appearance(player_uuid)

    return CreateResolution(
        player_id=player_id,
        print_name=derive_print_name(player_name),
        team_id=team.team_id,
        team_name=team.name,
        nationality_id=catalog.resolve(nationality),
        skin_color=skin_color,
        iris_color=iris_color,
    )
