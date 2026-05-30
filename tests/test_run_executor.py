from __future__ import annotations

from simulator.run_executor import RunExecution, RunExecutor
from simulator.runner import PyrolysisRun
from simulator.trace import PhysicsTrace


def _run(**overrides) -> PyrolysisRun:
    kwargs = {
        "feedstock_id": "mars_basalt",
        "campaign": "C2A",
        "hours": 2,
        "additives_kg": {"C": 30.0},
        "allow_fallback_vapor": True,
        "allow_unmeasured_alpha_fallback": True,
        "run_metadata_overrides": {
            "started_at_utc": "2026-05-30T00:00:00Z",
            "kernel_commit_sha": "run-executor-fixture",
        },
    }
    kwargs.update(overrides)
    return PyrolysisRun(**kwargs)


def test_run_executor_returns_structured_execution():
    execution = RunExecutor().execute(_run()._session_config())

    assert isinstance(execution, RunExecution)
    assert execution.status == "ok"
    assert execution.error_message == ""
    assert execution.reason == ""
    assert execution.snapshots
    assert len(execution.per_hour) == len(execution.snapshots)
    assert isinstance(execution.trace, PhysicsTrace)
    assert execution.trace.snapshots == execution.snapshots
    assert isinstance(execution.operator_decisions, tuple)


def test_pyrolysis_run_is_executor_json_adapter():
    run = _run()
    execution = RunExecutor().execute(run._session_config())

    assert run._build_output(execution) == _run().run()


def test_run_executor_partial_path_sets_status_and_decisions():
    run = _run(
        feedstock_id="lunar_mare_low_ti",
        campaign="C0",
        hours=500,
        additives_kg={},
    )

    execution = RunExecutor().execute(run._session_config())

    assert execution.status == "partial"
    assert execution.error_message == ""
    assert execution.operator_decisions
    assert execution.shadow_trace == execution.operator_decisions
