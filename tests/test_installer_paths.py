from __future__ import annotations

import os
from pathlib import Path

import pytest

import installer.paths as paths_module
from installer.paths import (
    DestinationError,
    GameTarget,
    SaveLocation,
    discover_save_locations,
    validate_destination,
)


FL_RELATIVE = Path(
    "Documents/KONAMI/eFootball PES 2021 SEASON UPDATE/2026/save"
)
PES_PARENT_RELATIVE = Path(
    "Documents/KONAMI/eFootball PES 2021 SEASON UPDATE"
)


def _make_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_discovers_exact_save_directories_in_stable_game_and_path_order(
    tmp_path: Path,
) -> None:
    profile = tmp_path / "ZProfile"
    one_drive = tmp_path / "AOneDrive"
    consumer = tmp_path / "MOneDriveConsumer"

    profile_fl = _make_directory(profile / FL_RELATIVE)
    one_drive_fl = _make_directory(one_drive / FL_RELATIVE)
    profile_pes_200 = _make_directory(profile / PES_PARENT_RELATIVE / "200" / "save")
    profile_pes_100 = _make_directory(profile / PES_PARENT_RELATIVE / "100" / "save")
    consumer_pes_300 = _make_directory(
        consumer / PES_PARENT_RELATIVE / "300" / "save"
    )

    # These nearby paths must not be mistaken for exact save candidates.
    _make_directory(profile / PES_PARENT_RELATIVE / "999")
    _make_directory(profile / PES_PARENT_RELATIVE / "player" / "save")
    _make_directory(profile / PES_PARENT_RELATIVE / "400" / "nested" / "save")
    save_file = consumer / PES_PARENT_RELATIVE / "500" / "save"
    save_file.parent.mkdir(parents=True)
    save_file.write_bytes(b"")

    locations = discover_save_locations(
        {
            "USERPROFILE": str(profile),
            "OneDrive": str(one_drive),
            "OneDriveConsumer": str(consumer),
        }
    )

    assert locations == (
        SaveLocation(GameTarget.FL26, "Football Life 2026", one_drive_fl),
        SaveLocation(GameTarget.FL26, "Football Life 2026", profile_fl),
        SaveLocation(GameTarget.PES2021, "PES 2021", consumer_pes_300),
        SaveLocation(GameTarget.PES2021, "PES 2021", profile_pes_100),
        SaveLocation(GameTarget.PES2021, "PES 2021", profile_pes_200),
    )


def test_deduplicates_candidate_paths_case_insensitively_by_root_priority(
    tmp_path: Path,
) -> None:
    upper_root = tmp_path / "DocumentsRoot"
    lower_root = tmp_path / "documentsroot"
    upper_fl = _make_directory(upper_root / FL_RELATIVE)
    upper_pes = _make_directory(upper_root / PES_PARENT_RELATIVE / "123" / "save")
    _make_directory(lower_root / FL_RELATIVE)
    _make_directory(lower_root / PES_PARENT_RELATIVE / "123" / "save")

    locations = discover_save_locations(
        {
            "USERPROFILE": str(upper_root),
            "OneDrive": str(lower_root),
            "OneDriveConsumer": str(upper_root),
        }
    )

    assert locations == (
        SaveLocation(GameTarget.FL26, "Football Life 2026", upper_fl),
        SaveLocation(GameTarget.PES2021, "PES 2021", upper_pes),
    )


def test_uses_process_environment_when_no_mapping_is_supplied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = tmp_path / "Profile"
    fl_save = _make_directory(profile / FL_RELATIVE)
    monkeypatch.setenv("USERPROFILE", str(profile))
    monkeypatch.delenv("OneDrive", raising=False)
    monkeypatch.delenv("OneDriveConsumer", raising=False)

    assert discover_save_locations() == (
        SaveLocation(GameTarget.FL26, "Football Life 2026", fl_save),
    )


def test_empty_or_missing_roots_produce_no_locations(tmp_path: Path) -> None:
    assert discover_save_locations(
        {
            "USERPROFILE": "",
            "OneDrive": str(tmp_path / "missing"),
        }
    ) == ()


def test_save_location_exposes_the_edit_file_path(tmp_path: Path) -> None:
    save_directory = tmp_path / "save"
    location = SaveLocation(
        GameTarget.FL26,
        "Football Life 2026",
        save_directory,
    )

    assert location.edit_file == save_directory / "EDIT00000000"
    assert location.target.value == "fl26-u2.2-national-squads"
    assert GameTarget.PES2021.value == "pes2021-vanilla"


def test_validate_destination_probes_real_writability_and_cleans_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    save_directory = _make_directory(tmp_path / "profile" / "SaVe")
    observed_probes: list[tuple[Path, int]] = []
    real_open = os.open

    def recording_open(path: Path, flags: int, mode: int = 0o777) -> int:
        descriptor = real_open(path, flags, mode)
        observed_probes.append((Path(path), os.fstat(descriptor).st_size))
        return descriptor

    monkeypatch.setattr(paths_module.os, "open", recording_open)
    monkeypatch.chdir(tmp_path)

    result = validate_destination(
        Path("profile") / "SaVe",
        GameTarget.FL26,
    )

    assert result == save_directory.resolve()
    assert len(observed_probes) == 1
    assert observed_probes[0][1] == 0
    assert observed_probes[0][0].parent == result
    assert not observed_probes[0][0].exists()
    assert list(save_directory.iterdir()) == []


def test_validate_destination_does_not_require_an_existing_edit_file(
    tmp_path: Path,
) -> None:
    save_directory = _make_directory(tmp_path / "save")

    assert validate_destination(save_directory, GameTarget.PES2021) == (
        save_directory.resolve()
    )
    assert not (save_directory / "EDIT00000000").exists()
    assert list(save_directory.iterdir()) == []


