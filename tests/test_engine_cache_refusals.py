import sqlite3
import threading

import pytest

from engines.domain_reason import OutOfDomainReason, STABLE_OUT_OF_DOMAIN_REASONS
from simulator import engine_cache
from simulator.melt_backend.base import _call_with_hard_timeout

pytestmark = pytest.mark.engine_cache_off


def test_deterministic_refusal_allowlist_is_explicit():
    from simulator.engine_cache import (
        CACHEABLE_REFUSAL_CODES,
        EngineCacheValue,
        cacheable_refusal,
    )
    from simulator.melt_backend.vaporock import EMPTY_SPECIATION_STATUS_REASON

    assert CACHEABLE_REFUSAL_CODES == frozenset({
        "out_of_basis", "composition_projected", "sum_below_95_wt_pct", "no_liquid",
        OutOfDomainReason.FORBIDDEN_SPECIES.value,
        OutOfDomainReason.LIQUID_STATE.value,
        OutOfDomainReason.INVALID_PRESSURE_BAR.value,
        OutOfDomainReason.INVALID_FO2_LOG.value,
        OutOfDomainReason.SUM_PRESSURE_SANITY.value,
        EMPTY_SPECIATION_STATUS_REASON,
    })
    assert OutOfDomainReason.SPECIATION_NO_FINITE_VALUES in (
        STABLE_OUT_OF_DOMAIN_REASONS
    )
    reason = OutOfDomainReason.SPECIATION_NO_FINITE_VALUES.value
    assert cacheable_refusal(EngineCacheValue(
        {}, refusal_reason=reason, refusal_status="not_converged",
    ))
    assert cacheable_refusal({
        "status": "refusal",
        "refusal_reason": reason,
        "engine_status": "not_converged",
    })
    assert not cacheable_refusal(EngineCacheValue(
        {},
        refusal_reason=OutOfDomainReason.NOT_CONVERGED.value,
        refusal_status="not_converged",
    ))


def _vaporock_equilibrium_cache_call(kwargs, tmp_path, monkeypatch, *, available=True):
    from simulator.diagnostic_helpers.binary_pot_battery import (
        EngineHandle,
        _call_cached_backend_equilibrate,
    )
    from simulator.melt_backend.vaporock import VapoRockBackend

    path = tmp_path / "cache.sqlite"
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(path))
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_BOX", "laptop")
    engine_cache.engine_cache_counters(reset=True)
    engine_cache._MEMORY_CACHE.clear()
    backend = VapoRockBackend()
    backend._available = available
    backend._vaporock = object() if available else None
    handle = EngineHandle(
        name="vaporock",
        backend=backend,
        available=available,
        unavailable_reason=None if available else "VapoRock backend not initialized",
        takes_fo2=True,
        supports_intrinsic_fo2=True,
        engine_settings={
            "temperature_units": backend._temperature_units,
            "pressure_units": backend._pressure_units,
            "vapor_pressure_units": backend._vapor_pressure_units,
        },
    )
    calls = []

    def solve():
        calls.append(1)
        return backend.equilibrate(**kwargs)

    return _call_cached_backend_equilibrate, backend, handle, calls, solve


