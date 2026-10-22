"""b-1016: an imposed carrier partial is not a vapour back-pressure."""
import pytest

from engines.builtin.overhead_bleed import controlled_flow_capacity
from simulator.overhead import OverheadGasModel, continuous_sweep_balance
from simulator.state import Atmosphere, CondensationTrain, EvaporationFlux, MeltState, MOLAR_MASS


def _boundary(carrier, fraction=1.0, *, atmosphere=Atmosphere.CONTROLLED_O2):
    melt = MeltState(temperature_C=1600.0, atmosphere=atmosphere,
                     p_total_mbar=10.2, pO2_mbar=0.2)
    melt.background_gas_species = carrier
    melt.background_gas_mole_fraction = fraction
    flux = EvaporationFlux(total_kg_hr=0.1, species_kg_hr={"Fe": 0.1})
    model = OverheadGasModel({"enabled": False})
    capacity = controlled_flow_capacity(
        pipe_capacity_kg_hr=1.0, equipment_capacity_kg_hr=1.0,
        evolved_flux_kg_hr=0.1, upstream_pressure_bar=0.0102,
        pipe_diameter_m=0.12, pipe_length_m=1.0,
        gas_temperature_K=1873.15, gas_molar_mass_kg_mol=0.055845,
    )
    return model, melt, flux, capacity


@pytest.mark.parametrize("carrier", ["Kr", "N2"])
def test_carrier_full_pressure_requires_achieved_composition(carrier):
    model, melt, flux, capacity = _boundary(carrier)
    melt.sweep_mol_per_s = 0.01
    expected = _owner_steady_partials(melt, flux, carrier)
    transport = model.estimate_transport_state(flux, melt, effective_transport_capacity=capacity)
    partials = model.backpressure_partials(flux, melt, transport)
    assert partials['Fe'] == pytest.approx(expected['Fe'])
    assert partials == pytest.approx(expected)
    gas = model.update(flux, melt, CondensationTrain.create_default(),
                       effective_transport_capacity=capacity)
    assert gas.composition['Fe'] == pytest.approx(expected['Fe'])
    assert 0.0 < gas.composition['Fe'] < 10.2
    assert gas.composition['O2'] == 0.2  # C9 still owns this controlled floor.
    assert model.last_backpressure_notice is None


def _owner_steady_partials(melt, flux, carrier, oxygen_mol_hr=0.0):
    generated = {s: mass / (MOLAR_MASS[s] / 1000.0)
                 for s, mass in flux.species_kg_hr.items()}
    if oxygen_mol_hr:
        generated['O2'] = max(generated.get('O2', 0.0), oxygen_mol_hr)
    plan = continuous_sweep_balance(
        {}, generated, carrier=carrier, sweep_mol_per_s=melt.sweep_mol_per_s,
        dt_s=3600.0, pressure_bar=melt.p_total_mbar / 1000.0,
        volume_m3=0.001, temperature_K=melt.temperature_C + 273.15,
    )
    # The small chamber undergoes many residence times, so the integrated
    # production owner reaches its steady mixture within floating precision.
    partials_bar = OverheadGasModel._compute_partial_pressures(
        plan['final_species_mol'], 0.001, melt.temperature_C + 273.15)
    return {s: partials_bar.get(s, 0.0) * 1000.0 for s in plan['final_species_mol']}


def test_carrier_absent_controlled_split_is_bit_identical():
    model, melt, flux, capacity = _boundary("")
    transport = model.estimate_transport_state(flux, melt, effective_transport_capacity=capacity)
    partials = model.species_partial_pressures(flux, transport["vapor_pressure_mbar"])
    assert partials["Fe"].hex() == "0x1.4666666666666p+3"
    assert transport["pressure_mbar"].hex() == "0x1.4666666666666p+3"
    gas = model.update(flux, melt, CondensationTrain.create_default(), effective_transport_capacity=capacity)
    assert {key: value.hex() for key, value in gas.composition.items()} == {
        "Fe": "0x1.4666666666666p+3", "O2": "0x1.999999999999ap-3",
    }
    assert gas.pressure_mbar.hex() == "0x1.4ccccccccccccp+3"
    assert 'underspecified carrier boundary' in model.last_backpressure_notice


@pytest.mark.parametrize("carrier", ["Kr", "N2"])
def test_open_inert_pipe_keeps_source_only_pressure(carrier):
    model, melt, flux, _ = _boundary(carrier, atmosphere=Atmosphere.PN2_SWEEP)
    transport = model.estimate_transport_state(flux, melt)
    # An open pipe to vacuum has no carrier pressure in its source inversion.
    expected = model._vapor_pressure_mbar_from_flux(
        flux.total_kg_hr / 3600.0, melt.temperature_C,
        p_downstream_bar=0.0, species_kg_for_M_avg=flux.species_kg_hr,
    )
    assert transport["vapor_pressure_mbar"].hex() == expected.hex()
    assert model.species_partial_pressures(flux, expected)["Fe"].hex() == expected.hex()
    assert model.backpressure_partials(flux, melt, transport)['Fe'].hex() == expected.hex()


