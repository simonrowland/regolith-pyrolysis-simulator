"""Grounded tests for the physical headspace O₂ transport terms."""

from __future__ import annotations

import math
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from simulator.core import Atmosphere, PyrolysisSimulator
from simulator.chemistry.offgas_fo2 import (
    co2_dissociation_log10_K,
    load_buffer_polynomials,
)
from simulator.melt_backend.base import InternalAnalyticalBackend
from simulator.overhead import OverheadConfigurationError
from simulator.physical_constants import GAS_CONSTANT
from simulator.state import EvaporationFlux
from simulator.transport_constants import COLLISION_DIAMETERS_M
from simulator.transport_regime import (
    KnudsenRegime,
    solve_duct_throughput,
)


ROOT = Path(__file__).resolve().parents[1]


def _transport_sim() -> PyrolysisSimulator:
    def load(name: str):
        return yaml.safe_load((ROOT / 'data' / name).read_text())

    backend = InternalAnalyticalBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(
        backend,
        load('setpoints.yaml'),
        load('feedstocks.yaml'),
        load('vapor_pressures.yaml'),
    )
    sim.load_batch('lunar_mare_low_ti', mass_kg=1000.0)
    return sim


def _duct_result(total_mol_s: float, oxygen_mol_s: float):
    return solve_duct_throughput(
        total_molar_flow_mol_s=total_mol_s,
        oxygen_molar_flow_mol_s=oxygen_mol_s,
        downstream_pressure_bar=0.0,
        downstream_oxygen_pressure_bar=0.0,
        temperature_K=1773.15,
        diameter_m=0.12,
        length_m=1.0,
        molar_mass_kg_mol=0.031998,
        dynamic_viscosity_pa_s=6.243e-5,
        collision_diameter_m=COLLISION_DIAMETERS_M['O2'],
    )


def test_molecular_conductance_matches_independent_textbook_value():
    result = _duct_result(1.0e-7, 1.0e-7)
    mean_speed_m_s = math.sqrt(
        8.0 * GAS_CONSTANT * 1773.15 / (math.pi * 0.031998)
    )
    # Independent long-tube textbook asymptote: C=(pi/12)*vbar*d^3/L.
    expected_m3_s = math.pi / 12.0 * mean_speed_m_s * 0.12 ** 3 / 1.0
    assert result.regime is KnudsenRegime.FREE_MOLECULAR
    assert result.conductance_m3_s == pytest.approx(expected_m3_s, rel=1.0e-12)
    assert result.conductance_m3_s == pytest.approx(0.49, rel=0.03)


def test_golden_scale_o2_rate_produces_molecular_backpressure():
    result = _duct_result(1.0e-7, 1.0e-7)
    expected_bar = (
        1.0e-7 * GAS_CONSTANT * 1773.15
        / result.conductance_m3_s
        / 1.0e5
    )
    assert result.p_o2_bar == pytest.approx(expected_bar, rel=1.0e-12)
    assert result.p_o2_bar == pytest.approx(3.0e-8, rel=0.15)


def test_high_throughput_leaves_free_molecular_regime():
    result = _duct_result(3.0e-3, 3.0e-3)
    assert result.p_o2_bar == pytest.approx(result.p_headspace_bar)
    assert result.regime is not KnudsenRegime.FREE_MOLECULAR
    assert result.knudsen_number < 10.0


def test_commanded_hold_is_the_lower_bound_on_source_transport():
    sim = _transport_sim()
    sim.melt.atmosphere = Atmosphere.CONTROLLED_O2
    sim.melt.p_total_mbar = 5.0
    sim.melt.pO2_mbar = 2.0
    reservoir = sim._refresh_oxygen_reservoir_transport_pO2_for_vapor()
    assert reservoir.headspace_transport_pO2_bar == pytest.approx(0.002)
    assert reservoir.headspace_pO2_basis == 'commanded_hold'
    assert reservoir.headspace_transport_conductance_m3_s > 0.0


def test_sweep_without_explicit_carrier_flow_is_visible():
    sim = _transport_sim()
    sim.melt.atmosphere = Atmosphere.PN2_SWEEP
    sim.melt.p_total_mbar = 10.0
    sim.melt.pO2_mbar = 0.0
    reservoir = sim._refresh_oxygen_reservoir_transport_pO2_for_vapor()
    assert reservoir.headspace_transport_pO2_bar == pytest.approx(1.0e-9)
    assert reservoir.headspace_pO2_basis == 'carrier_flow_undeclared'


