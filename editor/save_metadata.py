"""Read the decrypted PES edit-container header."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


FILE_HEADER_SIZE = 208
_DATA_SIZE_OFFSET = 64
_LOGO_SIZE_OFFSET = 68
_DESCRIPTION_SIZE_OFFSET = 72
_SERIAL_LENGTH_OFFSET = 76
_FILE_TYPE_OFFSET = 144
_GAME_VERSION_OFFSET = 176
_FIXED_STRING_SIZE = 32


@dataclass(frozen=True, slots=True)
class SaveHeader:
    """Stable metadata identifying the game family of an EDIT container."""

    file_type: str
    game_version: str
    data_size: int
    logo_size: int
    description_size: int
    serial_length: int

    @property
    def is_pes21(self) -> bool:
        """Return whether the container identifies the vanilla PES 2021 save format."""
        return "pes 2021" in self.game_version.casefold()


def _read_fixed_string(raw: bytes, offset: int) -> str:
    return raw[offset : offset + _FIXED_STRING_SIZE].split(b"\0", 1)[0].decode(
        "utf-8", errors="replace"
    ).strip()


def parse_save_header(raw: bytes | bytearray | memoryview) -> SaveHeader:
    """Parse a decrypted ``header.dat`` block."""
    data = bytes(raw)
    if len(data) < FILE_HEADER_SIZE:
        raise ValueError(
            f"save header must contain at least {FILE_HEADER_SIZE} bytes; got {len(data)}"
        )
    return SaveHeader(
        file_type=_read_fixed_string(data, _FILE_TYPE_OFFSET),
        game_version=_read_fixed_string(data, _GAME_VERSION_OFFSET),
        data_size=int.from_bytes(data[_DATA_SIZE_OFFSET : _DATA_SIZE_OFFSET + 4], "little"),
        logo_size=int.from_bytes(data[_LOGO_SIZE_OFFSET : _LOGO_SIZE_OFFSET + 4], "little"),
        description_size=int.from_bytes(
            data[_DESCRIPTION_SIZE_OFFSET : _DESCRIPTION_SIZE_OFFSET + 4], "little"
        ),
        serial_length=int.from_bytes(
            data[_SERIAL_LENGTH_OFFSET : _SERIAL_LENGTH_OFFSET + 4], "little"
        ),
    )


def read_save_header(path: Path | str) -> SaveHeader:
    """Read and parse a decrypted ``header.dat`` path."""
    return parse_save_header(Path(path).read_bytes())


__all__ = (
    "FILE_HEADER_SIZE",
    "SaveHeader",
    "parse_save_header",
    "read_save_header",
)
