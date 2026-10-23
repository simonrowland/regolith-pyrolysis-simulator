"""Round-2 reference-state regressions for the intrinsic redox publisher."""

import math
from decimal import Decimal

import pytest

from simulator.chemistry.melt_activity import _oxide_liquid_reference_delta_g_kj_per_mol
from simulator.fe_redox import _published_intrinsic_melt_fO2
from simulator.reference_data.janaf import TABLES_DIR, load_table_document


ACCOUNT = {"FeO": 1.0, "Cr2O3": 1.0, "Al2O3": 1.0, "SiO2": 4.0}


@pytest.mark.parametrize("temperature, expected", [
    (1873.15, {"FeO": -10.12176596396069, "SiO2": -16.399171408348767,
               "Al2O3": -21.515944568874563, "Cr2O3": -10.922031113482255}),
    (2473.15, {"FeO": -7.064756023553955, "SiO2": -10.082176716696138,
               "Al2O3": -14.05165852590972, "Cr2O3": -5.734391936799886}),
])
@pytest.mark.parametrize('oxide', ['FeO', 'SiO2', 'Al2O3', 'Cr2O3'])
def test_candidate_floors_include_independent_table_reference_corrections(temperature, expected, oxide):
    # These are the a5ad8a8c5 pins, before the reference-state correction.
    # Independently read the printed end nodes, not the runtime interpolator.
    publication = _published_intrinsic_melt_fO2(-40.0, ACCOUNT, temperature)
    left = 1800 if temperature < 2000 else 2400
    base = expected[oxide]
    if oxide == 'FeO':
        # Holzheid1997 p.30 gives gamma_stoich/gamma_wustite=1.66 at
        # 1600 C; the existing owner holds that endpoint above its range.
        shift = -2 * math.log10(1.66)
    elif oxide == 'Cr2O3' or (oxide == 'Al2O3' and temperature < 2000):
        solid, liquid = ('Cr-014', 'Cr-015') if oxide == 'Cr2O3' else ('Al-096', 'Al-100')
        delta = _independent_table_gibbs(liquid, temperature, left) - _independent_table_gibbs(solid, temperature, left)
        # From mu_l=mu_s: 2/3 oxide/mol O2 times the parent-formula
        # Gibbs difference. Units: kJ/mol * 1000 / (J/mol/K * K).
        shift = (2 / 3) * float(delta) * 1000 / (8.31446 * temperature * math.log(10))
        assert shift > 0.0  # Below melting, the corrected floor is less reducing.
    else:
        # The selected Si row and the high-T Al row explicitly declare liquid.
        shift = 0.0
    assert publication['candidates'][oxide]['fO2_log_lower_bound'] == pytest.approx(base + shift, rel=0, abs=1e-12)


def _independent_table_gibbs(table_id, temperature, left):
    table = load_table_document(TABLES_DIR / f'{table_id}.yaml')['table']
    rows = {Decimal(str(row['temperature']['as_published'])): row
            for row in table['values'] if row.get('temperature', {}).get('value') is not None}
    lo, hi, target = Decimal(left), Decimal(left + 100), Decimal(str(temperature))
    # Use the table's signed numeric tokens: at Cr 2700 K the retained text
    # lost a minus sign, and the table explicitly records its restored sign.
    # Interpolation and reference conversion are still independent of runtime.
    a = Decimal(str(rows[lo]['formation_gibbs_energy']['value']))
    b = Decimal(str(rows[hi]['formation_gibbs_energy']['value']))
    return ((hi - target) * a + (target - lo) * b) / (hi - lo)


@pytest.mark.parametrize('temperature', [1873.15, 2473.15])
def test_iw_unknown_support_and_reference_conversion_are_visible_when_binding(temperature):
    publication = _published_intrinsic_melt_fO2(-40, {'FeO': 1.0, 'SiO2': 4.0}, temperature)
    assert publication['buffer_oxide'] == 'FeO'
    notice = publication.get('iw_temperature_support_notice', '')
    assert 'IW buffer temperature support not established' in notice
    assert notice in publication['notice']
    candidate = publication['candidates']['FeO']
    assert candidate['reference_conversion']['stoich_to_wustite_activity_divisor'] == 1.66
    if temperature > 1873.15:
        assert 'clamped_above_verified_range' in publication['notice']


