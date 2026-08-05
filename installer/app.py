from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from queue import SimpleQueue
from tempfile import TemporaryDirectory
import threading
from typing import Callable

from installer.catalog import (
    Catalog,
    DownloadError,
    ReleaseRecord,
    download_archive as default_download_archive,
    fetch_catalog as default_fetch_catalog,
)
from installer.install import (
    InstallError,
    InstallResult,
    InstallStage,
    install_archive as default_install_archive,
)
from installer.paths import SaveLocation


class WizardStep(str, Enum):
    UPDATE = "update"
    SAVE = "save"
    REVIEW = "review"
    PROGRESS = "progress"
    RESULT = "result"


@dataclass(frozen=True, slots=True)
class InstallerState:
    step: WizardStep = WizardStep.UPDATE
    catalog: Catalog | None = None
    selected_record: ReleaseRecord | None = None
    locations: tuple[SaveLocation, ...] = ()
    selected_location: SaveLocation | None = None
    progress_stage: str | None = None
    progress_downloaded: int = 0
    progress_total: int = 0
    result: InstallResult | None = None
    error_title: str | None = None
    error_detail: str | None = None
    commit_started: bool = False


@dataclass(frozen=True, slots=True)
class CatalogLoaded:
    catalog: Catalog


@dataclass(frozen=True, slots=True)
class ProgressChanged:
    stage: str
    downloaded: int = 0
    total: int = 0
    commit_started: bool = False


@dataclass(frozen=True, slots=True)
class InstallCompleted:
    result: InstallResult


@dataclass(frozen=True, slots=True)
class WorkerFailed:
    error: Exception


WorkerEvent = CatalogLoaded | ProgressChanged | InstallCompleted | WorkerFailed


@dataclass(frozen=True, slots=True)
class _LoadCatalog:
    pass


@dataclass(frozen=True, slots=True)
class _Install:
    record: ReleaseRecord
    location: SaveLocation


@dataclass(frozen=True, slots=True)
class _Stop:
    pass


_WorkerCommand = _LoadCatalog | _Install | _Stop


_NETWORK_ERROR_CODES = frozenset({"network_error", "http_error", "timeout"})
_VERIFICATION_ERROR_CODES = frozenset(
    {
        "archive_size_mismatch",
        "archive_sha256_mismatch",
        "checksum_mismatch",
        "install_verification_failed",
        "invalid_archive",
        "save_size_mismatch",
        "save_sha256_mismatch",
        "size_mismatch",
    }
)
_INCOMPATIBLE_ERROR_CODES = frozenset(
    {"unavailable_channel", "untrusted_asset", "invalid_record"}
)

_ERROR_TITLES = {
    "backup_failed": "The backup could not be created",
    "cancelled": "Installation cancelled",
    "cleanup_failed": "Temporary files could not be removed",
    "insufficient_space": "Not enough free space",
    "invalid_destination": "The save folder is not available",
    "not_directory": "The save folder is not available",
    "not_save": "The selected folder is not a save folder",
    "not_writable": "The save folder is not writable",
    "permission_denied": "The save folder is not writable",
    "recovery_failed": "The original save could not be restored",
    "replace_failed": "The save could not be replaced",
    "staging_failed": "The downloaded save could not be prepared",
    "target_locked": "Close the game and try again",
}


def error_copy(error: Exception) -> tuple[str, str]:
    """Return stable beginner-facing heading and preserved diagnostic text."""

    code = getattr(error, "code", None)
    if code in _NETWORK_ERROR_CODES:
        title = "No internet connection"
    elif code in _VERIFICATION_ERROR_CODES:
        title = "Downloaded file failed verification"
    elif code in _INCOMPATIBLE_ERROR_CODES:
        title = "This save is not available for the selected game"
    else:
        title = _ERROR_TITLES.get(code, "Installation could not be completed")
    return title, str(error)


