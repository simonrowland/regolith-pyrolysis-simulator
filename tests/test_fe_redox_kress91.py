from __future__ import annotations

import pytest

from simulator.core import PyrolysisSimulator
from simulator.fe_redox import (
    kress91_fe3_over_sigma_fe,
    kress91_split,
    melt_mol_fractions_for_kress91,
)


KRESS91_MOL_FRACTIONS = {
    'SiO2': 0.44,
    'TiO2': 0.01,
    'Al2O3': 0.08,
    'MnO': 0.002,
    'MgO': 0.12,
    'CaO': 0.12,
    'Na2O': 0.04,
    'K2O': 0.003,
    'P2O5': 0.001,
    'FeOt': 0.184,
}


@pytest.mark.parametrize(
    ('fO2_log', 'T_K', 'pressure_bar', 'expected_fe3', 'expected_ratio'),
    [
        (
            -9.0,
            1843.15,
            1.0e-6,
            0.024789388822726001,
            0.012709761634361348,
        ),
        (
            -7.5,
            1873.15,
            0.01,
            0.043532751407115045,
            0.02275705282703544,
        ),
        (
            -5.0,
            1973.15,
            1.0,
            0.095369139542541975,
            0.052711632839009745,
        ),
    ],
)
def test_kress91_shared_function_matches_inline_formula_pins(
    fO2_log: float,
    T_K: float,
    pressure_bar: float,
    expected_fe3: float,
    expected_ratio: float,
) -> None:
    split = kress91_split(
        fO2_log=fO2_log,
        mol_fractions=KRESS91_MOL_FRACTIONS,
        T_K=T_K,
        pressure_bar=pressure_bar,
    )

    assert kress91_fe3_over_sigma_fe(
        fO2_log=fO2_log,
        mol_fractions=KRESS91_MOL_FRACTIONS,
        T_K=T_K,
        pressure_bar=pressure_bar,
    ) == pytest.approx(expected_fe3, rel=0, abs=1.0e-15)
    assert split['fe3'] == pytest.approx(expected_fe3, rel=0, abs=1.0e-15)
    assert split['ratio'] == pytest.approx(expected_ratio, rel=0, abs=1.0e-15)


def test_kress91_fe3_increases_with_oxygen_fugacity() -> None:
    values = [
        kress91_fe3_over_sigma_fe(
            fO2_log=fO2_log,
            mol_fractions=KRESS91_MOL_FRACTIONS,
            T_K=1873.15,
            pressure_bar=0.01,
        )
        for fO2_log in (-10.0, -8.0, -6.0)
    ]

    assert values == sorted(values)
    assert len(set(values)) == len(values)


@pytest.mark.parametrize('fO2_log', [-12.0, -9.0, -6.0, -3.0])
def test_kress91_fe3_stays_bounded(fO2_log: float) -> None:
    fe3 = kress91_fe3_over_sigma_fe(
        fO2_log=fO2_log,
        mol_fractions=KRESS91_MOL_FRACTIONS,
        T_K=1873.15,
        pressure_bar=0.01,
    )

    assert 0.0 <= fe3 <= 1.0


def test_core_inline_kress91_fallback_uses_shared_split() -> None:
    comp_wt = {
        'SiO2': 46.0,
        'TiO2': 2.5,
        'Al2O3': 13.5,
        'FeO': 12.0,
        'Fe2O3': 1.5,
        'MnO': 0.2,
        'MgO': 9.5,
        'CaO': 10.5,
        'Na2O': 2.0,
        'K2O': 0.4,
        'P2O5': 0.2,
    }
    fO2_log = -7.75
    T_K = 1873.15
    pressure_bar = 0.01
    sim = PyrolysisSimulator.__new__(PyrolysisSimulator)

    core_split = sim._fe_redox_split_inline_kress91(
        comp_wt,
        fO2_log=fO2_log,
        T_K=T_K,
        pressure_bar=pressure_bar,
    )
    shared_split = kress91_split(
        fO2_log=fO2_log,
        mol_fractions=melt_mol_fractions_for_kress91(comp_wt),
        T_K=T_K,
        pressure_bar=pressure_bar,
    )

    assert core_split['fe3_over_sigma_fe'] == shared_split['fe3']
    assert core_split['fe2o3_over_feo_molar'] == shared_split['ratio']


def test_core_inline_kress91_full_split_regression_pins() -> None:
    # Full-output regression pin: locks fe3/ratio AND the core-side wt% derivation
    # (weighted_total -> fe2o3_equiv_wt_pct / feo_equiv_wt_pct) that stays in
    # core.py after the shared-fn extraction. Captured run-once from the
    # post-extraction inline at 3703063.
    comp_wt = {
        'SiO2': 46.0, 'TiO2': 2.5, 'Al2O3': 13.5, 'FeO': 12.0, 'Fe2O3': 1.5,
        'MnO': 0.2, 'MgO': 9.5, 'CaO': 10.5, 'Na2O': 2.0, 'K2O': 0.4, 'P2O5': 0.2,
    }
    sim = PyrolysisSimulator.__new__(PyrolysisSimulator)
    split = sim._fe_redox_split_inline_kress91(
        comp_wt, fO2_log=-7.75, T_K=1873.15, pressure_bar=0.01,
    )
    assert split['status'] == 'ok'
    assert split['source'] == 'inline:Kress-Carmichael1991'
    assert split['fe3_over_sigma_fe'] == pytest.approx(
        0.03875582404010491, rel=0, abs=1.0e-15
    )
    assert split['fe2o3_over_feo_molar'] == pytest.approx(
        0.020159198364662897, rel=0, abs=1.0e-15
    )
    assert split['fe2o3_equiv_wt_pct'] == pytest.approx(
        0.585472552554371, rel=0, abs=1.0e-12
    )
    assert split['feo_equiv_wt_pct'] == pytest.approx(
        13.066348090398607, rel=0, abs=1.0e-12
    )


def test_kress91_no_iron_and_empty_basis() -> None:
    # Empty composition -> no mol basis -> {} (the no_iron sentinel path).
    assert melt_mol_fractions_for_kress91({}) == {}
    sim = PyrolysisSimulator.__new__(PyrolysisSimulator)
    empty = sim._fe_redox_split_inline_kress91(
        {}, fO2_log=-7.75, T_K=1873.15, pressure_bar=0.01,
    )
    assert empty['status'] == 'no_iron'
    assert empty['fe3_over_sigma_fe'] == 0.0
    assert empty['feo_equiv_wt_pct'] == 0.0
    assert empty['fe2o3_equiv_wt_pct'] == 0.0
    # Silicate-but-no-iron: HAS a mol basis (status 'ok') but zero FeO equiv,
    # so the R2.1b formula a_FeO = (feot/100)*(1-fe3) collapses to 0 -- the
    # no-iron guard is satisfied by feot=0, not by the fe3 value.
    no_fe = sim._fe_redox_split_inline_kress91(
        {'SiO2': 100.0}, fO2_log=-7.75, T_K=1873.15, pressure_bar=0.01,
    )
    assert no_fe['feo_equiv_wt_pct'] == 0.0
    assert no_fe['fe2o3_equiv_wt_pct'] == 0.0
