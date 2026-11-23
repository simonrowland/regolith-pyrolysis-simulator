"""One owner of pure-sodium saturation pressure.

Expected pressures are the published Browning-Potter equation and Fink's
printed table, written here. They are not copied from the function.
"""

from __future__ import annotations

import math

import pytest

from engines.builtin.sodium_saturation import (
    SodiumSaturationError,
    sodium_saturation_pressure,
    sodium_saturation_temperature_K,
)
from simulator.physical_constants import GAS_CONSTANT
from simulator.vapour_rail.catalog import vapor_pressure_legacy_view

# Fink & Leibowitz ANL/RE-95/2 §1.2 Eq. (1).
_A = 11.9463
_B = 12633.73
_C = 0.4672
_FUSION_J_PER_MOL = 2598.0
_MELTING_K = 370.98
_STANDARD_ATMOSPHERE_PA = 101325.0


def _liquid_pa(temperature_K: float) -> float:
    ln_p_mpa = _A - _B / temperature_K - _C * math.log(temperature_K)
    return 1.0e6 * math.exp(ln_p_mpa)


def _block() -> dict:
    from simulator.config import load_config_bundle

    view = vapor_pressure_legacy_view(load_config_bundle().vapor_pressures)
    row = view["metals"]["Na"]
    assert "pure_component_antoine" not in row
    block = row["pure_component_saturation"]
    assert block["equation"] == "browning_potter"
    assert block["ln_p_mpa"] == {"a": _A, "b": _B, "c": _C}
    assert block["melting_temperature_K"] == _MELTING_K
    assert block["fusion_enthalpy_J_per_mol"] == _FUSION_J_PER_MOL
    assert block["certified_range_K"] == [864.0, 2499.0]
    return block


def test_yaml_block_matches_the_published_equation() -> None:
    _block()


@pytest.mark.parametrize(
    ("temperature_K", "printed_mpa", "relative"),
    [
        (400.0, 1.80e-10, 0.003),
        (500.0, 8.99e-8, 0.001),
    ],
)
def test_fink_printed_table(temperature_K: float, printed_mpa: float, relative: float) -> None:
    result = sodium_saturation_pressure(_block(), temperature_K)
    assert result.pressure_pa == pytest.approx(_liquid_pa(temperature_K), rel=1e-12)
    assert result.pressure_pa == pytest.approx(printed_mpa * 1.0e6, rel=relative)
    assert result.authority == "extrapolated"
    assert result.phase == "liquid"
    assert result.notice is not None
    assert "24%" in result.notice
    assert "[864, 2499]" in result.notice


def test_boiling_point_and_controller_check() -> None:
    block = _block()
    at_1156 = sodium_saturation_pressure(block, 1156.0)
    assert at_1156.authority == "certified"
    assert at_1156.notice is None
    assert at_1156.phase == "liquid"
    assert at_1156.reservoir_phase == "l"
    assert at_1156.pressure_pa == pytest.approx(_liquid_pa(1156.0), rel=1e-12)
    assert at_1156.pressure_pa == pytest.approx(1.01e5, rel=0.02)
    assert at_1156.pressure_pa == pytest.approx(_STANDARD_ATMOSPHERE_PA, rel=0.03)

    boiling_K = sodium_saturation_temperature_K(block, _STANDARD_ATMOSPHERE_PA)
    assert boiling_K == pytest.approx(1154.691, abs=0.001)
    assert sodium_saturation_pressure(block, boiling_K).pressure_pa == pytest.approx(
        _STANDARD_ATMOSPHERE_PA,
        rel=1e-9,
    )

    at_1723 = sodium_saturation_pressure(block, 1723.15)
    assert at_1723.authority == "certified"
    assert at_1723.pressure_pa == pytest.approx(3.10e6, rel=0.01)
    assert at_1723.pressure_pa / 1.0e5 == pytest.approx(31.0, rel=0.01)


@pytest.mark.parametrize("temperature_K", [864.0, 1156.0, 1723.15, 2499.0])
def test_certified_band_is_unflagged_liquid(temperature_K: float) -> None:
    result = sodium_saturation_pressure(_block(), temperature_K)
    assert result.authority == "certified"
    assert result.notice is None
    assert result.phase == "liquid"
    assert result.pressure_pa == pytest.approx(_liquid_pa(temperature_K), rel=1e-12)


def test_melting_point_continuity_of_the_solid_branch() -> None:
    block = _block()
    at_melt = sodium_saturation_pressure(block, _MELTING_K)
    below = math.nextafter(_MELTING_K, 0.0)
    solid = sodium_saturation_pressure(block, below)
    assert at_melt.phase == "liquid"
    assert solid.phase == "solid"
    assert solid.reservoir_phase == "cr"
    assert solid.pressure_pa == pytest.approx(at_melt.pressure_pa, rel=1e-6)
    assert at_melt.authority == "extrapolated"
    assert solid.authority == "extrapolated"


def test_solid_branch_at_313_kelvin_matches_the_fusion_correction() -> None:
    temperature_K = 313.15
    exponent = -(_FUSION_J_PER_MOL / GAS_CONSTANT) * (
        1.0 / temperature_K - 1.0 / _MELTING_K
    )
    ratio = math.exp(exponent)
    result = sodium_saturation_pressure(_block(), temperature_K)
    assert ratio == pytest.approx(0.86, rel=0.02)
    assert result.pressure_pa == pytest.approx(_liquid_pa(temperature_K) * ratio, rel=1e-9)
    assert result.phase == "solid"
    assert result.authority == "extrapolated"
    assert result.notice is not None
    assert "fusion" in result.notice
    assert "24%" in result.notice


def test_forty_celsius_is_a_flagged_number() -> None:
    result = sodium_saturation_pressure(_block(), 40.0 + 273.15)
    assert math.isfinite(result.pressure_pa)
    assert result.pressure_pa > 0.0
    assert result.authority == "extrapolated"
    assert result.phase == "solid"
    assert result.notice is not None


@pytest.mark.parametrize("temperature_K", [313.15, 400.0, 500.0, 523.15, 863.9])
def test_below_certified_band_is_flagged(temperature_K: float) -> None:
    result = sodium_saturation_pressure(_block(), temperature_K)
    assert result.authority == "extrapolated"
    assert result.notice is not None
    assert "[864, 2499]" in result.notice


@pytest.mark.parametrize("temperature_K", [0.0, -10.0, float("nan"), float("inf")])
def test_non_positive_temperature_is_an_error(temperature_K: float) -> None:
    with pytest.raises(SodiumSaturationError):
        sodium_saturation_pressure(_block(), temperature_K)


def test_unknown_equation_is_an_error() -> None:
    block = dict(_block())
    block["equation"] = "antoine"
    with pytest.raises(SodiumSaturationError):
        sodium_saturation_pressure(block, 1156.0)
