"""Shared subprocess contracts for the AlphaMELTS adapter surfaces."""

from __future__ import annotations

from enum import Enum
from typing import Mapping


class AlphaMELTSSubprocessRunMode(str, Enum):
    """Explicit alphaMELTS starting-state semantics for subprocess runs."""

    ISOTHERMAL = "isothermal"
    LIQUIDUS_FINDER = "liquidus_finder"


def isothermal_run_kwargs(
    backend_name: str,
    parameter_names: Mapping[str, object],
) -> dict[str, str]:
    """Run mode for one fixed-temperature equilibration.

    A single-T assemblage is one temperature, one pressure and one fO2,
    with no temperature path, so the subprocess run is isothermal.
    AlphaMELTS refuses a subprocess call that does not name that mode.
    """

    if backend_name == "alphamelts" or "subprocess_run_mode" in parameter_names:
        return {
            "subprocess_run_mode": AlphaMELTSSubprocessRunMode.ISOTHERMAL.value
        }
    return {}


__all__ = ("AlphaMELTSSubprocessRunMode", "isothermal_run_kwargs")
