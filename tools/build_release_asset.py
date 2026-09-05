from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import zipfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import build_opener

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from installer import CATALOG_URL, RELEASE_TAG, REPOSITORY
from installer.catalog import (
    CatalogError,
    Channel,
    TRUSTED_ASSET_NAMES,
    TrustedRedirectHandler,
    parse_catalog,
)


_SAVE_MEMBER_NAME = "EDIT00000000"
TRANSFER_LOG_MEMBER_NAME = "FLDailyEdit-transfer-log.md"
MAX_TRANSFER_LOG_BYTES = 4 * 1024 * 1024

_ZIP_MINIMUM = datetime(1980, 1, 1, tzinfo=timezone.utc)


def _utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise CatalogError("invalid_record", "generated_at must include a timezone")
    return value.astimezone(timezone.utc)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False).encode("utf-8")
        + b"\n"
    )

def _archive_member(name: str, generated_utc: datetime) -> zipfile.ZipInfo:
    zip_timestamp = max(generated_utc, _ZIP_MINIMUM)
    member = zipfile.ZipInfo(
        name,
        date_time=(
            zip_timestamp.year,
            zip_timestamp.month,
            zip_timestamp.day,
            zip_timestamp.hour,
            zip_timestamp.minute,
            zip_timestamp.second,
        ),
    )
    member.create_system = 3
    member.external_attr = 0o100644 << 16
    member.compress_type = zipfile.ZIP_DEFLATED
    return member



def _new_sibling_temp(destination: Path) -> Path:
    descriptor, raw_path = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    os.close(descriptor)
    return Path(raw_path)


@contextmanager
def _owned_sibling_temp(destination: Path) -> Iterator[Path]:
    path = _new_sibling_temp(destination)
    try:
        yield path
    finally:
        path.unlink(missing_ok=True)


def _fsync_file(path: Path) -> None:
    with path.open("r+b") as output:
        os.fsync(output.fileno())


def _write_fsynced(path: Path, payload: bytes) -> None:
    with path.open("wb") as output:
        output.write(payload)
        output.flush()
        os.fsync(output.fileno())


def _backup_file(path: Path) -> Path | None:
    if not path.exists():
        return None
    backup_path = _new_sibling_temp(path)
    try:
        with path.open("rb") as source, backup_path.open("wb") as backup:
            shutil.copyfileobj(source, backup)
            backup.flush()
            os.fsync(backup.fileno())
    except BaseException:
        backup_path.unlink(missing_ok=True)
        raise
    return backup_path


def _publish_pair(staged_paths: tuple[tuple[Path, Path], ...]) -> None:
    backups: list[Path | None] = []
    try:
        for _, final_path in staged_paths:
            backups.append(_backup_file(final_path))
        for staged_path, final_path in staged_paths:
            os.replace(staged_path, final_path)
    except BaseException:
        rollback_error: BaseException | None = None
        for (_, final_path), backup_path in reversed(
            tuple(zip(staged_paths, backups))
        ):
            try:
                if backup_path is None:
                    final_path.unlink(missing_ok=True)
                else:
                    os.replace(backup_path, final_path)
            except BaseException as error:
                if rollback_error is None:
                    rollback_error = error
        if rollback_error is not None:
            raise RuntimeError("release asset rollback failed") from rollback_error
        raise
    finally:
        for staged_path, _ in staged_paths:
            staged_path.unlink(missing_ok=True)
        for backup_path in backups:
            if backup_path is not None:
                backup_path.unlink(missing_ok=True)


