"""Perf-ratchet guard for call-volume and CPU-cost CI duration regressions.

This test exists because hot-loop call growth and de-warmed execution hid
behind multi-hour suite ceilings. A red is real until a measurement defect is
proved.

FIX THE CODE, DO NOT WEAKEN OR DELETE THIS TEST.
"""

from __future__ import annotations

import copy
import json
import math
import statistics
import subprocess
import sys

import pytest

import benchmarks.engine_throughput_bench as bench
from benchmarks.engine_throughput_bench import (
    BASELINE_PATH,
    MACHINE_CLASS,
    STAGE_NAMES,
    detect_machine_class,
    load_baselines,
    measure_all,
    protocol_metadata,
    reblessed_payload,
)

pytestmark = pytest.mark.engine_cache_off


def _power_state(
    *,
    power_source: str = "ac",
    battery_percent: int = 80,
    thermal_pressure: str = "nominal",
    thermal_signals: tuple[str, ...] = ("CPU_Speed_Limit=100",),
    probe_status: str = "ok",
) -> bench.PowerState:
    return bench.PowerState(
        platform="Darwin",
        power_source=power_source,
        battery_percent=battery_percent,
        thermal_pressure=thermal_pressure,
        thermal_signals=thermal_signals,
        probe_errors=(),
        probe_status=probe_status,
    )


LIMITED_POWER_STATES = (
    _power_state(power_source="battery"),
    _power_state(battery_percent=7),
    _power_state(
        thermal_pressure="elevated",
        thermal_signals=("CPU_Speed_Limit=75",),
    ),
)


RESPONSE_POLICY = (
    'Valid responses: "made the code faster" or '
    '"made the measurement contention-robust". '
    '"reran when quiet" is not one. '
    "FIX THE CODE, DO NOT WEAKEN OR DELETE THIS TEST."
)


def _assert_optimizer_stage_measurement(
    measurement: dict,
    trial_count: int = bench.OPTIMIZER_STAGE_TRIALS,
) -> None:
    trials = measurement["details"]["trial_records"]
    cpu_values = [float(trial["cpu_seconds"]) for trial in trials]
    median_cpu = float(measurement["cpu_seconds_median"])
    assert len(trials) == trial_count
    assert measurement["trial_count"] == trial_count
    assert math.isfinite(median_cpu)
    # Keep the existing 7.42 CPU-s sanity floor from the earlier 24 h
    # analytical run. The profile's authored hours are not the completed
    # campaign chain's simulated duration.
    assert median_cpu > 7.42
    assert median_cpu == pytest.approx(statistics.median(cpu_values))
    assert measurement["cpu_seconds_spread"] == pytest.approx(
        max(cpu_values) - min(cpu_values)
    )
    assert measurement["work_units"] == sum(
        int(trial["trace_hours"]) for trial in trials
    )
    assert measurement["hot_path_calls"] == measurement["work_units"]
    assert math.isfinite(float(measurement["wall_seconds_median"]))
    assert math.isfinite(float(measurement["wall_cpu_ratio_median"]))
    for trial in trials:
        assert trial["date"] and trial["hostname"]
        assert trial["work_units"] == trial["trace_hours"]
        assert trial["step_calls"] == trial["trace_hours"]
        assert trial["hot_path_calls"] == trial["work_units"]
        assert sum(
            int(stop["duration_h"]) for stop in trial["campaign_stops"]
        ) == trial["trace_hours"]
        assert trial["campaign_chain"] == [
            str(stop["campaign"]) for stop in trial["campaign_stops"]
        ]
        assert trial["campaign_chain"]
        assert trial["cold_cache_miss"] is True
        assert trial["same_patch_cache_hit"] is True
        assert trial["cache_hit_counted"] is False
        assert trial["cache_hit_work_units"] == 0
        assert math.isfinite(float(trial["wall_seconds"]))
        assert math.isfinite(float(trial["wall_cpu_ratio"]))


