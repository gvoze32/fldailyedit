from __future__ import annotations

import hashlib
import os
import shutil
import stat
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path, PurePosixPath
from uuid import uuid4
from typing import BinaryIO, Callable

from installer.catalog import ReleaseRecord


SAVE_NAME = "EDIT00000000"
MAX_SAVE_BYTES = 32 * 1024 * 1024
_CHUNK_BYTES = 64 * 1024
_WINDOWS = os.name == "nt"


class InstallStage(str, Enum):
    VALIDATING_DESTINATION = "validating_destination"
    VERIFYING_ARCHIVE = "verifying_archive"
    BACKING_UP = "backing_up"
    STAGING = "staging"
    REPLACING = "replacing"
    VERIFYING_INSTALL = "verifying_install"
    RESTORING = "restoring"


@dataclass(frozen=True, slots=True)
class InstallResult:
    target_path: Path
    backup_path: Path | None
    installed_sha256: str


class InstallError(OSError):
    def __init__(self, code: str, message: str, *, stage: InstallStage):
        super().__init__(message)
        self.code = code
        self.stage = stage

_REPARSE_POINT_ATTRIBUTE = getattr(
    stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400
)


@dataclass(frozen=True, slots=True)
class _TargetSnapshot:
    exists: bool
    device: int | None = None
    inode: int | None = None
    size: int | None = None
    sha256: str | None = None

@dataclass(frozen=True, slots=True)
class _RollbackOutcome:
    quarantine_path: Path
    backup_path: Path | None
    recovery_path: Path | None

    def artifact_detail(self) -> str:
        artifacts = [f"quarantine: {self.quarantine_path}"]
        if self.backup_path is not None:
            artifacts.append(f"backup: {self.backup_path}")
        if self.recovery_path is not None:
            artifacts.append(f"recovery copy: {self.recovery_path}")
        return "; ".join(artifacts)


def _is_reparse_status(path_status: os.stat_result) -> bool:
    return stat.S_ISLNK(path_status.st_mode) or bool(
        getattr(path_status, "st_file_attributes", 0)
        & _REPARSE_POINT_ATTRIBUTE
    )


def _optional_lstat(path: Path) -> os.stat_result | None:
    try:
        return path.lstat()
    except FileNotFoundError:
        return None


def _assert_safe_destination(
    destination: Path,
    target: Path,
    stage: InstallStage,
) -> None:
    try:
        destination_status = destination.lstat()
        if (
            _is_reparse_status(destination_status)
            or not stat.S_ISDIR(destination_status.st_mode)
            or destination.name.casefold() != "save"
        ):
            raise OSError("unsafe save destination")
        target_status = _optional_lstat(target)
        if target_status is not None and (
            _is_reparse_status(target_status)
            or not stat.S_ISREG(target_status.st_mode)
        ):
            raise OSError("unsafe save target")
        backup_directory = destination / "FLDailyEditBackups"
        backup_status = _optional_lstat(backup_directory)
        if backup_status is not None and (
            _is_reparse_status(backup_status)
            or not stat.S_ISDIR(backup_status.st_mode)
        ):
            raise OSError("unsafe backup directory")
    except InstallError:
        raise
    except OSError as error:
        raise InstallError(
            "invalid_destination",
            "The save destination contains an unsafe link or reparse point",
            stage=stage,
        ) from error


