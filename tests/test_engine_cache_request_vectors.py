from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from simulator.diagnostic_helpers.binary_pot_battery import (
    BinaryPot,
    EngineHandle,
    Po2Request,
    _engine_cache_request_vector,
    equilibrate_cell,
)
from simulator.engine_cache import EngineCache


class _Backend:
    def __init__(self, name: str) -> None:
        self.name = name
        self.calls = 0
        self._mode = "python_api"
        self._model = "model-a"
        self._raw_model_name = "model-a"
        self._redox_buffer = "QFM"
        self._fo2_offset = 0.0
        self._fe3fet_ratio = None
        self._python_api_model = ("model-a", 1)
        self._bridge = "subprocess"
        self._database = "ig"
        self._config = {"fO2_buffer": None}
        self._binary_path = None
        self._pack_name = "default"
        self._allow_extrapolation = True
        self._allow_out_of_envelope = True
        self._temperature_units = "C"
        self._pressure_units = "bar"
        self._vapor_pressure_units = "bar"
        self._vapor_transport_floor_bar = 1.0e-12
        self._configured_vapor_transport_floor_bar = 1.0e-12
        self._configured_vapor_transport_pO2_bar = 1.0e-9
        self._vapor_transport_pO2_admission = "environment"

    def equilibrate(self, **_kwargs: Any) -> Any:
        self.calls += 1
        return SimpleNamespace(
            status="ok",
            diagnostics={},
            warnings=[],
            activity_coefficients={"SiO2": 0.5},
            vapor_pressures_Pa={},
            liquid_fraction=0.9,
            phase_assemblage_available=True,
        )

    @staticmethod
    def _pressure_bar_to_GPa(value: float) -> float:
        return value / 10_000.0

    @staticmethod
    def _GPa_to_kbar(value: float) -> float:
        return value * 10.0


def _handle(name: str, backend: _Backend | None = None) -> EngineHandle:
    backend = backend or _Backend(name)
    if name == "openimcc":
        from simulator.melt_backend import openimcc_bridge

        backend._bridge = openimcc_bridge
    return EngineHandle(
        name=name,
        backend=backend,
        available=True,
        unavailable_reason=None,
        takes_fo2=True,
        supports_intrinsic_fo2=True,
    )


def _pot(pot_id: str = "cache-key-pot") -> BinaryPot:
    return BinaryPot(
        pot_id=pot_id,
        kato_1993_table4_system=None,
        why="cache request vector fixture",
        composition_wt_pct={"SiO2": 50.0, "Al2O3": 50.0},
    )


def _vector(
    handle: EngineHandle,
    *,
    pressure_bar: float = 1.0,
    po2: Po2Request | None = None,
    timeout_s: float | None = None,
) -> dict[str, Any]:
    value = _engine_cache_request_vector(
        handle,
        _pot(),
        temperature_K=1703.0,
        po2=po2 or Po2Request("engine_default", None),
        physical_pressure_bar=pressure_bar,
        native_activity_formula=None,
        timeout_s=timeout_s,
    )
    assert value is not None
    return value


@pytest.mark.parametrize(
    ("engine", "first", "second"),
    [
        ("thermoengine", 1.0e-6, 1.0e-9),
        ("vaporock", 1.0e-6, -1.0),
    ],
)
def test_request_vector_keeps_pressure_seen_by_adapter(
    engine: str, first: float, second: float
) -> None:
    handle = _handle(engine)

    first_vector = _vector(handle, pressure_bar=first)
    second_vector = _vector(handle, pressure_bar=second)

    assert first_vector != second_vector


