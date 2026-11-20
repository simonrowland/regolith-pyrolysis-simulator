"""Size d-452 collector areas from production C6 capture calls on a Studio."""

from __future__ import annotations

import copy
import json
import math
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path.cwd()))

import simulator.condensation as condensation_module  # noqa: E402
from simulator.condensation import (  # noqa: E402
    STAGE_AREA_KEY_BY_STAGE_KEY,
    STAGE_AREA_KEY_BY_STAGE_NUMBER,
    WallSaturationPressureRefusal,
    antoine_dew_temperature_diagnostic,
    _try_antoine_psat_pa,
    stage_purity_report,
)
from simulator.cost_parameters import cost_parameters_from_mapping  # noqa: E402
from simulator.optimize.objective import (  # noqa: E402
    _collector_stage_hardware_cost_summary,
)
from simulator.state import CampaignPhase, DecisionType  # noqa: E402
from tests.chemistry.conftest import _build_sim, _load_yaml  # noqa: E402
from tests.chemistry.test_builtin_metallothermic_step_provider import (  # noqa: E402
    _hkl_only_setpoints,
)


DESIGN_STAGE_KEYS = (
    "fe_stage1",
    "cr_stage2",
    "sio_stage3",
    "mn_stage3b",
    "alkali_stage4",
    "k_stage4b",
)
CAPTURE_TARGET = 0.90
CEILING_TARGET_FRACTION = 0.95
CEILING_REFERENCE_AREA_M2 = 1.0e300
AREA_RELATIVE_TOLERANCE = 0.01
MAX_FIXED_POINT_PASSES = 8
DIAGNOSTIC_SPECIES = ("Mn", "SiO", "Si")
TIME_OVERLAP_SPECIES = ("Mn", "SiO", "Si", "Fe")
MODEL_STATE_FIELDS = (
    "wall_species_partial_pressures_pa",
    "vapor_pressure_data",
    "lab_geometry",
    "gas_temperature_C",
    "overhead_pressure_mbar",
    "stir_factor",
    "radial_stir_factor",
    "regime_factor",
    "carrier_gas",
    "stage_hydraulic_diameter_m_by_stage",
    "pipe_diameter_m",
)
MODEL_STATE_MAPPING_FIELDS = frozenset({
    "wall_species_partial_pressures_pa",
    "stage_hydraulic_diameter_m_by_stage",
})


def _snapshot_model_state(model) -> dict[str, object]:
    """Copy mutable scalar maps without copying immutable config proxies."""

    return {
        name: (dict(value) if name in MODEL_STATE_MAPPING_FIELDS else value)
        for name in MODEL_STATE_FIELDS
        for value in (getattr(model, name),)
    }


def _stage_area_key(stage: object) -> str | None:
    stage_key = str(getattr(stage, "stage_key", "") or "")
    if stage_key in STAGE_AREA_KEY_BY_STAGE_KEY:
        return STAGE_AREA_KEY_BY_STAGE_KEY[stage_key]
    try:
        return STAGE_AREA_KEY_BY_STAGE_NUMBER.get(
            int(getattr(stage, "stage_number"))
        )
    except (TypeError, ValueError):
        return None


def _capture_replay_args(kwargs: dict) -> dict:
    replay = dict(kwargs)
    replay["antoine_extrapolations"] = {}
    replay["antoine_extrapolation_warnings"] = []
    replay["efficiency_outcomes"] = []
    return replay


def _area_for_eta(efficiency, *, starting_area_m2: float, target_eta: float):
    """Find a finite area that reaches a target efficiency, if one exists."""

    high = max(float(starting_area_m2), 1.0e-12)
    high_eta = float(efficiency(high))
    while math.isfinite(high_eta) and high_eta < target_eta:
        next_high = high * 2.0
        if not math.isfinite(next_high):
            return float(starting_area_m2), float(efficiency(starting_area_m2)), False
        high = next_high
        high_eta = float(efficiency(high))
    if not math.isfinite(high_eta) or high_eta < target_eta:
        return float(starting_area_m2), high_eta, False
    low = 0.0
    for _ in range(60):
        mid = (low + high) / 2.0
        if float(efficiency(mid)) >= target_eta:
            high = mid
        else:
            low = mid
    return high, float(efficiency(high)), True


