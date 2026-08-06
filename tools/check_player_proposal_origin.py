"""Validate the trusted origin of generated player update proposals."""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections.abc import Mapping, Sequence
from pathlib import Path


_MAX_SPEC_BYTES = 2 * 1024 * 1024
_GENERATOR = "pes-retro-mature-proposal-v1"
_OWNER_COMPONENT = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?"
_REPOSITORY_COMPONENT = r"[A-Za-z0-9._-]{1,100}"
_REPOSITORY = re.compile(rf"{_OWNER_COMPONENT}/{_REPOSITORY_COMPONENT}")
_INVALID_REF_CHARACTER = re.compile(r"[\x00-\x20\x7f~^:?*\[\\]")


class ProposalOriginError(ValueError):
    """A player proposal does not have an allowed origin."""


def _canonical_text(value: object, label: str) -> str:
    if type(value) is not str or not value:
        raise ProposalOriginError(f"{label} must be non-empty text")
    if value != value.strip():
        raise ProposalOriginError(f"{label} must use canonical spelling")
    if any(
        unicodedata.category(character).startswith("C")
        or unicodedata.category(character) in {"Zl", "Zp"}
        for character in value
    ):
        raise ProposalOriginError(f"{label} contains a control character")
    return value


def _canonical_repository(value: object, label: str) -> str:
    repository = _canonical_text(value, label)
    if _REPOSITORY.fullmatch(repository) is None:
        raise ProposalOriginError(f"{label} must be a canonical owner/repository name")
    owner, name = repository.split("/", 1)
    if owner.endswith("-") or name in {".", ".."}:
        raise ProposalOriginError(f"{label} must be a canonical owner/repository name")
    return repository


def _canonical_head_ref(value: object) -> str:
    head_ref = _canonical_text(value, "head ref")
    if (
        len(head_ref) > 255
        or head_ref.startswith(("-", "/", "refs/"))
        or head_ref.endswith(("/", "."))
        or "//" in head_ref
        or ".." in head_ref
        or "@{" in head_ref
        or head_ref == "@"
        or _INVALID_REF_CHARACTER.search(head_ref) is not None
    ):
        raise ProposalOriginError("head ref must use canonical branch spelling")
    for component in head_ref.split("/"):
        if (
            not component
            or component.startswith(".")
            or component.endswith(".lock")
        ):
            raise ProposalOriginError("head ref must use canonical branch spelling")
    return head_ref


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ProposalOriginError(f"{label} must be an object")
    return value


def validate_player_proposal_origin(
    payload: object,
    *,
    base_repo: str,
    head_repo: str,
    head_ref: str,
) -> None:
    """Reject generated proposals that did not come from their trusted issue branch."""

    proposal = _mapping(payload, "proposal")
    canonical_base_repo = _canonical_repository(base_repo, "base repository")
    canonical_head_repo = _canonical_repository(head_repo, "head repository")
    canonical_head_ref = _canonical_head_ref(head_ref)

    has_source = "source" in proposal
    has_draft = "draft" in proposal
    if not has_source and not has_draft:
        return
    if has_source != has_draft:
        raise ProposalOriginError("generated proposal must contain source and draft")

    _mapping(proposal["source"], "source")
    draft = _mapping(proposal["draft"], "draft")
    generator = _canonical_text(draft.get("generator"), "generator")
    if generator != _GENERATOR:
        raise ProposalOriginError("generated proposal uses an untrusted generator")

    evidence = _mapping(proposal.get("evidence"), "evidence")
    issue_number = evidence.get("issue_number")
    if type(issue_number) is not int or issue_number <= 0:
        raise ProposalOriginError("issue number must be a positive integer")

    issue_url = _canonical_text(evidence.get("issue_url"), "issue URL")
    expected_issue_url = (
        f"https://github.com/{canonical_base_repo}/issues/{issue_number}"
    )
    if issue_url != expected_issue_url:
        raise ProposalOriginError("issue URL must match the canonical base issue URL")
    if canonical_head_repo != canonical_base_repo:
        raise ProposalOriginError("generated proposal must come from the base repository")

    expected_head_ref = f"player-draft/issue-{issue_number}"
    if canonical_head_ref != expected_head_ref:
        raise ProposalOriginError("generated proposal must come from its issue branch")


def _json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ProposalOriginError("proposal contains a duplicate JSON key")
        result[key] = value
    return result


def _read_spec(path: Path) -> Mapping[str, object]:
    try:
        with path.open("rb") as spec_file:
            raw_bytes = spec_file.read(_MAX_SPEC_BYTES + 1)
        if len(raw_bytes) > _MAX_SPEC_BYTES:
            raise ProposalOriginError("proposal JSON exceeds the maximum size")
        payload = json.loads(
            raw_bytes.decode("utf-8"),
            object_pairs_hook=_json_object,
        )
    except ProposalOriginError:
        raise
    except RecursionError:
        raise ProposalOriginError("proposal JSON is too deeply nested") from None
    except (OSError, UnicodeError, ValueError):
        raise ProposalOriginError("cannot read proposal JSON") from None
    return _mapping(payload, "proposal")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate the trusted origin of a player proposal."
    )
    parser.add_argument("--spec", required=True, type=Path)
    parser.add_argument("--base-repo", required=True)
    parser.add_argument("--head-repo", required=True)
    parser.add_argument("--head-ref", required=True)
    args = parser.parse_args(argv)

    try:
        payload = _read_spec(args.spec)
        validate_player_proposal_origin(
            payload,
            base_repo=args.base_repo,
            head_repo=args.head_repo,
            head_ref=args.head_ref,
        )
    except ProposalOriginError as error:
        print(f"player proposal origin guard: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
