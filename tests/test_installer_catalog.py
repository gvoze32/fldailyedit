from __future__ import annotations

import copy
import hashlib
import json
import threading
import time
from dataclasses import FrozenInstanceError, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterator
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pytest

from installer.catalog import (
    MAX_ARCHIVE_BYTES,
    CatalogError,
    Channel,
    DownloadError,
    ReleaseRecord,
    TrustedRedirectHandler,
    download_archive,
    fetch_catalog,
    parse_catalog,
    select_record,
)


VALID_CATALOG = {
    "schema_version": 1,
    "records": [
        {
            "target_id": "fl26-u2.2-national-squads",
            "target_name": "Football Life 2026 Update 2.2 + National Squads",
            "channel": "fast",
            "generated_at": "2026-08-06T00:00:00Z",
            "asset_name": "fldailyedit-fl2026-fast.zip",
            "download_url": "https://github.com/gvoze32/fldailyedit/releases/download/latest/fldailyedit-fl2026-fast.zip",
            "archive_size": 123,
            "archive_sha256": "a" * 64,
            "save_size": 10995800,
            "save_sha256": "b" * 64,
        },
        {
            "target_id": "fl26-u2.2-national-squads",
            "target_name": "Football Life 2026 Update 2.2 + National Squads",
            "channel": "deep",
            "generated_at": "2026-08-06T01:00:00Z",
            "asset_name": "fldailyedit-fl2026-deep.zip",
            "download_url": "https://github.com/gvoze32/fldailyedit/releases/download/latest/fldailyedit-fl2026-deep.zip",
            "archive_size": 456,
            "archive_sha256": "c" * 64,
            "save_size": 10995800,
            "save_sha256": "d" * 64,
        },
    ],
}


def _payload(catalog: object = VALID_CATALOG) -> bytes:
    return json.dumps(catalog).encode("utf-8")


def _catalog_copy() -> dict[str, object]:
    return copy.deepcopy(VALID_CATALOG)


def test_selects_fast_and_deep_records_independently() -> None:
    catalog = parse_catalog(_payload())

    fast = select_record(catalog, "fl26-u2.2-national-squads", Channel.FAST)
    deep = select_record(catalog, "fl26-u2.2-national-squads", Channel.DEEP)

    assert fast.asset_name == "fldailyedit-fl2026-fast.zip"
    assert deep.asset_name == "fldailyedit-fl2026-deep.zip"
    assert fast.channel is Channel.FAST
    assert deep.channel is Channel.DEEP


def test_missing_pes_2021_channel_is_unavailable() -> None:
    catalog = parse_catalog(_payload())

    with pytest.raises(CatalogError) as caught:
        select_record(catalog, "pes2021", Channel.FAST)

    assert caught.value.code == "unavailable_channel"


def test_catalog_and_records_are_immutable() -> None:
    catalog = parse_catalog(_payload())

    assert isinstance(catalog.records, tuple)
    with pytest.raises(FrozenInstanceError):
        catalog.schema_version = 2  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        catalog.records[0].asset_name = "changed.zip"  # type: ignore[misc]


def test_rejects_duplicate_target_channel_records() -> None:
    document = _catalog_copy()
    document["records"].append(copy.deepcopy(document["records"][0]))  # type: ignore[union-attr,index]

    with pytest.raises(CatalogError):
        parse_catalog(_payload(document))


@pytest.mark.parametrize("unknown_key", ["extra", "signature"])
def test_rejects_unknown_top_level_keys(unknown_key: str) -> None:
    document = _catalog_copy()
    document[unknown_key] = "unexpected"

    with pytest.raises(CatalogError):
        parse_catalog(_payload(document))


def test_rejects_unknown_record_keys() -> None:
    document = _catalog_copy()
    document["records"][0]["extra"] = "unexpected"  # type: ignore[index]

    with pytest.raises(CatalogError):
        parse_catalog(_payload(document))


