"""Shared loading, validation and evaluation of evaporation/condensation alpha."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from copy import deepcopy
from numbers import Real
from typing import Any


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
    gas_constant = float(alpha_form.get("R_J_per_mol_K") or 8.314462618)
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
    if contract.get("status") != ANALYTICAL_UPPER_BOUND_ALPHA_STATUS:
        return value
    if isinstance(value, Mapping):
        executable = dict(value)
    else:
        executable = {"form": "scalar", "value": value}
    executable[ALPHA_AUTHORITY_STATUS_FIELD] = (
        ANALYTICAL_UPPER_BOUND_ALPHA_STATUS
    )
    return executable


def _valid_temperature_range_K(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    try:
        low = float(value[0])
        high = float(value[1])
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(low) and math.isfinite(high) and low <= high):
        return None
    return [low, high]


def _alpha_s_extrapolation_warning(
    species: str,
    form: str,
    T_K: float,
    valid_low: float,
    valid_high: float,
) -> str:
    return (
        f'{species} alpha_s {form} coefficient extrapolated beyond '
        f'valid_range_K [{valid_low:g}, {valid_high:g}] at {T_K:.2f} K'
    )


def _alpha_s_scalar_evaluation(
    species: str,
    T_K: float,
    spec: Mapping[str, Any],
) -> tuple[float, dict[str, Any]]:
    try:
        value = float(spec.get('value'))
    except (TypeError, ValueError) as exc:
        raise ValueError(f'alpha_s({species}): scalar coefficient required') from exc
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError(f'alpha_s({species}): scalar outside [0, 1]')

    valid_range = _valid_temperature_range_K(spec.get('temperature_range_K'))
    extrapolated = False
    result: dict[str, Any] = {
        'species': str(species),
        'alpha_s': value,
        'alpha_s_form': 'scalar',
        'alpha_s_temperature_K': T_K,
        'alpha_s_extrapolated': False,
    }
    if (
        spec.get(ALPHA_AUTHORITY_STATUS_FIELD)
        == ANALYTICAL_UPPER_BOUND_ALPHA_STATUS
    ):
        result[ALPHA_AUTHORITY_STATUS_FIELD] = (
            ANALYTICAL_UPPER_BOUND_ALPHA_STATUS
        )
    if valid_range is not None:
        valid_low, valid_high = valid_range
        extrapolated = not (valid_low <= T_K <= valid_high)
        result.update({
            'alpha_s_valid_range_K': [valid_low, valid_high],
            'alpha_s_temperature_range_K': [valid_low, valid_high],
            'alpha_s_extrapolated': extrapolated,
        })
        if extrapolated:
            result['alpha_s_extrapolation_warning'] = (
                _alpha_s_extrapolation_warning(
                    species,
                    'scalar',
                    T_K,
                    valid_low,
                    valid_high,
                )
            )
            result[
                'alpha_s_temperature_below_valid_range'
                if T_K < valid_low
                else 'alpha_s_temperature_above_valid_range'
            ] = True
    return value, result


class _ArrheniusAlphaUnderflow(ValueError):
    """Arrhenius alpha_s underflowed to exact 0.0 below its valid domain.

    ValueError subclass so the module's invalid-input contract is unchanged;
    the distinct type lets the SiO cold-wall override claim temperatures
    below its documented validity floor without masking the refusal.
    """


def _alpha_s_evaluation(
    species: str,
    T_K: float,
    spec: Any,
) -> tuple[float, dict[str, Any]]:
    try:
        T_K = float(T_K)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'alpha_s({species}): T_K must be numeric') from exc
    if not math.isfinite(T_K) or T_K <= 0.0:
        raise ValueError(f'alpha_s({species}): T_K must be finite and > 0')

    try:
        spec = parse_alpha_value(spec)
    except AlphaSpecError as exc:
        detail = str(exc)
        if detail == 'arrhenius A and B must be finite and positive':
            detail = 'arrhenius A/B must be finite and > 0'
        else:
            detail = f'malformed coefficient spec: {detail}'
        raise ValueError(f'alpha_s({species}): {detail}') from exc

    if isinstance(spec, Mapping):
        form = spec.get('form')
        if form == 'scalar':
            return _alpha_s_scalar_evaluation(species, T_K, spec)
        if form != 'arrhenius':
            raise ValueError(f'alpha_s({species}): malformed coefficient spec')
        required_fields = (
            'A',
            'B',
            'valid_range_K',
            'uncertainty_envelope',
            'cite',
            'status',
        )
        missing_fields = [field for field in required_fields if field not in spec]
        if missing_fields:
            raise ValueError(
                f'alpha_s({species}): malformed arrhenius coefficient spec; '
                f"missing {', '.join(missing_fields)}"
            )
        try:
            A = float(spec['A'])
            B = float(spec['B'])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f'alpha_s({species}): malformed arrhenius coefficient spec'
            ) from exc
        if (
            not math.isfinite(A)
            or not math.isfinite(B)
            or A <= 0.0
            or B <= 0.0
        ):
            raise ValueError(
                f'alpha_s({species}): arrhenius A/B must be finite and > 0'
            )
        cite = str(spec.get('cite') or '').strip()
        status = str(spec.get('status') or '').upper()
        if not cite or status not in {'CITED', 'UNCERTIFIED'}:
            raise ValueError(
                f'alpha_s({species}): malformed arrhenius coefficient metadata'
            )
        valid_range = spec.get('valid_range_K')
        if not isinstance(valid_range, (list, tuple)) or len(valid_range) != 2:
            raise ValueError(f'alpha_s({species}): malformed valid_range_K')
        try:
            valid_low = float(valid_range[0])
            valid_high = float(valid_range[1])
        except (TypeError, ValueError) as exc:
            raise ValueError(f'alpha_s({species}): malformed valid_range_K') from exc
        if (
            not math.isfinite(valid_low)
            or not math.isfinite(valid_high)
            or valid_low <= 0.0
            or valid_high < valid_low
        ):
            raise ValueError(f'alpha_s({species}): malformed valid_range_K')
        envelope = spec.get('uncertainty_envelope')
        if not isinstance(envelope, (list, tuple)) or len(envelope) != 2:
            raise ValueError(
                f'alpha_s({species}): malformed uncertainty_envelope'
            )
        try:
            envelope_low = float(envelope[0])
            envelope_high = float(envelope[1])
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f'alpha_s({species}): malformed uncertainty_envelope'
            ) from exc
        if (
            not math.isfinite(envelope_low)
            or not math.isfinite(envelope_high)
            or not 0.0 <= envelope_low <= envelope_high <= 1.0
        ):
            raise ValueError(
                f'alpha_s({species}): malformed uncertainty_envelope'
            )
        value = A * math.exp(-B / T_K)
        if not math.isfinite(value) or value > 1.0:
            raise ValueError(f'alpha_s({species}): evaluated value outside [0, 1]')
        if value <= 0.0:
            # A*exp(-B/T) > 0 mathematically; an exact 0.0 is floating-point
            # underflow far below valid_range_K. That is out-of-domain, not
            # a measured perfectly non-sticking zero (b-311 secondary).
            raise _ArrheniusAlphaUnderflow(
                f'alpha_s({species}): arrhenius evaluation underflowed to '
                f'0.0 at {T_K:g} K (domain floor {valid_low:g} K); '
                'out-of-domain, not a non-sticking measurement'
            )
        extrapolated = not (valid_low <= T_K <= valid_high)
        result = {
            'species': str(species),
            'alpha_s': value,
            'alpha_s_form': 'arrhenius',
            'alpha_s_temperature_K': T_K,
            'alpha_s_A': A,
            'alpha_s_B': B,
            'alpha_s_valid_range_K': (
                [valid_low, valid_high]
                if valid_low is not None and valid_high is not None
                else None
            ),
            'alpha_s_extrapolated': extrapolated,
            'alpha_s_uncertainty_envelope': [envelope_low, envelope_high],
            'alpha_s_cite': cite,
            'alpha_s_status': status,
        }
        if T_K > valid_high:
            result['alpha_s_temperature_above_valid_range'] = True
            result['alpha_s_extrapolation_warning'] = (
                _alpha_s_extrapolation_warning(
                    species,
                    'arrhenius',
                    T_K,
                    valid_low,
                    valid_high,
                )
            )
        return value, result

    try:
        value = float(spec)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'alpha_s({species}): scalar coefficient required') from exc
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError(f'alpha_s({species}): scalar outside [0, 1]')
    return value, {
        'species': str(species),
        'alpha_s': value,
        'alpha_s_form': 'scalar',
        'alpha_s_temperature_K': T_K,
        'alpha_s_extrapolated': False,
    }



BROAD_PROXY_NOT_INTRINSIC_TAG = "broad_proxy_not_intrinsic"
_EVAPORATION_ALPHA_GROUPS = ("metals", "oxide_vapors")


def is_broad_proxy_not_intrinsic_row(alpha_data: Any) -> bool:
    """True when an ``evaporation_alpha`` contract row is tagged as a tier-2
    broad proxy — inherited from another measurement family, not an intrinsic
    melt coefficient. Such a row is proxy evidence, never a measurement, and
    must not satisfy the flux path's measured-alpha requirement."""

    if not isinstance(alpha_data, Mapping):
        return False
    return (
        str(alpha_data.get("tag") or "").strip().lower()
        == BROAD_PROXY_NOT_INTRINSIC_TAG
    )



