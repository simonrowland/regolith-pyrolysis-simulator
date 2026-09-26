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

import re
from dataclasses import dataclass, fields, is_dataclass
from decimal import Decimal
from fractions import Fraction
from typing import Any, Generic, Mapping, TypeVar

from simulator.battery.enums import (
    AdmissionStatus,
    AmountBasis,
    AssetRole,
    Authority,
    BenchAbsenceReason,
    BenchIdentityBasis,
    Engine,
    EvidenceClass,
    ExecutionState,
    ExperimentKind,
    FO2Channel,
    MethodToken,
    MetricOperation,
    NoticeKind,
    Phase,
    Polymorph,
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
        if self.reason is not None and not isinstance(self.reason, str):
            raise ValueError(f"State.{self.tag} reason must be a string")
        if self.tag is StateTag.VALUE:
            if self.value is None:
                raise ValueError("State.value requires a value")
        else:
            if self.value is not None:
                raise ValueError(f"State.{self.tag} cannot carry a value")
            if not self.reason or not self.reason.strip():
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


_CHARGE_SUFFIX_RE = re.compile(r"([+-])$")


def split_formula_charge(formula: str) -> tuple[str, int]:
    """Strip a trailing ``+`` / ``-`` marker. Neutral is charge 0 as a value."""

    text = str(formula or "")
    match = _CHARGE_SUFFIX_RE.search(text)
    if match is None:
        return text, 0
    return text[: match.start()], 1 if match.group(1) == "+" else -1


def _coerce_polymorph_value(value: object) -> Polymorph:
    if isinstance(value, Polymorph):
        return value
    from simulator.battery.polymorph_dictionary import coerce_polymorph_token

    return coerce_polymorph_token(value)


def _coerce_polymorph_state(polymorph: State[object] | None) -> State[Polymorph] | None:
    if polymorph is None:
        return None
    if not polymorph.is_value:
        return polymorph  # type: ignore[return-value]
    try:
        token = _coerce_polymorph_value(polymorph.value)
    except ValueError:
        from simulator.battery.polymorph_dictionary import unrecognised_polymorph_reason

        return State.unknown(unrecognised_polymorph_reason(polymorph.value))
    return State.of(token)


def _coerce_charge_state(charge: State[object] | int | None, inferred: int) -> State[int]:
    if charge is None:
        return State.of(inferred)
    if isinstance(charge, int) and not isinstance(charge, bool):
        return State.of(charge)
    if not isinstance(charge, State):
        raise ValueError(
            f"Species.charge must be an int or State[int], not {charge!r}"
        )
    if charge.is_value:
        raw = charge.value
        if isinstance(raw, bool) or not isinstance(raw, int):
            try:
                raw = int(str(raw))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Species.charge value is not an integer: {raw!r}") from exc
            charge = State.of(raw)
        if inferred != 0 and raw != inferred:
            raise ValueError(
                f"formula charge marker {inferred} disagrees with Species.charge {raw}"
            )
        return charge
    return charge  # type: ignore[return-value]


@dataclass(frozen=True)
class Species:
    formula: str
    phase: State[Phase] | Phase
    polymorph: State[Polymorph] | State[str] | None = None
    charge: State[int] | int | None = None

    def __post_init__(self) -> None:
        if not self.formula:
            raise ValueError("Species.formula is required")
        stripped, inferred = split_formula_charge(self.formula)
        if stripped != self.formula:
            object.__setattr__(self, "formula", stripped)
        object.__setattr__(self, "charge", _coerce_charge_state(self.charge, inferred))
        phase = self.phase
        if isinstance(phase, Phase):
            object.__setattr__(self, "phase", State.of(phase))
            phase = self.phase
        if not isinstance(phase, State):
            raise ValueError(
                f"Species.phase must be a closed Phase token or State[Phase], not {phase!r}"
            )
        if phase.is_value and phase.value not in Phase:
            raise ValueError(
                f"Species.phase must be a closed Phase token, not {phase.value!r}"
            )
        object.__setattr__(self, "polymorph", _coerce_polymorph_state(self.polymorph))


def phase_token(species: Species) -> Phase | None:
    """Closed Phase token when the axis is a value; None for unknown / n/a."""

    phase = species.phase
    if isinstance(phase, Phase):
        return phase
    if isinstance(phase, State) and phase.is_value:
        return phase.value
    return None


def polymorph_token(species: Species) -> Polymorph | None:
    """Closed Polymorph token when the axis is a value; None for unknown / n/a."""

    poly = species.polymorph
    if poly is None:
        return None
    if isinstance(poly, State) and poly.is_value:
        value = poly.value
        return value if isinstance(value, Polymorph) else None
    return None


def charge_value(species: Species) -> int | None:
    """Integer charge when the axis is a value; None for unknown / n/a."""

    charge = species.charge
    if charge is None:
        return None
    if isinstance(charge, int) and not isinstance(charge, bool):
        return charge
    if isinstance(charge, State) and charge.is_value:
        value = charge.value
        return value if isinstance(value, int) and not isinstance(value, bool) else None
    return None


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
        keys = [k for k, _ in canonical]
        if len(keys) != len(set(keys)):
            raise ValueError("Composition.components keys must be unique")
        for key, amount in canonical:
            if not amount.is_finite() or amount < 0:
                raise ValueError(
                    f"Composition component {key!r} must be a nonnegative finite amount"
                )
        object.__setattr__(self, "components", canonical)

    def as_map(self) -> dict[str, Decimal]:
        return dict(self.components)


@dataclass(frozen=True)
class StandardState:
    convention: ReferenceStateConvention
    endmember: Species
    component_basis: str
    # Absent when the source does not print a number. Never a default of 1 bar.
    reference_pressure_bar: Decimal | None = None

    def __post_init__(self) -> None:
        if not self.component_basis:
            raise ValueError("StandardState.component_basis is required")
        if self.component_basis == "single_cation_oxide":
            raise ValueError(
                "single_cation_oxide is a component_id, never a convention "
                "or component_basis token"
            )
        if self.reference_pressure_bar is not None:
            object.__setattr__(
                self, "reference_pressure_bar", as_decimal(self.reference_pressure_bar)
            )


@dataclass(frozen=True)
class Derivation:
    relation: str
    inputs: tuple[str, ...]
    parameters: tuple[tuple[str, Located[Decimal]], ...]
    output_unit: str
    # Generator-declared engine_point disposition. True means this row is a
    # pure-substance reference and engine_point does not apply. Absent means
    # not declared — never a stamped false.
    pure_substance_reference: bool | None = None

    def __post_init__(self) -> None:
        if not self.relation:
            raise ValueError("Derivation.relation is required")
        if not self.inputs:
            raise ValueError("Derivation.inputs is required")
        if not self.output_unit:
            raise ValueError("Derivation.output_unit is required")
        if self.pure_substance_reference is not None and not isinstance(
            self.pure_substance_reference, bool
        ):
            raise TypeError("Derivation.pure_substance_reference must be bool or absent")


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


_VALUE_PAYLOAD = {
    ValueKind.POINT: ("point",),
    ValueKind.SERIES: ("series",),
    ValueKind.BOUND: ("bound_operator", "bound_value"),
    ValueKind.INTERVAL: ("interval_low", "interval_high"),
    ValueKind.ORDERING: ("ordering",),
    ValueKind.CATEGORICAL: ("categorical",),
    ValueKind.RELATIVE_SERIES: ("relative_series", "relative_normalization"),
    ValueKind.EXPRESSION: (
        "expression_text",
        "expression_parameters",
        "expression_domain",
    ),
    ValueKind.UNAVAILABLE: ("unavailable_reason",),
}
_VALUE_PAYLOAD_FIELDS = frozenset(
    name for names in _VALUE_PAYLOAD.values() for name in names
)


def _require_finite(name: str, value: Decimal) -> None:
    if not value.is_finite():
        raise ValueError(f"{name} must be a finite decimal")


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
    approximate: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.approximate, bool):
            raise TypeError("Value.approximate must be bool")
        kind = self.kind
        allowed = _VALUE_PAYLOAD.get(kind)
        if allowed is None:  # pragma: no cover
            raise ValueError(f"unknown Value.kind {kind}")
        for field in _VALUE_PAYLOAD_FIELDS:
            if field not in allowed and getattr(self, field) is not None:
                raise ValueError(f"Value.{kind.value} cannot carry {field}")
        if kind is ValueKind.POINT:
            if self.point is None:
                raise ValueError("Value.point requires point")
            point = as_decimal(self.point)
            _require_finite("Value.point", point)
            object.__setattr__(self, "point", point)
        elif kind is ValueKind.SERIES:
            if not self.series:
                raise ValueError("Value.series requires series points")
            series = tuple((as_decimal(c), as_decimal(v)) for c, v in self.series)
            for coord, val in series:
                _require_finite("Value.series coordinate", coord)
                _require_finite("Value.series value", val)
            object.__setattr__(self, "series", series)
        elif kind is ValueKind.BOUND:
            if self.bound_operator is None or self.bound_value is None:
                raise ValueError("Value.bound requires operator and value")
            bound = as_decimal(self.bound_value)
            _require_finite("Value.bound_value", bound)
            object.__setattr__(self, "bound_value", bound)
        elif kind is ValueKind.INTERVAL:
            if self.interval_low is None or self.interval_high is None:
                raise ValueError("Value.interval requires low and high")
            low = as_decimal(self.interval_low)
            high = as_decimal(self.interval_high)
            _require_finite("Value.interval_low", low)
            _require_finite("Value.interval_high", high)
            object.__setattr__(self, "interval_low", low)
            object.__setattr__(self, "interval_high", high)
        elif kind is ValueKind.ORDERING:
            if not self.ordering:
                raise ValueError("Value.ordering requires a list")
        elif kind is ValueKind.CATEGORICAL:
            if not self.categorical:
                raise ValueError("Value.categorical requires a string")
        elif kind is ValueKind.RELATIVE_SERIES:
            if not self.relative_series or not self.relative_normalization:
                raise ValueError("Value.relative_series requires series and normalization")
            series = tuple((as_decimal(c), as_decimal(v)) for c, v in self.relative_series)
            for coord, val in series:
                _require_finite("Value.relative_series coordinate", coord)
                _require_finite("Value.relative_series value", val)
            object.__setattr__(self, "relative_series", series)
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


