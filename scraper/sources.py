"""Cross-source reconciliation for normalized transfer events."""

from __future__ import annotations

from collections import defaultdict
import logging
import unicodedata

from rapidfuzz import fuzz, process

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

_SOURCE_PRIORITY = {
    "fotmob": 0,
    "transfermarkt": 1,
    "wikipedia": 2,
}


def _source_priority(transfer: Transfer) -> int:
    """Return the strongest provenance rank carried by an event."""
    return min(
        (_SOURCE_PRIORITY.get(source.casefold(), 3) for source in transfer.sources),
        default=3,
    )


class _FuzzyKeyIndex:
    """Resolve normalized fuzzy keys once and reuse the matching key set."""

    def __init__(self, values, score_cutoff: int) -> None:
        choices: list[str] = []
        seen: set[str] = set()
        for value in values:
            key = _normalize(value)
            if key and key not in seen:
                choices.append(key)
                seen.add(key)
        self._choices = tuple(choices)
        self._score_cutoff = score_cutoff
        self._matches: dict[str, tuple[str, ...]] = {}

    def matching_keys(self, value: str) -> tuple[str, ...]:
        query = _normalize(value)
        if not query:
            return ()

        cached = self._matches.get(query)
        if cached is not None:
            return cached

        matches = tuple(
            choice
            for choice, _score, _index in process.extract(
                query,
                self._choices,
                scorer=fuzz.token_set_ratio,
                score_cutoff=self._score_cutoff,
                limit=None,
                processor=None,
            )
        )
        self._matches[query] = matches
        return matches


def _transfer_club_key(transfer: Transfer, *, source: bool) -> str:
    if source:
        return _normalize(transfer.from_club_full_name or transfer.from_club)
    return _normalize(transfer.to_club_full_name or transfer.to_club)


class _TransferCandidateIndex:
    """Index transfers by fuzzy-resolvable source and destination club keys."""

    def __init__(
        self,
        transfers: list[Transfer],
        extra_transfers: list[Transfer] | tuple[Transfer, ...] = (),
    ) -> None:
        club_values = [
            club
            for transfer in (*transfers, *extra_transfers)
            for club in (
                transfer.from_club_full_name or transfer.from_club,
                transfer.to_club_full_name or transfer.to_club,
            )
        ]
        self._club_index = _FuzzyKeyIndex(club_values, score_cutoff=92)
        self._by_route: dict[tuple[str, str], list[Transfer]] = defaultdict(list)
        self._by_destination: dict[str, list[Transfer]] = defaultdict(list)
        self._free_by_destination: dict[str, list[Transfer]] = defaultdict(list)
        self._locations: dict[int, tuple[str, str, bool]] = {}

        for transfer in transfers:
            self.add(transfer)

    @staticmethod
    def _remove_from_bucket(
        buckets: dict,
        key,
        transfer: Transfer,
    ) -> None:
        bucket = buckets.get(key)
        if not bucket:
            return
        for index, candidate in enumerate(bucket):
            if candidate is transfer:
                del bucket[index]
                break
        if not bucket:
            buckets.pop(key, None)

    def add(self, transfer: Transfer) -> None:
        """Add a transfer using its current route fields."""
        identity = id(transfer)
        if identity in self._locations:
            self.remove(transfer)

        source_key = _transfer_club_key(transfer, source=True)
        destination_key = _transfer_club_key(transfer, source=False)
        is_free_transfer = transfer.transfer_type == "free transfer"
        self._locations[identity] = (source_key, destination_key, is_free_transfer)

        if destination_key:
            self._by_destination[destination_key].append(transfer)
            if is_free_transfer:
                self._free_by_destination[destination_key].append(transfer)
        if source_key and destination_key:
            self._by_route[(source_key, destination_key)].append(transfer)

    def remove(self, transfer: Transfer) -> None:
        """Remove a transfer using the route fields captured when it was added."""
        location = self._locations.pop(id(transfer), None)
        if location is None:
            return
        source_key, destination_key, is_free_transfer = location
        if destination_key:
            self._remove_from_bucket(
                self._by_destination,
                destination_key,
                transfer,
            )
            if is_free_transfer:
                self._remove_from_bucket(
                    self._free_by_destination,
                    destination_key,
                    transfer,
                )
        if source_key and destination_key:
            self._remove_from_bucket(
                self._by_route,
                (source_key, destination_key),
                transfer,
            )

    def refresh(self, transfer: Transfer) -> None:
        """Refresh an indexed transfer after provenance enrichment."""
        self.remove(transfer)
        self.add(transfer)

    @staticmethod
    def _collect(
        buckets: dict,
        keys,
    ) -> list[Transfer]:
        collected: list[Transfer] = []
        seen: set[int] = set()
        for key in keys:
            for transfer in buckets.get(key, ()):
                identity = id(transfer)
                if identity in seen:
                    continue
                seen.add(identity)
                collected.append(transfer)
        return collected

    def destination_candidates(self, transfer: Transfer) -> list[Transfer]:
        destination_key = _transfer_club_key(transfer, source=False)
        if not destination_key:
            return []
        destination_keys = self._club_index.matching_keys(destination_key)
        return self._collect(self._by_destination, destination_keys)

    def route_candidates(self, transfer: Transfer) -> list[Transfer]:
        source_key = _transfer_club_key(transfer, source=True)
        destination_key = _transfer_club_key(transfer, source=False)
        if not destination_key:
            return []

        free_transfer = transfer.transfer_type == "free transfer"
        if not source_key and not free_transfer:
            return []

        destination_keys = self._club_index.matching_keys(destination_key)
        if not destination_keys:
            return []

        candidates: list[Transfer] = []
        seen: set[int] = set()
        if source_key:
            source_keys = self._club_index.matching_keys(source_key)
            for source_match in source_keys:
                for destination_match in destination_keys:
                    for candidate in self._by_route.get(
                        (source_match, destination_match),
                        (),
                    ):
                        identity = id(candidate)
                        if identity in seen:
                            continue
                        seen.add(identity)
                        candidates.append(candidate)

        if free_transfer:
            for candidate in self._collect(
                self._free_by_destination,
                destination_keys,
            ):
                identity = id(candidate)
                if identity in seen:
                    continue
                seen.add(identity)
                candidates.append(candidate)

        return candidates




