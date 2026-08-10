"""Compare native PES metadata databases from two CPK variants."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from collections import Counter
from typing import Any, Callable, Iterable, Mapping

from editor.player_assignment import PlayerAssignmentDatabase, PlayerAssignmentRecord
from editor.playerbin import PlayerBinDatabase
from editor.teambin import TeamBinDatabase
_PREVIEW_LIMIT = 10


@dataclass(frozen=True, slots=True)
class MetadataDatabaseDiff:
    """Bounded diff for one native metadata database."""

    database: str
    left_entries: int
    right_entries: int
    identical_entries: int
    changed_entries: int
    only_left_entries: int
    only_right_entries: int
    changed_preview: tuple[str, ...]
    only_left_preview: tuple[str, ...]
    only_right_preview: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class MetadataVariantDiff:
    """Comparison of the supported native databases in two CPK variants."""

    left_source: str
    right_source: str
    databases: tuple[MetadataDatabaseDiff, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "left_source": self.left_source,
            "right_source": self.right_source,
            "databases": [database.to_dict() for database in self.databases],
        }


def _preview(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted(values)[:_PREVIEW_LIMIT])


def _compare_maps(
    database: str,
    left: Mapping[Any, Any],
    right: Mapping[Any, Any],
    key_formatter: Callable[[Any], str],
) -> MetadataDatabaseDiff:
    left_keys = set(left)
    right_keys = set(right)
    shared_keys = left_keys & right_keys
    changed = {
        key_formatter(key)
        for key in shared_keys
        if left[key] != right[key]
    }
    return MetadataDatabaseDiff(
        database=database,
        left_entries=len(left),
        right_entries=len(right),
        identical_entries=len(shared_keys) - len(changed),
        changed_entries=len(changed),
        only_left_entries=len(left_keys - right_keys),
        only_right_entries=len(right_keys - left_keys),
        changed_preview=_preview(changed),
        only_left_preview=_preview(key_formatter(key) for key in left_keys - right_keys),
        only_right_preview=_preview(key_formatter(key) for key in right_keys - left_keys),
    )
def _compare_multisets(
    database: str,
    left: Iterable[Any],
    right: Iterable[Any],
    key_formatter: Callable[[Any], str],
) -> MetadataDatabaseDiff:
    left_counts = Counter(left)
    right_counts = Counter(right)
    left_keys = set(left_counts)
    right_keys = set(right_counts)
    shared_keys = left_keys & right_keys
    changed_keys = {
        key_formatter(key)
        for key in shared_keys
        if left_counts[key] != right_counts[key]
    }
    only_left_keys = left_keys - right_keys
    only_right_keys = right_keys - left_keys
    return MetadataDatabaseDiff(
        database=database,
        left_entries=sum(left_counts.values()),
        right_entries=sum(right_counts.values()),
        identical_entries=sum(
            min(left_counts[key], right_counts[key]) for key in shared_keys
        ),
        changed_entries=len(changed_keys),
        only_left_entries=sum(
            max(left_counts[key] - right_counts[key], 0) for key in left_keys
        ),
        only_right_entries=sum(
            max(right_counts[key] - left_counts[key], 0) for key in right_keys
        ),
        changed_preview=_preview(changed_keys),
        only_left_preview=_preview(
            key_formatter(key) for key in only_left_keys
        ),
        only_right_preview=_preview(
            key_formatter(key) for key in only_right_keys
        ),
    )


def _assignment_key(record: PlayerAssignmentRecord) -> tuple[int, int]:
    """Compare assignment ownership, not opaque per-row source words."""
    return (record.player_id, record.team_key)






def compare_metadata_variants(
    left_source: str,
    left_playerbin: PlayerBinDatabase,
    left_teambin: TeamBinDatabase,
    left_assignment: PlayerAssignmentDatabase,
    right_source: str,
    right_playerbin: PlayerBinDatabase,
    right_teambin: TeamBinDatabase,
    right_assignment: PlayerAssignmentDatabase,
) -> MetadataVariantDiff:
    """Compare Player, Team, and PlayerAssignment indexes by stable native keys."""
    player_diff = _compare_maps(
        "Player.bin",
        dict(left_playerbin.items()),
        dict(right_playerbin.items()),
        lambda player_id: str(player_id),
    )
    team_diff = _compare_maps(
        "Team.bin",
        dict(left_teambin.items()),
        dict(right_teambin.items()),
        lambda team_key: str(team_key),
    )
    assignment_diff = _compare_multisets(
        "PlayerAssignment.bin",
        (_assignment_key(record) for record in left_assignment.records),
        (_assignment_key(record) for record in right_assignment.records),
        lambda key: ":".join(str(value) for value in key),
    )
    return MetadataVariantDiff(
        left_source=left_source,
        right_source=right_source,
        databases=(player_diff, team_diff, assignment_diff),
    )


def format_metadata_variant_diff(report: MetadataVariantDiff) -> str:
    """Format a bounded human-readable native metadata comparison."""
    lines = [
        "--- Native Metadata Variant Diff ---",
        f"Left: {report.left_source}",
        f"Right: {report.right_source}",
    ]
    for database in report.databases:
        lines.extend(
            [
                "",
                f"{database.database}:",
                f"  Entries: {database.left_entries:,} → {database.right_entries:,}",
                f"  Identical keys: {database.identical_entries:,}",
                f"  Changed keys: {database.changed_entries:,} "
                f"({', '.join(database.changed_preview) or '-'})",
                f"  Only left: {database.only_left_entries:,} "
                f"({', '.join(database.only_left_preview) or '-'})",
                f"  Only right: {database.only_right_entries:,} "
                f"({', '.join(database.only_right_preview) or '-'})",
            ]
        )
    return "\n".join(lines)


__all__ = (
    "MetadataDatabaseDiff",
    "MetadataVariantDiff",
    "compare_metadata_variants",
    "format_metadata_variant_diff",
)
