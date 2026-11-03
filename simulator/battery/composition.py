"""Shared composition conversions used by scoring and condition resolution."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from decimal import Decimal

from simulator.battery.records import Composition
from simulator.reference_data.janaf import formula_composition


def parse_species_formula(formula: str) -> tuple[tuple[str, float], ...] | None:
    """Closed element-symbol parse. None means unmatched, never string equality."""

    if not formula or formula.strip().lower() in {"unknown", "none", "n/a"}:
        return None
    return formula_composition(formula)


def composition_wt_pct(composition: Composition) -> dict[str, float] | None:
    """Convert an engine-basis composition to oxide wt%.

    Premise: Identity.composition is a complete ordered map on
    mol_inventory or mole_fraction. Equilibrate cells take oxide wt%.
    Algebra: m_i = n_i M_i; w_i = 100 m_i / Σ m_j.
    Unit check: (mol)×(g/mol) = g; g/g × 100 = wt%.
    Sanity: equal moles of SiO2 (60.084) and MgO (40.304) → ~59.9/40.1 wt%.
    """

    from simulator.diagnostic_helpers.binary_pot_scoring import oxide_molar_mass_g_mol

    masses: dict[str, float] = {}
    for oxide, amount in composition.components:
        if parse_species_formula(oxide) is None:
            return None
        n = float(amount)
        if not math.isfinite(n) or n < 0.0:
            return None
        mass = n * oxide_molar_mass_g_mol(oxide)
        if not math.isfinite(mass) or mass < 0.0:
            return None
        masses[oxide] = mass
    total = sum(masses.values())
    if total <= 0.0:
        return None
    return {key: 100.0 * value / total for key, value in masses.items() if value > 0.0}


def mass_weighted_mixture_wt_pct(
    parts: Sequence[tuple[Mapping[str, Decimal], Decimal]],
) -> dict[str, Decimal]:
    """Mass-weighted sum of printed wt% vectors. Does not renormalize.

    Premise: each part is (component wt% map w_k, mass fraction f_k) of one
    mixture. The fractions sum to 1. A named material contributes its printed
    oxide wt% scaled by f_material. A metallic element is its own component
    at 100 wt% of f_element (Mg0). It is never folded into its oxide: the
    metal is the reductant charge, and the oxide already in the material
    stays that oxide.
    Algebra: w_mix,j = sum_k f_k * w_k,j.
    Unit check: f is dimensionless; w is wt% (g per 100 g of that part);
    the product is wt% of the mixture.
    Sanity: 50 wt% pure Mg plus 50 wt% of a material that is 50 SiO2 / 50 MgO
    gives w_Mg = 50, w_SiO2 = 25, w_MgO = 25. MgO is not 75.
    """

    mixed: dict[str, Decimal] = {}
    for weights, fraction in parts:
        for name, weight in weights.items():
            contribution = fraction * weight
            if contribution == 0:
                continue
            mixed[name] = mixed.get(name, Decimal("0")) + contribution
    return mixed
