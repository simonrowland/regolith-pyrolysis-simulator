"""Process timestep: hourly bit-identity, short-step convergence, closure.

The one-hour path is the current green integrator. A shorter step is the
same flux times dt. These tests do not regenerate a runner golden.
"""

from __future__ import annotations

import math
import textwrap
from pathlib import Path

import pytest

from simulator.energy import EnergyTracker
from simulator.lab_schedule import (
    LabScheduleValidationError,
    interpolate_schedule_points,
    schedule_sample_time_h,
)
from simulator.process_timestep import (
    ONE_HOUR,
    PROCESS_HOUR_S,
    ProcessTimestepRefusal,
    duration_step_count,
    exponential_approach_fraction,
    resolve_process_timestep,
    step_end_sample_h,
    timeseries_t_s,
)
from simulator.runner import (
    PresetRunnerError,
    PyrolysisRun,
    build_per_hour_summary,
)
from simulator.runner import _load_vacuum_preset_run_spec
from simulator.accounting.run_artifact import (
    CONFIDENCE_MAX_MASS_BALANCE_RESIDUAL_PCT,
)
from simulator.campaigns import (
    CampaignManager,
    CampaignSubhourHourClockRefusal,
)
from simulator.state import (
    BatchRecord,
    CampaignPhase,
    CondensationTrain,
    EvaporationFlux,
    HourSnapshot,
    MeltState,
    OverheadGas,
)
from tests.chemistry.conftest import _build_sim, _load_yaml


def _loaded_sim():
    """A loaded lunar-mare sim. YAML loaders live in the chemistry conftest.

    This file is not under ``tests/chemistry/``, so the module-scoped
    fixtures there are not injected. ``_load_yaml`` is the compatibility
    view those fixtures return.
    """

    return _build_sim(
        "lunar_mare_low_ti",
        _load_yaml("vapor_pressures.yaml"),
        _load_yaml("feedstocks.yaml"),
        _load_yaml("setpoints.yaml"),
    )


def _species_data(sim, species: str) -> dict:
    return (
        sim.vapor_pressures.get("metals", {}).get(species, {})
        or sim.vapor_pressures.get("oxide_vapors", {}).get(species, {})
    )


def _flux(species_kg_hr: dict[str, float]) -> EvaporationFlux:
    flux = EvaporationFlux(species_kg_hr=dict(species_kg_hr))
    flux.update_totals()
    return flux


def _parent_draw_kg(flux: EvaporationFlux, stoich_by_species: dict) -> float:
    return sum(
        flux.species_kg_hr[species] * stoich_by_species[species]["oxide_per_product_kg"]
        for species in stoich_by_species
    )


def test_dt_3600_matches_green_analytic_depletion_golden():
    """dt = 3600 s is the current depletion golden, bit for bit.

    Grouped Si/SiO exposes five parent-equivalents in one hour, so the
    green draw is ``available * -expm1(-5)``. Three Ti carriers expose
    three, so the green draw is ``available * -expm1(-3)``. Those
    expressions are the pin in
    ``tests/chemistry/test_analytic_evaporation_depletion.py``.

    Mutation: rewriting the one-hour approach as ``expm1`` changes the
    last bits at large tau, and an hourly summary must not grow ``t_s``.
    """

    sim = _loaded_sim()
    assert sim.process_timestep is ONE_HOUR
    assert sim.process_dt_hr() == 1.0

    available_sio2_kg = sim.atom_ledger.kg_by_account("process.cleaned_melt")["SiO2"]
    stoich_si = sim._evaporation_stoich("Si", _species_data(sim, "Si"))
    stoich_sio = sim._evaporation_stoich("SiO", _species_data(sim, "SiO"))
    raw_si = {
        "Si": 3.0 * available_sio2_kg / stoich_si["oxide_per_product_kg"],
        "SiO": 2.0 * available_sio2_kg / stoich_sio["oxide_per_product_kg"],
    }
    default_si = sim._apply_analytic_evaporation_depletion(_flux(raw_si))
    explicit_si = sim._apply_analytic_evaporation_depletion(
        _flux(raw_si),
        dt_hr=1.0,
    )
    assert default_si.species_kg_hr == explicit_si.species_kg_hr
    si_draw = _parent_draw_kg(
        default_si,
        {"Si": stoich_si, "SiO": stoich_sio},
    )
    assert si_draw == available_sio2_kg * (-math.expm1(-5.0))

    carriers = ("Ti", "TiO", "TiO2_gas")
    stoich_ti = {
        species: sim._evaporation_stoich(species, _species_data(sim, species))
        for species in carriers
    }
    available_tio2_kg = sim.atom_ledger.kg_by_account("process.cleaned_melt")["TiO2"]
    raw_ti = {
        species: available_tio2_kg / row["oxide_per_product_kg"]
        for species, row in stoich_ti.items()
    }
    default_ti = sim._apply_analytic_evaporation_depletion(_flux(raw_ti))
    explicit_ti = sim._apply_analytic_evaporation_depletion(
        _flux(raw_ti),
        dt_hr=1.0,
    )
    assert default_ti.species_kg_hr == explicit_ti.species_kg_hr
    ti_draw = _parent_draw_kg(default_ti, stoich_ti)
    assert ti_draw == available_tio2_kg * (-math.expm1(-3.0))

    assert duration_step_count(24, ONE_HOUR) == 24
    assert step_end_sample_h(3, 1.0) == float(3) + 1.0
    assert resolve_process_timestep(3600) is ONE_HOUR
    assert resolve_process_timestep(3600.0) is ONE_HOUR
    assert timeseries_t_s(1, ONE_HOUR) is None

    tau_s = 1.0e8
    hourly_approach = exponential_approach_fraction(tau_s, PROCESS_HOUR_S)
    assert hourly_approach == 1.0 - math.exp(-3600.0 / tau_s)
    assert hourly_approach != -math.expm1(-3600.0 / tau_s)

    snapshot = sim._make_snapshot()
    assert not hasattr(snapshot, "t_s")
    hourly_summary = build_per_hour_summary(
        sim,
        snapshot,
        include_fe_redox_split=False,
    )
    assert "t_s" not in hourly_summary
    snapshot.t_s = 60.0
    short_summary = build_per_hour_summary(
        sim,
        snapshot,
        include_fe_redox_split=False,
    )
    assert short_summary["t_s"] == 60.0
    assert short_summary["hour"] == int(snapshot.hour)


