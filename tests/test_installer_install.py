from __future__ import annotations

import hashlib
import io
import os
import stat
import struct
import zipfile
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import pytest

import installer.install as install_module
from installer.catalog import Channel, ReleaseRecord
from installer.install import InstallError, InstallStage, install_archive


SAVE_NAME = "EDIT00000000"
NOW = datetime(2026, 8, 6, 12, 34, 56, tzinfo=timezone.utc)


def _zip_bytes(
    members: list[tuple[str | zipfile.ZipInfo, bytes]],
) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in members:
            archive.writestr(name, content)
    return output.getvalue()


def _encrypted_zip_bytes(content: bytes) -> bytes:
    data = bytearray(_zip_bytes([(SAVE_NAME, content)]))
    local = data.index(b"PK\x03\x04")
    central = data.index(b"PK\x01\x02")
    local_flags = struct.unpack_from("<H", data, local + 6)[0]
    central_flags = struct.unpack_from("<H", data, central + 8)[0]
    struct.pack_into("<H", data, local + 6, local_flags | 1)
    struct.pack_into("<H", data, central + 8, central_flags | 1)
    return bytes(data)

def _nul_suffixed_member_zip_bytes(content: bytes) -> bytes:
    raw_name = f"{SAVE_NAME}Xevil".encode()
    nul_name = f"{SAVE_NAME}\x00evil".encode()
    data = _zip_bytes([(raw_name.decode(), content)])
    assert data.count(raw_name) == 2
    return data.replace(raw_name, nul_name)


def _unsupported_zip_bytes(content: bytes, *, strong_encryption: bool) -> bytes:
    data = bytearray(_zip_bytes([(SAVE_NAME, content)]))
    local = data.index(b"PK\x03\x04")
    central = data.index(b"PK\x01\x02")
    if strong_encryption:
        local_flags = struct.unpack_from("<H", data, local + 6)[0]
        central_flags = struct.unpack_from("<H", data, central + 8)[0]
        struct.pack_into("<H", data, local + 6, local_flags | 0x40)
        struct.pack_into("<H", data, central + 8, central_flags | 0x40)
    else:
        struct.pack_into("<H", data, local + 8, 99)
        struct.pack_into("<H", data, central + 10, 99)
    return bytes(data)

def _zip_bytes_with_changed_metadata(data: bytes) -> bytes:
    changed = bytearray(data)
    local = changed.index(b"PK\x03\x04")
    central = changed.index(b"PK\x01\x02")
    local_time = struct.unpack_from("<H", changed, local + 10)[0]
    central_time = struct.unpack_from("<H", changed, central + 12)[0]
    struct.pack_into("<H", changed, local + 10, local_time ^ 1)
    struct.pack_into("<H", changed, central + 12, central_time ^ 1)
    return bytes(changed)


def _record(archive_bytes: bytes, save_bytes: bytes) -> ReleaseRecord:
    return ReleaseRecord(
        target_id="fl26-u2.2-national-squads",
        target_name="Football Life 2026",
        channel=Channel.FAST,
        generated_at=NOW,
        asset_name="fldailyedit-fl2026-fast.zip",
        download_url="https://github.com/example/archive.zip",
        archive_size=len(archive_bytes),
        archive_sha256=hashlib.sha256(archive_bytes).hexdigest(),
        save_size=len(save_bytes),
        save_sha256=hashlib.sha256(save_bytes).hexdigest(),
    )


def _setup(
    tmp_path: Path,
    save_bytes: bytes = b"verified new save",
    *,
    archive_bytes: bytes | None = None,
) -> tuple[Path, Path, ReleaseRecord]:
    destination = tmp_path / "save"
    destination.mkdir()
    if archive_bytes is None:
        archive_bytes = _zip_bytes([(SAVE_NAME, save_bytes)])
    archive_path = tmp_path / "release.zip"
    archive_path.write_bytes(archive_bytes)
    return archive_path, destination, _record(archive_bytes, save_bytes)


def _install(
    archive_path: Path,
    destination: Path,
    record: ReleaseRecord,
    *,
    progress: Callable[[InstallStage], None] = lambda stage: None,
    cancelled: Callable[[], bool] = lambda: False,
):
    return install_archive(
        archive_path,
        destination,
        record,
        now=lambda: NOW,
        progress=progress,
        cancelled=cancelled,
    )


def _assert_invalid_archive(
    tmp_path: Path,
    archive_bytes: bytes,
    save_bytes: bytes = b"save",
) -> None:
    archive_path, destination, record = _setup(
        tmp_path, save_bytes, archive_bytes=archive_bytes
    )

    with pytest.raises(InstallError) as caught:
        _install(archive_path, destination, record)

    assert caught.value.code == "invalid_archive"
    assert caught.value.stage is InstallStage.VERIFYING_ARCHIVE
    assert not (destination / SAVE_NAME).exists()


