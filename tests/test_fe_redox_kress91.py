from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from engines.builtin.electrolysis_step import BuiltinElectrolysisStepProvider
from simulator.core import PyrolysisSimulator
from simulator.fe_redox import (
    Kress91InvalidControls,
    KRESS91_INV_T_COEFFICIENT_K,
    KRESS91_LIQUID_CALIBRATION_MIN_T_C,
    KRESS91_LN_FO2_COEFFICIENT,
    floor_vacuum_pressure_bar,
    feo_iw_log10_fO2_bar,
    kress91_fe3_over_sigma_fe,
    kress91_ferrous_feo_activity,
    kress91_ln_fO2_temperature_delta,
    kress91_referenced_log_fO2,
    kress91_split,
    kress91_temperature_band_case,
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


def test_initial_feot_split_conserves_iron_and_normalizes_without_oxygen_import():
    from simulator.fe_redox import split_feot_composition, RedoxState, FE_FEO_BUFFER_STANDARD
    from simulator.state import MOLAR_MASS

    feedstock = {
        'composition_wt_pct': {'SiO2': 80.0, 'FeO': 20.0},
        'intrinsic_redox': {
            'delta_iw': -1.0, 'formation_temperature_K': 1673.15,
            'source': 'test fixture', 'locator': {'page': 1},
            'estimated': True,
        },
    }
    diagnostic = split_feot_composition(feedstock['composition_wt_pct'], 1673.15,
        redox_state=RedoxState.buffer_offset(FE_FEO_BUFFER_STANDARD, -1.0))
    comp = diagnostic['composition_wt_pct']
    factor = diagnostic['renormalization_factor']
    assert sum(comp.values()) == pytest.approx(100.0, abs=1e-12)
    assert comp['Fe2O3'] > 0.0
    assert comp['SiO2'] == 80.0 * factor
    iron = comp['FeO'] / MOLAR_MASS['FeO'] + 2 * comp['Fe2O3'] / MOLAR_MASS['Fe2O3']
    assert iron == pytest.approx(factor * 20.0 / MOLAR_MASS['FeO'], rel=2e-15)
    from simulator.fe_redox import intrinsic_melt_fO2
    recovered = intrinsic_melt_fO2(comp, 1673.15)
    assert recovered == pytest.approx(feo_iw_log10_fO2_bar(1673.15) - 1.0, abs=2e-14)
    assert diagnostic['condition'] == 'condition_stated'
    assert feedstock['composition_wt_pct'] == {'SiO2': 80.0, 'FeO': 20.0}


def test_measured_initial_split_is_preserved_without_kress_reanchoring():
    from simulator.fe_redox import initialize_feedstock_iron

    feedstock = {'composition_wt_pct': {'SiO2': 80.0, 'FeO': 18.0, 'Fe2O3': 2.0},
                 'elemental_composition_wt_pct': {'Fe': 0.5}}
    initialized, diagnostic = initialize_feedstock_iron(feedstock)
    assert initialized == feedstock
    assert diagnostic['method'] == 'declared_split'


def test_missing_initial_anchor_is_predicted_and_explicitly_flagged():
    from simulator.fe_redox import initialize_feedstock_iron

    initialized, diagnostic = initialize_feedstock_iron(
        {'composition_wt_pct': {'SiO2': 80.0, 'FeO': 20.0}})
    assert initialized['composition_wt_pct']['Fe2O3'] > 0.0
    assert set(diagnostic['certification_gaps']) >= {'intrinsic_delta_iw', 'formation_temperature_K'}


def test_exhausted_ferric_inventory_has_an_activity_corrected_iw_lower_bound():
    from simulator.fe_redox import kress91_intrinsic_state, calphad_ferrous_feo_activity_diagnostic
    comp = {'SiO2': 80.0, 'FeO': 20.0}
    T_K = 1873.15
    activity = calphad_ferrous_feo_activity_diagnostic(
        comp_wt=comp, T_K=T_K, pressure_bar=0.01,
        fO2_log=feo_iw_log10_fO2_bar(T_K), fe3_over_sigma_fe=0.0)['a_FeO_authoritative']
    from simulator.fe_redox import holzheid_stoich_feo_gamma_band
    assert activity == pytest.approx(min(1.0,
        melt_mol_fractions_for_kress91(comp)['FeOt'] *
        holzheid_stoich_feo_gamma_band(T_K)['central']))
    result = kress91_intrinsic_state(
        feo_mol=1.0, fe2o3_mol=0.0,
        mol_fractions=melt_mol_fractions_for_kress91(comp),
        composition_wt_pct=comp, T_K=T_K, pressure_bar=0.01,
        exchange_fO2_log=-9.0)
    assert result['fO2_log'] == feo_iw_log10_fO2_bar(T_K, a_feo=activity)
    assert result['bound'] == 'lower'
    assert 'FE_FEO_SATURATION_BOUND' in result['flags']
    assert result['fO2_log'] != -9.0


def test_exhausted_ferric_inventory_without_activity_is_a_typed_refusal():
    from simulator.fe_redox import kress91_intrinsic_state
    with pytest.raises(Kress91InvalidControls, match='FeO activity'):
        kress91_intrinsic_state(feo_mol=1.0, fe2o3_mol=0.0,
            mol_fractions=KRESS91_MOL_FRACTIONS, T_K=1873.15,
            pressure_bar=0.01, exchange_fO2_log=-9.0)


def test_lab_feot_split_collapse_round_trip_and_stated_iw_condition():
    from simulator.fe_redox import (split_feot_composition, collapse_feot_composition,
                                    RedoxState, FE_FEO_BUFFER_STANDARD)
    comp = {'SiO2': 80.0, 'FeO': 20.0}
    T_K = 1873.15  # Lab condition: IW-1 at 1600 C.
    result = split_feot_composition(comp, T_K,
        redox_state=RedoxState.buffer_offset(FE_FEO_BUFFER_STANDARD, -1.0))
    assert collapse_feot_composition(result['composition_wt_pct']) == pytest.approx(comp, abs=2e-14)
    expected = kress91_fe3_over_sigma_fe(fO2_log=feo_iw_log10_fO2_bar(T_K)-1,
        mol_fractions=melt_mol_fractions_for_kress91(comp), T_K=T_K, pressure_bar=1e-9)
    assert result['fe3_over_sigma_fe'] == expected
    assert result['condition'] == 'condition_stated'
    assert result['temperature_band']['authoritative']
    defaulted = split_feot_composition(comp, 2373.15)
    assert defaulted['condition'] == 'condition_defaulted'
    assert defaulted['certification_gaps'] == ['intrinsic_delta_iw']
    assert defaulted['temperature_band']['extrapolation']


@pytest.mark.parametrize('T_K', [1473.15, 1673.15, 1873.15, 2373.15])
@pytest.mark.parametrize('pressure_bar', [1e-9, 0.01, 1.0])
@pytest.mark.parametrize('fO2_log', [-18.0, -9.0, -3.0])
@pytest.mark.parametrize('alkali', [0.0, 0.04])
def test_intrinsic_kress91_inverse_round_trip(T_K, pressure_bar, fO2_log, alkali):
    from simulator.fe_redox import kress91_intrinsic_state

    basis = dict(KRESS91_MOL_FRACTIONS, Na2O=alkali, SiO2=0.48 - alkali)
    split = kress91_split(
        fO2_log=fO2_log, mol_fractions=basis, T_K=T_K, pressure_bar=pressure_bar,
    )
    state = kress91_intrinsic_state(
        feo_mol=1.0, fe2o3_mol=split['ratio'], mol_fractions=basis,
        T_K=T_K, pressure_bar=pressure_bar, exchange_fO2_log=-9.0,
    )
    assert state['fO2_log'] == pytest.approx(fO2_log, abs=2e-14)
    assert not state['exchange_bounded']
    assert ('OUT_OF_CERTIFIED_BAND' in state['flags']) == (T_K > 1903.15)


@pytest.mark.parametrize('feo_mol, fe2o3_mol', [(1.0, 0.0), (0.0, 1.0)])
@pytest.mark.parametrize('exchange, expected', [(-5.0, -5.0), (0.0, -2.0)])
def test_intrinsic_kress91_ends_follow_exchange(feo_mol, fe2o3_mol, exchange, expected):
    from simulator.fe_redox import kress91_intrinsic_state

    if feo_mol > 0.0:
        # A reducing endpoint needs an activity-grounded stability bound;
        # the gas pressure alone cannot establish that bound (d-147).
        with pytest.raises(Kress91InvalidControls, match='FeO activity'):
            kress91_intrinsic_state(
                feo_mol=feo_mol, fe2o3_mol=fe2o3_mol,
                mol_fractions=KRESS91_MOL_FRACTIONS, T_K=1873.15,
                pressure_bar=0.01, exchange_fO2_log=exchange)
        return

    state = kress91_intrinsic_state(
        feo_mol=feo_mol, fe2o3_mol=fe2o3_mol,
        mol_fractions=KRESS91_MOL_FRACTIONS, T_K=1873.15,
        pressure_bar=0.01, exchange_fO2_log=exchange,
    )
    assert state['fO2_log'] == expected
    assert state['exchange_bounded']
    assert 'EXCHANGE_BOUNDED' in state['flags']


@pytest.mark.parametrize('kind', ['absolute', 'buffer_offset'])
@pytest.mark.parametrize('temperature_K', [1873.15, 2373.15])
def test_intrinsic_kress91_preserves_imposed_state(kind, temperature_K):
    from simulator.fe_redox import (
        FE_FEO_BUFFER_STANDARD, RedoxState, _non_absolute_redox_state_kwargs,
        kress91_intrinsic_state,
    )

    imposed = (RedoxState.absolute(-7.0) if kind == 'absolute' else
               RedoxState.buffer_offset(FE_FEO_BUFFER_STANDARD, 0.5))
    identity = imposed.cache_identity()
    kwargs = _non_absolute_redox_state_kwargs(imposed)
    state = kress91_intrinsic_state(
        feo_mol=0.0, fe2o3_mol=1.0, mol_fractions=KRESS91_MOL_FRACTIONS,
        T_K=temperature_K, pressure_bar=0.01, exchange_fO2_log=-5.0,
        redox_state=imposed,
    )
    assert state['fO2_log'] == imposed.log_fO2_at(temperature_K)
    assert not state['exchange_bounded']
    assert state['flags'] == []
    assert imposed.cache_identity() == identity
    assert _non_absolute_redox_state_kwargs(imposed) == kwargs


@pytest.mark.parametrize('ferric_mol', [0.0, 1.0, 50.0])
def test_finite_headspace_exchange_cannot_cross_equilibrium(ferric_mol):
    from simulator.fe_redox import _kress91_exchange_uptake_mol, kress91_intrinsic_state

    feo_mol = 100.0 - 2.0 * ferric_mol
    head_o2_mol = 10.0

    def gas_potential(remaining_o2):
        # Ideal gas at fixed T/V: p is proportional to actual O2 mol.
        return math.log10(max(remaining_o2 / 1000.0, 1e-9))

    uptake = _kress91_exchange_uptake_mol(
        feo_mol=feo_mol, fe2o3_mol=ferric_mol,
        mol_fractions=KRESS91_MOL_FRACTIONS, T_K=1673.15,
        pressure_bar=0.01, head_o2_mol=head_o2_mol,
        available_o2_mol=head_o2_mol, gas_log_fO2=gas_potential,
    )
    assert -ferric_mol / 2.0 < uptake < head_o2_mol
    liquid = kress91_intrinsic_state(
        feo_mol=feo_mol - 4.0 * uptake, fe2o3_mol=ferric_mol + 2.0 * uptake,
        mol_fractions=KRESS91_MOL_FRACTIONS, T_K=1673.15,
        pressure_bar=0.01, exchange_fO2_log=gas_potential(head_o2_mol - uptake),
    )
    assert liquid['fO2_log'] == pytest.approx(gas_potential(head_o2_mol - uptake), abs=1e-9)


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
        'temperature_band_case': '1200C_1630C_kress91_authoritative',
        'temperature_band_status': 'authoritative',
        'temperature_band_source': 'REF-001 Kress91 1200-1630 C calibration band',
        'authoritative': True,
        'extrapolation': False,
        'high_uncertainty': False,
    }


