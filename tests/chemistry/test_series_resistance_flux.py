"""Helper-level limit tests for the 0.5.2 Phase B series-resistance flux.

The end-to-end golden fixtures (``tests/fixtures/sio_yield/*.json``) exercise
the integrated route, but the codex /review + gstack /review concern-diverse
sweep flagged that the new helper's regime limits (`f=0` viscous,
`f=1` free-molecular, smooth transition at `f=0.5`), the operator-level
``stir_factor`` clamp, and the non-finite-input defensive paths had no
direct unit coverage. This file fills that gap.

References:
 - Phase B P1 (codex challenge): NaN / out-of-range regime_factor and
   stir_factor must not silently route to either the unbounded HKL value
   or the max-Sherwood enhancement.
 - Phase B P1 (gstack review subagent): clamp-asymmetry between the
   condensation Sherwood path and the older evaporation linear-multiplier
   path is closed by canonical ``clamp_stir_factor`` in ``simulator/state``.
"""

from __future__ import annotations

import math

import pytest

from simulator.condensation import (
    _series_resistance_deposition_flux_mol_m2_s,
    _stirring_enhanced_sherwood,
)
from simulator.state import MAX_STIR_FACTOR, clamp_stir_factor


# ---------------------------------------------------------------------------
# clamp_stir_factor: canonical operator-boundary helper
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw,expected",
    [
        # Defensive defaults: non-numeric / non-finite / bool collapse
        # to the operator-facing fail-closed default 0.0 (which the
        # evaporation consumer reads as "halt evap" and the
        # condensation Sherwood helper floors at the laminar baseline).
        (None, 0.0),
        ("bad", 0.0),
        (float("nan"), 0.0),
        (float("inf"), 0.0),
        (float("-inf"), 0.0),
        (True, 0.0),   # bool subclass of int; rejected explicitly
        (False, 0.0),  # same
        (-5.0, 0.0),
        # Halt-evap signal preserved: stir_factor = 0 is a legitimate
        # operator control (pre-Phase B halted evap; canonical clamp
        # must preserve that).
        (0.0, 0.0),
        # Sub-laminar values pass through; condensation's
        # ``_stirring_enhanced_sherwood`` applies its own floor at 1.0
        # for Sherwood physics. Evap reads the sub-laminar multiplier
        # directly (legitimate "halve evap" operator control).
        (0.5, 0.5),
        (1.0, 1.0),
        (6.0, 6.0),
        (MAX_STIR_FACTOR, MAX_STIR_FACTOR),
        # Above-ceiling values clamp to MAX_STIR_FACTOR.
        (50.0, MAX_STIR_FACTOR),
        (1.0e9, MAX_STIR_FACTOR),
    ],
)
def test_clamp_stir_factor_handles_all_defensive_inputs(raw, expected):
    """Operator-boundary inputs collapse to ``[0.0, MAX_STIR_FACTOR]``.
    Non-finite / bool / non-numeric → ``0.0`` (fail-closed). Sub-laminar
    values are preserved so the evaporation linear-multiplier consumer
    can halt/halve evap; condensation's Sherwood helper applies its own
    physics floor at 1.0 above this canonical clamp."""
    assert clamp_stir_factor(raw) == pytest.approx(expected)


# ---------------------------------------------------------------------------
# _stirring_enhanced_sherwood: Frössling-style sqrt scaling
# ---------------------------------------------------------------------------

def test_stir_sherwood_no_stir_returns_laminar_asymptote():
    """``stir_factor = 1.0`` → laminar pipe asymptote ``Sh = 3.66``
    (BSL Eq 14.4-9). This is the no-stir baseline used by every code
    path that does not configure operator stirring (e.g., direct
    CondensationModel construction in unit tests)."""
    assert _stirring_enhanced_sherwood(1.0) == pytest.approx(3.66)


