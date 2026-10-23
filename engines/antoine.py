"""Shared arithmetic for Antoine vapour-pressure correlations."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence


def _antoine_skip_reason(coefficients: Mapping[str, object]) -> str:
    return (
        "missing_antoine_A"
        if coefficients.get("A") is None
        else "nonpositive_antoine_A"
    )


def _antoine_skip_notice(
    coefficients: Mapping[str, object],
    *,
    species: str,
    site: str,
    temperature_K: float,
    reason: str | None = None,
) -> str:
    """Describe a skipped Antoine row without changing its evaluation."""
    reason = reason or _antoine_skip_reason(coefficients)
    return (
        "antoine_species_skipped: "
        f"species={species} site={site} reason={reason} "
        f"temperature_K={temperature_K:.3f}"
    )


def _append_antoine_skip_notice(
    notices: list[str],
    seen: set[tuple[str, str, str]],
    coefficients: Mapping[str, object],
    *,
    species: str,
    site: str,
    temperature_K: float,
    reason: str | None = None,
) -> None:
    """Append at most one notice per skipped species/site/reason in a result."""
    resolved_reason = reason or _antoine_skip_reason(coefficients)
    key = (species, site, resolved_reason)
    if key in seen:
        return
    seen.add(key)
    notices.append(
        _antoine_skip_notice(
            coefficients,
            species=species,
            site=site,
            temperature_K=temperature_K,
            reason=resolved_reason,
        )
    )


def _antoine_skip_notices_for_omitted_species(
    notices: Sequence[str],
    resolved_vapor_pressures: Mapping[str, object],
) -> list[str]:
    """Keep skip notices only for species absent from the completed result."""
    retained: list[str] = []
    for notice in notices:
        if notice.startswith("antoine_species_skipped:"):
            species_field = next(
                (field for field in notice.split() if field.startswith("species=")),
                None,
            )
            if species_field is not None:
                species = species_field[len("species="):]
                try:
                    pressure_Pa = float(resolved_vapor_pressures[species])
                except (KeyError, TypeError, ValueError, OverflowError):
                    pass
                else:
                    if math.isfinite(pressure_Pa) and pressure_Pa > 0.0:
                        continue
        retained.append(notice)
    return retained


def _antoine_log10_pressure(
    A: float,
    B: float,
    C: float,
    temperature: float,
) -> float:
    """Return ``log10(P) = A - B / (T + C)`` in the row's pressure unit.

    Callers supply coefficients and temperature in their declared units and
    retain responsibility for unit conversion, coefficient selection, and
    validity policy.
    """
    return A - B / (temperature + C)


class _AntoineCoefficientRefusal(ValueError):
    """Typed refusal for missing or invalid Antoine inputs."""

    def __init__(
        self,
        coefficient: str,
        value: object,
        input_category: str,
        invalid_kind: str = "invalid",
    ) -> None:
        self.coefficient = coefficient
        self.value = value
        self.input_category = input_category
        self.invalid_kind = invalid_kind
        if input_category == "missing_input":
            prefix = "missing_antoine_coefficient"
        elif invalid_kind == "nonfinite":
            prefix = "nonfinite_antoine_coefficient"
        else:
            prefix = "invalid_antoine_coefficient"
        super().__init__(f"{prefix}: coefficient={coefficient} value={value!r}")


def _antoine_skip_reason_for_refusal(
    refusal: _AntoineCoefficientRefusal,
) -> str:
    if refusal.input_category == "missing_input":
        return f"missing_antoine_{refusal.coefficient}"
    if refusal.invalid_kind == "nonfinite":
        return f"nonfinite_antoine_{refusal.coefficient}"
    return f"invalid_antoine_{refusal.coefficient}"


def _is_two_parameter_antoine_form(form: object) -> bool:
    if not isinstance(form, str):
        return False
    expression = "".join(form.casefold().replace("−", "-").split())
    right_hand_side = expression.rsplit("=", 1)[-1]
    return right_hand_side in {"a-b/t", "a-b/t_k"}


def _read_antoine_coefficients(
    coefficients: Mapping[str, object] | None,
) -> tuple[float, float, float]:
    """Validate a coefficient block or source record with an explicit form."""
    if not isinstance(coefficients, Mapping):
        raise _AntoineCoefficientRefusal(
            "coefficients", coefficients, "missing_input"
        )

    record = coefficients
    nested_coefficients = coefficients.get("coefficients")
    if (
        "A" not in coefficients
        and "B" not in coefficients
        and isinstance(nested_coefficients, Mapping)
    ):
        coefficients = nested_coefficients

    def read(name: str) -> float:
        raw = coefficients.get(name)
        if raw is None:
            raise _AntoineCoefficientRefusal(name, raw, "missing_input")
        if isinstance(raw, bool):
            raise _AntoineCoefficientRefusal(name, raw, "invalid_input")
        try:
            value = float(raw)
        except (OverflowError, TypeError, ValueError) as exc:
            raise _AntoineCoefficientRefusal(
                name, raw, "invalid_input"
            ) from exc
        if not math.isfinite(value):
            raise _AntoineCoefficientRefusal(
                name, raw, "invalid_input", "nonfinite"
            )
        return value

    # A is a logarithmic intercept; its sign depends on the source pressure unit.
    A = read("A")
    B = read("B")
    raw_C = coefficients.get("C")
    if raw_C is None:
        declared_form = (
            record.get("runtime_form")
            or record.get("form")
            or record.get("source_form")
            or coefficients.get("runtime_form")
            or coefficients.get("form")
            or coefficients.get("source_form")
        )
        if not _is_two_parameter_antoine_form(declared_form):
            raise _AntoineCoefficientRefusal("C", raw_C, "missing_input")
        # In the declared A-B/T form, C is zero by definition.
        C = 0.0
    else:
        C = read("C")
    return A, B, C
