"""Configuration path regression tests."""

import config


def test_default_edit_file_uses_canonical_base_directory():
    assert config.EDIT_FILE_PATH == config.PROJECT_ROOT / "base" / "EDIT00000000"
    assert config.OUTPUT_FILE_PATH == config.PROJECT_ROOT / "output" / "EDIT00000000"


def test_frozen_build_keeps_backups_and_log_outside_bundle(monkeypatch, tmp_path):
    import importlib
    import sys

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    try:
        importlib.reload(config)
        state_dir = tmp_path / "Local" / "FLDailyEdit"
        assert config.BACKUP_DIR == state_dir / "backups"
        assert config.TRANSFER_LOG_FILE == state_dir / "transfer_log.jsonl"
        assert config.CLUB_IDENTITY_CACHE_FILE == state_dir / "club_identity_cache.json"
    finally:
        monkeypatch.undo()
        importlib.reload(config)
    assert config.BACKUP_DIR == config.PROJECT_ROOT / "backups"
    assert config.TRANSFER_LOG_FILE == config.DATA_DIR / "transfer_log.jsonl"
    assert config.CLUB_IDENTITY_CACHE_FILE == config.STORAGE_DIR / "club_identity_cache.json"


def test_user_state_dir_per_platform(tmp_path):
    home = tmp_path / "home"
    assert config._user_state_dir("darwin", {}, home) == (
        home / "Library" / "Application Support" / "FLDailyEdit"
    )
    assert config._user_state_dir("linux", {}, home) == (
        home / ".local" / "share" / "FLDailyEdit"
    )
    assert config._user_state_dir("linux", {"XDG_DATA_HOME": str(tmp_path / "x")}, home) == (
        tmp_path / "x" / "FLDailyEdit"
    )
    assert config._user_state_dir("win32", {}, home) == (
        home / "AppData" / "Local" / "FLDailyEdit"
    )