def test_request_vector_distinguishes_qualification_calls() -> None:
    handle = _handle("thermoengine")

    ordinary = _engine_cache_request_vector(
        handle,
        _pot(),
        temperature_K=1703.0,
        po2=Po2Request("engine_default", None),
        physical_pressure_bar=1.0,
        native_activity_formula=None,
        qualification=False,
    )
    qualification = _engine_cache_request_vector(
        handle,
        _pot(),
        temperature_K=1703.0,
        po2=Po2Request("engine_default", None),
        physical_pressure_bar=1.0,
        native_activity_formula=None,
        qualification=True,
    )

    assert ordinary is not None and qualification is not None
    assert ordinary != qualification


def test_request_vector_tracks_ambient_alphamelts_calc_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handle = _handle("alphamelts")
    monkeypatch.setenv("ALPHAMELTS_CALC_MODE", "MELTS")
    expected_mode = _vector(handle)
    monkeypatch.setenv("ALPHAMELTS_CALC_MODE", "pMELTS")
    conflicting_mode = _vector(handle)

    assert expected_mode != conflicting_mode


def test_alphamelts_calc_mode_conflict_matches_cache_off_and_warm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class CalcModeBackend(_Backend):
        def equilibrate(self, **_kwargs: Any) -> Any:
            self.calls += 1
            if os.environ.get("ALPHAMELTS_CALC_MODE") not in {None, "MELTS"}:
                return SimpleNamespace(
                    status="unavailable",
                    diagnostics={
                        "backend_status_reason": "alphamelts_calc_mode_mismatch"
                    },
                    warnings=[],
                    activity_coefficients={},
                    vapor_pressures_Pa={},
                    liquid_fraction=None,
                )
            return SimpleNamespace(
                status="ok",
                diagnostics={},
                warnings=[],
                activity_coefficients={"SiO2": 0.5},
                vapor_pressures_Pa={},
                liquid_fraction=0.9,
            )

    backend = CalcModeBackend("alphamelts")
    handle = _handle("alphamelts", backend)
    pot = _pot()
    cache = EngineCache(tmp_path / "alphamelts-calc-mode.sqlite")

    monkeypatch.setenv("ALPHAMELTS_CALC_MODE", "MELTS")
    cached_expected_mode = equilibrate_cell(
        handle,
        pot,
        temperature_K=1703.0,
        po2=Po2Request("engine_default", None),
        isolated=False,
        engine_cache=cache,
    )
    assert cached_expected_mode.status == "ok"

    monkeypatch.setenv("ALPHAMELTS_CALC_MODE", "pMELTS")
    conflict_off = equilibrate_cell(
        handle,
        pot,
        temperature_K=1703.0,
        po2=Po2Request("engine_default", None),
        isolated=False,
    )
    conflict_warm = equilibrate_cell(
        handle,
        pot,
        temperature_K=1703.0,
        po2=Po2Request("engine_default", None),
        isolated=False,
        engine_cache=cache,
    )

    assert conflict_off.status == conflict_warm.status == "refusal"
    assert conflict_off.refusal_reason == conflict_warm.refusal_reason
    assert conflict_warm.engine_reason == "alphamelts_calc_mode_mismatch"
    assert backend.calls == 3


