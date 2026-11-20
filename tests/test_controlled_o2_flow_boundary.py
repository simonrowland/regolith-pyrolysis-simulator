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
    assert flow.binding_cause == "pipe"


def test_disabled_runtime_policy_stays_on_no_equipment_path():
    """No equipment stays the binding cause. The pipe uses the inventory.

    1 mol of O2 in 0.1 m^3 at the 1500 C melt temperature. The upstream
    pressure is n R T / V with the CODATA gas constant. 450 mbar is the
    command and is not that pressure.
    """
    from simulator.overhead import pipe_species_outflow_mol_s
    from simulator.physical_constants import GAS_CONSTANT, PA_PER_BAR

    holdup_mol = {"O2": 1.0}
    volume_m3 = 0.1
    temperature_K = 1500.0 + 273.15
    # P = n R T / V. Unit check: mol * J/(mol·K) * K / m^3 = Pa.
    # Limit: the 450 mbar command is 45000 Pa and is not this pressure.
    inventory_pa = holdup_mol["O2"] * GAS_CONSTANT * temperature_K / volume_m3
    o2_molar_mass = 0.031998

    def _holdup_kg(holdup_mol_arg=None):
        basis = holdup_mol if holdup_mol_arg is None else holdup_mol_arg
        return {
            species: float(moles) * o2_molar_mass
            for species, moles in basis.items()
            if float(moles) > 0.0
        }

    sim = SimpleNamespace(
        overhead_model=OverheadGasModel(),
        melt=SimpleNamespace(
            atmosphere=Atmosphere.CONTROLLED_O2,
            p_total_mbar=450.0,
            temperature_C=1500.0,
        ),
        _overhead_headspace_config={
            "enabled": True,
            "volume_m3": volume_m3,
            "downstream_pressure_bar": 0.0,
            "temperature_model": "melt",
        },
        _overhead_holdup_mol=lambda: dict(holdup_mol),
        _overhead_holdup_species_kg=_holdup_kg,
    )
    for name in (
        "_cold_train_capacity_policy",
        "_headspace_inventory_pressure_Pa",
        "_headspace_volume_m3",
        "_headspace_temperature_K",
        "_headspace_downstream_pressure_bar",
        "_overhead_headspace_enabled",
    ):
        setattr(sim, name, MethodType(getattr(PyrolysisSimulator, name), sim))
    capacity, cold_train = sim._cold_train_capacity_policy()
    assert isinstance(capacity, NoColdTrain)
    assert cold_train.runtime_enforcement is False

    flow = PyrolysisSimulator._controlled_o2_transport_capacity(
        sim,
        _flux({"Na": 100.0}),
    )
    at_inventory = pipe_species_outflow_mol_s(
        inventory_pa,
        0.0,
        temperature_K,
        holdup_mol,
        sim.overhead_model.pipe_diameter_m,
        sim.overhead_model.pipe_length_m,
    )
    at_command = pipe_species_outflow_mol_s(
        450.0 * 100.0,
        0.0,
        temperature_K,
        holdup_mol,
        sim.overhead_model.pipe_diameter_m,
        sim.overhead_model.pipe_length_m,
    )

    assert flow is not None
    assert flow.equipment_capacity_kg_hr is None
    assert flow.pipe_capacity_kg_hr == pytest.approx(
        at_inventory.mass_flow_kg_s * 3600.0
    )
    assert flow.pipe_capacity_kg_hr != pytest.approx(
        at_command.mass_flow_kg_s * 3600.0
    )
    assert flow.upstream_pressure_bar == pytest.approx(inventory_pa / PA_PER_BAR)
    assert flow.upstream_pressure_bar != pytest.approx(0.45)
    assert flow.effective_capacity_kg_hr == flow.pipe_capacity_kg_hr
    assert flow.swallowed_flux_kg_hr == pytest.approx(
        min(flow.demand_flux_kg_hr, flow.pipe_capacity_kg_hr)
    )
    assert math.isfinite(flow.saturation)
    assert flow.binding_cause == "controlled_o2_no_equipment"