@pytest.mark.parametrize(
    ('reference_C', 'target_C', 'expected_error_dex'),
    [
        (1200.0, 1300.0, 0.04849),
        (1300.0, 1400.0, 0.01439),
        (1400.0, 1500.0, -0.01234),
        (1500.0, 1600.0, -0.03345),
        (1600.0, 1700.0, -0.05021),
        (1700.0, 1800.0, -0.06358),
        (1800.0, 1900.0, -0.07426),
        (1900.0, 2000.0, -0.08280),
    ],
)
def test_kress91_temperature_delta_includes_nonlinear_worked_errors(
    reference_C: float,
    target_C: float,
    expected_error_dex: float,
) -> None:
    reference_T_K = reference_C + 273.15
    target_T_K = target_C + 273.15

    corrected = (
        kress91_ln_fO2_temperature_delta(reference_T_K, target_T_K)
        / math.log(10.0)
    )
    released = -(
        KRESS91_INV_T_COEFFICIENT_K / KRESS91_LN_FO2_COEFFICIENT
    ) * ((1.0 / target_T_K) - (1.0 / reference_T_K)) / math.log(10.0)

    assert corrected - released == pytest.approx(expected_error_dex, abs=5.0e-6)


@pytest.mark.parametrize('target_C', [1200.0, 1630.0, 2000.0])
def test_kress91_referenced_log_fo2_preserves_forward_ratio(
    target_C: float,
) -> None:
    reference_T_K = 1400.0 + 273.15
    target_T_K = target_C + 273.15
    pressure_bar = 0.01
    reference_fO2_log = -7.75

    referenced_fO2_log = kress91_referenced_log_fO2(
        reference_fO2_log,
        reference_T_K=reference_T_K,
        target_T_K=target_T_K,
        reference_pressure_bar=pressure_bar,
        target_pressure_bar=pressure_bar,
    )
    reference_ratio = kress91_split(
        fO2_log=reference_fO2_log,
        mol_fractions=KRESS91_MOL_FRACTIONS,
        T_K=reference_T_K,
        pressure_bar=pressure_bar,
    )['ratio']
    target_ratio = kress91_split(
        fO2_log=referenced_fO2_log,
        mol_fractions=KRESS91_MOL_FRACTIONS,
        T_K=target_T_K,
        pressure_bar=pressure_bar,
    )['ratio']

    assert target_ratio == pytest.approx(reference_ratio, rel=0.0, abs=1.0e-14)


