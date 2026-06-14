"""Early-melt Stage-0 test harness (chunk H1).

Drives :meth:`simulator.session.SimSession.advance` through C0+C0B and stops at
the structural C0B→C2A handoff without entering the extraction tail. Captures
the cleaned-melt projection and a per-hour foulant-disposition timeline grouped
by ``{trapped_gasses, refractory_carbon, other_mineral_contaminant}``.

Verdict computation (noise floor + MELTS domain) is intentionally deferred to
H2/H3 — see :attr:`Stage0HarnessResult.verdicts`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import yaml

from engines.builtin.foulant_disposition import FoulantRegistry, load_foulant_registry
from engines.builtin.stage0_pretreatment import (
    REACTION_FAMILY_CARBONATE_DECOMPOSITION,
    REACTION_FAMILY_INERT_TO_RUMP,
    REACTION_FAMILY_PARTITION_CARBON,
    REACTION_FAMILY_SILICATE_DISPLACEMENT,
    REACTION_FAMILY_SULFATE_DECOMP,
    REACTION_FAMILY_VOLATILIZATION,
)
from simulator.core import (
    STAGE0_FOULANT_PHASE1_TEMP_C,
    STAGE0_FOULANT_PHASE2_TEMP_C,
    PyrolysisSimulator,
)
from simulator.session import SimSession, SimSessionConfig, StepResult
from simulator.state import CampaignPhase, DecisionType

STAGE0_CAMPAIGNS = frozenset({CampaignPhase.C0, CampaignPhase.C0B})

FOULANT_GROUPS = (
    "trapped_gasses",
    "refractory_carbon",
    "other_mineral_contaminant",
)

STAGE0_PHASE_BY_CAMPAIGN = {
    CampaignPhase.C0: "phase_2_vacuum",
    CampaignPhase.C0B: "phase_1_oxidizing",
}

STAGE0_PHASE_RATIFIED_CEILING_C = {
    "phase_1_oxidizing": STAGE0_FOULANT_PHASE1_TEMP_C,
    "phase_2_vacuum": STAGE0_FOULANT_PHASE2_TEMP_C,
}

_DEFAULT_MARGIN_HOURS = 8.0
_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_FOULANT_THERMO = _REPO_ROOT / "data" / "foulant_thermo.yaml"


class Stage0HarnessError(RuntimeError):
    """Fail-loud harness guard (e.g. ``stage0_did_not_converge``)."""

    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class HourlyDispositionEntry:
    hour: int
    campaign: str
    stage0_phase: str | None
    ratified_ceiling_C: float | None
    temperature_C: float
    by_group: dict[str, list[dict[str, Any]]]


@dataclass
class Stage0HarnessResult:
    early_melt_reached: bool
    stop_reason: str
    total_hours: int
    cleaned_melt_kg: dict[str, float]
    disposition_timeline: list[HourlyDispositionEntry] = field(default_factory=list)
    verdicts: None = None


def default_max_stage0_hours(setpoints: Mapping[str, Any]) -> float:
    """Sum configured C0 + C0B hold ceilings plus a small margin."""

    campaigns = setpoints.get("campaigns", {}) or {}
    total = 0.0
    for key in ("C0", "C0b_p_cleanup"):
        cfg = campaigns.get(key, {}) or {}
        total += float(cfg.get("max_hold_hr", 0.0) or 0.0)
    return total + _DEFAULT_MARGIN_HOURS


def _empty_by_group() -> dict[str, list[dict[str, Any]]]:
    return {group: [] for group in FOULANT_GROUPS}


def _resolve_group(
    carrier: str,
    reaction_family: str,
    registry: FoulantRegistry,
) -> str:
    key = registry.alias_to_carrier.get(carrier) or registry.alias_to_carrier.get(
        carrier.lower()
    )
    if key is not None:
        entry = registry.carriers.get(key)
        if entry is not None and entry.group in FOULANT_GROUPS:
            return entry.group
    if reaction_family == REACTION_FAMILY_PARTITION_CARBON:
        return "refractory_carbon"
    if reaction_family in {
        REACTION_FAMILY_VOLATILIZATION,
        REACTION_FAMILY_SULFATE_DECOMP,
        REACTION_FAMILY_SILICATE_DISPLACEMENT,
        REACTION_FAMILY_CARBONATE_DECOMPOSITION,
        REACTION_FAMILY_INERT_TO_RUMP,
    }:
        return "other_mineral_contaminant"
    return "other_mineral_contaminant"


def _diagnostic_events(
    diagnostic: Mapping[str, Any],
    registry: FoulantRegistry,
) -> list[dict[str, Any]]:
    family = str(diagnostic.get("reaction_family", ""))
    carrier = str(
        diagnostic.get("carrier")
        or diagnostic.get("species")
        or diagnostic.get("source_component")
        or ""
    )
    events: list[dict[str, Any]] = []

    if family == REACTION_FAMILY_VOLATILIZATION:
        feed_kg = float(diagnostic.get("feed_kg", 0.0) or 0.0)
        group = _resolve_group(carrier, family, registry)
        for split in diagnostic.get("phase_splits", ()) or ():
            phase_id = split.get("phase")
            phase_label = (
                "phase_1_oxidizing"
                if int(phase_id or 0) == 1
                else "phase_2_vacuum"
            )
            escaped = float(split.get("escaped_frac", 0.0) or 0.0)
            events.append({
                "carrier": carrier,
                "reaction_family": family,
                "group": group,
                "disposition": "escaped",
                "phase": phase_label,
                "T_C": float(split.get("T_C", 0.0) or 0.0),
                "escaped_frac": escaped,
                "escaped_kg": feed_kg * escaped,
                "source": "diagnostic",
            })
        return events

    if family == REACTION_FAMILY_SULFATE_DECOMP:
        group = _resolve_group(carrier, family, registry)
        feed_kg = float(diagnostic.get("feed_kg", 0.0) or 0.0)
        extent = float(diagnostic.get("extent", 0.0) or 0.0)
        phase_id = diagnostic.get("phase")
        phase_label = (
            "phase_1_oxidizing"
            if int(phase_id or 0) == 1
            else "phase_2_vacuum"
        )
        events.append({
            "carrier": carrier,
            "reaction_family": family,
            "group": group,
            "disposition": "decomposed",
            "phase": phase_label,
            "T_C": float(diagnostic.get("T_C", 0.0) or 0.0),
            "extent": extent,
            "decomposed_kg": feed_kg * extent,
            "source": "diagnostic",
        })
        return events

    if family == REACTION_FAMILY_PARTITION_CARBON:
        feed_kg = float(diagnostic.get("feed_kg", 0.0) or 0.0)
        labile_mol = diagnostic.get("labile_mol")
        refractory_mol = diagnostic.get("refractory_mol")
        if isinstance(labile_mol, (int, float)) and labile_mol > 0.0:
            events.append({
                "carrier": carrier,
                "reaction_family": family,
                "group": "trapped_gasses",
                "disposition": "burned",
                "labile_mol": float(labile_mol),
                "burned_kg": feed_kg * float(diagnostic.get("labile_extent", 0.0) or 0.0),
                "phase": "phase_1_oxidizing",
                "T_C": STAGE0_FOULANT_PHASE1_TEMP_C,
                "source": "diagnostic",
            })
        if isinstance(refractory_mol, (int, float)) and refractory_mol > 0.0:
            events.append({
                "carrier": carrier,
                "reaction_family": family,
                "group": "refractory_carbon",
                "disposition": "residual",
                "refractory_mol": float(refractory_mol),
                "residual_interval": diagnostic.get("refractory_interval"),
                "phase": "phase_1_oxidizing",
                "T_C": STAGE0_FOULANT_PHASE1_TEMP_C,
                "source": "diagnostic",
            })
        carbonate_mol = diagnostic.get("carbonate_mol")
        if isinstance(carbonate_mol, (int, float)) and carbonate_mol > 0.0:
            events.append({
                "carrier": carrier,
                "reaction_family": family,
                "group": "other_mineral_contaminant",
                "disposition": "carbonate_residual",
                "carbonate_mol": float(carbonate_mol),
                "source": "diagnostic",
            })
        return events

    if family in {
        REACTION_FAMILY_SILICATE_DISPLACEMENT,
        REACTION_FAMILY_CARBONATE_DECOMPOSITION,
        REACTION_FAMILY_INERT_TO_RUMP,
    }:
        group = _resolve_group(carrier, family, registry)
        feed_kg = float(diagnostic.get("feed_kg", 0.0) or 0.0)
        extent = float(diagnostic.get("extent", diagnostic.get("rump_frac", 0.0)) or 0.0)
        disposition = (
            "rump"
            if family == REACTION_FAMILY_INERT_TO_RUMP
            else "decomposed"
        )
        events.append({
            "carrier": carrier,
            "reaction_family": family,
            "group": group,
            "disposition": disposition,
            "extent": extent,
            "amount_kg": feed_kg * extent,
            "T_C": float(diagnostic.get("T_C", 0.0) or 0.0),
            "source": "diagnostic",
        })
        return events

    if carrier:
        group = _resolve_group(carrier, family, registry)
        events.append({
            "carrier": carrier,
            "reaction_family": family,
            "group": group,
            "disposition": "projected",
            "source": "diagnostic",
            "raw": dict(diagnostic),
        })
    return events


def _group_diagnostic_events(
    diagnostics: list[Mapping[str, Any]],
    registry: FoulantRegistry,
) -> dict[str, list[dict[str, Any]]]:
    grouped = _empty_by_group()
    for diagnostic in diagnostics:
        for event in _diagnostic_events(diagnostic, registry):
            grouped[event["group"]].append(event)
    return grouped


def _evaporation_events(
    species_kg_hr: Mapping[str, float],
    registry: FoulantRegistry,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for species, rate_kg_hr in sorted(species_kg_hr.items()):
        rate = float(rate_kg_hr or 0.0)
        if rate <= 1e-15:
            continue
        group = _resolve_group(species, "", registry)
        events.append({
            "carrier": species,
            "reaction_family": "evaporation",
            "group": group,
            "disposition": "evolved",
            "evolved_kg_hr": rate,
            "source": "hourly_evaporation",
        })
    return events


def _stage0_exit_reason(sim: PyrolysisSimulator) -> str | None:
    if (
        getattr(sim, "paused_for_decision", False)
        and sim.pending_decision is not None
        and sim.pending_decision.decision_type == DecisionType.PATH_AB
        and sim.melt.campaign == CampaignPhase.C0B
    ):
        return "c0b_path_ab_pause"
    if sim.melt.campaign not in STAGE0_CAMPAIGNS:
        return "campaign_left_stage0"
    return None


def _capture_cleaned_melt_kg(sim: PyrolysisSimulator) -> dict[str, float]:
    ledger_melt = sim.atom_ledger.kg_by_account("process.cleaned_melt")
    return {
        species: float(kg)
        for species, kg in ledger_melt.items()
        if float(kg) > 1e-15
    }


def _diagnostic_stage0_phase(diagnostic: Mapping[str, Any]) -> str | None:
    explicit = diagnostic.get("stage0_phase")
    if isinstance(explicit, str) and explicit:
        return explicit
    phase_id = diagnostic.get("phase")
    if phase_id is not None:
        return (
            "phase_1_oxidizing"
            if int(phase_id) == 1
            else "phase_2_vacuum"
        )
    family = str(diagnostic.get("reaction_family", ""))
    if family == REACTION_FAMILY_PARTITION_CARBON:
        return "phase_1_oxidizing"
    if family == REACTION_FAMILY_SILICATE_DISPLACEMENT:
        return "phase_2_vacuum"
    if family == REACTION_FAMILY_CARBONATE_DECOMPOSITION:
        return "phase_1_oxidizing"
    return None


def _pending_diagnostic_events_by_phase(
    diagnostics: list[Mapping[str, Any]],
    registry: FoulantRegistry,
) -> dict[str, list[dict[str, Any]]]:
    pending = {
        "phase_1_oxidizing": [],
        "phase_2_vacuum": [],
        "unphased": [],
    }
    for diagnostic in diagnostics:
        target_phase = _diagnostic_stage0_phase(diagnostic)
        for event in _diagnostic_events(diagnostic, registry):
            phase = event.get("phase") or target_phase
            if phase in pending:
                pending[phase].append(event)
            else:
                pending["unphased"].append(event)
    return pending


def _timeline_entry_from_step(
    *,
    hour: int,
    sim: PyrolysisSimulator,
    step: StepResult,
    registry: FoulantRegistry,
    step_diagnostic_events: list[dict[str, Any]] | None,
) -> HourlyDispositionEntry:
    campaign = sim.melt.campaign
    stage0_phase = STAGE0_PHASE_BY_CAMPAIGN.get(campaign)
    by_group = _empty_by_group()

    if step_diagnostic_events:
        for event in step_diagnostic_events:
            by_group[event["group"]].append(event)

    evap = getattr(step.snapshot, "evap_flux", None)
    species_kg_hr = getattr(evap, "species_kg_hr", {}) or {}
    for event in _evaporation_events(species_kg_hr, registry):
        by_group[event["group"]].append(event)

    return HourlyDispositionEntry(
        hour=hour,
        campaign=str(campaign.name),
        stage0_phase=stage0_phase,
        ratified_ceiling_C=(
            STAGE0_PHASE_RATIFIED_CEILING_C.get(stage0_phase)
            if stage0_phase is not None
            else None
        ),
        temperature_C=float(sim.melt.temperature_C),
        by_group=by_group,
    )


def run_stage0_harness(
    session: SimSession,
    *,
    max_stage0_hours: float | None = None,
    foulant_registry: FoulantRegistry | None = None,
) -> Stage0HarnessResult:
    """Drive ``session`` through Stage-0 and stop at the C0B→C2A boundary."""

    sim = session.simulator
    config = session._config
    if config is None:
        raise Stage0HarnessError(
            "session has no config — call start() first",
            reason="session_not_started",
        )

    if max_stage0_hours is None:
        max_stage0_hours = default_max_stage0_hours(config.setpoints)

    if foulant_registry is None:
        foulant_registry = load_foulant_registry(_DEFAULT_FOULANT_THERMO)

    pending_by_phase = _pending_diagnostic_events_by_phase(
        list(getattr(sim, "_stage0_foulant_diagnostics", []) or []),
        foulant_registry,
    )
    assigned_phase = {
        "phase_1_oxidizing": False,
        "phase_2_vacuum": False,
        "unphased": False,
    }
    diagnostic_cursor = len(getattr(sim, "_stage0_foulant_diagnostics", []) or [])
    timeline: list[HourlyDispositionEntry] = []
    hours_run = 0
    stop_reason = ""

    while True:
        if hours_run >= max_stage0_hours:
            raise Stage0HarnessError(
                f"Stage-0 did not reach the C0B→C2A boundary within "
                f"{max_stage0_hours:g} h",
                reason="stage0_did_not_converge",
            )

        step = session.advance()
        hours_run += 1

        diagnostics = list(getattr(sim, "_stage0_foulant_diagnostics", []) or [])
        if len(diagnostics) > diagnostic_cursor:
            fresh_pending = _pending_diagnostic_events_by_phase(
                diagnostics[diagnostic_cursor:],
                foulant_registry,
            )
            for phase_key, events in fresh_pending.items():
                pending_by_phase[phase_key].extend(events)
            diagnostic_cursor = len(diagnostics)

        campaign_phase = STAGE0_PHASE_BY_CAMPAIGN.get(sim.melt.campaign)
        step_events: list[dict[str, Any]] = []
        if campaign_phase and not assigned_phase[campaign_phase]:
            step_events.extend(pending_by_phase[campaign_phase])
            pending_by_phase[campaign_phase] = []
            assigned_phase[campaign_phase] = True
        if not assigned_phase["unphased"] and pending_by_phase["unphased"]:
            step_events.extend(pending_by_phase["unphased"])
            pending_by_phase["unphased"] = []
            assigned_phase["unphased"] = True

        timeline.append(
            _timeline_entry_from_step(
                hour=int(sim.melt.hour),
                sim=sim,
                step=step,
                registry=foulant_registry,
                step_diagnostic_events=step_events or None,
            )
        )

        stop_reason = _stage0_exit_reason(sim) or ""
        if stop_reason:
            break

    cleaned_melt_kg = _capture_cleaned_melt_kg(sim)
    return Stage0HarnessResult(
        early_melt_reached=True,
        stop_reason=stop_reason,
        total_hours=hours_run,
        cleaned_melt_kg=cleaned_melt_kg,
        disposition_timeline=timeline,
        verdicts=None,
    )


def run_stage0_harness_from_config(
    config: SimSessionConfig,
    *,
    max_stage0_hours: float | None = None,
    backend: Any | None = None,
) -> Stage0HarnessResult:
    session = SimSession().start(config, backend=backend)
    return run_stage0_harness(session, max_stage0_hours=max_stage0_hours)


def load_harness_yaml(name: str) -> dict[str, Any]:
    path = _REPO_ROOT / "data" / name
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}