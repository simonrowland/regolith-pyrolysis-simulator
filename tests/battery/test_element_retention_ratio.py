from __future__ import annotations

import pytest

from simulator.battery.enums import Quantity
from simulator.battery.residue import _sossi_project_element_ppm


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
