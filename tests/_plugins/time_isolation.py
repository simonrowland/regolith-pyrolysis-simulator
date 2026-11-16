"""Test helpers for replacing a module's clock without mutating stdlib time."""

from types import SimpleNamespace
from typing import Any


def patch_module_time(monkeypatch: Any, module: Any, **replacements: Any) -> None:
    """Patch a private copy of a module's imported time namespace."""
    local_time = SimpleNamespace(**vars(module.time))
    for name, replacement in replacements.items():
        setattr(local_time, name, replacement)
    monkeypatch.setattr(module, "time", local_time)
