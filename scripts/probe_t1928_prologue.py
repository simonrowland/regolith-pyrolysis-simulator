"""Time a warm parsed-store load, compilation reuse, and source-band reuse.

No arguments. The import root is the current working directory. The
controller runs this on a studio. It does not score engines:
compilation reuse is measured with invoke_pure_phase false so MAGEMin
and ThermoEngine are not called. The warm-score probe covers the
production flag.

Prints wall time and process CPU. The load and each reuse step report
one cold call and the median of three warm calls. Load average is
printed at the start and the end.
"""

from __future__ import annotations

import os
import platform
import statistics
import sys
import time
from pathlib import Path


def _timed(action):
    wall_start = time.perf_counter()
    cpu_start = time.process_time()
    value = action()
    return (
        value,
        time.perf_counter() - wall_start,
        time.process_time() - cpu_start,
    )


def _median(samples: list[tuple[float, float]]) -> tuple[float, float]:
    return (
        statistics.median(sample[0] for sample in samples),
        statistics.median(sample[1] for sample in samples),
    )


def _emit(text: str) -> None:
    print(text, flush=True)


def main() -> int:
    root = Path.cwd()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    scratch_root = os.environ.get("TMPDIR")
    if not scratch_root:
        _emit("error=TMPDIR is required")
        return 2
    cache_dir = Path(scratch_root) / "t1928-parsed-store"
    os.environ["REGOLITH_PARSED_STORE_CACHE"] = "on"
    os.environ["REGOLITH_PARSED_STORE_CACHE_DIR"] = str(cache_dir)
    _emit(
        "host={host} python={python} platform={platform} loadavg_start={loadavg}".format(
            host=platform.node(),
            python=platform.python_version(),
            platform=platform.platform(),
            loadavg=",".join(f"{value:.2f}" for value in os.getloadavg()),
        )
    )
    from simulator.battery.score import (
        SCORE_ENGINE_SET,
        _read_decision_band_ratchet,
        _source_band_catalog,
        load_score_context,
    )
    from simulator.battery.compilation_tier import _write_compilation_comparisons_jsonl

    def load_once():
        return load_score_context(root)

    _context, cold_wall, cold_cpu = _timed(load_once)
    _emit(f"cold_load wall_s={cold_wall:.3f} cpu_s={cold_cpu:.3f}")
    del _context
    warm_load: list[tuple[float, float]] = []
    context = None
    for index in range(3):
        context, wall, cpu = _timed(load_once)
        warm_load.append((wall, cpu))
        _emit(f"warm_load_{index + 1} wall_s={wall:.3f} cpu_s={cpu:.3f}")
    wall, cpu = _median(warm_load)
    _emit(f"warm_load_median wall_s={wall:.3f} cpu_s={cpu:.3f}")
    assert context is not None

    comparison_path = Path(scratch_root) / "t1928-compilation-comparisons.jsonl"

    def write_comparisons():
        # Production local scores pass invoke_pure_phase true. This probe
        # keeps it false so the measured rows do not call a live backend.
        return _write_compilation_comparisons_jsonl(
            context,
            comparison_path,
            engines=SCORE_ENGINE_SET,
            invoke_pure_phase=False,
        )

    rows, cold_wall, cold_cpu = _timed(write_comparisons)
    _emit(
        f"cold_compilation wall_s={cold_wall:.3f} cpu_s={cold_cpu:.3f} rows={rows}"
    )
    warm_compilation: list[tuple[float, float]] = []
    for index in range(3):
        rows, wall, cpu = _timed(write_comparisons)
        warm_compilation.append((wall, cpu))
        _emit(
            f"warm_compilation_{index + 1} wall_s={wall:.3f} cpu_s={cpu:.3f} rows={rows}"
        )
    wall, cpu = _median(warm_compilation)
    _emit(f"warm_compilation_median wall_s={wall:.3f} cpu_s={cpu:.3f}")

    entries, _missing, _history = _read_decision_band_ratchet(context.root)

    def catalog():
        return _source_band_catalog(context, entries)

    _bands, cold_wall, cold_cpu = _timed(catalog)
    _emit(f"cold_source_band wall_s={cold_wall:.3f} cpu_s={cold_cpu:.3f}")
    warm_bands: list[tuple[float, float]] = []
    for index in range(3):
        _bands, wall, cpu = _timed(catalog)
        warm_bands.append((wall, cpu))
        _emit(f"warm_source_band_{index + 1} wall_s={wall:.3f} cpu_s={cpu:.3f}")
    wall, cpu = _median(warm_bands)
    _emit(f"warm_source_band_median wall_s={wall:.3f} cpu_s={cpu:.3f}")
    _emit(
        "loadavg_end={}".format(
            ",".join(f"{value:.2f}" for value in os.getloadavg())
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
