from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Callable

from installer.catalog import Catalog, Channel, ReleaseRecord
from installer.install import InstallResult
from installer.paths import GameTarget, SaveLocation
from local_update import LocalUpdateProgress, LocalUpdateResult

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
        selected_record = (
            None
            if mode is InstallerMode.LOCAL
            else self.state.selected_record
        )
        selected_location = self.state.selected_location
        if not self._location_matches(
            selected_location,
            mode=mode,
            record=selected_record,
        ):
            selected_location = self._first_compatible_location(
                self.state.locations,
                mode=mode,
                record=selected_record,
            )
        return self._publish(
            replace(
                self.state,
                mode=mode,
                selected_record=selected_record,
                selected_location=selected_location,
                local_edit_file=(
                    selected_location.edit_file
                    if mode is InstallerMode.LOCAL and selected_location is not None
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
        if selected is None and self.state.mode is InstallerMode.RELEASE:
            selected = next(
                (
                    record
                    for record in catalog.records
                    if (
                        record.channel is Channel.FAST
                        and record.target_id != GameTarget.LOCAL.value
                    )
                ),
                None,
            )
        selected_location = self.state.selected_location
        if not self._location_matches(
            selected_location,
            mode=self.state.mode,
            record=selected,
        ):
            selected_location = self._first_compatible_location(
                self.state.locations,
                mode=self.state.mode,
                record=selected,
            )
        return self._publish(
            replace(
                self.state,
                catalog=catalog,
                selected_record=selected,
                selected_location=selected_location,
                local_edit_file=(
                    selected_location.edit_file
                    if self.state.mode is InstallerMode.LOCAL
                    and selected_location is not None
                    else None
                ),
            )
        )

    def set_local_deep(self, deep: bool) -> bool:
        if (
            self.state.step is not WizardStep.UPDATE
            or self.state.mode is not InstallerMode.LOCAL
        ):
            return False
        return self._publish(replace(self.state, local_deep=bool(deep)))

    def set_locations(self, locations: tuple[SaveLocation, ...]) -> bool:
        available = tuple(locations)
        selected = self.state.selected_location
        if selected is not None:
            selected = next(
                (location for location in available if location == selected), None
            )
        if selected is None:
            selected = self._first_compatible_location(
                available,
                mode=self.state.mode,
                record=self.state.selected_record,
            )
        return self._publish(
            replace(
                self.state,
                locations=available,
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
        selected_location = self.state.selected_location
        if not self._location_matches(
            selected_location,
            mode=InstallerMode.RELEASE,
            record=selected,
        ):
            selected_location = self._first_compatible_location(
                self.state.locations,
                mode=InstallerMode.RELEASE,
                record=selected,
            )
        return self._publish(
            replace(
                self.state,
                mode=InstallerMode.RELEASE,
                selected_record=selected,
                selected_location=selected_location,
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

    @staticmethod
    def _location_matches(
        location: SaveLocation | None,
        *,
        mode: InstallerMode,
        record: ReleaseRecord | None,
    ) -> bool:
        if location is None:
            return False
        if mode is InstallerMode.LOCAL:
            return location.edit_file.is_file()
        return (
            record is not None
            and record.target_id != GameTarget.LOCAL.value
            and location.target.value == record.target_id
        )

    @classmethod
    def _first_compatible_location(
        cls,
        locations: tuple[SaveLocation, ...],
        *,
        mode: InstallerMode,
        record: ReleaseRecord | None,
    ) -> SaveLocation | None:
        return next(
            (
                location
                for location in locations
                if cls._location_matches(location, mode=mode, record=record)
            ),
            None,
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
        if (
            location is None
            or not any(candidate == location for candidate in self.state.locations)
        ):
            return False
        if self.state.mode is InstallerMode.LOCAL:
            return self._location_matches(
                location,
                mode=InstallerMode.LOCAL,
                record=None,
            )
        return (
            self._has_valid_record()
            and self._location_matches(
                location,
                mode=InstallerMode.RELEASE,
                record=self.state.selected_record,
            )
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
