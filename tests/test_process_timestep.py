"""Process timestep: hourly bit-identity, short-step convergence, closure.

The one-hour path is the current green integrator. A shorter step is the
same flux times dt. These tests do not regenerate a runner golden.
"""

from __future__ import annotations

import copy
import math
import textwrap
from pathlib import Path

import pytest

from simulator.accounting.formulas import resolve_species_formula
from simulator.condensation import CondensationModel
from simulator.energy import EnergyTracker
from simulator.evaporation import (
    _rescale_stage3_reactive_alkali,
    _rescale_wall_reaction_booking,
)
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
from simulator.accounting.queries import AccountingQueries
from simulator.accounting.run_artifact import (
    CONFIDENCE_MAX_MASS_BALANCE_RESIDUAL_PCT,
)
from simulator.campaigns import (
    CampaignManager,
    CampaignSubhourHourClockRefusal,
)
from simulator.thermal_train import ThermalTrainSubhourRefusal
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
                # Printed face. A null area must not borrow size_crucible.
                exposed_melt_area_m2: 0.000123
                exposed_melt_area_source_ref: process_timestep_test_fixture
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
    dropping ``/ process_dt_hr()`` on the cleaned-melt kg/h rate,
    commits ``V * dt`` instead of the diagnostic step moles.
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
    captured: dict[str, float] = {}
    diagnose = sim._native_fe_partition_diagnostic

    def _capture(native_fe_mol: float) -> dict:
        diag = diagnose(native_fe_mol)
        captured.clear()
        captured.update(diag)
        return diag

    sim._native_fe_partition_diagnostic = _capture
    sim._apply_native_fe_saturation_split()
    vapor_before_route = float(captured["native_fe_vapor_mol"])
    capacity = float(captured["native_fe_vapor_capacity_mol_hr"])
    partition = sim._last_native_fe_partition_diagnostic
    committed = float(partition["native_fe_vapor_mol"])
    stored = float(sim._native_fe_vapor_residual_capacity_mol_this_hr)
    true_stored = max(0.0, capacity - vapor_before_route / dt_h)
    defect_stored = max(0.0, capacity - vapor_before_route)
    assert capacity > 0.0
    assert vapor_before_route > 0.0
    assert committed == pytest.approx(vapor_before_route, rel=1e-9)
    assert committed != pytest.approx(vapor_before_route * dt_h, rel=1e-6)
    assert true_stored != defect_stored
    assert stored == pytest.approx(true_stored, rel=1e-9)
    assert partition["ordinary_melt_fe_residual_capacity_mol_hr"] == pytest.approx(
        stored, rel=1e-9,
    )
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


