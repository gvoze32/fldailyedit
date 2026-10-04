"""Contract tests for transfer and squad-number logging."""

from __future__ import annotations

import json
from pathlib import Path

import config
from editor import logger as transfer_logger


def _entry(**overrides: object) -> dict:
    entry = {
        "timestamp": "2026-08-10T12:00:00+00:00",
        "player_name": "Player One",
        "player_id": 1,
        "from_team": "Old Club",
        "from_team_id": 10,
        "to_team": "New Club",
        "to_team_id": 20,
        "confidence": 96.0,
        "transfer_type": "transfer",
        "position": "CF",
        "fee": "€1m",
        "transfer_date": "2026-08-01",
        "dry_run": False,
        "previous_shirt_number": None,
        "shirt_number": None,
        "roster_action": "move",
        "save_scope": "",
        "sources": ["fotmob"],
        "source_urls": [],
        "proof_urls": [],
        "native_metadata": {},
    }
    entry.update(overrides)
    return entry


def test_log_transfer_persists_sources_and_native_metadata(monkeypatch, tmp_path: Path):
    log_path = tmp_path / "transfer_log.jsonl"
    monkeypatch.setattr(config, "TRANSFER_LOG_FILE", log_path)

    transfer_logger.log_transfer(
        "Ada Player",
        42,
        "Old Club",
        10,
        "New Club",
        20,
        confidence=94.26,
        transfer_date="2026-08-01",
        sources=("fotmob", "transfermarkt"),
        source_urls=("https://example.test/source",),
        proof_urls=("https://example.test/proof",),
        native_metadata={"player_bin": {"found": True, "name": "Ada Player"}},
    )

    entries = transfer_logger.read_log()
    assert len(entries) == 1
    assert entries[0]["confidence"] == 94.3
    assert entries[0]["sources"] == ["fotmob", "transfermarkt"]
    assert entries[0]["native_metadata"]["player_bin"]["found"] is True




