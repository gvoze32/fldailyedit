import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields, replace
from pathlib import Path

import pytest

from scraper.player_draft import PlayerDraftSource
from tools.generate_player_draft import (
    PlayerDraftError,
    PlayerDraftRequest,
    build_player_draft,
    parse_player_issue_event,
    write_player_draft,
)


PROFILE_URL = (
    "https://sortitoutsi.net/football-manager-data-update/person/2000370206"
)
CONFIRMATIONS = """- [X] I supplied source evidence.
- [X] I did not derive PES ratings from Football Manager values.
- [X] I understand a maintainer must review the draft PR."""
HEADINGS = (
    "Operation",
    "SortitoutSI profile",
    "Current team",
    "Effective date",
    "Proof URLs",
    "Contributor notes",
    "Confirmations",
)


def issue_body(**overrides: str) -> str:
    values = {
        "Operation": "create",
        "SortitoutSI profile": PROFILE_URL,
        "Current team": "Chelsea FC",
        "Effective date": "2026-08-04",
        "Proof URLs": (
            "https://sortitoutsi.net/football-manager-data-update/"
            "submission/fixture-proof"
        ),
        "Contributor notes": "Missing from the reviewed FL26 base.",
        "Confirmations": CONFIRMATIONS,
    }
    values.update(overrides)
    return "\n\n".join(f"### {heading}\n\n{values[heading]}" for heading in HEADINGS) + "\n"


RENDERED_TASK3_FORM_BODY = """### Operation

create

### SortitoutSI profile

https://sortitoutsi.net/football-manager-data-update/person/2000370206

### Current team

Chelsea FC

### Effective date

2026-08-04

### Proof URLs

https://sortitoutsi.net/football-manager-data-update/submission/fixture-proof

### Contributor notes

Missing from the reviewed FL26 base.

### Confirmations

- [X] I supplied source evidence.
- [X] I did not derive PES ratings from Football Manager values.
- [X] I understand a maintainer must review the draft PR.
"""


def dastan_issue_event() -> dict[str, object]:
    return {
        "action": "labeled",
        "label": {"name": "generate-player-draft"},
        "issue": {
            "number": 42,
            "state": "open",
            "html_url": "https://github.com/gvoze32/fldailyedit/issues/42",
            "user": {"type": "User"},
            "body": issue_body(),
        },
    }


def dastan_source(**overrides: object) -> PlayerDraftSource:
    values: dict[str, object] = {
        "sortitoutsi_id": 2000370206,
        "name": "Dastan Satpayev",
        "profile_url": PROFILE_URL,
        "date_of_birth": "2008-08-12",
        "nationality": "Kazakhstan",
        "positions": ("AM RL", "ST"),
        "current_club": "Chelsea",
    }
    values.update(overrides)
    return PlayerDraftSource(**values)


def mutate_issue(event: dict[str, object], field: str, value: object) -> None:
    issue = event["issue"]
    assert isinstance(issue, dict)
    issue[field] = value


def test_player_draft_request_has_the_published_interface():
    assert tuple(field.name for field in fields(PlayerDraftRequest)) == (
        "operation",
        "profile_url",
        "current_team",
        "effective_date",
        "proof_urls",
        "issue_number",
        "issue_url",
    )


def test_exact_issue_event_parses_to_request():
    request = parse_player_issue_event(dastan_issue_event())

    assert request == PlayerDraftRequest(
        operation="create",
        profile_url=PROFILE_URL,
        current_team="Chelsea FC",
        effective_date="2026-08-04",
        proof_urls=(
            "https://sortitoutsi.net/football-manager-data-update/submission/fixture-proof",
        ),
        issue_number=42,
        issue_url="https://github.com/gvoze32/fldailyedit/issues/42",
    )


def test_real_task3_rendered_form_fixture_matches_parser_contract():
    event = dastan_issue_event()
    mutate_issue(event, "body", RENDERED_TASK3_FORM_BODY)

    request = parse_player_issue_event(event)

    assert request.issue_number == 42
    assert request.operation == "create"


@pytest.mark.parametrize(
    ("mutation", "value"),
    [
        ("action", "opened"),
        ("label", {"name": "other-label"}),
        ("label", None),
        ("issue.state", "closed"),
        ("issue.user", {"type": "Bot"}),
        ("issue.user", {"type": "Organization"}),
        ("issue.number", True),
        ("issue.number", 0),
    ],
)
def test_untrusted_event_metadata_is_rejected(mutation: str, value: object):
    event = dastan_issue_event()
    if mutation.startswith("issue."):
        mutate_issue(event, mutation.removeprefix("issue."), value)
    else:
        event[mutation] = value

    with pytest.raises(PlayerDraftError):
        parse_player_issue_event(event)


