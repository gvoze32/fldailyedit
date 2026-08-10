"""Decode the small WESYS/zlib wrapper used by game database files."""

from __future__ import annotations

import struct
import zlib
from typing import Final


WESYS_HEADER_SIZE: Final = 16
WESYS_MAGIC: Final = b"WESYS"


def decompress_wesys(
    raw: bytes | bytearray | memoryview,
    *,
    label: str,
) -> bytes:
    """Return the payload from raw data or a WESYS/zlib container."""
    if not isinstance(raw, (bytes, bytearray, memoryview)):
        raise TypeError(f"{label} data must be bytes-like")

    payload = raw if isinstance(raw, bytes) else bytes(raw)
    if len(payload) < 8 or payload[3:8] != WESYS_MAGIC:
        return payload
    if len(payload) < WESYS_HEADER_SIZE:
        raise ValueError(f"{label} WESYS header is truncated")

    compressed_size = struct.unpack_from("<I", payload, 8)[0]
    expected_size = struct.unpack_from("<I", payload, 12)[0]
    compressed_end = WESYS_HEADER_SIZE + compressed_size
    if compressed_size <= 0 or compressed_end > len(payload):
        raise ValueError(f"{label} WESYS compressed payload is truncated")
    try:
        result = zlib.decompress(payload[WESYS_HEADER_SIZE:compressed_end])
    except zlib.error as exc:
        raise ValueError(f"{label} WESYS payload is not valid zlib data") from exc
    if expected_size and len(result) != expected_size:
        raise ValueError(
            f"{label} decompressed size is {len(result)}; expected {expected_size}"
        )
    return result


__all__ = ("WESYS_HEADER_SIZE", "WESYS_MAGIC", "decompress_wesys")
