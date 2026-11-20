"""Trace melt-metal floor binders and ledger oxygen on the requested paths.

Run from the repository root with ``python3 probe_r7d_floor_binders.py``.
The probe deliberately reports production resolver output; it does not
reimplement the Ellingham or melt-activity calculation.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from simulator.physical_constants import CELSIUS_TO_KELVIN_OFFSET
from simulator.state import CampaignPhase, DecisionType
from tests.chemistry.conftest import _build_sim, _load_yaml
from tests.chemistry.test_builtin_metallothermic_step_provider import (
    _hkl_only_setpoints,
)


def _floor_record(sim, *, case: str, step: int | None = None) -> dict[str, Any]:
    temperature_K = float(sim.melt.temperature_C) + CELSIUS_TO_KELVIN_OFFSET
    floor = sim._melt_metal_saturation_fO2_floor_log(temperature_K)
    return {
        "kind": "floor",
        "case": case,
        "step": step,
        "global_hour": int(sim.melt.hour),
        "campaign": sim.melt.campaign.name,
        "campaign_hour": float(sim.melt.campaign_hour),
        "temperature_K": temperature_K,
        "reservoir_fO2_log": float(
            sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log
        ),
        "binding_oxide": None if floor is None else floor[1],
        "floor_fO2_log": None if floor is None else floor[0],
    }


def _oxygen_transition_record(transition, registry) -> dict[str, Any] | None:
    debited_o_mol = float(
        transition.debit_atom_moles(registry).get("O", 0.0) or 0.0
    )
    credited_o_mol = float(
        transition.credit_atom_moles(registry).get("O", 0.0) or 0.0
    )
    if debited_o_mol == 0.0 and credited_o_mol == 0.0:
        return None
    return {
        "name": transition.name,
        "reason": transition.reason,
        "debit_O_atom_mol": debited_o_mol,
        "credit_O_atom_mol": credited_o_mol,
        "debits": [
            {"account": lot.account, "species_kg": dict(lot.species_kg)}
            for lot in transition.debits
        ],
        "credits": [
            {"account": lot.account, "species_kg": dict(lot.species_kg)}
            for lot in transition.credits
        ],
    }


def _emit_step(sim, *, case: str, step: int, first_transition: int) -> None:
    print(json.dumps(_floor_record(sim, case=case, step=step), sort_keys=True))
    transitions = sim.atom_ledger.transitions[first_transition:]
    for transition in transitions:
        record = _oxygen_transition_record(transition, sim.atom_ledger.registry)
        if record is not None:
            print(json.dumps({"kind": "oxygen_transition", "case": case,
                              "step": step, **record}, sort_keys=True))
    source_terms = dict(
        getattr(
            sim.melt.oxygen_reservoir,
            "redox_source_terms_mol_o2_equiv",
            {},
        )
        or {}
    )
    if source_terms:
        print(json.dumps({"kind": "redox_sources", "case": case,
                          "step": step, "terms_mol_o2_equiv": source_terms},
                         sort_keys=True))


def _drive_lunar_path(
    *,
    case: str,
    path_choice: str,
    vapor_pressure_data: dict,
    feedstocks_data: dict,
    setpoints_data: dict,
) -> None:
    patched = _hkl_only_setpoints(copy.deepcopy(setpoints_data))
    patched["furnace_max_T_C"] = 1800.0
    c6 = patched["campaigns"]["C6"]
    c6["default_hold_T_C"] = 1400.0
    c6["hold_temp_C"] = 1400.0
    c6["kinetic_driven_above_crossover"] = False
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        patched,
        additives_kg={"K": 30.0, "Na": 25.0, "Mg": 60.0},
    )
    sim.start_campaign(CampaignPhase.C0)
    choices = {
        DecisionType.ROOT_BRANCH: "pyrolysis",
        DecisionType.PATH_AB: path_choice,
        DecisionType.BRANCH_ONE_TWO: "two",
        DecisionType.C6_PROCEED: "yes",
    }
    steps = 0
    while not sim.is_complete() and steps < 5000:
        if sim.paused_for_decision:
            decision = sim.pending_decision
            choice = choices.get(decision.decision_type)
            if choice not in (decision.options or []):
                choice = (decision.options or [None])[0]
            sim.apply_decision(decision.decision_type, choice)
            continue
        first_transition = len(sim.atom_ledger.transitions)
        sim.step()
        steps += 1
        _emit_step(
            sim,
            case=case,
            step=steps,
            first_transition=first_transition,
        )
    print(json.dumps({"kind": "run_end", "case": case, "steps": steps,
                      "complete": bool(sim.is_complete()),
                      "campaign": sim.melt.campaign.name,
                      "campaign_hour": float(sim.melt.campaign_hour)},
                     sort_keys=True))


def _sample_shipped_feedstocks(
    *,
    vapor_pressure_data: dict,
    feedstocks_data: dict,
    setpoints_data: dict,
) -> None:
    c2a_cfg = setpoints_data["campaigns"]["C2A_continuous"]
    c2a_range = c2a_cfg["temp_range_C"]
    c2a_temperature_C = 0.5 * (float(c2a_range[0]) + float(c2a_range[1]))
    c3_cfg = setpoints_data["campaigns"]["C3"]
    c3_na = c3_cfg["Na_phase"]
    c3_temperatures_C = (
        0.5 * (float(c3_na["T_inject_C"][0]) + float(c3_na["T_inject_C"][1])),
        0.5 * (float(c3_na["T_bakeout_C"][0]) + float(c3_na["T_bakeout_C"][1])),
    )
    for feedstock_key in sorted(feedstocks_data):
        for campaign, temperatures_C in (
            (CampaignPhase.C2A, (c2a_temperature_C,)),
            (CampaignPhase.C3_NA, c3_temperatures_C),
        ):
            for temperature_C in temperatures_C:
                sim = _build_sim(
                    feedstock_key,
                    vapor_pressure_data,
                    feedstocks_data,
                    _hkl_only_setpoints(copy.deepcopy(setpoints_data)),
                )
                sim.melt.campaign = campaign
                sim.melt.campaign_hour = 1.0
                sim.melt.temperature_C = temperature_C
                print(json.dumps({
                    "kind": "feedstock_sample",
                    "feedstock": feedstock_key,
                    **_floor_record(sim, case="feedstock_sample"),
                }, sort_keys=True))


def main() -> None:
    root = Path.cwd()
    required = (
        root / "data" / "feedstocks.yaml",
        root / "data" / "setpoints.yaml",
        root / "data" / "vapor_pressures.yaml",
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError(
            "run probe_r7d_floor_binders.py from the repository root; "
            f"missing: {missing}"
        )
    feedstocks_data = _load_yaml("feedstocks.yaml")
    setpoints_data = _load_yaml("setpoints.yaml")
    vapor_pressure_data = _load_yaml("vapor_pressures.yaml")

    _sample_shipped_feedstocks(
        vapor_pressure_data=vapor_pressure_data,
        feedstocks_data=feedstocks_data,
        setpoints_data=setpoints_data,
    )
    _drive_lunar_path(
        case="lunar_fullrun_path_a",
        path_choice="A",
        vapor_pressure_data=vapor_pressure_data,
        feedstocks_data=feedstocks_data,
        setpoints_data=setpoints_data,
    )
    _drive_lunar_path(
        case="lunar_staged_c3_hour3_and_c6_hold",
        path_choice="A_staged",
        vapor_pressure_data=vapor_pressure_data,
        feedstocks_data=feedstocks_data,
        setpoints_data=setpoints_data,
    )


if __name__ == "__main__":
    main()
