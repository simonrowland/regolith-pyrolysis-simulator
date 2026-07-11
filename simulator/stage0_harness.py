"""Early-melt Stage-0 test harness (chunk H1).

Drives :meth:`simulator.session.SimSession.advance` through C0+C0B and stops at
the structural C0B→C2A handoff without entering the extraction tail. Captures
the cleaned-melt projection and a per-hour foulant-disposition timeline grouped
by ``{trapped_gasses, refractory_carbon, other_mineral_contaminant}``.

Verdict (a) property-impact flags include per-property clear-step records, and
verdict (b) stripped-silicate domain gate is computed at the C0B→C2A cut — see
:attr:`Stage0HarnessResult.verdicts`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import yaml

from engines.builtin.foulant_disposition import FoulantRegistry, load_foulant_registry
from engines.builtin.foulant_disposition import refractory_fraction_interval
from engines.builtin.melt_effect_adjustment import build_harness_verdicts
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
from simulator.accounting.formulas import ATOMIC_WEIGHTS_G_PER_MOL
from simulator.backend_names import ANALYTICAL_BACKEND_SERIALIZATION_TOKEN
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
_DEFAULT_CARBON_PARTITION = _REPO_ROOT / "data" / "stage0_carbon_partition.yaml"
_CARBON_KG_PER_MOL = float(ATOMIC_WEIGHTS_G_PER_MOL["C"]) / 1000.0


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
    verdicts: dict[str, Any] | None = None


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


def _positive_float(value: Any) -> float | None:
    if not isinstance(value, (int, float)):
        return None
    parsed = float(value)
    if parsed <= 0.0:
        return None
    return parsed


def _carbon_kg(carbon_mol: float) -> float:
    return float(carbon_mol) * _CARBON_KG_PER_MOL


def _carbon_carrier_equivalent_kg(
    split_mol: float,
    declared_c_mol: float | None,
    feed_kg: float,
) -> float | None:
    if declared_c_mol is None or declared_c_mol <= 0.0 or feed_kg <= 0.0:
        return None
    return feed_kg * float(split_mol) / declared_c_mol


def _carbon_partition_interval_event(
    *,
    carrier: str,
    diagnostic: Mapping[str, Any],
    feed_kg: float,
    declared_c_mol: float | None,
) -> dict[str, Any] | None:
    raw_not_speciated = diagnostic.get("not_speciated", ()) or ()
    if isinstance(raw_not_speciated, str):
        not_speciated = (raw_not_speciated,)
    else:
        not_speciated = tuple(str(item) for item in raw_not_speciated)
    if "f_refractory_organic_C" not in not_speciated:
        return None
    if feed_kg <= 0.0 or declared_c_mol is None:
        return None

    with _DEFAULT_CARBON_PARTITION.open(encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    matches: list[tuple[str, Mapping[str, Any], tuple[float, float]]] = []
    for key, row in (payload.get("phase_partitions", {}) or {}).items():
        if not isinstance(row, Mapping):
            continue
        if str(row.get("carrier") or "") != carrier:
            continue
        interval = refractory_fraction_interval(row)
        if interval is not None:
            matches.append((str(key), row, interval))
    if len(matches) != 1:
        return None

    partition_key, row, interval = matches[0]
    low, high = interval
    return {
        "carrier": carrier,
        "reaction_family": REACTION_FAMILY_PARTITION_CARBON,
        "group": "refractory_carbon",
        "disposition": "uncertain_partition",
        "interval_required": True,
        "feed_kg": feed_kg,
        "declared_c_mol": declared_c_mol,
        "declared_C_kg": _carbon_kg(declared_c_mol),
        "refractory_fraction_interval": [low, high],
        "refractory_C_mol_interval": [
            declared_c_mol * low,
            declared_c_mol * high,
        ],
        "refractory_C_kg_interval": [
            _carbon_kg(declared_c_mol * low),
            _carbon_kg(declared_c_mol * high),
        ],
        "partition_key": partition_key,
        "not_speciated": list(not_speciated),
        "confidence": row.get("confidence"),
        "phase": "phase_1_oxidizing",
        "T_C": STAGE0_FOULANT_PHASE1_TEMP_C,
        "source": "diagnostic",
    }


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
        declared_c_mol = _positive_float(diagnostic.get("declared_c_mol"))
        interval_event = _carbon_partition_interval_event(
            carrier=carrier,
            diagnostic=diagnostic,
            feed_kg=feed_kg,
            declared_c_mol=declared_c_mol,
        )
        if interval_event is not None:
            events.append(interval_event)
        labile_mol = _positive_float(diagnostic.get("labile_mol"))
        refractory_mol = _positive_float(diagnostic.get("refractory_mol"))
        if labile_mol is not None:
            labile_extent = float(diagnostic.get("labile_extent", 0.0) or 0.0)
            labile_c_kg = _carbon_kg(labile_mol)
            carrier_equivalent = _carbon_carrier_equivalent_kg(
                labile_mol,
                declared_c_mol,
                feed_kg,
            )
            events.append({
                "carrier": carrier,
                "reaction_family": family,
                "group": "trapped_gasses",
                "disposition": "burned",
                "feed_kg": feed_kg,
                "declared_c_mol": declared_c_mol,
                "labile_mol": labile_mol,
                "labile_C_kg": labile_c_kg,
                "burned_C_kg": labile_c_kg * labile_extent,
                "burned_kg": labile_c_kg * labile_extent,
                "labile_carrier_equivalent_kg": carrier_equivalent,
                "mass_basis": "declared_C",
                "phase": "phase_1_oxidizing",
                "T_C": STAGE0_FOULANT_PHASE1_TEMP_C,
                "source": "diagnostic",
            })
        if refractory_mol is not None:
            residual_mol = _positive_float(diagnostic.get("refractory_residual_mol"))
            if residual_mol is None:
                residual_mol = refractory_mol
            carrier_equivalent = _carbon_carrier_equivalent_kg(
                refractory_mol,
                declared_c_mol,
                feed_kg,
            )
            events.append({
                "carrier": carrier,
                "reaction_family": family,
                "group": "refractory_carbon",
                "disposition": "residual",
                "feed_kg": feed_kg,
                "declared_c_mol": declared_c_mol,
                "refractory_mol": refractory_mol,
                "refractory_C_kg": _carbon_kg(refractory_mol),
                "refractory_residual_mol": residual_mol,
                "refractory_residual_C_kg": _carbon_kg(residual_mol),
                "refractory_carrier_equivalent_kg": carrier_equivalent,
                "residual_interval": diagnostic.get("refractory_interval"),
                "mass_basis": "declared_C",
                "phase": "phase_1_oxidizing",
                "T_C": STAGE0_FOULANT_PHASE1_TEMP_C,
                "source": "diagnostic",
            })
        carbonate_mol = _positive_float(diagnostic.get("carbonate_mol"))
        if carbonate_mol is not None:
            events.append({
                "carrier": carrier,
                "reaction_family": family,
                "group": "other_mineral_contaminant",
                "disposition": "carbonate_residual",
                "feed_kg": feed_kg,
                "declared_c_mol": declared_c_mol,
                "carbonate_mol": carbonate_mol,
                "carbonate_C_kg": _carbon_kg(carbonate_mol),
                "mass_basis": "declared_C",
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
    config = session._config
    engine = str(
        getattr(
            config,
            "backend_name",
            ANALYTICAL_BACKEND_SERIALIZATION_TOKEN,
        )
        if config
        else ANALYTICAL_BACKEND_SERIALIZATION_TOKEN
    )
    T_in_C = float(sim.melt.temperature_C)
    verdicts = build_harness_verdicts(
        cleaned_melt_kg=cleaned_melt_kg,
        sim=sim,
        engine=engine,
        timeline=tuple(timeline),
        T_in_C=T_in_C,
    )
    return Stage0HarnessResult(
        early_melt_reached=True,
        stop_reason=stop_reason,
        total_hours=hours_run,
        cleaned_melt_kg=cleaned_melt_kg,
        disposition_timeline=timeline,
        verdicts=verdicts,
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
