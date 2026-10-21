"""Physical controls for a continuous well-mixed campaign sweep."""

import math

import pytest

from simulator.overhead import continuous_sweep_balance, sweep_boundary_partials_bar
from simulator.campaigns import CampaignManager, CampaignPressureSetpointRefusal
from simulator.config import load_config_bundle
from simulator.recipe import RecipePatch, RecipeSchema
from simulator.state import CampaignPhase, GAS_CONSTANT, MeltState
from tests.test_mars_process_pressure import mars_sim


def balance(initial, generated=None, flow=1.0, duration=1.0):
    # Choose PV/RT=1 mol, so one mol/s for one second is one residence time.
    return continuous_sweep_balance(
        initial, generated or {}, carrier="Ar", sweep_mol_per_s=flow,
        dt_s=duration, pressure_bar=GAS_CONSTANT * 300.0 / 1e5,
        volume_m3=1.0, temperature_K=300.0,
    )


def test_one_residence_time_removes_one_minus_exp_minus_one_oxygen():
    result = balance({"O2": 1.0})
    assert result["final_species_mol"]["O2"] == pytest.approx(math.exp(-1.0))
    assert result["outflow_species_mol"]["O2"] == pytest.approx(1.0 - math.exp(-1.0))
    assert sum(result["final_species_mol"].values()) == pytest.approx(1.0)
    assert sum(result["outflow_species_mol"].values()) == pytest.approx(1.0)


@pytest.mark.parametrize("initial,generated,flow", [({}, {}, 0.0), ({"O2": 2.0}, {}, 0.0),
    ({"O2": 0.5}, {"O2": 2.0, "CO2": 0.4}, 3.0)])
def test_integrated_species_balance_with_startup_and_generation(initial, generated, flow):
    result = balance(initial, generated, flow)
    for species in initial.keys() | generated.keys() | {"Ar"}:
        assert (initial.get(species, 0.0) + generated.get(species, 0.0)
                + result["inlet_species_mol"].get(species, 0.0)) == pytest.approx(
            result["final_species_mol"].get(species, 0.0)
            + result["outflow_species_mol"].get(species, 0.0))
    assert sum(result["final_species_mol"].values()) == pytest.approx(1.0)


def test_larger_flow_lowers_physical_oxygen_without_creating_a_floor():
    slow = balance({"Ar": 1.0}, {"O2": 1.0}, flow=1.0)
    fast = balance({"Ar": 1.0}, {"O2": 1.0}, flow=10.0)
    assert 0.0 < fast["final_species_mol"]["O2"] < slow["final_species_mol"]["O2"]
    assert balance({"Ar": 1.0}, flow=0.0)["final_species_mol"].get("O2", 0.0) == 0.0


def test_co2_projection_normalizes_final_pressure_without_changing_mole_bins():
    inventory = {"CO2": 1.0, "Ar": 0.2}
    boundary = sweep_boundary_partials_bar(inventory, pressure_bar=0.006, temperature_K=1800.0)
    assert sum(boundary["partials_bar"].values()) == pytest.approx(0.006)
    assert boundary["partials_bar"]["O2"] > 0.0
    assert inventory == {"CO2": 1.0, "Ar": 0.2}


def test_missing_mars_flow_refuses_and_zero_is_a_real_control():
    sim = mars_sim(sweep=None)
    before = sim.atom_ledger.mol_by_account()
    with pytest.raises(CampaignPressureSetpointRefusal) as exc:
        sim.step()
    assert exc.value.diagnostic["detail"] == "campaigns.C2A_continuous.sweep_mol_per_m2_s"
    assert sim.atom_ledger.mol_by_account() == before
    assert mars_sim(sweep=0.0).melt.sweep_mol_per_s == 0.0


def test_raw_zero_oxygen_remains_separate_from_vapor_guard():
    sim = mars_sim(sweep=1.0)
    assert sim._headspace_transport_pO2_bar() == 0.0
    sim._refresh_oxygen_reservoir_transport_pO2_for_vapor()
    assert sim.melt.oxygen_reservoir.headspace_transport_pO2_bar == 0.0
    assert sim._vapor_pressure_dispatch_pO2_bar() > 0.0


