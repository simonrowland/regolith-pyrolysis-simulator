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
from simulator.overhead import (
    OverheadGasModel,
    required_slider_vane_open_fraction_for_flow,
    required_throat_diameter_for_flow,
)
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
    # Tests-only pin before d-452 adds run diagnostics and changes the
    # configured throat. Keep the existing one-tick numerical boundary exact.
    assert first_tick.pipe_capacity_kg_hr.hex() == "0x1.4000000000000p+3"
    assert first_tick.demand_flux_kg_hr.hex() == "0x1.0000000000000p+0"
    assert first_tick.swallowed_flux_kg_hr.hex() == "0x1.0000000000000p+0"
    assert first_tick.saturation.hex() == "0x1.999999999999ap-4"
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


def test_required_throat_diameter_inverts_poiseuille_fourth_power_scaling():
    same_load, same_status = required_throat_diameter_for_flow(
        0.12, 10.0, 10.0
    )
    sixteenfold_load, scaled_status = required_throat_diameter_for_flow(
        0.12, 160.0, 10.0
    )

    assert same_status == "ok"
    assert same_load == pytest.approx(0.12)
    assert scaled_status == "ok"
    assert sixteenfold_load == pytest.approx(0.24)


def test_required_throat_diameter_reports_zero_capacity_as_unavailable():
    diameter, status = required_throat_diameter_for_flow(0.12, 1.0, 0.0)

    assert diameter is None
    assert status == "unavailable_zero_pipe_capacity"


def test_required_slider_vane_fraction_inverts_area_fraction_capacity():
    same_load, same_status = required_slider_vane_open_fraction_for_flow(
        0.25, 1.0, 1.0
    )
    fourfold_load, scaled_status = required_slider_vane_open_fraction_for_flow(
        0.25, 4.0, 1.0
    )
    exceeds_open, exceeds_status = required_slider_vane_open_fraction_for_flow(
        1.0, 4.0, 1.0
    )

    assert same_load == pytest.approx(0.25)
    assert same_status == "ok"
    assert fourfold_load == pytest.approx(0.5)
    assert scaled_status == "ok"
    assert exceeds_open == pytest.approx(2.0)
    assert exceeds_status == "exceeds_fully_open_capacity"


def test_slider_vane_reduces_pipe_capacity_by_open_fraction_squared():
    model = OverheadGasModel()
    flux = _flux({"O2": 0.01})
    full_open = SimpleNamespace(
        atmosphere=Atmosphere.CONTROLLED_O2,
        p_total_mbar=1.0,
        temperature_C=1500.0,
        slider_vane_open_fraction=1.0,
    )
    quarter_open = SimpleNamespace(
        atmosphere=Atmosphere.CONTROLLED_O2,
        p_total_mbar=1.0,
        temperature_C=1500.0,
        slider_vane_open_fraction=0.25,
    )

    full_capacity = model.controlled_o2_transport_capacity(
        flux,
        full_open,
        cold_train_capacity=FiniteCapacity(100.0),
    )
    quarter_capacity = model.controlled_o2_transport_capacity(
        flux,
        quarter_open,
        cold_train_capacity=FiniteCapacity(100.0),
    )

    assert full_capacity is not None
    assert quarter_capacity is not None
    assert model.effective_throat_diameter_m(quarter_open) == pytest.approx(
        model.pipe_diameter_m * 0.5
    )
    assert quarter_capacity.pipe_capacity_kg_hr == pytest.approx(
        full_capacity.pipe_capacity_kg_hr / 16.0
    )
    quarter_diameter = model.effective_throat_diameter_m(quarter_open)
    at_capacity = model.controlled_o2_transport_capacity(
        _flux({"O2": quarter_capacity.pipe_capacity_kg_hr}),
        quarter_open,
        cold_train_capacity=FiniteCapacity(1e9),
    )
    assert at_capacity is not None
    assert at_capacity.required_throat_diameter_m == pytest.approx(
        quarter_diameter
    )


def test_controlled_flow_reports_holdup_remaining_after_tick():
    flow = controlled_flow_capacity(
        pipe_capacity_kg_hr=0.25,
        equipment_capacity_kg_hr=0.25,
        evolved_flux_kg_hr=1.0,
        retained_holdup_kg=0.0,
        dt_hr=1.0,
        upstream_pressure_bar=0.001,
    )

    assert flow.retained_holdup_after_tick_kg == pytest.approx(0.75)


def test_d452_default_fixed_throat_matches_owner_hardware_aperture():
    model = OverheadGasModel()

    assert model.initial_throat_area_m2 == 0.19634954084936207
    assert model.pipe_diameter_m == pytest.approx(0.5)


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
