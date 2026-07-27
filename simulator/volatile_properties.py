"""Phase-aware volatile-property registry and exact lookup facade.

This module is deliberately detached from the existing evaporation,
condensation, and thermal-train paths.  It owns property evidence and lookup
machinery only; callers decide what to do with a resolved value.

The authority guard prevents accidental or test-harness authority inflation:
only one loader-owned capability can certify rows, and ordinary post-init
registry assignment is refused.  It does not defend against malicious
in-process monkeypatching of module privates, ``sys.modules`` replacement,
``object.__setattr__``, or ``ctypes`` writes; that residual is accepted by design.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, fields
from enum import Enum
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, TypeAlias

import yaml

from simulator.accounting.formulas import (
    ATOMIC_WEIGHTS_G_PER_MOL,
    SpeciesFormula,
    load_species_formulas,
    resolve_species_formula,
)
from simulator.accounting.exceptions import UnknownSpeciesError


PropertyKind: TypeAlias = Literal[
    "saturation_pressure",
    "sublimation_pressure",
    "normal_boiling_point",
    "melting_point",
    "triple_point_temperature",
    "triple_point_pressure",
    "molar_mass",
]
PressureKind: TypeAlias = Literal[
    "saturation",
    "monomer_partial",
    "dissociation",
    "not_applicable",
]
AuthorityClass: TypeAlias = Literal["certified"]
_RowAuthorityClass: TypeAlias = Literal["certified", "non-certified"]

_PRESSURE_PROPERTY_KINDS = frozenset(
    {"saturation_pressure", "sublimation_pressure"}
)
_STATIC_PROPERTY_KINDS = frozenset(
    {
        "normal_boiling_point",
        "melting_point",
        "triple_point_temperature",
        "triple_point_pressure",
    }
)
_PROPERTY_KINDS = _PRESSURE_PROPERTY_KINDS | _STATIC_PROPERTY_KINDS | {
    "molar_mass"
}
_PRESSURE_KINDS = frozenset(
    {"saturation", "monomer_partial", "dissociation", "not_applicable"}
)
_CORRELATION_FAMILIES = frozenset(
    {"antoine", "feistel_wagner_ice", "fray_schmitt_polynomial"}
)
_SCHEMA_VERSION = "volatile-properties-v1"
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_REGISTRY_PATH = _PROJECT_ROOT / "data" / "volatile_properties.yaml"
_SIGNED_RUNTIME_REGISTRY_PATH = _DEFAULT_REGISTRY_PATH
_SPECIES_CATALOG_PATH = _PROJECT_ROOT / "data" / "species_catalog.yaml"
_ATOMIC_WEIGHT_AUTHORITY = (
    "CIAAW/NIST standard atomic weights in "
    "simulator.accounting.formulas.ATOMIC_WEIGHTS_G_PER_MOL"
)


class PropertyStatus(str, Enum):
    VALUE = "value"
    NO_DATA = "no_data"
    NONVOLATILE_BY_PHYSICS = "nonvolatile_by_physics"


class _RegistrySourceClass(str, Enum):
    SIGNED_RUNTIME = "signed-runtime"
    MAPPING = "mapping"
    TEST_PATH = "test-path"
    AD_HOC_PATH = "ad-hoc-path"


@dataclass(frozen=True)
class _RegistrySourceToken:
    source_class: _RegistrySourceClass


_SIGNED_RUNTIME_CAPABILITY = _RegistrySourceToken(
    _RegistrySourceClass.SIGNED_RUNTIME
)
_UNTRUSTED_SOURCE_TOKEN = _RegistrySourceToken(_RegistrySourceClass.MAPPING)


class _ProvenanceBoundRow:
    _declared_authority_class: str
    _source_token: _RegistrySourceToken

    @property
    def authority_class(self) -> _RowAuthorityClass:
        return _derived_authority(
            self._declared_authority_class,
            self._source_token,
        )


class NoDataReason(str, Enum):
    NO_CERTIFIED_ROW = "no_certified_row"
    OUT_OF_CERTIFIED_RANGE = "out_of_certified_range"
    COVERAGE_GAP = "coverage_gap"
    PHASE_MISMATCH = "phase_mismatch"
    PRESSURE_KIND_MISMATCH = "pressure_kind_mismatch"
    PROCESS_CONTEXT_MISMATCH = "process_context_mismatch"
    INVERSE_PRESSURE_OUT_OF_RANGE = "inverse_pressure_out_of_range"


PropertyQueryErrorCode: TypeAlias = Literal[
    "nonfinite_temperature",
    "nonpositive_temperature",
    "nonfinite_pressure",
    "nonpositive_pressure",
    "invalid_temperature_range",
    "unsupported_property_kind",
    "missing_selector",
    "unknown_selector",
    "unsupported_band_query",
    "missing_independent_variable",
    "ambiguous_independent_variable",
    "forbidden_independent_variable",
    "selector_not_applicable",
    "missing_process_context",
]


class PropertyQueryError(ValueError):
    """Invalid query shape, numeric input, or selector."""

    code: PropertyQueryErrorCode

    def __init__(self, code: PropertyQueryErrorCode, message: str):
        super().__init__(message)
        self.code = code


class PropertyRegistryError(ValueError):
    """Malformed, unsigned, or unsupported registry content."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class PropertyCoverageConflictError(PropertyRegistryError):
    """More than one row owns the same query point or sub-band."""

    def __init__(self, message: str, row_ids: Sequence[str] = ()):
        super().__init__("coverage_conflict", message)
        self.row_ids = tuple(sorted(str(row_id) for row_id in row_ids))


@dataclass(frozen=True)
class CorrelationEvidence:
    evidence_kind: Literal["correlation"]
    species: str
    property_kind: Literal["saturation_pressure", "sublimation_pressure"]
    queried_T_K: float | None
    queried_P_Pa: float | None
    value: float
    units: str
    row_id: str
    source_registry: str
    source_reference_id: str
    phase_branch: str
    pressure_kind: PressureKind
    valid_range_K: tuple[float, float] | None
    valid_range_inclusive: tuple[bool, bool] | None
    authority_class: AuthorityClass
    evaluation_tolerance_relative: float
    evaluation_tolerance_absolute: float
    evaluation_tolerance_absolute_units: str
    equilibrium_route_id: str | None
    equilibrium_route_segment_index: int | None
    equilibrium_process_context: str | None


@dataclass(frozen=True)
class StaticEvidence:
    evidence_kind: Literal["static"]
    species: str
    property_kind: Literal[
        "normal_boiling_point",
        "melting_point",
        "triple_point_temperature",
        "triple_point_pressure",
    ]
    value: float
    units: str
    row_id: str
    source_registry: str
    source_reference_id: str
    phase_branch: str
    pressure_kind: PressureKind
    authority_class: AuthorityClass
    evaluation_tolerance_relative: Literal[None] = None
    evaluation_tolerance_absolute: Literal[None] = None
    evaluation_tolerance_absolute_units: Literal[None] = None


@dataclass(frozen=True)
class VirtualMolarMassEvidence:
    evidence_kind: Literal["virtual_molar_mass"]
    species: str
    property_kind: Literal["molar_mass"]
    formula: str
    value: float
    units: Literal["g/mol"]
    resolver: Literal["resolve_species_formula"]
    atomic_weight_authority: str


@dataclass(frozen=True)
class ProcessNonvolatileEvidence:
    evidence_kind: Literal["process_nonvolatile"]
    species: str
    property_kind: Literal["process_nonvolatile"]
    process_context: str
    queried_T_K: float
    valid_process_range_K: tuple[float, float]
    valid_process_range_inclusive: tuple[bool, bool]
    criterion: str
    row_id: str
    source_registry: str
    source_reference_id: str
    authority_class: AuthorityClass


@dataclass(frozen=True)
class CorrelationCoverageEvidence:
    evidence_kind: Literal["correlation_coverage"]
    species: str
    property_kind: Literal["saturation_pressure", "sublimation_pressure"]
    queried_temperature_range_K: tuple[float, float]
    queried_temperature_range_inclusive: tuple[bool, bool]
    row_id: str
    source_registry: str
    source_reference_id: str
    phase_branch: str
    pressure_kind: PressureKind
    valid_range_K: tuple[float, float]
    valid_range_inclusive: tuple[bool, bool]
    authority_class: AuthorityClass
    equilibrium_route_id: str | None
    equilibrium_route_segment_index: int | None
    equilibrium_process_context: str | None


@dataclass(frozen=True)
class ProcessNonvolatileCoverageEvidence:
    evidence_kind: Literal["process_nonvolatile_coverage"]
    species: str
    property_kind: Literal["process_nonvolatile"]
    process_context: str
    queried_temperature_range_K: tuple[float, float]
    queried_temperature_range_inclusive: tuple[bool, bool]
    valid_process_range_K: tuple[float, float]
    valid_process_range_inclusive: tuple[bool, bool]
    criterion: str
    row_id: str
    source_registry: str
    source_reference_id: str
    authority_class: AuthorityClass


PropertyEvidence: TypeAlias = (
    CorrelationEvidence
    | StaticEvidence
    | VirtualMolarMassEvidence
    | ProcessNonvolatileEvidence
)
PropertyCoverageEvidence: TypeAlias = (
    CorrelationCoverageEvidence | ProcessNonvolatileCoverageEvidence
)


@dataclass(frozen=True)
class PropertyResult:
    status: PropertyStatus
    evidence: PropertyEvidence | None
    reason: NoDataReason | None

    def __post_init__(self) -> None:
        if self.status is PropertyStatus.NO_DATA:
            if self.evidence is not None or self.reason is None:
                raise ValueError("no-data results require a reason and no evidence")
        elif self.evidence is None or self.reason is not None:
            raise ValueError("positive results require evidence and no reason")


@dataclass(frozen=True)
class PropertyCoverageBand:
    temperature_range_K: tuple[float, float]
    range_inclusive: tuple[bool, bool]
    status: PropertyStatus
    evidence: PropertyCoverageEvidence | None
    reason: NoDataReason | None

    def __post_init__(self) -> None:
        low, high = self.temperature_range_K
        if low > high:
            raise ValueError("coverage band range is reversed")
        if self.status is PropertyStatus.NO_DATA:
            if self.evidence is not None or self.reason is None:
                raise ValueError("no-data bands require a reason and no evidence")
            return
        if self.evidence is None or self.reason is not None:
            raise ValueError("positive bands require evidence and no reason")
        if (
            self.evidence.queried_temperature_range_K
            != self.temperature_range_K
            or self.evidence.queried_temperature_range_inclusive
            != self.range_inclusive
        ):
            raise ValueError("coverage evidence must repeat its queried band exactly")


