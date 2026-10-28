from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import ValueKind
from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / 'data/literature/extracts/shornikov-2025-perovskite-condensation.yaml'
SOURCE_ID = 'shornikov-2025-perovskite-condensation'
TEMPERATURES_K = (1800, 2241, 2400)

# Each pair is the Decimal-compatible literal and the superscript spelling printed in Table 1.
TABLE_1 = {
    'O': {
        'row': 3,
        'pressure': (
            ('6.25e-10', '6.25×10⁻¹⁰'),
            ('8.38e-7', '8.38×10⁻⁷'),
            ('5.98e-6', '5.98×10⁻⁶'),
        ),
        'x_i': (
            ('3.0e-1', '3.0×10⁻¹'),
            ('1.5e-1', '1.5×10⁻¹'),
            ('1.7e-1', '1.7×10⁻¹'),
        ),
        'ratio': '0.6',
    },
    'O2': {
        'row': 4,
        'pressure': (
            ('2.64e-11', '2.64×10⁻¹¹'),
            ('5.84e-8', '5.84×10⁻⁸'),
            ('4.87e-7', '4.87×10⁻⁷'),
        ),
        'x_i': (
            ('1.3e-2', '1.3×10⁻²'),
            ('1.1e-2', '1.1×10⁻²'),
            ('1.4e-2', '1.4×10⁻²'),
        ),
        'ratio': '1.1',
    },
    'Ca': {
        'row': 5,
        'pressure': (
            ('6.10e-10', '6.10×10⁻¹⁰'),
            ('5.57e-7', '5.57×10⁻⁷'),
            ('3.05e-6', '3.05×10⁻⁶'),
        ),
        'x_i': (
            ('2.9e-1', '2.9×10⁻¹'),
            ('1.0e-1', '1.0×10⁻¹'),
            ('8.6e-2', '8.6×10⁻²'),
        ),
        'ratio': '0.3',
    },
    'CaO': {
        'row': 6,
        'pressure': (
            ('2.47e-13', '2.47×10⁻¹³'),
            ('1.83e-9', '1.83×10⁻⁹'),
            ('1.80e-8', '1.80×10⁻⁸'),
        ),
        'x_i': (
            ('1.2e-4', '1.2×10⁻⁴'),
            ('3.3e-4', '3.3×10⁻⁴'),
            ('5.1e-4', '5.1×10⁻⁴'),
        ),
        'ratio': '4.3',
    },
    'Ti': {
        'row': 7,
        'pressure': (
            ('2.25e-15', '2.25×10⁻¹⁵'),
            ('1.67e-10', '1.67×10⁻¹⁰'),
            ('2.12e-9', '2.12×10⁻⁹'),
        ),
        'x_i': (
            ('1.1e-6', '1.1×10⁻⁶'),
            ('3.0e-5', '3.0×10⁻⁵'),
            ('6.0e-5', '6.0×10⁻⁵'),
        ),
        'ratio': '56',
    },
    'TiO': {
        'row': 8,
        'pressure': (
            ('3.73e-11', '3.73×10⁻¹¹'),
            ('4.75e-7', '4.75×10⁻⁷'),
            ('3.80e-6', '3.80×10⁻⁶'),
        ),
        'x_i': (
            ('1.8e-2', '1.8×10⁻²'),
            ('8.6e-2', '8.6×10⁻²'),
            ('1.1e-1', '1.1×10⁻¹'),
        ),
        'ratio': '6.1',
    },
    'TiO2': {
        'row': 9,
        'pressure': (
            ('8.05e-10', '8.05×10⁻¹⁰'),
            ('3.58e-6', '3.58×10⁻⁶'),
            ('2.20e-5', '2.20×10⁻⁵'),
        ),
        'x_i': (
            ('3.8e-1', '3.8×10⁻¹'),
            ('6.5e-1', '6.5×10⁻¹'),
            ('6.2e-1', '6.2×10⁻¹'),
        ),
        'ratio': '1.6',
    },
    'CaTiO3': {
        'row': 10,
        'pressure': (
            ('4.87e-15', '4.87×10⁻¹⁵'),
            ('3.58e-9', '3.58×10⁻⁹'),
            ('7.74e-8', '7.74×10⁻⁸'),
        ),
        'x_i': (
            ('2.3e-6', '2.3×10⁻⁶'),
            ('6.5e-4', '6.5×10⁻⁴'),
            ('2.2e-3', '2.2×10⁻³'),
        ),
        'ratio': '945',
    },
}

