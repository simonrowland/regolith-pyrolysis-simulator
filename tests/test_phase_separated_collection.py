"""Stage-3 topology and conservative silica admission (d-268, chunk 3)."""
from __future__ import annotations

import copy

import pytest

from simulator.campaigns import CampaignManager
from simulator.condensation import (
    CondensationModel, DepositionInputRefusal, Stage3SilicaExposureRefusal,
)
from simulator.config import load_config_bundle
from simulator.core import PyrolysisSimulator
from simulator.melt_backend.base import InternalAnalyticalBackend
from simulator.recipe import RecipePatch, RecipeSchema, RecipeValidationError
from simulator.state import CampaignPhase, CondensationTrain, EvaporationFlux, MeltState


def _model(route="divert"):
    model = CondensationModel(CondensationTrain.create_default())
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
    model = _model("through")
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


@pytest.mark.parametrize("carrier", ["Na", "K", "Na2", "K2O", "NaCl", "Na2O"])
@pytest.mark.parametrize("amount", [1.0, 1e-30])
def test_open_refuses_every_positive_alkali_carrier_before_route(carrier, amount, monkeypatch):
    model = _model("through")
    monkeypatch.setattr(model, "_route", lambda *args: pytest.fail("admission must precede contact"))
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