def _size_peak_to_capture_target(model, original, peak) -> dict[str, float | str]:
    """Replay the production resolver and size below an unattainable target."""

    saved_state = _snapshot_model_state(model)
    saved_areas = dict(model.stage_area_m2_by_stage)

    def efficiency(area_m2: float) -> float:
        model.stage_area_m2_by_stage[peak["stage_key"]] = area_m2
        return float(original(**_capture_replay_args(peak["kwargs"])))

    try:
        for name, value in peak["model_state"].items():
            setattr(model, name, value)
        # This huge area probes the resolver's limiting eta only. It is never
        # returned, installed in config, or sent to costing.
        ceiling_eta = float(efficiency(CEILING_REFERENCE_AREA_M2))
        if not math.isfinite(ceiling_eta):
            return {
                "area_m2": float(peak["current_area_m2"]),
                "eta": math.nan,
                "target_eta": CAPTURE_TARGET,
                "ceiling_eta": math.nan,
                "status": "non_finite_resolver_result",
            }
        ceiling_eta = min(1.0, max(0.0, ceiling_eta))
        ceiling_limited = ceiling_eta < CAPTURE_TARGET
        target_eta = (
            CEILING_TARGET_FRACTION * ceiling_eta
            if ceiling_limited else CAPTURE_TARGET
        )
        if ceiling_eta == 0.0:
            return {
                "area_m2": float(peak["current_area_m2"]),
                "eta": 0.0,
                "target_eta": 0.0,
                "ceiling_eta": ceiling_eta,
                "status": "capture_ceiling_below_target",
            }
        area_m2, eta, reached = _area_for_eta(
            efficiency,
            starting_area_m2=peak["current_area_m2"],
            target_eta=target_eta,
        )
        return {
            "area_m2": area_m2,
            "eta": eta,
            "target_eta": target_eta,
            "ceiling_eta": ceiling_eta,
            "status": (
                "capture_ceiling_below_target"
                if ceiling_limited else "sized"
            ) if reached else "capture_target_unreachable_at_finite_area",
        }
    finally:
        model.stage_area_m2_by_stage = saved_areas
        for name, value in saved_state.items():
            setattr(model, name, value)


