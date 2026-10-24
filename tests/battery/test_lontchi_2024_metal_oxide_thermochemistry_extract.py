from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

import simulator.battery.migrate as migrate_module
from tests.battery.test_migrate import assert_extract_source_mutation_fails

ROOT = Path(__file__).resolve().parents[2]
SID = 'lontchi-2024-metal-oxide-thermochemistry-thesis'
EXTRACT = ROOT / 'data/literature/extracts' / f'{SID}.yaml'
CORPUS = Path(os.environ.get('REGOLITH_CORPUS_ROOT', '/Users/simonrowland/Repos/regolith-corpus'))
PDF = CORPUS / 'raw' / SID / f'{SID}.pdf'


def _doc():
    if not PDF.is_file():
        pytest.skip(f'paper source missing: {PDF}')
    return yaml.safe_load(EXTRACT.read_text())


def test_computed_metal_oxide_values_and_literature_comparators_are_context():
    doc = _doc()
    table32 = doc['species']['VO2']['context'][0]
    assert table32['locator']['page'] == table32['locator']['pdf_page_index'] + 1
    assert table32['values']['printed_rows'][5] == [
        'NbO2', 'ΣD0(0 K)=337.2', 'ΔHf(0 K)=−44.8', 'ΔHf(298 K)=−45.5'
    ]
    assert table32['values']['quoted_experimental_comparators'][0] == {
        'species': 'VO2', 'value_as_published': '−55.6 ± 10', 'attribution': 'Ref. 90'
    }
    table37 = doc['species']['V2O5']['context'][0]
    assert table37['values']['printed_rows'][9] == [
        'Pa2O5', 'tri-bridge', 'ΣD0(0 K)=1043.5', 'ΔHf(0 K)=−476.1', 'ΔHf(298 K)=−477.2'
    ]
    table34 = next(row for row in doc['species']['V2O5']['context'] if row['observation_id'].endswith('table34_ionization_energy'))
    assert table34['values']['printed_rows'][0][2] == 'experimental=12.7 ± 0.2 (Ref. 108); 10.0 ± 0.2 (Ref. 109)'
    assert all(not group['observations'] for group in doc['species'].values())
    assert 'No thesis-only measured metal-oxide thermochemistry was found.' in doc['extraction']['source_note']
    figures = [row for row in doc['species']['V2O5']['context'] if row['type'] == 'figure_record']
    assert {row['locator']['figure'] for row in figures} == {'3.1', '3.2', '3.3', '3.4', '7.2'}
    assert all(row['values']['digitisation'] == 'deferred to a figure pass' for row in figures)
    assert all(not row['values'].get('points') for row in figures)


def test_context_only_extract_migrates_without_scored_rows_or_issues():
    _doc()
    with patch.object(migrate_module, 'discover_extracts', return_value=[EXTRACT]):
        migrator = migrate_module.Migrator(root=ROOT)
        migrator.migrate_extracts()
        migrator.finalize()
    assert not [row for row in migrator.result.observations.values() if row.source_id == SID]
    assert not [issue for issue in migrator.result.registry_issues if SID in issue.path]


def test_numeric_context_pin_fails_if_source_value_is_mutated(tmp_path, monkeypatch):
    _doc()
    assert_extract_source_mutation_fails(
        EXTRACT,
        'ΔHf(0 K)=−476.1',
        'ΔHf(0 K)=−476.0',
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        assertion=test_computed_metal_oxide_values_and_literature_comparators_are_context,
    )
