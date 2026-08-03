"""Report regression tests."""

from editor.logger import (
    generate_html_report,
    generate_markdown_report,
    log_transfer,
    read_log,
)


def _entries():
    return [
        {
            "player_name": "Transfer Player",
            "position": "CM",
            "from_team": "Old Club",
            "to_team": "New Club",
            "transfer_type": "loan",
            "fee": "on loan",
            "confidence": 98.0,
            "dry_run": False,
        },
        {
            "player_name": "Number Player",
            "from_team": "Same Club",
            "to_team": "Same Club",
            "transfer_type": "shirt_number_update",
            "previous_shirt_number": 18,
            "shirt_number": 8,
            "confidence": 100.0,
            "dry_run": False,
        },
    ]


def test_markdown_separates_transfers_from_shirt_numbers():
    report = generate_markdown_report(_entries())

    assert "Club transfers (1)" in report
    assert "Shirt-number changes (1)" in report
    assert "| #18 | #8 |" in report
    assert "They never move a player between clubs" in report


def test_github_summary_keeps_detailed_metrics_without_tables():
    report = generate_markdown_report(_entries(), include_table=False)

    assert "| **2** | **1** | 0 | 1 | **1** | 0 |" in report
    assert "Club transfers (1)" not in report


def test_html_report_has_distinct_sections_and_escapes_values():
    entries = _entries()
    entries[0]["player_name"] = "Player <script>"
    report = generate_html_report(entries)

    assert "Club transfers" in report
    assert "Shirt-number changes" in report
    assert "Kit numbers only. No club movement." in report
    assert "Player &lt;script&gt;" in report
    assert "Player <script>" not in report


def test_transfer_history_is_scoped_per_output_save(monkeypatch, tmp_path):
    import config

    monkeypatch.setattr(config, "TRANSFER_LOG_FILE", tmp_path / "transfers.jsonl")
    common = {
        "player_name": "Player",
        "player_id": 10,
        "from_team": "A",
        "from_team_id": 1,
        "to_team": "B",
        "to_team_id": 2,
    }
    log_transfer(
        **common,
        save_scope="save-a",
        fotmob_player_id=777,
        sortitoutsi_player_id=888,
        sources=("fotmob", "wikipedia", "sortitoutsi"),
        source_urls=("https://example.test/source",),
        proof_urls=("https://example.test/proof",),
    )
    log_transfer(**common, save_scope="save-b")
    log_transfer(**common)

    assert len(read_log(save_scope="save-a")) == 1
    assert len(read_log(save_scope="save-a", include_legacy=True)) == 2
    assert len(read_log()) == 3
    assert read_log(save_scope="save-a")[0]["fotmob_player_id"] == 777
    assert read_log(save_scope="save-a")[0]["sortitoutsi_player_id"] == 888
    assert read_log(save_scope="save-a")[0]["sources"] == [
        "fotmob",
        "wikipedia",
        "sortitoutsi",
    ]
    assert read_log(save_scope="save-a")[0]["proof_urls"] == [
        "https://example.test/proof"
    ]
