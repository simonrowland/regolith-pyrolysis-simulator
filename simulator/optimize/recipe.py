"""Deny-by-default recipe schema for optimizer-facing setpoint patches."""

from __future__ import annotations

import copy
from dataclasses import dataclass, replace
from functools import lru_cache
import hashlib
import math
from fnmatch import fnmatchcase
from types import MappingProxyType
from typing import Any, Literal, Mapping

import yaml

from simulator.config import DEFAULT_DATA_DIR
from simulator.chemistry.kernel.config import (
    OXYGEN_SINK_CHANNEL_MODE_KEY,
    OXYGEN_SINK_CHANNEL_MODE_VALUES,
)
from simulator.furnace_materials import FURNACE_MAX_T_BOUNDS_C
from simulator.optimize.canonical import canonical_json_dumps

KeyPath = tuple[str, ...]

recipe_schema_version = "recipe-schema-v1"
allowlist_version = "allowlist-v8"

FURNACE_MAX_T_C_PATH: KeyPath = ("furnace_max_T_C",)
C5_ALLOW_MRE_VOLTAGE_CAP_PATH: KeyPath = tuple(
    "campaigns.C5.allow_mre_voltage_cap_V".split(".")
)
C5_ALLOW_MRE_VOLTAGE_CAP_UPPER_BOUND_PATH: KeyPath = tuple(
    "campaigns.C5.allow_mre_voltage_cap_upper_bound_V".split(".")
)
DEFAULT_C5_ALLOW_MRE_VOLTAGE_CAP_UPPER_BOUND_V = 2.5


def _c5_allow_mre_voltage_cap_upper_bound() -> float:
    path = DEFAULT_DATA_DIR / "setpoints.yaml"
    try:
        with path.open("r", encoding="utf-8") as handle:
            loaded = yaml.safe_load(handle) or {}
    except OSError:
        return DEFAULT_C5_ALLOW_MRE_VOLTAGE_CAP_UPPER_BOUND_V
    if not isinstance(loaded, Mapping):
        return DEFAULT_C5_ALLOW_MRE_VOLTAGE_CAP_UPPER_BOUND_V
    campaigns = loaded.get("campaigns")
    c5 = campaigns.get("C5") if isinstance(campaigns, Mapping) else None
    if not isinstance(c5, Mapping):
        return DEFAULT_C5_ALLOW_MRE_VOLTAGE_CAP_UPPER_BOUND_V
    raw_bound = c5.get(
        "allow_mre_voltage_cap_upper_bound_V",
        DEFAULT_C5_ALLOW_MRE_VOLTAGE_CAP_UPPER_BOUND_V,
    )
    try:
        bound = float(raw_bound)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "campaigns.C5.allow_mre_voltage_cap_upper_bound_V must be numeric"
        ) from exc
    if not math.isfinite(bound) or bound < 0.0:
        raise ValueError(
            "campaigns.C5.allow_mre_voltage_cap_upper_bound_V must be finite and non-negative"
        )
    return bound

STAGE0_REDOX_OXIDANT_KG_PATH: KeyPath = tuple(
    "campaigns.C0.stage0_redox_cleanup.oxidant_kg".split(".")
)
STAGE0_CARBON_REDUCTANT_KG_PATH: KeyPath = tuple(
    "campaigns.C0.stage0_redox_cleanup.carbon_reductant_kg".split(".")
)
C4_HOLD_TEMP_C_PATH: KeyPath = tuple("campaigns.C4.hold_temp_C".split("."))

C2A_STAGED_STAGES_PATH: KeyPath = tuple("campaigns.C2A_staged.stages".split("."))
C2A_STAGED_MAX_HOLD_HR_PATH: KeyPath = tuple(
    "campaigns.C2A_staged.max_hold_hr".split(".")
)
C2A_STAGED_DEPLETION_FLUX_DECAY_FRACTION_PATH: KeyPath = tuple(
    "campaigns.C2A_staged.depletion_flux_decay_fraction".split(".")
)
C2A_STAGED_DEPLETION_FLUX_DECAY_FRACTION_FLOOR = 0.01
C2A_STAGED_STAGE_NAMES: tuple[str, ...] = (
    "alkali_early_fe",
    "sio_window",
    "fe_hot_hold",
    "cool_for_na_shuttle",
)
C2A_STAGED_STAGE_FIELDS_BY_NAME: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "alkali_early_fe": ("duration_h", "target_C", "ramp_rate_C_per_hr"),
        "sio_window": ("duration_h", "target_C", "ramp_rate_C_per_hr"),
        "fe_hot_hold": ("duration_h", "ramp_rate_C_per_hr"),
        "cool_for_na_shuttle": ("duration_h", "target_C", "ramp_rate_C_per_hr"),
    }
)


class RecipeValidationError(ValueError):
    """Raised when a recipe patch attempts an unsafe or unknown mutation."""


@dataclass(frozen=True)
class KnobSpec:
    path: KeyPath
    kind: Literal["float", "int", "categorical"]
    low: float | None = None
    high: float | None = None
    choices: tuple[str, ...] | None = None
    units: str = ""
    bounds_source: str = ""
    search_enabled: bool = True
    runtime_enabled: bool = True


def _knob(
    path: str,
    kind: Literal["float", "int", "categorical"] = "float",
    *,
    low: float | None = None,
    high: float | None = None,
    choices: tuple[str, ...] | None = None,
    units: str = "",
    bounds_source: str,
    search_enabled: bool = True,
    runtime_enabled: bool = True,
) -> KnobSpec:
    return KnobSpec(
        path=tuple(path.split(".")),
        kind=kind,
        low=low,
        high=high,
        choices=choices,
        units=units,
        bounds_source=bounds_source,
        search_enabled=search_enabled,
        runtime_enabled=runtime_enabled,
    )


