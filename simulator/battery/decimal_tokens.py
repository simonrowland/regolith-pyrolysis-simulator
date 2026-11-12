"""Canonical fixed-point Decimal tokens used by battery code."""

from decimal import Decimal


def decimal_token(value: Decimal) -> str:
    """Format a Decimal without exponent notation or insignificant zeros."""

    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"
