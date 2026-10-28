"""d-163: flagged class predictions and unchanged pre-existing seed paths."""

import copy
import hashlib
import json

import pytest

from simulator import fe_redox
from simulator.core import PyrolysisSimulator
from simulator.feedstock_guard import BlockedFeedstockError, is_blocked_feedstock
from simulator.state import CampaignPhase
from tests.chemistry.conftest import _build_sim, _load_yaml


PRIOR_FEEDSTOCKS = (
    'lunar_mare_high_ti', 'lunar_spa_kreep_influenced', 'targeted_super_kreep_ore',
    'lunar_highlands_lhs1', 'lunar_highlands_lhs1_yu_2025_reference',
    'lunar_mare_lms1', 'lunar_mare_oprl2n', 'lunar_highlands_nuw_lht_5m',
    'lunar_eac_1a', 'lunar_mls_1a', 's_type_asteroid_silicate',
    'm_type_silicate_phase', 'v_type_vesta_hed', 'e_type_enstatite_aubrite',
    'cm_carbonaceous_chondrite', 'ceres_regolith', 'comet_nucleus',
    'mars_global_mgs1', 'mars_sulfate_rich', 'mars_phyllosilicate_clay',
    'mars_perchlorate_rich',
)


@pytest.fixture(scope='module')
def data():
    return {name: _load_yaml(name + '.yaml')
            for name in ('feedstocks', 'setpoints', 'vapor_pressures')}


@pytest.mark.parametrize('key', PRIOR_FEEDSTOCKS)
def test_missing_anchor_seed_and_two_c0_hours_or_existing_composition_block(data, key, request):
    feed = data['feedstocks'][key]
    original = copy.deepcopy(feed)
    prepared, diagnostic = fe_redox.initialize_feedstock_iron(feed)
    assert diagnostic['seed_authority'].value == 'model_assumed'
    assert diagnostic['seed_basis'] == 'class_prior'
    assert diagnostic['class_prior'] == feed['iron_redox_class']
    assert diagnostic['prior_half_width_dex'] > 0
    assert fe_redox.initialize_feedstock_iron(prepared) == (prepared, diagnostic)
    assert feed == original
    if is_blocked_feedstock(feed):
        # Iron priors cannot supply the independently missing oxide assay.
        with pytest.raises(BlockedFeedstockError):
            _build_sim(key, data['vapor_pressures'], data['feedstocks'], data['setpoints'])
        request.node.user_properties.append(('composition_status', feed['composition_status']))
        return
    additives = ({'C': PyrolysisSimulator._carbon_reductant_required_kg(feed, 1000.)}
                 if PyrolysisSimulator._uses_mars_carbon_cleanup(feed) else None)
    sim = _build_sim(key, data['vapor_pressures'], data['feedstocks'], data['setpoints'],
                     additives_kg=additives)
    assert sim.record.seed_authority.value == 'model_assumed'
    sim._current_melt_redox_fO2_log()
    expected_status = ('ABOVE' if feed['iron_redox_class'] in {
        'terrestrial_basalt', 'terrestrial_anorthosite', 'cm_carbonaceous',
        'ceres', 'mars_regolith',
    } else 'INDETERMINATE')
    assert sim._last_intrinsic_redox_state['saturation_status'] == expected_status
    assert sim._last_intrinsic_redox_state['saturation_gap_dex'] > 0. or key in {
        'e_type_enstatite_aubrite', 'comet_nucleus',
    }
    sim.start_campaign(CampaignPhase.C0)
    for _ in range(2):
        snapshot = sim.step()
        assert snapshot.campaign is CampaignPhase.C0
        assert sim._last_intrinsic_redox_state['saturation_status'] == expected_status
        assert sim.atom_ledger.mol_by_account('process.metal_phase').get('Fe', 0.) == 0.
    # Also exercise the precipitation solver at the diagnostic's liquid
    # reference; the two C0 steps above may still be below the liquid gate.
    sim.melt.temperature_C = 1400.
    sim._current_melt_redox_fO2_log()
    result = sim._compute_native_fe_saturation_extent()
    assert result['native_fe_mol'] == 0.
    assert result['native_fe_state']['saturation_status'] == expected_status
    assert result['native_fe_state']['saturation_gap_dex'] == pytest.approx(
        sim._last_intrinsic_redox_state['saturation_gap_dex'])
    request.node.user_properties.extend((
        ('saturation_status', result['native_fe_state']['saturation_status']),
        ('saturation_gap_dex', result['native_fe_state']['saturation_gap_dex']),
        ('native_fe_mol', result['native_fe_mol']),
    ))
    assert sim.atom_ledger.mol_by_account('process.metal_phase').get('Fe', 0.) == 0.
    json.dumps(sim._feedstock_iron_initialization, allow_nan=False)


