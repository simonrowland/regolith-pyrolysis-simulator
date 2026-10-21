"""Captured pure-material outputs before adding the mixed crown boundary.

These are refactor pins, not independent thermodynamic validation; the existing
JANAF/KEMS tests retain authority over them.
"""
import pytest

from simulator.refractory_vaporization import solve_congruent_vaporization


@pytest.mark.parametrize("material, oxygen, formula_flux, status", [
    ("MgO", "0x1.308567c487090p-3", "0x1.d5d05468380cap-8", "provisional_species_screen"),
    ("CaO", "0x1.2e14c1297a564p-9", "0x1.5915cdfc8a620p-12", "provisional_boundary_mismatch"),
    ("Al2O3", "0x1.69137898ac222p-17", "0x1.a6891cc5242fbp-18", "provisional_incomplete_species"),
])
def test_pure_boundary_unchanged(material, oxygen, formula_flux, status):
    result = solve_congruent_vaporization(material, 2000.0)
    assert result.surface_pO2_pa.hex() == oxygen
    assert result.formula_unit_flux_mol_m2_s.hex() == formula_flux
    assert result.certification_status == status
    assert result.oxygen_mode == "self_buffered"
    assert result.external_oxygen_atom_flux_mol_m2_s == 0
