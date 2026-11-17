import sqlite3
import threading

import pytest

from simulator import engine_cache
from simulator.melt_backend.base import _call_with_hard_timeout


def test_deterministic_refusal_allowlist_is_explicit():
    from simulator.engine_cache import CACHEABLE_REFUSAL_CODES

    assert CACHEABLE_REFUSAL_CODES == frozenset({
        "out_of_basis", "composition_projected", "sum_below_95_wt_pct", "no_liquid",
    })


@pytest.mark.parametrize("engine", ["alphamelts", "vaporock"])
@pytest.mark.parametrize("code", ["out_of_basis", "composition_projected", "sum_below_95_wt_pct", "no_liquid"])
def test_allowlisted_refusal_hits_after_a_disk_round_trip(engine, code, tmp_path, monkeypatch):
    path = tmp_path / "cache.sqlite"
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(path))
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_BOX", "laptop")
    expected = {"status": "refusal", "refusal_reason": code, "engine_status": "out_of_domain"}
    assert engine_cache.cached_engine_call(engine, {}, lambda: expected) == expected
    engine_cache._MEMORY_CACHE.clear()
    assert engine_cache.cached_engine_call(engine, {}, lambda: pytest.fail("refusal was not stored")) == expected


@pytest.mark.parametrize("code", ["engine_timeout", "engine_crash", "unavailable", "not_converged", "io_error", "future_refusal"])
def test_transient_and_unknown_refusals_are_never_stored(code, tmp_path, monkeypatch):
    path = tmp_path / "cache.sqlite"
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(path))
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_BOX", "laptop")
    calls = []
    def solve():
        calls.append(1)
        return {"status": "refusal", "refusal_reason": code}
    for _ in range(2):
        engine_cache.cached_engine_call("alphamelts", {}, solve)
    assert len(calls) == 2
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT count(*) FROM engine_outputs").fetchone()[0] == 0


def test_abandoned_timeout_thread_cannot_insert_a_late_result(tmp_path, monkeypatch):
    path = tmp_path / "cache.sqlite"
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(path))
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_BOX", "laptop")
    release = threading.Event()
    finished = threading.Event()
    def delayed():
        release.wait(5)
        finished.set()
        return {"answer": 1.0}
    try:
        with pytest.raises(TimeoutError):
            engine_cache.cached_engine_call("alphamelts", {}, lambda: _call_with_hard_timeout(delayed, 0.02))
    finally:
        release.set()
        assert finished.wait(2)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT count(*) FROM engine_outputs").fetchone()[0] == 0


def test_bulk_loader_rejects_transient_refusals(tmp_path):
    # AlphaMELTS v3 carries the engine-cache disclosure value wrapper.
    version = engine_cache.ENGINE_CHEMISTRY_VERSION["alphamelts"]
    key = engine_cache.engine_cache_key("alphamelts", version, {})
    with pytest.raises(ValueError, match="refusal code is not cacheable"):
        engine_cache.load_cache_rows(tmp_path / "cache.sqlite", [(
            key, {"status": "refusal", "refusal_reason": "engine_timeout"},
            "alphamelts", version, "laptop", 1,
        )])


@pytest.mark.parametrize(("engine", "composition"), [
    ("alphamelts", {"Al2O3": 100.0}),
    # The existing VapoRock basis contract refuses metal/sulfide/halide input.
    ("vaporock", {"Fe": 50.0, "FeS": 30.0, "NaCl": 20.0}),
])
def test_real_engine_basis_refusal_hits_before_the_worker_pipe(engine, composition, tmp_path, monkeypatch):
    from simulator.diagnostic_helpers.binary_pot_battery import (
        BinaryPot, Po2Request, _close_isolated_cell_workers,
        close_battery_engine_handle, equilibrate_cell, open_battery_engine,
    )
    from simulator.engine_pool import WarmEngineWorker

    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(tmp_path / "cache.sqlite"))
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_BOX", "laptop")
    _close_isolated_cell_workers()
    handle = open_battery_engine(engine)
    if not handle.available:
        close_battery_engine_handle(handle)
        pytest.skip(handle.unavailable_reason or f"{engine} is unavailable")
    pot = BinaryPot("basis-refusal", None, "basis refusal", composition)
    calls = []
    original = WarmEngineWorker.call

    def counted(worker, *args, **kwargs):
        calls.append(1)
        return original(worker, *args, **kwargs)

    monkeypatch.setattr(WarmEngineWorker, "call", counted)
    engine_cache.engine_cache_counters(reset=True)
    try:
        def evaluate():
            return equilibrate_cell(
                handle, pot, temperature_K=1700.0,
                po2=Po2Request("engine_default", None), isolated=True, timeout_s=90.0,
            )
        cold = evaluate()
        engine_cache._MEMORY_CACHE.clear()
        warm = evaluate()
        assert cold.status == warm.status == "refusal"
        assert cold.refusal_reason == warm.refusal_reason == "out_of_basis"
        assert cold.engine_reason == warm.engine_reason
        assert len(calls) == 1
        counts = engine_cache.engine_cache_counters()[engine]
        assert counts["hits"] == counts["misses"] == counts["stores"] == 1
    finally:
        _close_isolated_cell_workers()
        close_battery_engine_handle(handle)
