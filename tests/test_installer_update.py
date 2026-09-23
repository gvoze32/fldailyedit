from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import hashlib
import json
import zipfile

import pytest

from installer import (
    APP_INSTALLER_ASSET_NAME,
    APP_INSTALLER_URL,
    APP_UPDATE_MANIFEST_URL,
    __version__,
)
from installer.app import InstallerApplication
from installer.state import AppUpdateChecked, AppUpdateDownloaded, InstallerState, WizardStep
from installer.update import (
    AppUpdateError,
    AppUpdateManifest,
    cleanup_staged_app_update,
    download_app_update,
    fetch_app_update_manifest,
    is_app_update_available,
    parse_app_update_manifest,
    stage_app_update,
)
from installer.worker import InstallerWorker
from tools.build_installer_update_manifest import build_manifest


@dataclass
class _Response:
    payload: bytes

    def __post_init__(self) -> None:
        self.headers = {"Content-Length": str(len(self.payload))}
        self._offset = 0

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self.payload) - self._offset
        start = self._offset
        self._offset = min(self._offset + size, len(self.payload))
        return self.payload[start : self._offset]

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: object) -> None:
        pass


class _Opener:
    def __init__(self, routes: dict[str, bytes]):
        self.routes = routes
        self.calls: list[str] = []

    def open(self, url: str, *, timeout: float) -> _Response:
        self.calls.append(url)
        return _Response(self.routes[url])


def _manifest_payload(
    archive: bytes = b"installer archive",
    *,
    version: str = "0.3.0",
) -> bytes:
    return json.dumps(
        {
            "schema_version": 1,
            "version": version,
            "asset_name": APP_INSTALLER_ASSET_NAME,
            "download_url": APP_INSTALLER_URL,
            "archive_size": len(archive),
            "archive_sha256": hashlib.sha256(archive).hexdigest(),
        }
    ).encode("utf-8")


def _zip_payload(*names_and_contents: tuple[str, bytes]) -> bytes:
    output = BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in names_and_contents:
            archive.writestr(name, content)
    return output.getvalue()


def _manifest_for_archive(archive: bytes, *, version: str = "0.3.0") -> AppUpdateManifest:
    return parse_app_update_manifest(_manifest_payload(archive, version=version))


def test_manifest_is_strict_and_compares_versions() -> None:
    manifest = parse_app_update_manifest(_manifest_payload())

    assert manifest.version == "0.3.0"
    assert is_app_update_available(manifest, __version__) is True
    assert is_app_update_available(manifest, "0.3.0") is False

    document = json.loads(_manifest_payload())
    document["download_url"] = "https://example.com/installer.zip"
    with pytest.raises(AppUpdateError) as caught:
        parse_app_update_manifest(json.dumps(document).encode("utf-8"))
    assert caught.value.code == "untrusted_asset"


def test_fetch_manifest_uses_trusted_url_and_injected_opener() -> None:
    opener = _Opener({APP_UPDATE_MANIFEST_URL: _manifest_payload()})

    manifest = fetch_app_update_manifest(opener=opener)

    assert manifest.version == "0.3.0"
    assert opener.calls == [APP_UPDATE_MANIFEST_URL]


def test_download_verifies_checksum_and_removes_partial_file(tmp_path: Path) -> None:
    archive = b"verified installer bytes"
    manifest = _manifest_for_archive(archive)
    opener = _Opener({APP_INSTALLER_URL: archive})
    destination = tmp_path / "installer.zip"

    download_app_update(manifest, destination, opener=opener)
    assert destination.read_bytes() == archive

    bad_manifest = AppUpdateManifest(
        manifest.version,
        manifest.asset_name,
        manifest.download_url,
        manifest.archive_size,
        "0" * 64,
    )
    with pytest.raises(AppUpdateError) as caught:
        download_app_update(bad_manifest, tmp_path / "bad.zip", opener=opener)
    assert caught.value.code == "checksum_mismatch"
    assert not (tmp_path / "bad.zip").exists()


def test_stage_app_update_extracts_only_the_expected_executable(tmp_path: Path) -> None:
    executable = b"new executable"
    archive = _zip_payload(("FLDailyEditInstaller.exe", executable))
    manifest = _manifest_for_archive(archive)
    opener = _Opener({APP_INSTALLER_URL: archive})

    staged = stage_app_update(manifest, opener=opener)
    try:
        assert staged.name == "FLDailyEditInstaller.exe"
        assert staged.read_bytes() == executable
    finally:
        cleanup_staged_app_update(staged)
    assert not staged.parent.exists()

    invalid_archive = _zip_payload(
        ("FLDailyEditInstaller.exe", executable),
        ("unexpected.txt", b"not allowed"),
    )
    invalid_manifest = _manifest_for_archive(invalid_archive)
    invalid_opener = _Opener({APP_INSTALLER_URL: invalid_archive})
    with pytest.raises(AppUpdateError) as caught:
        stage_app_update(invalid_manifest, opener=invalid_opener)
    assert caught.value.code == "invalid_archive"


def test_manifest_generator_matches_downloaded_asset(tmp_path: Path) -> None:
    asset = tmp_path / APP_INSTALLER_ASSET_NAME
    asset.write_bytes(b"installer zip")
    output = tmp_path / "installer-update.json"

    build_manifest(asset, output, version="0.4.0")

    manifest = parse_app_update_manifest(output.read_bytes())
    assert manifest.version == "0.4.0"
    assert manifest.archive_size == asset.stat().st_size
    assert manifest.archive_sha256 == hashlib.sha256(asset.read_bytes()).hexdigest()


def test_worker_checks_and_stages_app_updates_without_blocking_ui(tmp_path: Path) -> None:
    archive = _zip_payload(("FLDailyEditInstaller.exe", b"new"))
    manifest = _manifest_for_archive(archive)
    staged = tmp_path / "FLDailyEditInstaller.exe"
    staged.write_bytes(b"new")
    worker = InstallerWorker(
        fetch_app_update=lambda: manifest,
        stage_app_update=lambda _manifest: staged,
    )
    try:
        assert worker.check_app_update() is True
        checked = worker.events.get(timeout=2)
        assert isinstance(checked, AppUpdateChecked)
        assert checked.available is True

        assert worker.download_app_update(manifest) is True
        downloaded = worker.events.get(timeout=2)
        assert isinstance(downloaded, AppUpdateDownloaded)
        assert downloaded.staged_executable == staged
    finally:
        worker.close()


class _Button:
    def __init__(self) -> None:
        self.options: dict[str, object] = {}

    def configure(self, **options: object) -> None:
        self.options.update(options)


class _Variable:
    def __init__(self) -> None:
        self.value = ""

    def set(self, value: str) -> None:
        self.value = value


class _Worker:
    def __init__(self) -> None:
        self.downloaded: list[AppUpdateManifest] = []

    def download_app_update(self, manifest: AppUpdateManifest) -> bool:
        self.downloaded.append(manifest)
        return True


def test_update_button_starts_download_for_available_manifest() -> None:
    manifest = _manifest_for_archive(b"archive")
    worker = _Worker()
    application = object.__new__(InstallerApplication)
    application.worker = worker
    application.controller = type(
        "Controller",
        (),
        {"state": InstallerState(step=WizardStep.UPDATE)},
    )()
    application._app_update_supported = True
    application._app_update_pending = False
    application._app_update_manifest = manifest
    application._app_update_status_var = _Variable()
    application._render_footer = lambda _state: None

    application._start_app_update_download()

    assert worker.downloaded == [manifest]
    assert application._app_update_pending is True
    assert manifest.version in application._app_update_status_var.value
