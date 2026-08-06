"""Generate one reviewable player proposal from a trusted issue event."""

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
from uuid import UUID

import config
from editor import crypto
from editor.editfile import EditFile
from editor.player_spec import (
    PlayerSpecError,
    _load_proposal_spec,
    load_base_manifest,
    load_player_specs,
    normalize_player_identity,
    player_slug,
    verify_base_file,
)
from scraper.pes_retro_snapshot import profile_from_snapshot, profile_to_snapshot
from scraper.pes21_proposal import Pes21Proposal, map_pes21_proposal
from scraper.pes_retro_stats import (
    PesRetroStatsError,
    PesRetroStatsProfile,
    fetch_pes_retro_stats_profile,
    parse_pes_retro_stats_url,
)
from tools.player_draft_diff import (
    PlayerDraftDiffError,
    build_update_pes,
    resolve_update_player,
)
from tools.player_proposal_resolution import (
    NationalityCatalog,
    load_nationality_catalog,
    resolve_create_fields,
)
from tools.player_proposal_review import build_ovr_review


_LABEL = "generate-player-draft"
_CONFIRMATIONS = (
    "- [X] I supplied one canonical Pes Retro Stats player profile.",
    "- [X] I understand autofilled PES values are unapproved proposals.",
    "- [X] I understand a maintainer must review the draft PR.",
)
_HEADINGS = (
    "Operation",
    "Player name",
    "Pes Retro Stats profile",
    "Current team",
    "Effective date",
    "Proof URLs",
    "Contributor notes",
    "Confirmations",
)
_MAX_FILENAME_BYTES = 240
_MAX_PLAYER_NAME_BYTES = 60
_HEADING_RE = re.compile(r"^### ([^\r\n]+)$")
_GITHUB_ISSUE_PATH_RE = re.compile(
    r"^/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/issues/([1-9][0-9]*)$"
)


class PlayerDraftError(ValueError):
    """Raised when an issue event cannot safely produce one draft."""


@dataclass(frozen=True, slots=True)
class PlayerDraftRequest:
    """Validated issue-form data used to build one reviewable proposal."""

    operation: str
    player_name: str
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


def _player_name(value: object) -> str:
    name = _text(value, "Player name", _MAX_PLAYER_NAME_BYTES)
    if len(name.splitlines()) != 1:
        raise PlayerDraftError("Player name must contain exactly one name")
    if " ".join(name.split()) != name:
        raise PlayerDraftError("Player name must use canonical whitespace")
    if len(name.encode("utf-8")) > _MAX_PLAYER_NAME_BYTES:
        raise PlayerDraftError(
            f"Player name exceeds {_MAX_PLAYER_NAME_BYTES} UTF-8 bytes"
        )
    if not normalize_player_identity(name):
        raise PlayerDraftError("Player name must contain a canonical identity")
    return name


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
        raise PlayerDraftError("submitted player name cannot produce a safe filename")
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
        _short_id, canonical = parse_pes_retro_stats_url(value)
    except PesRetroStatsError as exc:
        raise PlayerDraftError(str(exc)) from None
    if canonical != value:
        raise PlayerDraftError("Pes Retro Stats profile must be canonical")
    return canonical


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

    player_name = _player_name(sections["Player name"])
    raw_profile_url = _text(
        sections["Pes Retro Stats profile"], "Pes Retro Stats profile", 300
    )
    if len(raw_profile_url.splitlines()) != 1:
        raise PlayerDraftError(
            "Pes Retro Stats profile must contain exactly one URL"
        )
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
        player_name=player_name,
        profile_url=profile_url,
        current_team=current_team,
        effective_date=effective_date,
        proof_urls=tuple(proof_urls),
        issue_number=issue_number,
        issue_url=issue_url,
    )


def _validated_source(
    request: PlayerDraftRequest, source: PesRetroStatsProfile
) -> str:
    try:
        request_short_id, request_url = parse_pes_retro_stats_url(request.profile_url)
        source_short_id, source_url = parse_pes_retro_stats_url(source.profile_url)
        source_uuid = UUID(source.player_id)
    except (PesRetroStatsError, TypeError, ValueError, AttributeError):
        raise PlayerDraftError("fetched Pes Retro Stats profile is invalid") from None
    if (
        request_url != source_url
        or request_short_id != source_short_id
        or source.short_id != source_short_id
        or str(source_uuid) != source.player_id
        or source.player_id[:8] != source_short_id
    ):
        raise PlayerDraftError(
            "fetched Pes Retro Stats profile does not match the request"
        )
    if normalize_player_identity(request.player_name) != normalize_player_identity(
        source.name
    ):
        raise PlayerDraftError(
            "Pes Retro Stats profile name does not match Player name"
        )
    return source_url



