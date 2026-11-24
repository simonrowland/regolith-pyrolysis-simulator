"""Fresh-interpreter guards for import-order cycles across simulator and engines."""

from __future__ import annotations

import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, wait
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
        timeout=300,
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
    try:
        result = subprocess.run(
            [sys.executable, "-c", f"import {module_name}"],
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        return module_name, "timed out after 300 s"
    if result.returncode == 0:
        return None
    frames = [line.strip() for line in result.stderr.splitlines() if line.startswith("  File ")]
    if not result.stderr:
        return module_name, f"process exited with return code {result.returncode} and empty stderr"
    stderr_lines = result.stderr.splitlines()
    first = frames[0] if frames else stderr_lines[0]
    last = frames[-1] if frames else stderr_lines[-1]
    return module_name, f"first frame: {first}\nlast frame: {last}"


@pytest.mark.nightly
def test_every_simulator_and_engine_module_imports_cold() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    modules = (
        *_module_names_below(repo_root / "simulator"),
        *_module_names_below(repo_root / "engines"),
    )
    pool = ThreadPoolExecutor(max_workers=4)
    futures = {pool.submit(_cold_import, module): module for module in modules}
    completed, pending = wait(futures, timeout=300)
    timed_out = [futures[future] for future in pending]
    failures = [failure for future in completed if (failure := future.result())]
    individual_timeouts = [
        module for module, message in failures if message.startswith("timed out after ")
    ]
    failures = [failure for failure in failures if failure[0] not in individual_timeouts]
    pool.shutdown(wait=not timed_out, cancel_futures=True)
    messages = [f"{module}:\n{frames}" for module, frames in failures]
    all_timed_out = [*timed_out, *individual_timeouts]
    if all_timed_out:
        messages.append("timed out modules: " + ", ".join(all_timed_out))
    assert not messages, "cold import failures:\n" + "\n".join(messages)
