"""
Transfer logging — structured JSONL output for audit trail and rollback.
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import config

logger = logging.getLogger(__name__)


def log_transfer(
    player_name: str,
    player_id: int,
    from_team: str,
    from_team_id: int,
    to_team: str,
    to_team_id: int,
    confidence: float = 0.0,
    transfer_type: str = "transfer",
    dry_run: bool = False,
):
    """
    Append a transfer record to the JSONL log file.

    Each line is a self-contained JSON object for easy parsing.
    """
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "player_name": player_name,
        "player_id": player_id,
        "from_team": from_team,
        "from_team_id": from_team_id,
        "to_team": to_team,
        "to_team_id": to_team_id,
        "confidence": round(confidence, 1),
        "transfer_type": transfer_type,
        "dry_run": dry_run,
    }

    log_file = config.TRANSFER_LOG_FILE
    log_file.parent.mkdir(parents=True, exist_ok=True)

    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    action = "DRY-RUN" if dry_run else "APPLIED"
    logger.info(
        f"[{action}] {player_name} (id={player_id}): "
        f"{from_team} → {to_team} (conf={confidence:.0f}%)"
    )


def read_log() -> list[dict]:
    """Read all transfer log entries."""
    log_file = config.TRANSFER_LOG_FILE
    if not log_file.exists():
        return []

    entries = []
    with open(log_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return entries


def print_summary(last_n: int = 20):
    """Print a human-readable summary of recent transfers."""
    entries = read_log()
    if not entries:
        print("No transfers logged yet.")
        return

    recent = entries[-last_n:]
    print(f"Last {len(recent)} transfers (of {len(entries)} total):")
    print("-" * 70)

    for e in recent:
        dry = " [DRY-RUN]" if e.get("dry_run") else ""
        ts = e.get("timestamp", "")[:19].replace("T", " ")
        print(
            f"  {ts}  {e['player_name']}: "
            f"{e['from_team']} → {e['to_team']} "
            f"(conf={e.get('confidence', 0):.0f}%){dry}"
        )
