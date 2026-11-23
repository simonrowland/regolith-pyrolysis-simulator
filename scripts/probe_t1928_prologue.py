"""Time the prologue key and a warm prologue hit.

No arguments. The import root is the current working directory. The
controller runs this on a studio. It does not score engines.
``key_pure_phase_on`` hashes the ThermoEngine tree and the MAGEMin
binary and does not call them. The cold and warm passes use
``invoke_pure_phase`` false, so the measured rows do not call a live
backend. The warm-score probe covers the production flag.

Prints wall time and process CPU. The key is one call. The prologue
reports one cold pass and the median of three warm passes. Load
average is printed at the start and the end.
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
    from simulator.battery.compilation_tier import _write_compilation_comparisons_jsonl
    from simulator.battery.parsed_store_cache import (
        canonical_material,
        close_prologue,
        open_prologue,
        prologue_key,
    )
    from simulator.battery.score import (
        SCORE_ENGINE_SET,
        _read_decision_band_ratchet,
        _source_band_catalog,
        load_score_context,
    )

    entries, _missing, _history = _read_decision_band_ratchet(root)
    material = canonical_material(entries)
    if material is None:
        _emit("error=ratchet entries are not canonical")
        return 2
    engines = tuple(engine.value for engine in SCORE_ENGINE_SET)

    def key_on():
        # Production local scores pass invoke_pure_phase true. Hashing the
        # backends is the key cost; this call does not execute them.
        return prologue_key(
            root,
            engines=engines,
            invoke_pure_phase=True,
            work_id=None,
            rail=None,
            species_arg=None,
            entries_material=material,
        )

    try:
        _key, wall, cpu = _timed(key_on)
    except Exception as exc:
        _emit(f"key_pure_phase_on error={type(exc).__name__}: {exc}")
        return 1
    _emit(f"key_pure_phase_on wall_s={wall:.3f} cpu_s={cpu:.3f}")

    comparison_path = Path(scratch_root) / "t1928-compilation-comparisons.jsonl"

    def one_pass():
        open_prologue(
            root,
            engines=engines,
            invoke_pure_phase=False,
            work_id=None,
            rail=None,
            species_arg=None,
            entries=entries,
        )
        try:
            _context, load_wall, load_cpu = _timed(lambda: load_score_context(root))
            _bands, band_wall, band_cpu = _timed(
                lambda: _source_band_catalog(_context, entries)
            )
            rows, comp_wall, comp_cpu = _timed(
                lambda: _write_compilation_comparisons_jsonl(
                    _context,
                    comparison_path,
                    engines=SCORE_ENGINE_SET,
                    invoke_pure_phase=False,
                )
            )
            return (load_wall, load_cpu), (band_wall, band_cpu), (comp_wall, comp_cpu), rows
        finally:
            close_prologue()

    def emit_pass(label: str, result) -> None:
        load, band, compilation, rows = result
        _emit(
            f"{label}_load wall_s={load[0]:.3f} cpu_s={load[1]:.3f}"
        )
        _emit(
            f"{label}_source_band wall_s={band[0]:.3f} cpu_s={band[1]:.3f}"
        )
        _emit(
            f"{label}_compilation wall_s={compilation[0]:.3f} cpu_s={compilation[1]:.3f} rows={rows}"
        )

    cold = one_pass()
    emit_pass("cold", cold)
    warm_load = []
    warm_band = []
    warm_compilation = []
    for index in range(3):
        result = one_pass()
        emit_pass(f"warm_{index + 1}", result)
        warm_load.append(result[0])
        warm_band.append(result[1])
        warm_compilation.append(result[2])
    wall, cpu = _median(warm_load)
    _emit(f"warm_load_median wall_s={wall:.3f} cpu_s={cpu:.3f}")
    wall, cpu = _median(warm_band)
    _emit(f"warm_source_band_median wall_s={wall:.3f} cpu_s={cpu:.3f}")
    wall, cpu = _median(warm_compilation)
    _emit(f"warm_compilation_median wall_s={wall:.3f} cpu_s={cpu:.3f}")
    _emit(
        "loadavg_end={}".format(
            ",".join(f"{value:.2f}" for value in os.getloadavg())
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