def test_campaign_and_stage_flow_recipe_identity():
    schema = RecipeSchema()
    path = ("campaigns", "C2A_continuous", "sweep_mol_per_m2_s")
    slow, fast = RecipePatch({path: 0.01}), RecipePatch({path: 0.1})
    assert slow.recipe_id(schema) != fast.recipe_id(schema)
    stage = RecipePatch.from_nested({"campaigns": {"C2A_staged": {"stages": [
        {"name": "alkali_early_fe", "sweep_mol_per_m2_s": 0.3},
    ]}}})
    patch = schema.to_setpoints_patch(stage)
    assert isinstance(patch["campaigns"]["C2A_staged"]["stages"], list)
    assert patch["campaigns"]["C2A_staged"]["stages"][0]["sweep_mol_per_m2_s"] == 0.3


def test_campaign_flow_runtime_override_wins_and_zero_stage_is_preserved():
    manager = CampaignManager(load_config_bundle().setpoints)
    assert manager._campaign_sweep_flow(CampaignPhase.C2A_STAGED, {"sweep_mol_per_m2_s": 0.0}) == 0.0
    melt = MeltState()
    manager.overrides['C2A'] = {"sweep_mol_per_m2_s": 0.4}
    manager.configure_campaign(melt, CampaignPhase.C2A)
    assert melt.sweep_mol_per_s == pytest.approx(0.4 * melt.melt_surface_area_m2)


def test_sweep_source_split_distinguishes_consumption_then_replacement():
    from simulator.accounting import AtomLedger, LedgerTransition
    from simulator.overhead import sweep_source_split

    ledger = AtomLedger()
    consumed = LedgerTransition("consume", (ledger.debit_mol("process.overhead_gas", {"O2": 1.0}),),
                               (ledger.credit_mol("terminal.offgas", {"O2": 1.0}),))
    produced = LedgerTransition("produce", (ledger.debit_mol("reservoir.reagent.O2", {"O2": 1.0}),),
                               (ledger.credit_mol("process.overhead_gas", {"O2": 1.0}),))
    old, generated, _, _ = sweep_source_split({"O2": 1.0}, [consumed, produced], ledger.registry)
    assert old["O2"] == 0.0
    assert generated["O2"] == 1.0


def test_zero_flow_retained_carrier_is_not_recurring_pump_load():
    sim = mars_sim(sweep=0.0, pressure_mbar=5.0, pumping={
        "inlet_temperature_K": 300.0, "max_pump_speed_m3_s": 1e-12,
        "validated_line_conductance_m3_s": 1e-12,
    })
    amount = sim._headspace_o2_mol_for_pO2_bar(0.005)
    sim.atom_ledger.load_external_mol("process.overhead_gas", {"Ar": amount},
                                    material_origin="reagent", source="existing sealed inventory")
    sim._admit_process_pressure()
    assert sim._process_pressure_diagnostic["chamber_mol_per_s"] == 0.0


def test_sweep_refusal_rolls_back_delivered_carrier(monkeypatch):
    sim = mars_sim(sweep=0.1)
    from simulator.chemistry.kernel import ChemistryIntent, IntentResult

    dispatch = sim._dispatch_and_commit
    def refuse(intent, **kwargs):
        if intent is ChemistryIntent.OVERHEAD_BLEED:
            return IntentResult(intent=intent, status="unsupported", diagnostic={"reason": "capacity rejected"})
        return dispatch(intent, **kwargs)
    monkeypatch.setattr(sim, "_dispatch_and_commit", refuse)
    before = sim.atom_ledger.mol_by_account()
    external = len(sim.atom_ledger.external_loads)
    with pytest.raises(CampaignPressureSetpointRefusal):
        sim.step()
    assert sim.atom_ledger.mol_by_account() == before
    assert len(sim.atom_ledger.external_loads) == external
    assert sim.melt.hour == 0


def test_integrated_outlet_rounding_does_not_overfill_pressure_endpoint():
    result = continuous_sweep_balance(
        {}, {"O2": 1.0, "Na": 30.0}, carrier="Ar", sweep_mol_per_s=0.001,
        dt_s=3600.0, pressure_bar=0.006, volume_m3=0.1, temperature_K=1807.5,
    )
    available = {"O2": 1.0, "Na": 30.0, "Ar": result["inlet_species_mol"]["Ar"]}
    final = {s: n - result["outflow_species_mol"][s] for s, n in available.items()}
    from simulator.overhead import OverheadGasModel
    assert sum(OverheadGasModel._compute_partial_pressures(final, 0.1, 1807.5).values()) <= 0.006