@pytest.mark.parametrize(
    "body",
    [
        issue_body().replace("### Operation", "## Operation", 1),
        issue_body().replace("### Operation\n\ncreate\n\n", "", 1),
        issue_body() + "\n### Operation\n\ncreate\n",
        issue_body().replace("### Current team", "### Team", 1),
        "untrusted preamble\n\n" + issue_body(),
        issue_body().replace(
            "### Operation\n\ncreate\n\n### SortitoutSI profile",
            "### SortitoutSI profile\n\n" + PROFILE_URL + "\n\n### Operation",
            1,
        ),
    ],
)
def test_malformed_or_nonexact_headings_are_rejected(body: str):
    event = dastan_issue_event()
    mutate_issue(event, "body", body)

    with pytest.raises(PlayerDraftError):
        parse_player_issue_event(event)


@pytest.mark.parametrize(
    "confirmations",
    [
        CONFIRMATIONS.replace("- [X]", "- [ ]", 1),
        CONFIRMATIONS.replace(
            "- [X] I supplied source evidence.\n", "", 1
        ),
        CONFIRMATIONS + "\n- [X] I also request automatic approval.",
        CONFIRMATIONS + "\n- [X] I supplied source evidence.",
        "\n".join(reversed(CONFIRMATIONS.splitlines())),
        CONFIRMATIONS.replace("[X]", "[x]"),
        CONFIRMATIONS.replace("[X]", "[x]", 1),
    ],
)
def test_confirmations_require_three_exact_checked_lines(confirmations: str):
    event = dastan_issue_event()
    mutate_issue(event, "body", issue_body(Confirmations=confirmations))

    with pytest.raises(PlayerDraftError):
        parse_player_issue_event(event)


def test_multiple_profile_urls_are_rejected():
    event = dastan_issue_event()
    mutate_issue(
        event,
        "body",
        issue_body(
            **{
                "SortitoutSI profile": PROFILE_URL
                + "\nhttps://sortitoutsi.net/football-manager-data-update/person/2"
            }
        ),
    )

    with pytest.raises(PlayerDraftError):
        parse_player_issue_event(event)


def test_more_than_ten_proof_urls_are_rejected():
    event = dastan_issue_event()
    proofs = "\n".join(f"https://example.com/proof/{index}" for index in range(11))
    mutate_issue(event, "body", issue_body(**{"Proof URLs": proofs}))

    with pytest.raises(PlayerDraftError):
        parse_player_issue_event(event)


@pytest.mark.parametrize(
    "proof_url",
    [
        "http://example.com/proof",
        "https:///missing-host",
        "https://example.com/proof\tvalue",
        "https://user:secret@example.com/proof",
    ],
)
def test_invalid_or_non_https_proof_urls_are_rejected(proof_url: str):
    event = dastan_issue_event()
    mutate_issue(event, "body", issue_body(**{"Proof URLs": proof_url}))

    with pytest.raises(PlayerDraftError):
        parse_player_issue_event(event)


@pytest.mark.parametrize(
    "effective_date",
    ["", "2026-02-30", "2026-8-4", "20260804", "04-08-2026", "not-a-date"],
)
def test_noncanonical_or_invalid_dates_are_rejected(effective_date: str):
    event = dastan_issue_event()
    mutate_issue(event, "body", issue_body(**{"Effective date": effective_date}))

    with pytest.raises(PlayerDraftError):
        parse_player_issue_event(event)


@pytest.mark.parametrize(
    "profile_url",
    [
        "http://sortitoutsi.net/football-manager-data-update/person/2000370206",
        "https://evil.example/football-manager-data-update/person/2000370206",
        PROFILE_URL + "?redirect=https://evil.example",
        PROFILE_URL + "/extra/path",
    ],
)
def test_untrusted_profile_urls_are_rejected(profile_url: str):
    event = dastan_issue_event()
    mutate_issue(event, "body", issue_body(**{"SortitoutSI profile": profile_url}))

    with pytest.raises(PlayerDraftError):
        parse_player_issue_event(event)


