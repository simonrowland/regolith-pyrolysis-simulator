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
def test_missing_anchor_seed_and_two_c0_hours_or_existing_composition_block(data, key):
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
        return
    additives = ({'C': PyrolysisSimulator._carbon_reductant_required_kg(feed, 1000.)}
                 if PyrolysisSimulator._uses_mars_carbon_cleanup(feed) else None)
    sim = _build_sim(key, data['vapor_pressures'], data['feedstocks'], data['setpoints'],
                     additives_kg=additives)
    assert sim.record.seed_authority.value == 'model_assumed'
    sim._current_melt_redox_fO2_log()
    assert sim._last_intrinsic_redox_state['saturation_status'] == 'INDETERMINATE'
    sim.start_campaign(CampaignPhase.C0)
    for _ in range(2):
        snapshot = sim.step()
        assert snapshot.campaign is CampaignPhase.C0
    assert sim._last_intrinsic_redox_state['saturation_status'] == 'INDETERMINATE'
    assert sim.atom_ledger.mol_by_account('process.metal_phase').get('Fe', 0.) == 0.
    json.dumps(sim._feedstock_iron_initialization, allow_nan=False)


# Captured from the production initializer at 336e2c980 before changing it.
@pytest.mark.parametrize('key, digest', [
    ('lunar_mare_jsc_1a_legacy', '6d10d668f519c00b9e20725b10a0f346d58b31eeda4e88819d4de3dffc14d76e'),
    ('lunar_highlands_nu_lht_2m', '89d8959e39be4af2c5377b4a77b597fde11f6d158fc4b245b5b798fa07f62e43'),
    ('m_type_metallic_phase', '9487bc7c5f5b9d16cee98d6a1928872c755b9c978ae328a65ac1a1d1289c1073'),
    ('ci_carbonaceous_chondrite', 'b2242266ea3a53eea4ebb10fddadc51683f20227d0696e7e7c48ca94dcc32224'),
    ('mars_basalt', '916b86396d83faead13a3c698f26d57fa53bea5d3cfc1c2f8e3b47325b30b797'),
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
