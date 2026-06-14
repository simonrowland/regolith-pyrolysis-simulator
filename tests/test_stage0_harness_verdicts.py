"""H2/H3 verdict layer — strip → adjust → warn + backend_status domain gate."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
import pytest

from engines.builtin.melt_effect_adjustment import (
    CertifiedPointRefusedError,
    aggregate_backend_status,
    build_harness_verdicts,
    evaluate_verdict_a,
    evaluate_verdict_a_timeline,
    evaluate_verdict_b,
    melt_effect_adjustment,
    request_certified_point,
    strip_non_oxide_residuals,
)
from simulator.run_executor import _aggregate_backend_status
from simulator.stage0_harness import run_stage0_harness_from_config
from tests.test_stage0_harness import _session_config


@dataclass
class _FakeTimelineEntry:
    hour: int
    by_group: dict[str, list[dict[str, Any]]] = field(default_factory=dict)


def _basalt_oxide_kg(total_kg: float = 1000.0) -> dict[str, float]:
    return {
        "SiO2": total_kg * 0.45,
        "Al2O3": total_kg * 0.15,
        "FeO": total_kg * 0.12,
        "MgO": total_kg * 0.10,
        "CaO": total_kg * 0.10,
        "Na2O": total_kg * 0.04,
        "K2O": total_kg * 0.04,
    }


def test_verdict_a_never_fails_harness():
    result = run_stage0_harness_from_config(_session_config("lunar_mare_low_ti"))
    assert result.early_melt_reached is True
    assert result.verdicts is not None
    assert result.verdicts["verdict_a"]["warn_only"] is True


def test_liquidus_warning_at_two_percent_of_T():
    residual = {"NaCl": 0.3}
    adj = melt_effect_adjustment(
        residual,
        {"liquidus_T_C": 1400.0},
        "alphamelts",
        T_in_C=1400.0,
    )
    assert adj.raw_liquidus_C == pytest.approx(1400.0)
    assert adj.adjusted_liquidus_C == pytest.approx(1370.0)
    flags = evaluate_verdict_a(adj.perturbations, hour=1)
    liquidus_flags = [f for f in flags if f.property == "liquidus"]
    assert liquidus_flags
    assert liquidus_flags[0].level == "WARNING"
    assert liquidus_flags[0].perturbation_before >= 2.0


def test_notice_when_after_in_band_not_warning():
    residual = {"C": 0.2}
    adj = melt_effect_adjustment(
        residual,
        {"liquidus_T_C": 1400.0},
        "alphamelts",
        T_in_C=1400.0,
    )
    flags = evaluate_verdict_a(adj.perturbations, hour=1)
    redox_flags = [f for f in flags if f.property == "redox"]
    assert redox_flags
    assert redox_flags[0].level == "NOTICE"
    assert redox_flags[0].perturbation_after >= 0.05
    assert max(
        redox_flags[0].perturbation_before,
        redox_flags[0].perturbation_after,
    ) < 0.20


def test_ungrounded_large_interval_escalates_to_warning_via_max_before_after():
    residual = {"NaF": 0.5}
    adj = melt_effect_adjustment(
        residual,
        {"liquidus_T_C": 1400.0},
        "alphamelts",
        T_in_C=1400.0,
    )
    assert adj.raw_liquidus_C == pytest.approx(1400.0)
    assert adj.adjusted_liquidus_C == pytest.approx(1400.0)
    assert adj.adjusted_liquidus_interval_C == pytest.approx((1300.0, 1375.0))
    assert adj.adjusted_liquidus_provenance == ()
    assert adj.adjusted_liquidus_interval_provenance
    pert = adj.perturbations[0]
    assert pert.raw_value is None
    assert pert.adjusted_value is None
    assert pert.interval == pytest.approx((-100.0, -25.0))
    assert not pert.grounded
    flags = evaluate_verdict_a(adj.perturbations, hour=1)
    liquidus_flags = [f for f in flags if f.property == "liquidus"]
    assert liquidus_flags
    assert not liquidus_flags[0].grounded
    assert liquidus_flags[0].noise_floor_status == "noise_floor_ungrounded"
    assert liquidus_flags[0].level == "WARNING"
    assert max(
        liquidus_flags[0].perturbation_before,
        liquidus_flags[0].perturbation_after,
    ) >= 2.0


def test_unpinned_residual_reaches_verdict_a_noise_floor_flag_without_magnitude():
    residual = {"Br": 5.0}
    adj = melt_effect_adjustment(
        residual,
        {"liquidus_T_C": 1400.0},
        "alphamelts",
        T_in_C=1400.0,
    )
    assert any("no effect row for residual Br" in w for w in adj.warnings)
    assert len(adj.perturbations) == 1
    pert = adj.perturbations[0]
    assert pert.property == "noise_floor"
    assert pert.metric == "noise_floor_ungrounded"
    assert pert.contaminant == "Br"
    assert pert.residual_wt_pct == pytest.approx(5.0)
    assert pert.raw_value is None
    assert pert.adjusted_value is None
    assert pert.perturbation_before is None
    assert pert.perturbation_after is None

    flags = evaluate_verdict_a(adj.perturbations, hour=1)
    assert flags
    flag = flags[0]
    assert flag.contaminant == "Br"
    assert flag.property == "noise_floor"
    assert flag.metric == "noise_floor_ungrounded"
    assert flag.noise_floor_status == "noise_floor_ungrounded"
    assert flag.level == "WARNING"
    assert flag.grounded is False
    assert flag.residual_wt_pct == pytest.approx(5.0)
    assert flag.perturbation_before is None
    assert flag.perturbation_after is None


def test_residual_bakes_out_clears_flag_step_resolved():
    timeline = (
        _FakeTimelineEntry(
            hour=1,
            by_group={"other_mineral_contaminant": []},
        ),
        _FakeTimelineEntry(
            hour=2,
            by_group={
                "other_mineral_contaminant": [
                    {
                        "carrier": "NaCl",
                        "disposition": "escaped",
                        "source": "diagnostic",
                    }
                ]
            },
        ),
        _FakeTimelineEntry(
            hour=3,
            by_group={"other_mineral_contaminant": []},
        ),
    )
    final_residual = {"NaCl": 0.3}
    verdict = evaluate_verdict_a_timeline(
        final_residual,
        {"liquidus_T_C": 1400.0},
        "alphamelts",
        T_in_C=1400.0,
        timeline=timeline,
    )
    hour1_flags = [s for s in verdict.step_resolved if s["hour"] == 1][0]["flags"]
    hour3_flags = [s for s in verdict.step_resolved if s["hour"] == 3][0]["flags"]
    assert hour1_flags
    assert not hour3_flags or all(f.get("cleared") for f in hour3_flags)


def test_raw_and_adjusted_are_separate_with_provenance():
    residual = {"NaCl": 0.3}
    adj = melt_effect_adjustment(
        residual,
        {"liquidus_T_C": 1400.0},
        "alphamelts",
        T_in_C=1400.0,
    )
    assert adj.raw_liquidus_C == pytest.approx(1400.0)
    assert adj.adjusted_liquidus_C == pytest.approx(1370.0)
    assert adj.adjusted_liquidus_C != adj.raw_liquidus_C
    assert adj.adjusted_liquidus_provenance
    pert = adj.perturbations[0]
    assert pert.raw_value is not None
    assert pert.adjusted_value == pytest.approx(0.0)


def test_certified_point_on_ungrounded_effect_fails_loud():
    with pytest.raises(CertifiedPointRefusedError):
        request_certified_point("fluoride", "liquidus")
    with pytest.raises(CertifiedPointRefusedError):
        request_certified_point("residual_carbon", "redox")


def test_strip_records_provenance_no_renormalize():
    oxides = _basalt_oxide_kg(900.0)
    cleaned = {**oxides, "NaCl": 50.0, "C": 50.0}
    stripped = strip_non_oxide_residuals(cleaned)
    assert stripped.stripped_mass_kg == pytest.approx(100.0)
    assert "NaCl" in stripped.stripped_kg
    assert "C" in stripped.stripped_kg
    assert stripped.provenance
    oxide_sum = sum(stripped.oxide_wt_pct.values())
    assert oxide_sum < 100.0
    assert "P2O5" not in stripped.stripped_kg


def test_p2o5_not_stripped():
    cleaned = {**_basalt_oxide_kg(950.0), "P2O5": 50.0}
    stripped = strip_non_oxide_residuals(cleaned)
    assert "P2O5" in stripped.oxide_kg
    assert stripped.stripped_mass_kg == pytest.approx(0.0)


def test_verdict_b_reads_backend_status_no_new_equilibrium():
    sim = SimpleNamespace(
        _backend_status_history=["ok", "out_of_domain"],
        _last_backend_status="ok",
        _backend_selection_status="ok",
        _last_backend_diagnostics={},
        melt=SimpleNamespace(temperature_C=1400.0),
    )
    cleaned = _basalt_oxide_kg()
    verdicts = build_harness_verdicts(
        cleaned_melt_kg=cleaned,
        sim=sim,
        engine="alphamelts",
        timeline=(),
        T_in_C=1400.0,
    )
    assert verdicts["verdict_b"]["backend_status"] == "out_of_domain"
    assert verdicts["verdict_b"]["contaminant_present_never_crash"] is True


def test_verdict_b_hard_gate_on_stripped_out_of_domain_not_contaminant():
    cleaned = {
        **_basalt_oxide_kg(999.0),
        "NaCl": 1.0,
    }
    stripped = strip_non_oxide_residuals(cleaned)
    assert stripped.stripped_mass_kg > 0.0
    verdict = evaluate_verdict_b(cleaned, "out_of_domain", "alphamelts")
    assert verdict.stripped_domain_valid is True
    assert verdict.hard_gate_failed is False
    assert verdict.backend_status == "out_of_domain"


def test_verdict_b_stripped_sio2_out_of_range_fails_hard_gate():
    cleaned = {
        "SiO2": 100.0,
        "FeO": 900.0,
    }
    verdict = evaluate_verdict_b(cleaned, "ok", "alphamelts")
    assert verdict.stripped_domain_valid is False
    assert verdict.hard_gate_failed is True
    assert verdict.domain_warnings


def test_aggregate_backend_status_matches_run_executor():
    history = ("ok", "out_of_domain", "ok")
    assert aggregate_backend_status(history, "ok") == "out_of_domain"
    assert _aggregate_backend_status(history, "ok") == "out_of_domain"
    assert aggregate_backend_status((), "not_converged") == "not_converged"


def test_cache_neutral_modules_not_in_source_patterns():
    from simulator.reduced_real_determinism import _SOURCE_MODULE_PATTERNS

    patterns = _SOURCE_MODULE_PATTERNS
    assert not any("stage0_harness" in p for p in patterns)
    assert not any("melt_effect_adjustment" in p for p in patterns)
    assert not any("foulant_disposition" in p for p in patterns)


def test_harness_verdicts_populated_on_real_feedstock():
    result = run_stage0_harness_from_config(_session_config("lunar_mare_low_ti"))
    v = result.verdicts
    assert "verdict_a" in v
    assert "verdict_b" in v
    assert "strip" in v
    assert "melt_effect_adjustment" in v
    assert v["strip"]["renormalized"] is False
    assert "raw_liquidus_C" in v["melt_effect_adjustment"]
    assert "adjusted_liquidus_C" in v["melt_effect_adjustment"]