def _run_c6(
    *,
    vapor: dict,
    feedstocks: dict,
    base_setpoints: dict,
    stage_areas: dict[str, float],
    label: str,
    include_mn_stage: bool = True,
) -> dict:
    setpoints = copy.deepcopy(base_setpoints)
    setpoints["condenser_geometry"]["stage_areas_m2"].update(stage_areas)
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor,
        feedstocks,
        setpoints,
        additives_kg={"K": 30.0, "Na": 25.0, "Mg": 60.0},
    )
    model = sim.condensation_model
    if not include_mn_stage:
        model.train.stages[:] = [
            stage for stage in model.train.stages
            if getattr(stage, "stage_key", "") != "stage_3b_mn_capture"
        ]
    original = model._condensation_efficiency
    hourly: dict[tuple[str, str, int], dict] = {}
    peaks: dict[tuple[str, str], dict] = {}
    diagnostic_peaks: dict[str, dict] = {}
    time_overlap_kg_hr: dict[tuple[int, str], float] = {}
    stage_profiles: dict[tuple[int, str], dict] = {}
    segment_profiles: dict[tuple[int, str], dict] = {}
    current_stage_partials: dict[str, float] = {}
    stage_location_keys = {
        f"stage_{int(getattr(stage, 'stage_number'))}"
        for stage in model.train.stages
    }
    partial_pressure_builder = (
        condensation_module._segment_species_partial_pressures_pa
    )

    def capture_partial_pressure_maps(*args, **kwargs):
        partial_maps = partial_pressure_builder(*args, **kwargs)
        hour = int(getattr(sim.melt, "hour", 0))
        for location, partials in partial_maps.items():
            if location in stage_location_keys:
                current_stage_partials.clear()
                current_stage_partials.update(partials)
            else:
                segment_profiles[(hour, str(location))] = {
                    "partial_pressures_pa": dict(partials),
                    "pO2_mbar": getattr(sim.melt, "pO2_mbar", None),
                }
        return partial_maps

    def capture(**kwargs):
        eta = original(**kwargs)
        stage = kwargs["stage"]
        area_key = _stage_area_key(stage)
        species = str(kwargs["species"])
        hour = int(getattr(sim.melt, "hour", 0))
        stage_label = (
            str(getattr(stage, "stage_key", "") or "")
            or f"stage_{int(getattr(stage, 'stage_number', -1))}"
        )
        local_partials = dict(current_stage_partials)
        inlet_partial = kwargs.get("inlet_partial_pressure_pa")
        if inlet_partial is None:
            inlet_partial = local_partials.get(species)
        if species in DIAGNOSTIC_SPECIES and inlet_partial is not None:
            try:
                local_partials[species] = float(inlet_partial)
            except (TypeError, ValueError):
                local_partials[species] = None
        pO2_mbar = getattr(sim.melt, "pO2_mbar", None)
        stage_profile = stage_profiles.setdefault(
            (hour, stage_label),
            {"partial_pressures_pa": {}, "pO2_mbar": pO2_mbar},
        )
        stage_profile["partial_pressures_pa"].update(local_partials)
        stage_profile["pO2_mbar"] = pO2_mbar

        if species in TIME_OVERLAP_SPECIES:
            incoming = float(kwargs["available_kg"])
            overlap_key = (hour, species)
            old_incoming = time_overlap_kg_hr.get(overlap_key)
            if old_incoming is None or incoming > old_incoming:
                time_overlap_kg_hr[overlap_key] = incoming

        if species in DIAGNOSTIC_SPECIES:
            incoming = float(kwargs["available_kg"])
            old_diagnostic_peak = diagnostic_peaks.get(species)
            if (
                old_diagnostic_peak is None
                or incoming > old_diagnostic_peak["incoming_kg_hr"]
            ):
                diagnostic_peaks[species] = {
                    "hour": hour,
                    "stage": stage_label,
                    "incoming_kg_hr": incoming,
                    "partial_pressure_pa": local_partials.get(species),
                    "pO2_mbar": pO2_mbar,
                }
        targets = set(getattr(stage, "target_species", ()) or ())
        if area_key not in DESIGN_STAGE_KEYS or species not in targets:
            return eta
        incoming = float(kwargs["available_kg"])
        row_key = area_key, species, hour
        old_row = hourly.get(row_key)
        if old_row is None or incoming > old_row["incoming_kg_hr"]:
            hourly[row_key] = {"incoming_kg_hr": incoming, "eta": float(eta)}
        peak_key = area_key, species
        old_peak = peaks.get(peak_key)
        if old_peak is None or incoming > old_peak["incoming_kg_hr"]:
            peaks[peak_key] = {
                "stage_key": area_key,
                "species": species,
                "hour": hour,
                "incoming_kg_hr": incoming,
                "eta": float(eta),
                "current_area_m2": float(
                    model.stage_area_m2_by_stage[area_key]
                ),
                "kwargs": dict(kwargs),
                "model_state": _snapshot_model_state(model),
            }
        return eta

    model._condensation_efficiency = capture
    condensation_module._segment_species_partial_pressures_pa = (
        capture_partial_pressure_maps
    )
    try:
        sim.start_campaign(CampaignPhase.C0)
        choices = {
            DecisionType.ROOT_BRANCH: "pyrolysis",
            DecisionType.PATH_AB: "A_staged",
            DecisionType.BRANCH_ONE_TWO: "two",
            DecisionType.C6_PROCEED: "yes",
        }
        steps = 0
        while not sim.is_complete() and steps < 5000:
            if sim.paused_for_decision:
                decision = sim.pending_decision
                choice = choices.get(decision.decision_type)
                if choice not in (decision.options or ()):
                    choice = (decision.options or [None])[0]
                sim.apply_decision(decision.decision_type, choice)
                continue
            sim.step()
            steps += 1
    finally:
        condensation_module._segment_species_partial_pressures_pa = (
            partial_pressure_builder
        )

    print(
        f"D452_RUN label={label} complete={sim.is_complete()} steps={steps} "
        f"campaign={sim.melt.campaign} hour={sim.melt.hour}"
    )
    for (area_key, species, hour), row in sorted(hourly.items()):
        print(
            f"D452_HOUR label={label} stage={area_key} species={species} "
            f"hour={hour} incident_kg_hr={row['incoming_kg_hr']:.12g} "
            f"eta={row['eta']:.12g}"
        )
    overlap_hours = sorted({hour for hour, _species in time_overlap_kg_hr})
    for hour in overlap_hours:
        rates = []
        for species in TIME_OVERLAP_SPECIES:
            incoming = time_overlap_kg_hr.get((hour, species))
            value = "not_observed" if incoming is None else f"{incoming:.12g}"
            rates.append(f"{species}_max_stage_inlet_kg_hr={value}")
        print(f"D452_TEMPORAL_OVERLAP label={label} hour={hour} " + " ".join(rates))
    return {
        "sim": sim,
        "model": model,
        "original": original,
        "vapor_pressure_data": vapor,
        "peaks": peaks,
        "diagnostic_peaks": diagnostic_peaks,
        "stage_profiles": stage_profiles,
        "segment_profiles": segment_profiles,
        "areas": dict(stage_areas),
        "include_mn_stage": include_mn_stage,
    }


