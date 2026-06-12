#!/usr/bin/env python3
"""Generate a REAL-fidelity optimizer profile from a shipped stub profile.

Flips backend stub->cached-real with live-fill, wiring the alphamelts subprocess
+ a shared reduced-real equilibrium cache. The `authorized_backend_version` is
queried from the LOCAL runtime engine (it embeds the binary path, so it is
machine-specific) — generate on the machine that will run the study.

Usage:
  make_recipe_db_profile.py <feedstock_id> [--campaign C2A_continuous]
      [--hours 30] [--gate stub_smoke|physics] [--db <cache.db>] [--out <path>]
      [--target <menu-id>|all]
Writes the real-fidelity profile to --out (default docs-private/recipe-db/profiles/<id>.real.yaml).
"""
import argparse
import copy
import math
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from simulator.optimize.product_pools import forbidden_gates_for_pool, product_pool_class

DESIGN_TARGET_PROVENANCE = (
    "design-composition-target-objective-2026-06-10 rev 3.2 PC target menu seed"
)
STANDARD_COST_METRICS = ("energy_kWh", "duration_h")
_SETPOINT_CAMPAIGN_ALIASES = {
    "C2A": "C2A_continuous",
    "C2A_staged": "C2A_staged",
    "C3_K": "C3",
    "C3_NA": "C3",
}
MENU_TARGET_IDS = (
    "pc-extract-na",
    "pc-extract-k",
    "pc-extract-fe",
    "pc-extract-mg",
    "pc-pure-silica-captured",
    "pc-extract-al",
    "pc-extract-o2",
    "pc-glass-clear",
    "pc-glass-green",
    "pc-glass-retain-na-k-c3",
    "pc-ceramic-ca-al-ree",
    "pc-ceramic-ca-al-ratio-seed",
    "pc-ceramic-ca-ree-after-al",
)
FURNACE_SURVIVABLE_T_MAX_C = 1800.0
DEFAULT_THERMAL_PREHEAT_RAMP_C_PER_HR = 600.0
DEFAULT_COLD_START_TEMPERATURE_C = 25.0
MIN_THERMAL_WINDOW_HOLD_HR = 1.0
THERMAL_VOLATILIZATION = "thermal_volatilization"
C3_METALLOTHERMIC_SHUTTLE = "c3_metallothermic_shuttle"
C6_MG_THERMITE = "c6_mg_thermite"


@dataclass(frozen=True)
class TargetMenuRow:
    target_id: str
    pool: str
    species_vector: Mapping[str, str]
    oxides: Mapping[str, Mapping[str, Any]]
    maturity_campaign: str
    maturity_hours: int
    ratios: tuple[Mapping[str, Any], ...] = ()
    extraction_min: Mapping[str, float] | None = None
    score_weights: Mapping[str, float] | None = None


def _extract_row(
    target_id: str,
    species: str,
    *,
    campaign: str,
    hours: int,
    completeness_min: float,
) -> TargetMenuRow:
    species_ids = ("Na", "K", "Fe", "Mg", "Si", "Al", "Ca", "O2")
    return TargetMenuRow(
        target_id=target_id,
        pool="captured_products",
        species_vector={
            key: "extract" if key == species else "free"
            for key in species_ids
        },
        oxides={},
        maturity_campaign=campaign,
        maturity_hours=hours,
        extraction_min={species: completeness_min},
        score_weights={"extraction": 1.0},
    )


