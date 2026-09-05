from __future__ import annotations

import hashlib
import json
import threading
import zipfile

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from queue import Empty, SimpleQueue
from threading import Event, get_ident, main_thread
from time import monotonic, sleep
from typing import Iterator
from urllib.parse import urlsplit
from urllib.request import urlopen

import pytest
from installer import app as installer_app

from installer.state import (
    CatalogLoaded,
    DestinationValidated,
    DestinationValidationFailed,
    InstallCompleted,
    InstallerController,
    InstallerMode,
    InstallerState,
    LocalProgressChanged,
    LocalUpdateCompleted,
    LocationsDiscovered,
    ProgressChanged,
    WizardStep,
    WorkerFailed,
    error_copy,
)
from installer.worker import InstallerWorker
from installer.catalog import (
    Catalog,
    CatalogError,
    Channel,
    DownloadError,
    ReleaseRecord,
    download_archive,
    fetch_catalog,
)
from installer.install import InstallError, InstallResult, InstallStage
from local_update import (
    CancellationToken,
    LocalUpdateError,
    LocalUpdateProgress,
    LocalUpdateRequest,
    LocalUpdateResult,
    LocalUpdateStage,
)
from installer.paths import DestinationError, GameTarget, SaveLocation


GENERATED_AT = datetime(2026, 8, 6, tzinfo=timezone.utc)


def _record(
    channel: Channel = Channel.FAST,
    *,
    target_id: str = GameTarget.FL26.value,
    generated_at: datetime = GENERATED_AT,
) -> ReleaseRecord:
    return ReleaseRecord(
        target_id=target_id,
        target_name="Football Life 2026",
        channel=channel,
        generated_at=generated_at,
        asset_name=f"fldailyedit-{channel.value}.zip",
        download_url=f"https://github.com/example/{channel.value}.zip",
        archive_size=100,
        archive_sha256="a" * 64,
        save_size=200,
        save_sha256="b" * 64,
    )


def _location(target: GameTarget, name: str) -> SaveLocation:
    return SaveLocation(target, name, Path("C:/Users/Test") / name / "save")


def _controller_with_choices() -> tuple[
    InstallerController, ReleaseRecord, ReleaseRecord, SaveLocation, SaveLocation
]:
    fast = _record(Channel.FAST)
    deep = _record(Channel.DEEP)
    fl_location = _location(GameTarget.FL26, "FL")
    pes_location = _location(GameTarget.PES2021, "PES")
    controller = InstallerController()
    controller.set_catalog(Catalog(1, (fast, deep)))
    controller.set_locations((fl_location, pes_location))
    return controller, fast, deep, fl_location, pes_location


def test_state_is_immutable_and_each_change_is_published_once() -> None:
    changes: list[InstallerState] = []
    controller = InstallerController(on_change=changes.append)
    fast = _record()

    controller.set_catalog(Catalog(1, (fast,)))

    assert changes == [controller.state]
    assert controller.state.catalog == Catalog(1, (fast,))
    try:
        controller.state.step = WizardStep.SAVE  # type: ignore[misc]
    except (AttributeError, TypeError):
        pass
    else:
        raise AssertionError("InstallerState must be frozen")


def test_fast_release_is_selected_by_default() -> None:
    controller, fast, _, _, _ = _controller_with_choices()

    assert controller.state.selected_record is fast
    assert controller.next() is True
    assert controller.state.step is WizardStep.SAVE

def test_fast_is_default_and_deep_remains_selectable() -> None:
    controller, fast, deep, _, _ = _controller_with_choices()

    assert controller.state.selected_record is fast
    assert controller.select_record(deep) is True
    assert controller.state.selected_record is deep


def test_first_compatible_location_is_selected_by_default() -> None:
    controller, _, _, fl_location, pes_location = _controller_with_choices()
    controller.next()

    assert controller.state.locations == (fl_location, pes_location)
    assert controller.state.selected_location is fl_location
    assert controller.next() is True
    assert controller.state.step is WizardStep.REVIEW

def test_incompatible_pes_location_is_visible_but_cannot_continue() -> None:
    controller, fast, _, _, pes_location = _controller_with_choices()
    controller.select_record(fast)
    controller.next()

    assert controller.select_location(pes_location) is True
    assert controller.state.selected_location is pes_location
    assert controller.next() is False
    assert controller.state.step is WizardStep.SAVE


def test_compatible_future_pes_record_can_continue_fail_closed() -> None:
    pes_record = _record(target_id=GameTarget.PES2021.value)
    pes_location = _location(GameTarget.PES2021, "PES")
    controller = InstallerController()
    controller.set_catalog(Catalog(1, (pes_record,)))
    controller.set_locations((pes_location,))

    assert controller.state.selected_record is pes_record
    assert controller.next() is True
    assert controller.state.selected_location is pes_location
    assert controller.next() is True
    assert controller.state.step is WizardStep.REVIEW


def test_back_preserves_valid_record_and_location_choices() -> None:
    controller, fast, _, fl_location, _ = _controller_with_choices()
    controller.select_record(fast)
    controller.next()
    controller.select_location(fl_location)
    controller.next()

    assert controller.back() is True
    assert controller.state.step is WizardStep.SAVE
    assert controller.state.selected_record is fast
    assert controller.state.selected_location is fl_location
    assert controller.back() is True
    assert controller.state.step is WizardStep.UPDATE
    assert controller.state.selected_record is fast
    assert controller.state.selected_location is fl_location


def test_invalid_choices_are_rejected_without_publishing_a_change() -> None:
    changes: list[InstallerState] = []
    controller, _, _, _, _ = _controller_with_choices()
    controller.on_change = changes.append

    assert controller.select_record(_record(target_id=GameTarget.PES2021.value)) is False
    assert controller.select_location(_location(GameTarget.FL26, "Other")) is False
    assert changes == []


def test_release_controller_rejects_local_policy_target() -> None:
    local_record = _record(target_id=GameTarget.LOCAL.value)
    controller = InstallerController()
    controller.set_catalog(Catalog(1, (local_record,)))

    assert controller.select_record(local_record) is False
    assert controller.state.selected_record is None


def test_release_compatibility_rejects_local_policy_target(tmp_path: Path) -> None:
    local_record = _record(target_id=GameTarget.LOCAL.value)
    save = tmp_path / "save"
    save.mkdir()
    (save / "EDIT00000000").write_bytes(b"local-policy")
    location = SaveLocation(GameTarget.LOCAL, "Selected local save", save)
    controller = InstallerController(
        state=InstallerState(
            catalog=Catalog(1, (local_record,)),
            selected_record=local_record,
            locations=(location,),
            selected_location=location,
        )
    )

    assert controller._has_compatible_location() is False


