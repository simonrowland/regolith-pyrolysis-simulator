from __future__ import annotations

from collections import Counter
from copy import deepcopy
from decimal import Decimal

import pytest
import yaml

from simulator.battery.enums import Quantity, ValueKind
from simulator.battery.migrate import REPO_ROOT
from tests.battery.factories import migrate_one_extract
from tests.helpers.corpus_paths import regolith_corpus_root
from tests.helpers.pdf_text import pdftotext_page_text
from tools.validate_literature_extracts import check_all_fidelity_samples_match


SID = 'yu-2003-k-na-evaporation-chondrule-melts'
EXTRACT = REPO_ROOT / 'data/literature/extracts' / f'{SID}.yaml'


def _assert_table1_compositions(document: dict) -> None:
    expected = {
        'yu_2003_composition_1_initial_charge': (
            {'SiO2': '52.36', 'TiO2': '0.20', 'Al2O3': '3.56', 'Cr2O3': '0.32', 'FeO': '13.91', 'MnO': '0.36', 'MgO': '21.25', 'CaO': '0.81', 'Na2O': '2.75', 'K2O': '3.21'},
            '98.72',
        ),
        'yu_2003_composition_2_initial_charge': (
            {'SiO2': '50.05', 'TiO2': '0.21', 'Al2O3': '1.03', 'Cr2O3': '0.44', 'FeO': '20.37', 'MnO': '0.42', 'MgO': '23.90', 'CaO': '1.02', 'Na2O': '1.31', 'K2O': '1.72'},
            '100.46',
        ),
        'yu_2003_chondrule_3_bulk_context': (
            {'SiO2': '55.1', 'TiO2': '0.19', 'Al2O3': '4.8', 'Cr2O3': '0.44', 'FeO': '14.3', 'MnO': '0.40', 'MgO': '18.7', 'CaO': '2.5', 'Na2O': '2.4', 'K2O': '0.29'},
            '99.12',
        ),
        'yu_2003_chondrule_4_bulk_context': (
            {'SiO2': '48.9', 'TiO2': '0.12', 'Al2O3': '3.23', 'Cr2O3': '0.54', 'FeO': '12.3', 'MnO': '0.40', 'MgO': '30.8', 'CaO': '2.32', 'Na2O': '2.17', 'K2O': '0.23'},
            '101.01',
        ),
    }
    records = {row['observation_id']: row for row in document['species']['SiO2']['context']}
    for observation_id, (composition, total) in expected.items():
        values = records[observation_id]['values']
        assert values['composition_wt_pct'] == composition, observation_id
        assert values['total_wt_pct_as_printed'] == total, observation_id
    assert records['yu_2003_composition_1_initial_charge']['values']['standard_deviation_wt_pct'] == {
        'SiO2': '0.93', 'TiO2': '0.16', 'Al2O3': '0.03', 'Cr2O3': '0.08', 'FeO': '0.67',
        'MnO': '0.13', 'MgO': '1.25', 'CaO': '0.09', 'Na2O': '0.50', 'K2O': '0.16',
    }


def _assert_table2_ratios(document: dict, expected: dict, expected_pressure_atm: dict, expected_atmosphere: dict) -> None:
    records = {}
    for species, observation_id in (
        ('K', 'yu_2003_table2_K_remaining_ratios'),
        ('Na', 'yu_2003_table2_Na_remaining_ratios'),
    ):
        context = next(row for row in document['species'][species]['context'] if row['observation_id'] == observation_id)
        records[species] = {row['sample_id']: row for row in context['values']['points']}
    assert set(records['K']) == set(records['Na']) == set(expected)
    for sample, (k_ratio, na_ratio, temperature, time) in expected.items():
        k_point, na_point = records['K'][sample], records['Na'][sample]
        assert k_point['K_over_K0_as_published'] == k_ratio, sample
        assert na_point['Na_over_Na0_as_published'] == na_ratio, sample
        assert k_point['T_C'] == na_point['T_C'] == temperature, sample
        assert k_point['point_conditions'] == na_point['point_conditions'], sample
        conditions = k_point['point_conditions']
        pressure = expected_pressure_atm[sample]
        if pressure is None:
            assert 'total_pressure_atm' not in conditions, sample
            assert 'atmosphere' not in conditions, sample
        else:
            assert conditions['total_pressure_atm']['state']['value'] == pressure, sample
            assert conditions['atmosphere']['state']['value'] == expected_atmosphere[sample], sample
        if time.endswith('°C/hr'):
            assert k_point['time_cell_as_published'] == na_point['time_cell_as_published'] == time, sample
        else:
            assert k_point['time_min_as_published'] == na_point['time_min_as_published'] == time, sample


