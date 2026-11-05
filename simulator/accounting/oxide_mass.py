"""Molar mass of one composition-component name.

The simulation run path imports this module. It does not import the
battery or the binary-pot scoring arm.
"""

from __future__ import annotations

import math

from simulator.state import MOLAR_MASS


def oxide_molar_mass_g_mol(oxide: str) -> float:
    """Molar mass for one component name.

    A positive tabulated ``MOLAR_MASS`` entry wins. Otherwise the atoms
    are ``atoms_parse_formula_else_janaf``: a successful ``parse_formula``
    (``CuSO4.5H2O`` stays 249.677 g/mol), else JANAF atoms times
    ``ATOMIC_WEIGHTS_G_PER_MOL``. Both failing raises
    ``UnparseableCompositionComponent``. PbO is not an OXIDE_SPECIES;
    CIAAW Pb + O via that same parse is how PbO-P2O5 pots convert.
    Domain gates still refuse PbO.
    """

    if oxide in MOLAR_MASS:
        mass = float(MOLAR_MASS[oxide])
        if math.isfinite(mass) and mass > 0.0:
            return mass
    from simulator.accounting.exceptions import UnparseableCompositionComponent
    from simulator.accounting.formulas import (
        ATOMIC_WEIGHTS_G_PER_MOL,
        atoms_parse_formula_else_janaf,
    )

    atoms = atoms_parse_formula_else_janaf(oxide)
    if not atoms:
        raise UnparseableCompositionComponent(oxide)
    total = 0.0
    for element, count in atoms:
        weight = ATOMIC_WEIGHTS_G_PER_MOL.get(str(element))
        if weight is None:
            raise UnparseableCompositionComponent(oxide)
        total += float(count) * float(weight)
    if not math.isfinite(total) or total <= 0.0:
        raise UnparseableCompositionComponent(oxide)
    return total
