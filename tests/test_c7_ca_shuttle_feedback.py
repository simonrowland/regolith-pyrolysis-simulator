from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from simulator.core import PyrolysisSimulator
from simulator.melt_backend.base import InternalAnalyticalBackend
from simulator.state import CampaignPhase
from tests.ledger_test_support import _transition_account_species_mol
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
                "rate_fraction": 0.5,
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
        al2o3_accounts = sim.atom_ledger.mol_by_account()
        initial_condensation_ca_mol = float(
            al2o3_accounts.get("process.condensation_train", {}).get("Ca", 0.0)
        )
        initial_al2o3_mol = sum(
            float(al2o3_accounts.get(account, {}).get("Al2O3", 0.0))
            for account in ("process.cleaned_melt", "terminal.slag")
        )
        transitions_before = len(sim.atom_ledger.transitions)

        sim._step_c7_ca_aluminothermic()

        capture_transition = next(
            transition
            for transition in sim.atom_ledger.transitions[transitions_before:]
            if transition.name == "ca_condenser_capture"
        )
        ledger_captured_ca_mol = _transition_account_species_mol(
            sim,
            capture_transition,
            side="credits",
            species="Ca",
            account="process.condensation_train",
        )
        diagnostic = sim._last_c7_diagnostic
        captured_ca_mol = float(diagnostic["captured_ca_mol"])
        ca_shuttle = setpoints["campaigns"]["C7"]["ca_shuttle"]
        al_credit_limit_kg = float(
            setpoints["campaigns"]["C7"]["al_credit_limit_kg"]
        )
        reserve_fraction = float(ca_shuttle["reserve_ca_product_fraction"])
        rate_fraction = float(ca_shuttle["rate_fraction"])
        assert captured_ca_mol == pytest.approx(ledger_captured_ca_mol)
        assert captured_ca_mol > 0.0
        assert rate_fraction > 0.0
        assert initial_condensation_ca_mol == pytest.approx(0.0)
        reduction_diagnostic = next(
            row
            for row in diagnostic["raw_diagnostics"]
            if row.get("operation") == "reduction"
        )
        assert reduction_diagnostic["limiting_cap"] == "recipe"
        assert reduction_diagnostic["al_spend_kg"] < al_credit_limit_kg

        # The fixture starts with no Ca in process.condensation_train. Extraction
        # moves the captured Ca there before dispatch; the provider reserves
        # min(available_train_Ca, ledger_captured_ca_mol * reserve_fraction),
        # which is ledger_captured_ca_mol * reserve_fraction for this fixture.
        # The C7 reduction's limiting cap is its 10% recipe extent_fraction; its
        # Al spend is below the 20 kg credit budget. It consumes CaO + Al,
        # credits Ca + slag, and leaves this profile's Al2O3 available. Thus
        # surplus Ca [mol] = captured Ca [mol] * (1 - 0.7), then rate-limited Ca
        # [mol] = surplus Ca [mol] * rate_fraction. For 3 Ca + Al2O3 -> 3 CaO +
        # 2 Al, extent [mol Al2O3] = min(rate-limited Ca [mol] / 3 [mol Ca / mol
        # Al2O3], available Al2O3 [mol]). Here available Al2O3 exceeds that
        # extent, so surplus Ca is binding; expected Ca and Al debits/credits
        # are 3*extent, extent, and 2*extent mol respectively.
        ca_after_reserve_mol = max(
            0.0, ledger_captured_ca_mol * (1.0 - reserve_fraction)
        )
        rate_limited_ca_mol = ca_after_reserve_mol * rate_fraction
        expected_extent_mol = min(
            rate_limited_ca_mol / 3.0,
            initial_al2o3_mol,
        )
        assert initial_al2o3_mol > rate_limited_ca_mol / 3.0
        expected_ca_debit_mol = 3.0 * expected_extent_mol
        expected_al2o3_debit_mol = expected_extent_mol
        expected_al_credit_mol = 2.0 * expected_extent_mol

        shuttle_transition = next(
            transition
            for transition in sim.atom_ledger.transitions[transitions_before:]
            if transition.name == "ca_shuttle_alumina_feedback"
        )
        assert _transition_account_species_mol(
            sim,
            shuttle_transition,
            side="debits",
            species="Ca",
            account="process.condensation_train",
        ) == pytest.approx(expected_ca_debit_mol)
        assert sum(
            _transition_account_species_mol(
                sim,
                shuttle_transition,
                side="debits",
                species="Al2O3",
                account=account,
            )
            for account in ("process.cleaned_melt", "terminal.slag")
        ) == pytest.approx(expected_al2o3_debit_mol)
        assert _transition_account_species_mol(
            sim,
            shuttle_transition,
            side="credits",
            species="Al",
            account="process.metal_phase",
        ) == pytest.approx(expected_al_credit_mol)
        shuttle_transition.validate_conservation(sim.species_formula_registry)
        assert sim.atom_ledger.assert_balanced()
    finally:
        close_test_owned_magemin_backend(sim)