@dataclass(frozen=True)
class _Source:
    reference_id: str
    citation: str
    doi: str | None
    url: str | None
    locator: str


@dataclass(frozen=True)
class _Tolerance:
    relative: float
    absolute: float
    absolute_units: str


@dataclass(frozen=True)
class _CorrelationRow(_ProvenanceBoundRow):
    row_id: str
    species: str
    property_kind: str
    correlation_family: str
    coefficients: Mapping[str, Any]
    phase_branch: str
    pressure_kind: str
    valid_range_K: tuple[float, float]
    valid_range_inclusive: tuple[bool, bool]
    output_units: str
    source: _Source
    source_registry: str
    tolerance: _Tolerance
    _declared_authority_class: str
    _source_token: _RegistrySourceToken = field(
        default=_UNTRUSTED_SOURCE_TOKEN,
        repr=False,
        compare=False,
    )


@dataclass(frozen=True)
class _StaticRow(_ProvenanceBoundRow):
    row_id: str
    species: str
    property_kind: str
    value: float
    units: str
    phase_branch: str
    pressure_kind: str
    source: _Source
    source_registry: str
    _declared_authority_class: str
    _source_token: _RegistrySourceToken = field(
        default=_UNTRUSTED_SOURCE_TOKEN,
        repr=False,
        compare=False,
    )


@dataclass(frozen=True)
class _ProcessNonvolatileRow(_ProvenanceBoundRow):
    row_id: str
    species: str
    process_context: str
    valid_range_K: tuple[float, float]
    valid_range_inclusive: tuple[bool, bool]
    criterion: str
    source: _Source
    source_registry: str
    _declared_authority_class: str
    _source_token: _RegistrySourceToken = field(
        default=_UNTRUSTED_SOURCE_TOKEN,
        repr=False,
        compare=False,
    )


def _bind_source_token(
    row: Any,
    row_type: type[Any],
    source_token: _RegistrySourceToken,
) -> Any:
    values = {
        definition.name: getattr(row, definition.name)
        for definition in fields(row_type)
        if definition.name != "_source_token"
    }
    return row_type(**values, _source_token=source_token)


@dataclass(frozen=True)
class _RouteSegment:
    temperature_range_K: tuple[float, float]
    range_inclusive: tuple[bool, bool]
    property_kind: str
    phase_branch: str
    pressure_kind: str
    row_id: str
    segment_index: int


@dataclass(frozen=True)
class _EquilibriumRoute:
    route_id: str
    species: str
    process_context: str
    segments: tuple[_RouteSegment, ...]


@dataclass(frozen=True)
class _Selection:
    status: PropertyStatus
    row: _CorrelationRow | _ProcessNonvolatileRow | None = None
    reason: NoDataReason | None = None
    route_id: str | None = None
    route_segment_index: int | None = None
    process_context: str | None = None

    def signature(self) -> tuple[Any, ...]:
        return (
            self.status,
            None if self.row is None else self.row.row_id,
            self.reason,
            self.route_id,
            self.route_segment_index,
            self.process_context,
        )


def _fail(code: str, message: str) -> PropertyRegistryError:
    return PropertyRegistryError(code, message)


def _as_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _fail("invalid_schema", f"{label} must be a mapping")
    return value


def _as_sequence(value: Any, label: str) -> Sequence[Any]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise _fail("invalid_schema", f"{label} must be a sequence")
    return value


