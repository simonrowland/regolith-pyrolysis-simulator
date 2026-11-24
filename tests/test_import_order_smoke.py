"""Fresh-interpreter guards for import-order cycles across simulator and engines."""

from __future__ import annotations

import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "module_name",
    (
        "simulator.thermal_budget",
        "simulator.thermal_train",
        "engines.builtin.overhead_bleed",
    ),
)
def test_required_modules_import_cold(module_name: str) -> None:
    result = subprocess.run(
        [sys.executable, "-c", f"import {module_name}"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"cold import failed for {module_name}:\n{result.stderr}"
    )


def _module_names_below(root: Path) -> tuple[str, ...]:
    names = set()
    for path in root.rglob("*.py"):
        relative = path.relative_to(root.parent).with_suffix("")
        parts = relative.parts
        if parts[-1] == "__init__":
            parts = parts[:-1]
        names.add(".".join(parts))
    return tuple(sorted(names))


def _cold_import(module_name: str) -> tuple[str, str] | None:
    result = subprocess.run(
        [sys.executable, "-c", f"import {module_name}"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return None
    frames = [line.strip() for line in result.stderr.splitlines() if line.startswith("  File ")]
    first = frames[0] if frames else result.stderr.splitlines()[0]
    last = frames[-1] if frames else result.stderr.splitlines()[-1]
    return module_name, f"first frame: {first}\nlast frame: {last}"


@pytest.mark.nightly
def test_every_simulator_and_engine_module_imports_cold() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    modules = (
        *_module_names_below(repo_root / "simulator"),
        *_module_names_below(repo_root / "engines"),
    )
    with ThreadPoolExecutor(max_workers=4) as pool:
        failures = [failure for failure in pool.map(_cold_import, modules) if failure]
    assert not failures, "cold import failures:\n" + "\n".join(
        f"{module}:\n{frames}" for module, frames in failures
    )
