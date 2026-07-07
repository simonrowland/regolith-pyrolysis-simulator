"""Deny-by-default recipe schema for optimizer-facing setpoint patches."""

from __future__ import annotations

import copy
from dataclasses import dataclass, replace
from functools import lru_cache
import hashlib
import math
from fnmatch import fnmatchcase
from types import MappingProxyType
from typing import Any, Literal, Mapping, Sequence
import warnings

import yaml

from simulator.config import DEFAULT_DATA_DIR
from simulator.chemistry.kernel.config import (
    OXYGEN_SINK_CHANNEL_MODE_KEY,
    OXYGEN_SINK_CHANNEL_MODE_VALUES,
)
from simulator.furnace_materials import FURNACE_MAX_T_BOUNDS_C
from simulator.optimize.canonical import canonical_json_dumps
from simulator.state import CondensationTrain

KeyPath = tuple[str, ...]

recipe_schema_version = "recipe-schema-v1"
allowlist_version = "allowlist-v11"
O2_BUBBLER_NEUTRAL_ALLOWLIST_VERSION = "allowlist-v11"
O2_BUBBLER_DEFAULT_ETA_ABSORB = 0.75

# Hot-wall bounds: mandate invariant keeps ducts upstream of the designated
# condenser above ~1400 C; Stage 0 setpoints cap the Doloma-REE hot duct at
# 1750 C. Source sidecar: data/setpoints.yaml condensation_train.metals_train
# stage_0_hot_duct temp_range_C [1400,1600], max_service_T_C 1750; material
# support: data/wall_materials.yaml doloma service_temp direct 1700 C
# (W:S7/W:S8) plus magnesia 1600-1800 C (W:S6).
OVERHEAD_HOT_WALL_MIN_C = 1400.0
OVERHEAD_HOT_WALL_MAX_C = 1750.0
OVERHEAD_HEADSPACE_OFFSET_MIN_K = -200.0
OVERHEAD_HEADSPACE_OFFSET_MAX_K = 0.0
OVERHEAD_HOT_WALL_BOUNDS_SOURCE = (
    "hot_wall_invariant: docs/concepts.md Hot walls section; "
    "data/setpoints.yaml condensation_train.metals_train.stage_0_hot_duct "
    "temp_range_C=[1400,1600], max_service_T_C=1750; "
    "data/wall_materials.yaml doloma W:S7/W:S8 direct service 1700 C"
)
OVERHEAD_HEADSPACE_OFFSET_BOUNDS_SOURCE = (
    "hot_wall_invariant: C2A_continuous peak SiO window 1600 C minus "
    "Stage 0 hot-wall floor 1400 C => offset >= -200 K; gas not hotter "
    "than melt without an explicit wall-heat model"
)
DOWNSTREAM_CONDENSATION_STAGE_PAIRS: tuple[tuple[str, int, int], ...] = (
    ("stage_4_to_stage_5", 4, 5),
    ("stage_5_to_stage_6", 5, 6),
    ("stage_6_to_stage_7", 6, 7),
)


def _condensation_train_downstream_segment_bounds() -> Mapping[str, tuple[float, float]]:
    default_train = CondensationTrain.create_default()
    stage_ranges = {
        stage.stage_number: (
            float(stage.temp_range_C[0]),
            float(stage.temp_range_C[1]),
        )
        for stage in default_train.stages
    }
    bounds: dict[str, tuple[float, float]] = {}
    for segment_name, upstream_stage, downstream_stage in DOWNSTREAM_CONDENSATION_STAGE_PAIRS:
        if upstream_stage not in stage_ranges or downstream_stage not in stage_ranges:
            raise RuntimeError(
                "CondensationTrain.create_default() missing stages needed for "
                f"{segment_name}"
            )
        low = stage_ranges[downstream_stage][0]
        high = stage_ranges[upstream_stage][1]
        if not (math.isfinite(low) and math.isfinite(high) and low <= high):
            raise RuntimeError(
                "CondensationTrain.create_default() produced invalid interface "
                f"bounds for {segment_name}: {(low, high)!r}"
            )
        bounds[segment_name] = (low, high)
    return MappingProxyType(bounds)


OVERHEAD_DOWNSTREAM_SEGMENT_BOUNDS_C: Mapping[str, tuple[float, float]] = (
    _condensation_train_downstream_segment_bounds()
)

FURNACE_MAX_T_C_PATH: KeyPath = ("furnace_max_T_C",)
C5_ALLOW_MRE_VOLTAGE_CAP_PATH: KeyPath = tuple(
    "campaigns.C5.allow_mre_voltage_cap_V".split(".")
)
C5_ALLOW_MRE_VOLTAGE_CAP_UPPER_BOUND_PATH: KeyPath = tuple(
    "campaigns.C5.allow_mre_voltage_cap_upper_bound_V".split(".")
)
DEFAULT_C5_ALLOW_MRE_VOLTAGE_CAP_UPPER_BOUND_V = 2.5
BOUNDS_DIGEST_SCHEMA_VERSION = "optimizer-bounds-digest-v1"


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