def _assert_stage_ratchet_acceptance(
    stage: str,
    measured: dict,
    baseline: dict,
) -> None:
    protocol = baseline["protocol"]
    if stage == bench.OPTIMIZER_STAGE_NAME:
        assert protocol["dynamic_work_units_per_trial"][stage] == (
            "sum_completed_campaign_duration_h"
        )
        _assert_optimizer_stage_measurement(
            measured,
            trial_count=int(
                protocol.get("stage_trial_counts", {}).get(
                    stage,
                    protocol["trials"],
                )
            ),
        )
    else:
        stage_trials = int(
            protocol.get("stage_trial_counts", {}).get(
                stage,
                protocol["trials"],
            )
        )
        # Protocol counts are per trial; multiplying by the declared trial
        # count derives the expected aggregate work in this measurement.
        expected_hot_path_calls = (
            protocol["hot_path_calls_per_trial"][stage] * stage_trials
        )
        assert measured["hot_path_calls"] == expected_hot_path_calls, (
            f"{stage}: intended hot-path counter="
            f"{measured['hot_path_calls']}, expected={expected_hot_path_calls}"
        )
        expected_work_units = (
            protocol["stage_work_units_per_trial"][stage] * stage_trials
        )
        assert measured["work_units"] == expected_work_units, (
            f"{stage}: measured work units={measured['work_units']}, "
            f"expected={expected_work_units}"
        )

    observed = float(measured["rate"])
    ratchet = float(baseline["stages"][stage]["ratchet_rate"])
    collapse_factor = float(baseline["collapse_factor"])
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
    margin = float(baseline["margin_frac"])
    threshold = ratchet * (1.0 - margin)
    assert observed >= threshold, (
        f"PERF RATCHET REGRESSION in {stage}: observed={observed:.6g}, "
        f"threshold={threshold:.6g}, ratchet={ratchet:.6g}, "
        f"margin_frac={margin:.3g}. {RESPONSE_POLICY}"
    )
    # Softened-baseline detector (NOT-FIXED lens residual, 2026-07-25):
    # the machine_class pin closed the skip-forever tamper, but a
    # down-edited ratchet_rate would silently loosen the bar. Excess
    # headroom means either a real speedup (rebless — the ratchet
    # exists to be raised) or a softened baseline; both deserve red.
    stale_ceiling = ratchet * 1.5
    assert observed <= stale_ceiling, (
        f"STALE/SOFTENED RATCHET in {stage}: observed={observed:.6g} "
        f"exceeds ratchet={ratchet:.6g} by >1.5x. If the code really "
        "got this much faster, run benchmarks/engine_throughput_bench.py "
        "--rebless-ratchet and commit the raised bar; if not, the "
        "baselines JSON has been softened. Never widen this ceiling."
    )


def _synthetic_optimizer_measurement() -> dict:
    from pathlib import Path

    from simulator.config import load_config_bundle
    from simulator.optimize.profiles import load_profile

    profile = load_profile(
        Path(__file__).resolve().parents[2]
        / "data"
        / "optimize_profiles"
        / "lunar_highland.yaml"
    )
    expected_campaigns, duration_caps = bench._optimizer_campaign_limits(
        profile,
        load_config_bundle().setpoints,
    )
    campaign_stops = [
        {"campaign": campaign, "duration_h": 1}
        for campaign in expected_campaigns
    ]
    trace_hours = bench._assert_optimizer_campaign_work(
        step_calls=len(campaign_stops),
        campaign_stops=campaign_stops,
        expected_campaigns=expected_campaigns,
        duration_caps=duration_caps,
    )
    cpu_seconds = 8.0
    campaign_chain = [str(stop["campaign"]) for stop in campaign_stops]
    trial_records = [
        {
            "date": "2026-01-01T00:00:00+00:00",
            "hostname": "perf-test",
            "cpu_seconds": cpu_seconds,
            "trace_hours": trace_hours,
            "work_units": trace_hours,
            "step_calls": trace_hours,
            "hot_path_calls": trace_hours,
            "campaign_stops": campaign_stops,
            "campaign_chain": campaign_chain,
            "cold_cache_miss": True,
            "same_patch_cache_hit": True,
            "cache_hit_counted": False,
            "cache_hit_work_units": 0,
            "wall_seconds": cpu_seconds + 1.0,
            "wall_cpu_ratio": (cpu_seconds + 1.0) / cpu_seconds,
        }
        for _ in range(bench.OPTIMIZER_STAGE_TRIALS)
    ]
    return {
        "rate": trace_hours / cpu_seconds,
        "rate_unit": "work_units_per_cpu_second",
        "work_units": trace_hours * bench.OPTIMIZER_STAGE_TRIALS,
        "hot_path_calls": trace_hours * bench.OPTIMIZER_STAGE_TRIALS,
        "cpu_seconds_median": cpu_seconds,
        "cpu_seconds_spread": 0.0,
        "wall_seconds_median": cpu_seconds + 1.0,
        "wall_cpu_ratio_median": (cpu_seconds + 1.0) / cpu_seconds,
        "trial_count": bench.OPTIMIZER_STAGE_TRIALS,
        "trial_rates": [trace_hours / cpu_seconds]
        * bench.OPTIMIZER_STAGE_TRIALS,
        "details": {
            "hot_path": bench.STAGE_HOT_PATHS[bench.OPTIMIZER_STAGE_NAME],
            "trial_records": trial_records,
        },
    }


