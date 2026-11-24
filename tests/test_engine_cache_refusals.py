import os
import sqlite3
import subprocess
import threading

import pytest

from engines.domain_reason import OutOfDomainReason, STABLE_OUT_OF_DOMAIN_REASONS
from simulator import engine_cache
from simulator.melt_backend.base import _call_with_hard_timeout

pytestmark = pytest.mark.engine_cache_off


def test_deterministic_refusal_allowlist_is_explicit():
    from simulator.engine_cache import (
        CACHEABLE_REFUSAL_CODES,
        REFUSAL_COMPOSITION_PROJECTED,
        REFUSAL_MAJOR_SUM,
        REFUSAL_NO_LIQUID,
        REFUSAL_OUT_OF_BASIS,
        EngineCacheValue,
        cacheable_refusal,
    )
    from engines.alphamelts.thermoengine import ThermoEngineRefusalCause
    from simulator.melt_backend.alphamelts import (
        ALPHAMELTS_REASON_CAO_SIO2_BINARY_CRASH,
        ALPHAMELTS_REASON_EXIT_SIGNAL,
        ALPHAMELTS_REASON_FE_FREE_ABSOLUTE_FO2_CRASH,
        ALPHAMELTS_REASON_FE_FREE_SILICATE_IMPOSED_FO2_CRASH,
        ALPHAMELTS_REASON_NO_CONVERGENCE,
        ALPHAMELTS_REASON_SUBPROCESS_DIED,
    )
    from simulator.melt_backend.openimcc_bridge import (
        ISOLATED_OPENIMCC_CACHEABLE_ERRORS,
    )
    from simulator.melt_backend.vaporock import EMPTY_SPECIATION_STATUS_REASON

    assert CACHEABLE_REFUSAL_CODES == frozenset({
        REFUSAL_OUT_OF_BASIS,
        REFUSAL_COMPOSITION_PROJECTED,
        REFUSAL_MAJOR_SUM,
        REFUSAL_NO_LIQUID,
        OutOfDomainReason.FORBIDDEN_SPECIES.value,
        OutOfDomainReason.LIQUID_STATE.value,
        OutOfDomainReason.INVALID_PRESSURE_BAR.value,
        OutOfDomainReason.INVALID_FO2_LOG.value,
        OutOfDomainReason.SUM_PRESSURE_SANITY.value,
        EMPTY_SPECIATION_STATUS_REASON,
        OutOfDomainReason.SILICATE_WINDOW.value,
        OutOfDomainReason.MAJOR_SUM.value,
        OutOfDomainReason.INVALID_INPUT.value,
        OutOfDomainReason.UNSUPPORTED_INPUT.value,
        OutOfDomainReason.CLAMPED_OPERATING_POINT.value,
        OutOfDomainReason.MAGEMIN_MODE_VECTOR_MASS_DEFICIT.value,
        ALPHAMELTS_REASON_NO_CONVERGENCE,
        ThermoEngineRefusalCause.FO2_NONMONOTONIC_BUFFERED.value,
        ALPHAMELTS_REASON_SUBPROCESS_DIED,
        ALPHAMELTS_REASON_FE_FREE_ABSOLUTE_FO2_CRASH,
        ALPHAMELTS_REASON_CAO_SIO2_BINARY_CRASH,
        ALPHAMELTS_REASON_FE_FREE_SILICATE_IMPOSED_FO2_CRASH,
        "internal_analytical_missing_composition",
        "internal_analytical_invalid_temperature",
        "internal_analytical_invalid_total_pressure",
        "internal_analytical_invalid_composition",
        "internal_analytical_missing_oxygen_condition",
        "internal_analytical_invalid_oxygen_condition",
        "openimcc_composition_canonicalization",
        "imcc_composition_incomplete",
        "imcc_ferric_input_unsupported",
        "imcc_component_outside_domain",
        "imcc_species_not_found",
        "imcc_sp_extension_required",
        "imcc_composition_outside_validated_envelope",
        "imcc_T_outside_datapack_domain",
        "imcc_gas_invalid_fO2",
    })
    assert set(ISOLATED_OPENIMCC_CACHEABLE_ERRORS.values()) <= (
        CACHEABLE_REFUSAL_CODES
    )
    assert "ImccNonconvergenceError" not in ISOLATED_OPENIMCC_CACHEABLE_ERRORS
    assert "internal_analytical_oxygen_balance_unavailable" not in (
        CACHEABLE_REFUSAL_CODES
    )
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
    assert cacheable_refusal(EngineCacheValue(
        {},
        refusal_reason=ALPHAMELTS_REASON_CAO_SIO2_BINARY_CRASH,
        refusal_status="not_converged",
    ))
    assert not cacheable_refusal(EngineCacheValue(
        {},
        refusal_reason=ALPHAMELTS_REASON_CAO_SIO2_BINARY_CRASH,
        refusal_status="process_failure",
    ))
    assert not cacheable_refusal(EngineCacheValue(
        {},
        refusal_reason=OutOfDomainReason.NOT_CONVERGED.value,
        refusal_status="not_converged",
    ))
    for transient in (
        OutOfDomainReason.NOT_CONVERGED.value,
        OutOfDomainReason.BACKEND_UNAVAILABLE.value,
        OutOfDomainReason.OUT_OF_DOMAIN.value,
        "negative_convergence_evidence",
        "engine_not_converged",
        "timeout",
        "nonzero_exit",
        "engine_crash",
        ALPHAMELTS_REASON_EXIT_SIGNAL,
        "unavailable",
        "missing_binary",
        "imcc_nonconvergence",
        "imcc_dataframe_unavailable",
        "imcc_unproven_datapack",
        "imcc_malformed_datapack",
        "imcc_gas_species_not_found",
        "imcc_gas_unknown_activity",
        "imcc_gas_T_outside_domain",
        "imcc_gas_oxygen_balance_failed",
        "imcc_gas_data_unavailable",
        "imcc_gas_duplicate_interval",
        "imcc_gas_invalid_interval",
        "openimcc_not_importable",
        "openimcc_oxygen_balance_unavailable",
    ):
        assert transient not in CACHEABLE_REFUSAL_CODES


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
    # AlphaMELTS v3 carries the engine-cache disclosure value wrapper.
    version = engine_cache.ENGINE_CHEMISTRY_VERSION["alphamelts"]
    key = engine_cache.engine_cache_key("alphamelts", version, {})
    with pytest.raises(ValueError, match="refusal code is not cacheable"):
        engine_cache.load_cache_rows(tmp_path / "cache.sqlite", [(
            key, {"status": "refusal", "refusal_reason": "engine_timeout"},
            "alphamelts", version, "laptop", 1,
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


def _row_count(path) -> int:
    if not os.path.exists(path):
        return 0
    with sqlite3.connect(path) as connection:
        return connection.execute("SELECT count(*) FROM engine_outputs").fetchone()[0]


def _one_cache_row(engine: str, request: dict, answer: dict, seq: int):
    key = engine_cache.engine_cache_key(engine, "1", request)
    return (key, answer, engine, "1", "laptop", seq)


def _expect_readonly(callback, path) -> None:
    with pytest.raises(engine_cache.EngineCacheReadonlyError) as caught:
        callback()
    assert caught.value.path == engine_cache._cache_path_identity(path)


def test_runner_readonly_refuses_every_write_api(tmp_path, monkeypatch) -> None:
    """Drop, load, merge, and snapshot/seed refuse on the runner file.

    Store stays a counted no-op. The typed error is raised before the
    shared file is created or changed. A pytest snapshot and a production
    snapshot of None both identify that file.
    """

    runner = tmp_path / "runner.sqlite"
    donor = tmp_path / "donor.sqlite"
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(runner))
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_BOX", "laptop")
    monkeypatch.delenv("REGOLITH_ENGINE_CACHE_MODE", raising=False)
    engine_cache._close_connections()
    engine_cache._MEMORY_CACHE.clear()
    with engine_cache.runner_engine_cache_snapshot(str(runner), ""):
        assert engine_cache.load_cache_rows(
            runner, [_one_cache_row("magemin", {"n": 1}, {"answer": 1}, 1)]
        ) == 1
        assert engine_cache.load_cache_rows(
            donor, [_one_cache_row("magemin", {"n": 2}, {"answer": 2}, 2)]
        ) == 1
    before = runner.read_bytes()
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_MODE", "readonly")
    engine_cache._close_connections()
    engine_cache._MEMORY_CACHE.clear()
    engine_cache._MEMORY_CACHE[(str(runner), b"k", True)] = {"answer": "mem"}

    with engine_cache.runner_engine_cache_snapshot(str(runner), "readonly"):
        _expect_readonly(lambda: engine_cache.drop_chemistry_version("magemin", "1"), runner)
        _expect_readonly(lambda: engine_cache.load_cache_rows(
            runner, [_one_cache_row("magemin", {"n": 9}, {"answer": 9}, 9)]
        ), runner)
        _expect_readonly(lambda: engine_cache.merge_cache_files(runner, [donor]), runner)
        _expect_readonly(
            lambda: engine_cache.snapshot_cache(runner, tmp_path / "snap.sqlite"), runner
        )
        _expect_readonly(lambda: engine_cache.snapshot_cache(donor, runner), runner)
    assert runner.read_bytes() == before
    assert _row_count(runner) == 1
    assert not (tmp_path / "snap.sqlite").exists()
    assert engine_cache._MEMORY_CACHE[(str(runner), b"k", True)] == {"answer": "mem"}

    fresh = tmp_path / "fresh-runner.sqlite"
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(fresh))
    engine_cache._close_connections()
    previous = engine_cache._RUNNER_CACHE_SNAPSHOT
    engine_cache._RUNNER_CACHE_SNAPSHOT = None
    try:
        _expect_readonly(lambda: engine_cache.drop_chemistry_version("magemin", "1"), fresh)
        _expect_readonly(lambda: engine_cache.load_cache_rows(
            fresh, [_one_cache_row("magemin", {"n": 3}, {"answer": 3}, 3)]
        ), fresh)
        _expect_readonly(lambda: engine_cache.merge_cache_files(fresh, [donor]), fresh)
        _expect_readonly(lambda: engine_cache.snapshot_cache(donor, fresh), fresh)
        _expect_readonly(
            lambda: engine_cache.snapshot_cache(fresh, tmp_path / "out.sqlite"), fresh
        )
        other = tmp_path / "other-master.sqlite"
        assert engine_cache.merge_cache_files(other, [donor])["inserted"] == 1
    finally:
        engine_cache._RUNNER_CACHE_SNAPSHOT = previous
        engine_cache._close_connections()
    assert not fresh.exists()
    assert not (tmp_path / "out.sqlite").exists()
    assert _row_count(other) == 1


def test_owned_cache_stays_writable_while_the_runner_is_readonly(
    tmp_path, monkeypatch
) -> None:
    runner = tmp_path / "runner.sqlite"
    owned = tmp_path / "owned.sqlite"
    donor = tmp_path / "donor.sqlite"
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(donor))
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_BOX", "laptop")
    monkeypatch.delenv("REGOLITH_ENGINE_CACHE_MODE", raising=False)
    engine_cache._close_connections()
    engine_cache._MEMORY_CACHE.clear()
    with engine_cache.runner_engine_cache_snapshot(None, ""):
        assert engine_cache.load_cache_rows(
            runner, [_one_cache_row("magemin", {"n": 1}, {"answer": 1}, 1)]
        ) == 1
        assert engine_cache.load_cache_rows(
            donor, [_one_cache_row("magemin", {"n": 2}, {"answer": 2}, 2)]
        ) == 1
    runner_bytes = runner.read_bytes()
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(owned))
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_MODE", "readonly")
    engine_cache._close_connections()
    with engine_cache.runner_engine_cache_snapshot(str(runner), "readonly"):
        assert engine_cache.load_cache_rows(
            owned, [_one_cache_row("magemin", {"n": 4}, {"answer": 4}, 4)]
        ) == 1
        assert engine_cache.drop_chemistry_version("magemin", "1") == 1
        master = tmp_path / "master.sqlite"
        assert engine_cache.merge_cache_files(master, [donor])["inserted"] == 1
        copy = tmp_path / "copy.sqlite"
        engine_cache.snapshot_cache(donor, copy)
    assert runner.read_bytes() == runner_bytes
    assert _row_count(owned) == 0
    assert copy.exists()
    with engine_cache.runner_engine_cache_snapshot(None, ""):
        monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(owned))
        engine_cache._close_connections()
        engine_cache._MEMORY_CACHE.clear()
        engine_cache.engine_cache_counters(reset=True)
        assert engine_cache.load_cache_rows(
            owned, [_one_cache_row("magemin", {"n": 5}, {"answer": 5}, 5)]
        ) == 1
        assert engine_cache.drop_chemistry_version("magemin", "1") == 1
        assert engine_cache.cached_engine_call(
            "magemin", {"n": 6}, lambda: {"answer": 6}
        ) == {"answer": 6}
    counts = engine_cache.engine_cache_counters()["magemin"]
    assert counts["stores"] == 0
    assert counts["not_stored:readonly"] == 1
    assert _row_count(owned) == 0