def _capture_target(target: Path, stage: InstallStage) -> _TargetSnapshot:
    try:
        path_status = target.lstat()
    except FileNotFoundError:
        return _TargetSnapshot(False)
    except OSError as error:
        raise InstallError(
            "target_changed",
            "The save file could not be inspected safely",
            stage=stage,
        ) from error
    if _is_reparse_status(path_status) or not stat.S_ISREG(path_status.st_mode):
        raise InstallError(
            "invalid_destination",
            "The save target is a link, reparse point, or non-file",
            stage=stage,
        )

    digest = hashlib.sha256()
    try:
        with target.open("rb") as source:
            before = os.fstat(source.fileno())
            while chunk := source.read(_CHUNK_BYTES):
                digest.update(chunk)
            after = os.fstat(source.fileno())
        final_path_status = target.lstat()
    except OSError as error:
        raise InstallError(
            "target_changed",
            "The save file changed while it was inspected",
            stage=stage,
        ) from error

    identities = {
        (path_status.st_dev, path_status.st_ino),
        (before.st_dev, before.st_ino),
        (after.st_dev, after.st_ino),
        (final_path_status.st_dev, final_path_status.st_ino),
    }
    if (
        len(identities) != 1
        or _is_reparse_status(final_path_status)
        or before.st_size != after.st_size
        or after.st_size != final_path_status.st_size
    ):
        raise InstallError(
            "target_changed",
            "The save file changed while it was inspected",
            stage=stage,
        )
    return _TargetSnapshot(
        True,
        after.st_dev,
        after.st_ino,
        after.st_size,
        digest.hexdigest(),
    )


def _assert_target_matches(
    target: Path,
    expected: _TargetSnapshot,
    stage: InstallStage,
) -> None:
    actual = _capture_target(target, stage)
    if actual != expected:
        raise InstallError(
            "target_changed",
            "The save file appeared, disappeared, or changed during installation",
            stage=stage,
        )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_stream(source: BinaryIO, destination: BinaryIO) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    while chunk := source.read(_CHUNK_BYTES):
        destination.write(chunk)
        size += len(chunk)
        digest.update(chunk)
    return size, digest.hexdigest()


def _hash_stream(source: BinaryIO, *, limit: int) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    while chunk := source.read(_CHUNK_BYTES):
        size += len(chunk)
        if size > limit:
            raise InstallError(
                "invalid_archive",
                "Archive member exceeds the maximum save size",
                stage=InstallStage.VERIFYING_ARCHIVE,
            )
        digest.update(chunk)
    return size, digest.hexdigest()


def _validated_member(archive: zipfile.ZipFile) -> zipfile.ZipInfo:
    members = archive.infolist()
    if len(members) != 1:
        raise InstallError(
            "invalid_archive",
            "Archive must contain exactly one member",
            stage=InstallStage.VERIFYING_ARCHIVE,
        )

    member = members[0]
    normalized = PurePosixPath(member.filename)
    mode = member.external_attr >> 16
    file_type = stat.S_IFMT(mode)
    if (
        member.is_dir()
        or normalized.is_absolute()
        or normalized.parts != (SAVE_NAME,)
        or member.filename != SAVE_NAME
        or member.orig_filename != SAVE_NAME
        or stat.S_ISLNK(mode)
        or file_type not in (0, stat.S_IFREG)
        or member.flag_bits & (0x1 | 0x40)
        or member.file_size > MAX_SAVE_BYTES
    ):
        raise InstallError(
            "invalid_archive",
            f"Archive must contain one regular unencrypted {SAVE_NAME} file",
            stage=InstallStage.VERIFYING_ARCHIVE,
        )
    return member


def _verify_open_archive(source: BinaryIO, record: ReleaseRecord) -> None:
    try:
        source.seek(0)
        archive_size = os.fstat(source.fileno()).st_size
    except OSError as error:
        raise InstallError(
            "invalid_archive",
            "Archive cannot be read",
            stage=InstallStage.VERIFYING_ARCHIVE,
        ) from error
    if archive_size != record.archive_size:
        raise InstallError(
            "archive_size_mismatch",
            "Archive size does not match the release catalog",
            stage=InstallStage.VERIFYING_ARCHIVE,
        )

    digest = hashlib.sha256()
    try:
        while chunk := source.read(_CHUNK_BYTES):
            digest.update(chunk)
        source.seek(0)
    except OSError as error:
        raise InstallError(
            "invalid_archive",
            "Archive cannot be read",
            stage=InstallStage.VERIFYING_ARCHIVE,
        ) from error
    if digest.hexdigest() != record.archive_sha256:
        raise InstallError(
            "archive_sha256_mismatch",
            "Archive SHA-256 does not match the release catalog",
            stage=InstallStage.VERIFYING_ARCHIVE,
        )

    try:
        with zipfile.ZipFile(source) as archive:
            member = _validated_member(archive)
            with archive.open(member, "r") as member_source:
                save_size, save_sha256 = _hash_stream(
                    member_source, limit=MAX_SAVE_BYTES
                )
        source.seek(0)
    except InstallError:
        raise
    except NotImplementedError as error:
        raise InstallError(
            "invalid_archive",
            "Archive uses an unsupported ZIP feature",
            stage=InstallStage.VERIFYING_ARCHIVE,
        ) from error
    except (OSError, EOFError, RuntimeError, zipfile.BadZipFile) as error:
        raise InstallError(
            "invalid_archive",
            "Archive is not a readable ZIP file",
            stage=InstallStage.VERIFYING_ARCHIVE,
        ) from error

    if save_size != record.save_size:
        raise InstallError(
            "save_size_mismatch",
            "Save size does not match the release catalog",
            stage=InstallStage.VERIFYING_ARCHIVE,
        )
    if save_sha256 != record.save_sha256:
        raise InstallError(
            "save_sha256_mismatch",
            "Save SHA-256 does not match the release catalog",
            stage=InstallStage.VERIFYING_ARCHIVE,
        )


