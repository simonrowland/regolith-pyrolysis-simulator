from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from simulator.core import PyrolysisSimulator
from simulator.melt_backend.base import StubBackend
from simulator.optimize.evaluate import evaluate
from simulator.optimize.physics import PhysicsConstraintSet
from simulator.optimize.recipe import RecipePatch
from simulator.state import Atmosphere, CampaignPhase


BASE_PROFILE = {
    "profile_id": "mre-policy-tc8",
    "profile_schema_version": "profile-schema-v1",
    "feedstock": "lunar_mare_low_ti",
    "objectives": [
        {
            "metric": "oxygen_kg",
            "sense": "max",
            "units": "kg",
            "weight": 0.7,
            "rationale": "test oxygen objective evidence",
        },
        {
            "metric": "energy_kWh",
            "sense": "min",
            "units": "kWh",
            "weight": 0.3,
            "rationale": "test energy objective evidence",
        },
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


def _profile(
    *,
    c5_enabled: bool,
    target_species: str = "SiO2",
    max_voltage_V: float = 1.45,
    hours: int = 15,
) -> dict:
    profile = deepcopy(BASE_PROFILE)
    profile["run"]["hours"] = hours
    profile["fidelities"]["fast"]["hours"] = hours
    profile["run"].update(
        {
            "c5_enabled": c5_enabled,
            "mre_target_species": target_species if c5_enabled else "",
            "mre_max_voltage_V": max_voltage_V if c5_enabled else 0.0,
        }
    )
    return profile


def _evaluate_policy(
    *,
    c5_enabled: bool,
    target_species: str = "SiO2",
    max_voltage_V: float = 1.45,
    hours: int = 15,
):
    return evaluate(
        RecipePatch({}),
        "lunar_mare_low_ti",
        "fast",
        profile=_profile(
            c5_enabled=c5_enabled,
            target_species=target_species,
            max_voltage_V=max_voltage_V,
            hours=hours,
        ),
        constraints=PhysicsConstraintSet(),
    )


def _product_ledger(result) -> dict[str, float]:
    return dict(result.run_reference.product_summary["product_ledger_kg"])


def _captured_c5_voltages(*, target_species: str, max_voltage_V: float) -> list[float]:
    setpoints = {
        "campaigns": {},
        "mre_voltage_sequence": {
            "sequence": [
                {"species": "FeO", "decomposition_V": 0.75, "min_hold_hours": 0},
                {"species": "SiO2", "decomposition_V": 1.45, "min_hold_hours": 0},
                {"species": "TiO2", "decomposition_V": 1.70, "min_hold_hours": 0},
            ],
        },
    }
    backend = StubBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(
        backend,
        setpoints,
        {"x": {"label": "X", "composition_wt_pct": {"SiO2": 100}}},
        {"metals": {}, "oxide_vapors": {}},
    )
    sim._mre_voltage_sequence = sim._build_mre_voltage_sequence()
    sim.melt.campaign = CampaignPhase.C5
    sim.melt.c5_enabled = True
    sim.melt.mre_target_species = target_species
    sim.melt.mre_max_voltage_V = max_voltage_V
    captured: list[float] = []

    def fake_dispatch(_intent, *, control_inputs):
        captured.append(control_inputs["voltage_V"])
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
    for _ in range(6):
        sim._step_mre()
    return captured


def test_tc8_si_target_mre_policy_splits_cache_key_and_stub_outcome() -> None:
    off = _evaluate_policy(c5_enabled=False)
    si_target = _evaluate_policy(c5_enabled=True)

    assert off.cache_key != si_target.cache_key
    assert off.eval_spec.c5_enabled is False
    assert off.eval_spec.mre_target_species == ""
    assert off.eval_spec.mre_max_voltage_V == pytest.approx(0.0)
    assert si_target.eval_spec.c5_enabled is True
    assert si_target.eval_spec.mre_target_species == "SiO2"
    assert si_target.eval_spec.mre_max_voltage_V == pytest.approx(1.45)

    assert _product_ledger(off) == {}
    assert _product_ledger(si_target) != _product_ledger(off)
    assert _product_ledger(si_target)["Na"] > 0.0
    assert _product_ledger(si_target)["K"] > 0.0

    off_trace = off.run_reference.trace
    si_trace = si_target.run_reference.trace
    assert max(snapshot.mre_current_A for snapshot in off_trace.snapshots) == pytest.approx(0.0)
    assert max(snapshot.mre_current_A for snapshot in si_trace.snapshots) > 0.0
    assert max(snapshot.mre_voltage_V for snapshot in si_trace.snapshots) <= 1.45


def test_tc8_si_and_ti_targets_split_c5_behavior_not_only_cache_key() -> None:
    si_voltages = _captured_c5_voltages(
        target_species="SiO2", max_voltage_V=1.45
    )
    ti_voltages = _captured_c5_voltages(
        target_species="TiO2", max_voltage_V=1.70
    )

    assert max(si_voltages) == pytest.approx(1.45)
    assert max(ti_voltages) == pytest.approx(1.70)
    assert 1.70 not in si_voltages
    assert ti_voltages != si_voltages


def test_c5_mre_dispatch_uses_live_o2_backpressure() -> None:
    setpoints = {
        "campaigns": {},
        "mre_voltage_sequence": {
            "sequence": [
                {"species": "SiO2", "decomposition_V": 1.45, "min_hold_hours": 0},
            ],
        },
    }
    backend = StubBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(
        backend,
        setpoints,
        {"x": {"label": "X", "composition_wt_pct": {"SiO2": 100}}},
        {"metals": {}, "oxide_vapors": {}},
    )
    sim._mre_voltage_sequence = sim._build_mre_voltage_sequence()
    sim.melt.campaign = CampaignPhase.C5
    sim.melt.atmosphere = Atmosphere.O2_BACKPRESSURE
    sim.melt.pO2_mbar = 50.0
    sim.melt.p_total_mbar = 50.0
    sim.melt.c5_enabled = True
    sim.melt.mre_target_species = "SiO2"
    sim.melt.mre_max_voltage_V = 1.45
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
    assert captured[0]["pO2_bar"] == pytest.approx(0.05)


@pytest.mark.parametrize("c5_enabled", (False, True))
def test_tc8_stub_path_mass_balance_closes_for_mre_policy(c5_enabled: bool) -> None:
    result = _evaluate_policy(c5_enabled=c5_enabled)
    snapshots = result.run_reference.trace.snapshots

    assert snapshots
    assert max(abs(snapshot.mass_balance_error_pct) for snapshot in snapshots) < 1e-9
