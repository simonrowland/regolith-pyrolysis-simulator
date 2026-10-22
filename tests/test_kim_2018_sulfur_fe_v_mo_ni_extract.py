from __future__ import annotations

import csv
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / 'data/literature/extracts/kim-2018-sulfur-fe-v-mo-ni.yaml'
RENDER_GRID = ROOT / 'tests/fixtures/literature/kim-2018-sulfur-fe-v-mo-ni-render-grid.csv'


def _read_grid() -> list[dict[str, str]]:
    with RENDER_GRID.open(encoding='utf-8', newline='') as stream:
        return list(csv.DictReader(stream))


def _decimal(value: object) -> Decimal:
    return Decimal(str(value).replace('−', '-').replace('†', '').replace(' ', ''))


def _metal_components(value: str) -> dict[str, str]:
    return dict(part.split('=', 1) for part in value.split(';'))


def _assert_render_grid_matches_extract(doc: dict, grid: list[dict[str, str]]) -> None:
    observations = {
        row['observation_id']: row
        for row in doc['species']['S']['observations']
    }
    context = {
        row['observation_id']: row
        for row in doc['species']['S']['context']
    }
    assert len(grid) == 38

    for source in grid:
        sample = source['sample_id']
        prefix = f"kim2018_{sample.replace('-', '_')}"
        ratio = observations[f'{prefix}_L_S']
        slag_s = observations[f'{prefix}_slag_S']
        metal = _metal_components(source['metal_composition_mass_percent'])
        ratio_values = ratio['values']
        slag_values = slag_s['values']

        assert ratio_values['quantity'] == 'distribution_ratio'
        assert ratio_values['formula'] == 'S'
        assert ratio_values['phase_label'] == 'slag'
        assert ratio_values['counter_phase_label'] == 'metal'
        assert ratio_values['sample_id'] == sample
        assert ratio_values['system_as_printed'] == source['system']
        assert ratio_values['slag_composition_as_printed'] == source['slag_composition']
        assert ratio_values['alloy_composition_mass_percent_as_printed'] == source['metal_composition_mass_percent']
        assert _decimal(ratio_values['distribution_ratio']) == _decimal(source['LS'])

        assert slag_values['quantity'] == 'equilibrium_phase_composition'
        assert slag_values['formula'] == 'S'
        assert slag_values['phase_label'] == 'slag'
        assert slag_values['sample_id'] == sample
        assert _decimal(slag_values['component_amount']) == _decimal(source['slag_S_mass_percent'])

        metal_s = observations[f'{prefix}_metal_S']['values']
        assert _decimal(metal_s['component_amount']) == _decimal(metal['S'])
        assert metal_s['phase_label'] == 'metal'
        assert metal_s['sample_id'] == sample
        for formula, published in metal.items():
            component_id = f'{prefix}_metal_{formula}'
            if published in {'-', '–', '—'}:
                assert component_id not in observations
                assert published in metal_s['alloy_composition_mass_percent_as_printed']
                continue
            component = observations[component_id]
            values = component['values']
            assert values['quantity'] == 'equilibrium_phase_composition'
            assert values['formula'] == formula
            assert values['phase_label'] == 'metal'
            assert values['sample_id'] == sample
            assert _decimal(values['component_amount']) == _decimal(published)

        assert ratio['derived_from'] == [slag_s['observation_id'], f'{prefix}_metal_S']
        assert ratio['locator']['page'] == (3 if source['table'] == '1' else 4 if source['table'] == '2' else 5)
        assert ratio['locator']['pdf_page_index'] == ratio['locator']['page'] - 1
        assert ratio['locator']['published_page'] == ratio['locator']['page'] + 407
        assert ratio['locator']['row_label'] == str(source['row'])
        for row in (ratio, slag_s, observations[f'{prefix}_metal_S']):
            condition = row['values']['point_conditions']['temperature_K']
            assert condition['state']['value'] == '1873'
            assert condition['locator']['page'] == row['locator']['page']
            assert condition['locator']['pdf_page_index'] == row['locator']['page'] - 1
            assert row['values']['point_conditions']['gas_mix']['state']['value'] == 'CO'

        log_context = context[f'{prefix}_log_L_S_context']['values']
        assert log_context['value_as_printed'] == source['log_LS']
        assert log_context['printed_form'] == 'log10'
        assert log_context['table_value_as_printed'] == source['LS']


def test_tables_1_to_3_are_independently_bound_to_the_600_dpi_render_grid() -> None:
    doc = yaml.safe_load(EXTRACT.read_text(encoding='utf-8'))
    grid = _read_grid()
    _assert_render_grid_matches_extract(doc, grid)

    changed_grid = deepcopy(grid)
    changed_grid[0]['LS'] = '999'
    with pytest.raises(AssertionError):
        _assert_render_grid_matches_extract(doc, changed_grid)

    changed_extract = deepcopy(doc)
    changed_extract['species']['S']['observations'][0]['values']['component_amount'] = '999'
    with pytest.raises(AssertionError):
        _assert_render_grid_matches_extract(changed_extract, grid)


