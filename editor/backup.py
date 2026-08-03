"""
Backup management for edit files.

Creates timestamped backups before any modifications.
Auto-cleans old backups beyond the configured limit.
"""
import hashlib
import logging
import shutil
from datetime import datetime
from pathlib import Path

import config

logger = logging.getLogger(__name__)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_backup(edit_file_path: Path) -> Path:
    """
    Create a timestamped backup of the edit file.

    Args:
        edit_file_path: Path to the edit file to back up.

    Returns:
        Path to the backup file.

    Raises:
        FileNotFoundError: If the edit file doesn't exist.
    """
    edit_file_path = Path(edit_file_path)
    if not edit_file_path.exists():
        raise FileNotFoundError(f"Cannot backup — file not found: {edit_file_path}")

    backup_dir = config.BACKUP_DIR
    backup_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup_name = f"{edit_file_path.name}.bak.{timestamp}"
    backup_path = backup_dir / backup_name

    shutil.copy2(edit_file_path, backup_path)

    # Verify backup
    orig_size = edit_file_path.stat().st_size
    backup_size = backup_path.stat().st_size
    if orig_size != backup_size or _sha256(edit_file_path) != _sha256(backup_path):
        logger.error(
            f"Backup verification mismatch! Original: {orig_size}, Backup: {backup_size}"
        )
        raise RuntimeError("Backup verification failed — content does not match")

    logger.info(f"Backup created: {backup_path} ({backup_size:,} bytes)")

    # Auto-cleanup old backups
    _cleanup_old_backups(edit_file_path.name)

    return backup_path


def _cleanup_old_backups(original_filename: str):
    """Delete oldest backups beyond the configured limit."""
    backup_dir = config.BACKUP_DIR
    if not backup_dir.exists():
        return

    pattern = f"{original_filename}.bak.*"
    backups = sorted(backup_dir.glob(pattern), key=lambda p: p.stat().st_mtime)

    while len(backups) > config.MAX_BACKUPS:
        oldest = backups.pop(0)
        oldest.unlink()
        logger.info(f"Removed old backup: {oldest.name}")


def list_backups(original_filename: str = "edit00000000") -> list[Path]:
    """List all existing backups, newest first."""
    backup_dir = config.BACKUP_DIR
    if not backup_dir.exists():
        return []

    pattern = f"{original_filename}.bak.*"
    return sorted(backup_dir.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)


def restore_backup(backup_path: Path, restore_to: Path) -> Path:
    """
    Restore a backup file.

    Args:
        backup_path: Path to the backup file.
        restore_to: Where to restore the file.

    Returns:
        Path to the restored file.
    """
    backup_path = Path(backup_path)
    restore_to = Path(restore_to)

    if not backup_path.exists():
        raise FileNotFoundError(f"Backup not found: {backup_path}")

    shutil.copy2(backup_path, restore_to)
    logger.info(f"Restored backup {backup_path.name} → {restore_to}")
    return restore_to