@pytest.mark.parametrize("version", [0, 2, True, "1"])
def test_rejects_schema_versions_other_than_integer_one(version: object) -> None:
    document = _catalog_copy()
    document["schema_version"] = version

    with pytest.raises(CatalogError):
        parse_catalog(_payload(document))


@pytest.mark.parametrize(
    "timestamp",
    [
        "2026-08-06T00:00:00+00:00",
        "2026-08-06T01:00:00+01:00",
        "2026-08-06T00:00:00",
        "not-a-timestamp",
    ],
)
def test_rejects_non_utc_or_invalid_timestamps(timestamp: str) -> None:
    document = _catalog_copy()
    document["records"][0]["generated_at"] = timestamp  # type: ignore[index]

    with pytest.raises(CatalogError):
        parse_catalog(_payload(document))


@pytest.mark.parametrize("field", ["archive_size", "save_size"])
@pytest.mark.parametrize("value", [True, False, 0, -1, 1.5, "123"])
def test_requires_positive_integer_sizes(field: str, value: object) -> None:
    document = _catalog_copy()
    document["records"][0][field] = value  # type: ignore[index]

    with pytest.raises(CatalogError):
        parse_catalog(_payload(document))


@pytest.mark.parametrize("field", ["archive_sha256", "save_sha256"])
@pytest.mark.parametrize("digest", ["A" * 64, "a" * 63, "a" * 65, "g" * 64, 123])
def test_requires_lowercase_sha256(field: str, digest: object) -> None:
    document = _catalog_copy()
    document["records"][0][field] = digest  # type: ignore[index]

    with pytest.raises(CatalogError):
        parse_catalog(_payload(document))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("target_id", 1),
        ("target_name", None),
        ("channel", 1),
        ("generated_at", 1),
        ("asset_name", None),
        ("download_url", None),
    ],
)
def test_requires_string_record_fields(field: str, value: object) -> None:
    document = _catalog_copy()
    document["records"][0][field] = value  # type: ignore[index]

    with pytest.raises(CatalogError):
        parse_catalog(_payload(document))


def test_rejects_target_channel_asset_tuple_outside_allowlist() -> None:
    document = _catalog_copy()
    document["records"][0]["target_id"] = "pes2021"  # type: ignore[index]

    with pytest.raises(CatalogError):
        parse_catalog(_payload(document))


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/gvoze32/fldailyedit/releases/download/latest/fldailyedit-fl2026-fast.zip",
        "https://example.com/gvoze32/fldailyedit/releases/download/latest/fldailyedit-fl2026-fast.zip",
        "https://github.com/attacker/fldailyedit/releases/download/latest/fldailyedit-fl2026-fast.zip",
        "https://github.com/gvoze32/other/releases/download/latest/fldailyedit-fl2026-fast.zip",
        "https://github.com/gvoze32/fldailyedit/releases/download/v1/fldailyedit-fl2026-fast.zip",
        "https://github.com/gvoze32/fldailyedit/releases/download/latest/other.zip",
        "https://github.com/gvoze32/fldailyedit/releases/download/latest/fldailyedit-fl2026-fast.zip?raw=1",
    ],
)
def test_rejects_untrusted_release_urls(url: str) -> None:
    document = _catalog_copy()
    document["records"][0]["download_url"] = url  # type: ignore[index]

    with pytest.raises(CatalogError):
        parse_catalog(_payload(document))


def test_malformed_release_authority_raises_coded_catalog_error() -> None:
    document = _catalog_copy()
    document["records"][0]["download_url"] = (  # type: ignore[index]
        "https://[invalid/gvoze32/fldailyedit/releases/download/latest/"
        "fldailyedit-fl2026-fast.zip"
    )

    with pytest.raises(CatalogError) as caught:
        parse_catalog(_payload(document))

    assert caught.value.code == "untrusted_asset"


def test_rejects_url_basename_that_differs_from_asset_name() -> None:
    document = _catalog_copy()
    document["records"][0]["asset_name"] = "fldailyedit-fl2026-deep.zip"  # type: ignore[index]

    with pytest.raises(CatalogError):
        parse_catalog(_payload(document))