def test_rejects_empty_archive(tmp_path: Path) -> None:
    _assert_invalid_archive(tmp_path, _zip_bytes([]))


def test_rejects_archive_with_multiple_members(tmp_path: Path) -> None:
    _assert_invalid_archive(
        tmp_path,
        _zip_bytes([(SAVE_NAME, b"save"), ("extra", b"extra")]),
    )


@pytest.mark.parametrize(
    "member_name",
    [
        "nested/EDIT00000000",
        "../EDIT00000000",
        "/EDIT00000000",
        "EDIT00000000/child",
        "other-save",
    ],
)
def test_rejects_member_not_named_exactly_edit_file(
    tmp_path: Path, member_name: str
) -> None:
    _assert_invalid_archive(tmp_path, _zip_bytes([(member_name, b"save")]))


def test_rejects_directory_member(tmp_path: Path) -> None:
    directory = zipfile.ZipInfo(f"{SAVE_NAME}/")
    directory.external_attr = (stat.S_IFDIR | 0o755) << 16
    _assert_invalid_archive(tmp_path, _zip_bytes([(directory, b"")]), b"")


def test_rejects_posix_symlink_member(tmp_path: Path) -> None:
    symlink = zipfile.ZipInfo(SAVE_NAME)
    symlink.create_system = 3
    symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
    _assert_invalid_archive(tmp_path, _zip_bytes([(symlink, b"elsewhere")]))


def test_rejects_encrypted_member(tmp_path: Path) -> None:
    content = b"save"
    _assert_invalid_archive(tmp_path, _encrypted_zip_bytes(content), content)

def test_rejects_raw_member_name_with_nul_suffix(tmp_path: Path) -> None:
    content = b"save"
    _assert_invalid_archive(tmp_path, _nul_suffixed_member_zip_bytes(content), content)


@pytest.mark.parametrize("strong_encryption", [False, True])
def test_maps_unsupported_zip_features_to_invalid_archive(
    tmp_path: Path, strong_encryption: bool
) -> None:
    content = b"save"
    _assert_invalid_archive(
        tmp_path,
        _unsupported_zip_bytes(content, strong_encryption=strong_encryption),
        content,
    )


def test_rejects_member_exceeding_maximum_extracted_size(tmp_path: Path) -> None:
    content = b"x" * (install_module.MAX_SAVE_BYTES + 1)
    _assert_invalid_archive(tmp_path, _zip_bytes([(SAVE_NAME, content)]), content)


def test_rejects_wrong_archive_size_before_transaction(tmp_path: Path) -> None:
    archive_path, destination, record = _setup(tmp_path)
    record = replace(record, archive_size=record.archive_size + 1)

    with pytest.raises(InstallError) as caught:
        _install(archive_path, destination, record)

    assert caught.value.code == "archive_size_mismatch"
    assert caught.value.stage is InstallStage.VERIFYING_ARCHIVE


def test_rejects_wrong_archive_sha256_before_transaction(tmp_path: Path) -> None:
    archive_path, destination, record = _setup(tmp_path)
    record = replace(record, archive_sha256="0" * 64)

    with pytest.raises(InstallError) as caught:
        _install(archive_path, destination, record)

    assert caught.value.code == "archive_sha256_mismatch"
    assert caught.value.stage is InstallStage.VERIFYING_ARCHIVE


def test_rejects_wrong_save_size_before_transaction(tmp_path: Path) -> None:
    archive_path, destination, record = _setup(tmp_path)
    record = replace(record, save_size=record.save_size + 1)

    with pytest.raises(InstallError) as caught:
        _install(archive_path, destination, record)

    assert caught.value.code == "save_size_mismatch"
    assert caught.value.stage is InstallStage.VERIFYING_ARCHIVE


def test_rejects_wrong_save_sha256_before_transaction(tmp_path: Path) -> None:
    archive_path, destination, record = _setup(tmp_path)
    record = replace(record, save_sha256="0" * 64)

    with pytest.raises(InstallError) as caught:
        _install(archive_path, destination, record)

    assert caught.value.code == "save_sha256_mismatch"
    assert caught.value.stage is InstallStage.VERIFYING_ARCHIVE


def test_existing_target_is_backed_up_and_replaced_atomically(tmp_path: Path) -> None:
    new_save = b"new verified save"
    old_save = b"old original save"
    archive_path, destination, record = _setup(tmp_path, new_save)
    target = destination / SAVE_NAME
    target.write_bytes(old_save)
    stages: list[InstallStage] = []

    result = _install(archive_path, destination, record, progress=stages.append)

    backup = (
        destination
        / "FLDailyEditBackups"
        / "EDIT00000000.20260806T123456Z.bak"
    )
    assert result.target_path == target
    assert result.backup_path == backup
    assert result.installed_sha256 == hashlib.sha256(new_save).hexdigest()
    assert target.read_bytes() == new_save
    assert backup.read_bytes() == old_save
    assert list(destination.glob(".fldailyedit-*.tmp")) == []
    assert stages == [
        InstallStage.VALIDATING_DESTINATION,
        InstallStage.VERIFYING_ARCHIVE,
        InstallStage.BACKING_UP,
        InstallStage.STAGING,
        InstallStage.REPLACING,
        InstallStage.VERIFYING_INSTALL,
    ]


