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
    extraction_completeness_by_target,
)
from simulator.condensation_routing import accepted_species_for_stage_number
from simulator.optimize.canonical import canonical_json_dumps, normalize_canonical_value
from simulator.trace import WALL_DEPOSIT_ZONE_NAMES

SourceKind = Literal[
    "literature",
    "materials.yaml",
    "profile",
    "engineering_envelope",
    "code_default",
]

PHYSICS_GATE_VERSION = "physics-feasibility-v1"
GATE_ORDER: tuple[str, ...] = (
    "delivered_stream_purity",
    "coating",
    "extraction_completeness",
    "knudsen_viscous",
    "furnace_temperature",
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
    target_species: tuple[str, ...] = ("SiO",)
    active_gates: tuple[str, ...] = GATE_ORDER
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
            "knudsen_max": self.knudsen_max,
            "furnace_T_max_C": self.furnace_T_max_C,
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
        return (
            self.stream_purity_min,
            self.coating_min_campaigns_to_resinter,
            self.extraction_min_fraction,
            self.knudsen_max,
            self.furnace_T_max_C,
            *tuple(self.allowable_wall_deposit_kg.values()),
        )

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
                    worst_observed = campaigns_to_resinter
                    worst_detail = (
                        f"{campaign}/{zone}/{segment}/{species}: "
                        f"deposit={kg:.6g} kg, "
                        f"allowable={limit.value:.6g} kg, "
                        f"campaigns_to_resinter={campaigns_to_resinter:.6g}"
                    )
            if math.isinf(worst_margin):
                worst_margin = math.inf
            return GateMargin(
                gate="coating",
                feasible=True,
                margin=float(worst_margin),
                threshold=self.coating_min_campaigns_to_resinter,
                observed=float(worst_observed),
                detail=(
                    worst_detail
                    if worst_detail == "no wall deposit"
                    else f"reported-only: {worst_detail}"
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            return _fail_closed("coating", self.coating_min_campaigns_to_resinter, str(exc))

    def extraction_completeness(self, trace: Any) -> GateMargin:
        try:
            products = _required_mapping(trace, "product_ledger_kg")
            rump = _required_mapping(trace, "terminal_rump_by_species_kg")
            by_target = extraction_completeness_by_target(
                self.target_species,
                self.residual_species_by_target,
                products,
                rump,
            )
            worst_margin = math.inf
            worst_fraction = 1.0
            worst_detail = ""
            for target in self.target_species:
                result = by_target[str(target)]
                if result.completeness_fraction is None:
                    detail = _extraction_completeness_fail_closed_detail(result)
                    if detail.startswith("not-applicable:"):
                        return _not_applicable(
                            "extraction_completeness",
                            self.extraction_min_fraction,
                            detail,
                        )
                    return _fail_closed(
                        "extraction_completeness",
                        self.extraction_min_fraction,
                        detail,
                    )
                fraction = result.completeness_fraction
                margin = fraction - self.extraction_min_fraction.value
                if margin < worst_margin:
                    worst_margin = margin
                    worst_fraction = fraction
                    worst_detail = result.detail
            return _margin(
                "extraction_completeness",
                worst_margin,
                self.extraction_min_fraction,
                worst_fraction,
                worst_detail,
            )
        except (AccountingError, KeyError, TypeError, ValueError) as exc:
            return _fail_closed(
                "extraction_completeness",
                self.extraction_min_fraction,
                str(exc),
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
        products = _required_mapping(trace, "product_ledger_kg")
        rump = _required_mapping(trace, "terminal_rump_by_species_kg")
        by_target = extraction_completeness_by_target(
            targets,
            active_constraints.residual_species_by_target,
            products,
            rump,
            require_residual_species=True,
        )
    except (AccountingError, KeyError, TypeError, ValueError) as exc:
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


def _extraction_completeness_target_report(
    result: TargetExtractionCompleteness,
    constraints: PhysicsConstraintSet,
) -> Mapping[str, Any]:
    target = str(result.target_species)
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
            constraints,
            result.denominator_target_equiv_mol if has_denominator else None,
            has_denominator,
        ),
        "product_bin": target,
        "product_account": "product_ledger_kg",
        "product_target_equiv_mol": (
            result.product_target_equiv_mol if has_denominator else None
        ),
        "residual_target_equiv_mol": (
            result.residual_target_equiv_mol if has_denominator else None
        ),
        "denominator_target_equiv_mol": (
            result.denominator_target_equiv_mol if has_denominator else None
        ),
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
    residual_species = _residual_species_for_target(target, constraints)
    return MappingProxyType({
        "status": "insufficient-evidence",
        "conclusion": "inconclusive",
        "target_species": target,
        "denominator_account": _extraction_denominator_account(),
        "denominator_basis": "target_equivalent_mol",
        "allowed_residual": _allowed_residual_payload(
            residual_species,
            constraints,
            None,
            False,
        ),
        "product_bin": target,
        "product_account": "product_ledger_kg",
        "product_target_equiv_mol": None,
        "residual_target_equiv_mol": None,
        "denominator_target_equiv_mol": None,
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


def _extraction_denominator_account() -> Mapping[str, str]:
    return MappingProxyType({
        "product": "product_ledger_kg",
        "residual": "terminal_rump_by_species_kg",
    })


def _allowed_residual_payload(
    residual_species: tuple[str, ...],
    constraints: PhysicsConstraintSet,
    denominator_target_equiv_mol: float | None,
    has_denominator: bool,
) -> Mapping[str, Any]:
    allowed_fraction = max(0.0, 1.0 - constraints.extraction_min_fraction.value)
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
