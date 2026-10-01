"""Grounded tests for the physical headspace O₂ transport terms."""

from __future__ import annotations

import math
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import simulator.core as core_module
from simulator.core import (
    Atmosphere,
    OxygenInterfaceConfigurationError,
    PyrolysisSimulator,
)
from engines.builtin.overhead_bleed import controlled_flow_capacity
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
from simulator.state import GAS_CONSTANT as STATE_GAS_CONSTANT, EvaporationFlux
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


def _trace_fe_transport_sim() -> PyrolysisSimulator:
    def load(name: str):
        return yaml.safe_load((ROOT / 'data' / name).read_text())

    setpoints = load('setpoints.yaml')
    setpoints.setdefault('chemistry_kernel', {})['allow_fallback_vapor'] = True
    setpoints['chemistry_kernel']['allow_unmeasured_alpha_fallback'] = True
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(
        backend,
        setpoints,
        {
            'trace_fe': {
                'label': 'Trace Fe test melt',
                'composition_wt_pct': {
                    'SiO2': 99.999998,
                    'FeO': 1.0e-9,
                    'Fe2O3': 1.0e-9,
                },
            }
        },
        load('vapor_pressures.yaml'),
    )
    sim.load_batch('trace_fe', mass_kg=1.0)
    sim.melt.temperature_C = 1600.0
    sim.melt.p_total_mbar = 100.0
    sim.melt.atmosphere = Atmosphere.PN2_SWEEP
    sim._overhead_headspace_config['enabled'] = True
    sim._melt_headspace_composition_mbar = {'N2': 1.0}
    sim._melt_redox_ledger_initialized = True
    sim._sync_oxygen_reservoir_mirror()

    # No interface transfer this tick.  A latched previous amount is not
    # the handover magnitude, so the interior ratio still inverts Kress.
    reservoir = sim.melt.oxygen_reservoir
    reservoir.headspace_transport_pO2_bar = 1.0e-6
    reservoir.exchange_o2_mol = 0.0
    reservoir.shadow_oxygen_transfer = {
        'status': 'ok',
        'transfer_o2_mol': 0.0,
        'committed_o2_mol': 0.0,
    }
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

    def independent_conductance_at_kn(knudsen):
        mean_pressure_pa = pressure_factor / knudsen
        molecular = (
            math.pi
            / 12.0
            * math.sqrt(8.0 * GAS_CONSTANT * T_K / (math.pi * molar_mass_kg_mol))
            * diameter_m ** 3
            / length_m
        )
        viscous = (
            math.pi
            * (diameter_m / 2.0) ** 4
            * mean_pressure_pa
            / (8.0 * viscosity_pa_s * length_m)
        )
        if knudsen <= VISCOUS_KNUDSEN_MAX:
            return viscous
        if knudsen >= FREE_MOLECULAR_KNUDSEN_MIN:
            return molecular
        weight = math.log(FREE_MOLECULAR_KNUDSEN_MIN / knudsen) / math.log(
            FREE_MOLECULAR_KNUDSEN_MIN / VISCOUS_KNUDSEN_MAX
        )
        return (1.0 - weight) * molecular + weight * viscous

    def result_at_kn(knudsen):
        mean_pressure_pa = pressure_factor / knudsen
        expected_conductance = independent_conductance_at_kn(knudsen)
        target_headspace_pa = 2.0 * mean_pressure_pa
        flow = expected_conductance * target_headspace_pa / (GAS_CONSTANT * T_K)
        return _duct_result(flow, flow), expected_conductance

    for boundary, expected in (
        (
            VISCOUS_KNUDSEN_MAX,
            independent_conductance_at_kn(VISCOUS_KNUDSEN_MAX),
        ),
        (
            FREE_MOLECULAR_KNUDSEN_MIN,
            independent_conductance_at_kn(FREE_MOLECULAR_KNUDSEN_MIN),
        ),
    ):
        result, _ = result_at_kn(boundary)
        assert result.conductance_m3_s == pytest.approx(expected, rel=2.0e-7)

    threshold_epsilon = 1.0e-6
    for boundary in (VISCOUS_KNUDSEN_MAX, FREE_MOLECULAR_KNUDSEN_MIN):
        for side in (1.0 - threshold_epsilon, 1.0 + threshold_epsilon):
            target_kn = boundary * side
            result, _ = result_at_kn(target_kn)
            expected_headspace_bar = (
                2.0 * pressure_factor / target_kn / 1.0e5
            )
            assert result.p_headspace_bar == pytest.approx(
                expected_headspace_bar,
                rel=2.0e-7,
            )
            assert result.knudsen_number == pytest.approx(target_kn, rel=2.0e-7)

    flows = [10.0 ** (-7.0 + 4.0 * index / 2000.0) for index in range(2001)]
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