def test_steady_carrier_flow_has_exactly_zero_net_chamber_transfer():
    result = balance({"Ar": 1.0}, flow=3.6)
    delivered = result["inlet_species_mol"]["Ar"]
    outflow = result["outflow_species_mol"]["Ar"]
    assert outflow == delivered
    assert 1.0 + (delivered - outflow) == 1.0


def test_sweep_knob_changes_existing_evalspec_recipe_identity_only():
    from dataclasses import replace
    from simulator.optimize.evalspec import EvalSpec, cache_key, canonical_evalspec_json
    schema = RecipeSchema()
    path = ("campaigns", "C2A_continuous", "sweep_mol_per_m2_s")
    slow, fast = RecipePatch({path: 0.001}), RecipePatch({path: 0.01})
    before = EvalSpec(recipe_id=slow.recipe_id(schema), feedstock_recipe_digest="same",
                      feedstock_id="mars_basalt", profile_id="same", fidelity="fast",
                      code_version="same", data_digests={})
    after = replace(before, recipe_id=fast.recipe_id(schema))
    assert cache_key(before) != cache_key(after)
    import json
    left, right = json.loads(canonical_evalspec_json(before)), json.loads(canonical_evalspec_json(after))
    assert {k for k in left if left[k] != right[k]} == {"recipe_id"}


def test_co_supplied_with_co2_requires_the_reactive_boundary():
    from simulator.overhead import OverheadConfigurationError
    with pytest.raises(OverheadConfigurationError, match="missing_input: reactive"):
        sweep_boundary_partials_bar({"CO2": 1.0, "CO": 0.1}, pressure_bar=0.006, temperature_K=1800.0)


@pytest.mark.parametrize("body,ambient", [("mars", 6.0), ("moon", 0.0)])
def test_cold_train_retention_refuses_incompatible_sweep_pressure(monkeypatch, body, ambient):
    from types import SimpleNamespace
    from simulator.thermal_train import FiniteCapacity
    sim = mars_sim(sweep=0.001)
    sim.melt.body, sim.melt.ambient_pressure_mbar = body, ambient
    sim.atom_ledger.load_external_mol("process.overhead_gas", {"O2": 1.0},
                                    source="retained oxygen", material_origin="feedstock")
    monkeypatch.setattr(sim, "_apply_oxygen_reservoir_exchange", lambda: None)
    cold = SimpleNamespace(accumulator_enabled=False, relief={
        "k_relief_kg_hr_Pa": 1e-9, "p_open_Pa": 1e9, "vessel_rating_Pa": 1e10,
    })
    monkeypatch.setattr(sim, "_cold_train_capacity_policy", lambda: (FiniteCapacity(1e-6), cold))
    before = sim.atom_ledger.mol_by_account()
    with pytest.raises(CampaignPressureSetpointRefusal) as exc:
        sim.step()
    assert exc.value.diagnostic["achieved_pressure_pa"] > 600.0
    assert sim.atom_ledger.mol_by_account() == before
    assert sim.melt.hour == 0


@pytest.mark.parametrize("body", ["moon", "asteroid", "mars"])
@pytest.mark.parametrize("campaign", [p for p in CampaignPhase if p not in {CampaignPhase.IDLE, CampaignPhase.COMPLETE}])
def test_common_sweep_control_is_applied_for_every_body_and_campaign(body, campaign):
    manager = CampaignManager(load_config_bundle().setpoints)
    manager.overrides[campaign.name] = {"sweep_mol_per_m2_s": 0.25}
    melt = MeltState(body=body)
    manager.configure_campaign(melt, campaign)
    assert melt.sweep_mol_per_s == pytest.approx(0.25 * melt.melt_surface_area_m2)
    key = manager._campaign_config_key(campaign)
    assert RecipeSchema().spec_for(("campaigns", key, "sweep_mol_per_m2_s")).units == "mol/m2/s"


@pytest.mark.parametrize("body", ["moon", "asteroid"])
def test_undeclared_non_mars_sweep_keeps_legacy_metering(body):
    manager = CampaignManager(load_config_bundle().setpoints)
    melt = MeltState(body=body)
    manager.configure_campaign(melt, CampaignPhase.C2A)
    assert melt.sweep_mol_per_s is None
    assert melt.background_gas_species == "N2"


@pytest.mark.parametrize("carrier", ["He", "Kr"])
def test_common_sweep_uses_existing_supported_carriers(carrier):
    result = continuous_sweep_balance({carrier: 1.0}, {}, carrier=carrier,
        sweep_mol_per_s=3.6, dt_s=1.0, pressure_bar=GAS_CONSTANT * 300.0 / 1e5,
        volume_m3=1.0, temperature_K=300.0)
    assert result["inlet_species_mol"][carrier] == result["outflow_species_mol"][carrier]


