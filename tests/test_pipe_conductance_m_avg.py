"""W7 (CW5 historical-audit closure, 2026-05-28): live mole-weighted
M_avg for pipe-conductance density. Pre-W7 the conductance was
hardcoded to ``M_avg = 0.040 kg/mol`` "mix of SiO, Fe, Na vapors
~40 g/mol" — a placeholder that hid the factor-of-2 swing between
real recipe compositions (alkali sweep ~23 g/mol → Fe vapor mid
~46 g/mol → O2-dominant late ~32 g/mol).

These tests pin:
1. ``_mean_molar_mass_kg_mol`` math (mole-weighted, not mass-weighted).
2. Defensive fallback to ``DEFAULT_PIPE_M_AVG_KG_MOL`` for empty /
   None / unknown-only inputs.
3. Backward-compat invariant: ``_pipe_conductance`` without the new
   kwarg matches pre-W7 bit-for-bit.
4. ``estimate_transport_state`` passes evap_flux species through.
"""

from __future__ import annotations

import math

import pytest

from simulator.overhead import (
    DEFAULT_PIPE_M_AVG_KG_MOL,
    OverheadGasModel,
    _mean_molar_mass_kg_mol,
)
from simulator.state import (
    Atmosphere,
    EvaporationFlux,
    MOLAR_MASS,
    MeltState,
)


# ---------------------------------------------------------------------------
# 1. _mean_molar_mass_kg_mol — pure-species sanity
# ---------------------------------------------------------------------------

def test_pure_na_returns_na_molar_mass():
    """A pure-Na vapor mixture returns ``M_Na / 1000`` kg/mol exactly.
    Alkali sweep mid-recipe approaches this — ~23 g/mol; conductance
    is correspondingly ~40/23 ≈ 1.7× higher than the legacy
    placeholder."""
    M_avg = _mean_molar_mass_kg_mol({"Na": 1.0})
    assert M_avg == pytest.approx(MOLAR_MASS["Na"] / 1000.0)
    # Numerically: ~0.023 kg/mol.
    assert M_avg == pytest.approx(0.0230, abs=1e-3)


def test_pure_o2_returns_o2_molar_mass():
    """Late-recipe gas dominated by O2 disproportionation — pure O2
    gives ``M_O2 / 1000 ≈ 0.032 kg/mol``."""
    M_avg = _mean_molar_mass_kg_mol({"O2": 1.0})
    assert M_avg == pytest.approx(MOLAR_MASS["O2"] / 1000.0)


def test_pure_sio_returns_sio_molar_mass():
    """SiO sweep window — ~44 g/mol, well above the alkali band."""
    M_avg = _mean_molar_mass_kg_mol({"SiO": 1.0})
    assert M_avg == pytest.approx(MOLAR_MASS["SiO"] / 1000.0)


# ---------------------------------------------------------------------------
# 2. Mixture math: mole-weighted, not mass-weighted
# ---------------------------------------------------------------------------

def test_mixture_uses_mole_weighting_not_mass_weighting():
    """For an equal-MASS mixture of Na (23 g/mol) and Fe (56 g/mol),
    the moles aren't equal — Na contributes ~2.4× more moles per unit
    mass than Fe. The correct mole-weighted M_avg is
    ``Σ kg / Σ (kg/M) = 2 / (1/0.023 + 1/0.056) ≈ 0.0326`` kg/mol;
    a (wrong) mass-weighted average would give the arithmetic mean
    ``(0.023 + 0.056)/2 = 0.0395`` kg/mol. Pin the correct formula."""
    M_Na = MOLAR_MASS["Na"] / 1000.0  # 0.022990
    M_Fe = MOLAR_MASS["Fe"] / 1000.0  # 0.055845
    expected_mole_weighted = 2.0 / (1.0 / M_Na + 1.0 / M_Fe)
    M_avg = _mean_molar_mass_kg_mol({"Na": 1.0, "Fe": 1.0})
    assert M_avg == pytest.approx(expected_mole_weighted, rel=1e-9)
    # Ensure we did NOT accidentally implement the wrong (mass-
    # weighted) form.
    mass_weighted = (M_Na + M_Fe) / 2.0
    assert abs(M_avg - mass_weighted) > 0.005


def test_mixture_spans_known_recipe_range():
    """Sanity check: a realistic mid-recipe mixture (Na, SiO, Fe, O2)
    lands within the documented 0.023-0.046 kg/mol band."""
    mixture = {"Na": 0.4, "SiO": 1.0, "Fe": 0.6, "O2": 0.3}
    M_avg = _mean_molar_mass_kg_mol(mixture)
    assert 0.020 <= M_avg <= 0.050, (
        f"M_avg={M_avg} outside documented recipe range 0.023-0.046"
    )


# ---------------------------------------------------------------------------
# 3. Fallback / defensive paths
# ---------------------------------------------------------------------------

def test_none_input_returns_default_fallback():
    """``None`` (legacy caller without the kwarg) → ``DEFAULT_PIPE_
    M_AVG_KG_MOL`` (0.040) — preserves pre-W7 behaviour bit-
    for-bit."""
    assert _mean_molar_mass_kg_mol(None) == DEFAULT_PIPE_M_AVG_KG_MOL


