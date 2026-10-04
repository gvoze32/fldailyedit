from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from collections.abc import Collection, Iterable, Mapping
import logging
from math import ceil
from typing import TYPE_CHECKING

import config
from editor.editfile import EditFile
from editor.roster import MIN_CLUB_ROSTER_SIZE
from scraper.club_identity import UNRESOLVED
from scraper.fotmob import parse_iso_datetime
from scraper.matcher import NameMatcher
from scraper.models import MatchedTransfer, SquadMember, SquadSnapshot, Transfer
from scraper.text import fold_text

if TYPE_CHECKING:
    from scraper.club_identity import ClubIdentityIndex

logger = logging.getLogger(__name__)

UNRESOLVED_TEAM_ID = -1
_NON_CLUB_LABELS = {"", "free agent", "without club", "unattached", "career break", "retired"}
_MIN_COMPLETE_SQUAD_MEMBERS = 11
_MIN_CURRENT_SQUAD_MATCH_RATIO = 0.75
_STRONG_TEAM_NAME_SCORE = 98.0

_ATTACKING_SHORT_ALIAS_POSITIONS = frozenset(
    {"AMF", "CAM", "AM", "SS", "LWF", "RWF", "LW", "RW", "LMF", "RMF"}
)

_MIN_SHORT_ALIAS_SCORE = 80.0
_SNAPSHOT_SOURCE = "fotmob_squad"


def _team_touches_save(team_id: int | None, club_ids: Collection[int] | None) -> bool:
    """True when a resolved side is a save club or an unresolved (maybe-save) club."""
    if team_id is None:
        return False
    if team_id == UNRESOLVED_TEAM_ID or club_ids is None:
        return True
    return team_id in club_ids


@dataclass(frozen=True, slots=True)
class SkippedTransfer:
    """One transfer the plan did not apply, with an explicit reason code.

    ``relevant`` is False only when both clubs confidently sit outside the
    selected save, so real misses are not hidden among foreign-league noise.
    """

    player_name: str
    from_team: str
    to_team: str
    date: str
    source: str
    reason: str
    detail: str
    relevant: bool
    fotmob_player_id: int | None
    candidates: tuple[str, ...]

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["candidates"] = list(self.candidates)
        return payload

    @classmethod
    def from_match(
        cls,
        match: MatchedTransfer,
        reason: str,
        detail: str = "",
        *,
        candidates: Iterable[str] = (),
        club_ids: Collection[int] | None = None,
    ) -> SkippedTransfer:
        transfer = match.transfer
        return cls(
            player_name=transfer.player_name,
            from_team=transfer.from_club_full_name or transfer.from_club,
            to_team=transfer.to_club_full_name or transfer.to_club,
            date=transfer.date,
            source=",".join(transfer.sources),
            reason=reason,
            detail=detail,
            relevant=(
                _team_touches_save(match.from_team_id, club_ids)
                or _team_touches_save(match.to_team_id, club_ids)
            ),
            fotmob_player_id=_optional_positive_int(transfer.player_id_fotmob),
            candidates=tuple(candidates),
        )


@dataclass
class PlanningReport:
    """Diagnostics shared by matching and roster planning for one run.

    ``skipped`` collects every event the plan could not apply. ``live_squad_ids``
    maps each save club to the PES players its complete live FotMob squad
    contains; planning uses it as reconciliation evidence and overflow
    protection. ``fotmob_player_ids`` maps FotMob player IDs to unique PES
    player IDs (live snapshot identity preferred over transfer-log history).
    ``match_reasons`` carries matching-stage skip reasons keyed by ``id()`` of
    the ``MatchedTransfer`` they belong to.
    """

    skipped: list[SkippedTransfer] = field(default_factory=list)
    live_squad_ids: dict[int, frozenset[int]] = field(default_factory=dict)
    fotmob_player_ids: dict[int, int] = field(default_factory=dict)
    match_reasons: dict[int, tuple[str, str, tuple[str, ...]]] = field(
        default_factory=dict
    )


@dataclass
class PlannedRosterAction:
    match: MatchedTransfer
    action: str
    current_team_id: int | None
    reason: str = ""
    overflow_player_id: int | None = None
    overflow_details: dict[str, object] | None = None
    detail: str = ""


@dataclass(frozen=True, slots=True)
class _SnapshotRosterCoverage:
    """Quality of one provider snapshot against the current local roster."""

    snapshot: SquadSnapshot
    team_id: int
    team_name: str
    current_player_ids: frozenset[int]
    snapshot_player_ids: frozenset[int]
    healthy: bool

