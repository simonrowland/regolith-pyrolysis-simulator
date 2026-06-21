from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from engines.builtin.electrolysis_step import BuiltinElectrolysisStepProvider
from simulator import mre_ladder
from simulator.chemistry.kernel.capabilities import ChemistryIntent
from simulator.chemistry.kernel.dto import IntentRequest, ProviderAccountView
from simulator.core import PyrolysisSimulator
from simulator.melt_backend.base import StubBackend
from simulator.runner import PyrolysisRun, build_per_hour_summary
from simulator.session import SimSession
from simulator.state import CampaignPhase, MeltState, MOLAR_MASS


def _repo_setpoints() -> dict:
    repo_root = Path(__file__).resolve().parent.parent
    return yaml.safe_load((repo_root / "data" / "setpoints.yaml").read_text())


def _sim(setpoints: dict) -> PyrolysisSimulator:
    backend = StubBackend()
    backend.initialize({})
    return PyrolysisSimulator(
        backend,
        setpoints,
        {"x": {"label": "X", "composition_wt_pct": {"SiO2": 100}}},
        {"metals": {}, "oxide_vapors": {}},
    )


def _species_names(sequence: list[dict]) -> list[str]:
    return [entry["species"][0] for entry in sequence]


def test_c5_fields_default_off_and_pass_through_session_config():
    melt = MeltState()

    assert melt.c5_enabled is False
    assert melt.mre_target_species == ""
    assert melt.mre_max_voltage_V == pytest.approx(0.0)

    config = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        c5_enabled=True,
        mre_target_species="SiO2",
        mre_max_voltage_V=1.7,
    )._session_config()
    session = SimSession().start(config)

    assert config.c5_enabled is True
    assert config.mre_target_species == "SiO2"
    assert config.mre_max_voltage_V == pytest.approx(1.7)
    assert session.simulator.melt.c5_enabled is True
    assert session.simulator.melt.mre_target_species == "SiO2"
    assert session.simulator.melt.mre_max_voltage_V == pytest.approx(1.7)
    assert session.simulator.campaign_mgr.c5_enabled is True


def test_build_mre_voltage_sequence_matches_published_yaml_ladder():
    setpoints = _repo_setpoints()

    sequence = mre_ladder.build_mre_voltage_sequence(setpoints)

    assert _species_names(sequence) == [
        "NiO",
        "Na2O",
        "K2O",
        "FeO",
        "Cr2O3",
        "MnO",
        "SiO2",
        "TiO2",
        "Al2O3",
        "MgO",
        "CaO",
    ]
    assert [entry["voltage"] for entry in sequence] == [
        0.39,
        0.5,
        0.5,
        0.75,
        0.95,
        1.05,
        1.45,
        1.70,
        1.95,
        2.2,
        2.5,
    ]
    assert [entry["min_hold_hours"] for entry in sequence] == [
        2,
        2,
        2,
        3,
        2,
        2,
        5,
        3,
        8,
        5,
        10,
    ]


def test_parse_ladder_from_setpoints_matches_repo_yaml_shape():
    setpoints = _repo_setpoints()

    sequence = mre_ladder.parse_ladder_from_setpoints(setpoints)

    assert _species_names(sequence)[:3] == ["NiO", "Na2O", "K2O"]
    assert _species_names(sequence)[6:8] == ["SiO2", "TiO2"]
    assert sequence[6]["voltage"] == pytest.approx(1.45)
    assert sequence[7]["voltage"] == pytest.approx(1.70)


def test_max_voltage_for_target_uses_ladder_ground_truth():
    sequence = mre_ladder.parse_ladder_from_setpoints(_repo_setpoints())

    assert mre_ladder.max_voltage_for_target("SiO2", sequence) == pytest.approx(1.45)
    assert mre_ladder.max_voltage_for_target("TiO2", sequence) == pytest.approx(1.70)
    assert mre_ladder.max_voltage_for_target("CaO", sequence) == pytest.approx(2.5)
    assert mre_ladder.max_voltage_for_target("not-an-oxide", sequence) == pytest.approx(0.0)


