"""Backup safety regression tests."""

from editor.backup import create_backup


def test_backups_are_content_verified_and_collision_safe(monkeypatch, tmp_path):
    import config

    source = tmp_path / "EDIT00000000"
    source.write_bytes(b"edit-content")
    backup_dir = tmp_path / "backups"
    monkeypatch.setattr(config, "BACKUP_DIR", backup_dir)

    first = create_backup(source)
    second = create_backup(source)

    assert first != second
    assert first.read_bytes() == source.read_bytes()
    assert second.read_bytes() == source.read_bytes()


def test_pruning_keeps_new_backup_of_file_with_old_mtime(monkeypatch, tmp_path):
    import os

    import config

    source = tmp_path / "EDIT00000000"
    source.write_bytes(b"edit-content")
    os.utime(source, (1_000_000_000, 1_000_000_000))
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    older = backup_dir / "EDIT00000000.bak.20200101_000000_000000"
    newer = backup_dir / "EDIT00000000.bak.20200102_000000_000000"
    older.write_bytes(b"old")
    newer.write_bytes(b"newer")
    monkeypatch.setattr(config, "BACKUP_DIR", backup_dir)
    monkeypatch.setattr(config, "MAX_BACKUPS", 2)

    created = create_backup(source)

    assert created.read_bytes() == b"edit-content"
    assert newer.exists()
    assert not older.exists()
