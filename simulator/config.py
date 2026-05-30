"""Central loader for simulator configuration YAML."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

import yaml


DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data"


@dataclass(frozen=True)
class ConfigBundle:
    setpoints: dict[str, Any]
    feedstocks: dict[str, Any]
    vapor_pressures: dict[str, Any]
    materials: dict[str, Any]
    source_paths: dict[str, Path]
    digests: dict[str, str]


def _load_required_yaml(path: Path) -> tuple[dict[str, Any], str]:
    if not path.exists():
        raise FileNotFoundError(f"required config file missing: {path}")
    raw = path.read_bytes()
    loaded = yaml.safe_load(raw.decode("utf-8")) or {}
    return loaded, sha256(raw).hexdigest()


def load_config_bundle(
    data_dir: Path | None = None,
    *,
    setpoints_path: Path | None = None,
    feedstocks_path: Path | None = None,
    vapor_pressures_path: Path | None = None,
    materials_path: Path | None = None,
) -> ConfigBundle:
    root = Path(data_dir) if data_dir is not None else DEFAULT_DATA_DIR
    source_paths = {
        "setpoints": Path(setpoints_path) if setpoints_path else root / "setpoints.yaml",
        "feedstocks": Path(feedstocks_path) if feedstocks_path else root / "feedstocks.yaml",
        "vapor_pressures": (
            Path(vapor_pressures_path)
            if vapor_pressures_path
            else root / "vapor_pressures.yaml"
        ),
        "materials": Path(materials_path) if materials_path else root / "materials.yaml",
    }
    loaded: dict[str, dict[str, Any]] = {}
    digests: dict[str, str] = {}
    for name, path in source_paths.items():
        loaded[name], digests[name] = _load_required_yaml(path)
    return ConfigBundle(
        setpoints=loaded["setpoints"],
        feedstocks=loaded["feedstocks"],
        vapor_pressures=loaded["vapor_pressures"],
        materials=loaded["materials"],
        source_paths=source_paths,
        digests=digests,
    )
