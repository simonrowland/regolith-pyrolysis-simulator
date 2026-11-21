"""Production-path regressions for liquid-FeO domain qualification."""

from __future__ import annotations

import pytest

from simulator import engine_binding_admission as admission_module
from simulator import fe_redox as fe_redox_module
from simulator.accounting.formulas import resolve_species_formula
from simulator.accounting.ledger import AtomLedger
from simulator.physical_constants import MBAR_PER_BAR
from tests.chemistry.test_evaporation_freeze_gate import (
    _CLEANED_MELT_ACCOUNT,
    _build_freeze_gate_sim,
    _install_magemin_subprocess_gate,
)

_BASE_A_FEO = {
    'no_pin_extrapolated': 0.2317151957262008,
    'failed_same_hour_pin_refresh': 0.2262023838309322,
    'stale_lookup_then_extrapolated': 0.2317151957262008,
    'fresh_extrapolated': 0.2317151957262008,
    'stable_refusal_floor': 0.05543412502330185,
    'internal_analytical_floor': 0.06704365219510658,
    'hot_read_above_known_liquidus_without_pin': 0.19767374970074492,
}

_EXPECTED_DOMAIN = {
    'no_pin_extrapolated': ('extrapolated', [30.0, 80.0], 'extrapolated'),
    'failed_same_hour_pin_refresh': (
        'extrapolated', [30.0, 80.0], 'extrapolated',
    ),
    'stale_lookup_then_extrapolated': (
        'extrapolated', [30.0, 80.0], 'extrapolated',
    ),
    'fresh_extrapolated': ('extrapolated', [30.0, 80.0], 'extrapolated'),
    'stable_refusal_floor': ('refused', None, 'unknown'),
}


def _liquid_feo_bulk(sim, silica_wt_pct):
    composition_wt_pct = {
        'SiO2': float(silica_wt_pct),
        'MgO': 90.0 - float(silica_wt_pct),
        'FeO': 10.0,
    }
    composition_mol = {
        species: amount / resolve_species_formula(
            species,
            sim.species_formula_registry,
        ).molar_mass_kg_per_mol()
        for species, amount in composition_wt_pct.items()
        if amount > 0.0
    }
    ledger = AtomLedger(registry=sim.atom_ledger.registry)
    ledger.load_external_mol(
        _CLEANED_MELT_ACCOUNT,
        composition_mol,
        source='r26 liquid-FeO current-domain regression',
        material_origin='feedstock',
    )
    sim.atom_ledger = ledger
    return composition_wt_pct