class InstallerController:
    """Pure state machine for the installer wizard."""

    def __init__(
        self,
        *,
        on_change: Callable[[InstallerState], None] | None = None,
        state: InstallerState | None = None,
    ) -> None:
        self.state = InstallerState() if state is None else state
        self.on_change = on_change if on_change is not None else lambda _state: None

    def _publish(self, state: InstallerState) -> bool:
        if state == self.state:
            return False
        self.state = state
        self.on_change(state)
        return True

    def handle_event(self, event: WorkerEvent) -> bool:
        if isinstance(event, CatalogLoaded):
            return self.set_catalog(event.catalog)
        if isinstance(event, ProgressChanged):
            if self.state.step is not WizardStep.PROGRESS:
                return False
            return self._publish(
                replace(
                    self.state,
                    progress_stage=event.stage,
                    progress_downloaded=event.downloaded,
                    progress_total=event.total,
                    commit_started=(
                        self.state.commit_started or event.commit_started
                    ),
                )
            )
        if isinstance(event, InstallCompleted):
            return self.succeed(event.result)
        if isinstance(event, WorkerFailed):
            return self.fail(event.error)
        return False

    def set_catalog(self, catalog: Catalog) -> bool:
        selected = self.state.selected_record
        if selected is not None:
            selected = next(
                (record for record in catalog.records if record == selected), None
            )
        return self._publish(
            replace(self.state, catalog=catalog, selected_record=selected)
        )

    def set_locations(self, locations: tuple[SaveLocation, ...]) -> bool:
        selected = self.state.selected_location
        if selected is not None:
            selected = next(
                (location for location in locations if location == selected), None
            )
        return self._publish(
            replace(self.state, locations=tuple(locations), selected_location=selected)
        )

    def select_record(self, record: ReleaseRecord) -> bool:
        if self.state.step is not WizardStep.UPDATE or self.state.catalog is None:
            return False
        selected = next(
            (
                catalog_record
                for catalog_record in self.state.catalog.records
                if catalog_record == record
            ),
            None,
        )
        if selected is None:
            return False
        return self._publish(replace(self.state, selected_record=selected))

    def select_location(self, location: SaveLocation) -> bool:
        if self.state.step is not WizardStep.SAVE:
            return False
        selected = next(
            (
                available
                for available in self.state.locations
                if available == location
            ),
            None,
        )
        if selected is None:
            return False
        return self._publish(replace(self.state, selected_location=selected))

    def _has_valid_record(self) -> bool:
        catalog = self.state.catalog
        record = self.state.selected_record
        return (
            catalog is not None
            and record is not None
            and any(candidate == record for candidate in catalog.records)
        )

    def _has_compatible_location(self) -> bool:
        record = self.state.selected_record
        location = self.state.selected_location
        return (
            self._has_valid_record()
            and location is not None
            and any(candidate == location for candidate in self.state.locations)
            and location.target.value == record.target_id
        )

    def next(self) -> bool:
        if self.state.step is WizardStep.UPDATE:
            if not self._has_valid_record():
                return False
            return self._publish(replace(self.state, step=WizardStep.SAVE))
        if self.state.step is WizardStep.SAVE:
            if not self._has_compatible_location():
                return False
            return self._publish(replace(self.state, step=WizardStep.REVIEW))
        if self.state.step is WizardStep.REVIEW:
            if not self._has_compatible_location():
                return False
            return self._publish(
                replace(
                    self.state,
                    step=WizardStep.PROGRESS,
                    progress_stage=None,
                    progress_downloaded=0,
                    progress_total=0,
                    result=None,
                    error_title=None,
                    error_detail=None,
                    commit_started=False,
                )
            )
        return False

    def back(self) -> bool:
        if self.state.step is WizardStep.SAVE:
            return self._publish(replace(self.state, step=WizardStep.UPDATE))
        if self.state.step is WizardStep.REVIEW:
            return self._publish(replace(self.state, step=WizardStep.SAVE))
        return False

    def succeed(self, result: InstallResult) -> bool:
        if self.state.step is not WizardStep.PROGRESS:
            return False
        return self._publish(
            replace(
                self.state,
                step=WizardStep.RESULT,
                result=result,
                error_title=None,
                error_detail=None,
            )
        )

    def fail(self, error: Exception) -> bool:
        if self.state.step not in {WizardStep.UPDATE, WizardStep.PROGRESS}:
            return False
        title, detail = error_copy(error)
        return self._publish(
            replace(
                self.state,
                step=WizardStep.RESULT,
                result=None,
                error_title=title,
                error_detail=detail,
            )
        )

    def retry(self) -> bool:
        if (
            self.state.step is not WizardStep.RESULT
            or self.state.error_title is None
            or not self._has_compatible_location()
        ):
            return False
        return self._publish(
            replace(
                self.state,
                step=WizardStep.PROGRESS,
                progress_stage=None,
                progress_downloaded=0,
                progress_total=0,
                result=None,
                error_title=None,
                error_detail=None,
                commit_started=False,
            )
        )