def _verify_archive(archive_path: Path, record: ReleaseRecord) -> None:
    try:
        with archive_path.open("rb") as source:
            _verify_open_archive(source, record)
    except InstallError:
        raise
    except OSError as error:
        raise InstallError(
            "invalid_archive",
            "Archive cannot be read",
            stage=InstallStage.VERIFYING_ARCHIVE,
        ) from error


def _raise_if_cancelled(cancelled: Callable[[], bool], stage: InstallStage) -> None:
    if cancelled():
        raise InstallError("cancelled", "Installation was cancelled", stage=stage)


def _backup_target(
    target: Path,
    destination: Path,
    timestamp: datetime,
    expected: _TargetSnapshot,
) -> tuple[Path, int, str]:
    backup_path: Path | None = None
    created = False
    try:
        backup_directory = destination / "FLDailyEditBackups"
        backup_directory.mkdir(exist_ok=True)
        utc_timestamp = timestamp.astimezone(timezone.utc).strftime(
            "%Y%m%dT%H%M%SZ"
        )
        backup_path = backup_directory / f"{SAVE_NAME}.{utc_timestamp}.bak"
        with target.open("rb") as source, backup_path.open("xb") as backup:
            created = True
            source_before = os.fstat(source.fileno())
            if (
                not expected.exists
                or (source_before.st_dev, source_before.st_ino)
                != (expected.device, expected.inode)
            ):
                raise InstallError(
                    "target_changed",
                    "The save file changed before backup",
                    stage=InstallStage.BACKING_UP,
                )
            copied_size, copied_sha256 = _copy_stream(source, backup)
            source_after = os.fstat(source.fileno())
            if (
                (source_after.st_dev, source_after.st_ino)
                != (expected.device, expected.inode)
                or source_after.st_size != expected.size
                or copied_size != expected.size
                or copied_sha256 != expected.sha256
            ):
                raise InstallError(
                    "target_changed",
                    "The save file changed during backup",
                    stage=InstallStage.BACKING_UP,
                )
            backup.flush()
            os.fsync(backup.fileno())
        if backup_path.stat().st_size != expected.size:
            raise OSError("backup size verification failed")
        if _file_sha256(backup_path) != expected.sha256:
            raise OSError("backup SHA-256 verification failed")
    except InstallError:
        if created and backup_path is not None:
            try:
                backup_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise
    except (OSError, ValueError) as error:
        if created and backup_path is not None:
            try:
                backup_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise InstallError(
            "backup_failed",
            "Could not create and verify the backup",
            stage=InstallStage.BACKING_UP,
        ) from error
    return backup_path, copied_size, copied_sha256


