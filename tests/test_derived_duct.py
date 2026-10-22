"""Duct design and finite sweep inventory regressions for d-231."""
import math

import pytest

from simulator.config import load_config_bundle
from simulator.equipment import EquipmentDesigner
from simulator.overhead import continuous_sweep_balance
from simulator.state import GAS_CONSTANT
from simulator.transport_constants import CARRIER_GAS_PROPERTIES
from simulator.transport_regime import (
    gas_dynamic_viscosity_Pa_s, pipe_mass_flow_kg_s, pipe_pressure_pa_for_mass_flow,
)


def test_automatic_duct_is_smallest_catalog_size_for_margined_load():
    designer = EquipmentDesigner(load_config_bundle().setpoints)
    flow, temperature, pressure = 2.930553684, 2473.15, 1000.0
    mass = CARRIER_GAS_PROPERTIES['Ar'].molar_mass_kg_mol
    pipe = designer.size_collection_pipe(
        0.01, sweep_mol_per_s=flow, temperature_K=temperature,
        carrier_molar_mass_kg_mol=mass,
    )
    # Existing design peak is 10 g/s per tonne, represented by 40 g/mol gas.
    demand = designer.DUCT_DESIGN_MARGIN * (0.01 + flow * mass)
    mixture_mass = (0.01 + flow * mass) / (0.25 + flow)
    eta = gas_dynamic_viscosity_Pa_s(temperature)
    assert pipe.diameter_m in designer.STANDARD_DUCT_DIAMETERS_M
    assert pipe.conductance_kg_s >= demand
    for smaller in designer.STANDARD_DUCT_DIAMETERS_M:
        if smaller >= pipe.diameter_m:
            break
        assert pipe_mass_flow_kg_s(smaller, 1.0, mixture_mass, eta, temperature, pressure, 0.0) < demand


@pytest.mark.parametrize('peak_kg_s', [0.01, 1.0])
def test_declared_throat_is_honored_without_batch_scaling(peak_kg_s):
    designer = EquipmentDesigner({'condenser_geometry': {'initial_throat_area_m2': 0.02}})
    pipe = designer.size_collection_pipe(peak_kg_s, sweep_mol_per_s=100.0)
    assert pipe.initial_throat_area_m2 == 0.02
    assert pipe.diameter_m == pytest.approx(math.sqrt(4 * 0.02 / math.pi))


def _balance(initial, generated=None, *, capacity=None, flow=1.0, duration=1.0, **kwargs):
    # Ideal gas at PV/RT=1 mol; capacities are mol/s, times are seconds.
    return continuous_sweep_balance(
        initial, generated or {}, carrier='Ar', sweep_mol_per_s=flow, dt_s=duration,
        pressure_bar=GAS_CONSTANT * 300.0 / 1e5, volume_m3=1.0, temperature_K=300.0,
        outlet_capacity_mol_per_s=capacity, **kwargs,
    )


def test_capacity_shortfall_integrates_accumulation_and_source_separated_oxygen():
    result = _balance({'O2': 1.0}, {'O2': 1.0}, capacity=lambda n, p: 0.5,
                      initial_external_o2_mol=1.0)
    # Independent constant-outlet solution: N(t)=1+(2-0.5)t.
    # Integral B/N dt=(1/3)ln(2.5), so old external O2 survives as 2.5^-1/3.
    retained_external = 2.5 ** (-1.0 / 3.0)
    assert sum(result['final_species_mol'].values()) == pytest.approx(2.5, rel=1e-8)
    assert result['external_o2_outflow_mol'] == pytest.approx(1.0 - retained_external, rel=1e-8)
    assert sum(result['outflow_species_mol'].values()) == pytest.approx(0.5, rel=1e-8)
    assert result['inlet_species_mol']['Ar'] == 1.0
    assert result['capacity_shortfall']['reason'] == 'duct_capacity_shortfall'
    assert result['pressure_bar'] == pytest.approx(2.5 * GAS_CONSTANT * 300 / 1e5, rel=1e-8)


def test_zero_capacity_retains_carry_in_and_the_whole_command():
    result = _balance({'Ar': 2.0}, capacity=lambda n, p: 0.0)
    assert result['final_species_mol']['Ar'] == pytest.approx(3.0)
    assert result['outflow_species_mol']['Ar'] == pytest.approx(0.0, abs=1e-12)
    assert result['inlet_species_mol']['Ar'] == 1.0


def test_feasible_finite_capacity_preserves_the_analytic_cstr():
    plain = _balance({'O2': 1.0}, {'O2': 1.0})
    finite = _balance({'O2': 1.0}, {'O2': 1.0}, capacity=lambda n, p: 1000.0)
    assert finite == plain


