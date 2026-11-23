"""Formula-owner pins for corpus spellings that cross formula syntax branches."""

from simulator.accounting.formulas import (
    _is_integer_compound_adduct,
    parse_formula,
)


def test_parse_corpus_adducts_and_adjacent_parenthesized_groups() -> None:
    expected = {
        "(CO)(C)": {"C": 2.0, "O": 1.0},
        "3Al2O3.2SiO2": {"Al": 6.0, "O": 13.0, "Si": 2.0},
        "CaAl2Si2O7(OH)2.H2O": {"Al": 2.0, "Ca": 1.0, "H": 4.0, "O": 10.0, "Si": 2.0},
        "CaCO3.H2O": {"C": 1.0, "Ca": 1.0, "H": 2.0, "O": 4.0},
        "FeSO4.H2O": {"Fe": 1.0, "H": 2.0, "O": 5.0, "S": 1.0},
        "Mg2(OH)2CO3.3H2O": {"C": 1.0, "H": 8.0, "Mg": 2.0, "O": 8.0},
        "NaAlSi2O6.H2O": {"Al": 1.0, "H": 2.0, "Na": 1.0, "O": 7.0, "Si": 2.0},
        "5MgO.4CO2.5H2O": {"C": 4.0, "H": 10.0, "Mg": 5.0, "O": 18.0},
    }

    for formula, atoms in expected.items():
        assert dict(parse_formula(formula).elements) == atoms


def test_default_parser_keeps_general_ascii_adduct_grammar() -> None:
    assert dict(parse_formula("CaAl.SiCK").elements) == {
        "Al": 1.0,
        "C": 1.0,
        "Ca": 1.0,
        "K": 1.0,
        "Si": 1.0,
    }


def test_ascii_adduct_recognizer_rejects_malformed_parts() -> None:
    for formula in (
        "3Al2O3.0SiO2",
        "3Al2O3.0",
        "XxO.2SiO2",
        "3Al2O3..2SiO2",
        "3Al2O3.2SiO2.",
    ):
        assert not _is_integer_compound_adduct(
            formula, require_explicit_multipliers=True
        )
