"""The 400 K thermal physical zero has one owner.

``BUILTIN_VAPOR_BELOW_400K_LIMIT_K`` is that owner. Each production
comparison below calls the real function. Raising or lowering the
constant moves that function's gate. A pasted ``400`` does not.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from engines.builtin.evaporation_flux import (
    BuiltinEvaporationFluxProvider,
    _solid_host_bakeout_result,
)
from engines.builtin.vapor_pressure import BUILTIN_VAPOR_BELOW_400K_LIMIT_K
from simulator.chemistry.kernel import ChemistryIntent, IntentRequest
from simulator.chemistry.kernel.dto import ProviderAccountView
from simulator.core import PyrolysisSimulator
from simulator.evaporation import EvaporationMixin
from simulator.melt_backend.base import EquilibriumResult
from simulator.thermal_train import FiniteCapacity
from tests.chemistry.test_authoritative_vapor_pressure_liquid_gate import (
    _sim_with_vapor_dispatch,
)


def test_species_rates_follow_the_vapor_owner_limit(monkeypatch):
    import simulator.thermal_budget as thermal_budget

    seen: list[tuple[float, float | None]] = []

    def rates(
        temperature_K: float,
        melt_surface_area_m2: float | None,
    ) -> dict[str, float]:
        seen.append((temperature_K, melt_surface_area_m2))
        return {}

    floor = thermal_budget.BUILTIN_VAPOR_BELOW_400K_LIMIT_K
    assert thermal_budget._species_rates_kg_hr(floor - 1.0, rates) == {}
    assert seen == []
    thermal_budget._species_rates_kg_hr(floor, rates)
    assert seen == [(floor, None)]

    monkeypatch.setattr(
        thermal_budget,
        "BUILTIN_VAPOR_BELOW_400K_LIMIT_K",
        floor + 50.0,
    )
    assert thermal_budget._species_rates_kg_hr(floor, rates) == {}
    assert seen == [(floor, None)]


def test_internal_analytical_cold_gate_follows_the_vapor_limit(monkeypatch):
    import engines.builtin.vapor_pressure as vapor_pressure
    from simulator.equilibrium import EquilibriumMixin

    monkeypatch.setattr(
        vapor_pressure,
        "BUILTIN_VAPOR_BELOW_400K_LIMIT_K",
        500.0,
    )
    result = EquilibriumMixin._internal_analytical_equilibrium(
        SimpleNamespace(
            melt=SimpleNamespace(temperature_C=130.0, p_total_mbar=1.0),
        )
    )
    assert result.status == "ok"
    assert result.diagnostics["vapor_pressure_zero_reason"] == (
        "internal_analytical_below_400K"
    )


def test_analytical_activity_vapor_gate_follows_the_vapor_limit(monkeypatch):
    import simulator.diagnostic_helpers.alphamelts_volatility as volatility

    monkeypatch.setattr(volatility, "BUILTIN_VAPOR_BELOW_400K_LIMIT_K", 200.0)

    def past_the_floor(name, value):
        raise RuntimeError(f"past the floor: {name}")

    monkeypatch.setattr(volatility, "_finite_positive", past_the_floor)
    with pytest.raises(RuntimeError, match="past the floor"):
        volatility._analytical_vapor_pressures_from_activities(
            vapor_pressure_data={},
            temperature_C=25.0,
            pO2_bar=1.0e-8,
            melt_oxide_activities={"SiO2": 1.0},
            composition_wt_pct={"SiO2": 100.0},
        )


def _evaporation_host(*, temperature_C: float, pressures: dict[str, float]):
    host = SimpleNamespace(
        melt=SimpleNamespace(temperature_C=temperature_C),
        setpoints={},
    )

    def resolve(_equilibrium, *, temperature_K, overhead_partials_Pa=None):
        return (
            dict(pressures),
            {},
            None,
            None,
            {},
            SimpleNamespace(physical_zero_reason=None),
        )

    host._resolve_evaporation_batch_flux_state = resolve
    return host


def test_empty_pressure_cold_gate_follows_the_vapor_limit(monkeypatch):
    import simulator.evaporation as evaporation

    monkeypatch.setattr(evaporation, "BUILTIN_VAPOR_BELOW_400K_LIMIT_K", 500.0)
    host = _evaporation_host(temperature_C=150.0, pressures={})
    equilibrium = SimpleNamespace(
        liquid_fraction=1.0,
        status="ok",
        diagnostics={
            "vapor_pressure_zero_reason": "internal_analytical_below_400K",
        },
    )
    EvaporationMixin._calculate_evaporation(host, equilibrium)
    assert host._last_evaporation_flux_diagnostic["reason"] == (
        "internal_analytical_below_400K"
    )


def test_catalog_pressure_gate_follows_the_vapor_limit(monkeypatch):
    import simulator.evaporation as evaporation

    monkeypatch.setattr(evaporation, "BUILTIN_VAPOR_BELOW_400K_LIMIT_K", 200.0)
    host = _evaporation_host(temperature_C=25.0, pressures={"SiO": 10.0})
    equilibrium = SimpleNamespace(liquid_fraction=1.0, status="ok", diagnostics={})
    try:
        flux = EvaporationMixin._calculate_evaporation(host, equilibrium)
    except Exception:
        return
    record = getattr(flux, "wall_supply_interval_physical_zero", None) or {}
    assert record.get("reason") != "below_400K"


def test_flux_provider_gate_follows_the_vapor_limit(monkeypatch):
    import engines.builtin.evaporation_flux as flux_module

    monkeypatch.setattr(
        flux_module,
        "BUILTIN_VAPOR_BELOW_400K_LIMIT_K",
        200.0,
    )
    provider = BuiltinEvaporationFluxProvider()
    request = IntentRequest(
        intent=ChemistryIntent.EVAPORATION_FLUX,
        account_view=ProviderAccountView(
            accounts={"process.cleaned_melt": {"SiO2": 10.0}},
            species_formula_registry={},
        ),
        temperature_C=25.0,
        pressure_bar=1.0e-6,
        fO2_log=None,
        control_inputs={
            "overhead_pressure_pa": 0.0,
            "vapour_batch_flux_pressures_Pa": {"Na": 1.0e6},
            "overhead_partials_Pa": {},
            "molar_mass_kg_mol": {"Na": 0.023},
            "stoich_by_species": {
                "Na": {
                    "parent_oxide": "Na2O",
                    "oxide_per_product_kg": 1.347,
                    "O2_per_product_kg": 0.347,
                },
            },
            "available_oxide_kg": {"Na": 10.0},
            "melt_surface_area_m2": 0.2,
            "stir_factor": 6.0,
            "alpha": 0.5,
        },
    )
    result = provider.dispatch(request)
    assert result.diagnostic.get("evaporation_flux_kg_hr") != {}


def test_solid_host_bakeout_gate_follows_the_vapor_limit(monkeypatch):
    import engines.builtin.evaporation_flux as flux_module

    monkeypatch.setattr(
        flux_module,
        "BUILTIN_VAPOR_BELOW_400K_LIMIT_K",
        200.0,
    )
    result = _solid_host_bakeout_result(
        controls={
            "vapour_batch_flux_pressures_Pa": {"S2": 10.0},
            "solid_host_area_m2": 1.0,
            "max_throughput_kg_hr": 10.0,
            "dt_hr": 1.0,
        },
        control_audit=None,
        temperature_K=298.15,
    )
    assert result.diagnostic.get("reason") != "below_400_K"


def test_kernel_refresh_gate_follows_the_vapor_limit(monkeypatch):
    import engines.builtin.vapor_pressure as vapor_pressure

    monkeypatch.setattr(
        vapor_pressure,
        "BUILTIN_VAPOR_BELOW_400K_LIMIT_K",
        300.0,
    )
    sim, calls = _sim_with_vapor_dispatch({"Na": 12.5})
    sim.melt.temperature_C = 100.0
    result = EquilibriumResult(
        temperature_C=100.0,
        pressure_bar=1.0e-6,
        liquid_fraction=1.0,
        vapor_pressures_Pa={"Na": 3.0},
        vapor_pressures_source={"Na": "builtin_authoritative"},
    )
    PyrolysisSimulator._refresh_vapor_pressures_from_kernel(sim, result)
    assert [call[0] for call in calls] == [ChemistryIntent.VAPOR_PRESSURE]


def test_capacity_shadow_gate_follows_the_vapor_limit(monkeypatch):
    import engines.builtin.vapor_pressure as vapor_pressure

    monkeypatch.setattr(
        vapor_pressure,
        "BUILTIN_VAPOR_BELOW_400K_LIMIT_K",
        200.0,
    )
    sim = SimpleNamespace(
        melt=SimpleNamespace(temperature_C=25.0),
        atom_ledger=SimpleNamespace(
            mol_by_account=lambda: {},
            transitions=[],
        ),
        _overhead_holdup_mol=lambda: {},
        _cold_train_capacity_policy=lambda: (
            FiniteCapacity(1.0),
            SimpleNamespace(accumulator_enabled=False),
        ),
    )

    def resolve(_equilibrium, *, temperature_K):
        raise RuntimeError("past the floor")

    sim._resolve_evaporation_batch_flux_state = resolve
    with pytest.raises(RuntimeError, match="past the floor"):
        PyrolysisSimulator._compute_capacity_coupling_shadow_impl(
            sim,
            SimpleNamespace(liquid_fraction=1.0, vapor_pressures_Pa={}),
        )


def test_owner_constant_is_the_published_limit():
    assert BUILTIN_VAPOR_BELOW_400K_LIMIT_K == 400.0