class RecipeSchema:
    """Curated allowlist plus global deny prefixes for optimizer recipes."""

    # These whole-value list paths replace YAML ranges such as
    # ``temp_range_C: [lo, hi]``. V1 still forbids list-item paths; future C5
    # endpoint-hours/current knobs should be added here only after R1/R2
    # parameterize them as explicit setpoint inputs.
    NUMERIC_PAIR_VALUE_PATHS: frozenset[KeyPath] = frozenset(
        tuple(path.split("."))
        for path in (
            "campaigns.C0.temp_range_C",
            "campaigns.C0b_p_cleanup.temp_range_C",
            "campaigns.C0b_p_cleanup.pO2_mbar",
            "campaigns.C0b_p_cleanup.duration_h",
            "campaigns.C2A_continuous.temp_range_C",
            "campaigns.C2A_continuous.dT_dt_C_per_hr.early_ramp_1050_1320C",
            "campaigns.C2A_continuous.p_total_mbar",
            "campaigns.C2A_continuous.duration_h",
            "campaigns.C2A_staged.temp_range_C",
            "campaigns.C2A_staged.p_total_mbar",
            "campaigns.C2B.temp_range_C",
            "campaigns.C2B.pO2_mbar",
            "campaigns.C3.K_phase.pO2_bakeout_mbar",
            "campaigns.C3.Na_phase.pO2_bakeout_mbar",
            "campaigns.C3.duration_after_pathA_h",
            "campaigns.C3.duration_after_pathB_h_per_phase",
            "campaigns.C4.temp_range_C",
            "campaigns.C4.pO2_mbar",
            "campaigns.C4.optional_Ca_harvest.pO2_mbar",
            "campaigns.C5.temp_range_C",
            "campaigns.C5.pO2_bar",
            "campaigns.C6.temp_range_C",
            "campaigns.C6.pO2_mbar",
        )
    )
    PRESSURE_TOTAL_DEFAULT_BY_PO2_DEFAULT: Mapping[KeyPath, KeyPath] = MappingProxyType({
        tuple("campaigns.C0b_p_cleanup.pO2_mbar_default".split(".")):
            tuple("campaigns.C0b_p_cleanup.p_total_mbar_default".split(".")),
        tuple("campaigns.C2B.pO2_mbar_default".split(".")):
            tuple("campaigns.C2B.p_total_mbar_default".split(".")),
        tuple("campaigns.C3.pO2_mbar_default".split(".")):
            tuple("campaigns.C3.p_total_mbar_default".split(".")),
        tuple("campaigns.C4.pO2_mbar_default".split(".")):
            tuple("campaigns.C4.p_total_mbar_default".split(".")),
        tuple("campaigns.C5.pO2_mbar_default".split(".")):
            tuple("campaigns.C5.p_total_mbar_default".split(".")),
        tuple("campaigns.C6.pO2_mbar_default".split(".")):
            tuple("campaigns.C6.p_total_mbar_default".split(".")),
    })
    PRESSURE_COUPLED_DEFAULT_PAIRS: tuple[tuple[KeyPath, KeyPath], ...] = tuple(
        PRESSURE_TOTAL_DEFAULT_BY_PO2_DEFAULT.items()
    )

    ALLOWLIST: tuple[KnobSpec, ...] = (
        _knob(
            "furnace_max_T_C",
            low=FURNACE_MAX_T_BOUNDS_C[0],
            high=FURNACE_MAX_T_BOUNDS_C[1],
            units="C",
            bounds_source=(
                "engineering_envelope service-temperature grounding from "
                "docs-private/research/2026-06-18-furnace-max-temp/findings.md"
            ),
            runtime_enabled=True,
        ),
        _knob(
            "campaigns.C0.temp_range_C",
            low=20,
            high=950,
            units="C",
            bounds_source="setpoints:campaigns.C0.temp_range_C",
        ),
        _knob(
            "campaigns.C0.dT_dt_C_per_hr",
            low=10,
            high=100,
            units="C/hr",
            # Wider ramp sweep around 50 C/hr nominal probes throughput vs thermal lag.
            bounds_source=(
                "engineering_envelope around setpoints.yaml nominal "
                "campaigns.C0.dT_dt_C_per_hr=50"
            ),
        ),
        _knob(
            "campaigns.C0.stage0_redox_cleanup.oxidant_kg",
            low=0.0,
            high=250.0,
            units="kg O2",
            bounds_source=(
                "engineering_envelope inert RDX-OPT0 schema placeholder; "
                "RDX-OPT1 defines live bounds"
            ),
            search_enabled=False,
            runtime_enabled=False,
        ),
        _knob(
            "campaigns.C0.stage0_redox_cleanup.carbon_reductant_kg",
            low=0.0,
            high=250.0,
            units="kg C",
            bounds_source=(
                "engineering_envelope inert RDX-OPT0 schema placeholder; "
                "RDX-OPT1 defines live bounds"
            ),
            search_enabled=False,
            runtime_enabled=False,
        ),
        _knob(
            "campaigns.C0b_p_cleanup.temp_range_C",
            low=1180,
            high=1320,
            units="C",
            bounds_source="setpoints:campaigns.C0b_p_cleanup.temp_range_C",
        ),
        _knob(
            "campaigns.C0b_p_cleanup.pO2_mbar",
            low=3.0,
            high=15.0,
            units="mbar",
            bounds_source="setpoints:campaigns.C0b_p_cleanup.pO2_mbar",
        ),
        _knob(
            "campaigns.C0b_p_cleanup.pO2_mbar_default",
            low=3.0,
            high=15.0,
            units="mbar",
            bounds_source="setpoints:campaigns.C0b_p_cleanup.pO2_mbar",
        ),
        _knob(
            "campaigns.C0b_p_cleanup.p_total_mbar_default",
            low=3.0,
            high=15.0,
            units="mbar",
            bounds_source="setpoints:campaigns.C0b_p_cleanup.pO2_mbar",
        ),
        _knob(
            "campaigns.C0b_p_cleanup.duration_h",
            low=0.5,
            high=2.5,
            units="h",
            bounds_source="setpoints:campaigns.C0b_p_cleanup.duration_h",
        ),
        _knob(
            "campaigns.C2A_continuous.temp_range_C",
            low=1050,
            high=1600,
            units="C",
            bounds_source="setpoints:campaigns.C2A_continuous.temp_range_C",
        ),
        _knob(
            "campaigns.C2A_continuous.dT_dt_C_per_hr.early_ramp_1050_1320C",
            low=10,
            high=20,
            units="C/hr",
            bounds_source="setpoints:campaigns.C2A_continuous.dT_dt_C_per_hr.early_ramp_1050_1320C",
        ),
        _knob(
            "campaigns.C2A_continuous.p_total_mbar",
            low=5,
            high=15,
            units="mbar",
            bounds_source="setpoints:campaigns.C2A_continuous.p_total_mbar",
        ),
        _knob(
            "campaigns.C2A_continuous.p_total_mbar_default",
            low=5,
            high=15,
            units="mbar",
            bounds_source="setpoints:campaigns.C2A_continuous.p_total_mbar",
        ),
        _knob(
            "campaigns.C2A_continuous.duration_h",
            low=18,
            high=28,
            units="h",
            bounds_source="setpoints:campaigns.C2A_continuous.duration_h",
        ),
        _knob(
            "campaigns.C2A_staged.temp_range_C",
            low=1250,
            high=1750,
            units="C",
            bounds_source="setpoints:campaigns.C2A_staged.temp_range_C",
        ),
        _knob(
            "campaigns.C2A_staged.default_hold_T_C",
            low=1250,
            high=1750,
            units="C",
            bounds_source="setpoints:campaigns.C2A_staged.temp_range_C",
        ),
        _knob(
            "campaigns.C2A_staged.p_total_mbar",
            low=5,
            high=15,
            units="mbar",
            bounds_source="setpoints:campaigns.C2A_staged.p_total_mbar",
        ),
        _knob(
            "campaigns.C2A_staged.p_total_mbar_default",
            low=5,
            high=15,
            units="mbar",
            bounds_source="setpoints:campaigns.C2A_staged.p_total_mbar",
        ),
        _knob(
            "campaigns.C2A_staged.depletion_flux_decay_fraction",
            low=C2A_STAGED_DEPLETION_FLUX_DECAY_FRACTION_FLOOR,
            high=0.50,
            units="fraction",
            bounds_source=(
                "engineering_envelope: disabled at 0.0; enabled optimizer "
                "range floors positive values to 0.01"
            ),
        ),
        _knob(
            "campaigns.C2A_staged.stages.alkali_early_fe.duration_h",
            "int",
            low=1,
            high=6,
            units="h",
            bounds_source=(
                "engineering_envelope around setpoints.yaml nominal "
                "campaigns.C2A_staged.stages.alkali_early_fe.duration_h=4"
            ),
        ),
        _knob(
            "campaigns.C2A_staged.stages.alkali_early_fe.target_C",
            low=1100,
            high=1320,
            units="C",
            bounds_source=(
                "engineering_envelope around setpoints.yaml nominal "
                "campaigns.C2A_staged.stages.alkali_early_fe.target_C=1250"
            ),
        ),
        _knob(
            "campaigns.C2A_staged.stages.alkali_early_fe.ramp_rate_C_per_hr",
            low=300,
            high=900,
            units="C/hr",
            bounds_source=(
                "engineering_envelope around setpoints.yaml nominal "
                "campaigns.C2A_staged.stages.alkali_early_fe.ramp_rate_C_per_hr=600"
            ),
        ),
        _knob(
            "campaigns.C2A_staged.stages.sio_window.duration_h",
            "int",
            low=1,
            high=6,
            units="h",
            bounds_source=(
                "engineering_envelope around setpoints.yaml nominal "
                "campaigns.C2A_staged.stages.sio_window.duration_h=3"
            ),
        ),
        _knob(
            "campaigns.C2A_staged.stages.sio_window.target_C",
            low=1450,
            high=1650,
            units="C",
            bounds_source=(
                "engineering_envelope around setpoints.yaml nominal "
                "campaigns.C2A_staged.stages.sio_window.target_C=1600"
            ),
        ),
        _knob(
            "campaigns.C2A_staged.stages.sio_window.ramp_rate_C_per_hr",
            low=100,
            high=300,
            units="C/hr",
            bounds_source=(
                "engineering_envelope around setpoints.yaml nominal "
                "campaigns.C2A_staged.stages.sio_window.ramp_rate_C_per_hr=175"
            ),
        ),
        _knob(
            "campaigns.C2A_staged.stages.fe_hot_hold.duration_h",
            "int",
            low=1,
            high=4,
            units="h",
            bounds_source=(
                "engineering_envelope around setpoints.yaml nominal "
                "campaigns.C2A_staged.stages.fe_hot_hold.duration_h=1"
            ),
        ),
        _knob(
            "campaigns.C2A_staged.stages.fe_hot_hold.ramp_rate_C_per_hr",
            low=75,
            high=300,
            units="C/hr",
            bounds_source=(
                "engineering_envelope around setpoints.yaml nominal "
                "campaigns.C2A_staged.stages.fe_hot_hold.ramp_rate_C_per_hr=150"
            ),
        ),
        _knob(
            "campaigns.C2A_staged.stages.cool_for_na_shuttle.duration_h",
            "int",
            low=1,
            high=3,
            units="h",
            bounds_source=(
                "engineering_envelope around setpoints.yaml nominal "
                "campaigns.C2A_staged.stages.cool_for_na_shuttle.duration_h=1"
            ),
        ),
        _knob(
            "campaigns.C2A_staged.stages.cool_for_na_shuttle.target_C",
            low=1050,
            high=1250,
            units="C",
            bounds_source=(
                "engineering_envelope around setpoints.yaml nominal "
                "campaigns.C2A_staged.stages.cool_for_na_shuttle.target_C=1150"
            ),
        ),
        _knob(
            "campaigns.C2A_staged.stages.cool_for_na_shuttle.ramp_rate_C_per_hr",
            low=300,
            high=900,
            units="C/hr",
            bounds_source=(
                "engineering_envelope around setpoints.yaml nominal "
                "campaigns.C2A_staged.stages.cool_for_na_shuttle.ramp_rate_C_per_hr=600"
            ),
        ),
        _knob(
            "campaigns.C2A_staged.na_shuttle_stage.ramp_rate_C_per_hr",
            low=300,
            high=900,
            units="C/hr",
            # Sweep around 600 C/hr nominal spans slower capture and faster Na release.
            bounds_source=(
                "engineering_envelope around setpoints.yaml nominal "
                "campaigns.C2A_staged.na_shuttle_stage.ramp_rate_C_per_hr=600"
            ),
        ),
        _knob(
            "campaigns.C2A_staged.na_shuttle_stage.duration_h",
            low=1,
            high=6,
            units="h",
            # Duration sweep around 3 h nominal covers under/over-hold Na shuttle cases.
            bounds_source=(
                "engineering_envelope around setpoints.yaml nominal "
                "campaigns.C2A_staged.na_shuttle_stage.duration_h=3"
            ),
        ),
        _knob(
            "campaigns.C2B.temp_range_C",
            low=1320,
            high=1480,
            units="C",
            bounds_source="setpoints:campaigns.C2B.temp_range_C",
        ),
        _knob(
            "campaigns.C2B.pO2_mbar",
            low=0.8,
            high=2.3,
            units="mbar",
            bounds_source="setpoints:campaigns.C2B.pO2_mbar",
        ),
        _knob(
            "campaigns.C2B.pO2_mbar_default",
            low=0.8,
            high=2.3,
            units="mbar",
            bounds_source="setpoints:campaigns.C2B.pO2_mbar",
        ),
        _knob(
            "campaigns.C2B.p_total_mbar_default",
            low=0.8,
            high=2.3,
            units="mbar",
            bounds_source="setpoints:campaigns.C2B.pO2_mbar",
        ),
        _knob(
            "campaigns.C3.pO2_mbar_default",
            low=0.5,
            high=1.5,
            units="mbar",
            bounds_source="setpoints:campaigns.C3.K_phase.pO2_bakeout_mbar",
        ),
        _knob(
            "campaigns.C3.p_total_mbar_default",
            low=0.5,
            high=1.5,
            units="mbar",
            bounds_source="setpoints:campaigns.C3.K_phase.pO2_bakeout_mbar",
        ),
        _knob(
            "campaigns.C3.K_phase.pO2_bakeout_mbar",
            low=0.5,
            high=1.5,
            units="mbar",
            bounds_source="setpoints:campaigns.C3.K_phase.pO2_bakeout_mbar",
        ),
        _knob(
            "campaigns.C3.Na_phase.pO2_bakeout_mbar",
            low=0.5,
            high=1.5,
            units="mbar",
            bounds_source="setpoints:campaigns.C3.Na_phase.pO2_bakeout_mbar",
        ),
        _knob(
            "campaigns.C3.endpoint.hold_time_min",
            "int",
            low=15,
            high=60,
            units="min",
            # Endpoint hold sweep around 30 min nominal tests equilibration margin.
            bounds_source=(
                "engineering_envelope around setpoints.yaml nominal "
                "campaigns.C3.endpoint.hold_time_min=30"
            ),
        ),
        _knob(
            "campaigns.C3.alkali_dosing.Na_kg",
            low=0.0,
            high=140.0,
            units="kg",
            bounds_source=(
                "engineering_envelope from disabled=0 plus "
                "setpoints:campaigns.C3.Na_phase.Na_total_kg high bound"
            ),
        ),
        _knob(
            "campaigns.C3.alkali_dosing.K_kg",
            low=0.0,
            high=56.0,
            units="kg",
            bounds_source=(
                "engineering_envelope from setpoints:campaigns.C3."
                "K_phase.K_per_cycle_kg high bound over two cycles"
            ),
        ),
        _knob(
            "campaigns.C3.duration_after_pathA_h",
            low=10,
            high=18,
            units="h",
            bounds_source="setpoints:campaigns.C3.duration_after_pathA_h",
        ),
        _knob(
            "campaigns.C3.duration_after_pathB_h_per_phase",
            low=20,
            high=35,
            units="h",
            bounds_source="setpoints:campaigns.C3.duration_after_pathB_h_per_phase",
        ),
        _knob(
            "campaigns.C4.temp_range_C",
            low=1580,
            high=1670,
            units="C",
            bounds_source="setpoints:campaigns.C4.temp_range_C",
        ),
        _knob(
            "campaigns.C4.hold_temp_C",
            low=1580,
            high=1670,
            units="C",
            bounds_source="setpoints:campaigns.C4.temp_range_C",
            runtime_enabled=False,
        ),
        _knob(
            "campaigns.C4.pO2_mbar",
            low=0.08,
            high=0.35,
            units="mbar",
            bounds_source="setpoints:campaigns.C4.pO2_mbar",
        ),
        _knob(
            "campaigns.C4.pO2_mbar_default",
            low=0.08,
            high=0.35,
            units="mbar",
            bounds_source="setpoints:campaigns.C4.pO2_mbar",
        ),
        _knob(
            "campaigns.C4.p_total_mbar_default",
            low=0.08,
            high=0.35,
            units="mbar",
            bounds_source="setpoints:campaigns.C4.pO2_mbar",
        ),
        _knob(
            "campaigns.C4.optional_Ca_harvest.pO2_mbar",
            low=0.03,
            high=0.12,
            units="mbar",
            bounds_source="setpoints:campaigns.C4.optional_Ca_harvest.pO2_mbar",
        ),
        _knob(
            "campaigns.C5.temp_range_C",
            low=1500,
            high=1650,
            units="C",
            bounds_source="setpoints:campaigns.C5.temp_range_C",
        ),
        _knob(
            "campaigns.C5.pO2_bar",
            low=0.01,
            high=0.1,
            units="bar",
            bounds_source="setpoints:campaigns.C5.pO2_bar",
        ),
        _knob(
            "campaigns.C5.pO2_mbar_default",
            low=10,
            high=100,
            units="mbar",
            # Default mbar sweep is the C5 bar range converted for runner setpoints.
            bounds_source=(
                "engineering_envelope converted from setpoints.yaml range "
                "campaigns.C5.pO2_bar=[0.01, 0.1] bar to mbar default"
            ),
        ),
        _knob(
            "campaigns.C5.p_total_mbar_default",
            low=10,
            high=100,
            units="mbar",
            # Total-pressure default follows the converted C5 pO2 bar sweep.
            bounds_source=(
                "engineering_envelope converted from setpoints.yaml range "
                "campaigns.C5.pO2_bar=[0.01, 0.1] bar to total mbar default"
            ),
        ),
        _knob(
            "campaigns.C5.allow_mre_voltage_cap_V",
            low=0.0,
            high=_c5_allow_mre_voltage_cap_upper_bound(),
            units="V",
            bounds_source=(
                "engineering_envelope owner-settable via setpoints.yaml "
                "campaigns.C5.allow_mre_voltage_cap_upper_bound_V; "
                "0 disables C5/MRE, positive enables C5 and sets "
                "EvalSpec.mre_max_voltage_V"
            ),
            runtime_enabled=False,
        ),
        _knob(
            "campaigns.C5.branch_two.max_voltage_V",
            low=0.0,
            high=2.5,
            units="V",
            bounds_source=(
                "engineering_envelope branch hardware ceiling, demoted from "
                "primary optimizer search; user gate is "
                "campaigns.C5.allow_mre_voltage_cap_V"
            ),
            search_enabled=False,
        ),
        _knob(
            "campaigns.C5.branch_one.max_voltage_V",
            low=0.0,
            high=2.5,
            units="V",
            bounds_source=(
                "engineering_envelope branch hardware ceiling, demoted from "
                "primary optimizer search; user gate is "
                "campaigns.C5.allow_mre_voltage_cap_V"
            ),
            search_enabled=False,
        ),
        _knob(
            "campaigns.C6.temp_range_C",
            low=1450,
            high=1550,
            units="C",
            bounds_source="setpoints:campaigns.C6.temp_range_C",
        ),
        _knob(
            "campaigns.C6.default_hold_T_C",
            low=1450,
            high=1550,
            units="C",
            bounds_source="setpoints:campaigns.C6.temp_range_C",
        ),
        _knob(
            "campaigns.C6.pO2_mbar",
            low=0.08,
            high=0.35,
            units="mbar",
            bounds_source="setpoints:campaigns.C6.pO2_mbar",
        ),
        _knob(
            "campaigns.C6.pO2_mbar_default",
            low=0.08,
            high=0.35,
            units="mbar",
            bounds_source="setpoints:campaigns.C6.pO2_mbar",
        ),
        _knob(
            "campaigns.C6.p_total_mbar_default",
            low=0.08,
            high=0.35,
            units="mbar",
            bounds_source="setpoints:campaigns.C6.pO2_mbar",
        ),
        _knob(
            f"chemistry_kernel.{OXYGEN_SINK_CHANNEL_MODE_KEY}",
            "categorical",
            choices=OXYGEN_SINK_CHANNEL_MODE_VALUES,
            units="diagnostic",
            bounds_source=(
                "engineering_envelope diagnostic Robinot oxygen-sink "
                "channel annotation; no behavior authority"
            ),
        ),
        _knob(
            "overhead_headspace.temperature_offset_K",
            low=-500,
            high=500,
            units="K",
            bounds_source=(
                "engineering_envelope around setpoints.yaml "
                "overhead_headspace.temperature_offset_K"
            ),
        ),
        _knob(
            "overhead_headspace.liner_temperature_C.default_C",
            low=20,
            high=2000,
            units="C",
            bounds_source=(
                "engineering_envelope around setpoints.yaml "
                "overhead_headspace.liner_temperature_C.default_C"
            ),
        ),
        _knob(
            "overhead_headspace.pipe_segment_temperatures_C.default_C",
            low=20,
            high=2000,
            units="C",
            bounds_source=(
                "engineering_envelope around setpoints.yaml "
                "overhead_headspace.pipe_segment_temperatures_C.default_C"
            ),
        ),
        _knob(
            "overhead_headspace.pipe_segment_temperatures_C.segments.stage_0_to_stage_1.default_C",
            low=20,
            high=2000,
            units="C",
            bounds_source=(
                "engineering_envelope around setpoints.yaml "
                "overhead_headspace.pipe_segment_temperatures_C.segments."
                "stage_0_to_stage_1.default_C"
            ),
        ),
        _knob(
            "overhead_headspace.pipe_segment_temperatures_C.segments.stage_1_to_stage_2.default_C",
            low=20,
            high=2000,
            units="C",
            bounds_source=(
                "engineering_envelope around setpoints.yaml "
                "overhead_headspace.pipe_segment_temperatures_C.segments."
                "stage_1_to_stage_2.default_C"
            ),
        ),
        _knob(
            "overhead_headspace.pipe_segment_temperatures_C.segments.stage_2_to_stage_3.default_C",
            low=20,
            high=2000,
            units="C",
            bounds_source=(
                "engineering_envelope around setpoints.yaml "
                "overhead_headspace.pipe_segment_temperatures_C.segments."
                "stage_2_to_stage_3.default_C"
            ),
        ),
        _knob(
            "overhead_headspace.pipe_segment_temperatures_C.segments.stage_3_to_stage_4.default_C",
            low=20,
            high=2000,
            units="C",
            bounds_source=(
                "engineering_envelope around setpoints.yaml "
                "overhead_headspace.pipe_segment_temperatures_C.segments."
                "stage_3_to_stage_4.default_C"
            ),
        ),
        _knob(
            "overhead_headspace.pipe_segment_temperatures_C.segments.stage_4_to_stage_5.default_C",
            low=20,
            high=2000,
            units="C",
            bounds_source=(
                "engineering_envelope around setpoints.yaml "
                "overhead_headspace.pipe_segment_temperatures_C.segments."
                "stage_4_to_stage_5.default_C"
            ),
        ),
        _knob(
            "overhead_headspace.pipe_segment_temperatures_C.segments.stage_5_to_stage_6.default_C",
            low=20,
            high=2000,
            units="C",
            bounds_source=(
                "engineering_envelope around setpoints.yaml "
                "overhead_headspace.pipe_segment_temperatures_C.segments."
                "stage_5_to_stage_6.default_C"
            ),
        ),
        _knob(
            "overhead_headspace.pipe_segment_temperatures_C.segments.stage_6_to_stage_7.default_C",
            low=20,
            high=2000,
            units="C",
            bounds_source=(
                "engineering_envelope around setpoints.yaml "
                "overhead_headspace.pipe_segment_temperatures_C.segments."
                "stage_6_to_stage_7.default_C"
            ),
        ),
    )

    FORBIDDEN_PREFIXES: tuple[str, ...] = (
        "chemistry_kernel",
        "mass_balance",
        "kinetic_parameters",
        "constants",
        "safety",
        "safety_ceilings",
        "*.products",
        "*.mass_balance",
        "*.kinetic_parameters",
        "*.constants",
        "*.safety",
        "*.safety_ceilings",
    )
    FORBIDDEN_EXACT_PATH_EXCEPTIONS: frozenset[KeyPath] = frozenset(
        {("chemistry_kernel", OXYGEN_SINK_CHANNEL_MODE_KEY)}
    )

    recipe_schema_version = recipe_schema_version
    allowlist_version = allowlist_version

    def __init__(
        self,
        allowlist: tuple[KnobSpec, ...] | None = None,
        *,
        forbidden_prefixes: tuple[str, ...] | None = None,
        recipe_schema_version: str | None = None,
        allowlist_version: str | None = None,
    ) -> None:
        # Treat the class allowlist as a template even when passed explicitly, so
        # source-fingerprinted bounds (notably C5 MRE cap) refresh on use.
        self.allowlist = (
            allowlist
            if allowlist is not None and allowlist != type(self).ALLOWLIST
            else _default_allowlist_for_source(type(self).ALLOWLIST)
        )
        # FORBIDDEN_PREFIXES is an INVIOLABLE floor: a caller may ADD deny
        # prefixes but can never remove the class-level set. Otherwise a custom
        # schema (e.g. RecipeSchema(forbidden_prefixes=())) passed to
        # RecipePatch.validated() could neuter the safety boundary and validate a
        # *.products / chemistry_kernel path.
        extra_forbidden = tuple(forbidden_prefixes or ())
        self.forbidden_prefixes = tuple(
            dict.fromkeys(type(self).FORBIDDEN_PREFIXES + extra_forbidden)
        )
        self.recipe_schema_version = (
            recipe_schema_version
            if recipe_schema_version is not None
            else type(self).recipe_schema_version
        )
        self.allowlist_version = (
            allowlist_version
            if allowlist_version is not None
            else type(self).allowlist_version
        )
        self._spec_by_path = {spec.path: spec for spec in self.allowlist}

    @property
    def search_allowlist(self) -> tuple[KnobSpec, ...]:
        return tuple(spec for spec in self.allowlist if spec.search_enabled)

    def spec_for(self, path: KeyPath) -> KnobSpec:
        normalized = _normalize_key_path(path)
        if self.is_forbidden(normalized):
            raise RecipeValidationError(
                f"forbidden recipe path: {_format_path(normalized)}"
            )
        try:
            return self._spec_by_path[normalized]
        except KeyError as exc:
            raise RecipeValidationError(
                f"unknown recipe path: {_format_path(normalized)}"
            ) from exc

    def is_forbidden(self, path: KeyPath) -> bool:
        if path in self.FORBIDDEN_EXACT_PATH_EXCEPTIONS:
            return False
        dotted_prefixes = _dotted_prefixes(path)
        return any(
            fnmatchcase(prefix, pattern)
            for pattern in self.forbidden_prefixes
            for prefix in dotted_prefixes
        )

    def to_setpoints_patch(self, patch: "RecipePatch") -> dict[str, Any]:
        """Validate then render the optimizer-facing setpoints patch."""
        validated = patch.validated(self)
        runtime_values = {
            path: value
            for path, value in validated.values.items()
            if self.spec_for(path).runtime_enabled
        }
        return _setpoints_patch_from_runtime_values(runtime_values)

    def redox_cleanup_doses_kg(self, patch: "RecipePatch") -> tuple[float, float]:
        validated = patch.validated(self)
        return (
            float(validated.values.get(STAGE0_REDOX_OXIDANT_KG_PATH, 0.0) or 0.0),
            float(
                validated.values.get(STAGE0_CARBON_REDUCTANT_KG_PATH, 0.0)
                or 0.0
            ),
        )


