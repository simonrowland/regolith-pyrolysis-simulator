"""Stage-3 topology and conservative silica admission (d-268, chunk 3)."""
from __future__ import annotations

import copy

import pytest

from simulator.campaigns import CampaignManager
from simulator.condensation import (
    CondensationModel, CondensationRouteResult, DepositionInputRefusal, Stage3SilicaExposureRefusal,
)
from simulator.config import load_config_bundle
from simulator.core import PyrolysisSimulator
from simulator.melt_backend.base import InternalAnalyticalBackend
from simulator.recipe import RecipePatch, RecipeSchema, RecipeValidationError
from simulator.state import CampaignPhase, CondensationTrain, EvaporationFlux, MeltState


def _model(route="divert", *, hard_gate=True):
    model = CondensationModel(CondensationTrain.create_default())
    model.hard_stage3_gate = hard_gate
    model.train.collection_route["stage3_route"] = route
    model.configure_operating_conditions(
        overhead_pressure_mbar=1.0,
        species_partial_pressures_mbar={"Na": 0.3, "K": 0.3, "SiO": 0.4},
        stage_area_m2_by_stage={str(i): 1.0 for i in range(8)},
    )
    return model


def _sim(setpoints=None):
    bundle = load_config_bundle()
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    return PyrolysisSimulator(
        backend, setpoints if setpoints is not None else bundle.setpoints,
        bundle.feedstocks, bundle.vapor_pressures,
    )


@pytest.mark.parametrize("missing", [
    ("stage3_product_purity",), ("stage3_instantaneous_purity",),
    ("stage3_product_purity", "stage3_instantaneous_purity"),
])
def test_partial_constructor_inherits_owned_collection_purity(missing):
    defaults = load_config_bundle().setpoints
    setpoints = copy.deepcopy(defaults)
    for field in missing:
        del setpoints["condensation_train"][field]
    sim = _sim(setpoints)
    for field in missing:
        assert sim.train.collection_route[field] == defaults['condensation_train'][field]


def test_divert_excludes_silica_from_stages_budget_walls_supply_and_sampling(monkeypatch):
    import simulator.condensation as owner
    model = _model()
    budget_stages = []
    original_budget = owner._pressure_isolated_capture_budget_kg

    def budget(species, rate, stages, *args, **kwargs):
        budget_stages.extend(stage.stage_number for stage in stages)
        return original_budget(species, rate, stages, *args, **kwargs)

    monkeypatch.setattr(owner, "_pressure_isolated_capture_budget_kg", budget)
    result = model.route(EvaporationFlux({"Na": 1.0, "K": 1.0, "SiO": 1.0}),
                         MeltState(temperature_C=1650))
    assert 3 not in result.condensed_by_stage_species
    assert 3 not in budget_stages
    active = model.active_pipe_segments
    assert [s.name for s in active] == [
        "stage_0_to_stage_1", "stage_1_to_stage_2", "stage_2_to_stage_4_bypass",
        "stage_4_to_stage_5", "stage_5_to_stage_6", "stage_6_to_stage_7",
    ]
    assert all(s.liner_material != "fused_silica_baffles" for s in active)
    assert "stage_2_to_stage_4_bypass" in {
        s.name for s in model._mixed_temperature_wall_candidate_segments("SiO")}
    supplies = model._segment_supply_by_name(1.0, {1: 1.0, 2: 1.0})
    assert set(supplies) == {s.name for s in active}
    assert set(model.wall_species_partial_pressures_pa_by_segment) <= set(supplies)
    assert not any("stage_3" in name for name in result.wall_deposit_by_segment_species)
    for species in ("Na", "K", "SiO"):
        assert (result.condensed_for_species(species)
                + result.wall_deposit_by_species.get(species, 0.0)
                + result.remaining_by_species.get(species, 0.0)
                + result.retained_in_source_by_species.get(species, 0.0)) == pytest.approx(1.0)


def test_open_zero_alkali_sio_window_reaches_stage3(monkeypatch):
    model = _model("through", hard_gate=False)
    evaluated = []
    original = model._condensation_efficiency

    def efficiency(**kwargs):
        evaluated.append((kwargs["species"], kwargs["stage"].stage_number,
                          kwargs["available_kg"]))
        return original(**kwargs)

    monkeypatch.setattr(model, "_condensation_efficiency", efficiency)
    model.route(EvaporationFlux({"SiO": 1.0}), MeltState(temperature_C=1650))
    assert any(sp == "SiO" and stage == 3 and kg > 0 for sp, stage, kg in evaluated)
    assert "stage_2_to_stage_4_bypass" not in {s.name for s in model.active_pipe_segments}


def test_diverted_alkali_sio_numeric_pin():
    """Pre-change pin: the isolated cartridge must preserve existing routing."""
    model = _model("divert", hard_gate=False)
    result = model.route(
        EvaporationFlux({"SiO": 1.0, "Na": 0.0007, "K": 0.0001}),
        MeltState(temperature_C=1650, pO2_mbar=1e-5),
    )
    assert 3 not in result.condensed_by_stage_species
    assert {sp: kg.hex() for sp, kg in result.condensed_by_stage_species[4].items()} == {
        "SiO": "0x1.1ac1c176ddee6p-1",
        "Na": "0x1.d08a5cd1c9aefp-12",
        "K": "0x1.d96ca206ef36dp-15",
    }
    assert {sp: kg.hex() for sp, kg in result.remaining_by_species.items()} == {
        "SiO": "0x1.5e35b5418eb60p-5",
        "Na": "0x1.0d7674e54da9ep-12",
        "K": "0x1.6d6fbb5c992edp-15",
    }


def test_evaporation_overhead_source_numeric_pin():
    """Pin the existing shared source projection before consolidating it."""
    source = _sim()._project_evaporation_overhead_source_mol_hr(
        {"SiO": 1.0, "Na": 0.0007, "K": 0.0001}, {})
    assert {sp: mol.hex() for sp, mol in source.items()} == {'SiO': '0x1.6af1875a0e72ap+4', 'O2': '0x1.6af1875a0e72bp+3', 'Na': '0x1.f2dd8993f4497p-6', 'K': '0x1.4f3cb1669146ep-9'}


