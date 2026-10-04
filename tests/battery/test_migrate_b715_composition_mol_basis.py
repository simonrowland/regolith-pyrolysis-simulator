"""PIN b-715: composition_mol reader always labels basis printed_mole_fraction.

Captures tip behaviour before the declared-basis accepting change. Never squash.
Cho & Suito 1994 (means of n runs) is the motivating extract shape: a basis
token beside the mole map is currently ignored.
"""

from __future__ import annotations

from decimal import Decimal

from simulator.battery.enums import AmountBasis
from simulator.battery.migrate import _mole_fraction_composition_from_values
from simulator.battery.records import as_decimal


_DERIVED_BASIS = "derived_mean_of_n_runs"
_MOLE_MAP = {"CaO": 0.45, "Al2O3": 0.35, "SiO2": 0.20}


def test_pin_b715_undeclared_composition_mol_is_printed_mole_fraction() -> None:
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


def test_pin_b715_nested_basis_token_is_ignored() -> None:
    """Current defect: composition_mol.basis is skipped; label stays printed."""

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
    assert composition.basis == "printed_mole_fraction"
    assert composition.as_map() == {
        "CaO": as_decimal("0.45"),
        "Al2O3": as_decimal("0.35"),
        "SiO2": as_decimal("0.20"),
    }


def test_pin_b715_sibling_composition_mol_basis_is_ignored() -> None:
    """Current defect: values.composition_mol_basis is not read."""

    composition, omitted = _mole_fraction_composition_from_values(
        {
            "composition_mol": dict(_MOLE_MAP),
            "composition_mol_basis": _DERIVED_BASIS,
        }
    )
    assert omitted == ()
    assert composition is not None
    assert composition.basis == "printed_mole_fraction"


def test_pin_b715_x_na2o_fallback_is_printed_mole_fraction() -> None:
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