MANDATE_LEVER_PATHS: frozenset[KeyPath] = frozenset(
    tuple(path.split("."))
    for path in (
        "furnace_max_T_C",
        "campaigns.C0.temp_range_C",
        "campaigns.C0.dT_dt_C_per_hr",
        "campaigns.C0b_p_cleanup.temp_range_C",
        "campaigns.C0b_p_cleanup.pO2_mbar",
        "campaigns.C0b_p_cleanup.pO2_mbar_default",
        "campaigns.C0b_p_cleanup.p_total_mbar_default",
        "campaigns.C0b_p_cleanup.duration_h",
        "campaigns.C2A_continuous.temp_range_C",
        "campaigns.C2A_continuous.dT_dt_C_per_hr.early_ramp_1050_1320C",
        "campaigns.C2A_continuous.p_total_mbar",
        "campaigns.C2A_continuous.p_total_mbar_default",
        "campaigns.C2A_continuous.duration_h",
        "campaigns.C2A_staged.temp_range_C",
        "campaigns.C2A_staged.default_hold_T_C",
        "campaigns.C2A_staged.p_total_mbar",
        "campaigns.C2A_staged.p_total_mbar_default",
        "campaigns.C2A_staged.depletion_flux_decay_fraction",
        "campaigns.C2A_staged.stages.alkali_early_fe.duration_h",
        "campaigns.C2A_staged.stages.alkali_early_fe.target_C",
        "campaigns.C2A_staged.stages.alkali_early_fe.ramp_rate_C_per_hr",
        "campaigns.C2A_staged.stages.sio_window.duration_h",
        "campaigns.C2A_staged.stages.sio_window.target_C",
        "campaigns.C2A_staged.stages.sio_window.ramp_rate_C_per_hr",
        "campaigns.C2A_staged.stages.fe_hot_hold.duration_h",
        "campaigns.C2A_staged.stages.fe_hot_hold.ramp_rate_C_per_hr",
        "campaigns.C2A_staged.stages.cool_for_na_shuttle.duration_h",
        "campaigns.C2A_staged.stages.cool_for_na_shuttle.target_C",
        "campaigns.C2A_staged.stages.cool_for_na_shuttle.ramp_rate_C_per_hr",
        "campaigns.C2A_staged.na_shuttle_stage.ramp_rate_C_per_hr",
        "campaigns.C2A_staged.na_shuttle_stage.duration_h",
        "campaigns.C2B.temp_range_C",
        "campaigns.C2B.pO2_mbar",
        "campaigns.C2B.pO2_mbar_default",
        "campaigns.C2B.p_total_mbar_default",
        "campaigns.C3.pO2_mbar_default",
        "campaigns.C3.p_total_mbar_default",
        "campaigns.C3.K_phase.pO2_bakeout_mbar",
        "campaigns.C3.Na_phase.pO2_bakeout_mbar",
        "campaigns.C3.endpoint.hold_time_min",
        "campaigns.C3.alkali_dosing.Na_kg",
        "campaigns.C3.alkali_dosing.K_kg",
        "campaigns.C3.duration_after_pathA_h",
        "campaigns.C3.duration_after_pathB_h_per_phase",
        "campaigns.C4.temp_range_C",
        "campaigns.C4.hold_temp_C",
        "campaigns.C4.pO2_mbar",
        "campaigns.C4.pO2_mbar_default",
        "campaigns.C4.p_total_mbar_default",
        "campaigns.C4.optional_Ca_harvest.pO2_mbar",
        "campaigns.C5.temp_range_C",
        "campaigns.C5.pO2_bar",
        "campaigns.C5.pO2_mbar_default",
        "campaigns.C5.p_total_mbar_default",
        "campaigns.C5.allow_mre_voltage_cap_V",
        "campaigns.C6.temp_range_C",
        "campaigns.C6.default_hold_T_C",
        "campaigns.C6.pO2_mbar",
        "campaigns.C6.pO2_mbar_default",
        "campaigns.C6.p_total_mbar_default",
        "overhead_headspace.temperature_offset_K",
        "overhead_headspace.liner_temperature_C.default_C",
        "overhead_headspace.pipe_segment_temperatures_C.default_C",
        "overhead_headspace.pipe_segment_temperatures_C.segments.stage_0_to_stage_1.default_C",
        "overhead_headspace.pipe_segment_temperatures_C.segments.stage_1_to_stage_2.default_C",
        "overhead_headspace.pipe_segment_temperatures_C.segments.stage_2_to_stage_3.default_C",
        "overhead_headspace.pipe_segment_temperatures_C.segments.stage_3_to_stage_4.default_C",
        "overhead_headspace.pipe_segment_temperatures_C.segments.stage_4_to_stage_5.default_C",
        "overhead_headspace.pipe_segment_temperatures_C.segments.stage_5_to_stage_6.default_C",
        "overhead_headspace.pipe_segment_temperatures_C.segments.stage_6_to_stage_7.default_C",
    )
)