TARGET_MENU: Mapping[str, TargetMenuRow] = {
    "pc-extract-na": _extract_row(
        "pc-extract-na",
        "Na",
        campaign="C2A_continuous",
        hours=24,
        completeness_min=0.95,
    ),
    "pc-extract-k": _extract_row(
        "pc-extract-k",
        "K",
        campaign="C2A_continuous",
        hours=24,
        completeness_min=0.90,
    ),
    "pc-extract-fe": _extract_row(
        "pc-extract-fe",
        "Fe",
        campaign="C2B",
        hours=17,
        completeness_min=0.85,
    ),
    "pc-extract-mg": _extract_row(
        "pc-extract-mg",
        "Mg",
        campaign="C4",
        hours=17,
        completeness_min=1.0,
    ),
    "pc-extract-al": _extract_row(
        "pc-extract-al",
        "Al",
        campaign="C6",
        hours=17,
        completeness_min=1.0,
    ),
    "pc-extract-o2": _extract_row(
        "pc-extract-o2",
        "O2",
        campaign="C2A_continuous",
        hours=24,
        completeness_min=1.0,
    ),
    "pc-glass-clear": TargetMenuRow(
        target_id="pc-glass-clear",
        pool="residual_rump_at_stop",
        species_vector={
            "Na": "extract",
            "K": "extract",
            "Fe": "extract",
            "Mg": "retain",
            "Ca": "retain",
            "Al": "retain",
            "Si": "retain",
            "O2": "free",
        },
        extraction_min={"Na": 0.95, "K": 0.90, "Fe": 0.85},
        oxides={
            "FeO_total": {
                "min": 0.0,
                "max": 0.5,
                "strict": True,
                "weight": 1.0,
                "needs_experiment": True,
                "provenance": "owner_seed_clear_fe_style",
            },
            "Al2O3": {
                "min": 15.0,
                "max": 20.0,
                "strict": False,
                "weight": 2.0,
                "needs_experiment": True,
                "provenance": "owner_seed_loose_stabilizer_style",
            },
        },
        maturity_campaign="C2B",
        maturity_hours=17,
        score_weights={"extraction": 0.50, "composition": 0.50},
    ),
    "pc-glass-retain-na-k-c3": TargetMenuRow(
        target_id="pc-glass-retain-na-k-c3",
        pool="residual_rump_at_stop",
        species_vector={
            "Na": "retain",
            "K": "retain",
            "Fe": "extract",
            "Mg": "retain",
            "Ca": "retain",
            "Al": "retain",
            "Si": "retain",
            "O2": "free",
        },
        extraction_min={"Fe": 0.95},
        oxides={
            "Na2O_plus_K2O": {
                "min": 5,
                "max": 18,
                "strict": False,
                "weight": 1.0,
                "needs_experiment": True,
                "provenance": (
                    "owner_adjudication_c3_na_retained_alkali_ceiling_soft_rank"
                ),
            },
            "Fe_total_as_Fe2O3_wt_pct": {
                "tier": "workable_glass",
                "needs_experiment": True,
            },
            "SiO2": {"min": 45, "max": 75, "weight": 1.0, "needs_experiment": True},
            "Al2O3_CaO_MgO_balance": {
                "min": 15,
                "max": 45,
                "weight": 1.0,
                "needs_experiment": True,
            },
        },
        # C3_NA retained-alkali ceiling is about 3.3 wt% for lunar mare; rank
        # closeness softly until best-tap C0b -> C2B -> partial C3_NA sequencing lands.
        maturity_campaign="C3_NA",
        maturity_hours=24,
        score_weights={"extraction": 0.0, "composition": 1.0},
    ),
    "pc-ceramic-ca-al-ree": TargetMenuRow(
        target_id="pc-ceramic-ca-al-ree",
        pool="terminal_rump_earned",
        species_vector={
            "Na": "extract",
            "K": "extract",
            "Fe": "extract",
            "Mg": "extract",
            "Si": "extract",
            "Ca": "retain",
            "Al": "retain",
            "O2": "free",
        },
        extraction_min={"Na": 0.95, "K": 0.90, "Fe": 0.85, "Mg": 0.85, "Si": 0.85},
        oxides={
            "CaO": {"min": 20, "max": 60, "weight": 1.0, "needs_experiment": True},
            "Al2O3": {"min": 10, "max": 45, "weight": 1.0, "needs_experiment": True},
            "TiO2_plus_Cr2O3_plus_REO": {
                "min": 1,
                "max": 25,
                "weight": 1.0,
                "needs_experiment": True,
            },
            "Na2O_plus_K2O": {
                "min": 0,
                "max": 2,
                "weight": 1.0,
                "needs_experiment": True,
            },
            "Fe_total_as_Fe2O3_wt_pct": {
                "min": 0,
                "max": 5,
                "weight": 1.0,
                "needs_experiment": True,
            },
        },
        maturity_campaign="C4",
        maturity_hours=17,
        score_weights={"extraction": 0.50, "composition": 0.50},
    ),
    "pc-ceramic-ca-al-ratio-seed": TargetMenuRow(
        target_id="pc-ceramic-ca-al-ratio-seed",
        pool="terminal_rump_earned",
        species_vector={
            "Na": "extract",
            "K": "extract",
            "Fe": "extract",
            "Mg": "extract",
            "Si": "extract",
            "Ca": "retain",
            "Al": "retain",
            "O2": "free",
        },
        extraction_min={"Na": 0.95, "K": 0.90, "Fe": 0.85, "Mg": 0.85, "Si": 0.85},
        oxides={
            "CaO": {"min": 20, "max": 60, "strict": True, "weight": 1.0, "needs_experiment": True},
            "Al2O3": {"min": 10, "max": 45, "strict": True, "weight": 1.0, "needs_experiment": True},
            "TiO2_plus_Cr2O3_plus_REO": {
                "min": 1,
                "max": 25,
                "strict": True,
                "weight": 1.0,
                "needs_experiment": True,
            },
            "Na2O_plus_K2O": {
                "min": 0,
                "max": 2,
                "strict": True,
                "weight": 1.0,
                "needs_experiment": True,
            },
            "Fe_total_as_Fe2O3_wt_pct": {
                "min": 0,
                "max": 5,
                "strict": True,
                "weight": 1.0,
                "needs_experiment": True,
            },
        },
        ratios=(
            {
                "numerator": "CaO",
                "denominator": "Al2O3",
                "min": 0.45,
                "max": 0.75,
                "strict": True,
                "weight": 1.0,
                "needs_experiment": True,
                "provenance": "owner_seed_calcium_aluminate_composition",
            },
        ),
        maturity_campaign="C4",
        maturity_hours=17,
        score_weights={"extraction": 0.50, "composition": 0.50},
    ),
}


