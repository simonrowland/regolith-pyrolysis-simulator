"""Deterministic CLI runner harness for the Oxygen-Shuttle simulator.

This module is the single source of truth for the simulator's
non-streaming run path.  Two consumers:

* The ``python -m simulator.runner`` CLI emits a fully-specified JSON
  result document via :class:`PyrolysisRun`.
* ``web/events.py`` reuses the same internals (loader, decision
  auto-apply, per-hour summary builder) so the SocketIO live stream
  yields the same per-hour shape the CLI commits to fixtures.

Goal #18 ``JSON-RUNNER-HARNESS`` invariants this module owns:

* No new physics: the runner orchestrates ``PyrolysisSimulator.step``;
  it never reaches into the kernel commit path or the ledger directly.
* No branching of the physics path: web vs CLI both call
  :meth:`PyrolysisRun.run` or :meth:`PyrolysisRun.iter_hours`.
* Deterministic JSON output: any wall-clock fields (``started_at_utc``,
  ``kernel_commit_sha``) accept caller-supplied overrides so golden
  fixtures stay stable across machines and time.

The JSON schema is pinned by ``docs/runner-output-schema.md`` and the
schema-shape assertion in ``tests/test_runner_smoke.py``.
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import importlib.abc
import importlib.machinery
import itertools
import json
import math
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Optional

import yaml

from simulator.backends import (
    BackendSelectionPolicy,
    SimulatorBuildConfig,
    build_simulator,
    resolve_backend,
)
from simulator.core import (
    BACKEND_FALLBACK_EXCEPTIONS,
    CampaignPhase,
    PyrolysisSimulator,
)
from simulator.state import HourSnapshot, MOLAR_MASS

# Public schema version pinned by docs/runner-output-schema.md.
RUNNER_SCHEMA_VERSION = "1.0.0"

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


# ----------------------------------------------------------------------
# Public dataclass: runner configuration
# ----------------------------------------------------------------------


@dataclass
class PyrolysisRun:
    """Configuration for a single deterministic simulator run.

    Attributes mirror the Goal #18 CHECKLIST exactly so the CLI flags
    map 1:1 to dataclass fields.

    ``setpoints_overrides`` is a mapping ``{campaign_name: {field:
    value}}`` -- written straight onto ``CampaignManager.overrides``
    after batch load.  Today the runner only forwards what the
    simulator already accepts via the existing web's
    ``campaign_override`` path; no new override fields are introduced.
    """

    feedstock_id: str
    campaign: str = "C0"
    hours: int = 24
    engines: dict[str, str] = field(default_factory=dict)
    additives_kg: dict[str, float] = field(default_factory=dict)
    mass_kg: float = 1000.0
    backend_name: str = "stub"
    setpoints_overrides: dict[str, dict] = field(default_factory=dict)
    track: str = "pyrolysis"
    allow_fallback_vapor: bool = False
    force_builtin_vapor_pressure: bool = False
    feedstocks_path: Optional[Path] = None
    setpoints_path: Optional[Path] = None
    vapor_pressures_path: Optional[Path] = None
    # Overrides for the run_metadata block -- accepted so fixture-driven
    # tests pin started_at_utc + kernel_commit_sha to deterministic
    # values.  Production CLI invocations leave both empty and pick up
    # the live values.
    run_metadata_overrides: dict[str, Any] = field(default_factory=dict)
    # Pre-built simulator handle for the web-stream path: when set,
    # ``iter_hours`` reuses the existing simulator without rebuilding
    # it.  CLI / golden-file paths always rebuild via ``_build_sim``.
    simulator: Optional[PyrolysisSimulator] = None

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

        sim = self._build_sim()
        per_hour: list[dict] = []
        operator_decisions: list[dict] = []
        status = "ok"
        error_message = ""

        try:
            for frame in self._step_loop(sim, operator_decisions):
                per_hour.append(frame["per_hour_summary"])
            # Status semantics:
            #   * "ok"      -- the run consumed its full hour budget and
            #                  the simulator is either mid-batch or
            #                  exactly at the campaign endpoint.
            #   * "partial" -- the simulator finished mid-batch (either
            #                  the campaign closed early or operator
            #                  decisions consumed iteration slots
            #                  without advancing the hour counter).
            #   * "failed"  -- set in the except blocks below.
            if sim.melt.hour < self.hours:
                status = "partial"
        except BACKEND_FALLBACK_EXCEPTIONS as exc:
            status = "failed"
            error_message = f"backend failure: {exc}"
        except Exception as exc:  # noqa: BLE001 -- envelope the error
            status = "failed"
            error_message = f"{type(exc).__name__}: {exc}"

        shadow_trace = self._collect_shadow_trace(sim, operator_decisions)
        return self._build_output(
            sim=sim,
            per_hour=per_hour,
            shadow_trace=shadow_trace,
            status=status,
            error_message=error_message,
        )

    def iter_hours(
        self,
        sim: Optional[PyrolysisSimulator] = None,
    ) -> Iterator[dict]:
        """Yield ``{snapshot, per_hour_summary}`` once per simulated hour.

        Used by the SocketIO live stream so the web UI sees the same
        per-hour summary that the CLI commits to its output document.
        When ``sim`` is provided the caller is the web stream and owns
        the simulator's lifecycle (so decisions, pause, parameter
        adjustments flow through the SocketIO handlers).  When ``sim``
        is ``None`` we own the simulator (CLI path).

        Each yielded dict carries:

        * ``snapshot`` -- the raw :class:`HourSnapshot` (web stream
          uses this to build its tick payload).
        * ``per_hour_summary`` -- the runner-format per-hour entry.
        * ``operator_decision`` (optional) -- present on the frame that
          auto-applied a decision, so the web stream can emit a
          ``decision_required`` event after the tick (preserving its
          existing semantics).
        """

        sim = sim or self._build_sim()
        operator_decisions: list[dict] = []
        for frame in self._step_loop(sim, operator_decisions):
            yield frame

    # ------------------------------------------------------------------
    # Step orchestration
    # ------------------------------------------------------------------

    def _step_loop(
        self,
        sim: PyrolysisSimulator,
        operator_decisions: list[dict],
    ) -> Iterator[dict]:
        """Drive ``sim.step()`` for up to ``self.hours`` hours.

        Auto-applies any pending decision using
        ``DecisionPoint.recommendation``; each application appends an
        ``operator_decision`` record to ``operator_decisions`` so the
        runner can emit it via shadow_trace.
        """

        for _ in range(self.hours):
            if sim.is_complete():
                return
            if sim.paused_for_decision and sim.pending_decision is not None:
                decision = sim.pending_decision
                choice = decision.recommendation or (
                    decision.options[0] if decision.options else ""
                )
                event = {
                    "event": "operator_decision",
                    "hour": sim.melt.hour,
                    "decision_type": decision.decision_type.name,
                    "choice": choice,
                    "recommendation": decision.recommendation,
                    "options": list(decision.options),
                    "context": decision.context,
                }
                operator_decisions.append(event)
                sim.apply_decision(decision.decision_type, choice)
                if sim.is_complete():
                    return
            snapshot = sim.step()
            yield {
                "snapshot": snapshot,
                "per_hour_summary": build_per_hour_summary(sim, snapshot),
            }

    # ------------------------------------------------------------------
    # Simulator construction
    # ------------------------------------------------------------------

    def _build_sim(self) -> PyrolysisSimulator:
        if self.simulator is not None:
            return self.simulator

        feedstocks = self._load_feedstocks()
        setpoints = self._load_setpoints()
        if self.allow_fallback_vapor or self.force_builtin_vapor_pressure:
            setpoints = dict(setpoints)
            kernel_config = dict(setpoints.get("chemistry_kernel", {}) or {})
            kernel_config["allow_fallback_vapor"] = True
            setpoints["chemistry_kernel"] = kernel_config
        vapor_pressures = self._load_vapor_pressures()
        if self.feedstock_id not in feedstocks:
            raise RunnerError(
                f"unknown feedstock {self.feedstock_id!r}; expected one of "
                f"{sorted(feedstocks)[:5]}..."
            )

        backend = self._build_backend()
        sim = build_simulator(
            SimulatorBuildConfig(
                backend=backend,
                setpoints=setpoints,
                feedstocks=feedstocks,
                vapor_pressures=vapor_pressures,
            )
        )
        try:
            sim.load_batch(
                self.feedstock_id,
                self.mass_kg,
                additives_kg=dict(self.additives_kg),
            )
        except ValueError as exc:
            raise RunnerError(f"load_batch failed: {exc}") from exc

        if self.force_builtin_vapor_pressure:
            _force_builtin_vapor_pressure(sim)

        campaign_name = SIO_YIELD_CAMPAIGN_ALIASES.get(
            self.campaign, self.campaign)
        try:
            campaign_phase = CampaignPhase[campaign_name]
        except KeyError as exc:
            valid = ", ".join(member.name for member in CampaignPhase)
            raise RunnerError(
                f"unknown campaign {self.campaign!r}; valid options: {valid}"
            ) from exc

        if self.track == "mre_baseline":
            sim.record.track = "mre_baseline"
        sim.start_campaign(campaign_phase)

        # Apply setpoints overrides through the campaign manager's
        # public override map.  Numeric coercion keeps the runner
        # tolerant of YAML-loaded values that arrive as strings.
        for camp, overrides in self.setpoints_overrides.items():
            if not isinstance(overrides, Mapping):
                raise RunnerError(
                    f"setpoints_overrides[{camp!r}] must be a mapping"
                )
            target = sim.campaign_mgr.overrides.setdefault(str(camp), {})
            for field_name, value in overrides.items():
                target[str(field_name)] = float(value)
        return sim

    def _build_backend(self):
        return resolve_backend(
            self.backend_name,
            BackendSelectionPolicy.RUNNER_STRICT,
            unavailable_error_cls=RunnerError,
        )

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

    def _collect_shadow_trace(
        self,
        sim: PyrolysisSimulator,
        operator_decisions: list[dict],
    ) -> list[dict]:
        events: list[dict] = list(operator_decisions)
        kernel = getattr(sim, "_chem_kernel", None)
        if kernel is None:
            return events
        # ``ChemistryKernel.planner`` is the public property; fall back
        # to the private slot as a forward-compatible safety net in
        # case a future refactor renames the property without breaking
        # the underlying attribute (e.g. dataclass conversion).  The
        # runner is not allowed to assume kernel internals, so neither
        # attribute is a hard requirement.
        planner = getattr(kernel, "planner", None) or getattr(
            kernel, "_planner", None
        )
        if planner is None:
            return events
        shadow_trace = getattr(planner, "shadow_trace", None)
        if shadow_trace is None:
            return events
        try:
            kernel_events = list(shadow_trace)
        except TypeError:
            kernel_events = []
        # Only surface ``parity_warning`` entries -- the bulk shadow
        # dispatch records are noise for the operator-facing JSON.
        for record in kernel_events:
            if not isinstance(record, Mapping):
                continue
            event_type = record.get("event")
            if event_type in ("parity_warning", "parity_error"):
                events.append(_json_safe(dict(record)))
        return events

    def _build_output(
        self,
        *,
        sim: PyrolysisSimulator,
        per_hour: list[dict],
        shadow_trace: list[dict],
        status: str,
        error_message: str,
    ) -> dict:
        metadata_overrides = dict(self.run_metadata_overrides)
        started_at_utc = metadata_overrides.pop(
            "started_at_utc",
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        kernel_commit_sha = metadata_overrides.pop(
            "kernel_commit_sha", _resolve_kernel_commit_sha()
        )

        engines_used = self._engines_used(sim)

        run_metadata = {
            "schema_version": RUNNER_SCHEMA_VERSION,
            "feedstock_id": self.feedstock_id,
            "campaign": self.campaign,
            "hours_requested": int(self.hours),
            "hours_completed": int(sim.melt.hour),
            "mass_kg": float(self.mass_kg),
            "additives_kg": {k: float(v) for k, v in sorted(self.additives_kg.items())},
            "track": self.track,
            "backend": self.backend_name,
            "started_at_utc": started_at_utc,
            "engines_used": engines_used,
            "kernel_commit_sha": kernel_commit_sha,
        }
        # Anything left in metadata_overrides is propagated verbatim --
        # callers can stuff extra provenance (CI run id, etc.) without
        # the runner needing to know about it.
        for key, value in metadata_overrides.items():
            run_metadata[str(key)] = value

        final_state = _final_state_from_ledger(sim)

        return {
            "schema_version": RUNNER_SCHEMA_VERSION,
            "run_metadata": run_metadata,
            "final_state": final_state,
            "per_hour_summary": per_hour,
            "shadow_trace": shadow_trace,
            "status": status,
            "error_message": error_message,
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
    """

    p_total_bar = float(snapshot.overhead.pressure_mbar) * _MBAR_TO_BAR
    pO2_bar = float(sim.melt.pO2_mbar) * _MBAR_TO_BAR

    products = sim.product_ledger()
    metal_yields = {
        species: float(products.get(species, 0.0))
        for species in _METAL_PRODUCT_SPECIES
        if abs(products.get(species, 0.0)) > 1e-12
    }

    return {
        "hour": int(snapshot.hour),
        "campaign": snapshot.campaign.name,
        "T_C": float(snapshot.temperature_C),
        "P_total_bar": p_total_bar,
        "pO2_bar": pO2_bar,
        "mass_balance_pct": float(snapshot.mass_balance_error_pct),
        "O2_yield_kg_cumulative": float(snapshot.oxygen_produced_kg),
        "metal_yields_kg": metal_yields,
        "condensation_train_kg": {
            species: float(kg)
            for species, kg in sorted(snapshot.condensation_totals.items())
            if abs(kg) > 1e-12
        },
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
    for species in SIO_WALL_DEPOSIT_SPECIES:
        wall_deposit_kg.setdefault(species, 0.0)
    return {
        species: wall_deposit_kg[species]
        for species in sorted(wall_deposit_kg)
    }


def _wall_liner_resinter_config() -> dict[str, Any]:
    materials = _load_yaml(DATA_DIR / "materials.yaml")
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
        sim.melt.fO2_log = sim._compute_intrinsic_melt_fO2()

    if liner_temperature_c is None:
        return
    overhead_cfg = dict(runtime_override.get("overhead_headspace", {}) or {})
    overhead_cfg["liner_temperature_C"] = float(liner_temperature_c)
    runtime_override["overhead_headspace"] = overhead_cfg
    sim._configure_overhead_headspace(CampaignPhase.C2A)
    if sim._condensation_model is not None:
        sim.condensation_model.configure_operating_conditions(
            wall_temperature_C=float(liner_temperature_c),
            pipe_diameter_m=sim.overhead_model.pipe_diameter_m,
            gas_temperature_C=float(liner_temperature_c),
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

    from simulator.evaporation import _load_evaporation_alpha_by_species

    feedstocks = _load_yaml(DATA_DIR / "feedstocks.yaml")
    feedstock = feedstocks.get(feedstock_id, {})
    alpha_by_species = _load_evaporation_alpha_by_species(
        _load_yaml(DATA_DIR / "vapor_pressures.yaml")
    )
    try:
        sio_alpha_value = alpha_by_species["SiO"]
    except KeyError as exc:
        raise RunnerError(
            "SiO evaporation alpha missing from data/vapor_pressures.yaml"
        ) from exc
    stage0_carbon_kg = _required_stage0_carbon_kg(feedstock, float(mass_kg))
    additives_kg = {}
    if stage0_carbon_kg > 1.0e-12:
        additives_kg["C"] = stage0_carbon_kg

    setpoints_overrides: dict[str, dict] = {}
    if ramp_c_per_hr is not None:
        setpoints_overrides["C2A"] = {"ramp_rate": float(ramp_c_per_hr)}

    base_run = PyrolysisRun(
        feedstock_id=feedstock_id,
        campaign=campaign,
        hours=int(hours),
        mass_kg=float(mass_kg),
        backend_name="stub",
        track="pyrolysis",
        additives_kg=additives_kg,
        engines={"vapor_pressure": "builtin-antoine"},
        allow_fallback_vapor=True,
        force_builtin_vapor_pressure=True,
        setpoints_overrides=setpoints_overrides,
    )
    sim = base_run._build_sim()
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

    run = dataclasses.replace(base_run, simulator=sim)
    result = run.run()
    if result.get("status") == "failed":
        raise RunnerError(str(result.get("error_message", "SiO run failed")))

    final_state = result.get("final_state", {})
    cleaned_melt = final_state.get("process.cleaned_melt", {})
    condensation_train = final_state.get("process.condensation_train", {})
    wall_deposit = final_state.get(WALL_DEPOSIT_ACCOUNT, {})
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
    sio_yield_pct = sio_evolved_kg / float(mass_kg) * 100.0

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
            "mass_balance_error_pct": float(
                (result.get("per_hour_summary") or [{}])[-1].get(
                    "mass_balance_pct", 0.0
                )
            ),
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
            abs(float(diagnostics.get("mass_balance_error_pct", 0.0)))
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
            abs(float(diagnostics.get("mass_balance_error_pct", 0.0)))
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
# JSON helpers
# ----------------------------------------------------------------------


def _json_safe(value: Any) -> Any:
    """Recursively convert ``value`` into a JSON-serialisable form."""

    if isinstance(value, Mapping):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, bool)) or value is None:
        return value
    if isinstance(value, (int, float)):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return str(value)
        return value
    if dataclasses.is_dataclass(value):
        return _json_safe(dataclasses.asdict(value))
    # Enums, IntentResult-like wrappers -- fall back to repr so the
    # shadow_trace stays informative without leaking object identity.
    return repr(value)


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
                        choices=("stub", "alphamelts", "factsage"),
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
        track=args.track,
        run_metadata_overrides=metadata_overrides,
    )

    try:
        result = run.run()
    except RunnerError as exc:
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
            "per_hour_summary": [],
            "shadow_trace": [],
            "status": "failed",
            "error_message": f"RunnerError: {exc}",
        }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w") as f:
        json.dump(result, f, indent=2, sort_keys=False)
        f.write("\n")

    return 0 if result["status"] != "failed" else 1


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