# Independently converted partial pressures, in the same species and temperature order.
TABLE_PRESSURES_PA = {
    'O': ('6.3328125e-5', '0.08491035', '0.6059235'),
    'O2': ('2.67498e-6', '0.00591738', '0.049345275'),
    'Ca': ('6.180825e-5', '0.056438025', '0.30904125'),
    'CaO': ('2.5027275e-8', '1.8542475e-4', '0.00182385'),
    'Ti': ('2.2798125e-10', '1.6921275e-5', '2.14809e-4'),
    'TiO': ('3.7794225e-6', '0.048129375', '0.385035'),
    'TiO2': ('8.1566625e-5', '0.3627435', '2.22915'),
    'CaTiO3': ('4.9345275e-10', '3.627435e-4', '0.007842555'),
}

PROSE_PRESSURES = {
    'shornikov_2025_prose_tio2_pressure_2200k': ('TiO2', '2.17e-6', '2.17×10⁻⁶', '0.21987525'),
    'shornikov_2025_prose_o_pressure_2200k': ('O', '5.05e-7', '5.05×10⁻⁷', '0.051169125'),
    'shornikov_2025_prose_ca_pressure_2200k': ('Ca', '3.18e-7', '3.18×10⁻⁷', '0.03222135'),
    'shornikov_2025_prose_tio_pressure_2200k': ('TiO', '2.55e-7', '2.55×10⁻⁷', '0.025837875'),
    'shornikov_2025_prose_catio3_pressure_2200k': ('CaTiO3', '1.28e-9', '1.28×10⁻⁹', '1.29696e-4'),
}


def _observation_id(observation) -> str:
    return observation.observation_id.split('::', 1)[1]


