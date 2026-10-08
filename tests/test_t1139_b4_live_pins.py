"""Committed dormant-run evidence for the B4 insertion and activation."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from simulator.runner import PyrolysisRun
from tests.test_runner_smoke import SCENARIOS
from tests.test_t1139_b1_ledger_pins import B1_FEEDSTOCKS, _digest


PIN_PATH = Path(__file__).parent / "fixtures" / "t1139_b4_dormant.json"
SCENARIOS_B4 = [
    {"name": name, "feedstock_id": name, "campaign": "C0", "hours": 24,
     "additives_kg": {}}
    for name in B1_FEEDSTOCKS
] + SCENARIOS


def dormant_catalog(payload, families):
    restored = deepcopy(payload)
    restored["families"] = {
        key: value for key, value in restored["families"].items()
        if not key.startswith("t1139_")
    }
    restored["families"].update(deepcopy(families))
    return restored


def capture_run(scenario, monkeypatch):
    run = PyrolysisRun(
        feedstock_id=scenario["feedstock_id"], campaign=scenario["campaign"],
        hours=scenario["hours"], additives_kg=dict(scenario["additives_kg"]),
        allow_fallback_vapor=True, allow_unmeasured_alpha_fallback=True,
        run_metadata_overrides={"started_at_utc": "2026-05-15T00:00:00Z",
                                "kernel_commit_sha": "goal-18-fixture"},
    )
    session = run._start_session()
    sim = session.simulator
    pressure_samples = []
    original = sim._get_equilibrium

    def record_pressure():
        result = original()
        pressure_samples.append({
            "temperature_C": sim.melt.temperature_C,
            "pressure_pa": dict(result.vapor_pressures_Pa),
        })
        return result

    monkeypatch.setattr(sim, "_get_equilibrium", record_pressure)
    initial_mol = sim.atom_ledger.mol_by_account()
    payload = run._run_session(session)
    account_kg = sim.atom_ledger.kg_by_account()
    return {
        "status": payload["status"],
        "pressures": pressure_samples,
        "fluxes": [dict(s.evap_flux.species_kg_hr) for s in sim.record.snapshots],
        "initial_mol": initial_mol,
        "ledger_mol": sim.atom_ledger.mol_by_account(),
        "ledger_kg": account_kg,
        "ledger_totals_kg": {
            account: float(total)
            for account, total in sim.atom_ledger.total_kg_by_account().items()
        },
        "condenser_by_stage_species": {
            str(stage.stage_number): dict(stage.collected_kg)
            for stage in sim.train.stages
        },
        "coating_ledger": {
            key: value for key, value in account_kg.items()
            if "wall_deposit" in key
        },
        "wall_deltas": [
            {str(key): value for key, value in s.wall_deposit_by_segment_species_delta.items()}
            for s in sim.record.snapshots
        ],
    }, payload, sim


@pytest.mark.parametrize("scenario", SCENARIOS_B4, ids=lambda row: row["name"])
def test_dormant_outputs_are_pinned(scenario, monkeypatch):
    import simulator.config as config

    pins = json.loads(PIN_PATH.read_text(), parse_int=float)
    original = config._load_required_yaml

    def replay(path, **kwargs):
        payload, digest = original(path, **kwargs)
        if path.name == "vapor_pressures.yaml":
            payload = dormant_catalog(payload, pins["catalog_families"])
        return payload, digest

    monkeypatch.setattr(config, "_load_required_yaml", replay)
    actual, _payload, _sim = capture_run(scenario, monkeypatch)
    assert actual["status"] == "ok"
    expected = pins["runs"][scenario["name"]]
    for field, value in actual.items():
        assert _digest(value) == _digest(expected[field]), (scenario["name"], field)


def test_condensation_source_selection_is_pinned():
    from simulator.condensation import _trace_vapour_carrier_sources

    pins = json.loads(PIN_PATH.read_text())
    assert _trace_vapour_carrier_sources() == pins["trace_sources"]
