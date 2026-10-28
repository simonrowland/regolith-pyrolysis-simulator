"""Iron seed authority, buffer conventions, and first-liquid atom accounting."""

import copy
import math

import pytest

from simulator import fe_redox
from simulator.fe_redox import initialize_feedstock_iron
from simulator.feedstock_composition import resolve_feedstock_composition
from simulator.state import MOLAR_MASS


def _observation(value, *, authority='measured', method='mossbauer', **extra):
    return dict(value=value, interval=[value, value],
                source={'reference': 'synthetic assay', 'page': '1'},
                method=method, authority=authority, **extra)


def _feedstock():
    return {
        'composition_wt_pct': {'SiO2': 80.0, 'FeO': 20.0},
        'intrinsic_redox': {
            'delta_iw': -1.0, 'interval': [-2.0, -0.5],
            'buffer': 'solid_iron_wustite_sato1973',
            'buffer_source': {'reference': 'Sato et al. 1973 Eq. 5', 'page': 1067},
            'source': 'synthetic class proxy', 'locator': {'published_page': 1},
            'authority': 'model_assumed', 'estimated': True,
            'formation_temperature_K': 1673.15,
        },
    }


@pytest.mark.parametrize('authority', ['measured', 'model_assumed', 'literature_typical'])
def test_point_anchor_native_fe_band_follows_provenance_not_authority(authority):
    feed = _feedstock()
    feed['intrinsic_redox'].update(
        authority=authority, buffer='pure_liquid_wustite',
        delta_iw=-3., interval=[-3., -3.])
    prepared, seed = initialize_feedstock_iron(feed)
    c = prepared['composition_wt_pct']
    result = fe_redox.native_fe_equilibrium_extent(
        comp_wt=c, feo_mol=c['FeO']/MOLAR_MASS['FeO'],
        fe2o3_mol=c['Fe2O3']/MOLAR_MASS['Fe2O3'], T_K=1673.15,
        pressure_bar=.01, gas_fO2_log=lambda n: -100., iron_seed=seed)
    state = result['native_fe_state']
    assert (state['band_lower_dex'], state['band_upper_dex']) == (0., 0.)
    assert state['saturation_status'] == 'BELOW'
    assert result['native_fe_mol'] > 0.
    assert seed['seed_authority'].value == authority


@pytest.mark.parametrize('authority', ['measured', 'model_assumed', 'literature_typical'])
def test_cited_lunar_interval_native_fe_band_is_declared_width(authority):
    from tests.chemistry.conftest import _load_yaml

    feed = copy.deepcopy(_load_yaml('feedstocks.yaml')['lunar_mare_low_ti'])
    feed['intrinsic_redox']['authority'] = authority
    _, seed = initialize_feedstock_iron(feed)
    # The cited delta IW=-1 in [-2, -.5] has a 1 dex reducing width
    # and a .5 dex oxidizing width, independent of the forward ferric split.
    assert fe_redox._native_fe_saturation_band(
        seed['fe3_over_sigma_fe'], iron_seed=seed) == (1., .5)
    assert seed['seed_authority'].value == authority


def test_explicit_liquid_wustite_anchor_preserves_b899_numeric_pin():
    # Captured at 8ad0cc9bf before d-160 changes. This explicit legacy buffer
    # must retain its meaning when literature solid-IW anchors are admitted.
    prepared, diagnostic = initialize_feedstock_iron({
        'composition_wt_pct': {'SiO2': 80.0, 'FeO': 20.0},
        'intrinsic_redox': {
            'delta_iw': -1.0, 'buffer': 'pure_liquid_wustite',
            'formation_temperature_K': 1673.15,
            'interval': [-1.0, -1.0],
            'source': 'test fixture offset', 'locator': {'page': 'fixture'},
            'buffer_source': {'reference': 'test fixture liquid-wustite reference', 'page': 'fixture'},
        },
    })
    assert {k: v.hex() for k, v in prepared['composition_wt_pct'].items()} == {
        'SiO2': '0x1.4000000000000p+6',
        'FeO': '0x1.3b9e01a2c7592p+4',
        'Fe2O3': '0x1.37bb097d1be40p-2',
    }
    assert diagnostic['fO2_log'].hex() == '-0x1.57b4427aead95p+3'


