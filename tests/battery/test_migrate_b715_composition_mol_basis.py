"""b-715: composition_mol reader honours declared basis; default unchanged.

PIN commit f8a834487 captured the pre-fix always-printed_mole_fraction
behaviour. This file now asserts the accepting contract.
"""

from __future__ import annotations

from simulator.battery.enums import AmountBasis
from simulator.battery.migrate import _mole_fraction_composition_from_values
from simulator.battery.records import as_decimal


_DERIVED_BASIS = "derived_mean_of_n_runs"
_MOLE_MAP = {"CaO": 0.45, "Al2O3": 0.35, "SiO2": 0.20}


def test_b715_undeclared_composition_mol_defaults_to_printed_mole_fraction() -> None:
    composition, omitted = _mole_fraction_composition_from_values(
        {"composition_mol": dict(_MOLE_MAP)}
    )
    assert omitted == ()
    assert composition is not None
    assert composition.basis == "printed_mole_fraction"
    assert composition.amount_basis is AmountBasis.MOLE_FRACTION
    assert composition.as_map() == {
        "CaO": as_decimal("0.45"),
        "Al2O3": as_decimal("0.35"),
        "SiO2": as_decimal("0.20"),
    }


def test_b715_nested_basis_token_is_honoured() -> None:
    """Cho & Suito shape: composition_mol.basis beside the mole map."""

    composition, omitted = _mole_fraction_composition_from_values(
        {
            "composition_mol": {
                "basis": _DERIVED_BASIS,
                **_MOLE_MAP,
            }
        }
    )
    assert omitted == ()
    assert composition is not None
    assert composition.basis == _DERIVED_BASIS
    assert composition.as_map() == {
        "CaO": as_decimal("0.45"),
        "Al2O3": as_decimal("0.35"),
        "SiO2": as_decimal("0.20"),
    }


def test_b715_sibling_composition_mol_basis_is_honoured() -> None:
    composition, omitted = _mole_fraction_composition_from_values(
        {
            "composition_mol": dict(_MOLE_MAP),
            "composition_mol_basis": _DERIVED_BASIS,
        }
    )
    assert omitted == ()
    assert composition is not None
    assert composition.basis == _DERIVED_BASIS


def test_b715_nested_basis_wins_over_sibling() -> None:
    composition, omitted = _mole_fraction_composition_from_values(
        {
            "composition_mol": {"basis": "nested_wins", **_MOLE_MAP},
            "composition_mol_basis": "sibling_loses",
        }
    )
    assert omitted == ()
    assert composition is not None
    assert composition.basis == "nested_wins"


def test_b715_descriptive_composition_basis_is_not_an_engine_token() -> None:
    """values.composition_basis is a note, not Composition.basis."""

    composition, omitted = _mole_fraction_composition_from_values(
        {
            "composition_mol": dict(_MOLE_MAP),
            "composition_basis": "mole_fraction",
        }
    )
    assert omitted == ()
    assert composition is not None
    assert composition.basis == "printed_mole_fraction"


def test_b715_x_na2o_fallback_defaults_to_printed_mole_fraction() -> None:
    composition, omitted = _mole_fraction_composition_from_values(
        {"X_Na2O_as_published": 0.4}
    )
    assert omitted == ()
    assert composition is not None
    assert composition.basis == "printed_mole_fraction"
    assert composition.as_map() == {
        "Na2O": as_decimal("0.4"),
        "SiO2": as_decimal("0.6"),
    }


def test_b715_x_na2o_fallback_honours_sibling_basis() -> None:
    composition, omitted = _mole_fraction_composition_from_values(
        {
            "X_Na2O_as_published": 0.4,
            "composition_mol_basis": _DERIVED_BASIS,
        }
    )
    assert omitted == ()
    assert composition is not None
    assert composition.basis == _DERIVED_BASIS