# Size the per-test CPU/wall guard to the complete measurement envelope. The
# optimizer contributes 5 trials * 1,283 CPU-s/trial = 6,415 CPU-s; the other
# stages contribute configured worker counts * 180 s = 1,980 CPU-s. Unit
# check: each term is CPU-seconds, so the derived budget is 8,395 CPU-s.
OPTIMIZER_RATCHET_CPU_BUDGET_SECONDS = math.ceil(
    bench.OPTIMIZER_STAGE_TRIALS * bench.OPTIMIZER_STAGE_CHAIN_CPU_ESTIMATE_S
    + sum(
        bench.PROTOCOL["stage_process_repeats"][stage]
        * bench.STAGE_WORKER_HANG_WALL_S
        for stage in STAGE_NAMES
        if stage != bench.OPTIMIZER_STAGE_NAME
    )
)


# The full campaign-chain evaluation is a heavy serialized test. Its CPU
# budget follows the sibling C0-to-C6 trajectory entry in the heavy roster.
@pytest.mark.nightly
@pytest.mark.xdist_group("serial")
@pytest.mark.cpu_budget(5000)
def test_optimizer_60h_stage_records_real_cold_work() -> None:
    """Measure real cold work for the profile's completed campaign chain."""
    assert bench.OPTIMIZER_STAGE_NAME in STAGE_NAMES, (
        "optimizer stage missing from STAGE_NAMES"
    )
    measurement = bench._measure_optimizer_profile_campaign_chain_stage(
        trial_count=1
    )
    _assert_optimizer_stage_measurement(
        measurement,
        trial_count=1,
    )
    # SQLite raised OverflowError at INT_MAX bytes on this path. A 64 MiB
    # ceiling leaves about 32x headroom below that observed failure boundary.
    for trial in measurement["details"]["trial_records"]:
        assert trial["max_stored_text_cell_bytes"] < 64 * 1024 * 1024
        assert trial["coating_warning_flag_count"] > 0
        assert trial["source_authority_object_count"] == 1
        assert trial["source_flag_object_count"] == 1
        assert trial["loaded_authority_object_count"] == 1
        assert trial["loaded_flag_object_count"] == 1
        print(
            "optimizer_60h_stage_records_real_cold_work: "
            + json.dumps(
                {
                    "campaign_chain": trial["campaign_chain"],
                    "step_calls": trial["step_calls"],
                    "trace_duration_sum": sum(
                        int(stop["duration_h"])
                        for stop in trial["campaign_stops"]
                    ),
                    "max_stored_text_cell_bytes": trial[
                        "max_stored_text_cell_bytes"
                    ],
                    "stored_text_cell_ceiling_bytes": 64 * 1024 * 1024,
                    "source_authority_object_count": trial[
                        "source_authority_object_count"
                    ],
                    "source_flag_object_count": trial["source_flag_object_count"],
                    "loaded_authority_object_count": trial[
                        "loaded_authority_object_count"
                    ],
                    "loaded_flag_object_count": trial["loaded_flag_object_count"],
                },
                sort_keys=True,
            )
        )


