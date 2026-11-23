"""Shared parsing and interpretation of printed uncertainty declarations."""

import re
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation

from simulator.battery.records import as_decimal


# Two-sided normal coverage factor for a 95% interval.
NORMAL_95_TICKET_MULTIPLIER = Decimal("1.959963984540054")
_CONVENTION_FACTORS: dict[str, tuple[str, Decimal | None]] = {
    "one_sigma": ("one_sigma", Decimal(1)),
    "two_sigma": ("two_sigma", Decimal(2)),
    "ci95": ("ci95", NORMAL_95_TICKET_MULTIPLIER),
    "95%": ("ci95", NORMAL_95_TICKET_MULTIPLIER),
    "range_half_width": ("range_half_width", None),
}
_COVERAGE_FACTOR_TOLERANCE = Decimal("0.001")
_DESCRIPTIVE_KINDS = frozenset(
    {
        "absolute",
        "categorical",
        "derived",
        "point",
        "printed",
        "read",
        "replicate_error",
        "standard_deviation",
    }
)
_NO_DECLARATION = frozenset(
    {"", "none", "not", "not stated", "not_stated", "unknown", "unspecified"}
)


class UncertaintyConventionConflict(ValueError):
    """Raised when declared uncertainty conventions imply different factors."""


UNCERTAINTY_NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
UNCERTAINTY_RANGE_RE = re.compile(
    rf"[\[(]\s*({UNCERTAINTY_NUMBER})\s*[,;–—]\s*({UNCERTAINTY_NUMBER})\s*[\])]"
)
UNCERTAINTY_PAIR_RE = re.compile(
    rf"({UNCERTAINTY_NUMBER})\s+(?:–|—|to)\s+({UNCERTAINTY_NUMBER})",
    re.IGNORECASE,
)
UNCERTAINTY_SCALAR_RE = re.compile(UNCERTAINTY_NUMBER)
_PRINTED_WIDTH_FIELDS = (
    "T_m_uncertainty_K",
    "gamma_uncertainty",
    "two_sigma",
    "limits",
    "source_uncertainty",
    "value_uncertainty",
    "read_uncertainty",
    "read_uncertainty_K",
    "log10_K_star_uncertainty",
    "S_298_over_R_uncertainty",
    "invT_times_1e4_K_inv_absolute_uncertainty",
)


def uncertainty_scalar_from_text(raw: str) -> str | None:
    match = re.search(rf"(?:±|\+/-)\s*({UNCERTAINTY_NUMBER})", raw)
    if match is not None:
        return match.group(1)
    match = re.search(
        rf"(?:standard\s+error|standard\s+deviation|\bsigma\b|\berror\b|\buncertainty\b)\s*[:=]?\s*({UNCERTAINTY_NUMBER})",
        raw,
        re.IGNORECASE,
    )
    if match is not None:
        return match.group(1)
    without_kind = re.sub(
        r"\b95\s*%\s*(?:ci|confidence interval)\b", " ", raw,
        flags=re.IGNORECASE,
    )
    without_kind = re.sub(
        r"\b95\s*(?:ci|confidence interval)\b|\b[12]\s*-?\s*(?:sigma|σ)\b",
        " ", without_kind, flags=re.IGNORECASE,
    )
    match = UNCERTAINTY_SCALAR_RE.search(without_kind)
    return None if match is None else match.group(0)


def mapping_uncertainty_value(
    mapping: Mapping[str, object], *, exact_fields: Sequence[str] = ()
) -> tuple[object | None, str | None]:
    """Select a width from the observation's exact field, then generic fields.

    Deliberately does not recurse: locator and provenance mappings are not width
    declarations, even when one of their keys happens to contain a unit token.
    """
    for name in exact_fields:
        if name not in mapping:
            continue
        value = mapping[name]
        if isinstance(value, (tuple, list)) and len(value) == 2:
            return value, "range"
        try:
            as_decimal(value)
        except (TypeError, ValueError, ArithmeticError, InvalidOperation):
            if isinstance(value, str):
                scalar = uncertainty_scalar_from_text(value)
                if scalar is not None:
                    unit = _unit_from_width_text(value)
                    return scalar, unit
            return None, None
        return value, None
    for name in ("value", "amount", "error_value", "uncertainty_value"):
        if name not in mapping:
            continue
        value = mapping[name]
        if isinstance(value, (tuple, list)) and len(value) == 2:
            return value, "range"
        try:
            as_decimal(value)
        except (TypeError, ValueError, ArithmeticError, InvalidOperation):
            if isinstance(value, str):
                scalar = uncertainty_scalar_from_text(value)
                if scalar is not None:
                    unit = _unit_from_width_text(value)
                    return scalar, unit
            continue
        return value, None
    for key in _PRINTED_WIDTH_FIELDS:
        if key not in mapping:
            continue
        value = mapping[key]
        if isinstance(value, (tuple, list)) and len(value) == 2:
            return value, "range"
        try:
            as_decimal(value)
        except (TypeError, ValueError, ArithmeticError, InvalidOperation):
            if isinstance(value, str):
                scalar = uncertainty_scalar_from_text(value)
                if scalar is not None:
                    return scalar, _unit_from_width_text(value)
            continue
        return value, None
    for key, value in mapping.items():
        name = str(key).casefold()
        if name in {
            "sigma", "std", "standard_deviation", "standard_error",
            "uncertainty", "error", "width", "range", "bounds", "interval",
            "limits",
        }:
            if isinstance(value, (tuple, list)) and len(value) == 2:
                return value, "range"
            try:
                as_decimal(value)
            except (TypeError, ValueError, ArithmeticError, InvalidOperation):
                if isinstance(value, str):
                    scalar = uncertainty_scalar_from_text(value)
                    if scalar is not None:
                        unit = _unit_from_width_text(value)
                        return scalar, unit
                continue
            return value, None
    return None, None


