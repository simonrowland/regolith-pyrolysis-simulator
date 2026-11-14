"""Elapsed-time contracts on the ordinary recipe execution path.

These are numerical integration checks, not experimental accuracy budgets.
The operator split is first order; Richardson differences must decrease on
halving dt. Equal segmentation uses the same internal mesh throughout.
"""
import json
import math

import pytest

from simulator.run_executor import RunExecutor
from simulator.runner import PyrolysisRun, RunnerError
from tests.test_run_executor import _run
from tests.test_runner_smoke import SCENARIOS


def _execute(*, hours=2, dt_hr=0.25):
    run = _run(hours=hours, dt_hr=dt_hr)
    session = run._start_session()
    return run, session, RunExecutor().execute_session(session, hours=hours)


@pytest.mark.parametrize("seconds", [77.4, 1002.0])
@pytest.mark.parametrize("entry", ["public", "helper", "public_area"])
def test_exact_stop_uses_physical_elapsed_time(seconds, entry):
    run = _run(hours=seconds / 3600.0, dt_hr=60.0 / 3600.0)
    if entry == "public_area":
        # Exercise the public branch which starts its own session before
        # handing it to RunExecutor (also used by SiO pre-run controls).
        run.sio_start_temperature_c = 1050.0
    if entry == "helper":
        session = run._start_session()
        payload = run._run_session(session)
        snapshots = session.simulator.record.snapshots
        assert math.fsum(s.duration_h for s in snapshots) == pytest.approx(
            seconds / 3600.0, abs=2e-17
        )
        assert snapshots[-1].duration_h <= run.dt_hr
        assert session.simulator.melt.hour == seconds / 3600.0
    else:
        payload = run.run()
    assert payload["status"] == "ok"
    assert payload["run_metadata"]["hours_requested"] == seconds / 3600.0
    assert payload["run_metadata"]["hours_completed"] == seconds / 3600.0


def test_schedule_breakpoints_split_physics_not_only_control_reads():
    from tests.test_lab_geometry_runtime import dynamic_lab_schedule

    schedule = dynamic_lab_schedule()
    # This existing schedule has a 1 h duration; add a non-grid insertion
    # boundary without changing its values or declaring a new schema.
    schedule["duration_h"] = 1.0
    schedule["melt_temperature_C"] = [
        {"t_h": t, "value": value, "unit": "C"}
        for t, value in [(0.0, 1050.0), (0.07, 1051.0), (1.0, 1060.0)]
    ]
    for key in ("chamber_pressure_mbar",):
        schedule[key] = [schedule[key][0], {**schedule[key][-1], "t_h": 1.0}]
    # Only the melt/control schedule is needed; geometry profiles belong to
    # their separately tested bridge.
    schedule.pop("surface_temperature_C", None)
    run = _run(hours=1, dt_hr=0.25, lab_schedule=schedule)
    execution = RunExecutor().execute(run._session_config())
    assert execution.status == "ok", execution.error_message
    times = [s.hour for s in execution.snapshots]
    assert 0.07 in times
    assert times[-1] == 1.0
    assert execution.snapshots[0].duration_h == 0.07
    assert execution.snapshots[0].temperature_C == pytest.approx(1051.0)


def _endpoint(session):
    sim = session.simulator
    accounts = sim.atom_ledger.mol_by_account()
    return (
        accounts,
        sim.O2_vented_cumulative_kg,
        sim.O2_stored_cumulative_kg,
        sim.energy_tracker.cumulative_breakdown(),
        sim._alpha_authority_status_by_species_engaged,
    )


def test_equal_segmentation_retains_inventories_gases_energy_and_flags():
    endpoints = []
    for segments in ([2.0], [1.0, 1.0], [0.25] * 8):
        run = _run(hours=2, dt_hr=0.25)
        session = run._start_session()
        for duration in segments:
            execution = RunExecutor().execute_session(session, hours=duration)
            assert execution.status == "ok", execution.error_message
        assert session.simulator.melt.hour == 2
        for snapshot in session.simulator.record.snapshots:
            assert abs(snapshot.mass_balance_error_pct) < 5e-12
        endpoints.append(_endpoint(session))
    assert endpoints[0] == endpoints[1] == endpoints[2]


def test_first_order_campaign_refinement():
    # Explicit operator splitting is first order globally. In its asymptotic
    # regime ||y(dt/2)-y(dt/4)|| <= ||y(dt)-y(dt/2)||, up to roundoff.
    # This compares full inventories, not a tolerance tuned to one gas yield.
    endpoints = []
    for dt_hr in (1 / 16, 1 / 32, 1 / 64):
        _, session, execution = _execute(hours=0.25, dt_hr=dt_hr)
        assert execution.status == "ok", execution.error_message
        sim = session.simulator
        endpoints.append(sim.atom_ledger.mol_by_account("process.cleaned_melt"))
        assert abs(execution.snapshots[-1].mass_balance_error_pct) < 5e-12
    species = set().union(*endpoints)
    coarse = max(abs(endpoints[0].get(s, 0) - endpoints[1].get(s, 0)) for s in species)
    fine = max(abs(endpoints[1].get(s, 0) - endpoints[2].get(s, 0)) for s in species)
    roundoff = 64 * math.ulp(max(max(row.values()) for row in endpoints))
    assert fine <= coarse + roundoff


@pytest.mark.parametrize("scenario", SCENARIOS[:2], ids=lambda s: s["name"])
def test_explicit_hour_is_byte_identical_to_ordinary_hourly_run(scenario):
    kwargs = {key: scenario[key] for key in (
        "feedstock_id", "campaign", "hours", "additives_kg"
    )}
    kwargs.update(
        allow_fallback_vapor=True,
        allow_unmeasured_alpha_fallback=True,
        run_metadata_overrides={
            "started_at_utc": "2026-05-15T00:00:00Z",
            "kernel_commit_sha": "goal-18-fixture",
        },
    )
    ordinary = PyrolysisRun(**kwargs).run()
    explicit = PyrolysisRun(**kwargs, dt_hr=1.0).run()
    assert json.dumps(ordinary, sort_keys=True) == json.dumps(explicit, sort_keys=True)


@pytest.mark.parametrize("value", [-0.1, math.nan, math.inf, True])
def test_invalid_elapsed_duration_refuses_before_execution(value):
    with pytest.raises((RunnerError, ValueError, TypeError)):
        _run(hours=value)
