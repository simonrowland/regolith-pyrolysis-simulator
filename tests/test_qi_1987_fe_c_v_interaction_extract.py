from __future__ import annotations

import csv
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / 'data/literature/extracts/qi-1987-fe-c-v-interaction.yaml'
RENDER_GRID = ROOT / 'tests/fixtures/literature/qi-1987-fe-c-v-interaction-table3-render-grid.csv'

TABLE1 = [
    ('1833', '-8.0', '(1)'),
    ('1803', '-4.49', '(7)'),
    ('1823', '-6.2', '(8)'),
    ('1823', '-11', '(9)'),
    ('1833', '-16.1', '(10)'),
    ('1873', '-6.8', '(11)'),
    ('1873', '-16.1', '(13)'),
]
TABLE2 = {
    'chipman': ['4.77', '4.90', '5.02'],
    'kitchner': ['4.73', '4.86', '4.99'],
    'matoba_banya': ['4.86', '5.00', '5.13'],
    'present': ['4.74', '4.87', '5.00'],
}
TEMPERATURES = {'1623': Decimal('1349.85'), '1673': Decimal('1399.85'), '1773': Decimal('1499.85')}


def _read_grid() -> list[dict[str, str]]:
    with RENDER_GRID.open(encoding='utf-8', newline='') as stream:
        return list(csv.DictReader(stream))


def _decimal(value: object) -> Decimal:
    return Decimal(str(value).replace('−', '-'))


def _assert_table3_matches_render(doc: dict, grid: list[dict[str, str]]) -> None:
    rows = {
        row['observation_id']: row
        for row in doc['species']['C']['observations']
        if row['values'].get('quantity') == 'equilibrium_phase_composition'
        and row['observation_id'].startswith('qi_1987_table3_')
    }
    assert len(grid) == 26
    assert len(rows) == 52
    for source in grid:
        sample, tk = source['sample_id'], source['T_K']
        for formula, field in (('C', 'C_wt_percent'), ('V', 'V_wt_percent')):
            row = rows[f'qi_1987_table3_{tk}_{sample}_{formula}']
            values = row['values']
            assert row['experiment'] == f'qi1987-table3-{tk}k'
            assert row['locator']['page'] == 3
            assert row['locator']['pdf_page_index'] == 2
            assert row['locator']['published_page'] == 414
            assert row['locator']['table'] == '3'
            assert row['locator']['row_label'] == sample
            assert values['sample_id'] == sample
            assert values['formula'] == formula
            assert values['phase_label'] == 'metal'
            assert values['basis'] == 'wt_percent'
            assert _decimal(values['T_C']) == TEMPERATURES[tk]
            assert _decimal(values['component_amount']) == _decimal(source[field])
            condition = values['point_conditions']['temperature_K']
            assert _decimal(condition['state']['value']) == Decimal(tk)
            assert condition['locator']['page'] == 3
            assert condition['locator']['pdf_page_index'] == 2
            assert values['point_conditions']['gas_mix']['state']['value'] == 'Ar'


def test_qi_table3_is_independently_bound_to_the_600_dpi_render_grid() -> None:
    doc = yaml.safe_load(EXTRACT.read_text(encoding='utf-8'))
    grid = _read_grid()
    _assert_table3_matches_render(doc, grid)

    changed_grid = deepcopy(grid)
    changed_grid[0]['C_wt_percent'] = '4.75'
    with pytest.raises(AssertionError):
        _assert_table3_matches_render(doc, changed_grid)

    changed_extract = deepcopy(doc)
    changed_row = next(
        row for row in changed_extract['species']['C']['observations']
        if row['observation_id'] == 'qi_1987_table3_1623_191_C'
    )
    changed_row['values']['component_amount'] = '999'
    with pytest.raises(AssertionError):
        _assert_table3_matches_render(changed_extract, grid)


