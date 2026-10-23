from __future__ import annotations

import csv
import re
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import IdentityEqualKind, Quantity, UncertaintyKind
from simulator.battery.identity import identity_equal
from simulator.battery.migrate import REPO_ROOT, convert_duration_to_s
from simulator.battery.records import Uncertainty
from simulator.battery.score import _printed_uncertainty_band
from tests.battery.test_migrate import _migrate_real_extract
from tools.validate_literature_extracts import check_all_fidelity_samples_match


SOURCE = 'neuman-2021-k-cu-evaporation-isotope-fractionation'
EXTRACT = REPO_ROOT / 'data/literature/extracts' / f'{SOURCE}.yaml'


def test_neuman_table2_retypes_fraction_remaining_rows_from_audit_cells() -> None:
    doc = yaml.safe_load(EXTRACT.read_text(encoding='utf-8'))
    assert doc['extraction']['table_2_printed_run_count'] == 20
    audit = EXTRACT.with_name(f'{SOURCE}-audit.csv')
    with audit.open(encoding='utf-8', newline='') as stream:
        audit_cells = list(csv.DictReader(stream))
    # The cell audit is the independent transcription of the rendered PDF page.
    # Retain the table's exact printed ratio and RSD precision in the typed rows.
    ratio_cell = {
        'K': '(K/Ca)/(K/Ca)0',
        'Cu': '(Cu/Ca)/(Cu/Ca)0',
        'Zn': '(Zn/Ca)/(Zn/Ca)0',
    }
    expected = {
        'K': ('0.051', '0.08'),
        'Cu': ('-0.153', '0.03'),
        'Zn': ('0.076', '0.07'),
    }
    for element, (delta, sigma) in expected.items():
        observations = doc['species'][element]['observations']
        isotope = next(
            obs for obs in observations if obs['values']['quantity'] == 'isotope_delta'
        )
        fractions = [
            obs for obs in observations
            if obs['values']['quantity'] == 'element_fraction_remaining'
        ]
        isotope_series = isotope['values']['series']
        assert len(isotope_series) == len(fractions) == 20
        assert isotope_series[0]['isotope_delta_isotope_delta'] == delta
        assert isotope_series[0]['sigma'] == sigma
        assert isotope['locator']['page'] == 51
        assert isotope['units'] == 'per_mil'
        assert isotope['uncertainty']['confidence_level'] == 'not_stated'
        assert all('sigma' in row and 'two_SE_per_mil' not in row for row in isotope_series)
        fraction_by_run = {
            obs['observation_id'].rsplit('_', 1)[-1].upper(): obs
            for obs in fractions
        }
        assert set(fraction_by_run) == {
            cell['row'] for cell in audit_cells
            if cell['pdf_page'] == '51'
            and cell['table'] == '2'
            and cell['cell'] == ratio_cell[element]
        }
        for fraction in fractions:
            values = fraction['values']
            assert fraction['type'] == 'concentration_series'
            assert fraction['locator']['page'] == 51
            assert values['quantity'] == 'element_fraction_remaining'
            assert values['element'] == element
            assert values['normalising_element'] == 'Ca'
            assert 'basis' not in values
            assert values['basis_as_printed'] == ratio_cell[element]
            assert 'quantitatively retained' in values['retention_assumption']
            assert values['sample_mass'] == {'value': 'approximately 250', 'unit': 'mg'}
            assert values['sample_geometry']['value'] == 'pre-baked alumina crucible, OD 8 mm, ID 6 mm'
            assert values['duration']['unit'] == 'h'
            assert 'RSD' in fraction['uncertainty']['note']
        assert all(
            not any(key.startswith('fraction_remaining_') for key in row)
            for row in isotope_series
        )
        extracted = {
            run: (
                fraction['values']['fraction_remaining'],
                fraction['values']['RSD_percent_as_printed'],
            )
            for run, fraction in fraction_by_run.items()
        }
        ratio_audit: dict[str, str] = {}
        rsd_audit: dict[str, str] = {}
        for cell in audit_cells:
            if cell['pdf_page'] != '51' or cell['table'] != '2':
                continue
            if cell['cell'] == ratio_cell[element]:
                ratio_audit[cell['row']] = cell['render_read']
            elif cell['cell'] == f'RSD {element} (%)':
                rsd_audit[cell['row']] = cell['render_read']
        assert set(extracted) == set(ratio_audit) == set(rsd_audit)
        assert extracted == {
            run: (ratio_audit[run], rsd_audit[run]) for run in ratio_audit
        }
    cu_fraction = next(
        obs for obs in doc['species']['Cu']['observations']
        if obs['values'].get('quantity') == 'element_fraction_remaining'
        and obs['observation_id'].endswith('_20b14a')
    )
    assert cu_fraction['values']['fraction_remaining'] == '1.218'
    assert 'table_2_fraction_remaining_schema_gap' not in doc['extraction']

    mutated = deepcopy(doc)
    mutated['species']['K']['observations'][0]['values']['series'][0]['isotope_delta_isotope_delta'] = '0.052'
    assert check_all_fidelity_samples_match(doc) == []
    errors = check_all_fidelity_samples_match(mutated)
    assert errors and any('0.052' in str(error) for error in errors)

    # Table 3 and quench evidence are pinned to their printed precision/pages.
    assert doc['extraction']['table_3_model_derived_context']['other_literature_rows_as_printed'][4]['alpha_evp'] == '0.9960'
    k_observation = doc['species']['K']['observations'][0]
    assert k_observation['quench_statement_locator']['page'] == 11
    assert k_observation['quench_diffusion_statement_locator']['page'] == 18
    audit = EXTRACT.with_name(f'{SOURCE}-audit.md').read_text(encoding='utf-8')
    assert re.search(r'`/[A-Za-z]', audit) is None
    assert 'tools/mineru_table_draft.py (offer-21 line; run standalone)' in audit
    with EXTRACT.with_name(f'{SOURCE}-audit.csv').open(encoding='utf-8', newline='') as stream:
        cells = list(csv.DictReader(stream))
    assert any(
        cell['pdf_page'] == '51'
        and cell['row'] == '8B13A'
        and cell['cell'] == '2 SE K'
        and cell['pdftotext_layout_read'] == '0.08'
        and cell['render_read'] == '0.08'
        for cell in cells
    )
    assert any(
        cell['row'] == 'other_literature_rows_as_printed[4]'
        and cell['cell'] == 'alpha_evp'
        and cell['pdftotext_layout_read'] == '0.9960'
        and cell['render_read'] == '0.9960'
        for cell in cells
    )


