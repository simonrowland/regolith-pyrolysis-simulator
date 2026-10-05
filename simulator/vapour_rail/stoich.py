"""Oxide-evaporation mass stoichiometry from a compiled source reaction.

One helper (d-068). ``stoich_oxide_per_vapor`` and ``stoich_O2_per_vapor``
are kg/kg of vapor: parent-oxide mass and signed O2 coproduct per kg vapor,
with ``oxide = 1 + O2`` for mass closure.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

from simulator.accounting.formulas import parse_formula

# Hand-written catalog rows retired by derivation. Other parent-oxide
# elementals keep the STOICH_RATIOS fallback in evaporation.py.
CATALOG_DERIVED_STOICH_SPECIES = frozenset(
    {
        "Si",
        "TiO",
        "TiO2_gas",
        "CaO_gas",
        "AlO",
        "Al2O",
        "Al2",
        "Al2O2",
        "Al2O3_gas",
        "AlO2",
        "Ca2",
        "CrO",
        "SiO",
        "CrO2",
        "CrO3",
        "PO",
        "PO2",
        "P4O6",
        "P4O10",
        "P2",
        "P4",
        "K2",
        "K2O_gas",
        "Mg2",
        "MgO_gas",
        "Na2",
        "Na2O_gas",
        "Si2",
        "Si3",
        "SiO2_gas",
        "FeO_association_gas",
        "NiO_gas",
        "MnO_gas",
        "CoO_gas",
    }
)


def strip_phase(formula: str) -> str:
    text = str(formula).strip()
    if text.endswith(")") and "(" in text:
        return text[: text.rfind("(")]
    return text


def _molar_mass_g(formula: str) -> float:
    return parse_formula(strip_phase(formula)).molar_mass_g_per_mol()


def derive_stoich_oxide_per_vapor(
    *,
    formula: str,
    parent_oxide: str,
    reaction: Mapping[str, Any],
) -> tuple[float, float]:
    """Return ``(stoich_oxide_per_vapor, stoich_O2_per_vapor)`` in kg/kg.

    Association rows (parent formula equals vapor formula) transfer 1 kg/kg
    with no O2 coproduct — the gas-association reaction is pressure-only.
    Oxide evaporation uses the condensed reactant and signed O2 of ``reaction``.
    """
    vapor_key = strip_phase(formula)
    parent_key = strip_phase(parent_oxide)
    if vapor_key == parent_key:
        return 1.0, 0.0

    reactants = reaction.get("reactants") or []
    products = reaction.get("products") or []
    if not isinstance(reactants, list) or not isinstance(products, list):
        raise ValueError("reaction requires reactants and products lists")

    condensed_nu = 0.0
    condensed_formula = None
    vapor_nu = 0.0
    o2_nu = 0.0
    for sign, participants in ((-1.0, reactants), (1.0, products)):
        for item in participants:
            if not isinstance(item, Mapping):
                continue
            part_formula = strip_phase(str(item.get("formula") or ""))
            amount = float(item.get("stoichiometry"))
            if part_formula == "O2":
                o2_nu += sign * amount
                continue
            if sign < 0.0 and part_formula != vapor_key:
                condensed_nu += amount
                condensed_formula = part_formula
            if sign > 0.0 and part_formula == vapor_key:
                vapor_nu += amount
    if condensed_formula is None or condensed_nu <= 0.0 or vapor_nu <= 0.0:
        raise ValueError(
            f"cannot derive stoich for {formula!r} from parent {parent_oxide!r}"
        )
    m_cond = _molar_mass_g(condensed_formula)
    m_vap = _molar_mass_g(vapor_key)
    m_o2 = _molar_mass_g("O2")
    oxide = (condensed_nu * m_cond) / (vapor_nu * m_vap)
    o2 = (o2_nu * m_o2) / (vapor_nu * m_vap)
    if not math.isclose(oxide, 1.0 + o2, rel_tol=1e-6, abs_tol=1e-9):
        raise ValueError(
            f"{formula}: derived stoich does not conserve mass: "
            f"oxide={oxide} O2={o2}"
        )
    return oxide, o2