def test_missing_target_installs_without_backup(tmp_path: Path) -> None:
    new_save = b"new verified save"
    archive_path, destination, record = _setup(tmp_path, new_save)

    result = _install(archive_path, destination, record)

    assert result.backup_path is None
    assert result.target_path.read_bytes() == new_save
    assert not (destination / "FLDailyEditBackups").exists()
    assert list(destination.glob(".fldailyedit-*.tmp")) == []


def test_insufficient_space_fails_before_backup_and_preserves_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_path, destination, record = _setup(tmp_path)
    target = destination / SAVE_NAME
    target.write_bytes(b"original")
    fake_usage = install_module.shutil._ntuple_diskusage(100, 100, 0)
    monkeypatch.setattr(install_module.shutil, "disk_usage", lambda path: fake_usage)

    with pytest.raises(InstallError) as caught:
        _install(archive_path, destination, record)

    assert caught.value.code == "insufficient_space"
    assert caught.value.stage is InstallStage.VALIDATING_DESTINATION
    assert target.read_bytes() == b"original"
    assert not (destination / "FLDailyEditBackups").exists()


def test_cancellation_before_replacing_preserves_original_and_cleans_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_path, destination, record = _setup(tmp_path)
    target = destination / SAVE_NAME
    target.write_bytes(b"original")
    reached_staging = False
    replace_calls: list[tuple[object, object]] = []

    def progress(stage: InstallStage) -> None:
        nonlocal reached_staging
        if stage is InstallStage.STAGING:
            reached_staging = True

    def recording_replace(source: object, destination_path: object) -> None:
        replace_calls.append((source, destination_path))

    monkeypatch.setattr(install_module.os, "replace", recording_replace)

    with pytest.raises(InstallError) as caught:
        _install(
            archive_path,
            destination,
            record,
            progress=progress,
            cancelled=lambda: reached_staging,
        )

    assert caught.value.code == "cancelled"
    assert caught.value.stage is InstallStage.STAGING
    assert replace_calls == []
    assert target.read_bytes() == b"original"
    assert list(destination.glob(".fldailyedit-*.tmp")) == []


def test_cancellation_after_commit_starts_is_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_path, destination, record = _setup(tmp_path)
    committed = False
    real_replace = os.replace

    def committing_replace(source: object, destination_path: object) -> None:
        nonlocal committed
        real_replace(source, destination_path)
        committed = True

    monkeypatch.setattr(install_module.os, "replace", committing_replace)

    result = _install(
        archive_path,
        destination,
        record,
        cancelled=lambda: committed,
    )

    assert result.target_path.read_bytes() == b"verified new save"
    assert committed


def test_existing_deterministic_backup_is_never_overwritten_or_deleted(
    tmp_path: Path,
) -> None:
    archive_path, destination, record = _setup(tmp_path)
    target = destination / SAVE_NAME
    target.write_bytes(b"original")
    backup = (
        destination
        / "FLDailyEditBackups"
        / "EDIT00000000.20260806T123456Z.bak"
    )
    backup.parent.mkdir()
    backup.write_bytes(b"earlier verified backup")

    with pytest.raises(InstallError) as caught:
        _install(archive_path, destination, record)

    assert caught.value.code == "backup_failed"
    assert caught.value.stage is InstallStage.BACKING_UP
    assert backup.read_bytes() == b"earlier verified backup"
    assert target.read_bytes() == b"original"


def test_backup_copy_failure_preserves_original_and_cleans_partial_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_path, destination, record = _setup(tmp_path)
    target = destination / SAVE_NAME
    target.write_bytes(b"original")

    def failing_copy(source, destination_file) -> tuple[int, str]:
        destination_file.write(b"partial")
        raise OSError("copy failed")

    monkeypatch.setattr(install_module, "_copy_stream", failing_copy)

    with pytest.raises(InstallError) as caught:
        _install(archive_path, destination, record)

    assert caught.value.code == "backup_failed"
    assert caught.value.stage is InstallStage.BACKING_UP
    assert target.read_bytes() == b"original"
    backup_directory = destination / "FLDailyEditBackups"
    assert not backup_directory.exists() or list(backup_directory.iterdir()) == []