def test_symlink_and_relative_runner_paths_stay_readonly(tmp_path, monkeypatch) -> None:
    runner = tmp_path / "runner.sqlite"
    link = tmp_path / "runner-link.sqlite"
    link.symlink_to(runner)
    relative = os.path.relpath(runner)
    relative_link = os.path.relpath(link)
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(runner))
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
    with engine_cache.runner_engine_cache_snapshot(str(runner), ""):
        assert engine_cache.cached_engine_call(
            "openimcc", {"request": 1}, real_seed
        ) == {"answer": "cached"}
    assert _row_count(runner) == 1
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_MODE", "readonly")

    spellings = (
        ("symlink-env", str(link), str(runner)),
        ("relative-env", relative, str(runner)),
        ("relative-snapshot", str(runner), relative),
        ("link-snapshot", str(runner), relative_link),
    )
    for label, env_path, snapshot_path in spellings:
        monkeypatch.setenv("REGOLITH_ENGINE_CACHE", env_path)
        engine_cache._close_connections()
        engine_cache._MEMORY_CACHE.clear()
        engine_cache.engine_cache_counters(reset=True)
        namespace["_probe_calls"] = []
        calls: list[str] = []

        def double() -> dict[str, str]:
            calls.append("double")
            return {"answer": "live"}

        real_hit = _compile_adapter_solve(
            "def _adapter_solve():\n"
            "    _probe_calls.append('hit')\n"
            "    return {'answer': 'live'}\n",
            namespace,
        )
        real_miss = _compile_adapter_solve(
            "def _adapter_solve():\n"
            f"    _probe_calls.append({label!r})\n"
            "    return {'answer': 'new'}\n",
            namespace,
        )
        with engine_cache.runner_engine_cache_snapshot(snapshot_path, "readonly"):
            assert engine_cache.cached_engine_call(
                "openimcc", {"request": 1}, double
            ) == {"answer": "live"}
            assert engine_cache.cached_engine_call(
                "openimcc", {"request": 1}, real_hit
            ) == {"answer": "cached"}
            assert engine_cache.cached_engine_call(
                "openimcc", {"request": label}, real_miss
            ) == {"answer": "new"}
            _expect_readonly(
                lambda: engine_cache.drop_chemistry_version("openimcc", "1"), env_path
            )
            _expect_readonly(
                lambda: engine_cache.load_cache_rows(
                    env_path,
                    [_one_cache_row("openimcc", {"request": 9}, {"answer": 9}, 9)],
                ),
                env_path,
            )
            _expect_readonly(
                lambda: engine_cache.merge_cache_files(env_path, [runner]), env_path
            )
            _expect_readonly(
                lambda: engine_cache.snapshot_cache(
                    env_path, tmp_path / f"{label}-out.sqlite"
                ),
                env_path,
            )
        assert calls == ["double"]
        assert namespace["_probe_calls"] == [label]
        assert _row_count(runner) == 1
        counts = engine_cache.engine_cache_counters()["openimcc"]
        assert counts["hits"] == 1
        assert counts["stores"] == 0
        assert counts["not_stored:double"] == 1
        assert counts["not_stored:readonly"] == 1
        assert not (tmp_path / f"{label}-out.sqlite").exists()

    owned = tmp_path / "owned.sqlite"
    owned_link = tmp_path / "owned-link.sqlite"
    owned_link.symlink_to(owned)
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(owned_link))
    engine_cache._close_connections()
    engine_cache._MEMORY_CACHE.clear()
    engine_cache.engine_cache_counters(reset=True)
    with engine_cache.runner_engine_cache_snapshot(str(link), "readonly"):
        assert engine_cache.cached_engine_call(
            "openimcc", {"request": 1}, lambda: {"answer": 1}
        ) == {"answer": 1}
    assert _row_count(runner) == 1
    assert _row_count(owned) == 1
    assert engine_cache.engine_cache_counters()["openimcc"]["stores"] == 1

def _enable_disk_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(tmp_path / "cache.sqlite"))
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_BOX", "laptop")
    monkeypatch.delenv("REGOLITH_ENGINE_CACHE_MODE", raising=False)
    engine_cache.engine_cache_counters(reset=True)
    engine_cache._MEMORY_CACHE.clear()


def _assert_returned_miss_accounting(engine):
    """Every miss is a store, a store error, a byte conflict, or a named bucket.

    ``not_stored:*`` and ``uncacheable:*`` are the buckets. ``store_error``
    and ``conflicts`` already name their paths, so they count here too.
    """

    counts = engine_cache.engine_cache_counters()[engine]
    accounted = sum(
        value for key, value in counts.items()
        if key.startswith("not_stored:") or key.startswith("uncacheable:")
    )
    assert counts["misses"] == (
        counts.get("stores", 0)
        + counts.get("store_error", 0)
        + counts.get("conflicts", 0)
        + accounted
    )
    return counts