def test_table_cells_and_prose_pressures_are_decimal_and_migrate() -> None:
    extract = yaml.safe_load(EXTRACT.read_text())
    document_rows = {
        observation['observation_id']: (species, observation)
        for species, block in extract['species'].items()
        for observation in block.get('observations', [])
    }
    table_rows = {
        observation_id: pair
        for observation_id, pair in document_rows.items()
        if observation_id.startswith('shornikov_2025_perovskite_condensation_p003_t01_')
    }
    assert len(table_rows) == 48
    expected_table_ids = {
        f"shornikov_2025_perovskite_condensation_p003_t01_r{expected['row']:02d}_c{column:02d}"
        for expected in TABLE_1.values()
        for column in range(2, 8)
    }
    assert set(table_rows) == expected_table_ids

    expected_points: dict[str, Decimal] = {}
    expected_temperatures: dict[str, Decimal] = {}
    for species, expected in TABLE_1.items():
        row = expected['row']
        for index, temperature in enumerate(TEMPERATURES_K):
            pressure_id = f'shornikov_2025_perovskite_condensation_p003_t01_r{row:02d}_c{index + 2:02d}'
            found_species, pressure = table_rows[pressure_id]
            pressure_literal, pressure_printed = expected['pressure'][index]
            assert found_species == species
            assert pressure['type'] == 'partial_pressure'
            assert pressure['phase'] == 'gas'
            assert pressure['units'] == 'atm'
            assert pressure['locator']['published_page'] == 33
            assert pressure['values']['quantity'] == 'partial_pressure'
            assert pressure['values']['method_class'] == 'quoted_attributed'
            assert pressure['values']['T_K'] == str(temperature)
            assert pressure['values']['pressure_atm'] == pressure_literal
            assert Decimal(pressure['values']['pressure_atm']) == Decimal(pressure_literal)
            assert pressure['values']['pressure_atm_as_printed'] == pressure_printed
            expected_points[pressure_id] = Decimal(TABLE_PRESSURES_PA[species][index])
            expected_temperatures[pressure_id] = Decimal(temperature)

            composition_id = f'shornikov_2025_perovskite_condensation_p003_t01_r{row:02d}_c{index + 5:02d}'
            found_species, composition = table_rows[composition_id]
            fraction_literal, fraction_printed = expected['x_i'][index]
            assert found_species == species
            assert composition['type'] == 'equilibrium_phase_composition'
            assert composition['phase'] == 'gas'
            assert composition['units'] == 'mol'
            assert composition['locator']['published_page'] == 33
            assert composition['values']['quantity'] == 'equilibrium_phase_composition'
            assert composition['values']['method_class'] == 'quoted_attributed'
            assert composition['values']['basis_as_published'] == 'x_i, mol'
            assert composition['values']['T_K'] == str(temperature)
            assert composition['values']['value'] == fraction_literal
            assert Decimal(composition['values']['value']) == Decimal(fraction_literal)
            assert composition['values']['value_as_printed'] == fraction_printed
            expected_points[composition_id] = Decimal(fraction_literal)
            expected_temperatures[composition_id] = Decimal(temperature)

    context_rows = [
        (species, row)
        for species, block in extract['species'].items()
        for row in block.get('context', [])
    ]
    ratio_cells = {
        refusal['species_as_printed']: refusal['value_as_printed']
        for _, context in context_rows
        if context['type'] == 'refusal'
        for refusal in context['values'].get('refused', [])
        if refusal.get('reason') == 'schema_gap_ratio_across_temperatures'
    }
    assert len(ratio_cells) == 8
    assert ratio_cells == {species: expected['ratio'] for species, expected in TABLE_1.items()}

    prose_rows = {
        observation_id: pair
        for observation_id, pair in document_rows.items()
        if observation_id.startswith('shornikov_2025_prose_')
    }
    assert set(prose_rows) == set(PROSE_PRESSURES)
    for observation_id, (species, expected_literal, expected_printed, expected_pa) in PROSE_PRESSURES.items():
        found_species, observation = prose_rows[observation_id]
        assert found_species == species
        assert observation['type'] == 'partial_pressure'
        assert observation['values']['quantity'] == 'partial_pressure'
        assert observation['values']['method_class'] == 'quoted_attributed'
        assert observation['values']['pressure_atm'] == expected_literal
        assert Decimal(observation['values']['pressure_atm']) == Decimal(expected_literal)
        assert observation['values']['pressure_atm_as_printed'] == expected_printed
        assert observation['point_conditions']['temperature_K']['state']['value']['point'] == '2200'
        assert observation['locator']['published_page'] == 34
        expected_points[observation_id] = Decimal(expected_pa)
        expected_temperatures[observation_id] = Decimal('2200')

    ca_tio3 = extract['species']['CaTiO3']
    contexts_by_id = {row['observation_id']: row for row in ca_tio3['context']}
    range_context = contexts_by_id['shornikov_2025_perovskite_evaporation_temperature_range_context']
    assert range_context['type'] == 'method'
    assert range_context['locator']['published_page'] == 33
    assert range_context['units'] == 'K'
    assert range_context['values']['method_class'] == 'quoted_attributed'
    assert range_context['values']['attribution'] == 'Shornikov (2019, 2019a, 2020)'
    assert range_context['values']['temperature_range_K'] == [1791, 2441]
    assert range_context['values']['quote_as_printed'] == (
        'CaTiO3 perovskite evaporation from a Knudsen effusion molybdenum cell was studied by mass spectrometry '
        'in the temperature range of 1791–2441 K (Shornikov, 2019, 2019a, 2020).'
    )
    ion_source_context = contexts_by_id['shornikov_2025_ion_source_temperature_limit_context']
    assert ion_source_context['type'] == 'method'
    assert ion_source_context['locator']['published_page'] == 33
    assert ion_source_context['units'] == 'K'
    assert ion_source_context['values']['method_class'] == 'quoted_attributed'
    assert ion_source_context['values']['attribution'] == 'Shornikov et al. (2000)'
    assert ion_source_context['values']['ion_source_temperature_limit_K'] == 3000
    assert ion_source_context['values']['quote_as_printed'] == (
        'The mass spectrometer MI-1201 was equipped with a modified ion source for high-temperature studies '
        '(up to a temperature of 3000 K). The design of the developed ion source and the methodological features '
        'of experiment were described in detail earlier (Shornikov et al., 2000).'
    )

    melting = document_rows['shornikov_2025_perovskite_melting_point'][1]
    assert melting['type'] == 'transition_point'
    assert melting['locator']['published_page'] == 34
    assert melting['phase'] == 'perovskite'
    assert melting['units'] == 'K'
    assert melting['values']['method_class'] == 'quoted_attributed'
    assert melting['values']['property_kind'] == 'melting_point'
    assert melting['values']['temperature_K'] == 2241
    assert melting['values']['temperature_K_as_printed'] == '2241 K'
    assert melting['values']['quote_as_printed'] == 'melting point of perovskite, equal to 2241 K'
    expected_points['shornikov_2025_perovskite_melting_point'] = Decimal('2241')

    fidelity = {sample['observation_id']: sample for sample in extract['fidelity_samples']}
    assert fidelity['shornikov_2025_perovskite_condensation_p003_t01_r03_c02']['value'] == '6.25e-10'
    assert fidelity['shornikov_2025_perovskite_condensation_p003_t01_r05_c03']['value'] == '5.57e-7'
    assert fidelity['shornikov_2025_perovskite_condensation_p003_t01_r10_c07']['value'] == '2.2e-3'

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    migrated = {
        _observation_id(observation): observation
        for observation in migrator.result.observations.values()
        if observation.source_id == SOURCE_ID
    }
    assert set(migrated) == set(expected_points)
    assert len(migrated) == 54
    assert not [issue for issue in migrator.result.validation.hard_issues if SOURCE_ID in issue.path]
    for observation_id, expected_point in expected_points.items():
        observation = migrated[observation_id]
        assert observation.value.kind is ValueKind.POINT, observation_id
        assert observation.value.point == expected_point, observation_id
        if observation_id in expected_temperatures:
            assert observation.identity.temperature_K.value == expected_temperatures[observation_id]
    assert migrated['shornikov_2025_perovskite_condensation_p003_t01_r03_c02'].value.point == Decimal('6.3328125e-5')


def test_temperature_conditions_and_deferred_figure_records_are_preserved() -> None:
    extract = yaml.safe_load(EXTRACT.read_text())
    table_and_prose = [
        observation
        for block in extract['species'].values()
        for observation in block.get('observations', [])
        if observation['observation_id'].startswith('shornikov_2025_perovskite_condensation_p003_t01_')
        or observation['observation_id'].startswith('shornikov_2025_prose_')
    ]
    assert len(table_and_prose) == 53
    for observation in table_and_prose:
        condition = observation['point_conditions']['temperature_K']
        assert condition['state']['tag'] == 'value'
        assert condition['locator'].get('table') == '1' or condition['locator']['published_page'] == 34
        assert observation['point_conditions']['fO2_control']['state']['tag'] == 'unknown'
        assert observation['point_conditions']['total_pressure_Pa']['state']['tag'] == 'unknown'

    figure_records = [
        context for block in extract['species'].values()
        for context in block.get('context', [])
        if context['type'] == 'figure_record'
    ]
    assert len(figure_records) == 2
    assert all(record['values']['digitisation'] == 'deferred to a figure pass' for record in figure_records)
    assert all(all(series['marker_count'] == 0 for series in record['values']['series']) for record in figure_records)
