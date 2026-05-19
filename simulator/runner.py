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
import dataclasses
import importlib.abc
import importlib.machinery
import json
import math
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Optional

import yaml

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
SIO_ALPHA_PROVENANCE = "placeholder; pending Phase 1 α surface"
SIO_INDUSTRIAL_BENCHMARK_PCT: tuple[int, int] = (8, 15)
SIO_YIELD_STAGE_KEYS: dict[int, str] = {
    1: "stage_1_fe_condenser_impurity",
    3: "stage_3_sio_zone_product",
    4: "stage_4_alkali_mg_carryover",
    5: "stage_5_dust_filter_carryover",
}

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
        sim = PyrolysisSimulator(backend, setpoints, feedstocks, vapor_pressures)
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
        from simulator.melt_backend.base import StubBackend

        if self.backend_name in ("", "stub"):
            backend = StubBackend()
            backend.initialize({})
            return backend
        if self.backend_name == "alphamelts":
            from simulator.melt_backend.alphamelts import AlphaMELTSBackend

            backend = AlphaMELTSBackend()
            if backend.initialize({}) and backend.is_available():
                return backend
            raise RunnerError(
                "AlphaMELTS unavailable; rerun with --backend=stub or "
                "install via install-dependencies.py"
            )
        if self.backend_name == "factsage":
            from simulator.melt_backend.factsage import FactSAGEBackend
            from simulator.melt_backend.factsage_config import (
                FactSAGEConfigError,
                load_factsage_config,
            )

            try:
                config = load_factsage_config()
            except FactSAGEConfigError as exc:
                raise RunnerError(
                    f"FactSAGE config error: {exc}; rerun with --backend=stub"
                ) from exc
            backend = FactSAGEBackend()
            if backend.initialize(config) and backend.is_available():
                return backend
            raise RunnerError(
                "FactSAGE unavailable; rerun with --backend=stub"
            )
        raise RunnerError(f"unknown backend {self.backend_name!r}")

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


def _required_stage0_carbon_kg(feedstock: Mapping[str, Any],
                               mass_kg: float) -> float:
    try:
        return float(
            PyrolysisSimulator._carbon_reductant_required_kg(
                feedstock, mass_kg)
        )
    except Exception as exc:  # noqa: BLE001 -- surface as runner config error
        raise RunnerError(f"invalid Stage 0 carbon cleanup metadata: {exc}") from exc


def _prepare_sio_campaign_start(sim: PyrolysisSimulator) -> None:
    campaign_cfg = (
        (sim.setpoints.get("campaigns", {}) or {}).get(SIO_YIELD_CAMPAIGN, {})
        or {}
    )
    temp_range = campaign_cfg.get("temp_range_C") or []
    if temp_range:
        sim.melt.temperature_C = max(sim.melt.temperature_C, float(temp_range[0]))


def build_sio_yield_report(
    *,
    feedstock_id: str,
    campaign: str = SIO_YIELD_CAMPAIGN,
    hours: int = 24,
    mass_kg: float = 1000.0,
    include_diagnostics: bool = False,
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

    from simulator.evaporation import _EVAPORATION_COEFFICIENT_ALPHA

    feedstocks = _load_yaml(DATA_DIR / "feedstocks.yaml")
    feedstock = feedstocks.get(feedstock_id, {})
    stage0_carbon_kg = _required_stage0_carbon_kg(feedstock, float(mass_kg))
    additives_kg = {}
    if stage0_carbon_kg > 1.0e-12:
        additives_kg["C"] = stage0_carbon_kg

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
    )
    sim = base_run._build_sim()
    _prepare_sio_campaign_start(sim)
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
    sio_escape_mol = float(terminal_offgas.get("SiO", 0.0))
    terminal_mol = si_terminal_mol + sio2_terminal_mol + sio_escape_mol
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

    report = {
        "feedstock_id": feedstock_id,
        "campaign": SIO_YIELD_CAMPAIGN,
        "alpha_SiO": _clean_report_float(_EVAPORATION_COEFFICIENT_ALPHA),
        "alpha_provenance": SIO_ALPHA_PROVENANCE,
        "sio_evolved_kg": _clean_report_float(sio_evolved_kg),
        "sio_to_silica_fume_kg": sio_to_silica_fume_kg,
        "sio_yield_pct_of_feedstock": _clean_report_float(sio_yield_pct),
        "industrial_benchmark_pct": list(SIO_INDUSTRIAL_BENCHMARK_PCT),
        "verdict": _industrial_sio_verdict(sio_yield_pct),
    }

    if include_diagnostics:
        diagnostics = {
            "sio_evaporated_mol": sio_evaporated_mol,
            "si_terminal_mol": si_terminal_mol,
            "sio2_terminal_mol": sio2_terminal_mol,
            "sio_escape_mol": sio_escape_mol,
            "closure_error_pct": closure_error_pct,
            "mass_balance_error_pct": float(
                (result.get("per_hour_summary") or [{}])[-1].get(
                    "mass_balance_pct", 0.0
                )
            ),
        }
        return report, diagnostics
    return report


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


class _SiOYieldModuleLoader(importlib.abc.Loader):
    def get_code(self, fullname: str):
        source = (
            "from simulator.runner import main_sio_yield\n"
            "raise SystemExit(main_sio_yield())\n"
        )
        return compile(source, "<simulator.runner.sio_yield>", "exec")

    def create_module(self, spec):  # noqa: D401
        return None

    def exec_module(self, module) -> None:
        return None


class _SiOYieldModuleFinder(importlib.abc.MetaPathFinder):
    _sio_yield_finder = True

    def find_spec(self, fullname: str, path=None, target=None):
        if fullname != "simulator.runner.sio_yield":
            return None
        return importlib.machinery.ModuleSpec(
            fullname,
            _SiOYieldModuleLoader(),
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
