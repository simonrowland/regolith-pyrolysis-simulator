from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

import simulator.battery.migrate as migrate_module
from simulator.battery.enums import ValueKind
from tools.validate_literature_extracts import check_all_fidelity_samples_match

ROOT = Path(__file__).resolve().parents[2]
SID = 'raberg-2007-salt-stability-na2s-thermochemistry-thesis'
EXTRACT = ROOT / 'data/literature/extracts' / f'{SID}.yaml'
CORPUS = Path(os.environ.get('REGOLITH_CORPUS_ROOT', '/Users/simonrowland/Repos/regolith-corpus'))
PDF = CORPUS / 'raw' / SID / f'{SID}.pdf'


def _doc():
    if not PDF.is_file():
        pytest.skip(f'paper source missing: {PDF}')
    return yaml.safe_load(EXTRACT.read_text())


def test_thesis_only_na2so3_transitions_and_repeated_na2s_results_are_preserved():
    doc = _doc()
    observations = doc['species']['Na2SO3']['observations']
    assert [(row['values']['property_kind'], row['values']['T_C']) for row in observations] == [
        ('solid_solid_transition', 675), ('melting_point', 750)
    ]
    assert observations[0]['values']['uncertainty_C'] == 10
    context = doc['species']['Na2S']['context'][0]['values']
    assert context['standard_enthalpy_of_formation_as_published'] == 'ΔfH°(Na2S(s), 298.15 K) = −369.0 ± 1.0 kJ/mol'
    assert context['standard_entropy_as_published'].startswith('S°(Na2S(s), 298.15 K) = 97.0 ± 2.0')
    assert context['published_paper']['doi'] == '10.1016/j.jct.2006.06.003'


def test_migrated_transition_values_and_species_identity():
    _doc()
    with patch.object(migrate_module, 'discover_extracts', return_value=[EXTRACT]):
        migrator = migrate_module.Migrator(root=ROOT)
        migrator.migrate_extracts()
        migrator.finalize()
    rows = [row for row in migrator.result.observations.values() if row.source_id == SID]
    assert len(rows) == 2
    assert all(row.value.kind is ValueKind.POINT for row in rows)
    assert all(row.identity.species.formula == 'Na2SO3' for row in rows)


def test_figures_are_deferred_and_contain_no_digitized_points():
    doc = _doc()
    figures = [
        row for group in doc['species'].values() for row in group.get('context', [])
        if row['type'] == 'figure_record'
    ]
    assert {row['locator']['figure'] for row in figures} == {'12', '13', '14', '15', '16', '17', '18'}
    assert all(row['values']['digitisation'] == 'deferred to a figure pass' for row in figures)
    assert all(not row['values'].get('points') for row in figures)


def test_numeric_pin_fails_when_transition_value_is_mutated():
    doc = _doc()
    row = doc['species']['Na2SO3']['observations'][0]
    assert row['values']['T_C'] == 675
    assert check_all_fidelity_samples_match(doc) == []
    row['values']['T_C'] = 674
    assert check_all_fidelity_samples_match(doc)
