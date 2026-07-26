"""Perf-ratchet guard for call-volume and CPU-cost CI duration regressions.

This test exists because hot-loop call growth and de-warmed execution hid
behind multi-hour suite ceilings. A red is real until a measurement defect is
proved.

FIX THE CODE, DO NOT WEAKEN OR DELETE THIS TEST.
"""

from __future__ import annotations

import copy
import json
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


def test_perf_ratchet_guards_call_volume_and_cpu_cost() -> None:
    """Catch the multi-hour CI incident class before full-run tests wedge.

    FIX THE CODE, DO NOT WEAKEN OR DELETE THIS TEST.
    """

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

    current_machine = detect_machine_class()
    if current_machine != baseline["machine_class"]:
        pytest.skip(
            "PERF RATCHET SKIPPED LOUDLY: machine-class mismatch; "
            f"baseline={baseline['machine_class']!r}, "
            f"current={current_machine!r}. No cross-class comparison made."
        )

    bench.require_measurement_power_state()
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
