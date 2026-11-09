from __future__ import annotations

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
) -> dict[str, Any]:
    value = _engine_cache_request_vector(
        handle,
        _pot(),
        temperature_K=1703.0,
        po2=po2 or Po2Request("engine_default", None),
        physical_pressure_bar=pressure_bar,
        native_activity_formula=None,
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


@pytest.mark.parametrize(
    ("engine", "setting", "value"),
    [
        ("alphamelts", "_model", "model-b"),
        ("alphamelts", "_mode", "subprocess"),
        ("alphamelts", "_redox_buffer", "NNO"),
        ("alphamelts", "_fo2_offset", 0.25),
        ("alphamelts", "_fe3fet_ratio", 0.2),
        ("alphamelts", "_python_api_model", ("model-b", 2)),
        ("thermoengine", "_model", "model-b"),
        ("thermoengine", "_mode", "alternate-transport"),
        ("magemin", "_bridge", "julia"),
        ("magemin", "_database", "mp"),
        ("magemin", "_binary_path", Path("/selected/magemin")),
        ("openimcc", "_pack_name", "published-core"),
        ("vaporock", "_temperature_units", "K"),
        ("vaporock", "_pressure_units", "Pa"),
        ("vaporock", "_vapor_pressure_units", "Pa"),
        ("alphamelts", "_vapor_transport_floor_bar", 1.0e-10),
        ("thermoengine", "_vapor_transport_floor_bar", 1.0e-10),
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
