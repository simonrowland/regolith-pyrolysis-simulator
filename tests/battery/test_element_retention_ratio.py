from __future__ import annotations

import pytest

from simulator.battery.enums import Quantity
from simulator.battery import residue
from simulator.battery.residue import (
    ResidueInventoryRefusal,
    _sossi_project_element_ppm,
)


def test_existing_sossi_inventory_projection_is_pinned_before_refactor() -> None:
    projected = _sossi_project_element_ppm(
        {"SiO2": 1.0e-3, "MnO": 1.0e-4, "TiO2": 2.0e-5},
        -3.0e-6,
        ("Mn", "Ti"),
    )

    assert projected["Mn"] == pytest.approx(79937.77912959337, rel=1e-13)
    assert projected["Ti"] == pytest.approx(13929.806724084485, rel=1e-13)


def test_element_retention_quantity_is_declared() -> None:
    assert hasattr(Quantity, "ELEMENT_RETENTION_RATIO")


def test_inventory_retention_uses_the_declared_printed_definition() -> None:
    retention = getattr(residue, "_element_retention_ratio_from_inventories", None)
    assert callable(retention)

    # Hand calculation, with CIAAW formula masses: initially 1 mol FeO + 1 mol
    # MgO weighs 71.844 + 40.304 = 112.148 g. After 80% of FeO and 40% of MgO
    # remain, the bulk weighs 0.8*71.844 + 0.4*40.304 = 73.5968 g, so
    # M_f/M_i = 73.5968/112.148 = 0.6562471. Fe mass retention is 0.8;
    # concentration retention is 0.8/0.6562471 = 1.219053. Majors lost from
    # the melt make the concentration FM exceed one while Fe mass still falls.
    initial = {"FeO": 1.0, "MgO": 1.0}
    final = {"FeO": 0.8, "MgO": 0.4}

    assert retention(initial, final, "Fe", "mass_retention") == pytest.approx(0.8)
    assert retention(
        initial, final, "Fe", "concentration_retention"
    ) == pytest.approx(1.2190530023044481)


def test_unprinted_fm_definition_is_a_typed_refusal() -> None:
    resolve = getattr(residue, "_printed_fm_definition", None)
    assert callable(resolve)
    with pytest.raises(ResidueInventoryRefusal) as exc:
        resolve(None)
    assert exc.value.reason == "fo_definition_unprinted"


@pytest.mark.parametrize(
    "missing_key,reason",
    (
        ("duration", "duration_missing"),
        ("temperature", "temperature_missing"),
        ("fO2", "fO2_missing"),
        ("starting_composition", "starting_composition_missing"),
        ("sample_area_or_geometry", "sample_area_or_geometry_missing"),
        ("gas_conditions", "gas_conditions_missing"),
    ),
)
def test_each_missing_transport_input_is_typed(missing_key, reason) -> None:
    require_inputs = getattr(
        residue, "_require_element_retention_transport_inputs", None
    )
    assert callable(require_inputs)
    inputs = {
        "duration": 900.0,
        "temperature": 1573.15,
        "fO2": -0.68,
        "starting_composition": {"FeO": 1.0},
        "sample_area_or_geometry": {"area_m2": 2.0e-5},
        "gas_conditions": {"total_pressure_Pa": 101325.0},
    }
    inputs[missing_key] = None
    with pytest.raises(ResidueInventoryRefusal) as exc:
        require_inputs(inputs)
    assert exc.value.reason == reason


def test_element_absent_from_integrated_inventory_is_unavailable() -> None:
    retention = getattr(residue, "_element_retention_ratio_from_inventories", None)
    assert callable(retention)
    with pytest.raises(ResidueInventoryRefusal) as exc:
        retention({"FeO": 1.0}, {"FeO": 0.5}, "Gd", "mass_retention")
    assert exc.value.reason == "element_unavailable"
