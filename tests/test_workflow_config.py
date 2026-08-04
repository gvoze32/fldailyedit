import os
import re
import subprocess
import textwrap
from pathlib import Path


FORM_PATH = Path(".github/ISSUE_TEMPLATE/player-spec.yml")
WORKFLOW_PATH = Path(".github/workflows/generate-player-spec.yml")

EXPECTED_FIELDS = (
    ("dropdown", "operation", "Operation"),
    ("input", "sortitoutsi_profile", "SortitoutSI profile"),
    ("input", "current_team", "Current team"),
    ("input", "effective_date", "Effective date"),
    ("textarea", "proof_urls", "Proof URLs"),
    ("textarea", "contributor_notes", "Contributor notes"),
    ("checkboxes", "confirmations", "Confirmations"),
)
EXPECTED_CONFIRMATIONS = (
    "I supplied source evidence.",
    "I did not derive PES ratings from Football Manager values.",
    "I understand a maintainer must review the draft PR.",
)


def _field_blocks(text: str) -> list[tuple[str, str]]:
    body = text.split("\nbody:\n", 1)[1]
    matches = list(re.finditer(r"(?m)^  - type: ([a-z]+)$", body))
    return [
        (match.group(1), body[match.start() : next_start])
        for match, next_start in zip(
            matches,
            [following.start() for following in matches[1:]] + [len(body)],
            strict=True,
        )
    ]


def _field_id(block: str) -> str:
    match = re.search(r"(?m)^    id: ([a-z0-9_]+)$", block)
    assert match is not None
    return match.group(1)


def _field_label(block: str) -> str:
    match = re.search(r"(?m)^      label: (.+)$", block)
    assert match is not None
    return match.group(1)


def _workflow_step_script(name: str) -> str:
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    match = re.search(
        rf"(?ms)^      - name: {re.escape(name)}$\n"
        r".*?^        run: \|\n(?P<script>.*?)(?=^      - name:|\Z)",
        text,
    )
    assert match is not None
    return textwrap.dedent(match.group("script"))


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def _remote_branch_fixture(
    tmp_path: Path,
    *,
    local_spec: str,
    remote_spec: str,
    extra_path: bool = False,
) -> tuple[Path, dict[str, str]]:
    origin = tmp_path / "origin.git"
    repository = tmp_path / "repository"
    runner_temp = tmp_path / "runner"
    output_path = tmp_path / "github-output"
    branch_name = "player-draft/issue-42"
    spec_path = Path("players/test-player.json")

    _git(tmp_path, "init", "--bare", str(origin))
    _git(tmp_path, "init", "-b", "main", str(repository))
    _git(repository, "config", "user.name", "Workflow Test")
    _git(repository, "config", "user.email", "workflow@example.invalid")
    (repository / "base.txt").write_text("base\n", encoding="utf-8")
    _git(repository, "add", "base.txt")
    _git(repository, "commit", "-m", "base")
    _git(repository, "remote", "add", "origin", str(origin))
    _git(repository, "push", "--set-upstream", "origin", "main")

    _git(repository, "switch", "--create", branch_name)
    (repository / spec_path).parent.mkdir(parents=True)
    (repository / spec_path).write_text(remote_spec, encoding="utf-8")
    _git(repository, "add", "--", spec_path.as_posix())
    if extra_path:
        (repository / "unexpected.txt").write_text("unexpected\n", encoding="utf-8")
        _git(repository, "add", "unexpected.txt")
    _git(repository, "commit", "-m", "remote draft")
    _git(repository, "push", "--set-upstream", "origin", branch_name)

    _git(repository, "switch", "main")
    (repository / spec_path).parent.mkdir(parents=True)
    (repository / spec_path).write_text(local_spec, encoding="utf-8")
    runner_temp.mkdir()
    environment = os.environ.copy()
    environment.update(
        {
            "BRANCH_NAME": branch_name,
            "GITHUB_OUTPUT": str(output_path),
            "RUNNER_TEMP": str(runner_temp),
            "SPEC_PATH": spec_path.as_posix(),
        }
    )
    return repository, environment


def test_issue_form_matches_the_generator_heading_contract_exactly():
    text = FORM_PATH.read_text(encoding="utf-8")
    fields = _field_blocks(text)

    assert tuple(
        (field_type, _field_id(block), _field_label(block))
        for field_type, block in fields
    ) == EXPECTED_FIELDS
    assert 'labels: ["player-spec"]' in text
    assert "generate-player-draft" not in text
    assert "draft" in text.lower()
    assert "not an approved player" in text.lower()


def test_issue_form_requires_inputs_and_exact_rendered_confirmations():
    text = FORM_PATH.read_text(encoding="utf-8")
    blocks = {_field_id(block): block for _type, block in _field_blocks(text)}

    for field_id in (
        "operation",
        "sortitoutsi_profile",
        "current_team",
        "effective_date",
        "proof_urls",
    ):
        assert re.search(
            r"(?m)^    validations:\n      required: true$", blocks[field_id]
        )
    assert "validations:" not in blocks["contributor_notes"]
    assert re.findall(r"(?m)^        - (create|update)$", blocks["operation"]) == [
        "create",
        "update",
    ]

    confirmation_options = re.findall(
        r"(?m)^      - label: (.+)\n        required: true$",
        blocks["confirmations"],
    )
    assert tuple(confirmation_options) == EXPECTED_CONFIRMATIONS
    assert tuple(f"- [X] {label}" for label in confirmation_options) == (
        "- [X] I supplied source evidence.",
        "- [X] I did not derive PES ratings from Football Manager values.",
        "- [X] I understand a maintainer must review the draft PR.",
    )