@pytest.mark.parametrize("carrier", ["Kr", "N2"])
def test_declared_carrier_legacy_full_total_pin(carrier):
    model, melt, flux, capacity = _boundary(carrier)
    gas = model.update(flux, melt, CondensationTrain.create_default(),
                       effective_transport_capacity=capacity)
    # Current defect: all 10.2 mbar is charged to Fe, in addition to the
    # independently retained 0.2 mbar O2 and 10 mbar carrier floors.
    assert {key: value.hex() for key, value in gas.composition.items()} == {
        "Fe": "0x1.4666666666666p+3", "O2": "0x1.999999999999ap-3",
        carrier: "0x1.4000000000000p+3",
    }
    assert gas.pressure_mbar.hex() == "0x1.4666666666666p+4"


def test_lab_schedule_argon_legacy_pin():
    from simulator.campaigns import CampaignManager
    from simulator.state import CampaignPhase
    from test_lab_geometry_runtime import dynamic_lab_schedule

    manager = CampaignManager({})
    manager.overrides["C2A"] = {"lab_schedule": dynamic_lab_schedule()}
    melt = MeltState(temperature_C=1700.0, campaign=CampaignPhase.C2A)
    manager.apply_lab_schedule_controls(melt, melt.campaign, sample_time_h=1.0)
    manager.apply_sweep_flow_control(melt)
    assert melt.sweep_mol_per_s == pytest.approx(0.00022307516702922367)
    assert melt.p_total_mbar.hex() == "0x1.a000000000000p+3"
    assert melt.background_gas_species == "Ar"
    assert melt.background_gas_mole_fraction == 1.0
    model = OverheadGasModel({"enabled": False})
    flux = EvaporationFlux(total_kg_hr=0.02, species_kg_hr={"SiO": 0.02})
    transport = model.estimate_transport_state(flux, melt)
    assert transport["vapor_pressure_mbar"].hex() == "0x1.a000000000000p+3"
    assert model.species_partial_pressures(flux, transport["vapor_pressure_mbar"])["SiO"].hex() == "0x1.a000000000000p+3"
    achieved = model.backpressure_partials(flux, melt, transport)
    assert achieved == pytest.approx(_owner_steady_partials(melt, flux, 'Ar'))
    assert 0.0 < achieved['SiO'] < 13.0


@pytest.mark.parametrize("requested_o2", [0.0, 0.2])
def test_controlled_carrier_sweep_preserves_oxygen_floors(requested_o2):
    from simulator.core import PyrolysisSimulator

    sim = PyrolysisSimulator.__new__(PyrolysisSimulator)
    sim.melt = MeltState(atmosphere=Atmosphere.CONTROLLED_O2,
                         pO2_mbar=requested_o2, p_total_mbar=13.0)
    sim.melt.sweep_mol_per_s = 0.00022307516702922367
    sim._physical_sweep_boundary = lambda: {"partials_bar": {"Ar": 0.013}}
    floor = sim._oxygen_numerical_floor_bar()
    expected = max(requested_o2 / 1000.0, floor)
    assert sim._commanded_pO2_bar() == expected
    assert sim._headspace_transport_pO2_bar() == expected
    assert sim._headspace_transport_pO2_bar_from_ledger(0.0) == expected


@pytest.mark.parametrize("flow", [None, 0.0, 0.3])
def test_lab_flow_declaration_survives_repeated_sweep_assignment(flow):
    from simulator.campaigns import CampaignManager
    from simulator.state import CampaignPhase
    from test_lab_geometry_runtime import dynamic_lab_schedule

    schedule = dynamic_lab_schedule()
    if flow is None:
        schedule["gas_boundary"]["imposed_flow"] = {
            "reported_status": "not_reported", "source_class": "literature_sidecar",
            "citation_id": "test", "digest": "test", "reason": "flow not measured",
        }
    else:
        schedule["gas_boundary"]["imposed_flow"]["value"] = flow
    manager = CampaignManager({})
    manager.overrides["C2A"] = {"lab_schedule": schedule}
    melt = MeltState(campaign=CampaignPhase.C2A)
    for _ in range(2):
        manager.apply_lab_schedule_controls(melt, melt.campaign, sample_time_h=1.0)
        manager.apply_sweep_flow_control(melt)
        if flow is None:
            assert melt.sweep_mol_per_s is None
        else:
            assert melt.sweep_mol_per_s == pytest.approx(
                {0.0: 0.0, 0.3: 0.00022307516702922367}[flow])


