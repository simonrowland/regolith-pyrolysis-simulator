from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from engines.builtin.electrolysis_step import BuiltinElectrolysisStepProvider
from simulator.core import PyrolysisSimulator
from simulator.fe_redox import (
    Kress91InvalidControls,
    floor_vacuum_pressure_bar,
    feo_iw_log10_fO2_bar,
    kress91_fe3_over_sigma_fe,
    kress91_ferrous_feo_activity,
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


@pytest.mark.parametrize('pressure_bar', [0.0, -1.0])
def test_floor_vacuum_pressure_bar_floors_finite_vacuum_pressure(
    pressure_bar: float,
) -> None:
    assert floor_vacuum_pressure_bar(pressure_bar) == 1.0e-9


@pytest.mark.parametrize('pressure_bar', [1.0e-12, 1.0e-9, 0.01])
def test_floor_vacuum_pressure_bar_preserves_finite_positive_pressure(
    pressure_bar: float,
) -> None:
    assert floor_vacuum_pressure_bar(pressure_bar) == pressure_bar


@pytest.mark.parametrize('pressure_bar', [float('nan'), float('inf'), float('-inf')])
def test_floor_vacuum_pressure_bar_preserves_nonfinite_for_validator(
    pressure_bar: float,
) -> None:
    floored = floor_vacuum_pressure_bar(pressure_bar)
    if math.isnan(pressure_bar):
        assert math.isnan(floored)
    else:
        assert floored == pressure_bar


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


def test_kress91_valid_finite_controls_stay_exactly_golden_neutral() -> None:
    split = kress91_split(
        fO2_log=-7.5,
        mol_fractions=KRESS91_MOL_FRACTIONS,
        T_K=1873.15,
        pressure_bar=0.01,
    )

    assert split == {
        'fe3': 0.043532751407115045,
        'ratio': 0.02275705282703544,
        'x_fe2o3': 0.004005013129454584,
        'x_feo': 0.17598997374109082,
    }


@pytest.mark.parametrize('fO2_log', [float('nan'), float('inf'), float('-inf')])
def test_kress91_non_finite_fo2_fails_loudly(fO2_log: float) -> None:
    with pytest.raises(Kress91InvalidControls, match='fO2_log'):
        kress91_split(
            fO2_log=fO2_log,
            mol_fractions=KRESS91_MOL_FRACTIONS,
            T_K=1873.15,
            pressure_bar=0.01,
        )


@pytest.mark.parametrize('T_K', [0.0, -1.0, float('nan'), float('inf')])
def test_kress91_invalid_temperature_fails_loudly(T_K: float) -> None:
    with pytest.raises(Kress91InvalidControls, match='T_K'):
        kress91_fe3_over_sigma_fe(
            fO2_log=-7.5,
            mol_fractions=KRESS91_MOL_FRACTIONS,
            T_K=T_K,
            pressure_bar=0.01,
        )


@pytest.mark.parametrize(
    'pressure_bar',
    [0.0, -1.0, float('nan'), float('inf'), float('-inf')],
)
def test_kress91_invalid_pressure_fails_loudly(pressure_bar: float) -> None:
    with pytest.raises(Kress91InvalidControls, match='pressure_bar'):
        kress91_split(
            fO2_log=-7.5,
            mol_fractions=KRESS91_MOL_FRACTIONS,
            T_K=1873.15,
            pressure_bar=pressure_bar,
        )


# kress91_ferrous_feo_activity serves the evaporation / vapor-pressure path,
# whose valid-input domain DIFFERS from kress91_split's: it is called at furnace
# vacuum with pressure_bar == 0.0, so it FLOORS non-positive pressure to 1e-9
# (Kress91 pressure terms are a negligible high-pressure correction here) rather
# than refusing it. Non-FINITE pressure (and non-finite fO2/T_K) still raises via
# the shared chokepoint. These tests lock that domain split so a future "make the
# siblings identical" fold cannot silently break vacuum evaporation goldens.
_FERROUS_ACTIVITY_COMP_WT = {
    'SiO2': 46.0, 'TiO2': 2.5, 'Al2O3': 13.5, 'FeO': 12.0, 'Fe2O3': 1.5,
    'MnO': 0.2, 'MgO': 9.5, 'CaO': 10.5, 'Na2O': 2.0, 'K2O': 0.4, 'P2O5': 0.2,
}


@pytest.mark.parametrize('pressure_bar', [float('nan'), float('inf'), float('-inf')])
def test_kress91_ferrous_feo_activity_nonfinite_pressure_fails_loudly(
    pressure_bar: float,
) -> None:
    with pytest.raises(Kress91InvalidControls, match='pressure_bar'):
        kress91_ferrous_feo_activity(
            comp_wt=_FERROUS_ACTIVITY_COMP_WT,
            fO2_log=-7.5,
            T_K=1873.15,
            pressure_bar=pressure_bar,
        )


@pytest.mark.parametrize('pressure_bar', [0.0, -1.0])
def test_kress91_ferrous_feo_activity_vacuum_pressure_is_floored_not_refused(
    pressure_bar: float,
) -> None:
    # The vacuum-evaporation contract: non-positive pressure floors to
    # 1e-9 and returns the SAME finite activity as an explicit 1e-9 bar request,
    # never raising. (Production reaches this via vapor_pressure.py with an
    # unfloored request.pressure_bar == 0.0.)
    floored = kress91_ferrous_feo_activity(
        comp_wt=_FERROUS_ACTIVITY_COMP_WT,
        fO2_log=-7.75,
        T_K=1873.15,
        pressure_bar=1.0e-9,
    )
    activity = kress91_ferrous_feo_activity(
        comp_wt=_FERROUS_ACTIVITY_COMP_WT,
        fO2_log=-7.75,
        T_K=1873.15,
        pressure_bar=pressure_bar,
    )
    assert activity > 0.0
    assert activity == floored


@pytest.mark.parametrize(
    ('control', 'fO2_log', 'T_K'),
    [
        ('fO2_log', float('nan'), 1873.15),
        ('fO2_log', float('inf'), 1873.15),
        ('T_K', -7.5, 0.0),
        ('T_K', -7.5, float('nan')),
        ('T_K', -7.5, float('inf')),
    ],
)
def test_kress91_ferrous_feo_activity_invalid_fo2_or_temperature_fails_loudly(
    control: str, fO2_log: float, T_K: float,
) -> None:
    with pytest.raises(Kress91InvalidControls, match=control):
        kress91_ferrous_feo_activity(
            comp_wt=_FERROUS_ACTIVITY_COMP_WT,
            fO2_log=fO2_log,
            T_K=T_K,
            pressure_bar=0.01,
        )


def test_kress91_ferrous_feo_activity_above_iw_plus_one_uses_kress91_limb() -> None:
    # Kress & Carmichael 1991 and Holzheid et al. 1997 Eq. (4) share an oxide
    # mole-fraction X_FeO convention; the oxidized/ferric limb remains Kress91.
    fO2_log = feo_iw_log10_fO2_bar(1873.15) + 1.25
    activity = kress91_ferrous_feo_activity(
        comp_wt=_FERROUS_ACTIVITY_COMP_WT,
        fO2_log=fO2_log,
        T_K=1873.15,
        pressure_bar=0.01,
    )
    split = kress91_split(
        fO2_log=fO2_log,
        mol_fractions=melt_mol_fractions_for_kress91(_FERROUS_ACTIVITY_COMP_WT),
        T_K=1873.15,
        pressure_bar=0.01,
    )
    assert activity == pytest.approx(split['x_feo'], rel=0, abs=1.0e-15)
    assert activity == pytest.approx(0.11129231084988625, rel=0, abs=1.0e-15)


@pytest.mark.parametrize('pressure_bar', [float('nan'), float('inf'), float('-inf')])
def test_electrolysis_fe_redox_diagnostic_nonfinite_pressure_reaches_validator(
    pressure_bar: float,
) -> None:
    with pytest.raises(Kress91InvalidControls, match='pressure_bar'):
        BuiltinElectrolysisStepProvider._compute_fe_redox_split_diagnostic(
            composition_kg=_FERROUS_ACTIVITY_COMP_WT,
            total_kg=sum(_FERROUS_ACTIVITY_COMP_WT.values()),
            T_K=1873.15,
            pressure_bar=pressure_bar,
            melt_fO2_log=-7.5,
        )


@pytest.mark.parametrize('pressure_mbar', [float('nan'), float('inf'), float('-inf')])
def test_core_melt_redox_capacity_nonfinite_pressure_reaches_validator(
    pressure_mbar: float,
) -> None:
    sim = PyrolysisSimulator.__new__(PyrolysisSimulator)
    sim.melt = SimpleNamespace(p_total_mbar=pressure_mbar)
    sim._cleaned_melt_fe_atom_mol = lambda: 1.0
    sim._melt_oxide_wt_pct = lambda: _FERROUS_ACTIVITY_COMP_WT

    with pytest.raises(Kress91InvalidControls, match='pressure_bar'):
        sim._melt_redox_capacity_mol_per_ln_fO2(
            fO2_log=-7.5,
            T_K=1873.15,
        )


@pytest.mark.parametrize('pressure_mbar', [float('nan'), float('inf'), float('-inf')])
def test_core_fe_redox_diagnostic_nonfinite_pressure_reaches_validator(
    pressure_mbar: float,
) -> None:
    sim = PyrolysisSimulator.__new__(PyrolysisSimulator)
    sim.melt = SimpleNamespace(
        temperature_C=1600.0,
        oxygen_reservoir=SimpleNamespace(melt_intrinsic_fO2_log=-7.75),
    )
    sim.overhead = SimpleNamespace(pressure_mbar=pressure_mbar)
    sim._melt_oxide_wt_pct = lambda: _FERROUS_ACTIVITY_COMP_WT
    sim._fe_redox_split_from_pysulfsat = lambda *args, **kwargs: None

    with pytest.raises(Kress91InvalidControls, match='pressure_bar'):
        sim._compute_fe_redox_split_diagnostic(temperature_K=1873.15)


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
    # Silicate-but-no-iron: HAS a mol basis (status 'ok') but zero FeO equiv, so
    # the R2.1b formula's mole-fraction X_FeO term collapses to 0 -- the no-iron
    # guard is satisfied by feot=0, not by the fe3 value.
    no_fe = sim._fe_redox_split_inline_kress91(
        {'SiO2': 100.0}, fO2_log=-7.75, T_K=1873.15, pressure_bar=0.01,
    )
    assert no_fe['feo_equiv_wt_pct'] == 0.0
    assert no_fe['fe2o3_equiv_wt_pct'] == 0.0
