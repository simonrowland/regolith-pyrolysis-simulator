"""Round-3 regressions for the controlled-O2 flow-boundary seam."""

from __future__ import annotations

import math
from dataclasses import replace
from types import MethodType, SimpleNamespace

import pytest

from engines.builtin.overhead_bleed import (
    BuiltinOverheadBleedProvider,
    controlled_flow_capacity,
)
from simulator.core import PyrolysisSimulator
from simulator.overhead import OverheadGasModel
from simulator.state import Atmosphere, EvaporationFlux
from simulator.thermal_train import FiniteCapacity, NoColdTrain


def _flux(species_kg_hr: dict[str, float]) -> EvaporationFlux:
    flux = EvaporationFlux(species_kg_hr=species_kg_hr)
    flux.update_totals()
    return flux


def _bled_kg(flow, *, holdup_kg: float) -> float:
    molar_mass_kg_mol = 0.032
    holdup_mol = holdup_kg / molar_mass_kg_mol
    bled = BuiltinOverheadBleedProvider._bled_species_mol(
        {"O2": holdup_mol},
        total_mol=holdup_mol,
        total_kg=holdup_kg,
        controls={
            "bleed_conductance_kg_s": 999.0,
            "dt_hr": 1.0,
            "effective_transport_capacity": flow,
        },
    )
    return bled.get("O2", 0.0) * molar_mass_kg_mol


def test_committed_disposition_uses_shared_flow_not_diagnostic_pressure():
    flow = controlled_flow_capacity(
        pipe_capacity_kg_hr=1.0,
        equipment_capacity_kg_hr=0.5,
        evolved_flux_kg_hr=0.4,
        upstream_pressure_bar=0.001,
    )

    committed = [
        _bled_kg(replace(flow, downstream_pressure_bar=p_down), holdup_kg=1.0)
        for p_down in (0.0, flow.downstream_pressure_bar, 0.001)
    ]

    assert committed == pytest.approx([committed[0]] * len(committed))
    assert committed == pytest.approx([0.4, 0.4, 0.4])


def test_retained_holdup_drains_and_throttle_clears_only_after_evacuation():
    first_tick = controlled_flow_capacity(
        pipe_capacity_kg_hr=10.0,
        equipment_capacity_kg_hr=10.0,
        evolved_flux_kg_hr=0.0,
        retained_holdup_kg=1.0,
        dt_hr=1.0,
        upstream_pressure_bar=0.001,
    )

    assert first_tick.demand_flux_kg_hr == pytest.approx(1.0)
    assert first_tick.swallowed_flux_kg_hr == pytest.approx(1.0)
    assert first_tick.saturation == pytest.approx(0.1)
    assert _bled_kg(first_tick, holdup_kg=1.0) == pytest.approx(1.0)

    recovered = controlled_flow_capacity(
        pipe_capacity_kg_hr=10.0,
        equipment_capacity_kg_hr=10.0,
        evolved_flux_kg_hr=0.0,
        retained_holdup_kg=0.0,
        dt_hr=1.0,
        upstream_pressure_bar=0.001,
    )
    assert recovered.swallowed_flux_kg_hr == 0.0
    assert recovered.saturation == 0.0


def test_explicit_zero_conductance_closes_controlled_line():
    model = OverheadGasModel(headspace_config={"conductance_kg_s": 0.0})
    melt = SimpleNamespace(
        atmosphere=Atmosphere.CONTROLLED_O2,
        p_total_mbar=1.0,
        temperature_C=1500.0,
    )

    flow = model.controlled_o2_transport_capacity(
        _flux({"Na": 0.25}),
        melt,
        cold_train_capacity=FiniteCapacity(10.0),
    )

    assert flow is not None
    assert flow.pipe_capacity_kg_hr == 0.0
    assert flow.swallowed_flux_kg_hr == 0.0
    assert math.isinf(flow.saturation)
    assert flow.overload_notice["overload_ratio"] == "infinite"
    assert flow.binding_cause == "pipe"


def test_disabled_runtime_policy_stays_on_no_equipment_path():
    sim = SimpleNamespace(
        overhead_model=OverheadGasModel(),
        melt=SimpleNamespace(
            atmosphere=Atmosphere.CONTROLLED_O2,
            p_total_mbar=450.0,
            temperature_C=1500.0,
        ),
        _overhead_holdup_species_kg=lambda: {},
    )
    sim._cold_train_capacity_policy = MethodType(
        PyrolysisSimulator._cold_train_capacity_policy,
        sim,
    )
    capacity, cold_train = sim._cold_train_capacity_policy()
    assert isinstance(capacity, NoColdTrain)
    assert cold_train.runtime_enforcement is False

    flow = PyrolysisSimulator._controlled_o2_transport_capacity(
        sim,
        _flux({"Na": 100.0}),
    )

    assert flow is not None
    assert flow.equipment_capacity_kg_hr is None
    assert flow.effective_capacity_kg_hr == flow.pipe_capacity_kg_hr
    assert flow.swallowed_flux_kg_hr == pytest.approx(
        min(flow.demand_flux_kg_hr, flow.pipe_capacity_kg_hr)
    )
    assert math.isfinite(flow.saturation)
    assert flow.binding_cause == "controlled_o2_no_equipment"


