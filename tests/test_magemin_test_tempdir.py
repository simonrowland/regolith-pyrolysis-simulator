from __future__ import annotations

import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

import conftest


def test_magemin_openmpi_tmpdir_is_removed_at_fixture_teardown(
    tmp_path: Path,
) -> None:
    original_tmpdir = os.environ.get("TMPDIR")
    original_tempfile_cache = tempfile.tempdir
    temp_path_factory = SimpleNamespace(getbasetemp=lambda: tmp_path)
    fixture_generator = conftest.magemin_openmpi_tmpdir.__wrapped__(
        temp_path_factory
    )

    tempdir = next(fixture_generator)
    try:
        assert tempdir.is_dir()
        assert os.environ["TMPDIR"] == str(tempdir)
        assert Path(tempfile.gettempdir()) == tempdir
    finally:
        assert next(fixture_generator, None) is None

    assert not tempdir.exists()
    assert os.environ.get("TMPDIR") == original_tmpdir
    assert tempfile.tempdir == original_tempfile_cache