def _located_value(value: Located[Value] | Located[Decimal] | None) -> Located[Value] | None:
    """Promote legacy decimal evidence to a tagged POINT without losing provenance."""

    if value is None or not value.state.is_value:
        return value  # type: ignore[return-value]
    raw = value.state.value
    if isinstance(raw, Value):
        return value  # type: ignore[return-value]
    return Located(
        state=State.of(Value.point_of(raw)),
        locator=value.locator,
        inference=value.inference,
    )


def _required_located_value(
    value: Located[Value] | Located[Decimal] | None,
    path: str,
) -> Located[Value]:
    if value is None:
        raise ValueError(f"{path} requires a Located value or typed absence state")
    promoted = _located_value(value)
    assert promoted is not None
    return promoted


def _validate_bench_absence(value: object, path: str = "bench") -> None:
    """Reject free-text absence reasons on the new bench evidence surface."""

    if isinstance(value, Located):
        state = value.state
        if state.is_unknown or state.is_not_applicable:
            try:
                BenchAbsenceReason(str(state.reason))
            except ValueError as exc:
                allowed = ", ".join(item.value for item in BenchAbsenceReason)
                raise ValueError(
                    f"{path} absence reason must be one of {allowed}; got {state.reason!r}"
                ) from exc
        return
    if is_dataclass(value) and not isinstance(value, type):
        for item in fields(value):
            _validate_bench_absence(getattr(value, item.name), f"{path}.{item.name}")
        return
    if isinstance(value, Mapping):
        for name, item in value.items():
            _validate_bench_absence(item, f"{path}.{name}")
        return
    if isinstance(value, (tuple, list)):
        for index, item in enumerate(value):
            _validate_bench_absence(item, f"{path}[{index}]")


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
    orifice_area_m2: Located[Value] | None = None
    orifice_diameter_m: Located[Value] | None = None
    clausing_factor: Located[Value] | None = None
    orifice_to_sample_area_ratio: Located[Value] | None = None
    exposed_area_m2: Located[Value] | None = None
    chamber_length_m: Located[Value] | None = None
    cell_internal_dimensions: Mapping[str, Located[Value]] | None = None
    chamber_dimensions: Mapping[str, Located[Value]] | None = None
    chamber_volume_m3: Located[Value] | None = None
    orifice_channel_length_m: Located[Value] | None = None
    orifice_count: Located[Value] | None = None
    orifice_shape: Located[str] | None = None

    def __post_init__(self) -> None:
        for name in (
            "orifice_area_m2",
            "orifice_diameter_m",
            "clausing_factor",
            "orifice_to_sample_area_ratio",
            "exposed_area_m2",
            "chamber_length_m",
            "chamber_volume_m3",
            "orifice_channel_length_m",
            "orifice_count",
        ):
            object.__setattr__(self, name, _located_value(getattr(self, name)))
        for name in ("cell_internal_dimensions", "chamber_dimensions"):
            raw = getattr(self, name)
            if raw is not None:
                object.__setattr__(
                    self,
                    name,
                    {key: _located_value(value) for key, value in raw.items()},
                )


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
    mass_kg: Located[Value] | None = None
    initial_composition: Located[Composition] | None = None
    printed_composition: Located[Mapping[str, Any]] | None = None
    form: Located[str] | None = None
    container: Located[str] | None = None
    composition_class: Located[str] | None = None
    characterization: Located[str] | None = None
    surface_area_m2: Located[Value] | None = None
    pretreatment: Located[str] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "mass_kg", _located_value(self.mass_kg))
        object.__setattr__(self, "surface_area_m2", _located_value(self.surface_area_m2))


