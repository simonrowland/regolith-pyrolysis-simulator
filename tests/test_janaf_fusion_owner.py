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


def test_chromia_fusion_matches_printed_janaf_1800_k_rows():
    # Chase 1998, Cr-014/Cr-015, delta-f G column at 1800 K:
    # crystal -666.534, liquid -628.733 kJ/mol. Independent source anchor.
    assert janaf_fusion_energy("Cr2O3", Decimal("1800")).delta_g_fus_kJ_per_mol == Decimal("37.801")


@pytest.mark.parametrize("oxide", ["FeO", "Cr2O3", "TiO2", "Al2O3", "SiO2(II)"])
def test_fusion_correction_vanishes_at_its_table_crossing(oxide):
    fusion = janaf_fusion_energy(oxide, Decimal("1773.15"))
    at_crossing = janaf_fusion_energy(oxide, fusion.melting_temperature_K)
    assert abs(at_crossing.delta_g_fus_kJ_per_mol) < Decimal("1e-20")