def test_review_starts_progress_with_same_choices() -> None:
    controller, fast, _, fl_location, _ = _controller_with_choices()
    controller.select_record(fast)
    controller.next()
    controller.select_location(fl_location)
    controller.next()

    assert controller.next() is True
    assert controller.state.step is WizardStep.PROGRESS
    assert controller.state.selected_record is fast
    assert controller.state.selected_location is fl_location
    assert controller.state.commit_started is False


def test_success_and_error_finish_at_result() -> None:
    controller, fast, _, fl_location, _ = _controller_with_choices()
    controller.select_record(fast)
    controller.next()
    controller.select_location(fl_location)
    controller.next()
    controller.next()
    result = InstallResult(Path("save/EDIT00000000"), None, "b" * 64)

    assert controller.succeed(result) is True
    assert controller.state.step is WizardStep.RESULT
    assert controller.state.result is result

    failed, fast, _, fl_location, _ = _controller_with_choices()
    failed.select_record(fast)
    failed.next()
    failed.select_location(fl_location)
    failed.next()
    failed.next()
    error = InstallError("target_locked", "sharing violation 32", stage=InstallStage.REPLACING)

    assert failed.fail(error) is True
    assert failed.state.step is WizardStep.RESULT
    assert failed.state.error_title == "Close the game and try again"
    assert failed.state.error_detail == "sharing violation 32"


def test_retry_returns_to_progress_with_the_same_choices() -> None:
    controller, fast, _, fl_location, _ = _controller_with_choices()
    controller.select_record(fast)
    controller.next()
    controller.select_location(fl_location)
    controller.next()
    controller.next()
    controller.fail(DownloadError("timeout", "socket timed out after 30 seconds"))

    assert controller.retry() is True
    assert controller.state.step is WizardStep.PROGRESS
    assert controller.state.selected_record is fast
    assert controller.state.selected_location is fl_location
    assert controller.state.error_title is None
    assert controller.state.error_detail is None
    assert controller.state.commit_started is False


def test_retry_is_rejected_after_success() -> None:
    controller, fast, _, fl_location, _ = _controller_with_choices()
    controller.select_record(fast)
    controller.next()
    controller.select_location(fl_location)
    controller.next()
    controller.next()
    controller.succeed(InstallResult(Path("save/EDIT00000000"), None, "b" * 64))

    assert controller.retry() is False
    assert controller.state.step is WizardStep.RESULT


def test_error_copy_maps_stable_codes_and_keeps_diagnostic_text() -> None:
    cases = (
        (CatalogError("network_error", "DNS lookup failed"), "No internet connection"),
        (
            DownloadError("checksum_mismatch", "expected aaaa, received bbbb"),
            "Downloaded file failed verification",
        ),
        (
            InstallError("target_locked", "sharing violation 32", stage=InstallStage.REPLACING),
            "Close the game and try again",
        ),
        (
            CatalogError("unavailable_channel", "no release for pes2021-vanilla"),
            "This save is not available for the selected game",
        ),
        (
            LocalUpdateError(
                "scrape_failed",
                "Could not fetch update data: deep-club index is empty",
                stage=LocalUpdateStage.SCRAPING,
            ),
            "Could not fetch update data",
        ),
    )

    for error, expected_title in cases:
        assert error_copy(error) == (expected_title, str(error))


def _next_event(
    worker: InstallerWorker,
    event_type: type[CatalogLoaded]
    | type[InstallCompleted]
    | type[ProgressChanged]
    | type[WorkerFailed],
    *,
    timeout: float = 2.0,
) -> CatalogLoaded | InstallCompleted | ProgressChanged | WorkerFailed:
    deadline = monotonic() + timeout
    while True:
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise AssertionError(f"timed out waiting for {event_type.__name__}")
        try:
            event = worker.events.get(timeout=remaining)
        except Empty as error:
            raise AssertionError(
                f"timed out waiting for {event_type.__name__}"
            ) from error
        if isinstance(event, event_type):
            return event


def test_worker_rejects_local_policy_target_from_release_install(
    monkeypatch, tmp_path: Path
) -> None:
    local_record = _record(target_id=GameTarget.LOCAL.value)
    location = SaveLocation(
        GameTarget.LOCAL,
        "Selected local save",
        tmp_path / "save",
    )
    worker = InstallerWorker()
    monkeypatch.setattr(worker, "start", lambda: None)

    with pytest.raises(ValueError, match="incompatible"):
        worker.install(local_record, location)

    assert worker._install_pending is False


def _progress_controller(
    on_change: object | None = None,
) -> tuple[InstallerController, ReleaseRecord, SaveLocation]:
    fast = _record()
    location = _location(GameTarget.FL26, "FL")
    callback = on_change if callable(on_change) else None
    controller = InstallerController(on_change=callback)
    controller.set_catalog(Catalog(1, (fast,)))
    controller.set_locations((location,))
    controller.select_record(fast)
    controller.next()
    controller.select_location(location)
    controller.next()
    controller.next()
    return controller, fast, location


def test_worker_uses_one_daemon_thread_and_a_typed_simple_queue() -> None:
    catalog = Catalog(1, (_record(),))
    worker_thread_ids: list[int] = []

    def fake_fetch_catalog() -> Catalog:
        worker_thread_ids.append(get_ident())
        return catalog

    worker = InstallerWorker(fetch_catalog=fake_fetch_catalog)
    try:
        worker.load_catalog()
        first = _next_event(worker, CatalogLoaded)
        thread = worker.thread
        worker.load_catalog()
        second = _next_event(worker, CatalogLoaded)

        assert isinstance(worker.events, SimpleQueue)
        assert first == CatalogLoaded(catalog)
        assert second == CatalogLoaded(catalog)
        assert thread is worker.thread
        assert thread is not None
        assert thread.daemon is True
        assert set(worker_thread_ids) == {thread.ident}
        assert thread.ident != main_thread().ident
    finally:
        worker.close()