@pytest.mark.parametrize(
    "code",
    [
        OutOfDomainReason.FORBIDDEN_SPECIES.value,
        OutOfDomainReason.LIQUID_STATE.value,
        OutOfDomainReason.INVALID_PRESSURE_BAR.value,
        OutOfDomainReason.INVALID_FO2_LOG.value,
        OutOfDomainReason.SUM_PRESSURE_SANITY.value,
    ],
)
def test_vaporock_domain_refusal_hits_after_equilibrium_hot_round_trip(
    code, tmp_path, monkeypatch,
):
    composition = (
        {"Fe": 1.0, "FeS": 0.5, "NaCl": 0.2}
        if code == OutOfDomainReason.FORBIDDEN_SPECIES.value
        else {"SiO2": 1.0}
    )
    kwargs = {
        "temperature_C": 1600.0,
        "composition_mol": composition,
        "pressure_bar": (
            0.0 if code == OutOfDomainReason.INVALID_PRESSURE_BAR.value else 1e-6
        ),
        "fO2_log": (
            None if code == OutOfDomainReason.INVALID_FO2_LOG.value else -8.0
        ),
    }
    if code == OutOfDomainReason.LIQUID_STATE.value:
        kwargs["liquid_fraction"] = 0.0
    cached_call, backend, handle, calls, solve = _vaporock_equilibrium_cache_call(
        kwargs, tmp_path, monkeypatch
    )
    if code == OutOfDomainReason.SUM_PRESSURE_SANITY.value:
        monkeypatch.setattr(
            backend,
            "_call_vaporock",
            lambda **_kwargs: {"SiO": 2_000_000.0},
        )

    cold = cached_call(handle, kwargs, solve)
    engine_cache._MEMORY_CACHE.clear()
    warm = cached_call(
        handle, kwargs, lambda: pytest.fail("refusal was not stored")
    )

    assert cold.status == warm.status == "out_of_domain"
    assert cold.diagnostics["backend_status_reason"] == code
    assert warm.diagnostics["backend_status_reason"] == code
    assert len(calls) == 1
    counts = engine_cache.engine_cache_counters()["vaporock"]
    assert counts["hits"] == counts["misses"] == counts["stores"] == 1


def test_vaporock_no_finite_speciation_refusal_hits_after_disk_round_trip(
    tmp_path, monkeypatch,
):
    from simulator.melt_backend.vaporock import (
        EMPTY_SPECIATION_STATUS_REASON,
        EmptySpeciationCause,
    )

    kwargs = {
        "temperature_C": 1600.0,
        "composition_mol": {"SiO2": 1.0},
        "pressure_bar": 1e-6,
        "fO2_log": -8.0,
    }
    cached_call, backend, handle, calls, solve = _vaporock_equilibrium_cache_call(
        kwargs, tmp_path, monkeypatch
    )

    def empty_speciation(**_kwargs):
        backend._latch_empty_speciation(
            EmptySpeciationCause.NO_FINITE_VALUES,
            {
                "speciation_row_count": 2,
                "speciation_finite_count": 0,
                "speciation_nan_count": 1,
                "speciation_neginf_count": 1,
                "speciation_other_nonfinite_count": 0,
            },
        )
        return {}

    monkeypatch.setattr(backend, "_call_vaporock", empty_speciation)
    cold = cached_call(handle, kwargs, solve)
    engine_cache._MEMORY_CACHE.clear()
    warm = cached_call(
        handle, kwargs, lambda: pytest.fail("speciation refusal was not stored")
    )

    assert cold.status == warm.status == "not_converged"
    assert (
        cold.diagnostics["backend_status_reason"]
        == EMPTY_SPECIATION_STATUS_REASON
    )
    assert warm.diagnostics == cold.diagnostics
    assert len(calls) == 1
    counts = engine_cache.engine_cache_counters()["vaporock"]
    assert counts["hits"] == counts["misses"] == counts["stores"] == 1


@pytest.mark.parametrize(
    ("status", "code"),
    [
        ("not_converged", OutOfDomainReason.NOT_CONVERGED.value),
        ("unavailable", OutOfDomainReason.BACKEND_UNAVAILABLE.value),
    ],
)
def test_vaporock_transient_refusal_is_not_stored(
    status, code, tmp_path, monkeypatch,
):
    kwargs = {
        "temperature_C": 1600.0,
        "composition_mol": {"SiO2": 1.0},
        "pressure_bar": 1e-6,
        "fO2_log": -8.0,
    }
    cached_call, backend, handle, calls, solve = _vaporock_equilibrium_cache_call(
        kwargs, tmp_path, monkeypatch, available=status != "unavailable"
    )
    if status == "not_converged":
        from simulator.melt_backend.vaporock import VapoRockCallError

        def fail_call(**_kwargs):
            raise VapoRockCallError("TransientFailure", "injected engine failure")

        monkeypatch.setattr(backend, "_call_vaporock", fail_call)

    for _ in range(2):
        result = cached_call(handle, kwargs, solve)
        assert result.status == status
        assert result.diagnostics["backend_status_reason"] == code
        engine_cache._MEMORY_CACHE.clear()

    assert len(calls) == 2
    assert backend._available is (status != "unavailable")
    counts = engine_cache.engine_cache_counters()["vaporock"]
    assert counts["misses"] == 2
    assert counts["stores"] == 0
    assert counts[f"not_stored:refusal:{code}"] == 2


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


