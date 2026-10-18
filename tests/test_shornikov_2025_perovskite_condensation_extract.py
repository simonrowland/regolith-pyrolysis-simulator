from __future__ import annotations

from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / 'data/literature/extracts/shornikov-2025-perovskite-condensation.yaml'


def test_table_cells_and_prose_pressures_are_preserved_and_migrate() -> None:
    extract = yaml.safe_load(EXTRACT.read_text())
    table_rows = []
    prose_rows = []
    for species, block in extract['species'].items():
        for observation in block.get('observations', []):
            if observation['observation_id'].startswith('shornikov_2025_perovskite_condensation_p003_t01_'):
                table_rows.append((species, observation))
            elif observation['observation_id'].startswith('shornikov_2025_prose_'):
                prose_rows.append((species, observation))

    assert len(table_rows) == 48
    assert len(prose_rows) == 5
    assert {species for species, _ in table_rows} == {'O', 'O2', 'Ca', 'CaO', 'Ti', 'TiO', 'TiO2', 'CaTiO3'}
    assert sum(obs['values']['quantity'] == 'partial_pressure' for _, obs in table_rows) == 24
    assert sum(obs['values']['quantity'] == 'vapor_mole_fraction' for _, obs in table_rows) == 24
    ratio_refusals = [
        (species, refusal)
        for species, block in extract['species'].items()
        for context in block.get('context', [])
        if context['type'] == 'refusal'
        for refusal in context['values'].get('refused', [])
    ]
    assert len(ratio_refusals) == 8
    assert {species for species, _ in ratio_refusals} == {'O', 'O2', 'Ca', 'CaO', 'Ti', 'TiO', 'TiO2', 'CaTiO3'}
    assert {item['value_as_printed'] for _, item in ratio_refusals} == {'0.6', '1.1', '0.3', '4.3', '56', '6.1', '1.6', '945'}

    table_pins = {
        'shornikov_2025_perovskite_condensation_p003_t01_r03_c02': ('O', 'pressure_atm', '6.25×10⁻¹⁰'),
        'shornikov_2025_perovskite_condensation_p003_t01_r05_c03': ('Ca', 'pressure_atm', '5.57×10⁻⁷'),
        'shornikov_2025_perovskite_condensation_p003_t01_r10_c07': ('CaTiO3', 'value', '2.2×10⁻³'),
    }
    by_id = {obs['observation_id']: (species, obs) for species, obs in table_rows}
    for observation_id, (species, field, expected) in table_pins.items():
        found_species, observation = by_id[observation_id]
        assert found_species == species
        assert observation['values'][field] == expected
        assert observation['locator']['published_page'] == 33

    prose_pins = {
        'shornikov_2025_prose_tio2_pressure_2200k': ('TiO2', '2.17×10⁻⁶'),
        'shornikov_2025_prose_o_pressure_2200k': ('O', '5.05×10⁻⁷'),
        'shornikov_2025_prose_ca_pressure_2200k': ('Ca', '3.18×10⁻⁷'),
        'shornikov_2025_prose_tio_pressure_2200k': ('TiO', '2.55×10⁻⁷'),
        'shornikov_2025_prose_catio3_pressure_2200k': ('CaTiO3', '1.28×10⁻⁹'),
    }
    prose_by_id = {obs['observation_id']: (species, obs) for species, obs in prose_rows}
    for observation_id, (species, expected) in prose_pins.items():
        found_species, observation = prose_by_id[observation_id]
        assert found_species == species
        assert observation['values']['pressure_atm'] == expected
        assert observation['point_conditions']['temperature_K']['state']['value']['point'] == '2200'
        assert observation['locator']['published_page'] == 34

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    migrated = [
        observation for observation in migrator.result.observations.values()
        if observation.source_id == 'shornikov-2025-perovskite-condensation'
    ]
    assert len(migrated) == 53
    assert not [issue for issue in migrator.result.validation.hard_issues if 'shornikov-2025' in issue.path]
    for species, observation in table_rows + prose_rows:
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
