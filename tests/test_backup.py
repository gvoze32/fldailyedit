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
