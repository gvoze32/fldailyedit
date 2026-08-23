"""Optional offline release protection and player-usage metadata."""

from __future__ import annotations

import csv
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
import tempfile
from types import MappingProxyType
from typing import Any, Mapping

import config


class ReleasePolicyError(ValueError):
    """A release policy snapshot is malformed or unsafe to use."""


@dataclass(frozen=True, slots=True)
class PlayerUsage:
    """Season usage counters used only as a reserve tie-breaker."""

    minutes: int
    starts: int
    appearances: int
    news_mentions: int = 0

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ReleasePolicy:
    """Immutable-by-convention offline release policy data."""

    protected_players: Mapping[int, frozenset[int]]
    usage: Mapping[int, PlayerUsage]
    source: str = ""
    season: str = ""
    as_of: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "protected_players",
            MappingProxyType(dict(self.protected_players)),
        )
        object.__setattr__(self, "usage", MappingProxyType(dict(self.usage)))

    @classmethod
    def empty(cls) -> "ReleasePolicy":
        return cls({}, {})

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "source": self.source,
            "season": self.season,
            "as_of": self.as_of,
            "protected_players": {
                str(team_id): sorted(player_ids)
                for team_id, player_ids in sorted(self.protected_players.items())
            },
            "usage": {
                str(player_id): usage.to_dict()
                for player_id, usage in sorted(self.usage.items())
            },
        }


def _text(value: object, context: str) -> str:
    if not isinstance(value, str) or value != value.strip():
        raise ReleasePolicyError(f"{context} must be canonical text")
    return value


def _nonnegative_int(value: object, context: str) -> int:
    if type(value) is not int or value < 0:
        raise ReleasePolicyError(f"{context} must be a non-negative integer")
    return value


def _positive_id(value: object, context: str) -> int:
    if type(value) is not int or value <= 0 or value > 0xFFFFFFFF:
        raise ReleasePolicyError(f"{context} must be a positive uint32")
    return value


def _id_key(value: object, context: str) -> int:
    if not isinstance(value, str) or not value.isdecimal():
        raise ReleasePolicyError(f"{context} must be a decimal ID key")
    return _positive_id(int(value), context)


