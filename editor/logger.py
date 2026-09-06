"""
Save-change logging — structured JSONL output for audit trail and rollback.
"""
import json
import logging
from html import escape
from datetime import datetime, timezone
from pathlib import Path

import config

logger = logging.getLogger(__name__)
_REMOVED_TRANSFER_TYPES = frozenset({"player_spec_create", "player_spec_update"})


def _is_removed_feature_entry(entry: dict) -> bool:
    return str(entry.get("transfer_type", "")).strip().lower() in _REMOVED_TRANSFER_TYPES


def _active_entries(entries: list[dict]) -> list[dict]:
    return [entry for entry in entries if not _is_removed_feature_entry(entry)]


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
    position: str = "",
    fee: str = "",
    market_value: int = 0,
    transfer_date: str = "",
    previous_shirt_number: int | None = None,
    shirt_number: int | None = None,
    roster_action: str = "",
    save_scope: str = "",
    fotmob_player_id: int | None = None,
    sortitoutsi_player_id: int | None = None,
    transfermarkt_player_id: int | None = None,
    transfermarkt_from_club_id: int | None = None,
    transfermarkt_to_club_id: int | None = None,
    transfermarkt_transfer_id: int | None = None,
    sources: tuple[str, ...] = (),
    source_urls: tuple[str, ...] = (),
    proof_urls: tuple[str, ...] = (),
    native_metadata: dict[str, object] | None = None,
):
    """
    Append one transfer, shirt-number, or captain-role audit record.

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
        "position": position,
        "fee": fee,
        "market_value": market_value,
        "transfer_date": transfer_date,
        "dry_run": dry_run,
        "previous_shirt_number": previous_shirt_number,
        "shirt_number": shirt_number,
        "roster_action": roster_action,
        "save_scope": save_scope,
        "fotmob_player_id": fotmob_player_id,
        "sortitoutsi_player_id": sortitoutsi_player_id,
        "transfermarkt_player_id": transfermarkt_player_id,
        "transfermarkt_from_club_id": transfermarkt_from_club_id,
        "transfermarkt_to_club_id": transfermarkt_to_club_id,
        "transfermarkt_transfer_id": transfermarkt_transfer_id,
        "sources": list(sources),
        "source_urls": list(source_urls),
        "proof_urls": list(proof_urls),
        "native_metadata": dict(native_metadata or {}),
    }

    log_file = config.TRANSFER_LOG_FILE
    log_file.parent.mkdir(parents=True, exist_ok=True)

    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    action = "DRY-RUN" if dry_run else "APPLIED"
    if transfer_type == "captain_update":
        logger.info(
            f"[{action}] {player_name} captain for {to_team} "
            f"(previous_player_id={native_metadata.get('previous_captain_player_id') if native_metadata else None}, "
            f"conf={confidence:.0f}%)"
        )
    elif transfer_type == "shirt_number_update":
        logger.info(
            f"[{action}] {player_name} (id={player_id}) at {to_team}: "
            f"#{previous_shirt_number} → #{shirt_number} (conf={confidence:.0f}%)"
        )
    else:
        logger.info(
            f"[{action}] {player_name} (id={player_id}): "
            f"{from_team} → {to_team} (conf={confidence:.0f}%)"
        )


def read_log(
    save_scope: str | None = None,
    *,
    include_legacy: bool = False,
) -> list[dict]:
    """Read transfer audit records, optionally isolated to one output save."""
    log_file = config.TRANSFER_LOG_FILE
    if not log_file.exists():
        return []

    entries = []
    with open(log_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if _is_removed_feature_entry(entry):
                continue
            if save_scope is None:
                entries.append(entry)
            elif entry.get("save_scope") == save_scope:
                entries.append(entry)
            elif include_legacy and not entry.get("save_scope"):
                entries.append(entry)
    return entries


def print_summary(last_n: int = 20):
    """Print a human-readable summary of recent transfers."""
    entries = read_log()
    if not entries:
        print("No transfers logged yet.")
        return

    recent = entries[-last_n:]
    for e in recent:
        dry = " [DRY-RUN]" if e.get("dry_run") else ""
        ts = e.get("timestamp", "")[:19].replace("T", " ")
        if _is_captain_update(e):
            previous_id = (e.get("native_metadata") or {}).get(
                "previous_captain_player_id",
                "?",
            )
            print(
                f"  {ts}  {e['player_name']} captain for {e['to_team']}: "
                f"{previous_id} → {e['player_id']} "
                f"(conf={e.get('confidence', 0):.0f}%){dry}"
            )
        elif _is_shirt_number_update(e):
            print(
                f"  {ts}  {e['player_name']} at {e['to_team']}: "
                f"#{e.get('previous_shirt_number', '?')} → "
                f"#{e.get('shirt_number', '?')} "
                f"(conf={e.get('confidence', 0):.0f}%){dry}"
            )
        else:
            print(
                f"  {ts}  {e['player_name']}: "
                f"{e['from_team']} → {e['to_team']} "
                f"(conf={e.get('confidence', 0):.0f}%){dry}"
            )


_SHIRT_UPDATE_TYPES = {"shirt_number_update", "squad_update"}
def _is_shirt_number_update(entry: dict) -> bool:
    """Recognize current and legacy shirt-number audit records."""
    return str(entry.get("transfer_type", "")) in _SHIRT_UPDATE_TYPES

_CAPTAIN_UPDATE_TYPES = {"captain_update"}


def _is_captain_update(entry: dict) -> bool:
    """Recognize captain-role audit records."""
    return str(entry.get("transfer_type", "")) in _CAPTAIN_UPDATE_TYPES


def _is_roster_release(entry: dict) -> bool:
    """Recognize roster releases for dedicated report rendering."""
    action = str(entry.get("roster_action", "")).strip().lower()
    transfer_type = str(entry.get("transfer_type", "")).strip().lower()
    return action == "release" or transfer_type in {"release", "released"}


def _report_metrics(entries: list[dict]) -> dict[str, int]:
    active_entries = _active_entries(entries)
    captain_entries = [
        entry for entry in active_entries if _is_captain_update(entry)
    ]
    transfer_entries = [
        entry
        for entry in active_entries
        if not _is_shirt_number_update(entry)
        and not _is_captain_update(entry)
    ]
    club_transfer_entries = [
        entry for entry in transfer_entries if not _is_roster_release(entry)
    ]
    shirt_entries = [
        entry for entry in active_entries if _is_shirt_number_update(entry)
    ]
    loans = sum(
        1
        for entry in club_transfer_entries
        if "loan" in str(entry.get("transfer_type", "")).lower()
    )
    return {
        "total_changes": len(active_entries),
        "transfers": len(transfer_entries),
        "club_transfers": len(club_transfer_entries),
        "permanent": len(club_transfer_entries) - loans,
        "loans": loans,
        "shirt_updates": len(shirt_entries),
        "captain_updates": len(captain_entries),
        "dry_run": sum(1 for entry in active_entries if entry.get("dry_run")),
        "moves": sum(
            1 for entry in club_transfer_entries if entry.get("roster_action") == "move"
        ),
        "signings": sum(
            1 for entry in club_transfer_entries if entry.get("roster_action") == "add"
        ),
        "releases": sum(1 for entry in transfer_entries if _is_roster_release(entry)),
    }





def _markdown_cell(value) -> str:
    return str(value if value not in (None, "") else "-").replace("|", "\\|").replace("\n", " ")
def _native_metadata_summary(entry: dict) -> str:
    """Render compact native metadata without expanding the full JSON payload."""
    native = entry.get("native_metadata") or {}
    player = native.get("player_bin") or {}
    parts: list[str] = []
    if player:
        if player.get("found"):
            identity = player.get("name") or player.get("player_id") or "matched"
            position = player.get("registered_position")
            parts.append(
                "Player.bin: "
                + str(identity)
                + (f" ({position})" if position else "")
            )
        else:
            parts.append("Player.bin: not found")
    assignment = native.get("player_assignment") or {}
    teams = assignment.get("teams") or []
    if teams:
        parts.append(
            "Assignment: "
            + ", ".join(
                str(team.get("abbreviation") or team.get("name") or team.get("team_key"))
                for team in teams
            )
        )
    return "; ".join(parts) or "-"


def generate_markdown_report(
    entries: list[dict],
    title: str = "Football Life Live Sync Report",
    include_table: bool = True,
) -> str:
    """Generate a report for transfers, shirt-number, and captain changes."""
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    entries = _active_entries(entries)
    if not entries:
        return (
            f"## ⚽ {title}\n\n"
            f"**Generated:** `{now_str}`\n\n"
            "> No save changes were needed in this run.\n"
        )

    metrics = _report_metrics(entries)
    captain_entries = [
        entry for entry in entries if _is_captain_update(entry)
    ]
    transfer_entries = [
        entry
        for entry in entries
        if not _is_shirt_number_update(entry)
        and not _is_captain_update(entry)
    ]
    releases = [entry for entry in transfer_entries if _is_roster_release(entry)]
    club_transfers = [
        entry for entry in transfer_entries if not _is_roster_release(entry)
    ]
    shirts = [entry for entry in entries if _is_shirt_number_update(entry)]

    md = [
        f"## ⚽ {title}",
        f"**Generated:** `{now_str}`",
        "",
        "### Run overview",
        "",
        "| Save changes | Club transfers | Permanent | Loans / returns | Shirt numbers | Captains | Dry-run |",
        "|---:|---:|---:|---:|---:|---:|---:|",
        (
            f"| **{metrics['total_changes']}** | **{metrics['club_transfers']}** | "
            f"{metrics['permanent']} | {metrics['loans']} | "
            f"**{metrics['shirt_updates']}** | **{metrics['captain_updates']}** | "
            f"{metrics['dry_run']} |"
        ),
        (
            "> Captain updates use the latest verified captain marker from the "
            "club's most recent lineup."
        ),
        "",
        "> Shirt-number updates only change kit numbers. They never move a player between clubs.",
        (
            f"> Roster actions: {metrics['moves']} direct moves · "
            f"{metrics['signings']} signings · {metrics['releases']} releases."
        ),
        "",
    ]

    if include_table and club_transfers:
        md.extend([
            f"### Club transfers ({len(club_transfers)})",
            "",
            "| Status | Player | Pos | From | To | Deal | Native metadata | Match |",
            "|:---:|---|:---:|---|---|---|---|---:|",
        ])
        for entry in club_transfers:
            status = "🧪 Dry-run" if entry.get("dry_run") else "✅ Applied"
            md.append(
                f"| {status} | **{_markdown_cell(entry.get('player_name', 'Unknown'))}** "
                f"| `{_markdown_cell(entry.get('position'))}` "
                f"| {_markdown_cell(entry.get('from_team'))} "
                f"| {_markdown_cell(entry.get('to_team'))} "
                f"| {_markdown_cell(entry.get('fee') or entry.get('transfer_type', 'transfer'))} "
                f"| {_markdown_cell(_native_metadata_summary(entry))} "
                f"| {entry.get('confidence', 0):.0f}% |"
            )
        md.append("")

    if include_table and releases:
        md.extend([
            f"### Player releases ({len(releases)})",
            "",
            "| Status | Player | Pos | Released from | Native metadata | Match |",
            "|:---:|---|:---:|---|---|---:|",
        ])
        for entry in releases:
            status = "Dry-run" if entry.get("dry_run") else "Released"
            md.append(
                f"| {status} | **{_markdown_cell(entry.get('player_name', 'Unknown'))}** "
                f"| `{_markdown_cell(entry.get('position'))}` "
                f"| {_markdown_cell(entry.get('from_team'))} "
                f"| {_markdown_cell(_native_metadata_summary(entry))} "
                f"| {entry.get('confidence', 0):.0f}% |"
            )
        md.append("")





    if include_table and shirts:
        md.extend([
            f"### Shirt-number changes ({len(shirts)})",
            "",
            "| Status | Player | Club | Previous | New | Match |",
            "|:---:|---|---|---:|---:|---:|",
        ])
        for entry in shirts:
            status = "🧪 Dry-run" if entry.get("dry_run") else "🔢 Updated"
            old_number = _markdown_cell(entry.get("previous_shirt_number"))
            new_number = _markdown_cell(entry.get("shirt_number"))
            md.append(
                f"| {status} | **{_markdown_cell(entry.get('player_name', 'Unknown'))}** "
                f"| {_markdown_cell(entry.get('to_team'))} | #{old_number} | #{new_number} "
                f"| {entry.get('confidence', 0):.0f}% |"
            )
        md.append("")

    if include_table and captain_entries:
        md.extend([
            f"### Captain changes ({len(captain_entries)})",
            "",
            "| Status | Club | New captain | Previous player ID | Match |",
            "|:---:|---|---|---:|---:|",
        ])
        for entry in captain_entries:
            status = "Dry-run" if entry.get("dry_run") else "Updated"
            previous_id = (entry.get("native_metadata") or {}).get(
                "previous_captain_player_id"
            )
            md.append(
                f"| {status} | {_markdown_cell(entry.get('to_team'))} "
                f"| **{_markdown_cell(entry.get('player_name', 'Unknown'))}** "
                f"| {_markdown_cell(previous_id)} "
                f"| {entry.get('confidence', 0):.0f}% |"
            )
        md.append("")

    return "\n".join(md) + "\n"


def generate_html_report(
    entries: list[dict],
    title: str = "Football Life Live Sync Report",
) -> str:
    """Generate a responsive report for transfers and role changes."""
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    entries = _active_entries(entries)
    metrics = _report_metrics(entries)
    captain_entries = [
        entry for entry in entries if _is_captain_update(entry)
    ]
    transfer_entries = [
        entry
        for entry in entries
        if not _is_shirt_number_update(entry)
        and not _is_captain_update(entry)
    ]
    releases = [entry for entry in transfer_entries if _is_roster_release(entry)]
    club_transfers = [
        entry for entry in transfer_entries if not _is_roster_release(entry)
    ]
    shirts = [entry for entry in entries if _is_shirt_number_update(entry)]



    def value(raw, fallback="-") -> str:
        return escape(str(raw if raw not in (None, "") else fallback))

    def badge(
        entry: dict,
        *,
        shirt: bool = False,
        captain: bool = False,
        released: bool = False,
    ) -> str:
        if entry.get("dry_run"):
            return '<span class="badge badge-dry">Dry-run</span>'
        if released:
            return '<span class="badge badge-release">Released</span>'
        if captain:
            return '<span class="badge badge-captain">Captain updated</span>'
        if shirt:
            return '<span class="badge badge-number">Number changed</span>'
        return '<span class="badge badge-applied">Transfer applied</span>'



    transfer_rows = "".join(
        "<tr>"
        f"<td>{badge(entry)}</td>"
        f"<td><strong>{value(entry.get('player_name'), 'Unknown')}</strong></td>"
        f"<td><span class='position'>{value(entry.get('position'))}</span></td>"
        f"<td>{value(entry.get('from_team'))}</td>"
        f"<td>{value(entry.get('to_team'))}</td>"
        f"<td>{value(entry.get('fee') or entry.get('transfer_type'), 'Transfer')}</td>"
        f"<td>{value(_native_metadata_summary(entry))}</td>"
        f"<td class='numeric'>{entry.get('confidence', 0):.0f}%</td>"
        "</tr>"
        for entry in club_transfers
    )
    release_rows = "".join(
        "<tr>"
        f"<td>{badge(entry, released=True)}</td>"
        f"<td><strong>{value(entry.get('player_name'), 'Unknown')}</strong></td>"
        f"<td><span class='position'>{value(entry.get('position'))}</span></td>"
        f"<td>{value(entry.get('from_team'))}</td>"
        f"<td>{value(_native_metadata_summary(entry))}</td>"
        f"<td class='numeric'>{entry.get('confidence', 0):.0f}%</td>"
        "</tr>"
        for entry in releases
    )


    shirt_rows = "".join(
        "<tr>"
        f"<td>{badge(entry, shirt=True)}</td>"
        f"<td><strong>{value(entry.get('player_name'), 'Unknown')}</strong></td>"
        f"<td>{value(entry.get('to_team'))}</td>"
        f"<td class='shirt old'>#{value(entry.get('previous_shirt_number'))}</td>"
        f"<td class='shirt new'>#{value(entry.get('shirt_number'))}</td>"
        f"<td class='numeric'>{entry.get('confidence', 0):.0f}%</td>"
        "</tr>"
        for entry in shirts
    )
    captain_rows = "".join(
        "<tr>"
        f"<td>{badge(entry, captain=True)}</td>"
        f"<td>{value(entry.get('to_team'))}</td>"
        f"<td><strong>{value(entry.get('player_name'), 'Unknown')}</strong></td>"
        f"<td>{value((entry.get('native_metadata') or {}).get('previous_captain_player_id'))}</td>"
        f"<td class='numeric'>{entry.get('confidence', 0):.0f}%</td>"
        "</tr>"
        for entry in captain_entries
    )

    transfer_section = (
        f"""<section class="report-section">
        <div class="section-heading"><div><h2>Club transfers</h2><p>Players moved or signed.</p></div><span class="count">{len(club_transfers)}</span></div>
        <div class="table-wrap" role="region" aria-label="Club transfer details" tabindex="0"><table><thead><tr><th>Status</th><th>Player</th><th>Pos</th><th>From</th><th>To</th><th>Deal</th><th>Native metadata</th><th>Match</th></tr></thead><tbody>{transfer_rows}</tbody></table></div>
      </section>"""
        if club_transfers
        else ""
    )
    release_section = (
        f"""<section class="report-section release-section">
        <div class="section-heading"><div><h2>Player releases</h2><p>Players removed from a club roster and returned to free agency.</p></div><span class="count">{len(releases)}</span></div>
        <div class="table-wrap" role="region" aria-label="Player release details" tabindex="0"><table><thead><tr><th>Status</th><th>Player</th><th>Pos</th><th>Released from</th><th>Native metadata</th><th>Match</th></tr></thead><tbody>{release_rows}</tbody></table></div>
      </section>"""
        if releases
        else ""
    )


    shirt_section = (
        f"""<section class="report-section number-section">
        <div class="section-heading"><div><h2>Shirt-number changes</h2><p>Kit numbers only. No club movement.</p></div><span class="count">{len(shirts)}</span></div>
        <div class="table-wrap" role="region" aria-label="Shirt-number change details" tabindex="0"><table><thead><tr><th>Status</th><th>Player</th><th>Club</th><th>Previous</th><th>New</th><th>Match</th></tr></thead><tbody>{shirt_rows}</tbody></table></div>
      </section>"""
        if shirts
        else ""
    )
    captain_section = (
        f"""<section class="report-section captain-section">
        <div class="section-heading"><div><h2>Captain changes</h2><p>Captain role updates from the latest verified lineup marker.</p></div><span class="count">{len(captain_entries)}</span></div>
        <div class="table-wrap" role="region" aria-label="Captain change details" tabindex="0"><table><thead><tr><th>Status</th><th>Club</th><th>New captain</th><th>Previous player ID</th><th>Match</th></tr></thead><tbody>{captain_rows}</tbody></table></div>
      </section>"""
        if captain_entries
        else ""
    )
    empty_state = (
        '<section class="empty"><strong>Everything already current.</strong><p>No save changes were needed in this run.</p></section>'
        if not entries
        else ""
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{escape(title)}</title>
<style>
  :root {{ --ink:#f5f7f2; --muted:#a9b3aa; --pitch:#07130e; --surface:#101d17; --surface-2:#16261e; --line:#2a3c31; --lime:#b8f34a; --cyan:#62d9ff; --amber:#ffd166; --green:#70e19a; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; padding:clamp(1rem,3vw,3rem); background:radial-gradient(circle at 85% -10%,#1a3928 0,transparent 34rem),var(--pitch); color:var(--ink); font:15px/1.55 Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif; }}
  .shell {{ width:min(1180px,100%); margin:auto; }}
  .masthead {{ position:relative; overflow:hidden; padding:clamp(1.5rem,4vw,3rem); background:#0d1b14; border:1px solid var(--line); border-radius:16px; box-shadow:0 22px 60px rgba(0,0,0,.28); }}
  .masthead::after {{ content:""; position:absolute; inset:auto -8% -70% auto; width:24rem; aspect-ratio:1; border:1px solid rgba(184,243,74,.24); border-radius:50%; box-shadow:0 16px 48px rgba(0,0,0,.22); }}
  .kicker {{ margin:0 0 .55rem; color:var(--lime); font-size:.78rem; font-weight:800; letter-spacing:.12em; text-transform:uppercase; }}
  h1 {{ max-width:18ch; margin:0; font-size:clamp(2rem,5vw,4.5rem); line-height:.98; letter-spacing:-.035em; text-wrap:balance; }}
  .generated {{ margin:1rem 0 0; color:var(--muted); }}
  .summary {{ display:grid; grid-template-columns:repeat(7,1fr); margin:1.25rem 0 2.5rem; border:1px solid var(--line); border-radius:14px; overflow:hidden; background:var(--surface); }}
  .metric strong {{ display:block; color:var(--lime); font-size:clamp(1.7rem,3vw,2.45rem); line-height:1; letter-spacing:-.03em; }}
  .metric span {{ display:block; margin-top:.45rem; color:var(--muted); font-size:.82rem; }}
  .action-line {{ margin:-1.3rem 0 2.5rem; color:var(--muted); text-align:right; font-size:.86rem; }} .action-line strong {{ color:var(--ink); }}
  .report-section {{ margin-top:1.5rem; background:var(--surface); border:1px solid var(--line); border-radius:16px; overflow:hidden; box-shadow:0 16px 44px rgba(0,0,0,.18); }}
  .section-heading {{ display:flex; align-items:center; justify-content:space-between; gap:1rem; padding:1.25rem 1.4rem; background:var(--surface-2); }}
  .section-heading h2 {{ margin:0; font-size:1.18rem; letter-spacing:-.015em; }} .section-heading p {{ margin:.2rem 0 0; color:var(--muted); font-size:.86rem; }}
  .count {{ min-width:2.3rem; padding:.28rem .65rem; border-radius:999px; background:var(--lime); color:#132008; text-align:center; font-weight:850; }}
  .table-wrap {{ overflow-x:auto; }} table {{ width:100%; min-width:780px; border-collapse:collapse; }}
  .table-wrap:focus-visible {{ outline:2px solid var(--cyan); outline-offset:-2px; }}
  th,td {{ padding:.88rem 1rem; text-align:left; border-bottom:1px solid var(--line); }} th {{ color:var(--muted); font-size:.72rem; letter-spacing:.08em; text-transform:uppercase; }} tbody tr:last-child td {{ border-bottom:0; }} tbody tr:hover {{ background:#18291f; }}
  .badge {{ display:inline-flex; align-items:center; white-space:nowrap; padding:.24rem .58rem; border-radius:999px; font-size:.72rem; font-weight:750; }}
  .badge-applied {{ color:#baf8ce; background:#174b2c; }} .badge-number {{ color:#c9f3ff; background:#124354; }} .badge-captain {{ color:#e2d3ff; background:#3c2860; }} .badge-release {{ color:#ffd2c8; background:#5b2118; }} .badge-dry {{ color:#ffe5a3; background:#55400e; }}

<body>
  <main class="shell">
    <header class="masthead"><p class="kicker">FL26 · verified sync</p><h1>{escape(title)}</h1><p class="generated">Generated {now_str} · {metrics['total_changes']} save changes</p></header>
    <section class="summary" aria-label="Run summary">
      <div class="metric"><strong>{metrics['club_transfers']}</strong><span>Club transfers</span></div>
      <div class="metric"><strong>{metrics['releases']}</strong><span>Releases</span></div>
      <div class="metric"><strong>{metrics['permanent']}</strong><span>Permanent moves</span></div>
      <div class="metric"><strong>{metrics['loans']}</strong><span>Loans / returns</span></div>
      <div class="metric"><strong>{metrics['shirt_updates']}</strong><span>Shirt numbers changed</span></div>
      <div class="metric"><strong>{metrics['captain_updates']}</strong><span>Captains changed</span></div>
      <div class="metric"><strong>{metrics['dry_run']}</strong><span>Dry-run</span></div>
    </section>
    <p class="action-line">Roster actions · <strong>{metrics['moves']}</strong> direct moves · <strong>{metrics['signings']}</strong> signings · <strong>{metrics['releases']}</strong> releases</p>
    {transfer_section}{release_section}{shirt_section}{captain_section}{empty_state}
  </main>
</body>
</html>
"""


def save_reports(
    entries: list[dict],
    output_dir: Path | None = None,
    write_github_summary: bool = True,
) -> str:
    """Save markdown and HTML save-change report cards."""
    import os

    out = output_dir or config.OUTPUT_DIR
    out.mkdir(parents=True, exist_ok=True)

    md_report = generate_markdown_report(entries)
    html_report = generate_html_report(entries)

    md_path = out / "transfer_summary.md"
    html_path = out / "transfer_summary.html"

    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_report)

    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_report)

    logger.info(f"Saved visual report cards: {md_path} and {html_path}")

    # GitHub Actions Step Summary support (Keep it concise, no huge table)
    if write_github_summary:
        summary_env = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary_env:
            try:
                # Generate a short version of the markdown report just for the step summary
                short_md_report = generate_markdown_report(entries, include_table=False)
                with open(summary_env, "a", encoding="utf-8") as f:
                    f.write(short_md_report + "\n")
                logger.info(f"Appended short markdown report to $GITHUB_STEP_SUMMARY")
            except Exception as e:
                logger.warning(f"Could not write to $GITHUB_STEP_SUMMARY: {e}")

    return md_report
