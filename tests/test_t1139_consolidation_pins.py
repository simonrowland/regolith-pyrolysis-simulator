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
        ("Al2O3", "2060", "11.8498", "2325.931928687196110210696921"),
        ("CaO", "2000", "29.537", "3200.0"),
        ("MgO", "2500", "14.650", "3104.945598417408506429277943"),
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