def test_declared_non_mars_flow_executes_the_same_physical_balance():
    sim = mars_sim(sweep=0.001)
    sim.melt.body, sim.melt.ambient_pressure_mbar = "moon", 0.0
    snapshot = sim.step()
    assert snapshot.mass_balance_error_pct < 5e-12
    assert snapshot.overhead.composition["Ar"] == pytest.approx(6.0)
    assert snapshot.oxygen_reservoir["headspace_transport_pO2_bar"] == 0.0
    assert sim.atom_ledger.mol_by_account("reservoir.reagent.sweep_gas") == {}


@pytest.mark.parametrize('flow', [0.0, 0.001])
def test_fifteen_mbar_isothermal_startup_is_attainable(monkeypatch, flow):
    sim = mars_sim(pressure_mbar=15.0, sweep=flow)
    sim._overhead_headspace_config['volume_m3'] = 0.1
    sim.melt.temperature_C += 500.0 - sim._headspace_temperature_K()
    monkeypatch.setattr(sim, '_update_temperature', lambda: None)
    sim.step()
    assert sim.melt.hour == 1
    assert sim._process_pressure_diagnostic['status'] == 'ok'
    assert sim.overhead.pressure_mbar == pytest.approx(15.0, rel=4 * math.ulp(1.0))


@pytest.mark.parametrize('initial_flow', [None, 0.0])
def test_live_continuous_campaign_flow_adjustment_is_applied(initial_flow):
    from simulator.runner import PyrolysisRun
    from simulator.session import SimSession

    patch = ({'campaigns': {'C2A_continuous': {'sweep_mol_per_m2_s': initial_flow}}}
             if initial_flow is not None else {})
    run = PyrolysisRun(feedstock_id='mars_basalt', campaign='C2A', hours=0,
                      additives_kg={'C': 30.0}, setpoints_patch=patch)
    session = SimSession().start(run._session_config())
    session.adjust('campaign_override', 0.001, campaign='C2A', field='sweep_mol_per_m2_s')
    sim = session.simulator
    assert sim.melt.sweep_mol_per_s == pytest.approx(0.001 * sim.melt.melt_surface_area_m2)
    sim.step()
    assert sim.melt.hour == 1
    session.adjust('campaign_override', 0.002, campaign='C2A', field='sweep_mol_per_m2_s')
    assert sim.melt.sweep_mol_per_s == pytest.approx(0.002 * sim.melt.melt_surface_area_m2)
    sim.step()
    assert sim.melt.hour == 2


@pytest.mark.parametrize('external_initial,external_generated,expected_external', [
    (0.0, 1.0, 0.5676676416183064),
    (1.0, 0.0, 0.8646647167633873),
])
def test_continuous_outlet_separates_old_and_new_oxygen_sources(
    external_initial, external_generated, expected_external,
):
    result = continuous_sweep_balance({'O2': 1.0}, {'O2': 1.0}, carrier='Ar',
        sweep_mol_per_s=1.0, dt_s=1.0, pressure_bar=GAS_CONSTANT * 300.0 / 1e5,
        volume_m3=1.0, temperature_K=300.0,
        initial_external_o2_mol=external_initial, generated_external_o2_mol=external_generated)
    assert result['external_o2_outflow_mol'] == pytest.approx(expected_external, rel=1e-14)
    assert result['outflow_species_mol']['O2'] == pytest.approx(1.4323323583816936, rel=1e-14)


def test_source_split_keeps_external_classes_through_consumption_and_replacement():
    from simulator.accounting import AtomLedger, LedgerTransition
    from simulator.overhead import sweep_source_split
    from engines.builtin.oxygen_bubbler import TRANSITION_NAME

    ledger = AtomLedger()
    external = LedgerTransition(TRANSITION_NAME,
        (ledger.debit_mol('reservoir.fo2_buffer', {'O2': 1.0}),),
        (ledger.credit_mol('process.overhead_gas', {'O2': 1.0}),))
    consumed = LedgerTransition('consume',
        (ledger.debit_mol('process.overhead_gas', {'O2': 1.0}),),
        (ledger.credit_mol('terminal.offgas', {'O2': 1.0}),))
    melt_source = LedgerTransition('melt_source',
        (ledger.debit_mol('reservoir.reagent.O2', {'O2': 1.0}),),
        (ledger.credit_mol('process.overhead_gas', {'O2': 1.0}),))
    old, generated, external_old, external_new = sweep_source_split(
        {'O2': 1.0}, [external, consumed, melt_source], ledger.registry,
        initial_external_o2_mol=0.0)
    assert old['O2'] == 0.5
    assert generated['O2'] == 1.5
    assert external_old == 0.0
    assert external_new == 0.5


