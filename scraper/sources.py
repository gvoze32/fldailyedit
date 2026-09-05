"""Cross-source reconciliation for normalized transfer events."""

from __future__ import annotations

import logging
import unicodedata

from rapidfuzz import fuzz

from scraper.fotmob import merge_transfers, parse_iso_date
from scraper.models import Transfer


logger = logging.getLogger(__name__)
_NON_CLUB_LABELS = {
    "",
    "free agent",
    "without club",
    "unattached",
    "career break",
    "retired",
}


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value or "")
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(plain.casefold().replace(".", " ").split())


def _is_non_club(value: str) -> bool:
    return _normalize(value) in _NON_CLUB_LABELS


def _same_or_adjacent_date(left: str, right: str) -> bool:
    left_date = parse_iso_date(left)
    right_date = parse_iso_date(right)
    if left_date is None or right_date is None:
        return True
    return abs((left_date - right_date).days) <= 1


def _same_club_name(left_name: str, right_name: str) -> bool:
    left_key = _normalize(left_name)
    right_key = _normalize(right_name)
    if not left_key or not right_key:
        return False
    return (
        left_key == right_key
        or fuzz.token_set_ratio(left_key, right_key) >= 92
    )

def _same_player_name(left_name: str, right_name: str) -> bool:
    left_key = _normalize(left_name)
    right_key = _normalize(right_name)
    if not left_key or not right_key:
        return False
    return (
        left_key == right_key
        or fuzz.token_sort_ratio(left_key, right_key) >= 95
    )


def _same_destination(left: Transfer, right: Transfer) -> bool:
    return _same_club_name(
        left.to_club_full_name or left.to_club,
        right.to_club_full_name or right.to_club,
    )


def _same_source(left: Transfer, right: Transfer) -> bool:
    return _same_club_name(
        left.from_club_full_name or left.from_club,
        right.from_club_full_name or right.from_club,
    )


def _compatible_source(left: Transfer, right: Transfer) -> bool:
    return _same_source(left, right) or (
        left.transfer_type == right.transfer_type == "free transfer"
    )


def _compatible_event_type(left: Transfer, right: Transfer) -> bool:
    if left.transfer_type == right.transfer_type:
        return True
    return "transfer" in {left.transfer_type, right.transfer_type}


def _merge_provenance(target: Transfer, source: Transfer) -> None:
    target.sources = tuple(dict.fromkeys((*target.sources, *source.sources)))
    target.source_urls = tuple(
        dict.fromkeys((*target.source_urls, *source.source_urls))
    )
    target.proof_urls = tuple(dict.fromkeys((*target.proof_urls, *source.proof_urls)))
    target_source = target.from_club_full_name or target.from_club
    source_source = source.from_club_full_name or source.from_club
    if (
        target.transfer_type == source.transfer_type == "free transfer"
        and _is_non_club(target_source)
        and not _is_non_club(source_source)
    ):
        target.from_club = source.from_club
        target.from_club_full_name = source_source
    if target.player_id_sortitoutsi is None:
        target.player_id_sortitoutsi = source.player_id_sortitoutsi
    for attr in (
        "player_id_transfermarkt",
        "from_club_id_transfermarkt",
        "to_club_id_transfermarkt",
        "transfer_id_transfermarkt",
    ):
        if getattr(target, attr) is None:
            setattr(target, attr, getattr(source, attr))
    for attr in ("position", "fee", "nationality", "age", "market_value"):
        if not getattr(target, attr) and getattr(source, attr):
            setattr(target, attr, getattr(source, attr))
    if target.transfer_type == "transfer" and source.transfer_type != "transfer":
        target.transfer_type = source.transfer_type
        target.is_loan = source.is_loan


def _merge_verified_batches(
    verified_batches: list[list[Transfer]],
) -> list[Transfer]:
    merged: list[Transfer] = []
    for transfer in merge_transfers(verified_batches):
        candidates = [
            existing
            for existing in merged
            if _same_player_name(existing.player_name, transfer.player_name)
            and _compatible_source(existing, transfer)
            and _same_destination(existing, transfer)
            and _same_or_adjacent_date(existing.date, transfer.date)
            and _compatible_event_type(existing, transfer)
        ]
        if len(candidates) == 1:
            _merge_provenance(candidates[0], transfer)
        else:
            merged.append(transfer)
    return merged


def reconcile_transfer_sources(
    verified_batches: list[list[Transfer]],
    fast_signals: list[Transfer] | None = None,
    corroborators: list[Transfer] | None = None,
) -> list[Transfer]:
    """
    Merge complete routes, then reconcile destination-only community signals.

    Sortitoutsi signals may enrich or infer a route under their adapter's
    explicit-date rules. Other sources are corroboration-only: they can merge
    provenance into one verified event, but never create a new event.
    """
    verified = _merge_verified_batches(verified_batches)
    inferred_signals = 0
    corroborated_signals = 0
    ambiguous_signals = 0
    ignored_signals = 0
    corroborated_routes = 0
    ignored_routes = 0

    for signal in fast_signals or []:
        candidates = [
            transfer
            for transfer in verified
            if _same_player_name(transfer.player_name, signal.player_name)
            and _same_destination(transfer, signal)
            and _same_or_adjacent_date(transfer.date, signal.date)
            and _compatible_event_type(transfer, signal)
        ]
        if len(candidates) == 1:
            _merge_provenance(candidates[0], signal)
            corroborated_signals += 1
        elif not candidates and signal.infer_from_current_roster:
            verified.append(signal)
            inferred_signals += 1
        elif not candidates:
            ignored_signals += 1
        else:
            ambiguous_signals += 1
            logger.warning(
                "Ignoring ambiguous Sortitoutsi signal for %s -> %s",
                signal.player_name,
                signal.to_club,
            )

    for corroborator in corroborators or []:
        candidates = [
            transfer
            for transfer in verified
            if _same_player_name(transfer.player_name, corroborator.player_name)
            and _same_or_adjacent_date(transfer.date, corroborator.date)
            and _same_source(transfer, corroborator)
            and _same_destination(transfer, corroborator)
            and _compatible_event_type(transfer, corroborator)
        ]
        if len(candidates) == 1:
            _merge_provenance(candidates[0], corroborator)
            corroborated_routes += 1
        else:
            ignored_routes += 1
            if len(candidates) > 1:
                logger.warning(
                    "Ignoring ambiguous route corroborator for %s: %s -> %s",
                    corroborator.player_name,
                    corroborator.from_club,
                    corroborator.to_club,
                )

    logger.info(
        "Cross-source reconciliation: %s fast signals corroborated, "
        "%s roster-inference candidates, %s submission-only signals ignored, "
        "%s ambiguous signals ignored, %s complete routes corroborated, "
        "%s route corroborators ignored",
        corroborated_signals,
        inferred_signals,
        ignored_signals,
        ambiguous_signals,
        corroborated_routes,
        ignored_routes,
    )
    return verified
