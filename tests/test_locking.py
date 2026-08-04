"""Process-level EDIT lock regression tests."""

import pytest

from editor.locking import EditFileLock, EditLockError


def test_second_lock_for_same_output_fails_closed(tmp_path):
    output = tmp_path / "EDIT00000000"
    first = EditFileLock(output)
    second = EditFileLock(output)

    first.acquire()
    try:
        with pytest.raises(EditLockError, match="already using output"):
            second.acquire()
    finally:
        first.release()

    second.acquire()
    second.release()