@pytest.mark.parametrize("sio_kg,retained,expected_o2", [
    (1.179680414706, 3.86523814998e-9, 13.379915782435),
    (5.133267625024, 3.17585413112e-9, 58.221436632609),
    (5.806568174578, 3.23181618178e-9, 65.858000346813),
])
def test_stage3_includes_reviewed_same_hour_stream_oxygen(sio_kg, retained, expected_o2):
    model = _model("through", hard_gate=False)
    model.retained_vapor_mol = {"O2": retained}
    melt = MeltState(temperature_C=1750)
    melt.oxygen_reservoir.headspace_ledger_pO2_bar = 1e-8
    result = model.route(EvaporationFlux({"SiO": sio_kg, "Na": 0.0007, "K": 0.0001}), melt)
    reactions = result.stage3_reactive_alkali_by_species
    for reaction in reactions.values():
        assert reaction["available_oxygen_mol"] == pytest.approx(expected_o2 + retained, rel=1e-10)
        assert reaction["uptake_mol"] == pytest.approx(reaction["transport_uptake_mol"])
    assert sum(r["uptake_mol"] for r in reactions.values()) > 4 * retained


def test_stage3_coproduct_pressure_uses_measured_vapor_carrier_dilution():
    from simulator.accounting.formulas import resolve_species_formula
    model = _model("through", hard_gate=False)
    rates = {"SiO": 1.0, "Na": 0.0007, "K": 0.0001}
    mol = {sp: kg / resolve_species_formula(sp).molar_mass_kg_per_mol()
           for sp, kg in rates.items()}
    model.configure_operating_conditions(
        overhead_pressure_mbar=10.0,
        species_partial_pressures_mbar={sp: 0.4 * n / mol["SiO"] for sp, n in mol.items()},
    )
    model.retained_vapor_mol = {"O2": 1e-7}
    melt = MeltState(temperature_C=1650)
    melt.oxygen_reservoir.headspace_ledger_pO2_bar = 1e-8
    result = model.route(EvaporationFlux(rates), melt)
    # SiO2 -> SiO + 1/2 O2 gives pO2_source = 0.5*pSiO by Dalton's law,
    # independent of the 10 mbar carrier-gas total; mbar/1000 is bar.
    for reaction in result.stage3_reactive_alkali_by_species.values():
        assert reaction["pO2_bar"] == pytest.approx(1e-8 + 0.5 * 0.4 / 1000)


@pytest.mark.parametrize("oxygen_mol", [1e-7, 1.0])
def test_stage3_shared_oxygen_split_is_order_independent(monkeypatch, oxygen_mol):
    import simulator.evaporation as source_owner
    # Isolate carried-in oxidant competition from the newly evolved stream.
    monkeypatch.setattr(source_owner, "_project_evaporation_overhead_source_mol_hr",
                        lambda *args, **kwargs: {}, raising=False)
    results = []
    for order in (("Na", "K"), ("K", "Na")):
        model = _model("through", hard_gate=False)
        model.retained_vapor_mol = {"O2": oxygen_mol}
        melt = MeltState(temperature_C=1650)
        melt.oxygen_reservoir.headspace_ledger_pO2_bar = 1e-8
        rates = {"SiO": 1.0, **{sp: {"Na": 0.0007, "K": 0.0001}[sp] for sp in order}}
        result = model.route(EvaporationFlux(rates), melt)
        results.append({sp: r["uptake_mol"] for sp, r in result.stage3_reactive_alkali_by_species.items()})
    assert results[0] == pytest.approx(results[1], rel=1e-12)
    if oxygen_mol == 1e-7:
        assert results[0] == pytest.approx({"Na": 3.69003728744e-7, "K": 3.09962712565e-8}, rel=1e-10)
    else:
        assert results[0] == pytest.approx({"Na": 0.0304483264479, "K": 0.00255765595947}, rel=1e-10)


@pytest.mark.parametrize("po2,expected_log,favoured", [
    (1e-8, -38.8286945826, True),
    (1.06560249466e-25, 0.251711636707, False),
])
def test_stage3_na_gate_matches_liquid_activity_reference(monkeypatch, po2, expected_log, favoured):
    import simulator.evaporation as source_owner
    monkeypatch.setattr(source_owner, "_project_evaporation_overhead_source_mol_hr",
                        lambda *args, **kwargs: {})
    model = _model("through", hard_gate=False)
    model.retained_vapor_mol = {"O2": 1.0}
    melt = MeltState(temperature_C=1650)
    # Geometric midpoint of the review's crystal and liquid critical pO2.
    melt.oxygen_reservoir.headspace_ledger_pO2_bar = po2
    result = model.route(EvaporationFlux({"SiO": 1.0, "Na": 0.0007, "K": 0.0001}), melt)
    reaction = result.stage3_reactive_alkali_by_species["Na"]
    assert reaction["log_Q_over_K"] == pytest.approx(expected_log, abs=1e-9)
    assert reaction["favoured"] is favoured


def test_stage3_k_evaluation_flags_missing_liquid_reference():
    model = _model("through", hard_gate=False)
    result = model.route(EvaporationFlux({"SiO": 1.0, "Na": 0.0007, "K": 0.0001}), MeltState())
    notice = result.stage3_reactive_alkali_by_species["K"]["reference_state_notice"]
    assert notice["status"] == "authority_extrapolated"
    assert notice["message"] == ("reference-state mismatch: pure-liquid KO0.5 activity vs crystalline K2O dG; "
                                 "no K2O fusion data; authority extrapolated")


@pytest.mark.parametrize("order", [("SiO", "Na", "K"), ("Na", "K", "SiO")])
def test_same_hour_zero_retained_oxygen_kernel_capture_closes(order, monkeypatch):
    from simulator.accounting.formulas import resolve_species_formula
    sim = _sim()
    sim.load_batch("lunar_mare_low_ti", mass_kg=1000.0)
    sim.campaign_mgr.overrides["C0"] = {"stage3_route": "through"}
    model = _model("through", hard_gate=False)
    model.train = sim.train
    sim._condensation_model = model
    sim.melt.oxygen_reservoir.headspace_ledger_pO2_bar = 0.0
    assert not sim.atom_ledger.physical_mol_by_account("process.overhead_gas").get("O2", 0)
    rates = {sp: {"SiO": 1.0, "Na": 0.0007, "K": 0.0001}[sp] for sp in order}
    from dataclasses import replace
    original_route = model.route

    def force_commit_order(flux, melt):
        return replace(original_route(flux, melt), wall_route_species_order=order)

    monkeypatch.setattr(model, "route", force_commit_order)
    first = len(sim.atom_ledger.transitions)
    sim._route_to_condensation(EvaporationFlux(rates))
    transitions = sim.atom_ledger.transitions[first:]
    for transition in transitions:
        transition.validate_conservation(sim.species_formula_registry)
    stage3 = sim.train.stages[3].collected_kg
    oxide_mol = sum(stage3.get(sp, 0) / resolve_species_formula(sp).molar_mass_kg_per_mol()
                    for sp in ("Na2O", "K2O"))
    assert oxide_mol > 0
    # SiO2 -> SiO + 1/2 O2 is the sole gas-side oxygen source here.
    expected_source = 0.5 / resolve_species_formula("SiO").molar_mass_kg_per_mol()
    oxygen_left = sim.atom_ledger.physical_mol_by_account("process.overhead_gas").get("O2", 0)
    assert oxygen_left + 0.5 * oxide_mol == pytest.approx(expected_source, rel=1e-12)