# Captured from the production initializer at 336e2c980 before changing it.
@pytest.mark.parametrize('key, digest', [
    ('lunar_mare_jsc_1a_legacy', '6d10d668f519c00b9e20725b10a0f346d58b31eeda4e88819d4de3dffc14d76e'),
    ('lunar_highlands_nu_lht_2m', '89d8959e39be4af2c5377b4a77b597fde11f6d158fc4b245b5b798fa07f62e43'),
    ('m_type_metallic_phase', '9487bc7c5f5b9d16cee98d6a1928872c755b9c978ae328a65ac1a1d1289c1073'),
    # d-166 / round-14 F1: anchors relabelled imposed_fO2, material unchanged.
    ('ci_carbonaceous_chondrite', '0f4baae9121b5412ddac81b4dcf7d532060d3570c124057af2b8985c5de7bc06'),
    ('mars_basalt', 'fce9537208c915f19decc5cc45743272ebd862736bf02fcb63923423bb410353'),
])
def test_five_preexisting_paths_unchanged(data, key, digest):
    payload = fe_redox.initialize_feedstock_iron(data['feedstocks'][key])
    # Steer F3 adds explanatory provenance only; retain the original numeric
    # and seed-path pin independently of that explicitly requested note.
    if 'buffer_conversion' in payload[1]:
        payload[1]['buffer_conversion'].pop('reference_uncertainty_note', None)
        payload[0]['composition_basis']['iron_initialization']['buffer_conversion'].pop(
            'reference_uncertainty_note', None)
    assert hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest() == digest


@pytest.mark.parametrize('authority', ['measured', 'literature_typical'])
def test_speciation_precedes_class_prior(data, authority):
    from tests.test_feedstock_iron_seed import _observation

    feed = copy.deepcopy(data['feedstocks']['lunar_mare_high_ti'])
    feed['iron_speciation'] = {'fe3_over_sum_fe': _observation(.2, authority=authority)}
    _, diagnostic = fe_redox.initialize_feedstock_iron(feed)
    assert diagnostic['seed_authority'].value == authority
    assert 'class_prior' not in diagnostic


def test_cited_anchor_precedes_class_prior(data):
    feed = copy.deepcopy(data['feedstocks']['mars_basalt'])
    feed['iron_redox_class'] = 'mars_regolith'
    _, diagnostic = fe_redox.initialize_feedstock_iron(feed)
    assert diagnostic['anchor'] == feed['intrinsic_redox']
    assert 'class_prior' not in diagnostic


def test_unclassified_prior_completes_without_precipitation(data):
    feed = {'composition_wt_pct': {'SiO2': 80., 'FeO': 20.}}
    sim = _build_sim('ad_hoc', data['vapor_pressures'], {'ad_hoc': feed},
                     data['setpoints'])
    sim.melt.temperature_C = 1400.
    assert sim._feedstock_iron_initialization['class_prior'] == 'unclassified'
    assert sim._compute_native_fe_saturation_extent()['native_fe_mol'] == 0.
    assert sim.atom_ledger.mol_by_account('process.metal_phase').get('Fe', 0.) == 0.


