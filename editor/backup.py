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
    _cleanup_old_backups(edit_file_path.name, keep=backup_path)

    return backup_path


def _cleanup_old_backups(original_filename: str, keep: Path):
    """Delete oldest backups beyond the configured limit, never ``keep``.

    Backups are ordered by the creation timestamp embedded in their name;
    file mtimes are unreliable because copies may preserve the source mtime.
    """
    backup_dir = config.BACKUP_DIR
    prefix = f"{original_filename}.bak."
    backups = sorted(
        (path for path in backup_dir.glob(f"{prefix}*") if path != keep),
        key=lambda p: p.name[len(prefix):],
    )

    while len(backups) + 1 > config.MAX_BACKUPS and backups:
        oldest = backups.pop(0)
        oldest.unlink()
        logger.info(f"Removed old backup: {oldest.name}")