@pytest.mark.parametrize(
    "issue_url",
    [
        "http://github.com/gvoze32/fldailyedit/issues/42",
        "https://evil.example/gvoze32/fldailyedit/issues/42",
        "https://github.com/gvoze32/fldailyedit/issues/41",
        "https://github.com/gvoze32/fldailyedit/issues/42?x=1",
    ],
)
def test_untrusted_issue_urls_are_rejected(issue_url: str):
    event = dastan_issue_event()
    mutate_issue(event, "html_url", issue_url)

    with pytest.raises(PlayerDraftError):
        parse_player_issue_event(event)


@pytest.mark.parametrize(
    ("heading", "value"),
    [
        ("Operation", "x" * 11),
        ("SortitoutSI profile", PROFILE_URL + "x" * 301),
        ("Current team", "x" * 101),
        ("Effective date", "2026-08-040"),
        ("Proof URLs", "https://example.com/" + "x" * 301),
        ("Contributor notes", "x" * 2001),
    ],
)
def test_issue_form_field_length_limits_are_enforced(heading: str, value: str):
    event = dastan_issue_event()
    mutate_issue(event, "body", issue_body(**{heading: value}))

    with pytest.raises(PlayerDraftError):
        parse_player_issue_event(event)


@pytest.mark.parametrize("operation", ["create", "update"])
def test_supported_operations_are_accepted(operation: str):
    event = dastan_issue_event()
    mutate_issue(event, "body", issue_body(Operation=operation))

    assert parse_player_issue_event(event).operation == operation


def test_create_draft_is_incomplete_and_preserves_source_provenance():
    request = parse_player_issue_event(dastan_issue_event())

    payload = build_player_draft(request, dastan_source())

    assert payload["schema_version"] == 1
    assert payload["operation"] == "create"
    assert payload["lifecycle"] == {"status": "active"}
    assert payload["applies_to"] == ["fl26-u2.2-national-squads"]
    assert payload["identity"] == {
        "name": "Dastan Satpayev",
        "print_name": None,
        "aliases": ["Dastan Satpayev"],
        "pes_id": None,
        "sortitoutsi_id": 2000370206,
    }
    assert payload["source"] == {
        "profile_url": PROFILE_URL,
        "date_of_birth": "2008-08-12",
        "nationality": "Kazakhstan",
        "positions": ["AM RL", "ST"],
        "current_club": "Chelsea",
    }
    assert payload["evidence"] == {
        "profile_url": PROFILE_URL,
        "proof_urls": [
            "https://sortitoutsi.net/football-manager-data-update/submission/fixture-proof"
        ],
        "effective_date": "2026-08-04",
        "current_team": "Chelsea FC",
        "issue_number": 42,
        "issue_url": "https://github.com/gvoze32/fldailyedit/issues/42",
    }
    assert payload["pes"] is None
    assert payload["draft"] == {
        "needs_human_review": True,
        "missing": ["identity.pes_id", "identity.print_name", "pes"],
    }


def test_update_draft_names_only_the_unresolved_patch_contract():
    request = replace(
        parse_player_issue_event(dastan_issue_event()), operation="update"
    )

    payload = build_player_draft(request, dastan_source())

    assert payload["operation"] == "update"
    assert payload["identity"]["pes_id"] is None
    assert payload["pes"] is None
    assert payload["draft"] == {
        "needs_human_review": True,
        "missing": ["identity.pes_id", "pes.abilities.<field>.from/to"],
    }


def test_mismatched_fetched_profile_is_rejected():
    request = parse_player_issue_event(dastan_issue_event())

    with pytest.raises(PlayerDraftError):
        build_player_draft(
            request,
            dastan_source(
                profile_url="https://sortitoutsi.net/football-manager-data-update/person/2",
                sortitoutsi_id=2,
            ),
        )


def test_issue_event_builds_one_atomic_deterministic_draft(monkeypatch, tmp_path):
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(dastan_issue_event()), encoding="utf-8")

    async def fake_fetch(url: str) -> PlayerDraftSource:
        assert url == PROFILE_URL
        return dastan_source()

    monkeypatch.setattr(
        "tools.generate_player_draft.fetch_sortitoutsi_player_profile", fake_fetch
    )

    path = write_player_draft(event_path, tmp_path / "players")
    text = path.read_text(encoding="utf-8")
    payload = json.loads(text)

    assert path.name == "dastan-satpayev.json"
    assert text.endswith("\n")
    assert text == json.dumps(payload, indent=2, sort_keys=True) + "\n"
    assert tuple(path.parent.iterdir()) == (path,)
    assert payload["identity"]["pes_id"] is None
    assert payload["pes"] is None