def _unit_from_width_text(raw: str) -> str | None:
    width = re.search(
        rf"(?:±|\+/-)\s*{UNCERTAINTY_NUMBER}\s*([A-Za-z%]+)", raw
    )
    if width is None:
        width = re.search(rf"{UNCERTAINTY_NUMBER}\s*([A-Za-z%]+)\s*$", raw)
    unit_text = width.group(1) if width is not None else raw
    if re.fullmatch(r"%|percent|percentage", unit_text, re.IGNORECASE):
        return "%"
    match = re.search(r"\b(dex|dimensionless|fraction)\b", unit_text, re.IGNORECASE)
    return None if match is None else match.group(1)


def mapping_uncertainty_unit(mapping: Mapping[str, object]) -> str | None:
    for key, value in mapping.items():
        if str(key).casefold() in {"unit", "units", "basis_unit"} and isinstance(value, str):
            return value
    return None


def paired_width_unit(printed_value: str | None) -> str | None:
    """Read the shared unit only from a value's printed parenthetical width."""
    if printed_value is not None and re.search(
        r"\d\s*\(\s*(?:±|\+/-)?\s*\d+(?:\.\d+)?\s*\)\s*%",
        printed_value,
    ):
        return "%"
    return None


def uncertainty_exact_fields(
    quantity: object,
    *,
    derivation_parameters: Sequence[tuple[str, object]] = (),
    subtype: object = None,
) -> tuple[str, ...]:
    """Return scorer and migrator width fields in the shared precedence order."""
    fields = [
        name.removeprefix("original_")
        for name, _ in derivation_parameters
        if name.startswith("original_")
    ]
    subtype_token = subtype
    if hasattr(subtype, "is_value"):
        subtype_token = subtype.value if subtype.is_value else None
    subtype_token = getattr(subtype_token, "value", subtype_token)
    if isinstance(subtype_token, str) and subtype_token:
        fields.append(subtype_token)
    token = getattr(quantity, "value", quantity)
    if isinstance(token, str):
        fields.append(token)
    return tuple(fields)


def resolve_width_unit_basis(
    unit: str | None,
    *,
    inline_unit: str | None = None,
    expected_unit: str | None = None,
    declared_basis: str | None = None,
    printed_value: str | None = None,
    paired_value: str | None = None,
    basis_evidence: str = "",
) -> tuple[str | None, str | None]:
    """Resolve width basis; the explicit mapping unit wins when it agrees.

    Inline units fill a missing explicit unit. Disagreement is returned as a
    typed conflict instead of choosing either declaration.
    """
    normalized = (unit or "").strip().casefold()
    inline_normalized = (inline_unit or "").strip().casefold()
    unit_aliases = {
        "%": "%",
        "percent": "%",
        "percentage": "%",
        "atom%": "atom%",
        "atom %": "atom%",
        "dimensionless": "dimensionless",
        "fraction": "fraction",
        "1": "1",
        "dex": "dex",
    }
    if (
        normalized
        and inline_normalized
        and unit_aliases.get(normalized, normalized)
        != unit_aliases.get(inline_normalized, inline_normalized)
    ):
        return None, (
            "width unit declarations disagree: "
            f"{unit!r} and {inline_unit!r}"
        )
    normalized = normalized or inline_normalized
    declared = (declared_basis or "").strip().casefold()
    if declared.startswith("uncertainty_convention_conflict:"):
        return None, declared.partition(":")[2]
    evidence = f"{basis_evidence} {printed_value or ''}".casefold()
    relative = declared == "relative" or bool(re.search(r"\brelative\b", evidence))
    absolute = declared in {"absolute", "dimensionless"} or bool(
        re.search(r"percentage\s*points?", evidence)
    ) or "atom%" in evidence or "atom %" in evidence
    if normalized in {"%", "percent", "percentage", "atom%", "atom %"}:
        if relative:
            return "relative", None
        if absolute or (
            paired_width_unit(paired_value) is not None
        ):
            return "dimensionless", None
        return None, None
    if normalized in {"dimensionless", "fraction", "1"}:
        return ("relative" if relative else "dimensionless"), None
    if normalized == "dex":
        if expected_unit and expected_unit.casefold() in {
            "dimensionless", "fraction", "1"
        }:
            return None, f"dex width is incompatible with {expected_unit} value"
        if relative:
            return None, "relative basis conflicts with dex width unit"
        return "dex", None
    return None, None