def test_large_o2_headspace_bolus_pumps_down_and_is_credited_once():
    sim = _transport_sim()
    sim._overhead_headspace_config.update({"enabled": True, "volume_m3": 1.0})
    sim.overhead_model.pipe_diameter_m = 0.01
    sim.overhead_model.pipe_length_m = 1.0
    sim.melt.temperature_C = 1000.0
    sim.melt.atmosphere = Atmosphere.CONTROLLED_O2
    sim.melt.p_total_mbar = 13.0
    sim.melt.pO2_mbar = 13.0

    commanded_pressure_Pa = sim.melt.p_total_mbar * 100.0
    assert sim._headspace_upstream_pressure_Pa() == pytest.approx(
        commanded_pressure_Pa
    )
    o2_kg_per_mol = sim._overhead_holdup_species_kg({"O2": 1.0})["O2"]
    commanded_pipe_capacity_kg_hr = sim.overhead_model._pipe_conductance(
        commanded_pressure_Pa,
        sim.melt.temperature_C,
        p_downstream_Pa=0.0,
        species_kg_for_M_avg={"O2": 1.0},
    ) * 3600.0
    bolus_kg = max(2.0 * commanded_pipe_capacity_kg_hr, 0.1)
    bolus_mol = bolus_kg / o2_kg_per_mol
    sim.atom_ledger.load_external_mol(
        "process.overhead_gas",
        {"O2": bolus_mol},
        source="test finite headspace O2 bolus",
        material_origin="feedstock",
    )

    initial_pressure_Pa = sim._headspace_upstream_pressure_Pa()
    initial_species_kg = sim._overhead_holdup_species_kg()
    initial_mass_kg = sum(initial_species_kg.values())
    true_pipe_capacity_kg_hr = sim.overhead_model._pipe_conductance(
        initial_pressure_Pa,
        sim.melt.temperature_C,
        p_downstream_Pa=0.0,
        species_kg_for_M_avg=initial_species_kg,
    ) * 3600.0
    assert commanded_pipe_capacity_kg_hr < initial_mass_kg
    # k(P_up²-P_down²) makes the one-tick capacity exceed the whole bolus at
    # its true pressure, so the bounded result is the 13 mbar setpoint.
    assert true_pipe_capacity_kg_hr >= initial_mass_kg

    flux = EvaporationFlux(species_kg_hr={"O2": 1.0})
    flux.update_totals()
    true_controlled_pipe_capacity_kg_hr = sim.overhead_model._pipe_conductance(
        initial_pressure_Pa,
        sim.melt.temperature_C,
        p_downstream_Pa=0.0,
        species_kg_for_M_avg=flux.species_kg_hr,
    ) * 3600.0
    controlled = sim._controlled_o2_transport_capacity(flux)
    assert controlled is not None
    assert controlled.upstream_pressure_bar == pytest.approx(
        initial_pressure_Pa / 1.0e5
    )
    assert controlled.pipe_capacity_kg_hr == pytest.approx(
        true_controlled_pipe_capacity_kg_hr
    )

    sim.melt.atmosphere = Atmosphere.HARD_VACUUM
    assert sim._headspace_bleed_conductance_kg_s() * 3600.0 == pytest.approx(
        true_pipe_capacity_kg_hr
    )
    transitions_before = len(sim.atom_ledger.transitions)
    offgas_account = "terminal.oxygen_melt_offgas_stored"
    offgas_before_mol = sim.atom_ledger.mol_by_account(offgas_account).get(
        "O2", 0.0
    )

    sim._dispatch_overhead_bleed()

    remaining_mol = sim.atom_ledger.mol_by_account(
        "process.overhead_gas"
    ).get("O2", 0.0)
    removed_mol = bolus_mol - remaining_mol
    offgas_after_mol = sim.atom_ledger.mol_by_account(offgas_account).get(
        "O2", 0.0
    )
    target_mol = (
        commanded_pressure_Pa
        * sim._headspace_volume_m3()
        / (STATE_GAS_CONSTANT * sim._headspace_temperature_K())
    )
    assert remaining_mol == pytest.approx(target_mol, rel=1e-12)
    assert offgas_after_mol - offgas_before_mol == pytest.approx(
        removed_mol
    )
    assert sim._headspace_upstream_pressure_Pa() == pytest.approx(
        commanded_pressure_Pa
    )
    for transition in sim.atom_ledger.transitions[transitions_before:]:
        transition.validate_conservation(sim.atom_ledger.registry)