def test_qi_tables1_and2_keep_each_printed_value_and_attribution() -> None:
    doc = yaml.safe_load(EXTRACT.read_text(encoding='utf-8'))
    observations = doc['species']['C']['observations']
    table1 = [row for row in observations if row['observation_id'].startswith('qi_1987_table1_')]
    assert len(table1) == len(TABLE1)
    assert [(row['values']['T_K'], row['values']['epsilon'], row['values']['attribution'].split()[-1].replace(')', '')) for row in table1] == [
        (temperature, value, reference.strip('()')) for temperature, value, reference in TABLE1
    ]

    table2 = [row for row in observations if row['observation_id'].startswith('qi_1987_table2_')]
    assert len(table2) == 12
    for source, values in TABLE2.items():
        for tk, expected in zip(('1623', '1673', '1773'), values, strict=True):
            row = next(row for row in table2 if row['observation_id'] == f'qi_1987_table2_{source}_{tk}_C')
            assert row['values']['component_amount'] == expected
            assert row['values']['formula'] == 'C'
            assert row['values']['phase_label'] == 'metal'
            assert row['locator']['page'] == 2
            assert row['locator']['pdf_page_index'] == 1
            assert row['values']['method_class'] == ('measured_direct' if source == 'present' else 'quoted_attributed')


def test_qi_values_migrate_as_points_with_temperature_and_conditions(tmp_path: Path) -> None:
    from tests.battery.test_migrate import _migrate_real_extract

    result = _migrate_real_extract(tmp_path, EXTRACT.name)
    assert result.validation is not None
    assert result.validation.ok, result.validation.hard_issues
    assert result.validation.hard_issues == (), result.validation.hard_issues
    rows = {
        row.observation_id.split('::', 1)[-1]: row
        for row in result.observations.values()
        if row.source_id == 'qi-1987-fe-c-v-interaction'
    }
    assert len(rows) == 71
    assert all(row.value.point is not None for row in rows.values())
    assert all(not row.notices for row in rows.values())

    for source in _read_grid():
        sample, tk = source['sample_id'], source['T_K']
        for formula, field in (('C', 'C_wt_percent'), ('V', 'V_wt_percent')):
            row = rows[f'qi_1987_table3_{tk}_{sample}_{formula}']
            assert row.value.point == _decimal(source[field])
            assert row.identity.species.formula == formula
            assert row.identity.phase_role.value.value == 'metal'
            assert row.identity.temperature_K.value == Decimal(tk)
            assert row.point_conditions['temperature_K'].state.value == Decimal(tk)
            assert row.point_conditions['temperature_K'].locator.page == 3
            assert row.point_conditions['temperature_K'].locator.pdf_page_index == 2

    for source, values in TABLE2.items():
        for tk, expected in zip(('1623', '1673', '1773'), values, strict=True):
            row = rows[f'qi_1987_table2_{source}_{tk}_C']
            assert row.value.point == Decimal(expected)
            assert row.identity.species.formula == 'C'
            assert row.identity.temperature_K.value == Decimal(tk)
            assert row.point_conditions['temperature_K'].state.value == Decimal(tk)
    for index, (tk, epsilon, _) in enumerate(TABLE1, 1):
        row = rows[f'qi_1987_table1_row_{index}_epsilon_C_V']
        assert row.value.point == Decimal(epsilon)
        assert row.identity.species.formula == 'C'
        assert row.identity.temperature_K.value == Decimal(tk)
        assert row.point_conditions['temperature_K'].state.value == Decimal(tk)


def test_qi_figures_are_context_only_and_deferred() -> None:
    doc = yaml.safe_load(EXTRACT.read_text(encoding='utf-8'))
    context = doc['species']['C']['context']
    figures = [row for row in context if row['type'] == 'figure_only']
    assert len(figures) == 20
    assert {row['locator']['figure'] for row in figures} == {str(n) for n in range(1, 21)}
    for row in figures:
        assert row['values']['digitisation'] == 'deferred to a figure pass'
        assert row['values']['admission_status'] == 'typed_refusal'
        assert 'value' not in row['values']
        assert row['values']['series']
