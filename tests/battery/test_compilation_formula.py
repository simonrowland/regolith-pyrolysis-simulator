"""Raster spelling edits. Case and 0→O / 1→l only, and only in that direction."""

from simulator.battery.generators.compilation_formula import (
    aligned_raster_formula,
    compilation_formula_text,
)
from simulator.reference_data.janaf import formula_composition


def test_case_and_digit_to_element_follow_the_raster() -> None:
    assert aligned_raster_formula("ca", "Ca") == "Ca"
    assert aligned_raster_formula("caso4", "CaSO4") == "CaSO4"
    assert aligned_raster_formula("MgSio3", "MgSiO3") == "MgSiO3"
    assert aligned_raster_formula("CaMgSio4", "CaMgSiO4") == "CaMgSiO4"
    assert aligned_raster_formula("Si02", "SiO2") == "SiO2"
    assert aligned_raster_formula("Al203", "Al2O3") == "Al2O3"
    # Si02 parses as silicon with a count. SiO2 is the oxide.
    assert formula_composition("Si02") == (("Si", 2.0),)
    assert formula_composition("SiO2") == (("O", 2.0), ("Si", 1.0))
    assert formula_composition("Si02") != formula_composition("SiO2")
    assert formula_composition("Al203") == (("Al", 203.0),)
    assert formula_composition("Al203") != formula_composition("Al2O3")


def test_digit_to_element_is_directional() -> None:
    """A letter never becomes a digit, and a different element is not substituted."""

    assert aligned_raster_formula("Si02", "SiO2") == "SiO2"
    assert aligned_raster_formula("PeS", "FeS") is None
    assert aligned_raster_formula("Fe.947o", "Fe.9470") is None
    assert aligned_raster_formula("AlO(OH)", "A10(OH)") is None
    assert aligned_raster_formula("KAlSi309", "KAlSi3O8") is None
    assert aligned_raster_formula("cao2", "CeO2") is None
    assert aligned_raster_formula("HiS", "NiS") is None
    assert aligned_raster_formula("Ca", "Cs") is None
    assert aligned_raster_formula("Na3Al", "Na3AlF6") is None
    assert aligned_raster_formula("SiO2", "SiO2") is None
    assert aligned_raster_formula("", "ZnO") is None


def test_formatting_marks_are_not_a_formula_change() -> None:
    assert compilation_formula_text(r"$SiO_{2}$") == "SiO2"
    assert compilation_formula_text(r"\mathrm{CaO}") == "CaO"
    assert compilation_formula_text("SiO_2") == "SiO2"
    assert compilation_formula_text(r"$\alpha$-Ca2SiO4") == r"\alpha-Ca2SiO4"
    # Equal after flattening, so there is no raster correction to record.
    assert aligned_raster_formula("SiO_2", "SiO2") is None