@pytest.mark.parametrize("outlet_config", ["headspace", "train"])
def test_quasi_steady_bleed_uses_configured_outlet_pressure(outlet_config):
    sim = _transport_sim()
    sim._overhead_headspace_config.update({"enabled": True, "volume_m3": 1.0})
    sim.overhead_model.pipe_diameter_m = 0.12
    sim.overhead_model.pipe_length_m = 1.0
    sim.melt.temperature_C = 1000.0
    sim.melt.atmosphere = Atmosphere.CONTROLLED_O2
    sim.melt.p_total_mbar = 13.0
    sim.melt.pO2_mbar = 13.0
    outlet_pressure_bar = 0.2
    if outlet_config == "headspace":
        sim._overhead_headspace_config["downstream_pressure_bar"] = (
            outlet_pressure_bar
        )
    else:
        sim.overhead_model._downstream_pressure_override = outlet_pressure_bar

    initial_pressure_Pa = 0.3e5
    initial_mol = (
        initial_pressure_Pa
        * sim._headspace_volume_m3()
        / (STATE_GAS_CONSTANT * sim._headspace_temperature_K())
    )
    sim.atom_ledger.load_external_mol(
        "process.overhead_gas",
        {"O2": initial_mol},
        source="configured-outlet headspace pressure test",
        material_origin="feedstock",
    )

    sim._dispatch_overhead_bleed()

    remaining_mol = sum(sim._overhead_holdup_mol().values())
    actual_pressure_bar = (
        remaining_mol
        * STATE_GAS_CONSTANT
        * sim._headspace_temperature_K()
        / sim._headspace_volume_m3()
        / 1.0e5
    )
    assert actual_pressure_bar == pytest.approx(outlet_pressure_bar, rel=1.0e-12)