MANDATE_LEVER_ALLOWLIST: tuple[KnobSpec, ...] = tuple(
    spec for spec in RecipeSchema.ALLOWLIST if spec.path in MANDATE_LEVER_PATHS
)


def _is_c2a_staged_stage_field_path(path: KeyPath) -> bool:
    return (
        len(path) == len(C2A_STAGED_STAGES_PATH) + 2
        and path[: len(C2A_STAGED_STAGES_PATH)] == C2A_STAGED_STAGES_PATH
        and path[-2] in C2A_STAGED_STAGE_NAMES
        and path[-1] in C2A_STAGED_STAGE_FIELDS_BY_NAME[path[-2]]
    )


def _setpoints_patch_from_runtime_values(
    runtime_values: Mapping[KeyPath, Any],
) -> dict[str, Any]:
    direct_values = {
        path: value
        for path, value in runtime_values.items()
        if not _is_c2a_staged_stage_field_path(path)
    }
    stage_values = {
        path: value
        for path, value in runtime_values.items()
        if _is_c2a_staged_stage_field_path(path)
    }
    nested = RecipePatch(direct_values).to_nested()
    if not stage_values:
        return nested

    stages = _default_c2a_staged_stages()
    stages_by_name = {
        str(stage.get("name")): stage
        for stage in stages
        if isinstance(stage, dict) and stage.get("name") is not None
    }
    for path, value in stage_values.items():
        stage_name = path[-2]
        field_name = path[-1]
        try:
            stage = stages_by_name[stage_name]
        except KeyError as exc:
            raise RecipeValidationError(
                f"missing default C2A_staged stage: {stage_name}"
            ) from exc
        stage[field_name] = _normalize_value(value)

    total_hours = 0
    for stage in stages:
        if isinstance(stage, dict):
            total_hours += max(1, int(float(stage.get("duration_h", 1.0))))

    campaigns = nested.setdefault("campaigns", {})
    if not isinstance(campaigns, dict):
        raise RecipeValidationError("recipe path conflicts with campaigns mapping")
    c2a = campaigns.setdefault("C2A_staged", {})
    if not isinstance(c2a, dict):
        raise RecipeValidationError("recipe path conflicts with C2A_staged mapping")
    c2a["stages"] = stages
    c2a["max_hold_hr"] = total_hours
    return nested


