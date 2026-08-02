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
    position: str = "",
    fee: str = "",
    market_value: int = 0,
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
        "position": position,
        "fee": fee,
        "market_value": market_value,
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


def generate_markdown_report(entries: list[dict], title: str = "Football Life Live Transfer Sync Report", include_table: bool = True) -> str:
    """Generate a clean, structured GitHub Markdown report card from transfer log entries."""
    if not entries:
        return f"## 📊 {title}\n\n*No transfers recorded in this sync run.*"

    total = len(entries)
    applied = sum(1 for e in entries if not e.get("dry_run"))
    dry = sum(1 for e in entries if e.get("dry_run"))
    loans = sum(1 for e in entries if "loan" in str(e.get("transfer_type", "")).lower())
    perm = total - loans

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    md = [
        f"## ⚽ {title}",
        f"**Generated:** `{now_str}`",
        "",
        "### 📈 Quick Metrics",
        f"- **Total Transfers:** `{total}`",
        f"- **Applied to Save:** `{applied}` {'✅' if applied > 0 else 'ℹ️'}",
        f"- **Dry-Run Validated:** `{dry}`",
        f"- **Permanent Transfers:** `{perm}` | **Loans / Loan Returns:** `{loans}`",
        "",
    ]

    if include_table:
        md.extend([
            "### 📋 Transfer Details",
            "| Status | Player | Pos | From Club | To Club | Fee / Type | Conf |",
            "| :---: | :--- | :---: | :--- | :--- | :--- | :---: |",
        ])

    if include_table:
        for e in entries:
            status = "🧪 Dry-Run" if e.get("dry_run") else "✅ Applied"
            pname = f"**{e.get('player_name', 'Unknown')}**"
            pos = e.get("position", "-") or "-"
            from_c = e.get("from_team", "-")
            to_c = e.get("to_team", "-")
            fee_type = e.get("fee") or e.get("transfer_type", "transfer")
            conf = f"{e.get('confidence', 0):.0f}%"

            md.append(f"| {status} | {pname} | `{pos}` | {from_c} | {to_c} | {fee_type} | {conf} |")

    return "\n".join(md) + "\n"


def generate_html_report(entries: list[dict], title: str = "Football Life Live Transfer Sync Report") -> str:
    """Generate a modern, standalone dark-mode HTML report card."""
    md_content = generate_markdown_report(entries, title)
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    total = len(entries)
    applied = sum(1 for e in entries if not e.get("dry_run"))

    rows_html = []
    for e in entries:
        status_badge = (
            '<span class="badge badge-dry">Dry-Run</span>'
            if e.get("dry_run")
            else '<span class="badge badge-applied">Applied</span>'
        )
        pos = e.get("position", "-") or "-"
        fee = e.get("fee") or e.get("transfer_type", "transfer")
        conf = f"{e.get('confidence', 0):.0f}%"
        rows_html.append(
            f"<tr>"
            f"<td>{status_badge}</td>"
            f"<td><strong>{e.get('player_name')}</strong></td>"
            f"<td><span class='pos'>{pos}</span></td>"
            f"<td>{e.get('from_team')}</td>"
            f"<td>{e.get('to_team')}</td>"
            f"<td>{fee}</td>"
            f"<td>{conf}</td>"
            f"</tr>"
        )

    table_body = "\n".join(rows_html)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
  :root {{
    --bg: #0f172a;
    --card-bg: #1e293b;
    --text: #f8fafc;
    --text-muted: #94a3b8;
    --primary: #38bdf8;
    --accent: #22c55e;
    --border: #334155;
  }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
    padding: 2rem;
    margin: 0;
  }}
  .container {{
    max-width: 1000px;
    margin: 0 auto;
  }}
  .header {{
    background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%);
    border: 1px solid var(--border);
    padding: 1.5rem 2rem;
    border-radius: 12px;
    margin-bottom: 2rem;
  }}
  .stats-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 1rem;
    margin-bottom: 2rem;
  }}
  .stat-card {{
    background: var(--card-bg);
    border: 1px solid var(--border);
    padding: 1.25rem;
    border-radius: 8px;
    text-align: center;
  }}
  .stat-num {{
    font-size: 2rem;
    font-weight: 700;
    color: var(--primary);
  }}
  table {{
    width: 100%;
    border-collapse: collapse;
    background: var(--card-bg);
    border-radius: 8px;
    overflow: hidden;
    border: 1px solid var(--border);
  }}
  th, td {{
    padding: 0.85rem 1rem;
    text-align: left;
    border-bottom: 1px solid var(--border);
  }}
  th {{
    background: #182234;
    color: var(--text-muted);
    font-size: 0.85rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }}
  .badge {{
    display: inline-block;
    padding: 0.2rem 0.6rem;
    border-radius: 9999px;
    font-size: 0.75rem;
    font-weight: 600;
  }}
  .badge-applied {{ background: #14532d; color: #4ade80; }}
  .badge-dry {{ background: #713f12; color: #facc15; }}
  .pos {{
    background: #334155;
    padding: 0.15rem 0.45rem;
    border-radius: 4px;
    font-family: monospace;
  }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>⚽ {title}</h1>
    <p style="color: var(--text-muted); margin: 0;">Generated: {now_str}</p>
  </div>
  <div class="stats-grid">
    <div class="stat-card">
      <div class="stat-num">{total}</div>
      <div style="color: var(--text-muted)">Total Transfers</div>
    </div>
    <div class="stat-card">
      <div class="stat-num" style="color: var(--accent);">{applied}</div>
      <div style="color: var(--text-muted)">Applied to Save</div>
    </div>
  </div>
  <table>
    <thead>
      <tr>
        <th>Status</th>
        <th>Player</th>
        <th>Pos</th>
        <th>From</th>
        <th>To</th>
        <th>Fee / Type</th>
        <th>Confidence</th>
      </tr>
    </thead>
    <tbody>
      {table_body}
    </tbody>
  </table>
</div>
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

