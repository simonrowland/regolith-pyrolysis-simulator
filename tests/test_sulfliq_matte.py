"""
Tests for the SulfLiq matte a_FeS provider
(``simulator.melt_backend.sulfliq_matte``).

(a) import + calibration smoke (skipif engine missing)
(b) μ0 standard-state smoke (composition-independent; not an activity gate)
(c) P0 composition-dependent published/experimental activity anchor
    (Fonseca 2008 FeOS14 log fO2 / log fS2 triple through Kress)
(d) P1 composition-sensitivity (a_FeS moves under Ni dilution)
(e) a_FeS T-sweep sanity (finite, positive, tight pure-FeS band)
(f) typed refusal when SulfLiq is absent (a_FeS is NaN, not 0)
(g) invalid composition refuses without raise
(h) mutation-proof: constant-wrong a_FeS cannot satisfy (c)+(d)
"""

from __future__ import annotations

import importlib
import math
from typing import Any, Dict, Mapping, Sequence, Union

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

# SulfLiq component atomic masses (getCompWeights pure-endmember probe).
_AM_O = 15.9994
_AM_S = 32.066
_AM_FE = 55.847
_AM_NI = 58.693
_AM_CU = 63.546


def _matte_from_wt_pct(
    *,
    Fe: float,
    S: float,
    O: float = 0.0,
    Ni: float = 0.0,
    Cu: float = 0.0,
) -> Dict[str, float]:
    """Convert matte wt% to mole amounts keyed O/S/Fe/Ni/Cu."""
    return {
        'O': O / _AM_O,
        'S': S / _AM_S,
        'Fe': Fe / _AM_FE,
        'Ni': Ni / _AM_NI,
        'Cu': Cu / _AM_CU,
    }