def _evidence_payload(
    request: PlayerDraftRequest, profile_url: str
) -> dict[str, object]:
    return {
        "profile_url": profile_url,
        "proof_urls": list(request.proof_urls),
        "effective_date": request.effective_date,
        "current_team": request.current_team,
        "issue_number": request.issue_number,
        "issue_url": request.issue_url,
    }


def _create_pes(
    request: PlayerDraftRequest,
    proposal: Pes21Proposal,
    *,
    player_id: int,
    print_name: str,
    team_id: int,
    team_name: str,
    nationality_id: int,
    skin_color: int,
    iris_color: int,
    shirt_number: int | None,
) -> dict[str, object]:
    if proposal.registered_position is None:
        unsupported = ", ".join(proposal.unsupported_positions) or "unknown"
        raise PlayerDraftError(
            f"Pes Retro Stats profile has unsupported registered position: {unsupported}"
        )
    result: dict[str, object] = {
        "player_id": player_id,
        "name": request.player_name,
        "print_name": print_name,
        "team_id": team_id,
        "team_name": team_name,
        "nationality_id": nationality_id,
        "age": proposal.age,
        "height": proposal.height,
        "weight": proposal.weight,
        "registered_position": proposal.registered_position,
        "playing_style": proposal.playing_style,
        "strong_foot": proposal.strong_foot,
        "weak_foot_usage": proposal.weak_foot_usage,
        "weak_foot_accuracy": proposal.weak_foot_accuracy,
        "form": proposal.form,
        "injury_resistance": proposal.injury_resistance,
        "position_proficiency": {
            position: grade
            for position, grade in proposal.position_proficiency.items()
            if grade
        },
        "abilities": dict(proposal.abilities),
        "player_skills": list(proposal.player_skills),
        "com_styles": list(proposal.com_styles),
        "skin_color": skin_color,
        "iris_color": iris_color,
    }
    if type(shirt_number) is int and 1 <= shirt_number <= 99:
        result["preferred_shirt_number"] = shirt_number
    return result