@pytest.mark.parametrize(
    ("failure_field", "failure_value"),
    [("exit_signal", 9), ("exit_code", 1)],
)
def test_allowlisted_refusal_with_process_failure_is_never_stored(
    failure_field, failure_value, tmp_path, monkeypatch,
):
    path = tmp_path / "cache.sqlite"
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(path))
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_BOX", "laptop")
    calls = []

    def solve():
        calls.append(1)
        return {
            "status": "refusal",
            "refusal_reason": "no_liquid",
            "engine_status": "no_liquid",
            failure_field: failure_value,
        }

    for _ in range(2):
        engine_cache.cached_engine_call("alphamelts", {}, solve)

    assert len(calls) == 2
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT count(*) FROM engine_outputs").fetchone()[0] == 0


def test_not_stored_paths_are_counted_by_reason(tmp_path, monkeypatch):
    path = tmp_path / "cache.sqlite"
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(path))
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_BOX", "laptop")
    engine_cache.engine_cache_counters(reset=True)
    engine_cache._MEMORY_CACHE.clear()

    from simulator.engine_cache import EngineCacheValue

    engine_cache.cached_engine_call(
        "alphamelts", {"case": "cacheable-false"},
        lambda: EngineCacheValue({"answer": 1}, cacheable=False),
    )
    engine_cache.cached_engine_call(
        "alphamelts", {"case": "refusal"},
        lambda: {"status": "refusal", "refusal_reason": "engine_timeout"},
    )
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_MODE", "readonly")
    engine_cache.cached_engine_call(
        "alphamelts", {"case": "readonly"}, lambda: {"answer": 2},
    )

    counts = engine_cache.engine_cache_counters()["alphamelts"]
    assert counts["not_stored:cacheable_false"] == 1
    assert counts["not_stored:refusal:engine_timeout"] == 1
    assert counts["not_stored:readonly"] == 1


def test_readonly_refusal_miss_is_counted_in_exactly_one_bucket(tmp_path, monkeypatch):
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(tmp_path / "cache.sqlite"))
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_BOX", "laptop")
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_MODE", "readonly")
    engine_cache.engine_cache_counters(reset=True)
    engine_cache._MEMORY_CACHE.clear()

    engine_cache.cached_engine_call(
        "alphamelts", {"case": "readonly-refusal"},
        lambda: {"status": "refusal", "refusal_reason": "engine_timeout"},
    )

    counts = engine_cache.engine_cache_counters()["alphamelts"]
    not_stored = sum(
        value for key, value in counts.items()
        if key.startswith("not_stored:") or key.startswith("uncacheable:")
    )
    assert counts["misses"] == 1
    assert counts["stores"] == 0
    assert counts["not_stored:readonly"] == 1
    assert counts.get("not_stored:refusal:engine_timeout", 0) == 0
    assert counts["misses"] == counts["stores"] + not_stored


def test_connection_open_failure_counts_as_no_connection(tmp_path, monkeypatch):
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(tmp_path / "cache.sqlite"))
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_BOX", "laptop")
    engine_cache.engine_cache_counters(reset=True)
    engine_cache._MEMORY_CACHE.clear()

    def fail_connect(*_args, **_kwargs):
        raise sqlite3.OperationalError("cache unavailable")

    monkeypatch.setattr(engine_cache, "_connect", fail_connect)
    assert engine_cache.cached_engine_call(
        "alphamelts", {"case": "connect-error"}, lambda: {"answer": 3},
    ) == {"answer": 3}

    counts = engine_cache.engine_cache_counters()["alphamelts"]
    assert counts["read_error"] == 1
    assert counts["not_stored:readonly"] == 1


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
    key = engine_cache.engine_cache_key("alphamelts", 2, {})
    with pytest.raises(ValueError, match="refusal code is not cacheable"):
        engine_cache.load_cache_rows(tmp_path / "cache.sqlite", [(
            key, {"status": "refusal", "refusal_reason": "engine_timeout"},
            "alphamelts", 2, "laptop", 1,
        )])


