from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from simulator import mre_ladder
from simulator.core import PyrolysisSimulator
from simulator.melt_backend.base import StubBackend
from simulator.state import CampaignPhase


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


def test_c5_voltage_ladder_uses_yaml_branch_two_max_v():
    setpoints = _repo_setpoints()
    sequence = mre_ladder.build_mre_voltage_sequence(setpoints)

    c5_sequence = mre_ladder.c5_voltage_ladder(sequence, setpoints)

    assert mre_ladder.branch_two_voltage_cap(setpoints) == 1.6
    assert _species_names(c5_sequence) == [
        "Na2O",
        "K2O",
        "FeO",
        "Cr2O3",
        "MnO",
        "SiO2",
        "TiO2",
    ]
    assert all(entry["voltage"] <= 1.6 for entry in c5_sequence)


def test_c5_voltage_ladder_tracks_yaml_cap_changes():
    setpoints = _repo_setpoints()
    setpoints = deepcopy(setpoints)
    setpoints["mre_voltage_sequence"]["voltage_strategy"]["branch_two"][
        "max_V"
    ] = 1.4

    sequence = mre_ladder.build_mre_voltage_sequence(setpoints)
    c5_sequence = mre_ladder.c5_voltage_ladder(sequence, setpoints)

    assert mre_ladder.branch_two_voltage_cap(setpoints) == 1.4
    assert _species_names(c5_sequence) == [
        "Na2O",
        "K2O",
        "FeO",
        "Cr2O3",
        "MnO",
        "SiO2",
    ]


def test_step_mre_dispatch_uses_yaml_cap_instead_of_literal_1_6():
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
    sim.melt.campaign = CampaignPhase.C5
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
    assert captured["current_A"] == pytest.approx(100.0)
