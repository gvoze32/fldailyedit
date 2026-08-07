import subprocess
import sys
from pathlib import Path

import pytest

from tools.check_player_spec_pr import (
    PlayerContributionError,
    validate_player_pr_changes,
)


@pytest.mark.parametrize("status", ["A", "M"])
@pytest.mark.parametrize("slug", ["a", "marco-palestra", "player-2"])
def test_player_contribution_accepts_one_canonical_player_file(status, slug):
    path = Path(f"players/{slug}.json")

    assert validate_player_pr_changes([f"{status}\t{path.as_posix()}"]) == path


@pytest.mark.parametrize(
    "changes",
    [
        [],
        ["M\tREADME.md"],
        ["D\tdocs/old.txt", "A\tdocs/new.txt"],
        ["R100\tdocs/old.txt\tdocs/new.txt"],
        ["R050\tdocs/old.txt\tdocs/new.txt"],
        ["C075\tdocs/source.txt\tdocs/copy.txt"],
        ["R000\tdocs/zero.txt\tdocs/still-zero.txt"],
        ["C100\tdocs/source.txt\tdocs/copy.txt"],
    ],
)
def test_non_player_pull_request_needs_no_player_guard(changes):
    assert validate_player_pr_changes(changes) is None


@pytest.mark.parametrize(
    "changes",
    [
        ["A\tplayers/a.json", "A\tplayers/b.json"],
        ["A\tplayers/a.json", "M\trun.py"],
        ["M\trun.py", "A\tplayers/a.json"],
        ["A\tplayers/a.json", "M\t.github/workflows/ci.yml"],
        ["A\tplayers/a.json", "M\tdata/base_manifest.json"],
    ],
)
def test_player_contribution_rejects_every_other_whole_pr_change(changes):
    with pytest.raises(PlayerContributionError):
        validate_player_pr_changes(changes)


@pytest.mark.parametrize(
    "record",
    [
        "A\tplayers/A.json",
        "A\tplayers/marco_Palestra.json",
        "A\tplayers/-marco.json",
        "A\tplayers/marco-.json",
        "A\tplayers/marco--palestra.json",
        "A\tplayers/marco.txt",
        "A\tplayers/marco.JSON",
        "A\tplayers/marco.json.bak",
        "A\tplayers/nested/marco.json",
        "A\tplayers/.json",
    ],
)
def test_player_contribution_rejects_noncanonical_player_paths(record):
    with pytest.raises(PlayerContributionError):
        validate_player_pr_changes([record])


@pytest.mark.parametrize(
    "record",
    [
        "A\t/players/marco.json",
        "A\t./players/marco.json",
        "A\tplayers/./marco.json",
        "A\tplayers/../players/marco.json",
        "A\tplayers//marco.json",
        "M\t../README.md",
        "M\t/README.md",
    ],
)
def test_changed_paths_must_be_relative_canonical_posix_paths(record):
    with pytest.raises(PlayerContributionError):
        validate_player_pr_changes([record])


@pytest.mark.parametrize(
    "record",
    [
        "A\tplayers/marco\x00.json",
        "A\tplayers/marco\r.json",
        "A\tplayers/marco\n.json",
        "A\tplayers/marco\x7f.json",
        'A\t"players/marco\\t.json"',
    ],
)
def test_changed_paths_reject_controls_and_git_quoted_paths(record):
    with pytest.raises(PlayerContributionError):
        validate_player_pr_changes([record])


@pytest.mark.parametrize(
    "record",
    [
        "D\tplayers/marco-palestra.json",
        "T\tplayers/marco-palestra.json",
        "U\tplayers/marco-palestra.json",
        "X\tplayers/marco-palestra.json",
        "B\tplayers/marco-palestra.json",
        "R100\tplayers/old.json\tplayers/new.json",
        "R050\tREADME.md\tplayers/new.json",
        "C100\tplayers/old.json\tplayers/copy.json",
        "C75\tREADME.md\tplayers/copy.json",
    ],
)
def test_player_contribution_rejects_non_add_or_modify_statuses(record):
    with pytest.raises(PlayerContributionError):
        validate_player_pr_changes([record])


@pytest.mark.parametrize(
    "record",
    [
        "",
        "A",
        "A\t",
        "\tplayers/a.json",
        "Z\tplayers/a.json",
        "AA\tplayers/a.json",
        "R\tplayers/old.json\tplayers/new.json",
        "R101\tplayers/old.json\tplayers/new.json",
        "R100\tplayers/old.json",
        "A\tplayers/a.json\textra",
        "R5\tdocs/old.txt\tdocs/new.txt",
        "R50\tdocs/old.txt\tdocs/new.txt",
        "C99\tdocs/source.txt\tdocs/copy.txt",
        "B\tREADME.md",
    ],
)
def test_player_contribution_rejects_malformed_name_status_records(record):
    with pytest.raises(PlayerContributionError):
        validate_player_pr_changes([record])


def test_cli_reads_only_the_caller_supplied_changes_file(tmp_path):
    changes_file = tmp_path / "changes.tsv"
    changes_file.write_bytes(b"A\tplayers/marco-palestra.json\n")

    result = subprocess.run(
        [
            sys.executable,
            "tools/check_player_spec_pr.py",
            "--changes-file",
            str(changes_file),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "players/marco-palestra.json"
    assert result.stderr == ""


def test_cli_rejects_a_malformed_changes_file(tmp_path):
    changes_file = tmp_path / "changes.tsv"
    changes_file.write_bytes(b"A\tplayers/marco-palestra.json\r\n")

    result = subprocess.run(
        [
            sys.executable,
            "tools/check_player_spec_pr.py",
            "--changes-file",
            str(changes_file),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "control character" in result.stderr