def test_stale_headspace_bolus_drains_to_command_when_source_stops():
    sim = _transport_sim()
    sim._overhead_headspace_config.update({"enabled": True, "volume_m3": 1.0})
    sim.overhead_model.pipe_diameter_m = 0.12
    sim.overhead_model.pipe_length_m = 1.0
    sim.melt.temperature_C = 1000.0
    sim.melt.atmosphere = Atmosphere.HARD_VACUUM
    sim.melt.p_total_mbar = 13.0
    steady_pressure_Pa = 0.3e5
    species_basis = {"O2": 1.0}
    k_kg_s_Pa2 = sim.overhead_model._pipe_conductance(
        1.0,
        sim.melt.temperature_C,
        p_downstream_Pa=0.0,
        species_kg_for_M_avg=species_basis,
    )
    sim._headspace_transport_source_mass_kg_s = (
        k_kg_s_Pa2 * steady_pressure_Pa**2
    )
    bolus_mol = (
        steady_pressure_Pa
        * sim._headspace_volume_m3()
        / (STATE_GAS_CONSTANT * sim._headspace_temperature_K())
    )
    sim.atom_ledger.load_external_mol(
        "process.overhead_gas",
        {"O2": bolus_mol},
        source="stale headspace bolus test",
        material_origin="feedstock",
    )

    sim._dispatch_overhead_bleed()
    after_source_mol = sum(sim._overhead_holdup_mol().values())
    source_pressure_Pa = (
        after_source_mol
        * STATE_GAS_CONSTANT
        * sim._headspace_temperature_K()
        / sim._headspace_volume_m3()
    )
    assert source_pressure_Pa == pytest.approx(steady_pressure_Pa, rel=1.0e-12)

    sim._headspace_transport_source_mass_kg_s = 0.0
    sim._dispatch_overhead_bleed()
    final_pressure_Pa = (
        sum(sim._overhead_holdup_mol().values())
        * STATE_GAS_CONSTANT
        * sim._headspace_temperature_K()
        / sim._headspace_volume_m3()
    )
    assert final_pressure_Pa == pytest.approx(13.0e2, rel=1.0e-12)


def test_steady_headspace_source_reaches_pressure_and_removes_source_each_tick():
    sim = _transport_sim()
    sim._overhead_headspace_config.update({"enabled": True, "volume_m3": 1.0})
    sim.overhead_model.pipe_diameter_m = 0.01
    sim.overhead_model.pipe_length_m = 1.0
    sim.melt.temperature_C = 1000.0
    sim.melt.atmosphere = Atmosphere.HARD_VACUUM
    sim.melt.p_total_mbar = 13.0

    commanded_pressure_Pa = sim.melt.p_total_mbar * 100.0
    volume_m3 = sim._headspace_volume_m3()
    temperature_K = sim._headspace_temperature_K()
    species_basis = {"O2": 1.0}
    k_kg_s_Pa2 = sim.overhead_model._pipe_conductance(
        1.0,
        sim.melt.temperature_C,
        p_downstream_Pa=0.0,
        species_kg_for_M_avg=species_basis,
    )
    assert k_kg_s_Pa2 > 0.0
    steady_pressure_Pa = 2.0 * commanded_pressure_Pa
    source_mass_kg_s = k_kg_s_Pa2 * steady_pressure_Pa ** 2
    oxygen_molar_mass_kg_mol = core_module.OXYGEN_MOLAR_MASS_KG_PER_MOL
    source_mol_per_tick = source_mass_kg_s * 3600.0 / oxygen_molar_mass_kg_mol

    sim.atom_ledger.load_external_mol(
        "process.overhead_gas",
        {
            "O2": (
                commanded_pressure_Pa * volume_m3
                / (STATE_GAS_CONSTANT * temperature_K)
            )
        },
        source="steady-headspace test setpoint inventory",
        material_origin="feedstock",
    )

    removed_per_tick = []
    pressure_per_tick = []
    for hour in range(2):
        sim.atom_ledger.load_external_mol(
            "process.overhead_gas",
            {"O2": source_mol_per_tick},
            source=f"steady-headspace test source hour {hour + 1}",
            material_origin="feedstock",
        )
        sim._headspace_transport_source_mass_kg_s = source_mass_kg_s
        sim._headspace_transport_source_total_mol_s = (
            source_mass_kg_s / oxygen_molar_mass_kg_mol
        )
        sim._headspace_transport_source_o2_mol_s = (
            source_mass_kg_s / oxygen_molar_mass_kg_mol
        )
        before_mol = sum(sim._overhead_holdup_mol().values())
        result = sim._dispatch_overhead_bleed()
        after_mol = sum(sim._overhead_holdup_mol().values())
        removed_per_tick.append(before_mol - after_mol)
        pressure_per_tick.append(
            after_mol * STATE_GAS_CONSTANT * temperature_K / volume_m3
        )
        expected_remaining_mol = (
            steady_pressure_Pa * volume_m3
            / (STATE_GAS_CONSTANT * temperature_K)
        )
        assert removed_per_tick[-1] == pytest.approx(
            max(0.0, before_mol - expected_remaining_mol), rel=1.0e-12
        )
        assert result.status == "ok"

    assert pressure_per_tick == pytest.approx(
        [steady_pressure_Pa, steady_pressure_Pa], rel=1.0e-12
    )
    # First tick fills the difference between commanded and steady inventory;
    # once at steady state, the next tick's removal equals its source exactly.
    assert removed_per_tick[1] == pytest.approx(
        source_mol_per_tick, rel=1.0e-12
    )


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