def _default_c2a_staged_stages() -> list[dict[str, Any]]:
    node: Any = _default_setpoints()
    for segment in C2A_STAGED_STAGES_PATH:
        if not isinstance(node, Mapping) or segment not in node:
            raise RecipeValidationError(
                f"missing YAML default for {_format_path(C2A_STAGED_STAGES_PATH)}"
            )
        node = node[segment]
    if not isinstance(node, list):
        raise RecipeValidationError(
            f"{_format_path(C2A_STAGED_STAGES_PATH)} must be a YAML list"
        )
    stages = copy.deepcopy(node)
    seen: set[str] = set()
    for stage in stages:
        if not isinstance(stage, dict) or not isinstance(stage.get("name"), str):
            raise RecipeValidationError("C2A_staged stages must be named mappings")
        seen.add(stage["name"])
    missing = set(C2A_STAGED_STAGE_NAMES) - seen
    if missing:
        raise RecipeValidationError(
            "missing default C2A_staged stage: " + ", ".join(sorted(missing))
        )
    return stages


def _setpoints_source_fingerprint() -> tuple[str, int | None, int | None, str]:
    path = DEFAULT_DATA_DIR / "setpoints.yaml"
    try:
        stat_result = path.stat()
        contents = path.read_bytes()
    except OSError:
        return (str(path), None, None, "missing")
    digest = hashlib.sha256(contents).hexdigest()
    return (str(path), stat_result.st_mtime_ns, stat_result.st_size, digest)