@pytest.mark.parametrize(
    ("engine", "setting", "value"),
    [
        ("alphamelts", "_model", "model-b"),
        ("alphamelts", "_mode", "subprocess"),
        ("alphamelts", "_redox_buffer", "NNO"),
        ("alphamelts", "_fo2_offset", 0.25),
        ("alphamelts", "_fe3fet_ratio", 0.2),
        ("alphamelts", "_python_api_model", ("model-b", 2)),
        ("alphamelts", "_configured_vapor_transport_pO2_bar", 1.0e-8),
        ("alphamelts", "_configured_vapor_transport_floor_bar", 1.0e-10),
        ("alphamelts", "_timeout_s", 42.0),
        ("alphamelts", "_pet_warm_enabled", True),
        ("alphamelts", "_pet_warm_timeout_s", 43.0),
        ("alphamelts", "_binary_path", "/other/alphamelts"),
        ("thermoengine", "_model", "model-b"),
        ("thermoengine", "_mode", "alternate-transport"),
        ("thermoengine", "_configured_vapor_transport_pO2_bar", 1.0e-8),
        ("thermoengine", "_configured_vapor_transport_floor_bar", 1.0e-10),
        ("thermoengine", "_health_timeout_s", 42.0),
        ("magemin", "_bridge", "julia"),
        ("magemin", "_database", "mp"),
        ("magemin", "_binary_path", "/other/MAGEMin"),
        ("openimcc", "_pack_name", "published-core"),
        ("vaporock", "_database_path", "/other/vaporock"),
        ("vaporock", "_temperature_units", "K"),
        ("vaporock", "_pressure_units", "Pa"),
        ("vaporock", "_vapor_pressure_units", "Pa"),
        ("vaporock", "_reuse_system", True),
        ("vaporock", "_warm_worker_enabled", True),
        ("vaporock", "_warm_pool_size", 2),
        ("vaporock", "_warm_call_timeout_s", 42.0),
        ("vaporock", "_worker_startup_timeout_s", 43.0),
    ],
)
def test_request_vector_changes_when_engine_setting_changes(
    engine: str, setting: str, value: Any
) -> None:
    backend = _Backend(engine)
    handle = _handle(engine, backend)
    first = _vector(handle)

    setattr(backend, setting, value)
    second = _vector(handle)

    assert first != second, f"{engine}.{setting} is missing from the cache key"


def test_request_vector_changes_when_magemin_buffer_setting_changes() -> None:
    backend = _Backend("magemin")
    handle = _handle("magemin", backend)
    first = _vector(handle)

    backend._config["fO2_buffer"] = "nno"
    second = _vector(handle)

    assert first != second


@pytest.mark.parametrize(
    ("engine", "setting", "value"),
    [
        ("alphamelts", "warm_worker", True),
        ("alphamelts", "petthermotools_warm_call_timeout_s", 42.0),
        ("alphamelts", "timeout_s", 43.0),
        ("thermoengine", "thermoengine_health_timeout_s", 42.0),
        ("thermoengine", "thermoengine_equilibrate_timeout_s", 43.0),
        ("magemin", "warm_worker", False),
        ("magemin", "warm_call_timeout_s", 42.0),
        ("magemin", "warm_pool_size", 2),
        ("magemin", "worker_startup_timeout_s", 43.0),
        ("magemin", "timeout_s", 44.0),
        ("vaporock", "warm_worker", True),
        ("vaporock", "warm_pool_size", 2),
        ("vaporock", "warm_call_timeout_s", 42.0),
        ("vaporock", "worker_startup_timeout_s", 43.0),
    ],
)
def test_request_vector_changes_when_backend_config_changes(
    engine: str, setting: str, value: Any
) -> None:
    backend = _Backend(engine)
    handle = _handle(engine, backend)
    first = _vector(handle)

    backend._config[setting] = value
    second = _vector(handle)

    assert first != second, f"{engine}.config[{setting!r}] is missing from the cache key"


def test_request_vector_changes_when_caller_timeout_changes() -> None:
    handle = _handle("magemin")

    ordinary = _vector(handle, timeout_s=30.0)
    longer = _vector(handle, timeout_s=31.0)

    assert ordinary != longer


def test_request_vector_tracks_magemin_slot_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handle = _handle("magemin")
    monkeypatch.setenv("REGOLITH_MAGEMIN_SLOTS", "1")
    serialized = _vector(handle)
    monkeypatch.setenv("REGOLITH_MAGEMIN_SLOTS", "3")

    assert serialized != _vector(handle)


def test_request_vector_tracks_vaporock_session_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handle = _handle("vaporock")
    monkeypatch.delenv("REGOLITH_VAPOROCK_SESSION_WARM", raising=False)
    ordinary = _vector(handle)
    monkeypatch.setenv("REGOLITH_VAPOROCK_SESSION_WARM", "1")

    assert ordinary != _vector(handle)


