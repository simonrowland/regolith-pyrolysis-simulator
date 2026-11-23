"""Shared interpretation of printed uncertainty coverage declarations."""

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation


_SIGMA_COVERAGE_FACTORS = {
    "one_sigma": Decimal(1),
    "two_sigma": Decimal(2),
}
# Two-sided normal coverage factor for a 95% interval.
NORMAL_95_TICKET_MULTIPLIER = Decimal("1.959963984540054")
_CONFIDENCE_COVERAGE_FACTORS = {
    **_SIGMA_COVERAGE_FACTORS,
    "ci95": NORMAL_95_TICKET_MULTIPLIER,
}
_COVERAGE_FACTOR_TOLERANCE = Decimal("0.001")
_CONFIDENCE_LEVELS = frozenset(
    {*_SIGMA_COVERAGE_FACTORS, "ci95", "range_half_width"}
)
_NO_DECLARATION = frozenset(
    {"", "none", "not", "not_stated", "unknown", "unspecified"}
)


class UncertaintyConventionConflict(ValueError):
    """Raised when declared uncertainty conventions imply different factors."""


def printed_uncertainty_convention(
    declaration: object,
) -> tuple[str | None, Decimal] | None:
    """Return the stated confidence token and its coverage factor, if valid.

    ``None`` means no usable convention was declared. Unsupported tokens and
    non-finite/non-positive factors are ignored. Conflicting usable declarations
    raise ``UncertaintyConventionConflict``. Factors within 0.001 agree, allowing
    a printed ``k=1.96`` to agree with the normal 95% factor.
    A numeric ``k`` remains an exact coverage factor; its confidence token is
    the matching sigma token when k is one or two, otherwise one_sigma after
    the width has been reduced to standard uncertainty by the caller.
    """
    if not isinstance(declaration, Mapping):
        return None

    factors: list[Decimal] = []
    confidence_token: str | None = None
    declared_range = False
    for key in ("confidence_level", "kind"):
        raw_token = declaration.get(key)
        if raw_token is None:
            continue
        if not isinstance(raw_token, str):
            return None
        token = raw_token.strip().lower()
        if token in _NO_DECLARATION or (key == "kind" and token == "printed"):
            continue
        if key == "confidence_level" and token not in _CONFIDENCE_LEVELS:
            return None
        if key == "confidence_level":
            confidence_token = token
            if token == "range_half_width":
                declared_range = True
            elif token in _CONFIDENCE_COVERAGE_FACTORS:
                factors.append(_CONFIDENCE_COVERAGE_FACTORS[token])
        elif token in _SIGMA_COVERAGE_FACTORS:
            factors.append(_SIGMA_COVERAGE_FACTORS[token])
            if confidence_token is None:
                confidence_token = token
        elif confidence_token is None:
            # ``kind`` can carry descriptive metadata alongside a named
            # confidence declaration; it does not erase that declaration.
            continue

    if "k" in declaration and declaration.get("k") is not None:
        try:
            coverage_factor = Decimal(str(declaration["k"]))
        except (InvalidOperation, TypeError, ValueError):
            return None
        if not coverage_factor.is_finite() or coverage_factor <= 0:
            return None
        factors.append(coverage_factor)

    if declared_range and factors:
        raise UncertaintyConventionConflict(
            "uncertainty_convention_conflict: range_half_width with coverage factor"
        )
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

    factor = _CONFIDENCE_COVERAGE_FACTORS.get(confidence_token, factors[0])
    if confidence_token is None or confidence_token not in _CONFIDENCE_LEVELS:
        confidence_token = next(
            (
                token
                for token, mapped_factor in _SIGMA_COVERAGE_FACTORS.items()
                if mapped_factor == factor
            ),
            "one_sigma",
        )
    return confidence_token, factor