def test_reductant_flash_carries_transport_domain_notice():
    """b-832: the 113 kg/h C3_NA flash exceeds the 3.9 kg/h pipe."""
    model = OverheadGasModel()
    melt = SimpleNamespace(
        atmosphere=Atmosphere.CONTROLLED_O2,
        p_total_mbar=0.2,
        temperature_C=1150.0,
    )
    flux = _flux({"Na": 113.0})
    flow = controlled_flow_capacity(
        pipe_capacity_kg_hr=3.9,
        equipment_capacity_kg_hr=None,
        equipment_capacity_required=False,
        evolved_flux_kg_hr=flux.total_kg_hr,
        upstream_pressure_bar=0.0002,
    )
    state = model.estimate_transport_state(
        flux, melt, effective_transport_capacity=flow,
    )

    assert flow.swallowed_flux_kg_hr == pytest.approx(3.9)
    assert state["pipe_capacity_used_pct"] > 2800.0
    notice = state.get("transport_overload_notice")
    assert notice is not None, "overloaded pipe silently retains the reductant flash"
    assert notice["overload_ratio"] == pytest.approx(flow.saturation)
    assert notice["authority_level"] == "extrapolated"
    assert notice["reason"]
    assert notice["regime"]
    assert notice["certified_band"]


@pytest.mark.parametrize("atmosphere", [Atmosphere.CONTROLLED_O2, Atmosphere.PN2_SWEEP])
def test_pressure_driven_flash_uses_one_pressure_for_pipe_and_condenser(atmosphere):
    model = OverheadGasModel(headspace_config={"enabled": False})
    melt = SimpleNamespace(
        atmosphere=atmosphere,
        p_total_mbar=0.2,
        pO2_mbar=0.0,
        temperature_C=1150.0,
        campaign=SimpleNamespace(name="C3_NA"),
        campaign_hour=0.0,
    )
    flux = _flux({"Na": 113.0})
    nominal_capacity = model._pipe_conductance(
        20.0, melt.temperature_C, species_kg_for_M_avg=flux.species_kg_hr,
    ) * 3600.0
    flow = model.controlled_o2_transport_capacity(
        flux, melt, cold_train_capacity=NoColdTrain("runtime_enforcement_disabled"),
    )
    assert flow.upstream_pressure_bar > 0.0002
    assert flow.swallowed_flux_kg_hr == pytest.approx(113.0)
    assert flow.saturation == 1.0
    assert flow.overload_notice["overload_ratio"] == pytest.approx(
        113.0 / nominal_capacity,
    )
    # The independent forward evaluation must carry the entire source at the
    # inverse's pressure; the test does not reproduce the pressure formula.
    assert model._pipe_conductance(
        flow.upstream_pressure_bar * 1e5,
        melt.temperature_C,
        p_downstream_Pa=flow.downstream_pressure_bar * 1e5,
        species_kg_for_M_avg=flux.species_kg_hr,
    ) * 3600.0 == pytest.approx(113.0)
    assert _bled_kg(flow, holdup_kg=113.0) == pytest.approx(113.0)

    configured = {}
    sim = SimpleNamespace(
        overhead_model=model,
        melt=melt,
        condensation_model=SimpleNamespace(
            pipe_segments=(),
            configure_operating_conditions=lambda **kw: configured.update(kw),
        ),
        _effective_transport_capacity_this_tick=flow,
        _resolve_condensation_carrier_gas=lambda: "O2",
    )
    PyrolysisSimulator._configure_condensation_operating_conditions(sim, flux)
    assert configured["overhead_pressure_mbar"] == pytest.approx(
        flow.upstream_pressure_bar * 1000.0,
    )
    assert configured["species_partial_pressures_mbar"] == pytest.approx(
        {"Na": flow.upstream_pressure_bar * 1000.0},
    )


def test_113_kg_hr_flash_solves_same_hour_pressure_to_pipe_flow():
    model = OverheadGasModel()
    melt = SimpleNamespace(p_total_mbar=0.2, temperature_C=1150.0)
    equilibrium_pressure_Pa = 1.0e7

    def provider_at_partials(partials_Pa):
        remaining_fraction = max(
            0.0,
            1.0 - partials_Pa["Na"] / equilibrium_pressure_Pa,
        )
        return _flux({"Na": 113.0 * remaining_fraction})

    flux, pressure_Pa = model._solve_overloaded_evaporation_flux(
        melt,
        equilibrium_partials_Pa={"Na": equilibrium_pressure_Pa},
        p_downstream_bar=0.0,
        flux_at_partials=provider_at_partials,
    )
    outflow_kg_hr = model._pipe_conductance(
        pressure_Pa,
        melt.temperature_C,
        p_downstream_Pa=0.0,
        species_kg_for_M_avg=flux.species_kg_hr,
    ) * 3600.0

    assert pressure_Pa > melt.p_total_mbar * 100.0
    assert 0.0 < flux.total_kg_hr < 113.0
    assert flux.total_kg_hr == pytest.approx(outflow_kg_hr, rel=1.0e-9)
