from __future__ import annotations

from copy import deepcopy

import pytest

from simulator.config import load_config_bundle
from simulator.session import SimSession, SimSessionConfig


def test_session_snapshot_after_fractional_step_uses_committed_interval() -> None:
    bundle = load_config_bundle()
    setpoints = deepcopy(bundle.setpoints)
    setpoints["mre_voltage_sequence"] = {
        "sequence": [
            {
                "species": "FeO",
                "decomposition_V": 0.8,
                "min_hold_hours": 1,
            }
        ]
    }
    setpoints.setdefault("chemistry_kernel", {}).update(
        liquid_fraction_provider="analytical",
        allow_fallback_vapor=True,
        allow_unmeasured_alpha_fallback=True,
    )
    session = SimSession().start(
        SimSessionConfig(
            feedstock_id="fe-only",
            feedstocks={
                "fe-only": {
                    "label": "FeO",
                    "composition_wt_pct": {"FeO": 100.0},
                }
            },
            setpoints=setpoints,
            vapor_pressures=bundle.vapor_pressures,
            materials=bundle.materials,
            campaign="C5",
            backend_name="internal-analytical",
            c5_enabled=True,
            mre_target_species="FeO",
            mre_max_voltage_V=0.8,
        )
    )
    sim = session.simulator
    sim.melt.temperature_C = 1600.0
    first_transition = len(sim.atom_ledger.transitions)

    step_snapshot = session.advance(dt_hr=0.25).snapshot
    produced_kg = sum(
        lot.species_kg.get("Fe", 0.0)
        for transition in sim.atom_ledger.transitions[first_transition:]
        if transition.name == "mre_electrolysis_reduction"
        for lot in transition.credits
        if lot.account == "process.metal_phase"
    )
    queried_snapshot = session.snapshot()

    assert produced_kg > 0.0
    assert step_snapshot.duration_h == pytest.approx(0.25)
    assert queried_snapshot.duration_h == pytest.approx(0.25)
    assert step_snapshot.mre_metals_kg_hr["Fe"] == pytest.approx(
        produced_kg / 0.25
    )
    assert queried_snapshot.mre_metals_kg_hr["Fe"] == pytest.approx(
        produced_kg / 0.25
    )
