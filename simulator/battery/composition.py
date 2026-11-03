"""Shared composition conversions used by scoring and condition resolution."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from decimal import Decimal

from simulator.battery.records import Composition
from simulator.reference_data.janaf import formula_composition

# Spaces, underscores, hyphens, and dashes are not part of the scale token.
_VOLUME_BASIS_DROPS = str.maketrans("", "", " _-\u2013\u2014")
_VOLUME_PERCENT_SCALES = frozenset({"volpercent", "volumepercent"})
_VOLUME_PPM_SCALES = frozenset({"volppm", "volumeppm", "vppm"})


# Absolute tolerance on a phase point's mole-fraction sum. 1 ± 0.02.
# Babaian's unclosed liquid (sum 0.913) is outside this band.
_MOLE_FRACTION_CLOSURE = Decimal("0.02")


def parse_species_formula(formula: str) -> tuple[tuple[str, float], ...] | None:
    """Closed element-symbol parse. None means unmatched, never string equality."""

    if not formula or formula.strip().lower() in {"unknown", "none", "n/a"}:
        return None
    return formula_composition(formula)


def ideal_gas_volume_scale(token: object) -> str | None:
    """Classify a printed volume basis as ``percent`` or ``ppm``.

    Bare ``ppm`` is not a volume scale. It can be a mass-ppm amount.
    """

    if not isinstance(token, str):
        return None
    folded = (
        token.strip().casefold().replace("%", "percent").translate(_VOLUME_BASIS_DROPS)
    )
    if folded in _VOLUME_PERCENT_SCALES:
        return "percent"
    if folded in _VOLUME_PPM_SCALES:
        return "ppm"
    return None


def ideal_gas_volume_amount_to_mole_fraction(
    amount: Decimal, printed_basis: str
) -> Decimal:
    """Convert a printed ideal-gas volume amount to a mole fraction.

    Premise: Amagat's law. For an ideal gas the partial volume equals the
    partial pressure, so V_i/V = n_i/n. Flue gas at about 1 bar and
    1100-1600 K has Z-1 < 1e-3, below the printed composition precision.
    Algebra: x_i = (vol%)/100 = (vol-ppm) * 1e-6. The ppm dopants are not
    folded back into the percent closure.
    Unit check: vol% and vol-ppm are both volume/volume, so the quotient is
    dimensionless, the same dimension as a mole fraction.
    Sanity: 15.2 vol% -> 0.152; 20 vol-ppm -> 2e-5.
    """

    scale = ideal_gas_volume_scale(printed_basis)
    if scale == "percent":
        return amount / Decimal("100")
    if scale == "ppm":
        return amount * Decimal("1e-6")
    raise ValueError(f"unrecognised ideal-gas volume basis {printed_basis!r}")


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


def mole_percent_to_mole_fraction(percent: Decimal) -> Decimal:
    """x = mol% / 100.

    Premise: the printed phase scalar is mole percent on a 0–100 scale,
    not a mole fraction. This is not ``migrate._composition_from_plain``:
    that converts a bulk composition map and requires the map to sum to
    100. A phase row is one number; closure is the sibling sum in
    ``phase_mole_fractions_to_component_wt_percent``.

    Algebra: x = percent / 100.
    Unit check: mol% / 100 = mol/mol.
    Worked example: 50 mol% → 0.50; 66.8 mol% → 0.668.
    """

    return percent / Decimal(100)


def phase_mole_fractions_to_component_wt_percent(
    component: str,
    mole_fractions: Mapping[str, Decimal],
) -> tuple[Decimal | None, str | None]:
    """Weight percent of one oxide, or a typed refusal. Does not renormalise.

    Premise: each value is an oxide mole fraction of one phase point, and
    the siblings are the whole phase. The engine map is wt% of an
    engine-map oxide key. An unclosed set (sum outside 1 ± 0.02) stays
    refused: dividing by the partial sum would invent the missing fraction.

    Algebra: w_i = 100 * x_i * M_i / sum_j(x_j * M_j). M comes from
    ``oxide_molar_mass`` (the project CIAAW table), not a rounded mass.

    Unit check: (mol/mol) * (g/mol) = g per mole of phase; g/g * 100 = wt%.

    Worked example with the ruling's rounded masses SiO2 = 60.08 and
    CaO = 56.08: w(SiO2) = 100 * 0.5 * 60.08 / (0.5 * 60.08 + 0.5 * 56.08)
    = 51.72 wt%. The CIAAW masses this function uses are SiO2 = 60.083 and
    CaO = 56.077, so the same equal-mole point is 51.724346 wt%.
    """

    from simulator.battery.migrate import is_engine_map_oxide_key, oxide_molar_mass

    if component not in mole_fractions:
        return None, "phase_mole_fraction_component_absent"
    if not is_engine_map_oxide_key(component):
        return None, "phase_component_not_engine_map_oxide"
    total = Decimal(0)
    weighted: dict[str, Decimal] = {}
    for name, amount in mole_fractions.items():
        if not isinstance(amount, Decimal) or not amount.is_finite() or amount < 0:
            return None, "phase_mole_fraction_non_finite"
        if not is_engine_map_oxide_key(name):
            return None, (
                "phase_component_not_engine_map_oxide"
                if name == component
                else "phase_sibling_not_engine_map_oxide"
            )
        weighted[name] = amount * oxide_molar_mass(name)
        total += amount
    # Closure is on the mole fractions. The mass sum is not a renormaliser.
    if abs(total - Decimal(1)) > _MOLE_FRACTION_CLOSURE:
        return None, "phase_mole_fraction_set_unclosed"
    denominator = sum(weighted.values(), Decimal(0))
    if denominator <= 0:
        return None, "phase_mole_fraction_set_unclosed"
    return Decimal(100) * weighted[component] / denominator, None


def phase_printed_mole_amounts_to_wt_percent(
    component: str,
    printed: Mapping[str, Decimal],
    *,
    mole_percent: bool,
) -> tuple[Decimal | None, str | None]:
    """Prose mole percent is x = mol%/100, then the closed mole-fraction rule.

    Premise: a prose mole-percent sibling prints 0–100. A mole_fraction
    token is already x. Mixing the two scales is the caller's grouping
    error; this function applies one scale to the whole map.

    Worked example: 50 mol% SiO2 and 50 mol% CaO are the equal-mole point
    in ``phase_mole_fractions_to_component_wt_percent``.
    """

    if not mole_percent:
        return phase_mole_fractions_to_component_wt_percent(component, printed)
    fractions: dict[str, Decimal] = {}
    for name, amount in printed.items():
        if not isinstance(amount, Decimal) or not amount.is_finite():
            return None, "phase_mole_fraction_non_finite"
        fractions[name] = mole_percent_to_mole_fraction(amount)
    return phase_mole_fractions_to_component_wt_percent(component, fractions)


def phase_ppm_mass_as_wt_percent(
    species: str, ppm: Decimal
) -> tuple[Decimal | None, str | None]:
    """wt% = ppm / 1e4, only when ``species`` is the engine-map oxide key.

    Premise: ppm_mass counts mass parts of the named species per 1e6.
    The engine phase map is oxide weight percent. An element (Ni, Mo) or
    any other non-key is a different thing and stays refused.

    Algebra: ``enums.ppm_mass_to_wt_percent`` is the only divisor
    (wt% = ppm / 1e4). This function does not write a second one.

    Unit check: (mass fraction * 1e6) / 1e4 = mass fraction * 100 = wt%.
    Worked example: 10000 ppm of an oxide key = 1 wt%; 2500 ppm = 0.25 wt%.
    """

    from simulator.battery.enums import ppm_mass_to_wt_percent
    from simulator.battery.migrate import is_engine_map_oxide_key

    if not is_engine_map_oxide_key(species):
        return None, "element_ppm_not_oxide_wt_percent"
    if not isinstance(ppm, Decimal) or not ppm.is_finite():
        return None, "phase_ppm_mass_non_finite"
    return ppm_mass_to_wt_percent(ppm), None
