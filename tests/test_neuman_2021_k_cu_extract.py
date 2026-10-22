from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.migrate import REPO_ROOT
from tests.battery.test_migrate import _migrate_real_extract
from tools.validate_literature_extracts import check_all_fidelity_samples_match


SOURCE = 'neuman-2021-k-cu-evaporation-isotope-fractionation'
EXTRACT = REPO_ROOT / 'data/literature/extracts' / f'{SOURCE}.yaml'


def test_neuman_table2_render_pins_row_count_and_fraction_schema_gap(tmp_path: Path) -> None:
    doc = yaml.safe_load(EXTRACT.read_text(encoding='utf-8'))
    assert doc['extraction']['table_2_printed_run_count'] == 20
    expected = {
        'K': ('0.051', '0.959'),
        'Cu': ('-0.153', '1.031'),
        'Zn': ('0.076', '0.610'),
    }
    for element, (delta, ratio) in expected.items():
        series = doc['species'][element]['observations'][0]['values']['series']
        assert len(series) == 20
        assert series[0]['isotope_delta_isotope_delta'] == delta
        assert series[0]['fraction_remaining_ratio_as_printed'] == ratio
        assert doc['species'][element]['observations'][0]['locator']['page'] == 51
    assert doc['species']['Cu']['observations'][0]['values']['series'][8]['fraction_remaining_ratio_as_printed'] == '1.218'
    assert doc['species']['Cu']['observations'][0]['values']['series'][8]['fraction_remaining_quantity_status'] == 'typed_refusal'

    mutated = deepcopy(doc)
    mutated['species']['K']['observations'][0]['values']['series'][0]['isotope_delta_isotope_delta'] = '0.052'
    assert check_all_fidelity_samples_match(mutated)


def test_neuman_series_conditions_reach_real_migrated_observations(tmp_path: Path) -> None:
    result = _migrate_real_extract(tmp_path, f'{SOURCE}.yaml')
    assert len(result.validation.hard_issues) == 0
    assert len(result.observations) == 60
    assert sum(obs.value.kind.value == 'unavailable' for obs in result.observations.values()) == 10
    for element in ('K', 'Cu', 'Zn'):
        prefix = f'{SOURCE}::neuman_2021_table2_{element.lower()}_isotope_and_remaining_ratio'
        matches = [obs for key, obs in result.observations.items() if key.startswith(prefix)]
        assert matches, element
        row = next(obs for obs in matches if '8b13a' in obs.observation_id)
        assert row.identity.temperature_K is not None and row.identity.temperature_K.is_value
        assert row.identity.temperature_K.value == Decimal('1573.15')
        # isotope_delta's identity profile only uses temperature; printed
        # pressure, composition, and fO2 are retained as located point conditions.
        assert row.identity.fO2_Pa is not None and row.identity.fO2_Pa.is_not_applicable
        assert row.identity.total_pressure_Pa is not None and row.identity.total_pressure_Pa.is_not_applicable
        assert row.identity.composition is not None and row.identity.composition.is_not_applicable
        conditions = row.point_conditions or {}
        assert conditions['temperature_K'].state.is_value
        assert conditions['temperature_K'].locator is not None
        assert conditions['fO2_log'].state.value == Decimal('-0.68')
        assert conditions['total_pressure_Pa'].state.is_value
        assert conditions['composition'].state.is_value
        assert all(conditions[key].locator is not None for key in ('fO2_log', 'total_pressure_Pa', 'composition'))