@pytest.mark.parametrize("oxygen_mol", [1.0, 1e-7, 0.0])
def test_admitted_alkali_parcel_requires_reactive_stage3_uptake(oxygen_mol, monkeypatch):
    import simulator.evaporation as source_owner
    monkeypatch.setattr(source_owner, "_project_evaporation_overhead_source_mol_hr",
                        lambda *args, **kwargs: {})
    from simulator.accounting.formulas import resolve_species_formula

    model = _model("through", hard_gate=False)
    model.retained_vapor_mol = {"O2": oxygen_mol}
    # A zero incoming-sweep command does not make retained headspace O2 vanish.
    melt = MeltState(temperature_C=1650, pO2_mbar=0.0)
    melt.oxygen_reservoir.headspace_ledger_pO2_bar = 1e-8 if oxygen_mol else 0.0
    result = model.route(
        EvaporationFlux({"SiO": 1.0, "Na": 0.0007, "K": 0.0001}),
        melt,
    )
    assert model.train.collection_route["stage3_route"] == "through"
    assert model.last_stage3_route_diagnostic["capture_purity"] >= 0.98
    stage3 = result.condensed_by_stage_species.get(3, {})
    uptake_mol = {
        sp: stage3.get(sp, 0.0) / resolve_species_formula(sp).molar_mass_kg_per_mol()
        for sp in ("Na", "K")
    }
    if oxygen_mol == 0.0:
        assert sum(uptake_mol.values()) == 0.0
    else:
        assert sum(uptake_mol.values()) > 0.0
        if oxygen_mol == 1.0:
            assert all(mol > 0.0 for mol in uptake_mol.values())
        # 4 M + O2 -> 2 M2O: one mol O2 can bind four mol alkali atoms.
        assert sum(uptake_mol.values()) <= 4.0 * oxygen_mol
        if oxygen_mol == 1e-7:
            assert sum(uptake_mol.values()) == pytest.approx(4.0 * oxygen_mol)


def test_vanishing_oxygen_pressure_disables_reaction_despite_available_oxidant(monkeypatch):
    import simulator.evaporation as source_owner
    monkeypatch.setattr(source_owner, "_project_evaporation_overhead_source_mol_hr",
                        lambda *args, **kwargs: {})
    model = _model("through", hard_gate=False)
    model.retained_vapor_mol = {"O2": 1.0}
    melt = MeltState(temperature_C=1650, pO2_mbar=1e-5)
    melt.oxygen_reservoir.headspace_ledger_pO2_bar = 1e-203
    result = model.route(
        EvaporationFlux({"SiO": 1.0, "Na": 0.0007, "K": 0.0001}),
        melt,
    )
    assert all(not r['favoured'] and r['log_Q_over_K'] > 0.0
               for r in result.stage3_reactive_alkali_by_species.values())
    assert result.condensed_by_stage_species[3].get('Na', 0.0) == 0.0
    assert result.condensed_by_stage_species[3].get('K', 0.0) == 0.0


@pytest.mark.parametrize('oxygen_mol', [1.0, 1e-7, 0.0])
def test_reactive_stage3_kernel_commit_closes_atoms_and_projects_oxide(oxygen_mol, monkeypatch):
    import simulator.evaporation as source_owner
    monkeypatch.setattr(source_owner, "_project_evaporation_overhead_source_mol_hr",
                        lambda *args, **kwargs: {})
    from simulator.accounting.formulas import resolve_species_formula
    from simulator.condensation import stage_purity_report

    sim = _sim()
    sim.load_batch('lunar_mare_low_ti', mass_kg=1.0)
    sim.campaign_mgr.overrides['C0'] = {'stage3_route': 'through'}
    sim.atom_ledger.load_external_mol('process.overhead_gas', {'O2': oxygen_mol},
                                      material_origin='reagent')
    model = _model('through', hard_gate=False)
    model.train = sim.train
    sim._condensation_model = model
    sim.melt.pO2_mbar = 0.0
    sim.melt.oxygen_reservoir.headspace_ledger_pO2_bar = 1e-8 if oxygen_mol else 0.0
    model = sim.condensation_model
    flux = EvaporationFlux({'SiO': 1.0, 'Na': 0.0007, 'K': 0.0001})
    result = model.route(flux, sim.melt)
    bypass = _model('divert', hard_gate=False).route(flux, sim.melt)
    assert sim.train.collection_route['stage3_route'] == 'through'
    registry = sim.species_formula_registry
    sim.atom_ledger.load_external_mol('process.overhead_gas', {
        sp: kg / resolve_species_formula(sp, registry).molar_mass_kg_per_mol()
        for sp, kg in flux.species_kg_hr.items()}, material_origin='reagent')
    first_transition = len(sim.atom_ledger.transitions)
    for sp, rate in flux.species_kg_hr.items():
        metadata = dict(sim.vapor_pressures['metals'].get(sp)
                        or sim.vapor_pressures['oxide_vapors'].get(sp) or {})
        credited = sim._dispatch_condensation_route(
            sp, rate - result.remaining_by_species[sp], metadata, result)
        sim._project_condensed_stage_collection(
            result, sp, credited, sim._condensed_products_kg(sp, credited, metadata))
    for transition in sim.atom_ledger.transitions[first_transition:]:
        transition.validate_conservation(registry)
        assert transition.debit_atom_moles(registry) == pytest.approx(
            transition.credit_atom_moles(registry))
    stage3 = sim.train.stages[3].collected_kg
    oxide_mol = sum(stage3.get(oxide, 0.0) / resolve_species_formula(
        oxide, registry).molar_mass_kg_per_mol() for oxide in ('Na2O', 'K2O'))
    oxygen_left = sim.atom_ledger.physical_mol_by_account('process.overhead_gas').get('O2', 0.0)
    # Each oxide formula unit requires one O atom, hence half a mol O2.
    assert oxygen_mol - oxygen_left == pytest.approx(oxide_mol * 0.5)
    assert oxide_mol * 0.5 <= oxygen_mol
    assert not stage3.get('Na', 0.0) and not stage3.get('K', 0.0)
    for sp in ('Na', 'K', 'Na2O', 'K2O', 'Si', 'SiO2'):
        projected = sum(stage.collected_kg.get(sp, 0.0) for stage in sim.train.stages)
        assert projected == pytest.approx(sim.atom_ledger.kg_by_account(
            'process.condensation_train').get(sp, 0.0))
    if oxygen_mol:
        assert oxide_mol > 0.0
        assert stage_purity_report(sim.train)['stage_3_sio_zone']['purity_fraction'] < 1.0
        assert sum(sim.train.stages[4].collected_kg.get(sp, 0.0) for sp in ('Na', 'K')) < sum(
            bypass.condensed_by_stage_species[4].get(sp, 0.0) for sp in ('Na', 'K'))
        assert sum(stage3.get(oxide, 0.0) for oxide in ('Na2O', 'K2O')) / (
            stage3['SiO2'] + sum(stage3.get(oxide, 0.0) for oxide in ('Na2O', 'K2O'))) <= 0.02