@pytest.mark.parametrize('flow', [0.0, 0.01])
def test_sweep_projection_matches_cstr_owner_and_zero_flow_limit(flow):
    model, melt, flux, capacity = _boundary('Kr')
    melt.sweep_mol_per_s = flow
    transport = model.estimate_transport_state(flux, melt, effective_transport_capacity=capacity)
    actual = model.backpressure_partials(flux, melt, transport)
    assert actual == pytest.approx(_owner_steady_partials(melt, flux, 'Kr'))
    if flow == 0.0:
        assert actual['Fe'].hex() == '0x1.4666666666666p+3'


def test_no_vapour_has_carrier_only_total():
    model, melt, _, _ = _boundary('Kr')
    melt.sweep_mol_per_s = 0.01
    flux = EvaporationFlux()
    transport = model.estimate_transport_state(flux, melt)
    assert model.backpressure_partials(flux, melt, transport) == {'Kr': 10.2}


def test_oxygen_source_is_in_cstr_denominator_once():
    model, melt, _, capacity = _boundary('N2')
    melt.sweep_mol_per_s = 0.01
    flux = EvaporationFlux(species_kg_hr={'Fe': 0.1, 'O2': MOLAR_MASS['O2'] / 1000.0})
    flux.update_totals()
    transport = model.estimate_transport_state(flux, melt, effective_transport_capacity=capacity)
    expected = _owner_steady_partials(melt, flux, 'N2', oxygen_mol_hr=1.0)
    assert model.backpressure_partials(flux, melt, transport, oxygen_mol_hr=1.0) == pytest.approx(expected)
    flux_without_o2 = EvaporationFlux(total_kg_hr=0.1, species_kg_hr={'Fe': 0.1})
    assert model.backpressure_partials(flux_without_o2, melt, transport, oxygen_mol_hr=1.0) == pytest.approx(expected)


def test_open_declared_sweep_uses_chamber_total_and_evaporation_reads_own_partial():
    from simulator.evaporation import EvaporationMixin
    from types import SimpleNamespace

    model, melt, flux, _ = _boundary('N2', atmosphere=Atmosphere.PN2_SWEEP)
    melt.sweep_mol_per_s = 0.01
    transport = model.estimate_transport_state(flux, melt)
    assert transport['vapor_pressure_mbar'] < transport['pressure_mbar']
    actual = model.backpressure_partials(flux, melt, transport)
    assert actual == pytest.approx(_owner_steady_partials(melt, flux, 'N2'))
    host = SimpleNamespace(_melt_headspace_composition_mbar=actual)
    assert EvaporationMixin._evaporation_bulk_partial_pressure_pa(host, 'Fe') == pytest.approx(actual['Fe'] * 100.0)


def test_legacy_upper_bound_notice_reaches_snapshot_warning_path():
    from simulator.core import PyrolysisSimulator
    from types import SimpleNamespace

    model, melt, flux, capacity = _boundary('Kr')
    transport = model.estimate_transport_state(flux, melt, effective_transport_capacity=capacity)
    actual = model.backpressure_partials(flux, melt, transport)
    assert actual['Fe'].hex() == '0x1.4666666666666p+3'
    sim = PyrolysisSimulator.__new__(PyrolysisSimulator)
    sim._process_pressure_diagnostic = {}
    sim._overhead_model = model
    sim._condensation_model = SimpleNamespace(last_knudsen_regime_diagnostic={})
    assert model.last_backpressure_notice in sim._latest_knudsen_summary()['warnings']


def test_legacy_notice_is_exported_and_cleared_without_mutating_retained_report():
    from simulator.runner import PyrolysisRun

    run = PyrolysisRun(
        feedstock_id='lunar_mare_low_ti', campaign='C2A', hours=1,
        backend_name='internal-analytical', force_builtin_vapor_pressure=True,
        sio_start_temperature_c=1450.0, allow_fallback_vapor=True,
        allow_unmeasured_alpha_fallback=True,
    )
    session = run._start_session()
    payload = run._run_session(session)
    assert payload['status'] == 'ok'
    notice = payload['per_hour_summary'][0]['vapour_batch_summary']['metadata']['backpressure_notice']
    assert 'underspecified carrier boundary' in notice
    assert 'UPPER BOUND' in notice
    sim = session.simulator
    retained = sim._last_vapour_batch_report
    sim.overhead_model.last_backpressure_notice = None
    sim._make_snapshot()
    assert 'backpressure_notice' not in sim._last_vapour_batch_report['metadata']
    assert retained['metadata']['backpressure_notice'] == notice
