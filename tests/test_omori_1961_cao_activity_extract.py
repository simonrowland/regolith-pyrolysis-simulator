from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.migrate import Migrator
from simulator.battery.records import ValueKind

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / 'data/literature/extracts/omori-1961-cao-activity-cas.yaml'

RENDER_PINS = {
    'omori_1961_table_1_row_01_cao_activity': ('3', '1', '1', 9, '1.00', '1.00'),
    'omori_1961_table_1_row_02_sio2_activity': ('3', '1', '2', 11, '0.0005', '(0.0005)'),
    'omori_1961_table_1_row_09_cao_activity': ('3', '1', '9', 9, '0.012', '0.012'),
    'omori_1961_table_2_cb_03_cao_activity_a': ('4', '2', '〃-3', 8, '0.148', '0.148'),
    'omori_1961_table_2_cb_10_cao_activity_b': ('4', '2', '〃-10', 9, '0.0175', '0.0175'),
}


def _document():
    return yaml.safe_load(EXTRACT.read_text(encoding='utf-8'))


def _raw_rows(document):
    return {
        row['observation_id']: row
        for species in document['species'].values()
        for row in species['observations']
    }


def _assert_render_pins(document):
    rows = _raw_rows(document)
    for observation_id, (page, table, row_label, column, value, printed) in RENDER_PINS.items():
        row = rows[observation_id]
        assert row['locator']['page'] == int(page)
        assert row['locator']['table'] == table
        assert row['locator']['row_label'] == row_label
        assert row['locator']['table_column_number'] == column
        assert row['values']['activity'] == value
        assert row['values']['activity_as_printed'] == printed


def _migrate():
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    return migrator


def test_render_pins_and_mutation_guard():
    document = _document()
    _assert_render_pins(document)
    changed = deepcopy(document)
    changed['species']['CaO']['observations'][0]['values']['activity'] = '0.10'
    with pytest.raises(AssertionError):
        _assert_render_pins(changed)


def test_all_table_rows_keep_composition_conditions_and_unknown_atmosphere():
    document = _document()
    assert len(document['species']['CaO']['observations']) == 31
    assert len(document['species']['SiO2']['observations']) == 22
    assert len(document['species']['Al2O3']['observations']) == 10
    assert document['scope_assessment']['activity_row_count'] == {
        'CaO': 31,
        'SiO2_inputs': 22,
        'Al2O3_inputs': 10,
    }
    assert [
        sum(row['locator']['table'] == table for row in document['species']['CaO']['observations'])
        for table in ('1', '2')
    ] == [12, 19]

    migrator = _migrate()
    result = migrator.result
    assert result.validation is not None
    assert result.validation.hard_issues == ()
    observations = list(result.observations.values())
    assert len(observations) == 63
    assert sum(row.value.kind is ValueKind.UNAVAILABLE for row in observations) == 0

    for row in observations:
        assert row.value.kind is ValueKind.POINT
        assert row.identity.temperature_K.is_value
        assert row.identity.temperature_K.value == Decimal('1903.15')
        temperature = row.point_conditions['temperature_K']
        assert temperature.state.is_value
        assert temperature.state.value == Decimal('1903.15')
        assert temperature.locator.page == row.locator.page
        composition = row.point_conditions['composition']
        assert composition.state.is_value
        assert composition.locator.page == row.locator.page
        assert row.identity.composition == composition.state
        assert row.identity.fO2_Pa.is_unknown
        assert row.identity.total_pressure_Pa.is_unknown
        assert not row.identity.fO2_Pa.is_value
        assert not row.identity.total_pressure_Pa.is_value
        if 'fO2_Pa' in row.point_conditions:
            assert row.point_conditions['fO2_Pa'].state.is_unknown
        if 'total_pressure_Pa' in row.point_conditions:
            assert row.point_conditions['total_pressure_Pa'].state.is_unknown

    migrated = {
        row.observation_id.split('::', 1)[1]: row
        for row in observations
    }
    expected = {
        'omori_1961_table_1_row_01_cao_activity': Decimal('1.00'),
        'omori_1961_table_1_row_02_sio2_activity': Decimal('0.0005'),
        'omori_1961_table_1_row_09_cao_activity': Decimal('0.012'),
        'omori_1961_table_2_cb_03_cao_activity_a': Decimal('0.148'),
        'omori_1961_table_2_cb_10_cao_activity_b': Decimal('0.0175'),
    }
    for observation_id, value in expected.items():
        row = migrated[observation_id]
        assert row.value.point == value
        assert row.locator.page == int(RENDER_PINS[observation_id][0])
        assert row.locator.table == RENDER_PINS[observation_id][1]

    for observation_id in (
        'omori_1961_table_1_row_03_cao_activity',
        'omori_1961_table_2_cb_03_cao_activity_b',
    ):
        row = migrated[observation_id]
        assert row.derived_from
        assert row.derivation is not None
        assert set(row.derived_from) == set(row.derivation.inputs)

    samples = [migrated[key] for key in (
        'omori_1961_table_1_row_01_cao_activity',
        'omori_1961_table_1_row_09_cao_activity',
        'omori_1961_table_2_cb_03_cao_activity_a',
    )]
    for row in samples:
        composition = row.identity.composition.value
        print(
            f'{row.observation_id}: composition={composition.components}; '
            f'total_pressure_Pa={row.identity.total_pressure_Pa.tag.value}; '
            f'fO2_Pa={row.identity.fO2_Pa.tag.value}; '
            f'temperature_K={row.identity.temperature_K.value}'
        )