def package_record(
    save_path: Path,
    output_dir: Path,
    *,
    target_id: str,
    target_name: str,
    channel: Channel,
    generated_at: datetime,
    transfer_report_path: Path | None = None,
) -> tuple[Path, Path]:
    if not isinstance(channel, Channel):
        raise CatalogError(
            "untrusted_asset", "target and channel are not allowlisted"
        )
    asset_name = TRUSTED_ASSET_NAMES.get((target_id, channel))
    if asset_name is None:
        raise CatalogError(
            "untrusted_asset", "target and channel are not allowlisted"
        )
    if not isinstance(target_name, str) or not target_name:
        raise CatalogError("invalid_record", "target_name must be a non-empty string")

    generated_utc = _utc_datetime(generated_at)
    transfer_report: bytes | None = None
    if transfer_report_path is not None:
        try:
            transfer_report = Path(transfer_report_path).read_bytes()
        except FileNotFoundError:
            transfer_report = (
                b"# FLDailyEdit Option File Transfer Log\n\n"
                b"> No verified transfer changes were applied for this release.\n"
            )
        except OSError as error:
            raise CatalogError(
                "invalid_record",
                f"could not read transfer report {transfer_report_path}",
            ) from error
        if len(transfer_report) > MAX_TRANSFER_LOG_BYTES:
            raise CatalogError(
                "invalid_record",
                "transfer report exceeds the maximum supported size",
            )
        try:
            transfer_report.decode("utf-8")
        except UnicodeDecodeError as error:
            raise CatalogError(
                "invalid_record",
                "transfer report must be valid UTF-8",
            ) from error

    save_bytes = save_path.read_bytes()
    if not save_bytes:
        raise CatalogError("invalid_record", "save must not be empty")
    output_dir.mkdir(parents=True, exist_ok=True)
    archive_path = output_dir / asset_name
    record_path = output_dir / "record.json"
    with (
        _owned_sibling_temp(archive_path) as staged_archive,
        _owned_sibling_temp(record_path) as staged_record,
    ):
        member = _archive_member(_SAVE_MEMBER_NAME, generated_utc)
        with zipfile.ZipFile(
            staged_archive,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
        ) as archive:
            archive.writestr(member, save_bytes)
            if transfer_report is not None:
                archive.writestr(
                    _archive_member(TRANSFER_LOG_MEMBER_NAME, generated_utc),
                    transfer_report,
                )
        _fsync_file(staged_archive)

        archive_bytes = staged_archive.read_bytes()
        record = {
            "target_id": target_id,
            "target_name": target_name,
            "channel": channel.value,
            "generated_at": (
                f"{generated_utc.year:04d}-{generated_utc.month:02d}-"
                f"{generated_utc.day:02d}T{generated_utc.hour:02d}:"
                f"{generated_utc.minute:02d}:{generated_utc.second:02d}Z"
            ),
            "asset_name": asset_name,
            "download_url": (
                f"https://github.com/{REPOSITORY}/releases/download/"
                f"{RELEASE_TAG}/{asset_name}"
            ),
            "archive_size": len(archive_bytes),
            "archive_sha256": _sha256(archive_bytes),
            "save_size": len(save_bytes),
            "save_sha256": _sha256(save_bytes),
        }
        _write_fsynced(staged_record, _json_bytes(record))
        _publish_pair(
            (
                (staged_archive, archive_path),
                (staged_record, record_path),
            )
        )
    return archive_path, record_path


def _validated_record_document(record_payload: bytes) -> dict[str, object]:
    wrapped_payload = (
        b'{"schema_version":1,"records":[' + record_payload + b"]}"
    )
    parsed = parse_catalog(wrapped_payload)
    if len(parsed.records) != 1:
        raise CatalogError("invalid_record", "record payload must contain one record")
    document = json.loads(record_payload)
    if not isinstance(document, dict):
        raise CatalogError("invalid_record", "record payload must be an object")
    return document


def merge_catalog(existing_payload: bytes | None, record_payload: bytes) -> bytes:
    replacement = _validated_record_document(record_payload)
    replacement_identity = (replacement["target_id"], replacement["channel"])

    if existing_payload is None:
        records: list[dict[str, object]] = []
    else:
        parse_catalog(existing_payload)
        existing_document = json.loads(existing_payload)
        records = existing_document["records"]

    merged = {
        (record["target_id"], record["channel"]): record for record in records
    }
    merged[replacement_identity] = replacement
    sorted_records = [
        merged[identity]
        for identity in sorted(merged, key=lambda identity: (identity[0], identity[1]))
    ]
    return _json_bytes({"schema_version": 1, "records": sorted_records})


def _parse_generated_at(value: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be an ISO-8601 datetime") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("must include a timezone")
    return parsed


def _fetch_existing_payload(url: str, *, timeout: float = 20.0) -> bytes | None:
    if url != CATALOG_URL:
        raise CatalogError("untrusted_catalog", "catalog URL is not trusted")
    opener = build_opener(TrustedRedirectHandler())
    try:
        with opener.open(url, timeout=timeout) as response:
            return response.read()
    except HTTPError as error:
        if error.code == 404:
            return None
        raise


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build deterministic installer release assets"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    package_parser = subparsers.add_parser("package")
    package_parser.add_argument("--save", type=Path, required=True)
    package_parser.add_argument("--output-dir", type=Path, required=True)
    package_parser.add_argument("--target-id", required=True)
    package_parser.add_argument("--target-name", required=True)
    package_parser.add_argument(
        "--channel", type=Channel, choices=list(Channel), required=True
    )
    package_parser.add_argument(
        "--generated-at", type=_parse_generated_at, required=True
    )
    package_parser.add_argument(
        "--transfer-report",
        type=Path,
        help="UTF-8 transfer report to include in the release archive",
    )

    merge_parser = subparsers.add_parser("merge")
    merge_parser.add_argument("--existing-url", required=True)
    merge_parser.add_argument("--record", type=Path, required=True)
    merge_parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _argument_parser().parse_args(argv)
    if arguments.command == "package":
        package_record(
            arguments.save,
            arguments.output_dir,
            target_id=arguments.target_id,
            target_name=arguments.target_name,
            channel=arguments.channel,
            generated_at=arguments.generated_at,
            transfer_report_path=arguments.transfer_report,
        )
        return 0

    existing_payload = _fetch_existing_payload(arguments.existing_url)
    catalog_payload = merge_catalog(
        existing_payload, arguments.record.read_bytes()
    )
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_bytes(catalog_payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