def test_lunar_highland_campaign_chain_work_volume_is_derived_from_config() -> None:
    """Derive the route from config; require one step per traced hour.

    Campaign endpoints determine actual durations, so the exact work volume is
    the sum of completed executor trace durations, independently checked
    against patched PyrolysisSimulator.step calls by the cold-work measurement.
    The ordered route and duration caps come from this profile and setpoints.
    """
    from pathlib import Path

    from simulator.config import load_config_bundle
    from simulator.optimize.profiles import load_profile

    profile = load_profile(
        Path(__file__).resolve().parents[2]
        / "data"
        / "optimize_profiles"
        / "lunar_highland.yaml"
    )
    assert profile["run"]["hours"] == bench.OPTIMIZER_PROFILE_RUN_HOURS == 60
    assert (
        profile["fidelities"]["internal-analytical"]["hours"]
        == bench.OPTIMIZER_PROFILE_RUN_HOURS
    )
    assert all(
        fidelity["hours"] == bench.OPTIMIZER_PROFILE_RUN_HOURS
        for fidelity in profile["fidelities"].values()
    )

    expected_campaigns, duration_caps = bench._optimizer_campaign_limits(
        profile,
        load_config_bundle().setpoints,
    )
    assert expected_campaigns
    assert set(expected_campaigns) == set(duration_caps)
    recorded_trace = tuple(
        {"campaign": campaign, "duration_h": 1}
        for campaign in expected_campaigns
    )
    assert bench._assert_optimizer_campaign_work(
        step_calls=len(recorded_trace),
        campaign_stops=recorded_trace,
        expected_campaigns=expected_campaigns,
        duration_caps=duration_caps,
    ) == len(recorded_trace)
    with pytest.raises(AssertionError, match="step calls"):
        bench._assert_optimizer_campaign_work(
            step_calls=len(recorded_trace) + 1,
            campaign_stops=recorded_trace,
            expected_campaigns=expected_campaigns,
            duration_caps=duration_caps,
        )


def test_optimizer_60h_eval_ratchet_is_baselined_and_visible() -> None:
    stage = load_baselines(BASELINE_PATH)["stages"][bench.OPTIMIZER_STAGE_NAME]
    assert "status" not in stage
    assert "reason" not in stage
    assert math.isfinite(float(stage["ratchet_rate"]))
    assert stage["ratchet_rate"] > 0.0


def test_reblessed_optimizer_stage_is_rated_with_dynamic_work_checks() -> None:
    baseline = load_baselines(BASELINE_PATH)
    measurement = _synthetic_optimizer_measurement()
    optimizer_baseline = baseline["stages"][bench.OPTIMIZER_STAGE_NAME]
    if "ratchet_rate" in optimizer_baseline:
        measurement["rate"] = max(
            measurement["rate"],
            float(optimizer_baseline["ratchet_rate"]) * 1.01,
        )
    reblessed = reblessed_payload(
        copy.deepcopy(baseline),
        {
            stage: {
                "rate": baseline["stages"][stage]["ratchet_rate"],
                "rate_unit": "work_units_per_cpu_second",
                "details": {"hot_path": bench.STAGE_HOT_PATHS[stage]},
            }
            for stage in STAGE_NAMES
            if stage != bench.OPTIMIZER_STAGE_NAME
        }
        | {bench.OPTIMIZER_STAGE_NAME: measurement},
        machine_class=baseline["machine_class"],
    )

    assert "status" not in reblessed["stages"][bench.OPTIMIZER_STAGE_NAME]
    assert (
        reblessed["stages"][bench.OPTIMIZER_STAGE_NAME]["hot_path"]
        == bench.STAGE_HOT_PATHS[bench.OPTIMIZER_STAGE_NAME]
    )
    for stage in STAGE_NAMES:
        if stage != bench.OPTIMIZER_STAGE_NAME:
            assert reblessed["stages"][stage] == baseline["stages"][stage]
    assert bench.OPTIMIZER_STAGE_NAME not in (
        reblessed["protocol"]["hot_path_calls_per_trial"]
    )
    assert bench.OPTIMIZER_STAGE_NAME not in (
        reblessed["protocol"]["stage_work_units_per_trial"]
    )
    _assert_stage_ratchet_acceptance(
        bench.OPTIMIZER_STAGE_NAME,
        measurement,
        reblessed,
    )

    collapsed = dict(measurement, rate=measurement["rate"] / 20.0)
    with pytest.raises(AssertionError, match="10x PERFORMANCE COLLAPSE"):
        _assert_stage_ratchet_acceptance(
            bench.OPTIMIZER_STAGE_NAME,
            collapsed,
            reblessed,
        )