O2_BUBBLER_RATE_KEY = "o2_bubbler_kg_per_hr"
O2_BUBBLER_ETA_ABSORB_DEFAULT_PATH: KeyPath = ("o2_bubbler_eta_absorb_default",)
O2_BUBBLER_TARGET_FO2_LOG_PATH: KeyPath = ("o2_bubbler_target_fO2_log",)
C2A_STAGED_STAGES_PATH: KeyPath = tuple("campaigns.C2A_staged.stages".split("."))
C2A_STAGED_ORDER_PATH: KeyPath = tuple("campaigns.C2A_staged.order".split("."))
C2A_STAGED_PO2_MBAR_DEFAULT_PATH: KeyPath = tuple(
    "campaigns.C2A_staged.pO2_mbar_default".split(".")
)
C2A_STAGED_P_TOTAL_MBAR_DEFAULT_PATH: KeyPath = tuple(
    "campaigns.C2A_staged.p_total_mbar_default".split(".")
)
C2A_STAGED_MAX_HOLD_HR_PATH: KeyPath = tuple(
    "campaigns.C2A_staged.max_hold_hr".split(".")
)
C2A_STAGED_DEPLETION_FLUX_DECAY_FRACTION_PATH: KeyPath = tuple(
    "campaigns.C2A_staged.depletion_flux_decay_fraction".split(".")
)
C2A_STAGED_DEPLETION_FLUX_DECAY_FRACTION_FLOOR = 0.01
# SC-50 audit consumers: RecipeSchema identity/canonicalization,
# CampaignManager staged endpoint logic, _build_eval_inputs runtime overrides,
# and knob-saturation requested/applied diagnostics.
C2A_STAGED_DEPLETION_LOG_SLOPE_FIELD = "depletion_log_slope_epsilon_per_hr"
C2A_STAGED_DEPLETION_LOG_SLOPE_EPSILON_FLOOR_PER_HR = 0.01
C3_ALKALI_DOSING_NA_KG_PATH: KeyPath = tuple(
    "campaigns.C3.alkali_dosing.Na_kg".split(".")
)
C3_ALKALI_DOSING_K_KG_PATH: KeyPath = tuple(
    "campaigns.C3.alkali_dosing.K_kg".split(".")
)
C3_ALKALI_DOSING_NA_HIGH_KG = 140.0
C3_ALKALI_DOSING_K_HIGH_KG = 56.0
# Deadband exists only so continuous samplers can reach OFF; keep it well
# below physically meaningful C3 shuttle doses.
C3_ALKALI_DOSING_ZERO_LEVEL_FRACTION = 0.01
C3_ALKALI_DOSING_ZERO_LEVEL_KG_BY_PATH: Mapping[KeyPath, float] = MappingProxyType(
    {
        C3_ALKALI_DOSING_NA_KG_PATH: (
            C3_ALKALI_DOSING_NA_HIGH_KG * C3_ALKALI_DOSING_ZERO_LEVEL_FRACTION
        ),
        C3_ALKALI_DOSING_K_KG_PATH: (
            C3_ALKALI_DOSING_K_HIGH_KG * C3_ALKALI_DOSING_ZERO_LEVEL_FRACTION
        ),
    }
)
C3_ALKALI_DOSING_ZERO_LEVEL_KG_BY_SPECIES: Mapping[str, float] = MappingProxyType(
    {
        "Na": C3_ALKALI_DOSING_ZERO_LEVEL_KG_BY_PATH[C3_ALKALI_DOSING_NA_KG_PATH],
        "K": C3_ALKALI_DOSING_ZERO_LEVEL_KG_BY_PATH[C3_ALKALI_DOSING_K_KG_PATH],
    }
)
C2A_STAGED_STAGE_NAMES: tuple[str, ...] = (
    "alkali_early_fe",
    "sio_window",
    "fe_hot_hold",
    "cool_for_na_shuttle",
)
C2A_STAGED_DEFAULT_ORDER = "sio_then_fe"
C2A_STAGED_ORDER_CHOICES: tuple[str, ...] = (
    C2A_STAGED_DEFAULT_ORDER,
    "fe_then_sio",
)
C2A_STAGED_STAGE_NAMES_BY_ORDER: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "sio_then_fe": C2A_STAGED_STAGE_NAMES,
        "fe_then_sio": (
            "alkali_early_fe",
            "fe_hot_hold",
            "sio_window",
            "cool_for_na_shuttle",
        ),
    }
)
C2A_STAGED_DEPLETION_LOG_SLOPE_STAGE_NAMES: tuple[str, ...] = (
    "alkali_early_fe",
    "sio_window",
    "fe_hot_hold",
)
C2A_STAGED_DEPLETION_LOG_SLOPE_EPSILON_PATHS_BY_STAGE: Mapping[
    str, KeyPath
] = MappingProxyType(
    {
        stage_name: C2A_STAGED_STAGES_PATH
        + (stage_name, C2A_STAGED_DEPLETION_LOG_SLOPE_FIELD)
        for stage_name in C2A_STAGED_DEPLETION_LOG_SLOPE_STAGE_NAMES
    }
)
C2A_STAGED_DEPLETION_LOG_SLOPE_EPSILON_PATHS: tuple[KeyPath, ...] = tuple(
    C2A_STAGED_DEPLETION_LOG_SLOPE_EPSILON_PATHS_BY_STAGE.values()
)
C2A_STAGED_STAGE_GAS_FIELDS: tuple[str, ...] = (
    "pO2_mbar",
    "p_total_mbar",
    "gas_cover_mode",
)
C2A_STAGED_STAGE_METADATA_FIELDS: frozenset[str] = frozenset(
    ("target_species", "endpoint", "verification")
)
C2A_STAGED_GAS_COVER_MODES: tuple[str, ...] = ("pn2_sweep", "po2_hold")
C2A_STAGED_STAGE_FIELDS_BY_NAME: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "alkali_early_fe": (
            "duration_h",
            "target_C",
            "ramp_rate_C_per_hr",
            C2A_STAGED_DEPLETION_LOG_SLOPE_FIELD,
        ) + C2A_STAGED_STAGE_GAS_FIELDS,
        "sio_window": (
            "duration_h",
            "target_C",
            "ramp_rate_C_per_hr",
            C2A_STAGED_DEPLETION_LOG_SLOPE_FIELD,
        ) + C2A_STAGED_STAGE_GAS_FIELDS,
        "fe_hot_hold": (
            "duration_h",
            "ramp_rate_C_per_hr",
            C2A_STAGED_DEPLETION_LOG_SLOPE_FIELD,
        ) + C2A_STAGED_STAGE_GAS_FIELDS,
        "cool_for_na_shuttle": (
            "duration_h",
            "target_C",
            "ramp_rate_C_per_hr",
        ) + C2A_STAGED_STAGE_GAS_FIELDS,
    }
)
O2_BUBBLER_CAMPAIGN_RATE_PATHS: tuple[KeyPath, ...] = tuple(
    tuple(f"campaigns.{campaign}.{O2_BUBBLER_RATE_KEY}".split("."))
    for campaign in ("C2B", "C3", "C4", "C6")
)
O2_BUBBLER_RATE_PATHS: tuple[KeyPath, ...] = O2_BUBBLER_CAMPAIGN_RATE_PATHS


