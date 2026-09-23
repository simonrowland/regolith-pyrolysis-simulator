"""Run-level rump-expectation notice: visible beside run/optimizer carriers."""

from __future__ import annotations

import json

from simulator.optimize.evaluate import _run_reference
from simulator.optimize.objective import compute_objectives
from simulator.optimize.profiles import load_profile
from simulator.run_executor import RunExecutor
from simulator.runner import PyrolysisRun


def _numbers(document: dict) -> dict:
    metadata = dict(document["run_metadata"])
    metadata.pop("rump_expectation_notice", None)
    metadata.pop("engine_commissioning_notice", None)
    metadata.pop("sulfur_saturation_notice", None)
    metadata.pop("started_at_utc", None)
    classification = dict(document["product_classification"])
    classification.pop("rump_expectation_notice", None)
    classification.pop("engine_commissioning_notice", None)
    classification.pop("sulfur_saturation_notice", None)
    classification.pop("markdown", None)
    return {
        "run_metadata": metadata,
        "final_state": document["final_state"],
        "per_hour_summary": document["per_hour_summary"],
        "classification": classification["classification"],
    }


def _summary_numbers(summary: object) -> object:
    payload = dict(summary)
    payload.pop("rump_expectation_notice", None)
    payload.pop("engine_commissioning_notice", None)
    payload.pop("sulfur_saturation_notice", None)
    return json.loads(json.dumps(payload, default=str))


def test_absent_rump_warnings_omit_run_notice() -> None:
    run = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        campaign="C0",
        hours=0,
        allow_fallback_vapor=True,
        allow_unmeasured_alpha_fallback=True,
    )
    sim = run._start_session().simulator
    assert sim._rump_expectation_warnings == []
    assert sim.rump_expectation_run_notice() is None


def test_rump_warning_reaches_run_and_optimizer_without_changing_numbers() -> None:
    run = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        campaign="C0",
        hours=1,
        allow_fallback_vapor=True,
        allow_unmeasured_alpha_fallback=True,
    )
    session = run._start_session()
    execution = RunExecutor().execute_session(session, hours=1)
    sim = execution.simulator
    profile = load_profile("lunar_mare_low_ti")

    clean = run._build_output(execution)
    assert "rump_expectation_notice" not in clean["run_metadata"]
    clean_reference = _run_reference(
        execution,
        profile,
        decided_backend_status="out_of_domain",
    )
    assert "rump_expectation_notice" not in clean_reference.trace
    assert "rump_expectation_notice" not in clean_reference.product_summary
    clean_objectives = compute_objectives(profile, execution).as_mapping()
    clean_summary = _summary_numbers(clean_reference.product_summary)

    warning = "C4 rump FeO below campaign expectation (0.12 wt% vs >= 1.0 wt%)"
    sim._rump_expectation_warnings.append(warning)
    sim._rump_expectation_warnings.append(warning)  # duplicate must collapse in notices list

    notice = sim.rump_expectation_run_notice()
    assert notice == {"warnings": [warning], "count": 2}

    flagged = run._build_output(execution)
    assert flagged["run_metadata"]["rump_expectation_notice"] == notice
    assert _numbers(flagged) == _numbers(clean)

    flagged_reference = _run_reference(
        execution,
        profile,
        decided_backend_status="out_of_domain",
    )
    assert flagged_reference.trace["rump_expectation_notice"] == notice
    assert flagged_reference.product_summary["rump_expectation_notice"] == notice
    assert _summary_numbers(flagged_reference.product_summary) == clean_summary
    assert compute_objectives(profile, execution).as_mapping() == clean_objectives
    assert "rump_expectation_notice" not in sim.product_ledger()


def test_capture_campaign_summary_feeds_rump_run_notice(monkeypatch) -> None:
    """Mutation proof: campaign capture must still populate the run rollup."""
    run = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        campaign="C0",
        hours=0,
        allow_fallback_vapor=True,
        allow_unmeasured_alpha_fallback=True,
    )
    sim = run._start_session().simulator
    warning = "rump mass below expectation"
    monkeypatch.setattr(
        sim,
        "_rump_expectation_diagnostic",
        lambda campaign_name: {"warning": warning, "campaign": campaign_name},
    )
    # Minimal campaign bookkeeping so capture can run.
    sim._campaign_start_hour = int(sim.melt.hour)
    sim._campaign_start_mass = float(sim.melt.total_mass_kg)
    sim._campaign_start_condensation = dict(sim.train.total_by_species())
    sim._campaign_start_electrical_plus_evaporation_energy = float(
        sim.energy_electrical_plus_evaporation_cumulative_kWh
    )
    sim._campaign_start_O2 = float(sim._oxygen_total_kg())
    summary = sim._capture_campaign_summary("C4")
    assert summary["rump_expectation"]["warning"] == warning
    assert warning in sim._rump_expectation_warnings
    notice = sim.rump_expectation_run_notice()
    assert notice is not None
    assert warning in notice["warnings"]
