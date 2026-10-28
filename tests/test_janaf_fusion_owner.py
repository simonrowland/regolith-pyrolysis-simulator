"""Numeric parity pins committed before moving the shared JANAF owner."""

from decimal import Decimal

import pytest

from simulator.battery.generators.janaf import janaf_fusion_energy


@pytest.mark.parametrize(
    ("oxide", "delta_g", "crossing", "solid", "liquid"),
    [
        ("CaO", "35.0056220", "3200.0", "Ca-027", "Ca-028"),
        ("Al2O3", "22.4611725", "2326.528497409326424870466321", "Al-096", "Al-100"),
        ("MgO", "30.6109325", "3104.968203497615262321144674", "Mg-008", "Mg-009"),
        ("SiO2", "0.905994", "1994.469026548672566371681416", "O-035", "O-038"),
    ],
)
def test_existing_fusion_owner_numeric_identity(oxide, delta_g, crossing, solid, liquid):
    # Refactor parity only: exact Decimal outputs captured at 8e9bd1c9f.
    fusion = janaf_fusion_energy(oxide, Decimal("1773.15"))
    assert str(fusion.delta_g_fus_kJ_per_mol) == delta_g
    assert str(fusion.melting_temperature_K) == crossing
    assert (fusion.crystal_table, fusion.liquid_table) == (solid, liquid)