def _optional_positive_int(value) -> int | None:
    """Parse an identifier from external/history data and reject sentinel values."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _resolve_snapshot_club(
    matcher: NameMatcher,
    club_identity: ClubIdentityIndex | None,
    fotmob_team_id: int,
    club_name: str,
) -> int | None:
    """Map one live FotMob club to a save club, or None when not provably one."""
    if club_identity is not None:
        team_id = club_identity.pes_for_fotmob(fotmob_team_id)
        if team_id is not None:
            return team_id
        resolved = club_identity.resolve_name(club_name)
        if resolved is UNRESOLVED or resolved is None:
            return None
        bound = club_identity.fotmob_for_pes(resolved)
        return resolved if bound in (None, fotmob_team_id) else None
    matched_team_id, _, confidence = matcher.match_team(
        club_name,
        threshold=_STRONG_TEAM_NAME_SCORE,
    )
    return matched_team_id if confidence >= _STRONG_TEAM_NAME_SCORE else None


def _transfer_sort_key(transfer):
    """Apply dated transfers chronologically and shirt-number updates last."""
    parsed_date = parse_iso_datetime(transfer.date)
    return (
        transfer.transfer_type == "shirt_number_update",
        parsed_date is None,
        parsed_date or parse_iso_datetime("2000-01-01"),
    )


def _resolve_identity_team(
    matcher: NameMatcher,
    club_identity: ClubIdentityIndex,
    fotmob_id: int | None,
    names: list[str],
) -> tuple[int | None, str, float]:
    """Resolve a club via learned FotMob bindings, then selected-save names."""
    if fotmob_id is not None:
        team_id = club_identity.pes_for_fotmob(fotmob_id)
        if team_id is not None:
            return team_id, names[0] if names else "", 100.0

    resolved_ids: set[int] = set()
    unsure = False
    for name in names:
        resolved = club_identity.resolve_name(name)
        if resolved is UNRESOLVED:
            unsure = True
        elif resolved is not None:
            bound = club_identity.fotmob_for_pes(resolved)
            if fotmob_id is not None and bound is not None and bound != fotmob_id:
                # The name points at a save club already bound to another
                # provider club: this is a different (possibly reserve) club.
                unsure = True
            else:
                resolved_ids.add(resolved)
    if len(resolved_ids) > 1:
        logger.warning(
            "Conflicting club identities for %s: %s", names, sorted(resolved_ids)
        )
        return UNRESOLVED_TEAM_ID, "", 0.0
    if resolved_ids:
        team_id = next(iter(resolved_ids))
        return team_id, matcher.get_team_name(team_id) or names[0], 100.0
    if unsure:
        return UNRESOLVED_TEAM_ID, "", 0.0
    return None, "", 100.0


def _match_transfer_team(
    matcher: NameMatcher,
    short_name: str,
    full_name: str = "",
    fotmob_id: int | str | None = None,
    club_identity: ClubIdentityIndex | None = None,
) -> tuple[int | None, str, float]:
    """Resolve a transfer club to a save club, None (outside), or UNRESOLVED.

    A weak or unvalidated name never becomes "outside the save": that would
    turn a move into a save club into a release. It is UNRESOLVED instead.
    """
    raw_names = [full_name or "", short_name or ""]
    if any(name.strip().casefold() in _NON_CLUB_LABELS for name in raw_names if name.strip()):
        return None, "", 100.0

    normalized_fotmob_id: int | None = None
    if fotmob_id is not None:
        try:
            normalized_fotmob_id = int(fotmob_id)
        except (TypeError, ValueError):
            return UNRESOLVED_TEAM_ID, "", 0.0

    names: list[str] = []
    for value in (full_name, short_name):
        clean = (value or "").strip()
        if clean and clean.casefold() not in {name.casefold() for name in names}:
            names.append(clean)

    if club_identity is not None:
        return _resolve_identity_team(
            matcher, club_identity, normalized_fotmob_id, names
        )

    results = [matcher.match_team(name) for name in names]
    matched_ids = {team_id for team_id, _, _ in results if team_id is not None}
    if len(matched_ids) > 1:
        logger.warning(
            "Conflicting club identities for %s: %s",
            names,
            sorted(matched_ids),
        )
        return UNRESOLVED_TEAM_ID, "", max(
            (confidence for _, _, confidence in results), default=0.0
        )
    if matched_ids:
        team_id = next(iter(matched_ids))
        best_result = max(
            (result for result in results if result[0] == team_id),
            key=lambda result: result[2],
        )
        if best_result[2] < _STRONG_TEAM_NAME_SCORE:
            logger.warning(
                "Rejecting weak club name match for %s at %.1f%%",
                full_name or short_name,
                best_result[2],
            )
            return UNRESOLVED_TEAM_ID, "", best_result[2]
        return best_result
    best_unresolved_confidence = max(
        (confidence for _, _, confidence in results), default=0.0
    )
    if best_unresolved_confidence >= float(config.MATCH_THRESHOLD_TEAM):
        # Similar save clubs exist but none uniquely: unsure, not outside.
        return UNRESOLVED_TEAM_ID, "", best_unresolved_confidence
    return None, "", best_unresolved_confidence


def _transfer_event_order_key(
    indexed_transfer: tuple[int, object],
) -> tuple[bool, datetime, int]:
    """Order transfer events by timestamp while preserving unknown-order input."""
    index, transfer = indexed_transfer
    event_datetime = parse_iso_datetime(str(getattr(transfer, "date", "") or ""))
    return (
        event_datetime is None,
        event_datetime or datetime.max.replace(tzinfo=timezone.utc),
        index,
    )


def _snapshot_identity_map(
    squad_snapshots: tuple[SquadSnapshot, ...] | list[SquadSnapshot],
) -> dict[int, tuple[tuple[int, str, SquadMember], ...]]:
    """Build a compact FotMob identity index from complete snapshots."""
    observations: dict[int, list[tuple[int, str, SquadMember]]] = {}
    for snapshot in squad_snapshots:
        if not snapshot.complete:
            continue
        for member in snapshot.members:
            player_id = _optional_positive_int(member.player_id_fotmob)
            if player_id is None:
                continue
            observations.setdefault(player_id, []).append(
                (snapshot.team_id_fotmob, snapshot.club_name, member)
            )
    return {
        player_id: tuple(values)
        for player_id, values in observations.items()
    }




def _all_roster_ids(team_player_map: Mapping[int, Iterable[int]]) -> frozenset[int]:
    return frozenset(
        player_id
        for roster in team_player_map.values()
        for player_id in roster
        if player_id
    )


def _match_snapshot_member(
    matcher: NameMatcher,
    member: SquadMember,
    team_id: int,
    team_player_map: dict[int, list[int]],
    threshold: float,
    team_shirt_numbers: Mapping[int, Mapping[int, int]] | None = None,
    all_roster_ids: frozenset[int] | None = None,
) -> tuple[int | None, str, float]:
    """Resolve a snapshot identity before using a guarded fuzzy fallback.

    ``all_roster_ids`` may be precomputed by callers matching many members
    against an unchanged ``team_player_map``.
    """
    if all_roster_ids is None:
        all_roster_ids = _all_roster_ids(team_player_map)
    team_roster_ids = {
        player_id
        for player_id in team_player_map.get(team_id, ())
        if player_id
    }
    exact_records = list(
        getattr(matcher, "_player_candidates", {}).get(
            fold_text(member.player_name),
            (),
        )
    )
    exact_global_candidate: tuple[str, int] | None = None
    if exact_records:
        # Prefer an exact identity already registered at this snapshot's club.
        # A provider can shorten a name ("Endrick" vs "Endrick Felipe") while
        # an unrelated player elsewhere has the shorter exact name.
        age_compatible = [
            candidate
            for candidate in exact_records
            if matcher._is_player_age_compatible(
                candidate[1],
                age=member.age,
            )
        ]
        team_candidates = [
            candidate
            for candidate in age_compatible
            if candidate[1] in team_roster_ids
        ]
        if len(team_candidates) == 1:
            alias_match = _match_short_name_by_current_shirt(
                matcher,
                member,
                team_id,
                team_player_map,
                team_shirt_numbers,
            )
            if alias_match is not None and alias_match[0] != team_candidates[0][1]:
                return alias_match
            name, player_id = team_candidates[0]
            return player_id, name, 100.0
        if len(team_candidates) > 1:
            metadata_candidates = [
                candidate
                for candidate in team_candidates
                if matcher._is_player_metadata_compatible(
                    candidate[1],
                    position=member.position,
                    age=member.age,
                )
            ]
            if len(metadata_candidates) == 1:
                name, player_id = metadata_candidates[0]
                return player_id, name, 100.0
            alias_match = _match_short_name_by_current_shirt(
                matcher,
                member,
                team_id,
                team_player_map,
                team_shirt_numbers,
            )
            if alias_match is not None:
                return alias_match
            return None, "", 100.0

        # A unique exact candidate already in any local roster is safe when
        # the snapshot club has no stronger contextual candidate. This covers
        # single-name players such as "Allan" and "Sávio" without guessing
        # among homonyms.
        if len(age_compatible) == 1:
            candidate = age_compatible[0]
            if (
                candidate[1] in all_roster_ids
                and matcher._is_player_metadata_compatible(
                    candidate[1],
                    position=member.position,
                    age=member.age,
                )
            ):
                exact_global_candidate = candidate
        # A unique exact catalog identity absent from every local roster is
        # safe to restore from a complete current-squad snapshot only when
        # the provider supplies a multi-token identity.
        if len(age_compatible) == 1 and len(
            fold_text(member.player_name).split()
        ) >= 2:
            candidate = age_compatible[0]
            if (
                candidate[1] not in all_roster_ids
                and matcher._is_player_metadata_compatible(
                    candidate[1],
                    position=member.position,
                    age=member.age,
                )
            ):
                name, player_id = candidate
                return player_id, name, 100.0


        # Multi-token exact names are safe enough to resolve from another
        # current club (the normal inferred-move case).
        if len(fold_text(member.player_name).split()) > 1:
            roster_candidates = [
                candidate
                for candidate in age_compatible
                if candidate[1] in all_roster_ids
            ]
            if len(roster_candidates) == 1:
                name, player_id = roster_candidates[0]
                return player_id, name, 100.0
            if len(roster_candidates) > 1:
                metadata_candidates = [
                    candidate
                    for candidate in roster_candidates
                    if matcher._is_player_metadata_compatible(
                        candidate[1],
                        position=member.position,
                        age=member.age,
                    )
                ]
                if len(metadata_candidates) == 1:
                    name, player_id = metadata_candidates[0]
                    return player_id, name, 100.0
                return None, "", 100.0

    alias_match = _match_short_name_by_current_shirt(
        matcher,
        member,
        team_id,
        team_player_map,
        team_shirt_numbers,
    )
    if alias_match is not None:
        return alias_match

    # Search only the snapshot club's local roster before falling back to a
    # global fuzzy match. This handles abbreviated provider names without
    # allowing a short exact name from an unrelated club to win.
    query_norm = fold_text(member.player_name)
    contextual_scores: dict[int, tuple[float, str]] = {}
    for candidate_id in team_roster_ids:
        for candidate_norm, candidate_name in getattr(
            matcher,
            "_player_id_to_names",
            {},
        ).get(candidate_id, ()):
            score = matcher._score_player(
                query_norm,
                candidate_norm,
                position=member.position,
                candidate_pid=candidate_id,
                nationality=member.nationality,
                age=member.age,
            )
            previous = contextual_scores.get(candidate_id)
            if previous is None or score > previous[0]:
                contextual_scores[candidate_id] = (score, candidate_name)

    ranked_context = sorted(
        (
            (score, name, candidate_id)
            for candidate_id, (score, name) in contextual_scores.items()
        ),
        reverse=True,
    )
    minimum_context_confidence = max(float(threshold or 0), 90.0)
    if ranked_context and ranked_context[0][0] >= minimum_context_confidence:
        best_score, best_name, best_id = ranked_context[0]
        runner_up = next(
            (item for item in ranked_context[1:] if item[2] != best_id),
            None,
        )
        if runner_up is None or best_score - runner_up[0] >= 3.0:
            return best_id, best_name, best_score
        return None, "", best_score

    # A unique exact name already present in another local roster is safe
    # after contextual matching; do not use a fuzzy one-token global guess.
    if exact_global_candidate is not None:
        name, player_id = exact_global_candidate
        return player_id, name, 100.0
    if len(query_norm.split()) < 2:
        return None, "", max(
            (score for score, _, _ in ranked_context),
            default=0.0,
        )
    global_match_threshold = max(float(threshold or 0), 95.0)
    player_id, player_name, confidence = matcher.match_player(
        member.player_name,
        threshold=global_match_threshold,
        from_team_id=team_id,
        team_player_map=team_player_map,
        position=member.position,
        nationality=member.nationality,
        age=member.age,
    )
    if (
        player_id is None
        or player_id not in all_roster_ids
        or confidence < global_match_threshold
    ):
        return None, "", confidence
    return player_id, player_name, confidence

def _match_short_name_by_current_shirt(
    matcher: NameMatcher,
    member: SquadMember,
    team_id: int,
    team_player_map: dict[int, list[int]],
    team_shirt_numbers: Mapping[int, Mapping[int, int]] | None,
) -> tuple[int, str, float] | None:
    """Resolve a single-token alias only with unique roster and shirt evidence."""
    query_tokens = fold_text(member.player_name).split()
    shirt_number = _optional_positive_int(member.shirt_number)
    if (
        len(query_tokens) != 1
        or shirt_number is None
        or team_shirt_numbers is None
    ):
        return None

    local_shirt_numbers = team_shirt_numbers.get(team_id, {})
    provider_position = (member.position or "").strip().upper()
    candidates: dict[int, tuple[float, str]] = {}
    for player_id in team_player_map.get(team_id, ()):
        if (
            _optional_positive_int(local_shirt_numbers.get(player_id))
            != shirt_number
            or not matcher._is_player_age_compatible(
                player_id,
                age=member.age,
            )
        ):
            continue

        position_compatible = matcher._is_player_metadata_compatible(
            player_id,
            position=member.position,
            age=member.age,
        )
        candidate_position = (
            matcher._player_positions.get(player_id, "") or ""
        ).strip().upper()
        adjacent_attacking_positions = (
            provider_position in _ATTACKING_SHORT_ALIAS_POSITIONS
            and candidate_position in _ATTACKING_SHORT_ALIAS_POSITIONS
        )
        for candidate_norm, candidate_name in getattr(
            matcher,
            "_player_id_to_names",
            {},
        ).get(player_id, ()):
            candidate_tokens = candidate_norm.split()
            if not candidate_tokens:
                continue
            exact_first_token = candidate_tokens[0] == query_tokens[0]
            if not position_compatible and not (
                adjacent_attacking_positions and exact_first_token
            ):
                continue
            score = matcher._score_player(query_tokens[0], candidate_norm)
            if score < _MIN_SHORT_ALIAS_SCORE:
                continue
            previous = candidates.get(player_id)
            if previous is None or score > previous[0]:
                candidates[player_id] = (score, candidate_name)

    if len(candidates) != 1:
        return None
    player_id, (_, player_name) = next(iter(candidates.items()))
    return player_id, player_name, 95.0




def _build_fotmob_identity_index(
    matcher: NameMatcher,
    team_player_map: dict[int, list[int]],
    club_ids: set[int],
    threshold: float,
    club_identity: ClubIdentityIndex | None,
    squad_snapshots: tuple[SquadSnapshot, ...] | list[SquadSnapshot],
    fotmob_identity_map: Mapping[
        int,
        tuple[tuple[int, str, SquadMember], ...],
    ]
    | None = None,
    team_shirt_numbers: Mapping[int, Mapping[int, int]] | None = None,
) -> tuple[
    dict[int, int],
    dict[int, str],
    Mapping[int, tuple[tuple[int, str, SquadMember], ...]],
    dict[int, frozenset[int]],
]:
    """Resolve current FotMob IDs to unique PES players once per snapshot.

    Also returns each save club's live squad as unique PES player IDs.
    """
    identity_map = (
        fotmob_identity_map
        if fotmob_identity_map is not None
        else _snapshot_identity_map(squad_snapshots)
    )
    candidates: dict[int, set[int]] = {}
    names: dict[int, str] = {}
    team_cache: dict[int, int | None] = {}
    provider_local_teams: dict[int, set[int]] = {}
    all_roster_ids = _all_roster_ids(team_player_map)

    for fotmob_player_id, observations in identity_map.items():
        normalized_player_id = _optional_positive_int(fotmob_player_id)
        if normalized_player_id is None:
            continue
        for observation in observations:
            if len(observation) == 2:
                fotmob_team_id, member = observation
                club_name = ""
            else:
                fotmob_team_id, club_name, member = observation
            normalized_team_id = _optional_positive_int(fotmob_team_id)
            if normalized_team_id is None:
                continue
            if normalized_team_id not in team_cache:
                team_cache[normalized_team_id] = _resolve_snapshot_club(
                    matcher, club_identity, normalized_team_id, club_name
                )
            local_team_id = team_cache[normalized_team_id]
            if local_team_id is None or local_team_id not in club_ids:
                continue
            player_id, player_name, confidence = _match_snapshot_member(
                matcher,
                member,
                local_team_id,
                team_player_map,
                threshold,
                team_shirt_numbers,
                all_roster_ids,
            )
            if player_id is None:
                continue
            candidates.setdefault(normalized_player_id, set()).add(player_id)
            provider_local_teams.setdefault(normalized_player_id, set()).add(
                local_team_id
            )
            if confidence >= max(float(threshold), 95.0):
                names[normalized_player_id] = player_name or member.player_name

    unique = {
        fotmob_player_id: next(iter(player_ids))
        for fotmob_player_id, player_ids in candidates.items()
        if len(player_ids) == 1
    }

    # One PES identity must not be claimed by multiple distinct provider
    # identities. Prefer the provider identity anchored to that player's
    # current local roster; otherwise reject the ambiguous mappings.
    provider_ids_by_player: dict[int, set[int]] = {}
    for fotmob_player_id, player_id in unique.items():
        provider_ids_by_player.setdefault(player_id, set()).add(fotmob_player_id)
    allowed_provider_ids = set(unique)
    for player_id, provider_ids in provider_ids_by_player.items():
        if len(provider_ids) <= 1:
            continue
        current_team_ids = {
            team_id
            for team_id in club_ids
            if player_id in team_player_map.get(team_id, ())
        }
        anchored_provider_ids = [
            provider_id
            for provider_id in provider_ids
            if provider_local_teams.get(provider_id, set()) & current_team_ids
        ]
        if len(anchored_provider_ids) == 1:
            keep = anchored_provider_ids[0]
            allowed_provider_ids.difference_update(provider_ids - {keep})
            logger.warning(
                "Provider identity collision for PES player %s; keeping "
                "provider %s anchored to current team",
                player_id,
                keep,
            )
        else:
            allowed_provider_ids.difference_update(provider_ids)
            logger.warning(
                "Rejecting ambiguous provider identities %s for PES player %s",
                sorted(provider_ids),
                player_id,
            )

    unique = {
        fotmob_player_id: player_id
        for fotmob_player_id, player_id in unique.items()
        if fotmob_player_id in allowed_provider_ids
    }
    unique_names = {
        fotmob_player_id: names[fotmob_player_id]
        for fotmob_player_id in unique
        if fotmob_player_id in names
    }
    live_squad_ids = {
        team_id: frozenset(
            unique[fotmob_player_id]
            for fotmob_player_id in unique
            if team_id in provider_local_teams.get(fotmob_player_id, ())
        )
        for team_id in {
            team_id
            for teams in provider_local_teams.values()
            for team_id in teams
        }
    }
    return unique, unique_names, identity_map, live_squad_ids

def _build_snapshot_roster_coverage(
    matcher: NameMatcher,
    threshold: float,
    virtual_rosters: dict[int, list[int]],
    club_ids: set[int],
    squad_snapshots: tuple[SquadSnapshot, ...] | list[SquadSnapshot],
    fotmob_to_pes: dict[int, int],
    club_identity: ClubIdentityIndex | None,
    team_shirt_numbers: Mapping[int, Mapping[int, int]] | None = None,
) -> dict[int, _SnapshotRosterCoverage]:
    """Classify snapshots before allowing destructive roster reconciliation."""
    coverage: dict[int, _SnapshotRosterCoverage] = {}
    seen_team_ids: set[int] = set()
    all_roster_ids = _all_roster_ids(virtual_rosters)

    for snapshot in squad_snapshots:
        if (
            not snapshot.complete
            or len(snapshot.members) < _MIN_COMPLETE_SQUAD_MEMBERS
        ):
            continue

        fotmob_team_id = _optional_positive_int(snapshot.team_id_fotmob)
        if fotmob_team_id is None:
            continue
        team_id = _resolve_snapshot_club(
            matcher, club_identity, fotmob_team_id, snapshot.club_name
        )
        team_name = snapshot.club_name
        if (
            team_id is None
            or team_id not in club_ids
            or team_id in seen_team_ids
        ):
            continue
        seen_team_ids.add(team_id)

        current_ids = frozenset(
            normalized_player_id
            for raw_player_id in virtual_rosters.get(team_id, ())
            if (
                normalized_player_id := _optional_positive_int(raw_player_id)
            ) is not None
        )
        snapshot_player_ids: set[int] = set()
        for member in snapshot.members:
            fotmob_player_id = _optional_positive_int(member.player_id_fotmob)
            known_player_id = (
                fotmob_to_pes.get(fotmob_player_id)
                if fotmob_player_id is not None
                else None
            )
            if known_player_id is not None and known_player_id in current_ids:
                snapshot_player_ids.add(known_player_id)
                continue

            player_id, _, player_confidence = _match_snapshot_member(
                matcher,
                member,
                team_id,
                virtual_rosters,
                threshold,
                team_shirt_numbers,
                all_roster_ids,
            )
            if (
                player_id is not None
                and player_id in current_ids
                and player_confidence >= max(float(threshold or 0), 90.0)
            ):
                snapshot_player_ids.add(player_id)

        minimum_current_matches = max(
            _MIN_COMPLETE_SQUAD_MEMBERS,
            ceil(len(current_ids) * _MIN_CURRENT_SQUAD_MATCH_RATIO),
        )
        healthy = bool(current_ids) and (
            len(snapshot_player_ids) >= minimum_current_matches
        )
        if not healthy:
            logger.warning(
                "Skipping roster reconciliation for %s (%s): only %s/%s "
                "current roster players matched",
                team_name or snapshot.club_name,
                team_id,
                len(snapshot_player_ids),
                len(current_ids),
            )
        coverage[team_id] = _SnapshotRosterCoverage(
            snapshot=snapshot,
            team_id=team_id,
            team_name=team_name or snapshot.club_name,
            current_player_ids=current_ids,
            snapshot_player_ids=frozenset(snapshot_player_ids),
            healthy=healthy,
        )
    return coverage


def _snapshot_skip(
    member: SquadMember,
    fotmob_player_id: int,
    source_name: str,
    destination_name: str,
    reason: str,
    detail: str,
    candidates: Iterable[str] = (),
) -> SkippedTransfer:
    """Describe a live-squad move into a save club that was not planned."""
    return SkippedTransfer(
        player_name=member.player_name,
        from_team=source_name,
        to_team=destination_name,
        date="",
        source=_SNAPSHOT_SOURCE,
        reason=reason,
        detail=detail,
        relevant=True,
        fotmob_player_id=fotmob_player_id,
        candidates=tuple(candidates),
    )


def _append_current_squad_moves(
    matched: list[MatchedTransfer],
    matcher: NameMatcher,
    virtual_rosters: dict[int, list[int]],
    club_ids: set[int],
    identity_map: Mapping[int, tuple[tuple[int, str, SquadMember], ...]],
    fotmob_to_pes: dict[int, int],
    fotmob_identity_names: dict[int, str],
    club_identity: ClubIdentityIndex | None,
    snapshot_coverage: Mapping[int, _SnapshotRosterCoverage],
    allow_uncovered_source: bool = False,
    skipped: list[SkippedTransfer] | None = None,
) -> None:
    """Create safe moves and destination-only registrations from snapshots.

    Every live-squad player who sits elsewhere in the save but cannot be moved
    safely is recorded in ``skipped`` with a reason code.
    """
    seen: set[tuple[int, int]] = set()
    uncovered_source_ids: set[int] = set()
    destination_cache: dict[int, int | None] = {}

    def team_label(team_id: int | None) -> str:
        if team_id is None:
            return "Free Agent"
        return matcher.get_team_name(team_id) or f"Team {team_id}"

    for raw_fotmob_id, observations in identity_map.items():
        fotmob_player_id = _optional_positive_int(raw_fotmob_id)
        if fotmob_player_id is None:
            continue
        player_id = fotmob_to_pes.get(fotmob_player_id)
        if player_id is None:
            continue

        destination_observations: list[
            tuple[int, int, str, SquadMember]
        ] = []
        destination_ids: set[int] = set()
        for observation in observations:
            if len(observation) == 2:
                fotmob_team_id, member = observation
                club_name = ""
            else:
                fotmob_team_id, club_name, member = observation
            normalized_team_id = _optional_positive_int(fotmob_team_id)
            if normalized_team_id is None:
                continue
            if normalized_team_id not in destination_cache:
                destination_cache[normalized_team_id] = _resolve_snapshot_club(
                    matcher, club_identity, normalized_team_id, club_name
                )
            destination_id = destination_cache[normalized_team_id]
            if destination_id is None:
                continue
            destination_ids.add(destination_id)
            destination_observations.append(
                (
                    destination_id,
                    normalized_team_id,
                    club_name,
                    member,
                )
            )

        save_destination_ids = destination_ids & club_ids
        if not save_destination_ids:
            continue
        current_clubs = [
            team_id
            for team_id, roster in virtual_rosters.items()
            if team_id in club_ids and player_id in roster
        ]
        first_member = next(
            member
            for team_id, _, _, member in destination_observations
            if team_id in save_destination_ids
        )

        def record(
            reason: str,
            detail: str,
            destination_name: str,
            candidates: Iterable[str] = (),
        ) -> None:
            if skipped is not None:
                skipped.append(
                    _snapshot_skip(
                        first_member,
                        fotmob_player_id,
                        ", ".join(team_label(team_id) for team_id in current_clubs)
                        or "Free Agent",
                        destination_name,
                        reason,
                        detail,
                        candidates,
                    )
                )

        if len(destination_ids) != 1:
            if not save_destination_ids.issubset(current_clubs):
                names = [team_label(team_id) for team_id in sorted(destination_ids)]
                record(
                    "snapshot_conflicting_clubs",
                    "player appears in several live squads",
                    ", ".join(names),
                    names,
                )
            continue
        destination_id = next(iter(destination_ids))
        destination_label = team_label(destination_id)

        if destination_id in current_clubs and len(current_clubs) == 1:
            continue
        if len(current_clubs) > 1:
            record(
                "duplicate_registration",
                f"save registers player at {sorted(current_clubs)}",
                destination_label,
                [team_label(team_id) for team_id in current_clubs],
            )
            continue
        source_id = current_clubs[0] if current_clubs else None

        destination_coverage = snapshot_coverage.get(destination_id)
        if destination_coverage is None:
            record(
                "snapshot_incomplete",
                "destination live squad is incomplete or duplicated",
                destination_label,
            )
            continue
        # A complete destination snapshot is sufficient evidence for a
        # non-destructive registration of a uniquely resolved catalog player.
        # Stale local academy entries can make the coverage ratio unhealthy;
        # they must not prevent adding a player absent from every local roster.
        if (
            not destination_coverage.healthy
            and bool(destination_coverage.current_player_ids)
            and source_id is not None
        ):
            record(
                "snapshot_coverage_low",
                f"{len(destination_coverage.snapshot_player_ids)}/"
                f"{len(destination_coverage.current_player_ids)} destination "
                "roster players confirmed by live squad",
                destination_label,
            )
            continue

        # A healthy complete destination snapshot is authoritative for current
        # membership, even when the player's local source team was not indexed.
        # Keep moves gated when destination coverage is also unhealthy.
        if source_id is not None:
            source_coverage = snapshot_coverage.get(source_id)
            if (
                source_coverage is None
                and not allow_uncovered_source
                and not destination_coverage.healthy
            ):
                if source_id not in uncovered_source_ids:
                    logger.warning(
                        "Skipping current-squad moves from %s (%s): source roster "
                        "snapshot is missing",
                        team_label(source_id),
                        source_id,
                    )
                    uncovered_source_ids.add(source_id)
                record(
                    "source_snapshot_missing",
                    "source club has no live squad and destination coverage is low",
                    destination_label,
                )
                continue
        if (player_id, destination_id) in seen:
            continue

        source_name = (
            matcher.get_team_name(source_id)
            if source_id is not None
            else "Free Agent"
        )
        destination_name = matcher.get_team_name(destination_id)
        if (source_id is not None and not source_name) or not destination_name:
            record(
                "team_name_missing",
                "save club has no loaded name",
                destination_label,
            )
            continue
        seen.add((player_id, destination_id))
        _, raw_team_id, _, member = destination_observations[0]
        source_url = f"https://www.fotmob.com/api/data/teams?id={raw_team_id}"
        transfer = Transfer(
            player_name=member.player_name,
            from_club=source_name,
            to_club=destination_name,
            transfer_type="squad_registration",
            shirt_number=member.shirt_number,
            position=member.position,
            age=member.age,
            nationality=member.nationality,
            to_club_id_fotmob=raw_team_id,
            player_id_fotmob=fotmob_player_id,
            from_club_full_name=source_name,
            to_club_full_name=destination_name,
            source_urls=(source_url,),
            proof_urls=(source_url,),
            verification_status="enabled",
            infer_from_current_roster=source_id is not None,
        )
        move = MatchedTransfer(
            transfer=transfer,
            player_id=player_id,
            from_team_id=source_id,
            to_team_id=destination_id,
            player_confidence=100.0,
            from_team_confidence=100.0,
            to_team_confidence=100.0,
            matched_player_name=(
                fotmob_identity_names.get(fotmob_player_id)
                or member.player_name
            ),
            matched_from_team=source_name,
            matched_to_team=destination_name,
        )
        insert_at = next(
            (
                index
                for index, item in enumerate(matched)
                if item.transfer.transfer_type == "shirt_number_update"
            ),
            len(matched),
        )
        matched.insert(insert_at, move)
        if source_id is not None:
            virtual_rosters[source_id].remove(player_id)
        virtual_rosters.setdefault(destination_id, []).append(player_id)




def _append_current_squad_releases(
    matched: list[MatchedTransfer],
    virtual_rosters: dict[int, list[int]],
    snapshot_coverage: Mapping[int, _SnapshotRosterCoverage],
    player_names: dict[int, str] | None,
) -> None:
    """Insert safe releases before shirt updates for healthy snapshots."""
    released_routes = {
        (match.player_id, match.from_team_id)
        for match in matched
        if match.is_release and match.player_id is not None
    }
    protected_destination_ids: dict[int, set[int]] = {}
    for match in matched:
        if (
            match.player_id is None
            or match.to_team_id is None
            or match.transfer.transfer_type == "shirt_number_update"
        ):
            continue
        if match.player_id in virtual_rosters.get(match.to_team_id, ()):
            protected_destination_ids.setdefault(match.to_team_id, set()).add(
                match.player_id
            )
    insert_at = next(
        (
            index
            for index, item in enumerate(matched)
            if item.transfer.transfer_type == "shirt_number_update"
        ),
        len(matched),
    )

    for coverage in snapshot_coverage.values():
        if not coverage.healthy:
            continue
        team_id = coverage.team_id
        current_ids = {
            normalized_player_id
            for raw_player_id in virtual_rosters.get(team_id, ())
            if (
                normalized_player_id := _optional_positive_int(raw_player_id)
            ) is not None
        }
        for player_id in sorted(current_ids - coverage.snapshot_player_ids):
            if (
                (player_id, team_id) in released_routes
                or player_id in protected_destination_ids.get(team_id, set())
            ):
                continue
            player_name = (player_names or {}).get(player_id) or f"Player {player_id}"
            matched.insert(
                insert_at,
                MatchedTransfer(
                    transfer=Transfer(
                        player_name=player_name,
                        from_club=coverage.team_name,
                        to_club="Free Agent",
                        transfer_type="squad_release",
                        from_club_id_fotmob=coverage.snapshot.team_id_fotmob,
                        from_club_full_name=coverage.team_name,
                        source_urls=(coverage.snapshot.source_url,),
                        proof_urls=(coverage.snapshot.source_url,),
                        verification_status="enabled",
                        infer_from_current_roster=True,
                    ),
                    player_id=player_id,
                    from_team_id=team_id,
                    from_team_confidence=100.0,
                    player_confidence=100.0,
                    matched_player_name=player_name,
                    matched_from_team=coverage.team_name,
                )
            )
            insert_at += 1
            released_routes.add((player_id, team_id))



def _match_transfers_statefully(
    transfers,
    matcher: NameMatcher,
    threshold: float,
    team_player_map: dict[int, list[int]],
    club_ids: set[int],
    historical_entries: list[dict] | None = None,
    club_identity: ClubIdentityIndex | None = None,
    squad_snapshots: tuple[SquadSnapshot, ...] | list[SquadSnapshot] = (),
    fotmob_identity_map: Mapping[
        int,
        tuple[tuple[int, str, SquadMember], ...],
    ]
    | None = None,
    player_names: dict[int, str] | None = None,
    allow_uncovered_source: bool = False,
    team_shirt_numbers: Mapping[int, Mapping[int, int]] | None = None,
    report: PlanningReport | None = None,
) -> list[MatchedTransfer]:
    """Match transfer events and derive releases from complete live squads.

    ``report`` (optional) receives live-squad membership, the final FotMob
    player identity map, live-squad skips, and matching-stage skip reasons.
    """
    report = report if report is not None else PlanningReport()
    virtual_rosters = {
        team_id: list(player_ids)
        for team_id, player_ids in team_player_map.items()
    }
    loaned_by_parent: dict[int, set[int]] = {}
    prior_permanent_routes: dict[
        tuple[int, int], list[tuple[int, datetime]]
    ] = {}
    fotmob_identity_candidates: dict[int, set[int]] = {}
    fotmob_identity_names: dict[int, str] = {}

    history = sorted(
        historical_entries or [],
        key=lambda entry: parse_iso_datetime(
            str(entry.get("transfer_date") or entry.get("timestamp") or "")
        ) or parse_iso_datetime("2000-01-01"),
    )
    for entry in history:
        player_id = _optional_positive_int(entry.get("player_id"))
        fotmob_player_id = _optional_positive_int(entry.get("fotmob_player_id"))
        if player_id and fotmob_player_id:
            fotmob_identity_candidates.setdefault(fotmob_player_id, set()).add(
                player_id
            )
            if entry.get("player_name"):
                fotmob_identity_names[fotmob_player_id] = str(entry["player_name"])
        source = _optional_positive_int(entry.get("from_team_id"))
        if not player_id or not source:
            continue
        transfer_type = str(entry.get("transfer_type", "")).lower()
        destination = _optional_positive_int(entry.get("to_team_id"))
        event_datetime = parse_iso_datetime(
            str(entry.get("transfer_date") or entry.get("timestamp") or "")
        )
        if transfer_type == "loan":
            loaned_by_parent.setdefault(source, set()).add(player_id)
        else:
            loaned_by_parent.get(source, set()).discard(player_id)
            if (
                destination
                and event_datetime is not None
                and transfer_type not in {"end of loan", "shirt_number_update"}
            ):
                prior_permanent_routes.setdefault((player_id, source), []).append(
                    (destination, event_datetime)
                )

    (
        snapshot_fotmob_to_pes,
        snapshot_identity_names,
        identity_map,
        live_squad_ids,
    ) = _build_fotmob_identity_index(
        matcher,
        team_player_map,
        club_ids,
        threshold,
        club_identity,
        squad_snapshots,
        fotmob_identity_map,
        team_shirt_numbers,
    )
    report.live_squad_ids.update(live_squad_ids)
    fotmob_identity_names.update(snapshot_identity_names)
    fotmob_to_pes = {
        fotmob_player_id: next(iter(player_ids))
        for fotmob_player_id, player_ids in fotmob_identity_candidates.items()
        if len(player_ids) == 1
    }
    # The current live squad is fresher evidence than any logged identity: a
    # single bad log entry must not block a player forever.
    for fotmob_player_id, player_id in snapshot_fotmob_to_pes.items():
        logged = fotmob_identity_candidates.get(fotmob_player_id, set())
        if logged and logged != {player_id}:
            logger.warning(
                "FotMob player %s: live squad identity %s overrides transfer "
                "log identities %s",
                fotmob_player_id,
                player_id,
                sorted(logged),
            )
        fotmob_to_pes[fotmob_player_id] = player_id

    ordered_transfers = [
        transfer
        for _, transfer in sorted(
            enumerate(transfers),
            key=_transfer_event_order_key,
        )
    ]
    matched: list[MatchedTransfer] = []

    for transfer in ordered_transfers:
        transfer_datetime = parse_iso_datetime(
            str(getattr(transfer, "date", "") or "")
        )
        ftid, ftname, ftconf = _match_transfer_team(
            matcher,
            transfer.from_club,
            transfer.from_club_full_name,
            transfer.from_club_id_fotmob,
            club_identity,
        )
        ttid, ttname, ttconf = _match_transfer_team(
            matcher,
            transfer.to_club,
            transfer.to_club_full_name,
            transfer.to_club_id_fotmob,
            club_identity,
        )
        skip_reason: tuple[str, str, tuple[str, ...]] | None = None

        context_map = virtual_rosters
        parent_loaned = loaned_by_parent.get(ftid, set()) if ftid is not None else set()
        if ftid is not None and parent_loaned:
            context_map = dict(virtual_rosters)
            context_map[ftid] = list(
                dict.fromkeys(virtual_rosters.get(ftid, []) + list(parent_loaned))
            )

        pid, pname, pconf = matcher.match_player(
            transfer.player_name,
            threshold=threshold,
            from_team_id=ftid,
            to_team_id=ttid,
            team_player_map=context_map,
            position=transfer.position,
            nationality=transfer.nationality,
            age=transfer.age,
        )
        fotmob_player_id = _optional_positive_int(transfer.player_id_fotmob)
        snapshot_known_pid = (
            snapshot_fotmob_to_pes.get(fotmob_player_id)
            if fotmob_player_id is not None
            else None
        )
        known_pid = (
            fotmob_to_pes.get(fotmob_player_id)
            if fotmob_player_id is not None
            else None
        )
        if (
            transfer.transfer_type == "shirt_number_update"
            and snapshot_known_pid is not None
        ):
            # The complete current-squad snapshot is the authoritative
            # provider identity for its own shirt observations. A stale
            # historical route must not make the number update ambiguous.
            pid = snapshot_known_pid
            pname = snapshot_identity_names.get(
                fotmob_player_id, transfer.player_name
            )
            pconf = 100.0
        elif known_pid is not None and pid is None:
            if transfer.infer_from_current_roster:
                logger.warning(
                    "FotMob player %s has no independently matched PES identity; "
                    "ignoring stale history for destination-only registration",
                    transfer.player_id_fotmob,
                )
            elif (
                (transfer.position or transfer.age)
                and not matcher._is_player_metadata_compatible(
                    known_pid,
                    position=transfer.position,
                    age=transfer.age,
                )
            ):
                logger.warning(
                    "Rejecting stale provider identity %s for %r: "
                    "metadata conflicts with PES player %s",
                    transfer.player_id_fotmob,
                    transfer.player_name,
                    known_pid,
                )
                pid, pname = None, ""
                skip_reason = (
                    "provider_identity_metadata_conflict",
                    f"FotMob identity maps to PES player {known_pid} whose "
                    "position/age contradicts this event",
                    (fotmob_identity_names.get(fotmob_player_id, ""),),
                )
            else:
                # Historical provider IDs remain the only evidence for
                # legitimate public-name changes when no metadata contradicts
                # the established identity.
                pid = known_pid
                pname = fotmob_identity_names.get(
                    fotmob_player_id, transfer.player_name
                )
                pconf = 100.0
        elif known_pid is not None and pid != known_pid:
            if known_pid == snapshot_known_pid:
                logger.warning(
                    "FotMob player %s: name matched PES %s but the live squad "
                    "identifies PES %s; using live squad identity",
                    transfer.player_id_fotmob,
                    pid,
                    known_pid,
                )
                pid = known_pid
                pname = fotmob_identity_names.get(
                    fotmob_player_id, transfer.player_name
                )
                pconf = 100.0
            else:
                event_rosters = {
                    player_id
                    for team_id in (ftid, ttid)
                    if team_id is not None and team_id >= 0
                    for player_id in context_map.get(team_id, ())
                }
                name_on_route = pid in event_rosters
                logged_on_route = known_pid in event_rosters
                if name_on_route and not logged_on_route:
                    logger.warning(
                        "FotMob player %s: transfer log identity %s is stale; "
                        "event clubs register name-matched PES %s",
                        transfer.player_id_fotmob,
                        known_pid,
                        pid,
                    )
                    fotmob_to_pes[fotmob_player_id] = pid
                    fotmob_identity_names[fotmob_player_id] = (
                        pname or transfer.player_name
                    )
                elif logged_on_route and not name_on_route:
                    pid = known_pid
                    pname = fotmob_identity_names.get(
                        fotmob_player_id, transfer.player_name
                    )
                    pconf = 100.0
                else:
                    logger.warning(
                        "FotMob player %s conflicts with PES history (%s vs %s); "
                        "no roster evidence decides",
                        transfer.player_id_fotmob,
                        known_pid,
                        pid,
                    )
                    skip_reason = (
                        "provider_identity_conflict",
                        f"transfer log maps FotMob player to PES {known_pid}, "
                        f"name matches PES {pid}",
                        tuple(
                            name
                            for name in (
                                fotmob_identity_names.get(fotmob_player_id, ""),
                                pname,
                            )
                            if name
                        ),
                    )
                    pid, pname = None, ""

        current_clubs = (
            [
                team_id
                for team_id, roster in virtual_rosters.items()
                if team_id in club_ids and pid in roster
            ]
            if pid is not None
            else []
        )
        current_team_id = current_clubs[0] if len(current_clubs) == 1 else None
        if ftid is None and transfer.infer_from_current_roster:
            inferred_team_id = current_team_id
            inferred_team_name = (
                matcher.get_team_name(inferred_team_id)
                if inferred_team_id is not None
                else ""
            )
            can_infer_source = (
                transfer.verification_status == "enabled"
                and bool(transfer.proof_urls)
                and pconf == 100.0
                and ttid is not None
                and ttid >= 0
                and inferred_team_id is not None
                and bool(inferred_team_name)
            )
            if can_infer_source:
                ftid = inferred_team_id
                ftname = inferred_team_name
                ftconf = 100.0
                transfer.from_club = ftname
                transfer.from_club_full_name = ftname
                logger.info(
                    "Inferred moderated transfer source from unique current roster: "
                    "%s (%s) -> %s",
                    transfer.player_name,
                    ftname,
                    transfer.to_club,
                )
            else:
                # A destination-only community signal must never degrade into a
                # generic free-agent signing or release when source inference is
                # ambiguous.
                ftid = UNRESOLVED_TEAM_ID
                ftname = ""
                ftconf = 0.0
                skip_reason = (
                    "source_not_inferable",
                    "destination-only signal needs a verified proof, an exact "
                    "player match, and one unique current save club",
                    tuple(
                        matcher.get_team_name(team_id) or f"Team {team_id}"
                        for team_id in current_clubs
                    ),
                )

        is_loan_transfer = (
            transfer.is_loan or transfer.transfer_type == "loan"
        )
        if (
            pid is not None
            and is_loan_transfer
            and ftid is not None
            and ftid >= 0
            and ttid is not None
            and ttid >= 0
            and current_team_id is not None
            and current_team_id != ftid
            and current_team_id != ttid
            and transfer_datetime is not None
            and any(
                destination_team_id == current_team_id
                and route_datetime <= transfer_datetime
                for destination_team_id, route_datetime in prior_permanent_routes.get(
                    (pid, ftid), []
                )
            )
        ):
            stale_source_id = ftid
            stale_source_name = ftname or transfer.from_club
            ftid = current_team_id
            ftname = matcher.get_team_name(current_team_id) or ftname
            ftconf = 100.0
            logger.info(
                "Reconciled stale loan source for %s: %s (%s) -> %s (%s)",
                transfer.player_name,
                stale_source_name,
                stale_source_id,
                ftname or "current roster",
                current_team_id,
            )

        if pid is not None and fotmob_player_id is not None:
            existing_pid = fotmob_to_pes.get(fotmob_player_id)
            if existing_pid is None:
                fotmob_to_pes[fotmob_player_id] = pid
                fotmob_identity_names[fotmob_player_id] = pname or transfer.player_name
        match = MatchedTransfer(
            transfer=transfer,
            player_id=pid,
            from_team_id=ftid,
            to_team_id=ttid,
            player_confidence=pconf,
            from_team_confidence=ftconf,
            to_team_confidence=ttconf,
            matched_player_name=pname,
            matched_from_team=ftname,
            matched_to_team=ttname,
        )
        matched.append(match)
        if skip_reason is not None:
            report.match_reasons[id(match)] = skip_reason

        if (
            pid is None
            or ftid == UNRESOLVED_TEAM_ID
            or ttid == UNRESOLVED_TEAM_ID
            or transfer.transfer_type == "shirt_number_update"
        ):
            continue

        if (
            ftid is not None
            and ttid is not None
            and current_team_id == ftid
            and transfer_datetime is not None
            and not is_loan_transfer
            and transfer.transfer_type != "end of loan"
        ):
            prior_permanent_routes.setdefault((pid, ftid), []).append(
                (ttid, transfer_datetime)
            )

        can_move_from_parent = (
            ftid is not None
            and pid in loaned_by_parent.get(ftid, set())
            and current_team_id is not None
        )
        live_at_destination = ttid is not None and pid in live_squad_ids.get(
            ttid, ()
        )

        # Mirror the plan's reconciliation so later events see the same state.
        if ftid is not None and ttid is not None:
            if current_team_id is not None and current_team_id != ttid and (
                current_team_id == ftid
                or can_move_from_parent
                or live_at_destination
            ):
                virtual_rosters[current_team_id].remove(pid)
                if pid not in virtual_rosters.setdefault(ttid, []):
                    virtual_rosters[ttid].append(pid)
            elif not current_clubs:
                virtual_rosters.setdefault(ttid, []).append(pid)
        elif ftid is None and ttid is not None:
            if not current_clubs:
                virtual_rosters.setdefault(ttid, []).append(pid)
            elif current_team_id is not None and current_team_id != ttid and (
                pid in loaned_by_parent.get(current_team_id, set())
                or live_at_destination
            ):
                virtual_rosters[current_team_id].remove(pid)
                if pid not in virtual_rosters.setdefault(ttid, []):
                    virtual_rosters[ttid].append(pid)
        elif ftid is not None and ttid is None and current_team_id == ftid:
            virtual_rosters[ftid].remove(pid)

        if is_loan_transfer and ftid is not None:
            loaned_by_parent.setdefault(ftid, set()).add(pid)
        elif ftid is not None:
            loaned_by_parent.get(ftid, set()).discard(pid)

    snapshot_coverage = _build_snapshot_roster_coverage(
        matcher,
        threshold,
        virtual_rosters,
        club_ids,
        squad_snapshots,
        fotmob_to_pes,
        club_identity,
        team_shirt_numbers,
    )
    _append_current_squad_moves(
        matched,
        matcher,
        virtual_rosters,
        club_ids,
        identity_map,
        snapshot_fotmob_to_pes,
        snapshot_identity_names,
        club_identity,
        snapshot_coverage,
        allow_uncovered_source=allow_uncovered_source,
        skipped=report.skipped,
    )
    _append_current_squad_releases(
        matched,
        virtual_rosters,
        snapshot_coverage,
        player_names,
    )
    report.fotmob_player_ids.update(fotmob_to_pes)
    return matched


def _decide_roster_action(
    current_team_id: int | None,
    from_team_id: int | None,
    to_team_id: int | None,
    transfer_type: str,
    reconcilable_team_ids: frozenset[int] = frozenset(),
) -> tuple[str, str]:
    """Choose a roster mutation from the verified current state.

    Returns ``(action, reason)``; ``reason`` is a code, set for every skip.
    ``reconcilable_team_ids`` are clubs other than the named source from
    which evidence (an earlier loan, the destination's live squad, or a later
    event from the destination) authorizes moving the player.
    """
    if transfer_type == "shirt_number_update":
        if to_team_id is not None and current_team_id == to_team_id:
            return "shirt_update", ""
        return "skip", "shirt_player_not_at_club"

    if from_team_id is not None and to_team_id is not None:
        if current_team_id == to_team_id:
            return "noop", ""
        if (
            current_team_id == from_team_id
            or current_team_id in reconcilable_team_ids
        ):
            return "move", ""
        if current_team_id is None:
            # Unattached in the save (e.g. an earlier loan outside the save
            # released him): the known destination is still authoritative.
            return "add", ""
        return "skip", "current_club_mismatch"

    if from_team_id is None and to_team_id is not None:
        if current_team_id == to_team_id:
            return "noop", ""
        if current_team_id is None:
            return "add", ""
        if current_team_id in reconcilable_team_ids:
            return "move", ""
        return "skip", "already_registered_elsewhere"

    if from_team_id is not None and to_team_id is None:
        if current_team_id == from_team_id:
            return "release", ""
        if current_team_id is None:
            return "noop", ""
        return "skip", "current_club_mismatch"

    return "skip", "outside_save"


def _build_superseded_loan_sources(
    matches: list[MatchedTransfer],
    historical_entries: list[dict] | None = None,
) -> dict[int, frozenset[int]]:
    """Authorize moves from the club a feed-omitted loan return left stale.

    Transfer feeds commonly omit the synthetic loan-return event. For example,
    PSG -> Tottenham (loan) followed by PSG -> Juventus (permanent) leaves a
    current PES roster at Tottenham even though the newer event names PSG as
    its source. Only a strictly earlier loan from that same parent club can
    authorize the stale loan club as the actual move source.

    Likewise, for an event from a club outside the save, a strictly earlier
    loan from a save club to a club outside the save authorizes moving the
    player from that parent club, where the save still registers him.
    """
    prior_loans: dict[int, list[tuple[int, int | None, datetime]]] = {}
    allowed_sources: dict[int, frozenset[int]] = {}

    for entry in historical_entries or []:
        if str(entry.get("transfer_type", "")).lower() != "loan":
            continue
        try:
            player_id = int(entry.get("player_id") or 0)
            parent_team_id = int(entry.get("from_team_id") or 0)
            loan_team_id = int(entry.get("to_team_id") or 0)
        except (TypeError, ValueError):
            continue
        transfer_date = parse_iso_datetime(
            str(entry.get("transfer_date") or entry.get("timestamp") or "")
        )
        if player_id and parent_team_id > 0 and transfer_date:
            prior_loans.setdefault(player_id, []).append(
                (parent_team_id, loan_team_id if loan_team_id > 0 else None, transfer_date)
            )

    for match in matches:
        player_id = match.player_id
        transfer_date = parse_iso_datetime(match.transfer.date)
        if (
            player_id is not None
            and match.from_team_id is not None
            and match.from_team_id >= 0
            and match.to_team_id != UNRESOLVED_TEAM_ID
            and transfer_date is not None
            and (match.transfer.is_loan or match.transfer.transfer_type == "loan")
        ):
            prior_loans.setdefault(player_id, []).append(
                (match.from_team_id, match.to_team_id, transfer_date)
            )

    for match in matches:
        player_id = match.player_id
        transfer_date = parse_iso_datetime(match.transfer.date)
        if (
            player_id is None
            or match.to_team_id is None
            or match.to_team_id < 0
            or transfer_date is None
        ):
            continue
        loans = prior_loans.get(player_id, [])
        if match.from_team_id is None:
            allowed_sources[id(match)] = frozenset(
                parent_team_id
                for parent_team_id, loan_team_id, loan_date in loans
                if loan_team_id is None
                and parent_team_id != match.to_team_id
                and loan_date < transfer_date
            )
        else:
            allowed_sources[id(match)] = frozenset(
                loan_team_id
                for parent_team_id, loan_team_id, loan_date in loans
                if parent_team_id == match.from_team_id
                and loan_team_id is not None
                and loan_team_id != match.to_team_id
                and loan_date < transfer_date
            )

    return allowed_sources


def _unmatched_skip_reason(match: MatchedTransfer) -> tuple[str, str]:
    """Explain why matching could not produce an actionable event."""
    transfer = match.transfer
    if match.player_id is None:
        fotmob_id = _optional_positive_int(transfer.player_id_fotmob)
        return "player_not_matched", (
            f"no unique save player matches {transfer.player_name!r}"
            + (f" (FotMob {fotmob_id})" if fotmob_id is not None else "")
        )
    if match.from_team_id == UNRESOLVED_TEAM_ID:
        return "source_team_not_matched", (
            f"club {transfer.from_club_full_name or transfer.from_club!r}"
            f" (FotMob {transfer.from_club_id_fotmob}) is not confidently "
            "resolved in the save"
        )
    if match.to_team_id == UNRESOLVED_TEAM_ID:
        return "destination_team_not_matched", (
            f"club {transfer.to_club_full_name or transfer.to_club!r}"
            f" (FotMob {transfer.to_club_id_fotmob}) is not confidently "
            "resolved in the save"
        )
    return "outside_save", "neither club is in the save"


def _plan_roster_actions(
    matches: list[MatchedTransfer],
    all_rosters: dict,
    club_ids: set[int],
    edit_file: EditFile,
    superseded_loan_sources: dict[int, frozenset[int]],
    allow_overflow_release: bool = True,
    report: PlanningReport | None = None,
) -> list[PlannedRosterAction]:
    """Build one chronological roster plan and simulate every accepted action.

    A move whose named source disagrees with the save is reconciled from the
    player's actual club when the destination's live squad, a later event
    from the destination, or an earlier loan proves where he belongs. A
    departure that would leave a club below the roster minimum is deferred
    to the end of the plan and applied only if the plan backfilled the club.
    Every skipped transfer is recorded in ``report.skipped``.
    """
    report = report if report is not None else PlanningReport()
    live_squad_ids = report.live_squad_ids
    rosters = {
        team_id: list(roster.player_ids)
        for team_id, roster in all_rosters.items()
        if team_id in club_ids
    }
    player_clubs: dict[int, set[int]] = {}
    for team_id, player_ids in rosters.items():
        for player_id in player_ids:
            if player_id:
                player_clubs.setdefault(player_id, set()).add(team_id)

    team_names: dict[int, str] = {}
    for match in matches:
        for team_id, name in (
            (match.from_team_id, match.matched_from_team),
            (match.to_team_id, match.matched_to_team),
        ):
            if team_id is not None and team_id >= 0 and name:
                team_names.setdefault(team_id, name)

    def team_label(team_id: int) -> str:
        name = team_names.get(team_id)
        return f"{name} ({team_id})" if name else f"club {team_id}"

    # Later events proving where each player went: a later event leaving
    # club X, or a live shirt observation at X, shows he reached X.
    later_evidence: dict[int, list[tuple[int, int]]] = {}
    for index, match in enumerate(matches):
        if match.player_id is None or not match.is_fully_matched:
            continue
        if match.transfer.transfer_type == "shirt_number_update":
            evidence_team_id = match.to_team_id
        else:
            evidence_team_id = match.from_team_id
        if evidence_team_id is not None and evidence_team_id >= 0:
            later_evidence.setdefault(match.player_id, []).append(
                (index, evidence_team_id)
            )

    def has_destination_evidence(index: int, match: MatchedTransfer) -> bool:
        destination = match.to_team_id
        if destination is None or destination < 0:
            return False
        if match.player_id in live_squad_ids.get(destination, ()):
            return True
        return any(
            later_index > index and team_id == destination
            for later_index, team_id in later_evidence.get(match.player_id, ())
        )

    planned: list[PlannedRosterAction] = []
    transferred_in_plan: set[int] = set()
    # Players a later plan step releases from a club. A full club drops one
    # of them before any player the club still wants.
    pending_releases: dict[int, set[int]] = {}
    for match in matches:
        if match.is_release:
            pending_releases.setdefault(match.from_team_id, set()).add(
                match.player_id
            )

    def remove_from_roster(team_id: int, player_id: int) -> None:
        # Leave a hole instead of compacting like the editor does: every
        # remaining player keeps the slot, and so the game-plan role, that
        # overflow ranking reads from the unmodified live file.
        rosters[team_id][rosters[team_id].index(player_id)] = 0
        player_clubs.get(player_id, set()).discard(team_id)

    def add_to_roster(team_id: int, player_id: int) -> None:
        roster = rosters[team_id]
        roster[roster.index(0)] = player_id
        player_clubs.setdefault(player_id, set()).add(team_id)

    def select_overflow_candidate(
        team_id: int, incoming_player_id: int
    ) -> tuple[int, set[int]]:
        roster_ids = {pid for pid in rosters[team_id] if pid}
        # Current live-squad members are never the overflow casualty.
        live_protected = transferred_in_plan | (
            set(live_squad_ids.get(team_id, ())) & roster_ids
        )
        releasable = {
            pid
            for pid in pending_releases.get(team_id, set()) & roster_ids
            if pid != incoming_player_id
            and pid not in live_protected
            and player_clubs.get(pid) == {team_id}
        }
        if releasable:
            protected = live_protected | (roster_ids - releasable)
            _, candidate = edit_file.find_overflow_release_candidate(
                team_id,
                exclude_player_id=incoming_player_id,
                roster_player_ids=rosters[team_id],
                protected_player_ids=protected,
            )
            if candidate in releasable:
                return candidate, protected
        _, candidate = edit_file.find_overflow_release_candidate(
            team_id,
            exclude_player_id=incoming_player_id,
            roster_player_ids=rosters[team_id],
            protected_player_ids=live_protected,
        )
        return candidate, live_protected

    def plan_match(
        index: int, match: MatchedTransfer, *, may_defer: bool
    ) -> PlannedRosterAction | None:
        """Simulate one match; None means deferred for the roster minimum."""
        if not match.is_fully_matched:
            reason, detail, _ = report.match_reasons.get(id(match), (
                *_unmatched_skip_reason(match),
                (),
            ))
            return PlannedRosterAction(match, "skip", None, reason, detail=detail)

        player_id = match.player_id
        current_clubs = sorted(player_clubs.get(player_id, set()))
        if len(current_clubs) > 1:
            return PlannedRosterAction(
                match,
                "skip",
                None,
                "duplicate_registration",
                detail="save registers player at "
                + ", ".join(team_label(team_id) for team_id in current_clubs),
            )

        current_team_id = current_clubs[0] if current_clubs else None
        reconcilable = superseded_loan_sources.get(id(match), frozenset())
        if (
            current_team_id is not None
            and current_team_id not in (match.from_team_id, match.to_team_id)
            and match.transfer.transfer_type != "shirt_number_update"
            and has_destination_evidence(index, match)
        ):
            reconcilable = reconcilable | {current_team_id}
        action, reason = _decide_roster_action(
            current_team_id,
            match.from_team_id,
            match.to_team_id,
            match.transfer.transfer_type,
            reconcilable,
        )
        detail = (
            f"save registers player at {team_label(current_team_id)}"
            if reason and current_team_id is not None
            else ""
        )
        item = PlannedRosterAction(match, action, current_team_id, reason, detail=detail)
        if action == "move" and current_team_id != match.from_team_id:
            logger.info(
                "Reconciled %s from actual save club %s (event source %s)",
                match.transfer.player_name,
                current_team_id,
                match.from_team_id,
            )
        if (
            action in {"move", "release"}
            and current_team_id in rosters
            and sum(player_id != 0 for player_id in rosters[current_team_id])
            <= MIN_CLUB_ROSTER_SIZE
        ):
            if may_defer:
                return None
            item.action = "skip"
            item.reason = "roster_minimum"
            item.detail = (
                f"{team_label(current_team_id)} would drop below "
                f"{MIN_CLUB_ROSTER_SIZE} players and this run signs no replacement"
            )

        if item.action in {"move", "add"}:
            destination = match.to_team_id
            if destination is None or destination not in rosters:
                item.action = "skip"
                item.reason = "destination_roster_missing"
            elif 0 not in rosters[destination]:
                if not allow_overflow_release:
                    item.action = "skip"
                    item.reason = "destination_roster_full"
                else:
                    overflow_player_id, protected_ids = select_overflow_candidate(
                        destination, player_id
                    )
                    if not overflow_player_id:
                        item.action = "skip"
                        item.reason = "no_safe_overflow_candidate"
                        item.detail = (
                            f"{team_label(destination)} is full and every "
                            "player is protected"
                        )
                    else:
                        item.overflow_player_id = overflow_player_id
                        describe = getattr(
                            edit_file,
                            "describe_overflow_release_candidate",
                            None,
                        )
                        if callable(describe):
                            item.overflow_details = describe(
                                destination,
                                overflow_player_id,
                                roster_player_ids=rosters[destination],
                                protected_player_ids=protected_ids,
                            )
                        remove_from_roster(destination, overflow_player_id)

        if item.action == "move":
            if current_team_id is None or current_team_id not in rosters:
                item.action = "skip"
                item.reason = "source_roster_missing"
            else:
                remove_from_roster(current_team_id, player_id)
                add_to_roster(match.to_team_id, player_id)
                transferred_in_plan.add(player_id)
        elif item.action == "add":
            add_to_roster(match.to_team_id, player_id)
            transferred_in_plan.add(player_id)
        elif item.action == "release" and current_team_id is not None:
            remove_from_roster(current_team_id, player_id)
        return item

    # Matches waiting for the roster minimum, in order; a deferred player's
    # later events wait too so his own history stays chronological.
    deferred: list[tuple[int, MatchedTransfer]] = []
    deferred_players: set[int] = set()
    for index, match in enumerate(matches):
        if match.is_release:
            pending_releases.get(match.from_team_id, set()).discard(
                match.player_id
            )
        if match.player_id is not None and match.player_id in deferred_players:
            deferred.append((index, match))
            continue
        item = plan_match(index, match, may_defer=True)
        if item is None:
            deferred.append((index, match))
            deferred_players.add(match.player_id)
            continue
        planned.append(item)

    while deferred:
        waiting: list[tuple[int, MatchedTransfer]] = []
        blocked_players: set[int] = set()
        for index, match in deferred:
            if match.player_id in blocked_players:
                waiting.append((index, match))
                continue
            item = plan_match(index, match, may_defer=True)
            if item is None:
                waiting.append((index, match))
                blocked_players.add(match.player_id)
                continue
            planned.append(item)
        if len(waiting) == len(deferred):
            for index, match in waiting:
                planned.append(plan_match(index, match, may_defer=False))
            break
        deferred = waiting

    for item in planned:
        if (
            item.action == "skip"
            and item.match.transfer.transfer_type != "shirt_number_update"
        ):
            _, _, candidates = report.match_reasons.get(
                id(item.match), ("", "", ())
            )
            report.skipped.append(
                SkippedTransfer.from_match(
                    item.match,
                    item.reason,
                    item.detail,
                    candidates=candidates,
                    club_ids=club_ids,
                )
            )
    return planned


def _dedupe_shirt_number_matches(
    matches: list[MatchedTransfer],
) -> tuple[list[MatchedTransfer], int]:
    """Keep one fail-closed shirt-number observation per player and club."""
    regular: list[MatchedTransfer] = []
    groups: dict[tuple[int, int], list[MatchedTransfer]] = {}

    for match in matches:
        if (
            match.transfer.transfer_type != "shirt_number_update"
            or match.player_id is None
            or match.to_team_id is None
        ):
            regular.append(match)
            continue
        groups.setdefault((match.player_id, match.to_team_id), []).append(match)

    skipped = 0
    for group in groups.values():
        ranked = sorted(group, key=lambda item: item.min_confidence, reverse=True)
        winner = ranked[0]
        conflicting = [
            item
            for item in ranked[1:]
            if item.transfer.shirt_number != winner.transfer.shirt_number
        ]
        if conflicting and winner.min_confidence - conflicting[0].min_confidence < 3.0:
            skipped += len(group)
            continue
        regular.append(winner)
        skipped += len(group) - 1

    return regular, skipped