def test_undeclared_sweep_keeps_legacy_value_without_duct_geometry():
    sim = _transport_sim()
    sim.melt.atmosphere = Atmosphere.PN2_SWEEP
    sim.melt.p_total_mbar = 10.0
    sim.melt.pO2_mbar = 0.0
    sim.overhead_model.pipe_length_m = None

    reservoir = sim._refresh_oxygen_reservoir_transport_pO2_for_vapor()

    assert reservoir.headspace_transport_pO2_bar == pytest.approx(1.0e-9)
    assert reservoir.headspace_pO2_basis == 'carrier_flow_undeclared'


def test_venting_refuses_missing_duct_geometry():
    sim = _transport_sim()
    sim.melt.atmosphere = Atmosphere.HARD_VACUUM
    sim._equipment = SimpleNamespace(
        pipe=SimpleNamespace(diameter_m=0.12, length_m=None),
    )

    with pytest.raises(OverheadConfigurationError):
        sim._refresh_oxygen_reservoir_transport_pO2_for_vapor()


def test_evaporation_buffer_source_reaches_transport_ledger():
    """The existing evaporation ledger source is flushed before transport."""

    sim = _transport_sim()
    sim.melt.temperature_C = 1500.0
    sim.melt.atmosphere = Atmosphere.HARD_VACUUM
    transition_start = len(sim.atom_ledger.transitions)
    species = 'K'
    sp_data = sim.vapor_pressures['metals'][species]
    stoich = sim._evaporation_stoich(species, sp_data)
    available_kg = sim.atom_ledger.kg_by_account(
        'process.cleaned_melt'
    ).get(stoich['parent_oxide'], 0.0)
    rate_kg_hr = min(
        1.0e-3,
        0.01 * available_kg / float(stoich['oxide_per_product_kg']),
    )
    assert rate_kg_hr > 0.0

    sim._credit_evaporation_transition(
        species,
        rate_kg_hr,
        rate_kg_hr,
        sp_data,
    )
    sim._get_turbine_spec()
    flux = EvaporationFlux(species_kg_hr={species: rate_kg_hr})
    flux.update_totals()
    sim._set_headspace_transport_source_rates(
        flux,
        transition_start_index=transition_start,
    )
    moved_o2_mol = sim._flush_evaporative_o2_buffer_to_headspace()
    reservoir = sim._refresh_oxygen_reservoir_transport_pO2_for_vapor()

    assert moved_o2_mol > 0.0
    assert sim.atom_ledger.mol_by_account('process.overhead_gas')['O2'] > 0.0
    assert reservoir.headspace_transport_pO2_bar > 1.0e-9
    assert reservoir.headspace_pO2_basis == 'venting_throughput'


def test_co2_buffer_does_not_promote_uncommitted_o2_to_inventory():
    """A buffer parcel not exchanged into the ledger cannot raise pO2."""

    sim = _transport_sim()
    sim.melt.atmosphere = Atmosphere.CO2_BACKPRESSURE
    sim.melt.p_total_mbar = 5.0
    sim.melt.temperature_C = 1500.0 - 273.15
    sim._headspace_transport_source_o2_buffer_mol_this_hr = 0.5
    sim._headspace_transport_source_o2_committed_mol_this_hr = 0.0

    result = sim._headspace_co2_buffer_equilibrium(0.0)
    assert result is not None
    p_co2_bar = 0.96 * 5.0e-3
    log10_K = co2_dissociation_log10_K(
        load_buffer_polynomials(),
        1500.0,
    )
    # Pure-CO2 extent gives pCO2=P-2q, pCO=2q, pO2=q. Solve the exact
    # independent equation (2q)²q/(P-2q)²=K; the small-q approximation is
    # q=(K*P²/4)^(1/3). The uncommitted O2 field must not enter p=nRT/V.
    equilibrium_K = 10.0 ** log10_K
    lo, hi = 0.0, p_co2_bar * (1.0 - 1.0e-12) / 2.0
    for _ in range(120):
        q = 0.5 * (lo + hi)
        lhs = (2.0 * q) ** 2 * q / (p_co2_bar - 2.0 * q) ** 2
        if lhs < equilibrium_K:
            lo = q
        else:
            hi = q
    expected_bar = 0.5 * (lo + hi)
    assert result.p_o2_bar == pytest.approx(expected_bar, rel=1.0e-10)
