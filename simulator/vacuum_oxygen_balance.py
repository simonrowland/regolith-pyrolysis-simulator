"""Vacuum oxygen-balance channel types shared by the melt surface and the battery.

The battery harness imports these names and re-exports them. Simulation
code must import them from this module, never from ``simulator.battery``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

OxygenBalanceBracketBoundary = Literal["lower", "upper"]
_OXYGEN_GAS_ALPHA = 1.0
_OXYGEN_GAS_ALPHA_SOURCE = (
    "declared common-unity sensitivity alpha=1.0 for O/O2 free-molecular gas escape; "
    "absolute oxygen-gas alpha is not measured"
)


@dataclass(frozen=True)
class _VacuumOxygenChannel:
    """One active gas channel participating in a vacuum oxygen balance."""

    species: str
    molar_mass_kg_per_mol: float
    oxygen_atoms: float
    parent_oxygen_demand: float
    pO2_exponent: float
    alpha: float
    alpha_source: str
    active: bool = True


class _VacuumOxygenPressureValues(dict[str, float]):
    """One pressure evaluation with the parent activities it actually used."""

    def __init__(
        self,
        pressures_Pa: Mapping[str, float],
        *,
        parent_activities: Mapping[str, float] | None = None,
    ) -> None:
        super().__init__(pressures_Pa)
        self.parent_activities = dict(parent_activities or {})


@dataclass(frozen=True)
class _VacuumOxygenBalance:
    pO2_bar: float
    bracket_boundary: OxygenBalanceBracketBoundary | None
    pressures_Pa: Mapping[str, float]
    channel_fluxes_mol_m2_s: Mapping[str, float]
    oxygen_atom_flux_mol_m2_s: float
    parent_oxygen_demand_flux_mol_m2_s: float
    relative_residual: float
    alpha_sources: Mapping[str, str]
    pressure_law_exponent_diagnostics: Mapping[str, Mapping[str, object]] = field(
        default_factory=dict
    )