def test_generated_draft_validation_reports_exact_missing_human_fields(
    monkeypatch, tmp_path, capsys
):
    import config
    import run

    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(dastan_issue_event()), encoding="utf-8")
    output_dir = tmp_path / "players"

    async def fake_fetch(url: str) -> PlayerDraftSource:
        assert url == PROFILE_URL
        return dastan_source()

    monkeypatch.setattr(
        "tools.generate_player_draft.fetch_sortitoutsi_player_profile", fake_fetch
    )
    write_player_draft(event_path, output_dir)
    monkeypatch.setattr(config, "PLAYER_SPECS_DIR", output_dir)

    with pytest.raises(SystemExit) as exc_info:
        run.cmd_players_validate(None)

    assert exc_info.value.code == 2
    assert capsys.readouterr().out.splitlines() == [
        "Player-spec semantic validation failed: incomplete draft dastan-satpayev.json",
        "Missing human fields: identity.pes_id, identity.print_name, pes",
    ]


@pytest.mark.parametrize(
    "source_overrides",
    (
        {"date_of_birth": "12 August 2008"},
        {"positions": ()},
    ),
)
def test_generated_draft_preserves_optional_source_metadata(
    tmp_path, source_overrides
):
    from editor.player_spec import IncompletePlayerSpecError, load_player_specs

    payload = build_player_draft(
        parse_player_issue_event(dastan_issue_event()),
        dastan_source(**source_overrides),
    )
    (tmp_path / "dastan-satpayev.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )

    with pytest.raises(IncompletePlayerSpecError):
        load_player_specs(tmp_path)


@pytest.mark.parametrize("person_id", (0, int("9" * 20)))
def test_generated_draft_accepts_source_id_bounds(tmp_path, person_id):
    from editor.player_spec import IncompletePlayerSpecError, load_player_specs

    payload = build_player_draft(
        parse_player_issue_event(dastan_issue_event()), dastan_source()
    )
    profile_url = (
        "https://sortitoutsi.net/football-manager-data-update/person/"
        f"{person_id}"
    )
    payload["identity"]["sortitoutsi_id"] = person_id
    payload["source"]["profile_url"] = profile_url
    payload["evidence"]["profile_url"] = profile_url
    (tmp_path / "dastan-satpayev.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )

    with pytest.raises(IncompletePlayerSpecError):
        load_player_specs(tmp_path)



def test_generated_draft_accepts_unbounded_positive_issue_number(tmp_path):
    from editor.player_spec import IncompletePlayerSpecError, load_player_specs

    payload = build_player_draft(
        parse_player_issue_event(dastan_issue_event()), dastan_source()
    )
    issue_number = int("9" * 20)
    payload["evidence"]["issue_number"] = issue_number
    payload["evidence"]["issue_url"] = (
        f"https://github.com/gvoze32/fldailyedit/issues/{issue_number}"
    )
    (tmp_path / "dastan-satpayev.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )

    with pytest.raises(IncompletePlayerSpecError):
        load_player_specs(tmp_path)

@pytest.mark.parametrize(
    "mutation",
    (
        "schema-version",
        "extra-field",
        "identity-name",
        "evidence-issue-number",
        "profile-host",
        "issue-url-number",
        "proof-credentials",
    ),
)
def test_malformed_draft_shape_uses_strict_schema_errors(tmp_path, mutation):
    from editor.player_spec import (
        IncompletePlayerSpecError,
        PlayerSpecError,
        load_player_specs,
    )

    payload = build_player_draft(
        parse_player_issue_event(dastan_issue_event()), dastan_source()
    )
    if mutation == "schema-version":
        payload["schema_version"] = 2
    elif mutation == "extra-field":
        payload["unexpected"] = "value"
    elif mutation == "identity-name":
        payload["identity"]["name"] = None
    elif mutation == "evidence-issue-number":
        payload["evidence"]["issue_number"] = "42"
    elif mutation == "profile-host":
        payload["source"]["profile_url"] = "https://example.com/person/2000370206"
        payload["evidence"]["profile_url"] = payload["source"]["profile_url"]
    elif mutation == "issue-url-number":
        payload["evidence"]["issue_url"] = (
            "https://github.com/gvoze32/fldailyedit/issues/99"
        )
    else:
        payload["evidence"]["proof_urls"] = [
            "https://user:secret@example.com/proof"
        ]
    (tmp_path / "dastan-satpayev.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )

    with pytest.raises(PlayerSpecError) as exc_info:
        load_player_specs(tmp_path)

    assert not isinstance(exc_info.value, IncompletePlayerSpecError)


def test_draft_missing_fields_require_exact_untrimmed_values(tmp_path):
    from editor.player_spec import (
        IncompletePlayerSpecError,
        PlayerSpecError,
        load_player_specs,
    )

    payload = build_player_draft(
        parse_player_issue_event(dastan_issue_event()), dastan_source()
    )
    payload["draft"]["missing"][0] = " identity.pes_id "
    (tmp_path / "dastan-satpayev.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )

    with pytest.raises(PlayerSpecError) as exc_info:
        load_player_specs(tmp_path)

    assert not isinstance(exc_info.value, IncompletePlayerSpecError)


def test_atomic_publication_never_exposes_an_empty_destination(
    monkeypatch, tmp_path
):
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(dastan_issue_event()), encoding="utf-8")
    output_dir = tmp_path / "players"

    async def fake_fetch(_url: str) -> PlayerDraftSource:
        return dastan_source()

    real_link = os.link
    publications: list[bytes] = []

    def inspecting_link(source, destination) -> None:
        source_path = Path(source)
        destination_path = Path(destination)
        assert not destination_path.exists()
        complete = source_path.read_bytes()
        assert complete
        assert json.loads(complete)["identity"]["name"] == "Dastan Satpayev"
        real_link(source_path, destination_path)
        assert destination_path.read_bytes() == complete
        publications.append(complete)

    monkeypatch.setattr(
        "tools.generate_player_draft.fetch_sortitoutsi_player_profile", fake_fetch
    )
    monkeypatch.setattr("tools.generate_player_draft.os.link", inspecting_link)

    path = write_player_draft(event_path, output_dir)

    assert path.read_bytes() == publications[0]
    assert tuple(output_dir.iterdir()) == (path,)


def test_existing_slug_collision_is_rejected_without_modification(monkeypatch, tmp_path):
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(dastan_issue_event()), encoding="utf-8")
    output_dir = tmp_path / "players"
    output_dir.mkdir()
    destination = output_dir / "dastan-satpayev.json"
    destination.write_text("reviewed-content\n", encoding="utf-8")

    async def fake_fetch(_url: str) -> PlayerDraftSource:
        return dastan_source()

    monkeypatch.setattr(
        "tools.generate_player_draft.fetch_sortitoutsi_player_profile", fake_fetch
    )

    with pytest.raises(PlayerDraftError):
        write_player_draft(event_path, output_dir)

    assert destination.read_text(encoding="utf-8") == "reviewed-content\n"
    assert tuple(output_dir.iterdir()) == (destination,)


def test_repeated_event_is_idempotently_rejected(monkeypatch, tmp_path):
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(dastan_issue_event()), encoding="utf-8")
    output_dir = tmp_path / "players"

    async def fake_fetch(_url: str) -> PlayerDraftSource:
        return dastan_source()

    monkeypatch.setattr(
        "tools.generate_player_draft.fetch_sortitoutsi_player_profile", fake_fetch
    )

    path = write_player_draft(event_path, output_dir)
    original = path.read_bytes()
    with pytest.raises(PlayerDraftError):
        write_player_draft(event_path, output_dir)

    assert path.read_bytes() == original


def test_concurrent_publication_has_one_winner_and_no_partial_file(
    monkeypatch, tmp_path
):
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(dastan_issue_event()), encoding="utf-8")
    output_dir = tmp_path / "players"

    async def fake_fetch(_url: str) -> PlayerDraftSource:
        return dastan_source()

    monkeypatch.setattr(
        "tools.generate_player_draft.fetch_sortitoutsi_player_profile", fake_fetch
    )

    def attempt() -> Path | PlayerDraftError:
        try:
            return write_player_draft(event_path, output_dir)
        except PlayerDraftError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(lambda _index: attempt(), range(2)))

    assert sum(isinstance(result, Path) for result in results) == 1
    assert sum(isinstance(result, PlayerDraftError) for result in results) == 1
    destination = output_dir / "dastan-satpayev.json"
    assert json.loads(destination.read_bytes())["identity"]["name"] == "Dastan Satpayev"
    assert tuple(output_dir.iterdir()) == (destination,)


