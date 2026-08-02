"""Shared validation for executable evaporation/condensation alpha contracts."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from copy import deepcopy
from numbers import Real
from typing import Any


class AlphaSpecError(ValueError):
    """Raised when policy metadata is presented as an executable alpha value."""


def _finite_pair(
    value: Any,
    *,
    field: str,
    positive_low: bool = False,
    unit_interval: bool = False,
) -> tuple[float, float]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise AlphaSpecError(f"{field} must be a two-value sequence")
    if len(value) != 2:
        raise AlphaSpecError(f"{field} must contain exactly two values")
    if any(
        isinstance(item, bool) or not isinstance(item, Real)
        for item in value
    ):
        raise AlphaSpecError(f"{field} values must be numeric and not boolean")
    try:
        low, high = (float(value[0]), float(value[1]))
    except (TypeError, ValueError) as exc:
        raise AlphaSpecError(f"{field} values must be numeric") from exc
    if not math.isfinite(low) or not math.isfinite(high) or high < low:
        raise AlphaSpecError(f"{field} must be finite and ordered")
    if positive_low and low <= 0.0:
        raise AlphaSpecError(f"{field} lower bound must be positive")
    if unit_interval and not 0.0 <= low <= high <= 1.0:
        raise AlphaSpecError(f"{field} must lie within [0, 1]")
    return low, high


def parse_alpha_value(value: Any) -> float | dict[str, Any]:
    """Return a validated executable scalar/correlation alpha specification."""

    if isinstance(value, bool):
        raise AlphaSpecError("boolean is not an alpha coefficient")
    if isinstance(value, Real):
        scalar = float(value)
        if not math.isfinite(scalar) or not 0.0 <= scalar <= 1.0:
            raise AlphaSpecError("scalar alpha must be finite and within [0, 1]")
        return scalar
    if not isinstance(value, Mapping):
        raise AlphaSpecError("alpha value must be a scalar or executable correlation")

    spec = deepcopy(dict(value))
    form = spec.get("form")
    if form == "scalar":
        scalar = parse_alpha_value(spec.get("value"))
        if isinstance(scalar, dict):
            raise AlphaSpecError("scalar form requires a numeric value")
        temperature_range = spec.get("temperature_range_K")
        if temperature_range is not None:
            _finite_pair(
                temperature_range,
                field="temperature_range_K",
                positive_low=True,
            )
        return spec
    if form != "arrhenius":
        raise AlphaSpecError("correlation form must be 'scalar' or 'arrhenius'")

    required = {
        "A",
        "B",
        "valid_range_K",
        "uncertainty_envelope",
        "cite",
        "status",
    }
    missing = sorted(required - set(spec))
    if missing:
        raise AlphaSpecError(
            "arrhenius alpha is missing required fields: " + ", ".join(missing)
        )
    if any(
        isinstance(spec[field], bool) or not isinstance(spec[field], Real)
        for field in ("A", "B")
    ):
        raise AlphaSpecError("arrhenius A and B must be numeric and not boolean")
    try:
        A = float(spec["A"])
        B = float(spec["B"])
    except (TypeError, ValueError) as exc:
        raise AlphaSpecError("arrhenius A and B must be numeric") from exc
    if not math.isfinite(A) or not math.isfinite(B) or A <= 0.0 or B <= 0.0:
        raise AlphaSpecError("arrhenius A and B must be finite and positive")
    _finite_pair(spec["valid_range_K"], field="valid_range_K", positive_low=True)
    _finite_pair(
        spec["uncertainty_envelope"],
        field="uncertainty_envelope",
        unit_interval=True,
    )
    if not isinstance(spec["cite"], str) or not spec["cite"].strip():
        raise AlphaSpecError("arrhenius cite must be a non-empty string")
    status = spec["status"]
    if not isinstance(status, str) or status.upper() not in {"CITED", "UNCERTIFIED"}:
        raise AlphaSpecError("arrhenius status must be CITED or UNCERTIFIED")
    return spec


def parse_alpha_contract(contract: Any) -> float | dict[str, Any] | None:
    """Parse an outer catalog alpha contract; exact no-data remains absence."""

    if not isinstance(contract, Mapping):
        raise AlphaSpecError("evaporation_alpha must be a mapping")
    if "value" not in contract:
        if contract.get("status") == "no_data":
            return None
        raise AlphaSpecError(
            "evaporation_alpha requires value or exact status='no_data'"
        )
    return parse_alpha_value(contract["value"])
