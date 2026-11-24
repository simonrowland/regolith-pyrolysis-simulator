"""Formula-owner pins for corpus spellings that cross formula syntax branches."""

import re

import pytest

from simulator.accounting.exceptions import UnknownSpeciesError
from simulator.accounting.formulas import (
    _ASCII_ADDUCT_UNITS,
    _is_ascii_adduct,
    atoms_parse_formula_else_janaf,
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


def test_valid_trailing_zero_decimal_subscript_and_malformed_adduct() -> None:
    assert dict(parse_formula("FeO1.0").elements) == {"Fe": 1.0, "O": 1.0}
    assert not _is_ascii_adduct("FeO1.0")
    for formula in ("3Al2O3.0SiO2", "3Al2O3.0", "3Al2O3..2SiO2"):
        assert not _is_ascii_adduct(formula)


def test_old_trailing_zero_mutation_fails_valid_subscript_assertion(monkeypatch) -> None:
    from simulator.accounting import formulas as formula_owner

    real_check = formula_owner._has_malformed_ascii_adduct

    def old_check(formula: str) -> bool:
        return real_check(formula) or any(
            re.search(r"\.0+$", segment)
            for segment in formula.split("·")
        )

    monkeypatch.setattr(formula_owner, "_has_malformed_ascii_adduct", old_check)

    def assert_valid_decimal() -> None:
        try:
            actual = dict(parse_formula("FeO1.0").elements)
        except UnknownSpeciesError as exc:
            raise AssertionError("valid trailing-zero decimal subscript was rejected") from exc
        assert actual == {"Fe": 1.0, "O": 1.0}

    with pytest.raises(AssertionError):
        assert_valid_decimal()


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


def test_leading_decimal_counts_and_decimal_adducts_stay_in_the_formula_owner() -> None:
    assert dict(parse_formula("Fe.9470").elements) == {"Fe": 0.947}
    assert dict(parse_formula("NbC.98").elements) == {"C": 0.98, "Nb": 1.0}
    assert dict(parse_formula("VC.88").elements) == {"C": 0.88, "V": 1.0}
    assert _is_ascii_adduct("H2SO4.6.5H2O")
    assert dict(parse_formula("H2SO4.6.5H2O").elements) == {
        "H": 15.0,
        "O": 10.5,
        "S": 1.0,
    }
    assert dict(parse_formula("CuO•CuSO₄").elements) == {
        "Cu": 2.0,
        "O": 5.0,
        "S": 1.0,
    }
    with pytest.raises(UnknownSpeciesError):
        parse_formula("3Al2O3.0")


def test_delimited_decimal_dot_survives_accounting_adduct_split(monkeypatch) -> None:
    from simulator.accounting import formulas as formula_owner

    sentinel = formula_owner._DECIMAL_DOT_SENTINEL
    formula = f"MgAl1{sentinel}5H2O.2H2O"
    expected = {"Al": 1.5, "H": 6.0, "Mg": 1.0, "O": 3.0}
    assert dict(parse_formula(formula).elements) == expected

    split = formula_owner._ascii_adduct_parts
    monkeypatch.setattr(
        formula_owner,
        "_ascii_adduct_parts",
        lambda text: split(text.replace(sentinel, ".")),
    )
    with pytest.raises((AssertionError, UnknownSpeciesError)):
        assert dict(parse_formula(formula).elements) == expected


def test_d2o_is_outside_the_adduct_vocabulary() -> None:
    """Accounting has no deuterium mass. Restore D2O only with a real D entry."""

    assert "D2O" not in _ASCII_ADDUCT_UNITS
    assert not _is_ascii_adduct("CaSO4.2D2O")
    try:
        parse_formula("CaSO4.2D2O")
    except UnknownSpeciesError:
        return
    raise AssertionError("CaSO4.2D2O parsed without a deuterium mass")


def test_formula_accounting_entrypoint_owns_delimited_decimals_and_d2o_refusal() -> None:
    assert atoms_parse_formula_else_janaf(r"$MgAl_{1.5}H_2O\cdot CO_2$") == (
        ("Al", 1.5),
        ("C", 1.0),
        ("H", 2.0),
        ("Mg", 1.0),
        ("O", 3.0),
    )
    assert atoms_parse_formula_else_janaf("CaSO4.2D2O") is None
    assert atoms_parse_formula_else_janaf("CaSO4·2D2O") is None
    assert atoms_parse_formula_else_janaf("CaSO4·1/2H2O") == (
        ("Ca", 1.0), ("H", 1.0), ("O", 4.5), ("S", 1.0)
    )
    assert atoms_parse_formula_else_janaf("CaSO4··H2O") is None
    assert atoms_parse_formula_else_janaf("CaSO4·") is None
    assert atoms_parse_formula_else_janaf("AsD3") == (("As", 1.0), ("D", 3.0))
    assert atoms_parse_formula_else_janaf("D2O(l)") == (("D", 2.0), ("O", 1.0))


def test_ascii_adduct_split_mutation_changes_mixed_decimal_result(monkeypatch) -> None:
    from simulator.accounting import formulas as formula_owner

    expected = {
        "Fe": 0.947,
        "H": 4.0,
        "O": 3.0,
    }
    assert dict(parse_formula("Fe0.947O.2H2O").elements) == expected
    real_parts = formula_owner._ascii_adduct_parts

    def reject_decimal_head(text: str):
        parts = real_parts(text)
        if parts is None or any("." in part for part in parts):
            return None
        return parts

    monkeypatch.setattr(formula_owner, "_ascii_adduct_parts", reject_decimal_head)
    try:
        mutated = dict(parse_formula("Fe0.947O.2H2O").elements)
    except UnknownSpeciesError:
        # Rejecting the split is also a regression: the printed hydrate is
        # valid and its decimal iron subscript must stay attached to Fe.
        return
    assert mutated != expected, "the split mutation did not break the consumer"
