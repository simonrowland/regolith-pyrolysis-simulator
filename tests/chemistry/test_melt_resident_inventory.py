"""Identity of the melt-resident kilogram sum when no shuttle residue is present."""

from __future__ import annotations

from simulator.account_ids import SPENT_REDUCTANT_RESIDUE_ACCOUNT
from simulator.melt_resident import (
    CLEANED_MELT_ACCOUNT,
    melt_resident_parent_kg,
    melt_resident_species_kg,
)


def test_no_residue_inventory_keeps_cleaned_melt_floats():
    cleaned = {
        "Na2O": 0.08512345678901234,
        "FeO": -1.5e-16,
    }
    summed = melt_resident_species_kg({CLEANED_MELT_ACCOUNT: cleaned})
    parent = melt_resident_parent_kg(
        {CLEANED_MELT_ACCOUNT: cleaned},
        "Na2O",
    )

    assert set(summed) == set(cleaned)
    for species, kg in cleaned.items():
        assert summed[species].hex() == float(kg).hex()
    assert parent.hex() == float(cleaned["Na2O"]).hex()


def test_explicit_zero_residue_does_not_change_cleaned_parent_float():
    cleaned_na2o = 0.08512345678901234
    parent = melt_resident_parent_kg(
        {
            CLEANED_MELT_ACCOUNT: {"Na2O": cleaned_na2o},
            SPENT_REDUCTANT_RESIDUE_ACCOUNT: {"Na2O": 0.0},
        },
        "Na2O",
    )

    assert parent.hex() == float(cleaned_na2o).hex()


def test_species_sum_keeps_an_explicit_zero_key_from_residue():
    summed = melt_resident_species_kg({
        CLEANED_MELT_ACCOUNT: {"FeO": 1.25},
        SPENT_REDUCTANT_RESIDUE_ACCOUNT: {"SiO2": 0.0},
    })

    assert set(summed) == {"FeO", "SiO2"}
    assert summed["FeO"].hex() == float(1.25).hex()
    assert summed["SiO2"].hex() == float(0.0).hex()
