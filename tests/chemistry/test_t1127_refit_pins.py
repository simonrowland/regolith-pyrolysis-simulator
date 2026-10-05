"""Pre-refit numeric pins for the Al, Si and Mn(l) Antoine sidecars."""

from __future__ import annotations

import pytest

from engines.antoine import _antoine_log10_pressure
from engines.builtin.vapor_pressure import vapor_pressure_antoine_coefficients


@pytest.mark.parametrize(
    ("species", "temperature_K", "expected_hex"),
    [
        ("Al", 1000.0, "0x1.a287b98fbfcb1p-10"),
        ("Al", 1300.0, "0x1.371b6dc583c7fp+1"),
        ("Al", 1500.0, "0x1.eb93be5cff585p+5"),
        ("Al", 2000.0, "0x1.610606a3701e7p+13"),
        ("Al", 2500.0, "0x1.edb2e20ea63c9p+17"),
        ("Si", 1700.0, "0x1.365f6e70ed99ap-1"),
        ("Si", 1300.0, "0x1.80d9ddee1e570p-18"),
        ("Si", 1500.0, "0x1.1b19188c0a93ap-8"),
        ("Si", 2000.0, "0x1.177e1fd3cdaadp+7"),
        ("Si", 2500.0, "0x1.bf4efe3baf739p+15"),
        ("Mn", 1600.0, "0x1.6e6d7e7b3b8abp+8"),
        ("Mn", 1300.0, "0x1.dc7f92d270a8bp+1"),
        ("Mn", 1500.0, "0x1.a13ea9f204004p+6"),
        ("Mn", 2000.0, "0x1.aaeac8e781922p+13"),
        ("Mn", 2500.0, "0x1.af6ce960ee404p+17"),
    ],
)
def test_t1127_current_pure_component_pressure_pin(
    vapor_pressure_data, species, temperature_K, expected_hex
):
    row = vapor_pressure_data["metals"][species]
    coefficients, _ = vapor_pressure_antoine_coefficients(row, temperature_K)
    pressure_pa = 10.0 ** _antoine_log10_pressure(
        float(coefficients["A"]),
        float(coefficients["B"]),
        float(coefficients.get("C", 0.0)),
        temperature_K,
    )

    assert pressure_pa.hex() == expected_hex