class InstallerWorker:
    """One daemon thread that communicates only through typed queue events."""

    def __init__(
        self,
        *,
        fetch_catalog: Callable[[], Catalog] = default_fetch_catalog,
        download_archive: Callable[..., None] = default_download_archive,
        install_archive: Callable[..., InstallResult] = default_install_archive,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.events: SimpleQueue[WorkerEvent] = SimpleQueue()
        self._commands: SimpleQueue[_WorkerCommand] = SimpleQueue()
        self._fetch_catalog = fetch_catalog
        self._download_archive = download_archive
        self._install_archive = install_archive
        self._now = (
            now
            if now is not None
            else lambda: datetime.now(timezone.utc)
        )
        self._cancel_requested = threading.Event()
        self._state_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._install_pending = False
        self._closed = False
        self._commit_started = False

    @property
    def thread(self) -> threading.Thread | None:
        return self._thread

    def start(self) -> None:
        with self._state_lock:
            if self._closed:
                raise RuntimeError("worker is closed")
            if self._thread is not None:
                return
            self._thread = threading.Thread(
                target=self._run,
                name="fldailyedit-installer-worker",
                daemon=True,
            )
            self._thread.start()

    def load_catalog(self) -> None:
        self.start()
        self._commands.put(_LoadCatalog())

    def install(self, record: ReleaseRecord, location: SaveLocation) -> None:
        if location.target.value != record.target_id:
            raise ValueError("record and save location are incompatible")
        self.start()
        with self._state_lock:
            if self._install_pending:
                raise RuntimeError("an installation is already pending")
            self._install_pending = True
            self._commit_started = False
            self._cancel_requested.clear()
        self._commands.put(_Install(record, location))

    def cancel(self) -> None:
        with self._state_lock:
            self._cancel_requested.set()

    def close(self, timeout: float | None = 2.0) -> None:
        with self._state_lock:
            if self._closed:
                thread = self._thread
            else:
                self._closed = True
                thread = self._thread
        if thread is None:
            return
        self.cancel()
        self._commands.put(_Stop())
        thread.join(timeout)

    def _cancelled(self) -> bool:
        with self._state_lock:
            return (
                self._cancel_requested.is_set()
                and not self._commit_started
            )

    def _emit_download_progress(self, downloaded: int, total: int) -> None:
        self.events.put(
            ProgressChanged(
                stage="downloading",
                downloaded=downloaded,
                total=total,
                commit_started=False,
            )
        )

    def _emit_install_progress(self, stage: InstallStage) -> None:
        stage_value = stage.value if isinstance(stage, InstallStage) else str(stage)
        with self._state_lock:
            if (
                stage_value == InstallStage.REPLACING.value
                and not self._commit_started
            ):
                if self._cancel_requested.is_set():
                    raise InstallError(
                        "cancelled",
                        "Installation was cancelled",
                        stage=InstallStage.REPLACING,
                    )
                self._commit_started = True
            commit_started = self._commit_started
        self.events.put(
            ProgressChanged(
                stage=stage_value,
                commit_started=commit_started,
            )
        )

    def _perform_install(
        self,
        record: ReleaseRecord,
        location: SaveLocation,
    ) -> InstallResult:
        with TemporaryDirectory(prefix="fldailyedit-installer-") as temporary:
            archive_path = Path(temporary) / "download.zip"
            self._download_archive(
                record,
                archive_path,
                progress=self._emit_download_progress,
                cancelled=self._cancelled,
            )
            if self._cancelled():
                raise DownloadError("cancelled", "download cancelled by user")
            result = self._install_archive(
                archive_path,
                location.save_directory,
                record,
                now=self._now,
                progress=self._emit_install_progress,
                cancelled=self._cancelled,
            )
        return result

    def _run(self) -> None:
        while True:
            command = self._commands.get()
            if isinstance(command, _Stop):
                return
            if isinstance(command, _LoadCatalog):
                try:
                    catalog = self._fetch_catalog()
                except Exception as error:
                    self.events.put(WorkerFailed(error))
                else:
                    self.events.put(CatalogLoaded(catalog))
                continue

            try:
                result = self._perform_install(command.record, command.location)
            except Exception as error:
                terminal_event: InstallCompleted | WorkerFailed = WorkerFailed(error)
            else:
                terminal_event = InstallCompleted(result)
            with self._state_lock:
                self._install_pending = False
            self.events.put(terminal_event)
