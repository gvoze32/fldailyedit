from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
import os
from pathlib import Path
from queue import Empty, SimpleQueue
import sys
from tempfile import TemporaryDirectory
import threading
from typing import Any, Callable

tkinter: Any = None
filedialog: Any = None
tkinter_font: Any = None
ttk: Any = None

from installer import __version__
from installer.catalog import (
    Catalog,
    Channel,
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
from local_update import (
    CancellationToken,
    LocalUpdateProgress,
    LocalUpdateRequest,
    LocalUpdateResult,
    LocalUpdateService,
    LocalUpdateStage,
)
from installer.paths import (
    DestinationError,
    GameTarget,
    SaveLocation,
    discover_save_locations,
    validate_destination,
)


class InstallerMode(str, Enum):
    RELEASE = "release"
    LOCAL = "local"


class WizardStep(str, Enum):
    UPDATE = "update"
    SAVE = "save"
    REVIEW = "review"
    PROGRESS = "progress"
    RESULT = "result"


@dataclass(frozen=True, slots=True)
class InstallerState:
    mode: InstallerMode = InstallerMode.RELEASE
    step: WizardStep = WizardStep.UPDATE
    catalog: Catalog | None = None
    selected_record: ReleaseRecord | None = None
    locations: tuple[SaveLocation, ...] = ()
    selected_location: SaveLocation | None = None
    local_edit_file: Path | None = None
    local_deep: bool = False
    progress_stage: str | None = None
    progress_downloaded: int = 0
    progress_total: int = 0
    result: InstallResult | LocalUpdateResult | None = None
    error_title: str | None = None
    error_detail: str | None = None
    commit_started: bool = False


@dataclass(frozen=True, slots=True)
class CatalogLoaded:
    catalog: Catalog



@dataclass(frozen=True, slots=True)
class LocationsDiscovered:
    locations: tuple[SaveLocation, ...]


@dataclass(frozen=True, slots=True)
class LocationDiscoveryFailed:
    error: Exception


@dataclass(frozen=True, slots=True)
class DestinationValidated:
    location: SaveLocation


@dataclass(frozen=True, slots=True)
class DestinationValidationFailed:
    error: Exception

@dataclass(frozen=True, slots=True)
class LocalSaveSelected:
    location: SaveLocation

@dataclass(frozen=True, slots=True)
class ProgressChanged:
    stage: str
    downloaded: int = 0
    total: int = 0
    commit_started: bool = False



@dataclass(frozen=True, slots=True)
class LocalProgressChanged:
    progress: LocalUpdateProgress


@dataclass(frozen=True, slots=True)
class LocalUpdateCompleted:
    result: LocalUpdateResult

@dataclass(frozen=True, slots=True)
class InstallCompleted:
    result: InstallResult


@dataclass(frozen=True, slots=True)
class WorkerFailed:
    error: Exception


WorkerEvent = (
    CatalogLoaded
    | LocationsDiscovered
    | LocationDiscoveryFailed
    | DestinationValidated
    | DestinationValidationFailed
    | ProgressChanged
    | LocalProgressChanged
    | LocalSaveSelected
    | InstallCompleted
    | LocalUpdateCompleted
    | WorkerFailed
)


@dataclass(frozen=True, slots=True)
class _LoadCatalog:
    pass


@dataclass(frozen=True, slots=True)
class _DiscoverLocations:
    pass


@dataclass(frozen=True, slots=True)
class _ValidateDestination:
    path: Path
    target: GameTarget
    game_name: str


@dataclass(frozen=True, slots=True)
class _Install:
    record: ReleaseRecord
    location: SaveLocation


@dataclass(frozen=True, slots=True)
class _SelectLocal:
    location: SaveLocation


@dataclass(frozen=True, slots=True)
class _StartLocalUpdate:
    location: SaveLocation
    deep: bool



@dataclass(frozen=True, slots=True)
class _Stop:
    pass


_WorkerCommand = (
    _LoadCatalog
    | _DiscoverLocations
    | _ValidateDestination
    | _Install
    | _SelectLocal
    | _StartLocalUpdate
    | _Stop
)


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
    "decrypt_failed": "The local save could not be opened",
    "input_changed": "The local save changed during the update",
    "insufficient_space": "Not enough free space",
    "invalid_destination": "The save folder is not available",
    "invalid_save": "The local save failed validation",
    "missing_input": "The local save could not be found",
    "not_directory": "The save folder is not available",
    "not_save": "The selected folder is not a save folder",
    "not_writable": "The save folder is not writable",
    "output_changed": "The output save changed during the update",
    "permission_denied": "The save folder is not writable",
    "post_validation_failed": "The updated save failed validation",
    "publish_failed": "The updated save could not be published",
    "reparse_point": "The save folder is not available",
    "recovery_failed": "The original save could not be restored",
    "replace_failed": "The save could not be replaced",
    "scrape_failed": "Could not fetch update data",
    "staging_failed": "The downloaded save could not be prepared",
    "target_changed": "The save file changed during installation",
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
        if isinstance(event, LocationsDiscovered):
            return self.set_locations(event.locations)
        if isinstance(event, LocalSaveSelected):
            if self.state.mode is not InstallerMode.LOCAL:
                return False
            return self._publish(
                replace(
                    self.state,
                    selected_location=event.location,
                    local_edit_file=event.location.edit_file,
                )
            )
        if isinstance(event, LocalProgressChanged):
            if self.state.step is not WizardStep.PROGRESS:
                return False
            progress = event.progress
            return self._publish(
                replace(
                    self.state,
                    progress_stage=progress.stage.value,
                    progress_downloaded=progress.current,
                    progress_total=progress.total,
                    commit_started=(
                        self.state.commit_started or progress.commit_started
                    ),
                )
            )
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
        if isinstance(event, InstallCompleted | LocalUpdateCompleted):
            return self.succeed(event.result)
        if isinstance(event, WorkerFailed):
            return self.fail(event.error)
        return False

    def select_mode(self, mode: InstallerMode) -> bool:
        if self.state.step is not WizardStep.UPDATE:
            return False
        return self._publish(
            replace(
                self.state,
                mode=mode,
                selected_record=(
                    None
                    if mode is InstallerMode.LOCAL
                    else self.state.selected_record
                ),
                local_edit_file=(
                    self.state.local_edit_file
                    if mode is InstallerMode.LOCAL
                    else None
                ),
            )
        )

    def set_catalog(self, catalog: Catalog) -> bool:
        selected = self.state.selected_record
        if selected is not None:
            selected = next(
                (record for record in catalog.records if record == selected), None
            )
        return self._publish(
            replace(self.state, catalog=catalog, selected_record=selected)
        )

    def set_local_deep(self, deep: bool) -> bool:
        if (
            self.state.step is not WizardStep.UPDATE
            or self.state.mode is not InstallerMode.LOCAL
        ):
            return False
        return self._publish(replace(self.state, local_deep=bool(deep)))

    def set_locations(self, locations: tuple[SaveLocation, ...]) -> bool:
        selected = self.state.selected_location
        if selected is not None:
            selected = next(
                (location for location in locations if location == selected), None
            )
        return self._publish(
            replace(
                self.state,
                locations=tuple(locations),
                selected_location=selected,
                local_edit_file=(
                    selected.edit_file
                    if self.state.mode is InstallerMode.LOCAL and selected is not None
                    else None
                ),
            )
        )

    def select_record(self, record: ReleaseRecord) -> bool:
        if (
            self.state.step is not WizardStep.UPDATE
            or self.state.mode is not InstallerMode.RELEASE
            or self.state.catalog is None
        ):
            return False
        if record.target_id == GameTarget.LOCAL.value:
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
        return self._publish(
            replace(
                self.state,
                mode=InstallerMode.RELEASE,
                selected_record=selected,
                local_edit_file=None,
            )
        )


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
        return self._publish(
            replace(
                self.state,
                selected_location=selected,
                local_edit_file=(
                    selected.edit_file
                    if self.state.mode is InstallerMode.LOCAL
                    else None
                ),
            )
        )

    def _has_valid_record(self) -> bool:
        if self.state.mode is InstallerMode.LOCAL:
            return True
        catalog = self.state.catalog
        record = self.state.selected_record
        return (
            catalog is not None
            and record is not None
            and any(candidate == record for candidate in catalog.records)
        )

    def _has_compatible_location(self) -> bool:
        location = self.state.selected_location
        if self.state.mode is InstallerMode.LOCAL:
            return (
                location is not None
                and any(candidate == location for candidate in self.state.locations)
                and location.edit_file.is_file()
            )
        record = self.state.selected_record
        return (
            self._has_valid_record()
            and location is not None
            and any(candidate == location for candidate in self.state.locations)
            and record is not None
            and record.target_id != GameTarget.LOCAL.value
            and location.target.value == record.target_id
        )


    def next(self) -> bool:
        if self.state.step is WizardStep.UPDATE:
            if self.state.mode is InstallerMode.LOCAL:
                return self._publish(replace(self.state, step=WizardStep.SAVE))
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

    def succeed(self, result: InstallResult | LocalUpdateResult) -> bool:
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

    def retry_catalog(self) -> bool:
        if (
            self.state.step is not WizardStep.RESULT
            or self.state.error_title is None
            or self.state.catalog is not None
        ):
            return False
        return self._publish(
            replace(
                self.state,
                step=WizardStep.UPDATE,
                progress_stage=None,
                progress_downloaded=0,
                progress_total=0,
                result=None,
                error_title=None,
                error_detail=None,
                commit_started=False,
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
        discover_locations: Callable[
            [], tuple[SaveLocation, ...]
        ] = discover_save_locations,
        validate_destination: Callable[
            [Path, GameTarget], Path
        ] = validate_destination,
        download_archive: Callable[..., None] = default_download_archive,
        install_archive: Callable[..., InstallResult] = default_install_archive,
        local_update_service: LocalUpdateService | None = None,
        local_update_factory: Callable[[], LocalUpdateService] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.events: SimpleQueue[WorkerEvent] = SimpleQueue()
        self._commands: SimpleQueue[_WorkerCommand] = SimpleQueue()
        self._fetch_catalog = fetch_catalog
        self._discover_locations = discover_locations
        self._validate_destination = validate_destination
        self._download_archive = download_archive
        self._local_update_factory = local_update_factory
        self._local_update_service = local_update_service
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
        self._local_token: CancellationToken | None = None
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

    def discover_locations(self) -> None:
        self.start()
        self._commands.put(_DiscoverLocations())

    def validate_destination(
        self,
        path: Path,
        target: GameTarget,
        game_name: str,
    ) -> None:
        self.start()
        self._commands.put(_ValidateDestination(path, target, game_name))

    def install(self, record: ReleaseRecord, location: SaveLocation) -> None:
        if (
            record.target_id == GameTarget.LOCAL.value
            or location.target is GameTarget.LOCAL
            or location.target.value != record.target_id
        ):
            raise ValueError("record and save location are incompatible")
        self.start()
        with self._state_lock:
            if self._install_pending:
                raise RuntimeError("an installation is already pending")
            self._install_pending = True
            self._commit_started = False
            self._cancel_requested.clear()
        self._commands.put(_Install(record, location))

    def select_local(self, location: SaveLocation) -> None:
        if not location.edit_file.is_file():
            raise ValueError(f"Edit file not found: {location.edit_file}")
        self.start()
        self._commands.put(_SelectLocal(location))


    def start_local_update(
        self,
        location: SaveLocation,
        *,
        deep: bool = False,
    ) -> None:
        self.start()
        with self._state_lock:
            if self._install_pending:
                raise RuntimeError("an installation is already pending")
            self._install_pending = True
            self._commit_started = False
            self._cancel_requested.clear()
            self._local_token = CancellationToken()
        self._commands.put(_StartLocalUpdate(location, bool(deep)))

    def cancel(self) -> bool:
        with self._state_lock:
            if not self._install_pending or self._commit_started:
                return False
            if self._local_token is not None:
                return self._local_token.request()
            self._cancel_requested.set()
            return True

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
            canonical_destination = self._validate_destination(
                location.save_directory,
                location.target,
            )
            result = self._install_archive(
                archive_path,
                canonical_destination,
                record,
                now=self._now,
                progress=self._emit_install_progress,
                cancelled=self._cancelled,
            )
        return result

    def _emit_local_progress(self, progress: LocalUpdateProgress) -> None:
        with self._state_lock:
            if progress.commit_started:
                self._commit_started = True
        self.events.put(LocalProgressChanged(progress))

    def _local_service(self) -> LocalUpdateService:
        if self._local_update_service is None:
            if self._local_update_factory is not None:
                self._local_update_service = self._local_update_factory()
            else:
                from run import build_local_update_service

                self._local_update_service = build_local_update_service()
        return self._local_update_service

    def _perform_local_update(
        self,
        location: SaveLocation,
        deep: bool,
    ) -> LocalUpdateResult:
        with self._state_lock:
            token = self._local_token
        if token is None:
            raise RuntimeError("local update cancellation token is unavailable")
        request = LocalUpdateRequest(edit_path=location.edit_file, deep=deep)
        return self._local_service().execute(
            request,
            progress=self._emit_local_progress,
            token=token,
        )

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
            if isinstance(command, _DiscoverLocations):
                try:
                    locations = self._discover_locations()
                    validated_locations: list[SaveLocation] = []
                    seen_paths: set[str] = set()
                    for location in locations:
                        try:
                            canonical_path = self._validate_destination(
                                location.save_directory,
                                location.target,
                            )
                        except DestinationError:
                            continue
                        key = str(canonical_path).casefold()
                        if key in seen_paths:
                            continue
                        seen_paths.add(key)
                        validated_locations.append(
                            replace(location, save_directory=canonical_path)
                        )
                except Exception as error:
                    self.events.put(LocationDiscoveryFailed(error))
                else:
                    self.events.put(
                        LocationsDiscovered(tuple(validated_locations))
                    )
                continue
            if isinstance(command, _ValidateDestination):
                try:
                    path = self._validate_destination(
                        command.path,
                        command.target,
                    )
                except Exception as error:
                    self.events.put(DestinationValidationFailed(error))
                else:
                    self.events.put(
                        DestinationValidated(
                            SaveLocation(
                                command.target,
                                command.game_name,
                                path,
                            )
                        )
                    )
                continue

            if isinstance(command, _SelectLocal):
                self.events.put(LocalSaveSelected(command.location))
                continue

            if isinstance(command, _StartLocalUpdate):
                try:
                    result = self._perform_local_update(
                        command.location,
                        command.deep,
                    )
                except Exception as error:
                    terminal_event: LocalUpdateCompleted | WorkerFailed = WorkerFailed(
                        error
                    )
                else:
                    terminal_event = LocalUpdateCompleted(result)
                with self._state_lock:
                    self._install_pending = False
                    self._local_token = None
                self.events.put(terminal_event)
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



UI_COPY = {
    "fast_title": "Fast — Recommended",
    "fast_description": "Standard daily update from the live transfer feed.",
    "deep_title": "Deep — Expanded coverage",
    "deep_description": (
        "Checks every locally indexed FotMob club for maximum coverage."
    ),
    "coverage_note": (
        "Fast and Deep describe update coverage, not download speed."
    ),
    "install": "Download and install",
    "local_mode": "Update my local save",
    "release_mode": "Install a prebuilt release",
    "local_description": (
        "Update the existing local save on this PC."
    ),
    "local_safety": (
        "The original save is backed up before an in-place replacement."
    ),
    "apply_local": "Apply update",
    "close_game": "Close the game before continuing.",
    "open_folder": "Open save folder",
    "copy_diagnostics": "Copy diagnostic details",
}

_WINDOW_MINIMUM = (760, 560)
_SPACE_XS = 4
_SPACE_S = 8
_SPACE_M = 16
_SPACE_L = 24
_RADIO_DESCRIPTION_INDENT = 28
_POLL_INTERVAL_MS = 50
_PROGRESS_PULSE_MS = 12
_MINIMUM_WRAP_WIDTH = 320
_LOCATION_VIEWPORT_HEIGHT = 200


class CloseDisposition(str, Enum):
    CLOSE = "close"
    CANCEL_AND_WAIT = "cancel_and_wait"
    BLOCK = "block"


_LOCAL_STAGE_COPY = {
    LocalUpdateStage.SCRAPING.value: "Checking the latest transfers…",
    LocalUpdateStage.VALIDATING.value: "Validating the local save…",
    LocalUpdateStage.MATCHING.value: "Matching players safely…",
    LocalUpdateStage.APPLYING.value: "Preparing the updated save…",
    LocalUpdateStage.VERIFYING.value: "Verifying the updated save…",
    LocalUpdateStage.ENCRYPTING.value: "Finishing the in-place update safely…",
    LocalUpdateStage.COMPLETE.value: "Local update complete.",
}

@dataclass(frozen=True, slots=True)
class ProgressPresentation:
    mode: str
    status: str
    maximum: int
    value: int
    controls_locked: bool


_STAGE_COPY = {
    InstallStage.VALIDATING_DESTINATION.value: "Checking the save folder…",
    InstallStage.VERIFYING_ARCHIVE.value: "Verifying the downloaded update…",
    InstallStage.BACKING_UP.value: "Creating a backup…",
    InstallStage.STAGING.value: "Preparing the updated save…",
    InstallStage.REPLACING.value: "Finishing installation safely…",
    InstallStage.VERIFYING_INSTALL.value: "Finishing installation safely…",
    InstallStage.RESTORING.value: "Restoring the original save safely…",
}


def progress_presentation(
    state: InstallerState,
    *,
    cancellation_requested: bool = False,
    commit_locked: bool = False,
) -> ProgressPresentation:
    """Translate controller progress into widget-neutral display state."""
    local = state.mode is InstallerMode.LOCAL
    if state.commit_started or commit_locked:
        return ProgressPresentation(
            mode="indeterminate",
            status=(
                "Finishing the local update safely…"
                if local
                else "Finishing installation safely…"
            ),
            maximum=100,
            value=0,
            controls_locked=True,
        )
    if cancellation_requested:
        return ProgressPresentation(
            mode="indeterminate",
            status=(
                "Cancelling local update…"
                if local
                else "Cancelling installation…"
            ),
            maximum=100,
            value=0,
            controls_locked=False,
        )
    if state.progress_stage == "downloading":
        total = max(state.progress_total, 1)
        downloaded = min(max(state.progress_downloaded, 0), total)
        percent = (downloaded * 100) // total
        return ProgressPresentation(
            mode="determinate",
            status=f"Downloading update… {percent}%",
            maximum=total,
            value=downloaded,
            controls_locked=False,
        )
    stage_copy = _LOCAL_STAGE_COPY if local else _STAGE_COPY
    return ProgressPresentation(
        mode="indeterminate",
        status=stage_copy.get(
            state.progress_stage,
            "Preparing the local update…" if local else "Preparing installation…",
        ),
        maximum=100,
        value=0,
        controls_locked=False,
    )


def close_disposition(state: InstallerState) -> CloseDisposition:
    """Describe close behavior without coupling policy to Tk callbacks."""

    if state.step is not WizardStep.PROGRESS:
        return CloseDisposition.CLOSE
    if state.commit_started:
        return CloseDisposition.BLOCK
    return CloseDisposition.CANCEL_AND_WAIT


def scroll_fraction_for_item(
    *,
    view_top: int,
    view_height: int,
    content_height: int,
    item_top: int,
    item_height: int,
) -> float | None:
    """Return a canvas yview fraction only when an item is clipped."""

    if content_height <= view_height:
        return None
    item_bottom = item_top + item_height
    if item_top < view_top:
        target_top = item_top
    elif item_bottom > view_top + view_height:
        target_top = item_bottom - view_height
    else:
        return None
    maximum_top = content_height - view_height
    target_top = min(max(target_top, 0), maximum_top)
    return target_top / content_height


def diagnostic_details(
    state: InstallerState,
    *,
    error_code: str | None,
) -> str:
    """Build a deliberately narrow diagnostic summary safe for clipboard use."""

    stage = state.progress_stage or state.step.value
    path = (
        str(state.selected_location.save_directory)
        if state.selected_location is not None
        else "not selected"
    )
    return (
        f"FLDailyEdit Installer {__version__}\n"
        f"Stage: {stage}\n"
        f"Code: {error_code or 'none'}\n"
        f"Selected path: {path}"
    )


def open_save_folder(path: Path) -> bool:
    """Open a save folder only on Windows, where os.startfile is available."""

    if sys.platform != "win32":
        return False
    startfile = getattr(os, "startfile", None)
    if startfile is None:
        return False
    try:
        startfile(path)
    except OSError:
        return False
    return True


def _load_tkinter() -> None:
    """Delay Tk imports so controller and CLI version checks remain headless."""

    global tkinter, filedialog, tkinter_font, ttk
    if tkinter is not None:
        return
    import tkinter as tkinter_module
    from tkinter import filedialog as filedialog_module
    from tkinter import font as tkinter_font_module
    from tkinter import ttk as ttk_module

    tkinter = tkinter_module
    filedialog = filedialog_module
    tkinter_font = tkinter_font_module
    ttk = ttk_module


def _enable_native_dpi_awareness() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        try:
            ctypes.windll.user32.SetProcessDpiAwarenessContext(-4)
        except (AttributeError, OSError):
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(2)
            except (AttributeError, OSError):
                ctypes.windll.user32.SetProcessDPIAware()
    except (AttributeError, ImportError, OSError):
        pass


class InstallerApplication:
    """Native four-step ttk view over InstallerController and InstallerWorker."""

    def __init__(self, root: tkinter.Tk) -> None:
        _load_tkinter()
        self.root = root
        self.worker = InstallerWorker()
        self.controller = InstallerController(on_change=self._render)
        self._closed = False
        self._close_pending = False
        self._cancel_requested = False
        self._error_code: str | None = None
        self._failure_operation: str | None = None
        self._location_discovery_error: str | None = None
        self._browse_pending = False
        self._commit_lock_observed = False
        self._poll_after_id: str | None = None
        self._rendered_step: WizardStep | None = None
        self._progress_running = False
        self._records_by_channel: dict[str, ReleaseRecord] = {}
        self._locations_by_key: dict[str, SaveLocation] = {}
        self._wrapped_labels: list[ttk.Label] = []

        self._configure_root()
        self._configure_styles()
        self._build_view()
        self._render(self.controller.state)


        self.root.protocol("WM_DELETE_WINDOW", self._on_window_close)
        self.root.bind("<Escape>", self._on_escape)
        self.root.bind("<Configure>", self._on_resize, add="+")
        self._schedule_poll()
        self.worker.discover_locations()
        self.worker.load_catalog()

    def _configure_root(self) -> None:
        self.root.title("FLDailyEdit Installer")
        self.root.minsize(*_WINDOW_MINIMUM)
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

    def _configure_styles(self) -> None:
        default_font = tkinter_font.nametofont("TkDefaultFont")
        self._title_font = default_font.copy()
        self._title_font.configure(
            size=default_font.cget("size") + 5,
            weight="bold",
        )
        self._section_font = default_font.copy()
        self._section_font.configure(
            size=default_font.cget("size") + 2,
            weight="bold",
        )
        style = ttk.Style(self.root)
        style.configure("Wizard.Title.TLabel", font=self._title_font)
        style.configure("Wizard.Section.TLabel", font=self._section_font)

    def _build_view(self) -> None:
        self._shell = ttk.Frame(
            self.root,
            padding=(_SPACE_L, _SPACE_L, _SPACE_L, _SPACE_M),
        )
        self._shell.grid(row=0, column=0, sticky="nsew")
        self._shell.columnconfigure(0, weight=1)
        self._shell.rowconfigure(3, weight=1)

        self._step_var = tkinter.StringVar(self.root)
        self._title_var = tkinter.StringVar(self.root)
        ttk.Label(self._shell, textvariable=self._step_var).grid(
            row=0,
            column=0,
            sticky="w",
        )
        ttk.Label(
            self._shell,
            textvariable=self._title_var,
            style="Wizard.Title.TLabel",
        ).grid(
            row=1,
            column=0,
            sticky="w",
            pady=(_SPACE_XS, _SPACE_L),
        )

        ttk.Separator(self._shell, orient="horizontal").grid(
            row=2,
            column=0,
            sticky="ew",
            pady=(0, _SPACE_M),
        )

        self._body = ttk.Frame(self._shell)
        self._body.grid(row=3, column=0, sticky="nsew")
        self._body.columnconfigure(0, weight=1)
        self._body.rowconfigure(0, weight=1)

        self._frames = {
            WizardStep.UPDATE: self._build_update_frame(),
            WizardStep.SAVE: self._build_save_frame(),
            WizardStep.REVIEW: self._build_review_frame(),
            WizardStep.PROGRESS: self._build_progress_frame(),
        }
        for frame in self._frames.values():
            frame.grid(row=0, column=0, sticky="nsew")
            frame.grid_remove()

        ttk.Separator(self._shell, orient="horizontal").grid(
            row=4,
            column=0,
            sticky="ew",
            pady=(_SPACE_M, _SPACE_M),
        )
        footer = ttk.Frame(self._shell)
        footer.grid(row=5, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)

        self._back_button = ttk.Button(
            footer,
            text="Back",
            command=self._back,
            underline=0,
        )
        self._back_button.grid(row=0, column=1, padx=(0, _SPACE_S))
        self._next_button = ttk.Button(
            footer,
            text="Next",
            command=self._next,
            underline=0,
        )
        self._next_button.grid(row=0, column=2, padx=(0, _SPACE_S))
        self._cancel_button = ttk.Button(
            footer,
            text="Cancel",
            command=self._cancel_or_close,
            underline=0,
        )
        self._cancel_button.grid(row=0, column=3)

    def _wrapped_label(
        self,
        parent: tkinter.Misc,
        *,
        text: str | None = None,
        textvariable: tkinter.StringVar | None = None,
        style: str | None = None,
    ) -> ttk.Label:
        options: dict[str, object] = {
            "justify": "left",
            "anchor": "w",
            "wraplength": _WINDOW_MINIMUM[0] - (_SPACE_L * 4),
        }
        if text is not None:
            options["text"] = text
        if textvariable is not None:
            options["textvariable"] = textvariable
        if style is not None:
            options["style"] = style
        label = ttk.Label(parent, **options)
        self._wrapped_labels.append(label)
        return label

    def _build_update_frame(self) -> ttk.Frame:
        frame = ttk.Frame(self._body)
        frame.columnconfigure(0, weight=1)

        self._wrapped_label(
            frame,
            text="Choose how FLDailyEdit should update your save.",
        ).grid(row=0, column=0, sticky="ew")
        self._wrapped_label(frame, text=UI_COPY["coverage_note"]).grid(
            row=1,
            column=0,
            sticky="ew",
            pady=(_SPACE_XS, _SPACE_M),
        )

        self._mode_var = tkinter.StringVar(
            self.root,
            value=InstallerMode.RELEASE.value,
        )
        self._mode_buttons: dict[InstallerMode, ttk.Radiobutton] = {}

        release_group = ttk.Frame(
            frame,
            relief="groove",
            borderwidth=1,
            padding=_SPACE_S,
        )
        release_group.grid(
            row=2,
            column=0,
            sticky="ew",
            pady=(_SPACE_S, 0),
        )
        release_group.columnconfigure(0, weight=1)

        release_button = ttk.Radiobutton(
            release_group,
            text=UI_COPY["release_mode"],
            value=InstallerMode.RELEASE.value,
            variable=self._mode_var,
            command=self._select_mode,
        )
        release_button.grid(row=0, column=0, sticky="w")
        self._mode_buttons[InstallerMode.RELEASE] = release_button

        self._catalog_status_var = tkinter.StringVar(
            self.root,
            value="Checking for available updates…",
        )
        self._wrapped_label(
            release_group,
            textvariable=self._catalog_status_var,
        ).grid(row=1, column=0, sticky="ew", pady=(0, _SPACE_XS))

        self._record_var = tkinter.StringVar(self.root)
        self._record_buttons: dict[Channel, ttk.Radiobutton] = {}
        choices = (
            (
                Channel.FAST,
                UI_COPY["fast_title"],
                UI_COPY["fast_description"],
            ),
            (
                Channel.DEEP,
                UI_COPY["deep_title"],
                UI_COPY["deep_description"],
            ),
        )
        choices_frame = ttk.Frame(release_group, padding=(_SPACE_S, 0))
        choices_frame.grid(row=2, column=0, sticky="ew")
        choices_frame.columnconfigure(0, weight=1)
        row = 0
        for channel, title, description in choices:
            button = ttk.Radiobutton(
                choices_frame,
                text=title,
                value=channel.value,
                variable=self._record_var,
                command=self._select_record,
                state="disabled",
            )
            button.grid(row=row, column=0, sticky="w", pady=(_SPACE_S, 0))
            self._record_buttons[channel] = button
            self._wrapped_label(choices_frame, text=description).grid(
                row=row + 1,
                column=0,
                sticky="ew",
                padx=(_RADIO_DESCRIPTION_INDENT, 0),
                pady=(_SPACE_XS, _SPACE_S),
            )
            row += 2

        local_group = ttk.Frame(
            frame,
            relief="groove",
            borderwidth=1,
            padding=_SPACE_S,
        )
        local_group.grid(
            row=3,
            column=0,
            sticky="ew",
            pady=(_SPACE_M, 0),
        )
        local_group.columnconfigure(0, weight=1)

        local_button = ttk.Radiobutton(
            local_group,
            text=UI_COPY["local_mode"],
            value=InstallerMode.LOCAL.value,
            variable=self._mode_var,
            command=self._select_mode,
        )
        local_button.grid(row=0, column=0, sticky="w")
        self._mode_buttons[InstallerMode.LOCAL] = local_button

        self._wrapped_label(local_group, text=UI_COPY["local_description"]).grid(
            row=1,
            column=0,
            sticky="ew",
            padx=(_RADIO_DESCRIPTION_INDENT, 0),
            pady=(_SPACE_XS, _SPACE_S),
        )
        self._local_deep_var = tkinter.BooleanVar(self.root, value=False)
        self._local_deep_button = ttk.Checkbutton(
            local_group,
            text=UI_COPY["deep_title"],
            variable=self._local_deep_var,
            command=self._select_local_deep,
        )
        self._local_deep_button.grid(
            row=2,
            column=0,
            sticky="w",
            padx=(_RADIO_DESCRIPTION_INDENT, 0),
            pady=(0, _SPACE_XS),
        )
        return frame

    def _build_save_frame(self) -> ttk.Frame:
        frame = ttk.Frame(self._body)
        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=0)
        frame.rowconfigure(2, weight=1)

        self._wrapped_label(
            frame,
            text=(
                "Select the save folder to update. Detected locations appear "
                "below; use Browse if yours is elsewhere."
            ),
        ).grid(row=0, column=0, columnspan=2, sticky="ew")

        self._location_status_var = tkinter.StringVar(self.root)
        self._wrapped_label(
            frame,
            textvariable=self._location_status_var,
        ).grid(
            row=1,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=(_SPACE_S, _SPACE_M),
        )

        location_viewport = ttk.Frame(frame)
        location_viewport.grid(row=2, column=0, sticky="nsew")
        location_viewport.columnconfigure(0, weight=1)
        location_viewport.rowconfigure(0, weight=1)
        frame_background = ttk.Style(self.root).lookup("TFrame", "background")
        self._location_canvas = tkinter.Canvas(
            location_viewport,
            background=frame_background,
            borderwidth=0,
            highlightthickness=0,
            height=_LOCATION_VIEWPORT_HEIGHT,
            takefocus=True,
        )
        self._location_canvas.grid(row=0, column=0, sticky="nsew")
        self._location_scrollbar = ttk.Scrollbar(
            location_viewport,
            orient="vertical",
            command=self._location_canvas.yview,
            takefocus=True,
        )
        self._location_scrollbar.grid(row=0, column=1, sticky="ns")
        self._location_canvas.configure(
            yscrollcommand=self._location_scrollbar.set
        )
        self._location_holder = ttk.Frame(self._location_canvas)
        self._location_holder.columnconfigure(0, weight=1)
        self._location_window = self._location_canvas.create_window(
            (0, 0),
            window=self._location_holder,
            anchor="nw",
        )
        self._location_holder.bind(
            "<Configure>",
            self._on_location_holder_configure,
            add="+",
        )
        self._location_canvas.bind(
            "<Configure>",
            self._on_location_canvas_configure,
            add="+",
        )
        self._bind_location_scrolling(self._location_canvas)
        self._bind_location_scrolling(self._location_holder)
        self._location_var = tkinter.StringVar(self.root)
        self._location_path_labels: list[ttk.Label] = []

        self._browse_button = ttk.Button(
            frame,
            text="Browse…",
            command=self._browse,
            underline=0,
        )
        self._browse_button.grid(
            row=2,
            column=1,
            sticky="ne",
            padx=(_SPACE_M, 0),
        )

        self._browse_error_var = tkinter.StringVar(self.root)
        self._browse_error_label = self._wrapped_label(
            frame,
            textvariable=self._browse_error_var,
        )
        self._browse_error_label.grid(
            row=3,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=(_SPACE_S, 0),
        )
        return frame


    def _bind_location_scrolling(self, widget: tkinter.Misc) -> None:
        widget.bind("<MouseWheel>", self._on_location_mousewheel, add="+")
        widget.bind("<Button-4>", self._on_location_mousewheel, add="+")
        widget.bind("<Button-5>", self._on_location_mousewheel, add="+")

    def _on_location_holder_configure(
        self,
        _event: tkinter.Event[tkinter.Misc],
    ) -> None:
        self._location_canvas.configure(
            scrollregion=self._location_canvas.bbox("all")
        )

    def _on_location_canvas_configure(
        self,
        event: tkinter.Event[tkinter.Misc],
    ) -> None:
        self._location_canvas.itemconfigure(
            self._location_window,
            width=event.width,
        )

    def _on_location_mousewheel(
        self,
        event: tkinter.Event[tkinter.Misc],
    ) -> str:
        button = getattr(event, "num", None)
        delta = getattr(event, "delta", 0)
        if button == 4 or delta > 0:
            units = -1
        elif button == 5 or delta < 0:
            units = 1
        else:
            return "break"
        self._location_canvas.yview_scroll(units, "units")
        return "break"

    def _ensure_location_visible(self, widget: tkinter.Misc) -> None:
        if not widget.winfo_exists():
            return
        content_height = self._location_holder.winfo_reqheight()
        view_height = self._location_canvas.winfo_height()
        view_top = round(
            self._location_canvas.yview()[0] * content_height
        )
        fraction = scroll_fraction_for_item(
            view_top=view_top,
            view_height=view_height,
            content_height=content_height,
            item_top=widget.winfo_y(),
            item_height=widget.winfo_height(),
        )
        if fraction is not None:
            self._location_canvas.yview_moveto(fraction)

    def _focus_location_button(self, widget: ttk.Radiobutton) -> None:
        if not widget.winfo_exists():
            return
        widget.focus_set()
        self._ensure_location_visible(widget)

    def _build_review_frame(self) -> ttk.Frame:
        frame = ttk.Frame(self._body)
        frame.columnconfigure(0, weight=1)

        self._wrapped_label(
            frame,
            text=UI_COPY["close_game"],
            style="Wizard.Section.TLabel",
        ).grid(row=0, column=0, sticky="ew", pady=(0, _SPACE_L))

        self._review_coverage_var = tkinter.StringVar(self.root)
        self._review_location_var = tkinter.StringVar(self.root)
        self._review_safety_var = tkinter.StringVar(self.root)
        ttk.Label(frame, text="Update coverage").grid(
            row=1,
            column=0,
            sticky="w",
        )
        self._wrapped_label(
            frame,
            textvariable=self._review_coverage_var,
            style="Wizard.Section.TLabel",
        ).grid(row=2, column=0, sticky="ew", pady=(_SPACE_XS, _SPACE_M))
        ttk.Label(frame, text="Save location").grid(
            row=3,
            column=0,
            sticky="w",
        )
        self._wrapped_label(
            frame,
            textvariable=self._review_location_var,
            style="Wizard.Section.TLabel",
        ).grid(row=4, column=0, sticky="ew", pady=(_SPACE_XS, _SPACE_S))
        self._wrapped_label(
            frame,
            textvariable=self._review_safety_var,
        ).grid(row=5, column=0, sticky="ew", pady=(0, _SPACE_S))
        return frame

    def _build_progress_frame(self) -> ttk.Frame:
        frame = ttk.Frame(self._body)
        frame.columnconfigure(0, weight=1)

        self._progress_status_var = tkinter.StringVar(self.root)
        self._wrapped_label(
            frame,
            textvariable=self._progress_status_var,
            style="Wizard.Section.TLabel",
        ).grid(row=0, column=0, sticky="ew")

        self._progress_bar = ttk.Progressbar(
            frame,
            mode="indeterminate",
            maximum=100,
        )
        self._progress_bar.grid(
            row=1,
            column=0,
            sticky="ew",
            pady=(_SPACE_M, _SPACE_M),
        )

        self._progress_detail_var = tkinter.StringVar(self.root)
        self._wrapped_label(
            frame,
            textvariable=self._progress_detail_var,
        ).grid(row=2, column=0, sticky="ew")

        self._result_actions = ttk.Frame(frame)
        self._result_actions.grid(
            row=3,
            column=0,
            sticky="w",
            pady=(_SPACE_L, 0),
        )
        self._open_folder_button = ttk.Button(
            self._result_actions,
            text=UI_COPY["open_folder"],
            command=self._open_selected_folder,
            underline=0,
        )
        self._open_folder_button.grid(
            row=0,
            column=0,
            padx=(0, _SPACE_S),
        )
        self._retry_button = ttk.Button(
            self._result_actions,
            text="Try again",
            command=self._retry,
            underline=0,
        )
        self._retry_button.grid(row=0, column=1, padx=(0, _SPACE_S))
        self._copy_button = ttk.Button(
            self._result_actions,
            text=UI_COPY["copy_diagnostics"],
            command=self._copy_diagnostics,
            underline=0,
        )
        self._copy_button.grid(row=0, column=2)
        self._result_actions.grid_remove()
        return frame

    def _on_resize(self, event: tkinter.Event[tkinter.Misc]) -> None:
        if event.widget is not self.root:
            return
        width = max(
            _MINIMUM_WRAP_WIDTH,
            event.width - (_SPACE_L * 4),
        )
        for label in self._wrapped_labels:
            label.configure(wraplength=width)

    def _schedule_poll(self) -> None:
        if not self._closed:
            self._poll_after_id = self.root.after(
                _POLL_INTERVAL_MS,
                self._poll_worker,
            )

    def _poll_worker(self) -> None:
        self._poll_after_id = None
        while not self._closed:
            try:
                event = self.worker.events.get_nowait()
            except Empty:
                break
            terminal = isinstance(
                event,
                (InstallCompleted, LocalUpdateCompleted, WorkerFailed),
            )
            state_before_event = self.controller.state
            if isinstance(event, WorkerFailed):
                if state_before_event.step is WizardStep.UPDATE:
                    self._failure_operation = "catalog"
                elif state_before_event.mode is InstallerMode.LOCAL:
                    self._failure_operation = "local"
                else:
                    self._failure_operation = "install"
                self._error_code = getattr(event.error, "code", None)
                self.controller.handle_event(event)
            elif isinstance(event, (InstallCompleted, LocalUpdateCompleted)):
                self._failure_operation = None
                self._error_code = None
                self._commit_lock_observed = False
                self.controller.handle_event(event)
            elif isinstance(event, LocationDiscoveryFailed):
                self._location_discovery_error = str(event.error)
                self._render(self.controller.state)
            elif isinstance(event, DestinationValidated):
                self._browse_pending = False
                location = event.location
                locations = tuple(
                    existing
                    for existing in self.controller.state.locations
                    if existing.save_directory != location.save_directory
                ) + (location,)
                self._browse_error_var.set("")
                self.controller.set_locations(locations)
                self.controller.select_location(location)
            elif isinstance(event, DestinationValidationFailed):
                self._browse_pending = False
                self._browse_error_var.set(
                    "That folder cannot be used. Choose the folder named "
                    f"“save”.\n{event.error}"
                )
                self._render(self.controller.state)
                self.root.after_idle(self._browse_button.focus_set)
            else:
                if isinstance(event, LocationsDiscovered):
                    self._location_discovery_error = None
                self.controller.handle_event(event)
                if isinstance(event, CatalogLoaded):
                    self._failure_operation = None
                    self.root.after_idle(
                        lambda: self._focus_for_step(WizardStep.UPDATE)
                    )
            if terminal:
                self._cancel_requested = False
                self._commit_lock_observed = False
                if self._close_pending:
                    self.close()
                    return
        self._schedule_poll()

    def _render(self, state: InstallerState) -> None:
        if self._closed:
            return
        visible_step = (
            WizardStep.PROGRESS
            if state.step is WizardStep.RESULT
            else state.step
        )
        for frame in self._frames.values():
            frame.grid_remove()
        self._frames[visible_step].grid()

        step_number = {
            WizardStep.UPDATE: 1,
            WizardStep.SAVE: 2,
            WizardStep.REVIEW: 3,
            WizardStep.PROGRESS: 4,
            WizardStep.RESULT: 4,
        }[state.step]
        self._step_var.set(f"Step {step_number} of 4")
        titles = {
            WizardStep.UPDATE: (
                "Choose local update"
                if state.mode is InstallerMode.LOCAL
                else "Choose update coverage"
            ),
            WizardStep.SAVE: "Choose save location",
            WizardStep.REVIEW: (
                "Review local update"
                if state.mode is InstallerMode.LOCAL
                else "Review installation"
            ),
            WizardStep.PROGRESS: (
                "Updating local save"
                if state.mode is InstallerMode.LOCAL
                else "Installing update"
            ),
            WizardStep.RESULT: (
                (
                    "Local update complete"
                    if state.mode is InstallerMode.LOCAL
                    else "Installation complete"
                )
                if state.result is not None
                else (
                    "Local update stopped"
                    if state.mode is InstallerMode.LOCAL
                    else "Installation stopped"
                )
            ),
        }
        self._title_var.set(titles[state.step])

        if state.step is WizardStep.UPDATE:
            self._render_update(state)
        elif state.step is WizardStep.SAVE:
            self._render_save(state)
        elif state.step is WizardStep.REVIEW:
            self._render_review(state)
        elif state.step is WizardStep.PROGRESS:
            self._render_progress(state)
        else:
            self._render_result(state)
        self._render_footer(state)

        if state.step is not self._rendered_step:
            self._rendered_step = state.step
            self.root.after_idle(lambda: self._focus_for_step(state.step))

    def _render_update(self, state: InstallerState) -> None:
        self._records_by_channel = {}
        mode_var = getattr(self, "_mode_var", None)
        local_mode = state.mode is InstallerMode.LOCAL
        if mode_var is not None:
            mode_var.set(state.mode.value)
            self._local_deep_var.set(state.local_deep)
            self._local_deep_button.configure(
                state="normal" if local_mode else "disabled"
            )
        if local_mode and mode_var is not None:
            self._catalog_status_var.set(
                "Choose Fast or Deep coverage for your existing local save."
            )
        elif state.catalog is None:
            self._catalog_status_var.set("Checking for available updates…")
        else:
            self._records_by_channel = {
                record.channel.value: record for record in state.catalog.records
            }
            if self._records_by_channel:
                self._catalog_status_var.set(
                    "Choose one update coverage option."
                )
            else:
                self._catalog_status_var.set(
                    "No compatible updates are available."
                )
        for channel, button in self._record_buttons.items():
            record = self._records_by_channel.get(channel.value)
            title = UI_COPY[f"{channel.value}_title"]
            if record is not None:
                generated_at = record.generated_at.astimezone(timezone.utc)
                title = (
                    f"{title} — Generated "
                    f"{generated_at:%Y-%m-%d %H:%M} UTC"
                )
            button.configure(
                state=(
                    "normal"
                    if record is not None and not local_mode
                    else "disabled"
                ),
                text=title,
            )
        self._record_var.set(
            state.selected_record.channel.value
            if state.selected_record is not None
            else ""
        )

    def _render_save(self, state: InstallerState) -> None:
        for label in self._location_path_labels:
            self._wrapped_labels.remove(label)
        self._location_path_labels.clear()
        for child in self._location_holder.winfo_children():
            child.destroy()
        self._locations_by_key = {}
        record = state.selected_record
        local_mode = state.mode is InstallerMode.LOCAL
        compatible_count = 0
        selected_button: ttk.Radiobutton | None = None
        for row, location in enumerate(state.locations):
            key = f"{location.target.value}|{location.save_directory}"
            self._locations_by_key[key] = location
            compatible = (
                location.edit_file.is_file()
                if local_mode
                else record is not None and location.target.value == record.target_id
            )
            if compatible:
                compatible_count += 1
            title = location.game_name
            if not compatible:
                title = (
                    f"{title} — Needs an existing EDIT00000000"
                    if local_mode
                    else f"{title} — Not compatible with this update"
                )
            button = ttk.Radiobutton(
                self._location_holder,
                text=title,
                value=key,
                variable=self._location_var,
                command=self._select_location,
                state="normal" if compatible else "disabled",
            )
            button.grid(row=row * 2, column=0, sticky="w")
            self._bind_location_scrolling(button)
            button.bind(
                "<FocusIn>",
                lambda _event, widget=button: self.root.after_idle(
                    lambda: self._ensure_location_visible(widget)
                ),
                add="+",
            )
            if location == state.selected_location:
                selected_button = button
            path_label = self._wrapped_label(
                self._location_holder,
                text=str(location.edit_file if local_mode else location.save_directory),
            )
            self._location_path_labels.append(path_label)
            path_label.grid(
                row=(row * 2) + 1,
                column=0,
                sticky="ew",
                padx=(_RADIO_DESCRIPTION_INDENT, 0),
                pady=(_SPACE_XS, _SPACE_M),
            )
            self._bind_location_scrolling(path_label)
        if state.locations:
            if local_mode:
                self._location_status_var.set(
                    f"{compatible_count} existing "
                    f"{'save' if compatible_count == 1 else 'saves'} found."
                )
            else:
                self._location_status_var.set(
                    f"{compatible_count} compatible save "
                    f"{'location' if compatible_count == 1 else 'locations'} found."
                )
        elif self._location_discovery_error is not None:
            self._location_status_var.set(
                "Save locations could not be detected automatically. "
                "Choose Browse to select one."
            )
        else:
            self._location_status_var.set(
                "No save locations were detected. Choose Browse to select one."
            )
        self._location_var.set(
            (
                f"{state.selected_location.target.value}|"
                f"{state.selected_location.save_directory}"
            )
            if state.selected_location is not None
            else ""
        )
        self._browse_button.configure(
            state="disabled" if self._browse_pending else "normal"
        )
        if selected_button is not None:
            self.root.after_idle(
                lambda: self._focus_location_button(selected_button)
            )

    def _render_review(self, state: InstallerState) -> None:
        record = state.selected_record
        location = state.selected_location
        if state.mode is InstallerMode.LOCAL:
            coverage = (
                UI_COPY["deep_title"]
                if state.local_deep
                else UI_COPY["fast_title"]
            )
            location_text = (
                str(location.edit_file)
                if location is not None
                else "Not selected"
            )
            safety = (
                f"{UI_COPY['local_safety']} "
                "Only the verified encrypted result is published."
            )
        else:
            coverage = (
                (
                    UI_COPY["fast_title"]
                    if record is not None and record.channel is Channel.FAST
                    else UI_COPY["deep_title"]
                )
                if record is not None
                else "Not selected"
            )
            location_text = (
                str(location.save_directory)
                if location is not None
                else "Not selected"
            )
            safety = ""
        self._review_coverage_var.set(coverage)
        self._review_location_var.set(location_text)
        safety_var = getattr(self, "_review_safety_var", None)
        if safety_var is not None:
            safety_var.set(safety)

    def _render_progress(self, state: InstallerState) -> None:
        self._result_actions.grid_remove()
        self._progress_bar.grid()
        presentation = progress_presentation(
            state,
            cancellation_requested=self._cancel_requested,
            commit_locked=self._commit_lock_observed,
        )
        self._progress_status_var.set(presentation.status)
        self._progress_detail_var.set(
            "Keep this window open while the save is updated."
            if not presentation.controls_locked
            else (
                "The original save is being replaced and verified. "
                "Do not close this window."
            )
        )
        if self._progress_running:
            self._progress_bar.stop()
            self._progress_running = False
        self._progress_bar.configure(
            mode=presentation.mode,
            maximum=presentation.maximum,
            value=presentation.value,
        )
        if presentation.mode == "indeterminate":
            self._progress_bar.start(_PROGRESS_PULSE_MS)
            self._progress_running = True

    def _render_result(self, state: InstallerState) -> None:
        if self._progress_running:
            self._progress_bar.stop()
            self._progress_running = False
        self._progress_bar.grid_remove()
        self._result_actions.grid()
        if state.result is not None:
            if isinstance(state.result, LocalUpdateResult):
                self._progress_status_var.set("Your local save is ready.")
                detail = f"Updated in place:\n{state.result.target_path}"
                detail += (
                    f"\n\nTransfers applied: {state.result.transfer_applied}"
                    f"\nShirt numbers changed: {state.result.shirt_numbers_changed}"
                    f"\nUnchanged: {state.result.unchanged}"
                    f"\nSafety skipped: {state.result.safety_skipped}"
                )
                if state.result.diagnostic:
                    detail += f"\n\nWarning:\n{state.result.diagnostic}"
            else:
                self._progress_status_var.set("Your save is ready.")
                detail = f"Installed to:\n{state.result.target_path}"
            if state.result.backup_path is not None:
                detail += f"\n\nBackup created at:\n{state.result.backup_path}"
            self._progress_detail_var.set(detail)
            self._open_folder_button.grid()
            self._open_folder_button.configure(
                state="normal" if sys.platform == "win32" else "disabled"
            )
            self._retry_button.grid_remove()
            self._copy_button.grid_remove()
        else:
            self._progress_status_var.set(
                state.error_title
                or (
                    "Local update could not be completed"
                    if state.mode is InstallerMode.LOCAL
                    else "Installation could not be completed"
                )
            )
            self._progress_detail_var.set(
                state.error_detail
                or "Review the details, check the save location, and try again."
            )
            self._open_folder_button.grid_remove()
            self._retry_button.grid()
            self._copy_button.grid()

    def _render_footer(self, state: InstallerState) -> None:
        back_enabled = (
            state.step in {WizardStep.SAVE, WizardStep.REVIEW}
            and not self._browse_pending
        )
        self._back_button.configure(
            state="normal" if back_enabled else "disabled"
        )
        next_enabled = False
        next_text = "Next"
        if state.step is WizardStep.UPDATE:
            next_enabled = (
                True
                if state.mode is InstallerMode.LOCAL
                else state.selected_record is not None
            )
        elif state.step is WizardStep.SAVE:
            if state.mode is InstallerMode.LOCAL:
                next_enabled = (
                    state.selected_location is not None
                    and state.selected_location.edit_file.is_file()
                    and not self._browse_pending
                )
            else:
                next_enabled = (
                    state.selected_record is not None
                    and state.selected_location is not None
                    and state.selected_location.target.value
                    == state.selected_record.target_id
                    and not self._browse_pending
                )
        elif state.step is WizardStep.REVIEW:
            next_enabled = self.controller._has_compatible_location()
            next_text = (
                UI_COPY["apply_local"]
                if state.mode is InstallerMode.LOCAL
                else UI_COPY["install"]
            )
        self._next_button.configure(
            text=next_text,
            state="normal" if next_enabled else "disabled",
        )

        if state.step is WizardStep.RESULT:
            self._cancel_button.configure(text="Close", state="normal")
        elif (
            state.step is WizardStep.PROGRESS
            and (state.commit_started or self._commit_lock_observed)
        ):
            self._cancel_button.configure(text="Cancel", state="disabled")
        elif state.step is WizardStep.PROGRESS and self._cancel_requested:
            self._cancel_button.configure(text="Cancelling…", state="disabled")
        else:
            self._cancel_button.configure(text="Cancel", state="normal")

    def _focus_for_step(self, step: WizardStep) -> None:
        if self._closed:
            return
        if step is WizardStep.UPDATE:
            if self.controller.state.mode is InstallerMode.LOCAL:
                local_button = self._mode_buttons.get(InstallerMode.LOCAL)
                if local_button is not None:
                    local_button.focus_set()
                    return
            for channel in (Channel.FAST, Channel.DEEP):
                button = self._record_buttons[channel]
                if "disabled" not in button.state():
                    button.focus_set()
                    return
        elif step is WizardStep.SAVE:
            for child in self._location_holder.winfo_children():
                if (
                    isinstance(child, ttk.Radiobutton)
                    and "disabled" not in child.state()
                ):
                    self._focus_location_button(child)
                    return
            self._browse_button.focus_set()
        elif step is WizardStep.REVIEW:
            self._next_button.focus_set()
        elif step is WizardStep.RESULT:
            self._cancel_button.focus_set()
        else:
            self._cancel_button.focus_set()

    def _select_mode(self) -> None:
        try:
            mode = InstallerMode(self._mode_var.get())
        except ValueError:
            return
        self.controller.select_mode(mode)

    def _select_local_deep(self) -> None:
        self.controller.set_local_deep(bool(self._local_deep_var.get()))

    def _select_record(self) -> None:
        record = self._records_by_channel.get(self._record_var.get())
        if record is not None:
            self.controller.select_record(record)
    def _select_location(self) -> None:
        location = self._locations_by_key.get(self._location_var.get())
        if location is not None:
            self.controller.select_location(location)

    def _browse(self) -> None:
        state = self.controller.state
        if self._browse_pending:
            return
        if state.mode is InstallerMode.LOCAL:
            target = GameTarget.LOCAL
            game_name = "Selected local save"
        else:
            record = state.selected_record
            if record is None:
                return
            try:
                target = GameTarget(record.target_id)
                if target is GameTarget.LOCAL:
                    raise ValueError(
                        "the local policy marker is only valid for local saves"
                    )
            except ValueError as error:
                self._browse_error_var.set(
                    f"The selected update target is not supported.\n{error}"
                )
                self._browse_button.focus_set()
                return
            game_name = record.target_name
        selected = filedialog.askdirectory(
            parent=self.root,
            title="Choose save folder",
            mustexist=True,
        )
        if not selected:
            return
        self._browse_pending = True
        self._browse_error_var.set("Checking selected folder…")
        self._render(self.controller.state)
        self.worker.validate_destination(
            Path(selected),
            target,
            game_name,
        )

    def _back(self) -> None:
        self._browse_error_var.set("")
        self.controller.back()

    def _next(self) -> None:
        previous = self.controller.state.step
        if not self.controller.next():
            return
        if previous is WizardStep.REVIEW:
            state = self.controller.state
            location = state.selected_location
            if location is None:
                return
            self._failure_operation = None
            self._cancel_requested = False
            self._commit_lock_observed = False
            try:
                if state.mode is InstallerMode.LOCAL:
                    self.worker.start_local_update(
                        location,
                        deep=state.local_deep,
                    )
                else:
                    record = state.selected_record
                    if record is None:
                        return
                    self.worker.install(record, location)
            except Exception as error:
                self._failure_operation = (
                    "local" if state.mode is InstallerMode.LOCAL else "install"
                )
                self._error_code = getattr(error, "code", None)
                self.controller.fail(error)

    def _retry(self) -> None:
        if self._failure_operation == "catalog":
            if not self.controller.retry_catalog():
                return
            self._failure_operation = None
            self._error_code = None
            self.worker.load_catalog()
            self.worker.discover_locations()
            return
        if not self.controller.retry():
            return
        state = self.controller.state
        location = state.selected_location
        if location is None:
            return
        self._failure_operation = None
        self._error_code = None
        self._cancel_requested = False
        self._commit_lock_observed = False
        try:
            if state.mode is InstallerMode.LOCAL:
                self.worker.start_local_update(
                    location,
                    deep=state.local_deep,
                )
            else:
                record = state.selected_record
                if record is None:
                    return
                self.worker.install(record, location)
        except Exception as error:
            self._failure_operation = (
                "local" if state.mode is InstallerMode.LOCAL else "install"
            )
            self._error_code = getattr(error, "code", None)
            self.controller.fail(error)

    def _request_cancel(self, *, close_after: bool) -> None:
        state = self.controller.state
        if state.step is not WizardStep.PROGRESS:
            self.close()
            return
        if self.worker.cancel():
            self._close_pending = self._close_pending or close_after
            self._cancel_requested = True
            self._commit_lock_observed = False
        else:
            self._cancel_requested = False
            self._commit_lock_observed = True
        self._render(state)

    def _cancel_or_close(self) -> None:
        self._request_cancel(close_after=False)

    def _on_window_close(self) -> None:
        self._request_cancel(close_after=True)

    def _on_escape(self, _event: tkinter.Event[tkinter.Misc]) -> str:
        self._cancel_or_close()
        return "break"

    def _open_selected_folder(self) -> None:
        location = self.controller.state.selected_location
        if location is None:
            return
        if not open_save_folder(location.save_directory):
            self._progress_detail_var.set(
                f"Open this folder in File Explorer:\n{location.save_directory}"
            )

    def _copy_diagnostics(self) -> None:
        details = diagnostic_details(
            self.controller.state,
            error_code=self._error_code,
        )
        self.root.clipboard_clear()
        self.root.clipboard_append(details)
        self.root.update_idletasks()
        self._progress_detail_var.set(
            f"{self.controller.state.error_detail or ''}\n\n"
            "Diagnostic details copied to clipboard."
        )

    def close(self) -> None:
        """Orderly, idempotent shutdown used by every terminal close path."""

        if self._closed:
            return
        self._closed = True
        if self._poll_after_id is not None:
            try:
                self.root.after_cancel(self._poll_after_id)
            except tkinter.TclError:
                pass
            self._poll_after_id = None
        self.worker.close(timeout=0.0)
        try:
            self.root.destroy()
        except tkinter.TclError:
            pass


def run_gui() -> int:
    _load_tkinter()
    _enable_native_dpi_awareness()
    root = tkinter.Tk()
    application = InstallerApplication(root)
    try:
        root.mainloop()
    finally:
        application.close()
    return 0


def self_test() -> int:
    _load_tkinter()
    root = tkinter.Tk()
    root.withdraw()
    root.update_idletasks()
    root.destroy()
    return 0