@pytest.mark.parametrize('margin, status', [(-6., 'BELOW'), (-4., 'INDETERMINATE'),
                                          (4., 'INDETERMINATE'), (6., 'ABOVE')])
def test_prior_range_controls_saturation_decision(monkeypatch, margin, status):
    monkeypatch.setattr(fe_redox, 'calphad_ferrous_feo_activity_diagnostic',
                        lambda **kw: {'a_FeO_authoritative': .3})
    bound = fe_redox._feo_activity_saturation_log10_fO2_bar(1673.15, a_feo=.3)
    controls = dict(fe3_over_sigma_fe=.039, T_K=1673.15, pressure_bar=.01,
                    fO2_log=bound+margin)
    comp = {'FeO': 10., 'SiO2': 90.}
    original = fe_redox._native_fe_saturation_comparison(comp, **controls)
    result = fe_redox._native_fe_saturation_comparison(
        comp, **controls, prior_half_width_dex=4.)
    assert result['band_lower_dex'] == pytest.approx(original['band_lower_dex']+4.)
    assert result['band_upper_dex'] == pytest.approx(original['band_upper_dex']+4.)
    assert result['saturation_status'] == status
    assert result['native_fe_saturation'] is (status == 'BELOW')


@pytest.mark.parametrize('seed_basis', [None, 'measured', 'literature_typical',
                                       'cited_anchor', 'class_prior'])
@pytest.mark.parametrize('margin, status', [(-6., 'BELOW'), (-4., 'INDETERMINATE'),
                                          (0., 'INDETERMINATE'), (6., 'ABOVE')])
def test_unmeasured_prior_cannot_authorize_precipitation(monkeypatch, seed_basis, margin, status):
    monkeypatch.setattr(fe_redox, 'calphad_ferrous_feo_activity_diagnostic',
                        lambda **kw: {'a_FeO_authoritative': .3})
    bound = fe_redox._feo_activity_saturation_log10_fO2_bar(1673.15, a_feo=.3)
    result = fe_redox._native_fe_saturation_comparison(
        {'FeO': 10., 'SiO2': 90.}, fe3_over_sigma_fe=.039,
        T_K=1673.15, pressure_bar=.01, fO2_log=bound+margin,
        prior_half_width_dex=4., seed_basis=seed_basis)
    if seed_basis == 'class_prior' and status == 'BELOW':
        status = 'UNMEASURED_PRIOR_BELOW'
        assert 'unmeasured' in result['saturation_notice']['reason'].lower()
        assert result['saturation_notice']['status'] == status
    assert result['saturation_status'] == status
    assert result['saturation_gap_dex'] == pytest.approx(margin)
    assert result['native_fe_saturation'] is (status == 'BELOW')
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize('classes, interval', [
    (('terrestrial_basalt', 'terrestrial_anorthosite'), [1.5, 5.5]),
    (('lunar_mare', 'lunar_kreep', 'lunar_highland'), [-2.5, .5]),
    (('s_type_ordinary_chondrite',), [-3., 1.]),
    (('v_type_hed',), [-2., 0.]),
    (('e_type_enstatite',), [-7., -3.]),
    (('m_type_silicate',), [-4., -1.]),
    (('cm_carbonaceous', 'ceres'), [1., 3.]),
    (('mars_basalt', 'mars_regolith'), [-1., 4.]),
    (('comet_nucleus',), [-5., 3.]),
])
def test_physical_class_ranges(classes, interval):
    # Controller-ratified physical envelopes, independently of saturation status.
    for name in classes:
        prior = fe_redox._iron_class_priors()[name]
        assert prior['interval'] == interval
        assert prior['reasoning']
        assert prior.get('locator', {}).get('published_page') or (
            'class range: physical reasoning, no corpus citation' in prior['reasoning'])
