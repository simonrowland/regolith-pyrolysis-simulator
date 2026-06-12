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

from simulator.backends import (
    BackendSelectionPolicy,
)
from simulator.config import ConfigBundle, load_config_bundle
from simulator.core import (
    CampaignPhase,
    PyrolysisSimulator,
)
from simulator.condensation import (
    stage_purity_report,
)
from simulator.run_executor import RunExecution, RunExecutor, _json_safe
from simulator.lab_schedule import LAB_SCHEDULE_OVERRIDE_KEY
from simulator.session import (
    SimSession,
    SimSessionConfig,
)
from simulator.state import (
    Atmosphere,
    HourSnapshot,
    MOLAR_MASS,
    PIPE_SEGMENT_WALL_DEPOSIT_ACCOUNT_PREFIX,
)

# Public schema version pinned by docs/runner-output-schema.md.
RUNNER_SCHEMA_VERSION = "1.3.0"
ZERO_INPUT_BASIS_BREACH = "zero_input_basis_breach"

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
    "Phase 1 α surface (commit fc2d40b); "
    "SF2004 Table 10 SiO2(liq) Hashimoto 1990"
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
SIO_TSWEEP_SCHEMA_VERSION = "sio-tsweep-v1"
SIO_TSWEEP_DEFAULT_T_LOW_GRID_C: tuple[float, ...] = (1050.0, 1100.0, 1150.0)
SIO_TSWEEP_DEFAULT_T_HOLD_GRID_C: tuple[float, ...] = (1400.0, 1500.0, 1600.0)
SIO_TSWEEP_DEFAULT_RAMP_GRID_C_PER_HR: tuple[float, ...] = (5.0, 10.0, 15.0)
SIO_TSWEEP_MASS_BALANCE_LIMIT_PCT = 5.0e-12
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


class RunnerError(RuntimeError):
    """Public exception for runner-level failures (config, load, IO).

    Physics-level failures bubble up through ``PyrolysisSimulator.step``
    and are caught in :meth:`PyrolysisRun.run` to populate the
    ``status=failed`` envelope.
    """


class EngineBugAbort(RunnerError):
    """Fatal runner abort for corrupted engine snapshots."""


