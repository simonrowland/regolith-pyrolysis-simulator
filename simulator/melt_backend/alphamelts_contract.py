"""Shared subprocess contracts for the AlphaMELTS adapter surfaces."""

from __future__ import annotations

from enum import Enum


class AlphaMELTSSubprocessRunMode(str, Enum):
    """Explicit alphaMELTS starting-state semantics for subprocess runs."""

    ISOTHERMAL = "isothermal"
    LIQUIDUS_FINDER = "liquidus_finder"


__all__ = ("AlphaMELTSSubprocessRunMode",)
