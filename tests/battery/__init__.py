"""Standalone schema v2.1 tests. Not wired into calibration_battery."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_BUILDER = None


def _literature_index_builder(root: Path):
    global _BUILDER
    if _BUILDER is None:
        path = root / "data" / "literature" / "build_index.py"
        spec = importlib.util.spec_from_file_location(
            "literature_index_store_summary", path
        )
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        _BUILDER = module
    return _BUILDER


def load_observation_store_summary(root: Path) -> dict[str, dict]:
    return _literature_index_builder(root).load_observation_store_summary(root)


def compilation_shard_observation_count(
    root: Path, paths: list[Path], obs_dir: Path
) -> int:
    """Serve compilation observation_id counts from the derived summary.

    Size-matched against the live files. Does not open shard payloads.
    """
    summary = load_observation_store_summary(root)
    total = 0
    for path in paths:
        rel = path.relative_to(obs_dir).as_posix()
        cached = summary.get(rel)
        assert cached is not None, f"observation_store_summary missing {rel}"
        assert cached["size"] == path.stat().st_size, rel
        total += cached["observation_id_count"]
    return total