def _size_pass(run: dict, search_ceiling_m2: float) -> dict[str, float]:
    required_by_stage: dict[str, list[dict]] = {}
    for (area_key, species), peak in sorted(run["peaks"].items()):
        sizing = _size_peak_to_capture_target(
            run["model"], run["original"], peak
        )
        area = float(sizing["area_m2"])
        eta = float(sizing["eta"])
        target_eta = float(sizing["target_eta"])
        ceiling_eta = float(sizing["ceiling_eta"])
        status = str(sizing["status"])
        if not math.isfinite(area) or area <= 0.0:
            raise ValueError(
                f"sizing returned a non-positive or non-finite area for "
                f"{area_key}/{species}: {area!r} ({status})"
            )
        if status in {
            "non_finite_resolver_result",
            "capture_target_unreachable_at_finite_area",
        }:
            raise ValueError(
                f"sizing refused {area_key}/{species}: {status}; "
                f"ceiling_eta={ceiling_eta!r}, target_eta={target_eta!r}"
            )
        required_by_stage.setdefault(area_key, []).append(sizing)
        print(
            f"D452_PEAK stage={area_key} species={species} hour={peak['hour']} "
            f"incident_kg_hr={peak['incoming_kg_hr']:.12g} "
            f"area_at_run_m2={peak['current_area_m2']:.12g} "
            f"eta_at_run_area={peak['eta']:.12g} "
            f"area_for_target_m2={area:.12g} eta_at_area={eta:.12g} "
            f"target_eta={target_eta:.12g} ceiling_eta={ceiling_eta:.12g} "
            f"status={status} "
            f"exceeds_optimizer_bound={area > search_ceiling_m2}"
        )
    result = dict(run["areas"])
    for stage_key in DESIGN_STAGE_KEYS:
        sizing_rows = required_by_stage.get(stage_key, [])
        if sizing_rows:
            areas = [float(row["area_m2"]) for row in sizing_rows]
            statuses = sorted({str(row["status"]) for row in sizing_rows})
            result[stage_key] = max(areas)
        print(
            f"D452_STAGE_AREA stage={stage_key} "
            + (
                f"area_for_all_designated_species_m2={result[stage_key]:.12g} "
                f"species_statuses={','.join(statuses)}"
                if sizing_rows else "status=no_designated_flux"
            )
        )
    return result


