#!/usr/bin/env python3
"""Trace iron in the lunar C0-to-C6 metallothermic mass-balance run."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from simulator.state import CampaignPhase, DecisionType  # noqa: E402
from tests.chemistry.test_builtin_metallothermic_step_provider import (  # noqa: E402
    _TEST_OWNED_SIMULATORS,
    _build_sim,
    _hkl_only_setpoints,
)
from tests.chemistry.conftest import _load_yaml  # noqa: E402
from tests.magemin_test_support import close_test_owned_magemin_backend  # noqa: E402


def _metal_phase_fe_kg(sim) -> float:
    return float(
        sim.atom_ledger.project_account_kg("process.metal_phase").get("Fe", 0.0)
    )


def _melt_iron_oxide_mol(sim) -> tuple[float, float]:
    melt_mol = sim.atom_ledger.mol_by_account("process.cleaned_melt")
    return float(melt_mol.get("FeO", 0.0)), float(melt_mol.get("Fe2O3", 0.0))


def _metal_phase_fe_delta_kg(transition) -> float:
    return sum(
        float(lot.species_kg.get("Fe", 0.0))
        for lot in transition.credits
        if lot.account == "process.metal_phase"
    ) - sum(
        float(lot.species_kg.get("Fe", 0.0))
        for lot in transition.debits
        if lot.account == "process.metal_phase"
    )


def _row(sim, step: int, transitions=()) -> str:
    fO2_log = sim._current_melt_redox_fO2_log()
    feo_mol, fe2o3_mol = _melt_iron_oxide_mol(sim)
    moved = [
        f"{transition.name}:{_metal_phase_fe_delta_kg(transition):+.9g} kg"
        for transition in transitions
        if _metal_phase_fe_delta_kg(transition) != 0.0
    ]
    campaign = getattr(sim.melt.campaign, "value", sim.melt.campaign)
    return (
        f"step={step} hour={sim.melt.hour} campaign_hour={sim.melt.campaign_hour} "
        f"campaign={campaign} T_C={sim.melt.temperature_C:.9g} "
        f"source_fO2_log={fO2_log:.12g} "
        f"metal_phase_Fe_kg={_metal_phase_fe_kg(sim):.12g} "
        f"melt_FeO_mol={feo_mol:.12g} melt_Fe2O3_mol={fe2o3_mol:.12g} "
        f"Fe_transitions=[{'; '.join(moved)}]"
    )


def main() -> int:
    vapor_pressure_data = _load_yaml("vapor_pressures.yaml")
    feedstocks_data = _load_yaml("feedstocks.yaml")
    setpoints_data = _load_yaml("setpoints.yaml")
    patched_setpoints = _hkl_only_setpoints(setpoints_data)
    patched_setpoints["furnace_max_T_C"] = 1800.0
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        patched_setpoints,
        additives_kg={"K": 30.0, "Na": 25.0, "Mg": 60.0},
    )
    try:
        sim.start_campaign(CampaignPhase.C0)
        decision_choice = {
            DecisionType.ROOT_BRANCH: "pyrolysis",
            DecisionType.PATH_AB: "A",
            DecisionType.BRANCH_ONE_TWO: "two",
            DecisionType.C6_PROCEED: "yes",
        }
        step = 0
        print(_row(sim, step))
        while not sim.is_complete() and step < 5000:
            if sim.paused_for_decision:
                decision = sim.pending_decision
                choice = decision_choice.get(decision.decision_type)
                if choice not in (decision.options or []):
                    choice = (decision.options or [None])[0]
                sim.apply_decision(decision.decision_type, choice)
                continue
            transition_start = len(sim.atom_ledger.transitions)
            sim.step()
            step += 1
            new_transitions = sim.atom_ledger.transitions[transition_start:]
            print(_row(sim, step, new_transitions))
        if not sim.is_complete():
            print("ERROR: lunar C0-to-C6 run did not complete within 5000 steps")
            return 1
    finally:
        for owned_sim in _TEST_OWNED_SIMULATORS:
            close_test_owned_magemin_backend(owned_sim)
        _TEST_OWNED_SIMULATORS.clear()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