def test_stir_sherwood_c2a_default_matches_documented_value():
    """``stir_factor = 6.0`` (C2A default per ``MeltState.stir_factor``
    + ``setpoints.yaml § induction_stirring: 4-8x``) → ``Sh ~ 8.97``.
    Pinned so the CHANGELOG / docs/model-limitations claim stays
    honest: ``Sh_eff = 3.66 × sqrt(6) ≈ 9``."""
    assert _stirring_enhanced_sherwood(6.0) == pytest.approx(
        3.66 * math.sqrt(6.0), rel=1e-12,
    )


def test_stir_sherwood_saturates_at_max_clamp():
    """``stir_factor`` beyond ``MAX_STIR_FACTOR`` saturates at the
    clamp (NOT at the raw input). Phase B's docs claim ``Sh_eff`` tops
    out near ``11.6`` at ``stir_factor = 10``; an operator override of
    1000 must NOT yield ``Sh_eff ≈ 116``."""
    sh_at_max = _stirring_enhanced_sherwood(MAX_STIR_FACTOR)
    assert _stirring_enhanced_sherwood(1000.0) == pytest.approx(sh_at_max)
    assert sh_at_max == pytest.approx(3.66 * math.sqrt(MAX_STIR_FACTOR))


@pytest.mark.parametrize(
    "pathological",
    [float("nan"), float("inf"), float("-inf"), -1.0, 0.0],
)
def test_stir_sherwood_non_finite_input_collapses_to_no_stir(pathological):
    """Codex /challenge Phase B P2: ``min(MAX, NaN) == MAX`` would
    silently promote a NaN override to max-stirring. The defensive
    path collapses non-finite + non-positive inputs to the no-stir
    baseline ``Sh = 3.66`` rather than letting them escape the clamp."""
    assert _stirring_enhanced_sherwood(pathological) == pytest.approx(3.66)


# ---------------------------------------------------------------------------
# _series_resistance_deposition_flux_mol_m2_s: regime + sanitisation
# ---------------------------------------------------------------------------

_SiO_DEFAULT_KWARGS = {
    "species": "SiO",
    "P_local_pa": 100.0,
    "T_surface_K": 1500.0,
    "alpha_s": 0.7,
    "pipe_diameter_m": 0.12,
    "T_gas_K": 1700.0,
    "overhead_pressure_pa": 1000.0,  # 10 mbar — C2A viscous regime
}


def _call(**overrides):
    kwargs = dict(_SiO_DEFAULT_KWARGS)
    kwargs.update(overrides)
    return _series_resistance_deposition_flux_mol_m2_s(**kwargs)


def test_series_flux_free_molecular_limit_is_pure_hkl():
    """``regime_factor = 1.0`` collapses the boundary-layer resistance
    weight ``(1 - f)`` to zero — the series form degenerates to pure
    HKL impingement (correct in the free-molecular regime where no
    continuum boundary layer exists)."""
    flux_freemol = _call(regime_factor=1.0, stir_factor=1.0)
    # Cross-check against the pure-HKL helper directly via flux balance:
    # at f=1 stirring should not affect the result.
    flux_freemol_stirred = _call(regime_factor=1.0, stir_factor=10.0)
    assert flux_freemol == pytest.approx(flux_freemol_stirred, rel=1e-12)


def test_series_flux_viscous_limit_is_mt_rate_limited():
    """``regime_factor = 0.0`` puts the full boundary-layer resistance
    in series with HKL. In viscous regime k_HKL >> k_MT, so the result
    should be approximately ``k_MT * driving_pressure`` (MT-rate-limited)."""
    flux_viscous = _call(regime_factor=0.0, stir_factor=1.0)
    flux_freemol = _call(regime_factor=1.0, stir_factor=1.0)
    # In viscous regime, k_MT (laminar, ~9e-5/Pa) is hundreds of times
    # smaller than k_HKL (~1e-2/Pa) at C2A conditions, so the series form
    # must be at least an order of magnitude smaller than pure HKL.
    assert flux_viscous < flux_freemol * 0.1