def test_worker_cleans_temp_directory_and_controller_polls_events_on_main_thread(
    tmp_path: Path,
) -> None:
    callback_thread_ids: list[int] = []
    worker_thread_ids: list[int] = []
    temporary_directories: list[Path] = []
    controller, record, _ = _progress_controller(
        lambda _state: callback_thread_ids.append(get_ident())
    )
    callback_thread_ids.clear()
    save_directory = tmp_path / "save"
    save_directory.mkdir()
    location = SaveLocation(GameTarget.FL26, "Football Life 2026", save_directory)
    result = InstallResult(save_directory / "EDIT00000000", None, "b" * 64)

    def fake_download(
        selected: ReleaseRecord,
        destination: Path,
        *,
        progress: object,
        cancelled: object,
    ) -> None:
        worker_thread_ids.append(get_ident())
        temporary_directories.append(destination.parent)
        assert selected is record
        assert callable(progress)
        assert callable(cancelled)
        progress(40, 100)
        destination.write_bytes(b"archive")

    def fake_install(
        archive_path: Path,
        destination: Path,
        selected: ReleaseRecord,
        *,
        now: object,
        progress: object,
        cancelled: object,
    ) -> InstallResult:
        worker_thread_ids.append(get_ident())
        assert archive_path.parent == temporary_directories[0]
        assert archive_path.read_bytes() == b"archive"
        assert destination == save_directory
        assert selected is record
        assert callable(now)
        assert callable(progress)
        assert callable(cancelled)
        progress(InstallStage.STAGING)
        progress(InstallStage.REPLACING)
        return result

    worker = InstallerWorker(
        download_archive=fake_download,
        install_archive=fake_install,
    )
    observed: list[ProgressChanged | InstallCompleted | WorkerFailed] = []
    try:
        worker.install(record, location)
        deadline = monotonic() + 2.0
        while monotonic() < deadline:
            event = worker.events.get(timeout=deadline - monotonic())
            observed.append(event)
            controller.handle_event(event)
            if isinstance(event, InstallCompleted):
                break
        else:
            raise AssertionError("worker did not complete")

        assert controller.state.step is WizardStep.RESULT
        assert controller.state.result is result
        assert [event.stage for event in observed if isinstance(event, ProgressChanged)] == [
            "downloading",
            InstallStage.STAGING.value,
            InstallStage.REPLACING.value,
        ]
        replacing = [
            event
            for event in observed
            if isinstance(event, ProgressChanged)
            and event.stage == InstallStage.REPLACING.value
        ]
        assert replacing == [ProgressChanged(InstallStage.REPLACING.value, 0, 0, True)]
        assert temporary_directories and not temporary_directories[0].exists()
        assert set(worker_thread_ids) == {worker.thread.ident}
        assert worker.thread.ident != main_thread().ident
        assert callback_thread_ids
        assert set(callback_thread_ids) == {main_thread().ident}
    finally:
        worker.close()


def test_worker_honors_cancellation_before_commit_and_removes_temp_directory(
    tmp_path: Path,
) -> None:
    started = Event()
    cancellation_observed = Event()
    temporary_directories: list[Path] = []
    install_calls: list[Path] = []
    save_directory = tmp_path / "save"
    save_directory.mkdir()
    record = _record()
    location = SaveLocation(GameTarget.FL26, "Football Life 2026", save_directory)

    def blocking_download(
        _record: ReleaseRecord,
        destination: Path,
        *,
        progress: object,
        cancelled: object,
    ) -> None:
        temporary_directories.append(destination.parent)
        destination.write_bytes(b"partial")
        started.set()
        assert callable(cancelled)
        while not cancelled():
            sleep(0.001)
        cancellation_observed.set()
        raise DownloadError("cancelled", "download cancelled by user")

    def unexpected_install(
        archive_path: Path,
        _destination: Path,
        _record: ReleaseRecord,
        **_kwargs: object,
    ) -> InstallResult:
        install_calls.append(archive_path)
        raise AssertionError("install must not start after cancellation")

    worker = InstallerWorker(
        download_archive=blocking_download,
        install_archive=unexpected_install,
    )
    try:
        worker.install(record, location)
        assert started.wait(2.0)
        assert worker.cancel() is True
        failure = _next_event(worker, WorkerFailed)

        assert cancellation_observed.wait(2.0)
        assert isinstance(failure.error, DownloadError)
        assert failure.error.code == "cancelled"
        assert install_calls == []
        assert temporary_directories and not temporary_directories[0].exists()
    finally:
        worker.close()


def test_commit_boundary_is_propagated_and_cancellation_is_ignored_after_it(
    tmp_path: Path,
) -> None:
    at_commit = Event()
    continue_after_cancel = Event()
    cancellation_values: list[bool] = []
    save_directory = tmp_path / "save"
    save_directory.mkdir()
    record = _record()
    location = SaveLocation(GameTarget.FL26, "Football Life 2026", save_directory)
    result = InstallResult(save_directory / "EDIT00000000", None, "b" * 64)

    def fake_download(
        _record: ReleaseRecord,
        destination: Path,
        *,
        progress: object,
        cancelled: object,
    ) -> None:
        destination.write_bytes(b"archive")

    def boundary_install(
        _archive_path: Path,
        _destination: Path,
        _record: ReleaseRecord,
        *,
        now: object,
        progress: object,
        cancelled: object,
    ) -> InstallResult:
        assert callable(progress)
        assert callable(cancelled)
        cancellation_values.append(cancelled())
        progress(InstallStage.REPLACING)
        at_commit.set()
        assert continue_after_cancel.wait(2.0)
        cancellation_values.append(cancelled())
        return result

    worker = InstallerWorker(
        download_archive=fake_download,
        install_archive=boundary_install,
    )
    try:
        worker.install(record, location)
        assert at_commit.wait(2.0)
        assert worker.cancel() is False
        continue_after_cancel.set()
        completed = _next_event(worker, InstallCompleted)

        assert completed == InstallCompleted(result)
        assert cancellation_values == [False, False]
    finally:
        worker.close()


def test_cancellation_wins_if_requested_before_replacing_handoff(
    tmp_path: Path,
) -> None:
    before_replacing = Event()
    continue_to_replacing = Event()
    replacement_started = Event()
    save_directory = tmp_path / "save"
    save_directory.mkdir()
    record = _record()
    location = SaveLocation(GameTarget.FL26, "Football Life 2026", save_directory)
    result = InstallResult(save_directory / "EDIT00000000", None, "b" * 64)

    def fake_download(
        _record: ReleaseRecord,
        destination: Path,
        *,
        progress: object,
        cancelled: object,
    ) -> None:
        destination.write_bytes(b"archive")

    def install_with_precommit_window(
        _archive_path: Path,
        _destination: Path,
        _record: ReleaseRecord,
        *,
        now: object,
        progress: object,
        cancelled: object,
    ) -> InstallResult:
        assert callable(progress)
        assert callable(cancelled)
        assert cancelled() is False
        before_replacing.set()
        assert continue_to_replacing.wait(2.0)
        progress(InstallStage.REPLACING)
        replacement_started.set()
        return result

    worker = InstallerWorker(
        download_archive=fake_download,
        install_archive=install_with_precommit_window,
    )
    observed: list[ProgressChanged | InstallCompleted | WorkerFailed] = []
    try:
        worker.install(record, location)
        assert before_replacing.wait(2.0)
        worker.cancel()
        continue_to_replacing.set()
        deadline = monotonic() + 2.0
        while monotonic() < deadline:
            event = worker.events.get(timeout=deadline - monotonic())
            observed.append(event)
            if isinstance(event, (InstallCompleted, WorkerFailed)):
                break
        else:
            raise AssertionError("worker did not emit a terminal event")

        assert isinstance(observed[-1], WorkerFailed)
        assert isinstance(observed[-1].error, InstallError)
        assert observed[-1].error.code == "cancelled"
        assert observed[-1].error.stage is InstallStage.REPLACING
        assert replacement_started.is_set() is False
        assert not any(
            isinstance(event, ProgressChanged)
            and event.stage == InstallStage.REPLACING.value
            for event in observed
        )
    finally:
        continue_to_replacing.set()
        worker.close()


