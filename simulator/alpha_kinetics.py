"""Shared validation for executable evaporation/condensation alpha contracts."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from copy import deepcopy
from numbers import Real
from typing import Any

from simulator.physical_constants import GAS_CONSTANT_10SIG


class AlphaSpecError(ValueError):
    """Raised when policy metadata is presented as an executable alpha value."""


ANALYTICAL_UPPER_BOUND_ALPHA_STATUS = "analytical_upper_bound"
ALPHA_AUTHORITY_STATUS_FIELD = "alpha_authority_status"


def _evaluate_extracted_arrhenius_alpha(
    alpha_form: Mapping[str, Any], temperature_K: float,
) -> tuple[float, float | None]:
    """Evaluate an extract's Arrhenius fit and its optional energy uncertainty."""
    if alpha_form.get("A") is not None and alpha_form.get("B_K") is not None:
        return float(alpha_form["A"]) * math.exp(
            -float(alpha_form["B_K"]) / temperature_K
        ), None
    prefactor = float(alpha_form.get("gamma_o", alpha_form.get("c0")))
    activation = float(alpha_form["E_J_per_mol"])
    gas_constant = float(alpha_form.get("R_J_per_mol_K") or GAS_CONSTANT_10SIG)
    # E [J/mol] / (R [J/(mol K)] * T [K]) is dimensionless. Thus
    # alpha=gamma0*exp(-E/RT) is dimensionless. Linear propagation gives
    # sigma_alpha=abs(alpha)*sigma_E/(RT), also dimensionless. Positive E
    # makes alpha increase with T; Richter Fig. 3 gives Mg~0.13, SiO~0.12
    # at 2073 K. Callers retain their own admission/domain/provenance checks.
    alpha = prefactor * math.exp(-activation / (gas_constant * temperature_K))
    sigma_E = alpha_form.get("E_uncertainty_J_per_mol")
    sigma = (
        abs(alpha) * float(sigma_E) / (gas_constant * temperature_K)
        if sigma_E is not None else None
    )
    return alpha, sigma


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
        if "calibrated_regime" in spec:
            regime = spec["calibrated_regime"]
            if not isinstance(regime, Mapping):
                raise AlphaSpecError("calibrated_regime must be a mapping")
            if temperature_range is None:
                raise AlphaSpecError("calibrated_regime requires temperature_range_K")
            for field in ("carrier_gas", "composition_family"):
                if not isinstance(regime.get(field), str) or not regime[field].strip():
                    raise AlphaSpecError(f"calibrated_regime.{field} must be non-empty")
            _finite_pair(
                regime.get("pressure_bar"),
                field="calibrated_regime.pressure_bar",
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
    value = parse_alpha_value(contract["value"])
    calibrated_regime = contract.get("calibrated_regime")
    upper_bound = contract.get("status") == ANALYTICAL_UPPER_BOUND_ALPHA_STATUS
    if "calibrated_regime" not in contract and not upper_bound:
        return value
    if isinstance(value, Mapping):
        executable = dict(value)
    else:
        executable = {"form": "scalar", "value": value}
    if "calibrated_regime" in contract:
        executable["calibrated_regime"] = deepcopy(calibrated_regime)
        executable["temperature_range_K"] = deepcopy(contract.get("T_band_K"))
    if upper_bound:
        executable[ALPHA_AUTHORITY_STATUS_FIELD] = ANALYTICAL_UPPER_BOUND_ALPHA_STATUS
    return parse_alpha_value(executable)


def evaluate_calibrated_alpha_regime(
    species: str,
    temperature_K: float,
    spec: Mapping[str, Any],
    context: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Predict with the fitted alpha while identifying unsupported caller regimes."""
    fit = dict(spec["calibrated_regime"])
    temperature_band = list(spec["temperature_range_K"])
    fit["temperature_K"] = temperature_band
    context = context or {}
    pressure = context.get("pressure_bar")
    if (
        isinstance(pressure, bool)
        or not isinstance(pressure, Real)
        or not math.isfinite(pressure)
    ):
        pressure = None
    caller = {
        "temperature_K": temperature_K,
        "carrier_gas": context.get("carrier_gas"),
        "pressure_bar": pressure,
        "composition_family": context.get("composition_family"),
    }

    # Fedkin section 4.2 / Fig.4 fits Yu composition 1 at one printed point:
    # 1723 K and 1e-5 bar air (1 Pa). A point supplies no temperature/pressure
    # tolerance or composition interpolation. Allow one floating-point ULP
    # for unit-conversion roundoff only; unknown gas/composition is not a match.
    # The family is an explicit source identity, never inferred from bulk chemistry.
    def within_band(value: float | None, band: Sequence[float]) -> bool:
        return value is not None and (
            math.nextafter(float(band[0]), -math.inf)
            <= value
            <= math.nextafter(float(band[1]), math.inf)
        )

    mismatches = []
    if not within_band(temperature_K, temperature_band):
        mismatches.append("temperature_K")
    if str(caller["carrier_gas"] or "").casefold() != fit["carrier_gas"].casefold():
        mismatches.append("carrier_gas")
    if not within_band(pressure, fit["pressure_bar"]):
        mismatches.append("pressure_bar")
    if caller["composition_family"] != fit["composition_family"]:
        mismatches.append("composition_family")
    evaluation = {
        "alpha_s_valid_range_K": temperature_band,
        "alpha_s_temperature_range_K": temperature_band,
        "alpha_s_calibrated_regime": fit,
        "alpha_s_caller_regime": caller,
        "alpha_s_extrapolated": bool(mismatches),
    }
    if mismatches:
        evaluation.update({
            "alpha_s_regime_mismatches": mismatches,
            "alpha_s_extrapolation_reason": "outside_calibrated_regime",
            "alpha_s_extrapolation_warning": (
                f"{species} alpha_s scalar coefficient extrapolated beyond "
                f"calibrated regime {fit}; caller regime {caller}; "
                f"mismatches: {', '.join(mismatches)}"
            ),
        })
    return evaluation
