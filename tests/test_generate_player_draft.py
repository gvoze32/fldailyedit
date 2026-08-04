import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields
from datetime import date
from pathlib import Path
from types import MappingProxyType

import pytest

import tools.generate_player_draft as generator

from editor.models import PlayerInfo, TeamData, TeamInfo
from editor.player_codec import PlayerAbilityProfile
from scraper.pes21_proposal import Pes21Proposal, map_pes21_proposal
from scraper.pes_retro_stats import PesRetroStatsProfile
from tools.generate_player_draft import (
    PlayerDraftError,
    PlayerDraftRequest,
    build_player_draft,
    parse_player_issue_event,
    write_player_draft,
)


PROFILE_URL = "https://pesretrostats.com/player/f77d9c27-dastan-satpaev"
PROFILE_UUID = "f77d9c27-8f02-4dbe-b877-4c13724a4886"
MARCO_URL = "https://pesretrostats.com/player/0ce2dbde-marco-palestra"
MARCO_UUID = "0ce2dbde-9cd9-423c-a90a-35b07df6a967"
CONFIRMATIONS = """- [X] I supplied one canonical Pes Retro Stats player profile.
- [X] I understand autofilled PES values are unapproved proposals.
- [X] I understand a maintainer must review the draft PR."""
HEADINGS = (
    "Operation",
    "Player name",
    "Pes Retro Stats profile",
    "Current team",
    "Effective date",
    "Proof URLs",
    "Contributor notes",
    "Confirmations",
)
SOURCE_POSITIONS = (
    "GK",
    "CB",
    "LB",
    "RB",
    "CWP",
    "DMF",
    "LWB",
    "RWB",
    "CMF",
    "LMF",
    "RMF",
    "AMF",
    "LWF",
    "RWF",
    "SS",
    "CF",
)
SOURCE_STATS = MappingProxyType(
    {
        "attacking_prowess": 76,
        "technique": 74,
        "dribbling": 75,
        "dribble_accuracy": 72,
        "short_pass_accuracy": 68,
        "long_pass_accuracy": 65,
        "shot_accuracy": 79,
        "heading": 68,
        "free_kick_accuracy": 65,
        "swerve": 70,
        "top_speed": 80,
        "acceleration": 82,
        "shot_power": 77,
        "jump": 70,
        "physical_contact": 68,
        "body_control": 78,
        "stamina": 72,
        "defensive_awareness": 42,
        "ball_winning": 43,
        "new_aggression": 70,
        "gk_awareness": 40,
        "gk_catching": 40,
        "gk_clearing": 40,
        "gk_reflexes": 40,
        "gk_reach": 40,
    }
)
EXPECTED_ABILITIES = {
    "attacking_awareness": 76,
    "ball_control": 74,
    "dribbling": 75,
    "tight_possession": 72,
    "low_pass": 68,
    "lofted_pass": 65,
    "finishing": 79,
    "heading": 68,
    "place_kicking": 65,
    "curl": 70,
    "speed": 80,
    "acceleration": 82,
    "kicking_power": 77,
    "jump": 70,
    "physical_contact": 68,
    "balance": 78,
    "stamina": 72,
    "defensive_awareness": 42,
    "ball_winning": 43,
    "aggression": 70,
    "gk_awareness": 40,
    "catching": 40,
    "clearing": 40,
    "reflexes": 40,
    "gk_reach": 40,
}
CREATE_MISSING_EXPECTED = (
    "identity.pes_id",
    "identity.print_name",
    "pes.player_id",
    "pes.print_name",
    "pes.team_id",
    "pes.team_name",
    "pes.nationality_id",
    "pes.skin_color",
    "pes.iris_color",
)


def issue_body(**overrides: str) -> str:
    values = {
        "Operation": "create",
        "Player name": "Dastan Satpaev",
        "Pes Retro Stats profile": PROFILE_URL,
        "Current team": "Chelsea FC",
        "Effective date": "2026-08-04",
        "Proof URLs": "https://example.com/official-proof",
        "Contributor notes": "Missing from the reviewed FL26 base.",
        "Confirmations": CONFIRMATIONS,
    }
    values.update(overrides)
    return "\n\n".join(f"### {heading}\n\n{values[heading]}" for heading in HEADINGS) + "\n"


