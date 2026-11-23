"""Vacuum oxygen-balance channel types shared by the melt surface and the battery.

The battery harness imports these names and re-exports them. Simulation
code must import them from this module, never from ``simulator.battery``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
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
    # Fe channels: o_bar(q) per metal atom, evaluated inside the residual.
    # None keeps the stoichiometric parent_oxygen_demand (fixed valence).
    oxygen_per_metal_atom: Callable[[float], float] | None = None
    redox_metal_atoms: float = 0.0


@dataclass(frozen=True)
class PressureLawChannelDiagnostic:
    """Declared, observed and expected d log10(P) / d log10(pO2) for one channel.

    ``reason`` is ``residue_pressure_law_redox_active`` when the engine does
    not declare fixed activities. The hard identity stays in the solver.
    """

    species: str
    declared: float
    observed: float
    expected: float
    reason: str


class PressureEvaluation(dict):
    """Species pressures in Pa, plus Fe speciation from that same evaluation.

    A plain dict is a pressure law with no speciation. The residual then
    keeps the stoichiometric parent demand.
    """

    def __init__(
        self,
        pressures: Mapping[str, float],
        *,
        fe3_over_sum_fe: float | None = None,
        fe_activity: float | None = None,
        speciation_source: str | None = None,
    ) -> None:
        super().__init__(pressures)
        self.fe3_over_sum_fe = fe3_over_sum_fe
        self.fe_activity = fe_activity
        self.speciation_source = speciation_source


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
    pressure_law_diagnostics: tuple[PressureLawChannelDiagnostic, ...] = ()
    parent_oxygen_demand_at_root: Mapping[str, float] = field(default_factory=dict)
