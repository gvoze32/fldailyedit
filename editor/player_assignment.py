"""Read-only PlayerAssignment.bin roster ownership index."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Final

from editor.wesys import decompress_wesys


RECORD_SIZE: Final = 16


@dataclass(frozen=True, slots=True)
class PlayerAssignmentRecord:
    """One assignment row; the first and last words remain opaque."""

    record_key: int
    player_id: int
    team_key: int
    auxiliary: int


class PlayerAssignmentDatabase:
    """Index PlayerAssignment rows by player ID without merging variants."""

    __slots__ = ("_by_player", "_records")

    def __init__(self, records: tuple[PlayerAssignmentRecord, ...]):
        by_player: dict[int, list[PlayerAssignmentRecord]] = defaultdict(list)
        for record in records:
            if record.player_id:
                by_player[record.player_id].append(record)
        self._records = records
        self._by_player = {
            player_id: tuple(player_records)
            for player_id, player_records in by_player.items()
        }

    @classmethod
    def load(cls, path: Path) -> "PlayerAssignmentDatabase":
        """Load raw or WESYS/zlib-compressed PlayerAssignment.bin bytes."""
        return cls.from_bytes(Path(path).read_bytes())

    @classmethod
    def from_bytes(
        cls, raw: bytes | bytearray | memoryview
    ) -> "PlayerAssignmentDatabase":
        data = decompress_wesys(raw, label="PlayerAssignment.bin")
        if len(data) % RECORD_SIZE:
            raise ValueError(
                "PlayerAssignment.bin data must be divisible by "
                f"{RECORD_SIZE}; got {len(data)}"
            )

        records = tuple(
            PlayerAssignmentRecord(
                record_key=int.from_bytes(data[offset : offset + 4], "little"),
                player_id=int.from_bytes(data[offset + 4 : offset + 8], "little"),
                team_key=int.from_bytes(data[offset + 8 : offset + 12], "little"),
                auxiliary=int.from_bytes(data[offset + 12 : offset + 16], "little"),
            )
            for offset in range(0, len(data), RECORD_SIZE)
        )
        return cls(records)

    @property
    def records(self) -> tuple[PlayerAssignmentRecord, ...]:
        return self._records

    @property
    def player_count(self) -> int:
        return len(self._by_player)

    def get(self, player_id: int) -> tuple[PlayerAssignmentRecord, ...]:
        return self._by_player.get(player_id, ())

    def team_keys_for(self, player_id: int) -> tuple[int, ...]:
        """Return distinct team keys in source-file order."""
        seen: set[int] = set()
        result: list[int] = []
        for record in self.get(player_id):
            if record.team_key not in seen:
                seen.add(record.team_key)
                result.append(record.team_key)
        return tuple(result)

    def items(self):
        return MappingProxyType(self._by_player).items()

    def __len__(self) -> int:
        return len(self._records)


__all__ = ("RECORD_SIZE", "PlayerAssignmentDatabase", "PlayerAssignmentRecord")