def _assert_runtime_alpha_source_not_vaporock(
    species: str, alpha_data: Mapping[str, Any]
) -> None:
    """Production evaporation gate: refuse VapoRock-fit alpha provenance.

    Complements catalog compile denial. Legacy compatibility views still load
    alphas outside a re-compile; this path is the second SC-50 consumer so a
    VapoRock source cannot reach HKL flux silently.
    """

    from simulator.vapour_rail.kinetics_anchors import (
        KineticsAnchorError,
        assert_alpha_source_not_vaporock,
    )

    sources: list[Any] = []
    # source_note is a supported provenance shape (kinetics_anchors loader);
    # omitting it lets VapoRock-fit alphas bypass the gate via
    # {value, source_note} contracts.
    for key in ("source", "provenance", "citation", "source_note"):
        if key in alpha_data and alpha_data.get(key) is not None:
            sources.append(alpha_data.get(key))
    value = alpha_data.get("value")
    if isinstance(value, Mapping):
        for key in ("cite", "source", "provenance", "source_note"):
            if key in value and value.get(key) is not None:
                sources.append(value.get(key))
    for source in sources:
        try:
            assert_alpha_source_not_vaporock(str(source))
        except KineticsAnchorError as exc:
            raise ValueError(
                f"evaporation alpha for {species!r} refused VapoRock provenance: {exc}"
            ) from exc