def test_filter_steps_up_to_target_max_selects_physical_prefixes():
    sequence = mre_ladder.parse_ladder_from_setpoints(_repo_setpoints())

    si_steps = mre_ladder.filter_steps_up_to_max_v(
        sequence, mre_ladder.max_voltage_for_target("SiO2", sequence)
    )
    ti_steps = mre_ladder.filter_steps_up_to_max_v(
        sequence, mre_ladder.max_voltage_for_target("TiO2", sequence)
    )

    assert "SiO2" in _species_names(si_steps)
    assert "TiO2" not in _species_names(si_steps)
    assert "CaO" not in _species_names(si_steps)
    assert "SiO2" in _species_names(ti_steps)
    assert "TiO2" in _species_names(ti_steps)
    assert "CaO" not in _species_names(ti_steps)


def test_preset_catalog_includes_disabled_alkali_targets():
    presets = mre_ladder.preset_catalog(_repo_setpoints())
    by_target = {preset.get("mre_target_species"): preset for preset in presets}

    assert by_target[""]["c5_enabled"] is False
    assert by_target["SiO2"]["enabled"] is True
    assert by_target["SiO2"]["mre_max_voltage_V"] == pytest.approx(1.45)
    assert by_target["Na2O"]["enabled"] is False
    assert by_target["K2O"]["enabled"] is False
    assert "pre-depleted" in by_target["Na2O"]["disabled_reason"]
    assert "pre-depleted" in by_target["K2O"]["disabled_reason"]


def test_step_mre_dispatch_uses_selected_runtime_max_voltage():
    setpoints = {
        "campaigns": {},
        "mre_voltage_sequence": {
            "sequence": [
                {
                    "species": "SiO2",
                    "decomposition_V": 1.7,
                    "min_hold_hours": 0,
                },
            ],
            "voltage_strategy": {
                "branch_two": {
                    "max_V": 1.7,
                },
            },
        },
    }
    sim = _sim(setpoints)
    sim._mre_voltage_sequence = sim._build_mre_voltage_sequence()
    sim.melt.campaign = CampaignPhase.C5
    sim.melt.c5_enabled = True
    sim.melt.mre_target_species = "SiO2"
    sim.melt.mre_max_voltage_V = 1.7
    captured: dict = {}

    def fake_dispatch(
        _intent,
        *,
        control_inputs,
        fO2_log=None,
        fe_redox_policy="intrinsic",
    ):
        captured.update(control_inputs)
        captured["fO2_log"] = fO2_log
        captured["fe_redox_policy"] = fe_redox_policy
        return SimpleNamespace(
            diagnostic={
                "energy_kWh": 0.0,
                "metals_produced_kg": {},
                "metals_produced_mol": {},
                "oxides_reduced_kg": {},
            },
            transition=None,
        )

    sim._dispatch_only = fake_dispatch
    sim._ledger_account_species_kg = lambda _account, _species: 0.0
    sim._project_extraction_melt = lambda: None
    sim._sync_oxygen_kg_counters = lambda: None

    sim._step_mre()

    assert captured["voltage_V"] == pytest.approx(1.7)
    assert captured["current_A"] == pytest.approx(mre_ladder.C5_LIMITED_MRE_CURRENT_A)
    assert captured["allowed_oxides"] == ["SiO2"]
    assert captured["melt_fO2_log"] == pytest.approx(-9.0)
    assert captured["fO2_log"] == pytest.approx(-9.0)
    assert captured["fe_redox_policy"] == "kress91_live"


def test_step_mre_restricts_reducible_oxides_to_target_rung():
    setpoints = {
        "campaigns": {},
        "mre_voltage_sequence": {
            "sequence": [
                {"species": "FeO", "decomposition_V": 0.75, "min_hold_hours": 0},
                {"species": "SiO2", "decomposition_V": 1.45, "min_hold_hours": 0},
                {"species": "TiO2", "decomposition_V": 1.70, "min_hold_hours": 0},
                {"species": "CaO", "decomposition_V": 2.5, "min_hold_hours": 0},
            ],
        },
    }
    sim = _sim(setpoints)
    sim._mre_voltage_sequence = sim._build_mre_voltage_sequence()
    sim.melt.campaign = CampaignPhase.C5
    sim.melt.c5_enabled = True
    sim.melt.mre_target_species = "SiO2"
    sim.melt.mre_max_voltage_V = 1.45
    sim._mre_voltage_step_idx = 1
    captured: list[dict] = []

    def fake_dispatch(_intent, *, control_inputs, **_kwargs):
        captured.append(dict(control_inputs))
        return SimpleNamespace(
            diagnostic={
                "energy_kWh": 0.0,
                "metals_produced_kg": {},
                "metals_produced_mol": {},
                "oxides_reduced_kg": {},
            },
            transition=None,
        )

    sim._dispatch_only = fake_dispatch
    sim._ledger_account_species_kg = lambda _account, _species: 0.0
    sim._project_extraction_melt = lambda: None
    sim._sync_oxygen_kg_counters = lambda: None

    sim._step_mre()

    assert captured
    assert captured[0]["allowed_oxides"] == ["SiO2"]
    assert captured[0]["voltage_V"] == pytest.approx(1.45)


