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
