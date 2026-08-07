import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


WORKFLOW_PATH = Path(".github/workflows/validate-player-update-pr.yml")


def test_legacy_target_workflow_path_is_absent():
    assert not Path(".github/workflows/player-spec-pr.yml").exists()


def _bash_command() -> list[str]:
    if os.name != "nt":
        return ["bash", "-c"]

    bash_path = shutil.which("bash")
    if bash_path is not None:
        resolved_bash = Path(bash_path).resolve()
        system_root = Path(
            os.environ.get("SystemRoot", r"C:\Windows")
        ).resolve()
        if resolved_bash.parent.as_posix().casefold() != (
            (system_root / "System32").as_posix().casefold()
        ):
            return [str(resolved_bash), "-c"]

    git_path = shutil.which("git")
    if git_path is not None:
        git_root = Path(git_path).resolve().parent.parent
        for candidate in (
            git_root / "bin" / "bash.exe",
            git_root / "usr" / "bin" / "bash.exe",
        ):
            if candidate.is_file():
                return [str(candidate), "-c"]

    raise FileNotFoundError("Git Bash executable not found")


def _step_script(name: str) -> str:
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    match = re.search(
        rf"(?ms)^      - name: {re.escape(name)}$\n"
        r".*?^        run: \|\n(?P<script>.*?)(?=^      - name:|\Z)",
        text,
    )
    assert match is not None
    return textwrap.dedent(match.group("script"))


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _workflow_environment() -> dict[str, str]:
    environment = os.environ.copy()
    python_bin = str(Path(sys.executable).parent.resolve())
    environment["PATH"] = f"{python_bin}{os.pathsep}{environment.get('PATH', '')}"
    return environment