def test_backup_fsync_failure_preserves_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_path, destination, record = _setup(tmp_path)
    target = destination / SAVE_NAME
    target.write_bytes(b"original")
    monkeypatch.setattr(
        install_module.os, "fsync", lambda descriptor: (_ for _ in ()).throw(OSError("disk"))
    )

    with pytest.raises(InstallError) as caught:
        _install(archive_path, destination, record)

    assert caught.value.code == "backup_failed"
    assert caught.value.stage is InstallStage.BACKING_UP
    assert target.read_bytes() == b"original"


def test_staging_fsync_failure_preserves_original_and_verified_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_path, destination, record = _setup(tmp_path)
    target = destination / SAVE_NAME
    target.write_bytes(b"original")
    real_fsync = os.fsync
    calls = 0

    def fail_second_fsync(descriptor: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("stage fsync failed")
        real_fsync(descriptor)

    monkeypatch.setattr(install_module.os, "fsync", fail_second_fsync)

    with pytest.raises(InstallError) as caught:
        _install(archive_path, destination, record)

    assert caught.value.code == "staging_failed"
    assert caught.value.stage is InstallStage.STAGING
    assert target.read_bytes() == b"original"
    assert (
        destination
        / "FLDailyEditBackups"
        / "EDIT00000000.20260806T123456Z.bak"
    ).read_bytes() == b"original"
    assert list(destination.glob(".fldailyedit-*.tmp")) == []


def test_reauthenticates_archive_replaced_before_staging(
    tmp_path: Path,
) -> None:
    save_bytes = b"verified new save"
    archive_path, destination, record = _setup(tmp_path, save_bytes)
    target = destination / SAVE_NAME
    target.write_bytes(b"original")
    original_archive = archive_path.read_bytes()
    replacement = _zip_bytes_with_changed_metadata(original_archive)
    assert len(replacement) == record.archive_size
    assert hashlib.sha256(replacement).hexdigest() != record.archive_sha256

    def replace_archive_at_staging(stage: InstallStage) -> None:
        if stage is InstallStage.STAGING:
            archive_path.write_bytes(replacement)

    with pytest.raises(InstallError) as caught:
        _install(
            archive_path,
            destination,
            record,
            progress=replace_archive_at_staging,
        )

    assert caught.value.code == "archive_sha256_mismatch"
    assert caught.value.stage is InstallStage.VERIFYING_ARCHIVE
    assert target.read_bytes() == b"original"
    assert list(destination.glob(".fldailyedit-*.tmp")) == []


def test_invalid_archive_during_staging_never_leaks_temporary_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive_path, destination, record = _setup(tmp_path)
    target = destination / SAVE_NAME
    target.write_bytes(b"original")
    real_validated_member = install_module._validated_member
    validation_calls = 0

    def fail_staging_validation(archive: zipfile.ZipFile) -> zipfile.ZipInfo:
        nonlocal validation_calls
        validation_calls += 1
        if validation_calls == 3:
            raise InstallError(
                "invalid_archive",
                "staging validation failed",
                stage=InstallStage.VERIFYING_ARCHIVE,
            )
        return real_validated_member(archive)

    monkeypatch.setattr(
        install_module, "_validated_member", fail_staging_validation
    )

    with pytest.raises(InstallError) as caught:
        _install(archive_path, destination, record)

    assert caught.value.code == "invalid_archive"
    assert caught.value.stage is InstallStage.VERIFYING_ARCHIVE
    assert target.read_bytes() == b"original"
    assert list(destination.glob(".fldailyedit-*.tmp")) == []


def test_locked_target_maps_permission_error_and_preserves_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_path, destination, record = _setup(tmp_path)
    target = destination / SAVE_NAME
    target.write_bytes(b"original")

    def locked_replace(source: object, destination_path: object) -> None:
        raise PermissionError("used by another process")

    monkeypatch.setattr(install_module.os, "replace", locked_replace)

    with pytest.raises(InstallError) as caught:
        _install(archive_path, destination, record)

    assert caught.value.code == "target_locked"
    assert caught.value.stage is InstallStage.REPLACING
    assert target.read_bytes() == b"original"
    assert list(destination.glob(".fldailyedit-*.tmp")) == []


def test_backup_verification_failure_never_invokes_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_path, destination, record = _setup(tmp_path)
    target = destination / SAVE_NAME
    target.write_bytes(b"original")
    real_hash = install_module._file_sha256
    replace_calls: list[tuple[object, object]] = []

    def wrong_backup_hash(path: Path) -> str:
        if path.suffix == ".bak":
            return "0" * 64
        return real_hash(path)

    monkeypatch.setattr(install_module, "_file_sha256", wrong_backup_hash)
    monkeypatch.setattr(
        install_module.os,
        "replace",
        lambda source, destination_path: replace_calls.append((source, destination_path)),
    )

    with pytest.raises(InstallError) as caught:
        _install(archive_path, destination, record)

    assert caught.value.code == "backup_failed"
    assert caught.value.stage is InstallStage.BACKING_UP
    assert replace_calls == []
    assert target.read_bytes() == b"original"


def test_final_verification_mismatch_restores_verified_backup_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_path, destination, record = _setup(tmp_path)
    target = destination / SAVE_NAME
    original = b"original"
    target.write_bytes(original)
    real_hash = install_module._file_sha256
    target_hash_calls = 0
    replace_calls: list[tuple[Path, Path]] = []
    real_replace = os.replace

    def fail_first_target_verification(path: Path) -> str:
        nonlocal target_hash_calls
        if path == target:
            target_hash_calls += 1
            if target_hash_calls == 1:
                return "0" * 64
        return real_hash(path)

    def recording_replace(source: Path, destination_path: Path) -> None:
        replace_calls.append((Path(source), Path(destination_path)))
        real_replace(source, destination_path)

    monkeypatch.setattr(install_module, "_file_sha256", fail_first_target_verification)
    monkeypatch.setattr(install_module.os, "replace", recording_replace)

    with pytest.raises(InstallError) as caught:
        _install(archive_path, destination, record)

    assert caught.value.code == "install_verification_failed"
    assert caught.value.stage is InstallStage.VERIFYING_INSTALL
    assert target.read_bytes() == original
    assert len(replace_calls) == 1
    backup = (
        destination
        / "FLDailyEditBackups"
        / "EDIT00000000.20260806T123456Z.bak"
    )
    assert backup.read_bytes() == original
    recoveries = list(destination.glob(".fldailyedit-*.tmp"))
    quarantines = list(destination.glob(".fldailyedit-quarantine-*"))
    assert len(quarantines) == 1
    assert quarantines[0].read_bytes() == b"verified new save"
    detail = str(caught.value)
    assert str(backup) in detail
    assert str(quarantines[0]) in detail
    if install_module._WINDOWS:
        assert recoveries == []
    else:
        assert len(recoveries) == 1
        assert recoveries[0].read_bytes() == original
        assert str(recoveries[0]) in detail


def test_final_verification_mismatch_removes_new_invalid_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_path, destination, record = _setup(tmp_path)
    target = destination / SAVE_NAME
    real_hash = install_module._file_sha256

    def wrong_target_hash(path: Path) -> str:
        if path == target:
            return "0" * 64
        return real_hash(path)

    monkeypatch.setattr(install_module, "_file_sha256", wrong_target_hash)

    with pytest.raises(InstallError) as caught:
        _install(archive_path, destination, record)

    assert caught.value.code == "install_verification_failed"
    assert caught.value.stage is InstallStage.VERIFYING_INSTALL
    assert not target.exists()
    assert list(destination.glob(".fldailyedit-*.tmp")) == []
    quarantines = list(destination.glob(".fldailyedit-quarantine-*"))
    assert len(quarantines) == 1
    assert quarantines[0].read_bytes() == b"verified new save"
    assert str(quarantines[0]) in str(caught.value)


def test_restore_failure_retains_backup_and_reports_recovery_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_path, destination, record = _setup(tmp_path)
    target = destination / SAVE_NAME
    original = b"original"
    target.write_bytes(original)
    real_hash = install_module._file_sha256
    def wrong_installed_hash(path: Path) -> str:
        if path == target:
            return "0" * 64
        return real_hash(path)

    def fail_restore(source: Path, destination_path: Path) -> None:
        raise OSError("no-replace restore failed")

    monkeypatch.setattr(install_module, "_file_sha256", wrong_installed_hash)
    monkeypatch.setattr(install_module, "_move_no_replace", fail_restore)

    with pytest.raises(InstallError) as caught:
        _install(archive_path, destination, record)

    backup = (
        destination
        / "FLDailyEditBackups"
        / "EDIT00000000.20260806T123456Z.bak"
    )
    assert caught.value.code == "recovery_failed"
    assert caught.value.stage is InstallStage.RESTORING
    assert backup.read_bytes() == original
    assert not target.exists()
    quarantines = list(destination.glob(".fldailyedit-quarantine-*"))
    recoveries = list(destination.glob(".fldailyedit-*.tmp"))
    assert len(quarantines) == 1
    assert quarantines[0].read_bytes() == b"verified new save"
    assert len(recoveries) == 1
    assert recoveries[0].read_bytes() == original

def test_target_appearing_before_commit_is_not_overwritten(tmp_path: Path) -> None:
    archive_path, destination, record = _setup(tmp_path)
    target = destination / SAVE_NAME

    def create_target_at_commit(stage: InstallStage) -> None:
        if stage is InstallStage.REPLACING:
            target.write_bytes(b"created by another process")

    with pytest.raises(InstallError) as caught:
        _install(
            archive_path,
            destination,
            record,
            progress=create_target_at_commit,
        )

    assert caught.value.code == "target_changed"
    assert caught.value.stage is InstallStage.REPLACING
    assert target.read_bytes() == b"created by another process"
    assert list(destination.glob(".fldailyedit-*.tmp")) == []


def test_target_changed_after_backup_is_not_overwritten(tmp_path: Path) -> None:
    archive_path, destination, record = _setup(tmp_path)
    target = destination / SAVE_NAME
    target.write_bytes(b"original")

    def change_target_at_commit(stage: InstallStage) -> None:
        if stage is InstallStage.REPLACING:
            target.write_bytes(b"changed by another process")

    with pytest.raises(InstallError) as caught:
        _install(
            archive_path,
            destination,
            record,
            progress=change_target_at_commit,
        )

    assert caught.value.code == "target_changed"
    assert caught.value.stage is InstallStage.REPLACING
    assert target.read_bytes() == b"changed by another process"
    assert (
        destination
        / "FLDailyEditBackups"
        / "EDIT00000000.20260806T123456Z.bak"
    ).read_bytes() == b"original"
    assert list(destination.glob(".fldailyedit-*.tmp")) == []


def test_target_disappearing_after_backup_is_not_recreated(tmp_path: Path) -> None:
    archive_path, destination, record = _setup(tmp_path)
    target = destination / SAVE_NAME
    target.write_bytes(b"original")

    def remove_target_at_commit(stage: InstallStage) -> None:
        if stage is InstallStage.REPLACING:
            target.unlink()

    with pytest.raises(InstallError) as caught:
        _install(
            archive_path,
            destination,
            record,
            progress=remove_target_at_commit,
        )

    assert caught.value.code == "target_changed"
    assert caught.value.stage is InstallStage.REPLACING
    assert not target.exists()
    assert (
        destination
        / "FLDailyEditBackups"
        / "EDIT00000000.20260806T123456Z.bak"
    ).read_bytes() == b"original"
    assert list(destination.glob(".fldailyedit-*.tmp")) == []


def test_install_rejects_symlinked_destination_leaf(tmp_path: Path) -> None:
    archive_path, _unused, record = _setup(tmp_path)
    real_destination = tmp_path / "real" / "save"
    real_destination.mkdir(parents=True)
    linked_destination = tmp_path / "linked" / "save"
    linked_destination.parent.mkdir()
    linked_destination.symlink_to(real_destination, target_is_directory=True)

    with pytest.raises(InstallError) as caught:
        _install(archive_path, linked_destination, record)

    assert caught.value.code == "invalid_destination"
    assert caught.value.stage is InstallStage.VALIDATING_DESTINATION
    assert list(real_destination.iterdir()) == []


def test_install_rejects_symlinked_backup_directory(tmp_path: Path) -> None:
    archive_path, destination, record = _setup(tmp_path)
    target = destination / SAVE_NAME
    target.write_bytes(b"original")
    escaped = tmp_path / "escaped"
    escaped.mkdir()
    (destination / "FLDailyEditBackups").symlink_to(
        escaped, target_is_directory=True
    )

    with pytest.raises(InstallError) as caught:
        _install(archive_path, destination, record)

    assert caught.value.code == "invalid_destination"
    assert caught.value.stage is InstallStage.VALIDATING_DESTINATION
    assert target.read_bytes() == b"original"
    assert list(escaped.iterdir()) == []

def test_rollback_preserves_concurrent_target_when_original_existed(
    tmp_path: Path,
) -> None:
    archive_path, destination, record = _setup(tmp_path)
    target = destination / SAVE_NAME
    target.write_bytes(b"original")
    concurrent = b"changed after installer commit"

    def mutate_during_final_verification(stage: InstallStage) -> None:
        if stage is InstallStage.VERIFYING_INSTALL:
            target.write_bytes(concurrent)

    with pytest.raises(InstallError) as caught:
        _install(
            archive_path,
            destination,
            record,
            progress=mutate_during_final_verification,
        )

    backup = (
        destination
        / "FLDailyEditBackups"
        / "EDIT00000000.20260806T123456Z.bak"
    )
    assert caught.value.code == "recovery_failed"
    assert caught.value.stage is InstallStage.RESTORING
    assert "manual recovery" in str(caught.value).casefold()
    assert target.read_bytes() == concurrent
    assert backup.read_bytes() == b"original"
    recoveries = list(destination.glob(".fldailyedit-*.tmp"))
    assert len(recoveries) == 1
    assert recoveries[0].read_bytes() == b"original"
    assert str(backup) in str(caught.value)
    assert str(recoveries[0]) in str(caught.value)


def test_rollback_preserves_concurrent_target_when_original_was_absent(
    tmp_path: Path,
) -> None:
    archive_path, destination, record = _setup(tmp_path)
    target = destination / SAVE_NAME
    concurrent = b"created after installer commit"

    def mutate_during_final_verification(stage: InstallStage) -> None:
        if stage is InstallStage.VERIFYING_INSTALL:
            target.write_bytes(concurrent)

    with pytest.raises(InstallError) as caught:
        _install(
            archive_path,
            destination,
            record,
            progress=mutate_during_final_verification,
        )

    assert caught.value.code == "recovery_failed"
    assert caught.value.stage is InstallStage.RESTORING
    assert "manual recovery" in str(caught.value).casefold()
    assert target.read_bytes() == concurrent
    assert not (destination / "FLDailyEditBackups").exists()
    assert list(destination.glob(".fldailyedit-*.tmp")) == []

@pytest.mark.parametrize("windows_semantics", [False, True])
def test_no_replace_move_restores_only_to_an_absent_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    windows_semantics: bool,
) -> None:
    source = tmp_path / "recovery"
    target = tmp_path / SAVE_NAME
    source.write_bytes(b"original")
    real_rename = os.rename

    def windows_rename(source_path: Path, target_path: Path) -> None:
        if Path(target_path).exists():
            raise FileExistsError("target exists")
        real_rename(source_path, target_path)

    monkeypatch.setattr(install_module, "_WINDOWS", windows_semantics)
    if windows_semantics:
        monkeypatch.setattr(install_module.os, "rename", windows_rename)

    install_module._move_no_replace(source, target)

    assert source.exists() is (not windows_semantics)
    assert target.read_bytes() == b"original"


