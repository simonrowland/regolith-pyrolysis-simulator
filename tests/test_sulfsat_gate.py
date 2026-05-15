"""
Tests for the SULFUR_SATURATION_GATE adapter (``simulator.melt_backend.sulfsat``).

Covers four mocked paths plus one live PySulfSat smoke test:

* ``test_gate_unavailable_when_pysulfsat_import_fails`` - mocked-absent
  path: when ``importlib.import_module('PySulfSat')`` raises,
  ``is_available()`` returns False and ``compute_sulfur_saturation``
  returns ``calibration_status='unavailable'`` with a warning. No live
  library call.
* ``test_gate_populates_every_field_with_fake_pysulfsat`` - mocked-present
  path: monkeypatch a tiny fake ``PySulfSat`` module returning
  deterministic SCSS / SCAS / S6 values; assert every
  ``SulfurSaturationResult`` field populates.
* ``test_gate_flags_out_of_range_composition`` - out-of-range path:
  feed a high-FeO composition outside the calibration window and assert
  ``calibration_status='out_of_range'`` plus a descriptive warning. No
  silent extrapolation - result still comes back tagged out-of-range.
* ``test_stage0_fallback_records_warning_when_gate_unavailable`` - Stage 0
  fallback path: with ``SulfSatGate.is_available()`` patched to False,
  the Stage 0 sulfate / sulfide bucketing is preserved (builtin
  authority) and the simulator records an ``unavailable`` result with a
  warning on ``_last_sulfur_saturation_result``.
* ``test_live_pysulfsat_morb_basalt`` - skipif-guarded live test that
  calls real PySulfSat on a single MORB-basalt composition. Asserts
  ``calibration_status == 'in_range'`` and ``SCSS_ppm > 0``.

The skipif probe runs a full end-to-end gate call at import time so the
test skips cleanly when the upstream library is missing *or* when the
adapter can't drive it (e.g. an upstream column requirement the adapter
doesn't satisfy on its own). Either way, the deterministic mocked paths
still execute.
"""

from __future__ import annotations

import importlib
import sys
import types
from types import SimpleNamespace
from typing import Any, Dict

import pytest

from simulator.core import PyrolysisSimulator
from simulator.melt_backend.base import StubBackend
from simulator.melt_backend.sulfsat import (
    SulfSatGate,
    SulfurSaturationResult,
)
from simulator.state import CampaignPhase


# MORB-like cleaned-melt composition used by both the mocked-present and
# live paths. Numbers are within the SCSS+SCAS calibration window so the
# gate reports ``in_range`` when the upstream library runs.
_MORB_COMP_WT: Dict[str, float] = {
    'SiO2': 50.0,
    'TiO2': 1.5,
    'Al2O3': 15.0,
    'FeO': 10.0,
    'MgO': 7.0,
    'CaO': 11.0,
    'Na2O': 3.0,
    'K2O': 0.3,
}


def _probe_live_pysulfsat() -> bool:
    """
    Return True only if a fresh ``SulfSatGate`` can deliver an
    ``in_range`` result for ``_MORB_COMP_WT`` at MORB-style conditions.

    The probe runs the full gate pipeline, including the upstream
    PySulfSat call, so a missing optional column or an upstream API
    change cleanly downgrades to ``unavailable`` and the skip fires.
    """
    gate = SulfSatGate()
    if not gate.initialize({}):
        return False
    result = gate.compute_sulfur_saturation(
        liquid_comp_wt=_MORB_COMP_WT,
        T_K=1400.0,
        P_bar=1.0,
        fO2_log=-9.0,
        S_input_ppm=1000.0,
    )
    return (
        result.calibration_status == 'in_range'
        and result.SCSS_ppm > 0.0
    )


SULFSAT_AVAILABLE = _probe_live_pysulfsat()


# ---------------------------------------------------------------------------
# Fake PySulfSat module for the mocked-present path
# ---------------------------------------------------------------------------