def _same_route(left: Transfer, right: Transfer) -> bool:
    return _same_source(left, right) and _same_destination(left, right)


def _prefer_primary_routes(transfers: list[Transfer]) -> list[Transfer]:
    """Drop lower-trust routes that contradict a stronger same-day event."""
    groups: dict[tuple[str, object], list[Transfer]] = {}
    ungrouped: list[Transfer] = []
    for transfer in transfers:
        event_date = parse_iso_date(transfer.date)
        if event_date is None:
            ungrouped.append(transfer)
            continue
        groups.setdefault((_normalize(transfer.player_name), event_date), []).append(
            transfer
        )

    selected = list(ungrouped)
    for group in groups.values():
        distinct_routes: list[Transfer] = []
        for transfer in group:
            if not any(_same_route(transfer, route) for route in distinct_routes):
                distinct_routes.append(transfer)
        if len(distinct_routes) <= 1:
            selected.extend(group)
            continue

        strongest = min(_source_priority(transfer) for transfer in distinct_routes)
        preferred_routes = [
            transfer
            for transfer in distinct_routes
            if _source_priority(transfer) == strongest
        ]
        preferred = [
            transfer
            for transfer in group
            if any(_same_route(transfer, route) for route in preferred_routes)
        ]
        discarded = len(group) - len(preferred)
        if discarded:
            example = group[0]
            logger.warning(
                "Discarding %s lower-priority conflicting route(s) for %s on %s",
                discarded,
                example.player_name,
                parse_iso_date(example.date),
            )
        selected.extend(preferred)
    return selected


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
    incoming = merge_transfers(verified_batches)
    index = _TransferCandidateIndex([], incoming)
    for transfer in incoming:
        candidates = [
            existing
            for existing in index.route_candidates(transfer)
            if _same_player_name(existing.player_name, transfer.player_name)
            and _compatible_source(existing, transfer)
            and _same_destination(existing, transfer)
            and _same_or_adjacent_date(existing.date, transfer.date)
            and _compatible_event_type(existing, transfer)
        ]
        if len(candidates) == 1:
            target = candidates[0]
            _merge_provenance(target, transfer)
            index.refresh(target)
        else:
            merged.append(transfer)
            index.add(transfer)
    return _prefer_primary_routes(merged)


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
    fast_signals = fast_signals or []
    corroborators = corroborators or []
    index = _TransferCandidateIndex(
        verified,
        [*fast_signals, *corroborators],
    )

    for signal in fast_signals:
        candidates = [
            transfer
            for transfer in index.destination_candidates(signal)
            if _same_player_name(transfer.player_name, signal.player_name)
            and _same_destination(transfer, signal)
            and _same_or_adjacent_date(transfer.date, signal.date)
            and _compatible_event_type(transfer, signal)
        ]
        if len(candidates) == 1:
            target = candidates[0]
            _merge_provenance(target, signal)
            index.refresh(target)
            corroborated_signals += 1
        elif not candidates and signal.infer_from_current_roster:
            verified.append(signal)
            index.add(signal)
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

    for corroborator in corroborators:
        candidates = [
            transfer
            for transfer in index.route_candidates(corroborator)
            if _same_player_name(transfer.player_name, corroborator.player_name)
            and _same_or_adjacent_date(transfer.date, corroborator.date)
            and _same_source(transfer, corroborator)
            and _same_destination(transfer, corroborator)
            and _compatible_event_type(transfer, corroborator)
        ]
        if len(candidates) == 1:
            target = candidates[0]
            _merge_provenance(target, corroborator)
            index.refresh(target)
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
