"""Process-pressure admission, independently of downstream oxygen capture."""

from copy import deepcopy

import pytest

from simulator.campaigns import CampaignManager, CampaignPressureSetpointRefusal
from simulator.config import load_config_bundle
from simulator.core import PyrolysisSimulator
from simulator.environment import feedstock_body, feedstock_surface_pressure_mbar
from simulator.melt_backend.base import InternalAnalyticalBackend
from simulator.pumping_cost import process_pressure_state
from simulator.state import CampaignPhase, MeltState


HARDWARE = dict(inlet_temperature_K=300.0, max_pump_speed_m3_s=50.0,
                validated_line_conductance_m3_s=50.0)


@pytest.mark.parametrize('campaign', [
    CampaignPhase.C2A, CampaignPhase.C2A_STAGED,
    CampaignPhase.C3_NA, CampaignPhase.C3_K, CampaignPhase.C4,
])
@pytest.mark.parametrize('body,configured,override,expected', [
    ('mars', 'pN2', None, 'Ar'),
    ('mars', 'pCO2', None, 'CO2'), ('mars', 'pN2', 'pCO2', 'CO2'),
    ('mars', 'pN2', 'pN2', 'N2'),
])
def test_mars_carrier_default_and_operator_choice(campaign, body, configured, override, expected):
    setpoints = deepcopy(load_config_bundle().setpoints)
    key = CampaignManager(setpoints)._campaign_config_key(campaign)
    setpoints['campaigns'][key]['carrier_gas'] = configured
    manager = CampaignManager(setpoints)
    if override is not None:
        manager.overrides[campaign.name] = {'carrier_gas': override}
    melt = MeltState(body=body)
    manager.configure_campaign(melt, campaign)
    assert melt.background_gas_species == expected


def mars_sim(*, pressure_mbar=6.0, pumping=None):
    bundle = load_config_bundle()
    setpoints = deepcopy(bundle.setpoints)
    setpoints['campaigns']['C2A_continuous']['p_total_mbar_default'] = pressure_mbar
    if pumping is not None:
        setpoints.setdefault('overhead_headspace', {})['pumping'] = pumping
    sim = PyrolysisSimulator(InternalAnalyticalBackend(), setpoints,
                            bundle.feedstocks, bundle.vapor_pressures)
    sim.load_batch('mars_basalt', mass_kg=1000.0, additives_kg={'C': 30.0})
    sim.start_campaign(CampaignPhase.C2A)
    return sim


def test_all_shipped_mars_feedstocks_declare_origin_and_operator_override_wins():
    for key, fs in load_config_bundle().feedstocks.items():
        if key.startswith('mars_'):
            assert feedstock_body(fs) == 'mars'
            moved = {**fs, 'body': 'moon', 'surface_pressure_mbar': 0.0}
            assert feedstock_body(moved) == 'moon'
            assert feedstock_surface_pressure_mbar(moved) == 0.0


def test_ambient_needs_no_vacuum_hardware():
    state = process_pressure_state(
        requested_pressure_pa=600.0, ambient_pressure_pa=600.0,
        chamber_mol_per_s=1.0, chamber_temperature_K=1800.0, pumping={},
    )
    assert state['status'] == 'ok'
    assert state['achieved_pressure_pa'] == 600.0


@pytest.mark.parametrize('field', tuple(HARDWARE))
def test_subambient_requires_every_declared_hardware_input(field):
    pumping = {k: v for k, v in HARDWARE.items() if k != field}
    state = process_pressure_state(
        requested_pressure_pa=100.0, ambient_pressure_pa=600.0,
        chamber_mol_per_s=1.0, chamber_temperature_K=500.0, pumping=pumping,
    )
    assert state['status'] == 'missing_input'
    assert state['reason'] == f'overhead_headspace.pumping.{field}'
    assert state['achieved_pressure_pa'] is None


def test_series_speed_floor_includes_all_chamber_gas():
    state = process_pressure_state(
        requested_pressure_pa=0.1, ambient_pressure_pa=600.0,
        chamber_mol_per_s=1.0, chamber_temperature_K=500.0, pumping=HARDWARE,
    )
    # Ideal-gas throughput example in the reviewed design: 1 mol/s, 500 K,
    # 50 m3/s pump and line in series => 25 m3/s and 166.28925236 Pa.
    assert state['status'] == 'infeasible'
    assert state['effective_speed_ceiling_m3_s'] == 25.0
    assert state['achieved_pressure_pa'] == pytest.approx(166.28925236)


def test_b987_refuses_before_extraction_with_achieved_pressure():
    sim = mars_sim(pressure_mbar=0.001, pumping=HARDWARE)
    sim.atom_ledger.load_external_mol(
        'process.overhead_gas', {'O2': 3600.0}, source='pressure test load',
        material_origin='feedstock',
    )
    transitions = len(sim.atom_ledger.transitions)
    with pytest.raises(CampaignPressureSetpointRefusal) as exc:
        sim.step()
    assert exc.value.diagnostic['requested_pressure_pa'] == 0.1
    assert exc.value.diagnostic['achieved_pressure_pa'] > 0.1
    assert len(sim.atom_ledger.transitions) == transitions
    assert sim._poisoned_hour is None
    from simulator.pumping_cost import pumping_context_from_sim
    from simulator.cost_ledger import run_pumping_input_cost
    _, cost = run_pumping_input_cost(pumping_context_from_sim(sim, ()))
    assert cost['feasible'] is False


def test_undeclared_pump_refuses_before_any_extraction():
    sim = mars_sim(pressure_mbar=0.001)
    transitions = len(sim.atom_ledger.transitions)
    with pytest.raises(CampaignPressureSetpointRefusal, match='missing_input'):
        sim.step()
    assert len(sim.atom_ledger.transitions) == transitions


def test_pumped_redox_total_pressure_does_not_reapply_mars_ambient():
    sim = mars_sim(pressure_mbar=0.001, pumping=HARDWARE)
    assert sim._melt_redox_fO2_upper_bound_log() == pytest.approx(-6.0)


def test_post_bleed_pressure_excess_rolls_back_the_whole_hour(monkeypatch):
    sim = mars_sim()
    update = sim.overhead_model.update

    def retain_gas(*args, **kwargs):
        gas = update(*args, **kwargs)
        gas.pressure_mbar = 694323.5
        return gas

    monkeypatch.setattr(sim.overhead_model, 'update', retain_gas)
    before = sim.atom_ledger.mol_by_account()
    with pytest.raises(CampaignPressureSetpointRefusal) as exc:
        sim.step()
    assert exc.value.diagnostic['achieved_pressure_pa'] >= 69432350.0
    assert sim.atom_ledger.mol_by_account() == before
    assert sim.melt.hour == 0
    assert sim._poisoned_hour is None