@pytest.mark.parametrize(
    "payload",
    [
        b"\xff",
        b"not json",
        b"[]",
        _payload({"schema_version": 1}),
        _payload({"schema_version": 1, "records": {}}),
    ],
)
def test_rejects_invalid_catalog_documents(payload: bytes) -> None:
    with pytest.raises(CatalogError):
        parse_catalog(payload)


class _CatalogHTTPHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def do_GET(self) -> None:
        server = self.server
        server.request_count += 1  # type: ignore[attr-defined]
        status, body, declared_length, pause = server.routes[self.path]  # type: ignore[attr-defined]
        self.send_response(status)
        self.send_header("Content-Length", str(declared_length))
        self.end_headers()
        if not body:
            return
        try:
            if pause is None:
                self.wfile.write(body)
            else:
                self.wfile.write(body[:1])
                self.wfile.flush()
                time.sleep(pause)
                self.wfile.write(body[1:])
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture
def local_server() -> Iterator[ThreadingHTTPServer]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _CatalogHTTPHandler)
    server.routes = {}  # type: ignore[attr-defined]
    server.request_count = 0  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def _serve(
    server: ThreadingHTTPServer,
    path: str,
    body: bytes,
    *,
    status: int = 200,
    declared_length: int | None = None,
    pause: float | None = None,
) -> None:
    if declared_length is None:
        declared_length = len(body)
    server.routes[path] = (status, body, declared_length, pause)  # type: ignore[attr-defined]


class _LocalServerOpener:
    def __init__(self, server: ThreadingHTTPServer, path: str):
        self._server = server
        self._path = path
        self.requested_urls: list[str] = []

    def open(self, url: str, timeout: float):
        self.requested_urls.append(url)
        local_url = f"http://127.0.0.1:{self._server.server_port}{self._path}"
        return urlopen(local_url, timeout=timeout)


class _FailingOpener:
    def __init__(self, error: Exception):
        self._error = error

    def open(self, url: str, timeout: float):
        raise self._error


def _record_for(
    body: bytes, *, archive_size: int | None = None, digest: str | None = None
) -> ReleaseRecord:
    original = select_record(
        parse_catalog(_payload()), "fl26-u2.2-national-squads", Channel.FAST
    )
    return replace(
        original,
        archive_size=len(body) if archive_size is None else archive_size,
        archive_sha256=hashlib.sha256(body).hexdigest() if digest is None else digest,
    )


def test_fetch_catalog_uses_injected_local_network_opener(
    local_server: ThreadingHTTPServer,
) -> None:
    _serve(local_server, "/catalog", _payload())
    opener = _LocalServerOpener(local_server, "/catalog")

    catalog = fetch_catalog(opener=opener)

    assert select_record(
        catalog, "fl26-u2.2-national-squads", Channel.DEEP
    ).asset_name == "fldailyedit-fl2026-deep.zip"


def test_fetch_catalog_rejects_an_untrusted_initial_url_before_request(
    local_server: ThreadingHTTPServer,
) -> None:
    opener = _LocalServerOpener(local_server, "/unused")

    with pytest.raises(CatalogError):
        fetch_catalog("https://example.com/catalog.json", opener=opener)

    assert opener.requested_urls == []


def test_trusted_redirect_handler_allows_only_https_github_hosts() -> None:
    handler = TrustedRedirectHandler()
    request = Request(
        "https://github.com/gvoze32/fldailyedit/releases/download/latest/catalog.json"
    )
    trusted = (
        "https://release-assets.githubusercontent.com/path/to/catalog.json"
    )

    redirected = handler.redirect_request(request, None, 302, "Found", {}, trusted)

    assert redirected is not None
    assert redirected.full_url == trusted