def test_empty_mapping_returns_default_fallback():
    """An empty species dict (zero-flux warmup tick before any
    evaporation) → same fallback. The default 0.040 kg/mol is
    documented as the historical placeholder; matches the legacy
    ``M_avg = 0.040`` line."""
    assert _mean_molar_mass_kg_mol({}) == DEFAULT_PIPE_M_AVG_KG_MOL


def test_unknown_species_only_returns_default_fallback():
    """A mixture made entirely of species not in ``MOLAR_MASS`` (e.g.,
    a typo'd or experimental species) must NOT poison the denominator
    with a zero — fall back to the documented default."""
    M_avg = _mean_molar_mass_kg_mol({"ZZZ": 1.0, "Vibranium": 0.5})
    assert M_avg == DEFAULT_PIPE_M_AVG_KG_MOL


def test_negative_and_nan_masses_are_skipped():
    """Defensive: NaN or negative kg entries (poisoned upstream
    computation) get skipped, not folded into the mean. A pure
    Na mixture that has a NaN ghost entry still returns M_Na."""
    M_avg = _mean_molar_mass_kg_mol({
        "Na": 1.0,
        "Fe": float("nan"),
        "SiO": -1.0,
    })
    assert M_avg == pytest.approx(MOLAR_MASS["Na"] / 1000.0)


def test_zero_mass_entries_are_skipped():
    """Zero-mass entries are skipped (otherwise total_kg/total_mol
    stays defined but the implicit log doesn't change). Same fallback
    semantics."""
    M_avg = _mean_molar_mass_kg_mol({"Na": 1.0, "Fe": 0.0})
    assert M_avg == pytest.approx(MOLAR_MASS["Na"] / 1000.0)


# ---------------------------------------------------------------------------
# 4. _pipe_conductance backward-compat + new species kwarg
# ---------------------------------------------------------------------------

def _make_model() -> OverheadGasModel:
    model = OverheadGasModel({})
    return model


def test_pipe_conductance_no_kwarg_matches_legacy_fallback():
    """Backward-compat invariant: callers that don't pass
    ``species_kg_for_M_avg`` get the documented fallback density,
    which is the pre-W7 hardcoded 0.040 kg/mol value. Any legacy
    test / probe that called ``_pipe_conductance(p, T)`` directly
    must keep working."""
    model = _make_model()
    p_Pa = 1000.0
    T_C = 1500.0
    C_legacy = model._pipe_conductance(p_Pa, T_C)
    # Manually compute the expected legacy conductance.
    T_K = T_C + 273.15
    eta = 1.8e-5 * (T_K / 300.0) ** 0.7
    C_vol = math.pi * model.pipe_diameter_m ** 4 * p_Pa / (
        128.0 * eta * model.pipe_length_m
    )
    rho = p_Pa * DEFAULT_PIPE_M_AVG_KG_MOL / (8.314 * T_K)
    expected = C_vol * rho
    assert C_legacy == pytest.approx(expected, rel=1e-12)


def test_pipe_conductance_records_m_avg_fallback_engagement():
    engagements = []
    model = OverheadGasModel(
        {},
        degraded_path_engagement_recorder=lambda path, *, count: (
            engagements.append((path, count))
        ),
    )

    model._pipe_conductance(1000.0, 1500.0)
    model._pipe_conductance(
        1000.0,
        1500.0,
        species_kg_for_M_avg={"Na": 1.0},
    )

    assert engagements == [("pipe_m_avg_fallback", 1)]


def test_pipe_conductance_alkali_sweep_lower_density_than_legacy():
    """Mid-alkali-sweep gas (mostly Na ~23 g/mol) has lower mole-
    weighted M_avg than the legacy 40 g/mol placeholder → lower ρ
    → lower mass conductance. The factor is ``M_Na / 0.040`` ≈
    0.574 — the legacy code over-predicted conductance for alkali-
    dominated mixtures by ~70%."""
    model = _make_model()
    p_Pa = 1000.0
    T_C = 1500.0
    C_default = model._pipe_conductance(p_Pa, T_C)
    C_na = model._pipe_conductance(
        p_Pa, T_C, species_kg_for_M_avg={"Na": 1.0},
    )
    ratio = C_na / C_default
    expected = (MOLAR_MASS["Na"] / 1000.0) / DEFAULT_PIPE_M_AVG_KG_MOL
    assert ratio == pytest.approx(expected, rel=1e-9)
    assert ratio < 0.6  # Sanity check on the documented direction


def test_pipe_conductance_fe_vapor_higher_density_than_legacy():
    """Pure Fe vapor (56 g/mol) is heavier than the placeholder →
    higher ρ → higher mass conductance. Ratio is
    ``M_Fe / 0.040 ≈ 1.396``. Documents the factor-of-2 swing
    between extremes."""
    model = _make_model()
    p_Pa = 1000.0
    T_C = 1500.0
    C_default = model._pipe_conductance(p_Pa, T_C)
    C_fe = model._pipe_conductance(
        p_Pa, T_C, species_kg_for_M_avg={"Fe": 1.0},
    )
    ratio = C_fe / C_default
    expected = (MOLAR_MASS["Fe"] / 1000.0) / DEFAULT_PIPE_M_AVG_KG_MOL
    assert ratio == pytest.approx(expected, rel=1e-9)
    assert ratio > 1.0


