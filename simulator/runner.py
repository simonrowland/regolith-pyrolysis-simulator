"""Deterministic CLI runner harness for the Oxygen-Shuttle simulator.

This module is the single source of truth for the simulator's
non-streaming run path.  Two consumers:

* The ``python -m simulator.runner`` CLI emits a fully-specified JSON
  result document via :class:`PyrolysisRun`.
* ``SimSession`` owns the command core; this runner uses its AUTO_APPLY
  driver and emits the fully-specified JSON result document.

Goal #18 ``JSON-RUNNER-HARNESS`` invariants this module owns:

* No new physics: the runner orchestrates ``PyrolysisSimulator.step``;
  it never reaches into the kernel commit path or the ledger directly.
* No branching of the physics path: batch surfaces drive
  :class:`simulator.session.SimSession`, which orchestrates
  ``PyrolysisSimulator.step``.
* Deterministic JSON output: any wall-clock fields (``started_at_utc``,
  ``kernel_commit_sha``) accept caller-supplied overrides so golden
  fixtures stay stable across machines and time.

The JSON schema is pinned by ``docs/runner-output-schema.md`` and the
schema-shape assertion in ``tests/test_runner_smoke.py``.
"""

from __future__ import annotations

import argparse
import hashlib
import copy
import csv
import importlib.abc
import importlib.machinery
import itertools
import json
import math
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

import yaml

from simulator.backend_names import (
    ANALYTICAL_BACKEND_SERIALIZATION_TOKEN,
    canonical_backend_name,
)
from simulator.backends import (
    BackendSelectionPolicy,
)
from simulator.config import ConfigBundle, load_config_bundle
from simulator.fidelity_vocabulary import canonicalize_fidelity_emission
from simulator.campaigns import CampaignManager, CampaignPressureSetpointRefusal
from simulator.accounting import AccountingQueries
from simulator.accounting.formulas import ATOMIC_WEIGHTS_G_PER_MOL
from simulator.chemistry.kernel import (
    OXYGEN_SINK_CHANNEL_MODE_KEY,
    normalize_chemistry_kernel_config,
)
from simulator.core import (
    CampaignPhase,
    DEGRADED_PATH_ENGAGEMENT_KEYS,
    PyrolysisSimulator,
)
from simulator.condensation import (
    KNUDSEN_REFUSAL_REASON,
    gram_lab_exposed_melt_area_bridge,
    stage_purity_report,
)
from simulator.cost_ledger import build_cost_rollup_diagnostic
from simulator.diagnostics import (
    pressure_coating_pareto_diagnostic,
    wall_deposit_sticking_authority_status,
)
from simulator.trace import wall_deposit_by_segment_species_kg
from simulator.pumping_cost import pumping_context_from_sim
from simulator.run_executor import (
    RunExecution,
    RunExecutor,
    _json_safe,
    _safe_exception_text,
)
from simulator.lab_geometry import LabGeometryError, parse_lab_geometry
from simulator.lab_schedule import (
    LAB_SCHEDULE_OVERRIDE_KEY,
    LabScheduleValidationError,
    normalize_lab_schedule,
)
from simulator.optimize.recipe import C3_ALKALI_DOSING_ZERO_LEVEL_KG_BY_SPECIES
from simulator.physical_constants import CELSIUS_TO_KELVIN_OFFSET
from simulator.recipe_io import RecipeIOError, load_recipe_patch
from simulator.session import (
    SimSession,
    SimSessionConfig,
)
from simulator.state import (
    Atmosphere,
    CampaignPhase,
    HourSnapshot,
    MOLAR_MASS,
    PIPE_SEGMENT_WALL_DEPOSIT_ACCOUNT_PREFIX,
)

# Public schema version pinned by docs/runner-output-schema.md.
RUNNER_SCHEMA_VERSION = "1.5.0"
ZERO_INPUT_BASIS_BREACH = "zero_input_basis_breach"
RUNNER_MASS_BALANCE_LIMIT_PCT = 5.0e-12
O2_SOURCE_SIDE_POTENTIAL_LABEL = (
    "source-side O2 potential (emitted; not recovered)"
)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

SIO_YIELD_FEEDSTOCKS: tuple[str, ...] = (
    "lunar_mare_low_ti",
    "mars_basalt",
)
SIO_YIELD_CAMPAIGN = "C2A_continuous"
SIO_YIELD_CAMPAIGN_ALIASES: dict[str, str] = {
    SIO_YIELD_CAMPAIGN: "C2A",
}
SIO_ALPHA_PROVENANCE = (
    "Wetzel & Gail 2013 A&A 553 A92 DOI 10.1051/0004-6361/201220803; "
    "alpha_s_SiO(T)=0.52*exp(-3685/T), reaction-rate-limited"
)
SIO_INDUSTRIAL_BENCHMARK_PCT: tuple[int, int] = (8, 15)
WALL_DEPOSIT_ACCOUNT = "process.wall_deposit"
SIO_YIELD_STAGE_KEYS: dict[int, str] = {
    1: "stage_1_fe_condenser_impurity",
    3: "stage_3_sio_zone_product",
    4: "stage_4_alkali_mg_carryover",
    5: "stage_5_dust_filter_carryover",
}
SIO_WALL_DEPOSIT_SPECIES: tuple[str, ...] = ("SiO", "Na", "K", "Mg", "Fe")
NOT_APPLICABLE_UNTIL_P0 = "not_applicable_until_p0"
PRESET_PROVENANCE_METADATA_KEY = "preset"
SIO_TSWEEP_SCHEMA_VERSION = "sio-tsweep-v1"
SIO_TSWEEP_DEFAULT_T_LOW_GRID_C: tuple[float, ...] = (1050.0, 1100.0, 1150.0)
SIO_TSWEEP_DEFAULT_T_HOLD_GRID_C: tuple[float, ...] = (1400.0, 1500.0, 1600.0)
SIO_TSWEEP_DEFAULT_RAMP_GRID_C_PER_HR: tuple[float, ...] = (5.0, 10.0, 15.0)
SIO_TSWEEP_MASS_BALANCE_LIMIT_PCT = RUNNER_MASS_BALANCE_LIMIT_PCT
SIO_TSWEEP_GAP_A_BAND = "1200-1673K low-T extrapolation"
SIO_TSWEEP_WARNING_TEXT = (
    "Recipe T_hold in Gap A (1200-1673K low-T extrapolation); promote "
    "Tickler #4 SIO-TRANGE-EXTENSION-OPERATIONAL Phase A "
    "(Cardiff/Matchett/Tsuchiyama/Steurer extraction) before relying on "
    "this recommendation operationally."
)
SIO_WALL_SWEEP_SCHEMA_VERSION = "sio-wall-sweep-v1"
SIO_WALL_SWEEP_DEFAULT_WALL_T_GRID_C: tuple[float, ...] = (
    1100.0,
    1300.0,
    1500.0,
    1650.0,
)
SIO_WALL_SWEEP_DEFAULT_PO2_MODES: tuple[str, ...] = (
    "no_suppress",
    "o2_1mbar",
)
_SETPOINTS_PATCH_CHEMISTRY_KERNEL_KEYS = frozenset({OXYGEN_SINK_CHANNEL_MODE_KEY})
SIO_WALL_SWEEP_PO2_MODE_CONFIG: dict[str, dict[str, Any]] = {
    "no_suppress": {
        "label": "C2A no-suppress SiO extraction",
        "pO2_mbar": None,
    },
    "o2_1mbar": {
        "label": "1 mbar pO2 glass / clean-alkali mode",
        "pO2_mbar": 1.0,
    },
}
SIO_SLOW_FOULING_WALL_DEPOSIT_KG = 1.0e-6
SIO_WALL_SWEEP_EVOLVED_REL_TOL = 1.0e-6
C0_CHAR_WARNING_FEO_FRACTION = 0.0

# kg -> bar gauge for the snapshot pressure fields exposed to summaries.
_MBAR_TO_BAR = 1.0e-3

# Metal product species the per-hour summary surfaces.  Anything else
# (oxides, salts, halides) stays inside ``final_state`` -- the summary
# is the "operator readout" view, not the full ledger projection.
_METAL_PRODUCT_SPECIES: tuple[str, ...] = (
    "Fe",
    "Mg",
    "Al",
    "Ti",
    "Ca",
    "Cr",
    "Ni",
    "Co",
    "Mn",
    "Na",
    "K",
    "Si",
)
_CARRIER_TOKENS: dict[str, str] = {
    "N2": "N2",
    "AR": "Ar",
    "CO2": "CO2",
}


class RunnerError(RuntimeError):
    """Public exception for runner-level failures (config, load, IO).

    Physics-level failures bubble up through ``PyrolysisSimulator.step``
    and are caught in :meth:`PyrolysisRun.run` to populate the
    ``status=failed`` envelope.
    """


class EngineBugAbort(RunnerError):
    """Fatal runner abort for corrupted engine snapshots."""


