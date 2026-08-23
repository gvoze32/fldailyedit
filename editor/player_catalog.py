"""Load and validate an optional external Football Life player-name catalog."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from editor.models import PlayerInfo


MIN_CURRENT_PLAYER_CATALOG_SIZE = 25_000


class PlayerCatalogError(RuntimeError):
    """Raised when an explicitly supplied player catalog is invalid or incomplete."""


@dataclass(frozen=True)
class PlayerCatalogReport:
    current_entries: int
    legacy_roster_fallbacks: int
    edited_entries: int
    roster_entries: int
    missing_roster_ids: tuple[int, ...]
    positions: int
    nationalities: int
    ages: int
    overall_ratings: int



def load_id_name_text(
    path: Path,
    *,
    label: str,
    minimum_entries: int = 1,
) -> dict[int, str]:
    """Read strict ``ID - Name`` reference data without accepting partial files."""
    path = Path(path)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise PlayerCatalogError(f"Could not read {label} catalog {path}: {exc}") from exc

    entries: dict[int, str] = {}
    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line:
            continue
        if "-" not in line:
            raise PlayerCatalogError(
                f"Malformed {label} catalog line {line_number} in {path}"
            )
        raw_id, raw_name = line.split("-", 1)
        raw_id = raw_id.strip()
        name = raw_name.strip()
        if not raw_id.isdigit() or not name:
            raise PlayerCatalogError(
                f"Malformed {label} catalog line {line_number} in {path}"
            )
        item_id = int(raw_id)
        previous = entries.get(item_id)
        if previous is not None and previous != name:
            raise PlayerCatalogError(
                f"Conflicting {label} names for ID {item_id}: {previous!r} vs {name!r}"
            )
        entries[item_id] = name

    if len(entries) < minimum_entries:
        raise PlayerCatalogError(
            f"{label.title()} catalog {path} has only {len(entries):,} entries; "
            f"expected at least {minimum_entries:,}"
        )
    return entries


def _load_legacy_roster_fallbacks(
    csv_path: Path | None,
    roster_ids: set[int],
    known_ids: set[int],
) -> dict[int, PlayerInfo]:
    """Recover rostered IDs missing from the current reference, never stale free agents."""
    if csv_path is None or not Path(csv_path).exists():
        return {}

    fallbacks: dict[int, PlayerInfo] = {}
    try:
        with Path(csv_path).open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames or not {"PlayerID", "PlayerName"}.issubset(
                reader.fieldnames
            ):
                raise PlayerCatalogError(
                    f"Legacy player CSV {csv_path} requires PlayerID and PlayerName columns"
                )
            for row in reader:
                raw_id = str(row.get("PlayerID") or "").strip()
                name = str(row.get("PlayerName") or "").strip()
                if not raw_id.isdigit() or not name:
                    continue
                player_id = int(raw_id)
                if player_id in roster_ids and player_id not in known_ids:
                    fallbacks[player_id] = PlayerInfo(player_id, name, name)
    except OSError as exc:
        raise PlayerCatalogError(f"Could not read legacy player CSV {csv_path}: {exc}") from exc
    return fallbacks


def build_player_catalog(
    *,
    current_path: Path | None,
    legacy_csv_path: Path | None,
    edited_players: dict[int, PlayerInfo],
    roster_ids: set[int],
) -> tuple[dict[int, PlayerInfo], PlayerCatalogReport]:
    """Build a player catalog, optionally using an external current reference."""
    current_names = (
        load_id_name_text(
            current_path,
            label="player",
            minimum_entries=MIN_CURRENT_PLAYER_CATALOG_SIZE,
        )
        if current_path is not None
        else {}
    )
    players = {
        player_id: PlayerInfo(player_id, name, name)
        for player_id, name in current_names.items()
    }
    fallbacks = _load_legacy_roster_fallbacks(
        legacy_csv_path, roster_ids, set(players)
    )
    players.update(fallbacks)
    for player_id, edited_player in edited_players.items():
        existing = players.get(player_id)
        if existing is None:
            players[player_id] = edited_player
            continue

        # The versioned FL26 reference is authoritative for names. Edited
        # records embedded in a save are often abbreviated, but may eventually
        # provide useful metadata that the name-only reference does not.
        players[player_id] = PlayerInfo(
            player_id=player_id,
            name=existing.name,
            print_name=existing.print_name or edited_player.print_name,
            overall_rating=(
                edited_player.overall_rating or existing.overall_rating
            ),
            position=edited_player.position or existing.position,
            nationality=edited_player.nationality or existing.nationality,
            age=edited_player.age or existing.age,
            position_proficiency=(
                edited_player.position_proficiency
                or existing.position_proficiency
            ),
        )

    missing = tuple(sorted(roster_ids - set(players)))
    report = PlayerCatalogReport(
        current_entries=len(current_names),
        legacy_roster_fallbacks=len(fallbacks),
        edited_entries=len(edited_players),
        roster_entries=len(roster_ids),
        missing_roster_ids=missing,
        positions=sum(
            bool(players.get(player_id) and players[player_id].position)
            for player_id in roster_ids
        ),
        nationalities=sum(
            bool(players.get(player_id) and players[player_id].nationality)
            for player_id in roster_ids
        ),
        ages=sum(
            bool(players.get(player_id) and players[player_id].age)
            for player_id in roster_ids
        ),
        overall_ratings=sum(
            bool(players.get(player_id) and players[player_id].overall_rating)
            for player_id in roster_ids
        ),
    )
    if current_path is not None and missing:
        preview = ", ".join(str(player_id) for player_id in missing[:10])
        raise PlayerCatalogError(
            f"Player catalog misses {len(missing):,} roster IDs (first: {preview})"
        )
    return players, report
