from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from simulator import mre_ladder
from simulator.core import PyrolysisSimulator
from simulator.melt_backend.base import StubBackend
from simulator.runner import PyrolysisRun
from simulator.session import SimSession
from simulator.state import CampaignPhase, MeltState


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
        0.5,
        0.5,
        0.6,
        0.9,
        0.9,
        1.4,
        1.5,
        1.9,
        2.2,
        2.5,
    ]
    assert [entry["min_hold_hours"] for entry in sequence] == [
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

    assert _species_names(sequence)[:2] == ["Na2O", "K2O"]
    assert _species_names(sequence)[5:7] == ["SiO2", "TiO2"]
    assert sequence[5]["voltage"] == pytest.approx(1.4)
    assert sequence[6]["voltage"] == pytest.approx(1.5)


def test_max_voltage_for_target_uses_ladder_ground_truth():
    sequence = mre_ladder.parse_ladder_from_setpoints(_repo_setpoints())

    assert mre_ladder.max_voltage_for_target("SiO2", sequence) == pytest.approx(1.4)
    assert mre_ladder.max_voltage_for_target("TiO2", sequence) == pytest.approx(1.5)
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
    assert by_target["SiO2"]["mre_max_voltage_V"] == pytest.approx(1.4)
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

    def fake_dispatch(_intent, *, control_inputs):
        captured.update(control_inputs)
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


def test_step_mre_restricts_reducible_oxides_to_target_prefix():
    setpoints = {
        "campaigns": {},
        "mre_voltage_sequence": {
            "sequence": [
                {"species": "FeO", "decomposition_V": 0.6, "min_hold_hours": 0},
                {"species": "SiO2", "decomposition_V": 1.4, "min_hold_hours": 0},
                {"species": "TiO2", "decomposition_V": 1.5, "min_hold_hours": 0},
                {"species": "CaO", "decomposition_V": 2.5, "min_hold_hours": 0},
            ],
        },
    }
    sim = _sim(setpoints)
    sim._mre_voltage_sequence = sim._build_mre_voltage_sequence()
    sim.melt.campaign = CampaignPhase.C5
    sim.melt.c5_enabled = True
    sim.melt.mre_target_species = "SiO2"
    sim.melt.mre_max_voltage_V = 1.4
    captured: list[dict] = []

    def fake_dispatch(_intent, *, control_inputs):
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
    assert captured[0]["allowed_oxides"] == ["FeO", "SiO2"]
    assert captured[0]["voltage_V"] == pytest.approx(0.6)