def _production_psat(
    species: str,
    temperature_C: float,
    vapor_pressure_data: dict,
    cache: dict,
) -> tuple[float | None, bool, bool, str | None]:
    key = (species, round(float(temperature_C), 9))
    if key not in cache:
        notes: dict = {}
        try:
            pressure, refused = _try_antoine_psat_pa(
                species,
                float(temperature_C) + 273.15,
                vapor_pressure_data=vapor_pressure_data,
                antoine_extrapolations=notes,
                antoine_extrapolation_warnings=[],
                enforce_hot_train_applicability=False,
            )
        except WallSaturationPressureRefusal as exc:
            cache[key] = (
                None,
                True,
                False,
                f"{type(exc).__name__}: {exc}",
            )
            return cache[key]
        cache[key] = (
            None if pressure is None else float(pressure),
            bool(refused),
            any(
                str(record.get("authority_level", "")) == "extrapolated"
                or str(record.get("status", "")) == "extrapolated"
                for record in notes.values()
                if isinstance(record, dict)
            ),
            next(
                (
                    str(record.get("reason"))
                    for record in notes.values()
                    if isinstance(record, dict) and record.get("reason")
                ),
                None,
            ),
        )
    return cache[key]


def _dewpoint_in_wall_band(
    species: str,
    partial_pressure_pa: float | None,
    low_C: float,
    high_C: float,
    vapor_pressure_data: dict,
    psat_cache: dict,
) -> dict:
    if partial_pressure_pa is None:
        return {"status": "partial_pressure_missing", "temperature_C": None}
    try:
        pressure = float(partial_pressure_pa)
    except (TypeError, ValueError):
        return {"status": "invalid_partial_pressure", "temperature_C": None}
    if not math.isfinite(pressure):
        return {"status": "invalid_partial_pressure", "temperature_C": None}
    if pressure < 0.0:
        return {"status": "invalid_partial_pressure", "temperature_C": None}
    if pressure == 0.0:
        return {"status": "absent", "temperature_C": None}

    try:
        owner_dewpoint = antoine_dew_temperature_diagnostic(
            species,
            pressure,
            vapor_pressure_data=vapor_pressure_data,
        )
    except WallSaturationPressureRefusal as exc:
        return {
            "status": "psat_refused",
            "temperature_C": None,
            "extrapolated": False,
            "reason": f"{type(exc).__name__}: {exc}",
        }
    if owner_dewpoint.get("status") == "diagnostic_only":
        owner_temperature_C = (
            float(owner_dewpoint["temperature_K"]) - 273.15
        )
        endpoint_pressures = [
            _production_psat(
                species, temperature_C, vapor_pressure_data, psat_cache
            )
            for temperature_C in (low_C, high_C)
        ]
        if all(
            not refused and not extrapolated and psat is not None
            for psat, refused, extrapolated, _reason in endpoint_pressures
        ):
            return {
                "status": (
                    "below_range" if owner_temperature_C < low_C
                    else "above_range" if owner_temperature_C > high_C
                    else "in_range"
                ),
                "temperature_C": owner_temperature_C,
                "extrapolated": False,
            }

    # The production inverse is certified-range only. This local inversion is
    # retained only for the requested train-band extrapolation diagnostic; it
    # calls the same production Psat resolver and marks every extrapolated row.
    samples = [
        low_C + (high_C - low_C) * index / 16.0
        for index in range(17)
    ]
    sampled_psat = []
    extrapolated = False
    for temperature_C in samples:
        psat, refused, sample_extrapolated, reason = _production_psat(
            species, temperature_C, vapor_pressure_data, psat_cache
        )
        extrapolated = extrapolated or sample_extrapolated
        if refused or psat is None or not math.isfinite(psat):
            return {
                "status": "psat_refused",
                "temperature_C": None,
                "extrapolated": extrapolated,
                "reason": reason,
            }
        sampled_psat.append(psat)
    if any(right < left for left, right in zip(sampled_psat, sampled_psat[1:])):
        return {
            "status": "psat_non_monotonic_in_wall_band",
            "temperature_C": None,
            "extrapolated": extrapolated,
        }
    if pressure <= sampled_psat[0]:
        return {
            "status": "below_range",
            "temperature_C": low_C,
            "extrapolated": extrapolated,
        }
    if pressure > sampled_psat[-1]:
        return {
            "status": "above_range",
            "temperature_C": high_C,
            "extrapolated": extrapolated,
        }

    low, high = low_C, high_C
    for _ in range(48):
        mid = (low + high) / 2.0
        psat, refused, sample_extrapolated, reason = _production_psat(
            species, mid, vapor_pressure_data, psat_cache
        )
        extrapolated = extrapolated or sample_extrapolated
        if refused or psat is None or not math.isfinite(psat):
            return {
                "status": "psat_refused",
                "temperature_C": None,
                "extrapolated": extrapolated,
                "reason": reason,
            }
        if psat < pressure:
            low = mid
        else:
            high = mid
    return {
        "status": "in_range",
        "temperature_C": (low + high) / 2.0,
        "extrapolated": extrapolated,
    }


