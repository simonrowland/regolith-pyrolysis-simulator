"""F4 R-wall: condensation backstops authorize by declared class, not species name.

The pressure of a species that declares a wall reaction is the wall law.
The class gate still authorizes the legacy backstop, and that backstop
still applies only to a species with no wall-reaction block.
"""

from __future__ import annotations

import math

import pytest

from simulator import condensation
from simulator.condensation import (
    WallSaturationPressureRefusal,
    _antoine_psat_pa,
    _reactive_product_backstop_authorized,
    _stable_condensation_product_backstop_authorized,
    _sticking_reactivity_class,
    _sticking_wall_product_class,
    _wall_deposition_driving_pressure_pa,
)

# 1e-9 bar. PA_PER_BAR is 1e5, so this is 1e-4 Pa.
_PO2_PA = 1.0e-4
_WALL_K = 1500.0


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


def test_cro2_wall_law_is_not_the_stable_product_backstop() -> None:
    """CrO2 Psat at a finite pO2 is positive, so full-pressure driving fails.

    Restoring the stable-product branch (return the local pressure and stamp
    ``stable_condensation_product_backstop``) fails the undersaturated case:
    that branch drives with the full local pressure when Psat >= p_local.
    """

    psat = _antoine_psat_pa("CrO2", _WALL_K, wall_pO2_Pa=_PO2_PA)
    assert psat is not None and math.isfinite(psat) and psat > 0.0
    undersaturated = psat * 0.5
    diag: dict = {}
    driving = _wall_deposition_driving_pressure_pa(
        "CrO2",
        undersaturated,
        _WALL_K,
        wall_pO2_Pa=_PO2_PA,
        reactive_product_backstop=False,
        stable_condensation_product_backstop=True,
        diagnostic_out=diag,
    )
    assert driving == 0.0
    assert diag["wall_saturation_pressure_pa"] == pytest.approx(psat)
    assert diag["wall_saturation_pressure_refused"] is False
    assert diag.get("wall_saturation_pressure_status") != (
        "stable_condensation_product_backstop"
    )
    supersaturated = psat * 4.0
    driving_above = _wall_deposition_driving_pressure_pa(
        "CrO2",
        supersaturated,
        _WALL_K,
        wall_pO2_Pa=_PO2_PA,
        reactive_product_backstop=False,
        stable_condensation_product_backstop=True,
    )
    assert driving_above == pytest.approx(supersaturated - psat)
    assert driving_above < supersaturated


def test_sio_undersaturation_is_zero_not_the_reactive_backstop() -> None:
    """The former reactive branch at Psat >= p_local drove with the full pressure.

    Restoring ``effective_product_psat_pa = 0`` fails this: undersaturation
    would deposit the local pressure instead of zero.
    """

    psat = _antoine_psat_pa("SiO", _WALL_K, wall_pO2_Pa=_PO2_PA)
    assert psat is not None and math.isfinite(psat) and psat > 0.0
    diag: dict = {}
    driving = _wall_deposition_driving_pressure_pa(
        "SiO",
        psat * 0.5,
        _WALL_K,
        wall_pO2_Pa=_PO2_PA,
        reactive_product_backstop=True,
        diagnostic_out=diag,
    )
    assert driving == 0.0
    assert diag["wall_saturation_pressure_pa"] == pytest.approx(psat)
    assert diag.get("wall_saturation_pressure_status") != "reactive_product_backstop"


def test_covered_species_missing_po2_does_not_drive_with_full_pressure() -> None:
    for species, stable in (("SiO", False), ("CrO2", True)):
        with pytest.raises(WallSaturationPressureRefusal, match="wall_pO2_required"):
            _wall_deposition_driving_pressure_pa(
                species,
                10.0,
                _WALL_K,
                reactive_product_backstop=not stable,
                stable_condensation_product_backstop=stable,
            )


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