RENDERED_FORM_BODY = """### Operation

create

### Player name

Dastan Satpaev

### Pes Retro Stats profile

https://pesretrostats.com/player/f77d9c27-dastan-satpaev

### Current team

Chelsea FC

### Effective date

2026-08-04

### Proof URLs

https://example.com/official-proof

### Contributor notes

Missing from the reviewed FL26 base.

### Confirmations

- [X] I supplied one canonical Pes Retro Stats player profile.
- [X] I understand autofilled PES values are unapproved proposals.
- [X] I understand a maintainer must review the draft PR.
"""


def issue_event(**body_overrides: str) -> dict[str, object]:
    return {
        "action": "labeled",
        "label": {"name": "generate-player-draft"},
        "issue": {
            "number": 42,
            "state": "open",
            "html_url": "https://github.com/gvoze32/fldailyedit/issues/42",
            "user": {"type": "User"},
            "body": issue_body(**body_overrides),
        },
    }


def mutate_issue(event: dict[str, object], field: str, value: object) -> None:
    issue = event["issue"]
    assert isinstance(issue, dict)
    issue[field] = value


def make_source(**overrides: object) -> PesRetroStatsProfile:
    positions = {position: None for position in SOURCE_POSITIONS}
    positions.update({"LWF": "A", "RWF": "B", "SS": "B", "CF": "★"})
    values: dict[str, object] = {
        "player_id": PROFILE_UUID,
        "short_id": "f77d9c27",
        "name": "Dastan Satpaev",
        "full_name": "Dastan Satpaev",
        "profile_url": PROFILE_URL,
        "birth_date": date(2008, 8, 12),
        "nationality": "Kazakhstan",
        "current_club": "Chelsea FC",
        "shirt_number": 36,
        "height": 176,
        "weight": 73,
        "strong_foot": "R",
        "weak_foot_accuracy": 5,
        "weak_foot_frequency": 5,
        "form": 6,
        "injury_tolerance": "A",
        "playing_style": "Goal Poacher",
        "positions": MappingProxyType(positions),
        "stats": SOURCE_STATS,
        "player_skill_codes": ("S02", "S13"),
        "com_playing_styles": ("Incisive Run",),
    }
    values.update(overrides)
    return PesRetroStatsProfile(**values)


def proposal_for(source: PesRetroStatsProfile) -> Pes21Proposal:
    return map_pes21_proposal(source, effective_date=date(2026, 8, 4))


def marco_source(**overrides: object) -> PesRetroStatsProfile:
    positions = {position: None for position in SOURCE_POSITIONS}
    positions.update({"RB": "A", "RWB": "★"})
    values: dict[str, object] = {
        "player_id": MARCO_UUID,
        "short_id": "0ce2dbde",
        "name": "Marco Palestra",
        "full_name": "Marco Palestra",
        "profile_url": MARCO_URL,
        "birth_date": date(2005, 3, 3),
        "nationality": "Italy",
        "current_club": "Chelsea FC",
        "shirt_number": 27,
        "height": 180,
        "weight": 68,
        "strong_foot": "R",
        "weak_foot_accuracy": 4,
        "weak_foot_frequency": 4,
        "form": 6,
        "injury_tolerance": "B",
        "playing_style": "Offensive Full-back",
        "positions": MappingProxyType(positions),
        "stats": SOURCE_STATS,
        "player_skill_codes": (),
        "com_playing_styles": (),
    }
    values.update(overrides)
    return PesRetroStatsProfile(**values)


def current_profile(proposal: Pes21Proposal, *, speed: int | None = None) -> PlayerAbilityProfile:
    abilities = dict(proposal.abilities)
    if speed is not None:
        abilities["speed"] = speed
    return PlayerAbilityProfile(
        player_id=162196,
        nationality_id=215,
        height=proposal.height,
        weight=proposal.weight,
        age=proposal.age,
        registered_position="RB",
        registered_position_id=3,
        playing_style=proposal.playing_style,
        strong_foot=proposal.strong_foot,
        weak_foot_usage=proposal.weak_foot_usage,
        weak_foot_accuracy=proposal.weak_foot_accuracy,
        form=proposal.form,
        injury_resistance=proposal.injury_resistance,
        abilities=abilities,
        position_proficiency=dict(proposal.position_proficiency),
        player_skills=proposal.player_skills,
        com_styles=proposal.com_styles,
    )


