"""Shared structured backend out-of-domain reason codes."""

from __future__ import annotations

from enum import Enum


class OutOfDomainReason(str, Enum):
    FORBIDDEN_SPECIES = "forbidden_species"
    SILICATE_WINDOW = "silicate_window"
    MAJOR_SUM = "major_sum"
    NOT_CONVERGED = "not_converged"
    BACKEND_UNAVAILABLE = "backend_unavailable"
    # VapoRock external domain gate (VR-5 / DESIGN-REV5 §4.2.1).
    # Load-bearing: upstream fabricates smooth finite garbage outside the
    # admitted envelope (probe: ~8.3e5 bar total at 10000 K) and never
    # self-refuses.
    TEMPERATURE_RANGE = "temperature_range"
    LIQUID_STATE = "liquid_state"
    SUM_PRESSURE_SANITY = "sum_pressure_sanity"


def reason_value(reason: OutOfDomainReason | str | None) -> str | None:
    if reason is None:
        return None
    if isinstance(reason, OutOfDomainReason):
        return reason.value
    value = str(reason)
    return value or None


__all__ = ("OutOfDomainReason", "reason_value")
