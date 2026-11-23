"""Shared parsing and interpretation of printed uncertainty declarations."""

import re
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import NamedTuple

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


def standard_uncertainty_width(
    printed_half_width: Decimal, coverage_factor: Decimal
) -> Decimal:
    """Convert a printed half-width at coverage k to its 1σ half-width.

    A printed half-width w covers k standard deviations, so σ = w/k. A
    two-sigma band is ±2σ; callers apply their metric transform once to that
    width. For example, 80 kPa at k=2 gives σ=40 kPa.
    """
    return printed_half_width / coverage_factor


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
# Top-level keys only. Nested locator text is not a width: recursing into it
# selected page numbers on Leister ESR rows.
_WIDTH_KEY_TOKENS = ("uncert", "error", "sigma", "std", "dex", "width", "range")
_EXACT_WIDTH_NAMES = frozenset(
    {
        "sigma",
        "std",
        "standard_deviation",
        "standard_error",
        "uncertainty",
        "error",
        "width",
        "range",
        "bounds",
        "interval",
        "limits",
    }
)
_UNIT_ALIASES = {
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
_SCALED_PERCENT_CANONICAL = frozenset({"dimensionless", "fraction"})
_PERCENT_CANONICAL = frozenset({"%", "atom%"})
# d-460: a percent width with no printed basis is predicted, not refused, and
# the flag names the default. Relative matches the percent-to-fraction
# conversion already used when the paper does not say percentage points.
PERCENT_BASIS_DEFAULT_REASON = (
    "uncertainty_basis_defaulted: relative; "
    "printed percent width has no basis"
)
_CONFIDENCE_TRAILER_RE = re.compile(
    r"\s*\(?\s*(?:95\s*%?\s*(?:ci|confidence interval)"
    r"|[12]\s*-?\s*(?:sigma|σ)|range|limits?"
    r"|maximum error|maximum deviation)\s*\)?\s*$",
    re.IGNORECASE,
)


class WidthUnitBasis(NamedTuple):
    basis: str | None
    conflict: str | None
    basis_default_reason: str | None = None


def _is_width_key(name: str) -> bool:
    folded = name.casefold()
    if folded in _EXACT_WIDTH_NAMES:
        return True
    return any(token in folded for token in _WIDTH_KEY_TOKENS)


def _alias_unit(unit: str | None) -> str:
    text = (unit or "").strip()
    if not text or text.casefold() == "range":
        return ""
    folded = text.casefold()
    compact = re.sub(r"\s+", "", folded)
    if compact in _UNIT_ALIASES:
        return _UNIT_ALIASES[compact]
    return _UNIT_ALIASES.get(folded, compact)


def _clean_unit_suffix(raw: str) -> str | None:
    suffix = _CONFIDENCE_TRAILER_RE.sub("", raw.strip().strip(" ,;[]()")).strip()
    if not suffix or not re.search(r"[A-Za-z°µμ%]", suffix):
        return None
    if re.fullmatch(r"%|percent|percentage", suffix, re.IGNORECASE):
        return "%"
    return suffix


def _unit_bound_to_number(raw: str, number: str) -> str | None:
    """Unit of the selected width number, not an earlier confidence percent."""
    escaped = re.escape(number)
    anchored = re.compile(
        rf"(?:±|\+/-)\s*{escaped}(?![\d.])\s*(.*)\Z",
        re.IGNORECASE | re.DOTALL,
    )
    match = anchored.search(raw)
    if match is None:
        loose = re.compile(
            rf"(?<![\d.]){escaped}(?![\d.])\s*(.*)\Z",
            re.DOTALL,
        )
        found = list(loose.finditer(raw))
        match = found[-1] if found else None
    if match is None:
        return None
    return _clean_unit_suffix(match.group(1))


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
    for key, value in mapping.items():
        name = str(key).casefold()
        if not _is_width_key(name):
            continue
        if isinstance(value, (tuple, list)) and len(value) == 2:
            return value, "range"
        try:
            as_decimal(value)
        except (TypeError, ValueError, ArithmeticError, InvalidOperation):
            if isinstance(value, str):
                scalar = uncertainty_scalar_from_text(value)
                if scalar is not None:
                    unit = _unit_from_width_text(value)
                    if unit is None and "dex" in name:
                        unit = "dex"
                    return scalar, unit
            continue
        return value, "dex" if "dex" in name else None
    return None, None


def _unit_from_width_text(raw: str) -> str | None:
    number = uncertainty_scalar_from_text(raw)
    if number is None:
        return None
    return _unit_bound_to_number(raw, number)


def printed_width_token(raw: str) -> tuple[str, str | None] | None:
    """Width number and the unit bound to that number."""
    number = uncertainty_scalar_from_text(raw)
    if number is None:
        return None
    return number, _unit_bound_to_number(raw, number)


def printed_width_span(raw: str) -> tuple[str, str, str | None] | None:
    """Endpoints of a printed range and the unit after the span, if any."""
    match = UNCERTAINTY_RANGE_RE.search(raw) or UNCERTAINTY_PAIR_RE.search(raw)
    if match is None:
        return None
    return match.group(1), match.group(2), _clean_unit_suffix(raw[match.end() :])


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
    printed_units: str | None = None,
) -> WidthUnitBasis:
    """Resolve width basis from the original unit declarations.

    A range shape is not a unit. A percent width already scaled to a
    dimensionless or fraction value keeps that canonical unit and the
    original percent in ``printed_units``; that pair is one declaration.
    Any other two units, including ones outside the percent vocabulary,
    are a typed conflict. A percent width with no basis uses the stated
    relative default and names it.
    """
    left = unit if _alias_unit(unit) else None
    right = inline_unit if _alias_unit(inline_unit) else None
    left_key = _alias_unit(left)
    right_key = _alias_unit(right)
    printed_key = _alias_unit(printed_units)
    if (
        left_key in _SCALED_PERCENT_CANONICAL
        and printed_key in _PERCENT_CANONICAL
        and right_key in {"", printed_key}
    ):
        right = None
        right_key = ""
    if left_key and right_key and left_key != right_key:
        return WidthUnitBasis(
            None,
            "width unit declarations disagree: "
            f"{left!r} and {right!r}",
            None,
        )
    normalized = left_key or right_key
    declared = (declared_basis or "").strip().casefold()
    if declared.startswith("uncertainty_convention_conflict:"):
        return WidthUnitBasis(None, declared.partition(":")[2], None)
    evidence = f"{basis_evidence} {printed_value or ''}".casefold()
    relative = declared == "relative" or bool(re.search(r"\brelative\b", evidence))
    absolute = declared in {"absolute", "dimensionless"} or bool(
        re.search(r"percentage\s*points?", evidence)
    ) or "atom%" in evidence or "atom %" in evidence
    if normalized in _PERCENT_CANONICAL:
        if relative:
            return WidthUnitBasis("relative", None, None)
        if absolute or paired_width_unit(paired_value) is not None:
            return WidthUnitBasis("dimensionless", None, None)
        return WidthUnitBasis("relative", None, PERCENT_BASIS_DEFAULT_REASON)
    if normalized in {"dimensionless", "fraction", "1"}:
        return WidthUnitBasis(
            "relative" if relative else "dimensionless", None, None
        )
    if normalized == "dex":
        if expected_unit and expected_unit.casefold() in {
            "dimensionless", "fraction", "1"
        }:
            return WidthUnitBasis(
                None, f"dex width is incompatible with {expected_unit} value", None
            )
        if relative:
            return WidthUnitBasis(
                None, "relative basis conflicts with dex width unit", None
            )
        return WidthUnitBasis("dex", None, None)
    return WidthUnitBasis(None, None, None)


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