def test_terminal_event_is_published_after_pending_state_is_cleared(
    tmp_path: Path,
) -> None:
    class TerminalGateQueue:
        def __init__(self) -> None:
            self._events: SimpleQueue[
                CatalogLoaded | InstallCompleted | ProgressChanged | WorkerFailed
            ] = SimpleQueue()
            self.terminal_published = Event()
            self.release_publisher = Event()

        def put(
            self,
            event: CatalogLoaded | InstallCompleted | ProgressChanged | WorkerFailed,
        ) -> None:
            self._events.put(event)
            if isinstance(event, (InstallCompleted, WorkerFailed)):
                self.terminal_published.set()
                assert self.release_publisher.wait(2.0)

        def get(
            self,
            block: bool = True,
            timeout: float | None = None,
        ) -> CatalogLoaded | InstallCompleted | ProgressChanged | WorkerFailed:
            return self._events.get(block=block, timeout=timeout)

    save_directory = tmp_path / "save"
    save_directory.mkdir()
    record = _record()
    location = SaveLocation(GameTarget.FL26, "Football Life 2026", save_directory)
    result = InstallResult(save_directory / "EDIT00000000", None, "b" * 64)

    def fake_download(
        _record: ReleaseRecord,
        destination: Path,
        *,
        progress: object,
        cancelled: object,
    ) -> None:
        destination.write_bytes(b"archive")

    def fake_install(
        _archive_path: Path,
        _destination: Path,
        _record: ReleaseRecord,
        **_kwargs: object,
    ) -> InstallResult:
        return result

    worker = InstallerWorker(
        download_archive=fake_download,
        install_archive=fake_install,
    )
    gate = TerminalGateQueue()
    worker.events = gate  # type: ignore[assignment]
    try:
        worker.install(record, location)
        first = gate.get(timeout=2.0)
        assert first == InstallCompleted(result)
        assert gate.terminal_published.wait(2.0)

        worker.install(record, location)
        gate.release_publisher.set()
        second = gate.get(timeout=2.0)

        assert second == InstallCompleted(result)
    finally:
        gate.release_publisher.set()
        worker.close()


def test_exact_english_ui_copy_is_available_without_rendering() -> None:
    assert {
        "Fast — Recommended",
        "Standard daily update from the live transfer feed.",
        "Deep — Expanded coverage",
        "Checks every locally indexed FotMob club for maximum coverage.",
        "Fast and Deep describe update coverage, not download speed.",
        "Download and install",
        "Close the game before continuing.",
        "Open save folder",
        "Copy diagnostic details",
    } <= set(installer_app.UI_COPY.values())


