from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

import simulator.battery.migrate as migrate_module
from tests.battery.test_migrate import assert_extract_source_mutation_fails
from simulator.battery.enums import ValueKind

ROOT = Path(__file__).resolve().parents[2]
SID = 'chen-2019-sioch-cp-entropy-gibbs'
EXTRACT = ROOT / 'data/literature/extracts' / f'{SID}.yaml'
CORPUS = Path(os.environ.get('REGOLITH_CORPUS_ROOT', '/Users/simonrowland/Repos/regolith-corpus'))
PDF = CORPUS / 'raw' / SID / f'{SID}.pdf'


def _doc():
    if not PDF.is_file():
        pytest.skip(f'paper source missing: {PDF}')
    return yaml.safe_load(EXTRACT.read_text())


def test_cp_grid_sample_binding_and_render_pins():
    doc = _doc()
    table3 = next(row for row in doc['species']['SiOCH']['context'] if row['observation_id'] == 'chen2019_table3_context')
    assert table3['values']['printed_rows'][7][2] == '-228.27'
    observations = doc['species']['SiOCH']['observations']
    assert sum(len(o['values'].get('series', [])) for o in observations if o['values'].get('quantity') == 'cp') == 736
    cp_observations = [o for o in observations if o['values'].get('quantity') == 'cp']
    pins = {
        'R337': ('1.8308', '1.7349e-4'),
        '300': ('1.8416', '4.7419e-4'),
        '844': ('1.8276', '1.7110e-4'),
        '539': ('1.8686', '5.0650e-4'),
        '338': ('1.8169', '1.1247e-3'),
        '248': ('1.8329', '1.5039e-4'),
        '149': ('1.8307', '2.4771e-3'),
        '659': ('1.8304', '2.2792e-3'),
    }
    for sample, expected in pins.items():
        matches = [o for o in cp_observations if o['values']['sample_id'] == sample]
        assert matches, sample
        points = [p for o in matches for p in o['values']['series']]
        assert expected in {(str(p['T_K']), str(p['cp'])) for p in points}, sample
        assert all(p['T_K'] is not None and p['cp'] is not None for p in points)
    # R337 continuation is printed on physical PDF page 5.
    continuation = next(o for o in cp_observations if o['values']['sample_id'] == 'R337' and o['locator']['page'] == 5)
    assert {'T_K': '7.0686', 'cp': '1.8421e-3'} in continuation['values']['series']
    assert continuation['locator']['page'] == 5
    assert all(o['per'] == 'mol_atom' for o in cp_observations)
    assert len([o for o in observations if o['locator'].get('table') == '4' and o['values'].get('quantity') in {'delta_fH', 'delta_fG', 'S'}]) == 24
    contexts = {o['observation_id']: o for o in doc['species']['SiOCH']['context']}
    expected_contexts = {
        'chen2019_table14_context': (14, 'a-SiCH #R337', 10, '14'),
        'chen2019_table15_context': (15, 'a-SiCH #300', 11, '15'),
        'chen2019_table16_context': (16, 'a-SiOCH #844', 12, '16'),
        'chen2019_table17_context': (17, 'a-SiOCH #539', 12, '17'),
        'chen2019_table18_context': (18, 'a-SiOCH #338', 13, '18'),
        'chen2019_table18_continued_context': (18, 'a-SiOCH #338', 14, '18'),
        'chen2019_table19_context': (19, 'a-SiOCH #248', 14, '19'),
        'chen2019_table20_context': (20, 'a-SiOCH #149', 15, '20'),
        'chen2019_table21_context': (21, 'a-SiOCH #659', 15, '21'),
        'chen2019_table21_continued_context': (21, 'a-SiOCH #659', 16, '21 continued'),
    }
    for observation_id, (table, sample, page, locator_table) in expected_contexts.items():
        context = contexts[observation_id]
        assert context['values']['table_number'] == table
        assert context['values']['sample_id'] == sample
        assert context['values']['method_class'] == 'model_derived'
        assert context['locator']['page'] == page
        assert context['locator']['pdf_page_index'] == page - 1
        assert context['locator']['table'] == locator_table
    assert contexts['chen2019_table18_continued_context']['locator']['note'] == 'Table 18 continued on physical page 14.'
    assert ['300', '14.436', '13.104', '2.2052', '5.7537'] in contexts['chen2019_table16_context']['values']['printed_rows']
    assert ['298.15', '12.200', '11.176', '1.8611', '4.9341'] in contexts['chen2019_table17_context']['values']['printed_rows']
    assert ['300', '12.264', '11.252', '1.8837', '4.9729'] in contexts['chen2019_table17_context']['values']['printed_rows']
    assert ['300', '12.473', '14.477', '2.1633', '7.2657'] in contexts['chen2019_table20_context']['values']['printed_rows']
    assert ['1', '$8.584·10^{-4}$', '$8.361·10^{-4}$', '$4.200·10^{-7}$', '$4.161·10^{-4}$'] in contexts['chen2019_table21_context']['values']['printed_rows']
    table21_cont = contexts['chen2019_table21_continued_context']['values']['printed_rows']
    assert ['40', '1.5954', '1.0693', '0.02648', '0.40730'] in table21_cont
    assert ['300', '10.895', '11.838', '1.8010', '5.8345'] in table21_cont


