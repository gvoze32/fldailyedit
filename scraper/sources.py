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


def _same_destination(left: Transfer, right: Transfer) -> bool:
    left_name = _normalize(left.to_club_full_name or left.to_club)
    right_name = _normalize(right.to_club_full_name or right.to_club)
    if not left_name or not right_name:
        return False
    return left_name == right_name or fuzz.token_set_ratio(left_name, right_name) >= 92


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
    if target.transfer_type == "transfer" and source.transfer_type != "transfer":
        target.transfer_type = source.transfer_type
        target.is_loan = source.is_loan


def reconcile_transfer_sources(
    verified_batches: list[list[Transfer]],
    fast_signals: list[Transfer] | None = None,
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

    logger.info(
        "Cross-source reconciliation: %s corroborated, %s roster-inference candidates, "
        "%s ambiguous signals ignored",
        corroborated_signals,
        inferred_signals,
        ambiguous_signals,
    )
    return verified