class FakeEditFile:
    def __init__(self, proposal: Pes21Proposal, *, changed: bool = True) -> None:
        self.players = {162196: PlayerInfo(162196, "Marco Palestra", "M. Palestra")}
        self.teams = {101: TeamInfo(101, "Chelsea FC")}
        self.rosters = {101: TeamData(101, [162196] + [0] * 39)}
        target_speed = proposal.abilities["speed"]
        self.profiles = {
            162196: current_profile(
                proposal, speed=target_speed - 1 if changed else target_speed
            )
        }
        self.loaded_path: Path | None = None

    def load(self, path: Path) -> None:
        self.loaded_path = Path(path)

    def get_all_players(self):
        return self.players

    def get_all_team_info(self):
        return self.teams

    def get_team_roster(self, team_id):
        return self.rosters.get(team_id)

    def get_player_ability_profile(self, player_id):
        return self.profiles.get(player_id)


def update_request() -> PlayerDraftRequest:
    return parse_player_issue_event(
        issue_event(
            Operation="update",
            **{
                "Player name": "Marco Palestra",
                "Pes Retro Stats profile": MARCO_URL,
            },
        )
    )


def test_player_draft_request_has_the_published_interface():
    assert tuple(field.name for field in fields(PlayerDraftRequest)) == (
        "operation",
        "player_name",
        "profile_url",
        "current_team",
        "effective_date",
        "proof_urls",
        "issue_number",
        "issue_url",
    )


def test_exact_rendered_issue_event_parses_to_request():
    event = issue_event()
    mutate_issue(event, "body", RENDERED_FORM_BODY)

    assert parse_player_issue_event(event) == PlayerDraftRequest(
        operation="create",
        player_name="Dastan Satpaev",
        profile_url=PROFILE_URL,
        current_team="Chelsea FC",
        effective_date="2026-08-04",
        proof_urls=("https://example.com/official-proof",),
        issue_number=42,
        issue_url="https://github.com/gvoze32/fldailyedit/issues/42",
    )


@pytest.mark.parametrize(
    ("mutation", "value"),
    [
        ("action", "opened"),
        ("label", {"name": "other-label"}),
        ("label", None),
        ("issue.state", "closed"),
        ("issue.user", {"type": "Bot"}),
        ("issue.number", True),
        ("issue.number", 0),
    ],
)
def test_untrusted_event_metadata_is_rejected(mutation: str, value: object):
    event = issue_event()
    if mutation.startswith("issue."):
        mutate_issue(event, mutation.removeprefix("issue."), value)
    else:
        event[mutation] = value

    with pytest.raises(PlayerDraftError):
        parse_player_issue_event(event)


@pytest.mark.parametrize(
    "body",
    [
        issue_body().replace("### Player name\n\nDastan Satpaev\n\n", "", 1),
        issue_body() + "\n### Player name\n\nDastan Satpaev\n",
        issue_body().replace("### Current team", "### Team", 1),
        issue_body().replace(
            "### Player name\n\nDastan Satpaev\n\n### Pes Retro Stats profile",
            "### Pes Retro Stats profile\n\n" + PROFILE_URL + "\n\n### Player name",
            1,
        ),
        "untrusted preamble\n\n" + issue_body(),
    ],
)
def test_missing_multiple_or_nonexact_headings_are_rejected(body: str):
    event = issue_event()
    mutate_issue(event, "body", body)
    with pytest.raises(PlayerDraftError):
        parse_player_issue_event(event)


@pytest.mark.parametrize(
    "player_name",
    ["", "Dastan Satpaev\nOther Player", "é" * 31, "Dastan\x00Satpaev"],
)
def test_player_name_requires_one_canonical_identity_within_sixty_utf8_bytes(
    player_name: str,
):
    with pytest.raises(PlayerDraftError):
        parse_player_issue_event(issue_event(**{"Player name": player_name}))


@pytest.mark.parametrize(
    "profile_url",
    [
        "https://evil.example/player/f77d9c27-dastan-satpaev",
        "https://www.pesretrostats.com/player/f77d9c27-dastan-satpaev",
        PROFILE_URL + "?source=issue",
        PROFILE_URL + "#stats",
        PROFILE_URL + "/",
    ],
)
def test_wrong_host_and_noncanonical_profile_urls_are_rejected(profile_url: str):
    with pytest.raises(PlayerDraftError):
        parse_player_issue_event(
            issue_event(**{"Pes Retro Stats profile": profile_url})
        )