@pytest.mark.parametrize("carrier", ["Na", "K", "Na2", "K2O", "NaCl", "Na2O"])
@pytest.mark.parametrize("amount", [1.0, 1e-30])
def test_open_refuses_every_positive_alkali_carrier_with_zero_capture(carrier, amount, monkeypatch):
    model = _model("through")
    monkeypatch.setattr(model, "_route", lambda *args: CondensationRouteResult())
    with pytest.raises(Stage3SilicaExposureRefusal, match="silica_exposed_to_alkali") as caught:
        model.route(EvaporationFlux({carrier: amount}), MeltState())
    finding = caught.value.stage3_route_diagnostic["findings"][0]
    assert carrier in finding["carriers"]
    assert finding["carriers"][carrier]["alkali_atom_mol_s"] > 0


@pytest.mark.parametrize("supply", [{"NaCl": 1e-30}, {"unknown_carrier": 1.0}, {"K": None}])
def test_retained_or_unknown_supply_refuses_even_native_fe_parcel(supply):
    model = _model("through")
    model.retained_vapor_mol = supply
    with pytest.raises(Stage3SilicaExposureRefusal):
        model.route(EvaporationFlux({"Fe": 1.0}), MeltState())
    from simulator.optimize.physics import _coating_runtime_diagnostics
    from types import SimpleNamespace
    diagnostics = _coating_runtime_diagnostics(SimpleNamespace(condensation_model=model))
    assert diagnostics["silica_exposed_to_alkali_findings"]


@pytest.mark.parametrize("missing", ["liner_material", "length_m", "inner_diameter_m", "wall_temperature_C"])
def test_bypass_missing_physical_input_is_typed_refusal(missing):
    model = _model()
    model.stage3_bypass.pop(missing)
    with pytest.raises(DepositionInputRefusal, match="stage3_bypass"):
        model.route(EvaporationFlux({"Na": 1.0}), MeltState(temperature_C=1650))


def test_schedule_override_precedence_reset_and_lazy_train():
    sim = _sim()
    assert sim._condensation_model is None
    sim.load_batch("lunar_mare_low_ti", mass_kg=1.0)
    assert sim.condensation_model.train is sim.train
    assert sim.train.collection_route["stage3_route"] == "divert"
    sim.melt.campaign = CampaignPhase.C2A_STAGED
    sim.campaign_mgr.configure_campaign(sim.melt, sim.melt.campaign)
    sim.melt.campaign_hour = 4
    assert sim.condensation_model.train.collection_route["stage3_route"] == "through"
    assert sim.train.collection_route["substage"] == "sio_window"
    sim.campaign_mgr.overrides["C2A_STAGED"] = {"stage3_route": "divert"}
    assert sim.condensation_model.train.collection_route["stage3_route"] == "divert"
    sim.load_batch("lunar_mare_low_ti", mass_kg=1.0)
    assert sim.condensation_model.train is sim.train
    assert sim.train.collection_route["stage3_route"] == "divert"


def test_campaign_recipe_through_during_alkali_refuses_and_preserves_ledger():
    sim = _sim()
    sim.load_batch("lunar_mare_low_ti", mass_kg=1.0)
    sim.condensation_model.hard_stage3_gate = True
    sim.melt.campaign = CampaignPhase.C2A_STAGED
    sim.campaign_mgr.configure_campaign(sim.melt, sim.melt.campaign)
    sim.campaign_mgr.overrides["C2A_STAGED"] = {"stage3_route": "through"}
    before = copy.deepcopy(sim.atom_ledger.mol_by_account())
    with pytest.raises(Stage3SilicaExposureRefusal):
        sim.condensation_model.route(EvaporationFlux({"Na": 1.0}), sim.melt)
    assert sim.atom_ledger.mol_by_account() == before
    assert sim.train.stages[3].collected_kg == {}


def test_recipe_route_vocabulary_and_invalid_enum():
    schema = RecipeSchema()
    path = tuple("campaigns.C2A_staged.stages.sio_window.stage3_route".split("."))
    assert schema.to_setpoints_patch(RecipePatch({path: "divert"}))["campaigns"]["C2A_staged"]["stages"][1]["stage3_route"] == "divert"
    with pytest.raises(RecipeValidationError):
        RecipePatch({path: "open_silica"}).validated(schema)
    cfg = load_config_bundle().setpoints
    for phase, config in cfg["campaigns"].items():
        if isinstance(config, dict) and "temp_range_C" in config:
            assert config["stage3_route"] in ("divert", "through"), phase


