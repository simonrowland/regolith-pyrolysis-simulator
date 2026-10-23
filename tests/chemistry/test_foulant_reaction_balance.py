"""Atom-balance guard for diagnostic foulant reaction labels."""

from __future__ import annotations

from collections import defaultdict
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace
from typing import Mapping

import pytest
import yaml

import simulator.core as core
from simulator.accounting.formulas import load_species_formulas, resolve_species_formula

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


def _runtime_cation_sulfate_reaction(
    sulfate: str,
    product_mode: str,
    stoich: Mapping[str, float],
    registry: Mapping[str, object],
) -> str:
    feed_mass = resolve_species_formula(
        sulfate, registry
    ).molar_mass_kg_per_mol()
    runtime = SimpleNamespace(
        species_formula_registry=registry,
        inventory=SimpleNamespace(
            cation_sulfate_feed_kg={sulfate: stoich["feed"] * feed_mass},
            gas_volatiles_kg={},
            stage0_products_kg={},
            melt_oxide_kg={},
            sulfide_matte_kg={},
        ),
        _decrease_inventory_species=(
            core.PyrolysisSimulator._decrease_inventory_species
        ),
        _merge_masses=core.PyrolysisSimulator._merge_masses,
    )
    specs: list[dict] = []
    core.PyrolysisSimulator._apply_stage0_cation_sulfate_carbon_reactions(
        runtime,
        {"stage0_carbon_cleanup": {"cation_sulfate_product": product_mode}},
        stoich["C"],
        specs,
    )
    if len(specs) != 1:
        raise AssertionError(
            f"{sulfate} runtime emitted {len(specs)} sulfate reactions"
        )

    reaction_spec = specs[0]
    reactants_kg: dict[str, float] = {}
    for debit in reaction_spec["debits"]:
        for species, mass_kg in debit[1].items():
            reactants_kg[species] = reactants_kg.get(species, 0.0) + mass_kg

    products_kg: dict[str, float] = {}
    for field in ("products_kg", "oxide_products_kg", "sulfide_products_kg"):
        for species, mass_kg in reaction_spec[field].items():
            products_kg[species] = products_kg.get(species, 0.0) + mass_kg

    def reaction_terms(masses: Mapping[str, float]) -> tuple[tuple[float, str], ...]:
        return tuple(
            (
                mass_kg
                / resolve_species_formula(species, registry).molar_mass_kg_per_mol(),
                species,
            )
            for species, mass_kg in masses.items()
        )

    return _format_reaction(
        reaction_terms(reactants_kg),
        reaction_terms(products_kg),
    )


def test_stage0_cation_sulfate_code_tables_are_atom_balanced() -> None:
    _assert_stage0_cation_sulfate_code_tables_are_atom_balanced()


def _assert_stage0_cation_sulfate_code_tables_are_atom_balanced() -> None:
    registry = _species_registry()

    for sulfate in core.STAGE0_CATION_SULFATE_OXIDE_PRODUCTS:
        reaction = _runtime_cation_sulfate_reaction(
            sulfate,
            "oxide",
            core.STAGE0_CATION_SULFATE_OXIDE_STOICH[sulfate],
            registry,
        )
        _assert_reaction_balanced(
            f"STAGE0_CATION_SULFATE_OXIDE_PRODUCTS[{sulfate!r}]",
            reaction,
            registry,
        )

    for sulfate in core.STAGE0_CATION_SULFATE_SULFIDE_PRODUCTS:
        reaction = _runtime_cation_sulfate_reaction(
            sulfate,
            "sulfide",
            core.STAGE0_CATION_SULFATE_SULFIDE_STOICH[sulfate],
            registry,
        )
        _assert_reaction_balanced(
            f"STAGE0_CATION_SULFATE_SULFIDE_PRODUCTS[{sulfate!r}]",
            reaction,
            registry,
        )


def test_stage0_sulfate_guard_rejects_runtime_oxide_yield_mutation(
    monkeypatch,
) -> None:
    runtime_method = (
        core.PyrolysisSimulator._apply_stage0_cation_sulfate_carbon_reactions
    )

    def halve_runtime_oxide_yields(runtime, feedstock, carbon_mol_remaining, specs):
        remaining = runtime_method(
            runtime, feedstock, carbon_mol_remaining, specs
        )
        for spec in specs:
            spec["oxide_products_kg"] = {
                oxide: product_kg / 2.0
                for oxide, product_kg in spec["oxide_products_kg"].items()
            }
        return remaining

    monkeypatch.setattr(
        core.PyrolysisSimulator,
        "_apply_stage0_cation_sulfate_carbon_reactions",
        halve_runtime_oxide_yields,
    )

    with pytest.raises(AssertionError, match="Ca"):
        _assert_stage0_cation_sulfate_code_tables_are_atom_balanced()


def test_stage0_carbonate_code_table_is_atom_balanced() -> None:
    _assert_stage0_carbonate_code_table_is_atom_balanced()


def _assert_stage0_carbonate_code_table_is_atom_balanced() -> None:
    registry = _species_registry()

    class _CompleteDecompositionRuntime:
        def __init__(self) -> None:
            self.species_formula_registry = registry

        def _stage0_carbonate_decomposition_extent(
            self, *_args
        ) -> float:
            return 1.0

    runtime = _CompleteDecompositionRuntime()
    for metal, _, atoms_per_oxide in core.STAGE0_CARBONATE_METAL_OXIDE_STOICH:
        carbonate = f"{metal}CO3" if atoms_per_oxide == 1.0 else f"{metal}2CO3"
        feed_kg = resolve_species_formula(
            carbonate, registry
        ).molar_mass_kg_per_mol()
        oxide_kg, gas_kg, _, _ = (
            core.PyrolysisSimulator._decompose_single_carbonate_species(
                runtime, carbonate, feed_kg, {}, 0.0
            )
        )
        products_kg = {**oxide_kg, **gas_kg}
        products = tuple(
            (
                product_kg
                / resolve_species_formula(product, registry).molar_mass_kg_per_mol(),
                product,
            )
            for product, product_kg in products_kg.items()
        )
        reaction = _format_reaction(
            ((1.0, carbonate),),
            products,
        )
        _assert_reaction_balanced(
            f"STAGE0_CARBONATE_METAL_OXIDE_STOICH[{metal!r}]",
            reaction,
            registry,
        )


def test_stage0_carbonate_guard_rejects_runtime_divisor_mutation(monkeypatch) -> None:
    mutated_stoich = tuple(
        (metal, oxide, 3.0 if metal == "Na" else atoms_per_oxide)
        for metal, oxide, atoms_per_oxide in core.STAGE0_CARBONATE_METAL_OXIDE_STOICH
    )
    monkeypatch.setattr(core, "STAGE0_CARBONATE_METAL_OXIDE_STOICH", mutated_stoich)

    with pytest.raises(AssertionError, match="Na"):
        _assert_stage0_carbonate_code_table_is_atom_balanced()


def test_foulant_reaction_balance_guard_rejects_unbalanced_oxygen_row() -> None:
    registry = _species_registry()

    with pytest.raises(AssertionError, match="O=3/2"):
        _assert_reaction_balanced(
            "old_FeS_roasting",
            "FeS + 3/4 O2 -> FeO + SO2",
            registry,
        )