def _runtime_engine_identity() -> tuple[str, str]:
    from simulator.melt_backend.alphamelts import AlphaMELTSBackend
    b = AlphaMELTSBackend()
    b.initialize({"mode": None})
    name = getattr(b, "name", None) or "alphamelts"
    getter = getattr(b, "get_engine_version", None)
    version = str(getter()).strip() if callable(getter) else ""
    if not version:
        raise SystemExit("could not resolve runtime engine version")
    return str(name), version


def _load_base_profile(feedstock: str) -> dict[str, Any]:
    src = REPO_ROOT / "data" / "optimize_profiles" / f"{feedstock}.yaml"
    if not src.exists():
        raise SystemExit(f"no shipped profile: {src}")
    profile = yaml.safe_load(src.read_text())
    if not isinstance(profile, dict):
        raise SystemExit(f"invalid shipped profile: {src}")
    return profile


def _cached_real_config(db_path: str, name: str, version: str) -> dict[str, str]:
    return {
        "db_path": db_path,
        "miss_policy": "live-fill",
        "authorized_backend_name": name,
        "authorized_backend_version": version,
    }


def _apply_cached_real(
    profile: dict[str, Any],
    *,
    campaign: str,
    hours: int,
    gate: str,
    cache: Mapping[str, str],
) -> None:
    profile["study_constraints"] = gate
    run = dict(profile.get("run") or {})
    run.update({
        "campaign": campaign,
        "hours": hours,
        "backend_name": "cached-real",
        "reduced_real_cache": dict(cache),
    })
    profile["run"] = run
    fid = dict(profile.get("fidelities") or {})
    fid["high"] = {
        "backend_name": "cached-real",
        "hours": hours,
        "reduced_real_cache": dict(cache),
    }
    profile["fidelities"] = fid