def test_pressure_dependent_duct_reaches_its_inverse_instead_of_frozen_hour_pressure():
    temperature, volume, flow = 1980.65, 0.0853838076, 2.930553684
    mass = CARRIER_GAS_PROPERTIES['Ar'].molar_mass_kg_mol
    eta = gas_dynamic_viscosity_Pa_s(temperature)
    def capacity(inventory, pressure_bar):
        return pipe_mass_flow_kg_s(0.12, 1.0, mass, eta, temperature, pressure_bar * 1e5, 0.0) / mass
    result = continuous_sweep_balance(
        {}, {}, carrier='Ar', sweep_mol_per_s=flow, dt_s=3600.0,
        pressure_bar=0.01, volume_m3=volume, temperature_K=temperature,
        outlet_capacity_mol_per_s=capacity,
    )
    required_pressure = pipe_pressure_pa_for_mass_flow(0.12, 1.0, mass, eta, temperature, flow * mass, 0.0)
    assert 1000 < required_pressure < 1200  # independent documented Mars carrier-only operating bracket
    assert result['pressure_bar'] * 1e5 == pytest.approx(required_pressure, rel=1e-8)
    assert result['inlet_species_mol']['Ar'] >= flow * 3600.0
    assert result['capacity_shortfall'] is not None


def test_runtime_declared_undersized_duct_continues_with_eos_pressure():
    from tests.test_mars_process_pressure import mars_sim
    sim = mars_sim(pressure_mbar=10.0, sweep=2.930553684)
    area = math.pi * 0.12**2 / 4.0
    sim.campaign_mgr.overrides['C2A'] = {'condenser_geometry': {'initial_throat_area_m2': area}}
    sim.melt.temperature_C = 1700.0
    sim.step()
    assert sim.melt.hour == 1
    assert sim.overhead_model.pipe_diameter_m == pytest.approx(0.12)
    assert sim.melt.sweep_mol_per_s == pytest.approx(2.930553684)
    assert sim._process_pressure_diagnostic['status'] == 'ok'
    assert sim._process_pressure_diagnostic['capacity_shortfall']['reason'] == 'duct_capacity_shortfall'
    assert sim.overhead.pressure_mbar > sim.melt.p_total_mbar
    from simulator.overhead import OverheadGasModel
    pressure = sum(OverheadGasModel._compute_partial_pressures(
        sim._overhead_holdup_mol(), sim._headspace_volume_m3(), sim._headspace_temperature_K()).values())
    assert sim.overhead.pressure_mbar == pytest.approx(pressure * 1000.0)
    sim.atom_ledger.assert_balanced()
    from simulator.pumping_cost import pumping_context_from_sim
    pumping = pumping_context_from_sim(sim, sim.record.snapshots)
    assert pumping['status'] == 'ok'
    assert pumping['rows'] == ()


def test_initial_mixture_shortfall_is_not_hidden_by_feasible_final_mixture():
    result = _balance({'Kr': 1.0}, flow=1.5,
        capacity=lambda inventory, pressure: 2.0 - inventory.get('Kr', 0.0) / sum(inventory.values()))
    assert result['capacity_shortfall'] is not None
    assert result['capacity_shortfall']['capacity_at_request_mol_per_s'] == 1.0


def test_depressurizing_plan_retains_its_integrated_outlet():
    result = _balance({'Ar': 100.0}, capacity=lambda inventory, pressure: sum(inventory.values()))
    # dN/dt=1-N => N=1+99 exp(-t); integrated outlet is N0+Ft-N.
    retained = 1.0 + 99.0 * math.exp(-1.0)
    assert result['final_species_mol']['Ar'] == pytest.approx(retained, rel=1e-8)
    assert result['outflow_species_mol']['Ar'] == pytest.approx(101.0 - retained, rel=1e-8)
    assert result['outflow_species_mol']['Ar'] > retained


def test_sizing_carries_the_resolved_command_for_actual_nondefault_melt_area():
    from tests.test_mars_process_pressure import mars_sim
    sim = mars_sim(pressure_mbar=10.0, sweep=2.930553684)
    sim.melt.melt_surface_area_m2 = 1.0
    sim._apply_sweep_flow_control()
    expected = EquipmentDesigner().size_collection_pipe(
        0.01, pressure_mbar=sim.melt.p_total_mbar,
        sweep_mol_per_s=sim.melt.sweep_mol_per_s,
        temperature_K=sim.campaign_mgr.furnace_max_T_C + 273.15,
        carrier_molar_mass_kg_mol=CARRIER_GAS_PROPERTIES['Ar'].molar_mass_kg_mol,
    )
    assert sim.overhead_model.pipe_diameter_m == expected.diameter_m