def test_co2_duct_outlet_uses_effective_pump_downstream_pressure():
    sim = _transport_sim()
    sim.melt.atmosphere = Atmosphere.CO2_BACKPRESSURE
    sim.melt.p_total_mbar = 5.0
    sim._headspace_transport_source_total_mol_s = 0.0
    sim._headspace_transport_source_o2_mol_s = 0.0
    sim._headspace_transport_source_molar_mass_kg_mol = 0.031998
    pump_capacity = controlled_flow_capacity(
        pipe_capacity_kg_hr=10.0,
        equipment_capacity_kg_hr=2.0,
        evolved_flux_kg_hr=1.0,
        upstream_pressure_bar=5.0e-3,
    )
    sim._effective_transport_capacity_this_tick = pump_capacity

    result = sim._headspace_venting_throughput()

    assert result.p_headspace_bar == pytest.approx(
        pump_capacity.downstream_pressure_bar
    )
    assert result.p_headspace_bar < 5.0e-3


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


def test_interface_po2_uses_finite_two_film_force_and_publishes_regime():
    """The release boundary uses finite ledger O2 inventory, not a tangent law."""

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
    reservoir.melt_intrinsic_fO2_log = sim._melt_fO2_from_ledger(
        T_K=sim.melt.temperature_C + 273.15
    )

    interface_pO2_bar = sim._interface_pO2_bar()
    diagnostic = sim._last_oxygen_interface_diagnostic
    gas_k = diagnostic['gas_side_k_m_s']
    melt_k = diagnostic['melt_side_k_O_m_s']
    transport_pO2_bar = reservoir.headspace_transport_pO2_bar
    melt_pO2_bar = diagnostic['melt_intrinsic_pO2_bar']
    gas_temperature_K = float(
        getattr(sim.overhead, 'headspace_temperature_K', 0.0)
        or sim.melt.temperature_C + 273.15
    )
    gas_pressure_factor = 1.0e5 / (STATE_GAS_CONSTANT * gas_temperature_K)
    melt_depth_m = float(
        sim.setpoints['sso_r']['oxygen_exchange']['effective_melt_depth_m']
    )
    gas_flux = gas_k * gas_pressure_factor * (
        transport_pO2_bar - interface_pO2_bar
    )
    melt_flux = melt_k * diagnostic['finite_melt_driving_force_mol'] / (
        sim.melt.melt_surface_area_m2 * melt_depth_m
    )
    assert math.isfinite(gas_k) and gas_k > 0.0
    assert diagnostic['finite_melt_driving_force_mol'] == pytest.approx(
        diagnostic['melt_oxygen_equilibrium_mol']
        - diagnostic['melt_oxygen_ledger_mol']
    )
    # The root stays inside the melt and transport pressures.  When those
    # two pressures coincide, an exact closed bracket rejects the rounding.
    low_pO2_bar = min(transport_pO2_bar, melt_pO2_bar)
    high_pO2_bar = max(transport_pO2_bar, melt_pO2_bar)
    bracket_ulp = 8.0 * max(low_pO2_bar, high_pO2_bar, 1.0e-30) * 2.220446049250313e-16
    assert low_pO2_bar - bracket_ulp <= interface_pO2_bar <= high_pO2_bar + bracket_ulp
    if diagnostic['interface_root_clamped']:
        # The fixture is at a native-Fe/FeO ledger endpoint, so its formal
        # Kress91 equilibrium can sit outside the gas/melt pressure bracket.
        # The finite root must report that typed diagnostic instead of
        # inventing a flux continuity point.
        assert diagnostic['interface_root_residual_mol_m2_s'] != pytest.approx(
            0.0,
            abs=2.0e-14,
        )
    else:
        assert gas_flux == pytest.approx(melt_flux, rel=1.0e-10, abs=2.0e-14)
        assert diagnostic['interface_flux_mol_m2_s'] == pytest.approx(
            gas_flux,
            rel=1.0e-10,
            abs=2.0e-14,
        )
    assert reservoir.interface_pO2_bar == pytest.approx(interface_pO2_bar)
    assert reservoir.interface_pO2_limiting_regime == diagnostic[
        'limiting_regime'
    ]
    assert diagnostic['limiting_regime'] in {
        'gas_side_limited',
        'melt_side_limited',
    }


