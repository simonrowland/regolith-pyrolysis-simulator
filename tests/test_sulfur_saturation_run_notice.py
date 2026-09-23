"""Run-level sulfur-saturation notice: visible, and not a second set of numbers."""

from __future__ import annotations

import json
from types import SimpleNamespace

from simulator.optimize.evaluate import _run_reference
from simulator.optimize.objective import compute_objectives
from simulator.optimize.profiles import load_profile
from simulator.run_executor import RunExecutor
from simulator.runner import PyrolysisRun


def _sulfur_result(status: str = "out_of_range", warnings: list[str] | None = None):
    return SimpleNamespace(
        calibration_status=status,
        warnings=list(warnings or ["SiO2 outside SCSS calibration window"]),
        SCSS_ppm=100.0,
        SCAS_ppm=200.0,
        S6_fraction=0.1,
        S_in_sulfide_ppm=10.0,
        S_in_sulfate_ppm=5.0,
    )


def _numbers(document: dict) -> dict:
    metadata = dict(document["run_metadata"])
    metadata.pop("sulfur_saturation_notice", None)
    metadata.pop("engine_commissioning_notice", None)
    metadata.pop("rump_expectation_notice", None)
    metadata.pop("started_at_utc", None)
    classification = dict(document["product_classification"])
    classification.pop("sulfur_saturation_notice", None)
    classification.pop("engine_commissioning_notice", None)
    classification.pop("rump_expectation_notice", None)
    classification.pop("markdown", None)
    return {
        "run_metadata": metadata,
        "final_state": document["final_state"],
        "per_hour_summary": document["per_hour_summary"],
        "classification": classification["classification"],
    }


def _summary_numbers(summary: object) -> object:
    payload = dict(summary)
    payload.pop("sulfur_saturation_notice", None)
    payload.pop("engine_commissioning_notice", None)
    payload.pop("rump_expectation_notice", None)
    return json.loads(json.dumps(payload, default=str))


def test_in_range_sulfsat_leaves_run_notice_absent() -> None:
    run = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        campaign="C0",
        hours=0,
        allow_fallback_vapor=True,
        allow_unmeasured_alpha_fallback=True,
    )
    session = run._start_session()
    sim = session.simulator
    assert sim.sulfur_saturation_run_notice() is None
    sim._note_sulfur_saturation_step(_sulfur_result("in_range"))
    assert sim.sulfur_saturation_run_notice() is None


def test_out_of_range_sulfsat_accumulates_and_does_not_change_numbers() -> None:
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
    # A live C0 hour may already stamp an unavailable/out_of_range SulfSat
    # notice; clear so the inject below is the sole provenance for the claim.
    baseline_count = int((sim.sulfur_saturation_run_notice() or {}).get("count") or 0)
    sim._sulfur_saturation_steps = []
    clean = run._build_output(execution)
    assert "sulfur_saturation_notice" not in clean["run_metadata"]
    clean_reference = _run_reference(
        execution,
        profile,
        decided_backend_status="out_of_domain",
    )
    assert "sulfur_saturation_notice" not in clean_reference.trace
    assert "sulfur_saturation_notice" not in clean_reference.product_summary
    clean_objectives = compute_objectives(profile, execution).as_mapping()
    clean_summary = _summary_numbers(clean_reference.product_summary)

    saved_hour = sim.melt.hour
    sim.melt.hour = 0
    sim._note_sulfur_saturation_step(_sulfur_result("out_of_range"))
    sim.melt.hour = 2
    sim._note_sulfur_saturation_step(
        _sulfur_result("unavailable", ["PySulfSat not installed"])
    )
    sim.melt.hour = saved_hour

    notice = sim.sulfur_saturation_run_notice()
    assert notice is not None
    assert notice["count"] == 2
    assert baseline_count >= 0
    assert notice["first_hour"] == 1
    assert notice["last_hour"] == 3
    assert [item["calibration_status"] for item in notice["notices"]] == [
        "out_of_range",
        "unavailable",
    ]
    assert "calibration_status" not in notice  # mixed statuses omit a single label

    flagged = run._build_output(execution)
    assert flagged["run_metadata"]["sulfur_saturation_notice"] == notice
    assert _numbers(flagged) == _numbers(clean)

    flagged_reference = _run_reference(
        execution,
        profile,
        decided_backend_status="out_of_domain",
    )
    assert flagged_reference.trace["sulfur_saturation_notice"] == notice
    assert flagged_reference.product_summary["sulfur_saturation_notice"] == notice
    assert _summary_numbers(flagged_reference.product_summary) == clean_summary
    assert compute_objectives(profile, execution).as_mapping() == clean_objectives
    assert "sulfur_saturation_notice" not in sim.product_ledger()


def test_attach_post_equilibrium_sulfsat_notes_out_of_range(monkeypatch) -> None:
    """Mutation proof: the post-eq hook must feed the run rollup."""
    run = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        campaign="C0",
        hours=0,
        allow_fallback_vapor=True,
        allow_unmeasured_alpha_fallback=True,
    )
    sim = run._start_session().simulator
    monkeypatch.setattr(sim, "_stage0_sulfur_input_ppm", lambda: 500.0)
    monkeypatch.setattr(sim, "_melt_oxide_wt_pct", lambda: {"SiO2": 45.0, "FeO": 10.0})
    monkeypatch.setattr(sim, "_current_melt_redox_fO2_log", lambda: -10.0)
    monkeypatch.setattr(sim, "_sync_oxygen_reservoir_mirror", lambda: None)
    monkeypatch.setattr(
        sim._sulfsat_gate,
        "compute_sulfur_saturation",
        lambda **kwargs: _sulfur_result("out_of_range"),
    )
    result = SimpleNamespace(warnings=[])
    sim.melt.hour = 0
    sim._attach_post_equilibrium_sulfsat(result)
    notice = sim.sulfur_saturation_run_notice()
    assert notice is not None
    assert notice["count"] == 1
    assert notice["notices"][0]["calibration_status"] == "out_of_range"
    assert any("SulfSat gate" in item for item in result.warnings)
