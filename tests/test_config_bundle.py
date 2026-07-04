from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import shutil

import pytest
import yaml

from simulator.config import (
    DEFAULT_DATA_DIR,
    functional_data_yaml_digest,
    load_config_bundle,
)


REQUIRED_CONFIGS = {
    "setpoints": "setpoints.yaml",
    "feedstocks": "feedstocks.yaml",
    "foulant_thermo": "foulant_thermo.yaml",
    "vapor_pressures": "vapor_pressures.yaml",
    "materials": "materials.yaml",
    "species_catalog": "species_catalog.yaml",
}
FUNCTIONAL_DATA_CONFIGS = {"setpoints", "vapor_pressures"}


def _write_minimal_config_bundle(root: Path) -> None:
    functional_content = (
        "b: 2\n"
        "a: 1\n"
        "nested:\n"
        "  z: 3\n"
        "  y:\n"
        "    - 1\n"
        "    - 2\n"
    )
    for name, filename in REQUIRED_CONFIGS.items():
        content = functional_content if name in FUNCTIONAL_DATA_CONFIGS else "{}\n"
        (root / filename).write_text(content)


def test_load_config_bundle_returns_all_required_data_and_digests() -> None:
    bundle = load_config_bundle()

    assert bundle.setpoints
    assert bundle.feedstocks
    assert bundle.foulant_thermo
    assert bundle.vapor_pressures
    assert bundle.materials
    assert bundle.species_catalog
    assert set(bundle.source_paths) == set(REQUIRED_CONFIGS)
    assert set(bundle.digests) == set(REQUIRED_CONFIGS)


def test_load_config_bundle_digests_are_stable_and_scoped() -> None:
    first = load_config_bundle()
    second = load_config_bundle()

    assert first.digests == second.digests
    for name, path in first.source_paths.items():
        if name in FUNCTIONAL_DATA_CONFIGS:
            assert first.digests[name] == functional_data_yaml_digest(
                getattr(first, name)
            )
        else:
            assert first.digests[name] == sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("name", sorted(FUNCTIONAL_DATA_CONFIGS))
def test_functional_data_yaml_digests_ignore_comments_and_mapping_order(
    tmp_path: Path,
    name: str,
) -> None:
    _write_minimal_config_bundle(tmp_path)
    path = tmp_path / REQUIRED_CONFIGS[name]
    baseline = load_config_bundle(tmp_path).digests[name]

    path.write_text("# documentation-only note\n" + path.read_text())
    assert load_config_bundle(tmp_path).digests[name] == baseline

    path.write_text(
        "nested:\n"
        "  y:\n"
        "    - 1\n"
        "    - 2\n"
        "  z: 3\n"
        "a: 1\n"
        "b: 2\n"
    )
    assert load_config_bundle(tmp_path).digests[name] == baseline


@pytest.mark.parametrize("name", sorted(FUNCTIONAL_DATA_CONFIGS))
def test_functional_data_yaml_digests_track_values_and_added_keys(
    tmp_path: Path,
    name: str,
) -> None:
    _write_minimal_config_bundle(tmp_path)
    path = tmp_path / REQUIRED_CONFIGS[name]
    baseline = load_config_bundle(tmp_path).digests[name]
    data = yaml.safe_load(path.read_text())

    value_changed = dict(data)
    value_changed["a"] = 9
    path.write_text(yaml.safe_dump(value_changed, sort_keys=False))
    assert load_config_bundle(tmp_path).digests[name] != baseline

    added_key = dict(data)
    added_key["added_functional_key"] = True
    path.write_text(yaml.safe_dump(added_key, sort_keys=False))
    assert load_config_bundle(tmp_path).digests[name] != baseline


@pytest.mark.parametrize("name", sorted(FUNCTIONAL_DATA_CONFIGS))
def test_functional_data_yaml_digests_distinguish_degenerate_roots(
    tmp_path: Path,
    name: str,
) -> None:
    # #89 review-fold: distinct degenerate roots must NOT collide — the old
    # `yaml.safe_load(...) or {}` fallback collapsed {}, [], null, and empty-file
    # to one digest, masking a real root-structure change.
    _write_minimal_config_bundle(tmp_path)
    path = tmp_path / REQUIRED_CONFIGS[name]

    def _digest_for(text: str) -> str:
        path.write_text(text)
        return load_config_bundle(tmp_path).digests[name]

    # all four are falsey YAML roots that the old `... or {}` fallback collapsed to
    # a single hash({}); the fix digests the parsed root so they are now distinct.
    # (null and empty-file both parse to None, so they are intentionally omitted.)
    variants = {
        _digest_for("{}\n"),
        _digest_for("[]\n"),
        _digest_for("false\n"),
        _digest_for("0\n"),
    }
    assert len(variants) == 4


def test_load_config_bundle_missing_required_file_raises(tmp_path: Path) -> None:
    for filename in REQUIRED_CONFIGS.values():
        shutil.copy2(DEFAULT_DATA_DIR / filename, tmp_path / filename)
    (tmp_path / "materials.yaml").unlink()

    with pytest.raises(FileNotFoundError, match="required config file missing"):
        load_config_bundle(tmp_path)