def _default_allowlist_for_source(
    template_allowlist: tuple[KnobSpec, ...],
) -> tuple[KnobSpec, ...]:
    return _cached_default_allowlist(
        _setpoints_source_fingerprint(),
        template_allowlist,
    )


@lru_cache(maxsize=8)
def _cached_default_allowlist(
    _source_fingerprint: tuple[str, int | None, int | None, str],
    template_allowlist: tuple[KnobSpec, ...],
) -> tuple[KnobSpec, ...]:
    c5_cap_high = _c5_allow_mre_voltage_cap_upper_bound()
    return tuple(
        replace(spec, high=c5_cap_high)
        if spec.path == C5_ALLOW_MRE_VOLTAGE_CAP_PATH
        else spec
        for spec in template_allowlist
    )


@dataclass(frozen=True)
class RecipePatch:
    values: Mapping[KeyPath, Any]

    def __post_init__(self) -> None:
        normalized = {
            _normalize_key_path(path): _normalize_value(value)
            for path, value in self.values.items()
        }
        object.__setattr__(self, "values", MappingProxyType(normalized))

    def __reduce__(self) -> tuple[Any, tuple[dict[KeyPath, Any]]]:
        return (type(self), (dict(self.values),))

    @classmethod
    def from_nested(cls, nested: Mapping[str, Any]) -> "RecipePatch":
        if not isinstance(nested, Mapping):
            raise RecipeValidationError("recipe patch must be a nested mapping")
        if not nested:
            return cls({})

        flat: dict[KeyPath, Any] = {}

        def walk(prefix: KeyPath, node: Any) -> None:
            if prefix == C2A_STAGED_MAX_HOLD_HR_PATH:
                return
            if prefix == C2A_STAGED_STAGES_PATH and isinstance(node, list):
                _flatten_c2a_staged_stage_list(flat, node)
                return
            if isinstance(node, Mapping):
                if not node:
                    raise RecipeValidationError(
                        f"empty nested recipe branch: {_format_path(prefix)}"
                    )
                for key, value in node.items():
                    if not isinstance(key, str):
                        raise RecipeValidationError(
                            "recipe patch nested keys must be strings"
                        )
                    walk(prefix + (key,), value)
                return
            if not prefix:
                raise RecipeValidationError("recipe patch path cannot be empty")
            flat[prefix] = _normalize_value(node)

        walk((), nested)
        return cls(flat)

    def to_nested(self) -> dict[str, Any]:
        """Render values only; UNVALIDATED. Use RecipeSchema.to_setpoints_patch()."""
        nested: dict[str, Any] = {}
        for path, value in sorted(self.values.items()):
            cursor: dict[str, Any] = nested
            for segment in path[:-1]:
                existing = cursor.setdefault(segment, {})
                if not isinstance(existing, dict):
                    raise RecipeValidationError(
                        f"recipe path conflicts with scalar: {_format_path(path)}"
                    )
                cursor = existing
            leaf = path[-1]
            if leaf in cursor:
                raise RecipeValidationError(
                    f"duplicate recipe path: {_format_path(path)}"
                )
            cursor[leaf] = _normalize_value(value)
        return nested

    def validated(self, schema: RecipeSchema | None = None) -> "RecipePatch":
        active_schema = schema or RecipeSchema()
        for path, value in self.values.items():
            spec = active_schema.spec_for(path)
            _validate_value(spec, value, active_schema)
        _validate_pressure_default_pairs(active_schema, self.values)
        return RecipePatch(dict(self.values))

    def recipe_id(
        self,
        schema: RecipeSchema | None = None,
        *,
        recipe_schema_version: str | None = None,
        allowlist_version: str | None = None,
    ) -> str:
        active_schema = schema or RecipeSchema()
        schema_version = recipe_schema_version or active_schema.recipe_schema_version
        active_allowlist_version = allowlist_version or active_schema.allowlist_version
        canonical = self.canonical_json().encode("utf-8")
        payload = (
            canonical
            + b"\n"
            + schema_version.encode("utf-8")
            + b"\n"
            + active_allowlist_version.encode("utf-8")
        )
        return hashlib.sha256(payload).hexdigest()

    def canonical_json(self) -> str:
        entries = [
            {"path": list(path), "value": _normalize_value(value)}
            for path, value in sorted(self.values.items())
        ]
        return canonical_json_dumps(entries)