def _c4_feed_ledger_above_the_limit():
    """Feed Na the C4 endpoint can read, with residual Na above the limit.

    A missing ledger raises ``c4_feed_na_ledger_missing`` before either
    hour-clock. Residual 1 mol against 1000 mol of feed stays above the
    0.0005 feed fraction, so the Na residual does not end the phase and
    the clock under test is reached.
    """

    from types import SimpleNamespace

    return SimpleNamespace(
        external_loads=(
            SimpleNamespace(
                account="process.raw_feedstock",
                origin_atom_moles={"feedstock": {"Na": 800.0}},
            ),
            SimpleNamespace(
                account="reservoir.reagent.Na",
                origin_atom_moles={"reagent": {"Na": 200.0}},
            ),
        ),
        atom_moles_by_account=lambda account: (
            {"Na": 1.0, "Mg": 500.0}
            if account == "process.cleaned_melt"
            else {}
        ),
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
        manager.check_endpoint(
            melt, flux, CondensationTrain(), record,
            atom_ledger=_c4_feed_ledger_above_the_limit(),
        )
    assert window.value.diagnostic["clock"] == "c4_snapshot_window"

    manager._process_dt_h = 1.0
    assert manager.check_endpoint(
        melt, flux, CondensationTrain(), record,
        atom_ledger=_c4_feed_ledger_above_the_limit(),
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


def _k_full_condensation_route():
    from types import SimpleNamespace

    return SimpleNamespace(
        remaining_by_species={"K": 0.0},
        retained_in_source_by_species={},
        wall_deposit_fraction_by_species={"K": 0.0},
        wall_deposit_account_fractions_by_species={"K": {}},
        condensation_authority_by_species={},
        condensed_for_species=lambda _species: 0.0,
        condensed_by_stage_species={},
        impurity_by_stage_species={},
        wall_reaction_booking_by_species={},
        stage3_reactive_alkali_by_species={},
    )


def test_short_step_condensation_debit_matches_the_overhead_credit():
    """Condensed kilograms are the step mass, not the hourly rate.

    Efficiency 1 at ``dt_h = 0.25`` credits ``rate * dt`` and must
    debit the same mass. The pre-fix debit is the hourly rate, which
    is larger than the credit and overdraws overhead. At one hour the
    two masses are the same number.

    Mutation: drop ``* process_dt_hr()`` on ``condensed_kg``. The
    short step then debits ``rate_kg_hr`` instead of ``rate_kg_hr * dt``.
    """

    rate_kg_hr = 0.4
    dt_h = 0.25
    step_kg = rate_kg_hr * dt_h

    short = _loaded_sim()
    short.process_timestep = resolve_process_timestep(900.0)
    short._route_evaporated_species_to_condensation(
        _k_full_condensation_route(),
        "K",
        rate_kg_hr,
        apply_evaporative_redox_source_terms=False,
    )
    train = short.atom_ledger.kg_by_account(
        "process.condensation_train"
    ).get("K", 0.0)
    overhead = short.atom_ledger.kg_by_account(
        "process.overhead_gas"
    ).get("K", 0.0)
    assert train == pytest.approx(step_kg, rel=1e-9)
    assert overhead == pytest.approx(0.0, abs=1e-9)
    assert train != pytest.approx(rate_kg_hr, rel=1e-6)

    hourly = _loaded_sim()
    hourly.process_timestep = resolve_process_timestep(3600.0)
    hourly._route_evaporated_species_to_condensation(
        _k_full_condensation_route(),
        "K",
        rate_kg_hr,
        apply_evaporative_redox_source_terms=False,
    )
    hourly_train = hourly.atom_ledger.kg_by_account(
        "process.condensation_train"
    ).get("K", 0.0)
    assert hourly_train == pytest.approx(rate_kg_hr, rel=1e-9)


def test_metal_phase_native_fe_condensation_debits_the_step_mass():
    """The metal-phase tap credits step moles. The debit matches them.

    ``native_fe_vapor_mol`` on this branch is moles this step, and the
    metallic tap credits that count. Multiplying the debit by ``dt``
    would leave ``1 - dt`` of the vapor in overhead. This is not the
    evaporation caller, whose rate is kg/h.
    """

    from types import SimpleNamespace

    from simulator.accounting.formulas import resolve_species_formula

    sim = _loaded_sim()
    sim.process_timestep = resolve_process_timestep(900.0)
    step_mol = 0.2
    formula = resolve_species_formula("Fe", sim.species_formula_registry)
    molar_mass = formula.molar_mass_kg_per_mol()
    step_kg = step_mol * molar_mass
    sim.atom_ledger.load_external(
        "process.overhead_gas",
        {"Fe": step_kg},
        material_origin="feedstock",
    )

    def _route(_flux, _melt, *, dt_h):
        # Step kilograms are labeled kg/h. The clip stays on the hour.
        assert dt_h == 1.0
        return SimpleNamespace(
            remaining_by_species={"Fe": 0.0},
            wall_deposit_fraction_by_species={"Fe": 0.0},
            wall_deposit_account_fractions_by_species={"Fe": {}},
            condensation_authority_by_species={},
            condensed_for_species=lambda _species: 0.0,
            condensed_by_stage_species={},
            impurity_by_stage_species={},
            wall_reaction_booking_by_species={},
            stage3_reactive_alkali_by_species={},
        )

    sim.condensation_model.route = _route
    result = sim._route_native_fe_vapor_to_condensation(
        step_mol,
        source_account="process.metal_phase",
    )
    overhead = sim.atom_ledger.kg_by_account(
        "process.overhead_gas"
    ).get("Fe", 0.0)
    train = sim.atom_ledger.kg_by_account(
        "process.condensation_train"
    ).get("Fe", 0.0)
    assert result["native_fe_vapor_mol"] == pytest.approx(step_mol)
    assert overhead == pytest.approx(0.0, abs=1e-9)
    assert train == pytest.approx(step_kg, rel=1e-9)
    assert train != pytest.approx(step_kg * sim.process_dt_hr(), rel=1e-6)


def _partial_species_route(species: str, fraction: float):
    from types import SimpleNamespace

    def _route(flux, _melt, *, dt_h):
        _route.dt_h.append(dt_h)
        rate_kg_hr = float(flux.species_kg_hr[species])
        return SimpleNamespace(
            remaining_by_species={species: fraction * rate_kg_hr},
            retained_in_source_by_species={},
            wall_deposit_fraction_by_species={species: 0.0},
            wall_deposit_account_fractions_by_species={species: {}},
            condensation_authority_by_species={},
            condensed_for_species=lambda _species: 0.0,
            condensed_by_stage_species={},
            impurity_by_stage_species={},
            wall_route_species_order=(species,),
            wall_reaction_booking_by_species={},
            stage3_reactive_alkali_by_species={},
        )

    _route.dt_h = []
    return _route


def test_cleaned_melt_uncondensed_moles_match_the_overhead_left():
    """Uncondensed native Fe is the step moles the debit leaves behind.

    The route remainder is a kg/h rate. The overhead credit is that
    rate times dt, and the debit is ``(rate - remaining) * dt``, so
    the kilograms left are ``remaining_kg_hr * dt``. The moles are
    that mass over ``M_Fe``. Storing ``remaining_kg_hr / M_Fe`` makes
    the uncondensed fraction of the committed vapor ``f / dt``. At
    ``dt_h = 0.25`` and ``f = 0.5`` that fraction is 2. At one hour
    the two formulas are the same number. The metal-phase return is
    the ledger kilogram delta and is not multiplied by dt.

    Mutation: drop ``* process_dt_hr()`` on the cleaned-melt
    ``native_fe_uncondensed_mol``. The short moles become
    ``remaining_kg_hr / M_Fe``.
    """

    from simulator.accounting.formulas import resolve_species_formula

    fraction = 0.5
    vapor_mol = 1.0
    dt_h = 0.25
    sim = _loaded_sim()
    sim.process_timestep = resolve_process_timestep(900.0)
    formula = resolve_species_formula("Fe", sim.species_formula_registry)
    molar_mass = formula.molar_mass_kg_per_mol()
    rate_kg_hr = vapor_mol * molar_mass / dt_h
    remaining_kg_hr = fraction * rate_kg_hr
    cleaned_route = _partial_species_route("Fe", fraction)
    sim.condensation_model.route = cleaned_route
    result = sim._route_native_fe_vapor_to_condensation(
        vapor_mol,
        source_account="process.cleaned_melt",
    )
    assert cleaned_route.dt_h == [pytest.approx(dt_h)]
    committed_mol = float(result["native_fe_vapor_mol"])
    uncondensed_mol = float(result["native_fe_uncondensed_mol"])
    overhead_kg = sim.atom_ledger.kg_by_account(
        "process.overhead_gas"
    ).get("Fe", 0.0)
    assert committed_mol == pytest.approx(vapor_mol, rel=1e-9)
    assert overhead_kg == pytest.approx(remaining_kg_hr * dt_h, rel=1e-9)
    assert uncondensed_mol == pytest.approx(
        remaining_kg_hr * dt_h / molar_mass, rel=1e-9,
    )
    assert uncondensed_mol / committed_mol == pytest.approx(fraction)
    assert uncondensed_mol != pytest.approx(
        remaining_kg_hr / molar_mass, rel=1e-6,
    )
    assert result["native_fe_uncondensed_kg"] == pytest.approx(
        remaining_kg_hr, rel=1e-9,
    )

    partitioned = _loaded_sim()
    partitioned.process_timestep = resolve_process_timestep(900.0)
    partitioned.melt.temperature_C = 1600.0
    partitioned.melt.oxygen_reservoir.melt_intrinsic_fO2_log = -10.0
    partitioned._sync_oxygen_reservoir_mirror()
    partitioned.melt.melt_surface_area_m2 = 1.0e6
    partition_route = _partial_species_route("Fe", fraction)
    partitioned.condensation_model.route = partition_route
    partitioned._apply_native_fe_saturation_split()
    assert partition_route.dt_h == [pytest.approx(dt_h)]
    partition = partitioned._last_native_fe_partition_diagnostic
    partition_vapor = float(partition["native_fe_vapor_mol"])
    partition_uncondensed = float(partition["native_fe_uncondensed_mol"])
    assert partition_vapor > 0.0
    assert partition_uncondensed / partition_vapor == pytest.approx(fraction)
    assert partition["native_fe_uncondensed_fraction_of_pool"] == pytest.approx(
        partition_uncondensed
        / float(partition["native_fe_pool_mol"]),
    )
    assert partition["native_fe_uncondensed_fraction_of_pool"] != pytest.approx(
        fraction / dt_h, rel=1e-6,
    )

    hourly = _loaded_sim()
    hourly.process_timestep = resolve_process_timestep(3600.0)
    hourly_route = _partial_species_route("Fe", fraction)
    hourly.condensation_model.route = hourly_route
    hourly_result = hourly._route_native_fe_vapor_to_condensation(
        vapor_mol,
        source_account="process.cleaned_melt",
    )
    assert hourly_route.dt_h == [pytest.approx(1.0)]
    assert float(hourly_result["native_fe_uncondensed_mol"]) == pytest.approx(
        fraction * vapor_mol, rel=1e-9,
    )

    metal = _loaded_sim()
    metal.process_timestep = resolve_process_timestep(900.0)
    step_kg = vapor_mol * molar_mass
    metal.atom_ledger.load_external(
        "process.overhead_gas",
        {"Fe": step_kg},
        material_origin="feedstock",
    )
    metal_route = _partial_species_route("Fe", fraction)
    metal.condensation_model.route = metal_route
    metal_result = metal._route_native_fe_vapor_to_condensation(
        vapor_mol,
        source_account="process.metal_phase",
    )
    assert metal_route.dt_h == [pytest.approx(1.0)]
    assert float(metal_result["native_fe_uncondensed_mol"]) == pytest.approx(
        fraction * vapor_mol, rel=1e-9,
    )
    assert float(metal_result["native_fe_uncondensed_mol"]) != pytest.approx(
        fraction * vapor_mol * metal.process_dt_hr(), rel=1e-6,
    )


def test_short_step_residual_flux_is_kilograms_per_hour():
    """The free-vapor flux is a kg/h rate, not this step's leftover mass.

    ``OverheadModel.estimate_transport_state`` reads ``total_kg_hr`` as
    kg/h on the non-controlled path. The overhead ledger delta is
    ``remaining_kg_hr * dt``. Dividing that delta by the step recovers
    the kg/h remainder. Leaving the delta in ``species_kg_hr`` reports
    a short step as ``f * rate * dt``. At one hour the divisor is 1.0
    and the residual equals the ledger delta.

    Mutation: drop ``/ process_dt_hr()`` on the residual assignment.
    The short residual then equals the ledger kilograms.
    """

    rate_kg_hr = 0.4
    fraction = 0.5
    dt_h = 0.25
    remaining_kg_hr = rate_kg_hr * fraction

    short = _loaded_sim()
    short.process_timestep = resolve_process_timestep(900.0)
    short.condensation_model._knudsen_policy_configured = True
    short_route = _partial_species_route("K", fraction)
    short.condensation_model.route = short_route
    before_kg = short.atom_ledger.kg_by_account(
        "process.overhead_gas"
    ).get("K", 0.0)
    residual = short._route_to_condensation(_flux({"K": rate_kg_hr}))
    after_kg = short.atom_ledger.kg_by_account(
        "process.overhead_gas"
    ).get("K", 0.0)
    ledger_kg = after_kg - before_kg
    assert ledger_kg == pytest.approx(remaining_kg_hr * dt_h, rel=1e-9)
    assert residual.species_kg_hr["K"] == pytest.approx(
        ledger_kg / dt_h, rel=1e-9,
    )
    assert residual.total_kg_hr == pytest.approx(ledger_kg / dt_h, rel=1e-9)
    assert short_route.dt_h == [pytest.approx(dt_h)]
    assert residual.species_kg_hr["K"] == pytest.approx(
        remaining_kg_hr, rel=1e-9,
    )
    assert residual.species_kg_hr["K"] != pytest.approx(ledger_kg, rel=1e-6)

    hourly = _loaded_sim()
    hourly.process_timestep = resolve_process_timestep(3600.0)
    hourly.condensation_model._knudsen_policy_configured = True
    hourly_route = _partial_species_route("K", fraction)
    hourly.condensation_model.route = hourly_route
    hourly_before = hourly.atom_ledger.kg_by_account(
        "process.overhead_gas"
    ).get("K", 0.0)
    hourly_residual = hourly._route_to_condensation(_flux({"K": rate_kg_hr}))
    hourly_after = hourly.atom_ledger.kg_by_account(
        "process.overhead_gas"
    ).get("K", 0.0)
    hourly_ledger_kg = hourly_after - hourly_before
    assert hourly_ledger_kg == pytest.approx(remaining_kg_hr, rel=1e-9)
    assert hourly_route.dt_h == [pytest.approx(1.0)]
    assert hourly_residual.species_kg_hr["K"] == pytest.approx(
        hourly_ledger_kg, rel=1e-9,
    )


def test_subhour_c4_acquisition_count_is_refused():
    """Below-window C4 must not treat snapshots as hours.

    One snapshot at 1150 C is still below the 1580 C window. The
    hourly call counts that snapshot and stays under the 60 h limit.
    A 0.25 h step raises ``c4_acquisition_count`` before the count is
    compared with the limit. Empty history is not this clock.

    Mutation: delete the ``c4_acquisition_count`` refusal. The short
    call then returns ``False`` or raises ``CampaignC4EndpointRefusal``.
    """

    manager = CampaignManager(_load_yaml("setpoints.yaml"))
    record = BatchRecord(snapshots=[
        HourSnapshot(
            campaign=CampaignPhase.C4,
            temperature_C=1150.0,
            evap_flux=_endpoint_flux(Mg=0.0),
        )
    ])
    melt = _endpoint_melt(CampaignPhase.C4, 1, temperature_C=1150.0)
    flux = _endpoint_flux(Mg=0.0)
    train = CondensationTrain()
    ledger = _c4_feed_ledger_above_the_limit()
    manager._process_dt_h = 1.0
    assert manager.check_endpoint(
        melt, flux, train, record, atom_ledger=ledger,
    ) is False
    manager._process_dt_h = 0.25
    with pytest.raises(CampaignSubhourHourClockRefusal) as acquired:
        manager.check_endpoint(
            melt, flux, train, record, atom_ledger=ledger,
        )
    assert acquired.value.reason == "subhour_hour_clock_refused"
    assert acquired.value.terminal_refusal is True
    assert acquired.value.diagnostic["clock"] == "c4_acquisition_count"
    assert acquired.value.diagnostic["process_dt_h"] == 0.25


def test_subhour_thermal_train_refuses_snapshot_hours():
    """A short step must not price one snapshot as one hour.

    The hourly report still sets ``run_hours`` from the snapshot count
    on the basis ``one_hour_per_snapshot``. A 900 s step raises
    ``ThermalTrainSubhourRefusal`` before that cost is built.

    Mutation: delete the ``process_dt_h != 1.0`` refusal. The short
    call then returns ``run_hours == 1``.
    """

    record = BatchRecord(snapshots=[
        HourSnapshot(
            hour=1,
            temperature_C=1500.0,
            evap_flux=EvaporationFlux(
                species_kg_hr={"Na": 1.0},
                total_kg_hr=1.0,
            ),
            melt_offgas_O2_mol_hr=1.0,
            O2_vented_kg_hr=0.0,
        )
    ])
    short = _loaded_sim()
    short.record = record
    short.process_timestep = resolve_process_timestep(900.0)
    with pytest.raises(ThermalTrainSubhourRefusal) as refused:
        AccountingQueries(short).thermal_train_report()
    assert refused.value.reason == "subhour_hour_clock_refused"
    assert refused.value.process_dt_h == pytest.approx(0.25)

    hourly = _loaded_sim()
    hourly.record = record
    hourly.process_timestep = resolve_process_timestep(3600.0)
    report = AccountingQueries(hourly).thermal_train_report()
    assert report["snapshot_count"] == 1
    assert report["run_hours"] == 1
    assert report["run_hours_basis"] == "one_hour_per_snapshot"


# SiO oxidative design table: 1/2 mol O2 per mol vapour (section 3.4).
_SIO_O2_PER_VAPOUR = 0.5
_SIO_VAPOUR_MOL_PER_H = 60.0
_OXYGEN_INVENTORY_MOL = 1.0
_SHORT_DT_H = 1.0 / 60.0
# Admitted Stage-3 parcel in test_stage3_na_gate_matches_liquid_activity_reference.
# That Na/SiO mass ratio is favoured at 1e-8 bar. K is omitted so the
# oxygen demand is the Na rate alone.
_ADMITTED_NA_PER_SIO_KG = 0.0007


def _kg_per_h(model: CondensationModel, species: str, mol_per_h: float) -> float:
    molar_mass = resolve_species_formula(
        species, model.species_formula_registry,
    ).molar_mass_kg_per_mol()
    return mol_per_h * molar_mass


def _oxidative_step_mol(route, dt_h: float, surface: str) -> tuple[float, float]:
    """Step moles of the named surface's oxidative leg.

    The hour path reads the route booking. A shorter step uses the
    production rescaler, which is the one multiply by dt_h.
    """

    booking = copy.deepcopy(route.wall_reaction_booking_by_species["SiO"])
    if dt_h != 1.0:
        _rescale_wall_reaction_booking(booking, dt_h)
    legs = [
        leg for leg in booking["reaction_legs"]
        if leg["reaction_id"] == "oxidative" and leg["surface"] == surface
    ]
    vapour = sum(float(leg["vapour_mol"]) for leg in legs)
    oxygen = sum(float(leg["oxygen_debit_mol"]) for leg in legs)
    return vapour, oxygen


def _sio_oxygen_model(oxygen_mol: float) -> CondensationModel:
    model = CondensationModel(CondensationTrain.create_default())
    model.train.collection_route["stage3_route"] = "divert"
    model.retained_vapor_mol["O2"] = oxygen_mol
    return model


def _isolate_capture_surface(monkeypatch, model: CondensationModel, surface: str) -> None:
    """Force the named surface to see the whole supply.

    The other surface contributes no deposit. This is transport
    control: the production oxygen clip still assigns the capture.
    """

    if surface == "wall":
        def candidates(**kwargs):
            return {
                str(name): float(kg)
                for name, kg in kwargs["supply_by_segment_kg"].items()
            }

        def efficiency(**kwargs):
            return 0.0
    elif surface == "baffle":
        def candidates(**kwargs):
            return {}

        def efficiency(**kwargs):
            return 1.0
    else:
        raise AssertionError(surface)
    monkeypatch.setattr(model, "_wall_deposit_candidates_by_segment_kg", candidates)
    monkeypatch.setattr(model, "_condensation_efficiency", efficiency)


class TestShortStepOxygenClip:
    """Inventory mol and the mol/h vapour extent meet in one unit.

    Review extents (ror-dtr2x1121), with the SiO design-table ratio
    a = 1/2. 60 mol/h and 1 mol O2 at dt_h = 1/60 commits 1 mol of
    vapour: min(60 * 1/60, 1 / (1/2)) = 1, and that vapour takes
    0.5 mol O2. The same rates at dt_h = 1 commit the unscaled clip
    min(60, 1 / (1/2)) = 2 mol vapour and 1 mol O2. Unbound oxygen
    commits 60 mol/h * dt_h. Stage-3 Na at 240 mol/h commits 4 mol of
    metal and 1 mol of O2 on the short step and on the hour.

    Mutation: leave the clip comparing inventory mol with mol/h, then
    multiply by dt. The short SiO case books 1/30 mol of vapour, not
    1 mol. The short alkali case books 1/60 mol of O2, not 1 mol.

    The SiO flux goes through ``_flux``, so ``total_kg_hr`` is the
    species rate. A zero total makes the wall-partial fixture project
    nothing, and the route then refuses the missing SiO partial.
    """

    @pytest.mark.usefixtures("production_configured_condensation_route")
    @pytest.mark.parametrize("surface", ["wall", "baffle"])
    def test_short_step_limited_capture_commits_one_mol(self, monkeypatch, surface):
        model = _sio_oxygen_model(_OXYGEN_INVENTORY_MOL)
        _isolate_capture_surface(monkeypatch, model, surface)
        route = model.route(
            _flux({"SiO": _kg_per_h(model, "SiO", _SIO_VAPOUR_MOL_PER_H)}),
            MeltState(temperature_C=1600.0),
            dt_h=_SHORT_DT_H,
        )
        vapour, oxygen = _oxidative_step_mol(route, _SHORT_DT_H, surface)
        assert vapour == pytest.approx(1.0)
        assert oxygen == pytest.approx(_SIO_O2_PER_VAPOUR)

    @pytest.mark.usefixtures("production_configured_condensation_route")
    @pytest.mark.parametrize("surface", ["wall", "baffle"])
    def test_unbound_oxygen_commits_the_hourly_extent_times_dt(
        self, monkeypatch, surface,
    ):
        model = _sio_oxygen_model(1000.0)
        _isolate_capture_surface(monkeypatch, model, surface)
        route = model.route(
            _flux({"SiO": _kg_per_h(model, "SiO", _SIO_VAPOUR_MOL_PER_H)}),
            MeltState(temperature_C=1600.0),
            dt_h=_SHORT_DT_H,
        )
        vapour, oxygen = _oxidative_step_mol(route, _SHORT_DT_H, surface)
        assert vapour == pytest.approx(_SIO_VAPOUR_MOL_PER_H * _SHORT_DT_H)
        assert oxygen == pytest.approx(vapour * _SIO_O2_PER_VAPOUR)

    @pytest.mark.usefixtures("production_configured_condensation_route")
    @pytest.mark.parametrize("surface", ["wall", "baffle"])
    def test_hour_commits_the_unscaled_clip(self, monkeypatch, surface):
        model = _sio_oxygen_model(_OXYGEN_INVENTORY_MOL)
        _isolate_capture_surface(monkeypatch, model, surface)
        route = model.route(
            _flux({"SiO": _kg_per_h(model, "SiO", _SIO_VAPOUR_MOL_PER_H)}),
            MeltState(temperature_C=1600.0),
            dt_h=1.0,
        )
        vapour, oxygen = _oxidative_step_mol(route, 1.0, surface)
        assert vapour == pytest.approx(2.0)
        assert oxygen == pytest.approx(1.0)

    def test_short_step_alkali_commits_four_mol_metal_and_one_mol_oxygen(
        self, monkeypatch,
    ):
        uptake, oxygen = _alkali_step(monkeypatch, dt_h=_SHORT_DT_H)
        assert uptake == pytest.approx(4.0)
        assert oxygen == pytest.approx(1.0)

    def test_hour_alkali_commits_the_unscaled_clip(self, monkeypatch):
        uptake, oxygen = _alkali_step(monkeypatch, dt_h=1.0)
        assert uptake == pytest.approx(4.0)
        assert oxygen == pytest.approx(1.0)

    def test_non_positive_dt_is_refused(self):
        model = CondensationModel(CondensationTrain.create_default())
        flux = EvaporationFlux()
        melt = MeltState()
        for refused in (True, False, 0, 0.0, -1.0, float("nan"), float("inf"), "1"):
            with pytest.raises(ValueError, match="dt_h"):
                model.route(flux, melt, dt_h=refused)


def test_c0_forecast_adjustment_uses_the_float_campaign_hour(monkeypatch):
    """A short C0 step budgets the fractional hour it has already run.

    Exact zero still refreshes the full-budget exit. Hour 2 keeps the
    same remaining budget as the integer clock. The notice hour stays
    the truncated label the recorder stores.

    Mutation: int(campaign_hour) before the zero check. A step at
    1/60 h refreshes the full exit instead of budgeting max_hold - 1/60.
    """

    sim = _loaded_sim()
    sim.melt.campaign = CampaignPhase.C0
    recorded: list[float | None] = []
    refreshes = {"n": 0}
    max_hold = sim.campaign_mgr._max_hold_hr(CampaignPhase.C0)

    def predict(**kwargs):
        recorded.append(kwargs.get("remaining_hold_budget_hr"))
        return {"prediction_status": "ok", "T_exit_C": 1.0}

    def refresh():
        refreshes["n"] += 1
        sim.campaign_mgr.c0_exit_prediction = {"prediction_status": "ok"}

    monkeypatch.setattr(sim, "_predict_c0_exit", predict)
    monkeypatch.setattr(sim, "_refresh_c0_exit_prediction", refresh)

    sim.melt.campaign_hour = 0
    sim._handle_c0_forecast_adjustment("temperature_C")
    assert refreshes["n"] == 1
    assert recorded == []

    sim.melt.campaign_hour = 0.0
    sim._handle_c0_forecast_adjustment("temperature_C")
    assert refreshes["n"] == 2
    assert recorded == []

    sim.melt.campaign_hour = _SHORT_DT_H
    sim._handle_c0_forecast_adjustment("temperature_C")
    assert refreshes["n"] == 2
    assert recorded[-1] == pytest.approx(max_hold - _SHORT_DT_H)
    assert sim.campaign_mgr.c0_forecast_adjustment_notices[-1]["campaign_hour"] == 0

    sim.melt.campaign_hour = 2
    sim._handle_c0_forecast_adjustment("temperature_C")
    assert recorded[-1] == pytest.approx(max_hold - 2.0)
    assert sim.campaign_mgr.c0_forecast_adjustment_notices[-1]["campaign_hour"] == 2


def _alkali_step(monkeypatch, *, dt_h: float) -> tuple[float, float]:
    """Production Stage-3 Na uptake after one rescale by dt_h.

    Partials are the favoured 1e-8 bar stream. The Na/SiO mass ratio
    matches that admitted parcel, so the activity sign stays favoured.
    Walls do not spend the oxygen inventory.
    """

    model = CondensationModel(CondensationTrain.create_default())
    model.hard_stage3_gate = False
    model.train.collection_route["stage3_route"] = "through"
    model.pipe_segments = []
    model.retained_vapor_mol = {"O2": _OXYGEN_INVENTORY_MOL}
    po2_mbar = 1.0e-5
    model.configure_operating_conditions(
        overhead_pressure_mbar=1.0,
        species_partial_pressures_mbar={
            "Na": 0.3,
            "O2": po2_mbar,
            "SiO": 0.7 - po2_mbar,
        },
        stage_area_m2_by_stage={str(index): 1.0 for index in range(8)},
    )
    na_kg_hr = _kg_per_h(model, "Na", 240.0)
    sio_kg_hr = na_kg_hr / _ADMITTED_NA_PER_SIO_KG

    def efficiency(**kwargs):
        stage = kwargs["stage"]
        species = kwargs["species"]
        if int(stage.stage_number) == 3 and species in {"Na", "SiO"}:
            return 1.0
        return 0.0

    monkeypatch.setattr(model, "_condensation_efficiency", efficiency)
    monkeypatch.setattr(
        "simulator.condensation._species_declares_wall_reactions",
        lambda *args, **kwargs: False,
    )
    route = model.route(
        EvaporationFlux(species_kg_hr={"SiO": sio_kg_hr, "Na": na_kg_hr}),
        MeltState(temperature_C=1650.0),
        dt_h=dt_h,
    )
    reaction = dict(route.stage3_reactive_alkali_by_species["Na"])
    if dt_h != 1.0:
        _rescale_stage3_reactive_alkali(reaction, dt_h)
    assert reaction["available_oxygen_mol"] == pytest.approx(_OXYGEN_INVENTORY_MOL)
    return float(reaction["uptake_mol"]), float(reaction["oxygen_debit_mol"])
