"""Surface partial pressures of O(g) and O2(g) from gas thermochemistry.

The residue pressure model is the caller. No melt engine is asked for O or O2.
K_diss is the project's existing NASA CEA Gibbs evaluation, not a new constant.
"""

from __future__ import annotations

from collections.abc import Collection
from pathlib import Path

from simulator.physical_constants import PA_PER_BAR


def surface_oxygen_partial_pressures_Pa(
    temperature_K: float,
    fO2_bar: float,
    *,
    extract_path: Path | str,
    species: Collection[str],
) -> dict[str, float]:
    """Return requested O2 and O partial pressures at the melt surface, in pascal.

    Premise: ideal gas at the surface, so p_O2 = fO2. Dissociation is
    O2(g) = 2 O(g). The CEA reference pressure on the shipped O and O2
    polynomials is P° = 1 bar (100000 Pa). The activity equilibrium
    constant from those Gibbs polynomials is

        K_diss(T) = exp(-(2 G°_O(T) - G°_O2(T)) / RT)
                  = (p_O / P°)^2 / (p_O2 / P°)

    Algebra: p_O = P° * sqrt(K_diss * p_O2 / P°). With P° = 1 bar this is
    p_O = sqrt(K_diss * p_O2), with both pressures in bar. The evaluation
    is ``offgas_fo2._oxygen_atom_partial_pressure_bar``; this module does
    not introduce another K. O is evaluated only when requested, so an
    O2-only channel list does not read the extract.

    Units: temperature_K is kelvin, fO2_bar is bar, and each returned
    pressure is pascal (bar * PA_PER_BAR).

    Limiting case: at p_O2 = 1 bar and 2000 K, K_diss = 4.472e-7 and
    p_O = 6.687e-4 bar, which is on the order of 1e-4 bar.
    """

    requested = {str(name) for name in species}
    pressures: dict[str, float] = {}
    if "O2" in requested:
        pressures["O2"] = float(fO2_bar) * PA_PER_BAR
    if "O" in requested:
        # Attribute lookup, not a bound import: callers patch
        # offgas_fo2._oxygen_atom_partial_pressure_bar and this call must
        # see that patch.
        from simulator.chemistry import offgas_fo2

        p_O_bar = offgas_fo2._oxygen_atom_partial_pressure_bar(
            float(temperature_K),
            float(fO2_bar),
            extract_path=extract_path,
        )
        pressures["O"] = p_O_bar * PA_PER_BAR
    return pressures
