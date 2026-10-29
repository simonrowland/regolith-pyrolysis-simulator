"""Load the engine config owner before importing the simulator package."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

_PRIVATE_MODULE_NAME = "_regolith_engine_local_config"


def load_engine_local_config(repo_root: Path) -> ModuleType:
    source = Path(repo_root) / "simulator" / "engine_local_config.py"
    spec = importlib.util.spec_from_file_location(_PRIVATE_MODULE_NAME, source)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load engine config owner from {source}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise
    return module
