from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import cast
from urllib.error import HTTPError, URLError

import pytest

from installer import CATALOG_URL
from installer.catalog import CatalogError, Channel, TRUSTED_ASSET_NAMES, parse_catalog
from tools import build_release_asset
from tools.build_release_asset import main, merge_catalog, package_record


TARGET_ID = "fl26-u2.2-national-squads"
TARGET_NAME = "Football Life 2026 Update 2.2 + National Squads"
GENERATED_AT = datetime(2026, 8, 6, 3, 4, 5, 987654, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("channel", "asset_name"),
    [
        (Channel.FAST, "fldailyedit-fl2026-fast.zip"),
        (Channel.DEEP, "fldailyedit-fl2026-deep.zip"),
    ],
)
def test_package_record_writes_exact_archive_and_metadata(
    tmp_path: Path, channel: Channel, asset_name: str
) -> None:
    save_bytes = b"fake Football Life save\x00\x01"
    save_path = tmp_path / "EDIT00000000"
    save_path.write_bytes(save_bytes)

    archive_path, record_path = package_record(
        save_path,
        tmp_path / "release",
        target_id=TARGET_ID,
        target_name=TARGET_NAME,
        channel=channel,
        generated_at=GENERATED_AT,
    )

    archive_bytes = archive_path.read_bytes()
    assert archive_path.name == asset_name
    assert record_path.name == "record.json"
    with zipfile.ZipFile(archive_path) as archive:
        assert archive.namelist() == ["EDIT00000000"]
        member = archive.infolist()[0]
        assert stat.S_IFMT(member.external_attr >> 16) == stat.S_IFREG
        assert stat.S_IMODE(member.external_attr >> 16) == 0o644
        assert member.compress_type == zipfile.ZIP_DEFLATED
        assert member.date_time == (2026, 8, 6, 3, 4, 4)
        assert archive.read(member) == save_bytes

    assert json.loads(record_path.read_bytes()) == {
        "archive_sha256": hashlib.sha256(archive_bytes).hexdigest(),
        "archive_size": len(archive_bytes),
        "asset_name": asset_name,
        "channel": channel.value,
        "download_url": (
            "https://github.com/gvoze32/fldailyedit/releases/download/latest/"
            f"{asset_name}"
        ),
        "generated_at": "2026-08-06T03:04:05Z",
        "save_sha256": hashlib.sha256(save_bytes).hexdigest(),
        "save_size": len(save_bytes),
        "target_id": TARGET_ID,
        "target_name": TARGET_NAME,
    }
    assert record_path.read_bytes().endswith(b"\n")


def test_package_record_is_byte_deterministic(tmp_path: Path) -> None:
    save_path = tmp_path / "EDIT00000000"
    save_path.write_bytes(b"same input")
    output_dir = tmp_path / "release"

    first_archive, _ = package_record(
        save_path,
        output_dir,
        target_id=TARGET_ID,
        target_name=TARGET_NAME,
        channel=Channel.FAST,
        generated_at=GENERATED_AT,
    )
    first_bytes = first_archive.read_bytes()
    second_archive, _ = package_record(
        save_path,
        output_dir,
        target_id=TARGET_ID,
        target_name=TARGET_NAME,
        channel=Channel.FAST,
        generated_at=GENERATED_AT,
    )

    assert second_archive.read_bytes() == first_bytes


def test_package_record_rolls_back_pair_when_second_publish_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    save_path = tmp_path / "EDIT00000000"
    output_dir = tmp_path / "release"
    save_path.write_bytes(b"old consistent save")
    archive_path, record_path = package_record(
        save_path,
        output_dir,
        target_id=TARGET_ID,
        target_name=TARGET_NAME,
        channel=Channel.FAST,
        generated_at=GENERATED_AT,
    )
    old_archive = archive_path.read_bytes()
    old_record = record_path.read_bytes()
    save_path.write_bytes(b"new save that must not publish alone")
    real_replace = os.replace
    replace_calls = 0

    def fail_second_replace(source: str | Path, destination: str | Path) -> None:
        nonlocal replace_calls
        replace_calls += 1
        if replace_calls == 2:
            raise OSError("simulated record publication failure")
        real_replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_second_replace)

    with pytest.raises(OSError, match="record publication failure"):
        package_record(
            save_path,
            output_dir,
            target_id=TARGET_ID,
            target_name=TARGET_NAME,
            channel=Channel.FAST,
            generated_at=datetime(2026, 8, 6, 4, 5, 6, tzinfo=timezone.utc),
        )

    assert archive_path.read_bytes() == old_archive
    assert record_path.read_bytes() == old_record
    assert set(output_dir.iterdir()) == {archive_path, record_path}