@pytest.mark.parametrize(
    "redirect_url",
    [
        "https://example.com/catalog.json",
        "https://githubusercontent.com.example.com/catalog.json",
        "http://github.com/gvoze32/fldailyedit/releases/download/latest/catalog.json",
    ],
)
def test_trusted_redirect_handler_rejects_untrusted_redirects(
    redirect_url: str,
) -> None:
    handler = TrustedRedirectHandler()
    request = Request(
        "https://github.com/gvoze32/fldailyedit/releases/download/latest/catalog.json"
    )

    with pytest.raises(HTTPError):
        handler.redirect_request(request, None, 302, "Found", {}, redirect_url)


def test_download_streams_verified_bytes_and_monotonic_progress(
    local_server: ThreadingHTTPServer, tmp_path: Path
) -> None:
    body = (b"archive-data-" * 7000) + b"done"
    _serve(local_server, "/archive", body)
    opener = _LocalServerOpener(local_server, "/archive")
    progress_events: list[tuple[int, int]] = []
    destination = tmp_path / "archive.zip"

    download_archive(
        _record_for(body),
        destination,
        progress=lambda downloaded, expected: progress_events.append(
            (downloaded, expected)
        ),
        cancelled=lambda: False,
        opener=opener,
    )

    assert destination.read_bytes() == body
    assert progress_events[0] == (0, len(body))
    assert progress_events[-1] == (len(body), len(body))
    assert progress_events == sorted(progress_events)


def test_download_rejects_declared_size_above_limit_before_request(
    local_server: ThreadingHTTPServer, tmp_path: Path
) -> None:
    opener = _LocalServerOpener(local_server, "/unused")
    record = _record_for(b"x", archive_size=MAX_ARCHIVE_BYTES + 1)

    with pytest.raises(DownloadError) as caught:
        download_archive(
            record,
            tmp_path / "archive.zip",
            progress=lambda downloaded, expected: None,
            cancelled=lambda: False,
            opener=opener,
        )

    assert caught.value.code == "archive_too_large"
    assert opener.requested_urls == []


def test_download_rejects_response_larger_than_declared_size(
    local_server: ThreadingHTTPServer, tmp_path: Path
) -> None:
    _serve(local_server, "/oversize", b"four")
    destination = tmp_path / "archive.zip"

    with pytest.raises(DownloadError) as caught:
        download_archive(
            _record_for(b"abc"),
            destination,
            progress=lambda downloaded, expected: None,
            cancelled=lambda: False,
            opener=_LocalServerOpener(local_server, "/oversize"),
        )

    assert caught.value.code == "size_mismatch"
    assert not destination.exists()


def test_download_rejects_response_larger_than_global_limit(
    local_server: ThreadingHTTPServer, tmp_path: Path
) -> None:
    _serve(
        local_server,
        "/global-oversize",
        b"",
        declared_length=MAX_ARCHIVE_BYTES + 1,
    )
    destination = tmp_path / "archive.zip"

    with pytest.raises(DownloadError) as caught:
        download_archive(
            _record_for(b"x", archive_size=MAX_ARCHIVE_BYTES),
            destination,
            progress=lambda downloaded, expected: None,
            cancelled=lambda: False,
            opener=_LocalServerOpener(local_server, "/global-oversize"),
        )

    assert caught.value.code == "archive_too_large"
    assert not destination.exists()


def test_download_removes_partial_file_on_timeout(
    local_server: ThreadingHTTPServer, tmp_path: Path
) -> None:
    body = b"slow"
    _serve(local_server, "/slow", body, pause=0.2)
    destination = tmp_path / "archive.zip"

    with pytest.raises(DownloadError) as caught:
        download_archive(
            _record_for(body),
            destination,
            progress=lambda downloaded, expected: None,
            cancelled=lambda: False,
            timeout=0.02,
            opener=_LocalServerOpener(local_server, "/slow"),
        )

    assert caught.value.code == "timeout"
    assert not destination.exists()


def test_download_removes_partial_file_on_cancellation(
    local_server: ThreadingHTTPServer, tmp_path: Path
) -> None:
    body = b"x" * (64 * 1024 + 1)
    _serve(local_server, "/cancel", body)
    destination = tmp_path / "archive.zip"
    progress_events: list[tuple[int, int]] = []

    with pytest.raises(DownloadError) as caught:
        download_archive(
            _record_for(body),
            destination,
            progress=lambda downloaded, expected: progress_events.append(
                (downloaded, expected)
            ),
            cancelled=lambda: bool(
                progress_events and progress_events[-1][0] > 0
            ),
            opener=_LocalServerOpener(local_server, "/cancel"),
        )

    assert caught.value.code == "cancelled"
    assert not destination.exists()