def test_neuman_series_conditions_reach_real_migrated_observations(tmp_path: Path) -> None:
    result = _migrate_real_extract(tmp_path, f'{SOURCE}.yaml')
    assert len(result.validation.hard_issues) == 0
    assert len(result.observations) == 120
    assert sum(obs.value.kind.value == 'unavailable' for obs in result.observations.values()) == 10
    expected_points = {
        'K': Decimal('0.051'),
        'Cu': Decimal('-0.153'),
        'Zn': Decimal('0.076'),
    }
    doc = yaml.safe_load(EXTRACT.read_text(encoding='utf-8'))
    for element in ('K', 'Cu', 'Zn'):
        prefix = f'{SOURCE}::neuman_2021_table2_{element.lower()}_isotope_and_remaining_ratio'
        matches = [obs for key, obs in result.observations.items() if key.startswith(prefix)]
        assert matches, element
        row = next(obs for obs in matches if '8b13a' in obs.observation_id)
        assert row.value.point == expected_points[element]
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

        isotope = next(
            obs for obs in doc['species'][element]['observations']
            if obs['values']['quantity'] == 'isotope_delta'
        )
        series = isotope['values']['series']
        sigma_by_run = {row['run'].casefold(): row['sigma'] for row in series}
        for observation in matches:
            run = next(run for run in sorted(sigma_by_run, key=len, reverse=True) if run in observation.observation_id.casefold())
            expected_sigma = sigma_by_run[run]
            if expected_sigma is None:
                assert observation.value.kind.value == 'unavailable'
                continue
            assert observation.value.kind.value == 'point'
            resolution = _printed_uncertainty_band(
                Quantity.ISOTOPE_DELTA,
                observation.uncertainty,
                abs(observation.value.point),
                source_observation=observation,
            )
            assert resolution.refusal is None
            assert resolution.band is not None
            assert resolution.band.value == Decimal(expected_sigma)
            assert resolution.band.unit == 'per_mil'
            assert resolution.band.rule.startswith('printed_unstated:')
            assert resolution.notice is not None
            assert 'confidence_level_not_stated' in resolution.notice.reason

        typed_prefix = f'{SOURCE}::neuman_2021_table2_{element.lower()}_fraction_remaining'
        typed = [obs for key, obs in result.observations.items() if key.startswith(typed_prefix)]
        fraction_by_observation = {}
        for fraction in doc['species'][element]['observations']:
            if fraction['values'].get('quantity') != 'element_fraction_remaining':
                continue
            duration_s, refusal = convert_duration_to_s(
                fraction['values']['duration']['value'],
                fraction['values']['duration']['unit'],
            )
            assert duration_s is not None, refusal
            fraction_by_observation[fraction['observation_id']] = (
                Decimal(fraction['values']['fraction_remaining']), duration_s
            )
        assert len(typed) == 20
        assert all(obs.identity.quantity.value.value == 'element_fraction_remaining' for obs in typed)
        assert all(obs.identity.normalising_element.value == 'Ca' for obs in typed)
        assert all(obs.admission.status.value == 'admitted' for obs in typed)
        assert all(
            identity_equal(obs.identity, obs.identity).kind
            is IdentityEqualKind.IDENTITY_UNKNOWN
            for obs in typed
        )
        assert all('quantitatively retained' in obs.provenance['retention_assumption'] for obs in typed)
        assert all(
            obs.provenance['sample_geometry']['value']
            == 'pre-baked alumina crucible, OD 8 mm, ID 6 mm'
            for obs in typed
        )
        for observation in typed:
            observation_id = observation.observation_id.rsplit('::', 1)[-1]
            expected_value, expected_duration_s = fraction_by_observation[observation_id]
            assert observation.value.point == expected_value
            assert observation.identity.temperature_K.value is not None
            assert observation.identity.exposure.value.duration_s.value == expected_duration_s
            conditions = observation.point_conditions or {}
            assert set(('temperature_K', 'fO2_log', 'total_pressure_Pa', 'composition')) <= set(conditions)
            assert all(
                conditions[key].locator is not None
                for key in ('temperature_K', 'fO2_log', 'total_pressure_Pa', 'composition')
            )
            assert conditions['temperature_K'].state.value.point == observation.identity.temperature_K.value


