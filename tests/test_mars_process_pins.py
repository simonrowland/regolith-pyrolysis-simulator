"""Pre-move binary64 pins for the pressure owners (fd288e7d7)."""

import pytest

from simulator.core import _feedstock_surface_pressure_mbar
from simulator.pumping_cost import estimate_subambient_pump_cost


@pytest.mark.parametrize(
    "pressure,energy,speed",
    [
        (100.0, "0x1.b3b21a94bfa75p+3", "0x1.4c9418e2a4facp+5"),
        (0.1, "0x1.144d1020caaecp+6", "0x1.44c8a04d551cdp+15"),
        (610.0, "0x0.0p+0", "0x0.0p+0"),
    ],
)
def test_staged_compression_pre_move_pin(pressure, energy, speed):
    result = estimate_subambient_pump_cost(
        pressure, 1.0, 3600.0, ambient_pressure_pa=610.0,
        gas_temperature_K=500.0, max_pump_speed_m3_s=50.0,
        validated_line_conductance_m3_s=50.0,
    )
    assert result.energy_kWh.hex() == energy
    assert result.required_pump_speed_m3_s.hex() == speed


@pytest.mark.parametrize("declaration,expected", [
    ({}, 0.0),
    ({"surface_pressure_mbar": 6.0}, 6.0),
    ({"environment": {"surface_pressure_mbar": 0.72}}, 0.72),
    ({"surface_pressure_mbar": 1.0,
      "environment": {"surface_pressure_mbar": 6.0}}, 1.0),
])
def test_surface_pressure_pre_move_pin(declaration, expected):
    assert _feedstock_surface_pressure_mbar(declaration).hex() == expected.hex()
