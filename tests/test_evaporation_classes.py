"""Degenerate-input guards and valid-path pins for evaporation_classes."""

from __future__ import annotations

import math

import pytest

from simulator.evaporation_classes import (
    e_down_from_s,
    flux_band_factors,
    interface_resistance_share,
    interface_share_s,
    report_species_class_diagnostics,
)


NAN = float("nan")
INF = float("inf")

_SHARE_KW = dict(
    species="Fe",
    alpha=0.1,
    T_K=1800.0,
    molar_mass_kg_mol=0.055845,
    overhead_pressure_pa=1000.0,
)


def test_flux_band_factors_valid_half_share_half_dex():
    lo, hi = flux_band_factors(0.5, 0.5)
    assert lo == pytest.approx(10.0 ** (-0.25))
    assert hi == pytest.approx(10.0 ** 0.25)


def test_flux_band_factors_limiting_cases():
    assert flux_band_factors(0.0, 0.5) == (1.0, 1.0)
    lo, hi = flux_band_factors(1.0, 0.5)
    assert lo == pytest.approx(10.0 ** (-0.5))
    assert hi == pytest.approx(10.0 ** 0.5)
    lo_hi_s, hi_hi_s = flux_band_factors(2.0, 0.5)
    assert (lo_hi_s, hi_hi_s) == (lo, hi)


@pytest.mark.parametrize(
    ("s", "residual"),
    [
        (NAN, 0.5),
        (0.5, NAN),
        (INF, 0.5),
        (0.5, INF),
        (-1.0, 0.5),
    ],
)
def test_flux_band_factors_refuses_nonfinite_or_negative_s(s, residual):
    with pytest.raises(ValueError, match="must be"):
        flux_band_factors(s, residual)


def test_e_down_from_s_valid_half_share_half_dex():
    expected = math.log10(1.0 + 0.5 * (10.0**0.5 - 1.0)) / 0.5
    assert e_down_from_s(0.5, 0.5) == pytest.approx(expected)
    assert e_down_from_s(0.0, 0.5) == 0.0
    assert e_down_from_s(1.0, 0.5) == pytest.approx(1.0)


def test_e_down_from_s_s_above_one_still_evaluates():
    # Physical s≤1 is a Bucket B proposal; this pin records current behaviour.
    assert e_down_from_s(2.0, 0.5) == pytest.approx(
        math.log10(1.0 + 2.0 * (10.0**0.5 - 1.0)) / 0.5
    )


@pytest.mark.parametrize(
    ("s", "delta"),
    [
        (NAN, 0.5),
        (0.5, NAN),
        (INF, 0.5),
        (0.5, INF),
        (-0.1, 0.5),
    ],
)
def test_e_down_from_s_refuses_nonfinite_or_negative_s(s, delta):
    with pytest.raises(ValueError, match="must be"):
        e_down_from_s(s, delta)


def test_e_down_from_s_zero_delta_still_refused():
    with pytest.raises(ValueError, match="non-zero"):
        e_down_from_s(0.5, 0.0)


def test_interface_resistance_share_valid_pin_unchanged():
    series = interface_resistance_share(**_SHARE_KW)
    assert series.flux_kg_s_m2 == pytest.approx(3.320903236135304e-06)
    assert interface_share_s(series) == pytest.approx(0.0430930618815259)


def test_interface_resistance_share_zero_overhead_is_vacuum_hkl():
    series = interface_resistance_share(**{**_SHARE_KW, "overhead_pressure_pa": 0.0})
    assert series.r_gas == 0.0
    assert interface_share_s(series) == pytest.approx(1.0)
    assert series.flux_kg_s_m2 == pytest.approx(7.706352464035475e-05)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"overhead_pressure_pa": NAN},
        {"overhead_pressure_pa": INF},
        {"overhead_pressure_pa": -1.0},
        {"pipe_diameter_m": 0.0},
        {"pipe_diameter_m": NAN},
        {"pipe_diameter_m": -0.1},
        {"p_eq_pa": NAN},
        {"p_eq_pa": INF},
        {"p_bulk_pa": NAN},
        {"p_bulk_pa": INF},
        {"radial_stir_factor": NAN},
        {"radial_stir_factor": INF},
        {"radial_stir_factor": -1.0},
    ],
)
def test_interface_resistance_share_refuses_degenerate_transport_inputs(kwargs):
    with pytest.raises(ValueError, match="must be finite"):
        interface_resistance_share(**{**_SHARE_KW, **kwargs})


def test_report_species_class_diagnostics_class_alpha_fallback_unchanged():
    diag = report_species_class_diagnostics(
        "Fe",
        T_K=2000.0,
        overhead_pressure_pa=1000.0,
        vapor_pressure_data={},
    )
    assert diag.alpha_runtime is None
    assert diag.alpha_runtime_note.endswith("+class_alpha_fallback_for_s")
    assert diag.series is not None
    assert diag.series.alpha_intrinsic == pytest.approx(0.084)