@pytest.mark.parametrize(
    ("name", "filename"),
    [
        ("A" * 235, "a" * 235 + ".json"),
        ("Álvaro Núñez ⚽", "alvaro-nunez.json"),
    ],
)
def test_filename_byte_limit_accepts_safe_normalized_boundaries(
    monkeypatch, tmp_path, name: str, filename: str
):
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(dastan_issue_event()), encoding="utf-8")

    async def fake_fetch(_url: str) -> PlayerDraftSource:
        return dastan_source(name=name)

    monkeypatch.setattr(
        "tools.generate_player_draft.fetch_sortitoutsi_player_profile", fake_fetch
    )

    path = write_player_draft(event_path, tmp_path / "players")

    assert path.name == filename
    assert len(path.name.encode("utf-8")) <= 240


def test_overlong_filename_is_rejected_before_output_filesystem_calls(
    monkeypatch, tmp_path
):
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(dastan_issue_event()), encoding="utf-8")
    output_dir = tmp_path / "players"

    async def fake_fetch(_url: str) -> PlayerDraftSource:
        return dastan_source(name="A" * 236)

    monkeypatch.setattr(
        "tools.generate_player_draft.fetch_sortitoutsi_player_profile", fake_fetch
    )

    with pytest.raises(PlayerDraftError, match="filename"):
        write_player_draft(event_path, output_dir)

    assert not output_dir.exists()


