"""Pins for t-1139 follow-up consolidations, taken before the moves.

cea_delta_fG_kJ_mol, JANAF fusion ΔG, and the Ga2O3→Ga oxide balance are
the numeric outputs the shared kernel, interpolator, and balancer must keep.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from simulator.battery.generators.janaf import janaf_fusion_energy
from simulator.diagnostic_helpers.species_rail_differential import cea_delta_fG_kJ_mol
from simulator.vapour_rail.catalog import _formula_atoms
from simulator.vapour_rail.stoich import balance_oxide_evaporation
from simulator.diagnostic_helpers.species_rail import (
    parse_formula_composition,
    parse_formula_elements,
)


@pytest.mark.parametrize(
    ("formula", "expected"),
    (
        ("Fe.947O", (("Fe", "0x1.e4dd2f1a9fbe7p-1"), ("O", "0x1.0000000000000p+0"))),
        ("Fe.90S", (("Fe", "0x1.ccccccccccccdp-1"), ("S", "0x1.0000000000000p+0"))),
        ("NaCl.2H2O", (("Cl", "0x1.999999999999ap-3"), ("H", "0x1.0000000000000p+1"), ("Na", "0x1.0000000000000p+0"), ("O", "0x1.0000000000000p+0"))),
        ("CuSO4.5H2O", (("Cu", "0x1.0000000000000p+0"), ("H", "0x1.0000000000000p+1"), ("O", "0x1.6000000000000p+2"), ("S", "0x1.0000000000000p+0"))),
        ("H2SO4.2H2O", (("H", "0x1.0000000000000p+2"), ("O", "0x1.4cccccccccccdp+2"), ("S", "0x1.0000000000000p+0"))),
        ("(Na.78K.22)AlSiO4", None),
    ),
)
def test_diagnostic_leading_dot_normalization_pin(formula, expected) -> None:
    """Capture existing diagnostic output before moving its normalization rule."""
    composition = parse_formula_composition(formula)
    actual = None if composition is None else tuple((e, n.hex()) for e, n in composition)
    assert actual == expected
    assert parse_formula_elements(formula) == (
        None if expected is None else tuple(e for e, _n in expected)
    )


@pytest.mark.parametrize(
    ("cea_key", "temperature_K", "expected_hex"),
    (
        ("O2", 1000.0, "0x0.0p+0"),
        ("O2", 1800.0, "0x0.0p+0"),
        ("SiO", 1800.0, "-0x1.eb8c909eeb554p+7"),
        ("Fe", 1600.0, "0x1.6cfad07445c44p+7"),
    ),
)
def test_cea_formation_gibbs_pin(cea_key, temperature_K, expected_hex) -> None:
    assert cea_delta_fG_kJ_mol(cea_key, temperature_K).hex() == expected_hex


@pytest.mark.parametrize(
    ("oxide", "temperature_K", "delta_g", "melting_K"),
    (
        ("Al2O3", "2060", "11.8498", "2326.528497409326424870466321"),
        ("CaO", "2000", "29.537", "3200.0"),
        ("MgO", "2500", "14.650", "3104.968203497615262321144674"),
        ("SiO2", "1933", "0.27784", "1994.469026548672566371681416"),
    ),
)
def test_janaf_fusion_energy_pin(oxide, temperature_K, delta_g, melting_K) -> None:
    fusion = janaf_fusion_energy(oxide, Decimal(temperature_K))
    assert str(fusion.delta_g_fus_kJ_per_mol) == delta_g
    assert str(fusion.melting_temperature_K) == melting_K


def test_ga2o3_to_ga_balance_pin() -> None:
    reaction = balance_oxide_evaporation(
        "Ga2O3",
        "Ga",
        parent_atoms=_formula_atoms("Ga2O3"),
        vapor_atoms=_formula_atoms("Ga"),
    )
    assert reaction["reactants"] == [
        {"formula": "Ga2O3(l)", "stoichiometry": 0.5},
    ]
    assert reaction["products"] == [
        {"formula": "Ga(g)", "stoichiometry": 1.0},
        {"formula": "O2(g)", "stoichiometry": 0.75},
    ]