def test_entry_point_supports_launch_version_self_test_and_rejects_unknown(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from installer import __main__ as entry_point

    calls: list[str] = []
    monkeypatch.setattr(
        entry_point, "run_gui", lambda: calls.append("launch") or 0
    )
    monkeypatch.setattr(
        entry_point, "self_test", lambda: calls.append("self-test") or 0
    )

    assert entry_point.main([]) == 0
    assert calls == ["launch"]
    assert entry_point.main(["--self-test"]) == 0
    assert calls == ["launch", "self-test"]
    assert entry_point.main(["--version"]) == 0
    assert capsys.readouterr().out == "0.1.0\n"
    with pytest.raises(SystemExit) as error:
        entry_point.main(["--unknown"])
    assert error.value.code != 0


def test_progress_presentation_switches_mode_and_locks_at_commit() -> None:
    downloading = InstallerState(
        step=WizardStep.PROGRESS,
        progress_stage="downloading",
        progress_downloaded=25,
        progress_total=100,
    )
    download_view = installer_app.progress_presentation(downloading)
    assert download_view.mode == "determinate"
    assert download_view.maximum == 100
    assert download_view.value == 25
    assert download_view.controls_locked is False
    assert download_view.status == "Downloading update… 25%"

    replacing = InstallerState(
        step=WizardStep.PROGRESS,
        progress_stage=InstallStage.REPLACING.value,
        commit_started=True,
    )
    commit_view = installer_app.progress_presentation(replacing)
    assert commit_view.mode == "indeterminate"
    assert commit_view.controls_locked is True
    assert commit_view.status == "Finishing installation safely…"


def test_release_completion_renders_result_instead_of_leaving_commit_screen(
    tmp_path: Path,
) -> None:
    class ViewDouble:
        def __init__(self) -> None:
            self.value = ""
            self.visible = False
            self.options: dict[str, str] = {}

        def set(self, value: str) -> None:
            self.value = value

        def grid(self) -> None:
            self.visible = True

        def grid_remove(self) -> None:
            self.visible = False

        def configure(self, **options: str) -> None:
            self.options.update(options)

    target = tmp_path / "save" / "EDIT00000000"
    log_path = tmp_path / "save" / "FLDailyEditLogs" / "transfer-log.md"
    application = object.__new__(installer_app.InstallerApplication)
    application._progress_running = False
    application._progress_bar = ViewDouble()
    application._result_actions = ViewDouble()
    application._progress_status_var = ViewDouble()
    application._progress_detail_var = ViewDouble()
    application._open_folder_button = ViewDouble()
    application._retry_button = ViewDouble()
    application._copy_button = ViewDouble()
    state = InstallerState(
        step=WizardStep.RESULT,
        result=InstallResult(target, None, "a" * 64, log_path),
    )

    application._render_result(state)

    assert application._progress_status_var.value == "Your save is ready."
    assert str(target) in application._progress_detail_var.value
    assert str(log_path) in application._progress_detail_var.value
    assert application._result_actions.visible is True


def test_close_disposition_cancels_before_commit_and_blocks_during_commit() -> None:
    before_commit = InstallerState(step=WizardStep.PROGRESS)
    at_commit = InstallerState(step=WizardStep.PROGRESS, commit_started=True)

    assert (
        installer_app.close_disposition(before_commit)
        is installer_app.CloseDisposition.CANCEL_AND_WAIT
    )
    assert (
        installer_app.close_disposition(at_commit)
        is installer_app.CloseDisposition.BLOCK
    )
    assert (
        installer_app.close_disposition(InstallerState())
        is installer_app.CloseDisposition.CLOSE
    )


def test_diagnostics_include_only_version_stage_code_and_selected_path() -> None:
    location = _location(GameTarget.FL26, "Football Life 2026")
    state = InstallerState(
        step=WizardStep.RESULT,
        selected_location=location,
        progress_stage=InstallStage.VERIFYING_ARCHIVE.value,
        error_title="Downloaded file failed verification",
        error_detail="payload secret must not be copied",
    )

    assert installer_app.diagnostic_details(
        state, error_code="checksum_mismatch"
    ) == (
        "FLDailyEdit Installer 0.1.0\n"
        "Stage: verifying_archive\n"
        "Code: checksum_mismatch\n"
        f"Selected path: {location.save_directory}"
    )


def test_open_save_folder_is_platform_guarded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[Path] = []
    monkeypatch.setattr(installer_app.sys, "platform", "darwin")
    monkeypatch.setattr(
        installer_app.os,
        "startfile",
        lambda path: opened.append(Path(path)),
        raising=False,
    )

    assert installer_app.open_save_folder(Path("/tmp/save")) is False
    assert opened == []

    monkeypatch.setattr(installer_app.sys, "platform", "win32")
    assert installer_app.open_save_folder(Path("C:/save")) is True
    assert opened == [Path("C:/save")]


def test_orderly_application_shutdown_closes_worker_before_destroying_root() -> None:
    actions: list[str] = []

    class Root:
        def after_cancel(self, _identifier: str) -> None:
            actions.append("cancel-poll")

        def destroy(self) -> None:
            actions.append("destroy-root")

    class Worker:
        def close(self, timeout: float | None = 2.0) -> None:
            actions.append(f"close-worker:{timeout}")

    application = object.__new__(installer_app.InstallerApplication)
    application.root = Root()
    application.worker = Worker()
    application._poll_after_id = "poll"
    application._closed = False

    application.close()

    assert actions == ["cancel-poll", "close-worker:0.0", "destroy-root"]


def test_catalog_failure_retry_reloads_catalog_and_locations() -> None:
    controller = InstallerController()
    assert controller.fail(CatalogError("network_error", "offline"))
    calls: list[str] = []

    class Worker:
        def load_catalog(self) -> None:
            calls.append("catalog")

        def discover_locations(self) -> None:
            calls.append("locations")

    application = object.__new__(installer_app.InstallerApplication)
    application.controller = controller
    application.worker = Worker()
    application._failure_operation = "catalog"
    application._error_code = "network_error"
    application._cancel_requested = False

    application._retry()

    assert controller.state.step is WizardStep.UPDATE
    assert controller.state.error_title is None
    assert calls == ["catalog", "locations"]


def test_worker_runs_location_discovery_and_browse_validation_off_main_thread(
    tmp_path: Path,
) -> None:
    save_directory = tmp_path / "save"
    save_directory.mkdir()
    location = SaveLocation(
        GameTarget.FL26,
        "Football Life 2026",
        save_directory,
    )
    operation_thread_ids: list[int] = []

    def fake_discovery() -> tuple[SaveLocation, ...]:
        operation_thread_ids.append(get_ident())
        return (location,)

    def fake_validation(path: Path, target: GameTarget) -> Path:
        operation_thread_ids.append(get_ident())
        assert path == save_directory
        assert target is GameTarget.FL26
        return save_directory

    worker = InstallerWorker(
        discover_locations=fake_discovery,
        validate_destination=fake_validation,
    )
    try:
        worker.discover_locations()
        discovered = _next_event(worker, LocationsDiscovered)
        worker.validate_destination(
            save_directory,
            GameTarget.FL26,
            "Football Life 2026",
        )
        validated = _next_event(worker, DestinationValidated)

        assert discovered.locations == (location,)
        assert validated.location == location
        assert operation_thread_ids
        assert all(identifier != get_ident() for identifier in operation_thread_ids)
    finally:
        worker.close()


def test_worker_reports_browse_validation_failure_as_a_typed_event(
    tmp_path: Path,
) -> None:
    expected = DestinationError(
        "not_save",
        "destination directory must be named save",
    )

    def reject(_path: Path, _target: GameTarget) -> Path:
        raise expected

    worker = InstallerWorker(validate_destination=reject)
    try:
        worker.validate_destination(
            tmp_path,
            GameTarget.FL26,
            "Football Life 2026",
        )
        event = _next_event(
            worker,
            DestinationValidationFailed,
        )

        assert event.error is expected
    finally:
        worker.close()




def test_open_save_folder_returns_false_when_startfile_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(_path: Path) -> None:
        raise OSError("Explorer is unavailable")

    monkeypatch.setattr(installer_app.sys, "platform", "win32")
    monkeypatch.setattr(installer_app.os, "startfile", fail, raising=False)

    assert installer_app.open_save_folder(Path("C:/save")) is False


def test_close_uses_atomic_worker_answer_when_commit_event_is_not_polled() -> None:
    controller, _, _ = _progress_controller()
    rendered: list[InstallerState] = []

    class Worker:
        def cancel(self) -> bool:
            return False

    application = object.__new__(installer_app.InstallerApplication)
    application.controller = controller
    application.worker = Worker()
    application._close_pending = False
    application._cancel_requested = False
    application._commit_lock_observed = False
    application._render = rendered.append

    application._request_cancel(close_after=True)

    assert application._close_pending is False
    assert application._cancel_requested is False
    assert application._commit_lock_observed is True
    assert rendered == [controller.state]



class _InstallerReleaseHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def do_GET(self) -> None:
        body = self.server.routes[self.path]  # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture
def installer_release_server() -> Iterator[ThreadingHTTPServer]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _InstallerReleaseHandler)
    server.routes = {}  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


class _InstallerReleaseOpener:
    def __init__(self, server: ThreadingHTTPServer):
        self._server = server
        self.requested_urls: list[str] = []

    def open(self, url: str, timeout: float):
        self.requested_urls.append(url)
        asset_name = urlsplit(url).path.rsplit("/", 1)[-1]
        local_url = f"http://127.0.0.1:{self._server.server_port}/{asset_name}"
        return urlopen(local_url, timeout=timeout)


