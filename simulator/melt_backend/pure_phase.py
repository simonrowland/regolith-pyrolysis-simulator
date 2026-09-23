"""Pure-phase standard-state property access — shared typed result.

One narrow capability, two engines (MAGEMin subprocess binary, ThermoEngine
warm worker): for a named phase at (T_K, P_bar) return the per-formula
standard-state properties G, S, Cp, H with explicit provenance (engine phase
id, polymorph, formula basis, database, Gibbs convention).

Both underlying datasets report an **apparent** Gibbs energy of formation
(elements referenced only at the 298.15 K reference temperature), NOT the
JANAF Delta_fG(T) convention (elements in their reference states at T).
Per-phase apparent G must therefore never be compared against JANAF
Delta_fG(T); only balanced-reaction sums cancel the element terms exactly.

A property that is not reachable (e.g. a phase that is not stable at the
requested T,P in MAGEMin's ig database) is a TYPED absence in
``absences`` with a ``None`` value — never a fabricated or proxied number
(absence is never a zero).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

GIBBS_CONVENTION_APPARENT_298 = 'apparent_gibbs_formation_Tref_298.15K'

# Closed reason tokens for PropertyAbsence.reason.
PHASE_NOT_STABLE_AT_TP = 'phase_not_stable_at_TP'
PHASE_NOT_PURE_AT_EQUILIBRIUM = 'phase_not_pure_at_equilibrium'
PHASE_FACTOR_UNAVAILABLE = 'phase_factor_unavailable'


class PurePhaseAccessError(RuntimeError):
    """The engine route for pure-phase properties is unavailable."""


class PurePhaseUnknownSymbolError(ValueError):
    """The requested phase symbol is not in the engine's known set."""


class PurePhaseUnsupportedDatabaseError(ValueError):
    """The engine's loaded database has no verified phase map for this query."""


class PurePhaseBulkMismatchError(PurePhaseAccessError):
    """The engine ran a different bulk than requested (KLB1 trap guard).

    MAGEMin's CLI honors ``--Bulk`` only when its first component (SiO2 in
    the ig order) is > 0 (upstream src/toolkit.c retrieve_bulk_PT); otherwise
    it silently minimizes the default KLB1 test bulk. The guard compares the
    matlab dump's SYS oxide row against the requested bulk and refuses typed
    on mismatch.
    """


@dataclass(frozen=True)
class PropertyAbsence:
    """One property that could not be returned, with the typed reason."""

    property: str  # 'G' | 'S' | 'Cp' | 'H'
    reason: str    # one of the PHASE_* tokens above


@dataclass(frozen=True)
class PurePhaseProperties:
    """Per-formula standard-state properties of one named phase at (T, P).

    Units: G/H in J per mol of ``formula_basis``; S/Cp in J/(K mol of
    ``formula_basis``). ``None`` plus a typed ``PropertyAbsence`` marks an
    unreachable property.
    """

    engine: str                # 'magemin' | 'thermoengine'
    phase_id: str              # engine-native symbol: 'fo', 'Fo', 'q', 'Qz' ...
    host_phase: Optional[str]  # solution host for endmembers ('ol','fper','opx'); None for pure phases
    polymorph: Optional[str]   # 'quartz' | 'cristobalite' | ... ; None when N/A
    formula: str               # formula unit the properties are per-mol of
    formula_basis: str         # human-readable basis note incl. any divisor derivation
    database: str              # thermodynamic dataset identity
    gibbs_convention: str      # GIBBS_CONVENTION_APPARENT_298
    temperature_K: float
    pressure_bar: float
    G_J_mol: Optional[float]
    S_J_K_mol: Optional[float]
    Cp_J_K_mol: Optional[float]
    H_J_mol: Optional[float]
    absences: tuple[PropertyAbsence, ...] = ()
    warnings: tuple[str, ...] = ()