class RecipeValidationError(ValueError):
    """Raised when a recipe patch attempts an unsafe or unknown mutation."""


class RecipePinWarning(UserWarning):
    """Warns when an optimizer pin names a knob that is already not searched."""


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


def _c2a_stage_gas_knobs(stage_name: str) -> tuple[KnobSpec, ...]:
    prefix = f"campaigns.C2A_staged.stages.{stage_name}"
    return (
        _knob(
            f"{prefix}.pO2_mbar",
            low=0.0,
            high=15.0,
            units="mbar",
            bounds_source=(
                "engineering_envelope: per-stage pO2 lever for future "
                "Atmosphere.CONTROLLED_O2 C2A_staged execution; bounded to "
                "the 5-15 mbar gas-cover band for this optimizer slice"
            ),
        ),
        _knob(
            f"{prefix}.p_total_mbar",
            low=5.0,
            high=15.0,
            units="mbar",
            bounds_source="setpoints:campaigns.C2A_staged.p_total_mbar",
        ),
        _knob(
            f"{prefix}.gas_cover_mode",
            "categorical",
            choices=C2A_STAGED_GAS_COVER_MODES,
            bounds_source=(
                "engineering_envelope: pn2_sweep maps to "
                "Atmosphere.PN2_SWEEP; po2_hold maps to "
                "Atmosphere.CONTROLLED_O2"
            ),
        ),
    )


def _c2a_stage_depletion_log_slope_knob(stage_name: str) -> KnobSpec:
    prefix = f"campaigns.C2A_staged.stages.{stage_name}"
    return _knob(
        f"{prefix}.{C2A_STAGED_DEPLETION_LOG_SLOPE_FIELD}",
        low=0.0,
        high=0.50,
        units="1/hr",
        bounds_source=(
            "engineering_envelope: per-stage cumulative-yield log-slope epsilon; "
            "0.0 keeps fixed-duration mode, positive values floor to 0.01 1/hr"
        ),
    )