@pytest.mark.parametrize('old_external', [False, True])
def test_runtime_sweep_routes_source_integrals_and_retains_external_inventory(monkeypatch, old_external):
    from simulator.chemistry.kernel import ChemistryIntent
    from simulator.accounting import LedgerTransition

    # PV/RT=1 mol and one hour: one mol delivered + one generated gives
    # two residence times. These published ODE integrals are independent
    # of the production transport helper (review-of-record R1).
    sim = mars_sim(pressure_mbar=GAS_CONSTANT * 500.0 / 0.1 / 100.0, sweep=1.0 / 3600.0)
    sim._overhead_headspace_config['volume_m3'] = 0.1
    sim.melt.temperature_C += 500.0 - sim._headspace_temperature_K()
    monkeypatch.setattr(sim, '_update_temperature', lambda: None)
    monkeypatch.setattr(sim, '_apply_oxygen_reservoir_exchange', lambda: None)
    sim.atom_ledger.load_external_mol('process.overhead_gas', {'O2': 1.0},
                                    source='old O2', material_origin='reagent' if old_external else 'feedstock')
    sim._o2_bubbler_external_o2_in_overhead_mol = float(old_external)
    # The ledger requires an explicitly mixed pool for partial withdrawals
    # of shared-element origins; source-species attribution is independent.
    sim.atom_ledger.mark_amalgamated_pool('process.overhead_gas', ['O'])
    def source():
        if old_external:
            sim.atom_ledger.load_external_mol('reservoir.reagent.O2', {'O2': 1.0},
                                            source='new melt proxy', material_origin='feedstock')
            sim.atom_ledger.apply(LedgerTransition('new_melt_oxygen',
                (sim.atom_ledger.debit_mol('reservoir.reagent.O2', {'O2': 1.0}),),
                (sim.atom_ledger.credit_mol('process.overhead_gas', {'O2': 1.0}),)))
        else:
            sim.atom_ledger.load_external_mol('reservoir.fo2_buffer', {'O2': 1.0},
                                            source='new external O2', material_origin='reagent')
            sim._dispatch_and_commit(ChemistryIntent.OXYGEN_BUBBLER, control_inputs={
                'passthrough_mol': 1.0, 'injected_mol': 1.0, 'absorbed_mol': 0.0,
            })
    monkeypatch.setattr(sim, '_apply_o2_bubbler', source)
    sim.step()
    expected_external = 0.8646647167633873 if old_external else 0.5676676416183064
    expected_melt = 0.5676676416183064 if old_external else 0.8646647167633873
    assert sim.atom_ledger.mol_by_account('terminal.oxygen_melt_offgas_stored')['O2'] == pytest.approx(expected_melt, rel=1e-13)
    assert sim.atom_ledger.mol_by_account('terminal.oxygen_bubbler_external_vented_to_vacuum')['O2'] == pytest.approx(expected_external, rel=1e-13)
    assert sim._o2_bubbler_external_o2_overhead_mol() == pytest.approx(1.0 - expected_external, rel=1e-13)


@pytest.mark.parametrize('flow', [0.001, 1.0, 10.0])
@pytest.mark.parametrize('initial_o2', [0.0, 1.0])
def test_wholly_external_oxygen_cannot_become_melt_product_by_rounding(flow, initial_o2):
    result = continuous_sweep_balance({'O2': initial_o2}, {'O2': 1.0}, carrier='Ar',
        sweep_mol_per_s=flow, dt_s=1.0, pressure_bar=GAS_CONSTANT * 300.0 / 1e5,
        volume_m3=1.0, temperature_K=300.0,
        initial_external_o2_mol=initial_o2, generated_external_o2_mol=1.0)
    assert result['external_o2_outflow_mol'] == result['outflow_species_mol']['O2']


