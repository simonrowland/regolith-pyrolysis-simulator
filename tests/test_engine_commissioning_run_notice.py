"""Run-level engine commissioning notice: visible, and not a second set of numbers."""

from __future__ import annotations

import json
from types import SimpleNamespace

from simulator.optimize.evaluate import _run_reference
from simulator.optimize.objective import compute_objectives
from simulator.optimize.profiles import load_profile
from simulator.run_executor import RunExecutor
from simulator.runner import PyrolysisRun

_BAND = {
    "sio2_wt_pct": [30.0, 80.0],
    "temperature_K": [1073.15, 1700.0],
}


def _step_notice(
    reason: str = "temperature_range",
    failed: tuple[str, ...] = ("temperature_range",),
) -> dict:
    return {
        "kind": "engine_commissioning",
        "reason": reason,
        "authority": "extrapolated",
        "certified_band": {
            "sio2_wt_pct": list(_BAND["sio2_wt_pct"]),
            "temperature_K": list(_BAND["temperature_K"]),
        },
        "failed_constraints": list(failed),
        "warnings": ["out of certified band"],
        "sio2_wt_pct": 45.8,
        "temperature_K": 1773.15,
    }


def _result(notice: dict | None) -> SimpleNamespace:
    diagnostics: dict = {}
    if notice is not None:
        diagnostics = {
            "commissioning_notice": notice,
            "authority": notice["authority"],
            "certified_band": notice["certified_band"],
        }
    return SimpleNamespace(
        status="ok",
        diagnostics=diagnostics,
        warnings=[],
        vapor_pressures_Pa={},
        vapor_pressures_source={},
        liquid_fraction=1.0,
    )


def _numbers(document: dict) -> dict:
    metadata = dict(document["run_metadata"])
    metadata.pop("engine_commissioning_notice", None)
    metadata.pop("started_at_utc", None)
    classification = dict(document["product_classification"])
    classification.pop("engine_commissioning_notice", None)
    classification.pop("markdown", None)
    return {
        "run_metadata": metadata,
        "final_state": document["final_state"],
        "per_hour_summary": document["per_hour_summary"],
        "classification": classification["classification"],
    }


def _summary_numbers(summary: object) -> object:
    payload = dict(summary)
    payload.pop("engine_commissioning_notice", None)
    return json.loads(json.dumps(payload, default=str))


def test_equilibrium_status_accumulates_only_out_of_band_steps() -> None:
    run = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        campaign="C0",
        hours=0,
        allow_fallback_vapor=True,
        allow_unmeasured_alpha_fallback=True,
    )
    session = run._start_session()
    sim = session.simulator
    assert sim.melt.hour == 0
    assert sim.engine_commissioning_run_notice() is None

    out_of_band = _result(_step_notice())
    assert sim._record_equilibrium_status(out_of_band) is out_of_band
    sim._record_equilibrium_status(_result(None))
    sim._record_equilibrium_status(_result(_step_notice()))
    sim.melt.hour = 4
    sim._record_equilibrium_status(
        _result(_step_notice("silicate_network_band", ("silicate_network_band",)))
    )

    notice = sim.engine_commissioning_run_notice()
    assert notice is not None
    assert notice["authority"] == "extrapolated"
    assert notice["count"] == 3
    assert notice["first_hour"] == 1
    assert notice["last_hour"] == 5
    assert [item["reason"] for item in notice["notices"]] == [
        "silicate_network_band",
        "temperature_range",
    ]
    assert notice["notices"][1]["certified_band"] == _BAND
    assert "engine_commissioning_notice" not in sim.product_ledger()


def test_in_band_run_is_clean_and_the_notice_does_not_change_numbers() -> None:
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
    assert "engine_commissioning_notice" not in clean["run_metadata"]
    assert "engine_commissioning_notice" not in clean["product_classification"]
    assert "Engine commissioning" not in clean["product_classification"]["markdown"]
    clean_reference = _run_reference(
        execution,
        profile,
        decided_backend_status="out_of_domain",
    )
    assert "engine_commissioning_notice" not in clean_reference.trace
    assert "engine_commissioning_notice" not in clean_reference.product_summary
    assert clean_reference.backend_status == "out_of_domain"
    clean_objectives = compute_objectives(profile, execution).as_mapping()
    clean_summary = _summary_numbers(clean_reference.product_summary)

    saved_diagnostics = dict(sim._last_backend_diagnostics)
    saved_hour = sim.melt.hour
    sim.melt.hour = 0
    sim._last_backend_diagnostics = {
        **saved_diagnostics,
        "commissioning_notice": _step_notice(),
        "authority": "extrapolated",
        "certified_band": _step_notice()["certified_band"],
    }
    sim._note_engine_commissioning_from_last_diagnostics()
    sim._last_backend_diagnostics = saved_diagnostics
    sim.melt.hour = saved_hour

    flagged = run._build_output(execution)
    notice = flagged["run_metadata"]["engine_commissioning_notice"]
    assert notice["authority"] == "extrapolated"
    assert notice["count"] == 1
    assert notice["first_hour"] == 1
    assert notice["last_hour"] == 1
    assert notice["notices"][0]["reason"] == "temperature_range"
    assert notice["notices"][0]["certified_band"] == _BAND
    assert flagged["product_classification"]["engine_commissioning_notice"] == notice
    markdown = flagged["product_classification"]["markdown"]
    totals_at = markdown.index("**Class totals**:")
    notice_at = markdown.index("**Engine commissioning**:")
    assert totals_at < notice_at
    assert "temperature_range" in markdown
    assert "Reported numbers are unchanged." in markdown
    assert _numbers(flagged) == _numbers(clean)

    flagged_reference = _run_reference(
        execution,
        profile,
        decided_backend_status="out_of_domain",
    )
    assert flagged_reference.backend_status == "out_of_domain"
    assert flagged_reference.trace["engine_commissioning_notice"] == notice
    assert flagged_reference.product_summary["engine_commissioning_notice"] == notice
    assert _summary_numbers(flagged_reference.product_summary) == clean_summary
    assert compute_objectives(profile, execution).as_mapping() == clean_objectives
    assert "engine_commissioning_notice" not in sim.product_ledger()
