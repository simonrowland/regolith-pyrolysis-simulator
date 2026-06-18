from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import shutil

import pytest

from simulator.config import DEFAULT_DATA_DIR, load_config_bundle


REQUIRED_CONFIGS = {
    "setpoints": "setpoints.yaml",
    "feedstocks": "feedstocks.yaml",
    "vapor_pressures": "vapor_pressures.yaml",
    "materials": "materials.yaml",
    "species_catalog": "species_catalog.yaml",
}


def test_load_config_bundle_returns_all_required_data_and_digests() -> None:
    bundle = load_config_bundle()

    assert bundle.setpoints
    assert bundle.feedstocks
    assert bundle.vapor_pressures
    assert bundle.materials
    assert bundle.species_catalog
    assert set(bundle.source_paths) == set(REQUIRED_CONFIGS)
    assert set(bundle.digests) == set(REQUIRED_CONFIGS)


def test_load_config_bundle_digests_are_stable_file_byte_sha256() -> None:
    first = load_config_bundle()
    second = load_config_bundle()

    assert first.digests == second.digests
    for name, path in first.source_paths.items():
        assert first.digests[name] == sha256(path.read_bytes()).hexdigest()


def test_load_config_bundle_missing_required_file_raises(tmp_path: Path) -> None:
    for filename in REQUIRED_CONFIGS.values():
        shutil.copy2(DEFAULT_DATA_DIR / filename, tmp_path / filename)
    (tmp_path / "materials.yaml").unlink()

    with pytest.raises(FileNotFoundError, match="required config file missing"):
        load_config_bundle(tmp_path)