def _stage_archive(
    archive_path: Path,
    destination: Path,
    record: ReleaseRecord,
) -> Path:
    temporary_path: Path | None = None
    completed = False
    try:
        with archive_path.open("rb") as archive_source:
            _verify_open_archive(archive_source, record)
            archive_source.seek(0)
            with tempfile.NamedTemporaryFile(
                delete=False,
                dir=destination,
                prefix=".fldailyedit-",
                suffix=".tmp",
            ) as staged:
                temporary_path = Path(staged.name)
                with zipfile.ZipFile(archive_source) as archive:
                    member = _validated_member(archive)
                    with archive.open(member, "r") as source:
                        save_size, save_sha256 = _copy_stream(source, staged)
                if save_size != record.save_size or save_size > MAX_SAVE_BYTES:
                    raise OSError("staged save size verification failed")
                if save_sha256 != record.save_sha256:
                    raise OSError("staged save SHA-256 verification failed")
                staged.flush()
                os.fsync(staged.fileno())
        if temporary_path.stat().st_size != record.save_size:
            raise OSError("staged save size verification failed")
        if _file_sha256(temporary_path) != record.save_sha256:
            raise OSError("staged save SHA-256 verification failed")
        completed = True
        return temporary_path
    except InstallError:
        raise
    except NotImplementedError as error:
        raise InstallError(
            "staging_failed",
            "Could not stage and verify the new save",
            stage=InstallStage.STAGING,
        ) from error
    except (OSError, EOFError, RuntimeError, ValueError, zipfile.BadZipFile) as error:
        raise InstallError(
            "staging_failed",
            "Could not stage and verify the new save",
            stage=InstallStage.STAGING,
        ) from error
    finally:
        if not completed and temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


def _move_no_replace(source: Path, target: Path) -> bool:
    source_status = source.lstat()
    if (
        _is_reparse_status(source_status)
        or not stat.S_ISREG(source_status.st_mode)
    ):
        raise OSError("no-replace source is not a regular file")
    if _WINDOWS:
        os.rename(source, target)
        return False
    os.link(source, target, follow_symlinks=False)
    return True


def _quarantine_target(target: Path) -> Path:
    quarantine = (
        target.parent
        / f".fldailyedit-quarantine-{uuid4().hex}.save"
    )
    os.rename(target, quarantine)
    return quarantine


def _assert_rollback_ownership(
    target: Path,
    installed_snapshot: _TargetSnapshot,
    backup_path: Path | None,
) -> None:
    try:
        _assert_target_matches(
            target, installed_snapshot, InstallStage.RESTORING
        )
    except InstallError as error:
        backup_detail = (
            f" The verified backup remains at {backup_path}."
            if backup_path is not None
            else ""
        )
        raise InstallError(
            "recovery_failed",
            "The save file changed after installation; manual recovery is "
            f"required and the concurrent file was preserved.{backup_detail}",
            stage=InstallStage.RESTORING,
        ) from error


