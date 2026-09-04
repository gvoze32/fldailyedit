from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
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
from installer.paths import (
    DestinationError,
    GameTarget,
    SaveLocation,
    discover_save_locations,
    validate_destination,
)
from installer.state import (
    CatalogLoaded,
    DestinationValidated,
    DestinationValidationFailed,
    InstallCompleted,
    LocalProgressChanged,
    LocalSaveSelected,
    LocalUpdateCompleted,
    LocationDiscoveryFailed,
    LocationsDiscovered,
    ProgressChanged,
    WorkerEvent,
    WorkerFailed,
)
from local_update import (
    CancellationToken,
    LocalUpdateProgress,
    LocalUpdateRequest,
    LocalUpdateResult,
    LocalUpdateService,
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
                from run_pipeline import build_local_update_service

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