@pytest.mark.nightly
@pytest.mark.xdist_group("serial")
@pytest.mark.cpu_budget(OPTIMIZER_RATCHET_CPU_BUDGET_SECONDS)
def test_perf_ratchet_guards_call_volume_and_cpu_cost() -> None:
    """Catch the multi-hour CI incident class before full-run tests wedge.

    FIX THE CODE, DO NOT WEAKEN OR DELETE THIS TEST.
    """

    assert bench.OPTIMIZER_STAGE_NAME in STAGE_NAMES, (
        "optimizer stage missing from STAGE_NAMES"
    )
    assert bench.PROTOCOL["aggregation"] == "max_trial_rate"
    baseline = load_baselines(BASELINE_PATH)
    # Milestone review F1 (HIGH): without this pin, a one-line edit to the
    # baselines JSON machine_class field made the gate skip green forever
    # on every box (metadata-softened gate, SC-12 shape). The baseline must
    # claim the canonical class the bench compiles in; the runtime skip
    # below remains the legitimate cross-class escape.
    assert baseline["machine_class"] == MACHINE_CLASS, (
        "perf_ratchet_baselines.json machine_class "
        f"{baseline['machine_class']!r} != benchmark canonical "
        f"{MACHINE_CLASS!r} — baseline tampering or an unratified "
        "cross-class rebless. FIX THE BASELINE, DO NOT WEAKEN THIS PIN."
    )
    assert baseline["schema_version"] == 1
    assert baseline["margin_frac"] == pytest.approx(0.1)
    assert baseline["collapse_factor"] == pytest.approx(10.0)
    assert baseline["protocol"] == protocol_metadata()
    assert set(baseline["stages"]) == set(STAGE_NAMES)
    unbaselined_stages = {
        stage
        for stage, values in baseline["stages"].items()
        if values.get("status") == "needs_rebaseline"
    }
    assert unbaselined_stages <= {bench.OPTIMIZER_STAGE_NAME}
    if bench.OPTIMIZER_STAGE_NAME in unbaselined_stages:
        assert baseline["stages"][bench.OPTIMIZER_STAGE_NAME] == {
            "status": "needs_rebaseline",
            "reason": bench.OPTIMIZER_REBASELINE_REASON,
        }
    rated_stages = tuple(
        stage for stage in STAGE_NAMES if stage not in unbaselined_stages
    )

    current_machine = detect_machine_class()
    if current_machine != baseline["machine_class"]:
        pytest.skip(
            "PERF RATCHET SKIPPED LOUDLY: machine-class mismatch; "
            f"baseline={baseline['machine_class']!r}, "
            f"current={current_machine!r}. No cross-class comparison made."
        )

    bench.require_measurement_power_state()
    measurements = measure_all(stage_names=rated_stages)
    assert set(measurements) == set(rated_stages)

    for stage in rated_stages:
        measured = measurements[stage]
        _assert_stage_ratchet_acceptance(stage, measured, baseline)


def test_measure_all_selects_fastest_independent_stage_process(monkeypatch) -> None:
    calls_by_stage = {stage: 0 for stage in STAGE_NAMES}
    timeouts_by_stage = {}

    def fake_run(command, **kwargs):
        stage = command[-1]
        calls_by_stage[stage] += 1
        timeouts_by_stage[stage] = kwargs["timeout"]
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps({"rate": float(calls_by_stage[stage])}),
            stderr="",
        )

    monkeypatch.setattr(bench.subprocess, "run", fake_run)

    measurements = bench.measure_all()

    for stage in STAGE_NAMES:
        repeats = bench.PROTOCOL["stage_process_repeats"][stage]
        assert calls_by_stage[stage] == repeats
        assert measurements[stage]["rate"] == pytest.approx(float(repeats))
        expected_timeout = (
            bench.OPTIMIZER_STAGE_WORKER_HANG_WALL_S
            if stage == bench.OPTIMIZER_STAGE_NAME
            else bench.STAGE_WORKER_HANG_WALL_S
        )
        assert timeouts_by_stage[stage] == expected_timeout


