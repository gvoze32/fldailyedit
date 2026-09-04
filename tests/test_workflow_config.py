from __future__ import annotations

import re
from pathlib import Path


CI_PATH = Path(".github/workflows/ci.yml")
SYNC_WORKFLOW_PATHS = (
    Path(".github/workflows/sync-fast.yml"),
    Path(".github/workflows/sync-deep.yml"),
)
INSTALLER_WORKFLOW_PATH = Path(".github/workflows/build-installer.yml")
INSTALLER_SPEC_PATH = Path("FLDailyEditInstaller.spec")
PYPROJECT_PATH = Path("pyproject.toml")


def test_sync_workflows_validate_run_and_package_transfer_only_saves():
    workflows = (
        (Path(".github/workflows/sync-fast.yml"), "Fast", "fast"),
        (Path(".github/workflows/sync-deep.yml"), "Deep", "deep"),
    )

    for path, display_channel, channel in workflows:
        text = path.read_text(encoding="utf-8")
        sync, publish = text.split("\n  publish:\n", 1)
        run_position = sync.index("          python run.py run \\")
        final_validation_position = sync.index("      - name: Validate final save")
        package_position = text.index(
            f"      - name: Package public {display_channel} save"
        )

        assert "players" not in sync.lower()
        assert "base-audit" not in sync
        assert "base_manifest" not in sync
        assert run_position < final_validation_position < package_position
        assert "python run.py validate --edit-file base/EDIT00000000" in sync
        assert "python run.py validate --edit-file output/EDIT00000000" in sync
        assert "cp base/EDIT00000000 output/EDIT00000000" in sync
        assert "python tools/build_release_asset.py package" in text
        assert f"--channel {channel}" in text
        assert "output/base_roster_audit.json" not in text
        assert "--allow-create" not in text
        assert "players" not in publish.lower()


def test_fast_and_deep_sync_workflows_differ_only_by_channel_and_deep_mode():
    fast = Path(".github/workflows/sync-fast.yml").read_text(encoding="utf-8")
    deep = Path(".github/workflows/sync-deep.yml").read_text(encoding="utf-8")
    release_notes = "Validated Fast and Deep option files for FL Daily Edit."
    normalized_fast = (
        fast.replace(release_notes, "{RELEASE_NOTES}")
        .replace("Fast", "{DISPLAY}")
        .replace("fast", "{channel}")
    )
    normalized_deep = (
        deep.replace(release_notes, "{RELEASE_NOTES}")
        .replace("            --deep \\\n", "")
        .replace("Deep", "{DISPLAY}")
        .replace("deep", "{channel}")
    )

    assert normalized_fast == normalized_deep


def test_ci_runs_tests_without_contribution_validation_step():
    text = CI_PATH.read_text(encoding="utf-8")

    assert "pytest -v" in text
    assert "runs-on: ubuntu-latest" in text
    assert 'python-version: "3.12"' in text
    assert "windows" not in text.lower()
    assert "matrix." not in text


def test_contribution_surfaces_are_removed_from_source_and_workflows():
    removed_paths = (
        Path("editor/player_codec.py"),
        Path("editor/player_ovr.py"),
        Path("editor/player_spec.py"),
        Path("editor/base_audit.py"),
        Path("editor/base_refresh.py"),
        Path("tools/generate_player_draft.py"),
        Path("tools/player_draft_diff.py"),
        Path("tools/player_proposal_review.py"),
        Path("tools/player_proposal_resolution.py"),
        Path("scraper/pes_retro_stats.py"),
        Path("scraper/pes_retro_snapshot.py"),
        Path("scraper/pes21_proposal.py"),
    )
    assert all(not path.exists() for path in removed_paths)

    run_source = Path("run.py").read_text(encoding="utf-8").lower()
    assert 'sub.add_parser("players"' not in run_source
    assert "player_spec" not in run_source
    assert "playerappearance" not in run_source


def test_installer_build_metadata_discovers_package_and_pins_pyinstaller():
    text = PYPROJECT_PATH.read_text(encoding="utf-8")
    package_find = text.split("[tool.setuptools.packages.find]", 1)[1].split(
        "\n[", 1
    )[0]
    include_line = re.search(r"(?m)^include = \[(.+)\]$", package_find)
    assert include_line is not None
    assert "installer*" in re.findall(r'"([^"]+)"', include_line.group(1))

    dependency_block = text.split("installer-build = [", 1)[1].split("]", 1)[0]
    assert re.findall(r'"([^"]+)"', dependency_block) == ["pyinstaller>=6.14,<7"]


