"""Resolve exact base players and build source-to-base PES update patches."""

from __future__ import annotations

from dataclasses import dataclass

from editor.editfile import EditFile
from editor.player_codec import (
    ABILITY_FIELDS,
    COM_STYLE_FIELDS,
    PLAYER_SKILL_FIELDS,
    POSITION_NAMES,
    PlayerAbilityProfile,
)
from editor.player_spec import normalize_player_identity
from scraper.pes21_proposal import Pes21Proposal
from scraper.pes_retro_stats import PesRetroStatsProfile


class PlayerDraftDiffError(ValueError):
    """Raised when a base player or source-target diff is unsafe."""


@dataclass(frozen=True, slots=True)
class BasePlayerMatch:
    pes_id: int
    name: str
    print_name: str
    profile: PlayerAbilityProfile


def resolve_update_player(
    edit_file: EditFile,
    *,
    canonical_name: str,
    current_team: str,
    source: PesRetroStatsProfile,
    proposal: Pes21Proposal,
) -> BasePlayerMatch:
    """Resolve one exact player from one exact submitted club roster."""
    del proposal
    player_key = normalize_player_identity(canonical_name)
    if not player_key or player_key != normalize_player_identity(source.name):
        raise PlayerDraftDiffError(
            "submitted player name does not match the Pes Retro Stats profile"
        )

    team_key = normalize_player_identity(current_team)
    if not team_key or team_key != normalize_player_identity(source.current_club):
        raise PlayerDraftDiffError(
            "submitted current team does not match the Pes Retro Stats source club"
        )

    team_matches = [
        team
        for team in edit_file.get_all_team_info().values()
        if normalize_player_identity(team.name) == team_key
    ]
    if not team_matches:
        raise PlayerDraftDiffError(
            f"submitted team {current_team!r} was not found in the base"
        )
    if len(team_matches) != 1:
        raise PlayerDraftDiffError(
            f"multiple teams match submitted team {current_team!r}"
        )

    team = team_matches[0]
    roster = edit_file.get_team_roster(team.team_id)
    roster_ids = () if roster is None else roster.roster
    players = edit_file.get_all_players()
    player_matches = [
        players[player_id]
        for player_id in roster_ids
        if player_id in players
        and normalize_player_identity(players[player_id].name) == player_key
    ]
    if not player_matches:
        raise PlayerDraftDiffError(
            f"submitted player {canonical_name!r} was not found in the team roster"
        )
    if len(player_matches) != 1:
        raise PlayerDraftDiffError(
            f"multiple roster players match submitted player {canonical_name!r}"
        )

    player = player_matches[0]
    profile = edit_file.get_player_ability_profile(player.player_id)
    if profile is None:
        raise PlayerDraftDiffError(
            f"base ability profile for PES player {player.player_id} was not found"
        )
    return BasePlayerMatch(
        pes_id=player.player_id,
        name=player.name,
        print_name=player.print_name,
        profile=profile,
    )


def _patch(current: object, target: object) -> dict[str, object]:
    return {"from": current, "to": target}


def build_update_pes(
    current: PlayerAbilityProfile,
    target: Pes21Proposal,
) -> dict[str, object]:
    """Return deterministic Player Update groups for values changed from the base."""
    result: dict[str, object] = {}

    ability_changes = {
        field: _patch(current.abilities[field], target.abilities[field])
        for field in ABILITY_FIELDS
        if current.abilities[field] != target.abilities[field]
    }
    if ability_changes:
        result["abilities"] = ability_changes

    for field in (
        "age",
        "height",
        "weight",
        "playing_style",
        "strong_foot",
        "weak_foot_usage",
        "weak_foot_accuracy",
        "form",
        "injury_resistance",
    ):
        current_value = getattr(current, field)
        target_value = getattr(target, field)
        if current_value != target_value:
            result[field] = _patch(current_value, target_value)

    if (
        target.registered_position is not None
        and current.registered_position != target.registered_position
    ):
        result["registered_position"] = _patch(
            current.registered_position, target.registered_position
        )

    position_changes = {
        position: _patch(
            current.position_proficiency[position],
            target.position_proficiency[position],
        )
        for position in POSITION_NAMES
        if current.position_proficiency[position]
        != target.position_proficiency[position]
    }
    if position_changes:
        result["position_proficiency"] = position_changes

    current_skills = frozenset(current.player_skills)
    target_skills = frozenset(target.player_skills)
    skill_changes = {
        name: _patch(int(name in current_skills), int(name in target_skills))
        for name in PLAYER_SKILL_FIELDS
        if (name in current_skills) != (name in target_skills)
    }
    if skill_changes:
        result["player_skills"] = skill_changes

    current_styles = frozenset(current.com_styles)
    target_styles = frozenset(target.com_styles)
    style_changes = {
        name: _patch(int(name in current_styles), int(name in target_styles))
        for name in COM_STYLE_FIELDS
        if (name in current_styles) != (name in target_styles)
    }
    if style_changes:
        result["com_styles"] = style_changes

    if not result:
        raise PlayerDraftDiffError(
            "Pes Retro Stats profile has no changes against the base"
        )
    return result
