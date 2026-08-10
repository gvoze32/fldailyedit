"""Read-only index over PES/Football Life ``Player.bin`` records."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Final, Iterable, Mapping

from editor.wesys import decompress_wesys


POSITION_NAMES: Final[tuple[str, ...]] = (
    "GK",
    "CB",
    "LB",
    "RB",
    "DMF",
    "CMF",
    "LMF",
    "RMF",
    "AMF",
    "LWF",
    "RWF",
    "SS",
    "CF",
)
RECORD_SIZE: Final = 312
YOUTH_TEAM_OFFSET: Final = 0x00
OWNER_TEAM_OFFSET: Final = 0x04
PLAYER_ID_OFFSET: Final = 0x08
CONTRACT_UNTIL_OFFSET: Final = 0x0C
LOAN_UNTIL_OFFSET: Final = 0x10
MARKET_VALUE_OFFSET: Final = 0x14
CAPS_OFFSET: Final = 0x17
AGE_OFFSET: Final = 0x33
POSITION_OFFSET: Final = 0x36
NAME_OFFSET: Final = 0x44
PRINT_NAME_OFFSET: Final = 0x81
NAME_SIZE: Final = 61


@dataclass(frozen=True, slots=True)
class PlayerBinRecord:
    player_id: int
    name: str
    age: int
    registered_position: str
    market_value_eur: int
    print_name: str = ""
    youth_team_id: int = 0
    owner_team_key: int = 0
    contract_until: int = 0
    loan_until: int = 0
    caps: int = 0

    @property
    def is_on_loan(self) -> bool:
        return self.loan_until > 0 and self.owner_team_key > 0

class PlayerBinDatabase:
    """Immutable-by-convention lookup index keyed by PES player ID."""

    __slots__ = ("_index",)

    def __init__(self, index: Mapping[int, PlayerBinRecord] | Iterable[PlayerBinRecord]):
        if isinstance(index, Mapping):
            source = index
        else:
            source = {record.player_id: record for record in index}
        self._index = dict(source)

    @classmethod
    def load(cls, path: Path) -> "PlayerBinDatabase":
        """Load raw or WESYS/zlib-compressed ``Player.bin`` bytes."""
        return cls.from_bytes(Path(path).read_bytes())

    @classmethod
    def from_bytes(cls, raw: bytes | bytearray | memoryview) -> "PlayerBinDatabase":
        data = decompress_wesys(raw, label="Player.bin")
        if len(data) % RECORD_SIZE:
            raise ValueError(
                f"Player.bin data must be divisible by {RECORD_SIZE}; got {len(data)} bytes"
            )

        index: dict[int, PlayerBinRecord] = {}
        for offset in range(0, len(data), RECORD_SIZE):
            player_id = struct.unpack_from("<I", data, offset + PLAYER_ID_OFFSET)[0]
            if player_id == 0:
                continue
            name = _read_text(data, offset + NAME_OFFSET, NAME_SIZE)
            if not name:
                continue
            position_index = (data[offset + POSITION_OFFSET] >> 2) & 0x0F
            position = (
                POSITION_NAMES[position_index]
                if position_index < len(POSITION_NAMES)
                else f"UNKNOWN({position_index})"
            )
            index[player_id] = PlayerBinRecord(
                player_id=player_id,
                name=name,
                age=(data[offset + AGE_OFFSET] & 0x3F) + 15,
                registered_position=position,
                market_value_eur=int.from_bytes(
                    data[
                        offset + MARKET_VALUE_OFFSET : offset + MARKET_VALUE_OFFSET + 3
                    ],
                    "little",
                )
                * 100,
                print_name=_read_text(
                    data, offset + PRINT_NAME_OFFSET, NAME_SIZE
                ),
                youth_team_id=struct.unpack_from(
                    "<I", data, offset + YOUTH_TEAM_OFFSET
                )[0],
                owner_team_key=struct.unpack_from(
                    "<I", data, offset + OWNER_TEAM_OFFSET
                )[0],
                contract_until=struct.unpack_from(
                    "<I", data, offset + CONTRACT_UNTIL_OFFSET
                )[0]
                & 0x7FFFFFF,
                loan_until=struct.unpack_from(
                    "<I", data, offset + LOAN_UNTIL_OFFSET
                )[0]
                & 0x7FFFFFF,
                caps=data[offset + CAPS_OFFSET],
            )
        return cls(index)

    def get(self, player_id: int) -> PlayerBinRecord | None:
        return self._index.get(player_id)

    def items(self):
        return MappingProxyType(self._index).items()

    def values(self):
        return MappingProxyType(self._index).values()

    def __len__(self) -> int:
        return len(self._index)

    def __contains__(self, player_id: object) -> bool:
        return player_id in self._index


def _read_text(data: bytes, offset: int, size: int) -> str:
    return data[offset : offset + size].split(b"\0", 1)[0].decode(
        "utf-8", errors="replace"
    ).strip()


__all__ = (
    "CAPS_OFFSET",
    "CONTRACT_UNTIL_OFFSET",
    "LOAN_UNTIL_OFFSET",
    "MARKET_VALUE_OFFSET",
    "NAME_OFFSET",
    "POSITION_NAMES",
    "POSITION_OFFSET",
    "PRINT_NAME_OFFSET",
    "RECORD_SIZE",
    "OWNER_TEAM_OFFSET",
    "PLAYER_ID_OFFSET",
    "PlayerBinDatabase",
    "PlayerBinRecord",
    "YOUTH_TEAM_OFFSET",
)


