from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path


CommandRunner = Callable[[list[str]], subprocess.CompletedProcess[str]]


class ReleasePublishError(RuntimeError):
    """A release asset pair could not be published consistently."""


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
    )


def _error_detail(error: BaseException) -> str:
    if isinstance(error, subprocess.CalledProcessError) and error.stderr:
        return error.stderr.strip()
    return str(error)


def _release_asset_names(
    repository: str, tag: str, runner: CommandRunner
) -> set[str]:
    completed = runner(
        [
            "gh",
            "release",
            "view",
            tag,
            "--repo",
            repository,
            "--json",
            "assets",
        ]
    )
    try:
        document = json.loads(completed.stdout)
        assets = document["assets"]
        names = [asset["name"] for asset in assets]
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise ReleasePublishError("gh returned malformed release asset data") from error
    if (
        not isinstance(assets, list)
        or any(not isinstance(name, str) or not name for name in names)
        or len(names) != len(set(names))
    ):
        raise ReleasePublishError("gh returned malformed release asset data")
    return set(names)


def _download_asset(
    repository: str,
    tag: str,
    asset_name: str,
    destination: Path,
    runner: CommandRunner,
) -> Path:
    runner(
        [
            "gh",
            "release",
            "download",
            tag,
            "--repo",
            repository,
            "--pattern",
            asset_name,
            "--dir",
            str(destination),
            "--clobber",
        ]
    )
    downloaded = destination / asset_name
    if not downloaded.is_file():
        raise ReleasePublishError(f"gh did not download existing asset {asset_name}")
    return downloaded


def _upload_asset(
    repository: str, tag: str, asset_path: Path, runner: CommandRunner
) -> None:
    runner(
        [
            "gh",
            "release",
            "upload",
            tag,
            str(asset_path),
            "--repo",
            repository,
            "--clobber",
        ]
    )


def _delete_asset(
    repository: str, tag: str, asset_name: str, runner: CommandRunner
) -> None:
    runner(
        [
            "gh",
            "release",
            "delete-asset",
            tag,
            asset_name,
            "--repo",
            repository,
            "--yes",
        ]
    )


def _rollback_pair(
    repository: str,
    tag: str,
    asset_paths: tuple[Path, Path],
    backups: dict[str, Path],
    runner: CommandRunner,
) -> list[tuple[str, BaseException]]:
    failures: list[tuple[str, BaseException]] = []
    current_names: set[str] | None
    try:
        current_names = _release_asset_names(repository, tag, runner)
    except BaseException as error:
        failures.append(("release snapshot", error))
        current_names = None

    for asset_path in asset_paths:
        asset_name = asset_path.name
        try:
            backup_path = backups.get(asset_name)
            if backup_path is not None:
                _upload_asset(repository, tag, backup_path, runner)
            elif current_names is None or asset_name in current_names:
                _delete_asset(repository, tag, asset_name, runner)
        except BaseException as error:
            failures.append((asset_name, error))
    return failures


def publish_asset_pair(
    repository: str,
    tag: str,
    asset_paths: tuple[Path, Path],
    *,
    runner: CommandRunner | None = None,
) -> None:
    if not repository or not tag:
        raise ValueError("repository and tag must be non-empty")
    if len(asset_paths) != 2:
        raise ValueError("exactly two release assets are required")
    if asset_paths[0].name == asset_paths[1].name:
        raise ValueError("release asset names must be distinct")
    for asset_path in asset_paths:
        if not asset_path.is_file():
            raise FileNotFoundError(asset_path)

    command_runner = _run if runner is None else runner
    previous_names = _release_asset_names(repository, tag, command_runner)
    with tempfile.TemporaryDirectory(prefix="release-asset-backup-") as raw_directory:
        backup_directory = Path(raw_directory)
        backups: dict[str, Path] = {}
        for asset_path in asset_paths:
            if asset_path.name in previous_names:
                backups[asset_path.name] = _download_asset(
                    repository,
                    tag,
                    asset_path.name,
                    backup_directory,
                    command_runner,
                )

        try:
            for asset_path in asset_paths:
                _upload_asset(repository, tag, asset_path, command_runner)
        except BaseException as publication_error:
            rollback_failures = _rollback_pair(
                repository,
                tag,
                asset_paths,
                backups,
                command_runner,
            )
            message = "release asset pair publication failed"
            if rollback_failures:
                details = ", ".join(
                    f"{name}: {_error_detail(error)}"
                    for name, error in rollback_failures
                )
                message += f"; rollback failed for {details}"
            raise ReleasePublishError(message) from publication_error


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Publish a pair of GitHub release assets with rollback"
    )
    parser.add_argument("--repo", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("assets", nargs=2, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _argument_parser().parse_args(argv)
    publish_asset_pair(
        arguments.repo,
        arguments.tag,
        (arguments.assets[0], arguments.assets[1]),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
