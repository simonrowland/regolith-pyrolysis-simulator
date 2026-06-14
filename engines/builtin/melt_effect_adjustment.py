"""Residual-contaminant strip → adjust → warn layer (chunks H2/H3 / A2-adj).

Pure, stateless, no ledger writes. The adjustment annotates MELTS results with
provenance; it never silently retunes certified values.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from engines.alphamelts.domain import (
    AlphaMELTSDomainGate,
    _is_non_oxide_species_name,
)
from engines.magemin.domain import MAGEMinDomainGate
from simulator.state import OXIDE_SPECIES

_OXIDE_SET = frozenset(OXIDE_SPECIES)

# Ratified property-impact thresholds (CONTAMINANT-WARNING-DOC §★, PROPOSED).
LIQUIDUS_WARNING_FRAC_OF_T = 0.02
LIQUIDUS_NOTICE_FRAC_OF_T = 0.005
PHASE_WARNING_ABS_FRAC = 0.02
PHASE_NOTICE_ABS_FRAC = 0.005
# Redox Δlog₁₀ fO₂ equivalents — PROPOSED, sweep-grounded (S-E5-4).
REDOX_WARNING_DELTA_LOG = 0.20
REDOX_NOTICE_DELTA_LOG = 0.05

EFFECT_TABLE_VERSION = "2026-06-14-proposed-e5"

# Per-contaminant effect rows sourced from CONTAMINANT-WARNING-DOC + evidence-E5.
# Intervals are literature-imported, NOT simulator-measured.
EFFECT_ROWS: dict[str, dict[str, Any]] = {
    "cl_halide": {
        "contaminant_group": "Cl/NaCl/KCl",
        "species_aliases": ("Cl", "NaCl", "KCl", "CaCl2", "MgCl2"),
        "properties": {
            "liquidus": {
                "mode": "delta_T_per_wt_pct",
                "coefficient_C_per_wt_pct": -100.0,
                "grounded": True,
                "source": "Filiberto & Treiman 2009; LPSC 2011 #2064",
            },
        },
    },
    "fluoride": {
        "contaminant_group": "F/NaF",
        "species_aliases": ("F", "NaF", "KF", "CaF2", "MgF2"),
        "properties": {
            "liquidus": {
                "mode": "delta_T_interval_per_wt_pct",
                "interval_C_per_wt_pct": (-200.0, -50.0),
                "grounded": False,
                "source": "Filiberto et al. 2010 EOS; LPSC 2011 #2064",
            },
        },
    },
    "sulfide": {
        "contaminant_group": "S/FeS/CaS",
        "species_aliases": ("S", "S2", "FeS", "FeS2", "CaS", "MgS", "NiS"),
        "properties": {
            "phase": {
                "mode": "delta_fraction_interval_per_wt_pct",
                "interval_per_wt_pct": (0.05, 0.20),
                "grounded": False,
                "source": "Jugo et al. 2010 Nat. Geosci. 3:521-525 (SCSS)",
            },
        },
    },
    "sulfate_proxy": {
        "contaminant_group": "SO3/sulfate carrier",
        "species_aliases": ("SO3", "SO2"),
        "properties": {
            "phase": {
                "mode": "delta_fraction_interval_per_wt_pct",
                "interval_per_wt_pct": (0.02, 0.10),
                "grounded": False,
                "source": "Jugo SCSS; sulfate clearance routing",
            },
        },
    },
    "residual_carbon": {
        "contaminant_group": "residual C",
        "species_aliases": ("C", "graphite"),
        "properties": {
            "redox": {
                "mode": "delta_log10_fO2_interval_per_wt_pct",
                "interval_per_wt_pct": (0.10, 0.50),
                "grounded": False,
                "source": "Brooker et al. 2014; Sephton 2004",
            },
        },
    },
    "p2o5": {
        "contaminant_group": "P2O5",
        "species_aliases": ("P2O5",),
        "stripped": False,
        "properties": {
            "liquidus": {
                "mode": "delta_T_interval_per_wt_pct",
                "interval_C_per_wt_pct": (-15.0, -5.0),
                "grounded": False,
                "source": "Watson 1979; Harrison 1981",
            },
        },
    },
}


class CertifiedPointRefusedError(ValueError):
    """Raised when a caller requests a certified point on an ungrounded row."""


@dataclass(frozen=True)
class StrippedMassProvenance:
    species: str
    kg: float
    wt_pct_of_total: float
    reason: str


@dataclass(frozen=True)
class StripResult:
    oxide_kg: dict[str, float]
    stripped_kg: dict[str, float]
    total_kg: float
    oxide_wt_pct: dict[str, float]
    provenance: tuple[StrippedMassProvenance, ...]
    stripped_mass_kg: float


@dataclass(frozen=True)
class PropertyPerturbation:
    property: str
    contaminant: str
    effect_row: str
    source: str
    residual_wt_pct: float
    perturbation_before: float
    perturbation_after: float
    metric: str
    grounded: bool
    raw_value: float | None = None
    adjusted_value: float | None = None
    interval: tuple[float, float] | None = None


@dataclass(frozen=True)
class PropertyFlag:
    property: str
    level: str
    contaminant: str
    effect_row: str
    perturbation_before: float
    perturbation_after: float
    metric: str
    grounded: bool
    hour: int
    active: bool = True
    cleared: bool = False
    noise_floor_status: str = "proposed"


@dataclass(frozen=True)
class MeltEffectAdjustmentResult:
    effect_table_version: str
    T_in_C: float
    engine: str
    perturbations: tuple[PropertyPerturbation, ...]
    raw_liquidus_C: float | None
    adjusted_liquidus_C: float | None
    adjusted_liquidus_provenance: tuple[dict[str, Any], ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class VerdictAResult:
    flags: tuple[PropertyFlag, ...]
    step_resolved: tuple[dict[str, Any], ...]
    warn_only: bool = True


@dataclass(frozen=True)
class VerdictBResult:
    backend_status: str
    stripped_domain_valid: bool
    hard_gate_failed: bool
    stripped_oxide_wt_pct: dict[str, float]
    stripped_mass_provenance: tuple[StrippedMassProvenance, ...]
    domain_warnings: tuple[str, ...]
    engine: str
    contaminant_present_never_crash: bool = True


def is_strippable_non_oxide_residual(species: str) -> bool:
    """True for Cl/F/S/elemental-C residuals; P2O5 and valid oxides stay."""
    key = str(species).strip()
    if key in _OXIDE_SET:
        return False
    if key in {"C", "graphite"}:
        return True
    return _is_non_oxide_species_name(key)


def _species_effect_row(species: str) -> str | None:
    for row_key, row in EFFECT_ROWS.items():
        if row.get("stripped") is False:
            continue
        aliases = row.get("species_aliases", ())
        if species in aliases or species.lower() in {a.lower() for a in aliases}:
            return row_key
    if _is_non_oxide_residual_name(species):
        return "cl_halide" if "Cl" in species or species in {"NaCl", "KCl"} else None
    return None


def _is_non_oxide_residual_name(species: str) -> bool:
    return is_strippable_non_oxide_residual(species)


def strip_non_oxide_residuals(
    cleaned_melt_kg: Mapping[str, float],
) -> StripResult:
    """Strip non-oxide residuals; record provenance; do NOT renormalize oxides."""
    oxide_kg: dict[str, float] = {}
    stripped_kg: dict[str, float] = {}
    total_kg = 0.0

    for species, kg_raw in cleaned_melt_kg.items():
        kg = float(kg_raw or 0.0)
        if kg <= 1e-15:
            continue
        total_kg += kg
        if is_strippable_non_oxide_residual(species):
            stripped_kg[species] = stripped_kg.get(species, 0.0) + kg
        else:
            oxide_kg[species] = oxide_kg.get(species, 0.0) + kg

    provenance: list[StrippedMassProvenance] = []
    stripped_mass = 0.0
    for species, kg in sorted(stripped_kg.items()):
        stripped_mass += kg
        wt = (kg / total_kg * 100.0) if total_kg > 0.0 else 0.0
        provenance.append(
            StrippedMassProvenance(
                species=species,
                kg=kg,
                wt_pct_of_total=wt,
                reason="non_oxide_residual_stripped_before_engine",
            )
        )

    oxide_wt_pct: dict[str, float] = {}
    if total_kg > 0.0:
        for species, kg in sorted(oxide_kg.items()):
            oxide_wt_pct[species] = (kg / total_kg) * 100.0

    return StripResult(
        oxide_kg=dict(oxide_kg),
        stripped_kg=dict(stripped_kg),
        total_kg=total_kg,
        oxide_wt_pct=oxide_wt_pct,
        provenance=tuple(provenance),
        stripped_mass_kg=stripped_mass,
    )


def residual_wt_pct_by_species(
    cleaned_melt_kg: Mapping[str, float],
) -> dict[str, float]:
    total = sum(float(v or 0.0) for v in cleaned_melt_kg.values())
    if total <= 0.0:
        return {}
    out: dict[str, float] = {}
    for species, kg in cleaned_melt_kg.items():
        mass = float(kg or 0.0)
        if mass <= 1e-15:
            continue
        if is_strippable_non_oxide_residual(species) or species == "P2O5":
            out[species] = (mass / total) * 100.0
    return out


def _match_effect_row(species: str) -> tuple[str, dict[str, Any]] | None:
    for row_key, row in EFFECT_ROWS.items():
        aliases = row.get("species_aliases", ())
        if species in aliases:
            return row_key, row
        lowered = {a.lower() for a in aliases}
        if species.lower() in lowered:
            return row_key, row
    if species in {"Cl", "NaCl", "KCl"}:
        return "cl_halide", EFFECT_ROWS["cl_halide"]
    if species in {"C", "graphite"}:
        return "residual_carbon", EFFECT_ROWS["residual_carbon"]
    if "F" in re.findall(r"[A-Z][a-z]?", species) and species not in _OXIDE_SET:
        return "fluoride", EFFECT_ROWS["fluoride"]
    if species in {"S", "S2", "FeS", "FeS2", "CaS"}:
        return "sulfide", EFFECT_ROWS["sulfide"]
    if species in {"SO3", "SO2"}:
        return "sulfate_proxy", EFFECT_ROWS["sulfate_proxy"]
    return None


def _liquidus_perturbation_pct(delta_T_C: float, T_in_C: float) -> float:
    if T_in_C <= 0.0:
        return abs(delta_T_C)
    return abs(delta_T_C) / T_in_C * 100.0


def _compute_property_perturbation(
    *,
    property_name: str,
    species: str,
    wt_pct: float,
    prop_cfg: Mapping[str, Any],
    row_key: str,
    contaminant_group: str,
    T_in_C: float,
) -> PropertyPerturbation:
    mode = str(prop_cfg.get("mode", ""))
    grounded = bool(prop_cfg.get("grounded", False))
    source = str(prop_cfg.get("source", ""))

    if mode == "delta_T_per_wt_pct":
        coeff = float(prop_cfg["coefficient_C_per_wt_pct"])
        delta_T = coeff * wt_pct
        before = _liquidus_perturbation_pct(delta_T, T_in_C)
        after = 0.0
        return PropertyPerturbation(
            property=property_name,
            contaminant=species,
            effect_row=row_key,
            source=source,
            residual_wt_pct=wt_pct,
            perturbation_before=before,
            perturbation_after=after,
            metric="delta_T_frac_of_T_in_C",
            grounded=grounded,
            raw_value=delta_T,
            adjusted_value=0.0,
        )

    if mode == "delta_T_interval_per_wt_pct":
        low_c, high_c = prop_cfg["interval_C_per_wt_pct"]
        delta_low = float(low_c) * wt_pct
        delta_high = float(high_c) * wt_pct
        before = max(
            _liquidus_perturbation_pct(delta_low, T_in_C),
            _liquidus_perturbation_pct(delta_high, T_in_C),
        )
        width = abs(
            _liquidus_perturbation_pct(delta_high, T_in_C)
            - _liquidus_perturbation_pct(delta_low, T_in_C)
        )
        after = width
        midpoint = (delta_low + delta_high) / 2.0
        return PropertyPerturbation(
            property=property_name,
            contaminant=species,
            effect_row=row_key,
            source=source,
            residual_wt_pct=wt_pct,
            perturbation_before=before,
            perturbation_after=after,
            metric="delta_T_frac_of_T_in_C",
            grounded=False,
            raw_value=midpoint,
            adjusted_value=None,
            interval=(delta_low, delta_high),
        )

    if mode == "delta_fraction_interval_per_wt_pct":
        low_f, high_f = prop_cfg["interval_per_wt_pct"]
        before = max(abs(float(low_f) * wt_pct), abs(float(high_f) * wt_pct))
        after = abs(float(high_f) - float(low_f)) * wt_pct
        return PropertyPerturbation(
            property=property_name,
            contaminant=species,
            effect_row=row_key,
            source=source,
            residual_wt_pct=wt_pct,
            perturbation_before=before,
            perturbation_after=after,
            metric="delta_absolute_fraction",
            grounded=False,
            interval=(float(low_f) * wt_pct, float(high_f) * wt_pct),
        )

    if mode == "delta_log10_fO2_interval_per_wt_pct":
        low_l, high_l = prop_cfg["interval_per_wt_pct"]
        before = max(abs(float(low_l) * wt_pct), abs(float(high_l) * wt_pct))
        after = abs(float(high_l) - float(low_l)) * wt_pct
        return PropertyPerturbation(
            property=property_name,
            contaminant=species,
            effect_row=row_key,
            source=source,
            residual_wt_pct=wt_pct,
            perturbation_before=before,
            perturbation_after=after,
            metric="delta_log10_fO2",
            grounded=False,
            interval=(float(low_l) * wt_pct, float(high_l) * wt_pct),
        )

    raise ValueError(f"unsupported effect mode {mode!r} for {property_name}")


def request_certified_point(
    row_key: str,
    property_name: str,
    *,
    wt_pct: float = 1.0,
) -> float:
    """Fail loud when an ungrounded effect row has no certified point."""
    row = EFFECT_ROWS[row_key]
    prop_cfg = row["properties"][property_name]
    if not prop_cfg.get("grounded", False):
        raise CertifiedPointRefusedError(
            f"certified-point refused for ungrounded effect "
            f"{row_key}.{property_name} (interval only; wt%={wt_pct})"
        )
    mode = prop_cfg["mode"]
    if mode == "delta_T_per_wt_pct":
        return float(prop_cfg["coefficient_C_per_wt_pct"]) * wt_pct
    raise CertifiedPointRefusedError(
        f"no certified-point path for {row_key}.{property_name} mode={mode!r}"
    )


def melt_effect_adjustment(
    residual_by_species_wt_pct: Mapping[str, float],
    melts_result: Mapping[str, Any] | None,
    engine: str,
    *,
    T_in_C: float,
) -> MeltEffectAdjustmentResult:
    """Per-residual analytical correction with separate raw vs adjusted fields."""
    perturbations: list[PropertyPerturbation] = []
    liquidus_delta = 0.0
    liquidus_prov: list[dict[str, Any]] = []
    warnings: list[str] = []

    raw_liquidus = None
    if melts_result is not None:
        raw_liquidus = melts_result.get("liquidus_T_C")
        if raw_liquidus is not None:
            raw_liquidus = float(raw_liquidus)

    for species, wt_pct in sorted(residual_by_species_wt_pct.items()):
        if wt_pct <= 1e-12:
            continue
        matched = _match_effect_row(species)
        if matched is None:
            warnings.append(
                f"noise_floor_ungrounded: no effect row for residual {species} "
                f"at {wt_pct:.4g} wt%"
            )
            continue
        row_key, row = matched
        for prop_name, prop_cfg in row.get("properties", {}).items():
            pert = _compute_property_perturbation(
                property_name=prop_name,
                species=species,
                wt_pct=float(wt_pct),
                prop_cfg=prop_cfg,
                row_key=row_key,
                contaminant_group=str(row.get("contaminant_group", "")),
                T_in_C=T_in_C,
            )
            perturbations.append(pert)
            if prop_name == "liquidus" and pert.raw_value is not None:
                liquidus_delta += float(pert.raw_value)
                liquidus_prov.append({
                    "contaminant": species,
                    "effect_row": row_key,
                    "source": pert.source,
                    "delta_T_C": pert.raw_value,
                    "grounded": pert.grounded,
                })
            if not pert.grounded:
                warnings.append(
                    f"noise_floor_ungrounded: {species} {prop_name} effect "
                    f"interval width drives flag (row={row_key})"
                )

    adjusted_liquidus = None
    if raw_liquidus is not None:
        adjusted_liquidus = raw_liquidus + liquidus_delta

    return MeltEffectAdjustmentResult(
        effect_table_version=EFFECT_TABLE_VERSION,
        T_in_C=float(T_in_C),
        engine=str(engine),
        perturbations=tuple(perturbations),
        raw_liquidus_C=raw_liquidus,
        adjusted_liquidus_C=adjusted_liquidus,
        adjusted_liquidus_provenance=tuple(liquidus_prov),
        warnings=tuple(warnings),
    )


def _property_thresholds(property_name: str, metric: str) -> tuple[float, float]:
    if property_name == "liquidus" or metric == "delta_T_frac_of_T_in_C":
        return LIQUIDUS_WARNING_FRAC_OF_T * 100.0, LIQUIDUS_NOTICE_FRAC_OF_T * 100.0
    if property_name == "redox" or metric == "delta_log10_fO2":
        return REDOX_WARNING_DELTA_LOG, REDOX_NOTICE_DELTA_LOG
    if property_name == "phase" or metric == "delta_absolute_fraction":
        return PHASE_WARNING_ABS_FRAC, PHASE_NOTICE_ABS_FRAC
    return 2.0, 0.5


def _classify_flag(pert: PropertyPerturbation) -> str | None:
    warn_thr, notice_thr = _property_thresholds(pert.property, pert.metric)
    severity = max(pert.perturbation_before, pert.perturbation_after)
    if severity >= warn_thr:
        return "WARNING"
    if pert.perturbation_after >= notice_thr:
        return "NOTICE"
    return None


def evaluate_verdict_a(
    perturbations: tuple[PropertyPerturbation, ...],
    *,
    hour: int,
    confounding_threshold_pct: float = 0.01,
    residual_wt_pct: Mapping[str, float] | None = None,
) -> tuple[PropertyFlag, ...]:
    """WARN-only property-impact flags for one timeline step."""
    flags: list[PropertyFlag] = []
    for pert in perturbations:
        if residual_wt_pct is not None:
            wt = float(residual_wt_pct.get(pert.contaminant, 0.0))
            if wt < confounding_threshold_pct:
                continue
        level = _classify_flag(pert)
        if level is None:
            continue
        noise_status = "proposed" if pert.grounded else "noise_floor_ungrounded"
        flags.append(
            PropertyFlag(
                property=pert.property,
                level=level,
                contaminant=pert.contaminant,
                effect_row=pert.effect_row,
                perturbation_before=pert.perturbation_before,
                perturbation_after=pert.perturbation_after,
                metric=pert.metric,
                grounded=pert.grounded,
                hour=hour,
                noise_floor_status=noise_status,
            )
        )
    return tuple(flags)


def _bakeoff_hour_by_species(
    timeline: tuple[Any, ...],
) -> dict[str, int]:
    """First hour a carrier is cleared by escape/decompose/burn."""
    bakeoff: dict[str, int] = {}
    for entry in timeline:
        hour = int(getattr(entry, "hour", 0))
        for group_events in (getattr(entry, "by_group", {}) or {}).values():
            for event in group_events:
                carrier = str(event.get("carrier", ""))
                disposition = str(event.get("disposition", ""))
                if disposition not in {"escaped", "decomposed", "burned"}:
                    continue
                if carrier and carrier not in bakeoff:
                    bakeoff[carrier] = hour
    return bakeoff


def _estimate_hourly_residuals(
    final_residual_wt_pct: Mapping[str, float],
    timeline: tuple[Any, ...],
) -> list[tuple[int, dict[str, float]]]:
    """Step-resolved residual fractions from disposition bakeoff events."""
    if not timeline:
        return [(0, dict(final_residual_wt_pct))]

    bakeoff = _bakeoff_hour_by_species(timeline)
    hourly: list[tuple[int, dict[str, float]]] = []

    for entry in timeline:
        hour = int(getattr(entry, "hour", 0))
        residual_at_hour: dict[str, float] = {}
        for species, final_wt in final_residual_wt_pct.items():
            clear_hour = bakeoff.get(species)
            if clear_hour is not None and hour >= clear_hour:
                residual_at_hour[species] = 0.0
            else:
                residual_at_hour[species] = float(final_wt)
        hourly.append((hour, residual_at_hour))

    return hourly


def evaluate_verdict_a_timeline(
    final_residual_wt_pct: Mapping[str, float],
    melts_result: Mapping[str, Any] | None,
    engine: str,
    *,
    T_in_C: float,
    timeline: tuple[Any, ...],
    confounding_threshold_pct: float = 0.01,
) -> VerdictAResult:
    """Step-resolved WARN-only flags; clears when bakeoff drops residual."""
    hourly = _estimate_hourly_residuals(final_residual_wt_pct, timeline)
    all_flags: list[PropertyFlag] = []
    step_resolved: list[dict[str, Any]] = []

    for hour, residual_at_hour in hourly:
        adjustment = melt_effect_adjustment(
            residual_at_hour,
            melts_result,
            engine,
            T_in_C=T_in_C,
        )
        flags = evaluate_verdict_a(
            adjustment.perturbations,
            hour=hour,
            confounding_threshold_pct=confounding_threshold_pct,
            residual_wt_pct=residual_at_hour,
        )
        active = [f for f in flags if f.level]
        cleared = all(
            float(residual_at_hour.get(s, 0.0)) < confounding_threshold_pct
            for s in final_residual_wt_pct
        )
        step_resolved.append({
            "hour": hour,
            "residual_wt_pct": dict(residual_at_hour),
            "flags": [
                {
                    "property": f.property,
                    "level": f.level,
                    "contaminant": f.contaminant,
                    "cleared": cleared
                    or float(residual_at_hour.get(f.contaminant, 0.0))
                    < confounding_threshold_pct,
                }
                for f in active
            ],
        })
        all_flags.extend(flags)

    return VerdictAResult(
        flags=tuple(all_flags),
        step_resolved=tuple(step_resolved),
        warn_only=True,
    )


def _domain_gate_for_engine(engine: str):
    engine_key = str(engine).lower()
    if "magemin" in engine_key or engine_key in {"ig", "igad"}:
        return MAGEMinDomainGate
    return AlphaMELTSDomainGate


def evaluate_verdict_b(
    cleaned_melt_kg: Mapping[str, float],
    backend_status: str,
    engine: str,
) -> VerdictBResult:
    """Hard gate on stripped silicate OOD only; contaminant-present never crashes."""
    stripped = strip_non_oxide_residuals(cleaned_melt_kg)
    gate = _domain_gate_for_engine(engine)
    stripped_valid, domain_warnings = gate.validate(stripped.oxide_wt_pct)

    hard_gate_failed = not stripped_valid
    status = str(backend_status)
    if status in {"unavailable", "out_of_domain", "not_converged"} and stripped_valid:
        pass

    return VerdictBResult(
        backend_status=status,
        stripped_domain_valid=stripped_valid,
        hard_gate_failed=hard_gate_failed,
        stripped_oxide_wt_pct=dict(stripped.oxide_wt_pct),
        stripped_mass_provenance=stripped.provenance,
        domain_warnings=tuple(domain_warnings),
        engine=str(engine),
    )


def aggregate_backend_status(history: Any, latest: str) -> str:
    """Mirror run_executor._aggregate_backend_status (no new equilibrium)."""
    try:
        statuses = [str(s) for s in history]
    except TypeError:
        statuses = []
    statuses.append(str(latest))
    for status in ("unavailable", "out_of_domain", "not_converged"):
        if status in statuses:
            return status
    return str(latest)


def build_harness_verdicts(
    *,
    cleaned_melt_kg: Mapping[str, float],
    sim: Any,
    engine: str,
    timeline: tuple[Any, ...],
    T_in_C: float,
) -> dict[str, Any]:
    """Assemble verdict (a) + verdict (b) for Stage0HarnessResult."""
    residual_wt = residual_wt_pct_by_species(cleaned_melt_kg)
    strip_result = strip_non_oxide_residuals(cleaned_melt_kg)

    melts_result: dict[str, Any] = {}
    raw_liq = getattr(sim, "_last_liquidus_T_C", None)
    if raw_liq is None:
        diag = getattr(sim, "_last_backend_diagnostics", {}) or {}
        raw_liq = diag.get("liquidus_T_C") or diag.get("liquidus_C")
    if raw_liq is not None:
        melts_result["liquidus_T_C"] = float(raw_liq)

    adjustment = melt_effect_adjustment(
        residual_wt,
        melts_result or None,
        engine,
        T_in_C=T_in_C,
    )
    verdict_a = evaluate_verdict_a_timeline(
        residual_wt,
        melts_result or None,
        engine,
        T_in_C=T_in_C,
        timeline=timeline,
    )

    latest_status = str(
        getattr(
            sim,
            "_backend_selection_status",
            getattr(sim, "_last_backend_status", "ok"),
        )
    )
    backend_status = aggregate_backend_status(
        getattr(sim, "_backend_status_history", ()),
        latest_status,
    )
    verdict_b = evaluate_verdict_b(cleaned_melt_kg, backend_status, engine)

    return {
        "verdict_a": {
            "warn_only": verdict_a.warn_only,
            "flags": [
                {
                    "property": f.property,
                    "level": f.level,
                    "contaminant": f.contaminant,
                    "effect_row": f.effect_row,
                    "perturbation_before": f.perturbation_before,
                    "perturbation_after": f.perturbation_after,
                    "metric": f.metric,
                    "grounded": f.grounded,
                    "hour": f.hour,
                    "noise_floor_status": f.noise_floor_status,
                }
                for f in verdict_a.flags
            ],
            "step_resolved": list(verdict_a.step_resolved),
        },
        "verdict_b": {
            "backend_status": verdict_b.backend_status,
            "stripped_domain_valid": verdict_b.stripped_domain_valid,
            "hard_gate_failed": verdict_b.hard_gate_failed,
            "stripped_oxide_wt_pct": verdict_b.stripped_oxide_wt_pct,
            "stripped_mass_provenance": [
                {
                    "species": p.species,
                    "kg": p.kg,
                    "wt_pct_of_total": p.wt_pct_of_total,
                    "reason": p.reason,
                }
                for p in verdict_b.stripped_mass_provenance
            ],
            "domain_warnings": list(verdict_b.domain_warnings),
            "engine": verdict_b.engine,
            "contaminant_present_never_crash": True,
        },
        "strip": {
            "oxide_wt_pct": dict(strip_result.oxide_wt_pct),
            "stripped_mass_kg": strip_result.stripped_mass_kg,
            "provenance": [
                {
                    "species": p.species,
                    "kg": p.kg,
                    "wt_pct_of_total": p.wt_pct_of_total,
                    "reason": p.reason,
                }
                for p in strip_result.provenance
            ],
            "renormalized": False,
        },
        "melt_effect_adjustment": {
            "effect_table_version": adjustment.effect_table_version,
            "raw_liquidus_C": adjustment.raw_liquidus_C,
            "adjusted_liquidus_C": adjustment.adjusted_liquidus_C,
            "adjusted_liquidus_provenance": list(
                adjustment.adjusted_liquidus_provenance
            ),
            "perturbations": [
                {
                    "property": p.property,
                    "contaminant": p.contaminant,
                    "effect_row": p.effect_row,
                    "source": p.source,
                    "residual_wt_pct": p.residual_wt_pct,
                    "perturbation_before": p.perturbation_before,
                    "perturbation_after": p.perturbation_after,
                    "metric": p.metric,
                    "grounded": p.grounded,
                    "raw_value": p.raw_value,
                    f"adjusted_{p.property}": p.adjusted_value,
                    "interval": p.interval,
                }
                for p in adjustment.perturbations
            ],
            "warnings": list(adjustment.warnings),
        },
    }