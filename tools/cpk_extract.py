"""Extract files from PES/Football Life CPK archives (CRIWARE format)."""

from __future__ import annotations

import argparse
import struct
from pathlib import Path

from installer.paths import find_game_cpks


_CPK_DATA_BASE = 0x800
DATABASE_FILES = {
    "Player.bin": "common/etc/pesdb/Player.bin",
    "PlayerAssignment.bin": "common/etc/pesdb/PlayerAssignment.bin",
    "Team.bin": "common/etc/pesdb/Team.bin",
}


def _require_bounds(data: bytes, offset: int, size: int, label: str) -> None:
    if offset < 0 or size < 0 or offset + size > len(data):
        raise ValueError(
            f"{label} at 0x{offset:X} with size {size} exceeds {len(data)} bytes"
        )


def _read_utf(data: bytes, offset: int) -> tuple[list[dict[str, object]], list[tuple[str, int, int, object]]]:
    """Read one CRI ``@UTF`` table and return rows plus column metadata."""
    _require_bounds(data, offset, 24, "@UTF header")
    if data[offset : offset + 4] != b"@UTF":
        raise ValueError(f"Expected @UTF at 0x{offset:X}, got {data[offset:offset + 4]!r}")

    table_size = struct.unpack_from(">I", data, offset + 4)[0]
    table_start = offset + 8
    _require_bounds(data, table_start, table_size, "@UTF table")
    table = data[table_start : table_start + table_size]
    if len(table) < 24:
        raise ValueError("@UTF table header is truncated")

    rows_offset, strings_offset, data_offset = struct.unpack_from(">III", table, 0)
    number_columns, row_length = struct.unpack_from(">HH", table, 16)
    number_rows = struct.unpack_from(">I", table, 20)[0]

    for name, value in (
        ("rows", rows_offset),
        ("strings", strings_offset),
        ("data", data_offset),
    ):
        if value > len(table):
            raise ValueError(f"@UTF {name} offset {value} exceeds table size {len(table)}")

    def read_string(string_offset: int) -> str:
        start = strings_offset + string_offset
        if not 0 <= start < len(table):
            raise ValueError(f"@UTF string offset {string_offset} is out of bounds")
        end = table.find(b"\0", start)
        if end < 0:
            raise ValueError(f"@UTF string at offset {string_offset} is unterminated")
        return table[start:end].decode("utf-8", errors="replace")

    def read_value(value_offset: int, content_type: int) -> tuple[object, int]:
        formats: dict[int, tuple[str, int]] = {
            0: (">B", 1),
            1: (">b", 1),
            2: (">H", 2),
            3: (">h", 2),
            4: (">I", 4),
            5: (">i", 4),
            6: (">Q", 8),
            7: (">q", 8),
            8: (">f", 4),
            9: (">d", 8),
        }
        if content_type == 10:
            _require_bounds(table, value_offset, 4, "@UTF string value")
            string_offset = struct.unpack_from(">I", table, value_offset)[0]
            return read_string(string_offset), value_offset + 4
        if content_type == 11:
            _require_bounds(table, value_offset, 8, "@UTF data value")
            data_offset_value, data_size = struct.unpack_from(">II", table, value_offset)
            start = data_offset + data_offset_value
            _require_bounds(table, start, data_size, "@UTF data payload")
            return bytes(table[start : start + data_size]), value_offset + 8
        try:
            value_format, value_size = formats[content_type]
        except KeyError as exc:
            raise ValueError(f"Unsupported @UTF content type {content_type}") from exc
        _require_bounds(table, value_offset, value_size, "@UTF value")
        return struct.unpack_from(value_format, table, value_offset)[0], value_offset + value_size

    columns: list[tuple[str, int, int, object]] = []
    column_offset = 24
    for _ in range(number_columns):
        _require_bounds(table, column_offset, 5, "@UTF column descriptor")
        flags = table[column_offset]
        column_offset += 1
        name_offset = struct.unpack_from(">I", table, column_offset)[0]
        column_offset += 4
        storage = flags & 0xF0
        content_type = flags & 0x0F
        constant: object = None
        if storage == 0x30:
            constant, column_offset = read_value(column_offset, content_type)
        elif storage not in (0x00, 0x10, 0x50):
            raise ValueError(f"Unsupported @UTF storage type 0x{storage:02X}")
        columns.append((read_string(name_offset), content_type, storage, constant))

    rows: list[dict[str, object]] = []
    for row_number in range(number_rows):
        row_offset = rows_offset + row_number * row_length
        _require_bounds(table, row_offset, row_length, "@UTF row")
        row: dict[str, object] = {}
        cursor = row_offset
        for name, content_type, storage, constant in columns:
            if storage == 0x00:
                row[name] = None
            elif storage == 0x10:
                row[name] = 0
            elif storage == 0x30:
                row[name] = constant
            else:
                value, cursor = read_value(cursor, content_type)
                row[name] = value
        rows.append(row)

    return rows, columns


