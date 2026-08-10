"""Read-only index over Football Life ``Team.bin`` records."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Final, Iterable, Mapping

from editor.wesys import decompress_wesys


RECORD_SIZE: Final = 1532
TEAM_KEY_OFFSET: Final = 0x08
NAME_OFFSET: Final = 0x170
NAME_SIZE: Final = 70
ABBREVIATION_OFFSET: Final = 0x372
ABBREVIATION_SIZE: Final = 10


def _read_text(data: bytes, offset: int, size: int) -> str:
    return data[offset : offset + size].split(b"\0", 1)[0].decode(
        "utf-8", errors="replace"
    ).strip()


@dataclass(frozen=True, slots=True)
class TeamBinRecord:
    """Stable team-key metadata used by Player.bin owner references."""

    team_key: int
    name: str
    abbreviation: str = ""


class TeamBinDatabase:
    """Immutable-by-convention lookup index keyed by Team.bin team key."""

    __slots__ = ("_index",)

    def __init__(
        self,
        index: Mapping[int, TeamBinRecord] | Iterable[TeamBinRecord],
    ):
        if isinstance(index, Mapping):
            source = index
        else:
            source = {record.team_key: record for record in index}
        self._index = dict(source)

    @classmethod
    def load(cls, path: Path) -> "TeamBinDatabase":
        """Load raw or WESYS/zlib-compressed ``Team.bin`` bytes."""
        return cls.from_bytes(Path(path).read_bytes())

    @classmethod
    def from_bytes(cls, raw: bytes | bytearray | memoryview) -> "TeamBinDatabase":
        data = decompress_wesys(raw, label="Team.bin")
        if len(data) % RECORD_SIZE:
            raise ValueError(
                f"Team.bin data must be divisible by {RECORD_SIZE}; got {len(data)}"
            )

        index: dict[int, TeamBinRecord] = {}
        for offset in range(0, len(data), RECORD_SIZE):
            team_key = int.from_bytes(
                data[offset + TEAM_KEY_OFFSET : offset + TEAM_KEY_OFFSET + 4],
                "little",
            )
            name = _read_text(data, offset + NAME_OFFSET, NAME_SIZE)
            if team_key == 0 or not name:
                continue
            index[team_key] = TeamBinRecord(
                team_key=team_key,
                name=name,
                abbreviation=_read_text(
                    data,
                    offset + ABBREVIATION_OFFSET,
                    ABBREVIATION_SIZE,
                ),
            )
        return cls(index)

    def get(self, team_key: int) -> TeamBinRecord | None:
        return self._index.get(team_key)

    def items(self):
        return MappingProxyType(self._index).items()

    def values(self):
        return MappingProxyType(self._index).values()

    def __len__(self) -> int:
        return len(self._index)

    def __contains__(self, team_key: object) -> bool:
        return team_key in self._index


__all__ = (
    "ABBREVIATION_OFFSET",
    "NAME_OFFSET",
    "RECORD_SIZE",
    "TEAM_KEY_OFFSET",
    "TeamBinDatabase",
    "TeamBinRecord",
)