@dataclass(frozen=True)
class BenchReference:
    work_id: str | None
    cited_as: str
    for_parameters: tuple[str, ...]
    locator: Locator

    def __post_init__(self) -> None:
        if not self.cited_as.strip():
            raise ValueError("BenchReference.cited_as is required")
        if not isinstance(self.locator, Locator):
            raise ValueError("BenchReference.locator is required")


@dataclass(frozen=True)
class BenchIdentity:
    basis: BenchIdentityBasis
    ref: BenchReference | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        basis = BenchIdentityBasis(self.basis)
        object.__setattr__(self, "basis", basis)
        if basis is BenchIdentityBasis.INFERRED_FROM_EMBEDDED_EVIDENCE:
            if not self.reason or not self.reason.strip():
                raise ValueError("inferred bench identity requires an embedded-evidence reason")
        elif self.reason is not None:
            raise ValueError("only inferred bench identity carries an inference reason")
        if basis is BenchIdentityBasis.CITED_BY_AUTHOR:
            if self.ref is None:
                raise ValueError("cited_by_author BenchIdentity requires ref.cited_as")
            if not isinstance(self.ref, BenchReference):
                raise ValueError(
                    "cited_by_author BenchIdentity requires a BenchReference"
                )
            if not self.ref.cited_as.strip():
                raise ValueError("cited_by_author BenchIdentity requires ref.cited_as")
        elif self.ref is not None:
            raise ValueError("only cited_by_author BenchIdentity can carry ref")