def test_all_typed_values_migrate_as_points_with_species_temperature_and_conditions(tmp_path: Path) -> None:
    from tests.battery.test_migrate import _migrate_real_extract

    result = _migrate_real_extract(tmp_path, EXTRACT.name)
    assert result.validation is not None
    assert result.validation.hard_issues == (), result.validation.hard_issues

    rows = {
        row.observation_id.split('::', 1)[-1]: row
        for row in result.observations.values()
        if row.source_id == 'kim-2018-sulfur-fe-v-mo-ni'
    }
    assert len(rows) == 208
    assert all(row.value.point is not None for row in rows.values())
    assert not any('source_value_outside_physical_range' in str(issue) for issue in result.validation.issues)

    for source in _read_grid():
        prefix = f"kim2018_{source['sample_id'].replace('-', '_')}"
        expected_temperature = Decimal('1873')
        expected_ids = [f'{prefix}_L_S', f'{prefix}_slag_S']
        expected_ids.extend(
            f'{prefix}_metal_{formula}'
            for formula, published in _metal_components(source['metal_composition_mass_percent']).items()
            if _not_dash(published)
        )
        for observation_id in expected_ids:
            row = rows[observation_id]
            assert row.identity.temperature_K.value == expected_temperature
            assert row.point_conditions['temperature_K'].state.value == expected_temperature
            page = 3 if source['table'] == '1' else 4 if source['table'] == '2' else 5
            assert row.point_conditions['temperature_K'].locator.page == page
            assert row.point_conditions['temperature_K'].locator.pdf_page_index == page - 1
            assert row.point_conditions['gas_mix'].state.value == 'CO'
            assert row.identity.fO2_Pa.is_unknown
            expected_formula = 'S' if observation_id.endswith('_L_S') or observation_id.endswith('_slag_S') else observation_id.rsplit('_metal_', 1)[1]
            assert row.identity.species.formula == expected_formula
            if observation_id.endswith('_L_S'):
                assert row.identity.phase_role.value.value == 'slag'
                assert row.identity.counter_phase_role.value.value == 'metal'
            else:
                role = 'slag' if observation_id.endswith('_slag_S') else 'metal'
                assert row.identity.phase_role.value.value == role

    parameters = [row for observation_id, row in rows.items() if observation_id.startswith('kim2018_table4_')]
    assert len(parameters) == 9
    assert all(row.identity.species.formula == 'S' for row in parameters)
    assert all(row.identity.temperature_K.value in {Decimal('1823'), Decimal('1873')} for row in parameters)
    for observation_id, row in rows.items():
        if observation_id.startswith('kim2018_table4_'):
            condition = row.point_conditions['temperature_K']
            assert condition.state.value == row.identity.temperature_K.value
            assert condition.locator.page == 6
            assert condition.locator.pdf_page_index == 5


def _not_dash(value: str) -> bool:
    return value not in {'-', '–', '—'}


def test_table4_first_order_fits_keep_the_printed_values_and_conflict_note() -> None:
    doc = yaml.safe_load(EXTRACT.read_text(encoding='utf-8'))
    present_experiment = next(
        experiment for experiment in doc['experiments']
        if experiment['experiment_id'] == 'kim2018-table4-present-fit'
    )
    assert present_experiment['conditions']['gas_mix']['state']['value'] == 'CO'
    assert present_experiment['conditions']['temperature_K']['state']['value'] == '1873'
    parameters = [
        row for row in doc['species']['S']['observations']
        if row['values'].get('quantity') == 'wagner_interaction_parameter'
    ]
    assert len(parameters) == 9
    expected = {
        'epsilon_S_V': ['-0.019', '-0.016', '-0.019'],
        'epsilon_S_Ni': ['0', '-0.00006', '0'],
        'epsilon_S_Mo': ['+0.0064', '0.0027', '0.0064'],
    }
    for parameter, values in expected.items():
        records = [row for row in parameters if row['values']['parameter'] == parameter]
        assert [row['values']['epsilon'] for row in records] == values
    mo_fit = next(row for row in parameters if row['values']['parameter'] == 'epsilon_S_Mo' and row['values']['method_class'] == 'model_derived')
    assert mo_fit['uncertainty']['sigma'] == '0.0004'
    assert '−0.0064 ± 0.0004' in mo_fit['values']['note']
    assert len(mo_fit['derived_from']) == 52
    assert all(row.get('uncertainty', {}).get('sigma') != '0' for row in parameters)


def test_figures_and_non_phase_data_are_context_only_and_deferred() -> None:
    doc = yaml.safe_load(EXTRACT.read_text(encoding='utf-8'))
    context = doc['species']['S']['context']
    figures = [row for row in context if row['type'] == 'figure_only']
    assert len(figures) == 10
    assert {row['locator']['figure'] for row in figures} == {str(number) for number in range(1, 11)}
    for row in figures:
        assert row['values']['digitisation'] == 'deferred to a figure pass'
        assert row['values']['series']
        assert all('marker_count' in series for series in row['values']['series'])
        assert 'value' not in row['values']
    table4 = next(row for row in context if row['observation_id'] == 'kim2018_table4_second_order_parameters_context')
    assert [row['value_as_printed'] for row in table4['values']['rows']] == ['0', '0', '0', '0', '0.0003', '0']
    table5 = next(row for row in context if row['observation_id'] == 'kim2018_table5_spent_petroleum_catalyst_context')
    assert table5['values']['values_as_printed']['S'] == '< 15'