def _equilibrate_through_cache(engine_name, backend, kwargs, tmp_path, monkeypatch):
    from simulator.diagnostic_helpers.binary_pot_battery import (
        EngineHandle,
        _call_cached_backend_equilibrate,
        normalize_engine_settings,
    )

    _enable_disk_cache(tmp_path, monkeypatch)
    handle = EngineHandle(
        name=engine_name,
        backend=backend,
        available=True,
        unavailable_reason=None,
        takes_fo2=True,
        supports_intrinsic_fo2=bool(getattr(backend, "supports_intrinsic_fo2", False)),
        engine_settings=normalize_engine_settings(engine_name, {}),
    )
    calls = []

    def solve():
        calls.append(1)
        return backend.equilibrate(**kwargs)

    return _call_cached_backend_equilibrate, handle, calls, solve


def _assert_disk_round_trip(cached_call, handle, kwargs, calls, code):
    cold = cached_call(handle, kwargs, calls["solve"])
    engine_cache._MEMORY_CACHE.clear()
    warm = cached_call(
        handle, kwargs, lambda: pytest.fail("refusal was not stored"),
    )
    assert cold.status == warm.status
    assert cold.diagnostics["backend_status_reason"] == code
    assert warm.diagnostics["backend_status_reason"] == code
    assert len(calls["calls"]) == 1
    counts = _assert_returned_miss_accounting(handle.name)
    assert counts["hits"] == counts["misses"] == counts["stores"] == 1
    return cold, warm


@pytest.mark.parametrize(
    ("engine_name", "backend_type"),
    [("alphamelts", "alphamelts"), ("thermoengine", "thermoengine")],
)
def test_melts_silicate_window_refusal_hits_after_disk_round_trip(
    engine_name, backend_type, tmp_path, monkeypatch,
):
    if backend_type == "alphamelts":
        from simulator.melt_backend.alphamelts import AlphaMELTSBackend

        backend = AlphaMELTSBackend()
    else:
        from simulator.melt_backend.thermoengine import ThermoEngineBackend

        backend = ThermoEngineBackend()
    kwargs = {
        "temperature_C": 1400.0,
        "composition_kg": {"Al2O3": 1.0},
        "fO2_log": -8.0,
        "pressure_bar": 1.0,
    }
    cached_call, handle, calls, solve = _equilibrate_through_cache(
        engine_name, backend, kwargs, tmp_path, monkeypatch,
    )
    cold, _warm = _assert_disk_round_trip(
        cached_call, handle, kwargs,
        {"solve": solve, "calls": calls},
        OutOfDomainReason.SILICATE_WINDOW.value,
    )
    assert cold.status == "out_of_domain"


def test_alphamelts_prelaunch_crash_family_hits_after_disk_round_trip(
    tmp_path, monkeypatch,
):
    from simulator.melt_backend.alphamelts import (
        ALPHAMELTS_REASON_CAO_SIO2_BINARY_CRASH,
        AlphaMELTSBackend,
    )
    from simulator.melt_backend.alphamelts_contract import isothermal_run_kwargs

    backend = AlphaMELTSBackend()
    backend._mode = "subprocess"
    backend._configure_subprocess_calc_mode(None)
    kwargs = {
        "temperature_C": 1400.0,
        "composition_kg": {"CaO": 0.4, "SiO2": 0.6},
        "fO2_log": -8.0,
        "pressure_bar": 1.0,
    }
    # Production equilibrate_cell passes this. The crash family is after
    # the run-mode check, so an omitted mode raises the contract error
    # and never returns the prelaunch refusal.
    kwargs.update(isothermal_run_kwargs("alphamelts", {"subprocess_run_mode": None}))
    cached_call, handle, calls, solve = _equilibrate_through_cache(
        "alphamelts", backend, kwargs, tmp_path, monkeypatch,
    )
    cold, warm = _assert_disk_round_trip(
        cached_call, handle, kwargs,
        {"solve": solve, "calls": calls},
        ALPHAMELTS_REASON_CAO_SIO2_BINARY_CRASH,
    )
    assert cold.status == warm.status == "not_converged"
    assert cold.diagnostics["backend_failure_category"] == "engine_crash"
    assert warm.diagnostics["backend_failure_category"] == "engine_crash"


def test_alphamelts_empty_composition_major_sum_hits_after_disk_round_trip(
    tmp_path, monkeypatch,
):
    from simulator.melt_backend.alphamelts import AlphaMELTSBackend

    backend = AlphaMELTSBackend()
    kwargs = {
        "temperature_C": 1400.0,
        "composition_kg": {},
        "fO2_log": -8.0,
        "pressure_bar": 1.0,
    }
    cached_call, handle, calls, solve = _equilibrate_through_cache(
        "alphamelts", backend, kwargs, tmp_path, monkeypatch,
    )
    cold, _warm = _assert_disk_round_trip(
        cached_call, handle, kwargs,
        {"solve": solve, "calls": calls},
        OutOfDomainReason.MAJOR_SUM.value,
    )
    assert cold.status == "out_of_domain"


def test_alphamelts_other_redox_without_iron_hits_after_disk_round_trip(
    tmp_path, monkeypatch,
):
    from simulator.melt_backend.alphamelts import AlphaMELTSBackend

    backend = AlphaMELTSBackend()
    kwargs = {
        "temperature_C": 1400.0,
        "composition_kg": {"SiO2": 0.8, "TiO2": 0.2},
        "fO2_log": -8.0,
        "pressure_bar": 1.0,
    }
    cached_call, handle, calls, solve = _equilibrate_through_cache(
        "alphamelts", backend, kwargs, tmp_path, monkeypatch,
    )
    cold, _warm = _assert_disk_round_trip(
        cached_call, handle, kwargs,
        {"solve": solve, "calls": calls},
        OutOfDomainReason.UNSUPPORTED_INPUT.value,
    )
    assert cold.status == "out_of_domain"


def test_alphamelts_uninitialized_refusal_is_not_stored(tmp_path, monkeypatch):
    from simulator.melt_backend.alphamelts import AlphaMELTSBackend

    backend = AlphaMELTSBackend()
    kwargs = {
        "temperature_C": 1400.0,
        "composition_kg": {"SiO2": 0.6, "FeO": 0.4},
        "fO2_log": -8.0,
        "pressure_bar": 1.0,
    }
    cached_call, handle, calls, solve = _equilibrate_through_cache(
        "alphamelts", backend, kwargs, tmp_path, monkeypatch,
    )
    for _ in range(2):
        result = cached_call(handle, kwargs, solve)
        assert result.status == "unavailable"
        engine_cache._MEMORY_CACHE.clear()
    assert len(calls) == 2
    counts = _assert_returned_miss_accounting("alphamelts")
    assert counts["misses"] == 2
    assert counts["stores"] == 0
    assert counts["not_stored:refusal:backend_unavailable"] == 2


def test_alphamelts_process_failure_with_allowlisted_reason_is_not_stored(
    tmp_path, monkeypatch,
):
    from simulator.melt_backend.alphamelts import (
        ALPHAMELTS_REASON_CAO_SIO2_BINARY_CRASH,
        AlphaMELTSBackend,
    )

    backend = AlphaMELTSBackend()
    kwargs = {
        "temperature_C": 1400.0,
        "composition_kg": {"CaO": 0.4, "SiO2": 0.6},
        "fO2_log": -8.0,
        "pressure_bar": 1.0,
    }
    cached_call, handle, calls, _ = _equilibrate_through_cache(
        "alphamelts", backend, kwargs, tmp_path, monkeypatch,
    )

    def solve():
        calls.append(1)
        return backend._subprocess_operational_failure_result(
            temperature_C=1400.0,
            pressure_bar=1.0,
            fO2_log=-8.0,
            warnings=[],
            diagnostics={},
            reason_code=ALPHAMELTS_REASON_CAO_SIO2_BINARY_CRASH,
            failure={"returncode": 1},
            detail="injected nonzero exit",
        )

    for _ in range(2):
        result = cached_call(handle, kwargs, solve)
        assert result.status == "out_of_domain"
        assert (
            result.diagnostics["backend_status_reason"]
            == ALPHAMELTS_REASON_CAO_SIO2_BINARY_CRASH
        )
        engine_cache._MEMORY_CACHE.clear()
    assert len(calls) == 2
    counts = _assert_returned_miss_accounting("alphamelts")
    assert counts["stores"] == 0
    assert counts[
        f"not_stored:refusal:{ALPHAMELTS_REASON_CAO_SIO2_BINARY_CRASH}"
    ] == 2
    with sqlite3.connect(tmp_path / "cache.sqlite") as connection:
        assert connection.execute("SELECT count(*) FROM engine_outputs").fetchone()[0] == 0


def test_thermoengine_uninitialized_valid_melt_raises_and_is_not_stored(
    tmp_path, monkeypatch,
):
    from simulator.melt_backend.thermoengine import ThermoEngineBackend

    backend = ThermoEngineBackend()
    kwargs = {
        "temperature_C": 1400.0,
        "composition_kg": {"SiO2": 0.6, "MgO": 0.4},
        "fO2_log": -8.0,
        "pressure_bar": 1.0,
    }
    cached_call, handle, calls, solve = _equilibrate_through_cache(
        "thermoengine", backend, kwargs, tmp_path, monkeypatch,
    )
    for _ in range(2):
        with pytest.raises(ImportError, match="ThermoEngine transport not initialized"):
            cached_call(handle, kwargs, solve)
        engine_cache._MEMORY_CACHE.clear()
    assert len(calls) == 2
    counts = engine_cache.engine_cache_counters()["thermoengine"]
    # Uninitialized ThermoEngineBackend.equilibrate raises ImportError.
    # That is this family's real raising path: the counter label is the
    # exception class, and the value is not stored.
    assert counts["misses"] == 2
    assert counts["stores"] == 0
    assert counts["not_stored:exception:ImportError"] == 2
    _assert_returned_miss_accounting("thermoengine")