def _print_mn_clean_window_diagnostics(run: dict, label: str) -> None:
    """Check whether a Mn dew-point window excludes both SiO and Si."""

    stages = list(run["model"].train.stages)
    wall_bands: dict[str, tuple[float, float]] = {}
    for stage in stages:
        stage_label = (
            str(getattr(stage, "stage_key", "") or "")
            or f"stage_{int(getattr(stage, 'stage_number', -1))}"
        )
        bounds = getattr(stage, "temp_range_C", None)
        if bounds is not None:
            wall_bands[f"stage:{stage_label}"] = tuple(
                float(value) for value in bounds
            )
    for segment in run["model"].pipe_segments:
        temperature_C = float(segment.wall_temperature_C)
        wall_bands[f"duct:{segment.name}"] = (temperature_C, temperature_C)
    design_hours = {
        int(peak["hour"]) for peak in run["peaks"].values()
    }
    design_hours.update(
        int(peak["hour"]) for peak in run["diagnostic_peaks"].values()
    )
    psat_cache: dict = {}
    feasible_count = 0
    evaluated_count = 0
    indeterminate_count = 0

    for species, peak in sorted(run["diagnostic_peaks"].items()):
        print(
            f"D452_MN_DESIGN_HOUR label={label} species={species} "
            f"hour={peak['hour']} stage={peak['stage']} "
            f"incident_kg_hr={peak['incoming_kg_hr']:.12g} "
            f"partial_pressure_pa={peak['partial_pressure_pa']} "
            f"pO2_mbar={peak['pO2_mbar']}"
        )

    profiles = {}
    profiles.update({
        (hour, f"stage:{location}"): profile
        for (hour, location), profile in run["stage_profiles"].items()
    })
    profiles.update({
        (hour, f"duct:{location}"): profile
        for (hour, location), profile in run["segment_profiles"].items()
    })
    for (hour, location), profile in sorted(profiles.items()):
        if hour not in design_hours:
            continue
        wall_band = wall_bands.get(location)
        if wall_band is None:
            print(
                f"D452_MN_CLEAN_WINDOW label={label} hour={hour} "
                f"location={location} status=indeterminate_wall_band_missing"
            )
            indeterminate_count += 1
            continue
        low_C, high_C = wall_band
        partials = profile["partial_pressures_pa"]
        pressures = {
            species: partials.get(species)
            for species in DIAGNOSTIC_SPECIES
        }
        dewpoints = {
            species: _dewpoint_in_wall_band(
                species,
                pressures[species],
                low_C,
                high_C,
                run["vapor_pressure_data"],
                psat_cache,
            )
            for species in DIAGNOSTIC_SPECIES
        }
        statuses = [row["status"] for row in dewpoints.values()]
        if any(
            status in {
                "partial_pressure_missing",
                "invalid_partial_pressure",
                "psat_refused",
                "psat_non_monotonic_in_wall_band",
            }
            for status in statuses
        ):
            window_status = "indeterminate"
            indeterminate_count += 1
        else:
            mn = dewpoints["Mn"]
            mn_can_condense = mn["status"] in {"in_range", "above_range"}
            silicon_clean_floor = low_C
            for species in ("SiO", "Si"):
                row = dewpoints[species]
                if row["status"] == "above_range":
                    silicon_clean_floor = math.inf
                elif row["status"] == "in_range":
                    silicon_clean_floor = max(
                        silicon_clean_floor,
                        float(row["temperature_C"]),
                    )
            mn_condensation_ceiling = (
                high_C if mn["status"] == "above_range"
                else float(mn["temperature_C"])
                if mn["status"] == "in_range" else -math.inf
            )
            feasible = (
                mn_can_condense
                and silicon_clean_floor < mn_condensation_ceiling
            )
            window_status = "clean_temperature_window" if feasible else "no_clean_temperature_window"
            evaluated_count += 1
            feasible_count += int(feasible)
        dewpoint_text = {
            species: {
                "temperature_C": row.get("temperature_C"),
                "status": row["status"],
                "extrapolated": row.get("extrapolated"),
                "reason": row.get("reason"),
            }
            for species, row in dewpoints.items()
        }
        print(
            f"D452_MN_CLEAN_WINDOW label={label} hour={hour} "
            f"location={location} pO2_mbar={profile.get('pO2_mbar')} "
            f"p_Mn_pa={pressures['Mn']} p_SiO_pa={pressures['SiO']} "
            f"p_Si_pa={pressures['Si']} wall_band_C=[{low_C:.12g},{high_C:.12g}] "
            f"dewpoints={json.dumps(dewpoint_text, sort_keys=True, separators=(',', ':'))} "
            f"status={window_status}"
        )

    complete = indeterminate_count == 0 and evaluated_count > 0
    summary_status = (
        "clean_window_found" if feasible_count
        else "no_clean_window_in_train_range" if complete
        else "indeterminate_missing_or_refused_inputs"
    )
    print(
        f"D452_MN_WINDOW_SUMMARY label={label} "
        f"design_hours={','.join(str(hour) for hour in sorted(design_hours))} "
        f"evaluated_profiles={evaluated_count} "
        f"indeterminate_profiles={indeterminate_count} "
        f"clean_window_profiles={feasible_count} status={summary_status}"
    )


