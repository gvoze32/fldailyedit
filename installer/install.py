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
from typing import BinaryIO, Callable

from installer.catalog import ReleaseRecord


SAVE_NAME = "EDIT00000000"
MAX_SAVE_BYTES = 32 * 1024 * 1024
_CHUNK_BYTES = 64 * 1024


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
            copied_size, copied_sha256 = _copy_stream(source, backup)
            backup.flush()
            os.fsync(backup.fileno())
        if backup_path.stat().st_size != copied_size:
            raise OSError("backup size verification failed")
        if _file_sha256(backup_path) != copied_sha256:
            raise OSError("backup SHA-256 verification failed")
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


def _restore_after_failed_commit(
    target: Path,
    destination: Path,
    backup_path: Path | None,
    backup_size: int | None,
    backup_sha256: str | None,
    progress: Callable[[InstallStage], None],
) -> None:
    progress(InstallStage.RESTORING)
    recovery_path: Path | None = None
    try:
        if backup_path is None:
            target.unlink(missing_ok=True)
            return

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
        os.replace(recovery_path, target)
        recovery_path = None
    except (OSError, ValueError) as error:
        raise InstallError(
            "recovery_failed",
            "Installation failed and the original save could not be restored",
            stage=InstallStage.RESTORING,
        ) from error
    finally:
        if recovery_path is not None:
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
        if not destination.is_dir() or (target.exists() and not target.is_file()):
            raise OSError("destination is not a writable save directory")
        existing_target_size = target.stat().st_size if target.exists() else 0
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
        if target.exists():
            progress(InstallStage.BACKING_UP)
            backup_path, backup_size, backup_sha256 = _backup_target(
                target, destination, now()
            )
            _raise_if_cancelled(cancelled, InstallStage.BACKING_UP)

        progress(InstallStage.STAGING)
        staged_path = _stage_archive(archive_path, destination, record)
        _raise_if_cancelled(cancelled, InstallStage.STAGING)

        progress(InstallStage.REPLACING)
        commit_started = True
        try:
            os.replace(staged_path, target)
            staged_path = None
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
            _restore_after_failed_commit(
                target,
                destination,
                backup_path,
                backup_size,
                backup_sha256,
                progress,
            )
        except InstallError:
            raise
        raise InstallError(
            "install_verification_failed",
            "The installed save failed final verification",
            stage=InstallStage.VERIFYING_INSTALL,
        ) from verification_error
    finally:
        if staged_path is not None:
            try:
                staged_path.unlink(missing_ok=True)
            except OSError:
                pass
