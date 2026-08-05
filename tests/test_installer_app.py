from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from queue import Empty, SimpleQueue
from threading import Event, get_ident, main_thread
from time import monotonic, sleep

from installer.app import (
    CatalogLoaded,
    InstallCompleted,
    InstallerController,
    InstallerState,
    InstallerWorker,
    ProgressChanged,
    WizardStep,
    WorkerFailed,
    error_copy,
)
from installer.catalog import Catalog, CatalogError, Channel, DownloadError, ReleaseRecord
from installer.install import InstallError, InstallResult, InstallStage
from installer.paths import GameTarget, SaveLocation


GENERATED_AT = datetime(2026, 8, 6, tzinfo=timezone.utc)


def _record(
    channel: Channel = Channel.FAST,
    *,
    target_id: str = GameTarget.FL26.value,
) -> ReleaseRecord:
    return ReleaseRecord(
        target_id=target_id,
        target_name="Football Life 2026",
        channel=channel,
        generated_at=GENERATED_AT,
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


def test_update_cannot_continue_without_a_catalog_record_selection() -> None:
    controller, fast, _, _, _ = _controller_with_choices()

    assert controller.next() is False
    assert controller.state.step is WizardStep.UPDATE

    controller.select_record(_record(Channel.FAST))
    assert controller.next() is True
    assert controller.state.selected_record is fast
    assert controller.state.step is WizardStep.SAVE


def test_fast_and_deep_remain_distinct_catalog_choices() -> None:
    controller, fast, deep, _, _ = _controller_with_choices()

    assert controller.select_record(fast) is True
    assert controller.state.selected_record is fast
    assert controller.select_record(deep) is True
    assert controller.state.selected_record is deep


def test_multiple_locations_remain_visible_and_are_not_auto_selected() -> None:
    controller, fast, _, fl_location, pes_location = _controller_with_choices()
    controller.select_record(fast)
    controller.next()

    assert controller.state.locations == (fl_location, pes_location)
    assert controller.state.selected_location is None
    assert controller.next() is False
    assert controller.state.step is WizardStep.SAVE


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

    assert controller.select_record(pes_record) is True
    assert controller.next() is True
    assert controller.select_location(pes_location) is True
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
        worker.cancel()
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
        worker.cancel()
        continue_after_cancel.set()
        completed = _next_event(worker, InstallCompleted)

        assert completed == InstallCompleted(result)
        assert cancellation_values == [False, False]
    finally:
        worker.close()
