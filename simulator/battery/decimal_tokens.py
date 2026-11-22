"""Decimal precision helpers and canonical fixed-point tokens."""

from decimal import Decimal


def _decimal_rounding_half_width(value: Decimal) -> Decimal:
    """Return half the unit of ``value``'s last represented decimal place."""

    return Decimal("0.5").scaleb(value.as_tuple().exponent)


def decimal_token(value: Decimal) -> str:
    """Format a Decimal without exponent notation or insignificant zeros."""

    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"