@pytest.mark.parametrize('chamber_flow,pump_fails', [(10.0, True), (0.01, False)])
def test_duct_flag_does_not_clear_a_simultaneous_pump_speed_failure(chamber_flow, pump_fails):
    from tests.test_mars_process_pressure import mars_sim, HARDWARE
    from simulator.campaigns import CampaignPressureSetpointRefusal
    sim = mars_sim(pressure_mbar=1.0, pumping=HARDWARE)
    sim._process_pressure_diagnostic['chamber_mol_per_s'] = chamber_flow
    kwargs = {'actual_pressure_pa': 800.0,
              'duct_capacity_shortfall': {'reason': 'duct_capacity_shortfall'}}
    if pump_fails:
        with pytest.raises(CampaignPressureSetpointRefusal):
            sim._admit_process_pressure(**kwargs)
        assert sim._process_pressure_diagnostic['pump_feasible'] is False
    else:
        sim._admit_process_pressure(**kwargs)
        assert sim._process_pressure_diagnostic['status'] == 'ok'
        assert sim._process_pressure_diagnostic['capacity_shortfall']['reason'] == 'duct_capacity_shortfall'


@pytest.mark.parametrize('downstream', [1000.0, 2000.0])
def test_auto_sizing_refuses_impossible_gradient_but_honors_declared_duct(downstream):
    with pytest.raises(ValueError, match='no finite duct'):
        EquipmentDesigner().size_collection_pipe(0.01, downstream_pressure_Pa=downstream)
    declared = EquipmentDesigner({'condenser_geometry': {'initial_throat_area_m2': 0.02}})
    pipe = declared.size_collection_pipe(0.01, downstream_pressure_Pa=downstream)
    assert pipe.initial_throat_area_m2 == 0.02
    assert pipe.conductance_kg_s == 0.0


def test_finite_inventory_excess_uses_the_pressure_admission_roundoff_owner():
    result = _balance({'Ar': 1.0 + 1e-10}, capacity=lambda inventory, pressure: 1.0)
    assert result['capacity_shortfall'] is not None
    assert result['final_species_mol']['Ar'] > 1.0


def test_campaign_pressure_is_configured_before_pressure_specific_duct_sizing():
    from simulator.runner import PyrolysisRun
    run = PyrolysisRun(feedstock_id='mars_basalt', campaign='C2A', hours=0,
        additives_kg={'C': 30.0},
        setpoints_patch={'overhead_headspace': {'downstream_pressure_bar': 0.008}})
    sim = run._start_session().simulator
    assert sim.melt.p_total_mbar == 10.0
    assert sim._headspace_downstream_pressure_bar() == 0.008
    assert sim._equipment.pipe.conductance_kg_s > 0.0


def test_excess_inventory_drains_to_pressure_hold_without_valve_chatter():
    calls = 0
    def capacity(inventory, pressure):
        nonlocal calls
        calls += 1
        assert calls < 3000, "constant outlet drain must stop at the pressure-hold boundary"
        return 100.0
    result = _balance({'Ar': 2.0}, duration=3600.0, capacity=capacity)
    # Before reaching 1 mol, N=2+(1-100)t; reaching it takes 1/99 s.
    # Thereafter the balanced valve retains N=1 mol for the remaining hour.
    assert result['final_species_mol']['Ar'] == pytest.approx(1.0, rel=1e-8)
    assert result['outflow_species_mol']['Ar'] == pytest.approx(3601.0, rel=1e-8)
    assert result['capacity_shortfall'] is None


def test_pressure_hold_crossing_preserves_external_oxygen_residence():
    duration = 0.02
    result = _balance({'Ar': 1.0, 'O2': 1.0}, {'O2': duration}, duration=duration,
                      capacity=lambda n, p: 100.0, initial_external_o2_mol=1.0)
    # Total source=2 mol/s: drain N=2-98t reaches 1 at t=1/98 s.
    # Integral B/N dt=(100/98)ln2 during drainage, then 2*(dt-1/98)
    # at constant N=1. Only the carry-in O2 is external.
    surviving_external = 0.5 ** (100.0 / 98.0) * math.exp(-2.0 * (duration - 1.0 / 98.0))
    assert result['external_o2_outflow_mol'] == pytest.approx(1.0 - surviving_external, rel=1e-8)
    assert sum(result['final_species_mol'].values()) == pytest.approx(1.0, rel=1e-8)


def test_mixture_shortfall_can_accumulate_then_return_to_pressure_hold():
    calls = 0
    def capacity(inventory, pressure):
        nonlocal calls
        calls += 1
        assert calls < 3000, "mixture-driven return must hand off at the pressure-hold boundary"
        return 2.0 - inventory.get('Kr', 0.0) / sum(inventory.values())
    result = _balance({'Kr': 1.0}, flow=1.5, duration=3600.0, capacity=capacity)
    assert sum(result['final_species_mol'].values()) == pytest.approx(1.0, rel=1e-8)
    assert result['final_species_mol']['Ar'] == pytest.approx(1.0, rel=1e-8)
    assert result['capacity_shortfall']['capacity_at_request_mol_per_s'] == 1.0