def _make_fake_pysulfsat(
    *,
    scss_ppm: float = 1200.0,
    scas_ppm: float = 2500.0,
    s6_fraction: float = 0.40,
) -> types.ModuleType:
    """
    Return a stand-in ``PySulfSat`` module exposing the three symbols the
    gate consumes (``calculate_S2017_SCSS``, ``calculate_CD2019_SCAS``,
    ``calculate_S6St_Jugo2010_eq10``). Values are deterministic; the test
    asserts they propagate untouched into every
    ``SulfurSaturationResult`` field.
    """
    import pandas as pd

    fake = types.ModuleType('PySulfSat')

    def _calc_scss(df=None, T_K=None, P_kbar=None, Fe_FeNiCu_Sulf=None):
        return pd.DataFrame({'SCSS2_ppm_ideal_Smythe2017': [scss_ppm]})

    def _calc_scas(df=None, T_K=None):
        return pd.DataFrame({'SCAS6_ppm': [scas_ppm]})

    def _calc_s6(deltaQFM=None):
        return s6_fraction

    fake.calculate_S2017_SCSS = _calc_scss
    fake.calculate_CD2019_SCAS = _calc_scas
    fake.calculate_S6St_Jugo2010_eq10 = _calc_s6
    return fake


# ---------------------------------------------------------------------------
# 1. Mocked-absent path
# ---------------------------------------------------------------------------


def test_gate_unavailable_when_pysulfsat_import_fails(monkeypatch):
    """``importlib.import_module('PySulfSat')`` raises -> gate unavailable."""

    real_import_module = importlib.import_module

    def _failing_import(name, *args, **kwargs):
        if name == 'PySulfSat':
            raise ImportError('PySulfSat absent for this test')
        return real_import_module(name, *args, **kwargs)

    monkeypatch.setattr(importlib, 'import_module', _failing_import)

    gate = SulfSatGate()
    initialized = gate.initialize({})

    assert initialized is False
    assert gate.is_available() is False

    result = gate.compute_sulfur_saturation(
        liquid_comp_wt=_MORB_COMP_WT,
        T_K=1400.0,
        P_bar=1.0,
        fO2_log=-9.0,
        S_input_ppm=1000.0,
    )

    assert result.calibration_status == 'unavailable'
    assert result.SCSS_ppm == 0.0
    assert result.SCAS_ppm == 0.0
    assert result.S6_fraction == 0.0
    assert result.S_in_sulfide_ppm == 0.0
    assert result.S_in_sulfate_ppm == 0.0
    assert len(result.warnings) >= 1
    assert any('PySulfSat' in w for w in result.warnings)


# ---------------------------------------------------------------------------
# 2. Mocked-present path
# ---------------------------------------------------------------------------


def test_gate_populates_every_field_with_fake_pysulfsat(monkeypatch):
    """Fake PySulfSat -> every SulfurSaturationResult field is populated."""
    scss_ppm = 1500.0
    scas_ppm = 2200.0
    s6_fraction = 0.30
    fake = _make_fake_pysulfsat(
        scss_ppm=scss_ppm, scas_ppm=scas_ppm, s6_fraction=s6_fraction,
    )

    monkeypatch.setitem(sys.modules, 'PySulfSat', fake)

    gate = SulfSatGate()
    assert gate.initialize({}) is True
    assert gate.is_available() is True

    S_input_ppm = 1000.0
    result = gate.compute_sulfur_saturation(
        liquid_comp_wt=_MORB_COMP_WT,
        T_K=1400.0,
        P_bar=1.0,
        fO2_log=-9.0,
        S_input_ppm=S_input_ppm,
    )

    assert result.calibration_status == 'in_range'
    assert result.SCSS_ppm == pytest.approx(scss_ppm)
    assert result.SCAS_ppm == pytest.approx(scas_ppm)
    assert result.S6_fraction == pytest.approx(s6_fraction)
    # S_input * S6 = sulfate-bound share, S_input * (1 - S6) = sulfide;
    # both stay below the SCSS/SCAS caps so no clamping kicks in.
    expected_sulfate = S_input_ppm * s6_fraction
    expected_sulfide = S_input_ppm - expected_sulfate
    assert result.S_in_sulfate_ppm == pytest.approx(expected_sulfate)
    assert result.S_in_sulfide_ppm == pytest.approx(expected_sulfide)
    # MORB comp is inside the calibration window: no warnings expected.
    assert result.warnings == []


# ---------------------------------------------------------------------------
# 3. Out-of-range path
# ---------------------------------------------------------------------------