@dataclass(frozen=True)
class BenchFact:
    name: str
    value: Located[Value]
    unit: str | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("BenchFact.name is required")
        object.__setattr__(self, "value", _located_value(self.value))
        _validate_bench_absence(self.value, f"bench.other_facts.{self.name}")


@dataclass(frozen=True)
class Bench:
    id: str
    work_id: str
    identity: BenchIdentity
    apparatus_family: Located[str] | None = None
    method: Located[str] | None = None
    cell_material_and_liner: Located[str] | None = None
    geometry: ApparatusGeometry | None = None
    pumping_type: Located[str] | None = None
    pumping_speed_m3_s: Located[Value] | None = None
    gauges: Mapping[str, Located[str]] | None = None
    detector: Located[str] | None = None
    ionization: Mapping[str, Located[Value | str]] | None = None
    temperature_measurement: Mapping[str, Located[str]] | None = None
    temperature_calibration: Mapping[str, Located[str]] | None = None
    heating_method: Located[str] | None = None
    other_facts: tuple[BenchFact, ...] = ()

    def __post_init__(self) -> None:
        if not self.id or not self.work_id:
            raise ValueError("Bench requires id and work_id")
        if not isinstance(self.identity, BenchIdentity):
            raise ValueError("Bench requires a BenchIdentity")
        object.__setattr__(
            self, "pumping_speed_m3_s", _located_value(self.pumping_speed_m3_s)
        )
        _validate_bench_absence(self)