def _restore_after_failed_commit(
    target: Path,
    destination: Path,
    backup_path: Path | None,
    backup_size: int | None,
    backup_sha256: str | None,
    installed_snapshot: _TargetSnapshot,
    progress: Callable[[InstallStage], None],
) -> _RollbackOutcome:
    progress(InstallStage.RESTORING)
    recovery_path: Path | None = None
    quarantine_path: Path | None = None
    preserve_recovery = False
    try:
        if backup_path is not None:
            if backup_size is None or backup_sha256 is None:
                raise OSError("verified backup metadata is unavailable")
            with tempfile.NamedTemporaryFile(
                delete=False,
                dir=destination,
                prefix=".fldailyedit-",
                suffix=".tmp",
            ) as recovery:
                recovery_path = Path(recovery.name)
                with backup_path.open("rb") as source:
                    copied_size, copied_sha256 = _copy_stream(source, recovery)
                if copied_size != backup_size or copied_sha256 != backup_sha256:
                    raise OSError("backup changed before recovery")
                recovery.flush()
                os.fsync(recovery.fileno())
            if recovery_path.stat().st_size != backup_size:
                raise OSError("recovery copy size verification failed")
            if _file_sha256(recovery_path) != backup_sha256:
                raise OSError("recovery copy SHA-256 verification failed")

        # Diagnostics only: the quarantine move below is the safety boundary.
        _assert_rollback_ownership(target, installed_snapshot, backup_path)
        _assert_rollback_ownership(target, installed_snapshot, backup_path)
        quarantine_path = _quarantine_target(target)

        try:
            quarantined_snapshot = _capture_target(
                quarantine_path, InstallStage.RESTORING
            )
        except InstallError as error:
            preserve_recovery = recovery_path is not None
            raise InstallError(
                "recovery_failed",
                "The quarantined save could not be verified and was not "
                "republished; manual recovery is required. "
                f"quarantine: {quarantine_path}; backup: {backup_path}; "
                f"recovery copy: {recovery_path}",
                stage=InstallStage.RESTORING,
            ) from error

        if quarantined_snapshot != installed_snapshot:
            preserve_recovery = recovery_path is not None
            try:
                source_retained = _move_no_replace(quarantine_path, target)
            except OSError:
                source_retained = True
            if not source_retained:
                quarantine_path = None
            raise InstallError(
                "recovery_failed",
                "A concurrent save was quarantined and preserved; manual "
                f"recovery is required. Target: {target}; "
                f"quarantine: {quarantine_path}; backup: {backup_path}; "
                f"recovery copy: {recovery_path}",
                stage=InstallStage.RESTORING,
            )

        if backup_path is None:
            if _optional_lstat(target) is not None:
                raise InstallError(
                    "recovery_failed",
                    "A concurrent save appeared during rollback and was "
                    f"preserved at {target}; manual recovery is required. "
                    f"quarantine: {quarantine_path}",
                    stage=InstallStage.RESTORING,
                )
            return _RollbackOutcome(quarantine_path, None, None)

        if recovery_path is None:
            raise OSError("verified recovery copy is unavailable")
        recovery_snapshot = _capture_target(
            recovery_path, InstallStage.RESTORING
        )
        try:
            source_retained = _move_no_replace(recovery_path, target)
        except OSError as error:
            preserve_recovery = True
            raise InstallError(
                "recovery_failed",
                "The original save could not be restored without overwriting "
                f"a concurrent file; manual recovery is required. Target: "
                f"{target}; quarantine: {quarantine_path}; backup: "
                f"{backup_path}; recovery copy: {recovery_path}",
                stage=InstallStage.RESTORING,
            ) from error
        if source_retained:
            preserve_recovery = True
        else:
            recovery_path = None
        try:
            _assert_target_matches(
                target, recovery_snapshot, InstallStage.RESTORING
            )
        except InstallError as error:
            preserve_recovery = recovery_path is not None
            raise InstallError(
                "recovery_failed",
                "The restored target changed after no-clobber publication; "
                f"manual recovery is required. Target: {target}; quarantine: "
                f"{quarantine_path}; backup: {backup_path}; recovery copy: "
                f"{recovery_path}",
                stage=InstallStage.RESTORING,
            ) from error
        return _RollbackOutcome(
            quarantine_path, backup_path, recovery_path
        )
    except InstallError as error:
        preserve_recovery = (
            preserve_recovery
            or quarantine_path is not None
            or error.code == "recovery_failed"
        )
        raise InstallError(
            "recovery_failed",
            f"{error} Retained artifacts — target: {target}; quarantine: "
            f"{quarantine_path}; backup: {backup_path}; recovery copy: "
            f"{recovery_path}",
            stage=InstallStage.RESTORING,
        ) from error
    except (OSError, ValueError) as error:
        preserve_recovery = preserve_recovery or quarantine_path is not None
        raise InstallError(
            "recovery_failed",
            "Installation failed and safe recovery could not be completed; "
            f"manual recovery is required. Target: {target}; quarantine: "
            f"{quarantine_path}; backup: {backup_path}; recovery copy: "
            f"{recovery_path}",
            stage=InstallStage.RESTORING,
        ) from error
    finally:
        if recovery_path is not None and not preserve_recovery:
            try:
                recovery_path.unlink(missing_ok=True)
            except OSError:
                pass


