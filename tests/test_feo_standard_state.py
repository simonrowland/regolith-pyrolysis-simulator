"""Standard-state invariance and the admitted pure-Fe coexistence rows."""

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


def test_oneill_corrected_standard_state_residual():
    # O'Neill & Eggins 2002 Tables 4/7: 19 Fe-saturated CMAS melts,
    # 1400 C and imposed log10(fO2/bar)=-12.61. Owner acceptance from the
    # source assessment: about -0.36 dex, without fitting gamma.
    residuals = _oneill_residuals()
    assert len(residuals) == 19
    assert median(residuals) == pytest.approx(-0.36, abs=0.01)


@pytest.mark.parametrize('temperature', [1573.15, 1673.15, 1873.15])
def test_saturation_is_invariant_under_wustite_stoichiometric_conversion(temperature):
    comp = {'SiO2': 50., 'CaO': 20., 'MgO': 20., 'FeO': 10.}
    result = redox._native_fe_saturation_comparison(
        comp, fe3_over_sigma_fe=0., T_K=temperature,
        pressure_bar=0.01, fO2_log=-15.)
    multiplier = redox.holzheid_stoich_feo_gamma_band(temperature)['conversion_multiplier']
    a_s = result['native_fe_activity']
    a_w = a_s / multiplier
    # Equal chemical potential: mu_s^0=mu_w^0-RT*ln(m), a_s=m*a_w.
    # The measured wustite standard and the converted standard must predict
    # the same Fe/O2 coexistence potential. m=1 recovers the same standard.
    expected = redox.feo_iw_log10_fO2_bar(temperature, a_feo=a_w)
    assert result['iw_bound_log'] == pytest.approx(expected, abs=5e-14)


def test_declared_wustite_formation_buffer_keeps_its_reference():
    expected = redox.feo_iw_log10_fO2_bar(1673.15)
    assert expected == pytest.approx(-9.7407543565, abs=1e-10)
    assert redox.RedoxState.buffer_offset(redox.FE_FEO_BUFFER_STANDARD, 0.).log_fO2_at(1673.15) == expected
