"""Cross-source reconciliation for normalized transfer events."""

from __future__ import annotations

import logging
import unicodedata

from rapidfuzz import fuzz

from scraper.fotmob import merge_transfers, parse_iso_date
from scraper.models import Transfer


logger = logging.getLogger(__name__)


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value or "")
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(plain.casefold().replace(".", " ").split())


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
    for attr in ("position", "fee", "nationality", "age"):
        if not getattr(target, attr) and getattr(source, attr):
            setattr(target, attr, getattr(source, attr))
    if target.transfer_type == "transfer" and source.transfer_type != "transfer":
        target.transfer_type = source.transfer_type
        target.is_loan = source.is_loan


def reconcile_transfer_sources(
    verified_batches: list[list[Transfer]],
    fast_signals: list[Transfer] | None = None,
    corroborators: list[Transfer] | None = None,
) -> list[Transfer]:
    """
    Merge complete routes, then reconcile destination-only community signals.

    A uniquely corroborated Sortitoutsi signal enriches an existing event. An
    unmatched enabled signal is retained, but it carries an explicit request
    for fail-closed source inference from the current FL26 roster.
    """
    verified = merge_transfers(verified_batches)
    inferred_signals = 0
    corroborated_signals = 0
    ambiguous_signals = 0
    corroborated_routes = 0
    ignored_routes = 0

    for signal in fast_signals or []:
        candidates = [
            transfer
            for transfer in verified
            if _normalize(transfer.player_name) == _normalize(signal.player_name)
            and _same_destination(transfer, signal)
            and _same_or_adjacent_date(transfer.date, signal.date)
            and _compatible_event_type(transfer, signal)
        ]
        if len(candidates) == 1:
            _merge_provenance(candidates[0], signal)
            corroborated_signals += 1
        elif not candidates:
            verified.append(signal)
            inferred_signals += 1
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
            if _normalize(transfer.player_name)
            == _normalize(corroborator.player_name)
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
        "%s roster-inference candidates, %s ambiguous signals ignored, "
        "%s complete routes corroborated, %s route corroborators ignored",
        corroborated_signals,
        inferred_signals,
        ambiguous_signals,
        corroborated_routes,
        ignored_routes,
    )
    return verified
