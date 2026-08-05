from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import socket
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import (
    HTTPRedirectHandler,
    OpenerDirector,
    Request,
    build_opener,
)

from installer import CATALOG_URL, RELEASE_TAG, REPOSITORY


class Channel(str, Enum):
    FAST = "fast"
    DEEP = "deep"


TRUSTED_ASSET_NAMES = {
    ("fl26-u2.2-national-squads", Channel.FAST): "fldailyedit-fl2026-fast.zip",
    ("fl26-u2.2-national-squads", Channel.DEEP): "fldailyedit-fl2026-deep.zip",
}

MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
_DOWNLOAD_CHUNK_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class ReleaseRecord:
    target_id: str
    target_name: str
    channel: Channel
    generated_at: datetime
    asset_name: str
    download_url: str
    archive_size: int
    archive_sha256: str
    save_size: int
    save_sha256: str


@dataclass(frozen=True, slots=True)
class Catalog:
    schema_version: int
    records: tuple[ReleaseRecord, ...]


class CatalogError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class DownloadError(OSError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code

def _is_trusted_redirect_host(host: str | None) -> bool:
    return host in {("github.com"), ("githubusercontent.com")} or (
        host is not None and host.endswith(".githubusercontent.com")
    )


def _is_trusted_https_url(url: str, *, initial: bool = False) -> bool:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        return False
    host = parsed.hostname
    trusted_host = host == "github.com" if initial else _is_trusted_redirect_host(host)
    return (
        parsed.scheme == "https"
        and trusted_host
        and port is None
        and parsed.username is None
        and parsed.password is None
    )


class TrustedRedirectHandler(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> Request | None:
        if not _is_trusted_https_url(req.full_url) or not _is_trusted_https_url(
            newurl
        ):
            raise HTTPError(
                newurl, code, "redirect target is not trusted", headers, fp
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_TOP_LEVEL_KEYS = frozenset({"schema_version", "records"})
_RECORD_KEYS = frozenset(
    {
        "target_id",
        "target_name",
        "channel",
        "generated_at",
        "asset_name",
        "download_url",
        "archive_size",
        "archive_sha256",
        "save_size",
        "save_sha256",
    }
)
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_UTC_TIMESTAMP_PATTERN = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z\Z"
)


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CatalogError("invalid_catalog", f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _require_exact_keys(
    value: object, expected: frozenset[str], description: str
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise CatalogError(
            "invalid_catalog", f"{description} must contain exactly {sorted(expected)}"
        )
    return value


def _require_string(record: dict[str, Any], field: str) -> str:
    value = record[field]
    if not isinstance(value, str) or not value:
        raise CatalogError("invalid_record", f"{field} must be a non-empty string")
    return value


def _require_positive_integer(record: dict[str, Any], field: str) -> int:
    value = record[field]
    if type(value) is not int or value <= 0:
        raise CatalogError("invalid_record", f"{field} must be a positive integer")
    return value


def _require_sha256(record: dict[str, Any], field: str) -> str:
    value = record[field]
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise CatalogError(
            "invalid_record", f"{field} must be a lowercase SHA-256 digest"
        )
    return value


def _parse_generated_at(value: str) -> datetime:
    if _UTC_TIMESTAMP_PATTERN.fullmatch(value) is None:
        raise CatalogError("invalid_record", "generated_at must be a UTC timestamp")
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise CatalogError("invalid_record", "generated_at is invalid") from error


def _validate_release_url(
    target_id: str, channel: Channel, asset_name: str, download_url: str
) -> None:
    trusted_asset = TRUSTED_ASSET_NAMES.get((target_id, channel))
    if trusted_asset != asset_name:
        raise CatalogError(
            "untrusted_asset", "target, channel, and asset are not allowlisted"
        )

    parsed = urlsplit(download_url)
    expected_path = (
        f"/{REPOSITORY}/releases/download/{RELEASE_TAG}/{asset_name}"
    )
    if (
        parsed.scheme != "https"
        or parsed.netloc != "github.com"
        or parsed.path != expected_path
        or parsed.query
        or parsed.fragment
    ):
        raise CatalogError("untrusted_asset", "release URL is not trusted")


def _parse_record(value: object) -> ReleaseRecord:
    record = _require_exact_keys(value, _RECORD_KEYS, "record")
    target_id = _require_string(record, "target_id")
    target_name = _require_string(record, "target_name")
    channel_value = _require_string(record, "channel")
    try:
        channel = Channel(channel_value)
    except ValueError as error:
        raise CatalogError("invalid_record", "channel is not supported") from error
    generated_at = _parse_generated_at(_require_string(record, "generated_at"))
    asset_name = _require_string(record, "asset_name")
    download_url = _require_string(record, "download_url")
    _validate_release_url(target_id, channel, asset_name, download_url)

    return ReleaseRecord(
        target_id=target_id,
        target_name=target_name,
        channel=channel,
        generated_at=generated_at,
        asset_name=asset_name,
        download_url=download_url,
        archive_size=_require_positive_integer(record, "archive_size"),
        archive_sha256=_require_sha256(record, "archive_sha256"),
        save_size=_require_positive_integer(record, "save_size"),
        save_sha256=_require_sha256(record, "save_sha256"),
    )


def parse_catalog(payload: bytes) -> Catalog:
    try:
        decoded = payload.decode("utf-8")
        document = json.loads(decoded, object_pairs_hook=_json_object)
    except CatalogError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CatalogError("invalid_catalog", "catalog is not valid UTF-8 JSON") from error

    top_level = _require_exact_keys(document, _TOP_LEVEL_KEYS, "catalog")
    if type(top_level["schema_version"]) is not int or top_level["schema_version"] != 1:
        raise CatalogError("unsupported_schema", "schema_version must be 1")
    if not isinstance(top_level["records"], list):
        raise CatalogError("invalid_catalog", "records must be a list")

    records: list[ReleaseRecord] = []
    identities: set[tuple[str, Channel]] = set()
    for value in top_level["records"]:
        record = _parse_record(value)
        identity = (record.target_id, record.channel)
        if identity in identities:
            raise CatalogError(
                "duplicate_record", "target and channel records must be unique"
            )
        identities.add(identity)
        records.append(record)

    return Catalog(schema_version=1, records=tuple(records))


def select_record(
    catalog: Catalog, target_id: str, channel: Channel
) -> ReleaseRecord:
    for record in catalog.records:
        if record.target_id == target_id and record.channel is channel:
            return record
    raise CatalogError(
        "unavailable_channel", f"no {channel.value} release for target {target_id}"
    )


def _validate_catalog_url(url: str) -> None:
    if url != CATALOG_URL or not _is_trusted_https_url(url, initial=True):
        raise CatalogError("untrusted_catalog", "catalog URL is not trusted")


def _network_opener(opener: OpenerDirector | None) -> OpenerDirector:
    return opener if opener is not None else build_opener(TrustedRedirectHandler())


def fetch_catalog(
    url: str = CATALOG_URL,
    *,
    timeout: float = 20.0,
    opener: OpenerDirector | None = None,
) -> Catalog:
    _validate_catalog_url(url)
    try:
        with _network_opener(opener).open(url, timeout=timeout) as response:
            payload = response.read()
    except HTTPError as error:
        raise CatalogError(
            "http_error", f"catalog request failed with HTTP {error.code}"
        ) from error
    except URLError as error:
        raise CatalogError("network_error", "catalog request failed") from error
    except (TimeoutError, socket.timeout) as error:
        raise CatalogError("timeout", "catalog request timed out") from error
    except OSError as error:
        raise CatalogError("network_error", "catalog request failed") from error
    return parse_catalog(payload)


def _validate_download_record(record: ReleaseRecord) -> None:
    try:
        _validate_release_url(
            record.target_id,
            record.channel,
            record.asset_name,
            record.download_url,
        )
    except CatalogError as error:
        raise DownloadError("untrusted_asset", str(error)) from error
    if type(record.archive_size) is not int or record.archive_size <= 0:
        raise DownloadError("invalid_record", "archive size must be positive")
    if (
        not isinstance(record.archive_sha256, str)
        or _SHA256_PATTERN.fullmatch(record.archive_sha256) is None
    ):
        raise DownloadError("invalid_record", "archive checksum is invalid")


def _response_content_length(response: Any) -> int | None:
    raw_length = response.headers.get("Content-Length")
    if raw_length is None:
        return None
    try:
        length = int(raw_length)
    except (TypeError, ValueError) as error:
        raise DownloadError(
            "size_mismatch", "response Content-Length is invalid"
        ) from error
    if length < 0:
        raise DownloadError("size_mismatch", "response Content-Length is invalid")
    return length


def download_archive(
    record: ReleaseRecord,
    destination: Path,
    *,
    progress: Callable[[int, int], None],
    cancelled: Callable[[], bool],
    timeout: float = 30.0,
    opener: OpenerDirector | None = None,
) -> None:
    complete = False
    try:
        _validate_download_record(record)
        if record.archive_size > MAX_ARCHIVE_BYTES:
            raise DownloadError(
                "archive_too_large",
                f"archive exceeds {MAX_ARCHIVE_BYTES} byte limit",
            )
        if cancelled():
            raise DownloadError("cancelled", "download cancelled")

        with _network_opener(opener).open(
            record.download_url, timeout=timeout
        ) as response:
            content_length = _response_content_length(response)
            if content_length is not None:
                if content_length > MAX_ARCHIVE_BYTES:
                    raise DownloadError(
                        "archive_too_large",
                        f"response exceeds {MAX_ARCHIVE_BYTES} byte limit",
                    )
                if content_length > record.archive_size:
                    raise DownloadError(
                        "size_mismatch", "response exceeds declared archive size"
                    )

            downloaded = 0
            digest = hashlib.sha256()
            progress(0, record.archive_size)
            with destination.open("wb") as output:
                while True:
                    if cancelled():
                        raise DownloadError("cancelled", "download cancelled")
                    remaining = record.archive_size - downloaded
                    read_size = min(_DOWNLOAD_CHUNK_BYTES, remaining + 1)
                    chunk = response.read(read_size)
                    if not chunk:
                        break
                    next_downloaded = downloaded + len(chunk)
                    if next_downloaded > MAX_ARCHIVE_BYTES:
                        raise DownloadError(
                            "archive_too_large",
                            f"response exceeds {MAX_ARCHIVE_BYTES} byte limit",
                        )
                    if next_downloaded > record.archive_size:
                        raise DownloadError(
                            "size_mismatch",
                            "response exceeds declared archive size",
                        )
                    output.write(chunk)
                    digest.update(chunk)
                    downloaded = next_downloaded
                    progress(downloaded, record.archive_size)

                if downloaded != record.archive_size:
                    raise DownloadError(
                        "size_mismatch",
                        f"received {downloaded} of {record.archive_size} bytes",
                    )
                if digest.hexdigest() != record.archive_sha256:
                    raise DownloadError(
                        "checksum_mismatch", "archive checksum does not match"
                    )
                output.flush()
                os.fsync(output.fileno())
            complete = True
    except DownloadError:
        raise
    except HTTPError as error:
        raise DownloadError(
            "http_error", f"archive request failed with HTTP {error.code}"
        ) from error
    except URLError as error:
        if isinstance(error.reason, (TimeoutError, socket.timeout)):
            raise DownloadError("timeout", "archive request timed out") from error
        raise DownloadError("network_error", "archive request failed") from error
    except (TimeoutError, socket.timeout) as error:
        raise DownloadError("timeout", "archive request timed out") from error
    except http.client.IncompleteRead as error:
        raise DownloadError("size_mismatch", "archive response ended early") from error
    except OSError as error:
        raise DownloadError("io_error", "archive download failed") from error
    finally:
        if not complete:
            try:
                destination.unlink(missing_ok=True)
            except OSError:
                pass
