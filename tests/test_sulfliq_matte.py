"""
Tests for the SulfLiq matte a_FeS provider
(``simulator.melt_backend.sulfliq_matte``).

(a) import + calibration smoke (skipif engine missing)
(b) published Kress-model standard-state point
(c) a_FeS T-sweep sanity (finite, positive, continuous)
(d) typed refusal when SulfLiq is absent
"""

from __future__ import annotations

import importlib
import math
import sys
from typing import Any

import pytest

import simulator.melt_backend.sulfliq_matte as sulfliq_matte_module
from simulator.melt_backend.sulfliq_matte import (
    FES_MU0_1300K_J_PER_MOL,
    ActivityResult,
    SulfLiqMatteProvider,
    a_FeS,
)


def _sulfliq_importable() -> bool:
    try:
        mod = importlib.import_module('SulfLiq')
        _ = mod.pySulfLiq()
        return True
    except Exception:
        return False


_SULFLIQ_OK = _sulfliq_importable()
_requires_sulfliq = pytest.mark.skipif(
    not _SULFLIQ_OK,
    reason='SulfLiq extension not importable (build against this CPython ABI)',
)


@_requires_sulfliq
def test_import_and_calibration_smoke() -> None:
    """(a) SulfLiq importable; provider initialises; calib identifiers present."""
    provider = SulfLiqMatteProvider()
    assert provider.initialize() is True
    assert provider.is_available() is True
    ids = provider.calibration_identifiers()
    assert ids['calib_identifier'] == 'Version_1_0_0'
    assert ids['calib_name'] == 'Sulfide Liquid'
    assert 'Kress' in ids['model']
    assert provider.package_version() != 'unavailable'

    # ThermoEngine sulfide_liquid wrapper (enable-don't-rewrite).
    from thermoengine import sulfide_liquid as te_sl

    assert te_sl.cy_SulfLiq_sulfide_liquid_calib_identifier() == 'Version_1_0_0'
    assert te_sl.cy_SulfLiq_sulfide_liquid_calib_name() == 'Sulfide Liquid'
    names = [
        te_sl.cy_SulfLiq_sulfide_liquid_calib_endmember_name(i) for i in range(5)
    ]
    assert names == ['O', 'S', 'Fe', 'Ni', 'Cu']
    import numpy as np

    mol = np.array([0.0, 0.5, 0.5, 0.0, 0.0])
    G = te_sl.cy_SulfLiq_sulfide_liquid_calib_g(1473.15, 1.0, mol)
    assert math.isfinite(G)


@_requires_sulfliq
def test_published_kress_fes_mu0_at_1300k() -> None:
    """
    (b) Published Kress-model standard-state μ0(FeS) at 1300 K, 1 bar.

    Citation
    --------
    Kress (2000) associated-solution model for O–S–Fe sulfide liquids
    (Contrib. Mineral. Petrol. 139:316–325, DOI 10.1007/s004100000143),
    as encoded in ENKI-portal/sulfliq ``SulfLiq.cc`` SLSSData for
    ``tt liquid`` / FeS:

        g0(FeS, 1300 K)  = −93139.49 J/mol
        ge1300           = −164460.1212 J/mol
        μ0 = g0 + ge1300 = −257599.6112 J/mol

    At the model reference temperature STR = 1300 K and 1 bar, SulfLiq's
    ``getMu0(FeS_species_index)`` must return this value exactly (the
    SLSSPhase Gibbs path collapses to g0+ge1300 when T = STR).

    Tolerance: ±0.01 J/mol (machine float on a hard-coded sum).
    """
    import SulfLiq

    sl = SulfLiq.pySulfLiq()
    sl.setTK(1300.0)
    sl.setPa(1.0e5)
    # Composition required only so the phase is constructed; μ0 is
    # standard-state and composition-independent.
    sl.setComps([0.0, 0.5, 0.5, 0.0, 0.0])
    mu0 = float(sl.getMu0(8))
    assert mu0 == pytest.approx(FES_MU0_1300K_J_PER_MOL, abs=0.01)

    # Cross-check the algebraic identity used above.
    g0 = -93139.49
    ge1300 = -164460.1212
    assert (g0 + ge1300) == pytest.approx(FES_MU0_1300K_J_PER_MOL, abs=1e-9)


@_requires_sulfliq
def test_a_FeS_temperature_sweep_sane() -> None:
    """(c) a_FeS finite, positive, continuous across a small T sweep."""
    provider = SulfLiqMatteProvider()
    assert provider.initialize()
    # Stoichiometric FeS matte (no O/Ni/Cu).
    matte = {'S': 0.5, 'Fe': 0.5}
    temps = [1200.0, 1300.0, 1400.0, 1500.0, 1600.0]
    activities = []
    for T in temps:
        result = provider.a_FeS(T, matte, P_bar=1.0)
        assert result.calibration_status in ('in_range', 'out_of_range')
        assert math.isfinite(result.a_FeS)
        assert result.a_FeS > 0.0
        # Pure FeS liquid: species activity near the Raoultian limit
        # (association leaves free Fe+S; ~0.97 at 1473 K).
        assert 0.5 < result.a_FeS < 1.5
        activities.append(result.a_FeS)

    # Continuity: adjacent T steps must not jump by more than 10% relative.
    for a0, a1 in zip(activities, activities[1:]):
        rel = abs(a1 - a0) / max(a0, a1)
        assert rel < 0.10, f'a_FeS jumped {a0} -> {a1} (rel {rel})'


def test_typed_refusal_when_sulfliq_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """(d) Typed unavailable result when SulfLiq cannot be imported."""

    def _boom(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == 'SulfLiq':
            raise ModuleNotFoundError('SulfLiq forced-absent for test')
        return importlib.import_module(name)

    monkeypatch.setattr(sulfliq_matte_module.importlib, 'import_module', _boom)

    provider = SulfLiqMatteProvider()
    assert provider.initialize() is False
    assert provider.is_available() is False
    result = provider.a_FeS(1473.15, {'S': 0.5, 'Fe': 0.5})
    assert isinstance(result, ActivityResult)
    assert result.calibration_status == 'unavailable'
    assert result.a_FeS == 0.0
    assert result.warnings
    assert any('SulfLiq' in w for w in result.warnings)

    # Module-level helper also refuses cleanly.
    result2 = a_FeS(1473.15, {'S': 0.5, 'Fe': 0.5}, provider=provider)
    assert result2.calibration_status == 'unavailable'


def test_invalid_composition_refuses_without_raise() -> None:
    """Bad inputs return unavailable, never raise."""
    provider = SulfLiqMatteProvider()
    provider.initialize()  # may or may not be available
    result = provider.a_FeS(1473.15, {'S': -1.0, 'Fe': 0.5})
    assert result.calibration_status == 'unavailable'
    assert result.a_FeS == 0.0
    result = provider.a_FeS(-10.0, {'S': 0.5, 'Fe': 0.5})
    assert result.calibration_status == 'unavailable'