def _read_cpk_header(cpk_data: bytes) -> dict[str, object]:
    """Read the root CPK header, including archives with encrypted chunk IDs."""
    rows, _columns = _read_utf(cpk_data, 16)
    if len(rows) != 1:
        raise ValueError(f"CPK header must contain one row; got {len(rows)}")
    return rows[0]


def _parse_mode5_toc_rows(
    cpk_data: bytes,
    toc_offset: int,
) -> list[dict[str, object]]:
    """Read the compact TOC used by PES CPK mode 5 archives.

    PES 2021 patch archives keep the row data and string table in normal UTF
    form, but protect the TOC schema descriptor. The row shape is stable:
    directory, filename, packed size, extract size, 64-bit offset, ID,
    an omitted/zero user string column, and CRC.
    """
    utf_offset = toc_offset + 16
    _require_bounds(cpk_data, utf_offset, 24, "CPK mode 5 TOC")
    if cpk_data[utf_offset : utf_offset + 4] != b"@UTF":
        raise ValueError("CPK mode 5 TOC does not contain an @UTF table")

    table_size = struct.unpack_from(">I", cpk_data, utf_offset + 4)[0]
    table_start = utf_offset + 8
    _require_bounds(cpk_data, table_start, table_size, "CPK mode 5 TOC table")
    table = cpk_data[table_start : table_start + table_size]
    if len(table) < 24:
        raise ValueError("CPK mode 5 TOC table header is truncated")

    rows_offset, strings_offset, data_offset = struct.unpack_from(">III", table, 0)
    number_columns, row_length = struct.unpack_from(">HH", table, 16)
    number_rows = struct.unpack_from(">I", table, 20)[0]
    if (number_columns, row_length) != (8, 32):
        raise ValueError(
            "Unsupported protected CPK TOC shape: "
            f"{number_columns} columns, {row_length}-byte rows"
        )
    if strings_offset > len(table) or data_offset > len(table):
        raise ValueError("CPK mode 5 TOC string/data section is out of bounds")

    def read_string(string_offset: int) -> str:
        start = strings_offset + string_offset
        if not 0 <= start < len(table):
            raise ValueError(
                f"CPK mode 5 TOC string offset {string_offset} is out of bounds"
            )
        end = table.find(b"\0", start)
        if end < 0:
            raise ValueError(
                f"CPK mode 5 TOC string at offset {string_offset} is unterminated"
            )
        return table[start:end].decode("utf-8", errors="replace")

    rows: list[dict[str, object]] = []
    for row_number in range(number_rows):
        row_offset = rows_offset + row_number * row_length
        _require_bounds(table, row_offset, row_length, "CPK mode 5 TOC row")
        directory_offset, filename_offset, file_size, extract_size = struct.unpack_from(
            ">IIII", table, row_offset
        )
        file_offset = struct.unpack_from(">Q", table, row_offset + 16)[0]
        file_id = struct.unpack_from(">I", table, row_offset + 24)[0]
        crc = struct.unpack_from(">I", table, row_offset + 28)[0]
        rows.append(
            {
                "DirName": read_string(directory_offset),
                "FileName": read_string(filename_offset),
                "FileSize": file_size,
                "ExtractSize": extract_size,
                "FileOffset": file_offset,
                "ID": file_id,
                "UserString": None,
                "CRC": crc,
            }
        )
    return rows


