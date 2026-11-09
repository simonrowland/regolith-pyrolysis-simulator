from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from engines.alphamelts.thermoengine import (
    ThermoEngineOutOfDomainError,
    ThermoEngineRefusalCause,
)
from simulator import engine_cache
from simulator.diagnostic_helpers import binary_pot_battery as battery
from simulator.diagnostic_helpers.binary_pot_battery import (
    BinaryPot,
    EngineHandle,
    EquilibrateCell,
    Po2Request,
    equilibrate_cell,
)
from simulator.engine_cache import EngineCache, cached_solve


def _cell(
    *,
    engine: str,
    disposition: str | None = None,
    status: str = "not_converged",
    refusal_reason: str | None = "not_converged",
    engine_status: str | None = "not_converged",
    engine_reason: str | None = "not_converged",
) -> EquilibrateCell:
    return EquilibrateCell(
        pot_id="cache-r2",
        engine=engine,
        temperature_K=1700.0,
        po2=Po2Request("engine_default", None),
        status=status,
        refusal_reason=refusal_reason,
        engine_status=engine_status,
        engine_reason=engine_reason,
        melt_activities={},
        gas_partial_pressures_Pa={},
        liquid_fraction=None,
        wall_s=0.0,
        cpu_s=0.0,
        hostname="test",
        backend_failure_disposition=disposition,
    )


def test_isolated_magemin_environment_failure_is_transient_across_cache_boundary(
    tmp_path: Path, monkeypatch
) -> None:
    handle = EngineHandle(
        name="magemin",
        backend=object(),
        available=True,
        unavailable_reason=None,
        takes_fo2=True,
        supports_intrinsic_fo2=False,
    )
    pot = BinaryPot(
        pot_id="isolated-magemin-environment",
        kato_1993_table4_system=None,
        why="cache regression",
        composition_wt_pct={"SiO2": 50.0, "FeO": 50.0},
    )
    adapter_calls = 0

    def environment_failure(*_args, **_kwargs) -> EquilibrateCell:
        nonlocal adapter_calls
        adapter_calls += 1
        return _cell(
            engine="magemin",
            disposition="environment",
            engine_status="MAGEMinEngineCallError",
            engine_reason="engine setup failed",
        )

    class InProcessWorker:
        def request(self, payload, *, timeout_s):
            return battery.run_isolated_cell_worker(payload, handle=handle)

        def retire(self):
            pass

    monkeypatch.setattr(battery, "equilibrate_cell", environment_failure)
    monkeypatch.setattr(
        battery, "_isolated_cell_worker", lambda *_args: InProcessWorker()
    )
    monkeypatch.setattr(engine_cache, "runtime_identity", lambda _engine: {"runtime": "test"})

    def solve() -> EquilibrateCell:
        return battery._run_cell_in_subprocess(
            handle,
            pot,
            temperature_K=1700.0,
            po2=Po2Request("engine_default", None),
            timeout_s=5.0,
            qualification=False,
            arm="headline",
            notices=(),
            authority=None,
            certified_band=None,
        )

    cache = EngineCache(tmp_path / "engine-cache.sqlite")
    request = {"call": "equilibrate_cell", "case": "magemin-environment"}
    first = cached_solve(cache, "magemin", request, solve)
    second = cached_solve(cache, "magemin", request, solve)

    assert first.backend_failure_disposition == "environment"
    assert second.backend_failure_disposition == "environment"
    assert adapter_calls == 2
    assert cache.counters["magemin"]["stores"] == 0
    assert cache.counters["magemin"]["hits"] == 0
    assert cache.counters["magemin"]["transient_skips"] == 2


def test_qualification_decoration_matches_cache_off_cold_and_warm(
    tmp_path: Path, monkeypatch
) -> None:
    class AlphaBackend:
        _mode = "python"
        _model = "MELTS"
        _python_api_model = "MELTS"
        _redox_buffer = None
        _fo2_offset = None
        _fe3fet_ratio = None
        _configured_vapor_transport_pO2_bar = None
        _configured_vapor_transport_floor_bar = None

        def equilibrate(self, **_kwargs):
            return SimpleNamespace(
                status="not_converged",
                diagnostics={"backend_failure_disposition": "environment"},
                warnings=[],
                activity_coefficients={},
                vapor_pressures_Pa={},
                liquid_fraction=None,
            )

    handle = EngineHandle(
        name="alphamelts",
        backend=AlphaBackend(),
        available=True,
        unavailable_reason=None,
        takes_fo2=True,
        supports_intrinsic_fo2=False,
    )
    pot = BinaryPot(
        pot_id="qualification-environment-refusal",
        kato_1993_table4_system=None,
        why="cache regression",
        composition_wt_pct={"SiO2": 20.0, "FeO": 48.0, "MgO": 32.0},
    )

    def evaluate(cache: EngineCache | None = None) -> EquilibrateCell:
        return equilibrate_cell(
            handle,
            pot,
            temperature_K=1700.0,
            po2=Po2Request("engine_default", None),
            qualification=True,
            isolated=False,
            engine_cache=cache,
        )

    cache = EngineCache(tmp_path / "qualification-cache.sqlite")
    off = evaluate()
    cold = evaluate(cache)
    warm = evaluate(cache)

    assert off.refusal_reason == cold.refusal_reason == warm.refusal_reason
    assert off.refusal_reason == "gate_refused_in_adapter"
    assert cold.backend_failure_disposition == "environment"
    assert warm.backend_failure_disposition == "environment"
    assert cache.counters["alphamelts"]["transient_skips"] == 2


def test_typed_thermoengine_domain_refusal_is_cached(
    tmp_path: Path,
) -> None:
    class ThermoBackend:
        _model = "MELTSv1.0.2"
        _mode = "thermoengine"
        _configured_vapor_transport_pO2_bar = 1.0e-9
        _configured_vapor_transport_floor_bar = 1.0e-9
        _health_timeout_s = 30.0
        _thermoengine_transport = SimpleNamespace(equilibrate_timeout_s=3.0)
        _config = {}

        def equilibrate(self, **_kwargs):
            raise ThermoEngineOutOfDomainError(
                ThermoEngineRefusalCause.FO2_NONMONOTONIC_BUFFERED,
                requested=-3.0,
                solved=-3.0,
            )

    handle = EngineHandle(
        name="thermoengine",
        backend=ThermoBackend(),
        available=True,
        unavailable_reason=None,
        takes_fo2=True,
        supports_intrinsic_fo2=True,
    )
    pot = BinaryPot(
        pot_id="thermoengine-row-local-refusal",
        kato_1993_table4_system=None,
        why="cache regression",
        composition_wt_pct={"SiO2": 50.0, "FeO": 50.0},
    )

    def evaluate(cache: EngineCache | None = None) -> EquilibrateCell:
        return equilibrate_cell(
            handle,
            pot,
            temperature_K=1700.0,
            po2=Po2Request("commanded", 1.0e-3),
            isolated=False,
            engine_cache=cache,
        )

    cache = EngineCache(tmp_path / "thermoengine-refusal-cache.sqlite")
    off = evaluate()
    cold = evaluate(cache)
    warm = evaluate(cache)

    assert off.refusal_reason == cold.refusal_reason == warm.refusal_reason
    assert cold.refusal_reason == "fo2_nonmonotonic_buffered"
    assert cache.counters["thermoengine"]["misses"] == 1
    assert cache.counters["thermoengine"]["stores"] == 1
    assert cache.counters["thermoengine"]["hits"] == 1
    assert cache.counters["thermoengine"]["transient_skips"] == 0