def _load_evaporation_alpha_specs(vapor_pressure_data: dict) -> dict[str, Any]:
    """Load alpha specs from resolved legacy rows, preserving dormant carriers.

    b-314: a row tagged ``broad_proxy_not_intrinsic`` (tier-2 proxy inherited
    from another measurement family) is not an intrinsic melt alpha. It stays
    in this map — the pressure-gating path
    (``_legacy_evaporation_shadow_pressure_map``) and class diagnostics consume
    it — but as a provenance-carrying mapping marked with
    ``alpha_proxy_tag``, never a bare number indistinguishable from a
    measured coefficient. The provider-facing measured view
    (``_measured_alpha_control_view``) strips these rows so the SC-67
    unmeasured-alpha refusal fires instead of a proxy satisfying the gate.
    """

    alpha_by_species: dict[str, Any] = {}
    for group_name in _EVAPORATION_ALPHA_GROUPS:
        group = vapor_pressure_data.get(group_name, {}) or {}
        for species, species_data in group.items():
            if not isinstance(species_data, dict):
                continue
            if "evaporation_alpha" not in species_data:
                continue
            alpha_data = species_data.get("evaporation_alpha") or {}
            if isinstance(alpha_data, Mapping):
                _assert_runtime_alpha_source_not_vaporock(str(species), alpha_data)
            value = parse_alpha_contract(alpha_data)
            if value is None:
                continue
            if species_data.get("flux_dormant") is True:
                alpha_by_species[species] = 0.0
                continue
            if is_broad_proxy_not_intrinsic_row(alpha_data):
                payload = (
                    dict(value)
                    if isinstance(value, Mapping)
                    else {"form": "scalar", "value": value}
                )
                payload["alpha_proxy_tag"] = BROAD_PROXY_NOT_INTRINSIC_TAG
                if alpha_data.get("tier") is not None:
                    payload["alpha_proxy_tier"] = alpha_data.get("tier")
                if alpha_data.get("source"):
                    payload["source"] = str(alpha_data.get("source"))
                alpha_by_species[species] = payload
                continue
            alpha_by_species[species] = value
    return alpha_by_species