def test_package_record_clamps_pre_1980_zip_timestamp(tmp_path: Path) -> None:
    save_path = tmp_path / "EDIT00000000"
    save_path.write_bytes(b"old save")

    archive_path, _ = package_record(
        save_path,
        tmp_path / "release",
        target_id=TARGET_ID,
        target_name=TARGET_NAME,
        channel=Channel.FAST,
        generated_at=datetime(1970, 1, 1, tzinfo=timezone.utc),
    )

    with zipfile.ZipFile(archive_path) as archive:
        assert archive.infolist()[0].date_time == (1980, 1, 1, 0, 0, 0)


@pytest.mark.parametrize(
    ("target_id", "channel"),
    [
        ("pes2021", Channel.FAST),
        (TARGET_ID, cast(Channel, "nightly")),
        (TARGET_ID, cast(Channel, "fast")),
    ],
)
def test_package_record_rejects_non_allowlisted_target_channel(
    tmp_path: Path, target_id: str, channel: Channel
) -> None:
    save_path = tmp_path / "EDIT00000000"
    save_path.write_bytes(b"not publishable")

    with pytest.raises(CatalogError) as caught:
        package_record(
            save_path,
            tmp_path / "release",
            target_id=target_id,
            target_name=TARGET_NAME,
            channel=channel,
            generated_at=GENERATED_AT,
        )

    assert caught.value.code == "untrusted_asset"
    assert not (tmp_path / "release").exists()


@pytest.mark.parametrize(
    ("save_bytes", "target_name"),
    [
        (b"", TARGET_NAME),
        (b"valid save", ""),
    ],
)
def test_package_record_rejects_inputs_that_cannot_form_a_valid_record(
    tmp_path: Path, save_bytes: bytes, target_name: str
) -> None:
    save_path = tmp_path / "EDIT00000000"
    save_path.write_bytes(save_bytes)

    with pytest.raises(CatalogError) as caught:
        package_record(
            save_path,
            tmp_path / "release",
            target_id=TARGET_ID,
            target_name=target_name,
            channel=Channel.FAST,
            generated_at=GENERATED_AT,
        )

    assert caught.value.code == "invalid_record"
    assert not (tmp_path / "release").exists()


def _record(
    channel: Channel,
    *,
    target_id: str = TARGET_ID,
    target_name: str = TARGET_NAME,
    generated_at: str = "2026-08-06T03:04:05Z",
    asset_name: str | None = None,
) -> dict[str, object]:
    if asset_name is None:
        asset_name = f"fldailyedit-fl2026-{channel.value}.zip"
    return {
        "target_id": target_id,
        "target_name": target_name,
        "channel": channel.value,
        "generated_at": generated_at,
        "asset_name": asset_name,
        "download_url": (
            f"https://github.com/gvoze32/fldailyedit/releases/download/latest/"
            f"{asset_name}"
        ),
        "archive_size": 123,
        "archive_sha256": "a" * 64,
        "save_size": 456,
        "save_sha256": "b" * 64,
    }


def _payload(value: object) -> bytes:
    return json.dumps(value).encode("utf-8")


def test_merge_catalog_initializes_schema_one_deterministically() -> None:
    record = _record(Channel.FAST)

    merged = merge_catalog(None, _payload(record))

    expected = {"schema_version": 1, "records": [record]}
    assert merged == (
        json.dumps(expected, sort_keys=True, indent=2).encode("utf-8") + b"\n"
    )
    assert merge_catalog(None, _payload(record)) == merged
    assert parse_catalog(merged).schema_version == 1