def _normalize_key_path(path: Any) -> KeyPath:
    if not isinstance(path, tuple) or not path:
        raise RecipeValidationError("recipe paths must be non-empty KeyPath tuples")
    if not all(isinstance(segment, str) and segment for segment in path):
        raise RecipeValidationError("recipe path segments must be non-empty strings")
    # Segments must be atomic: "." is the path separator. A segment that embeds a
    # "." (e.g. "products.oxygen_kg") would defeat dotted-prefix forbidden
    # matching -- the joined string would not end in ".products", slipping past
    # the "*.products" deny pattern.
    if any("." in segment for segment in path):
        raise RecipeValidationError(
            "recipe path segments must not contain '.' (the path separator)"
        )
    return path


def _flatten_c2a_staged_stage_list(
    flat: dict[KeyPath, Any],
    stages: list[Any],
) -> None:
    seen: set[str] = set()
    for stage in stages:
        if not isinstance(stage, Mapping) or not isinstance(stage.get("name"), str):
            raise RecipeValidationError("C2A_staged stages must be named mappings")
        stage_name = str(stage["name"])
        if stage_name not in C2A_STAGED_STAGE_NAMES:
            raise RecipeValidationError(f"unknown C2A_staged stage: {stage_name}")
        if stage_name in seen:
            raise RecipeValidationError(f"duplicate C2A_staged stage: {stage_name}")
        seen.add(stage_name)
        for field_name in C2A_STAGED_STAGE_FIELDS_BY_NAME[stage_name]:
            if field_name in stage:
                flat[C2A_STAGED_STAGES_PATH + (stage_name, field_name)] = (
                    _normalize_value(stage[field_name])
                )