def test_worker_downloads_backs_up_and_installs_end_to_end(
    installer_release_server: ThreadingHTTPServer,
    tmp_path: Path,
) -> None:
    old_save = b"verified original save"
    new_save = b"verified replacement save"
    archive_buffer = BytesIO()
    archive_member = zipfile.ZipInfo(
        "EDIT00000000",
        date_time=(2026, 8, 6, 0, 0, 0),
    )
    archive_member.compress_type = zipfile.ZIP_STORED
    with zipfile.ZipFile(archive_buffer, "w") as archive:
        archive.writestr(archive_member, new_save)
    archive_bytes = archive_buffer.getvalue()

    archive_url = (
        "https://github.com/gvoze32/fldailyedit/releases/download/latest/"
        "fldailyedit-fl2026-fast.zip"
    )
    catalog_bytes = json.dumps(
        {
            "schema_version": 1,
            "records": [
                {
                    "target_id": "fl26-u2.2-national-squads",
                    "target_name": "Football Life 2026 Update 2.2 + National Squads",
                    "channel": "fast",
                    "generated_at": "2026-08-06T00:00:00Z",
                    "asset_name": "fldailyedit-fl2026-fast.zip",
                    "download_url": archive_url,
                    "archive_size": len(archive_bytes),
                    "archive_sha256": hashlib.sha256(archive_bytes).hexdigest(),
                    "save_size": len(new_save),
                    "save_sha256": hashlib.sha256(new_save).hexdigest(),
                }
            ],
        },
        separators=(",", ":"),
    ).encode("utf-8")
    installer_release_server.routes.update(  # type: ignore[attr-defined]
        {
            "/catalog.json": catalog_bytes,
            "/fldailyedit-fl2026-fast.zip": archive_bytes,
        }
    )

    save_directory = tmp_path / "Football Life 2026" / "save"
    save_directory.mkdir(parents=True)
    target = save_directory / "EDIT00000000"
    target.write_bytes(old_save)
    location = SaveLocation(
        GameTarget.FL26,
        "Football Life 2026",
        save_directory,
    )
    opener = _InstallerReleaseOpener(installer_release_server)
    worker = InstallerWorker(
        fetch_catalog=partial(fetch_catalog, opener=opener),
        download_archive=partial(download_archive, opener=opener),
        now=lambda: GENERATED_AT,
    )
    controller = InstallerController()
    observed: list[ProgressChanged | InstallCompleted | WorkerFailed] = []

    try:
        worker.load_catalog()
        catalog_event = _next_event(worker, CatalogLoaded)
        assert isinstance(catalog_event, CatalogLoaded)
        controller.handle_event(catalog_event)
        record = catalog_event.catalog.records[0]

        controller.set_locations((location,))
        controller.select_record(record)
        controller.next()
        controller.select_location(location)
        controller.next()
        controller.next()
        worker.install(record, location)

        deadline = monotonic() + 5.0
        while monotonic() < deadline:
            event = worker.events.get(timeout=deadline - monotonic())
            observed.append(event)
            controller.handle_event(event)
            if isinstance(event, (InstallCompleted, WorkerFailed)):
                break
        else:
            raise AssertionError("worker did not complete the local release install")
    finally:
        worker.close()

    assert isinstance(observed[-1], InstallCompleted)
    assert controller.state.step is WizardStep.RESULT
    assert controller.state.error_title is None
    assert controller.state.result is observed[-1].result
    assert target.read_bytes() == new_save
    assert controller.state.result.backup_path == (
        save_directory
        / "FLDailyEditBackups"
        / "EDIT00000000.20260806T000000Z.bak"
    )
    assert controller.state.result.backup_path.read_bytes() == old_save
    assert controller.state.result.installed_sha256 == hashlib.sha256(
        new_save
    ).hexdigest()

    progress_events = [
        event for event in observed if isinstance(event, ProgressChanged)
    ]
    assert [
        event.stage for event in progress_events if event.stage != "downloading"
    ] == [
        InstallStage.VALIDATING_DESTINATION.value,
        InstallStage.VERIFYING_ARCHIVE.value,
        InstallStage.BACKING_UP.value,
        InstallStage.STAGING.value,
        InstallStage.REPLACING.value,
        InstallStage.VERIFYING_INSTALL.value,
    ]
    assert [
        (event.downloaded, event.total)
        for event in progress_events
        if event.stage == "downloading"
    ] == [(0, len(archive_bytes)), (len(archive_bytes), len(archive_bytes))]
    assert opener.requested_urls == [
        "https://github.com/gvoze32/fldailyedit/releases/download/latest/catalog.json",
        archive_url,
    ]


class _UpdateViewVar:
    def __init__(self) -> None:
        self.value = ""

    def set(self, value: str) -> None:
        self.value = value


class _UpdateViewButton:
    def __init__(self) -> None:
        self.options: dict[str, str] = {}

    def configure(self, **options: str) -> None:
        self.options.update(options)


def _update_view_for(
    catalog: Catalog,
) -> tuple[installer_app.InstallerApplication, dict[Channel, _UpdateViewButton]]:
    controller = InstallerController()
    controller.set_catalog(catalog)
    application = object.__new__(installer_app.InstallerApplication)
    application._catalog_status_var = _UpdateViewVar()
    application._record_var = _UpdateViewVar()
    buttons = {
        Channel.FAST: _UpdateViewButton(),
        Channel.DEEP: _UpdateViewButton(),
    }
    application._record_buttons = buttons

    application._render_update(controller.state)

    return application, buttons


def test_update_view_shows_each_release_generation_in_utc_beside_its_channel() -> None:
    fast = _record(
        Channel.FAST,
        generated_at=datetime(2026, 8, 6, 1, 2, tzinfo=timezone.utc),
    )
    deep = _record(
        Channel.DEEP,
        generated_at=datetime(
            2026,
            8,
            6,
            4,
            30,
            tzinfo=timezone(timedelta(hours=5, minutes=30)),
        ),
    )

    application, buttons = _update_view_for(Catalog(1, (fast, deep)))

    assert application._records_by_channel == {
        Channel.FAST.value: fast,
        Channel.DEEP.value: deep,
    }
    assert application._record_var.value == Channel.FAST.value
    assert buttons[Channel.FAST].options == {
        "state": "normal",
        "text": "Fast — Recommended — Generated 2026-08-06 01:02 UTC",
    }
    assert buttons[Channel.DEEP].options == {
        "state": "normal",
        "text": "Deep — Expanded coverage — Generated 2026-08-05 23:00 UTC",
    }


def test_update_view_keeps_static_choice_copy_when_release_is_unavailable() -> None:
    fast = _record(Channel.FAST)

    _, buttons = _update_view_for(Catalog(1, (fast,)))

    assert buttons[Channel.FAST].options == {
        "state": "normal",
        "text": "Fast — Recommended — Generated 2026-08-06 00:00 UTC",
    }
    assert buttons[Channel.DEEP].options == {
        "state": "disabled",
        "text": "Deep — Expanded coverage",
    }

