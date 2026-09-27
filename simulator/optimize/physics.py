"""Hard physics-feasibility gates over a completed PhysicsTrace."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field, replace
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
# v5: coating and Knudsen transport retain signed continuous margins without
# Boolean exclusion, so cached v4 feasibility verdicts cannot be reused.
# v6: predicted upstream coating, including flagged quantities, is a hard
# no-coating violation; refused wall quantities are unavailable.
# v7: d-045 bounds upstream wall deposition by feedstock charge mass and
# bounds refused trace species by their vapour flux instead of pricing them as
# zero.
PHYSICS_GATE_VERSION = "physics-feasibility-v7-d045-upstream-fraction"
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
# Round-off guard only. The d-045 physical fraction below determines whether a
# non-zero upstream deposit violates the no-coating gate.
COATING_POSITIVE_DEPOSIT_TOLERANCE_KG_PER_CAMPAIGN = 1.0e-12
# d-045 (2026-09-26): the physical no-coating envelope is
# deposit_kg_per_campaign <= 5e-4 * m_charge_kg. The fraction is dimensionless.
MAX_UPSTREAM_WALL_DEPOSIT_FRACTION_PER_CAMPAIGN = 5.0e-4


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


def _d045_upstream_wall_deposit_threshold() -> ThresholdSpec:
    return ThresholdSpec(
        id="coating_max_upstream_wall_deposit_fraction_per_campaign",
        value=MAX_UPSTREAM_WALL_DEPOSIT_FRACTION_PER_CAMPAIGN,
        units="fraction",
        source="engineering_envelope",
        source_ref=(
            "d-045 (2026-09-26): upstream wall deposit / feedstock charge "
            "mass per campaign"
        ),
    )


@dataclass(frozen=True)
class GateMargin:
    gate: str
    feasible: bool
    margin: float
    threshold: ThresholdSpec
    observed: float | None
    detail: str
    status: str = "available"
    authoritative: bool = True
    output_status: str = "authoritative"
    status_reason: str = ""
    status_payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        status_payload = _plain_payload(self.status_payload)
        object.__setattr__(
            self,
            "status_payload",
            status_payload,
        )
        object.__setattr__(
            self,
            "feasible",
            _normalized_gate_margin_feasibility(
                self.gate,
                self.feasible,
                self.margin,
                self.threshold,
                status_payload,
            ),
        )


def _normalized_gate_margin_feasibility(
    gate: str,
    feasible: bool,
    margin: float,
    threshold: ThresholdSpec,
    status_payload: Mapping[str, Any],
) -> bool:
    if gate != "coating":
        return bool(feasible)
    verdict = status_payload.get("coating_verdict")
    if verdict in {"violated", "unavailable"}:
        return False
    if (
        margin == -math.inf
        or status_payload.get("coating_constraint_mode")
        == "no_unqualified_deposition"
        or threshold.id == "coating_max_unqualified_deposit_kg_per_campaign"
    ):
        return bool(feasible)
    return True


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
            _d045_upstream_wall_deposit_threshold(),
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
                "coating",
                (
                    f"{_d045_upstream_wall_deposit_threshold().id}="
                    f"{MAX_UPSTREAM_WALL_DEPOSIT_FRACTION_PER_CAMPAIGN:g}"
                ),
                "engineering_envelope",
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
        base_trace = getattr(trace, "trace", None) or trace
        missing_report = object()
        report = getattr(trace, "wall_fouling_report", missing_report)
        if report is missing_report:
            report = getattr(base_trace, "wall_fouling_report", missing_report)
        if report is not missing_report:
            if isinstance(report, Mapping):
                runtime_diagnostics = _coating_runtime_diagnostics(trace)
                report_diagnostics = report.get("coating_diagnostics")
                merged_diagnostics = dict(
                    report_diagnostics
                    if isinstance(report_diagnostics, Mapping)
                    else {}
                )
                for key in (
                    "upstream_hot_wall_findings",
                    "silica_exposed_to_alkali_findings",
                ):
                    merged_diagnostics[key] = [
                        *(
                            report_diagnostics.get(key, ())
                            if isinstance(report_diagnostics, Mapping)
                            and isinstance(
                                report_diagnostics.get(key, ()),
                                (tuple, list),
                            )
                            else ()
                        ),
                        *runtime_diagnostics[key],
                    ]
                report = {
                    **report,
                    "coating_diagnostics": merged_diagnostics,
                }
            return self.coating_from_fouling_report(report)
        try:
            snapshots = _required_sequence(base_trace, "snapshots")
            deltas = _required_sequence(
                base_trace,
                "wall_deposit_by_segment_species_delta",
            )
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
            has_wall_deposit = any(
                kg > COATING_POSITIVE_DEPOSIT_TOLERANCE_KG_PER_CAMPAIGN
                for kg in by_campaign.values()
            )
            authority = _coating_authority_status(base_trace, by_campaign)
            authoritative = _authority_is_authoritative(authority)
            diagnostics = _coating_runtime_diagnostics(trace)
            deposit_records = [
                {
                    "campaign": campaign,
                    "segment": segment,
                    "species": species,
                    "deposit_kg_per_campaign": float(kg),
                }
                for (campaign, segment, species), kg in sorted(by_campaign.items())
                if kg > COATING_POSITIVE_DEPOSIT_TOLERANCE_KG_PER_CAMPAIGN
            ]
            reasons = _coating_violation_reasons(
                authority=authority,
                deposit_records=deposit_records,
                diagnostics=diagnostics,
            )
            unavailable_reason = _coating_wall_quantity_unavailable(authority)
            common_payload = {
                **authority,
                "constraint_mode": "continuous",
                "coating_positive_deposit_tolerance_kg_per_campaign": (
                    COATING_POSITIVE_DEPOSIT_TOLERANCE_KG_PER_CAMPAIGN
                ),
                "coating_violation_reasons": reasons,
            }
            if unavailable_reason:
                return GateMargin(
                    gate="coating",
                    feasible=False,
                    margin=-math.inf,
                    threshold=self.coating_min_campaigns_to_resinter,
                    observed=None,
                    detail=f"coating unavailable: {unavailable_reason}",
                    status="unavailable",
                    authoritative=False,
                    output_status=str(
                        authority.get("output_status", "unavailable")
                    ),
                    status_reason=unavailable_reason,
                    status_payload={
                        **common_payload,
                        "coating_verdict": "unavailable",
                        "coating_unavailable_reason": unavailable_reason,
                    },
                )
            zone_by_segment: Mapping[Any, Any] | None = None
            if has_wall_deposit:
                zone_by_segment = getattr(base_trace, "wall_zone_by_segment", None)
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
            for (campaign, segment, species), kg in sorted(by_campaign.items()):
                if kg <= COATING_POSITIVE_DEPOSIT_TOLERANCE_KG_PER_CAMPAIGN:
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
            if math.isinf(worst_margin):
                worst_margin = math.inf
            detail = (
                worst_detail
                if worst_detail == "no wall deposit"
                else f"reported-only: {worst_detail}"
            )
            if reasons:
                detail = (
                    "coating constraint violated: "
                    f"{_coating_reason_summary(reasons)}; {detail}"
                )
                if not authoritative:
                    detail = f"non-authoritative prediction: {detail}"
            elif not authoritative:
                detail = (
                    "non-authoritative: grounded coating criterion not enforced; "
                    f"{detail}"
                )
            common_payload["coating_verdict"] = "violated" if reasons else "clear"
            return GateMargin(
                gate="coating",
                feasible=not reasons,
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
                status_payload=common_payload,
            )
        except (KeyError, TypeError, ValueError) as exc:
            return _fail_closed("coating", self.coating_min_campaigns_to_resinter, str(exc))

    def coating_from_fouling_report(
        self,
        report: Any,
    ) -> GateMargin:
        """Classify the runner's worst-segment lifespan verdict.

        Authority controls the label only. Predicted upstream deposition and
        upstream chemistry findings remain hard no-coating violations; a
        refused wall quantity is unavailable rather than a zero.
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
        constraint_mode = report.get("coating_constraint_mode")
        threshold_is_unqualified = False
        if "resinter_threshold_kg" in report:
            raw_threshold = report["resinter_threshold_kg"]
            if raw_threshold is None:
                threshold_is_unqualified = True
            elif isinstance(raw_threshold, bool) or not isinstance(
                raw_threshold, int | float
            ):
                raise CoatingFeasibilityReportError(
                    "wall-fouling resinter_threshold_kg must be numeric or null"
                )
            else:
                threshold = float(raw_threshold)
                threshold_is_unqualified = (
                    not math.isfinite(threshold) or threshold <= 0.0
                )
        authority_payload = _coating_report_authority(report)
        diagnostics = _coating_runtime_diagnostics(report)
        report_diagnostics = report.get("coating_diagnostics")
        if isinstance(report_diagnostics, Mapping):
            for key in (
                "upstream_hot_wall_findings",
                "silica_exposed_to_alkali_findings",
            ):
                diagnostics[key].extend(
                    _plain_value(report_diagnostics.get(key, ()))
                    if isinstance(report_diagnostics.get(key, ()), (tuple, list))
                    else []
                )
        deposit_records = _coating_report_deposit_records(report)
        physical_fraction_mode = (
            report.get("coating_constraint_mode")
            == "upstream_deposit_fraction"
            or "feedstock_charge_mass_kg" in report
        )
        if physical_fraction_mode:
            return _coating_from_upstream_deposit_fraction_report(
                report=report,
                authority=authority_payload,
                diagnostics=diagnostics,
                deposit_records=deposit_records,
                output_status=output_status,
                status_reason=status_reason,
            )
        unavailable_reason = _coating_wall_quantity_unavailable(
            report,
            authority_payload,
            include_coverage_unknown=True,
        )
        if constraint_mode == "no_unqualified_deposition" or threshold_is_unqualified:
            if (
                constraint_mode == "no_unqualified_deposition"
                and report.get("coating_constraint_authoritative") is not True
            ):
                raise CoatingFeasibilityReportError(
                    "no-unqualified-deposition constraint must be authoritative"
                )
            raw_rate = report.get(
                "unqualified_deposition_rate_kg_per_campaign",
                report.get("wall_deposit_kg_per_campaign"),
            )
            if isinstance(raw_rate, bool) or not isinstance(raw_rate, int | float):
                raise CoatingFeasibilityReportError(
                    "unqualified deposition rate must be numeric"
                )
            rate = float(raw_rate)
            if not math.isfinite(rate) or rate < 0.0:
                raise CoatingFeasibilityReportError(
                    "unqualified deposition rate must be finite and non-negative"
                )
            reasons = _coating_violation_reasons(
                authority=authority_payload,
                deposit_records=deposit_records,
                aggregate_deposit_kg=rate,
                diagnostics=diagnostics,
                explicit_reasons=report.get("coating_violation_reasons", ()),
            )
            threshold = ThresholdSpec(
                id="coating_max_unqualified_deposit_kg_per_campaign",
                value=0.0,
                units="kg/campaign",
                source="engineering_envelope",
                source_ref=(
                    "require_coating_gate with no sourced resinter capacity"
                ),
            )
            coating_authoritative = (
                _authority_is_authoritative(authority_payload)
                if "sticking_alpha_authority" in report
                else bool(report.get("authoritative_for_resinter", True))
            )
            constraint_authoritative = (
                report.get("coating_constraint_authoritative") is True
            )
            if unavailable_reason:
                return GateMargin(
                    gate="coating",
                    feasible=False,
                    margin=-math.inf,
                    threshold=threshold,
                    observed=None,
                    detail=f"coating unavailable: {unavailable_reason}",
                    status="unavailable",
                    authoritative=False,
                    output_status=output_status,
                    status_reason=unavailable_reason,
                    status_payload={
                        **report,
                        "coating_constraint_mode": "no_unqualified_deposition",
                        "coating_constraint_authoritative": False,
                        "constraint_mode": "continuous",
                        "coating_verdict": "unavailable",
                        "coating_unavailable_reason": unavailable_reason,
                        "coating_violation_reasons": reasons,
                        "coating_positive_deposit_tolerance_kg_per_campaign": (
                            COATING_POSITIVE_DEPOSIT_TOLERANCE_KG_PER_CAMPAIGN
                        ),
                    },
                )
            violated = bool(reasons)
            detail = (
                "coating constraint violated: "
                f"{_coating_reason_summary(reasons)}; "
                f"deposit_rate={rate:.6g} kg/campaign"
                if violated
                else (
                    "no positive upstream wall deposition or coating finding; "
                    f"deposit_rate={rate:.6g} kg/campaign"
                )
            )
            if violated and not coating_authoritative:
                detail = f"non-authoritative prediction: {detail}"
            return GateMargin(
                gate="coating",
                feasible=not violated,
                margin=-rate if math.isfinite(rate) else -math.inf,
                threshold=threshold,
                observed=rate,
                detail=detail,
                status="available" if coating_authoritative else "warning",
                authoritative=coating_authoritative,
                output_status=output_status,
                status_reason=(
                    ""
                    if coating_authoritative
                    else status_reason
                ),
                status_payload={
                    **report,
                    "coating_constraint_mode": "no_unqualified_deposition",
                    "coating_constraint_authoritative": constraint_authoritative,
                    "constraint_mode": "continuous",
                    "coating_verdict": "violated" if violated else "clear",
                    "coating_violation_reasons": reasons,
                    "coating_positive_deposit_tolerance_kg_per_campaign": (
                        COATING_POSITIVE_DEPOSIT_TOLERANCE_KG_PER_CAMPAIGN
                    ),
                },
            )
        observed_field = (
            "campaigns_to_resinter_worst_segment"
            if "campaigns_to_resinter_worst_segment" in report
            else "campaigns_to_resinter_total"
        )
        raw_observed = report[observed_field]
        if isinstance(raw_observed, bool) or not isinstance(raw_observed, int | float):
            raise CoatingFeasibilityReportError(
                f"wall-fouling {observed_field} must be numeric"
            )
        observed = float(raw_observed)
        if math.isnan(observed) or observed < 0.0:
            raise CoatingFeasibilityReportError(
                f"wall-fouling {observed_field} must be non-negative"
            )
        margin = observed - self.coating_min_campaigns_to_resinter.value
        raw_rate = report.get(
            "unqualified_deposition_rate_kg_per_campaign",
            report.get("wall_deposit_kg_per_campaign"),
        )
        aggregate_deposit = None
        if raw_rate is not None:
            if isinstance(raw_rate, bool) or not isinstance(raw_rate, int | float):
                raise CoatingFeasibilityReportError(
                    "wall deposit rate must be numeric"
                )
            aggregate_deposit = float(raw_rate)
            if not math.isfinite(aggregate_deposit) or aggregate_deposit < 0.0:
                raise CoatingFeasibilityReportError(
                    "wall deposit rate must be finite and non-negative"
                )
        reasons = _coating_violation_reasons(
            authority=authority_payload,
            deposit_records=deposit_records,
            aggregate_deposit_kg=aggregate_deposit,
            diagnostics=diagnostics,
            explicit_reasons=report.get("coating_violation_reasons", ()),
        )
        coating_authoritative = (
            _authority_is_authoritative(authority_payload)
            if "sticking_alpha_authority" in report
            else authoritative
        )
        if unavailable_reason:
            return GateMargin(
                gate="coating",
                feasible=False,
                margin=-math.inf,
                threshold=self.coating_min_campaigns_to_resinter,
                observed=None,
                detail=f"coating unavailable: {unavailable_reason}",
                status="unavailable",
                authoritative=False,
                output_status=output_status,
                status_reason=unavailable_reason,
                status_payload={
                    **report,
                    "constraint_mode": "continuous",
                    "coating_verdict": "unavailable",
                    "coating_unavailable_reason": unavailable_reason,
                    "coating_violation_reasons": reasons,
                    "coating_positive_deposit_tolerance_kg_per_campaign": (
                        COATING_POSITIVE_DEPOSIT_TOLERANCE_KG_PER_CAMPAIGN
                    ),
                },
            )
        violated = bool(reasons)
        if authoritative:
            detail = (
                f"continuous runner wall-fouling {observed_field}={observed:.6g}; "
                f"minimum={self.coating_min_campaigns_to_resinter.value:.6g}"
            )
        else:
            detail = (
                "non-authoritative: coating feasibility unconstrained; "
                f"output_status={output_status}; "
                f"status_reason={status_reason}"
            )
        if violated:
            detail = (
                "coating constraint violated: "
                f"{_coating_reason_summary(reasons)}; {detail}"
            )
            if not coating_authoritative:
                detail = f"non-authoritative prediction: {detail}"
        return GateMargin(
            gate="coating",
            feasible=not violated,
            margin=margin,
            threshold=self.coating_min_campaigns_to_resinter,
            observed=observed,
            detail=detail,
            status="available" if authoritative else "warning",
            authoritative=authoritative,
            output_status=output_status,
            status_reason="" if authoritative else status_reason,
            status_payload={
                **report,
                "constraint_mode": "continuous",
                "coating_verdict": "violated" if violated else "clear",
                "coating_violation_reasons": reasons,
                "coating_positive_deposit_tolerance_kg_per_campaign": (
                    COATING_POSITIVE_DEPOSIT_TOLERANCE_KG_PER_CAMPAIGN
                ),
            },
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
                    if margin < worst_margin:
                        worst_margin = margin
                        worst_kn = kn
                        worst_detail = (
                            f"snapshot {index} {label} Kn={kn:.6g} regime={regime}"
                        )
            result = _margin(
                "knudsen_viscous",
                worst_margin,
                self.knudsen_max,
                worst_kn,
                worst_detail,
            )
            return replace(
                result,
                feasible=True,
                detail=f"continuous transport constraint: {result.detail}",
                status_payload={
                    **dict(result.status_payload),
                    "constraint_mode": "continuous",
                },
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
    # Core mol fields: None is incomplete provenance, never silent 0.0 product.
    # A claimed completeness_fraction without mol evidence is refused.
    product_mol = _optional_finite_number(
        payload.get("product_target_equiv_mol"),
        f"{target}.product_target_equiv_mol",
    )
    residual_mol = _optional_finite_number(
        payload.get("residual_target_equiv_mol"),
        f"{target}.residual_target_equiv_mol",
    )
    denominator_mol = _optional_finite_number(
        payload.get("denominator_target_equiv_mol"),
        f"{target}.denominator_target_equiv_mol",
    )
    wall_mol = _optional_finite_number(
        payload.get("wall_deposit_target_equiv_mol"),
        f"{target}.wall_deposit_target_equiv_mol",
    )
    reagent_mol = _optional_finite_number(
        payload.get("reagent_target_equiv_mol"),
        f"{target}.reagent_target_equiv_mol",
    )
    gross_mol = _optional_finite_number(
        payload.get("gross_product_target_equiv_mol"),
        f"{target}.gross_product_target_equiv_mol",
    )
    feedstock_recovered_mol = _optional_finite_number(
        payload.get("feedstock_recovered_reagent_target_equiv_mol"),
        f"{target}.feedstock_recovered_reagent_target_equiv_mol",
    )
    credit_line_mol = _optional_finite_number(
        payload.get("credit_line_reagent_target_equiv_mol"),
        f"{target}.credit_line_reagent_target_equiv_mol",
    )
    external_additive_mol = _optional_finite_number(
        payload.get("external_additive_reagent_target_equiv_mol"),
        f"{target}.external_additive_reagent_target_equiv_mol",
    )
    missing_core = [
        name
        for name, value in (
            ("product_target_equiv_mol", product_mol),
            ("residual_target_equiv_mol", residual_mol),
            ("denominator_target_equiv_mol", denominator_mol),
        )
        if value is None
    ]
    reason = str(payload.get("reason") or "unknown: no result")
    if missing_core:
        # Refuse the completeness claim; do not assert zero product for a
        # target the fraction (if any) said was extracted.
        fraction = None
        reason = (
            "incomplete extraction-completeness payload: missing mol fields "
            f"{missing_core}; unknown mol is not zero product"
        )
    return TargetExtractionCompleteness(
        target,
        fraction,
        # Honest fields: missing core mols stay None (not fabricated 0.0).
        product_mol,
        residual_mol,
        denominator_mol,
        reason,
        wall_deposit_target_equiv_mol=0.0 if wall_mol is None else wall_mol,
        reagent_target_equiv_mol=0.0 if reagent_mol is None else reagent_mol,
        gross_product_target_equiv_mol=0.0 if gross_mol is None else gross_mol,
        contract_id=str(payload.get("contract_id") or ""),
        feedstock_recovered_reagent_target_equiv_mol=(
            0.0 if feedstock_recovered_mol is None else feedstock_recovered_mol
        ),
        credit_line_reagent_target_equiv_mol=(
            0.0 if credit_line_mol is None else credit_line_mol
        ),
        external_additive_reagent_target_equiv_mol=(
            0.0 if external_additive_mol is None else external_additive_mol
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
        by_segment_species[(segment, species)] += amount
    trace_status = getattr(trace, "wall_deposit_sticking_authority", {}) or {}
    return wall_deposit_sticking_authority_status(
        by_segment_species,
        trace_status if isinstance(trace_status, Mapping) else {},
    )


def _coating_runtime_diagnostics(value: Any) -> dict[str, Any]:
    """Collect upstream coating findings without changing PhysicsTrace shape."""

    upstream_hot_wall_findings: list[Mapping[str, Any]] = []
    silica_exposed_to_alkali_findings: list[Mapping[str, Any]] = []
    sources: list[Any] = []
    seen: set[int] = set()

    def add_source(source: Any) -> None:
        if source is None or id(source) in seen:
            return
        seen.add(id(source))
        sources.append(source)

    add_source(value)
    for owner in (
        value,
        getattr(value, "trace", None),
        getattr(value, "original_trace", None),
        getattr(value, "simulator", None),
    ):
        if isinstance(owner, Mapping):
            add_source(owner.get("coating_diagnostics"))
            add_source(owner.get("stage3_route_diagnostic"))
        add_source(getattr(owner, "coating_diagnostics", None))
        for attr in ("condensation_model", "_condensation_model"):
            model = getattr(owner, attr, None)
            add_source(getattr(model, "last_cold_spot_diagnostic", None))
            add_source(getattr(model, "cold_spot_history", None))
            add_source(getattr(model, "last_stage3_route_diagnostic", None))
            add_source(getattr(model, "operating_history", None))
        add_source(getattr(owner, "run_metadata", None))

    def append_findings(target: list[Mapping[str, Any]], raw: Any) -> None:
        if isinstance(raw, Mapping):
            target.append(dict(raw))
        elif isinstance(raw, (tuple, list)):
            target.extend(
                dict(item)
                for item in raw
                if isinstance(item, Mapping)
            )

    for source in sources:
        if isinstance(source, Mapping):
            append_findings(
                upstream_hot_wall_findings,
                source.get("upstream_hot_wall_findings"),
            )
            append_findings(
                silica_exposed_to_alkali_findings,
                source.get("silica_exposed_to_alkali_findings"),
            )
            route = source.get("stage3_route_diagnostic")
            if isinstance(route, Mapping):
                append_findings(
                    silica_exposed_to_alkali_findings,
                    [
                        finding
                        for finding in route.get("findings", ())
                        if isinstance(finding, Mapping)
                        and finding.get("key") == "silica_exposed_to_alkali"
                    ],
                )
                if "silica_exposed_to_alkali" in (route.get("finding_keys") or ()):
                    silica_exposed_to_alkali_findings.append({
                        "key": "silica_exposed_to_alkali",
                    })
            if source.get("key") == "silica_exposed_to_alkali":
                silica_exposed_to_alkali_findings.append(dict(source))
            if source.get("finding_keys") and "silica_exposed_to_alkali" in source.get(
                "finding_keys", ()
            ):
                silica_exposed_to_alkali_findings.append({
                    "key": "silica_exposed_to_alkali",
                })
            if source.get("stage3_route") and source.get("findings"):
                append_findings(
                    silica_exposed_to_alkali_findings,
                    [
                        finding
                        for finding in source.get("findings", ())
                        if isinstance(finding, Mapping)
                        and finding.get("key") == "silica_exposed_to_alkali"
                    ],
                )
        elif isinstance(source, (tuple, list)):
            for item in source:
                if isinstance(item, Mapping):
                    append_findings(
                        upstream_hot_wall_findings,
                        item.get("upstream_hot_wall_findings"),
                    )
                    if item.get("key") == "silica_exposed_to_alkali":
                        silica_exposed_to_alkali_findings.append(dict(item))
                    if item.get("stage3_route_diagnostic"):
                        add_source(item["stage3_route_diagnostic"])

    def unique(findings: list[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
        result: list[Mapping[str, Any]] = []
        keys: set[str] = set()
        for finding in findings:
            plain = _plain_value(finding)
            key = repr(plain)
            if key in keys:
                continue
            keys.add(key)
            result.append(plain)
        return result

    return {
        "upstream_hot_wall_findings": unique(upstream_hot_wall_findings),
        "silica_exposed_to_alkali_findings": unique(
            silica_exposed_to_alkali_findings
        ),
    }


def _coating_authority_flags(authority: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "code",
        "output_status",
        "authoritative_for_deposit_mass",
        "authoritative_for_coating",
        "wall_saturation_pressure_extrapolations_by_species",
        "wall_saturation_pressure_refusals_by_species",
        "wall_saturation_pressure_refused_species",
        "out_of_domain_alpha_species",
        "uncertified_alpha_species",
        "codes",
        "not_applicable_carrier_species",
        "not_applicable_by_species",
    )
    return {
        key: _plain_value(authority[key])
        for key in keys
        if key in authority
    }


def _coating_reason_record(
    reason: str,
    *,
    authority: Mapping[str, Any],
    **payload: Any,
) -> dict[str, Any]:
    return {
        "reason": reason,
        **payload,
        "authority": _plain_value(authority),
        "flags": _coating_authority_flags(authority),
    }


def _coating_from_upstream_deposit_fraction_report(
    *,
    report: Mapping[str, Any],
    authority: Mapping[str, Any],
    diagnostics: Mapping[str, Any],
    deposit_records: list[Mapping[str, Any]],
    output_status: str,
    status_reason: str,
) -> GateMargin:
    """Apply d-045 to a report with an explicit feedstock charge basis."""

    threshold = _d045_upstream_wall_deposit_threshold()
    charge_mass_kg = _coating_report_feedstock_charge_mass(report)
    limit_kg = threshold.value * charge_mass_kg
    refused_species = _coating_wall_refused_species(report, authority)
    not_applicable_species, not_applicable_by_species = (
        _coating_not_applicable_carriers(authority)
    )
    certain_deposit_kg, certain_records = _coating_certain_upstream_deposit(
        report,
        deposit_records,
    )
    refused_bounds = _coating_refused_flux_bounds(report, refused_species)
    warning_flags: list[dict[str, Any]] = []
    violation_reasons: list[dict[str, Any]] = []

    for record in certain_records:
        amount = float(record["deposit_kg_per_campaign"])
        if (
            amount <= limit_kg
            and _coating_quantity_is_flagged(record, authority)
        ):
            warning_flags.append(
                _coating_reason_record(
                    "flagged_upstream_wall_deposit_below_threshold",
                    authority=authority,
                    **{
                        key: _plain_value(record[key])
                        for key in (
                            "campaign",
                            "segment",
                            "species",
                            "zone",
                        )
                        if key in record
                    },
                    deposit_kg_per_campaign=amount,
                    threshold_kg_per_campaign=limit_kg,
                )
            )

    for finding in diagnostics.get("upstream_hot_wall_findings", ()):
        warning_flags.append(
            _coating_reason_record(
                "upstream_hot_wall_supersaturation",
                authority=authority,
                finding=_plain_value(finding),
            )
        )

    for species in refused_species:
        if species in refused_bounds:
            warning_flags.append(
                _coating_reason_record(
                    "wall_saturation_pressure_refused_bounded",
                    authority=authority,
                    species=species,
                    flux_upper_bound_kg_per_campaign=refused_bounds[species],
                    derivation=(
                        "a refused species' wall deposit cannot exceed its "
                        "total vapour flux reaching the segment in the "
                        "campaign"
                    ),
                )
            )

    for finding in diagnostics.get("silica_exposed_to_alkali_findings", ()):
        violation_reasons.append(
            _coating_reason_record(
                "silica_exposed_to_alkali",
                authority=authority,
                finding=_plain_value(finding),
            )
        )

    for explicit in report.get("coating_violation_reasons", ()):
        if isinstance(explicit, Mapping):
            reason = dict(_plain_value(explicit))
            reason.setdefault("authority", _plain_value(authority))
            reason.setdefault("flags", _coating_authority_flags(authority))
            violation_reasons.append(reason)

    non_refusal_unavailable = _coating_fraction_wall_quantity_unavailable(
        report,
        authority,
        refused_species,
    )
    missing_bounds = [
        species for species in refused_species if species not in refused_bounds
    ]
    if non_refusal_unavailable:
        return _coating_fraction_unavailable_margin(
            report=report,
            authority=authority,
            threshold=threshold,
            output_status=output_status,
            reason=non_refusal_unavailable,
            violation_reasons=violation_reasons,
            warning_flags=warning_flags,
            refused_species=refused_species,
            not_applicable_species=not_applicable_species,
            not_applicable_by_species=not_applicable_by_species,
            refused_bounds=refused_bounds,
            charge_mass_kg=charge_mass_kg,
            limit_kg=limit_kg,
            certain_deposit_kg=certain_deposit_kg,
        )
    if missing_bounds:
        return _coating_fraction_unavailable_margin(
            report=report,
            authority=authority,
            threshold=threshold,
            output_status=output_status,
            reason=(
                "wall saturation pressure refused for "
                + ", ".join(missing_bounds)
                + "; vapour flux upper bound unavailable"
            ),
            violation_reasons=violation_reasons,
            warning_flags=warning_flags,
            refused_species=refused_species,
            not_applicable_species=not_applicable_species,
            not_applicable_by_species=not_applicable_by_species,
            refused_bounds=refused_bounds,
            charge_mass_kg=charge_mass_kg,
            limit_kg=limit_kg,
            certain_deposit_kg=certain_deposit_kg,
        )

    refused_bound_kg = sum(refused_bounds.get(species, 0.0) for species in refused_species)
    upper_bound_kg = certain_deposit_kg + refused_bound_kg
    if refused_species and upper_bound_kg > limit_kg:
        return _coating_fraction_unavailable_margin(
            report=report,
            authority=authority,
            threshold=threshold,
            output_status=output_status,
            reason=(
                "certain upstream deposit plus refused-species vapour-flux "
                f"upper bound {upper_bound_kg:.6g} kg/campaign exceeds "
                f"d-045 limit {limit_kg:.6g} kg/campaign"
            ),
            violation_reasons=violation_reasons,
            warning_flags=warning_flags,
            refused_species=refused_species,
            not_applicable_species=not_applicable_species,
            not_applicable_by_species=not_applicable_by_species,
            refused_bounds=refused_bounds,
            charge_mass_kg=charge_mass_kg,
            limit_kg=limit_kg,
            certain_deposit_kg=certain_deposit_kg,
            upper_bound_kg=upper_bound_kg,
        )

    if not refused_species and certain_deposit_kg > limit_kg:
        reason_payload = {
            key: _plain_value(certain_records[0][key])
            for key in ("campaign", "segment", "species", "zone")
            if len(certain_records) == 1 and key in certain_records[0]
        }
        violation_reasons.append(
            _coating_reason_record(
                "upstream_wall_deposit_fraction_exceeded",
                authority=authority,
                **reason_payload,
                deposit_kg_per_campaign=certain_deposit_kg,
                threshold_kg_per_campaign=limit_kg,
                feedstock_charge_mass_kg=charge_mass_kg,
                deposit_fraction=certain_deposit_kg / charge_mass_kg,
            )
        )

    if not violation_reasons and diagnostics.get(
        "silica_exposed_to_alkali_findings"
    ):
        # Keep the hard silica rule explicit if a future reason filter changes
        # the list above; zero deposited mass does not waive this finding.
        violation_reasons.extend(
            _coating_reason_record(
                "silica_exposed_to_alkali",
                authority=authority,
                finding=_plain_value(finding),
            )
            for finding in diagnostics["silica_exposed_to_alkali_findings"]
        )

    unique_warnings = _unique_coating_records(warning_flags)
    unique_reasons = _unique_coating_records(violation_reasons)
    feasible = not unique_reasons
    observed_kg = upper_bound_kg
    observed_fraction = observed_kg / charge_mass_kg
    coating_authoritative = _authority_is_authoritative(authority)
    status = "warning" if unique_warnings or not coating_authoritative else "available"
    detail = (
        "coating constraint violated: "
        f"{_coating_reason_summary(unique_reasons)}; "
        f"upstream deposit upper bound={observed_kg:.6g} kg/campaign, "
        f"d-045 limit={limit_kg:.6g} kg/campaign"
        if unique_reasons
        else (
            "d-045 upstream wall-deposit upper bound="
            f"{observed_kg:.6g} kg/campaign <= {limit_kg:.6g} kg/campaign"
        )
    )
    payload = _coating_fraction_payload(
        report=report,
        authority=authority,
        threshold=threshold,
        violation_reasons=unique_reasons,
        warning_flags=unique_warnings,
        refused_species=refused_species,
        not_applicable_species=not_applicable_species,
        not_applicable_by_species=not_applicable_by_species,
        refused_bounds=refused_bounds,
        charge_mass_kg=charge_mass_kg,
        limit_kg=limit_kg,
        certain_deposit_kg=certain_deposit_kg,
        upper_bound_kg=upper_bound_kg,
        verdict="violated" if unique_reasons else "clear",
        constraint_authoritative=True,
    )
    return GateMargin(
        gate="coating",
        feasible=feasible,
        margin=threshold.value - observed_fraction,
        threshold=threshold,
        observed=observed_fraction,
        detail=detail,
        status=status,
        authoritative=coating_authoritative,
        output_status=output_status,
        status_reason=(
            ""
            if coating_authoritative
            else status_reason
            if "resinter threshold" not in status_reason.lower()
            else ""
        ),
        status_payload=payload,
    )


def _coating_fraction_unavailable_margin(
    *,
    report: Mapping[str, Any],
    authority: Mapping[str, Any],
    threshold: ThresholdSpec,
    output_status: str,
    reason: str,
    violation_reasons: list[dict[str, Any]],
    warning_flags: list[dict[str, Any]],
    refused_species: tuple[str, ...],
    not_applicable_species: tuple[str, ...],
    not_applicable_by_species: Mapping[str, Any],
    refused_bounds: Mapping[str, float],
    charge_mass_kg: float,
    limit_kg: float,
    certain_deposit_kg: float,
    upper_bound_kg: float | None = None,
) -> GateMargin:
    payload = _coating_fraction_payload(
        report=report,
        authority=authority,
        threshold=threshold,
        violation_reasons=_unique_coating_records(violation_reasons),
        warning_flags=_unique_coating_records(warning_flags),
        refused_species=refused_species,
        not_applicable_species=not_applicable_species,
        not_applicable_by_species=not_applicable_by_species,
        refused_bounds=refused_bounds,
        charge_mass_kg=charge_mass_kg,
        limit_kg=limit_kg,
        certain_deposit_kg=certain_deposit_kg,
        upper_bound_kg=upper_bound_kg,
        verdict="unavailable",
        constraint_authoritative=False,
    )
    payload["coating_unavailable_reason"] = reason
    payload["coating_excluded_species"] = [
        {
            "species": species,
            "status": "unavailable",
            "reason": "wall_saturation_pressure_refused",
        }
        for species in refused_species
    ]
    return GateMargin(
        gate="coating",
        feasible=False,
        margin=-math.inf,
        threshold=threshold,
        observed=None,
        detail=f"coating unavailable: {reason}",
        status="unavailable",
        authoritative=False,
        output_status=output_status,
        status_reason=reason,
        status_payload=payload,
    )


def _coating_fraction_payload(
    *,
    report: Mapping[str, Any],
    authority: Mapping[str, Any],
    threshold: ThresholdSpec,
    violation_reasons: list[dict[str, Any]],
    warning_flags: list[dict[str, Any]],
    refused_species: tuple[str, ...],
    not_applicable_species: tuple[str, ...],
    not_applicable_by_species: Mapping[str, Any],
    refused_bounds: Mapping[str, float],
    charge_mass_kg: float,
    limit_kg: float,
    certain_deposit_kg: float,
    upper_bound_kg: float | None,
    verdict: str,
    constraint_authoritative: bool,
) -> dict[str, Any]:
    payload = {
        **_plain_value(report),
        "coating_constraint_mode": "upstream_deposit_fraction",
        "coating_constraint_authoritative": constraint_authoritative,
        "constraint_mode": "fraction",
        "coating_threshold_fraction": threshold.value,
        "feedstock_charge_mass_kg": charge_mass_kg,
        "upstream_wall_deposit_limit_kg_per_campaign": limit_kg,
        "certain_upstream_deposit_kg_per_campaign": certain_deposit_kg,
        "upstream_wall_deposit_upper_bound_kg_per_campaign": upper_bound_kg,
        "wall_saturation_pressure_refused_species": list(refused_species),
        "wall_saturation_pressure_refused_flux_upper_bounds_kg_per_campaign": {
            str(species): float(bound)
            for species, bound in refused_bounds.items()
        },
        "not_applicable_carrier_species": list(not_applicable_species),
        "not_applicable_by_species": _plain_value(not_applicable_by_species),
        "coating_violation_reasons": violation_reasons,
        "coating_warning_flags": warning_flags,
        "coating_verdict": verdict,
        "coating_positive_deposit_tolerance_kg_per_campaign": (
            COATING_POSITIVE_DEPOSIT_TOLERANCE_KG_PER_CAMPAIGN
        ),
    }
    return payload


def _coating_report_feedstock_charge_mass(report: Mapping[str, Any]) -> float:
    raw = report.get("feedstock_charge_mass_kg")
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        raise CoatingFeasibilityReportError(
            "d-045 coating report requires numeric feedstock_charge_mass_kg"
        )
    mass = float(raw)
    if not math.isfinite(mass) or mass <= 0.0:
        raise CoatingFeasibilityReportError(
            "d-045 feedstock_charge_mass_kg must be finite and positive"
        )
    return mass


def _coating_certain_upstream_deposit(
    report: Mapping[str, Any],
    records: list[Mapping[str, Any]],
) -> tuple[float, list[dict[str, Any]]]:
    total = 0.0
    certain_records: list[dict[str, Any]] = []
    upstream_record_seen = False
    scoped_record_seen = False
    for record in records:
        scope = str(record.get("scope", "upstream"))
        if scope in {"designated_condenser", "condenser"}:
            scoped_record_seen = True
            continue
        upstream_record_seen = True
        raw_amount = record.get(
            "deposit_kg_per_campaign",
            record.get("wall_deposit_kg_per_campaign", record.get("kg", 0.0)),
        )
        try:
            amount = float(raw_amount)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(amount) or amount < 0.0:
            raise CoatingFeasibilityReportError(
                "upstream wall deposit must be finite and non-negative"
            )
        if amount <= COATING_POSITIVE_DEPOSIT_TOLERANCE_KG_PER_CAMPAIGN:
            continue
        item = dict(_plain_value(record))
        item["deposit_kg_per_campaign"] = amount
        certain_records.append(item)
        total += amount
    if not upstream_record_seen and not scoped_record_seen:
        raw_aggregate = report.get(
            "unqualified_deposition_rate_kg_per_campaign",
            report.get("wall_deposit_kg_per_campaign"),
        )
        if raw_aggregate is not None:
            try:
                aggregate = float(raw_aggregate)
            except (TypeError, ValueError) as exc:
                raise CoatingFeasibilityReportError(
                    "wall deposit rate must be numeric"
                ) from exc
            if not math.isfinite(aggregate) or aggregate < 0.0:
                raise CoatingFeasibilityReportError(
                    "wall deposit rate must be finite and non-negative"
                )
            if aggregate > COATING_POSITIVE_DEPOSIT_TOLERANCE_KG_PER_CAMPAIGN:
                certain_records.append({
                    "scope": "upstream",
                    "deposit_kg_per_campaign": aggregate,
                })
                total = aggregate
    return total, certain_records


def _coating_quantity_is_flagged(
    record: Mapping[str, Any],
    authority: Mapping[str, Any],
) -> bool:
    if any(
        bool(record.get(key))
        for key in ("flagged", "extrapolated", "is_extrapolated", "status_bearing")
    ):
        return True
    code = " ".join(
        str(authority.get(key, ""))
        for key in ("code", "output_status", "status", "message")
    ).lower()
    return "extrapolat" in code or "status_bearing" in code


def _coating_wall_refused_species(
    *sources: Mapping[str, Any] | None,
) -> tuple[str, ...]:
    species: set[str] = set()
    for source in sources:
        if not isinstance(source, Mapping):
            continue
        raw_species = source.get("wall_saturation_pressure_refused_species", ())
        if isinstance(raw_species, str):
            species.add(raw_species)
        elif isinstance(raw_species, (tuple, list, set, frozenset)):
            species.update(str(item) for item in raw_species if str(item))
        raw_map = source.get("wall_saturation_pressure_refusals_by_species")
        if isinstance(raw_map, Mapping):
            species.update(str(item) for item in raw_map if str(item))
    return tuple(sorted(species))


def _coating_not_applicable_carriers(
    authority: Mapping[str, Any],
) -> tuple[tuple[str, ...], dict[str, Any]]:
    species: set[str] = set()
    records: dict[str, Any] = {}
    raw_species = authority.get("not_applicable_carrier_species", ())
    if isinstance(raw_species, str):
        species.add(raw_species)
    elif isinstance(raw_species, (tuple, list, set, frozenset)):
        species.update(str(item) for item in raw_species if str(item))
    raw_records = authority.get("not_applicable_by_species")
    if isinstance(raw_records, Mapping):
        for item, record in raw_records.items():
            key = str(item)
            species.add(key)
            records[key] = _plain_value(record)
    carriers = authority.get("vapour_carrier_authority_by_species", {})
    if isinstance(carriers, Mapping):
        for item, record in carriers.items():
            if not isinstance(record, Mapping) or not _is_inapplicable_carrier(record):
                continue
            key = str(item)
            species.add(key)
            records[key] = _plain_value(record)
    return tuple(sorted(species)), records


def _is_inapplicable_carrier(record: Mapping[str, Any]) -> bool:
    if str(record.get("refusal_code", "")) == "inapplicable_by_declared_predicate":
        return True
    extra = record.get("extra")
    evidence = extra.get("applicability_evidence") if isinstance(extra, Mapping) else None
    return (
        isinstance(evidence, Mapping)
        and str(evidence.get("code", evidence.get("refusal_code", "")))
        == "inapplicable_by_declared_predicate"
    )


def _coating_refused_flux_bounds(
    report: Mapping[str, Any],
    refused_species: tuple[str, ...],
) -> dict[str, float]:
    raw = report.get(
        "wall_saturation_pressure_refused_flux_upper_bounds_kg_per_campaign",
        report.get("refused_species_flux_upper_bounds_kg_per_campaign", {}),
    )
    if raw is None:
        return {}
    if isinstance(raw, Mapping):
        entries = raw.items()
    elif isinstance(raw, (tuple, list)):
        entries = (
            (
                item.get("species"),
                item.get(
                    "flux_upper_bound_kg_per_campaign",
                    item.get("bound_kg_per_campaign"),
                ),
            )
            for item in raw
            if isinstance(item, Mapping)
        )
    else:
        raise CoatingFeasibilityReportError(
            "refused species flux upper bounds must be a mapping or records"
        )
    bounds: dict[str, float] = {}
    for raw_species, raw_bound in entries:
        if raw_species is None:
            continue
        if isinstance(raw_bound, bool) or not isinstance(raw_bound, int | float):
            raise CoatingFeasibilityReportError(
                f"refused species flux bound for {raw_species!r} must be numeric"
            )
        bound = float(raw_bound)
        if not math.isfinite(bound) or bound < 0.0:
            raise CoatingFeasibilityReportError(
                f"refused species flux bound for {raw_species!r} must be finite "
                "and non-negative"
            )
        bounds[str(raw_species)] = bound
    # Mutation guard: refused flux bounds must not default to zero.
    return bounds


def _coating_fraction_wall_quantity_unavailable(
    report: Mapping[str, Any],
    authority: Mapping[str, Any],
    refused_species: tuple[str, ...],
) -> str:
    for source in (report, authority):
        if not isinstance(source, Mapping):
            continue
        if source.get("wall_quantity_unavailable") is True:
            if refused_species:
                continue
            return str(
                source.get("status_reason")
                or source.get("message")
                or "wall quantity unavailable"
            )
        code = str(source.get("code", "")).lower()
        if (
            "saturation_pressure_refused" in code
            or "wall_saturation_pressure_refused" in code
        ) and not refused_species:
            return str(
                source.get("status_reason")
                or source.get("message")
                or "wall saturation pressure was refused"
            )
        if code == "wall_deposit_coverage_unknown":
            return str(source.get("message") or "wall deposit quantity is unavailable")
        lowered_reason = str(source.get("status_reason", "")).lower()
        if (
            "wall saturation pressure" in lowered_reason
            and "refus" in lowered_reason
            and not refused_species
        ):
            return str(source.get("status_reason"))
    return ""


def _unique_coating_records(
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in records:
        key = repr(_plain_value(record))
        if key in seen:
            continue
        seen.add(key)
        unique.append(record)
    return unique


def _coating_violation_reasons(
    *,
    authority: Mapping[str, Any],
    deposit_records: Any = (),
    aggregate_deposit_kg: float | None = None,
    diagnostics: Mapping[str, Any] | None = None,
    explicit_reasons: Any = (),
) -> list[dict[str, Any]]:
    reasons: list[dict[str, Any]] = []
    positive_record_seen = False
    scoped_record_seen = False
    for record in deposit_records if isinstance(deposit_records, (tuple, list)) else ():
        if not isinstance(record, Mapping):
            continue
        if record.get("scope") in {"designated_condenser", "condenser"}:
            scoped_record_seen = True
            continue
        raw_amount = record.get(
            "deposit_kg_per_campaign",
            record.get("wall_deposit_kg_per_campaign", record.get("kg", 0.0)),
        )
        try:
            amount = float(raw_amount)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(amount) or amount <= COATING_POSITIVE_DEPOSIT_TOLERANCE_KG_PER_CAMPAIGN:
            continue
        positive_record_seen = True
        payload = {
            key: _plain_value(record[key])
            for key in ("campaign", "segment", "species", "zone")
            if key in record
        }
        payload["deposit_kg_per_campaign"] = amount
        reasons.append(
            _coating_reason_record(
                "positive_upstream_wall_deposit",
                authority=authority,
                **payload,
            )
        )
    if (
        not positive_record_seen
        and not scoped_record_seen
        and aggregate_deposit_kg is not None
        and math.isfinite(aggregate_deposit_kg)
        and aggregate_deposit_kg > COATING_POSITIVE_DEPOSIT_TOLERANCE_KG_PER_CAMPAIGN
    ):
        reasons.append(
            _coating_reason_record(
                "positive_upstream_wall_deposit",
                authority=authority,
                deposit_kg_per_campaign=float(aggregate_deposit_kg),
            )
        )
    diagnostics = diagnostics or {}
    for finding in diagnostics.get("upstream_hot_wall_findings", ()):
        reasons.append(
            _coating_reason_record(
                "upstream_hot_wall_supersaturation",
                authority=authority,
                finding=_plain_value(finding),
            )
        )
    for finding in diagnostics.get("silica_exposed_to_alkali_findings", ()):
        reasons.append(
            _coating_reason_record(
                "silica_exposed_to_alkali",
                authority=authority,
                finding=_plain_value(finding),
            )
        )
    for explicit in explicit_reasons if isinstance(explicit_reasons, (tuple, list)) else ():
        if isinstance(explicit, Mapping):
            record = dict(_plain_value(explicit))
            record.setdefault("authority", _plain_value(authority))
            record.setdefault("flags", _coating_authority_flags(authority))
            reasons.append(record)
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for reason in reasons:
        key = repr(_plain_value(reason))
        if key in seen:
            continue
        seen.add(key)
        unique.append(reason)
    return unique


def _coating_reason_summary(reasons: list[Mapping[str, Any]]) -> str:
    labels: list[str] = []
    for reason in reasons:
        label = str(reason.get("reason", "coating violation"))
        segment = reason.get("segment")
        species = reason.get("species")
        if segment or species:
            label += f" ({segment or '?'}/{species or '?'})"
        labels.append(label)
    return "; ".join(labels)


def _coating_wall_quantity_unavailable(
    *sources: Mapping[str, Any] | None,
    include_coverage_unknown: bool = False,
) -> str:
    for source in sources:
        if not isinstance(source, Mapping):
            continue
        if source.get("wall_quantity_unavailable") is True:
            return str(source.get("status_reason") or source.get("message") or "wall quantity unavailable")
        refusal_species = source.get("wall_saturation_pressure_refused_species")
        refusal_map = source.get("wall_saturation_pressure_refusals_by_species")
        if refusal_species or refusal_map:
            return str(
                source.get("status_reason")
                or source.get("message")
                or "wall saturation pressure was refused"
            )
        code = str(source.get("code", ""))
        if "wall_saturation_pressure_refused" in code:
            return str(
                source.get("status_reason")
                or source.get("message")
                or "wall saturation pressure was refused"
            )
        if include_coverage_unknown and code == "wall_deposit_coverage_unknown":
            return str(source.get("message") or "wall deposit quantity is unavailable")
        status = str(source.get("status", ""))
        if status == "unavailable" or source.get("output_status") == "unavailable":
            return str(source.get("status_reason") or source.get("message") or "wall quantity unavailable")
        status_reason = str(source.get("status_reason", ""))
        lowered_reason = status_reason.lower()
        if (
            "wall saturation unavailable" in lowered_reason
            or (
                "wall saturation" in lowered_reason
                and "refus" in lowered_reason
            )
        ):
            return status_reason
    return ""


def _coating_report_authority(report: Mapping[str, Any]) -> Mapping[str, Any]:
    nested = report.get("sticking_alpha_authority")
    if isinstance(nested, Mapping):
        return nested
    return report


def _coating_report_deposit_records(report: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw = report.get("upstream_wall_deposit_records", ())
    if not isinstance(raw, (tuple, list)):
        return []
    return [dict(item) for item in raw if isinstance(item, Mapping)]


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


def _optional_finite_number(value: Any, name: str) -> float | None:
    """Parse a finite number, or return None when the field is absent.

    Callers must not treat None as physical zero: missing mol fields on an
    extraction-completeness payload are incomplete provenance.
    """
    if value is None:
        return None
    return _finite_number(value, name)
