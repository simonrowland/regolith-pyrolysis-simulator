"""Shared structured backend out-of-domain reason codes."""

from __future__ import annotations

from enum import Enum


class OutOfDomainReason(str, Enum):
    FORBIDDEN_SPECIES = "forbidden_species"
    SILICATE_WINDOW = "silicate_window"
    MAJOR_SUM = "major_sum"
    NOT_CONVERGED = "not_converged"
    BACKEND_UNAVAILABLE = "backend_unavailable"


def reason_value(reason: OutOfDomainReason | str | None) -> str | None:
    if reason is None:
        return None
    if isinstance(reason, OutOfDomainReason):
        return reason.value
    value = str(reason)
    return value or None


__all__ = ("OutOfDomainReason", "reason_value")