@pytest.mark.parametrize(
    ("reference_T_K", "target_T_K", "control"),
    [
        (
            KRESS91_LIQUID_CALIBRATION_MIN_T_C + 273.15 - 1.0e-6,
            KRESS91_LIQUID_CALIBRATION_MIN_T_C + 273.15,
            "reference_T_K",
        ),
        (
            KRESS91_LIQUID_CALIBRATION_MIN_T_C + 273.15,
            KRESS91_LIQUID_CALIBRATION_MIN_T_C + 273.15 - 1.0e-6,
            "target_T_K",
        ),
    ],
)
def test_kress91_referenced_log_fo2_refuses_below_calibration_temperature(
    reference_T_K: float,
    target_T_K: float,
    control: str,
) -> None:
    with pytest.raises(
        Kress91InvalidControls,
        match=rf"{control}.*below liquid calibration floor",
    ):
        kress91_referenced_log_fO2(
            -7.75,
            reference_T_K=reference_T_K,
            target_T_K=target_T_K,
        )


def test_kress91_referenced_log_fo2_refuses_below_calibration_target_without_reference(
) -> None:
    with pytest.raises(
        Kress91InvalidControls,
        match=r"target_T_K.*below liquid calibration floor",
    ):
        kress91_referenced_log_fO2(
            -7.75,
            reference_T_K=None,
            target_T_K=1000.0,
        )