def test_magemin_unavailable_refusal_is_not_stored(tmp_path, monkeypatch):
    from simulator.melt_backend.magemin import MAGEMinBackend

    backend = MAGEMinBackend()
    kwargs = {
        "temperature_C": 1400.0,
        "composition_kg": {"SiO2": 0.6, "MgO": 0.4},
        "pressure_bar": 1.0,
    }
    cached_call, handle, calls, solve = _equilibrate_through_cache(
        "magemin", backend, kwargs, tmp_path, monkeypatch,
    )
    for _ in range(2):
        result = cached_call(handle, kwargs, solve)
        assert result.status == "unavailable"
        engine_cache._MEMORY_CACHE.clear()
    assert len(calls) == 2
    counts = _assert_returned_miss_accounting("magemin")
    assert counts["stores"] == 0
    assert counts["not_stored:refusal:backend_unavailable"] == 2


def test_magemin_empty_composition_invalid_input_hits_after_disk_round_trip(
    tmp_path, monkeypatch,
):
    from simulator.melt_backend.magemin import MAGEMinBackend

    backend = MAGEMinBackend()
    backend._available = True
    backend._bridge = "subprocess"
    kwargs = {
        "temperature_C": 1400.0,
        "composition_kg": {},
        "pressure_bar": 1.0,
    }
    cached_call, handle, calls, solve = _equilibrate_through_cache(
        "magemin", backend, kwargs, tmp_path, monkeypatch,
    )
    cold, _warm = _assert_disk_round_trip(
        cached_call, handle, kwargs,
        {"solve": solve, "calls": calls},
        OutOfDomainReason.INVALID_INPUT.value,
    )
    assert cold.status == "out_of_domain"


def test_magemin_forbidden_species_hits_after_disk_round_trip(
    tmp_path, monkeypatch,
):
    from simulator.melt_backend.magemin import MAGEMinBackend

    backend = MAGEMinBackend()
    backend._available = True
    backend._bridge = "subprocess"
    kwargs = {
        "temperature_C": 1400.0,
        "composition_mol": {"Fe": 1.0, "FeS": 0.5, "NaCl": 0.2},
        "pressure_bar": 1.0,
    }
    cached_call, handle, calls, solve = _equilibrate_through_cache(
        "magemin", backend, kwargs, tmp_path, monkeypatch,
    )
    cold, _warm = _assert_disk_round_trip(
        cached_call, handle, kwargs,
        {"solve": solve, "calls": calls},
        OutOfDomainReason.FORBIDDEN_SPECIES.value,
    )
    assert cold.status == "out_of_domain"


def test_magemin_negative_convergence_is_not_stored(tmp_path, monkeypatch):
    from simulator.melt_backend.magemin import MAGEMinBackend

    backend = MAGEMinBackend()
    backend._available = True
    backend._bridge = "subprocess"
    monkeypatch.setattr(
        backend, "_call_magemin", lambda *_args, **_kwargs: {"converged": False},
    )
    kwargs = {
        "temperature_C": 1400.0,
        "composition_kg": {"SiO2": 0.6, "MgO": 0.4},
        "pressure_bar": 1.0,
    }
    cached_call, handle, calls, solve = _equilibrate_through_cache(
        "magemin", backend, kwargs, tmp_path, monkeypatch,
    )
    for _ in range(2):
        result = cached_call(handle, kwargs, solve)
        assert result.status == "not_converged"
        assert result.diagnostics["backend_status_reason"] == (
            "negative_convergence_evidence"
        )
        engine_cache._MEMORY_CACHE.clear()
    assert len(calls) == 2
    counts = _assert_returned_miss_accounting("magemin")
    assert counts["stores"] == 0
    assert counts["not_stored:refusal:negative_convergence_evidence"] == 2


@pytest.mark.parametrize(
    ("code", "kwargs"),
    [
        (
            "internal_analytical_missing_composition",
            {"composition_kg": None, "temperature_C": 1400.0, "pressure_bar": 1.0,
             "po2_request": ("commanded", 1.0e-8)},
        ),
        (
            "internal_analytical_invalid_temperature",
            {"composition_kg": {"SiO2": 1.0}, "temperature_C": -300.0,
             "pressure_bar": 1.0, "po2_request": ("commanded", 1.0e-8)},
        ),
        (
            "internal_analytical_invalid_total_pressure",
            {"composition_kg": {"SiO2": 1.0}, "temperature_C": 1400.0,
             "pressure_bar": -1.0, "po2_request": ("commanded", 1.0e-8)},
        ),
        (
            "internal_analytical_invalid_composition",
            {"composition_kg": {"SiO2": -1.0}, "temperature_C": 1400.0,
             "pressure_bar": 1.0, "po2_request": ("commanded", 1.0e-8)},
        ),
        (
            "internal_analytical_missing_oxygen_condition",
            {"composition_kg": {"SiO2": 1.0}, "temperature_C": 1400.0,
             "pressure_bar": 1.0, "po2_request": None},
        ),
        (
            "internal_analytical_invalid_oxygen_condition",
            {"composition_kg": {"SiO2": 1.0}, "temperature_C": 1400.0,
             "pressure_bar": 1.0, "po2_request": ("commanded", 0.0)},
        ),
    ],
)
def test_internal_analytical_input_refusal_hits_after_disk_round_trip(
    code, kwargs, tmp_path, monkeypatch,
):
    from types import SimpleNamespace

    from simulator.diagnostic_helpers import binary_pot_battery
    from simulator.diagnostic_helpers.binary_pot_battery import (
        Po2Request,
        _InternalAnalyticalInputRefusal,
        internal_analytical_residue_vapor_pressure,
    )

    _enable_disk_cache(tmp_path, monkeypatch)
    po2 = kwargs["po2_request"]
    po2_request = None if po2 is None else Po2Request(mode=po2[0], po2_bar=po2[1])
    calls = []
    original = binary_pot_battery._internal_analytical_vapor_pressure_adapter

    def wrapped(**adapter_kwargs):
        calls.append(1)
        return original(**adapter_kwargs)

    monkeypatch.setattr(
        binary_pot_battery,
        "_internal_analytical_vapor_pressure_adapter",
        wrapped,
    )
    core = SimpleNamespace(_high_t_melt_activity="openimcc")

    def run():
        return internal_analytical_residue_vapor_pressure(
            core=core,
            temperature_C=kwargs["temperature_C"],
            pressure_bar=kwargs["pressure_bar"],
            composition_kg=kwargs["composition_kg"],
            composition_mol=None,
            fO2_log=None,
            po2_request=po2_request,
            include_diagnostic_shadows=False,
        )

    with pytest.raises(_InternalAnalyticalInputRefusal) as first:
        run()
    engine_cache._MEMORY_CACHE.clear()
    with pytest.raises(_InternalAnalyticalInputRefusal) as second:
        run()

    assert first.value.code == second.value.code == code
    assert first.value.category == second.value.category
    assert str(first.value) == str(second.value)
    assert len(calls) == 1
    counts = _assert_returned_miss_accounting("internal-analytical")
    assert counts["hits"] == counts["misses"] == counts["stores"] == 1
    assert counts.get(f"not_stored:refusal:{code}", 0) == 0


def test_openimcc_canonicalization_refusal_hits_after_disk_round_trip(
    tmp_path, monkeypatch,
):
    pytest.importorskip("openimcc")
    from simulator.melt_backend import openimcc_bridge

    _enable_disk_cache(tmp_path, monkeypatch)
    calls = []
    original = openimcc_bridge._evaluate_uncached

    def wrapped(**adapter_kwargs):
        calls.append(1)
        return original(**adapter_kwargs)

    monkeypatch.setattr(openimcc_bridge, "_evaluate_uncached", wrapped)

    def run():
        return openimcc_bridge.evaluate(
            composition_mol={"FeO": 1.0, "FeO_total": 1.0},
            temperature_K=1600.0,
        )

    with pytest.raises(openimcc_bridge.OpenImccCompositionPolicyRefusal) as first:
        run()
    engine_cache._MEMORY_CACHE.clear()
    with pytest.raises(openimcc_bridge.OpenImccCompositionPolicyRefusal) as second:
        run()

    assert first.value.code == second.value.code == (
        "openimcc_composition_canonicalization"
    )
    assert str(first.value) == str(second.value)
    assert len(calls) == 1
    counts = _assert_returned_miss_accounting("openimcc")
    assert counts["hits"] == counts["misses"] == counts["stores"] == 1


_SCORING_ENGINES = (
    "openimcc",
    "internal-analytical",
    "vaporock",
    "magemin",
    "alphamelts",
    "thermoengine",
)


