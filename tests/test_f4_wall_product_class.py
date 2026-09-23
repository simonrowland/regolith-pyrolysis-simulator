"""F4 R-wall: condensation backstops authorize by declared class, not species name."""

from __future__ import annotations

import pytest

from simulator import condensation
from simulator.condensation import (
    _reactive_product_backstop_authorized,
    _stable_condensation_product_backstop_authorized,
    _sticking_reactivity_class,
    _sticking_wall_product_class,
    _wall_deposition_driving_pressure_pa,
)


def test_sio_authorized_by_reactivity_class_not_name() -> None:
    assert _sticking_reactivity_class("SiO") == "reactive"
    assert _reactive_product_backstop_authorized("SiO") is True
    assert _stable_condensation_product_backstop_authorized("SiO") is False


def test_cro2_authorized_by_wall_product_class_not_name() -> None:
    assert _sticking_reactivity_class("CrO2") == "physisorbing"
    assert _sticking_wall_product_class("CrO2") == "stable_condensation_product"
    assert _stable_condensation_product_backstop_authorized("CrO2") is True
    assert _reactive_product_backstop_authorized("CrO2") is False


def test_reactive_backstop_accepts_any_reactive_species(monkeypatch) -> None:
    """A future reactive stamp must follow the class path, not raise on name."""

    monkeypatch.setitem(
        condensation.STICKING_DATA["reactivity_class_by_species"],
        "Mg",
        "reactive",
    )
    assert _reactive_product_backstop_authorized("Mg") is True
    driving = _wall_deposition_driving_pressure_pa(
        "Mg",
        10.0,
        300.0,
        vapor_pressure_data={},
        reactive_product_backstop=True,
        diagnostic_out={},
    )
    assert driving == pytest.approx(10.0)


def test_stable_backstop_rejects_undeclared_species() -> None:
    with pytest.raises(ValueError, match="wall_product_class"):
        _wall_deposition_driving_pressure_pa(
            "Fe",
            10.0,
            300.0,
            vapor_pressure_data={},
            reactive_product_backstop=False,
            stable_condensation_product_backstop=True,
        )


def test_stable_backstop_accepts_declared_cro2() -> None:
    diag: dict = {}
    driving = _wall_deposition_driving_pressure_pa(
        "CrO2",
        7.5,
        300.0,
        vapor_pressure_data={},
        reactive_product_backstop=False,
        stable_condensation_product_backstop=True,
        diagnostic_out=diag,
    )
    assert driving == pytest.approx(7.5)
    assert diag["wall_saturation_pressure_status"] == "stable_condensation_product_backstop"


def test_mutation_reactivity_class_is_what_drives_reactive_auth(monkeypatch) -> None:
    assert _reactive_product_backstop_authorized("SiO") is True
    monkeypatch.setattr(
        condensation,
        "_sticking_reactivity_class",
        lambda _species: "physisorbing",
    )
    assert _reactive_product_backstop_authorized("SiO") is False


def test_mutation_wall_product_class_is_what_drives_stable_auth(monkeypatch) -> None:
    assert _stable_condensation_product_backstop_authorized("CrO2") is True
    monkeypatch.setattr(
        condensation,
        "_sticking_wall_product_class",
        lambda _species: None,
    )
    assert _stable_condensation_product_backstop_authorized("CrO2") is False
