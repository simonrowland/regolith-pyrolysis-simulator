from __future__ import annotations

from copy import deepcopy

import pytest

from simulator.optimize.evaluate import evaluate
from simulator.optimize.physics import PhysicsConstraintSet
from simulator.optimize.recipe import RecipePatch


BASE_PROFILE = {
    "profile_id": "mre-policy-tc8",
    "profile_schema_version": "profile-schema-v1",
    "feedstock": "lunar_mare_low_ti",
    "objectives": [
        {"metric": "oxygen_kg", "sense": "max", "units": "kg", "weight": 0.7},
        {"metric": "energy_kWh", "sense": "min", "units": "kWh", "weight": 0.3},
    ],
    "constraints": {"gates": ["delivered_stream_purity"]},
    "run": {
        "campaign": "C5",
        "hours": 15,
        "mass_kg": 1000.0,
        "backend_name": "stub",
    },
    "fidelities": {"fast": {"backend_name": "stub", "hours": 15}},
    "seed_recipes": [{"id": "seed", "source_campaign": "C5", "patch": {}}],
}


def _profile(*, c5_enabled: bool) -> dict:
    profile = deepcopy(BASE_PROFILE)
    profile["run"].update(
        {
            "c5_enabled": c5_enabled,
            "mre_target_species": "SiO2" if c5_enabled else "",
            "mre_max_voltage_V": 1.4 if c5_enabled else 0.0,
        }
    )
    return profile


def _evaluate_policy(*, c5_enabled: bool):
    return evaluate(
        RecipePatch({}),
        "lunar_mare_low_ti",
        "fast",
        profile=_profile(c5_enabled=c5_enabled),
        constraints=PhysicsConstraintSet(),
    )


def _product_ledger(result) -> dict[str, float]:
    return dict(result.run_reference.product_summary["product_ledger_kg"])


def test_tc8_si_target_mre_policy_splits_cache_key_and_stub_outcome() -> None:
    off = _evaluate_policy(c5_enabled=False)
    si_target = _evaluate_policy(c5_enabled=True)

    assert off.cache_key != si_target.cache_key
    assert off.eval_spec.c5_enabled is False
    assert off.eval_spec.mre_target_species == ""
    assert off.eval_spec.mre_max_voltage_V == pytest.approx(0.0)
    assert si_target.eval_spec.c5_enabled is True
    assert si_target.eval_spec.mre_target_species == "SiO2"
    assert si_target.eval_spec.mre_max_voltage_V == pytest.approx(1.4)

    assert _product_ledger(off) == {}
    assert _product_ledger(si_target) != _product_ledger(off)
    assert _product_ledger(si_target)["Na"] > 0.0
    assert _product_ledger(si_target)["K"] > 0.0

    off_trace = off.run_reference.trace
    si_trace = si_target.run_reference.trace
    assert max(snapshot.mre_current_A for snapshot in off_trace.snapshots) == pytest.approx(0.0)
    assert max(snapshot.mre_current_A for snapshot in si_trace.snapshots) > 0.0
    assert max(snapshot.mre_voltage_V for snapshot in si_trace.snapshots) <= 1.4


@pytest.mark.parametrize("c5_enabled", (False, True))
def test_tc8_stub_path_mass_balance_closes_for_mre_policy(c5_enabled: bool) -> None:
    result = _evaluate_policy(c5_enabled=c5_enabled)
    snapshots = result.run_reference.trace.snapshots

    assert snapshots
    assert max(abs(snapshot.mass_balance_error_pct) for snapshot in snapshots) < 1e-9