def test_refusal_keeps_finding_after_hour_stops(monkeypatch):
    sim = _sim()
    sim.load_batch("lunar_mare_low_ti", mass_kg=1.0)
    sim.condensation_model.hard_stage3_gate = True
    sim.campaign_mgr.overrides["C0"] = {"stage3_route": "through"}
    before = copy.deepcopy(sim.atom_ledger.mol_by_account())

    def refuse():
        sim.condensation_model.route(EvaporationFlux({"Na": 1e-30}), sim.melt)

    monkeypatch.setattr(sim, "_step_one_hour", refuse)
    with pytest.raises(Stage3SilicaExposureRefusal):
        sim.step()
    assert sim.atom_ledger.mol_by_account() == before
    from simulator.optimize.physics import _coating_runtime_diagnostics
    assert _coating_runtime_diagnostics(sim)["silica_exposed_to_alkali_findings"]
    sim.load_batch("lunar_mare_low_ti", mass_kg=1.0)
    assert not _coating_runtime_diagnostics(sim)["silica_exposed_to_alkali_findings"]


def test_runtime_reads_retained_overhead_on_each_parcel():
    sim = _sim()
    sim.load_batch("lunar_mare_low_ti", mass_kg=1.0)
    sim.condensation_model.hard_stage3_gate = True
    sim.campaign_mgr.overrides["C0"] = {"stage3_route": "through"}
    model = sim.condensation_model
    sim.atom_ledger.load_external_mol("process.overhead_gas", {"K": 1e-9},
                                      material_origin="reagent")
    before = copy.deepcopy(sim.atom_ledger.mol_by_account())
    with pytest.raises(Stage3SilicaExposureRefusal):
        sim.condensation_model.route(EvaporationFlux({"Fe": 1.0}), sim.melt)
    assert sim.condensation_model is model
    assert sim.atom_ledger.mol_by_account() == before


@pytest.mark.parametrize("missing", ["phase_command", "substage_command", "bypass"])
def test_complete_config_file_cannot_omit_route_or_hardware(tmp_path, missing):
    import yaml
    cfg = load_config_bundle().setpoints
    if missing == "phase_command":
        cfg["campaigns"]["C0"].pop("stage3_route")
    elif missing == "substage_command":
        cfg["campaigns"]["C2A_staged"]["stages"][0].pop("stage3_route")
    else:
        cfg["condensation_train"].pop("stage3_bypass")
    path = tmp_path / "setpoints.yaml"
    path.write_text(yaml.safe_dump(cfg))
    with pytest.raises(ValueError, match="stage3"):
        load_config_bundle(setpoints_path=path)


def test_session_override_accepts_literal_route_and_rejects_unknown_enum():
    from simulator.session import SimSession
    sim = _sim()
    sim.load_batch("lunar_mare_low_ti", mass_kg=1.0)
    session = SimSession()
    session._sim = sim
    session.adjust("campaign_override", "through", campaign="C0", field="stage3_route")
    assert sim.train.collection_route["stage3_route"] == "through"
    with pytest.raises(ValueError, match="stage3_route"):
        session.adjust("campaign_override", "thru", campaign="C0", field="stage3_route")
    assert sim.train.collection_route["stage3_route"] == "through"


def test_admission_keeps_prior_committed_vapor_and_prevents_replay(monkeypatch):
    sim = _sim()
    sim.load_batch("lunar_mare_low_ti", mass_kg=1.0)
    sim.condensation_model.hard_stage3_gate = True
    sim.campaign_mgr.overrides["C0"] = {"stage3_route": "through"}
    ledger = sim.atom_ledger
    ledger.load_external_mol("process.metal_phase", {"Na": 1e-6}, material_origin="reagent")
    count_before = len(ledger.transitions)

    def committed_source_then_native_fe():
        ledger.transfer("preexisting_source_vapor", (
            ledger.debit_mol("process.metal_phase", {"Na": 1e-6}),), (
            ledger.credit_mol("process.overhead_gas", {"Na": 1e-6}),))
        sim.condensation_model.route(EvaporationFlux({"Fe": 1.0}), sim.melt)

    monkeypatch.setattr(sim, "_step_one_hour", committed_source_then_native_fe)
    with pytest.raises(Stage3SilicaExposureRefusal):
        sim.step()
    assert len(sim.atom_ledger.transitions) == count_before + 1
    assert sim.atom_ledger.mol_by_account("process.overhead_gas")["Na"] == pytest.approx(1e-6)
    assert sim.train.stages[3].collected_kg == {}
    assert sim._poisoned_hour is not None
    with pytest.raises(RuntimeError, match="poisoned"):
        sim.step()


def test_diverted_sio_bypass_remains_in_wall_and_cold_spot_model():
    model = _model()
    model.stage3_bypass["wall_temperature_C"] = 900.0
    model.route(EvaporationFlux({"SiO": 1.0}), MeltState(temperature_C=1650))
    findings = model.last_cold_spot_diagnostic["findings"]
    assert any(f["segment"] == "stage_2_to_stage_4_bypass" for f in findings)
    assert all("stage_3" not in f["segment"] for f in findings)


@pytest.mark.parametrize("prior_commit", [False, True])
def test_run_executor_preserves_typed_silica_refusal_and_coating_finding(monkeypatch, prior_commit):
    from simulator.run_executor import RunExecutor
    from simulator.session import SimSession
    from simulator.optimize.physics import _coating_runtime_diagnostics
    sim = _sim()
    sim.load_batch("lunar_mare_low_ti", mass_kg=1.0)
    sim.condensation_model.hard_stage3_gate = True
    sim.campaign_mgr.overrides["C0"] = {"stage3_route": "through"}
    session = SimSession()
    session._sim = sim
    ledger = sim.atom_ledger
    ledger.load_external_mol("process.metal_phase", {"Na": 1e-6}, material_origin="reagent")
    before_count = len(ledger.transitions)

    def refuse():
        if prior_commit:
            ledger.transfer("source_before_admission", (
                ledger.debit_mol("process.metal_phase", {"Na": 1e-6}),), (
                ledger.credit_mol("process.overhead_gas", {"Na": 1e-6}),))
        sim.condensation_model.route(EvaporationFlux({"Na": 1e-30}), sim.melt)

    def drive(*args, **kwargs):
        sim.step()
        yield  # Admission always raises before producing a snapshot.

    monkeypatch.setattr(sim, "_step_one_hour", refuse)
    monkeypatch.setattr("simulator.run_executor.drive_session", drive)
    execution = RunExecutor().execute_session(session, hours=1)
    assert execution.status == ("failed" if prior_commit else "refused")
    assert execution.reason == ("poisoned_hour" if prior_commit else "silica_exposed_to_alkali")
    assert isinstance(execution.failure_exception, Stage3SilicaExposureRefusal)
    assert execution.refusal_diagnostic["findings"][0]["type"] == "silica_exposed_to_alkali"
    assert _coating_runtime_diagnostics(execution)["silica_exposed_to_alkali_findings"]
    assert len(execution.simulator.atom_ledger.transitions) == before_count + int(prior_commit)
    if prior_commit:
        assert execution.simulator.atom_ledger.mol_by_account("process.overhead_gas")["Na"] == pytest.approx(1e-6)