def test_every_miss_path_is_counted_for_every_scoring_engine(tmp_path, monkeypatch):
    """The measured gap was a miss with no store and no not_stored bucket."""

    from simulator.engine_cache import EngineCacheValue

    _enable_disk_cache(tmp_path, monkeypatch)
    for engine in _SCORING_ENGINES:
        assert engine_cache.cached_engine_call(
            engine, {"path": "store"}, lambda: {"answer": 1},
        ) == {"answer": 1}
        assert engine_cache.cached_engine_call(
            engine, {"path": "cacheable-false"},
            lambda: EngineCacheValue({"answer": 1}, cacheable=False),
        ) == {"answer": 1}
        assert engine_cache.cached_engine_call(
            engine, {"path": "refusal"},
            lambda: {"status": "refusal", "refusal_reason": "engine_timeout"},
        )["refusal_reason"] == "engine_timeout"
        assert engine_cache.cached_engine_call(
            engine, {"path": "uncacheable"},
            lambda: {"nested": {"a": 1}},
        ) == {"nested": {"a": 1}}
        with pytest.raises(ValueError, match="engine failed"):
            engine_cache.cached_engine_call(
                engine,
                {"path": "raise"},
                lambda: (_ for _ in ()).throw(ValueError("engine failed")),
            )
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_MODE", "readonly")
    for engine in _SCORING_ENGINES:
        assert engine_cache.cached_engine_call(
            engine, {"path": "readonly"}, lambda: {"answer": 2},
        ) == {"answer": 2}
        counts = _assert_returned_miss_accounting(engine)
        assert counts["stores"] == 1
        assert counts["not_stored:cacheable_false"] == 1
        assert counts["not_stored:refusal:engine_timeout"] == 1
        assert counts["uncacheable:dict"] == 1
        assert counts["not_stored:exception:ValueError"] == 1
        assert counts["not_stored:readonly"] == 1
        assert counts["misses"] == 6


def test_store_error_and_already_present_misses_are_counted(tmp_path, monkeypatch):
    _enable_disk_cache(tmp_path, monkeypatch)

    def fail_insert(*_args, **_kwargs):
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr(engine_cache, "_insert_cache_row_if_absent", fail_insert)
    assert engine_cache.cached_engine_call(
        "alphamelts", {"path": "store-error"}, lambda: {"answer": 1},
    ) == {"answer": 1}
    counts = _assert_returned_miss_accounting("alphamelts")
    assert counts["misses"] == 1
    assert counts["stores"] == 0
    assert counts["store_error"] == 1

    engine_cache.engine_cache_counters(reset=True)
    engine_cache._MEMORY_CACHE.clear()

    def already_present(*_args, **_kwargs):
        return False, False

    monkeypatch.setattr(engine_cache, "_insert_cache_row_if_absent", already_present)
    assert engine_cache.cached_engine_call(
        "thermoengine", {"path": "already-present"}, lambda: {"answer": 1},
    ) == {"answer": 1}
    counts = _assert_returned_miss_accounting("thermoengine")
    assert counts["misses"] == 1
    assert counts["stores"] == 0
    assert counts["conflicts"] == 0
    assert counts["not_stored:already_present"] == 1


def test_worker_timeout_exception_is_counted_and_not_stored(tmp_path, monkeypatch):
    class WorkerTimeout(RuntimeError):
        def __init__(self) -> None:
            super().__init__("worker timed out")
            self.timed_out = True

    path = tmp_path / "cache.sqlite"
    _enable_disk_cache(tmp_path, monkeypatch)
    with pytest.raises(WorkerTimeout):
        engine_cache.cached_engine_call(
            "magemin",
            {"path": "worker-timeout"},
            lambda: (_ for _ in ()).throw(WorkerTimeout()),
        )
    counts = _assert_returned_miss_accounting("magemin")
    assert counts["misses"] == 1
    assert counts["stores"] == 0
    assert counts["not_stored:exception:timeout"] == 1
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT count(*) FROM engine_outputs"
        ).fetchone()[0] == 0


@pytest.mark.parametrize(
    ("composition_mol", "code", "exception_name"),
    [
        (
            {"SiO2": 1.0, "Fe2O3": 1.0},
            "imcc_ferric_input_unsupported",
            "ImccFerricInputUnsupportedError",
        ),
        (
            {"SiO2": 1.0, "P2O5": 0.1},
            "imcc_sp_extension_required",
            "ImccSPComponentRequiresExtensionError",
        ),
    ],
)
def test_openimcc_presolver_refusal_hits_after_disk_round_trip(
    composition_mol, code, exception_name, tmp_path, monkeypatch,
):
    pytest.importorskip("openimcc")
    from openimcc.kernel import ImccFerricInputUnsupportedError
    from openimcc.model import ImccSPComponentRequiresExtensionError
    from simulator.melt_backend import openimcc_bridge

    expected = {
        "ImccFerricInputUnsupportedError": ImccFerricInputUnsupportedError,
        "ImccSPComponentRequiresExtensionError": (
            ImccSPComponentRequiresExtensionError
        ),
    }[exception_name]
    _enable_disk_cache(tmp_path, monkeypatch)
    calls = []
    original = openimcc_bridge._evaluate_uncached

    def wrapped(**adapter_kwargs):
        calls.append(1)
        return original(**adapter_kwargs)

    monkeypatch.setattr(openimcc_bridge, "_evaluate_uncached", wrapped)

    def run():
        return openimcc_bridge.evaluate(
            composition_mol=composition_mol,
            temperature_K=1600.0,
        )

    with pytest.raises(expected) as first:
        run()
    engine_cache._MEMORY_CACHE.clear()
    with pytest.raises(expected) as second:
        run()

    assert first.value.code == second.value.code == code
    assert str(first.value) == str(second.value)
    assert len(calls) == 1
    counts = _assert_returned_miss_accounting("openimcc")
    assert counts["hits"] == counts["misses"] == counts["stores"] == 1
    assert counts.get(f"not_stored:refusal:{code}", 0) == 0


