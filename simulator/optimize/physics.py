"""Hard physics-feasibility gates over a completed PhysicsTrace."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
import math
from types import MappingProxyType
from typing import Any, Literal, Mapping

from simulator.accounting import AccountingError, parse_formula
from simulator.condensation_routing import accepted_species_for_stage_number
from simulator.state import MOLAR_MASS

SourceKind = Literal[
    "literature",
    "materials.yaml",
    "profile",
    "engineering_envelope",
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
        source="profile",
        source_ref="profile.feasibility.extraction_completeness.min_pct",
    ))
    knudsen_max: ThresholdSpec = field(default_factory=lambda: ThresholdSpec(
        id="knudsen_viscous_max",
        value=0.01,
        units="Kn",
        source="profile",
        source_ref="profile.feasibility.knudsen=viscous",
    ))
    furnace_T_max_C: ThresholdSpec = field(default_factory=lambda: ThresholdSpec(
        id="furnace_T_max_C",
        value=1800.0,
        units="degC",
        source="profile",
        source_ref="profile.feasibility.furnace_T_max_C",
    ))
    target_species: tuple[str, ...] = ("SiO",)
    residual_species_by_target: Mapping[str, tuple[str, ...]] = field(
        default_factory=lambda: MappingProxyType({
            "SiO": ("SiO2", "SiO"),
            "Fe": ("FeO", "Fe"),
            "CrO2": ("Cr2O3", "CrO2", "Cr"),
            "Mg": ("MgO", "Mg"),
            "Na": ("Na2O", "Na"),
            "K": ("K2O", "K"),
        })
    )
    allowable_wall_deposit_kg: Mapping[tuple[str, str], ThresholdSpec] = field(
        default_factory=dict
    )

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

    def evaluate(self, trace: Any) -> FeasibilityResult:
        margins = {
            "delivered_stream_purity": self.delivered_stream_purity(trace),
            "coating": self.coating(trace),
            "extraction_completeness": self.extraction_completeness(trace),
            "knudsen_viscous": self.knudsen_viscous(trace),
            "furnace_temperature": self.furnace_temperature(trace),
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
                return _fail_closed(
                    "delivered_stream_purity",
                    self.stream_purity_min,
                    "no nonzero delivered stream evidence",
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
            worst_margin = math.inf
            worst_observed = math.inf
            worst_detail = "no wall deposit"
            for (campaign, segment, species), kg in sorted(by_campaign.items()):
                if kg <= _EPS:
                    continue
                limit = self.allowable_wall_deposit_kg.get((segment, species))
                if limit is None:
                    return _fail_closed(
                        "coating",
                        self.coating_min_campaigns_to_resinter,
                        (
                            "missing allowable_wall_deposit_kg for "
                            f"{segment}/{species}"
                        ),
                    )
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
                        f"{campaign}/{segment}/{species}: deposit={kg:.6g} kg, "
                        f"allowable={limit.value:.6g} kg, "
                        f"campaigns_to_resinter={campaigns_to_resinter:.6g}"
                    )
            if math.isinf(worst_margin):
                worst_margin = math.inf
            return _margin(
                "coating",
                worst_margin,
                self.coating_min_campaigns_to_resinter,
                worst_observed,
                worst_detail,
            )
        except (KeyError, TypeError, ValueError) as exc:
            return _fail_closed("coating", self.coating_min_campaigns_to_resinter, str(exc))

    def extraction_completeness(self, trace: Any) -> GateMargin:
        try:
            products = _required_mapping(trace, "product_ledger_kg")
            rump = _required_mapping(trace, "terminal_rump_by_species_kg")
            worst_margin = math.inf
            worst_fraction = 1.0
            worst_detail = ""
            for target in self.target_species:
                product_mol = _target_equivalent_mol(target, target, products.get(target, 0.0))
                residual_mol = 0.0
                for residual in self.residual_species_by_target.get(target, (target,)):
                    residual_mol += _target_equivalent_mol(
                        target,
                        residual,
                        rump.get(residual, 0.0),
                    )
                denom = product_mol + residual_mol
                if denom <= _EPS:
                    return _fail_closed(
                        "extraction_completeness",
                        self.extraction_min_fraction,
                        f"{target}: no target-equivalent mol evidence",
                    )
                fraction = product_mol / denom
                margin = fraction - self.extraction_min_fraction.value
                if margin < worst_margin:
                    worst_margin = margin
                    worst_fraction = fraction
                    worst_detail = (
                        f"{target}: product_target_equiv_mol={product_mol:.6g}, "
                        f"residual_target_equiv_mol={residual_mol:.6g}, "
                        f"denominator_target_equiv_mol={denom:.6g}"
                    )
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


def _fail_closed(gate: str, threshold: ThresholdSpec, detail: str) -> GateMargin:
    return GateMargin(
        gate=gate,
        feasible=False,
        margin=-math.inf,
        threshold=threshold,
        observed=math.nan,
        detail=f"fail-closed: {detail}",
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


def _target_equivalent_mol(target: str, species: str, kg: Any) -> float:
    species_mol = _species_mol(species, kg)
    if species_mol <= _EPS:
        return 0.0
    target_element = _target_element(target)
    species_formula = parse_formula(species, species=species)
    element_count = species_formula.elements.get(target_element, 0.0)
    if element_count <= 0.0:
        raise ValueError(f"{species} contains no {target_element} for target {target}")
    return species_mol * element_count


def _target_element(target: str) -> str:
    formula = parse_formula(target, species=target)
    if len(formula.elements) == 1:
        return next(iter(formula.elements))
    non_oxygen = [element for element in formula.elements if element != "O"]
    if len(non_oxygen) == 1:
        return non_oxygen[0]
    raise ValueError(f"target {target} does not identify one target element")


def _species_mol(species: str, kg: Any) -> float:
    amount = _non_negative_number(kg, f"{species} kg")
    if amount <= _EPS:
        return 0.0
    molar_mass = MOLAR_MASS.get(species)
    if molar_mass is None:
        raise KeyError(f"missing molar mass for {species}")
    return amount * 1000.0 / float(molar_mass)


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
