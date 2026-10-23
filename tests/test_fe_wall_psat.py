"""t-1484 Fe wall pressure: tests-only baseline before JANAF intake."""

import pytest

from simulator import condensation


@pytest.mark.parametrize("temperature, pressure_hex", [
    (1400, "0x1.3c937e7b26f48p-7"),
    (1600, "0x1.7d0efb6f2ca55p-2"),
    (1800, "0x1.97891a48f18eap+2"),
    (1923, "0x1.b3b1e8c849a46p+4"),
    (2000, "0x1.edfb3ac884c5ep+5"),
    (2300, "0x1.bc70f4034e423p+9"),
])
def test_fe_wall_pressure_baseline(temperature, pressure_hex):
    diagnostic = {}
    condensation._wall_deposition_driving_pressure_pa(
        "Fe", 200.0, temperature, diagnostic_out=diagnostic,
    )
    assert diagnostic["wall_saturation_pressure_pa"].hex() == pressure_hex
    assert diagnostic["wall_saturation_pressure_refused"] is False


def test_fe_dew_baseline_has_no_certified_range():
    diagnostic = condensation.antoine_dew_temperature_diagnostic("Fe", 154.6)
    assert diagnostic["status"] == "unavailable_no_certified_antoine_range"
    assert diagnostic["temperature_K"] is None
    assert diagnostic["routing_authority"] is False