def test_download_removes_partial_file_on_short_body(
    local_server: ThreadingHTTPServer, tmp_path: Path
) -> None:
    _serve(local_server, "/short", b"ab", declared_length=3)
    destination = tmp_path / "archive.zip"

    with pytest.raises(DownloadError) as caught:
        download_archive(
            _record_for(b"abc"),
            destination,
            progress=lambda downloaded, expected: None,
            cancelled=lambda: False,
            opener=_LocalServerOpener(local_server, "/short"),
        )

    assert caught.value.code == "size_mismatch"
    assert not destination.exists()


def test_download_removes_partial_file_on_checksum_mismatch(
    local_server: ThreadingHTTPServer, tmp_path: Path
) -> None:
    body = b"archive"
    _serve(local_server, "/checksum", body)
    destination = tmp_path / "archive.zip"

    with pytest.raises(DownloadError) as caught:
        download_archive(
            _record_for(body, digest="0" * 64),
            destination,
            progress=lambda downloaded, expected: None,
            cancelled=lambda: False,
            opener=_LocalServerOpener(local_server, "/checksum"),
        )

    assert caught.value.code == "checksum_mismatch"
    assert not destination.exists()


def test_download_surfaces_partial_cleanup_failure(
    local_server: ThreadingHTTPServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = b"archive"
    _serve(local_server, "/cleanup-failure", body)
    destination = tmp_path / "archive.zip"

    def fail_unlink(path: Path, missing_ok: bool = False) -> None:
        raise PermissionError("cleanup denied")

    monkeypatch.setattr(Path, "unlink", fail_unlink)

    with pytest.raises(DownloadError) as caught:
        download_archive(
            _record_for(body, digest="0" * 64),
            destination,
            progress=lambda downloaded, expected: None,
            cancelled=lambda: False,
            opener=_LocalServerOpener(local_server, "/cleanup-failure"),
        )

    assert caught.value.code == "cleanup_failed"
    assert destination.exists()


def test_download_maps_http_errors_to_stable_code(
    local_server: ThreadingHTTPServer, tmp_path: Path
) -> None:
    _serve(local_server, "/failure", b"unavailable", status=503)

    with pytest.raises(DownloadError) as caught:
        download_archive(
            _record_for(b"x"),
            tmp_path / "archive.zip",
            progress=lambda downloaded, expected: None,
            cancelled=lambda: False,
            opener=_LocalServerOpener(local_server, "/failure"),
        )

    assert caught.value.code == "http_error"


def test_download_maps_url_errors_to_stable_code(tmp_path: Path) -> None:
    with pytest.raises(DownloadError) as caught:
        download_archive(
            _record_for(b"x"),
            tmp_path / "archive.zip",
            progress=lambda downloaded, expected: None,
            cancelled=lambda: False,
            opener=_FailingOpener(URLError(OSError("offline"))),
        )

    assert caught.value.code == "network_error"


def test_download_malformed_authority_raises_coded_untrusted_asset(
    local_server: ThreadingHTTPServer, tmp_path: Path
) -> None:
    opener = _LocalServerOpener(local_server, "/unused")
    malformed = replace(
        _record_for(b"x"),
        download_url=(
            "https://[invalid/gvoze32/fldailyedit/releases/download/latest/"
            "fldailyedit-fl2026-fast.zip"
        ),
    )

    with pytest.raises(DownloadError) as caught:
        download_archive(
            malformed,
            tmp_path / "archive.zip",
            progress=lambda downloaded, expected: None,
            cancelled=lambda: False,
            opener=opener,
        )

    assert caught.value.code == "untrusted_asset"
    assert opener.requested_urls == []
