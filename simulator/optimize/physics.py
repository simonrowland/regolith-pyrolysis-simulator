"""Hard physics-feasibility gates over a completed PhysicsTrace."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
import hashlib
import math
from types import MappingProxyType
from typing import Any, Literal, Mapping

from simulator.accounting import AccountingError
from simulator.accounting.completeness import (
    DEFAULT_RESIDUAL_SPECIES_BY_TARGET,
    TargetExtractionCompleteness,
    TargetSpeciesYield,
    TARGET_YIELD_DENOMINATOR_SOURCE,
    TARGET_YIELD_NUMERATOR_SOURCE,
    TARGET_YIELD_PROVENANCE_RULE,
    extraction_completeness_by_target,
    target_species_yield_by_initial_cleaned_melt,
)
from simulator.accounting.queries import AccountingQueries
from simulator.condensation_routing import accepted_species_for_stage_number
from simulator.diagnostics import wall_deposit_sticking_authority_status
from simulator.optimize.canonical import canonical_json_dumps, normalize_canonical_value
from simulator.trace import WALL_DEPOSIT_ZONE_NAMES

SourceKind = Literal[
    "literature",
    "materials.yaml",
    "profile",
    "engineering_envelope",
    "code_default",
]

# D1: coating gate armed -- fail-closed on grounded
# campaigns-to-resinter<N when authoritative; bump invalidates pre-D1 cached
# feasibility verdicts.
# v3 (2026-07-03, milestone-3 L2-P2): the extraction_completeness gate now
# routes through the S2c provenance-aware trace surface (credit-line /
# additive exclusion, honest denominator) when present -- a semantic flip,
# so pre-S2c cached feasibility verdicts must not be served under the same
# physics_constraints_digest.
# v4 (2026-07-12, t-005): optimizer results now include the body-aware
# sub-ambient pumping hard gate, so pre-wiring feasibility/cache identities
# cannot be reused.
PHYSICS_GATE_VERSION = "physics-feasibility-v4-subambient-pumping"
DEFAULT_ACTIVE_GATES: tuple[str, ...] = (
    "delivered_stream_purity",
    "coating",
    "extraction_completeness",
    "knudsen_viscous",
    "furnace_temperature",
)
GATE_ORDER: tuple[str, ...] = (*DEFAULT_ACTIVE_GATES, "cycle_time")
E1B_TARGET_SPECIES: tuple[str, ...] = ("Na", "K", "Fe", "Mg", "SiO")
E1B_TARGET_YIELD_GATE_FRACTION = 0.95
TARGET_SPECIES_YIELD_CONSUMERS: tuple[str, ...] = (
    "tests/test_north_star_baseline.py::test_e1b_future_target_species_yield_threshold",
    "product_summary.target_species_yield_report",
)
_EPS = 1.0e-12


@dataclass(frozen=True)
class ThresholdSpec:
    """Non-null threshold plus declared provenance."""

    id: str
    value: float
    units: str
    source: SourceKind
    source_ref: str
    tolerance: float = 0.0

    def __post_init__(self) -> None:
        _finite_number(self.value, f"{self.id}.value")
        if self.tolerance < 0.0 or not math.isfinite(self.tolerance):
            raise ValueError(f"{self.id}.tolerance must be finite and non-negative")
        if not self.source_ref:
            raise ValueError(f"{self.id}.source_ref must be declared")


@dataclass(frozen=True)
class GateMargin:
    gate: str
    feasible: bool
    margin: float
    threshold: ThresholdSpec
    observed: float
    detail: str
    status: str = "available"
    authoritative: bool = True
    output_status: str = "authoritative"
    status_reason: str = ""
    status_payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "status_payload",
            _plain_payload(self.status_payload),
        )


@dataclass(frozen=True)
class FeasibilityResult:
    feasible: bool
    margins: Mapping[str, GateMargin]
    version: str = PHYSICS_GATE_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "margins", MappingProxyType(dict(self.margins)))

    @property
    def failing_gates(self) -> tuple[str, ...]:
        return tuple(
            gate
            for gate in GATE_ORDER
            if gate in self.margins and not self.margins[gate].feasible
        )


class CoatingFeasibilityReportError(ValueError):
    """Runner wall-fouling report cannot support a coating verdict."""


@dataclass(frozen=True)
class PhysicsConstraintSet:
    """Stage-1 hard feasibility constraints for optimizer traces."""

    stream_purity_min: ThresholdSpec = field(default_factory=lambda: ThresholdSpec(
        id="delivered_stream_purity_min",
        value=0.95,
        units="fraction",
        source="engineering_envelope",
        source_ref="stage_purity_report PURE cutoff / optimizer profile default",
    ))
    coating_min_campaigns_to_resinter: ThresholdSpec = field(
        default_factory=lambda: ThresholdSpec(
            id="coating_min_campaigns_to_resinter",
            value=10.0,
            units="campaigns",
            source="materials.yaml",
            source_ref=(
                "data/materials.yaml:"
                "liner_materials.hot_wall_refractory_liner."
                "fast_fouling_campaign_threshold"
            ),
        )
    )
    extraction_min_fraction: ThresholdSpec = field(default_factory=lambda: ThresholdSpec(
        id="extraction_completeness_min",
        value=0.95,
        units="fraction",
        source="code_default",
        source_ref="simulator.optimize.physics.PhysicsConstraintSet.extraction_min_fraction",
    ))
    extraction_min_fraction_by_species: Mapping[str, ThresholdSpec] = field(
        default_factory=dict
    )
    knudsen_max: ThresholdSpec = field(default_factory=lambda: ThresholdSpec(
        id="knudsen_viscous_max",
        value=0.01,
        units="Kn",
        source="code_default",
        source_ref="simulator.optimize.physics.PhysicsConstraintSet.knudsen_max",
    ))
    furnace_T_max_C: ThresholdSpec = field(default_factory=lambda: ThresholdSpec(
        id="furnace_T_max_C",
        value=1800.0,
        units="degC",
        source="code_default",
        source_ref="simulator.optimize.physics.PhysicsConstraintSet.furnace_T_max_C",
    ))
    cycle_time_max_h: ThresholdSpec = field(default_factory=lambda: ThresholdSpec(
        id="cycle_time_max_h",
        value=1.0e12,
        units="h",
        source="code_default",
        source_ref="simulator.optimize.physics.PhysicsConstraintSet.cycle_time_max_h",
    ))
    target_species: tuple[str, ...] = ("SiO",)
    active_gates: tuple[str, ...] = DEFAULT_ACTIVE_GATES
    residual_species_by_target: Mapping[str, tuple[str, ...]] = field(
        default_factory=lambda: DEFAULT_RESIDUAL_SPECIES_BY_TARGET
    )
    allowable_wall_deposit_kg: Mapping[tuple[str, str], ThresholdSpec] = field(
        default_factory=dict
    )

    def __getstate__(self) -> dict[str, Any]:
        return {
            "stream_purity_min": self.stream_purity_min,
            "coating_min_campaigns_to_resinter": self.coating_min_campaigns_to_resinter,
            "extraction_min_fraction": self.extraction_min_fraction,
            "extraction_min_fraction_by_species": dict(
                self.extraction_min_fraction_by_species
            ),
            "knudsen_max": self.knudsen_max,
            "furnace_T_max_C": self.furnace_T_max_C,
            "cycle_time_max_h": self.cycle_time_max_h,
            "target_species": self.target_species,
            "active_gates": self.active_gates,
            "residual_species_by_target": dict(self.residual_species_by_target),
            "allowable_wall_deposit_kg": dict(self.allowable_wall_deposit_kg),
        }

    def __setstate__(self, state: Mapping[str, Any]) -> None:
        for key, value in state.items():
            object.__setattr__(self, key, value)
        self.__post_init__()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "residual_species_by_target",
            MappingProxyType({
                str(target): tuple(str(item) for item in species)
                for target, species in self.residual_species_by_target.items()
            }),
        )
        object.__setattr__(
            self,
            "extraction_min_fraction_by_species",
            MappingProxyType({
                str(species): threshold
                for species, threshold in self.extraction_min_fraction_by_species.items()
            }),
        )
        if self.extraction_min_fraction_by_species:
            missing = sorted(
                str(species)
                for species in self.target_species
                if str(species) not in self.extraction_min_fraction_by_species
            )
            if missing:
                raise ValueError(
                    "extraction_min_fraction_by_species missing thresholds for "
                    f"target_species: {missing}"
                )
        object.__setattr__(
            self,
            "allowable_wall_deposit_kg",
            MappingProxyType(dict(self.allowable_wall_deposit_kg)),
        )
        if not self.target_species:
            raise ValueError("target_species must be non-empty")
        active = tuple(str(gate) for gate in self.active_gates)
        if not active:
            raise ValueError("active_gates must be non-empty")
        unknown = set(active) - set(GATE_ORDER)
        if unknown:
            raise ValueError(f"active_gates contains unknown gates: {sorted(unknown)}")
        object.__setattr__(self, "active_gates", active)
        for threshold in self.thresholds:
            if threshold.value is None:
                raise ValueError(f"{threshold.id} threshold must not be null")
        for key, threshold in self.allowable_wall_deposit_kg.items():
            if len(key) != 2:
                raise ValueError("allowable_wall_deposit_kg keys are (segment, species)")
            if threshold.source not in {
                "literature",
                "materials.yaml",
                "profile",
                "engineering_envelope",
                "code_default",
            }:
                raise ValueError(f"{threshold.id} source is not declared")

    @property
    def thresholds(self) -> tuple[ThresholdSpec, ...]:
        thresholds = [
            self.stream_purity_min,
            self.coating_min_campaigns_to_resinter,
            self.extraction_min_fraction,
            *tuple(self.extraction_min_fraction_by_species.values()),
            self.knudsen_max,
            self.furnace_T_max_C,
            *tuple(self.allowable_wall_deposit_kg.values()),
        ]
        if "cycle_time" in self.active_gates:
            thresholds.append(self.cycle_time_max_h)
        return tuple(thresholds)

    def threshold_provenance_table(self) -> tuple[tuple[str, str, str], ...]:
        rows = [
            (
                "delivered_stream_purity",
                f"{self.stream_purity_min.id}={self.stream_purity_min.value:g}",
                self.stream_purity_min.source,
            ),
            (
                "coating",
                (
                    f"{self.coating_min_campaigns_to_resinter.id}="
                    f"{self.coating_min_campaigns_to_resinter.value:g}"
                ),
                self.coating_min_campaigns_to_resinter.source,
            ),
            (
                "extraction_completeness",
                f"{self.extraction_min_fraction.id}={self.extraction_min_fraction.value:g}",
                self.extraction_min_fraction.source,
            ),
            (
                "knudsen_viscous",
                f"{self.knudsen_max.id}={self.knudsen_max.value:g}",
                self.knudsen_max.source,
            ),
            (
                "furnace_temperature",
                f"{self.furnace_T_max_C.id}={self.furnace_T_max_C.value:g}",
                self.furnace_T_max_C.source,
            ),
        ]
        if "cycle_time" in self.active_gates:
            rows.append((
                "cycle_time",
                f"{self.cycle_time_max_h.id}={self.cycle_time_max_h.value:g}",
                self.cycle_time_max_h.source,
            ))
        for species, threshold in sorted(self.extraction_min_fraction_by_species.items()):
            rows.append((
                "extraction_completeness",
                f"extraction_min_fraction_by_species[{species}]={threshold.value:g}",
                threshold.source,
            ))
        for (segment, species), threshold in sorted(self.allowable_wall_deposit_kg.items()):
            rows.append((
                "coating",
                f"allowable_wall_deposit_kg[{segment}][{species}]={threshold.value:g}",
                threshold.source,
            ))
        return tuple(rows)

    def digest(self) -> str:
        return physics_constraints_digest(self)

    def evaluate(self, trace: Any) -> FeasibilityResult:
        evaluators = {
            "delivered_stream_purity": self.delivered_stream_purity,
            "coating": self.coating,
            "extraction_completeness": self.extraction_completeness,
            "knudsen_viscous": self.knudsen_viscous,
            "furnace_temperature": self.furnace_temperature,
            "cycle_time": self.cycle_time,
        }
        margins = {
            gate: evaluators[gate](trace)
            for gate in GATE_ORDER
            if gate in self.active_gates
        }
        return FeasibilityResult(
            feasible=all(margin.feasible for margin in margins.values()),
            margins=margins,
        )

    def delivered_stream_purity(self, trace: Any) -> GateMargin:
        try:
            snapshots = _required_sequence(trace, "snapshots")
            deltas = _required_sequence(trace, "condensed_by_stage_species_delta")
            if len(deltas) != len(snapshots):
                return _fail_closed(
                    "delivered_stream_purity",
                    self.stream_purity_min,
                    "condensed delta count does not match snapshots",
                )
            totals: dict[int, dict[str, float]] = defaultdict(lambda: defaultdict(float))
            for tick in deltas:
                if not isinstance(tick, Mapping):
                    return _fail_closed(
                        "delivered_stream_purity",
                        self.stream_purity_min,
                        "condensed_by_stage_species_delta tick is not a mapping",
                    )
                for key, kg in tick.items():
                    stage, species = _stage_species_key(key)
                    amount = _non_negative_number(kg, "condensed kg")
                    totals[stage][species] += amount
            worst_margin = math.inf
            worst_observed = 1.0
            worst_detail = "no delivered stream"
            for stage, species_kg in sorted(totals.items()):
                total_kg = sum(species_kg.values())
                if total_kg <= _EPS:
                    continue
                accepted = accepted_species_for_stage_number(stage)
                designated_kg = sum(
                    kg for species, kg in species_kg.items() if species in accepted
                )
                purity = designated_kg / total_kg
                margin = purity - self.stream_purity_min.value
                if margin < worst_margin:
                    contaminants = {
                        species: kg
                        for species, kg in species_kg.items()
                        if species not in accepted and kg > _EPS
                    }
                    worst_margin = margin
                    worst_observed = purity
                    worst_detail = (
                        f"stage {stage} purity {purity:.6g}; "
                        f"contaminants={contaminants}"
                    )
            if math.isinf(worst_margin):
                return _margin(
                    "delivered_stream_purity",
                    -self.stream_purity_min.value,
                    self.stream_purity_min,
                    0.0,
                    "zero delivered stream",
                )
            return _margin(
                "delivered_stream_purity",
                worst_margin,
                self.stream_purity_min,
                worst_observed,
                worst_detail,
            )
        except (KeyError, TypeError, ValueError) as exc:
            return _fail_closed("delivered_stream_purity", self.stream_purity_min, str(exc))

    def coating(self, trace: Any) -> GateMargin:
        if hasattr(trace, "wall_fouling_report"):
            return self.coating_from_fouling_report(trace.wall_fouling_report)
        try:
            snapshots = _required_sequence(trace, "snapshots")
            deltas = _required_sequence(trace, "wall_deposit_by_segment_species_delta")
            if len(deltas) != len(snapshots):
                return _fail_closed(
                    "coating",
                    self.coating_min_campaigns_to_resinter,
                    "wall-deposit delta count does not match snapshots",
                )
            by_campaign: dict[tuple[str, str, str], float] = defaultdict(float)
            for snapshot, tick in zip(snapshots, deltas, strict=True):
                if not isinstance(tick, Mapping):
                    return _fail_closed(
                        "coating",
                        self.coating_min_campaigns_to_resinter,
                        "wall_deposit_by_segment_species_delta tick is not a mapping",
                    )
                campaign = _campaign_name(snapshot)
                for key, kg in tick.items():
                    segment, species = _segment_species_key(key)
                    amount = _non_negative_number(kg, "wall deposit kg")
                    by_campaign[(campaign, segment, species)] += amount
            has_wall_deposit = any(kg > _EPS for kg in by_campaign.values())
            authority = _coating_authority_status(trace, by_campaign)
            authoritative = _authority_is_authoritative(authority)
            zone_by_segment: Mapping[Any, Any] | None = None
            if has_wall_deposit:
                zone_by_segment = getattr(trace, "wall_zone_by_segment", None)
                if zone_by_segment is None:
                    return _fail_closed(
                        "coating",
                        self.coating_min_campaigns_to_resinter,
                        "wall_zone_by_segment trace is missing for wall deposit",
                    )
                if not isinstance(zone_by_segment, Mapping):
                    return _fail_closed(
                        "coating",
                        self.coating_min_campaigns_to_resinter,
                        "wall_zone_by_segment trace is not a mapping",
                    )
            worst_margin = math.inf
            worst_observed = math.inf
            worst_detail = "no wall deposit"
            worst_campaign_detail = "no wall deposit"
            for (campaign, segment, species), kg in sorted(by_campaign.items()):
                if kg <= _EPS:
                    continue
                if zone_by_segment is None:
                    return _fail_closed(
                        "coating",
                        self.coating_min_campaigns_to_resinter,
                        "wall_zone_by_segment trace is missing for wall deposit",
                    )
                if segment not in zone_by_segment:
                    return _fail_closed(
                        "coating",
                        self.coating_min_campaigns_to_resinter,
                        f"missing wall zone for segment {segment}",
                    )
                zone = str(zone_by_segment[segment])
                if zone not in WALL_DEPOSIT_ZONE_NAMES:
                    return _fail_closed(
                        "coating",
                        self.coating_min_campaigns_to_resinter,
                        f"unknown wall zone {zone!r} for segment {segment}",
                    )
                limit = self.allowable_wall_deposit_kg.get((segment, species))
                if limit is None:
                    if math.isinf(worst_margin):
                        worst_margin = 0.0
                        worst_observed = math.inf
                        worst_detail = (
                            f"{campaign}/{zone}/{segment}/{species}: "
                            f"deposit={kg:.6g} kg, allowable=unconfigured; "
                            "absolute kg limit unconfigured; "
                            "campaigns_to_resinter=unreported"
                        )
                    continue
                campaigns_to_resinter = limit.value / kg
                campaign_margin = (
                    campaigns_to_resinter
                    - self.coating_min_campaigns_to_resinter.value
                )
                absolute_margin = limit.value - kg
                margin = min(campaign_margin, absolute_margin)
                if margin < worst_margin:
                    worst_margin = margin
                    worst_detail = (
                        f"{campaign}/{zone}/{segment}/{species}: "
                        f"deposit={kg:.6g} kg, "
                        f"allowable={limit.value:.6g} kg, "
                        f"campaigns_to_resinter={campaigns_to_resinter:.6g}"
                    )
                if campaigns_to_resinter < worst_observed:
                    worst_observed = campaigns_to_resinter
                    worst_campaign_detail = (
                        f"{campaign}/{zone}/{segment}/{species}: "
                        f"deposit={kg:.6g} kg, "
                        f"allowable={limit.value:.6g} kg, "
                        f"campaigns_to_resinter={campaigns_to_resinter:.6g}"
                    )
            if math.isinf(worst_margin):
                worst_margin = math.inf
            detail = (
                worst_detail
                if worst_detail == "no wall deposit"
                else f"reported-only: {worst_detail}"
            )
            grounded_campaign_ok = (
                worst_observed >= self.coating_min_campaigns_to_resinter.value
            )
            feasible = (not authoritative) or grounded_campaign_ok
            if not authoritative:
                detail = (
                    "non-authoritative: grounded coating criterion not enforced; "
                    f"{detail}"
                )
            elif not grounded_campaign_ok:
                detail = (
                    "fail-closed: grounded coating criterion "
                    f"campaigns_to_resinter={worst_observed:.6g} < "
                    f"{self.coating_min_campaigns_to_resinter.value:.6g}; "
                    f"{worst_campaign_detail}; advisory={detail}"
                )
            elif worst_detail != "no wall deposit":
                detail = (
                    "grounded coating criterion satisfied: "
                    f"campaigns_to_resinter={worst_observed:.6g} >= "
                    f"{self.coating_min_campaigns_to_resinter.value:.6g}; "
                    f"{detail}"
                )
            return GateMargin(
                gate="coating",
                feasible=feasible,
                margin=float(worst_margin),
                threshold=self.coating_min_campaigns_to_resinter,
                observed=float(worst_observed),
                detail=detail,
                status="available" if authoritative else "warning",
                authoritative=authoritative,
                output_status=str(authority.get("output_status", "authoritative")),
                status_reason=(
                    ""
                    if authoritative
                    else str(authority.get("message", "non-authoritative coating"))
                ),
                status_payload=authority,
            )
        except (KeyError, TypeError, ValueError) as exc:
            return _fail_closed("coating", self.coating_min_campaigns_to_resinter, str(exc))

    def coating_from_fouling_report(self, report: Any) -> GateMargin:
        """Classify the runner's total-load lifespan verdict.

        A non-authoritative wall-sticking or threshold verdict is deliberately
        unconstrained by coating: heuristics remain visible, but never become a
        hard feasibility block.
        """
        if not isinstance(report, Mapping):
            raise CoatingFeasibilityReportError(
                "wall-fouling report must be a mapping"
            )
        required = (
            "campaigns_to_resinter_total",
            "authoritative_for_resinter",
            "output_status",
            "status_reason",
        )
        missing = tuple(key for key in required if key not in report)
        if missing:
            raise CoatingFeasibilityReportError(
                f"wall-fouling report missing required fields: {missing}"
            )
        authoritative_raw = report["authoritative_for_resinter"]
        if not isinstance(authoritative_raw, bool):
            raise CoatingFeasibilityReportError(
                "wall-fouling authoritative_for_resinter must be bool"
            )
        authoritative = authoritative_raw
        output_status = report["output_status"]
        status_reason = report["status_reason"]
        if not isinstance(output_status, str):
            raise CoatingFeasibilityReportError(
                "wall-fouling output_status must be str"
            )
        if not isinstance(status_reason, str):
            raise CoatingFeasibilityReportError(
                "wall-fouling status_reason must be str"
            )
        raw_observed = report["campaigns_to_resinter_total"]
        if isinstance(raw_observed, bool) or not isinstance(raw_observed, int | float):
            raise CoatingFeasibilityReportError(
                "wall-fouling campaigns_to_resinter_total must be numeric"
            )
        observed = float(raw_observed)
        if math.isnan(observed) or observed < 0.0:
            raise CoatingFeasibilityReportError(
                "wall-fouling campaigns_to_resinter_total must be non-negative"
            )
        margin = observed - self.coating_min_campaigns_to_resinter.value
        feasible = (
            not authoritative
            or margin >= -self.coating_min_campaigns_to_resinter.tolerance
        )
        if authoritative:
            detail = (
                f"runner wall-fouling campaigns_to_resinter_total={observed:.6g}; "
                f"minimum={self.coating_min_campaigns_to_resinter.value:.6g}"
            )
        else:
            detail = (
                "non-authoritative: coating feasibility unconstrained; "
                f"output_status={output_status}; "
                f"status_reason={status_reason}"
            )
        return GateMargin(
            gate="coating",
            feasible=feasible,
            margin=margin,
            threshold=self.coating_min_campaigns_to_resinter,
            observed=observed,
            detail=detail,
            status="available" if authoritative else "warning",
            authoritative=authoritative,
            output_status=output_status,
            status_reason="" if authoritative else status_reason,
            status_payload=report,
        )

    def extraction_completeness(self, trace: Any) -> GateMargin:
        try:
            by_target = _extraction_completeness_by_target_for_trace(
                trace,
                self,
            )
            worst_margin = math.inf
            worst_fraction = 1.0
            worst_detail = ""
            worst_threshold = self.extraction_min_fraction
            for target in self.target_species:
                threshold = self._extraction_threshold_for_target(str(target))
                result = by_target[str(target)]
                if result.completeness_fraction is None:
                    detail = _extraction_completeness_fail_closed_detail(result)
                    if detail.startswith("not-applicable:"):
                        return _not_applicable(
                            "extraction_completeness",
                            threshold,
                            detail,
                        )
                    return _fail_closed(
                        "extraction_completeness",
                        threshold,
                        detail,
                    )
                fraction = result.completeness_fraction
                margin = fraction - threshold.value
                if margin < worst_margin:
                    worst_margin = margin
                    worst_fraction = fraction
                    worst_detail = result.detail
                    worst_threshold = threshold
            return _margin(
                "extraction_completeness",
                worst_margin,
                worst_threshold,
                worst_fraction,
                worst_detail,
            )
        except (AccountingError, KeyError, TypeError, ValueError) as exc:
            return _fail_closed(
                "extraction_completeness",
                self.extraction_min_fraction,
                str(exc),
            )

    def _extraction_threshold_for_target(self, target: str) -> ThresholdSpec:
        return self.extraction_min_fraction_by_species.get(
            str(target),
            self.extraction_min_fraction,
        )

    def knudsen_viscous(self, trace: Any) -> GateMargin:
        try:
            snapshots = _required_sequence(trace, "snapshots")
            if not snapshots:
                return _fail_closed(
                    "knudsen_viscous",
                    self.knudsen_max,
                    "trace has no snapshots",
                )
            worst_margin = math.inf
            worst_kn = 0.0
            worst_detail = ""
            for index, snapshot in enumerate(snapshots):
                summary = getattr(snapshot, "knudsen_regime_summary", None)
                if not isinstance(summary, Mapping) or not summary:
                    return _fail_closed(
                        "knudsen_viscous",
                        self.knudsen_max,
                        f"snapshot {index} missing knudsen_regime_summary",
                    )
                values = _knudsen_segment_values(summary)
                if not values:
                    return _fail_closed(
                        "knudsen_viscous",
                        self.knudsen_max,
                        f"snapshot {index} missing per-segment knudsen diagnostics",
                    )
                for label, kn, regime in values:
                    margin = self.knudsen_max.value - kn
                    if regime != "viscous":
                        margin = min(margin, -math.inf)
                    if margin < worst_margin:
                        worst_margin = margin
                        worst_kn = kn
                        worst_detail = (
                            f"snapshot {index} {label} Kn={kn:.6g} regime={regime}"
                        )
            return _margin(
                "knudsen_viscous",
                worst_margin,
                self.knudsen_max,
                worst_kn,
                worst_detail,
            )
        except (KeyError, TypeError, ValueError) as exc:
            return _fail_closed("knudsen_viscous", self.knudsen_max, str(exc))

    def furnace_temperature(self, trace: Any) -> GateMargin:
        try:
            snapshots = _required_sequence(trace, "snapshots")
            if not snapshots:
                return _fail_closed(
                    "furnace_temperature",
                    self.furnace_T_max_C,
                    "trace has no snapshots",
                )
            max_temperature = -math.inf
            max_index = -1
            for index, snapshot in enumerate(snapshots):
                temperature = _finite_number(
                    getattr(snapshot, "temperature_C", None),
                    "temperature_C",
                )
                if temperature > max_temperature:
                    max_temperature = temperature
                    max_index = index
            return _margin(
                "furnace_temperature",
                self.furnace_T_max_C.value - max_temperature,
                self.furnace_T_max_C,
                max_temperature,
                f"snapshot {max_index} temperature_C={max_temperature:.6g}",
            )
        except (KeyError, TypeError, ValueError) as exc:
            return _fail_closed("furnace_temperature", self.furnace_T_max_C, str(exc))

    def cycle_time(self, trace: Any) -> GateMargin:
        try:
            snapshots = _required_sequence(trace, "snapshots")
            if not snapshots:
                return _fail_closed(
                    "cycle_time",
                    self.cycle_time_max_h,
                    "trace has no snapshots",
                )
            max_hour = -math.inf
            max_index = -1
            for index, snapshot in enumerate(snapshots):
                hour = _finite_number(getattr(snapshot, "hour", None), "hour")
                if hour > max_hour:
                    max_hour = hour
                    max_index = index
            return _margin(
                "cycle_time",
                self.cycle_time_max_h.value - max_hour,
                self.cycle_time_max_h,
                max_hour,
                f"snapshot {max_index} hour={max_hour:.6g}",
            )
        except (KeyError, TypeError, ValueError) as exc:
            return _fail_closed("cycle_time", self.cycle_time_max_h, str(exc))


def _extraction_completeness_fail_closed_detail(
    result: TargetExtractionCompleteness,
) -> str:
    if result.reason.startswith("not-applicable:"):
        return result.reason
    if result.reason == "no target-equivalent mol evidence":
        return result.detail
    if result.reason.startswith("unknown: "):
        return result.reason.removeprefix("unknown: ")
    return result.detail


def extraction_completeness_report(
    trace: Any,
    constraints: PhysicsConstraintSet | None = None,
) -> Mapping[str, Any]:
    active_constraints = constraints or PhysicsConstraintSet()
    targets = tuple(str(target) for target in active_constraints.target_species)
    try:
        by_target = _extraction_completeness_by_target_for_trace(
            trace,
            active_constraints,
            require_residual_species=True,
        )
    except (AccountingError, AttributeError, KeyError, TypeError, ValueError) as exc:
        target_payloads = {
            target: _extraction_completeness_insufficient_report(
                target,
                active_constraints,
                str(exc),
            )
            for target in targets
        }
        return _extraction_completeness_report_payload(
            "insufficient-evidence",
            "inconclusive",
            target_payloads,
            str(exc),
        )

    target_payloads = {
        target: _extraction_completeness_target_report(
            by_target[target],
            active_constraints,
        )
        for target in targets
    }
    insufficient = tuple(
        payload
        for payload in target_payloads.values()
        if payload["status"] != "reported"
    )
    if insufficient:
        return _extraction_completeness_report_payload(
            "insufficient-evidence",
            "inconclusive",
            target_payloads,
            str(insufficient[0]["reason"]),
        )
    worst_target = min(
        targets,
        key=lambda target: target_payloads[target]["completeness_fraction"],
    )
    return _extraction_completeness_report_payload(
        "reported",
        "reported",
        target_payloads,
        "",
        worst_target=worst_target,
        completeness_fraction=target_payloads[worst_target]["completeness_fraction"],
    )


_EXTRACTION_COMPLETENESS_LEDGER_TOLERANCE = 1.0e-6


def _ledger_mappings_from_trace(
    trace: Any,
) -> tuple[Mapping[Any, Any], Mapping[Any, Any]] | None:
    products = getattr(trace, "product_ledger_kg", None)
    rump = getattr(trace, "terminal_rump_by_species_kg", None)
    if products is None or rump is None:
        return None
    if not isinstance(products, Mapping) or not isinstance(rump, Mapping):
        return None
    return products, rump


def _assert_extraction_completeness_provenance_matches_ledger(
    provenance_results: Mapping[str, TargetExtractionCompleteness],
    ledger_results: Mapping[str, TargetExtractionCompleteness],
    targets: tuple[str, ...],
) -> None:
    for target in targets:
        carried = provenance_results[target]
        ledger = ledger_results[target]
        carried_fraction = carried.completeness_fraction
        if carried_fraction is None:
            continue
        ledger_fraction = ledger.completeness_fraction
        if ledger_fraction is None:
            raise ValueError(
                "extraction completeness provenance contradicts ledger for "
                f"{target}: carried completeness_fraction={carried_fraction!r}, "
                f"ledger completeness_fraction=None "
                f"(ledger reason={ledger.reason!r})"
            )
        if not math.isclose(
            carried_fraction,
            ledger_fraction,
            abs_tol=_EXTRACTION_COMPLETENESS_LEDGER_TOLERANCE,
            rel_tol=0.0,
        ):
            raise ValueError(
                "extraction completeness provenance contradicts ledger for "
                f"{target}: carried completeness_fraction={carried_fraction!r}, "
                f"ledger completeness_fraction={ledger_fraction!r}"
            )


def _extraction_completeness_by_target_for_trace(
    trace: Any,
    constraints: PhysicsConstraintSet,
    *,
    require_residual_species: bool = False,
) -> Mapping[str, TargetExtractionCompleteness]:
    provenance_results = _provenance_extraction_results_from_trace(
        trace,
        constraints,
    )
    if provenance_results is not None:
        ledger_mappings = _ledger_mappings_from_trace(trace)
        if ledger_mappings is None:
            raise ValueError(
                "extraction completeness provenance cannot be verified without "
                "product_ledger_kg and terminal_rump_by_species_kg ledgers"
            )
        products, rump = ledger_mappings
        ledger_results = extraction_completeness_by_target(
            tuple(str(target) for target in constraints.target_species),
            constraints.residual_species_by_target,
            products,
            rump,
            require_residual_species=require_residual_species,
        )
        _assert_extraction_completeness_provenance_matches_ledger(
            provenance_results,
            ledger_results,
            tuple(str(target) for target in constraints.target_species),
        )
        return provenance_results
    products = _required_mapping(trace, "product_ledger_kg")
    rump = _required_mapping(trace, "terminal_rump_by_species_kg")
    return extraction_completeness_by_target(
        tuple(str(target) for target in constraints.target_species),
        constraints.residual_species_by_target,
        products,
        rump,
        require_residual_species=require_residual_species,
    )


def _provenance_extraction_results_from_trace(
    trace: Any,
    constraints: PhysicsConstraintSet,
) -> Mapping[str, TargetExtractionCompleteness] | None:
    values = getattr(trace, "extraction_completeness_by_target", None)
    if not isinstance(values, Mapping) or not values:
        return None
    results: dict[str, TargetExtractionCompleteness] = {}
    for target in tuple(str(target) for target in constraints.target_species):
        payload = values.get(target)
        if not isinstance(payload, Mapping):
            return None
        results[target] = _target_extraction_result_from_payload(target, payload)
    return MappingProxyType(results)


def _target_extraction_result_from_payload(
    target: str,
    payload: Mapping[str, Any],
) -> TargetExtractionCompleteness:
    fraction_raw = payload.get("completeness_fraction")
    fraction = None if fraction_raw is None else _finite_number(
        fraction_raw,
        f"{target}.completeness_fraction",
    )
    return TargetExtractionCompleteness(
        target,
        fraction,
        _optional_finite_number(
            payload.get("product_target_equiv_mol"),
            f"{target}.product_target_equiv_mol",
        ),
        _optional_finite_number(
            payload.get("residual_target_equiv_mol"),
            f"{target}.residual_target_equiv_mol",
        ),
        _optional_finite_number(
            payload.get("denominator_target_equiv_mol"),
            f"{target}.denominator_target_equiv_mol",
        ),
        str(payload.get("reason") or "unknown: no result"),
        wall_deposit_target_equiv_mol=_optional_finite_number(
            payload.get("wall_deposit_target_equiv_mol"),
            f"{target}.wall_deposit_target_equiv_mol",
        ),
        reagent_target_equiv_mol=_optional_finite_number(
            payload.get("reagent_target_equiv_mol"),
            f"{target}.reagent_target_equiv_mol",
        ),
        gross_product_target_equiv_mol=_optional_finite_number(
            payload.get("gross_product_target_equiv_mol"),
            f"{target}.gross_product_target_equiv_mol",
        ),
        contract_id=str(payload.get("contract_id") or ""),
        feedstock_recovered_reagent_target_equiv_mol=_optional_finite_number(
            payload.get("feedstock_recovered_reagent_target_equiv_mol"),
            f"{target}.feedstock_recovered_reagent_target_equiv_mol",
        ),
        credit_line_reagent_target_equiv_mol=_optional_finite_number(
            payload.get("credit_line_reagent_target_equiv_mol"),
            f"{target}.credit_line_reagent_target_equiv_mol",
        ),
        external_additive_reagent_target_equiv_mol=_optional_finite_number(
            payload.get("external_additive_reagent_target_equiv_mol"),
            f"{target}.external_additive_reagent_target_equiv_mol",
        ),
        denominator_basis_source=str(
            payload.get("denominator_basis_source") or "product_plus_residual"
        ),
    )


def _extraction_completeness_target_report(
    result: TargetExtractionCompleteness,
    constraints: PhysicsConstraintSet,
) -> Mapping[str, Any]:
    target = str(result.target_species)
    threshold = constraints._extraction_threshold_for_target(target)
    residual_species = _residual_species_for_target(target, constraints)
    has_denominator = result.completeness_fraction is not None
    payload: dict[str, Any] = {
        "status": "reported",
        "conclusion": "reported",
        "target_species": target,
        "denominator_account": _extraction_denominator_account(),
        "denominator_basis": "target_equivalent_mol",
        "allowed_residual": _allowed_residual_payload(
            residual_species,
            threshold,
            result.denominator_target_equiv_mol if has_denominator else None,
            has_denominator,
        ),
        "product_bin": target,
        "product_account": "product_ledger_kg",
        "product_target_equiv_mol": (
            result.product_target_equiv_mol if has_denominator else None
        ),
        "gross_product_target_equiv_mol": (
            result.gross_product_target_equiv_mol
            if result.gross_product_target_equiv_mol
            else None
        ),
        "residual_target_equiv_mol": (
            result.residual_target_equiv_mol if has_denominator else None
        ),
        "denominator_target_equiv_mol": (
            result.denominator_target_equiv_mol if has_denominator else None
        ),
        "feedstock_recovered_reagent_target_equiv_mol": (
            result.feedstock_recovered_reagent_target_equiv_mol
            if has_denominator
            else None
        ),
        "credit_line_reagent_target_equiv_mol": (
            result.credit_line_reagent_target_equiv_mol
            if has_denominator
            else None
        ),
        "external_additive_reagent_target_equiv_mol": (
            result.external_additive_reagent_target_equiv_mol
            if has_denominator
            else None
        ),
        "denominator_basis_source": result.denominator_basis_source,
        "completeness_fraction": result.completeness_fraction,
        "reason": "",
        "detail": result.detail,
    }
    if result.completeness_fraction is None:
        payload.update({
            "status": "insufficient-evidence",
            "conclusion": "inconclusive",
            "reason": _extraction_completeness_fail_closed_detail(result),
        })
    return MappingProxyType(payload)


def _extraction_completeness_insufficient_report(
    target: str,
    constraints: PhysicsConstraintSet,
    reason: str,
) -> Mapping[str, Any]:
    threshold = constraints._extraction_threshold_for_target(target)
    residual_species = _residual_species_for_target(target, constraints)
    return MappingProxyType({
        "status": "insufficient-evidence",
        "conclusion": "inconclusive",
        "target_species": target,
        "denominator_account": _extraction_denominator_account(),
        "denominator_basis": "target_equivalent_mol",
        "allowed_residual": _allowed_residual_payload(
            residual_species,
            threshold,
            None,
            False,
        ),
        "product_bin": target,
        "product_account": "product_ledger_kg",
        "product_target_equiv_mol": None,
        "gross_product_target_equiv_mol": None,
        "residual_target_equiv_mol": None,
        "denominator_target_equiv_mol": None,
        "feedstock_recovered_reagent_target_equiv_mol": None,
        "credit_line_reagent_target_equiv_mol": None,
        "external_additive_reagent_target_equiv_mol": None,
        "denominator_basis_source": "product_plus_residual",
        "completeness_fraction": None,
        "reason": reason,
        "detail": f"{target}: {reason}",
    })


def _extraction_completeness_report_payload(
    status: str,
    conclusion: str,
    target_payloads: Mapping[str, Mapping[str, Any]],
    reason: str,
    *,
    worst_target: str | None = None,
    completeness_fraction: float | None = None,
) -> Mapping[str, Any]:
    return MappingProxyType({
        "status": status,
        "conclusion": conclusion,
        "aggregation": "min_all_targets",
        "worst_target_species": worst_target,
        "completeness_fraction": completeness_fraction,
        "reason": reason,
        "targets": MappingProxyType(dict(target_payloads)),
    })


def target_species_yield_report(
    sim: Any,
    *,
    target_species: tuple[str, ...] = E1B_TARGET_SPECIES,
    gate_fraction: float = E1B_TARGET_YIELD_GATE_FRACTION,
) -> Mapping[str, Any]:
    targets = tuple(str(target) for target in target_species)
    try:
        threshold = _finite_number(gate_fraction, "target_species_yield gate_fraction")
        queries = AccountingQueries(sim)
        initial_cleaned_melt = queries.initial_cleaned_melt_kg()
        by_target = target_species_yield_by_initial_cleaned_melt(
            targets,
            initial_cleaned_melt,
            queries,
        )
    except (AccountingError, AttributeError, KeyError, TypeError, ValueError) as exc:
        payloads = {
            target: _target_species_yield_insufficient_payload(target, str(exc))
            for target in targets
        }
        return _target_species_yield_report_payload(
            "insufficient-evidence",
            "inconclusive",
            payloads,
            str(exc),
            threshold=gate_fraction,
        )

    payloads = {
        target: _target_species_yield_payload(by_target[target], threshold)
        for target in targets
    }
    blocked = tuple(
        payload for payload in payloads.values()
        if payload["status"] == "insufficient-evidence"
    )
    if blocked:
        return _target_species_yield_report_payload(
            "insufficient-evidence",
            "inconclusive",
            payloads,
            str(blocked[0]["reason"]),
            threshold=threshold,
        )

    applicable = tuple(
        target for target, payload in payloads.items()
        if payload["applicable"]
    )
    not_applicable = tuple(
        target for target, payload in payloads.items()
        if not payload["applicable"]
    )
    if not applicable:
        return _target_species_yield_report_payload(
            "not-applicable",
            "not-applicable",
            payloads,
            "no applicable target species",
            threshold=threshold,
            applicable=applicable,
            not_applicable=not_applicable,
        )
    worst_target = min(
        applicable,
        key=lambda target: payloads[target]["yield_fraction"],
    )
    return _target_species_yield_report_payload(
        "reported",
        "reported",
        payloads,
        "",
        threshold=threshold,
        applicable=applicable,
        not_applicable=not_applicable,
        worst_target=worst_target,
        worst_yield_fraction=payloads[worst_target]["yield_fraction"],
    )


def _target_species_yield_payload(
    result: TargetSpeciesYield,
    gate_fraction: float,
) -> Mapping[str, Any]:
    has_denominator = result.yield_fraction is not None
    status = "reported"
    if result.reason.startswith("not-applicable:"):
        status = "not-applicable"
    elif result.yield_fraction is None:
        status = "insufficient-evidence"
    return MappingProxyType({
        "status": status,
        "conclusion": "reported" if has_denominator else status,
        "applicable": has_denominator,
        "target_species": result.target_species,
        "denominator_source": result.denominator_source,
        "denominator_basis": "target_equivalent_mol",
        "initial_cleaned_target_equiv_mol": (
            result.initial_cleaned_target_equiv_mol if has_denominator else None
        ),
        "numerator_source": result.numerator_source,
        "provenance_rule": result.provenance_rule,
        "product_account": TARGET_YIELD_NUMERATOR_SOURCE,
        "product_species_kg": MappingProxyType(dict(result.product_species_kg)),
        "exact_product_kg": result.exact_product_kg if has_denominator else None,
        "product_target_equiv_mol": (
            result.product_target_equiv_mol if has_denominator else None
        ),
        "gross_product_target_equiv_mol": (
            result.gross_product_target_equiv_mol if has_denominator else None
        ),
        "excluded_non_feedstock_reagent_target_equiv_mol": (
            result.excluded_non_feedstock_reagent_target_equiv_mol
            if has_denominator
            else None
        ),
        "yield_fraction": result.yield_fraction,
        "yield_pct": (
            result.yield_fraction * 100.0
            if result.yield_fraction is not None
            else None
        ),
        "gate_fraction": gate_fraction,
        "gap_to_gate_fraction": (
            gate_fraction - result.yield_fraction
            if result.yield_fraction is not None
            else None
        ),
        "reason": result.reason,
    })


def _target_species_yield_insufficient_payload(
    target: str,
    reason: str,
) -> Mapping[str, Any]:
    return MappingProxyType({
        "status": "insufficient-evidence",
        "conclusion": "inconclusive",
        "applicable": False,
        "target_species": target,
        "denominator_source": TARGET_YIELD_DENOMINATOR_SOURCE,
        "denominator_basis": "target_equivalent_mol",
        "initial_cleaned_target_equiv_mol": None,
        "numerator_source": TARGET_YIELD_NUMERATOR_SOURCE,
        "provenance_rule": TARGET_YIELD_PROVENANCE_RULE,
        "product_account": TARGET_YIELD_NUMERATOR_SOURCE,
        "product_species_kg": MappingProxyType({}),
        "exact_product_kg": None,
        "product_target_equiv_mol": None,
        "gross_product_target_equiv_mol": None,
        "excluded_non_feedstock_reagent_target_equiv_mol": None,
        "yield_fraction": None,
        "yield_pct": None,
        "gate_fraction": E1B_TARGET_YIELD_GATE_FRACTION,
        "gap_to_gate_fraction": None,
        "reason": reason,
    })


def _target_species_yield_report_payload(
    status: str,
    conclusion: str,
    target_payloads: Mapping[str, Mapping[str, Any]],
    reason: str,
    *,
    threshold: float,
    applicable: tuple[str, ...] = (),
    not_applicable: tuple[str, ...] = (),
    worst_target: str | None = None,
    worst_yield_fraction: float | None = None,
) -> Mapping[str, Any]:
    return MappingProxyType({
        "status": status,
        "conclusion": conclusion,
        "consumer": TARGET_SPECIES_YIELD_CONSUMERS,
        "gate_status": "skipped_pending_physics",
        "gate_fraction": threshold,
        "denominator_source": TARGET_YIELD_DENOMINATOR_SOURCE,
        "numerator_source": TARGET_YIELD_NUMERATOR_SOURCE,
        "provenance_rule": TARGET_YIELD_PROVENANCE_RULE,
        "aggregation": "min_applicable_targets",
        "applicable_target_species": applicable,
        "not_applicable_target_species": not_applicable,
        "worst_target_species": worst_target,
        "worst_yield_fraction": worst_yield_fraction,
        "reason": reason,
        "targets": MappingProxyType(dict(target_payloads)),
    })


def _extraction_denominator_account() -> Mapping[str, str]:
    return MappingProxyType({
        "product": "product_ledger_kg",
        "residual": "terminal_rump_by_species_kg",
    })


def _allowed_residual_payload(
    residual_species: tuple[str, ...],
    threshold: ThresholdSpec,
    denominator_target_equiv_mol: float | None,
    has_denominator: bool,
) -> Mapping[str, Any]:
    allowed_fraction = max(0.0, 1.0 - threshold.value)
    return MappingProxyType({
        "account": "terminal_rump_by_species_kg",
        "species": residual_species,
        "fraction": allowed_fraction,
        "target_equiv_mol": (
            denominator_target_equiv_mol * allowed_fraction
            if has_denominator
            else None
        ),
    })


def _residual_species_for_target(
    target: str,
    constraints: PhysicsConstraintSet,
) -> tuple[str, ...]:
    return tuple(constraints.residual_species_by_target.get(target, ()))


def _coating_authority_status(
    trace: Any,
    by_campaign: Mapping[tuple[str, str, str], float],
) -> dict[str, Any]:
    by_segment_species: dict[tuple[str, str], float] = defaultdict(float)
    for (_campaign, segment, species), kg in by_campaign.items():
        amount = _non_negative_number(kg, "wall deposit kg")
        if amount > _EPS:
            by_segment_species[(segment, species)] += amount
    trace_status = getattr(trace, "wall_deposit_sticking_authority", {}) or {}
    return wall_deposit_sticking_authority_status(
        by_segment_species,
        trace_status if isinstance(trace_status, Mapping) else {},
    )


def _authority_is_authoritative(payload: Mapping[str, Any]) -> bool:
    for key in (
        "authoritative_for_coating",
        "authoritative_for_deposit_mass",
        "authoritative",
    ):
        if key in payload:
            return bool(payload[key])
    return not _payload_has_deposited_species(payload)


def _payload_has_deposited_species(payload: Mapping[str, Any]) -> bool:
    raw = payload.get("deposited_species")
    if isinstance(raw, str):
        return bool(raw)
    if isinstance(raw, (list, tuple)):
        return bool(raw)
    return False


def _plain_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: _plain_value(value)
        for key, value in payload.items()
    }


def _plain_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: _plain_value(item)
            for key, item in value.items()
        }
    if isinstance(value, tuple):
        return tuple(_plain_value(item) for item in value)
    if isinstance(value, list):
        return [_plain_value(item) for item in value]
    return value


def _margin(
    gate: str,
    margin: float,
    threshold: ThresholdSpec,
    observed: float,
    detail: str,
) -> GateMargin:
    return GateMargin(
        gate=gate,
        feasible=margin >= -threshold.tolerance,
        margin=float(margin),
        threshold=threshold,
        observed=float(observed),
        detail=detail,
    )


def physics_constraints_digest(constraints: Any | None = None) -> str:
    """Stable digest for feasibility constraints used in eval cache keys."""

    if constraints is None:
        constraints = PhysicsConstraintSet()
    payload: dict[str, Any] = {
        "version": PHYSICS_GATE_VERSION,
        "class": f"{type(constraints).__module__}.{type(constraints).__qualname__}",
    }
    if isinstance(constraints, PhysicsConstraintSet):
        payload.update({
            "target_species": constraints.target_species,
            "active_gates": constraints.active_gates,
            "residual_species_by_target": dict(constraints.residual_species_by_target),
            "thresholds": tuple(
                _threshold_payload(threshold)
                for threshold in constraints.thresholds
            ),
            "allowable_wall_deposit_kg": tuple(
                {
                    "segment": segment,
                    "species": species,
                    "threshold": _threshold_payload(threshold),
                }
                for (segment, species), threshold
                in sorted(constraints.allowable_wall_deposit_kg.items())
            ),
        })
    canonical = canonical_json_dumps(normalize_canonical_value(payload)).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _threshold_payload(threshold: ThresholdSpec) -> Mapping[str, Any]:
    return {
        "id": threshold.id,
        "value": threshold.value,
        "units": threshold.units,
        "source": threshold.source,
        "source_ref": threshold.source_ref,
        "tolerance": threshold.tolerance,
    }


def _fail_closed(gate: str, threshold: ThresholdSpec, detail: str) -> GateMargin:
    return GateMargin(
        gate=gate,
        feasible=False,
        margin=-math.inf,
        threshold=threshold,
        observed=math.nan,
        detail=f"fail-closed: {detail}",
    )


def _not_applicable(gate: str, threshold: ThresholdSpec, detail: str) -> GateMargin:
    return GateMargin(
        gate=gate,
        feasible=False,
        margin=-math.inf,
        threshold=threshold,
        observed=math.inf,
        detail=detail,
    )


def _required_sequence(trace: Any, field_name: str) -> tuple[Any, ...]:
    value = getattr(trace, field_name, None)
    if value is None:
        raise KeyError(f"trace missing {field_name}")
    if not isinstance(value, (tuple, list)):
        raise TypeError(f"{field_name} must be a sequence")
    return tuple(value)


def _required_mapping(trace: Any, field_name: str) -> Mapping[Any, Any]:
    value = getattr(trace, field_name, None)
    if value is None:
        raise KeyError(f"trace missing {field_name}")
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be a mapping")
    return value


def _stage_species_key(key: Any) -> tuple[int, str]:
    if not isinstance(key, tuple) or len(key) != 2:
        raise TypeError("stage/species key must be a 2-tuple")
    stage, species = key
    return int(stage), str(species)


def _segment_species_key(key: Any) -> tuple[str, str]:
    if not isinstance(key, tuple) or len(key) != 2:
        raise TypeError("segment/species key must be a 2-tuple")
    segment, species = key
    return str(segment), str(species)


def _campaign_name(snapshot: Any) -> str:
    campaign = getattr(snapshot, "campaign", None)
    if campaign is None:
        raise KeyError("snapshot missing campaign")
    return str(getattr(campaign, "name", campaign))


def _knudsen_segment_values(summary: Mapping[Any, Any]) -> list[tuple[str, float, str]]:
    values: list[tuple[str, float, str]] = []
    segments = summary.get("segments")
    if not segments:
        return values
    if not isinstance(segments, (tuple, list)):
        raise TypeError("knudsen segments must be a sequence")
    for segment in segments:
        if not isinstance(segment, Mapping):
            raise TypeError("knudsen segment must be a mapping")
        name = str(segment.get("name", "segment"))
        if "knudsen_number" not in segment:
            raise KeyError(f"knudsen segment {name} missing knudsen_number")
        if "regime" not in segment:
            raise KeyError(f"knudsen segment {name} missing regime")
        values.append((
            name,
            _finite_number(segment["knudsen_number"], f"{name}.knudsen_number"),
            str(segment["regime"]).strip().lower(),
        ))
    return values


def _non_negative_number(value: Any, name: str) -> float:
    amount = _finite_number(value, name)
    if amount < -_EPS:
        raise ValueError(f"{name} must be non-negative")
    return max(0.0, amount)


def _finite_number(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise TypeError(f"{name} must be numeric")
    try:
        amount = float(value)
    except (TypeError, ValueError) as exc:
        raise TypeError(f"{name} must be numeric") from exc
    if not math.isfinite(amount):
        raise ValueError(f"{name} must be finite")
    return amount


def _optional_finite_number(value: Any, name: str) -> float:
    if value is None:
        return 0.0
    return _finite_number(value, name)