class PresetRunnerError(RunnerError):
    """Runner error carrying whatever preset provenance is already known."""

    def __init__(
        self,
        message: str,
        *,
        provenance: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.provenance = dict(provenance or {})


# ----------------------------------------------------------------------
# Data loading helpers
# ----------------------------------------------------------------------


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        raise RunnerError(f"required data file missing: {path}")
    with path.open() as f:
        return yaml.safe_load(f) or {}


@dataclass(frozen=True)
class PresetRunSpec:
    feedstock_id: str
    hours: int
    mass_kg: float
    lab_schedule: Mapping[str, Any]
    lab_geometry: Mapping[str, Any]
    provenance: dict[str, Any]


def _load_preset_run_spec(path: Path, leg: str) -> PresetRunSpec:
    requested_leg = str(leg or "faithful").strip()
    base_provenance = {
        "path": str(path),
        "leg": requested_leg,
    }
    if not requested_leg:
        raise PresetRunnerError(
            "malformed_preset: --leg must be non-empty",
            provenance=base_provenance,
        )
    try:
        raw_bytes = path.read_bytes()
    except FileNotFoundError as exc:
        raise PresetRunnerError(
            f"malformed_preset: preset file missing: {path}",
            provenance=base_provenance,
        ) from exc
    except OSError as exc:
        raise PresetRunnerError(
            f"malformed_preset: could not read preset file {path}: {exc}",
            provenance=base_provenance,
        ) from exc

    digest = hashlib.sha256(raw_bytes).hexdigest()
    provenance = {
        **base_provenance,
        "digest": f"sha256:{digest}",
    }
    try:
        preset = yaml.safe_load(raw_bytes.decode("utf-8")) or {}
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise PresetRunnerError(
            f"malformed_preset: {path}: {exc}",
            provenance=provenance,
        ) from exc
    if not isinstance(preset, Mapping):
        raise PresetRunnerError(
            "malformed_preset: preset root must be a mapping",
            provenance=provenance,
        )

    pair = _preset_mapping(preset.get("pair"), "pair", provenance)
    if requested_leg not in pair:
        expected = ", ".join(sorted(str(name) for name in pair))
        raise PresetRunnerError(
            f"unknown_preset_leg: {requested_leg!r}; expected one of {expected}",
            provenance=provenance,
        )
    leg_block = _preset_mapping(
        pair.get(requested_leg),
        f"pair.{requested_leg}",
        provenance,
    )
    schedule = copy.deepcopy(
        _preset_mapping(preset.get("lab_schedule"), "lab_schedule", provenance)
    )
    geometry = copy.deepcopy(
        _preset_mapping(preset.get("lab_geometry"), "lab_geometry", provenance)
    )

    feedstock_id = _preset_text(
        leg_block.get("feedstock_id"),
        f"pair.{requested_leg}.feedstock_id",
        provenance,
    )
    schedule_id = _preset_text(
        leg_block.get("schedule_id"),
        f"pair.{requested_leg}.schedule_id",
        provenance,
    )
    geometry_id = _preset_text(
        leg_block.get("geometry_id"),
        f"pair.{requested_leg}.geometry_id",
        provenance,
    )
    if str(schedule.get("id") or "") != schedule_id:
        raise PresetRunnerError(
            "malformed_preset: "
            f"pair.{requested_leg}.schedule_id={schedule_id!r} "
            f"does not match lab_schedule.id={schedule.get('id')!r}",
            provenance=provenance,
        )
    if str(geometry.get("id") or "") != geometry_id:
        raise PresetRunnerError(
            "malformed_preset: "
            f"pair.{requested_leg}.geometry_id={geometry_id!r} "
            f"does not match lab_geometry.id={geometry.get('id')!r}",
            provenance=provenance,
        )

    _apply_leg_mitigation_to_schedule(
        schedule,
        leg_block.get("mitigation"),
        leg=requested_leg,
        provenance=provenance,
    )
    try:
        lab_geometry = parse_lab_geometry(
            geometry,
            allow_temperature_profiles=True,
        )
        if lab_geometry is None:
            raise LabGeometryError(
                "missing_lab_geometry",
                "preset lab_geometry is required",
            )
        required_profiles = tuple(
            surface.temperature_profile
            for surface in lab_geometry.surfaces
            if surface.temperature_profile
        )
        normalize_lab_schedule(
            schedule,
            required_surface_profiles=required_profiles,
        )
    except (LabGeometryError, LabScheduleValidationError, TypeError, ValueError) as exc:
        raise PresetRunnerError(
            f"malformed_preset: {exc}",
            provenance=provenance,
        ) from exc

    mass_g = lab_geometry.sample_mass_g
    if mass_g is None:
        raise PresetRunnerError(
            "malformed_preset: lab_geometry.sample.mass_g is required "
            "unless the runner grows an explicit preset mass contract",
            provenance=provenance,
        )
    duration_h = _preset_duration_h(
        leg_block.get("duration_h", schedule.get("duration_h")),
        f"pair.{requested_leg}.duration_h",
        provenance,
    )
    hours = int(duration_h)
    if not math.isclose(duration_h, float(hours), rel_tol=0.0, abs_tol=1e-9):
        raise PresetRunnerError(
            "malformed_preset: "
            f"pair.{requested_leg}.duration_h={duration_h!r} cannot map "
            "losslessly to runner integer --hours",
            provenance=provenance,
        )

    digests = preset.get("digests")
    if isinstance(digests, Mapping):
        for key, value in digests.items():
            if str(key).endswith("_digest"):
                provenance[str(key)] = str(value)
    for key in (
        "schema_version",
        "paper_id",
        "paper_citation_id",
        "measurement_id",
        "preset_kind",
        "extraction_status",
        "source_notes",
        "sticking_provenance",
        "comparison_contract",
    ):
        if key in preset:
            provenance[key] = copy.deepcopy(preset[key])
    mass_kg = float(mass_g) / 1000.0
    provenance.update(
        {
            "feedstock_id": feedstock_id,
            "duration_h": duration_h,
            "sample_mass_g": float(mass_g),
            "mass_kg": mass_kg,
            "schedule_id": schedule_id,
            "geometry_id": geometry_id,
        }
    )
    provenance.update(gram_lab_exposed_melt_area_bridge(geometry))
    return PresetRunSpec(
        feedstock_id=feedstock_id,
        hours=hours,
        mass_kg=mass_kg,
        lab_schedule=schedule,
        lab_geometry=geometry,
        provenance=provenance,
    )


def _preset_mapping(
    value: Any,
    field: str,
    provenance: Mapping[str, Any],
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PresetRunnerError(
            f"malformed_preset: {field} must be a mapping",
            provenance=provenance,
        )
    return value


def _preset_text(
    value: Any,
    field: str,
    provenance: Mapping[str, Any],
) -> str:
    text = str(value or "").strip()
    if not text:
        raise PresetRunnerError(
            f"malformed_preset: {field} is required",
            provenance=provenance,
        )
    return text


def _preset_duration_h(
    value: Any,
    field: str,
    provenance: Mapping[str, Any],
) -> float:
    try:
        duration_h = float(value)
    except (TypeError, ValueError) as exc:
        raise PresetRunnerError(
            f"malformed_preset: {field} must be a finite positive number",
            provenance=provenance,
        ) from exc
    if not math.isfinite(duration_h) or duration_h <= 0.0:
        raise PresetRunnerError(
            f"malformed_preset: {field} must be a finite positive number",
            provenance=provenance,
        )
    return duration_h


def _apply_leg_mitigation_to_schedule(
    schedule: dict[str, Any],
    mitigation: Any,
    *,
    leg: str,
    provenance: Mapping[str, Any],
) -> None:
    if mitigation in (None, "", "none"):
        return
    if not isinstance(mitigation, Mapping):
        raise PresetRunnerError(
            f"malformed_preset: pair.{leg}.mitigation must be 'none' or a mapping",
            provenance=provenance,
        )
    pO2_cover = mitigation.get("pO2_cover")
    if isinstance(pO2_cover, Mapping) and bool(pO2_cover.get("enabled", False)):
        schedule["pO2_cover"] = copy.deepcopy(dict(pO2_cover))
    elif pO2_cover not in (None, False):
        raise PresetRunnerError(
            f"malformed_preset: pair.{leg}.mitigation.pO2_cover must be a mapping",
            provenance=provenance,
        )

    shuttle = mitigation.get("alkali_shuttle_deconfliction")
    if isinstance(shuttle, Mapping) and bool(shuttle.get("enabled", False)):
        raise PresetRunnerError(
            "unsupported_preset_mitigation: "
            f"pair.{leg}.mitigation.alkali_shuttle_deconfliction",
            provenance=provenance,
        )
    known = {"pO2_cover", "alkali_shuttle_deconfliction"}
    unknown_enabled = [
        str(key)
        for key, value in mitigation.items()
        if key not in known and value not in (None, False, "", "none")
    ]
    if unknown_enabled:
        raise PresetRunnerError(
            "unsupported_preset_mitigation: "
            + ", ".join(sorted(unknown_enabled)),
            provenance=provenance,
        )


def _resolve_kernel_commit_sha() -> str:
    """Best-effort kernel commit SHA.

    Returns the current ``HEAD`` of the repo as the kernel marker.  The
    kernel lives inside the repo at HEAD, so a per-engine SHA would
    duplicate this value today.  Tests inject ``kernel_commit_sha`` via
    ``PyrolysisRun.run_metadata_overrides`` so fixtures stay stable
    when the repo SHA changes.

    Returns ``"unknown"`` when git is unreachable (CI without a worktree,
    container with stripped ``.git``, etc.) so the runner never raises
    purely for failure to read git metadata.
    """

    repo_root = Path(__file__).resolve().parent.parent
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, FileNotFoundError):
        return "unknown"
    if result.returncode != 0:
        return "unknown"
    return result.stdout.strip() or "unknown"


def _knudsen_regime_diagnostic_from_sim(
    sim: PyrolysisSimulator,
) -> dict[str, Any]:
    condensation_model = getattr(sim, "_condensation_model", None)
    if condensation_model is None:
        return {}
    diagnostic = getattr(
        condensation_model, "last_knudsen_regime_diagnostic", {}) or {}
    if not isinstance(diagnostic, Mapping):
        return {}
    return dict(diagnostic)


def _load_engines_config(path: Optional[Path]) -> dict:
    """Load the optional engines config file.

    Format is pinned forward by Goal #19 ``PER-INTENT-ENGINE-CONFIG``.
    Today the file is consumed only for the run_metadata
    ``engines_used`` echo: any ``intent: provider_id`` mapping under
    ``engines:`` is propagated verbatim into the output document so
    downstream pipelines can see which providers the operator
    requested.  No simulator wiring branches on the value yet --
    Goal #19 owns that.

    Missing path or missing file -> returns ``{}`` so the runner
    stays usable before Goal #19 lands.  A malformed file raises
    :class:`RunnerError` -- a typo today is the same kind of bug it
    will be tomorrow.
    """

    if path is None:
        return {}
    if not path.exists():
        raise RunnerError(f"engines config not found: {path}")
    try:
        data = _load_yaml(path)
    except yaml.YAMLError as exc:
        raise RunnerError(f"engines config malformed ({path}): {exc}") from exc
    engines = data.get("engines") or {}
    if not isinstance(engines, Mapping):
        raise RunnerError(
            f"engines config {path} must contain a mapping under 'engines:'"
        )
    return {str(intent): str(provider) for intent, provider in engines.items()}


def _deep_merge_setpoints(
    base: Mapping[str, Any],
    patch: Mapping[str, Any],
    *,
    _top_level: bool = True,
) -> dict[str, Any]:
    # Match the str(key) coercion the merge applies below, so a non-string
    # top-level key that stringifies to "chemistry_kernel" cannot slip past
    # this guard and overwrite kernel config.
    if _top_level and any(str(key) == "chemistry_kernel" for key in patch):
        raw_kernel_patch = patch.get("chemistry_kernel")
        if not isinstance(raw_kernel_patch, Mapping):
            raise RunnerError("setpoints_patch.chemistry_kernel must be a mapping")
        extra_keys = (
            set(map(str, raw_kernel_patch))
            - _SETPOINTS_PATCH_CHEMISTRY_KERNEL_KEYS
        )
        if extra_keys:
            raise RunnerError(
                "setpoints_patch may only contain diagnostic "
                f"chemistry_kernel.{OXYGEN_SINK_CHANNEL_MODE_KEY}; "
                "use fallback flags instead"
            )
        try:
            normalize_chemistry_kernel_config(raw_kernel_patch)
        except (TypeError, ValueError) as exc:
            raise RunnerError(str(exc)) from exc
    merged = dict(base)
    for key, value in patch.items():
        current = merged.get(key)
        if isinstance(current, Mapping) and isinstance(value, Mapping):
            merged[str(key)] = _deep_merge_setpoints(
                current, value, _top_level=False
            )
        else:
            merged[str(key)] = copy.deepcopy(value)
    return merged


def _additives_with_c3_alkali_dosing(
    additives_kg: Mapping[str, float],
    setpoints: Mapping[str, Any],
) -> dict[str, float]:
    additives = {str(k): float(v) for k, v in dict(additives_kg).items()}
    dosing_by_species = _c3_alkali_dosing_kg_by_species(setpoints)
    for species, dose_kg in dosing_by_species.items():
        raw_additive_kg = additives.get(species, 0.0)
        if raw_additive_kg > 0.0 and not math.isclose(
            raw_additive_kg, dose_kg, rel_tol=0.0, abs_tol=1.0e-12
        ):
            key = f"{species}_kg"
            raise RunnerError(
                f"campaigns.C3.alkali_dosing.{key} conflicts with "
                f"additives_kg[{species!r}]"
            )
    return additives


def _c3_alkali_dosing_kg_by_species(
    setpoints: Mapping[str, Any],
) -> dict[str, float]:
    campaigns = setpoints.get("campaigns", {})
    if not isinstance(campaigns, Mapping):
        return {}
    c3 = campaigns.get("C3", {})
    if not isinstance(c3, Mapping):
        return {}
    dosing = c3.get("alkali_dosing", {})
    if dosing in (None, {}):
        return {}
    if not isinstance(dosing, Mapping):
        raise RunnerError("campaigns.C3.alkali_dosing must be a mapping")

    doses: dict[str, float] = {}
    for key, species in (("Na_kg", "Na"), ("K_kg", "K")):
        if key not in dosing or dosing[key] is None:
            continue
        try:
            dose_kg = float(dosing[key])
        except (TypeError, ValueError) as exc:
            raise RunnerError(
                f"campaigns.C3.alkali_dosing.{key} must be numeric"
            ) from exc
        if not math.isfinite(dose_kg) or dose_kg < 0.0:
            raise RunnerError(
                f"campaigns.C3.alkali_dosing.{key} must be finite and non-negative"
            )
        if dose_kg <= C3_ALKALI_DOSING_ZERO_LEVEL_KG_BY_SPECIES[species]:
            continue
        doses[species] = dose_kg
    return doses


def _canonical_runtime_campaign_overrides(
    *,
    runtime_campaign_overrides: Mapping[str, Mapping[str, Any]] | None,
    setpoints_overrides: Mapping[str, Mapping[str, Any]] | None,
) -> dict[str, dict[str, Any]]:
    if (
        runtime_campaign_overrides is not None
        and setpoints_overrides is not None
        and dict(runtime_campaign_overrides) != dict(setpoints_overrides)
    ):
        raise ValueError(
            "runtime_campaign_overrides conflicts with deprecated "
            "setpoints_overrides alias"
        )
    source = (
        runtime_campaign_overrides
        if runtime_campaign_overrides is not None
        else setpoints_overrides
    )
    if source is None:
        return {}
    return {str(campaign): dict(fields) for campaign, fields in source.items()}


# ----------------------------------------------------------------------
# Public dataclass: runner configuration
# ----------------------------------------------------------------------


@dataclass
class PyrolysisRun:
    """Configuration for a single deterministic simulator run.

    Attributes mirror the Goal #18 CHECKLIST exactly so the CLI flags
    map 1:1 to dataclass fields.

    ``setpoints_patch`` is a compile-time deep merge onto the base
    setpoints before simulator construction. ``runtime_campaign_overrides``
    is a mapping ``{campaign_name: {field: value}}`` -- written straight
    onto ``CampaignManager.overrides`` after batch load.
    """

    feedstock_id: str
    campaign: str = "C0"
    hours: int = 24
    engines: dict[str, str] = field(default_factory=dict)
    additives_kg: dict[str, float] = field(default_factory=dict)
    mass_kg: float = 1000.0
    backend_name: str = ANALYTICAL_BACKEND_SERIALIZATION_TOKEN
    setpoints_patch: Mapping[str, Any] = field(default_factory=dict)
    runtime_campaign_overrides: Mapping[str, Mapping[str, Any]] | None = None
    setpoints_overrides: Mapping[str, Mapping[str, Any]] | None = None
    lab_schedule: Mapping[str, Any] | None = None
    track: str = "pyrolysis"
    c5_enabled: bool = False
    mre_target_species: str = ""
    mre_max_voltage_V: float = 0.0
    allow_fallback_vapor: bool = False
    allow_unmeasured_alpha_fallback: bool = False
    unmeasured_alpha_fallback_species: tuple[str, ...] = ()
    chemistry_kernel: Mapping[str, Any] | None = None
    force_builtin_vapor_pressure: bool = False
    sio_start_temperature_c: float | None = None
    sio_hold_temperature_c: float | None = None
    sio_ramp_c_per_hr: float | None = None
    sio_liner_temperature_c: float | None = None
    sio_pO2_mbar: float | None = None
    feedstocks_path: Optional[Path] = None
    setpoints_path: Optional[Path] = None
    vapor_pressures_path: Optional[Path] = None
    # Overrides for the run_metadata block -- accepted so fixture-driven
    # tests pin started_at_utc + kernel_commit_sha to deterministic
    # values.  Production CLI invocations leave both empty and pick up
    # the live values.
    run_metadata_overrides: dict[str, Any] = field(default_factory=dict)
    reduced_real_cache: Mapping[str, Any] | None = None
    strict_result_contract: bool = field(init=False, default=True)

    def __post_init__(self) -> None:
        # Fold the `internal-analytical` display alias onto the stable `stub`
        # token so the serialized run metadata (`"backend"`) and the fidelity-
        # vocabulary backend-token translator both see the legacy token.
        self.backend_name = canonical_backend_name(self.backend_name)
        if int(self.hours) < 0:
            raise RunnerError(
                f"invalid hours: hours must be >= 0; got {int(self.hours)}"
            )
        overrides = _canonical_runtime_campaign_overrides(
            runtime_campaign_overrides=self.runtime_campaign_overrides,
            setpoints_overrides=self.setpoints_overrides,
        )
        self.runtime_campaign_overrides = overrides
        self.setpoints_overrides = overrides
    # ------------------------------------------------------------------
    # Run entry points
    # ------------------------------------------------------------------

    def run(self) -> dict:
        """Execute the run and return the fully-specified JSON dict.

        ``RunExecutor`` turns per-step failures into ``status=failed``
        executions, whose detail-construction failures receive a reduced
        failure envelope. Unexpected detail errors for an otherwise non-failed
        execution can still propagate. CLI argument parsing happens before this
        method; argparse usage errors exit on stderr without producing JSON.
        """

        lab_area_bridge = self._lab_area_bridge()
        if self._has_sio_pre_run_controls() or lab_area_bridge:
            session = SimSession()
            try:
                session.start(self._session_config())
            except CampaignPressureSetpointRefusal as exc:
                execution = RunExecutor().execute_session(
                    session,
                    hours=int(self.hours),
                    initial_refusal=exc,
                )
            else:
                self._apply_lab_area_bridge(session.simulator, lab_area_bridge)
                self._apply_sio_pre_run_controls(session.simulator)
                execution = RunExecutor().execute_session(
                    session,
                    hours=int(self.hours),
                )
        else:
            config = self._session_config()
            execution = RunExecutor().execute(config)
        document = self._build_output(execution)
        execution.session._set_result_document(document)
        return document

    def _start_session(self) -> SimSession:
        session = SimSession()
        session.start(self._session_config())
        return session

    def _run_session(self, session: SimSession) -> dict:
        self._apply_lab_area_bridge(session.simulator, self._lab_area_bridge())
        self._apply_sio_pre_run_controls(session.simulator)
        execution = RunExecutor().execute_session(session, hours=int(self.hours))
        document = self._build_output(execution)
        execution.session._set_result_document(document)
        return document

    def _lab_area_bridge(self) -> dict[str, Any]:
        lab_geometry = (
            self.setpoints_patch.get("lab_geometry")
            if isinstance(self.setpoints_patch, Mapping)
            else None
        )
        try:
            return gram_lab_exposed_melt_area_bridge(lab_geometry)
        except LabGeometryError as exc:
            raise RunnerError(str(exc)) from exc

    @staticmethod
    def _apply_lab_area_bridge(
        sim: PyrolysisSimulator,
        bridge: Mapping[str, Any],
    ) -> None:
        if not bridge:
            return
        sim.melt.melt_surface_area_m2 = float(
            bridge["effective_exposed_area_m2"]
        )

    def _has_sio_pre_run_controls(self) -> bool:
        return any(
            value is not None
            for value in (
                self.sio_start_temperature_c,
                self.sio_hold_temperature_c,
                self.sio_ramp_c_per_hr,
                self.sio_liner_temperature_c,
                self.sio_pO2_mbar,
            )
        )

    def _apply_sio_pre_run_controls(self, sim: PyrolysisSimulator) -> None:
        if not self._has_sio_pre_run_controls():
            return
        _prepare_sio_campaign_start(
            sim,
            t_low_c=self.sio_start_temperature_c,
            t_hold_c=self.sio_hold_temperature_c,
            ramp_c_per_hr=self.sio_ramp_c_per_hr,
        )
        _apply_sio_wall_sweep_controls(
            sim,
            liner_temperature_c=self.sio_liner_temperature_c,
            pO2_mbar=self.sio_pO2_mbar,
        )

    # ------------------------------------------------------------------
    # Session construction
    # ------------------------------------------------------------------

    def _session_config(self) -> SimSessionConfig:
        bundle = self._load_config_bundle()
        feedstocks = bundle.feedstocks
        setpoints = copy.deepcopy(bundle.setpoints)
        setpoints = _deep_merge_setpoints(setpoints, self.setpoints_patch)
        if self.chemistry_kernel:
            try:
                diagnostic_kernel_config = normalize_chemistry_kernel_config(
                    self.chemistry_kernel
                )
            except (TypeError, ValueError) as exc:
                raise RunnerError(str(exc)) from exc
            extra_keys = (
                set(diagnostic_kernel_config)
                - _SETPOINTS_PATCH_CHEMISTRY_KERNEL_KEYS
            )
            if extra_keys:
                raise RunnerError(
                    "PyrolysisRun.chemistry_kernel only accepts diagnostic "
                    f"{OXYGEN_SINK_CHANNEL_MODE_KEY}"
                )
            setpoints = _deep_merge_setpoints(
                setpoints,
                {"chemistry_kernel": diagnostic_kernel_config},
            )
        if (
            self.allow_fallback_vapor
            or self.force_builtin_vapor_pressure
            or self.allow_unmeasured_alpha_fallback
            or self.unmeasured_alpha_fallback_species
        ):
            setpoints = dict(setpoints)
            kernel_config = dict(setpoints.get("chemistry_kernel", {}) or {})
            if self.allow_fallback_vapor or self.force_builtin_vapor_pressure:
                kernel_config["allow_fallback_vapor"] = True
            if self.allow_unmeasured_alpha_fallback:
                kernel_config["allow_unmeasured_alpha_fallback"] = True
            if self.unmeasured_alpha_fallback_species:
                kernel_config["unmeasured_alpha_fallback_species"] = list(
                    self.unmeasured_alpha_fallback_species
                )
            setpoints["chemistry_kernel"] = kernel_config
        vapor_pressures = bundle.vapor_pressures
        campaign_name = SIO_YIELD_CAMPAIGN_ALIASES.get(
            self.campaign, self.campaign)
        additives_kg = _additives_with_c3_alkali_dosing(
            self.additives_kg,
            setpoints,
        )
        mass_kg = _positive_mass_kg(self.mass_kg)
        runtime_overrides = {
            str(campaign): dict(fields)
            for campaign, fields in dict(self.runtime_campaign_overrides).items()
        }
        if self.lab_schedule is not None:
            campaign_overrides = runtime_overrides.setdefault(campaign_name, {})
            if LAB_SCHEDULE_OVERRIDE_KEY in campaign_overrides:
                raise RunnerError(
                    "lab_schedule conflicts with runtime_campaign_overrides "
                    f"{campaign_name}.{LAB_SCHEDULE_OVERRIDE_KEY}"
                )
            campaign_overrides[LAB_SCHEDULE_OVERRIDE_KEY] = dict(self.lab_schedule)
        try:
            CampaignManager.validate_runtime_campaign_overrides(runtime_overrides)
        except ValueError as exc:
            raise RunnerError(str(exc)) from exc
        return SimSessionConfig(
            feedstock_id=self.feedstock_id,
            feedstocks=feedstocks,
            setpoints=setpoints,
            vapor_pressures=vapor_pressures,
            materials=bundle.materials,
            campaign=campaign_name,
            backend_name=self.backend_name,
            reduced_real_cache=self.reduced_real_cache,
            backend_policy=BackendSelectionPolicy.RUNNER_STRICT,
            hours=int(self.hours),
            mass_kg=mass_kg,
            additives_kg=additives_kg,
            runtime_campaign_overrides=runtime_overrides,
            track=self.track,
            c5_enabled=self.c5_enabled,
            mre_target_species=self.mre_target_species,
            mre_max_voltage_V=self.mre_max_voltage_V,
            campaigns_elapsed=self.run_metadata_overrides.get(
                "campaigns_elapsed", 1.0
            ),
            unavailable_error_cls=RunnerError,
            force_builtin_vapor_pressure=(
                _force_builtin_vapor_pressure
                if self.force_builtin_vapor_pressure
                else None
            ),
        )

    def _load_config_bundle(self) -> ConfigBundle:
        try:
            return load_config_bundle(
                DATA_DIR,
                feedstocks_path=self.feedstocks_path,
                setpoints_path=self.setpoints_path,
                vapor_pressures_path=self.vapor_pressures_path,
            )
        except FileNotFoundError as exc:
            raise RunnerError(str(exc)) from exc

    def _load_feedstocks(self) -> dict:
        path = self.feedstocks_path or (DATA_DIR / "feedstocks.yaml")
        return _load_yaml(path)

    def _load_setpoints(self) -> dict:
        path = self.setpoints_path or (DATA_DIR / "setpoints.yaml")
        return _load_yaml(path)

    def _load_vapor_pressures(self) -> dict:
        path = self.vapor_pressures_path or (DATA_DIR / "vapor_pressures.yaml")
        return _load_yaml(path)

    # ------------------------------------------------------------------
    # Output assembly
    # ------------------------------------------------------------------

    def _build_output(
        self,
        execution: RunExecution,
    ) -> dict:
        if execution.envelope_detail_unavailable:
            return self._minimal_failure_output(execution)
        try:
            return self._build_output_detail(execution)
        except Exception as exc:  # noqa: BLE001 -- failure reporting must survive
            if execution.status not in {"failed", "refused"}:
                raise
            return self._minimal_failure_output(
                execution,
                detail=f"envelope detail unavailable: {_safe_exception_text(exc)}",
            )

    def _minimal_failure_output(
        self,
        execution: RunExecution,
        *,
        detail: str | None = None,
    ) -> dict[str, Any]:
        detail = detail or execution.envelope_detail_unavailable
        error_message = execution.error_message
        if detail:
            error_message = f"{error_message}\n{detail}"
        return _runner_failure_result(
            error=RunnerError(execution.error_message),
            feedstock_id=self.feedstock_id,
            campaign=self.campaign,
            hours=self.hours,
            mass_kg=self.mass_kg,
            additives_kg=self.additives_kg,
            track=self.track,
            backend_name=self.backend_name,
            engines=self.engines,
            metadata_overrides=self.run_metadata_overrides,
            reason=execution.reason,
            status=str(execution.status or "failed"),
            execution=execution,
            engines_used=self._safe_failure_engines_used(execution),
            error_message_override=error_message,
        )

    def _build_output_detail(
        self,
        execution: RunExecution,
    ) -> dict:
        sim = execution.simulator
        metadata_overrides = dict(self.run_metadata_overrides)
        started_at_utc = metadata_overrides.pop(
            "started_at_utc",
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        kernel_commit_sha = metadata_overrides.pop(
            "kernel_commit_sha", _resolve_kernel_commit_sha()
        )

        engines_used = self._engines_used(sim)
        session_config = getattr(execution.session, "_config", None)
        if session_config is None:
            raise RuntimeError("run execution session missing config")
        applied_additives_kg = dict(session_config.additives_kg)

        run_metadata = {
            "schema_version": RUNNER_SCHEMA_VERSION,
            "feedstock_id": self.feedstock_id,
            "campaign": self.campaign,
            "hours_requested": int(self.hours),
            "hours_completed": int(sim.melt.hour),
            "mass_kg": float(self.mass_kg),
            "additives_kg": {
                k: float(v) for k, v in sorted(applied_additives_kg.items())
            },
            "track": self.track,
            "backend": self.backend_name,
            "started_at_utc": started_at_utc,
            "engines_used": engines_used,
            "kernel_commit_sha": kernel_commit_sha,
        }
        c3_credit_dose_kg = _c3_alkali_dosing_kg_by_species(
            session_config.setpoints
        )
        c3_credit_drawn_kg = dict(
            getattr(sim, "_c3_alkali_credit_drawn_kg_by_species", {}) or {}
        )
        c3_credit_outstanding_kg = (
            sim._c3_alkali_credit_outstanding_kg_by_species()
        )
        c3_credit_species = (
            set(c3_credit_dose_kg)
            | set(c3_credit_drawn_kg)
            | set(c3_credit_outstanding_kg)
        )
        if c3_credit_species:
            ordered_species = sorted(c3_credit_species)
            run_metadata["c3_alkali_credit_dose_kg_by_species"] = {
                species: float(c3_credit_dose_kg.get(species, 0.0))
                for species in ordered_species
            }
            run_metadata["c3_alkali_credit_drawn_kg_by_species"] = {
                species: float(c3_credit_drawn_kg.get(species, 0.0))
                for species in ordered_species
            }
            run_metadata["c3_alkali_credit_outstanding_kg_by_species"] = {
                species: float(c3_credit_outstanding_kg.get(species, 0.0))
                for species in ordered_species
            }
        if execution.reduced_real_cache:
            run_metadata["reduced_real_cache"] = _json_safe(
                execution.reduced_real_cache
            )
        lab_area_bridge = self._lab_area_bridge()
        if lab_area_bridge:
            run_metadata.update(_json_safe(lab_area_bridge))
        # Anything left in metadata_overrides is propagated verbatim --
        # callers can stuff extra provenance (CI run id, etc.) without
        # the runner needing to know about it.
        for key, value in metadata_overrides.items():
            run_metadata[str(key)] = value
        run_metadata["campaigns_elapsed"] = float(execution.campaigns_elapsed)
        run_metadata.update(
            {
                "backend_status": str(execution.backend_status),
                "backend_authoritative": bool(execution.backend_authoritative),
            }
        )
        run_metadata.update(
            canonicalize_fidelity_emission(
                backend_name=self.backend_name,
                backend_status=execution.backend_status,
                backend_authoritative=execution.backend_authoritative,
            )
        )

        final_state = _final_state_from_ledger(sim)
        refusal_diagnostic = dict(execution.refusal_diagnostic or {})
        if refusal_diagnostic:
            run_metadata["refusal_diagnostic"] = _json_safe(
                refusal_diagnostic
            )
        knudsen_diagnostic = dict(
            refusal_diagnostic
            if execution.reason == KNUDSEN_REFUSAL_REASON
            else _knudsen_regime_diagnostic_from_sim(sim)
        )
        if knudsen_diagnostic:
            run_metadata["knudsen_regime_diagnostic"] = _json_safe(
                knudsen_diagnostic)
        run_metadata["pressure_coating_pareto_diagnostic"] = _json_safe(
            pressure_coating_pareto_diagnostic(sim, execution.per_hour)
        )
        c3_na_hold_adjustment = dict(
            getattr(sim, "_last_c3_na_hold_adjustment", {}) or {}
        )
        if c3_na_hold_adjustment:
            run_metadata["c3_na_hold_adjustment"] = _json_safe(
                c3_na_hold_adjustment
            )
        cost_rollup_diagnostic = build_cost_rollup_diagnostic(
            cost_ledger=sim.cost_ledger,
            per_hour=execution.per_hour,
            products_kg=sim.product_ledger(),
            pumping_context=pumping_context_from_sim(sim, execution.snapshots),
            snapshots=execution.snapshots,
        )
        cost_rollup_diagnostic["price_basis"] = (
            "legacy_placeholder_awaiting_owner_ratification"
        )
        run_metadata["cost_rollup_diagnostic"] = _json_safe(cost_rollup_diagnostic)
        sim.record.cost_rollup = dict(run_metadata["cost_rollup_diagnostic"])

        # Shuttle refusal log (autoreview r3 P2, 2026-05-27): every
        # ``status='refused'`` returned by the C3 K-shuttle / Na-shuttle
        # kernel dispatch is accumulated on
        # ``sim._shuttle_refusal_history``.  Surface it as an explicit
        # top-level field so operators see the recipe step the
        # thermodynamic gate rejected -- silently dropping the dispatch
        # used to leave the run looking ``ok``/`partial`` with the C3
        # cleanup quietly missing.  Empty list when no refusals.
        shuttle_refusal_history = list(
            getattr(sim, "_shuttle_refusal_history", []) or [])
        melt_redox_gate_floor_fallback_engagement = (
            _melt_redox_gate_floor_fallback_engagement(sim)
        )
        degraded_path_engagement = _degraded_path_engagement(sim)
        pO2_enforcement_by_hour = [
            dict(row["pO2_enforcement"])
            for row in execution.per_hour
            if isinstance(row, Mapping) and isinstance(row.get("pO2_enforcement"), Mapping)
        ]
        status, reason, error_message = _status_with_mass_balance_invariant(
            execution,
            strict_result_contract=self.strict_result_contract,
        )

        payload = {
            "schema_version": RUNNER_SCHEMA_VERSION,
            "run_metadata": run_metadata,
            "final_state": final_state,
            "final": _final_summary_report(final_state, execution),
            "stage_purity_report": stage_purity_report(sim.train),
            "vapor_pressure_source_report": _vapor_pressure_source_report(sim),
            "shuttle_refusal_history": _json_safe(shuttle_refusal_history),
            "c7_product_report": _json_safe(_c7_product_report(sim)),
            "c7_refusal_diagnostic": _json_safe(_c7_refusal_diagnostic(sim)),
            "degraded_path_engagement": degraded_path_engagement,
            "melt_redox_gate_floor_fallback_engagement": (
                melt_redox_gate_floor_fallback_engagement
            ),
            "pO2_enforcement_by_hour": _json_safe(pO2_enforcement_by_hour),
            "per_hour_summary": list(execution.per_hour),
            "shadow_trace": list(execution.shadow_trace),
            "status": status,
            "reason": reason,
            "error_message": error_message,
        }
        c0_char_diagnostic = _c0_char_diagnostic(
            sim,
            execution.snapshots,
            feedstock_id=self.feedstock_id,
        )
        if c0_char_diagnostic:
            run_metadata["c0_char_diagnostic"] = _json_safe(
                c0_char_diagnostic
            )
        return payload

    def _engines_used(self, sim: PyrolysisSimulator) -> dict[str, object]:
        """Build the run_metadata.engines_used dict.

        Goal #18 CHECKLIST item 3 names ``engines_used:
        {intent: provider_id}`` as the contract.  We expose three
        related projections so downstream tooling can pick the level
        it cares about:

        * ``active`` -- the flat ``{intent: provider_id}`` view the
          spec literally names, sourced from the authoritative slot of
          each intent registered with the kernel.
        * ``requested`` -- the operator's ``--engine`` / engines yaml
          overrides (forward-compat for Goal #19).
        * ``registry`` -- the full kernel ``capability_summary``
          (``{intent: {authoritative, fallback, shadows}}``) so a
          shadow-vs-authoritative audit doesn't need to re-walk the
          kernel.
        """

        echoed = {k: v for k, v in self.engines.items()}
        kernel = getattr(sim, "_chem_kernel", None)
        if kernel is None:
            return {"active": {}, "requested": echoed, "registry": {}}
        try:
            registry = kernel.registry
        except AttributeError:
            capability = {}
        else:
            capability = registry.capability_summary()
        internal_intents = {"backend_equilibrium"}
        if not getattr(sim, "_c7_product_report", None):
            internal_intents.add("ca_aluminothermic_step")
        if not self._requests_o2_bubbler_runtime(sim):
            internal_intents.add("oxygen_bubbler")
        capability = {
            intent: slots
            for intent, slots in capability.items()
            if intent not in internal_intents
        }
        active = {
            intent: slots["authoritative"]
            for intent, slots in capability.items()
            if isinstance(slots, Mapping) and slots.get("authoritative")
        }
        return {
            "active": active,
            "requested": echoed,
            "registry": _json_safe(capability),
        }

    def _safe_failure_engines_used(
        self,
        execution: RunExecution,
    ) -> dict[str, object]:
        try:
            return self._engines_used(execution.simulator)
        except Exception:  # noqa: BLE001 -- failure envelopes must survive
            return {
                "active": {},
                "requested": {k: v for k, v in self.engines.items()},
                "registry": {},
            }

    def _requests_o2_bubbler_runtime(self, sim: PyrolysisSimulator) -> bool:
        manager = getattr(sim, "campaign_mgr", None)
        if manager is None:
            return False
        for campaign in CampaignPhase:
            controls = manager.o2_bubbler_controls(campaign)
            raw_rate = controls.get("o2_bubbler_kg_per_hr")
            try:
                rate = float(raw_rate)
            except (TypeError, ValueError):
                continue
            if math.isfinite(rate) and rate > 0.0:
                return True
        return False


# ----------------------------------------------------------------------
# Per-hour summary builder (shared with web stream)
# ----------------------------------------------------------------------


def _empty_vapor_pressure_source_report() -> dict[str, object]:
    return {
        "species": {},
        "summary": {},
        "total_species": 0,
        "vapor_pressure_backend_status": "",
        "vapor_pressure_backend_status_summary": {},
        "vapor_pressure_backend_status_reason": "",
        "vapor_pressure_fallback_source": "",
        "authoritative_for_requested_vapor_pressure": None,
    }


def _vapor_pressure_source_report(sim: PyrolysisSimulator) -> dict[str, object]:
    source_by_species = {
        str(species): str(source)
        for species, source in sorted(
            (getattr(sim, "_last_vapor_pressures_source", {}) or {}).items()
        )
    }
    total = len(source_by_species)
    counts = Counter(source_by_species.values())
    diagnostics = dict(getattr(sim, "_last_backend_diagnostics", {}) or {})
    facet_status = str(
        diagnostics.get("vapor_pressure_backend_status") or ""
    ).strip()
    report: dict[str, object] = {
        "species": source_by_species,
        "summary": {
            source: {
                "count": count,
                "percentage": round(count / total * 100.0, 6) if total else 0.0,
            }
            for source, count in sorted(counts.items())
        },
        "total_species": total,
        "vapor_pressure_backend_status": facet_status,
        "vapor_pressure_backend_status_summary": (
            {
                facet_status: {
                    "count": total,
                    "percentage": 100.0 if total else 0.0,
                }
            }
            if facet_status
            else {}
        ),
        "vapor_pressure_backend_status_reason": diagnostics.get(
            "vapor_pressure_backend_status_reason",
            "",
        ),
        "vapor_pressure_fallback_source": diagnostics.get(
            "vapor_pressure_fallback_source",
            "",
        ),
        "authoritative_for_requested_vapor_pressure": diagnostics.get(
            "authoritative_for_requested_vapor_pressure",
            None,
        ),
    }
    return report


def _finite_export_float(value: Any, *, field: str) -> float:
    try:
        amount = float(value)
    except (TypeError, ValueError) as exc:
        raise RunnerError(f"non-numeric {field}: {value!r}") from exc
    if not math.isfinite(amount):
        raise RunnerError(f"non-finite {field}: {value!r}")
    return amount


def _positive_mass_kg(value: Any, *, field: str = "mass_kg") -> float:
    try:
        mass_kg = float(value)
    except (TypeError, ValueError) as exc:
        raise RunnerError(
            f"{ZERO_INPUT_BASIS_BREACH}: {field} must be numeric; got {value!r}"
        ) from exc
    if not math.isfinite(mass_kg):
        raise RunnerError(
            f"{ZERO_INPUT_BASIS_BREACH}: {field} must be finite; got {value!r}"
        )
    if mass_kg <= 0.0:
        raise RunnerError(
            f"{ZERO_INPUT_BASIS_BREACH}: {field} must be > 0 kg; got {mass_kg:.12g}"
        )
    return mass_kg


def _worst_execution_mass_balance_pct(execution: RunExecution) -> float | None:
    values: list[float] = []
    snapshots = tuple(getattr(execution, "snapshots", ()) or ())
    for index, snapshot in enumerate(snapshots):
        raw = getattr(snapshot, "mass_balance_error_pct", None)
        if raw is None:
            raise EngineBugAbort(
                "mass_balance_key_missing_in_snapshot: "
                f"source=execution.snapshots[{index}] "
                "key=mass_balance_error_pct"
            )
        values.append(
            _coerce_mass_balance_pct(
                raw, source=f"execution.snapshots[{index}]",
            )
        )

    if not values:
        per_hour = tuple(getattr(execution, "per_hour", ()) or ())
        for index, row in enumerate(per_hour):
            if not isinstance(row, Mapping):
                raise EngineBugAbort(
                    f"mass_balance_snapshot_malformed: source=per_hour_summary[{index}]"
                )
            values.append(
                _required_mass_balance_value(
                    row,
                    "mass_balance_pct",
                    source=f"per_hour_summary[{index}]",
                )
            )

    if not values:
        completed_hours = int(
            getattr(getattr(execution, "simulator", None), "melt", None).hour
            if getattr(getattr(execution, "simulator", None), "melt", None)
            is not None
            else 0
        )
        if completed_hours > 0:
            raise EngineBugAbort(
                "mass_balance_evidence_missing_for_completed_run"
            )
        return None
    # Closure is a per-hour invariant, so later cancellation cannot erase the
    # largest absolute excursion observed during the run.
    return max(values, key=abs)


def _execution_mass_balance_error_category(execution: RunExecution) -> str:
    snapshots = tuple(getattr(execution, "snapshots", ()) or ())
    for snapshot in snapshots:
        category = str(
            getattr(snapshot, "mass_balance_error_category", "") or ""
        )
        if category:
            return category
    per_hour = tuple(getattr(execution, "per_hour", ()) or ())
    for row in per_hour:
        if not isinstance(row, Mapping):
            continue
        category = str(row.get("mass_balance_error_category", "") or "")
        if category:
            return category
    return ""


def _coerce_mass_balance_pct(value: Any, *, source: str) -> float:
    try:
        pct = float(value)
    except (TypeError, ValueError) as exc:
        raise EngineBugAbort(
            f"mass_balance_key_non_numeric_in_snapshot: source={source} "
            "key=mass_balance_error_pct"
        ) from exc
    if not math.isfinite(pct):
        raise EngineBugAbort(
            f"mass_balance_key_nonfinite_in_snapshot: source={source} "
            "key=mass_balance_error_pct"
        )
    return pct


def _status_with_mass_balance_invariant(
    execution: RunExecution,
    *,
    strict_result_contract: bool,
) -> tuple[str, str, str]:
    status = str(execution.status)
    reason = str(execution.reason)
    error_message = str(execution.error_message)
    if status not in {"ok", "partial"}:
        return status, reason, error_message

    category = _execution_mass_balance_error_category(execution)
    if category:
        reason = category
        error_message = f"mass_balance_error_category: {category}"
        return "failed", reason, error_message

    mass_balance_pct = _worst_execution_mass_balance_pct(execution)
    if (
        mass_balance_pct is None
        or abs(mass_balance_pct) <= RUNNER_MASS_BALANCE_LIMIT_PCT
    ):
        if strict_result_contract:
            config = getattr(getattr(execution, "session", None), "_config", None)
            if config is None:
                raise RuntimeError("run execution session missing config")
            snapshots = tuple(getattr(execution, "snapshots", ()) or ())
            for snapshot in snapshots:
                drift = dict(
                    getattr(snapshot, "metal_projection_drift_kg", {}) or {}
                )
                if drift:
                    reason = "metal_projection_drift"
                    species = ", ".join(sorted(str(key) for key in drift))
                    error_message = (
                        f"{reason}: ledger and UI projection differ for {species}"
                    )
                    return "failed", reason, error_message
        return status, reason, error_message

    reason = "mass_balance_closure_breach"
    error_message = (
        f"{reason}: {mass_balance_pct:.12g}% > "
        f"{RUNNER_MASS_BALANCE_LIMIT_PCT:.12g}%"
    )
    return "failed", reason, error_message


def _nested_species_kg_from_segment_species(
    values: Mapping[tuple[str, str], float],
) -> dict[str, dict[str, float]]:
    nested: dict[str, dict[str, float]] = {}
    for key, raw_kg in sorted(values.items()):
        if not isinstance(key, tuple) or len(key) != 2:
            raise RunnerError(f"wall deposit key must be (segment, species): {key!r}")
        segment, species = key
        kg = _finite_export_float(raw_kg, field="wall deposit kg")
        if abs(kg) <= 1.0e-12:
            continue
        nested.setdefault(str(segment), {})[str(species)] = kg
    return {
        segment: dict(sorted(species_kg.items()))
        for segment, species_kg in sorted(nested.items())
    }


def _wall_deposit_cumulative_kg_at_snapshot(
    sim: PyrolysisSimulator,
    snapshot: HourSnapshot,
) -> dict[str, dict[str, float]]:
    cumulative: dict[tuple[str, str], float] = {}
    snapshots = tuple(getattr(getattr(sim, "record", None), "snapshots", ()) or ())
    found_snapshot = False
    for item in snapshots:
        if int(getattr(item, "hour", -1)) > int(snapshot.hour):
            break
        for key, kg in item.wall_deposit_by_segment_species_delta.items():
            cumulative[key] = cumulative.get(key, 0.0) + float(kg)
        if item is snapshot:
            found_snapshot = True
            break
    if not found_snapshot and snapshot not in snapshots:
        for key, kg in snapshot.wall_deposit_by_segment_species_delta.items():
            cumulative[key] = cumulative.get(key, 0.0) + float(kg)
    return _nested_species_kg_from_segment_species(cumulative)


def _vapor_species_kg_hr(snapshot: HourSnapshot) -> dict[str, float]:
    return {
        str(species): _finite_export_float(kg_hr, field="vapor species kg/hr")
        for species, kg_hr in sorted(snapshot.evap_flux.species_kg_hr.items())
        if abs(float(kg_hr)) > 1.0e-12
    }


def _knudsen_regime_observables(snapshot: HourSnapshot) -> dict[str, Any]:
    summary = dict(snapshot.knudsen_regime_summary or {})
    kn = summary.get("knudsen_number")
    return {
        "Kn": (
            _finite_export_float(kn, field="Kn")
            if kn is not None
            else None
        ),
        "regime": str(summary.get("knudsen_regime") or ""),
        "transport_formula_id": NOT_APPLICABLE_UNTIL_P0,
    }


def _evap_plane_selectivity_observables(snapshot: HourSnapshot) -> dict[str, Any]:
    summary = dict(getattr(snapshot, "evap_plane_selectivity", {}) or {})
    target_species = tuple(str(item) for item in summary.get("target_species", ()) or ())
    if not target_species:
        return {}
    fractions = {
        str(species): _finite_export_float(
            fraction,
            field=f"evap_plane_selectivity fraction {species}",
        )
        for species, fraction in sorted(
            (summary.get("per_species_fraction") or {}).items()
        )
    }
    return {
        "evap_plane_selectivity": {
            "total_flux_kg_hr": _finite_export_float(
                summary.get("total_flux_kg_hr", 0.0),
                field="evap_plane_selectivity total flux kg/hr",
            ),
            "per_species_fraction": fractions,
            "target_species": list(target_species),
            "target_flux_kg_hr": _finite_export_float(
                summary.get("target_flux_kg_hr", 0.0),
                field="evap_plane_selectivity target flux kg/hr",
            ),
            "target_selectivity": _finite_export_float(
                summary.get("target_selectivity", 0.0),
                field="evap_plane_selectivity target selectivity",
            ),
        },
    }


def _fe_redox_split_observables(snapshot: HourSnapshot) -> dict[str, Any]:
    summary = dict(getattr(snapshot, "fe_redox_split", {}) or {})
    if not summary:
        return {}
    numeric_fields = {
        "fO2_log",
        "fe3_over_sigma_fe",
        "ferric_frac",
        "ferrous_frac",
        "native_fe_frac",
        "fe2o3_over_feo_molar",
        "fe2o3_equiv_wt_pct",
        "feo_equiv_wt_pct",
        "temperature_K",
        "pressure_bar",
        "iw_log",
    }
    exported: dict[str, Any] = {}
    for key, value in sorted(summary.items()):
        if key in numeric_fields:
            exported[key] = _finite_export_float(
                value,
                field=f"fe_redox_split {key}",
            )
        elif isinstance(value, bool):
            exported[key] = bool(value)
        elif (
            key in ("native_fe_partition", "native_fe_saturation_event")
            and isinstance(value, Mapping)
        ):
            exported[key] = _json_safe(value)
        elif value is None:
            exported[key] = None
        else:
            exported[key] = str(value)
    return {"fe_redox_split": exported}


def _redox_source_breakdown_observables(snapshot: HourSnapshot) -> dict[str, Any]:
    summary = dict(getattr(snapshot, "redox_source_breakdown", {}) or {})
    if not summary:
        return {}
    return {"redox_source_breakdown": _json_safe(summary)}


def _mre_uncertified_yield_observables(snapshot: HourSnapshot) -> dict[str, Any]:
    summary = dict(getattr(snapshot, "mre_uncertified_yield", {}) or {})
    if not summary:
        return {}
    return {"mre_uncertified_yield": _json_safe(summary)}


def _mre_ellingham_ladder_diagnostic_observables(
    snapshot: HourSnapshot,
) -> dict[str, Any]:
    summary = dict(getattr(snapshot, "mre_ellingham_ladder_diagnostic", {}) or {})
    if not summary:
        return {}
    return {"mre_ellingham_ladder_diagnostic": _json_safe(summary)}


def _c7_product_report(
    sim: PyrolysisSimulator | None = None,
) -> dict[str, Any]:
    if sim is None:
        return {}
    return dict(getattr(sim, "_c7_product_report", {}) or {})


def _c7_refusal_diagnostic(
    sim: PyrolysisSimulator | None = None,
) -> dict[str, Any]:
    if sim is None:
        return {}
    return dict(getattr(sim, "_last_c7_refusal_diagnostic", {}) or {})


def _empty_melt_redox_gate_floor_fallback_engagement() -> dict[str, Any]:
    return {
        "engaged": False,
        "total_count": 0,
        "by_hour": [],
    }


def _empty_degraded_path_engagement_entry() -> dict[str, Any]:
    return {
        "engaged": False,
        "total_count": 0,
        "by_hour": [],
    }


def _empty_degraded_path_engagement() -> dict[str, dict[str, Any]]:
    return {
        path: _empty_degraded_path_engagement_entry()
        for path in DEGRADED_PATH_ENGAGEMENT_KEYS
    }


def _degraded_path_engagement(
    sim: PyrolysisSimulator,
) -> dict[str, dict[str, Any]]:
    raw_summary = dict(sim._degraded_path_engagement_summary() or {})
    unknown_paths = set(raw_summary) - set(DEGRADED_PATH_ENGAGEMENT_KEYS)
    if unknown_paths:
        raise RunnerError(
            "unknown degraded path engagement keys: "
            + ", ".join(sorted(unknown_paths))
        )
    summary = _empty_degraded_path_engagement()
    for path, raw_path_summary in raw_summary.items():
        total_count = int(raw_path_summary.get("total_count", 0) or 0)
        summary[path].update(
            engaged=total_count > 0,
            total_count=total_count,
            by_hour=_json_safe(
                list(raw_path_summary.get("by_hour", ()) or ())
            ),
        )
    return summary


def _melt_redox_gate_floor_fallback_engagement(
    sim: PyrolysisSimulator,
) -> dict[str, Any]:
    raw_summary = sim._melt_redox_liquidus_gate_fallback_summary()
    summary = _empty_melt_redox_gate_floor_fallback_engagement()
    summary.update(
        engaged=bool(raw_summary.get("engaged", False)),
        total_count=int(raw_summary.get("total_count", 0) or 0),
        by_hour=_json_safe(list(raw_summary.get("recent_hourly", ()) or ())),
    )
    return summary


def build_per_hour_summary(
    sim: PyrolysisSimulator,
    snapshot: HourSnapshot,
    *,
    include_fe_redox_split: bool = True,
) -> dict:
    """Build the per-hour summary entry for both the CLI runner and the
    SocketIO stream.

    Schema fields (pinned by docs/runner-output-schema.md):

    * ``hour``: snapshot hour
    * ``campaign``: snapshot campaign name (``CampaignPhase.name``)
    * ``T_C``: melt temperature in Celsius
    * ``P_total_bar``: total pressure above the melt in bar
    * ``pO2_bar``: pO2 partial pressure in bar
    * ``p_carrier_bar``: actual declared carrier partial pressure in bar, when present
    * ``carrier_identity``: canonical N2/Ar/CO2 carrier token, when present
    * ``mass_balance_pct``: ledger-based mass balance error, percent
    * ``O2_yield_kg_cumulative``: legacy serialized key for source-side
      O2 potential from all bins (kg), not recovered/captured O2
    * ``O2_source_side_potential_kg_cumulative``: honest alias for the
      same source-side emitted O2 potential value
    * ``O2_metric_label``: human-facing label for the O2 metric semantics
    * ``metal_yields_kg``: dict of metal product yields (kg) at this
      hour, sourced from the simulator's product_ledger projection
    * ``condensation_train_kg``: dict of cumulative condensation totals
    * ``vapor_species_kg_hr``: per-species vapor flux from the snapshot
    * ``wall_deposit_delta_kg``: per-hour wall deposit by segment/species
    * ``wall_deposit_cumulative_kg``: running wall deposit by segment/species
    * ``Kn`` / ``regime``: Knudsen-regime observables from the snapshot
    * ``transport_formula_id``: P0-gated sentinel until molecular transport lands
    """

    # 0.5.3 Phase C milestone review P1 (codex 2026-05-28): the per-hour
    # summary used to mix two different gas-state sources — `P_total_bar`
    # came from the snapshot overhead (holdup-derived under finite-
    # headspace) while `pO2_bar` came from the live commanded `melt.
    # pO2_mbar` setpoint. Under finite-headspace + HARD_VACUUM /
    # PN2_SWEEP, the commanded setpoint can exist (operator's intent)
    # but the floor doesn't fire (atmosphere excluded), so the live
    # `melt.pO2_mbar` painted a non-zero pO2 onto a vacuum-floor
    # `P_total_bar` — visible in `lunar_mare_low_ti_C0_24h.json` hour 19
    # as `P_total_bar=6.14e-9, pO2_bar=0.009`. Honest fix: read BOTH
    # from the same overhead-gas snapshot composition; `melt.pO2_mbar`
    # is the operator's *intent* and belongs in a different surface
    # if exposed at all.
    p_total_bar = float(snapshot.overhead.pressure_mbar) * _MBAR_TO_BAR
    pO2_bar = (
        float(snapshot.overhead.composition.get('O2', 0.0)) * _MBAR_TO_BAR
    )
    carrier_observables = _carrier_pressure_observables(sim, snapshot)

    products = sim.product_ledger()
    metal_yields = {
        species: float(products.get(species, 0.0))
        for species in _METAL_PRODUCT_SPECIES
        if abs(products.get(species, 0.0)) > 1e-12
    }

    # 0.5.4.1 midflight-review P2 (2026-05-28): the per-tick
    # Knudsen-regime summary (E3) is exposed on HourSnapshot via
    # ``snapshot.knudsen_regime_summary`` — adding it to the runner
    # per-hour summary output requires coordinated fixture regen
    # for `lunar_mare_low_ti_C0_24h`, `mars_basalt_C2A_12h`, and
    # `ci_carbonaceous_chondrite_C2B_12h`. Deferred to the
    # morning gate so the regen can be reviewed alongside the
    # decision on B5 hold-hours + Na/K/V species addition. The
    # snapshot-level surface is already accessible to in-process
    # consumers and the web UI; this just defers the JSON-output
    # propagation.
    mass_balance_category = str(
        getattr(snapshot, "mass_balance_error_category", "") or ""
    )
    raw_mass_balance_pct = getattr(snapshot, "mass_balance_error_pct", None)
    mass_balance_pct = (
        None if raw_mass_balance_pct is None else float(raw_mass_balance_pct)
    )
    if (
        mass_balance_pct is not None
        and abs(mass_balance_pct) <= 5e-12
        and getattr(sim.campaign_mgr, "last_pO2_enforcement", None) is not None
    ):
        mass_balance_pct = 0.0

    o2_source_side_potential_kg = float(snapshot.oxygen_produced_kg)
    fe_redox_split_observables = (
        _fe_redox_split_observables(snapshot)
        if include_fe_redox_split
        else {}
    )

    summary = {
        "hour": int(snapshot.hour),
        "campaign": snapshot.campaign.name,
        "T_C": float(snapshot.temperature_C),
        "P_total_bar": p_total_bar,
        "pO2_bar": pO2_bar,
        **carrier_observables,
        "mass_balance_pct": mass_balance_pct,
        "O2_yield_kg_cumulative": o2_source_side_potential_kg,
        "O2_source_side_potential_kg_cumulative": o2_source_side_potential_kg,
        "O2_metric_label": O2_SOURCE_SIDE_POTENTIAL_LABEL,
        "energy_electrical_plus_evaporation_kWh": float(
            snapshot.energy.electrical_plus_evaporation_kWh
        ),
        "energy_electrical_kWh": float(snapshot.energy.electrical_total_kWh),
        "energy_evaporation_thermal_kWh": float(
            snapshot.energy.evaporation_thermal_kWh
        ),
        "energy_scope": snapshot.energy.energy_scope,
        "furnace_heat_status": snapshot.energy.furnace_heat_status,
        "energy_latent_kWh": float(snapshot.energy.latent_kWh),
        "energy_dissociation_kWh": float(snapshot.energy.dissociation_kWh),
        "energy_electrical_plus_evaporation_cumulative_kWh": float(
            snapshot.energy_electrical_plus_evaporation_cumulative_kWh
        ),
        "energy_cumulative_breakdown_kWh": _json_safe(
            dict(snapshot.energy_cumulative_breakdown_kWh)
        ),
        "energy_evaporation_breakdown_kWh": _json_safe(
            dict(snapshot.energy.evaporation_breakdown_kWh)
        ),
        "metal_yields_kg": metal_yields,
        "condensation_train_kg": {
            species: float(kg)
            for species, kg in sorted(snapshot.condensation_totals.items())
            if abs(kg) > 1e-12
        },
        "vapor_species_kg_hr": _vapor_species_kg_hr(snapshot),
        "wall_deposit_delta_kg": _nested_species_kg_from_segment_species(
            snapshot.wall_deposit_by_segment_species_delta,
        ),
        "wall_deposit_cumulative_kg": _wall_deposit_cumulative_kg_at_snapshot(
            sim, snapshot,
        ),
        **_knudsen_regime_observables(snapshot),
        **_evap_plane_selectivity_observables(snapshot),
        **_redox_source_breakdown_observables(snapshot),
        **_mre_uncertified_yield_observables(snapshot),
        **_mre_ellingham_ladder_diagnostic_observables(snapshot),
        **fe_redox_split_observables,
    }
    if fe_redox_split_observables and hasattr(sim, "_stage3_fe_wt_pct_diagnostic"):
        stage3 = sim._stage3_fe_wt_pct_diagnostic()
        summary["stage_3_capture"] = {
            "Fe_kg": _finite_export_float(
                stage3.get("stage_3_fe_kg", 0.0),
                field="stage_3_capture Fe_kg",
            ),
            "total_kg": _finite_export_float(
                stage3.get("stage_3_total_kg", 0.0),
                field="stage_3_capture total_kg",
            ),
            "Fe_wt_pct": _finite_export_float(
                stage3.get("stage_3_fe_wt_pct", 0.0),
                field="stage_3_capture Fe_wt_pct",
            ),
        }
    metal_phase_stratification = dict(
        getattr(snapshot, "metal_phase_stratification", {}) or {}
    )
    if metal_phase_stratification:
        summary["metal_phase_stratification"] = _json_safe(
            metal_phase_stratification
        )
    if mass_balance_category:
        summary["mass_balance_error_category"] = mass_balance_category
    c2a_staged_gas = dict(getattr(snapshot, "c2a_staged_gas", {}) or {})
    if c2a_staged_gas:
        summary["c2a_staged_gas"] = _json_safe(c2a_staged_gas)
    enforcement = getattr(sim.campaign_mgr, "last_pO2_enforcement", None)
    if isinstance(enforcement, Mapping) and int(enforcement.get("hour", -1)) == int(snapshot.hour):
        summary["pO2_enforcement"] = _json_safe(dict(enforcement))
    return _json_safe(summary)


def _carrier_pressure_observables(
    sim: PyrolysisSimulator,
    snapshot: HourSnapshot,
) -> dict[str, float | str]:
    """Return the actual declared carrier partial pressure when present."""
    melt = getattr(sim, "melt", None)
    raw_carrier = str(
        getattr(melt, "background_gas_species", "") or ""
    ).strip()
    carrier = _CARRIER_TOKENS.get(raw_carrier.upper())
    atmosphere_name = str(
        getattr(getattr(melt, "atmosphere", None), "name", "") or ""
    )
    if carrier is None and atmosphere_name == "PN2_SWEEP":
        carrier = "N2"
    elif carrier is None and atmosphere_name == "CO2_BACKPRESSURE":
        carrier = "CO2"
    if carrier is None:
        return {}

    try:
        partial_mbar = float(
            snapshot.overhead.composition.get(carrier, 0.0) or 0.0
        )
    except (TypeError, ValueError):
        return {}
    if not math.isfinite(partial_mbar) or partial_mbar <= 0.0:
        return {}
    # Derivation: overhead.composition stores physical species partials in mbar;
    # p_carrier[bar] = p_carrier[mbar] * 1e-3. P_total - pO2 is forbidden
    # because total pressure may also include vapor species or a control floor.
    return {
        "p_carrier_bar": partial_mbar * _MBAR_TO_BAR,
        "carrier_identity": carrier,
    }


# ----------------------------------------------------------------------
# Final-state ledger projection
# ----------------------------------------------------------------------


def _final_state_from_ledger(sim: PyrolysisSimulator) -> dict[str, dict[str, float]]:
    """Return ``{account_name: {species_id: mol}}`` for the full ledger.

    ``AtomLedger.mol_by_account()`` returns mol-keyed balances for every
    registered account.  Zero entries are dropped to keep the output
    compact -- downstream callers should treat absent keys as 0.0.
    """

    balances = sim.atom_ledger.mol_by_account()
    return {
        account: {
            species: float(mol)
            for species, mol in sorted(species_mol.items())
            if abs(float(mol)) > 0.0
        }
        for account, species_mol in sorted(balances.items())
    }


def _c0_char_diagnostic(
    sim: PyrolysisSimulator,
    snapshots: tuple[HourSnapshot, ...],
    *,
    feedstock_id: str,
) -> dict[str, Any]:
    """Project refractory organic carbon and O2-lance coverage at C0 end."""
    c0_snapshots = tuple(
        snapshot
        for snapshot in snapshots
        if snapshot.campaign == CampaignPhase.C0
    )
    if not c0_snapshots:
        return {}

    carbon_diagnostics = tuple(
        row
        for row in (getattr(sim, "_stage0_foulant_diagnostics", ()) or ())
        if row.get("reaction_family") == "partition_carbon"
    )
    # t-325: committed solid-char account is authoritative residual
    # inventory; partition diagnostic is the Stage-0 formation total
    # used when the ledger has not yet been queried / is empty after
    # full consumption still warrants a diagnostic block only if the
    # partition reported char this batch.
    from simulator.account_ids import SOLID_CHAR_CARBON_ACCOUNT

    partition_char_mol = sum(
        float(row["refractory_mol"])
        for row in carbon_diagnostics
        if isinstance(row.get("refractory_mol"), (int, float))
        and float(row["refractory_mol"]) > 0.0
    )
    ledger_char_mol = max(
        0.0,
        float(
            sim.atom_ledger.mol_by_account(SOLID_CHAR_CARBON_ACCOUNT).get(
                "C", 0.0
            )
            or 0.0
        ),
    )
    if partition_char_mol <= 0.0 and ledger_char_mol <= 0.0:
        return {}
    # Residual hazard inventory: live ledger balance. Formation total
    # stays available under partition diagnostics for attribution.
    refractory_char_mol = ledger_char_mol

    partition_row = (
        sim._load_carbon_partition_config()
        .get("phase_partitions", {})
        .get(feedstock_id, {})
    )
    refractory_partition = dict(
        partition_row.get("f_refractory_organic_C", {}) or {}
    )
    partition_fraction = refractory_partition.get("floor")
    if partition_fraction is None:
        partition_fraction = refractory_partition.get("iom_anchor")

    o2_molar_mass_kg_per_mol = MOLAR_MASS["O2"] / 1000.0
    carbon_molar_mass_kg_per_mol = ATOMIC_WEIGHTS_G_PER_MOL["C"] / 1000.0
    feo_molar_mass_kg_per_mol = MOLAR_MASS["FeO"] / 1000.0
    fe_molar_mass_kg_per_mol = MOLAR_MASS["Fe"] / 1000.0
    o2_injected_kg = sum(
        max(0.0, float(snapshot.o2_bubbler_injected_kg))
        for snapshot in c0_snapshots
    )
    o2_absorbed_kg = sum(
        max(0.0, float(snapshot.o2_bubbler_absorbed_kg))
        for snapshot in c0_snapshots
    )
    o2_injected_mol = o2_injected_kg / o2_molar_mass_kg_per_mol
    o2_absorbed_mol = o2_absorbed_kg / o2_molar_mass_kg_per_mol

    # Premise: residual char can reduce molten FeO once the C/CO Ellingham
    # line is below Fe/FeO. Algebra: C + O2 -> CO2 needs 1 mol O2/mol C;
    # C + 1/2 O2 -> CO needs 0.5 mol O2/mol C; FeO + C -> Fe + CO is 1:1.
    # Unit check: kg O2 / (kg/mol) -> mol; mol C * kg/mol -> kg C/Fe.
    # Sanity: 1 tonne at 3.5 wt% C and the Sephton floor 0.39 gives
    # 1.136 kmol (13.65 kg) char, 36.3/18.2 kg O2 (CO2/CO), and at most
    # 63.5 kg Fe. This report is a projection only; no account is mutated.
    o2_required_co2_mol = partition_char_mol
    o2_required_co_mol = 0.5 * partition_char_mol
    injected_residual_co2_mol = max(
        partition_char_mol - o2_injected_mol, 0.0
    )
    injected_residual_co_mol = max(
        partition_char_mol - 2.0 * o2_injected_mol, 0.0
    )
    # The ledger balance is already post-lance. Do not subtract cumulative
    # bubbler absorption a second time; that telemetry also includes Fe-redox
    # absorption and is retained only for dose-coverage attribution.
    absorbed_residual_co2_mol = refractory_char_mol
    absorbed_residual_co_mol = refractory_char_mol

    c0_end = c0_snapshots[-1]
    melt_feo_kg = max(
        0.0,
        float(c0_end.inventory.melt_oxide_kg.get("FeO", 0.0) or 0.0),
    )
    melt_feo_mol = melt_feo_kg / feo_molar_mass_kg_per_mol
    feo_reducible_mol = min(absorbed_residual_co2_mol, melt_feo_mol)
    feo_fraction_at_risk = (
        feo_reducible_mol / melt_feo_mol if melt_feo_mol > 0.0 else 0.0
    )
    # No source establishes a safe non-zero residual-char allowance. The
    # owner-flagged 2026-07-15 Ellingham premise, grounded to REF-020
    # NIST-JANAF/Chase 1998 C/CO and Fe/FeO thermochemistry, makes onset the
    # warning boundary: any positive FeO fraction at risk warns, but never
    # refuses or changes process behavior.
    warning_fired = feo_fraction_at_risk > C0_CHAR_WARNING_FEO_FRACTION

    def coverage_pct(o2_mol: float, required_mol: float) -> float:
        if required_mol <= 0.0:
            return 100.0
        return 100.0 * min(max(o2_mol, 0.0) / required_mol, 1.0)

    warning = None
    if warning_fired:
        warning = (
            "WARNING: un-lanced refractory char can stoichiometrically reduce "
            "a positive fraction of C0-end melt FeO; diagnostic only, "
            "no process gate applied."
        )
    susceptible_melt_mol = {}
    for species in ("P2O5", "Cr2O3", "TiO2"):
        species_kg = max(
            0.0,
            float(c0_end.inventory.melt_oxide_kg.get(species, 0.0) or 0.0),
        )
        if species_kg > 0.0:
            formula = resolve_species_formula(
                species, sim.species_formula_registry
            )
            susceptible_melt_mol[species] = (
                species_kg / formula.molar_mass_kg_per_mol()
            )
    contamination_warn = refractory_char_mol > 0.0

    return {
        "status": "WARN" if warning_fired else "OK",
        "diagnostic_only": True,
        "warning": warning,
        "partition": {
            "feedstock_id": feedstock_id,
            "f_refractory_organic_C": float(partition_fraction),
            "fraction_basis": (
                "floor"
                if refractory_partition.get("floor") is not None
                else "iom_anchor"
            ),
            "source": refractory_partition.get("source"),
            "regime_caveat": refractory_partition.get("regime_caveat"),
        },
        "inventory": {
            "formed_refractory_char_C_mol": partition_char_mol,
            "refractory_char_C_mol": refractory_char_mol,
            "refractory_char_C_kg": (
                refractory_char_mol * carbon_molar_mass_kg_per_mol
            ),
        },
        "lance_stoichiometry": {
            "O2_injected_kg": o2_injected_kg,
            "O2_injected_mol": o2_injected_mol,
            "O2_absorbed_kg": o2_absorbed_kg,
            "O2_absorbed_mol": o2_absorbed_mol,
            "C_plus_O2_to_CO2": {
                "O2_required_mol": o2_required_co2_mol,
                "O2_required_kg": (
                    o2_required_co2_mol * o2_molar_mass_kg_per_mol
                ),
                "injected_coverage_pct": coverage_pct(
                    o2_injected_mol, o2_required_co2_mol
                ),
                "absorbed_coverage_pct": coverage_pct(
                    o2_absorbed_mol, o2_required_co2_mol
                ),
                "injected_basis_residual_char_C_mol": (
                    injected_residual_co2_mol
                ),
                "injected_basis_residual_char_C_kg": (
                    injected_residual_co2_mol * carbon_molar_mass_kg_per_mol
                ),
                "un_lanced_char_C_mol": absorbed_residual_co2_mol,
                "un_lanced_char_C_kg": (
                    absorbed_residual_co2_mol * carbon_molar_mass_kg_per_mol
                ),
            },
            "C_plus_half_O2_to_CO": {
                "O2_required_mol": o2_required_co_mol,
                "O2_required_kg": (
                    o2_required_co_mol * o2_molar_mass_kg_per_mol
                ),
                "injected_coverage_pct": coverage_pct(
                    o2_injected_mol, o2_required_co_mol
                ),
                "absorbed_coverage_pct": coverage_pct(
                    o2_absorbed_mol, o2_required_co_mol
                ),
                "injected_basis_residual_char_C_mol": injected_residual_co_mol,
                "injected_basis_residual_char_C_kg": (
                    injected_residual_co_mol * carbon_molar_mass_kg_per_mol
                ),
                "un_lanced_char_C_mol": absorbed_residual_co_mol,
                "un_lanced_char_C_kg": (
                    absorbed_residual_co_mol * carbon_molar_mass_kg_per_mol
                ),
            },
            "residual_basis": (
                "live_post_lance_solid_char_ledger; injected_and_absorbed_O2_"
                "are_dose_coverage_attribution_only"
            ),
        },
        "FeO_reduction_potential": {
            "basis": (
                "live_post_lance_solid_char_ledger_residual"
            ),
            "melt_FeO_available_mol": melt_feo_mol,
            "melt_FeO_available_kg": melt_feo_kg,
            "FeO_reducible_mol": feo_reducible_mol,
            "Fe_equivalent_kg": feo_reducible_mol * fe_molar_mass_kg_per_mol,
            "CO_equivalent_mol": feo_reducible_mol,
            "melt_FeO_fraction_at_risk": feo_fraction_at_risk,
            "warning_threshold_melt_FeO_fraction": (
                C0_CHAR_WARNING_FEO_FRACTION
            ),
            "warning_threshold_basis": (
                "thermodynamic-onset threshold: no sourced safe non-zero "
                "residual-char allowance; warn above zero melt-FeO fraction"
            ),
            "warning_threshold_source": (
                "owner-flagged 2026-07-15 Ellingham premise; REF-020 "
                "NIST-JANAF/Chase 1998 thermochemistry"
            ),
        },
        "contamination_risk": {
            "status": "WARN" if contamination_warn else "OK",
            "diagnostic_only": True,
            "warning": (
                "WARNING: un-lanced solid char can reduce melt P/Cr/Ti "
                "oxides where present and form metal carbides; selectivity "
                "is not modeled."
                if contamination_warn
                else None
            ),
            "susceptible_melt_mol": susceptible_melt_mol,
            "warning_threshold": (
                "generic carbide caution at positive surviving char; "
                "P/Cr/Ti reduction caution additionally requires positive "
                "susceptible oxide inventory"
            ),
            "p_cr_ti_reduction_status": (
                "WARN" if contamination_warn and susceptible_melt_mol else "OK"
            ),
            "carbide_risk_status": "WARN" if contamination_warn else "OK",
            "warning_threshold_source": (
                "thermodynamic-onset screen from REF-020 JANAF oxide "
                "stability; no sourced safe residual-char allowance"
            ),
            "out_of_scope": (
                "vacuum SiO2+C->SiO(g); P/Cr/Ti selectivity and carbide "
                "speciation"
            ),
        },
    }


# ----------------------------------------------------------------------
# SiO yield report
# ----------------------------------------------------------------------


def _force_builtin_vapor_pressure(sim: PyrolysisSimulator) -> None:
    """Route VAPOR_PRESSURE through the builtin fallback for stable goldens."""

    from simulator.chemistry.kernel.capabilities import ChemistryIntent

    registry = sim._chem_registry
    provider = registry.authoritative_for(ChemistryIntent.VAPOR_PRESSURE)
    if provider is None:
        return

    backend = getattr(provider, "_backend", None)
    if backend is not None and hasattr(backend, "is_available"):
        backend.is_available = lambda: False  # type: ignore[assignment]
    provider._ensure_backend = lambda: backend  # type: ignore[method-assign]


def _clean_report_float(value: float) -> float:
    if not math.isfinite(value):
        raise RunnerError(f"non-finite SiO yield value: {value!r}")
    if abs(value) < 1.0e-15:
        return 0.0
    return float(f"{value:.12g}")


def _required_mass_balance_value(
    snapshot: Mapping[str, Any],
    key: str,
    *,
    source: str,
) -> float:
    raw = snapshot.get(key)
    if raw is None:
        raise EngineBugAbort(
            f"mass_balance_key_missing_in_snapshot: source={source} key={key}"
        )
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise EngineBugAbort(
            f"mass_balance_key_non_numeric_in_snapshot: source={source} key={key}"
        ) from exc
    if not math.isfinite(value):
        raise EngineBugAbort(
            f"mass_balance_key_nonfinite_in_snapshot: source={source} key={key}"
        )
    return value


def _latest_mass_balance_pct(result: Mapping[str, Any]) -> float:
    summary = result.get("per_hour_summary")
    if not summary:
        raise EngineBugAbort(
            "mass_balance_snapshot_missing: source=per_hour_summary"
        )
    if not isinstance(summary, (list, tuple)):
        raise EngineBugAbort(
            "mass_balance_snapshot_malformed: source=per_hour_summary"
        )
    snapshot = summary[-1]
    if not isinstance(snapshot, Mapping):
        raise EngineBugAbort(
            "mass_balance_snapshot_malformed: source=per_hour_summary"
        )
    return _required_mass_balance_value(
        snapshot,
        "mass_balance_pct",
        source="per_hour_summary",
    )


def _format_sweep_float(value: float) -> str:
    if not math.isfinite(value):
        raise RunnerError(f"non-finite sweep value: {value!r}")
    if float(value).is_integer():
        return str(int(value))
    return f"{value:g}".replace(".", "p")


def _c_to_display_k(value_c: float) -> int:
    return int(round(float(value_c) + CELSIUS_TO_KELVIN_OFFSET))


def _warning_sticker_fires(t_hold_c: float) -> bool:
    return _c_to_display_k(t_hold_c) <= 1673


def _parse_float_grid(raw: str, *, label: str) -> tuple[float, ...]:
    values: list[float] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            value = float(item)
        except ValueError as exc:
            raise RunnerError(f"{label} contains non-numeric value {item!r}") from exc
        if not math.isfinite(value):
            raise RunnerError(f"{label} contains non-finite value {item!r}")
        values.append(value)
    if not values:
        raise RunnerError(f"{label} must contain at least one value")
    return tuple(values)


def _industrial_sio_verdict(yield_pct: float) -> str:
    low, high = SIO_INDUSTRIAL_BENCHMARK_PCT
    if yield_pct < low:
        bucket = "below"
    elif yield_pct > high:
        bucket = "above"
    else:
        bucket = "within"
    return (
        f"{bucket} industrial-Si envelope "
        "(order-of-magnitude regime check, not 1-decade fidelity)"
    )


def _stage_silica_fume_kg(sim: PyrolysisSimulator) -> dict[str, float]:
    by_stage = {stage.stage_number: stage for stage in sim.train.stages}
    return {
        key: _clean_report_float(
            by_stage.get(stage_number).collected_kg.get("SiO2", 0.0)
            if by_stage.get(stage_number) is not None
            else 0.0
        )
        for stage_number, key in SIO_YIELD_STAGE_KEYS.items()
    }


def _kg_by_species_from_mol_state(
    state: Mapping[str, Mapping[str, float]],
    account: str,
) -> dict[str, float]:
    species_mol = state.get(account, {})
    kg_by_species: dict[str, float] = {}
    for species, mol in sorted(species_mol.items()):
        molar_mass_g_mol = MOLAR_MASS.get(species)
        if molar_mass_g_mol is None:
            continue
        kg = float(mol) * (molar_mass_g_mol / 1000.0)
        if abs(kg) > 1e-12:
            kg_by_species[species] = _clean_report_float(kg)
    return kg_by_species


def _wall_deposit_report_kg(
    state: Mapping[str, Mapping[str, float]],
) -> dict[str, float]:
    wall_deposit_kg = _kg_by_species_from_mol_state(
        state, WALL_DEPOSIT_ACCOUNT,
    )
    segment_accounts = sorted(
        account
        for account in state
        if str(account).startswith(PIPE_SEGMENT_WALL_DEPOSIT_ACCOUNT_PREFIX)
    )
    for account in segment_accounts:
        for species, kg in _kg_by_species_from_mol_state(
            state, account,
        ).items():
            wall_deposit_kg[species] = wall_deposit_kg.get(species, 0.0) + kg
    for species in SIO_WALL_DEPOSIT_SPECIES:
        wall_deposit_kg.setdefault(species, 0.0)
    return {
        species: wall_deposit_kg[species]
        for species in sorted(wall_deposit_kg)
    }


def _wall_deposit_mol_by_species(
    state: Mapping[str, Mapping[str, float]],
) -> dict[str, float]:
    wall_deposit_mol = dict(state.get(WALL_DEPOSIT_ACCOUNT, {}) or {})
    segment_accounts = sorted(
        account
        for account in state
        if str(account).startswith(PIPE_SEGMENT_WALL_DEPOSIT_ACCOUNT_PREFIX)
    )
    for account in segment_accounts:
        for species, mol in (state.get(account, {}) or {}).items():
            wall_deposit_mol[species] = (
                wall_deposit_mol.get(species, 0.0) + float(mol)
            )
    return wall_deposit_mol


def _sio_wall_terminal_mol(wall_deposit: Mapping[str, float]) -> float:
    # SiO-equivalent (Si-atom) count of every Si-bearing wall deposit. SiO is
    # the only Si vapor species reaching the wall, so every wall Si atom -- as
    # direct SiO, as the disproportionation pair Si + SiO2 (2 SiO -> Si + SiO2,
    # which carry DIFFERENT Si atoms and are therefore both summed, not paired),
    # or as further-reduced FeSi -- descends from exactly one evaporated SiO.
    # Summing them is Si-atom conservation, required for the
    # evaporated -> (terminal + wall + escape) chain closure
    # (test_sio_chain_coherence). A prior de-double-count
    # 2*min(SiO2, Si+FeSi) coincided with the sum only for balanced
    # disproportionation and silently dropped unpaired wall Si, breaking closure.
    return (
        float(wall_deposit.get("SiO", 0.0))
        + float(wall_deposit.get("Si", 0.0))
        + float(wall_deposit.get("SiO2", 0.0))
        + float(wall_deposit.get("FeSi", 0.0))
    )


def _final_summary_report(
    final_state: Mapping[str, Mapping[str, float]],
    execution: RunExecution,
) -> dict[str, Any]:
    return {
        "wall_deposit_by_species_kg": _wall_deposit_report_kg(final_state),
        "deposit_by_surface_species_kg": _nested_species_kg_from_segment_species(
            execution.trace.wall_deposit_by_segment_species_kg,
        ),
        "pump_outlet_by_species_kg": NOT_APPLICABLE_UNTIL_P0,
    }


def _wall_liner_resinter_config() -> dict[str, Any]:
    materials = load_config_bundle(DATA_DIR).materials
    surface = (
        materials.get("wall_surfaces", {})
        .get("interstage_duct", {})
        if isinstance(materials.get("wall_surfaces", {}), Mapping)
        else {}
    )
    liner_material = str(surface.get("liner_material") or "")
    liner_cfg = (
        materials.get("liner_materials", {}).get(liner_material, {})
        if isinstance(materials.get("liner_materials", {}), Mapping)
        else {}
    )
    return {
        "liner_material": liner_material,
        "resinter_threshold_kg": liner_cfg.get("resinter_threshold_kg"),
        "resinter_threshold_basis": liner_cfg.get("resinter_threshold_basis"),
        "fast_fouling_campaign_threshold": int(
            liner_cfg.get("fast_fouling_campaign_threshold") or 10
        ),
    }


def _wall_fouling_report(
    wall_deposit_kg: Mapping[str, float],
    *,
    wall_deposit_by_segment_species: Mapping[tuple[str, str], float] | None = None,
    alpha_notice: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = _wall_liner_resinter_config()
    positive = {
        species: float(kg)
        for species, kg in wall_deposit_kg.items()
        if float(kg) > 0.0
    }
    dominant_species = max(positive, key=positive.get) if positive else "none"
    dominant_kg = positive.get(dominant_species, 0.0) if dominant_species else 0.0
    total_wall_load_kg = sum(positive.values())
    segment_load_kg: dict[str, float] = {}
    if wall_deposit_by_segment_species is not None:
        for key, kg in wall_deposit_by_segment_species.items():
            if not isinstance(key, tuple) or len(key) != 2:
                continue
            amount = float(kg)
            if amount > 0.0:
                segment = str(key[0])
                segment_load_kg[segment] = segment_load_kg.get(segment, 0.0) + amount
    threshold = cfg.get("resinter_threshold_kg")
    threshold_kg = None if threshold is None else float(threshold)
    threshold_is_qualified = (
        threshold_kg is not None
        and math.isfinite(threshold_kg)
        and threshold_kg > 0.0
    )
    fast_n = int(cfg["fast_fouling_campaign_threshold"])
    campaigns_by_segment: dict[str, float | str] = {}
    aggregate_campaigns_to_resinter: float | str = "infinite"
    if total_wall_load_kg <= 0.0:
        campaigns_to_resinter: float | str = "infinite"
        verdict = "slow-fouling"
    elif not threshold_is_qualified:
        controlling_segment_load_kg = max(
            segment_load_kg.values(),
            default=total_wall_load_kg,
        )
        campaigns_to_resinter = (
            f"resinter_threshold_kg / {controlling_segment_load_kg:.12g}"
        )
        aggregate_campaigns_to_resinter = (
            f"resinter_threshold_kg / {total_wall_load_kg:.12g}"
        )
        campaigns_by_segment = {
            segment: f"resinter_threshold_kg / {load_kg:.12g}"
            for segment, load_kg in sorted(segment_load_kg.items())
        }
        verdict = (
            "threshold-parametric: fast-fouling if campaigns_to_resinter "
            f"< {fast_n}, else slow-fouling"
        )
    else:
        assert threshold_kg is not None
        aggregate_campaigns_to_resinter = threshold_kg / total_wall_load_kg
        campaigns_by_segment = {
            segment: threshold_kg / load_kg
            for segment, load_kg in sorted(segment_load_kg.items())
        }
        campaigns_to_resinter = min(
            campaigns_by_segment.values(),
            default=aggregate_campaigns_to_resinter,
        )
        verdict = (
            "fast-fouling"
            if campaigns_to_resinter < fast_n
            else "slow-fouling"
        )
    authority = wall_deposit_sticking_authority_status(
        positive,
        alpha_notice or {},
    )
    authoritative = bool(authority.get("authoritative_for_resinter", True))
    nominal_verdict = verdict
    if not authoritative:
        verdict = "non-authoritative"
    return {
        "liner_material": cfg["liner_material"],
        "dominant_species": dominant_species,
        "dominant_species_wall_deposit_kg": _clean_report_float(dominant_kg),
        "wall_deposit_kg_per_campaign": _clean_report_float(total_wall_load_kg),
        "wall_deposit_basis": "total_wall_load_by_species",
        "resinter_threshold_kg": threshold,
        "resinter_threshold_basis": cfg.get("resinter_threshold_basis"),
        "campaigns_to_resinter": campaigns_to_resinter,
        "campaigns_to_resinter_by_segment": campaigns_by_segment,
        "aggregate_campaigns_to_resinter": aggregate_campaigns_to_resinter,
        "fast_fouling_campaign_threshold": fast_n,
        "output_status": str(authority.get("output_status", "authoritative")),
        "authoritative": authoritative,
        "authoritative_for_resinter": authoritative,
        "verdict_authoritative": authoritative,
        "status": "available" if authoritative else "warning",
        "status_reason": "" if authoritative else str(authority.get("message", "")),
        "sticking_alpha_authority": authority,
        "nominal_verdict": nominal_verdict,
        "verdict": verdict,
    }


def _required_stage0_carbon_kg(feedstock: Mapping[str, Any],
                               mass_kg: float) -> float:
    try:
        return float(
            PyrolysisSimulator._carbon_reductant_required_kg(
                feedstock, mass_kg)
        )
    except Exception as exc:  # noqa: BLE001 -- surface as runner config error
        raise RunnerError(f"invalid Stage 0 carbon cleanup metadata: {exc}") from exc


def _prepare_sio_campaign_start(
    sim: PyrolysisSimulator,
    *,
    t_low_c: float | None = None,
    t_hold_c: float | None = None,
    ramp_c_per_hr: float | None = None,
) -> None:
    campaign_cfg = (
        (sim.setpoints.get("campaigns", {}) or {}).get(SIO_YIELD_CAMPAIGN, {})
        or {}
    )
    temp_range = campaign_cfg.get("temp_range_C") or []
    if t_low_c is not None:
        sim.melt.temperature_C = float(t_low_c)
    elif temp_range:
        sim.melt.temperature_C = max(sim.melt.temperature_C, float(temp_range[0]))

    if ramp_c_per_hr is not None:
        sim.campaign_mgr.overrides.setdefault("C2A", {})["ramp_rate"] = (
            float(ramp_c_per_hr)
        )

    if t_hold_c is None:
        return

    base_get_temp_target = sim.campaign_mgr.get_temp_target

    def _sio_twindow_temp_target(campaign, campaign_hour, melt):
        target, ramp_rate = base_get_temp_target(campaign, campaign_hour, melt)
        if campaign == CampaignPhase.C2A:
            if ramp_c_per_hr is not None:
                ramp_rate = float(ramp_c_per_hr)
            return (
                sim.campaign_mgr._clamp_to_furnace_max(float(t_hold_c)),
                ramp_rate,
            )
        return (target, ramp_rate)

    sim.campaign_mgr.get_temp_target = _sio_twindow_temp_target


def _apply_sio_wall_sweep_controls(
    sim: PyrolysisSimulator,
    *,
    liner_temperature_c: float | None = None,
    pO2_mbar: float | None = None,
) -> None:
    runtime_override = sim.campaign_mgr.overrides.setdefault("C2A", {})
    if pO2_mbar is not None:
        pO2_value = max(0.0, float(pO2_mbar))
        runtime_override["pO2_mbar"] = pO2_value
        sim.melt.pO2_mbar = pO2_value
        sim.melt.p_total_mbar = max(float(sim.melt.p_total_mbar), pO2_value)
        sim.overhead.composition["O2"] = max(
            float(sim.overhead.composition.get("O2", 0.0)),
            pO2_value,
        )
        # Phase A chunk-review P2 (codex 2026-05-28): the wall-sweep
        # "1 mbar pO2 glass / clean-alkali mode" lever needs the
        # commanded-pO2 floor (equilibrium.py / overhead.py finite-
        # headspace branch) to actively suppress SiO via the
        # 1/sqrt(pO2) Ellingham factor. That floor only fires in the
        # ``_O2_CONTROLLED_ATMOSPHERES`` family. Under the default
        # C2A PN2_SWEEP atmosphere with finite headspace default-on,
        # operator pO2_mbar=1.0 produced NO suppression because the
        # holdup-derived O2 partial dominated. Switch atmosphere to
        # CONTROLLED_O2 when the wall-sweep operator commands a pO2
        # — this is the design-intent semantic of "1 mbar pO2 glass"
        # (operator is dosing oxygen, NOT just sweeping with N2).
        sim.melt.atmosphere = Atmosphere.CONTROLLED_O2
        sim._refresh_oxygen_reservoir_without_exchange(
            melt_intrinsic_fO2_log=sim._current_melt_redox_fO2_log(),
            exchange_direction='none:sio_wall_sweep_control',
        )

    if liner_temperature_c is None:
        return
    overhead_cfg = dict(runtime_override.get("overhead_headspace", {}) or {})
    overhead_cfg["liner_temperature_C"] = float(liner_temperature_c)
    overhead_cfg["pipe_segment_temperatures_C"] = float(liner_temperature_c)
    runtime_override["overhead_headspace"] = overhead_cfg
    sim._configure_overhead_headspace(CampaignPhase.C2A)
    if sim._condensation_model is not None:
        sim.condensation_model.configure_operating_conditions(
            wall_temperature_C=float(liner_temperature_c),
            pipe_diameter_m=sim.overhead_model.pipe_diameter_m,
            gas_temperature_C=float(sim.melt.temperature_C),
            stage_area_m2_by_stage=sim.overhead_model.stage_area_m2_by_stage(),
            stage_area_geometry_provenance_notice=(
                sim.overhead_model.stage_area_geometry_provenance_notice()),
            pipe_segment_temperatures_C={
                segment.name: float(liner_temperature_c)
                for segment in sim.condensation_model.pipe_segments
            },
        )


def build_sio_yield_report(
    *,
    feedstock_id: str,
    campaign: str = SIO_YIELD_CAMPAIGN,
    hours: int = 24,
    mass_kg: float = 1000.0,
    include_diagnostics: bool = False,
    include_lab_oxygen_diagnostics: bool = False,
    t_low_c: float | None = None,
    t_hold_c: float | None = None,
    ramp_c_per_hr: float | None = None,
    liner_temperature_c: float | None = None,
    pO2_mbar: float | None = None,
    allow_unmeasured_alpha_fallback: bool = False,
) -> dict[str, Any] | tuple[dict[str, Any], dict[str, Any]]:
    """Run the C2A SiO yield slice and return the golden-file report."""

    if include_lab_oxygen_diagnostics and not include_diagnostics:
        raise RunnerError(
            "lab oxygen diagnostics require include_diagnostics=True"
        )
    if feedstock_id not in SIO_YIELD_FEEDSTOCKS:
        raise RunnerError(
            "SiO yield report supports feedstocks "
            f"{', '.join(SIO_YIELD_FEEDSTOCKS)}; got {feedstock_id!r}"
        )
    if campaign not in (SIO_YIELD_CAMPAIGN, "C2A"):
        raise RunnerError(
            f"SiO yield report supports campaign {SIO_YIELD_CAMPAIGN!r}; "
            f"got {campaign!r}"
        )
    mass_kg = _positive_mass_kg(mass_kg)

    from simulator.condensation import alpha_s
    from simulator.evaporation import _load_evaporation_alpha_by_species

    try:
        bundle = load_config_bundle(DATA_DIR)
    except FileNotFoundError as exc:
        # Error-contract parity with _load_config_bundle (runner.py:417) and the
        # legacy _load_yaml path: main_sio_yield() only catches RunnerError, so a
        # missing config file must surface as RunnerError, not FileNotFoundError.
        raise RunnerError(str(exc)) from exc
    feedstocks = bundle.feedstocks
    feedstock = feedstocks.get(feedstock_id, {})
    alpha_by_species = _load_evaporation_alpha_by_species(
        bundle.vapor_pressures
    )
    try:
        sio_alpha_spec = alpha_by_species["SiO"]
    except KeyError as exc:
        raise RunnerError(
            "SiO evaporation alpha missing from data/vapor_pressures.yaml"
        ) from exc
    stage0_carbon_kg = _required_stage0_carbon_kg(feedstock, mass_kg)
    additives_kg = {}
    if stage0_carbon_kg > 1.0e-12:
        additives_kg["C"] = stage0_carbon_kg

    runtime_campaign_overrides: dict[str, dict] = {}
    if ramp_c_per_hr is not None:
        runtime_campaign_overrides["C2A"] = {
            "ramp_rate": float(ramp_c_per_hr),
        }

    base_run = PyrolysisRun(
        feedstock_id=feedstock_id,
        campaign=campaign,
        hours=int(hours),
        mass_kg=mass_kg,
        backend_name=ANALYTICAL_BACKEND_SERIALIZATION_TOKEN,
        track="pyrolysis",
        additives_kg=additives_kg,
        engines={"vapor_pressure": "builtin-antoine"},
        allow_fallback_vapor=True,
        allow_unmeasured_alpha_fallback=allow_unmeasured_alpha_fallback,
        force_builtin_vapor_pressure=True,
        runtime_campaign_overrides=runtime_campaign_overrides,
    )
    session = base_run._start_session()
    sim = session.simulator
    _prepare_sio_campaign_start(
        sim,
        t_low_c=t_low_c,
        t_hold_c=t_hold_c,
        ramp_c_per_hr=ramp_c_per_hr,
    )
    _apply_sio_wall_sweep_controls(
        sim,
        liner_temperature_c=liner_temperature_c,
        pO2_mbar=pO2_mbar,
    )
    sio_alpha_value = alpha_s(
        "SiO",
        max(float(sim.melt.temperature_C) + 273.15, 1.0),
        {"coefficient_spec": sio_alpha_spec},
    )
    initial_balances = sim.atom_ledger.mol_by_account()
    initial_sio2_mol = float(
        initial_balances.get("process.cleaned_melt", {}).get("SiO2", 0.0)
    )

    result = base_run._run_session(session)
    if result.get("status") in {"failed", "refused"}:
        raise RunnerError(str(result.get("error_message", "SiO run failed")))

    final_state = result.get("final_state", {})
    cleaned_melt = final_state.get("process.cleaned_melt", {})
    condensation_train = final_state.get("process.condensation_train", {})
    wall_deposit = _wall_deposit_mol_by_species(final_state)
    terminal_offgas: dict[str, float] = {}
    for account_name in ("process.overhead_gas", "terminal.offgas"):
        for species, mol in final_state.get(account_name, {}).items():
            terminal_offgas[species] = (
                terminal_offgas.get(species, 0.0) + float(mol)
            )
    retained_holdup = final_state.get(
        "process.condensation_retained_holdup", {}
    )

    final_sio2_mol = float(cleaned_melt.get("SiO2", 0.0))
    sio_evaporated_mol = max(0.0, initial_sio2_mol - final_sio2_mol)
    si_terminal_mol = float(condensation_train.get("Si", 0.0))
    sio2_terminal_mol = float(condensation_train.get("SiO2", 0.0))
    sio_wall_mol = _sio_wall_terminal_mol(wall_deposit)
    sio_escape_mol = float(terminal_offgas.get("SiO", 0.0))
    sio_retained_holdup_mol = float(retained_holdup.get("SiO", 0.0))
    terminal_mol = (
        si_terminal_mol
        + sio2_terminal_mol
        + sio_wall_mol
        + sio_escape_mol
        + sio_retained_holdup_mol
    )
    if sio_evaporated_mol > 0.0:
        closure_error_pct = abs(
            sio_evaporated_mol - terminal_mol
        ) / sio_evaporated_mol * 100.0
    else:
        closure_error_pct = 0.0

    sio_molar_mass_kg_mol = MOLAR_MASS["SiO"] / 1000.0
    sio_evolved_kg = sio_evaporated_mol * sio_molar_mass_kg_mol
    sio_yield_pct = sio_evolved_kg / mass_kg * 100.0

    sio_to_silica_fume_kg = _stage_silica_fume_kg(sim)
    reported_stage_numbers = set(SIO_YIELD_STAGE_KEYS)
    downstream_sio2_kg = sum(
        stage.collected_kg.get("SiO2", 0.0)
        for stage in sim.train.stages
        if stage.stage_number not in reported_stage_numbers
    )
    sio_to_silica_fume_kg["terminal_offgas_escape"] = _clean_report_float(
        downstream_sio2_kg + sio_escape_mol * sio_molar_mass_kg_mol
    )
    condensation_model = sim.condensation_model
    sticking_notice = dict(
        getattr(
            condensation_model,
            "last_sticking_alpha_provenance_notice",
            {},
        ) or {}
    )
    wall_deposit_kg = _wall_deposit_report_kg(final_state)
    wall_fouling = _wall_fouling_report(
        wall_deposit_kg,
        wall_deposit_by_segment_species=(
            wall_deposit_by_segment_species_kg(sim.atom_ledger)
        ),
        alpha_notice=sticking_notice,
    )

    report = {
        "feedstock_id": feedstock_id,
        "campaign": SIO_YIELD_CAMPAIGN,
        "alpha_SiO": _clean_report_float(sio_alpha_value),
        "alpha_provenance": SIO_ALPHA_PROVENANCE,
        "sio_evolved_kg": _clean_report_float(sio_evolved_kg),
        "sio_to_silica_fume_kg": sio_to_silica_fume_kg,
        "wall_deposit_kg": wall_deposit_kg,
        "fouling_rate": wall_fouling,
        "sio_yield_pct_of_feedstock": _clean_report_float(sio_yield_pct),
        "industrial_benchmark_pct": list(SIO_INDUSTRIAL_BENCHMARK_PCT),
        "verdict": _industrial_sio_verdict(sio_yield_pct),
    }

    if include_diagnostics:
        operating_history = list(
            getattr(condensation_model, "operating_history", []) or []
        )
        c2a_history = [
            entry for entry in operating_history
            if entry.get("campaign") == "C2A"
        ]
        operating_entry = (
            c2a_history[-1]
            if c2a_history
            else (operating_history[-1] if operating_history else {})
        )
        diagnostics = {
            "sio_evaporated_mol": sio_evaporated_mol,
            "si_terminal_mol": si_terminal_mol,
            "sio2_terminal_mol": sio2_terminal_mol,
            "sio_wall_mol": sio_wall_mol,
            "sio_escape_mol": sio_escape_mol,
            "sio_retained_holdup_mol": sio_retained_holdup_mol,
            "closure_error_pct": closure_error_pct,
            "mass_balance_error_pct": _latest_mass_balance_pct(result),
            "wall_deposit_total_kg": sum(float(v) for v in wall_deposit_kg.values()),
            "final_overhead_pressure_mbar": float(sim.overhead.pressure_mbar),
            "final_liner_temperature_C": float(sim.overhead_model.pipe_temperature_C),
            "final_knudsen_number": float(
                getattr(condensation_model, "knudsen_number", 0.0)
            ),
            "final_regime_factor": float(
                getattr(condensation_model, "regime_factor", 1.0)
            ),
            "wall_deposit_overhead_pressure_mbar": float(
                operating_entry.get("overhead_pressure_mbar", 0.0) or 0.0
            ),
            "wall_deposit_liner_temperature_C": float(
                operating_entry.get("wall_temperature_C", 0.0) or 0.0
            ),
            "wall_deposit_knudsen_number": float(
                operating_entry.get("knudsen_number", 0.0) or 0.0
            ),
            "wall_deposit_regime_factor": float(
                operating_entry.get("regime_factor", 1.0) or 1.0
            ),
            "wall_deposit_carrier_gas": str(
                operating_entry.get("carrier_gas", "")
            ),
            "wall_deposit_knudsen_regime_diagnostic": dict(
                operating_entry.get("knudsen_regime_diagnostic", {}) or {}
            ),
            "wall_deposit_pipe_segment_temperatures_C": dict(
                operating_entry.get("pipe_segment_temperatures_C", {}) or {}
            ),
            "cold_spot_diagnostic": dict(
                getattr(
                    condensation_model, "last_cold_spot_diagnostic", {}
                ) or {}
            ),
        }
        if sticking_notice:
            diagnostics["wall_sticking_alpha_provenance_notice"] = (
                sticking_notice
            )
        geometry_notice = dict(
            getattr(
                condensation_model,
                "stage_area_geometry_provenance_notice",
                {},
            ) or {}
        )
        if geometry_notice:
            diagnostics["stage_area_geometry_provenance_notice"] = (
                geometry_notice
            )
        transport_notice = dict(
            getattr(
                condensation_model,
                "last_transport_parameter_notice",
                {},
            ) or {}
        )
        if transport_notice:
            diagnostics["transport_parameter_notice"] = transport_notice
        capture_notice = dict(
            getattr(
                condensation_model,
                "last_capture_budget_regularizer_notice",
                {},
            ) or {}
        )
        if capture_notice:
            diagnostics["capture_budget_regularizer_notice"] = capture_notice
        vapor_pressure_diagnostic = dict(
            getattr(sim, "_last_vapor_pressure_diagnostic", {}) or {}
        )
        vaporock_full_speciation = dict(
            vapor_pressure_diagnostic.get("vaporock_full_speciation_Pa") or {}
        )
        if vaporock_full_speciation:
            diagnostics["vaporock_full_speciation_Pa"] = (
                vaporock_full_speciation
            )
        if include_lab_oxygen_diagnostics:
            queries = AccountingQueries(sim)
            diagnostics["lab_oxygen_atom_partition"] = (
                queries.lab_oxygen_atom_partition()
            )
            diagnostics["lab_plume_product_partition"] = (
                queries.lab_plume_product_partition()
            )
        return report, diagnostics
    return report


def _sio_tsweep_cell_id(
    t_low_c: float,
    t_hold_c: float,
    ramp_c_per_hr: float,
) -> str:
    return (
        f"tl{_format_sweep_float(t_low_c)}_"
        f"th{_format_sweep_float(t_hold_c)}_"
        f"r{_format_sweep_float(ramp_c_per_hr)}"
    )


def _sio_tsweep_row(
    *,
    cell_id: str,
    t_low_c: float,
    t_hold_c: float,
    ramp_c_per_hr: float,
    report: Mapping[str, Any],
    diagnostics: Mapping[str, float],
    mass_kg: float,
) -> dict[str, Any]:
    stage3_silica_kg = float(
        report.get("sio_to_silica_fume_kg", {}).get(
            "stage_3_sio_zone_product", 0.0
        )
    )
    terminal_offgas_kg = float(
        report.get("sio_to_silica_fume_kg", {}).get(
            "terminal_offgas_escape", 0.0
        )
    )
    return {
        "cell_id": cell_id,
        "T_low_C": _clean_report_float(float(t_low_c)),
        "T_hold_C": _clean_report_float(float(t_hold_c)),
        "ramp_C_per_hr": _clean_report_float(float(ramp_c_per_hr)),
        "sio_yield_pct_of_feedstock": _clean_report_float(
            float(report["sio_yield_pct_of_feedstock"])
        ),
        "terminal_offgas_escape_pct": _clean_report_float(
            terminal_offgas_kg / float(mass_kg) * 100.0
        ),
        "stage3_silica_kg": _clean_report_float(stage3_silica_kg),
        "mass_balance_err_pct": _clean_report_float(
            abs(
                _required_mass_balance_value(
                    diagnostics,
                    "mass_balance_error_pct",
                    source="diagnostics",
                )
            )
        ),
    }


def _sort_sio_tsweep_rows(rows: list[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (
            -float(row["sio_yield_pct_of_feedstock"]),
            float(row["mass_balance_err_pct"]),
            float(row["T_hold_C"]),
            float(row["ramp_C_per_hr"]),
            float(row["T_low_C"]),
        ),
    )


def _recommend_sio_tsweep_rows(
    rows: list[Mapping[str, Any]],
) -> tuple[Mapping[str, Any], list[Mapping[str, Any]]]:
    closing_rows = [
        row
        for row in _sort_sio_tsweep_rows(rows)
        if float(row["mass_balance_err_pct"]) <= SIO_TSWEEP_MASS_BALANCE_LIMIT_PCT
    ]
    if not closing_rows:
        raise RunnerError(
            "no SiO T-window sweep cells close mass balance at "
            f"{SIO_TSWEEP_MASS_BALANCE_LIMIT_PCT:g}%"
        )
    return closing_rows[0], closing_rows[1:4]


def _sio_tsweep_table(rows: list[Mapping[str, Any]]) -> str:
    headers = (
        "rank",
        "cell_id",
        "T_low_C",
        "T_hold_C",
        "ramp_C_per_hr",
        "sio_yield_pct_of_feedstock",
        "terminal_offgas_escape_pct",
        "stage3_silica_kg",
        "mass_balance_err_pct",
    )
    lines = [
        "| " + " | ".join(headers) + " |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for rank, row in enumerate(_sort_sio_tsweep_rows(rows), start=1):
        values = [
            str(rank),
            str(row["cell_id"]),
            f"{float(row['T_low_C']):g}",
            f"{float(row['T_hold_C']):g}",
            f"{float(row['ramp_C_per_hr']):g}",
            f"{float(row['sio_yield_pct_of_feedstock']):.12g}",
            f"{float(row['terminal_offgas_escape_pct']):.12g}",
            f"{float(row['stage3_silica_kg']):.12g}",
            f"{float(row['mass_balance_err_pct']):.12g}",
        ]
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def _format_sio_tsweep_triple(row: Mapping[str, Any]) -> str:
    return (
        f"(T_low={float(row['T_low_C']):g} C, "
        f"T_hold={float(row['T_hold_C']):g} C, "
        f"ramp={float(row['ramp_C_per_hr']):g} C/hr)"
    )


def build_sio_tsweep_report_markdown(
    *,
    feedstock_id: str,
    rows: list[Mapping[str, Any]],
) -> str:
    recommended, alternates = _recommend_sio_tsweep_rows(rows)
    warning_fired = _warning_sticker_fires(float(recommended["T_hold_C"]))
    lines = [
        f"# SiO T-Window Sweep - {feedstock_id}",
        "",
        "Date: 2026-05-19",
        "",
        "Scope: Phase 3 Stage 3 SiO setpoint characterization for "
        "`data/setpoints.yaml` C2A_continuous operator review. Engine-only "
        "outputs stay inside the [1323, 2400 K] authority band and the "
        "recipe [1050, 1600 C] envelope.",
        "",
        "Caveat: alpha_SiO uses Wetzel/Gail 2013 alpha_s(T), replacing "
        "the old Phase 1 fixed 0.04 alpha surface. Stage 3 is post-Cr v2 "
        "(commit `bb52c62`) and reports `stage_3_sio_zone_product`.",
        "",
        "## Recommendation",
        "",
        f"Recommended triple: `{_format_sio_tsweep_triple(recommended)}`",
        "",
        f"Yield: {float(recommended['sio_yield_pct_of_feedstock']):.12g}% "
        "of feedstock.",
        "",
        f"Mass balance error: {float(recommended['mass_balance_err_pct']):.12g}%.",
        "",
        "## Best Alternates",
        "",
        "| rank | cell_id | triple | yield_pct | mass_balance_err_pct |",
        "|---:|---|---|---:|---:|",
    ]
    for rank, row in enumerate(alternates, start=1):
        lines.append(
            "| "
            f"{rank} | {row['cell_id']} | `{_format_sio_tsweep_triple(row)}` | "
            f"{float(row['sio_yield_pct_of_feedstock']):.12g} | "
            f"{float(row['mass_balance_err_pct']):.12g} |"
        )
    lines.extend(
        [
            "",
            "## Sweep Table",
            "",
            _sio_tsweep_table(rows),
            "",
            "## Coverage Gap",
            "",
            f"Coverage gap checked: {SIO_TSWEEP_GAP_A_BAND}.",
        ]
    )
    if warning_fired:
        lines.extend(["", f"WARNING: {SIO_TSWEEP_WARNING_TEXT}"])
    else:
        lines.extend(
            [
                "",
                "WARNING sticker: not fired for this recommendation. Tickler #4 "
                "remains a monitor for any future recommended T_hold in Gap A.",
            ]
        )
    lines.append("")
    return "\n".join(lines)


def build_sio_tsweep_convergence_markdown(
    feedstock_rows: Mapping[str, list[Mapping[str, Any]]],
) -> str:
    lines = [
        "# SiO T-Window Sweep Convergence",
        "",
        "Date: 2026-05-19",
        "",
        "Scope: Phase 3 Stage 3 SiO T-window recommendations for "
        "`data/setpoints.yaml` C2A_continuous. Operator review gate only; "
        "`data/setpoints.yaml` is intentionally unchanged.",
        "",
        "Commit chain: Phase 1 alpha surface `fc2d40b`; Phase 2 goldens "
        "refresh landed in controller baseline `a2ab138`.",
        "",
        "Caveat: alpha_SiO uses Wetzel/Gail 2013 alpha_s(T). Stage 3 is post-Cr v2 "
        "(commit `bb52c62`). Reports are engine-only in [1323, 2400 K] "
        "and recipe-only in [1050, 1600 C].",
        "",
        "Warning-sticker logic: fire when rounded T_hold_K <= 1673 K "
        "(1400 C boundary included), because the recommendation is inside "
        f"{SIO_TSWEEP_GAP_A_BAND}. If fired, promote Tickler #4 "
        "SIO-TRANGE-EXTENSION-OPERATIONAL Phase A.",
        "",
    ]
    for feedstock_id, rows in feedstock_rows.items():
        recommended, alternates = _recommend_sio_tsweep_rows(rows)
        warning_fired = _warning_sticker_fires(float(recommended["T_hold_C"]))
        lines.extend(
            [
                f"## {feedstock_id}",
                "",
                f"Recommended: `{_format_sio_tsweep_triple(recommended)}`",
                "",
                f"Yield: {float(recommended['sio_yield_pct_of_feedstock']):.12g}%",
                "",
                f"Mass balance error: "
                f"{float(recommended['mass_balance_err_pct']):.12g}%",
                "",
                f"WARNING sticker fired: {str(warning_fired).lower()}",
                "",
                "| rank | cell_id | triple | yield_pct | mass_balance_err_pct |",
                "|---:|---|---|---:|---:|",
            ]
        )
        for rank, row in enumerate(alternates, start=1):
            lines.append(
                "| "
                f"{rank} | {row['cell_id']} | "
                f"`{_format_sio_tsweep_triple(row)}` | "
                f"{float(row['sio_yield_pct_of_feedstock']):.12g} | "
                f"{float(row['mass_balance_err_pct']):.12g} |"
            )
        if warning_fired:
            lines.extend(["", f"WARNING: {SIO_TSWEEP_WARNING_TEXT}", ""])
        else:
            lines.append("")
    return "\n".join(lines)


def run_sio_tsweep(
    *,
    feedstock_id: str,
    t_low_grid_c: tuple[float, ...],
    t_hold_grid_c: tuple[float, ...],
    ramp_grid_c_per_hr: tuple[float, ...],
    output_dir: Path,
    campaign: str = SIO_YIELD_CAMPAIGN,
    hours: int = 24,
    mass_kg: float = 1000.0,
    allow_unmeasured_alpha_fallback: bool = False,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for t_low_c, t_hold_c, ramp_c_per_hr in itertools.product(
        t_low_grid_c, t_hold_grid_c, ramp_grid_c_per_hr
    ):
        cell_id = _sio_tsweep_cell_id(t_low_c, t_hold_c, ramp_c_per_hr)
        report, diagnostics = build_sio_yield_report(
            feedstock_id=feedstock_id,
            campaign=campaign,
            hours=hours,
            mass_kg=mass_kg,
            include_diagnostics=True,
            t_low_c=t_low_c,
            t_hold_c=t_hold_c,
            ramp_c_per_hr=ramp_c_per_hr,
            allow_unmeasured_alpha_fallback=allow_unmeasured_alpha_fallback,
        )
        row = _sio_tsweep_row(
            cell_id=cell_id,
            t_low_c=t_low_c,
            t_hold_c=t_hold_c,
            ramp_c_per_hr=ramp_c_per_hr,
            report=report,
            diagnostics=diagnostics,
            mass_kg=mass_kg,
        )
        cell_doc = {
            "schema_version": SIO_TSWEEP_SCHEMA_VERSION,
            "feedstock_id": feedstock_id,
            "campaign": SIO_YIELD_CAMPAIGN,
            "cell_id": cell_id,
            "T_low_C": row["T_low_C"],
            "T_hold_C": row["T_hold_C"],
            "ramp_C_per_hr": row["ramp_C_per_hr"],
            "metrics": row,
            "report": report,
            "diagnostics": {
                "sio_terminal_closure_error_pct": _clean_report_float(
                    float(diagnostics.get("closure_error_pct", 0.0))
                ),
                "mass_balance_error_pct": row["mass_balance_err_pct"],
            },
        }
        with (output_dir / f"{cell_id}.json").open("w") as f:
            json.dump(
                _json_safe(cell_doc),
                f,
                indent=2,
                sort_keys=False,
                allow_nan=False,
            )
            f.write("\n")
        rows.append(row)

    fieldnames = [
        "cell_id",
        "T_low_C",
        "T_hold_C",
        "ramp_C_per_hr",
        "sio_yield_pct_of_feedstock",
        "terminal_offgas_escape_pct",
        "stage3_silica_kg",
        "mass_balance_err_pct",
    ]
    with (output_dir / "index.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    recommended, alternates = _recommend_sio_tsweep_rows(rows)
    return {
        "schema_version": SIO_TSWEEP_SCHEMA_VERSION,
        "feedstock_id": feedstock_id,
        "campaign": SIO_YIELD_CAMPAIGN,
        "cell_count": len(rows),
        "rows": rows,
        "recommended": dict(recommended),
        "alternates": [dict(row) for row in alternates],
        "warning_sticker_fired": _warning_sticker_fires(
            float(recommended["T_hold_C"])
        ),
        "output_dir": str(output_dir),
    }


def _sio_wall_sweep_cell_id(
    feedstock_id: str,
    pO2_mode: str,
    liner_temperature_c: float,
) -> str:
    return (
        f"{feedstock_id}_{pO2_mode}_"
        f"wall{_format_sweep_float(liner_temperature_c)}"
    )


def _sio_wall_sweep_row(
    *,
    cell_id: str,
    feedstock_id: str,
    pO2_mode: str,
    pO2_mbar: float | None,
    liner_temperature_c: float,
    report: Mapping[str, Any],
    diagnostics: Mapping[str, float],
) -> dict[str, Any]:
    wall_deposit = report.get("wall_deposit_kg", {})
    sio_wall_kg = float(wall_deposit.get("SiO", 0.0))
    total_wall_kg = sum(float(value) for value in wall_deposit.values())
    stage3_silica_kg = float(
        report.get("sio_to_silica_fume_kg", {}).get(
            "stage_3_sio_zone_product", 0.0
        )
    )
    return {
        "cell_id": cell_id,
        "feedstock_id": feedstock_id,
        "pO2_mode": pO2_mode,
        "pO2_mbar": None if pO2_mbar is None else _clean_report_float(pO2_mbar),
        "liner_temperature_C": _clean_report_float(liner_temperature_c),
        "overhead_pressure_mbar": _clean_report_float(
            float(diagnostics.get("wall_deposit_overhead_pressure_mbar", 0.0))
        ),
        "knudsen_number": _clean_report_float(
            float(diagnostics.get("wall_deposit_knudsen_number", 0.0))
        ),
        "regime_factor": _clean_report_float(
            float(diagnostics.get("wall_deposit_regime_factor", 1.0))
        ),
        "sio_wall_deposit_kg": _clean_report_float(sio_wall_kg),
        "total_wall_deposit_kg": _clean_report_float(total_wall_kg),
        "stage3_silica_kg": _clean_report_float(stage3_silica_kg),
        "sio_evolved_kg": _clean_report_float(float(report["sio_evolved_kg"])),
        "sio_yield_pct_of_feedstock": _clean_report_float(
            float(report["sio_yield_pct_of_feedstock"])
        ),
        "mass_balance_err_pct": _clean_report_float(
            abs(
                _required_mass_balance_value(
                    diagnostics,
                    "mass_balance_error_pct",
                    source="diagnostics",
                )
            )
        ),
        "closure_error_pct": _clean_report_float(
            abs(float(diagnostics.get("closure_error_pct", 0.0)))
        ),
    }


def _sort_sio_wall_sweep_rows(
    rows: list[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (
            str(row["feedstock_id"]),
            str(row["pO2_mode"]),
            float(row["liner_temperature_C"]),
        ),
    )


def _sio_wall_sweep_thresholds(
    rows: list[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    thresholds: dict[str, dict[str, Any]] = {}
    keys = sorted({(str(row["feedstock_id"]), str(row["pO2_mode"])) for row in rows})
    for feedstock_id, pO2_mode in keys:
        mode_rows = [
            row for row in rows
            if row["feedstock_id"] == feedstock_id and row["pO2_mode"] == pO2_mode
        ]
        crossing = None
        for row in sorted(mode_rows, key=lambda item: float(item["liner_temperature_C"])):
            if float(row["sio_wall_deposit_kg"]) <= SIO_SLOW_FOULING_WALL_DEPOSIT_KG:
                crossing = row
                break
        thresholds[f"{feedstock_id}:{pO2_mode}"] = {
            "threshold_liner_temperature_C": (
                None if crossing is None else crossing["liner_temperature_C"]
            ),
            "slow_fouling_wall_deposit_kg": SIO_SLOW_FOULING_WALL_DEPOSIT_KG,
            "basis": "sio_wall_deposit_kg",
        }
    return thresholds


def _sio_wall_sweep_evolved_guard(
    rows: list[Mapping[str, Any]],
) -> dict[str, Any]:
    checks: dict[str, dict[str, Any]] = {}
    keys = sorted({(str(row["feedstock_id"]), str(row["pO2_mode"])) for row in rows})
    max_relative_delta = 0.0

    for feedstock_id, pO2_mode in keys:
        mode_rows = [
            row
            for row in rows
            if row["feedstock_id"] == feedstock_id
            and row["pO2_mode"] == pO2_mode
        ]
        values = [float(row["sio_evolved_kg"]) for row in mode_rows]
        evolved_min = min(values)
        evolved_max = max(values)
        denominator = max(abs(evolved_min), abs(evolved_max), 1.0e-300)
        relative_delta = (evolved_max - evolved_min) / denominator
        max_relative_delta = max(max_relative_delta, relative_delta)
        checks[f"{feedstock_id}:{pO2_mode}"] = {
            "evolved_min_kg": _clean_report_float(evolved_min),
            "evolved_max_kg": _clean_report_float(evolved_max),
            "relative_delta": _clean_report_float(relative_delta),
            "passed": relative_delta <= SIO_WALL_SWEEP_EVOLVED_REL_TOL,
        }

    failed = [key for key, check in checks.items() if not check["passed"]]
    if failed:
        raise RunnerError(
            "SiO evolved kg changed across wall temperature at fixed "
            f"feedstock+pO2 mode beyond {SIO_WALL_SWEEP_EVOLVED_REL_TOL:g}: "
            + ", ".join(failed)
        )

    # Phase 3-bis full sweep: no_suppress evolved kg was byte-identical
    # across 1050-1600 C while wall deposit moved ~1.05e-2 kg to zero.
    # Cross-pO2 deltas are allowed because the sqrt(pO2) suppression mode
    # is meant to hold SiO in the melt and change evaporation.
    return {
        "scope": "fixed_feedstock_and_pO2_mode_wall_T_only",
        "relative_tolerance": SIO_WALL_SWEEP_EVOLVED_REL_TOL,
        "pO2_mode_allowed_to_differ": True,
        "max_relative_delta": _clean_report_float(max_relative_delta),
        "checks": checks,
    }


def _sio_wall_sweep_table(rows: list[Mapping[str, Any]]) -> str:
    headers = (
        "feedstock",
        "pO2_mode",
        "liner_T_C",
        "p_overhead_mbar",
        "Kn",
        "regime_factor",
        "SiO_wall_kg",
        "total_wall_kg",
        "sio_evolved_kg",
        "mass_balance_err_pct",
    )
    lines = [
        "| " + " | ".join(headers) + " |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in _sort_sio_wall_sweep_rows(rows):
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row["feedstock_id"]),
                    str(row["pO2_mode"]),
                    f"{float(row['liner_temperature_C']):g}",
                    f"{float(row['overhead_pressure_mbar']):.12g}",
                    f"{float(row['knudsen_number']):.12g}",
                    f"{float(row['regime_factor']):.12g}",
                    f"{float(row['sio_wall_deposit_kg']):.12g}",
                    f"{float(row['total_wall_deposit_kg']):.12g}",
                    f"{float(row['sio_evolved_kg']):.12g}",
                    f"{float(row['mass_balance_err_pct']):.12g}",
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def build_sio_wall_sweep_report_markdown(summary: Mapping[str, Any]) -> str:
    rows = list(summary.get("rows", []))
    thresholds = summary.get("thresholds", {})
    evolved_guard = summary.get("evolved_invariant_guard", {})
    lines = [
        "# SiO Wall-Deposit Sweep",
        "",
        "Date: 2026-05-19",
        "",
        "Scope: Phase 3-bis wall_deposit versus liner temperature and pO2 mode.",
        "",
        "Regime factor: Kn/(Kn + 0.01), with Kn = mean_free_path / pipe_diameter.",
        "",
        "Slow-fouling threshold: "
        f"{SIO_SLOW_FOULING_WALL_DEPOSIT_KG:.1e} kg SiO wall deposit per campaign. "
        "Non-SiO condensate and liner chemical attack are outside this SiO-fouling verdict.",
        "",
        "## Thresholds",
        "",
        "| case | threshold_liner_T_C |",
        "|---|---:|",
    ]
    for key, value in sorted(thresholds.items()):
        threshold = value.get("threshold_liner_temperature_C")
        rendered = "not crossed" if threshold is None else f"{float(threshold):g}"
        lines.append(f"| {key} | {rendered} |")
    lines.extend(
        [
            "",
            "## Evolved-Total Guard",
            "",
            "Wall-T invariance is checked only at fixed feedstock+pO2 mode; "
            "pO2 modes are allowed to differ because suppression changes evaporation.",
            "",
            "| case | evolved_min_kg | evolved_max_kg | relative_delta | passed |",
            "|---|---:|---:|---:|---|",
        ]
    )
    for key, value in sorted(evolved_guard.get("checks", {}).items()):
        lines.append(
            "| "
            + " | ".join(
                (
                    key,
                    f"{float(value['evolved_min_kg']):.12g}",
                    f"{float(value['evolved_max_kg']):.12g}",
                    f"{float(value['relative_delta']):.3e}",
                    str(bool(value["passed"])),
                )
            )
            + " |"
        )
    lines.extend(["", "## Sweep Table", "", _sio_wall_sweep_table(rows), ""])
    return "\n".join(lines)


def run_sio_wall_sweep(
    *,
    feedstock_ids: tuple[str, ...],
    wall_t_grid_c: tuple[float, ...],
    pO2_modes: tuple[str, ...],
    output_dir: Path,
    campaign: str = SIO_YIELD_CAMPAIGN,
    hours: int = 24,
    mass_kg: float = 1000.0,
    allow_unmeasured_alpha_fallback: bool = False,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for feedstock_id, pO2_mode, liner_temperature_c in itertools.product(
        feedstock_ids, pO2_modes, wall_t_grid_c
    ):
        if pO2_mode not in SIO_WALL_SWEEP_PO2_MODE_CONFIG:
            raise RunnerError(f"unknown pO2 mode {pO2_mode!r}")
        mode_config = SIO_WALL_SWEEP_PO2_MODE_CONFIG[pO2_mode]
        pO2_mbar = mode_config["pO2_mbar"]
        cell_id = _sio_wall_sweep_cell_id(
            feedstock_id, pO2_mode, liner_temperature_c
        )
        report, diagnostics = build_sio_yield_report(
            feedstock_id=feedstock_id,
            campaign=campaign,
            hours=hours,
            mass_kg=mass_kg,
            include_diagnostics=True,
            liner_temperature_c=liner_temperature_c,
            pO2_mbar=pO2_mbar,
            allow_unmeasured_alpha_fallback=allow_unmeasured_alpha_fallback,
        )
        row = _sio_wall_sweep_row(
            cell_id=cell_id,
            feedstock_id=feedstock_id,
            pO2_mode=pO2_mode,
            pO2_mbar=pO2_mbar,
            liner_temperature_c=liner_temperature_c,
            report=report,
            diagnostics=diagnostics,
        )
        cell_doc = {
            "schema_version": SIO_WALL_SWEEP_SCHEMA_VERSION,
            "cell_id": cell_id,
            "mode_label": mode_config["label"],
            "metrics": row,
            "report": report,
            "diagnostics": diagnostics,
        }
        with (output_dir / f"{cell_id}.json").open("w") as f:
            json.dump(
                _json_safe(cell_doc),
                f,
                indent=2,
                sort_keys=False,
                allow_nan=False,
            )
            f.write("\n")
        rows.append(row)

    fieldnames = [
        "cell_id",
        "feedstock_id",
        "pO2_mode",
        "pO2_mbar",
        "liner_temperature_C",
        "overhead_pressure_mbar",
        "knudsen_number",
        "regime_factor",
        "sio_wall_deposit_kg",
        "total_wall_deposit_kg",
        "stage3_silica_kg",
        "sio_evolved_kg",
        "sio_yield_pct_of_feedstock",
        "mass_balance_err_pct",
        "closure_error_pct",
    ]
    with (output_dir / "index.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    thresholds = _sio_wall_sweep_thresholds(rows)
    evolved_invariant_guard = _sio_wall_sweep_evolved_guard(rows)
    return {
        "schema_version": SIO_WALL_SWEEP_SCHEMA_VERSION,
        "campaign": SIO_YIELD_CAMPAIGN,
        "cell_count": len(rows),
        "rows": _sort_sio_wall_sweep_rows(rows),
        "thresholds": thresholds,
        "evolved_invariant_guard": evolved_invariant_guard,
        "output_dir": str(output_dir),
    }


# ----------------------------------------------------------------------
# CLI entry point
# ----------------------------------------------------------------------


def _parse_kv_pairs(items: list[str]) -> dict[str, float]:
    """Parse repeated ``--additive=K=V`` style flags into a dict."""

    parsed: dict[str, float] = {}
    for item in items or []:
        if "=" not in item:
            raise RunnerError(f"expected KEY=VALUE, got {item!r}")
        key, _, raw = item.partition("=")
        try:
            parsed[key.strip()] = float(raw.strip())
        except ValueError as exc:
            raise RunnerError(
                f"could not parse additive value {raw!r} as float"
            ) from exc
    return parsed


def _parse_engine_pairs(items: list[str]) -> dict[str, str]:
    """Parse repeated ``--engine=intent:provider`` flags into a dict."""

    parsed: dict[str, str] = {}
    for item in items or []:
        if ":" not in item:
            raise RunnerError(
                f"expected --engine=intent:provider, got {item!r}"
            )
        intent, _, provider = item.partition(":")
        parsed[intent.strip()] = provider.strip()
    return parsed


def _parse_runtime_campaign_overrides_json(
    raw_json: str | None,
    *,
    flag_name: str,
) -> dict[str, dict[str, float]] | None:
    if raw_json is None:
        return None
    loaded = json.loads(raw_json)
    if not isinstance(loaded, Mapping):
        raise RunnerError(f"{flag_name} must decode to an object")
    parsed: dict[str, dict[str, float]] = {}
    for campaign, fields in loaded.items():
        if not isinstance(fields, Mapping):
            raise RunnerError(
                f"{flag_name}[{campaign!r}] must decode to an object"
            )
        parsed[str(campaign)] = {
            str(field): float(value)
            for field, value in fields.items()
        }
    return parsed


def _safe_failure_value(builder: Any, default: Any) -> Any:
    try:
        return builder()
    except Exception:  # noqa: BLE001 -- failure reporting must survive
        return default


def _execution_hours_completed(execution: RunExecution | None) -> int:
    if execution is None:
        return 0
    return _safe_failure_value(
        lambda: int(getattr(execution.simulator.melt, "hour", 0)),
        0,
    )


def _execution_per_hour_summary(execution: RunExecution | None) -> list[Any]:
    if execution is None:
        return []
    return _json_safe(list(getattr(execution, "per_hour", ()) or ()))


def _execution_shadow_trace(execution: RunExecution | None) -> list[Any]:
    if execution is None:
        return []
    return _json_safe(list(getattr(execution, "shadow_trace", ()) or ()))


def _execution_pO2_enforcement_by_hour(
    execution: RunExecution | None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in _execution_per_hour_summary(execution):
        if isinstance(row, Mapping) and isinstance(row.get("pO2_enforcement"), Mapping):
            rows.append(dict(row["pO2_enforcement"]))
    return _json_safe(rows)


def _runner_failure_result(
    *,
    error: RunnerError,
    feedstock_id: str,
    campaign: str,
    hours: int,
    mass_kg: float,
    additives_kg: Mapping[str, float],
    track: str,
    backend_name: str,
    engines: Mapping[str, str],
    metadata_overrides: Mapping[str, Any],
    reason: str = "",
    status: str = "failed",
    execution: RunExecution | None = None,
    engines_used: Mapping[str, Any] | None = None,
    error_message_override: str | None = None,
) -> dict[str, Any]:
    overrides = dict(metadata_overrides)
    started_at_utc = overrides.pop(
        "started_at_utc",
        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    kernel_commit_sha = overrides.pop(
        "kernel_commit_sha",
        _resolve_kernel_commit_sha(),
    )
    backend_status = (
        str(getattr(execution, "backend_status", "unavailable"))
        if execution is not None
        else "unavailable"
    )
    backend_authoritative = (
        bool(getattr(execution, "backend_authoritative", False))
        if execution is not None
        else False
    )
    if engines_used is None:
        engines_used = {"active": {}, "requested": dict(engines), "registry": {}}
    run_metadata = {
        "schema_version": RUNNER_SCHEMA_VERSION,
        "feedstock_id": feedstock_id,
        "campaign": campaign,
        "hours_requested": int(hours),
        "hours_completed": _execution_hours_completed(execution),
        "mass_kg": _json_safe(float(mass_kg)),
        "additives_kg": _json_safe(dict(additives_kg)),
        "track": track,
        "backend": backend_name,
        "backend_status": backend_status,
        "backend_authoritative": backend_authoritative,
        "started_at_utc": started_at_utc,
        "engines_used": _json_safe(dict(engines_used)),
        "kernel_commit_sha": kernel_commit_sha,
    }
    for key, value in overrides.items():
        run_metadata[str(key)] = _json_safe(value)
    run_metadata["campaigns_elapsed"] = _json_safe(
        float(
            getattr(
                execution,
                "campaigns_elapsed",
                overrides.get("campaigns_elapsed", 1.0),
            )
            if execution is not None
            else overrides.get("campaigns_elapsed", 1.0)
        )
    )
    run_metadata.update(
        canonicalize_fidelity_emission(
            backend_name=backend_name,
            backend_status=backend_status,
            backend_authoritative=backend_authoritative,
        )
    )
    refusal_diagnostic = (
        dict(getattr(execution, "refusal_diagnostic", {}) or {})
        if execution is not None
        else {}
    )
    if refusal_diagnostic:
        run_metadata["refusal_diagnostic"] = _json_safe(
            refusal_diagnostic
        )
        if str(reason or "") == KNUDSEN_REFUSAL_REASON:
            run_metadata["knudsen_regime_diagnostic"] = _json_safe(
                refusal_diagnostic
            )
    sim = getattr(execution, "simulator", None) if execution is not None else None
    final_state = (
        _safe_failure_value(lambda: _final_state_from_ledger(sim), {})
        if sim is not None
        else {}
    )
    final = {
        "wall_deposit_by_species_kg": {},
        "deposit_by_surface_species_kg": {},
        "pump_outlet_by_species_kg": NOT_APPLICABLE_UNTIL_P0,
    }
    if sim is not None:
        final = _safe_failure_value(
            lambda: _final_summary_report(final_state, execution),
            final,
        )
    stage_report = (
        _safe_failure_value(lambda: stage_purity_report(sim.train), {})
        if sim is not None
        else {}
    )
    vapor_report = (
        _safe_failure_value(
            lambda: _vapor_pressure_source_report(sim),
            _empty_vapor_pressure_source_report(),
        )
        if sim is not None
        else _empty_vapor_pressure_source_report()
    )
    degraded_path_engagement = (
        _safe_failure_value(
            lambda: _degraded_path_engagement(sim),
            _empty_degraded_path_engagement(),
        )
        if sim is not None
        else _empty_degraded_path_engagement()
    )
    melt_redox_gate_floor_fallback_engagement = (
        _safe_failure_value(
            lambda: _melt_redox_gate_floor_fallback_engagement(sim),
            _empty_melt_redox_gate_floor_fallback_engagement(),
        )
        if sim is not None
        else _empty_melt_redox_gate_floor_fallback_engagement()
    )
    return {
        "schema_version": RUNNER_SCHEMA_VERSION,
        "run_metadata": run_metadata,
        "final_state": _json_safe(final_state),
        "final": _json_safe(final),
        "stage_purity_report": _json_safe(stage_report),
        "vapor_pressure_source_report": _json_safe(vapor_report),
        "shuttle_refusal_history": _json_safe(
            _safe_failure_value(
                lambda: list(getattr(sim, "_shuttle_refusal_history", []) or []),
                [],
            )
            if sim is not None
            else []
        ),
        "c7_product_report": _json_safe(
            _safe_failure_value(lambda: _c7_product_report(sim), {})
            if sim is not None
            else {}
        ),
        "c7_refusal_diagnostic": _json_safe(
            _safe_failure_value(lambda: _c7_refusal_diagnostic(sim), {})
            if sim is not None
            else {}
        ),
        "degraded_path_engagement": degraded_path_engagement,
        "melt_redox_gate_floor_fallback_engagement": (
            melt_redox_gate_floor_fallback_engagement
        ),
        "pO2_enforcement_by_hour": _execution_pO2_enforcement_by_hour(execution),
        "per_hour_summary": _execution_per_hour_summary(execution),
        "shadow_trace": _execution_shadow_trace(execution),
        "status": status,
        "reason": reason,
        "error_message": (
            error_message_override
            if error_message_override is not None
            else f"RunnerError: {error}"
        ),
    }


def _assert_cli_matches_preset(
    *,
    flag_name: str,
    cli_value: Any,
    preset_value: Any,
    preset: PresetRunSpec,
) -> None:
    if cli_value is None:
        return
    if isinstance(preset_value, float):
        try:
            cli_float = float(cli_value)
        except (TypeError, ValueError) as exc:
            raise PresetRunnerError(
                f"preset_cli_conflict: {flag_name}={cli_value!r} "
                f"does not match preset value {preset_value!r}",
                provenance=preset.provenance,
            ) from exc
        matches = math.isclose(
            cli_float,
            float(preset_value),
            rel_tol=0.0,
            abs_tol=1e-12,
        )
    else:
        matches = str(cli_value) == str(preset_value)
    if not matches:
        raise PresetRunnerError(
            f"preset_cli_conflict: {flag_name}={cli_value!r} "
            f"does not match preset value {preset_value!r}",
            provenance=preset.provenance,
        )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m simulator.runner",
        description=(
            "Deterministic CLI runner for the regolith pyrolysis simulator. "
            "Produces a fully-specified JSON document; see "
            "docs/runner-output-schema.md for the schema contract."
        ),
    )
    parser.add_argument("--feedstock",
                        help="Feedstock ID from data/feedstocks.yaml")
    parser.add_argument("--preset",
                        help="Path to a vacuum-pyrolysis preset YAML")
    parser.add_argument("--leg", default="faithful",
                        help="Preset leg to run when --preset is supplied "
                             "(default: faithful)")
    parser.add_argument("--campaign", default="C0",
                        help="Starting campaign phase (default: C0)")
    parser.add_argument("--hours", type=int, default=None,
                        help="Hours of simulated wallclock to advance")
    parser.add_argument("--mass-kg", type=float, default=None,
                        help="Batch mass in kg (default: 1000)")
    parser.add_argument(
                        "--backend",
                        default=ANALYTICAL_BACKEND_SERIALIZATION_TOKEN,
                        # type folds legacy analytical aliases before choices validation.
                        type=canonical_backend_name,
                        choices=(
                            ANALYTICAL_BACKEND_SERIALIZATION_TOKEN,
                            "alphamelts",
                            "thermoengine",
                        ),
                        help="Melt backend selection (default: internal-analytical)")
    parser.add_argument("--track", default="pyrolysis",
                        choices=("pyrolysis", "mre_baseline"),
                        help="Process track tag (default: pyrolysis)")
    parser.add_argument("--engines",
                        help="Path to engines config YAML (Goal #19 forward "
                             "compat).  Per-intent engine selection.")
    parser.add_argument("--engine", action="append", default=[],
                        metavar="INTENT:PROVIDER",
                        help="Per-intent engine override "
                             "(e.g. --engine=vapor_pressure:vaporock_v1). "
                             "Multiple permitted.")
    parser.add_argument("--additive", action="append", default=[],
                        metavar="SPECIES=KG",
                        help="Additive injection (e.g. --additive=C=30). "
                             "Multiple permitted.")
    parser.add_argument("--runtime-campaign-overrides", default=None,
                        help="JSON runtime per-campaign overrides")
    parser.add_argument("--setpoints-overrides", default=None,
                         help="Deprecated alias for "
                              "--runtime-campaign-overrides")
    parser.add_argument("--recipe", default=None,
                        help="Path to an optimizer setpoints_patch recipe YAML. "
                             "Recipe setpoints are merged before runtime "
                             "campaign overrides, so runtime overrides remain "
                             "the final word for their fields.")
    parser.add_argument("--allow-fallback-vapor", action="store_true",
                        help="Permit builtin vapor-pressure fallback")
    parser.add_argument("--allow-unmeasured-alpha-fallback",
                        action="store_true",
                        help="Permit configured fallback evaporation alpha")
    parser.add_argument("--force-builtin-vapor-pressure",
                        action="store_true",
                        help="Force builtin vapor-pressure provider")
    parser.add_argument("--sio-start-temperature-c", type=float, default=None,
                        help="Set initial melt temperature before SiO run")
    parser.add_argument("--sio-hold-temperature-c", type=float, default=None,
                        help="Override SiO campaign hold temperature")
    parser.add_argument("--sio-ramp-c-per-hr", type=float, default=None,
                        help="Override SiO campaign ramp rate")
    parser.add_argument("--sio-liner-temperature-c", type=float, default=None,
                        help="Override SiO overhead liner temperature")
    parser.add_argument("--sio-po2-mbar", type=float, default=None,
                        help="Override SiO controlled pO2 in mbar")
    parser.add_argument("--output", required=True,
                        help="Path to write the JSON result document")
    parser.add_argument("--started-at-utc", default=None,
                        help="Override run_metadata.started_at_utc "
                             "(for deterministic fixtures)")
    parser.add_argument("--kernel-commit-sha", default=None,
                        help="Override run_metadata.kernel_commit_sha "
                             "(for deterministic fixtures)")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    metadata_overrides: dict[str, Any] = {}
    if args.started_at_utc:
        metadata_overrides["started_at_utc"] = args.started_at_utc
    if args.kernel_commit_sha:
        metadata_overrides["kernel_commit_sha"] = args.kernel_commit_sha

    additives: dict[str, float] = {}
    merged: dict[str, str] = {}
    runtime_campaign_overrides: dict[str, dict[str, Any]] = {}
    feedstock_id = str(args.feedstock or "")
    campaign = str(args.campaign)
    hours = int(args.hours) if args.hours is not None else 24
    mass_kg = float(args.mass_kg) if args.mass_kg is not None else 1000.0

    try:
        additives = _parse_kv_pairs(args.additive)
        engine_overrides = _parse_engine_pairs(args.engine)
        if args.engines:
            file_engines = _load_engines_config(Path(args.engines))
            # CLI --engine flags win over file entries; the CLI surface is
            # the operator's last word.
            merged = {**file_engines, **engine_overrides}
        else:
            merged = engine_overrides
        runtime_campaign_overrides = _canonical_runtime_campaign_overrides(
            runtime_campaign_overrides=_parse_runtime_campaign_overrides_json(
                args.runtime_campaign_overrides,
                flag_name="--runtime-campaign-overrides",
            ),
            setpoints_overrides=_parse_runtime_campaign_overrides_json(
                args.setpoints_overrides,
                flag_name="--setpoints-overrides",
            ),
        )

        setpoints_patch: Mapping[str, Any] = {}
        lab_schedule: Mapping[str, Any] | None = None
        if args.preset:
            preset = _load_preset_run_spec(Path(args.preset), str(args.leg))
            _assert_cli_matches_preset(
                flag_name="--feedstock",
                cli_value=args.feedstock,
                preset_value=preset.feedstock_id,
                preset=preset,
            )
            _assert_cli_matches_preset(
                flag_name="--hours",
                cli_value=args.hours,
                preset_value=preset.hours,
                preset=preset,
            )
            _assert_cli_matches_preset(
                flag_name="--mass-kg",
                cli_value=args.mass_kg,
                preset_value=preset.mass_kg,
                preset=preset,
            )
            feedstock_id = preset.feedstock_id
            hours = preset.hours
            mass_kg = preset.mass_kg
            setpoints_patch = {
                "lab_geometry": copy.deepcopy(preset.lab_geometry),
            }
            lab_schedule = copy.deepcopy(preset.lab_schedule)
            metadata_overrides[PRESET_PROVENANCE_METADATA_KEY] = dict(
                preset.provenance
            )
        elif not feedstock_id:
            raise RunnerError("--feedstock is required unless --preset is supplied")
        if args.recipe:
            try:
                recipe_patch = load_recipe_patch(Path(args.recipe))
            except RecipeIOError as exc:
                raise RunnerError(str(exc)) from exc
            setpoints_patch = _deep_merge_setpoints(setpoints_patch, recipe_patch)

        run = PyrolysisRun(
            feedstock_id=feedstock_id,
            campaign=campaign,
            hours=hours,
            engines=merged,
            additives_kg=additives,
            mass_kg=mass_kg,
            backend_name=args.backend,
            setpoints_patch=setpoints_patch,
            runtime_campaign_overrides=runtime_campaign_overrides,
            lab_schedule=lab_schedule,
            track=args.track,
            allow_fallback_vapor=bool(args.allow_fallback_vapor),
            allow_unmeasured_alpha_fallback=bool(
                args.allow_unmeasured_alpha_fallback
            ),
            force_builtin_vapor_pressure=bool(args.force_builtin_vapor_pressure),
            sio_start_temperature_c=args.sio_start_temperature_c,
            sio_hold_temperature_c=args.sio_hold_temperature_c,
            sio_ramp_c_per_hr=args.sio_ramp_c_per_hr,
            sio_liner_temperature_c=args.sio_liner_temperature_c,
            sio_pO2_mbar=args.sio_po2_mbar,
            run_metadata_overrides=metadata_overrides,
        )
        result = run.run()
    except (RunnerError, json.JSONDecodeError, TypeError, ValueError) as exc:
        if isinstance(exc, PresetRunnerError) and exc.provenance:
            metadata_overrides.setdefault(
                PRESET_PROVENANCE_METADATA_KEY,
                dict(exc.provenance),
            )
        runner_error = exc if isinstance(exc, RunnerError) else RunnerError(str(exc))
        result = _runner_failure_result(
            error=runner_error,
            feedstock_id=feedstock_id,
            campaign=campaign,
            hours=hours,
            mass_kg=mass_kg,
            additives_kg=additives,
            track=args.track,
            # Failure envelope: fold the alias so even the error path serializes
            # the stable `stub` token (the success path folds in PyrolysisRun).
            backend_name=canonical_backend_name(args.backend),
            engines=merged,
            metadata_overrides=metadata_overrides,
        )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w") as f:
        json.dump(
            _json_safe(result),
            f,
            indent=2,
            sort_keys=False,
            allow_nan=False,
        )
        f.write("\n")

    return 0 if result["status"] not in {"failed", "refused"} else 1


def build_sio_yield_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m simulator.runner.sio_yield",
        description="Generate the C2A SiO yield / silica-fume report JSON.",
    )
    parser.add_argument(
        "--feedstock",
        required=True,
        choices=SIO_YIELD_FEEDSTOCKS,
        help="Feedstock ID to run",
    )
    parser.add_argument(
        "--campaign",
        default=SIO_YIELD_CAMPAIGN,
        choices=(SIO_YIELD_CAMPAIGN, "C2A"),
        help="SiO campaign alias (default: C2A_continuous)",
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=24,
        help="Hours of simulated wallclock to advance",
    )
    parser.add_argument(
        "--mass-kg",
        type=float,
        default=1000.0,
        help="Batch feedstock mass in kg (default: 1000)",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Path to write the SiO yield report JSON",
    )
    parser.add_argument(
        "--lab-oxygen-diagnostics-output",
        help="Optional JSON sidecar path for lab oxygen-atom diagnostics",
    )
    parser.add_argument(
        "--allow-unmeasured-alpha-fallback",
        action="store_true",
        help="Permit configured fallback evaporation alpha",
    )
    return parser


def main_sio_yield(argv: Optional[list[str]] = None) -> int:
    parser = build_sio_yield_arg_parser()
    args = parser.parse_args(argv)

    try:
        result = build_sio_yield_report(
            feedstock_id=args.feedstock,
            campaign=args.campaign,
            hours=int(args.hours),
            mass_kg=float(args.mass_kg),
            include_diagnostics=bool(args.lab_oxygen_diagnostics_output),
            include_lab_oxygen_diagnostics=bool(
                args.lab_oxygen_diagnostics_output
            ),
            allow_unmeasured_alpha_fallback=(
                args.allow_unmeasured_alpha_fallback
            ),
        )
    except RunnerError as exc:
        parser.error(str(exc))
    if args.lab_oxygen_diagnostics_output:
        report, diagnostics = result
        diagnostics_path = Path(args.lab_oxygen_diagnostics_output)
        diagnostics_path.parent.mkdir(parents=True, exist_ok=True)
        with diagnostics_path.open("w") as f:
            cli_diagnostics = dict(
                diagnostics["lab_oxygen_atom_partition"]
            )
            cli_diagnostics["lab_plume_product_partition"] = diagnostics[
                "lab_plume_product_partition"
            ]
            json.dump(
                _json_safe(cli_diagnostics),
                f,
                indent=2,
                sort_keys=False,
                allow_nan=False,
            )
            f.write("\n")
    else:
        report = result

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w") as f:
        json.dump(
            _json_safe(report),
            f,
            indent=2,
            sort_keys=False,
            allow_nan=False,
        )
        f.write("\n")
    return 0


def build_sio_tsweep_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m simulator.runner.sio_tsweep",
        description="Run the C2A SiO T-window sweep and write cell JSON/index CSV.",
    )
    parser.add_argument(
        "--feedstock",
        required=True,
        choices=SIO_YIELD_FEEDSTOCKS,
        help="Feedstock ID to run",
    )
    parser.add_argument(
        "--campaign",
        default=SIO_YIELD_CAMPAIGN,
        choices=(SIO_YIELD_CAMPAIGN, "C2A"),
        help="SiO campaign alias (default: C2A_continuous)",
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=24,
        help="Hours of simulated wallclock to advance per cell",
    )
    parser.add_argument(
        "--mass-kg",
        type=float,
        default=1000.0,
        help="Batch feedstock mass in kg (default: 1000)",
    )
    parser.add_argument(
        "--t-low-grid",
        default=",".join(
            _format_sweep_float(value) for value in SIO_TSWEEP_DEFAULT_T_LOW_GRID_C
        ),
        help="Comma-separated T_low grid in C",
    )
    parser.add_argument(
        "--t-hold-grid",
        default=",".join(
            _format_sweep_float(value) for value in SIO_TSWEEP_DEFAULT_T_HOLD_GRID_C
        ),
        help="Comma-separated T_hold grid in C",
    )
    parser.add_argument(
        "--ramp-grid",
        default=",".join(
            _format_sweep_float(value)
            for value in SIO_TSWEEP_DEFAULT_RAMP_GRID_C_PER_HR
        ),
        help="Comma-separated ramp grid in C/hr",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Directory to write cell JSON files plus index.csv",
    )
    parser.add_argument(
        "--report-output",
        help="Optional Markdown report path for this feedstock",
    )
    parser.add_argument(
        "--summary-output",
        help="Optional JSON summary path with recommendation metadata",
    )
    parser.add_argument(
        "--allow-unmeasured-alpha-fallback",
        action="store_true",
        help="Permit configured fallback evaporation alpha",
    )
    return parser


def main_sio_tsweep(argv: Optional[list[str]] = None) -> int:
    parser = build_sio_tsweep_arg_parser()
    args = parser.parse_args(argv)

    try:
        summary = run_sio_tsweep(
            feedstock_id=args.feedstock,
            campaign=args.campaign,
            hours=int(args.hours),
            mass_kg=float(args.mass_kg),
            t_low_grid_c=_parse_float_grid(
                args.t_low_grid, label="--t-low-grid"
            ),
            t_hold_grid_c=_parse_float_grid(
                args.t_hold_grid, label="--t-hold-grid"
            ),
            ramp_grid_c_per_hr=_parse_float_grid(
                args.ramp_grid, label="--ramp-grid"
            ),
            output_dir=Path(args.output_dir),
            allow_unmeasured_alpha_fallback=(
                args.allow_unmeasured_alpha_fallback
            ),
        )
    except RunnerError as exc:
        parser.error(str(exc))

    if args.report_output:
        report_path = Path(args.report_output)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            build_sio_tsweep_report_markdown(
                feedstock_id=args.feedstock,
                rows=summary["rows"],
            )
        )
    if args.summary_output:
        summary_path = Path(args.summary_output)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        with summary_path.open("w") as f:
            json.dump(
                _json_safe(summary),
                f,
                indent=2,
                sort_keys=False,
                allow_nan=False,
            )
            f.write("\n")
    return 0


def build_sio_wall_sweep_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m simulator.runner.sio_wall_sweep",
        description=(
            "Run the Phase 3-bis SiO wall-deposit sweep over liner T and pO2 mode."
        ),
    )
    parser.add_argument(
        "--feedstocks",
        default=",".join(SIO_YIELD_FEEDSTOCKS),
        help="Comma-separated feedstock IDs to run",
    )
    parser.add_argument(
        "--campaign",
        default=SIO_YIELD_CAMPAIGN,
        choices=(SIO_YIELD_CAMPAIGN, "C2A"),
        help="SiO campaign alias (default: C2A_continuous)",
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=24,
        help="Hours of simulated wallclock to advance per cell",
    )
    parser.add_argument(
        "--mass-kg",
        type=float,
        default=1000.0,
        help="Batch feedstock mass in kg (default: 1000)",
    )
    parser.add_argument(
        "--wall-t-grid",
        default=",".join(
            _format_sweep_float(value)
            for value in SIO_WALL_SWEEP_DEFAULT_WALL_T_GRID_C
        ),
        help="Comma-separated liner temperature grid in C",
    )
    parser.add_argument(
        "--pO2-modes",
        default=",".join(SIO_WALL_SWEEP_DEFAULT_PO2_MODES),
        help="Comma-separated modes: no_suppress,o2_1mbar",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Directory to write cell JSON files plus index.csv",
    )
    parser.add_argument(
        "--report-output",
        help="Optional Markdown report path",
    )
    parser.add_argument(
        "--summary-output",
        help="Optional JSON summary path with thresholds",
    )
    parser.add_argument(
        "--allow-unmeasured-alpha-fallback",
        action="store_true",
        help="Permit configured fallback evaporation alpha",
    )
    return parser


def main_sio_wall_sweep(argv: Optional[list[str]] = None) -> int:
    parser = build_sio_wall_sweep_arg_parser()
    args = parser.parse_args(argv)
    feedstock_ids = tuple(
        item.strip() for item in str(args.feedstocks).split(",") if item.strip()
    )
    invalid_feedstocks = sorted(set(feedstock_ids) - set(SIO_YIELD_FEEDSTOCKS))
    if invalid_feedstocks:
        parser.error(
            "SiO wall sweep supports feedstocks "
            f"{', '.join(SIO_YIELD_FEEDSTOCKS)}; got {invalid_feedstocks}"
        )
    pO2_modes = tuple(
        item.strip() for item in str(args.pO2_modes).split(",") if item.strip()
    )
    invalid_modes = sorted(set(pO2_modes) - set(SIO_WALL_SWEEP_PO2_MODE_CONFIG))
    if invalid_modes:
        parser.error(
            "unknown pO2 mode(s) "
            f"{invalid_modes}; expected {sorted(SIO_WALL_SWEEP_PO2_MODE_CONFIG)}"
        )

    try:
        summary = run_sio_wall_sweep(
            feedstock_ids=feedstock_ids,
            campaign=args.campaign,
            hours=int(args.hours),
            mass_kg=float(args.mass_kg),
            wall_t_grid_c=_parse_float_grid(
                args.wall_t_grid, label="--wall-t-grid"
            ),
            pO2_modes=pO2_modes,
            output_dir=Path(args.output_dir),
            allow_unmeasured_alpha_fallback=(
                args.allow_unmeasured_alpha_fallback
            ),
        )
    except RunnerError as exc:
        parser.error(str(exc))

    if args.report_output:
        report_path = Path(args.report_output)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(build_sio_wall_sweep_report_markdown(summary))
    if args.summary_output:
        summary_path = Path(args.summary_output)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        with summary_path.open("w") as f:
            json.dump(
                _json_safe(summary),
                f,
                indent=2,
                sort_keys=False,
                allow_nan=False,
            )
            f.write("\n")
    return 0


class _SiOYieldModuleLoader(importlib.abc.Loader):
    def __init__(self, main_name: str = "main_sio_yield") -> None:
        self._main_name = main_name

    def get_code(self, fullname: str):
        source = (
            f"from simulator.runner import {self._main_name}\n"
            f"raise SystemExit({self._main_name}())\n"
        )
        return compile(source, f"<{fullname}>", "exec")

    def create_module(self, spec):  # noqa: D401
        return None

    def exec_module(self, module) -> None:
        return None


class _SiOYieldModuleFinder(importlib.abc.MetaPathFinder):
    _sio_yield_finder = True
    _ENTRYPOINTS = {
        "simulator.runner.sio_yield": "main_sio_yield",
        "simulator.runner.sio_tsweep": "main_sio_tsweep",
        "simulator.runner.sio_wall_sweep": "main_sio_wall_sweep",
    }

    def find_spec(self, fullname: str, path=None, target=None):
        main_name = self._ENTRYPOINTS.get(fullname)
        if main_name is None:
            return None
        return importlib.machinery.ModuleSpec(
            fullname,
            _SiOYieldModuleLoader(main_name),
            is_package=False,
        )


def _install_sio_yield_entrypoint() -> None:
    if __name__ != "simulator.runner":
        return
    globals()["__path__"] = []
    if not any(
        getattr(finder, "_sio_yield_finder", False)
        for finder in sys.meta_path
    ):
        sys.meta_path.insert(0, _SiOYieldModuleFinder())


_install_sio_yield_entrypoint()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