def _deplete_constant_k(sim, *, dt_s: float, k_per_h: float, t_h: float) -> float:
    """Integrate production depletion with k held constant.

    Each step sets the raw parent draw to ``k * remaining``, which is how
    the production map defines ``k_hr``. The map then applies
    ``-expm1(-k * dt)``.
    """

    species = "TiO2_gas"
    stoich = sim._evaporation_stoich(species, _species_data(sim, species))
    oxide_per = float(stoich["oxide_per_product_kg"])
    parent = str(stoich["parent_oxide"])
    remaining = float(
        sim.atom_ledger.kg_by_account("process.cleaned_melt")[parent]
    )
    initial = remaining
    dt_h = dt_s / float(PROCESS_HOUR_S)
    steps = duration_step_count(t_h, resolve_process_timestep(dt_s))
    for _ in range(steps):
        raw_rate = (k_per_h * remaining) / oxide_per
        rates = sim._analytic_evaporation_depletion_rates(
            {species: raw_rate},
            dt_hr=dt_h,
            phase_scalar=1.0,
            cleaned_melt_kg={parent: remaining},
            available_o2_kg=1.0e6,
        )
        product_kg = float(rates[species]) * dt_h
        remaining = remaining - product_kg * oxide_per
    assert initial > 0.0
    return remaining / initial


def test_subhour_evaporation_converges_to_exponential():
    """Short steps land on the analytic exponential depletion.

    Closed form. For isothermal evaporation at constant specific rate
    ``k`` (1/hour), the parent inventory obeys

        dM/dt = -k M

    with ``t`` in hours. Separating and integrating from ``M0`` at
    ``t = 0`` gives

        M(t) = M0 exp(-k t)

    The production step freezes ``k = (raw parent draw kg/h) / parent kg``
    and removes the fraction ``-expm1(-k dt)``, which is exactly
    ``1 - exp(-k dt)``. Holding ``k`` constant by scaling the raw rate
    with the remaining parent makes every step that map. Over a fixed
    window the composition of those steps is ``exp(-k t)`` up to roundoff,
    at dt = 600, 60 and 6 s.

    Mutation: explicit Euler ``M <- M (1 - k dt)`` at dt = 600 s is the
    sibling test and misses the 1e-9 relative tolerance.
    """

    sim = _loaded_sim()
    k_per_h = 1.0
    t_h = 1.0
    expected = math.exp(-k_per_h * t_h)
    for dt_s in (600.0, 60.0, 6.0):
        fraction = _deplete_constant_k(
            sim,
            dt_s=dt_s,
            k_per_h=k_per_h,
            t_h=t_h,
        )
        assert fraction == pytest.approx(expected, rel=1.0e-9)


def test_explicit_euler_misses_the_exponential_tolerance():
    """The Euler mutation of the depletion map is outside the tolerance.

    At ``k = 1/h``, ``dt = 600 s = 1/6 h`` and ``t = 1 h`` there are six
    steps. Euler is ``(1 - 1/6) ** 6 = (5/6) ** 6``. The closed form is
    ``exp(-1)``. The gap is about nine percent, far outside 1e-9.
    """

    euler = (5.0 / 6.0) ** 6
    analytic = math.exp(-1.0)
    assert abs(euler - analytic) / analytic > 1.0e-9


