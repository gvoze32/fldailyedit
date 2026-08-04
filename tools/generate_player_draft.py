"""Generate one deliberately incomplete player spec from a trusted issue event."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit

from editor.player_spec import load_base_manifest, player_slug
from scraper.player_draft import (
    DraftSourceError,
    PlayerDraftSource,
    fetch_sortitoutsi_player_profile,
    parse_sortitoutsi_person_url,
)


_LABEL = "generate-player-draft"
_CONFIRMATIONS = (
    "- [x] I supplied source evidence.",
    "- [x] I did not derive PES ratings from Football Manager values.",
    "- [x] I understand a maintainer must review the draft PR.",
)
_HEADINGS = (
    "Operation",
    "SortitoutSI profile",
    "Current team",
    "Effective date",
    "Proof URLs",
    "Contributor notes",
    "Confirmations",
)
_MAX_FILENAME_BYTES = 240
_HEADING_RE = re.compile(r"^### ([^\r\n]+)$")
_GITHUB_ISSUE_PATH_RE = re.compile(
    r"^/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/issues/([1-9][0-9]*)$"
)


class PlayerDraftError(ValueError):
    """Raised when an issue event cannot safely produce one draft."""


@dataclass(frozen=True, slots=True)
class PlayerDraftRequest:
    """Validated issue-form data used to build a source-only draft."""

    operation: str
    profile_url: str
    current_team: str
    effective_date: str
    proof_urls: tuple[str, ...]
    issue_number: int
    issue_url: str


def _mapping(value: object, context: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise PlayerDraftError(f"{context} must be an object")
    return value


def _text(value: object, context: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise PlayerDraftError(f"{context} must be text")
    normalized = value.strip()
    if not normalized:
        raise PlayerDraftError(f"{context} must not be empty")
    if len(normalized) > maximum:
        raise PlayerDraftError(f"{context} exceeds {maximum} characters")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in normalized):
        raise PlayerDraftError(f"{context} contains control characters")
    return normalized


def _parse_form_body(body: object) -> dict[str, str]:
    if not isinstance(body, str):
        raise PlayerDraftError("issue body must be text")

    sections: dict[str, list[str]] = {}
    current: str | None = None
    heading_index = 0
    for line in body.splitlines():
        match = _HEADING_RE.fullmatch(line)
        if match is not None:
            heading = match.group(1)
            if heading not in _HEADINGS:
                raise PlayerDraftError(f"unexpected issue-form heading: {heading}")
            if heading in sections:
                raise PlayerDraftError(f"duplicate issue-form heading: {heading}")
            if heading_index >= len(_HEADINGS) or heading != _HEADINGS[heading_index]:
                raise PlayerDraftError("issue-form headings are missing or out of order")
            sections[heading] = []
            current = heading
            heading_index += 1
            continue
        if line.startswith("#"):
            raise PlayerDraftError("issue form contains a malformed heading")
        if current is None:
            if line.strip():
                raise PlayerDraftError("issue form contains content before its headings")
            continue
        sections[current].append(line)

    if tuple(sections) != _HEADINGS:
        raise PlayerDraftError("issue form must contain every exact heading once")
    return {heading: "\n".join(lines).strip() for heading, lines in sections.items()}


def _draft_filename(name: str) -> str:
    slug = player_slug(name)
    if not slug:
        raise PlayerDraftError("fetched player name cannot produce a safe filename")
    filename = f"{slug}.json"
    if len(filename.encode("utf-8")) > _MAX_FILENAME_BYTES:
        raise PlayerDraftError(
            f"player draft filename exceeds {_MAX_FILENAME_BYTES} UTF-8 bytes"
        )
    return filename


def _https_url(value: str, context: str) -> str:
    if any(character.isspace() for character in value):
        raise PlayerDraftError(f"{context} must be a valid HTTPS URL")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except (TypeError, ValueError):
        raise PlayerDraftError(f"{context} must be a valid HTTPS URL") from None
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
    ):
        raise PlayerDraftError(f"{context} must be a valid HTTPS URL")
    return value


def _profile_url(value: str) -> str:
    try:
        _person_id, normalized = parse_sortitoutsi_person_url(value)
    except DraftSourceError:
        raise PlayerDraftError("SortitoutSI profile must be one valid person URL") from None
    if normalized != value:
        raise PlayerDraftError("SortitoutSI profile must not contain a query or fragment")
    return normalized


def _issue_url(value: object, issue_number: int) -> str:
    url = _text(value, "issue URL", 500)
    _https_url(url, "issue URL")
    try:
        parsed = urlsplit(url)
    except ValueError:
        raise PlayerDraftError("issue URL must be a canonical GitHub issue URL") from None
    match = _GITHUB_ISSUE_PATH_RE.fullmatch(parsed.path)
    if (
        parsed.hostname != "github.com"
        or parsed.query
        or parsed.fragment
        or match is None
        or int(match.group(1)) != issue_number
    ):
        raise PlayerDraftError("issue URL must be a canonical GitHub issue URL")
    return url


def parse_player_issue_event(event: Mapping[str, object]) -> PlayerDraftRequest:
    """Validate one labeled GitHub issue event and parse its exact form body."""

    event = _mapping(event, "event")
    if event.get("action") != "labeled":
        raise PlayerDraftError("event action must be labeled")

    label = _mapping(event.get("label"), "event label")
    if label.get("name") != _LABEL:
        raise PlayerDraftError(f"event label must be {_LABEL}")

    issue = _mapping(event.get("issue"), "event issue")
    if issue.get("state") != "open":
        raise PlayerDraftError("issue must be open")
    author = _mapping(issue.get("user"), "issue author")
    if author.get("type") != "User":
        raise PlayerDraftError("issue author must be a human user")

    issue_number = issue.get("number")
    if (
        isinstance(issue_number, bool)
        or not isinstance(issue_number, int)
        or issue_number <= 0
    ):
        raise PlayerDraftError("issue number must be a positive integer")
    issue_url = _issue_url(issue.get("html_url"), issue_number)

    sections = _parse_form_body(issue.get("body"))
    operation = _text(sections["Operation"], "operation", 10)
    if operation not in {"create", "update"}:
        raise PlayerDraftError("operation must be create or update")

    raw_profile_url = _text(
        sections["SortitoutSI profile"], "SortitoutSI profile", 300
    )
    if len(raw_profile_url.splitlines()) != 1:
        raise PlayerDraftError("SortitoutSI profile must contain exactly one URL")
    profile_url = _profile_url(raw_profile_url)
    current_team = _text(sections["Current team"], "current team", 100)

    effective_date = _text(sections["Effective date"], "effective date", 10)
    try:
        parsed_date = date.fromisoformat(effective_date)
    except ValueError:
        raise PlayerDraftError("effective date must be a valid ISO date") from None
    if parsed_date.isoformat() != effective_date:
        raise PlayerDraftError("effective date must use YYYY-MM-DD")

    proof_lines = tuple(
        line.strip() for line in sections["Proof URLs"].splitlines() if line.strip()
    )
    if not proof_lines:
        raise PlayerDraftError("proof URLs must not be empty")
    if len(proof_lines) > 10:
        raise PlayerDraftError("proof URLs must contain at most 10 entries")
    proof_urls: list[str] = []
    seen_proofs: set[str] = set()
    for index, proof in enumerate(proof_lines, 1):
        proof = _text(proof, f"proof URL {index}", 300)
        proof = _https_url(proof, f"proof URL {index}")
        if proof in seen_proofs:
            raise PlayerDraftError("proof URLs must not contain duplicates")
        seen_proofs.add(proof)
        proof_urls.append(proof)

    notes = sections["Contributor notes"].strip()
    if len(notes) > 2_000:
        raise PlayerDraftError("contributor notes exceed 2000 characters")

    confirmations = tuple(sections["Confirmations"].splitlines())
    if confirmations != _CONFIRMATIONS:
        raise PlayerDraftError(
            "confirmations must contain the three exact checked statements in order"
        )

    return PlayerDraftRequest(
        operation=operation,
        profile_url=profile_url,
        current_team=current_team,
        effective_date=effective_date,
        proof_urls=tuple(proof_urls),
        issue_number=issue_number,
        issue_url=issue_url,
    )


def build_player_draft(
    request: PlayerDraftRequest, source: PlayerDraftSource
) -> dict[str, object]:
    """Build source provenance around intentionally missing PES review data."""

    try:
        request_person_id, _request_url = parse_sortitoutsi_person_url(
            request.profile_url
        )
        source_person_id, source_profile_url = parse_sortitoutsi_person_url(
            source.profile_url
        )
    except DraftSourceError:
        raise PlayerDraftError("fetched source profile is invalid") from None
    if (
        request_person_id != source_person_id
        or source_person_id != source.sortitoutsi_id
    ):
        raise PlayerDraftError("fetched source profile does not match the request")

    _draft_filename(source.name)

    missing = (
        ["identity.pes_id", "identity.print_name", "pes"]
        if request.operation == "create"
        else ["identity.pes_id", "pes.abilities.<field>.from/to"]
    )
    return {
        "schema_version": 1,
        "operation": request.operation,
        "lifecycle": {"status": "active"},
        "applies_to": [load_base_manifest().revision],
        "identity": {
            "name": source.name,
            "print_name": None,
            "aliases": [source.name],
            "pes_id": None,
            "sortitoutsi_id": source.sortitoutsi_id,
        },
        "source": {
            "profile_url": source_profile_url,
            "date_of_birth": source.date_of_birth,
            "nationality": source.nationality,
            "positions": list(source.positions),
            "current_club": source.current_club,
        },
        "evidence": {
            "profile_url": source_profile_url,
            "proof_urls": list(request.proof_urls),
            "effective_date": request.effective_date,
            "current_team": request.current_team,
            "issue_number": request.issue_number,
            "issue_url": request.issue_url,
        },
        "pes": None,
        "draft": {"needs_human_review": True, "missing": missing},
    }


def _load_event(event_path: Path) -> Mapping[str, object]:
    try:
        raw = json.loads(event_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PlayerDraftError(f"cannot read issue event: {exc}") from exc
    return _mapping(raw, "event")


def _write_exclusive_atomic(destination: Path, content: str) -> None:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.stem}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())

        try:
            os.link(temporary_path, destination)
        except FileExistsError:
            raise PlayerDraftError(f"player draft already exists: {destination}") from None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def write_player_draft(event_path: Path, output_dir: Path) -> Path:
    """Fetch source metadata and atomically write one canonical draft file."""

    event_path = Path(event_path)
    output_dir = Path(output_dir)
    request = parse_player_issue_event(_load_event(event_path))
    try:
        source = asyncio.run(fetch_sortitoutsi_player_profile(request.profile_url))
    except PlayerDraftError:
        raise
    except Exception as exc:
        raise PlayerDraftError(f"cannot fetch player profile: {exc}") from exc
    filename = _draft_filename(source.name)
    payload = build_player_draft(request, source)

    output_dir.mkdir(parents=True, exist_ok=True)
    if not output_dir.is_dir():
        raise PlayerDraftError(f"output directory is not a directory: {output_dir}")
    destination = output_dir / filename
    content = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    _write_exclusive_atomic(destination, content)
    return destination