def _with_row_provenance(
    oxides: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    copied: dict[str, dict[str, Any]] = {}
    for oxide, row in oxides.items():
        copied[str(oxide)] = dict(row)
    return copied


def _row_scores_extraction(row: TargetMenuRow) -> bool:
    weights = row.score_weights or {}
    try:
        return float(weights.get("extraction", 0.0)) > 0.0
    except (TypeError, ValueError):
        return False


def _scored_extraction_min(row: TargetMenuRow) -> dict[str, float]:
    if not _row_scores_extraction(row):
        return {}
    return dict(row.extraction_min or {})


def _extraction_mechanisms(row: TargetMenuRow, *, campaign: str) -> dict[str, str]:
    campaigns = _row_campaign_set(campaign)
    mechanisms: dict[str, str] = {}
    for species in _scored_extraction_min(row):
        mechanism = _extraction_mechanism_for_species(str(species), campaigns)
        if mechanism is not None:
            mechanisms[str(species)] = mechanism
    return mechanisms


def _row_campaign_set(campaign: str) -> frozenset[str]:
    selected = str(campaign)
    campaigns = {selected, _setpoint_campaign_key(selected)}
    if selected in {"C3", "C3_K", "C3_NA"}:
        campaigns.add("C3")
    return frozenset(campaigns)


def _extraction_mechanism_for_species(
    species: str,
    campaigns: frozenset[str],
) -> str | None:
    if species in {"Fe", "Cr"} and "C3" in campaigns:
        return C3_METALLOTHERMIC_SHUTTLE
    if species == "Al" and "C6" in campaigns:
        return C6_MG_THERMITE
    if _species_reachable_by_thermal_volatilization(species):
        return THERMAL_VOLATILIZATION
    return None


def _target_objective(
    row: TargetMenuRow,
    *,
    campaign: str,
    hours: int | float,
    hold_construction: str | None = None,
) -> dict[str, Any]:
    extraction_min = _scored_extraction_min(row)
    species_vector = dict(row.species_vector)
    if not extraction_min:
        species_vector = {
            species: ("retain" if action == "extract" else action)
            for species, action in species_vector.items()
        }
    target = {
        "pool": row.pool,
        "require_coating_gate": True,
        "species_vector": species_vector,
        "maturity": {
            "mode": "campaign_hours",
            "campaign": campaign,
            "hours": hours,
        },
        "constraints": {
            "coating_min_campaigns_to_resinter": "profile_default",
            "furnace_T_max_C": "profile_or_study_constraint",
        },
        "score_weights": dict(row.score_weights or {"extraction": 0.5, "composition": 0.5}),
    }
    if extraction_min:
        target["extraction"] = {
            "basis": "input_element_mol",
            "captured_pool": (
                "captured_stage_3_silica"
                if row.pool == "captured_stage_3_silica"
                else "captured_products"
            ),
            "credit_policy": {
                "additives": "no_product_credit",
                "vented": "no_product_credit",
            },
            "completeness_min": extraction_min,
            "mechanisms": _extraction_mechanisms(row, campaign=campaign),
        }
    thermal_window = _campaign_window_disposition(campaign)
    if thermal_window is not None:
        target["thermal_window"] = thermal_window
    if hold_construction is not None:
        target["hold_construction"] = hold_construction
    if row.oxides or row.ratios:
        window: dict[str, Any] = {
            "pool": row.pool,
            "basis": "oxide_wt_pct",
            "mode": "hard_window",
            "exploratory": False,
            "oxides": _with_row_provenance(row.oxides),
        }
        if row.ratios:
            window["ratios"] = [dict(ratio) for ratio in row.ratios]
        target["composition_window"] = window
    return {
        "type": "composition_target",
        "id": row.target_id,
        "metric": f"composition_target:{row.target_id}",
        "sense": "maximize",
        "units": "score_0_1",
        "weight": 1.0,
        "rationale": "PC target matrix seed; bounds are provisional.",
        "target": target,
    }


def _standard_cost_objectives(profile: Mapping[str, Any]) -> list[dict[str, Any]]:
    objectives = profile.get("objectives") or []
    if not isinstance(objectives, list):
        raise SystemExit("base profile objectives must be a list")
    by_metric = {
        objective.get("metric"): dict(objective)
        for objective in objectives
        if isinstance(objective, Mapping)
    }
    missing = [metric for metric in STANDARD_COST_METRICS if metric not in by_metric]
    if missing:
        raise SystemExit(f"base profile missing standard cost objectives: {', '.join(missing)}")
    return [by_metric[metric] for metric in STANDARD_COST_METRICS]


def _remove_constraint_gate(profile: dict[str, Any], gate: str) -> None:
    constraints = profile.get("constraints")
    if not isinstance(constraints, dict):
        return
    gates = constraints.get("gates")
    if not isinstance(gates, list):
        return
    constraints["gates"] = [item for item in gates if item != gate]


def _row_delivers_condenser_stream(row: TargetMenuRow) -> bool:
    return _row_product_pool_class(row) == "stream"


def _row_product_pool_class(row: TargetMenuRow) -> str:
    try:
        return product_pool_class(row.pool)
    except ValueError as exc:
        raise SystemExit(
            f"PC target {row.target_id!r} has unclassified product pool {row.pool!r}"
        ) from exc


def _scope_target_profile_constraints(profile: dict[str, Any], row: TargetMenuRow) -> None:
    constraints = profile.get("constraints")
    if not isinstance(constraints, dict):
        return
    gates = constraints.get("gates")
    if not isinstance(gates, list):
        return

    for gate in forbidden_gates_for_pool(row.pool):
        _remove_constraint_gate(profile, gate)

    extraction_targets = tuple(_scored_extraction_min(row))
    if extraction_targets:
        constraints["target_species"] = list(extraction_targets)
    else:
        _remove_constraint_gate(profile, "extraction_completeness")
        _remove_constraint_gate(profile, "knudsen_viscous")
        constraints.pop("target_species", None)


def _setpoint_campaign_key(campaign: str) -> str:
    return _SETPOINT_CAMPAIGN_ALIASES.get(campaign, campaign)


def _setpoint_campaign_config(campaign: str) -> Mapping[str, Any]:
    src = REPO_ROOT / "data" / "setpoints.yaml"
    loaded = yaml.safe_load(src.read_text())
    if not isinstance(loaded, Mapping):
        raise SystemExit(f"invalid setpoints file: {src}")
    campaigns = loaded.get("campaigns")
    if not isinstance(campaigns, Mapping):
        raise SystemExit(f"setpoints missing campaigns mapping: {src}")
    selected = campaigns.get(_setpoint_campaign_key(campaign))
    if selected is None:
        raise SystemExit(f"setpoints missing campaign {campaign!r}")
    if not isinstance(selected, Mapping):
        raise SystemExit(f"setpoints campaign {campaign!r} must be a mapping")
    return selected


def _setpoint_campaign_max_hold_hr(campaign: str) -> float | None:
    value = _setpoint_campaign_config(campaign).get("max_hold_hr")
    if isinstance(value, Mapping):
        return None
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise SystemExit(
            f"{campaign}.max_hold_hr must be numeric when declaring a thermal window"
        ) from exc


def _validate_campaign_window_within_caps(
    row: TargetMenuRow,
    *,
    campaign: str,
    hours: int | float,
) -> None:
    cfg = _setpoint_campaign_config(campaign)
    temp_range = cfg.get("temp_range_C")
    if temp_range is None:
        return
    low_C, high_C = _numeric_interval(temp_range, label=f"{campaign}.temp_range_C")
    if high_C < low_C:
        raise SystemExit(f"{campaign}.temp_range_C must be ascending")
    if high_C > FURNACE_SURVIVABLE_T_MAX_C:
        raise SystemExit(
            f"{row.target_id} {campaign}.temp_range_C exceeds furnace-survivable "
            f"window: {high_C:g} C > {FURNACE_SURVIVABLE_T_MAX_C:g} C"
        )
    max_hold_hr = _setpoint_campaign_max_hold_hr(campaign)
    preheat_hours = _thermal_window_preheat_hours(campaign, low_C)
    total_hours = preheat_hours + float(hours)
    if max_hold_hr is not None and total_hours > max_hold_hr:
        raise SystemExit(
            f"{row.target_id} {campaign}.duration_h exceeds campaign max_hold_hr: "
            f"{total_hours:g} h including {preheat_hours:g} h preheat > "
            f"{max_hold_hr:g} h"
        )


def _construct_campaign_hold_hours(
    row: TargetMenuRow,
    *,
    campaign: str,
    requested_hours: int | float,
) -> tuple[int | float, str | None]:
    cfg = _setpoint_campaign_config(campaign)
    temp_range = cfg.get("temp_range_C")
    if temp_range is None:
        return requested_hours, None

    low_C, high_C = _numeric_interval(temp_range, label=f"{campaign}.temp_range_C")
    if high_C < low_C:
        raise SystemExit(f"{campaign}.temp_range_C must be ascending")

    max_hold_hr = _setpoint_campaign_max_hold_hr(campaign)
    if max_hold_hr is None:
        return requested_hours, None

    try:
        requested = float(requested_hours)
    except (TypeError, ValueError) as exc:
        raise SystemExit(f"{row.target_id} {campaign}.duration_h must be numeric") from exc
    if not math.isfinite(requested) or requested <= 0.0:
        raise SystemExit(f"{row.target_id} {campaign}.duration_h must be positive")

    preheat_hours = _thermal_window_preheat_hours(campaign, low_C)
    usable_hold_hr = max_hold_hr - float(preheat_hours)
    if usable_hold_hr < MIN_THERMAL_WINDOW_HOLD_HR:
        raise SystemExit(
            f"{row.target_id} {campaign}.duration_h has no usable thermal-window hold: "
            f"{_format_hours(max_hold_hr)} h max_hold_hr - "
            f"{_format_hours(preheat_hours)} h preheat leaves "
            f"{_format_hours(usable_hold_hr)} h"
        )

    constructed = min(requested, usable_hold_hr)
    if constructed < MIN_THERMAL_WINDOW_HOLD_HR:
        raise SystemExit(
            f"{row.target_id} {campaign}.duration_h must be at least "
            f"{_format_hours(MIN_THERMAL_WINDOW_HOLD_HR)} h"
        )
    constructed_value = _plain_hour_value(constructed)
    if constructed >= requested:
        return constructed_value, None

    return constructed_value, (
        f"requested {_format_hours(requested)} h -> "
        f"{_format_hours(constructed)} h under {campaign} max_hold "
        f"{_format_hours(max_hold_hr)} h - {_format_hours(preheat_hours)} h preheat"
    )


def _plain_hour_value(value: float) -> int | float:
    if float(value).is_integer():
        return int(value)
    return value


def _format_hours(value: int | float) -> str:
    return f"{float(value):g}"


def _thermal_window_preheat_hours(campaign: str, low_C: float) -> int:
    cfg = _setpoint_campaign_config(campaign)
    value = cfg.get("preheat_ramp_C_per_hr") or cfg.get("ramp_rate_C_per_hr")
    if value is None:
        ramp_C_per_hr = DEFAULT_THERMAL_PREHEAT_RAMP_C_PER_HR
    else:
        try:
            ramp_C_per_hr = float(value)
        except (TypeError, ValueError) as exc:
            raise SystemExit(f"{campaign}.preheat_ramp_C_per_hr must be numeric") from exc
    if ramp_C_per_hr <= 0.0:
        raise SystemExit(f"{campaign}.preheat_ramp_C_per_hr must be positive")
    return int(
        math.ceil(
            max(0.0, low_C - DEFAULT_COLD_START_TEMPERATURE_C) / ramp_C_per_hr
        )
    )


def _campaign_window_patch(campaign: str, *, hours: int | float) -> dict[str, Any] | None:
    cfg = _setpoint_campaign_config(campaign)
    temp_range = cfg.get("temp_range_C")
    if temp_range is None:
        return None
    low_C, high_C = _numeric_interval(temp_range, label=f"{campaign}.temp_range_C")
    if high_C < low_C:
        raise SystemExit(f"{campaign}.temp_range_C must be ascending")
    patch: dict[str, Any] = {
        "temp_range_C": [low_C, high_C],
    }
    duration = cfg.get("duration_h")
    if duration is not None and _recipe_campaign_key_allowed(campaign, "duration_h"):
        patch["duration_h"] = duration
    for key in (
        "p_total_mbar",
        "p_total_mbar_default",
        "pO2_mbar",
        "pO2_mbar_default",
        "gas_temperature_C",
        "flow_regime",
    ):
        value = cfg.get(key)
        if value is not None and _recipe_campaign_key_allowed(campaign, key):
            patch[key] = copy.deepcopy(value)
    return patch


def _campaign_window_disposition(campaign: str) -> str | None:
    cfg = _setpoint_campaign_config(campaign)
    if cfg.get("temp_range_C") is None:
        return f"not-declared-for-campaign:{_setpoint_campaign_key(campaign)}"
    return None


def _vapor_pressure_entry(species: str) -> Mapping[str, Any] | None:
    src = REPO_ROOT / "data" / "vapor_pressures.yaml"
    loaded = yaml.safe_load(src.read_text())
    if not isinstance(loaded, Mapping):
        raise SystemExit(f"invalid vapor-pressure sidecar: {src}")
    for section in ("species", "metals", "oxide_vapors"):
        species_data = loaded.get(section)
        if not isinstance(species_data, Mapping):
            continue
        entry = species_data.get(species)
        if isinstance(entry, Mapping):
            return entry
    return None


def _species_reachable_by_thermal_volatilization(species: str) -> bool:
    if species == "O2":
        return True
    entry = _vapor_pressure_entry(species)
    if entry is None:
        return True
    notes = str(entry.get("notes", "")).lower()
    boiling_point = entry.get("boiling_point_C")
    try:
        boiling_point_C = float(boiling_point)
    except (TypeError, ValueError):
        boiling_point_C = None
    if "not pyrolysable" in notes and (
        boiling_point_C is None or boiling_point_C > FURNACE_SURVIVABLE_T_MAX_C
    ):
        return False
    return True


def _target_blocked_reason(row: TargetMenuRow, *, campaign: str) -> str | None:
    if not _row_scores_extraction(row):
        return None
    campaigns = _row_campaign_set(campaign)
    for species in _scored_extraction_min(row):
        species_name = str(species)
        if _extraction_mechanism_for_species(species_name, campaigns) is None:
            return _missing_extraction_mechanism_reason(species_name, campaigns)
    return None


def _missing_extraction_mechanism_reason(
    species: str,
    campaigns: frozenset[str],
) -> str:
    if species == "Al" and "C6" not in campaigns:
        return "Al reachable via C6 thermite - row lacks C6"
    if species in {"Fe", "Cr"} and "C3" not in campaigns:
        return f"{species} reachable via C3 metallothermic shuttle - row lacks C3"
    return f"{species} lacks thermal volatilization or configured extraction mechanism"


def _blocked_target_profile(
    base_profile: Mapping[str, Any],
    row: TargetMenuRow,
    *,
    campaign: str,
    hours: int,
    reason: str,
) -> dict[str, Any]:
    feedstock = str(base_profile["feedstock"])
    return {
        "profile_id": f"{feedstock}-{row.target_id}-blocked-v1",
        "profile_schema_version": "blocked-target-v1",
        "feedstock": feedstock,
        "target_id": row.target_id,
        "status": "BLOCKED",
        "blocked_reason": reason,
        "campaign": campaign,
        "hours": hours,
        "disposition": {
            "kind": "missing_extraction_mechanism",
            "reason": reason,
            "vapor_pressure_sidecar": "data/vapor_pressures.yaml",
        },
    }


def _recipe_campaign_key_allowed(campaign: str, key: str) -> bool:
    from simulator.optimize.recipe import RecipeSchema, RecipeValidationError

    try:
        RecipeSchema().spec_for(("campaigns", campaign, key))
    except RecipeValidationError:
        return False
    return True


def _seed_source_campaigns(seed: Mapping[str, Any]) -> frozenset[str]:
    campaigns: set[str] = set()
    source_campaign = seed.get("source_campaign")
    if source_campaign is not None:
        campaigns.add(str(source_campaign))
    source_campaigns = seed.get("source_campaigns")
    if source_campaigns is None:
        return frozenset(campaigns)
    if isinstance(source_campaigns, (str, bytes)) or not isinstance(source_campaigns, list):
        raise SystemExit(
            f"seed {seed.get('id', '<unnamed>')} source_campaigns must be a list"
        )
    campaigns.update(str(campaign) for campaign in source_campaigns)
    return frozenset(campaigns)


def _ensure_target_campaign_window(
    profile: dict[str, Any],
    row: TargetMenuRow,
    *,
    campaign: str,
    hours: int | float,
) -> None:
    patch = _campaign_window_patch(campaign, hours=hours)
    if patch is None:
        return
    seeds = list(profile.get("seed_recipes") or [])
    for seed in seeds:
        if not isinstance(seed, dict):
            continue
        if campaign not in _seed_source_campaigns(seed):
            continue
        seed_patch = seed.setdefault("patch", {})
        if not isinstance(seed_patch, dict):
            raise SystemExit(f"seed {seed.get('id', '<unnamed>')} patch must be a mapping")
        campaigns = seed_patch.setdefault("campaigns", {})
        if not isinstance(campaigns, dict):
            raise SystemExit(f"seed {seed.get('id', '<unnamed>')} campaigns must be a mapping")
        existing = campaigns.setdefault(campaign, {})
        if not isinstance(existing, dict):
            raise SystemExit(f"seed {seed.get('id', '<unnamed>')} {campaign} patch must be a mapping")
        for key, value in patch.items():
            if key in existing and existing[key] != value:
                if key == "duration_h":
                    continue
                raise SystemExit(
                    f"{row.target_id} {campaign}.{key} conflicts with setpoint window"
                )
            existing.setdefault(key, value)
        profile["seed_recipes"] = seeds
        return
    seeds.append({
        "id": f"{row.target_id}-{campaign}-thermal-window",
        "source_campaign": campaign,
        "rationale": "materialize target campaign thermal window for runtime scheduling",
        "patch": {"campaigns": {campaign: patch}},
    })
    profile["seed_recipes"] = seeds


def _numeric_interval(value: Any, *, label: str) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise SystemExit(f"{label} must be a two-value interval")
    try:
        return float(value[0]), float(value[1])
    except (TypeError, ValueError) as exc:
        raise SystemExit(f"{label} must be numeric") from exc


def _target_profile(
    base_profile: Mapping[str, Any],
    row: TargetMenuRow,
    *,
    campaign: str,
    hours: int | float,
    hold_construction: str | None = None,
) -> dict[str, Any]:
    _validate_campaign_window_within_caps(row, campaign=campaign, hours=hours)
    profile = copy.deepcopy(dict(base_profile))
    _scope_target_profile_constraints(profile, row)
    feedstock = str(profile["feedstock"])
    profile["profile_id"] = f"{feedstock}-{row.target_id}-recipe-db-profile-v1"
    profile["description"] = f"{feedstock} PC target matrix profile for {row.target_id}."
    profile["north_star_rationale"] = (
        f"Score {row.target_id} from the rev 2.1 PC target menu while retaining "
        "standard energy and duration minimization."
    )
    profile["objective_emphasis"] = f"PC target matrix: {row.target_id}."
    profile["objectives"] = [
        _target_objective(
            row,
            campaign=campaign,
            hours=hours,
            hold_construction=hold_construction,
        ),
        *_standard_cost_objectives(profile),
    ]
    _ensure_target_campaign_window(
        profile,
        row,
        campaign=campaign,
        hours=hours,
    )
    return profile


def _target_campaign(campaign: str | None, row: TargetMenuRow) -> str:
    if campaign is None:
        return row.maturity_campaign
    if campaign == "C3" and row.maturity_campaign in {"C3_K", "C3_NA"}:
        return row.maturity_campaign
    return _normalize_campaign(campaign)


def _normalize_campaign(campaign: str) -> str:
    if campaign == "C3":
        return "C3_NA"
    return campaign


def _plain_data(value: Any) -> Any:
    if isinstance(value, MappingProxyType):
        return {str(key): _plain_data(item) for key, item in value.items()}
    if isinstance(value, Mapping):
        return {str(key): _plain_data(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain_data(item) for item in value]
    if isinstance(value, list):
        return [_plain_data(item) for item in value]
    return value


def _validated_profile(profile: Mapping[str, Any], *, source: str) -> dict[str, Any]:
    from simulator.optimize.profiles import ProfileValidationError, validate_profile

    try:
        validated = validate_profile(
            copy.deepcopy(dict(profile)),
            expected_feedstock=str(profile["feedstock"]),
            source=source,
        )
    except ProfileValidationError as exc:
        raise SystemExit(f"generated profile failed validation: {exc}") from exc
    return _plain_data(validated)


def _resolve_target_rows(raw_targets: list[str] | None) -> list[TargetMenuRow]:
    if not raw_targets:
        return []
    selected: list[str] = []
    for raw in raw_targets:
        if raw == "all":
            selected.extend(TARGET_MENU)
        else:
            selected.append(raw)
    rows: list[TargetMenuRow] = []
    seen: set[str] = set()
    for target_id in selected:
        if target_id in seen:
            continue
        seen.add(target_id)
        if target_id not in MENU_TARGET_IDS:
            known = ", ".join(MENU_TARGET_IDS)
            raise SystemExit(f"unknown PC target {target_id!r}; known targets: {known}")
        try:
            rows.append(TARGET_MENU[target_id])
        except KeyError as exc:
            raise SystemExit(
                f"PC target {target_id!r} has no rev 3.2 seed window; refusing to invent bounds"
            ) from exc
    return rows


def _output_path(feedstock: str, target_id: str | None, out_arg: str | None, count: int) -> Path:
    if target_id is None:
        return Path(out_arg) if out_arg else (
            REPO_ROOT / "docs-private" / "recipe-db" / "profiles" / f"{feedstock}.real.yaml"
        )
    default = (
        REPO_ROOT
        / "docs-private"
        / "recipe-db"
        / "profiles"
        / f"{feedstock}__{target_id}.real.yaml"
    )
    if out_arg is None:
        return default
    out = Path(out_arg)
    if count == 1 and out.suffix in {".yaml", ".yml"}:
        return out
    if out.suffix in {".yaml", ".yml"}:
        raise SystemExit("--out must be a directory when emitting multiple target profiles")
    return out / f"{feedstock}__{target_id}.real.yaml"


def _write_profile(profile: Mapping[str, Any], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(yaml.safe_dump(_plain_data(profile), sort_keys=False))


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("feedstock")
    ap.add_argument("--campaign", default=None)
    ap.add_argument("--hours", type=int, default=None)
    ap.add_argument("--gate", default="stub_smoke", choices=["stub_smoke", "physics"])
    ap.add_argument("--db", default="docs-private/recipe-db/reduced-real.db")
    ap.add_argument("--out", default=None)
    ap.add_argument(
        "--target",
        action="append",
        default=None,
        help="PC target menu id to emit; repeatable, or 'all' for materialized seed rows",
    )
    args = ap.parse_args(argv)

    profile = _load_base_profile(args.feedstock)

    name, version = _runtime_engine_identity()
    cache = _cached_real_config(args.db, name, version)
    target_rows = _resolve_target_rows(args.target)

    if not target_rows:
        campaign = _normalize_campaign(args.campaign or "C2A_continuous")
        hours = args.hours if args.hours is not None else 30
        _apply_cached_real(
            profile,
            campaign=campaign,
            hours=hours,
            gate=args.gate,
            cache=cache,
        )
        validated = _validated_profile(profile, source=f"<generated:{args.feedstock}>")
        out = _output_path(args.feedstock, None, args.out, 1)
        _write_profile(validated, out)
        print(f"wrote {out}")
        print(f"  engine: {name}@{version}")
        print(f"  campaign={campaign} hours={hours} gate={args.gate} db={args.db}")
        return 0

    for row in target_rows:
        campaign = _target_campaign(args.campaign, row)
        requested_hours = args.hours if args.hours is not None else row.maturity_hours
        blocked_reason = _target_blocked_reason(row, campaign=campaign)
        out = _output_path(args.feedstock, row.target_id, args.out, len(target_rows))
        if blocked_reason is not None:
            _write_profile(
                _blocked_target_profile(
                    profile,
                    row,
                    campaign=campaign,
                    hours=requested_hours,
                    reason=blocked_reason,
                ),
                out,
            )
            print(f"blocked {out}")
            print(f"  target={row.target_id}")
            print(f"  reason={blocked_reason}")
            continue
        hours, hold_construction = _construct_campaign_hold_hours(
            row,
            campaign=campaign,
            requested_hours=requested_hours,
        )
        target_profile = _target_profile(
            profile,
            row,
            campaign=campaign,
            hours=hours,
            hold_construction=hold_construction,
        )
        _apply_cached_real(
            target_profile,
            campaign=campaign,
            hours=hours,
            gate=args.gate,
            cache=cache,
        )
        validated = _validated_profile(
            target_profile,
            source=f"<generated:{args.feedstock}:{row.target_id}>",
        )
        _write_profile(validated, out)
        print(f"wrote {out}")
        print(f"  target={row.target_id}")
        print(f"  engine: {name}@{version}")
        print(f"  campaign={campaign} hours={hours} gate={args.gate} db={args.db}")
        if hold_construction is not None:
            print(f"  hold_construction={hold_construction}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
