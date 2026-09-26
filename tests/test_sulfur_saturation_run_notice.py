"""Run-level sulfur-saturation notice: visible, and not a second set of numbers."""

from __future__ import annotations

import json
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from simulator.optimize.evaluate import _run_reference
from simulator.optimize.objective import compute_objectives
from simulator.optimize.profiles import load_profile
from simulator.melt_backend.base import InternalAnalyticalBackend
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
    """A 1700 K melt keeps the existing out-of-range notice."""
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
    captured = {}

    def fake_sulfsat(**kwargs):
        captured.update(kwargs)
        return _sulfur_result("out_of_range")

    monkeypatch.setattr(
        sim._sulfsat_gate,
        "compute_sulfur_saturation",
        fake_sulfsat,
    )
    sim.melt.temperature_C = 1700.0 - 273.15
    result = SimpleNamespace(warnings=[], liquid_fraction=1.0)
    sim.melt.hour = 0
    sim._attach_post_equilibrium_sulfsat(result)
    assert captured["T_K"] == pytest.approx(1700.0)
    notice = sim.sulfur_saturation_run_notice()
    assert notice is not None
    assert notice["count"] == 1
    assert notice["notices"][0]["calibration_status"] == "out_of_range"
    assert any("SulfSat gate" in item for item in result.warnings)


def test_attach_post_equilibrium_sulfsat_computes_for_1300k_melt(monkeypatch) -> None:
    run = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        campaign="C0",
        hours=0,
        allow_fallback_vapor=True,
        allow_unmeasured_alpha_fallback=True,
    )
    sim = run._start_session().simulator
    monkeypatch.setattr(sim, "_stage0_sulfur_input_ppm", lambda: 500.0)
    monkeypatch.setattr(sim, "_melt_oxide_wt_pct", lambda: {"SiO2": 45.0})
    monkeypatch.setattr(sim, "_current_melt_redox_fO2_log", lambda: -10.0)
    monkeypatch.setattr(sim, "_sync_oxygen_reservoir_mirror", lambda: None)
    captured = {}

    def fake_sulfsat(**kwargs):
        captured.update(kwargs)
        return _sulfur_result("in_range")

    monkeypatch.setattr(
        sim._sulfsat_gate,
        "compute_sulfur_saturation",
        fake_sulfsat,
    )
    sim.melt.temperature_C = 1300.0 - 273.15
    result = SimpleNamespace(warnings=[], liquid_fraction=1.0)

    sim._attach_post_equilibrium_sulfsat(result)

    assert captured["T_K"] == pytest.approx(1300.0)
    assert result.sulfur_saturation.SCAS_ppm == pytest.approx(200.0)
    assert sim.sulfur_saturation_run_notice() is None


def test_internal_analytical_none_uses_melt_redox_signal_at_1773k(monkeypatch) -> None:
    """A None analytical fraction must not suppress a hot SulfSat check."""
    run = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        campaign="C0",
        hours=0,
        allow_fallback_vapor=True,
        allow_unmeasured_alpha_fallback=True,
    )
    sim = run._start_session().simulator
    assert isinstance(sim.backend, InternalAnalyticalBackend)
    monkeypatch.setattr(sim, "_stage0_sulfur_input_ppm", lambda: 500.0)
    monkeypatch.setattr(sim, "_melt_oxide_wt_pct", lambda: {"SiO2": 45.0})
    monkeypatch.setattr(sim, "_current_melt_redox_fO2_log", lambda: -10.0)
    monkeypatch.setattr(sim, "_sync_oxygen_reservoir_mirror", lambda: None)
    captured = {}

    def fake_sulfsat(**kwargs):
        captured.update(kwargs)
        return _sulfur_result(
            "out_of_range",
            [
                "T_K=1773.00 K outside Chowdhury-Dasgupta 2019 SCAS "
                "calibration window [1023.0, 1598.0]"
            ],
        )

    monkeypatch.setattr(
        sim._sulfsat_gate,
        "compute_sulfur_saturation",
        fake_sulfsat,
    )
    sim.melt.temperature_C = 1773.0 - 273.15
    result = sim._internal_analytical_equilibrium()

    sim._attach_post_equilibrium_sulfsat(result)

    assert captured["T_K"] == pytest.approx(1773.0)
    assert result.sulfur_saturation is not None
    basis = result.sulfur_saturation.melt_presence_basis
    assert basis["basis"] == "melt_redox_liquid_fraction_factor"
    assert basis["source"]
    assert basis["authority"] in {
        "Kress91_liquid_calibration_floor",
        "liquidus_solidus",
    }
    notice = sim.sulfur_saturation_run_notice()
    assert notice is not None
    assert any(
        "T_K=1773.00 K outside Chowdhury-Dasgupta 2019 SCAS" in warning
        for warning in notice["notices"][0]["warnings"]
    )
    assert notice["notices"][0]["melt_presence_basis"] == basis