@pytest.mark.parametrize('authority', ['measured', 'literature_typical'])
def test_ferric_measurement_precedes_delta_iw(monkeypatch, authority):
    from simulator.state import IronSeedAuthority

    feed = _feedstock()
    feed['iron_speciation'] = {'fe3_over_sum_fe': _observation(.2, authority=authority)}
    original = copy.deepcopy(feed)
    monkeypatch.setattr(fe_redox, 'split_feot_composition',
                        lambda *a, **kw: pytest.fail('measured path used delta_iw'))
    prepared, diagnostic = initialize_feedstock_iron(feed)
    c = prepared['composition_wt_pct']
    ferric = 2 * c['Fe2O3'] / MOLAR_MASS['Fe2O3']
    ferrous = c['FeO'] / MOLAR_MASS['FeO']
    assert ferric / (ferric + ferrous) == pytest.approx(.2, abs=1e-15)
    assert diagnostic['seed_authority'] is IronSeedAuthority(authority)
    assert 'buffer_conversion' not in diagnostic
    assert feed == original
    assert initialize_feedstock_iron(prepared)[0] == prepared


def test_printed_oxide_pair_sets_the_split():
    feed = _feedstock()
    feed['iron_speciation'] = {
        'feo_wt_pct': _observation(14.3688, method='wet_chemistry_titration'),
        'fe2o3_wt_pct': _observation(15.9687, method='wet_chemistry_titration'),
    }
    prepared, diagnostic = initialize_feedstock_iron(feed)
    c = prepared['composition_wt_pct']
    # Printed values represent .2 mol FeO and .1 mol Fe2O3 per 100g:
    # half of the oxide iron atoms are ferric, independently of the bulk scale.
    assert c['FeO'] / MOLAR_MASS['FeO'] == pytest.approx(
        2 * c['Fe2O3'] / MOLAR_MASS['Fe2O3'])
    assert diagnostic['seed_authority'].value == 'measured'


@pytest.mark.parametrize('formation_T_K', [1673.0, 1673.15])
def test_cited_solid_iw_is_converted_once_with_range_and_extrapolation(formation_T_K):
    from decimal import Decimal, localcontext

    feed = _feedstock()
    feed['intrinsic_redox']['formation_temperature_K'] = formation_T_K
    prepared, diagnostic = initialize_feedstock_iron(feed)
    with localcontext() as ctx:
        ctx.prec = 45
        # Independent Decimal evaluation of Sato1973 p1067 Eq5 (atm),
        # with the exact atmosphere-to-bar definition, not a runtime helper.
        iw = Decimal('6.824') - Decimal(27470) / Decimal(str(formation_T_K))
        iw += Decimal('1.01325').log10()
    assert diagnostic['fO2_log'] == pytest.approx(float(iw - 1), abs=2e-14)
    conversion = diagnostic['buffer_conversion']
    assert conversion['extrapolated'] is True
    assert conversion['fO2_interval_log'] == pytest.approx([float(iw-2), float(iw-Decimal('.5'))])
    assert conversion['delta_iw_liquid'] == pytest.approx(
        diagnostic['fO2_log'] - fe_redox.feo_iw_log10_fO2_bar(formation_T_K))
    assert diagnostic['seed_authority'].value == 'model_assumed'
    assert initialize_feedstock_iron(prepared)[0] == prepared


def test_explicit_metal_repartitions_bulk_total_iron_and_loads_separately():
    feed = _feedstock()
    feed['iron_speciation'] = {
        'fe0_metal_wt_pct': _observation(.5, method='vsm_saturation_magnetization', form='nanophase'),
        'fe3_over_sum_fe': _observation(.2),
    }
    prepared, diagnostic = initialize_feedstock_iron(feed)
    c = prepared['composition_wt_pct']
    oxide_fe = c['FeO']/MOLAR_MASS['FeO'] + 2*c['Fe2O3']/MOLAR_MASS['Fe2O3']
    metal_fe = prepared['elemental_composition_wt_pct']['Fe']/MOLAR_MASS['Fe']
    assert metal_fe == .5/MOLAR_MASS['Fe']
    assert oxide_fe + metal_fe == pytest.approx(20./MOLAR_MASS['FeO'], rel=2e-15)
    assert 2*c['Fe2O3']/MOLAR_MASS['Fe2O3']/(oxide_fe+metal_fe) == pytest.approx(.2)
    assert diagnostic['metal_seed_authority'].value == 'measured'
    assert initialize_feedstock_iron(prepared)[1] == diagnostic


