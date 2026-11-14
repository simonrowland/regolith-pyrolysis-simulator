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
from tests.golden_sections import json_bytes
from tests.test_run_executor import _run
from tests.test_runner_smoke import FIXTURES_DIR, SCENARIOS


def _isothermal_session(dt_hr):
    run = _run(
        hours=2, dt_hr=dt_hr,
        runtime_campaign_overrides={"C2A": {"ramp_rate": 0.0}},
    )
    session = run._start_session()
    session.simulator.melt.temperature_C = 1450.0
    return session


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
        dict(getattr(sim, "_alpha_authority_status_by_species_engaged", {})),
        sim.condensation_model.last_sticking_alpha_provenance_notice,
    )


def test_equal_segmentation_retains_inventories_gases_energy_and_flags():
    endpoints = []
    for segments in ([2.0], [1.0, 1.0], [0.25] * 8):
        session = _isothermal_session(0.25)
        for duration in segments:
            execution = RunExecutor().execute_session(session, hours=duration)
            assert execution.status == "ok", execution.error_message
        assert session.simulator.melt.hour == 2
        assert session.simulator.atom_ledger.assert_balanced()
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
        session = _isothermal_session(dt_hr)
        execution = RunExecutor().execute_session(session, hours=0.25)
        assert execution.status == "ok", execution.error_message
        sim = session.simulator
        endpoints.append({
            (account, species): amount
            for account, rows in sim.atom_ledger.mol_by_account().items()
            for species, amount in rows.items()
        })
        assert sim.atom_ledger.assert_balanced()
        assert abs(execution.snapshots[-1].mass_balance_error_pct) < 5e-12
    species = set().union(*endpoints)
    coarse = max(abs(endpoints[0].get(s, 0) - endpoints[1].get(s, 0)) for s in species)
    fine = max(abs(endpoints[1].get(s, 0) - endpoints[2].get(s, 0)) for s in species)
    roundoff = 64 * math.ulp(max(max(row.values()) for row in endpoints))
    assert coarse > roundoff, "refinement must exercise changing inventories"
    assert fine <= coarse + roundoff


@pytest.mark.parametrize("scenario", SCENARIOS[:2], ids=lambda s: s["name"])
def test_hourly_bytes_preserve_values_with_reviewed_float_zero_sinks(scenario):
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
    historical = json.loads((FIXTURES_DIR / scenario["fixture"]).read_bytes())
    # Controller review seq 2 permits exactly this type-only serialization
    # correction at the thermal-budget owner. Every other byte stays pinned.
    for row in historical["per_hour_summary"]:
        sinks = row["energy_evaporation_breakdown_kWh"]
        for key in (
            "evaporation_enthalpy_sink",
            "product_vapor_enthalpy_sink",
            "reaction_disproportionation_enthalpy_sink",
        ):
            if sinks[key] == 0:
                sinks[key] = 0.0
    expected = json_bytes(historical)
    assert json_bytes(ordinary) == expected
    assert json_bytes(explicit) == expected


@pytest.mark.parametrize("value", [-0.1, math.nan, math.inf, True])
def test_invalid_elapsed_duration_refuses_before_execution(value):
    with pytest.raises((RunnerError, ValueError, TypeError)):
        _run(hours=value)


@pytest.mark.parametrize("dt_hr", [0, -0.01, math.nan, math.inf, True])
def test_invalid_substep_refuses_before_execution(dt_hr):
    with pytest.raises((RunnerError, ValueError, TypeError)):
        _run(dt_hr=dt_hr)


def test_rate_energy_scales_with_elapsed_time_without_rescaling_mre_energy():
    from simulator.energy import EnergyTracker
    from simulator.state import EvaporationFlux, MeltState, OverheadGas
    from tests.test_energy_budget import _na_vapor_pressures

    arguments = (
        MeltState(), OverheadGas(turbine_shaft_power_kW=4.0),
        EvaporationFlux(species_kg_hr={"Na": 1.0}),
    )
    records = [EnergyTracker().calculate_hour(
        *arguments, mre_kWh=3.0, vapor_pressures=_na_vapor_pressures(), dt_hr=dt
    ) for dt in (1.0, 0.25)]
    assert records[1].turbine_kWh == 1.0  # Physical definition of kWh.
    assert records[1].mre_kWh == records[0].mre_kWh == 3.0
    assert records[1].evaporation_thermal_kWh == records[0].evaporation_thermal_kWh / 4


@pytest.mark.parametrize("campaign", ["C2A", "C2B", "C3_K", "C3_NA", "C4", "C5", "C6"])
def test_participating_provider_controls_receive_real_dt(campaign, monkeypatch):
    session = _run(campaign=campaign, hours=0.25, dt_hr=0.25)._start_session()
    sim = session.simulator
    observed = []
    dispatch = sim._dispatch_only

    def record_duration(intent, *, control_inputs, **kwargs):
        if "dt_hr" in control_inputs:
            observed.append((intent, control_inputs["dt_hr"]))
        return dispatch(intent, control_inputs=control_inputs, **kwargs)

    monkeypatch.setattr(sim, "_dispatch_only", record_duration)
    execution = RunExecutor().execute_session(session, hours=0.25)
    assert execution.status in {"ok", "refused"}, execution.error_message
    assert observed, campaign
    assert all(dt == 0.25 for _, dt in observed), observed


