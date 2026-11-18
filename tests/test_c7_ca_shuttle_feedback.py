from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from simulator.core import PyrolysisSimulator
from simulator.melt_backend.base import InternalAnalyticalBackend
from simulator.state import CampaignPhase
from tests.magemin_test_support import close_test_owned_magemin_backend

ROOT = Path(__file__).resolve().parents[1]


def _load_yaml(name: str) -> dict:
    return yaml.safe_load((ROOT / "data" / name).read_text())


def test_c7_ca_shuttle_feedback_commits_surplus_from_real_profile() -> None:
    setpoints = copy.deepcopy(_load_yaml("setpoints.yaml"))
    setpoints["campaigns"]["C7"].update(
        {
            "enabled": True,
            "al_credit_limit_kg": 20.0,
            "extent_fraction": 0.1,
            "ca_shuttle": {
                "enabled": True,
                "rate_fraction": 1.0,
                "reserve_ca_product_fraction": 0.7,
                "targets": ["Al2O3"],
            },
        }
    )
    sim = PyrolysisSimulator(
        InternalAnalyticalBackend(),
        setpoints,
        _load_yaml("feedstocks.yaml"),
        _load_yaml("vapor_pressures.yaml"),
    )
    try:
        sim.load_batch("targeted_super_kreep_ore", mass_kg=1000.0)
        sim.start_campaign(CampaignPhase.C7_CA_ALUMINOTHERMIC)
        sim.melt.temperature_C = 1200.0
        # Keep the C7 transport calculation in its authorized viscous regime.
        sim.overhead_model.pipe_diameter_m = 1.0
        transitions_before = len(sim.atom_ledger.transitions)

        sim._step_c7_ca_aluminothermic()

        diagnostic = sim._last_c7_diagnostic
        assert diagnostic["captured_ca_mol"] > 0.0
        assert diagnostic["shuttle_drawn_ca_mol"] > 0.0
        shuttle_diagnostic = next(
            row
            for row in diagnostic["raw_diagnostics"]
            if row.get("operation") == "ca_shuttle_alumina_feedback"
        )
        assert shuttle_diagnostic["ca_shuttle_rate_fraction"] > 0.0
        shuttle_transition = next(
            transition
            for transition in sim.atom_ledger.transitions[transitions_before:]
            if transition.name == "ca_shuttle_alumina_feedback"
        )
        ca_debit_kg = sum(
            float(lot.species_kg.get("Ca", 0.0))
            for lot in shuttle_transition.debits
            if lot.account == "process.condensation_train"
        )
        assert ca_debit_kg == pytest.approx(diagnostic["ca_shuttle_drawn_kg"])
        shuttle_transition.validate_conservation(sim.species_formula_registry)
        assert sim.atom_ledger.assert_balanced()
    finally:
        close_test_owned_magemin_backend(sim)
