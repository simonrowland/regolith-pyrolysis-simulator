"""Shared structured backend out-of-domain reason codes."""

from __future__ import annotations

from enum import Enum


class OutOfDomainReason(str, Enum):
    FORBIDDEN_SPECIES = "forbidden_species"
    SILICATE_WINDOW = "silicate_window"
    MAJOR_SUM = "major_sum"
    NOT_CONVERGED = "not_converged"
    BACKEND_UNAVAILABLE = "backend_unavailable"
    MAGEMIN_MODE_VECTOR_MASS_DEFICIT = "magemin_mode_vector_mass_deficit"
    CLAMPED_OPERATING_POINT = "clamped_operating_point"
    COMPOSITION_PROJECTED = "composition_projected"
    INPUT_COMPOSITION_PROJECTED = "input_composition_projected"
    INVALID_FO2_LOG = "invalid_fo2_log"
    INVALID_INPUT = "invalid_input"
    INVALID_PRESSURE_BAR = "invalid_pressure_bar"
    NON_FINITE_COMPOSITION = "non_finite_composition"
    OUT_OF_DOMAIN = "out_of_domain"
    REDOX_STATE_NOT_EXPRESSIBLE = "redox_state_not_expressible"
    SUBPROCESS_PRESSURE_BELOW_MINIMUM = "subprocess_pressure_below_minimum"
    SUBPROCESS_TEMPERATURE_BELOW_MINIMUM = "subprocess_temperature_below_minimum"
    UNSUPPORTED_INPUT = "unsupported_input"
    # VapoRock external domain gate (VR-5 / DESIGN-REV5 §4.2.1).
    # Load-bearing: upstream fabricates smooth finite garbage outside the
    # admitted envelope (probe: ~8.3e5 bar total at 10000 K) and never
    # self-refuses.
    TEMPERATURE_RANGE = "temperature_range"
    LIQUID_STATE = "liquid_state"
    SUM_PRESSURE_SANITY = "sum_pressure_sanity"


TRANSIENT_OUT_OF_DOMAIN_REASONS = frozenset({
    OutOfDomainReason.NOT_CONVERGED,
    OutOfDomainReason.BACKEND_UNAVAILABLE,
})
STABLE_OUT_OF_DOMAIN_REASONS = frozenset(
    set(OutOfDomainReason) - TRANSIENT_OUT_OF_DOMAIN_REASONS
)


def reason_value(reason: OutOfDomainReason | str | None) -> str | None:
    if reason is None:
        return None
    if isinstance(reason, OutOfDomainReason):
        return reason.value
    value = str(reason)
    return value or None


__all__ = (
    "OutOfDomainReason",
    "STABLE_OUT_OF_DOMAIN_REASONS",
    "TRANSIENT_OUT_OF_DOMAIN_REASONS",
    "reason_value",
)
