"""Formula-owner pins for corpus spellings that cross formula syntax branches."""

from simulator.accounting.exceptions import UnknownSpeciesError
from simulator.accounting.formulas import (
    _ASCII_ADDUCT_UNITS,
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


def test_each_ascii_dot_is_classified_alone() -> None:
    """A decimal head and a later water are both real. Fe0.947O.2H2O is not one choice."""

    assert _is_ascii_adduct("Fe0.947O.2H2O")
    assert dict(parse_formula("Fe0.947O.2H2O").elements) == {
        "Fe": 0.947,
        "H": 4.0,
        "O": 3.0,
    }
    # The second dot is water, so oxygen stays 2 + 2, not the fractional O2.2 reading.
    assert _is_ascii_adduct("Fe0.947O2.2H2O")
    assert dict(parse_formula("Fe0.947O2.2H2O").elements) == {
        "Fe": 0.947,
        "H": 4.0,
        "O": 4.0,
    }


def test_d2o_is_outside_the_adduct_vocabulary() -> None:
    """Accounting has no deuterium mass. Restore D2O only with a real D entry."""

    assert "D2O" not in _ASCII_ADDUCT_UNITS
    assert not _is_ascii_adduct("CaSO4.2D2O")
    try:
        parse_formula("CaSO4.2D2O")
    except UnknownSpeciesError:
        return
    raise AssertionError("CaSO4.2D2O parsed without a deuterium mass")


def test_all_or_nothing_dot_split_refuses_mixed_decimal_adduct(monkeypatch) -> None:
    from simulator.accounting import formulas as formula_owner

    assert dict(parse_formula("Fe0.947O.2H2O").elements) == {
        "Fe": 0.947,
        "H": 4.0,
        "O": 3.0,
    }
    real_parts = formula_owner._ascii_adduct_parts

    def reject_decimal_head(text: str):
        parts = real_parts(text)
        if parts is None or any("." in part for part in parts):
            return None
        return parts

    monkeypatch.setattr(formula_owner, "_ascii_adduct_parts", reject_decimal_head)
    try:
        parse_formula("Fe0.947O.2H2O")
    except UnknownSpeciesError:
        return
    raise AssertionError("all-or-nothing split still accepted Fe0.947O.2H2O")
