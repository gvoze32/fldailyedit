"""Exact base-player resolution and grouped PES draft diff coverage."""
from dataclasses import replace
from datetime import date

import pytest

from editor.models import PlayerInfo, TeamData, TeamInfo
from editor.player_codec import ABILITY_FIELDS, POSITION_NAMES, PlayerAbilityProfile
from scraper.pes21_proposal import Pes21Proposal
from scraper.pes_retro_stats import PesRetroStatsProfile
from tools.player_draft_diff import (
    PlayerDraftDiffError,
    build_update_pes,
    resolve_update_player,
)


class FakeEditFile:
    def __init__(self) -> None:
        self.players = {
            162196: PlayerInfo(162196, "Marco Palestra", "Marco Palestra"),
        }
        self.teams = {
            101: TeamInfo(101, "Chelsea FC"),
        }
        self.rosters = {
            101: TeamData(101, [162196] + [0] * 39),
        }
        self.profiles = {162196: make_current_profile()}

    def get_all_players(self):
        return self.players

    def get_all_team_info(self):
        return self.teams

    def get_team_roster(self, team_id):
        return self.rosters.get(team_id)

    def get_player_ability_profile(self, player_id):
        return self.profiles.get(player_id)


def make_current_profile(**changes) -> PlayerAbilityProfile:
    values = {
        "player_id": 162196,
        "nationality_id": 215,
        "height": 180,
        "weight": 68,
        "age": 20,
        "registered_position": "RB",
        "registered_position_id": 3,
        "playing_style": 15,
        "strong_foot": 0,
        "weak_foot_usage": 1,
        "weak_foot_accuracy": 1,
        "form": 5,
        "injury_resistance": 1,
        "abilities": {field: (77 if field == "speed" else 70) for field in ABILITY_FIELDS},
        "position_proficiency": {
            position: (2 if position == "RB" else 0) for position in POSITION_NAMES
        },
        "player_skills": (),
        "com_styles": (),
    }
    values.update(changes)
    return PlayerAbilityProfile(**values)


def make_proposal(**changes) -> Pes21Proposal:
    current = make_current_profile()
    values = {
        "age": current.age,
        "height": current.height,
        "weight": current.weight,
        "registered_position": current.registered_position,
        "unsupported_positions": (),
        "playing_style": current.playing_style,
        "strong_foot": current.strong_foot,
        "weak_foot_usage": current.weak_foot_usage,
        "weak_foot_accuracy": current.weak_foot_accuracy,
        "form": current.form,
        "injury_resistance": current.injury_resistance,
        "position_proficiency": dict(current.position_proficiency),
        "abilities": dict(current.abilities),
        "player_skills": current.player_skills,
        "com_styles": current.com_styles,
    }
    values.update(changes)
    return Pes21Proposal(**values)


@pytest.fixture
def fake_edit():
    return FakeEditFile()


@pytest.fixture
def source_profile():
    return PesRetroStatsProfile(
        player_id="162196",
        short_id="marco-palestra",
        name="Marco Palestra",
        full_name="Marco Palestra",
        profile_url="https://pesstatsdatabase.com/PSD/Player.php?Id=162196&Club=0",
        birth_date=date(2005, 3, 3),
        nationality="Italy",
        current_club="Chelsea FC",
        shirt_number=27,
        height=180,
        weight=68,
        strong_foot="R",
        weak_foot_accuracy=4,
        weak_foot_frequency=4,
        form=5,
        injury_tolerance="B",
        playing_style="Offensive Full-back",
        positions={},
        stats={},
        player_skill_codes=(),
        com_playing_styles=(),
    )


@pytest.fixture
def proposal():
    return make_proposal()


def test_resolve_update_player_requires_one_exact_name_on_submitted_team(
    fake_edit, source_profile, proposal
):
    match = resolve_update_player(
        fake_edit,
        canonical_name="Marco Palestra",
        current_team="Chelsea FC",
        source=source_profile,
        proposal=proposal,
    )

    assert match.pes_id == 162196
    assert match.name == "Marco Palestra"
    assert match.print_name == "Marco Palestra"
    assert match.profile is fake_edit.profiles[162196]


def test_resolve_update_player_rejects_submitted_name_disagreeing_with_source(
    fake_edit, source_profile, proposal
):
    with pytest.raises(PlayerDraftDiffError, match="submitted player name"):
        resolve_update_player(
            fake_edit,
            canonical_name="Marc Palestra",
            current_team="Chelsea FC",
            source=source_profile,
            proposal=proposal,
        )


def test_resolve_update_player_rejects_absent_team(fake_edit, source_profile, proposal):
    source_profile = replace(source_profile, current_club="Arsenal FC")

    with pytest.raises(PlayerDraftDiffError, match="team"):
        resolve_update_player(
            fake_edit,
            canonical_name="Marco Palestra",
            current_team="Arsenal FC",
            source=source_profile,
            proposal=proposal,
        )