def test_multiple_profile_urls_are_rejected():
    with pytest.raises(PlayerDraftError):
        parse_player_issue_event(
            issue_event(
                **{
                    "Pes Retro Stats profile": PROFILE_URL
                    + "\nhttps://pesretrostats.com/player/0ce2dbde-marco-palestra"
                }
            )
        )


@pytest.mark.parametrize(
    "confirmations",
    [
        CONFIRMATIONS.replace("- [X]", "- [ ]", 1),
        CONFIRMATIONS.replace("\n- [X] I understand autofilled", "\n- [x] I understand autofilled"),
        "\n".join(reversed(CONFIRMATIONS.splitlines())),
        CONFIRMATIONS + "\n- [X] Extra confirmation.",
    ],
)
def test_confirmations_require_three_exact_checked_lines(confirmations: str):
    with pytest.raises(PlayerDraftError):
        parse_player_issue_event(issue_event(Confirmations=confirmations))


@pytest.mark.parametrize("operation", ["create", "update"])
def test_supported_operations_are_accepted(operation: str):
    assert parse_player_issue_event(issue_event(Operation=operation)).operation == operation


def test_create_draft_contains_the_complete_source_proposal_and_exact_missing_list():
    request = parse_player_issue_event(issue_event())
    source = make_source()

    payload = build_player_draft(request, source, proposal_for(source))

    assert generator.CREATE_MISSING == CREATE_MISSING_EXPECTED
    assert payload["schema_version"] == 2
    assert payload["operation"] == "create"
    assert payload["lifecycle"] == {"status": "active"}
    assert payload["identity"] == {
        "name": "Dastan Satpaev",
        "print_name": None,
        "aliases": ["Dastan Satpaev"],
        "pes_id": None,
        "pes_retro_stats_id": PROFILE_UUID,
    }
    assert payload["source"] == {
        "profile_url": PROFILE_URL,
        "date_of_birth": "2008-08-12",
        "nationality": "Kazakhstan",
        "positions": ["LWF", "RWF", "SS", "CF"],
        "current_club": "Chelsea FC",
    }
    assert payload["evidence"] == {
        "profile_url": PROFILE_URL,
        "proof_urls": ["https://example.com/official-proof"],
        "effective_date": "2026-08-04",
        "current_team": "Chelsea FC",
        "issue_number": 42,
        "issue_url": "https://github.com/gvoze32/fldailyedit/issues/42",
    }
    assert payload["pes"] == {
        "player_id": None,
        "name": "Dastan Satpaev",
        "print_name": None,
        "team_id": None,
        "team_name": None,
        "preferred_shirt_number": 36,
        "nationality_id": None,
        "age": 17,
        "height": 176,
        "weight": 73,
        "registered_position": "CF",
        "playing_style": 1,
        "strong_foot": 0,
        "weak_foot_usage": 2,
        "weak_foot_accuracy": 2,
        "form": 5,
        "injury_resistance": 2,
        "position_proficiency": {"LWF": 2, "RWF": 1, "SS": 1, "CF": 2},
        "abilities": EXPECTED_ABILITIES,
        "player_skills": ["double_touch", "long_range_shooting"],
        "com_styles": ["incisive_run"],
        "skin_color": None,
        "iris_color": None,
    }
    assert payload["draft"] == {
        "needs_human_review": True,
        "missing": list(CREATE_MISSING_EXPECTED),
    }


def test_create_draft_omits_an_out_of_range_source_shirt_number():
    source = make_source(shirt_number=100)
    payload = build_player_draft(
        parse_player_issue_event(issue_event()), source, proposal_for(source)
    )
    assert "preferred_shirt_number" not in payload["pes"]


def test_create_draft_rejects_an_unsupported_registered_position():
    positions = {position: None for position in SOURCE_POSITIONS}
    positions["RWB"] = "★"
    source = make_source(positions=MappingProxyType(positions))

    with pytest.raises(PlayerDraftError, match="unsupported registered position"):
        build_player_draft(
            parse_player_issue_event(issue_event()), source, proposal_for(source)
        )


def test_submitted_name_must_match_the_fetched_source_identity():
    source = make_source(name="Other Player")
    with pytest.raises(
        PlayerDraftError,
        match="^Pes Retro Stats profile name does not match Player name$",
    ):
        build_player_draft(
            parse_player_issue_event(issue_event()), source, proposal_for(source)
        )


