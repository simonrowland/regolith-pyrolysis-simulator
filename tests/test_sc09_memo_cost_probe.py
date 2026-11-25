from __future__ import annotations

import os
import resource
import runpy
import time
import warnings

import pytest


def _process_cpu_seconds() -> float:
    children = resource.getrusage(resource.RUSAGE_CHILDREN)
    return time.process_time() + children.ru_utime + children.ru_stime


def _measure(label, operation) -> float:
    wall_start = time.perf_counter()
    cpu_start = _process_cpu_seconds()
    operation()
    wall_seconds = time.perf_counter() - wall_start
    cpu_seconds = _process_cpu_seconds() - cpu_start
    warnings.warn(
        f"SC09_BENCH label={label} cpu_s={cpu_seconds:.3f} "
        f"wall_s={wall_seconds:.3f} loadavg={os.getloadavg()}",
        UserWarning,
        stacklevel=2,
    )
    return cpu_seconds


@pytest.mark.timeout(0)
def test_sc09_full_score_process_cpu_sample() -> None:
    from simulator.battery.score import load_score_context, score_store

    context = load_score_context()
    _measure("score_store_full", lambda: score_store(context))
