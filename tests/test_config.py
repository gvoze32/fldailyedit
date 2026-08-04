"""Configuration path regression tests."""

import config


def test_default_edit_file_uses_canonical_base_directory():
    assert config.EDIT_FILE_PATH == config.PROJECT_ROOT / "base" / "EDIT00000000"
    assert config.OUTPUT_FILE_PATH == config.PROJECT_ROOT / "output" / "EDIT00000000"