def test_openimcc_invalid_fo2_refusal_hits_after_disk_round_trip(
    tmp_path, monkeypatch,
):
    openimcc = pytest.importorskip("openimcc")
    from openimcc.gas import ImccGasInvalidFugacityError
    from simulator.melt_backend.openimcc_bridge import evaluate_gas

    _enable_disk_cache(tmp_path, monkeypatch)
    calls = []
    original = openimcc.evaluate_gas

    def wrapped(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(openimcc, "evaluate_gas", wrapped)

    def run():
        return evaluate_gas(
            {"SiO2": 1.0},
            1600.0,
            0.0,
            object(),
        )

    with pytest.raises(ImccGasInvalidFugacityError) as first:
        run()
    engine_cache._MEMORY_CACHE.clear()
    with pytest.raises(ImccGasInvalidFugacityError) as second:
        run()

    assert first.value.code == second.value.code == "imcc_gas_invalid_fO2"
    assert str(first.value) == str(second.value)
    assert len(calls) == 1
    counts = _assert_returned_miss_accounting("openimcc")
    assert counts["hits"] == counts["misses"] == counts["stores"] == 1


def test_openimcc_nonconvergence_is_not_stored(tmp_path, monkeypatch):
    pytest.importorskip("openimcc")
    from openimcc.kernel import ImccNonconvergenceError
    from simulator.melt_backend import openimcc_bridge

    _enable_disk_cache(tmp_path, monkeypatch)

    def refuse(**_kwargs):
        raise ImccNonconvergenceError("did not converge", {"residual": 1.0})

    monkeypatch.setattr(openimcc_bridge, "_evaluate_uncached", refuse)

    def run():
        return openimcc_bridge.evaluate(
            composition_mol={"SiO2": 1.0},
            temperature_K=1600.0,
        )

    with pytest.raises(ImccNonconvergenceError):
        run()
    with pytest.raises(ImccNonconvergenceError):
        run()

    counts = _assert_returned_miss_accounting("openimcc")
    assert counts["misses"] == 2
    assert counts["stores"] == 0
    assert counts["not_stored:exception:imcc_nonconvergence"] == 2


def test_isolated_openimcc_names_match_package_codes():
    pytest.importorskip("openimcc")
    from openimcc.gas import (
        ImccGasDataUnavailableError,
        ImccGasInvalidFugacityError,
        ImccGasOxygenBalanceError,
        ImccGasSpeciesNotFoundError,
        ImccGasTemperatureOutsideDomainError,
        ImccGasUnknownActivityError,
    )
    from openimcc.kernel import (
        ImccComponentOutsideDomainError,
        ImccCompositionIncompleteError,
        ImccDataframeUnavailableError,
        ImccFerricInputUnsupportedError,
        ImccNonconvergenceError,
        ImccSpeciesNotFoundError,
        ImccTOutsideDatapackDomainError,
    )
    from openimcc.model import (
        ImccCompositionOutsideValidatedEnvelopeError,
        ImccMalformedDatapackError,
        ImccSPComponentRequiresExtensionError,
        ImccUnprovenDatapackError,
    )
    from simulator.engine_cache import CACHEABLE_REFUSAL_CODES
    from simulator.melt_backend.openimcc_bridge import (
        ISOLATED_OPENIMCC_CACHEABLE_ERRORS,
    )

    package_classes = {
        "ImccCompositionIncompleteError": ImccCompositionIncompleteError,
        "ImccFerricInputUnsupportedError": ImccFerricInputUnsupportedError,
        "ImccComponentOutsideDomainError": ImccComponentOutsideDomainError,
        "ImccSpeciesNotFoundError": ImccSpeciesNotFoundError,
        "ImccSPComponentRequiresExtensionError": (
            ImccSPComponentRequiresExtensionError
        ),
        "ImccCompositionOutsideValidatedEnvelopeError": (
            ImccCompositionOutsideValidatedEnvelopeError
        ),
        "ImccTOutsideDatapackDomainError": ImccTOutsideDatapackDomainError,
    }
    assert set(package_classes) == set(ISOLATED_OPENIMCC_CACHEABLE_ERRORS)
    for name, refusal_class in package_classes.items():
        assert refusal_class.code == ISOLATED_OPENIMCC_CACHEABLE_ERRORS[name]
        assert refusal_class.code in CACHEABLE_REFUSAL_CODES
    assert ImccGasInvalidFugacityError.code in CACHEABLE_REFUSAL_CODES
    for refusal_class in (
        ImccNonconvergenceError,
        ImccDataframeUnavailableError,
        ImccMalformedDatapackError,
        ImccUnprovenDatapackError,
        ImccGasOxygenBalanceError,
        ImccGasDataUnavailableError,
        ImccGasSpeciesNotFoundError,
        ImccGasTemperatureOutsideDomainError,
        ImccGasUnknownActivityError,
    ):
        assert refusal_class.code not in CACHEABLE_REFUSAL_CODES


def test_isolated_openimcc_input_refusal_hits_after_disk_round_trip(
    tmp_path, monkeypatch,
):
    from simulator.battery import residue

    _enable_disk_cache(tmp_path, monkeypatch)
    calls = []

    def isolated(_evaluate, *, timeout_s, **kwargs):
        del timeout_s
        calls.append(kwargs)
        raise residue.IsolatedOpenImccWorkerError(
            "ImccFerricInputUnsupportedError",
            "Fe2O3 input is unsupported",
        )

    monkeypatch.setattr(residue, "_evaluate_openimcc_isolated", isolated)

    def run():
        return residue._cached_isolated_openimcc_state(
            {"SiO2": 1.0, "Fe2O3": 1.0},
            1600.0,
            timeout_s=15.0,
            allow_extrapolation=True,
            allow_out_of_envelope=True,
        )

    with pytest.raises(residue.IsolatedOpenImccWorkerError) as first:
        run()
    engine_cache._MEMORY_CACHE.clear()
    with pytest.raises(residue.IsolatedOpenImccWorkerError) as second:
        run()

    assert first.value.error_name == second.value.error_name
    assert str(first.value) == str(second.value) == (
        "isolated OpenIMCC ImccFerricInputUnsupportedError: "
        "Fe2O3 input is unsupported"
    )
    assert len(calls) == 1
    counts = _assert_returned_miss_accounting("openimcc")
    assert counts["hits"] == counts["misses"] == counts["stores"] == 1


def test_isolated_openimcc_solver_failure_is_not_stored(tmp_path, monkeypatch):
    from simulator.battery import residue

    _enable_disk_cache(tmp_path, monkeypatch)

    def isolated(_evaluate, *, timeout_s, **_kwargs):
        del timeout_s
        raise residue.ResidueEngineNonconvergence("openimcc", "did not converge")

    monkeypatch.setattr(residue, "_evaluate_openimcc_isolated", isolated)

    def run():
        return residue._cached_isolated_openimcc_state(
            {"SiO2": 1.0},
            1600.0,
            timeout_s=15.0,
            allow_extrapolation=True,
            allow_out_of_envelope=True,
        )

    with pytest.raises(residue.ResidueEngineNonconvergence):
        run()
    with pytest.raises(residue.ResidueEngineNonconvergence):
        run()
    counts = _assert_returned_miss_accounting("openimcc")
    assert counts["misses"] == 2
    assert counts["stores"] == 0
    assert counts["not_stored:exception:ResidueEngineNonconvergence"] == 2

    engine_cache.engine_cache_counters(reset=True)
    engine_cache._MEMORY_CACHE.clear()

    def unmapped(_evaluate, *, timeout_s, **_kwargs):
        del timeout_s
        raise residue.IsolatedOpenImccWorkerError(
            "ImccNonconvergenceError", "did not converge",
        )

    monkeypatch.setattr(residue, "_evaluate_openimcc_isolated", unmapped)

    def run_unmapped():
        return residue._cached_isolated_openimcc_state(
            {"SiO2": 1.0},
            1700.0,
            timeout_s=15.0,
            allow_extrapolation=True,
            allow_out_of_envelope=True,
        )

    with pytest.raises(residue.IsolatedOpenImccWorkerError):
        run_unmapped()
    with pytest.raises(residue.IsolatedOpenImccWorkerError):
        run_unmapped()
    counts = _assert_returned_miss_accounting("openimcc")
    assert counts["misses"] == 2
    assert counts["stores"] == 0
    assert counts["not_stored:exception:ImccNonconvergenceError"] == 2


def test_openimcc_gas_unknown_activity_is_not_stored(tmp_path, monkeypatch):
    """An unrecognised activity key is re-raised and never stored.

    evaluate_gas is the real call. ``NotAGasSpeciesXYZ`` is neither a
    recognised gas parent nor a species, so openimcc raises
    ImccGasUnknownActivityError before any table lookup. The cache does not
    allowlist that code: it counts ``not_stored:exception:<code>`` and the
    exception leaves the call. Two calls run because nothing was stored.

    ``imcc_gas_species_not_found`` is a different path (a requested species,
    a parent missing from a selected reaction, or a missing G(T) row). This
    bridge call does not pass ``gas_species``, and the default reaction list
    skips parents that were not supplied, so this input never reaches it.
    That code stays off the allowlist.
    """

    openimcc = pytest.importorskip("openimcc")
    from openimcc.gas import ImccGasUnknownActivityError
    from simulator.melt_backend.openimcc_bridge import evaluate_gas

    _enable_disk_cache(tmp_path, monkeypatch)
    gas_pack = openimcc.load_gas_datapack()
    calls = []
    original = openimcc.evaluate_gas

    def wrapped(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(openimcc, "evaluate_gas", wrapped)

    def run():
        return evaluate_gas(
            {"NotAGasSpeciesXYZ": 1.0},
            1600.0,
            1.0e-6,
            gas_pack,
            parent_oxides=("NotAGasSpeciesXYZ",),
        )

    with pytest.raises(ImccGasUnknownActivityError) as first:
        run()
    engine_cache._MEMORY_CACHE.clear()
    with pytest.raises(ImccGasUnknownActivityError) as second:
        run()

    code = ImccGasUnknownActivityError.code
    assert first.value.code == second.value.code == code
    assert len(calls) == 2
    counts = _assert_returned_miss_accounting("openimcc")
    assert counts["misses"] == 2
    assert counts["stores"] == 0
    assert counts[f"not_stored:exception:{code}"] == 2


_FE_BEARING_KG = {
    "SiO2": 49.0,
    "Al2O3": 15.0,
    "FeO": 10.0,
    "Fe2O3": 1.0,
    "MgO": 10.0,
    "CaO": 10.0,
    "Na2O": 5.0,
}


def _alphamelts_subprocess_cache(tmp_path, monkeypatch, *, run_mode=True, binary=True):
    from simulator.melt_backend.alphamelts import AlphaMELTSBackend
    from simulator.melt_backend.alphamelts_contract import isothermal_run_kwargs

    backend = AlphaMELTSBackend()
    backend._mode = "subprocess"
    backend._configure_subprocess_calc_mode(None)
    backend._timeout_s = 5.0
    if binary:
        backend._binary_path = "/bin/echo"
    kwargs = {
        "temperature_C": 1400.0,
        "composition_kg": dict(_FE_BEARING_KG),
        "fO2_log": -8.0,
        "pressure_bar": 1.0,
    }
    if run_mode:
        kwargs.update(isothermal_run_kwargs(
            "alphamelts", {"subprocess_run_mode": None},
        ))
    cached_call, handle, calls, solve = _equilibrate_through_cache(
        "alphamelts", backend, kwargs, tmp_path, monkeypatch,
    )
    return backend, cached_call, handle, calls, solve, kwargs


def test_magemin_mode_vector_mass_deficit_hits_after_disk_round_trip(
    tmp_path, monkeypatch,
):
    from simulator.melt_backend.magemin import MAGEMinBackend

    backend = MAGEMinBackend()
    backend._available = True
    backend._bridge = "subprocess"
    monkeypatch.setattr(
        backend,
        "_call_magemin",
        lambda *_args, **_kwargs: {
            "operating_point_diagnostics": {
                "authoritative_for_requested_conditions": True,
                "solved_fO2_log": -8.0,
                "backend_status": "ok",
            },
            "mode_vector_mass_deficit": {"mode_sum": 0.4, "deficit": 0.6},
        },
    )
    kwargs = {
        "temperature_C": 1400.0,
        "composition_kg": {"SiO2": 0.6, "MgO": 0.4},
        "pressure_bar": 1.0,
    }
    cached_call, handle, calls, solve = _equilibrate_through_cache(
        "magemin", backend, kwargs, tmp_path, monkeypatch,
    )
    cold, _warm = _assert_disk_round_trip(
        cached_call, handle, kwargs,
        {"solve": solve, "calls": calls},
        OutOfDomainReason.MAGEMIN_MODE_VECTOR_MASS_DEFICIT.value,
    )
    assert cold.status == "out_of_domain"


def test_alphamelts_no_convergence_hits_after_disk_round_trip(
    tmp_path, monkeypatch,
):
    from simulator.melt_backend.alphamelts import ALPHAMELTS_REASON_NO_CONVERGENCE

    backend, cached_call, handle, calls, solve, kwargs = (
        _alphamelts_subprocess_cache(tmp_path, monkeypatch)
    )
    stdout = (
        "Initial alphaMELTS calculation at: P 1 bars, T 1400 (C)\n"
        "Quadratic convergence failure. Aborting.\n"
    )

    def fake_run(*_args, **_kwargs):
        return subprocess.CompletedProcess(
            ["alphamelts"], 0, stdout, "",
        )

    monkeypatch.setattr(
        "simulator.melt_backend.alphamelts._run_alphamelts_subprocess",
        fake_run,
    )
    del backend
    cold, _warm = _assert_disk_round_trip(
        cached_call, handle, kwargs,
        {"solve": solve, "calls": calls},
        ALPHAMELTS_REASON_NO_CONVERGENCE,
    )
    assert cold.status == "out_of_domain"


@pytest.mark.parametrize(
    ("signame", "stored"),
    [
        ("SIGSEGV", True),
        ("SIGABRT", True),
        ("SIGBUS", True),
        ("SIGKILL", False),
        ("SIGPROF", False),
    ],
)
def test_alphamelts_signal_death_is_stored_only_for_a_binary_fault(
    signame, stored, tmp_path, monkeypatch,
):
    import signal

    from simulator.diagnostic_helpers.binary_pot_battery import (
        REFUSAL_ENGINE_CRASH,
        classify_equilibrate_outcome,
    )
    from simulator.melt_backend.alphamelts import (
        ALPHAMELTS_REASON_EXIT_SIGNAL,
        ALPHAMELTS_REASON_SUBPROCESS_DIED,
    )

    signum = getattr(signal, signame)
    expected_returncode = -int(signum)
    _backend, cached_call, handle, calls, solve, kwargs = (
        _alphamelts_subprocess_cache(tmp_path, monkeypatch)
    )

    def fake_run(*_args, **_kwargs):
        return subprocess.CompletedProcess(
            ["alphamelts"], expected_returncode, "", "",
        )

    monkeypatch.setattr(
        "simulator.melt_backend.alphamelts._run_alphamelts_subprocess",
        fake_run,
    )
    # The store decision reads the live signal. The HOT projection of
    # subprocess_failure keeps returncode only, so a round trip cannot
    # assert the signal name.
    if stored:
        cold, warm = _assert_disk_round_trip(
            cached_call, handle, kwargs,
            {"solve": solve, "calls": calls},
            ALPHAMELTS_REASON_SUBPROCESS_DIED,
        )
        for result in (cold, warm):
            assert (
                result.diagnostics["subprocess_failure"]["returncode"]
                == expected_returncode
            )
            assert result.diagnostics["backend_failure_category"] == "engine_crash"
            _status, reason, _engine_reason = classify_equilibrate_outcome(result)
            assert reason == REFUSAL_ENGINE_CRASH
        return

    for _ in range(2):
        result = cached_call(handle, kwargs, solve)
        assert result.diagnostics["backend_status_reason"] == (
            ALPHAMELTS_REASON_EXIT_SIGNAL
        )
        assert result.diagnostics["backend_failure_category"] == "engine_crash"
        assert (
            result.diagnostics["subprocess_failure"]["returncode"]
            == expected_returncode
        )
        _status, reason, _engine_reason = classify_equilibrate_outcome(result)
        assert reason == REFUSAL_ENGINE_CRASH
        engine_cache._MEMORY_CACHE.clear()
    assert len(calls) == 2
    counts = _assert_returned_miss_accounting("alphamelts")
    assert counts["stores"] == 0
    assert counts[f"not_stored:refusal:{ALPHAMELTS_REASON_EXIT_SIGNAL}"] == 2
    with sqlite3.connect(tmp_path / "cache.sqlite") as connection:
        assert connection.execute(
            "SELECT count(*) FROM engine_outputs"
        ).fetchone()[0] == 0


def test_mislabeled_sigkill_is_not_stored_as_subprocess_died(
    tmp_path, monkeypatch,
):
    """The status gate rejects an external kill even if the reason is wrong."""

    from simulator.melt_backend.alphamelts import ALPHAMELTS_REASON_SUBPROCESS_DIED

    backend, cached_call, handle, calls, _solve, kwargs = (
        _alphamelts_subprocess_cache(tmp_path, monkeypatch)
    )

    def solve():
        calls.append(1)
        return backend._subprocess_operational_failure_result(
            temperature_C=1400.0,
            pressure_bar=1.0,
            fO2_log=-8.0,
            warnings=[],
            diagnostics={},
            reason_code=ALPHAMELTS_REASON_SUBPROCESS_DIED,
            failure={"returncode": -9, "signal": "SIGKILL"},
            detail="external kill labeled as the binary crash",
        )

    for _ in range(2):
        result = cached_call(handle, kwargs, solve)
        assert (
            result.diagnostics["backend_status_reason"]
            == ALPHAMELTS_REASON_SUBPROCESS_DIED
        )
        engine_cache._MEMORY_CACHE.clear()
    assert len(calls) == 2
    counts = _assert_returned_miss_accounting("alphamelts")
    assert counts["stores"] == 0
    assert counts[
        f"not_stored:refusal:{ALPHAMELTS_REASON_SUBPROCESS_DIED}"
    ] == 2


def test_alphamelts_timeout_is_not_stored_as_a_crash(tmp_path, monkeypatch):
    from simulator.melt_backend.alphamelts import ALPHAMELTS_REASON_TIMEOUT

    _backend, cached_call, handle, calls, solve, kwargs = (
        _alphamelts_subprocess_cache(tmp_path, monkeypatch)
    )

    def fake_run(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(["alphamelts"], 5.0)

    monkeypatch.setattr(
        "simulator.melt_backend.alphamelts._run_alphamelts_subprocess",
        fake_run,
    )
    for _ in range(2):
        result = cached_call(handle, kwargs, solve)
        assert result.diagnostics["backend_status_reason"] == (
            ALPHAMELTS_REASON_TIMEOUT
        )
        engine_cache._MEMORY_CACHE.clear()
    assert len(calls) == 2
    counts = _assert_returned_miss_accounting("alphamelts")
    assert counts["stores"] == 0
    assert counts[f"not_stored:refusal:{ALPHAMELTS_REASON_TIMEOUT}"] == 2


def test_alphamelts_missing_binary_raises_and_is_not_stored(
    tmp_path, monkeypatch,
):
    from simulator.melt_backend.alphamelts import (
        ALPHAMELTS_REASON_MISSING_BINARY,
        AlphaMELTSSubprocessContractError,
    )

    _backend, cached_call, handle, calls, solve, kwargs = (
        _alphamelts_subprocess_cache(tmp_path, monkeypatch, binary=False)
    )
    for _ in range(2):
        with pytest.raises(AlphaMELTSSubprocessContractError) as caught:
            cached_call(handle, kwargs, solve)
        assert caught.value.backend_status_reason == (
            ALPHAMELTS_REASON_MISSING_BINARY
        )
        engine_cache._MEMORY_CACHE.clear()
    assert len(calls) == 2
    counts = _assert_returned_miss_accounting("alphamelts")
    assert counts["stores"] == 0
    assert counts[
        "not_stored:exception:AlphaMELTSSubprocessContractError"
    ] == 2


def test_alphamelts_missing_run_mode_raises_through_equilibrate(
    tmp_path, monkeypatch,
):
    """The family's real raising path is the contract error, not a lambda."""

    from simulator.melt_backend.alphamelts import (
        ALPHAMELTS_REASON_RUN_MODE_REQUIRED,
        AlphaMELTSSubprocessContractError,
    )

    _backend, cached_call, handle, calls, solve, kwargs = (
        _alphamelts_subprocess_cache(
            tmp_path, monkeypatch, run_mode=False,
        )
    )
    for _ in range(2):
        with pytest.raises(AlphaMELTSSubprocessContractError) as caught:
            cached_call(handle, kwargs, solve)
        assert caught.value.backend_status_reason == (
            ALPHAMELTS_REASON_RUN_MODE_REQUIRED
        )
        engine_cache._MEMORY_CACHE.clear()
    assert len(calls) == 2
    counts = _assert_returned_miss_accounting("alphamelts")
    assert counts["stores"] == 0
    assert counts[
        "not_stored:exception:AlphaMELTSSubprocessContractError"
    ] == 2


def test_alphamelts_worker_death_is_not_stored(tmp_path, monkeypatch):
    from simulator.melt_backend.alphamelts import (
        ALPHAMELTS_REASON_SUBPROCESS_DIED,
        AlphaMELTSBackend,
        AlphaMELTSSubprocessContractError,
    )

    _enable_disk_cache(tmp_path, monkeypatch)
    backend = AlphaMELTSBackend()

    class _DeadWorker:
        def call(self, *_args, **_kwargs):
            raise RuntimeError("worker exited")

    backend._pet_worker = _DeadWorker()

    def solve():
        return backend._run_petthermotools_isolated("equilibrate_MELTS")

    for _ in range(2):
        with pytest.raises(AlphaMELTSSubprocessContractError) as caught:
            engine_cache.cached_engine_call(
                "alphamelts", {"path": "worker-death"}, solve,
            )
        assert caught.value.backend_status_reason == (
            ALPHAMELTS_REASON_SUBPROCESS_DIED
        )
        engine_cache._MEMORY_CACHE.clear()
    counts = _assert_returned_miss_accounting("alphamelts")
    assert counts["misses"] == 2
    assert counts["stores"] == 0
    assert counts[
        "not_stored:exception:AlphaMELTSSubprocessContractError"
    ] == 2


def test_thermoengine_fo2_nonmonotonic_buffered_is_stored_and_reraised(
    tmp_path, monkeypatch,
):
    from engines.alphamelts.thermoengine import (
        ThermoEngineOutOfDomainError,
        ThermoEngineRefusalCause,
        ThermoEngineTransport,
    )
    from simulator.melt_backend.thermoengine import ThermoEngineBackend

    backend = ThermoEngineBackend()
    transport_calls = []

    class _Transport:
        engine_version = "test"

        def equilibrate(self, **_kwargs):
            transport_calls.append(1)
            ThermoEngineTransport._validate_fO2_order((
                (0.2, -5.0, None, None),
                (0.8, -6.0, None, None),
            ))

    backend._thermoengine_transport = _Transport()
    kwargs = {
        "temperature_C": 1400.0,
        "composition_kg": {"SiO2": 0.6, "MgO": 0.4},
        "fO2_log": -8.0,
        "pressure_bar": 1.0,
    }
    cached_call, handle, calls, solve = _equilibrate_through_cache(
        "thermoengine", backend, kwargs, tmp_path, monkeypatch,
    )
    raised = []
    for _ in range(2):
        with pytest.raises(ThermoEngineOutOfDomainError) as caught:
            cached_call(handle, kwargs, solve)
        raised.append(caught.value)
        engine_cache._MEMORY_CACHE.clear()
    assert len(transport_calls) == 1
    assert len(calls) == 1
    for exc in raised:
        assert exc.cause is ThermoEngineRefusalCause.FO2_NONMONOTONIC_BUFFERED
        assert exc.requested == pytest.approx(-5.0)
        assert exc.solved == pytest.approx(-6.0)
    counts = _assert_returned_miss_accounting("thermoengine")
    assert counts["hits"] == counts["misses"] == counts["stores"] == 1
    assert counts.get(
        "not_stored:exception:ThermoEngineOutOfDomainError", 0,
    ) == 0


def test_thermoengine_other_fo2_cause_is_not_stored(tmp_path, monkeypatch):
    from engines.alphamelts.thermoengine import (
        ThermoEngineOutOfDomainError,
        ThermoEngineRefusalCause,
    )
    from simulator.melt_backend.thermoengine import ThermoEngineBackend

    backend = ThermoEngineBackend()

    class _Transport:
        engine_version = "test"

        def equilibrate(self, **_kwargs):
            raise ThermoEngineOutOfDomainError(
                ThermoEngineRefusalCause.FO2_REQUIRES_IRON,
            )

    backend._thermoengine_transport = _Transport()
    kwargs = {
        "temperature_C": 1400.0,
        "composition_kg": {"SiO2": 0.6, "MgO": 0.4},
        "fO2_log": -8.0,
        "pressure_bar": 1.0,
    }
    cached_call, handle, calls, solve = _equilibrate_through_cache(
        "thermoengine", backend, kwargs, tmp_path, monkeypatch,
    )
    for _ in range(2):
        with pytest.raises(ThermoEngineOutOfDomainError) as caught:
            cached_call(handle, kwargs, solve)
        assert caught.value.cause is ThermoEngineRefusalCause.FO2_REQUIRES_IRON
        engine_cache._MEMORY_CACHE.clear()
    assert len(calls) == 2
    counts = _assert_returned_miss_accounting("thermoengine")
    assert counts["stores"] == 0
    assert counts["not_stored:exception:ThermoEngineOutOfDomainError"] == 2


def test_magemin_worker_timeout_raises_through_equilibrate(
    tmp_path, monkeypatch,
):
    """Declared-input errors are returned. EngineWorkerTimeout is re-raised.

    A hung binary is not a test input. The injection is at the call the
    real except already re-raises, so the counter label is that class.
    """

    from simulator.engine_pool import EngineWorkerTimeout
    from simulator.melt_backend.magemin import MAGEMinBackend

    backend = MAGEMinBackend()
    backend._available = True
    backend._bridge = "subprocess"

    def hang(*_args, **_kwargs):
        raise EngineWorkerTimeout("magemin", 1.0, phase="call")

    monkeypatch.setattr(backend, "_call_magemin", hang)
    kwargs = {
        "temperature_C": 1400.0,
        "composition_kg": {"SiO2": 0.6, "MgO": 0.4},
        "pressure_bar": 1.0,
    }
    cached_call, handle, calls, solve = _equilibrate_through_cache(
        "magemin", backend, kwargs, tmp_path, monkeypatch,
    )
    for _ in range(2):
        with pytest.raises(EngineWorkerTimeout):
            cached_call(handle, kwargs, solve)
        engine_cache._MEMORY_CACHE.clear()
    assert len(calls) == 2
    counts = _assert_returned_miss_accounting("magemin")
    assert counts["stores"] == 0
    assert counts["not_stored:exception:EngineWorkerTimeout"] == 2


def test_vaporock_remote_worker_error_raises_through_equilibrate(
    tmp_path, monkeypatch,
):
    """VapoRockCallError is returned. A remote worker error is not caught."""

    from simulator.engine_pool import EngineWorkerRemoteError

    kwargs = {
        "temperature_C": 1600.0,
        "composition_mol": {"SiO2": 1.0},
        "pressure_bar": 1.0e-6,
        "fO2_log": -8.0,
    }
    cached_call, backend, handle, calls, solve = _vaporock_equilibrium_cache_call(
        kwargs, tmp_path, monkeypatch,
    )

    class _Future:
        def result(self):
            raise EngineWorkerRemoteError("RuntimeError", "worker boom", "tb")

    class _Pool:
        def submit(self, request, timeout_s=None):
            del request, timeout_s
            return _Future()

    backend._warm_pool = _Pool()
    for _ in range(2):
        with pytest.raises(EngineWorkerRemoteError):
            cached_call(handle, kwargs, solve)
        engine_cache._MEMORY_CACHE.clear()
    assert len(calls) == 2
    counts = _assert_returned_miss_accounting("vaporock")
    assert counts["stores"] == 0
    assert counts["not_stored:exception:EngineWorkerRemoteError"] == 2


def test_internal_analytical_oxygen_balance_failure_is_not_stored(
    tmp_path, monkeypatch,
):
    """The adapter raises the non-allowlisted code into the cache except."""

    from types import SimpleNamespace

    from simulator.diagnostic_helpers import binary_pot_battery
    from simulator.diagnostic_helpers.binary_pot_battery import (
        Po2Request,
        _InternalAnalyticalInputRefusal,
        internal_analytical_residue_vapor_pressure,
    )

    _enable_disk_cache(tmp_path, monkeypatch)

    class _NonOk:
        def equilibrate(self, **_kwargs):
            return SimpleNamespace(status="not_converged", diagnostics={})

    monkeypatch.setattr(
        binary_pot_battery,
        "_OpenImccBatteryBackend",
        lambda *_args, **_kwargs: _NonOk(),
    )
    core = SimpleNamespace(_high_t_melt_activity="openimcc")

    def run():
        return internal_analytical_residue_vapor_pressure(
            core=core,
            temperature_C=1400.0,
            pressure_bar=1.0,
            composition_kg={"SiO2": 1.0},
            composition_mol=None,
            fO2_log=None,
            po2_request=Po2Request(
                mode="oxygen_balance_effusion", po2_bar=None,
            ),
            include_diagnostic_shadows=False,
        )

    for _ in range(2):
        with pytest.raises(_InternalAnalyticalInputRefusal) as caught:
            run()
        assert caught.value.code == (
            "internal_analytical_oxygen_balance_unavailable"
        )
        engine_cache._MEMORY_CACHE.clear()
    counts = _assert_returned_miss_accounting("internal-analytical")
    assert counts["misses"] == 2
    assert counts["stores"] == 0
    assert counts[
        "not_stored:exception:internal_analytical_oxygen_balance_unavailable"
    ] == 2
