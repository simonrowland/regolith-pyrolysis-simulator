from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

import simulator.battery.migrate as migrate_module

ROOT = Path(__file__).resolve().parents[2]
SID = 'sugie-2021-sioc-mixed-bond-calorimetry'
EXTRACT = ROOT / 'data/literature/extracts' / f'{SID}.yaml'
CORPUS = Path(os.environ.get('REGOLITH_CORPUS_ROOT', '/Users/simonrowland/Repos/regolith-corpus'))
PDF = CORPUS / 'raw' / SID / f'{SID}.pdf'


def _doc():
    if not PDF.is_file():
        pytest.skip(f'paper source missing: {PDF}')
    return yaml.safe_load(EXTRACT.read_text())


def test_table_values_and_reaction_basis_are_preserved():
    doc = _doc()
    context = doc['species']['SiOCH']['context']
    table6 = next(row for row in context if row['observation_id'] == 'sugie2021_table6_context')
    rows = table6['values']['printed_rows']
    assert len(rows) == 4
    assert rows[0][0] == 'HQ4_SG 800 C'
    assert 'Delta Hf,elem=-257.2 +/- 0.6 kJ/g-at' in rows[0]
    assert 'Delta Hf,comp=20.0 +/- 0.9 kJ/g-at' in rows[0]
    table5 = next(row for row in context if row['observation_id'] == 'sugie2021_table5_context')
    assert 'SiC' in table5['values']['printed_rows'][2][0]
    assert 'cristobalite' in table5['values']['printed_rows'][2][0]
    assert 'not delta_fH' in table6['values']['reason']


def test_extraction_does_not_score_incompatible_bulk_enthalpies():
    doc = _doc()
    assert not doc['species']['SiOCH']['observations']
    with patch.object(migrate_module, 'discover_extracts', return_value=[EXTRACT]):
        migrator = migrate_module.Migrator(root=ROOT)
        migrator.migrate_extracts()
        migrator.finalize()
    assert not [row for row in migrator.result.observations.values() if row.source_id == SID]
    assert not [issue for issue in migrator.result.registry_issues if SID in issue.path]


def test_numeric_context_pin_fails_if_source_cell_is_mutated():
    doc = _doc()
    row = next(row for row in doc['species']['SiOCH']['context'] if row['observation_id'] == 'sugie2021_table6_context')
    cell = row['values']['printed_rows'][0][2]
    assert cell == 'Delta Hds=-11.0 +/- 0.2 kJ/g-at'
    changed = cell.replace('-11.0', '-10.0')
    with pytest.raises(AssertionError):
        assert changed == 'Delta Hds=-11.0 +/- 0.2 kJ/g-at'