def test_validate_destination_rejects_missing_path_without_creating_it(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing" / "save"

    with pytest.raises(DestinationError) as caught:
        validate_destination(missing, GameTarget.FL26)

    assert caught.value.code == "missing"
    assert not missing.exists()
    assert not missing.parent.exists()


def test_validate_destination_rejects_file_with_directory_code(
    tmp_path: Path,
) -> None:
    save_file = tmp_path / "save"
    save_file.write_bytes(b"existing")

    with pytest.raises(DestinationError) as caught:
        validate_destination(save_file, GameTarget.FL26)

    assert caught.value.code == "not_directory"
    assert save_file.read_bytes() == b"existing"


def test_validate_destination_requires_directory_named_save_case_insensitively(
    tmp_path: Path,
) -> None:
    wrong_name = _make_directory(tmp_path / "profile")

    with pytest.raises(DestinationError) as caught:
        validate_destination(wrong_name, GameTarget.FL26)

    assert caught.value.code == "not_save"
    assert list(wrong_name.iterdir()) == []


def test_validate_destination_maps_probe_permission_failure_and_leaves_no_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    save_directory = _make_directory(tmp_path / "save")

    def deny_probe(*_args: object, **_kwargs: object) -> object:
        raise PermissionError("read-only directory")

    monkeypatch.setattr(paths_module.tempfile, "mkstemp", deny_probe)
    with pytest.raises(DestinationError) as caught:
        validate_destination(save_directory, GameTarget.FL26)

    assert caught.value.code == "permission_denied"
    assert list(save_directory.iterdir()) == []


def test_validate_destination_removes_probe_when_close_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    save_directory = _make_directory(tmp_path / "save")
    created_probes: list[Path] = []
    real_open = os.open
    real_close = os.close

    def recording_open(path: Path, flags: int, mode: int = 0o777) -> int:
        descriptor = real_open(path, flags, mode)
        created_probes.append(Path(path))
        return descriptor

    def failing_close(descriptor: int) -> None:
        real_close(descriptor)
        raise PermissionError("close denied")

    monkeypatch.setattr(paths_module.os, "open", recording_open)
    monkeypatch.setattr(paths_module.os, "close", failing_close)

    with pytest.raises(DestinationError) as caught:
        validate_destination(save_directory, GameTarget.FL26)

    assert caught.value.code == "permission_denied"
    assert len(created_probes) == 1
    assert not created_probes[0].exists()
    assert list(save_directory.iterdir()) == []


def test_validate_destination_maps_probe_time_disappearance_to_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    save_directory = _make_directory(tmp_path / "save")

    def missing_during_probe(*args: object, **kwargs: object) -> tuple[int, str]:
        raise FileNotFoundError("destination disappeared")

    monkeypatch.setattr(paths_module.tempfile, "mkstemp", missing_during_probe)

    with pytest.raises(DestinationError) as caught:
        validate_destination(save_directory, GameTarget.FL26)

    assert caught.value.code == "missing"
    assert list(save_directory.iterdir()) == []


def test_validate_destination_maps_probe_time_non_directory_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    save_directory = _make_directory(tmp_path / "save")

    def file_during_probe(*args: object, **kwargs: object) -> tuple[int, str]:
        raise NotADirectoryError("destination became a file")

    monkeypatch.setattr(paths_module.tempfile, "mkstemp", file_during_probe)

    with pytest.raises(DestinationError) as caught:
        validate_destination(save_directory, GameTarget.FL26)

    assert caught.value.code == "not_directory"
    assert list(save_directory.iterdir()) == []

def test_validate_destination_rejects_symlinked_save_leaf(tmp_path: Path) -> None:
    real_save = _make_directory(tmp_path / "real" / "save")
    linked_save = tmp_path / "linked" / "save"
    linked_save.parent.mkdir()
    linked_save.symlink_to(real_save, target_is_directory=True)

    with pytest.raises(DestinationError) as caught:
        validate_destination(linked_save, GameTarget.FL26)

    assert caught.value.code == "reparse_point"
    assert list(real_save.iterdir()) == []


def test_validate_destination_allows_redirected_ancestor_with_normal_leaf(
    tmp_path: Path,
) -> None:
    real_parent = _make_directory(tmp_path / "OneDrive" / "game")
    real_save = _make_directory(real_parent / "save")
    redirected_parent = tmp_path / "Documents"
    redirected_parent.symlink_to(real_parent, target_is_directory=True)

    assert validate_destination(
        redirected_parent / "save", GameTarget.FL26
    ) == real_save.resolve()


def test_validate_destination_rejects_symlinked_backup_directory(
    tmp_path: Path,
) -> None:
    save_directory = _make_directory(tmp_path / "save")
    escaped = _make_directory(tmp_path / "escaped")
    (save_directory / "FLDailyEditBackups").symlink_to(
        escaped, target_is_directory=True
    )

    with pytest.raises(DestinationError) as caught:
        validate_destination(save_directory, GameTarget.FL26)

    assert caught.value.code == "reparse_point"
    assert list(escaped.iterdir()) == []


def test_validate_destination_rejects_symlinked_edit_file(tmp_path: Path) -> None:
    save_directory = _make_directory(tmp_path / "save")
    escaped = tmp_path / "outside-edit"
    escaped.write_bytes(b"outside")
    (save_directory / "EDIT00000000").symlink_to(escaped)

    with pytest.raises(DestinationError) as caught:
        validate_destination(save_directory, GameTarget.FL26)

    assert caught.value.code == "reparse_point"
    assert escaped.read_bytes() == b"outside"