def test_merge_catalog_replaces_only_matching_tuple_and_sorts_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pes_asset = "fldailyedit-pes2021-deep.zip"
    monkeypatch.setitem(
        TRUSTED_ASSET_NAMES, ("pes2021", Channel.DEEP), pes_asset
    )
    old_fast = _record(Channel.FAST, generated_at="2026-08-01T00:00:00Z")
    deep = _record(Channel.DEEP, generated_at="2026-08-02T00:00:00Z")
    pes = _record(
        Channel.DEEP,
        target_id="pes2021",
        target_name="PES 2021",
        generated_at="2026-08-03T00:00:00Z",
        asset_name=pes_asset,
    )
    replacement = _record(Channel.FAST, generated_at="2026-08-06T03:04:05Z")
    existing = {"schema_version": 1, "records": [pes, old_fast, deep]}

    merged = merge_catalog(_payload(existing), _payload(replacement))
    document = json.loads(merged)

    assert document["records"] == [deep, replacement, pes]
    assert parse_catalog(merged).records[1].generated_at == datetime(
        2026, 8, 6, 3, 4, 5, tzinfo=timezone.utc
    )


@pytest.mark.parametrize(
    ("existing_payload", "record_payload"),
    [
        (b"not JSON", _payload(_record(Channel.FAST))),
        (_payload({"schema_version": 2, "records": []}), _payload(_record(Channel.FAST))),
        (None, b"not JSON"),
        (None, _payload({})),
    ],
)
def test_merge_catalog_fails_closed_on_malformed_inputs(
    existing_payload: bytes | None, record_payload: bytes
) -> None:
    with pytest.raises(CatalogError):
        merge_catalog(existing_payload, record_payload)


def test_package_cli_matches_documented_command(tmp_path: Path) -> None:
    save_path = tmp_path / "EDIT00000000"
    save_path.write_bytes(b"command save")
    output_dir = tmp_path / "release-payload"
    project_root = Path(__file__).resolve().parents[1]

    completed = subprocess.run(
        [
            sys.executable,
            "tools/build_release_asset.py",
            "package",
            "--save",
            str(save_path),
            "--output-dir",
            str(output_dir),
            "--target-id",
            TARGET_ID,
            "--target-name",
            TARGET_NAME,
            "--channel",
            "fast",
            "--generated-at",
            "2026-08-06T03:04:05Z",
        ],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert (output_dir / "fldailyedit-fl2026-fast.zip").is_file()
    assert (output_dir / "record.json").is_file()


class _Response:
    def __init__(self, payload: bytes):
        self._payload = payload

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._payload


class _Opener:
    def __init__(self, result: bytes | Exception):
        self._result = result

    def open(self, url: str, timeout: float) -> _Response:
        if isinstance(self._result, Exception):
            raise self._result
        return _Response(self._result)


def _patch_opener(
    monkeypatch: pytest.MonkeyPatch, result: bytes | Exception
) -> None:
    monkeypatch.setattr(
        build_release_asset, "build_opener", lambda *handlers: _Opener(result)
    )


def _merge_cli_args(record_path: Path, output_path: Path) -> list[str]:
    return [
        "merge",
        "--existing-url",
        CATALOG_URL,
        "--record",
        str(record_path),
        "--output",
        str(output_path),
    ]


def test_merge_cli_treats_http_404_as_first_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record_path = tmp_path / "record.json"
    record_path.write_bytes(_payload(_record(Channel.FAST)))
    output_path = tmp_path / "catalog.json"
    not_found = HTTPError(CATALOG_URL, 404, "Not Found", {}, None)
    _patch_opener(monkeypatch, not_found)

    assert main(_merge_cli_args(record_path, output_path)) == 0

    assert parse_catalog(output_path.read_bytes()).records[0].channel is Channel.FAST


@pytest.mark.parametrize(
    "failure",
    [
        HTTPError(CATALOG_URL, 500, "Server Error", {}, None),
        URLError("offline"),
        TimeoutError("timed out"),
    ],
)
def test_merge_cli_propagates_non_404_fetch_errors_without_overwriting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    record_path = tmp_path / "record.json"
    record_path.write_bytes(_payload(_record(Channel.FAST)))
    output_path = tmp_path / "catalog.json"
    output_path.write_bytes(b"keep me")
    _patch_opener(monkeypatch, failure)

    with pytest.raises(type(failure)):
        main(_merge_cli_args(record_path, output_path))

    assert output_path.read_bytes() == b"keep me"


def test_merge_cli_propagates_malformed_existing_catalog_without_overwriting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record_path = tmp_path / "record.json"
    record_path.write_bytes(_payload(_record(Channel.FAST)))
    output_path = tmp_path / "catalog.json"
    output_path.write_bytes(b"keep me")
    _patch_opener(monkeypatch, b"malformed catalog")

    with pytest.raises(CatalogError):
        main(_merge_cli_args(record_path, output_path))

    assert output_path.read_bytes() == b"keep me"
