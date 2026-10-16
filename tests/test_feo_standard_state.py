"""Standard-state invariance and the admitted pure-Fe coexistence rows."""

import math
from pathlib import Path
from statistics import median

import pytest
import yaml

from simulator import fe_redox as redox


def _oneill_residuals():
    path = (Path(__file__).resolve().parents[1] / 'data/literature/extracts'
            / 'oneill-2002-feo-activity-coefficients-cmas.yaml')
    rows = yaml.safe_load(path.read_text())['species']['FeO']['observations'][0]['values']['series']
    residuals = []
    for row in rows:
        comp = {s: float(w) for s, w in row['oxides_wt_pct'].items()}
        split = redox.kress91_split(
            fO2_log=-12.61, mol_fractions=redox.melt_mol_fractions_for_kress91(comp),
            T_K=1673.15, pressure_bar=1.)
        comparison = redox._native_fe_saturation_comparison(
            comp, fe3_over_sigma_fe=split['fe3'], fO2_log=-12.61,
            T_K=1673.15, pressure_bar=1.)
        residuals.append(-12.61 - comparison['iw_bound_log'])
    return residuals


def test_oneill_pre_correction_residual_pin():
    # O'Neill & Eggins 2002 Tables 4/7: 19 Fe-saturated CMAS melts,
    # 1400 C and imposed log10(fO2/bar)=-12.61. Historical assessment pin.
    residuals = _oneill_residuals()
    assert len(residuals) == 19
    assert median(residuals) == pytest.approx(-0.9384167892594242, abs=1e-12)
