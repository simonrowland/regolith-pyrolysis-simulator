"""Data-file loading shared by Flask routes and SocketIO handlers."""

from pathlib import Path
from typing import Any

from simulator.config import _load_setpoints_file
from simulator.yaml_cache import load_cached_safe_yaml


def load_yaml(filename: str, data_dir: Path) -> Any:
    path = data_dir / filename
    if filename == "setpoints.yaml":
        return _load_setpoints_file(path)
    if not path.exists():
        return {}
    with open(path) as file:
        return load_cached_safe_yaml(file.read()) or {}