def test_kress91_equal_temperature_still_references_pressure_change() -> None:
    temperature_K = 1400.0 + 273.15
    reference_fO2_log = -7.75

    referenced = kress91_referenced_log_fO2(
        reference_fO2_log,
        reference_T_K=temperature_K,
        target_T_K=temperature_K,
        reference_pressure_bar=1.0,
        target_pressure_bar=10_000.0,
    )

    assert referenced != pytest.approx(reference_fO2_log)
    reference_ratio = kress91_split(
        fO2_log=reference_fO2_log,
        mol_fractions=KRESS91_MOL_FRACTIONS,
        T_K=temperature_K,
        pressure_bar=1.0,
    )["ratio"]
    target_ratio = kress91_split(
        fO2_log=referenced,
        mol_fractions=KRESS91_MOL_FRACTIONS,
        T_K=temperature_K,
        pressure_bar=10_000.0,
    )["ratio"]
    assert target_ratio == pytest.approx(reference_ratio, rel=0.0, abs=1e-14)


@pytest.mark.parametrize(
    ('temperature_C', 'case', 'authoritative', 'extrapolation', 'high_uncertainty'),
    [
        (
            1199.0,
            'below_1200C_extrapolation',
            False,
            True,
            True,
        ),
        (
            1300.0,
            '1200C_1630C_kress91_authoritative',
            True,
            False,
            False,
        ),
        (
            1800.0,
            '1630C_2100C_extrapolation_experimentally_confirmed',
            False,
            True,
            False,
        ),
        (
            2300.0,
            '2100C_2500C_extrapolation_growing_uncertainty',
            False,
            True,
            True,
        ),
        (
            2600.0,
            'above_2500C_deauthorized_high_uncertainty',
            False,
            True,
            True,
        ),
    ],
)
def test_kress91_temperature_band_cases_gate_authority(
    temperature_C: float,
    case: str,
    authoritative: bool,
    extrapolation: bool,
    high_uncertainty: bool,
) -> None:
    band = kress91_temperature_band_case(temperature_C)

    assert band['case'] == case
    assert band['authoritative'] is authoritative
    assert band['extrapolation'] is extrapolation
    assert band['high_uncertainty'] is high_uncertainty


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
    assert split['source'] == 'simulator.fe_redox:kress91_split'
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


@pytest.mark.parametrize("amount", [float("nan"), float("inf"), -1.0])
def test_kress91_mol_basis_refuses_corrupt_composition(amount: float) -> None:
    with pytest.raises(Kress91InvalidControls, match="must be finite"):
        melt_mol_fractions_for_kress91({"SiO2": 50.0, "FeO": amount})


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