def test_live_depletion_owner_receives_the_interval(monkeypatch):
    from simulator.core import PyrolysisSimulator

    durations = []
    calculate = PyrolysisSimulator._analytic_evaporation_depletion_rates

    def record_interval(sim, *args, **kwargs):
        durations.append(kwargs["dt_hr"])
        return calculate(sim, *args, **kwargs)

    monkeypatch.setattr(PyrolysisSimulator, "_analytic_evaporation_depletion_rates", record_interval)
    session = _isothermal_session(0.25)
    execution = RunExecutor().execute_session(session, hours=0.25)
    assert execution.status == "ok", execution.error_message
    assert durations
    assert set(durations) == {0.25}


def test_missing_depletion_dt_mutation_is_rejected(monkeypatch):
    from simulator.core import PyrolysisSimulator

    apply = PyrolysisSimulator._apply_analytic_evaporation_depletion

    def omit_duration(sim, flux, dt_hr=1.0):
        return apply(sim, flux)

    monkeypatch.setattr(PyrolysisSimulator, "_apply_analytic_evaporation_depletion", omit_duration)
    with pytest.raises(AssertionError):
        test_live_depletion_owner_receives_the_interval(monkeypatch)


def test_missing_energy_dt_mutation_is_rejected(monkeypatch):
    from simulator.energy import EnergyTracker

    calculate = EnergyTracker.calculate_hour

    def omit_duration(tracker, *args, **kwargs):
        kwargs.pop("dt_hr", None)
        return calculate(tracker, *args, **kwargs)

    monkeypatch.setattr(EnergyTracker, "calculate_hour", omit_duration)
    with pytest.raises(AssertionError):
        test_rate_energy_scales_with_elapsed_time_without_rescaling_mre_energy()


def test_reverting_to_hourly_step_breaks_unchanged_exact_stop(monkeypatch):
    from simulator.core import PyrolysisSimulator

    step = PyrolysisSimulator.step

    def hourly_only(sim, dt_hr=1.0):
        return step(sim)

    monkeypatch.setattr(PyrolysisSimulator, "step", hourly_only)
    with pytest.raises(AssertionError):
        test_exact_stop_uses_physical_elapsed_time(77.4, "helper")


def test_c6_subhour_ramp_dispatch_and_hold_use_the_same_sample(monkeypatch):
    from tests.test_lab_geometry_runtime import dynamic_lab_schedule

    schedule = dynamic_lab_schedule()
    schedule.pop("surface_temperature_C")
    schedule["melt_temperature_C"] = [
        {"t_h": 0.0, "value": 1200.0, "unit": "C"},
        {"t_h": 2.0, "value": 1400.0, "unit": "C"},
    ]
    session = _run(campaign="C6", hours=0.25, dt_hr=0.25, lab_schedule=schedule)._start_session()
    sim = session.simulator
    sim.melt.temperature_C = 1200.0
    comparisons = []
    dispatch_temperatures = []
    predicate = sim.campaign_mgr.c6_at_hold_target
    thermite = sim._step_thermite

    def compare(target, temperature):
        comparisons.append((target, temperature))
        return predicate(target, temperature)

    def dispatch():
        dispatch_temperatures.append(sim.melt.temperature_C)
        return thermite()

    monkeypatch.setattr(sim.campaign_mgr, "c6_at_hold_target", compare)
    monkeypatch.setattr(sim, "_step_thermite", dispatch)
    snapshot = session.advance(dt_hr=0.25).snapshot
    assert snapshot.temperature_C == 1225.0
    assert comparisons[0] == (1225.0, 1225.0)
    assert dispatch_temperatures == [1225.0]
    assert snapshot.c6_at_hold_target is True


def test_fractional_notice_ticks_and_redox_source_times_remain_distinct():
    session = _isothermal_session(0.25)
    sim = session.simulator
    times = [0.25, 0.5, 0.75, 1.0]
    sim._high_t_melt_activity_fallback_steps = [
        {"hour": hour, "campaign": "C2A", "parent_oxides": ["FeO"],
         "reason": {"code": "openimcc_evaluation_refused"}}
        for hour in times
    ]
    sim._sulfur_saturation_steps = [
        {"hour": hour, "notice": {"calibration_status": "out_of_range",
                                  "warnings": ["outside calibration"]}}
        for hour in times
    ]
    high_t = sim.high_t_melt_activity_run_notice()
    sulfur = sim.sulfur_saturation_run_notice()
    assert high_t["affected_tick_count"] == sulfur["count"] == 4
    assert high_t["first_hour"] == sulfur["first_hour"] == 0.25
    assert high_t["last_hour"] == sulfur["last_hour"] == 1.0

    for hour in times:
        assert session.advance(dt_hr=0.25).snapshot.hour == hour
        # Record a diagnostic source at the real endpoint. The bookkeeping
        # term does not commit a chemistry transition or alter inventories.
        sim._reset_redox_source_diagnostics_for_hour()
        sim._accumulate_redox_source_terms_for_hour(
            "_redox_source_terms_this_hr", {"diagnostic_probe": 1.0},
        )
        diagnostic = sim._redox_source_breakdown_diagnostic()
        snapshot = sim._make_snapshot()
        assert diagnostic["source_context"]["hour"] == hour
        assert diagnostic["source_hour"] == hour
        assert diagnostic["source_campaign_hour"] == hour
        assert snapshot.oxygen_reservoir["redox_source_hour"] == hour
        assert snapshot.oxygen_reservoir["redox_source_campaign_hour"] == hour
