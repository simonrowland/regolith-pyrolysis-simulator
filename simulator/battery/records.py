"""Typed records for the empirical battery schema (v2.1 §Record definitions).

Four domain records (Work, Experiment, Observation, Residual) plus the
closed supporting types. No default values that fill physical holes:
optional fields are ``None`` (absent from storage) and three-valued
absence is explicit ``State`` (value / unknown / not_applicable).

Ambiguity resolutions:
- ``State[T]`` is a single tagged record, not a Python ``Optional``. A
  missing dataclass field (``None``) means "not stored"; that is distinct
  from ``unknown`` (archival hole) and ``not_applicable`` (profile forbids
  the axis).
- Locator keys are the existing extract SCHEMA set; at least one nonempty
  location key is required when a Locator is present.
- Reaction coefficients are ``fractions.Fraction`` (signed, products
  positive). Atom-balance is a validator concern, not a constructor
  default.
- Identity lives in ``identity.py``; Observation.identity is annotated
  under postponed evaluation to avoid an import cycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from typing import Any, Generic, Mapping, TypeVar

from simulator.battery.enums import (
    AdmissionStatus,
    AmountBasis,
    AssetRole,
    Authority,
    Engine,
    EvidenceClass,
    ExecutionState,
    ExperimentKind,
    FO2Channel,
    MethodToken,
    MetricOperation,
    NoticeKind,
    Phase,
    Quantity,
    Rail,
    ReferenceStateConvention,
    RefusalReason,
    RegimeClass,
    ResidualStatus,
    SourceRelation,
    StateTag,
    UncertaintyKind,
    ValueKind,
)

T = TypeVar("T")

_LOCATOR_KEYS = (
    "page",
    "published_page",
    "pdf_page_index",
    "table",
    "figure",
    "paragraph",
    "section",
    "equation",
    "line_range",
    "note",
    "source_path",
    "record",
)


def as_decimal(value: object) -> Decimal:
    """Exact decimal from int/str/Decimal; floats via their printed form.

    Binary floats are not identity keys. ``Decimal(str(x))`` preserves the
    literal the caller wrote (1643.15 stays 1643.15, not a binary neighbor).
    """

    if isinstance(value, bool):
        raise TypeError("bool is not a decimal quantity")
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str):
        return Decimal(value)
    if isinstance(value, Fraction):
        return Decimal(value.numerator) / Decimal(value.denominator)
    if isinstance(value, float):
        return Decimal(str(value))
    raise TypeError(f"cannot convert {type(value)!r} to Decimal")


def as_fraction(value: object) -> Fraction:
    if isinstance(value, bool):
        raise TypeError("bool is not a rational coefficient")
    if isinstance(value, Fraction):
        return value
    if isinstance(value, int):
        return Fraction(value)
    if isinstance(value, Decimal):
        return Fraction(value)
    if isinstance(value, str):
        return Fraction(value)
    if isinstance(value, float):
        return Fraction(str(value))
    raise TypeError(f"cannot convert {type(value)!r} to Fraction")


@dataclass(frozen=True)
class State(Generic[T]):
    """Three-valued absence: value(T) | unknown(reason) | not_applicable(reason)."""

    tag: StateTag
    value: T | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.tag is StateTag.VALUE:
            if self.value is None:
                raise ValueError("State.value requires a value")
        else:
            if self.value is not None:
                raise ValueError(f"State.{self.tag} cannot carry a value")
            if not self.reason:
                raise ValueError(f"State.{self.tag} requires a reason")

    @classmethod
    def of(cls, value: T) -> "State[T]":
        return cls(StateTag.VALUE, value=value)

    @classmethod
    def unknown(cls, reason: str) -> "State[T]":
        return cls(StateTag.UNKNOWN, reason=reason)

    @classmethod
    def not_applicable(cls, reason: str) -> "State[T]":
        return cls(StateTag.NOT_APPLICABLE, reason=reason)

    @property
    def is_value(self) -> bool:
        return self.tag is StateTag.VALUE

    @property
    def is_unknown(self) -> bool:
        return self.tag is StateTag.UNKNOWN

    @property
    def is_not_applicable(self) -> bool:
        return self.tag is StateTag.NOT_APPLICABLE


@dataclass(frozen=True)
class Locator:
    """Extract SCHEMA locator; nonempty page/table/figure/equation/record/etc."""

    page: str | int | None = None
    published_page: str | int | None = None
    pdf_page_index: int | None = None
    table: str | None = None
    figure: str | None = None
    paragraph: str | None = None
    section: str | None = None
    equation: str | None = None
    line_range: str | None = None
    note: str | None = None
    source_path: str | None = None
    record: str | None = None

    def __post_init__(self) -> None:
        if not self.has_location():
            raise ValueError("Locator requires at least one nonempty location key")

    def has_location(self) -> bool:
        for name in _LOCATOR_KEYS:
            raw = getattr(self, name)
            if raw is None:
                continue
            if isinstance(raw, str) and not raw.strip():
                continue
            return True
        return False


@dataclass(frozen=True)
class Located(Generic[T]):
    state: State[T]
    locator: Locator | None = None
    inference: Derivation | None = None


@dataclass(frozen=True)
class Species:
    formula: str
    phase: Phase
    polymorph: State[str] | None = None

    def __post_init__(self) -> None:
        if not self.formula:
            raise ValueError("Species.formula is required")


@dataclass(frozen=True)
class ReactionTerm:
    species: Species
    coefficient: Fraction

    def __post_init__(self) -> None:
        object.__setattr__(self, "coefficient", as_fraction(self.coefficient))
        if self.coefficient == 0:
            raise ValueError("ReactionTerm.coefficient cannot be zero")


@dataclass(frozen=True)
class Reaction:
    """Signed, products-positive terms. Atom-balance is checked by validate."""

    terms: tuple[ReactionTerm, ...]

    def __post_init__(self) -> None:
        if not self.terms:
            raise ValueError("Reaction requires at least one term")


@dataclass(frozen=True)
class Composition:
    basis: str
    components: tuple[tuple[str, Decimal], ...]
    amount_basis: AmountBasis

    def __post_init__(self) -> None:
        if not self.basis:
            raise ValueError("Composition.basis (engine-basis token) is required")
        if not self.components:
            raise ValueError("Composition.components must be a complete map")
        canonical = tuple((k, as_decimal(v)) for k, v in self.components)
        object.__setattr__(self, "components", canonical)

    def as_map(self) -> dict[str, Decimal]:
        return dict(self.components)


@dataclass(frozen=True)
class StandardState:
    convention: ReferenceStateConvention
    endmember: Species
    component_basis: str
    reference_pressure_bar: Decimal

    def __post_init__(self) -> None:
        if not self.component_basis:
            raise ValueError("StandardState.component_basis is required")
        if self.component_basis == "single_cation_oxide":
            raise ValueError(
                "single_cation_oxide is a component_id, never a convention "
                "or component_basis token"
            )
        object.__setattr__(
            self, "reference_pressure_bar", as_decimal(self.reference_pressure_bar)
        )


@dataclass(frozen=True)
class Derivation:
    relation: str
    inputs: tuple[str, ...]
    parameters: tuple[tuple[str, Located[Decimal]], ...]
    output_unit: str

    def __post_init__(self) -> None:
        if not self.relation:
            raise ValueError("Derivation.relation is required")
        if not self.inputs:
            raise ValueError("Derivation.inputs is required")
        if not self.output_unit:
            raise ValueError("Derivation.output_unit is required")


@dataclass(frozen=True)
class Notice:
    kind: NoticeKind
    affected_quantities: tuple[Quantity, ...]
    reason: str
    origin: str
    original: Decimal | None = None
    source: str | None = None  # C(fallback) "from"
    destination: str | None = None  # C(fallback) "to"
    band: str | None = None  # C(domain)
    dropped: tuple[str, ...] | None = None  # C(projection)
    dropped_mass_fraction: Decimal | None = None

    def __post_init__(self) -> None:
        if not self.affected_quantities:
            raise ValueError("Notice.affected_quantities is required")
        if not self.reason:
            raise ValueError("Notice.reason is required")
        if not self.origin:
            raise ValueError("Notice.origin is required")
        if self.original is not None:
            object.__setattr__(self, "original", as_decimal(self.original))
        if self.dropped_mass_fraction is not None:
            object.__setattr__(
                self, "dropped_mass_fraction", as_decimal(self.dropped_mass_fraction)
            )


@dataclass(frozen=True)
class Value:
    """Tagged observable; no scalar coercion between kinds."""

    kind: ValueKind
    point: Decimal | None = None
    series: tuple[tuple[Decimal, Decimal], ...] | None = None
    bound_operator: str | None = None
    bound_value: Decimal | None = None
    interval_low: Decimal | None = None
    interval_high: Decimal | None = None
    ordering: tuple[str, ...] | None = None
    categorical: str | None = None
    relative_series: tuple[tuple[Decimal, Decimal], ...] | None = None
    relative_normalization: str | None = None
    expression_text: str | None = None
    expression_parameters: tuple[tuple[str, Decimal], ...] | None = None
    expression_domain: str | None = None
    unavailable_reason: str | None = None

    def __post_init__(self) -> None:
        kind = self.kind
        if kind is ValueKind.POINT:
            if self.point is None:
                raise ValueError("Value.point requires point")
            object.__setattr__(self, "point", as_decimal(self.point))
        elif kind is ValueKind.SERIES:
            if not self.series:
                raise ValueError("Value.series requires series points")
            object.__setattr__(
                self,
                "series",
                tuple((as_decimal(c), as_decimal(v)) for c, v in self.series),
            )
        elif kind is ValueKind.BOUND:
            if self.bound_operator is None or self.bound_value is None:
                raise ValueError("Value.bound requires operator and value")
            object.__setattr__(self, "bound_value", as_decimal(self.bound_value))
        elif kind is ValueKind.INTERVAL:
            if self.interval_low is None or self.interval_high is None:
                raise ValueError("Value.interval requires low and high")
            object.__setattr__(self, "interval_low", as_decimal(self.interval_low))
            object.__setattr__(self, "interval_high", as_decimal(self.interval_high))
        elif kind is ValueKind.ORDERING:
            if not self.ordering:
                raise ValueError("Value.ordering requires a list")
        elif kind is ValueKind.CATEGORICAL:
            if not self.categorical:
                raise ValueError("Value.categorical requires a string")
        elif kind is ValueKind.RELATIVE_SERIES:
            if not self.relative_series or not self.relative_normalization:
                raise ValueError("Value.relative_series requires series and normalization")
            object.__setattr__(
                self,
                "relative_series",
                tuple(
                    (as_decimal(c), as_decimal(v)) for c, v in self.relative_series
                ),
            )
        elif kind is ValueKind.EXPRESSION:
            if not self.expression_text:
                raise ValueError("Value.expression requires text")
        elif kind is ValueKind.UNAVAILABLE:
            if not self.unavailable_reason:
                raise ValueError("Value.unavailable requires a reason")
        else:  # pragma: no cover
            raise ValueError(f"unknown Value.kind {kind}")

    @classmethod
    def point_of(cls, value: object) -> "Value":
        return cls(ValueKind.POINT, point=as_decimal(value))


@dataclass(frozen=True)
class Uncertainty:
    kind: UncertaintyKind
    verbatim: str | Mapping[str, Any] | None = None
    value: Decimal | tuple[Decimal, Decimal] | None = None
    basis: str | None = None
    derivation: Derivation | None = None

    def __post_init__(self) -> None:
        if self.kind is UncertaintyKind.PRINTED and self.verbatim is None:
            raise ValueError("printed uncertainty requires verbatim")
        if self.kind is UncertaintyKind.DERIVED and self.derivation is None:
            raise ValueError("derived uncertainty requires a Derivation")
        if self.value is not None and not self.basis:
            raise ValueError("Uncertainty.value requires a basis")
        if isinstance(self.value, tuple):
            object.__setattr__(
                self, "value", (as_decimal(self.value[0]), as_decimal(self.value[1]))
            )
        elif self.value is not None:
            object.__setattr__(self, "value", as_decimal(self.value))


@dataclass(frozen=True)
class SourceFile:
    asset_id: str
    role: AssetRole
    path: str
    sha256: State[str]
    provenance_asset: str | None = None

    def __post_init__(self) -> None:
        if not self.asset_id or not self.path:
            raise ValueError("SourceFile requires asset_id and path")


@dataclass(frozen=True)
class SourceFiles:
    corpus_repo: str
    corpus_commit: State[str]
    files: tuple[SourceFile, ...]

    def __post_init__(self) -> None:
        if not self.corpus_repo:
            raise ValueError("SourceFiles.corpus_repo is required")
        if not self.files:
            raise ValueError("SourceFiles.files is required")


@dataclass(frozen=True)
class Work:
    work_id: str
    citation: str
    source_ids: tuple[str, ...]
    source_files: SourceFiles
    doi: str | None = None

    def __post_init__(self) -> None:
        if not self.work_id:
            raise ValueError("Work.work_id is required")
        if not self.citation:
            raise ValueError("Work.citation is required")
        if not self.source_ids:
            raise ValueError("Work.source_ids is required")


@dataclass(frozen=True)
class ApparatusGeometry:
    orifice_area_m2: Located[Decimal] | None = None
    orifice_diameter_m: Located[Decimal] | None = None
    clausing_factor: Located[Decimal] | None = None
    orifice_to_sample_area_ratio: Located[Decimal] | None = None
    exposed_area_m2: Located[Decimal] | None = None
    chamber_length_m: Located[Decimal] | None = None


@dataclass(frozen=True)
class Apparatus:
    cell_material_and_liner: Located[str] | None = None
    geometry: ApparatusGeometry | None = None
    ionization: Mapping[str, Located[Any]] | None = None
    calibration: Mapping[str, Located[Any]] | None = None
    temperature_measurement: Mapping[str, Located[str]] | None = None
    wall: Mapping[str, Located[Any]] | None = None


@dataclass(frozen=True)
class Sample:
    mass_kg: Located[Decimal] | None = None
    initial_composition: Located[Composition] | None = None
    printed_composition: Located[Mapping[str, Any]] | None = None
    form: Located[str] | None = None
    container: Located[str] | None = None


@dataclass(frozen=True)
class FO2Control:
    channel: State[FO2Channel]
    buffer: Located[str] | None = None


@dataclass(frozen=True)
class SweepGas:
    species: str
    flow_sccm: State[Decimal]
    partial_pressure_Pa: State[Decimal]


@dataclass(frozen=True)
class FlowRegime:
    regime_class: State[RegimeClass]
    knudsen_number_orifice: Located[Decimal] | None = None
    knudsen_number_chamber: Located[Decimal] | None = None


@dataclass(frozen=True)
class PressureEnvironment:
    total_pressure_Pa: Located[Decimal]
    sweep_gas: Located[SweepGas]
    regime: FlowRegime
    gauge: Mapping[str, Located[str]] | None = None
    pressure_profile: Located[tuple[tuple[Decimal, Decimal], ...]] | None = None
    pumping: Mapping[str, Located[Any]] | None = None
    cell_internal_pressure_note: Located[str] | None = None


@dataclass(frozen=True)
class Experiment:
    experiment_id: str
    kind: ExperimentKind
    method: State[MethodToken]
    sample: Sample
    conditions: Mapping[str, Located[Decimal]]
    pressure_environment: PressureEnvironment
    work_id: str | None = None
    simulated_experiment_id: str | None = None
    locator: Locator | None = None
    apparatus: Apparatus | None = None
    fO2_control: FO2Control | None = None

    def __post_init__(self) -> None:
        if not self.experiment_id:
            raise ValueError("Experiment.experiment_id is required")
        if not self.conditions:
            raise ValueError("Experiment.conditions is required")


@dataclass(frozen=True)
class Evidence:
    class_: State[EvidenceClass]
    original_method_class: str | None = None
    attribution: str | None = None
    model: str | None = None
    correlation_group: str | None = None


@dataclass(frozen=True)
class AdmissionDecision:
    worker: str
    date: str
    evidence: Locator


@dataclass(frozen=True)
class Admission:
    status: AdmissionStatus
    reason: str
    superseded_by: str | None = None
    decided_by: AdmissionDecision | None = None

    def __post_init__(self) -> None:
        if not self.reason:
            raise ValueError("Admission.reason is required")


@dataclass(frozen=True)
class EngineTrace:
    name: Engine
    channel: str
    run_id: str
    coefficient_sources: tuple[str, ...]
    lineage_complete: bool
    version: str | None = None
    requested_composition: State[Composition] | None = None

    def __post_init__(self) -> None:
        if not self.name or not self.channel or not self.run_id:
            raise ValueError("EngineTrace requires name, channel, run_id")
        if not self.coefficient_sources:
            raise ValueError("EngineTrace.coefficient_sources is required")


@dataclass(frozen=True)
class Annotations:
    crystal_system: str | None = None
    space_group: str | None = None
    transition_note: str | None = None


@dataclass(frozen=True)
class Observation:
    observation_id: str
    experiment_id: str
    identity: Any  # Identity; annotated loosely to avoid import cycle
    value: Value
    uncertainty: Uncertainty
    evidence: Evidence
    admission: Admission
    notices: tuple[Notice, ...]
    source_id: str | None = None
    locator: Locator | None = None
    read_from: str | None = None
    point_conditions: Mapping[str, Located[Decimal]] | None = None
    derived_from: tuple[str, ...] | None = None
    derivation: Derivation | None = None
    annotations: Annotations | None = None
    engine: EngineTrace | None = None
    authority: Authority | None = None
    certified_band: Mapping[str, tuple[Decimal, Decimal]] | None = None

    def __post_init__(self) -> None:
        if not self.observation_id:
            raise ValueError("Observation.observation_id is required")
        if not self.experiment_id:
            raise ValueError("Observation.experiment_id is required")
        if self.certified_band is not None:
            object.__setattr__(
                self,
                "certified_band",
                {
                    k: (as_decimal(lo), as_decimal(hi))
                    for k, (lo, hi) in self.certified_band.items()
                },
            )


@dataclass(frozen=True)
class CandidateRequest:
    experiment_id: str
    quantity: Quantity
    engine: Engine
    channel: str


@dataclass(frozen=True)
class Execution:
    state: ExecutionState
    call_evidence: str | None = None


@dataclass(frozen=True)
class DecisionBand:
    value: Decimal
    unit: str
    rule: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", as_decimal(self.value))
        if not self.unit or not self.rule:
            raise ValueError("DecisionBand requires unit and rule")


@dataclass(frozen=True)
class ResidualNumeric:
    operation: MetricOperation
    unit: str
    value: Decimal
    decision_band: DecisionBand
    metric_uncertainty: Uncertainty | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", as_decimal(self.value))
        if not self.unit:
            raise ValueError("ResidualNumeric.unit is required")


@dataclass(frozen=True)
class ResidualRefusal:
    reason: RefusalReason
    detail: Mapping[str, Any]
    check_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class Residual:
    key: str
    reference: str
    execution: Execution
    rail: Rail
    status: ResidualStatus
    source_relation: SourceRelation
    score_eligible: bool
    exclusions: tuple[str, ...]
    notices: tuple[Notice, ...]
    candidate: str | None = None
    candidate_request: CandidateRequest | None = None
    numeric: ResidualNumeric | None = None
    refusal: ResidualRefusal | None = None

    def __post_init__(self) -> None:
        if not self.key:
            raise ValueError("Residual.key is required")
        if not self.reference:
            raise ValueError("Residual.reference is required")


def union_notices(*groups: tuple[Notice, ...] | None) -> tuple[Notice, ...]:
    """Derived Residual.notices: union of endpoint ancestry, order-preserving."""

    seen: set[tuple[object, ...]] = set()
    out: list[Notice] = []
    for group in groups:
        if not group:
            continue
        for notice in group:
            fingerprint = (
                notice.kind,
                notice.affected_quantities,
                notice.reason,
                notice.origin,
            )
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            out.append(notice)
    return tuple(out)
