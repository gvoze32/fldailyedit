from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from threading import Event, Lock
from typing import Any, Callable, Protocol, Sequence


class LocalUpdateStage(str, Enum):
    SCRAPING = "scraping"
    VALIDATING = "validating"
    MATCHING = "matching"
    APPLYING = "applying"
    VERIFYING = "verifying"
    ENCRYPTING = "encrypting"
    COMPLETE = "complete"


@dataclass(frozen=True, slots=True)
class LocalUpdateRequest:
    edit_path: Path
    output_path: Path | None = None
    deep: bool = False
    window: str = "auto"
    since: str | None = None
    club: str | None = None
    threshold: int | None = None
    popular: bool = False
    fotmob_only: bool = False
    allow_overflow_release: bool = False
    dry_run: bool = False

    @property
    def target_path(self) -> Path:
        return self.output_path or self.edit_path


@dataclass(frozen=True, slots=True)
class LocalUpdateProgress:
    stage: LocalUpdateStage
    detail: str = ""
    current: int = 0
    total: int = 0
    commit_started: bool = False


@dataclass(frozen=True, slots=True)
class LocalUpdateResult:
    target_path: Path
    backup_path: Path | None
    installed_sha256: str | None
    transfer_applied: int
    shirt_numbers_changed: int
    unchanged: int
    safety_skipped: int
    no_changes: bool = False
    diagnostic: str | None = None

class LocalUpdateError(RuntimeError):
    """Stable service error suitable for CLI and beginner-facing GUI copy."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        stage: LocalUpdateStage | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.stage = stage


class LocalUpdateCancelled(LocalUpdateError):
    def __init__(self, message: str = "Local update cancelled") -> None:
        super().__init__("cancelled", message)


class CancellationToken:
    """Thread-safe cancellation handoff that becomes immutable at commit."""

    def __init__(self) -> None:
        self._event = Event()
        self._lock = Lock()
        self._commit_started = False

    @property
    def requested(self) -> bool:
        with self._lock:
            return self._event.is_set() and not self._commit_started

    @property
    def commit_started(self) -> bool:
        with self._lock:
            return self._commit_started

    def request(self) -> bool:
        with self._lock:
            if self._commit_started:
                return False
            self._event.set()
            return True

    def mark_commit_started(self) -> None:
        with self._lock:
            self._commit_started = True
            self._event.clear()

    def raise_if_cancelled(self) -> None:
        with self._lock:
            cancelled = self._event.is_set() and not self._commit_started
        if cancelled:
            raise LocalUpdateCancelled()


class LocalUpdateRuntime(Protocol):
    def scrape(
        self, request: LocalUpdateRequest, token: CancellationToken
    ) -> Sequence[Any]: ...

    def validate_and_prepare(
        self,
        request: LocalUpdateRequest,
        transfers: Sequence[Any],
        token: CancellationToken,
    ) -> Any: ...

    def match_and_plan(
        self,
        request: LocalUpdateRequest,
        prepared: Any,
        transfers: Sequence[Any],
        token: CancellationToken,
    ) -> Any: ...

    def apply(
        self,
        request: LocalUpdateRequest,
        prepared: Any,
        plan: Any,
        token: CancellationToken,
    ) -> Any: ...

    def verify(
        self,
        request: LocalUpdateRequest,
        prepared: Any,
        mutation: Any,
        token: CancellationToken,
    ) -> None: ...

    def publish(
        self,
        request: LocalUpdateRequest,
        prepared: Any,
        mutation: Any,
        token: CancellationToken,
    ) -> LocalUpdateResult: ...

    def preview(
        self,
        request: LocalUpdateRequest,
        prepared: Any,
        plan: Any,
        token: CancellationToken,
    ) -> LocalUpdateResult: ...

    def cleanup(self, prepared: Any) -> None: ...


ProgressCallback = Callable[[LocalUpdateProgress], None]


class LocalUpdateService:
    """Run one local update through a typed, GUI/CLI-neutral lifecycle."""

    def __init__(self, runtime: LocalUpdateRuntime) -> None:
        self._runtime = runtime

    def execute(
        self,
        request: LocalUpdateRequest,
        *,
        progress: ProgressCallback | None = None,
        token: CancellationToken | None = None,
    ) -> LocalUpdateResult:
        emit = progress if progress is not None else lambda _event: None
        cancellation = token if token is not None else CancellationToken()
        prepared: Any = None

        try:
            emit(LocalUpdateProgress(LocalUpdateStage.SCRAPING))
            transfers = self._runtime.scrape(request, cancellation)
            cancellation.raise_if_cancelled()
            if not transfers:
                return LocalUpdateResult(
                    target_path=request.target_path,
                    backup_path=None,
                    installed_sha256=None,
                    transfer_applied=0,
                    shirt_numbers_changed=0,
                    unchanged=0,
                    safety_skipped=0,
                    no_changes=True,
                )

            emit(LocalUpdateProgress(LocalUpdateStage.VALIDATING))
            prepared = self._runtime.validate_and_prepare(
                request,
                transfers,
                cancellation,
            )
            cancellation.raise_if_cancelled()

            emit(LocalUpdateProgress(LocalUpdateStage.MATCHING))
            plan = self._runtime.match_and_plan(
                request,
                prepared,
                transfers,
                cancellation,
            )
            cancellation.raise_if_cancelled()

            if request.dry_run:
                result = self._runtime.preview(
                    request,
                    prepared,
                    plan,
                    cancellation,
                )
                emit(LocalUpdateProgress(LocalUpdateStage.COMPLETE))
                return result

            emit(LocalUpdateProgress(LocalUpdateStage.APPLYING))
            mutation = self._runtime.apply(
                request,
                prepared,
                plan,
                cancellation,
            )
            cancellation.raise_if_cancelled()

            if isinstance(mutation, LocalUpdateResult):
                emit(LocalUpdateProgress(LocalUpdateStage.COMPLETE))
                return mutation

            emit(LocalUpdateProgress(LocalUpdateStage.VERIFYING))
            self._runtime.verify(
                request,
                prepared,
                mutation,
                cancellation,
            )
            cancellation.raise_if_cancelled()

            cancellation.mark_commit_started()
            emit(
                LocalUpdateProgress(
                    LocalUpdateStage.ENCRYPTING,
                    commit_started=True,
                )
            )
            result = self._runtime.publish(
                request,
                prepared,
                mutation,
                cancellation,
            )
            emit(LocalUpdateProgress(LocalUpdateStage.COMPLETE))
            return result
        except LocalUpdateError:
            raise
        except Exception as error:
            raise LocalUpdateError("runtime_error", str(error)) from error
        finally:
            if prepared is not None:
                self._runtime.cleanup(prepared)