def test_internal_analytical_none_below_kress_floor_is_not_evaluated(monkeypatch) -> None:
    """The Kress-floor fallback reports no melt below its calibrated floor."""
    run = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        campaign="C0",
        hours=0,
        allow_fallback_vapor=True,
        allow_unmeasured_alpha_fallback=True,
    )
    sim = run._start_session().simulator
    assert isinstance(sim.backend, InternalAnalyticalBackend)
    monkeypatch.setattr(sim, "_stage0_sulfur_input_ppm", lambda: 500.0)
    monkeypatch.setattr(sim, "_melt_oxide_wt_pct", lambda: {"SiO2": 45.0})
    fallback = sim._melt_redox_liquidus_floor_fallback(
        source="none:liquidus_unavailable",
        reason="liquidus test fallback",
        liquidus_status="unavailable",
    )
    monkeypatch.setattr(
        sim,
        "_melt_redox_liquidus_gate_curve",
        lambda: fallback,
    )

    def fail_sulfsat(**_kwargs):
        raise AssertionError("SulfSat must not run below the melt-redox floor")

    monkeypatch.setattr(
        sim._sulfsat_gate,
        "compute_sulfur_saturation",
        fail_sulfsat,
    )
    sim.melt.temperature_C = 1300.0 - 273.15
    result = sim._internal_analytical_equilibrium()

    sim._attach_post_equilibrium_sulfsat(result)

    assert result.sulfur_saturation is None
    assert sim._last_sulfur_saturation_result is None
    diagnostic = sim._last_melt_redox_liquid_fraction_diagnostic
    assert diagnostic["liquid_fraction"] == pytest.approx(0.0)


def test_unavailable_melt_presence_records_typed_not_evaluated(monkeypatch) -> None:
    run = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        campaign="C0",
        hours=0,
        allow_fallback_vapor=True,
        allow_unmeasured_alpha_fallback=True,
    )
    sim = run._start_session().simulator
    monkeypatch.setattr(sim, "_stage0_sulfur_input_ppm", lambda: 500.0)
    monkeypatch.setattr(sim, "_melt_oxide_wt_pct", lambda: {"SiO2": 45.0})

    def unavailable_melt_signal(_T_K):
        sim._last_melt_redox_liquid_fraction_diagnostic = {
            "status": "unavailable",
            "source": "none:liquidus_gate_in_progress",
            "liquid_fraction": 0.0,
        }
        return 0.0

    monkeypatch.setattr(
        sim,
        "_melt_redox_liquid_fraction_factor",
        unavailable_melt_signal,
    )
    sim.melt.temperature_C = 1773.0 - 273.15
    result = SimpleNamespace(warnings=[], liquid_fraction=None)

    sim._attach_post_equilibrium_sulfsat(result)

    assert result.sulfur_saturation.calibration_status == "not_evaluated"
    assert result.sulfur_saturation.not_evaluated_reason == (
        "melt_presence_signal_unavailable"
    )
    assert asdict(result.sulfur_saturation)["not_evaluated_reason"] == (
        "melt_presence_signal_unavailable"
    )
    assert "melt_presence_signal_unavailable" in (
        result.sulfur_saturation.warnings[0]
    )
    notice = sim.sulfur_saturation_run_notice()
    assert notice is not None
    assert notice["notices"][0]["reason"] == "melt_presence_signal_unavailable"


def test_backend_zero_fraction_skips_hot_sulfsat(monkeypatch) -> None:
    run = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        campaign="C0",
        hours=0,
        allow_fallback_vapor=True,
        allow_unmeasured_alpha_fallback=True,
    )
    sim = run._start_session().simulator
    monkeypatch.setattr(sim, "_stage0_sulfur_input_ppm", lambda: 500.0)
    monkeypatch.setattr(sim, "_melt_oxide_wt_pct", lambda: {"SiO2": 45.0})

    def fail_sulfsat(**_kwargs):
        raise AssertionError("SulfSat must not run for liquid_fraction=0.0")

    monkeypatch.setattr(
        sim._sulfsat_gate,
        "compute_sulfur_saturation",
        fail_sulfsat,
    )
    sim.melt.temperature_C = 1773.0 - 273.15
    result = SimpleNamespace(warnings=[], liquid_fraction=0.0)

    sim._attach_post_equilibrium_sulfsat(result)

    assert result.sulfur_saturation is None
    assert sim._last_sulfur_saturation_result is None


def test_attach_post_equilibrium_sulfsat_skips_cold_charge(monkeypatch) -> None:
    run = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        campaign="C0",
        hours=0,
        allow_fallback_vapor=True,
        allow_unmeasured_alpha_fallback=True,
    )
    sim = run._start_session().simulator
    monkeypatch.setattr(sim, "_stage0_sulfur_input_ppm", lambda: 500.0)
    monkeypatch.setattr(sim, "_melt_oxide_wt_pct", lambda: {"SiO2": 45.0})

    def fail_sulfsat(**_kwargs):
        raise AssertionError("SulfSat must not run without a melt")

    monkeypatch.setattr(
        sim._sulfsat_gate,
        "compute_sulfur_saturation",
        fail_sulfsat,
    )
    sim._last_sulfur_saturation_result = _sulfur_result()
    result = SimpleNamespace(warnings=[], liquid_fraction=0.0)

    sim._attach_post_equilibrium_sulfsat(result)

    assert result.sulfur_saturation is None
    assert sim._last_sulfur_saturation_result is None
    assert result.warnings == []
    assert sim.sulfur_saturation_run_notice() is None
