"""CO2 thermal dissociation: external JANAF anchor and coupled gas constraints."""

import math

import pytest

from simulator.chemistry.offgas_fo2 import (
    OffgasFO2Unavailable,
    co2_dissociation_partials_bar,
    imposed_fo2,
)


@pytest.mark.parametrize('fixed_pressure', [False, True])
def test_1800_K_matches_independent_janaf_formation_gibbs(fixed_pressure):
    # NIST-JANAF C-093/C-095, 1800 K: ΔfG(CO)=-269.242 and
    # ΔfG(CO2)=-396.353 kJ/mol; O2 reference formation Gibbs = 0.
    # https://janaf.nist.gov/tables/C-093.html and C-095.html.
    # Independently tabulated ΔrG = 127.111 kJ/mol implies Kd=2.04818e-4.
    boundary = {'total_pressure_bar': 6.1e-3} if fixed_pressure else {}
    result = co2_dissociation_partials_bar(1800.0, 6.1e-3, 0.0, **boundary)
    quotient = result['p_CO_bar'] * math.sqrt(result['p_O2_bar']) / result['p_CO2_bar']
    assert quotient == pytest.approx(2.04818e-4, rel=0.004)
    assert 7.1e-5 < result['p_O2_bar'] < 7.4e-5


@pytest.mark.parametrize('ledger', [0.0, 3.7e-7, 1e-3, 1.0])
def test_coupled_solution_conserves_carbon_and_oxygen_and_reuses_couple(ledger):
    result = co2_dissociation_partials_bar(1800.0, 6.1e-3, ledger)
    assert result['p_CO_bar'] + result['p_CO2_bar'] == pytest.approx(6.1e-3, rel=1e-14)
    assert result['p_CO_bar'] / 2.0 == pytest.approx(result['p_O2_bar'] - ledger, abs=2e-16)
    # Algebraic cross-check against the EXISTING couple owner, not independent
    # physics evidence; this prevents a second Kp(T) source from drifting.
    coupled = imposed_fo2({'CO': result['p_CO_bar'], 'CO2': result['p_CO2_bar']}, 1800.0)
    assert coupled.log10_fO2 == pytest.approx(math.log10(result['p_O2_bar']), abs=1e-10)


def test_ledger_dominates_and_suppresses_dissociation():
    empty = co2_dissociation_partials_bar(1800.0, 6.1e-3, 0.0)
    rich = co2_dissociation_partials_bar(1800.0, 6.1e-3, 1.0)
    assert rich['p_O2_bar'] == pytest.approx(1.0, rel=1e-6)
    assert rich['p_CO_bar'] < empty['p_CO_bar'] / 100.0


def test_small_dissociation_limit_from_janaf_anchor():
    # Independent JANAF Kd above: small-dissociation solution at c=1 bar is
    # 0.002189 bar. This checks the 2:1 CO/O2 production factor.
    result = co2_dissociation_partials_bar(1800.0, 1.0, 0.0)
    assert result['p_O2_bar'] == pytest.approx(0.002189, rel=0.01)


def test_no_co2_returns_ledger_oxygen_without_inventing_carbon():
    result = co2_dissociation_partials_bar(1800.0, 0.0, 3.7e-7)
    assert result == {'p_O2_bar': 3.7e-7, 'p_CO_bar': 0.0, 'p_CO2_bar': 0.0, 'notices': []}


def test_cold_gas_equilibrium_is_flagged_and_predicts_negligible_dissociation():
    result = co2_dissociation_partials_bar(298.15, 6.1e-3, 0.0)
    assert result['p_O2_bar'] < 1e-25
    assert result['notices'][0]['code'] == 'co2_dissociation_equilibrium_assumption_unverified'


def test_lumped_cold_gas_below_cea_band_predicts_and_flags():
    # Existing lumped gas owner: a 298.15 K melt gives 198.15 K gas.
    result = co2_dissociation_partials_bar(198.15, 6.1e-3, 0.0)
    assert 0.0 < result['p_O2_bar'] < 1e-40
    assert result['notices'][0]['code'] == 'co2_dissociation_thermo_extrapolated'
    assert result['notices'][0]['certified_band_K'] == [200.0, 20000.0]
    assert result['notices'][1]['code'] == 'co2_dissociation_equilibrium_assumption_unverified'


@pytest.mark.parametrize('co2,ledger', [(None, 0.0), (-1.0, 0.0), (math.nan, 0.0), (1.0, math.inf)])
def test_missing_or_invalid_pressure_refuses(co2, ledger):
    with pytest.raises(OffgasFO2Unavailable):
        co2_dissociation_partials_bar(1800.0, co2, ledger)
