from __future__ import annotations

import os
import resource
import runpy
import statistics
import time
import warnings


def _process_cpu_seconds() -> float:
    children = resource.getrusage(resource.RUSAGE_CHILDREN)
    return time.process_time() + children.ru_utime + children.ru_stime


def _measure(label, operation) -> list[float]:
    cpu_samples = []
    for index in range(5):
        wall_start = time.perf_counter()
        cpu_start = _process_cpu_seconds()
        operation()
        wall_seconds = time.perf_counter() - wall_start
        cpu_seconds = _process_cpu_seconds() - cpu_start
        cpu_samples.append(cpu_seconds)
        warnings.warn(
            f"SC09_BENCH label={label} sample={index + 1} "
            f"cpu_s={cpu_seconds:.3f} wall_s={wall_seconds:.3f} "
            f"loadavg={os.getloadavg()}",
            UserWarning,
            stacklevel=2,
        )
    warnings.warn(
        f"SC09_BENCH_MEDIAN label={label} "
        f"cpu_s={statistics.median(cpu_samples):.3f} "
        f"samples={cpu_samples}",
        UserWarning,
        stacklevel=2,
    )
    return cpu_samples


def test_sc09_full_sim_and_score_process_cpu_medians() -> None:
    runner = runpy.run_path("tests/test_runner_smoke.py")
    scenario = runner["SCENARIOS"][0]
    assert scenario["name"] == "lunar_mare_low_ti_C0_24h"
    _measure(
        "lunar_mare_low_ti_C0_24h",
        lambda: runner["_run_scenario"](scenario),
    )

    from simulator.battery.score import load_score_context, score_store

    context = load_score_context()
    _measure("score_store_full", lambda: score_store(context))
