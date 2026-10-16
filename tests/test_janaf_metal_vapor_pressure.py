"""Freshness and physical anchors for JANAF pure-metal saturation data."""

from __future__ import annotations

import math
from pathlib import Path

import pytest
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


def test_generated_metal_rows_reproduce_one_bar_fugacity_marker() -> None:
    data = yaml.safe_load(GENERATED_DATA.read_text(encoding="utf-8"))
    assert set(data["metals"]) == set(METAL_TABLES)

    for element, metal in data["metals"].items():
        assert "one_bar_fugacity_marker_K" in metal
        assert "normal_boiling_point_K" not in metal
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


def test_pressure_readers_interpolate_reciprocal_temperature_midpoint() -> None:
    data = yaml.safe_load(GENERATED_DATA.read_text(encoding="utf-8"))
    rows = data["metals"]["Na"]["rows"]
    lower = next(row for row in rows if row["temperature_K"] == 450.0)
    upper = next(row for row in rows if row["temperature_K"] == 500.0)

    midpoint_temperature = 2.0 * lower["temperature_K"] * upper["temperature_K"] / (
        lower["temperature_K"] + upper["temperature_K"]
    )
    midpoint_pressure = math.sqrt(lower["pressure_Pa"] * upper["pressure_Pa"])
    assert lower["pressure_Pa"] < midpoint_pressure < upper["pressure_Pa"]
    assert pressure_pa_at_temperature(data, "Na", midpoint_temperature) == pytest.approx(
        midpoint_pressure, rel=1e-12
    )
    assert wall_temperature_for_pressure(data, "Na", midpoint_pressure) == pytest.approx(
        midpoint_temperature, abs=1e-10
    )

    for row in (lower, upper):
        assert pressure_pa_at_temperature(data, "Na", row["temperature_K"]) == pytest.approx(
            row["pressure_Pa"], rel=1e-12
        )
        assert wall_temperature_for_pressure(data, "Na", row["pressure_Pa"]) == pytest.approx(
            row["temperature_K"], abs=1e-10
        )


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