def test_existing_numeric_override_boundaries_and_silica_mass_pin():
    """Pin numeric parsing and the existing 2SiO -> Si + SiO2 mass basis."""
    from simulator.session import _finite_float
    from simulator.session_cli import _parse_override_json
    from simulator.runner import _parse_runtime_campaign_overrides_json
    from simulator.condensation import CondensationRouteResult
    assert _finite_float("1.25", "pin") == 1.25
    for parse in (_parse_override_json, _parse_runtime_campaign_overrides_json):
        assert parse('{"C0":{"max_hours":"1.25"}}', flag_name="pin") == {
            "C0": {"max_hours": 1.25}}
    result = CondensationRouteResult(condensed_by_stage_species={3: {"SiO": 1.0}})
    assert result.silica_fume_fraction_of_feedstock(1.0) == pytest.approx(
        0.6814603937936665, rel=1e-12)


@pytest.mark.parametrize("entry", ["session_start", "script_adjust", "script_setpoint", "script_json", "batch_json"])
@pytest.mark.parametrize("route", ["divert", "through"])
def test_route_enum_at_every_runtime_entry(entry, route):
    import json
    from simulator.session import SimSession, SimSessionConfig
    from simulator.session_cli import SessionScriptRunner
    from simulator.runner import _parse_runtime_campaign_overrides_json
    fields = {"C0": {"stage3_route": route, "stage3_product_purity": 0.998,
                     "stage3_instantaneous_purity": 0.98}}
    if entry == "batch_json":
        assert _parse_runtime_campaign_overrides_json(json.dumps(fields), flag_name="pin") == fields
        return
    runner = SessionScriptRunner()
    if entry == "session_start":
        bundle = load_config_bundle()
        runner.session.start(SimSessionConfig(
            feedstock_id="lunar_mare_low_ti", mass_kg=1.0, campaign="C0",
            feedstocks=bundle.feedstocks, setpoints=bundle.setpoints,
            vapor_pressures=bundle.vapor_pressures, backend_name="internal-analytical",
            runtime_campaign_overrides=fields))
    else:
        args = ["--feedstock=lunar_mare_low_ti", "--campaign=C0", "--mass-kg=1", "--backend=internal-analytical"]
        if entry == "script_setpoint":
            args += [f"--setpoint=C0.stage3_route={route}", "--setpoint=C0.stage3_product_purity=0.998",
                     "--setpoint=C0.stage3_instantaneous_purity=0.98"]
        elif entry == "script_json":
            args += ["--runtime-campaign-overrides=" + json.dumps(fields)]
        runner.execute(["start", *args], "test")
        if entry == "script_adjust":
            runner.execute(["adjust", "campaign_override", "C0", "stage3_route", route], "test")
            runner.execute(["adjust", "campaign_override", "C0", "stage3_product_purity", "0.998"], "test")
            runner.execute(["adjust", "campaign_override", "C0", "stage3_instantaneous_purity", "0.98"], "test")
    selected = runner.session._sim.condensation_model.train.collection_route
    assert selected["stage3_route"] == route
    assert selected["stage3_product_purity"] == 0.998
    assert selected["stage3_instantaneous_purity"] == 0.98


@pytest.mark.parametrize("value", [None, "bad"])
def test_complete_file_requires_valid_mre_route(tmp_path, value):
    import yaml
    cfg = load_config_bundle().setpoints
    if value is None:
        cfg["campaigns"]["mre_baseline"].pop("stage3_route")
    else:
        cfg["campaigns"]["mre_baseline"]["stage3_route"] = value
    path = tmp_path / "setpoints.yaml"
    path.write_text(yaml.safe_dump(cfg))
    with pytest.raises(ValueError, match="mre_baseline.stage3_route"):
        load_config_bundle(setpoints_path=path)


def test_known_nonalkali_retained_roundoff_is_not_alkali_exposure():
    model = _model("through")
    model.retained_vapor_mol = {"Al": 3.2311742677852644e-27, "Mg": -8.470329472543003e-22}
    model.route(EvaporationFlux({"SiO": 1.0}), MeltState(temperature_C=1650))
    assert model.last_stage3_route_diagnostic["unavailable"] == []
    assert model.last_stage3_route_diagnostic["alkali_oxide_kg_hr"] == 0.0


@pytest.mark.parametrize("purity, factor, capture, admitted", [
    (0.999, 0.5, 1.0, True), (0.999, 2.0, 1.0, False),
    (1.0, 0.5, 1.0, False), (0.999, 0.5, 0.0, False),
])
def test_live_capture_purity_bound_and_limits(monkeypatch, purity, factor, capture, admitted):
    from simulator.accounting import resolve_species_formula
    from simulator.condensation import CondensationRouteResult
    model = _model("through")
    model.train.collection_route["stage3_product_purity"] = purity
    projection = CondensationRouteResult(condensed_by_stage_species={3: {"SiO": capture}})
    monkeypatch.setattr(model, "_route", lambda *args: projection)
    # Set an oxide-equivalent dose independently via the production formula resolver.
    na = resolve_species_formula("Na", {}).molar_mass_kg_per_mol()
    oxide = resolve_species_formula("Na2O", {}).molar_mass_kg_per_mol()
    oxide_dose = projection.silica_capture_kg(3) * 0.001 * factor if capture else 1e-30
    flux = EvaporationFlux({"SiO": 1.0, "Na": oxide_dose * 2 * na / oxide})
    if admitted:
        assert model.route(flux, MeltState()) is projection
    else:
        with pytest.raises(Stage3SilicaExposureRefusal):
            model.route(flux, MeltState())
    diag = model.last_stage3_route_diagnostic
    assert diag["alkali_oxide_kg_hr"] == pytest.approx(oxide_dose)
    assert diag["silica_capture_kg_hr"] == projection.silica_capture_kg(3)
    assert diag["stage3_product_purity"] == purity
    assert model.train.stages[3].collected_kg == {}