@pytest.mark.parametrize(
    "variable",
    ("DYLD_LIBRARY_PATH", "DYLD_FALLBACK_LIBRARY_PATH"),
)
def test_request_vector_tracks_thermoengine_library_environment(
    monkeypatch: pytest.MonkeyPatch, variable: str
) -> None:
    handle = _handle("thermoengine")
    monkeypatch.setenv(variable, "/first/thermoengine")
    first = _vector(handle)
    monkeypatch.setenv(variable, "/second/thermoengine")

    assert first != _vector(handle)


def test_request_vector_changes_when_magemin_native_activity_changes() -> None:
    handle = _handle("magemin")
    common = {
        "temperature_K": 1703.0,
        "po2": Po2Request("engine_default", None),
        "physical_pressure_bar": 1.0,
    }

    without_activity = _engine_cache_request_vector(
        handle,
        _pot(),
        **common,
        native_activity_formula=None,
    )
    with_activity = _engine_cache_request_vector(
        handle,
        _pot(),
        **common,
        native_activity_formula="SiO2",
    )

    assert without_activity is not None and with_activity is not None
    assert without_activity != with_activity


def test_request_vector_changes_when_vaporock_model_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import simulator.melt_backend.vaporock as vaporock

    handle = _handle("vaporock")
    first = _vector(handle)
    monkeypatch.setattr(vaporock, "_MELT_MODEL_ID", "MELTS-v2")
    second = _vector(handle)

    assert first != second


def test_request_vector_uses_exact_magemin_pressure() -> None:
    handle = _handle("magemin")

    left = _vector(handle, pressure_bar=1.0001)
    right = _vector(handle, pressure_bar=1.0002)

    assert left != right


@pytest.mark.parametrize(
    "engine",
    [
        "openimcc",
        "internal-analytical",
        "vaporock",
        "magemin",
        "alphamelts",
        "thermoengine",
    ],
)
def test_request_vector_keys_each_engine_call_input(engine: str) -> None:
    handle = _handle(engine)
    default = _vector(handle)
    warmer = _engine_cache_request_vector(
        handle,
        replace(_pot(), composition_wt_pct={"SiO2": 60.0, "Al2O3": 40.0}),
        temperature_K=1704.0,
        po2=Po2Request("commanded", 1.0e-7),
        physical_pressure_bar=1.0001,
        native_activity_formula=None,
    )

    assert warmer is not None
    assert default != warmer


def test_openimcc_ferric_and_ferrous_requests_have_distinct_keys() -> None:
    from simulator.accounting.formulas import resolve_species_formula

    backend = _Backend("openimcc")
    handle = _handle("openimcc", backend)

    def pot_for_moles(pot_id: str, oxides: dict[str, float]) -> BinaryPot:
        masses = {
            oxide: amount * resolve_species_formula(oxide).molar_mass_kg_per_mol()
            for oxide, amount in oxides.items()
        }
        total = sum(masses.values())
        return replace(
            _pot(pot_id),
            composition_wt_pct={
                oxide: mass / total * 100.0 for oxide, mass in masses.items()
            },
        )

    ferric = _engine_cache_request_vector(
        handle,
        pot_for_moles("ferric", {"Fe2O3": 1.0, "SiO2": 1.0}),
        temperature_K=1703.0,
        po2=Po2Request("engine_default", None),
        physical_pressure_bar=1.0,
        native_activity_formula=None,
    )
    ferrous = _engine_cache_request_vector(
        handle,
        pot_for_moles("ferrous", {"FeO": 2.0, "SiO2": 1.0}),
        temperature_K=1703.0,
        po2=Po2Request("engine_default", None),
        physical_pressure_bar=1.0,
        native_activity_formula=None,
    )

    assert ferric is not None and ferrous is not None
    assert ferric != ferrous