def test_yu_tables_migrate_and_keep_isotope_rows_bound_to_conditions() -> None:
    pdf = regolith_corpus_root() / 'raw' / SID / f'{SID}.pdf'
    if not pdf.is_file():
        pytest.skip(f'corpus PDF unavailable: {pdf}')

    table1 = pdftotext_page_text(pdf, 3)
    table2 = pdftotext_page_text(pdf, 4)
    assert 'Table 1. Starting Compositions and Chondrules.' in table1
    assert '52.36' in next(line for line in table1.splitlines() if line.lstrip().startswith('SiO2'))
    assert '50.05' in next(line for line in table1.splitlines() if line.lstrip().startswith('SiO2'))
    row_v46 = next(line for line in table2.splitlines() if line.lstrip().startswith('V-46'))
    assert all(token in row_v46 for token in ('0.047', '0.006', '87.6', '0.9'))
    row_v43 = next(line for line in table2.splitlines() if line.lstrip().startswith('V-43'))
    assert all(token in row_v43 for token in ('0.500', '0.290', '20.4', '0.9'))
    row_v85 = next(line for line in table2.splitlines() if line.lstrip().startswith('V-85'))
    assert all(token in row_v85 for token in ('1.8', '10', '0.396', '0.236', '17.4', '0.7'))
    row_v103 = next(line for line in table2.splitlines() if line.lstrip().startswith('V-103'))
    assert all(token in row_v103 for token in ('1.3', '10', '0.185', '0.056', '5.0', '0.7'))

    document = yaml.safe_load(EXTRACT.read_text(encoding='utf-8'))
    _assert_table1_compositions(document)
    migrator = migrate_one_extract(REPO_ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    result = migrator.result
    assert not result.validation.hard_issues

    observations = [
        item for item in result.observations.values()
        if item.source_id == SID and item.identity.quantity.value is Quantity.ISOTOPE_DELTA
    ]
    assert len(observations) == 32
    assert all(item.identity.species.formula == 'K' for item in observations)
    assert all(
        item.value.kind is ValueKind.POINT
        for item in observations
    ), [(item.observation_id, item.value.kind.value, item.value.unavailable_reason) for item in observations if item.value.kind is not ValueKind.POINT]
    assert all(item.identity.temperature_K.is_value for item in observations)
    assert all(item.locator and item.locator.page in {4} for item in observations)

    sample_ids = [
        point['sample_id']
        for point in document['species']['K']['observations'][0]['values']['points']
    ]
    by_sample = dict(zip(sample_ids, observations, strict=True))
    assert len(by_sample) == 32
    expected_isotopes = {
        'V-54': ('0', '0.9'), 'V-43': ('20.4', '0.9'), 'V-44': ('44.0', '0.8'),
        'V-45': ('68.4', '0.9'), 'V-46': ('87.6', '0.9'),
        'NV-61': ('1.6', '0.7'), 'NV-62': ('2.3', '0.4'), 'NV-63': ('5', '0.5'),
        'V20': ('18.4', '1.4'), 'V21': ('27.3', '1.6'), 'V22': ('57.4', '1.0'),
        'V-59': ('7.5', '1.3'), 'V-60': ('22.6', '1.8'),
        'V-85': ('17.4', '0.7'), 'V-86': ('62.6', '0.8'), 'V-87': ('34.0', '2.1'),
        'V-88': ('15.6', '0.7'), 'V-89': ('48.8', '1.0'), 'V-90': ('38.7', '1.2'),
        'V-91': ('6.6', '0.6'), 'V-92': ('13.6', '0.7'), 'V-93': ('13.3', '0.9'),
        'V-94': ('21.4', '0.6'), 'V-95': ('57.3', '1.1'), 'V-96': ('14.8', '2.7'),
        'V-97': ('20.1', '0.6'), 'V-98': ('59.8', '1.6'), 'V-99': ('7.4', '3.0'),
        'V-100': ('21.7', '0.9'), 'V-101': ('15.4', '1.8'), 'V-102': ('4.9', '2.2'),
        'V-103': ('5.0', '0.7'),
    }
    assert set(expected_isotopes) == set(by_sample)
    for sample, (delta, sigma) in expected_isotopes.items():
        observation = by_sample[sample]
        assert observation.value.point == Decimal(delta), sample
        assert observation.uncertainty.verbatim['sigma'] == sigma, sample
        assert Decimal(observation.uncertainty.verbatim['sigma']) > 0, sample
    assert by_sample['V-46'].identity.temperature_K.value == Decimal('1723.15')
    assert by_sample['V-85'].identity.temperature_K.value == Decimal('1758.15')

    expected_pressure_atm = {
        'V-54': None, 'V-5': '1.0e-5', 'V-6': '1.0e-5', 'V-7': '1.0e-5',
        'V-8': '1.0e-5', 'V-9': '1.0e-5', 'V-10': '1.0e-5', 'V-11': '1.0e-5',
        'V-12': '1.0e-5', 'V-13': '1.0e-5', 'V-43': '1.0e-5', 'V-44': '1.0e-5',
        'V-45': '1.0e-5', 'V-46': '1.0e-5', 'NV-61': '1.0', 'NV-62': '1.0',
        'NV-63': '1.0', 'V20': '9e-5', 'V21': '9e-5', 'V22': '9e-5',
        'V-59': '1.00e-5', 'V-60': '1.00e-5',
        'V-85': '1.8e-5', 'V-86': '1.8e-5', 'V-87': '1.8e-5',
        'V-88': '1.8e-4', 'V-89': '1.8e-4', 'V-90': '1.8e-4',
        'V-91': '1.4e-3', 'V-92': '1.4e-3', 'V-93': '1.4e-3',
        'V-94': '7.2e-5', 'V-95': '7.2e-5', 'V-96': '7.2e-5',
        'V-97': '1.8e-4', 'V-98': '1.8e-4', 'V-99': '1.8e-4',
        'V-100': '1.4e-3', 'V-101': '1.4e-3', 'V-102': '1.4e-3', 'V-103': '1.3e-2',
    }
    expected_atmosphere = {
        sample: 'air'
        for sample, pressure in expected_pressure_atm.items()
        if pressure is not None and sample.startswith('V-')
    }
    expected_atmosphere.update({sample: 'CO–CO2 gas mixture' for sample in ('NV-61', 'NV-62', 'NV-63')})
    expected_atmosphere.update({sample: 'H2' for sample in ('V20', 'V21', 'V22', 'V-94', 'V-95', 'V-96', 'V-97', 'V-98', 'V-99', 'V-100', 'V-101', 'V-102')})
    expected_ratios = {
        'V-54': ('1', '1', '1450', '0'),
        'V-5': ('0.50', '0.29', '1450', '5'), 'V-6': ('0.35', '0.15', '1450', '10'),
        'V-7': ('0.29', '0.08', '1450', '15'), 'V-8': ('0.53', '0.32', '1450', '5'),
        'V-9': ('0.37', '0.16', '1450', '10'), 'V-10': ('0.28', '0.08', '1450', '15'),
        'V-11': ('0.20', '0.04', '1450', '20'), 'V-12': ('0.08', '0.01', '1450', '30'),
        'V-13': ('0.05', '0.003', '1450', '40'), 'V-43': ('0.500', '0.290', '1450', '5'),
        'V-44': ('0.287', '0.084', '1450', '15'), 'V-45': ('0.082', '0.010', '1450', '30'),
        'V-46': ('0.047', '0.006', '1450', '48'), 'NV-61': ('0.900', '0.797', '1450', '30'),
        'NV-62': ('0.770', '0.589', '1450', '60'), 'NV-63': ('0.590', '0.438', '1450', '90'),
        'V20': ('0.52', '0.33', '1450', '5'), 'V21': ('0.22', '0.06', '1450', '15'),
        'V22': ('0.03', '0.01', '1450', '30'), 'V-59': ('0.760', '0.441', '1450', '5000°C/hr'),
        'V-60': ('0.530', '0.188', '1450', '500°C/hr'),
        'V-85': ('0.396', '0.236', '1485', '5'), 'V-86': ('0.046', '0.017', '1485', '20'),
        'V-87': ('0.007', '0.010', '1485', '40'), 'V-88': ('0.406', '0.233', '1485', '5'),
        'V-89': ('0.057', '0.024', '1485', '20'), 'V-90': ('0.020', '0.013', '1485', '40'),
        'V-91': ('0.630', '0.508', '1485', '5'), 'V-92': ('0.372', '0.219', '1485', '20'),
        'V-93': ('0.206', '0.091', '1485', '40'), 'V-94': ('0.424', '0.239', '1485', '5'),
        'V-95': ('0.021', '0.020', '1485', '20'), 'V-96': ('0.006', '0.012', '1485', '40'),
        'V-97': ('0.418', '0.215', '1485', '5'), 'V-98': ('0.010', '0.012', '1485', '20'),
        'V-99': ('0.007', '0.011', '1485', '40'), 'V-100': ('0.228', '0.081', '1485', '5'),
        'V-101': ('0.013', '0.008', '1485', '20'), 'V-102': ('0.007', '0.007', '1485', '40'),
        'V-103': ('0.185', '0.056', '1485', '5'),
    }
    _assert_table2_ratios(document, expected_ratios, expected_pressure_atm, expected_atmosphere)
    for sample, observation in by_sample.items():
        conditions = observation.point_conditions or {}
        pressure = expected_pressure_atm[sample]
        if pressure is None:
            assert 'total_pressure_atm' not in conditions
        else:
            assert conditions['total_pressure_atm'].state.is_value, sample
            assert Decimal(str(conditions['total_pressure_atm'].state.value)) == Decimal(pressure), sample
            assert conditions['total_pressure_atm'].locator is not None, sample
        if sample == 'V-54':
            assert 'atmosphere' not in conditions
        else:
            assert conditions['atmosphere'].state.is_value, sample
            assert conditions['atmosphere'].state.value == expected_atmosphere[sample], sample
            assert conditions['atmosphere'].locator is not None, sample
        if sample in {'NV-61', 'NV-62', 'NV-63'}:
            assert conditions['fO2_control'].state.is_value, sample
            assert conditions['fO2_control'].locator is not None, sample
        assert conditions['temperature_C'].state.is_value, sample
        assert conditions['temperature_C'].locator is not None, sample
        assert conditions['temperature_C'].state.value == observation.identity.temperature_K.value - Decimal('273.15')
        assert 'duration_min' in conditions or 'peak_temperature_hold_s' in conditions
        assert not any(
            notice.kind.value == 'source_value_outside_physical_range'
            for notice in observation.notices
        )

    ratios_k = document['species']['K']['context'][0]['values']['points']
    ratios_na = document['species']['Na']['context'][0]['values']['points']
    assert len(ratios_k) == len(ratios_na) == 41
    assert 'K_over_K0_as_published' in ratios_k[10]
    assert 'Na_over_Na0_as_published' in ratios_na[40]
    assert document['species']['K']['context'][0]['values']['points'][10]['K_over_K0_as_published'] == '0.500'
    assert document['species']['Na']['context'][0]['values']['points'][40]['Na_over_Na0_as_published'] == '0.056'
    assert '0.500' in row_v43 and '0.056' in row_v103

    assert Counter(item.identity.species.formula for item in observations) == Counter({'K': 32})
    assert 'quenched in vacuum or in air' in document['species']['K']['observations'][0]['note']
    assert check_all_fidelity_samples_match(document, label=str(EXTRACT)) == []
    mutated_typed = deepcopy(document)
    mutated_typed['species']['K']['observations'][0]['values']['points'][4]['isotope_delta_isotope_delta'] = '999'
    assert any(
        'value mismatch' in error
        for error in check_all_fidelity_samples_match(mutated_typed, label='mutated Yu isotope cell')
    )
    mutated_context = deepcopy(document)
    mutated_context['species']['K']['context'][0]['values']['points'][10]['K_over_K0_as_published'] = '0.999'
    with pytest.raises(AssertionError):
        _assert_table2_ratios(mutated_context, expected_ratios, expected_pressure_atm, expected_atmosphere)
    mutated_table1 = deepcopy(document)
    mutated_table1['species']['SiO2']['context'][0]['values']['composition_wt_pct']['SiO2'] = '0'
    with pytest.raises(AssertionError):
        _assert_table1_compositions(mutated_table1)