@dataclass(frozen=True)
class ThermalRamp:
    rate_K_s: Located[Value]
    start_temperature_K: Located[Value] | None = None
    end_temperature_K: Located[Value] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "rate_K_s",
            _required_located_value(self.rate_K_s, "thermal_schedule.ramp.rate_K_s"),
        )
        for name in ("start_temperature_K", "end_temperature_K"):
            object.__setattr__(self, name, _located_value(getattr(self, name)))
        _validate_bench_absence(self, "thermal_schedule.ramp")


@dataclass(frozen=True)
class ThermalSetpoint:
    temperature_K: Located[Value]
    hold_duration_s: Located[Value] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "temperature_K",
            _required_located_value(
                self.temperature_K, "thermal_schedule.setpoint.temperature_K"
            ),
        )
        object.__setattr__(self, "hold_duration_s", _located_value(self.hold_duration_s))
        _validate_bench_absence(self, "thermal_schedule.setpoint")


@dataclass(frozen=True)
class ThermalPoint:
    time_s: Located[Value]
    temperature_K: Located[Value]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "time_s",
            _required_located_value(self.time_s, "thermal_schedule.point.time_s"),
        )
        object.__setattr__(
            self,
            "temperature_K",
            _required_located_value(
                self.temperature_K, "thermal_schedule.point.temperature_K"
            ),
        )
        _validate_bench_absence(self, "thermal_schedule.point")


@dataclass(frozen=True)
class ThermalSchedule:
    method: Located[str] | None = None
    ramps: tuple[ThermalRamp, ...] | None = None
    setpoints_and_holds: tuple[ThermalSetpoint, ...] | None = None
    total_duration_s: Located[Value] | None = None
    cooling_or_quench: Located[str] | None = None
    points: tuple[ThermalPoint, ...] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "total_duration_s", _located_value(self.total_duration_s))
        _validate_bench_absence(self, "thermal_schedule")


@dataclass(frozen=True)
class FO2Control:
    channel: State[FO2Channel]
    buffer: Located[str] | None = None
    oxygen_partial_pressure_Pa: Located[Value] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "oxygen_partial_pressure_Pa",
            _located_value(self.oxygen_partial_pressure_Pa),
        )


@dataclass(frozen=True)
class SweepGasComponent:
    species: str
    mole_fraction: State[Decimal]
    flow_sccm: State[Decimal]
    partial_pressure_Pa: State[Decimal]


@dataclass(frozen=True)
class SweepGas:
    species: str | None
    flow_sccm: State[Decimal]
    partial_pressure_Pa: State[Decimal]
    components: tuple[SweepGasComponent, ...] | None = None
    alternatives: tuple[SweepGas, ...] | None = None


@dataclass(frozen=True)
class FlowRegime:
    regime_class: State[RegimeClass]
    knudsen_number_orifice: Located[Value] | None = None
    knudsen_number_chamber: Located[Value] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "knudsen_number_orifice", _located_value(self.knudsen_number_orifice)
        )
        object.__setattr__(
            self, "knudsen_number_chamber", _located_value(self.knudsen_number_chamber)
        )


@dataclass(frozen=True)
class PressureEnvironment:
    total_pressure_Pa: Located[Value]
    sweep_gas: Located[SweepGas]
    regime: FlowRegime
    gauge: Mapping[str, Located[str]] | None = None
    pressure_profile: Located[tuple[tuple[Decimal, Decimal], ...]] | None = None
    pumping: Mapping[str, Located[Any]] | None = None
    cell_internal_pressure_note: Located[str] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "total_pressure_Pa", _located_value(self.total_pressure_Pa))


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
    bench_id: str | None = None
    # FK to the work record's context row holding this experiment's equipment
    # evidence (``<source_id>::context::<observation_id>``). Same convention as
    # bench_id: the record carries the reference; consumers resolve it against
    # the store's context rows. Values are never copied onto the experiment.
    equipment_context_id: str | None = None
    thermal_schedule: ThermalSchedule | None = None

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
    point_conditions: Mapping[str, Located[Any]] | None = None
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
    decision_band: DecisionBand | None
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
    rail: Rail | None
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
                notice.original,
                notice.source,
                notice.destination,
                notice.band,
                notice.dropped,
                notice.dropped_mass_fraction,
            )
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            out.append(notice)
    return tuple(out)