@pytest.mark.parametrize("windows_semantics", [False, True])
def test_no_replace_move_never_clobbers_an_existing_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    windows_semantics: bool,
) -> None:
    source = tmp_path / "recovery"
    target = tmp_path / SAVE_NAME
    source.write_bytes(b"original")
    target.write_bytes(b"concurrent")
    real_rename = os.rename

    def windows_rename(source_path: Path, target_path: Path) -> None:
        if Path(target_path).exists():
            raise FileExistsError("target exists")
        real_rename(source_path, target_path)

    monkeypatch.setattr(install_module, "_WINDOWS", windows_semantics)
    if windows_semantics:
        monkeypatch.setattr(install_module.os, "rename", windows_rename)

    with pytest.raises(FileExistsError):
        install_module._move_no_replace(source, target)

    assert source.read_bytes() == b"original"
    assert target.read_bytes() == b"concurrent"


def test_quarantine_preserves_target_replaced_after_last_absent_rollback_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive_path, destination, record = _setup(tmp_path)
    target = destination / SAVE_NAME
    concurrent = b"replacement after final ownership check"
    real_hash = install_module._file_sha256
    real_assert = install_module._assert_rollback_ownership
    ownership_checks = 0

    def wrong_installed_hash(path: Path) -> str:
        if path == target:
            return "0" * 64
        return real_hash(path)

    def replace_after_check(*args, **kwargs) -> None:
        nonlocal ownership_checks
        real_assert(*args, **kwargs)
        ownership_checks += 1
        if ownership_checks == 2:
            target.write_bytes(concurrent)

    monkeypatch.setattr(install_module, "_file_sha256", wrong_installed_hash)
    monkeypatch.setattr(
        install_module, "_assert_rollback_ownership", replace_after_check
    )

    with pytest.raises(InstallError) as caught:
        _install(archive_path, destination, record)

    quarantines = list(destination.glob(".fldailyedit-quarantine-*"))
    assert caught.value.code == "recovery_failed"
    assert target.exists() or quarantines
    surviving = target if target.exists() else quarantines[0]
    assert surviving.read_bytes() == concurrent
    assert list(destination.glob(".fldailyedit-*.tmp")) == []


