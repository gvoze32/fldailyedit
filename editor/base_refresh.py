"""Download, verify, audit, and optionally promote one EDIT base safely."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import config
from editor import crypto
from editor.base_audit import BaseRosterAuditReport, audit_base_roster
from editor.editfile import EditFile
from editor.player_spec import PlayerSpecError, load_player_specs

_MAX_DOWNLOAD_BYTES = 64 * 1024 * 1024


class BaseRefreshError(RuntimeError):
    """A candidate base could not be verified or promoted safely."""


@dataclass(frozen=True, slots=True)
class BaseRefreshReport:
    candidate: str
    size: int
    sha256: str
    revision: str
    integrity_valid: bool
    audit: BaseRosterAuditReport
    promoted: bool
    backup_path: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "candidate": self.candidate,
            "size": self.size,
            "sha256": self.sha256,
            "revision": self.revision,
            "integrity_valid": self.integrity_valid,
            "audit": self.audit.to_dict(),
            "promoted": self.promoted,
            "backup_path": self.backup_path,
        }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _decrypted_data_file(directory: Path) -> Path:
    direct = directory / "data.dat"
    if direct.is_file():
        return direct
    candidates = tuple(directory.glob("*.dat"))
    if not candidates:
        raise BaseRefreshError("decryption produced no .dat file")
    return max(candidates, key=lambda path: path.stat().st_size)


def _download(url: str, destination: Path) -> Path:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise BaseRefreshError("base source URL must use HTTPS")
    request = Request(url, headers={"User-Agent": "FLDailyEdit/base-refresh"})
    temporary: Path | None = None
    try:
        with urlopen(request, timeout=60) as response:
            content_length = response.headers.get("Content-Length")
            if content_length is not None and int(content_length) > _MAX_DOWNLOAD_BYTES:
                raise BaseRefreshError("base download exceeds the 64 MiB safety limit")
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=destination.parent, prefix=f".{destination.name}.", delete=False
            ) as handle:
                temporary = Path(handle.name)
                total = 0
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > _MAX_DOWNLOAD_BYTES:
                        raise BaseRefreshError("base download exceeds the 64 MiB safety limit")
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
        os.replace(temporary, destination)
        temporary = None
        return destination
    except BaseRefreshError:
        raise
    except Exception as exc:
        raise BaseRefreshError(f"could not download base source: {exc}") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=destination.parent, prefix=f".{destination.name}.", delete=False
        ) as handle:
            temporary = Path(handle.name)
            with source.open("rb") as source_handle:
                shutil.copyfileobj(source_handle, handle, length=1024 * 1024)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _atomic_manifest(path: Path, revision: str, sha256: str) -> None:
    payload = json.dumps(
        {"revision": revision, "sha256": sha256}, indent=2, sort_keys=False
    ) + "\n"
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def refresh_base(
    source: str | Path,
    *,
    revision: str,
    base_path: str | Path | None = None,
    manifest_path: str | Path | None = None,
    spec_dir: str | Path | None = None,
    as_of: date | None = None,
    promote: bool = False,
    strict_audit: bool = False,
) -> BaseRefreshReport:
    """Verify one local/HTTPS candidate and optionally replace the bundled base."""

    target_base = (
        Path(base_path)
        if base_path is not None
        else Path(getattr(config, "BASE_EDIT_PATH", config.EDIT_FILE_PATH))
    )
    target_manifest = (
        Path(manifest_path)
        if manifest_path is not None
        else config.BASE_MANIFEST_FILE
    )
    source_path = Path(source)
    temporary_download: Path | None = None
    if source_path.is_file():
        candidate = source_path
    else:
        with tempfile.TemporaryDirectory(prefix="fldailyedit-base-refresh-") as directory:
            temporary_download = Path(directory) / "EDIT00000000"
            _download(str(source), temporary_download)
            return refresh_base(
                temporary_download,
                revision=revision,
                base_path=target_base,
                manifest_path=target_manifest,
                spec_dir=spec_dir,
                as_of=as_of,
                promote=promote,
                strict_audit=strict_audit,
            )

    candidate_sha256 = _sha256(candidate)
    decrypted = None
    try:
        decrypted = crypto.decrypt(candidate)
        edit_file = EditFile()
        edit_file.load(_decrypted_data_file(decrypted))
        integrity = edit_file.validate_integrity()
        if not integrity["valid"]:
            raise BaseRefreshError(
                "candidate failed edit-file integrity validation: "
                + "; ".join(str(error) for error in integrity["errors"][:5])
            )
        specs = load_player_specs(spec_dir)
        audit = audit_base_roster(edit_file, specs, as_of=as_of)
        if strict_audit and not audit.valid:
            raise BaseRefreshError(
                f"candidate failed strict base audit ({audit.issue_count} issue(s))"
            )
    except BaseRefreshError:
        raise
    except Exception as exc:
        raise BaseRefreshError(str(exc)) from exc
    finally:
        if decrypted is not None:
            crypto.cleanup_temp(decrypted)

    backup_path: Path | None = None
    if promote:
        old_base = target_base.read_bytes() if target_base.is_file() else None
        old_manifest = target_manifest.read_bytes() if target_manifest.is_file() else None
        if target_base.is_file():
            backup_path = target_base.with_suffix(
                target_base.suffix + f".bak.{date.today().isoformat()}"
            )
            shutil.copy2(target_base, backup_path)
        try:
            _atomic_copy(candidate, target_base)
            _atomic_manifest(target_manifest, revision, candidate_sha256)
        except Exception as exc:
            if old_base is not None:
                target_base.write_bytes(old_base)
            if old_manifest is not None:
                target_manifest.write_bytes(old_manifest)
            raise BaseRefreshError(f"base promotion rolled back: {exc}") from exc

    return BaseRefreshReport(
        candidate=str(candidate),
        size=candidate.stat().st_size,
        sha256=candidate_sha256,
        revision=revision,
        integrity_valid=True,
        audit=audit,
        promoted=promote,
        backup_path=None if backup_path is None else str(backup_path),
    )


__all__ = ("BaseRefreshError", "BaseRefreshReport", "refresh_base")
