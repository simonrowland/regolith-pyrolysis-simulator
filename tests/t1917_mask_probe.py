"""Refusal-mask lookup cost and one functional pass (t-1917).

No arguments. Run from the repository root:

    python3 tests/t1917_mask_probe.py

Does not call an engine. Builds a temporary sqlite mask table, times
coarsen plus lookup on a battery-shaped request, and checks one learn /
short-circuit / clear cycle. Exits 2
when that lookup is not cheaper than the phase-A alphaMELTS ValueError
median (19 µs) by a different answer than MASKABLE_REFUSAL_CLASSES, or
when the coarse quanta disagree with ControlQuantization.
"""

from __future__ import annotations

import os
import sqlite3
import statistics
import sys
import tempfile
import time
from pathlib import Path

VALUEERROR_MEDIAN_S = 1.9e-5
LOOKUP_ROWS = 5000
LOOKUP_SAMPLES = 1000


def _boot() -> None:
    root = str(Path.cwd())
    if root not in sys.path:
        sys.path.insert(0, root)


def _percentile(samples: list[float], fraction: float) -> float:
    ordered = sorted(samples)
    index = int(fraction * (len(ordered) - 1))
    return ordered[index]


def _battery_request(index: int) -> dict:
    """Controls the battery sends, plus one exact setting so cells differ."""

    return {
        "temperature_C": 1200.0 + (index % 40) * 0.02,
        "composition_kg": {
            "SiO2": 0.55 + (index % 9) * 1.0e-5,
            "MgO": 0.2,
            "Al2O3": 0.15,
            "FeO": 0.1,
        },
        "pressure_bar": 1.0 + (index % 6) * 1.0e-5,
        "fO2_log": -8.0 + (index % 4) * 1.0e-4,
        "vapor_transport_pO2_bar": 1.0e-10 * (1.0 + (index % 5) * 1.0e-4),
        "engine_settings": {"probe": index},
    }


def _measure_lookup() -> tuple[float, float, float]:
    from simulator.engine_cache import (
        _ensure_mask_tables,
        _select_mask,
        pack,
        refusal_mask_cell,
    )

    with tempfile.TemporaryDirectory(prefix="t1917-mask-lookup-") as directory:
        path = Path(directory) / "mask.sqlite"
        connection = sqlite3.connect(path)
        try:
            _ensure_mask_tables(connection)
            outcome = pack({
                "kind": "refusal",
                "hot": {"status": "refusal", "refusal_reason": "engine_crash"},
            })
            rows = []
            requests = []
            for index in range(LOOKUP_ROWS):
                request = _battery_request(index)
                cell = refusal_mask_cell(request)
                if cell is None:
                    raise RuntimeError("probe request has no mask cell")
                requests.append(request)
                rows.append(("alphamelts", "4", cell, "refusal:engine_crash", outcome))
            connection.executemany(
                "INSERT INTO engine_refusal_mask "
                "(engine, chem_version, cell, refusal_class, outcome) "
                "VALUES (?, ?, ?, ?, ?)",
                rows,
            )
            connection.commit()
            for index in range(50):
                request = requests[index]
                cell = refusal_mask_cell(request)
                found = _select_mask(connection, "alphamelts", "4", cell)
                if found is None:
                    raise RuntimeError("warmup lookup missed a seeded cell")
            samples = []
            for index in range(LOOKUP_SAMPLES):
                request = requests[index]
                started = time.perf_counter()
                cell = refusal_mask_cell(request)
                found = _select_mask(connection, "alphamelts", "4", cell)
                samples.append(time.perf_counter() - started)
                if found is None:
                    raise RuntimeError("timed lookup missed a seeded cell")
        finally:
            connection.close()
    return statistics.median(samples), _percentile(samples, 0.90), _percentile(samples, 0.99)


def _quanta_pinned() -> bool:
    from simulator.engine_cache import (
        _MASK_COMPOSITION_SIG_FIGS,
        _MASK_LOG_FO2_DECIMALS,
        _MASK_LOG_FO2_QUANTUM,
        _MASK_PRESSURE_DECIMALS,
        _MASK_PRESSURE_QUANTUM,
        _MASK_T_DECIMALS,
        _MASK_T_QUANTUM,
        _mask_quantize,
        _mask_sigfig,
    )
    from simulator.reduced_real_determinism import (
        ControlQuantization,
        _quantize,
        _sigfig,
    )

    coarse = ControlQuantization.from_name("coarse")
    if (
        _MASK_T_QUANTUM != coarse.t_k_quantum
        or _MASK_PRESSURE_QUANTUM != coarse.pressure_bar_quantum
        or _MASK_LOG_FO2_QUANTUM != coarse.log_fo2_quantum
        or _MASK_COMPOSITION_SIG_FIGS != coarse.composition_sig_figs
        or _MASK_T_DECIMALS != coarse.t_k_decimals
        or _MASK_PRESSURE_DECIMALS != coarse.pressure_bar_decimals
        or _MASK_LOG_FO2_DECIMALS != coarse.log_fo2_decimals
    ):
        return False
    return (
        _mask_quantize(1000.4, coarse.t_k_quantum, coarse.t_k_decimals)
        == _quantize(1000.4, coarse.t_k_quantum, coarse.t_k_decimals)
        and _mask_sigfig(50.04, coarse.composition_sig_figs)
        == _sigfig(50.04, coarse.composition_sig_figs)
    )


