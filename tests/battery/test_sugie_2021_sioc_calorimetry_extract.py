from __future__ import annotations

import os
from pathlib import Path
from decimal import Decimal
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
    observations = doc['species']['SiOCH']['observations']
    assert [(row['values']['sample_id'], row['values']['delta_f_H']) for row in observations] == [
        ('HQ4_SG 800 C', -257.2), ('HQ4_SG 1100 C', -265.8),
        ('PhSiO1.5_SG 800 C', -93.2), ('PhSiO1.5_SG 1100 C', -116.4)
    ]


def test_extraction_does_not_score_incompatible_bulk_enthalpies():
    doc = _doc()
    assert len(doc['species']['SiOCH']['observations']) == 4
    with patch.object(migrate_module, 'discover_extracts', return_value=[EXTRACT]):
        migrator = migrate_module.Migrator(root=ROOT)
        migrator.migrate_extracts()
        migrator.finalize()
    rows = [row for row in migrator.result.observations.values() if row.source_id == SID]
    assert len(rows) == 4
    assert {row.value.point for row in rows} == {Decimal('-257.2'), Decimal('-265.8'), Decimal('-93.2'), Decimal('-116.4')}
    assert all(row.value.kind.value == 'point' and row.identity.quantity.value == 'delta_fH' for row in rows)
    assert all(row.identity.per.value.value == 'mol_atom' for row in rows)
    assert all(row.point_conditions['temperature_K'].state.value == Decimal('298.15') for row in rows)
    assert not [issue for issue in migrator.result.registry_issues if SID in issue.path]


def test_numeric_context_pin_fails_if_source_cell_is_mutated():
    doc = _doc()
    row = next(row for row in doc['species']['SiOCH']['context'] if row['observation_id'] == 'sugie2021_table6_context')
    cell = row['values']['printed_rows'][0][2]
    assert cell == 'Delta Hds=-11.0 +/- 0.2 kJ/g-at'
    changed = cell.replace('-11.0', '-10.0')
    with pytest.raises(AssertionError):
        assert changed == 'Delta Hds=-11.0 +/- 0.2 kJ/g-at'