def test_quarantine_preserves_target_replaced_after_last_backup_rollback_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive_path, destination, record = _setup(tmp_path)
    target = destination / SAVE_NAME
    target.write_bytes(b"original")
    concurrent = b"replacement after final ownership check"
    real_hash = install_module._file_sha256
    real_assert = install_module._assert_rollback_ownership
    ownership_checks = 0

    def wrong_installed_hash(path: Path) -> str:
        if path == target:
            return "0" * 64
        return real_hash(path)

    def replace_after_check(*args, **kwargs) -> None:
        nonlocal ownership_checks
        real_assert(*args, **kwargs)
        ownership_checks += 1
        if ownership_checks == 2:
            target.write_bytes(concurrent)

    monkeypatch.setattr(install_module, "_file_sha256", wrong_installed_hash)
    monkeypatch.setattr(
        install_module, "_assert_rollback_ownership", replace_after_check
    )

    with pytest.raises(InstallError) as caught:
        _install(archive_path, destination, record)

    backup = (
        destination
        / "FLDailyEditBackups"
        / "EDIT00000000.20260806T123456Z.bak"
    )
    quarantines = list(destination.glob(".fldailyedit-quarantine-*"))
    assert caught.value.code == "recovery_failed"
    assert target.exists() or quarantines
    surviving = target if target.exists() else quarantines[0]
    assert surviving.read_bytes() == concurrent
    assert backup.read_bytes() == b"original"

