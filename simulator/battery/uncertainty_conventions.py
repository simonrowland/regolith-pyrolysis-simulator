"""Shared interpretation of printed uncertainty coverage declarations."""

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation


_SIGMA_COVERAGE_FACTORS = {
    "one_sigma": Decimal(1),
    "two_sigma": Decimal(2),
}
_CONFIDENCE_LEVELS = frozenset(
    {*_SIGMA_COVERAGE_FACTORS, "ci95", "range_half_width"}
)
_NO_DECLARATION = frozenset(
    {"", "none", "not", "not_stated", "unknown", "unspecified"}
)


def printed_uncertainty_convention(
    declaration: object,
) -> tuple[str | None, Decimal] | None:
    """Return the stated confidence token and its coverage factor, if valid.

    ``None`` means no usable convention was declared. Unsupported tokens,
    non-finite/non-positive factors, and conflicting declarations are rejected.
    A numeric ``k`` remains an exact coverage factor; its confidence token is
    the matching sigma token when k is one or two, otherwise one_sigma after
    the width has been reduced to standard uncertainty by the caller.
    """
    if not isinstance(declaration, Mapping):
        return None

    factors: list[Decimal] = []
    confidence_token: str | None = None
    for key in ("confidence_level", "kind"):
        raw_token = declaration.get(key)
        if raw_token is None:
            continue
        if not isinstance(raw_token, str):
            return None
        token = raw_token.strip().lower()
        if token in _NO_DECLARATION or (key == "kind" and token == "printed"):
            continue
        if key == "kind" and token not in _SIGMA_COVERAGE_FACTORS:
            return None
        if key == "confidence_level" and token not in _CONFIDENCE_LEVELS:
            return None
        if token in _SIGMA_COVERAGE_FACTORS:
            factors.append(_SIGMA_COVERAGE_FACTORS[token])
        if key == "confidence_level":
            confidence_token = token
        elif confidence_token is None:
            confidence_token = token

    if "k" in declaration and declaration.get("k") is not None:
        try:
            coverage_factor = Decimal(str(declaration["k"]))
        except (InvalidOperation, TypeError, ValueError):
            return None
        if not coverage_factor.is_finite() or coverage_factor <= 0:
            return None
        factors.append(coverage_factor)

    if not factors:
        if confidence_token is None:
            return None
        return confidence_token, Decimal(1)
    if any(factor != factors[0] for factor in factors[1:]):
        return None

    factor = factors[0]
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
