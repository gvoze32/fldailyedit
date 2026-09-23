from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import OpenerDirector, build_opener

from installer import (
    APP_INSTALLER_ASSET_NAME,
    APP_INSTALLER_URL,
    APP_UPDATE_MANIFEST_URL,
    __version__,
)
from installer.catalog import TrustedRedirectHandler


MAX_APP_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_APP_EXECUTABLE_BYTES = 128 * 1024 * 1024
MAX_MANIFEST_BYTES = 64 * 1024
_DOWNLOAD_CHUNK_BYTES = 64 * 1024
_STAGING_PREFIX = "fldailyedit-app-update-"
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_VERSION_PATTERN = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)\Z")


@dataclass(frozen=True, slots=True)
class AppUpdateManifest:
    version: str
    asset_name: str
    download_url: str
    archive_size: int
    archive_sha256: str


class AppUpdateError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AppUpdateError("invalid_manifest", f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _require_exact_keys(
    value: object,
    expected: frozenset[str],
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise AppUpdateError(
            "invalid_manifest",
            f"manifest must contain exactly {sorted(expected)}",
        )
    return value


def _require_string(document: dict[str, Any], field: str) -> str:
    value = document[field]
    if not isinstance(value, str) or not value:
        raise AppUpdateError("invalid_manifest", f"{field} must be a non-empty string")
    return value


def _parse_version(value: str) -> tuple[int, int, int]:
    match = _VERSION_PATTERN.fullmatch(value)
    if match is None:
        raise AppUpdateError(
            "invalid_manifest",
            "version must use the numeric MAJOR.MINOR.PATCH format",
        )
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def _require_sha256(value: str) -> str:
    if _SHA256_PATTERN.fullmatch(value) is None:
        raise AppUpdateError(
            "invalid_manifest",
            "archive_sha256 must be a lowercase SHA-256 digest",
        )
    return value


def _validate_exact_url(url: str, expected: str, *, code: str) -> None:
    if url != expected:
        raise AppUpdateError(code, "update URL is not trusted")
    try:
        parsed = urlsplit(url)
    except ValueError as error:
        raise AppUpdateError(code, "update URL is malformed") from error
    if (
        parsed.scheme != "https"
        or parsed.netloc != "github.com"
        or parsed.query
        or parsed.fragment
    ):
        raise AppUpdateError(code, "update URL is not trusted")


def parse_app_update_manifest(payload: bytes) -> AppUpdateManifest:
    try:
        decoded = payload.decode("utf-8")
        document = json.loads(decoded, object_pairs_hook=_json_object)
    except AppUpdateError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AppUpdateError(
            "invalid_manifest",
            "update manifest is not valid UTF-8 JSON",
        ) from error

    top_level = _require_exact_keys(
        document,
        frozenset(
            {
                "schema_version",
                "version",
                "asset_name",
                "download_url",
                "archive_size",
                "archive_sha256",
            }
        ),
    )
    if type(top_level["schema_version"]) is not int or top_level["schema_version"] != 1:
        raise AppUpdateError("unsupported_manifest", "manifest schema_version must be 1")

    version = _require_string(top_level, "version")
    _parse_version(version)
    asset_name = _require_string(top_level, "asset_name")
    if asset_name != APP_INSTALLER_ASSET_NAME:
        raise AppUpdateError("untrusted_asset", "installer asset name is not trusted")
    download_url = _require_string(top_level, "download_url")
    _validate_exact_url(download_url, APP_INSTALLER_URL, code="untrusted_asset")

    archive_size = top_level["archive_size"]
    if type(archive_size) is not int or archive_size <= 0:
        raise AppUpdateError("invalid_manifest", "archive_size must be positive")
    if archive_size > MAX_APP_ARCHIVE_BYTES:
        raise AppUpdateError(
            "archive_too_large",
            f"installer archive exceeds {MAX_APP_ARCHIVE_BYTES} byte limit",
        )

    archive_sha256 = _require_sha256(_require_string(top_level, "archive_sha256"))
    return AppUpdateManifest(
        version=version,
        asset_name=asset_name,
        download_url=download_url,
        archive_size=archive_size,
        archive_sha256=archive_sha256,
    )


def _network_opener(opener: OpenerDirector | None) -> OpenerDirector:
    return opener if opener is not None else build_opener(TrustedRedirectHandler())


def _read_manifest_response(response: Any) -> bytes:
    raw_length = response.headers.get("Content-Length")
    if raw_length is not None:
        try:
            content_length = int(raw_length)
        except (TypeError, ValueError) as error:
            raise AppUpdateError(
                "invalid_manifest",
                "update manifest Content-Length is invalid",
            ) from error
        if content_length < 0 or content_length > MAX_MANIFEST_BYTES:
            raise AppUpdateError("manifest_too_large", "update manifest is too large")
    payload = response.read(MAX_MANIFEST_BYTES + 1)
    if len(payload) > MAX_MANIFEST_BYTES:
        raise AppUpdateError("manifest_too_large", "update manifest is too large")
    return payload


def fetch_app_update_manifest(
    url: str = APP_UPDATE_MANIFEST_URL,
    *,
    timeout: float = 15.0,
    opener: OpenerDirector | None = None,
) -> AppUpdateManifest:
    _validate_exact_url(url, APP_UPDATE_MANIFEST_URL, code="untrusted_manifest")
    try:
        with _network_opener(opener).open(url, timeout=timeout) as response:
            payload = _read_manifest_response(response)
    except AppUpdateError:
        raise
    except HTTPError as error:
        raise AppUpdateError(
            "update_http_error",
            f"update check failed with HTTP {error.code}",
        ) from error
    except URLError as error:
        reason = error.reason
        if isinstance(reason, (TimeoutError, socket.timeout)):
            raise AppUpdateError("update_timeout", "update check timed out") from error
        raise AppUpdateError("update_network_error", "update check failed") from error
    except (TimeoutError, socket.timeout) as error:
        raise AppUpdateError("update_timeout", "update check timed out") from error
    except OSError as error:
        raise AppUpdateError("update_network_error", "update check failed") from error
    return parse_app_update_manifest(payload)


def is_newer_version(version: str, current_version: str = __version__) -> bool:
    return _parse_version(version) > _parse_version(current_version)


def is_app_update_available(
    manifest: AppUpdateManifest,
    current_version: str = __version__,
) -> bool:
    return is_newer_version(manifest.version, current_version)


def _validate_manifest_for_download(manifest: AppUpdateManifest) -> None:
    parsed = parse_app_update_manifest(
        json.dumps(
            {
                "schema_version": 1,
                "version": manifest.version,
                "asset_name": manifest.asset_name,
                "download_url": manifest.download_url,
                "archive_size": manifest.archive_size,
                "archive_sha256": manifest.archive_sha256,
            }
        ).encode("utf-8")
    )
    if parsed != manifest:
        raise AppUpdateError("invalid_manifest", "update manifest changed during download")


def _response_content_length(response: Any) -> int | None:
    raw_length = response.headers.get("Content-Length")
    if raw_length is None:
        return None
    try:
        length = int(raw_length)
    except (TypeError, ValueError) as error:
        raise AppUpdateError(
            "size_mismatch",
            "installer archive Content-Length is invalid",
        ) from error
    if length < 0:
        raise AppUpdateError("size_mismatch", "installer archive Content-Length is invalid")
    return length


def download_app_update(
    manifest: AppUpdateManifest,
    destination: Path,
    *,
    progress: Callable[[int, int], None] | None = None,
    timeout: float = 60.0,
    opener: OpenerDirector | None = None,
) -> None:
    _validate_manifest_for_download(manifest)
    destination = Path(destination)
    complete = False
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with _network_opener(opener).open(
            manifest.download_url,
            timeout=timeout,
        ) as response:
            content_length = _response_content_length(response)
            if content_length is not None:
                if content_length > MAX_APP_ARCHIVE_BYTES:
                    raise AppUpdateError(
                        "archive_too_large",
                        "installer archive exceeds the download limit",
                    )
                if content_length > manifest.archive_size:
                    raise AppUpdateError(
                        "size_mismatch",
                        "installer archive exceeds its declared size",
                    )

            downloaded = 0
            digest = hashlib.sha256()
            if progress is not None:
                progress(0, manifest.archive_size)
            with destination.open("wb") as output:
                while True:
                    remaining = manifest.archive_size - downloaded
                    read_size = min(_DOWNLOAD_CHUNK_BYTES, remaining + 1)
                    chunk = response.read(read_size)
                    if not chunk:
                        break
                    next_downloaded = downloaded + len(chunk)
                    if next_downloaded > MAX_APP_ARCHIVE_BYTES:
                        raise AppUpdateError(
                            "archive_too_large",
                            "installer archive exceeds the download limit",
                        )
                    if next_downloaded > manifest.archive_size:
                        raise AppUpdateError(
                            "size_mismatch",
                            "installer archive exceeds its declared size",
                        )
                    output.write(chunk)
                    digest.update(chunk)
                    downloaded = next_downloaded
                    if progress is not None:
                        progress(downloaded, manifest.archive_size)
                if downloaded != manifest.archive_size:
                    raise AppUpdateError(
                        "size_mismatch",
                        f"received {downloaded} of {manifest.archive_size} bytes",
                    )
                if digest.hexdigest() != manifest.archive_sha256:
                    raise AppUpdateError(
                        "checksum_mismatch",
                        "installer archive checksum does not match",
                    )
                output.flush()
                os.fsync(output.fileno())
        complete = True
    except AppUpdateError:
        raise
    except HTTPError as error:
        raise AppUpdateError(
            "update_http_error",
            f"installer download failed with HTTP {error.code}",
        ) from error
    except URLError as error:
        reason = error.reason
        if isinstance(reason, (TimeoutError, socket.timeout)):
            raise AppUpdateError("update_timeout", "installer download timed out") from error
        raise AppUpdateError("update_network_error", "installer download failed") from error
    except (TimeoutError, socket.timeout) as error:
        raise AppUpdateError("update_timeout", "installer download timed out") from error
    except http.client.IncompleteRead as error:
        raise AppUpdateError("size_mismatch", "installer download ended early") from error
    except OSError as error:
        raise AppUpdateError("update_io_error", "installer download failed") from error
    finally:
        if not complete:
            try:
                destination.unlink(missing_ok=True)
            except OSError as error:
                raise AppUpdateError(
                    "cleanup_failed",
                    "partial installer download could not be removed",
                ) from error


def stage_app_update(
    manifest: AppUpdateManifest,
    *,
    timeout: float = 60.0,
    opener: OpenerDirector | None = None,
) -> Path:
    _validate_manifest_for_download(manifest)
    staging_directory = Path(tempfile.mkdtemp(prefix=_STAGING_PREFIX))
    archive_path = staging_directory / manifest.asset_name
    executable_path = staging_directory / "FLDailyEditInstaller.exe"
    try:
        download_app_update(
            manifest,
            archive_path,
            timeout=timeout,
            opener=opener,
        )
        with zipfile.ZipFile(archive_path) as archive:
            entries = archive.infolist()
            if len(entries) != 1 or entries[0].filename != executable_path.name:
                raise AppUpdateError(
                    "invalid_archive",
                    "installer archive must contain exactly FLDailyEditInstaller.exe",
                )
            entry = entries[0]
            if entry.is_dir() or entry.file_size <= 0:
                raise AppUpdateError("invalid_archive", "installer executable is invalid")
            if entry.file_size > MAX_APP_EXECUTABLE_BYTES:
                raise AppUpdateError(
                    "archive_too_large",
                    "installer executable exceeds the size limit",
                )
            with archive.open(entry, "r") as source, executable_path.open("wb") as target:
                shutil.copyfileobj(source, target, length=_DOWNLOAD_CHUNK_BYTES)
                target.flush()
                os.fsync(target.fileno())
        archive_path.unlink()
        return executable_path
    except AppUpdateError:
        shutil.rmtree(staging_directory, ignore_errors=True)
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile) as error:
        shutil.rmtree(staging_directory, ignore_errors=True)
        raise AppUpdateError(
            "invalid_archive",
            "downloaded installer archive could not be opened",
        ) from error


def cleanup_staged_app_update(staged_executable: Path) -> None:
    staged_executable = Path(staged_executable)
    if (
        staged_executable.name != "FLDailyEditInstaller.exe"
        or not staged_executable.parent.name.startswith(_STAGING_PREFIX)
    ):
        return
    shutil.rmtree(staged_executable.parent, ignore_errors=True)


def packaged_windows_app() -> bool:
    return sys.platform == "win32" and bool(getattr(sys, "frozen", False))


def _write_update_script() -> Path:
    descriptor, raw_path = tempfile.mkstemp(
        prefix="fldailyedit-app-update-",
        suffix=".ps1",
        text=True,
    )
    script_path = Path(raw_path)
    script = """param(\n    [int]$ParentPid,\n    [string]$Source,\n    [string]$Target\n)\n\ntry {\n    try {\n        Wait-Process -Id $ParentPid -Timeout 120 -ErrorAction SilentlyContinue\n    } catch {\n    }\n\n    for ($attempt = 0; $attempt -lt 120; $attempt++) {\n        try {\n            Move-Item -LiteralPath $Source -Destination $Target -Force -ErrorAction Stop\n            Start-Process -FilePath $Target\n            break\n        } catch {\n            Start-Sleep -Seconds 1\n        }\n    }\n} finally {\n    Remove-Item -LiteralPath $Source -Force -ErrorAction SilentlyContinue\n    Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue\n}\n"""
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(script)
            output.flush()
            os.fsync(output.fileno())
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        script_path.unlink(missing_ok=True)
        raise
    return script_path


def schedule_app_update(
    staged_executable: Path,
    current_executable: Path | None = None,
    *,
    popen: Callable[..., Any] = subprocess.Popen,
) -> None:
    if sys.platform != "win32":
        raise AppUpdateError(
            "update_not_supported",
            "automatic app updates are only supported in the packaged Windows app",
        )
    staged_executable = Path(staged_executable)
    target = Path(sys.executable if current_executable is None else current_executable)
    if not staged_executable.is_file():
        raise AppUpdateError("update_staging_missing", "staged installer executable is missing")
    if target.name != staged_executable.name or not target.parent.is_dir():
        raise AppUpdateError("update_target_invalid", "current installer path is invalid")

    script_path = _write_update_script()
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
        subprocess,
        "CREATE_NEW_PROCESS_GROUP",
        0,
    )
    try:
        popen(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script_path),
                str(os.getpid()),
                str(staged_executable),
                str(target),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
            close_fds=True,
        )
    except OSError as error:
        script_path.unlink(missing_ok=True)
        raise AppUpdateError(
            "update_launch_failed",
            "the Windows update helper could not be started",
        ) from error