def _toc_location(cpk_data: bytes, header: dict[str, object]) -> int:
    try:
        toc_offset = int(header["TocOffset"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("CPK header has no valid TocOffset") from exc
    if toc_offset == 0xFFFFFFFFFFFFFFFF:
        toc_offset = cpk_data.find(b"TOC ")
    if toc_offset < 0:
        raise ValueError("TOC marker not found in CPK")
    _require_bounds(cpk_data, toc_offset, 16, "CPK TOC chunk")
    return toc_offset


def _parse_toc_rows(cpk_data: bytes) -> tuple[int, list[dict[str, object]]]:
    header = _read_cpk_header(cpk_data)
    toc_offset = _toc_location(cpk_data, header)
    try:
        rows, _columns = _read_utf(cpk_data, toc_offset + 16)
    except ValueError as standard_error:
        try:
            rows = _parse_mode5_toc_rows(cpk_data, toc_offset)
        except ValueError as mode5_error:
            raise ValueError(
                f"Could not parse CPK TOC at 0x{toc_offset:X}: {standard_error}"
            ) from mode5_error
    for row in rows:
        row["_ContentOffset"] = int(header.get("ContentOffset", _CPK_DATA_BASE))
    return toc_offset, rows



def _full_name(row: dict[str, object]) -> str:
    directory = str(row.get("DirName") or "").strip("/")
    filename = str(row.get("FileName") or "").strip("/")
    if directory and filename:
        return f"{directory}/{filename}"
    return filename or directory


def list_files(cpk_path: Path) -> list[str]:
    """Return full archive paths contained in ``cpk_path``."""
    data = Path(cpk_path).read_bytes()
    _toc_offset, rows = _parse_toc_rows(data)
    return [_full_name(row) for row in rows]


def _select_row(rows: list[dict[str, object]], filename: str) -> dict[str, object]:
    requested = filename.replace("\\", "/").strip("/")
    exact = [row for row in rows if _full_name(row) == requested]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise ValueError(f"CPK contains duplicate path {filename!r}")

    basename_matches = [
        row for row in rows
        if str(row.get("FileName") or "").strip("/") == requested
        or _full_name(row).endswith("/" + requested)
    ]
    if not basename_matches:
        raise FileNotFoundError(f"File {filename!r} not found in CPK")
    if len(basename_matches) > 1:
        paths = ", ".join(_full_name(row) for row in basename_matches)
        raise ValueError(f"CPK filename {filename!r} is ambiguous: {paths}")
    return basename_matches[0]


def _payload_start(
    data: bytes,
    toc_offset: int,
    file_offset: int,
    file_size: int,
    content_offset: int,
) -> int:
    candidates = (
        file_offset + content_offset,
        file_offset + _CPK_DATA_BASE,
        file_offset + toc_offset,
    )
    for start in dict.fromkeys(candidates):
        if 0 <= start <= len(data) and start + file_size <= len(data):
            return start
    raise ValueError(
        f"CPK payload offset 0x{file_offset:X} and size {file_size} exceed archive bounds"
    )


def read_file(cpk_path: Path, filename: str) -> bytes:
    """Read one named file from a CPK archive into memory."""
    data = Path(cpk_path).read_bytes()
    toc_offset, rows = _parse_toc_rows(data)
    row = _select_row(rows, filename)
    try:
        file_offset = int(row["FileOffset"])
        file_size = int(row.get("FileSize", row.get("ExtractSize", 0)))
        content_offset = int(row.get("_ContentOffset", _CPK_DATA_BASE))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"CPK row for {filename!r} has invalid offset/size") from exc
    if file_offset < 0 or file_size < 0:
        raise ValueError(f"CPK row for {filename!r} has negative offset/size")
    start = _payload_start(
        data,
        toc_offset,
        file_offset,
        file_size,
        content_offset,
    )
    return data[start : start + file_size]

def extract_file(cpk_path: Path, filename: str, output_path: Path) -> int:
    """Extract a named file from a CPK archive and return bytes written."""
    payload = read_file(cpk_path, filename)
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(payload)
    return len(payload)

def _database_cpk_candidates(game_root: Path) -> tuple[Path, ...]:
    """Return database archives in the game's overlay precedence order."""
    return find_game_cpks(game_root)


def extract_game_databases(
    game_root: Path,
    output_directory: Path,
) -> dict[str, Path]:
    """Extract supported metadata databases from the game CPK archives."""
    cpk_paths = _database_cpk_candidates(game_root)
    if not cpk_paths:
        raise FileNotFoundError(
            "no database CPK archives found below "
            f"{Path(game_root)}"
        )
    output_directory = Path(output_directory)
    extracted: dict[str, Path] = {}
    for short_name, archive_name in DATABASE_FILES.items():
        output_path = output_directory / short_name
        for cpk_path in cpk_paths:
            try:
                extract_file(cpk_path, archive_name, output_path)
            except FileNotFoundError:
                continue
            extracted[short_name] = output_path
            break
        else:
            searched = ", ".join(str(path) for path in cpk_paths)
            raise FileNotFoundError(
                f"{short_name} not found in database archives: {searched}"
            )
    return extracted

def main() -> None:
    parser = argparse.ArgumentParser(description="Extract files from PES/FL CPK archives")
    parser.add_argument("--cpk", type=Path, help="Path to CPK archive")
    parser.add_argument("--game-root", type=Path, help="Game root containing download/")
    parser.add_argument("--list", action="store_true", help="List files in CPK archive")
    parser.add_argument("--extract", type=str, help="Filename to extract (e.g. Player.bin)")
    parser.add_argument("--output", type=Path, help="Output destination path")
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Destination directory for --game-root database extraction",
    )
    args = parser.parse_args()

    if args.game_root is not None:
        if args.cpk is not None:
            parser.error("--game-root and --cpk are mutually exclusive")
        if args.list or args.extract:
            parser.error("--game-root cannot be combined with --list or --extract")
        if args.output_dir is None:
            parser.error("--game-root requires --output-dir")
        for short_name, output_path in extract_game_databases(
            args.game_root, args.output_dir
        ).items():
            print(f"Extracted {short_name} to {output_path}")
        return
    if args.cpk is None:
        parser.error("--cpk is required unless --game-root is supplied")
    if args.list:
        files = list_files(args.cpk)
        print(f"CPK contains {len(files)} files:")
        for name in files:
            print(f"  {name}")
        return
    if args.extract:
        output_path = args.output or Path(args.extract).name
        written = extract_file(args.cpk, args.extract, output_path)
        print(f"Extracted {args.extract} ({written:,} bytes) to {output_path}")
        return
    parser.print_help()


if __name__ == "__main__":
    main()
