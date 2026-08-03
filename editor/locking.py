"""Cross-platform advisory lock for one EDIT output target."""

from __future__ import annotations

import os
from pathlib import Path


class EditLockError(RuntimeError):
    """Raised when another process already owns an EDIT output lock."""


class EditFileLock:
    """Hold an OS-level non-blocking lock for the lifetime of an edit run."""

    def __init__(self, target: Path):
        target = Path(target)
        self.path = target.with_name(f".{target.name}.lock")
        self._handle = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+", encoding="utf-8")
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                if handle.read(1) == "":
                    handle.write("0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as exc:
            handle.close()
            raise EditLockError(
                f"Another transfer run is already using output {self.path.parent / self.path.name[1:-5]}"
            ) from exc

        handle.seek(0)
        handle.truncate()
        handle.write(f"pid={os.getpid()}\n")
        handle.flush()
        self._handle = handle

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
            self._handle = None

    def __enter__(self) -> "EditFileLock":
        self.acquire()
        return self

    def __exit__(self, *_exc_info) -> None:
        self.release()