# ----------------------------------------------------------------------
# Data loading helpers
# ----------------------------------------------------------------------


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        raise RunnerError(f"required data file missing: {path}")
    with path.open() as f:
        return yaml.safe_load(f) or {}


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
        raise RunnerError(
            "setpoints_patch may not contain top-level 'chemistry_kernel'; "
            "use fallback flags instead"
        )
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
    campaigns = setpoints.get("campaigns", {})
    if not isinstance(campaigns, Mapping):
        return additives
    c3 = campaigns.get("C3", {})
    if not isinstance(c3, Mapping):
        return additives
    dosing = c3.get("alkali_dosing", {})
    if dosing in (None, {}):
        return additives
    if not isinstance(dosing, Mapping):
        raise RunnerError("campaigns.C3.alkali_dosing must be a mapping")

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
        if dose_kg <= 0.0:
            continue
        raw_additive_kg = additives.get(species, 0.0)
        if raw_additive_kg > 0.0 and not math.isclose(
            raw_additive_kg, dose_kg, rel_tol=0.0, abs_tol=1.0e-12
        ):
            raise RunnerError(
                f"campaigns.C3.alkali_dosing.{key} conflicts with "
                f"additives_kg[{species!r}]"
            )
        additives[species] = dose_kg
    return additives


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
    backend_name: str = "stub"
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

    def __post_init__(self) -> None:
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

        Catches any per-step exception and returns a ``status=failed``
        envelope rather than propagating: the CLI surface promises a
        JSON document on every invocation so calling pipelines can
        diff failure reasons without parsing stderr.
        """

        if self._has_sio_pre_run_controls():
            session = self._start_session()
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
        self._apply_sio_pre_run_controls(session.simulator)
        execution = RunExecutor().execute_session(session, hours=int(self.hours))
        document = self._build_output(execution)
        execution.session._set_result_document(document)
        return document

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
        if (
            self.allow_fallback_vapor
            or self.force_builtin_vapor_pressure
            or self.allow_unmeasured_alpha_fallback
        ):
            setpoints = dict(setpoints)
            kernel_config = dict(setpoints.get("chemistry_kernel", {}) or {})
            if self.allow_fallback_vapor or self.force_builtin_vapor_pressure:
                kernel_config["allow_fallback_vapor"] = True
            if self.allow_unmeasured_alpha_fallback:
                kernel_config["allow_unmeasured_alpha_fallback"] = True
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
        return SimSessionConfig(
            feedstock_id=self.feedstock_id,
            feedstocks=feedstocks,
            setpoints=setpoints,
            vapor_pressures=vapor_pressures,
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
        if execution.reduced_real_cache:
            run_metadata["reduced_real_cache"] = _json_safe(
                execution.reduced_real_cache
            )
        # Anything left in metadata_overrides is propagated verbatim --
        # callers can stuff extra provenance (CI run id, etc.) without
        # the runner needing to know about it.
        for key, value in metadata_overrides.items():
            run_metadata[str(key)] = value

        final_state = _final_state_from_ledger(sim)
        knudsen_diagnostic = dict(
            execution.refusal_diagnostic
            or _knudsen_regime_diagnostic_from_sim(sim)
        )
        if knudsen_diagnostic:
            run_metadata["knudsen_regime_diagnostic"] = _json_safe(
                knudsen_diagnostic)

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
        pO2_enforcement_by_hour = [
            dict(row["pO2_enforcement"])
            for row in execution.per_hour
            if isinstance(row, Mapping) and isinstance(row.get("pO2_enforcement"), Mapping)
        ]

        return {
            "schema_version": RUNNER_SCHEMA_VERSION,
            "run_metadata": run_metadata,
            "final_state": final_state,
            "final": _final_summary_report(final_state, execution),
            "stage_purity_report": stage_purity_report(sim.train),
            "vapor_pressure_source_report": _vapor_pressure_source_report(sim),
            "shuttle_refusal_history": _json_safe(shuttle_refusal_history),
            "pO2_enforcement_by_hour": _json_safe(pO2_enforcement_by_hour),
            "per_hour_summary": list(execution.per_hour),
            "shadow_trace": list(execution.shadow_trace),
            "status": execution.status,
            "reason": execution.reason,
            "error_message": execution.error_message,
        }

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
            capability = registry.capability_summary()
        except AttributeError:
            capability = {}
        internal_intents = {"backend_equilibrium"}
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


# ----------------------------------------------------------------------
# Per-hour summary builder (shared with web stream)
# ----------------------------------------------------------------------


def _vapor_pressure_source_report(sim: PyrolysisSimulator) -> dict[str, object]:
    source_by_species = {
        str(species): str(source)
        for species, source in sorted(
            (getattr(sim, "_last_vapor_pressures_source", {}) or {}).items()
        )
    }
    total = len(source_by_species)
    counts = Counter(source_by_species.values())
    return {
        "species": source_by_species,
        "summary": {
            source: {
                "count": count,
                "percentage": round(count / total * 100.0, 6) if total else 0.0,
            }
            for source, count in sorted(counts.items())
        },
        "total_species": total,
    }


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


def build_per_hour_summary(
    sim: PyrolysisSimulator,
    snapshot: HourSnapshot,
) -> dict:
    """Build the per-hour summary entry for both the CLI runner and the
    SocketIO stream.

    Schema fields (pinned by docs/runner-output-schema.md):

    * ``hour``: snapshot hour
    * ``campaign``: snapshot campaign name (``CampaignPhase.name``)
    * ``T_C``: melt temperature in Celsius
    * ``P_total_bar``: total pressure above the melt in bar
    * ``pO2_bar``: pO2 partial pressure in bar
    * ``mass_balance_pct``: ledger-based mass balance error, percent
    * ``O2_yield_kg_cumulative``: cumulative O2 from all bins (kg)
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

    summary = {
        "hour": int(snapshot.hour),
        "campaign": snapshot.campaign.name,
        "T_C": float(snapshot.temperature_C),
        "P_total_bar": p_total_bar,
        "pO2_bar": pO2_bar,
        "mass_balance_pct": mass_balance_pct,
        "O2_yield_kg_cumulative": float(snapshot.oxygen_produced_kg),
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
    }
    if mass_balance_category:
        summary["mass_balance_error_category"] = mass_balance_category
    enforcement = getattr(sim.campaign_mgr, "last_pO2_enforcement", None)
    if isinstance(enforcement, Mapping) and int(enforcement.get("hour", -1)) == int(snapshot.hour):
        summary["pO2_enforcement"] = _json_safe(dict(enforcement))
    return summary


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
    return int(round(float(value_c) + 273.15))


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