def test_series_flux_stirring_amplifies_viscous_flux():
    """Operator stirring enhances ``k_MT`` via the stir-Sherwood. In
    viscous regime (where MT rate-limits) more stirring → more flux."""
    flux_no_stir = _call(regime_factor=0.0, stir_factor=1.0)
    flux_c2a = _call(regime_factor=0.0, stir_factor=6.0)
    flux_max = _call(regime_factor=0.0, stir_factor=MAX_STIR_FACTOR)
    assert flux_no_stir < flux_c2a < flux_max
    # The ratio bounded by the Sherwood enhancement (sqrt(stir_factor))
    # because k_MT is the dominant resistance in this regime.
    ratio_c2a = flux_c2a / flux_no_stir
    assert 2.0 < ratio_c2a < math.sqrt(6.0) + 0.1


@pytest.mark.parametrize(
    "non_finite",
    [float("nan"), float("inf"), float("-inf")],
)
def test_series_flux_non_finite_regime_factor_is_treated_as_viscous(non_finite):
    """Codex /challenge Phase B P1: non-finite ``regime_factor`` (NaN,
    +/-inf) previously could route the helper into the free-molecular
    pure-HKL early-return branch even in viscous regime. The
    sanitisation treats non-finite as viscous (``f=0``) so the
    series-resistance branch carries — same result as
    ``regime_factor = 0.0``."""
    flux_bad = _call(regime_factor=non_finite)
    flux_viscous = _call(regime_factor=0.0)
    assert flux_bad == pytest.approx(flux_viscous, rel=1e-12)


def test_series_flux_finite_out_of_range_regime_factor_clamps_to_bounds():
    """Finite-but-out-of-range ``regime_factor`` values clamp into
    ``[0, 1]`` rather than escaping the regime weighting. Values >= 1
    saturate at f=1 (free-molecular, pure HKL — physically deep
    free-molecular regime); values <= 0 saturate at f=0 (viscous, full
    series resistance). This is the documented Phase B P1 fix per the
    codex /challenge worked example (`regime_factor=2.0` previously
    routed to the unbounded pure-HKL value)."""
    flux_high = _call(regime_factor=2.0)
    flux_freemol = _call(regime_factor=1.0)
    flux_low = _call(regime_factor=-1.0)
    flux_viscous = _call(regime_factor=0.0)
    assert flux_high == pytest.approx(flux_freemol, rel=1e-12)
    assert flux_low == pytest.approx(flux_viscous, rel=1e-12)


@pytest.mark.parametrize(
    "bad_field,bad_value",
    [
        ("T_surface_K", float("nan")),
        ("T_surface_K", float("inf")),
        ("T_surface_K", -1.0),
        ("P_local_pa", float("nan")),
        ("alpha_s", float("nan")),
        ("pipe_diameter_m", float("nan")),
        ("T_gas_K", float("nan")),
        ("T_gas_K", float("inf")),
    ],
)
def test_series_flux_non_finite_inputs_fail_closed(bad_field, bad_value):
    """Codex /challenge Phase B P1: any non-finite physical input
    must fail closed (return ``0.0``) rather than propagate ``NaN``
    or ``+inf`` into the downstream ledger. The mass-balance closure
    invariant (≤5e-12 % per AGENTS.md) cannot tolerate poisoned fluxes."""
    flux = _call(**{bad_field: bad_value})
    assert flux == 0.0


def test_series_flux_zero_alpha_returns_zero():
    """Existing alpha-gate (Tier 3 species without measured alpha) must
    stay intact. ``alpha_s = 0`` short-circuits the helper before the
    new regime / sanitisation branches."""
    assert _call(alpha_s=0.0) == 0.0


def test_series_flux_below_saturation_returns_zero():
    """``P_local < P_sat(T_surface)`` means there is no driving force;
    the helper must return ``0.0`` regardless of regime or stirring."""
    # At T_surface = 5000 K the SiO P_sat is astronomical and dominates
    # any realistic P_local.
    assert _call(P_local_pa=1.0, T_surface_K=5000.0) == 0.0
