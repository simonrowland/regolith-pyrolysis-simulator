from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

import simulator.battery.migrate as migrate_module
from tests.battery.test_migrate import assert_extract_source_mutation_fails

ROOT = Path(__file__).resolve().parents[2]
SID = 'anand-2024-sioc-carbothermal-stability'
EXTRACT = ROOT / 'data/literature/extracts' / f'{SID}.yaml'
CORPUS = Path(os.environ.get('REGOLITH_CORPUS_ROOT', '/Users/simonrowland/Repos/regolith-corpus'))
PDF = CORPUS / 'raw' / SID / f'{SID}.pdf'


def _doc():
    if not PDF.is_file():
        pytest.skip(f'paper source missing: {PDF}')
    return yaml.safe_load(EXTRACT.read_text())


def test_xps_composition_and_computed_thermodynamics_are_preserved_in_context():
    doc = _doc()
    table1 = doc['species']['SiOCH']['context'][0]
    assert table1['locator']['page'] == table1['locator']['pdf_page_index'] + 1
    assert table1['values']['printed_rows'][2]['empirical_formula_as_published'] == 'SiO0.8C0.6'
    assert table1['values']['printed_rows'][3]['Si_at_pct'] == '39.73'
    table2 = doc['species']['SiOCH']['context'][1]
    assert table2['values']['printed_rows'][1]['delta_Gf_as_printed'] == '−224.61 kJ·K−1·mol−1'
    assert table2['values']['printed_rows'][3]['minus_T_delta_Sf_as_printed_at_1400_C'] == '−17.60 kJ·mol−1'
    gas = doc['species']['SiOCH']['context'][2]['values']
    assert 'CO' in gas['printed_statement'] and 'CO2' in gas['printed_statement']
    assert all(not group['observations'] for group in doc['species'].values())


def test_all_figures_are_deferred_without_numeric_points():
    doc = _doc()
    figures = [row for row in doc['species']['SiOCH']['context'] if row['type'] == 'figure_record']
    assert {row['locator']['figure'] for row in figures} == {'1', '2', '3', '4', '5'}
    assert all(row['values']['digitisation'] == 'deferred to a figure pass' for row in figures)
    assert all(not row['values'].get('points') for row in figures)
    xrd = next(row for row in figures if row['locator']['figure'] == '2')
    assert xrd['values']['series'][-2]['marker_count'] == 5
    assert xrd['values']['series'][-1]['marker_count'] == 1


def test_context_only_extract_migrates_without_scored_rows_or_issues():
    _doc()
    with patch.object(migrate_module, 'discover_extracts', return_value=[EXTRACT]):
        migrator = migrate_module.Migrator(root=ROOT)
        migrator.migrate_extracts()
        migrator.finalize()
    assert not [row for row in migrator.result.observations.values() if row.source_id == SID]
    assert not [issue for issue in migrator.result.registry_issues if SID in issue.path]


def test_numeric_context_pin_fails_if_source_cell_is_mutated(tmp_path, monkeypatch):
    _doc()
    assert_extract_source_mutation_fails(
        EXTRACT,
        'delta_Gf_as_printed: \'−224.61 kJ·K−1·mol−1\'',
        'delta_Gf_as_printed: \'−224.60 kJ·K−1·mol−1\'',
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        assertion=test_xps_composition_and_computed_thermodynamics_are_preserved_in_context,
    )