def _print_purity_and_mn_accounts(run: dict, label: str) -> None:
    sim = run["sim"]
    purity = stage_purity_report(run["model"].train)
    for stage_key in (
        "stage_3_sio_zone",
        "stage_3b_mn_capture",
        "stage_4_alkali_mg_cyclone",
        "stage_4b_k_trap",
    ):
        row = purity.get(stage_key)
        if row is not None:
            print(
                f"D452_PURITY label={label} stage={stage_key} "
                f"row={json.dumps(row, sort_keys=True, separators=(',', ':'))}"
            )
    for index, stage in enumerate(run["model"].train.stages):
        mn_kg = float(stage.collected_kg.get("Mn", 0.0) or 0.0)
        if mn_kg > 0.0:
            print(
                f"D452_MN_STAGE label={label} index={index} "
                f"stage={getattr(stage, 'stage_key', '') or stage.stage_number} "
                f"Mn_kg={mn_kg:.12g}"
            )
    for account in (
        "process.wall_deposit",
        "terminal.offgas",
        "process.overhead_gas",
    ):
        balances = sim.atom_ledger.kg_by_account(account)
        print(
            f"D452_MN_ACCOUNT label={label} account={account} "
            f"Mn_kg={float(balances.get('Mn', 0.0)):.12g}"
        )
    notice = sim.condensation_model.last_sticking_alpha_provenance_notice
    mn_alpha = notice.get("alpha_s_provenance_by_species", {}).get("Mn", {})
    print(
        f"D452_MN_ALPHA label={label} "
        f"notice={json.dumps(mn_alpha, sort_keys=True, separators=(',', ':'))}"
    )


def _print_area_costs(stage_areas: dict[str, float]) -> None:
    cost_data = _load_yaml("optimize_costs.yaml")
    cost_parameters = cost_parameters_from_mapping(cost_data)
    summary = _collector_stage_hardware_cost_summary(
        SimpleNamespace(
            condensation_model=SimpleNamespace(
                stage_area_m2_by_stage=stage_areas,
            )
        ),
        cost_parameters=cost_parameters,
    )
    for stage_key, row in summary["collector_stage_costs"].items():
        print(
            f"D452_COST stage={stage_key} area_m2={row['area_m2']:.12g} "
            f"density_kg_m3={row['bulk_density_kg_m3']:.12g} "
            f"wall_thickness_m={row['wall_thickness_m']:.12g} "
            f"wall_mass_kg={row['mass_kg']:.12g} "
            f"cost_usd={row['cost_usd']:.12g}"
        )
    print(
        f"D452_COST_TOTAL status={summary['status']} "
        f"hardware_capital_cost_usd={summary['hardware_capital_cost_usd']:.12g}"
    )


