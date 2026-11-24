"""Formula-owner pins for corpus spellings that cross formula syntax branches."""

from simulator.accounting.formulas import (
    _is_ascii_adduct,
    parse_formula,
)


def test_parse_corpus_adducts_and_adjacent_parenthesized_groups() -> None:
    expected = {
        "(CO)(C)": {"C": 2.0, "O": 1.0},
        "3Al2O3.2SiO2": {"Al": 6.0, "O": 13.0, "Si": 2.0},
        "Al2O3.3H2O": {"Al": 2.0, "H": 6.0, "O": 6.0},
        "CaAl2Si2O7(OH)2.H2O": {"Al": 2.0, "Ca": 1.0, "H": 4.0, "O": 10.0, "Si": 2.0},
        "CaCO3.H2O": {"C": 1.0, "Ca": 1.0, "H": 2.0, "O": 4.0},
        "CoSO4.7H2O": {"Co": 1.0, "H": 14.0, "O": 11.0, "S": 1.0},
        "FeSO4.H2O": {"Fe": 1.0, "H": 2.0, "O": 5.0, "S": 1.0},
        "Mg2(OH)2CO3.3H2O": {"C": 1.0, "H": 8.0, "Mg": 2.0, "O": 8.0},
        "NaAlSi2O6.H2O": {"Al": 1.0, "H": 2.0, "Na": 1.0, "O": 7.0, "Si": 2.0},
        "5MgO.4CO2.5H2O": {"C": 4.0, "H": 10.0, "Mg": 5.0, "O": 18.0},
    }

    for formula, atoms in expected.items():
        assert dict(parse_formula(formula).elements) == atoms


def test_unlisted_ascii_dot_suffix_is_not_an_adduct() -> None:
    assert not _is_ascii_adduct("CaAl.SiCK")


def test_ascii_adduct_recognizer_rejects_malformed_parts() -> None:
    for formula in (
        "3Al2O3.0SiO2",
        "3Al2O3.0",
        "3Al2O3..2SiO2",
        "3Al2O3.2SiO2.",
    ):
        assert not _is_ascii_adduct(formula)

    # The classifier checks separator syntax; element validation stays with
    # the formula parser.
    assert _is_ascii_adduct("XxO.2SiO2")
    for formula in (
        "Al2O3.3H2O",
        "CoSO4.7H2O",
        "5MgO.4CO2.5H2O",
        "Mg2(OH)2CO3.3H2O",
        "3Al2O3.2SiO2",
    ):
        assert _is_ascii_adduct(formula)
    assert not _is_ascii_adduct("MgAl1.5SiO3")
    assert not _is_ascii_adduct("Fe0.947O")