def test_untrusted_event_is_rejected_before_profile_fetch(monkeypatch, tmp_path):
    event = dastan_issue_event()
    event["action"] = "opened"
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(event), encoding="utf-8")
    fetched = False

    async def fake_fetch(_url: str) -> PlayerDraftSource:
        nonlocal fetched
        fetched = True
        return dastan_source()

    monkeypatch.setattr(
        "tools.generate_player_draft.fetch_sortitoutsi_player_profile", fake_fetch
    )

    with pytest.raises(PlayerDraftError):
        write_player_draft(event_path, tmp_path / "players")

    assert fetched is False
    assert not (tmp_path / "players").exists()


def test_cli_prints_exact_machine_output(monkeypatch, tmp_path, capsys):
    import run

    draft = tmp_path / "players" / "dastan-satpayev.json"
    draft.parent.mkdir()
    draft.write_text(
        json.dumps({"identity": {"name": "Dastan Satpayev"}}), encoding="utf-8"
    )
    event = tmp_path / "event.json"
    event.write_text("{}", encoding="utf-8")

    def fake_write(event_path: Path, output_dir: Path) -> Path:
        assert event_path == event
        assert output_dir == Path("players")
        return draft

    monkeypatch.setattr(run, "write_player_draft", fake_write)
    monkeypatch.setattr(
        run.sys,
        "argv",
        ["run.py", "players", "generate-draft", "--event", str(event), "--output-dir", "players"],
    )

    run.main()

    assert capsys.readouterr().out.splitlines() == [
        "SPEC_PATH=players/dastan-satpayev.json",
        'PLAYER_NAME="Dastan Satpayev"',
    ]


def test_cli_escapes_shell_metacharacters_in_player_name(monkeypatch, tmp_path, capsys):
    import run

    name = 'Name "quoted"; $(touch PWNED) `touch ALSO_PWNED`\nnext'
    draft = tmp_path / "players" / "safe-name.json"
    draft.parent.mkdir()
    draft.write_text(json.dumps({"identity": {"name": name}}), encoding="utf-8")
    event = tmp_path / "event.json"
    event.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(run, "write_player_draft", lambda *_args: draft)
    monkeypatch.setattr(
        run.sys,
        "argv",
        ["run.py", "players", "generate-draft", "--event", str(event), "--output-dir", "players"],
    )

    run.main()

    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "SPEC_PATH=players/safe-name.json"
    assert len(lines) == 2
    assert lines[1].startswith("PLAYER_NAME=")
    assert json.loads(lines[1].removeprefix("PLAYER_NAME=")) == name
    assert "$(" not in lines[1]
    assert "`" not in lines[1]
    assert not (tmp_path / "PWNED").exists()
    assert not (tmp_path / "ALSO_PWNED").exists()


def test_cli_accepts_no_untrusted_network_url_argument(monkeypatch):
    import run

    monkeypatch.setattr(
        run.sys,
        "argv",
        [
            "run.py",
            "players",
            "generate-draft",
            "--event",
            "event.json",
            "--output-dir",
            "players",
            "--profile-url",
            "https://evil.example/profile",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        run.main()

    assert exc_info.value.code == 2