def main() -> None:
    vapor = _load_yaml("vapor_pressures.yaml")
    feedstocks = _load_yaml("feedstocks.yaml")
    setpoints = _hkl_only_setpoints(_load_yaml("setpoints.yaml"))
    c6 = setpoints["campaigns"]["C6"]
    c6["default_hold_T_C"] = 1400.0
    c6["hold_temp_C"] = 1400.0
    c6["kinetic_driven_above_crossover"] = False
    seed_areas = {
        key: float(value)
        for key, value in setpoints["condenser_geometry"][
            "stage_areas_m2"
        ].items()
    }
    search_ceiling = float(
        setpoints["condenser_geometry"]["optimizer_bounds"][
            "stage_area_m2"
        ][1]
    )

    current_run = _run_c6(
        vapor=vapor,
        feedstocks=feedstocks,
        base_setpoints=setpoints,
        stage_areas=seed_areas,
        label="seed",
    )
    previous_areas: dict[str, float] | None = None
    final_areas = dict(seed_areas)
    final_run = current_run
    fixed_point_passes = 0
    converged = False
    for pass_number in range(1, MAX_FIXED_POINT_PASSES + 1):
        proposed_areas = _size_pass(current_run, search_ceiling)
        fixed_point_passes = pass_number
        if previous_areas is None:
            for stage_key in DESIGN_STAGE_KEYS:
                seed_area = float(seed_areas[stage_key])
                proposed = float(proposed_areas[stage_key])
                relative_change = abs(proposed - seed_area) / max(
                    abs(seed_area), 1.0e-30
                )
                print(
                    f"D452_FIXED_POINT pass={pass_number} stage={stage_key} "
                    f"basis=seed prior_m2={seed_area:.12g} "
                    f"proposed_m2={proposed:.12g} "
                    f"relative_change={relative_change:.12g}"
                )
        else:
            relative_changes = []
            for stage_key in DESIGN_STAGE_KEYS:
                prior = float(previous_areas[stage_key])
                proposed = float(proposed_areas[stage_key])
                relative_change = abs(proposed - prior) / max(
                    abs(prior), 1.0e-30
                )
                relative_changes.append(relative_change)
                print(
                    f"D452_FIXED_POINT pass={pass_number} stage={stage_key} "
                    f"prior_m2={prior:.12g} proposed_m2={proposed:.12g} "
                    f"relative_change={relative_change:.12g}"
                )
            if max(relative_changes, default=0.0) < AREA_RELATIVE_TOLERANCE:
                final_areas = dict(previous_areas)
                final_run = current_run
                converged = True
                break

        final_areas = dict(proposed_areas)
        final_run = current_run
        previous_areas = dict(proposed_areas)
        if pass_number < MAX_FIXED_POINT_PASSES:
            current_run = _run_c6(
                vapor=vapor,
                feedstocks=feedstocks,
                base_setpoints=setpoints,
                stage_areas=proposed_areas,
                label=f"sized_pass_{pass_number}",
            )
    if not converged:
        final_areas = dict(final_run["areas"])
    print(
        f"D452_FIXED_POINT_SUMMARY converged={converged} "
        f"relative_tolerance={AREA_RELATIVE_TOLERANCE:.12g} "
        f"max_passes={MAX_FIXED_POINT_PASSES} pass_count={fixed_point_passes}"
    )
    print("D452_FINAL_AREAS " + json.dumps(final_areas, sort_keys=True))
    _print_area_costs(final_areas)
    _print_purity_and_mn_accounts(final_run, "with_mn_stage_fixed_point")
    _print_mn_clean_window_diagnostics(final_run, "with_mn_stage_fixed_point")

    without_mn_areas = dict(final_areas)
    without_mn_run = _run_c6(
        vapor=vapor,
        feedstocks=feedstocks,
        base_setpoints=setpoints,
        stage_areas=without_mn_areas,
        label="without_mn_stage",
        include_mn_stage=False,
    )
    _print_purity_and_mn_accounts(without_mn_run, "without_mn_stage")
    _print_mn_clean_window_diagnostics(without_mn_run, "without_mn_stage")


if __name__ == "__main__":
    main()