@pytest.mark.parametrize('mass_kg', [100.0, 1000.0])
def test_intensive_sweep_uses_designer_area_with_explicit_headspace(mass_kg):
    from simulator.runner import PyrolysisRun
    from simulator.session import SimSession
    from simulator.equipment import EquipmentDesigner

    run = PyrolysisRun(feedstock_id='mars_basalt', campaign='C2A', hours=0,
                      mass_kg=mass_kg, additives_kg={'C': 0.03 * mass_kg},
                      setpoints_patch={
                          'overhead_headspace': {'volume_m3': 0.1},
                          'campaigns': {'C2A_continuous': {'sweep_mol_per_m2_s': 2.0}},
                      })
    sim = SimSession().start(run._session_config()).simulator
    area = EquipmentDesigner(sim.setpoints).size_crucible(mass_kg).melt_surface_area_m2
    assert sim.melt.melt_surface_area_m2 == pytest.approx(area)
    assert sim.melt.sweep_mol_per_s == pytest.approx(2.0 * area)


def test_absolute_sweep_is_no_longer_an_operator_knob():
    with pytest.raises(ValueError):
        RecipeSchema().to_setpoints_patch(RecipePatch({
            ('campaigns', 'C2A_continuous', 'sweep_mol_per_s'): 1.0,
        }))
    manager = CampaignManager(load_config_bundle().setpoints)
    with pytest.raises(ValueError):
        manager._refuse_unknown_override_fields('C2A', {'sweep_mol_per_s': 1.0})


def test_mars_default_is_generic_only_when_explicitly_declared_elsewhere():
    setpoints = load_config_bundle().setpoints
    intensity = setpoints['overhead_headspace']['sweep_mol_per_m2_s_default']
    manager = CampaignManager(setpoints)
    mars, moon = MeltState(body='mars'), MeltState(body='moon')
    manager.configure_campaign(mars, CampaignPhase.C2A)
    manager.configure_campaign(moon, CampaignPhase.C2A)
    assert mars.sweep_mol_per_s == pytest.approx(intensity * mars.melt_surface_area_m2)
    assert moon.sweep_mol_per_s is None
    manager.overrides['C2A'] = {'sweep_mol_per_m2_s': 0.0}
    manager.apply_sweep_flow_control(mars, CampaignPhase.C2A)
    assert mars.sweep_mol_per_s == 0.0


def test_first_live_intensive_flow_sizes_geometry_before_application():
    from simulator.runner import PyrolysisRun
    from simulator.session import SimSession
    from simulator.equipment import EquipmentDesigner

    run = PyrolysisRun(feedstock_id='lunar_mare_low_ti', campaign='C2A', hours=0,
                      mass_kg=100.0, setpoints_patch={'overhead_headspace': {'volume_m3': 0.1}})
    session = SimSession().start(run._session_config())
    sim = session.simulator
    assert sim.melt.sweep_mol_per_s is None
    session.adjust('campaign_override', 2.0, campaign='C2A', field='sweep_mol_per_m2_s')
    area = EquipmentDesigner(sim.setpoints).size_crucible(100.0).melt_surface_area_m2
    assert sim.melt.melt_surface_area_m2 == pytest.approx(area)
    assert sim.melt.sweep_mol_per_s == pytest.approx(2.0 * area)


def test_first_staged_inert_default_activation_sizes_geometry():
    from simulator.runner import PyrolysisRun
    from simulator.session import SimSession
    from simulator.equipment import EquipmentDesigner
    from copy import deepcopy

    stages = deepcopy(load_config_bundle().setpoints['campaigns']['C2A_staged']['stages'])
    stages[0].update(gas_cover_mode='po2_hold', pO2_mbar=1.0)
    run = PyrolysisRun(feedstock_id='mars_basalt', campaign='C2A_staged', hours=0,
                      mass_kg=100.0, additives_kg={'C': 3.0}, setpoints_patch={
                          'overhead_headspace': {'volume_m3': 0.1},
                          'campaigns': {'C2A_staged': {'stages': stages}},
                      })
    sim = SimSession().start(run._session_config()).simulator
    assert sim.melt.sweep_mol_per_s is None
    sim.melt.campaign_hour = int(stages[0]['duration_h'])
    sim.step()
    area = EquipmentDesigner(sim.setpoints).size_crucible(100.0).melt_surface_area_m2
    intensity = sim.setpoints['overhead_headspace']['sweep_mol_per_m2_s_default']
    assert sim.melt.melt_surface_area_m2 == pytest.approx(area)
    assert sim.melt.sweep_mol_per_s == pytest.approx(intensity * area)
