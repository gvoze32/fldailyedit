"""
Transfer logging — structured JSONL output for audit trail and rollback.
"""
import json
import logging
from html import escape
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
    position: str = "",
    fee: str = "",
    market_value: int = 0,
    previous_shirt_number: int | None = None,
    shirt_number: int | None = None,
    roster_action: str = "",
):
    """
    Append a transfer or shirt-number audit record to the JSONL log file.

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
        "dry_run": dry_run,
        "previous_shirt_number": previous_shirt_number,
        "shirt_number": shirt_number,
        "roster_action": roster_action,
    }

    log_file = config.TRANSFER_LOG_FILE
    log_file.parent.mkdir(parents=True, exist_ok=True)

    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    action = "DRY-RUN" if dry_run else "APPLIED"
    if transfer_type == "shirt_number_update":
        logger.info(
            f"[{action}] {player_name} (id={player_id}) at {to_team}: "
            f"#{previous_shirt_number} → #{shirt_number} (conf={confidence:.0f}%)"
        )
    else:
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
    print(f"Last {len(recent)} save changes (of {len(entries)} total):")
    print("-" * 70)

    for e in recent:
        dry = " [DRY-RUN]" if e.get("dry_run") else ""
        ts = e.get("timestamp", "")[:19].replace("T", " ")
        if _is_shirt_number_update(e):
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


def _report_metrics(entries: list[dict]) -> dict[str, int]:
    transfer_entries = [e for e in entries if not _is_shirt_number_update(e)]
    shirt_entries = [e for e in entries if _is_shirt_number_update(e)]
    loans = sum(
        1
        for e in transfer_entries
        if "loan" in str(e.get("transfer_type", "")).lower()
    )
    return {
        "total_changes": len(entries),
        "transfers": len(transfer_entries),
        "permanent": len(transfer_entries) - loans,
        "loans": loans,
        "shirt_updates": len(shirt_entries),
        "dry_run": sum(1 for e in entries if e.get("dry_run")),
        "moves": sum(1 for e in transfer_entries if e.get("roster_action") == "move"),
        "signings": sum(1 for e in transfer_entries if e.get("roster_action") == "add"),
        "releases": sum(1 for e in transfer_entries if e.get("roster_action") == "release"),
    }


def _markdown_cell(value) -> str:
    return str(value if value not in (None, "") else "-").replace("|", "\\|").replace("\n", " ")


def generate_markdown_report(
    entries: list[dict],
    title: str = "Football Life Live Sync Report",
    include_table: bool = True,
) -> str:
    """Generate a clear report with transfers and shirt changes separated."""
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    if not entries:
        return (
            f"## ⚽ {title}\n\n"
            f"**Generated:** `{now_str}`\n\n"
            "> No save changes were needed in this run.\n"
        )

    metrics = _report_metrics(entries)
    transfers = [e for e in entries if not _is_shirt_number_update(e)]
    shirts = [e for e in entries if _is_shirt_number_update(e)]
    md = [
        f"## ⚽ {title}",
        f"**Generated:** `{now_str}`",
        "",
        "### Run overview",
        "",
        "| Save changes | Club transfers | Permanent | Loans / returns | Shirt numbers | Dry-run |",
        "|---:|---:|---:|---:|---:|---:|",
        (
            f"| **{metrics['total_changes']}** | **{metrics['transfers']}** | "
            f"{metrics['permanent']} | {metrics['loans']} | "
            f"**{metrics['shirt_updates']}** | {metrics['dry_run']} |"
        ),
        "",
        "> Shirt-number updates only change kit numbers. They never move a player between clubs.",
        (
            f"> Roster actions: {metrics['moves']} direct moves · "
            f"{metrics['signings']} signings · {metrics['releases']} releases."
        ),
        "",
    ]

    if include_table and transfers:
        md.extend([
            f"### Club transfers ({len(transfers)})",
            "",
            "| Status | Player | Pos | From | To | Deal | Match |",
            "|:---:|---|:---:|---|---|---|---:|",
        ])
        for entry in transfers:
            status = "🧪 Dry-run" if entry.get("dry_run") else "✅ Applied"
            md.append(
                f"| {status} | **{_markdown_cell(entry.get('player_name', 'Unknown'))}** "
                f"| `{_markdown_cell(entry.get('position'))}` "
                f"| {_markdown_cell(entry.get('from_team'))} "
                f"| {_markdown_cell(entry.get('to_team'))} "
                f"| {_markdown_cell(entry.get('fee') or entry.get('transfer_type', 'transfer'))} "
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

    return "\n".join(md) + "\n"


def generate_html_report(
    entries: list[dict],
    title: str = "Football Life Live Sync Report",
) -> str:
    """Generate a responsive matchday-style HTML report."""
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    metrics = _report_metrics(entries)
    transfers = [e for e in entries if not _is_shirt_number_update(e)]
    shirts = [e for e in entries if _is_shirt_number_update(e)]

    def value(raw, fallback="-") -> str:
        return escape(str(raw if raw not in (None, "") else fallback))

    def badge(entry: dict, shirt: bool = False) -> str:
        if entry.get("dry_run"):
            return '<span class="badge badge-dry">Dry-run</span>'
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
        f"<td class='numeric'>{entry.get('confidence', 0):.0f}%</td>"
        "</tr>"
        for entry in transfers
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

    transfer_section = (
        f"""<section class="report-section">
        <div class="section-heading"><div><h2>Club transfers</h2><p>Players moved, signed, or released.</p></div><span class="count">{len(transfers)}</span></div>
        <div class="table-wrap" role="region" aria-label="Club transfer details" tabindex="0"><table><thead><tr><th>Status</th><th>Player</th><th>Pos</th><th>From</th><th>To</th><th>Deal</th><th>Match</th></tr></thead><tbody>{transfer_rows}</tbody></table></div>
      </section>"""
        if transfers
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
<meta name="color-scheme" content="dark">
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
  .summary {{ display:grid; grid-template-columns:1.4fr repeat(3,1fr); margin:1.25rem 0 2.5rem; border:1px solid var(--line); border-radius:14px; overflow:hidden; background:var(--surface); }}
  .metric {{ min-width:0; padding:1.15rem 1.3rem; border-right:1px solid var(--line); }} .metric:last-child {{ border-right:0; }}
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
  .badge-applied {{ color:#baf8ce; background:#174b2c; }} .badge-number {{ color:#c9f3ff; background:#124354; }} .badge-dry {{ color:#ffe5a3; background:#55400e; }}
  .position {{ color:var(--amber); font-weight:750; }} .numeric,.shirt {{ font-variant-numeric:tabular-nums; }} .shirt {{ font-size:1.05rem; font-weight:800; }} .shirt.old {{ color:var(--muted); }} .shirt.new {{ color:var(--cyan); }}
  .empty {{ padding:3rem; text-align:center; background:var(--surface); border:1px solid var(--line); border-radius:16px; }} .empty strong {{ font-size:1.3rem; }} .empty p {{ margin:.35rem 0 0; color:var(--muted); }}
  @media (max-width:760px) {{ body {{ padding:1rem; }} .summary {{ grid-template-columns:1fr 1fr; }} .metric:nth-child(2) {{ border-right:0; }} .metric:nth-child(-n+2) {{ border-bottom:1px solid var(--line); }} .action-line {{ margin-top:-1.5rem; text-align:left; }} h1 {{ font-size:2.4rem; }} }}
  @media (prefers-reduced-motion:no-preference) {{ tbody tr {{ transition:background-color .16s ease-out; }} }}
</style>
</head>
<body>
  <main class="shell">
    <header class="masthead"><p class="kicker">FL26 · verified sync</p><h1>{escape(title)}</h1><p class="generated">Generated {now_str} · {metrics['total_changes']} save changes</p></header>
    <section class="summary" aria-label="Run summary">
      <div class="metric"><strong>{metrics['transfers']}</strong><span>Club transfers</span></div>
      <div class="metric"><strong>{metrics['permanent']}</strong><span>Permanent moves</span></div>
      <div class="metric"><strong>{metrics['loans']}</strong><span>Loans / returns</span></div>
      <div class="metric"><strong>{metrics['shirt_updates']}</strong><span>Shirt numbers changed</span></div>
    </section>
    <p class="action-line">Roster actions · <strong>{metrics['moves']}</strong> direct moves · <strong>{metrics['signings']}</strong> signings · <strong>{metrics['releases']}</strong> releases</p>
    {transfer_section}{shirt_section}{empty_state}
  </main>
</body>
</html>
"""


def save_reports(
    entries: list[dict],
    output_dir: Path | None = None,
    write_github_summary: bool = True,
):
    """Save markdown and HTML transfer report cards."""
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