def test_installer_spec_is_one_file_windowed_and_excludes_sensitive_payloads():
    text = INSTALLER_SPEC_PATH.read_text(encoding="utf-8")

    assert "Analysis(" in text
    assert '["installer/__main__.py"]' in text
    assert 'pathex=["."]' in text
    assert "EXE(" in text
    assert 'name="FLDailyEditInstaller"' in text
    assert "console=False" in text
    assert "debug=False" in text
    assert "strip=False" in text
    assert "upx=False" in text
    assert "COLLECT(" not in text

    for resource in (
        "data/major_clubs.json",
        "data/fotmob_teams_validated.json",
        "data/name_overrides.json",
        "data/team_aliases.json",
        "data/FL262_teams.txt",
        "vendor/pesXdecrypter/decrypter21.exe",
        "vendor/pesXdecrypter/encrypter21.exe",
    ):
        assert resource in text

    lowered = text.lower()
    for forbidden in (
        "edit00000000",
        "transfer_summary",
        "credentials",
    ):
        assert forbidden not in lowered


def test_installer_workflow_builds_tests_and_smoke_tests_on_windows():
    text = INSTALLER_WORKFLOW_PATH.read_text(encoding="utf-8")
    build = text.split("\n  publish:\n", 1)[0]

    assert "workflow_dispatch:" in text
    assert "branches: [main]" in text
    for path_filter in (
        "installer/**",
        "tests/test_installer_*.py",
        "tests/test_release_asset.py",
        "FLDailyEditInstaller.spec",
        "pyproject.toml",
        ".github/workflows/build-installer.yml",
        "tools/publish_release_assets.py",
        "tests/test_release_publisher.py",
        "config.py",
        "local_update.py",
        "run.py",
        "editor/**",
        "scraper/**",
    ):
        assert path_filter in text

    assert "runs-on: windows-latest" in build
    assert 'python-version: "3.12"' in build
    assert 'python -m pip install -e ".[installer-build]"' in build
    assert 'python -m pip install -e ".[dev]"' in build
    for path_filter in (
        "data/major_clubs.json",
        "data/fotmob_teams_validated.json",
        "data/name_overrides.json",
        "data/team_aliases.json",
        "data/FL262_teams.txt",
    ):
        assert f'      - "{path_filter}"' in text
    assert "pesXdecrypter_2021.7z" in build
    assert "201800b731a8c90109b30afeef26fb0cdcd552d2910b322bbc253b000d6aa3a6" in build
    assert "decrypter21.exe" in build
    assert "encrypter21.exe" in build
    for test_path in (
        "tests/test_installer_catalog.py",
        "tests/test_installer_paths.py",
        "tests/test_installer_install.py",
        "tests/test_installer_app.py",
        "tests/test_release_asset.py",
        "tests/test_workflow_config.py",
    ):
        assert test_path in build
    assert "pyinstaller --clean --noconfirm FLDailyEditInstaller.spec" in build
    assert (
        'Start-Process -FilePath "dist\\FLDailyEditInstaller.exe" '
        '-ArgumentList "--self-test" -Wait -PassThru'
    ) in build
    assert "if ($process.ExitCode -ne 0)" in build
    assert 'throw "Installer self-test exited $($process.ExitCode)"' in build
    assert (
        'Get-FileHash -Path "dist\\FLDailyEditInstaller.zip" -Algorithm SHA256'
        in build
    )
    assert "Compress-Archive" in build
    assert 'DestinationPath $zip' in build
    assert "ZipFile]::OpenRead($zip)" in build
    assert "dist/FLDailyEditInstaller.zip" in build
    assert "dist/FLDailyEditInstaller.zip.sha256" in build
    assert "dist/FLDailyEditInstaller.exe.sha256" not in build
    assert "uses: actions/upload-artifact@v7" in build
    assert "retention-days: 1" in build


def test_installer_publish_job_is_serialized_and_uploads_exact_release_assets():
    text = INSTALLER_WORKFLOW_PATH.read_text(encoding="utf-8")
    publish = "\n  publish:\n" + text.split("\n  publish:\n", 1)[1]

    assert "needs: build" in publish
    assert "runs-on: ubuntu-latest" in publish
    assert "contents: write" in publish
    assert "group: fldailyedit-latest-release" in publish
    assert "cancel-in-progress: false" in publish
    assert "uses: actions/checkout@v7" in publish
    assert "GH_REPO: ${{ github.repository }}" in publish
    assert (
        'gh release view latest --repo "$GH_REPO" >/dev/null 2>&1 || '
        'gh release create latest --repo "$GH_REPO" '
        '--title "Latest FL Daily Edit" '
        '--notes "Validated FL Daily Edit release assets."'
    ) in publish
    assert (
        "python tools/publish_release_assets.py \\\n"
        '            --repo "$GH_REPO" \\\n'
        "            --tag latest \\\n"
        "            release-payload/FLDailyEditInstaller.zip \\\n"
        "            release-payload/FLDailyEditInstaller.zip.sha256"
    ) in publish
    assert "Remove legacy standalone installer assets" in publish
    assert (
        'gh release delete-asset latest "$legacy" --repo "$GH_REPO" --yes'
        in publish
    )
    assert "release-payload/FLDailyEditInstaller.exe" not in publish
    assert "gh release upload" not in publish
    for line in publish.splitlines():
        if "gh release " in line:
            assert '--repo "$GH_REPO"' in line

    actions = re.findall(r"(?m)^\s*uses:\s+([^@\s]+)@", text)
    assert actions
    assert all(action.startswith("actions/") for action in actions)