def _wall_fouling_report(wall_deposit_kg: Mapping[str, float]) -> dict[str, Any]:
    cfg = _wall_liner_resinter_config()
    positive = {
        species: float(kg)
        for species, kg in wall_deposit_kg.items()
        if float(kg) > 0.0
    }
    dominant_species = max(positive, key=positive.get) if positive else "none"
    dominant_kg = positive.get(dominant_species, 0.0) if dominant_species else 0.0
    threshold = cfg.get("resinter_threshold_kg")
    fast_n = int(cfg["fast_fouling_campaign_threshold"])
    if dominant_kg <= 0.0:
        campaigns_to_resinter: float | str = "infinite"
        verdict = "slow-fouling"
    elif threshold is None:
        campaigns_to_resinter = (
            f"resinter_threshold_kg / {dominant_kg:.12g}"
        )
        verdict = (
            "threshold-parametric: fast-fouling if campaigns_to_resinter "
            f"< {fast_n}, else slow-fouling"
        )
    else:
        campaigns_to_resinter = float(threshold) / dominant_kg
        verdict = (
            "fast-fouling"
            if campaigns_to_resinter < fast_n
            else "slow-fouling"
        )
    return {
        "liner_material": cfg["liner_material"],
        "dominant_species": dominant_species,
        "wall_deposit_kg_per_campaign": _clean_report_float(dominant_kg),
        "resinter_threshold_kg": threshold,
        "resinter_threshold_basis": cfg.get("resinter_threshold_basis"),
        "campaigns_to_resinter": campaigns_to_resinter,
        "fast_fouling_campaign_threshold": fast_n,
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
            return (float(t_hold_c), ramp_rate)
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
        sim.melt.fO2_log = sim._compute_intrinsic_melt_fO2()

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
            gas_temperature_C=float(liner_temperature_c),
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
    t_low_c: float | None = None,
    t_hold_c: float | None = None,
    ramp_c_per_hr: float | None = None,
    liner_temperature_c: float | None = None,
    pO2_mbar: float | None = None,
    allow_unmeasured_alpha_fallback: bool = False,
) -> dict[str, Any] | tuple[dict[str, Any], dict[str, float]]:
    """Run the C2A SiO yield slice and return the golden-file report."""

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
        sio_alpha_value = alpha_by_species["SiO"]
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
        backend_name="stub",
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

    final_sio2_mol = float(cleaned_melt.get("SiO2", 0.0))
    sio_evaporated_mol = max(0.0, initial_sio2_mol - final_sio2_mol)
    si_terminal_mol = float(condensation_train.get("Si", 0.0))
    sio2_terminal_mol = float(condensation_train.get("SiO2", 0.0))
    sio_wall_mol = float(wall_deposit.get("SiO", 0.0))
    sio_escape_mol = float(terminal_offgas.get("SiO", 0.0))
    terminal_mol = (
        si_terminal_mol
        + sio2_terminal_mol
        + sio_wall_mol
        + sio_escape_mol
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
    wall_deposit_kg = _wall_deposit_report_kg(final_state)
    wall_fouling = _wall_fouling_report(wall_deposit_kg)

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
        condensation_model = sim.condensation_model
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
            "wall_deposit_pipe_segment_temperatures_C": dict(
                operating_entry.get("pipe_segment_temperatures_C", {}) or {}
            ),
            "cold_spot_diagnostic": dict(
                getattr(
                    condensation_model, "last_cold_spot_diagnostic", {}
                ) or {}
            ),
        }
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
        "Caveat: alpha_SiO = 0.04 from the Phase 1 alpha surface "
        "(commit `fc2d40b`). Stage 3 is post-Cr v2 "
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
        "Caveat: alpha_SiO = 0.04. Stage 3 is post-Cr v2 "
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
            json.dump(cell_doc, f, indent=2, sort_keys=False)
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
            json.dump(cell_doc, f, indent=2, sort_keys=False)
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


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m simulator.runner",
        description=(
            "Deterministic CLI runner for the regolith pyrolysis simulator. "
            "Produces a fully-specified JSON document; see "
            "docs/runner-output-schema.md for the schema contract."
        ),
    )
    parser.add_argument("--feedstock", required=True,
                        help="Feedstock ID from data/feedstocks.yaml")
    parser.add_argument("--campaign", default="C0",
                        help="Starting campaign phase (default: C0)")
    parser.add_argument("--hours", type=int, default=24,
                        help="Hours of simulated wallclock to advance")
    parser.add_argument("--mass-kg", type=float, default=1000.0,
                        help="Batch mass in kg (default: 1000)")
    parser.add_argument("--backend", default="stub",
                        choices=("stub", "alphamelts"),
                        help="Melt backend selection (default: stub)")
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

    metadata_overrides: dict[str, Any] = {}
    if args.started_at_utc:
        metadata_overrides["started_at_utc"] = args.started_at_utc
    if args.kernel_commit_sha:
        metadata_overrides["kernel_commit_sha"] = args.kernel_commit_sha

    run = PyrolysisRun(
        feedstock_id=args.feedstock,
        campaign=args.campaign,
        hours=int(args.hours),
        engines=merged,
        additives_kg=additives,
        mass_kg=float(args.mass_kg),
        backend_name=args.backend,
        runtime_campaign_overrides=runtime_campaign_overrides,
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

    try:
        result = run.run()
    except RunnerError as exc:
        # Autoreview r5 P2 (2026-05-27): the failure envelope MUST
        # match the schema version it advertises. Any top-level key
        # the happy-path output emits has to be present here too
        # (with an empty/zero default) so downstream consumers don't
        # have to special-case failed runs. Keep this dict in lockstep
        # with `RunnerOrchestrator._build_output` keys + the
        # ``TOP_LEVEL_KEYS`` set in tests/test_runner_smoke.py.
        result = {
            "schema_version": RUNNER_SCHEMA_VERSION,
            "run_metadata": {
                "schema_version": RUNNER_SCHEMA_VERSION,
                "feedstock_id": args.feedstock,
                "campaign": args.campaign,
                "hours_requested": int(args.hours),
                "hours_completed": 0,
                "mass_kg": float(args.mass_kg),
                "additives_kg": additives,
                "track": args.track,
                "backend": args.backend,
                "started_at_utc": metadata_overrides.get(
                    "started_at_utc",
                    datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                ),
                "engines_used": {"requested": merged, "registry": {}},
                "kernel_commit_sha": metadata_overrides.get(
                    "kernel_commit_sha", _resolve_kernel_commit_sha()),
            },
            "final_state": {},
            "final": {
                "wall_deposit_by_species_kg": {},
                "deposit_by_surface_species_kg": {},
                "pump_outlet_by_species_kg": NOT_APPLICABLE_UNTIL_P0,
            },
            "stage_purity_report": {},
            "vapor_pressure_source_report": {
                "species": {},
                "summary": {},
                "total_species": 0,
            },
            "shuttle_refusal_history": [],
            "pO2_enforcement_by_hour": [],
            "per_hour_summary": [],
            "shadow_trace": [],
            "status": "failed",
            "reason": "",
            "error_message": f"RunnerError: {exc}",
        }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w") as f:
        json.dump(result, f, indent=2, sort_keys=False)
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
    return parser


def main_sio_yield(argv: Optional[list[str]] = None) -> int:
    parser = build_sio_yield_arg_parser()
    args = parser.parse_args(argv)

    try:
        report = build_sio_yield_report(
            feedstock_id=args.feedstock,
            campaign=args.campaign,
            hours=int(args.hours),
            mass_kg=float(args.mass_kg),
        )
    except RunnerError as exc:
        parser.error(str(exc))

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w") as f:
        json.dump(report, f, indent=2, sort_keys=False)
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
            json.dump(summary, f, indent=2, sort_keys=False)
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
            json.dump(summary, f, indent=2, sort_keys=False)
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