def test_small_dt_evaporation_closes_mass_balance():
    """A 60 s evaporation transition closes, and the draw is the short integral.

    Mutation: leaving the ledger on a one-hour tick removes about ``k``
    parent-fractions instead of ``1 - exp(-k dt)``.
    """

    sim = _loaded_sim()
    dt_s = 60.0
    sim.process_timestep = resolve_process_timestep(dt_s)
    dt_h = sim.process_dt_hr()
    species = "TiO2_gas"
    stoich = sim._evaporation_stoich(species, _species_data(sim, species))
    parent = str(stoich["parent_oxide"])
    oxide_per = float(stoich["oxide_per_product_kg"])
    available = float(sim.atom_ledger.kg_by_account("process.cleaned_melt")[parent])
    k_per_h = 1.0
    raw_rate = (k_per_h * available) / oxide_per
    smoothed = sim._apply_analytic_evaporation_depletion(
        _flux({species: raw_rate}),
        dt_hr=dt_h,
    )
    kg_before = sim.atom_ledger.total_kg_by_account()
    total_before = sum(kg_before.values())
    parent_before = float(
        sim.atom_ledger.kg_by_account("process.cleaned_melt")[parent]
    )
    sim._configure_condensation_operating_conditions(smoothed)
    sim._route_to_condensation(smoothed)
    sim._update_melt_composition(smoothed)
    parent_after = float(
        sim.atom_ledger.kg_by_account("process.cleaned_melt").get(parent, 0.0)
    )
    expected_parent_draw = available * (-math.expm1(-k_per_h * dt_h))
    assert parent_before - parent_after == pytest.approx(
        expected_parent_draw,
        rel=1.0e-9,
    )
    one_hour_draw = available * (-math.expm1(-k_per_h * 1.0))
    assert parent_before - parent_after < one_hour_draw * 0.5
    kg_after = sim.atom_ledger.total_kg_by_account()
    total_after = sum(kg_after.values())
    closure_pct = abs(total_before - total_after) / total_before * 100.0
    assert closure_pct <= CONFIDENCE_MAX_MASS_BALANCE_RESIDUAL_PCT
    assert abs(sim._make_snapshot().mass_balance_error_pct) <= (
        CONFIDENCE_MAX_MASS_BALANCE_RESIDUAL_PCT
    )


def test_energy_one_hour_is_unscaled_and_short_step_scales_power_not_mre():
    """Turbine energy is shaft power times the step. MRE energy is not rescaled.

    Mutation: scaling ``mre_kWh`` by dt double-counts Faraday, and skipping
    the scale leaves a 15-minute step with a full hour of turbine energy.
    """

    vapor = {
        "metals": {
            "Na": {
                "parent_oxide": "Na2O",
                "molar_mass_g_mol": 22.98976928,
            }
        }
    }
    melt = MeltState(campaign=CampaignPhase.C2A)
    overhead = OverheadGas(turbine_shaft_power_kW=2.0)
    flux = _flux({"Na": 1.0})

    default = EnergyTracker().calculate_hour(
        melt, overhead, flux, mre_kWh=11.0, vapor_pressures=vapor,
    )
    hourly = EnergyTracker().calculate_hour(
        melt, overhead, flux, mre_kWh=11.0, vapor_pressures=vapor, dt_h=1.0,
    )
    short = EnergyTracker().calculate_hour(
        melt, overhead, flux, mre_kWh=11.0, vapor_pressures=vapor, dt_h=0.25,
    )
    assert hourly.turbine_kWh == default.turbine_kWh
    assert hourly.condenser_kWh == default.condenser_kWh
    assert hourly.mre_kWh == 11.0
    assert hourly.turbine_kWh == 2.0
    assert short.turbine_kWh == 2.0 * 0.25
    assert short.condenser_kWh == hourly.condenser_kWh * 0.25
    assert short.latent_kWh == hourly.latent_kWh * 0.25
    assert short.mre_kWh == 11.0