@pytest.mark.parametrize(
    ("engine", "good_pressure", "bad_pressure"),
    [
        ("thermoengine", 1.0e-6, 1.0e-9),
        ("vaporock", 1.0e-6, -1.0),
    ],
)
def test_pressure_boundary_outcome_matches_cache_off_and_warm(
    tmp_path: Path,
    engine: str,
    good_pressure: float,
    bad_pressure: float,
) -> None:
    backend = _Backend(engine)

    def adapter_equilibrate(**kwargs: Any) -> Any:
        backend.calls += 1
        pressure = float(kwargs["pressure_bar"])
        if engine == "thermoengine":
            from simulator.melt_backend.thermoengine import (
                thermoengine_effective_pressure_policy,
            )

            effective, _label = thermoengine_effective_pressure_policy(pressure)
            status = "ok" if pressure == effective else "out_of_domain"
            diagnostics = (
                {} if status == "ok" else {"backend_status_reason": "clamped_operating_point"}
            )
        else:
            from simulator.melt_backend.vaporock import vaporock_pressure_bar_admitted

            status = "ok" if vaporock_pressure_bar_admitted(pressure) else "out_of_domain"
            diagnostics = (
                {} if status == "ok" else {"backend_status_reason": "invalid_pressure_bar"}
            )
        return SimpleNamespace(
            status=status,
            diagnostics=diagnostics,
            warnings=[],
            activity_coefficients={"SiO2": 0.5} if status == "ok" else {},
            vapor_pressures_Pa={},
            liquid_fraction=0.9,
            phase_assemblage_available=True,
        )

    backend.equilibrate = adapter_equilibrate  # type: ignore[method-assign]
    handle = _handle(engine, backend)
    pot = _pot()
    po2 = Po2Request("engine_default", None)

    uncached = equilibrate_cell(
        handle,
        pot,
        temperature_K=1703.0,
        po2=po2,
        isolated=False,
        physical_pressure_bar=bad_pressure,
    )
    cache_path = tmp_path / f"{engine}.sqlite"
    cache = EngineCache(cache_path)
    equilibrate_cell(
        handle,
        pot,
        temperature_K=1703.0,
        po2=po2,
        isolated=False,
        physical_pressure_bar=good_pressure,
        engine_cache=cache,
    )
    warm = equilibrate_cell(
        handle,
        pot,
        temperature_K=1703.0,
        po2=po2,
        isolated=False,
        physical_pressure_bar=bad_pressure,
        engine_cache=cache,
    )

    assert warm.status == uncached.status
    assert warm.refusal_reason == uncached.refusal_reason


def test_qualification_cells_share_engine_result_and_rebuild_decorations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import simulator.diagnostic_helpers.binary_pot_battery as battery

    backend = _Backend("alphamelts")
    handle = _handle("alphamelts", backend)
    pot = _pot()
    assessment_calls = 0
    actual_assess = battery.assess_qualification_gate

    def count_assessment(*args: Any, **kwargs: Any) -> Any:
        nonlocal assessment_calls
        assessment_calls += 1
        return actual_assess(*args, **kwargs)

    monkeypatch.setattr(battery, "assess_qualification_gate", count_assessment)
    cache = EngineCache(tmp_path / "qualification.sqlite")
    first = equilibrate_cell(
        handle,
        pot,
        temperature_K=1703.0,
        po2=Po2Request("engine_default", None),
        qualification=True,
        isolated=False,
        engine_cache=cache,
    )
    second = equilibrate_cell(
        handle,
        replace(pot, pot_id="qualification-pot-2"),
        temperature_K=1703.0,
        po2=Po2Request("engine_default", None),
        qualification=True,
        isolated=False,
        engine_cache=cache,
    )

    assert backend.calls == 1
    assert assessment_calls == 2
    assert first.pot_id != second.pot_id
    assert first.notices == second.notices
    assert cache.counters["alphamelts"]["hits"] == 1
