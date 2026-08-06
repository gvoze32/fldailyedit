"""Trusted-origin contracts for generated Player Update proposals."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tools.check_player_proposal_origin import (
    ProposalOriginError,
    validate_player_proposal_origin,
)


BASE_REPO = "owner/repo"
ISSUE_NUMBER = 42
ISSUE_URL = f"https://github.com/{BASE_REPO}/issues/{ISSUE_NUMBER}"
HEAD_REF = f"player-draft/issue-{ISSUE_NUMBER}"
ORIGIN_CHECKER = Path("tools/check_player_proposal_origin.py")


def completed_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "operation": "update",
        "identity": {"name": "Reviewed Player"},
    }


def generated_payload(
    *,
    issue_number: object = ISSUE_NUMBER,
    issue_url: object = ISSUE_URL,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "operation": "update",
        "source": {
            "model": "pes-retro-profile-snapshot-v1",
            "snapshot_sha256": "0" * 64,
        },
        "evidence": {
            "issue_number": issue_number,
            "issue_url": issue_url,
        },
        "draft": {
            "generator": "pes-retro-mature-proposal-v1",
            "needs_human_review": True,
        },
    }


def run_origin_checker(
    spec_path: Path,
    *,
    base_repo: str = BASE_REPO,
    head_repo: str = BASE_REPO,
    head_ref: str = HEAD_REF,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(ORIGIN_CHECKER),
            "--spec",
            str(spec_path),
            "--base-repo",
            base_repo,
            "--head-repo",
            head_repo,
            "--head-ref",
            head_ref,
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def test_generated_proposal_requires_matching_same_repository_issue_branch() -> None:
    assert (
        validate_player_proposal_origin(
            generated_payload(),
            base_repo=BASE_REPO,
            head_repo=BASE_REPO,
            head_ref=HEAD_REF,
        )
        is None
    )


@pytest.mark.parametrize(
    ("head_repo", "head_ref", "issue_number"),
    [
        pytest.param("fork/repo", HEAD_REF, ISSUE_NUMBER, id="fork"),
        pytest.param(
            BASE_REPO,
            "feature/fabricated-proposal",
            ISSUE_NUMBER,
            id="unrelated-branch",
        ),
        pytest.param(
            BASE_REPO,
            "player-draft/issue-41",
            ISSUE_NUMBER,
            id="issue-branch-mismatch",
        ),
    ],
)
def test_generated_proposal_rejects_untrusted_origin(
    head_repo: str,
    head_ref: str,
    issue_number: int,
) -> None:
    with pytest.raises(ProposalOriginError):
        validate_player_proposal_origin(
            generated_payload(issue_number=issue_number),
            base_repo=BASE_REPO,
            head_repo=head_repo,
            head_ref=head_ref,
        )


def test_completed_spec_remains_allowed_from_a_fork() -> None:
    assert (
        validate_player_proposal_origin(
            completed_payload(),
            base_repo=BASE_REPO,
            head_repo="fork/repo",
            head_ref="player/update",
        )
        is None
    )


@pytest.mark.parametrize("missing_key", ["source", "draft"])
def test_generated_proposal_rejects_partial_origin_shape(missing_key: str) -> None:
    payload = generated_payload()
    del payload[missing_key]

    with pytest.raises(ProposalOriginError):
        validate_player_proposal_origin(
            payload,
            base_repo=BASE_REPO,
            head_repo=BASE_REPO,
            head_ref=HEAD_REF,
        )


@pytest.mark.parametrize("null_key", ["source", "draft"])
def test_generated_proposal_rejects_null_partial_origin_shape(
    null_key: str,
) -> None:
    payload = generated_payload()
    payload[null_key] = None

    with pytest.raises(ProposalOriginError):
        validate_player_proposal_origin(
            payload,
            base_repo=BASE_REPO,
            head_repo=BASE_REPO,
            head_ref=HEAD_REF,
        )


@pytest.mark.parametrize(
    "generator",
    [
        pytest.param("pes-retro-mature-proposal-v2", id="wrong-model"),
        pytest.param("", id="empty"),
        pytest.param(None, id="null"),
        pytest.param(True, id="bool"),
    ],
)
def test_generated_proposal_rejects_noncanonical_generator(generator: object) -> None:
    payload = generated_payload()
    draft = payload["draft"]
    assert isinstance(draft, dict)
    draft["generator"] = generator

    with pytest.raises(ProposalOriginError):
        validate_player_proposal_origin(
            payload,
            base_repo=BASE_REPO,
            head_repo=BASE_REPO,
            head_ref=HEAD_REF,
        )


@pytest.mark.parametrize(
    "issue_number",
    [
        pytest.param(0, id="zero"),
        pytest.param(-1, id="negative"),
        pytest.param(True, id="true"),
        pytest.param(False, id="false"),
        pytest.param(42.0, id="float"),
        pytest.param("42", id="string"),
        pytest.param(None, id="null"),
    ],
)
def test_generated_proposal_requires_a_positive_non_bool_issue_number(
    issue_number: object,
) -> None:
    with pytest.raises(ProposalOriginError):
        validate_player_proposal_origin(
            generated_payload(issue_number=issue_number),
            base_repo=BASE_REPO,
            head_repo=BASE_REPO,
            head_ref=HEAD_REF,
        )


@pytest.mark.parametrize(
    "issue_url",
    [
        pytest.param(
            "https://github.com/fork/repo/issues/42",
            id="wrong-repository",
        ),
        pytest.param(
            "https://github.com/owner/repo/issues/41",
            id="wrong-number",
        ),
        pytest.param(
            "http://github.com/owner/repo/issues/42",
            id="wrong-scheme",
        ),
        pytest.param(f"{ISSUE_URL}/", id="trailing-slash"),
        pytest.param(f"{ISSUE_URL}?from=workflow", id="query"),
        pytest.param(f"{ISSUE_URL}#issuecomment-1", id="fragment"),
        pytest.param(f"{ISSUE_URL}\nINJECTED=1", id="control-character"),
    ],
)
def test_generated_proposal_requires_the_canonical_base_issue_url(
    issue_url: str,
) -> None:
    with pytest.raises(ProposalOriginError):
        validate_player_proposal_origin(
            generated_payload(issue_url=issue_url),
            base_repo=BASE_REPO,
            head_repo=BASE_REPO,
            head_ref=HEAD_REF,
        )


@pytest.mark.parametrize(
    ("base_repo", "head_repo", "head_ref"),
    [
        pytest.param(
            "owner//repo",
            "owner//repo",
            HEAD_REF,
            id="empty-repository-component",
        ),
        pytest.param(
            "owner/repo/extra",
            "owner/repo/extra",
            HEAD_REF,
            id="extra-repository-component",
        ),
        pytest.param(
            "owner/repo\nINJECTED=1",
            "owner/repo\nINJECTED=1",
            HEAD_REF,
            id="repository-control-character",
        ),
        pytest.param(
            BASE_REPO,
            BASE_REPO,
            f"{HEAD_REF}\nINJECTED=1",
            id="branch-control-character",
        ),
        pytest.param(
            BASE_REPO,
            BASE_REPO,
            f"refs/heads/{HEAD_REF}",
            id="fully-qualified-branch",
        ),
        pytest.param(
            BASE_REPO,
            BASE_REPO,
            f"{HEAD_REF}/",
            id="trailing-branch-separator",
        ),
    ],
)
def test_generated_proposal_rejects_noncanonical_repository_or_branch_spelling(
    base_repo: str,
    head_repo: str,
    head_ref: str,
) -> None:
    with pytest.raises(ProposalOriginError):
        validate_player_proposal_origin(
            generated_payload(),
            base_repo=base_repo,
            head_repo=head_repo,
            head_ref=head_ref,
        )


def test_origin_checker_cli_accepts_one_json_without_printing_payload(
    tmp_path: Path,
) -> None:
    spec_path = tmp_path / "reviewed-player.json"
    spec_path.write_text(json.dumps(completed_payload()), encoding="utf-8")

    result = run_origin_checker(
        spec_path,
        head_repo="fork/repo",
        head_ref="player/update",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert result.stderr == ""


@pytest.mark.parametrize(
    "missing_option",
    ["--spec", "--base-repo", "--head-repo", "--head-ref"],
)
def test_origin_checker_cli_requires_every_origin_argument(
    tmp_path: Path,
    missing_option: str,
) -> None:
    spec_path = tmp_path / "proposal.json"
    spec_path.write_text(json.dumps(generated_payload()), encoding="utf-8")
    command = [
        sys.executable,
        str(ORIGIN_CHECKER),
        "--spec",
        str(spec_path),
        "--base-repo",
        BASE_REPO,
        "--head-repo",
        BASE_REPO,
        "--head-ref",
        HEAD_REF,
    ]
    option_index = command.index(missing_option)
    del command[option_index : option_index + 2]

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert result.stdout == ""
    assert missing_option in result.stderr


def test_origin_checker_cli_rejects_more_than_one_json_value(tmp_path: Path) -> None:
    spec_path = tmp_path / "two-values.json"
    serialized = json.dumps(completed_payload())
    spec_path.write_text(f"{serialized}\n{serialized}\n", encoding="utf-8")

    result = run_origin_checker(spec_path)

    assert result.returncode != 0
    assert result.stdout == ""


def test_origin_checker_cli_rejects_oversized_json_without_echoing_it(
    tmp_path: Path,
) -> None:
    spec_path = tmp_path / "oversized.json"
    sentinel = "PRIVATE-PAYLOAD-MUST-NOT-BE-PRINTED"
    spec_path.write_bytes(
        b'{"private":"'
        + sentinel.encode("ascii")
        + b"x" * (2 * 1024 * 1024)
        + b'"}'
    )

    result = run_origin_checker(spec_path)

    assert result.returncode != 0
    assert result.stdout == ""
    assert sentinel not in result.stderr


def test_origin_checker_cli_does_not_echo_a_rejected_payload(tmp_path: Path) -> None:
    spec_path = tmp_path / "untrusted.json"
    sentinel = "PRIVATE-PAYLOAD-MUST-NOT-BE-PRINTED"
    payload = generated_payload()
    payload["private"] = sentinel
    spec_path.write_text(json.dumps(payload), encoding="utf-8")

    result = run_origin_checker(spec_path, head_repo="fork/repo")

    assert result.returncode != 0
    assert result.stdout == ""
    assert sentinel not in result.stderr