def test_update_draft_resolves_base_identity_and_emits_only_exact_changes():
    source = marco_source()
    proposal = proposal_for(source)
    edit_file = FakeEditFile(proposal)

    payload = build_player_draft(
        update_request(), source, proposal, edit_file=edit_file
    )

    assert payload["schema_version"] == 2
    assert payload["identity"] == {
        "name": "Marco Palestra",
        "print_name": "M. Palestra",
        "aliases": ["Marco Palestra"],
        "pes_id": 162196,
        "pes_retro_stats_id": MARCO_UUID,
    }
    assert payload["pes"] == {
        "abilities": {"speed": {"from": 79, "to": 80}}
    }
    assert "registered_position" not in payload["pes"]
    assert "RWB" not in payload["pes"].get("position_proficiency", {})
    assert payload["draft"] == {"needs_human_review": True, "missing": []}


def test_update_draft_requires_a_loaded_base_edit_file():
    source = marco_source()
    with pytest.raises(PlayerDraftError, match="base EDIT file"):
        build_player_draft(update_request(), source, proposal_for(source))


@pytest.mark.parametrize("failure", ["absent", "ambiguous", "no-op"])
def test_update_resolution_and_no_op_failures_are_safe_player_draft_errors(failure: str):
    source = marco_source()
    proposal = proposal_for(source)
    edit_file = FakeEditFile(proposal, changed=failure != "no-op")
    if failure == "absent":
        edit_file.rosters[101] = TeamData(101)
    elif failure == "ambiguous":
        edit_file.players[262196] = PlayerInfo(262196, "Marco Palestra", "Marco 2")
        edit_file.rosters[101] = TeamData(101, [162196, 262196] + [0] * 38)
        edit_file.profiles[262196] = edit_file.profiles[162196]

    with pytest.raises(PlayerDraftError) as exc_info:
        build_player_draft(
            update_request(), source, proposal, edit_file=edit_file
        )

    assert type(exc_info.value) is PlayerDraftError
    assert str(exc_info.value)


def test_mapping_errors_are_normalized_and_never_create_output(monkeypatch, tmp_path):
    source = make_source(playing_style="Unknown")
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(issue_event()), encoding="utf-8")
    install_fetch(monkeypatch, source)
    output_dir = tmp_path / "players"

    with pytest.raises(PlayerDraftError, match="cannot be mapped"):
        write_player_draft(event_path, output_dir)
    assert not output_dir.exists()


def write_payload(tmp_path: Path, payload: dict[str, object], name: str) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.mark.parametrize("operation", ["create", "update"])
def test_schema_v2_generated_drafts_raise_exact_incomplete_error(tmp_path, operation):
    from editor.player_spec import IncompletePlayerSpecError, load_player_specs

    if operation == "create":
        source = make_source()
        payload = build_player_draft(
            parse_player_issue_event(issue_event()), source, proposal_for(source)
        )
        expected = CREATE_MISSING_EXPECTED
        filename = "dastan-satpaev.json"
    else:
        source = marco_source()
        proposal = proposal_for(source)
        payload = build_player_draft(
            update_request(), source, proposal, edit_file=FakeEditFile(proposal)
        )
        expected = ()
        filename = "marco-palestra.json"
    write_payload(tmp_path, payload, filename)

    with pytest.raises(IncompletePlayerSpecError) as exc_info:
        load_player_specs(tmp_path)

    assert exc_info.value.missing_fields == expected


@pytest.mark.parametrize(
    "mutation",
    ["schema", "uuid", "profile", "pes-shape", "missing"],
)
def test_any_file_with_a_draft_marker_is_never_loaded_as_a_completed_spec(
    tmp_path, mutation
):
    from editor.player_spec import IncompletePlayerSpecError, load_player_specs

    source = make_source()
    payload = build_player_draft(
        parse_player_issue_event(issue_event()), source, proposal_for(source)
    )
    if mutation == "schema":
        payload["schema_version"] = 1
    elif mutation == "uuid":
        payload["identity"]["pes_retro_stats_id"] = MARCO_UUID
    elif mutation == "profile":
        payload["source"]["profile_url"] = MARCO_URL
    elif mutation == "pes-shape":
        payload["pes"].pop("age")
    else:
        payload["draft"]["missing"] = list(reversed(CREATE_MISSING_EXPECTED))
    write_payload(tmp_path, payload, "dastan-satpaev.json")

    with pytest.raises(IncompletePlayerSpecError) as exc_info:
        load_player_specs(tmp_path)
    assert exc_info.value.missing_fields == ()