def _o2_bubbler_rate_knob(path: str, *, bounds_source: str) -> KnobSpec:
    return _knob(
        path,
        low=0.0,
        high=5.0,
        units="kg/hr O2",
        bounds_source=bounds_source,
        search_enabled=False,
        runtime_enabled=False,
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
    C2A_STAGED_STAGE_PRESSURE_TOTAL_BY_PO2: Mapping[KeyPath, KeyPath] = MappingProxyType(
        {
            C2A_STAGED_STAGES_PATH + (stage_name, "pO2_mbar"):
            C2A_STAGED_STAGES_PATH + (stage_name, "p_total_mbar")
            for stage_name in C2A_STAGED_STAGE_NAMES
        }
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
            "o2_bubbler_eta_absorb_default",
            low=0.0,
            high=1.0,
            units="fraction",
            bounds_source=(
                "engineering_envelope inert SSO-O2 chunk-A transfer-efficiency "
                "placeholder; runtime reader lands in chunk B"
            ),
            search_enabled=False,
            runtime_enabled=False,
        ),
        _knob(
            "o2_bubbler_target_fO2_log",
            low=-20.0,
            high=0.0,
            units="log10 fO2",
            bounds_source=(
                "engineering_envelope inert SSO-O2 chunk-A target placeholder; "
                "runtime reader lands in chunk B"
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
            low=0.0,
            high=0.50,
            units="fraction",
            bounds_source=(
                "engineering_envelope legacy replay only: replaced for search by per-stage "
                "depletion_log_slope_epsilon_per_hr"
            ),
            search_enabled=False,
        ),
        _knob(
            "campaigns.C2A_staged.order",
            "categorical",
            choices=C2A_STAGED_ORDER_CHOICES,
            bounds_source=(
                "engineering_envelope: C2A_staged internal order branch; "
                "sio_then_fe preserves the origin/main schedule, fe_then_sio "
                "swaps only sio_window and fe_hot_hold"
            ),
            search_enabled=False,
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
        _c2a_stage_depletion_log_slope_knob("alkali_early_fe"),
        *_c2a_stage_gas_knobs("alkali_early_fe"),
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
        _c2a_stage_depletion_log_slope_knob("sio_window"),
        *_c2a_stage_gas_knobs("sio_window"),
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
        _c2a_stage_depletion_log_slope_knob("fe_hot_hold"),
        *_c2a_stage_gas_knobs("fe_hot_hold"),
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
        *_c2a_stage_gas_knobs("cool_for_na_shuttle"),
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
        _o2_bubbler_rate_knob(
            "campaigns.C2B.o2_bubbler_kg_per_hr",
            bounds_source=(
                "engineering_envelope inert SSO-O2 chunk-A C2B actuator "
                "placeholder; runtime reader lands in chunk B"
            ),
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
            high=C3_ALKALI_DOSING_NA_HIGH_KG,
            units="kg",
            bounds_source=(
                "engineering_envelope from disabled band <=7 kg plus "
                "setpoints:campaigns.C3.Na_phase.Na_total_kg high bound"
            ),
        ),
        _knob(
            "campaigns.C3.alkali_dosing.K_kg",
            low=0.0,
            high=C3_ALKALI_DOSING_K_HIGH_KG,
            units="kg",
            bounds_source=(
                "engineering_envelope from disabled band <=2.8 kg plus "
                "setpoints:campaigns.C3.K_phase.K_per_cycle_kg high bound "
                "over two cycles"
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
        _o2_bubbler_rate_knob(
            "campaigns.C3.o2_bubbler_kg_per_hr",
            bounds_source=(
                "engineering_envelope inert SSO-O2 chunk-A C3 actuator "
                "placeholder; runtime reader lands in chunk B"
            ),
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
            search_enabled=False,
        ),
        _o2_bubbler_rate_knob(
            "campaigns.C4.o2_bubbler_kg_per_hr",
            bounds_source=(
                "engineering_envelope inert SSO-O2 chunk-A C4 actuator "
                "placeholder; runtime reader lands in chunk B"
            ),
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
        _o2_bubbler_rate_knob(
            "campaigns.C6.o2_bubbler_kg_per_hr",
            bounds_source=(
                "engineering_envelope inert SSO-O2 chunk-A C6 actuator "
                "placeholder; runtime reader lands in chunk B"
            ),
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
            low=OVERHEAD_HEADSPACE_OFFSET_MIN_K,
            high=OVERHEAD_HEADSPACE_OFFSET_MAX_K,
            units="K",
            bounds_source=OVERHEAD_HEADSPACE_OFFSET_BOUNDS_SOURCE,
        ),
        _knob(
            "overhead_headspace.liner_temperature_C.default_C",
            low=OVERHEAD_HOT_WALL_MIN_C,
            high=OVERHEAD_HOT_WALL_MAX_C,
            units="C",
            bounds_source=OVERHEAD_HOT_WALL_BOUNDS_SOURCE,
        ),
        _knob(
            "overhead_headspace.pipe_segment_temperatures_C.default_C",
            low=OVERHEAD_HOT_WALL_MIN_C,
            high=OVERHEAD_HOT_WALL_MAX_C,
            units="C",
            bounds_source=OVERHEAD_HOT_WALL_BOUNDS_SOURCE,
        ),
        _knob(
            "overhead_headspace.pipe_segment_temperatures_C.segments.stage_0_to_stage_1.default_C",
            low=OVERHEAD_HOT_WALL_MIN_C,
            high=OVERHEAD_HOT_WALL_MAX_C,
            units="C",
            bounds_source=OVERHEAD_HOT_WALL_BOUNDS_SOURCE,
        ),
        _knob(
            "overhead_headspace.pipe_segment_temperatures_C.segments.stage_1_to_stage_2.default_C",
            low=OVERHEAD_HOT_WALL_MIN_C,
            high=OVERHEAD_HOT_WALL_MAX_C,
            units="C",
            bounds_source=OVERHEAD_HOT_WALL_BOUNDS_SOURCE,
        ),
        _knob(
            "overhead_headspace.pipe_segment_temperatures_C.segments.stage_2_to_stage_3.default_C",
            low=OVERHEAD_HOT_WALL_MIN_C,
            high=OVERHEAD_HOT_WALL_MAX_C,
            units="C",
            bounds_source=OVERHEAD_HOT_WALL_BOUNDS_SOURCE,
        ),
        _knob(
            "overhead_headspace.pipe_segment_temperatures_C.segments.stage_3_to_stage_4.default_C",
            low=OVERHEAD_HOT_WALL_MIN_C,
            high=OVERHEAD_HOT_WALL_MAX_C,
            units="C",
            bounds_source=OVERHEAD_HOT_WALL_BOUNDS_SOURCE,
        ),
        _knob(
            "overhead_headspace.pipe_segment_temperatures_C.segments.stage_4_to_stage_5.default_C",
            low=OVERHEAD_DOWNSTREAM_SEGMENT_BOUNDS_C["stage_4_to_stage_5"][0],
            high=OVERHEAD_DOWNSTREAM_SEGMENT_BOUNDS_C["stage_4_to_stage_5"][1],
            units="C",
            bounds_source=(
                "condensation_train stage 4/5 interface envelope: "
                "state.py CondensationTrain.create_default"
            ),
        ),
        _knob(
            "overhead_headspace.pipe_segment_temperatures_C.segments.stage_5_to_stage_6.default_C",
            low=OVERHEAD_DOWNSTREAM_SEGMENT_BOUNDS_C["stage_5_to_stage_6"][0],
            high=OVERHEAD_DOWNSTREAM_SEGMENT_BOUNDS_C["stage_5_to_stage_6"][1],
            units="C",
            bounds_source=(
                "condensation_train stage 5/6 interface envelope: "
                "state.py CondensationTrain.create_default"
            ),
        ),
        _knob(
            "overhead_headspace.pipe_segment_temperatures_C.segments.stage_6_to_stage_7.default_C",
            low=OVERHEAD_DOWNSTREAM_SEGMENT_BOUNDS_C["stage_6_to_stage_7"][0],
            high=OVERHEAD_DOWNSTREAM_SEGMENT_BOUNDS_C["stage_6_to_stage_7"][1],
            units="C",
            bounds_source=(
                "condensation_train stage 6/7 interface envelope: "
                "state.py CondensationTrain.create_default"
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
        pinned_paths: Sequence[str | KeyPath] | None = None,
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
        if pinned_paths:
            self.allowlist, resolved_pins = _apply_pinned_paths(self, pinned_paths)
            self._spec_by_path = {spec.path: spec for spec in self.allowlist}
            self.pinned_paths = resolved_pins
        else:
            self.pinned_paths = ()
        self._bounds_identity_json = _bounds_identity_json(self)
        self.bounds_digest = hashlib.sha256(
            self._bounds_identity_json.encode("utf-8")
        ).hexdigest()

    @property
    def search_allowlist(self) -> tuple[KnobSpec, ...]:
        return tuple(spec for spec in self.allowlist if spec.search_enabled)

    @property
    def bounds_identity_json(self) -> str:
        return self._bounds_identity_json

    def with_pinned_paths(
        self,
        pinned_paths: Sequence[str | KeyPath] | None,
    ) -> "RecipeSchema":
        if not pinned_paths:
            return self
        return type(self)(
            allowlist=self.allowlist,
            forbidden_prefixes=self.forbidden_prefixes,
            recipe_schema_version=self.recipe_schema_version,
            allowlist_version=self.allowlist_version,
            pinned_paths=pinned_paths,
        )

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
        canonical_values = _canonical_recipe_values(validated.values)
        runtime_values = {
            path: value
            for path, value in canonical_values.items()
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

    def o2_bubbler_settings(self, patch: "RecipePatch") -> Mapping[str, Any]:
        validated = patch.validated(self)
        return _o2_bubbler_identity_settings(validated.values)


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
        "campaigns.C2A_staged.stages.alkali_early_fe.duration_h",
        "campaigns.C2A_staged.stages.alkali_early_fe.target_C",
        "campaigns.C2A_staged.stages.alkali_early_fe.ramp_rate_C_per_hr",
        "campaigns.C2A_staged.stages.alkali_early_fe.depletion_log_slope_epsilon_per_hr",
        "campaigns.C2A_staged.stages.alkali_early_fe.pO2_mbar",
        "campaigns.C2A_staged.stages.alkali_early_fe.p_total_mbar",
        "campaigns.C2A_staged.stages.alkali_early_fe.gas_cover_mode",
        "campaigns.C2A_staged.stages.sio_window.duration_h",
        "campaigns.C2A_staged.stages.sio_window.target_C",
        "campaigns.C2A_staged.stages.sio_window.ramp_rate_C_per_hr",
        "campaigns.C2A_staged.stages.sio_window.depletion_log_slope_epsilon_per_hr",
        "campaigns.C2A_staged.stages.sio_window.pO2_mbar",
        "campaigns.C2A_staged.stages.sio_window.p_total_mbar",
        "campaigns.C2A_staged.stages.sio_window.gas_cover_mode",
        "campaigns.C2A_staged.stages.fe_hot_hold.duration_h",
        "campaigns.C2A_staged.stages.fe_hot_hold.ramp_rate_C_per_hr",
        "campaigns.C2A_staged.stages.fe_hot_hold.depletion_log_slope_epsilon_per_hr",
        "campaigns.C2A_staged.stages.fe_hot_hold.pO2_mbar",
        "campaigns.C2A_staged.stages.fe_hot_hold.p_total_mbar",
        "campaigns.C2A_staged.stages.fe_hot_hold.gas_cover_mode",
        "campaigns.C2A_staged.stages.cool_for_na_shuttle.duration_h",
        "campaigns.C2A_staged.stages.cool_for_na_shuttle.target_C",
        "campaigns.C2A_staged.stages.cool_for_na_shuttle.ramp_rate_C_per_hr",
        "campaigns.C2A_staged.stages.cool_for_na_shuttle.pO2_mbar",
        "campaigns.C2A_staged.stages.cool_for_na_shuttle.p_total_mbar",
        "campaigns.C2A_staged.stages.cool_for_na_shuttle.gas_cover_mode",
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
    order = direct_values.get(C2A_STAGED_ORDER_PATH)
    if not stage_values and order is None:
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

    if order is not None:
        stages = _ordered_c2a_staged_stages(stages, order)

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
    names: list[str] = []
    for stage in stages:
        if not isinstance(stage, dict) or not isinstance(stage.get("name"), str):
            raise RecipeValidationError("C2A_staged stages must be named mappings")
        names.append(stage["name"])
    validate_c2a_staged_stage_order(names, source="default C2A_staged stages")
    return stages


def c2a_staged_stage_order(order: Any = C2A_STAGED_DEFAULT_ORDER) -> tuple[str, ...]:
    if order is None:
        order = C2A_STAGED_DEFAULT_ORDER
    if not isinstance(order, str):
        raise RecipeValidationError("campaigns.C2A_staged.order must be a string")
    normalized = order.strip()
    try:
        return C2A_STAGED_STAGE_NAMES_BY_ORDER[normalized]
    except KeyError as exc:
        raise RecipeValidationError(f"unknown C2A_staged order: {order!r}") from exc


def c2a_staged_order_from_stage_names(stage_names: Sequence[str]) -> str:
    names = validate_c2a_staged_stage_order(stage_names)
    for order, expected in C2A_STAGED_STAGE_NAMES_BY_ORDER.items():
        if names == expected:
            return order
    raise RecipeValidationError(
        "unsupported C2A_staged stage order: " + " -> ".join(names)
    )


def validate_c2a_staged_stage_order(
    stage_names: Sequence[str],
    *,
    source: str = "C2A_staged.stages",
) -> tuple[str, ...]:
    names = tuple(str(name) for name in stage_names)
    required = set(C2A_STAGED_STAGE_NAMES)
    seen: set[str] = set()
    duplicates: set[str] = set()
    unknown: set[str] = set()
    for name in names:
        if name in seen:
            duplicates.add(name)
        seen.add(name)
        if name not in required:
            unknown.add(name)
    if unknown:
        raise RecipeValidationError(
            f"unknown {source} stage: " + ", ".join(sorted(unknown))
        )
    if duplicates:
        raise RecipeValidationError(
            f"duplicate {source} stage: " + ", ".join(sorted(duplicates))
        )
    missing = required - seen
    if missing:
        raise RecipeValidationError(
            f"missing {source} stage: " + ", ".join(sorted(missing))
        )
    if not names or names[0] != C2A_STAGED_STAGE_NAMES[0]:
        raise RecipeValidationError(
            f"{source} must keep {C2A_STAGED_STAGE_NAMES[0]} first"
        )
    if names[-1] != C2A_STAGED_STAGE_NAMES[-1]:
        raise RecipeValidationError(
            f"{source} must keep {C2A_STAGED_STAGE_NAMES[-1]} last"
        )
    return names


def _ordered_c2a_staged_stages(stages: list[dict[str, Any]], order: Any) -> list[dict[str, Any]]:
    stage_order = c2a_staged_stage_order(order)
    stages_by_name = {
        str(stage.get("name")): stage
        for stage in stages
        if isinstance(stage, dict) and stage.get("name") is not None
    }
    validate_c2a_staged_stage_order(tuple(stages_by_name), source="C2A_staged.stages")
    return [stages_by_name[name] for name in stage_order]


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


def default_bounds_digest() -> str:
    return RecipeSchema().bounds_digest


def _bounds_identity_json(schema: RecipeSchema) -> str:
    return canonical_json_dumps(_bounds_identity_payload(schema))


def _bounds_identity_payload(schema: RecipeSchema) -> dict[str, Any]:
    return {
        "schema_version": BOUNDS_DIGEST_SCHEMA_VERSION,
        "knobs": [
            _bounds_identity_entry(spec)
            for spec in sorted(
                schema.allowlist,
                key=lambda item: _format_path(item.path),
            )
        ],
        "sampling_constraints": _sampling_bounds_constraints(schema),
    }


def _bounds_identity_entry(spec: KnobSpec) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "kind": spec.kind,
        "path": _format_path(spec.path),
    }
    if spec.low is not None:
        entry["low"] = _bounds_identity_float(spec.low)
    if spec.high is not None:
        entry["high"] = _bounds_identity_float(spec.high)
    if spec.choices is not None:
        entry["choices"] = [str(choice) for choice in spec.choices]
    step = getattr(spec, "step", None)
    if step is not None:
        entry["step"] = _bounds_identity_float(step)
    return entry


def _bounds_identity_float(value: Any) -> str:
    numeric = float(value)
    if not math.isfinite(numeric):
        raise RecipeValidationError("optimizer bounds digest rejects NaN and infinity")
    return repr(numeric)


def _sampling_bounds_constraints(schema: RecipeSchema) -> dict[str, Any]:
    pressure_pairs = tuple(schema.PRESSURE_COUPLED_DEFAULT_PAIRS) + tuple(
        schema.C2A_STAGED_STAGE_PRESSURE_TOTAL_BY_PO2.items()
    )
    return {
        "pressure_coupled_pairs": [
            {
                "pO2_path": _format_path(po2_path),
                "p_total_path": _format_path(total_path),
                "pO2_effective_high": "min(pO2_high,p_total)",
                "p_total_effective_low": "max(p_total_low,pO2)",
                "positive_carrier_open_interval": (
                    po2_path in schema.C2A_STAGED_STAGE_PRESSURE_TOTAL_BY_PO2
                ),
            }
            for po2_path, total_path in sorted(
                pressure_pairs,
                key=lambda pair: (_format_path(pair[0]), _format_path(pair[1])),
            )
        ]
    }


def _apply_pinned_paths(
    schema: RecipeSchema,
    pinned_paths: Sequence[str | KeyPath],
) -> tuple[tuple[KnobSpec, ...], tuple[KeyPath, ...]]:
    pinned: set[KeyPath] = set()
    resolved: list[KeyPath] = []
    spec_by_path = dict(schema._spec_by_path)

    for raw_path in pinned_paths:
        parsed = _coerce_pinned_path(raw_path)
        path = _resolve_pin_path(parsed, spec_by_path)
        if schema.is_forbidden(path):
            raise RecipeValidationError(
                f"forbidden recipe pin path: {_format_path(path)}"
            )
        spec = spec_by_path.get(path)
        if spec is None:
            reason = _already_fixed_pin_reason(path)
            if reason:
                warnings.warn(
                    f"WARNING: optimizer pin {_format_path(path)} already fixed; "
                    f"{reason}",
                    RecipePinWarning,
                    stacklevel=3,
                )
                continue
            raise RecipeValidationError(
                f"pin path matches no optimizer knob: {_format_path(parsed)}"
            )
        if not spec.search_enabled:
            warnings.warn(
                f"WARNING: optimizer pin {_format_path(path)} already fixed; "
                "knob is not in optimizer search_allowlist",
                RecipePinWarning,
                stacklevel=3,
            )
            continue
        if path not in pinned:
            resolved.append(path)
        pinned.add(path)

    if not pinned:
        return schema.allowlist, ()
    return (
        tuple(
            replace(spec, search_enabled=False) if spec.path in pinned else spec
            for spec in schema.allowlist
        ),
        tuple(resolved),
    )


def _coerce_pinned_path(path: str | KeyPath) -> KeyPath:
    if isinstance(path, str):
        return _normalize_key_path(tuple(path.split(".")))
    return _normalize_key_path(path)


def _resolve_pin_path(
    path: KeyPath,
    spec_by_path: Mapping[KeyPath, KnobSpec],
) -> KeyPath:
    if path in spec_by_path or _already_fixed_pin_reason(path):
        return path
    if path[0] != "campaigns":
        campaign_path = ("campaigns",) + path
        if campaign_path in spec_by_path or _already_fixed_pin_reason(campaign_path):
            return campaign_path
    return path


def _already_fixed_pin_reason(path: KeyPath) -> str:
    if (
        len(path) == 5
        and path[:3] == C2A_STAGED_STAGES_PATH
        and path[3] == "fe_hot_hold"
        and path[4] == "target_C"
    ):
        return (
            "C2A_staged.stages.fe_hot_hold has no per-stage target_C knob; "
            "pin ignored"
        )
    return ""


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
        _validate_c2a_staged_depletion_knob_conflict(self.values)
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
        active_allowlist_version = _effective_o2_bubbler_allowlist_version(
            self.values,
            allowlist_version or active_schema.allowlist_version,
        )
        canonical = self.canonical_json().encode("utf-8")
        payload = (
            canonical
            + b"\n"
            + schema_version.encode("utf-8")
            + b"\n"
            + active_allowlist_version.encode("utf-8")
            + b"\n"
            + active_schema.bounds_digest.encode("utf-8")
        )
        return hashlib.sha256(payload).hexdigest()

    def canonical_json(self) -> str:
        values = _canonical_recipe_values(self.values)
        entries = [
            {"path": list(path), "value": _normalize_value(value)}
            for path, value in sorted(values.items())
        ]
        return canonical_json_dumps(entries)


def _identity_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result):
        return None
    return result


def _canonical_c3_alkali_dosing_recipe_values(
    values: Mapping[KeyPath, Any],
) -> dict[KeyPath, Any]:
    canonical = dict(values)
    for path, zero_level_kg in C3_ALKALI_DOSING_ZERO_LEVEL_KG_BY_PATH.items():
        if path not in canonical:
            continue
        dose_kg = _identity_float(canonical[path])
        if dose_kg is None:
            continue
        if dose_kg <= zero_level_kg:
            canonical.pop(path, None)
        else:
            canonical[path] = dose_kg
    return canonical


def _canonical_recipe_values(values: Mapping[KeyPath, Any]) -> dict[KeyPath, Any]:
    return _canonical_o2_bubbler_recipe_values(
        _canonical_c3_alkali_dosing_recipe_values(
            _canonical_c2a_staged_depletion_log_slope_recipe_values(
                _canonical_c2a_staged_depletion_flux_decay_recipe_values(values)
            )
        )
    )


def _validate_c2a_staged_depletion_knob_conflict(
    values: Mapping[KeyPath, Any],
) -> None:
    if C2A_STAGED_DEPLETION_FLUX_DECAY_FRACTION_PATH not in values:
        return
    if not any(path in values for path in C2A_STAGED_DEPLETION_LOG_SLOPE_EPSILON_PATHS):
        return
    raise RecipeValidationError(
        "campaigns.C2A_staged.depletion_flux_decay_fraction is legacy-only "
        "and conflicts with per-stage depletion_log_slope_epsilon_per_hr"
    )


def _canonical_c2a_staged_depletion_log_slope_recipe_values(
    values: Mapping[KeyPath, Any],
) -> dict[KeyPath, Any]:
    _validate_c2a_staged_depletion_knob_conflict(values)
    canonical = dict(values)
    for path in C2A_STAGED_DEPLETION_LOG_SLOPE_EPSILON_PATHS:
        if path not in canonical:
            continue
        epsilon = _identity_float(canonical[path])
        if epsilon is None:
            continue
        if epsilon <= 0.0:
            canonical.pop(path, None)
        elif epsilon < C2A_STAGED_DEPLETION_LOG_SLOPE_EPSILON_FLOOR_PER_HR:
            canonical[path] = C2A_STAGED_DEPLETION_LOG_SLOPE_EPSILON_FLOOR_PER_HR
        else:
            canonical[path] = epsilon
    return canonical


def _canonical_c2a_staged_depletion_flux_decay_recipe_values(
    values: Mapping[KeyPath, Any],
) -> dict[KeyPath, Any]:
    canonical = dict(values)
    if C2A_STAGED_DEPLETION_FLUX_DECAY_FRACTION_PATH not in canonical:
        return canonical
    fraction = _identity_float(
        canonical[C2A_STAGED_DEPLETION_FLUX_DECAY_FRACTION_PATH]
    )
    if fraction is None:
        return canonical
    if 0.0 <= fraction < C2A_STAGED_DEPLETION_FLUX_DECAY_FRACTION_FLOOR:
        canonical[C2A_STAGED_DEPLETION_FLUX_DECAY_FRACTION_PATH] = 0.0
    else:
        canonical[C2A_STAGED_DEPLETION_FLUX_DECAY_FRACTION_PATH] = fraction
    return canonical


def _canonical_o2_bubbler_recipe_values(
    values: Mapping[KeyPath, Any],
) -> dict[KeyPath, Any]:
    canonical = dict(values)
    for path in O2_BUBBLER_RATE_PATHS:
        if path not in canonical:
            continue
        rate = _identity_float(canonical[path])
        if rate is None:
            continue
        if rate <= 0.0:
            canonical.pop(path, None)
        else:
            canonical[path] = rate
    if O2_BUBBLER_ETA_ABSORB_DEFAULT_PATH in canonical:
        eta = _identity_float(canonical[O2_BUBBLER_ETA_ABSORB_DEFAULT_PATH])
        if eta is None or math.isclose(
            eta,
            O2_BUBBLER_DEFAULT_ETA_ABSORB,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            canonical.pop(O2_BUBBLER_ETA_ABSORB_DEFAULT_PATH, None)
        elif eta is not None:
            canonical[O2_BUBBLER_ETA_ABSORB_DEFAULT_PATH] = eta
    if O2_BUBBLER_TARGET_FO2_LOG_PATH in canonical:
        target = _identity_float(canonical[O2_BUBBLER_TARGET_FO2_LOG_PATH])
        if target is None:
            canonical.pop(O2_BUBBLER_TARGET_FO2_LOG_PATH, None)
        else:
            canonical[O2_BUBBLER_TARGET_FO2_LOG_PATH] = target
    return canonical


def _o2_bubbler_identity_settings(values: Mapping[KeyPath, Any]) -> Mapping[str, Any]:
    canonical = _canonical_o2_bubbler_recipe_values(values)
    settings: dict[str, Any] = {}
    rates = {
        ".".join(path[1:-1]): float(canonical[path])
        for path in O2_BUBBLER_RATE_PATHS
        if path in canonical
    }
    if rates:
        settings["kg_per_hr"] = rates
    if O2_BUBBLER_ETA_ABSORB_DEFAULT_PATH in canonical:
        settings["eta_absorb_default"] = float(
            canonical[O2_BUBBLER_ETA_ABSORB_DEFAULT_PATH]
        )
    if O2_BUBBLER_TARGET_FO2_LOG_PATH in canonical:
        settings["target_fO2_log"] = float(canonical[O2_BUBBLER_TARGET_FO2_LOG_PATH])
    return MappingProxyType(settings)


def _effective_o2_bubbler_allowlist_version(
    values: Mapping[KeyPath, Any],
    requested_version: str,
) -> str:
    if (
        requested_version == allowlist_version
        and not _o2_bubbler_identity_settings(values)
    ):
        return O2_BUBBLER_NEUTRAL_ALLOWLIST_VERSION
    return requested_version


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
    stage_names: list[str] = []
    for stage in stages:
        if not isinstance(stage, Mapping) or not isinstance(stage.get("name"), str):
            raise RecipeValidationError("C2A_staged stages must be named mappings")
        stage_name = str(stage["name"])
        if stage_name not in C2A_STAGED_STAGE_NAMES:
            raise RecipeValidationError(f"unknown C2A_staged stage: {stage_name}")
        if stage_name in seen:
            raise RecipeValidationError(f"duplicate C2A_staged stage: {stage_name}")
        seen.add(stage_name)
        stage_names.append(stage_name)
        allowed_fields = set(C2A_STAGED_STAGE_FIELDS_BY_NAME[stage_name])
        allowed_fields.update(C2A_STAGED_STAGE_METADATA_FIELDS)
        unknown_fields = sorted(
            field_name
            for field_name in stage
            if isinstance(field_name, str)
            and field_name != "name"
            and field_name not in allowed_fields
        )
        if unknown_fields:
            first = unknown_fields[0]
            raise RecipeValidationError(
                "unknown C2A_staged stage field: "
                f"{_format_path(C2A_STAGED_STAGES_PATH + (stage_name, first))}"
            )
        for field_name in C2A_STAGED_STAGE_FIELDS_BY_NAME[stage_name]:
            if field_name in stage:
                flat[C2A_STAGED_STAGES_PATH + (stage_name, field_name)] = (
                    _normalize_value(stage[field_name])
                )
    has_complete_stage_set = len(stage_names) == len(C2A_STAGED_STAGE_NAMES) and set(
        stage_names
    ) == set(C2A_STAGED_STAGE_NAMES)
    if has_complete_stage_set:
        order = c2a_staged_order_from_stage_names(stage_names)
        if order != C2A_STAGED_DEFAULT_ORDER:
            flat[C2A_STAGED_ORDER_PATH] = order
    elif (
        len(stage_names) > 1
        and stage_names[0] == C2A_STAGED_STAGE_NAMES[0]
        and stage_names[-1] == C2A_STAGED_STAGE_NAMES[-1]
    ):
        validate_c2a_staged_stage_order(stage_names)


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
        _validate_pressure_pair(
            po2_path,
            po2,
            po2_source,
            total_path,
            total,
            total_source,
            "set both pO2 and p_total knobs for this campaign",
        )
    for (
        po2_path,
        total_path,
    ) in schema.C2A_STAGED_STAGE_PRESSURE_TOTAL_BY_PO2.items():
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
            po2 = float(_default_setpoint_value(C2A_STAGED_PO2_MBAR_DEFAULT_PATH))
            po2_source = "C2A_staged YAML default"
        if total_path in values:
            total = float(values[total_path])
            total_source = "patched"
        else:
            total = float(_default_setpoint_value(C2A_STAGED_P_TOTAL_MBAR_DEFAULT_PATH))
            total_source = "C2A_staged YAML default"
        mode_path = po2_path[:-1] + ("gas_cover_mode",)
        if mode_path in values:
            gas_cover_mode = str(values[mode_path])
            gas_cover_mode_source = "patched"
        else:
            gas_cover_mode = "pn2_sweep"
            gas_cover_mode_source = "C2A_staged default"
        _validate_pressure_pair(
            po2_path,
            po2,
            po2_source,
            total_path,
            total,
            total_source,
            "set both pO2_mbar and p_total_mbar knobs for this C2A_staged stage",
            gas_cover_mode_path=mode_path,
            gas_cover_mode=gas_cover_mode,
            gas_cover_mode_source=gas_cover_mode_source,
        )


def _validate_pressure_pair(
    po2_path: KeyPath,
    po2: float,
    po2_source: str,
    total_path: KeyPath,
    total: float,
    total_source: str,
    guidance: str,
    *,
    gas_cover_mode_path: KeyPath | None = None,
    gas_cover_mode: str | None = None,
    gas_cover_mode_source: str = "",
) -> None:
    tolerance = max(1e-12, 1e-12 * max(1.0, abs(po2), abs(total)))
    if po2 - total > tolerance:
        raise RecipeValidationError(
            "recipe_pressure_partial_exceeds_total: "
            f"{_format_path(po2_path)}={po2:.12g} ({po2_source}) > "
            f"{_format_path(total_path)}={total:.12g} ({total_source}); "
            "oxygen partial pressure cannot exceed total pressure; "
            f"{guidance}"
        )
    if gas_cover_mode == "pn2_sweep" and total <= po2:
        mode_path = gas_cover_mode_path or total_path[:-1] + ("gas_cover_mode",)
        raise RecipeValidationError(
            "recipe_pressure_pn2_sweep_requires_positive_carrier: "
            f"{_format_path(total_path)}={total:.12g} ({total_source}) must be "
            f"strictly greater than {_format_path(po2_path)}={po2:.12g} "
            f"({po2_source}) when {_format_path(mode_path)}=pn2_sweep "
            f"({gas_cover_mode_source or 'unspecified'}); implied pN2_mbar must be > 0; "
            f"{guidance}"
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
        if spec.path == O2_BUBBLER_TARGET_FO2_LOG_PATH and value is None:
            return
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
