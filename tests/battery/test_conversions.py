"""Unit/standard-state conversion helpers. Derivations live in identity.py."""

from __future__ import annotations

from decimal import Decimal
from fractions import Fraction

from simulator.battery.enums import Engine, PerBasis, Phase, Rail
from simulator.battery.identity import (
    R_J_PER_MOL_K,
    THERMOCHEMICAL_CALORIE_J,
    atm_to_pa,
    bar_to_pa,
    celsius_to_kelvin,
    kcal_th_to_kJ_per_mol,
    log10K_from_delta_fG_kJ_mol,
    rescale_energy_per_basis,
    standard_pressure_delta_g_kJ_per_mol,
)
from simulator.battery.records import Species, State
from tests.battery.factories import formation_reaction


def test_thermochemical_calorie_kcal_to_kj() -> None:
    assert THERMOCHEMICAL_CALORIE_J == Decimal("4.184")
    assert kcal_th_to_kJ_per_mol("1") == Decimal("4.184")
    # Sanity: B689 AgS(g) 298.15 K 80.700 kcal/mol → 337.6488 kJ/mol.
    assert kcal_th_to_kJ_per_mol("80.700") == Decimal("337.6488")


def test_standard_pressure_0p1_mpa_vs_1_atm() -> None:
    assert bar_to_pa("1") == Decimal("100000")
    assert atm_to_pa("1") == Decimal("101325")
    assert bar_to_pa("1") != atm_to_pa("1")
    # Δn_g = 1, 298.15 K, 101325/100000 → ≈ 0.033 kJ/mol.
    delta = standard_pressure_delta_g_kJ_per_mol(
        1, Decimal("298.15"), bar_to_pa("1"), atm_to_pa("1")
    )
    assert Decimal("0.03") < delta < Decimal("0.04")


def test_celsius_to_kelvin_is_exact_no_rounding_band() -> None:
    assert celsius_to_kelvin(Decimal("1370")) == Decimal("1643.15")
    assert celsius_to_kelvin(Decimal("1370")) != Decimal("1643")


def test_log10k_from_delta_fg_o2_and_ags_grain() -> None:
    assert log10K_from_delta_fG_kJ_mol(Decimal("0"), Decimal("298.15")) == Decimal("0")
    recomputed = log10K_from_delta_fG_kJ_mol(Decimal("337.6488"), Decimal("298.15"))
    assert abs(recomputed - Decimal("-59.154")) < Decimal("0.002")


def test_per_mol_o2_rescaling_cao() -> None:
    product = Species("CaO", Phase.CR, polymorph=State.of("lime"))
    metal = Species("Ca", Phase.L)
    reaction = formation_reaction(product, metal, Fraction(1), Fraction(1, 2))
    # |ν_product|/|ν_O2| = 1 / (1/2) = 2 on the per-formula write-up.
    per_species = Decimal("100")
    per_o2 = rescale_energy_per_basis(per_species, PerBasis.MOL_SPECIES, PerBasis.MOL_O2, reaction)
    assert per_o2 == Decimal("200")
    assert rescale_energy_per_basis(per_o2, PerBasis.MOL_O2, PerBasis.MOL_SPECIES, reaction) == per_species
    from simulator.battery.records import Reaction, ReactionTerm

    doubled = Reaction(
        (
            ReactionTerm(product, Fraction(2)),
            ReactionTerm(metal, Fraction(-2)),
            ReactionTerm(Species("O2", Phase.G), Fraction(-1)),
        )
    )
    assert rescale_energy_per_basis(per_species, PerBasis.MOL_SPECIES, PerBasis.MOL_O2, doubled) == Decimal("200")


def test_engine_enum_is_closed_and_includes_imcc_sf04() -> None:
    """Owner steer: first-class engines, never derived from resolve_backend."""

    assert Engine.IMCC_SF04.value == "imcc_sf04"
    assert Engine.IMCC_SF04_EXT.value == "imcc_sf04_ext"
    assert {e.value for e in Engine} == {
        "internal-analytical",
        "nasa_cea_9",
        "ellingham",
        "antoine_sidecar",
        "catalog_evaluator",
        "alphamelts",
        "thermoengine",
        "vaporock",
        "magemin",
        "imcc_sf04",
        "imcc_sf04_ext",
    }
    import ast
    from pathlib import Path

    import simulator.battery.enums as enums_mod

    tree = ast.parse(Path(enums_mod.__file__).read_text(encoding="utf-8"))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    imported.update(
        n.names[0].name if isinstance(n, ast.Import) else ""
        for n in ast.walk(tree)
        if isinstance(n, ast.Import)
    )
    assert "resolve_backend" not in imported
    assert not any(
        isinstance(n, ast.Name) and n.id == "resolve_backend" for n in ast.walk(tree)
    )


def test_eight_rails_are_the_owner_bound_set() -> None:
    assert {r.value for r in Rail} == {
        "vapour",
        "melt_activity",
        "thermochemistry",
        "SiO_evolution",
        "pyrolysis_yield",
        "wall_deposition",
        "redox",
        "alkali_shuttle",
    }
    assert R_J_PER_MOL_K > 0
