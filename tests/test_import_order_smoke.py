"""Fresh-interpreter guards for import-order cycles across simulator and engines."""

from __future__ import annotations

import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path
from threading import Event, Lock

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


def _cold_import(
    module_name: str,
    deadline: float,
    processes: set[subprocess.Popen],
    processes_lock: Lock,
) -> tuple[str, str] | None:
    with processes_lock:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return module_name, "timed out before sweep deadline"
        process = subprocess.Popen(
            [sys.executable, "-c", f"import {module_name}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        processes.add(process)
    try:
        try:
            stdout, stderr = process.communicate(timeout=deadline - time.monotonic())
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
            return module_name, "timed out before sweep deadline"
    finally:
        with processes_lock:
            processes.discard(process)
    result = subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)
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
    deadline = time.monotonic() + 300
    processes: set[subprocess.Popen] = set()
    processes_lock = Lock()
    pool = ThreadPoolExecutor(max_workers=4)
    futures = {
        pool.submit(_cold_import, module, deadline, processes, processes_lock): module
        for module in modules
    }
    completed, pending = wait(futures, timeout=max(0, deadline - time.monotonic()))
    timed_out = [futures[future] for future in pending]
    if pending:
        with processes_lock:
            running_processes = tuple(processes)
        for process in running_processes:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        for process in running_processes:
            process.wait()
    failures = [failure for future in completed if (failure := future.result())]
    individual_timeouts = [module for module, message in failures if message.startswith("timed out")]
    failures = [failure for failure in failures if failure[0] not in individual_timeouts]
    pool.shutdown(wait=True, cancel_futures=True)
    messages = [f"{module}:\n{frames}" for module, frames in failures]
    all_timed_out = [*timed_out, *individual_timeouts]
    if all_timed_out:
        messages.append("timed out modules: " + ", ".join(all_timed_out))
    assert not messages, "cold import failures:\n" + "\n".join(messages)


def test_sweep_deadline_kills_and_reaps_running_imports(monkeypatch) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    module = sys.modules[__name__]
    started = Event()
    children: list[subprocess.Popen] = []
    popen = subprocess.Popen
    wait_for_futures = wait

    def start_slow_import(command, **kwargs):
        child = popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            **kwargs,
        )
        children.append(child)
        started.set()
        return child

    def wait_until_deadline(futures, *, timeout):
        assert timeout > 0
        assert started.wait(timeout=5)
        return wait_for_futures(futures, timeout=0)

    monkeypatch.setattr(subprocess, "Popen", start_slow_import)
    monkeypatch.setattr(
        module,
        "_module_names_below",
        lambda root: ("simulator.slow_import",) if root == repo_root / "simulator" else (),
    )
    monkeypatch.setattr(module, "wait", wait_until_deadline)

    with pytest.raises(AssertionError, match="timed out modules: simulator.slow_import"):
        test_every_simulator_and_engine_module_imports_cold()

    assert len(children) == 1
    assert children[0].poll() is not None
