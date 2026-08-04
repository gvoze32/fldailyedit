"""Report regression tests."""

import pytest

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


def _player_spec_create_entry():
    return {
        "player_name": "Dastan Satpaev",
        "position": "CF",
        "from_team": "Missing from FL26 database",
        "to_team": "Chelsea FC",
        "transfer_type": "player_spec_create",
        "shirt_number": 36,
        "confidence": 100.0,
        "roster_action": "create",
        "dry_run": False,
    }



def test_markdown_separates_transfers_from_shirt_numbers():
    report = generate_markdown_report(_entries())

    assert "Club transfers (1)" in report
    assert "Shirt-number changes (1)" in report
    assert "| #18 | #8 |" in report
    assert "They never move a player between clubs" in report


def test_github_summary_keeps_detailed_metrics_without_tables():
    report = generate_markdown_report(_entries(), include_table=False)

    assert "| **2** | **1** | **0** | **0** | 0 | 1 | **1** | 0 |" in report
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


def test_reports_label_current_player_spec_creation_consistently():
    entries = _entries() + [_player_spec_create_entry()]

    markdown = generate_markdown_report(entries)
    html = generate_html_report(entries)

    assert "Club transfers (1)" in markdown
    assert "Player creations (1)" in markdown
    assert "Dastan Satpaev" in markdown
    assert "Reviewed Player Update" in markdown
    assert "Reviewed Player Update" in html
    assert "Reviewed player spec" not in markdown
    assert "Reviewed player spec" not in html
    assert "Reviewed manifest" not in markdown
    assert "Reviewed manifest" not in html


def test_retired_curated_creation_alias_is_not_classified_as_player_spec():
    legacy = _player_spec_create_entry()
    legacy["transfer_type"] = "curated_player_creation"

    markdown = generate_markdown_report([legacy])

    assert "### Player creations (" not in markdown
    assert "Club transfers (1)" in markdown


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
    try:
        log_transfer(
            **common,
            save_scope="save-a",
            fotmob_player_id=777,
            sortitoutsi_player_id=888,
            transfermarkt_player_id=999,
            transfermarkt_from_club_id=111,
            transfermarkt_to_club_id=222,
            transfermarkt_transfer_id=333,
            sources=("fotmob", "wikipedia", "sortitoutsi", "transfermarkt"),
            source_urls=("https://example.test/source",),
            proof_urls=("https://example.test/proof",),
        )
    except TypeError:
        pytest.fail("Transfermarkt audit fields are not implemented")
    log_transfer(**common, save_scope="save-b")
    log_transfer(**common)

    assert len(read_log(save_scope="save-a")) == 1
    assert len(read_log(save_scope="save-a", include_legacy=True)) == 2
    assert len(read_log()) == 3
    assert read_log(save_scope="save-a")[0]["fotmob_player_id"] == 777
    assert read_log(save_scope="save-a")[0]["sortitoutsi_player_id"] == 888
    assert read_log(save_scope="save-a")[0]["transfermarkt_player_id"] == 999
    assert read_log(save_scope="save-a")[0]["transfermarkt_from_club_id"] == 111
    assert read_log(save_scope="save-a")[0]["transfermarkt_to_club_id"] == 222
    assert read_log(save_scope="save-a")[0]["transfermarkt_transfer_id"] == 333
    assert read_log(save_scope="save-a")[0]["sources"] == [
        "fotmob",
        "wikipedia",
        "sortitoutsi",
        "transfermarkt",
    ]
    assert read_log(save_scope="save-a")[0]["proof_urls"] == [
        "https://example.test/proof"
    ]


def test_reports_classify_player_specs_separately_from_club_transfers():
    player_spec_entries = [
        {
            "player_name": "Dastan Satpaev",
            "position": "CF",
            "from_team": "Missing from FL26 database",
            "to_team": "Chelsea FC",
            "transfer_type": "player_spec_create",
            "shirt_number": 36,
            "confidence": 100.0,
            "roster_action": "create",
            "dry_run": False,
        },
        {
            "player_name": "Marco Palestra",
            "from_team": "",
            "to_team": "",
            "transfer_type": "player_spec_update",
            "field_changes": [
                {"field": "speed", "from": 77, "to": 80},
                {"field": "acceleration", "from": 75, "to": 77},
            ],
            "confidence": 100.0,
            "roster_action": "update",
            "dry_run": False,
        },
    ]
    entries = _entries() + player_spec_entries

    markdown = generate_markdown_report(entries)
    html = generate_html_report(entries)

    assert "Club transfers (1)" in markdown
    assert "Player creations (1)" in markdown
    assert "Player updates (1)" in markdown
    assert "| **4** | **1** | **1** | **1** | 0 | 1 | **1** | 0 |" in markdown
    assert "speed: 77 -> 80" in markdown
    assert "acceleration: 75 -> 77" in markdown
    assert "Player creations" in html
    assert "Player updates" in html
    assert "speed: 77 -> 80" in html
    assert "Marco Palestra" not in markdown.split("### Club transfers (1)", 1)[1].split("###", 1)[0]


def test_log_transfer_persists_player_spec_field_changes(monkeypatch, tmp_path):
    import config

    monkeypatch.setattr(config, "TRANSFER_LOG_FILE", tmp_path / "transfers.jsonl")
    changes = [{"field": "speed", "from": 77, "to": 80}]

    log_transfer(
        player_name="Marco Palestra",
        player_id=162196,
        from_team="",
        from_team_id=0,
        to_team="",
        to_team_id=0,
        transfer_type="player_spec_update",
        roster_action="update",
        field_changes=changes,
        pes_retro_stats_player_id="0ce2dbde-9cd9-423c-a90a-35b07df6a967",
    )

    entry = read_log()[0]
    assert entry["field_changes"] == changes
    assert entry["pes_retro_stats_player_id"] == (
        "0ce2dbde-9cd9-423c-a90a-35b07df6a967"
    )