@pytest.mark.parametrize(
    ("output", "expected_source", "expected_percent"),
    (
        (
            "Now drawing from 'AC Power'\n"
            " -InternalBattery-0\t81%; charged; present: true\n",
            "ac",
            81,
        ),
        (
            "Now drawing from 'AC Power'\n"
            " -InternalBattery-0\t7%; charging; present: true\n",
            "ac",
            7,
        ),
        (
            "Now drawing from 'Battery Power'\n"
            " -InternalBattery-0\t76%; discharging; present: true\n",
            "battery",
            76,
        ),
    ),
    ids=("healthy-ac", "low-soc-on-ac", "battery-only"),
)
def test_parse_pmset_battery_states(
    output: str,
    expected_source: str,
    expected_percent: int,
) -> None:
    assert bench.parse_pmset_battery(output) == (
        expected_source,
        expected_percent,
    )


def test_parse_pmset_thermal_detects_only_explicit_pressure() -> None:
    assert bench.parse_pmset_thermal(
        "CPU Power notify\nCPU_Speed_Limit = 75\nScheduler_Limit = 100\n"
    ) == (
        "elevated",
        ("CPU_Speed_Limit=75", "Scheduler_Limit=100"),
    )
    assert bench.parse_pmset_thermal(
        "Error: Failed to get thermal warning level\n"
    ) == ("unavailable", ())


@pytest.mark.parametrize(
    (
        "thermal_output",
        "expected_status",
        "expected_pressure",
        "expected_errors",
    ),
    (
        (
            "CPU Power notify\n"
            "CPU_Speed_Limit = 100\n"
            "Scheduler_Limit = 100\n",
            "ok",
            "nominal",
            (),
        ),
        (
            "Error: Failed to get thermal warning level\n",
            "signal_unsupported",
            "unavailable",
            ("pmset therm exposed no supported current-pressure signal",),
        ),
        (
            "CPU Power notify\nCPU_Speed_Limit = unavailable\n",
            "parse_failed",
            "unavailable",
            ("pmset therm current-pressure signal could not be parsed",),
        ),
    ),
    ids=("ok", "signal-unsupported", "parse-failed"),
)
def test_probe_power_state_classifies_canned_pmset_outputs(
    monkeypatch: pytest.MonkeyPatch,
    thermal_output: str,
    expected_status: str,
    expected_pressure: str,
    expected_errors: tuple[str, ...],
) -> None:
    outputs = {
        "batt": (
            "Now drawing from 'AC Power'\n"
            " -InternalBattery-0\t100%; charged; present: true\n",
            None,
        ),
        "therm": (thermal_output, None),
    }
    monkeypatch.setattr(bench.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(bench, "_pmset_output", lambda mode: outputs[mode])

    power_state = bench.probe_power_state()

    assert power_state.probe_status == expected_status
    assert power_state.thermal_pressure == expected_pressure
    assert power_state.probe_errors == expected_errors
    assert power_state.refusal_reasons == ()
    assert power_state.as_dict()["measurement_valid"] is True


@pytest.mark.parametrize(
    "power_state",
    LIMITED_POWER_STATES,
    ids=("battery-only", "low-soc", "thermal-pressure"),
)
def test_pytest_gate_refusal_is_typed(
    power_state: bench.PowerState,
) -> None:
    with pytest.raises(bench.MeasurementInvalid, match="REFUSED-MEASUREMENT"):
        bench.require_measurement_power_state(power_state)


def test_pytest_gate_refusal_is_process_nonzero(tmp_path) -> None:
    test_path = tmp_path / "test_refused_perf_measurement.py"
    test_path.write_text(
        "from benchmarks.engine_throughput_bench import "
        "PowerState, require_measurement_power_state\n\n"
        "def test_refused_perf_measurement():\n"
        "    require_measurement_power_state(PowerState(\n"
        "        platform='Darwin',\n"
        "        power_source='ac',\n"
        "        battery_percent=7,\n"
        "        thermal_pressure='nominal',\n"
        "        thermal_signals=('CPU_Speed_Limit=100',),\n"
        "        probe_errors=(),\n"
        "        probe_status='ok',\n"
        "    ))\n"
    )
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-n0", str(test_path)],
        cwd=bench.REPO_ROOT,
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )

    assert result.returncode != 0
    assert "REFUSED-MEASUREMENT" in result.stdout
    assert "xfailed" not in result.stdout


