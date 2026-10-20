from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

import simulator.battery.migrate as migrate_module
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
    assert contexts['chen2019_table16_context']['locator']['page'] == 12
    assert contexts['chen2019_table20_context']['locator']['page'] == 15
    assert contexts['chen2019_table21_context']['locator']['page'] == 15


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


def test_extract_numeric_pin_rejects_cell_mutation():
    doc = _doc()
    obs = next(o for o in doc['species']['SiOCH']['observations'] if o['values']['sample_id'] == '300')
    first = obs['values']['series'][0]
    assert (first['T_K'], first['cp']) == ('1.8416', '4.7419e-4')
    def assert_render_pin(point):
        assert (point['T_K'], point['cp']) == ('1.8416', '4.7419e-4')
    assert_render_pin(first)
    mutated = dict(first, cp='0')
    with pytest.raises(AssertionError):
        assert_render_pin(mutated)