def _write_lab_preset(
    tmp_path: Path,
    *,
    duration_h: float,
    process_dt_s: float | None,
) -> Path:
    dt_line = (
        ""
        if process_dt_s is None
        else f"\n                process_dt_s: {process_dt_s}"
    )
    preset_path = tmp_path / f"preset_{duration_h}_{process_dt_s}.yaml"
    preset_path.write_text(
        textwrap.dedent(
            f"""
            schema_version: vacuum_pyrolysis_preset.v1
            preset_kind: faithful_with_remediation_twin
            lab_schedule:
              id: bridge_schedule
              duration_h: {duration_h}
              interpolation: piecewise_linear
              interpolation_source_class: assumption_with_sensitivity_marker
              interpolation_citation_id: bridge_test
              interpolation_extraction_note: bridge test schedule
              furnace_ceiling_C: 1600.0
              melt_temperature_C:
                - {{t_h: 0.0, value: 25.0, unit: C}}
                - {{t_h: {duration_h}, value: 1500.0, unit: C}}
              chamber_pressure_mbar:
                - {{t_h: 0.0, value: 13.0, unit: mbar}}
                - {{t_h: {duration_h}, value: 13.0, unit: mbar}}
              gas_boundary:
                background_gas:
                  species: Ar
                  mole_fraction: 1.0
                  source_class: assumption_with_sensitivity_marker
                  citation_id: bridge_test
                  digest: bridge_argon_boundary
                imposed_flow:
                  value: 0.3
                  unit: NL_min
                  source_class: assumption_with_sensitivity_marker
                  citation_id: bridge_test
                  digest: bridge_flow_boundary
                pressure_control:
                  mode: flow_through_with_pump
                  source_class: assumption_with_sensitivity_marker
                  citation_id: bridge_test
                  digest: bridge_pressure_control
              surface_temperature_C:
                witness:
                  - {{t_h: 0.0, value: 25.0, unit: C}}
                  - {{t_h: {duration_h}, value: 300.0, unit: C}}
            lab_geometry:
              id: bridge_geometry
              scale: gram_lab
              equipment_sizing: lab_fixed_geometry
              sample:
                mass_g: 2.0
              surfaces:
                - id: witness
                  role: condenser
                  area_m2: 0.001
                  view_factor_from_melt: 0.25
                  line_of_sight_to_melt: false
                  temperature_profile: witness
                  source_class: assumption_with_sensitivity_marker
                  sensitivity_marker: bridge_witness_surface_sweep
                  extraction_note: bridge test declared surface
            pair:
              faithful:
                feedstock_id: lunar_mare_low_ti
                schedule_id: bridge_schedule
                geometry_id: bridge_geometry
                duration_h: {duration_h}{dt_line}
                mitigation: none
              remediation:
                feedstock_id: lunar_mare_low_ti
                schedule_id: bridge_schedule
                geometry_id: bridge_geometry
                duration_h: {duration_h}
                mitigation:
                  pO2_cover:
                    enabled: true
                    setpoint_mbar: 1.0e-4
                    p_total_mbar: 13.0
                    effective_pO2_achieved_mbar: 1.0e-4
                    limited_by_total_pressure: false
                  alkali_shuttle_deconfliction:
                    enabled: false
            digests:
              schedule_digest: bridge_schedule_digest
              gas_boundary_digest: bridge_gas_boundary_digest
              geometry_digest: bridge_geometry_digest
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    return preset_path


def test_integer_hour_preset_keeps_hourly_metadata(tmp_path: Path):
    path = _write_lab_preset(tmp_path, duration_h=1.0, process_dt_s=None)
    spec = _load_vacuum_preset_run_spec(path, "faithful")
    assert spec.hours == 1
    assert spec.process_dt_s == 3600.0
    assert spec.step_count is None
    assert spec.provenance["duration_h"] == 1.0
    assert "process_dt_s" not in spec.provenance
    assert "step_count" not in spec.provenance


def test_quarter_hour_preset_runs_and_samples_inside_the_window(tmp_path: Path):
    """A 0.25 h program at 900 s is one step, sampled at the printed end.

    Piecewise-linear interpolation of the printed points is the sample.
    t = 1 h is outside this window. Time-warping the program onto one
    hour would ask for that point and refuse.

    Mutation: accepting a dt that does not divide the duration, or
    sampling at campaign_hour + 1 h, lands outside the window.
    """

    path = _write_lab_preset(tmp_path, duration_h=0.25, process_dt_s=900)
    spec = _load_vacuum_preset_run_spec(path, "faithful")
    assert spec.hours == 1
    assert spec.process_dt_s == 900.0
    assert spec.step_count == 1
    assert spec.provenance["duration_h"] == 0.25
    assert spec.provenance["process_dt_s"] == 900.0
    assert spec.provenance["step_count"] == 1

    schedule = spec.lab_schedule
    sample_h = schedule_sample_time_h(schedule, 0, dt_h=0.25)
    assert sample_h == 0.25
    assert interpolate_schedule_points(
        schedule["melt_temperature_C"],
        sample_h,
    ) == 1500.0
    midpoint = interpolate_schedule_points(
        schedule["melt_temperature_C"],
        0.125,
    )
    assert midpoint == pytest.approx(762.5)
    with pytest.raises(LabScheduleValidationError):
        interpolate_schedule_points(schedule["melt_temperature_C"], 1.0)

    refused = _write_lab_preset(tmp_path, duration_h=0.25, process_dt_s=7)
    with pytest.raises(PresetRunnerError, match="timestep_does_not_divide_duration"):
        _load_vacuum_preset_run_spec(refused, "faithful")
    hourly = _write_lab_preset(tmp_path, duration_h=0.25, process_dt_s=None)
    with pytest.raises(PresetRunnerError, match="timestep_does_not_divide_duration"):
        _load_vacuum_preset_run_spec(hourly, "faithful")
    with pytest.raises(ProcessTimestepRefusal) as bad_dt:
        resolve_process_timestep(0)
    assert bad_dt.value.reason == "process_dt_not_positive"
    with pytest.raises(ProcessTimestepRefusal):
        resolve_process_timestep(True)
    assert duration_step_count(0.1, resolve_process_timestep(60)) == 6
    assert duration_step_count(1.0, resolve_process_timestep(60)) == 60
    with pytest.raises(ProcessTimestepRefusal, match="timestep_does_not_divide_duration"):
        duration_step_count(1.0, resolve_process_timestep(7))

    run = PyrolysisRun(
        feedstock_id=spec.feedstock_id,
        campaign="C0",
        hours=spec.hours,
        mass_kg=spec.mass_kg,
        process_dt_s=spec.process_dt_s,
        lab_schedule=spec.lab_schedule,
        setpoints_patch={"lab_geometry": spec.lab_geometry},
        allow_fallback_vapor=True,
        allow_unmeasured_alpha_fallback=True,
    )
    session = run._start_session()
    assert session.simulator.process_timestep.dt_s == 900.0
    assert session.simulator.process_dt_hr() == 0.25
    result = session.advance()
    assert result.per_hour_summary["hour"] == 1
    assert result.per_hour_summary["t_s"] == 900.0
    assert result.snapshot.duration_h == 0.25
    assert session.simulator.melt.campaign_hour == 0.25
    enforcement = session.simulator.campaign_mgr.last_pO2_enforcement
    assert enforcement is not None
    assert enforcement["schedule_time_h"] == 0.25


def _cro2_parent(sim) -> tuple[str, float, float, float]:
    stoich = sim._evaporation_stoich("CrO2", _species_data(sim, "CrO2"))
    parent = str(stoich["parent_oxide"])
    available_parent = float(
        sim.atom_ledger.kg_by_account("process.cleaned_melt")[parent]
    )
    oxide_per = float(stoich["oxide_per_product_kg"])
    o2_per = float(stoich["O2_per_product_kg"])
    assert o2_per < 0.0
    assert available_parent > 0.0
    return parent, available_parent, oxide_per, abs(o2_per)


def test_cro2_oxygen_integral_at_one_hour_matches_the_golden():
    """dt_hr = 1 keeps the CrO2 oxygen integral the chemistry file pins.

    The parent map at three parent-equivalents an hour removes
    ``available * -expm1(-3)``. The oxygen tail then limits that draw
    by the overhead O2 integral. At one hour the rate scale is
    ``-expm1(-k) / k``, which is the expression this fix must not move.

    Mutation: dividing the allowed oxygen mass by ``dt_hr`` a second
    time, or dropping the division, changes this row. The division is
    bit-identical to the old quotient at ``dt_hr == 1``.
    """

    sim = _loaded_sim()
    parent, available_parent, oxide_per, o2_per = _cro2_parent(sim)
    available_o2_kg = 0.001
    raw_rate = 3.0 * available_parent / oxide_per
    rates = sim._analytic_evaporation_depletion_rates(
        {"CrO2": raw_rate},
        dt_hr=1.0,
        phase_scalar=1.0,
        cleaned_melt_kg={parent: available_parent},
        available_o2_kg=available_o2_kg,
    )
    required_o2_kg = rates["CrO2"] * o2_per
    parent_only_product_kg = (
        available_parent * (-math.expm1(-3.0)) / oxide_per
    )
    parent_only_o2_draw_kg = parent_only_product_kg * o2_per
    expected_fraction = max(
        0.0,
        min(
            math.nextafter(1.0, 0.0),
            -math.expm1(-(parent_only_o2_draw_kg / available_o2_kg)),
        ),
    )
    expected_o2_draw_kg = available_o2_kg * expected_fraction
    assert required_o2_kg <= available_o2_kg
    assert required_o2_kg < parent_only_o2_draw_kg
    assert required_o2_kg == pytest.approx(expected_o2_draw_kg, rel=1e-12)


def test_cro2_short_step_oxygen_rate_matches_closed_form():
    """A 60 s oxygen-limited CrO2 rate is the step integral over dt.

    Closed form. Parent inventory and overhead O2 each follow
    ``dM/dt = -k M``. The average product rate over the step is the
    parent rate times

        -expm1(-k dt) / (k dt)

    where ``k`` is the oxygen demand (kg/h) divided by the O2 on hand
    (kg), and ``dt`` is in hours. The pre-fix scale omitted ``/ dt``
    and stored ``dt`` times that rate. The ledger multiplies by ``dt``
    again, so the committed mass was short by ``dt``.

    Mutation: ``allowed_draw_kg / required_o2_kg_hr`` without ``* dt_hr``
    returns the defect rate below, sixty times smaller at this step.
    """

    sim = _loaded_sim()
    parent, available_parent, oxide_per, o2_per = _cro2_parent(sim)
    dt_hr = 1.0 / 60.0
    parent_k = 1.0
    raw_rate = parent_k * available_parent / oxide_per
    parent_fraction = -math.expm1(-parent_k * dt_hr)
    current_rate = (available_parent * parent_fraction / oxide_per) / dt_hr
    k_per_h = 60.0
    available_o2_kg = (current_rate * o2_per) / k_per_h
    true_rate = current_rate * (
        -math.expm1(-k_per_h * dt_hr) / (k_per_h * dt_hr)
    )
    defect_rate = current_rate * (-math.expm1(-k_per_h * dt_hr) / k_per_h)
    assert true_rate == pytest.approx(defect_rate / dt_hr, rel=1e-12)
    assert true_rate != pytest.approx(defect_rate, rel=1e-6)

    rates = sim._analytic_evaporation_depletion_rates(
        {"CrO2": raw_rate},
        dt_hr=dt_hr,
        phase_scalar=1.0,
        cleaned_melt_kg={parent: available_parent},
        available_o2_kg=available_o2_kg,
    )
    assert rates["CrO2"] == pytest.approx(true_rate, rel=1e-12)
    assert rates["CrO2"] != pytest.approx(defect_rate, rel=1e-6)


def test_second_campaign_hour_is_campaign_local(tmp_path: Path):
    """A campaign started after step 0 counts its own steps.

    ``start_campaign`` stores ``_campaign_start_hour`` and zeros
    ``campaign_hour``. One short step later the elapsed hours are
    ``dt_h``, the product of one step and the step length. The defect
    wrote ``melt.hour * dt_h`` and jumped to the run's global time.

    Mutation: restoring ``float(melt.hour) * dt_h`` yields ``1.25``
    here (step index 5 times 0.25 h) instead of ``0.25``.
    """

    path = _write_lab_preset(tmp_path, duration_h=0.25, process_dt_s=900)
    spec = _load_vacuum_preset_run_spec(path, "faithful")
    run = PyrolysisRun(
        feedstock_id=spec.feedstock_id,
        campaign="C0",
        hours=spec.hours,
        mass_kg=spec.mass_kg,
        process_dt_s=spec.process_dt_s,
        lab_schedule=spec.lab_schedule,
        setpoints_patch={"lab_geometry": spec.lab_geometry},
        allow_fallback_vapor=True,
        allow_unmeasured_alpha_fallback=True,
    )
    session = run._start_session()
    sim = session.simulator
    sim.melt.hour = 4
    sim.start_campaign(CampaignPhase.C0)
    assert sim.melt.campaign_hour == 0
    assert sim._campaign_start_hour == 4
    session.advance()
    assert sim.melt.hour == 5
    assert sim.melt.campaign_hour == pytest.approx(0.25)
    assert sim.melt.campaign_hour != pytest.approx(5 * 0.25)


def test_c7_one_shot_keeps_configured_hold_on_a_short_step():
    """C7 integrates the configured hold once, not the process step.

    The host carries a 60 s timestep and a 1 h configured hold. The
    extent's ``c7_hold_time_s`` is 3600. A 2 h hold is 7200. The defect
    replaced either hold with ``process_dt_hr()`` and stored 60 s.

    Mutation: the ``is_one_hour`` branch that assigns
    ``hold_time_h = process_dt_hr()`` makes both rows 60.
    """

    from types import MethodType, SimpleNamespace

    from simulator.extraction import ExtractionMixin

    setpoints = _load_yaml("setpoints.yaml")
    sim = SimpleNamespace(
        melt=SimpleNamespace(
            campaign=CampaignPhase.C7_CA_ALUMINOTHERMIC,
            campaign_hour=0,
            melt_surface_area_m2=0.2,
            p_total_mbar=0.05,
            stir_state=SimpleNamespace(axial=1.0),
            stir_factor=1.0,
        ),
        vapor_pressures={
            "metals": {
                "Ca": {
                    "evaporation_alpha": {"value": 0.5},
                    "pure_component_antoine": {
                        "A": 10.0,
                        "B": 15000.0,
                        "C": 0.0,
                    },
                }
            }
        },
        setpoints=setpoints,
        campaign_mgr=CampaignManager(setpoints),
        _resolve_condensation_carrier_gas=lambda: "N2",
        overhead=SimpleNamespace(headspace_temperature_K=1000.0),
        overhead_model=SimpleNamespace(pipe_diameter_m=0.12),
        _evaporation_bulk_partial_pressure_pa=lambda _species: 0.0,
        _silent_zero_notes=[],
        process_timestep=resolve_process_timestep(60),
    )
    sim.process_dt_hr = lambda: sim.process_timestep.dt_h
    sim._c7_float = ExtractionMixin._c7_float
    sim._c7_clamp = ExtractionMixin._c7_clamp
    sim._c7_bool = ExtractionMixin._c7_bool
    sim._c7_hold_temperature_C = MethodType(
        ExtractionMixin._c7_hold_temperature_C,
        sim,
    )
    sim._c7_knob_diag = MethodType(ExtractionMixin._c7_knob_diag, sim)

    def _hold_s(hold_time_h: float) -> float:
        _extent, diagnostic = ExtractionMixin._c7_transport_extent_mol(
            sim,
            {"hold_time_h": hold_time_h, "p_total_mbar": 0.05},
            ca_per_extent=1.0,
        )
        return float(diagnostic["c7_hold_time_s"])

    assert sim.process_dt_hr() == pytest.approx(60.0 / 3600.0)
    assert _hold_s(1.0) == 3600.0
    assert _hold_s(2.0) == 7200.0
    assert _hold_s(1.0) != 60.0


def _native_fe_case(sim, *, dt_s: float):
    sim.process_timestep = resolve_process_timestep(dt_s)
    sim.melt.temperature_C = 1700.0
    sim.melt.p_total_mbar = 0.0
    sim.overhead.pressure_mbar = 0.0
    sim.overhead.composition = {"Fe": 0.0}
    return sim._native_fe_partition_diagnostic(100.0)


def test_native_fe_residual_stays_mol_per_hour_on_a_short_step():
    """Step-moles are not subtracted from the hourly Fe capacity.

    The diagnostic's ``native_fe_vapor_capacity_mol_hr`` is an hourly
    rate. On a short step ``native_fe_vapor_mol`` is moles this step.
    The residual the kg/h cap reads is

        capacity_mol_hr - vapor_mol / dt_h

    The defect stored ``capacity_mol_hr - vapor_mol``. The saturation
    split stores the same hourly residual from the committed vapor.
    On the hour the stored value stays ``capacity - committed``.

    A wide crucible keeps the committed vapor below one step of
    capacity, so the short residual is not zero on both formulas.
    The kg/h cap is that residual times ``M_Fe``. The defect cap is
    the leftover step kilograms, and a rate at the hourly capacity
    must not be cut down to that step mass.

    Mutation: ``capacity_mol_hr - vapor_mol`` in the diagnostic, or
    ``capacity_mol_hr * dt_h - committed`` in the split, returns the
    defect residual.
    """

    short = _native_fe_case(_loaded_sim(), dt_s=900.0)
    vapor_mol = float(short["native_fe_vapor_mol"])
    capacity_mol_hr = float(short["native_fe_vapor_capacity_mol_hr"])
    dt_h = 0.25
    assert vapor_mol > 0.0
    assert capacity_mol_hr > 0.0
    true_residual = max(0.0, capacity_mol_hr - vapor_mol / dt_h)
    defect_residual = max(0.0, capacity_mol_hr - vapor_mol)
    assert true_residual != defect_residual
    assert short["ordinary_melt_fe_residual_capacity_mol_hr"] == true_residual

    hourly = _native_fe_case(_loaded_sim(), dt_s=3600.0)
    assert hourly["ordinary_melt_fe_residual_capacity_mol_hr"] == max(
        0.0,
        float(hourly["native_fe_vapor_capacity_mol_hr"])
        - float(hourly["native_fe_vapor_mol"]),
    )

    sim = _loaded_sim()
    sim.process_timestep = resolve_process_timestep(900.0)
    sim.campaign_mgr._process_dt_h = sim.process_dt_hr()
    sim.melt.temperature_C = 1600.0
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = -10.0
    sim._sync_oxygen_reservoir_mirror()
    sim.melt.melt_surface_area_m2 = 1.0e6
    sim._apply_native_fe_saturation_split()
    partition = sim._last_native_fe_partition_diagnostic
    capacity = float(partition["native_fe_vapor_capacity_mol_hr"])
    committed = float(partition["native_fe_vapor_mol"])
    stored = float(sim._native_fe_vapor_residual_capacity_mol_this_hr)
    true_stored = max(0.0, capacity - committed / dt_h)
    defect_stored = max(0.0, capacity * dt_h - committed)
    assert capacity > 0.0
    assert true_stored != defect_stored
    assert stored == true_stored
    assert partition["ordinary_melt_fe_residual_capacity_mol_hr"] == stored
    molar_mass = (
        float(partition["native_fe_vapor_capacity_kg_hr"]) / capacity
    )
    reader_cap_kg_hr = stored * molar_mass
    defect_cap_kg_hr = defect_stored * molar_mass
    hourly_rate_kg_hr = float(partition["native_fe_vapor_capacity_kg_hr"])
    assert min(hourly_rate_kg_hr, reader_cap_kg_hr) != pytest.approx(
        min(hourly_rate_kg_hr, defect_cap_kg_hr),
        rel=1e-9,
    )


def _endpoint_melt(
    campaign: CampaignPhase,
    hour: int,
    *,
    temperature_C: float,
) -> MeltState:
    return MeltState(
        campaign=campaign,
        campaign_hour=hour,
        temperature_C=temperature_C,
        composition_kg={"SiO2": 50.0, "Al2O3": 20.0, "CaO": 30.0},
    )


def _endpoint_flux(**species_kg_hr: float) -> EvaporationFlux:
    return EvaporationFlux(
        total_kg_hr=sum(species_kg_hr.values()),
        species_kg_hr=dict(species_kg_hr),
    )


def test_subhour_c2a_duration_clamp_and_c4_window_are_refused():
    """A short step must not walk an hour-counted campaign clock.

    C2A stage length is still ``max(1, int(duration_h))``. C4's
    in-window time still counts snapshots. Both raise
    ``subhour_hour_clock_refused`` when the process step is not one
    hour. The hourly call keeps the clamp and the snapshot count.
    C0 and C7 at the same short step do not raise.

    Mutation: deleting ``_refuse_subhour_hour_clock`` from a live site
    lets that site return a stage, a hold, or ``False`` instead of
    the typed refusal. Deleting it from C0 would be a different bug;
    this test requires the short C0 call to return.
    """

    manager = CampaignManager(_load_yaml("setpoints.yaml"))
    stages = manager._c2a_staged_enabled_stages()
    clamped_hours = sum(
        max(1, int(float(stage["duration_h"]))) for stage in stages
    )
    assert manager._configured_staged_max_hold_hr(
        CampaignPhase.C2A_STAGED
    ) == pytest.approx(clamped_hours)
    selected = manager._c2a_staged_stage_by_hour(0, stages)
    assert selected is not None
    assert selected["name"] == "alkali_early_fe"

    manager._process_dt_h = 0.25
    with pytest.raises(CampaignSubhourHourClockRefusal) as max_hold:
        manager._configured_staged_max_hold_hr(CampaignPhase.C2A_STAGED)
    assert max_hold.value.reason == "subhour_hour_clock_refused"
    assert max_hold.value.terminal_refusal is True
    assert max_hold.value.diagnostic["clock"] == "c2a_staged_duration_clamp"
    assert max_hold.value.diagnostic["process_dt_h"] == 0.25

    with pytest.raises(CampaignSubhourHourClockRefusal) as stage_pick:
        manager._c2a_staged_stage_by_hour(0, stages)
    assert stage_pick.value.diagnostic["clock"] == "c2a_staged_duration_clamp"

    record = BatchRecord(snapshots=[
        HourSnapshot(
            campaign=CampaignPhase.C4,
            temperature_C=1580.0,
            evap_flux=_endpoint_flux(Mg=0.019),
        )
    ])
    melt = _endpoint_melt(CampaignPhase.C4, 1, temperature_C=1580.0)
    flux = _endpoint_flux(Mg=0.019)
    with pytest.raises(CampaignSubhourHourClockRefusal) as window:
        manager.check_endpoint(melt, flux, CondensationTrain(), record)
    assert window.value.diagnostic["clock"] == "c4_snapshot_window"

    manager._process_dt_h = 1.0
    assert manager.check_endpoint(
        melt, flux, CondensationTrain(), record
    ) is False

    manager._process_dt_h = 0.25
    assert manager.check_endpoint(
        _endpoint_melt(CampaignPhase.C0, 0, temperature_C=25.0),
        _endpoint_flux(),
        CondensationTrain(),
        BatchRecord(),
    ) is False
    assert manager.check_endpoint(
        _endpoint_melt(
            CampaignPhase.C7_CA_ALUMINOTHERMIC, 0, temperature_C=25.0
        ),
        _endpoint_flux(),
        CondensationTrain(),
        BatchRecord(),
    ) is False

    # The depletion path clamps duration after the max-hold helper.
    # Replace that helper so this call reaches the third site.
    setpoints = _load_yaml("setpoints.yaml")
    setpoints["campaigns"]["C2A_staged"]["stages"][0][
        "depletion_log_slope_epsilon_per_hr"
    ] = 0.01
    depletion = CampaignManager(setpoints)
    depletion._configured_staged_max_hold_hr = lambda _campaign: 100.0
    staged = _endpoint_melt(
        CampaignPhase.C2A_STAGED, 0, temperature_C=1250.0
    )
    empty = _endpoint_flux()
    train = CondensationTrain()
    depletion._process_dt_h = 1.0
    assert depletion.check_endpoint(
        staged, empty, train, BatchRecord()
    ) is False
    depletion._process_dt_h = 0.25
    with pytest.raises(CampaignSubhourHourClockRefusal) as duration:
        depletion.check_endpoint(staged, empty, train, BatchRecord())
    assert duration.value.diagnostic["clock"] == "c2a_staged_duration_clamp"