def test_speciation_overrides_a_declared_pair_and_preserves_alloy_inventory():
    feed = _feedstock()
    feed['composition_wt_pct']['Fe2O3'] = 1.
    feed['composition_wt_pct']['Ni'] = .1
    feed['iron_speciation'] = {
        'fe0_metal_wt_pct': _observation(.5, method='vsm_saturation_magnetization',
            form='meteoritic_fe_ni', metal_composition_wt_pct={'Ni': 5., 'Co': 1.}),
        'fe3_over_sum_fe': _observation(.2),
    }
    p, d = initialize_feedstock_iron(feed)
    assert p['elemental_composition_wt_pct']['Fe'] == .5
    c = p['composition_wt_pct']
    n3 = 2*c['Fe2O3']/MOLAR_MASS['Fe2O3']
    assert n3/(n3+c['FeO']/MOLAR_MASS['FeO']+.5/MOLAR_MASS['Fe']) == pytest.approx(.2)
    assert p['bulk_additions']['Ni'] == pytest.approx(.5*.05/.94)
    assert p['bulk_additions']['Co'] == pytest.approx(.5*.01/.94)
    assert c['Ni'] + p['bulk_additions']['Ni'] == pytest.approx(.1)
    assert d['metal_phase_species'] == ['Fe', 'Ni', 'Co']
    assert initialize_feedstock_iron(p) == (p, d)


def test_raw_fmr_measurement_is_retained_and_converted_with_published_example():
    feed = _feedstock()
    feed['iron_speciation'] = {
        'is_over_feo': _observation(12., method='fmr_is_feo'),
        'feo_wt_pct': _observation(17.6, method='fmr_is_feo'),
    }
    prepared, diagnostic = initialize_feedstock_iron(feed)
    # Escobar-Cerezo2018 p5 Eq6 and worked example: .069 +/- .013 wt%.
    assert prepared['elemental_composition_wt_pct']['Fe'] == pytest.approx(.069, abs=.0005)
    assert prepared['iron_speciation'] == feed['iron_speciation']
    assert '10.3847/1538-4357/aaa24d' in diagnostic['metal_conversion']['source']


@pytest.mark.parametrize('change', [
    {'value': True}, {'value': float('nan')}, {'value': -0.1}, {'value': 1.1},
    {'interval': [.3, .1]}, {'interval': [.3, .4]}, {'interval': [0.]},
    {'source': {'reference': 'assay'}}, {'method': 'guess'}, {'authority': 'inferred'},
    {'source': {'reference': 'assay', 'page': None}},
])
def test_invalid_speciation_is_rejected_by_existing_resolver(change):
    feed = _feedstock()
    feed['iron_speciation'] = {'fe3_over_sum_fe': {**_observation(.2), **change}}
    with pytest.raises(ValueError, match='iron_speciation'):
        resolve_feedstock_composition(feed)


def test_unknown_buffer_is_a_typed_refusal():
    feed = _feedstock()
    feed['intrinsic_redox']['buffer'] = 'unknown_IW'
    with pytest.raises(fe_redox.Kress91InvalidControls, match='buffer'):
        initialize_feedstock_iron(feed)


def test_liquid_reference_also_requires_cited_offset_and_range():
    feed = {'composition_wt_pct': {'SiO2': 80., 'FeO': 20.},
            'intrinsic_redox': {'delta_iw': -1., 'buffer': 'pure_liquid_wustite'}}
    with pytest.raises(fe_redox.Kress91InvalidControls, match='interval'):
        initialize_feedstock_iron(feed)