def install_fetch(monkeypatch, source: PesRetroStatsProfile) -> list[str]:
    fetched: list[str] = []

    async def fake_fetch(url: str) -> PesRetroStatsProfile:
        fetched.append(url)
        return source

    monkeypatch.setattr(
        "tools.generate_player_draft.fetch_pes_retro_stats_profile", fake_fetch
    )
    return fetched


def test_create_writer_fetches_once_without_verifying_or_decrypting_base(
    monkeypatch, tmp_path
):
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(issue_event()), encoding="utf-8")
    fetched = install_fetch(monkeypatch, make_source())
    monkeypatch.setattr(
        "tools.generate_player_draft.verify_base_file",
        lambda *_args: pytest.fail("create must not verify the base"),
    )
    monkeypatch.setattr(
        "tools.generate_player_draft.crypto.decrypt",
        lambda *_args: pytest.fail("create must not decrypt the base"),
    )

    path = write_player_draft(event_path, tmp_path / "players")
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert fetched == [PROFILE_URL]
    assert path.name == "dastan-satpaev.json"
    assert payload["schema_version"] == 2
    assert tuple(path.parent.iterdir()) == (path,)


def test_update_writer_verifies_decrypts_loads_and_always_cleans_up(
    monkeypatch, tmp_path
):
    source = marco_source()
    proposal = proposal_for(source)
    fake_edit = FakeEditFile(proposal)
    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps(
            issue_event(
                Operation="update",
                **{
                    "Player name": "Marco Palestra",
                    "Pes Retro Stats profile": MARCO_URL,
                },
            )
        ),
        encoding="utf-8",
    )
    base_path = tmp_path / "EDIT00000000"
    base_path.write_bytes(b"encrypted")
    decrypted = tmp_path / "decrypted"
    decrypted.mkdir()
    (decrypted / "data.dat").write_bytes(b"plain")
    calls: list[tuple[str, Path]] = []
    install_fetch(monkeypatch, source)
    monkeypatch.setattr(
        "tools.generate_player_draft.verify_base_file",
        lambda path: calls.append(("verify", Path(path))),
    )
    monkeypatch.setattr(
        "tools.generate_player_draft.crypto.decrypt",
        lambda path: calls.append(("decrypt", Path(path))) or decrypted,
    )
    monkeypatch.setattr(
        "tools.generate_player_draft.crypto.cleanup_temp",
        lambda path: calls.append(("cleanup", Path(path))),
    )
    monkeypatch.setattr("tools.generate_player_draft.EditFile", lambda: fake_edit)

    path = write_player_draft(
        event_path, tmp_path / "players", base_edit_path=base_path
    )

    assert calls == [
        ("verify", base_path),
        ("decrypt", base_path),
        ("cleanup", decrypted),
    ]
    assert fake_edit.loaded_path == decrypted / "data.dat"
    assert json.loads(path.read_text(encoding="utf-8"))["draft"]["missing"] == []


def test_update_verification_failure_never_decrypts_or_creates_output(
    monkeypatch, tmp_path
):
    from editor.player_spec import PlayerSpecError

    source = marco_source()
    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps(
            issue_event(
                Operation="update",
                **{
                    "Player name": "Marco Palestra",
                    "Pes Retro Stats profile": MARCO_URL,
                },
            )
        ),
        encoding="utf-8",
    )
    install_fetch(monkeypatch, source)
    monkeypatch.setattr(
        "tools.generate_player_draft.verify_base_file",
        lambda _path: (_ for _ in ()).throw(PlayerSpecError("bad base")),
    )
    monkeypatch.setattr(
        "tools.generate_player_draft.crypto.decrypt",
        lambda _path: pytest.fail("verification must precede decrypt"),
    )
    output_dir = tmp_path / "players"

    with pytest.raises(PlayerDraftError, match="bad base"):
        write_player_draft(
            event_path, output_dir, base_edit_path=tmp_path / "bad-base"
        )
    assert not output_dir.exists()