def install_archive(
    archive_path: Path,
    destination: Path,
    record: ReleaseRecord,
    *,
    now: Callable[[], datetime],
    progress: Callable[[InstallStage], None],
    cancelled: Callable[[], bool],
) -> InstallResult:
    target = destination / SAVE_NAME
    backup_path: Path | None = None
    backup_size: int | None = None
    backup_sha256: str | None = None
    staged_path: Path | None = None
    commit_started = False

    progress(InstallStage.VALIDATING_DESTINATION)
    _raise_if_cancelled(cancelled, InstallStage.VALIDATING_DESTINATION)
    try:
        _assert_safe_destination(
            destination, target, InstallStage.VALIDATING_DESTINATION
        )
        target_snapshot = _capture_target(
            target, InstallStage.VALIDATING_DESTINATION
        )
        existing_target_size = target_snapshot.size or 0
        required_space = record.archive_size + record.save_size + existing_target_size
        if shutil.disk_usage(destination).free < required_space:
            raise InstallError(
                "insufficient_space",
                "Not enough free space for backup and installation",
                stage=InstallStage.VALIDATING_DESTINATION,
            )
    except InstallError:
        raise
    except OSError as error:
        raise InstallError(
            "invalid_destination",
            "The save destination is unavailable",
            stage=InstallStage.VALIDATING_DESTINATION,
        ) from error

    progress(InstallStage.VERIFYING_ARCHIVE)
    _verify_archive(archive_path, record)
    _raise_if_cancelled(cancelled, InstallStage.VERIFYING_ARCHIVE)

    try:
        if target_snapshot.exists:
            progress(InstallStage.BACKING_UP)
            _assert_safe_destination(destination, target, InstallStage.BACKING_UP)
            _assert_target_matches(
                target, target_snapshot, InstallStage.BACKING_UP
            )
            backup_path, backup_size, backup_sha256 = _backup_target(
                target, destination, now(), target_snapshot
            )
            _raise_if_cancelled(cancelled, InstallStage.BACKING_UP)

        progress(InstallStage.STAGING)
        _assert_safe_destination(destination, target, InstallStage.STAGING)
        _assert_target_matches(target, target_snapshot, InstallStage.STAGING)
        staged_path = _stage_archive(archive_path, destination, record)
        staged_snapshot = _capture_target(staged_path, InstallStage.STAGING)
        _raise_if_cancelled(cancelled, InstallStage.STAGING)

        progress(InstallStage.REPLACING)
        _assert_safe_destination(destination, target, InstallStage.REPLACING)
        _assert_target_matches(target, target_snapshot, InstallStage.REPLACING)
        _assert_target_matches(
            staged_path, staged_snapshot, InstallStage.REPLACING
        )
        commit_started = True
        try:
            os.replace(staged_path, target)
            staged_path = None
            installed_snapshot = staged_snapshot
        except PermissionError as error:
            raise InstallError(
                "target_locked",
                "The save file is locked by another process",
                stage=InstallStage.REPLACING,
            ) from error
        except OSError as error:
            raise InstallError(
                "replace_failed",
                "Could not replace the save file",
                stage=InstallStage.REPLACING,
            ) from error

        progress(InstallStage.VERIFYING_INSTALL)
        try:
            installed_size = target.stat().st_size
            installed_sha256 = _file_sha256(target)
            _assert_target_matches(
                target, installed_snapshot, InstallStage.VERIFYING_INSTALL
            )
        except OSError as error:
            verification_error: BaseException = error
        else:
            if (
                installed_size == record.save_size
                and installed_sha256 == record.save_sha256
            ):
                return InstallResult(target, backup_path, installed_sha256)
            verification_error = OSError("installed save verification failed")

        try:
            rollback = _restore_after_failed_commit(
                target,
                destination,
                backup_path,
                backup_size,
                backup_sha256,
                installed_snapshot,
                progress,
            )
        except InstallError:
            raise
        raise InstallError(
            "install_verification_failed",
            "The installed save failed final verification. Retained artifacts "
            f"— {rollback.artifact_detail()}",
            stage=InstallStage.VERIFYING_INSTALL,
        ) from verification_error
    finally:
        if staged_path is not None:
            try:
                staged_path.unlink(missing_ok=True)
            except OSError:
                pass