def test_every_migrated_cp_value_and_identity():
    _doc()
    with patch.object(migrate_module, 'discover_extracts', return_value=[EXTRACT]):
        migrator = migrate_module.Migrator(root=ROOT)
        migrator.migrate_extracts()
        migrator.finalize()
    rows = [o for o in migrator.result.observations.values() if o.source_id == SID]
    assert len(rows) == 760
    cp_rows = [row for row in rows if row.identity.quantity.value == 'cp']
    table4_rows = [row for row in rows if row.observation_id.rsplit('::', 1)[1].startswith('chen2019_table4_')]
    assert len(cp_rows) == 736
    assert len(table4_rows) == 24
    for row in rows:
        assert row.value.kind is ValueKind.POINT, row.observation_id
        assert row.identity.species.formula == 'unknown'
        assert row.point_conditions['temperature_K'].state.is_value
    for row in cp_rows:
        assert row.point_conditions['total_pressure_Pa'].state.is_value
        assert row.identity.per.value.value == 'mol_atom'
    assert {row.value.point for row in table4_rows if row.identity.quantity.value == 'delta_fH'} == {
        Decimal('-37.50'), Decimal('-133.15'), Decimal('-266.47'), Decimal('-174.21'),
        Decimal('-149.40'), Decimal('-133.71'), Decimal('-82.78'), Decimal('-133.61')
    }
    expected_table4 = {
        'R337': {'delta_fH': '-37.50', 'S': '8.80', 'delta_fG': '-32.21'},
        '300': {'delta_fH': '-133.15', 'S': '10.59', 'delta_fG': '-125.55'},
        '844': {'delta_fH': '-266.47', 'S': '13.02', 'delta_fG': '-252.66'},
        '539': {'delta_fH': '-174.21', 'S': '11.18', 'delta_fG': '-159.58'},
        '338': {'delta_fH': '-149.40', 'S': '17.46', 'delta_fG': '-136.92'},
        '248': {'delta_fH': '-133.71', 'S': '9.66', 'delta_fG': '-122.45'},
        '149': {'delta_fH': '-82.78', 'S': '14.40', 'delta_fG': '-69.64'},
        '659': {'delta_fH': '-133.61', 'S': '11.77', 'delta_fG': '-122.23'},
    }
    for row in table4_rows:
        sample, quantity = row.observation_id.rsplit('::', 1)[1].removeprefix('chen2019_table4_sample').split('_', 1)
        assert row.value.point == Decimal(expected_table4[sample][quantity])
        assert row.identity.per.value.value == 'mol_atom'


def test_extract_numeric_pin_rejects_cell_mutation(tmp_path, monkeypatch):
    _doc()
    assert_extract_source_mutation_fails(
        EXTRACT,
        "sample_id: '300'\n        film_type_as_published: a-SiCH\n        series:\n        - T_K: '1.8416'\n          cp: '4.7419e-4'",
        "sample_id: '300'\n        film_type_as_published: a-SiCH\n        series:\n        - T_K: '1.8416'\n          cp: '4.7000e-4'",
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        assertion=test_cp_grid_sample_binding_and_render_pins,
    )
