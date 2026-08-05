from __future__ import annotations

import os
import stat
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


FL_RELATIVE = Path(
    "Documents/KONAMI/eFootball PES 2021 SEASON UPDATE/2026/save"
)
PES_PARENT_RELATIVE = Path(
    "Documents/KONAMI/eFootball PES 2021 SEASON UPDATE"
)

_ENVIRONMENT_ROOTS = ("USERPROFILE", "OneDrive", "OneDriveConsumer")
_GAME_NAMES = {
    "fl26-u2.2-national-squads": "Football Life 2026",
    "pes2021-vanilla": "PES 2021",
}


class GameTarget(str, Enum):
    FL26 = "fl26-u2.2-national-squads"
    PES2021 = "pes2021-vanilla"


@dataclass(frozen=True, slots=True)
class SaveLocation:
    target: GameTarget
    game_name: str
    save_directory: Path

    @property
    def edit_file(self) -> Path:
        return self.save_directory / "EDIT00000000"


class DestinationError(OSError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


_REPARSE_POINT_ATTRIBUTE = getattr(
    stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400
)


def _is_reparse_status(path_status: os.stat_result) -> bool:
    return stat.S_ISLNK(path_status.st_mode) or bool(
        getattr(path_status, "st_file_attributes", 0)
        & _REPARSE_POINT_ATTRIBUTE
    )


def _reject_reparse_point(path: Path, description: str) -> None:
    try:
        path_status = path.lstat()
    except FileNotFoundError:
        return
    except PermissionError as error:
        raise DestinationError(
            "permission_denied", f"{description} cannot be accessed: {path}"
        ) from error
    except OSError as error:
        raise DestinationError(
            "not_writable", f"{description} cannot be inspected: {path}"
        ) from error
    if _is_reparse_status(path_status):
        raise DestinationError(
            "reparse_point",
            f"{description} must not be a symbolic link, junction, or reparse point: {path}",
        )

def _is_directory(path: Path) -> bool:
    try:
        return path.is_dir()
    except OSError:
        return False


def _deduplication_key(path: Path) -> str:
    return str(path).casefold()


def discover_save_locations(
    environment: Mapping[str, str] | None = None,
) -> tuple[SaveLocation, ...]:
    source = os.environ if environment is None else environment
    locations: list[SaveLocation] = []
    seen_paths: set[str] = set()

    def add(target: GameTarget, save_directory: Path) -> None:
        key = _deduplication_key(save_directory)
        if key in seen_paths or not _is_directory(save_directory):
            return
        seen_paths.add(key)
        locations.append(
            SaveLocation(target, _GAME_NAMES[target.value], save_directory)
        )

    for variable in _ENVIRONMENT_ROOTS:
        root_value = source.get(variable)
        if not root_value:
            continue
        root = Path(root_value)
        add(GameTarget.FL26, root / FL_RELATIVE)

        parent = root / PES_PARENT_RELATIVE
        try:
            children = sorted(
                parent.iterdir(),
                key=lambda child: (str(child).casefold(), str(child)),
            )
        except OSError:
            continue
        for child in children:
            if (
                child.name == "2026"
                or not child.name.isascii()
                or not child.name.isdecimal()
            ):
                continue
            add(GameTarget.PES2021, child / "save")

    target_order = {GameTarget.FL26: 0, GameTarget.PES2021: 1}
    locations.sort(
        key=lambda location: (
            target_order[location.target],
            str(location.save_directory).casefold(),
            str(location.save_directory),
        )
    )
    return tuple(locations)


def validate_destination(path: Path, target: GameTarget) -> Path:
    _reject_reparse_point(path, "destination")
    try:
        path_status = path.lstat()
    except (FileNotFoundError, NotADirectoryError) as error:
        raise DestinationError(
            "missing", f"destination does not exist: {path}"
        ) from error
    except PermissionError as error:
        raise DestinationError(
            "permission_denied", f"destination cannot be accessed: {path}"
        ) from error
    except OSError as error:
        raise DestinationError(
            "not_writable", f"destination cannot be inspected: {path}"
        ) from error

    if not stat.S_ISDIR(path_status.st_mode):
        raise DestinationError(
            "not_directory", f"destination is not a directory: {path}"
        )
    if path.name.casefold() != "save":
        raise DestinationError(
            "not_save", f"destination directory must be named save: {path}"
        )

    try:
        normalized = path.resolve(strict=True)
    except (FileNotFoundError, NotADirectoryError) as error:
        raise DestinationError(
            "missing", f"destination does not exist: {path}"
        ) from error
    except PermissionError as error:
        raise DestinationError(
            "permission_denied", f"destination cannot be accessed: {path}"
        ) from error
    except OSError as error:
        raise DestinationError(
            "not_writable", f"destination cannot be normalized: {path}"
        ) from error

    _reject_reparse_point(normalized, "destination")
    _reject_reparse_point(normalized / "EDIT00000000", "save file")
    backup_directory = normalized / "FLDailyEditBackups"
    _reject_reparse_point(backup_directory, "backup directory")
    if backup_directory.exists() and not backup_directory.is_dir():
        raise DestinationError(
            "not_directory",
            f"backup path is not a directory: {backup_directory}",
        )

    probe_descriptor: int | None = None
    probe_path: Path | None = None
    try:
        probe_descriptor, probe_name = tempfile.mkstemp(
            prefix=".fldailyedit-write-",
            suffix=".tmp",
            dir=normalized,
        )
        probe_path = Path(probe_name)
        os.close(probe_descriptor)
        probe_descriptor = None
    except FileNotFoundError as error:
        raise DestinationError(
            "missing", f"destination disappeared during write probe: {normalized}"
        ) from error
    except NotADirectoryError as error:
        raise DestinationError(
            "not_directory",
            f"destination stopped being a directory during write probe: {normalized}",
        ) from error
    except PermissionError as error:
        raise DestinationError(
            "permission_denied",
            f"destination is not writable for {target.value}: {normalized}",
        ) from error
    except OSError as error:
        raise DestinationError(
            "not_writable",
            f"destination write probe failed for {target.value}: {normalized}",
        ) from error
    finally:
        if probe_descriptor is not None:
            try:
                os.close(probe_descriptor)
            except OSError:
                pass
        if probe_path is not None:
            try:
                probe_path.unlink(missing_ok=True)
            except OSError as error:
                raise DestinationError(
                    "cleanup_failed",
                    f"destination write probe could not be removed: {probe_path}",
                ) from error

    return normalized
