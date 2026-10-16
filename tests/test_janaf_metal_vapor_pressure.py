"""Freshness and physical anchors for JANAF pure-metal saturation data."""

from __future__ import annotations

from pathlib import Path

import yaml

from simulator.reference_data.janaf_metal_vapor_pressure import (
    BOILING_CHECK_TOLERANCE_LOG10_BAR,
    METAL_TABLES,
    build_janaf_metal_vapor_pressure_data,
    pressure_pa_at_temperature,
    wall_temperature_for_pressure,
)

ROOT = Path(__file__).resolve().parents[1]
GENERATED_DATA = ROOT / "data" / "pure_metal_vapor_pressure_janaf.yaml"


def test_generated_pure_metal_vapor_pressure_data_is_fresh() -> None:
    assert yaml.safe_load(GENERATED_DATA.read_text(encoding="utf-8")) == (
        build_janaf_metal_vapor_pressure_data()
    )


def test_generated_metal_rows_reproduce_normal_boiling_pressure() -> None:
    data = yaml.safe_load(GENERATED_DATA.read_text(encoding="utf-8"))
    assert set(data["metals"]) == set(METAL_TABLES)

    for element, metal in data["metals"].items():
        check = metal["boiling_point_check"]
        assert check["absolute_log10_error_from_1_bar"] <= (
            BOILING_CHECK_TOLERANCE_LOG10_BAR
        ), element
        assert metal["temperature_range_K"][0] == 298.15
        assert metal["temperature_range_K"][1] <= 3500.0
        assert all(
            row["provenance"]["gas_temperature"]["locator"]
            and row["provenance"]["condensed_temperature"]["locator"]
            for row in metal["rows"]
        ), element


def test_crc_100_pa_temperature_anchors() -> None:
    """CRC vapor-pressure anchors independently check the JANAF curves."""

    data = yaml.safe_load(GENERATED_DATA.read_text(encoding="utf-8"))
    for element, published_temperature_K in (("Na", 697.0), ("Fe", 2091.0)):
        assert abs(
            wall_temperature_for_pressure(data, element, 100.0)
            - published_temperature_K
        ) <= 3.0
        assert abs(
            pressure_pa_at_temperature(data, element, published_temperature_K)
            - 100.0
        ) / 100.0 <= 0.05