def test_resolve_update_player_rejects_duplicate_normalized_team_names(
    fake_edit, source_profile, proposal
):
    fake_edit.teams[102] = TeamInfo(102, "Chelsea-FC")
    fake_edit.rosters[102] = TeamData(102, [162196] + [0] * 39)

    with pytest.raises(PlayerDraftDiffError, match="multiple teams"):
        resolve_update_player(
            fake_edit,
            canonical_name="Marco Palestra",
            current_team="Chelsea FC",
            source=source_profile,
            proposal=proposal,
        )


def test_resolve_update_player_rejects_player_absent_from_exact_team_roster(
    fake_edit, source_profile, proposal
):
    fake_edit.rosters[101] = TeamData(101)

    with pytest.raises(PlayerDraftDiffError, match="roster"):
        resolve_update_player(
            fake_edit,
            canonical_name="Marco Palestra",
            current_team="Chelsea FC",
            source=source_profile,
            proposal=proposal,
        )


def test_resolve_update_player_rejects_two_same_name_roster_candidates(
    fake_edit, source_profile, proposal
):
    fake_edit.players[262196] = PlayerInfo(262196, "Marco Palestra", "M. Palestra")
    fake_edit.rosters[101] = TeamData(101, [162196, 262196] + [0] * 38)

    with pytest.raises(PlayerDraftDiffError, match="multiple roster players"):
        resolve_update_player(
            fake_edit,
            canonical_name="Marco Palestra",
            current_team="Chelsea FC",
            source=source_profile,
            proposal=proposal,
        )


def test_resolve_update_player_rejects_missing_ability_profile(
    fake_edit, source_profile, proposal
):
    fake_edit.profiles.clear()

    with pytest.raises(PlayerDraftDiffError, match="ability profile"):
        resolve_update_player(
            fake_edit,
            canonical_name="Marco Palestra",
            current_team="Chelsea FC",
            source=source_profile,
            proposal=proposal,
        )


def test_resolve_update_player_rejects_current_team_disagreeing_with_source_club(
    fake_edit, source_profile, proposal
):
    with pytest.raises(PlayerDraftDiffError, match="source club"):
        resolve_update_player(
            fake_edit,
            canonical_name="Marco Palestra",
            current_team="Arsenal FC",
            source=source_profile,
            proposal=proposal,
        )


def test_resolve_update_player_ignores_national_team_membership_for_club_match(
    fake_edit, source_profile, proposal
):
    fake_edit.teams[501] = TeamInfo(501, "Italy")
    fake_edit.rosters[501] = TeamData(501, [162196] + [0] * 39)

    match = resolve_update_player(
        fake_edit,
        canonical_name="Marco Palestra",
        current_team="Chelsea FC",
        source=source_profile,
        proposal=proposal,
    )

    assert match.pes_id == 162196


def test_build_update_pes_returns_only_deterministic_grouped_changes():
    current = make_current_profile()
    abilities = dict(current.abilities)
    abilities["speed"] = 90
    target = make_proposal(
        abilities=abilities,
        playing_style=10,
        weak_foot_accuracy=2,
        player_skills=("double_touch",),
        com_styles=("incisive_run",),
    )

    assert build_update_pes(current, target) == {
        "abilities": {"speed": {"from": 77, "to": 90}},
        "playing_style": {"from": 15, "to": 10},
        "weak_foot_accuracy": {"from": 1, "to": 2},
        "player_skills": {"double_touch": {"from": 0, "to": 1}},
        "com_styles": {"incisive_run": {"from": 0, "to": 1}},
    }


def test_build_update_pes_omits_unsupported_positions_but_diffs_supported_grades():
    current = make_current_profile()
    positions = dict(current.position_proficiency)
    positions.update({"LB": 2, "RWB": 2})
    target = make_proposal(
        registered_position=None,
        unsupported_positions=("RWB",),
        position_proficiency=positions,
    )

    result = build_update_pes(current, target)

    assert result == {
        "position_proficiency": {"LB": {"from": 0, "to": 2}},
    }
    assert "registered_position" not in result
    assert "RWB" not in result["position_proficiency"]


def test_build_update_pes_emits_removed_skills_and_com_styles():
    current = make_current_profile(
        player_skills=("double_touch",),
        com_styles=("incisive_run",),
    )
    target = make_proposal()

    assert build_update_pes(current, target) == {
        "player_skills": {"double_touch": {"from": 1, "to": 0}},
        "com_styles": {"incisive_run": {"from": 1, "to": 0}},
    }


def test_build_update_pes_rejects_safe_no_op():
    current = make_current_profile()

    with pytest.raises(
        PlayerDraftDiffError,
        match="^Pes Retro Stats profile has no changes against the base$",
    ):
        build_update_pes(current, make_proposal())
