from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from simulator import engine_local_config
from tests.binding_admission_fixtures import isolate_engine_local_config


@pytest.fixture
def isolated_engine_config(tmp_path: Path) -> Iterator[Path]:
    source_path = engine_local_config.config_path()
    host_config_path = tmp_path / "host" / "engines" / source_path.name
    if source_path.is_file():
        host_config_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_path, host_config_path)

    with isolate_engine_local_config(
        tmp_path / "isolated" / "engines",
        source_path=source_path,
    ):
        yield host_config_path