@pytest.mark.parametrize("field", ["stage3_product_purity", "stage3_instantaneous_purity"])
@pytest.mark.parametrize("value", [None, 0, -1, 1.001, float("nan"), True])
def test_purity_config_recipe_and_runtime_reject_invalid(field, value, tmp_path):
    import yaml
    from simulator.campaigns import parse_campaign_override_value
    path = ("condensation_train", field)
    with pytest.raises(RecipeValidationError):
        RecipePatch({path: value}).validated(RecipeSchema())
    with pytest.raises(ValueError):
        parse_campaign_override_value(field, value)
    cfg = load_config_bundle().setpoints
    cfg["condensation_train"][field] = value
    with pytest.raises(ValueError, match=field):
        _sim(cfg)
    file = tmp_path / "setpoints.yaml"
    file.write_text(yaml.safe_dump(cfg))
    with pytest.raises(ValueError, match=f"{field}|rejects NaN"):
        load_config_bundle(setpoints_path=file)


@pytest.mark.parametrize("field, default, value", [
    # d-287 explicitly revises the product default from 0.999 to 0.98.
    ("stage3_product_purity", 0.98, 0.998),
    ("stage3_instantaneous_purity", 0.99, 0.98),
])
def test_recipe_purity_reaches_same_train_after_lazy_init_and_reset(field, default, value):
    bundle = load_config_bundle()
    assert bundle.setpoints["condensation_train"][field] == default
    path = ("condensation_train", field)
    patch = RecipeSchema().to_setpoints_patch(RecipePatch({path: value}))
    cfg = copy.deepcopy(bundle.setpoints)
    cfg["condensation_train"].update(patch["condensation_train"])
    sim = _sim(cfg)
    assert sim._condensation_model is None
    assert sim.condensation_model.train is sim.train
    assert sim.train.collection_route[field] == value
    sim.load_batch("lunar_mare_low_ti", mass_kg=1.0)
    assert sim.condensation_model.train is sim.train
    assert sim.train.collection_route[field] == value


@pytest.mark.parametrize("dose_factor", [0.5, 2.0])
def test_retained_alkali_is_charged_to_live_purity_budget(monkeypatch, dose_factor):
    from simulator.accounting import resolve_species_formula
    model = _model("through")
    # Exercise the supported 0.999 runtime override (d-287); the default is 0.98.
    model.train.collection_route["stage3_product_purity"] = 0.999
    projection = CondensationRouteResult(condensed_by_stage_species={3: {"SiO": 1.0}})
    monkeypatch.setattr(model, "_route", lambda *args: projection)
    oxide = resolve_species_formula("K2O", {}).molar_mass_kg_per_mol()
    # One hour of retained K, converted on the production formula basis.
    allowance = projection.silica_capture_kg(3) * (1 - 0.999) / 0.999
    model.retained_vapor_mol = {"K": 2 * dose_factor * allowance / oxide}
    if dose_factor > 1:
        with pytest.raises(Stage3SilicaExposureRefusal):
            model.route(EvaporationFlux({"SiO": 1.0}), MeltState())
    else:
        assert model.route(EvaporationFlux({"SiO": 1.0}), MeltState()) is projection
    assert model.last_stage3_route_diagnostic["alkali_oxide_kg_hr"] == pytest.approx(dose_factor * allowance)


def test_interim_gate_uses_product_grade_before_chunk4_burst_ceiling(monkeypatch):
    model = _model("through")
    model.train.collection_route.update({
        "stage3_product_purity": 0.999, "stage3_instantaneous_purity": 0.99})
    projection = CondensationRouteResult(condensed_by_stage_species={3: {"SiO": 1.0}})
    monkeypatch.setattr(model, "_route", lambda *args: projection)
    # A 99.5% silica/oxide mixture lies between the two owner specifications.
    # Na2O is already the oxide mass basis, so no carrier conversion is needed.
    oxide_dose = projection.silica_capture_kg(3) * (1 - 0.995) / 0.995
    with pytest.raises(Stage3SilicaExposureRefusal) as caught:
        model.route(EvaporationFlux({"SiO": 1.0, "Na2O": oxide_dose}), MeltState())
    diag = caught.value.stage3_route_diagnostic
    assert diag["capture_purity"] == pytest.approx(0.995)
    assert diag["stage3_product_purity"] == 0.999
    assert diag["stage3_instantaneous_purity"] == 0.99
    assert diag["effective_instantaneous_purity"] == 0.999


def test_refused_opening_routes_same_parcel_as_divert_and_isolates_whole_hour():
    sim = _sim()
    sim.load_batch("lunar_mare_low_ti", mass_kg=1.0)
    sim.campaign_mgr.overrides["C0"] = {"stage3_route": "through"}
    model = _model("through", hard_gate=False)
    model.train = sim.train
    sim._condensation_model = model
    flux = EvaporationFlux({"Na": 1.0, "SiO": 1.0})
    result = sim.condensation_model.route(flux, sim.melt)
    reference = model.route(flux, sim.melt)
    assert result.condensed_by_stage_species == reference.condensed_by_stage_species
    assert result.wall_deposit_by_segment_species == reference.wall_deposit_by_segment_species
    assert result.remaining_by_species == reference.remaining_by_species
    assert 3 not in result.condensed_by_stage_species
    # Re-accessing the lazy model reapplies the campaign command. It must
    # not reopen the cartridge for a later, even alkali-free parcel this hour.
    assert sim.condensation_model.train.collection_route["stage3_route"] == "divert"
    sim.condensation_model.route(EvaporationFlux({"SiO": 1.0}), sim.melt)
    events = [d for d in model.cold_spot_history
              if d.get("event") == "stage3_opening_refused"]
    assert len(events) == 1
    assert events[0]["hour"] == 0
    sim.melt.hour += 1
    assert sim.condensation_model.train.collection_route["stage3_route"] == "through"
    sim.load_batch("lunar_mare_low_ti", mass_kg=1.0)
    assert not [d for d in model.cold_spot_history
                if d.get("event") == "stage3_opening_refused"]