# ---------------------------------------------------------------------------
# Published / experimental anchors
# ---------------------------------------------------------------------------
#
# Fonseca et al. (2008) GCA 72:2619–2635, Table 1 run FeOS14.
# Experimental conditions (table logf columns omit the leading minus; they
# are log10 of fugacity in bar under the reduced CO–CO2–SO2 gas mixes used
# in the study — standard petrologic reporting):
#   T = 1300 °C
#   log10 fO2 = −10.2
#   log10 fS2 = −2.83
# Measured matte (wt%): Fe 65.2 ± 0.4, S 30.5 ± 0.5, O(WDS) 4.5 ± 0.3
#
# The Kress associated-solution model (Kress 2000 DOI 10.1007/s004100000143;
# SulfLiq encoding) is evaluated at the measured bulk; model log f must
# recover the experimental gas fugacities within the residual Fonseca report
# for mid-O mattes ("striking" agreement of Kress vs their empirical O
# solubility fit; larger residual only at very low O). Live residual on this
# host for FeOS14: Δlog fO2 ≈ +0.04, Δlog fS2 ≈ −0.44.
#
# Tolerance (log10 units): ±0.50 on both — wider than live residual, tight
# enough that a pure-FeS or constant-activity wrong model fails by >1 dex
# on fS2 and/or fails the companion a_FeS composition band.
FONSECA_FEOS14_T_C = 1300.0
FONSECA_FEOS14_T_K = FONSECA_FEOS14_T_C + 273.15
FONSECA_FEOS14_LOG10_FO2 = -10.2
FONSECA_FEOS14_LOG10_FS2 = -2.83
FONSECA_FEOS14_WT = {'Fe': 65.2, 'S': 30.5, 'O': 4.5}
FONSECA_FEOS14_LOGF_TOL = 0.50
# Model a_FeS at this bulk (composition-dependent; pure FeS is ~0.97).
# Band binds the activity path; width covers R-constant / float noise only.
FONSECA_FEOS14_A_FES = 0.599
FONSECA_FEOS14_A_FES_TOL = 0.08


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
    assert ids['log_f_base'] == '10'
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
    (b) μ0(FeS) standard-state smoke at STR=1300 K — NOT an activity gate.

    Kept as a load/calib identifier that species index 8 is FeS and the
    SLSSData table matches Kress 2000 encoding. Composition-independent;
    a wrong a_FeS formula still passes this test alone. The activity gate
    is ``test_fonseca_feos14_composition_dependent_activity_anchor``.
    """
    import SulfLiq

    sl = SulfLiq.pySulfLiq()
    sl.setTK(1300.0)
    sl.setPa(1.0e5)
    sl.setComps([0.0, 0.5, 0.5, 0.0, 0.0])
    mu0 = float(sl.getMu0(8))
    assert mu0 == pytest.approx(FES_MU0_1300K_J_PER_MOL, abs=0.01)

    g0 = -93139.49
    ge1300 = -164460.1212
    assert (g0 + ge1300) == pytest.approx(FES_MU0_1300K_J_PER_MOL, abs=1e-9)


@_requires_sulfliq
def test_fonseca_feos14_composition_dependent_activity_anchor() -> None:
    """
    (c) P0 — composition-dependent experimental anchor binding activity.

    Fonseca et al. 2008 GCA Table 1 FeOS14: measured O–S–Fe matte bulk at
    known T and gas log10 fO2 / log10 fS2. The Kress model (via the
    provider path that also yields a_FeS) must recover both fugacities
    within tolerance AND return a_FeS well below the pure-FeS limit.

    This is the load-bearing gate the μ0-only test is not: a constant
    a_FeS≡0.97 (or ≡1.0) wrong model fails the a_FeS band; a broken
    speciation / excess path fails the log-f recovery.
    """
    provider = SulfLiqMatteProvider()
    assert provider.initialize()

    matte = _matte_from_wt_pct(**FONSECA_FEOS14_WT)
    result = provider.a_FeS(FONSECA_FEOS14_T_K, matte, P_bar=1.0)

    assert result.calibration_status == 'in_range', result.warnings
    assert result.log_fO2 is not None
    assert result.log_fS2 is not None

    assert result.log_fO2 == pytest.approx(
        FONSECA_FEOS14_LOG10_FO2, abs=FONSECA_FEOS14_LOGF_TOL
    ), (
        f'model log10 fO2={result.log_fO2} vs experimental '
        f'{FONSECA_FEOS14_LOG10_FO2} ± {FONSECA_FEOS14_LOGF_TOL} '
        f'(Fonseca 2008 FeOS14; Kress 2000 through SulfLiq)'
    )
    assert result.log_fS2 == pytest.approx(
        FONSECA_FEOS14_LOG10_FS2, abs=FONSECA_FEOS14_LOGF_TOL
    ), (
        f'model log10 fS2={result.log_fS2} vs experimental '
        f'{FONSECA_FEOS14_LOG10_FS2} ± {FONSECA_FEOS14_LOGF_TOL} '
        f'(Fonseca 2008 FeOS14; Kress 2000 through SulfLiq)'
    )

    # Activity path: diluted O-bearing matte, not pure-FeS Raoultian ~0.97.
    assert math.isfinite(result.a_FeS)
    assert result.a_FeS == pytest.approx(
        FONSECA_FEOS14_A_FES, abs=FONSECA_FEOS14_A_FES_TOL
    ), (
        f'a_FeS={result.a_FeS} outside composition-dependent band '
        f'{FONSECA_FEOS14_A_FES} ± {FONSECA_FEOS14_A_FES_TOL} '
        f'(must bind getSpecMu path; pure-FeS ~0.97 must not pass)'
    )
    # Explicit pure-FeS exclusion (W2 constant-0.97 fails here).
    assert result.a_FeS < 0.85

    # ln_a_FeS helper is finite only on success.
    assert math.isfinite(result.ln_a_FeS)
    assert result.ln_a_FeS == pytest.approx(math.log(result.a_FeS), abs=1e-12)


@_requires_sulfliq
def test_a_FeS_composition_sensitivity_ni_dilution() -> None:
    """
    (d) P1 — a_FeS must drop under Ni dilution of stoichiometric FeS matte.

    Kress (2007) Ni-bearing liquids: substituting Ni for Fe dilutes the
    FeS associated species; Raoultian a_FeS falls well below the pure-FeS
    association limit (~0.97). Direction and magnitude are anchored to
    the associated-solution model behaviour (not a free inequality).

    At 1473.15 K, 1 bar (live Kress/SulfLiq):
      pure FeS {S:0.5, Fe:0.5}           a_FeS ≈ 0.972
      Ni-dilute {S:0.5, Fe:0.25, Ni:0.25} a_FeS ≈ 0.403
    Ratio a_dilute/a_pure ≈ 0.41; absolute drop ≈ 0.57.
    """
    provider = SulfLiqMatteProvider()
    assert provider.initialize()
    T_K = 1473.15

    pure = provider.a_FeS(T_K, {'S': 0.5, 'Fe': 0.5}, P_bar=1.0)
    dilute = provider.a_FeS(
        T_K, {'S': 0.5, 'Fe': 0.25, 'Ni': 0.25}, P_bar=1.0
    )
    assert pure.calibration_status == 'in_range', pure.warnings
    assert dilute.calibration_status == 'in_range', dilute.warnings

    # Direction: Ni dilution lowers a_FeS (Kress 2007 association physics).
    assert dilute.a_FeS < pure.a_FeS

    # Magnitude: absolute bands + ratio (mutation-proof vs W1/W2 constants).
    assert 0.90 < pure.a_FeS < 1.02, f'pure FeS a_FeS={pure.a_FeS}'
    assert 0.30 < dilute.a_FeS < 0.50, f'Ni-dilute a_FeS={dilute.a_FeS}'
    ratio = dilute.a_FeS / pure.a_FeS
    assert 0.30 < ratio < 0.55, f'a_dilute/a_pure={ratio}'
    drop = pure.a_FeS - dilute.a_FeS
    assert drop > 0.40, f'absolute drop {drop} too small for 50% Ni-for-Fe'


@_requires_sulfliq
def test_a_FeS_temperature_sweep_sane() -> None:
    """(e) a_FeS finite, positive, continuous; tight pure-FeS band."""
    provider = SulfLiqMatteProvider()
    assert provider.initialize()
    matte = {'S': 0.5, 'Fe': 0.5}
    temps = [1200.0, 1300.0, 1400.0, 1500.0, 1600.0]
    activities = []
    for T in temps:
        result = provider.a_FeS(T, matte, P_bar=1.0)
        assert result.calibration_status in ('in_range', 'out_of_range')
        assert math.isfinite(result.a_FeS)
        assert result.a_FeS > 0.0
        # Pure FeS liquid: association leaves free Fe+S; live ~0.95–0.99.
        # Tightened from (0.5, 1.5) so constant-wrong models cannot hide.
        assert 0.90 < result.a_FeS < 1.02, f'T={T} a_FeS={result.a_FeS}'
        activities.append(result.a_FeS)

    # Continuity: adjacent T steps must not jump by more than 5% relative.
    for a0, a1 in zip(activities, activities[1:]):
        rel = abs(a1 - a0) / max(a0, a1)
        assert rel < 0.05, f'a_FeS jumped {a0} -> {a1} (rel {rel})'

    # Mild decrease with T (more dissociation) over the full span.
    assert activities[-1] < activities[0]


def test_typed_refusal_when_sulfliq_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """(f) Typed unavailable result when SulfLiq cannot be imported."""

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
    # P1 footgun fix: NaN, not 0.0 — ln(a) must not silently go to -inf.
    assert math.isnan(result.a_FeS)
    assert math.isnan(result.ln_a_FeS)
    assert result.warnings
    assert any('SulfLiq' in w for w in result.warnings)

    result2 = a_FeS(1473.15, {'S': 0.5, 'Fe': 0.5}, provider=provider)
    assert result2.calibration_status == 'unavailable'
    assert math.isnan(result2.a_FeS)


def test_invalid_composition_refuses_without_raise() -> None:
    """(g) Bad inputs return unavailable with NaN a_FeS, never raise."""
    provider = SulfLiqMatteProvider()
    provider.initialize()  # may or may not be available
    result = provider.a_FeS(1473.15, {'S': -1.0, 'Fe': 0.5})
    assert result.calibration_status == 'unavailable'
    assert math.isnan(result.a_FeS)
    assert math.isnan(result.ln_a_FeS)
    result = provider.a_FeS(-10.0, {'S': 0.5, 'Fe': 0.5})
    assert result.calibration_status == 'unavailable'
    assert math.isnan(result.a_FeS)


@_requires_sulfliq
def test_wrong_constant_a_FeS_fails_composition_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    (h) Mutation-proof: constant a_FeS (W1/W2) cannot satisfy P0/P1 gates.

    Patches only the activity number after a real evaluation so log-f side
    channels stay honest; asserts the composition-dependent a_FeS band and
    Ni-dilution magnitude both reject the wrong constant.
    """
    provider = SulfLiqMatteProvider()
    assert provider.initialize()

    real_method = SulfLiqMatteProvider.a_FeS

    def _const_097(
        self: SulfLiqMatteProvider,
        T_K: float,
        matte_composition: Union[Mapping[str, float], Sequence[float]],
        *,
        P_bar: float = 1.0,
    ) -> ActivityResult:
        result = real_method(self, T_K, matte_composition, P_bar=P_bar)
        # Force W2: pure-FeS-looking constant, ignore composition.
        result.a_FeS = 0.97
        return result

    monkeypatch.setattr(SulfLiqMatteProvider, 'a_FeS', _const_097)

    matte = _matte_from_wt_pct(**FONSECA_FEOS14_WT)
    feos14 = provider.a_FeS(FONSECA_FEOS14_T_K, matte, P_bar=1.0)
    # log-f still real (composition-dependent path not monkeypatched there),
    # but a_FeS is the wrong constant — must fail the activity band.
    with pytest.raises(AssertionError):
        assert feos14.a_FeS == pytest.approx(
            FONSECA_FEOS14_A_FES, abs=FONSECA_FEOS14_A_FES_TOL
        )
    assert not (feos14.a_FeS < 0.85)

    pure = provider.a_FeS(1473.15, {'S': 0.5, 'Fe': 0.5}, P_bar=1.0)
    dilute = provider.a_FeS(
        1473.15, {'S': 0.5, 'Fe': 0.25, 'Ni': 0.25}, P_bar=1.0
    )
    # W2: both 0.97 → no drop under Ni dilution.
    with pytest.raises(AssertionError):
        assert dilute.a_FeS < pure.a_FeS and (pure.a_FeS - dilute.a_FeS) > 0.40