def _required_text(mapping: Mapping[str, Any], key: str, label: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise _fail("invalid_schema", f"{label}.{key} must be non-empty text")
    return value.strip()


def _reject_unknown(
    mapping: Mapping[str, Any], allowed: set[str], label: str
) -> None:
    unknown = set(mapping) - allowed
    if unknown:
        anchor_fields = {
            "anchor",
            "anchor_check",
            "expected_value",
            "expected_P_Pa",
        }
        leaked = unknown.intersection(anchor_fields)
        if leaked:
            raise _fail(
                "runtime_anchor_forbidden",
                f"{label} contains ground-truth anchor field(s): {sorted(leaked)}",
            )
        raise _fail("invalid_schema", f"{label} has unknown fields {sorted(unknown)}")


def _optional_sequence(
    mapping: Mapping[str, Any], key: str, label: str
) -> Sequence[Any]:
    return _as_sequence(mapping.get(key, ()), f"{label}.{key}")


def _finite_number(value: Any, label: str, *, positive: bool = False) -> float:
    if isinstance(value, bool):
        raise _fail("invalid_schema", f"{label} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise _fail("invalid_schema", f"{label} must be numeric") from exc
    if not math.isfinite(result) or (positive and result <= 0.0):
        qualifier = "positive and finite" if positive else "finite"
        raise _fail("invalid_schema", f"{label} must be {qualifier}")
    return result


def _range(
    mapping: Mapping[str, Any],
    range_key: str,
    inclusive_key: str,
    label: str,
) -> tuple[tuple[float, float], tuple[bool, bool]]:
    raw_range = _as_sequence(mapping.get(range_key), f"{label}.{range_key}")
    if len(raw_range) != 2:
        raise _fail("invalid_schema", f"{label}.{range_key} must have two values")
    low = _finite_number(raw_range[0], f"{label}.{range_key}[0]", positive=True)
    high = _finite_number(raw_range[1], f"{label}.{range_key}[1]", positive=True)
    if low >= high:
        raise _fail("invalid_schema", f"{label}.{range_key} must increase")
    raw_inclusive = _as_sequence(
        mapping.get(inclusive_key), f"{label}.{inclusive_key}"
    )
    if len(raw_inclusive) != 2 or any(
        not isinstance(value, bool) for value in raw_inclusive
    ):
        raise _fail(
            "invalid_schema", f"{label}.{inclusive_key} must contain two booleans"
        )
    return (low, high), (raw_inclusive[0], raw_inclusive[1])


def _source(mapping: Mapping[str, Any], label: str) -> _Source:
    source = _as_mapping(mapping.get("source"), f"{label}.source")
    forbidden = {"anchor", "anchor_check", "expected_value", "expected_P_Pa"}
    leaked = forbidden.intersection(mapping) | forbidden.intersection(source)
    if leaked:
        raise _fail(
            "runtime_anchor_forbidden",
            f"{label}.source contains ground-truth anchor field(s): {sorted(leaked)}",
        )
    expected = {"reference_id", "citation", "doi", "url", "locator"}
    if set(source) != expected:
        missing = expected - set(source)
        unknown = set(source) - expected
        raise _fail(
            "invalid_schema",
            f"{label}.source fields differ: missing={sorted(missing)} "
            f"unknown={sorted(unknown)}",
        )
    doi = source.get("doi")
    url = source.get("url")
    if doi is not None and not isinstance(doi, str):
        raise _fail("invalid_schema", f"{label}.source.doi must be text or null")
    if url is not None and not isinstance(url, str):
        raise _fail("invalid_schema", f"{label}.source.url must be text or null")
    return _Source(
        reference_id=_required_text(source, "reference_id", f"{label}.source"),
        citation=_required_text(source, "citation", f"{label}.source"),
        doi=None if doi is None else doi.strip() or None,
        url=None if url is None else url.strip() or None,
        locator=_required_text(source, "locator", f"{label}.source"),
    )


def _derived_authority(
    declared: str,
    source_token: _RegistrySourceToken,
) -> _RowAuthorityClass:
    if source_token is _SIGNED_RUNTIME_CAPABILITY and declared == "certified":
        return "certified"
    return "non-certified"


def _in_range(
    value: float,
    bounds: tuple[float, float],
    inclusive: tuple[bool, bool],
) -> bool:
    low_ok = value > bounds[0] or (inclusive[0] and value == bounds[0])
    high_ok = value < bounds[1] or (inclusive[1] and value == bounds[1])
    return low_ok and high_ok


def _ranges_overlap(
    left: tuple[float, float],
    left_inclusive: tuple[bool, bool],
    right: tuple[float, float],
    right_inclusive: tuple[bool, bool],
) -> bool:
    low = max(left[0], right[0])
    high = min(left[1], right[1])
    if low < high:
        return True
    if low > high:
        return False
    return _in_range(low, left, left_inclusive) and _in_range(
        low, right, right_inclusive
    )


def _evaluate_correlation(row: _CorrelationRow, T_K: float) -> float:
    coefficients = row.coefficients
    try:
        if row.correlation_family == "antoine":
            # Source-normalized Antoine form:
            # log10(P/Pa) = A - B / (T/K + C), so P_Pa = 10**exponent.
            exponent = (
                float(coefficients["A"])
                - float(coefficients["B"]) / (T_K + float(coefficients["C"]))
            )
            pressure = 10.0**exponent
        elif row.correlation_family == "feistel_wagner_ice":
            # Feistel-Wagner uses theta=T/T_t and
            # ln(P/P_t)=3/2 ln(theta)+(1-1/theta)*sum(e_i*theta**i).
            triple_T = float(coefficients["T_triple_K"])
            triple_P = float(coefficients["P_triple_Pa"])
            theta = T_K / triple_T
            polynomial = sum(
                float(coefficient) * theta**index
                for index, coefficient in enumerate(coefficients["e"])
            )
            ln_pressure_ratio = 1.5 * math.log(theta) + (
                1.0 - 1.0 / theta
            ) * polynomial
            pressure = triple_P * math.exp(ln_pressure_ratio)
        elif row.correlation_family == "fray_schmitt_polynomial":
            # Fray-Schmitt tabulates ln(P/bar)=sum(A_i/T**i).
            # Multiplication by exactly 1e5 performs the bar-to-Pa conversion.
            ln_pressure_bar = sum(
                float(coefficient) / T_K**index
                for index, coefficient in enumerate(coefficients["A"])
            )
            pressure = 1.0e5 * math.exp(ln_pressure_bar)
        else:  # pragma: no cover - loader closes this branch
            raise _fail(
                "unknown_correlation_family",
                f"unsupported correlation family {row.correlation_family!r}",
            )
    except (ArithmeticError, OverflowError, ValueError) as exc:
        raise _fail(
            "correlation_evaluation_failed",
            f"{row.row_id} cannot be evaluated at {T_K} K",
        ) from exc
    if not math.isfinite(pressure) or pressure <= 0.0:
        raise _fail(
            "correlation_evaluation_failed",
            f"{row.row_id} produced a non-positive or non-finite pressure",
        )
    return pressure


def _validate_coefficients(
    family: str, value: Any, label: str
) -> Mapping[str, Any]:
    coefficients = dict(_as_mapping(value, f"{label}.coefficients"))
    if family == "antoine":
        if set(coefficients) != {"A", "B", "C"}:
            raise _fail(
                "invalid_schema",
                f"{label}.coefficients must contain exactly A, B, C",
            )
        for key in ("A", "B", "C"):
            coefficients[key] = _finite_number(
                coefficients[key], f"{label}.coefficients.{key}"
            )
    elif family == "feistel_wagner_ice":
        if set(coefficients) != {"T_triple_K", "P_triple_Pa", "e"}:
            raise _fail(
                "invalid_schema",
                f"{label}.coefficients must contain T_triple_K, P_triple_Pa, e",
            )
        coefficients["T_triple_K"] = _finite_number(
            coefficients["T_triple_K"],
            f"{label}.coefficients.T_triple_K",
            positive=True,
        )
        coefficients["P_triple_Pa"] = _finite_number(
            coefficients["P_triple_Pa"],
            f"{label}.coefficients.P_triple_Pa",
            positive=True,
        )
        raw_e = _as_sequence(coefficients["e"], f"{label}.coefficients.e")
        if not raw_e:
            raise _fail("invalid_schema", f"{label}.coefficients.e is empty")
        coefficients["e"] = tuple(
            _finite_number(item, f"{label}.coefficients.e[{index}]")
            for index, item in enumerate(raw_e)
        )
    elif family == "fray_schmitt_polynomial":
        if set(coefficients) != {"A"}:
            raise _fail(
                "invalid_schema", f"{label}.coefficients must contain exactly A"
            )
        raw_a = _as_sequence(coefficients["A"], f"{label}.coefficients.A")
        if not raw_a:
            raise _fail("invalid_schema", f"{label}.coefficients.A is empty")
        coefficients["A"] = tuple(
            _finite_number(item, f"{label}.coefficients.A[{index}]")
            for index, item in enumerate(raw_a)
        )
    else:
        raise _fail(
            "unknown_correlation_family",
            f"{label}.correlation_family {family!r} is not implemented",
        )
    return MappingProxyType(coefficients)


def _binomial(n: int, k: int) -> float:
    return float(math.comb(n, k))


def _bernstein_coefficients(
    power_coefficients: Sequence[float],
    low: float,
    high: float,
) -> tuple[float, ...]:
    degree = len(power_coefficients) - 1
    width = high - low
    transformed = [0.0] * (degree + 1)
    for power, coefficient in enumerate(power_coefficients):
        for index in range(power + 1):
            transformed[index] += (
                coefficient
                * _binomial(power, index)
                * low ** (power - index)
                * width**index
            )
    return tuple(
        math.fsum(
            transformed[power]
            * _binomial(index, power)
            / _binomial(degree, power)
            for power in range(index + 1)
        )
        for index in range(degree + 1)
    )


def _polynomial_scale(
    power_coefficients: Sequence[float],
    low: float,
    high: float,
) -> float:
    magnitude = max(abs(low), abs(high))
    return max(
        1.0,
        math.fsum(
            abs(coefficient) * magnitude**power
            for power, coefficient in enumerate(power_coefficients)
        ),
    )


def _prove_polynomial_positive(
    power_coefficients: Sequence[float],
    low: float,
    high: float,
) -> bool:
    pending = [(low, high, 0)]
    examined = 0
    while pending:
        examined += 1
        if examined > 4096:
            return False
        interval_low, interval_high, depth = pending.pop()
        coefficients = _bernstein_coefficients(
            power_coefficients, interval_low, interval_high
        )
        scale = _polynomial_scale(
            power_coefficients, interval_low, interval_high
        )
        roundoff_guard = 512.0 * math.ulp(scale)
        if all(
            math.isfinite(coefficient) and coefficient > roundoff_guard
            for coefficient in coefficients
        ):
            continue
        if depth == 24:
            return False
        midpoint = interval_low + (interval_high - interval_low) / 2.0
        pending.append((interval_low, midpoint, depth + 1))
        pending.append((midpoint, interval_high, depth + 1))
    return True


def _trim_polynomial(coefficients: list[float]) -> tuple[float, ...]:
    while len(coefficients) > 1 and coefficients[-1] == 0.0:
        coefficients.pop()
    return tuple(coefficients)


def _log_pressure_derivative_polynomial(
    row: _CorrelationRow,
) -> tuple[tuple[float, ...], tuple[float, float]]:
    low, high = row.valid_range_K
    if row.correlation_family == "antoine":
        B = float(row.coefficients["B"])
        C = float(row.coefficients["C"])
        if low + C < 0.0 < high + C:
            raise _fail(
                "correlation_evaluation_failed",
                f"{row.row_id} has an in-band Antoine singularity",
            )
        if B <= 0.0:
            return (0.0,), (low, high)
        return (B,), (low, high)
    if row.correlation_family == "feistel_wagner_ice":
        triple_temperature = float(row.coefficients["T_triple_K"])
        values = tuple(float(value) for value in row.coefficients["e"])
        coefficients = [0.0] * (len(values) + 1)
        for index, value in enumerate(values):
            coefficients[index] += value
        coefficients[1] += 1.5
        for index, value in enumerate(values[1:], start=1):
            coefficients[index] -= index * value
            coefficients[index + 1] += index * value
        return _trim_polynomial(coefficients), (
            low / triple_temperature,
            high / triple_temperature,
        )
    values = tuple(float(value) for value in row.coefficients["A"])
    highest_power = len(values) - 1
    coefficients = [0.0] * max(1, highest_power)
    for index, value in enumerate(values[1:], start=1):
        coefficients[highest_power - index] -= index * value
    return _trim_polynomial(coefficients), (low, high)


def _validate_monotonic(row: _CorrelationRow) -> None:
    low, high = row.valid_range_K
    _evaluate_correlation(row, low)
    _evaluate_correlation(row, high)
    derivative, derivative_range = _log_pressure_derivative_polynomial(row)
    # Acceptance is sound over the whole band: each correlation family reduces
    # sign(d ln(P)/dT) to a polynomial on a positive domain.  Bernstein
    # coefficients bound that polynomial by convex hull; every accepted
    # recursive sub-band has all coefficients above a floating-point guard.
    # An inconclusive proof is rejected, so an unsampled negative pocket cannot
    # pass as it could under the former 65-point check.
    if not _prove_polynomial_positive(
        derivative, derivative_range[0], derivative_range[1]
    ):
        raise _fail(
            "nonmonotonic_correlation",
            f"{row.row_id} is not provably strictly increasing "
            "over its certified range",
        )


@lru_cache(maxsize=1)
def _catalog() -> tuple[
    Mapping[str, SpeciesFormula],
    Mapping[str, str],
    Mapping[str, Mapping[str, Any]],
    Mapping[str, str],
]:
    with _SPECIES_CATALOG_PATH.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    entries = raw.get("species")
    if not isinstance(entries, Sequence):
        raise _fail("invalid_species_catalog", "species catalog must contain a list")
    canonical_specs: dict[str, Mapping[str, Any]] = {}
    aliases: dict[str, str] = {}
    formula_texts: dict[str, str] = {}
    for raw_entry in entries:
        entry = _as_mapping(raw_entry, "species catalog entry")
        species = _required_text(entry, "id", "species catalog entry")
        if species in canonical_specs or species in aliases:
            raise _fail(
                "species_alias_collision", f"duplicate catalog identity {species!r}"
            )
        canonical_specs[species] = entry
        aliases[species] = species
        if isinstance(entry.get("formula"), str):
            formula_texts[species] = entry["formula"].strip()
        raw_aliases = entry.get("aliases", ())
        if raw_aliases is None:
            raw_aliases = ()
        if isinstance(raw_aliases, str):
            raw_aliases = (raw_aliases,)
        for raw_alias in _as_sequence(raw_aliases, f"aliases for {species}"):
            alias = str(raw_alias).strip()
            if not alias or alias in aliases:
                raise _fail(
                    "species_alias_collision",
                    f"catalog alias {alias!r} is duplicated",
                )
            aliases[alias] = species
    formulas = load_species_formulas(_SPECIES_CATALOG_PATH)
    return (
        MappingProxyType(formulas),
        MappingProxyType(aliases),
        MappingProxyType(canonical_specs),
        MappingProxyType(formula_texts),
    )


class VolatilePropertyRegistry:
    """Validated immutable registry plus exact point/band resolution."""

    def __setattr__(self, name: str, value: Any) -> None:
        state = object.__getattribute__(self, "__dict__")
        if state.get("_sealed", False):
            raise AttributeError(
                "VolatilePropertyRegistry is immutable after construction"
            )
        object.__setattr__(self, name, value)

    def __init__(
        self,
        *,
        correlations: Sequence[_CorrelationRow],
        static_rows: Sequence[_StaticRow],
        nonvolatile_rows: Sequence[_ProcessNonvolatileRow],
        routes: Sequence[_EquilibriumRoute],
        formula_registry: Mapping[str, SpeciesFormula],
        formula_texts: Mapping[str, str],
        aliases: Mapping[str, str],
    ):
        VolatilePropertyRegistry._materialize(
            self,
            correlations=correlations,
            static_rows=static_rows,
            nonvolatile_rows=nonvolatile_rows,
            routes=routes,
            formula_registry=formula_registry,
            formula_texts=formula_texts,
            aliases=aliases,
            source_token=_UNTRUSTED_SOURCE_TOKEN,
        )

    def _materialize(
        self,
        *,
        correlations: Sequence[_CorrelationRow],
        static_rows: Sequence[_StaticRow],
        nonvolatile_rows: Sequence[_ProcessNonvolatileRow],
        routes: Sequence[_EquilibriumRoute],
        formula_registry: Mapping[str, SpeciesFormula],
        formula_texts: Mapping[str, str],
        aliases: Mapping[str, str],
        source_token: _RegistrySourceToken,
    ) -> None:
        state = object.__getattribute__(self, "__dict__")
        if state.get("_sealed", False):
            raise AttributeError(
                "VolatilePropertyRegistry is immutable after construction"
            )
        bound_source_token = (
            _SIGNED_RUNTIME_CAPABILITY
            if source_token is _SIGNED_RUNTIME_CAPABILITY
            else _UNTRUSTED_SOURCE_TOKEN
        )
        correlations = tuple(
            _bind_source_token(row, _CorrelationRow, bound_source_token)
            for row in correlations
        )
        static_rows = tuple(
            _bind_source_token(row, _StaticRow, bound_source_token)
            for row in static_rows
        )
        nonvolatile_rows = tuple(
            _bind_source_token(
                row,
                _ProcessNonvolatileRow,
                bound_source_token,
            )
            for row in nonvolatile_rows
        )
        authority_rows = correlations + static_rows + nonvolatile_rows
        noncertified_row_ids = frozenset(
            row.row_id
            for row in authority_rows
            if row.authority_class != "certified"
        )
        certified_correlations = tuple(
            row for row in correlations if row.authority_class == "certified"
        )
        certified_static_rows = tuple(
            row for row in static_rows if row.authority_class == "certified"
        )
        certified_nonvolatile_rows = tuple(
            row for row in nonvolatile_rows if row.authority_class == "certified"
        )
        certified_correlation_ids = {
            row.row_id for row in certified_correlations
        }
        certified_routes = tuple(
            route
            for route in routes
            if all(
                segment.row_id in certified_correlation_ids
                for segment in route.segments
            )
        )
        object.__setattr__(
            self,
            "_noncertified_row_ids",
            noncertified_row_ids,
        )
        object.__setattr__(self, "_correlations", certified_correlations)
        object.__setattr__(self, "_static_rows", certified_static_rows)
        object.__setattr__(
            self,
            "_nonvolatile_rows",
            certified_nonvolatile_rows,
        )
        object.__setattr__(self, "_routes", certified_routes)
        object.__setattr__(
            self,
            "_formula_registry",
            MappingProxyType(dict(formula_registry)),
        )
        object.__setattr__(
            self,
            "_formula_texts",
            MappingProxyType(dict(formula_texts)),
        )
        object.__setattr__(
            self,
            "_aliases",
            MappingProxyType(dict(aliases)),
        )
        object.__setattr__(
            self,
            "_correlations_by_id",
            MappingProxyType(
                {row.row_id: row for row in certified_correlations}
            ),
        )
        object.__setattr__(self, "_sealed", True)

    @classmethod
    def load(
        cls,
        source: str | Path | Mapping[str, Any] = _DEFAULT_REGISTRY_PATH,
    ) -> "VolatilePropertyRegistry":
        source_path: Path | None
        if isinstance(source, (str, Path)):
            source_path = Path(source).resolve()
            if source_path.is_relative_to((_PROJECT_ROOT / "tests").resolve()):
                source_class = _RegistrySourceClass.TEST_PATH
            elif source_path == _SIGNED_RUNTIME_REGISTRY_PATH.resolve():
                source_class = _RegistrySourceClass.SIGNED_RUNTIME
            else:
                source_class = _RegistrySourceClass.AD_HOC_PATH
            if source_class is _RegistrySourceClass.TEST_PATH:
                raise _fail(
                    "registry_source_forbidden",
                    "public registry loading cannot read from tests/",
                )
            with source_path.open("r", encoding="utf-8") as handle:
                data = yaml.safe_load(handle) or {}
            try:
                source_registry = str(source_path.relative_to(_PROJECT_ROOT))
            except ValueError:
                source_registry = str(source_path)
        elif isinstance(source, Mapping):
            source_path = None
            data = source
            source_registry = "<mapping>"
            source_class = _RegistrySourceClass.MAPPING
        else:
            raise _fail("invalid_schema", "registry source must be a path or mapping")

        root = _as_mapping(data, "registry")
        if root.get("schema_version") != _SCHEMA_VERSION:
            raise _fail(
                "unsupported_schema",
                f"schema_version must be {_SCHEMA_VERSION!r}",
            )
        units = _as_mapping(root.get("units"), "registry.units")
        if dict(units) != {
            "temperature": "K",
            "pressure": "Pa",
            "molar_mass": "g/mol",
        }:
            raise _fail("invalid_units", "registry units must be K, Pa, and g/mol")
        allowed_root = {
            "schema_version",
            "units",
            "species",
            "equilibrium_routes",
            "legacy_adapter_rows",
            "nonvolatile_by_physics",
        }
        unknown_root = set(root) - allowed_root
        if unknown_root:
            raise _fail(
                "invalid_schema", f"unknown registry fields: {sorted(unknown_root)}"
            )

        formulas, aliases, catalog_specs, formula_texts = _catalog()
        correlations: list[_CorrelationRow] = []
        static_rows: list[_StaticRow] = []
        row_ids: set[str] = set()

        species_section = _as_mapping(root.get("species", {}), "registry.species")
        for species, raw_species_entry in species_section.items():
            if species not in catalog_specs:
                if species in aliases:
                    raise _fail(
                        "yaml_local_alias_forbidden",
                        f"registry species {species!r} is a catalog alias, not a bare id",
                    )
                raise _fail(
                    "unknown_species", f"registry species {species!r} is not cataloged"
                )
            species_entry = _as_mapping(
                raw_species_entry, f"registry.species.{species}"
            )
            allowed_species_fields = {"formula", "static_properties", "correlations"}
            unknown_species_fields = set(species_entry) - allowed_species_fields
            if unknown_species_fields:
                raise _fail(
                    "invalid_schema",
                    f"registry.species.{species} has unknown fields "
                    f"{sorted(unknown_species_fields)}",
                )
            expected_formula = catalog_specs[species].get("formula")
            formula = _required_text(
                species_entry, "formula", f"registry.species.{species}"
            )
            if expected_formula is not None and formula != str(expected_formula):
                raise _fail(
                    "formula_mismatch",
                    f"registry formula for {species!r} does not match species_catalog",
                )

            for index, raw_row in enumerate(
                _optional_sequence(
                    species_entry,
                    "static_properties",
                    f"registry.species.{species}",
                )
            ):
                label = f"registry.species.{species}.static_properties[{index}]"
                row = _as_mapping(raw_row, label)
                _reject_unknown(
                    row,
                    {
                        "row_kind",
                        "row_id",
                        "property_kind",
                        "value",
                        "units",
                        "phase_branch",
                        "pressure_kind",
                        "source",
                        "authority_class",
                    },
                    label,
                )
                if row.get("row_kind") != "static":
                    raise _fail("invalid_schema", f"{label}.row_kind must be static")
                row_id = _required_text(row, "row_id", label)
                cls._claim_row_id(row_id, row_ids)
                kind = _required_text(row, "property_kind", label)
                if kind not in _STATIC_PROPERTY_KINDS:
                    raise _fail(
                        "unsupported_property_kind",
                        f"{label}.property_kind {kind!r} is not a static kind",
                    )
                units_value = _required_text(row, "units", label)
                expected_units = (
                    "Pa" if kind == "triple_point_pressure" else "K"
                )
                if units_value != expected_units:
                    raise _fail(
                        "invalid_units",
                        f"{label}.units must be {expected_units}",
                    )
                pressure_kind = _required_text(row, "pressure_kind", label)
                if pressure_kind not in _PRESSURE_KINDS:
                    raise _fail("unknown_selector", f"{label} pressure kind is unknown")
                static_rows.append(
                    _StaticRow(
                        row_id=row_id,
                        species=species,
                        property_kind=kind,
                        value=_finite_number(
                            row.get("value"), f"{label}.value", positive=True
                        ),
                        units=units_value,
                        phase_branch=_required_text(row, "phase_branch", label),
                        pressure_kind=pressure_kind,
                        source=_source(row, label),
                        source_registry=source_registry,
                        _declared_authority_class=_required_text(
                            row, "authority_class", label
                        ),
                    )
                )

            for index, raw_row in enumerate(
                _optional_sequence(
                    species_entry,
                    "correlations",
                    f"registry.species.{species}",
                )
            ):
                label = f"registry.species.{species}.correlations[{index}]"
                row = _as_mapping(raw_row, label)
                correlation = cls._parse_correlation(
                    row,
                    label=label,
                    species=species,
                    source_registry=source_registry,
                )
                cls._claim_row_id(correlation.row_id, row_ids)
                correlations.append(correlation)

        for index, raw_adapter in enumerate(
            _optional_sequence(root, "legacy_adapter_rows", "registry")
        ):
            label = f"registry.legacy_adapter_rows[{index}]"
            adapter = _as_mapping(raw_adapter, label)
            _reject_unknown(
                adapter,
                {
                    "adapter_id",
                    "source_path",
                    "source_selector",
                    "species",
                    "property_kind",
                    "phase_branch",
                    "pressure_kind",
                    "valid_range_inclusive",
                },
                label,
            )
            adapter_id = _required_text(adapter, "adapter_id", label)
            cls._claim_row_id(adapter_id, row_ids)
            species = _required_text(adapter, "species", label)
            if species not in catalog_specs:
                raise _fail("unknown_species", f"{label}.species is not cataloged")
            source_file = Path(_required_text(adapter, "source_path", label))
            if not source_file.is_absolute():
                source_file = _PROJECT_ROOT / source_file
            source_file = source_file.resolve()
            if source_file != (
                _PROJECT_ROOT / "data" / "vapor_pressures.yaml"
            ).resolve():
                raise _fail(
                    "legacy_source_forbidden",
                    f"{label}.source_path must be data/vapor_pressures.yaml",
                )
            with source_file.open("r", encoding="utf-8") as handle:
                legacy_root = yaml.safe_load(handle) or {}
            selector = _required_text(adapter, "source_selector", label)
            if not selector.endswith(".pure_component_antoine"):
                raise _fail(
                    "legacy_selector_forbidden",
                    f"{label}.source_selector must select a pure_component_antoine row",
                )
            legacy_row: Any = legacy_root
            for token in selector.split("."):
                if not isinstance(legacy_row, Mapping) or token not in legacy_row:
                    raise _fail(
                        "legacy_selector_missing",
                        f"{label}.source_selector {selector!r} does not resolve",
                    )
                legacy_row = legacy_row[token]
            legacy = _as_mapping(legacy_row, f"{label} selected legacy row")
            if legacy.get("source_certification") not in {
                "source_equation_fit",
                "source_table_fit",
                "certified",
            }:
                raise _fail(
                    "uncertified_legacy_row",
                    f"{label} does not select a certified pure-component row",
                )
            legacy_range = _as_sequence(
                legacy.get("valid_range_K"), f"{label} legacy valid_range_K"
            )
            adapter_row = {
                "row_kind": "correlation",
                "row_id": adapter_id,
                "property_kind": _required_text(adapter, "property_kind", label),
                "correlation_family": "antoine",
                "coefficients": {
                    "A": legacy.get("A"),
                    "B": legacy.get("B"),
                    "C": legacy.get("C"),
                },
                "phase_branch": _required_text(adapter, "phase_branch", label),
                "pressure_kind": _required_text(adapter, "pressure_kind", label),
                "valid_range_K": list(legacy_range),
                "valid_range_inclusive": adapter.get(
                    "valid_range_inclusive", [True, True]
                ),
                "output_units": "Pa",
                "source": {
                    "reference_id": adapter_id,
                    "citation": _required_text(
                        legacy, "source", f"{label} selected legacy row"
                    ),
                    "doi": None,
                    "url": None,
                    "locator": f"{source_file.name}:{selector}",
                },
                "evaluation_tolerance": {
                    "relative": 1.0e-12,
                    "absolute_Pa": 1.0e-12,
                },
                "authority_class": "certified",
            }
            correlations.append(
                cls._parse_correlation(
                    adapter_row,
                    label=label,
                    species=species,
                    source_registry=f"{source_file}:{selector}",
                )
            )

        nonvolatile_rows: list[_ProcessNonvolatileRow] = []
        for index, raw_row in enumerate(
            _optional_sequence(root, "nonvolatile_by_physics", "registry")
        ):
            label = f"registry.nonvolatile_by_physics[{index}]"
            row = _as_mapping(raw_row, label)
            _reject_unknown(
                row,
                {
                    "row_kind",
                    "row_id",
                    "species",
                    "process_context",
                    "valid_process_range_K",
                    "valid_process_range_inclusive",
                    "criterion",
                    "source",
                    "authority_class",
                },
                label,
            )
            if row.get("row_kind") != "process_nonvolatile":
                raise _fail(
                    "invalid_schema", f"{label}.row_kind must be process_nonvolatile"
                )
            row_id = _required_text(row, "row_id", label)
            cls._claim_row_id(row_id, row_ids)
            species = _required_text(row, "species", label)
            if species not in catalog_specs:
                raise _fail("unknown_species", f"{label}.species is not cataloged")
            bounds, inclusive = _range(
                row,
                "valid_process_range_K",
                "valid_process_range_inclusive",
                label,
            )
            nonvolatile_rows.append(
                _ProcessNonvolatileRow(
                    row_id=row_id,
                    species=species,
                    process_context=_required_text(row, "process_context", label),
                    valid_range_K=bounds,
                    valid_range_inclusive=inclusive,
                    criterion=_required_text(row, "criterion", label),
                    source=_source(row, label),
                    source_registry=source_registry,
                    _declared_authority_class=_required_text(
                        row, "authority_class", label
                    ),
                )
            )

        correlations_by_id = {row.row_id: row for row in correlations}
        routes: list[_EquilibriumRoute] = []
        route_ids: set[str] = set()
        for route_index, raw_route in enumerate(
            _optional_sequence(root, "equilibrium_routes", "registry")
        ):
            label = f"registry.equilibrium_routes[{route_index}]"
            route = _as_mapping(raw_route, label)
            _reject_unknown(
                route,
                {"route_id", "species", "process_context", "segments"},
                label,
            )
            route_id = _required_text(route, "route_id", label)
            if route_id in route_ids:
                raise _fail("duplicate_route_id", f"duplicate route id {route_id!r}")
            route_ids.add(route_id)
            species = _required_text(route, "species", label)
            if species not in catalog_specs:
                raise _fail("unknown_species", f"{label}.species is not cataloged")
            segments: list[_RouteSegment] = []
            for segment_index, raw_segment in enumerate(
                _optional_sequence(route, "segments", label)
            ):
                segment_label = f"{label}.segments[{segment_index}]"
                segment = _as_mapping(raw_segment, segment_label)
                _reject_unknown(
                    segment,
                    {
                        "temperature_range_K",
                        "range_inclusive",
                        "property_kind",
                        "phase_branch",
                        "pressure_kind",
                        "promoted_row_id",
                    },
                    segment_label,
                )
                bounds, inclusive = _range(
                    segment,
                    "temperature_range_K",
                    "range_inclusive",
                    segment_label,
                )
                row_id = _required_text(segment, "promoted_row_id", segment_label)
                promoted = correlations_by_id.get(row_id)
                if promoted is None:
                    raise _fail(
                        "unknown_promoted_row",
                        f"{segment_label} names absent row {row_id!r}",
                    )
                kind = _required_text(segment, "property_kind", segment_label)
                phase = _required_text(segment, "phase_branch", segment_label)
                pressure_kind = _required_text(
                    segment, "pressure_kind", segment_label
                )
                if (
                    promoted.species != species
                    or promoted.property_kind != kind
                    or promoted.phase_branch != phase
                    or promoted.pressure_kind != pressure_kind
                ):
                    raise _fail(
                        "route_row_mismatch",
                        f"{segment_label} metadata differs from {row_id!r}",
                    )
                if bounds[0] < promoted.valid_range_K[0] or bounds[1] > promoted.valid_range_K[1]:
                    raise _fail(
                        "route_outside_row_range",
                        f"{segment_label} exceeds {row_id!r} certified range",
                    )
                for endpoint, included in zip(bounds, inclusive):
                    if included and not _in_range(
                        endpoint,
                        promoted.valid_range_K,
                        promoted.valid_range_inclusive,
                    ):
                        raise _fail(
                            "route_outside_row_range",
                            f"{segment_label} includes a row-excluded endpoint",
                        )
                segments.append(
                    _RouteSegment(
                        temperature_range_K=bounds,
                        range_inclusive=inclusive,
                        property_kind=kind,
                        phase_branch=phase,
                        pressure_kind=pressure_kind,
                        row_id=row_id,
                        segment_index=segment_index,
                    )
                )
            if not segments:
                raise _fail("invalid_schema", f"{label}.segments must not be empty")
            routes.append(
                _EquilibriumRoute(
                    route_id=route_id,
                    species=species,
                    process_context=_required_text(route, "process_context", label),
                    segments=tuple(segments),
                )
            )

        cls._validate_overlaps(correlations, static_rows, nonvolatile_rows, routes)
        registry = object.__new__(cls)
        VolatilePropertyRegistry._materialize(
            registry,
            correlations=correlations,
            static_rows=static_rows,
            nonvolatile_rows=nonvolatile_rows,
            routes=routes,
            formula_registry=formulas,
            formula_texts=formula_texts,
            aliases=aliases,
            source_token=(
                _SIGNED_RUNTIME_CAPABILITY
                if source_class is _RegistrySourceClass.SIGNED_RUNTIME
                else _UNTRUSTED_SOURCE_TOKEN
            ),
        )
        return registry

    @staticmethod
    def _claim_row_id(row_id: str, row_ids: set[str]) -> None:
        if row_id in row_ids:
            raise _fail("duplicate_row_id", f"duplicate row id {row_id!r}")
        row_ids.add(row_id)

    @staticmethod
    def _parse_correlation(
        row: Mapping[str, Any],
        *,
        label: str,
        species: str,
        source_registry: str,
    ) -> _CorrelationRow:
        _reject_unknown(
            row,
            {
                "row_kind",
                "row_id",
                "property_kind",
                "correlation_family",
                "coefficients",
                "phase_branch",
                "pressure_kind",
                "valid_range_K",
                "valid_range_inclusive",
                "output_units",
                "source",
                "evaluation_tolerance",
                "authority_class",
            },
            label,
        )
        if row.get("row_kind") != "correlation":
            raise _fail("invalid_schema", f"{label}.row_kind must be correlation")
        kind = _required_text(row, "property_kind", label)
        if kind not in _PRESSURE_PROPERTY_KINDS:
            raise _fail(
                "unsupported_property_kind",
                f"{label}.property_kind {kind!r} is not a pressure kind",
            )
        family = _required_text(row, "correlation_family", label)
        if family not in _CORRELATION_FAMILIES:
            raise _fail(
                "unknown_correlation_family",
                f"{label}.correlation_family {family!r} is not implemented",
            )
        pressure_kind = _required_text(row, "pressure_kind", label)
        if pressure_kind not in _PRESSURE_KINDS - {"not_applicable"}:
            raise _fail("unknown_selector", f"{label}.pressure_kind is unknown")
        bounds, inclusive = _range(
            row, "valid_range_K", "valid_range_inclusive", label
        )
        if row.get("output_units") != "Pa":
            raise _fail("invalid_units", f"{label}.output_units must be Pa")
        tolerance = _as_mapping(
            row.get("evaluation_tolerance"), f"{label}.evaluation_tolerance"
        )
        _reject_unknown(
            tolerance,
            {"relative", "absolute_Pa"},
            f"{label}.evaluation_tolerance",
        )
        parsed_tolerance = _Tolerance(
            relative=_finite_number(
                tolerance.get("relative"),
                f"{label}.evaluation_tolerance.relative",
            ),
            absolute=_finite_number(
                tolerance.get("absolute_Pa"),
                f"{label}.evaluation_tolerance.absolute_Pa",
            ),
            absolute_units="Pa",
        )
        if parsed_tolerance.relative < 0.0 or parsed_tolerance.absolute < 0.0:
            raise _fail(
                "invalid_schema", f"{label}.evaluation_tolerance cannot be negative"
            )
        parsed = _CorrelationRow(
            row_id=_required_text(row, "row_id", label),
            species=species,
            property_kind=kind,
            correlation_family=family,
            coefficients=_validate_coefficients(
                family, row.get("coefficients"), label
            ),
            phase_branch=_required_text(row, "phase_branch", label),
            pressure_kind=pressure_kind,
            valid_range_K=bounds,
            valid_range_inclusive=inclusive,
            output_units="Pa",
            source=_source(row, label),
            source_registry=source_registry,
            tolerance=parsed_tolerance,
            _declared_authority_class=_required_text(
                row, "authority_class", label
            ),
        )
        _validate_monotonic(parsed)
        return parsed

    @staticmethod
    def _validate_overlaps(
        correlations: Sequence[_CorrelationRow],
        static_rows: Sequence[_StaticRow],
        nonvolatile_rows: Sequence[_ProcessNonvolatileRow],
        routes: Sequence[_EquilibriumRoute],
    ) -> None:
        for index, left in enumerate(correlations):
            for right in correlations[index + 1 :]:
                if (
                    left.species,
                    left.property_kind,
                    left.phase_branch,
                    left.pressure_kind,
                ) != (
                    right.species,
                    right.property_kind,
                    right.phase_branch,
                    right.pressure_kind,
                ):
                    continue
                if _ranges_overlap(
                    left.valid_range_K,
                    left.valid_range_inclusive,
                    right.valid_range_K,
                    right.valid_range_inclusive,
                ):
                    raise PropertyCoverageConflictError(
                        f"correlation rows {left.row_id!r} and {right.row_id!r} overlap",
                        (left.row_id, right.row_id),
                    )
        seen_static: dict[tuple[str, str, str, str], str] = {}
        for row in static_rows:
            key = (
                row.species,
                row.property_kind,
                row.phase_branch,
                row.pressure_kind,
            )
            previous = seen_static.get(key)
            if previous is not None:
                raise PropertyCoverageConflictError(
                    f"static rows {previous!r} and {row.row_id!r} overlap",
                    (previous, row.row_id),
                )
            seen_static[key] = row.row_id

        route_segments: list[tuple[_EquilibriumRoute, _RouteSegment]] = [
            (route, segment) for route in routes for segment in route.segments
        ]
        for index, (left_route, left) in enumerate(route_segments):
            for right_route, right in route_segments[index + 1 :]:
                if (
                    left_route.species,
                    left_route.process_context,
                ) != (
                    right_route.species,
                    right_route.process_context,
                ):
                    continue
                if _ranges_overlap(
                    left.temperature_range_K,
                    left.range_inclusive,
                    right.temperature_range_K,
                    right.range_inclusive,
                ):
                    raise PropertyCoverageConflictError(
                        f"route segments {left_route.route_id!r}:{left.segment_index} "
                        f"and {right_route.route_id!r}:{right.segment_index} overlap",
                        (left.row_id, right.row_id),
                    )
        for index, left in enumerate(nonvolatile_rows):
            for right in nonvolatile_rows[index + 1 :]:
                if (
                    left.species,
                    left.process_context,
                ) != (
                    right.species,
                    right.process_context,
                ):
                    continue
                if _ranges_overlap(
                    left.valid_range_K,
                    left.valid_range_inclusive,
                    right.valid_range_K,
                    right.valid_range_inclusive,
                ):
                    raise PropertyCoverageConflictError(
                        f"nonvolatile rows {left.row_id!r} and {right.row_id!r} overlap",
                        (left.row_id, right.row_id),
                    )
        for route, segment in route_segments:
            for nonvolatile in nonvolatile_rows:
                if (
                    route.species,
                    route.process_context,
                ) != (
                    nonvolatile.species,
                    nonvolatile.process_context,
                ):
                    continue
                if _ranges_overlap(
                    segment.temperature_range_K,
                    segment.range_inclusive,
                    nonvolatile.valid_range_K,
                    nonvolatile.valid_range_inclusive,
                ):
                    raise PropertyCoverageConflictError(
                        f"route {route.route_id!r}:{segment.segment_index} overlaps "
                        f"nonvolatile row {nonvolatile.row_id!r}",
                        (segment.row_id, nonvolatile.row_id),
                    )

    def _canonical_species(self, species: str) -> str:
        if not isinstance(species, str) or not species.strip():
            return ""
        token = species.strip()
        return self._aliases.get(token, token)

    def _direct_selection(
        self,
        species: str,
        kind: str,
        T_K: float,
        phase_branch: str,
        pressure_kind: str,
        process_context: str | None,
    ) -> _Selection:
        if process_context is not None:
            selection = self._equilibrium_selection(
                species, T_K, process_context
            )
            if selection.status is not PropertyStatus.VALUE:
                return selection
            assert isinstance(selection.row, _CorrelationRow)
            row = selection.row
            if row.pressure_kind != pressure_kind:
                return _Selection(
                    PropertyStatus.NO_DATA,
                    reason=NoDataReason.PRESSURE_KIND_MISMATCH,
                )
            if row.phase_branch != phase_branch:
                return _Selection(
                    PropertyStatus.NO_DATA, reason=NoDataReason.PHASE_MISMATCH
                )
            if row.property_kind != kind:
                return _Selection(
                    PropertyStatus.NO_DATA,
                    reason=NoDataReason.NO_CERTIFIED_ROW,
                )
            return selection

        rows_for_kind = [
            row
            for row in self._correlations
            if row.species == species and row.property_kind == kind
        ]
        if not rows_for_kind:
            return _Selection(
                PropertyStatus.NO_DATA, reason=NoDataReason.NO_CERTIFIED_ROW
            )
        rows_for_pressure = [
            row for row in rows_for_kind if row.pressure_kind == pressure_kind
        ]
        if not rows_for_pressure:
            return _Selection(
                PropertyStatus.NO_DATA,
                reason=NoDataReason.PRESSURE_KIND_MISMATCH,
            )
        rows_for_phase = [
            row for row in rows_for_pressure if row.phase_branch == phase_branch
        ]
        if not rows_for_phase:
            return _Selection(
                PropertyStatus.NO_DATA, reason=NoDataReason.PHASE_MISMATCH
            )
        active = [
            row
            for row in rows_for_phase
            if _in_range(T_K, row.valid_range_K, row.valid_range_inclusive)
        ]
        if len(active) > 1:
            raise PropertyCoverageConflictError(
                f"multiple correlation rows own {species} at {T_K} K",
                [row.row_id for row in active],
            )
        if active:
            return _Selection(PropertyStatus.VALUE, row=active[0])
        reason = self._range_miss_reason(
            T_K,
            [(row.valid_range_K, row.valid_range_inclusive) for row in rows_for_phase],
        )
        return _Selection(PropertyStatus.NO_DATA, reason=reason)

    def _equilibrium_selection(
        self, species: str, T_K: float, process_context: str
    ) -> _Selection:
        matching_routes = [
            route
            for route in self._routes
            if route.species == species and route.process_context == process_context
        ]
        matching_nonvolatile = [
            row
            for row in self._nonvolatile_rows
            if row.species == species and row.process_context == process_context
        ]
        active_routes: list[tuple[_EquilibriumRoute, _RouteSegment]] = []
        for route in matching_routes:
            for segment in route.segments:
                if _in_range(
                    T_K, segment.temperature_range_K, segment.range_inclusive
                ):
                    active_routes.append((route, segment))
        active_nonvolatile = [
            row
            for row in matching_nonvolatile
            if _in_range(T_K, row.valid_range_K, row.valid_range_inclusive)
        ]
        if len(active_routes) + len(active_nonvolatile) > 1:
            raise PropertyCoverageConflictError(
                f"multiple equilibrium owners for {species} at {T_K} K",
                [segment.row_id for _, segment in active_routes]
                + [row.row_id for row in active_nonvolatile],
            )
        if active_nonvolatile:
            return _Selection(
                PropertyStatus.NONVOLATILE_BY_PHYSICS,
                row=active_nonvolatile[0],
                process_context=process_context,
            )
        if active_routes:
            route, segment = active_routes[0]
            return _Selection(
                PropertyStatus.VALUE,
                row=self._correlations_by_id[segment.row_id],
                route_id=route.route_id,
                route_segment_index=segment.segment_index,
                process_context=process_context,
            )
        other_context_exists = any(
            route.species == species for route in self._routes
        ) or any(row.species == species for row in self._nonvolatile_rows)
        if not matching_routes and not matching_nonvolatile:
            return _Selection(
                PropertyStatus.NO_DATA,
                reason=(
                    NoDataReason.PROCESS_CONTEXT_MISMATCH
                    if other_context_exists
                    else NoDataReason.NO_CERTIFIED_ROW
                ),
            )
        ranges = [
            (segment.temperature_range_K, segment.range_inclusive)
            for route in matching_routes
            for segment in route.segments
        ] + [
            (row.valid_range_K, row.valid_range_inclusive)
            for row in matching_nonvolatile
        ]
        return _Selection(
            PropertyStatus.NO_DATA,
            reason=self._range_miss_reason(T_K, ranges),
        )

    @staticmethod
    def _range_miss_reason(
        value: float,
        ranges: Sequence[tuple[tuple[float, float], tuple[bool, bool]]],
    ) -> NoDataReason:
        if not ranges:
            return NoDataReason.NO_CERTIFIED_ROW
        minimum = min(bounds[0] for bounds, _ in ranges)
        maximum = max(bounds[1] for bounds, _ in ranges)
        if minimum <= value <= maximum:
            return NoDataReason.COVERAGE_GAP
        return NoDataReason.OUT_OF_CERTIFIED_RANGE

    def _correlation_evidence(
        self,
        row: _CorrelationRow,
        *,
        queried_T_K: float | None,
        queried_P_Pa: float | None,
        value: float,
        units: str,
        selection: _Selection,
    ) -> CorrelationEvidence:
        return CorrelationEvidence(
            evidence_kind="correlation",
            species=row.species,
            property_kind=row.property_kind,
            queried_T_K=queried_T_K,
            queried_P_Pa=queried_P_Pa,
            value=value,
            units=units,
            row_id=row.row_id,
            source_registry=row.source_registry,
            source_reference_id=row.source.reference_id,
            phase_branch=row.phase_branch,
            pressure_kind=row.pressure_kind,
            valid_range_K=row.valid_range_K,
            valid_range_inclusive=row.valid_range_inclusive,
            authority_class=row.authority_class,
            evaluation_tolerance_relative=row.tolerance.relative,
            evaluation_tolerance_absolute=row.tolerance.absolute,
            evaluation_tolerance_absolute_units=row.tolerance.absolute_units,
            equilibrium_route_id=selection.route_id,
            equilibrium_route_segment_index=selection.route_segment_index,
            equilibrium_process_context=selection.process_context,
        )

    def property(
        self,
        species: str,
        kind: PropertyKind,
        *,
        T_K: float | None = None,
        P_Pa: float | None = None,
        phase_branch: str | None = None,
        pressure_kind: PressureKind | None = None,
        process_context: str | None = None,
    ) -> PropertyResult:
        kind = _validate_property_kind(kind)
        canonical = self._canonical_species(species)
        if kind == "molar_mass":
            if T_K is not None or P_Pa is not None:
                raise PropertyQueryError(
                    "forbidden_independent_variable",
                    "molar_mass accepts no independent variable",
                )
            if (
                phase_branch is not None
                or pressure_kind is not None
                or process_context is not None
            ):
                raise PropertyQueryError(
                    "selector_not_applicable", "molar_mass accepts no selectors"
                )
            if canonical not in self._formula_registry:
                return PropertyResult(
                    PropertyStatus.NO_DATA,
                    None,
                    NoDataReason.NO_CERTIFIED_ROW,
                )
            try:
                formula = resolve_species_formula(canonical, self._formula_registry)
            except UnknownSpeciesError:  # pragma: no cover - catalog load validates
                return PropertyResult(
                    PropertyStatus.NO_DATA,
                    None,
                    NoDataReason.NO_CERTIFIED_ROW,
                )
            return PropertyResult(
                PropertyStatus.VALUE,
                VirtualMolarMassEvidence(
                    evidence_kind="virtual_molar_mass",
                    species=canonical,
                    property_kind="molar_mass",
                    formula=self._formula_texts.get(
                        canonical, _formula_text(formula)
                    ),
                    value=formula.molar_mass_g_per_mol(ATOMIC_WEIGHTS_G_PER_MOL),
                    units="g/mol",
                    resolver="resolve_species_formula",
                    atomic_weight_authority=_ATOMIC_WEIGHT_AUTHORITY,
                ),
                None,
            )

        if kind in _STATIC_PROPERTY_KINDS:
            if T_K is not None or P_Pa is not None:
                raise PropertyQueryError(
                    "forbidden_independent_variable",
                    f"{kind} accepts no independent variable",
                )
            if process_context is not None:
                raise PropertyQueryError(
                    "selector_not_applicable",
                    f"{kind} does not accept process_context",
                )
            _require_selector(phase_branch, "phase_branch")
            _require_pressure_kind(pressure_kind)
            candidates = [
                row
                for row in self._static_rows
                if row.species == canonical and row.property_kind == kind
            ]
            if not candidates:
                return PropertyResult(
                    PropertyStatus.NO_DATA,
                    None,
                    NoDataReason.NO_CERTIFIED_ROW,
                )
            pressure_candidates = [
                row for row in candidates if row.pressure_kind == pressure_kind
            ]
            if not pressure_candidates:
                return PropertyResult(
                    PropertyStatus.NO_DATA,
                    None,
                    NoDataReason.PRESSURE_KIND_MISMATCH,
                )
            phase_candidates = [
                row for row in pressure_candidates if row.phase_branch == phase_branch
            ]
            if not phase_candidates:
                return PropertyResult(
                    PropertyStatus.NO_DATA,
                    None,
                    NoDataReason.PHASE_MISMATCH,
                )
            if len(phase_candidates) > 1:
                raise PropertyCoverageConflictError(
                    f"multiple static rows own {canonical} {kind}",
                    [row.row_id for row in phase_candidates],
                )
            row = phase_candidates[0]
            return PropertyResult(
                PropertyStatus.VALUE,
                StaticEvidence(
                    evidence_kind="static",
                    species=row.species,
                    property_kind=row.property_kind,
                    value=row.value,
                    units=row.units,
                    row_id=row.row_id,
                    source_registry=row.source_registry,
                    source_reference_id=row.source.reference_id,
                    phase_branch=row.phase_branch,
                    pressure_kind=row.pressure_kind,
                    authority_class=row.authority_class,
                ),
                None,
            )

        if T_K is None and P_Pa is None:
            raise PropertyQueryError(
                "missing_independent_variable",
                f"{kind} requires exactly one T_K",
            )
        if T_K is not None and P_Pa is not None:
            raise PropertyQueryError(
                "ambiguous_independent_variable",
                f"{kind} forward evaluation cannot receive both T_K and P_Pa",
            )
        if P_Pa is not None:
            _validate_pressure(P_Pa)
            raise PropertyQueryError(
                "forbidden_independent_variable",
                "forward pressure evaluation requires T_K, not P_Pa",
            )
        temperature = _validate_temperature(T_K)
        _require_selector(phase_branch, "phase_branch")
        _require_pressure_kind(pressure_kind)
        process_context = _validate_process_context(process_context)
        selection = self._direct_selection(
            canonical,
            kind,
            temperature,
            phase_branch,
            pressure_kind,
            process_context,
        )
        if selection.status is PropertyStatus.NO_DATA:
            return PropertyResult(selection.status, None, selection.reason)
        if selection.status is PropertyStatus.NONVOLATILE_BY_PHYSICS:
            assert isinstance(selection.row, _ProcessNonvolatileRow)
            row = selection.row
            return PropertyResult(
                selection.status,
                ProcessNonvolatileEvidence(
                    evidence_kind="process_nonvolatile",
                    species=row.species,
                    property_kind="process_nonvolatile",
                    process_context=row.process_context,
                    queried_T_K=temperature,
                    valid_process_range_K=row.valid_range_K,
                    valid_process_range_inclusive=row.valid_range_inclusive,
                    criterion=row.criterion,
                    row_id=row.row_id,
                    source_registry=row.source_registry,
                    source_reference_id=row.source.reference_id,
                    authority_class=row.authority_class,
                ),
                None,
            )
        assert isinstance(selection.row, _CorrelationRow)
        row = selection.row
        value = _evaluate_correlation(row, temperature)
        return PropertyResult(
            PropertyStatus.VALUE,
            self._correlation_evidence(
                row,
                queried_T_K=temperature,
                queried_P_Pa=None,
                value=value,
                units="Pa",
                selection=selection,
            ),
            None,
        )

    def property_bands(
        self,
        species: str,
        kind: Literal["saturation_pressure", "sublimation_pressure"],
        temperature_range_K: tuple[float, float],
        *,
        phase_branch: str,
        pressure_kind: PressureKind,
        process_context: str | None = None,
    ) -> tuple[PropertyCoverageBand, ...]:
        """Partition a closed query interval using exact row endpoint ownership."""
        kind = _validate_property_kind(kind)
        if kind not in _PRESSURE_PROPERTY_KINDS:
            raise PropertyQueryError(
                "unsupported_band_query", f"{kind} is not a band property"
            )
        low, high = _validate_temperature_range(temperature_range_K)
        _require_selector(phase_branch, "phase_branch")
        _require_pressure_kind(pressure_kind)
        canonical = self._canonical_species(species)
        process_context = _validate_process_context(process_context)
        if process_context is None:
            relevant = [
                row
                for row in self._correlations
                if row.species == canonical
                and row.property_kind == kind
                and row.phase_branch == phase_branch
                and row.pressure_kind == pressure_kind
            ]
            endpoints = [
                endpoint
                for row in relevant
                for endpoint in row.valid_range_K
                if low < endpoint < high
            ]

            def resolve(T_K: float) -> _Selection:
                return self._direct_selection(
                    canonical,
                    kind,
                    T_K,
                    phase_branch,
                    pressure_kind,
                    None,
                )

        else:
            endpoints = self._equilibrium_endpoints(
                canonical, process_context, low, high
            )

            def resolve(T_K: float) -> _Selection:
                return self._direct_selection(
                    canonical,
                    kind,
                    T_K,
                    phase_branch,
                    pressure_kind,
                    process_context,
                )

        return self._partition_bands(low, high, endpoints, resolve)

    def equilibrium_pressure_bands(
        self,
        species: str,
        temperature_range_K: tuple[float, float],
        *,
        process_context: str,
    ) -> tuple[PropertyCoverageBand, ...]:
        low, high = _validate_temperature_range(temperature_range_K)
        process_context = _validate_process_context(
            process_context, required=True
        )
        assert process_context is not None
        canonical = self._canonical_species(species)
        endpoints = self._equilibrium_endpoints(
            canonical, process_context, low, high
        )
        return self._partition_bands(
            low,
            high,
            endpoints,
            lambda temperature: self._equilibrium_selection(
                canonical, temperature, process_context
            ),
        )

    def _equilibrium_endpoints(
        self, species: str, process_context: str, low: float, high: float
    ) -> list[float]:
        route_endpoints = [
            endpoint
            for route in self._routes
            if route.species == species and route.process_context == process_context
            for segment in route.segments
            for endpoint in segment.temperature_range_K
            if low < endpoint < high
        ]
        nonvolatile_endpoints = [
            endpoint
            for row in self._nonvolatile_rows
            if row.species == species and row.process_context == process_context
            for endpoint in row.valid_range_K
            if low < endpoint < high
        ]
        return route_endpoints + nonvolatile_endpoints

    def _partition_bands(
        self,
        low: float,
        high: float,
        endpoints: Sequence[float],
        resolver: Callable[[float], _Selection],
    ) -> tuple[PropertyCoverageBand, ...]:
        cuts = sorted({low, high, *endpoints})
        cells: list[tuple[float, float, bool, bool, _Selection]] = []
        for index, point in enumerate(cuts):
            cells.append((point, point, True, True, resolver(point)))
            if index + 1 < len(cuts):
                right = cuts[index + 1]
                midpoint = point + (right - point) / 2.0
                cells.append(
                    (point, right, False, False, resolver(midpoint))
                )
        merged: list[tuple[float, float, bool, bool, _Selection]] = []
        for cell in cells:
            if (
                merged
                and merged[-1][1] == cell[0]
                and merged[-1][4].signature() == cell[4].signature()
            ):
                previous = merged[-1]
                merged[-1] = (
                    previous[0],
                    cell[1],
                    previous[2],
                    cell[3],
                    previous[4],
                )
            else:
                merged.append(cell)
        return tuple(
            self._coverage_band(
                selection,
                (band_low, band_high),
                (include_low, include_high),
            )
            for band_low, band_high, include_low, include_high, selection in merged
        )

    @staticmethod
    def _coverage_band(
        selection: _Selection,
        bounds: tuple[float, float],
        inclusive: tuple[bool, bool],
    ) -> PropertyCoverageBand:
        if selection.status is PropertyStatus.NO_DATA:
            return PropertyCoverageBand(
                bounds, inclusive, selection.status, None, selection.reason
            )
        if selection.status is PropertyStatus.NONVOLATILE_BY_PHYSICS:
            assert isinstance(selection.row, _ProcessNonvolatileRow)
            row = selection.row
            evidence: PropertyCoverageEvidence = ProcessNonvolatileCoverageEvidence(
                evidence_kind="process_nonvolatile_coverage",
                species=row.species,
                property_kind="process_nonvolatile",
                process_context=row.process_context,
                queried_temperature_range_K=bounds,
                queried_temperature_range_inclusive=inclusive,
                valid_process_range_K=row.valid_range_K,
                valid_process_range_inclusive=row.valid_range_inclusive,
                criterion=row.criterion,
                row_id=row.row_id,
                source_registry=row.source_registry,
                source_reference_id=row.source.reference_id,
                authority_class=row.authority_class,
            )
        else:
            assert isinstance(selection.row, _CorrelationRow)
            row = selection.row
            evidence = CorrelationCoverageEvidence(
                evidence_kind="correlation_coverage",
                species=row.species,
                property_kind=row.property_kind,
                queried_temperature_range_K=bounds,
                queried_temperature_range_inclusive=inclusive,
                row_id=row.row_id,
                source_registry=row.source_registry,
                source_reference_id=row.source.reference_id,
                phase_branch=row.phase_branch,
                pressure_kind=row.pressure_kind,
                valid_range_K=row.valid_range_K,
                valid_range_inclusive=row.valid_range_inclusive,
                authority_class=row.authority_class,
                equilibrium_route_id=selection.route_id,
                equilibrium_route_segment_index=selection.route_segment_index,
                equilibrium_process_context=selection.process_context,
            )
        return PropertyCoverageBand(
            bounds, inclusive, selection.status, evidence, None
        )

    def saturation_pressure(
        self,
        species: str,
        T_K: float,
        *,
        phase_branch: str,
        process_context: str | None = None,
    ) -> PropertyResult:
        return self.property(
            species,
            "saturation_pressure",
            T_K=T_K,
            phase_branch=phase_branch,
            pressure_kind="saturation",
            process_context=process_context,
        )

    def condensation_T(
        self,
        species: str,
        P_Pa: float,
        *,
        kind: Literal["saturation_pressure", "sublimation_pressure"],
        phase_branch: str,
        process_context: str | None = None,
    ) -> PropertyResult:
        kind = _validate_property_kind(kind)
        if kind not in _PRESSURE_PROPERTY_KINDS:
            raise PropertyQueryError(
                "unsupported_property_kind", f"{kind} cannot be inverted"
            )
        pressure = _validate_pressure(P_Pa)
        _require_selector(phase_branch, "phase_branch")
        canonical = self._canonical_species(species)
        selections: list[tuple[_Selection, tuple[float, float], tuple[bool, bool]]] = []
        if process_context is None:
            rows_for_kind = [
                row
                for row in self._correlations
                if row.species == canonical and row.property_kind == kind
            ]
            rows_for_pressure = [
                row for row in rows_for_kind if row.pressure_kind == "saturation"
            ]
            rows_for_phase = [
                row
                for row in rows_for_pressure
                if row.phase_branch == phase_branch
            ]
            for row in rows_for_phase:
                selections.append(
                    (
                        _Selection(PropertyStatus.VALUE, row=row),
                        row.valid_range_K,
                        row.valid_range_inclusive,
                    )
                )
            if not rows_for_kind:
                missing_reason = NoDataReason.NO_CERTIFIED_ROW
            elif not rows_for_pressure:
                missing_reason = NoDataReason.PRESSURE_KIND_MISMATCH
            elif not rows_for_phase:
                missing_reason = NoDataReason.PHASE_MISMATCH
            else:
                missing_reason = NoDataReason.NO_CERTIFIED_ROW
        else:
            process_context = _validate_process_context(process_context)
            assert process_context is not None
            matching_routes = [
                route
                for route in self._routes
                if route.species == canonical
                and route.process_context == process_context
            ]
            route_rows = [
                (route, segment, self._correlations_by_id[segment.row_id])
                for route in matching_routes
                for segment in route.segments
            ]
            kind_rows = [
                item for item in route_rows if item[2].property_kind == kind
            ]
            pressure_rows = [
                item
                for item in kind_rows
                if item[2].pressure_kind == "saturation"
            ]
            phase_rows = [
                item
                for item in pressure_rows
                if item[2].phase_branch == phase_branch
            ]
            for route, segment, row in phase_rows:
                selections.append(
                    (
                        _Selection(
                            PropertyStatus.VALUE,
                            row=row,
                            route_id=route.route_id,
                            route_segment_index=segment.segment_index,
                            process_context=process_context,
                        ),
                        segment.temperature_range_K,
                        segment.range_inclusive,
                    )
                )
            if not matching_routes:
                missing_reason = (
                    NoDataReason.PROCESS_CONTEXT_MISMATCH
                    if any(route.species == canonical for route in self._routes)
                    else NoDataReason.NO_CERTIFIED_ROW
                )
            elif not kind_rows:
                missing_reason = NoDataReason.NO_CERTIFIED_ROW
            elif not pressure_rows:
                missing_reason = NoDataReason.PRESSURE_KIND_MISMATCH
            elif not phase_rows:
                missing_reason = NoDataReason.PHASE_MISMATCH
            else:
                missing_reason = NoDataReason.NO_CERTIFIED_ROW
        if not selections:
            return PropertyResult(PropertyStatus.NO_DATA, None, missing_reason)
        pressure_candidates: list[
            tuple[_Selection, tuple[float, float], tuple[bool, bool]]
        ] = []
        for selection, bounds, inclusive in selections:
            assert isinstance(selection.row, _CorrelationRow)
            lower_pressure = _evaluate_correlation(selection.row, bounds[0])
            upper_pressure = _evaluate_correlation(selection.row, bounds[1])
            lower_ok = pressure > lower_pressure or (
                inclusive[0] and pressure == lower_pressure
            )
            upper_ok = pressure < upper_pressure or (
                inclusive[1] and pressure == upper_pressure
            )
            if lower_ok and upper_ok:
                pressure_candidates.append((selection, bounds, inclusive))
        if len(pressure_candidates) > 1:
            raise PropertyCoverageConflictError(
                f"pressure {pressure} Pa maps to multiple {canonical} branches",
                [
                    selection.row.row_id
                    for selection, _, _ in pressure_candidates
                    if selection.row is not None
                ],
            )
        if not pressure_candidates:
            return PropertyResult(
                PropertyStatus.NO_DATA,
                None,
                NoDataReason.INVERSE_PRESSURE_OUT_OF_RANGE,
            )
        selection, bounds, _ = pressure_candidates[0]
        assert isinstance(selection.row, _CorrelationRow)
        row = selection.row
        low, high = bounds
        for _ in range(160):
            midpoint = low + (high - low) / 2.0
            midpoint_pressure = _evaluate_correlation(row, midpoint)
            if midpoint_pressure < pressure:
                low = midpoint
            else:
                high = midpoint
        temperature = low + (high - low) / 2.0
        round_trip = _evaluate_correlation(row, temperature)
        # Solver-consistency guard only. Physical accuracy is established by
        # independent off-normalization literature anchors, not this same-model
        # forward/inverse round trip.
        if not math.isclose(
            round_trip,
            pressure,
            rel_tol=row.tolerance.relative,
            abs_tol=row.tolerance.absolute,
        ):
            raise _fail(
                "inverse_round_trip_failed",
                f"{row.row_id} inverse did not meet its evaluation tolerance",
            )
        return PropertyResult(
            PropertyStatus.VALUE,
            self._correlation_evidence(
                row,
                queried_T_K=temperature,
                queried_P_Pa=pressure,
                value=temperature,
                units="K",
                selection=selection,
            ),
            None,
        )


def _formula_text(formula: SpeciesFormula) -> str:
    return "".join(
        element + ("" if count == 1 else f"{count:g}")
        for element, count in formula.elements.items()
    )


def _validate_property_kind(kind: Any) -> str:
    if not isinstance(kind, str) or kind not in _PROPERTY_KINDS:
        raise PropertyQueryError(
            "unsupported_property_kind", f"unsupported property kind {kind!r}"
        )
    return str(kind)


def _validate_temperature(value: Any) -> float:
    if isinstance(value, bool):
        raise PropertyQueryError(
            "nonfinite_temperature", "temperature must be finite"
        )
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise PropertyQueryError(
            "nonfinite_temperature", "temperature must be finite"
        ) from exc
    if not math.isfinite(result):
        raise PropertyQueryError(
            "nonfinite_temperature", "temperature must be finite"
        )
    if result <= 0.0:
        raise PropertyQueryError(
            "nonpositive_temperature", "temperature must be positive"
        )
    return result


def _validate_pressure(value: Any) -> float:
    if isinstance(value, bool):
        raise PropertyQueryError("nonfinite_pressure", "pressure must be finite")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise PropertyQueryError(
            "nonfinite_pressure", "pressure must be finite"
        ) from exc
    if not math.isfinite(result):
        raise PropertyQueryError("nonfinite_pressure", "pressure must be finite")
    if result <= 0.0:
        raise PropertyQueryError("nonpositive_pressure", "pressure must be positive")
    return result


def _validate_temperature_range(value: Any) -> tuple[float, float]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise PropertyQueryError(
            "invalid_temperature_range",
            "temperature_range_K must contain two values",
        )
    if len(value) != 2:
        raise PropertyQueryError(
            "invalid_temperature_range",
            "temperature_range_K must contain two values",
        )
    if isinstance(value[0], bool) or isinstance(value[1], bool):
        raise PropertyQueryError(
            "invalid_temperature_range",
            "temperature range values must be finite",
        )
    try:
        low = float(value[0])
        high = float(value[1])
    except (TypeError, ValueError) as exc:
        raise PropertyQueryError(
            "invalid_temperature_range",
            "temperature range values must be finite",
        ) from exc
    if not math.isfinite(low) or not math.isfinite(high) or low <= 0.0 or low >= high:
        raise PropertyQueryError(
            "invalid_temperature_range",
            "temperature range must be finite, positive, and increasing",
        )
    return low, high


def _require_selector(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PropertyQueryError("missing_selector", f"{name} is required")
    return value.strip()


def _require_pressure_kind(value: Any) -> str:
    token = _require_selector(value, "pressure_kind")
    if token not in _PRESSURE_KINDS:
        raise PropertyQueryError(
            "unknown_selector", f"unknown pressure_kind {token!r}"
        )
    return token


def _validate_process_context(
    value: Any,
    *,
    required: bool = False,
) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        raise PropertyQueryError(
            "missing_process_context", "process_context must be non-empty text"
        )
    return value


@lru_cache(maxsize=1)
def _default_registry() -> VolatilePropertyRegistry:
    return VolatilePropertyRegistry.load(_DEFAULT_REGISTRY_PATH)


def property(
    species: str,
    kind: PropertyKind,
    *,
    T_K: float | None = None,
    P_Pa: float | None = None,
    phase_branch: str | None = None,
    pressure_kind: PressureKind | None = None,
    process_context: str | None = None,
) -> PropertyResult:
    return _default_registry().property(
        species,
        kind,
        T_K=T_K,
        P_Pa=P_Pa,
        phase_branch=phase_branch,
        pressure_kind=pressure_kind,
        process_context=process_context,
    )


def property_bands(
    species: str,
    kind: Literal["saturation_pressure", "sublimation_pressure"],
    temperature_range_K: tuple[float, float],
    *,
    phase_branch: str,
    pressure_kind: PressureKind,
    process_context: str | None = None,
) -> tuple[PropertyCoverageBand, ...]:
    """Partition a closed query interval using exact row endpoint ownership."""
    return _default_registry().property_bands(
        species,
        kind,
        temperature_range_K,
        phase_branch=phase_branch,
        pressure_kind=pressure_kind,
        process_context=process_context,
    )


def equilibrium_pressure_bands(
    species: str,
    temperature_range_K: tuple[float, float],
    *,
    process_context: str,
) -> tuple[PropertyCoverageBand, ...]:
    return _default_registry().equilibrium_pressure_bands(
        species,
        temperature_range_K,
        process_context=process_context,
    )


def saturation_pressure(
    species: str,
    T_K: float,
    *,
    phase_branch: str,
    process_context: str | None = None,
) -> PropertyResult:
    return _default_registry().saturation_pressure(
        species,
        T_K,
        phase_branch=phase_branch,
        process_context=process_context,
    )


def condensation_T(
    species: str,
    P_Pa: float,
    *,
    kind: Literal["saturation_pressure", "sublimation_pressure"],
    phase_branch: str,
    process_context: str | None = None,
) -> PropertyResult:
    return _default_registry().condensation_T(
        species,
        P_Pa,
        kind=kind,
        phase_branch=phase_branch,
        process_context=process_context,
    )