def test_finite_interface_root_conserves_flux_for_interior_inventory():
    sim = _transport_sim()
    sim.melt.temperature_C = 1500.0 - 273.15
    sim.melt.p_total_mbar = 100.0
    sim._melt_headspace_composition_mbar = {'N2': 1.0}
    T_K = sim.melt.temperature_C + 273.15
    comp = sim._melt_oxide_wt_pct()
    mol_fractions = core_module.melt_mol_fractions_for_kress91(comp)
    kress91_evaluator = core_module._Kress91Evaluator(
        mol_fractions=mol_fractions,
        T_K=T_K,
        pressure_bar=0.1,
    )
    k_m, _, _ = sim._oxygen_exchange_k_m_s(T_K)
    k_g, _ = sim._oxygen_interface_gas_side_k_m_s(T_K)
    melt_pO2_bar = sim._oxygen_melt_pO2_bar_for_inventory(
        n_feo_mol=2.0,
        n_fe2o3_mol=1.0,
        kress91_evaluator=kress91_evaluator,
        fallback_pO2_bar=1.0e-4,
    )
    root = sim._oxygen_finite_interface_root(
        gas_pO2_bar=1.0e-9,
        melt_pO2_bar=melt_pO2_bar,
        gas_temperature_K=T_K,
        k_g=k_g,
        k_m=k_m,
        surface_area_m2=sim.melt.melt_surface_area_m2,
        h_eff_m=sim.setpoints['sso_r']['oxygen_exchange'][
            'effective_melt_depth_m'
        ],
        kress91_evaluator=kress91_evaluator,
        n_feo_mol=2.0,
        n_fe2o3_mol=1.0,
        capacity_mol_per_ln_fO2=1.0,
    )

    assert root['interface_root_clamped'] is False
    assert root['finite_melt_driving_force_mol'] == pytest.approx(
        root['melt_oxygen_equilibrium_mol']
        - root['melt_oxygen_ledger_mol']
    )
    assert root['gas_flux_mol_m2_s'] == pytest.approx(
        root['melt_flux_mol_m2_s'],
        rel=1.0e-10,
        abs=2.0e-14,
    )
    assert math.isfinite(root['interface_flux_mol_m2_s'])