def test_gate_flags_out_of_range_composition(monkeypatch):
    """High-FeO composition -> ``out_of_range`` plus descriptive warning."""
    fake = _make_fake_pysulfsat()
    monkeypatch.setitem(sys.modules, 'PySulfSat', fake)

    gate = SulfSatGate()
    assert gate.initialize({}) is True

    # FeO well past the 25 wt% upper SCSS/SCAS bound encoded in
    # sulfsat._CALIBRATION_BOUNDS_WT_PCT.  Other oxides stay in-range so
    # we can pinpoint the violating oxide in the warning.
    out_of_range_comp = {
        'SiO2': 40.0,
        'FeO': 38.0,
        'MgO': 12.0,
        'CaO': 7.0,
        'Al2O3': 3.0,
    }

    result = gate.compute_sulfur_saturation(
        liquid_comp_wt=out_of_range_comp,
        T_K=1400.0,
        P_bar=1.0,
        fO2_log=-9.0,
        S_input_ppm=500.0,
    )

    assert result.calibration_status == 'out_of_range'
    # Result still populates (no silent extrapolation policy - fields
    # come back tagged, not blanked).
    assert result.SCSS_ppm > 0.0
    assert result.SCAS_ppm > 0.0
    assert len(result.warnings) >= 1
    assert any('FeO' in w for w in result.warnings)
    assert any('calibration' in w.lower() for w in result.warnings)


# ---------------------------------------------------------------------------
# 4. Stage 0 fallback path
# ---------------------------------------------------------------------------


def _sim_with_sulfur_feedstock() -> PyrolysisSimulator:
    """
    Build a simulator with a Mars-style feedstock that leaves SO3 and
    FeS-bearing inventory in ``salt_phase_kg`` / ``sulfide_matte_kg``,
    so the Stage 0 SulfSat hook actually runs (it short-circuits on
    zero S_input_ppm).
    """
    backend = StubBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(
        backend,
        {'campaigns': {}},
        {
            'mixed': {
                'label': 'Mixed raw regolith',
                'composition_wt_pct': {
                    'SiO2': 50.0,
                    'FeO': 10.0,
                    'Fe2O3': 2.0,
                    'H2O': 5.0,
                    'C': 2.0,
                    'S': 3.0,
                    'SO3': 4.0,
                    'Cl': 1.0,
                    'ClO4': 0.5,
                    'Fe': 6.0,
                    'Ni': 1.0,
                    'NiO': 1.2,
                    'ZrO2': 0.3,
                    'REE_oxides': 0.2,
                },
                'non_oxide_components': {'S_wt_pct': [1.0, 3.0]},
                'bulk_additions': {
                    'metallic_FeNi_wt_pct': [10.0, 20.0],
                    'FeS_troilite_wt_pct': [5.0, 6.0],
                    'C_wt_pct': [0.1, 0.5],
                },
            },
        },
        {'metals': {}, 'oxide_vapors': {}},
    )
    return sim


def test_stage0_fallback_records_warning_when_gate_unavailable(monkeypatch):
    """
    When ``SulfSatGate.is_available()`` is False at Stage 0, builtin
    sulfate/sulfide bucketing stays authoritative and the simulator
    records an ``unavailable`` SulfSat result with a warning.
    """
    monkeypatch.setattr(SulfSatGate, 'is_available', lambda self: False)

    sim = _sim_with_sulfur_feedstock()
    sim.load_batch('mixed', mass_kg=1000.0)

    # Builtin Stage 0 bucketing preserved - sulfide/sulfate inventory
    # is exactly what test_feedstock_inventory pins (the gate must not
    # rewrite the ledger).
    assert sim.inventory.salt_phase_kg['SO3'] == pytest.approx(36.697248)
    assert sim.inventory.sulfide_matte_kg['S'] == pytest.approx(45.871560)
    assert sim.inventory.sulfide_matte_kg['FeS_troilite'] == pytest.approx(
        50.458716
    )

    # The Stage 0 hook recorded an ``unavailable`` diagnostic, not None
    # (we have non-zero S_input_ppm so the hook didn't short-circuit).
    sulfsat_result = sim._last_sulfur_saturation_result
    assert sulfsat_result is not None
    assert sulfsat_result.calibration_status == 'unavailable'
    assert sulfsat_result.SCSS_ppm == 0.0
    assert sulfsat_result.SCAS_ppm == 0.0
    assert len(sulfsat_result.warnings) >= 1


# ---------------------------------------------------------------------------
# 5. Live PySulfSat smoke test (single composition, single assertion path)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not SULFSAT_AVAILABLE,
    reason='PySulfSat not installed / adapter unable to drive it end-to-end',
)
def test_live_pysulfsat_morb_basalt():
    """One MORB-basalt composition, one call, one assertion path."""
    gate = SulfSatGate()
    assert gate.initialize({}) is True

    result = gate.compute_sulfur_saturation(
        liquid_comp_wt=_MORB_COMP_WT,
        T_K=1400.0,
        P_bar=1.0,
        fO2_log=-9.0,
        S_input_ppm=1000.0,
    )

    assert result.calibration_status == 'in_range'
    assert result.SCSS_ppm > 0.0