def test_generate_workflow_is_label_gated_and_minimally_privileged():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "types: [labeled]" in text
    assert "github.event.label.name == 'generate-player-draft'" in text
    assert "contents: write" in text
    assert "pull-requests: write" in text
    assert "issues: write" in text
    assert "concurrency: player-draft-${{ github.event.issue.number }}" in text
    assert "pull_request_target" not in text


def test_generate_workflow_uses_trusted_event_file_and_exact_machine_output():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "actions/checkout@v4" in text
    assert "ref: ${{ github.event.repository.default_branch }}" in text
    assert "persist-credentials: true" in text
    assert "actions/setup-python@v5" in text
    assert 'python-version: "3.13"' in text
    assert "python -m pip install -e ." in text
    assert 'python run.py players generate-draft --event "$GITHUB_EVENT_PATH" --output-dir players' in text
    assert 'startswith("SPEC_PATH=")' in text
    assert 'startswith("PLAYER_NAME=")' in text
    assert 'os.environ["GITHUB_OUTPUT"]' in text
    assert 'f"{name}<<{delimiter}\\n{value}\\n{delimiter}\\n"' in text


def test_generate_workflow_uses_safe_branch_and_one_idempotent_draft_pr():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert 'isinstance(issue_number, bool)' in text
    assert 'isinstance(issue_number, int)' in text
    assert 'issue_number <= 0' in text
    assert "player-draft/issue-${{ steps.event.outputs.issue_number }}" in text
    assert (
        'gh api --method GET "repos/$GITHUB_REPOSITORY/pulls"' in text
    )
    assert '-f "head=$GITHUB_REPOSITORY_OWNER:$BRANCH_NAME"' in text
    assert "-f state=all" in text
    assert 'head.get("repo", {}).get("full_name") != repository' in text
    assert 'head.get("ref") != branch_name' in text
    assert 'base.get("repo", {}).get("full_name") != repository' in text
    assert 'gh pr list --head' not in text
    assert 'git switch --create "$BRANCH_NAME"' in text
    assert 'git add -- "$SPEC_PATH"' in text
    assert 'git diff --cached --name-only' in text
    assert 'git push --set-upstream origin "$BRANCH_NAME"' in text
    assert "gh pr create" in text
    assert "--draft" in text
    assert '--title "player: draft $PLAYER_NAME"' in text
    assert 'gh issue comment "$ISSUE_NUMBER" --body "$COMMENT_BODY"' in text


def test_generate_workflow_recovers_only_an_exact_matching_base_repo_branch():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert (
        'git ls-remote --exit-code --heads origin "refs/heads/$BRANCH_NAME"'
        in text
    )
    assert 'refs/heads/$BRANCH_NAME:refs/remotes/origin/$BRANCH_NAME' in text
    assert 'git diff --name-only "HEAD...refs/remotes/origin/$BRANCH_NAME"' in text
    assert '[[ "$changed_paths" != "$SPEC_PATH" ]]' in text
    assert 'git show "refs/remotes/origin/$BRANCH_NAME:$SPEC_PATH"' in text
    assert 'cmp -- "$SPEC_PATH" "$remote_spec"' in text
    assert "steps.remote.outputs.branch_exists == 'false'" in text


def test_remote_branch_recovery_accepts_the_exact_generated_spec(tmp_path):
    repository, environment = _remote_branch_fixture(
        tmp_path,
        local_spec='{"name": "same"}\n',
        remote_spec='{"name": "same"}\n',
    )

    result = subprocess.run(
        ["bash", "-c", _workflow_step_script("Verify an existing base-repository branch")],
        cwd=repository,
        env=environment,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert Path(environment["GITHUB_OUTPUT"]).read_text(encoding="utf-8") == (
        "branch_exists=true\n"
    )


def test_remote_branch_recovery_rejects_a_different_spec_blob(tmp_path):
    repository, environment = _remote_branch_fixture(
        tmp_path,
        local_spec='{"name": "new"}\n',
        remote_spec='{"name": "old"}\n',
    )

    result = subprocess.run(
        ["bash", "-c", _workflow_step_script("Verify an existing base-repository branch")],
        cwd=repository,
        env=environment,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "remote issue branch spec differs from generated spec" in result.stderr


def test_remote_branch_recovery_rejects_an_additional_changed_path(tmp_path):
    repository, environment = _remote_branch_fixture(
        tmp_path,
        local_spec='{"name": "same"}\n',
        remote_spec='{"name": "same"}\n',
        extra_path=True,
    )

    result = subprocess.run(
        ["bash", "-c", _workflow_step_script("Verify an existing base-repository branch")],
        cwd=repository,
        env=environment,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "remote issue branch changes unexpected paths" in result.stderr


def test_generate_workflow_does_not_put_untrusted_event_data_in_shell_structure():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")

    for unsafe in (
        "github.event.issue.body",
        "github.event.issue.title",
        "github.actor",
        "GITHUB_ENV",
        "eval ",
        "eval\n",
    ):
        assert unsafe not in text

    run_blocks = re.findall(
        r"(?ms)^        run: \|\n(.*?)(?=^      - name:|\Z)", text
    )
    assert run_blocks
    assert all("${{ github.event.issue" not in block for block in run_blocks)
    assert text.count("shell: bash") == len(run_blocks)
