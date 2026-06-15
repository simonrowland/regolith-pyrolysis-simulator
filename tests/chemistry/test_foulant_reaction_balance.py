"""Atom-balance guard for diagnostic foulant reaction labels."""

from __future__ import annotations

from collections import defaultdict
from fractions import Fraction
from pathlib import Path
from typing import Mapping

import pytest
import yaml

from simulator.accounting.formulas import load_species_formulas, resolve_species_formula
from simulator.core import (
    STAGE0_CARBONATE_METAL_OXIDE_STOICH,
    STAGE0_CATION_SULFATE_OXIDE_PRODUCTS,
    STAGE0_CATION_SULFATE_OXIDE_STOICH,
    STAGE0_CATION_SULFATE_SULFIDE_PRODUCTS,
    STAGE0_CATION_SULFATE_SULFIDE_STOICH,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
FOULANT_THERMO = REPO_ROOT / "data" / "foulant_thermo.yaml"
SPECIES_CATALOG = REPO_ROOT / "data" / "species_catalog.yaml"


def _species_registry() -> Mapping[str, object]:
    return load_species_formulas(SPECIES_CATALOG)


def _parse_reaction_term(term: str) -> tuple[Fraction, str]:
    stripped = term.strip()
    if not stripped:
        raise AssertionError("empty reaction term")

    parts = stripped.split(maxsplit=1)
    if len(parts) == 2:
        try:
            return Fraction(parts[0]), parts[1].strip()
        except ValueError:
            pass
    return Fraction(1), stripped


def _net_reaction_atoms(
    reaction: str, registry: Mapping[str, object]
) -> dict[str, Fraction]:
    left, arrow, right = reaction.partition("->")
    if not arrow or "->" in right:
        raise AssertionError(f"reaction must contain exactly one ->: {reaction!r}")

    net: defaultdict[str, Fraction] = defaultdict(Fraction)
    for side, sign in ((left, Fraction(-1)), (right, Fraction(1))):
        terms = [term.strip() for term in side.split("+") if term.strip()]
        if not terms:
            raise AssertionError(f"reaction side is empty: {reaction!r}")
        for term in terms:
            coefficient, species = _parse_reaction_term(term)
            formula = resolve_species_formula(species, registry)
            for element, atom_count in formula.elements.items():
                net[element] += sign * coefficient * Fraction(str(atom_count))

    return {element: count for element, count in net.items() if count}


def _format_fraction(value: Fraction) -> str:
    if value.denominator == 1:
        return str(value.numerator)
    return f"{value.numerator}/{value.denominator}"


def _format_reaction(
    left: tuple[tuple[float, str], ...],
    right: tuple[tuple[float, str], ...],
) -> str:
    def term(coefficient: float, species: str) -> str:
        value = Fraction(str(coefficient))
        if value == 1:
            return species
        return f"{_format_fraction(value)} {species}"

    return (
        " + ".join(term(coefficient, species) for coefficient, species in left)
        + " -> "
        + " + ".join(term(coefficient, species) for coefficient, species in right)
    )


def _assert_reaction_balanced(
    row_key: str, reaction: str, registry: Mapping[str, object]
) -> None:
    imbalance = _net_reaction_atoms(reaction, registry)
    details = ", ".join(
        f"{element}={_format_fraction(count)}"
        for element, count in sorted(imbalance.items())
    )
    assert not imbalance, f"{row_key} reaction is not atom-balanced: {details}"


def test_all_foulant_dg_reactions_are_atom_balanced() -> None:
    with FOULANT_THERMO.open(encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}

    registry = _species_registry()
    for row_key, row in payload["foulant_dG"].items():
        _assert_reaction_balanced(row_key, row["reaction"], registry)


def test_stage0_cation_sulfate_code_tables_are_atom_balanced() -> None:
    registry = _species_registry()

    for sulfate, oxide in STAGE0_CATION_SULFATE_OXIDE_PRODUCTS.items():
        stoich = STAGE0_CATION_SULFATE_OXIDE_STOICH[sulfate]
        reaction = _format_reaction(
            ((stoich["feed"], sulfate), (stoich["C"], "C")),
            (
                (stoich["oxide"], oxide),
                (stoich["SO2"], "SO2"),
                (stoich["CO"], "CO"),
            ),
        )
        _assert_reaction_balanced(
            f"STAGE0_CATION_SULFATE_OXIDE_PRODUCTS[{sulfate!r}]",
            reaction,
            registry,
        )

    for sulfate, sulfide in STAGE0_CATION_SULFATE_SULFIDE_PRODUCTS.items():
        stoich = STAGE0_CATION_SULFATE_SULFIDE_STOICH[sulfate]
        reaction = _format_reaction(
            ((stoich["feed"], sulfate), (stoich["C"], "C")),
            ((stoich["sulfide"], sulfide), (stoich["CO"], "CO")),
        )
        _assert_reaction_balanced(
            f"STAGE0_CATION_SULFATE_SULFIDE_PRODUCTS[{sulfate!r}]",
            reaction,
            registry,
        )


def test_stage0_carbonate_code_table_is_atom_balanced() -> None:
    registry = _species_registry()

    for metal, oxide, atoms_per_oxide in STAGE0_CARBONATE_METAL_OXIDE_STOICH:
        carbonate = f"{metal}CO3" if atoms_per_oxide == 1.0 else f"{metal}2CO3"
        reaction = _format_reaction(
            ((1.0, carbonate),),
            ((1.0, oxide), (1.0, "CO2")),
        )
        _assert_reaction_balanced(
            f"STAGE0_CARBONATE_METAL_OXIDE_STOICH[{metal!r}]",
            reaction,
            registry,
        )


def test_foulant_reaction_balance_guard_rejects_unbalanced_oxygen_row() -> None:
    registry = _species_registry()

    with pytest.raises(AssertionError, match="O=3/2"):
        _assert_reaction_balanced(
            "old_FeS_roasting",
            "FeS + 3/4 O2 -> FeO + SO2",
            registry,
        )