def test_default_lunar_staged_continues_after_refused_window():
    """Pin refusal and continuation; the full 120 h trajectory is measurement evidence (divert-ror's 1000 kg run), not a test."""
    import json
    from simulator.run_executor import RunExecutor
    from simulator.session import SimSession, SimSessionConfig
    from simulator.condensation import stage_purity_report
    from simulator.optimize.physics import _coating_runtime_diagnostics, PhysicsConstraintSet
    bundle = load_config_bundle()
    session = SimSession()
    session.start(SimSessionConfig(
        feedstock_id="lunar_mare_low_ti", mass_kg=1.0, campaign="C2A_staged",
        feedstocks=bundle.feedstocks, setpoints=bundle.setpoints,
        vapor_pressures=bundle.vapor_pressures, backend_name="internal-analytical"))
    execution = RunExecutor().execute_session(session, hours=12)
    assert execution.status in {"ok", "partial"}, execution.error_message
    assert execution.failure_exception is None
    assert execution.simulator.melt.hour > 6
    assert len(execution.snapshots) == execution.simulator.melt.hour
    events = [e for e in execution.shadow_trace
              if e.get("event") == "stage3_opening_refused"]
    # The default schedule requests SiO collection at elapsed hours 4, 5, 6.
    assert [e["hour"] for e in events] == [4, 5, 6]
    assert [e["campaign_hour"] for e in events] == [4, 5, 6]
    for event in events:
        assert event["campaign"] == "C2A_STAGED"
        assert event["substage"] == "sio_window"
        assert event["alkali_oxide_kg_hr"] > event["allowed_alkali_oxide_kg_hr"]
        assert event["silica_capture_kg_hr"] >= 0.0
        assert event["stage3_product_purity"] == 0.98  # d-287 product default
        assert event["reason"] == "alkali_purity_exceeded"
        assert event["resolved_stage3_route"] == "divert"
    product = stage_purity_report(execution.simulator.train)["stage_3_sio_zone"]
    assert product["total_kg"] == 0.0
    assert product["purity_fraction"] is None
    # The event survives JSON artifacts and alone makes the requested window
    # infeasible even when there is no actual silica contact or wall deposit.
    artifact = json.loads(json.dumps({"shadow_trace": execution.shadow_trace}))
    diagnostics = _coating_runtime_diagnostics(artifact)
    assert len(diagnostics["silica_exposed_to_alkali_findings"]) == 3
    margin = PhysicsConstraintSet(active_gates=("coating",)).coating_from_fouling_report({
        "campaigns_to_resinter_total": float("inf"),
        "authoritative_for_resinter": True,
        "output_status": "authoritative", "status_reason": "",
        "feedstock_charge_mass_kg": 1.0,
        "coating_constraint_mode": "upstream_deposit_fraction",
        "wall_deposit_kg_per_campaign": 0.0,
        "sticking_alpha_authority": {"authoritative_for_deposit_mass": True},
        **artifact,
    })
    assert not margin.feasible
    assert margin.status != "unavailable"
    assert any(r["reason"] == "silica_exposed_to_alkali"
               and r["finding"]["event"] == "stage3_opening_refused"
               for r in margin.status_payload["coating_violation_reasons"])


def test_default_lunar_post_c4_hot_continuation_admits_or_records_refusal():
    sim = _sim()
    sim.load_batch("lunar_mare_low_ti")
    sim.start_campaign(CampaignPhase.C2A_STAGED)
    # Warm-start at the C4 outlet; the full default trajectory is measured
    # separately. Use the production successor so its scoped continuation
    # overrides participate, without replaying the preceding campaigns here.
    sim.melt.temperature_C = sim.setpoints["campaigns"]["C4"]["temp_range_C"][-1]
    successor = sim.campaign_mgr.get_next_campaign(CampaignPhase.C4, sim.record)
    assert successor == CampaignPhase.C2A
    sim.start_campaign(successor)
    for _ in range(3):
        hour = sim.melt.hour
        assert sim.condensation_model.train.collection_route["stage3_route"] == "through"
        sim.step()
        events = [e for e in sim._condensation_model.cold_spot_history
                  if e.get("event") == "stage3_opening_refused" and e["hour"] == hour]
        route = sim.train.collection_route
        assert (route["stage3_route"] == "through"
                or (route.get("opening_refused_hour") == hour and events))
        for event in events:
            assert event["campaign"] == "C2A"
            assert event["resolved_stage3_route"] == "divert"
            assert event["reason"] == "alkali_purity_exceeded"


def test_retained_alkali_roundoff_projects_to_zero_before_admission():
    sim = _sim()
    sim.load_batch("lunar_mare_low_ti", mass_kg=1.0)
    sim.campaign_mgr.overrides["C0"] = {"stage3_route": "through"}
    sim.atom_ledger._balances.setdefault("process.overhead_gas", {})["Na"] = -1.1e-16
    model = _model("through", hard_gate=False)
    model.train = sim.train
    sim._condensation_model = model
    model = sim.condensation_model
    assert model.retained_vapor_mol["Na"] == 0.0
    model.route(EvaporationFlux({"Na": 1.0, "SiO": 1.0}), sim.melt)
    diagnostic = model.last_stage3_route_diagnostic
    assert diagnostic["event"] == "stage3_opening_refused"
    assert diagnostic["unavailable"] == []
    assert diagnostic["retained_supply_projection"]["roundoff_projected_mol"][
        "Na"]["signed_mol"] == -1.1e-16
    assert sim.atom_ledger.mol_by_account("process.overhead_gas")["Na"] == -1.1e-16


def test_negative_retained_na_still_refuses_without_terminal_purity_gate():
    model = _model("through", hard_gate=False)
    model.retained_vapor_mol = {"Na": -1.0}
    with pytest.raises(Stage3SilicaExposureRefusal) as caught:
        model.route(EvaporationFlux({"SiO": 1.0}), MeltState())
    assert caught.value.stage3_route_diagnostic["unavailable"] == [{
        "species": "Na", "source": "retained_mol",
        "detail": "Na must be finite and non-negative"}]


def test_refused_proposal_does_not_record_hypothetical_silica_surfaces():
    through = _model("through", hard_gate=False)
    diverted = _model("through", hard_gate=False)
    # Both have the same configured input history; only their actual parcel
    # route differs before admission. Configuration records the prior command.
    diverted.train.collection_route["stage3_route"] = "divert"
    flux = EvaporationFlux({"Na": 1.0, "SiO": 1.0})
    melt = MeltState(temperature_C=1650)
    through.route(flux, melt)
    diverted.route(flux, melt)
    assert through.last_sticking_alpha_provenance_notice == diverted.last_sticking_alpha_provenance_notice
    actual_history = [d for d in through.cold_spot_history
                      if d.get("event") != "stage3_opening_refused"]
    assert actual_history == diverted.cold_spot_history
    assert through.operating_history == diverted.operating_history