@pytest.mark.parametrize('target_fe3_fraction', [1.0e-300, 1.0 - 1.0e-6])
def test_interface_po2_holds_headspace_at_kress91_ratio_limits(
    target_fe3_fraction: float,
):
    """Only inventory available in the current ratio-limit direction buffers."""

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
        # Ferrous inventory can absorb O2 at the low-ferric endpoint even
        # when the local differential Kress capacity is small.
        assert interface_pO2_bar == pytest.approx(hold_pO2_bar)
        assert diagnostic['redox_buffer_status'] == 'available'
        assert diagnostic['redox_buffer_exhausted'] is False
    else:
        # The fixture's ledger is ferrous; a cached ferric endpoint cannot
        # invent Fe2O3 inventory for release in the opposite direction.
        assert interface_pO2_bar == pytest.approx(transport_pO2_bar)
        assert diagnostic['redox_buffer_status'] == 'exhausted'
        assert diagnostic['redox_buffer_exhausted'] is True
        assert diagnostic['limiting_regime'] == (
            'gas_side_redox_buffer_exhausted'
        )

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


def test_interface_keeps_directional_inventory_when_differential_capacity_is_zero(
    monkeypatch,
):
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

    assert interface_pO2_bar >= sim._vacuum_floor_bar()
    assert diagnostic['redox_buffer_status'] == 'available'
    assert diagnostic['redox_buffer_inventory_mol'] > 0.0
    assert diagnostic['redox_buffer_exhausted'] is False


def test_trace_fe_directional_inventory_uses_kress_inverse(
    monkeypatch,
):
    sim = _trace_fe_transport_sim()
    ratios = iter((0.03, 0.9999))

    def changing_ledger_ratio():
        return next(ratios, 0.9999)

    monkeypatch.setattr(sim, '_ledger_fe3_over_sigma_fe', changing_ledger_ratio)

    inverse_evaluations = []
    inverse = core_module.kress91_log_fO2_from_fe3_over_sigma_fe

    def record_inverse(**kwargs):
        result = inverse(**kwargs)
        inverse_evaluations.append((
            float(kwargs['fe3_over_sigma_fe']),
            result,
        ))
        return result

    monkeypatch.setattr(
        core_module,
        'kress91_log_fO2_from_fe3_over_sigma_fe',
        record_inverse,
    )

    samples = []
    for _ in range(2):
        evaluation_start = len(inverse_evaluations)
        fO2_log = sim._current_melt_redox_fO2_log()
        ferric_input, inverse_result = inverse_evaluations[evaluation_start]
        domain = dict(sim._last_redox_domain)
        interface_pO2_bar = sim._interface_pO2_bar()
        equilibrium = sim._internal_analytical_equilibrium()
        samples.append((
            fO2_log,
            domain,
            interface_pO2_bar,
            float(equilibrium.vapor_pressures_Pa['SiO']),
            ferric_input,
            inverse_result,
        ))

    transport_pO2_bar = sim.melt.oxygen_reservoir.headspace_transport_pO2_bar
    assert transport_pO2_bar == pytest.approx(1.0e-6)
    expected_ferric_inputs = (0.03, 0.9999)
    assert [sample[4] for sample in samples] == pytest.approx(
        expected_ferric_inputs
    )
    assert [sample[0] for sample in samples] == pytest.approx(
        [sample[5] for sample in samples]
    )
    for fO2_log, domain, interface_pO2_bar, sio_p, _, _ in samples:
        assert domain['basis'] == 'kress91_inverse'
        melt_pO2_bar = 10.0 ** fO2_log
        assert min(melt_pO2_bar, transport_pO2_bar) * (1.0 - 1.0e-6) <= (
            interface_pO2_bar
        )
        assert interface_pO2_bar <= max(melt_pO2_bar, transport_pO2_bar) * (
            1.0 + 1.0e-6
        )
        assert math.isfinite(sio_p) and sio_p > 0.0


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


def test_oxygen_exchange_refuses_missing_config_before_no_capacity_return():
    sim = _transport_sim()
    sim.setpoints['sso_r'] = {}

    with pytest.raises(
        OxygenInterfaceConfigurationError,
        match='missing_sso_r_oxygen_exchange_config',
    ):
        sim._apply_oxygen_reservoir_exchange()


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