def _normalize_value(value: Any) -> Any:
    if isinstance(value, float):
        if not math.isfinite(value):
            raise RecipeValidationError("recipe values must not be NaN or infinite")
        return value
    if isinstance(value, list):
        return [_normalize_value(item) for item in value]
    if isinstance(value, tuple):
        return [_normalize_value(item) for item in value]
    if isinstance(value, Mapping):
        raise RecipeValidationError("recipe values must be scalars or lists")
    return value


def _validate_pressure_default_pairs(
    schema: RecipeSchema,
    values: Mapping[KeyPath, Any],
) -> None:
    for po2_path, total_path in schema.PRESSURE_COUPLED_DEFAULT_PAIRS:
        if (
            po2_path not in schema._spec_by_path
            or total_path not in schema._spec_by_path
            or (po2_path not in values and total_path not in values)
        ):
            continue
        if po2_path in values:
            po2 = float(values[po2_path])
            po2_source = "patched"
        else:
            po2 = float(_default_setpoint_value(po2_path))
            po2_source = "YAML default"
        if total_path in values:
            total = float(values[total_path])
            total_source = "patched"
        else:
            total = float(_default_setpoint_value(total_path))
            total_source = "YAML default"
        tolerance = max(1e-12, 1e-12 * max(1.0, abs(po2), abs(total)))
        if po2 - total > tolerance:
            raise RecipeValidationError(
                "recipe_pressure_partial_exceeds_total: "
                f"{_format_path(po2_path)}={po2:.12g} ({po2_source}) > "
                f"{_format_path(total_path)}={total:.12g} ({total_source}); "
                "oxygen partial pressure cannot exceed total pressure; "
                "set both pO2 and p_total knobs for this campaign"
            )


@lru_cache(maxsize=1)
def _default_setpoints() -> Mapping[str, Any]:
    path = DEFAULT_DATA_DIR / "setpoints.yaml"
    with path.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle) or {}
    if not isinstance(loaded, Mapping):
        raise RecipeValidationError(
            f"recipe default setpoints must be a mapping: {path}"
        )
    return loaded


def _default_setpoint_value(path: KeyPath) -> Any:
    node: Any = _default_setpoints()
    for segment in path:
        if not isinstance(node, Mapping) or segment not in node:
            raise RecipeValidationError(
                "recipe_pressure_total_default_missing: "
                f"missing YAML default for {_format_path(path)}"
            )
        node = node[segment]
    return node


def _validate_value(spec: KnobSpec, value: Any, schema: RecipeSchema) -> None:
    if spec.kind == "categorical":
        if not isinstance(value, str):
            raise RecipeValidationError(
                f"{_format_path(spec.path)} requires categorical string value"
            )
        if spec.choices is None or value not in spec.choices:
            raise RecipeValidationError(
                f"{_format_path(spec.path)} value {value!r} not in choices"
            )
        return
    if spec.kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise RecipeValidationError(f"{_format_path(spec.path)} requires int value")
        _validate_numeric_bounds(spec, float(value))
        return
    if spec.kind == "float":
        if isinstance(value, list):
            if spec.path not in schema.NUMERIC_PAIR_VALUE_PATHS:
                raise RecipeValidationError(
                    f"{_format_path(spec.path)} requires scalar float value"
                )
            if len(value) != 2:
                raise RecipeValidationError(
                    f"{_format_path(spec.path)} requires [low, high] pair"
                )
            numeric_values = [_coerce_float(spec, item) for item in value]
            if numeric_values[0] > numeric_values[1]:
                raise RecipeValidationError(
                    f"{_format_path(spec.path)} range low exceeds high"
                )
            for item in numeric_values:
                _validate_numeric_bounds(spec, item)
            return
        numeric_value = _coerce_float(spec, value)
        if (
            spec.path == C2A_STAGED_DEPLETION_FLUX_DECAY_FRACTION_PATH
            and 0.0 <= numeric_value < C2A_STAGED_DEPLETION_FLUX_DECAY_FRACTION_FLOOR
        ):
            return
        _validate_numeric_bounds(spec, numeric_value)
        return
    raise RecipeValidationError(f"{_format_path(spec.path)} has unknown kind")


def _coerce_float(spec: KnobSpec, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RecipeValidationError(f"{_format_path(spec.path)} requires float value")
    result = float(value)
    if not math.isfinite(result):
        raise RecipeValidationError(
            f"{_format_path(spec.path)} must not be NaN or infinite"
        )
    return result


def _validate_numeric_bounds(spec: KnobSpec, value: float) -> None:
    if spec.low is not None and value < spec.low:
        raise RecipeValidationError(
            f"{_format_path(spec.path)} value {value!r} below lower bound {spec.low!r}"
        )
    if spec.high is not None and value > spec.high:
        raise RecipeValidationError(
            f"{_format_path(spec.path)} value {value!r} above upper bound {spec.high!r}"
        )


def _dotted_prefixes(path: KeyPath) -> tuple[str, ...]:
    return tuple(".".join(path[:idx]) for idx in range(1, len(path) + 1))


def _format_path(path: KeyPath) -> str:
    return ".".join(path)