def test_update_build_failure_still_cleans_decryption_and_writes_nothing(
    monkeypatch, tmp_path
):
    source = marco_source()
    proposal = proposal_for(source)
    fake_edit = FakeEditFile(proposal, changed=False)
    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps(
            issue_event(
                Operation="update",
                **{
                    "Player name": "Marco Palestra",
                    "Pes Retro Stats profile": MARCO_URL,
                },
            )
        ),
        encoding="utf-8",
    )
    decrypted = tmp_path / "fldailyedit_dec_test"
    decrypted.mkdir()
    (decrypted / "data.dat").write_bytes(b"plain")
    cleaned: list[Path] = []
    install_fetch(monkeypatch, source)
    monkeypatch.setattr("tools.generate_player_draft.verify_base_file", lambda _path: None)
    monkeypatch.setattr("tools.generate_player_draft.crypto.decrypt", lambda _path: decrypted)
    monkeypatch.setattr(
        "tools.generate_player_draft.crypto.cleanup_temp", lambda path: cleaned.append(path)
    )
    monkeypatch.setattr("tools.generate_player_draft.EditFile", lambda: fake_edit)
    output_dir = tmp_path / "players"

    with pytest.raises(PlayerDraftError, match="no changes"):
        write_player_draft(
            event_path, output_dir, base_edit_path=tmp_path / "base"
        )
    assert cleaned == [decrypted]
    assert not output_dir.exists()


def test_existing_slug_collision_is_rejected_without_modification(monkeypatch, tmp_path):
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(issue_event()), encoding="utf-8")
    output_dir = tmp_path / "players"
    output_dir.mkdir()
    destination = output_dir / "dastan-satpaev.json"
    destination.write_text("reviewed-content\n", encoding="utf-8")
    install_fetch(monkeypatch, make_source())

    with pytest.raises(PlayerDraftError):
        write_player_draft(event_path, output_dir)
    assert destination.read_text(encoding="utf-8") == "reviewed-content\n"
    assert tuple(output_dir.iterdir()) == (destination,)


def test_concurrent_publication_has_one_winner_and_no_partial_file(
    monkeypatch, tmp_path
):
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(issue_event()), encoding="utf-8")
    output_dir = tmp_path / "players"
    install_fetch(monkeypatch, make_source())

    def attempt() -> Path | PlayerDraftError:
        try:
            return write_player_draft(event_path, output_dir)
        except PlayerDraftError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(lambda _index: attempt(), range(2)))

    assert sum(isinstance(result, Path) for result in results) == 1
    assert sum(isinstance(result, PlayerDraftError) for result in results) == 1
    destination = output_dir / "dastan-satpaev.json"
    assert json.loads(destination.read_bytes())["identity"]["name"] == "Dastan Satpaev"
    assert tuple(output_dir.iterdir()) == (destination,)


def test_untrusted_event_is_rejected_before_profile_fetch(monkeypatch, tmp_path):
    event = issue_event()
    event["action"] = "opened"
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(event), encoding="utf-8")
    fetched = install_fetch(monkeypatch, make_source())
    output_dir = tmp_path / "players"

    with pytest.raises(PlayerDraftError):
        write_player_draft(event_path, output_dir)
    assert fetched == []
    assert not output_dir.exists()

@pytest.mark.parametrize("player_name", ["Dastan  Satpaev", "Dastan\u00a0Satpaev"])
def test_noncanonical_player_name_whitespace_is_rejected_before_profile_fetch(
    monkeypatch, tmp_path, player_name
):
    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps(issue_event(**{"Player name": player_name})), encoding="utf-8"
    )
    fetched = install_fetch(monkeypatch, make_source())
    output_dir = tmp_path / "players"

    with pytest.raises(
        PlayerDraftError, match="^Player name must use canonical whitespace$"
    ):
        write_player_draft(event_path, output_dir)
    assert fetched == []
    assert not output_dir.exists()


def test_cli_prints_exact_machine_output(monkeypatch, tmp_path, capsys):
    import run

    draft = tmp_path / "players" / "dastan-satpaev.json"
    draft.parent.mkdir()
    draft.write_text(
        json.dumps({"identity": {"name": "Dastan Satpaev"}}), encoding="utf-8"
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
        "SPEC_PATH=players/dastan-satpaev.json",
        'PLAYER_NAME="Dastan Satpaev"',
    ]


def test_cli_help_calls_the_output_a_reviewable_pes_retro_stats_proposal(
    monkeypatch, capsys
):
    import run

    monkeypatch.setattr(run.sys, "argv", ["run.py", "players", "generate-draft", "--help"])
    with pytest.raises(SystemExit) as exc_info:
        run.main()
    assert exc_info.value.code == 0
    assert "reviewable Pes Retro Stats proposal" in capsys.readouterr().out