# ---------------------------------------------------------------------------
# 4b. A2 (0.5.4.1): defensive input guards on T_K, L, d, p_mean_Pa
# ---------------------------------------------------------------------------

def test_pipe_conductance_zero_kelvin_returns_zero_no_crash():
    """T_K=0 (T_C=-273.15) is unreachable in valid recipes but a
    poisoned input MUST NOT raise ZeroDivisionError on the density
    divide. Fail-closed to 0.0 conductance instead."""
    model = _make_model()
    # T_C = -273.15 → T_K = 0
    result = model._pipe_conductance(1000.0, -273.15)
    assert result == 0.0


def test_pipe_conductance_below_absolute_zero_returns_zero_no_complex():
    """T_K < 0 (T_C < -273.15) would otherwise return a complex
    number from ``(T_K / 300.0) ** 0.7`` (fractional exponent of
    negative). Pre-A2 the ZeroDivisionError caught the T_K=0 case
    but the T_K<0 path would have leaked complex values into the
    snapshot. Fail-closed to 0.0."""
    model = _make_model()
    result = model._pipe_conductance(1000.0, -500.0)
    assert result == 0.0
    assert isinstance(result, float)


def test_pipe_conductance_zero_pipe_geometry_returns_zero():
    """Degenerate pipe geometry (L=0 or d=0) → zero conductance.
    A monkey-patched test sim could set this; guard catches it."""
    model = _make_model()
    saved_L = model.pipe_length_m
    saved_d = model.pipe_diameter_m
    try:
        model.pipe_length_m = 0.0
        assert model._pipe_conductance(1000.0, 1500.0) == 0.0
        model.pipe_length_m = saved_L
        model.pipe_diameter_m = 0.0
        assert model._pipe_conductance(1000.0, 1500.0) == 0.0
    finally:
        model.pipe_length_m = saved_L
        model.pipe_diameter_m = saved_d


def test_pipe_conductance_negative_pressure_clamps_to_zero():
    """Negative pressure is unphysical; guard clamps to 0. With p=0
    the conductance equation produces 0 mass flow regardless of
    other inputs. Mass-balance honesty: never return negative
    conductance."""
    model = _make_model()
    result = model._pipe_conductance(-1000.0, 1500.0)
    assert result == 0.0


def test_pipe_conductance_legal_inputs_still_work_after_guards():
    """Backward-compat: a legitimate call (positive T, positive
    geometry, positive p) returns the same conductance as before
    the A2 guards. Regression check that the guards didn't change
    the canonical path."""
    model = _make_model()
    # Reference value computed manually:
    p_Pa = 1000.0
    T_C = 1500.0
    T_K = T_C + 273.15
    eta = 1.8e-5 * (T_K / 300.0) ** 0.7
    C_vol = math.pi * model.pipe_diameter_m ** 4 * p_Pa / (
        128.0 * eta * model.pipe_length_m
    )
    rho = p_Pa * DEFAULT_PIPE_M_AVG_KG_MOL / (8.314 * T_K)
    expected = C_vol * rho
    actual = model._pipe_conductance(p_Pa, T_C)
    assert actual == pytest.approx(expected, rel=1e-12)


def test_estimate_transport_state_threads_evap_flux_species_through():
    """End-to-end: ``estimate_transport_state`` calls
    ``_pipe_conductance`` with ``evap_flux.species_kg_hr`` so the
    live mole-weighted M_avg is used. Pin this by comparing
    conductance for two mixtures with vastly different M_avg —
    a pure-Na flux vs a pure-Fe flux — at the same total flow
    rate; the conductance scales with ``M_Fe / M_Na ≈ 2.43``."""
    model = _make_model()
    melt = MeltState()
    melt.atmosphere = Atmosphere.PN2_SWEEP
    melt.temperature_C = 1500.0
    melt.p_total_mbar = 10.0

    flux_na = EvaporationFlux(species_kg_hr={"Na": 1.0}, total_kg_hr=1.0)
    flux_fe = EvaporationFlux(species_kg_hr={"Fe": 1.0}, total_kg_hr=1.0)
    state_na = model.estimate_transport_state(flux_na, melt)
    state_fe = model.estimate_transport_state(flux_fe, melt)
    # ``pipe_conductance_kg_hr`` propagates the W7 M_avg path.
    assert state_fe["pipe_conductance_kg_hr"] > state_na["pipe_conductance_kg_hr"]
    ratio = state_fe["pipe_conductance_kg_hr"] / state_na["pipe_conductance_kg_hr"]
    expected = MOLAR_MASS["Fe"] / MOLAR_MASS["Na"]
    assert ratio == pytest.approx(expected, rel=1e-6)