@pytest.mark.parametrize(
    'scenario',
    (*_BASE_A_FEO, 'internal_analytical_liquidus_above'),
)
def test_liquid_feo_activity_uses_current_domain_on_production_paths(
    scenario,
    monkeypatch,
    tmp_path,
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    provider = (
        'analytical'
        if scenario in {
            'internal_analytical_floor',
            'internal_analytical_liquidus_above',
        }
        else 'magemin'
    )
    sim = _build_freeze_gate_sim(
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
        enabled=True,
        liquid_fraction_provider=provider,
    )
    if scenario == 'failed_same_hour_pin_refresh':
        from tests.binding_admission_fixtures import (
            install_synthetic_binding_receipt,
        )

        install_synthetic_binding_receipt(
            tmp_path,
            monkeypatch,
            bind_direct_backend=True,
            identity_override=admission_module.BindingIdentity(
                'magemin', 'ig', 'magemin-ig-r1', 'subprocess',
            ),
            provenance_override={
                'binding_provenance_verifiable': True,
                'runtime_version': {
                    'tag': 'value',
                    'value': 'MAGEMin fixture runtime',
                    'reason': None,
                },
                'runtime_artifacts': {
                    'magemin_selected_binary': {'sha256': 'a' * 64},
                },
            },
        )
        monkeypatch.setattr(sim, '_pt0_store', lambda: None, raising=False)
    trace = (
        None
        if provider == 'analytical'
        else _install_magemin_subprocess_gate(sim, monkeypatch)
    )

    if scenario == 'failed_same_hour_pin_refresh':
        _liquid_feo_bulk(sim, 79.999)
        sim._freeze_gate_curve_record()
        sim._establish_melt_redox_gate_authority_for_current_hour()
        assert isinstance(sim._melt_redox_gate_authority_this_tick, dict)
        assert sim._melt_redox_gate_authority_tick_hour == int(sim.melt.hour)
        trace['fail'] = True
        composition = _liquid_feo_bulk(sim, 80.001)
        temperature_C = 1800.0
    elif scenario == 'stale_lookup_then_extrapolated':
        _liquid_feo_bulk(sim, 50.0)
        sim._freeze_gate_curve_record()
        composition = _liquid_feo_bulk(sim, 85.0)
        temperature_C = 1800.0
    elif scenario == 'stable_refusal_floor':
        composition = _liquid_feo_bulk(sim, 0.0)
        floor_curve = sim._establish_melt_redox_gate_authority_for_current_hour()
        assert floor_curve['gate_provider_refusal']['qualification'] == (
            'out_of_domain_kress91_floor_prediction'
        )
        temperature_C = 1300.0
    elif scenario == 'internal_analytical_floor':
        composition = _liquid_feo_bulk(sim, 50.0)
        sim._establish_melt_redox_gate_authority_for_current_hour()
        temperature_C = 1300.0
        domain = sim.liquid_feo_domain_at(temperature_C + 273.15)
        assert (
            domain.domain_identity.projection_threshold_verdict
            == 'not_applicable'
        )
        assert domain.below_liquidus is None
        assert domain.liquidus_unknown is True
    elif scenario == 'hot_read_above_known_liquidus_without_pin':
        composition = _liquid_feo_bulk(sim, 50.0)
        resolved = sim._freeze_gate_curve_record()
        assert resolved['curve']['liquidus_T_C'] < 1750.0
        assert getattr(sim, '_melt_redox_gate_authority_tick_hour', None) is None
        temperature_C = 1750.0
    elif scenario == 'internal_analytical_liquidus_above':
        composition = _liquid_feo_bulk(sim, 50.0)
        resolved = sim._freeze_gate_curve_record()
        assert resolved['curve']['liquidus_T_C'] < 1750.0
        temperature_C = 1750.0
    else:
        composition = _liquid_feo_bulk(sim, 85.0)
        temperature_C = 1800.0

    temperature_K = temperature_C + 273.15
    pressure_bar = float(sim.melt.p_total_mbar) / MBAR_PER_BAR
    fO2_log = float(sim._current_melt_redox_fO2_log())
    plain_activity = fe_redox_module.calphad_ferrous_feo_activity_diagnostic(
        comp_wt=composition,
        fO2_log=fO2_log,
        T_K=temperature_K,
        pressure_bar=pressure_bar,
    )['a_FeO_authoritative']
    if scenario in _BASE_A_FEO:
        assert plain_activity == pytest.approx(
            _BASE_A_FEO[scenario], rel=0, abs=1e-15,
        )

    state = sim._native_fe_saturation_state(
        composition,
        fe3_over_sigma_fe=0.1,
        T_K=temperature_K,
        pressure_bar=pressure_bar,
        fO2_log=fO2_log,
    )
    assert state['native_fe_activity'] == plain_activity
    notices = state.get('notices', [])
    identity_notices = [
        notice for notice in notices
        if notice.get('kind') == 'magemin_domain_identity'
    ]
    below_notices = [
        notice for notice in notices
        if notice.get('kind') == 'liquid_model_below_liquidus'
    ]
    liquidus_unknown_notices = [
        notice for notice in notices
        if notice.get('kind') == 'liquidus_unknown'
    ]
    if scenario == 'internal_analytical_floor':
        assert identity_notices == []
        assert len(liquidus_unknown_notices) == 1
        assert liquidus_unknown_notices[0]['authority'] == 'unknown'
        return

    if scenario == 'hot_read_above_known_liquidus_without_pin':
        assert identity_notices == []
        assert below_notices == []
        assert len(liquidus_unknown_notices) == 1
        assert liquidus_unknown_notices[0]['authority'] == 'unknown'
        return

    if scenario == 'internal_analytical_liquidus_above':
        assert notices == []
        assert state['native_fe_activity'] == plain_activity
        return

    expected_silica, expected_band, expected_authority = _EXPECTED_DOMAIN[
        scenario
    ]
    assert len(identity_notices) == 1
    notice = identity_notices[0]
    assert notice['silica_verdict'] == expected_silica
    assert notice['projection_threshold_verdict'] == (
        'clear'
    )
    assert notice['certified_band'] == (
        None if expected_band is None else {'sio2_wt_pct': expected_band}
    )
    assert notice['authority'] == expected_authority
    if scenario in {
        'no_pin_extrapolated',
        'failed_same_hour_pin_refresh',
        'stale_lookup_then_extrapolated',
        'fresh_extrapolated',
        'stable_refusal_floor',
    }:
        assert notice['liquidus_status'] == 'unknown'
        assert len(liquidus_unknown_notices) == 1
        assert len(notices) == 2
        assert below_notices == []
    if scenario in {
        'failed_same_hour_pin_refresh',
        'stale_lookup_then_extrapolated',
    }:
        repeated_state = sim._native_fe_saturation_state(
            composition,
            fe3_over_sigma_fe=0.1,
            T_K=temperature_K,
            pressure_bar=pressure_bar,
            fO2_log=fO2_log,
        )
        assert repeated_state['native_fe_activity'] == plain_activity
        assert repeated_state.get('notices', []) == notices