def test_read_log_isolates_save_scope_and_ignores_removed_feature_history(
    monkeypatch, tmp_path: Path
):
    log_path = tmp_path / "transfer_log.jsonl"
    monkeypatch.setattr(config, "TRANSFER_LOG_FILE", log_path)
    log_path.write_text(
        "\n".join(
            [
                json.dumps(_entry(player_name="Scoped", save_scope="save-a")),
                json.dumps(_entry(player_name="Other", save_scope="save-b")),
                json.dumps(
                    _entry(
                        player_name="Old contribution",
                        transfer_type="player_spec_update",
                    )
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    assert [entry["player_name"] for entry in transfer_logger.read_log()] == [
        "Scoped",
        "Other",
    ]
    assert [
        entry["player_name"]
        for entry in transfer_logger.read_log(save_scope="save-a")
    ] == ["Scoped"]
    assert transfer_logger.read_log(save_scope="save-a", include_legacy=True) == [
        _entry(player_name="Scoped", save_scope="save-a")
    ]


def test_markdown_report_contains_only_transfers_releases_and_shirt_changes():
    entries = [
        _entry(player_name="Moved Player"),
        _entry(
            player_name="Loan Player",
            transfer_type="loan",
            roster_action="add",
        ),
        _entry(
            player_name="Released Player",
            transfer_type="release",
            roster_action="release",
            to_team="Free Agency",
        ),
        _entry(
            player_name="Number Player",
            transfer_type="shirt_number_update",
            roster_action="squad_update",
            previous_shirt_number=8,
            shirt_number=10,
        ),
        _entry(
            player_name="Old contribution",
            transfer_type="player_spec_create",
        ),
    ]

    report = transfer_logger.generate_markdown_report(entries)

    assert "Club transfers (2)" in report
    assert "Player releases (1)" in report
    assert "Shirt-number changes (1)" in report
    assert "Moved Player" in report
    assert "Number Player" in report
    assert "Old contribution" not in report
    assert "Player creations" not in report
    assert "Player updates" not in report
    assert "Reviewed Player Update" not in report



def test_reports_render_captain_changes_separately():
    captain = _entry(
        player_name="Captain Player",
        player_id=1002,
        from_team="Example FC",
        from_team_id=101,
        to_team="Example FC",
        to_team_id=101,
        transfer_type="captain_update",
        roster_action="captain",
        native_metadata={"previous_captain_player_id": 1001},
    )

    markdown = transfer_logger.generate_markdown_report([captain])
    html = transfer_logger.generate_html_report([captain])

    assert "Captains |" in markdown
    assert "Captain changes (1)" in markdown
    assert "Captain Player" in markdown
    assert "Club transfers (" not in markdown
    assert "Captains changed" in html
    assert "Captain changes" in html
    assert "Captain Player" in html

def test_html_report_escapes_transfer_names_without_contribution_sections():
    report = transfer_logger.generate_html_report(
        [_entry(player_name="<script>alert('x')</script>")]
    )

    assert "&lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt;" in report
    assert "<script>alert('x')</script>" not in report
    assert "Player creations" not in report
    assert "Player updates" not in report
    assert "Club transfers" in report


def test_save_reports_writes_transfer_only_cards(monkeypatch, tmp_path: Path):
    summary_path = tmp_path / "github-summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_path))
    entries = [
        _entry(player_name="Applied Transfer"),
        _entry(
            player_name="Dry Number",
            transfer_type="shirt_number_update",
            roster_action="squad_update",
            dry_run=True,
            previous_shirt_number=7,
            shirt_number=11,
        ),
    ]

    returned_markdown = transfer_logger.save_reports(entries, output_dir=tmp_path)

    markdown = (tmp_path / "transfer_summary.md").read_text(encoding="utf-8")
    html = (tmp_path / "transfer_summary.html").read_text(encoding="utf-8")
    summary = summary_path.read_text(encoding="utf-8")
    assert returned_markdown == markdown
    assert "Applied Transfer" in markdown
    assert "Dry Number" in html
    assert "Player creations" not in summary
    assert "Player updates" not in summary


def _skipped(**overrides: object) -> dict:
    item = {
        "player_name": "Missing Player",
        "from_team": "Club A",
        "to_team": "Club B",
        "date": "2026-09-30",
        "source": "fotmob",
        "reason": "player_not_in_save",
        "detail": "No save player matched",
        "relevant": True,
        "fotmob_player_id": 12345,
        "candidates": [],
    }
    item.update(overrides)
    return item


def test_save_reports_writes_not_applied_section_and_jsonl(tmp_path: Path):
    skipped = [
        _skipped(player_name="Foreign Move", relevant=False, reason="team_not_in_save"),
        _skipped(player_name="Unknown Signing"),
        _skipped(player_name="Foreign Signing", relevant=False),
    ]

    markdown = transfer_logger.save_reports(
        [], output_dir=tmp_path, write_github_summary=False, skipped=skipped
    )

    html = (tmp_path / "transfer_summary.html").read_text(encoding="utf-8")
    rows = [
        json.loads(line)
        for line in (tmp_path / "skipped_transfers.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert [row["player_name"] for row in rows] == [
        "Foreign Move",
        "Unknown Signing",
        "Foreign Signing",
    ]
    assert "### Not applied (3)" in markdown
    # Reason groups with save-relevant entries come first; relevant rows lead.
    assert markdown.index("`player_not_in_save` (2)") < markdown.index(
        "`team_not_in_save` (1)"
    )
    assert markdown.index("Unknown Signing") < markdown.index("Foreign Signing")
    assert "12345" in markdown
    assert "Not applied" in html and "Unknown Signing" in html
    assert "</style>" in html


def test_save_reports_rewrites_empty_skipped_jsonl(tmp_path: Path):
    stale = tmp_path / "skipped_transfers.jsonl"
    stale.write_text('{"stale": true}\n', encoding="utf-8")

    markdown = transfer_logger.save_reports(
        [_entry()], output_dir=tmp_path, write_github_summary=False
    )

    assert stale.read_text(encoding="utf-8") == ""
    assert "Not applied" not in markdown