def test_liquid_reference_is_detected_from_declared_phase_without_table_lookup(monkeypatch):
    import simulator.reference_data.janaf as janaf

    def forbidden(*args):
        raise AssertionError('matching pure-liquid reference needs no phase conversion')

    monkeypatch.setattr(janaf, '_formation_gibbs_kJ_per_mol', forbidden)
    # Deliberately below alumina's melting point: the supplied phase contract,
    # rather than an invented T cutoff, decides reference compatibility.
    shift = _oxide_liquid_reference_delta_g_kj_per_mol(
        'Al2O3', 1873.15, '4/3 Al(l) + O2 -> 2/3 Al2O3(l)',
    )
    assert shift['delta_g_kJ_per_mol'] == 0.0


@pytest.mark.parametrize('oxide', ['NiO', 'MnO'])
def test_missing_reference_pair_is_flagged_and_never_published_as_a_floor(oxide):
    publication = _published_intrinsic_melt_fO2(-40, {oxide: 1.0, 'SiO2': 4.0}, 1873.15)
    assert oxide not in publication['candidates']
    assert 'reference pair unavailable' in publication['unavailable_buffers'][oxide]
    assert oxide in publication['notice']
    assert publication['buffer_oxide'] == 'SiO2'


def test_unavailable_cr_table_cannot_publish_the_mismatched_cr_floor(monkeypatch):
    import simulator.reference_data.janaf as janaf

    original = janaf._formation_gibbs_kJ_per_mol

    def missing(table, temperature):
        if table == 'Cr-015':
            raise ValueError('Cr liquid Gibbs unavailable')
        return original(table, temperature)

    monkeypatch.setattr(janaf, '_formation_gibbs_kJ_per_mol', missing)
    publication = _published_intrinsic_melt_fO2(-40, {'Cr2O3': 1.0, 'SiO2': 4.0}, 2473.15)
    assert 'Cr2O3' not in publication['candidates']
    assert 'Cr liquid Gibbs unavailable' in publication['notice']
    assert publication['buffer_oxide'] == 'SiO2'


def test_correction_vanishes_at_the_printed_crystal_liquid_gibbs_crossing():
    delta_2600 = _independent_table_gibbs('Cr-015', 2600, 2600) - _independent_table_gibbs('Cr-014', 2600, 2600)
    delta_2700 = _independent_table_gibbs('Cr-015', 2700, 2600) - _independent_table_gibbs('Cr-014', 2700, 2600)
    crossing = Decimal(2600) - delta_2600 * Decimal(100) / (delta_2700 - delta_2600)
    conversion = _oxide_liquid_reference_delta_g_kj_per_mol(
        'Cr2O3', float(crossing), '4/3 Cr(l) + O2 -> 2/3 Cr2O3(s)',
    )
    assert conversion['delta_g_kJ_per_mol'] == pytest.approx(0, rel=0, abs=1e-11)


@pytest.mark.parametrize('oxide', ['CaO', 'MgO'])
def test_glass_branch_is_not_used_as_pure_liquid_gibbs(oxide):
    # These liquid-table titles also contain a glass branch below the printed
    # GLASS <--> LIQUID transition; its Gibbs values are not liquid-reference data.
    publication = _published_intrinsic_melt_fO2(-40, {'FeO': 1.0, oxide: 1.0}, 1200.0)
    assert oxide not in publication['candidates']
    assert 'GLASS <--> LIQUID' in publication['unavailable_buffers'][oxide]
    assert publication['buffer_oxide'] == 'FeO'


def test_low_temperature_na_reuses_its_existing_reference_conversion():
    from simulator.chemistry.melt_activity import _na_activity_reference_standard_delta_g_kj_per_mol_o2
    from simulator.chemistry.ellingham_thermo import ellingham_segment_for_temperature

    publication = _published_intrinsic_melt_fO2(-40, {'Na2O': 1.0, 'FeO': 1.0}, 1100.0)
    conversion = publication['candidates']['Na2O']['reference_conversion']
    segment = ellingham_segment_for_temperature('Na', 1100.0)
    assert segment.delta_g_kJ_per_mol_O2(1100.0) + segment.n_ox * conversion['delta_g_kJ_per_mol'] == pytest.approx(
        _na_activity_reference_standard_delta_g_kj_per_mol_o2(1100.0), rel=0, abs=1e-12,
    )


def test_declared_rutile_buffer_uses_available_liquid_crystal_tables():
    publication = _published_intrinsic_melt_fO2(-40, {'TiO2': 1.0, 'SiO2': 4.0}, 1873.15)
    conversion = publication['candidates']['TiO2']['reference_conversion']
    expected = _independent_table_gibbs('O-044', 1873.15, 1800) - _independent_table_gibbs('O-043', 1873.15, 1800)
    assert conversion['tables'] == ('O-043', 'O-044')
    assert conversion['delta_g_kJ_per_mol'] == pytest.approx(float(expected), rel=0, abs=1e-12)
    assert expected > 0.0