def test_pytest_gate_accepts_power_sane_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    power_state = _power_state()
    monkeypatch.setattr(bench, "probe_power_state", lambda: power_state)
    assert bench.require_measurement_power_state() == power_state


def test_malformed_pmset_measures_with_parse_warning(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    outputs = {
        "batt": (
            "Now drawing from 'Sideways Power'\n"
            " -InternalBattery-0\tunknown%; charging; present: true\n",
            None,
        ),
        "therm": (
            "CPU_Speed_Limit = 100\nScheduler_Limit = 100\n",
            None,
        ),
    }
    monkeypatch.setattr(bench.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(bench, "_pmset_output", lambda mode: outputs[mode])
    power_state = bench.probe_power_state()

    assert power_state.refusal_reasons == ()
    assert power_state.probe_status == "parse_failed"
    assert power_state.probe_errors == (
        "pmset batt did not expose AC/battery source",
        "pmset batt did not expose battery SoC",
    )

    monkeypatch.setattr(bench, "probe_power_state", lambda: power_state)
    monkeypatch.setattr(bench, "detect_machine_class", lambda: MACHINE_CLASS)
    monkeypatch.setattr(bench, "measure_all", lambda: {"fixture": {"rate": 1.0}})

    assert bench.main([]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["outcome"] == "MEASURED"
    assert payload["power_state"]["probe_status"] == "parse_failed"
    assert payload["power_state"]["probe_errors"] == list(power_state.probe_errors)


@pytest.mark.parametrize(
    "power_state",
    LIMITED_POWER_STATES,
    ids=("battery-only", "low-soc", "thermal-pressure"),
)
def test_benchmark_cli_refuses_power_limited_measurement(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    power_state: bench.PowerState,
) -> None:
    monkeypatch.setattr(bench, "probe_power_state", lambda: power_state)
    monkeypatch.setattr(
        bench,
        "measure_all",
        lambda: pytest.fail("measurement ran after power refusal"),
    )

    assert bench.main([]) == bench.MEASUREMENT_INVALID_EXIT_CODE
    captured = capsys.readouterr()
    assert "REFUSED-MEASUREMENT" in captured.err
    payload = json.loads(captured.out)
    assert payload["outcome"] == "REFUSED-MEASUREMENT"
    assert payload["power_state"] == power_state.as_dict()
    assert payload["measurements"] == {}


def test_benchmark_cli_records_power_state_provenance(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    power_state = _power_state()
    monkeypatch.setattr(bench, "probe_power_state", lambda: power_state)
    monkeypatch.setattr(bench, "detect_machine_class", lambda: MACHINE_CLASS)
    monkeypatch.setattr(bench, "measure_all", lambda: {"fixture": {"rate": 1.0}})

    assert bench.main([]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["outcome"] == "MEASURED"
    assert payload["power_state"] == power_state.as_dict()


def test_rebless_refuses_power_limited_measurement(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path,
) -> None:
    baseline_path = tmp_path / "perf_ratchet_baselines.json"
    original = BASELINE_PATH.read_text()
    baseline_path.write_text(original)
    monkeypatch.setattr(
        bench,
        "probe_power_state",
        lambda: _power_state(battery_percent=7),
    )
    monkeypatch.setattr(
        bench,
        "measure_all",
        lambda: pytest.fail("rebless measured after power refusal"),
    )

    assert bench.main(
        ["--rebless-ratchet", "--baseline", str(baseline_path)]
    ) == bench.MEASUREMENT_INVALID_EXIT_CODE
    captured = capsys.readouterr()
    assert "REFUSED-MEASUREMENT" in captured.err
    assert json.loads(captured.out)["reblessed"] is False
    assert baseline_path.read_text() == original


def test_rebless_ratchet_is_monotonic_up_only() -> None:
    baseline = load_baselines(BASELINE_PATH)
    optimizer_measurement = _synthetic_optimizer_measurement()
    actual_optimizer_stage = baseline["stages"][bench.OPTIMIZER_STAGE_NAME]
    measurements = {}
    for stage in STAGE_NAMES:
        if stage == bench.OPTIMIZER_STAGE_NAME:
            measurements[stage] = optimizer_measurement
        else:
            measurements[stage] = {
                "rate": baseline["stages"][stage]["ratchet_rate"] / 2.0,
                "rate_unit": "work_units_per_cpu_second",
                "details": {"hot_path": bench.STAGE_HOT_PATHS[stage]},
            }

    unbaselined_baseline = copy.deepcopy(baseline)
    if actual_optimizer_stage.get("status") == "needs_rebaseline":
        assert actual_optimizer_stage == {
            "status": "needs_rebaseline",
            "reason": bench.OPTIMIZER_REBASELINE_REASON,
        }
    else:
        assert "ratchet_rate" in actual_optimizer_stage
        unbaselined_baseline["stages"][bench.OPTIMIZER_STAGE_NAME] = {
            "status": "needs_rebaseline",
            "reason": bench.OPTIMIZER_REBASELINE_REASON,
        }
    unbaselined_result = reblessed_payload(
        unbaselined_baseline,
        measurements,
        machine_class=baseline["machine_class"],
    )
    for stage in STAGE_NAMES:
        if stage == bench.OPTIMIZER_STAGE_NAME:
            assert unbaselined_result["stages"][stage]["ratchet_rate"] == (
                optimizer_measurement["rate"]
            )
            assert "status" not in unbaselined_result["stages"][stage]
            assert "reason" not in unbaselined_result["stages"][stage]
        else:
            assert unbaselined_result["stages"][stage]["ratchet_rate"] == (
                baseline["stages"][stage]["ratchet_rate"]
            )

    rated_baseline = copy.deepcopy(baseline)
    if "ratchet_rate" in actual_optimizer_stage:
        rated_rate = float(actual_optimizer_stage["ratchet_rate"])
    else:
        rated_rate = float(optimizer_measurement["rate"]) * 2.0
    rated_stage = rated_baseline["stages"][bench.OPTIMIZER_STAGE_NAME]
    rated_stage["ratchet_rate"] = rated_rate
    rated_stage["rate_unit"] = "work_units_per_cpu_second"
    rated_stage["hot_path"] = bench.STAGE_HOT_PATHS[bench.OPTIMIZER_STAGE_NAME]
    rated_stage.pop("status", None)
    rated_stage.pop("reason", None)
    rated_measurements = dict(measurements)
    rated_measurements[bench.OPTIMIZER_STAGE_NAME] = dict(
        optimizer_measurement,
        rate=rated_rate / 2.0,
    )
    rated_result = reblessed_payload(
        rated_baseline,
        rated_measurements,
        machine_class=baseline["machine_class"],
    )
    assert rated_result["stages"][bench.OPTIMIZER_STAGE_NAME]["ratchet_rate"] == (
        rated_rate
    )
    for stage in STAGE_NAMES:
        if stage != bench.OPTIMIZER_STAGE_NAME:
            assert rated_result["stages"][stage] == baseline["stages"][stage]

    first = STAGE_NAMES[0]
    raised = copy.deepcopy(measurements)
    raised[first]["rate"] = baseline["stages"][first]["ratchet_rate"] + 1.0
    updated = reblessed_payload(
        copy.deepcopy(baseline),
        raised,
        machine_class=baseline["machine_class"],
    )
    assert updated["stages"][first]["ratchet_rate"] == pytest.approx(
        baseline["stages"][first]["ratchet_rate"] + 1.0
    )
