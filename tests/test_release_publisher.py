from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tools.publish_release_assets import ReleasePublishError, publish_asset_pair


REPOSITORY = "gvoze32/fldailyedit"
TAG = "latest"


class _FakeGh:
    def __init__(
        self,
        assets: dict[str, bytes],
        *,
        failing_uploads: set[int] | None = None,
    ) -> None:
        self.assets = dict(assets)
        self.failing_uploads = failing_uploads or set()
        self.upload_count = 0
        self.commands: list[list[str]] = []

    def __call__(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        action = command[2]
        if action == "view":
            payload = {"assets": [{"name": name} for name in self.assets]}
            return subprocess.CompletedProcess(
                command, 0, stdout=json.dumps(payload), stderr=""
            )
        if action == "download":
            asset_name = command[command.index("--pattern") + 1]
            destination = Path(command[command.index("--dir") + 1]) / asset_name
            destination.write_bytes(self.assets[asset_name])
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if action == "upload":
            self.upload_count += 1
            if self.upload_count in self.failing_uploads:
                raise subprocess.CalledProcessError(
                    1, command, stderr=f"upload {self.upload_count} failed"
                )
            asset_path = Path(command[4])
            self.assets[asset_path.name] = asset_path.read_bytes()
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if action == "delete-asset":
            asset_name = command[4]
            del self.assets[asset_name]
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        raise AssertionError(f"unexpected gh command: {command}")


def _pair(tmp_path: Path) -> tuple[Path, Path]:
    first = tmp_path / "first.zip"
    second = tmp_path / "catalog.json"
    first.write_bytes(b"new first")
    second.write_bytes(b"new second")
    return first, second


def test_second_upload_failure_restores_exact_prior_pair(tmp_path: Path) -> None:
    first, second = _pair(tmp_path)
    original = {
        first.name: b"old first",
        second.name: b"old second",
        "unrelated.exe": b"leave alone",
    }
    gh = _FakeGh(original, failing_uploads={2})

    with pytest.raises(ReleasePublishError, match="publication failed"):
        publish_asset_pair(REPOSITORY, TAG, (first, second), runner=gh)

    assert gh.assets == original
    assert gh.upload_count == 4


def test_second_upload_failure_removes_new_assets_when_pair_was_absent(
    tmp_path: Path,
) -> None:
    first, second = _pair(tmp_path)
    gh = _FakeGh({"unrelated.exe": b"leave alone"}, failing_uploads={2})

    with pytest.raises(ReleasePublishError, match="publication failed"):
        publish_asset_pair(REPOSITORY, TAG, (first, second), runner=gh)

    assert gh.assets == {"unrelated.exe": b"leave alone"}
    delete_commands = [command for command in gh.commands if command[2] == "delete-asset"]
    assert [command[4] for command in delete_commands] == [first.name]


def test_rollback_attempts_every_restore_and_reports_failures(tmp_path: Path) -> None:
    first, second = _pair(tmp_path)
    gh = _FakeGh(
        {first.name: b"old first", second.name: b"old second"},
        failing_uploads={2, 3},
    )

    with pytest.raises(ReleasePublishError) as caught:
        publish_asset_pair(REPOSITORY, TAG, (first, second), runner=gh)

    assert "rollback failed" in str(caught.value)
    assert first.name in str(caught.value)
    assert "upload 3 failed" in str(caught.value)
    assert gh.upload_count == 4
    assert gh.assets[first.name] == b"new first"
    assert gh.assets[second.name] == b"old second"


def test_every_gh_release_operation_uses_explicit_repository(tmp_path: Path) -> None:
    pair = _pair(tmp_path)
    gh = _FakeGh({})

    publish_asset_pair(REPOSITORY, TAG, pair, runner=gh)

    release_commands = [command for command in gh.commands if command[:2] == ["gh", "release"]]
    assert release_commands
    for command in release_commands:
        repo_position = command.index("--repo")
        assert command[repo_position + 1] == REPOSITORY
