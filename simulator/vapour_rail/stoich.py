"""Oxide-evaporation mass stoichiometry from a compiled source reaction.

One helper (d-068). ``stoich_oxide_per_vapor`` and ``stoich_O2_per_vapor``
are kg/kg of vapor: parent-oxide mass and signed O2 coproduct per kg vapor,
with ``oxide = 1 + O2`` for mass closure.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

from simulator.accounting.formulas import parse_formula
from simulator.trace_oxide_parents import ledger_component_key

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


def balance_oxide_evaporation(
    parent_oxide: str,
    vapor_formula: str,
    *,
    parent_atoms: Mapping[str, float],
    vapor_atoms: Mapping[str, float],
) -> dict[str, Any]:
    """Balance parent(l) → vapor(g) ± O2(g) for one metal.

    Atom counts are ``catalog._formula_atoms`` results. This module does not
    import catalog: catalog already imports stoich.
    """
    metals = [symbol for symbol in vapor_atoms if symbol != "O"]
    if len(metals) != 1:
        raise ValueError(f"{vapor_formula}: expected one metal")
    metal = metals[0]
    parent_metal = float(parent_atoms.get(metal, 0.0))
    vapor_metal = float(vapor_atoms.get(metal, 0.0))
    if parent_metal <= 0.0 or vapor_metal <= 0.0:
        raise ValueError(f"{parent_oxide} does not supply {metal} for {vapor_formula}")
    nu_parent = vapor_metal / parent_metal
    nu_vapor = 1.0
    parent_oxygen = float(parent_atoms.get("O", 0.0))
    vapor_oxygen = float(vapor_atoms.get("O", 0.0))
    nu_o2 = (parent_oxygen * nu_parent - vapor_oxygen * nu_vapor) / 2.0
    reactants: list[dict[str, float | str]] = [
        {"formula": f"{parent_oxide}(l)", "stoichiometry": nu_parent}
    ]
    products: list[dict[str, float | str]] = [
        {"formula": f"{vapor_formula}(g)", "stoichiometry": nu_vapor}
    ]
    if nu_o2 > 0.0:
        products.append({"formula": "O2(g)", "stoichiometry": nu_o2})
    elif nu_o2 < 0.0:
        reactants.append({"formula": "O2(g)", "stoichiometry": -nu_o2})
    return {
        "id": f"{parent_oxide}_l_to_{vapor_formula}_g",
        "reactants": reactants,
        "products": products,
        "activity_input": {
            "component_id": f"{parent_oxide}(l)",
            "standard_state": {
                "convention": "raoultian_pure_endmember",
                "phase": "liquid",
                "reference_pressure_bar": 1.0,
            },
        },
    }


def strip_phase(formula: str) -> str:
    return ledger_component_key(formula)


def _molar_mass_g(formula: str) -> float:
    return parse_formula(strip_phase(formula)).molar_mass_g_per_mol()


def oxygen_coproduct_account(
    oxygen_destination: str | None,
    *,
    vapor_oxygen_atoms: float,
) -> str:
    """Account that receives a positive O2 coproduct.

    A declared destination wins. With none declared, metal vapour (no oxygen
    atoms in the vapor) credits ``reservoir.fo2_buffer`` and oxide vapour
    credits ``process.overhead_gas``. Negative O2 remains an overhead debit;
    this function is only the positive-coproduct choice.
    """

    destination = str(oxygen_destination or "")
    if destination == "reservoir.fo2_buffer" or (
        not destination and float(vapor_oxygen_atoms) <= 0.0
    ):
        return "reservoir.fo2_buffer"
    return "process.overhead_gas"


def reactant_masses_and_o2_per_vapor_kg(
    *,
    formula: str,
    reaction: Mapping[str, Any],
) -> tuple[dict[str, float], float]:
    """Condensed-reactant kg and signed O2 kg per kg of vapor.

    O2 is not a key in the reactant map. The reactant masses sum to
    ``1 + O2``. Keys are ledger component keys, with phase tags removed.
    """

    vapor_key = ledger_component_key(formula)
    reactants = reaction.get("reactants") or []
    products = reaction.get("products") or []
    if not isinstance(reactants, list) or not isinstance(products, list):
        raise ValueError("reaction requires reactants and products lists")

    condensed_g: dict[str, float] = {}
    vapor_nu = 0.0
    o2_nu = 0.0
    for sign, participants in ((-1.0, reactants), (1.0, products)):
        for item in participants:
            if not isinstance(item, Mapping):
                continue
            part = ledger_component_key(str(item.get("formula") or ""))
            amount = float(item.get("stoichiometry"))
            if part == "O2":
                o2_nu += sign * amount
                continue
            if sign < 0.0 and part != vapor_key:
                condensed_g[part] = condensed_g.get(part, 0.0) + (
                    amount * _molar_mass_g(part)
                )
            if sign > 0.0 and part == vapor_key:
                vapor_nu += amount
    if not condensed_g or vapor_nu <= 0.0:
        raise ValueError(f"cannot derive reactant masses for {formula!r}")
    denom = vapor_nu * _molar_mass_g(vapor_key)
    masses = {key: grams / denom for key, grams in condensed_g.items()}
    o2 = (o2_nu * _molar_mass_g("O2")) / denom
    oxide = sum(masses.values())
    if not math.isclose(oxide, 1.0 + o2, rel_tol=1e-6, abs_tol=1e-9):
        raise ValueError(
            f"{formula}: reactant masses do not conserve mass: "
            f"oxide={oxide} O2={o2}"
        )
    return masses, o2


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