def build_player_draft(
    request: PlayerDraftRequest,
    source: PesRetroStatsProfile,
    proposal: Pes21Proposal,
    *,
    edit_file: EditFile | None = None,
    nationality_catalog: NationalityCatalog | None = None,
    completed_player_ids: set[int] | None = None,
    team_aliases: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Build one complete, reviewable create or update proposal."""

    profile_url = _validated_source(request, source)
    _draft_filename(request.player_name)
    if edit_file is None:
        raise PlayerDraftError("a verified base EDIT file is required")

    identity: dict[str, object] = {
        "name": request.player_name,
        "print_name": None,
        "aliases": [request.player_name],
        "pes_id": None,
        "pes_retro_stats_id": source.player_id,
    }
    try:
        if request.operation == "create":
            resolution = resolve_create_fields(
                edit_file,
                source=source,
                submitted_team=request.current_team,
                completed_player_ids=(
                    completed_player_ids if completed_player_ids is not None else set()
                ),
                nationality_catalog=nationality_catalog,
                team_aliases=team_aliases,
            )
            pes = _create_pes(
                request,
                proposal,
                player_id=resolution.player_id,
                print_name=resolution.print_name,
                team_id=resolution.team_id,
                team_name=resolution.team_name,
                nationality_id=resolution.nationality_id,
                skin_color=resolution.skin_color,
                iris_color=resolution.iris_color,
                shirt_number=source.shirt_number,
            )
            identity["print_name"] = resolution.print_name
            identity["pes_id"] = resolution.player_id
            ovr_review = build_ovr_review(
                operation="create",
                proposal_abilities=proposal.abilities,
                registered_position=proposal.registered_position or "",
                position_proficiency=proposal.position_proficiency,
            )
        elif request.operation == "update":
            match = resolve_update_player(
                edit_file,
                canonical_name=request.player_name,
                current_team=request.current_team,
                source=source,
                proposal=proposal,
            )
            pes = build_update_pes(match.profile, proposal)
            identity["print_name"] = match.print_name
            identity["pes_id"] = match.pes_id
            ovr_review = build_ovr_review(
                operation="update",
                base_abilities=match.profile.abilities,
                proposal_abilities=proposal.abilities,
                registered_position=(
                    proposal.registered_position
                    or match.profile.registered_position
                ),
                position_proficiency=proposal.position_proficiency,
            )
        else:
            raise PlayerDraftError(f"unsupported operation: {request.operation!r}")
    except PlayerDraftDiffError as exc:
        raise PlayerDraftError(str(exc)) from None
    except PlayerDraftError:
        raise
    except (TypeError, ValueError, KeyError, AttributeError) as exc:
        raise PlayerDraftError(str(exc)) from None

    try:
        source_snapshot = profile_to_snapshot(source)
    except (TypeError, ValueError, KeyError, AttributeError) as exc:
        raise PlayerDraftError(f"cannot snapshot source profile: {exc}") from None

    return {
        "schema_version": 2,
        "operation": request.operation,
        "lifecycle": {"status": "active"},
        "applies_to": [load_base_manifest().revision],
        "identity": identity,
        "source": source_snapshot,
        "evidence": _evidence_payload(request, profile_url),
        "pes": pes,
        "draft": {
            "generator": "pes-retro-mature-proposal-v1",
            "needs_human_review": True,
            "ovr_review": ovr_review,
        },
    }


_MAX_PROPOSAL_BYTES = 2 * 1024 * 1024


def _proposal_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise PlayerDraftError(f"proposal contains duplicate JSON key {key!r}")
        result[key] = value
    return result


def _load_generated_proposal(path: Path) -> Mapping[str, object]:
    try:
        with path.open("rb") as handle:
            raw_bytes = handle.read(_MAX_PROPOSAL_BYTES + 1)
        if len(raw_bytes) > _MAX_PROPOSAL_BYTES:
            raise PlayerDraftError("proposal JSON exceeds the maximum size")
        payload = json.loads(
            raw_bytes.decode("utf-8"),
            object_pairs_hook=_proposal_json_object,
        )
    except PlayerDraftError:
        raise
    except RecursionError:
        raise PlayerDraftError(
            "cannot read generated proposal: JSON is too deeply nested"
        ) from None
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PlayerDraftError(f"cannot read generated proposal: {exc}") from None
    return _mapping(payload, "proposal")


def _canonical_value(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _first_proposal_difference(
    expected: object,
    actual: object,
    path: str = "",
) -> str | None:
    if isinstance(expected, Mapping) and isinstance(actual, Mapping):
        keys = sorted(set(expected) | set(actual))
        for key in keys:
            child = f"{path}.{key}" if path else str(key)
            if key not in expected or key not in actual:
                return child
            difference = _first_proposal_difference(expected[key], actual[key], child)
            if difference is not None:
                return difference
        return None
    if isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            return path or "$"
        if expected != actual:
            expected_items = sorted(_canonical_value(item) for item in expected)
            actual_items = sorted(_canonical_value(item) for item in actual)
            if expected_items == actual_items:
                return path or "$"
        for index, (expected_item, actual_item) in enumerate(
            zip(expected, actual, strict=True)
        ):
            child = f"{path}[{index}]" if path else f"[{index}]"
            difference = _first_proposal_difference(
                expected_item, actual_item, child
            )
            if difference is not None:
                return difference
        return None
    if type(expected) is not type(actual) or expected != actual:
        return path or "$"
    return None


def validate_generated_proposal(
    path: Path, edit_file: EditFile
) -> Mapping[str, object]:
    """Recompute one generated proposal from its embedded offline evidence."""

    path = Path(path)
    actual = _load_generated_proposal(path)
    try:
        _load_proposal_spec(path, actual)
    except PlayerSpecError as exc:
        raise PlayerDraftError(str(exc)) from None
    try:
        operation = actual["operation"]
        identity = _mapping(actual["identity"], "proposal identity")
        evidence = _mapping(actual["evidence"], "proposal evidence")
        source_snapshot = actual["source"]
        if not isinstance(operation, str):
            raise PlayerDraftError("proposal operation must be text")
        player_name = identity["name"]
        profile_url = evidence["profile_url"]
        current_team = evidence["current_team"]
        effective_date = evidence["effective_date"]
        proof_urls = evidence["proof_urls"]
        issue_number = evidence["issue_number"]
        issue_url = evidence["issue_url"]
        if (
            not isinstance(player_name, str)
            or not isinstance(profile_url, str)
            or not isinstance(current_team, str)
            or not isinstance(effective_date, str)
            or not isinstance(proof_urls, list)
            or not isinstance(issue_number, int)
            or isinstance(issue_number, bool)
            or not isinstance(issue_url, str)
        ):
            raise PlayerDraftError("proposal evidence is incomplete")
        try:
            source = profile_from_snapshot(source_snapshot)
        except ValueError as exc:
            message = str(exc)
            if "snapshot_sha256 mismatch" in message:
                source_data = (
                    source_snapshot.get("data")
                    if isinstance(source_snapshot, Mapping)
                    else None
                )
                source_club = (
                    source_data.get("current_club")
                    if isinstance(source_data, Mapping)
                    else None
                )
                field = (
                    "data.current_club"
                    if source_club != current_team
                    else "snapshot_sha256"
                )
                raise PlayerDraftError(
                    f"proposal mismatch at source.{field}"
                ) from None
            raise
        request = PlayerDraftRequest(
            operation=operation,
            player_name=player_name,
            profile_url=profile_url,
            current_team=current_team,
            effective_date=effective_date,
            proof_urls=tuple(proof_urls),
            issue_number=issue_number,
            issue_url=issue_url,
        )
        proposal = map_pes21_proposal(
            source, effective_date=date.fromisoformat(effective_date)
        )
        completed_player_ids = _base_and_completed_player_ids(edit_file)
        if operation == "create":
            expected = build_player_draft(
                request,
                source,
                proposal,
                edit_file=edit_file,
                nationality_catalog=load_nationality_catalog(),
                completed_player_ids=completed_player_ids,
                team_aliases=_load_team_aliases(),
            )
        else:
            expected = build_player_draft(
                request,
                source,
                proposal,
                edit_file=edit_file,
            )
    except PlayerDraftError:
        raise
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise PlayerDraftError(f"cannot recompute generated proposal: {exc}") from None

    difference = _first_proposal_difference(expected, actual)
    if difference is not None:
        raise PlayerDraftError(f"proposal mismatch at {difference}")
    return actual


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


def _configured_player_ids() -> set[int]:
    """Return completed configured IDs after loading all specs."""

    specs = load_player_specs(allow_proposals=True)
    return {
        spec.identity.pes_id
        for spec in specs
        if spec.proposal is None and type(spec.identity.pes_id) is int
    }


def _base_and_completed_player_ids(edit_file: EditFile) -> set[int]:
    player_ids = _configured_player_ids()
    try:
        players = edit_file.get_all_players()
    except Exception as exc:
        raise PlayerDraftError(
            f"cannot read verified base players: {exc}"
        ) from None
    if not isinstance(players, Mapping):
        raise PlayerDraftError(
            "cannot read verified base players: expected a mapping"
        )
    invalid_ids = tuple(
        player_id for player_id in players if type(player_id) is not int
    )
    if invalid_ids:
        raise PlayerDraftError(
            "cannot read verified base players: invalid player IDs"
        )
    player_ids.update(players)
    return player_ids


def _load_team_aliases() -> Mapping[str, str]:
    try:
        raw = json.loads(config.TEAM_ALIASES_FILE.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PlayerDraftError(f"cannot load team aliases: {exc}") from None
    if not isinstance(raw, Mapping):
        raise PlayerDraftError("cannot load team aliases: expected an object")
    return dict(raw)


def write_player_draft(
    event_path: Path,
    output_dir: Path,
    *,
    base_edit_path: Path | None = None,
) -> Path:
    """Fetch one profile and atomically write one reviewable proposal."""

    event_path = Path(event_path)
    output_dir = Path(output_dir)
    request = parse_player_issue_event(_load_event(event_path))
    try:
        source = asyncio.run(fetch_pes_retro_stats_profile(request.profile_url))
        proposal = map_pes21_proposal(
            source, effective_date=date.fromisoformat(request.effective_date)
        )
    except (PesRetroStatsError, PlayerDraftError) as exc:
        raise PlayerDraftError(str(exc)) from None
    except Exception as exc:
        raise PlayerDraftError(f"cannot fetch player profile: {exc}") from exc

    base_path = Path(base_edit_path or config.EDIT_FILE_PATH)
    try:
        verify_base_file(base_path)
    except PlayerSpecError as exc:
        raise PlayerDraftError(str(exc)) from None
    try:
        decrypted = crypto.decrypt(base_path)
    except Exception as exc:
        raise PlayerDraftError(f"cannot decrypt verified base: {exc}") from exc
    try:
        edit_file = EditFile()
        data_path = decrypted / "data.dat"
        if not data_path.exists():
            dat_files = list(decrypted.glob("*.dat"))
            if not dat_files:
                raise PlayerDraftError(
                    f"decryption produced no .dat files in {decrypted}"
                )
            data_path = max(dat_files, key=lambda path: path.stat().st_size)
        edit_file.load(data_path)
        completed_player_ids = _base_and_completed_player_ids(edit_file)
        if request.operation == "create":
            payload = build_player_draft(
                request,
                source,
                proposal,
                edit_file=edit_file,
                nationality_catalog=load_nationality_catalog(),
                completed_player_ids=completed_player_ids,
                team_aliases=_load_team_aliases(),
            )
        else:
            payload = build_player_draft(
                request,
                source,
                proposal,
                edit_file=edit_file,
            )
    except PlayerDraftError:
        raise
    except Exception as exc:
        raise PlayerDraftError(f"cannot load verified base: {exc}") from exc
    finally:
        crypto.cleanup_temp(decrypted)

    filename = _draft_filename(request.player_name)
    output_dir.mkdir(parents=True, exist_ok=True)
    if not output_dir.is_dir():
        raise PlayerDraftError(f"output directory is not a directory: {output_dir}")
    destination = output_dir / filename
    content = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    _write_exclusive_atomic(destination, content)
    return destination