def test_worker_validates_discovered_locations_and_emits_canonical_paths(
    tmp_path: Path,
) -> None:
    discovered_path = tmp_path / "redirected" / "save"
    discovered_path.mkdir(parents=True)
    canonical_path = tmp_path / "canonical" / "save"
    canonical_path.mkdir(parents=True)
    discovered = SaveLocation(
        GameTarget.FL26,
        "Football Life 2026",
        discovered_path,
    )
    validation_calls: list[tuple[Path, GameTarget]] = []

    def fake_validation(path: Path, target: GameTarget) -> Path:
        validation_calls.append((path, target))
        return canonical_path

    worker = InstallerWorker(
        discover_locations=lambda: (discovered,),
        validate_destination=fake_validation,
    )
    try:
        worker.discover_locations()
        event = _next_event(worker, LocationsDiscovered)

        assert validation_calls == [(discovered_path, GameTarget.FL26)]
        assert event.locations == (
            SaveLocation(
                GameTarget.FL26,
                "Football Life 2026",
                canonical_path,
            ),
        )
    finally:
        worker.close()


def _local_location(tmp_path: Path) -> SaveLocation:
    save_directory = tmp_path / "save"
    save_directory.mkdir()
    (save_directory / "EDIT00000000").write_bytes(b"local-save")
    return SaveLocation(GameTarget.FL26, "Football Life 2026", save_directory)
def test_local_browse_accepts_non_2026_save_layout(
    monkeypatch, tmp_path: Path
) -> None:
    vanilla_pes = (
        tmp_path
        / "Documents"
        / "KONAMI"
        / "eFootball PES 2021 SEASON UPDATE"
        / "2025"
        / "save"
    )
    vanilla_pes.mkdir(parents=True)

    class BrowseVar:
        def __init__(self) -> None:
            self.value = ""

        def set(self, value: str) -> None:
            self.value = value

    class BrowseWorker:
        def __init__(self) -> None:
            self.calls: list[tuple[Path, GameTarget, str]] = []

        def validate_destination(
            self, path: Path, target: GameTarget, game_name: str
        ) -> None:
            self.calls.append((path, target, game_name))

    controller = InstallerController()
    controller.select_mode(InstallerMode.LOCAL)
    worker = BrowseWorker()
    application = object.__new__(installer_app.InstallerApplication)
    application.controller = controller
    application.root = None
    application.worker = worker
    application._browse_pending = False
    application._browse_error_var = BrowseVar()
    application._browse_button = type(
        "BrowseButton", (), {"focus_set": lambda _self: None}
    )()
    application._render = lambda _state: None
    monkeypatch.setattr(
        installer_app,
        "filedialog",
        type("Dialog", (), {"askdirectory": staticmethod(lambda **_: str(vanilla_pes))}),
    )

    application._browse()

    assert worker.calls == [
        (vanilla_pes, GameTarget.LOCAL, "Selected local save")
    ]
    assert application._browse_pending is True
    assert application._browse_error_var.value == "Checking selected folder…"


def test_release_browse_rejects_local_policy_target(
    monkeypatch, tmp_path: Path
) -> None:
    local_record = _record(target_id=GameTarget.LOCAL.value)
    controller = InstallerController(
        state=InstallerState(
            catalog=Catalog(1, (local_record,)),
            selected_record=local_record,
        )
    )
    selected = tmp_path / "save"
    selected.mkdir()

    class BrowseVar:
        def __init__(self) -> None:
            self.value = ""

        def set(self, value: str) -> None:
            self.value = value

    class BrowseButton:
        def focus_set(self) -> None:
            pass

    class BrowseWorker:
        def __init__(self) -> None:
            self.calls: list[tuple[Path, GameTarget, str]] = []

        def validate_destination(
            self, path: Path, target: GameTarget, game_name: str
        ) -> None:
            self.calls.append((path, target, game_name))

    worker = BrowseWorker()
    application = object.__new__(installer_app.InstallerApplication)
    application.controller = controller
    application.root = None
    application.worker = worker
    application._browse_pending = False
    application._browse_error_var = BrowseVar()
    application._browse_button = BrowseButton()
    application._render = lambda _state: None
    monkeypatch.setattr(
        installer_app,
        "filedialog",
        type("Dialog", (), {"askdirectory": staticmethod(lambda **_: str(selected))}),
    )

    application._browse()

    assert worker.calls == []
    assert application._browse_pending is False
    assert "not supported" in application._browse_error_var.value

def test_local_mode_accepts_a_non_fl26_save_location(tmp_path: Path) -> None:
    save = tmp_path / "2025" / "save"
    save.mkdir(parents=True)
    (save / "EDIT00000000").write_bytes(b"standard-edit")
    location = SaveLocation(GameTarget.PES2021, "PES 2021", save)

    controller = InstallerController()
    assert controller.select_mode(InstallerMode.LOCAL)
    controller.set_locations((location,))
    assert controller.next()
    assert controller.state.selected_location is location
    assert controller.next()
    assert controller.state.step is WizardStep.REVIEW


def test_local_worker_accepts_non_fl26_location_and_reaches_service(
    tmp_path: Path,
) -> None:
    save = tmp_path / "2025" / "save"
    save.mkdir(parents=True)
    (save / "EDIT00000000").write_bytes(b"standard-edit")
    location = SaveLocation(GameTarget.PES2021, "PES 2021", save)
    calls: list[tuple[LocalUpdateRequest, CancellationToken]] = []

    class FakeService:
        def execute(
            self,
            request: LocalUpdateRequest,
            *,
            progress,
            token: CancellationToken,
        ) -> LocalUpdateResult:
            calls.append((request, token))
            return LocalUpdateResult(
                target_path=location.edit_file,
                backup_path=tmp_path / "backup",
                installed_sha256="a" * 64,
                transfer_applied=1,
                shirt_numbers_changed=0,
                unchanged=0,
                safety_skipped=0,
            )

    worker = InstallerWorker(local_update_factory=lambda: FakeService())
    try:
        worker.start_local_update(location, deep=True)
        event = _next_event(worker, LocalUpdateCompleted)
        assert isinstance(event, LocalUpdateCompleted)
        assert len(calls) == 1
        assert calls[0][0] == LocalUpdateRequest(
            edit_path=location.edit_file,
            deep=True,
        )
        assert isinstance(calls[0][1], CancellationToken)
    finally:
        worker.close()

def test_local_mode_rejects_missing_edit_file_before_review(
    tmp_path: Path,
) -> None:
    missing = SaveLocation(
        GameTarget.FL26,
        "Football Life 2026",
        tmp_path / "missing" / "save",
    )
    controller = InstallerController()
    assert controller.select_mode(InstallerMode.LOCAL) is True
    controller.set_locations((missing,))
    assert controller.next() is True
    assert controller.select_location(missing) is True
    assert controller.next() is False
    assert controller.state.step is WizardStep.SAVE


def test_local_mode_can_reach_browse_without_discovered_fl26_location() -> None:
    controller = InstallerController()
    assert controller.select_mode(InstallerMode.LOCAL) is True
    controller.set_locations(())

    assert controller.next() is True
    assert controller.state.step is WizardStep.SAVE