def test_c5_safety_max_hold_advances_without_low_current():
    setpoints = {
        "campaigns": {},
        "mre_voltage_sequence": {
            "sequence": [
                {"species": "FeO", "decomposition_V": 0.75, "min_hold_hours": 0},
                {"species": "SiO2", "decomposition_V": 1.45, "min_hold_hours": 0},
            ],
        },
    }
    sim = _sim(setpoints)
    sim._mre_voltage_sequence = sim._build_mre_voltage_sequence()
    sim.melt.campaign = CampaignPhase.C5
    sim.melt.c5_enabled = True
    sim.melt.mre_target_species = ""
    sim.melt.mre_max_voltage_V = 1.45
    sim._mre_voltage_step_idx = 0
    sim._mre_hold_hours = int(mre_ladder.C5_DEPLETION_SAFETY_MAX_HOLD_HR) - 1
    sim._mre_effective_current_A = mre_ladder.C5_LIMITED_MRE_CURRENT_A

    def fake_dispatch(_intent, *, control_inputs, **_kwargs):
        return SimpleNamespace(
            diagnostic={
                "energy_kWh": 0.0,
                "metals_produced_kg": {},
                "metals_produced_mol": {},
                "oxides_reduced_kg": {},
            },
            transition=None,
        )

    sim._dispatch_only = fake_dispatch
    sim._ledger_account_species_kg = lambda _account, _species: 0.0
    sim._project_extraction_melt = lambda: None
    sim._sync_oxygen_kg_counters = lambda: None

    sim._step_mre()

    assert sim._mre_voltage_step_idx == 1
    assert sim._mre_hold_hours == 0


def test_c5_safety_max_hold_stops_after_terminal_rung():
    setpoints = {
        "campaigns": {},
        "mre_voltage_sequence": {
            "sequence": [
                {"species": "SiO2", "decomposition_V": 1.45, "min_hold_hours": 0},
            ],
        },
    }
    sim = _sim(setpoints)
    sim._mre_voltage_sequence = sim._build_mre_voltage_sequence()
    sim.melt.campaign = CampaignPhase.C5
    sim.melt.c5_enabled = True
    sim.melt.mre_target_species = "SiO2"
    sim.melt.mre_max_voltage_V = 1.45
    sim._mre_voltage_step_idx = 0
    sim._mre_hold_hours = int(mre_ladder.C5_DEPLETION_SAFETY_MAX_HOLD_HR) - 1
    sim._mre_effective_current_A = mre_ladder.C5_LIMITED_MRE_CURRENT_A
    dispatches = 0

    def fake_dispatch(_intent, *, control_inputs, **_kwargs):
        nonlocal dispatches
        dispatches += 1
        return SimpleNamespace(
            diagnostic={
                "energy_kWh": 0.0,
                "metals_produced_kg": {},
                "metals_produced_mol": {},
                "oxides_reduced_kg": {},
            },
            transition=None,
        )

    sim._dispatch_only = fake_dispatch
    sim._ledger_account_species_kg = lambda _account, _species: 0.0
    sim._project_extraction_melt = lambda: None
    sim._sync_oxygen_kg_counters = lambda: None

    sim._step_mre()
    assert sim._mre_voltage_step_idx == 1
    assert sim._mre_hold_hours == 0

    sim._step_mre()
    assert dispatches == 1
    assert sim._mre_voltage_V == pytest.approx(0.0)
    assert sim._mre_current_A == pytest.approx(0.0)
    assert sim._mre_effective_current_A == pytest.approx(0.0)


