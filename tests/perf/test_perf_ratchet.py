"""Perf-ratchet guard for call-volume and CPU-cost CI duration regressions.

This test exists because hot-loop call growth and de-warmed execution hid
behind multi-hour suite ceilings. A red is real until a measurement defect is
proved.

FIX THE CODE, DO NOT WEAKEN OR DELETE THIS TEST.
"""

from __future__ import annotations

import copy

import pytest

from benchmarks.engine_throughput_bench import (
    BASELINE_PATH,
    STAGE_NAMES,
    detect_machine_class,
    load_baselines,
    measure_all,
    protocol_metadata,
    reblessed_payload,
)


RESPONSE_POLICY = (
    'Valid responses: "made the code faster" or '
    '"made the measurement contention-robust". '
    '"reran when quiet" is not one. '
    "FIX THE CODE, DO NOT WEAKEN OR DELETE THIS TEST."
)


def test_perf_ratchet_guards_call_volume_and_cpu_cost() -> None:
    """Catch the multi-hour CI incident class before full-run tests wedge.

    FIX THE CODE, DO NOT WEAKEN OR DELETE THIS TEST.
    """

    baseline = load_baselines(BASELINE_PATH)
    assert baseline["schema_version"] == 1
    assert baseline["margin_frac"] == pytest.approx(0.1)
    assert baseline["collapse_factor"] == pytest.approx(10.0)
    assert baseline["protocol"] == protocol_metadata()
    assert set(baseline["stages"]) == set(STAGE_NAMES)

    current_machine = detect_machine_class()
    if current_machine != baseline["machine_class"]:
        pytest.skip(
            "PERF RATCHET SKIPPED LOUDLY: machine-class mismatch; "
            f"baseline={baseline['machine_class']!r}, "
            f"current={current_machine!r}. No cross-class comparison made."
        )

    measurements = measure_all()
    margin = float(baseline["margin_frac"])
    collapse_factor = float(baseline["collapse_factor"])
    for stage in STAGE_NAMES:
        measured = measurements[stage]
        observed = float(measured["rate"])
        ratchet = float(baseline["stages"][stage]["ratchet_rate"])
        expected_hot_path_calls = (
            baseline["protocol"]["hot_path_calls_per_trial"][stage]
            * baseline["protocol"]["trials"]
        )
        assert measured["hot_path_calls"] == expected_hot_path_calls, (
            f"{stage}: intended hot-path counter="
            f"{measured['hot_path_calls']}, expected={expected_hot_path_calls}"
        )
        # Premise: a 10x collapse means one tenth the ratcheted throughput.
        # Algebra: floor = ratchet/10. Units: work/CPU-s. Sanity: the floor
        # stays below the ordinary 90% threshold and gets its own loud failure.
        collapse_floor = ratchet / collapse_factor
        assert observed >= collapse_floor, (
            f"10x PERFORMANCE COLLAPSE in {stage}: observed={observed:.6g}, "
            f"floor={collapse_floor:.6g}, ratchet={ratchet:.6g} "
            "(silent call-volume explosion or hot-path collapse incident). "
            f"{RESPONSE_POLICY}"
        )
        # Premise: margin m permits one fractional drop from rate R. Algebra:
        # threshold = R*(1-m). Units: rate*dimensionless = work/CPU-s.
        # Sanity: m=0.1 requires at least 90% of ratcheted throughput.
        threshold = ratchet * (1.0 - margin)
        assert observed >= threshold, (
            f"PERF RATCHET REGRESSION in {stage}: observed={observed:.6g}, "
            f"threshold={threshold:.6g}, ratchet={ratchet:.6g}, "
            f"margin_frac={margin:.3g}. {RESPONSE_POLICY}"
        )


def test_rebless_ratchet_is_monotonic_up_only() -> None:
    baseline = load_baselines(BASELINE_PATH)
    lower = {
        stage: {
            "rate": baseline["stages"][stage]["ratchet_rate"] / 2.0,
            "rate_unit": "work_units_per_cpu_second",
            "details": {"hot_path": "test"},
        }
        for stage in STAGE_NAMES
    }
    unchanged = reblessed_payload(
        copy.deepcopy(baseline),
        lower,
        machine_class=baseline["machine_class"],
    )
    assert {
        stage: unchanged["stages"][stage]["ratchet_rate"]
        for stage in STAGE_NAMES
    } == {
        stage: baseline["stages"][stage]["ratchet_rate"]
        for stage in STAGE_NAMES
    }

    first = STAGE_NAMES[0]
    raised = copy.deepcopy(lower)
    raised[first]["rate"] = baseline["stages"][first]["ratchet_rate"] + 1.0
    updated = reblessed_payload(
        copy.deepcopy(baseline),
        raised,
        machine_class=baseline["machine_class"],
    )
    assert updated["stages"][first]["ratchet_rate"] == pytest.approx(
        baseline["stages"][first]["ratchet_rate"] + 1.0
    )