def test_local_fast_is_default_and_deep_can_be_selected(
    tmp_path: Path,
) -> None:
    location = _local_location(tmp_path)
    controller = InstallerController()
    controller.select_mode(InstallerMode.LOCAL)
    controller.set_locations((location,))
    assert controller.state.local_deep is False
    assert controller.set_local_deep(True) is True
    assert controller.state.local_deep is True


def test_local_progress_marks_commit_and_completion(
    tmp_path: Path,
) -> None:
    location = _local_location(tmp_path)
    controller = InstallerController()
    controller.select_mode(InstallerMode.LOCAL)
    controller.set_locations((location,))
    controller.next()
    controller.select_location(location)
    assert controller.next() is True
    assert controller.next() is True

    assert controller.handle_event(
        LocalProgressChanged(
            LocalUpdateProgress(
                LocalUpdateStage.ENCRYPTING,
                commit_started=True,
            )
        )
    )
    assert controller.state.commit_started is True

    result = LocalUpdateResult(
        target_path=location.edit_file,
        backup_path=tmp_path / "backup",
        installed_sha256="a" * 64,
        transfer_applied=1,
        shirt_numbers_changed=0,
        unchanged=0,
        safety_skipped=0,
    )
    assert controller.handle_event(LocalUpdateCompleted(result)) is True
    assert controller.state.step is WizardStep.RESULT
    assert controller.state.result is result


def test_local_worker_cannot_cancel_after_commit(
    tmp_path: Path,
) -> None:
    location = _local_location(tmp_path)
    entered_commit = Event()
    release_commit = Event()

    class FakeService:
        def execute(
            self,
            request,
            *,
            progress,
            token: CancellationToken,
        ):
            assert request.edit_path == location.edit_file
            progress(
                LocalUpdateProgress(
                    LocalUpdateStage.ENCRYPTING,
                    commit_started=True,
                )
            )
            entered_commit.set()
            assert release_commit.wait(timeout=2)
            return LocalUpdateResult(
                target_path=location.edit_file,
                backup_path=tmp_path / "backup",
                installed_sha256="a" * 64,
                transfer_applied=1,
                shirt_numbers_changed=0,
                unchanged=0,
                safety_skipped=0,
            )

    worker = InstallerWorker(local_update_factory=lambda: FakeService())
    try:
        worker.start_local_update(location, deep=False)
        assert entered_commit.wait(timeout=2)
        assert worker.cancel() is False
        release_commit.set()
        event = _next_event(worker, LocalUpdateCompleted)
        assert isinstance(event, LocalUpdateCompleted)
    finally:
        release_commit.set()
        worker.close()


def test_local_progress_presentation_covers_stages_and_commit_lock() -> None:
    progress = InstallerState(
        mode=InstallerMode.LOCAL,
        step=WizardStep.PROGRESS,
        progress_stage=LocalUpdateStage.MATCHING.value,
    )
    matching = installer_app.progress_presentation(progress)
    assert matching.mode == "indeterminate"
    assert "Matching players" in matching.status
    assert matching.controls_locked is False

    cancelling = installer_app.progress_presentation(
        progress,
        cancellation_requested=True,
    )
    assert cancelling.status == "Cancelling local update…"
    assert cancelling.controls_locked is False

    committing = installer_app.progress_presentation(
        replace(
            progress,
            progress_stage=LocalUpdateStage.ENCRYPTING.value,
            commit_started=True,
        )
    )
    assert committing.controls_locked is True
    assert "Finishing the local update" in committing.status
class _LayoutWidget:
    def __init__(self, parent=None, **options):
        self.parent = parent
        self.options = options
        self.children = []
        self.grid_options = {}
        self.rowconfigure_calls = []
        if parent is not None and hasattr(parent, "children"):
            parent.children.append(self)

    def bind(self, *_args, **_kwargs):
        pass

    def columnconfigure(self, *_args, **_kwargs):
        pass

    def configure(self, **options):
        self.options.update(options)


    def grid(self, **options):
        self.grid_options = options

    def rowconfigure(self, row, **options):
        self.rowconfigure_calls.append((row, options))



class _LayoutVariable:
    def __init__(self, _root, value=None):
        self.value = value


class _LayoutStyle:
    def __init__(self, _root):
        pass

    def lookup(self, _style, _option):
        return "white"


class _LayoutTkinter:
    BooleanVar = _LayoutVariable
    StringVar = _LayoutVariable


class _LayoutTtk:
    Button = _LayoutWidget
    Checkbutton = _LayoutWidget
    Frame = _LayoutWidget
    Label = _LayoutWidget
    Radiobutton = _LayoutWidget
    Style = _LayoutStyle


def _layout_application(monkeypatch) -> InstallerApplication:
    monkeypatch.setattr(installer_app, "tkinter", _LayoutTkinter)
    monkeypatch.setattr(installer_app, "ttk", _LayoutTtk)
    application = object.__new__(installer_app.InstallerApplication)
    application.root = object()
    application._body = _LayoutWidget()
    application._wrapped_labels = []
    return application


def test_update_frame_groups_prebuilt_and_local_coverage_controls(
    monkeypatch,
) -> None:
    application = _layout_application(monkeypatch)

    frame = application._build_update_frame()

    groups = [
        child for child in frame.children if child.options.get("relief") == "groove"
    ]
    assert len(groups) == 2
    release_group, local_group = sorted(
        groups,
        key=lambda group: group.grid_options["row"],
    )
    assert release_group.grid_options["row"] == 2
    assert local_group.grid_options["row"] == 3

    release_button = next(
        child
        for child in release_group.children
        if child.options.get("text") == installer_app.UI_COPY["release_mode"]
    )
    local_button = next(
        child
        for child in local_group.children
        if child.options.get("text") == installer_app.UI_COPY["local_mode"]
    )
    assert release_button.parent is release_group
    assert local_button.parent is local_group

    local_deep = next(
        child
        for child in local_group.children
        if child.options.get("variable") is application._local_deep_var
    )
    assert local_deep.parent is local_group
    assert application._mode_var.value == InstallerMode.RELEASE.value


def test_save_frame_places_browse_right_of_location_list(monkeypatch) -> None:
    application = _layout_application(monkeypatch)

    frame = application._build_save_frame()

    browse_button = next(
        child
        for child in frame.children
        if child.options.get("text") == "Browse…"
    )
    location_list = application._location_holder
    assert location_list.parent is frame
    assert browse_button.grid_options["row"] == 2
    assert browse_button.grid_options["column"] == 1
    assert browse_button.grid_options["sticky"] == "ne"
    assert location_list.grid_options["row"] == 2
    assert location_list.grid_options["column"] == 0
    assert (2, {"weight": 1}) in frame.rowconfigure_calls
