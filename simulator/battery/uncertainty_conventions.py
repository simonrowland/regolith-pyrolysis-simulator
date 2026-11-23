"""Shared interpretation of printed uncertainty coverage declarations."""

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation


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

    factor = factors[0]
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