def _object(value: object, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReleasePolicyError(f"{context} must be an object")
    return value


def load_release_policy(path: str | Path | None = None) -> ReleasePolicy:
    """Load one optional strict JSON release policy snapshot.

    A missing configured file means no protections and no usage tie-breakers.
    A present malformed file fails closed instead of silently changing ranking.
    """

    policy_path = Path(path) if path is not None else config.RELEASE_POLICY_FILE
    if not policy_path.is_file():
        return ReleasePolicy.empty()
    try:
        raw = json.loads(policy_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReleasePolicyError(f"could not read release policy {policy_path}: {exc}") from exc
    document = _object(raw, "release policy")
    allowed = {
        "schema_version",
        "source",
        "season",
        "as_of",
        "protected_players",
        "usage",
    }
    unknown = set(document) - allowed
    if unknown:
        raise ReleasePolicyError(f"release policy has unknown fields: {sorted(unknown)}")
    if document.get("schema_version") != 1:
        raise ReleasePolicyError("release policy schema_version must be 1")

    protected_raw = _object(document.get("protected_players"), "protected_players")
    protected: dict[int, frozenset[int]] = {}
    for raw_team_id, raw_players in protected_raw.items():
        team_id = _id_key(raw_team_id, "protected_players team ID")
        if not isinstance(raw_players, list):
            raise ReleasePolicyError(
                f"protected_players[{raw_team_id}] must be a list"
            )
        player_ids = tuple(
            _positive_id(player_id, f"protected_players[{raw_team_id}]")
            for player_id in raw_players
        )
        if len(set(player_ids)) != len(player_ids):
            raise ReleasePolicyError(
                f"protected_players[{raw_team_id}] contains duplicates"
            )
        protected[team_id] = frozenset(player_ids)

    usage_raw = _object(document.get("usage"), "usage")
    usage: dict[int, PlayerUsage] = {}
    usage_fields = {"minutes", "starts", "appearances", "news_mentions"}
    for raw_player_id, raw_values in usage_raw.items():
        player_id = _id_key(raw_player_id, "usage player ID")
        values = _object(raw_values, f"usage[{raw_player_id}]")
        unknown_usage = set(values) - usage_fields
        if unknown_usage:
            raise ReleasePolicyError(
                f"usage[{raw_player_id}] has unknown fields: {sorted(unknown_usage)}"
            )
        missing_usage = {"minutes", "starts", "appearances"} - set(values)
        if missing_usage:
            raise ReleasePolicyError(
                f"usage[{raw_player_id}] is missing fields: {sorted(missing_usage)}"
            )
        usage[player_id] = PlayerUsage(
            minutes=_nonnegative_int(values["minutes"], f"usage[{raw_player_id}].minutes"),
            starts=_nonnegative_int(values["starts"], f"usage[{raw_player_id}].starts"),
            appearances=_nonnegative_int(
                values["appearances"], f"usage[{raw_player_id}].appearances"
            ),
            news_mentions=_nonnegative_int(
                values.get("news_mentions", 0),
                f"usage[{raw_player_id}].news_mentions",
            ),
        )

    source = _text(document.get("source", ""), "release policy source")
    season = _text(document.get("season", ""), "release policy season")
    as_of = _text(document.get("as_of", ""), "release policy as_of")
    return ReleasePolicy(protected, usage, source=source, season=season, as_of=as_of)

def import_usage_csv(
    csv_path: str | Path,
    output_path: str | Path | None = None,
    *,
    source: str = "",
    season: str = "",
    as_of: str = "",
) -> ReleasePolicy:
    """Merge a CSV usage snapshot into the protected-player policy atomically."""
    input_path = Path(csv_path)
    target_path = (
        Path(output_path)
        if output_path is not None
        else config.RELEASE_POLICY_FILE
    )
    current = load_release_policy(target_path)
    required = {"player_id", "minutes", "starts", "appearances"}
    usage = dict(current.usage)
    try:
        with input_path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            headers = set(reader.fieldnames or ())
            missing = required - headers
            if missing:
                raise ReleasePolicyError(
                    f"usage CSV is missing columns: {sorted(missing)}"
                )
            for row_number, row in enumerate(reader, 2):
                try:
                    player_id = _positive_id(
                        int(row["player_id"]), f"usage CSV row {row_number} player_id"
                    )
                    values = {
                        field: _nonnegative_int(
                            int(row[field]),
                            f"usage CSV row {row_number} {field}",
                        )
                        for field in required - {"player_id"}
                    }
                    news = row.get("news_mentions", "0") or "0"
                    values["news_mentions"] = _nonnegative_int(
                        int(news), f"usage CSV row {row_number} news_mentions"
                    )
                except (TypeError, ValueError) as exc:
                    raise ReleasePolicyError(
                        f"usage CSV row {row_number} contains invalid integers"
                    ) from exc
                usage[player_id] = PlayerUsage(**values)
    except OSError as exc:
        raise ReleasePolicyError(f"could not read usage CSV {input_path}: {exc}") from exc

    policy = ReleasePolicy(
        current.protected_players,
        usage,
        source=source or current.source,
        season=season or current.season,
        as_of=as_of or current.as_of,
    )
    target_path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=target_path.parent,
            prefix=f".{target_path.name}.",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(policy.to_dict(), handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target_path)
        temporary = None
    except OSError as exc:
        raise ReleasePolicyError(f"could not write release policy {target_path}: {exc}") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return policy


__all__ = (
    "PlayerUsage",
    "ReleasePolicy",
    "ReleasePolicyError",
    "import_usage_csv",
    "load_release_policy",
)
