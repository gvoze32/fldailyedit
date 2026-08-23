from __future__ import annotations

import json
from pathlib import Path

import pytest

from editor.base_audit import audit_base_roster
from editor.base_refresh import refresh_base
from editor.models import PlayerInfo, TeamData, TeamInfo
from editor.player_spec import load_player_specs
from editor.release_policy import (
    ReleasePolicyError,
    import_usage_csv,
    load_release_policy,
)


def test_base_audit_reports_missing_dastan_with_loan_parent() -> None:
    class Source:
        def get_all_rosters(self):
            return {378: TeamData(378, [2001] + [0] * 39)}

        def get_all_team_info(self):
            return {
                102: TeamInfo(102, "Chelsea FC"),
                378: TeamInfo(378, "Burnley FC"),
            }

        def get_all_players(self):
            return {2001: PlayerInfo(2001, "Other Player")}

    dastan = next(
        spec for spec in load_player_specs() if spec.identity.name == "Dastan Satpaev"
    )
    report = audit_base_roster(Source(), (dastan,))

    assert report.valid is False
    assert report.issue_count == 1
    finding = report.findings[0]
    assert finding.status == "missing"
    assert finding.expected_team_id == 378
    assert finding.actual_team_ids == ()
    assert finding.loan_parent_status == "present"


def test_release_policy_loader_reads_protected_players_and_usage(tmp_path: Path) -> None:
    policy_path = tmp_path / "release_policy.json"
    policy_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source": "fixture",
                "season": "2026/27",
                "as_of": "2026-08-23",
                "protected_players": {"378": [1073003]},
                "usage": {
                    "1073003": {
                        "minutes": 0,
                        "starts": 0,
                        "appearances": 0,
                        "news_mentions": 1,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    policy = load_release_policy(policy_path)

    assert policy.protected_players[378] == frozenset({1073003})
    assert policy.usage[1073003].minutes == 0
    assert policy.source == "fixture"


def test_release_policy_loader_rejects_unknown_usage_fields(tmp_path: Path) -> None:
    policy_path = tmp_path / "release_policy.json"
    policy_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "protected_players": {},
                "usage": {
                    "1073003": {
                        "minutes": 0,
                        "starts": 0,
                        "appearances": 0,
                        "unexpected": 1,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ReleasePolicyError, match="unknown fields"):
        load_release_policy(policy_path)


def test_usage_import_merges_csv_without_dropping_protections(tmp_path: Path) -> None:
    policy_path = tmp_path / "release_policy.json"
    policy_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "protected_players": {"378": [1073003]},
                "usage": {},
            }
        ),
        encoding="utf-8",
    )
    csv_path = tmp_path / "usage.csv"
    csv_path.write_text(
        "player_id,minutes,starts,appearances,news_mentions\n"
        "1073003,0,0,0,2\n",
        encoding="utf-8",
    )

    policy = import_usage_csv(
        csv_path,
        policy_path,
        source="fixture",
        season="2026/27",
        as_of="2026-08-23",
    )

    assert policy.protected_players[378] == frozenset({1073003})
    assert policy.usage[1073003].news_mentions == 2
    assert load_release_policy(policy_path).source == "fixture"

def test_base_refresh_verifies_candidate_without_promoting(
    tmp_path: Path, monkeypatch
) -> None:
    import editor.base_refresh as refresh_module

    candidate = tmp_path / "candidate.EDIT00000000"
    candidate.write_bytes(b"candidate")
    decrypted = tmp_path / "decrypted"
    decrypted.mkdir()
    (decrypted / "data.dat").write_bytes(b"data")
    spec_dir = tmp_path / "players"
    spec_dir.mkdir()

    class FakeEditFile:
        def load(self, _path):
            return None

        def validate_integrity(self):
            return {"valid": True, "errors": []}

        def get_all_rosters(self):
            return {}

        def get_all_team_info(self):
            return {}

        def get_all_players(self):
            return {}

    monkeypatch.setattr(refresh_module.crypto, "decrypt", lambda _path: decrypted)
    monkeypatch.setattr(refresh_module.crypto, "cleanup_temp", lambda _path: None)
    monkeypatch.setattr(refresh_module, "EditFile", FakeEditFile)

    report = refresh_module.refresh_base(
        candidate,
        revision="fixture-revision",
        base_path=tmp_path / "base" / "EDIT00000000",
        manifest_path=tmp_path / "manifest.json",
        spec_dir=spec_dir,
    )

    assert report.promoted is False
    assert report.integrity_valid is True
    assert report.audit.valid is True
    assert not (tmp_path / "base" / "EDIT00000000").exists()