def test_fe0_only_preserves_the_existing_measured_oxide_provenance():
    feed = _feedstock()
    feed['composition_wt_pct']['Fe2O3'] = 1.
    feed['composition_basis'] = {
        s: {'method': 'wet chemistry', 'source': 'original oxide assay p.1'}
        for s in ('FeO', 'Fe2O3')}
    feed['iron_speciation'] = {
        'fe0_metal_wt_pct': _observation(.5, form='native', method='vsm_saturation_magnetization')}
    prepared, diagnostic = initialize_feedstock_iron(feed)
    for s in ('FeO', 'Fe2O3'):
        assert prepared['composition_basis'][s] == feed['composition_basis'][s]
    assert diagnostic['seed_authority'].value == 'measured'


def test_widened_band_does_not_correct_the_inverse_point_estimate():
    # Independent propagation of the held-out Jayasuriya residuals in the
    # owner sheet gives model sigma=0.916 dex at q=.039; method precision
    # alone gives -0.6794/+0.5290 dex. Combined endpoints exceed each.
    low, high = fe_redox._native_fe_saturation_band(.039)
    assert low > .6794
    assert high > .5290
    assert low > 1.0 and high > 1.0


def test_seed_authority_reaches_the_run_metadata():
    import json
    from types import SimpleNamespace
    from simulator.runner import _attach_feedstock_iron_notice

    feed = _feedstock()
    feed['iron_speciation'] = {'fe3_over_sum_fe': _observation(.2)}
    _, diagnostic = initialize_feedstock_iron(feed)
    metadata = {}
    _attach_feedstock_iron_notice(metadata, SimpleNamespace(_feedstock_iron_initialization=diagnostic))
    assert json.loads(json.dumps(metadata, allow_nan=False))['seed_authority'] == 'measured'


def test_lunar_first_liquid_comproportionation_is_atom_closed_and_visible(monkeypatch):
    from tests.chemistry.conftest import _build_sim, _load_yaml

    feeds = _load_yaml('feedstocks.yaml')
    feed = copy.deepcopy(feeds['lunar_mare_low_ti'])
    feed['iron_speciation'] = {
        'fe0_metal_wt_pct': _observation(.5, form='nanophase', method='vsm_saturation_magnetization'),
        'fe3_over_sum_fe': _observation(.02),
    }
    feeds['lunar_mare_low_ti'] = feed
    sim = _build_sim('lunar_mare_low_ti', _load_yaml('vapor_pressures.yaml'), feeds, _load_yaml('setpoints.yaml'))
    before = dict(sim.atom_ledger.mol_by_account('process.cleaned_melt'))
    metal = sim.atom_ledger.mol_by_account('process.metal_phase')['Fe']
    assert metal > before['Fe2O3']
    assert sim.record.seed_authority.value == 'measured'
    assert sim._make_snapshot().seed_authority.value == 'measured'
    # Cold charge is not reacted. The existing liquidus authority authorizes
    # the next operation, without making a diagnostic backend look measured.
    sim._apply_feedstock_iron_comproportionation()
    assert sim.atom_ledger.mol_by_account('process.metal_phase')['Fe'] == metal
    sim.melt.temperature_C = 1600.
    monkeypatch.setattr(sim, '_melt_redox_temperature_shift_is_liquid', lambda *a, **kw: True)
    start = len(sim.atom_ledger.transitions)
    diagnostic = sim._apply_feedstock_iron_comproportionation()
    after = sim.atom_ledger.mol_by_account('process.cleaned_melt')
    extent = diagnostic['extent_mol']
    assert 0 < extent < before['Fe2O3']
    assert after['FeO']-before['FeO'] == pytest.approx(3*extent)
    assert sim.atom_ledger.mol_by_account('process.metal_phase')['Fe'] == pytest.approx(metal-extent)
    for t in sim.atom_ledger.transitions[start:]:
        t.validate_conservation(sim.species_formula_registry)
        debit = t.debit_atom_moles(sim.atom_ledger.registry)
        credit = t.credit_atom_moles(sim.atom_ledger.registry)
        for element, n in debit.items():
            assert 100*abs(credit.get(element, 0.)-n)/n <= 5e-12
    assert diagnostic['metal_remaining_mol'] > 0.
    state = sim._last_intrinsic_redox_state
    assert state['fO2_log'] == pytest.approx(state['iw_bound'], abs=1e-9)
    assert state['saturation_status'] == 'INDETERMINATE'
    sim._apply_feedstock_iron_comproportionation()
    assert len(sim.atom_ledger.transitions) == start+1