def test_c5_kress91_live_ferric_inventory_becomes_ferrous_behavior():
    setpoints = {
        "campaigns": {},
        "mre_voltage_sequence": {
            "sequence": [
                {"species": "FeO", "decomposition_V": 5.0, "min_hold_hours": 0},
            ],
        },
    }
    sim = _sim(setpoints)
    sim.atom_ledger = sim._new_atom_ledger()
    fe2o3_mol = 10.0 / (MOLAR_MASS["Fe2O3"] / 1000.0)
    sim.atom_ledger.load_external_mol(
        "process.cleaned_melt", {"Fe2O3": fe2o3_mol}, source="test seed"
    )
    sim._chem_kernel = sim._build_chemistry_kernel()
    sim._mre_voltage_sequence = sim._build_mre_voltage_sequence()
    sim.melt.campaign = CampaignPhase.C5
    sim.melt.c5_enabled = True
    sim.melt.mre_target_species = "FeO"
    sim.melt.mre_max_voltage_V = 5.0
    sim.melt.temperature_C = 1600.0

    produced_o2_kg = sim._step_mre()
    cleaned = sim.atom_ledger.mol_by_account("process.cleaned_melt")
    o2 = sim.atom_ledger.mol_by_account("terminal.oxygen_mre_anode_stored")

    converted_fe2o3_mol = fe2o3_mol - cleaned.get("Fe2O3", 0.0)
    assert produced_o2_kg > 0.0
    assert converted_fe2o3_mol > 0.0
    assert cleaned["FeO"] == pytest.approx(2.0 * converted_fe2o3_mol)
    assert o2["O2"] == pytest.approx(0.5 * converted_fe2o3_mol)
    marker = sim._mre_uncertified_yield["FeO"]
    assert marker["certification"] == "uncertified_ferric_to_ferrous_reference"
    assert marker["reference_V"] == pytest.approx(0.65)
    assert marker["reference_status"] == (
        "uncertified_heuristic_reference_not_raw_thermo"
    )

    snapshot = sim._make_snapshot()
    assert snapshot.mre_uncertified_yield["FeO"]["produced_mol"] > 0.0
    summary = build_per_hour_summary(sim, snapshot)
    assert summary["mre_uncertified_yield"]["FeO"]["certification"] == (
        "uncertified_ferric_to_ferrous_reference"
    )


def test_c5_sio2_target_step_does_not_reduce_feo():
    setpoints = {
        "campaigns": {},
        "mre_voltage_sequence": {
            "sequence": [
                {"species": "FeO", "decomposition_V": 0.75, "min_hold_hours": 0},
                {"species": "SiO2", "decomposition_V": 1.45, "min_hold_hours": 0},
            ],
        },
    }
    sim = _sim(setpoints)
    sim._mre_voltage_sequence = sim._build_mre_voltage_sequence()
    sim.melt.campaign = CampaignPhase.C5
    sim.melt.c5_enabled = True
    sim.melt.mre_target_species = "SiO2"
    sim.melt.mre_max_voltage_V = 1.45
    sim._mre_voltage_step_idx = 1
    provider = BuiltinElectrolysisStepProvider()
    view = ProviderAccountView(
        accounts={
            "process.cleaned_melt": {
                "FeO": 10.0,
                "Fe2O3": 10.0,
                "SiO2": 10.0,
            },
        },
        species_formula_registry={},
    )
    reductions: dict[str, float] = {}

    def dispatch_with_provider(_intent, *, control_inputs, fO2_log, fe_redox_policy):
        result = provider.dispatch(
            IntentRequest(
                intent=ChemistryIntent.ELECTROLYSIS_STEP,
                account_view=view,
                temperature_C=1600.0,
                pressure_bar=1e-9,
                fO2_log=fO2_log,
                fe_redox_policy=fe_redox_policy,
                control_inputs=control_inputs,
            )
        )
        reductions.update(result.diagnostic.get("oxides_reduced_kg", {}))
        return SimpleNamespace(diagnostic=result.diagnostic, transition=None)

    sim._dispatch_only = dispatch_with_provider
    sim._ledger_account_species_kg = lambda _account, _species: 0.0
    sim._project_extraction_melt = lambda: None
    sim._sync_oxygen_kg_counters = lambda: None

    sim._step_mre()

    assert reductions.get("SiO2", 0.0) > 0.0
    assert reductions.get("Fe2O3", 0.0) > 0.0
    assert "FeO" not in reductions