def printed_uncertainty_convention(
    declaration: object,
    *,
    fallback_token: str | None = None,
) -> tuple[str | None, Decimal | None] | None:
    """Return the stated confidence token and its coverage factor, if valid.

    ``None`` means no convention was declared. Unsupported tokens, invalid
    factors, and conflicting usable declarations raise
    ``UncertaintyConventionConflict``. Factors within 0.001 agree, allowing
    a printed ``k=1.96`` to agree with the normal 95% factor.
    A numeric ``k`` remains an exact coverage factor; its confidence token is
    the matching sigma token when k is one or two, otherwise one_sigma after
    the width has been reduced to standard uncertainty by the caller.
    """
    if not isinstance(declaration, Mapping):
        if fallback_token is None:
            return None
        declaration = {}

    factors: list[Decimal] = []
    numeric_factor: Decimal | None = None
    confidence_token: str | None = None
    declared_range = False
    for key in ("confidence_level", "kind"):
        raw_token = declaration.get(key)
        if raw_token is None:
            continue
        if not isinstance(raw_token, str):
            raise UncertaintyConventionConflict(f"{key} must be a string")
        token = raw_token.strip().lower()
        if token in _NO_DECLARATION:
            continue
        if key == "kind" and token in _DESCRIPTIVE_KINDS:
            continue
        if token not in _CONVENTION_FACTORS:
            raise UncertaintyConventionConflict(f"unrecognized {key} token {token!r}")
        canonical_token, mapped_factor = _CONVENTION_FACTORS[token]
        if mapped_factor is None:
            declared_range = True
        else:
            factors.append(mapped_factor)
        if confidence_token is None:
            confidence_token = canonical_token

    if "two_sigma" in declaration:
        try:
            as_decimal(declaration["two_sigma"])
        except (TypeError, ValueError, ArithmeticError, InvalidOperation):
            pass
        else:
            factors.append(Decimal(2))
            if confidence_token is None:
                confidence_token = "two_sigma"

    if confidence_token is None and fallback_token is not None:
        token = fallback_token.strip().lower()
        if token in _NO_DECLARATION:
            token = ""
        if token and token not in _CONVENTION_FACTORS:
            raise UncertaintyConventionConflict(
                f"unrecognized fallback confidence token {token!r}"
            )
        if token:
            confidence_token, mapped_factor = _CONVENTION_FACTORS[token]
            if mapped_factor is None:
                declared_range = True
            else:
                factors.append(mapped_factor)

    if "k" in declaration and declaration.get("k") is not None:
        try:
            coverage_factor = Decimal(str(declaration["k"]))
        except (InvalidOperation, TypeError, ValueError):
            raise UncertaintyConventionConflict("k must be a positive numeric factor")
        if not coverage_factor.is_finite() or coverage_factor <= 0:
            raise UncertaintyConventionConflict("k must be a positive finite factor")
        numeric_factor = coverage_factor
        factors.append(coverage_factor)

    if declared_range and factors:
        raise UncertaintyConventionConflict(
            "uncertainty_convention_conflict: range_half_width with coverage factor"
        )
    if declared_range:
        return "range_half_width", None
    if not factors:
        if confidence_token is None:
            return None
        return confidence_token, Decimal(1)
    if any(
        abs(factor - factors[0]) > _COVERAGE_FACTOR_TOLERANCE
        for factor in factors[1:]
    ):
        raise UncertaintyConventionConflict(
            "uncertainty_convention_conflict: declarations imply different coverage factors"
        )

    factor = numeric_factor if numeric_factor is not None else factors[0]
    if (
        confidence_token is None
        or _CONVENTION_FACTORS[confidence_token][1] is None
    ):
        confidence_token = next(
            (
                canonical_token
                for canonical_token, mapped_factor in _CONVENTION_FACTORS.values()
                if mapped_factor == factor
            ),
            "one_sigma",
        )
    return confidence_token, factor
