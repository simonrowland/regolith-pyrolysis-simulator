"""Size d-452 collector areas from production C6 capture calls on a Studio."""

from __future__ import annotations

import copy
import json
import math
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path.cwd()))

from simulator.condensation import (  # noqa: E402
    STAGE_AREA_KEY_BY_STAGE_KEY,
    STAGE_AREA_KEY_BY_STAGE_NUMBER,
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


def _area_for_ninety_percent(model, original, peak) -> tuple[float, float, str]:
    """Replay the production resolver with a growing area bracket."""

    saved_state = _snapshot_model_state(model)
    saved_areas = dict(model.stage_area_m2_by_stage)

    def efficiency(area_m2: float) -> float:
        model.stage_area_m2_by_stage[peak["stage_key"]] = area_m2
        return float(original(**_capture_replay_args(peak["kwargs"])))

    try:
        for name, value in peak["model_state"].items():
            setattr(model, name, value)
        high = max(1.0, peak["current_area_m2"])
        high_eta = efficiency(high)
        while math.isfinite(high_eta) and high_eta < CAPTURE_TARGET:
            if high >= 1.0e300:
                ceiling_eta = efficiency(1.0e300)
                status = (
                    "capture_ceiling_below_target"
                    if ceiling_eta < CAPTURE_TARGET
                    else "numerical_bracket_exhausted"
                )
                return high, ceiling_eta, status
            high *= 2.0
            high_eta = efficiency(high)
        if not math.isfinite(high_eta):
            return high, high_eta, "non_finite_resolver_result"
        low = 0.0
        for _ in range(60):
            mid = (low + high) / 2.0
            if efficiency(mid) >= CAPTURE_TARGET:
                high = mid
            else:
                low = mid
        return high, efficiency(high), "sized"
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
    original = model._condensation_efficiency
    hourly: dict[tuple[str, str, int], dict] = {}
    peaks: dict[tuple[str, str], dict] = {}

    def capture(**kwargs):
        eta = original(**kwargs)
        stage = kwargs["stage"]
        area_key = _stage_area_key(stage)
        species = str(kwargs["species"])
        targets = set(getattr(stage, "target_species", ()) or ())
        if area_key not in DESIGN_STAGE_KEYS or species not in targets:
            return eta
        hour = int(getattr(sim.melt, "hour", 0))
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
    return {
        "sim": sim,
        "model": model,
        "original": original,
        "peaks": peaks,
        "areas": dict(stage_areas),
    }


def _size_pass(run: dict, search_ceiling_m2: float) -> dict[str, float]:
    required_by_stage: dict[str, list[float]] = {}
    for (area_key, species), peak in sorted(run["peaks"].items()):
        area, eta, status = _area_for_ninety_percent(
            run["model"], run["original"], peak
        )
        required_by_stage.setdefault(area_key, []).append(area)
        print(
            f"D452_PEAK stage={area_key} species={species} hour={peak['hour']} "
            f"incident_kg_hr={peak['incoming_kg_hr']:.12g} "
            f"area_at_run_m2={peak['current_area_m2']:.12g} "
            f"eta_at_run_area={peak['eta']:.12g} "
            f"area_for_90pct_m2={area:.12g} eta_at_area={eta:.12g} "
            f"status={status} "
            f"exceeds_optimizer_bound={area > search_ceiling_m2}"
        )
    result = dict(run["areas"])
    for stage_key in DESIGN_STAGE_KEYS:
        areas = required_by_stage.get(stage_key, [])
        if areas:
            result[stage_key] = max(areas)
        print(
            f"D452_STAGE_AREA stage={stage_key} "
            + (
                f"area_for_all_designated_species_m2={result[stage_key]:.12g}"
                if areas else "status=no_designated_flux"
            )
        )
    return result


def _print_purity_and_mn_accounts(run: dict, label: str) -> None:
    sim = run["sim"]
    purity = stage_purity_report(sim.train)
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
    for index, stage in enumerate(sim.train.stages):
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

    seed_run = _run_c6(
        vapor=vapor,
        feedstocks=feedstocks,
        base_setpoints=setpoints,
        stage_areas=seed_areas,
        label="seed",
    )
    first_areas = _size_pass(seed_run, search_ceiling)
    first_run = _run_c6(
        vapor=vapor,
        feedstocks=feedstocks,
        base_setpoints=setpoints,
        stage_areas=first_areas,
        label="sized",
    )
    second_areas = _size_pass(first_run, search_ceiling)
    final_areas = {
        key: max(first_areas.get(key, 0.0), second_areas.get(key, 0.0))
        for key in set(first_areas) | set(second_areas)
    }
    for stage_key in DESIGN_STAGE_KEYS:
        first = first_areas[stage_key]
        second = second_areas[stage_key]
        relative_change = abs(second - first) / max(first, 1.0e-30)
        print(
            f"D452_FIXED_POINT stage={stage_key} pass1_m2={first:.12g} "
            f"pass2_m2={second:.12g} final_m2={final_areas[stage_key]:.12g} "
            f"relative_change={relative_change:.12g}"
        )
    print("D452_FINAL_AREAS " + json.dumps(final_areas, sort_keys=True))
    _print_area_costs(final_areas)
    _print_purity_and_mn_accounts(first_run, "with_mn_stage_pass1")

    without_mn_areas = dict(first_areas)
    without_mn_areas["mn_stage3b"] = 0.0
    without_mn_run = _run_c6(
        vapor=vapor,
        feedstocks=feedstocks,
        base_setpoints=setpoints,
        stage_areas=without_mn_areas,
        label="mn_stage_area_zero",
    )
    _print_purity_and_mn_accounts(without_mn_run, "without_mn_stage")


if __name__ == "__main__":
    main()