def test_posix_no_replace_retains_source_across_destination_race(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "recovery"
    target = tmp_path / SAVE_NAME
    source.write_bytes(b"original")
    real_link = os.link
    observed_follow_symlinks: list[bool | None] = []

    def race_after_link(
        source_path: Path,
        target_path: Path,
        *,
        follow_symlinks: bool | None = None,
    ) -> None:
        observed_follow_symlinks.append(follow_symlinks)
        real_link(
            source_path,
            target_path,
            follow_symlinks=bool(follow_symlinks),
        )
        Path(target_path).unlink()
        Path(target_path).write_bytes(b"concurrent")

    monkeypatch.setattr(install_module, "_WINDOWS", False)
    monkeypatch.setattr(install_module.os, "link", race_after_link)

    install_module._move_no_replace(source, target)

    assert observed_follow_symlinks == [False]
    assert source.read_bytes() == b"original"
    assert target.read_bytes() == b"concurrent"


def test_no_replace_rejects_symlink_source_without_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    escaped = tmp_path / "escaped"
    escaped.write_bytes(b"external")
    source = tmp_path / "recovery"
    source.symlink_to(escaped)
    target = tmp_path / SAVE_NAME
    monkeypatch.setattr(install_module, "_WINDOWS", False)

    with pytest.raises(OSError):
        install_module._move_no_replace(source, target)

    assert source.is_symlink()
    assert escaped.read_bytes() == b"external"
    assert not target.exists()


def test_unverified_symlink_quarantine_is_never_republished(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive_path, destination, record = _setup(tmp_path)
    target = destination / SAVE_NAME
    escaped = tmp_path / "escaped"
    escaped.write_bytes(b"external")
    real_hash = install_module._file_sha256
    real_capture = install_module._capture_target

    def wrong_installed_hash(path: Path) -> str:
        if path == target:
            return "0" * 64
        return real_hash(path)

    def inject_symlink(path: Path, stage: InstallStage):
        if path.name.startswith(".fldailyedit-quarantine-"):
            path.unlink()
            path.symlink_to(escaped)
            raise InstallError(
                "target_changed",
                "quarantine changed",
                stage=InstallStage.RESTORING,
            )
        return real_capture(path, stage)

    monkeypatch.setattr(install_module, "_file_sha256", wrong_installed_hash)
    monkeypatch.setattr(install_module, "_capture_target", inject_symlink)

    with pytest.raises(InstallError) as caught:
        _install(archive_path, destination, record)

    quarantines = list(destination.glob(".fldailyedit-quarantine-*"))
    assert caught.value.code == "recovery_failed"
    assert not target.exists()
    assert len(quarantines) == 1
    assert quarantines[0].is_symlink()
    assert str(quarantines[0]) in str(caught.value)
    assert escaped.read_bytes() == b"external"


def test_no_backup_concurrent_appearance_error_lists_retained_quarantine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive_path, destination, record = _setup(tmp_path)
    target = destination / SAVE_NAME
    concurrent = b"appeared after quarantine"
    real_hash = install_module._file_sha256
    real_quarantine = install_module._quarantine_target

    def wrong_installed_hash(path: Path) -> str:
        if path == target:
            return "0" * 64
        return real_hash(path)

    def quarantine_then_appear(path: Path) -> Path:
        quarantine = real_quarantine(path)
        target.write_bytes(concurrent)
        return quarantine

    monkeypatch.setattr(install_module, "_file_sha256", wrong_installed_hash)
    monkeypatch.setattr(
        install_module, "_quarantine_target", quarantine_then_appear
    )

    with pytest.raises(InstallError) as caught:
        _install(archive_path, destination, record)

    quarantines = list(destination.glob(".fldailyedit-quarantine-*"))
    assert caught.value.code == "recovery_failed"
    assert target.read_bytes() == concurrent
    assert len(quarantines) == 1
    assert str(target) in str(caught.value)
    assert str(quarantines[0]) in str(caught.value)


def test_repeated_failed_installs_leave_discoverable_quarantines(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive_path, destination, record = _setup(tmp_path)
    target = destination / SAVE_NAME
    real_hash = install_module._file_sha256

    def wrong_installed_hash(path: Path) -> str:
        if path == target:
            return "0" * 64
        return real_hash(path)

    monkeypatch.setattr(install_module, "_file_sha256", wrong_installed_hash)
    observed_errors: list[str] = []

    for _ in range(2):
        with pytest.raises(InstallError) as caught:
            _install(archive_path, destination, record)
        observed_errors.append(str(caught.value))

    quarantines = sorted(destination.glob(".fldailyedit-quarantine-*"))
    assert len(quarantines) == 2
    assert quarantines[0] != quarantines[1]
    assert all(
        any(str(quarantine) in detail for detail in observed_errors)
        for quarantine in quarantines
    )