def _functional() -> tuple[int, int, int, int]:
    from simulator import engine_cache

    with tempfile.TemporaryDirectory(prefix="t1917-mask-functional-") as directory:
        path = Path(directory) / "cache.sqlite"
        os.environ["REGOLITH_ENGINE_CACHE"] = str(path)
        os.environ["REGOLITH_ENGINE_CACHE_BOX"] = "laptop"
        os.environ.pop("REGOLITH_ENGINE_CACHE_MODE", None)
        engine_cache.engine_cache_counters(reset=True)
        engine_cache._MEMORY_CACHE.clear()
        version = engine_cache.ENGINE_CHEMISTRY_VERSION["alphamelts"]
        request = None
        for index in range(64):
            candidate = _battery_request(10_000 + index)
            key = engine_cache.engine_cache_key("alphamelts", version, candidate)
            if not engine_cache.mask_verify_selected(key):
                request = candidate
                break
        if request is None:
            raise RuntimeError("no unselected probe request")
        calls = []

        def solve():
            calls.append(1)
            return {"status": "refusal", "refusal_reason": "engine_crash"}

        first = engine_cache.cached_engine_call("alphamelts", request, solve)
        engine_cache._MEMORY_CACHE.clear()
        second = engine_cache.cached_engine_call("alphamelts", request, solve)
        if "mask_hit" in first or second.get("mask_hit") != "refusal:engine_crash":
            raise RuntimeError("mask short-circuit did not flag the class")
        if len(calls) != 1:
            raise RuntimeError(f"short-circuit solved {len(calls)} times")
        dry = engine_cache.clear_refusal_mask(
            path,
            engine="alphamelts",
            refusal_class="refusal:engine_crash",
            apply=False,
        )
        if dry["deleted_mask_cells"] != 0 or dry["mask_cells"] < 1:
            raise RuntimeError(f"dry-run clear changed rows: {dry}")
        applied = engine_cache.clear_refusal_mask(
            path,
            engine="alphamelts",
            refusal_class="refusal:engine_crash",
            apply=True,
        )
        engine_cache.cached_engine_call("alphamelts", request, solve)
        if len(calls) != 2:
            raise RuntimeError("clear did not make the next call solve")
        return (
            len(calls) - 1,
            1,
            int(applied["deleted_mask_cells"]),
            int(applied["deleted_audit_records"]),
        )


def main() -> int:
    _boot()
    status = 0
    try:
        median_s, p90_s, p99_s = _measure_lookup()
    except Exception as exc:
        print(f"T1917_MASK_LOOKUP_FAIL {type(exc).__name__}: {exc}")
        return 2
    print(
        "T1917_MASK_LOOKUP "
        f"n={LOOKUP_SAMPLES} median_s={median_s!r} p90_s={p90_s!r} p99_s={p99_s!r}"
    )
    from simulator.engine_cache import MASKABLE_REFUSAL_CLASSES

    should_mask = median_s < VALUEERROR_MEDIAN_S
    does_mask = ("alphamelts", "exception:ValueError") in MASKABLE_REFUSAL_CLASSES
    agree = should_mask == does_mask
    print(
        "T1917_MASK_VALUEERROR "
        f"measured_lookup_s={median_s!r} threshold_s={VALUEERROR_MEDIAN_S!r} "
        f"mask={does_mask} agree={agree}"
    )
    if not agree:
        status = 2
    try:
        pinned = _quanta_pinned()
    except Exception as exc:
        print(f"T1917_MASK_QUANTA pinned=False error={type(exc).__name__}: {exc}")
        return 2
    print(f"T1917_MASK_QUANTA pinned={pinned}")
    if not pinned:
        status = 2
    try:
        learned, shorted, cleared_cells, cleared_audit = _functional()
    except Exception as exc:
        print(f"T1917_MASK_FUNCTIONAL_FAIL {type(exc).__name__}: {exc}")
        return 2
    print(
        "T1917_MASK_FUNCTIONAL "
        f"learn={learned} short_circuit={shorted} "
        f"cleared_cells={cleared_cells} cleared_audit={cleared_audit} "
        f"solved_after_clear=1"
    )
    if cleared_cells < 1 or cleared_audit < 1:
        status = 2
    return status


if __name__ == "__main__":
    sys.exit(main())