def test_neuman_fraction_remaining_mutation_reaches_the_typed_value(tmp_path: Path) -> None:
    doc = yaml.safe_load(EXTRACT.read_text(encoding='utf-8'))
    mutated = deepcopy(doc)
    fraction = next(
        obs for obs in mutated['species']['K']['observations']
        if obs['values'].get('quantity') == 'element_fraction_remaining'
        and obs['observation_id'].endswith('_8b13a')
    )
    fraction['values']['fraction_remaining'] = '0.958'
    result = _migrate_real_extract(tmp_path, f'{SOURCE}.yaml', extract_doc=mutated)
    point = next(
        obs for obs in result.observations.values()
        if f'::neuman_2021_table2_k_fraction_remaining_8b13a' in obs.observation_id
    )
    assert point.value.point == Decimal('0.958')


def test_neuman_fraction_remaining_rows_migrate_relative_rsd(tmp_path: Path) -> None:
    result = _migrate_real_extract(tmp_path, f'{SOURCE}.yaml')
    doc = yaml.safe_load(EXTRACT.read_text(encoding='utf-8'))
    expected_rsd = {
        f"{SOURCE}::{row['observation_id']}": row['values'][
            'RSD_percent_as_printed'
        ]
        for element in ('K', 'Cu', 'Zn')
        for row in doc['species'][element]['observations']
        if row['values'].get('quantity') == 'element_fraction_remaining'
    }
    migrated = {
        key: observation
        for key, observation in result.observations.items()
        if key in expected_rsd
    }

    assert len(expected_rsd) == len(migrated) == 60
    for observation_id, relative_sd in expected_rsd.items():
        uncertainty = migrated[observation_id].uncertainty
        assert uncertainty.kind is UncertaintyKind.PRINTED
        assert uncertainty.basis == 'relative_sd'
        assert uncertainty.verbatim['RSD_percent_as_printed'] == relative_sd
        assert uncertainty.verbatim['confidence_level'] == 'not_stated'

    assert migrated[
        f'{SOURCE}::neuman_2021_table2_k_fraction_remaining_8b13a'
    ].uncertainty.value == Decimal('0.0103')


def test_neuman_relative_rsd_reaches_score_band(tmp_path: Path) -> None:
    result = _migrate_real_extract(tmp_path, f'{SOURCE}.yaml')
    observation = result.observations[
        f'{SOURCE}::neuman_2021_table2_k_fraction_remaining_8b13a'
    ]
    absolute_sigma = Uncertainty(
        kind=UncertaintyKind.PRINTED,
        verbatim={
            "value": "0.0098777",
            "unit": "dimensionless",
            "confidence_level": "not_stated",
        },
    )

    resolution = _printed_uncertainty_band(
        Quantity.ELEMENT_FRACTION_REMAINING,
        observation.uncertainty,
        abs(observation.value.point),
        source_observation=observation,
    )
    absolute_resolution = _printed_uncertainty_band(
        Quantity.ELEMENT_FRACTION_REMAINING,
        absolute_sigma,
        abs(observation.value.point),
        source_observation=observation,
    )

    assert observation.value.point == Decimal('0.959')
    assert observation.uncertainty.basis == 'relative_sd'
    assert observation.uncertainty.verbatim['confidence_level'] == 'not_stated'
    assert resolution.refusal is None
    assert resolution.band is not None
    assert absolute_resolution.refusal is None
    assert absolute_resolution.band is not None
    assert resolution.band.value == Decimal('0.0098777')
    assert resolution.band.value == absolute_resolution.band.value
    assert resolution.band.unit == 'dimensionless'
    assert resolution.band.rule.startswith('printed_unstated:')
    assert absolute_resolution.band.rule.startswith('printed_unstated:')
    assert resolution.notice is not None
    assert absolute_resolution.notice is not None
    assert resolution.notice.reason == 'confidence_level_not_stated'
    assert resolution.notice.reason == absolute_resolution.notice.reason
