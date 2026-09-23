from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Sequence
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from installer import (
    APP_INSTALLER_ASSET_NAME,
    APP_INSTALLER_URL,
    __version__,
)


_CHUNK_BYTES = 64 * 1024


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest(
    asset_path: Path,
    output_path: Path,
    *,
    version: str = __version__,
) -> None:
    asset_path = Path(asset_path)
    output_path = Path(output_path)
    if asset_path.name != APP_INSTALLER_ASSET_NAME:
        raise ValueError(f"installer asset must be named {APP_INSTALLER_ASSET_NAME}")
    if not asset_path.is_file():
        raise FileNotFoundError(asset_path)
    archive_size = asset_path.stat().st_size
    if archive_size <= 0:
        raise ValueError("installer asset must not be empty")
    document = {
        "schema_version": 1,
        "version": version,
        "asset_name": APP_INSTALLER_ASSET_NAME,
        "download_url": APP_INSTALLER_URL,
        "archive_size": archive_size,
        "archive_sha256": _sha256(asset_path),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build the installer self-update manifest"
    )
    parser.add_argument("--asset", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--version", default=__version__)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _argument_parser().parse_args(argv)
    build_manifest(
        arguments.asset,
        arguments.output,
        version=arguments.version,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