def _pull_ref_fixture(tmp_path: Path, *, extra_head_path: bool = False):
    origin = tmp_path / "origin.git"
    source = tmp_path / "source"
    runner = tmp_path / "runner"
    origin.mkdir()
    source.mkdir()
    _git(origin, "init", "--bare")
    _git(source, "init", "-b", "main")
    _git(source, "config", "user.name", "Fixture")
    _git(source, "config", "user.email", "fixture@example.test")

    guard = source / "tools/check_player_spec_pr.py"
    guard.parent.mkdir()
    guard.write_text(
        Path("tools/check_player_spec_pr.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (source / "README.md").write_text("trusted base\n", encoding="utf-8")
    _git(source, "add", "--", "tools/check_player_spec_pr.py", "README.md")
    _git(source, "commit", "-m", "trusted base")
    base_sha = _git(source, "rev-parse", "HEAD")
    _git(source, "remote", "add", "origin", str(origin))
    _git(source, "push", "origin", "main")
    _git(origin, "symbolic-ref", "HEAD", "refs/heads/main")

    _git(source, "switch", "-c", "contribution")
    player_path = Path("players/new-player.json")
    (source / player_path).parent.mkdir()
    player_blob = '{"schema_version": 1}\n'
    (source / player_path).write_text(player_blob, encoding="utf-8")
    _git(source, "add", "--", player_path.as_posix())
    if extra_head_path:
        hostile_workflow = source / ".github/workflows/hostile.yml"
        hostile_workflow.parent.mkdir(parents=True)
        hostile_workflow.write_text("run: contributor-code\n", encoding="utf-8")
        _git(source, "add", "--", hostile_workflow.as_posix())
    _git(source, "commit", "-m", "pull request head")
    head_sha = _git(source, "rev-parse", "HEAD")
    _git(source, "push", "origin", "HEAD:refs/pull/42/head")

    _git(tmp_path, "clone", str(origin), str(runner))
    _git(runner, "checkout", "--detach", base_sha)
    return runner, base_sha, head_sha, player_path, player_blob


def _run_boundary(runner: Path, base_sha: str, head_sha: str, tmp_path: Path):
    changes_file = tmp_path / "changes.tsv"
    output_file = tmp_path / "boundary-output"
    environment = _workflow_environment()
    environment.update(
        {
            "BASE_SHA": base_sha,
            "CHANGES_FILE": str(changes_file),
            "GITHUB_OUTPUT": str(output_file),
            "HEAD_SHA": head_sha,
            "PR_NUMBER": "42",
        }
    )
    result = subprocess.run(
        [*_bash_command(), _step_script("Fetch head data and enforce boundary")],
        cwd=runner,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    return result, output_file


def test_event_parser_accepts_only_positive_number_and_full_lowercase_shas(tmp_path):
    output_file = tmp_path / "event-output"
    event_file = tmp_path / "event.json"
    event_file.write_text(
        json.dumps(
            {
                "number": 42,
                "pull_request": {
                    "base": {"sha": "a" * 40},
                    "head": {
                        "sha": "b" * 40,
                        "repo": {"full_name": "gvoze32/fldailyedit"},
                        "ref": "player-draft/issue-42",
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    environment = _workflow_environment()
    environment.update(
        {"GITHUB_EVENT_PATH": str(event_file), "GITHUB_OUTPUT": str(output_file)}
    )

    result = subprocess.run(
        [*_bash_command(), _step_script("Read trusted pull request coordinates")],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert output_file.read_text(encoding="utf-8").splitlines() == [
        "pr_number=42",
        f"base_sha={'a' * 40}",
        f"head_sha={'b' * 40}",
        "head_repo=gvoze32/fldailyedit",
        "head_ref=player-draft/issue-42",
    ]


@pytest.mark.parametrize(
    "event",
    [
        {"number": True, "pull_request": {"base": {"sha": "a" * 40}, "head": {"sha": "b" * 40}}},
        {"number": 0, "pull_request": {"base": {"sha": "a" * 40}, "head": {"sha": "b" * 40}}},
        {"number": 42, "pull_request": {"base": {"sha": "A" * 40}, "head": {"sha": "b" * 40}}},
        {"number": 42, "pull_request": {"base": {"sha": "a" * 39}, "head": {"sha": "b" * 40}}},
        {"number": 42, "pull_request": {"base": {"sha": "a" * 40}, "head": {"sha": "refs/heads/main"}}},
    ],
)
def test_event_parser_rejects_untrusted_coordinates(tmp_path, event):
    event_file = tmp_path / "event.json"
    output_file = tmp_path / "event-output"
    event_file.write_text(json.dumps(event), encoding="utf-8")
    environment = _workflow_environment()
    environment.update(
        {"GITHUB_EVENT_PATH": str(event_file), "GITHUB_OUTPUT": str(output_file)}
    )

    result = subprocess.run(
        [*_bash_command(), _step_script("Read trusted pull request coordinates")],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0


def test_boundary_fetches_pull_ref_then_materializes_only_the_validated_blob(tmp_path):
    runner, base_sha, head_sha, player_path, player_blob = _pull_ref_fixture(tmp_path)

    boundary, output_file = _run_boundary(runner, base_sha, head_sha, tmp_path)

    assert boundary.returncode == 0, boundary.stderr
    assert output_file.read_text(encoding="utf-8") == (
        f"player_path={player_path.as_posix()}\n"
    )
    assert not (runner / player_path).exists()

    environment = _workflow_environment()
    environment.update(
        {"HEAD_SHA": head_sha, "PLAYER_PATH": player_path.as_posix()}
    )
    materialize = subprocess.run(
        [*_bash_command(), _step_script("Materialize validated Player Update")],
        cwd=runner,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert materialize.returncode == 0, materialize.stderr
    assert (runner / player_path).read_text(encoding="utf-8") == player_blob
    assert not (runner / ".github").exists()


def test_boundary_rejects_player_plus_head_code_before_materialization(tmp_path):
    runner, base_sha, head_sha, player_path, _ = _pull_ref_fixture(
        tmp_path, extra_head_path=True
    )

    boundary, output_file = _run_boundary(runner, base_sha, head_sha, tmp_path)

    assert boundary.returncode != 0
    assert "must change exactly one file" in boundary.stderr
    assert not output_file.exists() or output_file.read_text(encoding="utf-8") == ""
    assert not (runner / player_path).exists()
    assert not (runner / ".github").exists()