def test_bulk_loader_rejects_typed_transient_equilibrium_refusal(tmp_path):
    from simulator.engine_cache import EngineCacheValue

    key = engine_cache.engine_cache_key("vaporock", 1, {})
    transient = EngineCacheValue(
        ["not_converged"],
        refusal_reason=OutOfDomainReason.NOT_CONVERGED.value,
        refusal_status="not_converged",
    )
    with pytest.raises(ValueError, match="refusal code is not cacheable"):
        engine_cache.load_cache_rows(tmp_path / "cache.sqlite", [(
            key, transient, "vaporock", 1, "laptop", 1,
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


def _compile_adapter_solve(source: str, namespace: dict[str, object]) -> object:
    """Compile a solve whose code file is the real cache module, not this test."""

    from pathlib import Path

    filename = str(Path(engine_cache.__file__).resolve())
    exec(compile(source, filename, "exec"), namespace)
    return namespace["_adapter_solve"]


def test_readonly_store_attempt_is_a_counted_noop(tmp_path, monkeypatch) -> None:
    path = tmp_path / "cache.sqlite"
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(path))
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_BOX", "laptop")
    monkeypatch.delenv("REGOLITH_ENGINE_CACHE_MODE", raising=False)
    engine_cache._close_connections()
    engine_cache._MEMORY_CACHE.clear()
    engine_cache.engine_cache_counters(reset=True)
    assert engine_cache.cached_engine_call(
        "openimcc", {"request": 1}, lambda: {"answer": 1}
    ) == {"answer": 1}
    with sqlite3.connect(path) as connection:
        stored = connection.execute("SELECT key, value FROM engine_outputs").fetchall()
    assert len(stored) == 1
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_MODE", "readonly")
    engine_cache._close_connections()
    engine_cache._MEMORY_CACHE.clear()
    engine_cache.engine_cache_counters(reset=True)
    calls: list[int] = []

    def solve() -> dict[str, int]:
        calls.append(1)
        return {"answer": 2}

    assert engine_cache.cached_engine_call("openimcc", {"request": 2}, solve) == {"answer": 2}
    assert calls == [1]
    with sqlite3.connect(path) as connection:
        rows = connection.execute("SELECT key, value FROM engine_outputs").fetchall()
    assert rows == stored
    counts = engine_cache.engine_cache_counters()["openimcc"]
    assert counts["stores"] == 0
    assert counts["hits"] == 0
    assert counts["misses"] == 1
    assert counts["not_stored:readonly"] == 1


def test_runner_cache_does_not_serve_or_store_a_double(tmp_path, monkeypatch) -> None:
    path = tmp_path / "runner.sqlite"
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(path))
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_BOX", "laptop")
    monkeypatch.delenv("REGOLITH_ENGINE_CACHE_MODE", raising=False)
    engine_cache._close_connections()
    engine_cache._MEMORY_CACHE.clear()
    engine_cache.engine_cache_counters(reset=True)
    namespace = dict(engine_cache.__dict__)
    namespace["_probe_calls"] = []
    real_seed = _compile_adapter_solve(
        "def _adapter_solve():\n"
        "    _probe_calls.append('seed')\n"
        "    return {'answer': 'cached'}\n",
        namespace,
    )
    with engine_cache.runner_engine_cache_snapshot(str(path), ""):
        assert engine_cache.cached_engine_call(
            "openimcc", {"request": 1}, real_seed
        ) == {"answer": "cached"}
    assert namespace["_probe_calls"] == ["seed"]
    with sqlite3.connect(path) as connection:
        seeded = connection.execute("SELECT count(*) FROM engine_outputs").fetchone()[0]
    assert seeded == 1

    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_MODE", "readonly")
    engine_cache._close_connections()
    engine_cache._MEMORY_CACHE.clear()
    engine_cache.engine_cache_counters(reset=True)
    calls: list[str] = []

    def double() -> dict[str, str]:
        calls.append("double")
        return {"answer": "live"}

    with engine_cache.runner_engine_cache_snapshot(str(path), "readonly"):
        assert engine_cache.cached_engine_call(
            "openimcc", {"request": 1}, double
        ) == {"answer": "live"}
        namespace["_probe_calls"] = []
        real_hit = _compile_adapter_solve(
            "def _adapter_solve():\n"
            "    _probe_calls.append('hit')\n"
            "    return {'answer': 'live'}\n",
            namespace,
        )
        assert engine_cache.cached_engine_call(
            "openimcc", {"request": 1}, real_hit
        ) == {"answer": "cached"}
        real_miss = _compile_adapter_solve(
            "def _adapter_solve():\n"
            "    _probe_calls.append('miss')\n"
            "    return {'answer': 'new'}\n",
            namespace,
        )
        assert engine_cache.cached_engine_call(
            "openimcc", {"request": 2}, real_miss
        ) == {"answer": "new"}
    assert calls == ["double"]
    assert namespace["_probe_calls"] == ["miss"]
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT count(*) FROM engine_outputs").fetchone()[0] == 1
    counts = engine_cache.engine_cache_counters()["openimcc"]
    assert counts["hits"] == 1
    assert counts["stores"] == 0
    assert counts["not_stored:double"] == 1
    assert counts["not_stored:readonly"] == 1
    assert counts["misses"] == counts["stores"] + (
        counts["not_stored:double"] + counts["not_stored:readonly"]
    )


def test_replaced_adapter_method_and_mock_are_doubles(monkeypatch) -> None:
    from unittest.mock import Mock

    from simulator.melt_backend.base import InternalAnalyticalBackend

    backend = InternalAnalyticalBackend.__new__(InternalAnalyticalBackend)
    assert engine_cache._solve_is_test_double(backend.equilibrate) is False
    monkeypatch.setattr(backend, "equilibrate", lambda *args, **kwargs: {"answer": 1})
    assert engine_cache._solve_is_test_double(backend.equilibrate) is True

    fresh = InternalAnalyticalBackend.__new__(InternalAnalyticalBackend)
    assert engine_cache._solve_is_test_double(fresh.equilibrate) is False
    monkeypatch.setattr(
        InternalAnalyticalBackend,
        "equilibrate",
        lambda *args, **kwargs: {"answer": 2},
    )
    assert engine_cache._solve_is_test_double(fresh.equilibrate) is True
    assert engine_cache._solve_is_test_double(Mock()) is True
    assert engine_cache._solve_is_test_double(lambda: {"answer": 3}) is True


def test_explicit_cache_overrides_runner_readonly(tmp_path, monkeypatch) -> None:
    runner = tmp_path / "runner.sqlite"
    owned = tmp_path / "owned.sqlite"
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(owned))
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_MODE", "readonly")
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_BOX", "laptop")
    engine_cache._close_connections()
    engine_cache._MEMORY_CACHE.clear()
    engine_cache.engine_cache_counters(reset=True)
    with engine_cache.runner_engine_cache_snapshot(str(runner), "readonly"):
        assert engine_cache.cached_engine_call(
            "openimcc", {"request": 1}, lambda: {"answer": 1}
        ) == {"answer": 1}
    assert not runner.exists()
    with sqlite3.connect(owned) as connection:
        assert connection.execute("SELECT count(*) FROM engine_outputs").fetchone()[0] == 1
    counts = engine_cache.engine_cache_counters()["openimcc"]
    assert counts["stores"] == 1
    assert counts.get("not_stored:readonly", 0) == 0
    assert counts.get("not_stored:double", 0) == 0


def test_path_scope_overrides_runner_readonly(tmp_path, monkeypatch) -> None:
    runner = tmp_path / "runner.sqlite"
    owned = tmp_path / "owned.sqlite"
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(runner))
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_MODE", "readonly")
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_BOX", "laptop")
    engine_cache._close_connections()
    engine_cache._MEMORY_CACHE.clear()
    with engine_cache.runner_engine_cache_snapshot(str(runner), "readonly"):
        with engine_cache._engine_cache_path_scope(owned):
            assert engine_cache.cached_engine_call(
                "openimcc", {"request": 1}, lambda: {"answer": 1}
            ) == {"answer": 1}
    assert not runner.exists()
    with sqlite3.connect(owned) as connection:
        assert connection.execute("SELECT count(*) FROM engine_outputs").fetchone()[0] == 1
