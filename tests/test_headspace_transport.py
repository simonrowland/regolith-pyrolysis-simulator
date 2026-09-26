"""Grounded tests for the physical headspace O₂ transport terms."""

from __future__ import annotations

import math
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from simulator.core import (
    Atmosphere,
    OxygenInterfaceConfigurationError,
    PyrolysisSimulator,
)
from simulator.chemistry.offgas_fo2 import (
    co2_dissociation_log10_K,
    load_buffer_polynomials,
)
from simulator.melt_backend.base import InternalAnalyticalBackend
from simulator.overhead import OverheadConfigurationError
from simulator.physical_constants import (
    CATALOG_PHYSICAL_PRESSURE_CEILING_PA,
    GAS_CONSTANT,
)
from simulator.state import EvaporationFlux
from simulator.transport_constants import COLLISION_DIAMETERS_M
from simulator.transport_regime import (
    FREE_MOLECULAR_KNUDSEN_MIN,
    KnudsenRegime,
    VISCOUS_KNUDSEN_MAX,
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


def test_transitional_conductance_matches_both_endpoint_limits():
    """The duct pressure curve has no jump at either declared Kn boundary."""

    from simulator.transport_regime import _duct_conductance_at_mean_pressure

    T_K = 1773.15
    diameter_m = 0.12
    length_m = 1.0
    molar_mass_kg_mol = 0.031998
    viscosity_pa_s = 6.243e-5
    collision_diameter_m = COLLISION_DIAMETERS_M['O2']
    pressure_factor = (
        1.380649e-23 * T_K
        / (math.sqrt(2.0) * math.pi * collision_diameter_m ** 2 * length_m)
    )

    def conductance_at_kn(knudsen):
        mean_pressure_pa = pressure_factor / knudsen
        return _duct_conductance_at_mean_pressure(
            diameter_m,
            length_m,
            T_K,
            mean_pressure_pa,
            molar_mass_kg_mol,
            viscosity_pa_s,
            collision_diameter_m,
        )[0]

    molecular_boundary = conductance_at_kn(FREE_MOLECULAR_KNUDSEN_MIN)
    viscous_boundary = conductance_at_kn(VISCOUS_KNUDSEN_MAX)
    for boundary, expected in (
        (FREE_MOLECULAR_KNUDSEN_MIN, molecular_boundary),
        (VISCOUS_KNUDSEN_MAX, viscous_boundary),
    ):
        below = conductance_at_kn(boundary * (1.0 - 1.0e-8))
        above = conductance_at_kn(boundary * (1.0 + 1.0e-8))
        assert below == pytest.approx(expected, rel=2.0e-7)
        assert above == pytest.approx(expected, rel=2.0e-7)

    flows = [
        1.0e-7,
        3.0e-7,
        1.0e-6,
        3.0e-6,
        1.0e-5,
        3.0e-5,
        1.0e-4,
        3.0e-4,
        1.0e-3,
    ]
    pressures = [_duct_result(flow, flow).p_headspace_bar for flow in flows]
    assert all(b > a for a, b in zip(pressures, pressures[1:]))


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


def test_co2_buffer_uses_committed_ledger_and_duct_o2_once():
    """The assertion-site O/C balance sees both positive O2 source parcels."""

    sim = _transport_sim()
    sim.melt.atmosphere = Atmosphere.CO2_BACKPRESSURE
    sim.melt.p_total_mbar = 5.0
    sim.melt.temperature_C = 1500.0 - 273.15
    ledger_pO2_bar = 1.0e-4
    duct_source_pO2_bar = 2.0e-4

    result = sim._headspace_co2_buffer_equilibrium(
        ledger_pO2_bar,
        duct_source_pO2_bar=duct_source_pO2_bar,
    )
    assert result is not None
    p_co2_bar = 0.96 * 5.0e-3
    p_o2_initial_bar = ledger_pO2_bar + duct_source_pO2_bar
    equilibrium_K = 10.0 ** co2_dissociation_log10_K(
        load_buffer_polynomials(),
        1500.0,
    )
    # Independent O/C extent: pCO2=P-2ξ, pCO=2ξ, pO2=pO2,0+ξ.
    # With no initial CO, the physical extent is non-negative: the carrier
    # decomposes until the O/C quotient reaches K.  Starting below zero would
    # evaluate an unphysical negative CO branch and can select its second
    # mathematical crossing.
    lo, hi = 0.0, p_co2_bar / 2.0
    for _ in range(120):
        extent = 0.5 * (lo + hi)
        lhs = (
            (2.0 * extent) ** 2
            * (p_o2_initial_bar + extent)
            / (p_co2_bar - 2.0 * extent) ** 2
        )
        if lhs < equilibrium_K:
            lo = extent
        else:
            hi = extent
    expected_bar = p_o2_initial_bar + 0.5 * (lo + hi)
    assert result.p_o2_bar == pytest.approx(expected_bar, rel=1.0e-10)
    assert result.p_o2_bar > duct_source_pO2_bar

    # Exercise the production caller as well as the helper: the duct source
    # must arrive at the CO2 balance exactly once, rather than being dropped
    # at the return site.
    sim._headspace_transport_source_total_mol_s = 1.0e-3
    sim._headspace_transport_source_o2_mol_s = 1.0e-3
    duct_result = sim._headspace_venting_throughput()
    expected_from_caller = sim._headspace_co2_buffer_equilibrium(
        ledger_pO2_bar,
        duct_source_pO2_bar=duct_result.p_o2_bar,
    )
    transport_pO2_bar = sim._headspace_transport_pO2_bar_from_ledger(
        ledger_pO2_bar,
    )
    assert expected_from_caller is not None
    assert transport_pO2_bar == pytest.approx(
        expected_from_caller.p_o2_bar,
        rel=1.0e-10,
    )
    assert transport_pO2_bar > sim._headspace_co2_buffer_equilibrium(
        ledger_pO2_bar,
        duct_source_pO2_bar=0.0,
    ).p_o2_bar


def test_co2_duct_outlet_respects_declared_ambient_without_pump():
    sim = _transport_sim()
    sim.melt.atmosphere = Atmosphere.CO2_BACKPRESSURE
    sim.melt.p_total_mbar = 5.0
    sim._headspace_transport_source_total_mol_s = 0.0
    sim._headspace_transport_source_o2_mol_s = 0.0
    sim._headspace_transport_source_molar_mass_kg_mol = 0.031998
    zero_source = sim._headspace_venting_throughput()
    assert zero_source.p_headspace_bar >= 5.0e-3

    sim._headspace_transport_source_total_mol_s = 1.0e-3
    sim._headspace_transport_source_o2_mol_s = 1.0e-3
    finite_source = sim._headspace_venting_throughput()
    assert finite_source.p_headspace_bar >= 5.0e-3
    assert finite_source.p_headspace_bar > zero_source.p_headspace_bar


def test_co2_buffer_assumption_is_published_in_headspace_state():
    sim = _transport_sim()
    sim.melt.atmosphere = Atmosphere.CO2_BACKPRESSURE
    sim.melt.p_total_mbar = 5.0
    sim.melt.temperature_C = 1500.0 - 273.15
    reservoir = sim._refresh_oxygen_reservoir_transport_pO2_for_vapor()
    assert reservoir.headspace_co2_buffer_assumption == (
        'equilibrium_upper_bound_homogeneous_kinetics_unverified'
    )
    assert sim._last_headspace_transport_diagnostic[
        'co2_buffer_assumption'
    ] == reservoir.headspace_co2_buffer_assumption
    assert sim._oxygen_reservoir_guard_context(
        context='test_diagnostic_projection'
    )['headspace_co2_buffer_assumption'] == (
        'equilibrium_upper_bound_homogeneous_kinetics_unverified'
    )


def test_interface_po2_uses_two_film_log_series_resistance_and_publishes_regime():
    """The release boundary is the grounded gas/melt interface, not bulk fO2."""

    sim = _transport_sim()
    sim.melt.temperature_C = 1500.0 - 273.15
    # 100 mbar puts the declared 12 cm duct in the continuum Sherwood branch;
    # the lunar C0 vacuum path below is separately covered by the inf limit.
    sim.melt.p_total_mbar = 100.0
    sim._melt_headspace_composition_mbar = {'N2': 1.0}
    sim.overhead.composition = {'N2': 1.0e6}
    gas_k_with_downstream_report, gas_source = (
        sim._oxygen_interface_gas_side_k_m_s(1773.15)
    )
    sim.overhead.composition = {}
    gas_k_without_downstream_report, _ = (
        sim._oxygen_interface_gas_side_k_m_s(1773.15)
    )
    assert gas_source == 'evaporation_sherwood_chapman_enskog_O2'
    assert gas_k_with_downstream_report == pytest.approx(
        gas_k_without_downstream_report
    )
    reservoir = sim.melt.oxygen_reservoir
    reservoir.headspace_transport_pO2_bar = 1.0e-6
    reservoir.melt_intrinsic_fO2_log = -4.0

    interface_pO2_bar = sim._interface_pO2_bar()
    diagnostic = sim._last_oxygen_interface_diagnostic
    gas_k = diagnostic['gas_side_k_m_s']
    melt_k = diagnostic['melt_side_k_O_m_s']
    transport_pO2_bar = reservoir.headspace_transport_pO2_bar
    melt_pO2_bar = diagnostic['melt_intrinsic_pO2_bar']
    expected_bar = math.exp(
        (
            gas_k * math.log(transport_pO2_bar)
            + melt_k * math.log(melt_pO2_bar)
        )
        / (gas_k + melt_k)
    )

    assert math.isfinite(gas_k) and gas_k > 0.0
    assert interface_pO2_bar == pytest.approx(expected_bar, rel=1.0e-12)
    assert reservoir.interface_pO2_bar == pytest.approx(interface_pO2_bar)
    assert reservoir.interface_pO2_limiting_regime == diagnostic[
        'limiting_regime'
    ]
    assert diagnostic['limiting_regime'] in {
        'gas_side_limited',
        'melt_side_limited',
    }
    assert min(transport_pO2_bar, melt_pO2_bar) < interface_pO2_bar < max(
        transport_pO2_bar,
        melt_pO2_bar,
    )


@pytest.mark.parametrize('target_fe3_fraction', [1.0e-300, 1.0 - 1.0e-6])
def test_interface_po2_holds_headspace_at_kress91_ratio_limits(
    target_fe3_fraction: float,
):
    """Ratio-limit bulk redox cannot become a nonphysical surface source."""

    sim = _transport_sim()
    sim.melt.temperature_C = 1500.0 - 273.15
    sim.melt.p_total_mbar = 5.0
    reservoir = sim.melt.oxygen_reservoir
    reservoir.headspace_transport_pO2_bar = 1.0e-9
    T_K = sim.melt.temperature_C + 273.15
    pressure_bar = sim.melt.p_total_mbar / 1000.0
    comp = sim._melt_oxide_wt_pct()

    # Invert the grounded Kress91 Fe3+/ΣFe map at the assertion site. This
    # keeps the test's redox-limit input independent of the interface guard.
    low_log, high_log = -400.0, 400.0
    for _ in range(160):
        mid_log = 0.5 * (low_log + high_log)
        fraction = sim._fe3_over_sigma_fe_at_fO2(
            comp,
            fO2_log=mid_log,
            T_K=T_K,
            pressure_bar=pressure_bar,
        )
        if fraction < target_fe3_fraction:
            low_log = mid_log
        else:
            high_log = mid_log
    reservoir.melt_intrinsic_fO2_log = 0.5 * (low_log + high_log)
    actual_fraction = sim._fe3_over_sigma_fe_at_fO2(
        comp,
        fO2_log=reservoir.melt_intrinsic_fO2_log,
        T_K=T_K,
        pressure_bar=pressure_bar,
    )
    if target_fe3_fraction < 0.5:
        assert actual_fraction <= 1.0e-6
    else:
        assert 1.0 - actual_fraction <= 1.0e-6 + 1.0e-12

    interface_pO2_bar = sim._interface_pO2_bar()
    transport_pO2_bar = reservoir.headspace_transport_pO2_bar
    hold_pO2_bar = max(
        transport_pO2_bar,
        sim._headspace_control_floor_pO2_bar(),
    )
    assert sim._vacuum_floor_bar() <= interface_pO2_bar
    diagnostic = sim._last_oxygen_interface_diagnostic
    if target_fe3_fraction < 0.5:
        assert interface_pO2_bar <= hold_pO2_bar
        assert interface_pO2_bar == pytest.approx(transport_pO2_bar)
        assert diagnostic['redox_buffer_status'] == 'exhausted'
        assert diagnostic['redox_buffer_exhausted'] is True
        assert diagnostic['limiting_regime'] == (
            'gas_side_redox_buffer_exhausted'
        )
    else:
        # A high-Fe melt near the former ratio threshold still has finite
        # differential capacity.  Exhaustion is inventory/capacity based, not
        # a ferric-fraction clamp.
        assert diagnostic['redox_buffer_status'] == 'available'
        assert diagnostic['redox_buffer_exhausted'] is False
        assert interface_pO2_bar > transport_pO2_bar
        assert diagnostic['limiting_regime'] in {
            'gas_side_limited',
            'melt_side_limited',
        }

    equilibrium = sim._internal_analytical_equilibrium()
    release_pressures = [
        float(value) for value in equilibrium.vapor_pressures_Pa.values()
    ]
    assert release_pressures
    assert all(
        math.isfinite(value) and value < CATALOG_PHYSICAL_PRESSURE_CEILING_PA
        for value in release_pressures
    )


def test_interface_distinguishes_fe_free_from_capacity_exhaustion(monkeypatch):
    sim = _transport_sim()
    sim.melt.temperature_C = 1500.0 - 273.15
    sim.melt.oxygen_reservoir.headspace_transport_pO2_bar = 1.0e-9
    monkeypatch.setattr(sim, '_cleaned_melt_fe_atom_mol', lambda: 0.0)

    interface_pO2_bar = sim._interface_pO2_bar()
    diagnostic = sim._last_oxygen_interface_diagnostic

    assert interface_pO2_bar == pytest.approx(1.0e-9)
    assert diagnostic['redox_buffer_status'] == 'no_fe_redox_buffer'
    assert diagnostic['redox_buffer_exhausted'] is False
    assert diagnostic['limiting_regime'] == 'gas_side_no_fe_redox_buffer'


def test_interface_marks_positive_fe_with_zero_capacity_exhausted(monkeypatch):
    sim = _transport_sim()
    sim.melt.temperature_C = 1500.0 - 273.15
    sim.melt.oxygen_reservoir.headspace_transport_pO2_bar = 1.0e-9
    monkeypatch.setattr(
        sim,
        '_melt_redox_source_capacity_mol_per_ln_fO2',
        lambda **_: 0.0,
    )

    interface_pO2_bar = sim._interface_pO2_bar()
    diagnostic = sim._last_oxygen_interface_diagnostic

    assert interface_pO2_bar == pytest.approx(1.0e-9)
    assert diagnostic['redox_buffer_status'] == 'exhausted'
    assert diagnostic['redox_buffer_inventory_mol'] > 0.0
    assert diagnostic['redox_buffer_exhausted'] is True


def test_na2o_evaporation_from_fe_free_melt_has_no_direct_redox_source(
    monkeypatch,
):
    sim = _transport_sim()
    sim.melt.temperature_C = 1600.0
    sim._overhead_headspace_config['enabled'] = False
    monkeypatch.setattr(sim, '_cleaned_melt_fe_atom_mol', lambda: 0.0)

    before_fO2 = sim._current_melt_redox_fO2_log()
    sp_data = sim.vapor_pressures['oxide_vapors']['Na2O_gas']
    stoich = sim._evaporation_stoich('Na2O_gas', sp_data)
    available_kg = sim.atom_ledger.kg_by_account(
        'process.cleaned_melt'
    ).get(stoich['parent_oxide'], 0.0)
    rate_kg_hr = min(
        1.0e-6,
        0.1 * available_kg / float(stoich['oxide_per_product_kg']),
    )

    _, transition = sim._credit_evaporation_transition(
        'Na2O_gas',
        rate_kg_hr,
        rate_kg_hr,
        sp_data,
        return_transition=True,
    )
    assert transition is not None
    assert sim._evaporative_redox_source_terms_from_transition(transition) == {}
    assert sim._current_melt_redox_fO2_log() == pytest.approx(before_fO2)


def test_interface_po2_refuses_missing_sso_r_exchange_config():
    sim = _transport_sim()
    sim.setpoints['sso_r'] = {}

    with pytest.raises(
        OxygenInterfaceConfigurationError,
        match='missing_sso_r_oxygen_exchange_config',
    ) as exc_info:
        sim._interface_pO2_bar()

    assert exc_info.value.reason == 'missing_sso_r_oxygen_exchange_config'


def test_internal_equilibrium_uses_interface_for_all_surface_release_consumers():
    """The analytical consumer applies the same interface pO2 to every rail."""

    sim = _transport_sim()
    sim.melt.temperature_C = 1500.0
    sim.melt.p_total_mbar = 5.0
    reservoir = sim.melt.oxygen_reservoir
    reservoir.melt_intrinsic_fO2_log = -9.0

    reservoir.headspace_transport_pO2_bar = 1.0e-9
    low = sim._internal_analytical_equilibrium().vapor_pressures_Pa
    low_interface = reservoir.interface_pO2_bar

    reservoir.headspace_transport_pO2_bar = 1.0e-3
    high_result = sim._internal_analytical_equilibrium()
    high = high_result.vapor_pressures_Pa
    high_interface = reservoir.interface_pO2_bar
    vapor_diagnostic = high_result.diagnostics

    assert high_interface > low_interface
    assert vapor_diagnostic['headspace_transport_pO2_bar'] == pytest.approx(
        1.0e-3
    )
    assert vapor_diagnostic['interface_pO2_bar'] == pytest.approx(
        high_interface
    )
    assert vapor_diagnostic['interface_pO2_bar'] != pytest.approx(
        vapor_diagnostic['headspace_transport_pO2_bar']
    )
    for species in ('Na', 'K', 'Fe', 'SiO'):
        assert low[species] > 0.0
        assert high[species] > 0.0
        assert high[species] != pytest.approx(low[species])
