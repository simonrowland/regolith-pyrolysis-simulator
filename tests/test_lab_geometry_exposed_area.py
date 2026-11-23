from __future__ import annotations

import pytest

from simulator.condensation import read_gram_lab_exposed_melt_area
from simulator.lab_geometry import LabGeometryError


def test_gram_lab_exposed_area_reader_returns_the_printed_numeric_value() -> None:
    geometry = {
        "scale": "gram_lab",
        "sample": {"exposed_melt_area_m2": 0.00013273228961416876},
    }

    assert read_gram_lab_exposed_melt_area(geometry) == pytest.approx(
        0.00013273228961416876
    )


def test_gram_lab_exposed_area_reader_keeps_numeric_strings_admitted() -> None:
    geometry = {
        "scale": "gram_lab",
        "sample": {"exposed_melt_area_m2": "0.000123"},
    }

    assert read_gram_lab_exposed_melt_area(geometry) == pytest.approx(0.000123)


def test_gram_lab_exposed_area_reader_refuses_a_missing_area() -> None:
    with pytest.raises(LabGeometryError) as caught:
        read_gram_lab_exposed_melt_area(
            {"scale": "gram_lab", "sample": {"mass_g": 2.0}}
        )

    assert caught.value.code == "exposed_melt_area_not_printed"


@pytest.mark.parametrize("value", (0.0, True, "bad"))
def test_gram_lab_exposed_area_reader_refuses_invalid_values(value) -> None:
    with pytest.raises(LabGeometryError) as caught:
        read_gram_lab_exposed_melt_area(
            {"scale": "gram_lab", "sample": {"exposed_melt_area_m2": value}}
        )

    assert caught.value.code == "invalid_lab_geometry_positive_value"


def test_gram_lab_exposed_area_reader_ignores_non_gram_lab_geometry() -> None:
    assert read_gram_lab_exposed_melt_area(
        {"scale": "industrial_pot", "sample": {"exposed_melt_area_m2": 0.1}}
    ) is None
